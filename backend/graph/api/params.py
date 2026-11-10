"""The query parameters of /api/graph/v1/, read strictly (architecture §7.1).

Params collects problems instead of raising, so one 400 lists every problem:
unknown names when the view starts, then path parameters, then the route's
parameters in its allowed order, as the handler reads them. The handler then
calls raise_problems().

Nothing is guessed. A parameter given twice is refused, since QueryDict.get
would silently keep the last, a confident answer to another question. Values
compare exactly and case-sensitively, as the resolver's do. A well-formed ref
is a selection, never checked against what exists: a ref the caller may not
see must read like one that names nothing.
"""

import math
import re
import unicodedata
from dataclasses import dataclass, field

from browsers.rules import MAX_SLUG_CHARS, SLUG

from ..models import ContextFacet
from ..summary import ContextMatch, ScoreRange
from ..terms import TermError, normalize_curie, ontology_of
from .limits import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_FACETS,
    MAX_RELEASES,
    Q_MAX_CHARS,
    RESOLVE_MAX_TERMS,
)
from .refs import parse_ref

# The most problems one 400 lists: an 8 KB URL must not give a 400 KB body.
MAX_PROBLEMS = 20
# Not spaces: labels may contain them.
TERM_SEPARATORS = re.compile(r"[,\t\r\n]+")
# A whole number as a ref's digits are written: no sign, no leading zero, at
# most 19 digits, so int() never meets a string long enough to refuse.
WHOLE_NUMBER = re.compile(r"0|[1-9][0-9]{0,18}")
# A score bound, which float() then reads.
BOUND = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
MALFORMED_SCORE = (
    "score looks like score_type:min:max, with an empty min or max for an open "
    "bound."
)
# The context.<facet> parameters, in documentation order.
CONTEXT_NAMES = tuple(f"context.{facet}" for facet in ContextFacet.values)
# How the sentences of Params.refs name a kind: one of them, several of them.
KIND_WORDS = {
    "entity": ("an entity", "entities"),
    "relationship": ("a relationship", "relationships"),
    "release": ("a release", "releases"),
    "evidence": ("an evidence row", "evidence rows"),
}


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a request. `detail` is the sentence that says so,
    and names the parameter, and the allowed values or the limit when there
    is one. Two problems are equal when they say the same of the same value."""

    parameter: str
    value: str | None = None
    allowed: list | None = field(default=None, compare=False)
    limit: int | None = field(default=None, compare=False)
    detail: str = ""


class ParameterError(Exception):
    """The request's problems, as the body of a 400, which
    TypedGraphView.handle_exception answers.

    Not a DRF ValidationError: every APIException passes its detail through
    _get_error_details, which turns each leaf into a string, so null would
    reach the client as "None" and 200 as "200"."""

    def __init__(self, problems, *, more=False):
        sentences = [problem.detail for problem in problems]
        if more:
            sentences.append("and more.")
        self.body = {
            "detail": " ".join(sentences),
            "errors": [
                {
                    "parameter": problem.parameter,
                    "value": problem.value,
                    "allowed": problem.allowed,
                    "limit": problem.limit,
                }
                for problem in problems
            ],
        }
        super().__init__(self.body["detail"])


class Params:
    """One request's query string. `allowed` is the route's tuple of names, in
    the order its sentences list them; `format` is DRF's, and never unknown."""

    def __init__(self, query, *, allowed):
        self.query = query
        self.allowed = tuple(allowed)
        self.problems = []
        self.more = False
        if self.allowed:
            listed = f"Allowed: {', '.join(self.allowed)}."
        else:
            listed = "This route takes no parameters."
        for name in query:
            if name not in self.allowed and name != "format":
                self.add(
                    Problem(
                        name,
                        allowed=list(self.allowed),
                        detail=f"Unknown parameter {name!r}. {listed}",
                    )
                )

    def add(self, problem: Problem) -> None:
        """Records a problem once. Past MAX_PROBLEMS only the fact is kept, and
        `detail` ends with "and more."."""
        if problem in self.problems:
            return
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(problem)
        else:
            self.more = True

    def single(self, name: str) -> str | None:
        """The parameter's value; None when it is absent, or given twice."""
        values = self.query.getlist(name)
        if len(values) > 1:
            self.add(Problem(name, detail=f"{name} is given once."))
            return None
        return values[0] if values else None

    def terms(self, name: str) -> list[str]:
        """The terms of a repeatable parameter, as given, duplicates kept."""
        values = self.query.getlist(name)
        pieces = (
            piece.strip() for value in values for piece in TERM_SEPARATORS.split(value)
        )
        terms = [piece for piece in pieces if piece]
        for term in terms:
            if len(term) > Q_MAX_CHARS:
                self.add(
                    Problem(
                        name,
                        term,
                        limit=Q_MAX_CHARS,
                        detail=f"{name} terms are at most {Q_MAX_CHARS} characters.",
                    )
                )
            # PostgreSQL refuses a NUL in a string, so it would be a 500.
            if any(unicodedata.category(letter) == "Cc" for letter in term):
                self.add(
                    Problem(name, term, detail=f"{name} holds a control character.")
                )
        if len(terms) > RESOLVE_MAX_TERMS:
            self.add(
                Problem(
                    name,
                    limit=RESOLVE_MAX_TERMS,
                    detail=f"{name} names at most {RESOLVE_MAX_TERMS} terms.",
                )
            )
        if not terms:
            first = values[0] if values else None
            self.add(
                Problem(name, first or None, detail=f"{name} names at least one term.")
            )
        return terms

    def phrase(self, name: str) -> str | None:
        """One value, stripped, read as one phrase: never split, as terms()
        splits resolve's q. It is refused when it is empty once stripped,
        longer than Q_MAX_CHARS characters, or holds a control character,
        which no text it is matched against may hold."""
        value = self.single(name)
        if value is None:
            return None
        phrase = value.strip()
        if not phrase:
            self.add(Problem(name, value or None, detail=f"{name} names a value."))
            return None
        refused = False
        if len(phrase) > Q_MAX_CHARS:
            self.add(
                Problem(
                    name,
                    phrase,
                    limit=Q_MAX_CHARS,
                    detail=f"{name} is at most {Q_MAX_CHARS} characters.",
                )
            )
            refused = True
        if any(unicodedata.category(letter) == "Cc" for letter in phrase):
            self.add(Problem(name, phrase, detail=f"{name} holds a control character."))
            refused = True
        return None if refused else phrase

    def choice(self, name: str, allowed) -> str | None:
        """The value, when it is one of those `allowed` returns. `allowed` is a
        callable with no arguments, called only when the parameter is present,
        so a parameter not given costs no query."""
        value = self.single(name)
        if value is None:
            return None
        choices = list(allowed())
        if value in choices:
            return value
        listed = ", ".join(choices) or "none yet"
        self.add(
            Problem(
                name,
                value,
                allowed=choices,
                detail=f"Unknown {name} {value!r}. Allowed: {listed}.",
            )
        )
        return None

    def choices(
        self, name: str, allowed, *, what: str = "value", limit: int | None = None
    ) -> tuple[str, ...] | None:
        """The distinct valid pieces of a repeatable, comma-separated list, in
        order of first appearance, each one of those `allowed` returns (called
        only when the parameter is present); None when it is absent. `limit`
        counts the distinct pieces. Pieces are not stripped, as refs are not.
        """
        if name not in self.query:
            return None
        pieces = [
            piece for value in self.query.getlist(name) for piece in value.split(",")
        ]
        if not any(pieces):
            self.add(Problem(name, detail=f"{name} names at least one {what}."))
            return ()
        choices = list(allowed())
        listed = ", ".join(choices) or "none yet"
        found = []
        for piece in pieces:
            if piece not in choices:
                self.add(
                    Problem(
                        name,
                        piece,
                        allowed=choices,
                        detail=f"Unknown {name} {piece!r}. Allowed: {listed}.",
                    )
                )
            elif piece not in found:
                found.append(piece)
        if limit is not None and len(found) > limit:
            self.add(
                Problem(
                    name,
                    limit=limit,
                    detail=f"{name} names at most {limit} {what}s.",
                )
            )
        return tuple(found)

    def flag(self, name: str) -> bool | None:
        """true or false, as a bool."""
        value = self.choice(name, lambda: ["true", "false"])
        return None if value is None else value == "true"

    def integer(self, name: str, *, floor: int, cap: int) -> int | None:
        """A whole number from floor to cap, written as a ref's digits are
        (WHOLE_NUMBER); a longer digit string is the same problem as a value
        above the cap."""
        value = self.single(name)
        if value is None:
            return None
        if WHOLE_NUMBER.fullmatch(value) and floor <= int(value) <= cap:
            return int(value)
        self.add(
            Problem(
                name,
                value,
                limit=cap,
                detail=f"{name} is a whole number from {floor} to {cap:,}.",
            )
        )
        return None

    def ref(self, name: str, kind: str) -> int | None:
        """The id of one ref of this kind."""
        value = self.single(name)
        if value is None:
            return None
        pk = parse_ref(value, kind)
        if pk is None:
            one, _ = KIND_WORDS[kind]
            self.add(
                Problem(name, value, detail=f"{name}: {one} ref looks like {kind}:3.")
            )
        return pk

    def slug(self, name: str, example: str) -> str | None:
        """One slug, as browsers.rules writes them: lower-case letters and
        digits in groups joined by single hyphens, at most MAX_SLUG_CHARS
        characters, `example` showing one. As a well-formed ref is, a
        well-formed slug is a selection, never checked against what
        exists."""
        value = self.single(name)
        if value is None:
            return None
        if len(value) <= MAX_SLUG_CHARS and SLUG.fullmatch(value):
            return value
        self.add(
            Problem(name, value, detail=f"{name} looks like a slug, such as {example}.")
        )
        return None

    def taxon(self, allowed, *, name: str = "taxon") -> int | None:
        """The taxon, when it is one of the taxids (ints) `allowed` returns.
        Comparing strings refuses what int() alone would accept: +9606, 9_606,
        other scripts' digits."""
        value = self.choice(name, lambda: [str(pk) for pk in sorted(allowed())])
        return None if value is None else int(value)

    def methods(self) -> tuple[str, ...] | None:
        """The distinct detection methods of the comma-separated `method`, in
        order of first appearance, each a PSI-MI CURIE in canonical form. A
        CURIE is normalized, never looked up: a well-formed one that names no
        term matches nothing, as a well-formed ref does."""
        if "method" not in self.query:
            return None
        pieces = [
            piece
            for value in self.query.getlist("method")
            for piece in value.split(",")
        ]
        if not any(pieces):
            self.add(Problem("method", detail="method names at least one method."))
            return ()
        found = []
        for piece in pieces:
            try:
                curie = normalize_curie(piece)
                psi_mi = ontology_of(curie) == "psi-mi"
            except TermError:
                psi_mi = False
            if not psi_mi:
                self.add(
                    Problem(
                        "method",
                        piece,
                        detail=(
                            "method: a detection method is a PSI-MI CURIE, such as "
                            "MI:0018."
                        ),
                    )
                )
            elif curie not in found:
                found.append(curie)
        return tuple(found)

    def curie(self, name: str, ontology: str, example: str) -> str | None:
        """One CURIE of this ontology (graph.terms), in canonical form. As
        each piece of methods() is, it is normalized and never looked up: a
        well-formed one that names no term matches nothing."""
        value = self.single(name)
        if value is None:
            return None
        try:
            curie = normalize_curie(value)
            known = ontology_of(curie) == ontology
        except TermError:
            known = False
        if known:
            return curie
        # The sentence says "an assay" and would say "a method".
        article = "an" if name[0] in "aeiou" else "a"
        self.add(
            Problem(
                name,
                value,
                detail=(
                    f"{name}: {article} {name} is a {ontology.upper()} CURIE, such "
                    f"as {example}."
                ),
            )
        )
        return None

    def scores(self, allowed) -> tuple[ScoreRange, ...] | None:
        """The ranges of the repeatable `score`, each score_type:min:max split
        from the right, since a slug holds a colon; the type one of the slugs
        `allowed` returns (called once, when the parameter is present) and
        named once; each bound empty, for an open one, or a finite decimal
        number; min at most max. `slug::` selects every assertion with a
        finite score of the type. A comma is no separator here: a value that
        holds one is malformed."""
        if "score" not in self.query:
            return None
        slugs = list(allowed())
        listed = ", ".join(slugs) or "none yet"
        ranges, named = [], set()
        for value in self.query.getlist("score"):
            if "," in value:
                self.add(Problem("score", value, detail=MALFORMED_SCORE))
                continue
            parts = value.rsplit(":", 2)
            if len(parts) != 3:
                self.add(Problem("score", value, detail=MALFORMED_SCORE))
                continue
            slug, low, high = parts
            if slug not in slugs:
                self.add(
                    Problem(
                        "score",
                        value,
                        allowed=slugs,
                        detail=f"Unknown score type {slug!r}. Allowed: {listed}.",
                    )
                )
                continue
            if slug in named:
                self.add(
                    Problem("score", value, detail="score names each score type once.")
                )
                continue
            named.add(slug)
            bounds = []
            for text in (low, high):
                if text == "":
                    bounds.append(None)
                elif not BOUND.fullmatch(text):
                    self.add(Problem("score", value, detail=MALFORMED_SCORE))
                    break
                elif not math.isfinite(float(text)):
                    self.add(
                        Problem(
                            "score", value, detail="score bounds are finite numbers."
                        )
                    )
                    break
                else:
                    bounds.append(float(text))
            if len(bounds) < 2:
                continue
            if (
                bounds[0] is not None
                and bounds[1] is not None
                and bounds[0] > bounds[1]
            ):
                self.add(
                    Problem(
                        "score", value, detail=f"score: min {low} is above max {high}."
                    )
                )
                continue
            ranges.append(ScoreRange(slug, *bounds))
        return tuple(ranges)

    def contexts(self, taxids) -> tuple[ContextMatch, ...] | None:
        """The matches of the context.<facet> parameters present, in
        documentation order; None when none is. context.host_taxon is a
        registered taxid (`taxids` as taxon() takes them). Another facet's
        value that is a CURIE after stripping matches the term; any other text
        is matched as written, at most MAX_CONTEXT_CHARS characters and with
        no control character. At most MAX_CONTEXT_FACETS facets, the problem
        attributed to the first past the cap once every value has been
        checked."""
        present = [name for name in CONTEXT_NAMES if name in self.query]
        if not present:
            return None
        matches = []
        for name in present:
            facet = name.removeprefix("context.")
            if facet == ContextFacet.HOST_TAXON:
                taxid = self.taxon(taxids, name=name)
                if taxid is not None:
                    matches.append(ContextMatch(facet, taxid=taxid))
                continue
            value = self.single(name)
            if value is None:
                continue
            if value == "":
                self.add(Problem(name, detail=f"{name} names a value."))
                continue
            try:
                curie = normalize_curie(value)
            except TermError:
                curie = None
            if curie is not None:
                matches.append(ContextMatch(facet, term=curie))
            elif len(value) > MAX_CONTEXT_CHARS:
                self.add(
                    Problem(
                        name,
                        value,
                        limit=MAX_CONTEXT_CHARS,
                        detail=f"{name} is at most {MAX_CONTEXT_CHARS} characters.",
                    )
                )
            elif any(unicodedata.category(letter) == "Cc" for letter in value):
                self.add(
                    Problem(name, value, detail=f"{name} holds a control character.")
                )
            else:
                matches.append(ContextMatch(facet, raw=value))
        if len(present) > MAX_CONTEXT_FACETS:
            name = present[MAX_CONTEXT_FACETS]
            self.add(
                Problem(
                    name,
                    self.query.get(name),
                    limit=MAX_CONTEXT_FACETS,
                    detail=f"context names at most {MAX_CONTEXT_FACETS} facets.",
                )
            )
        return tuple(matches)

    def refs(self, name: str, kind: str, limit: int) -> frozenset | None:
        """The ids of a repeatable, comma-separated list of refs; None when the
        parameter is absent."""
        if name not in self.query:
            return None
        one, several = KIND_WORDS[kind]
        pieces = [
            piece for value in self.query.getlist(name) for piece in value.split(",")
        ]
        if not any(pieces):
            self.add(Problem(name, detail=f"{name} names at least one {kind}."))
            return frozenset()
        ids = set()
        for piece in pieces:
            pk = parse_ref(piece, kind)
            if pk is None:
                self.add(
                    Problem(
                        name, piece, detail=f"{name}: {one} ref looks like {kind}:3."
                    )
                )
            else:
                ids.add(pk)
        if len(ids) > limit:
            self.add(
                Problem(
                    name,
                    limit=limit,
                    detail=f"{name} names at most {limit} {several}.",
                )
            )
        return frozenset(ids)

    def releases(self) -> frozenset | None:
        return self.refs("releases", "release", MAX_RELEASES)

    def raise_problems(self) -> None:
        if self.problems:
            raise ParameterError(self.problems, more=self.more)
