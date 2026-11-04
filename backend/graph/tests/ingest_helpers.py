"""Helpers for the import tests (test_ingest_mapping, test_dry_run,
test_register_release): canonical_tsv_v1 rows and files, a release
registered from them, and a run's mapping."""

import io
import json
import signal
from contextlib import contextmanager
from pathlib import Path

from graph.ingest.canonical_tsv import ADAPTER, SPEC
from graph.ingest.context import RunContext, load_registry_snapshot
from graph.ingest.manifest import load_manifest
from graph.ingest.mapping import MappingRun, plan_batch
from graph.ingest.registration import Options, register_release
from graph.ingest.resolutions import NO_RESOLUTIONS
from graph.models import ApprovalScope
from graph.tests.factories import (
    ReleaseLicenceFactory,
    SourceDatasetFactory,
    StaffUserFactory,
    approve,
    confirm_diligence,
)

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
