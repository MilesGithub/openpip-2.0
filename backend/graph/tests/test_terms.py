"""Vocabulary terms: CURIEs, the term rows, get_or_create_term, and the PSI-MI
physical crosswalk (graph.terms); and the label a term is shown with
(graph.summary.term_label)."""

import dataclasses
import re
import threading

import pytest
from django.db import connection, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory

from graph.models import (
    ApprovalScope,
    DatasetRelease,
    ImmutableRecordError,
    ImportMode,
    ImportState,
    InteractionLevel,
    Term,
)
from graph.terms import (
    ONTOLOGIES,
    PHYSICAL_INTERACTION_LEVELS,
    TermError,
    get_or_create_term,
    normalize_curie,
    ontology_of,
)
from graph.summary import term_label
from graph.tests.factories import (
    DatasetReleaseFactory,
    EvidenceAssertionFactory,
    ImportRunFactory,
    SourceFileFactory,
    StaffUserFactory,
    TermFactory,
    UsageApprovalFactory,
    approvable_release,
    approve,
    evidence_release,
)
from graph.tests.test_provenance import (
    DELETE_PATHS,
    DRAFT,
    PUBLISHED,
    STAGED,
    SUPERSEDED,
    WITHDRAWN,
    WRITE_PATHS,
    _a_backend_waits_on_a_lock,
    advance,
    delete,
    move,
    stored,
    write,
)
from graph.visibility import Purpose, ReleaseSet, visible_release_ids

# ── CURIEs ──────────────────────────────────────────────────────────────────


def test_the_accepted_ontologies_are_pinned():
    assert {
        prefix: (slug, form.pattern) for prefix, (slug, form) in ONTOLOGIES.items()
    } == {
        "MI": ("psi-mi", r"\d{4}"),
        "ECO": ("eco", r"\d{7}"),
    }
    # \d is ASCII digits only.
    assert all(form.flags & re.ASCII for _, form in ONTOLOGIES.values())


@pytest.mark.parametrize(
    "value,canonical",
    [
        ("MI:0915", "MI:0915"),
        ("mi:0915", "MI:0915"),
        (" Mi:0915\n", "MI:0915"),
        ("ECO:0000353", "ECO:0000353"),
        ("eco:0000353", "ECO:0000353"),
    ],
)
def test_a_curie_is_written_canonically(value, canonical):
    assert normalize_curie(value) == canonical
    assert ontology_of(value) == ("psi-mi" if canonical.startswith("MI") else "eco")


@pytest.mark.parametrize(
    "value",
    [
        "GO:0005737",
        "psi-mi:MI:0915",
        "MI0915",
        "MI:915",
        "MI:09150",
        "MI:0915a",
        "MI: 0915",
        "MI:٠٩١٥",
        "ECO:000353",
        "ECO:00003530",
        "MI:0915\x00",
        "\x00MI:0915",
        # A dotless i upper-cases to I, and is still not the prefix.
        "mı:0915",
        "",
        ":0915",
        None,
        915,
    ],
)
def test_anything_but_a_curie_of_a_known_ontology_is_refused(value):
    with pytest.raises(TermError):
        normalize_curie(value)
    assert issubclass(TermError, ValueError)


# ── The PSI-MI physical crosswalk ───────────────────────────────────────────


def test_the_crosswalk_pins_its_three_anchors():
    assert PHYSICAL_INTERACTION_LEVELS["MI:0914"] == (
        InteractionLevel.ASSOCIATION,
        "association",
    )
    assert PHYSICAL_INTERACTION_LEVELS["MI:0915"] == (
        InteractionLevel.PHYSICAL_ASSOCIATION,
        "physical association",
    )
    assert PHYSICAL_INTERACTION_LEVELS["MI:0407"] == (
        InteractionLevel.DIRECT_INTERACTION,
        "direct interaction",
    )
    assert PHYSICAL_INTERACTION_LEVELS["MI:0195"] == (
        InteractionLevel.DIRECT_INTERACTION,
        "covalent binding",
    )


@pytest.mark.parametrize(
    "curie",
    [
        # Colocalization, and proximity under it.
        "MI:0403",
        "MI:2364",
        # Genetic interaction, and the terms under it.
        "MI:0208",
        "MI:2402",
        "MI:2373",
        # Functional association, predicted interaction, and MI:0914's parent.
        "MI:2286",
        "MI:1110",
        "MI:2232",
        # Obsolete.
        "MI:0218",
        # Under MI:0407, but intra-molecular (MI:1126), or either that or a
        # homomer, the source cannot say which (MI:1127).
        "MI:1126",
        "MI:1127",
    ],
)
def test_the_crosswalk_leaves_out_what_is_not_clearly_physical_between_molecules(
    curie,
):
    assert curie not in PHYSICAL_INTERACTION_LEVELS


def test_every_crosswalk_entry_is_a_canonical_psi_mi_term_with_a_level_and_label():
    for curie, (level, label) in PHYSICAL_INTERACTION_LEVELS.items():
        assert normalize_curie(curie) == curie
        assert ontology_of(curie) == "psi-mi"
        assert level in InteractionLevel.values and level != "not_stated", curie
        assert isinstance(label, str) and label.strip() and label == label.strip()
    # As generated from psi-mi.obo at commit 7b5bb4f: regenerating it from
    # another release changes these counts, and the review checks why.
    levels = [level for level, _ in PHYSICAL_INTERACTION_LEVELS.values()]
    assert (len(levels), levels.count("direct_interaction")) == (71, 69)
    assert levels.count("association") == levels.count("physical_association") == 1


# ── Term rows ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize(
    "fields,refusal",
    [
        ({"curie": "mi:0915"}, "Term.curie: is stored canonical"),
        ({"curie": "GO:0005737"}, "Term.curie"),
        ({"curie": "MI:915"}, "Term.curie"),
        ({"ontology": "eco"}, "Term.ontology: follows the CURIE's prefix"),
        ({"ontology": "PSI-MI"}, "Term.ontology"),
        ({"label": ""}, "Term.label: is never blank"),
        ({"label": "  "}, "Term.label: is never blank"),
        ({"label": "a\x00b"}, "Term.label: contains a NUL"),
        ({"label": "x" * 256}, "Term.label: is at most 255"),
    ],
)
def test_a_term_is_inserted_canonical_with_its_ontology_and_a_label(
    db, fields, refusal, path
):
    values = {"curie": "MI:0915", "ontology": "psi-mi", "label": "physical association"}
    values.update(fields)
    term = Term(**values)
    with pytest.raises(ImmutableRecordError, match=refusal):
        if path == "save":
            term.save()
        else:
            Term.objects.bulk_create([term], ignore_conflicts=True)
    assert not Term.objects.exists()


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_term_is_never_changed(db, path):
    term = TermFactory(curie="MI:0915", label="physical association")
    before = stored(term)
    with pytest.raises(ImmutableRecordError, match="Term"):
        write(path, term, label="something else")
    assert stored(term) == before


@pytest.mark.parametrize("path", DELETE_PATHS)
def test_a_term_is_never_deleted(db, path):
    term = TermFactory()
    with pytest.raises(ImmutableRecordError, match="Term"):
        delete(path, term)
    with pytest.raises(ImmutableRecordError, match="Term"):
        Term(pk=term.pk).delete()
    assert Term.objects.filter(pk=term.pk).exists()


def test_evidence_keeps_the_terms_it_names(db):
    assertion = EvidenceAssertionFactory(eco_term=TermFactory(curie="ECO:0000353"))
    with pytest.raises(ProtectedError):
        Term._base_manager.filter(pk="ECO:0000353").delete()
    assert stored(assertion)["eco_term_id"] == "ECO:0000353"


def test_deleting_the_draft_release_that_supplied_a_label_keeps_the_term(db):
    release = DatasetReleaseFactory()
    term = TermFactory(curie="MI:0018", label="two hybrid", label_release=release)
    DatasetRelease.objects.get(pk=release.pk).delete()
    row = stored(term)
    assert (row["label"], row["label_release_id"]) == ("two hybrid", None)


@pytest.mark.parametrize(
    "path", ["save", "create", "bulk_create", "bulk_create_ignore_conflicts"]
)
@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_a_label_is_attributed_only_to_a_draft_release(db, path, status):
    """Only a draft release's commit states a label (graph.terms), so no
    insert path attributes one to a release past draft."""
    release = advance(DatasetReleaseFactory(), status)
    values = {
        "curie": "MI:0018",
        "ontology": "psi-mi",
        "label": "two hybrid",
        "label_release": release,
    }
    with pytest.raises(
        ImmutableRecordError,
        match=f"Term.label_release: .*release {release.pk} is {status}",
    ):
        if path == "save":
            Term(**values).save()
        elif path == "create":
            Term.objects.create(**values)
        else:
            Term.objects.bulk_create(
                [Term(**values)], ignore_conflicts=path.endswith("conflicts")
            )
    assert not Term.objects.exists()


# ── get_or_create_term ──────────────────────────────────────────────────────


@pytest.fixture
def commit(switch_on):
    """An approved draft release's running commit run."""
    return evidence_release(records=0).run


def test_a_commit_records_a_term_with_the_release_that_supplied_its_label(commit):
    result = get_or_create_term(" mi:0018 ", "  two hybrid ", import_run=commit)
    assert result.created and result.label_conflict is None
    row = stored(result.term)
    assert (row["curie"], row["ontology"], row["label"], row["label_release_id"]) == (
        "MI:0018",
        "psi-mi",
        "two hybrid",
        commit.release_id,
    )
    # Again, by the run's id: the same row.
    again = get_or_create_term("MI:0018", "two hybrid", import_run=commit.pk)
    assert (again.term.pk, again.created, again.label_conflict) == (
        "MI:0018",
        False,
        None,
    )
    assert Term.objects.count() == 1


def test_a_term_keeps_its_first_label_and_reports_another(commit):
    get_or_create_term(
        "ECO:0000353", "physical interaction evidence", import_run=commit
    )
    other = evidence_release(records=0).run
    result = get_or_create_term("ECO:0000353", "physical evidence", import_run=other)
    assert not result.created
    assert "keeps its label 'physical interaction evidence'" in result.label_conflict
    assert "'physical evidence', which is not written" in result.label_conflict
    row = stored(result.term)
    assert (row["label"], row["label_release_id"]) == (
        "physical interaction evidence",
        commit.release_id,
    )


def test_a_crosswalk_term_takes_its_pinned_label(commit):
    result = get_or_create_term("MI:0915", "phys. assoc.", import_run=commit)
    assert result.created
    assert "pins MI:0915's label as 'physical association'" in result.label_conflict
    # The commit stated another label, so no release supplied the one kept.
    assert (
        stored(result.term)["label"],
        stored(result.term)["label_release_id"],
    ) == ("physical association", None)
    same = get_or_create_term("MI:0915", "physical association", import_run=commit)
    assert (same.created, same.label_conflict) == (False, None)
    # A commit that states the pinned label supplies it.
    stated = get_or_create_term("MI:0407", "direct interaction", import_run=commit)
    assert (stated.created, stated.label_conflict) == (True, None)
    assert stored(stated.term)["label_release_id"] == commit.release_id


@pytest.mark.parametrize(
    "curie,label",
    [
        ("GO:0005737", "cytoplasm"),
        ("MI:018", "two hybrid"),
        ("MI:0018", ""),
        ("MI:0018", "   "),
        ("MI:0018", "two\x00hybrid"),
        ("MI:0018", "x" * 256),
        ("MI:0018", None),
    ],
)
def test_a_malformed_curie_or_label_records_nothing(commit, curie, label):
    with pytest.raises(TermError):
        get_or_create_term(curie, label, import_run=commit)
    assert not Term.objects.exists()


def test_no_term_is_recorded_while_the_switch_is_off(commit, settings):
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(TermError, match="TYPED_GRAPH_ENABLED"):
        get_or_create_term("MI:0018", "two hybrid", import_run=commit)
    assert not Term.objects.exists()


@pytest.mark.parametrize(
    "mode,state",
    [
        (ImportMode.DRY_RUN, ImportState.RUNNING),
        (ImportMode.COMMIT, ImportState.FAILED),
        (ImportMode.COMMIT, ImportState.COMPLETED),
        (ImportMode.COMMIT, ImportState.ABANDONED),
    ],
)
def test_only_a_running_commit_run_records_terms(switch_on, mode, state):
    bundle = evidence_release(records=0)
    if mode == ImportMode.DRY_RUN:
        run = ImportRunFactory(release=bundle.release, file=None, mode=mode)
    else:
        run = bundle.run
        run.state = state
        run.save()
    with pytest.raises(TermError, match="only a running commit run"):
        get_or_create_term("MI:0018", "two hybrid", import_run=run)
    assert not Term.objects.exists()


@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_only_a_draft_release_records_terms(commit, status):
    advance(commit.release, status)
    with pytest.raises(TermError, match=f"is {status}: terms are recorded only"):
        get_or_create_term("MI:0018", "two hybrid", import_run=commit)
    assert not Term.objects.exists()


def test_an_unapproved_release_records_no_terms(switch_on):
    for run in (
        evidence_release(records=0, approved=False).run,
        _revoked().run,
    ):
        with pytest.raises(TermError, match="no effective internal approval"):
            get_or_create_term("MI:0018", "two hybrid", import_run=run)
    assert not Term.objects.exists()


def _revoked():
    bundle = evidence_release(records=0)
    approval = bundle.release.approvals.get(scope=ApprovalScope.INTERNAL)
    approval.revoked_at = approval.decided_at
    approval.revoked_by_username = "graph-staff"
    approval.revoke_reason = "Test only."
    approval.save()
    return bundle


def test_a_synthetic_release_records_terms_without_approvals(switch_on):
    run = evidence_release(records=0, synthetic=True).run
    assert not run.release.approvals.exists()
    assert get_or_create_term("MI:0018", "two hybrid", import_run=run).created


def test_an_unknown_run_records_nothing(switch_on):
    for run in (10**12, "1", None, ImportRunFactory.build()):
        with pytest.raises(TermError, match="Unknown import run"):
            get_or_create_term("MI:0018", "two hybrid", import_run=run)
    assert not Term.objects.exists()


def test_a_denied_internal_decision_is_no_approval(switch_on):
    bundle = evidence_release(records=0, approved=False)
    UsageApprovalFactory(release=bundle.release, decision="denied", licence=None)
    with pytest.raises(TermError, match="no effective internal approval"):
        get_or_create_term("MI:0018", "two hybrid", import_run=bundle.run)


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_commits_recording_one_term_at_once_give_one_row(settings):
    """The second insert waits on the first's uncommitted row, then finds it."""
    settings.TYPED_GRAPH_ENABLED = True
    first_run = evidence_release(records=0, synthetic=True).run
    second_run = evidence_release(records=0, synthetic=True).run
    holding, go = threading.Event(), threading.Event()
    results, errors = {}, []

    def first():
        try:
            with transaction.atomic():
                results["first"] = get_or_create_term(
                    "MI:0018", "two hybrid", import_run=first_run
                )
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def second():
        try:
            holding.wait(30)
            results["second"] = get_or_create_term(
                "MI:0018", "two-hybrid", import_run=second_run
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first transaction never inserted"
        assert _a_backend_waits_on_a_lock(), "the second insert never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)

    assert errors == []
    assert (results["first"].created, results["second"].created) == (True, False)
    assert "keeps its label 'two hybrid'" in results["second"].label_conflict
    assert Term.objects.count() == 1
    assert Term.objects.get().label_release_id == first_run.release_id


# ── term_label (graph.summary) ──────────────────────────────────────────────


def _visitors():
    return visible_release_ids(None, Purpose.VIEW)


def _labelled(curie, label, status=PUBLISHED, *, public=True):
    """A term whose label a release supplied, that release then taken to
    this status."""
    bundle = evidence_release(records=0)
    if public:
        approve(bundle.release, ApprovalScope.PUBLIC_VIEW)
    term = get_or_create_term(curie, label, import_run=bundle.run).term
    advance(bundle.release, status)
    return term


def test_a_crosswalk_term_shows_its_pinned_label_to_anyone(switch_on):
    releases = _visitors()
    assert term_label("MI:0407", releases=releases) == "direct interaction"
    assert term_label(" mi:0915", releases=releases) == "physical association"
    # Whatever label a commit stated, and whichever release supplied it.
    term = _labelled("MI:0914", "co-purification", WITHDRAWN)
    assert term_label(term, releases=releases) == "association"


def test_a_label_shows_only_when_the_release_that_supplied_it_is_visible(switch_on):
    shown = _labelled("MI:0018", "two hybrid")
    unapproved = _labelled("MI:0019", "coimmunoprecipitation", public=False)
    withdrawn = _labelled("MI:0020", "transcriptional complementation assay", WITHDRAWN)
    draft = _labelled("ECO:0000353", "physical interaction evidence", DRAFT)
    unlabelled = TermFactory(curie="ECO:0000021", label="physical interaction")
    releases = _visitors()
    assert term_label(shown, releases=releases) == "two hybrid"
    assert term_label("MI:0018", releases=releases) == "two hybrid"
    for term in (unapproved, withdrawn, draft, unlabelled):
        assert term_label(term, releases=releases) is None
    # Staff see the release approved for internal use only.
    staff = RequestFactory().get("/")
    staff.user = StaffUserFactory()
    staff_releases = visible_release_ids(staff, Purpose.VIEW)
    assert term_label(unapproved, releases=staff_releases) == "coimmunoprecipitation"
    assert term_label("MI:9999", releases=releases) is None
    assert term_label("GO:0005737", releases=releases) is None


def test_a_stale_or_hand_built_set_shows_no_label(switch_on):
    term = _labelled("MI:0018", "two hybrid")
    hidden = _labelled("MI:0019", "coimmunoprecipitation", public=False)
    releases = _visitors()
    forged = ReleaseSet(releases.ids, releases.superseded)
    # Widened from an issued set to a release visitors may not see.
    widened = dataclasses.replace(
        releases, ids=releases.ids | {hidden.label_release_id}
    )
    for hand_built in (forged, widened):
        with pytest.raises(TypeError, match="visible_release_ids"):
            term_label(hidden, releases=hand_built)
    with pytest.raises(TypeError):
        term_label(18, releases=releases)
    move(DatasetRelease.objects.get(pk=term.label_release_id), WITHDRAWN)
    assert term_label(term, releases=releases) is None


def _synthetic_commit():
    """A synthetic draft release's running commit run; the release may be
    approved later, as the demo's is."""
    release = approvable_release(is_synthetic=True)
    file = SourceFileFactory(release=release)
    return ImportRunFactory(release=release, file=file, mode=ImportMode.COMMIT)


def test_a_label_a_synthetic_release_supplied_is_never_shown(switch_on):
    """D12: the synthetic demo, approved for everyone and published, still
    labels no term that real evidence may name; a crosswalk term it recorded
    keeps its pinned label."""
    run = _synthetic_commit()
    assert not run.release.approvals.exists()
    term = get_or_create_term("MI:0018", "SYN-two hybrid", import_run=run).term
    get_or_create_term("MI:0915", "physical association", import_run=run)
    assert stored(term)["label_release_id"] == run.release_id
    approve(run.release, ApprovalScope.INTERNAL, ApprovalScope.PUBLIC_VIEW)
    advance(run.release, PUBLISHED)
    staff = RequestFactory().get("/")
    staff.user = StaffUserFactory()
    for releases in (_visitors(), visible_release_ids(staff, Purpose.VIEW)):
        assert run.release_id in releases.ids
        assert term_label(term, releases=releases) is None
        assert term_label("MI:0915", releases=releases) == "physical association"


def test_a_synthetic_commit_after_a_real_one_never_relabels_a_term(switch_on):
    term = _labelled("MI:0018", "two hybrid")
    result = get_or_create_term(
        "MI:0018", "SYN-two hybrid", import_run=_synthetic_commit()
    )
    assert not result.created
    assert "keeps its label 'two hybrid'" in result.label_conflict
    assert term_label(term, releases=_visitors()) == "two hybrid"


def test_term_label_returns_nothing_while_the_switch_is_off(switch_on, settings):
    _labelled("MI:0018", "two hybrid")
    releases = _visitors()
    settings.TYPED_GRAPH_ENABLED = False
    assert term_label("MI:0018", releases=releases) is None
    assert term_label("MI:0407", releases=releases) is None
