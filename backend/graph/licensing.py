"""Licence snapshots and due diligence: what approval decisions rely on.

A snapshot records a release's terms as a person read them. Nothing here
infers a term: a field not given stays unknown or blank, and a correction is
a new snapshot, because a snapshot never changes.

Recording a snapshot locks the dataset's due-diligence record but never
changes it. Reopening the record would make every approval of the dataset
ineffective at once, so recording a correction's terms would silently hide
public data.
New terms gate new approvals instead (graph.approvals): an approval of a
non-synthetic release needs due diligence confirmed after the release's
latest snapshot was recorded, which confirm_due_diligence records. Other
changes to a due-diligence record (creating it, reopening or blocking it,
the time-limited flag, the reviewer and the notes link) are saves of the
record with its actor set, and the model writes their history.
"""

import datetime

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from .models import (
    DUE_DILIGENCE_COMPLETE_RULE,
    HTTP_URL,
    DueDiligenceRecord,
    DueDiligenceStatus,
    ReleaseLicence,
    SourceDataset,
)
from .preconditions import (
    locked_release,
    optional_text,
    integer_ids,
    require_staff,
    require_switch,
    storable,
    typed_graph_enabled,
)

# What a person states in a snapshot. The release, the time and the actor
# are the service's to record.
LICENCE_FIELDS = (
    "licence_name",
    "spdx_id",
    "licence_url",
    "terms_quoted",
    "read_at",
    "read_on",
    "attribution",
    "redistribution",
    "commercial_use",
    "modification",
    "share_alike",
    "obligations",
    "ambiguity_notes",
    "reviewer",
)


class LicensingError(Exception):
    """Nothing was written: the snapshot or the confirmation breaks a
    licensing rule, or the caller may not record it."""


def record_licence(release, *, recorded_by, **fields) -> ReleaseLicence:
    """A new snapshot of the release's terms, as given; the latest from now.

    Accepts the fields in LICENCE_FIELDS. Closed values must be among their
    choices and links http(s); the rest is kept as stated. The release is
    locked while the snapshot is written, so a decision being recorded for
    it waits and then relies on this snapshot. So are the dataset and its
    due-diligence record, before the snapshot's time is taken: a
    confirmation (or a record created complete) in progress finishes first,
    and is older than the snapshot; one that starts now waits for the
    snapshot's commit, and is newer. A confirmation counts as covering only
    snapshots it could see.
    """
    require_switch(LicensingError, "no licence snapshot is recorded")
    unknown = sorted(set(fields) - set(LICENCE_FIELDS))
    if unknown:
        raise LicensingError(
            f"A licence snapshot has no field {', '.join(unknown)} that a caller "
            "states; the release, the time and who recorded it are the service's. "
            "Nothing was written."
        )
    require_staff(LicensingError, recorded_by, "recorded_by")
    values = {name: _checked(name, value) for name, value in fields.items()}
    with transaction.atomic():
        release = locked_release(release, LicensingError)
        # Release, dataset, then record: record_decision locks the release
        # before the record, and a record's creation locks the dataset.
        list(
            SourceDataset.objects.select_for_update()
            .filter(pk=release.dataset_id)
            .values_list("pk", flat=True)
        )
        list(
            DueDiligenceRecord.objects.select_for_update()
            .filter(dataset_id=release.dataset_id)
            .values_list("pk", flat=True)
        )
        return ReleaseLicence.objects.create(
            release=release,
            recorded_at=timezone.now(),
            recorded_by=recorded_by,
            **values,
        )


def confirm_due_diligence(record, *, by, note="") -> DueDiligenceRecord:
    """Mark the record complete, confirmed now: the first completion, or a
    re-confirmation that due diligence covers newly recorded terms.

    Acts on the record as stored, locked; writes one history row naming
    `by` and the note. Refuses while the reviewer or the notes link is
    blank, with the message of the rule the database enforces.
    """
    require_switch(LicensingError, "due diligence is not confirmed")
    require_staff(LicensingError, by, "by")
    note = optional_text(LicensingError, note, "A note")
    pk = record.pk if isinstance(record, DueDiligenceRecord) else record
    with transaction.atomic():
        stored = None
        if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
            stored = (
                DueDiligenceRecord.objects.select_for_update().filter(pk=pk).first()
            )
        if stored is None:
            raise LicensingError(
                f"Unknown due-diligence record {record!r}. Nothing was written."
            )
        if not stored.reviewer.strip() or not stored.notes_link.strip():
            raise LicensingError(DUE_DILIGENCE_COMPLETE_RULE)
        stored.status = DueDiligenceStatus.COMPLETE
        stored._changed_by = by
        stored._change_note = note
        stored._reconfirm = True
        stored.save(update_fields=["status", "updated_at"])
    return stored


def latest_licences(release_ids) -> dict:
    """{release id: its latest snapshot, the one with the highest id}, in
    one query. Releases without a snapshot are absent; nothing while the
    switch is off."""
    ids = storable(integer_ids(release_ids))
    if not typed_graph_enabled() or not ids:
        return {}
    newest = (
        ReleaseLicence.objects.filter(release_id__in=ids)
        .values("release_id")
        .annotate(newest=Max("pk"))
        .values("newest")
    )
    return {
        licence.release_id: licence
        for licence in ReleaseLicence.objects.filter(pk__in=newest)
    }


def _checked(name, value):
    """A snapshot field's value, checked as the database would not."""
    field = ReleaseLicence._meta.get_field(name)
    if isinstance(field, models.DateField):
        if value is None or (
            isinstance(value, datetime.date)
            and not isinstance(value, datetime.datetime)
        ):
            return value
        raise LicensingError(f"{name} is a date, not {value!r}. Nothing was written.")
    if not isinstance(value, str):
        raise LicensingError(f"{name} is text, not {value!r}. Nothing was written.")
    if field.choices:
        allowed = [choice for choice, _ in field.choices]
        if value not in allowed:
            raise LicensingError(
                f"{name} is one of {', '.join(allowed)}, not {value!r}. "
                "Nothing was written."
            )
        return value
    # SQLite does not enforce the column sizes, so they are checked here.
    if field.max_length is not None and len(value) > field.max_length:
        raise LicensingError(
            f"{name} is at most {field.max_length} characters. Nothing was written."
        )
    if isinstance(field, models.URLField) and value:
        try:
            HTTP_URL(value)
        except ValidationError:
            raise LicensingError(
                f"{name} is an http or https URL, not {value!r}. Nothing was written."
            ) from None
    return value
