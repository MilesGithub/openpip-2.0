"""Presentation schema v1: how a browser instance asks to be shown, as a
closed JSON object (BrowserInstance.presentation).

{} means every default, and a key left out takes its default: version 1;
the network's default layers and filters; every filter and evidence panel;
the cose layout; the entity's primary label (node_label_namespace null); the
blue accent. Any key not in KEYS is refused, and version 1 is required once
another key is given. No value is free text, a URL, HTML or CSS: each is a
name from a list here or from a registry.

validate_presentation checks, in KEYS order, each key's JSON type before any
membership or distinctness test, so nothing hashes or compares a value of
the wrong type. It reads each registry it needs once, with values_list, and
compares in Python: a submitted value never reaches a filter, where a NUL
would be a database error. The lists here are provisional, and the
frontend's must agree with them. The API returns presentation verbatim on
the detail route; F2, F3 and X1 read it.
"""

from django.core.exceptions import ValidationError

from graph.api.limits import MAX_LAYERS
from graph.models import (
    EvidenceBasis,
    IdentifierNamespace,
    InteractionLevel,
    Outcome,
    RelationshipType,
)
from graph.neighbourhood import DIRECTIONS
from graph.summary import EFFECT_KEYS

VERSION = 1
KEYS = (
    "version",
    "default_layers",
    "default_filters",
    "filter_panels",
    "layout",
    "evidence_panels",
    "node_label_namespace",
    "accent",
)
FILTER_PANELS = (
    "layers",
    "direction",
    "outcome",
    "evidence_basis",
    "effect",
    "method",
    "level",
    "score",
    "context",
    "taxon",
)
EVIDENCE_PANELS = (
    "summary",
    "evidence",
    "publications",
    "scores",
    "contexts",
    "record",
)
LAYOUTS = ("cose", "cola", "concentric", "circle", "grid", "breadthfirst")
ACCENTS = ("blue", "teal", "green", "amber", "red", "purple", "grey")
DEFAULT_FILTER_NAMES = ("direction", "outcome", "evidence_basis", "effect", "level")
# As the network's outcome parameter, "all" stands alone.
ALL_OUTCOMES = "all"
# The admin's presentation field refuses longer text before parsing it.
MAX_PRESENTATION_CHARS = 8192

NOT_OBJECT = "presentation is an object."
NOT_VERSION_1 = "presentation.version is the number 1."
UNKNOWN_KEY = "presentation has no key '{key}'."
NOT_A_LIST = "presentation.{key} is a list of names."
UNKNOWN_VALUE = "presentation.{key} names '{value}', which is not one of its values."
REPEATED_VALUE = "presentation.{key} names each value once."
TOO_MANY_LAYERS = f"presentation.default_layers names at most {MAX_LAYERS} layers."
FILTERS_NOT_OBJECT = "presentation.default_filters is an object."
UNKNOWN_FILTER = "presentation.default_filters has no key '{key}'."
DIRECTION_NOT_ONE = (
    f"presentation.default_filters.direction is one of: {', '.join(DIRECTIONS)}."
)
OUTCOME_ALL_ALONE = (
    "presentation.default_filters.outcome is all alone or a list of outcomes."
)
NOT_ONE_OF = "presentation.{key} is one of: {values}."
TOO_LONG = f"presentation is at most {MAX_PRESENTATION_CHARS:,} characters."
NOT_JSON = "presentation is not valid JSON."


def validate_presentation(value):
    """value is a presentation object of schema v1 (the module docstring),
    or raises a ValidationError listing every problem."""
    if not isinstance(value, dict):
        raise ValidationError(NOT_OBJECT, code="presentation")
    if not value:
        return
    problems = []
    version = value.get("version")
    # true and 1.0 equal 1 in Python, but are not the JSON integer 1.
    if isinstance(version, bool) or not isinstance(version, int) or version != VERSION:
        problems.append(NOT_VERSION_1)
    problems += [UNKNOWN_KEY.format(key=key) for key in value if key not in KEYS]
    if "default_layers" in value:
        problems += _layers(value["default_layers"])
    if "default_filters" in value:
        problems += _filters(value["default_filters"])
    if "filter_panels" in value:
        problems += _names("filter_panels", value["filter_panels"], FILTER_PANELS)
    if "layout" in value:
        problems += _one_of("layout", value["layout"], LAYOUTS)
    if "evidence_panels" in value:
        problems += _names("evidence_panels", value["evidence_panels"], EVIDENCE_PANELS)
    if "node_label_namespace" in value:
        problems += _namespace(value["node_label_namespace"])
    if "accent" in value:
        problems += _one_of("accent", value["accent"], ACCENTS)
    if problems:
        raise ValidationError(
            [ValidationError(problem, code="presentation") for problem in problems]
        )


def _is_names(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _unknown_and_repeated(key, value, known) -> list:
    """The sentences for the names of `value` that are not in `known`, each
    once, then the one for a name given twice."""
    problems = [
        UNKNOWN_VALUE.format(key=key, value=item)
        for item in dict.fromkeys(value)
        if item not in known
    ]
    if len(set(value)) < len(value):
        problems.append(REPEATED_VALUE.format(key=key))
    return problems


def _names(key, value, known) -> list:
    """A list of distinct names from `known`."""
    if not _is_names(value):
        return [NOT_A_LIST.format(key=key)]
    return _unknown_and_repeated(key, value, known)


def _one_of(key, value, known) -> list:
    if isinstance(value, str) and value in known:
        return []
    return [NOT_ONE_OF.format(key=key, values=", ".join(known))]


def _layers(value) -> list:
    """Registered relationship types and family:<name> of a family some
    registered type has: one read of the registry."""
    if not _is_names(value):
        return [NOT_A_LIST.format(key="default_layers")]
    problems = []
    if value:
        known = set()
        for slug, family in RelationshipType.objects.values_list("slug", "family"):
            known.update((slug, f"family:{family}"))
        problems += _unknown_and_repeated("default_layers", value, known)
    if len(value) > MAX_LAYERS:
        problems.append(TOO_MANY_LAYERS)
    return problems


def _filters(value) -> list:
    """The network's filters, each in the network's own domain."""
    if not isinstance(value, dict):
        return [FILTERS_NOT_OBJECT]
    problems = [
        UNKNOWN_FILTER.format(key=key)
        for key in value
        if key not in DEFAULT_FILTER_NAMES
    ]
    if "direction" in value:
        direction = value["direction"]
        if not (isinstance(direction, str) and direction in DIRECTIONS):
            problems.append(DIRECTION_NOT_ONE)
    if "outcome" in value:
        problems += _outcome(value["outcome"])
    for name, known in (
        ("evidence_basis", EvidenceBasis.values),
        ("effect", EFFECT_KEYS),
        ("level", InteractionLevel.values),
    ):
        if name in value:
            problems += _names(f"default_filters.{name}", value[name], known)
    return problems


def _outcome(value) -> list:
    key = "default_filters.outcome"
    if not _is_names(value):
        return [NOT_A_LIST.format(key=key)]
    problems = _unknown_and_repeated(key, value, (*Outcome.values, ALL_OUTCOMES))
    if ALL_OUTCOMES in value and len(value) > 1:
        problems.insert(0, OUTCOME_ALL_ALONE)
    return problems


def _namespace(value) -> list:
    """A registered identifier namespace, or null: one read of the
    registry, unless null."""
    if value is None:
        return []
    known = sorted(IdentifierNamespace.objects.values_list("slug", flat=True))
    return _one_of("node_label_namespace", value, known)
