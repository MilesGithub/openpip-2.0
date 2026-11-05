"""A release's statistics (DatasetRelease.stats, G5): what its catalogue
figures read, recounted from rows when a commit stages the release.

release_stats gives, for one release (version 1 of the shape):

- records: its source records by status, every RecordStatus value, zeros
  included;
- assertions: its evidence assertions; assertions_by_outcome: those by
  outcome, every Outcome value, zeros included;
- relationships: the distinct relationships its assertions name;
- entities: the distinct entities its assertions' participants name;
- publications: the distinct publications its assertions cite;
- entities_created: the entities that name it as the release that created
  them;
- relationship_types: per type slug, in slug order, its assertions and the
  distinct relationships they name.

records, assertions, assertions_by_outcome, entities_created and each type's
assertions add up across releases; relationships, entities, publications
and each type's relationships are distinct within the release and are never
summed (architecture §3.6, D4). What one commit created (relationships,
publications, terms) is in its run's report, not here: those rows name no
release. stats is fixed once the release is published (UNPUBLISHED_FIELDS),
so this shape is too; a figure a later milestone needs beyond it is
recounted from rows.

Each figure is one query on the release's own rows, through the indexes on
(release, status) and (release, outcome) and the release foreign keys.
"""

from django.db.models import Count

from .models import (
    AssertionParticipant,
    DatasetRelease,
    Entity,
    EvidenceAssertion,
    EvidencePublication,
    Outcome,
    RecordStatus,
    SourceRecord,
)

STATS_VERSION = 1


def release_stats(release) -> dict:
    """The statistics of a release (a row or its id), recounted from its
    rows. Read-only."""
    pk = release.pk if isinstance(release, DatasetRelease) else release
    if isinstance(pk, bool) or not isinstance(pk, int):
        raise TypeError(f"A release or its id, not {release!r}.")
    records = _grouped(SourceRecord.objects.filter(release_id=pk), "status")
    outcomes = _grouped(EvidenceAssertion.objects.filter(release_id=pk), "outcome")
    assertions = EvidenceAssertion.objects.filter(release_id=pk)
    by_type = (
        assertions.values_list("relationship__relationship_type_id")
        .annotate(
            assertions=Count("pk"), relationships=Count("relationship", distinct=True)
        )
        .order_by("relationship__relationship_type_id")
    )
    return {
        "version": STATS_VERSION,
        "records": {status: records.get(status, 0) for status in RecordStatus.values},
        "assertions": sum(outcomes.values()),
        "assertions_by_outcome": {
            outcome: outcomes.get(outcome, 0) for outcome in Outcome.values
        },
        "relationships": _distinct(assertions, "relationship"),
        "entities": _distinct(
            AssertionParticipant.objects.filter(release_id=pk), "entity"
        ),
        "publications": _distinct(
            EvidencePublication.objects.filter(release_id=pk), "publication"
        ),
        "entities_created": Entity.objects.filter(created_by_release_id=pk).count(),
        "relationship_types": {
            slug: {"assertions": count, "relationships": distinct}
            for slug, count, distinct in by_type
        },
    }


def _grouped(queryset, field) -> dict:
    """{value: rows} of the queryset by one field, in one grouped query."""
    return dict(
        queryset.order_by().values_list(field).annotate(rows=Count("pk")).order_by()
    )


def _distinct(queryset, field) -> int:
    """The distinct values of one foreign key over the queryset's rows."""
    return queryset.aggregate(n=Count(field, distinct=True))["n"]
