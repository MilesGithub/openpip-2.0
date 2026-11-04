"""Register a typed-data release: a draft release of a dataset, with the
adapter that reads it, and its data file and manifest in private storage.

    python manage.py graph_register_release --dataset SLUG --version V \\
        --adapter NAME --file PATH [--manifest PATH] [--source-url URL] \\
        [--release-date YYYY-MM-DD] [--supersedes VERSION] [--taxon N ...] \\
        [--embargo-until ISO8601] [--synthetic] [--no-raw-rows] --as USERNAME

The dataset is created in the admin first, and so are the release's
licence snapshot and its dataset's due diligence, once it is registered.
Registering the same files and options again writes nothing, and restores a
stored copy that was lost; a draft without evidence takes a new adapter
version (graph.ingest.registration). Nothing is read or written while
TYPED_GRAPH_ENABLED is off.
"""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from graph.ingest.registration import Options, RegistrationError, register_release


def _date(value) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise CommandError(
            f"--release-date is a date, YYYY-MM-DD; not {value!r}. Nothing was "
            "written."
        ) from None


def _moment(value) -> datetime.datetime:
    try:
        parsed = parse_datetime(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.utcoffset() is None:
        raise CommandError(
            "--embargo-until is a date and time with its UTC offset, as "
            f"2027-01-01T00:00:00+00:00; not {value!r}. Nothing was written."
        )
    return parsed


class Command(BaseCommand):
    help = (
        "Register a typed-data release from its data file and optional manifest, "
        "as a draft read by the named adapter. Refuses while TYPED_GRAPH_ENABLED "
        "is off."
    )

    def create_parser(self, prog_name, subcommand, **kwargs):
        # --version is the release's version here, not Django's own option
        # that prints the framework's version: the later definition wins.
        kwargs.setdefault("conflict_handler", "resolve")
        return super().create_parser(prog_name, subcommand, **kwargs)

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True, help="The dataset's slug.")
        parser.add_argument(
            "--version", required=True, help="The release's version label."
        )
        parser.add_argument(
            "--adapter", required=True, help="The adapter that reads the file."
        )
        parser.add_argument("--file", required=True, help="The data file.")
        parser.add_argument(
            "--manifest", help="The release manifest: its papers and terms."
        )
        parser.add_argument(
            "--source-url", default="", help="Where the file came from."
        )
        parser.add_argument("--release-date", help="The source's release date.")
        parser.add_argument(
            "--supersedes", help="The version of the release this one corrects."
        )
        parser.add_argument(
            "--taxon",
            type=int,
            nargs="+",
            action="extend",
            default=[],
            help="A taxon the release declares (NCBI taxid); repeatable.",
        )
        parser.add_argument(
            "--embargo-until", help="An embargo, as an ISO 8601 date and time."
        )
        parser.add_argument(
            "--synthetic",
            action="store_true",
            help="Synthetic demonstration data: checked by every dry run.",
        )
        parser.add_argument(
            "--no-raw-rows",
            action="store_true",
            help="Keep each record's file, line and hash, not its column map.",
        )
        parser.add_argument(
            "--as",
            dest="as_user",
            required=True,
            help="The active staff account registering the release.",
        )

    def handle(self, *args, **options):
        if not settings.TYPED_GRAPH_ENABLED:
            raise CommandError(
                "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
                "release is registered. Nothing was written."
            )
        user = (
            get_user_model()
            ._default_manager.filter(
                **{get_user_model().USERNAME_FIELD: options["as_user"]}
            )
            .first()
        )
        if user is None:
            raise CommandError(
                f"--as {options['as_user']!r} names no account. Nothing was written."
            )
        stated = Options(
            source_url=options["source_url"] or "",
            release_date=(
                _date(options["release_date"]) if options["release_date"] else None
            ),
            supersedes=options["supersedes"],
            taxa=tuple(options["taxon"]),
            embargo_until=(
                _moment(options["embargo_until"]) if options["embargo_until"] else None
            ),
            is_synthetic=options["synthetic"],
            store_raw_rows=not options["no_raw_rows"],
        )
        try:
            registration = register_release(
                dataset=options["dataset"],
                version=options["version"],
                adapter=options["adapter"],
                file=options["file"],
                manifest=options["manifest"],
                options=stated,
                registered_by=user,
            )
        except RegistrationError as error:
            raise CommandError(str(error)) from None
        for line in registration.lines:
            self.stdout.write(line)
