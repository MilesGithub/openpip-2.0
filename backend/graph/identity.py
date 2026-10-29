"""Identity normalization: pure functions, with no database access.

A namespace argument is anything with IdentifierNamespace's fields (slug,
scope, fixed_taxon_id, case_policy, version_policy, pattern): a registry
row, or an unsaved instance.

The value a source wrote is always kept. The lookup is what identity and
matching use: the value with its version split off and its case normalized,
checked against the namespace's pattern.
"""

import re
from functools import lru_cache

from .models import CasePolicy, Scope, VersionPolicy

# The column sizes of EntityIdentifier.value, .lookup and .version. SQLite does
# not enforce them, so they are checked here.
VALUE_LENGTH = 255
VERSION_LENGTH = 32

# "uniprotkb:P04637". A prefix is the CURIE prefix of a namespace only if the
# registry says so; parse_prefixed() just splits the text.
_PREFIXED = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9._-]*):(?P<rest>.+)", re.DOTALL)
_VERSIONED = re.compile(r"(?P<base>.+)\.(?P<version>[0-9]+)", re.DOTALL)
_NUMBER = re.compile(r"[0-9]+")
# An isoform accession is the entry accession, a dash and a number.
_ISOFORM_SUFFIX = re.compile(r"-[0-9]+\Z")

# Namespaces whose identifiers carry their own prefix and a number. HGNC IDs
# "are of the format HGNC:n, where n is a unique number" (genenames.org): the
# lookup is always HGNC:<n> with no leading zeros, the prefix may be written
# in any case, and a bare number is read as an HGNC ID only when the hgnc
# namespace is named, because it could equally be an NCBI Gene ID.
EMBEDDED_PREFIXES = {"hgnc": "HGNC:"}


class InvalidIdentifier(ValueError):
    """The value is not an identifier in the namespace it was given for."""


def parse_prefixed(term: str) -> tuple[str | None, str]:
    """Split "prefix:value" into (prefix, value), the prefix lower-cased.

    Returns (None, term) when the term carries no prefix. Whether the prefix
    names a namespace is the caller's question, and an unknown one is never
    stripped so that the rest can be read as a name: "intact:EBI-1234" is not
    the gene symbol EBI-1234.
    """
    text = term.strip()
    match = _PREFIXED.fullmatch(text)
    if match is None or not match["rest"].strip():
        return None, text
    return match["prefix"].lower(), match["rest"].strip()


def split_version(namespace, value: str) -> tuple[str, str]:
    """(value without its version, version), for namespaces that version
    their identifiers; (value, "") for the rest."""
    if namespace.version_policy == VersionPolicy.SPLIT:
        match = _VERSIONED.fullmatch(value)
        if match:
            return match["base"], match["version"]
    return value, ""


def matches_pattern(namespace, lookup: str, *, ignore_case: bool = False) -> bool:
    return _compiled(namespace.pattern, ignore_case).fullmatch(lookup) is not None


def normalize(namespace, value: str, *, explicit: bool = True) -> tuple[str, str]:
    """(lookup, version) for a value written in this namespace.

    explicit says the namespace was named, by the caller or by a prefix on the
    term; only then may an HGNC ID be a bare number. Raises InvalidIdentifier
    when the value is not an identifier of this namespace.
    """
    text = value.strip()
    if not text:
        raise InvalidIdentifier(f"An empty value is not a {namespace.slug} identifier.")
    if len(text) > VALUE_LENGTH:
        raise InvalidIdentifier(
            f"{namespace.slug} values are at most {VALUE_LENGTH} characters."
        )
    base, version = split_version(namespace, text)
    if len(version) > VERSION_LENGTH:
        raise InvalidIdentifier(
            f"{text!r}: versions are at most {VERSION_LENGTH} characters."
        )
    prefix = EMBEDDED_PREFIXES.get(namespace.slug)
    if prefix is not None:
        lookup = _embedded_lookup(namespace, prefix, base, explicit)
    elif namespace.case_policy == CasePolicy.EXACT:
        lookup = base
    else:
        # Some other letters upper-case to ASCII (U+017F, long s, to S), and
        # the pattern alone would then take a non-accession for Q9S123.
        if not base.isascii():
            raise InvalidIdentifier(f"{text!r} is not a {namespace.slug} identifier.")
        lookup = base.upper()
    if len(lookup) > VALUE_LENGTH or not matches_pattern(namespace, lookup):
        raise InvalidIdentifier(f"{text!r} is not a {namespace.slug} identifier.")
    return lookup, version


def scope_key(namespace, taxon_id: int | None, assembly: str | None = None) -> str:
    """The scope key: empty for a global namespace, "t:<taxid>" for a
    taxon-scoped one and "a:<assembly>" for an assembly-scoped one.

    A namespace fixed to a taxon belongs to that taxon only: any other is a
    contradiction to report, never a value to override.
    """
    if namespace.scope == Scope.GLOBAL:
        return ""
    if namespace.scope == Scope.TAXON:
        if taxon_id is None:
            raise ValueError(f"{namespace.slug} identifiers belong to a taxon.")
        fixed = namespace.fixed_taxon_id
        if fixed is not None and taxon_id != fixed:
            raise ValueError(
                f"{namespace.slug} identifiers belong to taxon {fixed}, "
                f"not {taxon_id}."
            )
        return f"t:{taxon_id}"
    if namespace.scope == Scope.ASSEMBLY:
        if not assembly:
            raise ValueError(f"{namespace.slug} identifiers belong to an assembly.")
        return f"a:{assembly}"
    raise ValueError(f"{namespace.slug} has an unknown scope, {namespace.scope!r}.")


def identity_key(namespace, scope_key: str, lookup: str) -> str:
    """The identity key of an entity defined by this identifier."""
    return f"{namespace.slug}|{scope_key}|{lookup}"


def uniprot_entity_type(value: str) -> str:
    """protein_isoform for an accession with an isoform suffix, -1 included
    (P04637-1, P04637-2); protein for an entry accession (P04637).

    An isoform is never read as its entry, and no isoform is taken to be the
    entry's own sequence. synthetic_protein values follow the same rule.
    """
    return "protein_isoform" if _ISOFORM_SUFFIX.search(value.strip()) else "protein"


def _embedded_lookup(namespace, prefix: str, base: str, explicit: bool) -> str:
    if base[: len(prefix)].upper() == prefix:
        number = base[len(prefix) :]
    elif explicit:
        number = base
    else:
        raise InvalidIdentifier(
            f"{base!r} is read as a {namespace.slug} identifier only when the "
            f"namespace is named or the value starts with {prefix}."
        )
    if not _NUMBER.fullmatch(number) or int(number) == 0:
        raise InvalidIdentifier(f"{base!r} is not a {namespace.slug} identifier.")
    return f"{prefix}{int(number)}"


@lru_cache(maxsize=None)
def _compiled(pattern: str, ignore_case: bool) -> re.Pattern:
    # ASCII: \d and \w would otherwise accept digits and letters of any script.
    flags = re.ASCII | (re.IGNORECASE if ignore_case else 0)
    return re.compile(pattern, flags)
