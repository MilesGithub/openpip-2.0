"""Commits at the same time (graph.ingest.commit), on PostgreSQL: each runs
in a thread with its own connection, and the tests use transaction=True so
that the threads see what the others committed.

- A2, a real kill: a commit's backend is ended in its second batch, as a
  killed process's session ends; its run stays running, graph_check lists
  it as left running, and the rerun marks it failed, resumes it after its
  checkpoint and ends with the rows of an uninterrupted commit.
- A3: two releases that share an entity, a pair, a paper and a term commit
  at once; the second waits on the first, and neither fails a batch or
  leaves a duplicate or an orphan row.
- The lock order: two commits that create the same rows of one kind,
  first stated in opposite orders, wait on each other and never deadlock;
  with the order patched out they do, and the failed batch resumes.
- One committer: while a commit runs, a dry run, a second commit and a
  discard of its release are refused by the run lock; once it stopped, a
  dry run is refused only while its commit run is open.

Every wait is bounded (lock_helpers.WAIT_SECONDS, 30 s) and fails the test
when it expires; a paused worker is released in finally, and every thread
closes its connection. No test acts on a release's row from the thread
that must release a worker paused in that release's batch."""

import threading
import time

import pytest
from django.db import connection

from graph.checks import run_checks
from graph.entities import create_entity
from graph.ingest import commit as commit_module
from graph.ingest.commit import (
    COMMITTED,
    RESUMED,
    CommitError,
    CommitStopped,
)
from graph.ingest.dry_run import DryRunError, dry_run
from graph.ingest.runlock import run_lock_holder
from graph.models import (
    Entity,
    EvidenceAssertion,
    ImportMode,
    ImportRun,
    ImportState,
    Publication,
    Relationship,
    SourceRecord,
    Term,
)
from graph.releases import ReleaseError, discard_release
from graph.tests.factories import StaffUserFactory
from graph.tests.ingest_helpers import GENE, PROTEIN, natural_rows, registered, row
from graph.tests.lock_helpers import WAIT_SECONDS, terminate_backend
from graph.tests.test_commit import (
    commit,
    dry,
    every_row,
    named_by_evidence,
    spying,
    three_batches,
    two_batches,
    uninterrupted,
    without_resumed,
)

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]

RUNNING, FAILED, COMPLETED, ABANDONED = (
    ImportState.RUNNING,
    ImportState.FAILED,
    ImportState.COMPLETED,
    ImportState.ABANDONED,
)


@pytest.fixture
def genes(registry):
    """The human genes GENE names: TP53 and MDM2."""
    return (
        create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998"),
        create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973"),
    )


class Pause:
    """A point a worker stops at until the test releases it: `reached` is
    set when the worker gets there, and it goes on once `go` is set. A pause
    the test never releases fails the worker after WAIT_SECONDS."""

    def __init__(self):
        self.reached = threading.Event()
        self.go = threading.Event()

    def wait(self):
        self.reached.set()
        if not self.go.wait(WAIT_SECONDS):
            raise AssertionError("The test never released the pause.")


class Worker:
    """A function run in a thread of this name, on the thread's own
    connection, which it closes: what the function returned, or raised."""

    def __init__(self, name, function):
        self.result = None
        self.error = None
        self.thread = threading.Thread(
            target=self._run, args=(function,), name=name, daemon=True
        )
        self.thread.start()

    def _run(self, function):
        try:
            self.result = function()
        except BaseException as error:  # reported by the test
            self.error = error
        finally:
            connection.close()

    def join(self):
        self.thread.join(WAIT_SECONDS)
        assert not self.thread.is_alive(), f"{self.thread.name} never ended"


def in_thread(name) -> bool:
    return threading.current_thread().name == name


def waits_on_a_lock(release_id) -> bool:
    """Whether the backend holding the release's run lock (its commit's)
    waits on a lock now: one of its locks is not granted. pg_locks is read
    anew by each query."""
    pid = run_lock_holder(release_id)
    if pid is None:
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE pid = %s AND NOT granted", [pid]
        )
        return bool(cursor.fetchone()[0])


def paused_or_waiting(pause, release_id) -> str:
    """ "paused" once the worker reaches its pause, "waiting" once the
    release's commit is seen waiting on a lock: whichever comes first,
    within WAIT_SECONDS."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if pause.reached.is_set():
            return "paused"
        if waits_on_a_lock(release_id):
            return "waiting"
        time.sleep(0.05)
    pytest.fail("the second commit neither paused nor waited on a lock")


def release_all(pauses, workers):
    for pause in pauses:
        pause.go.set()
    for worker in workers:
        worker.join()


def records(release) -> list:
    return sorted(
        assertion.source_record.record_key
        for assertion in EvidenceAssertion.objects.filter(release=release)
    )


def runs_of(release) -> list:
    return list(
        ImportRun.objects.filter(release=release, mode=ImportMode.COMMIT).order_by("pk")
    )


# ── A2: a commit killed midway ──────────────────────────────────────────────


def test_a_commit_whose_process_is_killed_resumes_to_the_rows_of_one_that_was_not(
    registry, tmp_path, monkeypatch, genes
):
    release = three_batches(tmp_path, monkeypatch)
    dry(release, create_entities=True)
    staff = StaffUserFactory()
    expected_rows, expected_report = uninterrupted(release, staff, create_entities=True)
    # The commit pauses in batch 2 of 3, after the batch's entities, papers,
    # terms and relationships, before its first record's evidence.
    first_of_batch_2 = SourceRecord.objects.get(release=release, record_key="r3").pk
    real = commit_module.record_claims
    pause = Pause()

    def pausing(source_record, claims, *, import_run):
        if in_thread("commit") and source_record == first_of_batch_2:
            if not pause.reached.is_set():
                pause.wait()
        return real(source_record, claims, import_run=import_run)

    monkeypatch.setattr(commit_module, "record_claims", pausing)
    before = every_row()
    worker = Worker("commit", lambda: commit(release, by=staff, create_entities=True))
    try:
        assert pause.reached.wait(WAIT_SECONDS), "the commit never reached batch 2"
        pid = run_lock_holder(release.pk)
        assert pid is not None
        # As a killed process's session ends: the run lock goes with it.
        terminate_backend(pid)
    finally:
        release_all([pause], [worker])
    stopped = worker.error
    assert isinstance(stopped, CommitStopped), repr(stopped)
    assert not stopped.marked
    run = ImportRun.objects.get(pk=stopped.run_id)
    assert (run.state, run.error, run.checkpoint["line"]) == (RUNNING, "", 3)
    assert run_lock_holder(release.pk) is None
    [runs] = [section for section in run_checks() if section.name == "runs"]
    assert runs.lines == (
        f"Run {run.pk} (a commit) of release {release.pk} was left running by a "
        "process that ended: the release's next commit or discard marks it failed, "
        "then resume it with graph_import --commit or discard it with "
        "graph_discard_release.",
    )
    # Batch 2 rolled back whole: the evidence is batch 1's, and every row the
    # attempt created is named by it.
    assert records(release) == ["r1", "r2"]
    named = named_by_evidence(release)
    for kind, rows in every_row().items():
        assert rows - before[kind] <= named[kind], kind
    # The rerun, from this thread, marks the run failed, resumes it and skips
    # batch 1.
    monkeypatch.setattr(commit_module, "record_claims", real)
    called = spying(monkeypatch, release)
    resumed = commit(release, by=staff, create_entities=True)
    assert (resumed.outcome, resumed.run.pk, resumed.run.state) == (
        RESUMED,
        run.pk,
        COMPLETED,
    )
    [entry] = resumed.run.report["resumed"]
    assert entry["error"] == (
        "Its process ended before the run finished; found when a commit of release "
        f"{release.pk} started."
    )
    assert entry["line"] == 3
    assert called == ["r3", "r4", "r5", "r6"]
    assert set(
        EvidenceAssertion.objects.filter(release=release).values_list(
            "import_run_id", flat=True
        )
    ) == {run.pk}
    assert natural_rows() == expected_rows
    assert without_resumed(resumed.run.report) == without_resumed(expected_report)


# ── A3: two releases sharing rows commit at once ────────────────────────────


def test_two_releases_sharing_rows_commit_at_once_without_duplicates_or_orphans(
    registry, tmp_path, monkeypatch, genes
):
    # Both name a new gene (HGNC:5), the pair TP53 -> HGNC:5, a new PMID and
    # a new manifest term.
    shared = row(GENE, object_id="HGNC:5", publications="pmid:12345", eco="ECO:0000353")
    first = registered(tmp_path / "first", [shared])
    second = registered(tmp_path / "second", [shared])
    dry(first, create_entities=True)
    dry(second, create_entities=True)
    before = every_row()
    real = commit_module.create_relationships
    pause = Pause()

    def pausing(canonicals, *, import_run):
        results = real(canonicals, import_run=import_run)
        if in_thread("first") and not pause.reached.is_set():
            # The batch's shared rows are written, and not committed.
            pause.wait()
        return results

    monkeypatch.setattr(commit_module, "create_relationships", pausing)
    workers = [Worker("first", lambda: commit(first, create_entities=True))]
    try:
        assert pause.reached.wait(WAIT_SECONDS), "the first commit never paused"
        workers.append(Worker("second", lambda: commit(second, create_entities=True)))
        deadline = time.monotonic() + WAIT_SECONDS
        while not waits_on_a_lock(second.pk):
            assert time.monotonic() < deadline, "the second commit never waited"
            time.sleep(0.05)
    finally:
        release_all([pause], workers)
    for worker in workers:
        assert worker.error is None, repr(worker.error)
        assert (worker.result.outcome, worker.result.run.state) == (
            COMMITTED,
            COMPLETED,
        )
        # No batch failed: no run was resumed.
        assert worker.result.run.report["resumed"] == []
    gene = Entity.objects.get(identity_key__endswith="HGNC:5")
    assert gene.created_by_release_id == first.pk
    assert Relationship.objects.filter(object=gene).count() == 1
    assert Publication.objects.filter(pmid="12345").count() == 1
    assert Term.objects.filter(curie="ECO:0000353").count() == 1
    assert (records(first), records(second)) == (["r1"], ["r1"])
    created = [w.result.run.report["counts"]["created"] for w in workers]
    assert created == [
        {"entities": 1, "relationships": 1, "publications": 1, "terms": 1},
        {"entities": 0, "relationships": 0, "publications": 0, "terms": 0},
    ]
    # No orphan: every row the commits wrote is named by their evidence.
    named = {kind: set() for kind in before}
    for release in (first, second):
        for kind, rows in named_by_evidence(release).items():
            named[kind] |= rows
    for kind, rows in every_row().items():
        assert rows - before[kind] <= named[kind], kind


# ── The lock order: one kind at a time ──────────────────────────────────────


def _entities(tmp_path):
    """The same two new genes, first stated in opposite orders; no paper,
    no term, and disjoint relationships."""
    first = registered(
        tmp_path / "first",
        [
            row(GENE, subject_id="HGNC:5", object_id="HGNC:11998"),
            row(GENE, record_id="r2", subject_id="HGNC:7", object_id="HGNC:11998"),
        ],
    )
    second = registered(
        tmp_path / "second",
        [
            row(GENE, object_id="HGNC:7"),
            row(GENE, record_id="r2", object_id="HGNC:5"),
        ],
    )
    return first, second, "create_entity", {"create_entities": True}


def _publications(tmp_path):
    """The same two new PMIDs, first cited in opposite orders, by records
    whose endpoints are stored and disjoint."""
    create_entity("gene", "Gene five", 9606, "hgnc", "HGNC:5")
    create_entity("gene", "Gene seven", 9606, "hgnc", "HGNC:7")
    first = registered(
        tmp_path / "first",
        [
            row(GENE, publications="pmid:1001"),
            row(GENE, record_id="r2", publications="pmid:1002"),
        ],
    )
    others = {"subject_id": "HGNC:5", "object_id": "HGNC:7"}
    second = registered(
        tmp_path / "second",
        [
            row(GENE, publications="pmid:1002", **others),
            row(GENE, record_id="r2", publications="pmid:1001", **others),
        ],
    )
    return first, second, "get_or_create_publication", {}


def _terms(tmp_path):
    """The same two new manifest terms, first stated in opposite orders, by
    records whose endpoints are stored and disjoint; no paper."""
    for label, value in (
        ("TP53", "P04637"),
        ("MDM2", "Q00987"),
        ("BRCA1", "P38398"),
        ("BRCA2", "P51587"),
    ):
        create_entity("protein", label, 9606, "uniprotkb", value)
    first = registered(
        tmp_path / "first",
        [
            row(PROTEIN, method="MI:0018"),
            row(PROTEIN, record_id="r2", eco="ECO:0000353"),
        ],
    )
    others = {"subject_id": "P38398", "object_id": "P51587"}
    second = registered(
        tmp_path / "second",
        [
            row(PROTEIN, eco="ECO:0000353", **others),
            row(PROTEIN, record_id="r2", method="MI:0018", **others),
        ],
    )
    return first, second, "get_or_create_term", {}


def opposite_orders(tmp_path, monkeypatch, case):
    """Commit `first` until just after its first call of the case's kind,
    then `second`, until it is paused at the same point or waits on a lock;
    then let both go. Returns the releases, what the wait found, the
    workers, and the commit options."""
    first, second, name, options = case(tmp_path)
    dry(first, **options)
    dry(second, **options)
    real = getattr(commit_module, name)
    pauses = {"first": Pause(), "second": Pause()}

    def pausing(*args, **kwargs):
        result = real(*args, **kwargs)
        pause = pauses.get(threading.current_thread().name)
        if pause is not None and not pause.reached.is_set():
            pause.wait()
        return result

    monkeypatch.setattr(commit_module, name, pausing)
    workers = [Worker("first", lambda: commit(first, **options))]
    try:
        assert pauses["first"].reached.wait(WAIT_SECONDS), "the first never paused"
        workers.append(Worker("second", lambda: commit(second, **options)))
        found = paused_or_waiting(pauses["second"], second.pk)
    finally:
        release_all(pauses.values(), workers)
    return first, second, found, workers, options


@pytest.mark.parametrize("case", [_entities, _publications, _terms])
def test_commits_stating_shared_rows_in_opposite_orders_wait_and_never_deadlock(
    registry, tmp_path, monkeypatch, genes, case
):
    first, second, found, workers, _ = opposite_orders(tmp_path, monkeypatch, case)
    # The second waited before its first call of the kind returned: it asked
    # for the row the first holds, not for the other one.
    assert found == "waiting"
    for worker in workers:
        assert worker.error is None, repr(worker.error)
        assert (worker.result.outcome, worker.result.run.state) == (
            COMMITTED,
            COMPLETED,
        )
        assert worker.result.run.report["resumed"] == []
    assert (records(first), records(second)) == (["r1", "r2"], ["r1", "r2"])


def test_without_the_lock_order_the_same_commits_deadlock_and_resume(
    registry, tmp_path, monkeypatch, genes
):
    # Each kind written in the order first stated: the test above bites.
    monkeypatch.setattr(commit_module, "lock_order", lambda keys: list(keys))
    first, second, found, workers, options = opposite_orders(
        tmp_path, monkeypatch, _entities
    )
    assert found == "paused"
    failed = [worker for worker in workers if worker.error is not None]
    assert len(failed) == 1, [repr(worker.error) for worker in workers]
    [stopped] = [worker.error for worker in failed]
    assert isinstance(stopped, CommitStopped), repr(stopped)
    assert "the database stopped the batch: deadlock detected" in str(stopped)
    assert stopped.marked
    [done] = [worker for worker in workers if worker.error is None]
    assert done.result.run.state == COMPLETED
    release = first if failed[0] is workers[0] else second
    assert [run.state for run in runs_of(release)] == [FAILED]
    resumed = commit(release, **options)
    assert (resumed.outcome, resumed.run.pk, resumed.run.state) == (
        RESUMED,
        stopped.run_id,
        COMPLETED,
    )
    assert (records(first), records(second)) == (["r1", "r2"], ["r1", "r2"])


# ── One committer ───────────────────────────────────────────────────────────


def test_while_a_commit_runs_its_release_has_no_other_runner(
    registry, tmp_path, monkeypatch, genes
):
    release = two_batches(tmp_path, monkeypatch)
    dry(release)
    staff = StaffUserFactory()
    planning, writing = Pause(), Pause()
    plan_batch, record_claims = commit_module.plan_batch, commit_module.record_claims

    def pausing_planning(records, *, run_ctx, check=True):
        if in_thread("commit") and check and not planning.reached.is_set():
            planning.wait()
        return plan_batch(records, run_ctx=run_ctx, check=check)

    def stopping_writing(source_record, claims, *, import_run):
        if in_thread("commit"):
            writing.wait()
            raise ValueError("Stopped for the test.")
        return record_claims(source_record, claims, import_run=import_run)

    monkeypatch.setattr(commit_module, "plan_batch", pausing_planning)
    monkeypatch.setattr(commit_module, "record_claims", stopping_writing)
    running = (
        f"A dry run, commit or discard of release {release.pk} is running: one runs "
        "at a time. Nothing was written."
    )
    worker = Worker("commit", lambda: commit(release, by=staff))
    try:
        # Planning: the run does not exist yet, and the lock refuses a dry run.
        assert planning.reached.wait(WAIT_SECONDS), "the commit never planned"
        with pytest.raises(DryRunError) as dry_refused:
            dry_run(release, triggered_by=staff)
        assert (dry_refused.value.precondition, str(dry_refused.value)) == (
            9,
            running,
        )
        planning.go.set()
        # Writing a batch: the lock refuses a second commit and a discard; a
        # dry run is refused by the open commit run before the lock.
        assert writing.reached.wait(WAIT_SECONDS), "the commit never wrote"
        with pytest.raises(CommitError) as commit_refused:
            commit(release, by=staff)
        assert (commit_refused.value.precondition, str(commit_refused.value)) == (
            9,
            running,
        )
        with pytest.raises(ReleaseError) as discard_refused:
            discard_release(release, reason="Why.", discarded_by=staff)
        assert str(discard_refused.value) == (
            f"A dry run, commit or discard of release {release.pk} is running: a "
            "release is not discarded while one runs. Nothing was written."
        )
        [run] = runs_of(release)
        with pytest.raises(DryRunError) as open_refused:
            dry_run(release, triggered_by=staff)
        assert open_refused.value.precondition == 4
    finally:
        release_all([planning, writing], [worker])
    stopped = worker.error
    assert isinstance(stopped, CommitStopped), repr(stopped)
    assert str(stopped) == "Line 3: Stopped for the test."
    assert ImportRun.objects.get(pk=run.pk).state == FAILED
    # Once the commit stopped, a dry run is refused only while its run is
    # open: by the open run, never by the lock.
    with pytest.raises(DryRunError) as still_open:
        dry_run(release, triggered_by=staff)
    assert (still_open.value.precondition, str(still_open.value)) == (
        4,
        f"Release {release.pk} has an open commit run {run.pk}: a dry run never runs "
        "beside one. Resume it with graph_import --commit, or discard it with "
        "graph_discard_release. Nothing was written.",
    )
    discard_release(release, reason="Give it up.", discarded_by=staff)
    assert ImportRun.objects.get(pk=run.pk).state == ABANDONED
    again = dry_run(release, triggered_by=staff)
    assert (again.state, again.report["verdict"]) == (COMPLETED, "ok")
