"""The shared mapping (graph.ingest.mapping): each endpoint rule in order,
candidates and ambiguous identifiers with and without resolution entries,
planned entities shared across records and sides, labels, papers and terms,
the synthetic check, the status precedence, G3's check, and agreement with
create_entity."""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graph.claims import CanonicalRelationship, ClaimError, check_claims
from graph.entities import (
    IdentityConflict,
    IdentityNamespaceError,
    add_identifier,
    create_entity,
)
from graph.ingest import limits, mapping
from graph.ingest.canonical_tsv import ADAPTER
from graph.ingest.dry_run import Report
from graph.ingest.mapping import (
    Create,
    Existing,
    ExistingPaper,
    NewPaper,
    Unmapped,
    Written,
    checked_claims,
    plan_batch,
)
from graph.ingest.resolutions import load_resolutions
from graph.models import (
    ApprovalScope,
    IdentifierNamespace,
    IdentifierRole,
    RecordStatus,
    ResolutionBasis,
)
from graph.tests.factories import (
    DatasetReleaseFactory,
    EntityIdentifierFactory,
    PublicationFactory,
    TermFactory,
    approvable_release,
    approve,
    protein_pair_type,
)
from graph.tests.ingest_helpers import (
    GENE,
    HEADER,
    PROTEIN,
    SYNTHETIC,
    codes,
    mapping_run,
    plans_of,
    records_of,
    row,
)

MAPPED, UNRESOLVED, AMBIGUOUS, REJECTED = (
    RecordStatus.MAPPED,
    RecordStatus.UNRESOLVED,
    RecordStatus.AMBIGUOUS,
    RecordStatus.REJECTED,
)


@pytest.fixture
def release(registry):
    """A draft release that is not synthetic, approved for internal use."""
    release = approvable_release()
    approve(release, ApprovalScope.INTERNAL)
    return release


@pytest.fixture
def synthetic_release(registry):
    return DatasetReleaseFactory(is_synthetic=True)


@pytest.fixture
def tp53(registry):
    return create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")


@pytest.fixture
def mdm2(registry):
    return create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973")


def entry(target, **fields) -> dict:
    """A resolution entry mapping hgnc HGNC:5 (a human gene) to `target`
    through its primary identifier; `fields` replace any key."""
    return {
        "namespace": "hgnc",
        "value": "HGNC:5",
        "entity_type": "gene",
        "taxon": 9606,
        "entity": target.pk,
        "target_namespace": "hgnc",
        "target_value": target.identifiers.get(role=IdentifierRole.PRIMARY).value,
        "rationale": "Checked against the source's supplementary table.",
        "reviewer": "A. Reviewer",
        **fields,
    }


def resolutions(*entries):
    return load_resolutions(json.dumps({"resolutions": list(entries)}).encode())


def targets(plan) -> list:
    return [endpoint.target for endpoint in plan.endpoints]


def issue(plan, code):
    [found] = [issue for issue in plan.issues if issue.code == code]
    return found


# ── Rule 1: the registry and identifier_problem ─────────────────────────────


@pytest.mark.parametrize(
    "base,cells,code,field",
    [
        # The namespace is checked before the type, the type before the taxon.
        (
            GENE,
            {"subject_namespace": "nowhere", "subject_type": "transcript"},
            "unknown_namespace",
            "subject_namespace",
        ),
        (
            GENE,
            {"subject_type": "transcript", "subject_taxon": "123456789"},
            "unknown_entity_type",
            "subject_type",
        ),
        (GENE, {"subject_taxon": "123456789"}, "unknown_taxon", "subject_taxon"),
        (GENE, {"subject_taxon": ""}, "bad_taxon", "subject_taxon"),
        (
            GENE,
            {"subject_namespace": "uniprotkb", "subject_id": "P04637"},
            "namespace_not_for_type",
            "subject_type",
        ),
        (GENE, {"subject_id": "HGNC:0"}, "bad_identifier", "subject_id"),
        (PROTEIN, {"subject_id": "P04637-2"}, "isoform_form", "subject_id"),
        # hgnc is fixed to 9606.
        (GENE, {"subject_taxon": "7227"}, "taxon_contradiction", "subject_taxon"),
    ],
)
def test_rule_one_refuses_the_identifier_with_its_code(
    release, base, cells, code, field
):
    [plan] = plans_of(mapping_run(release, create_entities=True), row(base, **cells))
    assert plan.status == REJECTED
    refused = [i for i in plan.issues if i.field.startswith("subject")]
    assert [(i.code, i.field, i.line) for i in refused] == [(code, field, 2)]
    assert refused[0].message.startswith("The subject, ")
    assert plan.endpoints[0].target is None


def test_a_cell_the_adapter_refused_is_not_checked_again(release):
    [plan] = plans_of(mapping_run(release), row(GENE, subject_taxon="09606"))
    assert codes(plan, "error") == ["bad_taxon"]
    assert plan.endpoints[0].target is None


def test_a_refused_identifier_keeps_no_long_value_for_the_run(release):
    run = mapping_run(release)
    written = "HGNC:" + "1" * 9_000
    first, second = plans_of(
        run,
        row(GENE, subject_id=written),
        row(GENE, record_id="r2", subject_id=written),
    )
    assert codes(first, "error") == codes(second, "error") == ["bad_identifier"]
    [refused] = run.refused.values()
    assert (refused.records, refused.lines) == (2, [2, 3])
    kept = [refused.namespace, refused.value, refused.entity_type, *refused.written]
    assert max(map(len, kept)) <= limits.QUOTED_VALUE_CHARS
    for key in [*run.refused, *run._stated]:
        assert all(len(part) <= 255 for part in key if isinstance(part, str)), key


def test_an_unknown_namespace_is_one_issue_per_record_side(release):
    [plan] = plans_of(
        mapping_run(release),
        row(GENE, subject_namespace="nowhere", object_namespace="nowhere"),
    )
    assert [(i.code, i.field) for i in plan.issues] == [
        ("unknown_namespace", "subject_namespace"),
        ("unknown_namespace", "object_namespace"),
    ]


# ── Rule 2: a global value's primary holder ─────────────────────────────────


def test_rule_two_refuses_a_global_value_held_at_another_taxon(release):
    tp53 = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    run = mapping_run(release, create_entities=True)
    [plan] = plans_of(run, row(PROTEIN, subject_taxon="7227"))
    # Filtered by taxon 7227 the value is unmatched, and would be created.
    assert plan.status == REJECTED
    found = issue(plan, "taxon_contradiction")
    assert found.field == "subject_taxon"
    assert f"primary identifier of entity {tp53.pk}, a protein of taxon 9606" in (
        found.message
    )
    assert not run.planned or all(
        planned.create.lookup != "P04637" for planned in run.planned.values()
    )


def test_one_value_stated_at_two_taxa_creates_nothing(release):
    run = mapping_run(release, create_entities=True)
    first, second = plans_of(
        run,
        row(PROTEIN, subject_id="P12345", object_id="Q00987"),
        row(PROTEIN, record_id="r2", subject_id="P12345", subject_taxon="7227"),
    )
    # Neither statement is trusted over the other: file order decides nothing.
    assert first.status == second.status == REJECTED
    for plan in (first, second):
        message = issue(plan, "taxon_contradiction").message
        assert "a protein of taxon 9606 (line 2)" in message
        assert "a protein of taxon 7227 (line 3)" in message
        assert "none is created" in message
        assert plan.endpoints[0].target is None
    assert run.planned["uniprotkb||P12345"].contradicted


# ── Rule 3: the resolver's status ───────────────────────────────────────────


def test_a_resolved_pair_maps_by_primary_identifiers(release, tp53, mdm2):
    [plan] = plans_of(mapping_run(release), GENE)
    assert plan.status == MAPPED and plan.issues == []
    assert targets(plan) == [
        Existing(tp53.pk, ResolutionBasis.PRIMARY, "TP53"),
        Existing(mdm2.pk, ResolutionBasis.PRIMARY, "MDM2"),
    ]
    assert plan.relationships == (
        (
            CanonicalRelationship("transcriptional_regulation", tp53.pk, mdm2.pk, True),
            False,
        ),
    )


def test_an_unmatched_value_is_a_planned_creation(release, tp53):
    for flag, status in ((False, UNRESOLVED), (True, MAPPED)):
        [plan] = plans_of(mapping_run(release, create_entities=flag), GENE)
        assert plan.status == status
        target = targets(plan)[1]
        assert target == Create(
            "hgnc",
            "HGNC:6973",
            "HGNC:6973",
            "gene",
            9606,
            "HGNC:6973",
            "hgnc|t:9606|HGNC:6973",
        )
        notes = [i for i in plan.issues if i.code == "unmatched"]
        if flag:
            assert notes == []
        else:
            assert "--create-entities would create it" in notes[0].message


def test_an_unmatched_value_that_creates_nothing_stays_unresolved(release, tp53):
    run = mapping_run(release, create_entities=True)
    [plan] = plans_of(run, row(GENE, object_namespace="ncbigene", object_id="7157"))
    assert plan.status == UNRESOLVED
    target = targets(plan)[1]
    assert isinstance(target, Unmapped)
    assert (target.status, target.code) == (UNRESOLVED, "not_identity_namespace")
    note = issue(plan, "unmatched")
    assert note.severity == "note" and "created only from hgnc" in note.message


def test_a_candidate_is_listed_and_never_mapped(release, tp53):
    add_identifier(tp53, "hgnc", "HGNC:5", IdentifierRole.SECONDARY)
    [plan] = plans_of(
        mapping_run(release, create_entities=True), row(GENE, object_id="HGNC:5")
    )
    assert plan.status == UNRESOLVED
    target = targets(plan)[1]
    assert (target.status, target.code) == (UNRESOLVED, "candidate")
    [candidate] = target.candidates
    assert candidate["entity"] == tp53.pk
    assert candidate["primary"] == "hgnc:HGNC:11998"
    assert candidate["status"] == tp53.status
    assert candidate["matches"] == [
        {
            "kind": "secondary",
            "namespace": "hgnc",
            "value": "HGNC:5",
            "case_exact": True,
            "version_mismatch": False,
        }
    ]
    note = issue(plan, "candidate")
    assert f"entity {tp53.pk} (hgnc:HGNC:11998" in note.message
    assert "matched as secondary hgnc 'HGNC:5'" in note.message


def test_an_ambiguous_value_is_listed_with_every_candidate(release, tp53, mdm2):
    for gene in (tp53, mdm2):
        add_identifier(gene, "hgnc", "HGNC:5", IdentifierRole.SECONDARY)
    [plan] = plans_of(mapping_run(release), row(GENE, object_id="HGNC:5"))
    assert plan.status == AMBIGUOUS
    target = targets(plan)[1]
    assert target.status == AMBIGUOUS
    assert {c["entity"] for c in target.candidates} == {tp53.pk, mdm2.pk}
    assert "is ambiguous" in issue(plan, "candidate").message


# ── Resolution entries ──────────────────────────────────────────────────────


def test_an_entry_maps_a_candidate_to_one_of_its_candidates(release, tp53, mdm2):
    for gene in (tp53, mdm2):
        add_identifier(gene, "hgnc", "HGNC:5", IdentifierRole.SECONDARY)
    run = mapping_run(release, resolutions=resolutions(entry(mdm2)))
    [plan] = plans_of(run, row(GENE, object_id="HGNC:5"))
    assert plan.status == MAPPED
    assert targets(plan)[1] == Existing(mdm2.pk, ResolutionBasis.CURATED, "MDM2")
    note = issue(plan, "curated_mapping")
    assert "entry 1 of the resolution file maps it to entity" in note.message
    assert "A. Reviewer" in note.message and "supplementary table" in note.message
    assert run.entry_uses[1] == {"mapped"}


def test_an_entry_maps_an_unmatched_value(release, tp53, mdm2):
    run = mapping_run(release, resolutions=resolutions(entry(mdm2)))
    [plan] = plans_of(run, row(GENE, object_id="HGNC:5"))
    assert plan.status == MAPPED
    assert targets(plan)[1] == Existing(mdm2.pk, ResolutionBasis.CURATED, "MDM2")
    assert codes(plan) == ["curated_mapping"]


def test_an_entry_naming_no_candidate_maps_nothing(release, tp53, mdm2):
    add_identifier(tp53, "hgnc", "HGNC:5", IdentifierRole.SECONDARY)
    run = mapping_run(release, resolutions=resolutions(entry(mdm2)))
    [plan] = plans_of(run, row(GENE, object_id="HGNC:5"))
    assert plan.status == UNRESOLVED
    assert (
        f"entry 1 of the resolution file names entity {mdm2.pk}, which is not among"
        in issue(plan, "candidate").message
    )
    assert run.entry_uses[1] == {"not_a_candidate"}


def test_entries_that_contradict_repeat_or_go_unused(release, tp53, mdm2):
    third = create_entity("gene", "Third", 9606, "hgnc", "HGNC:3")
    run = mapping_run(
        release,
        resolutions=resolutions(
            # HGNC:11998 resolves to TP53: an entry naming another contradicts.
            entry(third, value="HGNC:11998"),
            # HGNC:6973 resolves to MDM2: an entry naming it is redundant.
            entry(mdm2, value="HGNC:6973"),
            # No row states HGNC:999.
            entry(tp53, value="HGNC:999"),
        ),
    )
    [plan] = plans_of(run, GENE)
    assert plan.status == REJECTED
    contradiction = issue(plan, "resolution_contradicts")
    assert contradiction.field == "subject_id"
    assert f"resolves to entity {tp53.pk}, and entry 1" in contradiction.message
    assert targets(plan)[1] == Existing(mdm2.pk, ResolutionBasis.PRIMARY, "MDM2")
    assert dict(run.entry_uses) == {1: {"contradicted"}, 2: {"redundant"}}


# ── Planned entities ────────────────────────────────────────────────────────


def test_a_planned_entity_is_shared_across_records_and_sides(release, tp53):
    run = mapping_run(release, create_entities=True)
    first, second = plans_of(
        run,
        row(GENE, subject_id="HGNC:5", object_id="HGNC:6"),
        row(GENE, record_id="r2", subject_id="HGNC:6", object_id="hgnc:5"),
    )
    assert (first.status, second.status) == (MAPPED, MAPPED)
    five, six = run.planned["hgnc|t:9606|HGNC:5"], run.planned["hgnc|t:9606|HGNC:6"]
    assert run.entity_of(first.endpoints[0]) is five.entity
    assert run.entity_of(second.endpoints[1]) is five.entity
    assert run.entity_of(first.endpoints[1]) is run.entity_of(second.endpoints[0])
    assert (five.records, five.lines, six.lines) == (2, [2, 3], [2, 3])


def test_a_self_pair_of_one_new_identifier_is_refused_by_rule_four(release):
    protein_pair_type()
    run = mapping_run(release, create_entities=True)
    [planned] = plans_of(
        run,
        row(
            PROTEIN,
            relationship_type="test_protein_pair",
            subject_id="P12345",
            object_id="p12345",
        ),
    )
    assert planned.status == REJECTED
    assert codes(planned) == ["claim_rule_4"]
    assert "relates two different entities" in planned.issues[0].message


def test_an_unmapped_identifier_on_both_sides_is_one_placeholder(release, tp53):
    protein_pair_type()
    create_entity("protein", "A", 9606, "uniprotkb", "P11111")
    other = create_entity("protein", "B", 9606, "uniprotkb", "P22222")
    for entity in (other, create_entity("protein", "C", 9606, "uniprotkb", "P33333")):
        add_identifier(entity, "uniprotkb", "Q99999", IdentifierRole.SECONDARY)
    run = mapping_run(release)
    [plan] = plans_of(
        run,
        row(
            PROTEIN,
            relationship_type="test_protein_pair",
            subject_id="Q99999",
            object_id="Q99999",
        ),
    )
    assert plan.status == REJECTED
    assert codes(plan, "error") == ["claim_rule_4"]
    assert run.entity_of(plan.endpoints[0]) is run.entity_of(plan.endpoints[1])


def test_a_gene_where_the_relationship_needs_a_protein_is_a_rule_two_rejection(
    release,
):
    [plan] = plans_of(
        mapping_run(release),
        row(
            PROTEIN, subject_namespace="hgnc", subject_id="HGNC:5", subject_type="gene"
        ),
    )
    assert plan.status == REJECTED
    assert codes(plan, "error") == ["claim_rule_2"]


# ── Labels ──────────────────────────────────────────────────────────────────


def test_a_planned_entitys_label_is_the_first_a_mapped_row_gives(release, tp53):
    run = mapping_run(release, create_entities=True)
    plans = plans_of(
        run,
        # A rejected row gives no label.
        row(GENE, record_id="r0", object_id="HGNC:5", object_label="Wrong", effect="x"),
        row(GENE, record_id="r1", object_id=" HGNC:5 "),
        row(GENE, record_id="r2", object_id="HGNC:5", object_label="Gene five"),
        row(GENE, record_id="r3", object_id="HGNC:5", object_label="Other"),
        row(GENE, record_id="r4", object_id="HGNC:5", object_label="Gene five"),
    )
    planned = run.planned["hgnc|t:9606|HGNC:5"]
    assert (planned.chosen, planned.label_line, planned.written) == (
        "Gene five",
        4,
        "HGNC:5",
    )
    assert (planned.other_labels, planned.other_label_count) == ({"Other": 5}, 1)
    assert [targets(plan)[1].label for plan in plans] == [
        "HGNC:5",
        "HGNC:5",
        "Gene five",
        "Gene five",
        "Gene five",
    ]
    assert plans[0].status == REJECTED and planned.records == 4


def test_without_a_label_a_planned_entity_is_named_by_its_identifier(release, tp53):
    run = mapping_run(release, create_entities=True)
    plans_of(run, row(GENE, object_id=" hgnc:5"))
    assert run.planned["hgnc|t:9606|HGNC:5"].chosen == "hgnc:5"


def test_an_existing_entitys_label_never_changes(release, tp53, mdm2):
    same, other = plans_of(
        mapping_run(release),
        row(GENE, subject_label="TP53"),
        row(GENE, record_id="r2", subject_label="p53"),
    )
    assert codes(same) == []
    note = issue(other, "label_differs")
    assert (note.severity, note.field) == ("note", "subject_label")
    assert f"entity {tp53.pk}'s, 'TP53'" in note.message
    assert other.status == MAPPED


# ── Papers and terms ────────────────────────────────────────────────────────


def test_papers_and_terms_are_found_or_planned(release):
    p53 = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    mdm2 = create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    paper = PublicationFactory(pmid="123")
    TermFactory(curie="MI:0018", label="two hybrid")
    run = mapping_run(release)
    [plan] = plans_of(
        run,
        row(
            PROTEIN,
            publications="pmid:123|ref:paper-a|doi:10.1000/XYZ",
            interaction_type="MI:0915",
            method="MI:0018",
            eco="ECO:0000353",
        ),
    )
    assert plan.status == MAPPED, plan.issues
    assert [(p.stated, p.target) for p in plan.papers] == [
        ({"pmid": "123"}, ExistingPaper(paper.pk)),
        ({"local": "paper-a"}, NewPaper("local", "paper-a", "A paper", None, False)),
        ({"doi": "10.1000/XYZ"}, NewPaper("doi", "10.1000/XYZ")),
    ]
    assert plan.terms == (
        ("ECO:0000353", "physical interaction evidence"),
        ("MI:0915", "physical association"),
    )
    [claim] = checked_claims(plan, run)
    assert [ref.publication for ref in claim.publications] == [paper.pk, None, None]
    assert (claim.subject.entity, claim.object.entity) == (p53.pk, mdm2.pk)


def test_two_items_naming_one_stored_paper_are_a_duplicate(release, tp53, mdm2):
    paper = PublicationFactory(pmid="123", doi="10.1000/ABC")
    [plan] = plans_of(
        mapping_run(release), row(GENE, publications="pmid:123|doi:10.1000/abc")
    )
    assert plan.status == REJECTED
    found = issue(plan, "duplicate_paper")
    assert (found.field, found.line) == ("publications", 2)
    assert "pmid '123'" in found.message and "doi '10.1000/abc'" in found.message
    assert f"publication {paper.pk}" in found.message


def test_a_pmid_and_a_doi_of_two_stored_papers_are_two_papers(release, tp53, mdm2):
    PublicationFactory(pmid="123")
    PublicationFactory(pmid="456", doi="10.1000/xyz")
    [plan] = plans_of(
        mapping_run(release), row(GENE, publications="pmid:123|doi:10.1000/xyz")
    )
    assert plan.status == MAPPED
    assert codes(plan) == ["pmid_and_doi"]


# ── The synthetic check ─────────────────────────────────────────────────────


def test_a_synthetic_row_maps_to_synthetic_constructs(synthetic_release):
    [plan] = plans_of(mapping_run(synthetic_release, create_entities=True), SYNTHETIC)
    assert plan.status == MAPPED, plan.issues
    assert [target.taxon for target in targets(plan)] == [32630, 32630]


def test_every_readable_synthetic_row_is_checked_rejected_ones_included(
    synthetic_release,
):
    run = mapping_run(synthetic_release, create_entities=True)
    real, rejected = plans_of(
        run,
        row(
            SYNTHETIC,
            subject_namespace="hgnc",
            subject_id="HGNC:5",
            subject_taxon="9606",
        ),
        row(SYNTHETIC, record_id="r2", subject_label="TP53", effect="negative"),
    )
    assert real.status == rejected.status == REJECTED
    assert codes(real).count("synthetic_violation") == 2
    assert "synthetic_violation" in codes(rejected)
    # The identifiers of every readable row are resolved and reported.
    assert ("hgnc", "HGNC:5", "", "gene", 9606) in run.identifiers


@pytest.mark.parametrize(
    "cells",
    [
        {"subject_namespace": "hgnc", "subject_id": "HGNC:5", "subject_taxon": "9606"},
        {"object_taxon": "9606"},
        {"subject_label": "TP53"},
        {"publications": "pmid:123"},
        {"publications": "doi:10.1000/x"},
        {"context:host_taxon": "9606"},
    ],
    ids=["namespace", "taxon", "label", "pmid", "doi", "host taxon"],
)
def test_each_synthetic_violation_rejects_its_row(synthetic_release, cells):
    run = mapping_run(synthetic_release, create_entities=True)
    [plan] = plans_of(
        run, row(SYNTHETIC, **cells), header=[*HEADER, "context:host_taxon"]
    )
    assert plan.status == REJECTED
    assert "synthetic_violation" in codes(plan)


def test_a_synthetic_row_meets_no_resolution_entry_of_another_taxon(
    synthetic_release,
):
    tp53 = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    found = resolutions(
        entry(
            tp53,
            namespace="uniprotkb",
            value="Q99999",
            entity_type="protein",
            target_namespace="uniprotkb",
        )
    )
    run = mapping_run(synthetic_release, resolutions=found)
    [plan] = plans_of(
        run,
        row(
            PROTEIN,
            subject_id="Q99999",
            subject_taxon="9606",
            object_namespace="synthetic_protein",
            object_id="SYN-P1",
            object_taxon="32630",
        ),
    )
    messages = [i.message for i in plan.issues if i.code == "synthetic_violation"]
    assert any(
        "entry 1 of the resolution file" in m and "taxon 9606" in m for m in messages
    )
    assert plan.status == REJECTED


def test_reference_types_are_refused_in_a_real_release_only(release, synthetic_release):
    encodes = row(
        SYNTHETIC,
        relationship_type="encodes",
        object_namespace="synthetic_protein",
        object_id="SYN-P1",
        object_type="protein",
        object_label="SYN-P1",
    )
    [real] = plans_of(
        mapping_run(release, create_entities=True),
        row(
            GENE,
            relationship_type="encodes",
            object_namespace="uniprotkb",
            object_id="P04637",
            object_type="protein",
        ),
    )
    assert real.status == REJECTED and "reference_type_not_allowed" in codes(real)
    [synthetic] = plans_of(
        mapping_run(synthetic_release, create_entities=True), encodes
    )
    assert synthetic.status == MAPPED, synthetic.issues


# ── Statuses ────────────────────────────────────────────────────────────────


def test_rejected_comes_before_ambiguous_before_unresolved_before_mapped(
    release, tp53, mdm2
):
    other = create_entity("gene", "Other", 9606, "hgnc", "HGNC:3")
    for gene in (mdm2, other):
        add_identifier(gene, "hgnc", "HGNC:5", IdentifierRole.SECONDARY)
    plans = plans_of(
        mapping_run(release),
        # Ambiguous and unmatched: ambiguous.
        row(GENE, subject_id="HGNC:5", object_id="HGNC:7"),
        # Ambiguous and an error: rejected.
        row(GENE, record_id="r2", subject_id="HGNC:5", effect="negative"),
        # Unmatched without --create-entities: unresolved.
        row(GENE, record_id="r3", object_id="HGNC:7"),
        row(GENE, record_id="r4"),
    )
    assert [plan.status for plan in plans] == [AMBIGUOUS, REJECTED, UNRESOLVED, MAPPED]
    assert not plans[1].relationships


# ── G3's check ──────────────────────────────────────────────────────────────


def test_a_claim_g3_refuses_in_a_batch_is_refused_in_its_own_words(release, tp53, mdm2):
    run = mapping_run(release)
    rows = [row(GENE, record_id=f"r{n}") for n in range(1, 6)]
    rows[2] = row(GENE, record_id="r3", directed="false")
    plans = plans_of(run, *rows)
    assert [plan.status for plan in plans] == [MAPPED] * 2 + [REJECTED] + [MAPPED] * 2
    refused = issue(plans[2], "claim_rule_3")
    # check_claims on that record alone refuses it in the same words.
    with pytest.raises(ClaimError) as raised:
        check_claims(checked_claims(plans[2], run), release=release.pk)
    assert refused.message == str(raised.value)
    assert refused.message.startswith("Rule 3 (stated direction), claim 1:")


def test_a_refusal_that_names_no_claim_splits_the_batch(
    release, tp53, mdm2, monkeypatch
):
    calls = []
    check = mapping.check_claims

    def unnamed(claims, **kwargs):
        calls.append(len(claims))
        if len(claims) > 1:
            raise ClaimError("Rule 6 (closed values): a refusal naming no claim.")
        return check(claims, **kwargs)

    monkeypatch.setattr(mapping, "check_claims", unnamed)
    plans = plans_of(
        mapping_run(release), *[row(GENE, record_id=f"r{n}") for n in range(4)]
    )
    assert [plan.status for plan in plans] == [MAPPED] * 4
    assert calls == [4, 2, 1, 1, 2, 1, 1]


def counted_checks(monkeypatch) -> list:
    """The number of claims of each check_claims call."""
    calls = []
    check = mapping.check_claims

    def counted(claims, **kwargs):
        calls.append(len(claims))
        return check(claims, **kwargs)

    monkeypatch.setattr(mapping, "check_claims", counted)
    return calls


def test_a_refusal_in_a_batch_checks_no_claim_twice(release, tp53, mdm2, monkeypatch):
    calls = counted_checks(monkeypatch)
    rows = [row(GENE, record_id=f"r{n}") for n in range(1, 7)]
    rows[1] = row(GENE, record_id="r2", directed="false")
    rows[4] = row(GENE, record_id="r5", directed="false")
    plans = plans_of(mapping_run(release), *rows)
    assert [plan.status for plan in plans] == [MAPPED, REJECTED] + [MAPPED] * 2 + [
        REJECTED,
        MAPPED,
    ]
    for plan in (plans[1], plans[4]):
        message = issue(plan, "claim_rule_3").message
        assert message.startswith("Rule 3 (stated direction), claim 1:")
    # The batch, refused at r2: r1, which passed, and r2 alone. Then r3 to
    # r5, refused at r5: r3 and r4, r5 alone. Then r6.
    assert calls == [6, 1, 1, 3, 2, 1, 1]
    assert all(plan.relationships for plan in plans if plan.status == MAPPED)


@pytest.mark.parametrize("refused", ["every", "every other"])
def test_the_claims_checked_stay_linear_in_the_batch(
    release, tp53, mdm2, monkeypatch, refused
):
    calls = counted_checks(monkeypatch)
    size = 40
    rows = [
        (
            row(GENE, record_id=f"r{n}", directed="false")
            if refused == "every" or n % 2
            else row(GENE, record_id=f"r{n}")
        )
        for n in range(size)
    ]
    plans = plans_of(mapping_run(release), *rows)
    assert all(
        (plan.status == REJECTED) == (refused == "every" or n % 2 == 1)
        for n, plan in enumerate(plans)
    )
    # Not a check of the whole rest of the batch per refusal.
    assert sum(calls) <= 4 * size
    assert len(calls) <= 2 * size + 2


def test_one_refusal_costs_a_few_calls_whatever_the_batch(
    release, tp53, mdm2, monkeypatch
):
    calls = counted_checks(monkeypatch)

    def checks(size):
        calls.clear()
        rows = [row(GENE, record_id="r0", directed="false")]
        rows += [row(GENE, record_id=f"r{n}") for n in range(1, size)]
        plans_of(mapping_run(release), *rows)
        return len(calls), sum(calls)

    small, large = checks(10), checks(80)
    assert small[0] == large[0] <= 5
    assert large[1] <= 3 * 80


def test_g3s_notes_are_kept_for_the_record(release):
    create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    [plan] = plans_of(
        mapping_run(release),
        row(PROTEIN, **{"context:host_taxon": "123456789"}),
        header=[*HEADER, "context:host_taxon"],
    )
    assert plan.status == MAPPED
    assert plan.notes == (
        "host taxon '123456789' names no registered taxon, so only its raw value "
        "is kept.",
    )


# ── Duplicates within a batch ───────────────────────────────────────────────


def test_a_later_line_of_a_record_id_is_unstored(release, tp53, mdm2):
    plans = plans_of(
        mapping_run(release),
        row(GENE, record_id="a"),
        row(GENE, record_id="a"),
        row(GENE, record_id="b"),
        row(GENE, record_id="b", evidence_group="other"),
    )
    assert [plan.unstored for plan in plans] == [False, True, False, True]
    assert codes(plans[1]) == ["duplicate_record"]
    assert codes(plans[3]) == ["conflicting_duplicate"]
    assert plans[0].status == MAPPED
    # Neither version of b is imported.
    assert plans[2].status == REJECTED
    assert "on line 5" in issue(plans[2], "conflicting_duplicate").message


# ── Queries ─────────────────────────────────────────────────────────────────


def queries(run, rows) -> list:
    records = records_of(run, *rows)
    with CaptureQueriesContext(connection) as captured:
        plan_batch(records, run_ctx=run)
    return [query["sql"] for query in captured]


def test_identifiers_are_resolved_once_per_run(release, tp53, mdm2):
    run = mapping_run(release)
    first = queries(run, [row(GENE, record_id="r1")])
    again = queries(run, [row(GENE, record_id="r2")])
    assert any("graph_entityidentifier" in sql for sql in first)
    assert not any("graph_entityidentifier" in sql for sql in again)
    assert len(again) < len(first)


def test_a_batch_takes_a_constant_number_of_queries(release, tp53, mdm2):
    def count(size):
        rows = [row(GENE, record_id=f"r{n}") for n in range(size)]
        rows += [
            row(GENE, record_id=f"new{n}", object_id=f"HGNC:{100 + n}")
            for n in range(size)
        ]
        return len(queries(mapping_run(release, create_entities=True), rows))

    assert count(3) == count(30)


# ── Agreement with create_entity ────────────────────────────────────────────


AGREEMENT = {
    "a new human gene": (GENE, {"object_id": "HGNC:5"}, None),
    "a fly protein": (PROTEIN, {"object_taxon": "7227"}, None),
    "a cross-reference namespace": (
        GENE,
        {"object_namespace": "ncbigene", "object_id": "7157"},
        IdentityNamespaceError,
    ),
    "a value held as an alias": (GENE, {"object_id": "HGNC:77"}, IdentityConflict),
}


@pytest.mark.parametrize("case", sorted(AGREEMENT))
def test_the_mapping_creates_what_create_entity_creates(release, tp53, case):
    base, cells, refusal = AGREEMENT[case]
    EntityIdentifierFactory(
        entity=tp53,
        namespace=IdentifierNamespace.objects.get(slug="hgnc"),
        value="HGNC:77",
        role=IdentifierRole.ALIAS,
    )
    if base is PROTEIN:
        create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    [plan] = plans_of(mapping_run(release, create_entities=True), row(base, **cells))
    target = targets(plan)[1]
    stated = plan.endpoints[1].stated

    def create():
        return create_entity(
            stated.entity_type,
            stated.label or stated.value,
            stated.taxon,
            stated.namespace,
            stated.value,
            release=release,
        )

    if refusal is None:
        assert isinstance(target, Create) and plan.status == MAPPED
        assert create().identity_key == target.identity_key
    else:
        assert isinstance(target, Unmapped) and plan.status == UNRESOLVED
        with pytest.raises(refusal):
            create()


# ── The report of planned pairs ─────────────────────────────────────────────


def test_a_symmetric_pair_stated_in_both_orders_counts_once(release):
    run = mapping_run(release, create_entities=True)
    plans = plans_of(
        run,
        row(PROTEIN, subject_id="P12345", object_id="P67890"),
        row(PROTEIN, record_id="r2", subject_id="P67890", object_id="P12345"),
        # A directed pair stated both ways is two.
        row(GENE, record_id="r3", subject_id="HGNC:5", object_id="HGNC:6"),
        row(GENE, record_id="r4", subject_id="HGNC:6", object_id="HGNC:5"),
    )
    assert [plan.status for plan in plans] == [MAPPED] * 4
    report = Report(release, ADAPTER, True)
    report.ctx, report.mapping_run = run.ctx, run
    report.add(plans, Written())
    relationships = report.finish()["relationships"]
    assert (relationships["existing"], relationships["new"]) == (0, 3)
    shown = {
        (
            entry["relationship_type"],
            entry["subject"]["planned"],
            entry["object"]["planned"],
        ): entry["records"]
        for entry in relationships["entries"]
    }
    assert shown == {
        ("physical_interaction", "uniprotkb||P12345", "uniprotkb||P67890"): 2,
        ("transcriptional_regulation", "hgnc|t:9606|HGNC:5", "hgnc|t:9606|HGNC:6"): 1,
        ("transcriptional_regulation", "hgnc|t:9606|HGNC:6", "hgnc|t:9606|HGNC:5"): 1,
    }
