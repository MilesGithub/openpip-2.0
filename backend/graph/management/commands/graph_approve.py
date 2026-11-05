"""Record an approval decision on one scope of a typed-data release, or
replace one.

    python manage.py graph_approve --release ID \\
        --scope {internal,public_view,public_bulk,external_services} \\
        --decision {approved,denied} --rationale TEXT [--evidence TEXT] \\
        --as USERNAME
    python manage.py graph_approve --replace APPROVAL --rationale TEXT \\
        [--evidence TEXT] --as USERNAME

A decision relies on the release's latest licence snapshot. Every rule is
graph.approvals': an approval needs a snapshot that records terms, its
dataset's due diligence confirmed after that snapshot (a synthetic release
has none to do), its prerequisite scope, the permissions a public scope
needs and, for a public scope, evidence; a denial is always recorded. A
scope keeps one active decision, which is revoked (graph_revoke) or
replaced: --replace revokes the decision and records it again, relying on
the latest snapshot, in one transaction, so the release never lacks it.
Nothing is written while TYPED_GRAPH_ENABLED is off.
"""

from django.core.management.base import BaseCommand, CommandError

from graph.approvals import (
    ApprovalError,
    effective_scopes,
    record_decision,
    replace_decision,
)
from graph.models import ApprovalScope, Decision

from ._typed import require_switch, staff_account


class Command(BaseCommand):
    help = (
        "Record an approval decision on one scope of a typed-data release, or "
        "replace an active decision with the same one relying on the release's "
        "latest licence snapshot. Refuses while TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument("--release", type=int, help="The release's id.")
        parser.add_argument(
            "--scope",
            choices=ApprovalScope.values,
            help="The use decided on.",
        )
        parser.add_argument(
            "--decision", choices=Decision.values, help="Approved or denied."
        )
        parser.add_argument(
            "--replace",
            type=int,
            metavar="APPROVAL",
            help=(
                "The id of an active decision to replace with the same decision, "
                "relying on the release's latest licence snapshot."
            ),
        )
        parser.add_argument(
            "--rationale", required=True, help="Why the decision is made."
        )
        parser.add_argument(
            "--evidence",
            default="",
            help="A link or text recording the evidence; a public scope needs it.",
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account making the decision.",
        )

    def handle(self, *args, **options):
        replacing = options["replace"] is not None
        require_switch(
            "no decision is replaced" if replacing else "no decision is recorded"
        )
        given = [
            name
            for name in ("release", "scope", "decision")
            if options[name] is not None
        ]
        if replacing and given:
            raise CommandError(
                "--replace takes no --release, --scope or --decision: the decision "
                "keeps its own. Nothing was written."
            )
        if not replacing and len(given) < 3:
            raise CommandError(
                "--release, --scope and --decision go together, unless --replace "
                "names a decision. Nothing was written."
            )
        user = staff_account(options["as_user"])
        try:
            if replacing:
                decision = replace_decision(
                    options["replace"],
                    rationale=options["rationale"],
                    decided_by=user,
                    evidence=options["evidence"],
                )
            else:
                decision = record_decision(
                    options["release"],
                    options["scope"],
                    options["decision"],
                    rationale=options["rationale"],
                    decided_by=user,
                    evidence=options["evidence"],
                )
        except ApprovalError as error:
            raise CommandError(str(error)) from None
        for line in decision_lines(decision, replaced=options["replace"]):
            self.stdout.write(line)


def decision_lines(decision, *, replaced=None, revoked=False) -> list[str]:
    """What graph_approve and graph_revoke print of a decision: its id,
    scope, decision and release, the licence snapshot it relies on, and
    whether its scope is effective for the release now."""
    release = decision.release_id
    relied_on = (
        f"licence snapshot {decision.licence_id}"
        if decision.licence_id is not None
        else "no licence snapshot"
    )
    what = (
        f"{decision.scope} {decision.decision} for release {release}, relying on "
        f"{relied_on}"
    )
    if revoked:
        first = f"Decision {decision.pk} ({what}) is revoked."
    elif replaced is not None:
        first = (
            f"Decision {decision.pk} replaces decision {replaced}, which is revoked: "
            f"{what}."
        )
    else:
        first = f"Decision {decision.pk}: {what}."
    scopes = effective_scopes(release)
    effective = [scope for scope in ApprovalScope.values if scope in scopes]
    now = "effective" if decision.scope in scopes else "not effective"
    return [
        first,
        f"Scope {decision.scope} is {now} for release {release} now; its effective "
        f"scopes are {', '.join(effective) if effective else 'none'}.",
    ]
