"""GET /api/graph/v1/vocabularies (spec §7.2): each list against the table it
reads, the closed sets against their choices, and the rules for blanks,
order and numbers, on the registries graph_sync_registry loads.
"""

import math

import pytest

from graph.api.limits import API_VERSION
from graph.models import (
    LEVEL_STRENGTH,
    AssertionMethod,
    ContextFacet,
    EntityType,
    EvidenceBasis,
    IdentifierNamespace,
    IdentifierRole,
    InteractionLevel,
    Outcome,
    RelationshipType,
    ScoreType,
    Taxon,
)
from graph.registry import REGISTRY_VERSION
from graph.summary import EFFECT_KEYS, MIXED, NOT_APPLICABLE, SIGN_KEYS
from graph.tests.api_helpers import VOCABULARIES
from graph.tests.factories import TaxonFactory

TAXON_KEYS = ("scientific_name", "common_name", "rank", "parent", "merged_into")
ENTITY_TYPE_KEYS = (
    "label",
    "definition",
    "taxon_policy",
    "identity_scheme",
    "assembly_required",
    "display_token",
)
NAMESPACE_KEYS = (
    "label",
    "curie_prefix",
    "scope",
    "fixed_taxon",
    "case_policy",
    "version_policy",
    "defines_identity",
    "pattern",
    "url_template",
    "authority",
)
RELATIONSHIP_TYPE_KEYS = (
    "label",
    "definition",
    "family",
    "orientation",
    "polarity",
    "subject_role_label",
    "object_role_label",
    "self_pairs_allowed",
    "psi_mi_anchors",
    "display_token",
    "is_reference",
)
SCORE_TYPE_KEYS = (
    "label",
    "definition",
    "value_kind",
    "direction",
    "range_min",
    "range_max",
    "unit",
    "null_model",
    "defining_document",
    "comparable_across_datasets",
)
# The model strings a blank in the table shows as null.
NULL_WHEN_BLANK = {"unit", "null_model", "common_name", "rank", "display_token"}


def table(model, key, fields) -> list[dict]:
    """The rows as the route must show them, sorted by `key`."""
    return [
        {
            name: None if name in NULL_WHEN_BLANK and value == "" else value
            for name, value in zip((key, *fields), row)
        }
        for row in model.objects.order_by(key).values_list(key, *fields)
    ]


def closed(values) -> list[dict]:
    return [{"value": value, "label": value.replace("_", " ")} for value in values]


def numbers(value):
    """Every float in a parsed body."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            yield from numbers(item)
    elif isinstance(value, float):
        yield value


@pytest.fixture
def body(registry, visitor_client):
    response = visitor_client.get(VOCABULARIES)
    assert response.status_code == 200
    return response.json()


def test_it_is_404_while_off(db, settings, visitor_client):
    settings.TYPED_GRAPH_ENABLED = False
    response = visitor_client.get(VOCABULARIES)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}


def test_the_keys_are_exactly_these(body):
    assert list(body) == [
        "api_version",
        "registry_version",
        "taxa",
        "entity_types",
        "namespaces",
        "relationship_types",
        "score_types",
        "outcomes",
        "effects",
        "genetic_signs",
        "evidence_bases",
        "assertion_methods",
        "context_facets",
        "interaction_levels",
        "identifier_roles",
    ]
    assert body["api_version"] == API_VERSION
    assert body["registry_version"] == {
        "code": REGISTRY_VERSION,
        "database": REGISTRY_VERSION,
    }


def test_taxa_and_entity_types_are_their_tables(body):
    assert body["taxa"] == table(Taxon, "taxid", TAXON_KEYS)
    assert [taxon["taxid"] for taxon in body["taxa"]] == [7227, 9606, 32630]
    assert body["entity_types"] == table(EntityType, "slug", ENTITY_TYPE_KEYS)


def test_namespaces_are_their_table(body):
    allowed = [entry.pop("allowed_entity_types") for entry in body["namespaces"]]
    assert body["namespaces"] == table(IdentifierNamespace, "slug", NAMESPACE_KEYS)
    assert allowed == [
        sorted(row.allowed_entity_types.values_list("slug", flat=True))
        for row in IdentifierNamespace.objects.order_by("slug")
    ]


def test_relationship_types_are_their_table(body):
    entries = body["relationship_types"]
    subjects = [entry.pop("allowed_subject_types") for entry in entries]
    objects = [entry.pop("allowed_object_types") for entry in entries]
    assert entries == table(RelationshipType, "slug", RELATIONSHIP_TYPE_KEYS)
    rows = RelationshipType.objects.order_by("slug")
    assert subjects == [
        sorted(row.allowed_subject_types.values_list("slug", flat=True)) for row in rows
    ]
    assert objects == [
        sorted(row.allowed_object_types.values_list("slug", flat=True)) for row in rows
    ]


def test_score_types_are_their_table(body):
    assert body["score_types"] == table(ScoreType, "slug", SCORE_TYPE_KEYS)


def test_a_blank_string_is_null_and_a_url_template_stays_a_string(body):
    encodes = {entry["slug"]: entry for entry in body["relationship_types"]}["encodes"]
    assert encodes["display_token"] is None
    rank = {entry["slug"]: entry for entry in body["score_types"]}["synthetic:rank"]
    assert rank["range_max"] is None
    assert rank["unit"] is None
    assert rank["null_model"] is None
    synthetic = {entry["taxid"]: entry for entry in body["taxa"]}[32630]
    assert synthetic["common_name"] is None
    namespaces = {entry["slug"]: entry for entry in body["namespaces"]}
    assert namespaces["synthetic_gene"]["url_template"] == ""
    assert namespaces["uniprotkb"]["url_template"].startswith("https://")


def test_a_blank_rank_is_null(registry, visitor_client):
    # Every registry taxon has a rank, so the blank needs a row of its own.
    taxon = TaxonFactory(rank="")
    taxa = visitor_client.get(VOCABULARIES).json()["taxa"]
    assert {entry["taxid"]: entry["rank"] for entry in taxa}[taxon.taxid] is None


def test_the_closed_sets_are_their_choices_in_their_order(body):
    assert body["outcomes"] == closed(Outcome.values)
    assert body["evidence_bases"] == closed(EvidenceBasis.values)
    assert body["assertion_methods"] == closed(AssertionMethod.values)
    assert body["context_facets"] == closed(ContextFacet.values)
    assert body["identifier_roles"] == closed(IdentifierRole.values)
    assert body["outcomes"][1] == {"value": "tested_absent", "label": "tested absent"}


def test_effects_and_signs_add_what_only_a_summary_emits(body):
    assert [entry["value"] for entry in body["effects"]] == [
        *EFFECT_KEYS,
        MIXED,
        NOT_APPLICABLE,
    ]
    assert [entry["value"] for entry in body["genetic_signs"]] == [
        *SIGN_KEYS,
        MIXED,
        NOT_APPLICABLE,
    ]
    stored = {
        key: [entry["value"] for entry in body[key] if entry["stored"]]
        for key in ("effects", "genetic_signs")
    }
    assert stored == {
        "effects": ["activating", "inhibiting", "unknown"],
        "genetic_signs": ["negative", "positive"],
    }
    assert body["effects"][-1] == {
        "value": "not_applicable",
        "label": "not applicable",
        "stored": False,
    }


def test_interaction_levels_run_strongest_first(body):
    assert body["interaction_levels"] == [
        {"value": level, "label": label, "strength": strength}
        for level, label, strength in (
            ("direct_interaction", "direct interaction", 3),
            ("physical_association", "physical association", 2),
            ("association", "association", 1),
            ("not_stated", "not stated", 0),
        )
    ]
    assert {
        entry["value"]: entry["strength"] for entry in body["interaction_levels"]
    } == {level.value: LEVEL_STRENGTH[level] for level in InteractionLevel}


def test_no_number_is_non_finite(body):
    found = list(numbers(body))
    assert found
    assert all(math.isfinite(number) for number in found)


def test_each_entry_has_exactly_its_keys(body):
    keys = {
        "taxa": {"taxid", *TAXON_KEYS},
        "entity_types": {"slug", *ENTITY_TYPE_KEYS},
        "namespaces": {"slug", "allowed_entity_types", *NAMESPACE_KEYS},
        "relationship_types": {
            "slug",
            "allowed_subject_types",
            "allowed_object_types",
            *RELATIONSHIP_TYPE_KEYS,
        },
        "score_types": {"slug", *SCORE_TYPE_KEYS},
        "outcomes": {"value", "label"},
        "effects": {"value", "label", "stored"},
        "genetic_signs": {"value", "label", "stored"},
        "evidence_bases": {"value", "label"},
        "assertion_methods": {"value", "label"},
        "context_facets": {"value", "label"},
        "interaction_levels": {"value", "label", "strength"},
        "identifier_roles": {"value", "label"},
    }
    for key, expected in keys.items():
        assert body[key], key
        assert [entry for entry in body[key] if set(entry) != expected] == [], key


def test_it_costs_the_same_few_queries_for_anyone(
    registry, visitor_client, user_auth_client, django_assert_num_queries
):
    # The five tables, the three many-to-many lists and RegistryState.
    with django_assert_num_queries(9):
        assert visitor_client.get(VOCABULARIES).status_code == 200
    # And the token's user.
    with django_assert_num_queries(10):
        assert user_auth_client.get(VOCABULARIES).status_code == 200
