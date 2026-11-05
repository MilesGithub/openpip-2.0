"""graph_register_release (graph.ingest.registration): a new release and its
stored files, the options it records, a release registered already, a lost
copy restored, a draft that takes a new adapter version, every difference
refused, and what a failure leaves behind."""

import datetime
import io
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graph import storage
from graph.claims import Claim, Endpoint, record_claims
from graph.entities import create_entity
from graph.ingest import limits
from graph.ingest import registry as adapters
from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.dry_run import dry_run
from graph.models import (
    DatasetRelease,
    Entity,
    FileRole,
    ImportMode,
    ImportState,
    ReleaseKind,
    ReleaseStatus,
    SourceFile,
    SourceRecord,
)
from graph.releases import publish_release, withdraw_release
from graph.tests.factories import (
    DatasetReleaseFactory,
    ImportRunFactory,
    SourceDatasetFactory,
    StaffUserFactory,
    TaxonFactory,
)
from graph.tests.ingest_helpers import (
    GENE,
    approved_for_internal_use,
    manifest_bytes,
    tsv,
    write,
)

DATA = tsv(GENE)
# How registration's refusal ends for a staged release, one whose records
# hold evidence, and one with an open commit run.
DISCARD_FIRST = (
    "Discard it with graph_discard_release, then register it again; or register its "
    "files under a new version. Nothing was written."
)


@pytest.fixture
def dataset(registry):
    return SourceDatasetFactory()


@pytest.fixture
def staff(registry):
    return StaffUserFactory()


@pytest.fixture
def files(tmp_path):
    """A data file and a manifest, as an operator stages them."""
    folder = tmp_path / "incoming-files"
    return SimpleNamespace(
        data=write(folder, "release.tsv", DATA),
        manifest=write(folder, "manifest.json", manifest_bytes(False)),
        other=write(folder, "other.tsv", tsv(GENE, {**GENE, "record_id": "r2"})),
        folder=folder,
    )


def register(dataset, staff, files, *extra, version="v1", manifest=True) -> str:
    out = io.StringIO()
    args = [
        "--dataset",
        dataset.slug,
        "--version",
        version,
        "--adapter",
        SPEC.name,
        "--file",
        str(files.data),
        "--as",
        staff.username,
    ]
    if manifest:
        args += ["--manifest", str(files.manifest)]
    call_command("graph_register_release", *args, *map(str, extra), stdout=out)
    return out.getvalue()


def stored_files(root) -> list:
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    )


def release_of(dataset, version="v1") -> DatasetRelease:
    return DatasetRelease.objects.get(dataset=dataset, version=version)


# ── A new release ───────────────────────────────────────────────────────────


def test_a_release_is_registered_as_a_draft_with_its_files(
    dataset, staff, files, graph_files_root
):
    out = register(dataset, staff, files)
    release = release_of(dataset)
    assert release.status == ReleaseStatus.DRAFT
    assert (
        release.adapter,
        release.adapter_version,
        release.mapping_version,
        release.kind,
    ) == (SPEC.name, SPEC.version, SPEC.mapping_version, ReleaseKind.EVIDENCE)
    data = release.files.get(role=FileRole.DATA)
    manifest = release.files.get(role=FileRole.MANIFEST)
    assert (data.original_name, data.size, data.media_type) == (
        "release.tsv",
        len(DATA),
        "text/tab-separated-values",
    )
    assert manifest.media_type == "application/json"
    assert storage.stored_problem(data) is None
    assert storage.stored_problem(manifest) is None
    assert stored_files(graph_files_root) == sorted(
        [data.storage_path, manifest.storage_path]
    )
    assert f"Registered release {release.pk}" in out
    assert f"SHA-256 {data.sha256}" in out
    assert f"graph_import --release {release.pk} --dry-run" in out
    # The next steps: approve it, dry-run it, then commit it.
    assert out.splitlines()[-2:] == [
        "Record its licence snapshot and due diligence in the admin, and its "
        "internal approval with graph_approve unless it is synthetic, then dry-run "
        f"it: graph_import --release {release.pk} --dry-run --as <username>.",
        "Then commit it, with the options of its dry run: graph_import --release "
        f"{release.pk} --commit --as <username>.",
    ]


def test_every_option_is_stored(dataset, staff, files):
    corrected = DatasetReleaseFactory(dataset=dataset, version="v0")
    TaxonFactory(taxid=7227, scientific_name="Drosophila melanogaster")
    register(
        dataset,
        staff,
        files,
        "--source-url",
        "https://example.org/release.tsv",
        "--release-date",
        "2026-10-01",
        "--supersedes",
        "v0",
        "--taxon",
        "9606",
        "7227",
        "--taxon",
        "9606",
        "--embargo-until",
        "2027-01-01T00:00:00+00:00",
        "--no-raw-rows",
    )
    release = release_of(dataset)
    assert release.source_url == "https://example.org/release.tsv"
    assert release.release_date == datetime.date(2026, 10, 1)
    assert release.supersedes == corrected
    assert sorted(release.declared_taxa.values_list("taxid", flat=True)) == [
        7227,
        9606,
    ]
    assert release.embargo_until == datetime.datetime(
        2027, 1, 1, tzinfo=datetime.timezone.utc
    )
    assert release.store_raw_rows is False and release.is_synthetic is False


def test_a_synthetic_release_is_flagged(dataset, staff, files):
    files.manifest.write_bytes(manifest_bytes(True))
    register(dataset, staff, files, "--synthetic")
    assert release_of(dataset).is_synthetic is True


def test_a_release_without_a_manifest_has_only_its_data_file(dataset, staff, files):
    register(dataset, staff, files, manifest=False)
    assert list(release_of(dataset).files.values_list("role", flat=True)) == ["data"]


# ── Refusals before any file is read ────────────────────────────────────────


def test_nothing_is_read_or_written_while_the_switch_is_off(
    dataset, staff, files, settings, graph_files_root
):
    settings.TYPED_GRAPH_ENABLED = False
    files.data.unlink()
    with pytest.raises(CommandError, match="TYPED_GRAPH_ENABLED"):
        register(dataset, staff, files)
    assert not DatasetRelease.objects.exists()
    assert stored_files(graph_files_root) == []


@pytest.mark.parametrize(
    "change,message",
    [
        ({"--dataset": "nowhere"}, "Unknown dataset 'nowhere'"),
        ({"--version": "v 1"}, "A release version is the source's label"),
        ({"--version": ".v1"}, "A release version is the source's label"),
        ({"--adapter": "mitab_typed"}, "Unknown adapter 'mitab_typed'"),
        ({"--supersedes": "v9"}, "--supersedes 'v9' names no release"),
        ({"--taxon": "123456789"}, "Taxon 123456789 is not in the registry"),
        ({"--source-url": "ftp://example.org/x"}, "is an http or https URL"),
        ({"--release-date": "1 October 2026"}, "--release-date is a date"),
        ({"--embargo-until": "2027-01-01T00:00:00"}, "with its UTC offset"),
        ({"--embargo-until": "tomorrow"}, "with its UTC offset"),
        ({"--as": "nobody"}, "names no account"),
        ({"--file": "missing.tsv"}, "The data file: .* cannot be read"),
    ],
)
def test_a_registration_is_refused_and_leaves_nothing(
    dataset, staff, files, graph_files_root, change, message
):
    args = {
        "--dataset": dataset.slug,
        "--version": "v1",
        "--adapter": SPEC.name,
        "--file": str(files.data),
        "--manifest": str(files.manifest),
        "--as": staff.username,
    }
    for name, value in change.items():
        args[name] = str(files.folder / value) if name == "--file" else value
    with pytest.raises(CommandError, match=message):
        call_command(
            "graph_register_release",
            *[item for pair in args.items() for item in pair],
            stdout=io.StringIO(),
        )
    assert not DatasetRelease.objects.filter(dataset=dataset).exists()
    assert stored_files(graph_files_root) == []


@pytest.mark.parametrize(
    "account", [{"is_active": False}, {"is_staff": False}], ids=["inactive", "clerk"]
)
def test_an_account_that_is_not_active_staff_registers_nothing(
    dataset, files, graph_files_root, account
):
    user = StaffUserFactory(**account)
    with pytest.raises(CommandError, match="must be an active staff account"):
        register(dataset, user, files)
    assert not DatasetRelease.objects.exists()
    assert stored_files(graph_files_root) == []


def test_a_correction_keeps_the_synthetic_flag(dataset, staff, files, graph_files_root):
    DatasetReleaseFactory(dataset=dataset, version="v0", is_synthetic=True)
    with pytest.raises(CommandError, match="keeps the synthetic flag"):
        register(dataset, staff, files, "--supersedes", "v0")
    assert not DatasetRelease.objects.filter(version="v1").exists()
    assert stored_files(graph_files_root) == []


@pytest.mark.parametrize(
    "content,message",
    [
        (b"{", "is not strict JSON"),
        (manifest_bytes(True), "any other release's none"),
        (b'{"terms": {"GO:0000001": "x"}}', "not a CURIE of an ontology"),
    ],
)
def test_an_invalid_manifest_is_refused_before_anything_is_placed(
    dataset, staff, files, graph_files_root, content, message
):
    files.manifest.write_bytes(content)
    with pytest.raises(CommandError, match=message):
        register(dataset, staff, files)
    assert not DatasetRelease.objects.exists()
    assert stored_files(graph_files_root) == []


def test_files_over_their_limits_are_refused(
    dataset, staff, files, graph_files_root, monkeypatch
):
    monkeypatch.setattr(limits, "MAX_JSON_BYTES", 10)
    with pytest.raises(CommandError, match="The manifest: .* more than the 10"):
        register(dataset, staff, files)
    monkeypatch.setattr(limits, "MAX_FILE_BYTES", 10)
    with pytest.raises(CommandError, match="The data file: .* more than the 10"):
        register(dataset, staff, files, manifest=False)
    assert not DatasetRelease.objects.exists()
    assert stored_files(graph_files_root) == []


def test_a_failure_while_placing_leaves_no_row_and_no_file(
    dataset, staff, files, graph_files_root, monkeypatch
):
    moved = []
    move = storage._move_into_place

    def failing(source, relative):
        moved.append(relative)
        if len(moved) == 2:
            raise OSError(28, "No space left on device")
        return move(source, relative)

    monkeypatch.setattr(storage, "_move_into_place", failing)
    with pytest.raises(CommandError, match="was not registered: .*No space left"):
        register(dataset, staff, files)
    assert len(moved) == 2
    assert not DatasetRelease.objects.exists()
    assert not SourceFile.objects.exists()
    assert stored_files(graph_files_root) == []


# ── A release registered already ───────────────────────────────────────────


def test_registering_the_same_files_and_options_again_writes_nothing(
    dataset, staff, files, graph_files_root
):
    register(dataset, staff, files, "--taxon", "9606")
    release = release_of(dataset)
    before = (stored_files(graph_files_root), release.updated_at)
    out = register(dataset, staff, files, "--taxon", "9606")
    assert "is already registered, with these files and options" in out
    release.refresh_from_db()
    assert (stored_files(graph_files_root), release.updated_at) == before
    assert DatasetRelease.objects.count() == 1 and SourceFile.objects.count() == 2


@pytest.mark.parametrize("change", ["lost", "tampered"])
def test_a_lost_copy_is_restored_from_the_same_file(
    dataset, staff, files, graph_files_root, change
):
    register(dataset, staff, files)
    release = release_of(dataset)
    data = release.files.get(role=FileRole.DATA)
    path = graph_files_root / data.storage_path
    if change == "lost":
        path.unlink()
    else:
        path.write_bytes(b"something else")
    # Whatever the release's status.
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])
    out = register(dataset, staff, files)
    assert "Restored the stored copy of the data file" in out
    assert storage.stored_problem(data) is None
    assert path.read_bytes() == DATA


@pytest.mark.parametrize(
    "extra,named",
    [
        (("--source-url", "https://example.org/x"), "--source-url"),
        (("--release-date", "2026-01-01"), "--release-date"),
        (("--taxon", "9606"), "--taxon"),
        (("--embargo-until", "2027-01-01T00:00:00+00:00"), "--embargo-until"),
        (("--no-raw-rows",), "--no-raw-rows"),
        (("--supersedes", "v0"), "--supersedes"),
    ],
)
def test_every_other_option_is_a_refused_difference(
    dataset, staff, files, graph_files_root, extra, named
):
    DatasetReleaseFactory(dataset=dataset, version="v0")
    register(dataset, staff, files)
    before = stored_files(graph_files_root)
    with pytest.raises(CommandError, match=f"registered already, and differs: {named}"):
        register(dataset, staff, files, *extra)
    assert stored_files(graph_files_root) == before


def test_a_synthetic_flag_that_differs_is_refused(dataset, staff, files):
    register(dataset, staff, files, manifest=False)
    with pytest.raises(CommandError, match="--synthetic is False there, not True"):
        register(dataset, staff, files, "--synthetic", manifest=False)


@pytest.mark.parametrize("which", ["data", "manifest", "no manifest"])
def test_another_file_is_a_refused_difference(
    dataset, staff, files, graph_files_root, which
):
    register(dataset, staff, files)
    before = stored_files(graph_files_root)
    if which == "data":
        files.data = files.other
        expected = "its data file has SHA-256"
    elif which == "manifest":
        files.manifest.write_bytes(manifest_bytes(False, terms={}))
        expected = "its manifest has SHA-256"
    else:
        expected = "and the one given none"
    with pytest.raises(CommandError, match=expected) as refused:
        register(dataset, staff, files, manifest=which != "no manifest")
    assert "A known version with other content is a new release" in str(refused.value)
    assert stored_files(graph_files_root) == before


def test_another_adapter_is_a_refused_difference(dataset, staff, files, monkeypatch):
    register(dataset, staff, files)
    other = SimpleNamespace(spec=replace(SPEC, name="canonical_tsv_v2"))
    monkeypatch.setitem(adapters.ADAPTERS, "canonical_tsv_v2", other)
    with pytest.raises(CommandError, match="the adapter is 'canonical_tsv_v1' there"):
        call_command(
            "graph_register_release",
            "--dataset",
            dataset.slug,
            "--version",
            "v1",
            "--adapter",
            "canonical_tsv_v2",
            "--file",
            str(files.data),
            "--manifest",
            str(files.manifest),
            "--as",
            staff.username,
            stdout=io.StringIO(),
        )


# ── A new adapter version ───────────────────────────────────────────────────


def upgrade(monkeypatch, **changes):
    """The code's canonical_tsv_v1 at a new version and mapping version,
    from now on."""
    spec = replace(SPEC, **{"version": "1.1.0", "mapping_version": "2", **changes})
    monkeypatch.setitem(
        adapters.ADAPTERS,
        SPEC.name,
        SimpleNamespace(
            spec=spec,
            iter_records=ADAPTER.iter_records,
            map_record=ADAPTER.map_record,
            synthetic_issues=ADAPTER.synthetic_issues,
        ),
    )
    return spec


def dry_run_records(release):
    approved_for_internal_use(release)
    dry_run(release, triggered_by=StaffUserFactory())
    return SourceRecord.objects.filter(release=release).count()


def test_a_draft_without_evidence_takes_a_new_adapter_version(
    dataset, staff, files, monkeypatch, graph_files_root
):
    register(dataset, staff, files)
    release = release_of(dataset)
    assert dry_run_records(release) == 1
    before = stored_files(graph_files_root)
    upgrade(monkeypatch)
    out = register(dataset, staff, files)
    release.refresh_from_db()
    assert (release.adapter_version, release.mapping_version) == ("1.1.0", "2")
    assert "now reads with canonical_tsv_v1 1.1.0 (mapping 2, kind evidence)" in out
    assert "not 1.0.0 (mapping 1, kind evidence)" in out
    assert "Its 1 source records were deleted" in out
    assert not SourceRecord.objects.filter(release=release).exists()
    # The runs and files stay; the next dry run reads the records again.
    assert release.import_runs.count() == 1
    assert stored_files(graph_files_root) == before
    run = dry_run(release, triggered_by=StaffUserFactory())
    assert run.adapter_version == "1.1.0"
    assert SourceRecord.objects.filter(release=release).count() == 1


def test_an_upgrade_deletes_the_records_in_batches_without_reading_them(
    dataset, staff, tmp_path, monkeypatch
):
    folder = tmp_path / "five"
    rows = [{**GENE, "record_id": f"r{n}"} for n in range(5)]
    files = SimpleNamespace(
        data=write(folder, "release.tsv", tsv(*rows)),
        manifest=write(folder, "manifest.json", manifest_bytes(False)),
    )
    register(dataset, staff, files)
    release = release_of(dataset)
    assert dry_run_records(release) == 5
    monkeypatch.setattr(limits, "BATCH_ROWS", 2)
    upgrade(monkeypatch)
    with CaptureQueriesContext(connection) as captured:
        out = register(dataset, staff, files)
    assert "Its 5 source records were deleted" in out
    assert not SourceRecord.objects.filter(release=release).exists()
    queries = [query["sql"] for query in captured]
    # No record's column map is read...
    assert not any('"graph_sourcerecord"."raw"' in sql for sql in queries)
    # ... and no list of ids is longer than a batch.
    for sql in queries:
        if "graph_sourcerecord" in sql or "graph_evidenceassertion" in sql:
            for ids in re.findall(r" IN \(([^()]*)\)", sql):
                assert ids.count(",") + 1 <= 2, sql


def test_a_draft_whose_records_hold_evidence_keeps_its_adapter(
    dataset, staff, files, monkeypatch
):
    register(dataset, staff, files)
    release = release_of(dataset)
    tp53 = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    mdm2 = create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973")
    dry_run_records(release)
    commit = ImportRunFactory(
        release=release,
        file=release.files.get(role=FileRole.DATA),
        mode=ImportMode.COMMIT,
    )
    record_claims(
        SourceRecord.objects.get(release=release),
        [
            Claim(
                relationship_type="transcriptional_regulation",
                subject=Endpoint(tp53.pk, "HGNC:11998", "primary"),
                object=Endpoint(mdm2.pk, "HGNC:6973", "primary"),
                outcome="supported",
                evidence_basis="experimental",
                assertion_method="manual_assertion",
            )
        ],
        import_run=commit,
    )
    commit.state = ImportState.COMPLETED
    commit.save(update_fields=["state"])
    upgrade(monkeypatch)
    with pytest.raises(
        CommandError, match="evidence is recorded from its records"
    ) as raised:
        register(dataset, staff, files)
    # The discard returns it to a draft without evidence, which registers.
    assert str(raised.value).endswith(f"rests on that mapping. {DISCARD_FIRST}")
    release.refresh_from_db()
    assert release.adapter_version == SPEC.version
    assert SourceRecord.objects.filter(release=release).count() == 1


def test_a_release_past_draft_keeps_its_adapter(dataset, staff, files, monkeypatch):
    register(dataset, staff, files)
    release = release_of(dataset)
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])
    upgrade(monkeypatch)
    with pytest.raises(
        CommandError, match="it is staged, and only a draft is read anew"
    ) as raised:
        register(dataset, staff, files)
    assert str(raised.value).endswith(f"only a draft is read anew. {DISCARD_FIRST}")
    release.refresh_from_db()
    assert release.adapter_version == SPEC.version


def staged(release):
    approved_for_internal_use(release)
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])


@pytest.mark.parametrize(
    "status",
    [ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED, ReleaseStatus.WITHDRAWN],
)
def test_a_published_or_withdrawn_release_takes_a_new_version(
    dataset, staff, files, monkeypatch, status
):
    register(dataset, staff, files)
    release = release_of(dataset)
    staged(release)
    if status == ReleaseStatus.WITHDRAWN:
        withdraw_release(release, reason="Test.", withdrawn_by=StaffUserFactory())
    else:
        publish_release(release, published_by=StaffUserFactory())
    if status == ReleaseStatus.SUPERSEDED:
        register(dataset, staff, files, "--supersedes", "v1", version="v2")
        correction = release_of(dataset, "v2")
        staged(correction)
        publish_release(correction, published_by=StaffUserFactory())
    release.refresh_from_db()
    assert release.status == status
    upgrade(monkeypatch)
    with pytest.raises(CommandError) as raised:
        register(dataset, staff, files)
    # A discard would not help: such a release is never a draft again.
    assert str(raised.value).endswith(
        f"; it is {status}, and only a draft is read anew. Register it under a new "
        "version instead. Nothing was written."
    )


def test_an_open_commit_run_keeps_the_adapter(dataset, staff, files, monkeypatch):
    register(dataset, staff, files)
    release = release_of(dataset)
    ImportRunFactory(
        release=release,
        file=release.files.get(role=FileRole.DATA),
        mode=ImportMode.COMMIT,
    )
    upgrade(monkeypatch)
    with pytest.raises(CommandError, match="a commit run of it is open") as raised:
        register(dataset, staff, files)
    assert str(raised.value).endswith(f"; a commit run of it is open. {DISCARD_FIRST}")


def test_the_kind_changes_only_while_no_entity_names_the_release(
    dataset, staff, files, monkeypatch
):
    register(dataset, staff, files)
    release = release_of(dataset)
    approved_for_internal_use(release)
    create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998", release=release)
    upgrade(monkeypatch, kind=ReleaseKind.REFERENCE.value)
    with pytest.raises(CommandError, match="its kind stays evidence") as raised:
        register(dataset, staff, files)
    # A discard would not help: the entities stay.
    assert str(raised.value).endswith(
        "since entities name it as the release that created them. Register it under "
        "a new version instead. Nothing was written."
    )
    Entity.objects.filter(created_by_release=release).delete()
    out = register(dataset, staff, files)
    assert "kind reference" in out
    release.refresh_from_db()
    assert release.kind == ReleaseKind.REFERENCE
