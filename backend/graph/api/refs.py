"""Opaque ids: <kind>:<integer>, such as entity:12 (architecture §7.1).

parse_ref accepts exactly the strings ref writes, so one row has one
spelling: ASCII digits, no sign, no spaces, no trailing new line, no leading
zeros, and at most 19 digits, so int() never meets a string long enough to
raise. A value that fits 19 digits but not 64 bits names nothing: callers
pass ids through preconditions.storable before any query.
"""

import re


def ref(kind: str, pk: int) -> str:
    return f"{kind}:{pk}"


def parse_ref(text: str, kind: str) -> int | None:
    """The id in `text`, when it is a ref of this kind; None otherwise."""
    found = re.fullmatch(rf"{kind}:(0|[1-9][0-9]{{0,18}})", text)
    return None if found is None else int(found[1])
