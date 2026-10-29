"""Database constraints on entities and identifiers, and the write path's rules."""

import threading
import time

import pytest
from django.db import IntegrityError, connection, transaction

from graph.entities import (
    IdentityConflict,
    IdentityNamespaceError,
    NamespaceTypeError,
    TaxonPolicyError,
    add_identifier,
    create_entity,
)
from graph.identity import identity_key
from graph.models import Entity, EntityIdentifier, IdentifierNamespace, IdentifierRole
from graph.tests.factories import (
    EntityFactory,
    EntityIdentifierFactory,
    TaxonFactory,
)


def namespace(slug):
    return IdentifierNamespace.objects.get(slug=slug)


@pytest.fixture
def tp53(registry):
    return create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")


# ── Database constraints ────────────────────────────────────────────────────


def test_a_second_primary_identifier_for_the_same_value_is_refused(tp53):
    other = EntityFactory(entity_type_id="protein", taxon_id=9606)
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityIdentifierFactory(
            entity=other,
            namespace=namespace("uniprotkb"),
            value="P04637",
            role=IdentifierRole.PRIMARY,
        )


def test_a_second_primary_identifier_on_one_entity_is_refused(tp53):
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityIdentifierFactory(
            entity=tp53,
            namespace=namespace("uniprotkb"),
            value="Q00987",
            role=IdentifierRole.PRIMARY,
        )


@pytest.mark.parametrize("role", ["xref", "secondary", "alias", "symbol"])
def test_the_same_non_primary_value_may_sit_on_two_entities(tp53, role):
    # A UniProt split leaves the old accession on each new entry, and an HGNC
    # previous symbol can point at several genes.
    other = create_entity("protein", "TP53 split", 9606, "uniprotkb", "P68250")
    for entity in (tp53, other):
        EntityIdentifierFactory(
            entity=entity, namespace=namespace("uniprotkb"), value="P29358", role=role
        )
    assert EntityIdentifier.objects.filter(lookup="P29358", role=role).count() == 2


def test_the_role_check_accepts_exactly_the_five_roles(tp53):
    assert set(IdentifierRole.values) == {
        "primary",
        "xref",
        "secondary",
        "alias",
        "symbol",
    }
    for role in ("xref", "secondary", "alias", "symbol"):
        EntityIdentifierFactory(
            entity=tp53, namespace=namespace("uniprotkb"), value="Q99999", role=role
        )
    for role in ("label", "PRIMARY", ""):
        with pytest.raises(IntegrityError), transaction.atomic():
            EntityIdentifierFactory(
                entity=tp53, namespace=namespace("uniprotkb"), value="Q88888", role=role
            )


def test_merged_without_merged_into_violates_the_check(registry):
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityFactory(entity_type_id="gene", taxon_id=9606, status="merged")
    survivor = EntityFactory(entity_type_id="gene", taxon_id=9606)
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityFactory(entity_type_id="gene", taxon_id=9606, merged_into=survivor)
    EntityFactory(
        entity_type_id="gene", taxon_id=9606, status="merged", merged_into=survivor
    )


def test_an_unknown_status_violates_the_check(registry):
    with pytest.raises(IntegrityError), transaction.atomic():
        EntityFactory(entity_type_id="gene", taxon_id=9606, status="deleted")


@pytest.mark.parametrize("taxid", [0, -1])
def test_a_taxid_must_be_positive(db, taxid):
    # PSI-MI's pseudo-taxa (-1 "in vitro" and the rest) are never taxa.
    with pytest.raises(IntegrityError), transaction.atomic():
        TaxonFactory(taxid=taxid)


def test_an_identity_key_of_the_longest_parts_fits_the_column(registry):
    longest_slug = IdentifierNamespace(slug="s" * 50)
    key = identity_key(longest_slug, "t:" + "9" * 62, "L" * 255)
    assert len(key) == 50 + 1 + 64 + 1 + 255
    assert len(key) <= Entity._meta.get_field("identity_key").max_length
    EntityFactory(entity_type_id="gene", taxon_id=9606, identity_key=key)
    assert Entity.objects.filter(identity_key=key).exists()


# ── The write path's rules ──────────────────────────────────────────────────


def test_a_gene_without_a_taxon_fails_validation(registry):
    with pytest.raises(TaxonPolicyError):
        create_entity("gene", "TP53", None, "hgnc", "HGNC:11998")
    with pytest.raises(TaxonPolicyError):
        create_entity("gene", "TP53", 123456789, "hgnc", "HGNC:11998")
    assert not Entity.objects.exists()


def test_a_uniprot_identifier_on_a_gene_fails_validation(registry):
    with pytest.raises(NamespaceTypeError):
        create_entity("gene", "TP53", 9606, "uniprotkb", "P04637")
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    with pytest.raises(NamespaceTypeError):
        add_identifier(gene, "uniprotkb", "P04637", IdentifierRole.XREF)
    assert gene.identifiers.count() == 1


def test_a_human_gene_is_created_only_from_an_hgnc_id(registry):
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    assert gene.identity_key == "hgnc|t:9606|HGNC:11998"
    for slug, value in (("ncbigene", "7157"), ("ensembl_gene", "ENSG00000141510")):
        with pytest.raises(IdentityNamespaceError, match="only from hgnc"):
            create_entity("gene", "TP53", 9606, slug, value)
    assert Entity.objects.count() == 1


def test_no_namespace_creates_a_fly_gene(registry):
    # Nothing defines gene identity for taxon 7227 yet (open question D7), and
    # the global cross-reference namespaces never stand in. The values are
    # valid in form; which genes they name does not matter.
    for slug, value in (
        ("hgnc", "HGNC:4851"),
        ("ncbigene", "30970"),
        ("ensembl_gene", "ENSG00000123456"),
        ("synthetic_gene", "SYN-H"),
    ):
        with pytest.raises(IdentityNamespaceError, match="No namespace defines"):
            create_entity("gene", "h", 7227, slug, value)
    assert not Entity.objects.exists()


@pytest.mark.parametrize("role", ["xref", "secondary", "alias", "symbol"])
def test_a_value_held_in_any_role_never_creates_a_second_entity(tp53, role):
    EntityIdentifierFactory(
        entity=tp53, namespace=namespace("uniprotkb"), value="Q15086", role=role
    )
    with pytest.raises(IdentityConflict) as conflict:
        create_entity("protein", "p53", 9606, "uniprotkb", "Q15086")
    assert conflict.value.entity_id == tp53.pk
    assert conflict.value.entity_ids == (tp53.pk,)
    assert Entity.objects.count() == 1


def test_identity_conflict_lists_every_holder(tp53):
    other = create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    for entity in (tp53, other):
        EntityIdentifierFactory(
            entity=entity, namespace=namespace("uniprotkb"), value="P29358"
        )
    with pytest.raises(IdentityConflict) as conflict:
        create_entity("protein", "split", 9606, "uniprotkb", "P29358")
    assert conflict.value.entity_ids == tuple(sorted((tp53.pk, other.pk)))
    assert conflict.value.entity_id is None


def test_an_entity_is_never_created_without_its_defining_identifier(registry):
    # Rows written around create_entity: a gene holding P04637 as its primary.
    gene = EntityFactory(entity_type_id="gene", taxon_id=9606)
    EntityIdentifierFactory(
        entity=gene,
        namespace=namespace("uniprotkb"),
        value="P04637",
        role=IdentifierRole.PRIMARY,
    )
    with pytest.raises(IdentityConflict) as conflict:
        create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    assert conflict.value.entity_id == gene.pk
    assert not Entity.objects.filter(identity_key="uniprotkb||P04637").exists()


def test_an_accession_of_another_taxon_is_a_conflict_not_an_override(tp53):
    with pytest.raises(IdentityConflict) as conflict:
        create_entity("protein", "p53", 7227, "uniprotkb", "P04637")
    assert conflict.value.entity_id == tp53.pk


def test_a_gene_and_a_protein_with_the_same_label_are_separate_entities(registry):
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    protein = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    assert gene.pk != protein.pk
    assert (gene.entity_type_id, protein.entity_type_id) == ("gene", "protein")


def test_creating_the_same_entity_twice_gives_one_row(registry):
    first = create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    second = create_entity(
        "protein", "Cellular tumor antigen p53", 9606, "uniprotkb", "p04637"
    )
    assert first.pk == second.pk
    assert Entity.objects.count() == 1
    primary = EntityIdentifier.objects.get()
    assert (primary.role, primary.value) == ("primary", "P04637")
    assert Entity.objects.get().label == "TP53"


def test_create_entity_sets_label_key_and_a_provisional_status(registry):
    entity = create_entity(
        "protein", "p53 Tumour suppressor", 9606, "uniprotkb", "P04637"
    )
    entity.refresh_from_db()
    assert entity.label_key == "P53 TUMOUR SUPPRESSOR"
    assert entity.status == "provisional"
    assert entity.identity_key == "uniprotkb||P04637"


def test_a_label_longer_than_the_column_is_refused(registry):
    with pytest.raises(ValueError, match="at most 255"):
        create_entity("protein", "x" * 256, 9606, "uniprotkb", "P04637")
    assert not Entity.objects.exists()
    assert not EntityIdentifier.objects.exists()


def test_synthetic_proteins_follow_the_isoform_rule(registry):
    entry = create_entity("protein", "SYN-P1", 32630, "synthetic_protein", "SYN-P1")
    isoform = create_entity(
        "protein_isoform", "SYN-P1 isoform 1", 32630, "synthetic_protein", "SYN-P1-1"
    )
    assert entry.pk != isoform.pk
    with pytest.raises(NamespaceTypeError):
        create_entity("protein", "SYN-P1", 32630, "synthetic_protein", "SYN-P1-2")
    # In the synthetic taxon only synthetic_protein defines proteins.
    with pytest.raises(IdentityNamespaceError):
        create_entity("protein", "SYN-P2", 32630, "uniprotkb", "P12345")


def test_add_identifier_attaches_non_primary_identifiers_only(registry):
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    xref = add_identifier(gene, "ncbigene", "7157", IdentifierRole.XREF)
    again = add_identifier(gene, "ncbigene", "7157", IdentifierRole.XREF)
    assert xref.pk == again.pk
    assert (xref.lookup, xref.scope_key) == ("7157", "")
    versioned = add_identifier(
        gene, "ensembl_gene", "ENSG00000141510.21", IdentifierRole.XREF
    )
    assert (versioned.lookup, versioned.version) == ("ENSG00000141510", "21")
    with pytest.raises(ValueError, match="comes only with its entity"):
        add_identifier(gene, "hgnc", "HGNC:11999", IdentifierRole.PRIMARY)
    fly_gene = EntityFactory(entity_type_id="gene", taxon_id=7227)
    with pytest.raises(TaxonPolicyError):
        add_identifier(fly_gene, "hgnc", "HGNC:11998", IdentifierRole.SECONDARY)


# ── Two transactions at once ────────────────────────────────────────────────


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_transactions_creating_one_entity_give_one_row(registry):
    """The second insert waits on the first's uncommitted row, then finds it.

    PostgreSQL only: SQLite's test database locks the table for the second
    writer instead.
    """
    holding, release = threading.Event(), threading.Event()
    created, errors = {}, []

    def first():
        try:
            with transaction.atomic():
                created["first"] = create_entity(
                    "protein", "TP53", 9606, "uniprotkb", "P04637"
                )
                holding.set()
                release.wait(30)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def second():
        try:
            holding.wait(30)
            created["second"] = create_entity(
                "protein", "p53", 9606, "uniprotkb", "P04637"
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
        release.set()
        for thread in threads:
            thread.join(30)

    assert errors == []
    assert created["first"].pk == created["second"].pk
    assert Entity.objects.filter(identity_key="uniprotkb||P04637").count() == 1
    assert EntityIdentifier.objects.filter(lookup="P04637").count() == 1


def _a_backend_waits_on_a_lock(seconds=20) -> bool:
    deadline = time.monotonic() + seconds
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.05)
    return False
