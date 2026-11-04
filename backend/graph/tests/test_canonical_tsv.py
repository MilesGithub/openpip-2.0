"""canonical_tsv_v1 (graph.ingest.canonical_tsv): reading lines under their
limits, the header, every column and every issue code the adapter reports,
with its physical line number, the threshold grammar, scores, terms, papers
and synthetic releases."""

import codecs
import dataclasses
import hashlib
import io
import json
import tracemalloc

import pytest

from graph.claims import (
    ClaimError,
    ContextValue,
    MethodRef,
    PlannedEntity,
    PublicationRef,
    ScoreValue,
    check_claims,
)
from graph.entities import identifier_problem
from graph.ingest import limits
from graph.ingest.canonical_tsv import ADAPTER, SPEC, score_form
from graph.ingest.context import RunContext, load_registry_snapshot
from graph.ingest.manifest import load_manifest
from graph.ingest.protocol import EntityRef, FileError, duplicate_issues
from graph.scores import parse_score
from graph.tests.factories import DatasetReleaseFactory, p_value_score_type

PREFIXED = [
    "score:stats:test_p",
    "score:synthetic:confidence",
    "context:host_taxon",
    "context:tissue",
    "context:assay_system",
    "qualifier:assay_note",
]
HEADER = [*SPEC.columns, *SPEC.optional_columns, *PREFIXED]

# A human gene regulating another, as a lab's release states it.
REAL = {
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
# Two proteins interacting.
PHYSICAL = {
    **REAL,
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
    **REAL,
    "subject_namespace": "synthetic_gene",
    "subject_id": "SYN-TF1",
    "subject_taxon": "32630",
    "object_namespace": "synthetic_gene",
    "object_id": "SYN-G1",
    "object_taxon": "32630",
}
TERMS = {
    "MI:0018": "two hybrid",
    "MI:0914": "association",
    "MI:0915": "physical association",
    "MI:0407": "direct interaction",
    "MI:0492": "in vitro",
    "ECO:0000353": "physical interaction evidence",
}


def manifest(is_synthetic):
    return load_manifest(
        json.dumps(
            {
                "publications": {
                    "paper-a": {"title": "A paper", "is_synthetic": is_synthetic}
                },
                "terms": TERMS,
            }
        ).encode(),
        release_is_synthetic=is_synthetic,
    )


@pytest.fixture
def snapshot(registry):
    p_value_score_type()
    return load_registry_snapshot()


@pytest.fixture
def ctx(snapshot):
    """A release that is not synthetic."""
    return RunContext(snapshot, False, manifest(False))


@pytest.fixture
def synthetic_ctx(snapshot):
    return RunContext(snapshot, True, manifest(True))


def line(row, header=HEADER) -> str:
    return "\t".join(row.get(column, "") for column in header)


def tsv(*rows, header=HEADER, ending="\n") -> bytes:
    return (
        ending.join(["\t".join(header), *(line(row, header) for row in rows)]).encode(
            "utf-8"
        )
        + ending.encode()
    )


def read(data, ctx):
    records = ADAPTER.iter_records(io.BytesIO(data), ctx)
    return records, list(records)


def mapped_rows(ctx, *rows):
    """Each row mapped, on lines 2, 3, ..."""
    _, records = read(tsv(*rows), ctx)
    return [ADAPTER.map_record(record, ctx) for record in records]


def mapped(ctx, base=REAL, **cells):
    """The row on line 3, after a valid one on line 2."""
    return mapped_rows(ctx, base, {**base, "record_id": "r2", **cells})[1]


def issues(result) -> list:
    return [(issue.code, issue.field, issue.line) for issue in result.issues]


def claim_of(result):
    [claim] = result.claims
    return claim


# ── Reading lines ───────────────────────────────────────────────────────────


def exact_row(size):
    """A header and a valid row whose line is exactly `size` bytes, its
    length spread over qualifier columns within MAX_FIELD_CHARS."""
    base = line(REAL, SPEC.columns)
    remaining = size - len(base)
    width = limits.MAX_FIELD_CHARS
    count = -(-remaining // (width + 1))
    header = [*SPEC.columns, *(f"qualifier:q{n}" for n in range(count))]
    values = ["x" * width] * (count - 1)
    values.append("x" * (remaining - (count - 1) * (width + 1) - 1))
    row = "\t".join([base, *values])
    assert len(row.encode()) == size and len(header) <= limits.MAX_COLUMNS
    return header, row


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_a_line_of_exactly_max_line_bytes_is_read(ctx, ending):
    columns, row = exact_row(limits.MAX_LINE_BYTES)
    _, [record] = read(("\t".join(columns) + ending + row + ending).encode(), ctx)
    assert (record.record_key, record.issues, record.size) == (
        "r1",
        (),
        limits.MAX_LINE_BYTES,
    )
    assert record.raw_sha256 == hashlib.sha256(row.encode()).hexdigest()


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_one_byte_more_is_too_long(ctx, ending):
    columns, row = exact_row(limits.MAX_LINE_BYTES + 1)
    header = "\t".join(columns)
    data = (header + "\n" + row + ending + line(REAL, columns) + "\n").encode()
    if not ending:
        data = (header + "\n" + row).encode()
    _, records = read(data, ctx)
    first = records[0]
    assert (first.line_number, first.record_key, first.fields) == (2, None, None)
    assert [issue.code for issue in first.issues] == ["line_too_long"]
    assert first.size == limits.MAX_LINE_BYTES + 1
    assert first.raw_sha256 == hashlib.sha256(row.encode()).hexdigest()
    if ending:
        # The next line is read as if nothing happened.
        assert (records[1].line_number, records[1].issues) == (3, ())


def test_a_last_line_without_a_terminator_is_read(ctx):
    data = tsv(REAL, {**REAL, "record_id": "r2"})[:-1]
    _, records = read(data, ctx)
    assert [(r.line_number, r.record_key, r.issues) for r in records] == [
        (2, "r1", ()),
        (3, "r2", ()),
    ]


def test_a_crlf_ending_is_not_part_of_the_line(ctx):
    _, [lf] = read(tsv(REAL), ctx)
    _, [crlf] = read(tsv(REAL, ending="\r\n"), ctx)
    assert (
        lf.raw_sha256
        == crlf.raw_sha256
        == hashlib.sha256(line(REAL).encode()).hexdigest()
    )
    assert lf.fields == crlf.fields


def test_a_64_mib_line_without_lf_is_read_in_bounded_memory(ctx, tmp_path):
    path = tmp_path / "long.tsv"
    chunk = b"x" * (1024 * 1024)
    long_line = hashlib.sha256()
    with open(path, "wb") as out:
        out.write(("\t".join(HEADER) + "\n").encode())
        out.write(b"r1\t")
        long_line.update(b"r1\t")
        for _ in range(64):
            out.write(chunk)
            long_line.update(chunk)
    tracemalloc.start()
    try:
        with open(path, "rb") as stream:
            records = list(ADAPTER.iter_records(stream, ctx))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, peak
    [record] = records
    assert [issue.code for issue in record.issues] == ["line_too_long"]
    assert record.size == 64 * 1024 * 1024 + 3
    assert record.raw_sha256 == long_line.hexdigest()


def test_a_cr_that_ends_a_chunk_and_an_lf_that_starts_the_next_are_one_crlf(ctx):
    columns, row = exact_row(limits.MAX_LINE_BYTES + 1)
    header = "\t".join(columns)
    # readline() returns the line's first MAX + 2 bytes, ending with its CR.
    data = (header + "\n" + row + "\r\n").encode()
    assert data[len(header) + 1 :][: limits.MAX_LINE_BYTES + 2].endswith(b"\r")
    _, [record] = read(data, ctx)
    assert record.size == limits.MAX_LINE_BYTES + 1
    assert record.raw_sha256 == hashlib.sha256(row.encode()).hexdigest()


def test_a_leading_byte_order_mark_is_dropped(ctx):
    records, [record] = read(codecs.BOM_UTF8 + tsv(REAL), ctx)
    assert records.columns[0] == "record_id"
    assert record.record_key == "r1"


@pytest.mark.parametrize(
    "data,code,message",
    [
        ("\t".join(HEADER).encode("utf-16"), "encoding", "UTF-16 or UTF-32"),
        ("\t".join(HEADER).encode("utf-32"), "encoding", "UTF-16 or UTF-32"),
        ("\t".join(HEADER).encode("utf-16-le"), "encoding", "NUL"),
        ("\t".join(HEADER).encode("utf-16-be"), "encoding", "NUL"),
        (b"record_id\t\xff\xfe\n", "encoding", "not UTF-8"),
        (b"", "no_header", "empty"),
        (b"\n" + tsv(REAL), "no_header", "first line is blank"),
        (b" \t \r\n", "no_header", "first line is blank"),
        (tsv(REAL, ending="\r"), "control_character", "carriage return"),
        (b"record_id\x1b[0m\tx\n", "control_character", r"U\+001B"),
    ],
)
def test_an_unreadable_header_refuses_the_file(ctx, data, code, message):
    with pytest.raises(FileError, match=message) as refused:
        read(data, ctx)
    [issue] = refused.value.issues
    assert (issue.code, issue.line) == (code, 1)


def test_a_header_over_the_line_limit_refuses_the_file(ctx):
    data = b"record_id\t" + b"x" * limits.MAX_LINE_BYTES + b"\n"
    with pytest.raises(FileError) as refused:
        read(data, ctx)
    assert [issue.code for issue in refused.value.issues] == ["line_too_long"]


def test_a_cr_inside_a_field_rejects_its_row_only(ctx):
    _, records = read(
        tsv(REAL, {**REAL, "record_id": "r2", "subject_label": "TP\r53"}, REAL), ctx
    )
    assert [(r.line_number, r.record_key) for r in records] == [
        (2, "r1"),
        (3, None),
        (4, "r1"),
    ]
    [issue] = records[1].issues
    assert (issue.code, issue.field, issue.line) == (
        "control_character",
        "subject_label",
        3,
    )
    assert "carriage return" in issue.message


@pytest.mark.parametrize("character", ["\x0b", "\r"])
def test_a_row_with_a_control_character_keeps_its_cells_if_they_split(
    ctx, synthetic_ctx, character
):
    # Unstored either way; a synthetic release checks the cells that split
    # into the header's columns, and cannot check the others.
    split = line({**REAL, "evidence_group": f"a{character}b"})
    data = "\n".join(["\t".join(HEADER), split, split + "\textra"]).encode()
    _, [kept, unsplit] = read(data, ctx)
    for record in (kept, unsplit):
        assert record.record_key is None
        assert [issue.code for issue in record.issues] == ["control_character"]
        assert ADAPTER.map_record(record, ctx).claims == ()
    assert kept.fields["evidence_group"] == f"a{character}b"
    assert kept.fields["subject_namespace"] == "hgnc"
    assert unsplit.fields is None
    found = ADAPTER.synthetic_issues(kept, synthetic_ctx)
    assert {issue.field for issue in found} == {
        f"{side}_{cell}"
        for side in ("subject", "object")
        for cell in ("namespace", "taxon")
    }
    assert ADAPTER.synthetic_issues(unsplit, synthetic_ctx) == ()


@pytest.mark.parametrize(
    "character,named",
    [("\x00", "NUL"), ("\x1b", "U+001B"), ("\x7f", "U+007F"), ("\x85", "U+0085")],
)
def test_any_other_control_character_rejects_its_row(ctx, character, named):
    _, [record] = read(tsv({**REAL, "evidence_group": f"g{character}1"}), ctx)
    [issue] = record.issues
    assert (issue.code, issue.field) == ("control_character", "evidence_group")
    assert named in issue.message


def test_line_and_paragraph_separators_are_text(ctx):
    label = "TP53\u2028second\u2029third"
    _, records = read(
        tsv({**REAL, "subject_label": label}, {**REAL, "record_id": "r2"}), ctx
    )
    assert [(r.line_number, r.issues) for r in records] == [(2, ()), (3, ())]
    assert records[0].fields["subject_label"] == label


def test_bad_utf8_rejects_its_row_only(ctx):
    data = (
        tsv(REAL).replace(b"r1\t", b"r\xc3\x28\t", 1)
        + line({**REAL, "record_id": "r2"}).encode()
        + b"\n"
    )
    _, records = read(data, ctx)
    [issue] = records[0].issues
    assert (issue.code, issue.line, records[0].fields) == ("bad_utf8", 2, None)
    assert "byte 2" in issue.message
    assert (records[1].record_key, records[1].issues) == ("r2", ())


def test_blank_lines_are_skipped_and_counted(ctx):
    data = tsv(REAL, {}, {**REAL, "record_id": "r2"})
    data = data.replace(
        b"\n" + line({}).encode() + b"\n", b"\n\n \t \r\n" + b"\t" * 3 + b"\n"
    )
    records, rows = read(data, ctx)
    assert [(r.line_number, r.record_key) for r in rows] == [(2, "r1"), (6, "r2")]
    assert (records.lines, records.blank_lines) == (6, 3)


@pytest.mark.parametrize(
    "change,code,field",
    [
        (lambda text: text + "\textra", "field_count", ""),
        (lambda text: text.rsplit("\t", 1)[0], "field_count", ""),
        (
            lambda text: text.replace("HGNC:6973", "X" * 10_001),
            "field_too_long",
            "object_id",
        ),
    ],
)
def test_a_row_without_trustworthy_values_is_unstored(ctx, change, code, field):
    data = "\n".join(["\t".join(HEADER), change(line(REAL))]).encode()
    _, [record] = read(data, ctx)
    assert record.record_key is None
    if code == "field_count":
        assert record.fields is None
    else:
        # Its cells split, so a synthetic release still checks them.
        assert record.fields[field] == "X" * 10_001
    [issue] = record.issues
    assert (issue.code, issue.field, issue.line) == (code, field, 2)
    assert ADAPTER.map_record(record, ctx).claims == ()
    assert ADAPTER.map_record(record, ctx).issues == record.issues


def test_a_field_of_exactly_the_limit_is_kept(ctx):
    _, [record] = read(
        tsv({**REAL, "evidence_group": "g" * limits.MAX_FIELD_CHARS}), ctx
    )
    assert record.issues == ()


@pytest.mark.parametrize(
    "record_id",
    ["", " r1", "r1 ", "-r1", "r1|x", "r\u00e91", "\ufeffr1", "r" * 256],
)
def test_a_malformed_record_id_is_unstored(ctx, record_id):
    _, [record] = read(tsv({**REAL, "record_id": record_id}), ctx)
    assert [(i.code, i.field, i.line) for i in record.issues] == [
        ("bad_record_id", "record_id", 2)
    ]
    assert record.record_key is None
    assert record.fields["record_id"] == record_id


@pytest.mark.parametrize("record_id", ["a", "r1.x:y-z_2", "R" * 255, "0"])
def test_a_record_id_is_the_record_key(ctx, record_id):
    _, [record] = read(tsv({**REAL, "record_id": record_id}), ctx)
    assert (record.record_key, record.record_key_basis) == (record_id, "source_id")


def test_too_many_lines_refuse_the_file_where_they_start(ctx, monkeypatch):
    monkeypatch.setattr(limits, "MAX_DATA_LINES", 3)
    rows = [{**REAL, "record_id": f"r{n}"} for n in range(5)]
    records = ADAPTER.iter_records(io.BytesIO(tsv(*rows)), ctx)
    read_so_far = []
    with pytest.raises(FileError) as refused:
        for record in records:
            read_so_far.append(record.record_key)
    assert read_so_far == ["r0", "r1", "r2"]
    [issue] = refused.value.issues
    assert (issue.code, issue.line) == ("too_many_lines", 5)


def test_identical_and_conflicting_duplicates_are_read_as_written(ctx):
    """The adapter gives each line its key, line number and hash; the dry
    run, whose stored records are the keys seen, words the duplicates with
    duplicate_issues (graph.ingest.protocol), whatever batches lie between
    the lines."""
    rows = [{**REAL, "record_id": f"r{n}"} for n in range(3, 2000)]
    first = {**REAL, "record_id": "dup"}
    other = {**REAL, "record_id": "conflict"}
    data = tsv(
        first,
        other,
        *rows,
        first,
        {**other, "effect": "activating"},
    )
    _, records = read(data, ctx)
    by_key = {}
    for record in records:
        by_key.setdefault(record.record_key, []).append(record)
    same, differing = by_key["dup"], by_key["conflict"]
    assert [r.line_number for r in same] == [2, 2001]
    assert [r.line_number for r in differing] == [3, 2002]
    assert same[0].raw_sha256 == same[1].raw_sha256
    assert differing[0].raw_sha256 != differing[1].raw_sha256
    # As the dry run applies the rule, lines 2,000 apart: more than a batch.
    assert 2001 - 2 > limits.BATCH_ROWS
    seen, unstored, rejected = {}, {}, {}
    for record in records:
        first = seen.setdefault(record.record_key, record)
        if first is record:
            continue
        later, earlier = duplicate_issues(record, first.line_number, first.raw_sha256)
        unstored[later.line] = later.code
        if earlier is not None:
            rejected[earlier.line] = (earlier.code, earlier.severity)
    assert unstored == {2001: "duplicate_record", 2002: "conflicting_duplicate"}
    # The first version is rejected too: neither is imported (D12).
    assert rejected == {3: ("conflicting_duplicate", "error")}


# ── The header ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "header,code,field",
    [
        ([c for c in HEADER if c != "outcome"], "header_missing_column", "outcome"),
        ([*HEADER, "source"], "header_unknown_column", "source"),
        ([*HEADER, ""], "header_unknown_column", ""),
        ([*HEADER, "Record_ID"], "header_unknown_column", "Record_ID"),
        ([*HEADER, "score:stats:nope"], "unknown_score_type", "score:stats:nope"),
        ([*HEADER, "score:"], "unknown_score_type", "score:"),
        ([*HEADER, "context:organ"], "unknown_facet", "context:organ"),
        ([*HEADER, "qualifier:Bad"], "header_unknown_column", "qualifier:Bad"),
        (
            [*HEADER, "qualifier:threshold"],
            "header_unknown_column",
            "qualifier:threshold",
        ),
        (
            [*HEADER, "qualifier:" + "k" * 65],
            "header_unknown_column",
            "qualifier:" + "k" * 65,
        ),
        ([*HEADER, "method"], "header_duplicate_column", "method"),
    ],
)
def test_a_header_problem_refuses_the_file(ctx, header, code, field):
    with pytest.raises(FileError) as refused:
        read(tsv(REAL, header=header), ctx)
    assert [(i.code, i.field, i.line) for i in refused.value.issues] == [
        (code, field, 1)
    ]


def test_every_header_problem_is_listed_and_no_record_is_read(ctx):
    header = [c for c in HEADER if c not in ("outcome", "record_id")] + ["source"]
    records = ADAPTER.iter_records(io.BytesIO(tsv(REAL, header=header)), ctx)
    with pytest.raises(FileError) as refused:
        next(records)
    assert sorted(i.code for i in refused.value.issues) == [
        "header_missing_column",
        "header_missing_column",
        "header_unknown_column",
    ]
    assert records.lines == 1


def test_more_than_max_columns_refuse_the_file(ctx):
    header = [*SPEC.columns, *(f"qualifier:q{n}" for n in range(limits.MAX_COLUMNS))]
    with pytest.raises(FileError) as refused:
        read(tsv(REAL, header=header), ctx)
    assert [i.code for i in refused.value.issues] == ["too_many_columns"]


def test_the_required_columns_alone_make_a_header(ctx):
    records, [record] = read(tsv(REAL, header=list(SPEC.columns)), ctx)
    assert records.columns == SPEC.columns
    assert ADAPTER.map_record(record, ctx).issues == ()


# ── Every column ────────────────────────────────────────────────────────────


def test_every_column_becomes_part_of_the_claim(ctx):
    result = mapped(
        ctx,
        PHYSICAL,
        subject_label="TP53",
        object_label=" MDM2 ",
        directed="false",
        interaction_type="mi:0407",
        method="MI:0018",
        eco="ECO:0000353",
        publications="pmid:017081976|doi:10.1000/ABC|ref:paper-a",
        evidence_group="complex-7",
        **{
            "score:stats:test_p": "0.01",
            "context:host_taxon": "9606",
            "context:tissue": "liver",
            "context:assay_system": "MI:0492",
            "qualifier:assay_note": "Done twice.",
        },
    )
    assert issues(result) == [("pmid_and_doi", "publications", 3)]
    claim = claim_of(result)
    assert claim.relationship_type == "physical_interaction"
    assert claim.subject.entity == EntityRef(
        "uniprotkb", "P04637", "protein", 9606, "TP53"
    )
    assert claim.object.entity == EntityRef(
        "uniprotkb", "Q00987", "protein", 9606, " MDM2 "
    )
    assert (claim.subject.as_stated, claim.subject.basis) == ("P04637", "")
    assert (claim.outcome, claim.evidence_basis, claim.assertion_method) == (
        "supported",
        "experimental",
        "manual_assertion",
    )
    assert (claim.effect, claim.genetic_sign, claim.stated_directed) == (
        None,
        None,
        False,
    )
    assert (claim.interaction_type, claim.interaction_level) == (
        "MI:0407",
        "direct_interaction",
    )
    assert claim.methods == (MethodRef("MI:0018", "detection"),)
    assert claim.eco_term == "ECO:0000353"
    assert claim.publications == (
        PublicationRef(None, "reported_in", {"pmid": "17081976"}),
        PublicationRef(None, "reported_in", {"doi": "10.1000/ABC"}),
        PublicationRef(None, "reported_in", {"local": "paper-a"}),
    )
    assert claim.evidence_group == "complex-7"
    assert claim.scores == (ScoreValue("stats:test_p", "0.01"),)
    assert claim.contexts == (
        ContextValue("host_taxon", "9606", taxon=9606),
        ContextValue("tissue", "liver"),
        ContextValue("assay_system", "MI:0492", term="MI:0492"),
    )
    assert claim.qualifiers == {"assay_note": "Done twice."}
    assert claim.key_part == ""


def test_blank_optional_cells_state_nothing(ctx):
    claim = claim_of(mapped(ctx, effect=" ", evidence_group="", directed=""))
    assert (claim.effect, claim.evidence_group, claim.stated_directed) == (
        None,
        "",
        None,
    )
    assert (claim.methods, claim.publications, claim.scores, claim.contexts) == (
        (),
        (),
        (),
        (),
    )
    assert claim.qualifiers == {}
    assert claim.subject.entity.label == ""


@pytest.mark.parametrize(
    "cells,value",
    [
        ({"effect": "activating"}, ("activating", None)),
        ({"effect": "inhibiting"}, ("inhibiting", None)),
        ({"effect": "unknown"}, ("unknown", None)),
        ({"genetic_sign": "negative"}, (None, "negative")),
        ({"genetic_sign": "positive"}, (None, "positive")),
    ],
)
def test_effects_and_signs_are_taken_as_stated(ctx, cells, value):
    claim = claim_of(mapped(ctx, **cells))
    assert (claim.effect, claim.genetic_sign) == value


@pytest.mark.parametrize(
    "written,stated", [("true", True), ("false", False), ("", None)]
)
def test_directed_is_only_checked_against_the_type(ctx, written, stated):
    assert claim_of(mapped(ctx, directed=written)).stated_directed is stated


@pytest.mark.parametrize(
    "base,cells,level",
    [
        (PHYSICAL, {"interaction_type": "MI:0914"}, "association"),
        (PHYSICAL, {"interaction_type": "MI:0915"}, "physical_association"),
        (PHYSICAL, {"interaction_type": "MI:0407"}, "direct_interaction"),
        (PHYSICAL, {}, "not_stated"),
        (
            {**PHYSICAL, "relationship_type": "protein_interaction_unspecified"},
            {},
            "not_stated",
        ),
        (REAL, {}, None),
    ],
)
def test_the_level_comes_from_the_list_or_is_not_stated(ctx, base, cells, level):
    """No level column: a listed type's level, not_stated on an untyped
    physical row, and none on any other family."""
    result = mapped(ctx, base, **cells)
    assert issues(result) == []
    assert claim_of(result).interaction_level == level


def test_a_threshold_becomes_the_criteria_g3_reads(ctx):
    claim = claim_of(
        mapped(
            ctx,
            outcome="not_significant",
            threshold="stats:test_p<0.05; stats:test_p  abs>=  1e-3 ;stats:test_p > -1",
            **{"score:stats:test_p": "0.2"},
        )
    )
    assert claim.qualifiers == {
        "threshold": [
            {"score_type": "stats:test_p", "operator": "<", "value": "0.05"},
            {"score_type": "stats:test_p", "operator": "abs>=", "value": "1e-3"},
            {"score_type": "stats:test_p", "operator": ">", "value": "-1"},
        ]
    }


@pytest.mark.parametrize(
    "threshold",
    [
        "stats:test_p ~ 0.05",
        "stats:test_p <",
        "stats:test_p < abc",
        "stats:test_p < 0.05;",
        "stats:test_p < 0.05 0.1",
        "< 0.05",
        "Stats:test_p < 0.05",
        "stats:test_q < 0.05",
        "stats:test_p =< 0.05",
    ],
)
def test_a_malformed_threshold_is_refused(ctx, threshold):
    result = mapped(
        ctx,
        outcome="not_significant",
        threshold=threshold,
        **{"score:stats:test_p": "0.2"},
    )
    assert issues(result) == [("bad_threshold", "threshold", 3)]


def test_a_thresholds_score_type_needs_a_score_on_the_row(ctx):
    result = mapped(ctx, outcome="not_significant", threshold="stats:test_p < 0.05")
    assert issues(result) == [("bad_threshold", "threshold", 3)]
    assert "has no score:stats:test_p score" in result.issues[0].message


@pytest.mark.parametrize(
    "raw,form",
    [
        ("0.5", "number"),
        (" 1e-300 ", "number"),
        ("-2.5E3", "number"),
        ("1e999", "number"),
        ("<2.2e-16", "censored"),
        ("<= 0.001", "censored"),
        (">1e999", "censored"),
        ("NaN", "not_finite"),
        ("nan", "not_finite"),
        ("-inf", "not_finite"),
        ("+Infinity", "not_finite"),
        ("INF", "not_finite"),
        ("NA", "text"),
        ("-", "text"),
        ("n/a", "text"),
        ("0,5", "text"),
        ("\u0665", "text"),
        ("< 2.2e-16 approx", "text"),
    ],
)
def test_score_form_reads_as_graph_scores_reads(raw, form):
    assert score_form(raw) == form
    parsed = parse_score(raw)
    if form == "number":
        assert parsed.value is not None or "overflows" in parsed.note
    else:
        assert parsed.value is None


def test_the_score_forms_are_graph_scores_own(monkeypatch):
    """graph.scores owns the rule (spec §1.6): parse_score chooses its
    branch by score_form, which the adapter imports, not a copy."""
    from graph import scores

    assert score_form is scores.score_form
    monkeypatch.setattr(scores, "score_form", lambda raw: "text")
    assert scores.parse_score("1.5").value is None


@pytest.mark.parametrize(
    "raw,codes",
    [
        ("0.01", []),
        ("1e999", []),
        ("<2.2e-16", [("censored_score", "score:stats:test_p", 3)]),
        ("NaN", [("score_not_finite", "score:stats:test_p", 3)]),
        ("-Infinity", [("score_not_finite", "score:stats:test_p", 3)]),
        ("NA", [("score_not_a_number", "score:stats:test_p", 3)]),
        ("-", [("score_not_a_number", "score:stats:test_p", 3)]),
    ],
)
def test_scores_are_kept_verbatim_or_refused(ctx, raw, codes):
    result = mapped(ctx, **{"score:stats:test_p": raw})
    assert issues(result) == codes
    kept = claim_of(result).scores
    if not codes or codes[0][0] == "censored_score":
        assert kept == (ScoreValue("stats:test_p", raw),)
        assert result.issues == () or result.issues[0].severity == "note"
    else:
        assert kept == ()


@pytest.mark.parametrize(
    "column,value",
    [
        ("interaction_type", "MI:0915"),
        ("method", "MI:0018"),
        ("eco", "ECO:0000353"),
        ("context:assay_system", "MI:0492"),
    ],
)
def test_a_declared_term_is_accepted_and_an_undeclared_one_refused(
    snapshot, column, value
):
    declared = RunContext(snapshot, False, manifest(False))
    bare = RunContext(snapshot, False, load_manifest(b"{}", release_is_synthetic=False))
    base = PHYSICAL if column == "interaction_type" else REAL
    assert issues(mapped(declared, base, **{column: value})) == []
    # Whether or not the term is stored: a release's outcome never depends on
    # what another release committed.
    assert issues(mapped(bare, base, **{column: value})) == [
        ("term_undeclared", column, 3)
    ]


def test_a_ref_key_names_a_manifest_paper(ctx):
    claim = claim_of(mapped(ctx, publications="ref:paper-a"))
    assert claim.publications == (
        PublicationRef(None, "reported_in", {"local": "paper-a"}),
    )


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script>",
        '=HYPERLINK("https://example.org","x")',
        "+cmd|' /C calc'!A0",
        "@SUM(1+1)",
        "-1+1",
        "../../etc/passwd",
        "C:\\Windows\\system32",
        "' OR 1=1 --",
    ],
)
def test_malicious_strings_are_kept_as_text(ctx, text):
    result = mapped(
        ctx,
        subject_label=text,
        evidence_group=text[:255],
        **{"context:tissue": text, "qualifier:assay_note": text},
    )
    assert issues(result) == []
    claim = claim_of(result)
    assert claim.subject.entity.label == text
    assert claim.evidence_group == text[:255]
    assert claim.contexts == (ContextValue("tissue", text),)
    assert claim.qualifiers == {"assay_note": text}


# ── Every issue code the adapter reports ────────────────────────────────────


ROW_ISSUES = [
    (REAL, {"method": "|".join(["MI:0018"] * 51)}, "too_many_values", "method"),
    (
        REAL,
        {"publications": "|".join(f"pmid:{n}" for n in range(1, 52))},
        "too_many_values",
        "publications",
    ),
    (REAL, {"subject_id": "11998"}, "hgnc_without_prefix", "subject_id"),
    (REAL, {"object_id": " 6973"}, "hgnc_without_prefix", "object_id"),
    (REAL, {"subject_taxon": "09606"}, "bad_taxon", "subject_taxon"),
    (REAL, {"object_taxon": "-1"}, "bad_taxon", "object_taxon"),
    (REAL, {"subject_taxon": "9606 "}, "bad_taxon", "subject_taxon"),
    (REAL, {"subject_taxon": "2147483648"}, "bad_taxon", "subject_taxon"),
    (REAL, {"subject_taxon": "\uff19\uff16\uff10\uff16"}, "bad_taxon", "subject_taxon"),
    (REAL, {"subject_taxon": "9_606"}, "bad_taxon", "subject_taxon"),
    (REAL, {"context:host_taxon": "-1"}, "bad_taxon", "context:host_taxon"),
    (
        REAL,
        {"relationship_type": "regulates"},
        "unknown_relationship_type",
        "relationship_type",
    ),
    (REAL, {"relationship_type": ""}, "unknown_relationship_type", "relationship_type"),
    (
        REAL,
        {"relationship_type": "encodes"},
        "reference_type_not_allowed",
        "relationship_type",
    ),
    (
        REAL,
        {"relationship_type": "isoform_of"},
        "reference_type_not_allowed",
        "relationship_type",
    ),
    (REAL, {"outcome": "maybe"}, "bad_value", "outcome"),
    (REAL, {"outcome": "Supported"}, "bad_value", "outcome"),
    (REAL, {"outcome": ""}, "bad_value", "outcome"),
    (REAL, {"evidence_basis": "experiment"}, "bad_value", "evidence_basis"),
    (REAL, {"assertion_method": "manual"}, "bad_value", "assertion_method"),
    (REAL, {"directed": "yes"}, "bad_value", "directed"),
    (REAL, {"effect": "up"}, "bad_value", "effect"),
    (REAL, {"effect": "not_applicable"}, "bad_value", "effect"),
    (REAL, {"genetic_sign": "neg"}, "bad_value", "genetic_sign"),
    (REAL, {"effect": "negative"}, "negative_effect", "effect"),
    (REAL, {"effect": " Negative "}, "negative_effect", "effect"),
    (
        PHYSICAL,
        {"interaction_type": "MI:0195"},
        "interaction_type_not_listed",
        "interaction_type",
    ),
    (
        PHYSICAL,
        {"interaction_type": "MI:0403"},
        "interaction_type_not_listed",
        "interaction_type",
    ),
    (
        PHYSICAL,
        {"interaction_type": "ECO:0000353"},
        "interaction_type_not_listed",
        "interaction_type",
    ),
    (REAL, {"method": "MI:0018|MI:0915"}, "method_is_interaction_type", "method"),
    (REAL, {"method": "MI:0006"}, "term_undeclared", "method"),
    (REAL, {"eco": "ECO:0000001"}, "term_undeclared", "eco"),
    (PHYSICAL, {"interaction_type": "MI:0195"}, "term_undeclared", "interaction_type"),
    (
        REAL,
        {"context:assay_system": "MI:0493"},
        "term_undeclared",
        "context:assay_system",
    ),
    (REAL, {"method": "MI:12"}, "bad_curie", "method"),
    (REAL, {"method": "MI:0018||MI:0018"}, "bad_curie", "method"),
    (REAL, {"eco": "eco 0000353"}, "bad_curie", "eco"),
    (PHYSICAL, {"interaction_type": "psi-mi:MI:0915"}, "bad_curie", "interaction_type"),
    (REAL, {"context:tissue": "MI:abc"}, "bad_curie", "context:tissue"),
    (REAL, {"score:stats:test_p": "nan"}, "score_not_finite", "score:stats:test_p"),
    (REAL, {"score:stats:test_p": "n/a"}, "score_not_a_number", "score:stats:test_p"),
    (REAL, {"threshold": "stats:test_p ~ 1"}, "bad_threshold", "threshold"),
    (REAL, {"publications": "ref:missing"}, "unknown_ref", "publications"),
    (REAL, {"publications": "ref:bad key"}, "unknown_ref", "publications"),
    (REAL, {"publications": "PMID:1"}, "bad_paper", "publications"),
    (REAL, {"publications": "pubmed:1"}, "bad_paper", "publications"),
    (REAL, {"publications": "isbn:0-306-40615-2"}, "bad_paper", "publications"),
    (REAL, {"publications": "pmid:abc"}, "bad_paper", "publications"),
    (REAL, {"publications": "pmid:0"}, "bad_paper", "publications"),
    (REAL, {"publications": "doi:11.1000/x"}, "bad_paper", "publications"),
    (REAL, {"publications": "pmid:1|"}, "bad_paper", "publications"),
    (REAL, {"publications": "pmid:1|pmid:01"}, "duplicate_paper", "publications"),
    (
        REAL,
        {"publications": "doi:10.1/A|doi:10.1/a"},
        "duplicate_paper",
        "publications",
    ),
    (
        REAL,
        {"publications": "ref:paper-a|ref:paper-a"},
        "duplicate_paper",
        "publications",
    ),
    (REAL, {"subject_label": "x" * 256}, "label_too_long", "subject_label"),
    (REAL, {"score:stats:test_p": "<2.2e-16"}, "censored_score", "score:stats:test_p"),
    (REAL, {"publications": "pmid:1|doi:10.1/x"}, "pmid_and_doi", "publications"),
]


@pytest.mark.parametrize(
    "base,cells,code,field",
    ROW_ISSUES,
    ids=[f"{case[2]}-{n}" for n, case in enumerate(ROW_ISSUES)],
)
def test_each_issue_names_its_code_column_and_line(ctx, base, cells, code, field):
    result = mapped(ctx, base, **cells)
    assert (code, field, 3) in issues(result)
    for issue in result.issues:
        assert issue.message.endswith(".") and len(issue.message) < 2_000


def test_each_code_the_adapter_reports_is_exercised():
    reported = {case[2] for case in ROW_ISSUES}
    assert reported == {
        "too_many_values",
        "hgnc_without_prefix",
        "bad_taxon",
        "unknown_relationship_type",
        "reference_type_not_allowed",
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
        "censored_score",
        "pmid_and_doi",
    }


def test_a_label_of_exactly_the_limit_is_kept(ctx):
    assert issues(mapped(ctx, subject_label=" " + "x" * 255 + " ")) == []


def test_an_hgnc_value_carries_its_prefix_in_any_case(ctx):
    for value in ("HGNC:11998", "hgnc:11998", " HGNC:11998"):
        assert issues(mapped(ctx, subject_id=value)) == []


def test_a_blank_taxon_is_left_to_the_types_taxon_policy(ctx):
    claim = claim_of(mapped(ctx, subject_taxon=""))
    assert claim.subject.entity.taxon is None
    # The shared mapping refuses it there, as create_entity would.
    assert identifier_problem("gene", None, "hgnc", "HGNC:11998") == (
        "A gene needs a taxon."
    )


def test_a_message_quotes_a_long_value_truncated(ctx):
    result = mapped(ctx, subject_taxon="9" * 5000)
    [issue] = result.issues
    assert len(issue.message) < 400 and "..." in issue.message


# ── Synthetic releases ──────────────────────────────────────────────────────


def test_a_synthetic_row_passes(synthetic_ctx):
    result = mapped(
        synthetic_ctx,
        SYNTHETIC,
        subject_label="SYN-TF1",
        publications="ref:paper-a",
        **{"context:host_taxon": "32630", "score:synthetic:confidence": "0.9"},
    )
    assert issues(result) == []
    # An unregistered host taxon is no real organism the release names.
    assert (
        issues(mapped(synthetic_ctx, SYNTHETIC, **{"context:host_taxon": "4932"})) == []
    )


@pytest.mark.parametrize(
    "cells,fields",
    [
        (
            {
                "subject_namespace": "hgnc",
                "subject_id": "HGNC:11998",
                "subject_taxon": "9606",
            },
            ["subject_namespace", "subject_taxon"],
        ),
        ({"object_taxon": "9606"}, ["object_taxon"]),
        ({"object_taxon": ""}, ["object_taxon"]),
        (
            {"subject_namespace": "uniprotkb", "subject_id": "P04637"},
            ["subject_namespace"],
        ),
        ({"subject_label": "TP53"}, ["subject_label"]),
        ({"object_label": "syn-g1"}, ["object_label"]),
        ({"publications": "pmid:1"}, ["publications"]),
        ({"publications": "doi:10.1000/x"}, ["publications"]),
        ({"publications": "pmid:abc"}, ["publications"]),
        # However many papers a cell lists, past the limit too.
        (
            {"publications": "|".join(f"pmid:{n}" for n in range(1, 61))},
            ["publications"],
        ),
        ({"context:host_taxon": "9606"}, ["context:host_taxon"]),
    ],
)
def test_a_synthetic_release_names_no_real_data(synthetic_ctx, cells, fields):
    result = mapped(synthetic_ctx, SYNTHETIC, **cells)
    violations = [
        (issue.field, issue.line)
        for issue in result.issues
        if issue.code == "synthetic_violation"
    ]
    assert violations == [(field, 3) for field in fields]


def test_every_readable_row_is_checked_rejected_ones_included(synthetic_ctx):
    result = mapped(
        synthetic_ctx, SYNTHETIC, outcome="maybe", subject_label="Real gene"
    )
    assert {issue.code for issue in result.issues} == {
        "bad_value",
        "synthetic_violation",
    }


def test_a_release_that_is_not_synthetic_is_not_checked_for_synthetic_names(ctx):
    assert issues(mapped(ctx, SYNTHETIC)) == []


def test_reference_links_are_accepted_in_a_synthetic_release_only(ctx, synthetic_ctx):
    row = {**SYNTHETIC, "relationship_type": "encodes"}
    assert issues(mapped(synthetic_ctx, row)) == []
    assert ("reference_type_not_allowed", "relationship_type", 3) in issues(
        mapped(ctx, row)
    )


# ── D12: must be reported, never imported ───────────────────────────────────


def planned(claim):
    """The claim as the shared mapping would check it with a planned entity
    on each side."""
    return dataclasses.replace(
        claim,
        subject=dataclasses.replace(
            claim.subject,
            entity=PlannedEntity(claim.subject.entity.entity_type, 32630),
            basis="primary",
        ),
        object=dataclasses.replace(
            claim.object,
            entity=PlannedEntity(claim.object.entity.entity_type, 32630),
            basis="primary",
        ),
    )


def g3_refusal(claim, release):
    with pytest.raises(ClaimError) as refused:
        check_claims([planned(claim)], release=release)
    return str(refused.value)


def test_d12s_cases_are_reported_with_their_line(synthetic_ctx):
    release = DatasetReleaseFactory(is_synthetic=True)
    rows = [
        {**SYNTHETIC, "record_id": "undeclared", "subject_namespace": "synthetic_cell"},
        {**SYNTHETIC, "record_id": "pattern", "object_id": "syn-g1"},
        {**SYNTHETIC, "record_id": "unknown-type", "relationship_type": "regulates"},
        {
            **SYNTHETIC,
            "record_id": "symmetric-effect",
            "subject_namespace": "synthetic_protein",
            "subject_id": "SYN-P1",
            "subject_type": "protein",
            "object_namespace": "synthetic_protein",
            "object_id": "SYN-P2",
            "object_type": "protein",
            "relationship_type": "physical_interaction",
            "effect": "activating",
        },
        {
            **SYNTHETIC,
            "record_id": "absent-effect",
            "outcome": "tested_absent",
            "effect": "inhibiting",
        },
        {**SYNTHETIC, "record_id": "direction", "directed": "false"},
        {**SYNTHETIC, "record_id": "negative", "effect": "negative"},
        {**SYNTHETIC, "record_id": "duplicate"},
        {**SYNTHETIC, "record_id": "duplicate", "effect": "activating"},
    ]
    results = mapped_rows(synthetic_ctx, *rows)
    claims = [result.claims[0] if result.claims else None for result in results]
    # An identifier in an undeclared namespace, or failing its pattern: the
    # shared mapping's identifier_problem reports what the adapter states.
    ref = claims[0].subject.entity
    assert identifier_problem(
        ref.entity_type, ref.taxon, ref.namespace, ref.value
    ).code == ("unknown_namespace")
    ref = claims[1].object.entity
    assert identifier_problem(
        ref.entity_type, ref.taxon, ref.namespace, ref.value
    ).code == ("bad_identifier")
    # An unknown type, and "negative" given as an effect: the adapter's own.
    assert ("unknown_relationship_type", "relationship_type", 4) in issues(results[2])
    assert ("negative_effect", "effect", 8) in issues(results[6])
    # An effect on a symmetric type, on a tested-absent row, and a direction
    # the type contradicts: G3's rules refuse the claims as stated.
    assert "Rule 7 (polarity)" in g3_refusal(claims[3], release)
    assert "Rule 8 (outcome)" in g3_refusal(claims[4], release)
    assert "Rule 3 (stated direction)" in g3_refusal(claims[5], release)
    # A duplicate record key with different content: two lines, one key, two
    # hashes, for the dry run to reject both.
    _, records = read(tsv(*rows), synthetic_ctx)
    first, second = records[-2:]
    assert (first.record_key, second.record_key) == ("duplicate", "duplicate")
    assert first.raw_sha256 != second.raw_sha256
    assert (first.line_number, second.line_number) == (9, 10)


def test_an_extremely_long_identifier_is_read_and_refused_by_its_namespace(ctx):
    claim = claim_of(mapped(ctx, subject_id="HGNC:" + "1" * 9_000))
    ref = claim.subject.entity
    problem = identifier_problem(ref.entity_type, ref.taxon, ref.namespace, ref.value)
    assert problem.code == "bad_identifier" and "at most 255" in problem
