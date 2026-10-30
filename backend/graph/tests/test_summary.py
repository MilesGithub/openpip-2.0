"""graph.summary: edge summaries over the evidence a caller may see
(architecture §4.5), on factory rows, through visible_release_ids. Each
check names the filter it uses; without one it is the default, which covers
supported assertions."""

import dataclasses
import datetime
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from graph.models import (
    ApprovalScope,
    ContextFacet,
    InteractionLevel,
    MethodRole,
    Outcome,
    RelationshipType,
)
from graph.summary import (
    ALL_OUTCOMES,
    ContextMatch,
    EvidenceFilter,
    Observation,
    ScoreRange,
    covered_assertions,
    evidence,
    independence_links,
    independent_count,
    summarize,
    term_label,
)
from graph.terms import get_or_create_term
from graph.tests.factories import (
    AssertionParticipantFactory,
    EvidenceAssertionFactory,
    EvidenceContextFactory,
    EvidenceMethodFactory,
    EvidencePublicationFactory,
    EvidenceScoreFactory,
    PublicationFactory,
    RelationshipFactory,
    ScoreTypeFactory,
    StaffUserFactory,
    TermFactory,
    approve,
    entity_of,
    evidence_release,
    genetic_type,
    mapped_record,
    taxon_of,
)
from graph.tests.test_provenance import (
    DRAFT,
    PUBLISHED,
    STAGED,
    SUPERSEDED,
    WITHDRAWN,
    advance,
    move,
)
from graph.visibility import NOTHING, Purpose, ReleaseSet, visible_release_ids

PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
NOTHING_COUNTED = {outcome: 0 for outcome in Outcome.values}


def counted(**counts) -> dict:
    """Counts by outcome, every outcome present."""
    return {**NOTHING_COUNTED, **counts}


def kind(slug):
    return RelationshipType.objects.get(slug=slug)


def regulation_edge(regulator=None, target=None):
    return RelationshipFactory(
        relationship_type=kind("transcriptional_regulation"),
        subject=regulator or entity_of("gene"),
        object=target or entity_of("gene"),
    )


def physical_edge():
    return RelationshipFactory(
        relationship_type=kind("physical_interaction"),
        subject=entity_of("protein"),
        object=entity_of("protein"),
    )


def genetic_edge():
    return RelationshipFactory(
        relationship_type=genetic_type(),
        subject=entity_of("gene"),
        object=entity_of("gene"),
    )


def release(*, public=True, **fields):
    """A draft release ready for evidence, with an effective internal
    approval and, unless public=False, one for public view."""
    ev = evidence_release(records=0, **fields)
    if public:
        approve(ev.release, PUBLIC_VIEW)
    return ev


def asserted(
    ev, relationship, *, methods=(), paper=None, group="", other_methods=(), **fields
):
    """An assertion of the release, with these detection methods, methods of
    other roles ((CURIE, role) pairs), and the paper that reports it."""
    made = EvidenceAssertionFactory(
        relationship=relationship,
        release=ev.release,
        import_run=ev.run,
        source_record=mapped_record(ev.release, ev.run),
        evidence_group=group,
        **fields,
    )
    for curie in methods:
        EvidenceMethodFactory(assertion=made, term=TermFactory(curie=curie))
    for curie, role in other_methods:
        EvidenceMethodFactory(assertion=made, term=TermFactory(curie=curie), role=role)
    if paper is not None:
        EvidencePublicationFactory(assertion=made, publication=paper)
    return made


def publish(*evs):
    for ev in evs:
        advance(ev.release, PUBLISHED)


def request_for(user):
    request = RequestFactory().get("/")
    request.user = AnonymousUser() if user is None else user
    return request


def visitor(pinned=None):
    return visible_release_ids(request_for(None), Purpose.VIEW, pinned)


def staff(pinned=None):
    return visible_release_ids(request_for(StaffUserFactory()), Purpose.VIEW, pinned)


def summary(relationship, releases=None, **filters):
    """The edge's summary under EvidenceFilter(**filters), or None when it
    has no visible assertion."""
    releases = visitor() if releases is None else releases
    found = summarize(
        [relationship.pk], releases=releases, filters=EvidenceFilter(**filters)
    )
    return found.get(relationship.pk)


@pytest.fixture
def ev(registry):
    return release()


# ── Direction and effect ────────────────────────────────────────────────────


def test_a_to_b_and_b_to_a_are_summarized_apart(ev):
    a, b = entity_of("gene"), entity_of("gene")
    forward, reverse = regulation_edge(a, b), regulation_edge(b, a)
    asserted(ev, forward, effect="activating")
    asserted(ev, reverse, effect="inhibiting")
    publish(ev)
    found = summarize([forward.pk, reverse.pk], releases=visitor())
    assert (found[forward.pk].effect, found[reverse.pk].effect) == (
        "activating",
        "inhibiting",
    )
    assert (found[forward.pk].subject_id, found[forward.pk].object_id) == (a.pk, b.pk)
    assert (found[reverse.pk].subject_id, found[reverse.pk].object_id) == (b.pk, a.pk)
    assert found[forward.pk].orientation == "directed"
    assert found[forward.pk].relationship_type == "transcriptional_regulation"


def test_activating_and_inhibiting_together_are_mixed(ev):
    """Default filter."""
    edge = regulation_edge()
    for effect in ("activating", "inhibiting", "unknown"):
        asserted(ev, edge, effect=effect)
    publish(ev)
    found = summary(edge)
    assert found.effect == "mixed"
    assert found.effect_counts == {
        "activating": 1,
        "inhibiting": 1,
        "unknown": 1,
        "not_stated": 0,
    }


def test_activating_with_unknown_reads_activating(ev):
    """Default filter."""
    edge = regulation_edge()
    for effect in ("activating", "unknown", None):
        asserted(ev, edge, effect=effect)
    publish(ev)
    found = summary(edge)
    assert found.effect == "activating"
    assert found.effect_counts == {
        "activating": 1,
        "inhibiting": 0,
        "unknown": 1,
        "not_stated": 1,
    }


def test_unknown_and_not_stated_both_read_unknown_and_are_counted_apart(ev):
    """Default filter."""
    stated_unknown, silent = regulation_edge(), regulation_edge()
    asserted(ev, stated_unknown, effect="unknown")
    asserted(ev, stated_unknown)
    asserted(ev, silent)
    publish(ev)
    found = summarize([stated_unknown.pk, silent.pk], releases=visitor())
    assert found[stated_unknown.pk].effect == "unknown"
    assert found[stated_unknown.pk].effect_counts == {
        "activating": 0,
        "inhibiting": 0,
        "unknown": 1,
        "not_stated": 1,
    }
    assert found[silent.pk].effect == "unknown"
    assert found[silent.pk].effect_counts["not_stated"] == 1


def test_a_type_without_effect_polarity_reads_not_applicable(ev):
    edge = physical_edge()
    asserted(ev, edge, interaction_level="not_stated")
    publish(ev)
    found = summary(edge)
    assert (found.effect, found.genetic_sign) == ("not_applicable", "not_applicable")


# ── Outcomes ────────────────────────────────────────────────────────────────


def test_a_tested_absent_only_edge_is_hidden_by_default(ev):
    """Default filter, then outcomes={tested_absent}."""
    edge = regulation_edge()
    asserted(ev, edge, outcome=Outcome.TESTED_ABSENT)
    publish(ev)
    found = summary(edge)
    assert found.shown is False
    assert found.outcome_counts == counted(tested_absent=1)
    assert (found.assertion_count, found.evidence_count) == (0, 0)
    assert found.release_ids == ()  # C is empty though V is not
    # Never part of the effect summary.
    assert found.effect is None and found.effect_counts["not_stated"] == 0
    shown = summary(edge, outcomes={Outcome.TESTED_ABSENT})
    assert shown.shown is True
    assert (shown.effect, shown.assertion_count) == (None, 0)
    assert shown.release_ids == (ev.release.pk,)


def test_a_tested_absent_edge_shows_the_level_it_was_tested_for(ev):
    """Default filter, then levels={association}."""
    TermFactory(curie="MI:0407")
    edge = physical_edge()
    asserted(
        ev,
        edge,
        outcome=Outcome.TESTED_ABSENT,
        interaction_level=InteractionLevel.DIRECT_INTERACTION,
        interaction_type_id="MI:0407",
    )
    asserted(
        ev,
        edge,
        outcome=Outcome.INCONCLUSIVE,
        interaction_level=InteractionLevel.ASSOCIATION,
    )
    publish(ev)
    found = summary(edge)
    assert found.shown is False
    assert found.level is None and found.level_assigned is None
    assert found.tested_levels == {
        "tested_absent": ("direct_interaction",),
        "inconclusive": ("association",),
    }
    # Over F: an assertion outside the filters is not listed.
    assert summary(edge, levels={"association"}).tested_levels == {
        "inconclusive": ("association",)
    }


def test_not_significant_and_inconclusive_are_counted_apart_and_never_support(ev):
    """Default filter, then every outcome."""
    edge = regulation_edge()
    asserted(ev, edge, outcome=Outcome.NOT_SIGNIFICANT)
    asserted(ev, edge, outcome=Outcome.INCONCLUSIVE)
    asserted(ev, edge, outcome=Outcome.INCONCLUSIVE)
    publish(ev)
    found = summary(edge)
    assert found.shown is False
    assert found.outcome_counts == counted(inconclusive=2, not_significant=1)
    everything = summary(edge, outcomes=ALL_OUTCOMES)
    assert everything.shown is True
    assert (everything.assertion_count, everything.evidence_count) == (0, 0)
    assert everything.effect is None


def test_a_shown_edge_shows_the_evidence_against_it(ev):
    """Default filter: outcome counts are over every filtered assertion."""
    edge = regulation_edge()
    asserted(ev, edge, effect="activating")
    asserted(ev, edge, outcome=Outcome.TESTED_ABSENT)
    publish(ev)
    found = summary(edge)
    assert found.shown is True
    assert found.outcome_counts == counted(supported=1, tested_absent=1)
    assert found.assertion_count == 1 and found.effect == "activating"


# ── Genetic signs ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "signs,expected",
    [
        (("negative",), "negative"),
        (("positive", "positive"), "positive"),
        (("negative", "positive"), "mixed"),
        ((None,), "not_stated"),
        (("negative", None), "negative"),
    ],
)
def test_the_genetic_sign_summary(ev, signs, expected):
    """Default filter."""
    edge = genetic_edge()
    for sign in signs:
        asserted(ev, edge, genetic_sign=sign)
    publish(ev)
    found = summary(edge)
    assert found.genetic_sign == expected
    assert found.effect == "not_applicable"
    assert found.sign_counts == {
        "negative": signs.count("negative"),
        "positive": signs.count("positive"),
        "not_stated": signs.count(None),
    }


def test_a_tested_absent_assertion_is_never_read_as_a_negative_sign(ev):
    """Default filter, then every outcome."""
    edge = genetic_edge()
    asserted(ev, edge, outcome=Outcome.TESTED_ABSENT)
    asserted(ev, edge, outcome=Outcome.NOT_SIGNIFICANT)
    publish(ev)
    for found in (summary(edge), summary(edge, outcomes=ALL_OUTCOMES)):
        assert found.genetic_sign is None
        assert found.sign_counts == {"negative": 0, "positive": 0, "not_stated": 0}


# ── Physical levels ─────────────────────────────────────────────────────────


def test_the_strongest_supported_level_is_shown(ev):
    """Default filter: association plus a tested-absent direct interaction
    reads association."""
    TermFactory(curie="MI:0407")
    edge = physical_edge()
    asserted(ev, edge, interaction_level=InteractionLevel.ASSOCIATION)
    asserted(
        ev,
        edge,
        outcome=Outcome.TESTED_ABSENT,
        interaction_level=InteractionLevel.DIRECT_INTERACTION,
        interaction_type_id="MI:0407",
    )
    publish(ev)
    found = summary(edge)
    assert found.level == "association"
    assert found.level_counts == {
        "direct_interaction": 0,
        "physical_association": 0,
        "association": 1,
        "not_stated": 0,
    }
    assert found.tested_levels == {"tested_absent": ("direct_interaction",)}


def test_tested_absent_levels_never_set_the_level_whatever_outcomes_are_covered(ev):
    """outcomes=ALL_OUTCOMES, then {supported, tested_absent}: covered
    tested-absent assertions never set the level, its counts or
    level_assigned (D20, B14)."""
    for curie in ("MI:0407", "MI:0914"):
        TermFactory(curie=curie)
    edge = physical_edge()
    asserted(ev, edge, interaction_level=InteractionLevel.ASSOCIATION)
    for level, curie in (
        (InteractionLevel.DIRECT_INTERACTION, "MI:0407"),
        (InteractionLevel.ASSOCIATION, "MI:0914"),
    ):
        asserted(
            ev,
            edge,
            outcome=Outcome.TESTED_ABSENT,
            interaction_level=level,
            interaction_type_id=curie,
        )
    publish(ev)
    for outcomes in (ALL_OUTCOMES, {Outcome.SUPPORTED, Outcome.TESTED_ABSENT}):
        found = summary(edge, outcomes=outcomes)
        assert (found.level, found.level_assigned) == ("association", True)
        assert found.level_counts == {
            "direct_interaction": 0,
            "physical_association": 0,
            "association": 1,
            "not_stated": 0,
        }


def test_a_stronger_supported_level_wins(ev):
    """Default filter."""
    for curie in ("MI:0914", "MI:0915"):
        TermFactory(curie=curie)
    edge = physical_edge()
    asserted(ev, edge, interaction_level="association", interaction_type_id="MI:0914")
    asserted(ev, edge, interaction_level="not_stated")
    asserted(
        ev,
        edge,
        interaction_level="physical_association",
        interaction_type_id="MI:0915",
    )
    publish(ev)
    found = summary(edge)
    assert (found.level, found.level_assigned) == ("physical_association", False)


@pytest.mark.parametrize(
    "stated,assigned",
    [
        # (level, interaction type) of each supporting assertion.
        ((("association", None),), True),
        ((("association", "MI:0914"),), False),
        ((("association", None), ("association", "MI:0914")), False),
        ((("association", None), ("direct_interaction", "MI:0407")), False),
        # Not stated is no assignment: openPIP assigns association only.
        ((("not_stated", None),), False),
    ],
)
def test_level_assigned_says_the_level_rests_on_openpips_assignment(
    ev, stated, assigned
):
    """Default filter (open question B14)."""
    edge = physical_edge()
    for level, curie in stated:
        if curie:
            TermFactory(curie=curie)
        asserted(ev, edge, interaction_level=level, interaction_type_id=curie)
    publish(ev)
    assert summary(edge).level_assigned is assigned


def test_an_unspecified_physical_interaction_rests_on_no_assignment(ev):
    """Default filter: protein_interaction_unspecified states no type, at
    not_stated, and nothing is promoted on openPIP's authority."""
    edge = RelationshipFactory(
        relationship_type=kind("protein_interaction_unspecified"),
        subject=entity_of("protein"),
        object=entity_of("protein"),
    )
    asserted(ev, edge, interaction_level=InteractionLevel.NOT_STATED)
    publish(ev)
    found = summary(edge)
    assert (found.level, found.level_assigned) == ("not_stated", False)


def test_level_fields_belong_to_the_physical_family(ev):
    edge = regulation_edge()
    asserted(ev, edge, effect="activating")
    publish(ev)
    found = summary(edge)
    assert (found.level, found.level_counts, found.level_assigned) == (None, {}, None)
    assert found.tested_levels == {}


# ── Independent evidence ────────────────────────────────────────────────────


def counts(edge, releases=None, **filters):
    found = summary(edge, releases, **filters)
    return found.assertion_count, found.evidence_count


def test_one_paper_and_method_twice_is_one_observation(ev):
    """Default filter."""
    edge, paper = regulation_edge(), PublicationFactory()
    for _ in range(2):
        asserted(ev, edge, methods=("MI:0018",), paper=paper)
    publish(ev)
    assert counts(edge) == (2, 1)


def test_only_detection_methods_decide_independence(ev):
    """Default filter: methods of other roles never split or join
    observations."""
    edge, paper = regulation_edge(), PublicationFactory()
    asserted(ev, edge, methods=("MI:0018",), paper=paper)
    asserted(
        ev,
        edge,
        paper=paper,
        other_methods=(("MI:0396", MethodRole.PARTICIPANT_IDENTIFICATION),),
    )
    asserted(
        ev,
        edge,
        paper=paper,
        other_methods=(("ECO:0000353", MethodRole.EVIDENCE_CODE),),
    )
    joined, other = regulation_edge(), PublicationFactory()
    for detection in ("MI:0018", "MI:0019"):
        asserted(
            ev,
            joined,
            methods=(detection,),
            paper=other,
            other_methods=(("MI:0396", MethodRole.PARTICIPANT_IDENTIFICATION),),
        )
    publish(ev)
    assert counts(edge) == (3, 1)
    assert counts(joined) == (2, 2)


@pytest.mark.parametrize(
    "methods,expected",
    [
        ((("MI:0018", "MI:0019"), ("MI:0019",)), 1),
        ((("MI:0018",), ()), 1),
        ((("MI:0018",), ("MI:0019",)), 2),
        # Two depths of one PSI-MI branch count twice until V1.
        ((("MI:0018",), ("MI:0397",)), 2),
        # A record that names no method links the others of its paper.
        ((("MI:0018",), (), ("MI:0019",)), 1),
    ],
)
def test_methods_decide_which_records_of_one_paper_are_one_observation(
    ev, methods, expected
):
    """Default filter."""
    edge, paper = regulation_edge(), PublicationFactory()
    for named in methods:
        asserted(ev, edge, methods=named, paper=paper)
    publish(ev)
    assert counts(edge) == (len(methods), expected)


def test_two_datasets_citing_one_paper_with_the_same_methods_count_once(ev):
    """Default filter."""
    other = release()
    edge, paper = regulation_edge(), PublicationFactory()
    asserted(ev, edge, methods=("MI:0018",), paper=paper)
    asserted(other, edge, methods=("MI:0018",), paper=paper)
    publish(ev, other)
    found = summary(edge)
    assert (found.assertion_count, found.evidence_count) == (2, 1)
    assert (found.distinct_datasets, found.distinct_releases) == (2, 2)
    assert found.distinct_publications == 1


def test_evidence_without_a_publication_in_two_datasets_counts_twice(ev):
    """Default filter."""
    other = release()
    edge = regulation_edge()
    asserted(ev, edge, methods=("MI:0018",))
    asserted(other, edge, methods=("MI:0018",))
    publish(ev, other)
    assert counts(edge) == (2, 2)
    assert summary(edge).distinct_publications == 0


def test_only_a_reported_in_paper_is_an_origin_or_a_counted_publication(ev):
    """Default filter: a cited or method_reference paper is neither the
    origin of an assertion nor one of its counted publications."""
    cited, paper = PublicationFactory(), PublicationFactory()
    edge = regulation_edge()
    asserted(ev, edge, methods=("MI:0018",), paper=paper)
    also_cites = asserted(ev, edge, methods=("MI:0018",))
    # Linked before its reported_in paper, so a read of any role meets it first.
    EvidencePublicationFactory(assertion=also_cites, publication=cited, role="cited")
    EvidencePublicationFactory(assertion=also_cites, publication=paper)
    unreported = regulation_edge()
    cites = asserted(ev, unreported, methods=("MI:0018",))
    EvidencePublicationFactory(
        assertion=cites, publication=cited, role="method_reference"
    )
    asserted(ev, unreported, methods=("MI:0018",))
    publish(ev)
    found = summary(edge)
    assert (found.assertion_count, found.evidence_count) == (2, 1)
    assert found.distinct_publications == 1
    # No reported_in paper: the origin is the dataset, as for a record of none.
    found = summary(unreported)
    assert (found.assertion_count, found.evidence_count) == (2, 1)
    assert found.distinct_publications == 0


def test_distinct_counts_read_covered_supporting_assertions_only(ev):
    """outcomes=ALL_OUTCOMES: a covered tested-absent assertion from another
    paper, release and dataset adds to no distinct count and no basis."""
    other = release()
    edge = regulation_edge()
    asserted(ev, edge, paper=PublicationFactory())
    asserted(
        other,
        edge,
        outcome=Outcome.TESTED_ABSENT,
        paper=PublicationFactory(),
        evidence_basis="text_mining",
    )
    publish(ev, other)
    found = summary(edge, outcomes=ALL_OUTCOMES)
    assert (
        found.distinct_publications,
        found.distinct_releases,
        found.distinct_datasets,
    ) == (1, 1, 1)
    assert found.by_basis == {"experimental": 1}


def test_a_restatement_in_a_later_release_of_one_dataset_counts_once(ev):
    """Default filter, then both releases pinned (D12 "No publication")."""
    edge = regulation_edge()
    asserted(ev, edge, evidence_basis="computational_prediction")
    publish(ev)
    v2 = release(dataset=ev.release.dataset, supersedes=ev.release)
    asserted(v2, edge, evidence_basis="computational_prediction")
    publish(v2)
    move(ev.release, SUPERSEDED)
    assert counts(edge) == (1, 1)
    pinned = visitor([ev.release.pk, v2.release.pk])
    assert counts(edge, pinned) == (2, 1)
    found = summary(edge, pinned)
    assert found.release_ids == (ev.release.pk, v2.release.pk)
    assert found.superseded_release_ids == (ev.release.pk,)
    assert found.distinct_datasets == 1


def test_one_evidence_group_is_one_observation(ev):
    """Default filter: records of one purification, with different papers
    and methods, count once."""
    edge = regulation_edge()
    asserted(ev, edge, methods=("MI:0018",), paper=PublicationFactory(), group="AP1")
    asserted(ev, edge, methods=("MI:0019",), paper=PublicationFactory(), group="AP1")
    asserted(ev, edge, methods=("MI:0019",), paper=PublicationFactory(), group="AP2")
    publish(ev)
    assert counts(edge) == (3, 2)


def test_one_group_label_in_two_releases_is_two_groups(ev):
    """Default filter."""
    other = release()
    edge = regulation_edge()
    asserted(ev, edge, methods=("MI:0018",), paper=PublicationFactory(), group="AP1")
    asserted(other, edge, methods=("MI:0019",), paper=PublicationFactory(), group="AP1")
    publish(ev, other)
    assert counts(edge) == (2, 2)


def test_independent_outcome_counts_count_observations_per_outcome(ev):
    """Default filter: over every filtered assertion, one outcome at a
    time."""
    edge, paper = regulation_edge(), PublicationFactory()
    for outcome in (Outcome.SUPPORTED, Outcome.TESTED_ABSENT, Outcome.TESTED_ABSENT):
        asserted(ev, edge, methods=("MI:0018",), paper=paper, outcome=outcome)
    publish(ev)
    found = summary(edge)
    assert found.outcome_counts == counted(supported=1, tested_absent=2)
    assert found.independent_outcome_counts == counted(supported=1, tested_absent=1)


def test_independence_links_records_into_connected_observations():
    def seen(
        pk, methods=(), origin=("publication", 1), group=None, outcome="supported"
    ):
        return Observation(pk, outcome, origin, frozenset(methods), group)

    links = independence_links(
        [
            seen(5, ["MI:0018"]),
            seen(3, ["MI:0018", "MI:0019"]),
            seen(9, ["MI:0019"]),
            seen(4, ["MI:0020"]),
            # Another paper and a dataset, in one group of one release.
            seen(7, ["MI:0021"], origin=("publication", 2), group=(1, "g")),
            seen(8, ["MI:0022"], origin=("dataset", 1), group=(1, "g")),
            # The same group in another release is another group.
            seen(6, ["MI:0023"], origin=("dataset", 2), group=(2, "g")),
            # Another outcome is never the same observation.
            seen(2, ["MI:0018"], outcome="tested_absent"),
        ]
    )
    assert links == {5: 3, 3: 3, 9: 3, 4: 4, 7: 7, 8: 7, 6: 6, 2: 2}
    assert independent_count([]) == 0


# ── Filters ─────────────────────────────────────────────────────────────────


def test_a_filter_covers_some_assertions_and_counts_the_rest_outside(ev):
    """evidence_bases={experimental}, then the default filter, then
    outcomes=ALL_OUTCOMES."""
    edge = regulation_edge()
    asserted(ev, edge, effect="activating")
    asserted(ev, edge, effect="inhibiting", evidence_basis="computational_prediction")
    asserted(ev, edge, outcome=Outcome.TESTED_ABSENT, evidence_basis="text_mining")
    publish(ev)
    found = summary(edge, evidence_bases={"experimental"})
    assert (found.assertion_count, found.effect) == (1, "activating")
    assert found.by_basis == {"experimental": 1}
    assert found.outside_filters == 2
    assert found.outside_outcome_counts == counted(supported=1, tested_absent=1)
    assert found.outcome_counts == counted(supported=1)
    assert found.independent_outcome_counts == counted(supported=1)
    unfiltered = summary(edge)
    assert unfiltered.by_basis == {"experimental": 1, "computational_prediction": 1}
    assert unfiltered.effect == "mixed" and unfiltered.outside_filters == 0
    # By basis reads covered support: the covered tested-absent row is not in it.
    assert summary(edge, outcomes=ALL_OUTCOMES).by_basis == {
        "experimental": 1,
        "computational_prediction": 1,
    }


def test_score_ranges_apply_to_their_own_type_and_never_pool(ev):
    """scores=(ScoreRange(stats:test_a, 0.5),)."""
    first, second = (
        ScoreTypeFactory(slug="stats:test_a"),
        ScoreTypeFactory(slug="stats:test_b"),
    )
    edge = regulation_edge()
    a = asserted(ev, edge)
    b = asserted(ev, edge)
    unread = asserted(ev, edge)
    EvidenceScoreFactory(assertion=a, score_type=first, raw_value="0.9", value=0.9)
    EvidenceScoreFactory(assertion=b, score_type=second, raw_value="0.9", value=0.9)
    EvidenceScoreFactory(
        assertion=unread, score_type=first, raw_value="NaN", value=None
    )
    publish(ev)
    assert (
        summary(edge, scores=(ScoreRange("stats:test_a", min=0.5),)).assertion_count
        == 1
    )
    assert (
        summary(edge, scores=(ScoreRange("stats:test_a", max=0.5),)).assertion_count
        == 0
    )
    # A NULL value never matches, not even an open range.
    assert summary(edge, scores=(ScoreRange("stats:test_a"),)).assertion_count == 1
    both = (ScoreRange("stats:test_a", 0, 1), ScoreRange("stats:test_b", 0, 1))
    assert summary(edge, scores=both).assertion_count == 0
    assert summary(edge, scores=(ScoreRange("stats:test_c"),)).shown is False


def test_an_absent_facet_never_matches(ev):
    """contexts=(ContextMatch(...),)."""
    TermFactory(curie="MI:0018")
    edge = regulation_edge()
    plain = asserted(ev, edge)
    tissue = asserted(ev, edge)
    host = asserted(ev, edge)
    EvidenceContextFactory(
        assertion=tissue, facet=ContextFacet.TISSUE, raw_value="liver"
    )
    EvidenceContextFactory(
        assertion=host,
        facet=ContextFacet.HOST_TAXON,
        taxon=taxon_of(9606),
        raw_value="taxid:9606",
    )
    publish(ev)
    assert plain.pk  # stated nothing: matched by no context filter
    assert summary(edge, contexts=(ContextMatch("tissue"),)).assertion_count == 1
    assert (
        summary(edge, contexts=(ContextMatch("tissue", raw="liver"),)).assertion_count
        == 1
    )
    assert summary(edge, contexts=(ContextMatch("tissue", raw="lung"),)).shown is False
    assert (
        summary(
            edge, contexts=(ContextMatch("host_taxon", taxid=9606),)
        ).assertion_count
        == 1
    )
    both = (ContextMatch("tissue"), ContextMatch("host_taxon"))
    assert summary(edge, contexts=both).shown is False
    assert summary(edge, contexts=(ContextMatch("cell_line"),)).shown is False


def test_detection_methods_match_any_one_named(ev):
    """detection_methods={...}."""
    edge = regulation_edge()
    asserted(ev, edge, methods=("MI:0018",))
    asserted(ev, edge, methods=("MI:0019", "MI:0020"))
    asserted(ev, edge)
    publish(ev)
    assert summary(edge, detection_methods={"MI:0018"}).assertion_count == 1
    assert summary(edge, detection_methods={"mi:0020", "MI:0018"}).assertion_count == 2
    assert summary(edge, detection_methods={"MI:0021"}).shown is False


def test_the_detection_filter_ignores_methods_of_other_roles(ev):
    """detection_methods={MI:0396}, then {MI:0018}."""
    edge = regulation_edge()
    asserted(ev, edge, methods=("MI:0018",))
    asserted(
        ev,
        edge,
        other_methods=(("MI:0396", MethodRole.PARTICIPANT_IDENTIFICATION),),
    )
    publish(ev)
    found = summary(edge, detection_methods={"MI:0396"})
    assert (found.shown, found.assertion_count, found.outside_filters) == (
        False,
        0,
        2,
    )
    assert summary(edge, detection_methods={"MI:0018"}).assertion_count == 1


def test_type_specific_filters_leave_other_types_alone(ev):
    """effects, genetic_signs and levels, on a view of three layers."""
    regulation, physical, genetic = regulation_edge(), physical_edge(), genetic_edge()
    asserted(ev, regulation, effect="activating")
    asserted(ev, physical, interaction_level="association")
    asserted(ev, genetic, genetic_sign="negative")
    publish(ev)
    ids = [regulation.pk, physical.pk, genetic.pk]

    def shown(**filters):
        found = summarize(ids, releases=visitor(), filters=EvidenceFilter(**filters))
        return [found[pk].shown for pk in ids]

    assert shown() == [True, True, True]
    assert shown(effects={"inhibiting"}) == [False, True, True]
    assert shown(effects={"activating", "not_stated"}) == [True, True, True]
    assert shown(levels={"direct_interaction"}) == [True, False, True]
    assert shown(genetic_signs={"positive"}) == [True, True, False]
    assert shown(genetic_signs={"negative"}, levels={"association"}) == [
        True,
        True,
        True,
    ]


def test_effect_and_sign_filters_keep_the_evidence_against_an_edge(ev):
    """effects={activating}, then {inhibiting}; genetic_signs={negative}, then
    {positive}. Only a supported assertion carries an effect or a sign, so
    these filters leave the other outcomes in F, and outcome counts show the
    evidence against the edge whatever the filter asks for."""
    regulation, genetic = regulation_edge(), genetic_edge()
    asserted(ev, regulation, effect="activating", methods=("MI:0018",))
    for outcome in (
        Outcome.TESTED_ABSENT,
        Outcome.NOT_SIGNIFICANT,
        Outcome.INCONCLUSIVE,
    ):
        asserted(ev, regulation, outcome=outcome, methods=("MI:0018",))
    asserted(ev, genetic, genetic_sign="negative")
    for outcome in (Outcome.NOT_SIGNIFICANT, Outcome.TESTED_ABSENT):
        asserted(ev, genetic, outcome=outcome)
    publish(ev)
    every_outcome = counted(
        supported=1, tested_absent=1, inconclusive=1, not_significant=1
    )
    found = summary(regulation, effects={"activating"})
    assert (found.shown, found.effect, found.assertion_count) == (
        True,
        "activating",
        1,
    )
    assert found.outcome_counts == every_outcome
    assert found.independent_outcome_counts == every_outcome
    assert (found.outside_filters, found.outside_outcome_counts) == (
        0,
        NOTHING_COUNTED,
    )
    found = summary(regulation, effects={"inhibiting"})
    assert (found.shown, found.effect) == (False, None)
    assert found.outside_outcome_counts == counted(supported=1)
    assert found.outcome_counts == counted(
        tested_absent=1, inconclusive=1, not_significant=1
    )
    found = summary(genetic, genetic_signs={"negative"})
    assert (found.shown, found.genetic_sign) == (True, "negative")
    assert found.outcome_counts == counted(
        supported=1, tested_absent=1, not_significant=1
    )
    found = summary(genetic, genetic_signs={"positive"})
    assert (found.shown, found.genetic_sign) == (False, None)
    assert found.outside_outcome_counts == counted(supported=1)


def test_under_an_effect_filter_a_requested_outcome_against_the_edge_is_covered(ev):
    """effects={activating} with outcomes=ALL_OUTCOMES, then with
    outcomes={tested_absent}: an assertion that is not supported passes the
    effect filter, so it is covered when its outcome is asked for."""
    edge, absent_only = regulation_edge(), regulation_edge()
    activating = asserted(ev, edge, effect="activating")
    asserted(ev, edge, effect="inhibiting")
    against = [
        asserted(ev, edge, outcome=outcome)
        for outcome in (
            Outcome.TESTED_ABSENT,
            Outcome.INCONCLUSIVE,
            Outcome.NOT_SIGNIFICANT,
        )
    ]
    absent = asserted(ev, absent_only, outcome=Outcome.TESTED_ABSENT)
    publish(ev)
    covered = covered_assertions(
        releases=visitor(),
        filters=EvidenceFilter(outcomes=ALL_OUTCOMES, effects={"activating"}),
    )
    assert set(covered) == {activating, *against, absent}
    found = summary(
        absent_only, effects={"activating"}, outcomes={Outcome.TESTED_ABSENT}
    )
    assert (found.shown, found.effect, found.release_ids) == (
        True,
        None,
        (ev.release.pk,),
    )


def test_not_stated_selects_assertions_that_state_no_effect(ev):
    """effects={not_stated}."""
    edge = regulation_edge()
    asserted(ev, edge, effect="unknown")
    asserted(ev, edge)
    publish(ev)
    found = summary(edge, effects={"not_stated"})
    assert found.effect_counts == {
        "activating": 0,
        "inhibiting": 0,
        "unknown": 0,
        "not_stated": 1,
    }
    assert found.outside_filters == 1


@pytest.mark.parametrize(
    "filters,error",
    [
        ({"outcomes": {"observed"}}, ValueError),
        ({"outcomes": "supported"}, TypeError),
        ({"effects": {"negative"}}, ValueError),
        ({"genetic_signs": {"aggravating"}}, ValueError),
        ({"levels": {"binding"}}, ValueError),
        ({"evidence_bases": {"curated"}}, ValueError),
        ({"detection_methods": {"ECO:0000353"}}, ValueError),
        ({"detection_methods": {"MI:18"}}, ValueError),
        ({"scores": (("stats:test_a", 0, 1),)}, TypeError),
        ({"contexts": ContextMatch("tissue")}, TypeError),
    ],
)
def test_a_filter_names_only_values_that_exist(filters, error):
    with pytest.raises(error):
        EvidenceFilter(**filters)


@pytest.mark.parametrize(
    "build",
    [
        lambda: ScoreRange("confidence"),
        lambda: ScoreRange("stats:test_a", min=float("nan")),
        lambda: ScoreRange("stats:test_a", max=float("inf")),
        lambda: ScoreRange("stats:test_a", min=True),
        lambda: ScoreRange("stats:test_a", min=2, max=1),
        lambda: ScoreRange("stats:test_a", min=10**400),
        lambda: ContextMatch("organism"),
        lambda: ContextMatch("tissue", term="UBERON:0002107"),
        lambda: ContextMatch("host_taxon", taxid="9606"),
        lambda: ContextMatch("tissue", raw=1),
    ],
)
def test_score_ranges_and_context_matches_are_checked(build):
    with pytest.raises(ValueError):
        build()


def test_filters_are_normalized():
    flt = EvidenceFilter(
        outcomes=["supported", "tested_absent"],
        detection_methods=["mi:0018"],
        contexts=[ContextMatch("tissue", term=" mi:0018 ")],
    )
    assert flt.outcomes == frozenset({"supported", "tested_absent"})
    assert flt.detection_methods == frozenset({"MI:0018"})
    assert flt.contexts[0].term == "MI:0018"
    assert hash(flt) == hash(EvidenceFilter(**flt.__dict__))


# ── Visibility ──────────────────────────────────────────────────────────────


def test_a_release_without_public_view_shows_visitors_nothing(registry):
    hidden = release(public=False)
    edge = regulation_edge()
    asserted(hidden, edge, effect="activating")
    publish(hidden)
    assert summary(edge, visitor()) is None
    assert summary(edge, visitor([hidden.release.pk])) is None
    assert summary(edge, staff()).effect == "activating"


def test_an_embargoed_release_shows_visitors_nothing(registry):
    embargoed = release(embargo_until=timezone.now() + datetime.timedelta(days=30))
    edge = regulation_edge()
    asserted(embargoed, edge)
    publish(embargoed)
    assert summary(edge, visitor()) is None
    assert summary(edge, staff()).assertion_count == 1


def test_a_withdrawn_release_shows_nothing(ev):
    edge = regulation_edge()
    asserted(ev, edge)
    publish(ev)
    move(ev.release, WITHDRAWN)
    for releases in (visitor(), staff(), staff([ev.release.pk])):
        assert summary(edge, releases) is None


def test_a_draft_release_shows_nothing(ev):
    edge = regulation_edge()
    asserted(ev, edge)
    assert summary(edge, staff([ev.release.pk])) is None
    assert covered_assertions(releases=staff([ev.release.pk])).count() == 0


def test_a_staged_release_shows_only_staff_who_pin_it(ev):
    edge = regulation_edge()
    asserted(ev, edge)
    advance(ev.release, STAGED)
    assert summary(edge, visitor([ev.release.pk])) is None
    assert summary(edge, staff()) is None
    assert summary(edge, staff([ev.release.pk])).assertion_count == 1


def test_a_superseded_release_counts_only_when_pinned_and_is_flagged(ev):
    edge = regulation_edge()
    asserted(ev, edge, effect="activating")
    publish(ev)
    v2 = release(dataset=ev.release.dataset, supersedes=ev.release)
    asserted(v2, edge, effect="inhibiting")
    publish(v2)
    move(ev.release, SUPERSEDED)
    default = summary(edge)
    assert (default.effect, default.release_ids) == ("inhibiting", (v2.release.pk,))
    assert default.superseded_release_ids == ()
    pinned = summary(edge, visitor([ev.release.pk, v2.release.pk]))
    assert pinned.effect == "mixed"
    assert pinned.superseded_release_ids == (ev.release.pk,)
    # Of C: the superseded release's assertion lies outside effects={inhibiting}.
    narrowed = summary(
        edge, visitor([ev.release.pk, v2.release.pk]), effects={"inhibiting"}
    )
    assert (narrowed.release_ids, narrowed.superseded_release_ids) == (
        (v2.release.pk,),
        (),
    )


def test_a_release_set_built_by_hand_is_refused(ev):
    """Also one derived from an issued set: widened to a release visitors may
    not see (approved for internal use only), it would show that release."""
    edge = regulation_edge()
    asserted(ev, edge)
    hidden = release(public=False)
    asserted(hidden, edge)
    publish(ev, hidden)
    issued = visitor()
    forged = ReleaseSet(frozenset({ev.release.pk}), frozenset())
    assert forged == issued
    for releases in (
        forged,
        dataclasses.replace(issued, ids=issued.ids | {hidden.release.pk}),
        dataclasses.replace(NOTHING, ids=frozenset({hidden.release.pk})),
        {ev.release.pk},
        SimpleNamespace(ids={ev.release.pk}),
    ):
        for call in (
            lambda: summarize([edge.pk], releases=releases),
            lambda: covered_assertions(releases=releases),
            lambda: evidence(edge.pk, releases=releases),
        ):
            with pytest.raises(TypeError, match="visible_release_ids"):
                call()
    # The constructor takes no mark.
    with pytest.raises(TypeError):
        ReleaseSet(issued.ids, issued.superseded, issued._issued)
    assert summary(edge, issued).assertion_count == 1


@pytest.mark.parametrize("change", ["withdrawn", "discarded to draft"])
def test_a_stale_set_never_shows_a_release_since_withdrawn_or_discarded(ev, change):
    edge = regulation_edge()
    asserted(ev, edge)
    if change == "withdrawn":
        publish(ev)
        releases = visitor()
        move(ev.release, WITHDRAWN)
    else:
        advance(ev.release, STAGED)
        releases = staff([ev.release.pk])
        move(ev.release, DRAFT)
    assert releases.ids == {ev.release.pk}
    assert summarize([edge.pk], releases=releases) == {}
    assert covered_assertions(releases=releases).count() == 0
    assert list(evidence(edge.pk, releases=releases)) == []


def test_every_function_returns_nothing_while_the_switch_is_off(ev, settings):
    edge = regulation_edge()
    asserted(ev, edge)
    publish(ev)
    releases = visitor()
    settings.TYPED_GRAPH_ENABLED = False
    assert summarize([edge.pk], releases=releases) == {}
    assert list(covered_assertions(releases=releases)) == []
    assert list(evidence(edge.pk, releases=releases)) == []
    assert visible_release_ids(None, Purpose.VIEW).ids == frozenset()
    assert summarize([edge.pk], releases=visitor()) == {}


def test_a_relationship_without_visible_evidence_is_absent(ev):
    shown, unseen = regulation_edge(), regulation_edge()
    asserted(ev, shown)
    publish(ev)
    assert set(summarize([shown.pk, unseen.pk, 10**12], releases=visitor())) == {
        shown.pk
    }
    assert summarize([], releases=visitor()) == {}
    with pytest.raises(TypeError):
        summarize([str(shown.pk)], releases=visitor())
    with pytest.raises(TypeError):
        summarize(shown.pk, releases=visitor())


# ── Queries ─────────────────────────────────────────────────────────────────


def test_summarize_runs_a_constant_number_of_queries(ev, django_assert_max_num_queries):
    """Every filter kind, over one edge or many."""
    score_type = ScoreTypeFactory(slug="stats:test_value")
    edges = [regulation_edge() for _ in range(6)]
    for edge in edges:
        for effect in ("activating", "inhibiting", None):
            made = asserted(
                ev,
                edge,
                effect=effect,
                methods=("MI:0018", "MI:0019"),
                paper=PublicationFactory(),
            )
            EvidenceScoreFactory(assertion=made, score_type=score_type)
            EvidenceContextFactory(assertion=made, raw_value="liver")
    publish(ev)
    releases = visitor()
    filters = EvidenceFilter(
        outcomes=ALL_OUTCOMES,
        evidence_bases={"experimental"},
        effects={"activating", "not_stated"},
        genetic_signs={"negative"},
        detection_methods={"MI:0018"},
        levels={"association"},
        scores=(ScoreRange("stats:test_value", 0, 1),),
        contexts=(ContextMatch("tissue"),),
    )
    used = []
    for ids in ([edges[0].pk], [edge.pk for edge in edges]):
        with django_assert_max_num_queries(5):
            with CaptureQueriesContext(connection) as queries:
                found = summarize(ids, releases=releases, filters=filters)
        used.append(len(queries.captured_queries))
        assert len(found) == len(ids)
        assert {s.assertion_count for s in found.values()} == {2}
    assert used[0] == used[1], used


# ── evidence() and covered_assertions() ─────────────────────────────────────


def test_evidence_lists_every_visible_assertion_by_id(ev):
    edge = regulation_edge()
    hidden = release(public=False)
    shown = [
        asserted(
            ev,
            edge,
            effect="activating",
            methods=("MI:0018",),
            paper=PublicationFactory(),
        ),
        asserted(ev, edge, effect="inhibiting"),
        asserted(ev, edge, outcome=Outcome.TESTED_ABSENT),
        asserted(ev, edge, effect="unknown", methods=("MI:0019",)),
    ]
    asserted(hidden, edge, effect="activating")
    for made in shown:
        AssertionParticipantFactory(assertion=made)
        EvidenceScoreFactory(assertion=made)
        EvidenceContextFactory(
            assertion=made, facet="host_taxon", taxon=taxon_of(9606), raw_value="9606"
        )
    publish(ev, hidden)
    releases = visitor()
    listed = evidence(edge.pk, releases=releases)
    # Contradictory evidence included: every outcome, every effect.
    assert [row.pk for row in listed] == [row.pk for row in shown]
    used = []
    # Each page is a slice of a queryset not yet read, as a paginator takes it.
    pages = (
        evidence(edge.pk, releases=releases)[:2],
        evidence(edge.pk, releases=releases)[2:4],
    )
    for page in pages:
        with CaptureQueriesContext(connection) as queries:
            for row in page:
                assert row.release.dataset.slug and row.source_record.record_key
                assert row.eco_term is None and row.interaction_type is None
                for part in row.participants.all():
                    assert part.entity.label and part.stated_entity is None
                for method in row.methods.all():
                    assert method.term.curie
                for link in row.publications.all():
                    assert link.publication.pmid
                for score in row.scores.all():
                    assert score.score_type.label
                for context in row.contexts.all():
                    assert context.taxon.scientific_name and context.term is None
        used.append(len(queries.captured_queries))
    assert used == [6, 6]
    # With filters: the covered assertions only.
    assert [
        row.pk for row in evidence(edge.pk, releases=releases, filters=EvidenceFilter())
    ] == [
        shown[0].pk,
        shown[1].pk,
        shown[3].pk,
    ]
    with pytest.raises(TypeError):
        evidence(str(edge.pk), releases=releases)
    assert list(evidence(10**30, releases=releases)) == []


def test_a_term_evidence_loads_is_labelled_only_through_term_label(ev):
    """A visible assertion names terms whose labels a release visitors may
    not see stated (approved for internal use only): evidence() loads the
    terms, and term_label shows none of those labels."""
    hidden = release(public=False)
    for curie in ("ECO:0000353", "MI:0018", "MI:0492"):
        get_or_create_term(curie, "a label visitors may not see", import_run=hidden.run)
    get_or_create_term("MI:0019", "coimmunoprecipitation", import_run=ev.run)
    edge = regulation_edge()
    made = asserted(ev, edge, eco_term_id="ECO:0000353", methods=("MI:0018", "MI:0019"))
    EvidenceContextFactory(
        assertion=made, facet=ContextFacet.ASSAY_SYSTEM, term_id="MI:0492"
    )
    publish(ev, hidden)
    releases = visitor()
    (row,) = evidence(edge.pk, releases=releases)
    loaded = [
        row.eco_term,
        *(method.term for method in row.methods.all()),
        *(context.term for context in row.contexts.all()),
    ]
    assert {term.pk: term_label(term, releases=releases) for term in loaded} == {
        "ECO:0000353": None,
        "MI:0018": None,
        "MI:0019": "coimmunoprecipitation",
        "MI:0492": None,
    }


def test_covered_assertions_is_c_over_every_relationship(ev):
    first, second = regulation_edge(), physical_edge()
    supported = asserted(ev, first, effect="activating")
    absent = asserted(ev, first, outcome=Outcome.TESTED_ABSENT)
    physical = asserted(ev, second, interaction_level="not_stated")
    unpublished = release()
    asserted(unpublished, first)
    publish(ev)
    releases = visitor()
    assert set(covered_assertions(releases=releases)) == {supported, physical}
    everything = covered_assertions(
        releases=releases, filters=EvidenceFilter(outcomes=ALL_OUTCOMES)
    )
    assert set(everything) == {supported, absent, physical}
    counted_edges = covered_assertions(
        releases=releases, filters=EvidenceFilter(effects={"activating"})
    ).values_list("relationship_id", flat=True)
    assert sorted(counted_edges) == sorted([first.pk, second.pk])
    with pytest.raises(TypeError):
        covered_assertions(releases=releases, filters={"outcomes": {"supported"}})
