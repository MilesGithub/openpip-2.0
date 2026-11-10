"""The synthetic demonstration (G6; architecture D12): one dataset of
synthetic regulation, in two releases whose rows cover the cases the typed
graph must handle, and its loader, which graph_load_demo runs.

Every row, entity, label and publication in DEMO_DIR is synthetic. VERSIONS
lists the releases in order, each read by canonical_tsv_v1 from its own data
file with the shared manifest; v2 supersedes v1 and corrects one effect. The
files never change in place: registration compares their SHA-256 digests,
so a change ships as a new Version (v3 superseding v2, with its own files).

load_demo checks, before anything is written: the switch is on; the account
is an active staff account; the database's registry is the code's; and
every file of every Version is shipped. Then, in order:

1. the data source openpip-synthetic (this portal's own) and its dataset
   synthetic-regulation are found or created, in one transaction; a slug
   that other data hold is refused, with nothing written;
2. each Version, v1 end to end before v2, so that v2's dry run lists its
   conflicts with the published v1:
   - a draft or staged release is refused, before anything is written for
     it, its registration included, while it has an active internal
     denial, revoked internal decisions and no active one, or a latest
     licence snapshot that records no terms;
   - it is registered as synthetic (taxon 32630, superseding the version
     before it) while it is absent or a draft, which takes the code's
     adapter version, and when it is past draft under the code's adapter
     version, which writes nothing or restores a stored copy that was lost;
     a release past draft under another adapter or mapping version keeps
     it, once its stored files are found to be these;
   - a published or superseded release is only reported, with what an
     operator did to it; a release withdrawn after it was published is kept
     as it is, unless it is the last version; any other withdrawal is final;
   - a draft or staged release is checked again once registered, then
     under its row lock at the licence and approval steps;
   - a draft is dry-run (its verdict must be ok) and committed with
     --create-entities and no resolution file, or its open commit run is
     resumed; a staged release must have been committed with those options;
   - its licence snapshot (DEMO_LICENCE) and its internal approval are
     recorded unless it has them, each in a short transaction holding the
     release's row lock, which record_licence and record_decision take
     too;
   - it is published;
3. the releases are read again for the result;
4. the demonstration's browser instance, selecting the last Version, and
   this portal's portal_ppi entry are found or created, in one transaction,
   for staff only (browsers.demo); a slug that other data hold is refused,
   after the lines so far, with neither row written.

The operator policy: never override an operator, and never refuse a load
that overrides nothing. Only internal approvals are recorded, so visitors
see the demonstration only once an operator records public_view with
graph_approve. A browser instance found is kept as it stands, and one that
was deleted is created again. Run again, the loader completes a load that
stopped, and writes nothing once the demonstration is loaded. It opens no
transaction around a dry run, a commit or the whole load: a commit that
stops marks its run failed in a transaction of its own, and the next load
resumes it.

Every refusal of a service becomes a DemoError with the lines so far, the
release in progress ending "stopped at" its step; where the refusal
advises a step that only graph_load_demo should take for the
demonstration, the loader's own advice follows (_demo_advice). Any other
exception is raised as it is, with those lines in its demo_lines
attribute, which graph_load_demo prints first.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from browsers.demo import DemoInstanceRefused, ensure_demo_instances

from .approvals import (
    INTERNAL,
    PUBLIC_VIEW,
    ApprovalError,
    effective_scopes,
    record_decision,
)
from .ingest.commit import (
    ALREADY_COMMITTED,
    RESUMED,
    CommitError,
    CommitStopped,
    commit_release,
)
from .ingest.dry_run import OK, DryRunError, dry_run
from .ingest.mapping import RunStopped
from .ingest.registration import (
    ALREADY_REGISTERED,
    UPDATED,
    Options,
    RegistrationError,
    register_release,
)
from .ingest.registry import get_adapter
from .ingest.runlock import runs_are_locked
from .licensing import LicensingError, record_licence
from .models import (
    SYNTHETIC_TAXON,
    DatasetRelease,
    DatasetStatus,
    DataSource,
    DataSourceKind,
    Decision,
    FileRole,
    ImportMode,
    ImportRun,
    ImportState,
    Permission,
    ReleaseLicence,
    ReleaseStatus,
    ShareAlike,
    SourceDataset,
    SourceRecord,
    UsageApproval,
)
from .preconditions import locked_release, require_staff, require_switch
from .registry import registry_state
from .releases import ReleaseError, publish_release

DEMO_DIR = Path(__file__).resolve().parent / "demo_data" / "synthetic_regulation"
SOURCE_SLUG = "openpip-synthetic"
DATASET_SLUG = "synthetic-regulation"


@dataclass(frozen=True)
class Version:
    """One release of the demonstration: its version label, its files in
    DEMO_DIR, the adapter that reads them, and the version it supersedes."""

    version: str
    data_file: str
    manifest_file: str
    adapter: str
    supersedes: str | None


VERSIONS = (
    Version("v1", "release_v1.tsv", "manifest.json", "canonical_tsv_v1", None),
    Version("v2", "release_v2.tsv", "manifest.json", "canonical_tsv_v1", "v1"),
)
# The manifest's two papers, in the order the browser instance cites them.
DEMO_PAPERS = ("syn-paper-1", "syn-paper-2")

SOURCE = {
    "kind": DataSourceKind.THIS_PORTAL,
    "name": "openPIP synthetic demonstration",
    "description": (
        "Synthetic data written for openPIP's tests and demonstrations: every row, "
        "entity, label and publication is invented, and describes no real biology."
    ),
}
DATASET = {
    "title": "Synthetic regulation example",
    "short_title": "Synthetic regulation",
    "description": (
        "Two synthetic releases, v2 correcting one effect of v1, whose rows cover "
        "the cases the typed graph must handle; loaded by graph_load_demo. Every "
        "row, entity, label and publication is invented."
    ),
}

LICENCE_URL = "https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE"
# The MIT License's grant and its condition, verbatim from the repository's
# LICENSE, one paragraph each: the backend image does not hold the file.
MIT_TERMS = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy "
    'of this software and associated documentation files (the "Software"), to deal '
    "in the Software without restriction, including without limitation the rights "
    "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
    "copies of the Software, and to permit persons to whom the Software is "
    "furnished to do so, subject to the following conditions:\n\n"
    "The above copyright notice and this permission notice shall be included in "
    "all copies or substantial portions of the Software."
)
# The snapshot each release records (architecture §3.7 and models.py's
# ReleaseLicence): the licence's name as stated, no SPDX id since none is
# stated, the terms quoted, and every permission among PERMITTED_VALUES, so a
# later public_view, public_bulk or export needs no new snapshot. read_on and
# reviewer are the load's: the operator named by --as records it.
DEMO_LICENCE = {
    "licence_name": "MIT License",
    "spdx_id": "",
    "licence_url": LICENCE_URL,
    "read_at": LICENCE_URL,
    "terms_quoted": MIT_TERMS,
    "attribution": "Copyright (c) 2016-2026 The openPIP authors",
    "redistribution": Permission.PERMITTED_WITH_CONDITIONS,
    "commercial_use": Permission.PERMITTED,
    "modification": Permission.PERMITTED,
    "share_alike": ShareAlike.NO,
    "obligations": (
        "Include the copyright notice and the MIT permission notice in all copies "
        "or substantial portions (LICENSE)."
    ),
}
DEMO_RATIONALE = (
    "Synthetic demonstration data written for openPIP; recorded by graph_load_demo "
    "for staff use."
)

NOTHING_WRITTEN = "Nothing was written."
# What a refusal advises that only graph_load_demo should do for the
# demonstration: a command named, or its steps in other words.
_OTHER_COMMANDS = ("graph_import", "graph_register_release")
_OTHER_STEPS = re.compile(r"\b(?:register it again|dry-run it)\b", re.IGNORECASE)
# Advice whose way out, for the demonstration, is a discard first:
# registration's, for a draft whose commit stopped under another adapter
# version, and a commit's, for an open commit run started with other options
# than the loader's, or one that this code cannot resume. Run again without
# the discard, the loader is refused the same way.
_REGISTER_AFTER_DISCARD = (
    "Discard it with graph_discard_release, then register it again; or register "
    "its files under a new version."
)
_OTHER_OPTIONS = "run the same command with those options"
# Precondition 10's refusal of an open commit run that this code cannot
# resume (the registry, the adapter or the latest dry run is not the run's),
# when it advises steps the loader takes; one that advises a new version
# gets no advice of the loader's.
_NOT_RESUMABLE = "that cannot be resumed under this code"
# A commit's refusal of an open commit run while its cause holds
# (preconditions 11 to 15): a rerun resumes the run once the cause is gone,
# and after a discard a load dry-runs and commits the release as it maps
# now. Its stored-file form names graph_register_release, whose copy a
# rerun's registration restores before the run resumes: the plain advice.
_WHILE_THIS_HOLDS = "cannot be resumed while this holds"
_RESTORED = re.compile(r"Restored the stored copy of the (\w+) file")


class DemoError(Exception):
    """The load stopped. `lines` says what was done before it stopped."""

    def __init__(self, message, lines=()):
        super().__init__(message)
        self.lines = tuple(lines)


@dataclass(frozen=True)
class DemoResult:
    """What load_demo did."""

    # For the operator: one line for the data source and dataset, one per
    # release, one for the browser instances, and how the load ended.
    lines: tuple[str, ...]
    # False only when no step wrote.
    wrote: bool
    # {version: its release}, read at the end.
    releases: dict[str, DatasetRelease]


def load_demo(*, loaded_by) -> DemoResult:
    """Load the synthetic demonstration for `loaded_by`, an active staff
    account, or complete a load that stopped (see the module docstring).
    Raises DemoError, with the lines so far, when a check or a service
    refuses."""
    load = _Load(loaded_by)
    try:
        return load.run()
    except DemoError:
        raise
    except BaseException as error:
        error.demo_lines = load.lines_so_far()
        raise


class _Load:
    """One call of load_demo: what it has done, as lines, and whether a
    step wrote."""

    def __init__(self, user):
        self.user = user
        self.lines = []
        self.wrote = False
        self.dataset = None
        # The release in progress: its name, its steps' outcomes so far, and
        # the step under way.
        self.named = None
        self.steps = None
        self.step = None

    # ── The lines ──

    def lines_so_far(self) -> tuple:
        """The lines, with the release in progress's own so far."""
        lines = list(self.lines)
        if self.steps is not None:
            lines.append(_line(self.named, [*self.steps, f"stopped at {self.step}"]))
        return tuple(lines)

    def _stop(self, message):
        raise DemoError(message, self.lines_so_far()) from None

    def _unknown(self, message):
        """locked_release's refusal of a release that is no longer stored."""
        return DemoError(
            f"{self.named}: {self.step} was refused: {_without_ending(message)}",
            self.lines_so_far(),
        )

    def _refused(self, step, error, advice=""):
        """A service's refusal, as a DemoError: what refused it and why,
        without its final "Nothing was written." (the lines say what was),
        then the loader's own advice: the caller's, or else what follows
        advice that only graph_load_demo should take (_demo_advice)."""
        message = _without_ending(str(error))
        text = f"{self.named}: {step} was refused: {message}"
        advice = advice or _demo_advice(message)
        if advice:
            text += f" {advice}"
        self._stop(text)

    # ── The load ──

    def run(self) -> DemoResult:
        self._check()
        self._catalogue()
        releases = {}
        for n, version in enumerate(VERSIONS):
            last = n == len(VERSIONS) - 1
            releases[version.version] = self._load(version, last=last).pk
        releases = {
            version: DatasetRelease.objects.get(pk=pk)
            for version, pk in releases.items()
        }
        current = releases[VERSIONS[-1].version]
        self._instances(current)
        if self.wrote:
            self.lines.append(
                "The synthetic demonstration is loaded: release "
                f"{current.pk} ({current.version}) is current, {_visitors(current)}."
            )
        else:
            self.lines.append(
                "The synthetic demonstration was loaded already: nothing was written."
            )
        return DemoResult(tuple(self.lines), self.wrote, releases)

    def _check(self):
        """What is checked before anything is written."""
        require_switch(DemoError, "the synthetic demonstration is not loaded")
        require_staff(DemoError, self.user, "--as")
        state = registry_state()
        if state.database != state.code:
            raise DemoError(
                f"The database's registry is at version {state.database or 'none'}, "
                f"and this code's at {state.code}: run graph_sync_registry first. "
                f"{NOTHING_WRITTEN}"
            )
        for version in VERSIONS:
            for name in (version.data_file, version.manifest_file):
                if not (DEMO_DIR / name).is_file():
                    raise DemoError(
                        f"The synthetic demonstration's file {name} is missing from "
                        f"{DEMO_DIR}: this installation is incomplete. "
                        f"{NOTHING_WRITTEN}"
                    )

    def _catalogue(self):
        """Step 1: the data source and the dataset, found or created; both
        slugs are unique, so get_or_create is safe beside another load."""
        taken = "its slug names data that are not the demonstration's"
        with transaction.atomic():
            source, source_created = DataSource.objects.get_or_create(
                slug=SOURCE_SLUG, defaults=SOURCE
            )
            if source.kind != DataSourceKind.THIS_PORTAL:
                raise DemoError(
                    f"Data source {SOURCE_SLUG} is of kind {source.kind}, not "
                    f"{DataSourceKind.THIS_PORTAL}: {taken}. {NOTHING_WRITTEN}"
                )
            dataset, dataset_created = SourceDataset.objects.get_or_create(
                slug=DATASET_SLUG,
                defaults={
                    **DATASET,
                    "data_source": source,
                    "status": DatasetStatus.ACTIVE,
                },
            )
            if dataset.data_source_id != source.pk:
                raise DemoError(
                    f"Dataset {DATASET_SLUG} is of data source "
                    f"{dataset.data_source.slug}, not {SOURCE_SLUG}: {taken}. "
                    f"{NOTHING_WRITTEN}"
                )
            real = (
                dataset.releases.filter(is_synthetic=False)
                .order_by("pk")
                .values_list("pk", "version")
                .first()
            )
            if real is not None:
                raise DemoError(
                    f"Dataset {DATASET_SLUG} holds release {real[0]} ({real[1]}), "
                    f"which is not synthetic: {taken}. {NOTHING_WRITTEN}"
                )
        self.dataset = dataset
        self.wrote |= source_created or dataset_created
        said = {False: "found", True: "created"}
        if source_created == dataset_created:
            done = f"Data source {SOURCE_SLUG} and dataset {DATASET_SLUG}: "
            done += f"{said[source_created]}."
        else:
            done = (
                f"Data source {SOURCE_SLUG}: {said[source_created]}; dataset "
                f"{DATASET_SLUG}: {said[dataset_created]}."
            )
        self.lines.append(done)

    def _instances(self, current):
        """Step 4: the browser instances, once the current release is
        published."""
        try:
            line, wrote = ensure_demo_instances(
                release=current,
                dataset=self.dataset,
                papers=DEMO_PAPERS,
                actor=self.user,
            )
        except DemoInstanceRefused as error:
            self._stop(str(error))
        self.wrote |= wrote
        self.lines.append(line)

    def _load(self, version, *, last) -> DatasetRelease:
        """Step 2, for one version; the release as it stands after."""
        found = self._stored(version)
        self.named = _named(found, version)
        self.steps = []
        if found is not None and found.status in (
            ReleaseStatus.DRAFT,
            ReleaseStatus.STAGED,
        ):
            # Before its registration too, which may update a draft or
            # restore a stored copy: nothing is written for a release an
            # operator's decision stops.
            self._decisions_allow(found)
        self.step = "registration"
        release = self._register(version, found)
        self.named = _named(release, version)
        if release.status in (ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED):
            self._found(release)
        elif release.status == ReleaseStatus.WITHDRAWN:
            self._withdrawn(release, last=last)
        else:
            self._decisions_allow(release)
            self._import(release)
            self._licence(release)
            self._approval(release)
            release = self._publish(release)
        self.lines.append(_line(self.named, self.steps))
        self.named = self.steps = self.step = None
        return release

    def _stored(self, version):
        return DatasetRelease.objects.filter(
            dataset=self.dataset, version=version.version
        ).first()

    # ── Registration ──

    def _register(self, version, found) -> DatasetRelease:
        """The release registered, or kept as it was committed: a release
        past draft keeps the adapter version it was committed with."""
        if found is not None and found.status != ReleaseStatus.DRAFT:
            if self._files_differ(found, version):
                self._stop(
                    f"{self.named}: the demo files changed under {version.version}, "
                    "which is past draft: a change ships as a new version."
                )
            spec = get_adapter(version.adapter).spec
            stored = (
                found.adapter,
                found.adapter_version,
                found.mapping_version,
                found.kind,
            )
            if stored != (spec.name, spec.version, spec.mapping_version, spec.kind):
                self.steps.append(
                    f"registered already, read by {found.adapter} "
                    f"{found.adapter_version} (mapping {found.mapping_version})"
                )
                return found
        records = 0
        if found is not None and found.status == ReleaseStatus.DRAFT:
            records = SourceRecord.objects.filter(release=found).count()
        try:
            registration = self._registration(version)
        except RegistrationError as error:
            # Another first registration of this version won the race.
            if found is not None or self._stored(version) is None:
                self._refused("registration", error)
            try:
                registration = self._registration(version)
            except RegistrationError as again:
                self._refused("registration", again)
        release = registration.release
        if registration.outcome == ALREADY_REGISTERED:
            if len(registration.lines) > 1:
                # A stored copy that was lost, put back.
                self.wrote = True
                self.steps.append(
                    f"registered already, restored {_restored(registration.lines[1:])}"
                )
            else:
                self.steps.append("registered already")
        elif registration.outcome == UPDATED:
            self.wrote = True
            self.steps.append(
                f"registered again under {release.adapter} {release.adapter_version} "
                f"({_counted(records, 'source record')} deleted)"
            )
        else:
            self.wrote = True
            self.steps.append("registered")
        return release

    def _registration(self, version):
        return register_release(
            dataset=DATASET_SLUG,
            version=version.version,
            adapter=version.adapter,
            file=DEMO_DIR / version.data_file,
            manifest=DEMO_DIR / version.manifest_file,
            options=Options(
                supersedes=version.supersedes,
                taxa=(SYNTHETIC_TAXON,),
                is_synthetic=True,
            ),
            registered_by=self.user,
        )

    def _files_differ(self, release, version) -> bool:
        """Whether the release's stored data file and manifest are other than
        DEMO_DIR's, by their SHA-256, as registration compares them."""
        for role, name in (
            (FileRole.DATA, version.data_file),
            (FileRole.MANIFEST, version.manifest_file),
        ):
            held = list(
                release.files.filter(role=role)
                .order_by("pk")
                .values_list("sha256", flat=True)
            )
            if held != [_sha256(DEMO_DIR / name)]:
                return True
        return False

    # ── What a release past draft holds ──

    def _found(self, release):
        """Step 2, for a published or superseded release: nothing is written
        or refused; what is found is reported, an operator's doing included."""
        self.step = "its status"
        latest = _latest_licence(release)
        if latest is None:
            self.steps.append("no licence snapshot")
        elif latest.records_terms:
            self.steps.append(f"licence snapshot {latest.pk} kept")
        else:
            self.steps.append(f"licence snapshot {latest.pk} records no terms")
        active = _active_internal(release)
        if active is None:
            self.steps.append(_internal_revoked(release) or "no internal approval")
        elif active.decision == Decision.DENIED:
            self.steps.append(f"internal denial {active.pk}")
        else:
            self.steps.append(f"internal approval {active.pk} kept" + _stale(active))
        if release.status == ReleaseStatus.PUBLISHED:
            self.steps.append("published already")
        else:
            by = (
                DatasetRelease.objects.filter(
                    supersedes=release, published_at__isnull=False
                )
                .values_list("pk", flat=True)
                .first()
            )
            self.steps.append(f"superseded by release {by}")

    def _withdrawn(self, release, *, last):
        """A withdrawal is final. One made after the release was published
        leaves the next version free to supersede it, so the load goes on.
        The last version withdrawn after it was published leaves no release
        current; withdrawn before it was published, it can never be
        published, and the release it corrects stays as it is. An earlier
        version withdrawn before it was published freed what the next
        corrects."""
        self.step = "its status"
        if release.published_at is not None and not last:
            self.steps.append(
                "withdrawn (kept as it is): no longer readable when selected"
            )
            return
        corrected = release.supersedes
        if not last:
            why = (
                "it was withdrawn before it was published, so the release that "
                "corrects it can never be published"
            )
        elif (
            release.published_at is None
            and corrected is not None
            and corrected.status == ReleaseStatus.PUBLISHED
        ):
            why = (
                "it was withdrawn before it was published, so it can never be "
                f"published, and release {corrected.pk} ({corrected.version}), "
                "which it corrects, stays current"
            )
        else:
            why = "no release of the demonstration is current"
        self._stop(
            f"{self.named} is withdrawn, and a withdrawal is final: {why}. The "
            "demonstration ships as these versions only, so graph_load_demo cannot "
            "load it again on this portal."
        )

    # ── A draft or staged release ──

    def _decisions_allow(self, release, active=None, *, read=True):
        """An operator's decision that the load must not override, read
        before anything is written for the release, and again under its lock:
        an active internal denial, or revoked internal decisions and no active
        one; then a latest licence snapshot that records no terms. The loader
        records its own snapshot only after the commit, and graph_approve
        refuses an approval while the latest snapshot records no terms, so
        the advice then names the admin step that records them first."""
        self.step = "the internal approval"
        if read:
            active = _active_internal(release)
        latest = _latest_licence(release)
        approve = "record an approval with graph_approve"
        if latest is None or not latest.records_terms:
            approve = (
                "record the release's terms in the admin (Release licences, then "
                "Add), then an approval with graph_approve"
            )
        if active is not None and active.decision == Decision.DENIED:
            self._stop(
                f"{self.named} has an active internal denial, decision {active.pk}: "
                f"revoke it with graph_revoke --approval {active.pk}, then {approve}, "
                "then run graph_load_demo again."
            )
        if active is None:
            revoked = _internal_revoked(release)
            if revoked is not None:
                self._stop(
                    f"{self.named} has no active internal decision ({revoked}): "
                    f"{approve}, then run graph_load_demo again."
                )
        if read:
            self.step = "the licence snapshot"
            self._terms_recorded(latest)

    def _terms_recorded(self, latest):
        if latest is not None and not latest.records_terms:
            self._stop(
                f"{self.named}'s latest licence snapshot, {latest.pk}, records no "
                "terms: record its terms in the admin, then run graph_load_demo again."
            )

    def _import(self, release):
        """A draft dry-run and committed, or its open commit run resumed; a
        staged release's commit checked to be the demonstration's."""
        if release.status == ReleaseStatus.DRAFT and not _open_commit_run(release):
            self._dry_run(release)
        self.step = "the commit"
        staged = release.status == ReleaseStatus.STAGED
        try:
            result = commit_release(
                release, triggered_by=self.user, create_entities=True
            )
        except CommitStopped as error:
            self.wrote = True
            self._stop(_stopped(release.pk, error))
        except CommitError as error:
            then = ""
            if staged and error.precondition == 6:
                then = "Then run graph_load_demo again."
            self._refused("the commit", error, then)
        if result.outcome == ALREADY_COMMITTED:
            self.steps.append("committed already")
            return
        self.wrote = True
        report = result.run.report
        created = report["counts"]["created"]
        done = f"committed by run {result.run.pk}"
        if result.outcome == RESUMED:
            done += f", resuming it after {_counted(len(report['resumed']), 'stop')}"
        self.steps.append(
            f"{done}: created {_counted(created['entities'], 'entity', 'entities')}, "
            f"{_counted(created['relationships'], 'relationship')}, "
            f"{_counted(created['publications'], 'publication')} and "
            f"{_counted(created['terms'], 'term')}"
        )

    def _dry_run(self, release):
        self.step = "the dry run"
        try:
            run = dry_run(release, triggered_by=self.user, create_entities=True)
        except DryRunError as error:
            self._refused("the dry run", error)
        except RunStopped as error:
            self.wrote = True
            self._stop(
                f"The dry run of release {release.pk} stopped, and its run is marked "
                f"failed: {_sentence(str(error))} Running graph_load_demo again "
                "dry-runs it again."
            )
        self.wrote = True
        report = run.report
        records = report["counts"]["records"]
        self.steps.append(
            f"dry run {run.pk}: {records['mapped']} mapped, {records['rejected']} "
            f"rejected, {report['counts']['unstored']} unstored, verdict "
            f"{report['verdict']}"
        )
        if report["verdict"] != OK:
            self._stop(
                f"{self.named}: dry run {run.pk} has the verdict {report['verdict']}: "
                "the demonstration's files break a rule, which the run's report "
                "names."
            )

    def _licence(self, release):
        """Its licence snapshot, recorded unless its latest records terms."""
        self.step = "the licence snapshot"
        recorded = False
        try:
            with transaction.atomic():
                locked_release(release.pk, self._unknown)
                latest = _latest_licence(release)
                self._terms_recorded(latest)
                if latest is None:
                    latest = record_licence(
                        release,
                        recorded_by=self.user,
                        **DEMO_LICENCE,
                        read_on=timezone.localdate(),
                        reviewer=self.user.get_username(),
                    )
                    recorded = True
        except LicensingError as error:
            self._refused("the licence snapshot", error)
        self.wrote |= recorded
        self.steps.append(
            f"licence snapshot {latest.pk} {'recorded' if recorded else 'kept'}"
        )

    def _approval(self, release):
        """Its internal approval, recorded unless one was ever decided; an
        active one is kept, and named stale when it relies on an older
        snapshot than the latest."""
        self.step = "the internal approval"
        recorded = False
        try:
            with transaction.atomic():
                locked_release(release.pk, self._unknown)
                active = _active_internal(release)
                ever = active is not None or (
                    UsageApproval.objects.filter(
                        release=release, scope=INTERNAL
                    ).exists()
                )
                if ever:
                    self._decisions_allow(release, active, read=False)
                else:
                    active = record_decision(
                        release,
                        INTERNAL,
                        Decision.APPROVED,
                        rationale=DEMO_RATIONALE,
                        decided_by=self.user,
                    )
                    recorded = True
        except ApprovalError as error:
            self._refused("the internal approval", error)
        if recorded:
            self.wrote = True
            self.steps.append(f"internal approval {active.pk} recorded")
        else:
            self.steps.append(f"internal approval {active.pk} kept" + _stale(active))

    def _publish(self, release) -> DatasetRelease:
        """It is published; a refusal is no error when another load published
        it meanwhile."""
        self.step = "publishing"
        try:
            published = publish_release(release, published_by=self.user)
        except ReleaseError as error:
            stored = DatasetRelease.objects.get(pk=release.pk)
            if stored.status in (ReleaseStatus.PUBLISHED, ReleaseStatus.SUPERSEDED):
                self.steps.append("published (meanwhile)")
                return stored
            advice = ""
            if _hides_public_data(stored):
                # A scope keeps one active decision: a denial is revoked
                # before an approval is recorded.
                denial = _public_view_denial(stored)
                if denial is None:
                    advice = (
                        "Record that approval with graph_approve, then run "
                        "graph_load_demo again."
                    )
                else:
                    advice = (
                        f"Its public_view decision {denial} is a denial: revoke it "
                        f"with graph_revoke --approval {denial}, then record an "
                        "approval with graph_approve, then run graph_load_demo again."
                    )
            self._refused("publishing", error, advice)
        self.wrote = True
        done = "published"
        target = published.supersedes_id
        if target is not None:
            status = (
                DatasetRelease.objects.filter(pk=target)
                .values_list("status", flat=True)
                .first()
            )
            if status == ReleaseStatus.WITHDRAWN:
                done += f"; release {target}, which it corrects, stays withdrawn"
            else:
                done += f", superseding release {target}"
        self.steps.append(done)
        return published


# ── Helpers ─────────────────────────────────────────────────────────────────


def _named(release, version) -> str:
    """How the lines name a release, by its id once it is registered."""
    if release is None:
        return f"Release {DATASET_SLUG} {version.version}"
    return f"Release {release.pk} ({DATASET_SLUG} {version.version})"


def _line(named, steps) -> str:
    return f"{named}: " + "; ".join(steps) + "."


def _restored(lines) -> str:
    """What a registration's lines after its first say it restored."""
    roles = [match.group(1) for match in map(_RESTORED.match, lines) if match]
    if not roles:
        return "its stored copies"
    return f"the stored {' and '.join(roles)} file" + ("s" if len(roles) > 1 else "")


def _latest_licence(release):
    return ReleaseLicence.objects.filter(release=release).order_by("-pk").first()


def _active_internal(release):
    return UsageApproval.objects.filter(
        release=release, scope=INTERNAL, revoked_at__isnull=True
    ).first()


def _internal_revoked(release) -> str | None:
    """The release's latest revoked internal decision, in words, if any."""
    revoked = (
        UsageApproval.objects.filter(
            release=release, scope=INTERNAL, revoked_at__isnull=False
        )
        .order_by("-pk")
        .first()
    )
    if revoked is None:
        return None
    what = "approval" if revoked.decision == Decision.APPROVED else "denial"
    by = revoked.revoked_by_username or "an unnamed account"
    return f"internal {what} {revoked.pk} revoked by {by}"


def _stale(approval) -> str:
    """The clause naming an approval that relies on an older snapshot than
    its release's latest, which graph_check lists as a problem. While the
    latest records no terms, graph_approve --replace is refused, so the
    clause says to record terms first, or to revoke the approval."""
    latest = _latest_licence(approval.release_id)
    if latest is None or approval.licence_id == latest.pk:
        return ""
    advice = f"refresh it with graph_approve --replace {approval.pk}"
    if not latest.records_terms:
        advice = (
            f"record the release's terms in the admin, then {advice}, or revoke it "
            f"with graph_revoke --approval {approval.pk}"
        )
    return (
        f"; it relies on snapshot {approval.licence_id}, not the latest, "
        f"{latest.pk}: {advice}"
    )


def _open_commit_run(release) -> bool:
    """Whether the release has an open commit run (running, or failed and
    resumable), which a dry run never runs beside."""
    return ImportRun.objects.filter(
        release=release,
        mode=ImportMode.COMMIT,
        state__in=[ImportState.RUNNING, ImportState.FAILED],
    ).exists()


def _hides_public_data(release) -> bool:
    """Whether publishing the release would hide public data: the release it
    corrects has an effective public_view, and it has none."""
    target = release.supersedes_id
    return (
        target is not None
        and PUBLIC_VIEW in effective_scopes(target)
        and PUBLIC_VIEW not in effective_scopes(release)
    )


def _public_view_denial(release):
    """The id of the release's active public_view denial, if any."""
    return (
        UsageApproval.objects.filter(
            release=release,
            scope=PUBLIC_VIEW,
            decision=Decision.DENIED,
            revoked_at__isnull=True,
        )
        .values_list("pk", flat=True)
        .first()
    )


def _visitors(release) -> str:
    """Who sees the current release, and what would show it to visitors."""
    scopes = effective_scopes(release)
    if INTERNAL not in scopes:
        return "but no one sees it: its internal approval is not effective"
    if PUBLIC_VIEW in scopes:
        return "and visitors see it (public_view)"
    denial = _public_view_denial(release)
    if denial is not None:
        return (
            f"for staff only; its public_view decision {denial} is a denial: revoke "
            f"it with graph_revoke --approval {denial}, then record an approval with "
            "graph_approve"
        )
    return (
        "for staff only; visitors see it once a public_view approval is recorded "
        "with graph_approve"
    )


def _stopped(release_id, error) -> str:
    """What the loader says of a commit that stopped (CommitStopped), as
    graph_import says it, with the loader's own advice."""
    run = error.run_id
    head = f"The commit of release {release_id} stopped, and its run {run}"
    cause = _sentence(str(error))
    if error.marked:
        return (
            f"{head} is marked failed: {cause} Run graph_load_demo again to resume it "
            "once the cause is fixed, or discard it with graph_discard_release."
        )
    if error.abandoned:
        return (
            f"{head} was given up by a discard meanwhile: {cause} Run graph_load_demo "
            "again, which dry-runs and commits it."
        )
    if not runs_are_locked():
        return (
            f"{head} could not be marked failed: {cause} It stays running, and on "
            "this database, which has no run lock, a commit never resumes a running "
            "run: discard it with graph_discard_release, then run graph_load_demo "
            "again."
        )
    return (
        f"{head} could not be marked failed, since the database connection was lost: "
        f"{cause} The next commit or discard of the release marks it. Run "
        "graph_load_demo again to resume it."
    )


def _demo_advice(message) -> str:
    """The loader's advice after a refusal's own advice to register the
    release again, dry-run it, commit it or resume its commit, by a
    command's name or in other words: steps graph_load_demo takes for the
    demonstration. Where a rerun is refused the same way until a discard,
    the discard comes first. A refusal of an open commit run that "cannot
    be resumed while this holds" names both ways out: a rerun once its
    cause is gone, which resumes the run, or a discard, after which the
    load dry-runs and commits the release as it maps now; its stored-file
    form keeps the plain advice, since a rerun's registration restores the
    copy before the run resumes. Advice naming graph_discard_release alone,
    graph_approve, graph_revoke or graph_sync_registry stays as it is."""
    if _REGISTER_AFTER_DISCARD in message:
        return (
            "For the demonstration, discard it with graph_discard_release, then run "
            "graph_load_demo again, which registers it again under this code's "
            "version."
        )
    if _OTHER_OPTIONS in message:
        return (
            "For the demonstration, discard it with graph_discard_release, then run "
            "graph_load_demo again."
        )
    if any(name in message for name in _OTHER_COMMANDS) or _OTHER_STEPS.search(message):
        if _NOT_RESUMABLE in message:
            return (
                "For the demonstration, discard it with graph_discard_release, then run "
                "graph_load_demo again."
            )
        if _WHILE_THIS_HOLDS in message and "graph_register_release" not in message:
            return (
                "For the demonstration, run graph_load_demo again once this no longer "
                "holds, which resumes the run; or discard the release with "
                "graph_discard_release, then run graph_load_demo again, which dry-runs "
                "and commits it as it maps now."
            )
        return "For the demonstration, run graph_load_demo again instead."
    return ""


def _without_ending(message) -> str:
    """A refusal without its final "Nothing was written.": a load's lines say
    what it wrote."""
    message = message.strip()
    if message.endswith(NOTHING_WRITTEN):
        message = message[: -len(NOTHING_WRITTEN)].rstrip()
    return message


def _sentence(text) -> str:
    """A clause as a sentence: with one final full stop."""
    text = text.strip()
    return text if text.endswith(".") else text + "."


def _counted(n, singular, plural=None) -> str:
    """n and the noun, singular or plural as n says."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()
