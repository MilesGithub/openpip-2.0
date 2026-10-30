"""The legacy PPI responses do not change when typed data exists.

The typed graph lives in its own tables, and nothing in the legacy code reads
them. These tests hold that from the outside: they record the legacy
responses, create typed entities and identifiers that use the same strings
(TP53, P04637, 7157 ...), with the provenance and licensing records of the
releases that introduce them (named like the legacy dataset, citing its
PubMed id), and expect every response back byte for byte.

The typed rows include relationships between those entities with evidence
of every kind, recorded by graph.claims.record_claims from the source
records of a release that is then published and approved for public view,
so visitors of the typed graph may see them: TP53 regulating MDM2, with
activating, inhibiting, unknown and unstated effects, a prediction, an
inconclusive and a not-significant test; TP53 and MDM2 binding, at each
level, with a tested-absent assertion; the same pair without a stated
interaction type; a negative genetic interaction between their genes; and
the encodes and isoform_of links between their genes, proteins and
isoforms; with methods of each role, publications, scores and contexts.
PSICQUIC still serves the two legacy interactions and nothing typed.

The typed records point at the legacy user table only through SET_NULL keys
with a username kept beside each, so deleting an account in the Django admin
still works.
"""

import datetime
import io

import pytest
from django.apps import apps
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import User, UserInteractionNetwork
from datasets.tests.factories import DatasetFactory
from graph.approvals import record_decision, revoke
from graph.claims import (
    Claim,
    ContextValue,
    Endpoint,
    MethodRef,
    PublicationRef,
    ScoreValue,
    record_claims,
)
from graph.entities import create_entity
from graph.licensing import confirm_due_diligence, record_licence
from graph.models import (
    ApprovalScope,
    AssertionMethod,
    DataSource,
    DataSourceKind,
    DatasetRelease,
    DatasetStatus,
    Decision,
    DueDiligenceChange,
    DueDiligenceRecord,
    Effect,
    Entity,
    EvidenceAssertion,
    EvidenceBasis,
    GeneticSign,
    IdentifierNamespace,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    InteractionLevel,
    Orientation,
    Outcome,
    Permission,
    Polarity,
    PublicationRole,
    Relationship,
    RelationshipFamily,
    RelationshipType,
    ReleaseLicence,
    ReleasePublication,
    ReleaseStatus,
    ResolutionBasis,
    ScoreDirection,
    ScoreType,
    ScoreValueKind,
    ShareAlike,
    SourceDataset,
    SourceDatasetPublication,
    UsageApproval,
)
from graph.publications import get_or_create_publication
from graph.releases import publish_release
from graph.summary import summarize
from graph.terms import get_or_create_term
from graph.tests.factories import (
    EntityIdentifierFactory,
    ImportRunFactory,
    SourceFileFactory,
    SourceRecordFactory,
)
from graph.visibility import Purpose, visible_release_ids
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

# The legacy strings: symbol, HGNC id, UniProt accession, NCBI Gene id and
# Ensembl gene id, as the legacy proteins below hold them.
LEGACY_STRINGS = (
    ("TP53", "HGNC:11998", "P04637", "7157", "ENSG00000141510"),
    ("MDM2", "HGNC:6973", "Q00987", "4193", "ENSG00000135679"),
)
# The source records release v2's commit reads, by key, as the file states
# them.
RECORDS = {
    "compat-regulation-1": {"regulator": "TP53", "target": "MDM2", "effect": "up"},
    "compat-regulation-2": {"regulator": "7157", "target": "4193", "effect": "down"},
    "compat-regulation-3": {"regulator": "TP53", "target": "MDM2", "effect": "?"},
    "compat-regulation-4": {"regulator": "TP53", "target": "MDM2", "predicted": "1"},
    "compat-regulation-5": {"regulator": "TP53", "target": "MDM2", "result": "?"},
    "compat-regulation-6": {"regulator": "TP53", "target": "MDM2", "p": "0.2"},
    "compat-binding-1": {"a": "Q00987", "b": "P04637", "type": "MI:0915"},
    "compat-binding-2": {"a": "P04637", "b": "Q00987", "type": "MI:0407"},
    "compat-binding-3": {"a": "P04637", "b": "Q00987", "assay": "AP-MS"},
    "compat-binding-4": {"a": "P04637", "b": "Q00987"},
    "compat-genetic": {"a": "TP53", "b": "MDM2", "score": "-0.31"},
    "compat-encodes": {"HGNC:11998": "P04637", "HGNC:6973": "Q00987"},
    "compat-isoforms": {"P04637-2": "P04637", "Q00987-2": "Q00987"},
}
# The terms the records name, with the labels they state.
TERMS = {
    "MI:0007": "anti tag coimmunoprecipitation",
    "MI:0018": "two hybrid",
    "MI:0396": "predetermined participant",
    "MI:0402": "chromatin immunoprecipitation assay",
    "MI:0407": "direct interaction",
    "MI:0915": "physical association",
    "ECO:0000353": "physical interaction evidence used in manual assertion",
}
# The compat source's score types and genetic interaction type: test-only,
# never in the registry JSON (no genetic type is registered until N1).
CONFIDENCE = "compat:confidence"
P_VALUE = "compat:p_value"
GENETIC_SCORE = "compat:genetic_score"
GENETIC = "compat_genetic"
DEFINED_IN = "docs/project/generalization/TEST_PLAN.md"
# Strings only the typed rows hold.
TYPED_ONLY = (
    "HGNC:11998",
    "HGNC:6973",
    "P04637-2",
    "Q00987-2",
    "transcriptional_regulation",
    "physical_interaction",
    "protein_interaction_unspecified",
    "isoform_of",
    GENETIC,
    "compat-regulation",
    "compat-binding",
    "compat-methods",
    CONFIDENCE,
    P_VALUE,
    GENETIC_SCORE,
    "MI:0402",
    "ECO:0000353",
    "HCT116",
)


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


def release_with_terms(dataset, version, paper, approver, supersedes=None, records=()):
    """A release citing the paper, with open terms recorded and its dataset's
    due diligence confirmed after them, by the approver. Staged; or, given
    record keys (RECORDS), a draft being committed: a file, a commit run the
    approver started, and a mapped source record of each key, added before
    anyone decides on the release, as its files must be."""
    release = DatasetRelease.objects.create(
        dataset=dataset,
        version=version,
        status=ReleaseStatus.DRAFT if records else ReleaseStatus.STAGED,
        supersedes=supersedes,
        source_url="https://example.org/compat",
    )
    ReleasePublication.objects.create(
        release=release, publication=paper, role=PublicationRole.DESCRIBES
    )
    if records:
        file = SourceFileFactory(release=release, original_name="compat.tsv")
        run = ImportRunFactory(
            release=release,
            file=file,
            adapter="compat",
            mode=ImportMode.COMMIT,
            triggered_by=approver,
        )
        for key in records:
            SourceRecordFactory(
                release=release,
                file=file,
                import_run=run,
                record_key=key,
                raw=RECORDS[key],
            )
    record_licence(
        release,
        recorded_by=approver,
        licence_name="Compat",
        read_at="https://example.org/compat/terms",
        read_on=datetime.date(2026, 10, 1),
        redistribution=Permission.PERMITTED,
        commercial_use=Permission.PERMITTED,
        modification=Permission.PERMITTED,
        share_alike=ShareAlike.NO,
        attribution="Compat, PubMed 12345678.",
    )
    confirm_due_diligence(dataset.due_diligence, by=approver)
    return release


def decide(release, scope, approver):
    return record_decision(
        release,
        scope,
        Decision.APPROVED,
        rationale="Compat: legacy strings in typed records.",
        decided_by=approver,
        evidence="https://example.org/compat/evidence",
    )


def add_provenance_rows(approver):
    """A data source and dataset named like the legacy dataset, a publication
    with its PubMed id, a published v1 and its correction v2, a draft being
    committed, with licence snapshots, due diligence and decisions recorded
    by the approver. Returns v2, which introduces the typed entities and
    states their relationships (add_evidence then publishes it, which
    supersedes v1, and stages its own correction, v3)."""
    source = DataSource.objects.create(
        slug="compat", name="Compat", kind=DataSourceKind.LAB
    )
    dataset = SourceDataset.objects.create(
        slug="compat",
        title="Compat",
        data_source=source,
        status=DatasetStatus.ACTIVE,
    )
    paper = get_or_create_publication(pmid="12345678", title="Compat").publication
    SourceDatasetPublication.objects.create(
        dataset=dataset, publication=paper, role=PublicationRole.PRIMARY
    )
    record = DueDiligenceRecord(
        dataset=dataset,
        reviewer="Compat",
        notes_link="docs/project/generalization/DATASET_DUE_DILIGENCE.md",
    )
    record._changed_by = approver
    record.save()

    v1 = release_with_terms(dataset, "v1", paper, approver)
    for scope in (
        ApprovalScope.INTERNAL,
        ApprovalScope.PUBLIC_VIEW,
        ApprovalScope.PUBLIC_BULK,
    ):
        decide(v1, scope, approver)
    publish_release(v1, published_by=approver)
    revoke(
        UsageApproval.objects.get(release=v1, scope=ApprovalScope.PUBLIC_BULK),
        reason="Compat: a revoked decision.",
        revoked_by=approver,
    )
    v2 = release_with_terms(
        dataset, "v2", paper, approver, supersedes=v1, records=list(RECORDS)
    )
    decide(v2, ApprovalScope.INTERNAL, approver)
    return v2


def add_typed_rows(settings, approver=None):
    """Typed entities and identifiers that use the legacy rows' strings and,
    given the account that records them, the provenance and licensing rows
    of the release that introduces them, and the relationships and evidence
    that release states, published for visitors to view."""
    settings.TYPED_GRAPH_ENABLED = True
    call_command("graph_sync_registry", stdout=io.StringIO())
    release = add_provenance_rows(approver) if approver is not None else None
    ncbigene = IdentifierNamespace.objects.get(slug="ncbigene")
    ensembl = IdentifierNamespace.objects.get(slug="ensembl_gene")
    entities = {}
    for symbol, hgnc, uniprot, entrez, ensembl_id in LEGACY_STRINGS:
        gene = create_entity("gene", symbol, 9606, "hgnc", hgnc, release=release)
        entry = create_entity(
            "protein", symbol, 9606, "uniprotkb", uniprot, release=release
        )
        isoform = create_entity(
            "protein_isoform",
            symbol,
            9606,
            "uniprotkb",
            f"{uniprot}-2",
            release=release,
        )
        entities[symbol] = (gene, entry, isoform)
        EntityIdentifierFactory(
            entity=gene, namespace=ncbigene, value=entrez, role=IdentifierRole.XREF
        )
        EntityIdentifierFactory(
            entity=gene, namespace=ensembl, value=ensembl_id, role=IdentifierRole.XREF
        )
    if release is not None:
        add_evidence(release, entities, approver)


def stated(entity, value) -> Endpoint:
    """An endpoint as the record writes it: by the entity's own identifier."""
    return Endpoint(entity, value, ResolutionBasis.PRIMARY)


def evidence_claims(entities, paper, methods_paper) -> dict[str, list[Claim]]:
    """{record key: its claims}: the relationships each source record states,
    as an adapter would map them, with evidence of every kind."""
    tp53_gene, tp53, tp53_isoform = entities["TP53"]
    mdm2_gene, mdm2, mdm2_isoform = entities["MDM2"]
    reported = (PublicationRef(paper, "reported_in", {"pmid": "12345678"}),)
    experiment = {
        "outcome": Outcome.SUPPORTED,
        "evidence_basis": EvidenceBasis.EXPERIMENTAL,
        "assertion_method": AssertionMethod.MANUAL_ASSERTION,
    }
    reference = {
        "outcome": Outcome.SUPPORTED,
        "evidence_basis": EvidenceBasis.UNKNOWN,
        "assertion_method": AssertionMethod.MANUAL_ASSERTION,
    }
    two_hybrid = MethodRef("MI:0018", "detection")

    def regulation(**fields):
        """TP53 regulating MDM2's transcription in a curated experiment,
        unless told otherwise."""
        claim = {
            **experiment,
            "subject": stated(tp53_gene, "HGNC:11998"),
            "object": stated(mdm2_gene, "HGNC:6973"),
            "relationship_as_stated": "TP53 regulates MDM2 transcription",
            "stated_directed": True,
            "methods": (MethodRef("MI:0402", "detection"),),
            "publications": reported,
            **fields,
        }
        return Claim(relationship_type="transcriptional_regulation", **claim)

    def binding(relationship_type="physical_interaction", **fields):
        """TP53 and MDM2 binding in a two-hybrid test, unless told
        otherwise."""
        claim = {
            **experiment,
            "subject": stated(tp53, "P04637"),
            "object": stated(mdm2, "Q00987"),
            "stated_directed": False,
            "methods": (two_hybrid,),
            "publications": reported,
            **fields,
        }
        return Claim(relationship_type=relationship_type, **claim)

    return {
        # Contradictory findings are kept side by side.
        "compat-regulation-1": [
            regulation(
                effect=Effect.ACTIVATING,
                effect_as_stated="up",
                evidence_group="ChIP series 1",
                qualifiers={"assay": "ChIP-seq"},
                publications=(*reported, PublicationRef(methods_paper, "cited")),
                scores=(ScoreValue(CONFIDENCE, "0.875"),),
                contexts=(ContextValue("cell_line", "HCT116", label="HCT 116"),),
            )
        ],
        # Stated by NCBI Gene ids, which the genes hold as cross-references.
        "compat-regulation-2": [
            regulation(
                subject=Endpoint(tp53_gene, "7157", ResolutionBasis.XREF_UNIQUE),
                object=Endpoint(mdm2_gene, "4193", ResolutionBasis.XREF_UNIQUE),
                effect=Effect.INHIBITING,
                effect_as_stated="down",
                scores=(ScoreValue(CONFIDENCE, "0.42"),),
            )
        ],
        "compat-regulation-3": [
            regulation(effect=Effect.UNKNOWN, effect_as_stated="?")
        ],
        # A prediction from no paper, stating no effect.
        "compat-regulation-4": [
            regulation(
                evidence_basis=EvidenceBasis.COMPUTATIONAL_PREDICTION,
                assertion_method=AssertionMethod.AUTOMATIC_ASSERTION,
                methods=(),
                publications=(),
            )
        ],
        "compat-regulation-5": [regulation(outcome=Outcome.INCONCLUSIVE)],
        "compat-regulation-6": [
            regulation(
                outcome=Outcome.NOT_SIGNIFICANT,
                scores=(ScoreValue(P_VALUE, "0.2"),),
                qualifiers={
                    "threshold": [
                        {"score_type": P_VALUE, "operator": "<", "value": "0.05"}
                    ]
                },
            )
        ],
        # Stated MDM2 first; the relationship keeps the lower entity id first.
        "compat-binding-1": [
            binding(
                subject=stated(mdm2, "Q00987"),
                object=stated(tp53, "P04637"),
                interaction_type="MI:0915",
                interaction_level=InteractionLevel.PHYSICAL_ASSOCIATION,
                eco_term="ECO:0000353",
                methods=(
                    two_hybrid,
                    MethodRef("MI:0396", "participant_identification"),
                    MethodRef("ECO:0000353", "evidence_code"),
                ),
                publications=(
                    *reported,
                    PublicationRef(methods_paper, "method_reference"),
                ),
                scores=(ScoreValue(CONFIDENCE, "0.91"),),
                contexts=(
                    ContextValue("host_taxon", "9606", taxon=9606),
                    ContextValue("assay_system", "in vivo"),
                ),
            )
        ],
        "compat-binding-2": [
            binding(
                outcome=Outcome.TESTED_ABSENT,
                interaction_type="MI:0407",
                interaction_level=InteractionLevel.DIRECT_INTERACTION,
            )
        ],
        # Co-purification with no stated interaction type: association,
        # assigned by openPIP (open question B14), in an unregistered host.
        "compat-binding-3": [
            binding(
                interaction_level=InteractionLevel.ASSOCIATION,
                methods=(MethodRef("MI:0007", "detection"),),
                contexts=(ContextValue("host_taxon", "10090", taxon=10090),),
            )
        ],
        "compat-binding-4": [
            binding(
                relationship_type="protein_interaction_unspecified",
                interaction_level=InteractionLevel.NOT_STATED,
            )
        ],
        "compat-genetic": [
            Claim(
                relationship_type=GENETIC,
                subject=stated(tp53_gene, "HGNC:11998"),
                object=stated(mdm2_gene, "HGNC:6973"),
                outcome=Outcome.SUPPORTED,
                genetic_sign=GeneticSign.NEGATIVE,
                evidence_basis=EvidenceBasis.EXPERIMENTAL,
                assertion_method=AssertionMethod.AUTOMATIC_ASSERTION,
                scores=(ScoreValue(GENETIC_SCORE, "-0.31"),),
            )
        ],
        "compat-encodes": [
            Claim(
                relationship_type="encodes",
                subject=stated(gene, gene_id),
                object=stated(entry, accession),
                key_part=symbol,
                **reference,
            )
            for symbol, gene, gene_id, entry, accession in (
                ("TP53", tp53_gene, "HGNC:11998", tp53, "P04637"),
                ("MDM2", mdm2_gene, "HGNC:6973", mdm2, "Q00987"),
            )
        ],
        "compat-isoforms": [
            Claim(
                relationship_type="isoform_of",
                subject=stated(isoform, f"{accession}-2"),
                object=stated(entry, accession),
                key_part=symbol,
                **reference,
            )
            for symbol, isoform, entry, accession in (
                ("TP53", tp53_isoform, tp53, "P04637"),
                ("MDM2", mdm2_isoform, mdm2, "Q00987"),
            )
        ],
    }


def add_source_types():
    """The compat source's score types, and a genetic interaction type for
    its sign, as test-only registry rows."""
    ScoreType.objects.bulk_create(
        [
            ScoreType(
                slug=CONFIDENCE,
                label="Compat confidence",
                definition="Test only: the source's confidence in a claim.",
                value_kind=ScoreValueKind.PROBABILITY,
                direction=ScoreDirection.HIGHER_IS_STRONGER,
                range_min=0,
                range_max=1,
                defining_document=DEFINED_IN,
            ),
            ScoreType(
                slug=P_VALUE,
                label="Compat p-value",
                definition="Test only: the p-value of the source's test of a claim.",
                value_kind=ScoreValueKind.P_VALUE,
                direction=ScoreDirection.LOWER_IS_STRONGER,
                range_min=0,
                range_max=1,
                defining_document=DEFINED_IN,
            ),
            ScoreType(
                slug=GENETIC_SCORE,
                label="Compat genetic interaction score",
                definition="Test only: a double mutant's fitness against the null "
                "model.",
                value_kind=ScoreValueKind.CONTINUOUS,
                direction=ScoreDirection.SIGNED,
                null_model="The product of the two single mutants' fitness.",
                defining_document=DEFINED_IN,
            ),
        ]
    )
    genetic = RelationshipType.objects.create(
        slug=GENETIC,
        label="Compat genetic interaction",
        definition="Test only: two genes interact genetically, with the sign "
        "the score's null model defines.",
        family=RelationshipFamily.GENETIC,
        orientation=Orientation.SYMMETRIC,
        polarity=Polarity.GENETIC_SIGN,
        subject_role_label="gene",
        object_role_label="gene",
        self_pairs_allowed=False,
    )
    genetic.allowed_subject_types.set(["gene"])
    genetic.allowed_object_types.set(["gene"])


def add_evidence(release, entities, approver):
    """The relationships release v2 states, recorded by its commit run from
    its source records, with the terms they name. The run then completes, and
    the release is staged, approved for public view and published, which
    supersedes v1; its own correction, v3, is staged with an internal
    approval."""
    run = ImportRun.objects.get(release=release, mode=ImportMode.COMMIT)
    for curie, label in TERMS.items():
        get_or_create_term(curie, label, import_run=run)
    add_source_types()
    paper = ReleasePublication.objects.get(release=release).publication
    # A paper with no PMID or DOI, known by the dataset's own key.
    methods_paper = get_or_create_publication(
        local_dataset=release.dataset_id,
        local_key="compat-methods",
        title="Compat methods",
    ).publication
    records = {record.record_key: record for record in release.records.all()}
    for key, claims in evidence_claims(entities, paper, methods_paper).items():
        record_claims(records[key], claims, import_run=run)
    run.state = ImportState.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["state", "finished_at"])
    release.status = ReleaseStatus.STAGED
    release.save(update_fields=["status", "updated_at"])
    # v1 is public, so its correction is published only once it is too.
    decide(release, ApprovalScope.PUBLIC_VIEW, approver)
    publish_release(release, published_by=approver)
    # And v2's own correction, staged, which visitors never see.
    v3 = release_with_terms(release.dataset, "v3", paper, approver, supersedes=release)
    decide(v3, ApprovalScope.INTERNAL, approver)


def test_legacy_responses_are_byte_identical_with_typed_rows_present(
    legacy, regular_user, admin_user, settings
):
    before = legacy_responses(regular_user, legacy)
    home_keys = home_network_keys()
    counts = legacy_row_counts()
    assert all(status == 200 for status, _ in before.values()), {
        name: status for name, (status, _) in before.items()
    }
    assert b"uniprotkb:P04637" in before["psicquic tab28"][1]
    assert before["psicquic count"][1] == b"2"

    # Decided by the legacy admin account, which the root conftest creates.
    add_typed_rows(settings, approver=admin_user)
    assert Entity.objects.filter(created_by_release__version="v2").count() == 6
    assert UsageApproval.objects.filter(decided_by=admin_user).count() == 6
    assert list(
        DatasetRelease.objects.order_by("version").values_list("version", "status")
    ) == [
        ("v1", ReleaseStatus.SUPERSEDED),
        ("v2", ReleaseStatus.PUBLISHED),
        ("v3", ReleaseStatus.STAGED),
    ]
    # The typed evidence is published, and visitors to the typed graph may
    # view every relationship, each summarized by its type's rules.
    v2 = DatasetRelease.objects.get(version="v2")
    assert (v2.status, Relationship.objects.count()) == (ReleaseStatus.PUBLISHED, 8)
    assert EvidenceAssertion.objects.filter(release=v2).count() == 15
    releases = visible_release_ids(RequestFactory().get("/"), Purpose.VIEW)
    assert releases.ids == {v2.pk}
    summaries = summarize(
        Relationship.objects.values_list("pk", flat=True), releases=releases
    )
    assert sorted(edge.relationship_type for edge in summaries.values()) == sorted(
        [
            "transcriptional_regulation",
            "physical_interaction",
            "protein_interaction_unspecified",
            GENETIC,
            *["encodes", "isoform_of"] * 2,
        ]
    )
    edges = {edge.relationship_type: edge for edge in summaries.values()}
    regulation = edges["transcriptional_regulation"]
    assert regulation.effect == "mixed"
    assert regulation.effect_counts == {
        "activating": 1,
        "inhibiting": 1,
        "unknown": 1,
        "not_stated": 1,
    }
    assert [regulation.outcome_counts[name] for name in Outcome.values] == [4, 0, 1, 1]
    # Three records of one experiment, and a prediction from no paper.
    assert (regulation.assertion_count, regulation.evidence_count) == (4, 2)
    binding = edges["physical_interaction"]
    assert (binding.level, binding.level_assigned) == (
        InteractionLevel.PHYSICAL_ASSOCIATION,
        False,
    )
    assert binding.outcome_counts[Outcome.TESTED_ABSENT] == 1
    assert edges["protein_interaction_unspecified"].level == InteractionLevel.NOT_STATED
    assert edges[GENETIC].genetic_sign == GeneticSign.NEGATIVE

    assert legacy_row_counts() == counts
    after = legacy_responses(regular_user, legacy)
    for name in before:
        assert after[name] == before[name], f"{name} changed"
    assert home_network_keys() == home_keys
    assert legacy_row_counts() == counts
    # PSICQUIC serves the two legacy interactions, and no typed relationship.
    tab28 = after["psicquic tab28"][1].decode()
    header, *rows = tab28.split("\n")
    assert header.startswith("#") and len(rows) == 2
    assert after["psicquic count"][1] == b"2"
    for typed in TYPED_ONLY:
        assert typed not in tab28, typed


def actors(model, name) -> set:
    """(account id, username snapshot) pairs of one actor field."""
    return set(model.objects.values_list(f"{name}_id", f"{name}_username"))


@pytest.mark.parametrize("switch", [False, True], ids=["switch off", "switch on"])
def test_deleting_a_legacy_user_in_the_django_admin_keeps_the_typed_records(
    legacy, admin_user, settings, client, switch
):
    add_typed_rows(settings, approver=admin_user)
    settings.TYPED_GRAPH_ENABLED = switch
    approvals = UsageApproval.objects.count()
    assertions = EvidenceAssertion.objects.count()
    username = admin_user.username
    assert actors(UsageApproval, "decided_by") == {(admin_user.pk, username)}
    assert (admin_user.pk, username) in actors(UsageApproval, "revoked_by")
    assert actors(ImportRun, "triggered_by") == {(admin_user.pk, username)}

    deleter = User.objects.create(username="deleter", is_staff=True, is_superuser=True)
    client.force_login(deleter)
    delete = reverse("admin:core_user_delete", args=[admin_user.pk])
    confirm = client.get(delete)
    assert confirm.status_code == 200
    assert not confirm.context["perms_lacking"] and not confirm.context["protected"]
    assert client.post(delete, {"post": "yes"}).status_code == 302

    assert not User.objects.filter(pk=admin_user.pk).exists()
    assert UsageApproval.objects.count() == approvals
    assert EvidenceAssertion.objects.count() == assertions
    assert actors(UsageApproval, "decided_by") == {(None, username)}
    assert actors(UsageApproval, "revoked_by") == {(None, ""), (None, username)}
    assert actors(ReleaseLicence, "recorded_by") == {(None, username)}
    assert actors(DueDiligenceChange, "changed_by") == {(None, username)}
    assert actors(ImportRun, "triggered_by") == {(None, username)}
    assert (None, username) in actors(DatasetRelease, "published_by")


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
