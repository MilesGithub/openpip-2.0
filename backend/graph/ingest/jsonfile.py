"""Strict JSON for the files an operator supplies with a release: its
manifest (manifest.py) and a reviewed resolution file (resolutions.py).

They are untrusted data, read under a size limit and parsed strictly. What
Python's json module would accept but these files never mean is refused:

- more than limits.MAX_JSON_BYTES, enforced while reading;
- anything but UTF-8: a byte order mark, UTF-16 or UTF-32;
- a key given twice in one object, which json would silently collapse;
- NaN, Infinity and -Infinity, which are not JSON;
- an integer of more than 19 digits (a signed 64-bit integer has 19), and
  any number with a fraction or an exponent: these files hold integers only;
- nesting deeper than Python's recursion limit;
- a string, a key included, that holds NUL, a lone surrogate or a control
  character (C0, DEL or C1), however it was escaped.

Each refusal raises the caller's error type, naming the file. The schema
helpers below check each value where the caller expects it: an integer is
an int, never a bool (true is not 1), and in range; text is a str within its
length; and an object has exactly the keys allowed.
"""

import codecs
import json
import re
from pathlib import Path

from . import limits
from .protocol import quoted

# A signed 64-bit integer has at most 19 digits.
MAX_INTEGER_DIGITS = 19
# NUL and every other C0 control, DEL and the C1 controls, and the
# surrogates, which a str holds only when unpaired: json turns an escaped
# pair into one character.
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_UTF16_32_MARKS = (
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
)


class JsonFileError(ValueError):
    """The file is not strict JSON, or does not follow its schema."""


def text_problem(value) -> str | None:
    """Why a str is not stored as text, or None: it holds NUL, a lone
    surrogate or a control character (C0, DEL or C1)."""
    found = _UNSAFE.search(value)
    if found is None:
        return None
    character = found.group()
    if character == "\x00":
        what = "a NUL character (U+0000)"
    elif "\ud800" <= character <= "\udfff":
        what = f"a lone surrogate (U+{ord(character):04X})"
    else:
        what = f"a control character (U+{ord(character):04X})"
    return f"{quoted(value)} contains {what}."


def read_bounded(source, *, what, error=JsonFileError) -> bytes:
    """A file's bytes, read from a path or an open binary stream, and never
    more than limits.MAX_JSON_BYTES of them: a longer file is refused once
    the limit is passed, without reading the rest."""
    limit = limits.MAX_JSON_BYTES
    if isinstance(source, (str, Path)):
        with open(source, "rb") as stream:
            data = stream.read(limit + 1)
    else:
        data = source.read(limit + 1)
    if not isinstance(data, bytes):
        raise TypeError(f"{what} is read from a binary stream, not {source!r}.")
    if len(data) > limit:
        raise error(f"{what.capitalize()} is larger than {limit} bytes.")
    return data


def load_strict_json(data: bytes, *, what, error=JsonFileError):
    """The JSON value these bytes hold, parsed strictly (the module
    docstring says what is refused). `what` names the file in messages, as
    in "the release manifest"; a refusal raises `error`."""
    if not isinstance(data, bytes):
        raise TypeError(f"{what} is given as bytes, not {type(data).__name__}.")
    limit = limits.MAX_JSON_BYTES
    if len(data) > limit:
        raise error(f"{what.capitalize()} is larger than {limit} bytes.")
    if data.startswith(codecs.BOM_UTF8):
        raise error(
            f"{what.capitalize()} starts with a byte order mark: save it as UTF-8 "
            "without one."
        )
    if data.startswith(_UTF16_32_MARKS):
        raise error(f"{what.capitalize()} is UTF-16 or UTF-32: save it as UTF-8.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise error(
            f"{what.capitalize()} is not UTF-8 (byte {exc.start} cannot be decoded)."
        ) from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_int=_integer,
            parse_float=_fraction,
        )
    except RecursionError:
        raise error(f"{what.capitalize()} is nested too deeply.") from None
    except ValueError as exc:
        raise error(f"{what.capitalize()} is not strict JSON: {exc}") from None
    problem = _first_unsafe_string(value)
    if problem:
        raise error(f"{what.capitalize()}: {problem}")
    return value


def _object(pairs):
    """An object's pairs as a dict, the first key given twice refused: in
    one pass, so a large object costs no more than reading it."""
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"the key {quoted(key)} is given twice in one object")
        seen.add(key)
    return dict(pairs)


def _constant(name):
    raise ValueError(f"{name} is not a number JSON can hold")


def _integer(digits):
    if len(digits.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValueError(
            f"an integer of more than {MAX_INTEGER_DIGITS} digits "
            f"({len(digits.lstrip('-'))})"
        )
    return int(digits)


def _fraction(written):
    raise ValueError(
        f"{quoted(written)} has a fraction or an exponent, and these files hold "
        "integers only"
    )


def _first_unsafe_string(value) -> str | None:
    """The first string, key or value, that text_problem refuses. Iterative,
    so a deep value cannot exhaust the stack."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            problem = text_problem(item)
            if problem:
                return problem
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


# ── The schema's checks ─────────────────────────────────────────────────────
# Each takes `where`, the value's place in the file ('publications["k"].year'),
# and raises `error` with a sentence that names it.


def fields(value, *, where, required, optional=(), error=JsonFileError) -> dict:
    """An object with every required key, and no key but these."""
    if not isinstance(value, dict):
        raise error(f"{where} is a JSON object, not {_kind(value)}.")
    missing = [key for key in required if key not in value]
    if missing:
        raise error(f"{where} has no {', '.join(missing)}.")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        listed = ", ".join(quoted(key) for key in unknown[:5])
        raise error(f"{where} has unknown keys: {listed}.")
    return value


def integer(value, *, where, low, high, null=False, error=JsonFileError):
    """An integer in [low, high], never a bool; or None when null."""
    if value is None and null:
        return None
    # json gives int or bool here; a bool is an int to isinstance, never here.
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        allowed = f"an integer from {low} to {high}" + (" or null" if null else "")
        raise error(f"{where} is {allowed}, not {quoted(value)}.")
    return value


def text(value, *, where, limit, blank=False, error=JsonFileError) -> str:
    """A str of at most `limit` characters, not blank unless `blank`."""
    if not isinstance(value, str):
        raise error(f"{where} is text, not {_kind(value)}.")
    if len(value) > limit:
        raise error(f"{where} is at most {limit} characters, not {len(value)}.")
    if not blank and not value.strip():
        raise error(f"{where} is never blank.")
    return value


def boolean(value, *, where, error=JsonFileError) -> bool:
    if not isinstance(value, bool):
        raise error(f"{where} is true or false, not {quoted(value)}.")
    return value


def _kind(value) -> str:
    if value is None:
        return "null"
    return {
        bool: "a boolean",
        int: "a number",
        str: "text",
        list: "a list",
        dict: "an object",
    }.get(type(value), type(value).__name__)
