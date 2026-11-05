"""Release statistics (graph.stats.release_stats): every key, recounted from
rows, against an independent recount written here and against figures
stated by hand; zeros for every status and outcome; distinct counts of
relationships, entities and publications; and a release's figures kept
apart from another's that shares its entities, relationship and paper.

The evidence is recorded with G3's record_claims directly, as a commit
records it."""

from collections import Counter, defaultdict

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graph.claims import Claim, Endpoint, PublicationRef, ScoreValue, record_claims
from graph.entities import create_entity
from graph.models import (
    AssertionMethod,
    AssertionParticipant,
    Entity,
    EvidenceAssertion,
    EvidenceBasis,
    EvidencePublication,
    InteractionLevel,
    Outcome,
    RecordStatus,
    Relationship,
    SourceRecord,
)
from graph.stats import release_stats
from graph.tests.factories import (
    PublicationFactory,
    entity_of,
    evidence_release,
    mapped_record,
    p_value_score_type,
)

# The criterion a not-significant result failed.
P_BELOW = [{"score_type": "stats:test_p", "operator": "<", "value": "0.05"}]
ZERO_RECORDS = {status: 0 for status in RecordStatus.values}
ZERO_OUTCOMES = {outcome: 0 for outcome in Outcome.values}


def claim(slug, subject, obj, paper=None, **fields) -> Claim:
    """A claim of a stated relationship between two stored entities, citing
    the paper if given."""
    fields.setdefault("outcome", Outcome.SUPPORTED)
    publications = ()
    if paper is not None:
        publications = (PublicationRef(paper, "reported_in", {"pmid": paper.pmid}),)
    return Claim(
        relationship_type=slug,
        subject=Endpoint(subject, f"SYN-{subject.pk}", "primary"),
        object=Endpoint(obj, f"SYN-{obj.pk}", "primary"),
        evidence_basis=EvidenceBasis.EXPERIMENTAL,
        assertion_method=AssertionMethod.MANUAL_ASSERTION,
        publications=publications,
        **fields,
    )


def regulation(regulator, target, paper=None, **fields) -> Claim:
    return claim("transcriptional_regulation", regulator, target, paper, **fields)


def recount(release) -> dict:
    """The figures counted another way: in Python, over every row of each
    table, with nothing filtered or grouped by the database."""
    records = Counter(
        record.status
        for record in SourceRecord.objects.all()
        if record.release_id == release.pk
    )
    assertions = [
        assertion
        for assertion in EvidenceAssertion.objects.all()
        if assertion.release_id == release.pk
    ]
    ids = {assertion.pk for assertion in assertions}
    types = {
        relationship.pk: relationship.relationship_type_id
        for relationship in Relationship.objects.all()
    }
    by_type = defaultdict(list)
    for assertion in assertions:
        by_type[types[assertion.relationship_id]].append(assertion.relationship_id)
    return {
        "version": 1,
        "records": {status: records[status] for status in RecordStatus.values},
        "assertions": len(assertions),
        "assertions_by_outcome": {
            outcome: sum(1 for assertion in assertions if assertion.outcome == outcome)
            for outcome in Outcome.values
        },
        "relationships": len({assertion.relationship_id for assertion in assertions}),
        "entities": len(
            {
                participant.entity_id
                for participant in AssertionParticipant.objects.all()
                if participant.assertion_id in ids
            }
        ),
        "publications": len(
            {
                cited.publication_id
                for cited in EvidencePublication.objects.all()
                if cited.assertion_id in ids
            }
        ),
        "entities_created": sum(
            1
            for entity in Entity.objects.all()
            if entity.created_by_release_id == release.pk
        ),
        "relationship_types": {
            slug: {"assertions": len(named), "relationships": len(set(named))}
            for slug, named in sorted(by_type.items())
        },
    }


@pytest.fixture
def evidence(registry):
    """A release whose records state, between three genes and two proteins:
    two assertions on one regulation (one supported, citing paper X; one
    tested absent, citing X again), an inconclusive and a not-significant
    regulation, and a supported physical interaction citing paper Y; four
    records of the other statuses; and one entity it created."""
    ev = evidence_release(records=5)
    release, run = ev.release, ev.run
    a, b, c = entity_of("gene"), entity_of("gene"), entity_of("gene")
    p, q = entity_of("protein"), entity_of("protein")
    x, y = PublicationFactory(), PublicationFactory()
    p_value_score_type()
    claims = [
        regulation(a, b, x),
        regulation(a, b, x, outcome=Outcome.TESTED_ABSENT),
        regulation(b, c, outcome=Outcome.INCONCLUSIVE),
        regulation(
            c,
            a,
            outcome=Outcome.NOT_SIGNIFICANT,
            scores=(ScoreValue("stats:test_p", "0.4"),),
            qualifiers={"threshold": P_BELOW},
        ),
        claim(
            "physical_interaction",
            p,
            q,
            y,
            interaction_level=InteractionLevel.NOT_STATED,
        ),
    ]
    for record, stated in zip(ev.records, claims):
        record_claims(record, [stated], import_run=run)
    for status in (
        RecordStatus.UNRESOLVED,
        RecordStatus.UNRESOLVED,
        RecordStatus.AMBIGUOUS,
        RecordStatus.REJECTED,
    ):
        mapped_record(release, run, status=status)
    create_entity("gene", "Gene five", 9606, "hgnc", "HGNC:5", release=release)
    return ev, (a, b, c), x


def test_the_statistics_of_a_release_with_evidence(evidence):
    ev, _, _ = evidence
    stats = release_stats(ev.release)
    assert stats == {
        "version": 1,
        "records": {
            **ZERO_RECORDS,
            "mapped": 5,
            "unresolved": 2,
            "ambiguous": 1,
            "rejected": 1,
        },
        "assertions": 5,
        "assertions_by_outcome": {
            "supported": 2,
            "tested_absent": 1,
            "inconclusive": 1,
            "not_significant": 1,
        },
        # A-B, B-C and C-A, and P-Q: A-B's two assertions name one.
        "relationships": 4,
        "entities": 5,
        # X, cited twice, and Y.
        "publications": 2,
        "entities_created": 1,
        "relationship_types": {
            "physical_interaction": {"assertions": 1, "relationships": 1},
            "transcriptional_regulation": {"assertions": 4, "relationships": 3},
        },
    }
    assert stats == recount(ev.release)
    assert list(stats) == [
        "version",
        "records",
        "assertions",
        "assertions_by_outcome",
        "relationships",
        "entities",
        "publications",
        "entities_created",
        "relationship_types",
    ]
    assert list(stats["records"]) == RecordStatus.values
    assert list(stats["assertions_by_outcome"]) == Outcome.values
    assert release_stats(ev.release.pk) == stats


def test_a_release_without_rows_counts_zero_everywhere(registry):
    release = evidence_release(records=0).release
    stats = release_stats(release)
    assert stats == {
        "version": 1,
        "records": ZERO_RECORDS,
        "assertions": 0,
        "assertions_by_outcome": ZERO_OUTCOMES,
        "relationships": 0,
        "entities": 0,
        "publications": 0,
        "entities_created": 0,
        "relationship_types": {},
    }
    assert stats == recount(release)


def test_each_release_counts_only_its_own_rows(evidence):
    ev, (a, b, _), x = evidence
    before = release_stats(ev.release)
    # A second release states one of the first's regulations again, citing
    # the same paper: the relationship, entities and paper are shared, each
    # release keeps its own assertion.
    other = evidence_release(records=2)
    record_claims(other.records[0], [regulation(a, b, x)], import_run=other.run)
    assert release_stats(ev.release) == before == recount(ev.release)
    stats = release_stats(other.release)
    assert stats == {
        "version": 1,
        "records": {**ZERO_RECORDS, "mapped": 2},
        "assertions": 1,
        "assertions_by_outcome": {**ZERO_OUTCOMES, "supported": 1},
        "relationships": 1,
        "entities": 2,
        "publications": 1,
        "entities_created": 0,
        "relationship_types": {
            "transcriptional_regulation": {"assertions": 1, "relationships": 1}
        },
    }
    assert stats == recount(other.release)


def test_each_figure_is_one_query_whatever_the_release_holds(evidence):
    ev, _, _ = evidence
    empty = evidence_release(records=0).release
    counted = []
    for release in (empty, ev.release):
        with CaptureQueriesContext(connection) as captured:
            release_stats(release)
        counted.append(len(captured))
    # records, outcomes (and so assertions), relationships, entities,
    # publications, entities created, relationship types.
    assert counted == [7, 7]


def test_a_release_is_given_as_a_row_or_its_id(registry):
    with pytest.raises(TypeError, match="A release or its id"):
        release_stats("1")
    with pytest.raises(TypeError, match="A release or its id"):
        release_stats(True)
