"""The dry run (architecture §8, step 2): a release's data file read with its
adapter, every record mapped (graph.ingest.mapping), and a report of what a
commit would do. It writes only its import run and the release's source
records.

Preconditions, in this order, each a DryRunError naming its number, before
anything is read or written: the switch is on; the actor is an active staff
account; the database's registry version is the code's; the release exists,
is a draft, has one data file and at most one manifest, and no open commit
run; a release that is not synthetic has an effective internal approval;
the release's adapter is registered at the version, mapping version and
kind the release records; the resolution file, if given, is valid; the
report file, if given, lies outside MEDIA_ROOT, STATIC_ROOT and
GRAPH_FILES_ROOT; and the release's run lock (graph.ingest.runlock) is
taken, which the run holds until it returns, so that one dry run, commit or
discard of a release runs at a time. Once it is, the release is read again
and precondition 4 checked again (still a draft, with no open commit run),
since a commit may have started or finished meanwhile. A dry run marks no
other run: one left running by a process that ended stays so until the
release's next commit or discard marks it failed.

Then the run is created (mode dry_run, running), the stored data file and
manifest are opened and checked (graph.storage.open_stored), the manifest is
loaded, and the file is read in batches of at most limits.BATCH_ROWS rows
and limits.BATCH_BYTES bytes: each batch is planned outside any transaction
(plan_batch) and written in one short transaction (write_records). When a
later batch changes how an earlier batch's rows map (a later line of a
record id rejects its first occurrence; a value stated at another taxon
refuses a planned entity), the file is read a second time, with what was
resolved, so the records and the report follow from the whole file
(MappingRun.read_again), unless a file error ended the reading. A stored
file that does not match its record, an invalid manifest, a file error of
the adapter (at the header: no record is written; later: the records
written so far stay) or of the mapping (more distinct identifiers than
limits.MAX_IDENTIFIERS: the batch that passed the limit is not written, and
the records written before it stay), or a synthetic violation make the
verdict refused; graph_import --commit (graph.ingest.commit) refuses a
release whose latest dry run was refused. The run is marked completed with
its report, once the release is checked to read with the run's adapter
version still, or failed with the error (its repr()), whatever stopped it;
a failed run leaves no report file.

The report (ImportRun.report) lists what the acceptance asks for; each list
keeps limits.REPORT_LIST_ENTRIES entries with its total, an entry its first
lines, issues and candidates, and the whole report limits.REPORT_BYTES.
--report-file writes every entry of every list, as JSON Lines, atomically;
every curated mapping and candidate list is also on its source record.
"""

import json
import os
import stat
import tempfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from graph.approvals import INTERNAL, effective_scopes, effective_scopes_by_release
from graph.licensing import latest_licences
from graph.models import (
    DatasetRelease,
    Effect,
    EvidenceAssertion,
    EvidenceMethod,
    FileRole,
    GeneticSign,
    ImportMode,
    ImportRun,
    ImportState,
    MethodRole,
    Orientation,
    Outcome,
    RecordStatus,
    RelationshipFamily,
    ReleaseStatus,
)
from graph.preconditions import locked_release, storable, typed_graph_enabled
from graph.registry import registry_state
from graph.storage import StorageError, open_stored
from graph.terms import PHYSICAL_INTERACTION_LEVELS

from . import limits
from .context import RunContext, load_registry_snapshot
from .jsonfile import read_bounded
from .manifest import EMPTY_MANIFEST, ManifestError, load_manifest
from .mapping import (
    Create,
    Existing,
    ExistingPaper,
    MappingRun,
    RunStopped,
    Unmapped,
    check_adapter_unchanged,
    paper_identity,
    plan_batch,
    write_records,
)
from .protocol import FileError, Issue
from .registry import AdapterSpecError, UnknownAdapter, get_adapter
from .resolutions import NO_RESOLUTIONS, ResolutionError, load_resolutions
from .runlock import KEY, MAX_RUN_LOCK_KEY, NESTED, RunLocked, release_run_lock

OK = "ok"
REFUSED = "refused"
# The statuses of the other releases a conflict is reported against, which
# also need an effective internal approval.
COMPARED_STATUSES = (
    ReleaseStatus.PUBLISHED,
    ReleaseStatus.SUPERSEDED,
    ReleaseStatus.STAGED,
)
OTHER_OUTCOMES = frozenset({Outcome.INCONCLUSIVE, Outcome.NOT_SIGNIFICANT})
# The kind of a pair where either side is one of OTHER_OUTCOMES.
OTHER_OUTCOME = "other_outcome"
# The report's lists, in the order the report file writes them.
LISTS = (
    "file_errors",
    "rejected",
    "unstored",
    "identifiers",
    "curated",
    "curated_unused",
    "curated_redundant",
    "curated_contradicted",
    "curated_not_applied",
    "would_create",
    "label_conflicts",
    "label_differs",
    "relationships",
    "publications",
    "manifest_differences",
    "terms",
    "reference_links",
    "conflicts",
    "other_outcomes",
    "synthetic_violations",
    "would_change",
    "notes",
)
# The order identifiers are listed in: what needs a person first.
_STATUS_ORDER = {"ambiguous": 0, "candidate": 1, "unmatched": 2, "resolved": 4}


class DryRunError(Exception):
    """The dry run did not start: precondition `precondition` (1 to 9, in
    the order they are checked) failed. Nothing was written."""

    def __init__(self, precondition, message):
        super().__init__(message)
        self.precondition = precondition


@dataclass(frozen=True)
class _Release:
    """The release as the run reads it at the start."""

    pk: int
    dataset_id: int
    is_synthetic: bool
    supersedes_id: int | None


# ── The service ─────────────────────────────────────────────────────────────


def dry_run(
    release,
    *,
    triggered_by,
    create_entities=False,
    resolutions=None,
    report_file=None,
) -> ImportRun:
    """Dry-run the release (a row or its id) and return its import run,
    completed with its report. `resolutions` and `report_file` are paths.
    Raises DryRunError, writing nothing, when a precondition fails; any
    error once the run exists marks it failed and is raised again. The
    release's run lock is held from precondition 9 until it returns."""
    checked = _preconditions(
        release, triggered_by, create_entities, resolutions, report_file
    )
    release, data, manifest_file, adapter, loaded = checked
    with ExitStack() as held:
        release, data, manifest_file = _run_locked(release, held)
        run = ImportRun(
            release=release,
            file=data,
            adapter=release.adapter,
            adapter_version=release.adapter_version,
            mapping_version=release.mapping_version,
            mode=ImportMode.DRY_RUN,
            state=ImportState.RUNNING,
            parameters={
                "create_entities": bool(create_entities),
                "resolutions": loaded.as_json(),
                "registry_version": registry_state().database,
            },
            triggered_by=triggered_by,
        )
        run.save()
        report = None
        try:
            report = Report(release, adapter, bool(create_entities), report_file)
            _read_and_write(run, release, data, manifest_file, adapter, loaded, report)
            run.report = report.finish()
            report.write_file(run.report)
            run.state = ImportState.COMPLETED
            run.finished_at = timezone.now()
            with transaction.atomic():
                # Registered again since the last batch was written: the
                # report no longer says what the release's records are.
                check_adapter_unchanged(locked_release(release.pk, RunStopped), run)
                run.save(update_fields=["state", "report", "finished_at"])
        except BaseException as error:
            run.state = ImportState.FAILED
            run.error = repr(error)
            run.report = {}
            if report is not None:
                report.discard()
                try:
                    run.report = report.finish()
                except Exception:  # noqa: BLE001 - the error raised below says why.
                    pass
            run.finished_at = timezone.now()
            run.save(update_fields=["state", "error", "report", "finished_at"])
            raise
    return run


def _preconditions(release, user, create_entities, resolutions, report_file):
    """§5.2's preconditions, in order; the rows and files the run needs."""
    if not typed_graph_enabled():
        raise DryRunError(
            1,
            "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
            "release is dry-run. Nothing was written.",
        )
    if not _active_staff(user):
        raise DryRunError(
            2,
            f"The run is triggered by an active staff account, not {user!r}. "
            "Nothing was written.",
        )
    state = registry_state()
    if state.database != state.code:
        raise DryRunError(
            3,
            f"The database's registry is at version {state.database or 'none'}, and "
            f"this code's at {state.code}: run graph_sync_registry first. Nothing "
            "was written.",
        )
    release = _release_row(release)
    data, manifest = _release_files(release)
    if not release.is_synthetic and INTERNAL not in effective_scopes(release):
        raise DryRunError(
            5,
            f"Release {release.pk} is not synthetic and has no effective internal "
            "approval (graph_approve records one). Nothing was written.",
        )
    adapter = _adapter(release)
    loaded = NO_RESOLUTIONS
    if resolutions is not None:
        loaded = _resolutions(resolutions)
    if report_file is not None:
        problem = report_file_problem(report_file)
        if problem:
            raise DryRunError(8, f"{problem} Nothing was written.")
    return release, data, manifest, adapter, loaded


def _active_staff(user) -> bool:
    """The account as stored decides, not the object given."""
    user_model = get_user_model()
    if not isinstance(user, user_model) or user.pk is None:
        return False
    account = (
        user_model._base_manager.filter(pk=user.pk)
        .values("is_active", "is_staff")
        .first()
    )
    return bool(account and account["is_active"] and account["is_staff"])


def _release_row(release) -> DatasetRelease:
    pk = release.pk if isinstance(release, DatasetRelease) else release
    row = None
    if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
        row = DatasetRelease.objects.filter(pk=pk).first()
    if row is None:
        raise DryRunError(4, f"Unknown release {release!r}. Nothing was written.")
    if row.status != ReleaseStatus.DRAFT:
        raise DryRunError(
            4,
            f"Release {row.pk} is {row.status}: a dry run runs on a draft only. "
            "Nothing was written.",
        )
    return row


def _release_files(release, *, open_commit_check=True):
    """The release's one data file and its manifest, if any; with
    open_commit_check (the dry run's precondition 4), the release has no
    open commit run too. A commit reads them without that check."""
    files = list(
        release.files.filter(role__in=[FileRole.DATA, FileRole.MANIFEST]).order_by("pk")
    )
    data = [file for file in files if file.role == FileRole.DATA]
    manifests = [file for file in files if file.role == FileRole.MANIFEST]
    if len(data) != 1:
        raise DryRunError(
            4,
            f"Release {release.pk} has {len(data)} data files, and a dry run reads "
            "one (graph_register_release stores it). Nothing was written.",
        )
    if len(manifests) > 1:
        raise DryRunError(
            4,
            f"Release {release.pk} has {len(manifests)} manifests, and a release "
            "has at most one. Nothing was written.",
        )
    if open_commit_check:
        _no_open_commit(release)
    return data[0], (manifests[0] if manifests else None)


def _no_open_commit(release):
    """Precondition 4's last check: no commit run of the release is open
    (running, or failed and resumable)."""
    found = (
        ImportRun.objects.filter(
            release=release,
            mode=ImportMode.COMMIT,
            state__in=[ImportState.RUNNING, ImportState.FAILED],
        )
        .order_by("pk")
        .values_list("pk", flat=True)
        .first()
    )
    if found is not None:
        raise DryRunError(
            4,
            f"Release {release.pk} has an open commit run {found}: a dry run never "
            "runs beside one. Resume it with graph_import --commit, or discard it "
            "with graph_discard_release. Nothing was written.",
        )


def _adapter(release):
    try:
        adapter = get_adapter(release.adapter)
    except (UnknownAdapter, AdapterSpecError) as error:
        raise DryRunError(
            6, f"Release {release.pk}'s adapter: {error} Nothing was written."
        ) from None
    spec = adapter.spec
    # The kind too: a dry run's report states the kind the release has.
    if (spec.version, spec.mapping_version, spec.kind) != (
        release.adapter_version,
        release.mapping_version,
        release.kind,
    ):
        raise DryRunError(
            6,
            f"Release {release.pk} was registered with {spec.name} {release.adapter_version}"
            f", mapping {release.mapping_version}, kind {release.kind}, and this code "
            f"carries {spec.version}, mapping {spec.mapping_version}, kind "
            f"{spec.kind}. Register it again with this adapter "
            "(graph_register_release), which updates a draft without evidence. "
            "Nothing was written.",
        )
    return adapter


def _run_locked(release, stack):
    """Precondition 9: the release's run lock, entered into `stack`, which
    holds it until the run returns. Then precondition 4 again, on the
    release as stored (a draft with one data file, at most one manifest and
    no open commit run), since a commit may have started or finished
    meanwhile: the release, its data file and its manifest."""
    try:
        stack.enter_context(release_run_lock(release.pk))
    except RunLocked as error:
        raise DryRunError(9, _run_lock_refusal(error)) from None
    release = _release_row(release.pk)
    data, manifest = _release_files(release)
    return release, data, manifest


def _run_lock_refusal(error) -> str:
    """Why the run lock refused a dry run (RunLocked.reason), in words."""
    release = error.release_id
    if error.reason == NESTED:
        why = (
            f"This connection already holds the run lock of release {release}: a "
            "dry run never runs inside another run of it."
        )
    elif error.reason == KEY:
        why = (
            f"Release {release}'s id is beyond {MAX_RUN_LOCK_KEY:,}, the largest a "
            "run lock keys."
        )
    else:
        why = (
            f"A dry run, commit or discard of release {release} is running: one "
            "runs at a time."
        )
    return f"{why} Nothing was written."


def _resolutions(path):
    """The resolution file, loaded: a regular file, as stat() says before
    it is opened and fstat() says of what was opened (O_NONBLOCK, so a FIFO
    put in its place is refused instead of waited on)."""
    not_regular = (
        f"The resolution file {path} is not a regular file. Nothing was written."
    )
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise ResolutionError(not_regular)
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        try:
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ResolutionError(not_regular)
            data = read_bounded(
                stream, what="the resolution file", error=ResolutionError
            )
        return load_resolutions(data)
    except OSError as error:
        raise DryRunError(
            7,
            f"The resolution file {path} cannot be read ({error.strerror or error}). "
            "Nothing was written.",
        ) from None
    except ResolutionError as error:
        message = str(error)
        if "othing was written" not in message:
            message += " Nothing was written."
        raise DryRunError(7, message) from None


def report_file_problem(path) -> str | None:
    """Why a report file may not be written at this path, or None: its
    directory must exist; whatever is at the path already is a regular file,
    which the report replaces in one rename, never a symbolic link, a FIFO
    or a device (/dev/null, /dev/stdout), which a rename would replace with
    a file; and it never lies under MEDIA_ROOT, STATIC_ROOT or
    GRAPH_FILES_ROOT, which hold files that are served, or private."""
    target = Path(path)
    try:
        resolved = target.parent.resolve(strict=True) / target.name
    except (OSError, RuntimeError):
        return f"The report file's directory, {target.parent}, does not exist."
    if not target.name or resolved.is_dir():
        return f"The report file {path} names a directory."
    try:
        # The last component itself, not what a link names.
        mode = os.lstat(resolved).st_mode
    except FileNotFoundError:
        mode = None
    except OSError as error:
        return f"The report file {path} cannot be checked ({error.strerror or error})."
    if mode is not None and not stat.S_ISREG(mode):
        return (
            f"The report file {path} exists and is not a regular file (a link, a "
            "FIFO or a device), which a report never replaces."
        )
    for name in ("MEDIA_ROOT", "STATIC_ROOT", "GRAPH_FILES_ROOT"):
        root = getattr(settings, name, None)
        if not root:
            continue
        root = Path(root).resolve()
        if resolved == root or root in resolved.parents:
            return f"The report file {path} lies under {name}, where it never goes."
    return None


# ── Reading, planning and writing ───────────────────────────────────────────


def _read_and_write(run, release, data, manifest_file, adapter, loaded, report):
    try:
        stream = open_stored(data)
    except StorageError as error:
        report.file_error(Issue("stored_file_mismatch", str(error), line=None))
        return
    with stream:
        manifest = EMPTY_MANIFEST
        if manifest_file is not None:
            manifest = _manifest(manifest_file, release, report)
            if manifest is None:
                return
        ctx = RunContext(
            load_registry_snapshot(),
            release.is_synthetic,
            manifest,
            release.pk,
            release.dataset_id,
        )
        report.ctx = ctx
        mapping_run = MappingRun(
            adapter=adapter,
            ctx=ctx,
            release=_Release(
                release.pk,
                release.dataset_id,
                release.is_synthetic,
                release.supersedes_id,
            ),
            resolutions=loaded,
            create_entities=report.create_entities,
        )
        report.mapping_run = mapping_run
        ended = _read_batches(stream, run, adapter, ctx, mapping_run, report)
        if mapping_run.read_again and not ended:
            # A later batch changed how an earlier batch's rows map: the
            # same checked file is read again, with what was resolved, so
            # write_records updates what changed and the report is gathered
            # from the whole file. The second reading finds nothing new. A
            # reading a file error ended is not read again: the run is
            # refused, and the records written stand.
            stream.seek(0)
            mapping_run.restart()
            report.restart()
            _read_batches(stream, run, adapter, ctx, mapping_run, report)


def _read_batches(stream, run, adapter, ctx, mapping_run, report) -> bool:
    """Read the file once, in batches, each planned and written (_batch).
    A file error ends the reading, its issues in the report: the adapter's
    (at the header nothing was read; later, the records read before it are
    written), or the mapping's (plan_batch: too many distinct identifiers),
    whose batch is dropped, and no batch is planned after it. Returns
    whether a file error ended the reading."""
    records = adapter.iter_records(stream, ctx)
    reading = iter(records)
    batch, size = [], 0
    ended = False
    while True:
        try:
            record = next(reading)
        except StopIteration:
            break
        except FileError as error:
            # At the header nothing was read; later, the records read so
            # far are written, and the run is refused.
            _file_error(report, error)
            ended = True
            break
        # A line too long to keep holds no memory.
        weight = record.size if record.fields is not None else 0
        if batch and (
            len(batch) >= limits.BATCH_ROWS or size + weight > limits.BATCH_BYTES
        ):
            if not _planned(batch, run, mapping_run, report):
                batch, ended = [], True
                break
            batch, size = [], 0
        batch.append(record)
        size += weight
    if batch and not _planned(batch, run, mapping_run, report):
        ended = True
    report.lines = getattr(records, "lines", None)
    report.blank_lines = getattr(records, "blank_lines", None)
    return ended


def _planned(batch, run, mapping_run, report) -> bool:
    """The batch planned and written (_batch); False, writing nothing, when
    the mapping refuses the file (a FileError from plan_batch), whose issues
    go to the report."""
    try:
        _batch(batch, run, mapping_run, report)
    except FileError as error:
        _file_error(report, error)
        return False
    return True


def _file_error(report, error):
    for issue in error.issues:
        report.file_error(issue)


def _manifest(manifest_file, release, report):
    try:
        with open_stored(manifest_file) as stream:
            data = read_bounded(
                stream, what="the release manifest", error=ManifestError
            )
    except StorageError as error:
        report.file_error(Issue("stored_file_mismatch", str(error)))
        return None
    except ManifestError as error:
        report.file_error(Issue("manifest_invalid", str(error)))
        return None
    try:
        return load_manifest(data, release_is_synthetic=release.is_synthetic)
    except ManifestError as error:
        report.file_error(Issue("manifest_invalid", str(error)))
        return None


def _batch(records, run, mapping_run, report):
    plans = plan_batch(records, run_ctx=mapping_run)
    written = write_records(plans, run=run)
    if written.rejected_firsts:
        # A first occurrence an earlier batch planned, and the report
        # counted, is rejected now: the file is read again.
        mapping_run.read_again = True
    report.add(plans, written)


# ── The report ──────────────────────────────────────────────────────────────


class ReportList:
    """One of the report's lists: its first limits.REPORT_LIST_ENTRIES
    entries and its total; with a spool, every entry is also written out
    for the report file."""

    def __init__(self, name, spool=None):
        self.name = name
        self.spool = spool
        self.entries = []
        self.total = 0

    def add(self, entry):
        self.total += 1
        if len(self.entries) < limits.REPORT_LIST_ENTRIES:
            self.entries.append(entry)
        if self.spool is not None:
            self.spool.write(self.name, entry)

    def as_json(self, keep=None) -> dict:
        kept = self.entries if keep is None else self.entries[:keep]
        return {
            "total": self.total,
            "truncated": len(kept) < self.total,
            "entries": [_capped(entry) for entry in kept],
        }


class _Spool:
    """Every list entry, as JSON Lines in a temporary file beside the report
    file, until the report file is written."""

    def __init__(self, path):
        self.path = Path(path)
        descriptor, name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".report-", suffix=".part"
        )
        self.name = Path(name)
        self.file = os.fdopen(descriptor, "w", encoding="utf-8")

    def write(self, section, entry):
        self.file.write(_line({"section": section, **entry}))

    def finish(self, summary):
        """Write the report file: the summary line, then every entry, and
        put it in place in one rename."""
        self.file.flush()
        self.file.close()
        descriptor, name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".report-", suffix=".part"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as out:
                out.write(_line({"section": "summary", **summary}))
                with open(self.name, encoding="utf-8") as entries:
                    for line in entries:
                        out.write(line)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        except BaseException:
            _unlink(name)
            raise
        finally:
            _unlink(self.name)

    def discard(self):
        if not self.file.closed:
            self.file.close()
        _unlink(self.name)


def _line(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n"


def _unlink(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class Report:
    """What one run reports, gathered batch by batch (add) and finished
    once the file is read (finish)."""

    def __init__(self, release, adapter, create_entities, report_file=None):
        self.release = release
        self.adapter = adapter
        self.create_entities = create_entities
        self.report_file = report_file
        self.ctx = None
        self.mapping_run = None
        self.spool = None
        # The report file once written: a run that fails after removes it.
        self.written = None
        # What write_records wrote, over both readings of the file.
        self.created = self.updated = 0
        self._gather()

    def _gather(self):
        """Gather what the batches give, from nothing."""
        if self.report_file is not None:
            self.spool = _Spool(self.report_file)
        self.lists = {name: ReportList(name, self.spool) for name in LISTS}
        self.lines = None
        self.blank_lines = None
        self.statuses = Counter()
        self.unstored = 0
        self.mapped = Counter()
        self.relationships = {}
        self.assertions = Counter()
        self.papers = {}
        self.terms = {}
        # {(relationship identity, other release): entry}.
        self.conflicts = {}
        self.other_outcomes = {}
        self.violations = 0

    def restart(self):
        """Forget what the batches gave, but what write_records wrote: the
        file is read again (MappingRun.read_again)."""
        if self.spool is not None:
            self.spool.discard()
            self.spool = None
        self._gather()

    # ── Gathering ──

    def file_error(self, issue):
        self.lists["file_errors"].add(issue.as_json())

    def add(self, plans, written):
        """One batch's plans, and what write_records did with them. A first
        occurrence that a later batch rejects (written.rejected_firsts) is
        counted when the file is read again, whose plans carry the conflict
        (MappingRun.read_again)."""
        for plan in plans:
            self._plan(plan)
        self._conflicts(plans)
        self.created += written.created
        self.updated += written.updated
        for key, line, status, issues in written.would_change:
            self.lists["would_change"].add(
                {
                    "line": line,
                    "key": key,
                    "status": "mapped",
                    "planned": status,
                    "issues": issues,
                }
            )

    def _plan(self, plan):
        record = plan.record
        line, key = record.line_number, record.record_key
        # Unstored rows too: a synthetic release is checked on every row
        # whose cells could be read.
        violations = [
            issue.as_json()
            for issue in plan.issues
            if issue.code == "synthetic_violation"
        ]
        if violations:
            self.violations += 1
            self.lists["synthetic_violations"].add(
                {"line": line, "key": key, "issues": violations}
            )
        if plan.unstored:
            self.unstored += 1
            self.lists["unstored"].add(
                {
                    "line": line,
                    "key": key,
                    "codes": sorted({issue.code for issue in plan.issues}),
                    "issues": [issue.as_json() for issue in plan.issues],
                }
            )
            return
        self.statuses[plan.status] += 1
        issues = [issue.as_json() for issue in plan.issues]
        if plan.status == RecordStatus.REJECTED:
            self.lists["rejected"].add({"line": line, "key": key, "issues": issues})
        for issue in plan.issues:
            if issue.code == "label_differs":
                self.lists["label_differs"].add(
                    {"line": line, "key": key, **_short(issue)}
                )
        for text in plan.notes:
            self.lists["notes"].add({"line": line, "key": key, "note": text})
        for endpoint in plan.endpoints:
            target = endpoint.target
            if isinstance(target, Existing) and target.basis == "curated":
                self._curated(plan, endpoint)
        for claim in plan.claims:
            rel_type = self.ctx.registry.relationship_types.get(claim.relationship_type)
            if rel_type is not None and rel_type.is_reference:
                self.lists["reference_links"].add(
                    {
                        "line": line,
                        "key": key,
                        "relationship_type": claim.relationship_type,
                        "subject": _stated(claim.subject.entity),
                        "object": _stated(claim.object.entity),
                        "status": plan.status,
                    }
                )
        for paper in plan.papers:
            seen = self.papers.setdefault(
                paper_identity(paper.stated), {"rows": 0, "lines": []}
            )
            _count(seen, line)
        for claim in plan.claims:
            for curie in _claim_terms(claim):
                _count(self.terms.setdefault(curie, {"rows": 0, "lines": []}), line)
        if plan.status == RecordStatus.MAPPED:
            self._mapped(plan)

    def _curated(self, plan, endpoint):
        info = self.mapping_run.identifiers[endpoint.key]
        entry = info.entry
        self.lists["curated"].add(
            {
                "line": plan.record.line_number,
                "key": plan.record.record_key,
                "status": plan.status,
                "side": endpoint.side,
                "namespace": info.namespace,
                "value": endpoint.stated.value,
                "entity_type": info.entity_type,
                "taxon": info.taxon,
                "resolver_status": info.status,
                "entity": endpoint.target.entity_id,
                "entry": entry.index,
                "rationale": entry.rationale,
                "reviewer": entry.reviewer,
            }
        )

    def _mapped(self, plan):
        line = plan.record.line_number
        ends = iter(plan.endpoints)
        if plan.assertions_exist:
            self.assertions["existing"] += 1
        else:
            self.assertions["new"] += 1
        for n, claim in enumerate(plan.claims):
            subject, obj = next(ends), next(ends)
            self.mapped[
                (
                    claim.relationship_type,
                    claim.outcome,
                    claim.effect,
                    claim.genetic_sign,
                    claim.interaction_level,
                )
            ] += 1
            rel_type = self.ctx.registry.relationship_types[claim.relationship_type]
            pair = [_endpoint_key(subject.target), _endpoint_key(obj.target)]
            if rel_type.orientation == Orientation.SYMMETRIC:
                pair.sort()
            exists = False
            if n < len(plan.relationships):
                exists = bool(plan.relationships[n][1])
            seen = self.relationships.setdefault(
                (claim.relationship_type, *pair),
                {"exists": exists, "records": 0, "lines": []},
            )
            _count(seen, line, "records")

    def _conflicts(self, plans):
        """Conflicts of the batch's mapped records with other releases'
        evidence on the same existing relationships: three queries."""
        claims = []
        for plan in plans:
            if plan.status != RecordStatus.MAPPED:
                continue
            for claim, (canonical, exists) in zip(plan.claims, plan.relationships):
                if canonical is not None and exists:
                    claims.append((plan, claim, canonical))
        if not claims:
            return
        identities = {_identity(canonical) for _, _, canonical in claims}
        rows = [
            row
            for row in EvidenceAssertion.objects.filter(
                relationship__relationship_type_id__in={i[0] for i in identities},
                relationship__subject_id__in={i[1] for i in identities},
                relationship__object_id__in={i[2] for i in identities},
                release__status__in=COMPARED_STATUSES,
                release__is_synthetic=self.release.is_synthetic,
            )
            .exclude(release_id=self.release.pk)
            .values(
                "pk",
                "release_id",
                "relationship__relationship_type_id",
                "relationship__subject_id",
                "relationship__object_id",
                "outcome",
                "effect",
                "genetic_sign",
                "interaction_level",
            )
            if _row_identity(row) in identities
        ]
        if not rows:
            return
        scopes = effective_scopes_by_release({row["release_id"] for row in rows})
        rows = [row for row in rows if INTERNAL in scopes.get(row["release_id"], ())]
        physical = {
            slug
            for slug, rules in self.ctx.registry.relationship_types.items()
            if rules.family == RelationshipFamily.PHYSICAL
        }
        methods = defaultdict(set)
        wanted = [
            row["pk"]
            for row in rows
            if row["relationship__relationship_type_id"] in physical
        ]
        if wanted:
            for assertion_id, term in EvidenceMethod.objects.filter(
                assertion_id__in=wanted, role=MethodRole.DETECTION
            ).values_list("assertion_id", "term_id"):
                methods[assertion_id].add(term)
        by_identity = defaultdict(list)
        for row in rows:
            by_identity[_row_identity(row)].append(row)
        for plan, claim, canonical in claims:
            identity = _identity(canonical)
            for row in by_identity[identity]:
                kinds = _conflict_kinds(claim, row)
                for store, found in (
                    (self.conflicts, kinds - {OTHER_OUTCOME}),
                    (self.other_outcomes, kinds & {OTHER_OUTCOME}),
                ):
                    if found:
                        self._conflict(
                            store, plan, claim, identity, row, found, physical, methods
                        )

    def _conflict(self, store, plan, claim, identity, row, kinds, physical, methods):
        """One record of this release against one assertion of another, in
        the entry of their relationship and that release."""
        key = (identity, row["release_id"])
        entry = store.get(key)
        shows_level = identity[0] in physical
        if entry is None:
            entry = store[key] = {
                "relationship": {
                    "type": identity[0],
                    "subject": identity[1],
                    "object": identity[2],
                },
                "other_release": row["release_id"],
                "kinds": set(),
                "corrected_by_this_release": (
                    row["release_id"] == self.release.supersedes_id
                ),
                "this": _side(shows_level, lines=True),
                "other": _side(shows_level),
                "_last": None,
                "_assertions": set(),
            }
        entry["kinds"] |= kinds
        this, other = entry["this"], entry["other"]
        line = plan.record.line_number
        # A record meets an entry with each assertion of the other release
        # on the relationship, one after the other: it counts once.
        if entry["_last"] != line:
            entry["_last"] = line
            _count(this, line, "records")
            _stated_side(
                this,
                claim.outcome,
                claim.effect,
                claim.genetic_sign,
                claim.interaction_level,
                {method.term for method in claim.methods},
                shows_level,
            )
        if row["pk"] not in entry["_assertions"]:
            entry["_assertions"].add(row["pk"])
            other["assertions"] = other.get("assertions", 0) + 1
            _stated_side(
                other,
                row["outcome"],
                row["effect"],
                row["genetic_sign"],
                row["interaction_level"],
                methods.get(row["pk"], set()),
                shows_level,
            )

    # ── Finishing ──

    def finish(self) -> dict:
        """The report as ImportRun.report keeps it: past limits.REPORT_BYTES
        as JSON, every list keeps half as many entries, again and again,
        and the report says it is truncated."""
        if self.mapping_run is not None:
            self._finish_lists()
            self.mapping_run = None
        header = self._header()
        report = self._report(header, keep=None)
        size = len(json.dumps(report, default=str))
        keep = limits.REPORT_LIST_ENTRIES
        while size > limits.REPORT_BYTES and keep > 0:
            keep //= 2
            report = self._report(header, keep=keep)
            report["truncated"] = True
            size = len(json.dumps(report, default=str))
        return report

    def _finish_lists(self):
        run = self.mapping_run
        for entry in _identifier_entries(run):
            self.lists["identifiers"].add(entry)
        for name, entries in _curated_entries(run).items():
            for entry in entries:
                self.lists[name].add(entry)
        for planned in sorted(run.planned.values(), key=_first_line):
            # A value stated at two taxa creates nothing (MappingRun).
            if not planned.records or planned.contradicted:
                continue
            create = planned.create
            self.lists["would_create"].add(
                {
                    "namespace": create.namespace,
                    "value": planned.written or create.value,
                    "lookup": create.lookup,
                    "entity_type": create.entity_type,
                    "taxon": create.taxon,
                    "identity_key": create.identity_key,
                    "label": planned.chosen,
                    "label_line": planned.label_line,
                    "records": planned.records,
                    "lines": planned.lines,
                }
            )
            for label, line in planned.other_labels.items():
                self.lists["label_conflicts"].add(
                    {
                        "identity_key": create.identity_key,
                        "label": planned.chosen,
                        "label_line": planned.label_line,
                        "other_label": label,
                        "line": line,
                        "rows_with_other_labels": planned.other_label_count,
                    }
                )
        for (rel_type, subject, obj), seen in sorted(
            self.relationships.items(), key=lambda item: item[1]["lines"][:1]
        ):
            self.lists["relationships"].add(
                {
                    "relationship_type": rel_type,
                    "subject": _shown_key(subject),
                    "object": _shown_key(obj),
                    "exists": seen["exists"],
                    "records": seen["records"],
                    "lines": seen["lines"],
                }
            )
        self._finish_papers(run)
        self._finish_terms(run)
        for name, store in (
            ("conflicts", self.conflicts),
            ("other_outcomes", self.other_outcomes),
        ):
            for entry in store.values():
                entry.pop("_last", None)
                entry.pop("_assertions", None)
                entry["kinds"] = sorted(entry["kinds"])
                for side in (entry["this"], entry["other"]):
                    for field, value in side.items():
                        if isinstance(value, set):
                            side[field] = sorted(
                                value, key=lambda v: (v is None, str(v))
                            )
                self.lists[name].add(entry)

    def _finish_papers(self, run):
        for identity, seen in sorted(
            self.papers.items(), key=lambda item: item[1]["lines"][:1]
        ):
            target = run.papers.get(identity)
            entry = {
                "kind": identity[0],
                "value": identity[1],
                "status": "found" if isinstance(target, ExistingPaper) else "new",
                "rows": seen["rows"],
                "lines": seen["lines"],
            }
            if isinstance(target, ExistingPaper):
                entry["publication"] = target.pk
            elif target is not None and target.kind == "local":
                entry.update(
                    title=target.title,
                    year=target.year,
                    is_synthetic=target.is_synthetic,
                )
            self.lists["publications"].add(entry)
        declared = self.ctx.manifest.publications
        for key, (title, year) in sorted(run.paper_differences.items()):
            self.lists["manifest_differences"].add(
                {
                    "key": key,
                    "stored_title": title,
                    "stored_year": year,
                    "manifest_title": declared[key].title,
                    "manifest_year": declared[key].year,
                }
            )

    def _finish_terms(self, run):
        declared = self.ctx.manifest.terms
        for curie, seen in sorted(self.terms.items()):
            stored = run.terms.get(curie)
            label = declared.get(curie)
            pinned = PHYSICAL_INTERACTION_LEVELS.get(curie)
            conflict = None
            if label is None:
                conflict = "Not declared in the manifest: rows citing it are rejected."
            elif pinned is not None and label != pinned[1]:
                conflict = (
                    f"The PSI-MI crosswalk pins {curie}'s label as {pinned[1]!r}; "
                    f"the manifest states {label!r}, which a commit does not write."
                )
            elif stored is not None and label != stored:
                conflict = (
                    f"Term {curie} keeps its label {stored!r}; the manifest states "
                    f"{label!r}, which a commit does not write."
                )
            self.lists["terms"].add(
                {
                    "curie": curie,
                    "status": "stored" if stored is not None else "new",
                    "label": label,
                    "stored_label": stored,
                    "pinned_label": pinned[1] if pinned else None,
                    "conflict": conflict,
                    "rows": seen["rows"],
                    "lines": seen["lines"],
                }
            )

    def _header(self) -> dict:
        """What the report says of the run as a whole, read once."""
        release = self.release
        licence = latest_licences([release.pk]).get(release.pk)
        spec = self.adapter.spec
        state = registry_state()
        return {
            "verdict": (
                REFUSED if self.lists["file_errors"].total or self.violations else OK
            ),
            "truncated": False,
            "adapter": {
                "name": spec.name,
                "version": spec.version,
                "mapping": spec.mapping_version,
                "kind": spec.kind,
                "isoform_specific": spec.isoform_specific,
                "changes_note": spec.changes_note,
            },
            "registry": {"code": state.code, "database": state.database},
            "release": {
                "id": release.pk,
                "dataset": release.dataset_id,
                "version": release.version,
                "status": release.status,
                "is_synthetic": release.is_synthetic,
                "store_raw_rows": release.store_raw_rows,
                "supersedes": release.supersedes_id,
            },
            "licence": (
                None
                if licence is None
                else {
                    "id": licence.pk,
                    "licence_name": licence.licence_name,
                    "redistribution": licence.redistribution,
                    "modification": licence.modification,
                    "share_alike": licence.share_alike,
                    "records_terms": licence.records_terms,
                }
            ),
            "approvals": sorted(effective_scopes(release)),
            "create_entities": self.create_entities,
        }

    def _report(self, header, keep) -> dict:
        lists = self.lists

        def listed(name):
            return lists[name].as_json(keep)

        release = self.release
        existing = sum(1 for seen in self.relationships.values() if seen["exists"])
        return {
            **header,
            "counts": {
                "lines": self.lines,
                "blank_lines": self.blank_lines,
                "records": {
                    status: self.statuses.get(status, 0)
                    for status in RecordStatus.values
                },
                "unstored": self.unstored,
                "created": self.created,
                "updated": self.updated,
                "mapped": [
                    {
                        "relationship_type": rel_type,
                        "outcome": outcome,
                        "effect": effect,
                        "genetic_sign": sign,
                        "interaction_level": level,
                        "records": count,
                    }
                    for (rel_type, outcome, effect, sign, level), count in sorted(
                        self.mapped.items(), key=lambda item: tuple(map(str, item[0]))
                    )
                ],
            },
            "file_errors": listed("file_errors"),
            "rejected": listed("rejected"),
            "unstored": listed("unstored"),
            "identifiers": listed("identifiers"),
            "curated": {
                "records": listed("curated"),
                "unused": listed("curated_unused"),
                "redundant": listed("curated_redundant"),
                "contradicted": listed("curated_contradicted"),
                "not_applied": listed("curated_not_applied"),
            },
            "would_create": listed("would_create"),
            "label_conflicts": listed("label_conflicts"),
            "label_differs": listed("label_differs"),
            "relationships": {
                "existing": existing,
                "new": len(self.relationships) - existing,
                **listed("relationships"),
            },
            "assertions": {
                "existing": self.assertions["existing"],
                "new": self.assertions["new"],
            },
            "publications": {
                **listed("publications"),
                "manifest_differences": listed("manifest_differences"),
            },
            "terms": listed("terms"),
            "reference_links": listed("reference_links"),
            "conflicts": {
                **listed("conflicts"),
                "other_outcomes": listed("other_outcomes"),
            },
            "synthetic": {
                "checked": release.is_synthetic,
                "passed": not self.violations,
                "violations": listed("synthetic_violations"),
            },
            "would_change": listed("would_change"),
            "notes": listed("notes"),
        }

    def write_file(self, report):
        """Write the report file, if one was asked for: a summary line, then
        every entry of every list."""
        if self.spool is None:
            return
        summary = {
            name: report[name]
            for name in (
                "verdict",
                "truncated",
                "adapter",
                "registry",
                "release",
                "licence",
                "approvals",
                "create_entities",
                "counts",
                "assertions",
            )
        }
        summary["synthetic"] = {
            "checked": report["synthetic"]["checked"],
            "passed": report["synthetic"]["passed"],
        }
        summary["totals"] = {name: self.lists[name].total for name in LISTS}
        self.spool.finish(summary)
        self.written = self.spool.path
        self.spool = None

    def discard(self):
        """Remove the spool, or the report file, of a run that failed: it
        leaves no report file."""
        if self.spool is not None:
            self.spool.discard()
            self.spool = None
        if self.written is not None:
            _unlink(self.written)
            self.written = None
        for listed in self.lists.values():
            listed.spool = None


# ── Report helpers ──────────────────────────────────────────────────────────


def _identifier_entries(run) -> list:
    entries = []
    for info in run.identifiers.values():
        target = info.target
        entry = {
            "namespace": info.namespace,
            "lookup": info.lookup,
            "version": info.version,
            "entity_type": info.entity_type,
            "taxon": info.taxon,
            "written": info.written,
            "status": info.status,
            "mapping": _mapping(info),
            "candidates": list(info.candidates),
            "records": info.records,
            "lines": info.lines,
        }
        if isinstance(target, Existing):
            entry["entity"] = target.entity_id
        elif isinstance(target, Create):
            entry["identity_key"] = target.identity_key
        elif isinstance(target, Unmapped):
            entry["code"], entry["reason"] = target.code, target.reason
        if info.refusal is not None:
            entry["code"], entry["reason"] = info.refusal
        if info.entry is not None:
            entry["entry"] = info.entry.index
        entries.append(entry)
    for refused in run.refused.values():
        if not refused.records:
            continue
        entries.append(
            {
                "namespace": refused.namespace,
                "lookup": None,
                "value": refused.value,
                "entity_type": refused.entity_type,
                "taxon": refused.taxon,
                "written": refused.written,
                "status": None,
                "mapping": "refused",
                "code": refused.code,
                "reason": refused.reason,
                "candidates": [],
                "records": refused.records,
                "lines": refused.lines,
            }
        )
    return sorted(entries, key=_identifier_order)


def _identifier_order(entry) -> tuple:
    if entry["mapping"] == "refused":
        rank = 3
    else:
        rank = _STATUS_ORDER.get(entry["status"], 3)
    return rank, entry["lines"][:1]


def _mapping(info) -> str:
    if info.refusal is not None:
        return "refused"
    target = info.target
    if isinstance(target, Existing):
        return target.basis
    if isinstance(target, Create):
        return "planned"
    return "unmapped"


def _curated_entries(run) -> dict:
    """The resolution file's entries the run did not use, or used
    otherwise than to map."""
    lists = defaultdict(list)
    for entry in run.resolutions.entries:
        uses = run.entry_uses.get(entry.index, set())
        shown = entry.as_json()
        if not uses:
            lists["curated_unused"].append(shown)
        if "redundant" in uses:
            lists["curated_redundant"].append(shown)
        if "contradicted" in uses:
            lists["curated_contradicted"].append(shown)
        if uses & {"not_a_candidate", "refused"}:
            lists["curated_not_applied"].append(shown)
    return lists


def _count(seen, line, name="rows"):
    seen[name] = seen.get(name, 0) + 1
    if len(seen["lines"]) < limits.REPORT_ENTRY_LINES and line not in seen["lines"]:
        seen["lines"].append(line)


def _first_line(planned) -> tuple:
    return (not planned.lines, planned.lines[:1])


def _claim_terms(claim):
    for term in (
        claim.interaction_type,
        claim.eco_term,
        *(method.term for method in claim.methods),
        *(context.term for context in claim.contexts),
    ):
        if isinstance(term, str):
            yield term


def _endpoint_key(target):
    """An endpoint in a relationship's key: the entity id, or the planned
    identity key; sortable among each other."""
    if isinstance(target, Existing):
        return (0, target.entity_id)
    return (1, target.identity_key)


def _shown_key(key) -> dict:
    kind, value = key
    return {"entity": value} if kind == 0 else {"planned": value}


def _stated(ref) -> str:
    return f"{ref.namespace}:{ref.value}"


def _short(issue) -> dict:
    return {"field": issue.field, "message": issue.message}


def _identity(canonical) -> tuple:
    return (canonical.type_slug, canonical.subject_id, canonical.object_id)


def _row_identity(row) -> tuple:
    return (
        row["relationship__relationship_type_id"],
        row["relationship__subject_id"],
        row["relationship__object_id"],
    )


def _conflict_kinds(claim, row) -> set:
    """What a record's claim and another release's assertion on the same
    relationship disagree on: opposite effects or genetic signs (both
    supported), supported against tested-absent; and OTHER_OUTCOME when
    either is inconclusive or not significant, which is listed apart."""
    this, other = claim.outcome, row["outcome"]
    kinds = set()
    if this == other == Outcome.SUPPORTED:
        if {claim.effect, row["effect"]} == {Effect.ACTIVATING, Effect.INHIBITING}:
            kinds.add("effect")
        if {claim.genetic_sign, row["genetic_sign"]} == {
            GeneticSign.NEGATIVE,
            GeneticSign.POSITIVE,
        }:
            kinds.add("sign")
    if {this, other} == {Outcome.SUPPORTED, Outcome.TESTED_ABSENT}:
        kinds.add("outcome")
    if this in OTHER_OUTCOMES or other in OTHER_OUTCOMES:
        kinds.add(OTHER_OUTCOME)
    return kinds


def _side(shows_level, *, lines=False) -> dict:
    """One side of a conflict: what it states, and for a physical type each
    level and detection method."""
    side = {"outcomes": set(), "effects": set(), "genetic_signs": set()}
    if shows_level:
        side.update(levels=set(), methods=set())
    if lines:
        side.update(records=0, lines=[])
    return side


def _stated_side(side, outcome, effect, sign, level, methods, shows_level):
    side["outcomes"].add(outcome)
    side["effects"].add(effect)
    side["genetic_signs"].add(sign)
    if shows_level:
        side["levels"].add(level)
        side["methods"] |= set(methods)


def _capped(entry):
    """A list entry as the report keeps it: at most
    limits.REPORT_ENTRY_ISSUES issues, REPORT_ENTRY_CANDIDATES candidates and
    REPORT_ENTRY_LINES lines, and labels of at most REPORT_LABEL_CHARS
    characters."""
    if isinstance(entry, list):
        return [_capped(item) for item in entry]
    if not isinstance(entry, dict):
        return entry
    capped = {}
    for name, value in entry.items():
        if name == "issues" and isinstance(value, list):
            value = value[: limits.REPORT_ENTRY_ISSUES]
        elif name == "candidates" and isinstance(value, list):
            value = value[: limits.REPORT_ENTRY_CANDIDATES]
        elif name == "lines" and isinstance(value, list):
            value = value[: limits.REPORT_ENTRY_LINES]
        elif (name == "label" or name.endswith("_label")) and isinstance(value, str):
            value = value[: limits.REPORT_LABEL_CHARS]
        capped[name] = _capped(value)
    return capped


def summary_lines(run) -> list:
    """What graph_import prints of a finished run."""
    report = run.report
    counts = report["counts"]
    records = ", ".join(
        f"{count} {status}" for status, count in counts["records"].items()
    )
    relationships = report["relationships"]
    lines = [
        f"Dry run {run.pk} of release {run.release_id}: {report['verdict']}"
        + (" (the report is truncated)" if report["truncated"] else "")
        + ".",
        f"Lines {counts['lines']}, blank {counts['blank_lines']}; records: "
        f"{records}; unstored rows {counts['unstored']}.",
        f"Records created {counts['created']}, updated {counts['updated']}.",
        f"File errors {report['file_errors']['total']}; rejected "
        f"{report['rejected']['total']}; identifiers "
        f"{report['identifiers']['total']}; curated "
        f"{report['curated']['records']['total']}.",
        f"Entities a commit with --create-entities would create: "
        f"{report['would_create']['total']}.",
        f"Relationships: {relationships['existing']} existing, "
        f"{relationships['new']} new; assertions: "
        f"{report['assertions']['existing']} existing, "
        f"{report['assertions']['new']} new; conflicts "
        f"{report['conflicts']['total']}.",
    ]
    if report["synthetic"]["checked"]:
        lines.append(
            "Synthetic check: "
            + (
                "passed."
                if report["synthetic"]["passed"]
                else f"{report['synthetic']['violations']['total']} rows violate it."
            )
        )
    for issue in report["file_errors"]["entries"]:
        lines.append(f"File error {issue['code']}: {issue['message']}")
    return lines
