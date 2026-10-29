"""Which entities a term names. Read-only, and it never guesses.

A status counts distinct entities, so one entity matched several ways (by
its label and an alias, say) counts once:

- resolved:  one entity, and one of its matches is an exact primary match;
- candidate: one entity, matched only some other way, to be confirmed. The
  match may be an alias, symbol, cross-reference, secondary accession or
  label, or a primary identifier that differs in version, or in case in a
  case-exact namespace that holds no exact match;
- ambiguous: two or more entities, in any roles. Exact primary matches are
  listed first and the rest grouped by entity type and taxon, but none is
  chosen: the caller must choose;
- unmatched: no entity.

Nothing in typed code takes the first of several candidates, or treats a
candidate as resolved. Which entities a caller may see arrives with the
release model (G2) and the API (A1); until then every entity is considered.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models.functions import Upper

from .identity import (
    InvalidIdentifier,
    matches_pattern,
    normalize,
    parse_prefixed,
    split_version,
)
from .models import (
    CasePolicy,
    Entity,
    EntityIdentifier,
    EntityType,
    IdentifierNamespace,
    IdentifierRole,
    Taxon,
)

RESOLVED = "resolved"
CANDIDATE = "candidate"
AMBIGUOUS = "ambiguous"
UNMATCHED = "unmatched"

LABEL = "label"
# Match kinds, in the order a candidate lists them.
MATCH_KINDS = (
    IdentifierRole.PRIMARY,
    IdentifierRole.SECONDARY,
    IdentifierRole.XREF,
    IdentifierRole.SYMBOL,
    IdentifierRole.ALIAS,
    LABEL,
)


@dataclass(frozen=True)
class Match:
    """One way a term matched an entity."""

    # An identifier role, or "label".
    kind: str
    # The namespace slug; None for a label.
    namespace: str | None
    # The identifier as the source wrote it, or the label.
    value: str
    # False when the term matched only by ignoring case.
    case_exact: bool
    # True when the term and the identifier state different versions.
    version_mismatch: bool


@dataclass(frozen=True)
class Candidate:
    entity: Entity
    matches: tuple[Match, ...]
    # One of the matches is an exact primary match: in the defining
    # namespace's own terms, the term is this entity's identifier.
    exact_primary: bool


@dataclass(frozen=True)
class Resolution:
    status: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class _Probe:
    """A term read as a value of one namespace."""

    namespace: IdentifierNamespace
    # None when only a case-insensitive match is possible: in a case-exact
    # namespace, "syn-tf1" is no value, but may be SYN-TF1 typed in lower case.
    lookup: str | None
    upper: str
    version: str
    # The value as given, version removed, case kept: for case_exact.
    written: str


@dataclass(frozen=True)
class _Plan:
    text: str
    probes: tuple[_Probe, ...]
    # Set when the term is also searched for as a label.
    label_key: str | None


def resolve(
    term: str, namespace=None, taxon=None, entity_type=None, limit: int = 20
) -> Resolution:
    """Resolve one term. See resolve_many."""
    return resolve_many(
        [term], namespace=namespace, taxon=taxon, entity_type=entity_type, limit=limit
    )[0]


def resolve_many(
    terms, namespace=None, taxon=None, entity_type=None, limit: int = 20
) -> list[Resolution]:
    """A Resolution per term, in order, from a constant number of queries.

    namespace: a slug or row. Given, the term is read in that namespace only
      (its own prefix may be written), and labels are not searched. Not
      given, a term with a known prefix ("uniprotkb:P04637", "HGNC:5") is read
      in that namespace; a term with an unknown prefix is unmatched; any other
      term is read in every namespace whose rules accept it, except that a
      bare number is never an HGNC ID, and is also compared with labels.
    taxon, entity_type: filters, as a taxid or row and a slug or row.
    limit: the most candidates returned per term. The status still counts
      every matching entity.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    named, namespaces = _namespaces(namespace)
    taxon_id = _taxon_id(taxon)
    type_slug = _entity_type_slug(entity_type)

    plans = [_plan(term, named, namespaces) for term in terms]
    probes = [probe for plan in plans for probe in plan.probes]
    exact = _exact_matches(probes, taxon_id, type_slug)
    ignoring_case = _case_insensitive_matches(probes, taxon_id, type_slug)
    labels = _label_matches(plans, taxon_id, type_slug)
    return [_resolution(plan, exact, ignoring_case, labels, limit) for plan in plans]


def lookups_ignoring_case(upper_lookups):
    """Identifiers whose lookup, upper-cased, is one of these: the query that
    finds case-mismatch candidates, served by graph_ident_lookup_upper."""
    return EntityIdentifier.objects.annotate(lookup_upper=Upper("lookup")).filter(
        lookup_upper__in=upper_lookups
    )


# ── Reading the terms ───────────────────────────────────────────────────────


def _plan(term, named, namespaces) -> _Plan:
    text = term.strip()
    if not text:
        return _Plan(text, (), None)
    prefix, rest = parse_prefixed(text)
    if named is not None:
        if prefix is not None and prefix == named.curie_prefix.lower():
            text = rest
        return _Plan(text, _probes([named], text, explicit=True), None)
    if prefix is not None:
        prefixed = [ns for ns in namespaces if ns.curie_prefix.lower() == prefix]
        # An unknown prefix: unmatched, and never re-read as a name.
        return _Plan(rest, _probes(prefixed, rest, explicit=True), None)
    return _Plan(text, _probes(namespaces, text, explicit=False), text.upper()[:255])


def _probes(namespaces, text, explicit) -> tuple[_Probe, ...]:
    probes = []
    for namespace in namespaces:
        written, version = split_version(namespace, text)
        try:
            lookup, version = normalize(namespace, text, explicit=explicit)
        except InvalidIdentifier:
            # A case-exact namespace may still hold the value in another case.
            if namespace.case_policy != CasePolicy.EXACT or not matches_pattern(
                namespace, written, ignore_case=True
            ):
                continue
            lookup = None
        upper = (lookup or written).upper()
        probes.append(_Probe(namespace, lookup, upper, version, written))
    return tuple(probes)


# ── The three queries ───────────────────────────────────────────────────────
# Each filters on one indexed column and leaves the namespace to Python, so
# the planner has one obvious index: (lookup), Upper("lookup") and label_key.


def _exact_matches(probes, taxon_id, type_slug):
    keys = {(p.namespace.slug, p.lookup) for p in probes if p.lookup is not None}
    if not keys:
        return {}
    rows = EntityIdentifier.objects.filter(lookup__in={lookup for _, lookup in keys})
    found = defaultdict(list)
    for row in _for_entities(rows, "entity__", taxon_id, type_slug).select_related(
        "entity"
    ):
        if (row.namespace_id, row.lookup) in keys:
            found[row.namespace_id, row.lookup].append(row)
    return found


def _case_insensitive_matches(probes, taxon_id, type_slug):
    keys = {
        (p.namespace.slug, p.upper)
        for p in probes
        if p.namespace.case_policy == CasePolicy.EXACT
    }
    if not keys:
        return {}
    rows = lookups_ignoring_case({upper for _, upper in keys})
    found = defaultdict(list)
    for row in _for_entities(rows, "entity__", taxon_id, type_slug).select_related(
        "entity"
    ):
        if (row.namespace_id, row.lookup_upper) in keys:
            found[row.namespace_id, row.lookup_upper].append(row)
    return found


def _label_matches(plans, taxon_id, type_slug):
    keys = {plan.label_key for plan in plans if plan.label_key is not None}
    if not keys:
        return {}
    found = defaultdict(list)
    rows = Entity.objects.filter(label_key__in=keys)
    for entity in _for_entities(rows, "", taxon_id, type_slug):
        found[entity.label_key].append(entity)
    return found


def _for_entities(queryset, path, taxon_id, type_slug):
    if taxon_id is not None:
        queryset = queryset.filter(**{f"{path}taxon_id": taxon_id})
    if type_slug is not None:
        queryset = queryset.filter(**{f"{path}entity_type_id": type_slug})
    return queryset


# ── Counting entities ───────────────────────────────────────────────────────


def _resolution(plan, exact, ignoring_case, labels, limit) -> Resolution:
    entities = {}
    matches = defaultdict(list)
    exact_primary = set()

    def add(entity, match, is_exact_primary):
        entities.setdefault(entity.pk, entity)
        matches[entity.pk].append(match)
        if is_exact_primary:
            exact_primary.add(entity.pk)

    # Case variants are matches only where nothing matches exactly: in a
    # case-exact namespace h (hairy) and H (Hairless) are two identifiers, not
    # one written two ways. The same goes for labels.
    for probe in plan.probes:
        namespace = probe.namespace
        rows = exact.get((namespace.slug, probe.lookup), ())
        for row in rows:
            mismatch = _version_mismatch(probe.version, row.version)
            add(
                row.entity,
                Match(
                    row.role,
                    namespace.slug,
                    row.value,
                    _case_exact(probe, row),
                    mismatch,
                ),
                row.role == IdentifierRole.PRIMARY and not mismatch,
            )
        if rows:
            continue
        for row in ignoring_case.get((namespace.slug, probe.upper), ()):
            add(
                row.entity,
                Match(
                    row.role,
                    namespace.slug,
                    row.value,
                    False,
                    _version_mismatch(probe.version, row.version),
                ),
                False,
            )
    labelled = labels.get(plan.label_key, ())
    exactly = [entity for entity in labelled if entity.label == plan.text]
    for entity in exactly or labelled:
        add(
            entity,
            Match(LABEL, None, entity.label, entity.label == plan.text, False),
            False,
        )

    candidates = sorted(
        (
            Candidate(
                entities[pk],
                tuple(sorted(matches[pk], key=_match_order)),
                pk in exact_primary,
            )
            for pk in entities
        ),
        key=_candidate_order,
    )
    if not candidates:
        status = UNMATCHED
    elif len(candidates) > 1:
        status = AMBIGUOUS
    elif candidates[0].exact_primary:
        status = RESOLVED
    else:
        status = CANDIDATE
    return Resolution(status, tuple(candidates[:limit]))


def _case_exact(probe, row) -> bool:
    # In an upper namespace case carries no meaning, and in a case-exact one
    # equal lookups are equal as written. An insensitive namespace matched
    # ignoring case, so say whether the case differed.
    if probe.namespace.case_policy == CasePolicy.INSENSITIVE:
        return probe.written == split_version(probe.namespace, row.value)[0]
    return True


def _version_mismatch(term_version, stored_version) -> bool:
    # Only two stated versions can disagree: an unversioned term names the
    # identifier itself, whatever version a source recorded.
    return bool(term_version and stored_version and term_version != stored_version)


def _match_order(match):
    return (
        MATCH_KINDS.index(match.kind),
        match.namespace or "",
        match.value,
        not match.case_exact,
        match.version_mismatch,
    )


def _candidate_order(candidate):
    entity = candidate.entity
    return (
        not candidate.exact_primary,
        entity.entity_type_id,
        entity.taxon_id is None,
        entity.taxon_id or 0,
        _match_order(candidate.matches[0]),
        entity.pk,
    )


# ── Arguments ───────────────────────────────────────────────────────────────


def _namespaces(namespace):
    if namespace is None:
        return None, list(IdentifierNamespace.objects.order_by("slug"))
    slug = namespace.slug if isinstance(namespace, IdentifierNamespace) else namespace
    found = list(IdentifierNamespace.objects.filter(slug=slug))
    if not found:
        raise ValueError(f"Unknown namespace {slug!r}.")
    return found[0], found


def _taxon_id(taxon):
    if taxon is None:
        return None
    taxid = taxon.taxid if isinstance(taxon, Taxon) else taxon
    if not Taxon.objects.filter(taxid=taxid).exists():
        raise ValueError(f"Taxon {taxid!r} is not in the registry.")
    return taxid


def _entity_type_slug(entity_type):
    if entity_type is None:
        return None
    slug = entity_type.slug if isinstance(entity_type, EntityType) else entity_type
    if not EntityType.objects.filter(slug=slug).exists():
        raise ValueError(f"Unknown entity type {slug!r}.")
    return slug
