"""The visibility service, the one choke point (architecture D9): which
releases a caller may use, and for what.

The matrix walks who asks, for what purpose, with which approvals, of a
release in which status, pinned or not. The rest checks each rule on its
own: supersession, staged previews, embargo, empty and unknown selections,
the switch, the query count, and the bulk-export refusals.
"""

import datetime

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone

from graph.models import (
    ApprovalScope,
    DatasetStatus,
    ReleaseStatus,
    ShareAlike,
    SourceDataset,
)
from graph.tests.factories import (
    DatasetReleaseFactory,
    ReleaseLicenceFactory,
    StaffUserFactory,
    approvable_release,
    approve,
)
from graph.visibility import (
    NOT_APPROVED,
    SHARE_ALIKE_UNEVALUATED,
    Purpose,
    ReleaseSet,
    bulk_export_refusals,
    visible_release_ids,
)

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
PUBLIC_BULK = ApprovalScope.PUBLIC_BULK
EXTERNAL = ApprovalScope.EXTERNAL_SERVICES
EVERY_SCOPE = tuple(ApprovalScope.values)
DRAFT = ReleaseStatus.DRAFT
STAGED = ReleaseStatus.STAGED
PUBLISHED = ReleaseStatus.PUBLISHED
SUPERSEDED = ReleaseStatus.SUPERSEDED
WITHDRAWN = ReleaseStatus.WITHDRAWN
STATUSES = [DRAFT, STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN]
NOTHING = ReleaseSet(frozenset(), frozenset())
DAY = datetime.timedelta(days=1)


def request_for(user):
    request = RequestFactory().get("/")
    request.user = AnonymousUser() if user is None else user
    return request


def caller(kind):
    """A request from each kind of caller; only the last is staff."""
    if kind == "no request":
        return None
    if kind == "anonymous":
        return request_for(None)
    if kind == "regular user":
        return request_for(StaffUserFactory(is_staff=False))
    if kind == "inactive staff":
        return request_for(StaffUserFactory(is_active=False))
    return request_for(StaffUserFactory())


CALLERS = ["no request", "anonymous", "regular user", "inactive staff", "staff"]

# The approvals the matrix walks, a gap in the prerequisites included.
APPROVAL_SETS = {
    "none": (),
    "internal": (INTERNAL,),
    "view": (INTERNAL, PUBLIC_VIEW),
    "bulk": (INTERNAL, PUBLIC_VIEW, PUBLIC_BULK),
    "external": (INTERNAL, EXTERNAL),
    "all": EVERY_SCOPE,
    "public without internal": (PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL),
}
PREREQUISITE = {PUBLIC_VIEW: INTERNAL, EXTERNAL: INTERNAL, PUBLIC_BULK: PUBLIC_VIEW}


def effective(held) -> set:
    """The scopes these approvals make effective, under open terms and
    complete due diligence."""
    scopes = set()
    for scope in (INTERNAL, PUBLIC_VIEW, EXTERNAL, PUBLIC_BULK):
        prerequisite = PREREQUISITE.get(scope)
        if scope in held and (prerequisite is None or prerequisite in scopes):
            scopes.add(scope)
    return scopes


def expected(staff, purpose, held, status, pinned) -> bool:
    """What the rules say: the statuses the selection admits, then the
    scopes the caller and purpose need."""
    statuses = {PUBLISHED}
    if pinned:
        statuses.add(SUPERSEDED)
        if staff and purpose == Purpose.VIEW:
            statuses.add(STAGED)
    if status not in statuses:
        return False
    extra = {
        Purpose.VIEW: set(),
        Purpose.BULK: {PUBLIC_BULK},
        Purpose.EXTERNAL: {EXTERNAL},
    }
    needed = {INTERNAL if staff else PUBLIC_VIEW} | extra[purpose]
    return needed <= effective(held)


def test_the_expected_effective_scopes_follow_the_prerequisites():
    assert effective(()) == set()
    assert effective((PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL)) == set()
    assert effective((INTERNAL, PUBLIC_BULK)) == {INTERNAL}
    assert effective(EVERY_SCOPE) == set(EVERY_SCOPE)


@pytest.mark.parametrize("purpose", Purpose.values)
@pytest.mark.parametrize("kind", CALLERS)
def test_the_visibility_matrix(switch_on, kind, purpose):
    request = caller(kind)
    staff = kind == "staff"
    releases = {}
    for status in STATUSES:
        for name, held in APPROVAL_SETS.items():
            release = approvable_release(status=status)
            approve(release, *held)
            releases[status, name] = release
    default = visible_release_ids(request, purpose)
    pinned = visible_release_ids(
        request, purpose, pinned=[release.pk for release in releases.values()]
    )
    for (status, name), release in releases.items():
        held = APPROVAL_SETS[name]
        for selection, found, is_pinned in (
            ("default", default, False),
            ("pinned", pinned, True),
        ):
            assert (release.pk in found.ids) is expected(
                staff, purpose, held, status, is_pinned
            ), (status, name, selection)
    assert default.superseded == frozenset()
    assert pinned.superseded == {
        release.pk
        for (status, _), release in releases.items()
        if status == SUPERSEDED and release.pk in pinned.ids
    }
    assert pinned.superseded <= pinned.ids


def test_a_release_set_holds_frozen_sets_of_ids(switch_on):
    release = approvable_release(status=PUBLISHED)
    approve(release, INTERNAL, PUBLIC_VIEW)
    found = visible_release_ids(None, Purpose.VIEW)
    assert found == ReleaseSet(frozenset({release.pk}), frozenset())
    assert isinstance(found.ids, frozenset) and isinstance(found.superseded, frozenset)
    with pytest.raises(AttributeError):
        found.ids = frozenset()


def test_the_default_set_leaves_out_superseded_releases_and_pins_flag_them(
    switch_on,
):
    old = approvable_release(status=SUPERSEDED)
    new = approvable_release(dataset=old.dataset, status=PUBLISHED, supersedes=old)
    for release in (old, new):
        approve(release, *EVERY_SCOPE)
    staff = caller("staff")
    for request in (None, staff):
        assert visible_release_ids(request, Purpose.VIEW) == ReleaseSet(
            frozenset({new.pk}), frozenset()
        )
        assert visible_release_ids(
            request, Purpose.VIEW, pinned=[old.pk, new.pk]
        ) == ReleaseSet(frozenset({old.pk, new.pk}), frozenset({old.pk}))
        # Pinned alone, the superseded release is used, flagged.
        assert visible_release_ids(
            request, Purpose.BULK, pinned=[old.pk]
        ) == ReleaseSet(frozenset({old.pk}), frozenset({old.pk}))


def test_staff_preview_a_staged_release_only_pinned_for_viewing(switch_on):
    staged = approvable_release(status=STAGED)
    approve(staged, *EVERY_SCOPE)
    staff = caller("staff")
    assert visible_release_ids(staff, Purpose.VIEW) == NOTHING
    assert visible_release_ids(staff, Purpose.VIEW, pinned=[staged.pk]).ids == {
        staged.pk
    }
    # Nothing staged is exported or sent out, nor shown to visitors.
    for purpose in (Purpose.BULK, Purpose.EXTERNAL):
        assert visible_release_ids(staff, purpose, pinned=[staged.pk]) == NOTHING
    for kind in CALLERS[:-1]:
        for purpose in Purpose.values:
            assert (
                visible_release_ids(caller(kind), purpose, pinned=[staged.pk])
                == NOTHING
            )


def test_staff_preview_needs_an_effective_internal_approval(switch_on):
    staged = approvable_release(status=STAGED)
    staff = caller("staff")
    assert visible_release_ids(staff, Purpose.VIEW, pinned=[staged.pk]) == NOTHING
    approve(staged, INTERNAL)
    assert visible_release_ids(staff, Purpose.VIEW, pinned=[staged.pk]).ids == {
        staged.pk
    }


@pytest.mark.parametrize("status", [DRAFT, WITHDRAWN])
def test_drafts_and_withdrawn_releases_are_never_visible(switch_on, status):
    release = approvable_release(status=status)
    approve(release, *EVERY_SCOPE)
    for kind in CALLERS:
        request = caller(kind)
        for purpose in Purpose.values:
            for pinned in (None, [release.pk]):
                assert visible_release_ids(request, purpose, pinned) == NOTHING


def test_an_empty_selection_selects_nothing(switch_on):
    release = approvable_release(status=PUBLISHED)
    approve(release, *EVERY_SCOPE)
    assert visible_release_ids(None, Purpose.VIEW).ids == {release.pk}
    for empty in ([], (), set(), iter([])):
        assert visible_release_ids(None, Purpose.VIEW, pinned=empty) == NOTHING


def test_unknown_and_invisible_pinned_ids_are_dropped_without_a_word(switch_on):
    visible = approvable_release(status=PUBLISHED)
    approve(visible, *EVERY_SCOPE)
    invisible = approvable_release(status=PUBLISHED)
    approve(invisible, INTERNAL)
    deleted = DatasetReleaseFactory()
    deleted_pk = deleted.pk
    deleted.delete()
    pinned = [visible.pk, invisible.pk, deleted_pk, 10**12, 0, -5, 2**80, -(2**80)]
    assert visible_release_ids(None, Purpose.VIEW, pinned=pinned) == ReleaseSet(
        frozenset({visible.pk}), frozenset()
    )


@pytest.mark.parametrize("purpose", ["download", "VIEW", "", None, 1])
def test_an_unknown_purpose_raises_whatever_the_switch(db, settings, purpose):
    for enabled in (True, False):
        settings.TYPED_GRAPH_ENABLED = enabled
        with pytest.raises(ValueError):
            visible_release_ids(None, purpose)
        with pytest.raises(ValueError):
            visible_release_ids(None, purpose, pinned=[1])


@pytest.mark.parametrize("pinned", [["1"], [1.0], [True], [None], "12", b"12", 5])
def test_pinned_ids_are_integers(db, settings, pinned):
    for enabled in (True, False):
        settings.TYPED_GRAPH_ENABLED = enabled
        with pytest.raises(TypeError):
            visible_release_ids(None, Purpose.VIEW, pinned=pinned)
        with pytest.raises(TypeError):
            bulk_export_refusals(None, pinned)


def test_purposes_are_the_three_the_design_names():
    assert set(Purpose.values) == {"view", "bulk", "external"}
    assert Purpose("view") is Purpose.VIEW


def test_nothing_is_visible_while_the_switch_is_off(db, settings):
    release = approvable_release(status=PUBLISHED)
    approve(release, *EVERY_SCOPE)
    settings.TYPED_GRAPH_ENABLED = False
    staff = caller("staff")
    for request in (None, staff):
        for purpose in Purpose.values:
            assert visible_release_ids(request, purpose) == NOTHING
            assert visible_release_ids(request, purpose, pinned=[release.pk]) == NOTHING
    settings.TYPED_GRAPH_ENABLED = True
    assert visible_release_ids(None, Purpose.VIEW).ids == {release.pk}


def test_an_embargo_hides_a_release_from_all_but_staff_viewing(switch_on):
    embargo = timezone.now() + DAY
    release = approvable_release(status=PUBLISHED, embargo_until=embargo)
    approve(release, *EVERY_SCOPE)
    staff = caller("staff")
    assert visible_release_ids(staff, Purpose.VIEW).ids == {release.pk}
    for purpose in (Purpose.BULK, Purpose.EXTERNAL):
        assert visible_release_ids(staff, purpose) == NOTHING
    for purpose in Purpose.values:
        assert visible_release_ids(None, purpose) == NOTHING
        assert visible_release_ids(None, purpose, pinned=[release.pk]) == NOTHING
    # It lapses at its date: `at` moves only the embargo comparison.
    for purpose in Purpose.values:
        assert visible_release_ids(None, purpose, at=embargo).ids == {release.pk}
    with pytest.raises(ValueError, match="aware datetime"):
        visible_release_ids(None, Purpose.VIEW, at=datetime.datetime(2027, 1, 1))


def test_bulk_needs_public_bulk_on_top_of_view_also_for_staff(switch_on):
    release = approvable_release(status=PUBLISHED)
    approve(release, INTERNAL, PUBLIC_VIEW)
    staff = caller("staff")
    for request in (None, staff):
        assert visible_release_ids(request, Purpose.VIEW).ids == {release.pk}
        assert visible_release_ids(request, Purpose.BULK) == NOTHING
    approve(release, PUBLIC_BULK)
    for request in (None, staff):
        assert visible_release_ids(request, Purpose.BULK).ids == {release.pk}
    # Staff need it as well when the release is not public at all.
    internal_only = approvable_release(status=PUBLISHED)
    approve(internal_only, INTERNAL)
    assert visible_release_ids(staff, Purpose.BULK, pinned=[internal_only.pk]) == (
        NOTHING
    )


def test_external_services_need_their_own_approval_also_for_staff(switch_on):
    release = approvable_release(status=PUBLISHED)
    approve(release, INTERNAL, PUBLIC_VIEW, PUBLIC_BULK)
    staff = caller("staff")
    for request in (None, staff):
        assert visible_release_ids(request, Purpose.EXTERNAL) == NOTHING
    approve(release, EXTERNAL)
    for request in (None, staff):
        assert visible_release_ids(request, Purpose.EXTERNAL).ids == {release.pk}


def test_the_datasets_status_changes_nothing(switch_on):
    shown = approvable_release(status=PUBLISHED)
    approve(shown, *EVERY_SCOPE)
    hidden = approvable_release(status=PUBLISHED)
    for status in DatasetStatus.values:
        SourceDataset.objects.filter(
            pk__in=[shown.dataset_id, hidden.dataset_id]
        ).update(status=status)
        for purpose in Purpose.values:
            assert visible_release_ids(None, purpose).ids == {shown.pk}, status


@pytest.mark.parametrize("count", [1, 25])
def test_one_query_whatever_the_number_of_releases(
    switch_on, django_assert_num_queries, django_assert_max_num_queries, count
):
    releases = []
    for _ in range(count):
        release = approvable_release(status=PUBLISHED)
        approve(release, *EVERY_SCOPE)
        releases.append(release)
    staff = caller("staff")
    ids = [release.pk for release in releases]
    for request in (None, staff):
        for purpose in Purpose.values:
            with django_assert_num_queries(1):
                assert visible_release_ids(request, purpose).ids == set(ids)
            with django_assert_num_queries(1):
                assert visible_release_ids(request, purpose, pinned=ids).ids == set(ids)
    # One more, for the ShareAlike terms of a combined export.
    with django_assert_max_num_queries(2):
        assert bulk_export_refusals(None, ids) == {}


# ── Bulk export ─────────────────────────────────────────────────────────────


def exportable(share_alike=ShareAlike.NO, **fields):
    """A published release approved for bulk download."""
    release = approvable_release(
        status=PUBLISHED, terms={"share_alike": share_alike}, **fields
    )
    approve(release, *EVERY_SCOPE)
    return release


def test_an_export_of_releases_approved_for_bulk_proceeds(switch_on):
    first, second = exportable(), exportable()
    for request in (None, caller("staff")):
        assert bulk_export_refusals(request, [first.pk, second.pk]) == {}
        assert bulk_export_refusals(request, [first.pk]) == {}


def test_a_release_not_approved_for_bulk_is_refused_without_saying_why(switch_on):
    view_only = approvable_release(status=PUBLISHED)
    approve(view_only, INTERNAL, PUBLIC_VIEW)
    draft = approvable_release(status=DRAFT)
    approve(draft, *EVERY_SCOPE)
    withdrawn = approvable_release(status=WITHDRAWN)
    approve(withdrawn, *EVERY_SCOPE)
    unapproved = DatasetReleaseFactory(status=PUBLISHED)
    missing = 10**12
    requested = [view_only.pk, draft.pk, withdrawn.pk, unapproved.pk, missing]
    for request in (None, caller("staff")):
        assert bulk_export_refusals(request, requested) == {
            pk: NOT_APPROVED for pk in sorted(requested)
        }
    # The same words for one that exists and one that does not.
    assert bulk_export_refusals(None, [missing]) == {missing: NOT_APPROVED}
    assert bulk_export_refusals(None, [unapproved.pk]) == {unapproved.pk: NOT_APPROVED}


@pytest.mark.parametrize("share_alike", [ShareAlike.YES, ShareAlike.UNKNOWN])
def test_a_share_alike_release_is_exported_only_on_its_own(switch_on, share_alike):
    share_alike_release = exportable(share_alike)
    other = exportable()
    assert bulk_export_refusals(None, [share_alike_release.pk]) == {}
    assert bulk_export_refusals(None, [share_alike_release.pk, other.pk]) == {
        share_alike_release.pk: SHARE_ALIKE_UNEVALUATED
    }
    # Asked twice for the same release, it is still one release.
    assert bulk_export_refusals(None, [share_alike_release.pk] * 2) == {}


def test_the_latest_snapshot_says_whether_share_alike_applies(switch_on):
    release = exportable(ShareAlike.YES)
    other = exportable()
    ReleaseLicenceFactory(release=release, open_terms=True, share_alike=ShareAlike.NO)
    assert bulk_export_refusals(None, [release.pk, other.pk]) == {}
    ReleaseLicenceFactory(
        release=other, open_terms=True, share_alike=ShareAlike.UNKNOWN
    )
    assert bulk_export_refusals(None, [release.pk, other.pk]) == {
        other.pk: SHARE_ALIKE_UNEVALUATED
    }


def test_releases_without_share_alike_combine(switch_on):
    releases = [exportable() for _ in range(3)]
    assert bulk_export_refusals(None, [release.pk for release in releases]) == {}


def test_each_release_of_a_combined_export_gets_its_own_reason(switch_on):
    share_alike_release = exportable(ShareAlike.YES)
    plain = exportable()
    invisible = approvable_release(status=PUBLISHED)
    assert bulk_export_refusals(
        None, [plain.pk, share_alike_release.pk, invisible.pk]
    ) == {
        share_alike_release.pk: SHARE_ALIKE_UNEVALUATED,
        invisible.pk: NOT_APPROVED,
    }


def test_a_bulk_export_is_refused_under_embargo_even_for_staff(switch_on):
    release = exportable(embargo_until=timezone.now() + DAY)
    for request in (None, caller("staff")):
        assert bulk_export_refusals(request, [release.pk]) == {release.pk: NOT_APPROVED}


def test_every_release_is_refused_while_the_switch_is_off(db, settings):
    release = exportable()
    settings.TYPED_GRAPH_ENABLED = False
    assert bulk_export_refusals(None, [release.pk]) == {release.pk: NOT_APPROVED}
    assert bulk_export_refusals(caller("staff"), [release.pk]) == {
        release.pk: NOT_APPROVED
    }


def test_nothing_requested_is_nothing_refused(switch_on):
    exportable()
    assert bulk_export_refusals(None, []) == {}
