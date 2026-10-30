"""Which releases a caller may use, and for what: the one choke point
(architecture D9). Every typed read, export and external call asks it.

A caller is staff when the request's user is authenticated, active and
staff; anyone else, a request without a user included, is a visitor. Staff
view what is effective for `internal`, visitors what is effective for
`public_view`, and bulk download and external services need their own scope
on top, staff included. While a release is embargoed only `internal` is
effective, so it is staff's alone.

The default set holds published releases only, so a superseded release
drops out of default views once its correction is published. A pinned set
(an explicit selection, a browser instance, a saved view) may also hold
superseded releases, flagged, and, for staff viewing, staged ones: staff
preview a staged correction by pinning it, and nothing staged is exported or
sent out. Drafts and withdrawn releases are never visible. A pinned id that
names no release, or one the caller may not see, is dropped without a word,
so existence never shows.
"""

from dataclasses import dataclass

from django.db import models

from .approvals import (
    EXTERNAL_SERVICES,
    INTERNAL,
    PUBLIC_BULK,
    PUBLIC_VIEW,
    effective_rows,
)
from .licensing import latest_licences
from .models import DatasetRelease, ReleaseStatus, ShareAlike
from .preconditions import integer_ids, moment, storable, typed_graph_enabled

NOT_APPROVED = "not approved for bulk download"
SHARE_ALIKE_UNEVALUATED = "ShareAlike terms not evaluated: export it on its own"


class Purpose(models.TextChoices):
    VIEW = "view"
    BULK = "bulk"
    EXTERNAL = "external"


# What each purpose needs besides the scope that lets the caller view.
_PURPOSE_SCOPES = {
    Purpose.VIEW: frozenset(),
    Purpose.BULK: frozenset({PUBLIC_BULK}),
    Purpose.EXTERNAL: frozenset({EXTERNAL_SERVICES}),
}


@dataclass(frozen=True)
class ReleaseSet:
    """Release ids a caller may use. `superseded` is the subset of `ids`
    whose status is superseded, for callers to flag."""

    ids: frozenset
    superseded: frozenset


NOTHING = ReleaseSet(frozenset(), frozenset())


def is_staff_caller(request) -> bool:
    user = getattr(request, "user", None)
    return bool(
        user is not None and user.is_authenticated and user.is_active and user.is_staff
    )


def visible_release_ids(request, purpose, pinned=None, *, at=None) -> ReleaseSet:
    """The releases this request may use for `purpose`, from one query.

    pinned: None for the default set, or an iterable of release ids; an
    empty one selects nothing. at: replaces now where the embargo is
    compared, and only there. An unknown purpose raises ValueError and a
    pinned id that is not an int raises TypeError, whatever the switch;
    while it is off, nothing is visible.
    """
    purpose = Purpose(purpose)
    selected = None if pinned is None else integer_ids(pinned)
    at = moment(at)
    if not typed_graph_enabled() or selected == frozenset():
        return NOTHING
    staff = is_staff_caller(request)
    needed = {INTERNAL if staff else PUBLIC_VIEW} | _PURPOSE_SCOPES[purpose]
    statuses = {ReleaseStatus.PUBLISHED}
    releases = DatasetRelease.objects.all()
    if selected is not None:
        statuses.add(ReleaseStatus.SUPERSEDED)
        if staff and purpose == Purpose.VIEW:
            statuses.add(ReleaseStatus.STAGED)
        releases = releases.filter(pk__in=storable(selected))
    ids, superseded = set(), set()
    for row, scopes in effective_rows(releases.filter(status__in=statuses), at):
        if needed <= scopes:
            ids.add(row["pk"])
            if row["status"] == ReleaseStatus.SUPERSEDED:
                superseded.add(row["pk"])
    return ReleaseSet(frozenset(ids), frozenset(superseded))


def bulk_export_refusals(request, release_ids, *, at=None) -> dict:
    """{release id: why it may not be exported}; empty when the export of
    these releases may proceed.

    A release the caller may not bulk-download is refused with the same
    words whether it exists or not. Until ShareAlike terms have been
    evaluated (open question C13), a release whose latest snapshot says
    ShareAlike applies, or does not say, is exported only on its own. While
    the switch is off, every release is refused.
    """
    requested = integer_ids(release_ids)
    visible = visible_release_ids(request, Purpose.BULK, pinned=requested, at=at).ids
    refusals = {pk: NOT_APPROVED for pk in requested - visible}
    if len(requested) > 1:
        terms = latest_licences(visible)
        for pk in visible:
            share_alike = terms[pk].share_alike if pk in terms else ShareAlike.UNKNOWN
            if share_alike != ShareAlike.NO:
                refusals[pk] = SHARE_ALIKE_UNEVALUATED
    return dict(sorted(refusals.items()))
