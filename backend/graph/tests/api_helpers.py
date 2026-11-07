"""What the tests of /api/graph/v1/ share. pytest does not collect this file:
its name does not start with test_.
"""

import datetime
import re
from urllib.parse import urlencode

from django.urls import URLResolver, get_resolver
from django.utils import timezone

from graph.models import ApprovalScope, ParticipantSide, RelationshipType
from graph.releases import discard_release, withdraw_release
from graph.tests.factories import (
    AssertionParticipantFactory,
    EvidenceAssertionFactory,
    RelationshipFactory,
    approvable_release,
    approve,
    evidence_release,
    mapped_record,
)
from graph.tests.test_provenance import PUBLISHED, STAGED, advance

BASE = "/api/graph/v1/"
STATUS = f"{BASE}status"
VOCABULARIES = f"{BASE}vocabularies"
RESOLVE = f"{BASE}resolve"
ENTITY = f"{BASE}entities/{{entity_id}}"
# Every path a test names by hand. typed_routes() finds what is served.
PATHS = {STATUS, VOCABULARIES, RESOLVE, ENTITY}

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def resolve_url(*terms, **parameters) -> str:
    """The resolve request for these terms, as one value of q."""
    return f"{RESOLVE}?{urlencode({'q': ','.join(terms), **parameters})}"


def entity_url(entity, **parameters) -> str:
    """The page of this row."""
    url = f"{BASE}entities/entity:{entity.pk}"
    return f"{url}?{urlencode(parameters)}" if parameters else url


# Each served path, with the requests that reach it: a builder takes the
# test's data (a SimpleNamespace, unused by routes that read no row) and
# returns the URL. test_api_visibility asserts the keys are the served paths,
# so a route with no sample fails there.
SAMPLES = {
    STATUS: [lambda world: STATUS],
    VOCABULARIES: [lambda world: VOCABULARIES],
    RESOLVE: [
        lambda world: resolve_url(
            world.h.label, world.h_primary, world.h_alias, world.s.label
        ),
        lambda world: resolve_url(world.v.label),
        lambda world: resolve_url(world.s_primary),
    ],
    ENTITY: [
        lambda world: entity_url(world.h),
        lambda world: entity_url(world.s),
        lambda world: entity_url(world.v),
    ],
}
# The query a served path needs to answer at all, so that a test adding one
# parameter tests only that.
REQUIRED = {RESOLVE: "q=x"}


def typed_routes():
    """(method, path, view class) of every route served under BASE, a path
    parameter written {name}, as the docs contract writes it. Its
    _served_endpoints returns no view class, so this is a walk of its own."""

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                yield prefix + str(pattern.pattern), pattern.callback

    for raw, callback in walk(get_resolver().url_patterns):
        path = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", raw)
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", path)
        path = "/" + path.replace("^", "").replace("$", "")
        if path.startswith(BASE):
            view = callback.view_class
            for method in HTTP_METHODS:
                if hasattr(view, method):
                    yield method.upper(), path, view


def reachable(path) -> str:
    """A served path with a well-formed ref for each path parameter:
    {entity_id} as entity:1."""
    return re.sub(r"\{([a-z]+)_\w+\}", r"\1:1", path)


def walk_json(value, path=()):
    """Every string in a parsed body, object keys included, with the path to
    it as a tuple of keys and indexes."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield (*path, key), key
            yield from walk_json(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_json(item, (*path, index))


def without_ids(text) -> str:
    """The text with every kind:N as kind:#k, k counting that kind's distinct
    ids in order of first appearance, as a page's ids are those of one
    portal."""
    numbers = {}

    def numbered(match):
        kind, pk = match.groups()
        seen = numbers.setdefault(kind, {})
        return f"{kind}:#{seen.setdefault(pk, len(seen) + 1)}"

    return re.sub(r"\b(entity|relationship|release):(\d+)", numbered, text)


def pin(*releases) -> dict:
    """The query that selects these releases."""
    return {"releases": ",".join(f"release:{release.pk}" for release in releases)}


def published(*scopes, **fields):
    """A published release holding these approvals: none, it is unapproved."""
    release = approvable_release(status=PUBLISHED, **fields)
    approve(release, *scopes)
    return release


def public(**fields):
    return published(ApprovalScope.INTERNAL, ApprovalScope.PUBLIC_VIEW, **fields)


def link(slug, subject, object_):
    """A relationship of this registry type."""
    return RelationshipFactory(
        relationship_type=RelationshipType.objects.get(slug=slug),
        subject=subject,
        object=object_,
    )


def ids(entries) -> list:
    """The ids of a body's nodes or release entries."""
    return [entry["id"] for entry in entries]


# ── Hidden releases ─────────────────────────────────────────────────────────
# Each kind's state is the only thing hiding it from a visitor. So the
# unapproved release is published, and every other holds internal and
# public_view: a draft is the kind its status alone hides, and proves less.

HIDDEN_KINDS = ("unapproved", "withdrawn", "embargoed", "draft", "staged", "discarded")


def state(ev, relationship, **fields):
    """An assertion of a release that is still a draft, with its participants,
    which the assertion's factory does not write. `ev` is evidence_release's."""
    made = EvidenceAssertionFactory(
        relationship=relationship,
        release=ev.release,
        import_run=ev.run,
        source_record=mapped_record(ev.release, ev.run),
        **fields,
    )
    AssertionParticipantFactory(assertion=made, side=ParticipantSide.SUBJECT)
    AssertionParticipantFactory(assertion=made, side=ParticipantSide.OBJECT)
    return made


def hidden_evidence(kind):
    """A draft of this kind, ready for the evidence a test states with state().
    hide() then hides it."""
    if kind == "unapproved":
        return evidence_release(records=0, approved=False)
    fields = {}
    if kind == "embargoed":
        fields["embargo_until"] = timezone.now() + datetime.timedelta(days=30)
    ev = evidence_release(records=0, **fields)
    approve(ev.release, ApprovalScope.PUBLIC_VIEW)
    return ev


def hide(ev, kind, staff):
    """Moves a release of hidden_evidence to its kind's state. A discarded one
    is a draft again, its assertions gone; what it created stays. `staff` is
    the account that withdraws or discards, and needs the switch on."""
    if kind in ("unapproved", "embargoed", "withdrawn"):
        advance(ev.release, PUBLISHED)
    elif kind in ("staged", "discarded"):
        advance(ev.release, STAGED)
    if kind == "withdrawn":
        withdraw_release(ev.release, reason="Test only.", withdrawn_by=staff)
    elif kind == "discarded":
        discard_release(ev.release, reason="Test only.", discarded_by=staff)
    ev.release.refresh_from_db()
    return ev.release


def hidden_release(kind, staff):
    """A release of this kind, for a test that needs only what it created. The
    discarded one first states an assertion, which the discard deletes."""
    ev = hidden_evidence(kind)
    if kind == "discarded":
        state(ev, RelationshipFactory())
    return hide(ev, kind, staff)


def public_release(*entities):
    """A published release with public_view that names these genes: it
    states a relationship between each one and the next."""
    ev = evidence_release(records=0)
    approve(ev.release, ApprovalScope.PUBLIC_VIEW)
    for subject, object_ in zip(entities, entities[1:]):
        state(ev, RelationshipFactory(subject=subject, object=object_))
    advance(ev.release, PUBLISHED)
    return ev.release
