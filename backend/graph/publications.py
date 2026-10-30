"""Publications: one row per paper, whichever identifiers name it.

A paper arrives with a PMID, a DOI, a PMCID, an IMEx id, several of them, or
none: a title-only paper is named by its dataset's release manifest with a
local key, unique within that dataset. Identifiers of one paper are merged
into one row, so a later call that gives a PMID and a DOI fills the DOI into
the row its PMID names. When one call's identifiers match different rows,
the call attaches to the row named by the strongest of them (PMID, then DOI,
PMCID, IMEx id, local key: architecture §4.4). An identifier that another
row holds, or that differs from the value the row already has, is not
written: it is reported as a conflict for an audited merge, and never fails
the call.

Identifiers are stored normalized, by the functions below, each citing the
authority its rule comes from. The Publication model refuses any other form
on every write path.
"""

import re
import string
from dataclasses import dataclass
from functools import reduce
from operator import or_

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import HTTP_URL, ImmutableRecordError, Publication, SourceDataset


class PublicationError(Exception):
    """Nothing was written: a value is not an identifier of its kind, or the
    call's identifiers and flags cannot name one paper."""


def _length(name) -> int:
    # SQLite does not enforce the column sizes, so they are checked here.
    return Publication._meta.get_field(name).max_length


def _text(value, what) -> str:
    if not isinstance(value, str):
        raise PublicationError(f"{value!r} is not a {what}.")
    return value.strip()


def _fits(text, name, what) -> str:
    if len(text) > _length(name):
        raise PublicationError(f"A {what} is at most {_length(name)} characters.")
    return text


# ── Normalizers ─────────────────────────────────────────────────────────────

# PubMed Help, checked 2026-10-29 (https://pubmed.ncbi.nlm.nih.gov/help/): the
# PMID is the "PubMed Unique Identifier", a "Unique number assigned to each
# PubMed citation", and "PMIDs do not change over time or during processing
# and are never reused". A number, so 012345 is the citation 12345, and no
# citation is 0. Sources write it bare, as "pubmed:<n>" (PSI-MI MITAB's
# publication column, as psicquic/mitab.py writes it) or as "PMID: <n>", the
# form PubMed Help asks for in error reports.
_PMID_PREFIX = re.compile(r"(?:pubmed|pmid)\s*:\s*", re.IGNORECASE)
_DIGITS = re.compile(r"[0-9]+")


def normalize_pmid(value: str) -> str:
    """The PMID as digits, without a pubmed: or pmid: prefix or leading
    zeros. Zero, and anything that is not a number, raise PublicationError."""
    text = _text(value, "PMID")
    prefix = _PMID_PREFIX.match(text)
    if prefix:
        text = text[prefix.end() :]
    # str(int(text)), without converting a very long string to an integer.
    digits = text.lstrip("0")
    if not _DIGITS.fullmatch(text) or not digits:
        raise PublicationError(f"{value!r} is not a PMID: a PMID is a number from 1.")
    return _fits(digits, "pmid", "PMID")


# The doi URN namespace registration, checked 2026-10-29 (IANA, registered
# for the DOI Foundation on 2023-03-14;
# https://www.iana.org/assignments/urn-namespaces/urn-formal/doi): a DOI name
# is a prefix, "a forward slash (/) as the delimiting character" and a
# suffix; the prefix is the directory indicator "10" followed by a registrant
# code, "separated by a full stop (period)"; "the DOI-NSS is case insensitive
# with respect to printable ASCII characters"; and 'The "doi:" label is not
# part of the DOI name'. The DOI Foundation's resolution documentation
# (https://www.doi.org/the-identifier/resources/factsheets/
# doi-resolution-documentation) names its proxy "https://doi.org (preferred)"
# and "the earlier but no longer preferred dx.doi.org", over HTTPS or HTTP.
# So `doi` keeps the name as written, without the label or the proxy, and
# `doi_lookup` lower-cases its ASCII letters only: case is ignored for those
# alone.
_DOI_PREFIX = re.compile(r"doi:\s*|https?://(?:dx\.)?doi\.org/", re.IGNORECASE)
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def normalize_doi(value: str) -> str:
    """The DOI name as written, without a doi: label or a doi.org or
    dx.doi.org proxy URL. Anything but 10.<registrant>/<suffix> raises
    PublicationError."""
    text = _text(value, "DOI")
    prefix = _DOI_PREFIX.match(text)
    if prefix:
        text = text[prefix.end() :]
    registrant, slash, suffix = text.partition("/")
    if (
        not registrant.startswith("10.")
        or registrant == "10."
        or not slash
        or not suffix
        or not text.isprintable()
    ):
        raise PublicationError(
            f"{value!r} is not a DOI: a DOI name is 10.<registrant>/<suffix>."
        )
    return _fits(text, "doi", "DOI")


def doi_lookup(doi: str) -> str:
    """The DOI with its ASCII letters lower-cased: the only case DOI names
    ignore. Other letters keep their case."""
    return doi.translate(_ASCII_LOWER)


# PMC's ID Converter API documentation, checked 2026-10-29
# (https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/): PMCIDs "must have
# the "PMC" prefix, in order to distinguish them from PMIDs", which "can have
# the same numerical value as the numerical portion of a PMCID". PubMed Help
# writes one as PMC2600426. So the prefix is required (a bare number may be
# a PMID) and written in upper case.
_PMCID = re.compile(r"(?i:PMC)([0-9]+)")


def normalize_pmcid(value: str) -> str:
    """PMC and digits, the prefix upper-cased. A bare number raises
    PublicationError."""
    match = _PMCID.fullmatch(_text(value, "PMCID"))
    if match is None:
        raise PublicationError(f"{value!r} is not a PMCID: a PMCID is PMC and digits.")
    return _fits(f"PMC{match[1]}", "pmcid", "PMCID")


# IntAct's curation manual, checked 2026-10-29 (https://intact-portal.gitbook.
# io/intact-portal-doc/docs/curation-manual/publication-level): the IMEx
# accession assigned to a publication "is of the form IM-XXXXX", issued by
# IMEx Central. The Bioregistry (https://bioregistry.io/registry/imex) gives
# IMEx identifiers as digits with an optional -<digits> after them, behind
# the embedded "IM-" (IM-19210-3): that longer form is not a publication's
# accession. A publication's IMEx id is therefore IM- and digits, as written.
_IMEX = re.compile(r"IM-[0-9]+")


def normalize_imex(value: str) -> str:
    """IM- and digits. Anything else raises PublicationError."""
    text = _text(value, "IMEx id")
    if not _IMEX.fullmatch(text):
        raise PublicationError(
            f"{value!r} is not a publication's IMEx id: one is IM- and digits."
        )
    return _fits(text, "imex_id", "IMEx id")


IDENTIFIER_NORMALIZERS = {
    "pmid": normalize_pmid,
    "doi": normalize_doi,
    "pmcid": normalize_pmcid,
    "imex_id": normalize_imex,
}
# The lookup each identifier is matched by, in identity precedence.
PRECEDENCE = ("pmid", "doi_lookup", "pmcid", "imex_id")


# ── One row per paper ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class PublicationConflict:
    """An identifier from the call that was not written to the publication."""

    # pmid, doi, pmcid or imex_id.
    field: str
    # The call's value, normalized.
    value: str
    # What the publication holds in that field; "" when it was blank there.
    current: str
    # The other publication that holds the value, when one does.
    holder_id: int | None


@dataclass(frozen=True)
class PublicationResult:
    publication: Publication
    created: bool
    conflicts: list


def get_or_create_publication(
    *,
    pmid="",
    doi="",
    pmcid="",
    imex_id="",
    local_dataset=None,
    local_key="",
    title="",
    authors="",
    journal="",
    year=None,
    url="",
    is_synthetic=False,
) -> PublicationResult:
    """The publication these identifiers name, created if none does.

    A row that matches is found by identity precedence and locked; each of
    its blank identifiers is filled from the call in its own savepoint, so
    that one another row holds (possibly written at this moment) becomes a
    reported conflict, not an error. Descriptive fields fill blanks only:
    the first writer's title stays. A new row is inserted race-safely (ON
    CONFLICT DO NOTHING, then a re-select), so two calls at once give one
    row, and `created` is true for the call whose insert it was.
    """
    if not settings.TYPED_GRAPH_ENABLED:
        raise PublicationError(
            "The typed graph is off on this portal (TYPED_GRAPH_ENABLED), so no "
            "publication is recorded. Nothing was written."
        )
    ids = {
        name: normalize(value) if _given(value) else ""
        for name, normalize, value in (
            ("pmid", normalize_pmid, pmid),
            ("doi", normalize_doi, doi),
            ("pmcid", normalize_pmcid, pmcid),
            ("imex_id", normalize_imex, imex_id),
        )
    }
    local_key = (
        _fits(_text(local_key, "local key"), "local_key", "local key")
        if _given(local_key)
        else ""
    )
    dataset_id = _dataset_id(local_dataset)
    _check_identity(ids, dataset_id, local_key, bool(is_synthetic))
    details = _details(title, authors, journal, year, url)
    keys = {
        "pmid": ids["pmid"],
        "doi_lookup": doi_lookup(ids["doi"]),
        "pmcid": ids["pmcid"],
        "imex_id": ids["imex_id"],
    }

    with transaction.atomic():
        row = _attach(keys, dataset_id, local_key)
        if row is None:
            values = {
                **ids,
                "local_dataset_id": dataset_id,
                "local_key": local_key,
                "is_synthetic": bool(is_synthetic),
                **details,
            }
            candidate = Publication(**values)
            Publication.objects.bulk_create([candidate], ignore_conflicts=True)
            row = _attach(keys, dataset_id, local_key)
            if row is None:
                # SQLite's INSERT OR IGNORE also skips a row that breaks a
                # check constraint.
                raise PublicationError(
                    "The publication was not written: a database constraint "
                    "refused it."
                )
            # bulk_create() stamped the candidate's created_at: the row found
            # is this call's own exactly when it carries the same stamp.
            if row.created_at == candidate.created_at:
                return PublicationResult(row, True, [])
        conflicts = _merge(row, ids, keys, details, bool(is_synthetic))
    return PublicationResult(row, False, conflicts)


def _given(value) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _dataset_id(local_dataset):
    if local_dataset is None:
        return None
    pk = local_dataset.pk if isinstance(local_dataset, SourceDataset) else local_dataset
    if pk is None or not SourceDataset.objects.filter(pk=pk).exists():
        raise PublicationError(f"Unknown source dataset {local_dataset!r}.")
    return pk


def _check_identity(ids, dataset_id, local_key, is_synthetic):
    named = [name for name, value in ids.items() if value]
    if local_key:
        if dataset_id is None:
            raise PublicationError(
                "A local key is unique within its dataset: give local_dataset too."
            )
        if named:
            raise PublicationError(
                "A local key names a paper that has no PMID, DOI, PMCID or IMEx "
                f"id; this call also gives {', '.join(named)}."
            )
    elif dataset_id is not None:
        raise PublicationError("local_dataset comes only with a local key.")
    if is_synthetic and named:
        raise PublicationError(
            "A synthetic paper has no PMID, DOI, PMCID or IMEx id; this call gives "
            f"{', '.join(named)}."
        )
    if not named and not local_key:
        raise PublicationError(
            "A publication needs a PMID, a DOI, a PMCID, an IMEx id or a local key."
        )


def _details(title, authors, journal, year, url) -> dict:
    details = {
        "title": _optional_text(title, "title"),
        "authors": _optional_text(authors, "authors"),
        "journal": _fits(_optional_text(journal, "journal"), "journal", "journal"),
        "year": _year(year),
        "url": _fits(_optional_text(url, "URL"), "url", "URL"),
    }
    if details["url"]:
        try:
            HTTP_URL(details["url"])
        except ValidationError:
            raise PublicationError(f"{url!r} is not an http or https URL.") from None
    return details


def _optional_text(value, what) -> str:
    return "" if value is None else _text(value, what)


def _year(value):
    if value is None or value == "":
        return None
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 32767:
        raise PublicationError(f"{value!r} is not a publication year.")
    return value


def _attach(keys, dataset_id, local_key):
    """The row the call attaches to, by identity precedence, locked; None
    when none matches."""
    conditions = [Q(**{name: value}) for name, value in keys.items() if value]
    if local_key:
        conditions.append(Q(local_dataset_id=dataset_id, local_key=local_key))
    rows = list(Publication.objects.filter(reduce(or_, conditions)).order_by("pk"))
    chosen = next(
        (
            row
            for name in PRECEDENCE
            if keys[name]
            for row in rows
            if getattr(row, name) == keys[name]
        ),
        None,
    )
    if chosen is None:
        chosen = next(
            (
                row
                for row in rows
                if local_key
                and (row.local_dataset_id, row.local_key) == (dataset_id, local_key)
            ),
            None,
        )
    if chosen is None:
        return None
    return Publication.objects.select_for_update().filter(pk=chosen.pk).first()


def _merge(row, ids, keys, details, is_synthetic) -> list:
    if row.is_synthetic != is_synthetic:
        raise PublicationError(
            f"Publication {row.pk} is {'' if row.is_synthetic else 'not '}synthetic, "
            "and this call says otherwise."
        )
    conflicts = []
    for name in Publication.IDENTIFIER_FIELDS:
        value = ids[name]
        if not value:
            continue
        key_name = "doi_lookup" if name == "doi" else name
        key = keys[key_name]
        if not getattr(row, name):
            holder = _holder(key_name, key, row.pk)
            if holder is None and _fill(row, name, value):
                continue
        if getattr(row, key_name) == key:
            continue
        conflicts.append(
            PublicationConflict(
                name, value, getattr(row, name), _holder(key_name, key, row.pk)
            )
        )
    _fill_details(row, details)
    return conflicts


def _holder(key_name, key, pk):
    """The other publication that holds this identifier, if any."""
    return (
        Publication.objects.filter(**{key_name: key})
        .exclude(pk=pk)
        .values_list("pk", flat=True)
        .first()
    )


def _fill(row, name, value) -> bool:
    """Write one blank identifier in its own savepoint. Another row may hold
    the value, written since this call looked: its partial unique key then
    refuses the write, and the caller reports a conflict instead of failing."""
    try:
        with transaction.atomic():
            setattr(row, name, value)
            row.save(update_fields=[name, "updated_at"])
    except (IntegrityError, ImmutableRecordError):
        row.refresh_from_db(fields=[name, "doi_lookup", "updated_at"])
        return False
    return True


def _fill_details(row, details):
    blanks = {
        name: value
        for name, value in details.items()
        if value not in ("", None) and getattr(row, name) in ("", None)
    }
    if blanks:
        for name, value in blanks.items():
            setattr(row, name, value)
        row.save(update_fields=[*blanks, "updated_at"])
