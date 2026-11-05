"""Revoke an approval decision of a typed-data release.

    python manage.py graph_revoke --approval ID --reason TEXT --as USERNAME

The decision keeps its row, with who revoked it, when and why
(graph.approvals.revoke). Its scope stops being effective at once, and so
do the scopes that need it (public view and external services need
internal, bulk download needs public view), until a new decision is
recorded with graph_approve. Nothing is written while TYPED_GRAPH_ENABLED
is off.
"""

from django.core.management.base import BaseCommand, CommandError

from graph.approvals import ApprovalError, revoke

from ._typed import require_switch, staff_account
from .graph_approve import decision_lines


class Command(BaseCommand):
    help = (
        "Revoke an approval decision of a typed-data release, with the reason. "
        "Refuses while TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--approval", type=int, required=True, help="The decision's id."
        )
        parser.add_argument(
            "--reason", required=True, help="Why the decision is revoked."
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account revoking the decision.",
        )

    def handle(self, *args, **options):
        require_switch("no decision is revoked")
        user = staff_account(options["as_user"])
        try:
            decision = revoke(
                options["approval"], reason=options["reason"], revoked_by=user
            )
        except ApprovalError as error:
            raise CommandError(str(error)) from None
        for line in decision_lines(decision, revoked=True):
            self.stdout.write(line)
