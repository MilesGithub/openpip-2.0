"""The operator guide's page on releases (docs/operator-guide/
typed-releases.md) against the code it describes: its headings, each
command's synopses against the options its parser takes, its lists of
preconditions and the check that takes the run lock, what a refused discard
and a stopped dry run leave, the blanks a commit fills in a stored paper,
the shell command that marks failed a dry run
left running where there is no run lock, graph_check's sections, the
release statistics' keys and which of them add up across releases, and the
commit of the format page's example, whose summary and statistics the page
shows.

It reads the page with test_operator_guide's helpers, and commits the
example that test_operator_guide dry-runs. Like those tests, these need the
full checkout: they skip without docs/ locally, and fail without it in
CI."""

import json
import re

import pytest
from django.core.management import call_command, load_command_class
from django.core.management.base import BaseCommand

from graph import checks
from graph.checks import run_checks
from graph.ingest.commit import commit_release, commit_summary_lines
from graph.ingest.dry_run import dry_run
from graph.ingest.runlock import RUN_LOCK_CLASS
from graph.models import ImportState, ReleaseStatus
from graph.stats import release_stats
from graph.tests import test_operator_guide as guide
from graph.tests.factories import ImportRunFactory, StaffUserFactory
from graph.tests.ingest_helpers import registered

PAGE = guide.PAGE.parent / "typed-releases.md"
pytestmark = guide.pytestmark

# The format page's example, registered and approved as test_operator_guide
# registers it, on a portal holding its two genes and two proteins.
example = guide.example

HEADINGS = [
    "## The life of a release",
    "## Approving a release",
    "## Committing a release",
    "### Before you commit",
    "### What a commit does",
    "### The summary and the report",
    "### A commit that stopped",
    "### One commit at a time",
    "## Publishing a release",
    "## Withdrawing a release",
    "## Discarding a release",
    "### Importing it again",
    "## Checking the graph",
    "## Release statistics",
    "## What is refused",
]
# The commands this page gives synopses of; graph_import's commit is here,
# and its dry run on the format page.
COMMANDS = (
    "graph_approve",
    "graph_revoke",
    "graph_publish_release",
    "graph_withdraw_release",
    "graph_discard_release",
    "graph_check",
)
# Which statistics add up across releases (spec §5; architecture §3.6, D4):
# the counts of a release's own rows do, and the distinct counts, of rows
# that several releases share, never do.
ADDED_UP = {
    "version": "no",
    "records": "yes",
    "assertions": "yes",
    "assertions_by_outcome": "yes",
    "relationships": "no",
    "entities": "no",
    "publications": "no",
    "entities_created": "yes",
    "relationship_types.<type>.assertions": "yes",
    "relationship_types.<type>.relationships": "no",
}


def outside_fences(text) -> list:
    """The page's lines that are not in a fenced code block."""
    lines, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            lines.append(line)
    return lines


def synopses(page, name) -> list:
    """The synopses of a command on a page: each part of a ```text block
    that starts with "python manage.py <name>", with its continuation
    lines."""
    text = page.read_text(encoding="utf-8")
    found = []
    for block in re.findall(r"```text\n(.*?)\n```", text, re.DOTALL):
        for part in re.split(r"\n(?=python manage\.py )", block):
            if re.match(rf"python manage\.py {name}(\s|$)", part):
                found.append(part)
    return found


def stated(shown) -> set:
    """The options these synopses name."""
    return {
        option
        for synopsis in shown
        for option in re.findall(r"(?<![\w-])--?[a-z][a-z-]*", synopsis)
    }


def options_of(name) -> set:
    """Every option the command's parser takes but BaseCommand's own
    (--version, -v/--verbosity, --settings, --pythonpath, --traceback,
    --no-color, --force-color, --skip-checks and -h/--help), which no
    synopsis lists."""

    def options(parser):
        return {
            option for action in parser._actions for option in action.option_strings
        }

    own = options(BaseCommand().create_parser("manage.py", "command"))
    command = load_command_class("graph", name)
    return options(command.create_parser("manage.py", name)) - own


def without_ids(lines) -> list:
    """The lines with their run and release ids as N, as the page's are
    those of one portal."""
    return [re.sub(r"\b(run|release|Release) \d+", r"\1 N", line) for line in lines]


def numbered(text) -> list:
    """The numbers of a section's top-level numbered list."""
    return [int(n) for n in re.findall(r"^(\d+)\. ", text, re.MULTILINE)]


# ── The page ────────────────────────────────────────────────────────────────


def test_the_page_has_its_headings():
    text = PAGE.read_text(encoding="utf-8")
    assert [
        line for line in outside_fences(text) if re.match(r"#{2,3} ", line)
    ] == HEADINGS


@pytest.mark.parametrize("name", COMMANDS)
def test_each_commands_synopses_list_exactly_its_options(name):
    shown = synopses(PAGE, name)
    assert shown, f"no synopsis of {name}"
    assert stated(shown) == options_of(name)


def test_graph_imports_synopses_on_both_pages_list_exactly_its_options():
    # The dry run's on the format page, the commit's here.
    dry, commit = synopses(guide.PAGE, "graph_import"), synopses(PAGE, "graph_import")
    assert (len(dry), len(commit)) == (1, 1)
    assert "--dry-run" in stated(dry) and "--commit" in stated(commit)
    assert stated(dry + commit) == options_of("graph_import")


def test_the_preconditions_are_listed_in_order():
    # A commit's 1 to 15 (CommitError), a dry run's 1 to 9 (DryRunError).
    assert numbered(guide.section("### Before you commit", page=PAGE)) == list(
        range(1, 16)
    )
    assert numbered(guide.section("### Before it starts")) == list(range(1, 10))


def test_the_run_lock_query_names_the_lock():
    found = guide.section("### One commit at a time", page=PAGE)
    assert f"l.classid = {RUN_LOCK_CLASS} AND" in found
    assert "l.objsubid = 2" in found
    assert "datname = current_database()" in found


def test_the_page_says_which_check_takes_the_run_lock():
    # A commit's ninth of fifteen, a dry run's last: never its first.
    lock = " ".join(guide.section("### One commit at a time", page=PAGE).split())
    assert "among its first checks" not in lock
    assert (
        "takes the release's **run lock** once its earlier checks pass (check 9 of a "
        "commit, the last of a dry run's)" in lock
    )
    commit = re.findall(
        r"^(\d+)\. (.*?)(?=^\d+\. |\Z)",
        guide.section("### Before you commit", page=PAGE),
        re.MULTILINE | re.DOTALL,
    )
    dry = re.findall(
        r"^(\d+)\. (.*?)(?=^\d+\. |\Z)",
        guide.section("### Before it starts"),
        re.MULTILINE | re.DOTALL,
    )
    assert "a dry run, commit or discard of the release is running" in commit[8][1]
    assert "a dry run, commit or discard of the release is running" in dry[-1][1]


def test_the_page_says_what_a_refused_discard_and_a_stopped_dry_run_leave():
    discarding = " ".join(guide.section("## Discarding a release", page=PAGE).split())
    assert "says which runs it marked" in discarding
    assert "refused, with nothing written, for a published" not in discarding
    lock = " ".join(guide.section("### One commit at a time", page=PAGE).split())
    assert "a dry run is never resumed" in lock
    assert "and resumes it." not in lock


def test_the_page_says_a_commit_fills_a_stored_papers_blank_title_or_year():
    # G2 fills a stored paper's blank details (test_commit pins it): a stored
    # ref: paper with no year is given the manifest's.
    does = " ".join(guide.section("### What a commit does", page=PAGE).split())
    assert "keeps its own, and the report lists a difference" not in does
    assert "and is given only one it lacks, such as a missing year" in does
    assert "it fills only a `ref:` paper's blank title or year from the" in does


def test_the_pages_shell_command_marks_a_dry_run_left_running_failed(
    registry, tmp_path, monkeypatch
):
    # Without the run lock (patched where the test database has it), nothing
    # marks a dry run left running, and graph_check lists it for good: the
    # page's shell command marks it failed, through the run's guard.
    starts = " ".join(guide.section("### Before it starts").split())
    assert "Without the run lock nothing tells that its process ended" in starts
    found = guide.section("### One commit at a time", page=PAGE)
    [shell] = re.findall(r'^python manage\.py shell -c "(.*)"$', found, re.MULTILINE)
    release = registered(tmp_path)
    left = ImportRunFactory(release=release, file=release.files.first())
    monkeypatch.setattr(checks, "runs_are_locked", lambda using=None: False)

    def runs():
        [section] = [s for s in run_checks() if s.name == "runs"]
        return section.total

    assert runs() == 1
    call_command("shell", command=shell.replace("pk=N,", f"pk={left.pk},"))
    left.refresh_from_db()
    assert (left.state, left.error) == (
        ImportState.FAILED,
        "Its process ended before the run finished.",
    )
    assert left.finished_at is not None
    assert runs() == 0


def test_graph_checks_sections_are_the_pages(registry):
    table = guide.rows(guide.section("## Checking the graph", page=PAGE))
    assert table[0] == ["Section", "Kind", "What it lists"]
    assert [(guide.code(name), kind) for name, kind, _ in table[1:]] == [
        (section.name, section.kind) for section in run_checks()
    ]


# ── The example, committed ──────────────────────────────────────────────────


@pytest.fixture
def committed_example(example):
    """The format page's example after its dry run and its commit, with the
    same options, as an operator runs them: the release, the dry run and the
    commit's CommitResult."""
    release, _ = example
    staff = StaffUserFactory()
    run = dry_run(release, triggered_by=staff)
    assert run.report["verdict"] == "ok"
    result = commit_release(release, triggered_by=staff)
    release.refresh_from_db()
    return release, run, result


def test_the_example_commits_as_the_page_says(committed_example):
    release, run, result = committed_example
    assert release.status == ReleaseStatus.STAGED
    assert result.run.report["dry_run"] == run.pk
    summary, again = re.findall(
        r"```text\n(.*?)\n```",
        guide.section("### The summary and the report", page=PAGE),
        re.DOTALL,
    )
    assert without_ids(commit_summary_lines(result)) == without_ids(
        summary.splitlines()
    )
    # Committed again with the same options: one line, nothing written.
    repeated = commit_release(release, triggered_by=StaffUserFactory())
    assert repeated.run == result.run
    assert without_ids(commit_summary_lines(repeated)) == without_ids(
        again.splitlines()
    )


def test_the_statistics_are_the_pages(committed_example):
    release, _, _ = committed_example
    stats = release.stats
    assert stats == release_stats(release)
    # Its example is the example's statistics.
    assert json.loads(guide.json_block("assertions_by_outcome", page=PAGE)) == stats
    table = guide.rows(guide.section("## Release statistics", page=PAGE))
    assert table[0] == ["Key", "What it counts", "Added up across releases"]
    stated_keys = {guide.code(key): added.split(":")[0] for key, _, added in table[1:]}
    assert len(stated_keys) == len(table) - 1
    per_type = {
        name for counts in stats["relationship_types"].values() for name in counts
    }
    assert per_type == {"assertions", "relationships"}
    keys = (set(stats) - {"relationship_types"}) | {
        f"relationship_types.<type>.{name}" for name in per_type
    }
    assert set(stated_keys) == keys
    assert stated_keys == ADDED_UP
