"""Helpers for the import tests (test_ingest_mapping, test_dry_run,
test_register_release, test_commit): canonical_tsv_v1 rows and files, a
release registered from them, a run's mapping, a release dry-run and
committed, and the graph's rows by natural key."""

import io
import json
import signal
from contextlib import contextmanager
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import models

from browsers.models import BrowserInstance
from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.commit import commit_release
from graph.ingest.context import RunContext, load_registry_snapshot
from graph.ingest.dry_run import dry_run
from graph.ingest.manifest import load_manifest
from graph.ingest.mapping import MappingRun, plan_batch
from graph.ingest.registration import Options, register_release
from graph.ingest.resolutions import NO_RESOLUTIONS
from graph.models import (
    ApprovalScope,
    DatasetRelease,
    DataSource,
    Entity,
    EvidenceAssertion,
    ImportRun,
    ImportState,
    Publication,
    Relationship,
    SourceDataset,
    SourceFile,
    SourceRecord,
)
from graph.tests.factories import (
    ReleaseLicenceFactory,
    SourceDatasetFactory,
    StaffUserFactory,
    approve,
    confirm_diligence,
)
from graph.tests.test_isolation import NEW_APPS

ADAPTER_NAME = SPEC.name
HEADER = [*SPEC.columns, *SPEC.optional_columns]

# A human gene regulating another (HGNC IDs of TP53 and MDM2).
GENE = {
    "record_id": "r1",
    "subject_namespace": "hgnc",
    "subject_id": "HGNC:11998",
    "subject_type": "gene",
    "subject_taxon": "9606",
    "object_namespace": "hgnc",
    "object_id": "HGNC:6973",
    "object_type": "gene",
    "object_taxon": "9606",
    "relationship_type": "transcriptional_regulation",
    "outcome": "supported",
    "evidence_basis": "experimental",
    "assertion_method": "manual_assertion",
}
# Two human proteins interacting (UniProtKB entries of TP53 and MDM2).
PROTEIN = {
    **GENE,
    "subject_namespace": "uniprotkb",
    "subject_id": "P04637",
    "subject_type": "protein",
    "object_namespace": "uniprotkb",
    "object_id": "Q00987",
    "object_type": "protein",
    "relationship_type": "physical_interaction",
}
# A synthetic release's row (D12).
SYNTHETIC = {
    **GENE,
    "subject_namespace": "synthetic_gene",
    "subject_id": "SYN-TF1",
    "subject_taxon": "32630",
    "subject_label": "SYN-TF1",
    "object_namespace": "synthetic_gene",
    "object_id": "SYN-G1",
    "object_taxon": "32630",
    "object_label": "SYN-G1",
}
TERMS = {
    "MI:0018": "two hybrid",
    "MI:0915": "physical association",
    "MI:0407": "direct interaction",
    "ECO:0000353": "physical interaction evidence",
}


def row(base=GENE, **cells) -> dict:
    return {**base, **cells}


def tsv(*rows, header=None) -> bytes:
    """A canonical_tsv_v1 file: the header, then each row on its own line
    (line 2, 3, ...)."""
    header = header or HEADER
    lines = ["\t".join(header)]
    lines += ["\t".join(r.get(column, "") for column in header) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def manifest_bytes(is_synthetic=False, publications=None, terms=None) -> bytes:
    return json.dumps(
        {
            "publications": (
                publications
                if publications is not None
                else {"paper-a": {"title": "A paper", "is_synthetic": is_synthetic}}
            ),
            "terms": terms if terms is not None else TERMS,
        }
    ).encode()


def write(directory, name, data) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


def registered(
    directory,
    rows=(GENE,),
    *,
    synthetic=False,
    manifest=True,
    approved=True,
    data=None,
    dataset=None,
    version="v1",
    **options,
):
    """A release registered from these rows, as graph_register_release
    registers it; with approved, a release that is not synthetic gets an
    open-terms licence snapshot, its dataset's due diligence and an
    effective internal approval, after its files."""
    dataset = dataset or SourceDatasetFactory()
    directory = Path(directory) / f"upload-{dataset.slug}-{version}"
    path = write(directory, "release.tsv", data if data is not None else tsv(*rows))
    stated = None
    if manifest:
        stated = write(
            directory,
            "manifest.json",
            manifest if isinstance(manifest, bytes) else manifest_bytes(synthetic),
        )
    registration = register_release(
        dataset=dataset.slug,
        version=version,
        adapter=ADAPTER_NAME,
        file=path,
        manifest=stated,
        options=Options(is_synthetic=synthetic, **options),
        registered_by=StaffUserFactory(),
    )
    release = registration.release
    if approved and not synthetic:
        approved_for_internal_use(release)
    return release


def approved_for_internal_use(release):
    ReleaseLicenceFactory(release=release, open_terms=True)
    confirm_diligence(release.dataset)
    approve(release, ApprovalScope.INTERNAL)
    return release


def mapping_run(
    release, *, resolutions=NO_RESOLUTIONS, create_entities=False, manifest=None
):
    """A run's mapping of this release, its manifest declaring TERMS and a
    paper of the release's synthetic flag unless given."""
    if manifest is None:
        manifest = load_manifest(
            manifest_bytes(release.is_synthetic),
            release_is_synthetic=release.is_synthetic,
        )
    ctx = RunContext(
        load_registry_snapshot(),
        release.is_synthetic,
        manifest,
        release.pk,
        release.dataset_id,
    )
    return MappingRun(
        adapter=ADAPTER,
        ctx=ctx,
        release=release,
        resolutions=resolutions,
        create_entities=create_entities,
    )


def committed(release, *, triggered_by=None, **options):
    """The release dry-run, then committed, with the same options
    (create_entities, resolutions): the commit's CommitResult. The dry run
    must complete with the verdict ok."""
    user = triggered_by or StaffUserFactory()
    run = dry_run(release, triggered_by=user, **options)
    assert (run.state, run.report["verdict"]) == (
        ImportState.COMPLETED,
        "ok",
    ), run.error or run.report.get("file_errors")
    return commit_release(release, triggered_by=user, **options)


def natural_rows() -> dict:
    """{table: its rows, as sorted tuples} for every table of every new app
    but graph_importrun: each row without its surrogate primary key, its
    timestamps or the runs it names, and each foreign key as the natural
    key of the row it names: an entity by its identity key; a relationship
    by its type, its endpoints' keys and its qualifier key; a publication
    by its identifiers, or its dataset's slug and its local key; a source
    record by its release's key and its record key; an assertion by its
    record's key and its assertion key; a release by its dataset's slug and
    its version; a dataset or a data source by its slug; a browser instance
    by its slug; a file by its release's key, its role and its SHA-256; an
    account by its username. A term, a taxon and a registry row are their
    own keys (a CURIE, a taxid, a slug). So rows that a rolled-back commit
    numbered otherwise compare equal; JSON values compare as JSON."""
    keys = _NaturalKeys()
    rows = {}
    for app in NEW_APPS:
        for model in apps.get_app_config(app).get_models(include_auto_created=True):
            if model is ImportRun:
                continue
            fields = [
                field for field in model._meta.concrete_fields if _compared(field)
            ]
            found = model._base_manager.values_list(
                *(field.attname for field in fields)
            )
            rows[model._meta.db_table] = sorted(
                (
                    tuple(keys.of(field, value) for field, value in zip(fields, row))
                    for row in found
                ),
                key=repr,
            )
    return rows


def _surrogate(field) -> bool:
    return field.primary_key and isinstance(
        field, (models.AutoField, models.BigAutoField)
    )


def _compared(field) -> bool:
    """A column natural_rows compares: not a surrogate key, a timestamp or
    a run."""
    if _surrogate(field) or isinstance(field, models.DateTimeField):
        return False
    return not (field.is_relation and field.related_model is ImportRun)


class _NaturalKeys:
    """{pk: natural key} for each model a compared foreign key names, read
    once per natural_rows call."""

    def __init__(self):
        self.found = {}

    def of(self, field, value):
        if value is None:
            return None
        if isinstance(field, models.JSONField):
            return json.dumps(value, sort_keys=True, default=str)
        if not field.is_relation:
            return value
        model = field.related_model
        if not _surrogate(model._meta.pk):
            return value
        return self.keyed(model).get(value, value)

    def keyed(self, model) -> dict:
        found = self.found.get(model)
        if found is None:
            found = self.found[model] = self._read(model)
        return found

    def _read(self, model) -> dict:
        rows = model._base_manager
        if model is Entity:
            return dict(rows.values_list("pk", "identity_key"))
        if model in (DataSource, SourceDataset, BrowserInstance):
            return dict(rows.values_list("pk", "slug"))
        if model is DatasetRelease:
            return {
                pk: (slug, version)
                for pk, slug, version in rows.values_list(
                    "pk", "dataset__slug", "version"
                )
            }
        if model is SourceFile:
            releases = self.keyed(DatasetRelease)
            return {
                pk: (releases[release], role, sha256)
                for pk, release, role, sha256 in rows.values_list(
                    "pk", "release_id", "role", "sha256"
                )
            }
        if model is SourceRecord:
            releases = self.keyed(DatasetRelease)
            return {
                pk: (releases[release], key)
                for pk, release, key in rows.values_list(
                    "pk", "release_id", "record_key"
                )
            }
        if model is EvidenceAssertion:
            records = self.keyed(SourceRecord)
            return {
                pk: (records[record], key)
                for pk, record, key in rows.values_list(
                    "pk", "source_record_id", "assertion_key"
                )
            }
        if model is Relationship:
            entities = self.keyed(Entity)
            return {
                pk: (slug, entities[subject], entities[obj], qualifier)
                for pk, slug, subject, obj, qualifier in rows.values_list(
                    "pk",
                    "relationship_type_id",
                    "subject_id",
                    "object_id",
                    "qualifier_key",
                )
            }
        if model is Publication:
            datasets = self.keyed(SourceDataset)
            return {
                pk: (pmid, doi, pmcid, imex, datasets.get(dataset, ""), local)
                for pk, pmid, doi, pmcid, imex, dataset, local in rows.values_list(
                    "pk",
                    "pmid",
                    "doi_lookup",
                    "pmcid",
                    "imex_id",
                    "local_dataset_id",
                    "local_key",
                )
            }
        if model is apps.get_model(settings.AUTH_USER_MODEL):
            return dict(rows.values_list("pk", model.USERNAME_FIELD))
        # A row no commit writes keeps its pk.
        return {}


def records_of(run, *rows, header=None) -> list:
    return list(ADAPTER.iter_records(io.BytesIO(tsv(*rows, header=header)), run.ctx))


def plans_of(run, *rows, header=None) -> list:
    """Each row planned, on lines 2, 3, ..., in one batch."""
    return plan_batch(records_of(run, *rows, header=header), run_ctx=run)


def codes(plan, severity=None) -> list:
    return [
        issue.code
        for issue in plan.issues
        if severity is None or issue.severity == severity
    ]


class Blocked(AssertionError):
    """A call blocked past its deadline. Not an OSError, which the code
    under test would catch as a failed open."""


@contextmanager
def deadline(seconds):
    """Raise Blocked in the block once it has run this long: a call that
    blocks (opening a FIFO with no writer) fails the test instead of hanging
    it. POSIX only, in the main thread."""

    def expired(signum, frame):
        raise Blocked(f"Still blocked after {seconds} s.")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
