"""Approval decisions, and whether each is effective.

A decision is recorded for one release and one scope: internal use, public
view, bulk download, or sending identifiers to external services. It is
never edited: a revocation sets its revoked fields once, and a new decision
is a new row. Unknown is never permission, so no effective approval means
denied.

Whether a scope is effective is evaluated each time it is asked, from the
decisions, the due-diligence record and the latest licence snapshot as they
are now. A scope is effective while the release is not withdrawn; its
dataset's due diligence is complete and its terms are not time-limited
(synthetic releases have none to do); it has an active approval; its
prerequisite is effective (public view and external services need internal,
bulk needs public view); for bulk, the latest snapshot permits
redistribution and modification; and, under embargo, only internal is.
Reopening or blocking due diligence, setting the flag, revoking `internal`
or withdrawing the release therefore take every dependent scope away at
once, while their rows stay as recorded.

Recording an approval asks more than effectiveness does: the latest snapshot
must record terms, due diligence must have been confirmed after that
snapshot was recorded, and public scopes need permissions and evidence.
Existing approvals keep relying on the snapshot they named, so new terms
never hide public data; stale_approvals() lists them for review, and
replace_decision() refreshes one without a moment in which it lapses. A
denial is recorded whenever the general rules pass: saying no is always
safe.
"""

from django.db import connection, transaction
from django.db.models import Exists, F, Max, OuterRef, Subquery
from django.utils import timezone

from .models import (
    PERMITTED_VALUES,
    ApprovalScope,
    DatasetRelease,
    Decision,
    DueDiligenceRecord,
    DueDiligenceStatus,
    ReleaseLicence,
    ReleaseStatus,
    UsageApproval,
)
from .preconditions import (
    integer_ids,
    locked_release,
    moment,
    optional_text,
    require_staff,
    require_switch,
    required_text,
    storable,
    typed_graph_enabled,
)

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
PUBLIC_BULK = ApprovalScope.PUBLIC_BULK
EXTERNAL_SERVICES = ApprovalScope.EXTERNAL_SERVICES

# The scope each scope needs to be effective first.
PREREQUISITES = {
    PUBLIC_VIEW: INTERNAL,
    EXTERNAL_SERVICES: INTERNAL,
    PUBLIC_BULK: PUBLIC_VIEW,
}
# Prerequisites before the scopes that need them.
_ORDER = (INTERNAL, PUBLIC_VIEW, EXTERNAL_SERVICES, PUBLIC_BULK)
# What an approval of a scope needs the latest snapshot to permit. Showing
# data to the public makes it available; an export is adapted material,
# with a note of changes.
NEEDED_PERMISSIONS = {
    PUBLIC_VIEW: ("redistribution",),
    PUBLIC_BULK: ("redistribution", "modification"),
}
# Approved, these need recorded evidence (open question C2).
EVIDENCED_SCOPES = frozenset({PUBLIC_VIEW, PUBLIC_BULK})


class ApprovalError(Exception):
    """Nothing was written: the decision breaks an approval rule, or the
    caller may not record it."""


# ── Recording decisions ─────────────────────────────────────────────────────


def record_decision(
    release, scope, decision, *, rationale, decided_by, evidence=""
) -> UsageApproval:
    """A new decision on one scope of the release, relying on its latest
    licence snapshot. Refused, with nothing written, unless the rules in
    this module's docstring allow it; the refusal says which failed."""
    require_switch(ApprovalError, "no decision is recorded")
    require_staff(ApprovalError, decided_by, "decided_by")
    scope, decision = _closed(scope, decision)
    rationale = required_text(ApprovalError, rationale, "A rationale")
    evidence = optional_text(ApprovalError, evidence, "Evidence")
    with transaction.atomic():
        release = locked_release(release, ApprovalError)
        active = _active(release.pk, scope)
        if active is not None:
            raise ApprovalError(
                f"Release {release.pk} already has an active {scope} decision, "
                f"{_describe(active)}: revoke or replace it first. Nothing was "
                "written."
            )
        licence = _relied_on(release, scope, decision, evidence)
        return _insert_decision(
            release=release,
            scope=scope,
            decision=decision,
            licence=licence,
            rationale=rationale,
            evidence=evidence,
            decided_by=decided_by,
            decided_at=timezone.now(),
        )


def revoke(approval, *, reason, revoked_by) -> UsageApproval:
    """Revoke this decision, and only this one. The scopes that need it stop
    being effective through the effectiveness rules, and become effective
    again only if a new approval is recorded for it."""
    require_switch(ApprovalError, "no decision is revoked")
    require_staff(ApprovalError, revoked_by, "revoked_by")
    reason = required_text(ApprovalError, reason, "A reason")
    with transaction.atomic():
        _, approval = _locked_active(approval)
        _revoke(approval, reason, revoked_by, timezone.now())
    return approval


def replace_decision(approval, *, rationale, decided_by, evidence="") -> UsageApproval:
    """Revoke the decision and record the same decision again, relying on
    the release's latest snapshot, in one transaction; the new decision is
    returned. The new one must pass every rule of record_decision, or
    nothing changes. Refreshes a stale approval without a moment in which
    the release lacks it."""
    require_switch(ApprovalError, "no decision is replaced")
    require_staff(ApprovalError, decided_by, "decided_by")
    rationale = required_text(ApprovalError, rationale, "A rationale")
    evidence = optional_text(ApprovalError, evidence, "Evidence")
    with transaction.atomic():
        release, old = _locked_active(approval)
        licence = _relied_on(release, old.scope, old.decision, evidence)
        now = timezone.now()
        pk = _reserve_decision_id()
        _revoke(old, f"Replaced by decision {pk}", decided_by, now)
        return _insert_decision(
            pk=pk,
            release=release,
            scope=old.scope,
            decision=old.decision,
            licence=licence,
            rationale=rationale,
            evidence=evidence,
            decided_by=decided_by,
            decided_at=now,
        )


def stale_approvals():
    """Active approvals whose licence snapshot is no longer the latest of
    their release, for review (graph_check). Empty while the switch is off."""
    if not typed_graph_enabled():
        return UsageApproval.objects.none()
    newest = (
        ReleaseLicence.objects.filter(release=OuterRef("release"))
        .order_by("-pk")
        .values("pk")[:1]
    )
    return (
        UsageApproval.objects.filter(
            decision=Decision.APPROVED, revoked_at__isnull=True
        )
        .annotate(latest_licence=Subquery(newest))
        .exclude(licence_id=F("latest_licence"))
        .order_by("pk")
    )


def _closed(scope, decision):
    if scope not in ApprovalScope.values:
        raise ApprovalError(
            f"A scope is one of {', '.join(ApprovalScope.values)}, not {scope!r}. "
            "Nothing was written."
        )
    if decision not in Decision.values:
        raise ApprovalError(
            f"A decision is one of {', '.join(Decision.values)}, not {decision!r}. "
            "Nothing was written."
        )
    return ApprovalScope(scope), Decision(decision)


def _active(release_id, scope):
    return UsageApproval.objects.filter(
        release_id=release_id, scope=scope, revoked_at__isnull=True
    ).first()


def _describe(approval) -> str:
    return (
        f"decision {approval.pk} ({approval.decision} by "
        f"{approval.decided_by_username or 'an unnamed account'})"
    )


def _locked_active(approval):
    """The release and the decision, locked in that order; the decision
    must still be active."""
    pk = approval.pk if isinstance(approval, UsageApproval) else approval
    release_id = None
    if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
        release_id = (
            UsageApproval.objects.filter(pk=pk)
            .values_list("release_id", flat=True)
            .first()
        )
    if release_id is None:
        raise ApprovalError(f"Unknown decision {approval!r}. Nothing was written.")
    release = locked_release(release_id, ApprovalError)
    stored = UsageApproval.objects.select_for_update().get(pk=pk)
    if stored.revoked_at is not None:
        raise ApprovalError(
            f"Decision {pk} is already revoked, by {stored.revoked_by_username}: "
            "a revocation is set once. Nothing was written."
        )
    return release, stored


def _relied_on(release, scope, decision, evidence):
    """The latest snapshot, which the new decision relies on, once the rules
    for this decision pass. The caller holds the release's lock."""
    latest = ReleaseLicence.objects.filter(release=release).order_by("-pk").first()
    if decision == Decision.DENIED:
        return latest
    refused = f"Release {release.pk} cannot be approved for {scope}"
    if release.status == ReleaseStatus.WITHDRAWN:
        raise ApprovalError(f"{refused}: it is withdrawn. Nothing was written.")
    if latest is None:
        raise ApprovalError(
            f"{refused}: it has no licence snapshot. Record its terms first "
            "(graph.licensing.record_licence). Nothing was written."
        )
    if not latest.records_terms:
        raise ApprovalError(
            f"{refused}: its latest licence snapshot, {latest.pk}, records no terms "
            "(a licence name or quoted terms, and where and when they were read). "
            "Nothing was written."
        )
    if not release.is_synthetic:
        _check_due_diligence(release, latest, refused)
    prerequisite = PREREQUISITES.get(scope)
    if prerequisite is not None:
        held = _active(release.pk, prerequisite)
        if held is None or held.decision != Decision.APPROVED:
            raise ApprovalError(
                f"{refused}: {scope} needs an active {prerequisite} approval first. "
                "Nothing was written."
            )
    for name in NEEDED_PERMISSIONS.get(scope, ()):
        value = getattr(latest, name)
        if value not in PERMITTED_VALUES:
            raise ApprovalError(
                f"{refused}: its latest licence snapshot, {latest.pk}, records "
                f"{name} as {value}, not permitted. Nothing was written."
            )
    if scope in EVIDENCED_SCOPES and not evidence:
        raise ApprovalError(
            f"{refused}: a public approval records its evidence. Nothing was "
            "written."
        )
    return latest


def _check_due_diligence(release, latest, refused):
    record = (
        DueDiligenceRecord.objects.select_for_update()
        .filter(dataset_id=release.dataset_id)
        .first()
    )
    if record is None:
        raise ApprovalError(
            f"{refused}: dataset {release.dataset_id} has no due-diligence record. "
            "Nothing was written."
        )
    if record.status != DueDiligenceStatus.COMPLETE:
        raise ApprovalError(
            f"{refused}: dataset {release.dataset_id}'s due diligence is "
            f"{record.status}, not complete. Nothing was written."
        )
    if record.terms_time_limited:
        raise ApprovalError(
            f"{refused}: dataset {release.dataset_id}'s terms are time-limited, and "
            "approvals do not expire, so none is recorded. Nothing was written."
        )
    if record.completed_at <= latest.recorded_at:
        raise ApprovalError(
            f"{refused}: due diligence was last confirmed at "
            f"{record.completed_at.isoformat()}, before licence snapshot "
            f"{latest.pk} was recorded at {latest.recorded_at.isoformat()}. "
            "Confirm that it covers those terms first "
            "(graph.licensing.confirm_due_diligence). Nothing was written."
        )


def _revoke(approval, reason, revoked_by, now):
    approval.revoked_at = now
    approval.revoked_by = revoked_by
    approval.revoke_reason = reason
    approval.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])


def _insert_decision(**values) -> UsageApproval:
    approval = UsageApproval(**values)
    approval.save(force_insert=True)
    return approval


def _reserve_decision_id() -> int:
    """The id the replacing decision will have, taken before it is written.

    The revocation names its replacement, and its reason is set with it,
    once; but the replacement can be inserted only after the revocation,
    because one active decision per scope is a partial unique index, which
    cannot be deferred. PostgreSQL hands the id out from the table's
    sequence, so no other insert can take it. SQLite, the development
    fallback, has no sequence to draw from: the next id after the highest is
    used, and an insert that raced for it would fail, never collide.
    """
    meta = UsageApproval._meta
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT nextval(pg_get_serial_sequence(%s, %s))",
                [meta.db_table, meta.pk.column],
            )
            return cursor.fetchone()[0]
    highest = UsageApproval._base_manager.aggregate(highest=Max("pk"))["highest"]
    return (highest or 0) + 1


# ── Effectiveness ───────────────────────────────────────────────────────────


def effective_scopes_by_release(release_ids, *, at=None) -> dict:
    """{release id: frozenset of its effective scopes}, from one query.

    Ids that name no release are absent. `at` replaces now only where the
    embargo is compared; decisions, due diligence, snapshots and statuses
    are read as they are now. Nothing while the switch is off.
    """
    ids = storable(integer_ids(release_ids))
    at = moment(at)
    if not typed_graph_enabled() or not ids:
        return {}
    return {
        row["pk"]: scopes
        for row, scopes in effective_rows(DatasetRelease.objects.filter(pk__in=ids), at)
    }


def effective_scopes(release, *, at=None) -> frozenset:
    """The release's effective scopes; `release` is a row or its id."""
    pk = release.pk if isinstance(release, DatasetRelease) else release
    return effective_scopes_by_release([pk], at=at).get(pk, frozenset())


def is_effective(release, scope, *, at=None) -> bool:
    """Whether this scope is effective for the release. An unknown scope is
    a programming error, and raises ValueError."""
    if scope not in ApprovalScope.values:
        raise ValueError(
            f"A scope is one of {', '.join(ApprovalScope.values)}, not {scope!r}."
        )
    return scope in effective_scopes(release, at=at)


def effective_rows(releases, at):
    """(row, effective scopes) for each release of the queryset, from one
    annotated query, for graph.visibility. A row holds the release's pk,
    status and the values the rules read. Nothing while the switch is off."""
    if not typed_graph_enabled():
        return
    approved = UsageApproval.objects.filter(
        release=OuterRef("pk"), decision=Decision.APPROVED, revoked_at__isnull=True
    )
    latest = ReleaseLicence.objects.filter(release=OuterRef("pk")).order_by("-pk")
    rows = releases.annotate(
        **{_held(scope): Exists(approved.filter(scope=scope)) for scope in _ORDER},
        latest_redistribution=Subquery(latest.values("redistribution")[:1]),
        latest_modification=Subquery(latest.values("modification")[:1]),
        diligence_status=F("dataset__due_diligence__status"),
        diligence_time_limited=F("dataset__due_diligence__terms_time_limited"),
    ).values(
        "pk",
        "status",
        "is_synthetic",
        "embargo_until",
        "latest_redistribution",
        "latest_modification",
        "diligence_status",
        "diligence_time_limited",
        *(_held(scope) for scope in _ORDER),
    )
    for row in rows.order_by("pk"):
        yield row, _effective(row, at)


def _held(scope) -> str:
    return f"approved_{scope}"


def _effective(row, at) -> frozenset:
    if row["status"] == ReleaseStatus.WITHDRAWN:
        return frozenset()
    diligent = row["diligence_status"] == DueDiligenceStatus.COMPLETE and not (
        row["diligence_time_limited"]
    )
    if not (row["is_synthetic"] or diligent):
        return frozenset()
    effective = set()
    for scope in _ORDER:
        prerequisite = PREREQUISITES.get(scope)
        if row[_held(scope)] and (prerequisite is None or prerequisite in effective):
            effective.add(scope)
    if not (
        row["latest_redistribution"] in PERMITTED_VALUES
        and row["latest_modification"] in PERMITTED_VALUES
    ):
        effective.discard(PUBLIC_BULK)
    embargo = row["embargo_until"]
    if embargo is not None and embargo > at:
        effective &= {INTERNAL}
    return frozenset(effective)
