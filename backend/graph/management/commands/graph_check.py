"""List what in the typed graph needs an operator's attention.

    python manage.py graph_check

It reads, and writes nothing (graph.checks). Problems: the registry
against the code's JSON, release files whose stored copy is missing or
changed (every stored byte is read), entries under the storage root that
no row names, runs left running by a process that ended and failed commit
runs, and approvals relying on an older licence snapshot. For review:
publication identifiers and term labels commits did not write, merge
candidates, provisional entities that no evidence names, and the runs
under way. Each section shows its total and at most 20 lines. It exits 1
when a problem is found, and 0 otherwise, whatever is listed for review.
It refuses while TYPED_GRAPH_ENABLED is off.
"""

from django.core.management.base import BaseCommand, CommandError

from graph.checks import PROBLEM, REVIEW, run_checks

from ._typed import require_switch

HEADINGS = {
    PROBLEM: "Problems (graph_check exits 1 while one is found):",
    REVIEW: "For review (listed, never a reason to exit 1):",
}


class Command(BaseCommand):
    help = (
        "List the typed graph's problems, which make it exit 1, and the entries "
        "for review; it writes nothing. Refuses while TYPED_GRAPH_ENABLED is off."
    )

    def handle(self, *args, **options):
        require_switch("the graph is not checked")
        sections = run_checks()
        kind = None
        for section in sections:
            if section.kind != kind:
                kind = section.kind
                self.stdout.write(HEADINGS[kind])
            self.stdout.write(f"{section.title} ({section.name}): {section.total}")
            for line in section.lines:
                self.stdout.write(f"  {line}")
            if section.total > len(section.lines):
                self.stdout.write(f"  and {section.total - len(section.lines)} more")
        problems = sum(s.total for s in sections if s.kind == PROBLEM)
        entries = sum(s.total for s in sections if s.kind == REVIEW)
        listed = (
            f"{entries} {'entry is' if entries == 1 else 'entries are'} listed for "
            "review"
        )
        if problems:
            raise CommandError(
                f"graph_check found {problems} "
                f"{'problem' if problems == 1 else 'problems'}; {listed}."
            )
        if entries:
            self.stdout.write(f"No problems; {listed}.")
        else:
            self.stdout.write("No problems, and nothing to review.")
