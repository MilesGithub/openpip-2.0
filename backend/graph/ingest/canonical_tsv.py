"""canonical_tsv_v1: openPIP's own tab-separated format for typed evidence,
and the import route for independent labs (architecture §8). The operator
guide documents it (docs/operator-guide/typed-data-format.md).

Reading. A file is UTF-8 text with one record per line and a header line
that names the columns. Lines are read as bytes, at most MAX_LINE_BYTES + 2
at a time, and split on tabs: the csv module is not used, because its
quoting lets one record span lines and its field limit raises mid-file,
while here the line limit holds before any value is kept. A longer line is
read to its end in bounded chunks, hashed as it goes, and never held whole.
Lines end only at LF, CRLF accepted; a CR anywhere else, any other C0
control but TAB, DEL or a C1 control is a control character (U+2028 and
U+2029 are text). A single leading UTF-8 byte order mark is dropped. Blank
lines are skipped and counted. A row that cannot be read (too long, not
UTF-8, a control character, the wrong number of fields, a field over
MAX_FIELD_CHARS, a malformed record_id) is reported by its line number and
stored nowhere: it has no trustworthy record id or values.

Columns. Each row states one claim (graph.claims.Claim) about the
relationship between its subject and its object, as the source states it;
the shared mapping resolves the identifiers, and G3's rules check the claim.
The adapter checks what a row's own cells say: closed values, CURIEs and
their declaration in the release manifest, papers, scores, the threshold,
and, in a synthetic release, that nothing names real data. It never infers,
merges or corrects a value, and makes no query: it reads the run's registry
snapshot (graph.ingest.context).
"""

import codecs
import hashlib
import re

from graph.claims import (
    Claim,
    ContextValue,
    Endpoint,
    MethodRef,
    PublicationRef,
    ScoreValue,
)
from graph.identity import EMBEDDED_PREFIXES
from graph.models import (
    SCORE_TYPE_SLUG,
    SYNTHETIC_TAXON,
    AssertionMethod,
    ContextFacet,
    Entity,
    EvidenceAssertion,
    EvidenceBasis,
    EvidencePublicationRole,
    GeneticSign,
    InteractionLevel,
    MethodRole,
    Outcome,
    RecordKeyBasis,
    RelationshipFamily,
    ReleaseKind,
)
from graph.publications import (
    PublicationError,
    doi_lookup,
    normalize_doi,
    normalize_pmid,
)

# score_form is graph.scores' own reading of a raw value, which parse_score
# follows (spec §1.6); re-exported here for the adapter's tests.
from graph.scores import NUMBER, score_form
from graph.terms import (
    ONTOLOGIES,
    PHYSICAL_INTERACTION_LEVELS,
    TermError,
    normalize_curie,
)

from . import limits
from .manifest import LOCAL_KEY
from .protocol import (
    AdapterSpec,
    FileError,
    Issue,
    MappedRecord,
    PaperRef,
    RawRecord,
    EntityRef,
    note,
    quoted,
)

NAME = "canonical_tsv_v1"
CHANGES_NOTE = (
    "Identifiers are normalized by their namespace's rules and resolved to typed "
    "entities; an identifier may be mapped by a reviewed resolution file (basis "
    "curated); a row that cites several papers becomes one assertion per paper. "
    "No value is inferred, merged or corrected."
)
# The PSI-MI interaction types the format accepts, each at the level G3's
# crosswalk gives it (architecture D20): association (co-purification),
# physical association and direct interaction. A subset of
# graph.terms.PHYSICAL_INTERACTION_LEVELS, pinned by a test.
INTERACTION_LEVELS = {
    "MI:0914": InteractionLevel.ASSOCIATION.value,
    "MI:0915": InteractionLevel.PHYSICAL_ASSOCIATION.value,
    "MI:0407": InteractionLevel.DIRECT_INTERACTION.value,
}
COLUMNS = (
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
OPTIONAL_COLUMNS = (
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
SCORE, CONTEXT, QUALIFIER = "score:", "context:", "qualifier:"
COLUMN_PREFIXES = (SCORE, CONTEXT, QUALIFIER)

SPEC = AdapterSpec(
    name=NAME,
    version="1.0.0",
    mapping_version="1",
    kind=ReleaseKind.EVIDENCE.value,
    # Identifiers are taken as stated: an isoform stays an isoform.
    isoform_specific=True,
    changes_note=CHANGES_NOTE,
    record_key_basis=RecordKeyBasis.SOURCE_ID.value,
    columns=COLUMNS,
    optional_columns=OPTIONAL_COLUMNS,
    column_prefixes=COLUMN_PREFIXES,
    relationship_types=None,
    # encodes and isoform_of only in synthetic releases, until RF1a's
    # reference adapters: a link a lab states would become mapping authority
    # for B12 and X1.
    reference_types_only_if_synthetic=True,
    interaction_levels=INTERACTION_LEVELS,
    coreferent_publication_ids=False,
)

RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}")
TAXID = re.compile(r"[1-9][0-9]{0,9}", re.ASCII)
LARGEST_TAXID = 2**31 - 1
QUALIFIER_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Every control character but TAB: C0, DEL and C1.
CONTROL = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f]")
# One significance criterion: <score type> <operator> <value>, spaces
# optional; abs operators before the plain ones they start with.
CRITERION = re.compile(
    rf"\s*(?P<score_type>{SCORE_TYPE_SLUG.pattern})\s*"
    r"(?P<operator>abs<=|abs>=|abs<|abs>|<=|>=|<|>)\s*(?P<value>[^\s;]+)\s*",
    re.ASCII,
)
PAPER_KINDS = {"pmid": "pmid", "doi": "doi", "ref": "local"}
LABEL_LENGTH = Entity._meta.get_field("label").max_length
_UTF16_32_MARKS = (
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
)


class CanonicalTsvV1:
    """The adapter (graph.ingest.protocol.Adapter)."""

    spec = SPEC

    def iter_records(self, stream, ctx) -> "Records":
        """The file's records, from a binary stream. The header is read, and
        checked against ctx's registry snapshot, before the first record."""
        return Records(stream, ctx)

    def map_record(self, record, ctx) -> MappedRecord:
        """The row's claim and issues. A record that could not be read has
        no claim: its reading issues stand."""
        if record.fields is None or record.issues:
            return MappedRecord((), tuple(record.issues))
        return _Row(record, ctx).mapped()

    def synthetic_issues(self, record, ctx) -> tuple:
        """In a synthetic release, the synthetic violations of a row that is
        not stored but whose cells split into the header's columns (a field
        too long, a malformed record_id, a control character in a field, a
        later line of a record id): the checks map_record makes, and no
        other issue. A line too long, not UTF-8 or with another number of
        fields than the header's columns has no cells to line up with them,
        and is not checked: it is reported as unstored. Makes no query."""
        if not ctx.is_synthetic or record.fields is None:
            return ()
        return tuple(
            issue
            for issue in _Row(record, ctx).mapped().issues
            if issue.code == "synthetic_violation"
        )


ADAPTER = CanonicalTsvV1()


# ── Reading ─────────────────────────────────────────────────────────────────


class Records:
    """The records of one canonical_tsv_v1 file: an iterator of RawRecords,
    in line order, which keeps the header's columns once it has read them
    and counts the lines it read (lines, the header included, and
    blank_lines). Raises FileError at the header, and past
    limits.MAX_DATA_LINES."""

    def __init__(self, stream, ctx):
        self.columns = None
        self.lines = 0
        self.blank_lines = 0
        self._records = self._read(stream, ctx)

    def __iter__(self):
        return self

    def __next__(self) -> RawRecord:
        return next(self._records)

    def _read(self, stream, ctx):
        lines = _physical_lines(stream)
        first = next(lines, None)
        if first is None:
            raise FileError(
                [
                    Issue(
                        "no_header",
                        "The file is empty: its first line is the header, which "
                        "names the columns.",
                        line=1,
                    )
                ]
            )
        self.lines = 1
        self.columns = _header(first[1], ctx)
        self._record_id = self.columns.index("record_id")
        for number, content, digest, size in lines:
            self.lines = number
            if number - 1 > limits.MAX_DATA_LINES:
                raise FileError(
                    [
                        Issue(
                            "too_many_lines",
                            f"The file has more than {limits.MAX_DATA_LINES} lines "
                            "after its header.",
                            line=number,
                        )
                    ]
                )
            if content is not None and not content.strip(b" \t"):
                self.blank_lines += 1
                continue
            yield self._record(number, content, digest, size)

    def _record(self, number, content, digest, size) -> RawRecord:
        basis = SPEC.record_key_basis

        def unstored(*issues):
            return RawRecord(number, None, basis, None, digest, issues, size)

        if content is None:
            return unstored(
                Issue(
                    "line_too_long",
                    f"Line {number} is {size} bytes long without its line ending, "
                    f"more than {limits.MAX_LINE_BYTES}; it was not kept.",
                    line=number,
                )
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            return unstored(
                Issue(
                    "bad_utf8",
                    f"Line {number} is not UTF-8: byte {exc.start + 1} "
                    f"({content[exc.start]:#04x}) cannot be decoded.",
                    line=number,
                )
            )
        control = CONTROL.search(text)
        values = text.split("\t")
        if control:
            column = self._column_at(text, control.start())
            issue = Issue(
                "control_character",
                f"Line {number} holds {_control_name(control.group())}"
                + (f", in {column}" if column else "")
                + ": a field holds text, and lines end only at LF or CRLF.",
                field=column,
                line=number,
            )
            if len(values) != len(self.columns):
                return unstored(issue)
            # Unstored, but its cells split: a synthetic release checks them
            # (CanonicalTsvV1.synthetic_issues).
            cells = dict(zip(self.columns, values))
            return RawRecord(number, None, basis, cells, digest, (issue,), size)
        if len(values) != len(self.columns):
            return unstored(
                Issue(
                    "field_count",
                    f"Line {number} has {len(values)} tab-separated fields, and the "
                    f"header has {len(self.columns)} columns.",
                    line=number,
                )
            )
        issues = [
            Issue(
                "field_too_long",
                f"{column} on line {number} is {len(value)} characters, more than "
                f"{limits.MAX_FIELD_CHARS}.",
                field=column,
                line=number,
            )
            for column, value in zip(self.columns, values)
            if len(value) > limits.MAX_FIELD_CHARS
        ]
        record_id = values[self._record_id]
        if len(record_id) <= limits.MAX_FIELD_CHARS and not RECORD_ID.fullmatch(
            record_id
        ):
            issues.append(
                Issue(
                    "bad_record_id",
                    f"record_id {quoted(record_id)} on line {number} is not a letter "
                    "or digit followed by up to 254 letters, digits or . _ : -.",
                    field="record_id",
                    line=number,
                )
            )
        cells = dict(zip(self.columns, values))
        if issues:
            # Unstored, but its cells split: a synthetic release checks them
            # (CanonicalTsvV1.synthetic_issues).
            return RawRecord(number, None, basis, cells, digest, tuple(issues), size)
        return RawRecord(number, record_id, basis, cells, digest, (), size)

    def _column_at(self, text, position) -> str:
        index = text.count("\t", 0, position)
        return self.columns[index] if index < len(self.columns) else ""


def _physical_lines(stream):
    """(line number, content, SHA-256, size) for each physical line: its
    bytes without LF or CRLF, or None when there are more than
    limits.MAX_LINE_BYTES of them, which are then read to the line's end in
    bounded chunks and hashed as they go."""
    limit = limits.MAX_LINE_BYTES
    size = limit + 2
    number = 0
    while True:
        chunk = stream.readline(size)
        if not isinstance(chunk, bytes):
            raise TypeError(f"{NAME} reads a binary stream, not {stream!r}.")
        if not chunk:
            return
        number += 1
        # readline() stops short of `size` only at a line ending or the end.
        if chunk.endswith(b"\n") or len(chunk) < size:
            content = _without_ending(chunk)
            digest = hashlib.sha256(content).hexdigest()
            kept = content if len(content) <= limit else None
            yield number, kept, digest, len(content)
        else:
            digest, length = _read_long_line(stream, chunk, size)
            yield number, None, digest, length


def _without_ending(chunk) -> bytes:
    if chunk.endswith(b"\r\n"):
        return chunk[:-2]
    if chunk.endswith(b"\n"):
        return chunk[:-1]
    return chunk


def _read_long_line(stream, chunk, size):
    """(SHA-256, length) of a line longer than a chunk, read chunk by chunk
    to its LF. A CR that ends one chunk and the LF that starts the next form
    one CRLF, so a CR is held back until the next chunk says which it is."""
    digest, length, held = hashlib.sha256(), 0, b""
    while True:
        if chunk.endswith(b"\n"):
            body = chunk[:-1]
            if body:
                if body.endswith(b"\r"):
                    body = body[:-1]
                digest.update(held + body)
                length += len(held) + len(body)
            # An LF alone ends the line; a CR held before it was the CRLF's.
            return digest.hexdigest(), length
        if not chunk:
            digest.update(held)
            return digest.hexdigest(), length + len(held)
        digest.update(held)
        length += len(held)
        held = b""
        if chunk.endswith(b"\r"):
            held, chunk = b"\r", chunk[:-1]
        digest.update(chunk)
        length += len(chunk)
        chunk = stream.readline(size)


def _control_name(character) -> str:
    if character == "\r":
        return "a carriage return (U+000D) that does not end the line"
    if character == "\x00":
        return "a NUL character (U+0000)"
    return f"the control character U+{ord(character):04X}"


def _header(content, ctx) -> tuple:
    """The header's columns; FileError for anything but a header of this
    format, every problem listed."""

    def refused(code, message, column=""):
        return FileError([Issue(code, message, field=column, line=1)])

    if content is None:
        raise refused(
            "line_too_long",
            f"The header, line 1, is longer than {limits.MAX_LINE_BYTES} bytes.",
        )
    if content.startswith(_UTF16_32_MARKS):
        raise refused(
            "encoding",
            "The file starts with a UTF-16 or UTF-32 byte order mark: save it as "
            "UTF-8.",
        )
    if content.startswith(codecs.BOM_UTF8):
        content = content[len(codecs.BOM_UTF8) :]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise refused(
            "encoding",
            f"The header is not UTF-8 (byte {exc.start + 1} cannot be decoded): "
            "save the file as UTF-8.",
        ) from None
    if "\x00" in text:
        raise refused(
            "encoding",
            "The header holds NUL characters, as UTF-16 text does: save the file "
            "as UTF-8.",
        )
    control = CONTROL.search(text)
    if control:
        raise refused(
            "control_character",
            f"The header holds {_control_name(control.group())}: lines end only at "
            "LF or CRLF.",
        )
    if not text.strip(" \t"):
        raise refused(
            "no_header",
            "The first line is blank: it is the header, which names the columns.",
        )
    columns = tuple(text.split("\t"))
    if len(columns) > limits.MAX_COLUMNS:
        raise refused(
            "too_many_columns",
            f"The header has {len(columns)} columns, more than {limits.MAX_COLUMNS}.",
        )
    issues = []

    def problem(code, column, message):
        issues.append(Issue(code, message, field=column, line=1))

    known = set(COLUMNS) | set(OPTIONAL_COLUMNS)
    seen = set()
    for column in columns:
        if column in seen:
            problem(
                "header_duplicate_column",
                column,
                f"The header names {quoted(column)} twice.",
            )
            continue
        seen.add(column)
        if column in known:
            continue
        if column.startswith(SCORE):
            slug = column[len(SCORE) :]
            if slug not in ctx.registry.score_types:
                problem(
                    "unknown_score_type",
                    column,
                    f"Column {quoted(column)} names {quoted(slug)}, which is not a "
                    "score type in the registry.",
                )
        elif column.startswith(CONTEXT):
            facet = column[len(CONTEXT) :]
            if facet not in ContextFacet.values:
                problem(
                    "unknown_facet",
                    column,
                    f"Column {quoted(column)} names {quoted(facet)}, which is not a "
                    f"context facet: one of {', '.join(ContextFacet.values)}.",
                )
        elif column.startswith(QUALIFIER):
            key = column[len(QUALIFIER) :]
            if not QUALIFIER_KEY.fullmatch(key) or key == "threshold":
                problem(
                    "header_unknown_column",
                    column,
                    f"Column {quoted(column)}: a qualifier key is a lower-case "
                    "letter, then up to 63 lower-case letters, digits or _, and "
                    "never threshold, which the threshold column fills.",
                )
        else:
            problem(
                "header_unknown_column",
                column,
                f"Column {quoted(column)} is not a column of {NAME}.",
            )
    for column in COLUMNS:
        if column not in seen:
            problem(
                "header_missing_column",
                column,
                f"The header has no {column} column, which {NAME} requires.",
            )
    if issues:
        raise FileError(issues)
    return columns


# ── Mapping a row ───────────────────────────────────────────────────────────


class _Row:
    """One readable row, mapped: every cell checked, every issue kept."""

    def __init__(self, record, ctx):
        self.fields = record.fields
        self.line = record.line_number
        self.ctx = ctx
        self.registry = ctx.registry
        self.synthetic = (
            ctx.registry.synthetic_namespaces if ctx.is_synthetic else frozenset()
        )
        self.issues = []

    def error(self, code, column, message):
        self.issues.append(Issue(code, message, field=column, line=self.line))

    def note(self, code, column, message):
        self.issues.append(note(code, message, field=column, line=self.line))

    def violation(self, column, message):
        self.error("synthetic_violation", column, message)

    def cell(self, column) -> str:
        """The cell as written; "" for an optional column the file lacks."""
        return self.fields.get(column, "")

    def given(self, column) -> str | None:
        """The cell, or None when it is blank: not stated."""
        value = self.cell(column)
        return value if value.strip() else None

    def columns(self, prefix):
        """(column, name after the prefix, cell) for each non-blank cell of a
        prefixed column, in the header's order."""
        for column, value in self.fields.items():
            if column.startswith(prefix) and value.strip():
                yield column, column[len(prefix) :], value

    def mapped(self) -> MappedRecord:
        rel_type = self.relationship_type()
        subject = self.endpoint("subject")
        obj = self.endpoint("object")
        outcome = self.closed("outcome", Outcome)
        evidence_basis = self.closed("evidence_basis", EvidenceBasis)
        assertion_method = self.closed("assertion_method", AssertionMethod)
        directed = self.directed()
        effect = self.effect()
        genetic_sign = self.genetic_sign()
        interaction_type, level = self.interaction(rel_type)
        methods = self.methods()
        eco = self.eco()
        publications = self.publications()
        scores = self.scores()
        contexts = self.contexts()
        qualifiers = self.qualifiers(scores)
        claim = Claim(
            relationship_type=self.cell("relationship_type"),
            subject=subject,
            object=obj,
            outcome=outcome,
            evidence_basis=evidence_basis,
            assertion_method=assertion_method,
            effect=effect,
            genetic_sign=genetic_sign,
            interaction_type=interaction_type,
            interaction_level=level,
            eco_term=eco,
            evidence_group=self.given("evidence_group") or "",
            stated_directed=directed,
            qualifiers=qualifiers,
            methods=methods,
            publications=publications,
            scores=tuple(scores.values()),
            contexts=contexts,
        )
        return MappedRecord((claim,), tuple(self.issues))

    # The relationship and its endpoints.

    def relationship_type(self):
        slug = self.cell("relationship_type")
        rules = self.registry.relationship_types.get(slug)
        accepted = SPEC.relationship_types
        if rules is None or (accepted is not None and slug not in accepted):
            self.error(
                "unknown_relationship_type",
                "relationship_type",
                f"relationship_type {quoted(slug)} is not a relationship type in the "
                "registry.",
            )
            return None
        if (
            rules.is_reference
            and SPEC.reference_types_only_if_synthetic
            and not self.ctx.is_synthetic
        ):
            self.error(
                "reference_type_not_allowed",
                "relationship_type",
                f"{slug} is a reference link, which only a synthetic release states "
                "in this format: reference links come from reference releases "
                "(RF1a), because later imports map through them.",
            )
        return rules

    def endpoint(self, side) -> Endpoint:
        namespace = self.cell(f"{side}_namespace")
        value = self.cell(f"{side}_id")
        entity_type = self.cell(f"{side}_type")
        taxon = self.taxid(f"{side}_taxon")
        label = self.cell(f"{side}_label")
        prefix = EMBEDDED_PREFIXES.get(namespace)
        if prefix and value.strip() and value.strip()[: len(prefix)].upper() != prefix:
            self.error(
                "hgnc_without_prefix",
                f"{side}_id",
                f"{side}_id {quoted(value)} is a {namespace} value without {prefix}: "
                f"write it as {prefix}<n>. A bare number could equally be an NCBI "
                "Gene ID, which the ncbigene namespace holds.",
            )
        if len(label.strip()) > LABEL_LENGTH:
            self.error(
                "label_too_long",
                f"{side}_label",
                f"{side}_label is {len(label.strip())} characters, more than the "
                f"{LABEL_LENGTH} an entity's label may be.",
            )
        if self.ctx.is_synthetic:
            self.synthetic_endpoint(side, namespace, taxon, label)
        return Endpoint(
            EntityRef(namespace, value, entity_type, taxon, label), value, ""
        )

    def synthetic_endpoint(self, side, namespace, taxon, label):
        if namespace not in self.synthetic:
            self.violation(
                f"{side}_namespace",
                f"{side}_namespace {quoted(namespace)} is not a synthetic namespace "
                f"({', '.join(sorted(self.synthetic)) or 'none is registered'}): a "
                "synthetic release names synthetic constructs only.",
            )
        if taxon != SYNTHETIC_TAXON:
            self.violation(
                f"{side}_taxon",
                f"{side}_taxon {quoted(self.cell(f'{side}_taxon'))} is not "
                f"{SYNTHETIC_TAXON}, the synthetic construct: a synthetic release "
                "names nothing else.",
            )
        stated = label.strip()
        if stated and not stated.startswith("SYN-"):
            self.violation(
                f"{side}_label",
                f"{side}_label {quoted(stated)} does not start with SYN-: a synthetic "
                "release's labels never read as a real gene's or protein's (D12).",
            )

    def taxid(self, column) -> int | None:
        """The taxid a cell states; None when blank, or when it is not one
        (reported)."""
        written = self.cell(column)
        if not written.strip():
            return None
        if not TAXID.fullmatch(written) or int(written) > LARGEST_TAXID:
            self.error(
                "bad_taxon",
                column,
                f"{column} {quoted(written)} is not an NCBI taxid: digits from 1 to "
                f"{LARGEST_TAXID}, without a sign, spaces or leading zeros.",
            )
            return None
        return int(written)

    # What the assertion states.

    def closed(self, column, choices) -> str:
        value = self.cell(column)
        if value not in choices.values:
            self.error(
                "bad_value",
                column,
                f"{column} is one of {', '.join(choices.values)}; not {quoted(value)}.",
            )
        return value

    def directed(self) -> bool | None:
        value = self.given("directed")
        if value is None:
            return None
        if value not in ("true", "false"):
            self.error(
                "bad_value",
                "directed",
                f"directed is true, false or blank; not {quoted(value)}.",
            )
            return None
        return value == "true"

    def effect(self) -> str | None:
        value = self.given("effect")
        if value is None:
            return None
        if value.strip().lower() == "negative":
            self.error(
                "negative_effect",
                "effect",
                "effect 'negative' is ambiguous (inhibiting? a negative genetic "
                "interaction? tested and not found?), so it is refused, never "
                "coerced: state inhibiting, a genetic_sign, or the outcome "
                "tested_absent.",
            )
            return None
        if value not in EvidenceAssertion.STORED_EFFECTS:
            self.error(
                "bad_value",
                "effect",
                "effect is activating, inhibiting, unknown, or blank when the source "
                f"states none; not {quoted(value)}.",
            )
            return None
        return value

    def genetic_sign(self) -> str | None:
        value = self.given("genetic_sign")
        if value is None:
            return None
        if value not in GeneticSign.values:
            self.error(
                "bad_value",
                "genetic_sign",
                f"genetic_sign is negative, positive or blank; not {quoted(value)}.",
            )
            return None
        return value

    def interaction(self, rel_type):
        """(interaction type, level). The level is the format's list's for the
        type stated; without one, not_stated on a physical-family row and
        none on any other (there is no level column)."""
        written = self.given("interaction_type")
        if written is None:
            physical = rel_type is not None and (
                rel_type.family == RelationshipFamily.PHYSICAL
            )
            return None, InteractionLevel.NOT_STATED.value if physical else None
        curie = self.curie("interaction_type", written)
        if curie is None:
            return None, None
        self.declared("interaction_type", curie)
        level = SPEC.interaction_levels.get(curie)
        if level is None:
            listed = ", ".join(
                f"{key} ({value})" for key, value in SPEC.interaction_levels.items()
            )
            self.error(
                "interaction_type_not_listed",
                "interaction_type",
                f"interaction_type {curie} is not one {NAME} accepts: {listed}.",
            )
        return curie, level

    def curie(self, column, written) -> str | None:
        """The CURIE in canonical form; None when it is not one (reported)."""
        try:
            return normalize_curie(written)
        except TermError:
            pass
        prefix = written.strip().partition(":")[0]
        forms = {key.upper(): (key, form) for key, (_, form) in ONTOLOGIES.items()}
        if prefix.isascii() and prefix.upper() in forms:
            key, form = forms[prefix.upper()]
            reason = f"a {key} CURIE's local id is {form.pattern} in ASCII digits"
        else:
            reason = f"openPIP accepts the ontologies {', '.join(ONTOLOGIES)}"
        self.error(
            "bad_curie", column, f"{column} {quoted(written)} is not a CURIE: {reason}."
        )
        return None

    def declared(self, column, curie):
        if curie not in self.ctx.manifest.terms:
            self.error(
                "term_undeclared",
                column,
                f"{column} {curie} is not declared in the release manifest's terms "
                "table, with the label the source gives it.",
            )

    def methods(self) -> tuple:
        written = self.given("method")
        if written is None:
            return ()
        items = written.split("|")
        if len(items) > limits.MAX_LIST_VALUES:
            self.error(
                "too_many_values",
                "method",
                f"method lists {len(items)} methods, more than "
                f"{limits.MAX_LIST_VALUES}.",
            )
            return ()
        methods = []
        for item in items:
            curie = self.curie("method", item.strip())
            if curie is None:
                continue
            if curie in PHYSICAL_INTERACTION_LEVELS:
                self.error(
                    "method_is_interaction_type",
                    "method",
                    f"method {curie} ({PHYSICAL_INTERACTION_LEVELS[curie][1]}) is a "
                    "PSI-MI interaction type, not a detection method: it belongs in "
                    "interaction_type.",
                )
                continue
            self.declared("method", curie)
            methods.append(MethodRef(curie, MethodRole.DETECTION.value))
        return tuple(methods)

    def eco(self) -> str | None:
        written = self.given("eco")
        if written is None:
            return None
        curie = self.curie("eco", written)
        if curie is not None:
            self.declared("eco", curie)
        return curie

    def publications(self) -> tuple:
        written = self.given("publications")
        if written is None:
            return ()
        items = written.split("|")
        if self.ctx.is_synthetic:
            # Every item, however many: the check refuses the release. Any
            # item but a ref: one, as paper() reads it, so a PMID or DOI is
            # found however it is written, in a form this format refuses
            # (bad_paper) too. A blank item cites nothing.
            cited = [
                item.strip()
                for item in items
                if item.strip() and not item.strip().startswith("ref:")
            ]
            if cited:
                others = len(cited) - 1
                self.violation(
                    "publications",
                    f"publications item {quoted(cited[0])} is not ref:<key>"
                    + (f", nor are {others} others" if others else "")
                    + ": a synthetic release cites only the synthetic papers of "
                    "its manifest (ref:<key>), since any made-up PMID or DOI is "
                    "someone's paper.",
                )
        if len(items) > limits.MAX_LIST_VALUES:
            self.error(
                "too_many_values",
                "publications",
                f"publications lists {len(items)} papers, more than "
                f"{limits.MAX_LIST_VALUES}.",
            )
            return ()
        papers, seen = [], {}
        for n, item in enumerate(items, 1):
            paper = self.paper(item.strip())
            if paper is None:
                continue
            kind, value = paper
            identity = (kind, doi_lookup(value) if kind == "doi" else value)
            if identity in seen:
                self.error(
                    "duplicate_paper",
                    "publications",
                    f"publications items {seen[identity]} and {n} name one paper, "
                    f"{kind} {quoted(value)}: cite each paper once.",
                )
                continue
            seen[identity] = n
            papers.append(PaperRef({kind: value}))
        kinds = {kind for kind, _ in seen}
        if {"pmid", "doi"} <= kinds:
            self.note(
                "pmid_and_doi",
                "publications",
                "The row cites a pmid: paper and a doi: paper. If they are one paper, "
                "cite it once: each item is one paper, and gives one assertion.",
            )
        return tuple(
            PublicationRef(
                None, EvidencePublicationRole.REPORTED_IN.value, paper.stated
            )
            for paper in papers
        )

    def paper(self, item):
        """(kind, value) of one cited paper, normalized as graph.publications
        stores it; None when it is not one (reported)."""
        prefix, colon, value = item.partition(":")
        if not colon or prefix not in PAPER_KINDS:
            self.error(
                "bad_paper",
                "publications",
                f"publications item {quoted(item)} is not pmid:<n>, doi:<doi> or "
                "ref:<key>.",
            )
            return None
        if prefix == "ref":
            if not LOCAL_KEY.fullmatch(value) or (
                value not in self.ctx.manifest.publications
            ):
                self.error(
                    "unknown_ref",
                    "publications",
                    f"publications item {quoted(item)} names no paper of the release "
                    "manifest's publications table.",
                )
                return None
            return "local", value
        normalize, what = (
            (normalize_pmid, "a PMID, a number from 1")
            if prefix == "pmid"
            else (normalize_doi, "a DOI, 10.<registrant>/<suffix>")
        )
        try:
            return PAPER_KINDS[prefix], normalize(value)
        except PublicationError:
            self.error(
                "bad_paper",
                "publications",
                f"publications item {quoted(item)} is not {what}.",
            )
            return None

    def scores(self) -> dict:
        """{score type: ScoreValue} of the row's scores, each kept verbatim."""
        scores = {}
        for column, slug, raw in self.columns(SCORE):
            form = score_form(raw)
            if form == "not_finite":
                self.error(
                    "score_not_finite",
                    column,
                    f"{column} {quoted(raw)} is not a finite number.",
                )
                continue
            if form == "text":
                self.error(
                    "score_not_a_number",
                    column,
                    f"{column} {quoted(raw)} is not a number, nor a censored value "
                    "such as <2.2e-16.",
                )
                continue
            if form == "censored":
                self.note(
                    "censored_score",
                    column,
                    f"{column} {quoted(raw)} is censored (a bound, not a value): its "
                    "raw value is kept, with no value.",
                )
            scores[slug] = ScoreValue(slug, raw)
        return scores

    def contexts(self) -> tuple:
        contexts = []
        for column, facet, raw in self.columns(CONTEXT):
            if facet == ContextFacet.HOST_TAXON:
                contexts.append(
                    ContextValue(facet, raw, taxon=self.host_taxon(column, raw))
                )
            else:
                contexts.append(
                    ContextValue(facet, raw, term=self.context_term(column, raw))
                )
        return tuple(contexts)

    def host_taxon(self, column, raw) -> int | None:
        taxon = self.taxid(column)
        if (
            self.ctx.is_synthetic
            and taxon is not None
            and taxon in self.registry.taxa
            and taxon != SYNTHETIC_TAXON
        ):
            self.violation(
                column,
                f"{column} {quoted(raw)} is a registered taxon other than "
                f"{SYNTHETIC_TAXON}: a synthetic release's host taxon is the "
                "synthetic construct, or unregistered.",
            )
        return taxon

    def context_term(self, column, raw) -> str | None:
        """The term a context names: a CURIE of an ontology openPIP accepts,
        declared in the manifest. Other text stays raw text only."""
        prefix, colon, _ = raw.strip().partition(":")
        known = {key.upper() for key in ONTOLOGIES}
        if not colon or not prefix.isascii() or prefix.upper() not in known:
            return None
        curie = self.curie(column, raw)
        if curie is not None:
            self.declared(column, curie)
        return curie

    def qualifiers(self, scores) -> dict:
        qualifiers = {key: value for _, key, value in self.columns(QUALIFIER)}
        threshold = self.threshold(scores)
        if threshold is not None:
            qualifiers["threshold"] = threshold
        return qualifiers

    def threshold(self, scores) -> list | None:
        """The significance criteria a not_significant row states, as G3
        reads them: every one must hold for significance, so the row fails
        one (rule 8). Each names a score on the row; the value is kept as
        written."""
        written = self.given("threshold")
        if written is None:
            return None
        criteria = []
        for part in written.split(";"):
            match = CRITERION.fullmatch(part)
            if match is None or not NUMBER.fullmatch(match["value"]):
                self.error(
                    "bad_threshold",
                    "threshold",
                    f"threshold criterion {quoted(part.strip())} is not <score type> "
                    "<operator> <number>, the operator one of <, <=, >, >=, abs<, "
                    "abs<=, abs>, abs>=, criteria joined by ;.",
                )
                return None
            slug = match["score_type"]
            if slug not in scores:
                self.error(
                    "bad_threshold",
                    "threshold",
                    f"threshold criterion {quoted(part.strip())} names {slug}, and "
                    f"the row has no score:{slug} score.",
                )
                return None
            criteria.append(
                {
                    "score_type": slug,
                    "operator": match["operator"],
                    "value": match["value"],
                }
            )
        return criteria
