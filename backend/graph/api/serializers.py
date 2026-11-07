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


class ReferenceRelationshipSerializer(serializers.Serializer):
    """The keys and types of an edge of architecture §7.3: v1 allows only
    additive change, so it has one edge shape."""

    id = serializers.CharField()
    type = serializers.CharField()
    family = serializers.CharField()
    orientation = serializers.CharField()
    source = serializers.CharField()
    target = serializers.CharField()
    releases = serializers.ListField(child=serializers.CharField())
    is_synthetic = serializers.BooleanField()


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
    reference_relationships = ReferenceRelationshipSerializer(many=True)
    nodes = EntityNodeSerializer(many=True)
    relationship_counts = serializers.DictField(child=serializers.IntegerField())
    releases = ReleaseEntrySerializer(many=True)
