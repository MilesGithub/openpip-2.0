"""The shapes every route of /api/graph/v1/ shares, so each is built one way.

Every entity node goes through entity_nodes, so no route can show a label the
caller may not read, and every release block through release_entries, the one
place a release's status is read again for a response. A ref a response shows
is one release_entries returned (release_refs).
"""

from ..approvals import effective_scopes_by_release
from ..entity_visibility import entity_text, primary_identifiers
from ..ingest.registry import UnknownAdapter, get_adapter
from ..licensing import latest_licences
from ..models import ApprovalScope, DatasetRelease
from ..preconditions import storable, typed_graph_enabled
from ..summary import VISIBLE_STATUSES
from ..visibility import issued_release_set
from .refs import ref


def entity_nodes(entities, labels) -> dict:
    """{pk: node} of the entities of a response, from two queries over them
    all. `labels` is label_release_set's. A node also carries the gated
    `description`, which the node's serializer does not declare, for the
    entity route."""
    entities = list(entities)
    primaries = primary_identifiers([entity.pk for entity in entities])
    text = entity_text(entities, primaries, labels)
    return {
        entity.pk: _node(entity, primaries.get(entity.pk), text[entity.pk])
        for entity in entities
        if entity.pk in text
    }


def _node(entity, primary, text) -> dict:
    return {
        "id": ref("entity", entity.pk),
        "type": entity.entity_type_id,
        "label": text.label,
        "description": text.description,
        "taxon": entity.taxon_id,
        "status": entity.status,
        "primary_identifier": (
            None if primary is None else {"namespace": primary[0], "value": primary[1]}
        ),
    }


def release_entries(ids, releases) -> list:
    """The entries of the ids that are in `releases` with a status that counts,
    read as stored, sorted by id. Three queries.

    An entry carries the release's attribution and licence summary, and its
    note of changes, which CC BY and CC BY-SA require with attribution. Every
    release of a ReleaseSet has a licence snapshot, since an effective approval
    must name one. Every effective scope is listed, to every caller."""
    releases = issued_release_set(releases)
    if not typed_graph_enabled():
        return []
    rows = list(
        DatasetRelease.objects.filter(
            pk__in=storable(set(ids) & releases.ids), status__in=VISIBLE_STATUSES
        )
        .select_related("dataset")
        .order_by("pk")
    )
    pks = [row.pk for row in rows]
    licences = latest_licences(pks)
    scopes = effective_scopes_by_release(pks)
    return [
        _entry(row, licences[row.pk], scopes.get(row.pk, ()), releases) for row in rows
    ]


def _entry(release, licence, scopes, releases) -> dict:
    return {
        "id": ref("release", release.pk),
        "dataset": {"slug": release.dataset.slug, "title": release.dataset.title},
        "version": release.version,
        "status": release.status,
        "superseded": release.pk in releases.superseded,
        "is_synthetic": release.is_synthetic,
        "licence": {
            "name": licence.licence_name or None,
            "spdx_id": licence.spdx_id or None,
            "url": licence.licence_url or None,
        },
        "attribution": licence.attribution,
        "changes_note": _changes_note(release.adapter),
        "scopes": [scope for scope in ApprovalScope.values if scope in scopes],
    }


def _changes_note(adapter) -> str | None:
    """The note of the adapter this release names; None when it names none, or
    one this code no longer has."""
    try:
        return get_adapter(adapter).spec.changes_note
    except UnknownAdapter:
        return None


def release_refs(ids, entries) -> list:
    """The refs of these release ids, sorted by id, that release_entries
    returned as `entries`."""
    shown = {entry["id"] for entry in entries}
    refs = (ref("release", pk) for pk in sorted(ids))
    return [item for item in refs if item in shown]
