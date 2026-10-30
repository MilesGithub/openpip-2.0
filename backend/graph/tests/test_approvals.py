"""Usage approvals: recorded decisions and their effectiveness.

The first part tests the decisions' own rules, which hold on every write
path (graph/models.py): append-only rows, revoked once, one active decision
per release and scope. The services in graph/approvals.py are tested after
it.
"""

import datetime
import threading
import time

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from graph import approvals
from graph.approvals import ApprovalError
from graph.licensing import confirm_due_diligence, record_licence
from graph.models import (
    ApprovalScope,
    Decision,
    DueDiligenceRecord,
    DueDiligenceStatus,
    ImmutableRecordError,
    Permission,
    ReleaseLicence,
    ReleaseStatus,
    UsageApproval,
)
from graph.tests.factories import (
    DatasetReleaseFactory,
    DueDiligenceRecordFactory,
    ReleaseLicenceFactory,
    StaffUserFactory,
    UsageApprovalFactory,
    approvable_release,
    approve,
    confirm_diligence,
)
from graph.visibility import Purpose, visible_release_ids

LATER = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)


def stored(obj) -> dict:
    return type(obj)._base_manager.filter(pk=obj.pk).values().get()


def revoke(approval, reason="Test only.", by=None):
    approval.revoked_at = timezone.now()
    approval.revoked_by = by or StaffUserFactory()
    approval.revoke_reason = reason
    approval.save(
        update_fields=[
            "revoked_at",
            "revoked_by",
            "revoked_by_username",
            "revoke_reason",
        ]
    )


# ── Record rules: decisions ─────────────────────────────────────────────────


def test_a_decision_records_who_decided_and_why(db):
    actor = StaffUserFactory(username="approver")
    approval = UsageApprovalFactory(decided_by=actor, rationale="Licence allows it.")
    row = stored(approval)
    assert (row["decided_by_id"], row["decided_by_username"]) == (actor.pk, "approver")
    assert (row["rationale"], row["revoked_at"]) == ("Licence allows it.", None)
    assert approval.active
    assert approval.licence.release_id == approval.release_id


def test_one_active_decision_per_release_and_scope(db):
    approval = UsageApprovalFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        UsageApprovalFactory(release=approval.release, decision=Decision.DENIED)
    # Another scope, or another release, is another decision.
    UsageApprovalFactory(
        release=approval.release, scope=ApprovalScope.EXTERNAL_SERVICES
    )
    UsageApprovalFactory()
    # Once revoked, a new decision may be recorded, and revoked ones pile up.
    revoke(approval)
    replacement = UsageApprovalFactory(release=approval.release)
    revoke(replacement)
    UsageApprovalFactory(release=approval.release, decision=Decision.DENIED)
    scoped = UsageApproval.objects.filter(
        release=approval.release, scope=ApprovalScope.INTERNAL
    )
    assert scoped.count() == 3
    assert scoped.filter(revoked_at__isnull=True).count() == 1


def changed_value(field, approval):
    return {
        "release": lambda: DatasetReleaseFactory(),
        "scope": lambda: ApprovalScope.EXTERNAL_SERVICES,
        "decision": lambda: Decision.DENIED,
        "licence": lambda: ReleaseLicenceFactory(
            release=approval.release, open_terms=True
        ),
        "rationale": lambda: "A better reason.",
        "evidence": lambda: "https://example.org/other",
        "decided_by": lambda: StaffUserFactory(),
        "decided_by_username": lambda: "someone-else",
        "decided_at": lambda: LATER,
    }[field]()


NEVER_EDITED = [
    "release",
    "scope",
    "decision",
    "licence",
    "rationale",
    "evidence",
    "decided_by",
    "decided_by_username",
    "decided_at",
]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize(
    "path", ["save", "save_update_fields", "update", "bulk_update"]
)
@pytest.mark.parametrize("field", NEVER_EDITED)
def test_a_decision_is_never_edited(db, field, path, revoked):
    approval = UsageApprovalFactory()
    if revoked:
        revoke(approval)
    value = changed_value(field, approval)
    before = stored(approval)
    with pytest.raises(ImmutableRecordError, match="UsageApproval"):
        if path == "update":
            UsageApproval.objects.filter(pk=approval.pk).update(**{field: value})
        else:
            setattr(approval, field, value)
            if path == "save":
                approval.save()
            elif path == "save_update_fields":
                approval.save(update_fields=[field])
            else:
                UsageApproval.objects.bulk_update([approval], [field])
    assert stored(approval) == before


def test_a_revocation_sets_the_revoked_fields_once(db):
    revoker = StaffUserFactory(username="revoker")
    approval = UsageApprovalFactory()
    revoke(approval, reason="Terms changed.", by=revoker)
    row = stored(approval)
    assert row["revoked_at"] is not None
    assert (row["revoked_by_id"], row["revoked_by_username"]) == (revoker.pk, "revoker")
    assert row["revoke_reason"] == "Terms changed."
    assert not UsageApproval.objects.get(pk=approval.pk).active
    # Never again, not even to correct the reason.
    for field, value in (
        ("revoke_reason", "Another reason."),
        ("revoked_at", LATER),
        ("revoked_by", StaffUserFactory()),
    ):
        fresh = UsageApproval.objects.get(pk=approval.pk)
        setattr(fresh, field, value)
        with pytest.raises(ImmutableRecordError, match="already revoked"):
            fresh.save()
    assert stored(approval) == row


def test_the_revoked_fields_are_set_together(db):
    approval = UsageApprovalFactory()
    before = stored(approval)
    # Without revoked_at, setting the others is no revocation.
    approval.revoke_reason = "Half a revocation."
    approval.revoked_by = StaffUserFactory()
    with pytest.raises(ImmutableRecordError, match="set together"):
        approval.save()
    assert stored(approval) == before
    # And the database refuses a revocation without its reason or actor.
    fresh = UsageApproval.objects.get(pk=approval.pk)
    fresh.revoked_at = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        fresh.save()
    assert stored(approval) == before


@pytest.mark.parametrize("path", ["update", "bulk_update"])
def test_a_queryset_cannot_revoke_a_decision_either(db, path):
    approval = UsageApprovalFactory()
    before = stored(approval)
    changes = {
        "revoked_at": timezone.now(),
        "revoked_by_username": "graph-staff",
        "revoke_reason": "In bulk.",
    }
    with pytest.raises(ImmutableRecordError, match="UsageApproval"):
        if path == "update":
            UsageApproval.objects.filter(pk=approval.pk).update(**changes)
        else:
            for name, value in changes.items():
                setattr(approval, name, value)
            UsageApproval.objects.bulk_update([approval], list(changes))
    assert stored(approval) == before


@pytest.mark.parametrize("path", ["delete", "queryset_delete", "bulk_create"])
def test_decisions_are_never_deleted_or_inserted_in_bulk(db, path):
    approval = UsageApprovalFactory()
    with pytest.raises(ImmutableRecordError, match="UsageApproval"):
        if path == "delete":
            approval.delete()
        elif path == "queryset_delete":
            UsageApproval.objects.filter(release=approval.release).delete()
        else:
            UsageApproval.objects.bulk_create(
                [UsageApprovalFactory.build(release=approval.release)]
            )
    assert list(UsageApproval.objects.all()) == [approval]


@pytest.mark.parametrize(
    "fields",
    [
        # A rationale is mandatory, and so is the name of who decided.
        {"rationale": ""},
        {"decided_by": None},
        # An approval relies on a licence snapshot.
        {"licence": None},
        # Public scopes need recorded evidence (open question C2).
        {"scope": ApprovalScope.PUBLIC_VIEW, "evidence": ""},
        {"scope": ApprovalScope.PUBLIC_BULK, "evidence": ""},
        # Closed sets.
        {"scope": "public"},
        {"decision": "maybe"},
        # A revocation states when, why and by whom.
        {"revoked_at": LATER},
        {"revoked_at": LATER, "revoke_reason": "Why."},
        {"revoke_reason": "Why.", "revoked_by_username": "graph-staff"},
    ],
)
def test_the_database_refuses_an_incomplete_decision(db, fields):
    release = DatasetReleaseFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        UsageApprovalFactory(release=release, **fields)
    assert not UsageApproval.objects.exists()


@pytest.mark.parametrize(
    "fields",
    [
        # A denial need not rely on a snapshot, nor give evidence.
        {"decision": Decision.DENIED, "licence": None},
        {
            "decision": Decision.DENIED,
            "scope": ApprovalScope.PUBLIC_VIEW,
            "evidence": "",
        },
        # Evidence is required of public approvals only.
        {"scope": ApprovalScope.INTERNAL, "evidence": ""},
        {"scope": ApprovalScope.EXTERNAL_SERVICES, "evidence": ""},
        # A decision recorded already revoked, complete.
        {
            "revoked_at": LATER,
            "revoke_reason": "Why.",
            "revoked_by_username": "graph-staff",
        },
    ],
)
def test_the_database_accepts_a_complete_decision(db, fields):
    UsageApprovalFactory(**fields)
    assert UsageApproval.objects.count() == 1


def test_a_decision_relies_on_a_snapshot_of_its_own_release(db):
    other = ReleaseLicenceFactory(open_terms=True)
    release = DatasetReleaseFactory()
    with pytest.raises(ImmutableRecordError, match="UsageApproval.licence"):
        UsageApprovalFactory(release=release, licence=other)
    assert not UsageApproval.objects.exists()


def test_deleting_the_approvers_and_revokers_accounts_keeps_the_decision(db):
    approver = StaffUserFactory(username="approver")
    revoker = StaffUserFactory(username="revoker")
    approval = UsageApprovalFactory(decided_by=approver)
    revoke(approval, by=revoker)
    approver.delete()
    revoker.delete()
    row = stored(approval)
    assert (row["decided_by_id"], row["decided_by_username"]) == (None, "approver")
    assert (row["revoked_by_id"], row["revoked_by_username"]) == (None, "revoker")
    assert row["revoked_at"] is not None


def test_a_release_with_decisions_cannot_be_deleted(db):
    # A draft release, which could otherwise be deleted.
    approval = UsageApprovalFactory(decision=Decision.DENIED, licence=None)
    with pytest.raises(ProtectedError):
        approval.release.delete()
    assert UsageApproval.objects.filter(pk=approval.pk).exists()


def test_a_snapshot_a_decision_relies_on_is_kept(db):
    approval = UsageApprovalFactory()
    with pytest.raises(ImmutableRecordError, match="ReleaseLicence"):
        approval.licence.delete()
    # Past the guard, PROTECT still keeps it.
    with pytest.raises(ProtectedError):
        type(approval.licence)._base_manager.filter(pk=approval.licence_id).delete()


# ── Services: graph.approvals ───────────────────────────────────────────────


INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
PUBLIC_BULK = ApprovalScope.PUBLIC_BULK
EXTERNAL = ApprovalScope.EXTERNAL_SERVICES
EVERY_SCOPE = frozenset(ApprovalScope.values)
APPROVED = Decision.APPROVED
DENIED = Decision.DENIED
COMPLETE = DueDiligenceStatus.COMPLETE
EVIDENCE = "https://example.org/evidence"
DAY = datetime.timedelta(days=1)


def decide(release, scope, decision=APPROVED, *, by=None, evidence=EVIDENCE):
    """A decision recorded through the service."""
    return approvals.record_decision(
        release,
        scope,
        decision,
        rationale="Test only.",
        decided_by=by or StaffUserFactory(),
        evidence=evidence,
    )


def save_as(record, actor, **changes):
    for name, value in changes.items():
        setattr(record, name, value)
    record._changed_by = actor
    record.save()


def diligence(release):
    return DueDiligenceRecord.objects.get(dataset=release.dataset)


def latest(release):
    return ReleaseLicence.objects.filter(release=release).order_by("-pk").first()


def actor_of(kind):
    if kind == "none":
        return None
    if kind == "unsaved":
        return StaffUserFactory.build()
    if kind == "not staff":
        return StaffUserFactory(is_staff=False)
    return StaffUserFactory(is_active=False)


# Each way an approval is refused while a denial is not: a builder of the
# release, the scope and the evidence, and what the refusal says.


def _withdrawn():
    return approvable_release(status=ReleaseStatus.WITHDRAWN), INTERNAL, ""


def _no_snapshot():
    release = DatasetReleaseFactory()
    confirm_diligence(release.dataset)
    return release, INTERNAL, ""


def _latest_records_no_terms():
    release = approvable_release()
    # An older snapshot that records terms does not stand in for the latest.
    ReleaseLicenceFactory(release=release)
    confirm_diligence(release.dataset)
    return release, INTERNAL, ""


def _latest_without_where_it_was_read():
    release = approvable_release()
    ReleaseLicenceFactory(release=release, open_terms=True, read_at="")
    confirm_diligence(release.dataset)
    return release, INTERNAL, ""


def _no_due_diligence():
    release = DatasetReleaseFactory()
    ReleaseLicenceFactory(release=release, open_terms=True)
    return release, INTERNAL, ""


def _due_diligence(**fields):
    def build():
        release = DatasetReleaseFactory()
        ReleaseLicenceFactory(release=release, open_terms=True)
        DueDiligenceRecordFactory(dataset=release.dataset, **fields)
        return release, INTERNAL, ""

    return build


def _confirmed_before_the_latest_snapshot():
    release = approvable_release()
    ReleaseLicenceFactory(release=release, open_terms=True)
    return release, INTERNAL, ""


def _without(prerequisite, scope, *held):
    def build():
        release = approvable_release()
        for granted in held:
            decide(release, granted)
        if prerequisite == "denied":
            decide(release, PREREQUISITE[scope], DENIED)
        return release, scope, EVIDENCE

    return build


PREREQUISITE = {PUBLIC_VIEW: INTERNAL, EXTERNAL: INTERNAL, PUBLIC_BULK: PUBLIC_VIEW}


def _terms(scope, held, **terms):
    def build():
        release = approvable_release(terms=terms)
        for granted in held:
            approve(release, granted)
        return release, scope, EVIDENCE

    return build


def _terms_changed_after(scope, held, **terms):
    """The prerequisites approved under open terms, then a newer snapshot,
    which due diligence covers."""

    def build():
        release = approvable_release()
        for granted in held:
            decide(release, granted)
        ReleaseLicenceFactory(release=release, open_terms=True, **terms)
        confirm_diligence(release.dataset)
        return release, scope, EVIDENCE

    return build


def _no_evidence(scope, held, evidence):
    def build():
        release = approvable_release()
        for granted in held:
            decide(release, granted)
        return release, scope, evidence

    return build


UNKNOWN = Permission.UNKNOWN
NOT_PERMITTED = Permission.NOT_PERMITTED
APPROVAL_REFUSALS = {
    "withdrawn release": (_withdrawn, "it is withdrawn"),
    "no licence snapshot": (_no_snapshot, "has no licence snapshot"),
    "latest snapshot records no terms": (_latest_records_no_terms, "records no terms"),
    "latest snapshot says not where it was read": (
        _latest_without_where_it_was_read,
        "records no terms",
    ),
    "no due-diligence record": (_no_due_diligence, "has no due-diligence record"),
    "due diligence open": (_due_diligence(), "is open, not complete"),
    "due diligence blocked": (
        _due_diligence(status=DueDiligenceStatus.BLOCKED),
        "is blocked, not complete",
    ),
    "terms time-limited": (
        _due_diligence(status=COMPLETE, terms_time_limited=True),
        "time-limited",
    ),
    "due diligence confirmed before the latest snapshot": (
        _confirmed_before_the_latest_snapshot,
        "Confirm that it covers those terms first",
    ),
    "public_view without internal": (
        _without("missing", PUBLIC_VIEW),
        "public_view needs an active internal approval",
    ),
    "public_view with internal denied": (
        _without("denied", PUBLIC_VIEW),
        "public_view needs an active internal approval",
    ),
    "external_services without internal": (
        _without("missing", EXTERNAL),
        "external_services needs an active internal approval",
    ),
    "public_bulk without public_view": (
        _without("missing", PUBLIC_BULK, INTERNAL),
        "public_bulk needs an active public_view approval",
    ),
    "public_bulk with public_view denied": (
        _without("denied", PUBLIC_BULK, INTERNAL),
        "public_bulk needs an active public_view approval",
    ),
    "public_view, redistribution unknown": (
        _terms(PUBLIC_VIEW, [INTERNAL], redistribution=UNKNOWN),
        "redistribution as unknown",
    ),
    "public_view, redistribution not permitted": (
        _terms(PUBLIC_VIEW, [INTERNAL], redistribution=NOT_PERMITTED),
        "redistribution as not_permitted",
    ),
    "public_view without evidence": (
        _no_evidence(PUBLIC_VIEW, [INTERNAL], ""),
        "records its evidence",
    ),
    "public_view with blank evidence": (
        _no_evidence(PUBLIC_VIEW, [INTERNAL], "  \n "),
        "records its evidence",
    ),
    "public_bulk, redistribution no longer permitted": (
        _terms_changed_after(
            PUBLIC_BULK, [INTERNAL, PUBLIC_VIEW], redistribution=NOT_PERMITTED
        ),
        "redistribution as not_permitted",
    ),
    "public_bulk, modification unknown": (
        _terms(PUBLIC_BULK, [INTERNAL, PUBLIC_VIEW], modification=UNKNOWN),
        "modification as unknown",
    ),
    "public_bulk, modification not permitted": (
        _terms(PUBLIC_BULK, [INTERNAL, PUBLIC_VIEW], modification=NOT_PERMITTED),
        "modification as not_permitted",
    ),
    "public_bulk without evidence": (
        _no_evidence(PUBLIC_BULK, [INTERNAL, PUBLIC_VIEW], ""),
        "records its evidence",
    ),
}


@pytest.mark.parametrize("case", sorted(APPROVAL_REFUSALS))
def test_each_rule_for_approvals_refuses_and_writes_nothing(
    switch_on, staff_user, case
):
    build, says = APPROVAL_REFUSALS[case]
    release, scope, evidence = build()
    rows = list(UsageApproval.objects.values())
    with pytest.raises(ApprovalError, match=says) as refusal:
        approvals.record_decision(
            release,
            scope,
            APPROVED,
            rationale="Terms allow it.",
            decided_by=staff_user,
            evidence=evidence,
        )
    assert "Nothing was written." in str(refusal.value)
    assert list(UsageApproval.objects.values()) == rows
    assert scope not in approvals.effective_scopes(release)


@pytest.mark.parametrize("case", sorted(APPROVAL_REFUSALS))
def test_a_denial_is_recorded_where_an_approval_is_refused(switch_on, staff_user, case):
    # Saying no is always safe, due diligence or not.
    build, _ = APPROVAL_REFUSALS[case]
    release, scope, _ = build()
    denial = approvals.record_decision(
        release, scope, DENIED, rationale="Not yet.", decided_by=staff_user
    )
    assert (denial.decision, denial.scope, denial.licence) == (
        DENIED,
        scope,
        latest(release),
    )
    assert denial.active
    assert scope not in approvals.effective_scopes(release)


def test_no_decision_is_recorded_while_the_switch_is_off(db, settings, staff_user):
    release = approvable_release()
    settings.TYPED_GRAPH_ENABLED = False
    for decision in Decision.values:
        with pytest.raises(ApprovalError, match="TYPED_GRAPH_ENABLED"):
            decide(release, INTERNAL, decision, by=staff_user)
    assert not UsageApproval.objects.exists()


@pytest.mark.parametrize("decision", Decision.values)
@pytest.mark.parametrize("actor", ["none", "unsaved", "not staff", "inactive staff"])
def test_a_decision_is_recorded_only_by_active_staff(switch_on, actor, decision):
    release = approvable_release()
    with pytest.raises(ApprovalError, match="decided_by must be an active staff"):
        approvals.record_decision(
            release, INTERNAL, decision, rationale="Why.", decided_by=actor_of(actor)
        )
    assert not UsageApproval.objects.exists()


@pytest.mark.parametrize(
    "scope,decision",
    [
        ("public", APPROVED),
        ("PUBLIC_VIEW", APPROVED),
        ("", DENIED),
        (None, DENIED),
        (INTERNAL, "approve"),
        (INTERNAL, "APPROVED"),
        (INTERNAL, None),
    ],
)
def test_unknown_scopes_and_decisions_are_refused(
    switch_on, staff_user, scope, decision
):
    release = approvable_release()
    with pytest.raises(ApprovalError, match="is one of"):
        approvals.record_decision(
            release, scope, decision, rationale="Why.", decided_by=staff_user
        )
    assert not UsageApproval.objects.exists()


@pytest.mark.parametrize("decision", Decision.values)
@pytest.mark.parametrize("rationale", ["", "   ", "\n\t", None, 7])
def test_a_decision_states_its_rationale(switch_on, staff_user, rationale, decision):
    release = approvable_release()
    with pytest.raises(ApprovalError, match="A rationale must be stated"):
        approvals.record_decision(
            release, INTERNAL, decision, rationale=rationale, decided_by=staff_user
        )
    assert not UsageApproval.objects.exists()


def test_a_decision_on_an_unknown_release_is_refused(switch_on, staff_user):
    for release in (10**12, 2**80, "1", None, DatasetReleaseFactory.build()):
        with pytest.raises(ApprovalError, match="Unknown release"):
            approvals.record_decision(
                release, INTERNAL, DENIED, rationale="Why.", decided_by=staff_user
            )
    assert not UsageApproval.objects.exists()


def test_an_approval_relies_on_the_latest_snapshot_and_records_its_decider(switch_on):
    actor = StaffUserFactory(username="approver")
    release = approvable_release()
    ReleaseLicenceFactory(release=release, open_terms=True)
    # Recorded with an earlier time, the snapshot with the highest id is
    # still the latest.
    last = ReleaseLicenceFactory(
        release=release, open_terms=True, recorded_at=timezone.now() - DAY
    )
    confirm_diligence(release.dataset)
    before = timezone.now()
    approval = approvals.record_decision(
        release,
        INTERNAL,
        APPROVED,
        rationale="  The terms allow internal use.  ",
        decided_by=actor,
        evidence="  https://example.org/terms  ",
    )
    row = stored(approval)
    assert row["licence_id"] == last.pk
    assert (row["rationale"], row["evidence"]) == (
        "The terms allow internal use.",
        "https://example.org/terms",
    )
    assert (row["decided_by_id"], row["decided_by_username"]) == (actor.pk, "approver")
    assert before <= row["decided_at"] <= timezone.now()
    assert row["revoked_at"] is None


def test_a_denial_without_any_snapshot_relies_on_none(switch_on, staff_user):
    release = DatasetReleaseFactory()
    denial = decide(release, PUBLIC_BULK, DENIED, by=staff_user, evidence="")
    assert (denial.licence, denial.evidence) == (None, "")


@pytest.mark.parametrize("first", Decision.values)
@pytest.mark.parametrize("second", Decision.values)
def test_one_active_decision_per_scope_until_it_is_revoked(
    switch_on, staff_user, first, second
):
    release = approvable_release()
    held = decide(release, INTERNAL, first, by=staff_user)
    with pytest.raises(ApprovalError) as refusal:
        decide(release, INTERNAL, second)
    assert f"active internal decision, decision {held.pk} ({first} by" in str(
        refusal.value
    )
    assert list(UsageApproval.objects.all()) == [held]
    # Another scope is another decision.
    decide(release, EXTERNAL, DENIED)
    approvals.revoke(held, reason="Recorded in error.", revoked_by=staff_user)
    again = decide(release, INTERNAL, second)
    assert again.active and not UsageApproval.objects.get(pk=held.pk).active


def test_an_approval_needs_due_diligence_confirmed_after_the_latest_snapshot(
    switch_on, staff_user
):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    decide(release, INTERNAL)
    decide(release, PUBLIC_VIEW)
    newer = record_licence(
        release,
        recorded_by=staff_user,
        licence_name="Corrected licence",
        read_at="https://example.org/terms",
        read_on=datetime.date(2026, 10, 20),
        redistribution=Permission.PERMITTED,
        modification=Permission.PERMITTED,
    )
    # The approvals already recorded stay effective: new terms hide nothing.
    assert approvals.effective_scopes(release) == {INTERNAL, PUBLIC_VIEW}
    assert visible_release_ids(None, Purpose.VIEW).ids == {release.pk}
    # A new approval waits until due diligence is confirmed to cover them.
    with pytest.raises(ApprovalError, match="Confirm that it covers"):
        decide(release, EXTERNAL)
    confirm_due_diligence(diligence(release), by=staff_user, note="Covers v2 terms.")
    external = decide(release, EXTERNAL)
    assert external.licence == newer
    assert approvals.effective_scopes(release) == {INTERNAL, PUBLIC_VIEW, EXTERNAL}


def test_synthetic_releases_need_decisions_but_no_due_diligence(switch_on):
    release = DatasetReleaseFactory(is_synthetic=True)
    ReleaseLicenceFactory(release=release, open_terms=True)
    assert approvals.effective_scopes(release) == frozenset()
    for scope in (INTERNAL, PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL):
        decide(release, scope)
    assert not DueDiligenceRecord.objects.exists()
    assert approvals.effective_scopes(release) == EVERY_SCOPE
    # The terms still need recording.
    bare = DatasetReleaseFactory(is_synthetic=True)
    ReleaseLicenceFactory(release=bare)
    with pytest.raises(ApprovalError, match="records no terms"):
        decide(bare, INTERNAL)
    assert not UsageApproval.objects.filter(release=bare).exists()


# ── Services: revoking and replacing decisions ──────────────────────────────


def test_a_revocation_names_who_revoked_and_why_once(switch_on):
    revoker = StaffUserFactory(username="revoker")
    approval = decide(approvable_release(), INTERNAL)
    before = timezone.now()
    revoked = approvals.revoke(
        approval, reason="  Terms changed.  ", revoked_by=revoker
    )
    row = stored(approval)
    assert row["revoke_reason"] == "Terms changed."
    assert (row["revoked_by_id"], row["revoked_by_username"]) == (revoker.pk, "revoker")
    assert before <= row["revoked_at"] <= timezone.now()
    assert revoked.pk == approval.pk and not revoked.active
    for target in (approval, approval.pk):
        with pytest.raises(ApprovalError, match="already revoked, by revoker"):
            approvals.revoke(target, reason="Again.", revoked_by=revoker)
    assert stored(approval) == row


@pytest.mark.parametrize("reason", ["", "  ", None])
def test_a_revocation_states_its_reason(switch_on, staff_user, reason):
    approval = decide(approvable_release(), INTERNAL)
    before = stored(approval)
    with pytest.raises(ApprovalError, match="A reason must be stated"):
        approvals.revoke(approval, reason=reason, revoked_by=staff_user)
    assert stored(approval) == before


@pytest.mark.parametrize("actor", ["none", "unsaved", "not staff", "inactive staff"])
def test_a_revocation_is_made_only_by_active_staff(switch_on, actor):
    approval = decide(approvable_release(), INTERNAL)
    before = stored(approval)
    with pytest.raises(ApprovalError, match="revoked_by must be an active staff"):
        approvals.revoke(approval, reason="Why.", revoked_by=actor_of(actor))
    assert stored(approval) == before


def test_nothing_is_revoked_or_replaced_while_the_switch_is_off(
    db, settings, staff_user
):
    settings.TYPED_GRAPH_ENABLED = True
    approval = decide(approvable_release(), INTERNAL)
    before = stored(approval)
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(ApprovalError, match="TYPED_GRAPH_ENABLED"):
        approvals.revoke(approval, reason="Why.", revoked_by=staff_user)
    with pytest.raises(ApprovalError, match="TYPED_GRAPH_ENABLED"):
        approvals.replace_decision(approval, rationale="Why.", decided_by=staff_user)
    assert list(UsageApproval.objects.values()) == [before]


def test_an_unknown_decision_is_neither_revoked_nor_replaced(switch_on, staff_user):
    for approval in (10**12, "1", UsageApprovalFactory.build()):
        with pytest.raises(ApprovalError, match="Unknown decision"):
            approvals.revoke(approval, reason="Why.", revoked_by=staff_user)
        with pytest.raises(ApprovalError, match="Unknown decision"):
            approvals.replace_decision(
                approval, rationale="Why.", decided_by=staff_user
            )


def test_revoking_internal_makes_the_scopes_that_need_it_ineffective_at_once(
    switch_on, staff_user
):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    internal, view, bulk, external = (
        decide(release, scope)
        for scope in (INTERNAL, PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL)
    )
    approvals.revoke(internal, reason="Terms withdrawn.", revoked_by=staff_user)
    assert approvals.effective_scopes(release) == frozenset()
    assert visible_release_ids(None, Purpose.VIEW).ids == frozenset()
    # Only that decision is revoked: the others stay active, as recorded...
    active = UsageApproval.objects.filter(revoked_at__isnull=True).order_by("pk")
    assert list(active) == [view, bulk, external]
    # ...and a new internal approval makes them effective again.
    decide(release, INTERNAL)
    assert approvals.effective_scopes(release) == EVERY_SCOPE


def test_revoking_public_view_takes_bulk_with_it_and_leaves_the_rest(
    switch_on, staff_user
):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    _, view, _, _ = (
        decide(release, scope)
        for scope in (INTERNAL, PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL)
    )
    approvals.revoke(view, reason="Not public after all.", revoked_by=staff_user)
    assert approvals.effective_scopes(release) == {INTERNAL, EXTERNAL}


def test_replacing_a_decision_revokes_it_and_records_it_again_on_the_latest_terms(
    switch_on, staff_user
):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    decide(release, INTERNAL)
    view = decide(release, PUBLIC_VIEW)
    newer = ReleaseLicenceFactory(release=release, open_terms=True)
    confirm_diligence(release.dataset)
    assert list(approvals.stale_approvals()) == [
        UsageApproval.objects.get(scope=INTERNAL),
        view,
    ]
    replacer = StaffUserFactory(username="replacer")
    new = approvals.replace_decision(
        view,
        rationale="  Reviewed the new terms.  ",
        decided_by=replacer,
        evidence=EVIDENCE,
    )
    old = stored(view)
    assert old["revoke_reason"] == f"Replaced by decision {new.pk}"
    assert (old["revoked_by_id"], old["revoked_by_username"]) == (
        replacer.pk,
        "replacer",
    )
    row = stored(new)
    assert (row["release_id"], row["scope"], row["decision"]) == (
        release.pk,
        PUBLIC_VIEW,
        APPROVED,
    )
    assert (row["licence_id"], row["rationale"], row["decided_by_username"]) == (
        newer.pk,
        "Reviewed the new terms.",
        "replacer",
    )
    # Recorded in one moment: the new decision starts when the old one ends.
    assert row["decided_at"] == old["revoked_at"]
    assert approvals.effective_scopes(release) == {INTERNAL, PUBLIC_VIEW}
    assert list(approvals.stale_approvals()) == [
        UsageApproval.objects.get(scope=INTERNAL)
    ]
    # Ids keep coming from the same sequence afterwards.
    assert decide(release, EXTERNAL).pk > new.pk


def test_replacing_a_denial_records_a_denial(switch_on, staff_user):
    release = DatasetReleaseFactory()
    denial = decide(release, PUBLIC_VIEW, DENIED, evidence="")
    new = approvals.replace_decision(
        denial, rationale="Still no.", decided_by=staff_user
    )
    assert (new.scope, new.decision, new.licence) == (PUBLIC_VIEW, DENIED, None)
    assert stored(denial)["revoke_reason"] == f"Replaced by decision {new.pk}"


@pytest.mark.parametrize(
    "change",
    ["terms not confirmed", "due diligence reopened", "evidence", "withdrawn"],
)
def test_a_replacement_that_is_refused_changes_nothing(switch_on, staff_user, change):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    decide(release, INTERNAL)
    view = decide(release, PUBLIC_VIEW)
    evidence = EVIDENCE
    if change == "terms not confirmed":
        ReleaseLicenceFactory(release=release, open_terms=True)
    elif change == "due diligence reopened":
        save_as(diligence(release), staff_user, status=DueDiligenceStatus.OPEN)
    elif change == "evidence":
        evidence = " "
    else:
        release.status = ReleaseStatus.WITHDRAWN
        release.withdrawn_at = timezone.now()
        release.withdrawn_reason = "Test only."
        release.withdrawn_by = staff_user
        release.save()
    rows = list(UsageApproval.objects.order_by("pk").values())
    with pytest.raises(ApprovalError, match="Nothing was written"):
        approvals.replace_decision(
            view, rationale="Refresh.", decided_by=staff_user, evidence=evidence
        )
    assert list(UsageApproval.objects.order_by("pk").values()) == rows
    assert UsageApproval.objects.get(pk=view.pk).active


def test_a_revoked_decision_is_not_replaced(switch_on, staff_user):
    approval = decide(approvable_release(), INTERNAL)
    approvals.revoke(approval, reason="Ended.", revoked_by=staff_user)
    with pytest.raises(ApprovalError, match="already revoked"):
        approvals.replace_decision(approval, rationale="Again.", decided_by=staff_user)
    assert UsageApproval.objects.count() == 1


@pytest.mark.parametrize("rationale", ["", " ", None])
def test_a_replacement_states_its_rationale(switch_on, staff_user, rationale):
    approval = decide(approvable_release(), INTERNAL)
    with pytest.raises(ApprovalError, match="A rationale must be stated"):
        approvals.replace_decision(approval, rationale=rationale, decided_by=staff_user)
    assert list(UsageApproval.objects.all()) == [approval]
    assert UsageApproval.objects.get(pk=approval.pk).active


def test_the_decisions_outlive_the_accounts_that_made_them(switch_on):
    approver = StaffUserFactory(username="approver")
    revoker = StaffUserFactory(username="revoker")
    approval = decide(approvable_release(), INTERNAL, by=approver)
    approvals.revoke(approval, reason="Ended.", revoked_by=revoker)
    approver.delete()
    revoker.delete()
    row = stored(approval)
    assert (row["decided_by_id"], row["decided_by_username"]) == (None, "approver")
    assert (row["revoked_by_id"], row["revoked_by_username"]) == (None, "revoker")


# ── Services: effectiveness ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "held,effective",
    [
        ((), ()),
        ((INTERNAL,), (INTERNAL,)),
        ((PUBLIC_VIEW,), ()),
        ((EXTERNAL,), ()),
        ((PUBLIC_BULK,), ()),
        ((PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL), ()),
        ((INTERNAL, PUBLIC_VIEW), (INTERNAL, PUBLIC_VIEW)),
        ((INTERNAL, EXTERNAL), (INTERNAL, EXTERNAL)),
        ((INTERNAL, PUBLIC_BULK), (INTERNAL,)),
        ((INTERNAL, PUBLIC_VIEW, PUBLIC_BULK), (INTERNAL, PUBLIC_VIEW, PUBLIC_BULK)),
        ((INTERNAL, PUBLIC_VIEW, PUBLIC_BULK, EXTERNAL), tuple(EVERY_SCOPE)),
    ],
)
def test_a_scope_is_effective_with_an_approval_and_its_prerequisites(
    switch_on, held, effective
):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    approve(release, *held)
    assert approvals.effective_scopes(release) == frozenset(effective)
    for scope in ApprovalScope.values:
        assert approvals.is_effective(release, scope) is (scope in effective)


def test_denials_and_revoked_approvals_are_never_effective(switch_on, staff_user):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    UsageApprovalFactory(release=release, decision=DENIED, licence=latest(release))
    approve(release, PUBLIC_VIEW)
    assert approvals.effective_scopes(release) == frozenset()
    other = approvable_release(status=ReleaseStatus.PUBLISHED)
    approvals.revoke(decide(other, INTERNAL), reason="Ended.", revoked_by=staff_user)
    assert approvals.effective_scopes(other) == frozenset()


@pytest.mark.parametrize(
    "lapse,restore",
    [
        ({"status": DueDiligenceStatus.OPEN}, {"status": COMPLETE}),
        ({"status": DueDiligenceStatus.BLOCKED}, {"status": COMPLETE}),
        ({"terms_time_limited": True}, {"terms_time_limited": False}),
    ],
    ids=["reopened", "blocked", "time-limited"],
)
def test_due_diligence_that_lapses_makes_every_approval_ineffective_at_once(
    switch_on, staff_user, lapse, restore
):
    first = approvable_release(status=ReleaseStatus.PUBLISHED)
    second = approvable_release(dataset=first.dataset, status=ReleaseStatus.STAGED)
    approve(first, *EVERY_SCOPE)
    approve(second, INTERNAL)
    ids = [first.pk, second.pk]
    save_as(diligence(first), staff_user, **lapse)
    assert approvals.effective_scopes_by_release(ids) == {
        first.pk: frozenset(),
        second.pk: frozenset(),
    }
    assert visible_release_ids(None, Purpose.VIEW).ids == frozenset()
    save_as(diligence(first), staff_user, **restore)
    assert approvals.effective_scopes_by_release(ids) == {
        first.pk: EVERY_SCOPE,
        second.pk: frozenset({INTERNAL}),
    }


def test_a_release_whose_dataset_has_no_due_diligence_has_no_effective_scope(
    switch_on,
):
    release = DatasetReleaseFactory(status=ReleaseStatus.PUBLISHED)
    ReleaseLicenceFactory(release=release, open_terms=True)
    approve(release, *EVERY_SCOPE)
    assert approvals.effective_scopes(release) == frozenset()


@pytest.mark.parametrize(
    "terms",
    [
        {"redistribution": Permission.NOT_PERMITTED},
        {"redistribution": Permission.UNKNOWN},
        {"modification": Permission.NOT_PERMITTED},
        {"modification": Permission.UNKNOWN},
    ],
)
def test_a_newer_snapshot_without_permission_stops_bulk_and_leaves_public_view(
    switch_on, terms
):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    approve(release, *EVERY_SCOPE)
    ReleaseLicenceFactory(release=release, open_terms=True, **terms)
    assert approvals.effective_scopes(release) == {INTERNAL, PUBLIC_VIEW, EXTERNAL}


def test_permission_with_conditions_is_permission(switch_on):
    release = approvable_release(
        status=ReleaseStatus.PUBLISHED,
        terms={
            "redistribution": Permission.PERMITTED_WITH_CONDITIONS,
            "modification": Permission.PERMITTED_WITH_CONDITIONS,
        },
    )
    approve(release, *EVERY_SCOPE)
    assert approvals.effective_scopes(release) == EVERY_SCOPE


def test_under_embargo_only_internal_is_effective(switch_on):
    embargo = timezone.now() + DAY
    release = approvable_release(status=ReleaseStatus.PUBLISHED, embargo_until=embargo)
    approve(release, *EVERY_SCOPE)
    assert approvals.effective_scopes(release) == {INTERNAL}
    # The embargo lapses at its date; `at` moves only that comparison.
    earlier = embargo - datetime.timedelta(seconds=1)
    assert approvals.effective_scopes(release, at=earlier) == {INTERNAL}
    assert approvals.effective_scopes(release, at=embargo) == EVERY_SCOPE
    lapsed = approvable_release(
        status=ReleaseStatus.PUBLISHED, embargo_until=timezone.now() - DAY
    )
    approve(lapsed, *EVERY_SCOPE)
    assert approvals.effective_scopes(lapsed) == EVERY_SCOPE


def test_at_never_brings_back_what_is_revoked_or_lapsed_now(switch_on, staff_user):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    internal = decide(release, INTERNAL)
    approvals.revoke(internal, reason="Ended.", revoked_by=staff_user)
    other = approvable_release(status=ReleaseStatus.PUBLISHED)
    approve(other, INTERNAL)
    save_as(diligence(other), staff_user, status=DueDiligenceStatus.BLOCKED)
    yesterday = timezone.now() - DAY
    assert approvals.effective_scopes_by_release(
        [release.pk, other.pk], at=yesterday
    ) == {release.pk: frozenset(), other.pk: frozenset()}


def test_a_withdrawn_release_has_no_effective_scope(switch_on, staff_user):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    approve(release, *EVERY_SCOPE)
    release.status = ReleaseStatus.WITHDRAWN
    release.withdrawn_at = timezone.now()
    release.withdrawn_reason = "Test only."
    release.withdrawn_by = staff_user
    release.save()
    assert approvals.effective_scopes(release) == frozenset()
    assert UsageApproval.objects.filter(revoked_at__isnull=True).count() == 4


def test_effectiveness_of_many_releases_takes_one_query(
    switch_on, django_assert_num_queries
):
    releases = [approvable_release(status=ReleaseStatus.PUBLISHED) for _ in range(12)]
    for release in releases:
        approve(release, INTERNAL, PUBLIC_VIEW)
    with django_assert_num_queries(1):
        found = approvals.effective_scopes_by_release(
            [release.pk for release in releases] + [10**12, 2**80]
        )
    assert found == {
        release.pk: frozenset({INTERNAL, PUBLIC_VIEW}) for release in releases
    }


def test_effectiveness_is_nothing_while_the_switch_is_off(db, settings):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    approve(release, *EVERY_SCOPE)
    settings.TYPED_GRAPH_ENABLED = False
    assert approvals.effective_scopes_by_release([release.pk]) == {}
    assert approvals.effective_scopes(release) == frozenset()
    assert not approvals.is_effective(release, INTERNAL)
    settings.TYPED_GRAPH_ENABLED = True
    assert approvals.effective_scopes(release.pk) == EVERY_SCOPE


def test_effectiveness_takes_integer_ids_and_known_scopes(switch_on):
    release = approvable_release()
    assert approvals.effective_scopes_by_release([]) == {}
    for ids in (["1"], [1.5], [True], [None], "12", None, 5):
        with pytest.raises(TypeError):
            approvals.effective_scopes_by_release(ids)
    with pytest.raises(ValueError, match="A scope is one of"):
        approvals.is_effective(release, "public")
    with pytest.raises(ValueError, match="aware datetime"):
        approvals.effective_scopes(release, at=datetime.datetime(2026, 1, 1))
    assert approvals.PREREQUISITES == {
        PUBLIC_VIEW: INTERNAL,
        EXTERNAL: INTERNAL,
        PUBLIC_BULK: PUBLIC_VIEW,
    }


# ── Services: two transactions at once ──────────────────────────────────────


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_two_approvals_of_one_scope_at_once_give_one_decision(settings):
    """The service locks the release before it looks for an active decision:
    the second approval waits, then finds the first and is refused."""
    settings.TYPED_GRAPH_ENABLED = True
    release = approvable_release()
    actors = StaffUserFactory(), StaffUserFactory()
    holding, go = threading.Event(), threading.Event()
    outcome, errors = {}, []

    def first():
        try:
            with transaction.atomic():
                outcome["first"] = approvals.record_decision(
                    release.pk,
                    INTERNAL,
                    APPROVED,
                    rationale="First.",
                    decided_by=actors[0],
                )
                holding.set()
                go.wait(30)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def second():
        try:
            holding.wait(30)
            approvals.record_decision(
                release.pk,
                INTERNAL,
                APPROVED,
                rationale="Second.",
                decided_by=actors[1],
            )
            outcome["second"] = True
        except ApprovalError as exc:
            outcome["refused"] = str(exc)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first approval never started"
        assert _a_backend_waits_on_a_lock(), "the second approval never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)

    assert errors == []
    assert "second" not in outcome
    assert f"decision {outcome['first'].pk}" in outcome["refused"]
    assert list(UsageApproval.objects.all()) == [outcome["first"]]


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_release_stays_visible_while_its_approval_is_replaced(settings, monkeypatch):
    """Halted after the old decision's revocation is written and before the
    new decision is, the replacement has committed nothing: every other
    transaction still sees the old decision, active."""
    settings.TYPED_GRAPH_ENABLED = True
    staff = StaffUserFactory()
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    decide(release, INTERNAL, by=staff)
    view = decide(release, PUBLIC_VIEW, by=staff)
    revoked, go = threading.Event(), threading.Event()
    outcome, errors = {}, []
    insert = approvals._insert_decision

    def halted_insert(**values):
        revoked.set()
        go.wait(30)
        return insert(**values)

    monkeypatch.setattr(approvals, "_insert_decision", halted_insert)

    def replace():
        try:
            outcome["new"] = approvals.replace_decision(
                view.pk, rationale="Refreshed.", decided_by=staff, evidence=EVIDENCE
            )
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=replace)
    thread.start()
    try:
        assert revoked.wait(30), "the replacement never revoked the old decision"
        assert visible_release_ids(None, Purpose.VIEW).ids == {release.pk}
        assert UsageApproval.objects.get(pk=view.pk).active
    finally:
        go.set()
        thread.join(30)

    assert errors == []
    assert visible_release_ids(None, Purpose.VIEW).ids == {release.pk}
    assert not UsageApproval.objects.get(pk=view.pk).active
    assert UsageApproval.objects.get(pk=outcome["new"].pk).active


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
