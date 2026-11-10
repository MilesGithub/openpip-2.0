"""Which browser instances a caller may list or reach, and the releases a
browser= slug pins.

Staff list and reach every instance. Everyone else lists the public ones
and reaches the public and the unlisted ones, by slug. An instance a caller
may not reach reads exactly as an unknown slug does. An instance's
visibility decides who sees the instance, never its data: its releases stay
behind the release gate (graph.visibility), which an instance never widens.
"""

from django.db.models import Q

from graph.visibility import is_staff_caller

from .models import BrowserInstance, BrowserInstanceRelease, Kind, Visibility

# An id no release has (graph.preconditions.storable keeps 0), pinned wherever
# an instance selects nothing, so that visible_release_ids sends its one
# statement whether a selection is empty or hidden from the caller: the
# statement count never tells the two apart.
EMPTY_PIN = frozenset({0})


def listable(request) -> Q:
    """The instances the caller's gallery lists."""
    if is_staff_caller(request):
        return Q()
    return Q(visibility=Visibility.PUBLIC)


def reachable(request) -> Q:
    """The instances the caller may open by slug and pin with browser=."""
    if is_staff_caller(request):
        return Q()
    return Q(visibility__in=(Visibility.PUBLIC, Visibility.UNLISTED))


def instance_releases(slug, request) -> frozenset:
    """The release ids browser=<slug> pins, read in one statement: every
    release, of either role, that the reachable typed instance of this slug
    selects, whatever its stored status or availability. The release gate
    then drops what the caller may not see. An unknown slug, an unreachable
    instance, another kind and an instance that selects nothing all pin
    EMPTY_PIN."""
    ids = frozenset(
        BrowserInstanceRelease.objects.filter(
            instance__in=BrowserInstance.objects.filter(
                reachable(request), slug=slug, kind=Kind.TYPED
            )
        )
        .order_by()
        .values_list("release_id", flat=True)
    )
    return ids or EMPTY_PIN
