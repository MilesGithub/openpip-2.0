"""The commit (graph.ingest.commit and graph_import --commit): its
preconditions in order, the open commit run that decides first, the dry run
it trusts, the planning's refusals (the file, the records, the statuses,
the resume limit), what it writes (entities, labels, papers, terms,
relationships, evidence; A7, A8), committing again (A1), a commit that
stops and resumes (A2), one whose session ends, the failure marking, the
checks of each batch, races that refuse, publication-identifier conflicts
(A6), the report, the statistics and the staging, and the command.

The discard and graph_check are tested in test_lifecycle_commands and
test_graph_check; here they complete the commit's cases: a release
discarded and imported again (H7, 12.6, A1), a run left running listed by
graph_check (A2), and a conflict listed for review (A6). The threaded races
and the real kill are in test_commit_concurrency."""

import io
import json
import threading
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import InterfaceError, OperationalError, connection, transaction

from graph import releases as releases_module
from graph import storage
from graph.approvals import revoke
from graph.entities import add_identifier, create_entity
from graph.ingest import commit as commit_module
from graph.ingest import dry_run as dry_run_module
from graph.ingest import limits
from graph.ingest import registry as adapters
from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.commit import (
    ALREADY_COMMITTED,
    COMMITTED,
    RESUMED,
    CommitError,
    CommitResult,
    CommitStopped,
    commit_release,
    commit_summary_lines,
)
from graph.management.commands import graph_import as graph_import_command
from graph.ingest.dry_run import DryRunError, dry_run
from graph.ingest.protocol import Issue
from graph.ingest.registration import RegistrationError, register_release
from graph.ingest.runlock import release_run_lock, run_lock_holder, runs_are_locked
from graph.models import (
    ApprovalScope,
    AssertionParticipant,
    DatasetRelease,
    Entity,
    EntityIdentifier,
    EntityStatus,
    EvidenceAssertion,
    EvidenceMethod,
    EvidencePublication,
    FileRole,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    Outcome,
    Publication,
    RecordStatus,
    RegistryState,
    Relationship,
    ReleaseKind,
    ReleaseStatus,
    SourceRecord,
    Term,
    UsageApproval,
)
from graph.checks import run_checks
from graph.registry import REGISTRY_VERSION
from graph.releases import discard_release, publish_release, withdraw_release
from graph.stats import release_stats
from graph.tests.factories import (
    DatasetReleaseFactory,
    ImportRunFactory,
    PublicationFactory,
    SourceFileFactory,
    SourceRecordFactory,
    StaffUserFactory,
    TermFactory,
    approve,
)
from graph.tests.ingest_helpers import (
    GENE,
    PROTEIN,
    SYNTHETIC,
    TERMS,
    approved_for_internal_use,
    committed,
    manifest_bytes,
    natural_rows,
    registered,
    row,
    write,
)
from graph.tests.lock_helpers import WAIT_SECONDS, held_run_lock, terminate_backend

MAPPED, UNRESOLVED, AMBIGUOUS, REJECTED = (
    RecordStatus.MAPPED,
    RecordStatus.UNRESOLVED,
    RecordStatus.AMBIGUOUS,
    RecordStatus.REJECTED,
)
RUNNING, FAILED, COMPLETED, ABANDONED = (
    ImportState.RUNNING,
    ImportState.FAILED,
    ImportState.COMPLETED,
    ImportState.ABANDONED,
)
# The advice refusal 12 gives, and the one every refusal of 11 to 15 gives
# while the release has an open commit run.
DRY_RUN_AGAIN = (
    "run graph_import --dry-run again, with the options you will commit with. "
    "Nothing was written."
)


def open_run_ending(run) -> str:
    return (
        f"Its open commit run {run} cannot be resumed while this holds: discard it "
        "with graph_discard_release, then dry-run it again. Nothing was written."
    )


def deadlocked(*args, **kwargs):
    """A database error, as PostgreSQL words a deadlock: its first line
    without a final full stop."""
    raise OperationalError(
        "deadlock detected\nDETAIL:  Process 1 waits for ShareLock on transaction "
        "2; blocked by process 3."
    )


@pytest.fixture
def genes(registry):
    """The human genes GENE names: TP53 and MDM2."""
    return (
        create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998"),
        create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973"),
    )


def data_file(release):
    return release.files.get(role=FileRole.DATA)


def dry(release, **options) -> ImportRun:
    """A dry run that completes."""
    run = dry_run(release, triggered_by=StaffUserFactory(), **options)
    assert run.state == COMPLETED, run.error
    return run


def commit(release, *, by=None, **options):
    """commit_release, triggered by `by` or a new staff account."""
    return commit_release(release, triggered_by=by or StaffUserFactory(), **options)


def commit_runs(release):
    return ImportRun.objects.filter(release=release, mode=ImportMode.COMMIT)


def statuses(release) -> dict:
    return dict(
        SourceRecord.objects.filter(release=release).values_list("record_key", "status")
    )


def table_contents(left_out=()) -> dict:
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


def command(*args) -> str:
    out = io.StringIO()
    call_command("graph_import", *map(str, args), stdout=out)
    return out.getvalue()


def stopping(monkeypatch, release, record_key, error=None):
    """commit.record_claims raises, for the record of this key, after the
    batch's other writes; returns the real record_claims."""
    target = SourceRecord.objects.get(release=release, record_key=record_key).pk
    real = commit_module.record_claims

    def recording(source_record, claims, *, import_run):
        if source_record == target:
            raise error if error is not None else ValueError("Stopped for the test.")
        return real(source_record, claims, import_run=import_run)

    monkeypatch.setattr(commit_module, "record_claims", recording)
    return real


def before_writing(monkeypatch, n, action):
    """Call action() before the nth batch of the commit's writing reading
    (commit.plan_batch called with check=False), in the commit's own
    thread."""
    real = commit_module.plan_batch
    calls = []

    def planning(records, *, run_ctx, check=True):
        if not check:
            calls.append(1)
            if len(calls) == n:
                action()
        return real(records, run_ctx=run_ctx, check=check)

    monkeypatch.setattr(commit_module, "plan_batch", planning)
    return calls


# ── Preconditions ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "locked",
    [False, pytest.param(True, marks=pytest.mark.postgres)],
    ids=["without the lock held", "with the lock held"],
)
def test_the_preconditions_are_checked_in_order(
    registry, settings, tmp_path, monkeypatch, genes, locked
):
    release = registered(
        tmp_path, [GENE, row(GENE, record_id="r2", object_id="HGNC:5")], approved=False
    )
    RegistryState.objects.filter(pk=1).update(version="0.9")
    settings.TYPED_GRAPH_ENABLED = False
    bad = write(tmp_path, "resolutions.json", b'{"resolutions": [1]}')
    inactive = StaffUserFactory(is_active=False)
    clerk = StaffUserFactory(is_staff=False)
    staff = StaffUserFactory()
    arguments = {"resolutions": bad}

    def refused(user=staff, target=release) -> CommitError:
        contents = table_contents()
        with pytest.raises(CommitError) as raised:
            commit_release(target, triggered_by=user, **arguments)
        assert str(raised.value).endswith(" Nothing was written."), raised.value
        assert table_contents() == contents
        return raised.value

    def by_command(error):
        # The command words it as the service does.
        with pytest.raises(CommandError) as raised:
            command("--release", release.pk, "--commit", "--as", staff.username)
        assert str(raised.value) == str(error)

    assert refused(inactive).precondition == 1
    settings.TYPED_GRAPH_ENABLED = True
    assert refused(inactive).precondition == 2
    assert refused(clerk).precondition == 2
    assert refused().precondition == 3
    RegistryState.objects.filter(pk=1).update(version=REGISTRY_VERSION)
    assert refused(target=release.pk + 1000).precondition == 4
    assert refused().precondition == 5
    arguments["resolutions"] = None
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])
    assert refused().precondition == 6
    release.status = ReleaseStatus.DRAFT
    release.save(update_fields=["status", "updated_at"])
    incoming = storage.stream_to_incoming(io.BytesIO(b"x\n"), max_bytes=100)
    with transaction.atomic():
        extra = storage.place(
            release, incoming, role=FileRole.DATA, original_name="more.tsv"
        )
    assert refused().precondition == 7
    extra.delete()
    assert refused().precondition == 8
    approved_for_internal_use(release)
    if locked:
        with held_run_lock(release.pk):
            error = refused()
            assert error.precondition == 9
            assert str(error) == (
                f"A dry run, commit or discard of release {release.pk} is running: "
                "one runs at a time. Nothing was written."
            )
            by_command(error)
    other_options = ImportRunFactory(
        release=release,
        file=data_file(release),
        mode=ImportMode.COMMIT,
        state=FAILED,
        adapter=SPEC.name,
        adapter_version=SPEC.version,
        mapping_version=SPEC.mapping_version,
        parameters={
            "create_entities": True,
            "resolutions": {"sha256": "", "entries": []},
            "registry_version": REGISTRY_VERSION,
            "dry_run": None,
        },
    )
    error = refused()
    assert error.precondition == 10
    by_command(error)
    other_options.state = ABANDONED
    other_options.save(update_fields=["state"])
    stale = SimpleNamespace(spec=replace(SPEC, version="1.1.0"))
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, stale)
    assert refused().precondition == 11
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, ADAPTER)
    assert refused().precondition == 12
    dry(release)
    monkeypatch.setattr(limits, "MAX_IDENTIFIERS", 1)
    assert refused().precondition == 13
    monkeypatch.setattr(limits, "MAX_IDENTIFIERS", 250_000)
    SourceRecord.objects.filter(release=release, record_key="r2").delete()
    assert refused().precondition == 14
    dry(release)
    create_entity("gene", "Gene five", 9606, "hgnc", "HGNC:5")
    error = refused()
    assert error.precondition == 15
    by_command(error)
    dry(release)
    result = commit_release(release, triggered_by=staff)
    assert (result.outcome, result.run.state) == (COMMITTED, COMPLETED)


def test_an_unknown_or_withdrawn_release_is_refused(registry, tmp_path, genes):
    release = registered(tmp_path)
    for target in (release.pk + 1000, str(release.pk), True, 2**63):
        with pytest.raises(CommitError) as raised:
            commit(target)
        assert raised.value.precondition == 4
        assert str(raised.value) == f"Unknown release {target!r}. Nothing was written."
    committed(release)
    withdraw_release(release, reason="Test.", withdrawn_by=StaffUserFactory())
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 4
    assert str(raised.value) == (
        f"Release {release.pk} is withdrawn, and a withdrawal is final: it is never "
        "committed. Nothing was written."
    )


def test_the_switch_staff_and_registry_are_worded_for_a_commit(
    registry, settings, tmp_path, genes
):
    release = registered(tmp_path)
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert str(raised.value) == (
        "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no release "
        "is committed. Nothing was written."
    )
    settings.TYPED_GRAPH_ENABLED = True
    clerk = StaffUserFactory(is_staff=False)
    with pytest.raises(CommitError) as raised:
        commit_release(release, triggered_by=clerk)
    assert str(raised.value) == (
        f"The run is triggered by an active staff account, not {clerk!r}. Nothing "
        "was written."
    )
    RegistryState.objects.filter(pk=1).update(version="0.9")
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert str(raised.value) == (
        f"The database's registry is at version 0.9, and this code's at "
        f"{REGISTRY_VERSION}: run graph_sync_registry first. Nothing was written."
    )


def test_a_release_with_no_effective_internal_approval_is_refused(
    registry, tmp_path, genes
):
    release = registered(tmp_path, approved=False)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 8
    assert str(raised.value) == (
        f"Release {release.pk} has no effective internal approval: record one with "
        "graph_approve. Nothing was written."
    )
    # A synthetic release needs none to be committed.
    synthetic = registered(tmp_path, [SYNTHETIC], synthetic=True)
    result = committed(synthetic, create_entities=True)
    assert result.run.state == COMPLETED


def test_a_release_that_is_not_a_draft_is_refused_with_what_to_do(
    registry, tmp_path, genes
):
    release = registered(tmp_path)
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 6
    assert str(raised.value) == (
        f"Release {release.pk} is staged without a completed commit run: discard it "
        "with graph_discard_release. Nothing was written."
    )
    other = registered(tmp_path, [row(GENE, record_id="o1")], version="v2")
    run = committed(other).run
    publish_release(other, published_by=StaffUserFactory())
    with pytest.raises(CommitError) as raised:
        commit(other, create_entities=True)
    assert raised.value.precondition == 6
    assert str(raised.value) == (
        f"Release {other.pk} is published, and a published release is never committed "
        "again: a correction is a new release that supersedes it. Nothing was "
        "written."
    )
    # With the options it was committed with, it was committed already.
    again = commit(other)
    assert (again.outcome, again.run.pk) == (ALREADY_COMMITTED, run.pk)
    assert commit_summary_lines(again) == [
        f"Release {other.pk} was committed by run {run.pk}, and is published: "
        "nothing was written."
    ]


@pytest.mark.parametrize("reason", ["unreadable", "invalid"])
def test_a_bad_resolution_file_is_refused_as_the_dry_run_words_it(
    registry, tmp_path, genes, reason
):
    release = registered(tmp_path)
    path = tmp_path / "missing.json"
    if reason == "invalid":
        path = write(tmp_path, "resolutions.json", b'{"resolutions": [1]}')
    with pytest.raises(DryRunError) as dry_error:
        dry_run(release, triggered_by=StaffUserFactory(), resolutions=path)
    with pytest.raises(CommitError) as raised:
        commit(release, resolutions=path)
    assert raised.value.precondition == 5
    assert str(raised.value) == str(dry_error.value)


def test_files_are_checked_as_the_dry_run_checks_them(registry, tmp_path, genes):
    release = registered(tmp_path, approved=False)
    data_file(release).delete()
    approved_for_internal_use(release)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 7
    assert str(raised.value) == (
        f"Release {release.pk} has 0 data files, and a dry run reads one "
        "(graph_register_release stores it). Nothing was written."
    )


@pytest.mark.postgres
def test_the_run_lock_refusals_are_worded_for_a_commit(registry, tmp_path, genes):
    release = registered(tmp_path)
    dry(release)
    with release_run_lock(release.pk):
        with pytest.raises(CommitError) as raised:
            commit(release)
    assert raised.value.precondition == 9
    assert str(raised.value) == (
        f"This connection already holds the run lock of release {release.pk}: a "
        "commit never runs inside another run of it. Nothing was written."
    )
    assert not commit_runs(release).exists()
    # A synthetic draft with a data file, at the first id past what a run
    # lock keys: precondition 9 is the first that refuses it.
    beyond = DatasetReleaseFactory(
        pk=2**31,
        is_synthetic=True,
        adapter=SPEC.name,
        adapter_version=SPEC.version,
        mapping_version=SPEC.mapping_version,
        kind=SPEC.kind,
    )
    SourceFileFactory(release=beyond)
    with pytest.raises(CommitError) as raised:
        commit(beyond)
    assert raised.value.precondition == 9
    assert str(raised.value) == (
        "Release 2147483648's id is beyond 2,147,483,647, the largest a run lock "
        "keys. Nothing was written."
    )
    assert not commit_runs(beyond).exists()


@pytest.mark.postgres
@pytest.mark.parametrize("dead", [1, 2])
def test_a_refusal_once_dead_runs_are_marked_says_so(registry, tmp_path, genes, dead):
    # Dry runs whose processes ended: running, with no lock held.
    release = registered(tmp_path)
    left = [
        ImportRunFactory(release=release, file=data_file(release)) for _ in range(dead)
    ]
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 12
    ending = (
        f"Run {left[0].pk}, left running by a process that ended, is marked failed; "
        "nothing else was written."
        if dead == 1
        else f"Runs {left[0].pk} and {left[1].pk}, left running by processes that "
        "ended, are marked failed; nothing else was written."
    )
    assert str(raised.value) == (
        f"Release {release.pk}'s latest dry run, {left[-1].pk}, is failed, not "
        "completed: run graph_import --dry-run again, with the options you will "
        f"commit with. {ending}"
    )
    for run in ImportRun.objects.filter(pk__in=[run.pk for run in left]):
        assert run.state == FAILED and run.finished_at is not None
        assert run.error == (
            "Its process ended before the run finished; found when a commit of "
            f"release {release.pk} started."
        )
    assert not commit_runs(release).exists()


# ── The open commit run decides first (10) ──────────────────────────────────


def failed_commit(monkeypatch, release, record_key, **options) -> ImportRun:
    """A commit of the release stopped in the batch of this record, before
    its evidence: the run, marked failed."""
    real = stopping(monkeypatch, release, record_key)
    with pytest.raises(CommitStopped) as raised:
        commit(release, **options)
    monkeypatch.setattr(commit_module, "record_claims", real)
    assert raised.value.marked
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert run.state == FAILED
    return run


def two_batches(tmp_path, monkeypatch, **options):
    """A release of two records, one per batch: r1 creates HGNC:5 with
    --create-entities, r2 names stored genes."""
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    return registered(
        tmp_path,
        [row(GENE, object_id="HGNC:5"), row(GENE, record_id="r2")],
        **options,
    )


def test_an_open_commit_run_with_other_options_is_named_then_resumed(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry_run_id = dry(release, create_entities=True).pk
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 10
    assert str(raised.value) == (
        f"Release {release.pk} has an open commit run {run.pk}, started from dry run "
        f"{dry_run_id} with --create-entities and no resolution file: run the same "
        "command with those options to resume it, or discard it with "
        "graph_discard_release, then dry-run it again. Nothing was written."
    )
    assert ImportRun.objects.get(pk=run.pk).state == FAILED
    result = commit(release, create_entities=True)
    assert (result.outcome, result.run.pk, result.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )


def test_an_open_commit_run_started_with_a_resolution_file_is_named_then_resumed(
    registry, tmp_path, monkeypatch, genes
):
    # 10.3's other half: no resolution file, or another, is other options.
    tp53, mdm2 = genes
    reviewed = resolution_file(tmp_path, "reviewed.json", mdm2, "HGNC:7")
    other = resolution_file(tmp_path, "other.json", mdm2, "HGNC:8")
    release = two_batches(tmp_path, monkeypatch)
    dry_run_id = dry(release, create_entities=True, resolutions=reviewed).pk
    run = failed_commit(
        monkeypatch, release, "r2", create_entities=True, resolutions=reviewed
    )
    sha = run.parameters["resolutions"]["sha256"]
    assert sha == dry_run_module._resolutions(reviewed).sha256 != ""
    staff = StaffUserFactory()
    contents = table_contents()
    for given in (None, other):
        with pytest.raises(CommitError) as raised:
            commit(release, by=staff, create_entities=True, resolutions=given)
        assert raised.value.precondition == 10
        assert str(raised.value) == (
            f"Release {release.pk} has an open commit run {run.pk}, started from dry "
            f"run {dry_run_id} with --create-entities and the resolution file of "
            f"SHA-256 {sha}: run the same command with those options to resume it, "
            "or discard it with graph_discard_release, then dry-run it again. "
            "Nothing was written."
        )
        assert table_contents() == contents
    result = commit(release, by=staff, create_entities=True, resolutions=reviewed)
    assert (result.outcome, result.run.pk, result.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )


def _upgraded(monkeypatch, release):
    monkeypatch.setitem(
        adapters.ADAPTERS,
        SPEC.name,
        SimpleNamespace(spec=replace(SPEC, version="1.1.0", mapping_version="2")),
    )
    return (
        "this code carries canonical_tsv_v1 1.1.0 (mapping 2), and the run reads with "
        f"{SPEC.version} (mapping {SPEC.mapping_version})",
        "register it again with graph_register_release, dry-run it and commit it",
    )


def _registry_upgraded(monkeypatch, release):
    # The run started under registry 1.0; the code and the database are at
    # the current version since.
    run = commit_runs(release).get()
    run.parameters = {**run.parameters, "registry_version": "1.0"}
    run.save(update_fields=["parameters"])
    return (
        f"the database's registry is at version {REGISTRY_VERSION}, and the run's "
        "at 1.0",
        "dry-run it and commit it",
    )


def _kind_changed(monkeypatch, release):
    monkeypatch.setitem(
        adapters.ADAPTERS,
        SPEC.name,
        SimpleNamespace(spec=replace(SPEC, kind=ReleaseKind.REFERENCE.value)),
    )
    return (
        "this code's kind is reference, and the release's evidence",
        "register its files as a new version, since a release keeps its kind once "
        "it has created entities",
    )


@pytest.mark.parametrize(
    "change", [_upgraded, _registry_upgraded, _kind_changed], ids=lambda f: f.__name__
)
def test_an_open_commit_run_this_code_cannot_resume_names_the_discard(
    registry, tmp_path, monkeypatch, genes, change
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    # Batch 1 created HGNC:5: an entity names the release as its creator.
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    assert Entity.objects.filter(created_by_release=release).count() == 1
    differs, then = change(monkeypatch, release)
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff, create_entities=True)
    assert raised.value.precondition == 10
    assert str(raised.value) == (
        f"Release {release.pk} has an open commit run {run.pk} that cannot be "
        f"resumed under this code ({differs}): discard it with graph_discard_release "
        "(the evidence its batches recorded is deleted; entities and relationships "
        f"stay), then {then}. Nothing was written."
    )
    assert table_contents() == contents


def test_a_run_this_code_cannot_resume_is_discarded_and_the_release_imported_again(
    registry, tmp_path, monkeypatch, genes
):
    # H7 from a failed run: 10.2 names the discard; the discard, a new
    # registration under the code's adapter version, a dry run and a commit
    # then import the release.
    release = two_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    monkeypatch.setitem(
        adapters.ADAPTERS,
        SPEC.name,
        SimpleNamespace(
            spec=replace(SPEC, version="1.1.0", mapping_version="2"),
            iter_records=ADAPTER.iter_records,
            map_record=ADAPTER.map_record,
            synthetic_issues=ADAPTER.synthetic_issues,
        ),
    )
    with pytest.raises(CommitError) as raised:
        commit(release, create_entities=True)
    assert raised.value.precondition == 10
    assert str(raised.value).endswith(
        "discard it with graph_discard_release (the evidence its batches recorded is "
        "deleted; entities and relationships stay), then register it again with "
        "graph_register_release, dry-run it and commit it. Nothing was written."
    )
    staff = StaffUserFactory()
    # Batch 1's evidence goes; the entity it created stays.
    discarded = discard_release(release, reason="A new adapter.", discarded_by=staff)
    assert (discarded.run.pk, discarded.assertions) == (run.pk, 1)
    assert Entity.objects.filter(created_by_release=release).count() == 1
    folder = tmp_path / f"upload-{release.dataset.slug}-v1"
    registration = register_release(
        dataset=release.dataset.slug,
        version="v1",
        adapter=SPEC.name,
        file=folder / "release.tsv",
        manifest=folder / "manifest.json",
        registered_by=staff,
    )
    assert registration.outcome == "updated"
    assert dry(release, create_entities=True).adapter_version == "1.1.0"
    result = commit(release, by=staff, create_entities=True)
    assert (result.outcome, result.run.state, result.run.adapter_version) == (
        COMMITTED,
        COMPLETED,
        "1.1.0",
    )
    assert [r.state for r in commit_runs(release).order_by("pk")] == [
        ABANDONED,
        COMPLETED,
    ]


def test_a_kind_change_before_any_entity_names_the_registration(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    run = failed_commit(monkeypatch, release, "r2")
    assert not Entity.objects.filter(created_by_release=release).exists()
    _kind_changed(monkeypatch, release)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert f"open commit run {run.pk} that cannot be resumed" in str(raised.value)
    assert str(raised.value).endswith(
        "then register it again with graph_register_release, dry-run it and commit "
        "it. Nothing was written."
    )


def test_the_dry_run_names_the_open_commit_run_and_what_to_do(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    with pytest.raises(DryRunError) as raised:
        dry_run(release, triggered_by=StaffUserFactory(), create_entities=True)
    assert raised.value.precondition == 4
    assert str(raised.value) == (
        f"Release {release.pk} has an open commit run {run.pk}: a dry run never runs "
        "beside one. Resume it with graph_import --commit, or discard it with "
        "graph_discard_release. Nothing was written."
    )


def test_without_the_run_lock_a_running_commit_run_is_never_resumed(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    # As a killed process leaves it, on a database without the run lock
    # (patched where the test database has it).
    run.state = RUNNING
    run.save(update_fields=["state"])
    if runs_are_locked():
        monkeypatch.setattr(commit_module, "runs_are_locked", lambda using=None: False)
        monkeypatch.setattr(
            commit_module, "end_dead_runs", lambda release_id, **kwargs: []
        )
    with pytest.raises(CommitError) as raised:
        commit(release, create_entities=True)
    assert raised.value.precondition == 10
    assert str(raised.value) == (
        f"Release {release.pk} has a commit run {run.pk} in state running, and this "
        "database cannot tell whether its process is alive (the run lock needs "
        "PostgreSQL). If no process runs it, discard it with graph_discard_release. "
        "Nothing was written."
    )
    assert ImportRun.objects.get(pk=run.pk).state == RUNNING


# ── The adapter (11) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("entities", [False, True], ids=["no entity", "entities"])
def test_another_adapter_version_or_kind_in_the_code_is_refused(
    registry, tmp_path, monkeypatch, genes, entities
):
    release = registered(tmp_path)
    dry(release)
    if entities:
        create_entity("gene", "Gene five", 9606, "hgnc", "HGNC:5", release=release)
        kind = ReleaseKind.REFERENCE.value
        spec = replace(SPEC, kind=kind)
    else:
        kind = SPEC.kind
        spec = replace(SPEC, version="1.1.0", mapping_version="2")
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, SimpleNamespace(spec=spec))
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 11
    advice = (
        "register its files as a new version, since a release keeps its kind once it "
        "has created entities"
        if entities
        else "register it again with graph_register_release, then dry-run it and "
        "commit it"
    )
    assert str(raised.value) == (
        f"Release {release.pk} was registered with canonical_tsv_v1 {SPEC.version} "
        f"(mapping {SPEC.mapping_version}, kind evidence), and this code carries "
        f"{spec.version} (mapping {spec.mapping_version}, kind {kind}): {advice}. "
        "Nothing was written."
    )
    assert not commit_runs(release).exists()


@pytest.mark.parametrize("open_run", [False, True], ids=["no open run", "open run"])
def test_an_adapter_the_code_does_not_carry_is_refused(
    registry, tmp_path, monkeypatch, genes, open_run
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    ending = "Nothing was written."
    if open_run:
        run = failed_commit(monkeypatch, release, "r2")
        ending = open_run_ending(run.pk)
    unsound = replace(SPEC, isoform_specific=False)
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, SimpleNamespace(spec=unsound))
    with pytest.raises(adapters.AdapterSpecError) as problem:
        adapters.get_adapter(SPEC.name)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 11
    assert str(raised.value) == (
        f"Release {release.pk}'s adapter: {str(problem.value).rstrip('.')}. {ending}"
    )


# ── The trusted dry run (12) ────────────────────────────────────────────────


def resolution_file(tmp_path, name, entity, value) -> str:
    """A reviewed resolution file mapping hgnc `value` to the entity."""
    return str(
        write(
            tmp_path,
            name,
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
                            "target_value": "HGNC:6973",
                            "rationale": "Reviewed.",
                            "reviewer": "A. Reviewer",
                        }
                    ]
                }
            ).encode(),
        )
    )


def test_a_release_never_dry_run_is_refused(registry, tmp_path, genes):
    release = registered(tmp_path)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 12
    assert str(raised.value) == (
        f"Release {release.pk} has no dry run: run graph_import --dry-run first, "
        "with the options you will commit with. Nothing was written."
    )


def _failed(release, tmp_path, monkeypatch, genes):
    def broken(plans, *, run):
        raise RuntimeError("The disk is gone.")

    with monkeypatch.context() as patched:
        patched.setattr(dry_run_module, "write_records", broken)
        with pytest.raises(RuntimeError):
            dry_run(release, triggered_by=StaffUserFactory())
    return {}, "is failed, not completed"


def _refused(release, tmp_path, monkeypatch, genes):
    with monkeypatch.context() as patched:
        patched.setattr(limits, "MAX_IDENTIFIERS", 1)
        assert dry(release).report["verdict"] == "refused"
    return {}, "was refused"


def _registered_again(release, tmp_path, monkeypatch, spec):
    dry(release)
    monkeypatch.setitem(adapters.ADAPTERS, SPEC.name, SimpleNamespace(spec=spec))
    folder = tmp_path / f"upload-{release.dataset.slug}-v1"
    registration = register_release(
        dataset=release.dataset.slug,
        version="v1",
        adapter=SPEC.name,
        file=folder / "release.tsv",
        manifest=folder / "manifest.json",
        registered_by=StaffUserFactory(),
    )
    assert registration.outcome == "updated"


def _other_versions(release, tmp_path, monkeypatch, genes):
    spec = replace(SPEC, version="1.1.0", mapping_version="2")
    _registered_again(release, tmp_path, monkeypatch, spec)
    return {}, (
        f"read it with canonical_tsv_v1 {SPEC.version} (mapping "
        f"{SPEC.mapping_version}, kind evidence), and the release reads with "
        "canonical_tsv_v1 1.1.0 (mapping 2, kind evidence)"
    )


def _other_kind(release, tmp_path, monkeypatch, genes):
    spec = replace(SPEC, kind=ReleaseKind.REFERENCE.value)
    _registered_again(release, tmp_path, monkeypatch, spec)
    return {}, (
        f"read it with canonical_tsv_v1 {SPEC.version} (mapping "
        f"{SPEC.mapping_version}, kind evidence), and the release reads with "
        f"canonical_tsv_v1 {SPEC.version} (mapping {SPEC.mapping_version}, kind "
        "reference)"
    )


def _other_registry(release, tmp_path, monkeypatch, genes):
    run = dry(release)
    run.parameters = {**run.parameters, "registry_version": "1.0"}
    run.save(update_fields=["parameters"])
    return {}, (
        f"ran under registry version 1.0, and the database's is at {REGISTRY_VERSION}"
    )


def _closed_commit_after(state):
    def change(release, tmp_path, monkeypatch, genes):
        dry(release)
        later = ImportRunFactory(
            release=release,
            file=data_file(release),
            mode=ImportMode.COMMIT,
            state=state,
        )
        return {}, f"came before commit run {later.pk}, which is {state}"

    change.__name__ = f"_older_than_a_{state}_commit_run"
    return change


def _without_create_entities(release, tmp_path, monkeypatch, genes):
    dry(release)
    return {"create_entities": True}, (
        "ran without --create-entities, and this commit runs with it"
    )


def _with_create_entities(release, tmp_path, monkeypatch, genes):
    dry(release, create_entities=True)
    return {}, "ran with --create-entities, and this commit runs without it"


def _another_resolution_file(release, tmp_path, monkeypatch, genes):
    tp53, mdm2 = genes
    first = resolution_file(tmp_path, "first.json", mdm2, "HGNC:7")
    second = resolution_file(tmp_path, "second.json", mdm2, "HGNC:8")
    one = dry(release, resolutions=first).parameters["resolutions"]["sha256"]
    other = dry_run_module._resolutions(second).sha256
    return {"resolutions": second}, (
        f"ran with the resolution file of SHA-256 {one}, and this commit has the "
        f"resolution file of SHA-256 {other}"
    )


def _no_resolution_file(release, tmp_path, monkeypatch, genes):
    tp53, mdm2 = genes
    first = resolution_file(tmp_path, "first.json", mdm2, "HGNC:7")
    one = dry(release, resolutions=first).parameters["resolutions"]["sha256"]
    return {}, (
        f"ran with the resolution file of SHA-256 {one}, and this commit has no "
        "resolution file"
    )


def _a_resolution_file_the_dry_run_lacked(release, tmp_path, monkeypatch, genes):
    # The reviewed dry run had no curated mapping: a file now would commit
    # one nobody reviewed.
    tp53, mdm2 = genes
    dry(release)
    first = resolution_file(tmp_path, "first.json", mdm2, "HGNC:7")
    one = dry_run_module._resolutions(first).sha256
    return {"resolutions": first}, (
        "ran with no resolution file, and this commit has the resolution file of "
        f"SHA-256 {one}"
    )


@pytest.mark.parametrize(
    "change",
    [
        _failed,
        _refused,
        _other_versions,
        _other_kind,
        _other_registry,
        _closed_commit_after(COMPLETED),
        _closed_commit_after(ABANDONED),
        _without_create_entities,
        _with_create_entities,
        _another_resolution_file,
        _no_resolution_file,
        _a_resolution_file_the_dry_run_lacked,
    ],
    ids=lambda f: f.__name__.strip("_"),
)
def test_the_latest_dry_run_is_trusted_only_as_it_ran(
    registry, tmp_path, monkeypatch, genes, change
):
    release = registered(tmp_path)
    options, what = change(release, tmp_path, monkeypatch, genes)
    latest = ImportRun.objects.filter(release=release, mode=ImportMode.DRY_RUN)
    latest = latest.order_by("-pk").first()
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff, **options)
    assert raised.value.precondition == 12
    assert str(raised.value) == (
        f"Release {release.pk}'s latest dry run, {latest.pk}, {what}: {DRY_RUN_AGAIN}"
    )
    assert table_contents() == contents


def test_a_dry_run_older_than_a_discard_is_not_trusted(registry, tmp_path, genes):
    # 12.6 with a real discard: the staged release's completed commit run is
    # abandoned after the dry run, and its records are deleted.
    release = registered(tmp_path)
    dry_run_row = dry(release)
    first = commit(release)
    discard_release(release, reason="Again.", discarded_by=StaffUserFactory())
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff)
    assert raised.value.precondition == 12
    assert str(raised.value) == (
        f"Release {release.pk}'s latest dry run, {dry_run_row.pk}, came before commit "
        f"run {first.run.pk}, which is abandoned: {DRY_RUN_AGAIN}"
    )
    assert table_contents() == contents
    dry(release)
    assert commit(release, by=staff).outcome == COMMITTED


def test_a_latest_dry_run_left_running_is_not_trusted(registry, tmp_path, genes):
    # Its process ended: running, with no lock held. On PostgreSQL the commit
    # marks it failed first; elsewhere it stays running, and untrusted.
    release = registered(tmp_path)
    dry(release)
    left = ImportRunFactory(release=release, file=data_file(release))
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 12
    if runs_are_locked():
        assert str(raised.value) == (
            f"Release {release.pk}'s latest dry run, {left.pk}, is failed, not "
            "completed: run graph_import --dry-run again, with the options you will "
            f"commit with. Run {left.pk}, left running by a process that ended, is "
            "marked failed; nothing else was written."
        )
        assert ImportRun.objects.get(pk=left.pk).state == FAILED
    else:
        assert str(raised.value) == (
            f"Release {release.pk}'s latest dry run, {left.pk}, is running, not "
            f"completed: {DRY_RUN_AGAIN}"
        )
        assert ImportRun.objects.get(pk=left.pk).state == RUNNING


# ── The planning: the file (13) ─────────────────────────────────────────────


def test_a_synthetic_violation_found_by_the_commit_refuses_the_file(
    registry, tmp_path, monkeypatch
):
    # H3: the dry run passed; the commit's planning checks every line again,
    # unstored ones included (the check patched to fire on one).
    release = registered(
        tmp_path, [SYNTHETIC, row(SYNTHETIC, record_id="-unstored")], synthetic=True
    )
    assert dry(release, create_entities=True).report["verdict"] == "ok"
    real = ADAPTER.synthetic_issues

    def violating(record, ctx):
        if record.line_number == 3:
            return [
                Issue(
                    "synthetic_violation",
                    "The subject names a real gene.",
                    field="subject_id",
                    line=3,
                )
            ]
        return real(record, ctx)

    monkeypatch.setattr(ADAPTER, "synthetic_issues", violating)
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff, create_entities=True)
    assert raised.value.precondition == 13
    assert str(raised.value) == (
        f"The file of release {release.pk} is refused: synthetic_violation at line 3: "
        "The subject names a real gene (1 issue). Nothing was written."
    )
    assert table_contents() == contents


def test_a_file_past_the_identifiers_a_run_keeps_is_refused(
    registry, tmp_path, monkeypatch, genes
):
    rows = [row(GENE, record_id=f"r{n}", object_id=f"HGNC:{n + 5}") for n in range(3)]
    release = registered(tmp_path, rows)
    dry(release, create_entities=True)
    monkeypatch.setattr(limits, "MAX_IDENTIFIERS", 3)
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff, create_entities=True)
    assert raised.value.precondition == 13
    assert str(raised.value) == (
        f"The file of release {release.pk} is refused: too_many_identifiers at line "
        "2: The file states more than 3 distinct identifiers, which a run does not "
        "keep: split it into releases (1 issue). Nothing was written."
    )
    assert table_contents() == contents


@pytest.mark.parametrize("open_run", [False, True], ids=["no open run", "open run"])
def test_a_stored_file_that_changed_refuses_the_file(
    registry, tmp_path, monkeypatch, genes, open_run
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    ending = "Nothing was written."
    if open_run:
        run = failed_commit(monkeypatch, release, "r2")
        ending = (
            f"Its open commit run {run.pk} cannot be resumed while this holds: "
            "discard it with graph_discard_release, then register it again with "
            "graph_register_release, which restores the stored copy, and dry-run it. "
            "Nothing was written."
        )
    stored = storage.root() / data_file(release).storage_path
    stored.write_bytes(stored.read_bytes().replace(b"HGNC:6973", b"HGNC:6974"))
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 13
    message = str(raised.value)
    assert message.startswith(
        f"The file of release {release.pk} is refused: stored_file_mismatch: "
    )
    assert message.endswith(f" (1 issue). {ending}")


# ── The records (14) ────────────────────────────────────────────────────────


def _deleted(release, dry_run_row):
    SourceRecord.objects.filter(release=release, record_key="r2").delete()
    return "record 'r2' of line 3 is not stored"


def _extra(release, dry_run_row):
    SourceRecordFactory(
        release=release,
        file=data_file(release),
        import_run=dry_run_row,
        record_key="extra",
        line_number=9,
    )
    return "it holds 3 records, and the file gives 2"


def _replaced(release, dry_run_row):
    stored = SourceRecord.objects.get(release=release, record_key="r2")
    SourceRecord.objects.filter(pk=stored.pk).delete()
    other = SourceRecordFactory(
        release=release,
        file=data_file(release),
        import_run=dry_run_row,
        record_key="r2",
        line_number=9,
    )
    return (
        f"record 'r2' is stored from line 9 with SHA-256 {other.raw_sha256}, and this "
        f"file has it on line 3 with {stored.raw_sha256}: the release's records do "
        "not come from its data file"
    )


@pytest.mark.parametrize(
    "change", [_deleted, _extra, _replaced], ids=lambda f: f.__name__
)
def test_records_that_are_not_those_of_the_dry_run_are_refused(
    registry, tmp_path, genes, change
):
    release = registered(tmp_path, [GENE, row(GENE, record_id="r2")])
    dry_run_row = dry(release)
    what = change(release, dry_run_row)
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff)
    assert raised.value.precondition == 14
    assert str(raised.value) == (
        f"Release {release.pk}'s records are not those of its latest dry run "
        f"{dry_run_row.pk} ({what}): dry-run it again. Nothing was written."
    )
    assert table_contents() == contents


# ── The statuses (15), and the resume limit ─────────────────────────────────


def variant_rows(test_namespaces, first, second):
    """Two records of a case-exact (or version-split) identity namespace's
    test taxon, stating `first`, then `second`, as their subjects."""
    if first.startswith("ENSX"):
        namespace, taxon, other = "test_versioned", test_namespaces.taxon_d, "ENSX0002"
    else:
        namespace, taxon, other = "test_exact_a", test_namespaces.taxon_a, "obj1"
    cells = {
        "subject_namespace": namespace,
        "subject_type": "gene",
        "subject_taxon": str(taxon),
        "object_namespace": namespace,
        "object_id": other,
        "object_type": "gene",
        "object_taxon": str(taxon),
    }
    return (
        [
            row(GENE, record_id="r1", subject_id=first, **cells),
            row(GENE, record_id="r2", subject_id=second, **cells),
        ],
        namespace,
        taxon,
    )


@pytest.mark.parametrize(
    "first,second", [("h", "H"), ("ENSX0001.1", "ENSX0001.2")], ids=["case", "version"]
)
def test_an_entity_another_release_created_since_the_dry_run_refuses_the_commit(
    registry, tmp_path, test_namespaces, first, second
):
    rows, namespace, taxon = variant_rows(test_namespaces, first, second)
    release = registered(tmp_path, rows[:1])
    dry_run_row = dry(release, create_entities=True)
    assert statuses(release) == {"r1": MAPPED}
    # Another release creates the other case (or version) of r1's subject:
    # r1's identifier is now a candidate.
    other = registered(tmp_path / "other", rows[1:], version="v2")
    create_entity("gene", "Variant", taxon, namespace, second, release=other)
    staff = StaffUserFactory()
    contents = table_contents()
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff, create_entities=True)
    assert raised.value.precondition == 15
    message = str(raised.value)
    assert message.startswith(
        f"1 record of release {release.pk} maps differently from its latest dry run "
        f"{dry_run_row.pk}, for example record 'r1' of line 2: mapped then, "
        "unresolved now (The subject: "
    )
    assert "is candidate" in message
    assert message.endswith(
        "). Dry-run it again and review its report. Nothing was written."
    )
    assert table_contents() == contents


@pytest.mark.parametrize(
    "first,second", [("h", "H"), ("ENSX0001.1", "ENSX0001.2")], ids=["case", "version"]
)
def test_a_resume_that_its_own_entities_change_is_refused(
    registry, tmp_path, monkeypatch, test_namespaces, first, second
):
    # The resume limit: a resume plans with the entities its run created, so
    # another case (or version) of one is a candidate where the dry run and
    # an uninterrupted commit create it. No namespace of registry v1 has
    # either rule.
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    rows, _, _ = variant_rows(test_namespaces, first, second)
    release = registered(tmp_path, rows)
    dry_run_row = dry(release, create_entities=True)
    assert statuses(release) == {"r1": MAPPED, "r2": MAPPED}
    # Stopped after the batch that creates r1's subject.
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    assert Entity.objects.filter(created_by_release=release).exists()
    with pytest.raises(CommitError) as raised:
        commit(release, create_entities=True)
    assert raised.value.precondition == 15
    message = str(raised.value)
    assert message.startswith(
        f"1 record of release {release.pk} maps differently from its latest dry run "
        f"{dry_run_row.pk}, for example record 'r2' of line 3: mapped then, "
        "unresolved now (The subject: "
    )
    assert message.endswith(f"). {open_run_ending(run.pk)}")
    assert ImportRun.objects.get(pk=run.pk).state == FAILED


# ── What a commit writes ────────────────────────────────────────────────────


def record_rows(release) -> dict:
    """Each stored record's status, issues and updated_at: what no commit
    changes."""
    return {
        record["record_key"]: record
        for record in SourceRecord.objects.filter(release=release).values(
            "record_key", "status", "issues", "updated_at"
        )
    }


def primary_value(entity) -> str:
    return EntityIdentifier.objects.get(
        entity=entity, role=IdentifierRole.PRIMARY
    ).value


def test_a_commit_writes_what_its_dry_run_reported(registry, tmp_path, genes):
    tp53, mdm2 = genes
    add_identifier(mdm2, "hgnc", "HGNC:7", IdentifierRole.SECONDARY)
    for gene in genes:
        add_identifier(gene, "hgnc", "HGNC:8", IdentifierRole.SECONDARY)
    rows = [
        row(GENE, record_id="r1", publications="pmid:12345|ref:paper-a"),
        row(
            GENE,
            record_id="r2",
            object_id="HGNC:5",
            object_label="Gene five",
            publications="doi:10.1000/AbC",
        ),
        row(
            PROTEIN,
            record_id="r3",
            interaction_type="MI:0915",
            method="MI:0018",
            publications="doi:10.1000/abc",
        ),
        row(GENE, record_id="candidate", object_id="HGNC:7"),
        row(GENE, record_id="ambiguous", object_id="HGNC:8"),
        row(GENE, record_id="rejected", effect="negative"),
        row(GENE, record_id="-unstored"),
    ]
    # The ref: paper's title and year, which the commit writes as declared.
    paper_a = {"title": "A paper", "year": 2026, "is_synthetic": False}
    release = registered(
        tmp_path, rows, manifest=manifest_bytes(publications={"paper-a": paper_a})
    )
    dry_run_row = dry(release, create_entities=True)
    before = record_rows(release)
    result = commit(release, create_entities=True)
    run = result.run
    assert (result.outcome, run.state) == (COMMITTED, COMPLETED)
    assert statuses(release) == {
        "r1": MAPPED,
        "r2": MAPPED,
        "r3": MAPPED,
        "candidate": UNRESOLVED,
        "ambiguous": AMBIGUOUS,
        "rejected": REJECTED,
    }
    # No source record changed: the dry run wrote them.
    assert record_rows(release) == before
    # The evidence of each mapped record (r1 reports two papers: one
    # assertion each), and none of the others.
    assertions = list(EvidenceAssertion.objects.filter(release=release))
    assert sorted(a.source_record.record_key for a in assertions) == [
        "r1",
        "r1",
        "r2",
        "r3",
    ]
    assert {assertion.import_run_id for assertion in assertions} == {run.pk}
    # Entities: provisional, named by the release that created them, with
    # the labels the dry run's report showed.
    created = {
        entity.identity_key: entity
        for entity in Entity.objects.filter(created_by_release=release)
    }
    shown = {
        entry["identity_key"]: entry["label"]
        for entry in dry_run_row.report["would_create"]["entries"]
    }
    assert {key: entity.label for key, entity in created.items()} == shown
    assert set(shown) == {
        "hgnc|t:9606|HGNC:5",
        "uniprotkb||P04637",
        "uniprotkb||Q00987",
    }
    assert {entity.status for entity in created.values()} == {EntityStatus.PROVISIONAL}
    assert created["hgnc|t:9606|HGNC:5"].label == "Gene five"
    # Relationships, the papers they cite and the terms they name.
    relationships = {
        (r.relationship_type_id, r.subject_id, r.object_id)
        for r in Relationship.objects.all()
    }
    hgnc5 = created["hgnc|t:9606|HGNC:5"]
    p53, mdm = created["uniprotkb||P04637"], created["uniprotkb||Q00987"]
    assert relationships == {
        ("transcriptional_regulation", tp53.pk, mdm2.pk),
        ("transcriptional_regulation", tp53.pk, hgnc5.pk),
        ("physical_interaction", min(p53.pk, mdm.pk), max(p53.pk, mdm.pk)),
    }
    pmid = Publication.objects.get(pmid="12345")
    doi = Publication.objects.get(doi_lookup="10.1000/abc")
    # As the first mapped record citing it states it.
    assert doi.doi == "10.1000/AbC"
    local = Publication.objects.get(local_key="paper-a")
    assert (local.local_dataset_id, local.title, local.year, local.is_synthetic) == (
        release.dataset_id,
        "A paper",
        2026,
        False,
    )
    cited = {
        (assertion.source_record.record_key, cite.publication_id)
        for assertion in assertions
        for cite in EvidencePublication.objects.filter(assertion=assertion)
    }
    assert cited == {
        ("r1", pmid.pk),
        ("r1", local.pk),
        ("r2", doi.pk),
        ("r3", doi.pk),
    }
    terms = dict(
        Term.objects.filter(label_release=release).values_list("curie", "label")
    )
    assert terms == {"MI:0915": "physical association", "MI:0018": "two hybrid"}
    [r3] = [a for a in assertions if a.source_record.record_key == "r3"]
    assert r3.interaction_type_id == "MI:0915"
    assert EvidenceMethod.objects.get(assertion=r3).term_id == "MI:0018"
    assert run.report["counts"] == {
        "records": {
            "mapped": 3,
            "unresolved": 1,
            "ambiguous": 1,
            "rejected": 1,
            "skipped": 0,
        },
        "batches": 1,
        "recorded": 3,
        "already_recorded": 0,
        "assertions": 4,
        "created": {"entities": 3, "relationships": 3, "publications": 3, "terms": 2},
        "late_relationships": 0,
    }


def test_created_entities_take_the_label_and_value_the_dry_run_showed(
    registry, tmp_path, monkeypatch, genes
):
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    rows = [
        # G3 refuses these (a directed type stated undirected): their labels
        # and their writing choose nothing.
        row(
            GENE,
            record_id="leak",
            object_id="hgnc:5",
            object_label="Leaked",
            directed="false",
        ),
        row(GENE, record_id="r1", object_id="HGNC:5"),
        # In the second batch: the label HGNC:5 takes when batch 1 creates it.
        row(GENE, record_id="r2", object_id="HGNC:5", object_label="Gene five"),
        # HGNC:7's only label is on a record G3 refuses.
        row(
            GENE,
            record_id="leak2",
            object_id="HGNC:7",
            object_label="Leaked",
            directed="false",
        ),
        row(GENE, record_id="r3", object_id="HGNC:7"),
    ]
    release = registered(tmp_path, rows)
    dry_run_row = dry(release, create_entities=True)
    assert statuses(release)["leak"] == statuses(release)["leak2"] == REJECTED
    shown = {
        entry["identity_key"]: (entry["label"], entry["value"])
        for entry in dry_run_row.report["would_create"]["entries"]
    }
    assert shown == {
        "hgnc|t:9606|HGNC:5": ("Gene five", "HGNC:5"),
        "hgnc|t:9606|HGNC:7": ("HGNC:7", "HGNC:7"),
    }
    commit(release, create_entities=True)
    assert {
        entity.identity_key: (entity.label, primary_value(entity))
        for entity in Entity.objects.filter(created_by_release=release)
    } == shown


def test_a_term_stored_since_the_planning_keeps_its_label_and_is_listed(
    registry, tmp_path, monkeypatch
):
    release = registered(tmp_path, [row(PROTEIN, method="MI:0018")])
    dry(release, create_entities=True)
    other = registered(tmp_path / "other", [GENE], version="v9")

    def stored_by_another_release():
        TermFactory(curie="MI:0018", label="two-hybrid", label_release=other)

    before_writing(monkeypatch, 1, stored_by_another_release)
    result = commit(release, create_entities=True)
    assert Term.objects.get(pk="MI:0018").label == "two-hybrid"
    report = result.run.report
    assert report["term_labels"] == {
        "total": 1,
        "truncated": False,
        "entries": [
            {
                "curie": "MI:0018",
                "stated": "two hybrid",
                "kept": "two-hybrid",
                "line": 2,
            }
        ],
    }
    assert report["counts"]["created"]["terms"] == 0
    assert (
        "1 term keeps a label other than the manifest's: graph_check lists it."
        in commit_summary_lines(result)
    )


def test_a_term_another_release_stored_after_the_dry_run_is_listed(registry, tmp_path):
    # Between this release's dry run and its commit's planning: the dry run
    # showed the term as new, with this manifest's label, and the commit
    # names the label it does not write.
    rows = [row(PROTEIN, method="MI:0018")]

    def manifest(label):
        return manifest_bytes(terms={**TERMS, "MI:0018": label})

    first = registered(tmp_path / "first", rows, manifest=manifest("two hybrid"))
    second = registered(tmp_path / "second", rows, manifest=manifest("Y2H"))
    dry(first, create_entities=True)
    shown = {
        entry["curie"]: entry
        for entry in dry(second, create_entities=True).report["terms"]["entries"]
    }
    assert (shown["MI:0018"]["status"], shown["MI:0018"]["conflict"]) == ("new", None)
    commit(first, create_entities=True)
    result = commit(second, create_entities=True)
    assert Term.objects.get(pk="MI:0018").label == "two hybrid"
    assert result.run.report["term_labels"] == {
        "total": 1,
        "truncated": False,
        "entries": [
            {"curie": "MI:0018", "stated": "Y2H", "kept": "two hybrid", "line": 2}
        ],
    }
    assert (
        "1 term keeps a label other than the manifest's: graph_check lists it."
        in commit_summary_lines(result)
    )
    [section] = [s for s in run_checks() if s.name == "term_labels"]
    assert section.lines == (
        f"Term MI:0018 keeps the label 'two hybrid', and release {second.pk}'s "
        f"manifest states 'Y2H': line 2, run {result.run.pk}.",
    )


def test_a_paper_another_release_stored_after_the_dry_run_is_listed(
    registry, tmp_path, genes
):
    # v1 and v2 of one dataset cite its paper-a with other titles and years;
    # v1 stores it between v2's dry run and v2's commit, and keeps its own.
    rows = [row(GENE, publications="ref:paper-a")]

    def manifest(title, year):
        paper_a = {"title": title, "year": year, "is_synthetic": False}
        return manifest_bytes(publications={"paper-a": paper_a})

    v1 = registered(tmp_path, rows, manifest=manifest("Title one", 2020))
    v2 = registered(
        tmp_path,
        rows,
        manifest=manifest("Title two", 2021),
        dataset=v1.dataset,
        version="v2",
    )
    dry(v1)
    [shown] = dry(v2).report["publications"]["entries"]
    assert (shown["status"], shown["title"], shown["year"]) == (
        "new",
        "Title two",
        2021,
    )
    commit(v1)
    result = commit(v2)
    paper = Publication.objects.get(local_dataset=v1.dataset, local_key="paper-a")
    assert (paper.title, paper.year) == ("Title one", 2020)
    assert result.run.report["paper_differences"] == {
        "total": 1,
        "truncated": False,
        "entries": [
            {
                "publication": paper.pk,
                "key": "paper-a",
                "stored_title": "Title one",
                "stored_year": 2020,
                "manifest_title": "Title two",
                "manifest_year": 2021,
                "line": 2,
            }
        ],
    }
    assert (
        "1 paper keeps a title or year other than the manifest's: graph_check lists "
        "it." in commit_summary_lines(result)
    )
    [section] = [s for s in run_checks() if s.name == "publication_conflicts"]
    assert section.lines == (
        f"Publication {paper.pk}, the paper 'paper-a' of release {v2.pk}'s dataset, "
        f"keeps the title 'Title one' and the year 2020, and release {v2.pk}'s "
        f"manifest states the title 'Title two' and the year 2021: line 2, run "
        f"{result.run.pk}.",
    )


def test_a_ref_paper_the_dataset_holds_without_a_year_is_given_the_manifests(
    registry, tmp_path, genes
):
    # G2 fills a stored paper's blank details only: v1 stores paper-a with
    # no year, and v2's commit gives it v2's year, which its dry run listed
    # as a difference; the commit then keeps none to list.
    rows = [row(GENE, publications="ref:paper-a")]

    def manifest(year):
        paper_a = {"title": "A paper", "is_synthetic": False}
        if year is not None:
            paper_a["year"] = year
        return manifest_bytes(publications={"paper-a": paper_a})

    v1 = registered(tmp_path, rows, manifest=manifest(None))
    committed(v1)
    paper = Publication.objects.get(local_dataset=v1.dataset, local_key="paper-a")
    assert (paper.title, paper.year) == ("A paper", None)
    v2 = registered(
        tmp_path, rows, manifest=manifest(2021), dataset=v1.dataset, version="v2"
    )
    [shown] = dry(v2).report["publications"]["manifest_differences"]["entries"]
    assert (shown["key"], shown["stored_year"], shown["manifest_year"]) == (
        "paper-a",
        None,
        2021,
    )
    result = commit(v2)
    paper.refresh_from_db()
    assert (paper.title, paper.year) == ("A paper", 2021)
    assert result.run.report["paper_differences"] == {
        "total": 0,
        "truncated": False,
        "entries": [],
    }


def test_a_synthetic_release_creates_its_ref_papers_synthetic(registry, tmp_path):
    paper_a = {"title": "A synthetic paper", "year": 2026, "is_synthetic": True}
    release = registered(
        tmp_path,
        [row(SYNTHETIC, publications="ref:paper-a")],
        synthetic=True,
        manifest=manifest_bytes(publications={"paper-a": paper_a}),
    )
    result = committed(release, create_entities=True)
    assert result.run.state == COMPLETED
    paper = Publication.objects.get(local_dataset=release.dataset, local_key="paper-a")
    assert (paper.title, paper.year, paper.is_synthetic) == (
        "A synthetic paper",
        2026,
        True,
    )
    assert (
        EvidencePublication.objects.filter(publication=paper, release=release).count()
        == 1
    )


def test_without_create_entities_nothing_is_created_and_records_stay_unresolved(
    registry, tmp_path, genes
):
    rows = [GENE, row(GENE, record_id="r2", object_id="HGNC:5")]
    release = registered(tmp_path, rows)
    result = committed(release)
    assert statuses(release) == {"r1": MAPPED, "r2": UNRESOLVED}
    assert not Entity.objects.filter(identity_key="hgnc|t:9606|HGNC:5").exists()
    assert [
        assertion.source_record.record_key
        for assertion in EvidenceAssertion.objects.filter(release=release)
    ] == ["r1"]
    assert result.run.report["counts"]["created"]["entities"] == 0
    # With it, the entity is created, provisional (A7).
    with_it = registered(tmp_path, rows, version="v2")
    committed(with_it, create_entities=True)
    created = Entity.objects.get(identity_key="hgnc|t:9606|HGNC:5")
    assert (created.status, created.created_by_release_id) == (
        EntityStatus.PROVISIONAL,
        with_it.pk,
    )


def test_a_second_release_keeps_its_own_evidence_and_reuses_shared_rows(
    registry, tmp_path, genes
):
    rows = [
        row(GENE, publications="pmid:12345"),
        row(GENE, record_id="r2", object_id="HGNC:5", publications="ref:paper-a"),
    ]
    first = registered(tmp_path, rows)
    committed(first, create_entities=True)
    shared = (
        Entity.objects.count(),
        Relationship.objects.count(),
        Publication.objects.count(),
    )
    second = registered(tmp_path, rows, dataset=first.dataset, version="v2")
    result = committed(second, create_entities=True)
    assert result.run.report["counts"]["created"] == {
        "entities": 0,
        "relationships": 0,
        "publications": 0,
        "terms": 0,
    }
    assert (
        Entity.objects.count(),
        Relationship.objects.count(),
        Publication.objects.count(),
    ) == shared

    def evidence(release):
        return sorted(
            (assertion.source_record.record_key, assertion.relationship_id)
            for assertion in EvidenceAssertion.objects.filter(release=release)
        )

    assert evidence(first) == evidence(second)
    assert len(evidence(second)) == 2
    assert not EvidenceAssertion.objects.filter(
        release=second, source_record__release=first
    ).exists()


# ── Committing again (A1) ───────────────────────────────────────────────────


def test_committing_again_with_the_same_options_changes_nothing(
    registry, tmp_path, genes
):
    release = registered(
        tmp_path, [GENE, row(GENE, record_id="r2", object_id="HGNC:5")]
    )
    first = committed(release, create_entities=True)
    staff = StaffUserFactory()
    rows = natural_rows()
    contents = table_contents()
    again = commit(release, by=staff, create_entities=True)
    assert (again.outcome, again.run.pk) == (ALREADY_COMMITTED, first.run.pk)
    assert natural_rows() == rows
    assert table_contents() == contents
    assert commit_summary_lines(again) == [
        f"Release {release.pk} was committed by run {first.run.pk}, and is staged: "
        "nothing was written."
    ]
    # With other options, it is refused.
    with pytest.raises(CommitError) as raised:
        commit(release, by=staff)
    assert raised.value.precondition == 6
    assert str(raised.value) == (
        f"Release {release.pk} was committed by run {first.run.pk} with "
        "--create-entities and no resolution file: discard it with "
        "graph_discard_release to commit it again with other options. Nothing was "
        "written."
    )
    assert table_contents() == contents


def test_another_or_no_resolution_file_is_other_options_for_a_committed_release(
    registry, tmp_path, genes
):
    # Precondition 6's other half: the resolution file, by its SHA-256.
    tp53, mdm2 = genes
    reviewed = resolution_file(tmp_path, "reviewed.json", mdm2, "HGNC:7")
    other = resolution_file(tmp_path, "other.json", mdm2, "HGNC:8")
    release = registered(
        tmp_path, [GENE, row(GENE, record_id="curated", object_id="HGNC:7")]
    )
    first = committed(release, resolutions=reviewed)
    assert first.run.parameters["resolutions"]["sha256"] == (
        dry_run_module._resolutions(reviewed).sha256
    )
    assert statuses(release) == {"r1": MAPPED, "curated": MAPPED}
    staff = StaffUserFactory()
    contents = table_contents()
    for options in (
        {},
        {"resolutions": other},
        {"resolutions": reviewed, "create_entities": True},
    ):
        with pytest.raises(CommitError) as raised:
            commit(release, by=staff, **options)
        assert raised.value.precondition == 6
        assert str(raised.value) == (
            f"Release {release.pk} was committed by run {first.run.pk} without "
            "--create-entities and its resolution file: discard it with "
            "graph_discard_release to commit it again with other options. Nothing "
            "was written."
        )
        assert table_contents() == contents
    # With the same file, it was committed already.
    again = commit(release, by=staff, resolutions=reviewed)
    assert (again.outcome, again.run.pk) == (ALREADY_COMMITTED, first.run.pk)
    assert table_contents() == contents


def test_a_discarded_release_dry_run_and_committed_again_has_the_same_rows(
    registry, tmp_path, genes
):
    # A1 through a discard: the second import writes what the first wrote.
    release = registered(
        tmp_path, [GENE, row(GENE, record_id="r2", object_id="HGNC:5")]
    )
    committed(release, create_entities=True)
    rows = natural_rows()
    discarded = discard_release(
        release, reason="Import it again.", discarded_by=StaffUserFactory()
    )
    assert (discarded.assertions, discarded.records) == (2, 2)
    again = committed(release, create_entities=True)
    assert (again.outcome, again.run.state) == (COMMITTED, COMPLETED)
    assert natural_rows() == rows


def test_a_commit_that_another_commit_finished_meanwhile_was_committed_already(
    registry, tmp_path, monkeypatch, genes
):
    # The release is a draft when 4 and 6 read it; another commit stages it
    # before this one takes the run lock.
    release = registered(tmp_path)
    dry(release)
    taking = commit_module.release_run_lock
    raced, finished = [], []

    def finishing_first(release_id, **kwargs):
        if not raced:
            raced.append(release_id)
            finished.append(commit(release))
        return taking(release_id, **kwargs)

    monkeypatch.setattr(commit_module, "release_run_lock", finishing_first)
    result = commit(release)
    [other] = finished
    assert (other.outcome, other.run.state) == (COMMITTED, COMPLETED)
    assert (result.outcome, result.run.pk) == (ALREADY_COMMITTED, other.run.pk)
    assert commit_runs(release).count() == 1


# ── A commit that stops, and resumes (A2) ───────────────────────────────────


def three_batches(tmp_path, monkeypatch):
    """Six records in three batches of two, sharing entities, relationships,
    papers and terms across batches."""
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    rows = [
        row(GENE, record_id="r1", publications="pmid:12345"),
        row(
            GENE,
            record_id="r2",
            object_id="HGNC:5",
            object_label="Gene five",
            publications="ref:paper-a",
        ),
        row(
            PROTEIN,
            record_id="r3",
            interaction_type="MI:0915",
            method="MI:0018",
            publications="pmid:12345",
        ),
        row(GENE, record_id="r4", object_id="HGNC:7", publications="doi:10.1000/x"),
        row(
            GENE,
            record_id="r5",
            subject_id="HGNC:5",
            object_id="HGNC:7",
            publications="pmid:12345",
        ),
        row(PROTEIN, record_id="r6", method="MI:0018"),
    ]
    return registered(tmp_path, rows)


def uninterrupted(release, staff, **options):
    """The commit of the release run to its end and rolled back: its rows
    by natural key, and its run's report."""
    with transaction.atomic():
        result = commit(release, by=staff, **options)
        assert result.run.state == COMPLETED
        rows = natural_rows()
        report = ImportRun.objects.get(pk=result.run.pk).report
        transaction.set_rollback(True)
    assert not commit_runs(release).exists()
    assert DatasetRelease.objects.get(pk=release.pk).status == ReleaseStatus.DRAFT
    return rows, report


def spying(monkeypatch, release) -> list:
    """The record keys commit.record_claims is called for, in order."""
    keys = dict(
        SourceRecord.objects.filter(release=release).values_list("pk", "record_key")
    )
    real = commit_module.record_claims
    called = []

    def recording(source_record, claims, *, import_run):
        called.append(keys[source_record])
        return real(source_record, claims, import_run=import_run)

    monkeypatch.setattr(commit_module, "record_claims", recording)
    return called


def without_resumed(report) -> dict:
    return {name: value for name, value in report.items() if name != "resumed"}


def named_by_evidence(release) -> dict:
    """The entities, relationships, papers and terms the release's
    evidence names."""
    assertions = EvidenceAssertion.objects.filter(release=release)
    terms = set(
        EvidenceMethod.objects.filter(release=release).values_list("term_id", flat=True)
    )
    terms |= {
        term
        for pair in assertions.values_list("interaction_type_id", "eco_term_id")
        for term in pair
        if term
    }
    return {
        "entities": set(
            AssertionParticipant.objects.filter(release=release).values_list(
                "entity_id", flat=True
            )
        ),
        "relationships": set(assertions.values_list("relationship_id", flat=True)),
        "publications": set(
            EvidencePublication.objects.filter(release=release).values_list(
                "publication_id", flat=True
            )
        ),
        "terms": terms,
    }


def every_row() -> dict:
    return {
        "entities": set(Entity.objects.values_list("pk", flat=True)),
        "relationships": set(Relationship.objects.values_list("pk", flat=True)),
        "publications": set(Publication.objects.values_list("pk", flat=True)),
        "terms": set(Term.objects.values_list("pk", flat=True)),
    }


def test_a_commit_stopped_midway_resumes_to_the_rows_of_an_uninterrupted_one(
    registry, tmp_path, monkeypatch, genes
):
    release = three_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    staff = StaffUserFactory()
    expected_rows, expected_report = uninterrupted(release, staff, create_entities=True)
    before = every_row()
    # Stopped in batch 2, after its entities, papers, terms, relationships
    # and r3's evidence.
    real = stopping(monkeypatch, release, "r4")
    with pytest.raises(CommitStopped) as raised:
        commit(release, by=staff, create_entities=True)
    monkeypatch.setattr(commit_module, "record_claims", real)
    assert str(raised.value) == "Lines 4 to 5: Stopped for the test."
    assert isinstance(raised.value.__cause__.__cause__, ValueError)
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert (run.state, run.error) == (FAILED, "Lines 4 to 5: Stopped for the test.")
    assert run.finished_at is not None
    assert run.checkpoint == {"version": 1, "line": 3, "batches": 1}
    # Batch 2 rolled back whole: the release's evidence is batch 1's, and
    # every row the attempt created is named by it.
    assert sorted(
        assertion.source_record.record_key
        for assertion in EvidenceAssertion.objects.filter(release=release)
    ) == ["r1", "r2"]
    named = named_by_evidence(release)
    for kind, rows in every_row().items():
        assert rows - before[kind] <= named[kind], kind
    # The rerun resumes it, and skips batch 1.
    called = spying(monkeypatch, release)
    resumed = commit(release, by=staff, create_entities=True)
    assert (resumed.outcome, resumed.run.pk, resumed.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )
    assert called == ["r3", "r4", "r5", "r6"]
    [entry] = resumed.run.report["resumed"]
    assert entry["error"] == "Lines 4 to 5: Stopped for the test."
    assert (entry["line"], entry["by"]) == (3, staff.username)
    assert set(
        EvidenceAssertion.objects.filter(release=release).values_list(
            "import_run_id", flat=True
        )
    ) == {run.pk}
    assert natural_rows() == expected_rows
    assert without_resumed(resumed.run.report) == without_resumed(expected_report)
    assert commit_summary_lines(resumed)[0] == (
        f"Commit run {run.pk} of release {release.pk}: completed after 1 resume, and "
        "the release is staged."
    )


def test_a_commit_stopped_in_its_last_transaction_resumes_to_the_same_rows(
    registry, tmp_path, monkeypatch, genes
):
    release = three_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    staff = StaffUserFactory()
    expected_rows, expected_report = uninterrupted(release, staff, create_entities=True)

    def failing(release):
        raise ValueError("Stopped for the test.")

    with monkeypatch.context() as patched:
        patched.setattr(commit_module, "release_stats", failing)
        with pytest.raises(CommitStopped) as raised:
            commit(release, by=staff, create_entities=True)
    assert str(raised.value) == f"Staging release {release.pk}: Stopped for the test."
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert run.state == FAILED
    assert run.checkpoint == {"version": 1, "line": 7, "batches": 3}
    assert DatasetRelease.objects.get(pk=release.pk).status == ReleaseStatus.DRAFT
    assert EvidenceAssertion.objects.filter(release=release).count() == 6
    called = spying(monkeypatch, release)
    resumed = commit(release, by=staff, create_entities=True)
    assert (resumed.outcome, resumed.run.state) == (RESUMED, COMPLETED)
    # Every batch committed: none is written again.
    assert called == []
    assert natural_rows() == expected_rows
    assert without_resumed(resumed.run.report) == without_resumed(expected_report)


def test_a_database_error_in_a_batch_stops_the_commit_resumably(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    real = commit_module.create_relationships
    monkeypatch.setattr(commit_module, "create_relationships", deadlocked)
    with pytest.raises(CommitStopped) as raised:
        commit(release)
    # The error's first line, as a sentence.
    assert str(raised.value) == (
        "Line 3: the database stopped the batch: deadlock detected."
    )
    assert isinstance(raised.value.__cause__.__cause__, OperationalError)
    assert raised.value.marked
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert (run.state, run.checkpoint["line"]) == (FAILED, 2)
    monkeypatch.setattr(commit_module, "create_relationships", real)
    assert commit(release).run.state == COMPLETED


def test_the_command_words_a_database_error_and_its_advice_as_sentences(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    monkeypatch.setattr(commit_module, "create_relationships", deadlocked)
    staff = StaffUserFactory()
    with pytest.raises(CommandError) as raised:
        command("--release", release.pk, "--commit", "--as", staff.username)
    run = commit_runs(release).get()
    assert str(raised.value) == (
        f"The commit stopped, and its run {run.pk} is marked failed: Line 3: the "
        "database stopped the batch: deadlock detected. Run the same command again to "
        "resume it once the cause is fixed, or discard it with graph_discard_release."
    )


def test_a_database_error_in_the_staging_stops_the_commit_resumably(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    with monkeypatch.context() as patched:
        patched.setattr(commit_module, "release_stats", deadlocked)
        with pytest.raises(CommitStopped) as raised:
            commit(release)
    stopped = (
        f"Staging release {release.pk}: the database stopped it: deadlock detected."
    )
    assert str(raised.value) == stopped
    assert isinstance(raised.value.__cause__.__cause__, OperationalError)
    assert raised.value.marked
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert (run.state, run.error, run.checkpoint["line"]) == (FAILED, stopped, 3)
    assert DatasetRelease.objects.get(pk=release.pk).status == ReleaseStatus.DRAFT
    resumed = commit(release)
    assert (resumed.outcome, resumed.run.pk, resumed.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )


def test_a_refusal_of_create_entity_stops_the_commit_and_says_so(
    registry, tmp_path, monkeypatch, genes
):
    release = registered(tmp_path, [row(GENE, object_id="HGNC:5")])
    staff = StaffUserFactory()
    dry(release, create_entities=True)
    real = commit_module.create_entity
    longest = Entity._meta.get_field("label").max_length

    def too_long(entity_type, label, *args, **kwargs):
        return real(entity_type, "x" * (longest + 1), *args, **kwargs)

    monkeypatch.setattr(commit_module, "create_entity", too_long)
    with pytest.raises(CommandError) as raised:
        command(
            "--release",
            release.pk,
            "--commit",
            "--create-entities",
            "--as",
            staff.username,
        )
    run = commit_runs(release).get()
    assert str(raised.value) == (
        f"The commit stopped, and its run {run.pk} is marked failed: Line 2: An entity "
        f"label is at most {longest} characters. Run the same command again to resume "
        "it once the cause is fixed, or discard it with graph_discard_release."
    )
    assert run.state == FAILED
    assert not Entity.objects.filter(created_by_release=release).exists()


@pytest.mark.postgres
def test_a_run_left_running_by_a_restored_database_is_marked_then_resumed(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    run = failed_commit(monkeypatch, release, "r2", create_entities=True)
    # As a database restored mid-commit holds it: running, and no process
    # holds the release's run lock.
    run.state = RUNNING
    run.error = ""
    run.finished_at = None
    run.save(update_fields=["state", "error", "finished_at"])
    assert run_lock_holder(release.pk) is None
    # graph_check lists it as left running.
    [runs] = [section for section in run_checks() if section.name == "runs"]
    assert runs.lines == (
        f"Run {run.pk} (a commit) of release {release.pk} was left running by a "
        "process that ended: the release's next commit or discard marks it failed, "
        "then resume it with graph_import --commit or discard it with "
        "graph_discard_release.",
    )
    staff = StaffUserFactory()
    resumed = commit(release, by=staff, create_entities=True)
    assert (resumed.outcome, resumed.run.pk, resumed.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )
    [entry] = resumed.run.report["resumed"]
    assert entry["error"] == (
        "Its process ended before the run finished; found when a commit of release "
        f"{release.pk} started."
    )
    assert entry["line"] == 2


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_commit_whose_session_ended_leaves_its_run_for_the_next_commit(
    registry, tmp_path, monkeypatch, genes
):
    # The commit's backend is ended inside a batch, as a killed process's
    # session ends, and the run lock goes with it. Django connects again, as
    # a new session that holds nothing, so the attempt writes nothing more
    # of the run: another process may hold the lock by then. The next
    # commit marks the run failed and resumes it.
    release = three_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    target = SourceRecord.objects.get(release=release, record_key="r4").pk
    real = commit_module.record_claims
    ended, failed = [], []

    def ending(source_record, claims, *, import_run):
        if source_record == target and not ended:
            ended.append(run_lock_holder(release.pk))

            def end():
                try:
                    terminate_backend(ended[0])
                except BaseException as error:  # reported by the main thread
                    failed.append(error)
                finally:
                    connection.close()

            thread = threading.Thread(target=end, daemon=True)
            thread.start()
            thread.join(WAIT_SECONDS)
            assert not thread.is_alive(), "the commit's backend was never ended"
            assert not failed, failed
        return real(source_record, claims, import_run=import_run)

    monkeypatch.setattr(commit_module, "record_claims", ending)
    with pytest.raises(CommitStopped) as raised:
        commit(release, create_entities=True)
    stopped = raised.value
    assert not stopped.marked
    assert str(stopped).startswith("Lines 4 to 5: the database stopped the batch: ")
    assert (
        f"Run {stopped.run_id} could not be marked failed (the connection's session "
        "ended, and the run lock with it): it stays running until the release's "
        "next commit or discard marks it."
    ) in stopped.__notes__
    run = ImportRun.objects.get(pk=stopped.run_id)
    assert (run.state, run.error, run.checkpoint["line"]) == (RUNNING, "", 3)
    assert run_lock_holder(release.pk) is None
    monkeypatch.setattr(commit_module, "record_claims", real)
    resumed = commit(release, create_entities=True)
    assert (resumed.outcome, resumed.run.pk, resumed.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )
    [entry] = resumed.run.report["resumed"]
    assert entry["error"] == (
        "Its process ended before the run finished; found when a commit of release "
        f"{release.pk} started."
    )
    assert sorted(
        assertion.source_record.record_key
        for assertion in EvidenceAssertion.objects.filter(release=release)
    ) == ["r1", "r2", "r3", "r4", "r5", "r6"]


# ── The failure marking ─────────────────────────────────────────────────────


def test_a_resume_refused_as_it_starts_leaves_its_run_as_it_was(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    run = failed_commit(monkeypatch, release, "r2")
    kept = ("state", "error", "finished_at", "checkpoint", "report")
    stored = ImportRun.objects.values(*kept).get(pk=run.pk)
    # The approval lapses after the planning, before the run is resumed.
    real = commit_module.plan_batch
    planned = []

    def revoking(records, *, run_ctx, check=True):
        plans = real(records, run_ctx=run_ctx, check=check)
        planned.append(check)
        if planned == [True, True]:
            for approval in UsageApproval.objects.filter(release=release):
                revoke(approval, reason="Test.", revoked_by=StaffUserFactory())
        return plans

    monkeypatch.setattr(commit_module, "plan_batch", revoking)
    with pytest.raises(CommitError) as raised:
        commit(release)
    assert raised.value.precondition == 8
    assert ImportRun.objects.values(*kept).get(pk=run.pk) == stored


def test_a_run_a_discard_gave_up_meanwhile_is_never_reopened(
    registry, tmp_path, monkeypatch, genes
):
    # A discard on a database without the run lock gives up a running run
    # (simulated here): its next batch stops, and the run stays abandoned.
    release = two_batches(tmp_path, monkeypatch)
    dry(release)

    def abandoned():
        run = commit_runs(release).get()
        run.state = ABANDONED
        run.save(update_fields=["state"])

    before_writing(monkeypatch, 2, abandoned)
    staff = StaffUserFactory()
    with pytest.raises(CommandError) as raised:
        command("--release", release.pk, "--commit", "--as", staff.username)
    run = commit_runs(release).get()
    assert run.state == ABANDONED
    assert str(raised.value) == (
        f"The commit stopped, and its run {run.pk} was given up by a discard "
        f"meanwhile: Run {run.pk} of release {release.pk} is abandoned now: a "
        "discard gave it up. Dry-run the release again, then commit it."
    )


def test_a_run_that_cannot_be_marked_stays_running_and_the_command_says_so(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    stopping(monkeypatch, release, "r2")
    saving = ImportRun.save

    def connection_lost(self, *args, **kwargs):
        marking = kwargs.get("update_fields") == ["state", "error", "finished_at"]
        if marking and self.state == FAILED:
            raise InterfaceError("connection already closed")
        return saving(self, *args, **kwargs)

    monkeypatch.setattr(ImportRun, "save", connection_lost)
    with pytest.raises(CommitStopped) as stopped:
        commit(release)
    assert not stopped.value.marked
    if runs_are_locked():
        stays = "it stays running until the release's next commit or discard marks it."
    else:
        stays = (
            "it stays running, and on this database, which has no run lock, only a "
            "discard gives it up."
        )
    assert stopped.value.__notes__ == [
        f"Run {stopped.value.run_id} could not be marked failed (InterfaceError: "
        f"connection already closed): {stays}"
    ]
    run = ImportRun.objects.get(pk=stopped.value.run_id)
    assert (run.state, run.error) == (RUNNING, "")
    # Through the command, for another release.
    staff = StaffUserFactory()
    other = registered(tmp_path / "other", [GENE, row(GENE, record_id="r2")])
    dry(other)
    stopping(monkeypatch, other, "r2")
    with pytest.raises(CommandError) as raised:
        command("--release", other.pk, "--commit", "--as", staff.username)
    other_run = commit_runs(other).get()
    if runs_are_locked():
        said = (
            f"The commit stopped, and its run {other_run.pk} could not be marked "
            "failed, since the database connection was lost: Line 3: Stopped for the "
            "test. The next commit or discard of the release marks it. Run the same "
            "command again to resume it once the cause is fixed, or discard it with "
            "graph_discard_release."
        )
    else:
        said = (
            f"The commit stopped, and its run {other_run.pk} could not be marked "
            "failed: Line 3: Stopped for the test. It stays running, and on this "
            "database, which has no run lock, a commit never resumes a running run: "
            "discard it with graph_discard_release, then dry-run the release again "
            "and commit it."
        )
    assert str(raised.value) == said


def test_without_the_run_lock_a_run_that_cannot_be_marked_is_discarded(
    registry, tmp_path, monkeypatch, genes
):
    # As a database without the run lock has it (patched where the test
    # database has the lock): nothing marks the run and a commit never
    # resumes a running one, so the command names the discard; following it
    # works.
    if runs_are_locked():
        for module in (commit_module, graph_import_command):
            monkeypatch.setattr(module, "runs_are_locked", lambda using=None: False)
        for module in (commit_module, releases_module):
            monkeypatch.setattr(
                module, "end_dead_runs", lambda release_id, **kwargs: []
            )
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    real = stopping(monkeypatch, release, "r2")
    saving = ImportRun.save

    def connection_lost(self, *args, **kwargs):
        marking = kwargs.get("update_fields") == ["state", "error", "finished_at"]
        if marking and self.state == FAILED:
            raise InterfaceError("connection already closed")
        return saving(self, *args, **kwargs)

    monkeypatch.setattr(ImportRun, "save", connection_lost)
    staff = StaffUserFactory()
    with pytest.raises(CommandError) as raised:
        command("--release", release.pk, "--commit", "--as", staff.username)
    run = commit_runs(release).get()
    assert str(raised.value) == (
        f"The commit stopped, and its run {run.pk} could not be marked failed: Line 3: "
        "Stopped for the test. It stays running, and on this database, which has no "
        "run lock, a commit never resumes a running run: discard it with "
        "graph_discard_release, then dry-run the release again and commit it."
    )
    assert (run.state, run.error) == (RUNNING, "")
    monkeypatch.setattr(ImportRun, "save", saving)
    monkeypatch.setattr(commit_module, "record_claims", real)
    # Run again, it is refused (10.1); the discard gives the run up.
    with pytest.raises(CommitError) as again:
        commit(release, by=staff)
    assert again.value.precondition == 10
    discarded = discard_release(
        release, reason="Give it up.", discarded_by=StaffUserFactory()
    )
    assert (discarded.run.pk, discarded.run.state) == (run.pk, ABANDONED)
    dry(release)
    assert commit(release, by=staff).outcome == COMMITTED


# ── What each batch checks again ────────────────────────────────────────────


def test_an_approval_revoked_between_batches_stops_the_commit_until_approved(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)

    def revoked():
        for approval in UsageApproval.objects.filter(release=release):
            revoke(approval, reason="Test.", revoked_by=StaffUserFactory())

    real = commit_module.plan_batch
    before_writing(monkeypatch, 2, revoked)
    with pytest.raises(CommitStopped) as raised:
        commit(release)
    assert str(raised.value) == (
        f"Release {release.pk} has no effective internal approval now."
    )
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert (run.state, run.checkpoint["line"]) == (FAILED, 2)
    monkeypatch.setattr(commit_module, "plan_batch", real)
    approve(release, ApprovalScope.INTERNAL)
    result = commit(release)
    assert (result.outcome, result.run.pk, result.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )


def test_a_registry_change_between_batches_stops_the_commit(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    before_writing(
        monkeypatch,
        2,
        lambda: RegistryState.objects.filter(pk=1).update(version="9.9"),
    )
    with pytest.raises(CommitStopped) as raised:
        commit(release)
    assert str(raised.value) == (
        f"The database's registry is at version 9.9 now, and this code's at "
        f"{REGISTRY_VERSION}."
    )
    assert ImportRun.objects.get(pk=raised.value.run_id).state == FAILED


def test_a_registration_meanwhile_is_refused_while_the_run_is_open(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    folder = tmp_path / f"upload-{release.dataset.slug}-v1"
    refused = []

    def registering():
        with monkeypatch.context() as patched:
            patched.setitem(
                adapters.ADAPTERS,
                SPEC.name,
                SimpleNamespace(
                    spec=replace(SPEC, version="1.1.0", mapping_version="2")
                ),
            )
            with pytest.raises(RegistrationError) as raised:
                register_release(
                    dataset=release.dataset.slug,
                    version="v1",
                    adapter=SPEC.name,
                    file=folder / "release.tsv",
                    manifest=folder / "manifest.json",
                    registered_by=StaffUserFactory(),
                )
            refused.append(str(raised.value))

    before_writing(monkeypatch, 2, registering)
    assert commit(release).run.state == COMPLETED
    [message] = refused
    assert message.endswith(
        "a commit run of it is open. Discard it with graph_discard_release, then "
        "register it again; or register its files under a new version. Nothing was "
        "written."
    )


# ── Races that refuse ───────────────────────────────────────────────────────


def test_an_identifier_another_entity_took_meanwhile_fails_the_batch(
    registry, tmp_path, monkeypatch, genes
):
    release = registered(tmp_path, [row(GENE, object_id="HGNC:5")])
    dry_run_row = dry(release, create_entities=True)
    holders = []

    def taken():
        holder = create_entity("gene", "Other gene", 9606, "hgnc", "HGNC:7")
        add_identifier(holder, "hgnc", "HGNC:5", IdentifierRole.SECONDARY)
        holders.append(holder)

    real = commit_module.plan_batch
    before_writing(monkeypatch, 1, taken)
    with pytest.raises(CommitStopped) as raised:
        commit(release, create_entities=True)
    [holder] = holders
    assert str(raised.value) == (
        f"Line 2: hgnc HGNC:5 is already held by entity {holder.pk}; it does not "
        "define a new one."
    )
    run = ImportRun.objects.get(pk=raised.value.run_id)
    assert run.state == FAILED
    assert not EvidenceAssertion.objects.filter(release=release).exists()
    monkeypatch.setattr(commit_module, "plan_batch", real)
    # The rerun plans the record as a candidate now.
    with pytest.raises(CommitError) as again:
        commit(release, create_entities=True)
    assert again.value.precondition == 15
    message = str(again.value)
    assert message.startswith(
        f"1 record of release {release.pk} maps differently from its latest dry run "
        f"{dry_run_row.pk}, for example record 'r1' of line 2: mapped then, "
        "unresolved now ("
    )
    assert message.endswith(f"). {open_run_ending(run.pk)}")


# ── Publication-identifier conflicts (A6) ───────────────────────────────────


def test_a_publication_identifier_another_paper_holds_is_reported_once(
    registry, tmp_path, monkeypatch, genes
):
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    rows = [
        row(GENE, publications="pmid:12345"),
        row(GENE, record_id="r2"),
        row(GENE, record_id="r3", publications="pmid:12345"),
    ]
    release = registered(tmp_path, rows)
    paper = PublicationFactory(pmid="12345")
    held = PublicationFactory(pmid="", doi="10.1000/held")
    dry(release)
    real = commit_module.get_or_create_publication

    def coreferent(**identifiers):
        # As M1 will pass co-referent identifiers: the PMID paper's call also
        # gives a DOI that another stored paper holds.
        if identifiers.get("pmid") == "12345":
            identifiers["doi"] = "10.1000/held"
        return real(**identifiers)

    monkeypatch.setattr(commit_module, "get_or_create_publication", coreferent)
    # Stopped in batch 2, and resumed: batch 3 meets the conflict again.
    failed_commit(monkeypatch, release, "r2")
    result = commit(release)
    assert (result.outcome, result.run.state) == (RESUMED, COMPLETED)
    paper.refresh_from_db()
    assert paper.doi == ""
    assert result.run.report["publication_conflicts"] == {
        "total": 1,
        "truncated": False,
        "entries": [
            {
                "publication": paper.pk,
                "field": "doi",
                "value": "10.1000/held",
                "current": "",
                "holder": held.pk,
                "line": 2,
            }
        ],
    }
    assert set(
        EvidencePublication.objects.filter(release=release).values_list(
            "publication_id", flat=True
        )
    ) == {paper.pk}
    assert (
        "1 publication identifier is held by another publication: graph_check "
        "lists it." in commit_summary_lines(result)
    )
    # graph_check lists it for review, once.
    [section] = [s for s in run_checks() if s.name == "publication_conflicts"]
    assert (section.kind, section.lines) == (
        "review",
        (
            f"Publication {paper.pk} was not given doi 10.1000/held (publication "
            f"{held.pk} holds this doi): release {release.pk}, line 2, run "
            f"{result.run.pk}.",
        ),
    )


def test_past_its_cap_a_list_counts_a_key_again_after_a_resume(
    registry, tmp_path, monkeypatch, genes
):
    # A documented limit, handed to M1 (IMPLEMENTATION_PLAN.md, M1): a resume
    # rebuilds the conflict list's keys from the entries it kept, so a
    # conflict counted past the cap before the stop is counted again when a
    # later batch meets it. This test fails, as it should, once M1 keeps every
    # counted key.
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    rows = [
        row(GENE, record_id="r1", publications="pmid:111"),
        row(GENE, record_id="r2", publications="pmid:222"),
        row(GENE, record_id="r3", publications="pmid:222"),
        row(GENE, record_id="r4"),
    ]
    release = registered(tmp_path, rows)
    for pmid in ("111", "222"):
        PublicationFactory(pmid=pmid)
    held = {
        pmid: PublicationFactory(pmid="", doi=f"10.1000/held-{pmid}").doi
        for pmid in ("111", "222")
    }
    dry(release)
    real = commit_module.get_or_create_publication

    def coreferent(**identifiers):
        # A6's route: each PMID paper's call also gives a DOI another holds.
        if identifiers.get("pmid") in held:
            identifiers["doi"] = held[identifiers["pmid"]]
        return real(**identifiers)

    monkeypatch.setattr(commit_module, "get_or_create_publication", coreferent)
    monkeypatch.setattr(limits, "REPORT_LIST_ENTRIES", 1)
    staff = StaffUserFactory()
    _, expected = uninterrupted(release, staff)
    # Two distinct conflicts, both in batch 1; the list keeps the first.
    listed = expected["publication_conflicts"]
    assert (listed["total"], len(listed["entries"])) == (2, 1)
    # Stopped in batch 2 (r4), after batch 1 counted both; batch 2 cites
    # PMID 222 again.
    run = failed_commit(monkeypatch, release, "r4")
    assert run.checkpoint["line"] == 3
    assert run.report["publication_conflicts"] == listed
    resumed = commit(release, by=staff)
    assert (resumed.outcome, resumed.run.state) == (RESUMED, COMPLETED)
    again = resumed.run.report["publication_conflicts"]
    assert again["entries"] == listed["entries"]
    assert again["total"] == 3


def test_past_its_cap_a_resume_counts_each_term_and_paper_once(
    registry, tmp_path, monkeypatch
):
    # Plain canonical_tsv_v1 rows fill term_labels and paper_differences. A
    # truncated list keeps every key it counted, so batch 2, which names
    # MI:0019 and cites paper-b again, counts neither again on a resume.
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    papers = {
        key: {"title": f"New {key}", "year": 2021, "is_synthetic": False}
        for key in ("paper-a", "paper-b")
    }
    rows = [
        row(PROTEIN, record_id="r1", method="MI:0018", publications="ref:paper-a"),
        row(PROTEIN, record_id="r2", method="MI:0019", publications="ref:paper-b"),
        row(PROTEIN, record_id="r3", method="MI:0019", publications="ref:paper-b"),
    ]
    terms = {**TERMS, "MI:0019": "coimmunoprecipitation"}
    release = registered(
        tmp_path, rows, manifest=manifest_bytes(publications=papers, terms=terms)
    )
    # Stored before the planning, with other labels, titles and years.
    TermFactory(curie="MI:0018", label="two-hybrid (old)")
    TermFactory(curie="MI:0019", label="coIP (old)")
    for key in papers:
        PublicationFactory(
            pmid="",
            local_dataset=release.dataset,
            local_key=key,
            title=f"Old {key}",
            year=2020,
        )
    dry(release, create_entities=True)
    monkeypatch.setattr(limits, "REPORT_LIST_ENTRIES", 1)
    staff = StaffUserFactory()
    _, expected = uninterrupted(release, staff, create_entities=True)
    # Stopped in batch 2 (r3), after batch 1 counted both of each.
    run = failed_commit(monkeypatch, release, "r3", create_entities=True)
    assert run.checkpoint["line"] == 3
    resumed = commit(release, by=staff, create_entities=True)
    assert (resumed.outcome, resumed.run.state) == (RESUMED, COMPLETED)
    lists = {
        "term_labels": (["MI:0018", "MI:0019"], "MI:0018"),
        "paper_differences": (["paper-a", "paper-b"], "paper-a"),
    }
    assert (
        {name: resumed.run.report[name]["total"] for name in lists}
        == {name: expected[name]["total"] for name in lists}
        == {"term_labels": 2, "paper_differences": 2}
    )
    for name, (keys, first) in lists.items():
        again = resumed.run.report[name]
        assert again == expected[name] == run.report[name]
        assert (again["truncated"], again["keys"]) == (True, keys)
        [entry] = again["entries"]
        assert first in entry.values()


# ── The report, the statistics and the staging ─────────────────────────────


def recount(release) -> dict:
    """The release's figures counted in Python, over every row."""
    assertions = [
        a for a in EvidenceAssertion.objects.all() if a.release_id == release.pk
    ]
    ids = {assertion.pk for assertion in assertions}
    by_type = {}
    for assertion in assertions:
        slug = assertion.relationship.relationship_type_id
        seen = by_type.setdefault(slug, {"assertions": 0, "relationships": set()})
        seen["assertions"] += 1
        seen["relationships"].add(assertion.relationship_id)
    records = Counter(
        record.status
        for record in SourceRecord.objects.all()
        if record.release_id == release.pk
    )
    outcomes = Counter(assertion.outcome for assertion in assertions)
    return {
        "version": 1,
        "records": {status: records[status] for status in RecordStatus.values},
        "assertions": len(assertions),
        "assertions_by_outcome": {
            outcome: outcomes[outcome] for outcome in Outcome.values
        },
        "relationships": len({a.relationship_id for a in assertions}),
        "entities": len(
            {
                p.entity_id
                for p in AssertionParticipant.objects.all()
                if p.assertion_id in ids
            }
        ),
        "publications": len(
            {
                p.publication_id
                for p in EvidencePublication.objects.all()
                if p.assertion_id in ids
            }
        ),
        "entities_created": sum(
            1 for e in Entity.objects.all() if e.created_by_release_id == release.pk
        ),
        "relationship_types": {
            slug: {
                "assertions": seen["assertions"],
                "relationships": len(seen["relationships"]),
            }
            for slug, seen in sorted(by_type.items())
        },
    }


def test_the_staged_release_holds_its_statistics_and_the_run_its_report(
    registry, tmp_path, genes
):
    rows = [
        row(GENE, publications="pmid:12345|ref:paper-a"),
        row(GENE, record_id="r2", object_id="HGNC:5", publications="pmid:12345"),
        row(PROTEIN, record_id="r3", interaction_type="MI:0915"),
        row(GENE, record_id="rejected", effect="negative"),
    ]
    release = registered(tmp_path, rows)
    dry_run_row = dry(release, create_entities=True)
    result = commit(release, create_entities=True)
    release.refresh_from_db()
    assert release.status == ReleaseStatus.STAGED
    # A staged release has no open commit run: its run completed with it.
    assert not commit_runs(release).filter(state__in=[RUNNING, FAILED]).exists()
    assert result.run.finished_at is not None
    figures = {
        "version": 1,
        "records": {
            "mapped": 3,
            "unresolved": 0,
            "ambiguous": 0,
            "rejected": 1,
            "skipped": 0,
        },
        # r1 reports two papers: an assertion each.
        "assertions": 4,
        "assertions_by_outcome": {
            "supported": 4,
            "tested_absent": 0,
            "inconclusive": 0,
            "not_significant": 0,
        },
        "relationships": 3,
        "entities": 5,
        "publications": 2,
        "entities_created": 3,
        "relationship_types": {
            "physical_interaction": {"assertions": 1, "relationships": 1},
            "transcriptional_regulation": {"assertions": 3, "relationships": 2},
        },
    }
    assert release.stats == release_stats(release) == recount(release) == figures
    report = result.run.report
    assert report == {
        "dry_run": dry_run_row.pk,
        "adapter": dry_run_row.report["adapter"],
        "registry": {"code": REGISTRY_VERSION, "database": REGISTRY_VERSION},
        "release": dry_run_row.report["release"],
        "create_entities": True,
        "resolutions": "",
        "planning_readings": 1,
        "counts": {
            "records": figures["records"],
            "batches": 1,
            "recorded": 3,
            "already_recorded": 0,
            "assertions": 4,
            "created": {
                "entities": 3,
                "relationships": 3,
                "publications": 2,
                "terms": 1,
            },
            "late_relationships": 0,
        },
        "term_labels": {"total": 0, "truncated": False, "entries": []},
        "publication_conflicts": {"total": 0, "truncated": False, "entries": []},
        "paper_differences": {"total": 0, "truncated": False, "entries": []},
        "resumed": [],
    }
    assert result.run.parameters == {
        "create_entities": True,
        "resolutions": {"sha256": "", "entries": []},
        "registry_version": REGISTRY_VERSION,
        "dry_run": dry_run_row.pk,
    }


def test_a_conflict_across_batches_is_read_twice_as_the_dry_run_reads_it(
    registry, tmp_path, monkeypatch, genes
):
    # A value stated at two taxa, the second in a later batch: the planning
    # reads the file a second time, and the commit creates nothing for it.
    monkeypatch.setattr(limits, "BATCH_ROWS", 1)
    rows = [
        row(PROTEIN, subject_id="P12345", object_id="Q00987"),
        row(PROTEIN, record_id="r2", subject_id="P12345", subject_taxon="7227"),
        row(PROTEIN, record_id="r3", subject_id="P99999", object_id="Q00987"),
    ]
    release = registered(tmp_path, rows)
    dry(release, create_entities=True)
    stored = statuses(release)
    result = commit(release, create_entities=True)
    assert result.run.report["planning_readings"] == 2
    assert statuses(release) == stored
    assert not Entity.objects.filter(identity_key="uniprotkb||P12345").exists()
    assert result.run.report["counts"]["recorded"] == sum(
        1 for status in stored.values() if status == MAPPED
    )


# ── The command ─────────────────────────────────────────────────────────────


def test_the_command_commits_and_prints_the_summary(registry, tmp_path, genes):
    release = registered(
        tmp_path, [GENE, row(GENE, record_id="r2", object_id="HGNC:5")]
    )
    staff = StaffUserFactory()
    dry_run_row = dry(release, create_entities=True)
    out = command(
        "--release", release.pk, "--commit", "--create-entities", "--as", staff.username
    )
    run = commit_runs(release).get()
    assert run.triggered_by == staff
    assert out.splitlines() == [
        f"Commit run {run.pk} of release {release.pk}: completed, and the release is "
        "staged.",
        f"It imported the records of dry run {dry_run_row.pk}: 2 mapped, 0 "
        "unresolved, 0 ambiguous, 0 rejected, 0 skipped.",
        "Recorded 2 records (0 already recorded), with 2 assertions.",
        "Created 1 entity, 2 relationships, 0 publications and 0 terms.",
        "Next: publish it with graph_publish_release, or discard it with "
        "graph_discard_release.",
    ]
    # Run again: committed already.
    assert command(
        "--release", release.pk, "--commit", "--create-entities", "--as", staff.username
    ).splitlines() == [
        f"Release {release.pk} was committed by run {run.pk}, and is staged: nothing "
        "was written."
    ]


def test_the_command_refuses_a_report_file_with_a_commit_and_without_the_switch(
    registry, settings, tmp_path, genes
):
    release = registered(tmp_path)
    dry(release)
    with pytest.raises(CommandError) as raised:
        command(
            "--release",
            release.pk,
            "--commit",
            "--report-file",
            tmp_path / "report.jsonl",
            "--as",
            "nobody",
        )
    assert str(raised.value) == (
        "--report-file goes with --dry-run: a commit keeps its report with its run. "
        "Nothing was written."
    )
    with pytest.raises(CommandError, match="names no account"):
        command("--release", release.pk, "--commit", "--as", "nobody")
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(CommandError) as raised:
        command("--release", release.pk, "--commit", "--as", "nobody")
    assert str(raised.value) == (
        "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no release is "
        "imported. Nothing was written."
    )
    assert not commit_runs(release).exists()


def test_the_command_says_the_commit_stopped_and_its_run_is_marked_failed(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    staff = StaffUserFactory()
    stopping(monkeypatch, release, "r2")
    with pytest.raises(CommandError) as raised:
        command("--release", release.pk, "--commit", "--as", staff.username)
    run = commit_runs(release).get()
    assert str(raised.value) == (
        f"The commit stopped, and its run {run.pk} is marked failed: Line 3: Stopped "
        "for the test. Run the same command again to resume it once the cause is "
        "fixed, or discard it with graph_discard_release."
    )
    assert run.state == FAILED


def test_the_summary_follows_the_numbers(registry, tmp_path, genes):
    release = registered(tmp_path, [row(GENE, object_id="HGNC:5")])
    result = committed(release, create_entities=True)
    lines = commit_summary_lines(result)
    assert lines[2:4] == [
        "Recorded 1 record (0 already recorded), with 1 assertion.",
        "Created 1 entity, 1 relationship, 0 publications and 0 terms.",
    ]
    report = result.run.report
    report["resumed"] = [{}, {}]
    report["term_labels"]["total"] = 2
    report["publication_conflicts"]["total"] = 3
    report["counts"]["created"].update(publications=1, terms=1)
    lines = commit_summary_lines(SimpleNamespace(run=result.run, outcome=RESUMED))
    assert lines[0] == (
        f"Commit run {result.run.pk} of release {release.pk}: completed after 2 "
        "resumes, and the release is staged."
    )
    assert lines[3:] == [
        "Created 1 entity, 1 relationship, 1 publication and 1 term.",
        "2 terms keep a label other than the manifest's: graph_check lists them.",
        "3 publication identifiers are held by other publications: graph_check lists "
        "them.",
        "Next: publish it with graph_publish_release, or discard it with "
        "graph_discard_release.",
    ]
    report["paper_differences"]["total"] = 2
    lines = commit_summary_lines(SimpleNamespace(run=result.run, outcome=RESUMED))
    assert lines[5] == (
        "2 papers keep a title or year other than the manifest's: graph_check lists "
        "them."
    )


def test_the_summary_of_a_stored_run_names_the_statuses_in_order(
    registry, tmp_path, genes
):
    # A stored report's keys come back in the database's order (PostgreSQL's
    # JSONB orders them by length): the summary keeps RecordStatus's.
    release = registered(
        tmp_path, [GENE, row(GENE, record_id="r2", object_id="HGNC:5")]
    )
    result = committed(release)
    stored = ImportRun.objects.get(pk=result.run.pk)
    assert commit_summary_lines(CommitResult(stored, COMMITTED)) == (
        commit_summary_lines(result)
    )
    assert commit_summary_lines(result)[1].endswith(
        ": 1 mapped, 1 unresolved, 0 ambiguous, 0 rejected, 0 skipped."
    )
