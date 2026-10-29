"""The legacy PPI responses do not change when typed data exists.

The typed graph lives in its own tables, and nothing in the legacy code reads
them. These tests hold that from the outside: they record the legacy
responses, create typed entities and identifiers that use the same strings
(TP53, P04637, 7157 ...), and expect every response back byte for byte.
"""

import io

import pytest
from django.apps import apps
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient

from core.models import UserInteractionNetwork
from datasets.tests.factories import DatasetFactory
from graph.entities import create_entity
from graph.models import IdentifierNamespace, IdentifierRole
from graph.tests.factories import EntityIdentifierFactory
from interactions.models import InteractionInteractionNetworks, InteractionNetwork
from interactions.tests.factories import (
    InteractionCategoryFactory,
    InteractionDatasetFactory,
    InteractionFactory,
    InteractionInteractionCategoryFactory,
)
from proteins.models import ProteinOrganism
from proteins.tests.factories import (
    IdentifierFactory,
    OrganismFactory,
    ProteinFactory,
    ProteinIdentifierFactory,
)

# The apps whose rows these tests create. Every other table is legacy here.
TYPED_APPS = {"graph"}

SEARCH_KEYS = {
    "all_proteins",
    "all_interactions",
    "domains",
    "complexes",
    "query_protein_id_array",
    "search_term",
    "found_protein_summary",
    "unfound_protein_summary",
}
PROTEIN_KEYS = {
    "protein_id",
    "protein_uniprot_id",
    "protein_ensembl_id",
    "protein_entrez_id",
    "protein_gene_name",
    "protein_protein_name",
    "protein_description",
    "protein_sequence",
    "number_of_interactions_in_database",
    "annotation_array",
    "tissue_expression_array",
    "subcellular_location_expression_array",
    "tissue_specificity_array",
}
INTERACTION_KEYS = {
    "interaction_id",
    "interactor_A",
    "interactor_B",
    "score",
    "annotation_array",
    "experiment_array",
    "dataset_array",
    "interaction_category_array",
}
INTERACTOR_KEYS = {
    "protein_id",
    "protein_uniprot_id",
    "protein_gene_name",
    "protein_ensembl_id",
}


def protein(gene_name, uniprot_id, ensembl_id, entrez_id, organism):
    made = ProteinFactory(
        gene_name=gene_name,
        uniprot_id=uniprot_id,
        ensembl_id=ensembl_id,
        entrez_id=entrez_id,
        number_of_interactions_in_database=2,
    )
    for value, convention in ((gene_name, "gene_name"), (uniprot_id, "uniprotkb")):
        ProteinIdentifierFactory(
            protein=made,
            identifier=IdentifierFactory(
                identifier=value, naming_convention=convention
            ),
        )
    ProteinOrganism.objects.create(protein=made, organism=organism)
    return made


@pytest.fixture
def legacy(db, regular_user):
    """Two proteins, two interactions (one negative), a dataset, a category,
    and a user with one saved network."""
    human = OrganismFactory()
    tp53 = protein("TP53", "P04637", "ENSG00000141510", "7157", human)
    mdm2 = protein("MDM2", "Q00987", "ENSG00000135679", "4193", human)
    dataset = DatasetFactory(name="Compat", pubmed_id="12345678")
    category = InteractionCategoryFactory(category_name="Published", order="1")
    interactions = [
        InteractionFactory(interactor_A=tp53, interactor_B=mdm2, score="0.91"),
        InteractionFactory(
            interactor_A=mdm2, interactor_B=tp53, score="0.10", negative=True
        ),
    ]
    for interaction in interactions:
        InteractionDatasetFactory(interaction=interaction, dataset=dataset)
        InteractionInteractionCategoryFactory(
            interaction=interaction, interaction_category=category
        )
    network = InteractionNetwork.objects.create(
        name="Compat network",
        query="TP53",
        interactor_query_string="TP53",
        score_parameter="0.0",
        category_array="Published",
    )
    for interaction in interactions:
        InteractionInteractionNetworks.objects.create(
            interaction_network=network, interaction=interaction
        )
    UserInteractionNetwork.objects.create(
        user=regular_user, interaction_network=network
    )
    return network


def legacy_responses(user, network) -> dict[str, tuple[int, bytes]]:
    # The throttles count through the cache; start each pass from zero so
    # that both passes see the same limits.
    cache.clear()
    anonymous, signed_in = APIClient(), APIClient()
    signed_in.force_authenticate(user=user)
    requests = {
        "search": lambda: anonymous.get("/api/search?q=TP53"),
        "search interactors": lambda: anonymous.post(
            "/api/search/interactors",
            {"searchTerm": "TP53", "filterParameter": "None"},
            format="json",
        ),
        "counts": lambda: anonymous.get("/api/counts"),
        "proteins": lambda: anonymous.get("/api/proteins"),
        "autocomplete": lambda: anonymous.get("/api/proteins/autocomplete?q=TP"),
        "protein": lambda: anonymous.get("/api/proteins/TP53"),
        "datasets": lambda: anonymous.get("/api/datasets"),
        "networks": lambda: signed_in.get("/api/networks"),
        "network": lambda: signed_in.get(f"/api/networks/{network.pk}"),
        "psicquic tab28": lambda: anonymous.get(
            "/psicquic/rest/query?q=*&format=tab28"
        ),
        "psicquic count": lambda: anonymous.get("/psicquic/rest/query/count?q=*"),
    }
    responses = {}
    for name, request in requests.items():
        response = request()
        responses[name] = (response.status_code, response.content)
    return responses


def home_network_keys() -> set[str]:
    # The endpoint samples with ORDER BY RANDOM(), so only its shape is fixed.
    response = APIClient().get("/api/home/network")
    assert response.status_code == 200
    return set(response.json())


def legacy_row_counts() -> dict[str, int]:
    counts = {}
    with connection.cursor() as cursor:
        for model in apps.get_models(include_auto_created=True):
            if model._meta.app_label in TYPED_APPS or model._meta.proxy:
                continue
            table = model._meta.db_table
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}")
            counts[table] = cursor.fetchone()[0]
    return counts


def add_typed_rows(settings):
    """Typed entities and identifiers that use the legacy rows' strings."""
    settings.TYPED_GRAPH_ENABLED = True
    call_command("graph_sync_registry", stdout=io.StringIO())
    ncbigene = IdentifierNamespace.objects.get(slug="ncbigene")
    ensembl = IdentifierNamespace.objects.get(slug="ensembl_gene")
    for symbol, hgnc, uniprot, entrez, ensembl_id in (
        ("TP53", "HGNC:11998", "P04637", "7157", "ENSG00000141510"),
        ("MDM2", "HGNC:6973", "Q00987", "4193", "ENSG00000135679"),
    ):
        gene = create_entity("gene", symbol, 9606, "hgnc", hgnc)
        create_entity("protein", symbol, 9606, "uniprotkb", uniprot)
        create_entity("protein_isoform", symbol, 9606, "uniprotkb", f"{uniprot}-2")
        EntityIdentifierFactory(
            entity=gene, namespace=ncbigene, value=entrez, role=IdentifierRole.XREF
        )
        EntityIdentifierFactory(
            entity=gene, namespace=ensembl, value=ensembl_id, role=IdentifierRole.XREF
        )


def test_legacy_responses_are_byte_identical_with_typed_rows_present(
    legacy, regular_user, settings
):
    before = legacy_responses(regular_user, legacy)
    home_keys = home_network_keys()
    counts = legacy_row_counts()
    assert all(status == 200 for status, _ in before.values()), {
        name: status for name, (status, _) in before.items()
    }
    assert b"uniprotkb:P04637" in before["psicquic tab28"][1]
    assert before["psicquic count"][1] == b"2"

    add_typed_rows(settings)

    assert legacy_row_counts() == counts
    after = legacy_responses(regular_user, legacy)
    for name in before:
        assert after[name] == before[name], f"{name} changed"
    assert home_network_keys() == home_keys
    assert legacy_row_counts() == counts


def test_search_keeps_its_shape(db):
    """/api/search: eight top-level keys, fixed object shapes, '' for missing
    text, and the query protein as interactor_A."""
    # Only names: every other text field is null, and must come back as "".
    tp53 = ProteinFactory(
        gene_name="TP53",
        uniprot_id="P04637",
        protein_name=None,
        ensembl_id=None,
        entrez_id=None,
        sequence=None,
        description=None,
    )
    ProteinIdentifierFactory(
        protein=tp53,
        identifier=IdentifierFactory(identifier="TP53", naming_convention="gene_name"),
    )
    mdm2 = ProteinFactory(gene_name="MDM2", uniprot_id="Q00987")
    # The query protein is interactor B in the row.
    InteractionFactory(interactor_A=mdm2, interactor_B=tp53, score="0.91")

    data = APIClient().get("/api/search?q=TP53").json()
    assert set(data) == SEARCH_KEYS
    assert (data["domains"], data["complexes"]) == ("", "")
    assert data["found_protein_summary"] == "TP53"
    assert data["unfound_protein_summary"] == ""
    assert data["query_protein_id_array"] == [tp53.pk]
    for found in data["all_proteins"]:
        assert set(found) == PROTEIN_KEYS
    query = next(p for p in data["all_proteins"] if p["protein_id"] == tp53.pk)
    for field in (
        "protein_protein_name",
        "protein_ensembl_id",
        "protein_entrez_id",
        "protein_sequence",
        "protein_description",
    ):
        assert query[field] == "", field

    [interaction] = data["all_interactions"]
    assert set(interaction) == INTERACTION_KEYS
    assert set(interaction["interactor_A"]) == INTERACTOR_KEYS
    assert set(interaction["interactor_B"]) == INTERACTOR_KEYS
    assert interaction["interactor_A"]["protein_gene_name"] == "TP53"
    assert interaction["interactor_A"]["protein_ensembl_id"] == ""
    assert interaction["interactor_B"]["protein_gene_name"] == "MDM2"
