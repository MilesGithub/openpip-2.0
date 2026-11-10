"""What a browser instance's fields and selection may hold, with every
sentence that says so.

A rule on one field is a function validator on that field
(browsers.models). The migration names each validator by its import path, so
its body and the lists it reads (RESERVED_SLUGS, TAGS) change with no
migration; only the function names are fixed, and each stays importable.
Django's clean_fields() never passes the empty value of a blank field to a
validator, so BrowserInstance.clean() refuses the empty values of the wrong
type itself.

The rules that span rows or read many-to-many values are validate_facets and
validate_selection. Nothing in Model.full_clean() runs them, and DRF runs
field validators but never Model.clean(), so every write path calls them:
the admin's form and selection formset, and the loader.

This module never imports browsers.models at its top, since browsers.models
imports its validators.
"""

import re
import unicodedata
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from graph.api.limits import MAX_RELEASES

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_SLUG_CHARS = 50
# Words a later page under /browsers/ may need as its own address.
RESERVED_SLUGS = (
    "new",
    "edit",
    "admin",
    "api",
    "all",
    "search",
    "explore",
    "static",
    "media",
    "settings",
)
# The biology areas the gallery's tag filter offers.
TAGS = (
    "protein_interaction",
    "gene_regulation",
    "rna_regulation",
    "genetic_interaction",
    "signaling",
    "ligand_receptor",
    "protein_modification",
    "disease",
    "host_pathogen",
    "phenotype",
)
MAX_TAGS = 10
MAX_LONG_TEXT_CHARS = 20_000
# So browser= never pins more releases than releases= may name.
MAX_INSTANCE_RELEASES = MAX_RELEASES
# graph.terms' name for PSI-MI, whose terms name detection methods.
ASSAY_ONTOLOGY = "psi-mi"

# Category Cc is exactly U+0000 to U+001F and U+007F to U+009F, a set
# Unicode's stability policy fixes, so a character class finds them without
# looking each character up.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# The same, less the tab and the two line breaks a paragraph may hold.
BLOCK_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Bidirectional embeddings, overrides and isolates, which reorder what a
# reader sees; a line also refuses the line and paragraph separators.
REORDERING_CHARACTERS = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
LINE_BREAKING_CHARACTERS = re.compile(r"[\u202a-\u202e\u2066-\u2069\u2028\u2029]")

SLUG_GRAMMAR = (
    "A slug is lower-case letters and digits in groups joined by single "
    "hyphens, such as synthetic-regulation."
)
SLUG_RESERVED = "'{slug}' is reserved."
CONTROL_CHARACTER = "This text holds a control character."
REORDERING_CHARACTER = "This text holds a character that reorders or breaks it."
TEXT_TOO_LONG = f"This text is at most {MAX_LONG_TEXT_CHARS:,} characters."
NOT_HTTPS = "An external URL is an https URL."
URL_CREDENTIALS = "An external URL holds no user name or password."
URL_HOST_NOT_ASCII = "Write the host in its ASCII (xn--) form."
URL_CHARACTER = "An external URL holds no control or formatting character."
TAGS_NOT_LIST = "tags is a list of tag names."
TAGS_NOT_NAMES = "tags holds tag names only."
UNKNOWN_TAG = "Unknown tag '{tag}'."
REPEATED_TAG = "tags names each tag once."
TOO_MANY_TAGS = f"tags names at most {MAX_TAGS} tags."
EXTERNAL_NEEDS_URL = "An external instance needs an https URL."
ONLY_EXTERNAL_HAS_URL = "Only an external instance has a URL."
PRIMARY_TAXON_OUTSIDE_TAXA = "The primary taxon is one of the taxa."
ASSAY_NOT_PSI_MI = "An assay is a PSI-MI term, such as MI:0018."
AVAILABLE_TYPED_NEEDS_RELEASE = (
    "An available typed instance selects at least one release."
)
ONLY_TYPED_SELECTS = "Only a typed instance selects releases."
TOO_MANY_RELEASES = f"An instance selects at most {MAX_INSTANCE_RELEASES} releases."
SECOND_PORTAL_PPI = "This portal already has a portal_ppi entry, {slug}."

_HTTPS_URL = URLValidator(schemes=["https"])


def validate_slug(value):
    """The slug's grammar (SLUG in full), its length and the reserved words.
    SlugField runs Django's own slug validator before this one and its
    length validator after it."""
    if not (len(value) <= MAX_SLUG_CHARS and SLUG.fullmatch(value)):
        raise ValidationError(SLUG_GRAMMAR, code="slug_grammar")
    if value in RESERVED_SLUGS:
        raise ValidationError(SLUG_RESERVED.format(slug=value), code="slug_reserved")


def _text_problems(value, controls, reordering) -> list:
    problems = []
    if controls.search(value):
        problems.append(ValidationError(CONTROL_CHARACTER, code="control_character"))
    if reordering.search(value):
        problems.append(
            ValidationError(REORDERING_CHARACTER, code="reordering_character")
        )
    return problems


def validate_line(value):
    """Plain text on one line (a title, short title or summary): no control
    character, nothing that reorders the text, and no line or paragraph
    separator. Markup is kept as typed and shown as text."""
    problems = _text_problems(value, CONTROL_CHARACTERS, LINE_BREAKING_CHARACTERS)
    if problems:
        raise ValidationError(problems)


def validate_block(value):
    """Plain text in paragraphs (a description or methods text): tabs and
    line breaks, but no other control character, nothing that reorders the
    text, and at most MAX_LONG_TEXT_CHARS characters."""
    problems = _text_problems(value, BLOCK_CONTROL_CHARACTERS, REORDERING_CHARACTERS)
    if len(value) > MAX_LONG_TEXT_CHARS:
        problems.append(ValidationError(TEXT_TOO_LONG, code="text_too_long"))
    if problems:
        raise ValidationError(problems)


def validate_https_url(value):
    """An https URL, which every reader sees as it is stored and nothing
    fetches, so nothing in it may mislead: no user name or password
    (https://trusted.example@evil.example/ opens evil.example), a host in
    ASCII (a look-alike letter shows only in Unicode), and no control or
    formatting character (one can reverse what follows it)."""
    try:
        _HTTPS_URL(value)
    except ValidationError:
        raise ValidationError(NOT_HTTPS, code="not_https") from None
    parts = urlsplit(value)
    problems = []
    if "@" in parts.netloc:
        problems.append(ValidationError(URL_CREDENTIALS, code="url_credentials"))
    if not (parts.hostname or "").isascii():
        problems.append(ValidationError(URL_HOST_NOT_ASCII, code="url_host"))
    if any(unicodedata.category(character) in ("Cc", "Cf") for character in value):
        problems.append(ValidationError(URL_CHARACTER, code="url_character"))
    if problems:
        raise ValidationError(problems)


def validate_tags(value):
    """A list of distinct names from TAGS, at most MAX_TAGS. Its type and its
    items' types are checked first, stopping there, so nothing hashes or
    compares a value that is not a string."""
    if not isinstance(value, list):
        raise ValidationError(TAGS_NOT_LIST, code="tags_not_list")
    if not all(isinstance(tag, str) for tag in value):
        raise ValidationError(TAGS_NOT_NAMES, code="tags_not_names")
    problems = [
        ValidationError(UNKNOWN_TAG.format(tag=tag), code="unknown_tag")
        for tag in dict.fromkeys(value)
        if tag not in TAGS
    ]
    if len(set(value)) < len(value):
        problems.append(ValidationError(REPEATED_TAG, code="repeated_tag"))
    if len(value) > MAX_TAGS:
        problems.append(ValidationError(TOO_MANY_TAGS, code="too_many_tags"))
    if problems:
        raise ValidationError(problems)


def validate_facets(*, taxa, primary_taxon, assays):
    """The facets an admin states: the primary taxon is one of the taxa when
    both are given, and every assay is a PSI-MI term. taxa and assays are
    rows, or None when not given; primary_taxon is a row or None. Errors are
    keyed to the admin form's fields of the same names."""
    problems = {}
    stated = {taxon.pk for taxon in (() if taxa is None else taxa)}
    if primary_taxon is not None and stated and primary_taxon.pk not in stated:
        problems["primary_taxon"] = [
            ValidationError(PRIMARY_TAXON_OUTSIDE_TAXA, code="primary_taxon")
        ]
    if any(
        term.ontology != ASSAY_ONTOLOGY for term in (() if assays is None else assays)
    ):
        problems["assays"] = [ValidationError(ASSAY_NOT_PSI_MI, code="assay")]
    if problems:
        raise ValidationError(problems)


def validate_selection(instance, releases):
    """The releases an instance selects, as it will be saved: `instance` with
    its submitted values, `releases` every release it will select, of either
    role (rows or ids; only their number counts). An available typed
    instance selects at least one release, no other kind selects any, none
    selects more than MAX_INSTANCE_RELEASES, and an install has one
    portal_ppi entry.

    Raises a ValidationError holding a list of sentences, never a dict: a
    formset shows a list as its own errors, and a dict-shaped error has no
    error_list. Reads no many-to-many value, so it runs before the instance
    is saved."""
    # browsers.models imports this module, so it is imported here.
    from .models import Kind, Status

    releases = list(releases)
    problems = []
    if (
        instance.kind == Kind.TYPED
        and instance.status == Status.AVAILABLE
        and not releases
    ):
        problems.append(AVAILABLE_TYPED_NEEDS_RELEASE)
    if instance.kind != Kind.TYPED and releases:
        problems.append(ONLY_TYPED_SELECTS)
    if len(releases) > MAX_INSTANCE_RELEASES:
        problems.append(TOO_MANY_RELEASES)
    if instance.kind == Kind.PORTAL_PPI:
        others = type(instance)._default_manager.filter(kind=Kind.PORTAL_PPI)
        if instance.pk is not None:
            others = others.exclude(pk=instance.pk)
        other = (
            others.order_by("display_order", "slug")
            .values_list("slug", flat=True)
            .first()
        )
        if other is not None:
            problems.append(SECOND_PORTAL_PPI.format(slug=other))
    if problems:
        raise ValidationError(problems)
