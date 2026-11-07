"""Which entities a caller may see, and what of them, until RF1a records which
release states an identifier (architecture §4.1).

An entity is visible when a release of the caller's ReleaseSet

- (a) created it, or
- (b) names it, in an assertion participant row,

and that release's status, read again as graph.summary reads it, is one that
counts, so a set kept too long shows nothing. An entity created by no release
and named by no visible assertion is invisible to everyone, staff included: a
deliberate narrowing of §4.1 until RF1a, since a ReleaseSet does not say who
the caller is. A merged or retired entity follows the same rule.

- Clause (b) reads participant rows where §4.1 says "an assertion on one of
  its relationships". The two agree today, nothing writes a merge, and only
  this form has been measured at scale; P5 decides.
- stated_entity is not consulted: no isoform_entry participant exists before
  R2.
- A visible entity's status shows one bit about a hidden creator, that a
  reference release created it. That is accepted, like its primary identifier,
  type and taxon, which follow the entity.

Entity.label and Entity.description are the first writer's text: a later
release's different label is only noted. So they are shown and matched like an
alias, not like a primary identifier, and only to a caller who may read the
release that wrote them, as graph.summary.term_label treats a term's label.
May read is may see, whatever is selected, so a pinned view keeps a reference
release's labels. There is no walk up `supersedes`: a release's text is
readable only if that release is itself visible to the caller. A hidden
creator's text is replaced, never blanked: the label becomes the primary
identifier's value, the documented default label, or the entity's ref for a
row with no primary, and the description "". A synthetic release needs no
clause of its own: real evidence never names a synthetic entity.

Every function that takes a ReleaseSet refuses one visible_release_ids did not
issue. While the switch is off every function returns nothing.

graph.resolution imports this module, so this module never imports
graph.resolution: the cycle would stop the server.
"""

from typing import NamedTuple

from django.db.models import Count, Exists, OuterRef, Q

from .models import (
    AssertionParticipant,
    DatasetRelease,
    EntityIdentifier,
    IdentifierRole,
    Relationship,
)
from .preconditions import storable, typed_graph_enabled
from .summary import VISIBLE_STATUSES, EdgeSummary, covered_assertions, summarize
from .visibility import (
    NOTHING,
    Purpose,
    ReleaseSet,
    issued_release_set,
    visible_release_ids,
)

# Written out: IdentifierRole declares them in another order.
ROLE_ORDER = (
    IdentifierRole.PRIMARY,
    IdentifierRole.SECONDARY,
    IdentifierRole.XREF,
    IdentifierRole.SYMBOL,
    IdentifierRole.ALIAS,
)


class EntityText(NamedTuple):
    """What a caller is shown of the first writer's text."""

    label: str
    description: str


def visible_entity_q(releases, *, path="") -> Q:
    """Clause (a) or (b) of the module docstring, as a predicate. `path`
    prefixes the entity's fields: "entity__" for identifier rows,
    "merged_into__" for a merged row's survivor."""
    releases = issued_release_set(releases)
    if not typed_graph_enabled():
        return Q(pk__in=[])
    live = storable(releases.ids)
    return Q(
        **{
            f"{path}created_by_release_id__in": live,
            f"{path}created_by_release__status__in": VISIBLE_STATUSES,
        }
    ) | Q(
        Exists(
            AssertionParticipant.objects.filter(
                entity_id=OuterRef(f"{path}pk"),
                release_id__in=live,
                release__status__in=VISIBLE_STATUSES,
            )
        )
    )


def label_release_set(request) -> ReleaseSet:
    """The releases whose text this caller may read: those the caller may see,
    selection ignored. Issued, so the functions here refuse a set built by
    hand. Two queries, once per request."""
    if not typed_graph_enabled():
        return NOTHING
    every = DatasetRelease.objects.values_list("pk", flat=True)
    return visible_release_ids(request, Purpose.VIEW, pinned=every)


def readable_label_q(labels, *, path="") -> Q:
    """The entities whose text the caller may read, `labels` being
    label_release_set's. The OR keeps the join outer, so an entity created by
    no release stays."""
    labels = issued_release_set(labels)
    if not typed_graph_enabled():
        return Q(pk__in=[])
    return Q(**{f"{path}created_by_release__isnull": True}) | Q(
        **{
            f"{path}created_by_release_id__in": storable(labels.ids),
            f"{path}created_by_release__status__in": VISIBLE_STATUSES,
        }
    )


def primary_identifiers(pks) -> dict:
    """{entity id: (namespace slug, value)} of the primary rows. One query."""
    if not typed_graph_enabled():
        return {}
    rows = EntityIdentifier.objects.filter(
        entity_id__in=storable(pks), role=IdentifierRole.PRIMARY
    )
    return {
        entity_id: (namespace, value)
        for entity_id, namespace, value in rows.values_list(
            "entity_id", "namespace_id", "value"
        )
    }


def entity_text(entities, primaries, labels) -> dict:
    """{pk: EntityText}: the stored text when the entity has no creator or the
    caller may read its creator, otherwise the replacement of the module
    docstring. `primaries` is primary_identifiers' result, `labels`
    label_release_set's. The creators are read with their status in one query,
    so a set kept too long shows nothing."""
    labels = issued_release_set(labels)
    if not typed_graph_enabled():
        return {}
    entities = list(entities)
    creators = {entity.created_by_release_id for entity in entities} & labels.ids
    readable = set(
        DatasetRelease.objects.filter(
            pk__in=storable(creators), status__in=VISIBLE_STATUSES
        ).values_list("pk", flat=True)
    )
    text = {}
    for entity in entities:
        creator = entity.created_by_release_id
        if creator is None or creator in readable:
            text[entity.pk] = EntityText(entity.label, entity.description)
        elif entity.pk in primaries:
            text[entity.pk] = EntityText(primaries[entity.pk][1], "")
        else:
            text[entity.pk] = EntityText(f"entity:{entity.pk}", "")
    return text


def visible_identifiers(entity, releases) -> list:
    """The entity's identifier rows while it is visible, by ROLE_ORDER, then
    namespace slug, then value. One query.

    An interim rule, which RF1a replaces with the source-row gate. No import
    stores a non-primary identifier before RF1a, so every such row is
    operator-entered; a primary is visible exactly when its entity is."""
    rows = (
        EntityIdentifier.objects.filter(entity_id=entity.pk)
        .filter(visible_entity_q(releases, path="entity__"))
        .select_related("namespace")
    )
    return sorted(
        rows, key=lambda row: (ROLE_ORDER.index(row.role), row.namespace_id, row.value)
    )


def visible_reference_relationships(entity, releases) -> list[EdgeSummary]:
    """The summaries, by relationship id, of the reference relationships
    touching the entity that are shown under `releases`. One query filters
    them first, so the count of queries is blind to a hidden reference link;
    then summarize's three."""
    releases = issued_release_set(releases)
    if not typed_graph_enabled():
        return []
    covered = covered_assertions(releases=releases)
    ids = (
        Relationship.objects.filter(relationship_type__is_reference=True)
        .filter(Q(subject_id=entity.pk) | Q(object_id=entity.pk))
        .filter(Exists(covered.filter(relationship_id=OuterRef("pk"))))
        .values_list("pk", flat=True)
    )
    summaries = summarize(list(ids), releases=releases).values()
    return sorted(
        (summary for summary in summaries if summary.shown),
        key=lambda summary: summary.relationship_id,
    )


def relationship_counts(entity, releases) -> dict:
    """{type slug: relationships touching the entity with a covered assertion
    under `releases`}, keys sorted, from one aggregate query. A self-loop
    counts once; a directed pair A to B and B to A are two relationships; a
    symmetric pair is one."""
    releases = issued_release_set(releases)
    if not typed_graph_enabled():
        return {}
    covered = covered_assertions(releases=releases)
    rows = (
        Relationship.objects.filter(Q(subject_id=entity.pk) | Q(object_id=entity.pk))
        .filter(Exists(covered.filter(relationship_id=OuterRef("pk"))))
        .values("relationship_type_id")
        .annotate(n=Count("pk"))
        .order_by("relationship_type_id")
    )
    return {row["relationship_type_id"]: row["n"] for row in rows}


def naming_releases(entities, releases) -> dict:
    """{entity id: frozenset of the ids of the set that created or name it},
    every entity given having a key, with an empty set when nothing names it,
    so a release withdrawn after the entity was read gives no KeyError.

    One query, with no OR around an EXISTS: under an OR PostgreSQL runs the
    EXISTS per release. The creator comes from the row in hand, so its status
    is read again in one place, the API's release_entries, and whatever a
    response shows names only ids that function returned."""
    releases = issued_release_set(releases)
    entities = list(entities)
    named = {entity.pk: set() for entity in entities}
    if typed_graph_enabled():
        rows = AssertionParticipant.objects.filter(
            entity_id__in=storable(named),
            release_id__in=storable(releases.ids),
            release__status__in=VISIBLE_STATUSES,
        ).values_list("entity_id", "release_id")
        for entity_id, release_id in rows.distinct():
            named[entity_id].add(release_id)
        for entity in entities:
            if entity.created_by_release_id in releases.ids:
                named[entity.pk].add(entity.created_by_release_id)
    return {pk: frozenset(ids) for pk, ids in named.items()}
