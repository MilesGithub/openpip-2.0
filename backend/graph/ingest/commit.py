"""The commit (architecture §8, step 4; G5): a release imported as its latest
dry run reported it, batch by batch, and then staged. graph_import --commit
calls it, and so will G6's loader and I1's task.

Preconditions, in this order, each a CommitError naming its number, before
anything is written: the switch is on; the actor is an active staff
account; the database's registry version is the code's; the release exists
and is not withdrawn; the resolution file, if given, is valid; a release
that is not a draft returns "already committed", writing nothing, when its
latest commit run completed with these options, and is refused otherwise;
the release has one data file and at most one manifest; a release that is
not synthetic has an effective internal approval; and the release's run
lock (graph.ingest.runlock) is taken, which the commit holds until it
returns. Holding it, the commit marks failed the release's runs that a
process which ended left running (end_dead_runs), and reads the release
again (a commit that finished meanwhile staged it). Then the release's open
commit run, if it has one, decides first: it is resumed with the options it
was started with, or refused with what to do; the code's adapter has the
release's version, mapping version and kind; and the release's latest dry
run is trusted only when it completed with the verdict ok, under the
release's adapter, versions and kind and the database's registry version,
after every commit run that is not open, and with these options.

The planning reads the stored data file once, or twice when a later batch
changes how an earlier batch's rows map (MappingRun.read_again), in the dry
run's batches and through its plan_batch, and writes nothing. It refuses
the file (a stored file or manifest that changed, a file error, more
distinct identifiers than limits.MAX_IDENTIFIERS, a synthetic violation on
any line), records that are not those the dry run stored, and records that
map to another status than the dry run gave them. A resume plans anew, with
the entities its earlier attempts created: in a case-exact or version-split
identity namespace (none in registry v1) another case or version of one is
then a candidate, and the resume is refused.

The run is then created, or the open one resumed (its report records who
resumed it, when, and where it had stopped), in one transaction that reads
everything again. The file is read a last time with the planning's mapping,
which chooses no label again (plan_batch with check=False), and each batch
is written in one transaction, which takes its locks in one order: the
release, the run, every registry row the batch's writes are checked against
(then the registry version is read again), the entities it creates
(create_entity, in lock_order), every paper it cites
(get_or_create_publication, in lock_order), the terms it names that were
not stored (get_or_create_term, in lock_order), its relationships (one
create_relationships call, which orders them itself), its records' evidence
(record_claims, a no-op for a record already recorded) and the checkpoint,
its last line, saved with the run's report. A resume skips every batch up
to its checkpoint. A refusal of a service or a record rule, or a database
error, rolls the batch back whole and stops the attempt (CommitStopped):
its run is marked failed while the connection still holds the run lock,
and running the commit again resumes it.

Finishing is one transaction: every mapped record holds evidence, the
release's statistics are recounted (graph.stats), the release is staged and
its run completed, so a staged release never has an open commit run.
"""

from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field

from django.db import DatabaseError, IntegrityError, InterfaceError, transaction
from django.db.models import Exists, OuterRef
from django.db.models.fields.json import KT
from django.utils import timezone

from graph.approvals import INTERNAL, is_effective
from graph.claims import create_relationships, record_claims
from graph.entities import EntityError, create_entity
from graph.models import (
    DatasetRelease,
    Entity,
    EntityType,
    EvidenceAssertion,
    IdentifierNamespace,
    ImmutableRecordError,
    ImportMode,
    ImportRun,
    ImportState,
    RecordStatus,
    RelationshipType,
    ReleaseStatus,
    ScoreType,
    SourceRecord,
    share_lock,
)
from graph.preconditions import locked_release, storable, typed_graph_enabled
from graph.publications import PublicationError, get_or_create_publication
from graph.registry import REGISTRY_VERSION, registry_state
from graph.stats import release_stats
from graph.storage import StorageError, open_stored
from graph.terms import TermError, get_or_create_term

from . import limits
from .context import RunContext, load_registry_snapshot
from .dry_run import (
    OK,
    REFUSED,
    DryRunError,
    _active_staff,
    _manifest,
    _Release,
    _release_files,
    _resolutions,
)
from .manifest import EMPTY_MANIFEST
from .mapping import (
    Create,
    MappingRun,
    RunStopped,
    _check,
    _curies,
    check_adapter_unchanged,
    checked_claims,
    paper_identity,
    plan_batch,
)
from .protocol import ERROR, FileError, Issue, quoted, rejects
from .registry import AdapterSpecError, UnknownAdapter, get_adapter
from .resolutions import NO_RESOLUTIONS
from .runlock import (
    KEY,
    MAX_RUN_LOCK_KEY,
    NESTED,
    RunLocked,
    end_dead_runs,
    release_run_lock,
    run_lock_lost,
    runs_are_locked,
)

# CommitResult's outcomes.
COMMITTED = "committed"
RESUMED = "resumed"
ALREADY_COMMITTED = "already committed"
# The checkpoint's shape: {"version", "line", "batches"}.
CHECKPOINT_VERSION = 1
# The records a refusal of precondition 15 names.
EXAMPLES = 5
# A commit run is open while it is running or failed (resumable).
OPEN_STATES = (ImportState.RUNNING, ImportState.FAILED)
CLOSED_STATES = (ImportState.COMPLETED, ImportState.ABANDONED)
# What the report counts as created, in this order.
CREATED = ("entities", "relationships", "publications", "terms")
# How a service or a record rule refuses a write: the batch stops with its
# message. InvalidIdentifier, ClaimError, TermError and create_entity's own
# refusals (an unknown type or namespace, a blank or long label) are
# ValueErrors.
REFUSALS = (EntityError, ValueError, PublicationError, TermError, ImmutableRecordError)


class CommitError(Exception):
    """The commit did not start: precondition `precondition` (1 to 15, in
    the order they are checked) failed. Nothing was written, but the runs
    that processes which ended left running, which the commit marked failed
    once it held the run lock: the message says so."""

    def __init__(self, precondition, message):
        super().__init__(message)
        self.precondition = precondition


class CommitStopped(RunStopped):
    """The commit stopped once its run existed; the message is the cause
    alone. The run (run_id) is resumable: running the commit again resumes
    it. marked: whether the run was marked failed, which it is not when the
    marking could not be written (the connection lost its session, and with
    it the run lock, or the database refused the write). The run then stays
    running: on PostgreSQL until the release's next commit or discard marks
    it, and on a database without the run lock until a discard gives it up,
    since a commit never resumes a running run there. abandoned: whether a
    discard gave the run up meanwhile, which only a database without the run
    lock allows."""

    def __init__(self, message, *, run_id, marked, abandoned=False):
        super().__init__(message)
        self.run_id = run_id
        self.marked = marked
        self.abandoned = abandoned


@dataclass(frozen=True)
class CommitResult:
    """What commit_release did."""

    run: ImportRun
    # COMMITTED, RESUMED or ALREADY_COMMITTED.
    outcome: str


def lock_order(keys) -> list:
    """The order a batch writes one kind of shared row in: entities by
    identity key, papers by paper identity, terms by CURIE. Every commit
    takes them in this one total order, after every shared registry lock,
    so two commits that share rows wait on each other but never in a cycle.
    Tests replace it to show that they would."""
    return sorted(keys)


# ── The service ─────────────────────────────────────────────────────────────


def commit_release(
    release, *, triggered_by, create_entities=False, resolutions=None
) -> CommitResult:
    """Commit the release (a row or its id) as its latest dry run reported
    it, with the same options, and stage it; or resume its open commit run.
    `resolutions` is a path. Raises CommitError, writing nothing, when a
    precondition fails, and CommitStopped once the run exists and the
    attempt stops; any other exception after that marks the run failed
    when it can and is raised again as it is. The release's run lock is
    held from precondition 9 until it returns."""
    commit = _Commit(triggered_by, bool(create_entities))
    found = commit.before_the_lock(release, resolutions)
    if found is not None:
        return found
    with ExitStack() as held:
        found = commit.with_the_lock(held)
        if found is not None:
            return found
        return commit.attempt()


class _Commit:
    """One call of commit_release: what its preconditions read, and its
    attempt."""

    def __init__(self, user, create_entities):
        self.user = user
        self.create_entities = create_entities
        self.loaded = NO_RESOLUTIONS
        self.release = None
        self.data = None
        self.manifest_file = None
        self.adapter = None
        # The release's latest dry run, the one a commit trusts (12).
        self.dry = None
        # The release's open commit run (10), resumed when it is not None.
        self.open = None
        # The runs end_dead_runs marked failed (9), which refusals name.
        self.dead = []
        self.ctx = None
        self.mapping_run = None
        self.planning = None
        self.readings = 0
        self.run = None
        self.report = None
        self.checkpoint_line = 0
        # The CURIEs this attempt has recorded (get_or_create_term).
        self.terms_done = set()

    # ── Refusals ──

    def _refuse(self, n, cause, advice=None, *, joined=": ", stored_file=False):
        """Raise CommitError(n): the cause, then the advice; while the
        release has an open commit run, the advice that applies while it is
        open instead (11 to 15). The message ends with what was written."""
        if self.open is not None and n >= 11:
            text = f"{cause}. {self._open_advice(stored_file)}"
        elif advice:
            text = f"{cause}{joined}{advice}."
        else:
            text = f"{cause}."
        raise CommitError(n, f"{text} {_ending(self.dead)}")

    def _refuse_as_is(self, n, text):
        raise CommitError(n, f"{text} {_ending(self.dead)}")

    def _open_advice(self, stored_file) -> str:
        run = self.open.pk
        if stored_file:
            return (
                f"Its open commit run {run} cannot be resumed while this holds: "
                "discard it with graph_discard_release, then register it again with "
                "graph_register_release, which restores the stored copy, and dry-run "
                "it."
            )
        return (
            f"Its open commit run {run} cannot be resumed while this holds: discard "
            "it with graph_discard_release, then dry-run it again."
        )

    def _refuse_file(self, first, count):
        """Precondition 13: the file is refused, with its first issue."""
        self._refuse(
            13,
            f"The file of release {self.release.pk} is refused: {_issue_text(first)} "
            f"({_counted(count, 'issue')})",
            stored_file=first.code == "stored_file_mismatch",
        )

    # ── Preconditions 1 to 8 ──

    def before_the_lock(self, release, resolutions):
        """Preconditions 1 to 8, in order; CommitResult when the release is
        committed already (6)."""
        if not typed_graph_enabled():
            self._refuse(
                1,
                "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
                "release is committed",
            )
        if not _active_staff(self.user):
            self._refuse(
                2, f"The run is triggered by an active staff account, not {self.user!r}"
            )
        self._check_registry()
        self.release = self._release_row(release)
        if resolutions is not None:
            try:
                self.loaded = _resolutions(resolutions)
            except DryRunError as error:
                raise CommitError(5, str(error)) from None
        found = self._committed_already()
        if found is not None:
            return found
        try:
            self.data, self.manifest_file = _release_files(
                self.release, open_commit_check=False
            )
        except DryRunError as error:
            raise CommitError(7, str(error)) from None
        self._check_approval()
        return None

    def _check_registry(self):
        """Precondition 3: the database's registry version is the code's."""
        state = registry_state()
        if state.database != state.code:
            self._refuse(
                3,
                f"The database's registry is at version {state.database or 'none'}, "
                f"and this code's at {state.code}",
                "run graph_sync_registry first",
            )

    def _release_row(self, release) -> DatasetRelease:
        """Precondition 4: the release as stored, which is not withdrawn."""
        pk = release.pk if isinstance(release, DatasetRelease) else release
        row = None
        if not isinstance(pk, bool) and isinstance(pk, int) and storable([pk]):
            row = DatasetRelease.objects.filter(pk=pk).first()
        if row is None:
            self._refuse(4, f"Unknown release {release!r}")
        self._check_not_withdrawn(row)
        return row

    def _check_not_withdrawn(self, release):
        if release.status == ReleaseStatus.WITHDRAWN:
            self._refuse(
                4,
                f"Release {release.pk} is withdrawn, and a withdrawal is final: it is "
                "never committed",
            )

    def _committed_already(self):
        """Precondition 6, for a release that is not a draft: CommitResult
        when its latest commit run completed with these options; refused
        otherwise."""
        release = self.release
        if release.status == ReleaseStatus.DRAFT:
            return None
        latest = (
            ImportRun.objects.filter(release=release, mode=ImportMode.COMMIT)
            .order_by("-pk")
            .first()
        )
        if latest is not None and latest.state == ImportState.COMPLETED:
            if self._same_options(latest.parameters):
                latest.release = release
                return CommitResult(latest, ALREADY_COMMITTED)
            if release.status == ReleaseStatus.STAGED:
                parameters = latest.parameters or {}
                self._refuse(
                    6,
                    f"Release {release.pk} was committed by run {latest.pk} "
                    f"{_with(parameters.get('create_entities'))} --create-entities "
                    f"and {'its' if _sha256(parameters) else 'no'} resolution file",
                    "discard it with graph_discard_release to commit it again with "
                    "other options",
                )
        if release.status != ReleaseStatus.STAGED:
            self._refuse(
                6,
                f"Release {release.pk} is {release.status}, and a published release "
                "is never committed again",
                "a correction is a new release that supersedes it",
            )
        self._refuse(
            6,
            f"Release {release.pk} is staged without a completed commit run",
            "discard it with graph_discard_release",
        )

    def _same_options(self, parameters) -> bool:
        """The run's parameters state these options: --create-entities and
        the resolution file's SHA-256 ("" without one)."""
        parameters = parameters or {}
        return (
            bool(parameters.get("create_entities")) == self.create_entities
            and _sha256(parameters) == self.loaded.sha256
        )

    def _check_approval(self):
        """Precondition 8: a release that is not synthetic has an effective
        internal approval."""
        release = self.release
        if not release.is_synthetic and not is_effective(release, INTERNAL):
            self._refuse(
                8,
                f"Release {release.pk} has no effective internal approval",
                "record one with graph_approve",
            )

    # ── Preconditions 9 to 12 ──

    def with_the_lock(self, held):
        """Precondition 9, the run lock, entered into `held`; then the runs
        left running marked failed, 4 and 6 again, and 10 to 12.
        CommitResult when the release is committed already (6)."""
        pk = self.release.pk
        try:
            held.enter_context(release_run_lock(pk))
        except RunLocked as error:
            raise CommitError(9, _run_lock_refusal(error)) from None
        self.dead = end_dead_runs(pk, found_by=f"a commit of release {pk}")
        # A commit that finished meanwhile staged it.
        self.release = self._release_row(pk)
        found = self._committed_already()
        if found is not None:
            return found
        self.dry = self._latest_dry_run()
        self.open = self._open_run()
        self.adapter = self._code_adapter()
        if self.open is not None:
            self._check_open_run()
        self._check_adapter()
        self._check_dry_run()
        return None

    def _latest_dry_run(self):
        """The release's dry run with the highest pk (every run's insert
        locks the release first, so pk order is insert order), with its
        verdict and kind read from its report, never the report itself."""
        return (
            ImportRun.objects.filter(release=self.release, mode=ImportMode.DRY_RUN)
            .defer("report")
            .annotate(
                verdict=KT("report__verdict"), report_kind=KT("report__adapter__kind")
            )
            .order_by("-pk")
            .first()
        )

    def _open_run(self):
        """The release's open commit run: at most one
        (graph_run_one_open_commit)."""
        return (
            ImportRun.objects.filter(
                release=self.release, mode=ImportMode.COMMIT, state__in=OPEN_STATES
            )
            .order_by("pk")
            .first()
        )

    def _code_adapter(self):
        """The code's adapter for the release (precondition 11)."""
        try:
            return get_adapter(self.release.adapter)
        except (UnknownAdapter, AdapterSpecError) as error:
            self._refuse(
                11, f"Release {self.release.pk}'s adapter: {_clause(str(error))}"
            )

    def _created_entities(self) -> bool:
        return Entity.objects.filter(created_by_release=self.release).exists()

    def _check_open_run(self):
        """Precondition 10: the open commit run decides first. A run that is
        running is never resumed where nothing tells whether its process is
        alive; one that no rerun can resume is refused with the discard; one
        started with other options is refused with them."""
        release, run, spec = self.release, self.open, self.adapter.spec
        pk = release.pk
        if run.state == ImportState.RUNNING and not runs_are_locked():
            self._refuse_as_is(
                10,
                f"Release {pk} has a commit run {run.pk} in state running, and this "
                "database cannot tell whether its process is alive (the run lock "
                "needs PostgreSQL). If no process runs it, discard it with "
                "graph_discard_release.",
            )
        parameters = run.parameters or {}
        differs, register, new_version = [], False, False
        if (spec.version, spec.mapping_version) != (
            run.adapter_version,
            run.mapping_version,
        ):
            differs.append(
                f"this code carries {spec.name} {spec.version} (mapping "
                f"{spec.mapping_version}), and the run reads with "
                f"{run.adapter_version} (mapping {run.mapping_version})"
            )
            register = True
        if spec.kind != release.kind:
            differs.append(
                f"this code's kind is {spec.kind}, and the release's {release.kind}"
            )
            if self._created_entities():
                new_version = True
            else:
                register = True
        database = registry_state().database
        if database != parameters.get("registry_version"):
            differs.append(
                f"the database's registry is at version {database or 'none'}, and the "
                f"run's at {parameters.get('registry_version') or 'none'}"
            )
        dry = self.dry.pk if self.dry is not None else None
        if dry != parameters.get("dry_run"):
            differs.append(
                f"the release's latest dry run is {dry or 'none'}, and the run's "
                f"{parameters.get('dry_run') or 'none'}"
            )
        if differs:
            if new_version:
                then = (
                    "register its files as a new version, since a release keeps its "
                    "kind once it has created entities"
                )
            elif register:
                then = (
                    "register it again with graph_register_release, dry-run it and "
                    "commit it"
                )
            else:
                then = "dry-run it and commit it"
            self._refuse_as_is(
                10,
                f"Release {pk} has an open commit run {run.pk} that cannot be resumed "
                f"under this code ({'; '.join(differs)}): discard it with "
                "graph_discard_release (the evidence its batches recorded is deleted; "
                f"entities and relationships stay), then {then}.",
            )
        if not self._same_options(parameters):
            self._refuse_as_is(
                10,
                f"Release {pk} has an open commit run {run.pk}, started from dry run "
                f"{parameters.get('dry_run')} {_options(parameters)}: run the same "
                "command with those options to resume it, or discard it with "
                "graph_discard_release, then dry-run it again.",
            )

    def _check_adapter(self):
        """Precondition 11: the code's adapter has the release's version,
        mapping version and kind."""
        release, spec = self.release, self.adapter.spec
        if (spec.version, spec.mapping_version, spec.kind) == (
            release.adapter_version,
            release.mapping_version,
            release.kind,
        ):
            return
        cause = (
            f"Release {release.pk} was registered with {release.adapter} "
            f"{release.adapter_version} (mapping {release.mapping_version}, kind "
            f"{release.kind}), and this code carries {spec.version} (mapping "
            f"{spec.mapping_version}, kind {spec.kind})"
        )
        if spec.kind != release.kind and self._created_entities():
            advice = (
                "register its files as a new version, since a release keeps its "
                "kind once it has created entities"
            )
        else:
            advice = (
                "register it again with graph_register_release, then dry-run it and "
                "commit it"
            )
        self._refuse(11, cause, advice)

    def _check_dry_run(self):
        """Precondition 12: the release's latest dry run is one a commit
        trusts."""
        release, dry = self.release, self.dry
        advice = (
            "run graph_import --dry-run again, with the options you will commit with"
        )
        if dry is None:
            self._refuse(
                12,
                f"Release {release.pk} has no dry run",
                "run graph_import --dry-run first, with the options you will commit "
                "with",
            )

        def stale(what):
            self._refuse(
                12, f"Release {release.pk}'s latest dry run, {dry.pk}, {what}", advice
            )

        if dry.state != ImportState.COMPLETED:
            stale(f"is {dry.state}, not completed")
        if dry.verdict == REFUSED:
            stale("was refused")
        if dry.verdict != OK:
            stale(f"has the verdict {dry.verdict or 'none'}, not {OK}")
        ran = (dry.adapter, dry.adapter_version, dry.mapping_version, dry.report_kind)
        reads = (
            release.adapter,
            release.adapter_version,
            release.mapping_version,
            release.kind,
        )
        if ran != reads:
            stale(
                f"read it with {ran[0]} {ran[1]} (mapping {ran[2]}, kind {ran[3]}), "
                f"and the release reads with {reads[0]} {reads[1]} (mapping "
                f"{reads[2]}, kind {reads[3]})"
            )
        parameters = dry.parameters or {}
        database = registry_state().database
        if parameters.get("registry_version") != database:
            stale(
                f"ran under registry version {parameters.get('registry_version')}, "
                f"and the database's is at {database or 'none'}"
            )
        later = self._closed_commit_after(dry.pk)
        if later is not None:
            stale(f"came before commit run {later.pk}, which is {later.state}")
        if bool(parameters.get("create_entities")) != self.create_entities:
            stale(
                f"ran {_with(parameters.get('create_entities'))} --create-entities, "
                f"and this commit runs {_with(self.create_entities)} it"
            )
        if _sha256(parameters) != self.loaded.sha256:
            stale(
                f"ran with {_resolution_file(_sha256(parameters))}, and this commit "
                f"has {_resolution_file(self.loaded.sha256)}"
            )

    def _closed_commit_after(self, pk):
        """The latest commit run of the release that is not open
        (completed or abandoned: something was committed or discarded)
        after run `pk`, if any."""
        return (
            ImportRun.objects.filter(
                release=self.release,
                mode=ImportMode.COMMIT,
                state__in=CLOSED_STATES,
                pk__gt=pk,
            )
            .order_by("-pk")
            .first()
        )

    # ── The attempt ──

    def attempt(self) -> CommitResult:
        """The planning (13 to 15), then the run started or resumed, every
        batch written and the release staged. From the run's start on, any
        exception marks the run failed while it can (_failed)."""
        try:
            stream = open_stored(self.data)
        except StorageError as error:
            self._refuse_file(Issue("stored_file_mismatch", str(error)), 1)
        with stream:
            self._plan(stream, self._manifest())
            found = self._start()
            if found is not None:
                return found
            try:
                self._write(stream)
                self._finish()
            except BaseException as error:
                raised = self._failed(error)
                if raised is error:
                    raise
                raise raised from error
        return CommitResult(self.run, RESUMED if self.open is not None else COMMITTED)

    def _manifest(self):
        if self.manifest_file is None:
            return EMPTY_MANIFEST
        found = _Findings()
        manifest = _manifest(self.manifest_file, self.release, found)
        if manifest is None:
            self._refuse_file(found.first, found.count)
        return manifest

    # ── The planning: preconditions 13 to 15 ──

    def _plan(self, stream, manifest):
        """One or two read-only readings of the file, as the dry run reads
        it, and their findings; the mapping is kept for the writing
        reading, never restarted after the last planning reading."""
        release = self.release
        self.ctx = RunContext(
            load_registry_snapshot(),
            release.is_synthetic,
            manifest,
            release.pk,
            release.dataset_id,
        )
        self.mapping_run = MappingRun(
            adapter=self.adapter,
            ctx=self.ctx,
            release=_Release(
                release.pk,
                release.dataset_id,
                release.is_synthetic,
                release.supersedes_id,
            ),
            resolutions=self.loaded,
            create_entities=self.create_entities,
        )
        planning = self._reading(stream)
        self.readings = 1
        if not planning.ended and self.mapping_run.read_again:
            # As the dry run: the second reading finds nothing new, and only
            # its findings count.
            stream.seek(0)
            self.mapping_run.restart()
            planning = self._reading(stream)
            self.readings = 2
        self.planning = planning
        self._check_planning(planning)

    def _reading(self, stream):
        """One planning reading. A file error, of the adapter or of the
        mapping (too many identifiers), and a stored record that this file
        does not have on its line, end it at once."""
        planning = _Planning()
        records = self.adapter.iter_records(stream, self.ctx)
        try:
            for batch in _batches(records):
                for plan in plan_batch(batch, run_ctx=self.mapping_run):
                    planning.add(plan)
        except FileError as error:
            for issue in error.issues:
                planning.found.add(issue)
            planning.ended = True
        except RunStopped as error:
            # "Record 'r1' is stored from line ...", as a clause.
            changed = _clause(str(error))
            planning.changed = changed[:1].lower() + changed[1:]
            planning.ended = True
        return planning

    def _check_planning(self, planning):
        """Preconditions 13, 14 and 15, on the last planning reading."""
        pk, dry = self.release.pk, self.dry.pk
        if planning.found.count:
            self._refuse_file(planning.found.first, planning.found.count)
        records = f"Release {pk}'s records are not those of its latest dry run {dry}"
        if planning.changed is not None:
            self._refuse(14, f"{records} ({planning.changed})", "dry-run it again")
        if planning.missing is not None:
            key, line = planning.missing
            self._refuse(
                14,
                f"{records} (record {quoted(key)} of line {line} is not stored)",
                "dry-run it again",
            )
        held = SourceRecord.objects.filter(release_id=pk).count()
        if held != planning.counted:
            self._refuse(
                14,
                f"{records} (it holds {_counted(held, 'record')}, and the file gives "
                f"{planning.counted})",
                "dry-run it again",
            )
        if planning.differing:
            n = planning.differing
            self._refuse(
                15,
                f"{_counted(n, 'record')} of release {pk} "
                f"{'maps' if n == 1 else 'map'} differently from its latest dry run "
                f"{dry}, for example " + "; ".join(planning.examples),
                "Dry-run it again and review its report",
                joined=". ",
            )

    # ── Starting or resuming the run ──

    def _start(self):
        """The run created, or the open one resumed, in one short
        transaction that checks 1, 3, 4, 6, 8 and 10 to 12 again as they are
        now: a change raises its CommitError, and nothing is written.
        CommitResult when a commit that finished meanwhile staged the
        release (6)."""
        pk = self.release.pk
        with transaction.atomic():
            release = DatasetRelease.objects.select_for_update().filter(pk=pk).first()
            if release is None:
                self._refuse(4, f"Unknown release {pk}")
            self.release = release
            if not typed_graph_enabled():
                self._refuse(
                    1,
                    "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so "
                    "no release is committed",
                )
            self._check_registry()
            self._check_not_withdrawn(release)
            found = self._committed_already()
            if found is not None:
                return found
            self._check_approval()
            self._check_still_open()
            self._check_adapter()
            latest = (
                ImportRun.objects.filter(release=release, mode=ImportMode.DRY_RUN)
                .order_by("-pk")
                .values_list("pk", flat=True)
                .first()
            )
            if latest != self.dry.pk:
                self._refuse(
                    12,
                    f"Release {pk}'s latest dry run is {latest} now, not {self.dry.pk}",
                    "run graph_import --dry-run again, with the options you will "
                    "commit with",
                )
            later = self._closed_commit_after(self.dry.pk)
            if later is not None:
                self._refuse(
                    12,
                    f"Release {pk}'s latest dry run, {self.dry.pk}, came before commit "
                    f"run {later.pk}, which is {later.state}",
                    "run graph_import --dry-run again, with the options you will "
                    "commit with",
                )
            if self.open is None:
                self._create()
            else:
                self._resume()
        self.checkpoint_line = self.run.checkpoint.get("line", 0)
        return None

    def _check_still_open(self):
        """Precondition 10 again: no open commit run for a new run; for a
        resume, the open run is still the one step 10 read, in its state and
        with its parameters."""
        release = self.release
        current = (
            ImportRun.objects.filter(
                release=release, mode=ImportMode.COMMIT, state__in=OPEN_STATES
            )
            .order_by("pk")
            .first()
        )
        if self.open is None:
            if current is not None:
                self._refuse_as_is(
                    10,
                    f"Release {release.pk} has an open commit run {current.pk} now: "
                    "another commit of it started meanwhile, and one runs at a time.",
                )
            return
        if current is None or (current.pk, current.state, current.parameters) != (
            self.open.pk,
            self.open.state,
            self.open.parameters,
        ):
            now = "closed" if current is None or current.pk != self.open.pk else ""
            self._refuse_as_is(
                10,
                f"Release {release.pk}'s commit run {self.open.pk} changed while this "
                f"commit read the file: it is {now or current.state} now, not "
                f"{self.open.state}.",
            )

    def _create(self):
        report = _Report(None)
        self._planned_into(report)
        run = ImportRun(
            release=self.release,
            file=self.data,
            adapter=self.release.adapter,
            adapter_version=self.release.adapter_version,
            mapping_version=self.release.mapping_version,
            mode=ImportMode.COMMIT,
            state=ImportState.RUNNING,
            parameters={
                "create_entities": self.create_entities,
                "resolutions": self.loaded.as_json(),
                "registry_version": registry_state().database,
                "dry_run": self.dry.pk,
            },
            report=report.as_json(),
            triggered_by=self.user,
        )
        try:
            with transaction.atomic():
                run.save()
        except IntegrityError:
            # graph_run_one_open_commit, where no run lock serializes
            # commits.
            self._refuse_as_is(
                10,
                f"Release {self.release.pk} has an open commit run now: another commit "
                "of it started meanwhile, and one runs at a time.",
            )
        self.run, self.report = run, report

    def _resume(self):
        run = ImportRun.objects.select_for_update().get(pk=self.open.pk)
        report = _Report(run.report)
        report.resumed.append(
            {
                "at": timezone.now().isoformat(),
                "by": self.user.get_username(),
                "error": run.error,
                "line": (run.checkpoint or {}).get("line", 0),
            }
        )
        self._planned_into(report)
        run.state = ImportState.RUNNING
        run.error = ""
        run.finished_at = None
        run.report = report.as_json()
        run.save(update_fields=["state", "error", "report", "finished_at"])
        self.run, self.report = run, report

    def _planned_into(self, report):
        """What the report says of this attempt's start and planning."""
        release, spec = self.release, self.adapter.spec
        state = registry_state()
        report.header = {
            "dry_run": self.dry.pk,
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
            "create_entities": self.create_entities,
            "resolutions": self.loaded.sha256,
            "planning_readings": self.readings,
        }
        report.records = {
            status: self.planning.statuses.get(status, 0)
            for status in RecordStatus.values
        }

    # ── Writing ──

    def _write(self, stream):
        """The writing reading: the file read a last time with the same
        mapping, each batch after the checkpoint written in one transaction.
        A service's or a record rule's refusal, or a database error, stops
        the batch, which rolls back whole."""
        stream.seek(0)
        batches = _batches(self.adapter.iter_records(stream, self.ctx))
        while True:
            try:
                batch = next(batches, None)
            except FileError as error:
                issue = error.issues[0] if error.issues else None
                raise RunStopped(
                    f"The file of release {self.release.pk} no longer reads as it did "
                    "when it was planned"
                    + (f": {_issue_text(issue)}." if issue is not None else ".")
                ) from error
            if batch is None:
                return
            first, last = batch[0].line_number, batch[-1].line_number
            if last <= self.checkpoint_line:
                # Committed by an earlier attempt.
                continue
            try:
                self._write_batch(batch, last)
            except RunStopped:
                raise
            except REFUSALS as error:
                raise RunStopped(f"{_lines(first, last)}: {error}") from error
            except (DatabaseError, InterfaceError) as error:
                raise RunStopped(
                    f"{_lines(first, last)}: the database stopped the batch: "
                    f"{_sentence(_first_line(error))}"
                ) from error

    def _write_batch(self, batch, last):
        """One batch: its plans (check=False: no G3 check and no label
        chosen), then one transaction, in the lock order."""
        plans = plan_batch(batch, run_ctx=self.mapping_run, check=False)
        mapped = [
            plan
            for plan in plans
            if not plan.unstored and plan.stored_status == RecordStatus.MAPPED
        ]
        for plan in mapped:
            if not plan.would_map:
                raise RunStopped(
                    f"Record {quoted(plan.record.record_key)} of line "
                    f"{plan.record.line_number} no longer maps as it was planned."
                )
        with transaction.atomic():
            release = locked_release(self.release.pk, RunStopped)
            self._still(release, self._locked_state())
            creations = self._creations(mapped)
            self._share_locks(creations, mapped)
            self._entities(creations)
            self._publications(mapped)
            self._terms(mapped)
            self._relationships(mapped)
            self._evidence(mapped)
            self.report.batches += 1
            self.run.checkpoint = {
                "version": CHECKPOINT_VERSION,
                "line": last,
                "batches": self.report.batches,
            }
            self.run.report = self.report.as_json()
            self.run.save(update_fields=["checkpoint", "report"])

    def _locked_state(self):
        """The run's state, its row locked (after the release's)."""
        return (
            ImportRun.objects.select_for_update()
            .filter(pk=self.run.pk)
            .values_list("state", flat=True)
            .first()
        )

    def _still(self, release, state):
        """What a batch, and the finishing, check again under the release's
        and the run's locks; RunStopped stating the cause."""
        pk, run = release.pk, self.run.pk
        if not typed_graph_enabled():
            raise RunStopped(
                "The typed graph is off on this portal now (TYPED_GRAPH_ENABLED)."
            )
        if release.status != ReleaseStatus.DRAFT:
            raise RunStopped(
                f"Release {pk} is {release.status} now: a commit writes to a draft "
                "only."
            )
        if state == ImportState.ABANDONED:
            raise RunStopped(
                f"Run {run} of release {pk} is abandoned now: a discard gave it up."
            )
        if state != ImportState.RUNNING:
            raise RunStopped(
                f"Run {run} of release {pk} is {state or 'gone'} now, not running: "
                "another process changed it."
            )
        check_adapter_unchanged(release, self.run)
        if release.kind != self.adapter.spec.kind:
            raise RunStopped(
                f"Release {pk} is of kind {release.kind} now, and this run reads it as "
                f"kind {self.adapter.spec.kind}: it was registered again."
            )
        if not release.is_synthetic and not is_effective(release, INTERNAL):
            raise RunStopped(f"Release {pk} has no effective internal approval now.")

    def _registry_still(self):
        """The registry version is the code's and the run's, read after the
        batch's shared registry locks, so a sync that committed while the
        batch waited is seen, and one still running waits for the batch."""
        database = registry_state().database
        if database != REGISTRY_VERSION or database != self.run.parameters.get(
            "registry_version"
        ):
            raise RunStopped(
                f"The database's registry is at version {database or 'none'} now, and "
                f"this code's at {REGISTRY_VERSION}."
            )

    def _creations(self, mapped) -> dict:
        """{identity key: Create} of the entities the batch's mapped records
        name that this attempt has not created yet, as first stated."""
        creations = {}
        for plan in mapped:
            for endpoint in plan.endpoints:
                target = endpoint.target
                if (
                    isinstance(target, Create)
                    and target.identity_key not in self.mapping_run.entity_ids
                ):
                    creations.setdefault(target.identity_key, target)
        return creations

    def _share_locks(self, creations, mapped):
        """Every registry row the batch's writes are checked against, held
        in shared mode before any write, in the sync's order (entity types,
        namespaces, relationship types, score types); later calls hold them
        again and wait on nothing new. Then the registry version."""
        claims = [claim for plan in mapped for claim in plan.claims]
        share_lock(EntityType, None, [c.entity_type for c in creations.values()])
        share_lock(IdentifierNamespace, None, [c.namespace for c in creations.values()])
        share_lock(
            RelationshipType, None, [claim.relationship_type for claim in claims]
        )
        share_lock(
            ScoreType,
            None,
            [score.score_type for claim in claims for score in claim.scores],
        )
        self._registry_still()

    def _entities(self, creations):
        """The entities the batch creates, in lock_order, each with the
        label and value the planning chose (the dry run's report showed
        them). One counts as created by this run when no entity had its key
        before the step and it names this release as its creator."""
        if not creations:
            return
        existing = set(
            Entity.objects.filter(identity_key__in=sorted(creations)).values_list(
                "identity_key", flat=True
            )
        )
        pk = self.release.pk
        for key in lock_order(creations):
            create = creations[key]
            planned = self.mapping_run.planned[key]
            entity = create_entity(
                create.entity_type,
                planned.chosen,
                create.taxon,
                create.namespace,
                planned.written or create.value,
                release=pk,
            )
            self.mapping_run.entity_ids[key] = entity.pk
            if key not in existing and entity.created_by_release_id == pk:
                self.report.created["entities"] += 1

    def _publications(self, mapped):
        """Every paper the batch's mapped records cite, stored or not, in
        lock_order: each is found or created, and locked, so no batch takes
        a paper's foreign-key lock only at COMMIT, behind another batch.
        A DOI is written as the first mapped record citing it states it. A
        paper local to the dataset that keeps another title or year than
        the manifest states (it was stored before, whenever: G2 fills
        blanks only) goes into paper_differences."""
        papers = {}
        for plan in mapped:
            for paper in plan.papers:
                identity = paper_identity(paper.stated)
                if identity is not None and identity not in papers:
                    papers[identity] = (paper.stated, plan.record.line_number)
        declared = self.ctx.manifest.publications
        for identity in lock_order(papers):
            kind, value = identity
            stated, line = papers[identity]
            if kind == "pmid":
                result = get_or_create_publication(pmid=value)
            elif kind == "doi":
                result = get_or_create_publication(doi=stated["doi"])
            else:
                paper = declared[value]
                result = get_or_create_publication(
                    local_dataset=self.release.dataset_id,
                    local_key=value,
                    title=paper.title,
                    year=paper.year,
                    is_synthetic=paper.is_synthetic,
                )
                stored = result.publication
                # As the dry run compares them (MappingRun.resolve_papers).
                if ((stored.title or "").strip(), stored.year) != (
                    paper.title,
                    paper.year,
                ):
                    self.report.paper_differences.add(
                        {
                            "publication": stored.pk,
                            "key": value,
                            "stored_title": stored.title,
                            "stored_year": stored.year,
                            "manifest_title": paper.title,
                            "manifest_year": paper.year,
                            "line": line,
                        }
                    )
            publication = result.publication
            self.mapping_run.paper_ids[identity] = publication.pk
            if result.created:
                self.report.created["publications"] += 1
            for conflict in result.conflicts:
                self.report.publication_conflicts.add(
                    {
                        "publication": publication.pk,
                        "field": conflict.field,
                        "value": conflict.value,
                        "current": conflict.current,
                        "holder": conflict.holder_id,
                        "line": line,
                    }
                )

    def _terms(self, mapped):
        """The terms the batch's mapped records name that were not stored
        when the planning read them, with the manifest's labels, in
        lock_order. Each term they name that keeps another label than the
        manifest states goes into term_labels: one the planning found
        stored with another label, whenever it was stored (before the dry
        run, or since), and, of those this commit records, a term the
        crosswalk pins and one another release stored since the planning."""
        terms, kept = {}, {}
        declared, stored = self.ctx.manifest.terms, self.mapping_run.terms
        for plan in mapped:
            line = plan.record.line_number
            for curie, label in plan.terms:
                if curie not in self.terms_done and curie not in terms:
                    terms[curie] = (label, line)
            for claim in plan.claims:
                for curie in _curies(claim):
                    # The label the planning read: a stored term's never
                    # changes.
                    label, held = declared.get(curie), stored.get(curie)
                    if label is not None and held is not None and held != label:
                        kept.setdefault(curie, (label, held, line))
        for curie in sorted(kept):
            label, held, line = kept[curie]
            self.report.term_labels.add(
                {"curie": curie, "stated": label, "kept": held, "line": line}
            )
        for curie in lock_order(terms):
            label, line = terms[curie]
            result = get_or_create_term(curie, label, import_run=self.run.pk)
            self.terms_done.add(curie)
            if result.created:
                self.report.created["terms"] += 1
            if result.label_conflict is not None:
                self.report.term_labels.add(
                    {
                        "curie": curie,
                        "stated": label,
                        "kept": result.term.label,
                        "line": line,
                    }
                )

    def _relationships(self, mapped):
        """G4's G3 check again, now that the batch's entities and papers
        exist (entity_ids, paper_ids): it gives each claim's canonical
        relationship. A record it refuses means the database changed since
        the planning. Then one create_relationships call, which orders the
        batch's missing relationships itself."""
        if not mapped:
            return
        _check(mapped, self.mapping_run)
        canonicals = []
        for plan in mapped:
            record = plan.record
            if rejects(plan.issues):
                issue = next(i for i in plan.issues if i.severity == ERROR)
                raise RunStopped(
                    f"Record {quoted(record.record_key)} of line {record.line_number} "
                    "is refused now, since the database changed after it was "
                    f"planned: {issue.message}"
                )
            for canonical, _ in plan.relationships:
                if canonical is None:
                    raise RunStopped(
                        f"Record {quoted(record.record_key)} of line "
                        f"{record.line_number} names an entity this commit has not "
                        "written."
                    )
                canonicals.append(canonical)
        if canonicals:
            results = create_relationships(
                list(dict.fromkeys(canonicals)), import_run=self.run.pk
            )
            self.report.created["relationships"] += sum(
                1 for result in results.values() if result.created
            )

    def _evidence(self, mapped):
        """Each mapped record's evidence, in line order: a record already
        recorded is a no-op. A relationship record_claims creates is one the
        batch's create_relationships missed (late_relationships)."""
        for plan in mapped:
            result = record_claims(
                plan.stored_id,
                checked_claims(plan, self.mapping_run),
                import_run=self.run.pk,
            )
            created = sum(1 for recorded in result.assertions if recorded.created)
            if created:
                self.report.recorded += 1
                self.report.assertions += created
            else:
                self.report.already_recorded += 1
            self.report.late_relationships += sum(
                1 for recorded in result.assertions if recorded.relationship_created
            )

    # ── Finishing ──

    def _finish(self):
        """One transaction: the checks of a batch, every mapped record holds
        evidence, the statistics recounted, the release staged and the run
        completed."""
        pk = self.release.pk
        try:
            with transaction.atomic():
                release = locked_release(pk, RunStopped)
                self._still(release, self._locked_state())
                self._registry_still()
                unrecorded = (
                    SourceRecord.objects.filter(
                        release_id=pk, status=RecordStatus.MAPPED
                    )
                    .filter(
                        ~Exists(
                            EvidenceAssertion.objects.filter(
                                source_record_id=OuterRef("pk")
                            )
                        )
                    )
                    .count()
                )
                if unrecorded:
                    raise RunStopped(
                        f"{_counted(unrecorded, 'mapped record')} of release {pk} "
                        f"{'holds' if unrecorded == 1 else 'hold'} no evidence."
                    )
                release.stats = release_stats(release)
                release.status = ReleaseStatus.STAGED
                release.save(update_fields=["status", "stats", "updated_at"])
                self.run.state = ImportState.COMPLETED
                self.run.report = self.report.as_json()
                self.run.finished_at = timezone.now()
                self.run.save(update_fields=["state", "report", "finished_at"])
        except RunStopped:
            raise
        except REFUSALS as error:
            raise RunStopped(f"Staging release {pk}: {error}") from error
        except (DatabaseError, InterfaceError) as error:
            raise RunStopped(
                f"Staging release {pk}: the database stopped it: "
                f"{_sentence(_first_line(error))}"
            ) from error
        self.run.release = release

    # ── Failure ──

    def _failed(self, error):
        """The run reloaded under its lock and, only while it is still
        running, marked failed with the error. Best effort: a connection
        that lost its session (and with it the run lock), an aborted
        transaction or a lost connection leave the run running for the next
        commit or discard to mark, and the error raised says so in a note.
        Returns the exception to raise: a RunStopped becomes
        CommitStopped."""
        pk, run_pk = self.release.pk, self.run.pk
        marked = abandoned = False
        problem = None
        try:
            if run_lock_lost(pk):
                problem = "the connection's session ended, and the run lock with it"
            else:
                with transaction.atomic():
                    # The release before its run, as every writer of a run.
                    DatasetRelease.objects.select_for_update().filter(pk=pk).first()
                    run = (
                        ImportRun.objects.select_for_update().filter(pk=run_pk).first()
                    )
                    found = run.state if run is not None else None
                    if found == ImportState.RUNNING:
                        run.state = ImportState.FAILED
                        run.error = (
                            str(error) if isinstance(error, RunStopped) else repr(error)
                        )
                        run.finished_at = timezone.now()
                        run.save(update_fields=["state", "error", "finished_at"])
                # Once the marking committed: a run no longer running (a
                # discard gave it up) is left as it is.
                marked = found == ImportState.RUNNING
                abandoned = found == ImportState.ABANDONED
        except (DatabaseError, InterfaceError) as failure:
            problem = f"{type(failure).__name__}: {_first_line(failure)}"
        raised = error
        if isinstance(error, RunStopped):
            raised = CommitStopped(
                str(error), run_id=run_pk, marked=marked, abandoned=abandoned
            )
        if problem is not None:
            if runs_are_locked():
                stays = (
                    "it stays running until the release's next commit or discard "
                    "marks it"
                )
            else:
                # end_dead_runs marks nothing there, and a commit never
                # resumes a running run (10.1).
                stays = (
                    "it stays running, and on this database, which has no run lock, "
                    "only a discard gives it up"
                )
            raised.add_note(
                f"Run {run_pk} could not be marked failed ({problem}): {stays}."
            )
        return raised


# ── What a planning reading finds ───────────────────────────────────────────


class _Findings:
    """The issues that refuse the file (precondition 13): its first, and
    their count. It collects as the dry run's report does (file_error), so
    dry_run._manifest takes it."""

    def __init__(self):
        self.first = None
        self.count = 0

    def add(self, issue):
        if self.first is None:
            self.first = issue
        self.count += 1

    file_error = add


@dataclass
class _Planning:
    """One planning reading: the file's findings (13), the records it gives
    that are not stored as the dry run stored them (14), and those that map
    to another status than the stored one (15)."""

    found: _Findings = field(default_factory=_Findings)
    # A file error, or a stored record this file does not have, ended it.
    ended: bool = False
    # _read's RunStopped: a stored record's line or digest differs.
    changed: str | None = None
    # (record key, line) of the first record the file gives that is not
    # stored.
    missing: tuple | None = None
    # The records the file gives (its lines that are not unstored), and
    # their planned statuses.
    counted: int = 0
    statuses: Counter = field(default_factory=Counter)
    differing: int = 0
    examples: list = field(default_factory=list)

    def add(self, plan):
        for issue in plan.issues:
            # Unstored lines too: a synthetic release is checked on every
            # row whose cells could be read.
            if issue.code == "synthetic_violation":
                self.found.add(issue)
        if plan.unstored:
            return
        record = plan.record
        self.counted += 1
        self.statuses[plan.status] += 1
        if plan.stored_id is None:
            if self.missing is None:
                self.missing = (record.record_key, record.line_number)
            return
        if plan.status == plan.stored_status:
            return
        self.differing += 1
        if len(self.examples) < EXAMPLES:
            issue = _first_issue(plan)
            self.examples.append(
                f"record {quoted(record.record_key)} of line {record.line_number}: "
                f"{plan.stored_status} then, {plan.status} now"
                + (f" ({_clause(issue.message)})" if issue is not None else "")
            )


def _first_issue(plan):
    """The issue that says why a record maps as it does now: its first
    error, else its first issue."""
    for issue in plan.issues:
        if issue.severity == ERROR:
            return issue
    return plan.issues[0] if plan.issues else None


def _batches(records):
    """The records in the dry run's batches: at most limits.BATCH_ROWS rows
    and limits.BATCH_BYTES bytes of them, a line too long to keep weighing
    nothing."""
    batch, size = [], 0
    for record in records:
        weight = record.size if record.fields is not None else 0
        if batch and (
            len(batch) >= limits.BATCH_ROWS or size + weight > limits.BATCH_BYTES
        ):
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += weight
    if batch:
        yield batch


# ── The report ──────────────────────────────────────────────────────────────


class _Listed:
    """One of the report's lists: its first limits.REPORT_LIST_ENTRIES
    entries and its total, as G4's lists keep them. An entry whose key the
    list holds already is not added again, so a resume repeats none.

    A later batch meets a key again when it names a term again (term_labels)
    or cites a paper again (paper_differences, publication_conflicts). With
    keep_keys, a truncated list also keeps every key it counted ("keys",
    plain strings: a CURIE or a paper's key, which the manifest declares),
    so a resume counts none of them again. publication_conflicts keeps no
    keys: a resume rebuilds them from its kept entries, so a conflict
    counted past the cap is counted again when a later batch meets it. No
    canonical_tsv_v1 file states a publication-identifier conflict before
    M1, which keeps every counted conflict's key (IMPLEMENTATION_PLAN.md,
    M1)."""

    def __init__(self, stored, key, *, keep_keys=False):
        stored = stored if isinstance(stored, dict) else {}
        self.key = key
        self.keep_keys = keep_keys
        self.entries = [dict(entry) for entry in stored.get("entries") or []]
        self.total = stored.get("total", len(self.entries))
        self.seen = {key(entry) for entry in self.entries}
        if keep_keys:
            self.seen.update(stored.get("keys") or ())

    def add(self, entry):
        key = self.key(entry)
        if key in self.seen:
            return
        self.seen.add(key)
        self.total += 1
        if len(self.entries) < limits.REPORT_LIST_ENTRIES:
            self.entries.append(entry)

    def as_json(self) -> dict:
        listed = {
            "total": self.total,
            "truncated": len(self.entries) < self.total,
            "entries": [dict(entry) for entry in self.entries],
        }
        if self.keep_keys and listed["truncated"]:
            listed["keys"] = sorted(self.seen)
        return listed


class _Report:
    """A commit run's report (ImportRun.report): the last attempt's start
    and planning (header, records), and what every attempt wrote, added up
    (a resume starts from the stored report)."""

    def __init__(self, stored):
        stored = stored if isinstance(stored, dict) else {}
        counts = stored.get("counts") or {}
        created = counts.get("created") or {}
        self.header = {}
        self.records = {}
        self.batches = counts.get("batches", 0)
        self.recorded = counts.get("recorded", 0)
        self.already_recorded = counts.get("already_recorded", 0)
        self.assertions = counts.get("assertions", 0)
        self.created = {kind: created.get(kind, 0) for kind in CREATED}
        self.late_relationships = counts.get("late_relationships", 0)
        self.term_labels = _Listed(
            stored.get("term_labels"),
            key=lambda entry: entry["curie"],
            keep_keys=True,
        )
        self.publication_conflicts = _Listed(
            stored.get("publication_conflicts"),
            key=lambda entry: (entry["publication"], entry["field"], entry["value"]),
        )
        self.paper_differences = _Listed(
            stored.get("paper_differences"),
            key=lambda entry: entry["key"],
            keep_keys=True,
        )
        self.resumed = [dict(entry) for entry in stored.get("resumed") or []]

    def as_json(self) -> dict:
        header = self.header
        return {
            "dry_run": header.get("dry_run"),
            "adapter": header.get("adapter"),
            "registry": header.get("registry"),
            "release": header.get("release"),
            "create_entities": header.get("create_entities"),
            "resolutions": header.get("resolutions"),
            "planning_readings": header.get("planning_readings"),
            "counts": {
                "records": dict(self.records),
                "batches": self.batches,
                "recorded": self.recorded,
                "already_recorded": self.already_recorded,
                "assertions": self.assertions,
                "created": dict(self.created),
                "late_relationships": self.late_relationships,
            },
            "term_labels": self.term_labels.as_json(),
            "publication_conflicts": self.publication_conflicts.as_json(),
            "paper_differences": self.paper_differences.as_json(),
            "resumed": [dict(entry) for entry in self.resumed],
        }


def commit_summary_lines(result) -> list[str]:
    """What graph_import prints of a commit (and the operator page shows):
    the run, what it imported, recorded and created, what graph_check lists
    for review, and the next step. An already committed release is one
    line."""
    run = result.run
    if result.outcome == ALREADY_COMMITTED:
        return [
            f"Release {run.release_id} was committed by run {run.pk}, and is "
            f"{run.release.status}: nothing was written."
        ]
    report = run.report
    counts = report["counts"]
    created = counts["created"]
    resumes = len(report["resumed"])
    after = f" after {_counted(resumes, 'resume')}" if resumes else ""
    # In RecordStatus's order, which a stored report need not keep: JSONB
    # orders keys by length, then bytes.
    records = ", ".join(
        f"{counts['records'].get(status, 0)} {status}" for status in RecordStatus.values
    )
    lines = [
        f"Commit run {run.pk} of release {run.release_id}: completed{after}, and the "
        "release is staged.",
        f"It imported the records of dry run {report['dry_run']}: {records}.",
        f"Recorded {_counted(counts['recorded'], 'record')} "
        f"({counts['already_recorded']} already recorded), with "
        f"{_counted(counts['assertions'], 'assertion')}.",
        f"Created {_counted(created['entities'], 'entity', 'entities')}, "
        f"{_counted(created['relationships'], 'relationship')}, "
        f"{_counted(created['publications'], 'publication')} and "
        f"{_counted(created['terms'], 'term')}.",
    ]
    labels = report["term_labels"]["total"]
    if labels:
        lines.append(
            f"{_counted(labels, 'term')} "
            + (
                "keeps a label other than the manifest's: graph_check lists it."
                if labels == 1
                else "keep a label other than the manifest's: graph_check lists them."
            )
        )
    papers = report["paper_differences"]["total"]
    if papers:
        lines.append(
            f"{_counted(papers, 'paper')} "
            + (
                "keeps a title or year other than the manifest's: graph_check lists "
                "it."
                if papers == 1
                else "keep a title or year other than the manifest's: graph_check "
                "lists them."
            )
        )
    conflicts = report["publication_conflicts"]["total"]
    if conflicts:
        lines.append(
            f"{_counted(conflicts, 'publication identifier')} "
            + (
                "is held by another publication: graph_check lists it."
                if conflicts == 1
                else "are held by other publications: graph_check lists them."
            )
        )
    lines.append(
        "Next: publish it with graph_publish_release, or discard it with "
        "graph_discard_release."
    )
    return lines


# ── Helpers ─────────────────────────────────────────────────────────────────


def _run_lock_refusal(error) -> str:
    """Why the run lock refused a commit (RunLocked.reason), in words."""
    release = error.release_id
    if error.reason == NESTED:
        why = (
            f"This connection already holds the run lock of release {release}: a "
            "commit never runs inside another run of it."
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


def _ending(dead) -> str:
    """How a refusal ends: nothing was written, or the runs end_dead_runs
    marked failed were."""
    if not dead:
        return "Nothing was written."
    if len(dead) == 1:
        return (
            f"Run {dead[0]}, left running by a process that ended, is marked failed; "
            "nothing else was written."
        )
    named = ", ".join(map(str, dead[:-1])) + f" and {dead[-1]}"
    return (
        f"Runs {named}, left running by processes that ended, are marked failed; "
        "nothing else was written."
    )


def _options(parameters) -> str:
    """A run's options, as a refusal names them."""
    return (
        f"{_with(parameters.get('create_entities'))} --create-entities and "
        f"{_resolution_file(_sha256(parameters))}"
    )


def _with(flag) -> str:
    return "with" if flag else "without"


def _resolution_file(sha256) -> str:
    return (
        f"the resolution file of SHA-256 {sha256}" if sha256 else "no resolution file"
    )


def _sha256(parameters) -> str:
    """The SHA-256 of a run's resolution file, "" without one."""
    resolutions = (parameters or {}).get("resolutions") or {}
    return resolutions.get("sha256", "") or ""


def _counted(n, singular, plural=None) -> str:
    """n and the noun, singular or plural as n says."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _lines(first, last) -> str:
    return f"Line {first}" if first == last else f"Lines {first} to {last}"


def _issue_text(issue) -> str:
    """An issue as a refusal quotes it: its code, its line, its message."""
    where = f" at line {issue.line}" if issue.line is not None else ""
    return f"{issue.code}{where}: {_clause(issue.message)}"


def _clause(text) -> str:
    """A sentence as part of another: without its final full stop."""
    text = text.strip()
    return text[:-1] if text.endswith(".") else text


def _sentence(text) -> str:
    """A clause as the end of a sentence: with one final full stop, so the
    advice the command adds after it reads as a sentence of its own."""
    return _clause(text) + "."


def _first_line(error) -> str:
    """The first line of a database error's message."""
    lines = str(error).strip().splitlines()
    return lines[0] if lines else type(error).__name__
