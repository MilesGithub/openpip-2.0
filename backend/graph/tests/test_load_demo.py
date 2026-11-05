"""The synthetic demonstration's loader (G6; graph.demo.load_demo and
graph_load_demo): what it refuses before anything is written, a full load
and every figure of what it writes, a rerun that writes nothing, and the
operator policy of spec §3.6, row by row, each from a real interrupted or
operated load: a stop after v1 is published, before v2 is published, at a
refused snapshot or approval, in the middle of v1's commit (marked, given
up, unmarked or killed), and a discard; an operator's denial, revocation or
snapshot, before or after publication, or during a load; a withdrawal; a
public v1; the advice a refusal carries, followed to the end; a newer
adapter version and changed files; and the command's output.

A stop is made where it happens in production: graph.demo imports each
service by name, and a test replaces one with a wrapper that raises once,
for one version, and calls the service otherwise; a commit stops in
graph.ingest.commit.record_claims, for the record of a key found when it is
recorded, since no record exists before the load. Every load reads the
shipped files (graph.demo.DEMO_DIR) and stores its copies under the test's
own GRAPH_FILES_ROOT. The summaries of what a load writes are
test_demo_matrix's."""

import hashlib
import io
import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import InterfaceError, connection
from django.test import RequestFactory
from django.utils import timezone

from graph import demo, storage
from graph import releases as releases_module
from graph.approvals import (
    INTERNAL,
    PUBLIC_VIEW,
    ApprovalError,
    effective_scopes,
    record_decision,
    revoke,
)
from graph.demo import (
    DATASET_SLUG,
    DEMO_DIR,
    DEMO_LICENCE,
    DEMO_RATIONALE,
    SOURCE_SLUG,
    DemoError,
    load_demo,
)
from graph.entities import add_identifier
from graph.ingest import commit as commit_module
from graph.ingest import dry_run as dry_run_module
from graph.ingest import limits
from graph.ingest import registry as adapters
from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.commit import CommitError, CommitStopped, commit_release
from graph.ingest.dry_run import DryRunError, dry_run
from graph.ingest.mapping import RunStopped
from graph.ingest.registration import RegistrationError
from graph.licensing import LicensingError, record_licence
from graph.models import (
    DatasetRelease,
    DataSource,
    DataSourceKind,
    Decision,
    Entity,
    EntityStatus,
    EvidencePublication,
    FileRole,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    Publication,
    Relationship,
    ReleaseKind,
    ReleaseLicence,
    ReleaseStatus,
    SourceDataset,
    SourceRecord,
    Term,
    UsageApproval,
)
from graph.registry import REGISTRY_VERSION
from graph.releases import ReleaseError, discard_release, withdraw_release
from graph.stats import release_stats
from graph.tests.factories import (
    DatasetReleaseFactory,
    DataSourceFactory,
    SourceDatasetFactory,
    StaffUserFactory,
)
from graph.tests.ingest_helpers import natural_rows
from graph.visibility import Purpose, visible_release_ids
from tests.compat.test_legacy_contracts import legacy_row_counts

DRAFT, STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN = (
    ReleaseStatus.DRAFT,
    ReleaseStatus.STAGED,
    ReleaseStatus.PUBLISHED,
    ReleaseStatus.SUPERSEDED,
    ReleaseStatus.WITHDRAWN,
)
TR, PI = "transcriptional_regulation", "physical_interaction"
SIMULATED = "Simulated for the test. Nothing was written."

# What the lines say of each step.
CREATED = "Data source openpip-synthetic and dataset synthetic-regulation: created."
FOUND = "Data source openpip-synthetic and dataset synthetic-regulation: found."
V1_DRY = "17 mapped, 8 rejected, 1 unstored, verdict ok"
V2_DRY = "17 mapped, 0 rejected, 0 unstored, verdict ok"
ALL_CREATED = "created 13 entities, 14 relationships, 2 publications and 2 terms"
NONE_CREATED = "created 0 entities, 0 relationships, 0 publications and 0 terms"
LOADED_ALREADY = "The synthetic demonstration was loaded already: nothing was written."
NO_PROBLEM = "No problems, and nothing to review."

# Spec §2.2: the rows v1's dry run reports and never imports.
REJECTED = [
    (19, "syn-x1", ["bad_identifier"]),
    (20, "syn-x2", ["unknown_relationship_type"]),
    (21, "syn-x3", ["unknown_entity_type"]),
    (22, "syn-x4", ["claim_rule_7"]),
    (23, "syn-x5", ["claim_rule_8"]),
    (24, "syn-x6", ["claim_rule_3"]),
    (25, "syn-x7", ["negative_effect"]),
    (26, "syn-x8", ["conflicting_duplicate"]),
]
UNSTORED = [(27, "syn-x8", ["conflicting_duplicate"])]

# Spec §2.6: the entities v1 creates, and the labels that are not their
# identifier.
GENES = (
    "SYN-TF1",
    "SYN-TF2",
    "SYN-TF3",
    "SYN-G1",
    "SYN-G2",
    "SYN-G3",
    "SYN-G4",
    "SYN-G5",
    "SYN-G6",
)
LABELS = {"SYN-G3": "SYN-ALPHA", "SYN-G6": "SYN-SHARED", "SYN-P3": "SYN-SHARED"}
RELATIONSHIP_TYPES = {
    "encodes": {"assertions": 1, "relationships": 1},
    "isoform_of": {"assertions": 1, "relationships": 1},
    PI: {"assertions": 4, "relationships": 2},
    TR: {"assertions": 12, "relationships": 10},
}


@pytest.fixture
def alice(registry):
    """The account --as names, created before any count is taken (the
    legacy row counts count the user table)."""
    return StaffUserFactory(username="alice")


# ── Helpers ─────────────────────────────────────────────────────────────────


def stopped(user) -> DemoError:
    """The DemoError a load raises."""
    with pytest.raises(DemoError) as raised:
        load_demo(loaded_by=user)
    return raised.value


def once(monkeypatch, name, version, error):
    """graph.demo's service `name` raises `error` once, for the release of
    this version, and is called otherwise."""
    real = getattr(demo, name)
    raised = []

    def flaky(*args, **kwargs):
        stated = kwargs.get("version") or getattr(args[0], "version", None)
        if not raised and stated == version:
            raised.append(stated)
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(demo, name, flaky)


def stopping_at(monkeypatch, record_key):
    """commit.record_claims raises once, for v1's record of this key, after
    its batch's other writes, as test_commit.stopping does; the record is
    found by its key when it is recorded."""
    real = commit_module.record_claims
    raised = []

    def recording(source_record, claims, *, import_run):
        key = (
            SourceRecord.objects.filter(pk=source_record)
            .values_list("record_key", "release__version")
            .first()
        )
        if not raised and key == (record_key, "v1"):
            raised.append(key)
            raise ValueError("Stopped for the test.")
        return real(source_record, claims, import_run=import_run)

    monkeypatch.setattr(commit_module, "record_claims", recording)


def losing_the_marking(monkeypatch):
    """The save that marks a stopped commit run failed raises
    InterfaceError, as a lost connection does (test_commit's pattern)."""
    saving = ImportRun.save

    def connection_lost(self, *args, **kwargs):
        marking = kwargs.get("update_fields") == ["state", "error", "finished_at"]
        if marking and self.state == ImportState.FAILED:
            raise InterfaceError("connection already closed")
        return saving(self, *args, **kwargs)

    monkeypatch.setattr(ImportRun, "save", connection_lost)


def upgrade(monkeypatch, **changes):
    """The code's canonical_tsv_v1 at a new version and mapping version, or
    as `changes` say, from now on (test_register_release's helper)."""
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


def release_of(version) -> DatasetRelease:
    return DatasetRelease.objects.get(dataset__slug=DATASET_SLUG, version=version)


def named(release) -> str:
    return f"Release {release.pk} ({DATASET_SLUG} {release.version})"


def runs(release, mode) -> list:
    return list(ImportRun.objects.filter(release=release, mode=mode).order_by("pk"))


def snapshot(release) -> ReleaseLicence:
    return ReleaseLicence.objects.filter(release=release).order_by("-pk").first()


def internal(release) -> UsageApproval:
    return (
        UsageApproval.objects.filter(release=release, scope=INTERNAL)
        .order_by("-pk")
        .first()
    )


def counts() -> tuple:
    return (
        ImportRun.objects.count(),
        ReleaseLicence.objects.count(),
        UsageApproval.objects.count(),
    )


def loaded(v2, visitors=None) -> str:
    """The last line of a load that wrote, v2 current."""
    visitors = visitors or (
        "for staff only; visitors see it once a public_view approval is recorded "
        "with graph_approve"
    )
    return (
        f"The synthetic demonstration is loaded: release {v2.pk} (v2) is current, "
        f"{visitors}."
    )


def found(release) -> str:
    """The line of a published or superseded release a rerun leaves as it
    is, whose snapshot and approval are the load's."""
    status = (
        "published already"
        if release.status == PUBLISHED
        else f"superseded by release {release_of('v2').pk}"
    )
    return (
        f"{named(release)}: registered already; licence snapshot "
        f"{snapshot(release).pk} kept; internal approval {internal(release).pk} "
        f"kept; {status}."
    )


def loaded_whole(release, dry_line, created, published) -> str:
    """The line of a release one load registered, dry-ran, committed,
    licensed, approved and published."""
    [dry] = runs(release, ImportMode.DRY_RUN)
    [commit] = runs(release, ImportMode.COMMIT)
    return (
        f"{named(release)}: registered; dry run {dry.pk}: {dry_line}; committed by "
        f"run {commit.pk}: {created}; licence snapshot {snapshot(release).pk} "
        f"recorded; internal approval {internal(release).pk} recorded; {published}."
    )


def request_for(user):
    request = RequestFactory().get("/")
    request.user = AnonymousUser() if user is None else user
    return request


def checked() -> str:
    """graph_check's last line; it raises CommandError on a problem."""
    out = io.StringIO()
    call_command("graph_check", stdout=out)
    return out.getvalue().splitlines()[-1]


def digests() -> dict:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DEMO_DIR.iterdir())
    }


def demo_copy(tmp_path, **changes):
    """A copy of DEMO_DIR, each file named in `changes` passed through the
    function given for it."""
    copy = tmp_path / "demo"
    shutil.copytree(DEMO_DIR, copy)
    for name, change in changes.items():
        path = copy / name.replace("_tsv", ".tsv")
        path.write_bytes(change(path.read_bytes()))
    return copy


def report_lists(report) -> tuple:
    """A dry run's rejected and unstored lists: (line, key, codes)."""
    rejected = [
        (entry["line"], entry["key"], sorted({i["code"] for i in entry["issues"]}))
        for entry in report["rejected"]["entries"]
    ]
    unstored = [
        (entry["line"], entry["key"], entry["codes"])
        for entry in report["unstored"]["entries"]
    ]
    return rejected, unstored


def stats(rejected, created) -> dict:
    """Spec §2.6's release_stats of a release."""
    return {
        "version": 1,
        "records": {
            "mapped": 17,
            "unresolved": 0,
            "ambiguous": 0,
            "rejected": rejected,
            "skipped": 0,
        },
        "assertions": 18,
        "assertions_by_outcome": {
            "supported": 16,
            "tested_absent": 2,
            "inconclusive": 0,
            "not_significant": 0,
        },
        "relationships": 14,
        "entities": 13,
        "publications": 2,
        "entities_created": created,
        "relationship_types": RELATIONSHIP_TYPES,
    }


def entities() -> dict:
    """{identifier: the entity's type, label, status, taxon, creating
    release and identifiers}."""
    found = {}
    for entity in Entity.objects.prefetch_related("identifiers"):
        identifiers = [
            (i.namespace_id, i.value, i.role) for i in entity.identifiers.all()
        ]
        found[identifiers[0][1]] = (
            entity.entity_type_id,
            entity.label,
            entity.status,
            entity.taxon_id,
            entity.created_by_release_id,
            identifiers,
        )
    return found


def expected_entities(v1) -> dict:
    expected = {}
    for values, entity_type, namespace in (
        (GENES, "gene", "synthetic_gene"),
        (("SYN-P1", "SYN-P2", "SYN-P3"), "protein", "synthetic_protein"),
        (("SYN-P1-1",), "protein_isoform", "synthetic_protein"),
    ):
        for value in values:
            expected[value] = (
                entity_type,
                LABELS.get(value, value),
                EntityStatus.PROVISIONAL,
                32630,
                v1.pk,
                [(namespace, value, IdentifierRole.PRIMARY)],
            )
    return expected


def assert_loaded():
    """Spec §2.6's state, whatever stops and resumptions led to it, and
    graph_check finds nothing."""
    v1, v2 = release_of("v1"), release_of("v2")
    assert (v1.status, v2.status, v2.supersedes_id) == (SUPERSEDED, PUBLISHED, v1.pk)
    assert entities() == expected_entities(v1)
    by_type = {}
    for slug in Relationship.objects.values_list("relationship_type_id", flat=True):
        by_type[slug] = by_type.get(slug, 0) + 1
    assert by_type == {TR: 10, PI: 2, "encodes": 1, "isoform_of": 1}
    assert (Publication.objects.count(), Term.objects.count()) == (2, 2)
    assert (v1.stats, v2.stats) == (stats(8, 13), stats(0, 0))
    assert (release_stats(v1), release_stats(v2)) == (v1.stats, v2.stats)
    for release in (v1, v2):
        [licence] = ReleaseLicence.objects.filter(release=release)
        assert licence.records_terms
        [approval] = UsageApproval.objects.filter(release=release)
        assert (approval.scope, approval.decision, approval.revoked_at) == (
            INTERNAL,
            Decision.APPROVED,
            None,
        )
        assert approval.licence_id == licence.pk
        assert effective_scopes(release) == {INTERNAL}
    assert checked() == NO_PROBLEM


# ── Refused before anything is written ──────────────────────────────────────


def test_the_switch_off_refuses_before_anything_is_written(alice, settings):
    settings.TYPED_GRAPH_ENABLED = False
    rows = natural_rows()
    expected = (
        "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so the "
        "synthetic demonstration is not loaded. Nothing was written."
    )
    with pytest.raises(CommandError) as raised:
        call_command("graph_load_demo", "--as", "alice", stdout=io.StringIO())
    assert str(raised.value) == expected
    error = stopped(alice)
    assert (str(error), error.lines) == (expected, ())
    assert natural_rows() == rows


@pytest.mark.parametrize(
    "username, account, expected",
    [
        ("nobody", None, "--as 'nobody' names no account. Nothing was written."),
        (
            "former",
            {"is_active": False},
            "--as must be an active staff account, not 'former'. Nothing was written.",
        ),
        (
            "clerk",
            {"is_staff": False},
            "--as must be an active staff account, not 'clerk'. Nothing was written.",
        ),
    ],
)
def test_as_names_an_active_staff_account(registry, username, account, expected):
    if account is not None:
        StaffUserFactory(username=username, **account)
    rows = natural_rows()
    out = io.StringIO()
    with pytest.raises(CommandError) as raised:
        call_command("graph_load_demo", "--as", username, stdout=out)
    assert (str(raised.value), out.getvalue()) == (expected, "")
    if account is not None:
        user = get_user_model().objects.get(username=username)
        error = stopped(user)
        assert str(error) == (
            f"--as must be an active staff account, not {user!r}. Nothing was "
            "written."
        )
    assert natural_rows() == rows


def test_a_registry_not_synced_or_a_missing_file_refuses(
    switch_on, tmp_path, monkeypatch
):
    user = StaffUserFactory()
    rows = natural_rows()
    error = stopped(user)
    assert (str(error), error.lines) == (
        f"The database's registry is at version none, and this code's at "
        f"{REGISTRY_VERSION}: run graph_sync_registry first. Nothing was written.",
        (),
    )
    assert natural_rows() == rows
    call_command("graph_sync_registry", stdout=io.StringIO())
    rows = natural_rows()
    copy = demo_copy(tmp_path)
    (copy / "release_v2.tsv").unlink()
    monkeypatch.setattr(demo, "DEMO_DIR", copy)
    error = stopped(user)
    assert str(error) == (
        f"The synthetic demonstration's file release_v2.tsv is missing from {copy}: "
        "this installation is incomplete. Nothing was written."
    )
    assert natural_rows() == rows


def _source_of_another_kind():
    DataSourceFactory(slug=SOURCE_SLUG, kind=DataSourceKind.LAB)
    return (
        "Data source openpip-synthetic is of kind lab, not this_portal: its slug "
        "names data that are not the demonstration's. Nothing was written."
    )


def _dataset_of_another_source():
    dataset = SourceDatasetFactory(slug=DATASET_SLUG)
    return (
        f"Dataset synthetic-regulation is of data source {dataset.data_source.slug}, "
        "not openpip-synthetic: its slug names data that are not the "
        "demonstration's. Nothing was written."
    )


def _dataset_with_a_real_release():
    source = DataSourceFactory(slug=SOURCE_SLUG, kind=DataSourceKind.THIS_PORTAL)
    dataset = SourceDatasetFactory(slug=DATASET_SLUG, data_source=source)
    release = DatasetReleaseFactory(dataset=dataset, version="v1")
    return (
        f"Dataset synthetic-regulation holds release {release.pk} (v1), which is "
        "not synthetic: its slug names data that are not the demonstration's. "
        "Nothing was written."
    )


@pytest.mark.parametrize(
    "taken",
    [_source_of_another_kind, _dataset_of_another_source, _dataset_with_a_real_release],
)
def test_a_slug_that_other_data_hold_refuses(alice, taken):
    expected = taken()
    rows = natural_rows()
    error = stopped(alice)
    assert (str(error), error.lines) == (expected, ())
    assert natural_rows() == rows
    assert not DatasetRelease.objects.filter(is_synthetic=True).exists()


# ── A full load ─────────────────────────────────────────────────────────────


def test_a_full_load_writes_the_demonstration(alice):
    staff, visitor = request_for(alice), request_for(None)
    legacy = legacy_row_counts()
    files = digests()
    before = timezone.localdate()
    out = io.StringIO()
    call_command("graph_load_demo", "--as", "alice", stdout=out)
    after = timezone.localdate()
    v1, v2 = release_of("v1"), release_of("v2")
    [dry1], [commit1] = runs(v1, ImportMode.DRY_RUN), runs(v1, ImportMode.COMMIT)
    [dry2], [commit2] = runs(v2, ImportMode.DRY_RUN), runs(v2, ImportMode.COMMIT)
    [l1] = ReleaseLicence.objects.filter(release=v1)
    [l2] = ReleaseLicence.objects.filter(release=v2)
    [a1] = UsageApproval.objects.filter(release=v1)
    [a2] = UsageApproval.objects.filter(release=v2)
    # What it printed.
    assert out.getvalue().splitlines() == [
        CREATED,
        f"Release {v1.pk} (synthetic-regulation v1): registered; dry run {dry1.pk}: "
        f"17 mapped, 8 rejected, 1 unstored, verdict ok; committed by run "
        f"{commit1.pk}: created 13 entities, 14 relationships, 2 publications and 2 "
        f"terms; licence snapshot {l1.pk} recorded; internal approval {a1.pk} "
        "recorded; published.",
        f"Release {v2.pk} (synthetic-regulation v2): registered; dry run {dry2.pk}: "
        f"17 mapped, 0 rejected, 0 unstored, verdict ok; committed by run "
        f"{commit2.pk}: created 0 entities, 0 relationships, 0 publications and 0 "
        f"terms; licence snapshot {l2.pk} recorded; internal approval {a2.pk} "
        f"recorded; published, superseding release {v1.pk}.",
        f"The synthetic demonstration is loaded: release {v2.pk} (v2) is current, "
        "for staff only; visitors see it once a public_view approval is recorded "
        "with graph_approve.",
    ]
    # The catalogue.
    source = DataSource.objects.get(slug=SOURCE_SLUG)
    dataset = SourceDataset.objects.get(slug=DATASET_SLUG)
    assert (source.kind, source.name) == (
        DataSourceKind.THIS_PORTAL,
        "openPIP synthetic demonstration",
    )
    assert (dataset.data_source_id, dataset.title, dataset.short_title) == (
        source.pk,
        "Synthetic regulation example",
        "Synthetic regulation",
    )
    assert dataset.status == "active"
    # The releases.
    assert (v1.status, v2.status, v2.supersedes_id) == (SUPERSEDED, PUBLISHED, v1.pk)
    for release in (v1, v2):
        assert (release.is_synthetic, release.adapter, release.adapter_version) == (
            True,
            SPEC.name,
            SPEC.version,
        )
        assert list(release.release_taxa.values_list("taxon_id", flat=True)) == [32630]
        assert release.published_by_username == "alice"
    # The runs: one dry run and one commit each, with the loader's options.
    resolutions = {"entries": [], "sha256": ""}
    for dry, commit in ((dry1, commit1), (dry2, commit2)):
        assert dry.parameters == {
            "create_entities": True,
            "registry_version": REGISTRY_VERSION,
            "resolutions": resolutions,
        }
        assert commit.parameters == {**dry.parameters, "dry_run": dry.pk}
        assert (dry.state, commit.state) == (ImportState.COMPLETED,) * 2
        assert (dry.triggered_by_username, commit.triggered_by_username) == (
            "alice",
        ) * 2
    # The dry runs' reports.
    report1, report2 = dry1.report, dry2.report
    assert (report1["verdict"], report2["verdict"]) == ("ok", "ok")
    assert report1["counts"]["records"] == {
        "mapped": 17,
        "unresolved": 0,
        "ambiguous": 0,
        "rejected": 8,
        "skipped": 0,
    }
    assert report2["counts"]["records"] == {
        "mapped": 17,
        "unresolved": 0,
        "ambiguous": 0,
        "rejected": 0,
        "skipped": 0,
    }
    assert (report1["counts"]["unstored"], report2["counts"]["unstored"]) == (1, 0)
    assert report_lists(report1) == (REJECTED, UNSTORED)
    assert report_lists(report2) == ([], [])
    assert report1["conflicts"]["total"] == 0
    # v2's conflicts with v1, by relationship: each entity has one identifier.
    names = dict(Entity.objects.values_list("pk", "identifiers__value"))
    conflicts = {
        (
            entry["relationship"]["type"],
            names[entry["relationship"]["subject"]],
            names[entry["relationship"]["object"]],
        ): (
            entry["kinds"],
            entry["this"]["lines"],
            entry["other_release"],
            entry["corrected_by_this_release"],
        )
        for entry in report2["conflicts"]["entries"]
    }
    assert report2["conflicts"]["total"] == 3
    assert conflicts == {
        (TR, "SYN-TF1", "SYN-G1"): (["effect"], [2], v1.pk, True),
        (TR, "SYN-TF2", "SYN-G2"): (["effect"], [4, 5], v1.pk, True),
        (PI, "SYN-P2", "SYN-P3"): (["outcome"], [17, 18], v1.pk, True),
    }
    # The commits' reports.
    for commit, created in (
        (commit1, {"entities": 13, "relationships": 14, "publications": 2, "terms": 2}),
        (commit2, {"entities": 0, "relationships": 0, "publications": 0, "terms": 0}),
    ):
        counted = commit.report["counts"]
        assert (counted["created"], counted["recorded"], counted["assertions"]) == (
            created,
            17,
            18,
        )
        assert commit.report["resumed"] == []
    # What the releases hold.
    assert entities() == expected_entities(v1)
    by_type = {}
    for slug in Relationship.objects.values_list("relationship_type_id", flat=True):
        by_type[slug] = by_type.get(slug, 0) + 1
    assert by_type == {TR: 10, PI: 2, "encodes": 1, "isoform_of": 1}
    assert (v1.stats, v2.stats) == (stats(8, 13), stats(0, 0))
    assert (release_stats(v1), release_stats(v2)) == (v1.stats, v2.stats)
    papers = list(
        Publication.objects.order_by("local_key").values_list(
            "pk",
            "local_key",
            "local_dataset_id",
            "is_synthetic",
            "year",
            "pmid",
            "doi",
            "pmcid",
            "imex_id",
        )
    )
    assert [paper[1:] for paper in papers] == [
        ("syn-paper-1", dataset.pk, True, 2026, "", "", "", ""),
        ("syn-paper-2", dataset.pk, True, 2026, "", "", "", ""),
    ]
    for release in (v1, v2):
        cited = EvidencePublication.objects.filter(release=release)
        assert set(cited.values_list("publication_id", flat=True)) == {
            paper[0] for paper in papers
        }
    assert sorted(Term.objects.values_list("curie", "label", "label_release_id")) == [
        ("MI:0407", "direct interaction", v1.pk),
        ("MI:0915", "physical association", v1.pk),
    ]
    # One snapshot and one internal approval each, the loader's; no public
    # scope.
    for release, licence, approval in ((v1, l1, a1), (v2, l2, a2)):
        assert {name: getattr(licence, name) for name in DEMO_LICENCE} == DEMO_LICENCE
        assert before <= licence.read_on <= after
        assert (licence.reviewer, licence.recorded_by_username) == ("alice", "alice")
        assert licence.records_terms
        assert (
            approval.scope,
            approval.decision,
            approval.licence_id,
            approval.rationale,
            approval.decided_by_username,
            approval.revoked_at,
        ) == (INTERNAL, Decision.APPROVED, licence.pk, DEMO_RATIONALE, "alice", None)
        assert effective_scopes(release) == {INTERNAL}
    # Staff see v2 by default and v1 when they select it; visitors see
    # nothing, by default or pinned, to view or to download.
    assert visible_release_ids(staff, Purpose.VIEW).ids == {v2.pk}
    pinned = visible_release_ids(staff, Purpose.VIEW, pinned=[v1.pk, v2.pk])
    assert (pinned.ids, pinned.superseded) == ({v1.pk, v2.pk}, {v1.pk})
    for purpose in (Purpose.VIEW, Purpose.BULK):
        assert visible_release_ids(visitor, purpose).ids == frozenset()
        assert (
            visible_release_ids(visitor, purpose, pinned=[v1.pk, v2.pk]).ids
            == frozenset()
        )
    # Nothing else changed.
    assert checked() == NO_PROBLEM
    assert legacy_row_counts() == legacy
    assert digests() == files


def test_a_rerun_writes_nothing(alice, graph_files_root):
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    assert result.wrote is True
    assert result.releases == {"v1": v1, "v2": v2}
    assert (result.releases["v1"].status, result.releases["v2"].status) == (
        SUPERSEDED,
        PUBLISHED,
    )
    rows, counted = natural_rows(), counts()
    stored = sorted(path for path in graph_files_root.rglob("*"))
    again = load_demo(loaded_by=alice)
    assert again.wrote is False
    assert again.lines == (
        FOUND,
        f"{named(v1)}: registered already; licence snapshot {snapshot(v1).pk} kept; "
        f"internal approval {internal(v1).pk} kept; superseded by release {v2.pk}.",
        f"{named(v2)}: registered already; licence snapshot {snapshot(v2).pk} kept; "
        f"internal approval {internal(v2).pk} kept; published already.",
        LOADED_ALREADY,
    )
    assert again.releases == {"v1": v1, "v2": v2}
    assert (natural_rows(), counts()) == (rows, counted)
    assert sorted(path for path in graph_files_root.rglob("*")) == stored
    assert checked() == NO_PROBLEM


def test_a_lost_stored_copy_is_restored(alice):
    load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    data = v1.files.get(role=FileRole.DATA)
    (storage.root() / data.storage_path).unlink()
    rows = natural_rows()
    again = load_demo(loaded_by=alice)
    assert again.wrote is True
    assert again.lines[1] == (
        f"{named(v1)}: registered already, restored the stored data file; licence "
        f"snapshot {snapshot(v1).pk} kept; internal approval {internal(v1).pk} kept; "
        f"superseded by release {v2.pk}."
    )
    assert again.lines[3] == loaded(v2)
    assert natural_rows() == rows
    assert checked() == NO_PROBLEM


# ── A load that stopped, completed ──────────────────────────────────────────


def test_a_stop_after_v1_is_published_is_completed(alice, monkeypatch):
    once(monkeypatch, "register_release", "v2", RegistrationError(SIMULATED))
    error = stopped(alice)
    v1 = release_of("v1")
    assert str(error) == (
        "Release synthetic-regulation v2: registration was refused: Simulated for "
        "the test."
    )
    assert error.lines == (
        CREATED,
        loaded_whole(v1, V1_DRY, ALL_CREATED, "published"),
        "Release synthetic-regulation v2: stopped at registration.",
    )
    assert v1.status == PUBLISHED
    assert not DatasetRelease.objects.filter(version="v2").exists()
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    assert result.lines == (
        FOUND,
        f"{named(v1)}: registered already; licence snapshot {snapshot(v1).pk} kept; "
        f"internal approval {internal(v1).pk} kept; published already.",
        loaded_whole(
            v2, V2_DRY, NONE_CREATED, f"published, superseding release {v1.pk}"
        ),
        loaded(v2),
    )
    assert result.wrote is True
    assert_loaded()


def test_a_stop_before_v2_is_published_is_completed(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    error = stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    assert str(error) == (
        f"{named(v2)}: publishing was refused: Simulated for the test."
    )
    assert error.lines[-1] == (
        f"{named(v2)}: registered; dry run {runs(v2, ImportMode.DRY_RUN)[0].pk}: "
        f"{V2_DRY}; committed by run {runs(v2, ImportMode.COMMIT)[0].pk}: "
        f"{NONE_CREATED}; licence snapshot {snapshot(v2).pk} recorded; internal "
        f"approval {internal(v2).pk} recorded; stopped at publishing."
    )
    assert (v1.status, v2.status) == (PUBLISHED, STAGED)
    counted = counts()
    result = load_demo(loaded_by=alice)
    assert result.lines[2] == (
        f"{named(v2)}: registered already; committed already; licence snapshot "
        f"{snapshot(v2).pk} kept; internal approval {internal(v2).pk} kept; "
        f"published, superseding release {v1.pk}."
    )
    assert result.lines[3] == loaded(v2)
    assert counts() == counted
    assert_loaded()


def test_a_staged_release_records_what_a_refusal_left_missing(alice, monkeypatch):
    once(monkeypatch, "record_licence", "v1", LicensingError(SIMULATED))
    error = stopped(alice)
    v1 = release_of("v1")
    [dry], [run] = runs(v1, ImportMode.DRY_RUN), runs(v1, ImportMode.COMMIT)
    assert str(error) == (
        f"{named(v1)}: the licence snapshot was refused: Simulated for the test."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; committed by run "
        f"{run.pk}: {ALL_CREATED}; stopped at the licence snapshot."
    )
    assert (v1.status, snapshot(v1), internal(v1)) == (STAGED, None, None)
    once(monkeypatch, "record_decision", "v1", ApprovalError(SIMULATED))
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)}: the internal approval was refused: Simulated for the test."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered already; committed already; licence snapshot "
        f"{snapshot(v1).pk} recorded; stopped at the internal approval."
    )
    assert (release_of("v1").status, internal(v1)) == (STAGED, None)
    result = load_demo(loaded_by=alice)
    assert result.lines[1] == (
        f"{named(v1)}: registered already; committed already; licence snapshot "
        f"{snapshot(v1).pk} kept; internal approval {internal(v1).pk} recorded; "
        "published."
    )
    assert_loaded()


def test_a_discarded_v2_is_imported_again_and_published(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    licence, approval = snapshot(v2), internal(v2)
    discarded = discard_release(v2, reason="Test.", discarded_by=alice)
    assert (discarded.from_status, discarded.assertions, discarded.records) == (
        STAGED,
        18,
        17,
    )
    assert checked() == NO_PROBLEM
    (imports, licences, approvals) = counts()
    result = load_demo(loaded_by=alice)
    dry, commit = runs(v2, ImportMode.DRY_RUN)[-1], runs(v2, ImportMode.COMMIT)[-1]
    assert result.lines[2] == (
        f"{named(v2)}: registered already; dry run {dry.pk}: {V2_DRY}; committed by "
        f"run {commit.pk}: {NONE_CREATED}; licence snapshot {licence.pk} kept; "
        f"internal approval {approval.pk} kept; published, superseding release "
        f"{v1.pk}."
    )
    assert counts() == (imports + 2, licences, approvals)
    assert (snapshot(v2), internal(v2)) == (licence, approval)
    assert_loaded()


def test_a_commit_stopped_midway_is_resumed_without_a_second_dry_run(
    alice, monkeypatch
):
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    stopping_at(monkeypatch, "syn-14")
    error = stopped(alice)
    v1 = release_of("v1")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    [run] = runs(v1, ImportMode.COMMIT)
    assert str(error) == (
        f"The commit of release {v1.pk} stopped, and its run {run.pk} is marked "
        "failed: Lines 14 to 17: Stopped for the test. Run graph_load_demo again to "
        "resume it once the cause is fixed, or discard it with graph_discard_release."
    )
    assert error.lines == (
        CREATED,
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; stopped at the commit.",
    )
    assert (v1.status, run.state, run.checkpoint["line"]) == (
        DRAFT,
        ImportState.FAILED,
        13,
    )
    # Batches of four rows report what one batch of the whole file reports.
    assert report_lists(dry.report) == (REJECTED, UNSTORED)
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    assert runs(v1, ImportMode.DRY_RUN) == [dry]
    assert runs(v1, ImportMode.COMMIT) == [run]
    run.refresh_from_db()
    assert run.state == ImportState.COMPLETED
    [resumed] = run.report["resumed"]
    assert (resumed["by"], resumed["error"], resumed["line"]) == (
        "alice",
        "Lines 14 to 17: Stopped for the test.",
        13,
    )
    assert result.lines[1] == (
        f"{named(v1)}: registered already; committed by run {run.pk}, resuming it "
        f"after 1 stop: {ALL_CREATED}; licence snapshot {snapshot(v1).pk} recorded; "
        f"internal approval {internal(v1).pk} recorded; published."
    )
    assert result.lines[2] == loaded_whole(
        v2, V2_DRY, NONE_CREATED, f"published, superseding release {v1.pk}"
    )
    assert_loaded()


@pytest.mark.skipif(
    connection.vendor == "postgresql", reason="needs a database without the run lock"
)
def test_without_the_run_lock_a_running_commit_run_is_discarded_first(
    alice, monkeypatch
):
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    [run] = runs(v1, ImportMode.COMMIT)
    # As a killed process leaves it.
    run.state = ImportState.RUNNING
    run.save(update_fields=["state"])
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)}: the commit was refused: Release {v1.pk} has a commit run "
        f"{run.pk} in state running, and this database cannot tell whether its "
        "process is alive (the run lock needs PostgreSQL). If no process runs it, "
        "discard it with graph_discard_release."
    )
    assert error.lines[-1] == f"{named(v1)}: registered already; stopped at the commit."


@pytest.mark.postgres
def test_a_killed_commit_is_resumed_without_a_second_dry_run(alice, monkeypatch):
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    [run] = runs(v1, ImportMode.COMMIT)
    # As a killed process leaves it: the commit finds no process holding the
    # run lock, end_dead_runs marks the run failed, and the commit resumes it.
    run.state = ImportState.RUNNING
    run.save(update_fields=["state"])
    result = load_demo(loaded_by=alice)
    assert runs(v1, ImportMode.DRY_RUN) == [dry]
    assert runs(v1, ImportMode.COMMIT) == [run]
    run.refresh_from_db()
    [resumed] = run.report["resumed"]
    assert (run.state, resumed["error"]) == (
        ImportState.COMPLETED,
        "Its process ended before the run finished; found when a commit of "
        f"release {v1.pk} started.",
    )
    assert result.lines[1] == (
        f"{named(v1)}: registered already; committed by run {run.pk}, resuming it "
        f"after 1 stop: {ALL_CREATED}; licence snapshot {snapshot(v1).pk} recorded; "
        f"internal approval {internal(v1).pk} recorded; published."
    )
    assert_loaded()


def test_a_commit_run_a_discard_gave_up_meanwhile_stops_the_load(alice, monkeypatch):
    # Before its second batch, v1's run is abandoned, as a discard on a
    # database without the run lock leaves it (test_commit's simulation).
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    real, calls = commit_module.plan_batch, []

    def planning(records, *, run_ctx, check=True):
        if not check:
            calls.append(1)
            if len(calls) == 2:
                ImportRun.objects.filter(
                    release__dataset__slug=DATASET_SLUG,
                    release__version="v1",
                    mode=ImportMode.COMMIT,
                    state=ImportState.RUNNING,
                ).update(state=ImportState.ABANDONED)
        return real(records, run_ctx=run_ctx, check=check)

    monkeypatch.setattr(commit_module, "plan_batch", planning)
    error = stopped(alice)
    v1 = release_of("v1")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    [run] = runs(v1, ImportMode.COMMIT)
    assert str(error) == (
        f"The commit of release {v1.pk} stopped, and its run {run.pk} was given up "
        f"by a discard meanwhile: Run {run.pk} of release {v1.pk} is abandoned now: "
        "a discard gave it up. Run graph_load_demo again, which dry-runs and "
        "commits it."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; stopped at the commit."
    )
    assert run.state == ImportState.ABANDONED


@pytest.mark.postgres
def test_a_commit_run_left_unmarked_by_a_lost_connection_is_resumed(alice, monkeypatch):
    stopping_at(monkeypatch, "syn-14")
    losing_the_marking(monkeypatch)
    error = stopped(alice)
    v1 = release_of("v1")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    [run] = runs(v1, ImportMode.COMMIT)
    assert str(error) == (
        f"The commit of release {v1.pk} stopped, and its run {run.pk} could not be "
        "marked failed, since the database connection was lost: Lines 2 to 27: "
        "Stopped for the test. The next commit or discard of the release marks it. "
        "Run graph_load_demo again to resume it."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; stopped at the commit."
    )
    assert run.state == ImportState.RUNNING
    monkeypatch.undo()
    load_demo(loaded_by=alice)
    assert runs(release_of("v1"), ImportMode.DRY_RUN) == [dry]
    assert_loaded()


def test_without_the_run_lock_a_commit_run_left_unmarked_is_discarded_first(
    alice, monkeypatch
):
    # As a database without the run lock has it (patched where the test
    # database has the lock): nothing marks the run, and a commit never
    # resumes a running one.
    for module in (commit_module, demo):
        monkeypatch.setattr(module, "runs_are_locked", lambda using=None: False)
    for module in (commit_module, releases_module):
        monkeypatch.setattr(module, "end_dead_runs", lambda release_id, **kwargs: [])
    stopping_at(monkeypatch, "syn-14")
    losing_the_marking(monkeypatch)
    error = stopped(alice)
    v1 = release_of("v1")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    [run] = runs(v1, ImportMode.COMMIT)
    assert str(error) == (
        f"The commit of release {v1.pk} stopped, and its run {run.pk} could not be "
        "marked failed: Lines 2 to 27: Stopped for the test. It stays running, and "
        "on this database, which has no run lock, a commit never resumes a running "
        "run: discard it with graph_discard_release, then run graph_load_demo again."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; stopped at the commit."
    )
    assert run.state == ImportState.RUNNING
    # The advice, followed.
    discard_release(v1, reason="Test.", discarded_by=alice)
    load_demo(loaded_by=alice)
    assert_loaded()


def test_a_stopped_dry_run_is_run_again(alice, monkeypatch):
    real = dry_run_module._read_and_write
    raised = []

    def stopping(*args, **kwargs):
        if not raised:
            raised.append(1)
            raise RunStopped("The release changed under the run, for the test.")
        return real(*args, **kwargs)

    monkeypatch.setattr(dry_run_module, "_read_and_write", stopping)
    error = stopped(alice)
    v1 = release_of("v1")
    [failed] = runs(v1, ImportMode.DRY_RUN)
    assert str(error) == (
        f"The dry run of release {v1.pk} stopped, and its run is marked failed: The "
        "release changed under the run, for the test. Running graph_load_demo again "
        "dry-runs it again."
    )
    assert error.lines[-1] == f"{named(v1)}: registered; stopped at the dry run."
    assert failed.state == ImportState.FAILED
    load_demo(loaded_by=alice)
    assert len(runs(release_of("v1"), ImportMode.DRY_RUN)) == 2
    assert_loaded()


def test_files_that_break_a_rule_stop_at_the_dry_run(alice, monkeypatch, tmp_path):
    # Line 2's subject label without SYN-: a synthetic violation.
    copy = demo_copy(
        tmp_path,
        release_v1_tsv=lambda data: data.replace(
            b"\tSYN-TF1\tgene\t32630\tSYN-TF1\t", b"\tSYN-TF1\tgene\t32630\tTF1\t", 1
        ),
    )
    monkeypatch.setattr(demo, "DEMO_DIR", copy)
    error = stopped(alice)
    v1 = release_of("v1")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    records = dry.report["counts"]["records"]
    assert (dry.report["verdict"], records["mapped"], records["rejected"]) == (
        "refused",
        16,
        9,
    )
    assert str(error) == (
        f"{named(v1)}: dry run {dry.pk} has the verdict refused: the demonstration's "
        "files break a rule, which the run's report names."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered; dry run {dry.pk}: 16 mapped, 9 rejected, 1 "
        "unstored, verdict refused; stopped at the dry run."
    )
    assert not runs(v1, ImportMode.COMMIT)


# ── An operator's decisions ─────────────────────────────────────────────────


def test_a_denial_and_a_revoked_denial_refuse_a_draft_before_any_write(
    alice, monkeypatch
):
    once(monkeypatch, "dry_run", "v1", DryRunError(4, SIMULATED))
    stopped(alice)
    v1 = release_of("v1")
    denial = record_decision(
        v1, INTERNAL, Decision.DENIED, rationale="Not here.", decided_by=alice
    )
    # v1 has no licence snapshot yet, and graph_approve refuses an approval
    # without one: the advice names the admin step that records it first.
    terms_first = (
        "record the release's terms in the admin (Release licences, then Add), "
        "then an approval with graph_approve"
    )
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)} has an active internal denial, decision {denial.pk}: revoke it "
        f"with graph_revoke --approval {denial.pk}, then {terms_first}, then run "
        "graph_load_demo again."
    )
    assert error.lines == (FOUND, f"{named(v1)}: stopped at the internal approval.")
    assert (natural_rows(), counts()) == (rows, counted)
    revoke(denial, reason="Test.", revoked_by=alice)
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)} has no active internal decision (internal denial {denial.pk} "
        f"revoked by alice): {terms_first}, then run graph_load_demo again."
    )
    assert (natural_rows(), counts()) == (rows, counted)
    assert release_of("v1").status == DRAFT
    # The advice, followed: the terms recorded as the admin's add view
    # records them, then an approval. The load keeps both.
    licence = record_licence(
        v1, recorded_by=alice, **DEMO_LICENCE, read_on=timezone.localdate()
    )
    approval = record_decision(
        v1, INTERNAL, Decision.APPROVED, rationale="Load it.", decided_by=alice
    )
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    [dry], [run] = runs(v1, ImportMode.DRY_RUN), runs(v1, ImportMode.COMMIT)
    assert result.lines[1] == (
        f"{named(v1)}: registered already; dry run {dry.pk}: {V1_DRY}; committed by "
        f"run {run.pk}: {ALL_CREATED}; licence snapshot {licence.pk} kept; internal "
        f"approval {approval.pk} kept; published."
    )
    assert (v1.status, v2.status) == (SUPERSEDED, PUBLISHED)
    assert checked() == NO_PROBLEM


def test_a_denied_draft_is_not_registered_again_before_the_refusal(alice, monkeypatch):
    once(monkeypatch, "commit_release", "v2", CommitError(12, SIMULATED))
    stopped(alice)
    v2 = release_of("v2")
    denial = record_decision(
        v2, INTERNAL, Decision.DENIED, rationale="Not here.", decided_by=alice
    )
    # Registered under this code, the draft would take the new version, its
    # dry run's source records deleted.
    upgrade(monkeypatch)
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)} has an active internal denial, decision {denial.pk}: revoke it "
        f"with graph_revoke --approval {denial.pk}, then record the release's terms "
        "in the admin (Release licences, then Add), then an approval with "
        "graph_approve, then run graph_load_demo again."
    )
    assert error.lines[-1] == f"{named(v2)}: stopped at the internal approval."
    v2.refresh_from_db()
    assert (
        SourceRecord.objects.filter(release=v2).count(),
        v2.adapter_version,
        v2.mapping_version,
    ) == (17, SPEC.version, SPEC.mapping_version)
    assert (natural_rows(), counts()) == (rows, counted)


def test_a_revoked_approval_or_a_snapshot_without_terms_refuses_a_staged_release(
    alice, monkeypatch, graph_files_root
):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    approval = internal(v2)
    revoke(approval, reason="Test.", revoked_by=alice)
    # Registration would restore a lost stored copy: it is not reached.
    (storage.root() / v2.files.get(role=FileRole.DATA).storage_path).unlink()
    rows, counted = natural_rows(), counts()
    stored = sorted(graph_files_root.rglob("*"))
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)} has no active internal decision (internal approval "
        f"{approval.pk} revoked by alice): record an approval with graph_approve, "
        "then run graph_load_demo again."
    )
    assert error.lines == (
        FOUND,
        found(v1),
        f"{named(v2)}: stopped at the internal approval.",
    )
    assert (natural_rows(), counts()) == (rows, counted)
    assert sorted(graph_files_root.rglob("*")) == stored
    # An operator approves it again, then records a snapshot without terms.
    record_decision(v2, INTERNAL, Decision.APPROVED, rationale="Yes.", decided_by=alice)
    blank = record_licence(v2, recorded_by=alice, reviewer="alice")
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)}'s latest licence snapshot, {blank.pk}, records no terms: "
        "record its terms in the admin, then run graph_load_demo again."
    )
    assert error.lines[-1] == f"{named(v2)}: stopped at the licence snapshot."
    assert (natural_rows(), counts()) == (rows, counted)
    assert sorted(graph_files_root.rglob("*")) == stored
    # Its approval revoked as well: graph_approve refuses an approval while
    # the latest snapshot records no terms, so the advice records them first.
    again = internal(v2)
    revoke(again, reason="Test.", revoked_by=alice)
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)} has no active internal decision (internal approval "
        f"{again.pk} revoked by alice): record the release's terms in the admin "
        "(Release licences, then Add), then an approval with graph_approve, then "
        "run graph_load_demo again."
    )
    assert error.lines[-1] == f"{named(v2)}: stopped at the internal approval."
    assert (natural_rows(), counts()) == (rows, counted)
    assert sorted(graph_files_root.rglob("*")) == stored
    assert release_of("v2").status == STAGED


def test_a_staged_release_committed_with_other_options_is_discarded_first(
    alice, monkeypatch
):
    once(monkeypatch, "dry_run", "v2", DryRunError(4, SIMULATED))
    stopped(alice)
    v2 = release_of("v2")
    # An operator commits it by hand, without --create-entities.
    dry_run(v2, triggered_by=alice)
    run = commit_release(v2, triggered_by=alice).run
    counted = counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)}: the commit was refused: Release {v2.pk} was committed by run "
        f"{run.pk} without --create-entities and no resolution file: discard it "
        "with graph_discard_release to commit it again with other options. Then run "
        "graph_load_demo again."
    )
    assert error.lines[-1] == f"{named(v2)}: registered already; stopped at the commit."
    assert counts() == counted
    assert release_of("v2").status == STAGED


def test_what_an_operator_did_to_a_published_release_is_reported_and_kept(alice):
    load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    first = internal(v1)
    revoke(first, reason="Hide v1.", revoked_by=alice)
    rows, counted = natural_rows(), counts()
    result = load_demo(loaded_by=alice)
    assert result.wrote is False
    assert result.lines[1] == (
        f"{named(v1)}: registered already; licence snapshot {snapshot(v1).pk} kept; "
        f"internal approval {first.pk} revoked by alice; superseded by release "
        f"{v2.pk}."
    )
    assert result.lines[3] == LOADED_ALREADY
    assert (natural_rows(), counts()) == (rows, counted)
    # An operator records newer terms for v2: its approval relies on the
    # older snapshot, which the line names.
    relied_on, approval = snapshot(v2), internal(v2)
    newer = record_licence(
        v2, recorded_by=alice, **DEMO_LICENCE, read_on=timezone.localdate()
    )
    rows, counted = natural_rows(), counts()
    result = load_demo(loaded_by=alice)
    assert result.wrote is False
    assert result.lines[2] == (
        f"{named(v2)}: registered already; licence snapshot {newer.pk} kept; "
        f"internal approval {approval.pk} kept; it relies on snapshot {relied_on.pk}, "
        f"not the latest, {newer.pk}: refresh it with graph_approve --replace "
        f"{approval.pk}; published already."
    )
    assert (natural_rows(), counts()) == (rows, counted)
    # Then a snapshot without terms: graph_approve --replace is refused until
    # terms are recorded, so the line says so, or to revoke the approval.
    no_terms = record_licence(v2, recorded_by=alice, reviewer="alice")
    rows, counted = natural_rows(), counts()
    result = load_demo(loaded_by=alice)
    assert result.wrote is False
    assert result.lines[2] == (
        f"{named(v2)}: registered already; licence snapshot {no_terms.pk} records no "
        f"terms; internal approval {approval.pk} kept; it relies on snapshot "
        f"{relied_on.pk}, not the latest, {no_terms.pk}: record the release's terms "
        f"in the admin, then refresh it with graph_approve --replace {approval.pk}, "
        f"or revoke it with graph_revoke --approval {approval.pk}; published already."
    )
    assert (natural_rows(), counts()) == (rows, counted)
    # An operator denies v1, then records a snapshot of it without terms.
    denial = record_decision(
        v1, INTERNAL, Decision.DENIED, rationale="Not here.", decided_by=alice
    )
    blank = record_licence(v1, recorded_by=alice, reviewer="alice")
    rows, counted = natural_rows(), counts()
    result = load_demo(loaded_by=alice)
    assert result.wrote is False
    assert result.lines[1] == (
        f"{named(v1)}: registered already; licence snapshot {blank.pk} records no "
        f"terms; internal denial {denial.pk}; superseded by release {v2.pk}."
    )
    assert (natural_rows(), counts()) == (rows, counted)


def test_v1_withdrawn_after_it_was_published_leaves_v2_to_publish(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    withdraw_release(v1, reason="Test.", withdrawn_by=alice)
    result = load_demo(loaded_by=alice)
    assert result.lines == (
        FOUND,
        f"{named(v1)}: registered already; withdrawn (kept as it is): no longer "
        "readable when selected.",
        f"{named(v2)}: registered already; committed already; licence snapshot "
        f"{snapshot(v2).pk} kept; internal approval {internal(v2).pk} kept; "
        f"published; release {v1.pk}, which it corrects, stays withdrawn.",
        loaded(v2),
    )
    assert (release_of("v1").status, release_of("v2").status) == (WITHDRAWN, PUBLISHED)


def test_v1_withdrawn_before_it_was_published_is_final(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v1", ReleaseError(SIMULATED))
    stopped(alice)
    v1 = release_of("v1")
    assert v1.status == STAGED
    withdraw_release(v1, reason="Test.", withdrawn_by=alice)
    counted = counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)} is withdrawn, and a withdrawal is final: it was withdrawn "
        "before it was published, so the release that corrects it can never be "
        "published. The demonstration ships as these versions only, so "
        "graph_load_demo cannot load it again on this portal."
    )
    assert error.lines == (
        FOUND,
        f"{named(v1)}: registered already; stopped at its status.",
    )
    assert counts() == counted
    assert not DatasetRelease.objects.filter(version="v2").exists()


def test_v2_withdrawn_is_final(alice):
    load_demo(loaded_by=alice)
    v2 = release_of("v2")
    withdraw_release(v2, reason="Test.", withdrawn_by=alice)
    rows = natural_rows()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)} is withdrawn, and a withdrawal is final: no release of the "
        "demonstration is current. The demonstration ships as these versions only, "
        "so graph_load_demo cannot load it again on this portal."
    )
    assert error.lines == (
        FOUND,
        found(release_of("v1")),
        f"{named(v2)}: registered already; stopped at its status.",
    )
    assert natural_rows() == rows


def test_v2_withdrawn_before_it_was_published_is_final_and_v1_stays(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    assert v2.status == STAGED
    withdraw_release(v2, reason="Test.", withdrawn_by=alice)
    rows = natural_rows()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)} is withdrawn, and a withdrawal is final: it was withdrawn "
        "before it was published, so it can never be published, and release "
        f"{v1.pk} (v1), which it corrects, stays current. The demonstration ships "
        "as these versions only, so graph_load_demo cannot load it again on this "
        "portal."
    )
    assert error.lines == (
        FOUND,
        found(release_of("v1")),
        f"{named(v2)}: registered already; stopped at its status.",
    )
    assert natural_rows() == rows
    assert visible_release_ids(request_for(alice), Purpose.VIEW).ids == {v1.pk}
    # v1 withdrawn too: no release of the demonstration is current.
    withdraw_release(v1, reason="Test.", withdrawn_by=alice)
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)} is withdrawn, and a withdrawal is final: no release of the "
        "demonstration is current. The demonstration ships as these versions only, "
        "so graph_load_demo cannot load it again on this portal."
    )
    assert error.lines[1] == (
        f"{named(v1)}: registered already; withdrawn (kept as it is): no longer "
        "readable when selected."
    )
    assert visible_release_ids(request_for(alice), Purpose.VIEW).ids == frozenset()


def test_a_public_v1_needs_v2_approved_for_public_view_first(alice, monkeypatch):
    once(monkeypatch, "register_release", "v2", RegistrationError(SIMULATED))
    stopped(alice)
    v1 = release_of("v1")
    record_decision(
        v1,
        PUBLIC_VIEW,
        Decision.APPROVED,
        rationale="Public.",
        decided_by=alice,
        evidence="Test evidence.",
    )
    error = stopped(alice)
    v2 = release_of("v2")
    assert str(error) == (
        f"{named(v2)}: publishing was refused: Release {v1.pk} is public (an "
        f"effective public_view approval) and its correction, release {v2.pk}, is "
        "not: publishing it would hide public data. Approve public_view for release "
        f"{v2.pk} first. Record that approval with graph_approve, then run "
        "graph_load_demo again."
    )
    assert error.lines[-1].endswith("; stopped at publishing.")
    assert v2.status == STAGED
    record_decision(
        v2,
        PUBLIC_VIEW,
        Decision.APPROVED,
        rationale="Public.",
        decided_by=alice,
        evidence="Test evidence.",
    )
    result = load_demo(loaded_by=alice)
    assert result.lines[2:] == (
        f"{named(v2)}: registered already; committed already; licence snapshot "
        f"{snapshot(v2).pk} kept; internal approval {internal(v2).pk} kept; "
        f"published, superseding release {v1.pk}.",
        loaded(v2, "and visitors see it (public_view)"),
    )
    assert visible_release_ids(request_for(None), Purpose.VIEW).ids == {v2.pk}


def test_a_public_view_denial_of_v2_is_revoked_before_its_approval(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    record_decision(
        v1,
        PUBLIC_VIEW,
        Decision.APPROVED,
        rationale="Public.",
        decided_by=alice,
        evidence="Test evidence.",
    )
    # A scope keeps one active decision: graph_approve refuses an approval
    # beside this denial.
    denial = record_decision(
        v2, PUBLIC_VIEW, Decision.DENIED, rationale="Not public.", decided_by=alice
    )
    rows = natural_rows()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v2)}: publishing was refused: Release {v1.pk} is public (an "
        f"effective public_view approval) and its correction, release {v2.pk}, is "
        "not: publishing it would hide public data. Approve public_view for release "
        f"{v2.pk} first. Its public_view decision {denial.pk} is a denial: revoke it "
        f"with graph_revoke --approval {denial.pk}, then record an approval with "
        "graph_approve, then run graph_load_demo again."
    )
    assert error.lines[-1].endswith("; stopped at publishing.")
    assert natural_rows() == rows
    assert release_of("v2").status == STAGED
    # The advice, followed.
    revoke(denial, reason="Test.", revoked_by=alice)
    record_decision(
        v2,
        PUBLIC_VIEW,
        Decision.APPROVED,
        rationale="Public.",
        decided_by=alice,
        evidence="Test evidence.",
    )
    result = load_demo(loaded_by=alice)
    assert result.lines[-1] == loaded(v2, "and visitors see it (public_view)")
    assert release_of("v2").status == PUBLISHED


def test_the_last_line_says_who_sees_the_current_release(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v1, v2 = release_of("v1"), release_of("v2")
    denial = record_decision(
        v2, PUBLIC_VIEW, Decision.DENIED, rationale="Not public.", decided_by=alice
    )
    result = load_demo(loaded_by=alice)
    assert result.lines[3] == loaded(
        v2,
        f"for staff only; its public_view decision {denial.pk} is a denial: revoke "
        f"it with graph_revoke --approval {denial.pk}, then record an approval with "
        "graph_approve",
    )
    # An operator hides the demonstration; a rerun that writes (it restores a
    # lost stored copy) says that no one sees it.
    revoke(internal(v2), reason="Hide the demonstration.", revoked_by=alice)
    (storage.root() / v1.files.get(role=FileRole.DATA).storage_path).unlink()
    again = load_demo(loaded_by=alice)
    assert again.wrote is True
    assert again.lines[3] == loaded(
        v2, "but no one sees it: its internal approval is not effective"
    )
    assert visible_release_ids(request_for(alice), Purpose.VIEW).ids == frozenset()


def test_what_a_concurrent_load_did_meanwhile_is_kept(alice, monkeypatch):
    """Another load, emulated in turn: it registers v1 first, records v1's
    snapshot and approval while this one commits it, and publishes v2
    first; this load's own calls lose those races."""
    bob = StaffUserFactory(username="bob")
    register, commit, publish = (
        demo.register_release,
        demo.commit_release,
        demo.publish_release,
    )
    lost = set()

    def registering(**kwargs):
        if kwargs["version"] == "v1" and "register" not in lost:
            lost.add("register")
            register(**{**kwargs, "registered_by": bob})
            raise RegistrationError(
                "Release synthetic-regulation v1 was not registered: duplicate key "
                "value violates unique constraint. Nothing was written."
            )
        return register(**kwargs)

    def committing(release, **kwargs):
        result = commit(release, **kwargs)
        if release.version == "v1" and "decide" not in lost:
            lost.add("decide")
            record_licence(
                release,
                recorded_by=bob,
                **DEMO_LICENCE,
                read_on=timezone.localdate(),
                reviewer="bob",
            )
            record_decision(
                release,
                INTERNAL,
                Decision.APPROVED,
                rationale=DEMO_RATIONALE,
                decided_by=bob,
            )
        return result

    def publishing(release, *, published_by):
        if release.version == "v2" and "publish" not in lost:
            lost.add("publish")
            publish(release, published_by=bob)
            raise ReleaseError(
                f"Release {release.pk} is published: only a staged release is "
                "published. Nothing was written."
            )
        return publish(release, published_by=published_by)

    monkeypatch.setattr(demo, "register_release", registering)
    monkeypatch.setattr(demo, "commit_release", committing)
    monkeypatch.setattr(demo, "publish_release", publishing)
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    [dry] = runs(v1, ImportMode.DRY_RUN)
    [run] = runs(v1, ImportMode.COMMIT)
    assert lost == {"register", "decide", "publish"}
    assert result.lines[1] == (
        f"{named(v1)}: registered already; dry run {dry.pk}: {V1_DRY}; committed by "
        f"run {run.pk}: {ALL_CREATED}; licence snapshot {snapshot(v1).pk} kept; "
        f"internal approval {internal(v1).pk} kept; published."
    )
    assert result.lines[2].endswith("; published (meanwhile).")
    assert result.lines[3] == loaded(v2)
    assert (
        snapshot(v1).recorded_by_username,
        internal(v1).decided_by_username,
        v2.published_by_username,
    ) == ("bob", "bob", "bob")
    assert_loaded()


def acting_after_the_commit(monkeypatch, version, act):
    """An operator's act, done once right after graph.demo's commit_release
    returns for this version: after the loader's unlocked check of the
    release, before its locked licence and approval steps. The list holds
    what the act returned."""
    commit, done = demo.commit_release, []

    def committing(release, **kwargs):
        result = commit(release, **kwargs)
        if release.version == version and not done:
            done.append(act(release))
        return result

    monkeypatch.setattr(demo, "commit_release", committing)
    return done


@pytest.mark.parametrize("act", ["deny", "revoke", "blank"])
def test_what_an_operator_records_during_a_load_is_checked_under_the_lock(
    alice, monkeypatch, act
):
    bob = StaffUserFactory(username="bob")

    def denied(release):
        return record_decision(
            release, INTERNAL, Decision.DENIED, rationale="Not here.", decided_by=bob
        )

    def revoked(release):
        record_licence(
            release,
            recorded_by=bob,
            **DEMO_LICENCE,
            read_on=timezone.localdate(),
            reviewer="bob",
        )
        approval = record_decision(
            release,
            INTERNAL,
            Decision.APPROVED,
            rationale=DEMO_RATIONALE,
            decided_by=bob,
        )
        return revoke(approval, reason="Not yet.", revoked_by=bob)

    def blank(release):
        return record_licence(release, recorded_by=bob, reviewer="bob")

    acts = {"deny": denied, "revoke": revoked, "blank": blank}
    done = acting_after_the_commit(monkeypatch, "v1", acts[act])
    error = stopped(alice)
    v1 = release_of("v1")
    [made] = done
    [dry], [run] = runs(v1, ImportMode.DRY_RUN), runs(v1, ImportMode.COMMIT)
    head = (
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; committed by run "
        f"{run.pk}: {ALL_CREATED}; "
    )
    message, tail = {
        "deny": (
            f"{named(v1)} has an active internal denial, decision {made.pk}: revoke "
            f"it with graph_revoke --approval {made.pk}, then record an approval "
            "with graph_approve, then run graph_load_demo again.",
            f"licence snapshot {snapshot(v1).pk} recorded; stopped at the internal "
            "approval.",
        ),
        "revoke": (
            f"{named(v1)} has no active internal decision (internal approval "
            f"{made.pk} revoked by bob): record an approval with graph_approve, then "
            "run graph_load_demo again.",
            f"licence snapshot {snapshot(v1).pk} kept; stopped at the internal "
            "approval.",
        ),
        "blank": (
            f"{named(v1)}'s latest licence snapshot, {made.pk}, records no terms: "
            "record its terms in the admin, then run graph_load_demo again.",
            "stopped at the licence snapshot.",
        ),
    }[act]
    assert str(error) == message
    assert error.lines == (CREATED, head + tail)
    assert v1.status == STAGED
    assert ReleaseLicence.objects.filter(release=v1).count() == 1


def test_a_snapshot_without_terms_recorded_during_a_load_stops_a_staged_release(
    alice, monkeypatch
):
    once(monkeypatch, "publish_release", "v2", ReleaseError(SIMULATED))
    stopped(alice)
    v2 = release_of("v2")
    done = acting_after_the_commit(
        monkeypatch,
        "v2",
        lambda release: record_licence(release, recorded_by=alice, reviewer="alice"),
    )
    error = stopped(alice)
    [blank] = done
    assert str(error) == (
        f"{named(v2)}'s latest licence snapshot, {blank.pk}, records no terms: "
        "record its terms in the admin, then run graph_load_demo again."
    )
    assert error.lines[-1] == (
        f"{named(v2)}: registered already; committed already; stopped at the "
        "licence snapshot."
    )
    assert release_of("v2").status == STAGED


# ── What a refusal advises ──────────────────────────────────────────────────


def test_advice_to_run_graph_import_names_the_loader_instead(alice, monkeypatch):
    # An operator dry-runs v1 without --create-entities between the loader's
    # dry run and its commit: the commit is refused (precondition 12).
    bob = StaffUserFactory(username="bob")
    real, raced = demo.dry_run, []

    def racing(release, **kwargs):
        run = real(release, **kwargs)
        if release.version == "v1" and not raced:
            raced.append(real(release, triggered_by=bob))
        return run

    monkeypatch.setattr(demo, "dry_run", racing)
    error = stopped(alice)
    v1 = release_of("v1")
    dry, [bobs] = runs(v1, ImportMode.DRY_RUN)[0], raced
    assert str(error) == (
        f"{named(v1)}: the commit was refused: Release {v1.pk}'s latest dry run, "
        f"{bobs.pk}, ran without --create-entities, and this commit runs with it: "
        "run graph_import --dry-run again, with the options you will commit with. "
        "For the demonstration, run graph_load_demo again instead."
    )
    assert error.lines[-1] == (
        f"{named(v1)}: registered; dry run {dry.pk}: {V1_DRY}; stopped at the commit."
    )
    # The advice, followed.
    load_demo(loaded_by=alice)
    assert_loaded()


def test_advice_to_dry_run_again_in_other_words_names_the_loader_instead(
    alice, monkeypatch
):
    # Between the loader's dry run of v1 and its commit, v1's record syn-05
    # is lost, so the commit is refused (precondition 14) with no commit run
    # open. Its advice, "dry-run it again", names no command: the loader's
    # sentence replaces it, and a rerun dry-runs and commits v1.
    real, lost = demo.dry_run, []

    def losing_a_record(release, **kwargs):
        run = real(release, **kwargs)
        if release.version == "v1" and not lost:
            records = SourceRecord.objects.filter(release=release, record_key="syn-05")
            lost.append(records.delete())
        return run

    monkeypatch.setattr(demo, "dry_run", losing_a_record)
    error = stopped(alice)
    v1 = release_of("v1")
    assert runs(v1, ImportMode.COMMIT) == []
    assert str(error).endswith(
        "(record 'syn-05' of line 6 is not stored): dry-run it again. For the "
        "demonstration, run graph_load_demo again instead."
    )
    # The advice, followed.
    load_demo(loaded_by=alice)
    assert len(runs(v1, ImportMode.DRY_RUN)) == 2
    assert_loaded()


def test_an_open_commit_run_this_code_cannot_resume_is_discarded_first(
    alice, monkeypatch
):
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    [run] = runs(v1, ImportMode.COMMIT)
    # As an earlier openPIP, whose registry was at another version, left it.
    run.parameters = {**run.parameters, "registry_version": "1.0"}
    run.save(update_fields=["parameters"])
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)}: the commit was refused: Release {v1.pk} has an open commit run "
        f"{run.pk} that cannot be resumed under this code (the database's registry "
        f"is at version {REGISTRY_VERSION}, and the run's at 1.0): discard it with "
        "graph_discard_release (the evidence its batches recorded is deleted; "
        "entities and relationships stay), then dry-run it and commit it. For the "
        "demonstration, discard it with graph_discard_release, then run "
        "graph_load_demo again."
    )
    assert error.lines[-1] == f"{named(v1)}: registered already; stopped at the commit."
    assert (natural_rows(), counts()) == (rows, counted)
    # Run again as it stands, the loader is refused the same way: the
    # discard comes first.
    again = stopped(alice)
    assert (str(again), again.lines) == (str(error), error.lines)
    assert (natural_rows(), counts()) == (rows, counted)
    # The advice, followed.
    discard_release(v1, reason="Test.", discarded_by=alice)
    load_demo(loaded_by=alice)
    assert_loaded()


def test_an_open_commit_run_waiting_on_its_cause_names_both_ways_out(
    alice, monkeypatch
):
    # v1's commit stops at syn-14; meanwhile SYN-G1 takes SYN-G3, which
    # syn-14 names first, as a secondary identifier, so syn-14 maps
    # differently (precondition 15). The open run cannot be resumed while
    # this holds: a rerun is refused the same way, and resumes the run once
    # the cause is gone.
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    [dry], [run] = runs(v1, ImportMode.DRY_RUN), runs(v1, ImportMode.COMMIT)
    gene = Entity.objects.get(
        identifiers__namespace_id="synthetic_gene", identifiers__value="SYN-G1"
    )
    taken = add_identifier(gene, "synthetic_gene", "SYN-G3", IdentifierRole.SECONDARY)
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)}: the commit was refused: 1 record of release {v1.pk} maps "
        f"differently from its latest dry run {dry.pk}, for example record 'syn-14' "
        "of line 15: mapped then, unresolved now (The object: synthetic_gene SYN-G3 "
        f"(gene, taxon 32630) is candidate: entity {gene.pk} (synthetic_gene:SYN-G1, "
        "provisional), matched as secondary synthetic_gene 'SYN-G3'. Nothing "
        "accepted one: a candidate maps only through a reviewed resolution file "
        f"until RF1a). Its open commit run {run.pk} cannot be resumed while this "
        "holds: discard it with graph_discard_release, then dry-run it again. For "
        "the demonstration, run graph_load_demo again once this no longer holds, "
        "which resumes the run; or discard the release with graph_discard_release, "
        "then run graph_load_demo again, which dry-runs and commits it as it maps "
        "now."
    )
    assert error.lines == (
        FOUND,
        f"{named(v1)}: registered already; stopped at the commit.",
    )
    assert (natural_rows(), counts()) == (rows, counted)
    # Run again while it holds, the loader is refused the same way.
    again = stopped(alice)
    assert (str(again), again.lines) == (str(error), error.lines)
    assert (natural_rows(), counts()) == (rows, counted)
    # The first way out, once the cause is gone: the run is resumed, with no
    # second dry run.
    taken.delete()
    load_demo(loaded_by=alice)
    assert runs(v1, ImportMode.DRY_RUN) == [dry]
    assert runs(v1, ImportMode.COMMIT) == [run]
    assert_loaded()


def test_an_open_commit_run_waiting_on_a_lost_stored_copy_is_resumed_by_a_rerun(
    alice, monkeypatch
):
    # While v1's commit run is open, the stored copy of its data file is
    # lost: the commit refuses it (precondition 13), naming
    # graph_register_release. The loader's advice is a plain rerun, whose
    # registration restores the copy before the run resumes.
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    [dry], [run] = runs(v1, ImportMode.DRY_RUN), runs(v1, ImportMode.COMMIT)
    (storage.root() / v1.files.get(role=FileRole.DATA).storage_path).unlink()
    with pytest.raises(CommitError) as raised:
        commit_release(v1, triggered_by=alice, create_entities=True)
    message = str(raised.value)
    assert raised.value.precondition == 13
    assert f"Its open commit run {run.pk} cannot be resumed while this holds" in message
    assert "graph_register_release" in message
    assert demo._demo_advice(message) == (
        "For the demonstration, run graph_load_demo again instead."
    )
    # The advice, followed.
    result = load_demo(loaded_by=alice)
    assert result.lines[1].startswith(
        f"{named(v1)}: registered already, restored the stored data file; committed "
        f"by run {run.pk}, resuming it after 1 stop: {ALL_CREATED};"
    )
    assert runs(v1, ImportMode.DRY_RUN) == [dry]
    assert runs(v1, ImportMode.COMMIT) == [run]
    assert_loaded()


@pytest.mark.parametrize(
    "changes, then, advice",
    [
        (
            {},
            "register it again with graph_register_release, dry-run it and commit it",
            "For the demonstration, discard it with graph_discard_release, then run "
            "graph_load_demo again.",
        ),
        (
            {
                "version": SPEC.version,
                "mapping_version": SPEC.mapping_version,
                "kind": ReleaseKind.REFERENCE.value,
            },
            "register its files as a new version, since a release keeps its kind "
            "once it has created entities",
            "",
        ),
    ],
    ids=["register-again", "new-version"],
)
def test_advice_on_a_commit_run_a_newer_adapter_cannot_resume(
    alice, monkeypatch, changes, then, advice
):
    # A load meets this draft at registration, which refuses it first
    # (test_a_commit_stopped_under_an_older_adapter_is_discarded_first); a
    # commit refuses it at precondition 10. The loader's advice on that
    # refusal: the discard first where a load then registers it again, and
    # none where only a new version would do.
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    upgrade(monkeypatch, **changes)
    with pytest.raises(CommitError) as raised:
        commit_release(v1, triggered_by=alice, create_entities=True)
    message = str(raised.value)
    assert raised.value.precondition == 10
    assert "that cannot be resumed under this code" in message
    assert message.endswith(f", then {then}. Nothing was written.")
    assert demo._demo_advice(message) == advice


def test_an_open_commit_run_with_other_options_is_discarded_first(alice, monkeypatch):
    once(monkeypatch, "dry_run", "v2", DryRunError(4, SIMULATED))
    stopped(alice)
    v2 = release_of("v2")
    # An operator dry-runs and commits v2 by hand, without --create-entities,
    # and the commit stops.
    dry = dry_run(v2, triggered_by=alice)
    real = commit_module.record_claims

    def failing(*args, **kwargs):
        raise ValueError("Stopped for the test.")

    monkeypatch.setattr(commit_module, "record_claims", failing)
    with pytest.raises(CommitStopped):
        commit_release(v2, triggered_by=alice)
    monkeypatch.setattr(commit_module, "record_claims", real)
    [run] = runs(v2, ImportMode.COMMIT)
    rows, counted = natural_rows(), counts()
    error = stopped(alice)
    # Run again as it stands, the loader would be refused the same way: the
    # discard comes first.
    assert str(error) == (
        f"{named(v2)}: the commit was refused: Release {v2.pk} has an open commit run "
        f"{run.pk}, started from dry run {dry.pk} without --create-entities and no "
        "resolution file: run the same command with those options to resume it, or "
        "discard it with graph_discard_release, then dry-run it again. For the "
        "demonstration, discard it with graph_discard_release, then run "
        "graph_load_demo again."
    )
    assert error.lines[-1] == f"{named(v2)}: registered already; stopped at the commit."
    assert (natural_rows(), counts()) == (rows, counted)
    # The advice, followed.
    discard_release(v2, reason="Test.", discarded_by=alice)
    load_demo(loaded_by=alice)
    assert_loaded()


# ── A newer adapter, and changed files ──────────────────────────────────────


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"version": SPEC.version},
        {"mapping_version": SPEC.mapping_version},
        {
            "version": SPEC.version,
            "mapping_version": SPEC.mapping_version,
            "kind": ReleaseKind.REFERENCE.value,
        },
    ],
    ids=["version-and-mapping", "mapping-only", "version-only", "kind-only"],
)
def test_a_newer_adapter_version_keeps_what_was_committed(alice, monkeypatch, changes):
    load_demo(loaded_by=alice)
    upgrade(monkeypatch, **changes)
    rows = natural_rows()
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    assert result.wrote is False
    kept = "registered already, read by canonical_tsv_v1 1.0.0 (mapping 1)"
    assert result.lines == (
        FOUND,
        f"{named(v1)}: {kept}; licence snapshot {snapshot(v1).pk} kept; internal "
        f"approval {internal(v1).pk} kept; superseded by release {v2.pk}.",
        f"{named(v2)}: {kept}; licence snapshot {snapshot(v2).pk} kept; internal "
        f"approval {internal(v2).pk} kept; published already.",
        LOADED_ALREADY,
    )
    assert natural_rows() == rows


def test_a_draft_takes_a_newer_adapter_version(alice, monkeypatch):
    once(monkeypatch, "commit_release", "v2", CommitError(12, SIMULATED))
    error = stopped(alice)
    v2 = release_of("v2")
    assert str(error) == f"{named(v2)}: the commit was refused: Simulated for the test."
    assert SourceRecord.objects.filter(release=v2).count() == 17
    upgrade(monkeypatch)
    result = load_demo(loaded_by=alice)
    v1, v2 = release_of("v1"), release_of("v2")
    dry, commit = runs(v2, ImportMode.DRY_RUN)[-1], runs(v2, ImportMode.COMMIT)[-1]
    assert result.lines[1:] == (
        f"{named(v1)}: registered already, read by canonical_tsv_v1 1.0.0 (mapping "
        f"1); licence snapshot {snapshot(v1).pk} kept; internal approval "
        f"{internal(v1).pk} kept; published already.",
        f"{named(v2)}: registered again under canonical_tsv_v1 1.1.0 (17 source "
        f"records deleted); dry run {dry.pk}: {V2_DRY}; committed by run "
        f"{commit.pk}: {NONE_CREATED}; licence snapshot {snapshot(v2).pk} recorded; "
        f"internal approval {internal(v2).pk} recorded; published, superseding "
        f"release {v1.pk}.",
        loaded(v2),
    )
    assert (v1.status, v2.status) == (SUPERSEDED, PUBLISHED)
    assert (v1.adapter_version, v2.adapter_version, v2.mapping_version) == (
        "1.0.0",
        "1.1.0",
        "2",
    )


def test_a_commit_stopped_under_an_older_adapter_is_discarded_first(alice, monkeypatch):
    monkeypatch.setattr(limits, "BATCH_ROWS", 4)
    stopping_at(monkeypatch, "syn-14")
    stopped(alice)
    v1 = release_of("v1")
    upgrade(monkeypatch)
    rows, imports = natural_rows(), ImportRun.objects.count()
    error = stopped(alice)
    # Registration refuses the draft its commit left with evidence and an
    # open run; run again as it stands, the loader is refused the same way.
    assert str(error) == (
        f"{named(v1)}: registration was refused: {named(v1)} was registered with "
        "canonical_tsv_v1 1.0.0 (mapping 1, kind evidence), and this code carries "
        "1.1.0 (mapping 2, kind evidence); evidence is recorded from its records, "
        "which rests on that mapping. Discard it with graph_discard_release, then "
        "register it again; or register its files under a new version. For the "
        "demonstration, discard it with graph_discard_release, then run "
        "graph_load_demo again, which registers it again under this code's version."
    )
    assert error.lines == (FOUND, f"{named(v1)}: stopped at registration.")
    assert (natural_rows(), ImportRun.objects.count()) == (rows, imports)
    # The advice, followed. What the stopped batches created stays, so the
    # commit creates the rest.
    discard_release(v1, reason="Test.", discarded_by=alice)
    result = load_demo(loaded_by=alice)
    v1 = release_of("v1")
    dry, run = runs(v1, ImportMode.DRY_RUN)[-1], runs(v1, ImportMode.COMMIT)[-1]
    assert result.lines[1] == (
        f"{named(v1)}: registered again under canonical_tsv_v1 1.1.0 (0 source "
        f"records deleted); dry run {dry.pk}: {V1_DRY}; committed by run {run.pk}: "
        "created 4 entities, 4 relationships, 0 publications and 0 terms; licence "
        f"snapshot {snapshot(v1).pk} recorded; internal approval {internal(v1).pk} "
        "recorded; published."
    )
    assert (v1.adapter_version, v1.mapping_version) == ("1.1.0", "2")
    assert_loaded()


@pytest.mark.parametrize("upgraded", [False, True])
@pytest.mark.parametrize(
    "name, old, new",
    [
        ("release_v1.tsv", b"activating", b"Activating"),
        ("manifest.json", b"paper 1", b"paper 9"),
    ],
)
def test_files_changed_under_a_published_version_are_refused(
    alice, monkeypatch, tmp_path, name, old, new, upgraded
):
    load_demo(loaded_by=alice)
    v1 = release_of("v1")
    if upgraded:
        # Never registered again: the loader's own comparison is the guard.
        upgrade(monkeypatch)
    copy = demo_copy(tmp_path, **{name: lambda data: data.replace(old, new, 1)})
    monkeypatch.setattr(demo, "DEMO_DIR", copy)
    rows = natural_rows()
    error = stopped(alice)
    assert str(error) == (
        f"{named(v1)}: the demo files changed under v1, which is past draft: a "
        "change ships as a new version."
    )
    assert error.lines == (FOUND, f"{named(v1)}: stopped at registration.")
    assert natural_rows() == rows


# ── The command ─────────────────────────────────────────────────────────────


def test_the_command_prints_the_lines_so_far_before_it_refuses(alice, monkeypatch):
    once(monkeypatch, "publish_release", "v1", ReleaseError(SIMULATED))
    out = io.StringIO()
    with pytest.raises(CommandError) as raised:
        call_command("graph_load_demo", "--as", "alice", stdout=out)
    v1 = release_of("v1")
    assert str(raised.value) == (
        f"{named(v1)}: publishing was refused: Simulated for the test."
    )
    assert out.getvalue().splitlines() == [
        CREATED,
        f"{named(v1)}: registered; dry run {runs(v1, ImportMode.DRY_RUN)[0].pk}: "
        f"{V1_DRY}; committed by run {runs(v1, ImportMode.COMMIT)[0].pk}: "
        f"{ALL_CREATED}; licence snapshot {snapshot(v1).pk} recorded; internal "
        f"approval {internal(v1).pk} recorded; stopped at publishing.",
    ]
    # Any other exception is raised as it is, after the lines so far.
    once(monkeypatch, "publish_release", "v2", RuntimeError("Unexpected."))
    out = io.StringIO()
    with pytest.raises(RuntimeError) as raised:
        call_command("graph_load_demo", "--as", "alice", stdout=out)
    v2 = release_of("v2")
    lines = out.getvalue().splitlines()
    assert raised.value.demo_lines == tuple(lines)
    assert lines[1] == (
        f"{named(v1)}: registered already; committed already; licence snapshot "
        f"{snapshot(v1).pk} kept; internal approval {internal(v1).pk} kept; "
        "published."
    )
    assert lines[2].startswith(f"{named(v2)}: registered; dry run ")
    assert lines[2].endswith("; stopped at publishing.")
    out = io.StringIO()
    call_command("graph_load_demo", "--as", "alice", stdout=out)
    assert out.getvalue().splitlines()[-1] == loaded(release_of("v2"))
    assert_loaded()
