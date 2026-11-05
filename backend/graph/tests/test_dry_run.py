"""The dry run (graph.ingest.dry_run and graph_import --dry-run): its
preconditions in order, the run lock it holds, that it writes only its run
and the source records, statuses, re-runs, records that hold evidence, every
report section, caps and the report file, stored files that changed, file
errors and the identifiers a run keeps, a release that changes mid-run,
batches and their queries."""

import io
import json
import os
import re
import stat
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from graph import storage
from graph.approvals import revoke
from graph.claims import Claim, Endpoint, record_claims
from graph.entities import add_identifier, create_entity
from graph.ingest import dry_run as dry_run_module
from graph.ingest import limits, mapping
from graph.ingest import registry as adapters
from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.dry_run import DryRunError, dry_run
from graph.ingest.mapping import RunStopped
from graph.ingest.registration import register_release
from graph.ingest.runlock import release_run_lock, run_lock_holder
from graph.models import (
    ApprovalScope,
    DatasetRelease,
    Decision,
    DueDiligenceRecord,
    DueDiligenceStatus,
    FileRole,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    RecordStatus,
    RegistryState,
    ReleaseKind,
    ReleaseStatus,
    SourceRecord,
    UsageApproval,
)
from graph.registry import REGISTRY_VERSION
from graph.tests.factories import (
    DatasetReleaseFactory,
    ImportRunFactory,
    PublicationFactory,
    ReleaseLicenceFactory,
    SourceFileFactory,
    StaffUserFactory,
    TermFactory,
    UsageApprovalFactory,
    approve,
    confirm_diligence,
    evidence_release,
)
from graph.tests.ingest_helpers import (
    GENE,
    HEADER,
    PROTEIN,
    SYNTHETIC,
    approved_for_internal_use,
    deadline,
    manifest_bytes,
    registered,
    row,
    tsv,
    write,
)
from graph.tests.lock_helpers import held_run_lock

MAPPED, UNRESOLVED, AMBIGUOUS, REJECTED = (
    RecordStatus.MAPPED,
    RecordStatus.UNRESOLVED,
    RecordStatus.AMBIGUOUS,
    RecordStatus.REJECTED,
)


@pytest.fixture
def genes(registry):
    """The human genes GENE names: TP53 and MDM2."""
    return (
        create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998"),
        create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973"),
    )


def run_of(release, **options) -> ImportRun:
    return dry_run(release, triggered_by=StaffUserFactory(), **options)


def statuses(release) -> dict:
    return dict(
        SourceRecord.objects.filter(release=release).values_list("record_key", "status")
    )


def data_file(release):
    return release.files.get(role=FileRole.DATA)


def regulation(tp53, mdm2, **fields) -> Claim:
    """The claim a GENE row states, as the dry run plans it."""
    return Claim(
        relationship_type="transcriptional_regulation",
        subject=Endpoint(tp53.pk, "HGNC:11998", "primary"),
        object=Endpoint(mdm2.pk, "HGNC:6973", "primary"),
        outcome="supported",
        evidence_basis="experimental",
        assertion_method="manual_assertion",
        **fields,
    )


def with_evidence(release, record_key, claim):
    """Evidence recorded from the record by a commit run, then completed."""
    commit = ImportRunFactory(
        release=release, file=data_file(release), mode=ImportMode.COMMIT
    )
    record = SourceRecord.objects.get(release=release, record_key=record_key)
    record_claims(record, [claim], import_run=commit)
    commit.state = ImportState.COMPLETED
    commit.save(update_fields=["state"])
    return record


# ── Preconditions ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "locked",
    [False, pytest.param(True, marks=pytest.mark.postgres)],
    ids=["without the lock held", "with the lock held"],
)
def test_the_preconditions_are_checked_in_order(
    registry, settings, tmp_path, monkeypatch, locked
):
    release = registered(tmp_path, approved=False)
    commit = ImportRunFactory(
        release=release, file=data_file(release), mode=ImportMode.COMMIT
    )
    RegistryState.objects.filter(pk=1).update(version="0.9")
    settings.TYPED_GRAPH_ENABLED = False
    stale = SimpleNamespace(spec=replace(SPEC, version="1.1.0"))
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, stale)
    bad = write(tmp_path, "resolutions.json", b'{"resolutions": [1]}')
    settings.MEDIA_ROOT = tmp_path / "media"
    (tmp_path / "media").mkdir()
    inactive = StaffUserFactory(is_active=False)
    clerk = StaffUserFactory(is_staff=False)
    staff = StaffUserFactory()
    arguments = {
        "resolutions": bad,
        "report_file": tmp_path / "media" / "report.jsonl",
    }

    def refused(user=staff) -> int:
        with pytest.raises(DryRunError) as raised:
            dry_run(release, triggered_by=user, **arguments)
        assert "Nothing was written" in str(raised.value)
        assert ImportRun.objects.filter(mode=ImportMode.DRY_RUN).count() == 0
        assert SourceRecord.objects.count() == 0
        return raised.value.precondition

    # With the run lock held by another session, the lock refuses last.
    with held_run_lock(release.pk) if locked else nullcontext():
        assert refused(inactive) == 1
        settings.TYPED_GRAPH_ENABLED = True
        assert refused(inactive) == 2
        assert refused(clerk) == 2
        assert refused() == 3
        RegistryState.objects.filter(pk=1).update(version=REGISTRY_VERSION)
        assert refused() == 4
        commit.state = ImportState.ABANDONED
        commit.save(update_fields=["state"])
        assert refused() == 5
        approved_for_internal_use(release)
        assert refused() == 6
        monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, ADAPTER)
        assert refused() == 7
        arguments["resolutions"] = None
        assert refused() == 8
        arguments["report_file"] = tmp_path / "report.jsonl"
        if locked:
            assert refused() == 9
    run = dry_run(release, triggered_by=staff, **arguments)
    assert run.state == ImportState.COMPLETED


@pytest.mark.parametrize(
    "change,message",
    [
        ("unknown", "Unknown release"),
        ("staged", "is staged: a dry run runs on a draft only"),
        ("second data file", "has 2 data files"),
        ("no data file", "has 0 data files"),
        ("open commit run", "has an open commit run"),
    ],
)
def test_the_release_is_a_draft_with_one_data_file_and_no_open_commit(
    registry, tmp_path, change, message
):
    release = registered(tmp_path, approved=False)
    target = release.pk
    if change == "unknown":
        target = release.pk + 1000
    elif change == "staged":
        release.status = ReleaseStatus.STAGED
        release.save(update_fields=["status", "updated_at"])
    elif change == "second data file":
        incoming = storage.stream_to_incoming(io.BytesIO(b"x\n"), max_bytes=100)
        with transaction.atomic():
            storage.place(
                release, incoming, role=FileRole.DATA, original_name="more.tsv"
            )
    elif change == "no data file":
        data_file(release).delete()
    else:
        ImportRunFactory(
            release=release, file=data_file(release), mode=ImportMode.COMMIT
        )
    with pytest.raises(DryRunError, match=message) as raised:
        run_of(target)
    assert raised.value.precondition == 4


@pytest.mark.parametrize("state", [ImportState.RUNNING, ImportState.FAILED])
def test_an_open_commit_run_is_named_with_what_to_do(registry, tmp_path, state):
    release = registered(tmp_path, approved=False)
    commit = ImportRunFactory(
        release=release, file=data_file(release), mode=ImportMode.COMMIT, state=state
    )
    with pytest.raises(DryRunError) as raised:
        run_of(release)
    assert raised.value.precondition == 4
    assert str(raised.value) == (
        f"Release {release.pk} has an open commit run {commit.pk}: a dry run never "
        "runs beside one. Resume it with graph_import --commit, or discard it with "
        "graph_discard_release. Nothing was written."
    )
    assert not ImportRun.objects.filter(mode=ImportMode.DRY_RUN).exists()


def test_a_release_registered_with_another_kind_is_refused(
    registry, tmp_path, genes, monkeypatch
):
    release = registered(tmp_path)
    assert release.kind == ReleaseKind.EVIDENCE
    other = SimpleNamespace(spec=replace(SPEC, kind=ReleaseKind.REFERENCE))
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, other)
    with pytest.raises(DryRunError) as raised:
        run_of(release)
    assert raised.value.precondition == 6
    assert str(raised.value) == (
        f"Release {release.pk} was registered with {SPEC.name} {SPEC.version}, "
        f"mapping {SPEC.mapping_version}, kind evidence, and this code carries "
        f"{SPEC.version}, mapping {SPEC.mapping_version}, kind reference. Register "
        "it again with this adapter (graph_register_release), which updates a draft "
        "without evidence. Nothing was written."
    )
    assert not ImportRun.objects.filter(release=release).exists()
    assert not SourceRecord.objects.filter(release=release).exists()


def test_another_adapter_version_names_both_kinds(
    registry, tmp_path, genes, monkeypatch
):
    release = registered(tmp_path)
    upgraded = SimpleNamespace(spec=replace(SPEC, version="1.1.0", mapping_version="2"))
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, upgraded)
    with pytest.raises(DryRunError) as raised:
        run_of(release)
    assert raised.value.precondition == 6
    assert (
        f"{SPEC.version}, mapping {SPEC.mapping_version}, kind evidence, and this "
        "code carries 1.1.0, mapping 2, kind evidence."
    ) in str(raised.value)


# ── The run lock (precondition 9) ───────────────────────────────────────────


def backend_pid() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def nothing_written(release):
    assert not ImportRun.objects.filter(release=release).exists()
    assert not SourceRecord.objects.filter(release=release).exists()


@pytest.mark.postgres
def test_a_dry_run_is_refused_while_another_run_of_the_release_runs(
    registry, tmp_path, genes
):
    release = registered(tmp_path)
    with held_run_lock(release.pk):
        with pytest.raises(DryRunError) as raised:
            run_of(release)
        assert raised.value.precondition == 9
        assert str(raised.value) == (
            f"A dry run, commit or discard of release {release.pk} is running: one "
            "runs at a time. Nothing was written."
        )
        nothing_written(release)
        # Another release's runs go on.
        other = registered(tmp_path / "other", [row(GENE, record_id="o1")])
        assert run_of(other).state == ImportState.COMPLETED
    assert run_of(release).state == ImportState.COMPLETED


@pytest.mark.postgres
def test_a_dry_run_never_runs_inside_another_run_of_its_release(
    registry, tmp_path, genes
):
    release = registered(tmp_path)
    with release_run_lock(release.pk):
        with pytest.raises(DryRunError) as raised:
            run_of(release)
        assert raised.value.precondition == 9
        assert str(raised.value) == (
            f"This connection already holds the run lock of release {release.pk}: a "
            "dry run never runs inside another run of it. Nothing was written."
        )
        nothing_written(release)
    assert run_lock_holder(release.pk) is None
    assert run_of(release).state == ImportState.COMPLETED


@pytest.mark.postgres
def test_a_release_id_the_run_lock_cannot_key_is_refused(registry):
    # A synthetic draft with a data file, at the first id past what a run
    # lock keys: precondition 9 is the first that refuses it.
    release = DatasetReleaseFactory(
        pk=2**31,
        is_synthetic=True,
        adapter=SPEC.name,
        adapter_version=SPEC.version,
        mapping_version=SPEC.mapping_version,
        kind=SPEC.kind,
    )
    SourceFileFactory(release=release)
    with pytest.raises(DryRunError) as raised:
        run_of(release)
    assert raised.value.precondition == 9
    assert str(raised.value) == (
        "Release 2147483648's id is beyond 2,147,483,647, the largest a run lock "
        "keys. Nothing was written."
    )
    nothing_written(release)


@pytest.mark.postgres
def test_a_dry_run_holds_the_run_lock_until_it_returns(
    registry, tmp_path, genes, monkeypatch
):
    release = registered(tmp_path, [GENE, row(GENE, record_id="r2")])
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    holders = []
    plan = dry_run_module.plan_batch

    def watching(records, *, run_ctx):
        holders.append(run_lock_holder(release.pk))
        return plan(records, run_ctx=run_ctx)

    monkeypatch.setattr(dry_run_module, "plan_batch", watching)
    assert run_of(release).state == ImportState.COMPLETED
    assert holders == [backend_pid()] * 2
    assert run_lock_holder(release.pk) is None

    def broken(plans, *, run):
        raise RuntimeError("the disk is gone")

    # A run that fails releases it too.
    monkeypatch.setattr(dry_run_module, "write_records", broken)
    with pytest.raises(RuntimeError):
        run_of(release)
    assert run_lock_holder(release.pk) is None


def _staged(release):
    release.refresh_from_db()
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])


def _commit_opened(release):
    return ImportRunFactory(
        release=release, file=data_file(release), mode=ImportMode.COMMIT
    )


@pytest.mark.parametrize(
    "change,message",
    [
        (_staged, "is staged: a dry run runs on a draft only"),
        (_commit_opened, "has an open commit run [0-9]+: a dry run never runs"),
    ],
    ids=["staged", "commit run opened"],
)
def test_the_release_is_read_again_once_the_run_lock_is_taken(
    registry, tmp_path, genes, monkeypatch, change, message
):
    # A commit started, or finished, between precondition 4 and the lock.
    release = registered(tmp_path)
    taking = dry_run_module.release_run_lock

    def changed_meanwhile(release_id, **kwargs):
        change(release)
        return taking(release_id, **kwargs)

    monkeypatch.setattr(dry_run_module, "release_run_lock", changed_meanwhile)
    with pytest.raises(DryRunError, match=message) as raised:
        run_of(release)
    assert raised.value.precondition == 4
    assert not ImportRun.objects.filter(mode=ImportMode.DRY_RUN).exists()
    assert not SourceRecord.objects.filter(release=release).exists()
    assert run_lock_holder(release.pk) is None


def test_a_dry_run_marks_no_other_run(registry, tmp_path, genes):
    # A dry run whose process ended: running for good, until the release's
    # next commit or discard marks it.
    release = registered(tmp_path)
    left = ImportRunFactory(release=release, file=data_file(release))
    run = run_of(release)
    assert run.state == ImportState.COMPLETED
    stored = ImportRun.objects.get(pk=left.pk)
    assert (stored.state, stored.error, stored.finished_at) == (
        ImportState.RUNNING,
        "",
        None,
    )


def test_a_synthetic_release_needs_no_approval(registry, tmp_path):
    release = registered(tmp_path, [SYNTHETIC], synthetic=True)
    assert run_of(release, create_entities=True).state == ImportState.COMPLETED


def _revoked(release):
    approved_for_internal_use(release)
    revoke(release.approvals.get(), reason="Test.", revoked_by=StaffUserFactory())


def _denied(release):
    UsageApprovalFactory(release=release, decision=Decision.DENIED, licence=None)


def _another_scope(release):
    ReleaseLicenceFactory(release=release, open_terms=True)
    confirm_diligence(release.dataset)
    approve(release, ApprovalScope.EXTERNAL_SERVICES)


def _diligence_reopened(release):
    approved_for_internal_use(release)
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)
    record.status = DueDiligenceStatus.OPEN
    record._changed_by = StaffUserFactory()
    record.save()


@pytest.mark.parametrize(
    "lapse", [_revoked, _denied, _another_scope, _diligence_reopened]
)
def test_a_recorded_approval_that_is_not_effective_is_no_approval(
    registry, tmp_path, lapse
):
    release = registered(tmp_path, approved=False)
    lapse(release)
    with pytest.raises(
        DryRunError, match="has no effective internal approval"
    ) as raised:
        run_of(release)
    assert raised.value.precondition == 5
    # The command that records one, not a Python function.
    assert str(raised.value) == (
        f"Release {release.pk} is not synthetic and has no effective internal "
        "approval (graph_approve records one). Nothing was written."
    )
    assert not ImportRun.objects.filter(mode=ImportMode.DRY_RUN).exists()
    assert not SourceRecord.objects.filter(release=release).exists()


def test_an_unreadable_resolution_file_is_refused(registry, tmp_path, genes):
    release = registered(tmp_path)
    for path, message in (
        (tmp_path / "missing.json", "cannot be read"),
        (tmp_path, "is not a regular file"),
        (write(tmp_path, "r.json", b"{}"), "has no resolutions"),
    ):
        with pytest.raises(DryRunError, match=message) as raised:
            run_of(release, resolutions=path)
        assert raised.value.precondition == 7


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_resolution_file_swapped_for_a_fifo_is_not_read(
    registry, tmp_path, genes, monkeypatch
):
    tp53, mdm2 = genes
    release = registered(tmp_path)
    path = resolution_file(tmp_path, mdm2, "HGNC:8", "HGNC:6973")
    real = dry_run_module.os

    class Swapping:
        """os, but a FIFO takes the resolution file's place once it is
        checked."""

        def __getattr__(self, name):
            return getattr(real, name)

        def stat(self, target, *args, **kwargs):
            found = real.stat(target, *args, **kwargs)
            if Path(target) == path:
                real.unlink(path)
                real.mkfifo(path)
            return found

    monkeypatch.setattr(dry_run_module, "os", Swapping())
    with deadline(5):
        with pytest.raises(DryRunError, match="is not a regular file") as raised:
            run_of(release, resolutions=path)
    assert raised.value.precondition == 7
    assert not ImportRun.objects.filter(release=release).exists()


@pytest.mark.parametrize("root", ["MEDIA_ROOT", "STATIC_ROOT", "GRAPH_FILES_ROOT"])
def test_a_report_file_never_goes_where_files_are_served_or_kept(
    registry, settings, tmp_path, genes, root
):
    release = registered(tmp_path)
    folder = tmp_path / root.lower()
    folder.mkdir(exist_ok=True)
    setattr(settings, root, folder)
    (folder / "sub").mkdir()
    with pytest.raises(DryRunError, match=f"lies under {root}") as raised:
        run_of(release, report_file=folder / "sub" / ".." / "report.jsonl")
    assert raised.value.precondition == 8


def test_a_report_file_needs_its_directory(registry, tmp_path, genes):
    release = registered(tmp_path)
    with pytest.raises(DryRunError, match="does not exist"):
        run_of(release, report_file=tmp_path / "nowhere" / "report.jsonl")
    with pytest.raises(DryRunError, match="names a directory"):
        run_of(release, report_file=tmp_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
@pytest.mark.parametrize("kind", ["fifo", "link"])
def test_a_report_file_replaces_only_a_regular_file(registry, tmp_path, genes, kind):
    release = registered(tmp_path)
    target = tmp_path / "out" / "report.jsonl"
    target.parent.mkdir()
    linked = tmp_path / "linked.jsonl"
    if kind == "fifo":
        os.mkfifo(target)
    else:
        linked.write_text("kept\n")
        target.symlink_to(linked)
    with pytest.raises(DryRunError, match="is not a regular file") as raised:
        run_of(release, report_file=target)
    assert raised.value.precondition == 8
    mode = os.lstat(target).st_mode
    assert stat.S_ISFIFO(mode) if kind == "fifo" else stat.S_ISLNK(mode)
    if kind == "link":
        assert linked.read_text() == "kept\n"
    assert not ImportRun.objects.filter(release=release).exists()


# ── What it writes ──────────────────────────────────────────────────────────


def row_counts() -> dict:
    counts = {}
    with connection.cursor() as cursor:
        for model in apps.get_models(include_auto_created=True):
            if model._meta.proxy:
                continue
            table = model._meta.db_table
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}")
            counts[table] = cursor.fetchone()[0]
    return counts


def table_contents(left_out=("graph_importrun", "graph_sourcerecord")) -> dict:
    """{table: its rows in primary-key order}, for every table but those
    left out: an update shows, as well as an insert."""
    contents = {}
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for model in apps.get_models(include_auto_created=True):
            table = model._meta.db_table
            if model._meta.proxy or table in left_out:
                continue
            cursor.execute(
                f"SELECT * FROM {quote(table)} ORDER BY {quote(model._meta.pk.column)}"
            )
            contents[table] = cursor.fetchall()
    return contents


def test_a_dry_run_writes_only_its_run_and_the_records(registry, tmp_path, genes):
    rows = [
        row(GENE, record_id="mapped"),
        row(GENE, record_id="new", object_id="HGNC:5", object_label="Gene five"),
        row(PROTEIN, record_id="paper", publications="pmid:123|ref:paper-a"),
        row(GENE, record_id="rejected", effect="negative"),
    ]
    release = registered(tmp_path, rows)
    user = StaffUserFactory()
    before = row_counts()
    contents = table_contents()
    dry_run(release, triggered_by=user, create_entities=True)
    after = row_counts()
    changed = {
        table: after[table] - before[table]
        for table in after
        if after[table] != before[table]
    }
    assert changed == {"graph_importrun": 1, "graph_sourcerecord": 4}
    # Nothing else is updated either.
    assert table_contents() == contents


def test_records_follow_the_statuses_of_section_4_1(registry, tmp_path, genes):
    tp53, mdm2 = genes
    add_identifier(mdm2, "hgnc", "HGNC:7", IdentifierRole.SECONDARY)
    for gene in genes:
        add_identifier(gene, "hgnc", "HGNC:8", IdentifierRole.SECONDARY)
    rows = [
        row(GENE, record_id="mapped"),
        row(GENE, record_id="unmatched", object_id="HGNC:5"),
        row(GENE, record_id="candidate", object_id="HGNC:7"),
        row(GENE, record_id="ambiguous", object_id="HGNC:8"),
        row(GENE, record_id="rejected", effect="negative"),
        row(GENE, record_id="-unstored"),
    ]
    release = registered(tmp_path, rows)
    run = run_of(release)
    assert statuses(release) == {
        "mapped": MAPPED,
        "unmatched": UNRESOLVED,
        "candidate": UNRESOLVED,
        "ambiguous": AMBIGUOUS,
        "rejected": REJECTED,
    }
    assert run.report["counts"]["records"] == {
        "mapped": 1,
        "unresolved": 2,
        "ambiguous": 1,
        "rejected": 1,
        "skipped": 0,
    }
    assert run.report["counts"]["unstored"] == 1
    # Without --create-entities too, the report lists what a commit with it
    # would create: from records that would then be mapped.
    [planned] = run.report["would_create"]["entries"]
    assert (planned["identity_key"], planned["lines"]) == ("hgnc|t:9606|HGNC:5", [3])
    record = SourceRecord.objects.get(release=release, record_key="candidate")
    assert record.import_run_id == run.pk and record.file_id == data_file(release).pk
    assert [issue["code"] for issue in record.issues] == ["candidate"]
    assert record.raw == {
        column: row(GENE, record_id="candidate", object_id="HGNC:7").get(column, "")
        for column in HEADER
    }
    assert record.line_number == 4 and record.record_key_basis == "source_id"


def test_without_raw_rows_a_record_keeps_its_file_line_and_hash(
    registry, tmp_path, genes
):
    release = registered(tmp_path, store_raw_rows=False)
    run_of(release)
    record = SourceRecord.objects.get(release=release)
    assert record.raw is None and len(record.raw_sha256) == 64


def test_a_rerun_updates_statuses_and_issues_only(registry, tmp_path, genes):
    release = registered(
        tmp_path, [row(GENE, record_id="later", object_id="HGNC:5"), GENE]
    )
    first = run_of(release)
    stored = {r.record_key: r for r in SourceRecord.objects.filter(release=release)}
    assert stored["later"].status == UNRESOLVED
    create_entity("gene", "Gene five", 9606, "hgnc", "HGNC:5")
    second = run_of(release)
    assert second.report["counts"]["created"] == 0
    assert second.report["counts"]["updated"] == 1
    again = {r.record_key: r for r in SourceRecord.objects.filter(release=release)}
    assert set(again) == set(stored)
    later = again["later"]
    assert (later.status, later.issues) == (MAPPED, [])
    assert later.pk == stored["later"].pk and later.import_run_id == first.pk
    assert later.created_at == stored["later"].created_at
    assert later.updated_at > stored["later"].updated_at
    for name in ("raw", "raw_sha256", "line_number", "file_id"):
        assert getattr(later, name) == getattr(stored["later"], name)
    # An unchanged record is not written again.
    assert again["r1"].updated_at == stored["r1"].updated_at


def test_records_that_hold_evidence_stay_mapped(registry, tmp_path, genes):
    tp53, mdm2 = genes
    release = registered(
        tmp_path, [row(GENE, record_id="held"), row(GENE, record_id="free")]
    )
    run_of(release)
    with_evidence(release, "held", regulation(tp53, mdm2))
    unchanged = run_of(release)
    assert unchanged.report["assertions"] == {"existing": 1, "new": 1}
    assert unchanged.report["would_change"]["total"] == 0
    # The subject is now one gene's primary and another's secondary
    # identifier: ambiguous.
    other = create_entity("gene", "Other", 9606, "hgnc", "HGNC:3")
    add_identifier(other, "hgnc", "HGNC:11998", IdentifierRole.SECONDARY)
    run = run_of(release)
    held = SourceRecord.objects.get(release=release, record_key="held")
    assert held.status == MAPPED
    assert [issue["code"] for issue in held.issues] == ["candidate"]
    assert statuses(release)["free"] == AMBIGUOUS
    [entry] = run.report["would_change"]["entries"]
    assert (entry["key"], entry["status"], entry["planned"]) == (
        "held",
        "mapped",
        "ambiguous",
    )


def test_evidence_its_claims_no_longer_give_is_a_conflict(registry, tmp_path, genes):
    tp53, mdm2 = genes
    release = registered(tmp_path, [row(GENE, record_id="held")])
    run_of(release)
    with_evidence(release, "held", regulation(tp53, mdm2, evidence_group="other"))
    run = run_of(release)
    held = SourceRecord.objects.get(release=release, record_key="held")
    assert held.status == MAPPED
    [issue] = held.issues
    assert issue["code"] == "claim_conflict"
    assert "already holds assertions that these claims do not reproduce" in (
        issue["message"]
    )
    assert run.report["would_change"]["entries"][0]["planned"] == "rejected"


# ── The report ──────────────────────────────────────────────────────────────


SECTIONS = {
    "verdict",
    "truncated",
    "adapter",
    "registry",
    "release",
    "licence",
    "approvals",
    "create_entities",
    "counts",
    "file_errors",
    "rejected",
    "unstored",
    "identifiers",
    "curated",
    "would_create",
    "label_conflicts",
    "label_differs",
    "relationships",
    "assertions",
    "publications",
    "terms",
    "reference_links",
    "conflicts",
    "synthetic",
    "would_change",
    "notes",
}


def resolution_file(tmp_path, entity, value, target_value) -> Path:
    return write(
        tmp_path,
        "resolutions.json",
        json.dumps(
            {
                "resolutions": [
                    {
                        "namespace": "hgnc",
                        "value": value,
                        "entity_type": "gene",
                        "taxon": 9606,
                        "entity": entity.pk,
                        "target_namespace": "hgnc",
                        "target_value": target_value,
                        "rationale": "Reviewed.",
                        "reviewer": "A. Reviewer",
                    }
                ]
            }
        ).encode(),
    )


def test_the_report_has_every_section(registry, tmp_path, genes):
    tp53, mdm2 = genes
    add_identifier(mdm2, "hgnc", "HGNC:7", IdentifierRole.SECONDARY)
    rows = [
        row(GENE, record_id="mapped", subject_label="p53"),
        row(GENE, record_id="new", object_id="HGNC:5", object_label="Gene five"),
        row(GENE, record_id="other-label", object_id="HGNC:5", object_label="Five"),
        row(GENE, record_id="rejected", effect="negative"),
        row(GENE, record_id="candidate", object_id="HGNC:7"),
        row(GENE, record_id="curated", object_id="HGNC:8"),
        row(GENE, record_id="-unstored"),
    ]
    release = registered(tmp_path, rows)
    run = run_of(
        release,
        create_entities=True,
        resolutions=resolution_file(tmp_path, mdm2, "HGNC:8", "HGNC:6973"),
    )
    report = run.report
    assert set(report) == SECTIONS
    assert report["verdict"] == "ok" and report["truncated"] is False
    assert report["adapter"] == {
        "name": SPEC.name,
        "version": SPEC.version,
        "mapping": SPEC.mapping_version,
        "kind": SPEC.kind,
        "isoform_specific": True,
        "changes_note": SPEC.changes_note,
    }
    assert report["registry"] == {
        "code": REGISTRY_VERSION,
        "database": REGISTRY_VERSION,
    }
    assert report["release"] == {
        "id": release.pk,
        "dataset": release.dataset_id,
        "version": "v1",
        "status": "draft",
        "is_synthetic": False,
        "store_raw_rows": True,
        "supersedes": None,
    }
    assert report["licence"]["records_terms"] is True
    assert report["approvals"] == ["internal"]
    counts = report["counts"]
    assert (counts["lines"], counts["blank_lines"], counts["unstored"]) == (8, 0, 1)
    assert counts["records"] == {
        "mapped": 4,
        "unresolved": 1,
        "ambiguous": 0,
        "rejected": 1,
        "skipped": 0,
    }
    assert counts["mapped"] == [
        {
            "relationship_type": "transcriptional_regulation",
            "outcome": "supported",
            "effect": None,
            "genetic_sign": None,
            "interaction_level": None,
            "records": 4,
        }
    ]
    [rejected] = report["rejected"]["entries"]
    assert (rejected["line"], rejected["key"]) == (5, "rejected")
    assert rejected["issues"][0]["code"] == "negative_effect"
    [unstored] = report["unstored"]["entries"]
    assert (unstored["line"], unstored["codes"]) == (8, ["bad_record_id"])
    identifiers = report["identifiers"]["entries"]
    assert identifiers[0]["lookup"] == "HGNC:7"
    assert identifiers[0]["status"] == "candidate"
    assert identifiers[0]["candidates"][0]["entity"] == mdm2.pk
    assert {entry["lookup"]: entry["mapping"] for entry in identifiers} == {
        "HGNC:7": "unmapped",
        "HGNC:5": "planned",
        "HGNC:8": "curated",
        "HGNC:11998": "primary",
        "HGNC:6973": "primary",
    }
    [curated] = report["curated"]["records"]["entries"]
    assert (curated["key"], curated["entity"], curated["reviewer"]) == (
        "curated",
        mdm2.pk,
        "A. Reviewer",
    )
    assert curated["resolver_status"] == "unmatched"
    assert report["curated"]["unused"]["total"] == 0
    [planned] = report["would_create"]["entries"]
    assert (planned["identity_key"], planned["label"], planned["lines"]) == (
        "hgnc|t:9606|HGNC:5",
        "Gene five",
        [3, 4],
    )
    [conflict] = report["label_conflicts"]["entries"]
    assert (conflict["other_label"], conflict["line"]) == ("Five", 4)
    [differs] = report["label_differs"]["entries"]
    assert (differs["key"], differs["field"]) == ("mapped", "subject_label")
    relationships = report["relationships"]
    assert (relationships["existing"], relationships["new"]) == (0, 2)
    assert {entry["records"] for entry in relationships["entries"]} == {2}
    assert report["assertions"] == {"existing": 0, "new": 4}
    assert report["synthetic"] == {
        "checked": False,
        "passed": True,
        "violations": {"total": 0, "truncated": False, "entries": []},
    }
    for name in ("conflicts", "would_change", "reference_links", "terms", "notes"):
        assert report[name]["total"] == 0, name
    assert report["publications"]["total"] == 0


def test_the_report_lists_curated_records_and_unused_or_redundant_entries(
    registry, tmp_path, genes
):
    tp53, mdm2 = genes
    add_identifier(mdm2, "hgnc", "HGNC:7", IdentifierRole.SECONDARY)
    release = registered(
        tmp_path,
        [
            row(GENE, record_id="plain"),
            row(GENE, record_id="candidate", object_id="HGNC:7"),
        ],
    )

    def entry(entity, value, target_value):
        return {
            "namespace": "hgnc",
            "value": value,
            "entity_type": "gene",
            "taxon": 9606,
            "entity": entity.pk,
            "target_namespace": "hgnc",
            "target_value": target_value,
            "rationale": "Reviewed.",
            "reviewer": "A. Reviewer",
        }

    resolutions = write(
        tmp_path,
        "resolutions.json",
        json.dumps(
            {
                "resolutions": [
                    # HGNC:7 is a candidate (MDM2's secondary): the entry maps it.
                    entry(mdm2, "HGNC:7", "HGNC:6973"),
                    # HGNC:6973 resolves to MDM2: redundant.
                    entry(mdm2, "HGNC:6973", "HGNC:6973"),
                    # No row states HGNC:999: unused.
                    entry(tp53, "HGNC:999", "HGNC:11998"),
                ]
            }
        ).encode(),
    )
    curated = run_of(release, resolutions=resolutions).report["curated"]
    [record] = curated["records"]["entries"]
    assert (record["key"], record["resolver_status"], record["entry"]) == (
        "candidate",
        "candidate",
        1,
    )
    assert (record["entity"], record["rationale"], record["reviewer"]) == (
        mdm2.pk,
        "Reviewed.",
        "A. Reviewer",
    )
    assert [e["index"] for e in curated["redundant"]["entries"]] == [2]
    assert [e["index"] for e in curated["unused"]["entries"]] == [3]
    assert curated["contradicted"]["total"] == curated["not_applied"]["total"] == 0


def test_every_candidate_is_on_the_record_and_in_the_report_file(
    registry, tmp_path, genes
):
    holders = [
        create_entity("gene", f"G{n}", 9606, "hgnc", f"HGNC:{1000 + n}")
        for n in range(25)
    ]
    for gene in holders:
        add_identifier(gene, "hgnc", "HGNC:8", IdentifierRole.SECONDARY)
    release = registered(tmp_path, [row(GENE, object_id="HGNC:8")])
    target = tmp_path / "report.jsonl"
    run = run_of(release, report_file=target)
    ids = {gene.pk for gene in holders}
    [note] = [
        issue
        for issue in SourceRecord.objects.get(release=release).issues
        if issue["code"] == "candidate"
    ]
    assert set(map(int, re.findall(r"entity (\d+)", note["message"]))) == ids
    [listed] = [
        entry
        for entry in map(json.loads, target.read_text().splitlines())
        if entry["section"] == "identifiers" and entry["lookup"] == "HGNC:8"
    ]
    assert {candidate["entity"] for candidate in listed["candidates"]} == ids
    [kept] = [
        entry
        for entry in run.report["identifiers"]["entries"]
        if entry["lookup"] == "HGNC:8"
    ]
    assert len(kept["candidates"]) == limits.REPORT_ENTRY_CANDIDATES


def test_the_report_lists_papers_and_terms(registry, tmp_path):
    create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    stored = PublicationFactory(pmid="123")
    TermFactory(curie="MI:0018", label="two-hybrid")
    rows = [
        row(
            PROTEIN,
            publications="pmid:123|ref:paper-a",
            interaction_type="MI:0915",
            method="MI:0018",
        ),
        row(
            PROTEIN,
            record_id="r2",
            publications="doi:10.1000/x",
            interaction_type="MI:0407",
        ),
    ]
    release = registered(tmp_path, rows)
    # The dataset's own paper, stored by an earlier release with another
    # title than this manifest gives.
    local = PublicationFactory(
        pmid="", local_key="paper-a", local_dataset=release.dataset, title="Old title"
    )
    report = run_of(release).report
    papers = {
        (entry["kind"], entry["value"]): entry
        for entry in report["publications"]["entries"]
    }
    assert papers[("pmid", "123")]["publication"] == stored.pk
    assert papers[("local", "paper-a")]["publication"] == local.pk
    assert papers[("doi", "10.1000/x")]["status"] == "new"
    assert papers[("pmid", "123")]["rows"] == 1
    [difference] = report["publications"]["manifest_differences"]["entries"]
    assert (difference["stored_title"], difference["manifest_title"]) == (
        "Old title",
        "A paper",
    )
    terms = {entry["curie"]: entry for entry in report["terms"]["entries"]}
    assert terms["MI:0018"]["status"] == "stored"
    assert "keeps its label 'two-hybrid'" in terms["MI:0018"]["conflict"]
    assert terms["MI:0915"]["status"] == "new"
    assert terms["MI:0915"]["label"] == "physical association"
    assert terms["MI:0915"]["conflict"] is None


def test_conflicts_with_other_releases_are_listed(registry, tmp_path, genes):
    tp53, mdm2 = genes
    other = evidence_release(records=1)
    record_claims(
        other.records[0],
        [regulation(tp53, mdm2, effect="inhibiting")],
        import_run=other.run,
    )
    other.run.state = ImportState.COMPLETED
    other.run.save(update_fields=["state"])
    other.release.status = ReleaseStatus.STAGED
    other.release.save(update_fields=["status", "updated_at"])
    release = registered(
        tmp_path,
        [
            row(GENE, effect="activating"),
            row(GENE, record_id="r2", outcome="tested_absent"),
            # No effect conflicts with no effect.
            row(GENE, record_id="r3"),
            row(GENE, record_id="r4", outcome="inconclusive"),
        ],
        dataset=other.release.dataset,
        version="corrected",
        supersedes=other.release.version,
    )
    report = run_of(release).report
    # One entry per relationship and other release, with what conflicts.
    [conflict] = report["conflicts"]["entries"]
    assert conflict["kinds"] == ["effect", "outcome"]
    assert conflict["other_release"] == other.release.pk
    assert conflict["corrected_by_this_release"] is True
    assert conflict["relationship"] == {
        "type": "transcriptional_regulation",
        "subject": tp53.pk,
        "object": mdm2.pk,
    }
    this, theirs = conflict["this"], conflict["other"]
    assert (this["records"], this["lines"]) == (2, [2, 3])
    assert this["outcomes"] == ["supported", "tested_absent"]
    assert this["effects"] == ["activating", None]
    assert (theirs["assertions"], theirs["outcomes"], theirs["effects"]) == (
        1,
        ["supported"],
        ["inhibiting"],
    )
    # The regulation family states no level or method.
    assert "levels" not in this and "methods" not in theirs
    # Other outcomes are listed apart.
    [other_outcome] = report["conflicts"]["other_outcomes"]["entries"]
    assert other_outcome["kinds"] == ["other_outcome"]
    assert other_outcome["this"]["outcomes"] == ["inconclusive"]
    assert other_outcome["this"]["lines"] == [5]
    assert report["relationships"]["existing"] == 1


def test_no_conflict_is_listed_against_an_unapproved_release(registry, tmp_path, genes):
    tp53, mdm2 = genes
    other = evidence_release(records=1)
    record_claims(
        other.records[0],
        [regulation(tp53, mdm2, effect="inhibiting")],
        import_run=other.run,
    )
    other.release.status = ReleaseStatus.STAGED
    other.release.save(update_fields=["status", "updated_at"])
    for approval in UsageApproval.objects.filter(release=other.release):
        revoke(approval, reason="Test.", revoked_by=StaffUserFactory())
    release = registered(tmp_path, [row(GENE, effect="activating")])
    assert run_of(release).report["conflicts"]["total"] == 0


def test_reference_links_of_a_synthetic_release_are_listed(registry, tmp_path):
    encodes = row(
        SYNTHETIC,
        relationship_type="encodes",
        object_namespace="synthetic_protein",
        object_id="SYN-P1",
        object_type="protein",
        object_label="SYN-P1",
    )
    release = registered(tmp_path, [encodes], synthetic=True)
    report = run_of(release, create_entities=True).report
    [link] = report["reference_links"]["entries"]
    assert (link["relationship_type"], link["subject"], link["object"]) == (
        "encodes",
        "synthetic_gene:SYN-TF1",
        "synthetic_protein:SYN-P1",
    )
    assert link["status"] == MAPPED
    assert report["synthetic"]["passed"] is True and report["verdict"] == "ok"


def test_a_synthetic_release_with_real_data_is_refused_as_a_whole(registry, tmp_path):
    rows = [
        SYNTHETIC,
        row(SYNTHETIC, record_id="r2", publications="pmid:123"),
        row(SYNTHETIC, record_id="r3"),
    ]
    release = registered(tmp_path, rows, synthetic=True)
    run = run_of(release, create_entities=True)
    report = run.report
    assert run.state == ImportState.COMPLETED
    assert report["verdict"] == "refused"
    assert report["synthetic"]["passed"] is False
    [violation] = report["synthetic"]["violations"]["entries"]
    assert (violation["line"], violation["key"]) == (3, "r2")
    assert statuses(release) == {"r1": MAPPED, "r2": REJECTED, "r3": MAPPED}


@pytest.mark.parametrize(
    "cited",
    [
        "PMID:17081976",
        "Pmid:1",
        "DOI:10.1038/x",
        "pmid :1",
        "pubmed:1",
        "https://doi.org/10.1038/x",
        "10.1038/x",
        "17081976",
        "PMID 17081976",
        "PMID17081976",
        "pmid=17081976",
        "https://pubmed.ncbi.nlm.nih.gov/17081976/",
        "https://www.doi.org/10.1038/nature05000",
        "doi.org/10.1038/nature05000",
        "info:doi/10.1038/nature05000",
        "urn:doi:10.1038/nature05000",
        "DOI 10.1038/nature05000",
        "PMCID:PMC1234567",
        "ref:paper-a|17081976",
    ],
)
def test_a_synthetic_release_citing_a_pmid_or_doi_in_any_form_is_refused(
    registry, tmp_path, cited
):
    rows = [SYNTHETIC, row(SYNTHETIC, record_id="r2", publications=cited)]
    release = registered(tmp_path, rows, synthetic=True)
    report = run_of(release, create_entities=True).report
    assert report["verdict"] == "refused"
    assert report["synthetic"]["passed"] is False
    [violation] = report["synthetic"]["violations"]["entries"]
    assert (violation["line"], violation["key"]) == (3, "r2")
    assert statuses(release)["r2"] == REJECTED


def test_a_synthetic_release_citing_its_manifests_papers_passes(registry, tmp_path):
    # A ref: item names a paper of the manifest, or none (unknown_ref): it
    # cites no real paper either way.
    rows = [
        row(SYNTHETIC, publications="ref:paper-a"),
        row(SYNTHETIC, record_id="r2", publications="ref:paper-a|ref:missing"),
    ]
    release = registered(tmp_path, rows, synthetic=True)
    report = run_of(release, create_entities=True).report
    assert report["verdict"] == "ok"
    assert report["synthetic"]["passed"] is True
    assert statuses(release) == {"r1": MAPPED, "r2": REJECTED}
    [rejected] = report["rejected"]["entries"]
    assert [issue["code"] for issue in rejected["issues"]] == ["unknown_ref"]


# A real row: human genes, by their HGNC IDs and symbols.
REAL = {"subject_label": "TP53", "object_label": "MDM2"}


@pytest.mark.parametrize(
    "cells",
    [
        # Line 2's record id again, with other content.
        {"record_id": "r1"},
        {"record_id": "#real"},
        {"record_id": "-real"},
        {"record_id": "r2", "evidence_group": "g" * 10_001},
        {"record_id": "r2", "evidence_group": "a\x0bb"},
        {"record_id": "r2", "evidence_group": "a\rb"},
    ],
    ids=[
        "conflicting duplicate",
        "hash record id",
        "dash record id",
        "long field",
        "vertical tab",
        "carriage return",
    ],
)
def test_a_synthetic_release_checks_the_rows_it_does_not_store(
    registry, tmp_path, monkeypatch, cells
):
    monkeypatch.setattr(limits, "REPORT_ENTRY_ISSUES", 10)
    release = registered(
        tmp_path, [SYNTHETIC, row(GENE, **REAL, **cells)], synthetic=True
    )
    report = run_of(release, create_entities=True).report
    assert report["verdict"] == "refused"
    assert report["synthetic"]["passed"] is False
    [violation] = report["synthetic"]["violations"]["entries"]
    assert violation["line"] == 3
    assert {issue["field"] for issue in violation["issues"]} == {
        f"{side}_{cell}"
        for side in ("subject", "object")
        for cell in ("namespace", "taxon", "label")
    }
    [unstored] = report["unstored"]["entries"]
    assert unstored["line"] == 3 and "synthetic_violation" in unstored["codes"]
    assert not SourceRecord.objects.filter(release=release, line_number=3).exists()


# ── Caps and the report file ────────────────────────────────────────────────


def test_lists_are_capped_and_the_report_file_has_every_entry(
    registry, tmp_path, genes, monkeypatch
):
    rows = [row(GENE, record_id=f"bad{n}", effect="negative") for n in range(5)]
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "REPORT_LIST_ENTRIES", 2)
    target = tmp_path / "out" / "report.jsonl"
    target.parent.mkdir()
    report = run_of(release, report_file=target).report
    rejected = report["rejected"]
    assert (rejected["total"], rejected["truncated"]) == (5, True)
    assert [entry["key"] for entry in rejected["entries"]] == ["bad0", "bad1"]
    lines = [json.loads(line) for line in target.read_text().splitlines()]
    assert lines[0]["section"] == "summary" and lines[0]["verdict"] == "ok"
    assert lines[0]["totals"]["rejected"] == 5
    assert [line["key"] for line in lines if line["section"] == "rejected"] == [
        f"bad{n}" for n in range(5)
    ]
    # Written in one rename: nothing else is left beside it.
    assert sorted(path.name for path in target.parent.iterdir()) == ["report.jsonl"]


def test_an_entry_carries_its_first_lines_issues_and_short_labels(
    registry, tmp_path, genes, monkeypatch
):
    monkeypatch.setattr(limits, "REPORT_ENTRY_LINES", 2)
    monkeypatch.setattr(limits, "REPORT_ENTRY_ISSUES", 1)
    monkeypatch.setattr(limits, "REPORT_LABEL_CHARS", 3)
    rows = [
        row(GENE, record_id=f"r{n}", object_id="HGNC:5", object_label="Gene five")
        for n in range(4)
    ]
    rows.append(row(GENE, record_id="bad", effect="negative", directed="maybe"))
    release = registered(tmp_path, rows)
    report = run_of(release, create_entities=True).report
    [planned] = report["would_create"]["entries"]
    assert (planned["lines"], planned["label"], planned["records"]) == (
        [2, 3],
        "Gen",
        4,
    )
    [rejected] = report["rejected"]["entries"]
    assert len(rejected["issues"]) == 1
    stored = SourceRecord.objects.get(release=release, record_key="bad")
    assert len(stored.issues) == 2


def test_a_report_past_its_byte_budget_is_marked_truncated(
    registry, tmp_path, genes, monkeypatch
):
    rows = [row(GENE, record_id=f"bad{n}", effect="negative") for n in range(20)]
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "REPORT_BYTES", 8_000)
    report = run_of(release).report
    assert report["truncated"] is True
    assert report["rejected"]["total"] == 20
    assert len(report["rejected"]["entries"]) < 20


def test_a_failed_run_leaves_no_report_file(registry, tmp_path, genes, monkeypatch):
    release = registered(tmp_path, [GENE, row(GENE, record_id="r2")])
    target = tmp_path / "out" / "report.jsonl"
    target.parent.mkdir()

    def broken(plans, *, run):
        raise RuntimeError("the disk is gone")

    monkeypatch.setattr(dry_run_module, "write_records", broken)
    with pytest.raises(RuntimeError):
        run_of(release, report_file=target)
    assert list(target.parent.iterdir()) == []
    run = ImportRun.objects.get(release=release, mode=ImportMode.DRY_RUN)
    assert run.state == ImportState.FAILED
    assert run.error == "RuntimeError('the disk is gone')"
    assert run.finished_at is not None


# ── Stored files and file errors ────────────────────────────────────────────


def test_a_tampered_stored_file_refuses_the_run(registry, tmp_path, genes):
    release = registered(tmp_path)
    stored = storage.root() / data_file(release).storage_path
    stored.write_bytes(stored.read_bytes().replace(b"HGNC:6973", b"HGNC:6974"))
    run = run_of(release)
    assert run.state == ImportState.COMPLETED
    assert run.report["verdict"] == "refused"
    [error] = run.report["file_errors"]["entries"]
    assert error["code"] == "stored_file_mismatch"
    assert SourceRecord.objects.filter(release=release).count() == 0


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX")
def test_a_stored_file_replaced_by_a_fifo_refuses_the_run_at_once(
    registry, tmp_path, genes
):
    release = registered(tmp_path)
    stored = storage.root() / data_file(release).storage_path
    stored.unlink()
    os.mkfifo(stored)
    with deadline(5):
        run = run_of(release)
    assert run.report["verdict"] == "refused"
    [error] = run.report["file_errors"]["entries"]
    assert error["code"] == "stored_file_mismatch"
    assert "is not a regular file" in error["message"]


def test_a_tampered_manifest_refuses_the_run(registry, tmp_path, genes):
    release = registered(tmp_path)
    manifest = release.files.get(role=FileRole.MANIFEST)
    (storage.root() / manifest.storage_path).unlink()
    report = run_of(release).report
    assert report["verdict"] == "refused"
    assert report["file_errors"]["entries"][0]["code"] == "stored_file_mismatch"
    assert "is missing" in report["file_errors"]["entries"][0]["message"]


def test_an_invalid_manifest_refuses_the_run(registry, tmp_path, genes):
    release = registered(tmp_path, manifest=False, approved=False)
    incoming = storage.stream_to_incoming(
        io.BytesIO(manifest_bytes(is_synthetic=True)), max_bytes=10_000
    )
    with transaction.atomic():
        storage.place(
            release, incoming, role=FileRole.MANIFEST, original_name="manifest.json"
        )
    approved_for_internal_use(release)
    report = run_of(release).report
    assert report["verdict"] == "refused"
    [error] = report["file_errors"]["entries"]
    assert error["code"] == "manifest_invalid"
    assert SourceRecord.objects.filter(release=release).count() == 0


def test_a_header_error_writes_no_record(registry, tmp_path, genes):
    data = tsv(GENE, header=[*HEADER, "colour"])
    release = registered(tmp_path, data=data)
    run = run_of(release)
    assert run.state == ImportState.COMPLETED
    assert run.report["verdict"] == "refused"
    assert run.report["file_errors"]["entries"][0]["code"] == "header_unknown_column"
    assert SourceRecord.objects.filter(release=release).count() == 0


def test_a_later_file_error_keeps_the_records_read_before_it(
    registry, tmp_path, genes, monkeypatch
):
    rows = [row(GENE, record_id=f"r{n}") for n in range(5)]
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "MAX_DATA_LINES", 3)
    run = run_of(release)
    assert run.report["verdict"] == "refused"
    [error] = run.report["file_errors"]["entries"]
    assert (error["code"], error["line"]) == ("too_many_lines", 5)
    assert sorted(statuses(release)) == ["r0", "r1", "r2"]


def planned_batches(monkeypatch) -> list:
    """The first line of each batch the dry run plans."""
    firsts = []
    plan = dry_run_module.plan_batch

    def recording(records, *, run_ctx):
        firsts.append(records[0].line_number)
        return plan(records, run_ctx=run_ctx)

    monkeypatch.setattr(dry_run_module, "plan_batch", recording)
    return firsts


def mapping_runs(monkeypatch) -> list:
    """Each MappingRun the dry run makes."""
    made = []

    class Kept(mapping.MappingRun):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            made.append(self)

    monkeypatch.setattr(dry_run_module, "MappingRun", Kept)
    return made


TOO_MANY = (
    "The file states more than 3 distinct identifiers, which a run does not keep: "
    "split it into releases."
)


@pytest.mark.parametrize(
    "trailing", [False, True], ids=["in the loop", "in the trailing batch"]
)
def test_a_file_past_the_identifiers_a_run_keeps_is_refused_once(
    registry, tmp_path, genes, monkeypatch, trailing
):
    first = row(GENE, record_id="k")
    rows = [
        # Batch 1: two identifiers.
        first,
        row(GENE, record_id="r1"),
        # Batch 2 states k again otherwise, which rejects line 2's record:
        # the file is to be read again.
        {**first, "evidence_group": "other"},
        row(GENE, record_id="r2"),
        # Batch 3 states two more: past the limit.
        row(GENE, record_id="r3", subject_id="HGNC:7", object_id="HGNC:8"),
        row(GENE, record_id="r4"),
    ]
    if not trailing:
        # Batch 4, read before batch 3 is planned, and never planned.
        rows.append(row(GENE, record_id="r5"))
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    monkeypatch.setattr(limits, "MAX_IDENTIFIERS", 3)
    readings = counted_readings(monkeypatch)
    planned = planned_batches(monkeypatch)
    written = counted_batches(monkeypatch)
    runs = mapping_runs(monkeypatch)
    run = run_of(release)
    assert run.state == ImportState.COMPLETED
    report = run.report
    assert report["verdict"] == "refused"
    [error] = report["file_errors"]["entries"]
    # The first line of the batch that passed it, not a second reading's.
    assert (error["code"], error["line"], error["message"]) == (
        "too_many_identifiers",
        6,
        TOO_MANY,
    )
    # Read once, though batch 2 asked for a second reading; batch 3 planned
    # once, and dropped.
    [mapping_run] = runs
    assert mapping_run.read_again is True
    assert readings == [1]
    assert planned == [2, 4, 6]
    assert written == [2, 2]
    assert statuses(release) == {"k": REJECTED, "r1": MAPPED, "r2": MAPPED}
    counts = report["counts"]
    # What batches 1 and 2 gave: lines 2 to 5.
    assert sum(counts["records"].values()) + counts["unstored"] == 4
    assert (counts["created"], counts["updated"]) == (3, 1)
    summary = dry_run_module.summary_lines(run)
    assert f"File error too_many_identifiers: {TOO_MANY}" in summary


def test_a_batch_that_asks_for_a_second_reading_and_passes_the_limit(
    registry, tmp_path, monkeypatch
):
    rows = [
        # Batch 1 plans P12345 at taxon 9606, and Q00987.
        row(PROTEIN, subject_id="P12345", object_id="Q00987"),
        # Batch 2 states P12345 at taxon 7227, which takes back what batch 1
        # planned (a second reading is asked), and a fourth identifier.
        row(
            PROTEIN,
            record_id="r2",
            subject_id="P12345",
            subject_taxon="7227",
            object_id="Q99999",
        ),
        row(PROTEIN, record_id="r3"),
    ]
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    monkeypatch.setattr(limits, "MAX_IDENTIFIERS", 3)
    readings = counted_readings(monkeypatch)
    planned = planned_batches(monkeypatch)
    runs = mapping_runs(monkeypatch)
    report = run_of(release, create_entities=True).report
    assert report["verdict"] == "refused"
    [error] = report["file_errors"]["entries"]
    assert (error["code"], error["line"], error["message"]) == (
        "too_many_identifiers",
        3,
        TOO_MANY,
    )
    [mapping_run] = runs
    assert mapping_run.read_again is True
    assert readings == [1]
    assert planned == [2, 3]
    # The counts and records are batch 1's.
    assert report["counts"]["records"] == {
        **{status: 0 for status in RecordStatus.values},
        "mapped": 1,
    }
    assert statuses(release) == {"r1": MAPPED}


# ── Batches ─────────────────────────────────────────────────────────────────


def counted_batches(monkeypatch) -> list:
    sizes = []
    write = dry_run_module.write_records

    def counting(plans, *, run):
        sizes.append(len(plans))
        return write(plans, run=run)

    monkeypatch.setattr(dry_run_module, "write_records", counting)
    return sizes


def test_batches_hold_at_most_their_rows_and_bytes(
    registry, tmp_path, genes, monkeypatch
):
    rows = [row(GENE, record_id=f"r{n}") for n in range(5)]
    release = registered(tmp_path, rows)
    sizes = counted_batches(monkeypatch)
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    run_of(release)
    assert sizes == [2, 2, 1]
    sizes.clear()
    monkeypatch.setattr(limits, "BATCH_ROWS", 500)
    line = len("\t".join(row(GENE).get(column, "") for column in HEADER).encode())
    monkeypatch.setattr(limits, "BATCH_BYTES", 3 * line)
    run_of(release)
    assert sizes == [3, 2]


def test_duplicates_are_found_across_batches(registry, tmp_path, genes):
    rows = [row(GENE, record_id=f"r{n}") for n in range(1999)]
    rows[0] = row(GENE, record_id="dup-a")
    rows[1] = row(GENE, record_id="dup-b")
    rows[1497] = row(GENE, record_id="dup-a")
    rows[1998] = row(GENE, record_id="dup-b", evidence_group="other")
    release = registered(tmp_path, rows)
    report = run_of(release).report
    found = statuses(release)
    assert len(found) == 1997
    assert (found["dup-a"], found["dup-b"]) == (MAPPED, REJECTED)
    unstored = {
        entry["line"]: entry["codes"] for entry in report["unstored"]["entries"]
    }
    assert unstored == {1499: ["duplicate_record"], 2000: ["conflicting_duplicate"]}
    record = SourceRecord.objects.get(release=release, record_key="dup-b")
    [issue] = record.issues
    assert issue["code"] == "conflicting_duplicate" and "line 2000" in issue["message"]
    assert issue["line"] == 3
    assert report["counts"]["records"]["rejected"] == 1
    assert report["counts"]["records"]["mapped"] == 1996
    assert [entry["key"] for entry in report["rejected"]["entries"]] == ["dup-b"]


def mapped_sections(report) -> dict:
    """What a report says of the records that map, and of the rows."""
    counts = {
        name: value
        for name, value in report["counts"].items()
        if name not in ("created", "updated")
    }
    return {
        "counts": counts,
        **{
            name: report[name]
            for name in (
                "rejected",
                "unstored",
                "identifiers",
                "would_create",
                "label_conflicts",
                "relationships",
                "assertions",
            )
        },
    }


def test_a_conflict_across_batches_takes_back_what_its_first_line_counted(
    registry, tmp_path, genes, monkeypatch
):
    first = row(GENE, record_id="r1", object_id="HGNC:5", object_label="Wrong label")
    rows = [first, row(GENE, record_id="r2"), {**first, "evidence_group": "other"}]
    single = run_of(registered(tmp_path / "one", rows), create_entities=True).report
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    release = registered(tmp_path / "many", rows)
    report = run_of(release, create_entities=True).report
    assert statuses(release) == {"r1": REJECTED, "r2": MAPPED}
    for found in (single, report):
        counts = found["counts"]
        assert sum(entry["records"] for entry in counts["mapped"]) == 1
        assert counts["records"]["mapped"] == 1
        assert found["would_create"]["entries"] == []
        assert (found["relationships"]["existing"], found["relationships"]["new"]) == (
            0,
            1,
        )
        assert found["assertions"] == {"existing": 0, "new": 1}
        assert [entry["key"] for entry in found["rejected"]["entries"]] == ["r1"]
        assert [
            (entry["line"], entry["codes"]) for entry in found["unstored"]["entries"]
        ] == [(4, ["conflicting_duplicate"])]
    assert mapped_sections(report) == mapped_sections(single)


def issues_of(release, record_key) -> list:
    return SourceRecord.objects.get(release=release, record_key=record_key).issues


@pytest.mark.parametrize(
    "cells",
    [{"directed": "false"}, {"object_label": "Wrong label"}],
    ids=["refused by G3", "with a note"],
)
def test_a_conflicting_first_occurrence_has_its_issues_whatever_the_batches(
    registry, tmp_path, genes, monkeypatch, cells
):
    # The first occurrence maps to stored genes: G3 refuses its claim, or its
    # label differs. In one batch the conflict rejects it before G3's check,
    # and comes before the endpoints' notes.
    first = row(GENE, record_id="k", **cells)
    rows = [first, row(GENE, record_id="r2"), {**first, "evidence_group": "other"}]
    one = registered(tmp_path / "one", rows)
    single = run_of(one).report
    assert issues_of(one, "k")[0]["code"] == "conflicting_duplicate"
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    many = registered(tmp_path / "many", rows)
    for _ in range(2):
        report = run_of(many).report
        assert issues_of(many, "k") == issues_of(one, "k")
        assert report["rejected"] == single["rejected"]


@pytest.mark.parametrize("own,batch_rows", [(20, 1), (25, 2)])
def test_a_conflict_past_a_records_kept_issues_is_counted_once(
    registry, tmp_path, genes, monkeypatch, own, batch_rows
):
    # The first occurrence has issues of its own (papers that are not
    # papers) up to or past limits.RECORD_ISSUES, so the conflict a later
    # batch finds is folded into its count of further issues.
    first = row(GENE, record_id="k", publications="|".join(f"x{n}" for n in range(own)))
    rows = [
        first,
        row(GENE, record_id="r1"),
        row(GENE, record_id="r2"),
        {**first, "evidence_group": "other"},
    ]
    one = registered(tmp_path / "one", rows)
    single = run_of(one).report
    expected = issues_of(one, "k")
    assert expected[-1] == {"code": "more", "count": own + 1 - limits.RECORD_ISSUES}
    # The conflict is kept among the stored issues, so a later run finds it.
    assert expected[-2]["code"] == "conflicting_duplicate"
    monkeypatch.setattr(limits, "BATCH_ROWS", batch_rows)
    many = registered(tmp_path / "many", rows)
    readings = counted_readings(monkeypatch)
    counts = []
    for _ in range(3):
        readings.clear()
        report = run_of(many).report
        counts.append((len(readings), report["counts"]["updated"]))
        assert issues_of(many, "k") == expected
        assert report["rejected"] == single["rejected"]
    # The first run reads the file again once the later line's batch finds
    # the conflict; a re-run reads it once and changes nothing.
    assert [reading for reading, _ in counts] == [2, 1, 1]
    assert [updated for _, updated in counts[1:]] == [0, 0]


def counted_readings(monkeypatch) -> list:
    readings = []
    read = dry_run_module._read_batches

    def counting(*args, **kwargs):
        readings.append(1)
        return read(*args, **kwargs)

    monkeypatch.setattr(dry_run_module, "_read_batches", counting)
    return readings


def test_a_held_first_occurrence_a_later_batch_contradicts_is_listed_once(
    registry, tmp_path, genes, monkeypatch
):
    rows = [
        row(GENE, record_id="dup"),
        row(GENE, record_id="r1"),
        row(GENE, record_id="dup", evidence_group="other"),
    ]
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    run_of(release)
    # Evidence recorded from the first occurrence: it stays mapped, with the
    # conflict among its issues.
    SourceRecord.objects.filter(release=release, record_key="dup").update(status=MAPPED)
    with_evidence(release, "dup", regulation(*genes))
    readings = counted_readings(monkeypatch)
    for _ in range(2):
        readings.clear()
        report = run_of(release).report
        assert readings == [1]
        assert report["would_change"]["total"] == 1
        [entry] = report["would_change"]["entries"]
        assert (entry["key"], entry["line"], entry["planned"]) == ("dup", 2, REJECTED)
        record = SourceRecord.objects.get(release=release, record_key="dup")
        assert record.status == MAPPED
        assert [issue["code"] for issue in record.issues] == ["conflicting_duplicate"]
    monkeypatch.setattr(limits, "BATCH_ROWS", 500)
    single = run_of(release).report
    for name in ("would_change", "rejected"):
        assert report[name] == single[name]


def test_one_accession_stated_at_two_taxa_creates_nothing_across_batches(
    registry, tmp_path, monkeypatch
):
    rows = [
        row(PROTEIN, subject_id="P12345", object_id="Q00987"),
        row(PROTEIN, record_id="r2", subject_id="P12345", subject_taxon="7227"),
    ]
    single = run_of(registered(tmp_path / "one", rows), create_entities=True).report
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    release = registered(tmp_path / "many", rows)
    report = run_of(release, create_entities=True).report
    assert statuses(release) == {"r1": REJECTED, "r2": REJECTED}
    for found in (single, report):
        assert found["would_create"]["total"] == 0
        assert found["counts"]["records"]["rejected"] == 2
        assert found["counts"]["mapped"] == []
        assert [entry["key"] for entry in found["rejected"]["entries"]] == ["r1", "r2"]
    assert mapped_sections(report) == mapped_sections(single)
    record = SourceRecord.objects.get(release=release, record_key="r1")
    [issue] = [i for i in record.issues if i["code"] == "taxon_contradiction"]
    assert "a protein of taxon 9606 (line 2)" in issue["message"]
    assert "a protein of taxon 7227 (line 3)" in issue["message"]


def test_a_rerun_leaves_a_conflict_across_batches_rejected(
    registry, tmp_path, genes, monkeypatch
):
    rows = [
        row(GENE, record_id="dup"),
        row(GENE, record_id="r1"),
        row(GENE, record_id="r2"),
        row(GENE, record_id="dup", evidence_group="other"),
    ]
    release = registered(tmp_path, rows)
    # Lines 2 and 5 fall in different batches.
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    first = run_of(release).report
    assert first["counts"]["records"]["rejected"] == 1
    seen = []
    write = dry_run_module.write_records

    def checking(plans, *, run):
        result = write(plans, run=run)
        record = SourceRecord.objects.get(release=release, record_key="dup")
        seen.append((record.status, [issue["code"] for issue in record.issues]))
        return result

    monkeypatch.setattr(dry_run_module, "write_records", checking)
    second = run_of(release).report
    assert seen == [(REJECTED, ["conflicting_duplicate"])] * 2
    assert second["counts"]["updated"] == 0
    assert second["counts"]["records"] == first["counts"]["records"]
    assert second["rejected"] == first["rejected"]


def test_the_report_file_lists_a_long_refused_identifier(registry, tmp_path, genes):
    release = registered(tmp_path, [row(GENE, subject_id="HGNC:" + "1" * 9_000)])
    target = tmp_path / "report.jsonl"
    run_of(release, report_file=target)
    [refused] = [
        entry
        for entry in map(json.loads, target.read_text().splitlines())
        if entry["section"] == "identifiers" and entry["mapping"] == "refused"
    ]
    assert (refused["code"], refused["lines"], refused["records"]) == (
        "bad_identifier",
        [2],
        1,
    )
    assert len(refused["value"]) <= limits.QUOTED_VALUE_CHARS


def test_queries_per_batch_do_not_grow_with_the_batch(registry, tmp_path, genes):
    staff = StaffUserFactory()

    def queries(count):
        rows = [row(GENE, record_id=f"r{n}") for n in range(count)]
        rows += [
            row(GENE, record_id=f"new{n}", object_id=f"HGNC:{100 + n}")
            for n in range(count)
        ]
        release = registered(tmp_path / str(count), rows)
        with CaptureQueriesContext(connection) as captured:
            dry_run(release, triggered_by=staff, create_entities=True)
        return len(captured)

    queries(2)
    assert queries(3) == queries(40)


# ── A release that changes under the run ────────────────────────────────────


def _stage(release):
    release.refresh_from_db()
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])


def _open_commit(release):
    ImportRunFactory(release=release, file=data_file(release), mode=ImportMode.COMMIT)


def _revoke(release):
    for approval in UsageApproval.objects.filter(release=release):
        revoke(approval, reason="Test.", revoked_by=StaffUserFactory())


def _resync(release):
    RegistryState.objects.filter(pk=1).update(version="9.9")


def _reregister(release):
    release.refresh_from_db()
    release.adapter_version, release.mapping_version = "1.1.0", "2"
    release.save(update_fields=["adapter_version", "mapping_version", "updated_at"])


@pytest.mark.parametrize(
    "change,message",
    [
        (_stage, "is staged now"),
        (_open_commit, "has an open commit run now"),
        (_revoke, "has no effective internal approval now"),
        (_resync, "registry is at version 9.9 now"),
        (_reregister, "reads with canonical_tsv_v1 1.1.0"),
    ],
)
def test_a_release_that_changes_mid_run_stops_it_as_failed(
    registry, tmp_path, genes, monkeypatch, change, message
):
    rows = [row(GENE, record_id=f"r{n}") for n in range(5)]
    release = registered(tmp_path, rows)
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    plan = dry_run_module.plan_batch
    calls = []

    def changing(records, *, run_ctx):
        calls.append(1)
        if len(calls) == 2:
            change(release)
        return plan(records, run_ctx=run_ctx)

    monkeypatch.setattr(dry_run_module, "plan_batch", changing)
    with pytest.raises(RunStopped, match=message):
        run_of(release)
    run = ImportRun.objects.get(release=release, mode=ImportMode.DRY_RUN)
    assert run.state == ImportState.FAILED and run.error.startswith("RunStopped(")
    assert run.finished_at is not None
    assert sorted(statuses(release)) == ["r0", "r1"]


def test_a_release_registered_again_mid_run_stops_it(
    registry, tmp_path, genes, monkeypatch
):
    rows = [row(GENE, record_id=f"r{n}") for n in range(6)]
    release = registered(tmp_path, rows)
    folder = tmp_path / f"upload-{release.dataset.slug}-v1"
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    upgraded = SimpleNamespace(spec=replace(SPEC, version="1.1.0", mapping_version="2"))
    plan = dry_run_module.plan_batch
    calls = []

    def registering(records, *, run_ctx):
        calls.append(1)
        if len(calls) == 2:
            # Another process, with the code's next adapter version.
            monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, upgraded)
            registration = register_release(
                dataset=release.dataset.slug,
                version="v1",
                adapter=SPEC.name,
                file=folder / "release.tsv",
                manifest=folder / "manifest.json",
                registered_by=StaffUserFactory(),
            )
            assert registration.outcome == "updated"
        return plan(records, run_ctx=run_ctx)

    monkeypatch.setattr(dry_run_module, "plan_batch", registering)
    with pytest.raises(RunStopped, match="registered again"):
        run_of(release)
    run = ImportRun.objects.get(release=release, mode=ImportMode.DRY_RUN)
    assert run.state == ImportState.FAILED
    assert not SourceRecord.objects.filter(release=release).exists()


def test_a_release_registered_again_after_its_last_batch_fails_the_run(
    registry, tmp_path, genes, monkeypatch
):
    release = registered(tmp_path)
    target = tmp_path / "out" / "report.jsonl"
    target.parent.mkdir()
    finish = dry_run_module.Report.finish

    def registering(report):
        _reregister(release)
        return finish(report)

    monkeypatch.setattr(dry_run_module.Report, "finish", registering)
    with pytest.raises(RunStopped, match="registered again"):
        run_of(release, report_file=target)
    run = ImportRun.objects.get(release=release, mode=ImportMode.DRY_RUN)
    assert run.state == ImportState.FAILED
    # Its report file was written, and is removed with the run's failure.
    assert list(target.parent.iterdir()) == []


@pytest.mark.postgres
def test_each_batch_locks_the_release_before_checking_it_again(
    registry, tmp_path, genes, monkeypatch
):
    release = registered(tmp_path, [GENE])
    batches = []
    write = dry_run_module.write_records

    def capturing(plans, *, run):
        with CaptureQueriesContext(connection) as captured:
            result = write(plans, run=run)
        batches.append([query["sql"] for query in captured])
        return result

    monkeypatch.setattr(dry_run_module, "write_records", capturing)
    run_of(release)
    [sql] = batches
    lock = next(
        n
        for n, text in enumerate(sql)
        if "FOR UPDATE" in text and 'FROM "graph_datasetrelease"' in text
    )
    check = next(
        n
        for n, text in enumerate(sql)
        if 'FROM "graph_importrun"' in text and "'commit'" in text
    )
    assert lock < check


# ── The command ─────────────────────────────────────────────────────────────


def command(*args) -> str:
    out = io.StringIO()
    call_command("graph_import", *map(str, args), stdout=out)
    return out.getvalue()


def test_the_command_dry_runs_and_prints_a_summary(registry, tmp_path, genes):
    release = registered(tmp_path, [GENE, row(GENE, record_id="bad", effect="x")])
    staff = StaffUserFactory()
    report = tmp_path / "report.jsonl"
    out = command(
        "--release",
        release.pk,
        "--dry-run",
        "--report-file",
        report,
        "--as",
        staff.username,
    )
    run = ImportRun.objects.get(release=release)
    assert f"Dry run {run.pk} of release {release.pk}: ok." in out
    assert "1 mapped" in out and "1 rejected" in out
    assert f"Wrote every list entry to {report}." in out
    assert run.triggered_by == staff and run.triggered_by_username == staff.username
    assert run.parameters == {
        "create_entities": False,
        "resolutions": {"sha256": "", "entries": []},
        "registry_version": REGISTRY_VERSION,
    }
    assert (run.adapter, run.adapter_version, run.mapping_version) == (
        SPEC.name,
        SPEC.version,
        SPEC.mapping_version,
    )
    assert run.file == data_file(release) and run.finished_at is not None


def test_the_command_passes_its_options_to_the_run(registry, tmp_path, genes):
    tp53, mdm2 = genes
    release = registered(
        tmp_path,
        [row(GENE, object_id="HGNC:5"), row(GENE, record_id="r2", object_id="HGNC:8")],
    )
    staff = StaffUserFactory()
    command(
        "--release",
        release.pk,
        "--dry-run",
        "--create-entities",
        "--resolutions",
        resolution_file(tmp_path, mdm2, "HGNC:8", "HGNC:6973"),
        "--as",
        staff.username,
    )
    run = ImportRun.objects.get(release=release)
    assert run.parameters["create_entities"] is True
    [entry] = run.parameters["resolutions"]["entries"]
    assert (entry["value"], entry["entity"]) == ("HGNC:8", mdm2.pk)
    assert len(run.parameters["resolutions"]["sha256"]) == 64
    assert statuses(release) == {"r1": MAPPED, "r2": MAPPED}


def test_the_command_commits_after_a_dry_run_and_refuses_without_the_switch(
    registry, settings, tmp_path, genes
):
    release = registered(tmp_path)
    staff = StaffUserFactory()
    # A commit trusts the release's latest dry run, and there is none yet.
    with pytest.raises(CommandError) as raised:
        command("--release", release.pk, "--commit", "--as", staff.username)
    assert str(raised.value) == (
        f"Release {release.pk} has no dry run: run graph_import --dry-run first, with "
        "the options you will commit with. Nothing was written."
    )
    # A commit keeps its report with its run.
    with pytest.raises(CommandError, match="--report-file goes with --dry-run"):
        command(
            "--release",
            release.pk,
            "--commit",
            "--report-file",
            tmp_path / "report.jsonl",
            "--as",
            staff.username,
        )
    with pytest.raises(CommandError, match="names no account"):
        command("--release", release.pk, "--dry-run", "--as", "nobody")
    with pytest.raises(CommandError, match="precondition|Unknown release"):
        command("--release", release.pk + 99, "--dry-run", "--as", staff.username)
    settings.TYPED_GRAPH_ENABLED = False
    for mode in ("--dry-run", "--commit"):
        with pytest.raises(CommandError, match="TYPED_GRAPH_ENABLED"):
            command(
                "--release",
                release.pk,
                mode,
                "--resolutions",
                tmp_path / "missing.json",
                "--as",
                "nobody",
            )
    assert ImportRun.objects.count() == 0
    settings.TYPED_GRAPH_ENABLED = True
    command("--release", release.pk, "--dry-run", "--as", staff.username)
    out = command("--release", release.pk, "--commit", "--as", staff.username)
    run = ImportRun.objects.get(release=release, mode=ImportMode.COMMIT)
    assert out.startswith(
        f"Commit run {run.pk} of release {release.pk}: completed, and the release is "
        "staged."
    )


def test_the_command_refuses_an_active_account_that_is_not_staff(registry, tmp_path):
    release = registered(tmp_path, synthetic=True, approved=False)
    clerk = StaffUserFactory(is_staff=False)
    with pytest.raises(CommandError, match="active staff account"):
        command("--release", release.pk, "--dry-run", "--as", clerk.username)
    assert not ImportRun.objects.filter(mode=ImportMode.DRY_RUN).exists()
    assert SourceRecord.objects.count() == 0


def test_the_command_reports_a_run_that_stopped(registry, tmp_path, genes, monkeypatch):
    release = registered(tmp_path)

    def changing(records, *, run_ctx):
        _stage(release)
        return mapping.plan_batch(records, run_ctx=run_ctx)

    monkeypatch.setattr(dry_run_module, "plan_batch", changing)
    staff = StaffUserFactory()
    with pytest.raises(CommandError, match="stopped, and its run is marked failed"):
        command("--release", release.pk, "--dry-run", "--as", staff.username)
    assert ImportRun.objects.get(release=release).state == ImportState.FAILED


def test_the_release_is_found_by_its_id_only(registry, tmp_path, genes):
    release = registered(tmp_path)
    assert DatasetRelease.objects.filter(pk=release.pk).exists()
    with pytest.raises(DryRunError, match="Unknown release"):
        run_of(str(release.pk))
