"""The frontend's presentation lists against browsers.presentation's.

frontend/src/features/graph/network/presentation.ts copies this module's lists,
each on one line and in its order, between the lines
"// presentation-lists:start" and "// presentation-lists:end", with the default
layout and accent, which the API documents as the first of their lists
(docs/developer/api/typed-graph.md). The frontend reads an instance's
presentation by those copies, so a list changed on one side alone fails here:
N1 to N3, which add filters to FILTER_PANELS and DEFAULT_FILTER_NAMES, change
both files in one PR. No database.
"""

import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from browsers import presentation

FRONTEND = (
    Path(settings.BASE_DIR).parent
    / "frontend/src/features/graph/network/presentation.ts"
)
START = "// presentation-lists:start"
END = "// presentation-lists:end"
LIST_LINE = re.compile(r"^export const PRESENTATION_([A-Z_]+) = \[(.*)\] as const$")
DEFAULT_LINE = re.compile(r"^export const PRESENTATION_(DEFAULT_[A-Z]+) = '([^']*)'$")
# A findall alone would pass over an item quoted otherwise, so the whole
# bracket must be single-quoted names.
ITEMS = re.compile(r"\s*(?:'[^']*'(?:\s*,\s*'[^']*')*\s*,?)?\s*")
QUOTED = re.compile(r"'([^']*)'")
LISTS = (
    "KEYS",
    "FILTER_PANELS",
    "EVIDENCE_PANELS",
    "LAYOUTS",
    "ACCENTS",
    "DEFAULT_FILTER_NAMES",
)
# Containers often mount backend/ alone: these then skip, except in CI, where a
# missing frontend/ must fail, as in graph/tests/test_api_fixtures.py.
IN_CI = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
pytestmark = pytest.mark.skipif(
    not FRONTEND.is_file() and not IN_CI,
    reason="needs the full repository checkout",
)


def marked() -> tuple:
    """({name: (item, ...)}, {name: value}): the lists and the defaults stated
    between the marker lines."""
    lines = FRONTEND.read_text(encoding="utf-8").splitlines()
    for marker in (START, END):
        assert lines.count(marker) == 1, f"presentation.ts needs one line {marker!r}"
    start, end = lines.index(START), lines.index(END)
    assert start < end, f"presentation.ts has {END!r} before {START!r}"
    lists, defaults = {}, {}
    for line in lines[start + 1 : end]:
        found = LIST_LINE.match(line)
        if found:
            assert ITEMS.fullmatch(found[2]), f"not single-quoted names: {line!r}"
            lists[found[1]] = tuple(QUOTED.findall(found[2]))
        found = DEFAULT_LINE.match(line)
        if found:
            defaults[found[1]] = found[2]
    return lists, defaults


def test_each_list_is_browsers_presentations_in_order():
    lists, _ = marked()
    for name in LISTS:
        assert name in lists, f"presentation.ts has no one-line PRESENTATION_{name}"
        assert lists[name] == getattr(presentation, name), f"PRESENTATION_{name}"


def test_the_defaults_are_the_first_layout_and_accent():
    _, defaults = marked()
    for name in ("DEFAULT_LAYOUT", "DEFAULT_ACCENT"):
        assert name in defaults, f"presentation.ts has no line PRESENTATION_{name}"
    assert defaults["DEFAULT_LAYOUT"] == presentation.LAYOUTS[0] == "cose"
    assert defaults["DEFAULT_ACCENT"] == presentation.ACCENTS[0] == "blue"
