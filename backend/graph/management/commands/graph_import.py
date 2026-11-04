"""Dry-run a registered typed-data release.

    python manage.py graph_import --release ID --dry-run [--create-entities] \\
        [--resolutions FILE] [--report-file PATH] --as USERNAME

The release names its adapter (graph_register_release recorded it). The dry
run reads the stored data file, maps every record and writes only its import
run, with the report, and the release's source records (graph.ingest.dry_run).
--resolutions names a reviewed resolution file, --report-file a JSON Lines
file for every entry of the report's lists. Committing (--commit) arrives
with G5. Nothing is read or written while TYPED_GRAPH_ENABLED is off.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from graph.ingest.dry_run import DryRunError, dry_run, summary_lines
from graph.ingest.mapping import RunStopped


class Command(BaseCommand):
    help = (
        "Dry-run a registered typed-data release: map its records and report "
        "what a commit would do. Refuses while TYPED_GRAPH_ENABLED is off."
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
            "--commit", action="store_true", help="Refused: committing arrives with G5."
        )
        parser.add_argument(
            "--create-entities",
            action="store_true",
            help="Count the entities a commit would create as mapped.",
        )
        parser.add_argument("--resolutions", help="A reviewed resolution file.")
        parser.add_argument(
            "--report-file",
            help="Write every entry of the report's lists here, as JSON Lines.",
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
        if options["commit"]:
            raise CommandError(
                "Committing arrives with G5: graph_import runs --dry-run only. "
                "Nothing was written."
            )
        user_model = get_user_model()
        user = user_model._default_manager.filter(
            **{user_model.USERNAME_FIELD: options["as_user"]}
        ).first()
        if user is None:
            raise CommandError(
                f"--as {options['as_user']!r} names no account. Nothing was written."
            )
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
