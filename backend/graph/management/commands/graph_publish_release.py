"""Publish a staged typed-data release.

    python manage.py graph_publish_release --release ID --as USERNAME

Publishing needs an effective internal approval, never public_view: it
makes the release current in the default views of staff, and marks the
release it supersedes, if that one is published, as superseded, in the
same transaction (graph.releases.publish_release). A correction that would
hide public data is refused: while the release it corrects has an
effective public_view approval, it needs one too. Visitors see a published
release only while it has an effective public_view approval, and the
command says what would give it one: an approval to record, a denial to
revoke first, or an embargo to wait out. Nothing is written while
TYPED_GRAPH_ENABLED is off.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from graph.approvals import PUBLIC_VIEW, effective_scopes
from graph.models import DatasetRelease, Decision, ReleaseStatus, UsageApproval
from graph.releases import ReleaseError, publish_release

from ._typed import require_switch, staff_account


class Command(BaseCommand):
    help = (
        "Publish a staged typed-data release, which needs an effective internal "
        "approval, and supersede the release it corrects. Refuses while "
        "TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--release", type=int, required=True, help="The release's id."
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account publishing the release.",
        )

    def handle(self, *args, **options):
        require_switch("no release is published")
        user = staff_account(options["as_user"])
        try:
            release = publish_release(options["release"], published_by=user)
        except ReleaseError as error:
            raise CommandError(str(error)) from None
        for line in published_lines(release):
            self.stdout.write(line)


def published_lines(release) -> list[str]:
    """What graph_publish_release prints: what the release superseded, or
    that the release it corrects was withdrawn and stays so, and whether
    visitors see it; when they do not, what would show it to them."""
    pk = release.pk
    lines = [f"Release {pk} is published, and current in the default views of staff."]
    if release.supersedes_id is not None:
        target = (
            DatasetRelease.objects.filter(pk=release.supersedes_id)
            .values_list("status", flat=True)
            .first()
        )
        if target == ReleaseStatus.WITHDRAWN:
            lines.append(
                f"It corrects release {release.supersedes_id}, which was withdrawn and "
                "stays withdrawn."
            )
        else:
            lines.append(
                f"It supersedes release {release.supersedes_id}, which is "
                f"{target} now and leaves the default views."
            )
    if PUBLIC_VIEW in effective_scopes(pk):
        lines.append("Visitors see it, since it has an effective public_view approval.")
    else:
        lines.append(_hidden_from_visitors(release))
    return lines


def _hidden_from_visitors(release) -> str:
    """Why visitors do not see a release just published, and what would show
    it to them. Its internal approval is effective (publishing needs it), so
    public_view is not because the scope has no active decision, its
    decision is a denial (a scope keeps one active decision, so graph_approve
    is refused until it is revoked), or an embargo holds its approval back
    (the embargo is fixed once the release is published)."""
    held = "Visitors see it only with an effective public_view approval"
    active = (
        UsageApproval.objects.filter(
            release_id=release.pk, scope=PUBLIC_VIEW, revoked_at__isnull=True
        )
        .values_list("pk", "decision")
        .first()
    )
    embargo = release.embargo_until
    lapses = ""
    if embargo is not None and embargo > timezone.now():
        lapses = f", effective once its embargo lapses at {embargo.isoformat()}"
    if active is None:
        return f"{held}, which it does not have: graph_approve records one{lapses}."
    number, decision = active
    if decision == Decision.DENIED:
        return (
            f"{held}, and its public_view decision {number} is a denial: revoke it "
            f"with graph_revoke --approval {number}, then record an approval with "
            f"graph_approve{lapses}."
        )
    if lapses:
        return (
            f"{held}, and its public_view approval, decision {number}, is not "
            f"effective before its embargo lapses at {embargo.isoformat()}."
        )
    return f"{held}, and its public_view approval, decision {number}, is not effective."
