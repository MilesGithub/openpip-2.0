"""graph.claims: the rules a source record's claims are checked by, the
commit that writes them (record_claims), and the dry run's read-only check
(check_claims). Every refusal is shown to write nothing."""

import dataclasses
import datetime
import json
import re
from contextlib import contextmanager

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import RestrictedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from graph.approvals import revoke
from graph.claims import (
    RULES,
    Claim,
    ClaimConflict,
    ClaimError,
    ContextValue,
    Endpoint,
    MethodRef,
    PlannedEntity,
    PublicationRef,
    ScoreValue,
    check_claims,
    record_claim,
    record_claims,
)
from graph.models import (
    ApprovalScope,
    AssertionMethod,
    AssertionParticipant,
    DueDiligenceRecord,
    DueDiligenceStatus,
    EntityStatus,
    EvidenceAssertion,
    EvidenceBasis,
    EvidenceContext,
    EvidenceMethod,
    EvidencePublication,
    EvidenceScore,
    ImmutableRecordError,
    ImportMode,
    ImportRun,
    ImportState,
    InteractionLevel,
    Outcome,
    Publication,
    RecordStatus,
    Relationship,
    RelationshipType,
    ScoreDirection,
    ScoreValueKind,
    SourceRecord,
    Term,
    UsageApproval,
)
from graph.publications import get_or_create_publication
from graph.tests.factories import (
    AssertionParticipantFactory,
    DatasetReleaseFactory,
    EvidenceAssertionFactory,
    EvidenceContextFactory,
    EvidencePublicationFactory,
    EvidenceScoreFactory,
    ImportRunFactory,
    PublicationFactory,
    RelationshipFactory,
    ScoreTypeFactory,
    StaffUserFactory,
    TermFactory,
    entity_of,
    evidence_release,
    genetic_type,
    mapped_record,
    p_value_score_type,
    signed_score_type,
    taxon_of,
)
from graph.tests.test_provenance import (
    DELETE_PATHS,
    PUBLISHED,
    STAGED,
    SUPERSEDED,
    WITHDRAWN,
    WRITE_PATHS,
    advance,
    delete,
    stored,
    write,
)

EVIDENCE_MODELS = (
    Relationship,
    EvidenceAssertion,
    AssertionParticipant,
    EvidenceMethod,
    EvidencePublication,
    EvidenceScore,
    EvidenceContext,
)
PART_MODELS = EVIDENCE_MODELS[2:]
NOT_SUPPORTED = [Outcome.TESTED_ABSENT, Outcome.INCONCLUSIVE, Outcome.NOT_SIGNIFICANT]
P_BELOW = [{"score_type": "stats:test_p", "operator": "<", "value": "0.05"}]
GI_ABOVE = [{"score_type": "stats:test_gi", "operator": "abs>", "value": "0.08"}]


def snapshot() -> dict:
    return {model.__name__: model.objects.count() for model in EVIDENCE_MODELS}


@contextmanager
def refused(match, error=ClaimError):
    """The block raises `error` matching `match`, and writes no evidence."""
    before = snapshot()
    with pytest.raises(error, match=match) as raised:
        yield raised
    assert snapshot() == before


def rule(number) -> str:
    """How a refusal names a rule."""
    return re.escape(f"Rule {number} ({RULES[number]})")


def end(value) -> Endpoint:
    """An endpoint as a source states it, by its primary identifier."""
    if isinstance(value, Endpoint):
        return value
    if isinstance(value, PlannedEntity):
        return Endpoint(value, "planned", "primary")
    return Endpoint(value, f"SYN-{getattr(value, 'pk', value)}", "primary")


def claim(slug, subject, obj, **fields) -> Claim:
    fields.setdefault("outcome", Outcome.SUPPORTED)
    fields.setdefault("evidence_basis", EvidenceBasis.EXPERIMENTAL)
    fields.setdefault("assertion_method", AssertionMethod.MANUAL_ASSERTION)
    return Claim(
        relationship_type=slug, subject=end(subject), object=end(obj), **fields
    )


def regulation(regulator=None, target=None, **fields) -> Claim:
    return claim(
        "transcriptional_regulation",
        regulator or entity_of("gene"),
        target or entity_of("gene"),
        **fields,
    )


def physical(first=None, second=None, level=InteractionLevel.NOT_STATED, **fields):
    return claim(
        "physical_interaction",
        first or entity_of("protein"),
        second or entity_of("protein"),
        interaction_level=level,
        **fields,
    )


def genetic(**fields) -> Claim:
    genetic_type()
    return claim("test_genetic", entity_of("gene"), entity_of("gene"), **fields)


def signed(raw) -> ScoreValue:
    return ScoreValue(signed_score_type(), raw)


@pytest.fixture
def ev(registry):
    """A draft release ready for evidence: a running commit run, three mapped
    records, and an effective internal approval."""
    return evidence_release(records=3)


def record(ev, *claims, at=0):
    return record_claims(ev.records[at], list(claims), import_run=ev.run)


def kind(slug):
    return RelationshipType.objects.get(slug=slug)


# ── Writing evidence ────────────────────────────────────────────────────────


def test_a_claim_is_written_as_an_assertion_with_its_parts(ev):
    regulator, target = entity_of("gene"), entity_of("gene")
    paper = PublicationFactory()
    result = record(
        ev,
        regulation(
            regulator,
            target,
            effect="activating",
            relationship_as_stated="activates",
            effect_as_stated="up",
            evidence_group="group 1",
            eco_term=TermFactory(curie="ECO:0000353", label="physical interaction"),
            qualifiers={"tested_effect": "activation"},
            methods=(MethodRef(TermFactory(curie="MI:0018"), "detection"),),
            publications=(PublicationRef(paper, "reported_in"),),
            scores=(ScoreValue(ScoreTypeFactory(slug="stats:test_value"), "0.25"),),
            contexts=(ContextValue("tissue", "liver", label="Liver"),),
        ),
    )
    (recorded,) = result.assertions
    assert recorded.created and recorded.relationship_created
    assertion = recorded.assertion
    row = stored(assertion)
    assert {name: row[name] for name in ("assertion_key", "effect", "eco_term_id")} == {
        "assertion_key": "main",
        "effect": "activating",
        "eco_term_id": "ECO:0000353",
    }
    assert (row["release_id"], row["source_record_id"], row["import_run_id"]) == (
        ev.release.pk,
        ev.records[0].pk,
        ev.run.pk,
    )
    assert row["qualifiers"] == {"tested_effect": "activation"}
    relationship = Relationship.objects.get(pk=row["relationship_id"])
    assert (relationship.subject_id, relationship.object_id) == (
        regulator.pk,
        target.pk,
    )
    assert sorted(
        assertion.participants.values_list("side", "entity_id", "identifier_as_stated")
    ) == [
        ("object", target.pk, f"SYN-{target.pk}"),
        ("subject", regulator.pk, f"SYN-{regulator.pk}"),
    ]
    assert list(assertion.methods.values_list("term_id", "role")) == [
        ("MI:0018", "detection")
    ]
    assert list(assertion.publications.values_list("publication_id", "role")) == [
        (paper.pk, "reported_in")
    ]
    assert list(assertion.scores.values_list("raw_value", "value")) == [("0.25", 0.25)]
    assert list(assertion.contexts.values_list("facet", "raw_value", "label")) == [
        ("tissue", "liver", "Liver")
    ]
    assert result.notes == ()


def test_record_claim_records_one_claim(ev):
    result = record_claim(ev.records[0].pk, regulation(), import_run=ev.run.pk)
    assert [r.assertion.assertion_key for r in result.assertions] == ["main"]


def test_an_existing_relationship_is_reused(ev):
    regulator, target = entity_of("gene"), entity_of("gene")
    first = record(ev, regulation(regulator, target), at=0).assertions[0]
    second = record(ev, regulation(regulator, target), at=1).assertions[0]
    assert first.relationship.pk == second.relationship.pk
    assert (first.relationship_created, second.relationship_created) == (True, False)
    assert Relationship.objects.count() == 1


# ── Idempotency, per record ─────────────────────────────────────────────────


def test_the_same_claims_twice_are_a_no_op(ev):
    paper = PublicationFactory()
    claims = [
        regulation(
            effect="inhibiting",
            methods=(MethodRef("MI:0018", "detection"),),
            publications=(PublicationRef(paper, "reported_in"),),
            scores=(ScoreValue("stats:test_value", "NaN"),),
            contexts=(ContextValue("cell_line", "HeLa"),),
            key_part="r1",
        ),
        physical(key_part="p1"),
    ]
    TermFactory(curie="MI:0018")
    ScoreTypeFactory(slug="stats:test_value")
    first = record(ev, *claims)
    before = snapshot()
    again = record(ev, *claims)
    assert snapshot() == before
    assert [r.created for r in again.assertions] == [False, False]
    assert [r.relationship_created for r in again.assertions] == [False, False]
    assert [r.assertion.pk for r in again.assertions] == [
        r.assertion.pk for r in first.assertions
    ]
    # The notes are what the claims give, every time.
    assert again.notes == first.notes and len(first.notes) == 1


def test_parts_given_in_another_order_are_the_same_assertion(ev):
    for curie in ("MI:0018", "MI:0019"):
        TermFactory(curie=curie)
    regulator, target = entity_of("gene"), entity_of("gene")
    methods = (MethodRef("MI:0018", "detection"), MethodRef("MI:0019", "detection"))
    record(ev, regulation(regulator, target, methods=methods))
    again = record(ev, regulation(regulator, target, methods=methods[::-1]))
    assert not again.assertions[0].created


@pytest.mark.parametrize(
    "change,named",
    [
        ({"effect": "inhibiting"}, "effect"),
        ({"evidence_basis": "computational_prediction"}, "evidence_basis"),
        ({"effect_as_stated": "down"}, "effect_as_stated"),
        ({"evidence_group": "g2"}, "evidence_group"),
        ({"qualifiers": {"x": "2"}}, "qualifiers"),
        ({"contexts": (ContextValue("tissue", "lung"),)}, "contexts"),
    ],
)
def test_the_same_key_with_other_content_is_a_conflict(ev, change, named):
    regulator, target = entity_of("gene"), entity_of("gene")
    base = {"effect": "activating", "evidence_group": "g1", "qualifiers": {"x": "1"}}
    record(ev, regulation(regulator, target, **base))
    with refused(re.escape(f"differing main ({named})"), ClaimConflict):
        record(ev, regulation(regulator, target, **{**base, **change}))
    assert stored(EvidenceAssertion.objects.get())["effect"] == "activating"


def test_another_relationship_under_the_same_key_is_a_conflict(ev):
    record(ev, regulation())
    with refused(
        re.escape("differing main (relationship, participants)"), ClaimConflict
    ):
        record(ev, regulation())


def _same_key(first, **changes):
    """A claim and the same claim with `changes`: one key, other content."""
    return first, dataclasses.replace(first, **changes)


def _restated(made):
    """The claim, and the same claim with its subject stated another way."""
    subject = dataclasses.replace(
        made.subject, as_stated="SYN-ALIAS", basis="alias_unique"
    )
    return made, dataclasses.replace(made, subject=subject)


def _swapped(made):
    """A symmetric claim, and the same claim stated the other way round."""
    return _same_key(made, subject=made.object, object=made.subject)


@pytest.mark.parametrize(
    "named,claims",
    [
        ("outcome", lambda: _same_key(regulation(), outcome=Outcome.INCONCLUSIVE)),
        (
            "assertion_method",
            lambda: _same_key(
                regulation(), assertion_method=AssertionMethod.AUTOMATIC_ASSERTION
            ),
        ),
        (
            "genetic_sign",
            lambda: _same_key(
                genetic(genetic_sign="negative", scores=(signed("-0.3"),)),
                genetic_sign=None,
            ),
        ),
        (
            "eco_term_id",
            lambda: _same_key(
                regulation(eco_term=TermFactory(curie="ECO:0000353")),
                eco_term=TermFactory(curie="ECO:0000314"),
            ),
        ),
        (
            "interaction_type_id",
            lambda: _same_key(
                physical(level="association"),
                interaction_type=TermFactory(curie="MI:0914"),
            ),
        ),
        (
            "interaction_level",
            lambda: _same_key(physical(), interaction_level="association"),
        ),
        (
            "relationship_as_stated",
            lambda: _same_key(
                regulation(relationship_as_stated="activates"),
                relationship_as_stated="induces",
            ),
        ),
        ("participants", lambda: _restated(regulation())),
        ("participants", lambda: _swapped(physical())),
        (
            "methods",
            lambda: _same_key(
                regulation(
                    methods=(MethodRef(TermFactory(curie="MI:0018"), "detection"),)
                ),
                methods=(MethodRef(TermFactory(curie="MI:0019"), "detection"),),
            ),
        ),
        (
            "publications",
            lambda: _same_key(
                regulation(
                    publications=(PublicationRef(PublicationFactory(), "reported_in"),)
                ),
                publications=(PublicationRef(PublicationFactory(), "reported_in"),),
            ),
        ),
        (
            "scores",
            lambda: _same_key(
                regulation(
                    scores=(
                        ScoreValue(ScoreTypeFactory(slug="stats:test_value"), "0.5"),
                    )
                ),
                scores=(ScoreValue("stats:test_value", "0.9"),),
            ),
        ),
    ],
)
def test_any_compared_change_under_the_same_key_is_a_conflict(ev, named, claims):
    """Each of the names a re-record compares, changed alone: the refusal
    names exactly that one."""
    first, second = claims()
    record(ev, first)
    with refused(re.escape(f"differing main ({named})"), ClaimConflict):
        record(ev, second)


def test_a_record_claimed_again_with_one_claim_more_or_less_is_a_conflict(ev):
    claims = [regulation(key_part="a"), regulation(key_part="b")]
    record(ev, *claims)
    with refused("missing b", ClaimConflict):
        record(ev, claims[0])
    with refused("added c", ClaimConflict):
        record(ev, *claims, regulation(key_part="c"))
    assert EvidenceAssertion.objects.count() == 2


def test_a_run_again_under_a_second_commit_run_of_the_draft_is_a_no_op(ev):
    claims = [regulation(effect="activating")]
    first = record(ev, *claims).assertions[0].assertion
    ev.run.state = ImportState.ABANDONED
    ev.run.save()
    second_run = ImportRunFactory(
        release=ev.release, file=ev.file, mode=ImportMode.COMMIT
    )
    again = record_claims(ev.records[0], claims, import_run=second_run)
    assert not again.assertions[0].created
    # The assertion keeps the run that first wrote it.
    assert stored(again.assertions[0].assertion)["import_run_id"] == ev.run.pk
    assert again.assertions[0].assertion.pk == first.pk


def test_a_paper_gaining_a_pmid_between_two_identical_calls_is_a_no_op(ev):
    # Test-only identifiers: no real paper is named.
    first = get_or_create_publication(doi="10.1000/ABC.1").publication
    second = get_or_create_publication(doi="10.1000/xyz.2").publication
    claims = [
        regulation(
            publications=(
                PublicationRef(first, "reported_in", {"doi": "10.1000/ABC.1"}),
                PublicationRef(second, "reported_in", {"doi": "doi:10.1000/xyz.2"}),
            )
        )
    ]
    keys = [r.assertion.assertion_key for r in record(ev, *claims).assertions]
    assert keys == ["main|pub:doi:10.1000/abc.1", "main|pub:doi:10.1000/xyz.2"]
    # A co-referent merge names the first paper by a PMID too.
    merged = get_or_create_publication(pmid="99000001", doi="10.1000/abc.1")
    assert merged.publication.pk == first.pk
    again = record(ev, *claims)
    assert [r.created for r in again.assertions] == [False, False]


@pytest.mark.parametrize(
    "first,second",
    [({"x": True}, {"x": 1}), ({"x": 1.0}, {"x": 1}), ({"x": "1"}, {"x": 1})],
)
def test_qualifiers_are_compared_as_json(ev, first, second):
    regulator, target = entity_of("gene"), entity_of("gene")
    record(ev, regulation(regulator, target, qualifiers=first))
    with refused(re.escape("differing main (qualifiers)"), ClaimConflict):
        record(ev, regulation(regulator, target, qualifiers=second))


def test_a_tuple_valued_qualifier_recorded_twice_is_a_no_op(ev):
    regulator, target = entity_of("gene"), entity_of("gene")
    given = {"alleles": ("a1", "a2"), "nested": {"pair": (1, 2)}}
    first = record(ev, regulation(regulator, target, qualifiers=given))
    assert stored(first.assertions[0].assertion)["qualifiers"] == {
        "alleles": ["a1", "a2"],
        "nested": {"pair": [1, 2]},
    }
    again = record(ev, regulation(regulator, target, qualifiers=given))
    assert not again.assertions[0].created


@pytest.mark.parametrize(
    "given,kept",
    [
        (1e16, 10**16),
        (-1.5e16, -15 * 10**15),
        (1.2345678901234567e20, 123456789012345670000),
        (-0.0, 0.0),
        ([1e17, {"y": -0.0}], [10**17, {"y": 0.0}]),
        (1e-20, 1e-20),
        (0.1, 0.1),
    ],
)
def test_qualifier_numbers_are_kept_as_the_database_keeps_them(ev, given, kept):
    """PostgreSQL's jsonb gives 1e+16 back as the integer 10000000000000000
    and -0.0 as 0.0: a claim takes them so, and recording it twice is a
    no-op on every database."""
    regulator, target = entity_of("gene"), entity_of("gene")
    made = regulation(regulator, target, qualifiers={"x": given})
    assert json.dumps(made.qualifiers) == json.dumps({"x": kept})
    first = record(ev, made)
    held = stored(first.assertions[0].assertion)["qualifiers"]
    assert json.dumps(held) == json.dumps({"x": kept})
    again = record(ev, regulation(regulator, target, qualifiers={"x": given}))
    assert not again.assertions[0].created


def test_qualifiers_that_contain_themselves_are_refused(ev):
    looped = {"x": []}
    looped["x"].append(looped)
    with refused(f"{rule(16)}.*survive a round trip through JSON"):
        record(ev, regulation(qualifiers=looped))


def test_a_claims_mappings_are_copied_when_it_is_made(ev):
    given = {"x": "1"}
    stated = {"pmid": "1"}
    made = regulation(qualifiers=given)
    ref = PublicationRef(None, "reported_in", stated)
    given["x"], stated["pmid"] = "2", "2"
    assert made.qualifiers == {"x": "1"} and ref.stated == {"pmid": "1"}
    with pytest.raises(TypeError):
        hash(made)


# ── Several claims, and several papers ──────────────────────────────────────


def test_several_claims_of_one_record_need_distinct_key_parts(ev):
    gene = entity_of("gene")
    entries = entity_of("protein"), entity_of("protein")
    result = record(
        ev,
        claim("encodes", gene, entries[0], key_part="P04637"),
        claim("encodes", gene, entries[1], key_part="Q00987"),
    )
    assert [r.assertion.assertion_key for r in result.assertions] == [
        "P04637",
        "Q00987",
    ]
    assert Relationship.objects.filter(subject=gene).count() == 2


@pytest.mark.parametrize(
    "parts,message",
    [
        (("", "b"), "this claim has none"),
        (("a", ""), "this claim has none"),
        (("a", "a"), "is claim 1's too"),
        (("a", "b c"), "a letter or digit"),
        (("a", "-b"), "a letter or digit"),
        (("a", "b|pub:x"), "a letter or digit"),
        (("a", "x" * 65), "a letter or digit"),
        (("main", "b"), "what a blank key part means"),
    ],
)
def test_key_parts_are_required_distinct_and_well_formed(ev, parts, message):
    with refused(f"{rule(11)}.*{re.escape(message)}"):
        record(ev, *(regulation(key_part=part) for part in parts))


def test_one_claim_may_name_its_key_part(ev):
    (recorded,) = record(ev, regulation(key_part="site:S15")).assertions
    assert recorded.assertion.assertion_key == "site:S15"
    with refused("what a blank key part means"):
        record(ev, regulation(key_part="main"), at=1)


def test_two_papers_give_two_assertions_one_reported_in_each(ev):
    first, second = PublicationFactory(), PublicationFactory()
    cited = PublicationFactory()
    result = record(
        ev,
        regulation(
            publications=(
                PublicationRef(second, "reported_in", {"pmid": second.pmid}),
                PublicationRef(first, "reported_in", {"pmid": f"PMID: {first.pmid}"}),
                PublicationRef(cited, "cited"),
                PublicationRef(cited, "cited"),
            ),
        ),
    )
    keys = [r.assertion.assertion_key for r in result.assertions]
    assert keys == [f"main|pub:pmid:{first.pmid}", f"main|pub:pmid:{second.pmid}"]
    for recorded, paper in zip(result.assertions, (first, second)):
        assert sorted(
            recorded.assertion.publications.values_list("publication_id", "role")
        ) == sorted([(paper.pk, "reported_in"), (cited.pk, "cited")])
    # One relationship, two assertions.
    assert len({r.relationship.pk for r in result.assertions}) == 1


def test_coreferent_identifiers_give_one_publication_and_one_assertion(ev):
    """A row stating imex:IM-99999|pubmed:99000002 (test-only values) names
    one paper."""
    paper = get_or_create_publication(imex_id="IM-99999", pmid="99000002").publication
    assert Publication.objects.count() == 1
    stated = {"imex": "IM-99999", "pmid": "pubmed:99000002"}
    result = record(
        ev, regulation(publications=(PublicationRef(paper, "reported_in", stated),))
    )
    assert [r.assertion.assertion_key for r in result.assertions] == ["main"]
    # Two references to the one paper, each stating one of its identifiers.
    result = record(
        ev,
        regulation(
            publications=(
                PublicationRef(paper, "reported_in", {"imex": "IM-99999"}),
                PublicationRef(paper.pk, "reported_in", {"pmid": "99000002"}),
            )
        ),
        at=1,
    )
    assert [r.assertion.assertion_key for r in result.assertions] == ["main"]
    assert result.assertions[0].assertion.publications.count() == 1


@pytest.mark.parametrize(
    "stated,message",
    [
        (({}, {"pmid": "2"}), "has none"),
        (({"pmid": "7"}, {"pmid": "2"}), "whose pmid is"),
        (({"doi": "10.1/other"}, {"pmid": "2"}), "whose doi_lookup is"),
        (({"pmid": "x"}, {"pmid": "2"}), "is not a PMID"),
        (({"isbn": "1"}, {"pmid": "2"}), "is keyed by pmid, doi, pmcid, imex, local"),
    ],
)
def test_several_papers_are_keyed_by_what_the_record_states(ev, stated, message):
    first = PublicationFactory(pmid="1")
    second = PublicationFactory(pmid="2")
    refs = (
        PublicationRef(first, "reported_in", stated[0]),
        PublicationRef(second, "reported_in", stated[1]),
    )
    with refused(f"{rule(11)}.*{re.escape(message)}"):
        record(ev, regulation(publications=refs))


def test_a_paper_stated_twice_with_two_values_is_refused(ev):
    paper = PublicationFactory(pmid="5")
    refs = (
        PublicationRef(paper, "reported_in", {"pmid": "5"}),
        PublicationRef(paper, "reported_in", {"pmid": "6"}),
    )
    with refused("states two pmid values for one paper"):
        record(ev, regulation(publications=refs))


def test_publications_are_stored_and_roles_closed(ev):
    with refused(f"{rule(11)}.*names no publication"):
        record(ev, regulation(publications=(PublicationRef(10**12, "reported_in"),)))
    with refused(f"{rule(11)}.*role is one of"):
        record(
            ev,
            regulation(publications=(PublicationRef(PublicationFactory(), "primary"),)),
        )
    with refused(f"{rule(11)}.*for check_claims"):
        record(
            ev,
            regulation(
                publications=(PublicationRef(None, "reported_in", {"pmid": "1"}),)
            ),
        )


def test_a_local_publication_is_cited_by_its_own_datasets_releases(ev):
    elsewhere = Publication(
        local_dataset=DatasetReleaseFactory().dataset, local_key="table-1"
    )
    elsewhere.save()
    with refused(f"{rule(11)}.*local to dataset"):
        record(ev, regulation(publications=(PublicationRef(elsewhere, "cited"),)))
    own = Publication(local_dataset=ev.release.dataset, local_key="table-1")
    own.save()
    first = PublicationFactory()
    result = record(
        ev,
        regulation(
            publications=(
                PublicationRef(own, "reported_in", {"local": " table-1 "}),
                PublicationRef(first, "reported_in", {"pmid": first.pmid}),
            )
        ),
    )
    assert [r.assertion.assertion_key for r in result.assertions] == [
        "main|pub:local:table-1",
        f"main|pub:pmid:{first.pmid}",
    ]


# ── Outcomes, effects and genetic signs ─────────────────────────────────────


@pytest.mark.parametrize("outcome", Outcome.values)
def test_each_outcome_is_recorded(ev, outcome):
    fields = {"outcome": outcome}
    if outcome == Outcome.NOT_SIGNIFICANT:
        p_value_score_type()
        fields |= {
            "scores": (ScoreValue("stats:test_p", "0.4"),),
            "qualifiers": {"threshold": P_BELOW},
        }
    (recorded,) = record(ev, regulation(**fields)).assertions
    assert stored(recorded.assertion)["outcome"] == outcome


@pytest.mark.parametrize(
    "outcome,value",
    [("triumph", "outcome"), ("experiment", "evidence_basis"), ("curated", "method")],
)
def test_outcome_basis_and_method_are_closed(ev, outcome, value):
    fields = {
        "outcome": {"outcome": outcome},
        "evidence_basis": {"evidence_basis": outcome},
        "method": {"assertion_method": outcome},
    }[value]
    with refused(f"{rule(6)}.*not {outcome!r}"):
        record(ev, regulation(**fields))


def test_basis_and_method_are_independent(ev):
    """A literature-curated experiment is experimental and a manual
    assertion; any basis goes with any method."""
    for basis in EvidenceBasis.values:
        for method in AssertionMethod.values:
            source = mapped_record(ev.release, ev.run)
            (recorded,) = record_claims(
                source,
                [regulation(evidence_basis=basis, assertion_method=method)],
                import_run=ev.run,
            ).assertions
            row = stored(recorded.assertion)
            assert (row["evidence_basis"], row["assertion_method"]) == (basis, method)
    assert EvidenceAssertion.objects.count() == 18


def test_an_effect_and_a_sign_together_are_refused(ev):
    with refused(f"{rule(7)}.*only a type with genetic_sign polarity carries a sign"):
        record(ev, regulation(effect="activating", genetic_sign="negative"))
    with refused(f"{rule(7)}.*only a type with effect polarity carries an effect"):
        record(
            ev,
            genetic(
                effect="activating", genetic_sign="negative", scores=(signed("-1"),)
            ),
        )


def test_the_database_keeps_an_effect_and_a_sign_apart(ev):
    """graph_assert_effect_xor_sign, reached past the guard through the base
    manager."""
    (recorded,) = record(ev, regulation(effect="activating")).assertions
    with pytest.raises(IntegrityError, match="graph_assert_effect_xor_sign"):
        with transaction.atomic():
            EvidenceAssertion._base_manager.filter(pk=recorded.assertion.pk).update(
                genetic_sign="negative"
            )


@pytest.mark.parametrize("outcome", NOT_SUPPORTED)
def test_only_a_supported_assertion_carries_an_effect_or_a_sign(ev, outcome):
    fields = {
        "outcome": outcome,
        "scores": (ScoreValue(p_value_score_type(), "0.5"),),
    }
    if outcome == Outcome.NOT_SIGNIFICANT:
        fields["qualifiers"] = {"threshold": P_BELOW}
    with refused(f"{rule(8)}.*not supported carries no effect and no sign"):
        record(ev, regulation(effect="unknown", **fields))
    fields["scores"] += (signed("0.2"),)
    with refused(f"{rule(8)}.*not supported carries no effect and no sign"):
        record(ev, genetic(genetic_sign="positive", **fields))


@pytest.mark.parametrize("outcome", NOT_SUPPORTED)
def test_the_database_keeps_an_assertion_that_is_not_supported_bare(ev, outcome):
    """graph_assert_bare_unless_sup, reached past the guard."""
    fields = {"outcome": outcome}
    if outcome == Outcome.NOT_SIGNIFICANT:
        fields |= {
            "scores": (ScoreValue(p_value_score_type(), "0.5"),),
            "qualifiers": {"threshold": P_BELOW},
        }
    (recorded,) = record(ev, regulation(**fields)).assertions
    with pytest.raises(IntegrityError, match="graph_assert_bare_unless_sup"):
        with transaction.atomic():
            EvidenceAssertion._base_manager.filter(pk=recorded.assertion.pk).update(
                effect="inhibiting"
            )


@pytest.mark.parametrize("effect", ["negative", "Negative", " NEGATIVE "])
def test_negative_as_an_effect_is_refused_as_ambiguous(ev, effect):
    with refused(f"{rule(7)}.*'negative' is ambiguous as an effect"):
        record(ev, regulation(effect=effect))


@pytest.mark.parametrize("effect", ["repression", "ACTIVATING", "", True, 1])
def test_an_effect_outside_what_a_source_states_is_refused(ev, effect):
    with refused(f"{rule(7)}.*effect is activating, inhibiting, unknown, or None"):
        record(ev, regulation(effect=effect))


def test_not_applicable_is_never_stated(ev):
    with refused(f"{rule(7)}.*never stated"):
        record(ev, regulation(effect="not_applicable"))


def test_unknown_and_not_stated_effects_are_kept_apart(ev):
    unknown = record(ev, regulation(effect="unknown"), at=0).assertions[0].assertion
    silent = record(ev, regulation(), at=1).assertions[0].assertion
    assert (stored(unknown)["effect"], stored(silent)["effect"]) == ("unknown", None)


@pytest.mark.parametrize("slug", ["physical_interaction", "encodes"])
def test_an_effect_on_a_symmetric_or_reference_type_is_refused(ev, slug):
    made = (
        physical(effect="activating")
        if slug == "physical_interaction"
        else claim(
            "encodes", entity_of("gene"), entity_of("protein"), effect="activating"
        )
    )
    with refused(f"{rule(7)}.*only a type with effect polarity carries an effect"):
        record(ev, made)


def test_a_sign_on_regulation_is_refused(ev):
    with refused(f"{rule(7)}.*only a type with genetic_sign polarity"):
        record(ev, regulation(genetic_sign="negative", scores=(signed("-1"),)))
    with refused(f"{rule(7)}.*genetic_sign is negative, positive or None"):
        record(ev, genetic(genetic_sign="aggravating", scores=(signed("-1"),)))


def test_a_sign_needs_a_signed_score_with_a_null_model(ev):
    with refused(
        f"{rule(7)}.*needs a score on the same assertion whose type is signed"
    ):
        record(ev, genetic(genetic_sign="negative"))
    unsigned = ScoreTypeFactory(slug="stats:test_unsigned")
    with refused("whose type is signed and states its null_model"):
        record(
            ev, genetic(genetic_sign="negative", scores=(ScoreValue(unsigned, "-1"),))
        )
    no_model = ScoreTypeFactory(
        slug="stats:test_no_model", direction=ScoreDirection.SIGNED, null_model=" "
    )
    with refused("whose type is signed and states its null_model"):
        record(
            ev, genetic(genetic_sign="negative", scores=(ScoreValue(no_model, "-1"),))
        )
    (recorded,) = record(
        ev, genetic(genetic_sign="negative", scores=(signed("-0.31"),))
    ).assertions
    assert stored(recorded.assertion)["genetic_sign"] == "negative"


@pytest.mark.parametrize(
    "sign,raw,agrees",
    [
        ("negative", "-0.3", True),
        ("negative", "0.3", False),
        ("positive", "0.3", True),
        ("positive", "-1e-3", False),
        # Zero and a value the grammar does not read decide nothing.
        ("negative", "0", True),
        ("positive", "NaN", True),
        ("negative", "<-0.2", True),
    ],
)
def test_a_sign_agrees_with_its_signed_score(ev, sign, raw, agrees):
    made = genetic(genetic_sign=sign, scores=(signed(raw),))
    if agrees:
        record(ev, made)
        assert EvidenceAssertion.objects.get().genetic_sign == sign
    else:
        with refused(f"{rule(7)}.*disagrees with the stats:test_gi score"):
            record(ev, made)


# ── Not significant ─────────────────────────────────────────────────────────


def test_a_not_significant_result_keeps_its_scores_and_carries_no_sign(ev):
    p_value_score_type()
    (recorded,) = record(
        ev,
        genetic(
            outcome=Outcome.NOT_SIGNIFICANT,
            scores=(signed("-0.08"), ScoreValue("stats:test_p", "0.3")),
            qualifiers={"threshold": P_BELOW, "screen": "SGA"},
        ),
    ).assertions
    row = stored(recorded.assertion)
    # Never tested_absent: measured, below the threshold, neither support
    # nor absence.
    assert (row["outcome"], row["genetic_sign"]) == ("not_significant", None)
    assert sorted(recorded.assertion.scores.values_list("raw_value", "value")) == [
        ("-0.08", -0.08),
        ("0.3", 0.3),
    ]
    assert row["qualifiers"]["threshold"] == P_BELOW


@pytest.mark.parametrize(
    "threshold,message",
    [
        (
            None,
            'records the significance criterion it failed in qualifiers["threshold"]',
        ),
        ([], "is a non-empty list of criteria"),
        ({"score_type": "stats:test_p"}, "is a non-empty list of criteria"),
        ([{"score_type": "stats:test_p", "operator": "<"}], "is a non-empty list"),
        ([{**P_BELOW[0], "unit": "x"}], "is a non-empty list"),
        ([{**P_BELOW[0], "score_type": "stats:test_q"}], "is not one"),
        ([{**P_BELOW[0], "operator": "!="}], "operator is one of"),
        ([{**P_BELOW[0], "value": 0.05}], "the threshold as stated"),
        ([{**P_BELOW[0], "value": " "}], "the threshold as stated"),
    ],
)
def test_a_not_significant_result_states_the_criterion_it_failed(
    ev, threshold, message
):
    p_value_score_type()
    qualifiers = {} if threshold is None else {"threshold": threshold}
    with refused(f"{rule(8)}.*{re.escape(message)}"):
        record(
            ev,
            regulation(
                outcome=Outcome.NOT_SIGNIFICANT,
                scores=(ScoreValue("stats:test_p", "0.3"),),
                qualifiers=qualifiers,
            ),
        )


@pytest.mark.parametrize(
    "criteria,raw,significant",
    [
        (P_BELOW, "0.01", True),
        (P_BELOW, "0.05", False),
        (P_BELOW, "0.3", False),
        (GI_ABOVE, "-0.2", True),
        (GI_ABOVE, "-0.05", False),
        (
            [
                {"score_type": "stats:test_gi", "operator": "abs>=", "value": "0.08"},
                {"score_type": "stats:test_gi", "operator": "<=", "value": "0"},
            ],
            "0.1",
            False,
        ),
        # A censored value's bound settles a criterion every value within it
        # meets, and only such a one.
        (P_BELOW, "<2.2e-16", True),
        (P_BELOW, "< 0.05", True),
        (P_BELOW, "<=0.05", False),
        (P_BELOW, "<0.1", False),
        (P_BELOW, ">0.5", False),
        ([{**P_BELOW[0], "operator": "<="}], "<=0.05", True),
        ([{**GI_ABOVE[0], "operator": ">"}], ">0.5", True),
        ([{**GI_ABOVE[0], "operator": ">"}], ">=0.08", False),
        ([{**GI_ABOVE[0], "operator": ">="}], ">=0.08", True),
        # A bound settles "abs> t" when every value within it is above t, or
        # every one is below -t, or when 0 meets it (so every value does);
        # "abs>= t" likewise.
        (GI_ABOVE, ">0.5", True),
        (GI_ABOVE, "<-0.5", True),
        ([{**GI_ABOVE[0], "operator": "abs>="}], "<=-0.5", True),
        ([{**GI_ABOVE[0], "operator": "abs>=", "value": "0"}], "<0.3", True),
        (GI_ABOVE, ">=0.08", False),
        (GI_ABOVE, ">0.05", False),
        ([{**GI_ABOVE[0], "operator": "abs<"}], ">0.5", False),
        # A limit or threshold that underflows a double settles nothing: read
        # as 0 it would be tighter than stated.
        ([{**GI_ABOVE[0], "value": "0"}], "<0", True),
        ([{**GI_ABOVE[0], "value": "0"}], "<1e-400", False),
        ([{**GI_ABOVE[0], "value": "0"}], ">-1e-400", False),
        ([{**GI_ABOVE[0], "operator": "<", "value": "0"}], "<1e-400", False),
        ([{**GI_ABOVE[0], "value": "1e-400"}], "<0", False),
    ],
)
def test_a_score_that_meets_every_criterion_is_significant_and_refused(
    ev, criteria, raw, significant
):
    p_value_score_type()
    slug = criteria[0]["score_type"]
    score = ScoreValue(slug, raw) if slug == "stats:test_p" else signed(raw)
    made = genetic(
        outcome=Outcome.NOT_SIGNIFICANT,
        scores=(score,),
        qualifiers={"threshold": criteria},
    )
    if significant:
        with refused(f"{rule(8)}.*so the result is significant"):
            record(ev, made)
    else:
        record(ev, made)
        assert EvidenceAssertion.objects.get().outcome == Outcome.NOT_SIGNIFICANT


def test_a_criterion_whose_values_are_not_finite_decides_nothing(ev):
    p_value_score_type()
    for at, (raw, stated) in enumerate([("NaN", "0.05"), ("0.01", "n/a")]):
        criteria = [{**P_BELOW[0], "value": stated}]
        record(
            ev,
            regulation(
                outcome=Outcome.NOT_SIGNIFICANT,
                scores=(ScoreValue("stats:test_p", raw),),
                qualifiers={"threshold": criteria},
            ),
            at=at,
        )
    assert EvidenceAssertion.objects.count() == 2


@pytest.mark.parametrize("outcome", ["supported", "tested_absent", "inconclusive"])
def test_a_threshold_belongs_to_a_not_significant_result_only(ev, outcome):
    p_value_score_type()
    with refused(f"{rule(8)}.*is refused on a {outcome} assertion"):
        record(
            ev,
            regulation(
                outcome=outcome,
                scores=(ScoreValue("stats:test_p", "0.3"),),
                qualifiers={"threshold": P_BELOW},
            ),
        )


# ── Physical levels ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "curie,level",
    [
        ("MI:0914", InteractionLevel.ASSOCIATION),
        ("MI:0915", InteractionLevel.PHYSICAL_ASSOCIATION),
        ("MI:0407", InteractionLevel.DIRECT_INTERACTION),
        ("MI:0195", InteractionLevel.DIRECT_INTERACTION),
    ],
)
def test_a_stated_interaction_type_sets_its_crosswalk_level(ev, curie, level):
    TermFactory(curie=curie)
    (recorded,) = record(ev, physical(level=level, interaction_type=curie)).assertions
    row = stored(recorded.assertion)
    assert (row["interaction_type_id"], row["interaction_level"]) == (curie, level)
    others = [value for value in InteractionLevel.values if value != level]
    for at, other in enumerate(others, 1):
        with refused(f"{rule(9)}.*the crosswalk's level for {curie}"):
            record(ev, physical(level=other, interaction_type=curie), at=at % 3)


@pytest.mark.parametrize(
    "curie",
    [
        "MI:0403",
        "MI:2364",
        "MI:0208",
        "MI:2402",
        "MI:2373",
        "MI:2286",
        "MI:1110",
        "MI:2232",
        "MI:0218",
        "MI:1126",
    ],
)
def test_an_interaction_type_outside_the_crosswalk_is_refused(ev, curie):
    """Colocalization, proximity, genetic interaction, functional and
    predicted association, an obsolete term and self interaction are never
    physical evidence between two proteins."""
    TermFactory(curie=curie)
    for level in (InteractionLevel.ASSOCIATION, InteractionLevel.DIRECT_INTERACTION):
        with refused(f"{rule(9)}.*{curie} is not in the PSI-MI physical crosswalk"):
            record(ev, physical(level=level, interaction_type=curie))


@pytest.mark.parametrize(
    "level",
    [InteractionLevel.DIRECT_INTERACTION, InteractionLevel.PHYSICAL_ASSOCIATION],
)
def test_a_level_above_association_needs_a_stated_type(ev, level):
    with refused(f"{rule(9)}.*{level} rests on the interaction type the source states"):
        record(ev, physical(level=level))


def test_association_without_a_type_is_assigned_by_openpip(ev):
    result = record(ev, physical(level=InteractionLevel.ASSOCIATION))
    row = stored(result.assertions[0].assertion)
    assert (row["interaction_type_id"], row["interaction_level"]) == (
        None,
        "association",
    )
    assert result.notes == (
        "Claim 1: level association is assigned by openPIP from the assay the "
        "source states, not from a stated interaction type (open question B14).",
    )


def test_a_physical_assertion_states_a_level(ev):
    with refused(f"{rule(9)}.*states an interaction_level"):
        record(ev, physical(level=None))
    with refused(f"{rule(9)}.*interaction_level is one of"):
        record(ev, physical(level="binding"))


def test_a_level_belongs_to_physical_evidence_only(ev):
    TermFactory(curie="MI:0407")
    with refused(f"{rule(9)}.*interaction_level belongs to physical-family evidence"):
        record(ev, regulation(interaction_level=InteractionLevel.NOT_STATED))
    with refused(f"{rule(9)}.*interaction_type belongs to physical-family evidence"):
        record(ev, regulation(interaction_type="MI:0407"))


def test_the_unspecified_type_states_no_type_at_level_not_stated(ev):
    TermFactory(curie="MI:0914")
    proteins = entity_of("protein"), entity_of("protein")

    def unspecified(**fields):
        return claim("protein_interaction_unspecified", *proteins, **fields)

    for fields in (
        {"interaction_level": InteractionLevel.ASSOCIATION},
        {
            "interaction_level": InteractionLevel.ASSOCIATION,
            "interaction_type": "MI:0914",
        },
    ):
        with refused(f"{rule(9)}.*states no interaction type, at level not_stated"):
            record(ev, unspecified(**fields))
    record(ev, unspecified(interaction_level=InteractionLevel.NOT_STATED))
    assert EvidenceAssertion.objects.get().interaction_level == "not_stated"


def test_a_tested_absent_assertion_keeps_its_tested_level(ev):
    TermFactory(curie="MI:0407")
    (recorded,) = record(
        ev,
        physical(
            outcome=Outcome.TESTED_ABSENT,
            level=InteractionLevel.DIRECT_INTERACTION,
            interaction_type="MI:0407",
        ),
    ).assertions
    row = stored(recorded.assertion)
    assert (row["outcome"], row["interaction_level"], row["effect"]) == (
        "tested_absent",
        "direct_interaction",
        None,
    )


# ── Terms ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fields,message",
    [
        ({"eco_term": "MI:0018"}, "eco_term is a eco term; MI:0018 is psi-mi"),
        (
            {"methods": (MethodRef("ECO:0000353", "detection"),)},
            "a detection method is a psi-mi term",
        ),
        (
            {"methods": (MethodRef("ECO:0000353", "participant_identification"),)},
            "a participant_identification method is a psi-mi term",
        ),
        (
            {"methods": (MethodRef("MI:0018", "evidence_code"),)},
            "a evidence_code method is a eco term",
        ),
        ({"methods": (MethodRef("MI:0018", "assay"),)}, "a method's role is one of"),
        ({"eco_term": "GO:0005634"}, "not a CURIE of an ontology openPIP accepts"),
        ({"eco_term": "ECO:353"}, "not a eco CURIE"),
        ({"eco_term": 353}, "is a term or its CURIE"),
    ],
)
def test_terms_come_from_their_roles_ontologies(ev, fields, message):
    TermFactory(curie="MI:0018")
    TermFactory(curie="ECO:0000353")
    with refused(f"{rule(10)}.*{re.escape(message)}"):
        record(ev, regulation(**fields))


def test_an_interaction_type_is_a_psi_mi_term(ev):
    TermFactory(curie="ECO:0000353")
    with refused(f"{rule(10)}.*interaction_type is a psi-mi term"):
        record(ev, physical(interaction_type="ECO:0000353"))


def test_a_term_must_be_stored_and_is_never_created(ev):
    with refused(f"{rule(10)}.*names no stored term: MI:0018"):
        record(ev, regulation(methods=(MethodRef("mi:0018", "detection"),)))
    assert not Term.objects.filter(pk="MI:0018").exists()


def test_identical_method_pairs_collapse(ev):
    term = TermFactory(curie="MI:0018")
    (recorded,) = record(
        ev,
        regulation(
            methods=(
                MethodRef(term, "detection"),
                MethodRef(" mi:0018", "detection"),
                MethodRef("MI:0018", "participant_identification"),
            )
        ),
    ).assertions
    assert sorted(recorded.assertion.methods.values_list("term_id", "role")) == [
        ("MI:0018", "detection"),
        ("MI:0018", "participant_identification"),
    ]


# ── Preconditions ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_a_release_past_draft_records_no_evidence(ev, status):
    advance(ev.release, status)
    with refused(f"Release {ev.release.pk} is {status}: evidence is recorded only"):
        record(ev, regulation())


@pytest.mark.parametrize(
    "mode,state",
    [
        (ImportMode.DRY_RUN, ImportState.RUNNING),
        (ImportMode.COMMIT, ImportState.COMPLETED),
        (ImportMode.COMMIT, ImportState.FAILED),
        (ImportMode.COMMIT, ImportState.ABANDONED),
    ],
)
def test_only_a_running_commit_run_records_evidence(ev, mode, state):
    run = ev.run
    if mode == ImportMode.DRY_RUN:
        run = ImportRunFactory(release=ev.release, file=ev.file, mode=mode)
    else:
        run.state = state
        run.save()
    with refused(f"Import run {run.pk} is a {mode} run, {state}: only a running"):
        record_claims(ev.records[0], [regulation()], import_run=run)


def test_a_record_of_another_release_or_not_mapped_is_refused(ev):
    other = evidence_release(records=1)
    with refused(f"Import run {ev.run.pk} belongs to release {ev.release.pk}"):
        record_claims(other.records[0], [regulation()], import_run=ev.run)
    for status in RecordStatus.values:
        if status == RecordStatus.MAPPED:
            continue
        unmapped = mapped_record(ev.release, ev.run, status=status)
        with refused(f"Source record {unmapped.pk} is {status}: evidence is recorded"):
            record_claims(unmapped, [regulation()], import_run=ev.run)


def test_unknown_records_and_runs_are_refused(ev):
    with refused("Unknown source record"):
        record_claims(10**12, [regulation()], import_run=ev.run)
    with refused("Unknown source record"):
        record_claims("1", [regulation()], import_run=ev.run)
    with refused("Unknown import run"):
        record_claims(ev.records[0], [regulation()], import_run=10**12)
    with pytest.raises(TypeError):
        record_claims(ev.records[0], regulation(), import_run=ev.run)
    with pytest.raises(TypeError):
        record_claims(ev.records[0], ["claim"], import_run=ev.run)


def test_an_embargoed_draft_with_an_effective_internal_approval_records(registry):
    embargoed = evidence_release(
        embargo_until=timezone.now() + datetime.timedelta(days=30)
    )
    (recorded,) = record(embargoed, regulation()).assertions
    assert recorded.created


def _reopened(release, **changes):
    diligence = DueDiligenceRecord.objects.get(dataset=release.dataset)
    for name, value in changes.items():
        setattr(diligence, name, value)
    diligence._changed_by = StaffUserFactory()
    diligence.save()


@pytest.mark.parametrize(
    "lapse",
    ["revoked", "reopened", "blocked", "time-limited", "denied"],
)
def test_internal_use_must_be_effective(registry, lapse):
    if lapse == "denied":
        ev = evidence_release(approved=False)
        approval = UsageApproval(
            release=ev.release,
            scope=ApprovalScope.INTERNAL,
            decision="denied",
            rationale="Test only.",
            decided_by=StaffUserFactory(),
        )
        approval.save()
    else:
        ev = evidence_release()
    if lapse == "revoked":
        approval = UsageApproval.objects.get(release=ev.release)
        revoke(approval, reason="Test only.", revoked_by=StaffUserFactory())
    elif lapse == "reopened":
        _reopened(ev.release, status=DueDiligenceStatus.OPEN)
    elif lapse == "blocked":
        _reopened(ev.release, status=DueDiligenceStatus.BLOCKED)
    elif lapse == "time-limited":
        _reopened(ev.release, terms_time_limited=True)
    with refused(f"Release {ev.release.pk} has no effective internal approval"):
        record(ev, regulation())


def test_a_synthetic_draft_needs_no_approval(registry):
    synthetic = evidence_release(synthetic=True)
    assert not UsageApproval.objects.filter(release=synthetic.release).exists()
    constructs = entity_of("gene", 32630), entity_of("gene", 32630)
    (recorded,) = record(synthetic, regulation(*constructs)).assertions
    assert recorded.created


def test_nothing_is_recorded_while_the_switch_is_off(ev, settings):
    settings.TYPED_GRAPH_ENABLED = False
    with refused("The typed graph is off on this portal"):
        record(ev, regulation())
    with refused("The typed graph is off on this portal"):
        check_claims([regulation()], release=ev.release)


# ── Synthetic separation ────────────────────────────────────────────────────


@pytest.fixture
def synthetic(registry):
    return evidence_release(synthetic=True)


def constructs(type_slug="gene"):
    return entity_of(type_slug, 32630), entity_of(type_slug, 32630)


def _synthetic_paper(release):
    paper = Publication(
        local_dataset=release.dataset, local_key="synthetic-1", is_synthetic=True
    )
    paper.save()
    return paper


def test_endpoints_stay_on_their_releases_side_of_the_synthetic_line(ev, synthetic):
    with refused(f"{rule(13)}.*synthetic constructs \\(taxon 32630\\) appear only"):
        record(ev, regulation(entity_of("gene"), entity_of("gene", 32630)))
    with refused(f"{rule(13)}.*joins synthetic constructs only"):
        record(synthetic, regulation(entity_of("gene", 32630), entity_of("gene")))


def test_publications_stay_on_their_releases_side_of_the_synthetic_line(ev, synthetic):
    with refused(f"{rule(11)}.*a synthetic release cites only synthetic papers"):
        record(
            synthetic,
            regulation(
                *constructs(),
                publications=(PublicationRef(PublicationFactory(), "reported_in"),),
            ),
        )
    paper = _synthetic_paper(synthetic.release)
    with refused(f"{rule(11)}.*a synthetic release cites only synthetic papers"):
        record(ev, regulation(publications=(PublicationRef(paper, "cited"),)))
    record(
        synthetic,
        regulation(*constructs(), publications=(PublicationRef(paper, "reported_in"),)),
    )


def test_score_types_stay_on_their_releases_side_of_the_synthetic_line(ev, synthetic):
    lab = ScoreTypeFactory(slug="lab:confidence")
    with refused(f"{rule(14)}.*scores are of synthetic: or stats: types"):
        record(synthetic, regulation(*constructs(), scores=(ScoreValue(lab, "0.5"),)))
    with refused(f"{rule(14)}.*synthetic: score types belong to synthetic releases"):
        record(ev, regulation(scores=(ScoreValue("synthetic:confidence", "0.5"),)))
    record(
        synthetic,
        regulation(
            *constructs(),
            scores=(
                ScoreValue("synthetic:confidence", "0.5"),
                ScoreValue("synthetic:rank", "2"),
                signed("0.1"),
            ),
        ),
    )
    record(ev, regulation(scores=(ScoreValue(lab, "0.5"), signed("0.1"))))
    assert EvidenceScore.objects.count() == 5


def test_host_taxa_stay_on_their_releases_side_of_the_synthetic_line(ev, synthetic):
    with refused(f"{rule(15)}.*is the synthetic construct \\(32630\\) or unregistered"):
        record(
            synthetic,
            regulation(
                *constructs(),
                contexts=(ContextValue("host_taxon", "9606", taxon=9606),),
            ),
        )
    with refused(f"{rule(15)}.*the host taxon of synthetic releases only"):
        record(
            ev,
            regulation(contexts=(ContextValue("host_taxon", "32630", taxon=32630),)),
        )
    result = record(
        synthetic,
        regulation(
            *constructs(),
            contexts=(
                ContextValue("host_taxon", "32630", taxon=32630),
                ContextValue("host_taxon", "taxid:4932", taxon=4932),
            ),
        ),
    )
    assert sorted(
        result.assertions[0].assertion.contexts.values_list("raw_value", "taxon_id")
    ) == [("32630", 32630), ("taxid:4932", None)]


def _bulk_or_save(path, row):
    if path == "save":
        row.save()
    else:
        type(row).objects.bulk_create([row])


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("part", ["publication", "score", "host taxon"])
def test_the_model_guards_keep_the_synthetic_line_on_every_insert(registry, path, part):
    """What record_claims refuses, the parts refuse on save() and
    bulk_create() too, both ways."""
    synthetic_release = DatasetReleaseFactory(is_synthetic=True)
    synthetic_assertion = EvidenceAssertionFactory(
        relationship=RelationshipFactory(
            subject=entity_of("gene", 32630), object=entity_of("gene", 32630)
        ),
        release=synthetic_release,
    )
    real_assertion = EvidenceAssertionFactory()
    if part == "publication":
        crossing = [
            (synthetic_assertion, {"publication": PublicationFactory()}),
            # Local to the real release's own dataset: only the synthetic
            # line refuses it.
            (real_assertion, {"publication": _synthetic_paper(real_assertion.release)}),
        ]
        factory, field = EvidencePublicationFactory, "publication"
    elif part == "score":
        crossing = [
            (synthetic_assertion, {"score_type": ScoreTypeFactory(slug="lab:x")}),
            (real_assertion, {"score_type": ScoreTypeFactory(slug="synthetic:x")}),
        ]
        factory, field = EvidenceScoreFactory, "score_type"
    else:
        crossing = [
            (synthetic_assertion, {"taxon": taxon_of(9606)}),
            (real_assertion, {"taxon": taxon_of(32630)}),
        ]
        factory, field = EvidenceContextFactory, "taxon"
    model = factory._meta.model
    for assertion, fields in crossing:
        if part == "host taxon":
            fields |= {"facet": "host_taxon", "raw_value": "x"}
        row = factory.build(assertion=assertion, **fields)
        with pytest.raises(
            ImmutableRecordError, match=f"{model.__name__}.{field}: .*synthetic"
        ):
            _bulk_or_save(path, row)
    assert not model.objects.exists()


def test_the_assertion_guard_keeps_endpoints_on_the_synthetic_line(registry):
    synthetic_release = DatasetReleaseFactory(is_synthetic=True)
    human = RelationshipFactory()
    construct = RelationshipFactory(
        subject=entity_of("gene", 32630), object=entity_of("gene", 32630)
    )
    for relationship, release in (
        (human, synthetic_release),
        (construct, DatasetReleaseFactory()),
    ):
        with pytest.raises(
            ImmutableRecordError, match="EvidenceAssertion.relationship"
        ):
            EvidenceAssertionFactory(relationship=relationship, release=release)
    assert not EvidenceAssertion.objects.exists()


# ── Participants ────────────────────────────────────────────────────────────


def test_a_symmetric_claim_stated_higher_id_first_keeps_the_sources_order(ev):
    lower, higher = entity_of("protein"), entity_of("protein")
    (recorded,) = record(ev, physical(higher, lower)).assertions
    relationship = recorded.relationship
    assert (relationship.subject_id, relationship.object_id) == (lower.pk, higher.pk)
    assert sorted(recorded.assertion.participants.values_list("side", "entity_id")) == [
        ("object", lower.pk),
        ("subject", higher.pk),
    ]
    # Stated the other way round, the same relationship.
    again = record(ev, physical(lower, higher), at=1).assertions[0]
    assert again.relationship.pk == relationship.pk


def test_an_ordered_claims_participants_are_its_endpoints_as_stated(ev):
    regulator, target = entity_of("protein"), entity_of("gene")
    (recorded,) = record(ev, regulation(regulator, target)).assertions
    assert sorted(recorded.assertion.participants.values_list("side", "entity_id")) == [
        ("object", target.pk),
        ("subject", regulator.pk),
    ]


def test_two_participants_naming_one_endpoint_of_a_pair_are_refused(registry):
    """record_claims writes the two sides from the claim's endpoints; the
    guard beneath it refuses any other pair."""
    first, second = entity_of("protein"), entity_of("protein")
    relationship = RelationshipFactory(
        relationship_type=kind("physical_interaction"), subject=first, object=second
    )
    assertion = EvidenceAssertionFactory(
        relationship=relationship, interaction_level=InteractionLevel.NOT_STATED
    )
    both = [
        AssertionParticipantFactory.build(assertion=assertion, side=side, entity=first)
        for side in ("subject", "object")
    ]
    with pytest.raises(ImmutableRecordError, match="as a multiset"):
        AssertionParticipant.objects.bulk_create(both)
    assert not AssertionParticipant.objects.exists()


@pytest.mark.parametrize(
    "as_stated,message",
    [
        ("", "identifier as stated is never blank"),
        ("  ", "identifier as stated is never blank"),
        ("x" * 256, "is at most 255 characters"),
        ("P04637\x00", "contains a NUL character"),
        (None, "is text"),
    ],
)
def test_an_identifier_is_kept_as_stated_and_never_blank(ev, as_stated, message):
    target = entity_of("gene")
    made = regulation(entity_of("gene"), Endpoint(target, as_stated, "primary"))
    with refused(re.escape(message)):
        record(ev, made)


def test_a_resolution_basis_is_closed(ev):
    with refused(f"{rule(12)}.*resolution basis is one of"):
        record(ev, regulation(Endpoint(entity_of("gene"), "TP53", "guessed")))
    with refused(f"{rule(12)}.*given exactly when the basis is isoform_entry"):
        record(
            ev,
            regulation(
                Endpoint(entity_of("gene"), "TP53", "primary", entity_of("gene"))
            ),
        )


def isoform_link(isoform, entry, *, status=PUBLISHED, approved=True):
    """isoform_of, stated by a supported assertion of a release in this
    status, with an effective internal approval or none."""
    reference = evidence_release(records=1, approved=approved)
    link = RelationshipFactory(
        relationship_type=kind("isoform_of"), subject=isoform, object=entry
    )
    EvidenceAssertionFactory(
        relationship=link,
        release=reference.release,
        import_run=reference.run,
        source_record=reference.records[0],
    )
    advance(reference.release, status)
    return link


def isoform_claim(entry, isoform, partner=None):
    return physical(
        Endpoint(entry, "P04637-2", "isoform_entry", isoform),
        partner or entity_of("protein"),
    )


def test_an_isoform_entry_with_a_verified_link_is_accepted(ev):
    entry, isoform = entity_of("protein"), entity_of("protein_isoform")
    isoform_link(isoform, entry)
    (recorded,) = record(ev, isoform_claim(entry, isoform)).assertions
    participant = recorded.assertion.participants.get(side="subject")
    assert (
        participant.entity_id,
        participant.stated_entity_id,
        participant.resolution_basis,
        participant.identifier_as_stated,
    ) == (entry.pk, isoform.pk, "isoform_entry", "P04637-2")
    # A superseded release's link still holds.
    other_entry, other_isoform = entity_of("protein"), entity_of("protein_isoform")
    isoform_link(other_isoform, other_entry, status=SUPERSEDED)
    record(ev, isoform_claim(other_entry, other_isoform), at=1)


@pytest.mark.parametrize(
    "link",
    ["none", "draft", "staged", "withdrawn", "unapproved", "tested absent", "reversed"],
)
def test_an_isoform_entry_without_a_verified_link_is_refused(ev, link):
    entry, isoform = entity_of("protein"), entity_of("protein_isoform")
    if link in ("draft", "staged", "withdrawn"):
        isoform_link(isoform, entry, status=link)
    elif link == "unapproved":
        isoform_link(isoform, entry, approved=False)
    elif link == "tested absent":
        reference = evidence_release(records=1)
        EvidenceAssertionFactory(
            relationship=RelationshipFactory(
                relationship_type=kind("isoform_of"), subject=isoform, object=entry
            ),
            release=reference.release,
            import_run=reference.run,
            source_record=reference.records[0],
            outcome=Outcome.TESTED_ABSENT,
        )
        advance(reference.release, PUBLISHED)
    elif link == "reversed":
        # A link from another isoform to this entry says nothing of this one.
        isoform_link(entity_of("protein_isoform"), entry)
    with refused(f"{rule(12)}.*maps to its entry only through an isoform_of link"):
        record(ev, isoform_claim(entry, isoform))


def test_an_isoform_linked_to_two_entries_is_ambiguous(ev):
    entry, other, isoform = (
        entity_of("protein"),
        entity_of("protein"),
        entity_of("protein_isoform"),
    )
    isoform_link(isoform, entry)
    isoform_link(isoform, other)
    with refused(f"{rule(12)}.*is ambiguous"):
        record(ev, isoform_claim(entry, isoform))


def test_an_isoform_entry_names_an_isoform_and_its_protein_entry(ev):
    entry, isoform = entity_of("protein"), entity_of("protein_isoform")
    isoform_link(isoform, entry)
    with refused(f"{rule(12)}.*is a protein_isoform; entity"):
        record(ev, isoform_claim(entry, entity_of("protein")))
    with refused(f"{rule(12)}.*names no entity"):
        record(ev, isoform_claim(entry, 10**12))
    with refused(f"{rule(12)}.*is the isoform's protein entry"):
        record(
            ev,
            physical(
                Endpoint(isoform, "P04637-2", "isoform_entry", isoform),
                entity_of("protein"),
            ),
        )


# ── Scores, contexts, text and JSON ─────────────────────────────────────────


def test_a_context_states_a_facet_and_its_raw_value(ev):
    tissue = TermFactory(curie="MI:0018")
    result = record(
        ev,
        regulation(
            contexts=(
                ContextValue("host_taxon", "taxid:9606(human)", taxon=9606),
                ContextValue("host_taxon", "taxid:4932(yeast)", taxon=4932),
                ContextValue("assay_system", "-1", label="in vitro"),
                ContextValue("tissue", "liver", term=tissue),
                ContextValue("tissue", "liver", term="MI:0018"),
            )
        ),
    )
    contexts = result.assertions[0].assertion.contexts
    assert sorted(
        contexts.values_list("facet", "raw_value", "taxon_id", "term_id")
    ) == [
        ("assay_system", "-1", None, None),
        ("host_taxon", "taxid:4932(yeast)", None, None),
        ("host_taxon", "taxid:9606(human)", 9606, None),
        ("tissue", "liver", None, "MI:0018"),
    ]
    assert result.notes == (
        "Claim 1: host taxon 'taxid:4932(yeast)' names no registered taxon, so "
        "only its raw value is kept.",
    )


@pytest.mark.parametrize(
    "contexts,message",
    [
        ((ContextValue("organism", "x"),), "facet is one of"),
        ((ContextValue("tissue", " "),), "raw value is never blank"),
        ((ContextValue("tissue", "x" * 256),), "at most 255 characters"),
        ((ContextValue("tissue", "x", label="y" * 256),), "at most 255 characters"),
        ((ContextValue("tissue", "x\x00"),), "contains a NUL character"),
        ((ContextValue("tissue", "x", taxon=9606),), "for host_taxon only"),
        ((ContextValue("host_taxon", "9606", term="MI:0018"),), "never a term"),
        ((ContextValue("host_taxon", "-1", taxon=-1),), "PSI-MI pseudo-taxon"),
        ((ContextValue("host_taxon", "x", taxon="9606"),), "is a taxid"),
        (
            (
                ContextValue("tissue", "liver"),
                ContextValue("tissue", "liver", label="L"),
            ),
            "is given twice, with a different term, taxon or label",
        ),
        ((ContextValue("tissue", "liver", term="MI:9999"),), "names no stored term"),
    ],
)
def test_a_context_that_breaks_a_rule_is_refused(ev, contexts, message):
    TermFactory(curie="MI:0018")
    with refused(re.escape(message)):
        record(ev, regulation(contexts=contexts))


@pytest.mark.parametrize(
    "fields,message",
    [
        ({"relationship_as_stated": "x" * 10_001}, "at most 10000 characters"),
        ({"effect_as_stated": "up\x00"}, "contains a NUL character"),
        ({"evidence_group": "g" * 256}, "at most 255 characters"),
        ({"evidence_group": None}, "evidence_group is text"),
        ({"qualifiers": {1: "x"}}, "keys are strings, at every level"),
        ({"qualifiers": {"a": {2: "x"}}}, "keys are strings, at every level"),
        ({"qualifiers": ["x"]}, "keys are strings"),
        ({"qualifiers": {"x": float("nan")}}, "survive a round trip through JSON"),
        ({"qualifiers": {"x": {1, 2}}}, "survive a round trip through JSON"),
        ({"qualifiers": {"x": "a\x00"}}, "NUL character"),
        ({"qualifiers": {"x\x00": "a"}}, "NUL character"),
        ({"qualifiers": {"x": "y" * 10_000}}, "at most 10000 characters as JSON"),
    ],
)
def test_text_and_qualifiers_keep_their_limits(ev, fields, message):
    with refused(f"{rule(16)}.*{re.escape(message)}"):
        record(ev, regulation(**fields))


def test_qualifiers_are_stored_as_a_json_round_trip_gives_them(ev):
    given = {"n": 1, "f": 1.5, "b": False, "none": None, "list": (1, "a")}
    (recorded,) = record(ev, regulation(qualifiers=given)).assertions
    assert stored(recorded.assertion)["qualifiers"] == {
        "n": 1,
        "f": 1.5,
        "b": False,
        "none": None,
        "list": [1, "a"],
    }


# ── Relationship rules through record_claims ────────────────────────────────


def test_the_relationship_rules_apply_to_claims(ev):
    merged = entity_of(
        "gene", status=EntityStatus.MERGED, merged_into=entity_of("gene")
    )
    with refused(f"{rule(1)}.*names no relationship type: 'interacts'"):
        record(ev, claim("interacts", entity_of("gene"), entity_of("gene")))
    with refused(f"{rule(1)}.*the object names no entity"):
        record(ev, regulation(entity_of("gene"), 10**12))
    with refused(f"{rule(1)}.*is merged"):
        record(ev, regulation(merged, entity_of("gene")))
    with refused(f"{rule(1)}.*only check_claims accepts"):
        record(ev, regulation(PlannedEntity("gene", 9606), entity_of("gene")))
    with refused(f"{rule(2)}.*a transcriptional_regulation object is a gene"):
        record(ev, regulation(entity_of("gene"), entity_of("protein")))
    with refused(f"{rule(3)}.*is directed, and the record states the pair undirected"):
        record(ev, regulation(stated_directed=False))
    genetic_type()
    gene = entity_of("gene")
    with refused(f"{rule(4)}.*test_genetic relates two different entities"):
        record(ev, claim("test_genetic", gene, gene))


# ── The rows written ────────────────────────────────────────────────────────


def _recorded(ev):
    result = record(
        ev,
        regulation(
            methods=(MethodRef(TermFactory(curie="MI:0018"), "detection"),),
            publications=(PublicationRef(PublicationFactory(), "reported_in"),),
            scores=(ScoreValue(ScoreTypeFactory(slug="stats:test_value"), "1"),),
            contexts=(ContextValue("tissue", "liver"),),
        ),
    )
    return result.assertions[0].assertion


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_published_evidence_is_never_changed_on_any_path(ev, path):
    assertion = _recorded(ev)
    advance(ev.release, PUBLISHED)
    rows = [assertion] + [
        model.objects.get(assertion=assertion) for model in PART_MODELS[1:]
    ]
    rows.append(assertion.participants.get(side="subject"))
    changes = {
        EvidenceAssertion: {"effect_as_stated": "edited"},
        AssertionParticipant: {"identifier_as_stated": "edited"},
        EvidenceMethod: {"role": "participant_identification"},
        EvidencePublication: {"role": "cited"},
        EvidenceScore: {"raw_value": "2"},
        EvidenceContext: {"raw_value": "lung"},
    }
    for row in rows:
        before = stored(row)
        with pytest.raises(ImmutableRecordError):
            write(path, row, **changes[type(row)])
        assert stored(row) == before


@pytest.mark.parametrize("path", DELETE_PATHS)
@pytest.mark.parametrize("status", [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN])
def test_evidence_past_draft_is_never_deleted(ev, path, status):
    assertion = _recorded(ev)
    advance(ev.release, status)
    before = snapshot()
    for row in [
        *(model.objects.filter(assertion=assertion).first() for model in PART_MODELS),
        assertion,
    ]:
        with pytest.raises(
            ImmutableRecordError, match="only while their release is a draft"
        ):
            delete(path, row)
        # A pk alone is judged by the stored row.
        with pytest.raises(ImmutableRecordError):
            type(row)(pk=row.pk).delete()
    assert snapshot() == before


def test_parts_are_added_only_while_the_release_is_a_draft(ev):
    assertion = _recorded(ev)
    advance(ev.release, STAGED)
    with pytest.raises(ImmutableRecordError, match="only while its release is a draft"):
        EvidenceContextFactory(assertion=assertion, raw_value="lung")
    with pytest.raises(ImmutableRecordError, match="only while its release is a draft"):
        EvidenceContext.objects.bulk_create(
            [EvidenceContextFactory.build(assertion=assertion, raw_value="lung")]
        )
    assert assertion.contexts.count() == 1


def test_a_stored_part_is_never_saved_again_even_in_a_draft(ev):
    assertion = _recorded(ev)
    context = assertion.contexts.get()
    with pytest.raises(ImmutableRecordError, match="never saved again"):
        context.save()
    with pytest.raises(ImmutableRecordError, match="never saved again"):
        assertion.save()


def test_a_draft_releases_deletion_takes_its_evidence_and_keeps_relationships(
    registry,
):
    # Synthetic: no licence snapshot or approval keeps the release (PROTECT).
    synthetic = evidence_release(synthetic=True)
    paper = Publication(
        local_dataset=synthetic.release.dataset, local_key="p1", is_synthetic=True
    )
    paper.save()
    record(
        synthetic,
        regulation(
            entity_of("gene", 32630),
            entity_of("gene", 32630),
            methods=(MethodRef(TermFactory(curie="MI:0018"), "detection"),),
            publications=(PublicationRef(paper, "reported_in"),),
            scores=(ScoreValue("synthetic:confidence", "0.9"),),
            contexts=(ContextValue("tissue", "liver"),),
        ),
    )
    assert all(snapshot().values())
    synthetic.release.delete()
    counts = snapshot()
    assert counts.pop("Relationship") == 1
    assert set(counts.values()) == {0}


def test_a_record_or_run_with_evidence_is_never_deleted_alone(ev):
    assertion = _recorded(ev)
    with pytest.raises(RestrictedError):
        SourceRecord.objects.get(pk=assertion.source_record_id).delete()
    with pytest.raises(RestrictedError):
        ImportRun.objects.get(pk=ev.run.pk).delete()
    assert EvidenceAssertion.objects.filter(pk=assertion.pk).exists()


def test_a_record_with_evidence_stays_mapped(ev):
    assertion = _recorded(ev)
    source = SourceRecord.objects.get(pk=assertion.source_record_id)
    source.status = RecordStatus.REJECTED
    with pytest.raises(ImmutableRecordError, match="stays mapped"):
        source.save()


# ── The number of queries ───────────────────────────────────────────────────


def test_record_claims_runs_as_many_queries_for_five_parts_as_for_one(ev):
    terms = [TermFactory() for _ in range(5)]
    score_types = [ScoreTypeFactory() for _ in range(5)]
    papers = [PublicationFactory() for _ in range(5)]

    def with_parts(count):
        return regulation(
            methods=tuple(MethodRef(term, "detection") for term in terms[:count]),
            scores=tuple(ScoreValue(st, "0.5") for st in score_types[:count]),
            contexts=tuple(ContextValue("tissue", f"t{n}") for n in range(count)),
            publications=tuple(PublicationRef(p, "cited") for p in papers[:count]),
        )

    counts = []
    for at, count in ((0, 1), (1, 5)):
        made = with_parts(count)
        with CaptureQueriesContext(connection) as queries:
            record(ev, made, at=at)
        counts.append(len(queries.captured_queries))
    assert counts[0] == counts[1], counts
    assert EvidenceMethod.objects.count() == 6


def test_reading_a_record_back_runs_as_many_queries_for_many_assertions(ev):
    papers = [PublicationFactory() for _ in range(4)]

    def reported(count):
        return regulation(
            publications=tuple(
                PublicationRef(p, "reported_in", {"pmid": p.pmid})
                for p in papers[:count]
            )
        )

    small, large = reported(2), reported(4)
    record(ev, small, at=0)
    record(ev, large, at=1)
    counts = []
    for at, made in ((0, small), (1, large)):
        with CaptureQueriesContext(connection) as queries:
            assert not record(ev, made, at=at).assertions[0].created
        counts.append(len(queries.captured_queries))
    assert counts[0] == counts[1], counts


# ── check_claims ────────────────────────────────────────────────────────────


BAD_CLAIMS = {
    "unknown type": lambda: claim("interacts", entity_of("gene"), entity_of("gene")),
    "swapped": lambda: regulation(entity_of("gene"), entity_of("protein")),
    "negative effect": lambda: regulation(effect="negative"),
    "effect on tested absent": lambda: regulation(
        effect="activating", outcome="tested_absent"
    ),
    "colocalization": lambda: physical(
        level=InteractionLevel.ASSOCIATION, interaction_type="MI:0403"
    ),
    "level without type": lambda: physical(level=InteractionLevel.DIRECT_INTERACTION),
    "eco method": lambda: regulation(methods=(MethodRef("ECO:0000353", "detection"),)),
    "bad key part": lambda: regulation(key_part="-x"),
    "probability above one": lambda: regulation(
        scores=(
            ScoreValue(
                ScoreTypeFactory(
                    slug="stats:test_probability",
                    value_kind=ScoreValueKind.PROBABILITY,
                ),
                "1.5",
            ),
        )
    ),
    "synthetic score type": lambda: regulation(
        scores=(ScoreValue("synthetic:confidence", "0.5"),)
    ),
    "qualifiers": lambda: regulation(qualifiers={1: "x"}),
    "pseudo-taxon host": lambda: regulation(
        contexts=(ContextValue("host_taxon", "-1", taxon=-1),)
    ),
}


@pytest.mark.parametrize("name", BAD_CLAIMS)
def test_check_claims_refuses_in_record_claims_words(ev, name):
    TermFactory(curie="MI:0403")
    made = BAD_CLAIMS[name]()
    with refused("Rule") as checked:
        check_claims([made], release=ev.release)
    with refused("Rule") as recorded:
        record(ev, made)
    assert str(checked.value) == str(recorded.value)


def test_check_claims_writes_nothing_and_needs_no_run(ev):
    regulator, target = entity_of("gene"), entity_of("gene")
    claims = [
        regulation(regulator, target, key_part="a"),
        regulation(PlannedEntity("gene", 9606), target, key_part="b"),
        physical(
            level=InteractionLevel.DIRECT_INTERACTION,
            interaction_type="MI:0407",
            methods=(MethodRef("MI:0018", "detection"),),
            publications=(
                PublicationRef(None, "reported_in", {"pmid": "PMID:12345"}),
                PublicationRef(None, "reported_in", {"doi": "10.1/X"}),
            ),
            key_part="c",
        ),
    ]
    before = snapshot()
    with CaptureQueriesContext(connection) as queries:
        plan = check_claims(claims, release=ev.release.pk)
    assert snapshot() == before
    assert not [
        q
        for q in queries.captured_queries
        if re.match(r"\s*(INSERT|UPDATE|DELETE)", q["sql"])
    ]
    assert [c.assertion_keys for c in plan.claims] == [
        ("a",),
        ("b",),
        ("c|pub:doi:10.1/x", "c|pub:pmid:12345"),
    ]
    assert plan.claims[0].canonical.subject_id == regulator.pk
    assert plan.claims[0].relationship_exists is False
    # A planned endpoint has no identity yet.
    assert (plan.claims[1].canonical, plan.claims[1].relationship_exists) == (
        None,
        None,
    )
    assert plan.assertions_exist is None
    assert plan.assertion_keys == ("a", "b", "c|pub:doi:10.1/x", "c|pub:pmid:12345")
    # The terms named are not stored: record_claims would refuse them.
    with refused("names no stored term: MI:0407"):
        record(ev, dataclasses.replace(claims[2], key_part=""))


def test_check_claims_still_checks_what_is_not_stored_yet(ev):
    planned = PlannedEntity("protein", 9606)
    with refused(f"{rule(9)}.*MI:0403 is not in the PSI-MI physical crosswalk"):
        check_claims(
            [physical(level="association", interaction_type="MI:0403")],
            release=ev.release,
        )
    with refused(f"{rule(2)}.*the subject, a planned gene, is a gene"):
        check_claims([physical(PlannedEntity("gene", 9606))], release=ev.release)
    genetic_type()
    same = PlannedEntity("gene", 9606)
    with refused(f"{rule(4)}.*test_genetic relates two different entities"):
        check_claims([claim("test_genetic", same, same)], release=ev.release)
    with refused(f"{rule(13)}.*synthetic constructs"):
        check_claims([physical(PlannedEntity("protein", 32630))], release=ev.release)
    with refused(f"{rule(11)}.*these state none"):
        check_claims(
            [regulation(publications=(PublicationRef(None, "reported_in"),))],
            release=ev.release,
        )
    # Two planned entities of one type are two entities.
    plan = check_claims(
        [physical(planned, PlannedEntity("protein", 9606))], release=ev.release
    )
    assert plan.claims[0].canonical is None


def test_check_claims_compares_a_record_as_record_claims_would(ev):
    regulator, target = entity_of("gene"), entity_of("gene")
    made = regulation(regulator, target, effect="activating")
    plan = check_claims([made], release=ev.release, source_record=ev.records[0])
    assert plan.assertions_exist is False
    record(ev, made)
    plan = check_claims([made], release=ev.release, source_record=ev.records[0].pk)
    assert plan.assertions_exist is True
    assert plan.claims[0].relationship_exists is True
    with refused("differing main \\(effect\\)", ClaimConflict):
        check_claims(
            [regulation(regulator, target, effect="inhibiting")],
            release=ev.release,
            source_record=ev.records[0],
        )
    other = evidence_release(records=1)
    with refused(f"belongs to release {other.release.pk}, not to release"):
        check_claims([made], release=ev.release, source_record=other.records[0])
    with refused("Unknown release"):
        check_claims([made], release=10**12)
