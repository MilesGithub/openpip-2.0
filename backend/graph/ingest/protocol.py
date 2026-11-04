"""The adapter protocol: what an adapter declares, what it reads from a file,
and what it states about each record (architecture §8).

An adapter (Adapter) has a spec (AdapterSpec) and three methods:

- iter_records(stream, ctx) streams the file's records as RawRecords, with
  their physical line numbers. It may raise FileError at any point: at the
  header, before any record; later, the records read so far stand and the
  run is refused (G4's dry run; G5 refuses to commit such a file). A record
  with reading issues is unstored: it has no trustworthy key or values.
- map_record(record, ctx) states what one record says as G3's claims
  (graph.claims), with the issues found. An endpoint's entity is an
  EntityRef, the identifier as stated, until the shared mapping resolves it;
  a paper is a PublicationRef(None, role, stated) whose stated identifiers
  are a PaperRef's.
- synthetic_issues(record, ctx) checks, in a synthetic release, a record
  that is not stored but whose cells were read (a field too long, a
  malformed record id, a control character in a field, a later line of a
  record id), so that real data is found on every row it can be read from.
  A line too long, not UTF-8 or with another number of fields than the
  header has no cells that line up with the header's columns: it is not
  checked, and is reported as unstored.

Adapters are pure: they read the stream and ctx (graph.ingest.context: an
in-memory snapshot of the registry, the release's synthetic flag and its
manifest) and nothing else, write nothing, and make no query and no network
call.

Issues are coded (ISSUE_CODES, pinned by a test and listed in the operator
guide). A message is a sentence, and quotes a file value with quoted(): its
repr(), truncated, so NUL, surrogates and control characters show as
escapes.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from graph.claims import RULES, Claim

from . import limits

ERROR = "error"
NOTE = "note"
SEVERITIES = (ERROR, NOTE)

# Every code an issue may carry, and what it means. The operator guide lists
# them (docs/operator-guide/typed-data-format.md); a test pins the set.
ISSUE_CODES = {
    # Reading a line. Such a row has no trustworthy record id or values, so
    # no source record is stored for it; the report lists it by line.
    "line_too_long": (
        "The line is longer than MAX_LINE_BYTES bytes without its LF or CRLF. "
        "It was read to its end in bounded chunks and not kept."
    ),
    "bad_utf8": "The line is not valid UTF-8.",
    "control_character": (
        "The line holds a control character: a CR that does not end it, another "
        "C0 control except TAB, DEL, or a C1 control."
    ),
    "field_count": (
        "The line does not have as many tab-separated fields as the header has "
        "columns."
    ),
    "field_too_long": "A field is longer than MAX_FIELD_CHARS characters.",
    "bad_record_id": (
        "The record_id is not a letter or digit followed by up to 254 letters, "
        "digits or . _ : -."
    ),
    "duplicate_record": (
        "The record_id was stored from an earlier line with the same content; "
        "this line is not stored again."
    ),
    "conflicting_duplicate": (
        "The record_id was stored from an earlier line with other content: "
        "neither version is imported."
    ),
    # A row's values. The row is stored, as a rejected record.
    "too_many_values": "A cell lists more than MAX_LIST_VALUES papers or methods.",
    "unknown_namespace": "The identifier namespace is not in the registry.",
    "namespace_not_for_type": (
        "The namespace does not identify entities of the stated type."
    ),
    "bad_identifier": (
        "The value is not an identifier of its namespace: it fails the "
        "namespace's normalization or pattern."
    ),
    "hgnc_without_prefix": (
        "An hgnc value is written without HGNC:. A bare number could equally be "
        "an NCBI Gene ID."
    ),
    "bad_taxon": (
        "The taxon is not an NCBI taxid (1 to 2147483647, written in digits "
        "without a sign or leading zeros), or it is missing where the entity "
        "type needs one, or given where it has none."
    ),
    "unknown_taxon": "The taxid is not in the registry.",
    "taxon_contradiction": (
        "The stated taxon contradicts the namespace's fixed taxon, or the taxon "
        "of the entity that holds the value."
    ),
    "isoform_form": (
        "The value's form names the other type: an isoform accession for a "
        "protein entry, or an entry accession for an isoform."
    ),
    "unknown_entity_type": "The entity type is not in the registry.",
    "unknown_relationship_type": (
        "The relationship type is not in the registry, or not one the adapter "
        "accepts."
    ),
    "reference_type_not_allowed": (
        "A reference relationship type (encodes, isoform_of) in a release that "
        "is not synthetic: reference links come from reference adapters (RF1a)."
    ),
    "unknown_score_type": (
        "A score: column names a score type that is not in the registry."
    ),
    "unknown_facet": "A context: column names a facet that does not exist.",
    "bad_value": "A column with a closed set of values holds another value.",
    "negative_effect": (
        "The effect is 'negative', which is ambiguous (inhibiting? a negative "
        "genetic interaction? tested and not found?): refused, never coerced."
    ),
    "interaction_type_not_listed": (
        "The interaction type is not in the adapter's list of accepted PSI-MI "
        "interaction types."
    ),
    "method_is_interaction_type": (
        "The method column names a PSI-MI interaction type, which is not a "
        "detection method."
    ),
    "term_undeclared": (
        "A CURIE the row uses is not declared, with the source's label, in the "
        "release manifest's terms table."
    ),
    "bad_curie": "A value is not a CURIE of an ontology openPIP accepts.",
    "score_not_finite": "A score is NaN or infinity, in any spelling.",
    "score_not_a_number": (
        "A score is text that is neither a number nor a censored value."
    ),
    "bad_threshold": (
        "The threshold does not follow its grammar, or one of its criteria "
        "names a score type with no score on the row."
    ),
    "unknown_ref": (
        "A ref: key is not declared in the release manifest's publications " "table."
    ),
    "bad_paper": "A cited paper is not pmid:<n>, doi:<doi> or ref:<key>.",
    "duplicate_paper": "Two items of the publications cell name one paper.",
    "label_too_long": "A label is longer than an entity's label may be.",
    "label_blank": "An entity needs a label, and none was given.",
    "synthetic_violation": (
        "A synthetic release names a namespace that is not synthetic, a taxon "
        "other than 32630, a label without SYN-, a paper other than its "
        "manifest's (ref:<key>), such as a PMID or DOI, a registered host taxon "
        "other than 32630, or a resolution target of another taxon. The release "
        "cannot be committed as synthetic."
    ),
    "resolution_contradicts": (
        "The identifier resolves to one entity, and the resolution file maps it "
        "to another."
    ),
    "not_identity_namespace": (
        "Entities of this type and taxon are not created from this namespace: "
        "another namespace defines their identity, or none does."
    ),
    "identity_conflict": (
        "The identifier already names, or is held by, another entity, so it "
        "defines no new one."
    ),
    "entity_exists": (
        "An entity with this identity key exists: it is found, not created."
    ),
    "release_cannot_introduce": (
        "The release introduces no entities: the switch is off, it is past "
        "staged, it has no effective internal approval, or it is synthetic and "
        "the entity is not a synthetic construct."
    ),
    **{
        f"claim_rule_{n}": f"G3's rule {n} ({name}) refuses the claim, in G3's words."
        for n, name in RULES.items()
    },
    "claim_conflict": (
        "The record holds evidence that its claims no longer give exactly "
        "(G3's ClaimConflict, in G3's words): a record's evidence never changes."
    ),
    # Notes, which never reject a record.
    "censored_score": (
        "A score is censored (a bound, such as <2.2e-16): its raw value is kept, "
        "with no value."
    ),
    "curated_mapping": (
        "The identifier was mapped by the reviewed resolution file (basis " "curated)."
    ),
    "candidate": (
        "The identifier matched candidates that nothing accepted; they are "
        "listed for review."
    ),
    "unmatched": (
        "The identifier matches no entity: a commit with --create-entities would "
        "create one, or none is created, for the reason given."
    ),
    "label_differs": (
        "The stated label differs from the existing entity's, which never " "changes."
    ),
    "pmid_and_doi": (
        "The row cites a pmid: paper and a doi: paper: check that they are not "
        "one paper, which is cited once."
    ),
    # File errors: the run is refused. At the header, no record is written.
    "encoding": (
        "The file is not UTF-8: it starts with a UTF-16 or UTF-32 byte order "
        "mark, or its header does not decode. Save it as UTF-8."
    ),
    "no_header": "The file has no header line.",
    "header_unknown_column": (
        "A header column is not one of the format's columns, or a qualifier: "
        "key is not [a-z][a-z0-9_]{0,63}, or is threshold."
    ),
    "header_missing_column": "A required column is missing from the header.",
    "header_duplicate_column": "A column appears twice in the header.",
    "too_many_columns": "The header has more than MAX_COLUMNS columns.",
    "too_many_lines": "The file has more than MAX_DATA_LINES lines after its header.",
    "manifest_invalid": "The release manifest is not valid.",
    "stored_file_mismatch": (
        "A stored release file is missing, or its size or SHA-256 differs from "
        "its record."
    ),
    # A source record's issues stop after limits.RECORD_ISSUES.
    "more": "The record has further issues, counted and not listed.",
}

# Reading issues: a record that has one is unstored (RawRecord.issues).
UNSTORED_CODES = frozenset(
    {
        "line_too_long",
        "bad_utf8",
        "control_character",
        "field_count",
        "field_too_long",
        "bad_record_id",
        "duplicate_record",
        "conflicting_duplicate",
    }
)
NOTE_CODES = frozenset(
    {
        "censored_score",
        "curated_mapping",
        "candidate",
        "unmatched",
        "label_differs",
        "pmid_and_doi",
    }
)
# Codes that refuse a whole file. line_too_long and control_character refuse
# it too when the header has them.
FILE_ERROR_CODES = frozenset(
    {
        "encoding",
        "no_header",
        "header_unknown_column",
        "header_missing_column",
        "header_duplicate_column",
        "too_many_columns",
        "too_many_lines",
        "unknown_score_type",
        "unknown_facet",
        "manifest_invalid",
        "stored_file_mismatch",
    }
)


def quoted(value) -> str:
    """A file value as a message quotes it: its repr(), at most
    limits.QUOTED_VALUE_CHARS characters, so NUL, lone surrogates and control
    characters show as escapes and a long value stays short."""
    text = repr(value)
    limit = limits.QUOTED_VALUE_CHARS
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True)
class Issue:
    """One problem found in a file, a record or its mapping."""

    # From ISSUE_CODES.
    code: str
    # A sentence; file values quoted with quoted().
    message: str
    # The column, when one is at fault.
    field: str = ""
    # The physical line, 1-based, the header included.
    line: int | None = None
    # ERROR rejects the record; NOTE does not.
    severity: str = ERROR

    def __post_init__(self):
        if self.code not in ISSUE_CODES:
            raise ValueError(f"{self.code!r} is not an issue code (ISSUE_CODES).")
        if self.severity not in SEVERITIES:
            raise ValueError(f"An issue's severity is error or note, not {self!r}.")
        if (self.code in NOTE_CODES) != (self.severity == NOTE):
            raise ValueError(
                f"{self.code} is {'a note' if self.code in NOTE_CODES else 'an error'}"
                f", not {self.severity!r}."
            )

    def as_json(self) -> dict:
        """The issue as SourceRecord.issues and the report store it."""
        return {
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "line": self.line,
            "severity": self.severity,
        }


def note(code, message, *, field="", line=None) -> Issue:
    return Issue(code, message, field=field, line=line, severity=NOTE)


def issues_json(issues) -> list:
    """A source record's issues as stored: at most limits.RECORD_ISSUES, then
    {"code": "more", "count": n} for the n left out.

    Its conflicting_duplicate issues are always among those kept (up to
    limits.RECORD_ISSUES of them), in their place: the other issues fill the
    room left, in order. A later run finds the conflict on the stored record
    (mapping._carried) and so never writes it again; mapping._with_issue
    keeps the same layout when a later batch adds one."""
    issues = list(issues)
    kept = limits.RECORD_ISSUES
    if len(issues) <= kept:
        return [issue.as_json() for issue in issues]
    conflicts = {
        index
        for index, issue in enumerate(issues)
        if issue.code == "conflicting_duplicate"
    }
    conflicts = set(sorted(conflicts)[:kept])
    room = kept - len(conflicts)
    stored = []
    for index, issue in enumerate(issues):
        if index in conflicts:
            stored.append(issue.as_json())
        elif room > 0:
            stored.append(issue.as_json())
            room -= 1
    stored.append({"code": "more", "count": len(issues) - len(stored)})
    return stored


def rejects(issues) -> bool:
    """Whether any of these issues rejects its record."""
    return any(issue.severity == ERROR for issue in issues)


def duplicate_issues(record, first_line, first_sha256):
    """(this line's issue, the first occurrence's issue or None) for a line
    whose record key the run already stored from an earlier line.

    The later line is unstored either way. With the same content (the same
    raw_sha256) it is a duplicate_record, and the first occurrence stays as
    it is. With other content it is a conflicting_duplicate, and the first
    occurrence's record gets an issue naming both lines, which rejects it:
    the file states two versions of one record, and neither is imported
    (D12). The dry run keeps no set of the keys it saw: the stored records
    are that set, read once per batch."""
    key, line = quoted(record.record_key), record.line_number
    if record.raw_sha256 == first_sha256:
        later = Issue(
            "duplicate_record",
            f"Line {line} repeats record {key} of line {first_line} exactly; it is "
            "not stored again.",
            field="record_id",
            line=line,
        )
        return later, None
    later = Issue(
        "conflicting_duplicate",
        f"Line {line} states record {key} again, with other content than line "
        f"{first_line}: neither version is imported.",
        field="record_id",
        line=line,
    )
    first = Issue(
        "conflicting_duplicate",
        f"Record {key} of line {first_line} is stated again, with other content, "
        f"on line {line}: neither version is imported.",
        field="record_id",
        line=first_line,
    )
    return later, first


class FileError(Exception):
    """The file is refused as a whole. iter_records raises it at any point."""

    def __init__(self, issues):
        self.issues = tuple(issues)
        if not self.issues or not all(isinstance(i, Issue) for i in self.issues):
            raise TypeError("A FileError carries one or more Issues.")
        super().__init__(" ".join(issue.message for issue in self.issues))


@dataclass(frozen=True)
class AdapterSpec:
    """What an adapter declares (architecture §8). registry.validate_spec
    checks every adapter's spec."""

    # "canonical_tsv_v1"; what a release records as its adapter.
    name: str
    # A semantic version, recorded with the release and its runs.
    version: str
    mapping_version: str
    # A ReleaseKind value: whether the entities a commit creates are active.
    kind: str
    # Whether the source's analysis is isoform-specific (architecture §4.1,
    # open question B12). False arrives with R2.
    isoform_specific: bool
    # The changes the adapter makes to the source's data, shown with each
    # release's attribution (CC BY and CC BY-SA require it).
    changes_note: str
    # A RecordKeyBasis value.
    record_key_basis: str
    # The header's required and optional columns, and the prefixes of the
    # columns named by a registry row ("score:<score type>").
    columns: tuple[str, ...]
    optional_columns: tuple[str, ...]
    column_prefixes: tuple[str, ...]
    # The relationship types it may state; None: every one in the registry.
    relationship_types: frozenset[str] | None
    # Reference types (is_reference) only in synthetic releases.
    reference_types_only_if_synthetic: bool
    # The PSI-MI interaction types it accepts, each with its level: a subset
    # of graph.terms.PHYSICAL_INTERACTION_LEVELS, at the levels it gives.
    interaction_levels: Mapping[str, str]
    # Whether one row may name one paper by several identifiers. True
    # arrives with M1.
    coreferent_publication_ids: bool

    __hash__ = None

    def __post_init__(self):
        object.__setattr__(
            self, "interaction_levels", MappingProxyType(dict(self.interaction_levels))
        )


@dataclass(frozen=True)
class RawRecord:
    """One physical line of a file, as read."""

    # Physical, 1-based, the header included.
    line_number: int
    # None when the line is unreadable or its record id malformed.
    record_key: str | None
    # A RecordKeyBasis value.
    record_key_basis: str
    # The verbatim column map; None when the line cannot be split into the
    # header's columns. A record with issues is unstored even with one.
    fields: Mapping[str, str] | None
    # Of the line's bytes without its LF or CRLF.
    raw_sha256: str
    # Reading issues: a record that has one is unstored.
    issues: tuple[Issue, ...] = ()
    # Bytes of the line without its LF or CRLF, for a batch's byte budget.
    size: int = 0

    __hash__ = None

    def __post_init__(self):
        # Copied, as G3 copies a claim's mappings: a plain dict, which a
        # source record's raw column map stores as it is.
        if self.fields is not None:
            object.__setattr__(self, "fields", dict(self.fields))
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True)
class EntityRef:
    """An endpoint as the record states it, before any mapping."""

    namespace: str
    # As written.
    value: str
    entity_type: str
    taxon: int | None
    label: str = ""


@dataclass(frozen=True)
class PaperRef:
    """One paper a record cites, by the identifiers it states: one in G4
    ({"pmid": "123"}, {"doi": "10.1/x"} or {"local": "<manifest key>"}), each
    normalized as graph.publications stores it. A claim carries it as
    PublicationRef(None, role, paper.stated)."""

    stated: Mapping[str, str]

    __hash__ = None

    def __post_init__(self):
        object.__setattr__(self, "stated", dict(self.stated))


@dataclass(frozen=True)
class MappedRecord:
    """What one record states."""

    # G3 Claims, as far as the row could be read. An endpoint's entity is an
    # EntityRef and its basis is "" until the shared mapping sets them; a
    # paper is PublicationRef(None, role, stated).
    claims: tuple[Claim, ...]
    # Any error-severity issue rejects the record.
    issues: tuple[Issue, ...]

    __hash__ = None


class Adapter(Protocol):
    """One release file format. See the module docstring."""

    spec: AdapterSpec

    def iter_records(self, stream, ctx) -> Iterator[RawRecord]:
        """The file's records, in line order, from a binary stream. May raise
        FileError."""

    def map_record(self, record: RawRecord, ctx) -> MappedRecord:
        """What one readable record states. Makes no query."""

    def synthetic_issues(self, record: RawRecord, ctx) -> tuple[Issue, ...]:
        """In a synthetic release (ctx.is_synthetic), the synthetic_violation
        issues of a record that is not stored but whose fields were read (a
        field too long, a malformed record id, a control character in a
        field, a later line of a record id): a synthetic release is checked
        on every row whose values can be read (§11c), stored or not. Empty
        otherwise, and for a record without fields (a line too long, not
        UTF-8 or with another number of fields than the header). Makes no
        query."""
