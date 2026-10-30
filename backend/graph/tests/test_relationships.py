"""Relationships: identity only, with the rules of their types kept on every
write path (architecture §3.6, §4.2). graph.claims builds the canonical
identity (canonical_relationship) and creates a batch's relationships in
canonical order (create_relationships); the model refuses whatever breaks
it."""

import dataclasses
import threading

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test.utils import CaptureQueriesContext

from graph.claims import (
    CanonicalRelationship,
    ClaimError,
    RelationshipResult,
    canonical_relationship,
    create_relationships,
    find_relationship,
)
from graph.models import (
    EntityStatus,
    ImmutableRecordError,
    Relationship,
    RelationshipType,
    ReleaseStatus,
)
from graph.tests.factories import (
    ImportRunFactory,
    RelationshipFactory,
    entity_of,
    evidence_release,
    genetic_type,
    protein_pair_type,
)
from graph.tests.test_provenance import (
    DELETE_PATHS,
    WRITE_PATHS,
    _a_backend_waits_on_a_lock,
    _race,
    advance,
    delete,
    stored,
    write,
)

INSERT_PATHS = ["save", "bulk_create", "bulk_create_ignore_conflicts"]


def kind(slug):
    return RelationshipType.objects.get(slug=slug)


def unsaved(slug, subject, obj, **fields):
    """A relationship of the type as given: no canonical order applied."""
    rel_type = kind(slug)
    fields.setdefault("ordered", rel_type.orientation != "symmetric")
    return Relationship(
        relationship_type=rel_type, subject=subject, object=obj, **fields
    )


def insert(path, relationship):
    if path == "save":
        relationship.save()
    elif path == "bulk_create":
        Relationship.objects.bulk_create([relationship])
    else:
        Relationship.objects.bulk_create([relationship], ignore_conflicts=True)


def pair(type_slug="protein"):
    """Two entities of the type, lower id first."""
    first, second = entity_of(type_slug), entity_of(type_slug)
    return first, second


# ── Identity ────────────────────────────────────────────────────────────────


def test_a_directed_claim_and_its_reverse_are_two_relationships(registry):
    a, b = pair("gene")
    forward = RelationshipFactory(
        relationship_type=kind("transcriptional_regulation"), subject=a, object=b
    )
    reverse = RelationshipFactory(
        relationship_type=kind("transcriptional_regulation"), subject=b, object=a
    )
    assert forward.pk != reverse.pk
    assert (stored(forward)["subject_id"], stored(reverse)["subject_id"]) == (
        a.pk,
        b.pk,
    )
    assert stored(forward)["ordered"] is True


def test_a_symmetric_pair_is_one_relationship_lower_id_first(commit):
    a, b = pair()
    # Stated higher id first: the canonical identity puts it in order.
    canonical = identity("physical_interaction", b, a)
    rel = create_relationships([canonical], import_run=commit)[canonical].relationship
    assert (stored(rel)["subject_id"], stored(rel)["object_id"]) == (a.pk, b.pk)
    assert stored(rel)["ordered"] is False
    # The same pair again is the same identity.
    with pytest.raises(IntegrityError), transaction.atomic():
        insert("save", unsaved("physical_interaction", a, b))
    Relationship.objects.bulk_create(
        [unsaved("physical_interaction", a, b)], ignore_conflicts=True
    )
    assert Relationship.objects.count() == 1


@pytest.mark.parametrize("path", INSERT_PATHS)
def test_a_symmetric_pair_stated_higher_id_first_is_refused_on_every_path(
    registry, path
):
    """SQLite's INSERT OR IGNORE would drop the row without a word, so the
    guard refuses it before the database sees it."""
    a, b = pair()
    with pytest.raises(ImmutableRecordError, match="lower entity id first"):
        insert(path, unsaved("physical_interaction", b, a))
    assert not Relationship.objects.exists()


def test_the_database_keeps_a_symmetric_pair_in_order(registry):
    """graph_rel_symmetric_order, reached past the guard through the base
    manager."""
    a, b = pair()
    with pytest.raises(IntegrityError, match="graph_rel_symmetric_order"):
        with transaction.atomic():
            Relationship._base_manager.bulk_create(
                [unsaved("physical_interaction", b, a)]
            )
    assert not Relationship.objects.exists()


def test_two_types_on_one_pair_are_two_relationships(registry):
    a, b = pair()
    physical = RelationshipFactory(
        relationship_type=kind("physical_interaction"), subject=a, object=b
    )
    unspecified = RelationshipFactory(
        relationship_type=kind("protein_interaction_unspecified"), subject=a, object=b
    )
    assert physical.pk != unspecified.pk
    assert Relationship.objects.filter(subject=a, object=b).count() == 2


@pytest.mark.parametrize("path", INSERT_PATHS)
def test_ordered_follows_the_types_orientation(registry, path):
    a, b = pair("gene")
    with pytest.raises(ImmutableRecordError, match="Relationship.ordered"):
        insert(path, unsaved("transcriptional_regulation", a, b, ordered=False))
    c, d = pair()
    with pytest.raises(ImmutableRecordError, match="Relationship.ordered"):
        insert(path, unsaved("physical_interaction", c, d, ordered=True))
    assert not Relationship.objects.exists()


@pytest.mark.parametrize("path", INSERT_PATHS)
def test_a_qualifier_key_waits_for_identity_qualifiers(registry, path):
    a, b = pair("gene")
    with pytest.raises(ImmutableRecordError, match="Relationship.qualifier_key"):
        insert(
            path,
            unsaved("transcriptional_regulation", a, b, qualifier_key="site:S15"),
        )
    assert not Relationship.objects.exists()


# ── Endpoint types and self-pairs ───────────────────────────────────────────

ACCEPTED = [
    ("transcriptional_regulation", "gene", "gene"),
    ("transcriptional_regulation", "protein", "gene"),
    ("physical_interaction", "protein", "protein"),
    ("physical_interaction", "protein", "protein_isoform"),
    ("physical_interaction", "protein_isoform", "protein_isoform"),
    ("protein_interaction_unspecified", "protein", "protein_isoform"),
    ("encodes", "gene", "protein"),
    ("isoform_of", "protein_isoform", "protein"),
]
REFUSED = [
    # The target of transcriptional regulation is a gene.
    ("transcriptional_regulation", "gene", "protein"),
    ("transcriptional_regulation", "protein_isoform", "gene"),
    # A gene is never a participant of a physical interaction.
    ("physical_interaction", "gene", "protein"),
    ("physical_interaction", "protein", "gene"),
    ("protein_interaction_unspecified", "gene", "gene"),
    # encodes runs from a gene to a protein entry, never the other way.
    ("encodes", "protein", "gene"),
    ("encodes", "gene", "protein_isoform"),
    ("encodes", "protein_isoform", "protein"),
    # isoform_of runs from an isoform to its entry, never the other way.
    ("isoform_of", "protein", "protein_isoform"),
    ("isoform_of", "gene", "protein"),
]


@pytest.mark.parametrize("slug,subject_type,object_type", ACCEPTED)
def test_each_type_relates_the_entity_types_it_allows(
    registry, slug, subject_type, object_type
):
    subject, obj = entity_of(subject_type), entity_of(object_type)
    rel = RelationshipFactory(relationship_type=kind(slug), subject=subject, object=obj)
    assert stored(rel)["relationship_type_id"] == slug


@pytest.mark.parametrize("path", INSERT_PATHS)
@pytest.mark.parametrize("slug,subject_type,object_type", REFUSED)
def test_an_entity_type_a_side_does_not_allow_is_refused(
    registry, slug, subject_type, object_type, path
):
    subject, obj = entity_of(subject_type), entity_of(object_type)
    if kind(slug).orientation == "symmetric" and subject.pk > obj.pk:
        subject, obj = obj, subject
    with pytest.raises(ImmutableRecordError, match=f"a {slug} (subject|object) is a"):
        insert(path, unsaved(slug, subject, obj))
    assert not Relationship.objects.exists()


@pytest.mark.parametrize(
    "slug,entity_type,allowed",
    [
        # Autoregulation, and homomers.
        ("transcriptional_regulation", "gene", True),
        ("physical_interaction", "protein", True),
        ("protein_interaction_unspecified", "protein_isoform", True),
        ("test_protein_pair", "protein", False),
        ("test_genetic", "gene", False),
    ],
)
def test_a_self_pair_only_where_the_type_allows_it(
    registry, slug, entity_type, allowed
):
    protein_pair_type()
    genetic_type()
    entity = entity_of(entity_type)
    rel = unsaved(slug, entity, entity)
    if allowed:
        rel.save()
        assert stored(rel)["subject_id"] == stored(rel)["object_id"] == entity.pk
    else:
        with pytest.raises(ImmutableRecordError, match="relates two different"):
            rel.save()
        with pytest.raises(ImmutableRecordError, match="relates two different"):
            Relationship.objects.bulk_create([unsaved(slug, entity, entity)])
        assert not Relationship.objects.exists()


def test_the_reference_types_have_no_self_pairs(registry):
    assert not kind("encodes").self_pairs_allowed
    assert not kind("isoform_of").self_pairs_allowed


@pytest.mark.parametrize("status", [EntityStatus.MERGED, EntityStatus.RETIRED])
@pytest.mark.parametrize("side", ["subject", "object"])
def test_a_merged_or_retired_entity_is_never_an_endpoint(registry, side, status):
    a, b = pair("gene")
    fields = {"status": status}
    if status == EntityStatus.MERGED:
        fields["merged_into"] = entity_of("gene")
    ended = entity_of("gene", **fields)
    ends = {"subject": a, "object": b, side: ended}
    with pytest.raises(
        ImmutableRecordError, match=f"Relationship.{side}: is an active"
    ):
        insert(
            "save",
            unsaved("transcriptional_regulation", ends["subject"], ends["object"]),
        )
    assert not Relationship.objects.exists()
    # Active and provisional entities are both endpoints.
    active = entity_of("gene", status=EntityStatus.ACTIVE)
    RelationshipFactory(
        relationship_type=kind("transcriptional_regulation"), subject=active, object=a
    )


def test_a_relationship_names_a_type_and_entities_that_exist(registry):
    a, b = pair("gene")
    rel = unsaved("transcriptional_regulation", a, b)
    rel.relationship_type_id = "no_such_type"
    with pytest.raises(ImmutableRecordError, match="names no relationship type"):
        rel.save()
    rel = unsaved("transcriptional_regulation", a, b)
    rel.object_id = 10**12
    with pytest.raises(ImmutableRecordError, match="names no entity"):
        rel.save()
    assert not Relationship.objects.exists()


# ── Never changed or deleted ────────────────────────────────────────────────


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_relationship_is_never_changed(registry, path):
    a, b = pair("gene")
    rel = RelationshipFactory(
        relationship_type=kind("transcriptional_regulation"), subject=a, object=b
    )
    before = stored(rel)
    with pytest.raises(ImmutableRecordError, match="Relationship"):
        write(path, rel, subject=b, object=a)
    assert stored(rel) == before


@pytest.mark.parametrize("path", DELETE_PATHS)
def test_a_relationship_is_never_deleted(registry, path):
    rel = RelationshipFactory()
    with pytest.raises(ImmutableRecordError, match="Relationship"):
        delete(path, rel)
    with pytest.raises(ImmutableRecordError, match="Relationship"):
        Relationship(pk=rel.pk).delete()
    assert Relationship.objects.filter(pk=rel.pk).exists()


def test_a_relationship_is_never_upserted(registry):
    rel = RelationshipFactory()
    again = Relationship(
        relationship_type=rel.relationship_type,
        subject=rel.subject,
        object=rel.object,
        ordered=True,
    )
    with pytest.raises(ImmutableRecordError, match="update_conflicts"):
        Relationship.objects.bulk_create(
            [again],
            update_conflicts=True,
            unique_fields=["relationship_type", "subject", "object", "qualifier_key"],
            update_fields=["ordered"],
        )
    assert Relationship.objects.count() == 1


def test_a_relationships_type_and_endpoints_are_kept(registry):
    rel = RelationshipFactory()
    with pytest.raises(ProtectedError):
        rel.subject.delete()
    with pytest.raises(ProtectedError):
        rel.relationship_type.delete()


# ── Rows read from the database, a fixed number of queries ─────────────────


def test_bulk_create_reads_the_same_rows_for_one_relationship_or_many(registry):
    def batch(count):
        rows = []
        for _ in range(count):
            a, b = pair()
            rows.append(unsaved("physical_interaction", a, b))
            g, h = pair("gene")
            rows.append(unsaved("transcriptional_regulation", g, h))
        return rows

    counts = []
    for rows in (batch(1), batch(5)):
        with CaptureQueriesContext(connection) as queries:
            Relationship.objects.bulk_create(rows, ignore_conflicts=True)
        counts.append(len(queries.captured_queries))
    assert counts[0] == counts[1], counts
    assert Relationship.objects.count() == 12


@pytest.mark.postgres
def test_a_relationship_holds_its_type_in_shared_mode_before_writing(registry):
    """So graph_sync_registry, which locks a type before changing it, waits
    for the write to commit and then counts it (§4.3)."""
    a, b = pair("gene")
    with CaptureQueriesContext(connection) as queries:
        RelationshipFactory(
            relationship_type=kind("transcriptional_regulation"), subject=a, object=b
        )
    sql = [query["sql"] for query in queries.captured_queries]
    shared = [
        n
        for n, text in enumerate(sql)
        if "FOR SHARE" in text and '"graph_relationshiptype"' in text
    ]
    inserting = [
        n for n, text in enumerate(sql) if 'INSERT INTO "graph_relationship"' in text
    ]
    assert shared and inserting and shared[0] < inserting[0]


# ── The canonical identity (graph.claims) ───────────────────────────────────


def identity(slug, subject, obj, **options):
    return canonical_relationship(slug, subject, obj, **options)


def test_a_directed_claim_and_its_reverse_have_two_identities(registry):
    a, b = pair("gene")
    forward = identity("transcriptional_regulation", a, b)
    reverse = identity(kind("transcriptional_regulation"), b.pk, a.pk)
    assert forward == CanonicalRelationship(
        "transcriptional_regulation", a.pk, b.pk, True, ""
    )
    assert reverse == CanonicalRelationship(
        "transcriptional_regulation", b.pk, a.pk, True, ""
    )


def test_a_symmetric_pair_has_one_identity_lower_id_first(registry):
    a, b = pair()
    assert (
        identity("physical_interaction", b, a)
        == identity("physical_interaction", a, b)
        == CanonicalRelationship("physical_interaction", a.pk, b.pk, False, "")
    )
    # Another type on the same pair is another identity.
    assert identity("protein_interaction_unspecified", b, a) != identity(
        "physical_interaction", a, b
    )


@pytest.mark.parametrize("slug,subject_type,object_type", ACCEPTED)
def test_the_identity_accepts_the_entity_types_each_side_allows(
    registry, slug, subject_type, object_type
):
    subject, obj = entity_of(subject_type), entity_of(object_type)
    canonical = identity(slug, subject, obj)
    assert canonical.type_slug == slug and canonical.qualifier_key == ""


@pytest.mark.parametrize("slug,subject_type,object_type", REFUSED)
def test_the_identity_refuses_a_side_the_type_does_not_allow_never_swapping(
    registry, slug, subject_type, object_type
):
    subject, obj = entity_of(subject_type), entity_of(object_type)
    with pytest.raises(ClaimError, match=r"Rule 2 \(endpoint types\)"):
        identity(slug, subject, obj)


@pytest.mark.parametrize(
    "slug,types,stated,accepted",
    [
        ("transcriptional_regulation", ("gene", "gene"), True, True),
        ("transcriptional_regulation", ("gene", "gene"), False, False),
        ("physical_interaction", ("protein", "protein"), False, True),
        ("physical_interaction", ("protein", "protein"), True, False),
        # Role order is not a causal direction: never checked.
        ("encodes", ("gene", "protein"), True, True),
        ("encodes", ("gene", "protein"), False, True),
        ("isoform_of", ("protein_isoform", "protein"), False, True),
    ],
)
def test_a_stated_direction_is_checked_against_the_type(
    registry, slug, types, stated, accepted
):
    subject, obj = entity_of(types[0]), entity_of(types[1])
    if accepted:
        assert identity(slug, subject, obj, stated_directed=stated).type_slug == slug
    else:
        with pytest.raises(ClaimError, match=r"Rule 3 \(stated direction\)"):
            identity(slug, subject, obj, stated_directed=stated)
    with pytest.raises(ClaimError, match="stated_directed is True, False or None"):
        identity(slug, subject, obj, stated_directed="yes")


@pytest.mark.parametrize(
    "slug,entity_type,allowed",
    [
        ("transcriptional_regulation", "gene", True),
        ("physical_interaction", "protein", True),
        ("test_protein_pair", "protein", False),
        ("test_genetic", "gene", False),
    ],
)
def test_the_identity_of_a_self_pair_only_where_allowed(
    registry, slug, entity_type, allowed
):
    protein_pair_type()
    genetic_type()
    entity = entity_of(entity_type)
    if allowed:
        assert identity(slug, entity, entity).subject_id == entity.pk
    else:
        with pytest.raises(ClaimError, match=r"Rule 4 \(self-pairs\)"):
            identity(slug, entity, entity)


@pytest.mark.parametrize("status", [EntityStatus.MERGED, EntityStatus.RETIRED])
def test_the_identity_of_a_merged_or_retired_endpoint_is_refused(registry, status):
    fields = {"status": status}
    if status == EntityStatus.MERGED:
        fields["merged_into"] = entity_of("gene")
    ended = entity_of("gene", **fields)
    with pytest.raises(ClaimError, match=f"Rule 1.*is {status}"):
        identity("transcriptional_regulation", ended, entity_of("gene"))
    with pytest.raises(ClaimError, match=f"Rule 1.*is {status}"):
        identity("transcriptional_regulation", entity_of("gene"), ended)


def test_the_identity_names_a_type_and_entities_that_exist(registry):
    a, b = pair("gene")
    for args, message in (
        (("no_such_type", a, b), "names no relationship type"),
        (("transcriptional_regulation", a, 10**12), "the object names no entity"),
        (("transcriptional_regulation", "x", b), "the subject names no entity"),
        (("transcriptional_regulation", True, b), "the subject names no entity"),
    ):
        with pytest.raises(ClaimError, match=f"Rule 1.*{message}"):
            identity(*args)


def test_the_identity_is_refused_while_the_switch_is_off(registry, settings):
    a, b = pair("gene")
    settings.TYPED_GRAPH_ENABLED = False
    with pytest.raises(ClaimError, match="TYPED_GRAPH_ENABLED"):
        identity("transcriptional_regulation", a, b)


def test_find_relationship_finds_a_stored_identity(registry):
    a, b = pair()
    canonical = identity("physical_interaction", b, a)
    assert find_relationship(canonical) is None
    stored_row = RelationshipFactory(
        relationship_type=kind("physical_interaction"), subject=a, object=b
    )
    assert find_relationship(canonical) == stored_row
    assert find_relationship(identity("protein_interaction_unspecified", a, b)) is None
    with pytest.raises(TypeError):
        find_relationship(("physical_interaction", a.pk, b.pk))


def test_find_relationship_finds_nothing_while_the_switch_is_off(registry, settings):
    rel = RelationshipFactory()
    canonical = CanonicalRelationship(
        rel.relationship_type_id, rel.subject_id, rel.object_id, True, ""
    )
    assert find_relationship(canonical) == rel
    settings.TYPED_GRAPH_ENABLED = False
    assert find_relationship(canonical) is None


# ── create_relationships (graph.claims) ─────────────────────────────────────


@pytest.fixture
def commit(registry):
    """An approved draft release's running commit run."""
    return evidence_release(records=0).run


def test_the_missing_relationships_are_created_in_canonical_order(commit):
    a, b = pair()
    g, h = pair("gene")
    canonicals = [
        identity("transcriptional_regulation", h, g),
        identity("physical_interaction", b, a),
        identity("transcriptional_regulation", g, h),
        identity("protein_interaction_unspecified", a, b),
    ]
    existing = RelationshipFactory(
        relationship_type=kind("protein_interaction_unspecified"), subject=a, object=b
    )
    created = create_relationships(reversed(canonicals), import_run=commit)
    assert set(created) == set(canonicals)
    assert created[canonicals[3]] == RelationshipResult(existing, False)
    new = sorted(
        (c for c in canonicals if c != canonicals[3]),
        key=lambda c: (c.type_slug, c.subject_id, c.object_id),
    )
    assert all(created[c].created for c in new)
    # Inserted in canonical order, whatever the order given.
    ids = [created[c].relationship.pk for c in new]
    assert ids == sorted(ids)
    for canonical in canonicals:
        row = created[canonical].relationship
        assert (row.relationship_type_id, row.subject_id, row.object_id) == (
            canonical.type_slug,
            canonical.subject_id,
            canonical.object_id,
        )
    again = create_relationships(canonicals, import_run=commit.pk)
    assert not any(result.created for result in again.values())
    assert Relationship.objects.count() == 4


@pytest.mark.parametrize(
    "change,message",
    [
        ({"subject_id": "higher"}, "is not canonical"),
        ({"ordered": True}, "is not canonical"),
        ({"qualifier_key": "site:S15"}, "is not canonical"),
        ({"type_slug": "no_such_type"}, "names no relationship type"),
        ({"object_id": 10**12}, "names no entity"),
    ],
)
def test_create_relationships_checks_each_identity_again(commit, change, message):
    a, b = pair()
    canonical = identity("physical_interaction", a, b)
    values = dataclasses.asdict(canonical)
    if change.get("subject_id") == "higher":
        values["subject_id"], values["object_id"] = b.pk, a.pk
    else:
        values.update(change)
    with pytest.raises(ClaimError, match=message):
        create_relationships([CanonicalRelationship(**values)], import_run=commit)
    assert not Relationship.objects.exists()


def test_create_relationships_keeps_the_synthetic_line(registry):
    real_run = evidence_release(records=0).run
    synthetic_run = evidence_release(records=0, synthetic=True).run
    constructs = entity_of("gene", 32630), entity_of("gene", 32630)
    humans = pair("gene")
    for run, ends in ((real_run, constructs), (synthetic_run, humans)):
        canonical = identity("transcriptional_regulation", *ends)
        with pytest.raises(ClaimError, match=r"Rule 13 \(synthetic separation\)"):
            create_relationships([canonical], import_run=run)
    assert not Relationship.objects.exists()
    canonical = identity("transcriptional_regulation", *constructs)
    created = create_relationships([canonical], import_run=synthetic_run)
    assert created[canonical].created


@pytest.mark.parametrize(
    "case",
    ["switch off", "staged", "dry run", "completed run", "unapproved", "unknown run"],
)
def test_create_relationships_needs_record_claims_preconditions(
    registry, settings, case
):
    bundle = evidence_release(records=0, approved=case != "unapproved")
    run = bundle.run
    a, b = pair()
    canonical = identity("physical_interaction", a, b)
    messages = {
        "switch off": "TYPED_GRAPH_ENABLED",
        "staged": "is staged: evidence is recorded only while its release is a draft",
        "dry run": "is a dry_run run, running: only a running commit run",
        "completed run": "is a commit run, completed: only a running commit run",
        "unapproved": "has no effective internal approval",
        "unknown run": "Unknown import run",
    }
    if case == "switch off":
        settings.TYPED_GRAPH_ENABLED = False
    elif case == "staged":
        advance(bundle.release, ReleaseStatus.STAGED)
    elif case == "dry run":
        run = ImportRunFactory(release=bundle.release, file=None, mode="dry_run")
    elif case == "completed run":
        run.state = "completed"
        run.save()
    elif case == "unknown run":
        run = 10**12
    with pytest.raises(ClaimError, match=messages[case]):
        create_relationships([canonical], import_run=run)
    assert not Relationship.objects.exists()


def test_create_relationships_takes_canonical_identities_only(commit):
    with pytest.raises(TypeError):
        create_relationships([("physical_interaction", 1, 2)], import_run=commit)
    with pytest.raises(TypeError):
        create_relationships(
            CanonicalRelationship("physical_interaction", 1, 2, False),
            import_run=commit,
        )
    assert create_relationships([], import_run=commit) == {}


def _construct_pair():
    first, second = entity_of("protein", 32630), entity_of("protein", 32630)
    return identity("physical_interaction", second, first)


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_a_batch_waits_for_another_writing_the_same_pairs_then_reuses_them(
    registry,
):
    """Two batches over the same pairs in opposite orders: the second waits on
    the first's uncommitted rows, then finds them; neither fails."""
    first_run = evidence_release(records=0, synthetic=True).run
    second_run = evidence_release(records=0, synthetic=True).run
    canonicals = [_construct_pair() for _ in range(2)]
    outcome = {}

    def first():
        outcome["first"] = create_relationships(canonicals, import_run=first_run)

    def second():
        outcome["second"] = create_relationships(
            canonicals[::-1], import_run=second_run
        )

    assert _race(first, second) == []
    assert [r.created for r in outcome["first"].values()] == [True, True]
    assert [r.created for r in outcome["second"].values()] == [False, False]
    assert Relationship.objects.count() == 2


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_batches_over_shared_pairs_in_opposite_orders_never_deadlock(registry):
    """The race made deterministic: one commit has inserted the lower of two
    pairs and not yet the higher (its canonical INSERT, paused between rows);
    a batch given [higher, lower] waits on the lower before taking the
    higher, so the first then inserts the higher and commits. Inserting in
    the order given would hold the higher and wait on the lower: a deadlock."""
    first_run, second_run = (
        evidence_release(records=0, synthetic=True).run for _ in range(2)
    )
    low, high = _construct_pair(), _construct_pair()
    holding, go = threading.Event(), threading.Event()
    errors, outcome = [], {}

    def first():
        try:
            with transaction.atomic():
                create_relationships([low], import_run=first_run)
                holding.set()
                go.wait(30)
                outcome["first"] = create_relationships([high], import_run=first_run)
        except Exception as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            connection.close()

    def second():
        try:
            holding.wait(30)
            outcome["second"] = create_relationships([high, low], import_run=second_run)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    try:
        assert holding.wait(30), "the first batch never started"
        assert _a_backend_waits_on_a_lock(), "the second batch never waited"
    finally:
        go.set()
        for thread in threads:
            thread.join(30)
    assert errors == []
    assert [r.created for r in outcome["first"].values()] == [True]
    assert [r.created for r in outcome["second"].values()] == [False, False]
    assert Relationship.objects.count() == 2
