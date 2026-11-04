"""The adapters this code carries, by name, and the rules every adapter's
spec follows.

A release records the adapter it was registered with, with the adapter's
version and mapping version and kind (graph_register_release); its dry run
uses that adapter, at those versions (graph_import refuses otherwise).

validate_spec checks a spec, and a test runs it over every adapter:

- name, version and mapping version fit the release's columns, the version
  is semantic (1.0.0), and kind and record_key_basis are closed values;
- isoform_specific is a bool, and a note of the changes the adapter makes is
  given, because CC BY and CC BY-SA require one with each release's
  attribution;
- the accepted interaction types are a subset of G3's PSI-MI crosswalk
  (graph.terms.PHYSICAL_INTERACTION_LEVELS), each at the crosswalk's level;
- isoform_specific False (open question B12's isoform_entry mapping) and
  co-referent publication identifiers are declared by the protocol and
  refused until R2 and M1 implement them.
"""

import re

from graph.models import DatasetRelease, RecordKeyBasis, ReleaseKind
from graph.terms import PHYSICAL_INTERACTION_LEVELS, TermError, normalize_curie

from . import canonical_tsv
from .protocol import AdapterSpec

ADAPTERS = {
    canonical_tsv.ADAPTER.spec.name: canonical_tsv.ADAPTER,
}

ADAPTER_NAME = re.compile(r"[a-z][a-z0-9_]*")
SEMANTIC_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class UnknownAdapter(LookupError):
    """No adapter has this name."""


class AdapterSpecError(ValueError):
    """An adapter's spec breaks a rule of validate_spec."""


def get_adapter(name):
    """The adapter with this name, once its spec is sound."""
    adapter = ADAPTERS.get(name) if isinstance(name, str) else None
    if adapter is None:
        known = ", ".join(sorted(ADAPTERS))
        raise UnknownAdapter(f"Unknown adapter {name!r}: the adapters are {known}.")
    problems = validate_spec(adapter.spec)
    if problems:
        raise AdapterSpecError(f"Adapter {name}: " + " ".join(problems))
    return adapter


def validate_spec(spec) -> list[str]:
    """Every rule the spec breaks, as sentences; empty when it is sound."""
    if not isinstance(spec, AdapterSpec):
        return [f"A spec is an AdapterSpec, not {spec!r}."]
    problems = []
    lengths = {
        name: DatasetRelease._meta.get_field(name).max_length
        for name in ("adapter", "adapter_version", "mapping_version")
    }
    if (
        not isinstance(spec.name, str)
        or not ADAPTER_NAME.fullmatch(spec.name)
        or len(spec.name) > lengths["adapter"]
    ):
        problems.append(
            f"name {spec.name!r} is lower case, a letter then letters, digits or _, "
            f"at most {lengths['adapter']} characters."
        )
    if (
        not isinstance(spec.version, str)
        or not SEMANTIC_VERSION.fullmatch(spec.version)
        or len(spec.version) > lengths["adapter_version"]
    ):
        problems.append(f"version {spec.version!r} is a semantic version, as 1.0.0.")
    if (
        not isinstance(spec.mapping_version, str)
        or not spec.mapping_version.strip()
        or len(spec.mapping_version) > lengths["mapping_version"]
    ):
        problems.append(
            f"mapping_version {spec.mapping_version!r} is non-blank, at most "
            f"{lengths['mapping_version']} characters."
        )
    if spec.kind not in ReleaseKind.values:
        problems.append(
            f"kind is one of {', '.join(ReleaseKind.values)}, not {spec.kind!r}."
        )
    if not isinstance(spec.isoform_specific, bool):
        problems.append(
            f"isoform_specific is true or false, not {spec.isoform_specific!r}."
        )
    elif not spec.isoform_specific:
        problems.append(
            "isoform_specific false (an isoform recorded on its entry, open question "
            "B12) arrives with R2."
        )
    if not isinstance(spec.changes_note, str) or not spec.changes_note.strip():
        problems.append(
            "changes_note states the changes the adapter makes to the source's data."
        )
    if spec.record_key_basis not in RecordKeyBasis.values:
        problems.append(
            f"record_key_basis is one of {', '.join(RecordKeyBasis.values)}, not "
            f"{spec.record_key_basis!r}."
        )
    problems += _columns(spec)
    if spec.relationship_types is not None and not (
        isinstance(spec.relationship_types, frozenset)
        and all(isinstance(slug, str) for slug in spec.relationship_types)
    ):
        problems.append("relationship_types is None or a frozenset of slugs.")
    if not isinstance(spec.reference_types_only_if_synthetic, bool):
        problems.append("reference_types_only_if_synthetic is true or false.")
    problems += _interaction_levels(spec.interaction_levels)
    if not isinstance(spec.coreferent_publication_ids, bool):
        problems.append("coreferent_publication_ids is true or false.")
    elif spec.coreferent_publication_ids:
        problems.append(
            "coreferent_publication_ids (one paper under several identifiers in a "
            "row) arrives with M1."
        )
    return problems


def _columns(spec) -> list[str]:
    problems = []
    named = []
    for name in ("columns", "optional_columns", "column_prefixes"):
        value = getattr(spec, name)
        if not isinstance(value, tuple) or not all(
            isinstance(column, str) and column.strip() for column in value
        ):
            problems.append(f"{name} is a tuple of column names.")
            continue
        named.extend(value)
    repeated = sorted({column for column in named if named.count(column) > 1})
    if repeated:
        problems.append(f"columns are named once; {', '.join(repeated)} is not.")
    if not spec.columns:
        problems.append("columns lists the required columns.")
    return problems


def _interaction_levels(levels) -> list[str]:
    problems = []
    for curie, level in levels.items():
        try:
            canonical = normalize_curie(curie)
        except TermError as exc:
            problems.append(f"interaction_levels: {exc}")
            continue
        crosswalk = PHYSICAL_INTERACTION_LEVELS.get(canonical)
        if canonical != curie or crosswalk is None:
            problems.append(
                f"interaction_levels: {curie!r} is not a canonical CURIE of G3's "
                "PSI-MI crosswalk (graph.terms.PHYSICAL_INTERACTION_LEVELS)."
            )
        elif level != crosswalk[0]:
            problems.append(
                f"interaction_levels: {curie} is at the crosswalk's level, "
                f"{crosswalk[0]}, not {level!r}."
            )
    return problems
