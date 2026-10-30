"""The registry JSON, its rules, and graph_sync_registry."""

import dataclasses
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
from graph.claims import Claim, ClaimError, ContextValue, Endpoint, record_claims
from graph.entities import create_entity, identity_namespace
from graph.identity import normalize
from graph.models import (
    Entity,
    EntityType,
    EvidenceAssertion,
    EvidenceContext,
    IdentifierNamespace,
    ImmutableRecordError,
    RegistryState,
    Relationship,
    RelationshipType,
    ScoreType,
    Taxon,
)
from graph.registry import (
    REGISTRY_DIR,
    REGISTRY_VERSION,
    RegistryError,
    load_registry,
    registry_state,
    validate,
)
from graph.tests.factories import (
    EntityIdentifierFactory,
    EvidenceScoreFactory,
    IdentifierNamespaceFactory,
    RelationshipFactory,
    entity_of,
    evidence_release,
)
from graph.tests.test_provenance import _race

# The JSON's content, with the version it was released as. Editing the JSON
# changes the digest: bump REGISTRY_VERSION, so that the import commands see
# databases loaded from the old JSON as out of date, and update both here.
PINNED_VERSION = "1.1"
PINNED_DIGEST = "266561b7f87ea63c63eb3fa273c3af47fe6ad90ee6c89ab8684ec28c00b77300"
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
    assert {t["slug"] for t in loaded.relationship_types} == {
        "transcriptional_regulation",
        "physical_interaction",
        "protein_interaction_unspecified",
        "encodes",
        "isoform_of",
    }
    assert {t["slug"] for t in loaded.score_types} == {
        "synthetic:confidence",
        "synthetic:rank",
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
    assert RelationshipType.objects.count() == 5
    regulation = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert set(regulation.allowed_subject_types.values_list("slug", flat=True)) == {
        "gene",
        "protein",
    }
    assert list(regulation.allowed_object_types.values_list("slug", flat=True)) == [
        "gene"
    ]
    assert set(ScoreType.objects.values_list("slug", flat=True)) == {
        "synthetic:confidence",
        "synthetic:rank",
    }
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


# ── Relationship types and score types ──────────────────────────────────────


def test_the_relationship_types_are_the_five_the_plan_names():
    types = {t["slug"]: t for t in load_registry().relationship_types}
    shape = {
        slug: (
            t["family"],
            t["orientation"],
            t["polarity"],
            sorted(t["allowed_subject_types"]),
            sorted(t["allowed_object_types"]),
            t["self_pairs_allowed"],
            t["psi_mi_anchors"],
            t["is_reference"],
        )
        for slug, t in types.items()
    }
    proteins = ["protein", "protein_isoform"]
    assert shape == {
        "transcriptional_regulation": (
            "regulation",
            "directed",
            "effect",
            ["gene", "protein"],
            ["gene"],
            True,
            ["MI:2247"],
            False,
        ),
        "physical_interaction": (
            "physical",
            "symmetric",
            "none",
            proteins,
            proteins,
            True,
            ["MI:0914"],
            False,
        ),
        "protein_interaction_unspecified": (
            "physical",
            "symmetric",
            "none",
            proteins,
            proteins,
            True,
            [],
            False,
        ),
        "encodes": (
            "reference",
            "role_ordered",
            "none",
            ["gene"],
            ["protein"],
            False,
            [],
            True,
        ),
        "isoform_of": (
            "reference",
            "role_ordered",
            "none",
            ["protein_isoform"],
            ["protein"],
            False,
            [],
            True,
        ),
    }
    roles = {
        slug: (t["subject_role_label"], t["object_role_label"])
        for slug, t in types.items()
    }
    assert roles["transcriptional_regulation"] == ("regulator", "target")
    assert roles["encodes"] == ("gene", "protein entry")
    assert roles["isoform_of"] == ("isoform", "protein entry")
    assert all(t["identity_qualifiers"] == [] for t in types.values())
    # Physical types say that association-only evidence is never binding.
    assert "never presented as binding" in types["physical_interaction"]["definition"]


def _with_type(slug, /, **changes):
    loaded = load_registry()
    entries = [
        {**entry, **changes} if entry["slug"] == slug else entry
        for entry in loaded.relationship_types
    ]
    return validate(dataclasses.replace(loaded, relationship_types=tuple(entries)))


@pytest.mark.parametrize(
    "slug,changes,problem",
    [
        ("encodes", {"family": "kinship"}, "family 'kinship' is not one of"),
        ("encodes", {"orientation": "both"}, "orientation 'both' is not one of"),
        ("encodes", {"polarity": "sign"}, "polarity 'sign' is not one of"),
        ("encodes", {"label": ""}, "needs a label and a definition"),
        ("encodes", {"definition": " "}, "needs a label and a definition"),
        ("encodes", {"subject_role_label": ""}, "needs a subject_role_label"),
        (
            "encodes",
            {"allowed_subject_types": []},
            "allowed_subject_types is a non-empty list",
        ),
        (
            "encodes",
            {"allowed_object_types": ["chemical"]},
            "entity type 'chemical' is not listed",
        ),
        (
            "physical_interaction",
            {"allowed_object_types": ["protein"]},
            "is symmetric, so its subject and object types are the same",
        ),
        (
            "physical_interaction",
            {"polarity": "effect"},
            "effect polarity belongs to directed types only",
        ),
        (
            "encodes",
            {"orientation": "directed"},
            "a reference type is role_ordered, polarity none",
        ),
        (
            "isoform_of",
            {"is_reference": False},
            "is_reference is true for the reference family, and only for it",
        ),
        (
            "physical_interaction",
            {"is_reference": True},
            "is_reference is true for the reference family, and only for it",
        ),
        ("encodes", {"self_pairs_allowed": "no"}, "self_pairs_allowed is true or"),
        (
            "transcriptional_regulation",
            {"identity_qualifiers": ["site"]},
            "identity_qualifiers is [] until",
        ),
        (
            "physical_interaction",
            {"psi_mi_anchors": ["MI:914"]},
            "psi_mi_anchors is a list of PSI-MI CURIEs",
        ),
        (
            "physical_interaction",
            {"psi_mi_anchors": "MI:0914"},
            "psi_mi_anchors is a list of PSI-MI CURIEs",
        ),
        ("encodes", {"slug": "Encodes"}, "a slug is lower case"),
    ],
)
def test_a_relationship_type_that_breaks_a_rule_is_rejected(slug, changes, problem):
    problems = _with_type(slug, **changes)
    assert any(problem in line for line in problems), problems


def test_a_relationship_type_entry_has_exactly_its_keys():
    problems = _with_type("encodes", extra="x")
    assert any("unknown keys ['extra']" in line for line in problems), problems


def _regulation_in_use():
    """A stored relationship of transcriptional_regulation."""
    return RelationshipFactory(
        relationship_type=RelationshipType.objects.get(
            slug="transcriptional_regulation"
        )
    )


def test_a_relationship_types_descriptive_fields_change_while_it_is_used(
    registry, edited_json
):
    _regulation_in_use()
    edited_json(
        "relationship_types.json",
        with_entry(
            "slug",
            "transcriptional_regulation",
            label="Regulation of transcription",
            subject_role_label="transcription factor",
            psi_mi_anchors=[],
        ),
    )
    out = sync()
    assert "update relationship type transcriptional_regulation: label" in out
    stored = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert (stored.label, stored.subject_role_label, stored.psi_mi_anchors) == (
        "Regulation of transcription",
        "transcription factor",
        [],
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"orientation": "role_ordered", "polarity": "none"},
        {"polarity": "none"},
        {"family": "rna"},
        {"self_pairs_allowed": False},
        {"allowed_subject_types": ["gene"]},
        {"allowed_object_types": ["gene", "protein"]},
    ],
    ids=lambda changes: "+".join(changes),
)
def test_a_relationship_types_rules_are_fixed_while_a_relationship_uses_it(
    registry, edited_json, changes
):
    used = _regulation_in_use()
    edited_json(
        "relationship_types.json",
        with_entry("slug", "transcriptional_regulation", label="Renamed", **changes),
    )
    out = io.StringIO()
    with pytest.raises(CommandError) as refused:
        call_command("graph_sync_registry", stdout=out)
    message = str(refused.value)
    for field in changes:
        assert f"refused: relationship type transcriptional_regulation: {field}" in (
            message
        )
    assert f"1 relationship uses it (id {used.pk})" in message
    # All or nothing: the label is not changed either.
    stored = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert stored.label == "Transcriptional regulation"
    assert set(stored.allowed_subject_types.values_list("slug", flat=True)) == {
        "gene",
        "protein",
    }


def test_a_relationship_types_rules_change_while_nothing_uses_it(registry, edited_json):
    edited_json(
        "relationship_types.json",
        with_entry(
            "slug",
            "transcriptional_regulation",
            allowed_subject_types=["gene"],
            self_pairs_allowed=False,
        ),
    )
    sync()
    stored = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert list(stored.allowed_subject_types.values_list("slug", flat=True)) == ["gene"]
    assert stored.self_pairs_allowed is False


def test_a_score_types_range_is_fixed_while_a_score_uses_it(registry, edited_json):
    score = EvidenceScoreFactory(
        score_type=ScoreType.objects.get(slug="synthetic:confidence"),
        assertion__release__is_synthetic=True,
        assertion__relationship__subject=entity_of("gene", 32630),
        assertion__relationship__object=entity_of("gene", 32630),
    )
    edited_json(
        "score_types.json",
        with_entry("slug", "synthetic:confidence", range_max=0.5, label="Renamed"),
    )
    with pytest.raises(CommandError) as refused:
        sync()
    assert "refused: score type synthetic:confidence: range_max" in str(refused.value)
    assert f"1 evidence score uses it (id {score.pk})" in str(refused.value)
    edited_json(
        "score_types.json",
        with_entry("slug", "synthetic:confidence", range_max=1, label="Renamed"),
    )
    sync()
    assert ScoreType.objects.get(slug="synthetic:confidence").label == "Renamed"


def test_check_covers_relationship_and_score_types(registry, edited_json):
    sync("--check")
    RelationshipType.objects.create(
        slug="retired_type",
        label="x",
        definition="x",
        family="association",
        orientation="symmetric",
        polarity="none",
        subject_role_label="a",
        object_role_label="b",
        self_pairs_allowed=False,
    )
    out = io.StringIO()
    with pytest.raises(CommandError, match="differ from the JSON"):
        call_command("graph_sync_registry", "--check", stdout=out)
    assert "missing from the JSON: relationship type retired_type" in out.getvalue()
    RelationshipType.objects.filter(slug="retired_type").delete()
    edited_json(
        "score_types.json", with_entry("slug", "synthetic:rank", unit="positions")
    )
    out = io.StringIO()
    with pytest.raises(CommandError, match="differ from the JSON"):
        call_command("graph_sync_registry", "--check", stdout=out)
    assert "update score type synthetic:rank: unit '' -> 'positions'" in out.getvalue()


def test_relationship_and_score_types_that_data_use_cannot_be_deleted(registry):
    used = _regulation_in_use()
    with pytest.raises(ProtectedError):
        RelationshipType.objects.get(slug="transcriptional_regulation").delete()
    score = EvidenceScoreFactory()
    with pytest.raises(ProtectedError):
        score.score_type.delete()
    assert Relationship.objects.filter(pk=used.pk).exists()


@pytest.mark.postgres
def test_the_sync_locks_a_row_before_counting_its_users(registry, edited_json):
    edited_json(
        "relationship_types.json",
        with_entry("slug", "isoform_of", self_pairs_allowed=True),
    )
    with CaptureQueriesContext(connection) as queries:
        sync()
    sql = [query["sql"] for query in queries.captured_queries]
    # FOR NO KEY UPDATE: the lock the sync's own updates take.
    locked = [
        n
        for n, text in enumerate(sql)
        if "FOR NO KEY UPDATE" in text and 'FROM "graph_relationshiptype"' in text
    ]
    counted = [n for n, text in enumerate(sql) if 'FROM "graph_relationship"' in text]
    updated = [
        n for n, text in enumerate(sql) if 'UPDATE "graph_relationshiptype"' in text
    ]
    assert locked and counted and updated
    assert locked[0] < counted[-1] < updated[0]


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_sync_that_locks_second_sees_the_relationship_written_first(
    registry, edited_json
):
    """The relationship's guard holds its type in shared mode until it
    commits; the sync waits for it, then counts it and refuses."""
    regulation = RelationshipType.objects.get(slug="transcriptional_regulation")
    regulator, target = entity_of("gene"), entity_of("gene")
    edited_json(
        "relationship_types.json",
        with_entry("slug", "transcriptional_regulation", self_pairs_allowed=False),
    )
    outcome = {}

    def write_relationship():
        outcome["relationship"] = RelationshipFactory(
            relationship_type=regulation, subject=regulator, object=target
        )

    def run_sync():
        sync()

    errors = _race(write_relationship, run_sync)
    assert len(errors) == 1 and isinstance(errors[0], CommandError), errors
    assert "self_pairs_allowed True -> False" in str(errors[0])
    assert "1 relationship uses it" in str(errors[0])
    stored = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert stored.self_pairs_allowed is True
    assert Relationship.objects.filter(pk=outcome["relationship"].pk).exists()


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_relationship_that_locks_second_sees_the_sync_written_first(
    registry, edited_json
):
    """The sync holds the type it changes until it commits; the
    relationship's guard waits for it, then reads the new rule."""
    regulation = RelationshipType.objects.get(slug="transcriptional_regulation")
    regulator, target = entity_of("protein"), entity_of("gene")
    edited_json(
        "relationship_types.json",
        with_entry(
            "slug", "transcriptional_regulation", allowed_subject_types=["gene"]
        ),
    )
    refused = {}

    def run_sync():
        sync()

    def write_relationship():
        try:
            RelationshipFactory(
                relationship_type=regulation, subject=regulator, object=target
            )
        except ImmutableRecordError as exc:
            refused["message"] = str(exc)

    assert _race(run_sync, write_relationship) == []
    assert "subject is a gene; entity" in refused["message"]
    assert not Relationship.objects.exists()
    stored = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert list(stored.allowed_subject_types.values_list("slug", flat=True)) == ["gene"]


def _regulation_claim(regulator, target):
    return Claim(
        relationship_type="transcriptional_regulation",
        subject=Endpoint(regulator, "SYN-REG", "primary"),
        object=Endpoint(target, "SYN-TARGET", "primary"),
        outcome="supported",
        evidence_basis="experimental",
        assertion_method="manual_assertion",
        effect="activating",
    )


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_sync_that_locks_second_sees_the_claim_recorded_first(registry, edited_json):
    """record_claims holds the type in shared mode until it commits; the
    sync waits for it, then counts the relationship and refuses."""
    bundle = evidence_release(records=1)
    claim = _regulation_claim(entity_of("gene"), entity_of("gene"))
    edited_json(
        "relationship_types.json",
        with_entry("slug", "transcriptional_regulation", self_pairs_allowed=False),
    )
    recorded = {}

    def record():
        recorded["result"] = record_claims(
            bundle.records[0], [claim], import_run=bundle.run
        )

    errors = _race(record, sync)
    assert len(errors) == 1 and isinstance(errors[0], CommandError), errors
    assert "self_pairs_allowed True -> False" in str(errors[0])
    assert "1 relationship uses it" in str(errors[0])
    stored = RelationshipType.objects.get(slug="transcriptional_regulation")
    assert stored.self_pairs_allowed is True
    assert EvidenceAssertion.objects.get().pk == (
        recorded["result"].assertions[0].assertion.pk
    )


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_claim_that_locks_second_sees_the_sync_written_first(registry, edited_json):
    """The sync holds the type it changes until it commits; record_claims
    waits for it, then checks the claim against the new rule."""
    bundle = evidence_release(records=1)
    claim = _regulation_claim(entity_of("protein"), entity_of("gene"))
    edited_json(
        "relationship_types.json",
        with_entry(
            "slug", "transcriptional_regulation", allowed_subject_types=["gene"]
        ),
    )
    refused = {}

    def record():
        try:
            record_claims(bundle.records[0], [claim], import_run=bundle.run)
        except ClaimError as exc:
            refused["message"] = str(exc)

    assert _race(sync, record) == []
    assert "Rule 2 (endpoint types)" in refused["message"]
    assert "subject is a gene; entity" in refused["message"]
    assert not Relationship.objects.exists()
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("writer", ["create_entity", "record_claims"])
def test_a_writer_referencing_a_taxon_the_sync_changes_never_deadlocks(
    registry, edited_json, writer
):
    """The writer holds its entity type or relationship type in shared mode,
    and its row's key to taxon 9606 is checked only when it commits; the
    sync, changing both, locks the taxon first and waits on the type. The
    sync's lock leaves key checks alone, so the writer commits, then the sync
    applies its changes."""
    edited_json("taxa.json", with_entry("taxid", 9606, common_name="human (test)"))
    if writer == "create_entity":
        edited_json(
            "entity_types.json", with_entry("slug", "gene", label="Gene (test)")
        )

        def write():
            create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")

    else:
        edited_json(
            "relationship_types.json",
            with_entry("slug", "transcriptional_regulation", label="Test label"),
        )
        bundle = evidence_release(records=1)
        claim = dataclasses.replace(
            _regulation_claim(entity_of("gene"), entity_of("gene")),
            contexts=(ContextValue("host_taxon", "9606", taxon=9606),),
        )

        def write():
            record_claims(bundle.records[0], [claim], import_run=bundle.run)

    assert _race(write, sync) == []
    assert Taxon.objects.get(taxid=9606).common_name == "human (test)"
    if writer == "create_entity":
        assert Entity.objects.filter(identity_key="hgnc|t:9606|HGNC:11998").exists()
        assert EntityType.objects.get(slug="gene").label == "Gene (test)"
    else:
        assert EvidenceContext.objects.get().taxon_id == 9606
        assert (
            RelationshipType.objects.get(slug="transcriptional_regulation").label
            == "Test label"
        )
