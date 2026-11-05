"""Dry-run or commit a registered typed-data release.

    python manage.py graph_import --release ID --dry-run [--create-entities] \\
        [--resolutions FILE] [--report-file PATH] --as USERNAME
    python manage.py graph_import --release ID --commit [--create-entities] \\
        [--resolutions FILE] --as USERNAME

The release names its adapter (graph_register_release recorded it). The dry
run reads the stored data file, maps every record and writes only its import
run, with the report, and the release's source records (graph.ingest.dry_run).
The commit imports what the release's latest dry run reported, with the same
--create-entities and --resolutions, writes its entities, relationships,
publications, terms and evidence batch by batch, and stages the release
(graph.ingest.commit); run again after it stopped, it resumes its run.
--resolutions names a reviewed resolution file, --report-file a JSON Lines
file for every entry of a dry run's lists (a commit keeps its report with its
run). Nothing is read or written while TYPED_GRAPH_ENABLED is off.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from graph.ingest.commit import (
    CommitError,
    CommitStopped,
    commit_release,
    commit_summary_lines,
)
from graph.ingest.dry_run import DryRunError, dry_run, summary_lines
from graph.ingest.mapping import RunStopped
from graph.ingest.runlock import runs_are_locked


class Command(BaseCommand):
    help = (
        "Dry-run a registered typed-data release (map its records and report what "
        "a commit would do), or commit it as its latest dry run reported it and "
        "stage it. Refuses while TYPED_GRAPH_ENABLED is off."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--release", type=int, required=True, help="The release's id."
        )
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Map and report, writing only the run and the source records.",
        )
        mode.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Import the release as its latest dry run reported it, and stage it; "
                "run again, resume a commit that stopped."
            ),
        )
        parser.add_argument(
            "--create-entities",
            action="store_true",
            help=(
                "Create the entities that match no stored entity (a dry run counts "
                "their records as mapped); a commit needs the option its dry run had."
            ),
        )
        parser.add_argument(
            "--resolutions",
            help=(
                "A reviewed resolution file; a commit needs the one its dry run had."
            ),
        )
        parser.add_argument(
            "--report-file",
            help=(
                "With --dry-run: write every entry of the report's lists here, as "
                "JSON Lines."
            ),
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account triggering the run.",
        )

    def handle(self, *args, **options):
        if not settings.TYPED_GRAPH_ENABLED:
            raise CommandError(
                "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
                "release is imported. Nothing was written."
            )
        if options["commit"] and options["report_file"]:
            raise CommandError(
                "--report-file goes with --dry-run: a commit keeps its report with "
                "its run. Nothing was written."
            )
        user_model = get_user_model()
        user = user_model._default_manager.filter(
            **{user_model.USERNAME_FIELD: options["as_user"]}
        ).first()
        if user is None:
            raise CommandError(
                f"--as {options['as_user']!r} names no account. Nothing was written."
            )
        if options["commit"]:
            self._commit(user, options)
        else:
            self._dry_run(user, options)

    def _dry_run(self, user, options):
        try:
            run = dry_run(
                options["release"],
                triggered_by=user,
                create_entities=options["create_entities"],
                resolutions=options["resolutions"],
                report_file=options["report_file"],
            )
        except DryRunError as error:
            raise CommandError(str(error)) from None
        except RunStopped as error:
            raise CommandError(
                f"The dry run stopped, and its run is marked failed: {error}"
            ) from None
        for line in summary_lines(run):
            self.stdout.write(line)
        if options["report_file"]:
            self.stdout.write(f"Wrote every list entry to {options['report_file']}.")

    def _commit(self, user, options):
        try:
            result = commit_release(
                options["release"],
                triggered_by=user,
                create_entities=options["create_entities"],
                resolutions=options["resolutions"],
            )
        except CommitError as error:
            raise CommandError(str(error)) from None
        except CommitStopped as error:
            raise CommandError(_stopped(error)) from None
        for line in commit_summary_lines(result):
            self.stdout.write(line)


def _stopped(error) -> str:
    """What the command says of a commit that stopped (CommitStopped): its
    run, whether it is marked failed, the cause, and what to do: a run that
    could not be marked is marked by the next commit or discard on
    PostgreSQL, and is given up by a discard elsewhere."""
    run = error.run_id
    resume = (
        "Run the same command again to resume it once the cause is fixed, or discard "
        "it with graph_discard_release."
    )
    if error.marked:
        return (
            f"The commit stopped, and its run {run} is marked failed: {error} {resume}"
        )
    if error.abandoned:
        return (
            f"The commit stopped, and its run {run} was given up by a discard "
            f"meanwhile: {error} Dry-run the release again, then commit it."
        )
    if not runs_are_locked():
        # Nothing marks the run there, and a commit never resumes a running
        # run (precondition 10.1): only a discard gives it up.
        return (
            f"The commit stopped, and its run {run} could not be marked failed: "
            f"{error} It stays running, and on this database, which has no run "
            "lock, a commit never resumes a running run: discard it with "
            "graph_discard_release, then dry-run the release again and commit it."
        )
    return (
        f"The commit stopped, and its run {run} could not be marked failed, since the "
        f"database connection was lost: {error} The next commit or discard of the "
        f"release marks it. {resume}"
    )
