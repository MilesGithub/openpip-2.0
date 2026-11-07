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

import re
import unicodedata
from dataclasses import dataclass, field

from .limits import MAX_RELEASES, Q_MAX_CHARS, RESOLVE_MAX_TERMS
from .refs import parse_ref

# The most problems one 400 lists: an 8 KB URL must not give a 400 KB body.
MAX_PROBLEMS = 20
# Not spaces: labels may contain them.
TERM_SEPARATORS = re.compile(r"[,\t\r\n]+")
# How the sentences of Params.refs name a kind: one of them, several of them.
KIND_WORDS = {
    "entity": ("an entity", "entities"),
    "relationship": ("a relationship", "relationships"),
    "release": ("a release", "releases"),
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

    def taxon(self, allowed) -> int | None:
        """The taxon, when it is one of the taxids (ints) `allowed` returns.
        Comparing strings refuses what int() alone would accept: +9606, 9_606,
        other scripts' digits."""
        value = self.choice("taxon", lambda: [str(pk) for pk in sorted(allowed())])
        return None if value is None else int(value)

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
