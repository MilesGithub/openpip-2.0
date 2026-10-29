"""Load the typed graph's registries from their reviewed JSON.

    python manage.py graph_sync_registry             # apply
    python manage.py graph_sync_registry --dry-run   # show what would change
    python manage.py graph_sync_registry --check     # exit 1 unless in sync

The sync creates missing rows and is all or nothing. On existing rows it
updates descriptive fields: labels, definitions, display tokens, URL templates
and authority references. Every other field shapes stored data (a namespace's
case policy decides each lookup in it), so a change to one is applied only
while no stored row uses that registry row, and is refused, naming the rows,
once one does: a change that would re-key data needs a deliberate re-key.
Rows missing from the JSON are reported and never deleted. Nothing is
written while TYPED_GRAPH_ENABLED is off.
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
    IdentifierNamespace,
    RegistryState,
    Taxon,
)

TAXON_DESCRIPTIVE = {"scientific_name", "common_name", "rank", "label_source"}
TYPE_DESCRIPTIVE = {"label", "definition", "display_token"}
NAMESPACE_DESCRIPTIVE = {"label", "url_template", "authority"}
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


class Plan:
    """What a sync would do, and what it would refuse."""

    def __init__(self):
        self.lines = []
        self.writes = []
        self.refusals = []
        self.missing = []

    @property
    def in_sync(self) -> bool:
        return not (self.writes or self.refusals or self.missing)

    def add_registry(self, kind, model, entries, key, fields_of, descriptive, users):
        many = "allowed_entity_types" if model is IdentifierNamespace else None
        rows = model.objects.all()
        if many:
            rows = rows.prefetch_related(many)
        existing = {row.pk: row for row in rows}
        for entry in entries:
            pk, values = entry[key], fields_of(entry)
            wanted = set(entry[many]) if many else None
            row = existing.pop(pk, None)
            if row is None:
                self.lines.append(f"create {kind} {pk}")
                self.writes.append(partial(_create, model, pk, values, many, wanted))
                continue
            changes = {
                field: (getattr(row, field), value)
                for field, value in values.items()
                if getattr(row, field) != value
            }
            if many:
                current = {related.pk for related in getattr(row, many).all()}
                if current != wanted:
                    changes[many] = (sorted(current), sorted(wanted))
            if not changes:
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
                field: new for field, (_, new) in changes.items() if field != many
            }
            self.writes.append(
                partial(_update, model, pk, update, many, wanted, many in changes)
            )
        for pk in sorted(existing):
            self.missing.append(
                f"missing from the JSON: {kind} {pk} (kept; the sync never deletes)"
            )


def _create(model, pk, values, many, wanted):
    row = model.objects.create(pk=pk, **values)
    if many:
        getattr(row, many).set(sorted(wanted))


def _update(model, pk, values, many, wanted, many_changed):
    if values:
        model.objects.filter(pk=pk).update(**values)
    if many_changed:
        getattr(model.objects.get(pk=pk), many).set(sorted(wanted))


def plan_sync(loaded: registry.Registry) -> Plan:
    plan = Plan()
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
        "namespaces) from backend/graph/registry/. Refuses while "
        "TYPED_GRAPH_ENABLED is off."
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
            for write in plan.writes:
                write()
            if _record_version not in plan.writes:
                _record_version()
        self.stdout.write(f"Synced the registry to version {version}.")
