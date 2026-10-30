"""Publishing and withdrawing releases (graph.releases): supersession, the
rule that a correction never hides public data, and withdrawal."""

import datetime
import threading
import time
from functools import partial

import pytest
from django.db import connection, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from graph.approvals import revoke
from graph.models import (
    ApprovalScope,
    DatasetRelease,
    DueDiligenceRecord,
    DueDiligenceStatus,
    ImmutableRecordError,
    ReleaseLicence,
    ReleaseStatus,
    UsageApproval,
)
from graph.releases import ReleaseError, publish_release, withdraw_release
from graph.tests.factories import (
    DatasetReleaseFactory,
    StaffUserFactory,
    approvable_release,
    approve,
)
from graph.visibility import Purpose, ReleaseSet, visible_release_ids

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
EVERY_SCOPE = tuple(ApprovalScope.values)
DRAFT = ReleaseStatus.DRAFT
STAGED = ReleaseStatus.STAGED
PUBLISHED = ReleaseStatus.PUBLISHED
SUPERSEDED = ReleaseStatus.SUPERSEDED
WITHDRAWN = ReleaseStatus.WITHDRAWN
NOTHING = ReleaseSet(frozenset(), frozenset())
DAY = datetime.timedelta(days=1)


def stored(obj) -> dict:
    return type(obj)._base_manager.filter(pk=obj.pk).values().get()


def status(release) -> str:
    return stored(release)["status"]


def staged(*scopes, **fields):
    """A staged release with these scopes approved; internal by default."""
    release = approvable_release(status=STAGED, **fields)
    approve(release, *(scopes or (INTERNAL,)))
    return release


def published(*scopes, **fields):
    release = approvable_release(status=PUBLISHED, **fields)
    approve(release, *(scopes or (INTERNAL,)))
    return release


def request_for(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


def visitors_see(pinned=None):
    return visible_release_ids(None, Purpose.VIEW, pinned).ids


def actor_of(kind):
    if kind == "none":
        return None
    if kind == "unsaved":
        return StaffUserFactory.build()
    if kind == "not staff":
        return StaffUserFactory(is_staff=False)
    return StaffUserFactory(is_active=False)


# ── Publishing ──────────────────────────────────────────────────────────────


def test_publishing_a_staged_release_records_who_published_it(switch_on):
    publisher = StaffUserFactory(username="publisher-one")
    release = staged()
    before = timezone.now()
    result = publish_release(release, published_by=publisher)
    row = stored(release)
    assert row["status"] == PUBLISHED
    assert before <= row["published_at"] <= timezone.now()
    assert (row["published_by_id"], row["published_by_username"]) == (
        publisher.pk,
        "publisher-one",
    )
    assert (result.pk, result.status) == (release.pk, PUBLISHED)


def test_publishing_is_not_public(switch_on, staff_user):
    release = staged()
    publish_release(release.pk, published_by=staff_user)
    assert visitors_see() == frozenset()
    staff = request_for(StaffUserFactory())
    assert visible_release_ids(staff, Purpose.VIEW).ids == {release.pk}


def _off(settings, staff_user):
    settings.TYPED_GRAPH_ENABLED = False
    return staged(), {"published_by": staff_user}


def _actor(kind):
    def build(settings, staff_user):
        return staged(), {"published_by": actor_of(kind)}

    return build


def _status(value):
    def build(settings, staff_user):
        release = approvable_release(status=value)
        approve(release, INTERNAL)
        return release, {"published_by": staff_user}

    return build


def _no_approval(settings, staff_user):
    return approvable_release(status=STAGED), {"published_by": staff_user}


def _internal_revoked(settings, staff_user):
    release = staged()
    revoke(
        UsageApproval.objects.get(release=release),
        reason="Ended.",
        revoked_by=staff_user,
    )
    return release, {"published_by": staff_user}


def _diligence_reopened(settings, staff_user):
    release = staged()
    record = DueDiligenceRecord.objects.get(dataset=release.dataset)
    record.status = DueDiligenceStatus.OPEN
    record._changed_by = staff_user
    record.save()
    return release, {"published_by": staff_user}


def _target_of_another_dataset(settings, staff_user):
    release = staged()
    target = published()
    # Rows written around the record rules, which refuse this.
    DatasetRelease._base_manager.filter(pk=release.pk).update(supersedes=target)
    return release, {"published_by": staff_user}


def _target_with_another_synthetic_flag(settings, staff_user):
    target = published()
    release = staged(dataset=target.dataset, is_synthetic=True)
    DatasetRelease._base_manager.filter(pk=release.pk).update(supersedes=target)
    return release, {"published_by": staff_user}


def _target(value, **fields):
    def build(settings, staff_user):
        target = approvable_release(status=value, **fields)
        approve(target, INTERNAL)
        return (
            staged(dataset=target.dataset, supersedes=target),
            {"published_by": staff_user},
        )

    return build


PUBLISH_REFUSALS = {
    "switch off": (_off, "TYPED_GRAPH_ENABLED"),
    "no actor": (_actor("none"), "published_by must be an active staff"),
    "unsaved actor": (_actor("unsaved"), "published_by must be an active staff"),
    "actor not staff": (_actor("not staff"), "published_by must be an active staff"),
    "inactive actor": (
        _actor("inactive staff"),
        "published_by must be an active staff",
    ),
    "a draft": (_status(DRAFT), "is draft: only a staged release is published"),
    "already published": (_status(PUBLISHED), "only a staged release"),
    "superseded": (_status(SUPERSEDED), "only a staged release"),
    "withdrawn": (_status(WITHDRAWN), "only a staged release"),
    "no approval": (_no_approval, "no effective internal approval"),
    "internal revoked": (_internal_revoked, "no effective internal approval"),
    "due diligence reopened": (_diligence_reopened, "no effective internal approval"),
    "target in another dataset": (_target_of_another_dataset, "not its own dataset"),
    "target with another synthetic flag": (
        _target_with_another_synthetic_flag,
        "whose synthetic flag differs",
    ),
    "target a draft": (_target(DRAFT), "only a published release is superseded"),
    "target staged": (_target(STAGED), "only a published release is superseded"),
    "target superseded": (_target(SUPERSEDED), "is already superseded"),
    "target withdrawn before publication": (
        _target(WITHDRAWN),
        "was withdrawn before it was published",
    ),
}


@pytest.mark.parametrize("case", sorted(PUBLISH_REFUSALS))
def test_each_publishing_rule_refuses_and_changes_nothing(
    db, settings, staff_user, case
):
    settings.TYPED_GRAPH_ENABLED = True
    build, says = PUBLISH_REFUSALS[case]
    release, kwargs = build(settings, staff_user)
    rows = list(DatasetRelease.objects.order_by("pk").values())
    with pytest.raises(ReleaseError, match=says) as refusal:
        publish_release(release, **kwargs)
    assert "Nothing was written." in str(refusal.value)
    assert list(DatasetRelease.objects.order_by("pk").values()) == rows


def test_an_unknown_release_is_neither_published_nor_withdrawn(switch_on, staff_user):
    for release in (10**12, 2**80, "1", None, DatasetReleaseFactory.build()):
        with pytest.raises(ReleaseError, match="Unknown release"):
            publish_release(release, published_by=staff_user)
        with pytest.raises(ReleaseError, match="Unknown release"):
            withdraw_release(release, reason="Why.", withdrawn_by=staff_user)


def test_publishing_supersedes_the_corrected_release_in_the_same_transaction(
    switch_on, staff_user, monkeypatch
):
    old = published()
    new = staged(dataset=old.dataset, supersedes=old)
    guard = DatasetRelease.guard_update

    def refuse_the_correction(self, stored, changed, using, locked):
        if self.pk == new.pk:
            raise ImmutableRecordError("Refused for the test.")
        return guard(self, stored, changed, using, locked)

    # The correction's own save fails after the old release was marked: the
    # mark goes with it.
    monkeypatch.setattr(DatasetRelease, "guard_update", refuse_the_correction)
    with pytest.raises(ImmutableRecordError, match="Refused for the test"):
        publish_release(new, published_by=staff_user)
    assert (status(old), status(new)) == (PUBLISHED, STAGED)
    monkeypatch.undo()
    publish_release(new, published_by=staff_user)
    assert (status(old), status(new)) == (SUPERSEDED, PUBLISHED)


def test_an_internal_only_correction_supersedes_an_internal_only_release(
    switch_on, staff_user
):
    # A private stack publishes and supersedes with internal approvals alone.
    old = published()
    new = staged(dataset=old.dataset, supersedes=old)
    publish_release(new, published_by=staff_user)
    assert (status(old), status(new)) == (SUPERSEDED, PUBLISHED)
    staff = request_for(staff_user)
    assert visible_release_ids(staff, Purpose.VIEW).ids == {new.pk}


def test_a_correction_of_a_public_release_waits_for_its_own_public_view(
    switch_on, staff_user
):
    old = published(INTERNAL, PUBLIC_VIEW)
    new = staged(dataset=old.dataset, supersedes=old)
    assert visitors_see() == {old.pk}
    rows = list(DatasetRelease.objects.order_by("pk").values())
    with pytest.raises(ReleaseError, match=f"Approve public_view for release {new.pk}"):
        publish_release(new, published_by=staff_user)
    assert list(DatasetRelease.objects.order_by("pk").values()) == rows
    assert visitors_see() == {old.pk}

    approve(new, PUBLIC_VIEW)
    publish_release(new, published_by=staff_user)
    assert (status(old), status(new)) == (SUPERSEDED, PUBLISHED)
    # Visitors' default set moves from the old release to its correction; the
    # old one shows only when pinned, flagged.
    assert visitors_see() == {new.pk}
    assert visible_release_ids(None, Purpose.VIEW, [old.pk, new.pk]) == ReleaseSet(
        frozenset({old.pk, new.pk}), frozenset({old.pk})
    )


def test_an_embargoed_correction_cannot_supersede_a_public_release(
    switch_on, staff_user
):
    old = published(INTERNAL, PUBLIC_VIEW)
    new = staged(
        INTERNAL,
        PUBLIC_VIEW,
        dataset=old.dataset,
        supersedes=old,
        embargo_until=timezone.now() + DAY,
    )
    with pytest.raises(ReleaseError, match="would hide public data"):
        publish_release(new, published_by=staff_user)
    assert (status(old), status(new)) == (PUBLISHED, STAGED)
    assert visitors_see() == {old.pk}


def test_a_correction_withdrawn_before_publication_frees_its_target(
    switch_on, staff_user
):
    first = published(INTERNAL, PUBLIC_VIEW)
    abandoned = staged(INTERNAL, PUBLIC_VIEW, dataset=first.dataset, supersedes=first)
    withdraw_release(abandoned, reason="Wrong files.", withdrawn_by=staff_user)
    # A correction of the withdrawn one is refused, naming the original.
    stray = staged(INTERNAL, PUBLIC_VIEW, dataset=first.dataset, supersedes=abandoned)
    with pytest.raises(ReleaseError, match=f"name release {first.pk}"):
        publish_release(stray, published_by=staff_user)
    third = staged(INTERNAL, PUBLIC_VIEW, dataset=first.dataset, supersedes=first)
    publish_release(third, published_by=staff_user)
    assert (status(first), status(abandoned), status(third)) == (
        SUPERSEDED,
        WITHDRAWN,
        PUBLISHED,
    )
    assert visitors_see() == {third.pk}


def test_of_two_corrections_of_one_release_only_the_first_is_published(
    switch_on, staff_user
):
    old = published()
    first = staged(dataset=old.dataset, supersedes=old)
    second = staged(dataset=old.dataset, supersedes=old)
    publish_release(first, published_by=staff_user)
    rows = list(DatasetRelease.objects.order_by("pk").values())
    with pytest.raises(ReleaseError, match="is already superseded"):
        publish_release(second, published_by=staff_user)
    assert list(DatasetRelease.objects.order_by("pk").values()) == rows
    assert (status(old), status(first), status(second)) == (
        SUPERSEDED,
        PUBLISHED,
        STAGED,
    )


def test_a_release_withdrawn_after_publication_may_be_corrected_and_stays_withdrawn(
    switch_on, staff_user
):
    old = published()
    withdraw_release(old, reason="Errors found.", withdrawn_by=staff_user)
    new = staged(dataset=old.dataset, supersedes=old)
    publish_release(new, published_by=staff_user)
    assert (status(old), status(new)) == (WITHDRAWN, PUBLISHED)


def test_a_release_corrected_once_is_not_corrected_again_once_withdrawn(
    switch_on, staff_user
):
    old = published()
    first = staged(dataset=old.dataset, supersedes=old)
    publish_release(first, published_by=staff_user)
    withdraw_release(old, reason="Errors found.", withdrawn_by=staff_user)
    second = staged(dataset=old.dataset, supersedes=old)
    with pytest.raises(ReleaseError, match=f"release {first.pk} already corrected"):
        publish_release(second, published_by=staff_user)
    assert (status(first), status(second)) == (PUBLISHED, STAGED)


@pytest.mark.postgres
def test_publishing_locks_the_release_and_its_target_before_reading_them(
    switch_on, staff_user
):
    old = published()
    new = staged(dataset=old.dataset, supersedes=old)
    with CaptureQueriesContext(connection) as queries:
        publish_release(new, published_by=staff_user)
    sql = [query["sql"] for query in queries.captured_queries]
    locking = [n for n, text in enumerate(sql) if "FOR UPDATE" in text]
    first = sql[locking[0]]
    assert 'FROM "graph_datasetrelease"' in first and "IN" in first
    assert f"{old.pk}, {new.pk}" in first
    reading = [n for n, text in enumerate(sql) if "graph_usageapproval" in text]
    assert locking[0] < reading[0]


# ── Withdrawing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [STAGED, PUBLISHED, SUPERSEDED])
def test_withdrawing_records_who_and_why_and_hides_the_release_at_once(
    switch_on, value
):
    withdrawer = StaffUserFactory(username="withdrawer")
    release = approvable_release(status=value)
    approve(release, *EVERY_SCOPE)
    staff = request_for(StaffUserFactory())
    pinned = [release.pk]
    assert visible_release_ids(staff, Purpose.VIEW, pinned).ids == {release.pk}
    before = timezone.now()
    result = withdraw_release(
        release, reason="  Retracted by its authors.  ", withdrawn_by=withdrawer
    )
    row = stored(release)
    assert (row["status"], row["withdrawn_reason"]) == (
        WITHDRAWN,
        "Retracted by its authors.",
    )
    assert before <= row["withdrawn_at"] <= timezone.now()
    assert (row["withdrawn_by_id"], row["withdrawn_by_username"]) == (
        withdrawer.pk,
        "withdrawer",
    )
    assert result.status == WITHDRAWN
    for request in (None, staff):
        for purpose in Purpose.values:
            assert visible_release_ids(request, purpose) == NOTHING
            assert visible_release_ids(request, purpose, pinned) == NOTHING
    # Its rows are kept.
    assert UsageApproval.objects.filter(release=release).count() == 4
    assert ReleaseLicence.objects.filter(release=release).exists()


@pytest.mark.parametrize("value", [DRAFT, WITHDRAWN])
def test_only_a_staged_published_or_superseded_release_is_withdrawn(
    switch_on, staff_user, value
):
    release = DatasetReleaseFactory(status=value)
    before = stored(release)
    with pytest.raises(ReleaseError, match="only a staged, published or superseded"):
        withdraw_release(release, reason="Why.", withdrawn_by=staff_user)
    assert stored(release) == before


@pytest.mark.parametrize("reason", ["", "   ", None, 5])
def test_a_withdrawal_states_its_reason(switch_on, staff_user, reason):
    release = DatasetReleaseFactory(status=PUBLISHED)
    before = stored(release)
    with pytest.raises(ReleaseError, match="A reason must be stated"):
        withdraw_release(release, reason=reason, withdrawn_by=staff_user)
    assert stored(release) == before


@pytest.mark.parametrize("actor", ["none", "unsaved", "not staff", "inactive staff"])
def test_a_withdrawal_is_made_only_by_active_staff(switch_on, actor):
    release = DatasetReleaseFactory(status=PUBLISHED)
    before = stored(release)
    with pytest.raises(ReleaseError, match="withdrawn_by must be an active staff"):
        withdraw_release(release, reason="Why.", withdrawn_by=actor_of(actor))
    assert stored(release) == before


def test_nothing_is_withdrawn_while_the_switch_is_off(db, settings, staff_user):
    release = DatasetReleaseFactory(status=PUBLISHED)
    before = stored(release)
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(ReleaseError, match="TYPED_GRAPH_ENABLED"):
        withdraw_release(release, reason="Why.", withdrawn_by=staff_user)
    assert stored(release) == before


def test_withdrawing_a_correction_does_not_bring_back_what_it_superseded(
    switch_on, staff_user
):
    old = published(INTERNAL, PUBLIC_VIEW)
    new = staged(INTERNAL, PUBLIC_VIEW, dataset=old.dataset, supersedes=old)
    publish_release(new, published_by=staff_user)
    withdraw_release(new, reason="Errors found.", withdrawn_by=staff_user)
    assert (status(old), status(new)) == (SUPERSEDED, WITHDRAWN)
    assert visitors_see() == frozenset()
    # The old release is still there for whoever pins it, flagged.
    assert visible_release_ids(None, Purpose.VIEW, [old.pk, new.pk]) == ReleaseSet(
        frozenset({old.pk}), frozenset({old.pk})
    )


# ── Two transactions at once ────────────────────────────────────────────────


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_withdrawal_and_a_publication_at_once_never_lose_the_withdrawal(settings):
    """Both lock the release being withdrawn and corrected. Whichever comes
    second waits, then reads it as the first left it: a publication after
    the withdrawal leaves it withdrawn, and a withdrawal after the
    publication withdraws the superseded release."""
    settings.TYPED_GRAPH_ENABLED = True
    staff = StaffUserFactory()
    for first in ("withdraw", "publish"):
        old = staged()
        publish_release(old, published_by=staff)
        new = staged(dataset=old.dataset, supersedes=old)
        withdraw = partial(
            withdraw_release, old.pk, reason="Withdrawn upstream.", withdrawn_by=staff
        )
        publish = partial(publish_release, new.pk, published_by=staff)
        order = (withdraw, publish) if first == "withdraw" else (publish, withdraw)
        assert _race(*order) == [], first
        row = stored(old)
        assert (row["status"], row["withdrawn_reason"]) == (
            WITHDRAWN,
            "Withdrawn upstream.",
        ), first
        assert status(new) == PUBLISHED, first


def _race(first, second):
    """Run `first` in a transaction held open until `second`, started
    meanwhile on another connection, waits on a lock; then let both finish.
    Returns what either raised."""
    holding, go = threading.Event(), threading.Event()
    errors = []

    def run_first():
        try:
            with transaction.atomic():
                first()
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the caller
            errors.append(exc)
        finally:
            connection.close()

    def run_second():
        try:
            holding.wait(30)
            second()
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run_first), threading.Thread(target=run_second)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first transaction never started"
        assert _a_backend_waits_on_a_lock(), "the second never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)
    return errors


def _a_backend_waits_on_a_lock(seconds=20) -> bool:
    deadline = time.monotonic() + seconds
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.05)
    return False
