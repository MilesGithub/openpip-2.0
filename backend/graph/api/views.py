"""The routes of /api/graph/v1/ (architecture §7.2). Each subclasses
TypedGraphView, and each answers through its serializer_class.
"""

from dataclasses import asdict, replace
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.db.models import Count, Exists, F, Func, IntegerField, OuterRef, Q
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
    Relationship,
    RelationshipType,
    ScoreType,
    SourceRecord,
    Taxon,
)
from ..neighbourhood import DIRECTIONS, neighbourhood, timed
from ..preconditions import storable, typed_graph_enabled
from ..registry import REGISTRY_VERSION, registry_state
from ..resolution import resolve_many
from ..summary import (
    ALL_OUTCOMES,
    DEFAULT_FILTER,
    EFFECT_KEYS,
    LEVEL_KEYS,
    MIXED,
    NOT_APPLICABLE,
    SIGN_KEYS,
    EvidenceFilter,
    covered_assertions,
    evidence,
    summarize,
)
from ..visibility import is_staff_caller
from .base import TypedGraphView
from .limits import (
    API_VERSION,
    CANDIDATES_PER_SEED,
    DEFAULT_MAX_EDGES,
    EVIDENCE_MAX_PAGE_SIZE,
    EVIDENCE_PAGE_RAW_CHARS,
    EVIDENCE_PAGE_SIZE,
    MAX_CONTEXT_FACETS,
    MAX_EDGES,
    MAX_LAYERS,
    MAX_NODES,
    MAX_RELEASES,
    MAX_SEEDS,
    Q_MAX_CHARS,
    RESOLVE_MAX_CANDIDATES,
    RESOLVE_MAX_TERMS,
)
from .params import CONTEXT_NAMES, Problem
from .refs import parse_ref, ref
from .serializers import (
    EntitySerializer,
    EvidenceSerializer,
    NetworkSerializer,
    ParameterErrorSerializer,
    RelationshipSerializer,
    ResolveSerializer,
    StatusSerializer,
    VocabulariesSerializer,
)
from .shapes import (
    edge,
    entity_nodes,
    evidence_rows,
    readable_raw,
    release_entries,
    release_refs,
)
from .throttling import (
    GraphNetworkBudgetThrottle,
    GraphNetworkThrottle,
    GraphResolveThrottle,
)

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
SERVED_FEATURES = frozenset(
    {"resolve", "entities", "network", "relationships", "browsers"}
)
# The throttle scopes of served routes, the ones status publishes.
PUBLISHED_SCOPES = (
    "graph_read",
    "graph_resolve",
    "graph_network",
    "graph_network_site",
)
# The evidence-level filters the network and the relationship routes share,
# in documentation order.
FILTER_PARAMS = (
    "outcome",
    "evidence_basis",
    "effect",
    "method",
    "level",
    "score",
    *CONTEXT_NAMES,
)
RELEASES_PARAMETER = OpenApiParameter(
    "releases",
    str,
    description=(
        "The releases to read, as comma-separated refs (release:3), replacing the "
        "default set. A ref the caller may not see is dropped without a word."
    ),
)
BROWSER_PARAMETER = OpenApiParameter(
    "browser",
    str,
    description=(
        "A browser instance's slug: read only its releases. Not with releases."
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
                "max_seeds": MAX_SEEDS,
                "max_layers": MAX_LAYERS,
                "max_context_facets": MAX_CONTEXT_FACETS,
                "default_max_edges": DEFAULT_MAX_EDGES,
                "max_edges": MAX_EDGES,
                "candidates_per_seed": CANDIDATES_PER_SEED,
                "max_nodes": MAX_NODES,
                "evidence_page_size": EVIDENCE_PAGE_SIZE,
                "evidence_max_page_size": EVIDENCE_MAX_PAGE_SIZE,
                "evidence_page_raw_chars": EVIDENCE_PAGE_RAW_CHARS,
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

    allowed_params = ("q", "namespace", "taxon", "entity_type", "releases", "browser")
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
            BROWSER_PARAMETER,
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
                "browser": self.browser,
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

    allowed_params = ("releases", "browser")
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
            BROWSER_PARAMETER,
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
        summaries = visible_reference_relationships(row, releases)
        endpoints = {summary.subject_id for summary in summaries}
        endpoints |= {summary.object_id for summary in summaries}
        others = Entity.objects.filter(pk__in=endpoints - {row.pk})
        nodes = entity_nodes([row, *others], labels)
        counts = relationship_counts(row, releases)
        named = naming_releases([row], releases)[row.pk]
        entries = release_entries(
            named.union(*(summary.release_ids for summary in summaries)), releases
        )
        data = {
            "api_version": API_VERSION,
            "query": {
                "entity_id": entity_id,
                "releases": _pinned_refs(self.params.releases()),
                "browser": self.browser,
            },
            "entity": nodes[row.pk],
            "description": nodes[row.pk]["description"],
            "created_by_release": _creator_ref(row, entries),
            "identifiers": [_identifier(identifier) for identifier in identifiers],
            "reference_relationships": [
                edge(summary, entries) for summary in summaries
            ],
            "nodes": [nodes[pk] for pk in sorted(endpoints)],
            "relationship_counts": counts,
            "releases": entries,
        }
        return Response(self.serializer_class(instance=data).data)


# ── Network ─────────────────────────────────────────────────────────────────


def _score_type_slugs() -> list[str]:
    return sorted(ScoreType.objects.values_list("slug", flat=True))


def _context_description(name) -> str:
    facet = name.removeprefix("context.").replace("_", " ")
    if name == "context.host_taxon":
        value = "a registered taxid"
    else:
        value = (
            "a PSI-MI or ECO CURIE, which matches the term, or any other text, "
            "which matches the value as written"
        )
    return (
        f"A stated {facet} context: {value}. At most {MAX_CONTEXT_FACETS} "
        "context facets per request."
    )


def _many(name, description) -> OpenApiParameter:
    return OpenApiParameter(name, str, many=True, description=description)


# The evidence-level filters, declared once for the routes that share them.
FILTER_PARAMETERS = [
    _many(
        "outcome",
        "The outcomes whose evidence covers an edge, comma-separated, or all; "
        "supported unless given.",
    ),
    _many("evidence_basis", "Evidence bases, comma-separated."),
    _many(
        "effect",
        "Effects, comma-separated: activating, inhibiting, unknown or not_stated.",
    ),
    _many("method", "Detection methods, as comma-separated PSI-MI CURIEs."),
    _many(
        "level",
        "Interaction levels, comma-separated; they apply to the physical family.",
    ),
    _many(
        "score",
        "score_type:min:max, an empty bound being open; repeatable, once per "
        "score type.",
    ),
    *(
        OpenApiParameter(name, str, description=_context_description(name))
        for name in CONTEXT_NAMES
    ),
]


def _listed(pieces) -> list | None:
    return None if pieces is None else list(pieces)


def _frozen(pieces) -> frozenset | None:
    return None if pieces is None else frozenset(pieces)


def _evidence_filter(values, *, outcomes) -> EvidenceFilter:
    """The filter of validated values. `outcomes` is the route's default,
    which `outcome` replaces, `all` meaning every outcome; an absent list
    leaves its filter out."""
    outcome = values["outcome"]
    if outcome is not None:
        outcomes = ALL_OUTCOMES if "all" in outcome else frozenset(outcome)
    return EvidenceFilter(
        outcomes=outcomes,
        evidence_bases=_frozen(values["evidence_basis"]),
        effects=_frozen(values["effect"]),
        detection_methods=_frozen(values["method"]),
        levels=_frozen(values["level"]),
        scores=values["score"] or (),
        contexts=values["context"] or (),
    )


class EvidenceFilters:
    """What the routes that take the network's evidence-level filters share:
    the filters read in documentation order and echoed as given, and the
    taxa read once per request, so `taxon` and `context.host_taxon` cost one
    query together. A parameter not given reads nothing, as the collectors
    promise. A mixin, so every route stays a direct subclass of
    TypedGraphView, which the schema test lists."""

    _taxa = None

    def _taxids(self) -> list[int]:
        if self._taxa is None:
            self._taxa = sorted(_taxids())
        return self._taxa

    def _filter_values(self) -> dict:
        """The filter parameters as read, by name, each None when absent; the
        collectors record every problem."""
        params = self.params
        outcome = params.choices(
            "outcome", lambda: [*Outcome.values, "all"], what="outcome"
        )
        if outcome and "all" in outcome and len(outcome) > 1:
            params.add(Problem("outcome", "all", detail="outcome: all stands alone."))
        return {
            "outcome": outcome,
            "evidence_basis": params.choices(
                "evidence_basis", lambda: list(EvidenceBasis.values), what="basis"
            ),
            "effect": params.choices(
                "effect", lambda: list(EFFECT_KEYS), what="effect"
            ),
            "method": params.methods(),
            "level": params.choices("level", lambda: list(LEVEL_KEYS), what="level"),
            "score": params.scores(_score_type_slugs),
            "context": params.contexts(self._taxids),
        }

    def _filter_echo(self, values) -> dict:
        """The filters as `query` echoes them: each list distinct and in order
        of first appearance, `method` normalized, `score` as given, `context`
        {facet: value} of the facets given."""
        query = self.params.query
        return {
            "outcome": _listed(values["outcome"]),
            "evidence_basis": _listed(values["evidence_basis"]),
            "effect": _listed(values["effect"]),
            "method": _listed(values["method"]),
            "level": _listed(values["level"]),
            "score": query.getlist("score") if "score" in query else None,
            "context": {
                name.removeprefix("context."): query.get(name)
                for name in CONTEXT_NAMES
                if name in query
            },
        }


def _network_page(found, max_edges) -> dict:
    """What was cut: `candidates` counts the lookups the pre-limit cut (one per
    truncated seed, one for the partners), `edges` the ranked candidates a cap
    refused; `hidden` the relationships of the seeds, per outcome, whose
    evidence passes every other filter and none of the requested outcomes,
    exact over every candidate of the request."""
    omitted = {}
    candidates = len(found.truncated_seeds)
    if found.partner_candidates_truncated:
        candidates += 1
    if candidates:
        omitted["candidates"] = candidates
    if found.omitted_edges:
        omitted["edges"] = found.omitted_edges
    return {
        "truncated": bool(omitted),
        "max_edges": max_edges,
        "omitted": omitted,
        "hidden": found.hidden,
    }


def _network_warnings(found, max_edges) -> list[dict]:
    """In order: each truncated seed, the partner lookup, the edge cap, the
    node cap, then each outcome that hidden relationships have. The two caps'
    counts sum to page.omitted.edges."""
    ranked = f"the {CANDIDATES_PER_SEED} most covered were ranked."
    warnings = [
        {
            "code": "seed_truncated",
            "detail": (
                f"{ref('entity', pk)}: more than {CANDIDATES_PER_SEED} relationships "
                f"have covered evidence; {ranked}"
            ),
        }
        for pk in found.truncated_seeds
    ]
    if found.partner_candidates_truncated:
        warnings.append(
            {
                "code": "partner_edges_truncated",
                "detail": (
                    f"More than {CANDIDATES_PER_SEED} relationships among the "
                    f"partners have covered evidence; {ranked}"
                ),
            }
        )
    for_edges = found.omitted_edges - found.omitted_for_nodes
    if for_edges:
        warnings.append(
            {
                "code": "edges_truncated",
                "detail": f"{for_edges} ranked edges omitted: max_edges is {max_edges}.",
            }
        )
    if found.omitted_for_nodes:
        warnings.append(
            {
                "code": "nodes_truncated",
                "detail": (
                    f"{found.omitted_for_nodes} ranked edges omitted: a network holds "
                    f"at most {MAX_NODES} nodes; edges between the shown nodes were "
                    "kept."
                ),
            }
        )
    for outcome in Outcome.values:
        count = found.hidden[outcome]
        if count:
            warnings.append(
                {
                    "code": f"{outcome}_hidden",
                    "detail": (
                        f"{count} hidden relationships have "
                        f"{outcome.replace('_', '-')} evidence; outcome={outcome} or "
                        "outcome=all shows them."
                    ),
                }
            )
    return warnings


class NetworkView(EvidenceFilters, TypedGraphView):
    """The network around up to ten seeds, as the caller may see it.

    Each visible seed's relationships of the layers, in the direction asked,
    with evidence the filters cover are ranked by how many assertions cover
    them, the most covered first and ties by ascending id; the caps keep the
    first `max_edges` of them within the node cap, and `page` and `warnings`
    say what was cut. A relationship whose evidence passes the
    filters but has none of the requested outcomes is hidden, and counted per
    outcome in `page.hidden`. A seed the caller may not see is dropped without
    a word, as a release ref is, so a hidden seed reads like one that names
    nothing.

    The site-wide budget is spent by the handler after validation, so a
    malformed request, which reads no data, spends none, and a request that
    reaches the computation spends it whether it answers 200 or 503. Every
    statement runs under the statement timeout and the request deadline, and
    a request past either answers 503.
    """

    allowed_params = (
        "seeds",
        "layers",
        "direction",
        *FILTER_PARAMS,
        "taxon",
        "include_partner_edges",
        "max_edges",
        "releases",
        "browser",
    )
    throttle_classes = [GraphNetworkThrottle, AnonRateThrottle, UserRateThrottle]
    serializer_class = NetworkSerializer
    _relationship_types = None

    def _layer_types(self) -> dict:
        """{slug: family} of every registered relationship type, read once:
        the layers are validated against it and expanded from it."""
        if self._relationship_types is None:
            self._relationship_types = dict(
                RelationshipType.objects.values_list("slug", "family")
            )
        return self._relationship_types

    def _layer_names(self) -> list[str]:
        """What `layers` may name: every type slug, then family:<name> for
        each family some registered type has."""
        types = self._layer_types()
        families = set(types.values())
        return sorted(types) + sorted(f"family:{family}" for family in families)

    def _layers_of(self, pieces) -> frozenset:
        """The type slugs the layers name, a family expanded to every
        registered type of it, reference types included when named."""
        types = self._layer_types()
        found = set()
        for piece in pieces:
            if piece.startswith("family:"):
                family = piece.removeprefix("family:")
                found |= {slug for slug, kind in types.items() if kind == family}
            else:
                found.add(piece)
        return frozenset(found)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "seeds",
                str,
                many=True,
                required=True,
                description=(
                    "The seeds, as comma-separated entity refs (entity:12); "
                    f"repeatable. At most {MAX_SEEDS} distinct entities; a ref the "
                    "caller may not see is dropped without a word."
                ),
            ),
            OpenApiParameter(
                "layers",
                str,
                many=True,
                description=(
                    "The relationship types to walk, as comma-separated type slugs "
                    f"or family:<name>; repeatable. At most {MAX_LAYERS}; every "
                    "non-reference type unless given."
                ),
            ),
            OpenApiParameter(
                "direction",
                str,
                description=(
                    "out, in or both (the default), from each seed; a symmetric "
                    "relationship matches either way."
                ),
            ),
            *FILTER_PARAMETERS,
            OpenApiParameter("taxon", int, description="A taxid: the partners' taxon."),
            OpenApiParameter(
                "include_partner_edges",
                bool,
                description="true lists the edges among the partners too.",
            ),
            OpenApiParameter(
                "max_edges",
                int,
                description=(
                    f"The most edges to list, from 1 to {MAX_EDGES}; "
                    f"{DEFAULT_MAX_EDGES} unless given."
                ),
            ),
            RELEASES_PARAMETER,
            BROWSER_PARAMETER,
        ],
        responses={200: NetworkSerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request):
        params = self.params
        seeds = params.refs("seeds", "entity", MAX_SEEDS)
        if seeds is None:
            params.add(Problem("seeds", detail="seeds names at least one entity."))
        layers = params.choices(
            "layers", self._layer_names, what="layer", limit=MAX_LAYERS
        )
        direction = params.choice("direction", lambda: list(DIRECTIONS))
        values = self._filter_values()
        taxon = params.taxon(self._taxids)
        include_partner_edges = params.flag("include_partner_edges")
        max_edges = params.integer("max_edges", floor=1, cap=MAX_EDGES)
        self.selection()
        params.raise_problems()
        # Spent here, after validation, and never by a request that reads no
        # data; it raises its own 429 when the budget is gone.
        GraphNetworkBudgetThrottle().allow_request(request, self)
        releases = self.release_set()
        labels = self.label_set()
        cap = DEFAULT_MAX_EDGES if max_edges is None else max_edges
        found = neighbourhood(
            releases=releases,
            seeds=seeds,
            layers=None if layers is None else self._layers_of(layers),
            direction="both" if direction is None else direction,
            filters=_evidence_filter(values, outcomes=DEFAULT_FILTER.outcomes),
            taxon=taxon,
            include_partner_edges=bool(include_partner_edges),
            max_edges=cap,
        )
        nodes = entity_nodes(Entity.objects.filter(pk__in=sorted(found.nodes)), labels)
        entries = release_entries(
            set().union(*(summary.release_ids for summary in found.edges)), releases
        )
        data = {
            "api_version": API_VERSION,
            "query": {
                "seeds": [ref("entity", pk) for pk in sorted(seeds)],
                "layers": _listed(layers),
                "direction": direction,
                **self._filter_echo(values),
                "taxon": taxon,
                "include_partner_edges": include_partner_edges,
                "max_edges": max_edges,
                "releases": _pinned_refs(params.releases()),
                "browser": self.browser,
            },
            "nodes": [
                {**nodes[pk], "is_seed": pk in found.seeds}
                for pk in sorted(found.nodes)
            ],
            "edges": [edge(summary, entries) for summary in found.edges],
            "releases": entries,
            "page": _network_page(found, cap),
            "warnings": _network_warnings(found, cap),
        }
        return Response(self.serializer_class(instance=data).data)


# ── Relationships ───────────────────────────────────────────────────────────


RELATIONSHIP_REF_PARAMETER = OpenApiParameter(
    "relationship_ref",
    str,
    OpenApiParameter.PATH,
    description="The relationship's ref, such as relationship:12.",
)


def _relationship_pk(params, relationship_ref) -> int | None:
    """The id the path names; a malformed ref is recorded before the route's
    parameters, as the entity page records its own."""
    pk = parse_ref(relationship_ref, "relationship")
    if pk is None:
        params.add(
            Problem(
                "relationship_ref",
                relationship_ref,
                detail="relationship_ref looks like relationship:12.",
            )
        )
    return pk


def _existing(pk, releases):
    """The relationship, with its type row, when it has a visible assertion;
    None otherwise. One query decides the nonexistent and the hidden case
    alike, and a ref beyond 64 bits sends none."""
    every = covered_assertions(
        releases=releases, filters=EvidenceFilter(outcomes=ALL_OUTCOMES)
    )
    return (
        Relationship.objects.filter(pk__in=storable([pk]))
        .filter(Exists(every.filter(relationship_id=OuterRef("pk"))))
        .select_related("relationship_type")
        .first()
    )


def _by_release(pk, releases, filters) -> dict:
    """{release id: {outcome: n}} of one relationship's visible assertions
    that pass every filter but the outcome filter, every outcome a key, from
    one aggregate."""
    filtered = covered_assertions(
        releases=releases, filters=replace(filters, outcomes=ALL_OUTCOMES)
    )
    rows = (
        filtered.filter(relationship_id=pk)
        .order_by()
        .values("release_id", "outcome")
        .annotate(n=Count("pk"))
    )
    counts = {}
    for row in rows:
        by_outcome = counts.setdefault(
            row["release_id"], {outcome: 0 for outcome in Outcome.values}
        )
        by_outcome[row["outcome"]] = row["n"]
    return counts


class RelationshipView(EvidenceFilters, TypedGraphView):
    """One relationship, as the caller may see it: its summary under the
    network's evidence-level filters, so the panel and the network agree,
    its type's definition, its endpoints, and its evidence counted per
    release.

    One query decides whether it exists for the caller, so a relationship
    with no visible assertion and a nonexistent id answer the same 404 from
    the same number of queries. One whose only visible evidence is
    tested-absent, inconclusive or not significant exists: its page answers
    with `shown` false under the default outcome. It is rationed per caller
    at the network's rate, since summarizing a relationship's evidence grows
    with the data, and spends no site-wide budget, being bounded by one
    relationship's rows.
    """

    allowed_params = (*FILTER_PARAMS, "releases", "browser")
    throttle_classes = [GraphNetworkThrottle, AnonRateThrottle, UserRateThrottle]
    serializer_class = RelationshipSerializer

    @extend_schema(
        parameters=[
            RELATIONSHIP_REF_PARAMETER,
            *FILTER_PARAMETERS,
            RELEASES_PARAMETER,
            BROWSER_PARAMETER,
        ],
        responses={200: RelationshipSerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request, relationship_ref):
        params = self.params
        pk = _relationship_pk(params, relationship_ref)
        values = self._filter_values()
        self.selection()
        params.raise_problems()
        releases = self.release_set()
        labels = self.label_set()
        filters = _evidence_filter(values, outcomes=DEFAULT_FILTER.outcomes)
        with timed(what="relationship page"):
            row = _existing(pk, releases)
            if row is None:
                raise Http404
            # Each statement reads the statuses afresh, so a release withdrawn
            # since the existence query may leave no visible assertion: the
            # relationship then reads as one that does not exist.
            summary = summarize([pk], releases=releases, filters=filters).get(pk)
            if summary is None:
                raise Http404
            counts = _by_release(pk, releases, filters)
        nodes = entity_nodes(
            Entity.objects.filter(pk__in={row.subject_id, row.object_id}), labels
        )
        entries = release_entries(set(counts), releases)
        listed = {entry["id"] for entry in entries}
        data = {
            "api_version": API_VERSION,
            "query": {
                "relationship_ref": relationship_ref,
                **self._filter_echo(values),
                "releases": _pinned_refs(params.releases()),
                "browser": self.browser,
            },
            "edge": edge(summary, entries),
            "definition": row.relationship_type.definition,
            "nodes": [nodes[node_id] for node_id in sorted(nodes)],
            "by_release": [
                {
                    "release": ref("release", release_id),
                    "outcome_counts": counts[release_id],
                }
                for release_id in sorted(counts)
                if ref("release", release_id) in listed
            ],
            "releases": entries,
        }
        return Response(self.serializer_class(instance=data).data)


class _RawChars(Func):
    """The characters of a record's raw values as the database counts them,
    so a page is cut before any raw value is read: an object's values as
    text, which is every column an adapter stores; a record that is no
    object counts its JSON text, and a null one nothing. Its argument is a
    column, which the template names three times."""

    output_field = IntegerField()
    template = (
        "COALESCE(CASE WHEN jsonb_typeof(%(expressions)s) = 'object' THEN "
        "(SELECT SUM(length(t.value)) FROM jsonb_each_text(%(expressions)s) AS t) "
        "ELSE length(%(expressions)s::text) END, 0)"
    )

    def as_sqlite(self, compiler, connection, **extra_context):
        return self.as_sql(
            compiler,
            connection,
            template=(
                "COALESCE(CASE WHEN json_type(%(expressions)s) = 'object' THEN "
                "(SELECT SUM(length(t.value)) FROM json_each(%(expressions)s) AS t) "
                "ELSE length(%(expressions)s) END, 0)"
            ),
            **extra_context,
        )


def _evidence_slice(pk, releases, filters, after, size) -> list:
    """The rows that decide a page: `size` + 1 of them after `after`, the
    extra one saying more follow, each record's raw value left unread and
    its characters counted as `raw_chars`. An `after` beyond any row's id
    gives none, with no statement."""
    if after is not None and not storable([after]):
        return []
    rows = evidence(pk, releases=releases, filters=filters)
    if after is not None:
        rows = rows.filter(pk__gt=after)
    rows = rows.defer("source_record__raw").annotate(
        raw_chars=_RawChars(F("source_record__raw"))
    )
    return list(rows[: size + 1])


def _within_raw_cap(rows, readable, more) -> tuple:
    """The rows up to and including the first that brings the characters of
    their readable raw records (`readable`, readable_raw's ids) past
    EVIDENCE_PAGE_RAW_CHARS, and whether a row of the slice follows the last
    one kept; a record the caller may not read counts nothing. A page of
    megabyte-scale records is bounded by characters as well as rows; a
    single row past the cap is still delivered, alone."""
    total = 0
    for index, row in enumerate(rows):
        if row.pk in readable:
            total += row.raw_chars
        if total > EVIDENCE_PAGE_RAW_CHARS and index < len(rows) - 1:
            return rows[: index + 1], True
    return rows, more


def _read_raw(rows, readable) -> None:
    """The raw values of the kept rows' readable records, read in one
    statement into the records the slice left them unread on, and none when
    no kept record is readable: a page reads no raw value it does not
    deliver. A record gone since the slice was read shows none."""
    wanted = {row.source_record_id for row in rows if row.pk in readable}
    if not wanted:
        return
    raws = dict(
        SourceRecord.objects.filter(pk__in=sorted(wanted)).values_list("pk", "raw")
    )
    for row in rows:
        if row.pk in readable:
            row.source_record.raw = raws.get(row.source_record_id)


class EvidenceView(EvidenceFilters, TypedGraphView):
    """The evidence of one relationship, row by row with its provenance, as
    the caller may see it (architecture §7.3), by keyset: `after` names the
    row the page starts after and `page_size` how many rows it holds, so a
    page reads the same however often it is asked for, ids only growing.
    Every outcome is listed unless `outcome` narrows it, so the evidence
    against an edge is read beside the evidence for it. A page also ends
    early once its readable raw records pass a character cap, `next` naming
    its last row. The 404 is the relationship page's.
    """

    allowed_params = (*FILTER_PARAMS, "after", "page_size", "releases", "browser")
    serializer_class = EvidenceSerializer

    @extend_schema(
        parameters=[
            RELATIONSHIP_REF_PARAMETER,
            *FILTER_PARAMETERS,
            OpenApiParameter(
                "after",
                str,
                description=(
                    "The ref of the row the page starts after (evidence:36): the "
                    "page holds the rows with a greater id."
                ),
            ),
            OpenApiParameter(
                "page_size",
                int,
                description=(
                    f"Rows per page, from 1 to {EVIDENCE_MAX_PAGE_SIZE}; "
                    f"{EVIDENCE_PAGE_SIZE} unless given."
                ),
            ),
            RELEASES_PARAMETER,
            BROWSER_PARAMETER,
        ],
        responses={200: EvidenceSerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request, relationship_ref):
        params = self.params
        pk = _relationship_pk(params, relationship_ref)
        values = self._filter_values()
        after = params.ref("after", "evidence")
        page_size = params.integer("page_size", floor=1, cap=EVIDENCE_MAX_PAGE_SIZE)
        self.selection()
        params.raise_problems()
        releases = self.release_set()
        labels = self.label_set()
        staff = is_staff_caller(request)
        size = EVIDENCE_PAGE_SIZE if page_size is None else page_size
        filters = _evidence_filter(values, outcomes=ALL_OUTCOMES)
        with timed(what="evidence page"):
            if _existing(pk, releases) is None:
                raise Http404
            listed = _evidence_slice(pk, releases, filters, after, size)
            more = len(listed) > size
            listed = listed[:size]
            read_to = listed[-1].pk if listed else None
            entries = release_entries({row.release_id for row in listed}, releases)
            shown = {entry["id"] for entry in entries}
            # A release withdrawn while the page is read is named nowhere, so
            # its rows, read before its status was, are left out.
            listed = [row for row in listed if ref("release", row.release_id) in shown]
            # The readable records' characters decide the cut, and only the
            # kept ones are read, so the cap bounds the work as well as the
            # response.
            readable = readable_raw(listed, entries, staff=staff)
            listed, more = _within_raw_cap(listed, readable, more)
            _read_raw(listed, readable)
        named = {ref("release", row.release_id) for row in listed}
        entries = [entry for entry in entries if entry["id"] in named]
        rows = evidence_rows(
            listed,
            entries,
            data_releases=releases,
            label_releases=labels,
            staff=staff,
        )
        if not more:
            following = None
        elif listed:
            following = ref("evidence", listed[-1].pk)
        else:
            # Every row read was of a release withdrawn meanwhile: the page
            # holds none, and names where it read to, so the walk goes on.
            following = ref("evidence", read_to)
        data = {
            "api_version": API_VERSION,
            "query": {
                "relationship_ref": relationship_ref,
                **self._filter_echo(values),
                "after": params.query.get("after") if "after" in params.query else None,
                "page_size": page_size,
                "releases": _pinned_refs(params.releases()),
                "browser": self.browser,
            },
            "evidence": rows,
            "releases": entries,
            "page": {"page_size": size, "next": following},
        }
        return Response(self.serializer_class(instance=data).data)
