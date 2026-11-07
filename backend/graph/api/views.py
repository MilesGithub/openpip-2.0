"""The routes of /api/graph/v1/ (architecture §7.2). Each subclasses
TypedGraphView, and each answers through its serializer_class.
"""

from dataclasses import asdict
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.db.models import Q
from django.http import Http404, HttpResponsePermanentRedirect
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from ..entity_visibility import (
    naming_releases,
    relationship_counts,
    visible_entity_q,
    visible_identifiers,
    visible_reference_relationships,
)
from ..models import (
    LEVEL_STRENGTH,
    AssertionMethod,
    ContextFacet,
    Effect,
    Entity,
    EntityStatus,
    EntityType,
    EvidenceBasis,
    GeneticSign,
    IdentifierNamespace,
    IdentifierRole,
    Outcome,
    RelationshipType,
    ScoreType,
    Taxon,
)
from ..preconditions import storable, typed_graph_enabled
from ..registry import REGISTRY_VERSION, registry_state
from ..resolution import resolve_many
from ..summary import EFFECT_KEYS, LEVEL_KEYS, MIXED, NOT_APPLICABLE, SIGN_KEYS
from .base import TypedGraphView
from .limits import (
    API_VERSION,
    MAX_RELEASES,
    Q_MAX_CHARS,
    RESOLVE_MAX_CANDIDATES,
    RESOLVE_MAX_TERMS,
)
from .params import Problem
from .refs import parse_ref, ref
from .serializers import (
    EntitySerializer,
    ParameterErrorSerializer,
    ResolveSerializer,
    StatusSerializer,
    VocabulariesSerializer,
)
from .shapes import entity_nodes, release_entries, release_refs
from .throttling import GraphResolveThrottle

# The route groups of architecture §7.2, each a key of status.features.
FEATURES = (
    "resolve",
    "entities",
    "network",
    "relationships",
    "browsers",
    "datasets",
    "summary",
    "exports",
)
# The groups this build serves.
SERVED_FEATURES = frozenset({"resolve", "entities"})
# The throttle scopes of served routes, the ones status publishes.
PUBLISHED_SCOPES = ("graph_read", "graph_resolve")
RELEASES_PARAMETER = OpenApiParameter(
    "releases",
    str,
    description=(
        "The releases to read, as comma-separated refs (release:3), replacing the "
        "default set. A ref the caller may not see is dropped without a word."
    ),
)


def _registry_version() -> dict:
    """The registry version in this code and the one in the database. While
    the switch is off no table is read, so the database's is null."""
    database = registry_state().database if typed_graph_enabled() else None
    return {"code": REGISTRY_VERSION, "database": database}


class StatusView(TypedGraphView):
    """Whether the typed graph is on, and what this version serves.

    The one route that answers while the switch is off. It reads no token, so
    a stale one never turns it into a 401 and a token refresh on every page
    load. It is counted per address at the `user` rate alone, so a page load
    spends neither `graph_read` nor the address's `anon` allowance.
    """

    answers_while_off = True
    authentication_classes = []
    throttle_classes = [UserRateThrottle]
    serializer_class = StatusSerializer

    @extend_schema(responses={200: StatusSerializer, 400: ParameterErrorSerializer})
    def get(self, request):
        self.params.raise_problems()
        on = typed_graph_enabled()
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        data = {
            "typed_graph": on,
            "api_version": API_VERSION,
            "registry_version": _registry_version(),
            "features": {key: on and key in SERVED_FEATURES for key in FEATURES},
            "limits": {
                "q_max_chars": Q_MAX_CHARS,
                "resolve_max_terms": RESOLVE_MAX_TERMS,
                "resolve_max_candidates": RESOLVE_MAX_CANDIDATES,
                "max_releases": MAX_RELEASES,
                "throttles": {scope: rates[scope] for scope in PUBLISHED_SCOPES},
            },
        }
        return Response(self.serializer_class(instance=data).data)


def _closed_set(values) -> list[dict]:
    """A closed set's entries, in the order given. No written definition
    exists in code, so the label is the value in words."""
    return [{"value": value, "label": value.replace("_", " ")} for value in values]


def _summary_values(values, stored) -> list[dict]:
    """A closed set that summaries extend. `stored` names the values the
    database holds, so an edge summary never emits a value the vocabulary
    lacks."""
    return [
        {**entry, "stored": entry["value"] in stored} for entry in _closed_set(values)
    ]


def _slugs(rows) -> list[str]:
    return sorted(row.slug for row in rows.all())


def _taxa() -> list[dict]:
    return [
        {
            "taxid": taxon.taxid,
            "scientific_name": taxon.scientific_name,
            "common_name": taxon.common_name or None,
            "rank": taxon.rank or None,
            "parent": taxon.parent_id,
            "merged_into": taxon.merged_into_id,
        }
        for taxon in Taxon.objects.order_by("taxid")
    ]


def _entity_types() -> list[dict]:
    return [
        {
            "slug": entity_type.slug,
            "label": entity_type.label,
            "definition": entity_type.definition,
            "taxon_policy": entity_type.taxon_policy,
            "identity_scheme": entity_type.identity_scheme,
            "assembly_required": entity_type.assembly_required,
            "display_token": entity_type.display_token or None,
        }
        for entity_type in EntityType.objects.order_by("slug")
    ]


def _namespaces() -> list[dict]:
    rows = IdentifierNamespace.objects.prefetch_related("allowed_entity_types")
    return [
        {
            "slug": namespace.slug,
            "label": namespace.label,
            "curie_prefix": namespace.curie_prefix,
            "allowed_entity_types": _slugs(namespace.allowed_entity_types),
            "scope": namespace.scope,
            "fixed_taxon": namespace.fixed_taxon_id,
            "case_policy": namespace.case_policy,
            "version_policy": namespace.version_policy,
            "defines_identity": namespace.defines_identity,
            "pattern": namespace.pattern,
            "url_template": namespace.url_template,
            "authority": namespace.authority,
        }
        for namespace in rows.order_by("slug")
    ]


def _relationship_types() -> list[dict]:
    rows = RelationshipType.objects.prefetch_related(
        "allowed_subject_types", "allowed_object_types"
    )
    return [
        {
            "slug": row.slug,
            "label": row.label,
            "definition": row.definition,
            "family": row.family,
            "orientation": row.orientation,
            "polarity": row.polarity,
            "allowed_subject_types": _slugs(row.allowed_subject_types),
            "allowed_object_types": _slugs(row.allowed_object_types),
            "subject_role_label": row.subject_role_label,
            "object_role_label": row.object_role_label,
            "self_pairs_allowed": row.self_pairs_allowed,
            "psi_mi_anchors": row.psi_mi_anchors,
            "display_token": row.display_token or None,
            "is_reference": row.is_reference,
        }
        for row in rows.order_by("slug")
    ]


def _score_types() -> list[dict]:
    return [
        {
            "slug": score_type.slug,
            "label": score_type.label,
            "definition": score_type.definition,
            "value_kind": score_type.value_kind,
            "direction": score_type.direction,
            "range_min": score_type.range_min,
            "range_max": score_type.range_max,
            "unit": score_type.unit or None,
            "null_model": score_type.null_model or None,
            "defining_document": score_type.defining_document,
            "comparable_across_datasets": score_type.comparable_across_datasets,
        }
        for score_type in ScoreType.objects.order_by("slug")
    ]


class VocabulariesView(TypedGraphView):
    """The registries as the tables hold them, and the closed sets.

    A list of rows is sorted by its key; a closed set keeps its declared
    order, strongest first for interaction levels. Terms are left out: they
    are not registries, and their labels are release-gated. A namespace's
    url_template is the registry's text: clients use an identifier's own url,
    and never build a link from a template.
    """

    serializer_class = VocabulariesSerializer

    @extend_schema(
        responses={200: VocabulariesSerializer, 400: ParameterErrorSerializer}
    )
    def get(self, request):
        self.params.raise_problems()
        data = {
            "api_version": API_VERSION,
            "registry_version": _registry_version(),
            "taxa": _taxa(),
            "entity_types": _entity_types(),
            "namespaces": _namespaces(),
            "relationship_types": _relationship_types(),
            "score_types": _score_types(),
            "outcomes": _closed_set(Outcome.values),
            "effects": _summary_values(
                (*EFFECT_KEYS, MIXED, NOT_APPLICABLE),
                stored=set(Effect.values) - {NOT_APPLICABLE},
            ),
            "genetic_signs": _summary_values(
                (*SIGN_KEYS, MIXED, NOT_APPLICABLE), stored=set(GeneticSign.values)
            ),
            "evidence_bases": _closed_set(EvidenceBasis.values),
            "assertion_methods": _closed_set(AssertionMethod.values),
            "context_facets": _closed_set(ContextFacet.values),
            "interaction_levels": [
                {**entry, "strength": LEVEL_STRENGTH[entry["value"]]}
                for entry in _closed_set(LEVEL_KEYS)
            ],
            "identifier_roles": _closed_set(IdentifierRole.values),
        }
        return Response(self.serializer_class(instance=data).data)


def _pinned_refs(named) -> list[str] | None:
    """The releases a request named, as its query echoes them: sorted by id,
    and never the subset the service kept. A handler passes params.releases(),
    not self.pinned, so the echo stays the caller's own words whatever
    selection() pins."""
    return None if named is None else [ref("release", pk) for pk in sorted(named)]


def _namespace_slugs() -> list[str]:
    return sorted(IdentifierNamespace.objects.values_list("slug", flat=True))


def _taxids():
    return Taxon.objects.values_list("taxid", flat=True)


def _entity_type_slugs() -> list[str]:
    return sorted(EntityType.objects.values_list("slug", flat=True))


def _resolve_result(term, answer, nodes, named, entries) -> dict:
    """What a term's answer shows: the candidates up to the cap, under the
    resolver's status, which counted them all."""
    return {
        "term": term,
        "status": answer.status,
        "truncated": len(answer.candidates) > RESOLVE_MAX_CANDIDATES,
        "candidates": [
            {
                "entity": nodes[candidate.entity.pk],
                "exact_primary": candidate.exact_primary,
                "matches": [asdict(match) for match in candidate.matches],
                "releases": release_refs(named[candidate.entity.pk], entries),
            }
            for candidate in answer.candidates[:RESOLVE_MAX_CANDIDATES]
        ],
    }


class ResolveView(TypedGraphView):
    """Which entities each term names, of those the caller may see.

    A term resolves once however often it is given, since resolving costs CPU
    per term and matching entity, and every copy repeats its result. A status
    counts every visible match, before the candidates are cut to the cap; a
    cut shows in the term, which says which, and in the page and warnings. The
    top-level releases are those a candidate shown names, each with its
    attribution and licence summary.
    """

    allowed_params = ("q", "namespace", "taxon", "entity_type", "releases")
    throttle_classes = [GraphResolveThrottle, AnonRateThrottle, UserRateThrottle]
    serializer_class = ResolveSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "q",
                str,
                many=True,
                required=True,
                description=(
                    "The terms, split on commas, tabs and new lines; repeatable. "
                    f"At most {RESOLVE_MAX_TERMS} terms of {Q_MAX_CHARS} characters."
                ),
            ),
            OpenApiParameter("namespace", str, description="A namespace slug."),
            OpenApiParameter("taxon", int, description="A taxid."),
            OpenApiParameter("entity_type", str, description="An entity type slug."),
            RELEASES_PARAMETER,
        ],
        responses={200: ResolveSerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request):
        terms = self.params.terms("q")
        namespace = self.params.choice("namespace", _namespace_slugs)
        taxon = self.params.taxon(_taxids)
        entity_type = self.params.choice("entity_type", _entity_type_slugs)
        self.selection()
        self.params.raise_problems()
        releases = self.release_set()
        labels = self.label_set()
        distinct = list(dict.fromkeys(terms))
        answers = resolve_many(
            distinct,
            namespace=namespace,
            taxon=taxon,
            entity_type=entity_type,
            limit=RESOLVE_MAX_CANDIDATES + 1,
            releases=releases,
            label_releases=labels,
        )
        entities = {
            candidate.entity.pk: candidate.entity
            for answer in answers
            for candidate in answer.candidates[:RESOLVE_MAX_CANDIDATES]
        }
        nodes = entity_nodes(entities.values(), labels)
        named = naming_releases(entities.values(), releases)
        entries = release_entries(set().union(*named.values()), releases)
        results = {
            term: _resolve_result(term, answer, nodes, named, entries)
            for term, answer in zip(distinct, answers)
        }
        cut = [term for term in distinct if results[term]["truncated"]]
        data = {
            "api_version": API_VERSION,
            "query": {
                "q": terms,
                "namespace": namespace,
                "taxon": taxon,
                "entity_type": entity_type,
                "releases": _pinned_refs(self.params.releases()),
            },
            "results": [results[term] for term in terms],
            "releases": entries,
            "page": {
                "truncated": bool(cut),
                "max_candidates": RESOLVE_MAX_CANDIDATES,
                "omitted": {"terms": len(cut)} if cut else {},
            },
            "warnings": [
                {
                    "code": "candidates_truncated",
                    "detail": (
                        f"{term}: more than {RESOLVE_MAX_CANDIDATES} entities match, "
                        f"{RESOLVE_MAX_CANDIDATES} shown."
                    ),
                }
                for term in cut
            ],
        }
        return Response(self.serializer_class(instance=data).data)


def _identifier_url(identifier) -> str | None:
    """Where the identifier's namespace says it lives, when that is an http(s)
    URL with a host. The registry checks only that a template holds {lookup},
    so what is tested is the string built. A colon stays, for HGNC's
    #!/hgnc_id/HGNC:5; the rest of the value is percent-encoded."""
    template = identifier.namespace.url_template
    if not template:
        return None
    url = template.replace("{lookup}", quote(identifier.lookup, safe=":"))
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme in ("http", "https") and parts.hostname else None


def _identifier(identifier) -> dict:
    return {
        "namespace": identifier.namespace_id,
        "value": identifier.value,
        "version": identifier.version or None,
        "role": identifier.role,
        "url": _identifier_url(identifier),
    }


def _reference_relationship(edge, entries) -> dict:
    """An edge as architecture §7.3 shapes one: `source` and `target` are the
    stored subject and object, since reference types are role-ordered."""
    listed = release_refs(edge.release_ids, entries)
    return {
        "id": ref("relationship", edge.relationship_id),
        "type": edge.relationship_type,
        "family": edge.family,
        "orientation": edge.orientation,
        "source": ref("entity", edge.subject_id),
        "target": ref("entity", edge.object_id),
        "releases": listed,
        "is_synthetic": any(
            entry["is_synthetic"] for entry in entries if entry["id"] in listed
        ),
    }


def _creator_ref(entity, entries) -> str | None:
    """The release that created the entity, when the response lists it, so a
    visible entity never names the embargoed or superseded release that
    created it."""
    creator = ref("release", entity.created_by_release_id)
    return creator if any(entry["id"] == creator for entry in entries) else None


def _survivor_redirect(request, entity):
    """A merged entity's redirect, keeping the request's query string, which
    has passed validation, so a pinned selection survives it. Empty, and no
    DRF Response, which would render a page under ?format=api."""
    location = reverse(
        "graph_api:entity",
        kwargs={"entity_id": ref("entity", entity.merged_into_id)},
    )
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"{location}?{query}" if query else location)


class EntityView(TypedGraphView):
    """One entity, as the caller may see it.

    One query decides every case, so a nonexistent entity, an invisible one, a
    merged one the caller may not see and a merged one whose survivor the
    caller may not see answer the same 404 from the same number of queries.
    Every other merged row redirects to its survivor.

    A label and a description show only to a caller who may read the release
    that wrote them, and a release is named only when the response lists it,
    its status read again. The number of queries does not depend on the
    entity's degree, nor on a reference relationship the caller may not see.
    """

    allowed_params = ("releases",)
    serializer_class = EntitySerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "entity_id",
                str,
                OpenApiParameter.PATH,
                description="The entity's ref, such as entity:12.",
            ),
            RELEASES_PARAMETER,
        ],
        responses={200: EntitySerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request, entity_id):
        pk = parse_ref(entity_id, "entity")
        if pk is None:
            self.params.add(
                Problem(
                    "entity_id", entity_id, detail="entity_id looks like entity:12."
                )
            )
        self.selection()
        self.params.raise_problems()
        releases = self.release_set()
        labels = self.label_set()
        row = (
            Entity.objects.filter(pk__in=storable([pk]))
            .filter(visible_entity_q(releases))
            .filter(
                ~Q(status=EntityStatus.MERGED)
                | visible_entity_q(releases, path="merged_into__")
            )
            .first()
        )
        if row is None:
            raise Http404
        if row.status == EntityStatus.MERGED:
            return _survivor_redirect(request, row)
        identifiers = visible_identifiers(row, releases)
        edges = visible_reference_relationships(row, releases)
        endpoints = {edge.subject_id for edge in edges}
        endpoints |= {edge.object_id for edge in edges}
        others = Entity.objects.filter(pk__in=endpoints - {row.pk})
        nodes = entity_nodes([row, *others], labels)
        counts = relationship_counts(row, releases)
        named = naming_releases([row], releases)[row.pk]
        entries = release_entries(
            named.union(*(edge.release_ids for edge in edges)), releases
        )
        data = {
            "api_version": API_VERSION,
            "query": {
                "entity_id": entity_id,
                "releases": _pinned_refs(self.params.releases()),
            },
            "entity": nodes[row.pk],
            "description": nodes[row.pk]["description"],
            "created_by_release": _creator_ref(row, entries),
            "identifiers": [_identifier(identifier) for identifier in identifiers],
            "reference_relationships": [
                _reference_relationship(edge, entries) for edge in edges
            ],
            "nodes": [nodes[pk] for pk in sorted(endpoints)],
            "relationship_counts": counts,
            "releases": entries,
        }
        return Response(self.serializer_class(instance=data).data)
