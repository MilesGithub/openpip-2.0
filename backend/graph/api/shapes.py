"""The shapes every route of /api/graph/v1/ shares, so each is built one way.

Every entity node goes through entity_nodes, so no route can show a label the
caller may not read, and every release block through release_entries, the one
place a release's status is read again for a response. A ref a response shows
is one release_entries returned (release_refs). Every edge goes through edge,
so the network, the relationship page and the entity page show one edge shape,
and every evidence row through evidence_rows, which gates a term's label, a
stated isoform and a record's raw columns.
"""

from ..approvals import effective_scopes_by_release
from ..entity_visibility import entity_text, primary_identifiers, visible_entity_q
from ..ingest.registry import UnknownAdapter, get_adapter
from ..licensing import latest_licences
from ..models import ApprovalScope, DatasetRelease, Entity, ParticipantSide
from ..preconditions import storable, typed_graph_enabled
from ..summary import VISIBLE_STATUSES, term_labels
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


def edge(summary, entries) -> dict:
    """One relationship as every route shows it (architecture §7.3), from its
    EdgeSummary and the response's release entries: the eight keys of the
    first version, then every figure of the summary, in the order the
    EdgeSerializer declares. `source` and `target` are the stored subject and
    object, a symmetric pair in canonical order; `releases` the refs of the
    covered assertions' releases that the response lists; `is_synthetic`
    whether any of them is synthetic. `qualifiers` is empty until identity
    qualifiers are parsed. Every count holds every key."""
    listed = release_refs(summary.release_ids, entries)
    synthetic = {entry["id"] for entry in entries if entry["is_synthetic"]}
    return {
        "id": ref("relationship", summary.relationship_id),
        "type": summary.relationship_type,
        "family": summary.family,
        "orientation": summary.orientation,
        "source": ref("entity", summary.subject_id),
        "target": ref("entity", summary.object_id),
        "releases": listed,
        "is_synthetic": any(item in synthetic for item in listed),
        "qualifiers": {},
        "shown": summary.shown,
        "effect": summary.effect,
        "effect_counts": summary.effect_counts,
        "genetic_sign": summary.genetic_sign,
        "sign_counts": summary.sign_counts,
        "outcome_counts": summary.outcome_counts,
        "independent_outcome_counts": summary.independent_outcome_counts,
        "assertion_count": summary.assertion_count,
        "evidence_count": summary.evidence_count,
        "distinct": {
            "publications": summary.distinct_publications,
            "releases": summary.distinct_releases,
            "datasets": summary.distinct_datasets,
        },
        "by_basis": summary.by_basis,
        "level": summary.level,
        "level_counts": summary.level_counts,
        "level_assigned": summary.level_assigned,
        "tested_levels": {
            outcome: list(levels) for outcome, levels in summary.tested_levels.items()
        },
        "outside_filters": summary.outside_filters,
        "outside_outcome_counts": summary.outside_outcome_counts,
    }


def evidence_rows(assertions, entries, *, data_releases, label_releases, staff) -> list:
    """The rows of an evidence page (architecture §7.3), in the order given,
    from the assertions evidence() loaded and the response's release entries.
    `data_releases` is release_set's, `label_releases` label_set's, `staff`
    whether the caller is staff.

    Every term of the rows is labelled through one term_labels call with the
    may-read set, so a selection never blanks a label the caller may read. A
    participant's stated isoform is named only when the caller may see it
    under the data set: one query over the distinct stated ids, sent only when
    a row names one. A record's raw columns show to staff, or when the row's
    release has an effective public_view approval and stores raw rows
    (readable_raw); otherwise null, the key and line kept, and the raw value
    is not read. The one builder of the row: the exports and the frontend's
    fixtures call it too."""
    assertions = list(assertions)
    labels = term_labels(_terms_of(assertions), releases=label_releases)
    stated = {
        participant.stated_entity_id
        for row in assertions
        for participant in row.participants.all()
    } - {None}
    seen = set()
    if stated:
        seen = set(
            Entity.objects.filter(pk__in=storable(stated))
            .filter(visible_entity_q(data_releases))
            .values_list("pk", flat=True)
        )
    readable = readable_raw(assertions, entries, staff=staff)
    return [_evidence_row(row, labels, seen, row.pk in readable) for row in assertions]


def readable_raw(assertions, entries, *, staff) -> set:
    """The ids of the rows whose record's raw columns the caller reads: staff,
    or a row whose release entry lists an effective public_view approval, and
    either way only when the release stores raw rows. The one gate of raw:
    evidence_rows applies it, and the evidence page counts and loads only the
    records it lets through."""
    scopes = {entry["id"]: entry["scopes"] for entry in entries}
    return {
        row.pk
        for row in assertions
        if row.release.store_raw_rows
        and (
            staff
            or ApprovalScope.PUBLIC_VIEW
            in scopes.get(ref("release", row.release_id), ())
        )
    }


def _terms_of(assertions) -> list:
    """Every term the rows show: ECO terms, interaction types, the methods'
    and the contexts' terms."""
    terms = []
    for row in assertions:
        terms += [row.eco_term, row.interaction_type]
        terms += [method.term for method in row.methods.all()]
        terms += [context.term for context in row.contexts.all()]
    return [term for term in terms if term is not None]


def _term_ref(term, labels) -> dict | None:
    return None if term is None else {"curie": term.pk, "label": labels.get(term.pk)}


def _publication(paper) -> dict:
    """A paper by its identifiers, blanks as null; its descriptive text is the
    first writer's, as the model keeps it."""
    return {
        "pmid": paper.pmid or None,
        "doi": paper.doi or None,
        "pmcid": paper.pmcid or None,
        "imex_id": paper.imex_id or None,
        "local_key": paper.local_key or None,
        "title": paper.title,
        "authors": paper.authors,
        "journal": paper.journal,
        "year": paper.year,
        "url": paper.url or None,
        "is_synthetic": paper.is_synthetic,
    }


def _participant(participant, seen) -> dict:
    stated = participant.stated_entity_id
    return {
        "side": participant.side,
        "entity": ref("entity", participant.entity_id),
        "stated_entity": ref("entity", stated) if stated in seen else None,
        "identifier_as_stated": participant.identifier_as_stated,
        "resolution_basis": participant.resolution_basis,
    }


def _evidence_row(row, labels, seen, readable) -> dict:
    record = row.source_record
    # The prefetch is in id order; the subject is listed first whatever
    # order the source wrote the sides in.
    participants = sorted(
        row.participants.all(),
        key=lambda participant: participant.side != ParticipantSide.SUBJECT,
    )
    return {
        "id": ref("evidence", row.pk),
        "release": ref("release", row.release_id),
        "record": {
            "key": record.record_key,
            "line_number": record.line_number,
            "raw": record.raw if readable else None,
        },
        "outcome": row.outcome,
        "evidence_basis": row.evidence_basis,
        "assertion_method": row.assertion_method,
        "eco_term": _term_ref(row.eco_term, labels),
        "effect": row.effect,
        "effect_as_stated": row.effect_as_stated,
        "genetic_sign": row.genetic_sign,
        "interaction_type": _term_ref(row.interaction_type, labels),
        "interaction_level": row.interaction_level,
        "relationship_as_stated": row.relationship_as_stated,
        "methods": [
            {
                "role": method.role,
                "curie": method.term_id,
                "label": labels.get(method.term_id),
            }
            for method in row.methods.all()
        ],
        "scores": [
            {
                "type": score.score_type_id,
                "label": score.score_type.label,
                "value": score.value,
                "raw_value": score.raw_value,
                "value_kind": score.score_type.value_kind,
                "direction": score.score_type.direction,
                "unit": score.score_type.unit or None,
            }
            for score in row.scores.all()
        ],
        "contexts": [
            {
                "facet": context.facet,
                "term": _term_ref(context.term, labels),
                "taxon": context.taxon_id,
                "label": context.label,
                "raw_value": context.raw_value,
            }
            for context in row.contexts.all()
        ],
        "participants": [
            _participant(participant, seen) for participant in participants
        ],
        "qualifiers": row.qualifiers,
        "evidence_group": row.evidence_group,
        "publications": [
            {"role": cited.role, "publication": _publication(cited.publication)}
            for cited in row.publications.all()
        ],
    }
