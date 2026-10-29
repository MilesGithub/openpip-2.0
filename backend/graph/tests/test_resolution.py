"""The four resolution statuses, counted over distinct entities.

Non-primary identifiers are factory rows here: nothing imports them before
RF1a adds their provenance.
"""

import re

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from graph.entities import create_entity
from graph.models import Entity, IdentifierNamespace, IdentifierRole
from graph.resolution import (
    AMBIGUOUS,
    CANDIDATE,
    RESOLVED,
    UNMATCHED,
    Match,
    lookups_ignoring_case,
    resolve,
    resolve_many,
)
from graph.tests.factories import EntityIdentifierFactory

WRITES = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def namespace(slug):
    return IdentifierNamespace.objects.get(slug=slug)


def kinds(candidate):
    return [match.kind for match in candidate.matches]


@pytest.fixture
def tp53(registry):
    return create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")


# ── resolved ────────────────────────────────────────────────────────────────


def test_one_entity_matched_by_an_exact_primary_identifier_is_resolved(tp53):
    for term, options in (
        ("P04637", {}),
        ("p04637", {}),
        ("uniprotkb:P04637", {}),
        ("P04637", {"namespace": "uniprotkb"}),
        ("uniprotkb:P04637", {"namespace": "uniprotkb"}),
        ("P04637", {"taxon": 9606, "entity_type": "protein"}),
    ):
        resolution = resolve(term, **options)
        assert resolution.status == RESOLVED, (term, options)
        [candidate] = resolution.candidates
        assert candidate.entity == tp53
        assert candidate.exact_primary
        assert candidate.matches == (
            Match("primary", "uniprotkb", "P04637", True, False),
        )


def test_a_label_equal_to_the_primary_value_counts_once(registry):
    entity = create_entity("protein", "P04637", 9606, "uniprotkb", "P04637")
    resolution = resolve("P04637")
    assert resolution.status == RESOLVED
    [candidate] = resolution.candidates
    assert candidate.entity == entity
    assert kinds(candidate) == ["primary", "label"]


def test_an_hgnc_id_resolves_with_its_prefix_or_its_namespace_named(registry):
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    for term, options in (
        ("HGNC:11998", {}),
        ("hgnc:11998", {}),
        ("11998", {"namespace": "hgnc"}),
        ("HGNC:011998", {"namespace": "hgnc"}),
    ):
        resolution = resolve(term, **options)
        assert resolution.status == RESOLVED, term
        assert resolution.candidates[0].entity == gene
    # A bare number, with no namespace named, could be an NCBI Gene ID.
    assert resolve("11998").status == UNMATCHED


# ── ambiguous ───────────────────────────────────────────────────────────────


def test_a_primary_value_in_two_taxon_scoped_namespaces_is_ambiguous(
    test_namespaces,
):
    in_a = create_entity(
        "gene", "dorsal", test_namespaces.taxon_a, test_namespaces.exact_a, "dl"
    )
    in_b = create_entity(
        "gene", "dorsal", test_namespaces.taxon_b, test_namespaces.exact_b, "dl"
    )
    resolution = resolve("dl")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == [in_a, in_b]
    assert all(c.exact_primary for c in resolution.candidates)

    narrowed = resolve("dl", taxon=test_namespaces.taxon_b)
    assert narrowed.status == RESOLVED
    assert narrowed.candidates[0].entity == in_b


def test_a_primary_value_that_is_another_entitys_secondary_is_ambiguous(registry):
    # As after a UniProt split: the value is A's accession and is kept on B.
    a = create_entity("protein", "A", 9606, "uniprotkb", "P68250")
    b = create_entity("protein", "B", 9606, "uniprotkb", "P68251")
    EntityIdentifierFactory(
        entity=b,
        namespace=namespace("uniprotkb"),
        value="P68250",
        role=IdentifierRole.SECONDARY,
    )
    resolution = resolve("P68250")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == [a, b]
    assert [c.exact_primary for c in resolution.candidates] == [True, False]
    assert kinds(resolution.candidates[1]) == ["secondary"]


def test_the_exact_primary_match_is_listed_before_earlier_type_and_taxon_groups(
    registry,
):
    # By taxon alone the fly protein (7227) would come before the human one.
    fly = create_entity("protein", "F", 7227, "uniprotkb", "Q9VXX1")
    human = create_entity("protein", "A", 9606, "uniprotkb", "P68250")
    EntityIdentifierFactory(
        entity=fly,
        namespace=namespace("uniprotkb"),
        value="P68250",
        role=IdentifierRole.SECONDARY,
    )
    resolution = resolve("P68250")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == [human, fly]
    assert [c.exact_primary for c in resolution.candidates] == [True, False]


def test_a_split_accession_kept_on_both_new_entries_is_ambiguous(registry):
    # UniProt's own example: P29358 was "demerged" into P68250 and P68251,
    # and kept as a secondary accession on both.
    entries = [
        create_entity("protein", label, 9606, "uniprotkb", accession)
        for label, accession in (("A", "P68250"), ("B", "P68251"))
    ]
    for entry in entries:
        EntityIdentifierFactory(
            entity=entry,
            namespace=namespace("uniprotkb"),
            value="P29358",
            role=IdentifierRole.SECONDARY,
        )
    resolution = resolve("P29358")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == entries


def test_a_label_shared_by_a_gene_and_a_protein_is_ambiguous(registry):
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    protein = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    resolution = resolve("TP53")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == [gene, protein]
    assert {c.entity.entity_type_id for c in resolution.candidates} == {
        "gene",
        "protein",
    }
    assert resolve("TP53", entity_type="protein").status == CANDIDATE
    # Labels match ignoring case when none matches as written.
    lower = resolve("tp53")
    assert lower.status == AMBIGUOUS
    assert [c.matches for c in lower.candidates] == [
        (Match("label", None, "TP53", False, False),)
    ] * 2


def test_an_exact_match_hides_its_case_variants(test_namespaces):
    # As for FlyBase symbols, here as identifiers and labels both: h is hairy
    # and H is Hairless, and neither is a case-mismatch candidate for the other.
    ns = test_namespaces
    hairy = create_entity("gene", "h", ns.taxon_a, ns.exact_a, "h")
    hairless = create_entity("gene", "H", ns.taxon_a, ns.exact_a, "H")
    for term, gene in (("h", hairy), ("H", hairless)):
        resolution = resolve(term)
        assert resolution.status == RESOLVED, term
        [candidate] = resolution.candidates
        assert candidate.entity == gene
        assert kinds(candidate) == ["primary", "label"]
        assert all(match.case_exact for match in candidate.matches)


# ── candidate ───────────────────────────────────────────────────────────────


def test_a_value_held_only_as_one_entitys_alias_is_a_candidate(test_namespaces):
    gene = create_entity(
        "gene", "hairy", test_namespaces.taxon_a, test_namespaces.exact_a, "h"
    )
    EntityIdentifierFactory(
        entity=gene, namespace=test_namespaces.symbols_a, value="hry", role="alias"
    )
    for options in ({}, {"taxon": test_namespaces.taxon_a}):
        resolution = resolve("hry", **options)
        assert resolution.status == CANDIDATE
        [candidate] = resolution.candidates
        assert candidate.entity == gene
        assert not candidate.exact_primary
        assert candidate.matches == (
            Match("alias", "test_symbols_a", "hry", True, False),
        )


def test_a_value_held_as_an_alias_of_two_entities_is_ambiguous(test_namespaces):
    genes = [
        create_entity(
            "gene", label, test_namespaces.taxon_a, test_namespaces.exact_a, v
        )
        for label, v in (("hairy", "h"), ("Hairless", "H"))
    ]
    for gene in genes:
        EntityIdentifierFactory(
            entity=gene, namespace=test_namespaces.symbols_a, value="Hry", role="alias"
        )
    resolution = resolve("Hry")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == genes


def test_a_symbol_in_two_organisms_is_ambiguous_until_a_taxon_is_given(
    test_namespaces,
):
    ns = test_namespaces
    in_a = create_entity("gene", "gene", ns.taxon_a, ns.exact_a, "g1")
    in_b = create_entity("gene", "gene", ns.taxon_b, ns.exact_b, "g1")
    for gene, symbols in ((in_a, ns.symbols_a), (in_b, ns.symbols_b)):
        EntityIdentifierFactory(
            entity=gene, namespace=symbols, value="Abc", role=IdentifierRole.SYMBOL
        )
    assert resolve("Abc").status == AMBIGUOUS
    narrowed = resolve("Abc", taxon=ns.taxon_a)
    assert narrowed.status == CANDIDATE
    assert narrowed.candidates[0].entity == in_a
    assert kinds(narrowed.candidates[0]) == ["symbol"]


def test_a_value_in_a_global_namespace_spans_taxa(test_namespaces):
    ns = test_namespaces
    in_a = create_entity("gene", "gene", ns.taxon_a, ns.exact_a, "g1")
    in_b = create_entity("gene", "gene", ns.taxon_b, ns.exact_b, "g1")
    for gene in (in_a, in_b):
        alias = EntityIdentifierFactory(
            entity=gene, namespace=ns.global_alias, value="Xyz", role="alias"
        )
        assert alias.scope_key == ""
    resolution = resolve("Xyz")
    assert resolution.status == AMBIGUOUS
    assert [c.entity for c in resolution.candidates] == [in_a, in_b]
    narrowed = resolve("Xyz", taxon=ns.taxon_b)
    assert narrowed.status == CANDIDATE
    assert narrowed.candidates[0].entity == in_b
    assert narrowed.candidates[0].matches == (
        Match("alias", "test_global_alias", "Xyz", True, False),
    )


def test_one_entitys_label_and_alias_make_one_candidate_with_two_matches(
    test_namespaces,
):
    gene = create_entity(
        "gene", "Hairless", test_namespaces.taxon_a, test_namespaces.exact_a, "H"
    )
    EntityIdentifierFactory(
        entity=gene, namespace=test_namespaces.symbols_a, value="Hairless"
    )
    resolution = resolve("Hairless")
    assert resolution.status == CANDIDATE
    [candidate] = resolution.candidates
    assert kinds(candidate) == ["alias", "label"]


def test_a_cross_reference_is_only_a_candidate(registry):
    # An NCBI Gene ID never defines a gene; it maps to one only as a stated
    # cross-reference, and then for confirmation.
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    EntityIdentifierFactory(
        entity=gene, namespace=namespace("ncbigene"), value="7157", role="xref"
    )
    for term in ("7157", "ncbigene:7157"):
        resolution = resolve(term)
        assert resolution.status == CANDIDATE
        assert resolution.candidates[0].matches == (
            Match("xref", "ncbigene", "7157", True, False),
        )


def test_a_case_mismatch_in_a_case_exact_namespace_is_a_candidate(test_namespaces):
    gene = create_entity(
        "gene", "Hairless", test_namespaces.taxon_a, test_namespaces.exact_a, "H"
    )
    for options in ({}, {"namespace": test_namespaces.exact_a}):
        resolution = resolve("h", **options)
        assert resolution.status == CANDIDATE
        [candidate] = resolution.candidates
        assert candidate.entity == gene
        assert candidate.matches == (
            Match("primary", "test_exact_a", "H", False, False),
        )


def test_a_version_mismatch_is_a_candidate(test_namespaces):
    gene = create_entity(
        "gene", "X1", test_namespaces.taxon_d, test_namespaces.versioned, "ENSX0001.4"
    )
    mismatch = resolve("ENSX0001.3")
    assert mismatch.status == CANDIDATE
    assert mismatch.candidates[0].entity == gene
    assert mismatch.candidates[0].matches == (
        Match("primary", "test_versioned", "ENSX0001.4", True, True),
    )
    # The same version, or none, names the identifier itself.
    assert resolve("ENSX0001.4").status == RESOLVED
    assert resolve("ENSX0001").status == RESOLVED


# ── unmatched, ordering and limits ──────────────────────────────────────────


def test_an_unknown_value_is_unmatched(tp53):
    for term in ("Q99999", "NOSUCHGENE", "", "   ", "hgnc:", "uniprotkb:TP53"):
        resolution = resolve(term)
        assert (resolution.status, resolution.candidates) == (UNMATCHED, ()), term
    assert resolve("P04637", namespace="ncbigene").status == UNMATCHED
    assert resolve("P04637", taxon=7227).status == UNMATCHED


def test_a_non_ascii_letter_that_upper_cases_to_ascii_is_unmatched(registry):
    # U+017F, long s, upper-cases to S: "q9ſ123" must not read as Q9S123.
    create_entity("protein", "Some protein", 9606, "uniprotkb", "Q9S123")
    assert resolve("q9ſ123").status == UNMATCHED


def test_unknown_filters_are_errors_not_empty_results(registry):
    with pytest.raises(ValueError, match="Unknown namespace"):
        resolve("P04637", namespace="uniprot")
    with pytest.raises(ValueError, match="not in the registry"):
        resolve("P04637", taxon=10090)
    with pytest.raises(ValueError, match="Unknown entity type"):
        resolve("P04637", entity_type="chemical")


def test_ordering_is_deterministic_and_the_limit_is_enforced(test_namespaces):
    genes = [
        create_entity(
            "gene",
            f"gene {n}",
            test_namespaces.taxon_a,
            test_namespaces.exact_a,
            f"g{n}",
        )
        for n in range(5)
    ]
    for gene in reversed(genes):
        EntityIdentifierFactory(
            entity=gene, namespace=test_namespaces.symbols_a, value="shared"
        )
    full = resolve("shared")
    assert full.status == AMBIGUOUS
    assert [c.entity for c in full.candidates] == genes
    assert resolve("shared") == full

    limited = resolve("shared", limit=3)
    assert limited.status == AMBIGUOUS
    assert [c.entity for c in limited.candidates] == genes[:3]
    with pytest.raises(ValueError):
        resolve("shared", limit=0)


def test_resolve_many_answers_in_order_from_a_constant_number_of_queries(
    test_namespaces,
):
    for n in range(25):
        create_entity(
            "gene",
            f"gene {n}",
            test_namespaces.taxon_a,
            test_namespaces.exact_a,
            f"g{n}",
        )
    few = ["g0", "uniprotkb:P04637", "nothing"]
    many = [f"g{n}" for n in range(23)] + few[1:]

    with CaptureQueriesContext(connection) as small:
        resolve_many(few)
    with CaptureQueriesContext(connection) as large:
        resolutions = resolve_many(many)
    assert len(large.captured_queries) == len(small.captured_queries) <= 6
    assert [r.status for r in resolutions[:23]] == [RESOLVED] * 23
    assert [r.candidates[0].entity.label for r in resolutions[:3]] == [
        "gene 0",
        "gene 1",
        "gene 2",
    ]
    assert [r.status for r in resolutions[23:]] == [UNMATCHED, UNMATCHED]


def test_the_resolver_writes_nothing(test_namespaces, tp53):
    gene = create_entity(
        "gene", "TP53", test_namespaces.taxon_a, test_namespaces.exact_a, "TP53"
    )
    EntityIdentifierFactory(entity=gene, namespace=test_namespaces.symbols_a)
    with CaptureQueriesContext(connection) as queries:
        resolve_many(["TP53", "tp53", "P04637", "intact:X", "11998"])
        resolve(
            "TP53", namespace=test_namespaces.exact_a, taxon=test_namespaces.taxon_a
        )
    assert queries.captured_queries
    assert [q["sql"] for q in queries.captured_queries if WRITES.match(q["sql"])] == []
    assert Entity.objects.count() == 2


# ── Indexes ─────────────────────────────────────────────────────────────────


def _plan(queryset) -> str:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
        return queryset.explain()


@pytest.mark.postgres
def test_a_label_prefix_search_uses_the_pattern_index(registry):
    create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    plan = _plan(Entity.objects.filter(label_key__startswith="TP5"))
    assert "Seq Scan" not in plan, plan
    used = re.search(r"Index (?:Only )?Scan using (\S+)", plan) or re.search(
        r"Bitmap Index Scan on (\S+)", plan
    )
    assert used and used.group(1).endswith("_like"), plan
    assert "~>=~" in plan, plan


@pytest.mark.postgres
def test_the_case_insensitive_lookup_uses_the_upper_index(tp53):
    plan = _plan(lookups_ignoring_case(["P04637", "H"]))
    assert "Seq Scan" not in plan, plan
    assert "graph_ident_lookup_upper" in plan, plan
