"""Withdraw a typed-data release, for good.

    python manage.py graph_withdraw_release --release ID --reason TEXT \\
        --as USERNAME

A staged, published or superseded release is withdrawn, with the reason
(graph.releases.withdraw_release). It is hidden from every view and export
at once, its rows are kept, and a withdrawal is final: it restores nothing,
so the release it superseded when it was published stays superseded. A
draft is never withdrawn: nothing shows it. Nothing is written while
TYPED_GRAPH_ENABLED is off.
"""

from django.core.management.base import BaseCommand, CommandError

from graph.models import DatasetRelease, ReleaseStatus
from graph.releases import ReleaseError, withdraw_release

from ._typed import require_switch, staff_account


class Command(BaseCommand):
    help = (
        "Withdraw a staged, published or superseded typed-data release, with the "
        "reason: it is hidden at once, and for good. Refuses while "
        "TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--release", type=int, required=True, help="The release's id."
        )
        parser.add_argument(
            "--reason", required=True, help="Why the release is withdrawn."
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account withdrawing the release.",
        )

    def handle(self, *args, **options):
        require_switch("no release is withdrawn")
        user = staff_account(options["as_user"])
        try:
            release = withdraw_release(
                options["release"], reason=options["reason"], withdrawn_by=user
            )
        except ReleaseError as error:
            raise CommandError(str(error)) from None
        for line in withdrawn_lines(release):
            self.stdout.write(line)


def withdrawn_lines(release) -> list[str]:
    """What graph_withdraw_release prints: the release is hidden everywhere
    at once, its rows are kept, and the release it superseded when it was
    published stays so."""
    lines = [
        f"Release {release.pk} is withdrawn: it is hidden from every view and export "
        "at once, and its rows are kept. A withdrawal is final."
    ]
    # Only a published release ever superseded the release it names: one
    # correction per release does (graph_release_supersedes_once), and
    # publishing refuses a target superseded already. A staged correction
    # never did, even when another correction of its release did; the
    # withdrawal leaves published_at as it was.
    if release.published_at is not None and release.supersedes_id is not None:
        target = (
            DatasetRelease.objects.filter(pk=release.supersedes_id)
            .values_list("status", flat=True)
            .first()
        )
        if target == ReleaseStatus.SUPERSEDED:
            lines.append(
                f"Release {release.supersedes_id}, which it superseded, stays "
                "superseded: a withdrawal restores nothing."
            )
    return lines
