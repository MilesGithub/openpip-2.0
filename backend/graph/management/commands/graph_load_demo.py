"""Load the synthetic demonstration: two synthetic releases, for staff.

    python manage.py graph_load_demo --as USERNAME

It registers the demonstration's releases (graph/demo_data/
synthetic_regulation) as synthetic, v2 superseding v1; dry-runs and commits
each with --create-entities; records an MIT licence snapshot and an internal
approval for each; and publishes v1, then v2 (graph.demo.load_demo). One
account, the one --as names, does every step. Only internal approvals are
recorded, so visitors see nothing until an operator records a public_view
approval with graph_approve. Run again, it completes a load that stopped,
and writes nothing once the demonstration is loaded; it never overrides an
operator's decision. It prints what each step did, and, when it stops, what
was done before. Nothing is written while TYPED_GRAPH_ENABLED is off.
"""

from django.core.management.base import BaseCommand, CommandError

from graph.demo import DemoError, load_demo

from ._typed import require_switch, staff_account


class Command(BaseCommand):
    help = (
        "Load the synthetic demonstration, two synthetic releases that only staff "
        "see, or complete a load that stopped: it is safe to run again. Refuses "
        "while TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help=(
                "The active staff account that registers, commits, approves and "
                "publishes the demonstration."
            ),
        )

    def handle(self, *args, **options):
        require_switch("the synthetic demonstration is not loaded")
        user = staff_account(options["as_user"])
        try:
            result = load_demo(loaded_by=user)
        except DemoError as error:
            self._write(error.lines)
            raise CommandError(str(error)) from None
        except BaseException as error:
            # Raised as it is, after what was done before it.
            self._write(getattr(error, "demo_lines", ()))
            raise
        self._write(result.lines)

    def _write(self, lines):
        for line in lines:
            self.stdout.write(line)
