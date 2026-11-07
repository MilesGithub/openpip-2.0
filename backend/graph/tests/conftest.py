import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.management import call_command
from rest_framework.test import APIClient

from graph.demo import load_demo
from graph.models import CasePolicy, Scope, VersionPolicy
from graph.resolution import RESOLVED, resolve
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


def _tree(root) -> set:
    """Every path under root, relative to it; None when root is absent."""
    root = Path(root)
    if not root.exists():
        return None
    return {str(path.relative_to(root)) for path in root.rglob("*")}


@pytest.fixture(scope="session", autouse=True)
def default_graph_files_root():
    """The real GRAPH_FILES_ROOT (BASE_DIR/graph_files in a checkout, which the
    dev stack bind-mounts) as the session found it. The suite never writes
    there: the session fails at its end if anything under it changed, and
    test_storage asserts it while the suite runs."""
    root = Path(django_settings.BASE_DIR) / "graph_files"
    before = _tree(root)
    yield SimpleNamespace(path=root, before=before, now=lambda: _tree(root))
    assert _tree(root) == before, f"the test suite wrote under {root}"


@pytest.fixture(autouse=True)
def graph_files_root(settings, tmp_path):
    """Each test stores release files under its own temporary directory,
    never under the real GRAPH_FILES_ROOT. graph.storage reads the setting
    when it runs, so this applies to every call a test makes."""
    root = tmp_path / "graph_files"
    settings.GRAPH_FILES_ROOT = root
    return root


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
def staff_client(db):
    """A client authenticated as an active staff account. force_authenticate
    swaps in DRF's own authenticator, so it hides a view that authenticates
    nobody: test_api_base covers that with a real token."""
    client = APIClient()
    client.force_authenticate(StaffUserFactory())
    return client


@pytest.fixture
def visitor_client():
    """A client with no account. A logged-in non-staff user, who is a visitor
    too, comes from the root fixture user_auth_client (a real bearer token)."""
    return APIClient()


@pytest.fixture
def demo(registry):
    """The demonstration as graph.demo.load_demo leaves it, loaded by alice,
    an active staff account: its releases, `staff`, a client authenticated as
    alice, and `entity`, the entity a primary identifier names, through the
    resolver."""
    alice = StaffUserFactory(username="alice")
    result = load_demo(loaded_by=alice)
    staff = APIClient()
    staff.force_authenticate(alice)

    def entity(value):
        found = resolve(value)
        assert found.status == RESOLVED, (value, found)
        [candidate] = found.candidates
        return candidate.entity

    return SimpleNamespace(
        alice=alice,
        v1=result.releases["v1"],
        v2=result.releases["v2"],
        staff=staff,
        entity=entity,
    )


@pytest.fixture(autouse=True)
def fresh_throttles(request):
    """Throttle counters live in the cache, outside the test's transaction."""
    if request.node.path.name.startswith("test_api_"):
        cache.clear()


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
