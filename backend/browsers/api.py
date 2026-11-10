"""The browser instance routes of /api/graph/v1/ (architecture §7.2): the
gallery, browsers, and one instance, browsers/{slug}, which graph.api.urls
serves beside the other routes.

An instance's own visibility decides who sees it (browsers.selection): the
gallery lists the instances its caller may list, and an instance's page
answers for one its caller may reach, and otherwise exactly as for a slug no
instance has. What a response says of the selected releases comes from the
set visible_release_ids issues for the caller, pinned to the instances' own
selections, so a selected release the caller may not see gets no ref, role,
position, count or statistic: availability, the synthetic flag and the
derived taxa and relationship types read only the releases the caller may
see. An instance that selects nothing pins EMPTY_PIN, so a selection hidden
from the caller costs what an empty one does, and availability is the one
bit a hidden selection discloses.

The gallery reads every instance its caller may list in a fixed number of
statements, whatever their number or their releases, and filters them here:
a filter changes only whether the assays' labels are read, which follows the
instances listed. It names no release; an instance's page lists them, with
their licences.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Prefetch, prefetch_related_objects
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response

from graph.api.base import TypedGraphView
from graph.api.limits import API_VERSION, Q_MAX_CHARS
from graph.api.refs import ref
from graph.api.serializers import ParameterErrorSerializer
from graph.api.shapes import _publication, release_entries
from graph.models import DatasetRelease, RelationshipType, ReleaseTaxon, Taxon
from graph.summary import term_labels
from graph.visibility import Purpose, visible_release_ids

from .availability import Availability, compute
from .models import (
    BrowserInstance,
    BrowserInstancePublication,
    BrowserInstanceRelease,
    Kind,
    Status,
)
from .rules import ASSAY_ONTOLOGY, MAX_SLUG_CHARS, SLUG, TAGS
from .selection import EMPTY_PIN, listable, reachable
from .serializers import BrowserSerializer, BrowsersSerializer

# A paper's year, as graph.publications stores one: 1 to 32,767.
MAX_YEAR = 32_767

GALLERY_PARAMETERS = [
    OpenApiParameter(
        "q",
        str,
        description=(
            "Text the title, short title or summary holds, matched without regard "
            f"to case; at most {Q_MAX_CHARS} characters."
        ),
    ),
    OpenApiParameter(
        "tag", str, description="A tag the instance names, such as gene_regulation."
    ),
    OpenApiParameter(
        "relationship_type",
        str,
        description=(
            "A relationship type slug: one the instance states or, when it states "
            "none, a non-reference type the selected releases you may see hold."
        ),
    ),
    OpenApiParameter(
        "taxon",
        int,
        description=(
            "A taxid: the instance's primary taxon, one it states or, when it "
            "states none, one the selected releases you may see declare."
        ),
    ),
    OpenApiParameter(
        "assay",
        str,
        description="A PSI-MI CURIE the instance names as an assay, such as MI:0018.",
    ),
    OpenApiParameter(
        "year", int, description="The year of a paper of the instance's citation."
    ),
    OpenApiParameter(
        "status", str, description=f"The stored status: {', '.join(Status.values)}."
    ),
    OpenApiParameter(
        "availability",
        str,
        description=(
            f"What the instance can show you now: {', '.join(Availability.values)}."
        ),
    ),
]
SLUG_PARAMETER = OpenApiParameter(
    "slug",
    str,
    OpenApiParameter.PATH,
    description="The instance's slug, such as synthetic-regulation.",
)


@dataclass(frozen=True)
class Selected:
    """A release an instance selects, as its selection row reads it."""

    release_id: int
    role: str
    status: str
    is_synthetic: bool


@dataclass(frozen=True)
class Shown:
    """What an instance shows one caller. `selected` holds every release a
    typed instance selects, of both roles, in (order, release id) order, and
    `seen` the ids of those the caller may see, in the same order; another
    kind selects nothing. `taxa` and `relationship_types` are as stated, or
    derived when none is stated."""

    selected: tuple
    seen: tuple
    availability: str
    is_synthetic: bool
    taxa: list
    relationship_types: list


def _type_slugs() -> list[str]:
    return sorted(RelationshipType.objects.values_list("slug", flat=True))


def _taxids():
    return Taxon.objects.values_list("taxid", flat=True)


def _citations() -> Prefetch:
    """The header citation in its order, each paper read with its row."""
    return Prefetch(
        "citations",
        queryset=BrowserInstancePublication.objects.select_related(
            "publication"
        ).order_by("order", "publication_id"),
    )


def _selections(rows) -> dict:
    """{instance id: (Selected, ...)} of these instances, in (order, release
    id) order, from one statement, none for no instance. Only a typed
    instance selects releases (rules.validate_selection), so a row a bare
    save gave another kind is ignored."""
    typed = {row.pk for row in rows if row.kind == Kind.TYPED}
    found = {row.pk: [] for row in rows}
    selection = BrowserInstanceRelease.objects.filter(
        instance_id__in=list(found)
    ).order_by("order", "release_id")
    for instance_id, *fields in selection.values_list(
        "instance_id", "release_id", "role", "release__status", "release__is_synthetic"
    ):
        if instance_id in typed:
            found[instance_id].append(Selected(*fields))
    return {pk: tuple(chosen) for pk, chosen in found.items()}


def _visible(request, selections):
    """The selected releases the caller may see, from one statement: every
    selection pinned at once, which issues each instance what pinning its
    own would, since each release is judged on its own. EMPTY_PIN when none
    selects a release, so the statement is sent either way."""
    pinned = frozenset(
        item.release_id for chosen in selections.values() for item in chosen
    )
    return visible_release_ids(request, Purpose.VIEW, pinned=pinned or EMPTY_PIN)


def _declared_taxa(ids) -> dict:
    """{release id: the taxids it declares} of these releases, from one
    statement; none for no id."""
    declared = defaultdict(set)
    if ids:
        rows = ReleaseTaxon.objects.filter(release_id__in=sorted(ids))
        for release_id, taxid in rows.values_list("release_id", "taxon_id"):
            declared[release_id].add(taxid)
    return declared


def _held_types(ids) -> dict:
    """{release id: the non-reference relationship types its stats count} of
    these releases, from two statements; none for no id. A reference type
    links entity layers and states no interaction, so it is left out, as the
    network's default layers leave it out."""
    held = defaultdict(set)
    if ids:
        reference = set(
            RelationshipType.objects.filter(is_reference=True).values_list(
                "slug", flat=True
            )
        )
        rows = DatasetRelease.objects.filter(pk__in=sorted(ids))
        for pk, stats in rows.values_list("pk", "stats"):
            held[pk] = set(stats.get("relationship_types", {})) - reference
    return held


def _shown(rows, selections, visible) -> dict:
    """{instance id: Shown} for one caller, `visible` being _visible's set.
    A stated taxa or relationship_types set is shown as stated. An empty one
    means what the selected releases the caller may see declare or hold: one
    statement for the taxa and two for the types, each sent only when an
    instance that states none selects such a release."""
    seen = {
        row.pk: tuple(
            item.release_id
            for item in selections[row.pk]
            if item.release_id in visible.ids
        )
        for row in rows
    }
    stated_taxa = {row.pk: sorted(taxon.pk for taxon in row.taxa.all()) for row in rows}
    stated_types = {
        row.pk: sorted(stated.pk for stated in row.relationship_types.all())
        for row in rows
    }
    declared = _declared_taxa(
        {pk for row in rows if not stated_taxa[row.pk] for pk in seen[row.pk]}
    )
    held = _held_types(
        {pk for row in rows if not stated_types[row.pk] for pk in seen[row.pk]}
    )
    shown = {}
    for row in rows:
        chosen, own = selections[row.pk], seen[row.pk]
        synthetic = any(item.is_synthetic for item in chosen if item.release_id in own)
        derived_taxa = set().union(*(declared[pk] for pk in own))
        derived_types = set().union(*(held[pk] for pk in own))
        shown[row.pk] = Shown(
            selected=chosen,
            seen=own,
            availability=compute(
                row.kind,
                row.status,
                [(item.release_id, item.status) for item in chosen],
                visible.ids,
            ),
            is_synthetic=row.is_synthetic or synthetic,
            taxa=stated_taxa[row.pk] or sorted(derived_taxa),
            relationship_types=stated_types[row.pk] or sorted(derived_types),
        )
    return shown


def _matches(row, shown, query) -> bool:
    """Whether the instance passes every filter the query gives. They combine
    with AND."""
    checks = {
        "q": lambda q: any(
            q.casefold() in text.casefold()
            for text in (row.title, row.short_title, row.summary)
        ),
        "tag": lambda tag: tag in row.tags,
        "relationship_type": lambda slug: slug in shown.relationship_types,
        "taxon": lambda taxid: taxid in shown.taxa or taxid == row.primary_taxon_id,
        "assay": lambda curie: any(term.pk == curie for term in row.assays.all()),
        "year": lambda year: any(
            cited.publication.year == year for cited in row.citations.all()
        ),
        "status": lambda status: status == row.status,
        "availability": lambda availability: availability == shown.availability,
    }
    return all(
        check(query[name]) for name, check in checks.items() if query[name] is not None
    )


def _assay_labels(view, rows) -> dict:
    """{CURIE: label or None} of the assays these instances name, as the
    caller may read them (label_set, which ignores any selection): three
    statements, two when every one is a PSI-MI crosswalk term, none when no
    instance names an assay."""
    curies = sorted({term.pk for row in rows for term in row.assays.all()})
    if not curies:
        return {}
    return term_labels(curies, releases=view.label_set())


def _item(row, shown, labels) -> dict:
    """An instance's keys in the gallery, which its page shows first, in the
    order InstanceItemSerializer declares them."""
    return {
        "slug": row.slug,
        "title": row.title,
        "short_title": row.short_title,
        "summary": row.summary,
        "kind": row.kind,
        "status": row.status,
        "visibility": row.visibility,
        "availability": shown.availability,
        "is_synthetic": shown.is_synthetic,
        "external_url": row.external_url if row.kind == Kind.EXTERNAL else None,
        "tags": row.tags,
        "taxa": shown.taxa,
        "primary_taxon": row.primary_taxon_id,
        "relationship_types": shown.relationship_types,
        "assays": [
            {"curie": term.pk, "label": labels.get(term.pk)}
            for term in sorted(row.assays.all(), key=lambda term: term.pk)
        ],
        "publications": [
            _publication(cited.publication) for cited in row.citations.all()
        ],
    }


def _external_services_effective(request, row, shown) -> bool:
    """Whether the instance's data may go to an external service for this
    caller: it is a typed instance, its stored flag is on, it is live for the
    caller, and every release it selects has external_services in effect for
    the caller. A portal_ppi entry is live but selects nothing, so no approval
    speaks for it. One statement, sent only when those hold."""
    if not (
        row.kind == Kind.TYPED
        and row.allow_external_services
        and shown.availability == Availability.LIVE
    ):
        return False
    selected = frozenset(item.release_id for item in shown.selected)
    external = visible_release_ids(request, Purpose.EXTERNAL, pinned=selected)
    return external.ids == selected


class BrowsersView(TypedGraphView):
    """The gallery: every instance the caller may list that passes the
    filters, ordered by display_order, then slug.

    Each filter takes one value, and they combine with AND. A malformed
    value, or one outside a closed set (tag, relationship_type, taxon,
    status, availability), is a 400; a well-formed q, year or assay that no
    instance has matches nothing. Every instance the caller may list is
    read and filtered here, since availability is computed for each caller
    and SQLite filters no JSON list. One page holds every instance, with
    page.next null, so a page size can come later as an addition.
    """

    allowed_params = (
        "q",
        "tag",
        "relationship_type",
        "taxon",
        "assay",
        "year",
        "status",
        "availability",
    )
    serializer_class = BrowsersSerializer

    @extend_schema(
        operation_id="api_graph_v1_browsers_list",
        parameters=GALLERY_PARAMETERS,
        responses={200: BrowsersSerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request):
        params = self.params
        query = {
            "q": params.phrase("q"),
            "tag": params.choice("tag", lambda: TAGS),
            "relationship_type": params.choice("relationship_type", _type_slugs),
            "taxon": params.taxon(_taxids),
            "assay": params.curie("assay", ASSAY_ONTOLOGY, "MI:0018"),
            "year": params.integer("year", floor=1, cap=MAX_YEAR),
            "status": params.choice("status", lambda: Status.values),
            "availability": params.choice("availability", lambda: Availability.values),
        }
        params.raise_problems()
        rows = list(BrowserInstance.objects.filter(listable(request)))
        selections = _selections(rows)
        prefetch_related_objects(
            rows, _citations(), "taxa", "relationship_types", "assays"
        )
        shown = _shown(rows, selections, _visible(request, selections))
        listed = [row for row in rows if _matches(row, shown[row.pk], query)]
        labels = _assay_labels(self, listed)
        data = {
            "api_version": API_VERSION,
            "query": query,
            "instances": [_item(row, shown[row.pk], labels) for row in listed],
            "page": {"next": None},
        }
        return Response(self.serializer_class(instance=data).data)


class BrowserView(TypedGraphView):
    """One instance, as the caller may see it: the gallery's keys, then its
    texts, the entity types it states, its selection, its presentation and
    whether external services may read its data, with the release entries
    of the selected releases the caller may see.

    A slug outside the grammar is a 404 before any statement: every write
    path checks the slug, so no instance has one, and the grammar is public.
    One statement then finds the instance by its slug among those the caller
    may reach, so an unknown slug and an instance the caller may not reach
    answer the same 404 from the same number of statements. The selection
    lists the releases the caller may see, in the instance's order, with no
    stored order, whose gaps would count the hidden ones between them.
    """

    allowed_params = ()
    serializer_class = BrowserSerializer

    @extend_schema(
        operation_id="api_graph_v1_browsers_retrieve",
        parameters=[SLUG_PARAMETER],
        responses={200: BrowserSerializer, 400: ParameterErrorSerializer},
    )
    def get(self, request, slug):
        self.params.raise_problems()
        # A NUL or any other character outside the grammar never reaches the
        # database.
        if not (len(slug) <= MAX_SLUG_CHARS and SLUG.fullmatch(slug)):
            raise Http404
        row = BrowserInstance.objects.filter(reachable(request), slug=slug).first()
        if row is None:
            raise Http404
        selections = _selections([row])
        prefetch_related_objects(
            [row],
            _citations(),
            "taxa",
            "relationship_types",
            "entity_types",
            "assays",
        )
        visible = _visible(request, selections)
        shown = _shown([row], selections, visible)[row.pk]
        effective = _external_services_effective(request, row, shown)
        labels = _assay_labels(self, [row])
        entries = release_entries(set(shown.seen), visible)
        listed = {entry["id"] for entry in entries}
        data = {
            "api_version": API_VERSION,
            "query": {"slug": slug},
            "instance": {
                **_item(row, shown, labels),
                "description": row.description,
                "methods_text": row.methods_text,
                "stated_entity_types": sorted(
                    entity_type.pk for entity_type in row.entity_types.all()
                ),
                "selection": [
                    {"release": ref("release", item.release_id), "role": item.role}
                    for item in shown.selected
                    if ref("release", item.release_id) in listed
                ],
                "presentation": row.presentation,
                "allow_external_services": row.allow_external_services,
                "external_services_effective": effective,
            },
            "releases": entries,
        }
        return Response(self.serializer_class(instance=data).data)
