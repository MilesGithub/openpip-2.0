"""Registering a release (architecture §8, step 1; G4 §6): a draft release of
a dataset, with the adapter that reads it and the options its source
states, and its data file and manifest in private storage (graph.storage).

Data sources, datasets, licence snapshots and due diligence are recorded in
the admin; approval decisions with graph.approvals.record_decision until
G5's graph_approve. A release's files are added only before anyone decides
on it (G2), so registering comes first.

register_release refuses while the switch is off, and checks, before it
reads a file: the actor is an active staff account (a gate: G2 stores no
registrant); the dataset exists; the version follows G2's rule; the adapter
exists; supersedes names a release of the same dataset (G2's guard checks
the synthetic flag); each declared taxon is registered; the source URL, the
release date and the embargo are well formed. The data file and the
manifest are then each streamed into incoming/ once, and the manifest's
bytes are validated (graph.ingest.manifest) before anything is placed.

A new (dataset, version) is written in one short transaction: the draft
release, with the adapter's name, version, mapping version and kind and
every option, its declared taxa, and its files, placed. If it fails, every
placed and incoming file is removed.

A known (dataset, version) is never registered twice:
- with the same files and options, it is already registered and nothing is
  written; a stored copy that is missing or no longer matches is restored
  from the file given (the same digest), whatever the release's status;
- with the same files and options but another adapter version, mapping
  version or kind in the code, a draft whose records hold no evidence (and
  with no open commit run) takes the code's, and its source records are
  deleted, to be read again from the stored file by the next dry run: with
  G5's discard, that is architecture §4.4's discard and re-import. The kind
  changes only while no entity names the release. Anything else is refused;
- any other difference (a file, the adapter, an option) is refused, naming
  it: a known version with other content is a new release.
"""

import datetime
from dataclasses import dataclass, field
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from graph.models import (
    HTTP_URL,
    RELEASE_VERSION,
    DatasetRelease,
    Entity,
    EvidenceAssertion,
    FileRole,
    ImmutableRecordError,
    ImportMode,
    ImportRun,
    ImportState,
    ReleaseStatus,
    ReleaseTaxon,
    SourceDataset,
    SourceRecord,
    Taxon,
)
from graph.preconditions import require_staff, typed_graph_enabled
from graph import storage

from . import limits
from .jsonfile import read_bounded
from .manifest import ManifestError, load_manifest
from .registry import AdapterSpecError, UnknownAdapter, get_adapter

DATA_MEDIA_TYPE = "text/tab-separated-values"
MANIFEST_MEDIA_TYPE = "application/json"
SOURCE_URL_LENGTH = DatasetRelease._meta.get_field("source_url").max_length

REGISTERED = "registered"
ALREADY_REGISTERED = "already registered"
UPDATED = "updated"


class RegistrationError(Exception):
    """Nothing was registered: a rule refuses the release or one of its
    files. Nothing was written, and no file was left behind."""


@dataclass(frozen=True)
class Options:
    """What a registration states besides its files."""

    source_url: str = ""
    release_date: datetime.date | None = None
    supersedes: str | None = None
    taxa: tuple[int, ...] = ()
    embargo_until: datetime.datetime | None = None
    is_synthetic: bool = False
    store_raw_rows: bool = True


@dataclass(frozen=True)
class Registration:
    """What register_release did."""

    release: DatasetRelease
    # REGISTERED, ALREADY_REGISTERED or UPDATED.
    outcome: str
    # Sentences for the operator.
    lines: tuple[str, ...] = field(default_factory=tuple)


def register_release(
    *,
    dataset,
    version,
    adapter,
    file,
    manifest=None,
    options=Options(),
    registered_by,
) -> Registration:
    """Register a release of the dataset (its slug) at this version, read by
    this adapter (its name), from a data file and an optional manifest
    (paths). See the module docstring."""
    if not typed_graph_enabled():
        raise RegistrationError(
            "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
            "release is registered. Nothing was written."
        )
    require_staff(RegistrationError, registered_by, "--as")
    dataset = _dataset(dataset)
    _check_version(version)
    adapter = _adapter(adapter)
    supersedes = _supersedes(dataset, options.supersedes)
    taxa = _taxa(options.taxa)
    _check_options(options)
    incomings = []
    try:
        data = _streamed(file, limits.MAX_FILE_BYTES, "The data file")
        incomings.append(data)
        stated = None
        if manifest is not None:
            stated = _streamed(manifest, limits.MAX_JSON_BYTES, "The manifest")
            incomings.append(stated)
            _check_manifest(stated, options.is_synthetic)
            if stated.sha256 == data.sha256:
                raise RegistrationError(
                    "The manifest and the data file have the same content. Nothing "
                    "was written."
                )
        existing = DatasetRelease.objects.filter(
            dataset=dataset, version=version
        ).first()
        if existing is None:
            return _create(
                dataset,
                version,
                adapter.spec,
                options,
                supersedes,
                taxa,
                (data, Path(file).name),
                (stated, Path(manifest).name) if stated is not None else None,
                incomings,
            )
        return _known(existing, adapter.spec, options, supersedes, taxa, data, stated)
    finally:
        # Whatever was not placed or restored.
        storage.discard(incomings)


# ── Before any file is read ─────────────────────────────────────────────────


def _dataset(slug) -> SourceDataset:
    dataset = None
    if isinstance(slug, str):
        dataset = SourceDataset.objects.filter(slug=slug).first()
    if dataset is None:
        raise RegistrationError(
            f"Unknown dataset {slug!r}: data sources and datasets are created in the "
            "admin. Nothing was written."
        )
    return dataset


def _check_version(version):
    if not isinstance(version, str) or not RELEASE_VERSION.fullmatch(version):
        raise RegistrationError(
            "A release version is the source's label, URL-safe: a letter or digit, "
            f"then up to 63 letters, digits or . _ + -; not {version!r}. Nothing was "
            "written."
        )


def _adapter(name):
    try:
        return get_adapter(name)
    except (UnknownAdapter, AdapterSpecError) as error:
        raise RegistrationError(f"{error} Nothing was written.") from None


def _supersedes(dataset, version):
    if version is None:
        return None
    found = DatasetRelease.objects.filter(dataset=dataset, version=version).first()
    if found is None:
        raise RegistrationError(
            f"--supersedes {version!r} names no release of dataset {dataset.slug}. "
            "Nothing was written."
        )
    return found


def _taxa(taxa) -> tuple:
    taxa = tuple(sorted(set(taxa)))
    for taxid in taxa:
        if isinstance(taxid, bool) or not isinstance(taxid, int) or taxid <= 0:
            raise RegistrationError(
                f"A declared taxon is an NCBI taxid, not {taxid!r}. Nothing was "
                "written."
            )
    known = set(Taxon.objects.filter(taxid__in=taxa).values_list("taxid", flat=True))
    missing = [taxid for taxid in taxa if taxid not in known]
    if missing:
        raise RegistrationError(
            f"Taxon {', '.join(map(str, missing))} is not in the registry. Nothing "
            "was written."
        )
    return taxa


def _check_options(options):
    url = options.source_url
    if url:
        if len(url) > SOURCE_URL_LENGTH:
            raise RegistrationError(
                f"--source-url is at most {SOURCE_URL_LENGTH} characters. Nothing "
                "was written."
            )
        try:
            HTTP_URL(url)
        except ValidationError:
            raise RegistrationError(
                f"--source-url is an http or https URL, not {url!r}. Nothing was "
                "written."
            ) from None
    embargo = options.embargo_until
    if embargo is not None and (
        not isinstance(embargo, datetime.datetime) or embargo.utcoffset() is None
    ):
        raise RegistrationError(
            "--embargo-until is a date and time with its UTC offset, as "
            f"2027-01-01T00:00:00+00:00; not {embargo!r}. Nothing was written."
        )


# ── The files ───────────────────────────────────────────────────────────────


def _streamed(path, max_bytes, what) -> storage.Incoming:
    try:
        return storage.stream_to_incoming(path, max_bytes=max_bytes)
    except storage.StorageError as error:
        raise RegistrationError(f"{what}: {error}") from None


def _check_manifest(incoming, is_synthetic):
    try:
        data = read_bounded(
            incoming.path, what="the release manifest", error=ManifestError
        )
        load_manifest(data, release_is_synthetic=is_synthetic)
    except ManifestError as error:
        raise RegistrationError(
            f"The manifest is not valid: {error} Nothing was written."
        ) from None


# ── A new release ───────────────────────────────────────────────────────────


def _create(dataset, version, spec, options, supersedes, taxa, data, stated, incomings):
    placed = []
    try:
        with transaction.atomic():
            release = DatasetRelease(
                dataset=dataset,
                version=version,
                release_date=options.release_date,
                source_url=options.source_url,
                kind=spec.kind,
                supersedes=supersedes,
                is_synthetic=options.is_synthetic,
                embargo_until=options.embargo_until,
                adapter=spec.name,
                adapter_version=spec.version,
                mapping_version=spec.mapping_version,
                store_raw_rows=options.store_raw_rows,
            )
            release.save()
            for taxid in taxa:
                ReleaseTaxon(release=release, taxon_id=taxid).save()
            files = [(data, FileRole.DATA, DATA_MEDIA_TYPE)]
            if stated is not None:
                files.append((stated, FileRole.MANIFEST, MANIFEST_MEDIA_TYPE))
            for (incoming, name), role, media_type in files:
                placed.append(
                    storage.place(
                        release,
                        incoming,
                        role=role,
                        original_name=name,
                        media_type=media_type,
                    )
                )
    except (
        ImmutableRecordError,
        IntegrityError,
        storage.StorageError,
        OSError,
    ) as error:
        storage.discard(incomings, placed)
        raise RegistrationError(
            f"Release {dataset.slug} {version} was not registered: {error} Nothing "
            "was written."
        ) from None
    except BaseException:
        storage.discard(incomings, placed)
        raise
    lines = [
        f"Registered release {release.pk}: {dataset.slug} {version}, read by "
        f"{spec.name} {spec.version} (mapping {spec.mapping_version}, kind "
        f"{spec.kind}), a draft" + (", synthetic" if release.is_synthetic else "") + "."
    ]
    for file in placed:
        lines.append(
            f"Stored the {file.role} file {file.original_name!r}: {file.size} bytes, "
            f"SHA-256 {file.sha256}."
        )
    lines.append(
        "Record its licence snapshot and due diligence in the admin, then dry-run "
        f"it: graph_import --release {release.pk} --dry-run --as <username>."
    )
    return Registration(release, REGISTERED, tuple(lines))


# ── A known release ─────────────────────────────────────────────────────────


def _known(release, spec, options, supersedes, taxa, data, stated) -> Registration:
    files = {
        role: list(release.files.filter(role=role).order_by("pk"))
        for role in (FileRole.DATA, FileRole.MANIFEST)
    }
    differences = _differences(release, spec, options, supersedes, taxa, files)
    differences += _file_differences(files, data, stated)
    if differences:
        raise RegistrationError(
            f"Release {release.pk} ({release.dataset.slug} {release.version}) is "
            "registered already, and differs: "
            + "; ".join(differences)
            + ". A known version with other content is a new release: register it "
            "under a new version. Nothing was written."
        )
    lines = _restore(files, data, stated)
    code = (spec.version, spec.mapping_version, spec.kind)
    stored = (release.adapter_version, release.mapping_version, release.kind)
    if code == stored:
        lines.insert(
            0,
            f"Release {release.pk} ({release.dataset.slug} {release.version}) is "
            "already registered, with these files and options. Nothing was written"
            + (" but the restored copies." if lines else "."),
        )
        return Registration(release, ALREADY_REGISTERED, tuple(lines))
    return _update(release, spec, stored, lines)


def _differences(release, spec, options, supersedes, taxa, files) -> list:
    stated = {
        "the adapter": (release.adapter, spec.name),
        "--source-url": (release.source_url, options.source_url),
        "--release-date": (release.release_date, options.release_date),
        "--supersedes": (
            release.supersedes_id,
            supersedes.pk if supersedes is not None else None,
        ),
        "--taxon": (
            tuple(sorted(release.release_taxa.values_list("taxon_id", flat=True))),
            tuple(taxa),
        ),
        "--embargo-until": (release.embargo_until, options.embargo_until),
        "--synthetic": (release.is_synthetic, options.is_synthetic),
        "--no-raw-rows": (not release.store_raw_rows, not options.store_raw_rows),
    }
    return [
        f"{name} is {_shown(held)} there, not {_shown(given)}"
        for name, (held, given) in stated.items()
        if held != given
    ]


def _shown(value) -> str:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return "(" + ", ".join(map(str, value)) + ")" if value else "none"
    return repr(value)


def _file_differences(files, data, stated) -> list:
    differences = []
    held = [file.sha256 for file in files[FileRole.DATA]]
    if held != [data.sha256]:
        differences.append(
            f"its data file has SHA-256 {', '.join(held) or 'none'}, and the file "
            f"given {data.sha256}"
        )
    held = [file.sha256 for file in files[FileRole.MANIFEST]]
    given = [stated.sha256] if stated is not None else []
    if held != given:
        differences.append(
            f"its manifest has SHA-256 {', '.join(held) or 'none'}, and the one given "
            f"{', '.join(given) or 'none'}"
        )
    return differences


def _restore(files, data, stated) -> list:
    """Put back each stored copy that is missing or no longer matches its
    record, from the file given: the same digest."""
    lines = []
    for role, incoming in ((FileRole.DATA, data), (FileRole.MANIFEST, stated)):
        if incoming is None:
            continue
        [row] = files[role]
        problem = storage.stored_problem(row)
        if problem is None:
            continue
        try:
            storage.restore(row, incoming)
        except storage.StorageError as error:
            raise RegistrationError(
                f"The stored {role} file of release {row.release_id} could not be "
                f"restored: {error}"
            ) from None
        lines.append(f"Restored the stored copy of the {role} file: {problem}")
    return lines


def _update(release, spec, stored, lines) -> Registration:
    """A draft without evidence takes the code's adapter version, mapping
    version and kind; its source records are deleted."""
    with transaction.atomic():
        release = DatasetRelease.objects.select_for_update().get(pk=release.pk)
        refused = _update_refusal(release, spec)
        if refused:
            raise RegistrationError(
                f"Release {release.pk} ({release.dataset.slug} {release.version}) "
                f"was registered with {release.adapter} {stored[0]} (mapping "
                f"{stored[1]}, kind {stored[2]}), and this code carries {spec.version} "
                f"(mapping {spec.mapping_version}, kind {spec.kind}); {refused} "
                "Register it under a new version instead. Nothing was written."
            )
        release.adapter_version = spec.version
        release.mapping_version = spec.mapping_version
        release.kind = spec.kind
        release.save(
            update_fields=["adapter_version", "mapping_version", "kind", "updated_at"]
        )
        deleted = _delete_records(release)
    lines.insert(
        0,
        f"Release {release.pk} ({release.dataset.slug} {release.version}) now reads "
        f"with {spec.name} {spec.version} (mapping {spec.mapping_version}, kind "
        f"{spec.kind}), not {stored[0]} (mapping {stored[1]}, kind {stored[2]}). "
        f"Its {deleted} source records were deleted: the next dry run reads them "
        "again from the stored file.",
    )
    return Registration(release, UPDATED, tuple(lines))


def _delete_records(release) -> int:
    """Delete the release's source records, under the caller's lock, a batch
    of limits.BATCH_ROWS at a time by their ids: no record's raw row is
    read, and no list of ids grows with the release."""
    deleted, last = 0, 0
    while True:
        ids = list(
            SourceRecord.objects.filter(release=release, pk__gt=last)
            .order_by("pk")
            .values_list("pk", flat=True)[: limits.BATCH_ROWS]
        )
        if not ids:
            return deleted
        _, counts = SourceRecord.objects.filter(pk__in=ids).only("pk").delete()
        deleted += counts.get(SourceRecord._meta.label, 0)
        last = ids[-1]


def _update_refusal(release, spec) -> str | None:
    if release.status != ReleaseStatus.DRAFT:
        return f"it is {release.status}, and only a draft is read anew."
    if EvidenceAssertion.objects.filter(release=release).exists():
        return "evidence is recorded from its records, which rests on that mapping."
    if ImportRun.objects.filter(
        release=release,
        mode=ImportMode.COMMIT,
        state__in=[ImportState.RUNNING, ImportState.FAILED],
    ).exists():
        return "a commit run of it is open."
    if (
        release.kind != spec.kind
        and Entity.objects.filter(created_by_release=release).exists()
    ):
        return (
            f"its kind stays {release.kind}, since entities name it as the release "
            "that created them."
        )
    return None
