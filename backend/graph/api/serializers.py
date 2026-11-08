"""The shapes of /api/graph/v1/, each declared once. A handler builds plain
dicts and lists and returns them through its view's serializer_class, so a key
not declared here never reaches a client, and drf-spectacular derives the
schema from the same classes.

Fields are declared plain, without read_only=True, which would drop a key the
handler forgot without a word: a plain field raises KeyError. allow_null=True
turns a missing key into null, so each route's test asserts its exact keys.
"""

from rest_framework import serializers

# ── Errors ──────────────────────────────────────────────────────────────────


class ProblemSerializer(serializers.Serializer):
    parameter = serializers.CharField()
    value = serializers.CharField(allow_null=True)
    allowed = serializers.ListField(child=serializers.CharField(), allow_null=True)
    limit = serializers.IntegerField(allow_null=True)


class ParameterErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    errors = ProblemSerializer(many=True)


# ── Status ──────────────────────────────────────────────────────────────────


class RegistryVersionSerializer(serializers.Serializer):
    code = serializers.CharField()
    database = serializers.CharField(allow_null=True)


class FeaturesSerializer(serializers.Serializer):
    resolve = serializers.BooleanField()
    entities = serializers.BooleanField()
    network = serializers.BooleanField()
    relationships = serializers.BooleanField()
    browsers = serializers.BooleanField()
    datasets = serializers.BooleanField()
    summary = serializers.BooleanField()
    exports = serializers.BooleanField()


class LimitsSerializer(serializers.Serializer):
    q_max_chars = serializers.IntegerField()
    resolve_max_terms = serializers.IntegerField()
    resolve_max_candidates = serializers.IntegerField()
    max_releases = serializers.IntegerField()
    max_seeds = serializers.IntegerField()
    max_layers = serializers.IntegerField()
    max_context_facets = serializers.IntegerField()
    default_max_edges = serializers.IntegerField()
    max_edges = serializers.IntegerField()
    candidates_per_seed = serializers.IntegerField()
    max_nodes = serializers.IntegerField()
    evidence_page_size = serializers.IntegerField()
    evidence_max_page_size = serializers.IntegerField()
    evidence_page_raw_chars = serializers.IntegerField()
    throttles = serializers.DictField(child=serializers.CharField())


class StatusSerializer(serializers.Serializer):
    typed_graph = serializers.BooleanField()
    api_version = serializers.CharField()
    registry_version = RegistryVersionSerializer()
    features = FeaturesSerializer()
    limits = LimitsSerializer()


# ── Vocabularies ────────────────────────────────────────────────────────────


class TaxonEntrySerializer(serializers.Serializer):
    taxid = serializers.IntegerField()
    scientific_name = serializers.CharField()
    common_name = serializers.CharField(allow_null=True)
    rank = serializers.CharField(allow_null=True)
    parent = serializers.IntegerField(allow_null=True)
    merged_into = serializers.IntegerField(allow_null=True)


class EntityTypeEntrySerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()
    definition = serializers.CharField()
    taxon_policy = serializers.CharField()
    identity_scheme = serializers.CharField()
    assembly_required = serializers.BooleanField()
    display_token = serializers.CharField(allow_null=True)


class NamespaceEntrySerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()
    curie_prefix = serializers.CharField()
    allowed_entity_types = serializers.ListField(child=serializers.CharField())
    scope = serializers.CharField()
    fixed_taxon = serializers.IntegerField(allow_null=True)
    case_policy = serializers.CharField()
    version_policy = serializers.CharField()
    defines_identity = serializers.BooleanField()
    pattern = serializers.CharField()
    # "" means no link.
    url_template = serializers.CharField(allow_blank=True)
    authority = serializers.CharField()


class RelationshipTypeEntrySerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()
    definition = serializers.CharField()
    family = serializers.CharField()
    orientation = serializers.CharField()
    polarity = serializers.CharField()
    allowed_subject_types = serializers.ListField(child=serializers.CharField())
    allowed_object_types = serializers.ListField(child=serializers.CharField())
    subject_role_label = serializers.CharField()
    object_role_label = serializers.CharField()
    self_pairs_allowed = serializers.BooleanField()
    psi_mi_anchors = serializers.ListField(child=serializers.CharField())
    display_token = serializers.CharField(allow_null=True)
    is_reference = serializers.BooleanField()


class ScoreTypeEntrySerializer(serializers.Serializer):
    slug = serializers.CharField()
    label = serializers.CharField()
    definition = serializers.CharField()
    value_kind = serializers.CharField()
    direction = serializers.CharField()
    range_min = serializers.FloatField(allow_null=True)
    range_max = serializers.FloatField(allow_null=True)
    unit = serializers.CharField(allow_null=True)
    null_model = serializers.CharField(allow_null=True)
    defining_document = serializers.CharField()
    comparable_across_datasets = serializers.BooleanField()


class ClosedEntrySerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class SummaryEntrySerializer(ClosedEntrySerializer):
    # False for a value only summaries emit, which the database never holds.
    stored = serializers.BooleanField()


class LevelEntrySerializer(ClosedEntrySerializer):
    strength = serializers.IntegerField()


class VocabulariesSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    registry_version = RegistryVersionSerializer()
    taxa = TaxonEntrySerializer(many=True)
    entity_types = EntityTypeEntrySerializer(many=True)
    namespaces = NamespaceEntrySerializer(many=True)
    relationship_types = RelationshipTypeEntrySerializer(many=True)
    score_types = ScoreTypeEntrySerializer(many=True)
    outcomes = ClosedEntrySerializer(many=True)
    effects = SummaryEntrySerializer(many=True)
    genetic_signs = SummaryEntrySerializer(many=True)
    evidence_bases = ClosedEntrySerializer(many=True)
    assertion_methods = ClosedEntrySerializer(many=True)
    context_facets = ClosedEntrySerializer(many=True)
    interaction_levels = LevelEntrySerializer(many=True)
    identifier_roles = ClosedEntrySerializer(many=True)


# ── Shared by the routes that read data ─────────────────────────────────────


class PrimaryIdentifierSerializer(serializers.Serializer):
    namespace = serializers.CharField()
    value = serializers.CharField()


class EntityNodeSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    label = serializers.CharField()
    taxon = serializers.IntegerField(allow_null=True)
    # Nullable for a node with no entity row: v1 allows no later change of
    # type. Every node today has a value.
    status = serializers.CharField(allow_null=True)
    primary_identifier = PrimaryIdentifierSerializer(allow_null=True)


class ReleaseDatasetSerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()


class LicenceSerializer(serializers.Serializer):
    name = serializers.CharField(allow_null=True)
    spdx_id = serializers.CharField(allow_null=True)
    url = serializers.CharField(allow_null=True)


class ReleaseEntrySerializer(serializers.Serializer):
    id = serializers.CharField()
    dataset = ReleaseDatasetSerializer()
    version = serializers.CharField()
    status = serializers.CharField()
    superseded = serializers.BooleanField()
    is_synthetic = serializers.BooleanField()
    licence = LicenceSerializer()
    attribution = serializers.CharField(allow_blank=True)
    changes_note = serializers.CharField(allow_null=True)
    scopes = serializers.ListField(child=serializers.CharField())


class WarningSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class DistinctSerializer(serializers.Serializer):
    publications = serializers.IntegerField()
    releases = serializers.IntegerField()
    datasets = serializers.IntegerField()


class EdgeSerializer(serializers.Serializer):
    """One relationship as the network, the relationship page and the entity
    page show it (architecture §7.3): the eight keys of the first version
    first, then every figure of its summary. v1 allows only additive change,
    so it has one edge shape, built by shapes.edge."""

    id = serializers.CharField()
    type = serializers.CharField()
    family = serializers.CharField()
    orientation = serializers.CharField()
    source = serializers.CharField()
    target = serializers.CharField()
    releases = serializers.ListField(child=serializers.CharField())
    is_synthetic = serializers.BooleanField()
    qualifiers = serializers.JSONField()
    shown = serializers.BooleanField()
    # Null without covered support.
    effect = serializers.CharField(allow_null=True)
    effect_counts = serializers.DictField(child=serializers.IntegerField())
    genetic_sign = serializers.CharField(allow_null=True)
    sign_counts = serializers.DictField(child=serializers.IntegerField())
    outcome_counts = serializers.DictField(child=serializers.IntegerField())
    independent_outcome_counts = serializers.DictField(child=serializers.IntegerField())
    assertion_count = serializers.IntegerField()
    evidence_count = serializers.IntegerField()
    distinct = DistinctSerializer()
    by_basis = serializers.DictField(child=serializers.IntegerField())
    # Physical family only, and only with covered support.
    level = serializers.CharField(allow_null=True)
    level_counts = serializers.DictField(child=serializers.IntegerField())
    level_assigned = serializers.BooleanField(allow_null=True)
    tested_levels = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField())
    )
    outside_filters = serializers.IntegerField()
    outside_outcome_counts = serializers.DictField(child=serializers.IntegerField())


# ── Resolve ─────────────────────────────────────────────────────────────────


class MatchSerializer(serializers.Serializer):
    kind = serializers.CharField()
    # Null for a label.
    namespace = serializers.CharField(allow_null=True)
    value = serializers.CharField()
    case_exact = serializers.BooleanField()
    version_mismatch = serializers.BooleanField()


class CandidateSerializer(serializers.Serializer):
    entity = EntityNodeSerializer()
    exact_primary = serializers.BooleanField()
    matches = MatchSerializer(many=True)
    releases = serializers.ListField(child=serializers.CharField())


class ResolveResultSerializer(serializers.Serializer):
    term = serializers.CharField()
    status = serializers.CharField()
    truncated = serializers.BooleanField()
    candidates = CandidateSerializer(many=True)


class ResolveQuerySerializer(serializers.Serializer):
    q = serializers.ListField(child=serializers.CharField())
    namespace = serializers.CharField(allow_null=True)
    taxon = serializers.IntegerField(allow_null=True)
    entity_type = serializers.CharField(allow_null=True)
    releases = serializers.ListField(child=serializers.CharField(), allow_null=True)


class ResolvePageSerializer(serializers.Serializer):
    truncated = serializers.BooleanField()
    max_candidates = serializers.IntegerField()
    omitted = serializers.DictField(child=serializers.IntegerField())


class ResolveSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = ResolveQuerySerializer()
    results = ResolveResultSerializer(many=True)
    releases = ReleaseEntrySerializer(many=True)
    page = ResolvePageSerializer()
    warnings = WarningSerializer(many=True)


# ── Entities ────────────────────────────────────────────────────────────────


class IdentifierSerializer(serializers.Serializer):
    namespace = serializers.CharField()
    value = serializers.CharField()
    version = serializers.CharField(allow_null=True)
    role = serializers.CharField()
    # An http(s) URL with a host, or null.
    url = serializers.CharField(allow_null=True)


class EntityQuerySerializer(serializers.Serializer):
    entity_id = serializers.CharField()
    releases = serializers.ListField(child=serializers.CharField(), allow_null=True)


class EntitySerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = EntityQuerySerializer()
    entity = EntityNodeSerializer()
    description = serializers.CharField(allow_blank=True)
    created_by_release = serializers.CharField(allow_null=True)
    identifiers = IdentifierSerializer(many=True)
    reference_relationships = EdgeSerializer(many=True)
    nodes = EntityNodeSerializer(many=True)
    relationship_counts = serializers.DictField(child=serializers.IntegerField())
    releases = ReleaseEntrySerializer(many=True)


# ── Network ─────────────────────────────────────────────────────────────────


class NetworkNodeSerializer(EntityNodeSerializer):
    is_seed = serializers.BooleanField()


class NetworkQuerySerializer(serializers.Serializer):
    seeds = serializers.ListField(child=serializers.CharField())
    layers = serializers.ListField(child=serializers.CharField(), allow_null=True)
    direction = serializers.CharField(allow_null=True)
    outcome = serializers.ListField(child=serializers.CharField(), allow_null=True)
    evidence_basis = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )
    effect = serializers.ListField(child=serializers.CharField(), allow_null=True)
    method = serializers.ListField(child=serializers.CharField(), allow_null=True)
    level = serializers.ListField(child=serializers.CharField(), allow_null=True)
    score = serializers.ListField(child=serializers.CharField(), allow_null=True)
    # {facet: value} of the context.<facet> parameters given; {} for none.
    context = serializers.DictField(child=serializers.CharField())
    taxon = serializers.IntegerField(allow_null=True)
    include_partner_edges = serializers.BooleanField(allow_null=True)
    max_edges = serializers.IntegerField(allow_null=True)
    releases = serializers.ListField(child=serializers.CharField(), allow_null=True)


class NetworkPageSerializer(serializers.Serializer):
    truncated = serializers.BooleanField()
    max_edges = serializers.IntegerField()
    omitted = serializers.DictField(child=serializers.IntegerField())
    hidden = serializers.DictField(child=serializers.IntegerField())


class NetworkSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = NetworkQuerySerializer()
    nodes = NetworkNodeSerializer(many=True)
    edges = EdgeSerializer(many=True)
    releases = ReleaseEntrySerializer(many=True)
    page = NetworkPageSerializer()
    warnings = WarningSerializer(many=True)


# ── Relationships ───────────────────────────────────────────────────────────


class RelationshipQuerySerializer(serializers.Serializer):
    relationship_ref = serializers.CharField()
    outcome = serializers.ListField(child=serializers.CharField(), allow_null=True)
    evidence_basis = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )
    effect = serializers.ListField(child=serializers.CharField(), allow_null=True)
    method = serializers.ListField(child=serializers.CharField(), allow_null=True)
    level = serializers.ListField(child=serializers.CharField(), allow_null=True)
    score = serializers.ListField(child=serializers.CharField(), allow_null=True)
    context = serializers.DictField(child=serializers.CharField())
    releases = serializers.ListField(child=serializers.CharField(), allow_null=True)


class ReleaseCountsSerializer(serializers.Serializer):
    release = serializers.CharField()
    outcome_counts = serializers.DictField(child=serializers.IntegerField())


class RelationshipSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = RelationshipQuerySerializer()
    edge = EdgeSerializer()
    definition = serializers.CharField(allow_blank=True)
    nodes = EntityNodeSerializer(many=True)
    by_release = ReleaseCountsSerializer(many=True)
    releases = ReleaseEntrySerializer(many=True)


class EvidenceQuerySerializer(serializers.Serializer):
    relationship_ref = serializers.CharField()
    outcome = serializers.ListField(child=serializers.CharField(), allow_null=True)
    evidence_basis = serializers.ListField(
        child=serializers.CharField(), allow_null=True
    )
    effect = serializers.ListField(child=serializers.CharField(), allow_null=True)
    method = serializers.ListField(child=serializers.CharField(), allow_null=True)
    level = serializers.ListField(child=serializers.CharField(), allow_null=True)
    score = serializers.ListField(child=serializers.CharField(), allow_null=True)
    context = serializers.DictField(child=serializers.CharField())
    after = serializers.CharField(allow_null=True)
    page_size = serializers.IntegerField(allow_null=True)
    releases = serializers.ListField(child=serializers.CharField(), allow_null=True)


class TermRefSerializer(serializers.Serializer):
    curie = serializers.CharField()
    # Null when the caller may not read the release that stated it.
    label = serializers.CharField(allow_null=True)


class SourceRecordSerializer(serializers.Serializer):
    key = serializers.CharField()
    line_number = serializers.IntegerField()
    # The verbatim record, or null when the caller may not read it or the
    # release stores none.
    raw = serializers.JSONField(allow_null=True)


class MethodSerializer(serializers.Serializer):
    role = serializers.CharField()
    curie = serializers.CharField()
    label = serializers.CharField(allow_null=True)


class ScoreSerializer(serializers.Serializer):
    type = serializers.CharField()
    label = serializers.CharField()
    # Null when the raw value gives no finite number.
    value = serializers.FloatField(allow_null=True)
    raw_value = serializers.CharField()
    value_kind = serializers.CharField()
    direction = serializers.CharField()
    unit = serializers.CharField(allow_null=True)


class ContextSerializer(serializers.Serializer):
    facet = serializers.CharField()
    term = TermRefSerializer(allow_null=True)
    taxon = serializers.IntegerField(allow_null=True)
    label = serializers.CharField(allow_blank=True)
    raw_value = serializers.CharField()


class ParticipantSerializer(serializers.Serializer):
    side = serializers.CharField()
    entity = serializers.CharField()
    # The isoform the source named, when the caller may see it.
    stated_entity = serializers.CharField(allow_null=True)
    identifier_as_stated = serializers.CharField()
    resolution_basis = serializers.CharField()


class PublicationSerializer(serializers.Serializer):
    pmid = serializers.CharField(allow_null=True)
    doi = serializers.CharField(allow_null=True)
    pmcid = serializers.CharField(allow_null=True)
    imex_id = serializers.CharField(allow_null=True)
    local_key = serializers.CharField(allow_null=True)
    title = serializers.CharField(allow_blank=True)
    authors = serializers.CharField(allow_blank=True)
    journal = serializers.CharField(allow_blank=True)
    year = serializers.IntegerField(allow_null=True)
    url = serializers.CharField(allow_null=True)
    is_synthetic = serializers.BooleanField()


class EvidencePublicationSerializer(serializers.Serializer):
    role = serializers.CharField()
    publication = PublicationSerializer()


class EvidenceRowSerializer(serializers.Serializer):
    """One assertion with its provenance (architecture §7.3), built by
    shapes.evidence_rows."""

    id = serializers.CharField()
    release = serializers.CharField()
    # Null for a row with no stored record, which no row has yet.
    record = SourceRecordSerializer(allow_null=True)
    outcome = serializers.CharField()
    evidence_basis = serializers.CharField()
    assertion_method = serializers.CharField()
    eco_term = TermRefSerializer(allow_null=True)
    # Null when the source states none.
    effect = serializers.CharField(allow_null=True)
    effect_as_stated = serializers.CharField(allow_blank=True)
    genetic_sign = serializers.CharField(allow_null=True)
    interaction_type = TermRefSerializer(allow_null=True)
    # Null outside the physical family.
    interaction_level = serializers.CharField(allow_null=True)
    relationship_as_stated = serializers.CharField(allow_blank=True)
    methods = MethodSerializer(many=True)
    scores = ScoreSerializer(many=True)
    contexts = ContextSerializer(many=True)
    participants = ParticipantSerializer(many=True)
    qualifiers = serializers.JSONField()
    evidence_group = serializers.CharField(allow_blank=True)
    publications = EvidencePublicationSerializer(many=True)


class EvidencePageSerializer(serializers.Serializer):
    page_size = serializers.IntegerField()
    # The ref of the page's last row when more rows follow.
    next = serializers.CharField(allow_null=True)


class EvidenceSerializer(serializers.Serializer):
    api_version = serializers.CharField()
    query = EvidenceQuerySerializer()
    evidence = EvidenceRowSerializer(many=True)
    releases = ReleaseEntrySerializer(many=True)
    page = EvidencePageSerializer()
