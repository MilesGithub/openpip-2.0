"""A reviewed resolution file: the mappings a person chose for identifiers
that the resolver cannot settle (architecture §4.1, basis curated).

    {"resolutions": [{"namespace": "...", "value": "...", "entity_type": "...",
                      "taxon": 9606, "entity": 42,
                      "target_namespace": "...", "target_value": "...",
                      "rationale": "...", "reviewer": "..."}]}

An operator gives it to a dry run (graph_import --resolutions); the run keeps
its SHA-256 and its entries in its parameters. It is strict JSON
(jsonfile.py), and every entry is checked before the run starts, so an
invalid file writes nothing:

- at most limits.MAX_RESOLUTION_ENTRIES entries, each with exactly the keys
  above; value and target_value at most 255 characters, reviewer at most
  150, rationale non-blank and at most 10,000; entity an id (1 to 2^63-1);
  taxon a taxid (1 to 2^31-1) or null;
- the identifier an entry states passes graph.entities.identifier_problem
  for its type and taxon, so an isoform is never mapped to its entry this
  way, and a namespace's fixed taxon is never contradicted;
- the target identifier resolves (status resolved, graph.resolution) to
  exactly the entry's entity, which is active or provisional and of the
  entry's type and taxon: an id changed by a typo, or taken from another
  database, is caught;
- no two entries state one identifier: one (namespace, lookup, type, taxon).

Whether an entry applies to an identifier is the shared mapping's decision
(graph.ingest.mapping): only where the resolver gives no exact match.
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from graph.entities import identifier_problems
from graph.identity import normalize
from graph.models import EntityStatus, IdentifierNamespace
from graph.resolution import RESOLVED, resolve_many

from . import limits
from .jsonfile import JsonFileError, fields, integer, load_strict_json, text
from .protocol import quoted

ENTRY_KEYS = (
    "namespace",
    "value",
    "entity_type",
    "taxon",
    "entity",
    "target_namespace",
    "target_value",
    "rationale",
    "reviewer",
)
SLUG_LENGTH = 50
VALUE_LENGTH = 255
REVIEWER_LENGTH = 150
RATIONALE_LENGTH = 10_000
LARGEST_ID = 2**63 - 1
LARGEST_TAXID = 2**31 - 1
# How many entries' problems a refusal lists.
SHOWN_PROBLEMS = 20
MAPPABLE = (EntityStatus.ACTIVE, EntityStatus.PROVISIONAL)


class ResolutionError(JsonFileError):
    """The resolution file is not valid: nothing is written."""


@dataclass(frozen=True)
class ResolutionEntry:
    """One reviewed mapping: the identifier as stated, and the entity it
    names."""

    # 1-based, in file order.
    index: int
    namespace: str
    value: str
    # The value normalized by its namespace's rules (graph.identity).
    lookup: str
    entity_type: str
    taxon: int | None
    entity: int
    target_namespace: str
    target_value: str
    rationale: str
    reviewer: str

    @property
    def key(self) -> tuple:
        """The identifier the entry maps: (namespace, lookup, type, taxon)."""
        return (self.namespace, self.lookup, self.entity_type, self.taxon)

    def as_json(self) -> dict:
        return {
            "index": self.index,
            "namespace": self.namespace,
            "value": self.value,
            "entity_type": self.entity_type,
            "taxon": self.taxon,
            "entity": self.entity,
            "target_namespace": self.target_namespace,
            "target_value": self.target_value,
            "rationale": self.rationale,
            "reviewer": self.reviewer,
        }


@dataclass(frozen=True)
class Resolutions:
    entries: tuple[ResolutionEntry, ...]
    # Of the file's bytes.
    sha256: str

    __hash__ = None

    def __post_init__(self):
        object.__setattr__(
            self, "_by_key", {entry.key: entry for entry in self.entries}
        )

    def get(self, namespace, lookup, entity_type, taxon) -> ResolutionEntry | None:
        """The entry for this identifier, if the file has one."""
        return self._by_key.get((namespace, lookup, entity_type, taxon))

    def as_json(self) -> dict:
        """As a run's parameters keep it."""
        return {
            "sha256": self.sha256,
            "entries": [entry.as_json() for entry in self.entries],
        }


# A dry run without a resolution file.
NO_RESOLUTIONS = Resolutions((), "")


def load_resolutions(data: bytes) -> Resolutions:
    """The resolution file these bytes hold, every entry checked (the
    module docstring says how). Raises ResolutionError, listing the entries
    at fault. Read-only, with a constant number of queries per target
    namespace."""
    value = load_strict_json(data, what="the resolution file", error=ResolutionError)
    fields(
        value,
        where="The resolution file",
        required=("resolutions",),
        error=ResolutionError,
    )
    listed = value["resolutions"]
    if not isinstance(listed, list):
        raise ResolutionError('The resolution file\'s "resolutions" is a JSON list.')
    most = limits.MAX_RESOLUTION_ENTRIES
    if len(listed) > most:
        raise ResolutionError(
            f"The resolution file has {len(listed)} entries, more than the {most} "
            "a run keeps in its parameters."
        )
    stated = [_entry(n, entry) for n, entry in enumerate(listed, 1)]
    problems = {}
    checked = identifier_problems(
        (entry["entity_type"], entry["taxon"], entry["namespace"], entry["value"])
        for entry in stated
    )
    for entry, problem in zip(stated, checked):
        if problem:
            problems[entry["index"]] = f"{problem}"
    entries = _normalized(stated, problems)
    _check_unique(entries, problems)
    _check_targets(entries, problems)
    if problems:
        raise ResolutionError(_listing(problems))
    return Resolutions(tuple(entries), hashlib.sha256(data).hexdigest())


def _entry(n, value) -> dict:
    """The entry's values, each of its kind; anything else raises."""
    where = f"Entry {n} of the resolution file"
    fields(value, where=where, required=ENTRY_KEYS, error=ResolutionError)

    def checked(name, limit):
        return text(
            value[name], where=f"{where}: {name}", limit=limit, error=ResolutionError
        )

    return {
        "index": n,
        "namespace": checked("namespace", SLUG_LENGTH),
        "value": checked("value", VALUE_LENGTH),
        "entity_type": checked("entity_type", SLUG_LENGTH),
        "taxon": integer(
            value["taxon"],
            where=f"{where}: taxon",
            low=1,
            high=LARGEST_TAXID,
            null=True,
            error=ResolutionError,
        ),
        "entity": integer(
            value["entity"],
            where=f"{where}: entity",
            low=1,
            high=LARGEST_ID,
            error=ResolutionError,
        ),
        "target_namespace": checked("target_namespace", SLUG_LENGTH),
        "target_value": checked("target_value", VALUE_LENGTH),
        "rationale": checked("rationale", RATIONALE_LENGTH),
        "reviewer": checked("reviewer", REVIEWER_LENGTH),
    }


def _normalized(stated, problems) -> list:
    """The entries whose identifier passed, with its lookup."""
    rows = {
        row.slug: row
        for row in IdentifierNamespace.objects.filter(
            slug__in={entry["namespace"] for entry in stated}
        )
    }
    entries = []
    for entry in stated:
        if entry["index"] in problems:
            continue
        lookup, _ = normalize(rows[entry["namespace"]], entry["value"])
        entries.append(ResolutionEntry(lookup=lookup, **entry))
    return entries


def _check_unique(entries, problems):
    first = {}
    for entry in entries:
        held = first.setdefault(entry.key, entry.index)
        if held != entry.index:
            problems[entry.index] = (
                f"it maps {entry.namespace} {entry.lookup} ({entry.entity_type}, "
                f"taxon {entry.taxon}) again, as entry {held} does: one entry per "
                "identifier."
            )


def _check_targets(entries, problems):
    """Each target identifier resolves to exactly its entry's entity, of the
    entry's type and taxon, active or provisional."""
    by_namespace = defaultdict(list)
    for entry in entries:
        if entry.index not in problems:
            by_namespace[entry.target_namespace].append(entry)
    for namespace, group in sorted(by_namespace.items()):
        try:
            found = resolve_many(
                [entry.target_value for entry in group], namespace=namespace
            )
        except ValueError:
            for entry in group:
                problems[entry.index] = (
                    f"its target namespace {quoted(namespace)} is not in the registry."
                )
            continue
        for entry, resolution in zip(group, found):
            problem = _target_problem(entry, resolution)
            if problem:
                problems[entry.index] = problem


def _target_problem(entry, resolution) -> str | None:
    target = f"{entry.target_namespace} {quoted(entry.target_value)}"
    if resolution.status != RESOLVED:
        listed = ", ".join(
            str(candidate.entity.pk) for candidate in resolution.candidates[:5]
        )
        return (
            f"its target {target} is {resolution.status}"
            + (f" (entities {listed})" if listed else "")
            + ", not resolved to one entity."
        )
    entity = resolution.candidates[0].entity
    if entity.pk != entry.entity:
        return (
            f"its target {target} resolves to entity {entity.pk}, not to entity "
            f"{entry.entity}."
        )
    if entity.status not in MAPPABLE:
        return f"entity {entity.pk} is {entity.status}: it maps nothing."
    if (entity.entity_type_id, entity.taxon_id) != (entry.entity_type, entry.taxon):
        return (
            f"entity {entity.pk} is a {entity.entity_type_id} of taxon "
            f"{entity.taxon_id}, and the entry states a {entry.entity_type} of taxon "
            f"{entry.taxon}."
        )
    return None


def _listing(problems) -> str:
    shown = sorted(problems)[:SHOWN_PROBLEMS]
    lines = [f"entry {n}: {problems[n]}" for n in shown]
    if len(problems) > len(shown):
        lines.append(f"... and {len(problems) - len(shown)} more entries.")
    return "The resolution file is not valid; nothing was written.\n" + "\n".join(lines)
