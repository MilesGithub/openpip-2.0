"""The registry JSON, its rules, and graph_sync_registry."""

import hashlib
import io
import json
import re
import shutil

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test.utils import CaptureQueriesContext

import graph.registry
from graph.entities import create_entity, identity_namespace
from graph.identity import normalize
from graph.models import EntityType, IdentifierNamespace, RegistryState, Taxon
from graph.registry import (
    REGISTRY_DIR,
    REGISTRY_VERSION,
    RegistryError,
    load_registry,
    registry_state,
)
from graph.tests.factories import EntityIdentifierFactory, IdentifierNamespaceFactory

# The JSON's content, with the version it was released as. Editing the JSON
# changes the digest: bump REGISTRY_VERSION, so that the import commands see
# databases loaded from the old JSON as out of date, and update both here.
PINNED_VERSION = "1.0"
PINNED_DIGEST = "c08bc730d60877e2779554c81c8c0828e2ae0a78623b3f4550ce58788e402125"
WRITES = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def sync(*args):
    out = io.StringIO()
    call_command("graph_sync_registry", *args, stdout=out)
    return out.getvalue()


def writes(queries):
    return [q["sql"] for q in queries.captured_queries if WRITES.match(q["sql"])]


@pytest.fixture
def switch_on(db, settings):
    settings.TYPED_GRAPH_ENABLED = True


@pytest.fixture
def edited_json(tmp_path, monkeypatch):
    """A copy of the registry JSON that a test may change."""
    shutil.copytree(REGISTRY_DIR, tmp_path / "v1")
    monkeypatch.setattr(graph.registry, "REGISTRY_DIR", tmp_path / "v1")

    def edit(filename, change):
        path = tmp_path / "v1" / filename
        entries = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(change(entries)), encoding="utf-8")

    return edit


def with_entry(key, value, **fields):
    def change(entries):
        for entry in entries:
            if entry[key] == value:
                entry.update(fields)
        return entries

    return change


# ── The JSON ────────────────────────────────────────────────────────────────


def test_the_json_is_valid():
    loaded = load_registry()
    assert {t["taxid"] for t in loaded.taxa} == {9606, 7227, 32630}
    assert {t["slug"] for t in loaded.entity_types} == {
        "gene",
        "protein",
        "protein_isoform",
    }
    assert {n["slug"] for n in loaded.namespaces} == {
        "uniprotkb",
        "hgnc",
        "ncbigene",
        "ensembl_gene",
        "synthetic_gene",
        "synthetic_protein",
    }


def test_every_namespaces_allowed_entity_types_exist():
    loaded = load_registry()
    types = {t["slug"] for t in loaded.entity_types}
    for namespace in loaded.namespaces:
        assert namespace["allowed_entity_types"]
        assert set(namespace["allowed_entity_types"]) <= types, namespace["slug"]


def test_no_namespace_identifies_both_genes_and_proteins():
    for namespace in load_registry().namespaces:
        allowed = set(namespace["allowed_entity_types"])
        assert not ({"gene"} & allowed and {"protein", "protein_isoform"} & allowed)


def test_at_most_one_namespace_defines_identity_per_type_and_taxon():
    defining = {}
    for namespace in load_registry().namespaces:
        if namespace["defines_identity"]:
            for type_slug in namespace["allowed_entity_types"]:
                key = (type_slug, namespace["fixed_taxon"])
                assert key not in defining, (namespace["slug"], defining.get(key))
                defining[key] = namespace["slug"]
    assert defining == {
        ("protein", None): "uniprotkb",
        ("protein_isoform", None): "uniprotkb",
        ("gene", 9606): "hgnc",
        ("gene", 32630): "synthetic_gene",
        ("protein", 32630): "synthetic_protein",
        ("protein_isoform", 32630): "synthetic_protein",
    }


def test_ncbigene_and_ensembl_gene_are_cross_references_only():
    namespaces = {n["slug"]: n for n in load_registry().namespaces}
    for slug in ("ncbigene", "ensembl_gene"):
        assert namespaces[slug]["defines_identity"] is False
        assert namespaces[slug]["allowed_entity_types"] == ["gene"]


def test_cross_reference_namespaces_are_never_a_global_fallback(registry):
    # ncbigene and ensembl_gene are global gene namespaces, yet no fly gene
    # can be defined by them: nothing defines gene identity for 7227.
    assert identity_namespace("gene", 7227) is None
    assert identity_namespace("gene", 9606).slug == "hgnc"
    assert identity_namespace("protein", 7227).slug == "uniprotkb"
    assert identity_namespace("protein", 32630).slug == "synthetic_protein"


def test_every_pattern_compiles_and_accepts_its_documented_examples():
    for namespace in load_registry().namespaces:
        re.compile(namespace["pattern"])
        assert namespace["examples"], namespace["slug"]
        rules = IdentifierNamespace(
            **{
                key: namespace[key]
                for key in ("slug", "scope", "case_policy", "version_policy")
            },
            pattern=namespace["pattern"],
        )
        for example in namespace["examples"]:
            normalize(rules, example)


def test_every_namespace_cites_its_authority():
    namespaces = {n["slug"]: n for n in load_registry().namespaces}
    for slug, namespace in namespaces.items():
        assert len(namespace["authority"]) > 100, slug
    # The four G1 checked against their authorities say where, and when.
    for slug in ("uniprotkb", "hgnc", "ncbigene", "ensembl_gene"):
        authority = namespaces[slug]["authority"]
        assert "https://" in authority and "2026-10-29" in authority, slug


def test_registry_version_changes_with_the_json():
    contents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(REGISTRY_DIR.glob("*.json"))
    }
    digest = hashlib.sha256(
        json.dumps(contents, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert (REGISTRY_VERSION, digest) == (PINNED_VERSION, PINNED_DIGEST), (
        "The registry JSON changed: bump REGISTRY_VERSION, then pin the new "
        "version and digest in this test."
    )


@pytest.mark.parametrize(
    "filename, change, problem",
    [
        (
            "namespaces.json",
            with_entry("slug", "hgnc", allowed_entity_types=["gene", "protein"]),
            "identifies both genes and proteins",
        ),
        (
            "namespaces.json",
            lambda entries: with_entry("slug", "ensembl_gene", defines_identity=True)(
                with_entry("slug", "ncbigene", defines_identity=True)(entries)
            ),
            "ncbigene, ensembl_gene all define identity for gene globally",
        ),
        (
            "namespaces.json",
            with_entry("slug", "synthetic_gene", fixed_taxon=9606),
            "all define identity for gene in taxon 9606",
        ),
        (
            "namespaces.json",
            with_entry("slug", "hgnc", allowed_entity_types=["chemical"]),
            "entity type 'chemical' is not listed",
        ),
        (
            "namespaces.json",
            with_entry("slug", "hgnc", examples=["HGNC:0"]),
            "rejects its example 'HGNC:0'",
        ),
        (
            "namespaces.json",
            with_entry("slug", "ncbigene", pattern="[0-9"),
            "pattern does not compile",
        ),
        (
            "namespaces.json",
            with_entry("slug", "ncbigene", case_policy="lower"),
            "case_policy 'lower' is not one of",
        ),
        (
            "taxa.json",
            with_entry("taxid", 7227, parent=1),
            "parent 1 is not listed",
        ),
    ],
)
def test_a_registry_that_breaks_a_rule_is_rejected(
    edited_json, filename, change, problem
):
    edited_json(filename, change)
    with pytest.raises(RegistryError, match=re.escape(problem)):
        load_registry()


# ── graph_sync_registry ─────────────────────────────────────────────────────


def test_the_command_refuses_while_the_switch_is_off(db, settings):
    settings.TYPED_GRAPH_ENABLED = False
    for args in ((), ("--dry-run",), ("--check",)):
        with pytest.raises(CommandError, match="TYPED_GRAPH_ENABLED"):
            sync(*args)
    assert not Taxon.objects.exists()
    assert not RegistryState.objects.exists()


def test_the_first_sync_creates_the_rows_and_a_second_changes_nothing(switch_on):
    out = sync()
    assert "create namespace uniprotkb" in out
    assert set(Taxon.objects.values_list("taxid", flat=True)) == {9606, 7227, 32630}
    assert EntityType.objects.count() == 3
    assert IdentifierNamespace.objects.count() == 6
    hgnc = IdentifierNamespace.objects.get(slug="hgnc")
    assert hgnc.fixed_taxon_id == 9606
    assert list(hgnc.allowed_entity_types.values_list("slug", flat=True)) == ["gene"]
    assert set(
        IdentifierNamespace.objects.get(
            slug="uniprotkb"
        ).allowed_entity_types.values_list("slug", flat=True)
    ) == {"protein", "protein_isoform"}
    assert registry_state().database == REGISTRY_VERSION

    with CaptureQueriesContext(connection) as queries:
        out = sync()
    assert "No changes" in out
    assert writes(queries) == []


def test_every_namespaces_allowed_entity_types_exist_in_the_database(registry):
    for namespace in IdentifierNamespace.objects.all():
        assert namespace.allowed_entity_types.exists(), namespace.slug


def test_a_dry_run_writes_nothing(switch_on):
    with CaptureQueriesContext(connection) as queries:
        out = sync("--dry-run")
    assert "create taxon 9606" in out
    assert "nothing was written" in out
    assert writes(queries) == []
    assert not Taxon.objects.exists()
    assert registry_state().database is None


def test_check_passes_after_a_sync_and_writes_nothing(switch_on):
    sync()
    with CaptureQueriesContext(connection) as queries:
        out = sync("--check")
    assert "match the JSON" in out
    assert writes(queries) == []


def test_check_fails_when_a_sync_would_write(switch_on):
    with pytest.raises(CommandError, match="differ from the JSON"):
        sync("--check")
    assert not Taxon.objects.exists()


def test_check_fails_when_a_sync_would_update_a_label(switch_on, edited_json):
    sync()
    edited_json("namespaces.json", with_entry("slug", "hgnc", label="HGNC gene ID"))
    with pytest.raises(CommandError, match="differ from the JSON"):
        sync("--check")
    assert IdentifierNamespace.objects.get(slug="hgnc").label == "HGNC ID"


def test_check_fails_when_a_change_would_be_refused(registry, edited_json):
    create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    edited_json("namespaces.json", with_entry("slug", "hgnc", case_policy="exact"))
    out = io.StringIO()
    with pytest.raises(CommandError, match="differ from the JSON"):
        call_command("graph_sync_registry", "--check", stdout=out)
    assert "refused: namespace hgnc: case_policy" in out.getvalue()


def test_check_fails_when_a_row_is_missing_from_the_json(registry):
    IdentifierNamespaceFactory(slug="retired_ns", allowed_entity_types=["gene"])
    out = io.StringIO()
    with pytest.raises(CommandError, match="differ from the JSON"):
        call_command("graph_sync_registry", "--check", stdout=out)
    assert "missing from the JSON: namespace retired_ns" in out.getvalue()


def test_an_entry_removed_from_the_json_is_reported_not_deleted(registry, edited_json):
    edited_json(
        "namespaces.json",
        lambda entries: [e for e in entries if e["slug"] != "ensembl_gene"],
    )
    out = sync()
    assert "missing from the JSON: namespace ensembl_gene" in out
    assert IdentifierNamespace.objects.filter(slug="ensembl_gene").exists()


def test_a_changed_label_is_applied(registry, edited_json):
    edited_json("namespaces.json", with_entry("slug", "hgnc", label="HGNC gene ID"))
    out = sync()
    assert "update namespace hgnc: label 'HGNC ID' -> 'HGNC gene ID'" in out
    assert IdentifierNamespace.objects.get(slug="hgnc").label == "HGNC gene ID"


def test_a_changed_case_policy_on_a_namespace_in_use_is_refused_naming_the_rows(
    registry, edited_json
):
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    primary = gene.identifiers.get()
    edited_json(
        "namespaces.json",
        with_entry("slug", "hgnc", case_policy="exact", label="HGNC gene ID"),
    )
    out = io.StringIO()
    with pytest.raises(CommandError) as refused:
        call_command("graph_sync_registry", stdout=out)
    message = str(refused.value)
    assert "refused: namespace hgnc: case_policy 'upper' -> 'exact'" in message
    assert f"1 entity identifier uses it (id {primary.pk})" in message
    # All or nothing: the label change in the same JSON is not applied either.
    hgnc = IdentifierNamespace.objects.get(slug="hgnc")
    assert (hgnc.case_policy, hgnc.label) == ("upper", "HGNC ID")


def test_a_structural_change_on_a_namespace_nothing_uses_is_applied(
    registry, edited_json
):
    edited_json("namespaces.json", with_entry("slug", "hgnc", case_policy="exact"))
    sync()
    assert IdentifierNamespace.objects.get(slug="hgnc").case_policy == "exact"


def test_a_changed_taxon_policy_on_an_entity_type_in_use_is_refused(
    registry, edited_json
):
    create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    edited_json(
        "entity_types.json", with_entry("slug", "gene", taxon_policy="optional")
    )
    with pytest.raises(CommandError, match="refused: entity type gene: taxon_policy"):
        sync()


def test_the_registry_state_is_one_row(registry):
    state = registry_state()
    assert (state.code, state.database) == (REGISTRY_VERSION, REGISTRY_VERSION)
    assert state.synced_at is not None
    with pytest.raises(IntegrityError), transaction.atomic():
        RegistryState.objects.create(id=2, version="x", synced_at=state.synced_at)


def test_registry_rows_that_data_use_cannot_be_deleted(registry):
    create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")
    EntityIdentifierFactory(
        entity=create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998"),
        namespace=IdentifierNamespace.objects.get(slug="ncbigene"),
        value="7157",
        role="xref",
    )
    # Only this entity refers to the fly; 9606 is also hgnc's fixed taxon.
    create_entity("protein", "Dmel", 7227, "uniprotkb", "Q9VXX1")
    for row in (
        IdentifierNamespace.objects.get(slug="ncbigene"),
        EntityType.objects.get(slug="protein"),
        Taxon.objects.get(taxid=7227),
    ):
        with pytest.raises(ProtectedError):
            row.delete()
