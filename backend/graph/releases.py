"""Publishing and withdrawing releases: the lifecycle transitions whose rules
span releases. G5's commands wrap these; staging, commit and discard are
G5's.

Publishing is not public: it needs an effective `internal` approval, makes
the release current in the default views of those who may see it, and
supersedes the release it corrects, in the same transaction. Whether
visitors see it is decided by `public_view` alone (graph.visibility). A
correction never hides public data, so publishing one is refused while the
release it corrects has an effective `public_view` and the correction does
not. Withdrawal hides a release at once, from every purpose; it keeps its
rows, and never brings back the release it superseded.
"""

from django.db import transaction
from django.utils import timezone

from .approvals import INTERNAL, PUBLIC_VIEW, effective_scopes_by_release
from .models import DatasetRelease, ReleaseStatus
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
