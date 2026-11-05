"""The import framework (graph.ingest): adapter specs and their registry, the
pinned issue codes, the pure adapters' context, strict JSON, the release
manifest and the reviewed resolution file."""

import codecs
import dataclasses
import io
import json
import pickle
import time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graph.claims import RULES
from graph.entities import add_identifier, create_entity
from graph.ingest import limits
from graph.ingest.canonical_tsv import ADAPTER, CHANGES_NOTE, SPEC
from graph.ingest.context import RunContext, load_registry_snapshot
from graph.ingest.jsonfile import (
    JsonFileError,
    boolean,
    integer,
    load_strict_json,
    read_bounded,
    text_problem,
)
from graph.ingest.manifest import EMPTY_MANIFEST, ManifestError, load_manifest
from graph.ingest.protocol import (
    FILE_ERROR_CODES,
    ISSUE_CODES,
    NOTE_CODES,
    UNSTORED_CODES,
    FileError,
    Issue,
    RawRecord,
    duplicate_issues,
    issues_json,
    note,
    quoted,
    rejects,
)
from graph.ingest.registry import (
    ADAPTERS,
    AdapterSpecError,
    UnknownAdapter,
    get_adapter,
    validate_spec,
)
from graph.ingest.resolutions import NO_RESOLUTIONS, ResolutionError, load_resolutions
from graph.models import Entity, EntityStatus, IdentifierRole
from graph.terms import PHYSICAL_INTERACTION_LEVELS

# ── Adapters and their specs ────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_adapters_spec_is_sound(name):
    adapter = ADAPTERS[name]
    assert validate_spec(adapter.spec) == []
    assert adapter.spec.name == name
    assert get_adapter(name) is adapter


def test_canonical_tsv_v1s_spec_is_pinned():
    spec = ADAPTERS["canonical_tsv_v1"].spec
    assert spec is SPEC
    assert (spec.name, spec.version, spec.mapping_version, spec.kind) == (
        "canonical_tsv_v1",
        "1.0.0",
        "1",
        "evidence",
    )
    assert spec.isoform_specific is True
    assert spec.record_key_basis == "source_id"
    assert spec.relationship_types is None
    assert spec.reference_types_only_if_synthetic is True
    assert spec.coreferent_publication_ids is False
    assert dict(spec.interaction_levels) == {
        "MI:0914": "association",
        "MI:0915": "physical_association",
        "MI:0407": "direct_interaction",
    }
    assert (
        spec.changes_note
        == CHANGES_NOTE
        == (
            "Identifiers are normalized by their namespace's rules and resolved to "
            "typed entities; an identifier may be mapped by a reviewed resolution file "
            "(basis curated); a row that cites several papers becomes one assertion per "
            "paper. No value is inferred, merged or corrected."
        )
    )
    assert spec.columns == (
        "record_id",
        "subject_namespace",
        "subject_id",
        "subject_type",
        "subject_taxon",
        "object_namespace",
        "object_id",
        "object_type",
        "object_taxon",
        "relationship_type",
        "outcome",
        "evidence_basis",
        "assertion_method",
    )
    assert spec.optional_columns == (
        "subject_label",
        "object_label",
        "directed",
        "effect",
        "genetic_sign",
        "interaction_type",
        "method",
        "eco",
        "publications",
        "evidence_group",
        "threshold",
    )
    assert spec.column_prefixes == ("score:", "context:", "qualifier:")


def test_the_accepted_interaction_types_are_a_subset_of_the_crosswalk_at_its_levels():
    for curie, level in SPEC.interaction_levels.items():
        assert PHYSICAL_INTERACTION_LEVELS[curie][0] == level


@pytest.mark.parametrize(
    "change,problem",
    [
        ({"isoform_specific": False}, "arrives with R2"),
        ({"isoform_specific": 1}, "isoform_specific is true or false"),
        ({"coreferent_publication_ids": True}, "arrives with M1"),
        ({"changes_note": "  "}, "changes_note"),
        ({"kind": "reference_or_evidence"}, "kind is one of"),
        ({"record_key_basis": "row"}, "record_key_basis"),
        ({"version": "1.0"}, "semantic version"),
        ({"version": "v1.0.0"}, "semantic version"),
        ({"mapping_version": ""}, "mapping_version"),
        ({"name": "Canonical TSV"}, "name"),
        ({"name": "x" * 65}, "name"),
        (
            {"interaction_levels": {"MI:0915": "direct_interaction"}},
            "crosswalk's level",
        ),
        ({"interaction_levels": {"MI:0403": "association"}}, "crosswalk"),
        ({"interaction_levels": {"mi:0915": "physical_association"}}, "canonical"),
        ({"interaction_levels": {"XX:1": "association"}}, "interaction_levels"),
        ({"columns": ()}, "required columns"),
        ({"optional_columns": ("record_id",)}, "named once"),
        ({"relationship_types": {"physical_interaction"}}, "frozenset"),
    ],
)
def test_validate_spec_refuses_what_g4_does_not_implement(change, problem):
    spec = dataclasses.replace(SPEC, **change)
    problems = validate_spec(spec)
    assert any(problem in sentence for sentence in problems), problems


def test_an_unknown_adapter_is_refused_and_an_unsound_one_too(monkeypatch):
    with pytest.raises(UnknownAdapter, match="canonical_tsv_v1"):
        get_adapter("canonical_tsv_v2")
    with pytest.raises(UnknownAdapter):
        get_adapter(None)
    unsound = dataclasses.replace(SPEC, coreferent_publication_ids=True)
    monkeypatch.setitem(
        ADAPTERS, "canonical_tsv_v1", type("Unsound", (), {"spec": unsound})()
    )
    with pytest.raises(AdapterSpecError, match="M1"):
        get_adapter("canonical_tsv_v1")


def test_validate_spec_takes_a_spec_only():
    assert validate_spec({"name": "canonical_tsv_v1"}) != []


# ── Issue codes ─────────────────────────────────────────────────────────────


PINNED_CODES = {
    # Reading a line: unstored.
    "line_too_long",
    "bad_utf8",
    "control_character",
    "field_count",
    "field_too_long",
    "bad_record_id",
    "duplicate_record",
    "conflicting_duplicate",
    # A row's values.
    "too_many_values",
    "unknown_namespace",
    "namespace_not_for_type",
    "bad_identifier",
    "hgnc_without_prefix",
    "bad_taxon",
    "unknown_taxon",
    "taxon_contradiction",
    "isoform_form",
    "unknown_entity_type",
    "unknown_relationship_type",
    "reference_type_not_allowed",
    "unknown_score_type",
    "unknown_facet",
    "bad_value",
    "negative_effect",
    "interaction_type_not_listed",
    "method_is_interaction_type",
    "term_undeclared",
    "bad_curie",
    "score_not_finite",
    "score_not_a_number",
    "bad_threshold",
    "unknown_ref",
    "bad_paper",
    "duplicate_paper",
    "label_too_long",
    "label_blank",
    "synthetic_violation",
    "resolution_contradicts",
    "not_identity_namespace",
    "identity_conflict",
    "entity_exists",
    "release_cannot_introduce",
    *(f"claim_rule_{n}" for n in range(1, 17)),
    "claim_conflict",
    # Notes.
    "censored_score",
    "curated_mapping",
    "candidate",
    "unmatched",
    "label_differs",
    "pmid_and_doi",
    # File errors.
    "encoding",
    "no_header",
    "header_unknown_column",
    "header_missing_column",
    "header_duplicate_column",
    "too_many_columns",
    "too_many_lines",
    "too_many_identifiers",
    "manifest_invalid",
    "stored_file_mismatch",
    # A record's issues past the first limits.RECORD_ISSUES.
    "more",
}
# The codes that refuse a whole file.
PINNED_FILE_ERRORS = {
    "encoding",
    "no_header",
    "header_unknown_column",
    "header_missing_column",
    "header_duplicate_column",
    "too_many_columns",
    "too_many_lines",
    # The mapping's: past limits.MAX_IDENTIFIERS distinct identifiers.
    "too_many_identifiers",
    "unknown_score_type",
    "unknown_facet",
    "manifest_invalid",
    "stored_file_mismatch",
}


def test_the_issue_codes_are_pinned():
    assert set(ISSUE_CODES) == PINNED_CODES
    assert all(meaning.strip() for meaning in ISSUE_CODES.values())
    assert {f"claim_rule_{n}" for n in RULES} <= set(ISSUE_CODES)
    assert UNSTORED_CODES | NOTE_CODES | FILE_ERROR_CODES <= set(ISSUE_CODES)
    assert not UNSTORED_CODES & NOTE_CODES
    assert not FILE_ERROR_CODES & NOTE_CODES
    assert FILE_ERROR_CODES == PINNED_FILE_ERRORS
    assert ISSUE_CODES["too_many_identifiers"] == (
        "The file states more distinct identifiers than a run keeps."
    )
    assert limits.MAX_IDENTIFIERS == 250_000


def test_an_issue_has_a_pinned_code_and_a_severity_its_code_allows():
    issue = Issue(
        "bad_taxon", "subject_taxon '09606' is not a taxid.", "subject_taxon", 3
    )
    assert issue.as_json() == {
        "code": "bad_taxon",
        "field": "subject_taxon",
        "message": "subject_taxon '09606' is not a taxid.",
        "line": 3,
        "severity": "error",
    }
    assert rejects([issue]) and not rejects([note("pmid_and_doi", "Both.")])
    with pytest.raises(ValueError, match="not an issue code"):
        Issue("made_up", "A message.")
    with pytest.raises(ValueError, match="is a note"):
        Issue("candidate", "A message.")
    with pytest.raises(ValueError, match="is an error"):
        note("bad_taxon", "A message.")
    with pytest.raises(ValueError, match="severity"):
        Issue("bad_taxon", "A message.", severity="warning")


def test_a_record_keeps_twenty_issues_and_counts_the_rest():
    issues = [Issue("bad_value", f"Issue {n}.", line=2) for n in range(25)]
    stored = issues_json(issues)
    assert len(stored) == limits.RECORD_ISSUES + 1
    assert stored[-1] == {"code": "more", "count": 5}
    assert stored[0]["message"] == "Issue 0."
    assert issues_json(issues[:20]) == [issue.as_json() for issue in issues[:20]]


def conflict(n) -> Issue:
    return Issue("conflicting_duplicate", f"Conflict {n}.", field="record_id", line=2)


def test_a_records_conflicts_are_kept_past_twenty_issues():
    own = [Issue("bad_value", f"Issue {n}.", line=2) for n in range(25)]
    stored = issues_json([*own[:3], conflict(0), *own[3:], conflict(1)])
    assert len(stored) == limits.RECORD_ISSUES + 1
    assert stored[-1] == {"code": "more", "count": 7}
    codes = [item["code"] for item in stored[:-1]]
    assert codes.count("conflicting_duplicate") == 2
    # In their place: the other issues fill the room left, in order.
    assert stored[3]["message"] == "Conflict 0."
    assert stored[-2]["message"] == "Conflict 1."
    assert [item["message"] for item in stored[:-1] if item["code"] == "bad_value"] == [
        f"Issue {n}." for n in range(18)
    ]
    many = issues_json([conflict(n) for n in range(25)])
    assert [item["message"] for item in many[:-1]] == [
        f"Conflict {n}." for n in range(20)
    ]
    assert many[-1] == {"code": "more", "count": 5}


@pytest.mark.parametrize("own", [0, 5, 19, 20, 21, 25])
@pytest.mark.parametrize("conflicts", [0, 1, 19, 20, 21])
@pytest.mark.parametrize("added", ["conflict", "other"])
def test_an_issue_a_later_batch_adds_is_stored_as_one_batch_stores_it(
    own, conflicts, added
):
    # mapping._with_issue gives a stored record one more issue; the result
    # is what issues_json stores for all of them at once.
    from graph.ingest.mapping import _with_issue

    issues = [Issue("bad_value", f"Issue {n}.", line=2) for n in range(own)]
    issues += [conflict(n) for n in range(conflicts)]
    issue = conflict("added") if added == "conflict" else note("candidate", "Added.")
    assert _with_issue(issues_json(issues), issue) == issues_json([*issues, issue])


def test_a_quoted_value_is_its_repr_truncated():
    assert quoted("a\x00b") == "'a\\x00b'"
    assert quoted("\ud800") == "'\\ud800'"
    long = quoted("x" * 1000)
    assert len(long) == limits.QUOTED_VALUE_CHARS and long.endswith("...")


def test_a_file_error_carries_issues():
    error = FileError([Issue("no_header", "The file is empty.", line=1)])
    assert [issue.code for issue in error.issues] == ["no_header"]
    assert str(error) == "The file is empty."
    with pytest.raises(TypeError):
        FileError([])


def test_a_duplicate_record_key_is_worded_with_both_lines():
    record = RawRecord(2001, "r1", "source_id", {"record_id": "r1"}, "b" * 64)
    later, first = duplicate_issues(record, 2, "a" * 64)
    assert (later.code, later.line, later.field) == (
        "conflicting_duplicate",
        2001,
        "record_id",
    )
    assert (first.code, first.line, first.severity) == (
        "conflicting_duplicate",
        2,
        "error",
    )
    assert "line 2:" in later.message and "line 2001" in first.message
    later, first = duplicate_issues(record, 2, "b" * 64)
    assert (later.code, later.line, first) == ("duplicate_record", 2001, None)
    assert {"duplicate_record", "conflicting_duplicate"} <= UNSTORED_CODES


# ── Adapters are pure ───────────────────────────────────────────────────────

SYNTHETIC_ROWS = (
    "record_id\tsubject_namespace\tsubject_id\tsubject_type\tsubject_taxon\t"
    "object_namespace\tobject_id\tobject_type\tobject_taxon\trelationship_type\t"
    "outcome\tevidence_basis\tassertion_method\teffect\tpublications\tmethod\t"
    "interaction_type\tscore:synthetic:confidence\tthreshold\tcontext:host_taxon\n"
    "r1\tsynthetic_gene\tSYN-TF1\tgene\t32630\tsynthetic_gene\tSYN-G1\tgene\t32630\t"
    "transcriptional_regulation\tsupported\texperimental\tmanual_assertion\t"
    "activating\tref:paper-a\t\t\t0.9\t\t\n"
    "r2\tsynthetic_protein\tSYN-P1\tprotein\t32630\tuniprotkb\tP04637\tprotein\t9606\t"
    "physical_interaction\tsupported\texperimental\tmanual_assertion\t\t"
    "pmid:1|doi:10.1/x\tMI:0018|MI:0915\tMI:0915\tnan\t\t9606\n"
    "r3\tsynthetic_gene\tSYN-TF1\tgene\t32630\tsynthetic_gene\tSYN-G2\tgene\t32630\t"
    "transcriptional_regulation\tnot_significant\tstatistical\tautomatic_assertion\t"
    "\t\t\t\t<2.2e-16\tsynthetic:confidence > 0.5\t\n"
    "r4\tsynthetic_gene\tSYN-TF1\tgene\t32630\tsynthetic_gene\tSYN-G2\tgene\t32630\t"
    "encodes\tsupported\texperimental\tmanual_assertion\tnegative\tref:nope\t\t\t\t\t\n"
)


@pytest.fixture
def snapshot(registry):
    return load_registry_snapshot()


def synthetic_context(snapshot):
    manifest = load_manifest(
        json.dumps(
            {
                "publications": {
                    "paper-a": {"title": "SYN paper", "is_synthetic": True}
                },
                "terms": {"MI:0018": "two hybrid", "MI:0915": "physical association"},
            }
        ).encode(),
        release_is_synthetic=True,
    )
    return RunContext(snapshot, True, manifest)


def test_an_adapter_reads_and_maps_without_a_query(snapshot, django_assert_num_queries):
    ctx = synthetic_context(snapshot)
    with django_assert_num_queries(0):
        records = list(ADAPTER.iter_records(io.BytesIO(SYNTHETIC_ROWS.encode()), ctx))
        mapped = [ADAPTER.map_record(record, ctx) for record in records]
    assert [record.record_key for record in records] == ["r1", "r2", "r3", "r4"]
    codes = [sorted({issue.code for issue in result.issues}) for result in mapped]
    assert codes == [
        [],
        [
            "method_is_interaction_type",
            "pmid_and_doi",
            "score_not_finite",
            "synthetic_violation",
        ],
        ["censored_score"],
        ["negative_effect", "unknown_ref"],
    ]


def test_an_unstored_synthetic_row_is_checked_without_a_query(
    snapshot, django_assert_num_queries
):
    ctx = synthetic_context(snapshot)
    fields = {
        "record_id": "#real",
        "subject_namespace": "hgnc",
        "subject_id": "HGNC:11998",
        "subject_type": "gene",
        "subject_taxon": "9606",
        "subject_label": "TP53",
        "object_namespace": "synthetic_gene",
        "object_id": "SYN-G1",
        "object_type": "gene",
        "object_taxon": "32630",
        "object_label": "SYN-G1",
        "relationship_type": "transcriptional_regulation",
        "outcome": "supported",
        "evidence_basis": "experimental",
        "assertion_method": "manual_assertion",
        "publications": "pmid:1",
    }
    unstored = Issue("bad_record_id", "Malformed.", field="record_id", line=3)
    record = RawRecord(3, None, "source_id", fields, "0" * 64, (unstored,))
    with django_assert_num_queries(0):
        found = ADAPTER.synthetic_issues(record, ctx)
    assert {issue.code for issue in found} == {"synthetic_violation"}
    assert {issue.field for issue in found} == {
        "subject_namespace",
        "subject_taxon",
        "subject_label",
        "publications",
    }
    # Only a synthetic release is checked, and only a row whose cells split.
    assert (
        ADAPTER.synthetic_issues(record, dataclasses.replace(ctx, is_synthetic=False))
        == ()
    )
    assert ADAPTER.synthetic_issues(dataclasses.replace(record, fields=None), ctx) == ()


def test_the_registry_snapshot_holds_what_adapters_read(snapshot):
    assert snapshot.version == "1.1"
    assert snapshot.synthetic_namespaces == {"synthetic_gene", "synthetic_protein"}
    hgnc = snapshot.namespaces["hgnc"]
    assert (hgnc.scope, hgnc.fixed_taxon_id, hgnc.allowed_entity_types) == (
        "taxon",
        9606,
        frozenset({"gene"}),
    )
    assert snapshot.entity_types["gene"].taxon_policy == "required"
    assert {9606, 7227, 32630} <= snapshot.taxa
    physical = snapshot.relationship_types["physical_interaction"]
    assert (physical.family, physical.orientation, physical.is_reference) == (
        "physical",
        "symmetric",
        False,
    )
    assert physical.subject_types == {"protein", "protein_isoform"}
    assert snapshot.relationship_types["encodes"].is_reference is True
    assert "synthetic:confidence" in snapshot.score_types


def test_the_snapshot_is_read_in_a_constant_number_of_queries(registry):
    with CaptureQueriesContext(connection) as queries:
        load_registry_snapshot()
    assert len(queries) == 9


def test_a_run_context_without_a_manifest_declares_nothing(snapshot):
    ctx = RunContext(snapshot, False)
    assert ctx.manifest is EMPTY_MANIFEST
    assert dict(ctx.manifest.terms) == {} and dict(ctx.manifest.publications) == {}


# ── Strict JSON ─────────────────────────────────────────────────────────────


def strict(data):
    return load_strict_json(data, what="the test file")


@pytest.mark.parametrize(
    "data,message",
    [
        (b"[" * 100_000 + b"]" * 100_000, "nested too deeply"),
        (b'{"entity": ' + b"9" * 5000 + b"}", "more than 19 digits"),
        (b"[12345678901234567890]", "more than 19 digits"),
        (b"[1e999]", "fraction or an exponent"),
        (b"[1.5]", "fraction or an exponent"),
        (b"[2E3]", "fraction or an exponent"),
        (b"[NaN]", "NaN"),
        (b"[Infinity]", "Infinity"),
        (b"[-Infinity]", "-Infinity"),
        (b'{"a": 1, "a": 1}', "given twice"),
        (b'{"x": {"a": 1, "a": 2}}', "given twice"),
        ('{"a": 1}'.encode("utf-16"), "UTF-16 or UTF-32"),
        ('{"a": 1}'.encode("utf-16-be"), "not strict JSON|not UTF-8"),
        ('{"a": 1}'.encode("utf-16-le"), "not strict JSON|not UTF-8"),
        ('{"a": 1}'.encode("utf-32"), "UTF-16 or UTF-32"),
        (codecs.BOM_UTF8 + b'{"a": 1}', "byte order mark"),
        (b'{"a": "\\u0000"}', "NUL"),
        (b'{"\\u0000": 1}', "NUL"),
        (b'{"a": "\\ud800"}', "lone surrogate"),
        (b'{"a": "x\\udfffy"}', "lone surrogate"),
        (b'{"a": "\\u001b[31m"}', "control character"),
        (b'{"a": "line\\nbreak"}', "control character"),
        (b'{"a": "\\u007f"}', "control character"),
        (b'{"a": "\\u0085"}', "control character"),
        (b'["\xc3\x28"]', "not UTF-8"),
        (b"{'a': 1}", "not strict JSON"),
        (b"", "not strict JSON"),
    ],
)
def test_strict_json_refuses_what_these_files_never_mean(data, message):
    with pytest.raises(JsonFileError, match=message):
        strict(data)


def test_a_repeated_key_is_found_in_one_pass():
    keys = b",".join(b'"k%07d":0' % n for n in range(100_000))
    data = b"{" + keys + b',"k0000000":1}'
    started = time.perf_counter()
    with pytest.raises(JsonFileError, match="'k0000000' is given twice"):
        strict(data)
    # Counting each key against every other would take minutes here.
    assert time.perf_counter() - started < 1.0


def test_strict_json_reads_what_these_files_hold():
    value = strict(
        '{"key": "SYN- text \U0001f600", "n": -9223372036854775807, '
        '"list": [true, false, null]}'.encode()
    )
    assert value == {
        "key": "SYN- text \U0001f600",
        "n": -9223372036854775807,
        "list": [True, False, None],
    }
    # An escaped surrogate pair is one character, not two lone halves.
    assert strict(b'["\\ud83d\\ude00"]') == ["\U0001f600"]


def test_a_json_file_over_the_limit_is_refused_while_it_is_read(monkeypatch, tmp_path):
    monkeypatch.setattr(limits, "MAX_JSON_BYTES", 10)
    with pytest.raises(JsonFileError, match="larger than 10 bytes"):
        strict(b'{"a": "0123456789"}')

    class Endless(io.RawIOBase):
        def readable(self):
            return True

        def read(self, size=-1):
            assert size != -1, "read without a bound"
            return b" " * size

    with pytest.raises(JsonFileError, match="larger than 10 bytes"):
        read_bounded(Endless(), what="the test file")
    path = tmp_path / "file.json"
    path.write_bytes(b"[1]")
    assert read_bounded(path, what="the test file") == b"[1]"


def test_a_value_is_checked_where_an_integer_or_a_boolean_is_expected():
    for value in (True, False, 1.0, "1", None):
        with pytest.raises(JsonFileError):
            integer(value, where="entity", low=1, high=10)
    assert integer(None, where="year", low=1, high=10, null=True) is None
    assert integer(10, where="entity", low=1, high=10) == 10
    for value in (1, 0, "true", None):
        with pytest.raises(JsonFileError):
            boolean(value, where="is_synthetic")
    with pytest.raises(TypeError):
        load_strict_json("[1]", what="the test file")
    assert text_problem("SYN-TF1") is None


# ── The release manifest ────────────────────────────────────────────────────


def manifest_bytes(publications=None, terms=None, **extra) -> bytes:
    value = dict(extra)
    if publications is not None:
        value["publications"] = publications
    if terms is not None:
        value["terms"] = terms
    return json.dumps(value).encode()


def paper(**fields):
    return {"title": "A synthetic paper", "year": 2026, "is_synthetic": True, **fields}


def test_a_manifest_declares_papers_and_terms():
    manifest = load_manifest(
        manifest_bytes(
            {"paper-a": paper(), "b.2:x-y_z": paper(year=None, title=" Spaced ")},
            {"mi:0018": " two hybrid ", "ECO:0000353": "physical interaction evidence"},
        ),
        release_is_synthetic=True,
    )
    assert set(manifest.publications) == {"paper-a", "b.2:x-y_z"}
    first = manifest.publications["paper-a"]
    assert (first.key, first.title, first.year, first.is_synthetic) == (
        "paper-a",
        "A synthetic paper",
        2026,
        True,
    )
    assert manifest.publications["b.2:x-y_z"].year is None
    assert manifest.publications["b.2:x-y_z"].title == "Spaced"
    # CURIEs normalized as graph.terms normalizes them; labels stripped.
    assert dict(manifest.terms) == {
        "MI:0018": "two hybrid",
        "ECO:0000353": "physical interaction evidence",
    }


def test_an_empty_manifest_declares_nothing():
    manifest = load_manifest(b"{}", release_is_synthetic=False)
    assert manifest == EMPTY_MANIFEST
    real = load_manifest(
        manifest_bytes({"paper-a": paper(is_synthetic=False)}),
        release_is_synthetic=False,
    )
    assert real.publications["paper-a"].is_synthetic is False


@pytest.mark.parametrize(
    "data,message",
    [
        (b"[]", "is a JSON object"),
        (manifest_bytes(papers={}), "unknown keys"),
        (manifest_bytes(publications=[]), '"publications" is a JSON object'),
        (manifest_bytes(terms=[]), '"terms" is a JSON object'),
        (manifest_bytes({"bad key": paper()}), "not a local key"),
        (manifest_bytes({"a|b": paper()}), "not a local key"),
        (manifest_bytes({"-a": paper()}), "not a local key"),
        (manifest_bytes({"a" * 101: paper()}), "not a local key"),
        (manifest_bytes({"a": "A title"}), "is a JSON object"),
        (manifest_bytes({"a": {**paper(), "doi": "10.1/x"}}), "unknown keys"),
        (manifest_bytes({"a": {"year": 2026, "is_synthetic": True}}), "has no title"),
        (manifest_bytes({"a": {"title": "T"}}), "has no is_synthetic"),
        (manifest_bytes({"a": paper(title=" ")}), "never blank"),
        (manifest_bytes({"a": paper(title="t" * 10_001)}), "at most 10000"),
        (manifest_bytes({"a": paper(title=5)}), "is text"),
        (manifest_bytes({"a": paper(title="a\nb")}), "control character"),
        (manifest_bytes({"a": paper(year=0)}), "from 1 to 32767"),
        (manifest_bytes({"a": paper(year=32768)}), "from 1 to 32767"),
        (manifest_bytes({"a": paper(year=True)}), "from 1 to 32767"),
        (manifest_bytes({"a": paper(year="2026")}), "from 1 to 32767"),
        (
            b'{"publications": {"a": {"title": "T", "year": 2026.0, '
            b'"is_synthetic": true}}}',
            "fraction",
        ),
        (manifest_bytes({"a": paper(is_synthetic=1)}), "true or false"),
        (manifest_bytes({"a": paper(is_synthetic=False)}), "is not synthetic|only"),
        (manifest_bytes(terms={"XX:0001": "label"}), "not a CURIE"),
        (manifest_bytes(terms={"MI:12": "label"}), "not a psi-mi CURIE"),
        (manifest_bytes(terms={"MI:0018": "a", "mi:0018": "b"}), "declared once"),
        (manifest_bytes(terms={"MI:0018": " "}), "never blank"),
        (manifest_bytes(terms={"MI:0018": "l" * 256}), "at most 255"),
        (manifest_bytes(terms={"MI:0018": 5}), "the term's label, text"),
        (manifest_bytes(terms={"MI:0018": "two\x00hybrid"}), "NUL"),
    ],
)
def test_a_manifest_is_refused_naming_what_is_wrong(data, message):
    with pytest.raises(ManifestError, match=message):
        load_manifest(data, release_is_synthetic=True)


def test_a_real_release_declares_no_synthetic_paper():
    with pytest.raises(ManifestError, match="any other release's none"):
        load_manifest(manifest_bytes({"a": paper()}), release_is_synthetic=False)


@pytest.mark.parametrize(
    "key",
    ["MI:" + "1" * 10_000, "ZZ" + "1" * 10_000 + ":0001"],
    ids=["known prefix", "unknown prefix"],
)
def test_a_refusal_quotes_a_long_term_key_short(key):
    with pytest.raises(ManifestError, match="not a (psi-mi )?CURIE") as refused:
        load_manifest(manifest_bytes(terms={key: "x"}), release_is_synthetic=True)
    message = str(refused.value)
    assert len(message) < 500 and key not in message
    assert len(message) < len(key)


# ── The resolution file ─────────────────────────────────────────────────────


@pytest.fixture
def tp53(registry):
    return create_entity("protein", "TP53", 9606, "uniprotkb", "P04637")


def entry(target, /, **fields) -> dict:
    """An entry mapping uniprotkb Q99999 to `target`, an entity, through its
    primary identifier P04637; `fields` replace any key."""
    return {
        "namespace": "uniprotkb",
        "value": "Q99999",
        "entity_type": "protein",
        "taxon": 9606,
        "entity": target.pk if isinstance(target, Entity) else target,
        "target_namespace": "uniprotkb",
        "target_value": "P04637",
        "rationale": "Reviewed against the source's supplementary table.",
        "reviewer": "A. Reviewer",
        **fields,
    }


def resolutions(*entries, **extra) -> bytes:
    return json.dumps({"resolutions": list(entries), **extra}).encode()


def test_a_resolution_file_maps_identifiers_to_reviewed_entities(tp53):
    data = resolutions(entry(tp53, value="q99999"))
    loaded = load_resolutions(data)
    [mapping] = loaded.entries
    assert (mapping.index, mapping.lookup, mapping.entity) == (1, "Q99999", tp53.pk)
    assert mapping.key == ("uniprotkb", "Q99999", "protein", 9606)
    assert loaded.get("uniprotkb", "Q99999", "protein", 9606) is mapping
    assert loaded.get("uniprotkb", "Q99999", "protein", 7227) is None
    assert loaded.sha256 == __import__("hashlib").sha256(data).hexdigest()
    assert loaded.as_json() == {
        "sha256": loaded.sha256,
        "entries": [{**entry(tp53, value="q99999"), "index": 1}],
    }
    assert NO_RESOLUTIONS.entries == () and NO_RESOLUTIONS.get("a", "b", "c", 1) is None


@pytest.mark.parametrize(
    "change,message",
    [
        ({"entity": 0}, "from 1 to 9223372036854775807"),
        ({"entity": 2**63}, "from 1 to 9223372036854775807"),
        ({"entity": True}, "from 1 to 9223372036854775807"),
        ({"entity": "1"}, "from 1 to 9223372036854775807"),
        ({"taxon": 0}, "from 1 to 2147483647 or null"),
        ({"taxon": 2**31}, "from 1 to 2147483647 or null"),
        ({"taxon": False}, "from 1 to 2147483647 or null"),
        ({"value": "Q" * 256}, "at most 255"),
        ({"target_value": "P" * 256}, "at most 255"),
        ({"reviewer": "r" * 151}, "at most 150"),
        ({"reviewer": " "}, "never blank"),
        ({"rationale": " "}, "never blank"),
        ({"rationale": "r" * 10_001}, "at most 10000"),
        ({"namespace": "n" * 51}, "at most 50"),
        ({"namespace": 5}, "is text"),
        ({"chosen_by": "someone"}, "unknown keys"),
    ],
)
def test_an_entrys_values_are_checked(tp53, change, message):
    with pytest.raises(ResolutionError, match=message):
        load_resolutions(resolutions(entry(tp53, **change)))


def test_an_entry_without_a_key_is_refused(tp53):
    incomplete = entry(tp53)
    del incomplete["reviewer"]
    with pytest.raises(ResolutionError, match="has no reviewer"):
        load_resolutions(resolutions(incomplete))


@pytest.mark.parametrize(
    "data,message",
    [
        (b"[]", "is a JSON object"),
        (b"{}", "has no resolutions"),
        (b'{"resolutions": {}}', "is a JSON list"),
        (b'{"resolutions": [], "notes": "x"}', "unknown keys"),
        (b'{"resolutions": [1]}', "is a JSON object"),
        (b'{"resolutions": [{"entity": 1.0}]}', "fraction"),
    ],
)
def test_a_resolution_file_has_one_shape(registry, data, message):
    with pytest.raises(ResolutionError, match=message):
        load_resolutions(data)


def test_a_resolution_file_holds_at_most_its_limit(tp53, monkeypatch):
    monkeypatch.setattr(limits, "MAX_RESOLUTION_ENTRIES", 1)
    with pytest.raises(ResolutionError, match="more than the 1"):
        load_resolutions(resolutions(entry(tp53), entry(tp53, value="Q88888")))


@pytest.mark.parametrize(
    "change,message",
    [
        # An isoform is never mapped to its entry this way.
        ({"value": "P04637-2"}, "is a protein_isoform identifier, not a protein"),
        # A namespace's fixed taxon is never contradicted.
        (
            {
                "namespace": "hgnc",
                "value": "HGNC:5",
                "entity_type": "gene",
                "taxon": 7227,
            },
            "belong to taxon 9606, not 7227",
        ),
        ({"value": "not an accession"}, "is not a uniprotkb identifier"),
        ({"namespace": "nowhere"}, "Unknown namespace"),
        ({"entity_type": "transcript"}, "Unknown entity type"),
        ({"taxon": None}, "A protein needs a taxon"),
        ({"taxon": 123456789}, "not in the registry"),
    ],
)
def test_the_stated_identifier_passes_identifier_problem(tp53, change, message):
    with pytest.raises(ResolutionError, match=message):
        load_resolutions(resolutions(entry(tp53, **change)))


def test_the_target_resolves_to_exactly_the_entry_entity(tp53):
    mdm2 = create_entity("protein", "MDM2", 9606, "uniprotkb", "Q00987")
    fly = create_entity("protein", "Fly protein", 7227, "uniprotkb", "Q9VCA8")
    gene = create_entity("gene", "TP53", 9606, "hgnc", "HGNC:11998")
    for entity in (tp53, mdm2):
        add_identifier(entity, "uniprotkb", "P11111", IdentifierRole.SECONDARY)
    add_identifier(mdm2, "uniprotkb", "P22222", IdentifierRole.SECONDARY)
    cases = [
        (entry(tp53, target_value="P99999"), "is unmatched"),
        (entry(tp53, target_value="P11111"), "is ambiguous"),
        (entry(mdm2, target_value="P22222"), "is candidate"),
        (entry(mdm2), f"resolves to entity {tp53.pk}, not to entity {mdm2.pk}"),
        (entry(fly, target_value="Q9VCA8"), "of taxon 7227, and the entry states"),
        (
            entry(tp53, namespace="hgnc", value="HGNC:11998", entity_type="gene"),
            "is a protein of taxon 9606, and the entry states a gene",
        ),
        (entry(gene, target_namespace="nowhere"), "target namespace 'nowhere'"),
    ]
    for case, message in cases:
        with pytest.raises(ResolutionError, match=message):
            load_resolutions(resolutions(case))


@pytest.mark.parametrize("status", [EntityStatus.RETIRED])
def test_the_target_maps_nothing_once_retired(tp53, status):
    Entity.objects.filter(pk=tp53.pk).update(status=status)
    with pytest.raises(ResolutionError, match=f"is {status}: it maps nothing"):
        load_resolutions(resolutions(entry(tp53)))


def test_two_entries_for_one_identifier_are_refused(tp53):
    with pytest.raises(ResolutionError, match="again, as entry 1 does") as refused:
        load_resolutions(resolutions(entry(tp53), entry(tp53, value="q99999")))
    assert "entry 2:" in str(refused.value)


def test_a_refusal_lists_every_entry_at_fault(tp53):
    entries = [entry(tp53, value=f"P{n:05d}", target_value="P99999") for n in range(30)]
    with pytest.raises(ResolutionError) as refused:
        load_resolutions(resolutions(*entries))
    listed = str(refused.value)
    assert "entry 1:" in listed and "entry 20:" in listed
    assert "entry 21:" not in listed and "and 10 more entries" in listed


def test_checking_entries_takes_a_constant_number_of_queries(tp53):
    def queries_for(count):
        entries = [entry(tp53, value=f"Q{n:05d}") for n in range(count)]
        with CaptureQueriesContext(connection) as queries:
            load_resolutions(resolutions(*entries))
        return len(queries)

    assert queries_for(1) == queries_for(40)


def test_a_resolution_file_survives_a_copy(tp53):
    loaded = load_resolutions(resolutions(entry(tp53)))
    again = pickle.loads(pickle.dumps(loaded.entries))
    assert again == loaded.entries
