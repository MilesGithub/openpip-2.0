"""Load the typed graph's registries from their reviewed JSON.

    python manage.py graph_sync_registry             # apply
    python manage.py graph_sync_registry --dry-run   # show what would change
    python manage.py graph_sync_registry --check     # exit 1 unless in sync

The sync creates missing rows and is all or nothing. On existing rows it
updates descriptive fields: labels, definitions, display tokens, URL templates
and authority references, and a relationship type's role labels and PSI-MI
anchors. Every other field shapes stored data (a namespace's case policy
decides each lookup in it, a relationship type's orientation which pairs are
one relationship, a score type's range which values are valid), so a change to
one, many-to-many fields included, is applied only while no stored row uses
that registry row, and is refused, naming the rows, once one does: a change
that would re-key data needs a deliberate re-key. Rows missing from the JSON
are reported and never deleted. Nothing is written while TYPED_GRAPH_ENABLED
is off.

Writers that validate against a registry row hold it in shared mode until
they commit (graph.models.share_lock). The sync therefore plans again inside
its write transaction: it locks every row it will change, in key order and
registry by registry, and only then reads it again and counts the rows that
use it. Whichever of a sync and a writer locks second sees the other's
result. The lock is FOR NO KEY UPDATE, which a writer's shared lock waits
for but a foreign-key check does not, so a row a writer only references (a
taxon, checked when it commits) never joins a cycle.
"""

from functools import partial

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from graph import registry
from graph.models import (
    Entity,
    EntityIdentifier,
    EntityType,
    EvidenceScore,
    IdentifierNamespace,
    RegistryState,
    Relationship,
    RelationshipType,
    ScoreType,
    Taxon,
)

TAXON_DESCRIPTIVE = {"scientific_name", "common_name", "rank", "label_source"}
TYPE_DESCRIPTIVE = {"label", "definition", "display_token"}
NAMESPACE_DESCRIPTIVE = {"label", "url_template", "authority"}
RELATIONSHIP_TYPE_DESCRIPTIVE = {
    "label",
    "definition",
    "subject_role_label",
    "object_role_label",
    "display_token",
    "psi_mi_anchors",
}
SCORE_TYPE_DESCRIPTIVE = {"label", "definition", "defining_document"}
# Each registry's many-to-many fields: the JSON lists slugs.
MANY_TO_MANY = {
    IdentifierNamespace: ("allowed_entity_types",),
    RelationshipType: ("allowed_subject_types", "allowed_object_types"),
}
# How many of the stored rows that block a change a refusal lists by id.
SHOWN_IDS = 10


def taxon_fields(entry) -> dict:
    return {
        "scientific_name": entry["scientific_name"],
        "common_name": entry["common_name"],
        "rank": entry["rank"],
        "label_source": entry["label_source"],
        "parent_id": entry["parent"],
        "merged_into_id": entry["merged_into"],
    }


def entity_type_fields(entry) -> dict:
    return {
        field: entry[field]
        for field in (
            "label",
            "definition",
            "taxon_policy",
            "identity_scheme",
            "assembly_required",
            "display_token",
        )
    }


def namespace_fields(entry) -> dict:
    fields = {
        field: entry[field]
        for field in (
            "label",
            "curie_prefix",
            "scope",
            "case_policy",
            "version_policy",
            "defines_identity",
            "pattern",
            "url_template",
            "authority",
        )
    }
    fields["fixed_taxon_id"] = entry["fixed_taxon"]
    return fields


def relationship_type_fields(entry) -> dict:
    return {
        field: entry[field]
        for field in (
            "label",
            "definition",
            "family",
            "orientation",
            "polarity",
            "subject_role_label",
            "object_role_label",
            "identity_qualifiers",
            "self_pairs_allowed",
            "psi_mi_anchors",
            "display_token",
            "is_reference",
        )
    }


def score_type_fields(entry) -> dict:
    return {
        field: entry[field]
        for field in (
            "label",
            "definition",
            "value_kind",
            "direction",
            "range_min",
            "range_max",
            "unit",
            "null_model",
            "defining_document",
            "comparable_across_datasets",
        )
    }


def _users(queryset, noun, nouns):
    """The stored rows that depend on a registry row, as a phrase, or None."""
    count = queryset.count()
    if not count:
        return None
    ids = list(queryset.order_by("pk").values_list("pk", flat=True)[:SHOWN_IDS])
    shown = ", ".join(map(str, ids)) + (", ..." if count > len(ids) else "")
    if count == 1:
        return f"1 {noun} uses it (id {shown})"
    return f"{count} {nouns} use it (ids {shown})"


def taxon_users(taxid):
    return _users(Entity.objects.filter(taxon_id=taxid), "entity", "entities")


def entity_type_users(slug):
    return _users(Entity.objects.filter(entity_type_id=slug), "entity", "entities")


def namespace_users(slug):
    return _users(
        EntityIdentifier.objects.filter(namespace_id=slug),
        "entity identifier",
        "entity identifiers",
    )


def relationship_type_users(slug):
    return _users(
        Relationship.objects.filter(relationship_type_id=slug),
        "relationship",
        "relationships",
    )


def score_type_users(slug):
    return _users(
        EvidenceScore.objects.filter(score_type_id=slug),
        "evidence score",
        "evidence scores",
    )


class Plan:
    """What a sync would do, and what it would refuse. With `lock`, as the
    sync plans inside its write transaction: each row it will change is
    locked before it is read again and its users are counted."""

    def __init__(self, lock=False):
        self.lock = lock
        self.lines = []
        self.writes = []
        self.refusals = []
        self.missing = []

    @property
    def in_sync(self) -> bool:
        return not (self.writes or self.refusals or self.missing)

    def add_registry(self, kind, model, entries, key, fields_of, descriptive, users):
        many = MANY_TO_MANY.get(model, ())
        entries = [
            (entry[key], fields_of(entry), {name: set(entry[name]) for name in many})
            for entry in entries
        ]
        existing = _rows(model, many)
        locked = set()
        if self.lock:
            locked = {
                pk
                for pk, values, wanted in entries
                if pk in existing and _changes(existing[pk], values, wanted, many)
            }
            _lock(model, locked)
            existing = _rows(model, many)
        for pk, values, wanted in entries:
            row = existing.pop(pk, None)
            if row is None:
                self.lines.append(f"create {kind} {pk}")
                self.writes.append(partial(_create, model, pk, values, wanted))
                continue
            changes = _changes(row, values, wanted, many)
            if not changes:
                continue
            if self.lock and pk not in locked:
                self.refusals.append(
                    f"refused: {kind} {pk} changed while this sync ran; run it again"
                )
                continue
            structural = sorted(set(changes) - descriptive)
            blocked_by = users(pk) if structural else None
            for field in sorted(changes):
                old, new = changes[field]
                change = f"{kind} {pk}: {field.removesuffix('_id')} {old!r} -> {new!r}"
                if blocked_by and field in structural:
                    self.refusals.append(f"refused: {change}; {blocked_by}")
                else:
                    self.lines.append(f"update {change}")
            update = {
                field: new for field, (_, new) in changes.items() if field not in many
            }
            relinked = {name: wanted[name] for name in many if name in changes}
            self.writes.append(partial(_update, model, pk, update, relinked))
        for pk in sorted(existing):
            self.missing.append(
                f"missing from the JSON: {kind} {pk} (kept; the sync never deletes)"
            )


def _rows(model, many) -> dict:
    rows = model.objects.all()
    if many:
        rows = rows.prefetch_related(*many)
    return {row.pk: row for row in rows}


def _changes(row, values, wanted, many) -> dict:
    """{field: (stored, wanted)} for each field the JSON changes."""
    changes = {
        field: (getattr(row, field), value)
        for field, value in values.items()
        if getattr(row, field) != value
    }
    for name in many:
        current = {related.pk for related in getattr(row, name).all()}
        if current != wanted[name]:
            changes[name] = (sorted(current), sorted(wanted[name]))
    return changes


def _lock(model, pks):
    """Lock these rows, in key order, until the sync commits: FOR NO KEY
    UPDATE, the lock its own updates take, since the sync never changes a
    key. It conflicts with a writer's shared lock (share_lock) but not with
    the key-share lock a foreign-key check takes at commit, so a writer that
    only references a changed row (an entity its taxon) never waits on the
    sync in a cycle."""
    list(
        model.objects.select_for_update(no_key=True)
        .filter(pk__in=sorted(pks))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _create(model, pk, values, wanted):
    row = model.objects.create(pk=pk, **values)
    for name, slugs in wanted.items():
        getattr(row, name).set(sorted(slugs))


def _update(model, pk, values, relinked):
    if values:
        model.objects.filter(pk=pk).update(**values)
    if relinked:
        row = model.objects.get(pk=pk)
        for name, slugs in relinked.items():
            getattr(row, name).set(sorted(slugs))


def plan_sync(loaded: registry.Registry, *, lock=False) -> Plan:
    """What the sync would do. With `lock`, inside the write transaction, the
    rows it will change are locked first (Plan)."""
    plan = Plan(lock=lock)
    plan.add_registry(
        "taxon",
        Taxon,
        registry.taxa_in_dependency_order(loaded.taxa),
        "taxid",
        taxon_fields,
        TAXON_DESCRIPTIVE,
        taxon_users,
    )
    plan.add_registry(
        "entity type",
        EntityType,
        loaded.entity_types,
        "slug",
        entity_type_fields,
        TYPE_DESCRIPTIVE,
        entity_type_users,
    )
    plan.add_registry(
        "namespace",
        IdentifierNamespace,
        loaded.namespaces,
        "slug",
        namespace_fields,
        NAMESPACE_DESCRIPTIVE,
        namespace_users,
    )
    plan.add_registry(
        "relationship type",
        RelationshipType,
        loaded.relationship_types,
        "slug",
        relationship_type_fields,
        RELATIONSHIP_TYPE_DESCRIPTIVE,
        relationship_type_users,
    )
    plan.add_registry(
        "score type",
        ScoreType,
        loaded.score_types,
        "slug",
        score_type_fields,
        SCORE_TYPE_DESCRIPTIVE,
        score_type_users,
    )
    database = registry.registry_state().database
    if database != registry.REGISTRY_VERSION:
        plan.lines.append(
            f"record registry version {registry.REGISTRY_VERSION} "
            f"(the database has {database or 'none'})"
        )
        plan.writes.append(_record_version)
    return plan


def _record_version():
    # Every sync that writes anything ends here, so synced_at is the last one.
    RegistryState.objects.update_or_create(
        pk=1,
        defaults={"version": registry.REGISTRY_VERSION, "synced_at": timezone.now()},
    )


class Command(BaseCommand):
    help = (
        "Load the typed-graph registries (taxa, entity types, identifier "
        "namespaces, relationship types, score types) from "
        "backend/graph/registry/. Refuses while TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what a sync would change, and write nothing.",
        )
        mode.add_argument(
            "--check",
            action="store_true",
            help=(
                "Write nothing, and exit 1 if a sync would write something, "
                "would refuse a change, or a row is missing from the JSON."
            ),
        )

    def handle(self, *args, dry_run=False, check=False, **options):
        if not settings.TYPED_GRAPH_ENABLED:
            raise CommandError(
                "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), "
                "so its registries are not loaded. Nothing was written."
            )
        try:
            loaded = registry.load_registry()
        except registry.RegistryError as exc:
            raise CommandError(
                f"The registry JSON is invalid; nothing was written.\n{exc}"
            ) from None

        plan = plan_sync(loaded)
        for line in plan.lines + plan.refusals + plan.missing:
            self.stdout.write(line)

        version = registry.REGISTRY_VERSION
        if check:
            if not plan.in_sync:
                raise CommandError(
                    f"The registry tables differ from the JSON (version {version})."
                )
            self.stdout.write(
                f"The registry tables match the JSON (version {version})."
            )
            return
        if plan.refusals:
            raise CommandError(
                "Stored data depend on these fields, so nothing was written:\n"
                + "\n".join(plan.refusals)
            )
        if dry_run:
            self.stdout.write("Dry run: nothing was written.")
            return
        if not plan.writes:
            self.stdout.write(f"No changes: the registry is at version {version}.")
            return
        with transaction.atomic():
            # Planned again under the locks: a writer that held a row first
            # has committed, and what it stored is counted now.
            plan = plan_sync(loaded, lock=True)
            if plan.refusals:
                raise CommandError(
                    "Stored data depend on these fields, so nothing was written:\n"
                    + "\n".join(plan.refusals)
                )
            for write in plan.writes:
                write()
            if _record_version not in plan.writes:
                _record_version()
        self.stdout.write(f"Synced the registry to version {version}.")
