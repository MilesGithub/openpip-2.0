"""Normalization: how a written value becomes a lookup, a version and a scope."""

import pytest

from graph.entities import NamespaceTypeError, create_entity
from graph.identity import (
    InvalidIdentifier,
    identity_key,
    normalize,
    parse_prefixed,
    scope_key,
    uniprot_entity_type,
)
from graph.models import Entity, IdentifierNamespace, Scope
from graph.resolution import RESOLVED, UNMATCHED, resolve


def namespace(slug):
    return IdentifierNamespace.objects.get(slug=slug)


# ── UniProtKB: entries and isoforms ─────────────────────────────────────────


def test_a_uniprot_value_keeps_its_original_and_gets_an_upper_case_lookup(registry):
    assert normalize(namespace("uniprotkb"), "p04637") == ("P04637", "")
    entity = create_entity("protein", "TP53", 9606, "uniprotkb", "p04637")
    primary = entity.identifiers.get()
    assert (primary.value, primary.lookup) == ("p04637", "P04637")


def test_a_suffixed_accession_is_an_isoform_never_its_entry(registry):
    assert uniprot_entity_type("P04637-2") == "protein_isoform"
    assert uniprot_entity_type("P04637") == "protein"
    assert normalize(namespace("uniprotkb"), "P04637-2") == ("P04637-2", "")

    isoform = create_entity("protein_isoform", "TP53", 9606, "uniprotkb", "P04637-2")
    assert isoform.identity_key == "uniprotkb||P04637-2"
    assert resolve("P04637").status == UNMATCHED


def test_the_dash_1_isoform_is_its_own_entity_never_collapsed_into_the_entry(
    registry,
):
    assert uniprot_entity_type("P04637-1") == "protein_isoform"
    entry = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    isoform = create_entity("protein_isoform", "TP53", 9606, "uniprotkb", "P04637-1")
    assert entry.pk != isoform.pk
    assert Entity.objects.count() == 2

    # Neither stands for the other: the entry is not its canonical isoform.
    [only_entry] = resolve("P04637").candidates
    assert only_entry.entity == entry
    [only_isoform] = resolve("P04637-1").candidates
    assert only_isoform.entity == isoform

    # A declared type that contradicts the value is refused.
    with pytest.raises(NamespaceTypeError):
        create_entity("protein", "TP53", 9606, "uniprotkb", "P04637-1")
    with pytest.raises(NamespaceTypeError):
        create_entity("protein_isoform", "TP53", 9606, "uniprotkb", "P04637")


@pytest.mark.parametrize(
    "value", ["P0463", "P04637.2", "P04637-0", "P04637-01", "P04637_2", "Q9-1234"]
)
def test_values_that_are_not_uniprot_accessions_are_rejected(registry, value):
    with pytest.raises(InvalidIdentifier):
        normalize(namespace("uniprotkb"), value)


# ── HGNC IDs ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "written", ["HGNC:11998", "hgnc:11998", "Hgnc:011998", " HGNC:11998 ", "11998"]
)
def test_an_hgnc_lookup_is_canonical(registry, written):
    assert normalize(namespace("hgnc"), written) == ("HGNC:11998", "")


def test_a_bare_number_is_an_hgnc_id_only_when_the_namespace_is_named(registry):
    hgnc = namespace("hgnc")
    with pytest.raises(InvalidIdentifier):
        normalize(hgnc, "11998", explicit=False)
    assert normalize(hgnc, "HGNC:11998", explicit=False) == ("HGNC:11998", "")


@pytest.mark.parametrize("value", ["HGNC:0", "HGNC:", "HGNC:12a", "HGNC 11998", "TP53"])
def test_values_that_are_not_hgnc_ids_are_rejected(registry, value):
    with pytest.raises(InvalidIdentifier):
        normalize(namespace("hgnc"), value)


# ── Case and version policies, on test-only namespaces ──────────────────────


def test_a_case_exact_namespace_keeps_h_and_h_as_two_identifiers(test_namespaces):
    exact = test_namespaces.exact_a
    assert normalize(exact, "h") == ("h", "")
    assert normalize(exact, "H") == ("H", "")
    hairy = create_entity("gene", "hairy", test_namespaces.taxon_a, exact, "h")
    hairless = create_entity("gene", "Hairless", test_namespaces.taxon_a, exact, "H")
    assert hairy.pk != hairless.pk
    lookups = [e.identifiers.get().lookup for e in (hairy, hairless)]
    assert lookups == ["h", "H"]
    # Each resolves to its own gene: the other is not a case-mismatch for it.
    for value, gene in (("h", hairy), ("H", hairless)):
        resolution = resolve(value, namespace=exact)
        assert resolution.status == RESOLVED
        assert [c.entity for c in resolution.candidates] == [gene]


def test_a_case_insensitive_namespace_matches_both_and_reports_case_exact_false(
    test_namespaces,
):
    insensitive = test_namespaces.insensitive
    assert normalize(insensitive, "h") == normalize(insensitive, "H") == ("H", "")
    gene = create_entity("gene", "Hairless", test_namespaces.taxon_c, insensitive, "H")
    # One identifier, whichever case it is written in.
    same = create_entity("gene", "Hairless", test_namespaces.taxon_c, insensitive, "h")
    assert same.pk == gene.pk

    lower = resolve("h", namespace=insensitive)
    [match] = lower.candidates[0].matches
    assert (lower.status, match.value, match.case_exact) == (RESOLVED, "H", False)
    [match] = resolve("H", namespace=insensitive).candidates[0].matches
    assert match.case_exact is True


def test_a_versioned_value_splits_into_lookup_and_version(test_namespaces):
    versioned = test_namespaces.versioned
    assert normalize(versioned, "ENSX0001.4") == ("ENSX0001", "4")
    assert normalize(versioned, "ENSX0001") == ("ENSX0001", "")
    gene = create_entity("gene", "X1", test_namespaces.taxon_d, versioned, "ENSX0001.4")
    primary = gene.identifiers.get()
    assert (primary.value, primary.lookup, primary.version) == (
        "ENSX0001.4",
        "ENSX0001",
        "4",
    )


def test_ensembl_gene_splits_the_version_off(registry):
    assert normalize(namespace("ensembl_gene"), "ENSG00000141510.21") == (
        "ENSG00000141510",
        "21",
    )
    assert normalize(namespace("ensembl_gene"), "ensg00000141510") == (
        "ENSG00000141510",
        "",
    )
    with pytest.raises(InvalidIdentifier):
        normalize(namespace("ensembl_gene"), "ENST00000269305")


@pytest.mark.parametrize(
    "slug, value",
    [
        # U+017F, long s, upper-cases to S; the pattern must not see it as S.
        ("uniprotkb", "q9ſ123"),
        ("ensembl_gene", "enſg00000141510"),
    ],
)
def test_a_letter_that_upper_cases_to_ascii_is_not_accepted(registry, slug, value):
    with pytest.raises(InvalidIdentifier):
        normalize(namespace(slug), value)


# ── Prefixes ────────────────────────────────────────────────────────────────


def test_parse_prefixed_splits_a_curie_and_leaves_other_values_alone():
    assert parse_prefixed("UniProtKB:P04637") == ("uniprotkb", "P04637")
    assert parse_prefixed(" hgnc:HGNC:5 ") == ("hgnc", "HGNC:5")
    assert parse_prefixed("P04637") == (None, "P04637")
    assert parse_prefixed("HLA-A*02:01") == (None, "HLA-A*02:01")
    assert parse_prefixed("intact:") == (None, "intact:")


def test_an_unknown_prefix_is_unmatched_never_read_as_a_gene_name(test_namespaces):
    # The legacy upload parser strips any prefix and keeps the rest as a gene
    # name (backend/datasets/upload_parser.py:63-77). The typed resolver never
    # does: an entity labelled, or identified, EBI-1234 is not found this way.
    gene = create_entity(
        "gene", "EBI-1234", test_namespaces.taxon_a, test_namespaces.exact_a, "EBI-1234"
    )
    assert parse_prefixed("intact:EBI-1234") == ("intact", "EBI-1234")
    assert resolve("intact:EBI-1234").status == UNMATCHED
    assert resolve("EBI-1234").candidates[0].entity == gene


# ── Scope and identity keys ─────────────────────────────────────────────────


def test_scope_key_is_empty_for_global_namespaces_and_tagged_for_scoped_ones(
    registry,
):
    assert scope_key(namespace("uniprotkb"), 9606) == ""
    assert scope_key(namespace("uniprotkb"), None) == ""
    assert scope_key(namespace("hgnc"), 9606) == "t:9606"
    assert scope_key(namespace("synthetic_gene"), 32630) == "t:32630"
    assembly = IdentifierNamespace(slug="test_assembly", scope=Scope.ASSEMBLY)
    assert scope_key(assembly, None, assembly="GRCh38") == "a:GRCh38"


def test_a_namespace_fixed_to_a_taxon_refuses_any_other(registry):
    with pytest.raises(ValueError, match="taxon 9606, not 7227"):
        scope_key(namespace("hgnc"), 7227)
    with pytest.raises(ValueError, match="belong to a taxon"):
        scope_key(namespace("hgnc"), None)
    with pytest.raises(ValueError, match="belong to an assembly"):
        scope_key(IdentifierNamespace(slug="x", scope=Scope.ASSEMBLY), 9606)


def test_identity_key_joins_namespace_scope_and_lookup(registry):
    assert identity_key(namespace("hgnc"), "t:9606", "HGNC:5") == "hgnc|t:9606|HGNC:5"
    assert identity_key(namespace("uniprotkb"), "", "P04637") == "uniprotkb||P04637"


def test_values_longer_than_the_column_are_rejected(registry):
    with pytest.raises(InvalidIdentifier, match="at most 255"):
        normalize(namespace("ncbigene"), "1" * 256)
