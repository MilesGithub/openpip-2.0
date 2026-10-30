"""Score values: the one strict grammar a raw value is read by, and the
values a score type allows.

A source's raw value is always kept verbatim (EvidenceScore.raw_value); it is
a non-blank string of at most 255 characters without NUL. Its value is the
float the grammar reads, in ASCII digits only, or NULL with a note: NaN or
infinity in any spelling, a value that overflows a double, a censored value
(<x, <=x, >x, >=x, such as R's "< 2.2e-16"), and anything else the grammar
does not read. A non-zero value that underflows to zero or to a subnormal
keeps that value, with a note. A finite value must suit its type's kind and
lie within its range, or the claim is refused (graph.claims; the
EvidenceScore guard checks it again on every write path). A censored value's
bound (censored_bound) still settles a not-significant result's criterion
that every value within it meets (graph.claims, rule 8).
"""

import math
import re
import sys
from dataclasses import dataclass

from .models import ScoreValueKind

# A sign, digits with an optional fraction (or a fraction alone), and an
# optional exponent. re.ASCII: Python's \d also matches other scripts' digits,
# and float() reads them.
NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)
_CENSORED = re.compile(r"(<=|>=|<|>)\s*(.*)", re.ASCII)
_NOT_FINITE = re.compile(r"[+-]?(nan|inf|infinity)", re.ASCII | re.IGNORECASE)
RAW_MAX = 255
UNIT_INTERVAL = frozenset(
    {ScoreValueKind.PROBABILITY, ScoreValueKind.P_VALUE, ScoreValueKind.Q_VALUE}
)


@dataclass(frozen=True)
class ParsedScore:
    value: float | None
    # Why the value is NULL, or what reading it lost; "" when nothing.
    note: str = ""


def raw_value_problem(raw) -> str | None:
    """Why a raw value is not kept as a score's, or None."""
    if not isinstance(raw, str):
        return f"A score's raw value is text, not {raw!r}."
    if "\x00" in raw:
        return "A score's raw value never contains a NUL character (U+0000)."
    if not raw.strip():
        return "A score's raw value is never blank."
    if len(raw) > RAW_MAX:
        return f"A score's raw value is at most {RAW_MAX} characters."
    return None


def parse_score(raw: str) -> ParsedScore:
    """The value a raw value states, read by NUMBER after stripping; NULL with
    a note where it states none (the module docstring lists them)."""
    text = raw.strip()
    if NUMBER.fullmatch(text):
        value = float(text)
        if math.isinf(value):
            return ParsedScore(
                None, f"{raw!r} overflows a double, so its value is NULL."
            )
        if _states_nonzero(text) and abs(value) < sys.float_info.min:
            return ParsedScore(
                value, f"{raw!r} underflows a double, and is kept as {value!r}."
            )
        return ParsedScore(value)
    censored = _CENSORED.fullmatch(text)
    if censored and NUMBER.fullmatch(censored[2]):
        return ParsedScore(
            None,
            f"{raw!r} is censored (a bound, not a value), so its value is NULL.",
        )
    if _NOT_FINITE.fullmatch(text):
        return ParsedScore(
            None, f"{raw!r} is not a finite number, so its value is NULL."
        )
    return ParsedScore(
        None, f"{raw!r} is not a number openPIP reads, so its value is NULL."
    )


def censored_bound(raw: str) -> tuple[str, float] | None:
    """A censored value's bound as (operator, finite number), such as
    ("<", 2.2e-16) for R's "< 2.2e-16"; None for anything else. A limit that
    overflows or underflows a double gives None too: read as a double, an
    underflowing "<1e-400" would become "< 0", a tighter bound than stated."""
    censored = _CENSORED.fullmatch(raw.strip())
    if censored and NUMBER.fullmatch(censored[2]):
        limit = float(censored[2])
        underflows = _states_nonzero(censored[2]) and abs(limit) < sys.float_info.min
        if not math.isinf(limit) and not underflows:
            return censored[1], limit
    return None


def _states_nonzero(text) -> bool:
    mantissa = re.split("[eE]", text, maxsplit=1)[0]
    return any(digit in "123456789" for digit in mantissa)


def score_value_problem(value_kind, range_min, range_max, value) -> str | None:
    """Why a finite value does not suit its type, or None: probabilities and
    p- and q-values lie in [0, 1], a rank is an integer from 1, a count an
    integer from 0, and every value lies within the type's range where it
    sets one."""
    if value_kind in UNIT_INTERVAL and not 0 <= value <= 1:
        return f"a {value_kind} lies in [0, 1], and {value!r} does not"
    if value_kind == ScoreValueKind.RANK and not (value == int(value) and value >= 1):
        return f"a rank is an integer from 1, and {value!r} is not"
    if value_kind == ScoreValueKind.COUNT and not (value == int(value) and value >= 0):
        return f"a count is an integer from 0, and {value!r} is not"
    if range_min is not None and value < range_min:
        return f"the type's values are at least {range_min!r}, and {value!r} is less"
    if range_max is not None and value > range_max:
        return f"the type's values are at most {range_max!r}, and {value!r} is more"
    return None
