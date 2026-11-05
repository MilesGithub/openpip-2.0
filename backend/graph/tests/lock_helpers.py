"""Helpers for the tests of the run lock (graph.ingest.runlock): a release's
run lock held by another session, a release's row held by another session,
and a backend ended as a killed process's session ends. PostgreSQL only:
tests that use them carry the postgres marker.

A lock held in the test's own session would pass silently, since the run
lock refuses re-entry only through its own nested check, so held_run_lock
holds it on a thread's connection; that works inside a plain db test. Every
wait is bounded (30 s at most) and fails the test when it expires."""

import threading
import time
from contextlib import contextmanager

from django.db import connection, transaction

from graph.ingest.runlock import RUN_LOCK_CLASS
from graph.models import DatasetRelease

# The longest any wait here lasts.
WAIT_SECONDS = 30


@contextmanager
def held_run_lock(release_id):
    """Hold the run lock of release `release_id` on another session for the
    block: a thread whose own connection takes it, and which must get it.
    Yields that backend's pid. On exit the thread releases the lock and
    closes its connection."""
    taken, done = threading.Event(), threading.Event()
    state = {}

    def hold():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s, %s), pg_backend_pid()",
                    [RUN_LOCK_CLASS, release_id],
                )
                state["taken"], state["pid"] = cursor.fetchone()
            taken.set()
            if not done.wait(WAIT_SECONDS):
                state["expired"] = True
            if state["taken"]:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s, %s)",
                        [RUN_LOCK_CLASS, release_id],
                    )
        except Exception as error:  # reported by the main thread
            state["error"] = error
        finally:
            taken.set()
            connection.close()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    try:
        assert taken.wait(WAIT_SECONDS), "the thread never took the run lock"
        assert "error" not in state, state.get("error")
        assert (
            state["taken"] is True
        ), f"another session holds the run lock of release {release_id} already"
        yield state["pid"]
    finally:
        done.set()
        thread.join(WAIT_SECONDS)
    assert not thread.is_alive(), "the thread holding the run lock never ended"
    assert "error" not in state, state.get("error")
    assert (
        "expired" not in state
    ), f"the run lock was held for more than {WAIT_SECONDS} s, and released"


@contextmanager
def held_release_row(release_id):
    """Hold release `release_id`'s row FOR UPDATE on another session for the
    block, as an approval under way or a session left open in a transaction
    holds it: a thread whose own connection locks the row in a transaction
    it keeps open. The row must be committed, so the test needs
    transaction=True. On exit the thread ends its transaction and closes its
    connection."""
    taken, done = threading.Event(), threading.Event()
    state = {}

    def hold():
        try:
            with transaction.atomic():
                state["found"] = (
                    DatasetRelease.objects.select_for_update()
                    .filter(pk=release_id)
                    .first()
                    is not None
                )
                taken.set()
                if not done.wait(WAIT_SECONDS):
                    state["expired"] = True
        except Exception as error:  # reported by the main thread
            state["error"] = error
        finally:
            taken.set()
            connection.close()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    try:
        assert taken.wait(WAIT_SECONDS), "the thread never locked the release's row"
        assert "error" not in state, state.get("error")
        assert state["found"] is True, f"release {release_id} is not committed"
        yield
    finally:
        done.set()
        thread.join(WAIT_SECONDS)
    assert not thread.is_alive(), "the thread holding the release's row never ended"
    assert "error" not in state, state.get("error")
    assert (
        "expired" not in state
    ), f"the release's row was held for more than {WAIT_SECONDS} s, and released"


def terminate_backend(pid):
    """End backend `pid`, as a killed process's session ends: its locks go
    with it. Returns once the server lists neither the backend nor a lock of
    it (at most 30 s)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_terminate_backend(%s)", [pid])
        assert cursor.fetchone()[0] is True, f"backend {pid} was not signalled"
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            # pg_stat_activity is read once per transaction unless its
            # snapshot is cleared; pg_locks is read anew each time.
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT (SELECT count(*) FROM pg_stat_activity WHERE pid = %s)"
                " + (SELECT count(*) FROM pg_locks WHERE pid = %s)",
                [pid, pid],
            )
            if not cursor.fetchone()[0]:
                return
            assert (
                time.monotonic() < deadline
            ), f"backend {pid} still runs {WAIT_SECONDS} s after it was terminated"
            time.sleep(0.05)
