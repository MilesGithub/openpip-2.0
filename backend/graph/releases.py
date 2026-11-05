"""Publishing, withdrawing and discarding releases: the lifecycle
transitions whose rules span releases, or a release's runs. G5's commands
wrap these; a commit stages a release (graph.ingest.commit).

Publishing is not public: it needs an effective `internal` approval, makes
the release current in the default views of those who may see it, and
supersedes the release it corrects, in the same transaction. Whether
visitors see it is decided by `public_view` alone (graph.visibility). A
correction never hides public data, so publishing one is refused while the
release it corrects has an effective `public_view` and the correction does
not. Withdrawal hides a release at once, from every purpose; it keeps its
rows, and never brings back the release it superseded.

A discard returns a staged release, or a draft whose commit cannot or
should not resume, to a draft without evidence: it deletes the release's
assertions and source records, gives up its commit run (abandoned, with who
discarded it, when and why in the run's report), and keeps everything else.
It holds the release's run lock (graph.ingest.runlock), so it never runs
beside a dry run or commit of the release.
"""

from contextlib import ExitStack
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .approvals import INTERNAL, PUBLIC_VIEW, effective_scopes_by_release
from .ingest import limits
from .ingest.runlock import (
    KEY,
    MAX_RUN_LOCK_KEY,
    NESTED,
    RunLocked,
    end_dead_runs,
    release_run_lock,
)
from .models import (
    DatasetRelease,
    EvidenceAssertion,
    ImportMode,
    ImportRun,
    ImportState,
    ReleaseStatus,
    SourceRecord,
)
from .preconditions import (
    locked_release,
    require_staff,
    require_switch,
    required_text,
    storable,
)

# What a release may be withdrawn from: a draft is discarded or deleted
# instead, and a withdrawal is final.
WITHDRAWABLE = (ReleaseStatus.STAGED, ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED)
# A commit run is open while it is running or failed (resumable).
OPEN_RUN_STATES = (ImportState.RUNNING, ImportState.FAILED)


class ReleaseError(Exception):
    """Nothing was written: the transition breaks a lifecycle rule, or the
    caller may not make it."""


def publish_release(release, *, published_by) -> DatasetRelease:
    """Publish a staged release, and mark the release it supersedes, if
    that one is published, as superseded. Returns the release as stored.

    The release and the release it supersedes are locked, in id order,
    before anything that decides the outcome is read.
    """
    require_switch(ReleaseError, "no release is published")
    require_staff(ReleaseError, published_by, "published_by")
    with transaction.atomic():
        release, target = _locked_with_target(release)
        if release.status != ReleaseStatus.STAGED:
            raise ReleaseError(
                f"Release {release.pk} is {release.status}: only a staged release "
                "is published. Nothing was written."
            )
        now = timezone.now()
        ids = [release.pk] + ([target.pk] if target is not None else [])
        scopes = effective_scopes_by_release(ids, at=now)
        if INTERNAL not in scopes.get(release.pk, ()):
            raise ReleaseError(
                f"Release {release.pk} has no effective internal approval, which "
                "publishing needs. Nothing was written."
            )
        if target is not None:
            _check_target(release, target, scopes)
            if target.status == ReleaseStatus.PUBLISHED:
                target.status = ReleaseStatus.SUPERSEDED
                # The rules that span releases are checked above; the model
                # refuses these two transitions without this.
                target._lifecycle = True
                target.save(update_fields=["status", "updated_at"])
        release.status = ReleaseStatus.PUBLISHED
        release._lifecycle = True
        release.published_at = now
        release.published_by = published_by
        release.save(
            update_fields=["status", "published_at", "published_by", "updated_at"]
        )
    return release


def withdraw_release(release, *, reason, withdrawn_by) -> DatasetRelease:
    """Withdraw a staged, published or superseded release, with the reason.
    Its rows are kept; the release it superseded stays as it is."""
    require_switch(ReleaseError, "no release is withdrawn")
    require_staff(ReleaseError, withdrawn_by, "withdrawn_by")
    reason = required_text(ReleaseError, reason, "A reason")
    with transaction.atomic():
        release = locked_release(release, ReleaseError)
        if release.status not in WITHDRAWABLE:
            raise ReleaseError(
                f"Release {release.pk} is {release.status}: only a staged, published "
                "or superseded release is withdrawn. Nothing was written."
            )
        release.status = ReleaseStatus.WITHDRAWN
        release.withdrawn_at = timezone.now()
        release.withdrawn_reason = reason
        release.withdrawn_by = withdrawn_by
        release.save(
            update_fields=[
                "status",
                "withdrawn_at",
                "withdrawn_reason",
                "withdrawn_by",
                "updated_at",
            ]
        )
    return release


def _locked_with_target(release):
    """The release and the one it supersedes, if any, locked in id order."""
    pk = release.pk if isinstance(release, DatasetRelease) else release
    stated = None
    if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
        stated = DatasetRelease.objects.filter(pk=pk).values("supersedes_id").first()
    if stated is None:
        raise ReleaseError(f"Unknown release {release!r}. Nothing was written.")
    target_id = stated["supersedes_id"]
    if target_id is None:
        return locked_release(pk, ReleaseError), None
    locked = {
        row.pk: row
        for row in DatasetRelease.objects.select_for_update()
        .filter(pk__in=sorted({pk, target_id}))
        .order_by("pk")
    }
    row = locked.get(pk)
    if row is None:
        raise ReleaseError(f"Unknown release {release!r}. Nothing was written.")
    if row.supersedes_id != target_id:
        # Only a draft's supersedes changes, and a draft is not published.
        raise ReleaseError(
            f"Release {pk} changed while it was being published; try again. "
            "Nothing was written."
        )
    return row, locked[target_id]


def _check_target(release, target, scopes):
    """A correction supersedes a release of its own dataset and synthetic
    flag that was published, and never hides what visitors see."""
    if target.dataset_id != release.dataset_id:
        raise ReleaseError(
            f"Release {release.pk} supersedes release {target.pk} of dataset "
            f"{target.dataset_id}, not its own dataset {release.dataset_id}. "
            "Nothing was written."
        )
    if target.is_synthetic != release.is_synthetic:
        raise ReleaseError(
            f"Release {release.pk} supersedes release {target.pk}, whose synthetic "
            "flag differs: a correction keeps it. Nothing was written."
        )
    was_published = target.status == ReleaseStatus.PUBLISHED or (
        target.status == ReleaseStatus.WITHDRAWN and target.published_at is not None
    )
    if not was_published:
        if target.status == ReleaseStatus.WITHDRAWN:
            original = (
                f"name release {target.supersedes_id}, the one it corrected, instead"
                if target.supersedes_id is not None
                else "name a release that was published instead"
            )
            rule = (
                f"was withdrawn before it was published, which freed what it "
                f"corrected: {original}"
            )
        elif target.status == ReleaseStatus.SUPERSEDED:
            rule = "is already superseded"
        else:
            rule = f"is {target.status}, and only a published release is superseded"
        raise ReleaseError(
            f"Release {release.pk} supersedes release {target.pk}, which {rule}. "
            "Nothing was written."
        )
    holder = (
        DatasetRelease.objects.filter(supersedes=target, published_at__isnull=False)
        .exclude(pk=release.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if holder is not None:
        raise ReleaseError(
            f"Release {release.pk} supersedes release {target.pk}, which release "
            f"{holder} already corrected when it was published. Nothing was "
            "written."
        )
    if PUBLIC_VIEW in scopes.get(target.pk, ()) and PUBLIC_VIEW not in scopes.get(
        release.pk, ()
    ):
        raise ReleaseError(
            f"Release {target.pk} is public (an effective public_view approval) and "
            f"its correction, release {release.pk}, is not: publishing it would hide "
            f"public data. Approve public_view for release {release.pk} first. "
            "Nothing was written."
        )


# ── Discarding ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DiscardResult:
    """What discard_release did."""

    # The release as stored after the discard: a draft.
    release: DatasetRelease
    # The commit run it gave up (abandoned), or None when there was none.
    run: ImportRun | None
    # The assertions and source records it deleted.
    assertions: int
    records: int
    # The release's status before: staged or draft.
    from_status: str


def discard_release(release, *, reason, discarded_by) -> DiscardResult:
    """Discard a staged release, or a draft with an open commit run or
    evidence, back to a draft without evidence. `release` is a row or its
    id.

    It deletes the release's assertions, with their parts, then its source
    records, in batches of limits.BATCH_ROWS by id, through the guarded
    querysets; resets its statistics; and gives up its open commit run, or
    a staged release's completed one, which becomes abandoned with who
    discarded it, when, why and what was deleted in its report. It keeps
    the release row, its files, licence snapshots, approvals and import
    runs, and every entity, identifier, relationship, publication and term.
    The next commit needs a new dry run, which writes the records again.

    It holds the release's run lock once the switch, the account, the
    reason and the release's id are checked, so a dry run, commit or
    discard of the release that is running refuses it, and marks failed the
    release's runs that a process which ended left running (end_dead_runs).
    Raises ReleaseError when a rule refuses it, with nothing written but
    that marking, which the message names. A published or superseded
    release is never discarded, nor a withdrawn one; a draft with no open
    commit run and no evidence has nothing to discard."""
    require_switch(ReleaseError, "no release is discarded")
    require_staff(ReleaseError, discarded_by, "discarded_by")
    reason = required_text(ReleaseError, reason, "A reason")
    pk = _stored_release_id(release)
    with ExitStack() as held:
        try:
            held.enter_context(release_run_lock(pk))
        except RunLocked as error:
            raise ReleaseError(_run_lock_refusal(error)) from None
        ended = end_dead_runs(pk, found_by=f"a discard of release {pk}")
        return _discard(pk, reason, discarded_by, _ending(ended))


def _discard(pk, reason, discarded_by, ending) -> DiscardResult:
    """The discard itself, in one transaction, under the release's lock and
    then its commit runs'. `ending` ends a refusal: what was written."""
    with transaction.atomic():
        release = locked_release(pk, ReleaseError)
        status = release.status
        if status in (ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED):
            raise ReleaseError(
                f"Release {pk} is {status}, and a published release is never "
                "discarded: correct it with a new release that supersedes it, or "
                f"withdraw it. {ending}"
            )
        if status == ReleaseStatus.WITHDRAWN:
            raise ReleaseError(
                f"Release {pk} is withdrawn, and a withdrawal is final: it is never "
                f"discarded. {ending}"
            )
        runs = list(
            ImportRun.objects.select_for_update()
            .filter(release_id=pk, mode=ImportMode.COMMIT)
            .order_by("pk")
            .values_list("pk", "state")
        )
        if (
            status == ReleaseStatus.DRAFT
            and not any(state in OPEN_RUN_STATES for _, state in runs)
            and not EvidenceAssertion.objects.filter(release_id=pk).exists()
        ):
            raise ReleaseError(
                f"Release {pk} is a draft with no open commit run and no evidence: "
                f"there is nothing to discard. {ending}"
            )
        given_up = _given_up(runs)
        if status != ReleaseStatus.DRAFT or release.stats != {}:
            # First, so that the deletes are allowed: a release's rows are
            # deleted only while it is a draft.
            release.status = ReleaseStatus.DRAFT
            release.stats = {}
            release.save(update_fields=["status", "stats", "updated_at"])
        # The records last: an assertion's source record is RESTRICT.
        assertions = _delete_rows(EvidenceAssertion, pk)
        records = _delete_rows(SourceRecord, pk)
        run = None
        if given_up is not None:
            run = ImportRun.objects.get(pk=given_up)
            state = run.state
            run.state = ImportState.ABANDONED
            run.report = {
                **(run.report if isinstance(run.report, dict) else {}),
                "discard": {
                    "at": timezone.now().isoformat(),
                    "by": discarded_by.get_username(),
                    "reason": reason,
                    "status": status,
                    "state": state,
                    "assertions": assertions,
                    "records": records,
                },
            }
            if run.finished_at is None:
                run.finished_at = timezone.now()
            run.save(update_fields=["state", "report", "finished_at"])
    return DiscardResult(release, run, assertions, records, status)


def _stored_release_id(release) -> int:
    """The id of the release, which must be stored: read before the run
    lock is taken, so an unknown id is refused as unknown."""
    pk = release.pk if isinstance(release, DatasetRelease) else release
    if (
        isinstance(pk, bool)
        or not isinstance(pk, int)
        or not storable([pk])
        or not DatasetRelease.objects.filter(pk=pk).exists()
    ):
        raise ReleaseError(f"Unknown release {release!r}. Nothing was written.")
    return pk


def _given_up(runs):
    """The commit run a discard gives up, of the release's (pk, state) in pk
    order: its open run, else its latest completed one (a staged
    release's), else None. Where no run lock tells a live process, a
    running run is given up too: its next batch stops."""
    for states in (OPEN_RUN_STATES, (ImportState.COMPLETED,)):
        found = [run for run, state in runs if state in states]
        if found:
            return found[-1]
    return None


def _delete_rows(model, release_id) -> int:
    """Delete the release's rows of this model through its guarded
    queryset, a batch of limits.BATCH_ROWS at a time by their ids, so no
    row's values are loaded and no list of ids grows with the release. An
    assertion's parts go with it, one DELETE per table and batch. Returns
    how many rows of the model were deleted."""
    deleted, last = 0, 0
    while True:
        ids = list(
            model.objects.filter(release_id=release_id, pk__gt=last)
            .order_by("pk")
            .values_list("pk", flat=True)[: limits.BATCH_ROWS]
        )
        if not ids:
            return deleted
        _, counts = model.objects.filter(pk__in=ids).only("pk").delete()
        deleted += counts.get(model._meta.label, 0)
        last = ids[-1]


def _run_lock_refusal(error) -> str:
    """Why the run lock refused a discard (RunLocked.reason), in words."""
    release = error.release_id
    if error.reason == NESTED:
        why = (
            f"This connection already holds the run lock of release {release}: a "
            "discard never runs inside another run of it."
        )
    elif error.reason == KEY:
        why = (
            f"Release {release}'s id is beyond {MAX_RUN_LOCK_KEY:,}, the largest a "
            "run lock keys."
        )
    else:
        why = (
            f"A dry run, commit or discard of release {release} is running: a "
            "release is not discarded while one runs."
        )
    return f"{why} Nothing was written."


def _ending(ended) -> str:
    """How a refusal ends: nothing was written, or the runs end_dead_runs
    marked failed were."""
    if not ended:
        return "Nothing was written."
    if len(ended) == 1:
        return (
            f"Run {ended[0]}, left running by a process that ended, is marked "
            "failed; nothing else was written."
        )
    named = ", ".join(map(str, ended[:-1])) + f" and {ended[-1]}"
    return (
        f"Runs {named}, left running by processes that ended, are marked failed; "
        "nothing else was written."
    )
