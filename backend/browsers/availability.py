"""What a browser instance can show its caller now: its availability,
computed for each request and never stored.

A stored planned or retired status comes first. Then the kind decides: an
external entry links elsewhere; a portal_ppi entry is this portal's /search,
which the release gate does not cover, and is live; a citation card is
metadata only. A typed instance is live while it selects at least one
release and every selected release, of either role, is published or
superseded and visible to the caller; otherwise it is metadata_only, shown as
"data unavailable", and a withdrawal or a revoked approval shows on the next
request. Since the visible set is pinned to the instance's own releases, an
instance pinned to v1 stays live after v2 supersedes it. The status test is
needed besides the visible set: a pinned set admits staged releases for
staff (graph.visibility), and a staged release is not yet live.
"""

from django.db import models

from graph.models import ReleaseStatus

from .models import Kind, Status


class Availability(models.TextChoices):
    LIVE = "live"
    PLANNED = "planned"
    EXTERNAL = "external"
    METADATA_ONLY = "metadata_only"
    RETIRED = "retired"


LIVE_RELEASE_STATUSES = frozenset(
    {ReleaseStatus.PUBLISHED.value, ReleaseStatus.SUPERSEDED.value}
)


def compute(kind, status, selected, visible) -> str:
    """The availability of an instance of this kind and stored status, for
    one caller. selected: (release id, release status) of every selected
    release, of both roles; visible: the release ids visible_release_ids
    issued for the caller, pinned to the selected ones. Reads nothing."""
    selected = tuple(selected)
    if status == Status.RETIRED:
        return Availability.RETIRED
    if status == Status.PLANNED:
        return Availability.PLANNED
    if kind == Kind.EXTERNAL:
        return Availability.EXTERNAL
    if kind == Kind.PORTAL_PPI:
        return Availability.LIVE
    if (
        kind == Kind.TYPED
        and selected
        and all(
            release_status in LIVE_RELEASE_STATUSES and release_id in visible
            for release_id, release_status in selected
        )
    ):
        return Availability.LIVE
    return Availability.METADATA_ONLY
