"""The run lock: whether a dry run, a commit or a discard of a release is
under way (G5).

A commit is many transactions, so no row lock spans it, and a process that
is killed leaves its run running for good. One PostgreSQL session advisory
lock per release says whether a run of it is under way: its first key is
RUN_LOCK_CLASS (b"GRC1"), so openPIP's locks never meet another
application's, and its second the release id, 1 to MAX_RUN_LOCK_KEY. Every
dry run, commit and discard holds it (release_run_lock) from the check that
takes it until the service returns, and a second one is refused. It holds
across the run's transactions, survives their rollbacks, and ends with the
session, which a killed process loses.

It is not re-entrant, as PostgreSQL's own advisory locks are: a connection
that already holds a release's lock is refused it ("nested"), so a
connection that kept a hold (a worker with CONN_MAX_AGE, a loader, a shell)
never passes the lock to itself while every other process is refused. A
hold that cannot be released (an aborted transaction, a lost connection)
closes its connection, which ends the session and every lock it holds.

pg_locks lists the advisory locks of every database on the server, and an
advisory lock belongs to one database (two portals on one server share
release ids), so each read of the run locks here is limited to the current
database. run_lock_holder gives the backend that holds a release's lock,
and run_lock_holders every held lock's, for graph_check and an operator;
waiting_backends says which of those backends wait on a lock. A commit or
a discard, once it holds the lock, marks the release's runs left running by
a process that ended as failed (end_dead_runs); a dry run marks none, so it
writes only its run and the records.

On any other database (SQLite: tests, and a portal or host tool run without
DATABASE_URL) there is no lock: release_run_lock yields without locking,
run_lock_holder gives None, run_lock_holders {}, waiting_backends an empty
set, and end_dead_runs marks nothing. The partial unique constraint
graph_run_one_open_commit still allows one open commit run there. A
connection pooler in transaction mode (PgBouncer) would break any session
lock; the compose files have none.
"""

from contextlib import contextmanager

from django.db import DatabaseError, InterfaceError, connections, router, transaction
from django.utils import timezone

from graph.models import DatasetRelease, ImportRun, ImportState

# The first key of the two-key advisory locks: int.from_bytes(b"GRC1", "big").
RUN_LOCK_CLASS = 1196573489
# The second key is the release id, a positive int4.
MAX_RUN_LOCK_KEY = 2_147_483_647

# RunLocked's reasons.
HELD = "held"
NESTED = "nested"
KEY = "key"

# The grants of run locks (RUN_LOCK_CLASS, any release id) in the current
# database, as pg_locks lists a two-key advisory lock: objid is the second
# key.
_HELD = (
    "FROM pg_locks WHERE locktype = 'advisory' AND classid = %s AND objsubid = 2 "
    "AND granted AND database = "
    "(SELECT oid FROM pg_database WHERE datname = current_database())"
)
# The grant of the lock on (RUN_LOCK_CLASS, release id).
_GRANTED = f"{_HELD} AND objid = %s"


class RunLocked(Exception):
    """The run lock of release `release_id` was not taken, for `reason`:
    another session holds it (HELD), this connection holds it already
    (NESTED), or the id is outside 1 to MAX_RUN_LOCK_KEY (KEY). Nothing was
    taken; each caller words each reason."""

    def __init__(self, release_id, reason):
        lock = f"the run lock of release {release_id}"
        if reason == HELD:
            text = f"Another session holds {lock}."
        elif reason == NESTED:
            text = f"This connection already holds {lock}."
        elif reason == KEY:
            text = (
                f"Release {release_id}'s id is not one a run lock keys (1 to "
                f"{MAX_RUN_LOCK_KEY:,})."
            )
        else:
            raise ValueError(f"A run lock is held, nested or key, not {reason!r}.")
        super().__init__(text)
        self.release_id = release_id
        self.reason = reason


def runs_are_locked(using=None) -> bool:
    """Whether the database has the run lock: PostgreSQL."""
    return connections[_alias(using)].vendor == "postgresql"


@contextmanager
def release_run_lock(release_id, *, using=None):
    """Hold the run lock of release `release_id` for the block, on the
    connection ImportRun is written through, unless `using` names another.

    Raises RunLocked, holding nothing, when the id is not one a lock keys,
    when this connection holds the lock already, or when another session
    does. On leaving the block the lock is released; when that fails or
    finds no hold, the connection is closed, which ends the session and its
    locks. That failure never replaces an exception leaving the block (it
    is added to it as a note), and is not raised on a clean exit. On any
    other database than PostgreSQL the block runs without a lock."""
    alias = _alias(using)
    connection = connections[alias]
    if connection.vendor != "postgresql":
        yield
        return
    if not _keyed(release_id):
        raise RunLocked(release_id, KEY)
    # PostgreSQL would grant the lock again to the session that holds it.
    if _holds(connection, release_id):
        raise RunLocked(release_id, NESTED)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", [RUN_LOCK_CLASS, release_id]
        )
        if not cursor.fetchone()[0]:
            raise RunLocked(release_id, HELD)
    try:
        yield
    except BaseException as error:
        _release(alias, release_id, error)
        raise
    _release(alias, release_id, None)


def run_lock_holder(release_id, *, using=None) -> int | None:
    """The pid of the backend that holds the run lock of release
    `release_id` in this database, or None. None on any other database than
    PostgreSQL: callers check runs_are_locked() first. Takes no lock."""
    connection = connections[_alias(using)]
    if connection.vendor != "postgresql" or not _keyed(release_id):
        return None
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT pid {_GRANTED}", [RUN_LOCK_CLASS, release_id])
        row = cursor.fetchone()
    return None if row is None else row[0]


def run_lock_holders(*, using=None) -> dict[int, int]:
    """{release id: the pid of the backend that holds its run lock}, for
    every run lock held in this database, from one read of pg_locks; {} on
    any other database than PostgreSQL. Takes no lock."""
    connection = connections[_alias(using)]
    if connection.vendor != "postgresql":
        return {}
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT objid, pid {_HELD}", [RUN_LOCK_CLASS])
        return {int(release_id): pid for release_id, pid in cursor.fetchall()}


def waiting_backends(pids, *, using=None) -> set[int]:
    """The backends among `pids` that wait on a lock now: pg_locks lists a
    lock of theirs that is not granted (a row another transaction holds,
    say). An empty set on any other database than PostgreSQL. Takes no
    lock."""
    pids = sorted(set(pids))
    connection = connections[_alias(using)]
    if connection.vendor != "postgresql" or not pids:
        return set()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT pid FROM pg_locks WHERE NOT granted AND pid = ANY(%s)",
            [pids],
        )
        return {pid for (pid,) in cursor.fetchall()}


def run_lock_lost(release_id, *, using=None) -> bool:
    """Whether this connection no longer holds the run lock of release
    `release_id`, which it took (release_run_lock): its session ended (a
    terminated backend, a lost connection), and Django, which closes a
    connection whose rollback fails, may since have connected again as a
    new session, which holds nothing. A run's holder writes nothing more of
    the run once it is lost: another process may hold the lock by then. A
    check that cannot run counts as lost. False on any other database than
    PostgreSQL, which has no lock to lose."""
    connection = connections[_alias(using)]
    if connection.vendor != "postgresql":
        return False
    try:
        return not _holds(connection, release_id)
    except (DatabaseError, InterfaceError):
        return True


def end_dead_runs(release_id, *, found_by, using=None) -> list[int]:
    """Mark failed every run of the release that is still running: called
    by a commit or a discard that holds the release's run lock, so no live
    process runs one, and each was left running by a process that ended.
    `found_by` completes "found when ... started" ("a commit of release
    12"). Returns their pks, in pk order.

    Raises RuntimeError, writing nothing, unless this connection holds the
    release's run lock: calling it otherwise is the caller's mistake. On any
    other database than PostgreSQL there is no lock to tell a live process
    by: it marks nothing and returns []."""
    alias = _alias(using)
    connection = connections[alias]
    if connection.vendor != "postgresql":
        return []
    if not _holds(connection, release_id):
        raise RuntimeError(
            f"end_dead_runs is called by the holder of the run lock of release "
            f"{release_id} (release_run_lock), and this connection does not hold it."
        )
    error = f"Its process ended before the run finished; found when {found_by} started."
    ended = []
    with transaction.atomic(using=alias):
        # The release before its runs: the order every writer of a run
        # locks them in.
        DatasetRelease.objects.using(alias).select_for_update().filter(
            pk=release_id
        ).first()
        runs = (
            ImportRun.objects.using(alias)
            .select_for_update()
            .filter(release_id=release_id, state=ImportState.RUNNING)
            .order_by("pk")
        )
        for run in runs:
            run.state = ImportState.FAILED
            run.error = error
            if run.finished_at is None:
                run.finished_at = timezone.now()
            run.save(using=alias, update_fields=["state", "error", "finished_at"])
            ended.append(run.pk)
    return ended


def _alias(using) -> str:
    return using or router.db_for_write(ImportRun)


def _keyed(release_id) -> bool:
    """Whether a run lock keys this release id: an int from 1 to
    MAX_RUN_LOCK_KEY."""
    return (
        isinstance(release_id, int)
        and not isinstance(release_id, bool)
        and 1 <= release_id <= MAX_RUN_LOCK_KEY
    )


def _holds(connection, release_id) -> bool:
    """Whether this connection's session holds the release's run lock."""
    if not _keyed(release_id):
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT EXISTS (SELECT 1 {_GRANTED} AND pid = pg_backend_pid())",
            [RUN_LOCK_CLASS, release_id],
        )
        return cursor.fetchone()[0]


def _release(alias, release_id, leaving):
    """Release the lock. When the unlock raises (an aborted transaction, a
    lost connection) or finds no hold, close the connection: that ends the
    session, and with it every lock it holds. Never raises: `leaving`, the
    exception leaving the block, if any, gets a note instead."""
    connection = connections[alias]
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)", [RUN_LOCK_CLASS, release_id]
            )
            if cursor.fetchone()[0]:
                return
        problem = "this session held no such lock"
    except (DatabaseError, InterfaceError) as error:
        problem = f"{type(error).__name__}: {str(error).strip()}"
    try:
        connection.close()
    except (DatabaseError, InterfaceError):
        # The session is lost already, and its locks with it.
        pass
    if leaving is not None:
        leaving.add_note(
            f"The run lock of release {release_id} was not released ({problem}), "
            "so its connection was closed, which ends the lock."
        )
