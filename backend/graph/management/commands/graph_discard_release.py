"""Discard a typed-data release back to a draft without evidence.

    python manage.py graph_discard_release --release ID --reason TEXT \\
        --as USERNAME

A staged release is discarded to re-import it; a draft whose commit cannot
or should not resume (an open commit run, or evidence) is discarded to give
the commit up (graph.releases.discard_release). The release's assertions
and source records are deleted, its statistics reset, and its commit run
abandoned, with who discarded it, when and why in the run's report. The
release row, its files, licence snapshots, approvals and import runs, and
every entity, identifier, relationship, publication and term are kept. A
published or superseded release is never discarded: it is corrected by a
new release, or withdrawn (graph_withdraw_release). Nothing is written
while TYPED_GRAPH_ENABLED is off.
"""

from django.core.management.base import BaseCommand, CommandError

from graph.models import ReleaseStatus
from graph.releases import ReleaseError, discard_release

from ._typed import require_switch, staff_account


class Command(BaseCommand):
    help = (
        "Discard a staged typed-data release, or a draft with an open commit run "
        "or evidence, back to a draft without evidence, keeping its audit trail. "
        "Refuses while TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--release", type=int, required=True, help="The release's id."
        )
        parser.add_argument(
            "--reason", required=True, help="Why the release is discarded."
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account discarding the release.",
        )

    def handle(self, *args, **options):
        require_switch("no release is discarded")
        user = staff_account(options["as_user"])
        try:
            result = discard_release(
                options["release"], reason=options["reason"], discarded_by=user
            )
        except ReleaseError as error:
            raise CommandError(str(error)) from None
        for line in discarded_lines(result):
            self.stdout.write(line)


def discarded_lines(result) -> list[str]:
    """What graph_discard_release prints: the release and the run it gave
    up, what was deleted, what was kept, and the next steps."""
    pk = result.release.pk
    if result.from_status == ReleaseStatus.STAGED:
        first = f"Release {pk} is a draft again (it was staged)"
    else:
        first = f"Release {pk} stays a draft"
    run = result.run
    if run is None:
        first += ", and had no commit run to give up."
    else:
        state = run.report["discard"]["state"]
        first += (
            f", and its commit run {run.pk} ({state}) is abandoned: the run's report "
            "keeps who discarded it, when and why."
        )
    return [
        first,
        f"Deleted {_counted(result.assertions, 'assertion')}, with their "
        "participants, methods, publications, scores and contexts, and "
        f"{_counted(result.records, 'source record')}.",
        "Kept: the release, its files, licence snapshots, approvals and import "
        "runs, and every entity with its identifiers, every relationship, "
        "publication and term.",
        "Next: dry-run it again with graph_import --dry-run, then commit it with "
        "graph_import --commit; to read it with this code's adapter version, "
        "register it again with graph_register_release first.",
    ]


def _counted(n, singular) -> str:
    return f"{n} {singular if n == 1 else singular + 's'}"
