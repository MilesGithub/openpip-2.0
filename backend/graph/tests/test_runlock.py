"""The run lock (graph.ingest.runlock): one dry run, commit or discard of a
release at a time. On PostgreSQL a second session is refused while one
holds the lock, and closing that session, or ending its backend, frees it;
the lock holds across the transactions it spans; it is not re-entrant; it
keys release ids 1 to 2,147,483,647; it sees only the current database's
locks; an unlock that fails closes its connection without hiding the error
leaving the block; a session that no longer holds the lock it took knows it
(run_lock_lost); end_dead_runs marks only the release's running runs, and
only for the holder. On any other database there is no lock.

The lock is held by another session through a thread's own connection
(lock_helpers.held_run_lock), which works inside a plain db test: a lock
held in the test's own session would pass, since PostgreSQL's advisory
locks are re-entrant. Every wait is bounded."""

import datetime
import threading
import time
from contextlib import ExitStack

import psycopg2
import pytest
from django.db import DatabaseError, InterfaceError, connection, transaction
from django.utils import timezone

from graph.ingest.runlock import (
    HELD,
    KEY,
    MAX_RUN_LOCK_KEY,
    NESTED,
    RUN_LOCK_CLASS,
    RunLocked,
    end_dead_runs,
    release_run_lock,
    run_lock_holder,
    run_lock_lost,
    runs_are_locked,
)
from graph.models import ImportMode, ImportRun, ImportState
from graph.tests.factories import DatasetReleaseFactory, ImportRunFactory
from graph.tests.lock_helpers import WAIT_SECONDS, held_run_lock, terminate_backend

RUNNING, FAILED, COMPLETED = (
    ImportState.RUNNING,
    ImportState.FAILED,
    ImportState.COMPLETED,
)
# A release id; the lock needs no release row.
RELEASE = 12


def backend_pid() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def refused(release_id) -> RunLocked:
    """The RunLocked that release_run_lock raises for this id."""
    with pytest.raises(RunLocked) as raised:
        with release_run_lock(release_id):
            pytest.fail(f"the run lock of release {release_id} was taken")
    assert raised.value.release_id == release_id
    return raised.value


def freed(release_id) -> bool:
    """Whether no backend holds the lock, within 30 s: a session that ends
    releases its locks a moment after its client closes it."""
    deadline = time.monotonic() + WAIT_SECONDS
    while run_lock_holder(release_id) is not None:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def in_a_thread(function):
    """function() on a thread's own connection, which is closed after; what
    it returns, or what it raised, raised here."""
    out = {}

    def call():
        try:
            out["result"] = function()
        except Exception as error:  # reported by the main thread
            out["error"] = error
        finally:
            connection.close()

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive(), f"the thread still runs after {WAIT_SECONDS} s"
    if "error" in out:
        raise out["error"]
    return out["result"]


def closed(wrapper) -> bool:
    """Whether Django's connection is closed (also inside an atomic block,
    where it keeps its closed driver connection until the block ends)."""
    return wrapper.connection is None or bool(wrapper.connection.closed)


# ── The keys ────────────────────────────────────────────────────────────────


def test_the_lock_is_keyed_by_openpip_and_the_release():
    assert RUN_LOCK_CLASS == int.from_bytes(b"GRC1", "big")
    assert MAX_RUN_LOCK_KEY == 2**31 - 1


@pytest.mark.postgres
def test_runs_are_locked_on_postgresql(db):
    assert runs_are_locked() is True


# ── One holder ──────────────────────────────────────────────────────────────


@pytest.mark.postgres
def test_a_second_session_is_refused_while_one_holds_the_lock(db):
    with held_run_lock(RELEASE) as pid:
        assert run_lock_holder(RELEASE) == pid
        assert refused(RELEASE).reason == HELD
        # Another release's lock is another lock.
        with release_run_lock(RELEASE + 1):
            assert run_lock_holder(RELEASE + 1) == backend_pid()
        assert run_lock_holder(RELEASE) == pid
    assert run_lock_holder(RELEASE) is None
    with release_run_lock(RELEASE):
        assert run_lock_holder(RELEASE) == backend_pid()
    assert run_lock_holder(RELEASE) is None


@pytest.mark.postgres
def test_closing_the_holders_connection_frees_the_lock(db):
    taken, go = threading.Event(), threading.Event()
    out = {}

    def holder():
        try:
            with release_run_lock(RELEASE):
                out["pid"] = backend_pid()
                taken.set()
                if not go.wait(WAIT_SECONDS):
                    out["expired"] = True
                # Its connection is lost, as a killed process's is. Leaving
                # the block then finds no hold, and raises nothing.
                connection.close()
        except Exception as error:  # reported by the main thread
            out["error"] = error
        finally:
            taken.set()
            connection.close()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert taken.wait(WAIT_SECONDS), "the holder never took the lock"
        assert "error" not in out, out.get("error")
        assert run_lock_holder(RELEASE) == out["pid"]
        assert refused(RELEASE).reason == HELD
    finally:
        go.set()
        thread.join(WAIT_SECONDS)
    assert not thread.is_alive()
    assert "error" not in out and "expired" not in out, out
    assert freed(RELEASE)
    with release_run_lock(RELEASE):
        assert run_lock_holder(RELEASE) == backend_pid()


@pytest.mark.postgres
def test_the_lock_holds_across_the_transactions_it_spans(db):
    class Stopped(Exception):
        pass

    with ExitStack() as stack:
        with pytest.raises(Stopped):
            with transaction.atomic():
                stack.enter_context(release_run_lock(RELEASE))
                raise Stopped
        # The transaction it was taken in is rolled back; the lock stays.
        assert run_lock_holder(RELEASE) == backend_pid()
        assert in_a_thread(lambda: run_lock_holder(RELEASE)) == backend_pid()
    assert run_lock_holder(RELEASE) is None


@pytest.mark.postgres
def test_the_lock_is_not_reentrant(db):
    with release_run_lock(RELEASE):
        own = backend_pid()
        assert run_lock_holder(RELEASE) == own
        assert refused(RELEASE).reason == NESTED
        assert run_lock_holder(RELEASE) == own
    # The refusal took no second hold: leaving the block released the one.
    assert run_lock_holder(RELEASE) is None
    with held_run_lock(RELEASE) as pid:
        assert run_lock_holder(RELEASE) == pid


@pytest.mark.postgres
def test_ids_outside_the_keys_range_are_refused(db):
    for release_id in (0, -1, MAX_RUN_LOCK_KEY + 1, 2**63 - 1):
        error = refused(release_id)
        assert error.reason == KEY
        assert run_lock_holder(release_id) is None
    assert "2,147,483,647" in str(refused(MAX_RUN_LOCK_KEY + 1))
    with release_run_lock(MAX_RUN_LOCK_KEY):
        assert run_lock_holder(MAX_RUN_LOCK_KEY) == backend_pid()
    with release_run_lock(1):
        assert run_lock_holder(1) == backend_pid()


@pytest.mark.postgres
def test_a_lock_held_in_another_database_is_not_seen(db):
    # The server's postgres database, as the test database's settings reach
    # it (in CI, the job's PostgreSQL service).
    settings = connection.settings_dict
    elsewhere = psycopg2.connect(
        dbname="postgres",
        user=settings["USER"] or None,
        password=settings["PASSWORD"] or None,
        host=settings["HOST"] or None,
        port=settings["PORT"] or None,
    )
    elsewhere.autocommit = True
    try:
        with elsewhere.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone()[0] != connection.settings_dict["NAME"]
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s)", [RUN_LOCK_CLASS, RELEASE]
            )
            assert cursor.fetchone()[0] is True
        with connection.cursor() as cursor:
            # pg_locks lists it, as every database's advisory locks.
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND "
                "classid = %s AND objid = %s AND objsubid = 2 AND granted",
                [RUN_LOCK_CLASS, RELEASE],
            )
            assert cursor.fetchone()[0] == 1
        assert run_lock_holder(RELEASE) is None
        with release_run_lock(RELEASE):
            assert run_lock_holder(RELEASE) == backend_pid()
    finally:
        elsewhere.close()


# ── A hold that cannot be released ──────────────────────────────────────────


@pytest.mark.postgres
def test_an_unlock_that_fails_closes_the_connection_and_keeps_the_error(db):
    # The block's error aborts the test's transaction, so the unlock fails.
    with pytest.raises(DatabaseError, match="division by zero") as raised:
        with release_run_lock(RELEASE):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")
    [note] = raised.value.__notes__
    assert note.startswith(f"The run lock of release {RELEASE} was not released (")
    assert "so its connection was closed, which ends the lock." in note
    assert closed(connection)
    # The session ended, and its lock with it.
    assert in_a_thread(lambda: freed(RELEASE))
    with held_run_lock(RELEASE):
        pass


@pytest.mark.postgres
def test_a_holder_whose_backend_ends_frees_the_lock_and_its_error_says_so(db):
    # The holder's backend is ended, as a killed process's session ends: the
    # lock goes with it at once, and the holder's next query fails, which
    # the unlock that fails does not hide.
    taken, go = threading.Event(), threading.Event()
    out = {}

    def holder():
        try:
            with release_run_lock(RELEASE):
                out["pid"] = backend_pid()
                taken.set()
                if not go.wait(WAIT_SECONDS):
                    out["expired"] = True
                backend_pid()
        except (DatabaseError, InterfaceError) as error:
            out["error"] = error
        except Exception as error:  # reported by the main thread
            out["other"] = error
        finally:
            taken.set()
            connection.close()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert taken.wait(WAIT_SECONDS), "the holder never took the lock"
        assert run_lock_holder(RELEASE) == out["pid"]
        terminate_backend(out["pid"])
        assert run_lock_holder(RELEASE) is None
        with release_run_lock(RELEASE):
            assert run_lock_holder(RELEASE) == backend_pid()
    finally:
        go.set()
        thread.join(WAIT_SECONDS)
    assert not thread.is_alive()
    assert "other" not in out and "expired" not in out, out
    error = out["error"]
    [note] = error.__notes__
    assert note.startswith(f"The run lock of release {RELEASE} was not released (")


@pytest.mark.postgres
def test_an_unlock_that_finds_no_hold_closes_the_connection_quietly(db):
    with release_run_lock(RELEASE):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)", [RUN_LOCK_CLASS, RELEASE]
            )
    # A clean exit stays clean; the connection is closed all the same.
    assert closed(connection)
    assert in_a_thread(lambda: freed(RELEASE))


@pytest.mark.postgres
def test_a_lock_this_session_no_longer_holds_is_lost(db, monkeypatch):
    # What a run's holder asks before it writes the run once more: a session
    # that ended and connected again holds nothing.
    assert run_lock_lost(RELEASE) is True
    with release_run_lock(RELEASE):
        assert run_lock_lost(RELEASE) is False
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)", [RUN_LOCK_CLASS, RELEASE]
            )
        assert run_lock_lost(RELEASE) is True
    # A check that cannot run (the connection is closed) counts as lost.
    assert closed(connection)
    assert run_lock_lost(RELEASE) is True
    # Without PostgreSQL there is no lock to lose.
    with monkeypatch.context() as patched:
        patched.setattr(connection, "vendor", "sqlite")
        assert run_lock_lost(RELEASE) is False


# ── Runs left running ───────────────────────────────────────────────────────


@pytest.mark.postgres
def test_end_dead_runs_marks_the_releases_running_runs(db):
    release, other = DatasetReleaseFactory(), DatasetReleaseFactory()
    then = timezone.now() - datetime.timedelta(hours=1)
    dry = ImportRunFactory(release=release)
    commit = ImportRunFactory(release=release, file=None, mode=ImportMode.COMMIT)
    stamped = ImportRunFactory(release=release, finished_at=then)
    failed = ImportRunFactory(
        release=release, state=FAILED, error="Stopped.", finished_at=then
    )
    completed = ImportRunFactory(release=release, state=COMPLETED, finished_at=then)
    elsewhere = ImportRunFactory(release=other)
    found_by = f"a commit of release {release.pk}"
    with release_run_lock(release.pk):
        ended = end_dead_runs(release.pk, found_by=found_by)
        assert end_dead_runs(release.pk, found_by=found_by) == []
    assert ended == sorted([dry.pk, commit.pk, stamped.pk])
    error = (
        "Its process ended before the run finished; found when a commit of "
        f"release {release.pk} started."
    )
    for run in (dry, commit, stamped):
        stored = ImportRun.objects.get(pk=run.pk)
        assert (stored.state, stored.error) == (FAILED, error)
        assert stored.finished_at is not None
    # finished_at is set only where it was empty.
    assert ImportRun.objects.get(pk=stamped.pk).finished_at == then
    assert ImportRun.objects.get(pk=dry.pk).finished_at > then
    for run in (failed, completed, elsewhere):
        stored = ImportRun.objects.get(pk=run.pk)
        assert (stored.state, stored.error, stored.finished_at) == (
            run.state,
            run.error,
            run.finished_at,
        )


@pytest.mark.postgres
def test_end_dead_runs_is_for_the_holder_of_the_lock(db):
    release = DatasetReleaseFactory()
    run = ImportRunFactory(release=release)
    found_by = f"a discard of release {release.pk}"
    with pytest.raises(RuntimeError, match="this connection does not hold it"):
        end_dead_runs(release.pk, found_by=found_by)
    with held_run_lock(release.pk):
        with pytest.raises(RuntimeError, match="this connection does not hold it"):
            end_dead_runs(release.pk, found_by=found_by)
    with release_run_lock(release.pk + 1):
        with pytest.raises(RuntimeError, match="this connection does not hold it"):
            end_dead_runs(release.pk, found_by=found_by)
    stored = ImportRun.objects.get(pk=run.pk)
    assert (stored.state, stored.error, stored.finished_at) == (RUNNING, "", None)


# ── Without PostgreSQL ──────────────────────────────────────────────────────


def test_without_postgresql_there_is_no_lock(db, monkeypatch):
    release = DatasetReleaseFactory()
    run = ImportRunFactory(release=release)
    with monkeypatch.context() as patched:
        patched.setattr(connection, "vendor", "sqlite")
        assert runs_are_locked() is False
        with release_run_lock(release.pk):
            # Nothing is held, so nothing is nested or refused either.
            with release_run_lock(release.pk):
                assert run_lock_holder(release.pk) is None
        with release_run_lock(0):
            pass
        assert end_dead_runs(release.pk, found_by="a commit of a release") == []
    stored = ImportRun.objects.get(pk=run.pk)
    assert (stored.state, stored.error, stored.finished_at) == (RUNNING, "", None)
