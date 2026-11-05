"""The release manifest: the papers a release cites that no PMID or DOI names,
and the vocabulary terms its rows use, with the labels the source gives them.

    {"publications": {"<local key>": {"title": "...", "year": 2026,
                                      "is_synthetic": true}},
     "terms": {"<CURIE>": "<label as the source states it>"}}

It is one of the release's files (a SourceFile with role manifest), strict
JSON (jsonfile.py). Rows cite a manifest paper as ref:<local key>; the commit
(graph.ingest.commit) creates it as a publication local to the release's
dataset, so every release of the dataset names the same row. Every CURIE a
row uses is declared under terms, so a commit records each term with the
source's own label (graph.terms never invents one), and a release's outcome
never depends on what another release committed first.

- A local key is a letter or digit, then up to 99 letters, digits or . _ : -
  (never | or a space, so it can be cited in a list cell).
- A paper's title is non-blank text of at most 10,000 characters; its year
  is 1 to 32767, or null; is_synthetic equals the release's flag, so a
  synthetic release's manifest declares only synthetic papers and any other
  release's none.
- A term's CURIE is normalized as graph.terms normalizes it (MI and ECO), and
  each term is declared once; its label is checked as graph.terms checks a
  label.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from graph.terms import ONTOLOGIES, TermError, checked_label, normalize_curie

from .jsonfile import JsonFileError, boolean, fields, integer, load_strict_json, text
from .protocol import quoted

LOCAL_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
TITLE_MAX = 10_000
YEAR_MAX = 32_767


class ManifestError(JsonFileError):
    """The release manifest is not valid."""


@dataclass(frozen=True)
class ManifestPublication:
    """A paper the manifest declares, cited as ref:<key>."""

    key: str
    title: str
    year: int | None
    is_synthetic: bool


@dataclass(frozen=True)
class Manifest:
    # {local key: paper}
    publications: Mapping[str, ManifestPublication] = field(default_factory=dict)
    # {canonical CURIE: the label the source gives it}
    terms: Mapping[str, str] = field(default_factory=dict)

    __hash__ = None

    def __post_init__(self):
        object.__setattr__(
            self, "publications", MappingProxyType(dict(self.publications))
        )
        object.__setattr__(self, "terms", MappingProxyType(dict(self.terms)))


# A release without a manifest declares nothing.
EMPTY_MANIFEST = Manifest()


def load_manifest(data: bytes, *, release_is_synthetic) -> Manifest:
    """The manifest these bytes hold. Raises ManifestError, naming the value
    at fault, for anything the module docstring does not allow."""
    if not isinstance(release_is_synthetic, bool):
        raise TypeError("release_is_synthetic is True or False.")
    value = load_strict_json(data, what="the release manifest", error=ManifestError)
    fields(
        value,
        where="The release manifest",
        required=(),
        optional=("publications", "terms"),
        error=ManifestError,
    )
    return Manifest(
        publications=_publications(value.get("publications", {}), release_is_synthetic),
        terms=_terms(value.get("terms", {})),
    )


def _publications(value, release_is_synthetic) -> dict:
    if not isinstance(value, dict):
        raise ManifestError('The manifest\'s "publications" is a JSON object.')
    papers = {}
    for key, entry in value.items():
        if not LOCAL_KEY.fullmatch(key):
            raise ManifestError(
                f"The manifest's publication key {quoted(key)} is not a local key: "
                "a letter or digit, then up to 99 letters, digits or . _ : -."
            )
        where = f"publications[{quoted(key)}]"
        fields(
            entry,
            where=where,
            required=("title", "is_synthetic"),
            optional=("year",),
            error=ManifestError,
        )
        title = text(
            entry["title"], where=f"{where}.title", limit=TITLE_MAX, error=ManifestError
        )
        year = integer(
            entry.get("year"),
            where=f"{where}.year",
            low=1,
            high=YEAR_MAX,
            null=True,
            error=ManifestError,
        )
        is_synthetic = boolean(
            entry["is_synthetic"], where=f"{where}.is_synthetic", error=ManifestError
        )
        if is_synthetic != release_is_synthetic:
            raise ManifestError(
                f"{where}.is_synthetic is {str(is_synthetic).lower()}, and the "
                f"release is {'' if release_is_synthetic else 'not '}synthetic: a "
                "synthetic release's manifest declares only synthetic papers, and "
                "any other release's none."
            )
        papers[key] = ManifestPublication(key, title.strip(), year, is_synthetic)
    return papers


def _terms(value) -> dict:
    if not isinstance(value, dict):
        raise ManifestError('The manifest\'s "terms" is a JSON object.')
    terms = {}
    for written, label in value.items():
        where = f"terms[{quoted(written)}]"
        try:
            curie = normalize_curie(written)
        except TermError:
            # Not graph.terms' message, which quotes the whole key: `where`
            # quotes it, cut short (protocol.quoted).
            raise ManifestError(f"{where} is {_not_a_curie(written)}.") from None
        if curie in terms:
            raise ManifestError(
                f"{where} declares {curie} again: each term is declared once."
            )
        if not isinstance(label, str):
            raise ManifestError(
                f"{where} is the term's label, text, not {quoted(label)}."
            )
        try:
            terms[curie] = checked_label(label)
        except TermError as exc:
            raise ManifestError(f"{where}: {exc}") from None
    return terms


def _not_a_curie(written) -> str:
    """Why a term key is no CURIE, without quoting it again: the local id's
    form when its prefix names an ontology openPIP accepts."""
    prefix = written.strip().partition(":")[0]
    for key, (ontology, form) in ONTOLOGIES.items():
        if prefix.isascii() and prefix.upper() == key.upper():
            return (
                f"not a {ontology} CURIE: its local id is {form.pattern} in ASCII "
                "digits"
            )
    return f"not a CURIE of an ontology openPIP accepts ({', '.join(ONTOLOGIES)})"
