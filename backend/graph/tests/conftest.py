import io
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from graph.models import CasePolicy, Scope, VersionPolicy
from graph.tests.factories import (
    IdentifierNamespaceFactory,
    StaffUserFactory,
    TaxonFactory,
)

# Test-only taxa. Out of the range NCBI assigns today, and never in the JSON.
TAXON_A = 999_999_901
TAXON_B = 999_999_902
TAXON_C = 999_999_903
TAXON_D = 999_999_904


@pytest.fixture
def switch_on(db, settings):
    """TYPED_GRAPH_ENABLED on for one test, as an operator would set it."""
    settings.TYPED_GRAPH_ENABLED = True


@pytest.fixture
def staff_user(db):
    """An active staff account, the actor the services and record rules ask
    for."""
    return StaffUserFactory()


@pytest.fixture
def registry(db, settings):
    """The registries from the JSON, loaded as an operator would."""
    settings.TYPED_GRAPH_ENABLED = True
    call_command("graph_sync_registry", stdout=io.StringIO())


@pytest.fixture
def test_namespaces(registry):
    """Test-only gene namespaces covering every case policy and version
    policy, so tests rely on no real-world rule beyond those of the four real
    namespaces G1 verified (uniprotkb, hgnc, ncbigene and ensembl_gene).

    Scopes: taxon scope here, on the test taxa; global scope here through
    global_alias, which never defines identity, and through the verified
    uniprotkb, ncbigene and ensembl_gene rows; assembly scope only through
    scope_key on an unsaved namespace (test_identity.py), since no G1 entity
    type is assembly-scoped.

    Each identity namespace is fixed to its own test taxon, so the real rule
    stays true: nothing defines gene identity for the fly (7227).
    """
    taxa = {
        taxid: TaxonFactory(taxid=taxid, scientific_name=f"Testus {name}")
        for taxid, name in (
            (TAXON_A, "primus"),
            (TAXON_B, "secundus"),
            (TAXON_C, "tertius"),
            (TAXON_D, "quartus"),
        )
    }

    def namespace(slug, taxid, **rules):
        return IdentifierNamespaceFactory(
            slug=slug,
            curie_prefix=slug.replace("_", "-"),
            scope=Scope.TAXON,
            fixed_taxon=taxa[taxid],
            allowed_entity_types=["gene"],
            **rules,
        )

    return SimpleNamespace(
        # Like FlyBase symbols: case tells h (hairy) from H (Hairless).
        exact_a=namespace("test_exact_a", TAXON_A, defines_identity=True),
        exact_b=namespace("test_exact_b", TAXON_B, defines_identity=True),
        insensitive=namespace(
            "test_insensitive",
            TAXON_C,
            defines_identity=True,
            case_policy=CasePolicy.INSENSITIVE,
            pattern="[A-Z][A-Z0-9-]*",
        ),
        versioned=namespace(
            "test_versioned",
            TAXON_D,
            defines_identity=True,
            case_policy=CasePolicy.UPPER,
            version_policy=VersionPolicy.SPLIT,
            pattern="ENSX[0-9]{4}",
        ),
        # Organism-owned symbol namespaces, which never define identity.
        symbols_a=namespace("test_symbols_a", TAXON_A),
        symbols_b=namespace("test_symbols_b", TAXON_B),
        # Global, so one value spans taxa. Never an identity namespace: that
        # would define gene identity for every taxon, the fly's included.
        global_alias=IdentifierNamespaceFactory(
            slug="test_global_alias",
            curie_prefix="test-global-alias",
            scope=Scope.GLOBAL,
            fixed_taxon=None,
            allowed_entity_types=["gene"],
        ),
        taxon_a=TAXON_A,
        taxon_b=TAXON_B,
        taxon_c=TAXON_C,
        taxon_d=TAXON_D,
    )
