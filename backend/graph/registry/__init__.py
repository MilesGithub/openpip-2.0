"""The typed graph's registries: taxa, entity types, identifier namespaces,
relationship types and score types.

They are reviewed JSON in v1/, loaded into their tables by
`manage.py graph_sync_registry`, never by a migration, so a portal that keeps
the typed graph off has empty registry tables. This module reads and checks
the JSON; the command decides what may change in the database.

Each file holds a list of entries with exactly these keys:

- taxa.json: taxid, scientific_name, common_name, rank, parent and
  merged_into (taxids of other entries, or null), label_source.
- entity_types.json: slug, label, definition, taxon_policy, identity_scheme,
  assembly_required, display_token.
- namespaces.json: slug, label, curie_prefix, allowed_entity_types, scope,
  fixed_taxon, case_policy, version_policy, defines_identity, pattern (matched
  against the whole lookup), url_template, authority (the documents the rules
  were checked against), and examples, values the rules must accept, which
  are checked here and not stored.
- relationship_types.json: slug, label, definition (what the edge claims and
  what it does not), family, orientation, polarity, allowed_subject_types and
  allowed_object_types (entity type slugs), subject_role_label,
  object_role_label, identity_qualifiers (always [] until P2),
  self_pairs_allowed, psi_mi_anchors (PSI-MI CURIEs), display_token,
  is_reference.
- score_types.json: slug (<source>:<name> or stats:<name>), label,
  definition, value_kind, direction, range_min and range_max (finite numbers
  or null), unit, null_model, defining_document, comparable_across_datasets.
"""

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from graph.identity import InvalidIdentifier, normalize
from graph.models import (
    SCORE_TYPE_SLUG,
    CasePolicy,
    IdentityScheme,
    Orientation,
    Polarity,
    RegistryState,
    RelationshipFamily,
    Scope,
    ScoreDirection,
    ScoreValueKind,
    TaxonPolicy,
    VersionPolicy,
)

# The version of the JSON below. Bump it with any change to the JSON: the
# import commands refuse to run while the database holds another version, and
# graph_sync_registry records the one it loaded. 1.1 added the relationship
# and score types.
REGISTRY_VERSION = "1.1"
REGISTRY_DIR = Path(__file__).resolve().parent / "v1"

TAXON_KEYS = {
    "taxid",
    "scientific_name",
    "common_name",
    "rank",
    "parent",
    "merged_into",
    "label_source",
}
ENTITY_TYPE_KEYS = {
    "slug",
    "label",
    "definition",
    "taxon_policy",
    "identity_scheme",
    "assembly_required",
    "display_token",
}
NAMESPACE_KEYS = {
    "slug",
    "label",
    "curie_prefix",
    "allowed_entity_types",
    "scope",
    "fixed_taxon",
    "case_policy",
    "version_policy",
    "defines_identity",
    "pattern",
    "url_template",
    "authority",
    "examples",
}
RELATIONSHIP_TYPE_KEYS = {
    "slug",
    "label",
    "definition",
    "family",
    "orientation",
    "polarity",
    "allowed_subject_types",
    "allowed_object_types",
    "subject_role_label",
    "object_role_label",
    "identity_qualifiers",
    "self_pairs_allowed",
    "psi_mi_anchors",
    "display_token",
    "is_reference",
}
SCORE_TYPE_KEYS = {
    "slug",
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
}
SLUG = re.compile(r"[a-z][a-z0-9_]{0,49}")
CURIE_PREFIX = re.compile(r"[a-z][a-z0-9._-]{0,49}")
PSI_MI_CURIE = re.compile(r"MI:[0-9]{4}")
# One namespace never identifies both a gene and a protein: an accession could
# then sit on either, and gene and protein must stay separate entities.
GENE_TYPES = {"gene"}
PROTEIN_TYPES = {"protein", "protein_isoform"}
# Kinds whose values lie in [0, 1].
UNIT_INTERVAL_KINDS = {
    ScoreValueKind.PROBABILITY,
    ScoreValueKind.P_VALUE,
    ScoreValueKind.Q_VALUE,
}


class RegistryError(ValueError):
    """The registry JSON breaks a rule. The message lists every problem."""


@dataclass(frozen=True)
class Registry:
    taxa: tuple[dict, ...]
    entity_types: tuple[dict, ...]
    namespaces: tuple[dict, ...]
    relationship_types: tuple[dict, ...] = ()
    score_types: tuple[dict, ...] = ()


@dataclass(frozen=True)
class RegistryStatus:
    code: str
    database: str | None
    synced_at: datetime | None


def load_registry(directory: Path | None = None) -> Registry:
    """Read and check the registry JSON. Raises RegistryError."""
    directory = Path(directory or REGISTRY_DIR)
    problems = []
    registry = Registry(
        taxa=_read(directory / "taxa.json", problems),
        entity_types=_read(directory / "entity_types.json", problems),
        namespaces=_read(directory / "namespaces.json", problems),
        relationship_types=_read(directory / "relationship_types.json", problems),
        score_types=_read(directory / "score_types.json", problems),
    )
    if not problems:
        problems = validate(registry)
    if problems:
        raise RegistryError("\n".join(problems))
    return registry


def registry_state() -> RegistryStatus:
    """The registry version this code carries, and the one the database holds."""
    row = RegistryState.objects.filter(pk=1).first()
    return RegistryStatus(
        code=REGISTRY_VERSION,
        database=row.version if row else None,
        synced_at=row.synced_at if row else None,
    )


def validate(registry: Registry) -> list[str]:
    """Every rule the registry breaks, as sentences; empty when it is sound."""
    problems = []
    taxa = _keyed(registry.taxa, "taxid", TAXON_KEYS, "taxa.json", problems)
    types = _keyed(
        registry.entity_types, "slug", ENTITY_TYPE_KEYS, "entity_types.json", problems
    )
    namespaces = _keyed(
        registry.namespaces, "slug", NAMESPACE_KEYS, "namespaces.json", problems
    )
    relationship_types = _keyed(
        registry.relationship_types,
        "slug",
        RELATIONSHIP_TYPE_KEYS,
        "relationship_types.json",
        problems,
    )
    score_types = _keyed(
        registry.score_types, "slug", SCORE_TYPE_KEYS, "score_types.json", problems
    )
    if problems:
        return problems

    for taxid, taxon in taxa.items():
        if not isinstance(taxid, int) or isinstance(taxid, bool) or taxid <= 0:
            problems.append(f"taxon {taxid!r}: a taxid is a positive integer.")
        if not taxon["scientific_name"]:
            problems.append(f"taxon {taxid}: scientific_name is empty.")
        for link in ("parent", "merged_into"):
            if taxon[link] is not None and taxon[link] not in taxa:
                problems.append(f"taxon {taxid}: {link} {taxon[link]} is not listed.")
            if taxon[link] == taxid:
                problems.append(f"taxon {taxid}: {link} is itself.")
    if not problems and taxa_in_dependency_order(registry.taxa) is None:
        problems.append("taxa.json: parent and merged_into links form a cycle.")

    for slug, entity_type in types.items():
        problems += _check_slug("entity type", slug)
        _check_choice(entity_type, "taxon_policy", TaxonPolicy, slug, problems)
        _check_choice(entity_type, "identity_scheme", IdentityScheme, slug, problems)
        if not isinstance(entity_type["assembly_required"], bool):
            problems.append(f"entity type {slug}: assembly_required is true or false.")
        if not entity_type["label"] or not entity_type["definition"]:
            problems.append(f"entity type {slug}: needs a label and a definition.")

    prefixes = defaultdict(list)
    for slug, namespace in namespaces.items():
        problems += _check_namespace(slug, namespace, taxa, types)
        prefixes[str(namespace["curie_prefix"]).lower()].append(slug)
    for prefix, slugs in prefixes.items():
        if len(slugs) > 1:
            problems.append(f"namespaces {', '.join(slugs)} share prefix {prefix!r}.")
    problems += _check_identity_namespaces(namespaces)
    for slug, relationship_type in relationship_types.items():
        problems += _check_relationship_type(slug, relationship_type, types)
    for slug, score_type in score_types.items():
        problems += _check_score_type(slug, score_type)
    return problems


def taxa_in_dependency_order(taxa) -> list[dict] | None:
    """The taxa with every parent and merged_into target before the taxa that
    point at it, or None if the links form a cycle."""
    remaining = {taxon["taxid"]: taxon for taxon in taxa}
    ordered = []
    while remaining:
        ready = [
            taxid
            for taxid, taxon in remaining.items()
            if all(
                taxon[link] is None or taxon[link] not in remaining
                for link in ("parent", "merged_into")
            )
        ]
        if not ready:
            return None
        for taxid in sorted(ready):
            ordered.append(remaining.pop(taxid))
    return ordered


def _read(path: Path, problems: list[str]) -> tuple[dict, ...]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{path.name}: {exc}")
        return ()
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        problems.append(f"{path.name}: expected a list of objects.")
        return ()
    return tuple(entries)


def _keyed(entries, key, keys, filename, problems) -> dict:
    keyed = {}
    for entry in entries:
        if not isinstance(entry.get(key), (str, int)):
            problems.append(f"{filename}: an entry has no usable {key}.")
            continue
        if set(entry) != keys:
            missing, extra = keys - set(entry), set(entry) - keys
            problems.append(
                f"{filename}: entry {entry.get(key)!r} has missing keys "
                f"{sorted(missing)} and unknown keys {sorted(extra)}."
            )
            continue
        if entry[key] in keyed:
            problems.append(f"{filename}: {entry[key]!r} is listed twice.")
        keyed[entry[key]] = entry
    return keyed


def _check_slug(kind, slug) -> list[str]:
    if isinstance(slug, str) and SLUG.fullmatch(slug):
        return []
    return [f"{kind} {slug!r}: a slug is lower case, at most 50 characters."]


def _check_choice(entry, field, choices, slug, problems):
    if entry[field] not in choices.values:
        problems.append(
            f"{slug}: {field} {entry[field]!r} is not one of {', '.join(choices.values)}."
        )


def _check_namespace(slug, namespace, taxa, types) -> list[str]:
    problems = _check_slug("namespace", slug)
    for field, choices in (
        ("scope", Scope),
        ("case_policy", CasePolicy),
        ("version_policy", VersionPolicy),
    ):
        _check_choice(namespace, field, choices, slug, problems)
    if not isinstance(namespace["curie_prefix"], str) or not CURIE_PREFIX.fullmatch(
        namespace["curie_prefix"]
    ):
        problems.append(f"namespace {slug}: curie_prefix is a lower-case CURIE prefix.")
    if not isinstance(namespace["defines_identity"], bool):
        problems.append(f"namespace {slug}: defines_identity is true or false.")
    if not namespace["label"] or not namespace["authority"]:
        problems.append(f"namespace {slug}: needs a label and an authority.")

    allowed = namespace["allowed_entity_types"]
    if not isinstance(allowed, list) or not allowed:
        problems.append(f"namespace {slug}: allowed_entity_types is a non-empty list.")
        allowed = []
    for type_slug in allowed:
        if type_slug not in types:
            problems.append(
                f"namespace {slug}: entity type {type_slug!r} is not listed."
            )
    if GENE_TYPES & set(allowed) and PROTEIN_TYPES & set(allowed):
        problems.append(f"namespace {slug}: identifies both genes and proteins.")

    fixed = namespace["fixed_taxon"]
    if fixed is not None and fixed not in taxa:
        problems.append(f"namespace {slug}: fixed_taxon {fixed} is not listed.")
    if fixed is not None and namespace["scope"] != Scope.TAXON:
        problems.append(f"namespace {slug}: only a taxon-scoped namespace is fixed.")
    if namespace["defines_identity"] and not (
        namespace["scope"] == Scope.GLOBAL
        or (namespace["scope"] == Scope.TAXON and fixed is not None)
    ):
        problems.append(
            f"namespace {slug}: defines identity, so it is global or fixed to a taxon."
        )

    template = namespace["url_template"]
    if template and "{lookup}" not in template:
        problems.append(f"namespace {slug}: url_template has no {{lookup}}.")
    try:
        re.compile(namespace["pattern"], re.ASCII)
    except (re.error, TypeError) as exc:
        problems.append(f"namespace {slug}: pattern does not compile ({exc}).")
        return problems
    examples = namespace["examples"]
    if not isinstance(examples, list) or not examples:
        problems.append(f"namespace {slug}: examples is a non-empty list.")
        return problems
    rules = SimpleNamespace(**namespace, fixed_taxon_id=fixed)
    for example in examples:
        try:
            normalize(rules, example)
        except (InvalidIdentifier, AttributeError, TypeError):
            problems.append(f"namespace {slug}: rejects its example {example!r}.")
    return problems


def _check_identity_namespaces(namespaces) -> list[str]:
    """At most one namespace defines identity per entity type and taxon: no
    two global ones, and no two fixed to one taxon, for the same type."""
    defining = defaultdict(list)
    for slug, namespace in namespaces.items():
        allowed = namespace["allowed_entity_types"]
        if namespace["defines_identity"] is True and isinstance(allowed, list):
            for type_slug in allowed:
                defining[type_slug, namespace["fixed_taxon"]].append(slug)
    return [
        f"{', '.join(slugs)} all define identity for {type_slug}"
        + (f" in taxon {taxid}." if taxid is not None else " globally.")
        for (type_slug, taxid), slugs in sorted(
            defining.items(), key=lambda item: (item[0][0], item[0][1] or 0)
        )
        if len(slugs) > 1
    ]


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _fits(entry, field, limit, where, problems):
    value = entry[field]
    if not isinstance(value, str) or len(value) > limit:
        problems.append(f"{where}: {field} is text of at most {limit} characters.")


def _check_relationship_type(slug, entry, types) -> list[str]:
    problems = _check_slug("relationship type", slug)
    where = f"relationship type {slug}"
    for field, choices in (
        ("family", RelationshipFamily),
        ("orientation", Orientation),
        ("polarity", Polarity),
    ):
        _check_choice(entry, field, choices, slug, problems)
    if not _text(entry["label"]) or not _text(entry["definition"]):
        problems.append(f"{where}: needs a label and a definition.")
    _fits(entry, "label", 100, where, problems)
    for field in ("subject_role_label", "object_role_label"):
        if not _text(entry[field]):
            problems.append(f"{where}: needs a {field}.")
        _fits(entry, field, 50, where, problems)
    _fits(entry, "display_token", 50, where, problems)
    for field in ("self_pairs_allowed", "is_reference"):
        if not isinstance(entry[field], bool):
            problems.append(f"{where}: {field} is true or false.")

    sides = {}
    for field in ("allowed_subject_types", "allowed_object_types"):
        allowed = entry[field]
        if (
            not isinstance(allowed, list)
            or not allowed
            or not all(isinstance(type_slug, str) for type_slug in allowed)
        ):
            problems.append(f"{where}: {field} is a non-empty list of entity types.")
            continue
        for type_slug in allowed:
            if type_slug not in types:
                problems.append(f"{where}: entity type {type_slug!r} is not listed.")
        sides[field] = set(allowed)
    if (
        entry["orientation"] == Orientation.SYMMETRIC
        and len(sides) == 2
        and sides["allowed_subject_types"] != sides["allowed_object_types"]
    ):
        problems.append(
            f"{where}: is symmetric, so its subject and object types are the same."
        )
    if entry["polarity"] == Polarity.EFFECT and (
        entry["orientation"] != Orientation.DIRECTED
    ):
        problems.append(f"{where}: effect polarity belongs to directed types only.")
    reference = entry["family"] == RelationshipFamily.REFERENCE
    if entry["is_reference"] is not reference:
        problems.append(
            f"{where}: is_reference is true for the reference family, and only "
            "for it."
        )
    if reference and (
        entry["orientation"] != Orientation.ROLE_ORDERED
        or entry["polarity"] != Polarity.NONE
    ):
        problems.append(f"{where}: a reference type is role_ordered, polarity none.")
    qualifiers = entry["identity_qualifiers"]
    if not isinstance(qualifiers, list) or qualifiers:
        problems.append(
            f"{where}: identity_qualifiers is [] until identity qualifiers arrive "
            "(P2)."
        )
    anchors = entry["psi_mi_anchors"]
    if not isinstance(anchors, list) or not all(
        isinstance(anchor, str) and PSI_MI_CURIE.fullmatch(anchor) for anchor in anchors
    ):
        problems.append(
            f"{where}: psi_mi_anchors is a list of PSI-MI CURIEs (MI: and four "
            "digits)."
        )
    return problems


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _check_score_type(slug, entry) -> list[str]:
    problems = []
    if (
        not isinstance(slug, str)
        or len(slug) > 64
        or not SCORE_TYPE_SLUG.fullmatch(slug)
    ):
        problems.append(
            f"score type {slug!r}: a slug is <source>:<name> or stats:<name>, in "
            "lower case, at most 64 characters."
        )
    where = f"score type {slug}"
    _check_choice(entry, "value_kind", ScoreValueKind, slug, problems)
    _check_choice(entry, "direction", ScoreDirection, slug, problems)
    if not _text(entry["label"]) or not _text(entry["definition"]):
        problems.append(f"{where}: needs a label and a definition.")
    _fits(entry, "label", 100, where, problems)
    _fits(entry, "unit", 50, where, problems)
    if not isinstance(entry["null_model"], str):
        problems.append(f"{where}: null_model is text.")
    if not _text(entry["defining_document"]):
        problems.append(f"{where}: needs a defining_document.")
    if not isinstance(entry["comparable_across_datasets"], bool):
        problems.append(f"{where}: comparable_across_datasets is true or false.")

    bounds = {}
    for field in ("range_min", "range_max"):
        value = entry[field]
        if value is None:
            continue
        if not _finite_number(value):
            problems.append(
                f"{where}: {field} is a finite number or null, not {value!r}."
            )
            continue
        bounds[field] = value
    if len(bounds) == 2 and bounds["range_min"] > bounds["range_max"]:
        problems.append(f"{where}: range_min is at most range_max.")
    kind, direction = entry["value_kind"], entry["direction"]
    if kind in UNIT_INTERVAL_KINDS and not all(0 <= v <= 1 for v in bounds.values()):
        problems.append(f"{where}: a {kind}'s range lies within [0, 1].")
    if kind in (ScoreValueKind.P_VALUE, ScoreValueKind.Q_VALUE) and (
        direction != ScoreDirection.LOWER_IS_STRONGER
    ):
        problems.append(f"{where}: a {kind} is lower_is_stronger.")
    if direction == ScoreDirection.SIGNED and (
        kind != ScoreValueKind.CONTINUOUS or not _text(entry["null_model"])
    ):
        problems.append(
            f"{where}: a signed score is continuous, and states the null_model its "
            "sign is relative to."
        )
    return problems
