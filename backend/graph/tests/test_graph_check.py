"""graph_check (graph.checks and the command): its ten sections, each with
a finding and without; problems, which make it exit 1, against entries for
review, which never do; the storage walk, which never follows a link; the
runs read before the locks, a dead run under runs and a live one under
live_runs, with its backend: of a held release's running runs only the
latest, a held lock with no run running, and a failed commit run of a held
release named there rather than under runs; the latest running run of a
held release is its holder's only once the holder has started or resumed
its own run, which it does only once it can lock the release's row, so a
dead run named live while its holder waits for that row says that the
holder waits on a lock (a transaction=True test); a withdrawn release's stale
approval left out; the publication conflicts, paper differences and term
labels of commit reports, distinct, with a line for a list cut short; each
section's lines capped, with its total apart; and the switch off. It writes
nothing."""

import io
import os
import time

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from graph import checks
from graph import registry as registry_module
from graph.approvals import INTERNAL, PUBLIC_VIEW, revoke
from graph.checks import PROBLEM, REVIEW, CheckError, run_checks
from graph.entities import add_identifier, create_entity
from graph.ingest.commit import COMMITTED, commit_release
from graph.ingest.dry_run import dry_run
from graph.ingest.runlock import run_lock_holder, runs_are_locked
from graph.models import (
    Entity,
    IdentifierNamespace,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    RegistryState,
    ReleaseStatus,
)
from graph.registry import REGISTRY_VERSION
from graph.releases import withdraw_release
from graph.storage import open_stored
from graph.tests.factories import (
    DatasetReleaseFactory,
    EntityFactory,
    IdentifierNamespaceFactory,
    ImportRunFactory,
    ReleaseLicenceFactory,
    StaffUserFactory,
    approvable_release,
    approve,
)
from graph.tests.ingest_helpers import GENE, committed, registered
from graph.tests.lock_helpers import WAIT_SECONDS, held_release_row, held_run_lock
from graph.tests.test_commit import table_contents
from graph.tests.test_commit_concurrency import Worker, waits_on_a_lock

NAMES = [
    "registry",
    "files",
    "unnamed_files",
    "runs",
    "approvals",
    "publication_conflicts",
    "term_labels",
    "merge_candidates",
    "provisional_entities",
    "live_runs",
]
SHA = "ab" * 32


def sections(**options) -> dict:
    found = run_checks(**options)
    assert [section.name for section in found] == NAMES
    return {section.name: section for section in found}


def check() -> list:
    """graph_check's output, lines."""
    out = io.StringIO()
    call_command("graph_check", stdout=out)
    return out.getvalue().splitlines()


def check_refused() -> tuple:
    """graph_check's output and the CommandError it ended with."""
    out = io.StringIO()
    with pytest.raises(CommandError) as raised:
        call_command("graph_check", stdout=out)
    return out.getvalue().splitlines(), str(raised.value)


def only(found, *names) -> dict:
    """The sections with findings: exactly these."""
    with_findings = {name: s for name, s in found.items() if s.total}
    assert sorted(with_findings) == sorted(names), with_findings
    return with_findings


def draft():
    return DatasetReleaseFactory()


# ── A graph with nothing to report ──────────────────────────────────────────


def test_a_clean_graph_has_no_problems_and_nothing_to_review(registry):
    found = sections()
    assert [found[name].kind for name in NAMES] == [PROBLEM] * 5 + [REVIEW] * 5
    for section in found.values():
        assert (section.lines, section.total) == ((), 0), section
    out = check()
    assert out[0] == "Problems (graph_check exits 1 while one is found):"
    assert out[1] == (
        f"Registry rows against the code's JSON (version {REGISTRY_VERSION} in the "
        f"code, {REGISTRY_VERSION} in the database) (registry): 0"
    )
    assert out[6] == "For review (listed, never a reason to exit 1):"
    assert out[-1] == "No problems, and nothing to review."
    assert len(out) == 13


def test_graph_check_writes_nothing(registry, tmp_path):
    release = registered(tmp_path)
    ImportRunFactory(release=release, file=release.files.first())
    EntityFactory()
    contents = table_contents()
    with pytest.raises(CommandError):
        call_command("graph_check", stdout=io.StringIO())
    run_checks()
    assert table_contents() == contents


def test_graph_check_refuses_while_the_switch_is_off(registry, settings):
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(CommandError) as raised:
        call_command("graph_check", stdout=io.StringIO())
    assert str(raised.value) == (
        "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so the graph "
        "is not checked. Nothing was written."
    )
    with pytest.raises(CheckError, match="TYPED_GRAPH_ENABLED"):
        run_checks()


def test_the_limit_is_a_number_of_lines(switch_on):
    for limit in (-1, True, 2.0, None):
        with pytest.raises(ValueError, match="limit is a number of lines"):
            run_checks(limit=limit)


# ── Problems against entries for review: the exit status ────────────────────


def test_problems_exit_1_and_entries_for_review_never_do(registry):
    # Entries for review alone: two provisional entities.
    first, second = EntityFactory(), EntityFactory()
    assert check()[-1] == "No problems; 2 entries are listed for review."
    # A problem as well: exit 1, naming both.
    RegistryState.objects.filter(pk=1).update(version="9.9")
    _, error = check_refused()
    assert error == "graph_check found 1 problem; 2 entries are listed for review."
    # Problems alone.
    Entity.objects.filter(pk__in=[first.pk, second.pk]).delete()
    IdentifierNamespaceFactory(slug="test_unknown", curie_prefix="test-unknown")
    _, error = check_refused()
    assert error == "graph_check found 2 problems; 0 entries are listed for review."
    # One entry for review, and no problem.
    RegistryState.objects.filter(pk=1).update(version=REGISTRY_VERSION)
    IdentifierNamespace.objects.filter(slug="test_unknown").delete()
    EntityFactory()
    assert check()[-1] == "No problems; 1 entry is listed for review."


def test_each_section_shows_its_total_and_at_most_its_limit_of_lines(registry):
    entities = [EntityFactory() for _ in range(22)]
    found = sections(limit=2)
    section = found["provisional_entities"]
    assert (len(section.lines), section.total) == (2, 22)
    assert section.lines[0].startswith(f"Entity {entities[0].pk} (gene ")
    assert sections(limit=0)["provisional_entities"].lines == ()
    out = check()
    at = out.index(
        "Provisional entities that no evidence names (provisional_entities): 22"
    )
    shown = out[at + 1 : at + 22]
    assert [line.startswith("  Entity ") for line in shown[:20]] == [True] * 20
    assert shown[20] == "  and 2 more"


def test_every_section_keeps_at_most_its_limit_of_lines(
    registry, tmp_path, graph_files_root
):
    # Two findings or more in every section.
    RegistryState.objects.filter(pk=1).update(version="9.9")
    IdentifierNamespaceFactory(slug="test_unknown", curie_prefix="test-unknown")
    release = registered(tmp_path)
    for file in release.files.all():
        (graph_files_root / file.storage_path).unlink()
    for name in ("file-a.part", "file-b.part"):
        (graph_files_root / "incoming" / name).write_bytes(b"left")
    for _ in range(2):
        ImportRunFactory(release=release, file=release.files.first())
    approve(release, PUBLIC_VIEW)
    ReleaseLicenceFactory(release=release, open_terms=True)
    commit_run(
        draft(),
        publication_conflicts=listed(
            conflict(4, "10.1/a", 2), conflict(5, "10.1/b", 3)
        ),
        term_labels=listed(label("MI:0018", "A", 2), label("MI:0915", "B", 3)),
    )
    other = create_entity("gene", "Other gene", 9606, "hgnc", "HGNC:7")
    for value, label_ in (("HGNC:11998", "TP53"), ("HGNC:6973", "MDM2")):
        create_entity("gene", label_, 9606, "hgnc", value)
        add_identifier(other, "hgnc", value, IdentifierRole.SECONDARY)
    live = [registered(tmp_path / f"live-{n}") for n in range(2)]
    for each in live:
        ImportRunFactory(release=each, file=each.files.first())
    if runs_are_locked():
        with held_run_lock(live[0].pk), held_run_lock(live[1].pk):
            found = sections(limit=1)
    else:
        found = sections(limit=1)
    for name, section in found.items():
        if name == "live_runs" and not runs_are_locked():
            continue
        assert len(section.lines) == 1, name
        assert section.total >= 2, name


# ── 1. registry ─────────────────────────────────────────────────────────────


def test_the_registry_section_lists_what_a_sync_would_change(registry):
    RegistryState.objects.filter(pk=1).update(version="9.9")
    IdentifierNamespaceFactory(slug="test_unknown", curie_prefix="test-unknown")
    found = only(sections(), "registry")
    section = found["registry"]
    assert section.title == (
        f"Registry rows against the code's JSON (version {REGISTRY_VERSION} in the "
        "code, 9.9 in the database)"
    )
    assert section.lines == (
        f"record registry version {REGISTRY_VERSION} (the database has 9.9)",
        "missing from the JSON: namespace test_unknown (kept; the sync never "
        "deletes)",
    )
    assert section.total == 2


def test_an_invalid_registry_json_is_a_finding(registry, monkeypatch):
    def invalid(directory=None):
        raise registry_module.RegistryError("taxa.json: broken\nnamespaces.json: too")

    monkeypatch.setattr(registry_module, "load_registry", invalid)
    section = only(sections(), "registry")["registry"]
    assert section.lines == (
        "The registry JSON is invalid: taxa.json: broken",
        "The registry JSON is invalid: namespaces.json: too",
    )


# ── 2. files ────────────────────────────────────────────────────────────────


def test_a_stored_copy_that_is_missing_or_changed_is_a_problem(
    registry, tmp_path, graph_files_root
):
    release = registered(tmp_path)
    data = release.files.get(role="data")
    manifest = release.files.get(role="manifest")
    (graph_files_root / data.storage_path).unlink()
    stored = graph_files_root / manifest.storage_path
    stored.write_bytes(b"x" * manifest.size)
    found = only(sections(), "files")
    section = found["files"]
    assert section.total == 2
    assert section.lines[0] == (
        f"Release {release.pk}: The stored copy of source file {data.pk} "
        "('release.tsv') is missing; registering its files again restores the copy."
    )
    assert section.lines[1].startswith(
        f"Release {release.pk}: The stored copy of source file {manifest.pk} "
        "('manifest.json') has SHA-256 "
    )
    assert section.lines[1].endswith(
        f"and its record {manifest.sha256}; registering its files again restores "
        "the copy."
    )


def test_stored_copies_that_match_are_no_finding(registry, tmp_path):
    release = registered(tmp_path)
    for file in release.files.all():
        open_stored(file).close()
    assert sections()["files"].total == 0


# ── 3. unnamed_files: the storage walk ──────────────────────────────────────


def test_the_storage_walk_lists_what_no_row_names_and_follows_no_link(
    registry, tmp_path, graph_files_root
):
    release = registered(tmp_path)
    data = release.files.get(role="data")
    root = graph_files_root
    (root / "incoming").mkdir(exist_ok=True)
    (root / "incoming" / "file-left.part").write_bytes(b"left behind")
    (root / "notes.txt").write_text("not here")
    (root / "releases" / "999999").mkdir()
    (root / "releases" / "0012").mkdir()
    (root / "releases" / "loose").write_text("not a directory")
    unnamed = root / "releases" / str(release.pk) / SHA
    unnamed.mkdir()
    (unnamed / "content").write_bytes(b"no row names it")
    named = root / data.storage_path
    (named.parent / "extra").write_text("beside the content")
    # A link to a directory holding what would be findings if it were
    # followed.
    outside = tmp_path / "outside"
    (outside / "incoming").mkdir(parents=True)
    (outside / "incoming" / "followed.part").write_text("never read")
    os.symlink(outside, root / "stray")
    os.symlink(outside, root / "releases" / str(release.pk) / ("cd" * 32))
    found = only(sections(), "unnamed_files")
    section = found["unnamed_files"]
    # Each directory's entries by name, so a release's own directory sorts
    # among the others by its id's digits.
    assert sorted(section.lines) == sorted(
        [
            "incoming/file-left.part: a file in incoming/, where a registration copies "
            "a file before it is placed, and which no row names.",
            "notes.txt: a file, beside incoming/ and releases/, the only entries of the "
            "storage root.",
            "releases/0012: a directory, not a release's directory.",
            "releases/999999: the directory of release 999999, which does not exist.",
            f"releases/{release.pk}/{SHA}: a stored file's directory, and no file of "
            f"release {release.pk} has this SHA-256.",
            f"releases/{release.pk}/{'cd' * 32}: a symbolic link, not followed, not a "
            "stored file's directory.",
            f"releases/{release.pk}/{data.sha256}/extra: a file beside a stored file's "
            "content.",
            "releases/loose: a file, not a release's directory.",
            "stray: a symbolic link, not followed, beside incoming/ and releases/, the "
            "only entries of the storage root.",
        ]
    )
    # The root's entries come first by name, then each directory's.
    assert section.lines[:2] == (
        "incoming/file-left.part: a file in incoming/, where a registration copies "
        "a file before it is placed, and which no row names.",
        "notes.txt: a file, beside incoming/ and releases/, the only entries of the "
        "storage root.",
    )
    assert section.lines[-1].startswith("stray: ")
    assert section.total == 9
    # Nothing was deleted.
    assert (root / "incoming" / "file-left.part").exists()
    assert (outside / "incoming" / "followed.part").exists()


def test_a_root_entry_named_incoming_or_releases_that_is_no_directory(
    registry, tmp_path, graph_files_root
):
    graph_files_root.mkdir(parents=True, exist_ok=True)
    (graph_files_root / "incoming").write_text("not a directory")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "999999").mkdir(parents=True)
    os.symlink(elsewhere, graph_files_root / "releases")
    section = only(sections(), "unnamed_files")["unnamed_files"]
    # The link is not followed: releases/999999 behind it is not listed.
    assert section.lines == (
        "incoming: a file, where the storage root's incoming/ directory belongs.",
        "releases: a symbolic link, not followed, where the storage root's "
        "releases/ directory belongs.",
    )


def test_a_link_or_special_file_in_place_of_a_content_is_listed(
    registry, tmp_path, graph_files_root
):
    release = registered(tmp_path)
    data = release.files.get(role="data")
    content = graph_files_root / data.storage_path
    content.unlink()
    os.symlink(tmp_path / "elsewhere", content)
    found = sections()
    assert found["unnamed_files"].lines == (
        f"releases/{release.pk}/{data.sha256}/content: a symbolic link, not followed, "
        "where a stored file's content is.",
    )
    # The files section says the copy cannot be read.
    assert found["files"].total == 1
    if hasattr(os, "mkfifo"):
        content.unlink()
        os.mkfifo(content)
        assert sections()["unnamed_files"].lines == (
            f"releases/{release.pk}/{data.sha256}/content: a special file, where a "
            "stored file's content is.",
        )


def test_a_storage_root_that_does_not_exist_yet_holds_nothing(
    switch_on, settings, tmp_path
):
    settings.GRAPH_FILES_ROOT = tmp_path / "not-yet"
    section = sections()["unnamed_files"]
    assert (section.lines, section.total) == ((), 0)


# ── 4. runs and 10. live_runs ───────────────────────────────────────────────


@pytest.mark.postgres
def test_a_dead_run_is_listed_under_runs_and_a_live_one_under_live_runs(
    registry, tmp_path
):
    dead = registered(tmp_path / "dead")
    dead_dry = ImportRunFactory(release=dead, file=dead.files.first())
    dead_commit = ImportRunFactory(
        release=dead, file=dead.files.first(), mode=ImportMode.COMMIT
    )
    live = registered(tmp_path / "live")
    live_run = ImportRunFactory(release=live, file=live.files.first())
    with held_run_lock(live.pk) as pid:
        found = sections()
    assert found["runs"].lines == (
        f"Run {dead_dry.pk} (a dry run) of release {dead.pk} was left running by a "
        "process that ended: the release's next commit or discard marks it failed.",
        f"Run {dead_commit.pk} (a commit) of release {dead.pk} was left running by a "
        "process that ended: the release's next commit or discard marks it failed, "
        "then resume it with graph_import --commit or discard it with "
        "graph_discard_release.",
    )
    assert found["live_runs"].lines == (
        f"Run {live_run.pk} (a dry run) of release {live.pk} is running, held by "
        f"backend {pid}.",
    )
    assert (found["runs"].kind, found["live_runs"].kind) == (PROBLEM, REVIEW)
    # Nothing was marked.
    assert ImportRun.objects.get(pk=dead_dry.pk).state == ImportState.RUNNING


@pytest.mark.postgres
def test_of_a_held_releases_running_runs_only_the_latest_is_live(registry, tmp_path):
    # A dry run marks no other run, so one a killed process left running can
    # sit beside a later dry run of its release that holds the run lock: a
    # holder takes the lock before it inserts its run, so the older run is
    # not the holder's.
    release = registered(tmp_path)
    dead = ImportRunFactory(release=release, file=release.files.first())
    live = ImportRunFactory(release=release, file=release.files.first())
    with held_run_lock(release.pk) as pid:
        found = sections()
        _, error = check_refused()
    assert found["runs"].lines == (
        f"Run {dead.pk} (a dry run) of release {release.pk} was left running by a "
        "process that ended: the release's next commit or discard marks it failed.",
    )
    assert found["live_runs"].lines == (
        f"Run {live.pk} (a dry run) of release {release.pk} is running, held by "
        f"backend {pid}.",
    )
    assert error == "graph_check found 1 problem; 1 entry is listed for review."


@pytest.mark.postgres
def test_a_held_run_lock_with_no_run_running_is_listed_with_its_backend(
    registry, tmp_path
):
    # A commit reading its file before it starts its run, or a discard.
    release = registered(tmp_path)
    with held_run_lock(release.pk) as pid:
        found = only(sections(), "live_runs")
        assert check()[-1] == "No problems; 1 entry is listed for review."
    assert found["live_runs"].lines == (
        f"Release {release.pk}'s run lock is held by backend {pid}, and no run of it "
        "is running: a commit reading its file before it starts or resumes its run, "
        "or a discard.",
    )
    assert sections()["live_runs"].total == 0


@pytest.mark.postgres
def test_a_failed_commit_run_is_no_problem_while_its_release_is_held(
    registry, tmp_path
):
    # A commit resuming it, or a discard giving it up, holds the release's run
    # lock: the advice to resume or discard it could not be followed until
    # it returns.
    release = registered(tmp_path)
    run = ImportRunFactory(
        release=release,
        file=release.files.first(),
        mode=ImportMode.COMMIT,
        state=ImportState.FAILED,
        checkpoint={"version": 1, "line": 3, "batches": 1},
        error="Line 4: Stopped.",
    )
    with held_run_lock(release.pk) as pid:
        found = only(sections(), "live_runs")
        assert check()[-1] == "No problems; 1 entry is listed for review."
    assert found["live_runs"].lines == (
        f"Release {release.pk}'s run lock is held by backend {pid}, and its commit "
        f"run {run.pk} is failed: a commit resuming it, or a discard giving it up.",
    )
    # Once the lock is released, it is a problem again.
    assert only(sections(), "runs")["runs"].lines == (
        f"Run {run.pk} (a commit) of release {release.pk} failed, with its checkpoint "
        "at line 3 (Line 4: Stopped): resume it (graph_import --commit) or discard "
        "it (graph_discard_release); until then the release cannot be dry-run.",
    )


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_dead_run_named_live_while_its_holder_waits_says_the_holder_waits(
    registry, tmp_path
):
    # A commit takes the run lock, then waits in end_dead_runs for the
    # release's row, which another session holds (an approval under way, a
    # session left open in a transaction). Until it gets the row, the dry run
    # a killed process left running is the release's latest running run, so
    # live_runs names it with the commit's backend, and says that this
    # backend waits on a lock.
    create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973")
    release = registered(tmp_path)
    dead = ImportRunFactory(release=release, file=release.files.first())
    staff = StaffUserFactory()
    assert dry_run(release, triggered_by=staff).state == ImportState.COMPLETED
    worker = None
    try:
        with held_release_row(release.pk):
            worker = Worker(
                "commit", lambda: commit_release(release, triggered_by=staff)
            )
            deadline = time.monotonic() + WAIT_SECONDS
            while not waits_on_a_lock(release.pk):
                assert worker.thread.is_alive(), repr(worker.error)
                assert time.monotonic() < deadline, "the commit never waited"
                time.sleep(0.05)
            pid = run_lock_holder(release.pk)
            found = sections()
    finally:
        if worker is not None:
            worker.join()
    assert found["runs"].total == 0
    assert found["live_runs"].lines == (
        f"Run {dead.pk} (a dry run) of release {release.pk} is running, held by "
        f"backend {pid}, which waits on a lock: if that backend has not started or "
        f"resumed its own run yet, run {dead.pk} was left running by a process that "
        "ended, and the release's next commit or discard marks it failed.",
    )
    # Once it had the row, the commit marked the run failed, and committed.
    assert worker.error is None, repr(worker.error)
    assert worker.result.outcome == COMMITTED
    dead.refresh_from_db()
    assert (dead.state, dead.error) == (
        ImportState.FAILED,
        "Its process ended before the run finished; found when a commit of release "
        f"{release.pk} started.",
    )


@pytest.mark.postgres
def test_a_run_that_finished_after_it_was_read_is_not_listed(
    registry, tmp_path, monkeypatch
):
    release = registered(tmp_path)
    run = ImportRunFactory(release=release, file=release.files.first())
    reading = checks.run_lock_holders

    def finishing(**kwargs):
        # The run completes, and its process ends, between the two readings.
        ImportRun.objects.filter(pk=run.pk).update(state=ImportState.COMPLETED)
        return reading(**kwargs)

    monkeypatch.setattr(checks, "run_lock_holders", finishing)
    found = sections()
    assert found["runs"].total == 0
    assert found["live_runs"].total == 0


def test_without_the_run_lock_every_running_run_is_listed(
    registry, tmp_path, monkeypatch
):
    if runs_are_locked():
        monkeypatch.setattr(checks, "runs_are_locked", lambda using=None: False)
    release = registered(tmp_path)
    run = ImportRunFactory(release=release, file=release.files.first())
    found = only(sections(), "runs")
    assert found["runs"].lines == (
        f"Run {run.pk} (a dry run) of release {release.pk} is running; whether its "
        "process is alive cannot be told on this database.",
    )


def test_an_open_failed_commit_run_is_listed_with_its_checkpoint_and_error(
    registry, tmp_path
):
    release = registered(tmp_path)
    run = ImportRunFactory(
        release=release,
        file=release.files.first(),
        mode=ImportMode.COMMIT,
        state=ImportState.FAILED,
        checkpoint={"version": 1, "line": 3, "batches": 1},
        error="Lines 4 to 5: the database stopped the batch: deadlock detected\nDETAIL",
    )
    # A finished commit run, and a failed dry run, are no finding.
    other = registered(tmp_path / "other")
    ImportRunFactory(
        release=other,
        file=other.files.first(),
        mode=ImportMode.COMMIT,
        state=ImportState.COMPLETED,
    )
    ImportRunFactory(
        release=other, file=other.files.first(), state=ImportState.FAILED, error="x"
    )
    found = only(sections(), "runs")
    assert found["runs"].lines == (
        f"Run {run.pk} (a commit) of release {release.pk} failed, with its checkpoint "
        "at line 3 (Lines 4 to 5: the database stopped the batch: deadlock "
        "detected): resume it (graph_import --commit) or discard it "
        "(graph_discard_release); until then the release cannot be dry-run.",
    )


# ── 5. approvals ────────────────────────────────────────────────────────────


def test_a_stale_approval_is_listed_with_how_to_refresh_it(switch_on, registry):
    release = approvable_release(status=ReleaseStatus.STAGED)
    [approval] = approve(release, INTERNAL)
    first = approval.licence_id
    assert sections()["approvals"].total == 0
    latest = ReleaseLicenceFactory(release=release, open_terms=True)
    found = only(sections(), "approvals")
    assert found["approvals"].lines == (
        f"Decision {approval.pk} (internal approved) of release {release.pk} relies "
        f"on licence snapshot {first}, and the latest is {latest.pk}: refresh it "
        f"with graph_approve --replace {approval.pk} (with --evidence for a public "
        "scope, once the dataset's due diligence is confirmed after snapshot "
        f"{latest.pk}), or revoke it with graph_revoke --approval {approval.pk} if "
        "the latest snapshot no longer allows it.",
    )
    # Revoked, it is no longer listed.
    revoke(approval, reason="Test.", revoked_by=StaffUserFactory())
    assert sections()["approvals"].total == 0


def test_a_synthetic_releases_stale_approval_advice_names_no_due_diligence(
    switch_on, registry
):
    # G2 asks no due diligence of a synthetic release, so the advice names none.
    release = approvable_release(status=ReleaseStatus.STAGED, is_synthetic=True)
    [approval] = approve(release, INTERNAL)
    first = approval.licence_id
    latest = ReleaseLicenceFactory(release=release, open_terms=True)
    found = only(sections(), "approvals")
    assert found["approvals"].lines == (
        f"Decision {approval.pk} (internal approved) of release {release.pk} relies "
        f"on licence snapshot {first}, and the latest is {latest.pk}: refresh it "
        f"with graph_approve --replace {approval.pk} (with --evidence for a public "
        f"scope), or revoke it with graph_revoke --approval {approval.pk} if the "
        "latest snapshot no longer allows it.",
    )


def test_a_withdrawn_releases_stale_approval_is_not_listed(switch_on, registry):
    release = approvable_release(status=ReleaseStatus.PUBLISHED)
    approve(release, INTERNAL)
    ReleaseLicenceFactory(release=release, open_terms=True)
    assert sections()["approvals"].total == 1
    withdraw_release(release, reason="Test.", withdrawn_by=StaffUserFactory())
    assert sections()["approvals"].total == 0


# ── 6. publication_conflicts and 7. term_labels ─────────────────────────────


def conflict(publication, value, line, holder=7, current=""):
    return {
        "publication": publication,
        "field": "doi",
        "value": value,
        "current": current,
        "holder": holder,
        "line": line,
    }


def label(curie, stated, line):
    return {"curie": curie, "stated": stated, "kept": "two hybrid", "line": line}


def listed(*entries, total=None):
    total = len(entries) if total is None else total
    return {
        "total": total,
        "truncated": total > len(entries),
        "entries": list(entries),
    }


def commit_run(release, **lists):
    return ImportRunFactory(
        release=release,
        file=None,
        mode=ImportMode.COMMIT,
        state=ImportState.COMPLETED,
        report={"counts": {}, **lists},
    )


def test_the_conflicts_and_labels_commits_reported_are_listed_once(switch_on, registry):
    first, second = draft(), draft()
    one = commit_run(
        first,
        publication_conflicts=listed(
            conflict(4, "10.1000/a", 2), conflict(5, "10.1000/b", 3, current="10.1/x")
        ),
        term_labels=listed(label("MI:0018", "two-hybrid", 2)),
    )
    two = commit_run(
        second,
        publication_conflicts=listed(
            conflict(4, "10.1000/a", 9),
            conflict(6, "10.1000/c", 4, holder=None, current="10.1/y"),
            total=1500,
        ),
        term_labels=listed(
            label("MI:0018", "two-hybrid", 5), label("MI:0018", "Y2H", 6), total=3
        ),
    )
    # A dry run's report is never read for them.
    ImportRunFactory(
        release=first,
        file=None,
        state=ImportState.COMPLETED,
        report={"publication_conflicts": listed(conflict(8, "10.1000/z", 2))},
    )
    found = only(sections(), "publication_conflicts", "term_labels")
    assert found["publication_conflicts"].lines == (
        f"Publication 4 was not given doi 10.1000/a (publication 7 holds this doi): "
        f"release {first.pk}, line 2, run {one.pk}.",
        "Publication 5 was not given doi 10.1000/b (it holds doi 10.1/x and "
        f"publication 7 holds this doi): release {first.pk}, line 3, run {one.pk}.",
        f"Publication 6 was not given doi 10.1000/c (it holds doi 10.1/y): release "
        f"{second.pk}, line 4, run {two.pk}.",
        f"Run {two.pk} of release {second.pk} found 1,500 publication-identifier "
        "conflicts and its report keeps 2: the others are not listed here.",
    )
    assert found["publication_conflicts"].total == 4
    assert found["term_labels"].lines == (
        f"Term MI:0018 keeps the label 'two hybrid', and release {first.pk}'s "
        f"manifest states 'two-hybrid': line 2, run {one.pk}.",
        f"Term MI:0018 keeps the label 'two hybrid', and release {second.pk}'s "
        f"manifest states 'Y2H': line 6, run {two.pk}.",
        f"Run {two.pk} of release {second.pk} found 3 term labels and its report "
        "keeps 2: the others are not listed here.",
    )
    assert found["term_labels"].kind == REVIEW


def paper(
    publication, key, line, stored=("Title one", 2020), stated=("Title two", None)
):
    return {
        "publication": publication,
        "key": key,
        "stored_title": stored[0],
        "stored_year": stored[1],
        "manifest_title": stated[0],
        "manifest_year": stated[1],
        "line": line,
    }


def test_the_papers_commits_kept_another_title_or_year_of_are_listed_once(
    switch_on, registry
):
    first, second = draft(), draft()
    one = commit_run(
        first,
        publication_conflicts=listed(conflict(4, "10.1000/a", 2)),
        paper_differences=listed(paper(9, "paper-a", 3)),
    )
    two = commit_run(
        second,
        paper_differences=listed(
            paper(9, "paper-a", 5), paper(10, "paper-b", 6, stated=("B", 2021)), total=4
        ),
    )
    found = only(sections(), "publication_conflicts")["publication_conflicts"]
    assert found.title == (
        "Publication identifiers, and paper titles and years, that commits did not "
        "write"
    )
    # The identifiers first, then the papers, each distinct across runs.
    assert found.lines == (
        f"Publication 4 was not given doi 10.1000/a (publication 7 holds this doi): "
        f"release {first.pk}, line 2, run {one.pk}.",
        f"Publication 9, the paper 'paper-a' of release {first.pk}'s dataset, keeps "
        f"the title 'Title one' and the year 2020, and release {first.pk}'s manifest "
        f"states the title 'Title two' and no year: line 3, run {one.pk}.",
        f"Publication 10, the paper 'paper-b' of release {second.pk}'s dataset, keeps "
        f"the title 'Title one' and the year 2020, and release {second.pk}'s manifest "
        f"states the title 'B' and the year 2021: line 6, run {two.pk}.",
        f"Run {two.pk} of release {second.pk} found 4 papers that keep another title "
        "or year and its report keeps 2: the others are not listed here.",
    )
    assert found.total == 4


def test_a_commit_report_without_these_lists_adds_nothing(switch_on, registry):
    ImportRunFactory(
        release=draft(),
        file=None,
        mode=ImportMode.COMMIT,
        state=ImportState.ABANDONED,
        report={},
    )
    found = sections()
    assert found["publication_conflicts"].total == 0
    assert found["term_labels"].total == 0


# ── 8. merge_candidates ─────────────────────────────────────────────────────


def test_a_primary_identifier_another_entity_holds_is_a_merge_candidate(registry):
    tp53 = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    other = create_entity("gene", "Other gene", 9606, "hgnc", "HGNC:7")
    assert sections()["merge_candidates"].total == 0
    add_identifier(other, "hgnc", "HGNC:11998", IdentifierRole.SECONDARY)
    found = sections()
    assert found["merge_candidates"].lines == (
        f"hgnc HGNC:11998 is the primary identifier of entity {tp53.pk} and a "
        f"secondary identifier of entity {other.pk}, of the same type and taxon: "
        "candidates for a merge.",
    )
    assert found["merge_candidates"].kind == REVIEW


def test_an_entity_holding_its_own_primary_identifier_again_is_no_candidate(
    registry,
):
    tp53 = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    add_identifier(tp53, "hgnc", "HGNC:11998", IdentifierRole.ALIAS)
    assert sections()["merge_candidates"].total == 0


# ── 9. provisional_entities ─────────────────────────────────────────────────


def test_provisional_entities_that_no_evidence_names_are_listed(registry, tmp_path):
    tp53 = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    mdm2 = create_entity("gene", "MDM2", 9606, "hgnc", "HGNC:6973")
    found = only(sections(), "provisional_entities")
    assert found["provisional_entities"].lines == (
        f"Entity {tp53.pk} (gene 'TP53', {tp53.identity_key}) is provisional and no "
        "evidence names it; it was created without a release.",
        f"Entity {mdm2.pk} (gene 'MDM2', {mdm2.identity_key}) is provisional and no "
        "evidence names it; it was created without a release.",
    )
    # Once evidence names them, they are not listed.
    release = registered(tmp_path, [GENE])
    committed(release)
    assert sections()["provisional_entities"].total == 0


def test_the_command_prints_each_section_and_ends_with_the_count(registry):
    EntityFactory(label="Lonely")
    out = check()
    at = out.index(
        "Provisional entities that no evidence names (provisional_entities): 1"
    )
    assert out[at + 1].startswith("  Entity ")
    assert "'Lonely'" in out[at + 1]
    titles = [line for line in out if not line.startswith("  ")]
    assert titles[0] == "Problems (graph_check exits 1 while one is found):"
    assert titles[6] == "For review (listed, never a reason to exit 1):"
    assert [line.rsplit("(", 1)[-1] for line in titles[1:6] + titles[7:12]] == [
        f"{name}): {1 if name == 'provisional_entities' else 0}" for name in NAMES
    ]
    assert out[-1] == "No problems; 1 entry is listed for review."
