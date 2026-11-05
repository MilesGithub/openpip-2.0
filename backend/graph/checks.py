"""graph_check (G5): what in the typed graph needs an operator. Read only:
it takes no lock and writes nothing.

run_checks gives ten sections, in this order. The first five are problems,
which make graph_check exit 1; the last five are entries for review, which
it lists and which never change its exit status.

Problems:

1. registry: the database's and the code's registry versions, and what
   graph_sync_registry would change, refuse or keep that the JSON lacks
   (unknown slugs, both ways); a registry JSON error is a finding too.
   Nothing marks a registry row deprecated yet (Term.obsolete exists, and
   nothing sets it).
2. files: every release file whose stored copy is missing or changed
   (storage.stored_problem, which reads every stored byte: minutes per
   10 GiB).
3. unnamed_files: what lies under the storage root that no row names:
   every entry of incoming/, a releases/<id> directory naming no release,
   a <sha256> directory naming no file of its release, anything else not
   shaped releases/<id>/<sha256>/content, and any symbolic link or special
   file, which is listed and never followed. A storage root that does not
   exist yet holds nothing. A copy that a registration is storing right
   now looks the same as one left behind. Nothing is deleted.
4. runs: on PostgreSQL, the runs left running by a process that ended:
   running, and not the latest running run of a release whose run lock a
   backend holds (a holder takes the lock before it inserts or resumes its
   run, so until it has, a run left running can be that latest one, which
   live_runs names); elsewhere, every running run, since nothing tells
   whether its process is alive; and every open failed commit run, which
   keeps its release from being dry-run until it is resumed or discarded,
   but while its release's run lock is held (live_runs names it then). The
   runs are read before the locks, and a run found dead is read again
   before it is listed, so one that finished meanwhile is not.
5. approvals: the active approvals relying on a licence snapshot that is
   no longer their release's latest (approvals.stale_approvals), except a
   withdrawn release's, which is never effective.

For review:

6. publication_conflicts: the publication identifiers commits did not
   write, as their reports list them, distinct by publication, field and
   value; then the papers local to a release's dataset that keep another
   title or year than its manifest states, distinct by publication, title
   and year. A run's list that was cut short says so.
7. term_labels: the labels a commit stated for a term that keeps another,
   as their reports list them, distinct by CURIE and label; likewise.
8. merge_candidates: a primary identifier of one entity that another
   entity of the same type and taxon holds in another role.
9. provisional_entities: provisional entities that no assertion's
   participant names, which a discard leaves by design.
10. live_runs: on PostgreSQL, each held run lock with the backend that
    holds it: the release's run under way, or, while a commit reads its
    file before it starts or resumes its run, or a discard runs, the
    release, with its failed commit run if it has one. A run's line says
    when its backend waits on a lock: a holder that has not started or
    resumed its own run yet may be waiting for the release's row, and the
    run named is then one a process that ended left running.

Each section keeps at most `limit` lines; its total is counted apart, and
each query reads only the lines it shows where the rows allow it.
"""

import os
import re
from dataclasses import dataclass

from django.db.models import Exists, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from . import registry, storage
from .approvals import stale_approvals
from .ingest.runlock import run_lock_holders, runs_are_locked, waiting_backends
from .management.commands.graph_sync_registry import plan_sync
from .models import (
    AssertionParticipant,
    DatasetRelease,
    Entity,
    EntityIdentifier,
    EntityStatus,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    SHA256_HEX,
    ReleaseStatus,
    SourceFile,
)
from .preconditions import require_switch

PROBLEM = "problem"
REVIEW = "review"
# The lines a section shows by default.
LIMIT = 20
# The taxon an entity without one compares as: no taxid is 0.
_NO_TAXON = 0
_DIGITS = re.compile(r"[0-9]+")


class CheckError(Exception):
    """The checks did not run: the switch is off. Nothing was read."""


@dataclass(frozen=True)
class Section:
    """One section of graph_check: its name, its title, its kind (PROBLEM
    or REVIEW), at most `limit` lines, and how many findings it has."""

    name: str
    title: str
    kind: str
    lines: tuple
    total: int


class _Found:
    """A section's findings as they are found: the first `limit` lines, and
    their count."""

    def __init__(self, limit):
        self.limit = limit
        self.lines = []
        self.total = 0

    def add(self, line):
        self.total += 1
        if len(self.lines) < self.limit:
            self.lines.append(line)

    def section(self, name, title, kind) -> Section:
        return Section(name, title, kind, tuple(self.lines), self.total)


def run_checks(*, limit=LIMIT) -> list[Section]:
    """The ten sections, in order. Raises CheckError, reading nothing, while
    the switch is off: approvals.stale_approvals() is empty then, which
    would mislead."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit is a number of lines, not {limit!r}.")
    require_switch(CheckError, "the graph is not checked")
    runs, live = _runs(limit)
    return [
        _registry(limit),
        _files(limit),
        _unnamed_files(limit),
        runs,
        _approvals(limit),
        _reported(
            "publication_conflicts",
            "Publication identifiers, and paper titles and years, that commits did "
            "not write",
            [
                (
                    "publication_conflicts",
                    _conflict_key,
                    _conflict_line,
                    "publication-identifier conflicts",
                ),
                (
                    "paper_differences",
                    _paper_key,
                    _paper_line,
                    "papers that keep another title or year",
                ),
            ],
            limit,
        ),
        _reported(
            "term_labels",
            "Terms that keep another label than a manifest stated",
            [("term_labels", _label_key, _label_line, "term labels")],
            limit,
        ),
        _merge_candidates(limit),
        _provisional_entities(limit),
        live,
    ]


# ── Problems ────────────────────────────────────────────────────────────────


def _registry(limit) -> Section:
    state = registry.registry_state()
    found = _Found(limit)
    try:
        loaded = registry.load_registry()
    except registry.RegistryError as error:
        for problem in str(error).splitlines():
            if problem.strip():
                found.add(f"The registry JSON is invalid: {problem.strip()}")
    else:
        plan = plan_sync(loaded)
        for line in plan.lines + plan.refusals + plan.missing:
            found.add(line)
    return found.section(
        "registry",
        f"Registry rows against the code's JSON (version {state.code} in the code, "
        f"{state.database or 'none'} in the database)",
        PROBLEM,
    )


def _files(limit) -> Section:
    found = _Found(limit)
    rows = SourceFile.objects.order_by("pk").only(
        "pk", "release_id", "role", "original_name", "storage_path", "size", "sha256"
    )
    for row in rows.iterator():
        problem = storage.stored_problem(row)
        if problem is not None:
            found.add(
                f"Release {row.release_id}: {_clause(problem)}; registering its "
                "files again restores the copy."
            )
    return found.section(
        "files", "Release files whose stored copy is missing or changed", PROBLEM
    )


def _unnamed_files(limit) -> Section:
    found = _Found(limit)
    _walk_storage(found)
    return found.section(
        "unnamed_files", "Entries under the storage root that no row names", PROBLEM
    )


def _walk_storage(found):
    """What the storage root holds that no row names, never following a
    symbolic link."""
    try:
        base = storage.root()
    except storage.StorageError:
        # Nothing is stored without a root, so nothing is left behind.
        return
    if not os.path.lexists(base):
        return
    if not base.is_dir():
        found.add(f"The storage root {base} is not a directory.")
        return
    release_ids = set(DatasetRelease.objects.values_list("pk", flat=True))
    files = set(SourceFile.objects.values_list("release_id", "sha256"))
    for entry in _entries(base, "", found):
        if entry.name == storage.INCOMING and _is_directory(entry):
            for item in _entries(entry.path, storage.INCOMING, found):
                found.add(
                    f"{storage.INCOMING}/{item.name}: {_kind(item)} in "
                    f"{storage.INCOMING}/, where a registration copies a file "
                    "before it is placed, and which no row names."
                )
        elif entry.name == storage.RELEASES and _is_directory(entry):
            _walk_releases(entry, release_ids, files, found)
        elif entry.name in (storage.INCOMING, storage.RELEASES):
            # Not a directory (the branches above take those): it is what
            # should be one.
            found.add(
                f"{entry.name}: {_kind(entry)}, where the storage root's "
                f"{entry.name}/ directory belongs."
            )
        else:
            found.add(
                f"{entry.name}: {_kind(entry)}, beside {storage.INCOMING}/ and "
                f"{storage.RELEASES}/, the only entries of the storage root."
            )


def _walk_releases(directory, release_ids, files, found):
    for release in _entries(directory.path, storage.RELEASES, found):
        path = f"{storage.RELEASES}/{release.name}"
        name = release.name
        if not (
            _is_directory(release)
            and _DIGITS.fullmatch(name)
            and str(int(name)) == name
        ):
            found.add(f"{path}: {_kind(release)}, not a release's directory.")
            continue
        release_id = int(name)
        if release_id not in release_ids:
            found.add(
                f"{path}: the directory of release {release_id}, which does not "
                "exist."
            )
            continue
        for stored in _entries(release.path, path, found):
            where = f"{path}/{stored.name}"
            if not (_is_directory(stored) and SHA256_HEX.fullmatch(stored.name)):
                found.add(f"{where}: {_kind(stored)}, not a stored file's directory.")
            elif (release_id, stored.name) not in files:
                found.add(
                    f"{where}: a stored file's directory, and no file of release "
                    f"{release_id} has this SHA-256."
                )
            else:
                _walk_stored(stored, where, found)


def _walk_stored(directory, path, found):
    """A named file's directory holds its content, a regular file, alone;
    a missing content is the files section's."""
    for item in _entries(directory.path, path, found):
        where = f"{path}/{item.name}"
        if item.name != storage.CONTENT:
            found.add(f"{where}: {_kind(item)} beside a stored file's content.")
        elif item.is_symlink() or not item.is_file(follow_symlinks=False):
            found.add(f"{where}: {_kind(item)}, where a stored file's content is.")


def _entries(path, shown, found) -> list:
    """The entries of a directory, by name; none when it cannot be read,
    which is a finding."""
    try:
        with os.scandir(path) as entries:
            return sorted(entries, key=lambda entry: entry.name)
    except OSError as error:
        found.add(
            f"{shown or 'The storage root'}: cannot be read "
            f"({error.strerror or error})."
        )
        return []


def _is_directory(entry) -> bool:
    return not entry.is_symlink() and entry.is_dir(follow_symlinks=False)


def _kind(entry) -> str:
    if entry.is_symlink():
        return "a symbolic link, not followed"
    if entry.is_dir(follow_symlinks=False):
        return "a directory"
    if entry.is_file(follow_symlinks=False):
        return "a file"
    return "a special file"


def _runs(limit) -> tuple[Section, Section]:
    """The runs and live_runs sections. The running runs are read first,
    then the run locks, in one read. A holder takes its release's run lock
    before it inserts or resumes its run, and a release's runs are inserted
    in pk order (each insert locks the release first), so of a held
    release's running runs only the latest can be its holder's: every other
    running run was left by a process that ended, and is read again before
    it is listed, so one that finished meanwhile is not. A failed commit run
    whose release's lock is held while no run of it is running is named
    under live_runs, not runs: a commit resuming it or a discard giving it
    up holds the lock, and both commands are refused until it returns.

    The latest running run of a held release is its holder's only once the
    holder has started or resumed its own run: a dry run inserts it, and a
    commit or a discard first marks the release's dead runs failed
    (end_dead_runs). Both lock the release's row first, so they wait for as
    long as another session holds that row (an approval or a publication
    under way, a session left open in a transaction). Until then, a run
    that a process which ended left running is named under live_runs, held
    by the waiting holder, and not under runs; its line says that the
    backend waits on a lock (waiting_backends, a read of pg_locks). That is
    no proof: a commit's batch waiting on another commit's rows shows the
    same, so the run stays an entry for review."""
    running = list(
        ImportRun.objects.filter(state=ImportState.RUNNING)
        .order_by("pk")
        .values_list("pk", "release_id", "mode")
    )
    found, live = _Found(limit), _Found(limit)
    locked = runs_are_locked()
    holders = run_lock_holders() if locked else {}
    # {release id: (pk, mode)}: each held release's latest running run.
    latest = {}
    for pk, release_id, mode in running:
        if release_id in holders:
            # In pk order: the last is the latest.
            latest[release_id] = (pk, mode)
    if locked:
        dead = [run for run in running if latest.get(run[1], (None,))[0] != run[0]]
        # Read again: a run that finished since it was read is not dead.
        still = set(
            ImportRun.objects.filter(
                pk__in=[pk for pk, _, _ in dead], state=ImportState.RUNNING
            ).values_list("pk", flat=True)
        )
        for pk, release_id, mode in dead:
            if pk not in still:
                continue
            then = ""
            if mode == ImportMode.COMMIT:
                then = (
                    ", then resume it with graph_import --commit or discard it with "
                    "graph_discard_release"
                )
            found.add(
                f"Run {pk} ({_mode(mode)}) of release {release_id} was left running "
                "by a process that ended: the release's next commit or discard marks "
                f"it failed{then}."
            )
    else:
        for pk, release_id, mode in running:
            found.add(
                f"Run {pk} ({_mode(mode)}) of release {release_id} is running; "
                "whether its process is alive cannot be told on this database."
            )
    failed = ImportRun.objects.filter(mode=ImportMode.COMMIT, state=ImportState.FAILED)
    # {release id: pk}: the failed commit runs that the holder of their
    # release's run lock is resuming or giving up (one per release:
    # graph_run_one_open_commit).
    waiting = dict(
        failed.filter(
            release_id__in=[release for release in holders if release not in latest]
        ).values_list("release_id", "pk")
    )
    failed = failed.exclude(pk__in=list(waiting.values()))
    total = failed.count()
    shown = failed.order_by("pk").values_list("pk", "release_id", "checkpoint", "error")
    for pk, release_id, checkpoint, error in shown[: max(limit - len(found.lines), 0)]:
        line = checkpoint.get("line", 0) if isinstance(checkpoint, dict) else 0
        found.lines.append(
            f"Run {pk} (a commit) of release {release_id} failed, with its checkpoint "
            f"at line {line} ({_clause(_first_line(error)) or 'no error recorded'}): "
            "resume it (graph_import --commit) or discard it (graph_discard_release); "
            "until then the release cannot be dry-run."
        )
    found.total += total
    # The holders that wait on a lock: one may not have started its run yet.
    waits = waiting_backends(holders[release] for release in latest)
    for release_id in sorted(holders):
        pid = holders[release_id]
        if release_id in latest:
            pk, mode = latest[release_id]
            line = (
                f"Run {pk} ({_mode(mode)}) of release {release_id} is running, held "
                f"by backend {pid}"
            )
            if pid in waits:
                line += (
                    ", which waits on a lock: if that backend has not started or "
                    f"resumed its own run yet, run {pk} was left running by a process "
                    "that ended, and the release's next commit or discard marks it "
                    "failed"
                )
            live.add(f"{line}.")
        elif release_id in waiting:
            live.add(
                f"Release {release_id}'s run lock is held by backend {pid}, and its "
                f"commit run {waiting[release_id]} is failed: a commit resuming it, "
                "or a discard giving it up."
            )
        else:
            live.add(
                f"Release {release_id}'s run lock is held by backend {pid}, and no run "
                "of it is running: a commit reading its file before it starts or "
                "resumes its run, or a discard."
            )
    return (
        found.section(
            "runs",
            "Runs left running by a process that ended, and failed commit runs",
            PROBLEM,
        ),
        live.section(
            "live_runs",
            "Run locks held, each with its backend and the release's run under way",
            REVIEW,
        ),
    )


def _mode(mode) -> str:
    return "a commit" if mode == ImportMode.COMMIT else "a dry run"


def _approvals(limit) -> Section:
    # A withdrawn release's approval is never effective, and is never
    # refreshed.
    stale = stale_approvals().exclude(release__status=ReleaseStatus.WITHDRAWN)
    found = _Found(limit)
    found.total = stale.count()
    for row in stale.values(
        "pk",
        "release_id",
        "release__is_synthetic",
        "scope",
        "decision",
        "licence_id",
        "latest_licence",
    )[:limit]:
        pk, latest = row["pk"], row["latest_licence"]
        # A synthetic release has no due diligence to confirm (G2).
        diligence = (
            ""
            if row["release__is_synthetic"]
            else ", once the dataset's due diligence is confirmed after snapshot "
            f"{latest}"
        )
        found.lines.append(
            f"Decision {pk} ({row['scope']} {row['decision']}) of release "
            f"{row['release_id']} relies on licence snapshot {row['licence_id']}, and "
            f"the latest is {latest}: refresh it with graph_approve --replace {pk} "
            f"(with --evidence for a public scope{diligence}), or revoke it with "
            f"graph_revoke --approval {pk} if the latest snapshot no longer allows it."
        )
    return found.section(
        "approvals",
        "Approvals relying on an older licence snapshot than their release's latest",
        PROBLEM,
    )


# ── For review ──────────────────────────────────────────────────────────────


def _reported(name, title, lists, limit) -> Section:
    """Lists that commit runs keep in their reports, one after the other:
    each (its report key, key_of, line_of, what it counts) is read by its
    key alone, its entries distinct by key_of, in run order; a run whose
    list was cut short adds a line saying so."""
    found = _Found(limit)
    for listed_as, key_of, line_of, what in lists:
        seen = set()
        runs = (
            ImportRun.objects.filter(mode=ImportMode.COMMIT)
            .order_by("pk")
            .values_list("pk", "release_id", f"report__{listed_as}")
        )
        for run, release_id, listed in runs.iterator():
            if not isinstance(listed, dict):
                continue
            entries = [entry for entry in listed.get("entries") or [] if _entry(entry)]
            for entry in entries:
                key = key_of(entry)
                if key not in seen:
                    seen.add(key)
                    found.add(line_of(entry, run, release_id))
            total = listed.get("total")
            if isinstance(total, int) and total > len(entries):
                found.add(
                    f"Run {run} of release {release_id} found {total:,} {what} and "
                    f"its report keeps {len(entries):,}: the others are not listed "
                    "here."
                )
    return found.section(name, title, REVIEW)


def _entry(entry) -> bool:
    return isinstance(entry, dict)


def _conflict_key(entry):
    return (entry.get("publication"), entry.get("field"), entry.get("value"))


def _conflict_line(entry, run, release_id) -> str:
    field, value = entry.get("field"), entry.get("value")
    held = []
    if entry.get("current"):
        held.append(f"it holds {field} {entry['current']}")
    if entry.get("holder") is not None:
        held.append(f"publication {entry['holder']} holds this {field}")
    why = f" ({' and '.join(held)})" if held else ""
    return (
        f"Publication {entry.get('publication')} was not given {field} {value}{why}: "
        f"release {release_id}, line {entry.get('line')}, run {run}."
    )


def _paper_key(entry):
    return (
        entry.get("publication"),
        entry.get("manifest_title"),
        entry.get("manifest_year"),
    )


def _paper_line(entry, run, release_id) -> str:
    kept = _title_and_year(entry.get("stored_title"), entry.get("stored_year"))
    stated = _title_and_year(entry.get("manifest_title"), entry.get("manifest_year"))
    return (
        f"Publication {entry.get('publication')}, the paper {entry.get('key')!r} of "
        f"release {release_id}'s dataset, keeps {kept}, and release {release_id}'s "
        f"manifest states {stated}: line {entry.get('line')}, run {run}."
    )


def _title_and_year(title, year) -> str:
    return f"the title {title!r} and " + (
        f"the year {year}" if year is not None else "no year"
    )


def _label_key(entry):
    return (entry.get("curie"), entry.get("stated"))


def _label_line(entry, run, release_id) -> str:
    return (
        f"Term {entry.get('curie')} keeps the label {entry.get('kept')!r}, and "
        f"release {release_id}'s manifest states {entry.get('stated')!r}: line "
        f"{entry.get('line')}, run {run}."
    )


def _merge_candidates(limit) -> Section:
    holders = (
        EntityIdentifier.objects.filter(
            role=IdentifierRole.PRIMARY,
            namespace_id=OuterRef("namespace_id"),
            lookup=OuterRef("lookup"),
            scope_key=OuterRef("scope_key"),
            entity__entity_type_id=OuterRef("entity__entity_type_id"),
        )
        .exclude(entity_id=OuterRef("entity_id"))
        .annotate(taxon_key=_taxon_key())
        .filter(taxon_key=OuterRef("taxon_key"))
        .values("entity_id")[:1]
    )
    candidates = (
        EntityIdentifier.objects.exclude(role=IdentifierRole.PRIMARY)
        .annotate(taxon_key=_taxon_key())
        .annotate(primary_entity=Subquery(holders))
        .filter(primary_entity__isnull=False)
    )
    found = _Found(limit)
    found.total = candidates.count()
    rows = candidates.order_by("primary_entity", "entity_id", "pk").values_list(
        "primary_entity", "entity_id", "namespace_id", "value", "role"
    )
    for primary, other, namespace, value, role in rows[:limit]:
        found.lines.append(
            f"{namespace} {value} is the primary identifier of entity {primary} and "
            f"a {role} identifier of entity {other}, of the same type and taxon: "
            "candidates for a merge."
        )
    return found.section(
        "merge_candidates",
        "Primary identifiers that another entity of the same type and taxon holds",
        REVIEW,
    )


def _taxon_key():
    return Coalesce("entity__taxon_id", Value(_NO_TAXON), output_field=IntegerField())


def _provisional_entities(limit) -> Section:
    named = AssertionParticipant.objects.filter(entity_id=OuterRef("pk"))
    stated = AssertionParticipant.objects.filter(stated_entity_id=OuterRef("pk"))
    unnamed = Entity.objects.filter(status=EntityStatus.PROVISIONAL).filter(
        ~Exists(named), ~Exists(stated)
    )
    found = _Found(limit)
    found.total = unnamed.count()
    rows = unnamed.order_by("pk").values_list(
        "pk", "entity_type_id", "label", "identity_key", "created_by_release_id"
    )
    for pk, entity_type, label, key, release in rows[:limit]:
        created = (
            f"release {release} created it"
            if release is not None
            else "it was created without a release"
        )
        found.lines.append(
            f"Entity {pk} ({entity_type} {label!r}, {key}) is provisional and no "
            f"evidence names it; {created}."
        )
    return found.section(
        "provisional_entities",
        "Provisional entities that no evidence names",
        REVIEW,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _clause(text) -> str:
    """A message as part of a line: without "Nothing was read." and its
    final full stop."""
    text = text.strip().removesuffix("Nothing was read.").strip()
    return text[:-1] if text.endswith(".") else text


def _first_line(text) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""
