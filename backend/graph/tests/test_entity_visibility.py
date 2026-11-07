"""What graph.entity_visibility promises, which no route can test, since a
route issues its sets moments before it reads: a set not issued is refused, a
set kept too long or outliving the switch shows nothing, the predicates work
from every path, and the import graph has no cycle. Then each function's own
rules, its query count included. The routes' use of them is in test_api_resolve.
"""

import dataclasses
import subprocess
import sys
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from graph.api.shapes import entity_nodes, release_entries, release_refs
from graph.entity_visibility import (
    ROLE_ORDER,
    EntityText,
    entity_text,
    label_release_set,
    naming_releases,
    primary_identifiers,
    readable_label_q,
    relationship_counts,
    visible_entity_q,
    visible_identifiers,
    visible_reference_relationships,
)
from graph.models import (
    ApprovalScope,
    Entity,
    EntityIdentifier,
    EntityStatus,
    IdentifierNamespace,
    IdentifierRole,
    InteractionLevel,
    Outcome,
    RelationshipType,
)
from graph.releases import withdraw_release
from graph.resolution import (
    CANDIDATE,
    LABEL,
    MATCH_KINDS,
    UNMATCHED,
    resolve,
    resolve_many,
)
from graph.tests.api_helpers import hidden_release, public_release, state
from graph.tests.factories import (
    EntityIdentifierFactory,
    RelationshipFactory,
    approvable_release,
    approve,
    entity_of,
    evidence_release,
)
from graph.tests.test_provenance import PUBLISHED, SUPERSEDED, advance
from graph.visibility import Purpose, ReleaseSet, visible_release_ids

INTERNAL = ApprovalScope.INTERNAL
PUBLIC_VIEW = ApprovalScope.PUBLIC_VIEW
PRIMARY = IdentifierRole.PRIMARY


def request_for(user=None):
    request = RequestFactory().get("/")
    request.user = AnonymousUser() if user is None else user
    return request


def visitor_set(pinned=None):
    return visible_release_ids(request_for(), Purpose.VIEW, pinned)


def relationship_type(slug):
    return RelationshipType.objects.get(slug=slug)


def primary(entity, value):
    """The entity's primary identifier, an HGNC ID."""
    return EntityIdentifierFactory(
        entity=entity,
        namespace=IdentifierNamespace.objects.get(slug="hgnc"),
        value=value,
        role=PRIMARY,
    )


def public_world():
    """A published release visitors may see, that created `gene`, with text and
    a primary identifier, and states an encodes edge to `named`, which it did
    not create."""
    ev = evidence_release(records=0)
    approve(ev.release, PUBLIC_VIEW)
    gene = entity_of(
        label="WRITTEN", description="Written.", created_by_release=ev.release
    )
    primary(gene, "HGNC:901")
    named = entity_of("protein", label="NAMED")
    encodes = relationship_type("encodes")
    state(
        ev, RelationshipFactory(relationship_type=encodes, subject=gene, object=named)
    )
    advance(ev.release, PUBLISHED)
    return SimpleNamespace(release=ev.release, gene=gene, named=named)


def shown(world, releases, labels):
    """What the functions that take a set show of a world. entity_text and
    readable_label_q show nothing in ways of their own, so the tests read
    them apart."""
    gene, named = world.gene, world.named
    names = naming_releases([gene], releases)[gene.pk]
    return {
        "entities": sorted(
            Entity.objects.filter(visible_entity_q(releases)).values_list(
                "label", flat=True
            )
        ),
        "identifiers": [row.value for row in visible_identifiers(gene, releases)],
        "edges": len(visible_reference_relationships(gene, releases)),
        "counts": relationship_counts(gene, releases),
        "named by": naming_releases([named], releases)[named.pk],
        "entries": [entry["id"] for entry in release_entries(names, releases)],
        "resolves": resolve("WRITTEN", releases=releases, label_releases=labels).status,
    }


# ── A set not issued ────────────────────────────────────────────────────────

TAKES_A_SET = {
    "visible_entity_q": lambda entity, forged: visible_entity_q(forged),
    "readable_label_q": lambda entity, forged: readable_label_q(forged),
    "entity_text": lambda entity, forged: entity_text([entity], {}, forged),
    "visible_identifiers": lambda entity, forged: visible_identifiers(entity, forged),
    "visible_reference_relationships": lambda entity, forged: (
        visible_reference_relationships(entity, forged)
    ),
    "relationship_counts": lambda entity, forged: relationship_counts(entity, forged),
    "naming_releases": lambda entity, forged: naming_releases([entity], forged),
    "entity_nodes": lambda entity, forged: entity_nodes([entity], forged),
    "release_entries": lambda entity, forged: release_entries([1], forged),
    "resolve_many": lambda entity, forged: resolve_many(
        ["x"], releases=forged, label_releases=forged
    ),
}
FORGERIES = {
    "built by hand": lambda issued: ReleaseSet(frozenset(), frozenset()),
    "derived": dataclasses.replace,
}


@pytest.mark.parametrize("on", (True, False), ids=("on", "off"))
@pytest.mark.parametrize("forge", FORGERIES.values(), ids=FORGERIES)
@pytest.mark.parametrize("call", TAKES_A_SET.values(), ids=TAKES_A_SET)
def test_a_set_visible_release_ids_did_not_issue_is_refused(
    call, forge, on, db, settings
):
    settings.TYPED_GRAPH_ENABLED = on
    with pytest.raises(TypeError):
        call(Entity(pk=1), forge(visitor_set()))


def test_resolve_many_takes_both_sets_or_neither(switch_on):
    issued = visitor_set()
    for one in ({"releases": issued}, {"label_releases": issued}):
        with pytest.raises(TypeError, match="together"):
            resolve_many(["x"], **one)
        with pytest.raises(TypeError, match="together"):
            resolve("x", **one)


def test_resolve_many_refuses_either_set_alone_if_it_was_not_issued(switch_on):
    issued = visitor_set()
    forged = ReleaseSet(frozenset(), frozenset())
    with pytest.raises(TypeError):
        resolve_many(["x"], releases=issued, label_releases=forged)
    with pytest.raises(TypeError):
        resolve_many(["x"], releases=forged, label_releases=issued)
    assert resolve_many(["x"], releases=issued, label_releases=issued)[0].status == (
        UNMATCHED
    )


# ── A set that outlives what it says ────────────────────────────────────────


def test_a_set_issued_before_a_withdrawal_shows_nothing(registry, staff_user):
    world = public_world()
    request = request_for()
    issued = visible_release_ids(request, Purpose.VIEW)
    labels = label_release_set(request)
    assert issued.ids == labels.ids == {world.release.pk}
    assert shown(world, issued, labels) == {
        "entities": ["NAMED", "WRITTEN"],
        "identifiers": ["HGNC:901"],
        "edges": 1,
        "counts": {"encodes": 1},
        "named by": frozenset({world.release.pk}),
        "entries": [f"release:{world.release.pk}"],
        "resolves": CANDIDATE,
    }

    withdraw_release(world.release, reason="Test only.", withdrawn_by=staff_user)

    assert shown(world, issued, labels) == {
        "entities": [],
        "identifiers": [],
        "edges": 0,
        "counts": {},
        "named by": frozenset(),
        "entries": [],
        "resolves": UNMATCHED,
    }
    gene = world.gene
    assert not Entity.objects.filter(pk=gene.pk).filter(readable_label_q(labels))
    primaries = primary_identifiers([gene.pk])
    assert entity_text([gene], primaries, labels) == {
        gene.pk: EntityText("HGNC:901", "")
    }
    assert entity_nodes([gene], labels)[gene.pk]["label"] == "HGNC:901"


def test_a_set_issued_before_the_switch_went_off_shows_nothing(
    registry, settings, django_assert_num_queries
):
    world = public_world()
    request = request_for()
    issued = visible_release_ids(request, Purpose.VIEW)
    labels = label_release_set(request)
    assert issued.ids == labels.ids == {world.release.pk}

    settings.TYPED_GRAPH_ENABLED = False

    assert shown(world, issued, labels) == {
        "entities": [],
        "identifiers": [],
        "edges": 0,
        "counts": {},
        "named by": frozenset(),
        "entries": [],
        "resolves": UNMATCHED,
    }
    gene = world.gene
    assert not Entity.objects.filter(readable_label_q(labels))
    assert not EntityIdentifier.objects.filter(
        visible_entity_q(issued, path="entity__")
    )
    with django_assert_num_queries(0):
        assert label_release_set(request).ids == frozenset()
        assert primary_identifiers([gene.pk]) == {}
        assert entity_text([gene], {}, labels) == {}
        assert entity_nodes([gene], labels) == {}
        assert relationship_counts(gene, issued) == {}
        assert visible_identifiers(gene, issued) == []
        assert visible_reference_relationships(gene, issued) == []
        assert naming_releases([gene], issued) == {gene.pk: frozenset()}
        assert release_entries(issued.ids, issued) == []


# ── The predicates ──────────────────────────────────────────────────────────


def test_the_two_clauses_from_each_path(registry):
    public = approvable_release(status=PUBLISHED)
    approve(public, INTERNAL, PUBLIC_VIEW)
    hidden = approvable_release(status=PUBLISHED)
    created = entity_of(label="CREATED", created_by_release=public)
    named = entity_of(label="NAMED")
    partner = entity_of(label="PARTNER")
    invisible = entity_of(label="INVISIBLE", created_by_release=hidden)
    nobody = entity_of(label="NOBODY")
    public_release(named, partner)
    for number, entity in enumerate((created, named, invisible, nobody), 1):
        primary(entity, f"HGNC:{number}")
    releases = visitor_set()

    assert set(Entity.objects.filter(visible_entity_q(releases))) == {
        created,
        named,
        partner,
    }
    rows = EntityIdentifier.objects.filter(visible_entity_q(releases, path="entity__"))
    assert set(rows.values_list("entity_id", flat=True)) == {created.pk, named.pk}

    merged = {
        survivor: entity_of(
            label=f"INTO {survivor.label}",
            status=EntityStatus.MERGED,
            merged_into=survivor,
        )
        for survivor in (created, named, invisible, nobody)
    }
    assert set(
        Entity.objects.filter(status=EntityStatus.MERGED).filter(
            visible_entity_q(releases, path="merged_into__")
        )
    ) == {merged[created], merged[named]}


def test_without_the_sets_every_entity_is_considered(registry, staff_user):
    # The import pipeline's use.
    hidden = entity_of(
        label="HIDDEN", created_by_release=hidden_release("unapproved", staff_user)
    )
    primary(hidden, "HGNC:3")
    nobody = entity_of(label="NOBODY")
    assert resolve("HIDDEN").candidates[0].entity == hidden
    assert resolve("HGNC:3").candidates[0].entity == hidden
    assert resolve("NOBODY").candidates[0].entity == nobody
    request = request_for()
    sets = {
        "releases": visible_release_ids(request, Purpose.VIEW),
        "label_releases": label_release_set(request),
    }
    assert [
        resolve(term, **sets).status for term in ("HIDDEN", "HGNC:3", "NOBODY")
    ] == [UNMATCHED, UNMATCHED, UNMATCHED]


def test_the_empty_set_selects_nothing(registry):
    world = public_world()
    assert world.release.pk in visitor_set().ids
    empty = visitor_set(pinned=[])
    assert empty.ids == frozenset()
    assert not Entity.objects.filter(visible_entity_q(empty))
    # A row written by no release stays readable, whoever asks: what hides it
    # is visible_entity_q.
    assert set(Entity.objects.filter(readable_label_q(empty))) == {world.named}


def test_a_null_creator_named_by_a_visible_release_keeps_its_text(registry):
    entity = entity_of(label="NO-CREATOR", description="Stored.")
    primary(entity, "HGNC:7")
    public_release(entity, entity_of(label="PARTNER"))
    request = request_for()
    labels = label_release_set(request)
    releases = visible_release_ids(request, Purpose.VIEW)

    assert entity_text([entity], {}, labels) == {
        entity.pk: EntityText("NO-CREATOR", "Stored.")
    }
    found = resolve("no-creator", releases=releases, label_releases=labels)
    assert found.status == CANDIDATE
    assert [match.kind for match in found.candidates[0].matches] == [LABEL]


def test_readable_label_q_keeps_the_join_outer(registry):
    world = public_world()
    labels = label_release_set(request_for())
    rows = Entity.objects.filter(readable_label_q(labels))
    assert 'LEFT OUTER JOIN "graph_datasetrelease"' in str(rows.query)
    assert set(rows) == {world.gene, world.named}


def test_readable_label_q_from_each_path(registry, staff_user):
    public = approvable_release(status=PUBLISHED)
    approve(public, INTERNAL, PUBLIC_VIEW)
    readable = entity_of(label="READABLE", created_by_release=public)
    secret = entity_of(
        label="SECRET", created_by_release=hidden_release("unapproved", staff_user)
    )
    nobody = entity_of(label="NOBODY")
    for number, entity in enumerate((readable, secret, nobody), 1):
        primary(entity, f"HGNC:{number}")
    labels = label_release_set(request_for())

    assert set(Entity.objects.filter(readable_label_q(labels))) == {readable, nobody}
    rows = EntityIdentifier.objects.filter(readable_label_q(labels, path="entity__"))
    assert set(rows.values_list("entity_id", flat=True)) == {readable.pk, nobody.pk}

    merged = {
        survivor: entity_of(
            label=f"INTO {survivor.label}",
            status=EntityStatus.MERGED,
            merged_into=survivor,
            created_by_release=public,
        )
        for survivor in (readable, secret, nobody)
    }
    assert set(
        Entity.objects.filter(status=EntityStatus.MERGED).filter(
            readable_label_q(labels, path="merged_into__")
        )
    ) == {merged[readable], merged[nobody]}


# ── The orders ──────────────────────────────────────────────────────────────


def test_match_kinds_are_the_roles_then_label():
    assert MATCH_KINDS == (*ROLE_ORDER, LABEL)
    assert set(ROLE_ORDER) == set(IdentifierRole.values)
    # Not the order IdentifierRole declares.
    assert [str(role.value) for role in ROLE_ORDER] == [
        "primary",
        "secondary",
        "xref",
        "symbol",
        "alias",
    ]


def test_identifiers_are_listed_by_role_then_namespace_then_value(test_namespaces):
    public = approvable_release(status=PUBLISHED)
    approve(public, INTERNAL, PUBLIC_VIEW)
    entity = entity_of(
        "gene", test_namespaces.taxon_a, label="E", created_by_release=public
    )
    rows = (
        (test_namespaces.global_alias, "zeta", IdentifierRole.ALIAS),
        (test_namespaces.symbols_a, "beta", IdentifierRole.ALIAS),
        (test_namespaces.global_alias, "alpha", IdentifierRole.ALIAS),
        (test_namespaces.symbols_a, "sym", IdentifierRole.SYMBOL),
        (test_namespaces.symbols_a, "ref", IdentifierRole.XREF),
        (test_namespaces.exact_a, "old", IdentifierRole.SECONDARY),
        (test_namespaces.exact_a, "dl", PRIMARY),
    )
    for namespace, value, role in rows:
        EntityIdentifierFactory(
            entity=entity, namespace=namespace, value=value, role=role
        )
    assert [
        (row.role, row.namespace_id, row.value)
        for row in visible_identifiers(entity, visitor_set())
    ] == [
        ("primary", "test_exact_a", "dl"),
        ("secondary", "test_exact_a", "old"),
        ("xref", "test_symbols_a", "ref"),
        ("symbol", "test_symbols_a", "sym"),
        ("alias", "test_global_alias", "alpha"),
        ("alias", "test_global_alias", "zeta"),
        ("alias", "test_symbols_a", "beta"),
    ]


# ── Entity text ─────────────────────────────────────────────────────────────


def test_a_hidden_creators_text_is_replaced_never_blanked(registry, staff_user):
    hidden = hidden_release("unapproved", staff_user)
    with_primary = entity_of(
        label="SECRET", description="Secret.", created_by_release=hidden
    )
    primary(with_primary, "HGNC:5")
    without = entity_of(
        label="SECRET 2", description="Secret 2.", created_by_release=hidden
    )
    labels = label_release_set(request_for())
    primaries = primary_identifiers([with_primary.pk, without.pk])
    assert entity_text([with_primary, without], primaries, labels) == {
        with_primary.pk: EntityText("HGNC:5", ""),
        without.pk: EntityText(f"entity:{without.pk}", ""),
    }


def test_the_label_set_is_every_release_the_caller_may_see(
    registry, staff_user, django_assert_num_queries
):
    published = approvable_release(status=PUBLISHED)
    superseded = approvable_release(status=SUPERSEDED)
    internal = approvable_release(status=PUBLISHED)
    approve(published, INTERNAL, PUBLIC_VIEW)
    approve(superseded, INTERNAL, PUBLIC_VIEW)
    approve(internal, INTERNAL)
    unapproved = approvable_release(status=PUBLISHED)
    with django_assert_num_queries(2):
        labels = label_release_set(request_for())
    assert labels.ids == {published.pk, superseded.pk}
    staff = label_release_set(request_for(staff_user))
    assert staff.ids == {published.pk, superseded.pk, internal.pk}
    assert unapproved.pk not in staff.ids


def test_a_label_of_a_superseded_release_reads_under_any_selection(registry):
    superseded = approvable_release(status=SUPERSEDED)
    approve(superseded, INTERNAL, PUBLIC_VIEW)
    entity = entity_of(label="FIRST", created_by_release=superseded)
    public_release(entity, entity_of(label="PARTNER"))
    # The default set leaves the superseded release out; its text stays.
    labels = label_release_set(request_for())
    assert superseded.pk not in visitor_set().ids
    assert entity_text([entity], {}, labels)[entity.pk].label == "FIRST"


# ── Relationships and the releases that name an entity ──────────────────────


def test_relationship_counts_count_relationships_with_supporting_evidence(registry):
    ev = evidence_release(records=0)
    approve(ev.release, PUBLIC_VIEW)
    a, b, c = (entity_of(label=label) for label in "ABC")
    regulation = relationship_type("transcriptional_regulation")
    forward = RelationshipFactory(relationship_type=regulation, subject=a, object=b)
    # One relationship, however many assertions state it.
    state(ev, forward)
    state(ev, forward)
    # The opposite direction is a second relationship, and a self-loop one.
    state(ev, RelationshipFactory(relationship_type=regulation, subject=b, object=a))
    state(ev, RelationshipFactory(relationship_type=regulation, subject=a, object=a))
    # Evidence against an edge does not count it.
    state(
        ev,
        RelationshipFactory(relationship_type=regulation, subject=a, object=c),
        outcome=Outcome.TESTED_ABSENT,
    )
    p, q = (entity_of("protein", label=label) for label in "PQ")
    physical = relationship_type("physical_interaction")
    state(
        ev,
        RelationshipFactory(relationship_type=physical, subject=q, object=p),
        interaction_level=InteractionLevel.NOT_STATED,
    )
    advance(ev.release, PUBLISHED)
    releases = visitor_set()
    assert relationship_counts(a, releases) == {"transcriptional_regulation": 3}
    assert relationship_counts(c, releases) == {}
    # A symmetric pair is one, from either end.
    assert relationship_counts(p, releases) == {"physical_interaction": 1}
    assert relationship_counts(q, releases) == {"physical_interaction": 1}


def test_a_hidden_release_adds_no_relationship(registry):
    gene = entity_of(label="GENE")
    public_release(gene, entity_of(label="PARTNER"))
    hidden = evidence_release(records=0, approved=False)
    state(hidden, RelationshipFactory(subject=gene, object=entity_of(label="OTHER")))
    protein = entity_of("protein", label="ENTRY")
    encodes = relationship_type("encodes")
    state(
        hidden,
        RelationshipFactory(relationship_type=encodes, subject=gene, object=protein),
    )
    advance(hidden.release, PUBLISHED)
    releases = visitor_set()
    assert list(relationship_counts(gene, releases)) == ["test_regulation"]
    assert visible_reference_relationships(gene, releases) == []
    assert naming_releases([gene], releases)[gene.pk] <= releases.ids


def test_reference_relationships_are_the_shown_ones_by_id(registry):
    ev = evidence_release(records=0)
    approve(ev.release, PUBLIC_VIEW)
    gene = entity_of(label="GENE")
    encodes = relationship_type("encodes")
    first, second, absent = (
        RelationshipFactory(
            relationship_type=encodes,
            subject=gene,
            object=entity_of("protein", label=label),
        )
        for label in ("P1", "P2", "P3")
    )
    state(ev, second)
    state(ev, first)
    state(ev, absent, outcome=Outcome.TESTED_ABSENT)
    # A regulation edge is no reference relationship.
    state(ev, RelationshipFactory(subject=gene, object=entity_of(label="OTHER")))
    advance(ev.release, PUBLISHED)
    edges = visible_reference_relationships(gene, visitor_set())
    assert [edge.relationship_id for edge in edges] == [first.pk, second.pk]
    assert {edge.relationship_type for edge in edges} == {"encodes"}


def test_naming_releases_has_a_key_for_every_entity_given(
    registry, django_assert_num_queries
):
    first = public_world()
    other = entity_of(label="NOBODY")
    releases = visitor_set()
    with django_assert_num_queries(1):
        named = naming_releases([first.gene, first.named, other], releases)
    assert named == {
        first.gene.pk: frozenset({first.release.pk}),
        first.named.pk: frozenset({first.release.pk}),
        other.pk: frozenset(),
    }
    assert naming_releases([], releases) == {}


def test_naming_releases_gathers_creator_and_namers_for_many_entities(registry):
    created = approvable_release(status=PUBLISHED)
    approve(created, INTERNAL, PUBLIC_VIEW)
    entities = [
        entity_of(label=f"E{index}", created_by_release=created) for index in range(3)
    ]
    first = public_release(*entities[:2])
    second = public_release(*entities[1:])
    named = naming_releases(entities, visitor_set())
    assert named == {
        entities[0].pk: {created.pk, first.pk},
        entities[1].pk: {created.pk, first.pk, second.pk},
        entities[2].pk: {created.pk, second.pk},
    }


def test_naming_releases_leaves_out_a_creator_outside_the_set(registry):
    entity = entity_of(
        label="CREATED", created_by_release=approvable_release(status=PUBLISHED)
    )
    shown = public_release(entity, entity_of(label="PARTNER"))
    assert naming_releases([entity], visitor_set())[entity.pk] == {shown.pk}


def test_release_entries_lists_only_what_the_set_holds(registry):
    shown = public_world().release
    outside = approvable_release(status=PUBLISHED)
    entries = release_entries([outside.pk, shown.pk, 10**30], visitor_set())
    assert [entry["id"] for entry in entries] == [f"release:{shown.pk}"]


def test_release_refs_are_those_of_the_entries_by_id():
    # Sorted as ids: as strings, release:10 would come first.
    entries = [{"id": "release:9"}, {"id": "release:10"}]
    assert release_refs([11, 10, 9], entries) == ["release:9", "release:10"]


def test_the_query_counts(registry, django_assert_num_queries):
    world = public_world()
    gene = world.gene
    request = request_for()
    releases = visible_release_ids(request, Purpose.VIEW)
    labels = label_release_set(request)
    with django_assert_num_queries(1):
        primaries = primary_identifiers([gene.pk, world.named.pk])
    assert primaries == {gene.pk: ("hgnc", "HGNC:901")}
    with django_assert_num_queries(1):
        entity_text([gene, world.named], primaries, labels)
    with django_assert_num_queries(2):
        entity_nodes([gene, world.named], labels)
    with django_assert_num_queries(1):
        visible_identifiers(gene, releases)
    with django_assert_num_queries(4):
        visible_reference_relationships(gene, releases)
    with django_assert_num_queries(1):
        relationship_counts(gene, releases)
    with django_assert_num_queries(3):
        release_entries(releases.ids, releases)


# ── The import graph ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "module",
    (
        "graph.resolution",
        "graph.entity_visibility",
        "graph.ingest.mapping",
        "graph.ingest.resolutions",
        "graph.api.views",
    ),
)
def test_each_module_can_be_imported_first(module):
    # graph.resolution imports graph.entity_visibility, so the opposite import
    # would be a cycle, and one that stops the server. Only a fresh process
    # shows it: here every module has been imported already.
    code = f"import django; django.setup(); import {module}"
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=django_settings.BASE_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
