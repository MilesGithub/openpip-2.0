"""Availability (browsers.availability.compute), rule by rule, with no
database: the stored status first, then the kind, then for a typed instance
the status and visibility of every selected release."""

import pytest

from browsers.availability import LIVE_RELEASE_STATUSES, Availability, compute
from browsers.models import Kind, Status
from graph.models import ReleaseStatus

LIVE = Availability.LIVE
PLANNED = Availability.PLANNED
EXTERNAL = Availability.EXTERNAL
METADATA_ONLY = Availability.METADATA_ONLY
RETIRED = Availability.RETIRED
# One selected release that the caller may see, live in every way.
SEEN = ((1, ReleaseStatus.PUBLISHED),)


def test_the_values_and_the_live_release_statuses():
    assert Availability.values == [
        "live",
        "planned",
        "external",
        "metadata_only",
        "retired",
    ]
    assert LIVE_RELEASE_STATUSES == {"published", "superseded"}


@pytest.mark.parametrize("kind", Kind.values)
@pytest.mark.parametrize(
    "status, expected", [(Status.RETIRED, RETIRED), (Status.PLANNED, PLANNED)]
)
def test_a_stored_retired_or_planned_status_comes_first(kind, status, expected):
    # Whatever the kind, and even with a selection the caller sees live.
    assert compute(kind, status, SEEN, {1}) == expected
    assert compute(kind, status, (), frozenset()) == expected


@pytest.mark.parametrize(
    "kind, expected",
    [
        (Kind.EXTERNAL, EXTERNAL),
        (Kind.PORTAL_PPI, LIVE),
        (Kind.METADATA_ONLY, METADATA_ONLY),
    ],
)
def test_an_available_instance_of_another_kind_follows_its_kind(kind, expected):
    # Its selection, which it never has, would not change that.
    assert compute(kind, Status.AVAILABLE, (), frozenset()) == expected
    assert compute(kind, Status.AVAILABLE, SEEN, {1}) == expected


@pytest.mark.parametrize(
    "release_status, expected",
    [
        (ReleaseStatus.PUBLISHED, LIVE),
        # v1 after v2: an instance pinned to v1 stays live.
        (ReleaseStatus.SUPERSEDED, LIVE),
        # A pinned set admits a staged release for staff; it is not live.
        (ReleaseStatus.STAGED, METADATA_ONLY),
        (ReleaseStatus.DRAFT, METADATA_ONLY),
        (ReleaseStatus.WITHDRAWN, METADATA_ONLY),
    ],
)
def test_a_typed_instance_is_live_only_on_published_or_superseded_releases(
    release_status, expected
):
    selected = ((7, release_status),)
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, {7}) == expected
    # Hidden from the caller, it is never live.
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, frozenset()) == (
        METADATA_ONLY
    )


def test_a_typed_instance_needs_every_selected_release_visible():
    selected = ((1, ReleaseStatus.PUBLISHED), (2, ReleaseStatus.SUPERSEDED))
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, {1, 2}) == LIVE
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, {1, 2, 3}) == LIVE
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, {1}) == METADATA_ONLY
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, {2}) == METADATA_ONLY


def test_a_typed_instance_with_one_release_not_yet_live_is_not_live():
    selected = ((1, ReleaseStatus.PUBLISHED), (2, ReleaseStatus.STAGED))
    assert compute(Kind.TYPED, Status.AVAILABLE, selected, {1, 2}) == METADATA_ONLY


def test_a_typed_instance_with_no_release_is_metadata_only():
    assert compute(Kind.TYPED, Status.AVAILABLE, (), frozenset()) == METADATA_ONLY
    assert compute(Kind.TYPED, Status.AVAILABLE, (), {1}) == METADATA_ONLY


def test_the_statuses_compare_as_stored_strings():
    # The routes read release statuses with values_list, as plain strings.
    selected = ((1, "published"), (2, "superseded"))
    assert compute("typed", "available", selected, {1, 2}) == "live"
    assert compute("typed", "available", iter(selected), {1, 2}) == "live"
    assert compute("typed", "available", [(1, "staged")], {1}) == "metadata_only"


def test_an_unknown_kind_is_never_live():
    # No row can hold one (browsers_instance_kind_valid); it fails closed.
    assert compute("hub", Status.AVAILABLE, SEEN, {1}) == METADATA_ONLY
