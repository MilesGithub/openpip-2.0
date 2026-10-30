"""What the typed graph's services check before they read or write.

Write services refuse while TYPED_GRAPH_ENABLED is off, act only for an active
staff account, and need a stated reason where they ask for one; each refuses
with its own typed error, before anything is written. Read services take
release ids, which are integers: anything else is a programming error, while
an id that names no release is simply not found, so existence never shows.
"""

import datetime

from django.conf import settings
from django.utils import timezone

from .models import DatasetRelease

# Primary keys are signed 64-bit integers. A larger id names no row, and
# SQLite cannot even compare one with a column.
_SMALLEST_ID, _LARGEST_ID = -(2**63), 2**63 - 1


def typed_graph_enabled() -> bool:
    return bool(settings.TYPED_GRAPH_ENABLED)


def require_switch(error, what):
    """Refuse while the switch is off. `what` completes "so ...": what the
    service would have done."""
    if not typed_graph_enabled():
        raise error(
            f"The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so {what}."
            " Nothing was written."
        )


def require_staff(error, user, role):
    """The actor: a saved, active staff account."""
    if getattr(user, "pk", None) is None or not (
        getattr(user, "is_active", False) and getattr(user, "is_staff", False)
    ):
        raise error(
            f"{role} must be an active staff account, not {user!r}. "
            "Nothing was written."
        )
    return user


def required_text(error, value, what) -> str:
    """A reason or rationale, stripped; blank is refused."""
    if not isinstance(value, str) or not value.strip():
        raise error(f"{what} must be stated, not {value!r}. Nothing was written.")
    return value.strip()


def optional_text(error, value, what) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise error(f"{what} is text, not {value!r}. Nothing was written.")
    return value.strip()


def integer_ids(values) -> frozenset:
    """The release ids a caller gave, as a set. Each is an int (a bool is
    not); anything else raises TypeError."""
    if values is None or isinstance(values, (str, bytes)):
        raise TypeError(f"Release ids are an iterable of integers, not {values!r}.")
    ids = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"A release id is an integer, not {value!r}.")
        ids.add(value)
    return frozenset(ids)


def storable(ids) -> list:
    """The ids a row could have, in order: the others name nothing."""
    return sorted(pk for pk in ids if _SMALLEST_ID <= pk <= _LARGEST_ID)


def moment(at) -> datetime.datetime:
    """The time an embargo is compared with: now unless given."""
    if at is None:
        return timezone.now()
    if not isinstance(at, datetime.datetime) or timezone.is_naive(at):
        raise ValueError(f"at is an aware datetime, not {at!r}.")
    return at


def locked_release(release, error) -> DatasetRelease:
    """The release as stored, locked for the caller's transaction: its state
    decides the write. `release` is a row or its id."""
    pk = release.pk if isinstance(release, DatasetRelease) else release
    row = None
    if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
        row = DatasetRelease.objects.select_for_update().filter(pk=pk).first()
    if row is None:
        raise error(f"Unknown release {release!r}. Nothing was written.")
    return row
