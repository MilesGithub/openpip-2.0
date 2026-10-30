"""Evidence assertions and their parts keep their rules on every write path,
whichever code writes them: graph.claims is the writer, and these rules are
what it cannot get past."""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError, RestrictedError
from django.test.utils import CaptureQueriesContext

from graph.models import (
    AssertionParticipant,
    ContextFacet,
    DatasetRelease,
    Effect,
    EvidenceAssertion,
    EvidenceContext,
    EvidenceMethod,
    EvidencePublication,
    EvidenceScore,
    GeneticSign,
    ImmutableRecordError,
    ImportMode,
    ImportRun,
    ImportState,
    InteractionLevel,
    MethodRole,
    Outcome,
    ParticipantSide,
    Publication,
    RecordStatus,
    Relationship,
    RelationshipType,
    ResolutionBasis,
    SourceRecord,
)
from graph.tests.factories import (
    FAILED_CRITERION,
    AssertionParticipantFactory,
    DatasetReleaseFactory,
    EntityFactory,
    EvidenceAssertionFactory,
    EvidenceContextFactory,
    EvidenceMethodFactory,
    EvidencePublicationFactory,
    EvidenceScoreFactory,
    ImportRunFactory,
    PublicationFactory,
    RelationshipFactory,
    ScoreTypeFactory,
    TermFactory,
    commit_run,
    entity_of,
    genetic_type,
    mapped_record,
    protein_pair_type,
    signed_score_type,
    taxon_of,
)
from graph.tests.test_provenance import (
    DELETE_PATHS,
    DRAFT,
    PUBLISHED,
    STAGED,
    SUPERSEDED,
    WITHDRAWN,
    WRITE_PATHS,
    advance,
    delete,
    stored,
    write,
)

PAST_DRAFT = [STAGED, PUBLISHED, SUPERSEDED, WITHDRAWN]
PART_FACTORIES = {
    AssertionParticipant: AssertionParticipantFactory,
    EvidenceMethod: EvidenceMethodFactory,
    EvidencePublication: EvidencePublicationFactory,
    EvidenceScore: EvidenceScoreFactory,
    EvidenceContext: EvidenceContextFactory,
}
PART_MODELS = list(PART_FACTORIES)
PART_IDS = [model.__name__ for model in PART_MODELS]


def build_part(model, assertion, **fields):
    """An unsaved part of the assertion; the rows it points at are stored."""
    if model is EvidenceMethod:
        fields.setdefault("term", TermFactory(curie="MI:0018", label="two hybrid"))
    elif model is EvidencePublication:
        fields.setdefault("publication", PublicationFactory())
    elif model is EvidenceScore:
        fields.setdefault("score_type", ScoreTypeFactory(slug="stats:test_value"))
    return PART_FACTORIES[model].build(assertion=assertion, **fields)


def insert(path, obj):
    if path == "save":
        obj.save()
    else:
        type(obj).objects.bulk_create([obj])


def relationship(slug, subject, obj):
    return RelationshipFactory(
        relationship_type=RelationshipType.objects.get(slug=slug),
        subject=subject,
        object=obj,
    )


def proteins(count=2, taxid=9606):
    return [entity_of("protein", taxid) for _ in range(count)]


def prepared(release=None):
    """A draft release's running commit run and a mapped record of it, made
    while it is a draft."""
    release = release or DatasetReleaseFactory()
    run = commit_run(release)
    return release, run, mapped_record(release, run)


# ── Writing an assertion ────────────────────────────────────────────────────


def test_an_assertion_is_written_from_a_mapped_record_by_a_running_commit_run(db):
    assertion = EvidenceAssertionFactory(effect=Effect.ACTIVATING)
    row = stored(assertion)
    assert (row["release_id"], row["outcome"], row["effect"]) == (
        assertion.source_record.release_id,
        Outcome.SUPPORTED,
        Effect.ACTIVATING,
    )
    assert list(assertion.relationship.assertions.all()) == [assertion]
    assert row["qualifiers"] == {}


@pytest.mark.parametrize("status", PAST_DRAFT)
def test_an_assertion_is_written_only_while_its_release_is_a_draft(db, status):
    """The draft rule is the only one the row breaks (bulk_create() is
    refused for every release: test_assertions_are_never_inserted_in_bulk)."""
    release, run, record = prepared()
    advance(release, status)
    assertion = EvidenceAssertionFactory.build(
        relationship=RelationshipFactory(),
        release=release,
        import_run=run,
        source_record=record,
    )
    with pytest.raises(
        ImmutableRecordError,
        match="EvidenceAssertion.release: evidence is written only while its "
        "release is a draft",
    ):
        assertion.save()
    assert not EvidenceAssertion.objects.exists()


def test_assertions_are_never_inserted_in_bulk(db):
    """record_claims writes them one by one; bulk_create() is the parts'."""
    release, run, record = prepared()
    assertion = EvidenceAssertionFactory.build(
        release=release, import_run=run, source_record=record
    )
    with pytest.raises(ImmutableRecordError, match="bulk_create"):
        EvidenceAssertion.objects.bulk_create([assertion])
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.parametrize("foreign", ["source_record", "import_run"])
def test_the_record_and_the_run_belong_to_the_assertions_release(db, foreign):
    release, run, record = prepared()
    other, other_run, other_record = prepared()
    own = {"source_record": record, "import_run": run}
    own[foreign] = {"source_record": other_record, "import_run": other_run}[foreign]
    with pytest.raises(
        ImmutableRecordError, match=f"EvidenceAssertion.{foreign}: belongs to"
    ):
        EvidenceAssertionFactory(release=release, **own)
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.parametrize(
    "mode,state",
    [
        (ImportMode.DRY_RUN, ImportState.RUNNING),
        (ImportMode.COMMIT, ImportState.FAILED),
        (ImportMode.COMMIT, ImportState.COMPLETED),
        (ImportMode.COMMIT, ImportState.ABANDONED),
    ],
)
def test_only_a_running_commit_run_writes_evidence(db, mode, state):
    release = DatasetReleaseFactory()
    run = ImportRunFactory(release=release, file=None, mode=mode, state=state)
    record = mapped_record(release, run)
    with pytest.raises(ImmutableRecordError, match="a running commit run"):
        EvidenceAssertionFactory(release=release, import_run=run, source_record=record)
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.parametrize(
    "status",
    [
        RecordStatus.UNRESOLVED,
        RecordStatus.AMBIGUOUS,
        RecordStatus.REJECTED,
        RecordStatus.SKIPPED,
    ],
)
def test_evidence_is_recorded_only_from_a_mapped_record(db, status):
    release, run, _ = prepared()
    record = mapped_record(release, run, status=status)
    with pytest.raises(ImmutableRecordError, match=f"record {record.pk} is {status}"):
        EvidenceAssertionFactory(release=release, import_run=run, source_record=record)
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.parametrize(
    "attname,value",
    [
        ("relationship_id", 10**12),
        ("import_run_id", 10**12),
        ("source_record_id", 10**12),
        ("eco_term_id", "ECO:0000001"),
    ],
)
def test_an_assertion_names_rows_that_exist(db, attname, value):
    release, run, record = prepared()
    assertion = EvidenceAssertionFactory.build(
        relationship=RelationshipFactory(),
        release=release,
        import_run=run,
        source_record=record,
    )
    setattr(assertion, attname, value)
    with pytest.raises(
        ImmutableRecordError, match=f"{attname.removesuffix('_id')}: names no"
    ):
        assertion.save()
    assert not EvidenceAssertion.objects.exists()


# ── Never saved again; deleted only with a draft ────────────────────────────


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_stored_assertion_is_never_saved_again(db, path):
    assertion = EvidenceAssertionFactory(effect=Effect.ACTIVATING)
    before = stored(assertion)
    with pytest.raises(ImmutableRecordError, match="EvidenceAssertion"):
        write(path, assertion, effect=Effect.INHIBITING)
    assert stored(assertion) == before


def test_a_stored_assertion_is_not_saved_again_even_unchanged(db):
    assertion = EvidenceAssertionFactory()
    with pytest.raises(ImmutableRecordError, match="changed or not"):
        EvidenceAssertion.objects.get(pk=assertion.pk).save()
    with pytest.raises(ImmutableRecordError, match="changed or not"):
        assertion.save(update_fields=["outcome"])


@pytest.mark.parametrize("status", [DRAFT, *PAST_DRAFT])
@pytest.mark.parametrize("path", DELETE_PATHS)
def test_an_assertion_is_deleted_only_while_its_release_is_a_draft(db, path, status):
    assertion = EvidenceAssertionFactory()
    EvidenceMethodFactory(assertion=assertion)
    advance(assertion.release, status)
    if status == DRAFT:
        delete(path, assertion)
        assert not EvidenceAssertion.objects.exists()
        # Its parts go with it.
        assert not EvidenceMethod.objects.exists()
    else:
        with pytest.raises(ImmutableRecordError, match="EvidenceAssertion.release"):
            delete(path, assertion)
        assert EvidenceAssertion.objects.filter(pk=assertion.pk).exists()
        assert EvidenceMethod.objects.filter(assertion=assertion).exists()


def test_a_draft_releases_deletion_takes_its_evidence_and_keeps_relationships(db):
    assertion = EvidenceAssertionFactory()
    for model in PART_MODELS:
        if model is AssertionParticipant:
            AssertionParticipantFactory(assertion=assertion)
        else:
            PART_FACTORIES[model](assertion=assertion)
    relationship_id = assertion.relationship_id
    DatasetRelease.objects.get(pk=assertion.release_id).delete()
    for model in (EvidenceAssertion, *PART_MODELS, SourceRecord, ImportRun):
        assert not model.objects.exists(), model.__name__
    # A relationship is identity only: it stays, with no evidence.
    assert Relationship.objects.filter(pk=relationship_id).exists()


def test_a_record_or_run_with_evidence_is_never_deleted_alone(db):
    assertion = EvidenceAssertionFactory()
    with pytest.raises(RestrictedError):
        SourceRecord.objects.get(pk=assertion.source_record_id).delete()
    with pytest.raises(RestrictedError):
        ImportRun.objects.filter(pk=assertion.import_run_id).delete()
    assert EvidenceAssertion.objects.filter(pk=assertion.pk).exists()


def test_a_relationship_with_evidence_and_its_endpoints_are_kept(db):
    assertion = EvidenceAssertionFactory()
    with pytest.raises(ProtectedError):
        assertion.relationship.subject.delete()
    with pytest.raises(ImmutableRecordError, match="Relationship"):
        assertion.relationship.delete()
    assert Relationship.objects.filter(pk=assertion.relationship_id).exists()


# ── The rules that compare an assertion with its relationship's type ──────


def test_an_effect_is_carried_only_by_effect_types(registry):
    a, b = proteins()
    physical = relationship("physical_interaction", a, b)
    with pytest.raises(ImmutableRecordError, match="EvidenceAssertion.effect: only"):
        EvidenceAssertionFactory(
            relationship=physical,
            effect=Effect.ACTIVATING,
            interaction_level=InteractionLevel.NOT_STATED,
        )
    gene = entity_of("gene")
    encodes = relationship("encodes", gene, a)
    with pytest.raises(ImmutableRecordError, match="effect polarity"):
        EvidenceAssertionFactory(relationship=encodes, effect=Effect.UNKNOWN)
    assert not EvidenceAssertion.objects.exists()


def test_a_sign_is_carried_only_by_genetic_sign_types(registry):
    regulation = RelationshipFactory()
    with pytest.raises(ImmutableRecordError, match="genetic_sign: only"):
        EvidenceAssertionFactory(
            relationship=regulation, genetic_sign=GeneticSign.NEGATIVE
        )
    genes = sorted((entity_of("gene"), entity_of("gene")), key=lambda e: e.pk)
    genetic = RelationshipFactory(
        relationship_type=genetic_type(), subject=genes[0], object=genes[1]
    )
    assertion = EvidenceAssertionFactory(
        relationship=genetic, genetic_sign=GeneticSign.NEGATIVE
    )
    assert stored(assertion)["genetic_sign"] == GeneticSign.NEGATIVE
    with pytest.raises(ImmutableRecordError, match="EvidenceAssertion.effect: only"):
        EvidenceAssertionFactory(relationship=genetic, effect=Effect.INHIBITING)


@pytest.mark.parametrize(
    "effect,message",
    [
        ("negative", "'negative' is ambiguous as an effect"),
        ("Negative", "'negative' is ambiguous as an effect"),
        (Effect.NOT_APPLICABLE, "never stored"),
        ("repressing", "is activating, inhibiting, unknown, or NULL"),
        ("", "is activating, inhibiting, unknown, or NULL"),
    ],
)
def test_an_effect_outside_what_a_source_states_is_refused(db, effect, message):
    with pytest.raises(ImmutableRecordError, match=message):
        EvidenceAssertionFactory(effect=effect)
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.parametrize(
    "outcome", [Outcome.TESTED_ABSENT, Outcome.INCONCLUSIVE, Outcome.NOT_SIGNIFICANT]
)
def test_an_assertion_that_is_not_supported_carries_no_effect_and_no_sign(db, outcome):
    with pytest.raises(ImmutableRecordError, match=f"this one is {outcome}"):
        EvidenceAssertionFactory(outcome=outcome, effect=Effect.ACTIVATING)
    genes = sorted((entity_of("gene"), entity_of("gene")), key=lambda e: e.pk)
    genetic = RelationshipFactory(
        relationship_type=genetic_type(), subject=genes[0], object=genes[1]
    )
    with pytest.raises(ImmutableRecordError, match=f"this one is {outcome}"):
        EvidenceAssertionFactory(
            relationship=genetic, outcome=outcome, genetic_sign=GeneticSign.POSITIVE
        )
    assert not EvidenceAssertion.objects.exists()
    # Bare, it is kept (a not-significant one with the criterion it failed):
    # a tested-absent result is evidence too.
    assert stored(EvidenceAssertionFactory(outcome=outcome))["outcome"] == outcome


@pytest.mark.parametrize(
    "outcome,qualifiers,message",
    [
        (Outcome.SUPPORTED, FAILED_CRITERION, "is refused on a supported assertion"),
        (
            Outcome.TESTED_ABSENT,
            FAILED_CRITERION,
            "is refused on a tested_absent assertion",
        ),
        (
            Outcome.INCONCLUSIVE,
            FAILED_CRITERION,
            "is refused on a inconclusive assertion",
        ),
        (Outcome.NOT_SIGNIFICANT, {}, "records the significance criterion"),
        (Outcome.NOT_SIGNIFICANT, {"screen": "SGA"}, "records the significance"),
        (Outcome.NOT_SIGNIFICANT, {"threshold": []}, "a non-empty list of criteria"),
        (Outcome.NOT_SIGNIFICANT, {"threshold": "p < 0.05"}, "a non-empty list"),
    ],
)
def test_only_a_not_significant_assertion_states_the_criterion_it_failed(
    db, outcome, qualifiers, message
):
    """Rule 8 on the row alone; whether the criteria name its scores and one
    fails is record_claims' to check."""
    with pytest.raises(
        ImmutableRecordError, match=f"EvidenceAssertion.qualifiers: .*{message}"
    ):
        EvidenceAssertionFactory(outcome=outcome, qualifiers=qualifiers)
    assert not EvidenceAssertion.objects.exists()
    kept = EvidenceAssertionFactory(outcome=Outcome.NOT_SIGNIFICANT)
    assert stored(kept)["qualifiers"] == FAILED_CRITERION


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "negative"),
        ("evidence_basis", "observed"),
        ("assertion_method", "curated"),
        ("genetic_sign", "aggravating"),
        ("interaction_level", "binding"),
    ],
)
def test_every_closed_field_is_checked_before_the_database(db, field, value):
    with pytest.raises(ImmutableRecordError, match=f"EvidenceAssertion.{field}"):
        EvidenceAssertionFactory(**{field: value})
    assert not EvidenceAssertion.objects.exists()


def test_the_database_keeps_effect_and_sign_apart(db):
    """graph_assert_effect_xor_sign, reached past the guard through the base
    manager's plain update."""
    assertion = EvidenceAssertionFactory(effect=Effect.ACTIVATING)
    with pytest.raises(IntegrityError, match="graph_assert_effect_xor_sign"):
        with transaction.atomic():
            EvidenceAssertion._base_manager.filter(pk=assertion.pk).update(
                genetic_sign=GeneticSign.NEGATIVE
            )
    assert stored(assertion)["genetic_sign"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"effect": Effect.ACTIVATING},
        {"effect": Effect.UNKNOWN},
        {"genetic_sign": GeneticSign.NEGATIVE},
    ],
)
@pytest.mark.parametrize(
    "outcome", [Outcome.TESTED_ABSENT, Outcome.INCONCLUSIVE, Outcome.NOT_SIGNIFICANT]
)
def test_the_database_keeps_unsupported_assertions_bare(db, outcome, change):
    """graph_assert_bare_unless_sup, reached past the guard through the base
    manager's plain update; not_applicable is the one effect it allows."""
    assertion = EvidenceAssertionFactory(outcome=outcome)
    with pytest.raises(IntegrityError, match="graph_assert_bare_unless_sup"):
        with transaction.atomic():
            EvidenceAssertion._base_manager.filter(pk=assertion.pk).update(**change)
    EvidenceAssertion._base_manager.filter(pk=assertion.pk).update(
        effect=Effect.NOT_APPLICABLE
    )
    assert stored(assertion)["effect"] == Effect.NOT_APPLICABLE


# ── Levels and terms ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "curie,level",
    [
        ("MI:0914", InteractionLevel.ASSOCIATION),
        ("MI:0915", InteractionLevel.PHYSICAL_ASSOCIATION),
        ("MI:0407", InteractionLevel.DIRECT_INTERACTION),
        ("MI:0195", InteractionLevel.DIRECT_INTERACTION),
    ],
)
def test_a_stated_interaction_type_sets_its_crosswalk_level(registry, curie, level):
    a, b = proteins()
    physical = relationship("physical_interaction", a, b)
    term = TermFactory(curie=curie)
    assertion = EvidenceAssertionFactory(
        relationship=physical, interaction_type=term, interaction_level=level
    )
    assert stored(assertion)["interaction_level"] == level
    for other in set(InteractionLevel.values) - {level}:
        with pytest.raises(ImmutableRecordError, match="crosswalk's level"):
            EvidenceAssertionFactory(
                relationship=physical, interaction_type=term, interaction_level=other
            )


@pytest.mark.parametrize(
    "curie",
    [
        "MI:0403",
        "MI:2364",
        "MI:0208",
        "MI:2402",
        "MI:2373",
        "MI:2286",
        "MI:1110",
        "MI:2232",
        "MI:0218",
        "MI:1126",
    ],
)
def test_a_psi_mi_type_outside_the_crosswalk_is_never_physical_evidence(
    registry, curie
):
    a, b = proteins()
    physical = relationship("physical_interaction", a, b)
    with pytest.raises(ImmutableRecordError, match="not in the PSI-MI physical"):
        EvidenceAssertionFactory(
            relationship=physical,
            interaction_type=TermFactory(curie=curie),
            interaction_level=InteractionLevel.ASSOCIATION,
        )
    assert not EvidenceAssertion.objects.exists()


def test_a_level_above_association_needs_a_stated_type(registry):
    a, b = proteins()
    physical = relationship("physical_interaction", a, b)
    for level in (
        InteractionLevel.PHYSICAL_ASSOCIATION,
        InteractionLevel.DIRECT_INTERACTION,
    ):
        with pytest.raises(ImmutableRecordError, match="rests on the interaction"):
            EvidenceAssertionFactory(relationship=physical, interaction_level=level)
    with pytest.raises(ImmutableRecordError, match="is stated on physical_inter"):
        EvidenceAssertionFactory(relationship=physical)
    # Association without a type is openPIP's assignment (B14), and not
    # stated is not stated.
    for level in (InteractionLevel.ASSOCIATION, InteractionLevel.NOT_STATED):
        EvidenceAssertionFactory(relationship=physical, interaction_level=level)
    assert physical.assertions.count() == 2


def test_the_unspecified_type_states_no_interaction_type(registry):
    a, b = proteins()
    unspecified = relationship("protein_interaction_unspecified", a, b)
    EvidenceAssertionFactory(
        relationship=unspecified, interaction_level=InteractionLevel.NOT_STATED
    )
    with pytest.raises(ImmutableRecordError, match="states no interaction type"):
        EvidenceAssertionFactory(
            relationship=unspecified, interaction_level=InteractionLevel.ASSOCIATION
        )
    with pytest.raises(ImmutableRecordError, match="states no interaction type"):
        EvidenceAssertionFactory(
            relationship=unspecified,
            interaction_type=TermFactory(curie="MI:0915"),
            interaction_level=InteractionLevel.PHYSICAL_ASSOCIATION,
        )
    assert unspecified.assertions.count() == 1


def test_a_level_belongs_to_physical_evidence_only(registry):
    regulation = RelationshipFactory()
    with pytest.raises(ImmutableRecordError, match="physical-family evidence only"):
        EvidenceAssertionFactory(
            relationship=regulation, interaction_level=InteractionLevel.NOT_STATED
        )
    with pytest.raises(ImmutableRecordError, match="interaction_type: belongs to"):
        EvidenceAssertionFactory(
            relationship=regulation, interaction_type=TermFactory(curie="MI:0915")
        )
    assert not EvidenceAssertion.objects.exists()


def test_terms_come_from_their_ontologies(registry):
    a, b = proteins()
    physical = relationship("physical_interaction", a, b)
    with pytest.raises(ImmutableRecordError, match="eco_term: is a eco term"):
        EvidenceAssertionFactory(eco_term=TermFactory(curie="MI:0018"))
    with pytest.raises(ImmutableRecordError, match="interaction_type: is a psi-mi"):
        EvidenceAssertionFactory(
            relationship=physical,
            interaction_type=TermFactory(curie="ECO:0000353"),
            interaction_level=InteractionLevel.ASSOCIATION,
        )
    assertion = EvidenceAssertionFactory(eco_term=TermFactory(curie="ECO:0000353"))
    assert stored(assertion)["eco_term_id"] == "ECO:0000353"


# ── Synthetic separation, text and qualifiers ───────────────────────────────


def test_a_synthetic_releases_evidence_joins_synthetic_constructs_only(db):
    synthetic = DatasetReleaseFactory(is_synthetic=True)
    human = RelationshipFactory()
    with pytest.raises(ImmutableRecordError, match="joins synthetic constructs"):
        EvidenceAssertionFactory(relationship=human, release=synthetic)
    constructs = RelationshipFactory(
        subject=entity_of("gene", 32630), object=entity_of("gene", 32630)
    )
    EvidenceAssertionFactory(relationship=constructs, release=synthetic)
    # And a real release never names one, even on one side.
    mixed = RelationshipFactory(
        subject=entity_of("gene", 9606), object=entity_of("gene", 32630)
    )
    for release in (DatasetReleaseFactory(), synthetic):
        with pytest.raises(ImmutableRecordError, match="synthetic"):
            EvidenceAssertionFactory(relationship=mixed, release=release)
    assert EvidenceAssertion.objects.count() == 1


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("assertion_key", "", "never blank"),
        ("assertion_key", "k" * 401, "at most 400"),
        ("assertion_key", "main\x00", "NUL"),
        ("relationship_as_stated", "x" * 10_001, "at most 10000"),
        ("effect_as_stated", "up\x00", "NUL"),
        ("evidence_group", "g" * 256, "at most 255"),
        ("evidence_group", None, "is text"),
    ],
)
def test_text_is_kept_within_its_limits(db, field, value, message):
    with pytest.raises(ImmutableRecordError, match=f"{field}: .*{message}"):
        EvidenceAssertionFactory(**{field: value})
    assert not EvidenceAssertion.objects.exists()


@pytest.mark.parametrize(
    "qualifiers,message",
    [
        ([], "are a JSON object"),
        ({"alleles": ("a1", "a2")}, "hold a tuple"),
        ({"score": float("nan")}, "JSON cannot hold"),
        ({"score": float("inf")}, "JSON cannot hold"),
        ({1: "x"}, "key that is not a string"),
        ({"a\x00": "x"}, "key with a NUL"),
        ({"a": ["ok", "b\x00"]}, "NUL character"),
        ({"text": "x" * 10_001}, "at most 10000"),
        ({"when": {1, 2}}, "hold a set"),
    ],
)
def test_qualifiers_are_stored_as_json_gives_them_back(db, qualifiers, message):
    with pytest.raises(ImmutableRecordError, match=message):
        EvidenceAssertionFactory(qualifiers=qualifiers)
    assert not EvidenceAssertion.objects.exists()


def test_json_native_qualifiers_are_stored_exactly(db):
    qualifiers = {"expansion": "spoke", "n": 1, "x": 1.5, "flag": True, "none": None}
    qualifiers["alleles"] = ["a1", {"b": [2]}]
    assertion = EvidenceAssertionFactory(qualifiers=qualifiers)
    assert stored(assertion)["qualifiers"] == qualifiers


# ── Parts: inserted while a draft, never saved again ────────────────────────


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("status", PAST_DRAFT)
@pytest.mark.parametrize("model", PART_MODELS, ids=PART_IDS)
def test_a_part_is_inserted_only_while_its_release_is_a_draft(db, model, status, path):
    assertion = EvidenceAssertionFactory()
    part = build_part(model, assertion)
    advance(assertion.release, status)
    with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.release"):
        insert(path, part)
    assert not model.objects.exists()


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize("model", PART_MODELS, ids=PART_IDS)
def test_a_part_belongs_to_its_assertions_release(db, model, path):
    assertion = EvidenceAssertionFactory()
    part = build_part(model, assertion, release=DatasetReleaseFactory())
    with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.assertion"):
        insert(path, part)
    assert not model.objects.exists()


PART_CHANGES = {
    AssertionParticipant: {"identifier_as_stated": "changed"},
    EvidenceMethod: {"role": MethodRole.PARTICIPANT_IDENTIFICATION},
    EvidencePublication: {"role": "cited"},
    EvidenceScore: {"raw_value": "0.6"},
    EvidenceContext: {"label": "changed"},
}


@pytest.mark.parametrize("path", WRITE_PATHS)
@pytest.mark.parametrize("model", PART_MODELS, ids=PART_IDS)
def test_a_part_is_never_saved_again(db, model, path):
    part = build_part(model, EvidenceAssertionFactory())
    part.save()
    before = stored(part)
    with pytest.raises(ImmutableRecordError, match=model.__name__):
        write(path, part, **PART_CHANGES[model])
    assert stored(part) == before
    with pytest.raises(ImmutableRecordError, match="never saved again"):
        model.objects.get(pk=part.pk).save()


@pytest.mark.parametrize("status", [DRAFT, *PAST_DRAFT])
@pytest.mark.parametrize("path", DELETE_PATHS)
@pytest.mark.parametrize("model", PART_MODELS, ids=PART_IDS)
def test_a_part_is_deleted_only_while_its_release_is_a_draft(db, model, path, status):
    part = build_part(model, EvidenceAssertionFactory())
    part.save()
    advance(part.release, status)
    if status == DRAFT:
        delete(path, part)
        assert not model.objects.exists()
    else:
        with pytest.raises(ImmutableRecordError, match=f"{model.__name__}.release"):
            delete(path, part)
        assert model.objects.filter(pk=part.pk).exists()


def _many_parts(model):
    """Several valid rows of one kind, across assertions where one assertion
    holds only so many."""
    if model is AssertionParticipant:
        rows = []
        for _ in range(3):
            assertion = EvidenceAssertionFactory()
            rows.append(build_part(model, assertion))
            rows.append(
                build_part(
                    model,
                    assertion,
                    side=ParticipantSide.OBJECT,
                    entity=assertion.relationship.object,
                )
            )
        return rows
    assertion = EvidenceAssertionFactory()
    if model is EvidenceMethod:
        return [build_part(model, assertion, term=TermFactory()) for _ in range(5)]
    if model is EvidencePublication:
        return [build_part(model, assertion)] + [
            build_part(model, assertion, role="cited") for _ in range(4)
        ]
    if model is EvidenceScore:
        return [
            build_part(model, assertion, score_type=ScoreTypeFactory())
            for _ in range(5)
        ]
    return [build_part(model, assertion) for _ in range(5)]


@pytest.mark.parametrize("model", PART_MODELS, ids=PART_IDS)
def test_a_parts_bulk_create_runs_as_many_queries_for_many_rows_as_for_one(db, model):
    one = [build_part(model, EvidenceAssertionFactory())]
    many = _many_parts(model)
    counts = []
    for rows in (one, many):
        with CaptureQueriesContext(connection) as queries:
            model.objects.bulk_create(rows)
        counts.append(len(queries.captured_queries))
    assert counts[0] == counts[1], counts
    assert model.objects.count() == len(one) + len(many)


# ── Participants ────────────────────────────────────────────────────────────


def _pair(slug="physical_interaction"):
    """A symmetric relationship between two proteins, and an assertion of it."""
    a, b = proteins()
    rel = relationship(slug, a, b)
    return rel, EvidenceAssertionFactory(
        relationship=rel, interaction_level=InteractionLevel.NOT_STATED
    )


def test_a_symmetric_claim_keeps_the_order_its_source_stated(registry):
    rel, assertion = _pair()
    # Stated higher id first: the relationship holds the canonical order,
    # the participants the source's.
    AssertionParticipant.objects.bulk_create(
        [
            build_part(
                AssertionParticipant,
                assertion,
                side=ParticipantSide.SUBJECT,
                entity=rel.object,
            ),
            build_part(
                AssertionParticipant,
                assertion,
                side=ParticipantSide.OBJECT,
                entity=rel.subject,
            ),
        ]
    )
    sides = dict(assertion.participants.values_list("side", "entity_id"))
    assert sides == {"subject": rel.object_id, "object": rel.subject_id}
    assert rel.subject_id < rel.object_id


@pytest.mark.parametrize("path", ["bulk_create", "save"])
def test_two_participants_naming_one_endpoint_of_a_pair_are_refused(registry, path):
    rel, assertion = _pair()
    rows = [
        build_part(AssertionParticipant, assertion, side=side, entity=rel.subject)
        for side in (ParticipantSide.SUBJECT, ParticipantSide.OBJECT)
    ]
    with pytest.raises(ImmutableRecordError, match="as a multiset"):
        if path == "bulk_create":
            AssertionParticipant.objects.bulk_create(rows)
        else:
            # The second is compared with the first, read back.
            for row in rows:
                row.save()
    assert assertion.participants.count() == (1 if path == "save" else 0)


def test_a_participant_is_an_endpoint_of_its_relationship(registry):
    rel, assertion = _pair()
    stranger = entity_of("protein")
    with pytest.raises(ImmutableRecordError, match="one of the relationship's"):
        build_part(AssertionParticipant, assertion, entity=stranger).save()
    # A self-pair: both sides name the one endpoint.
    (protein,) = proteins(1)
    homomer = relationship("physical_interaction", protein, protein)
    own = EvidenceAssertionFactory(
        relationship=homomer, interaction_level=InteractionLevel.NOT_STATED
    )
    AssertionParticipant.objects.bulk_create(
        [
            build_part(AssertionParticipant, own, side=side, entity=protein)
            for side in (ParticipantSide.SUBJECT, ParticipantSide.OBJECT)
        ]
    )
    assert own.participants.count() == 2


def test_an_ordered_claims_sides_are_its_endpoints(db):
    assertion = EvidenceAssertionFactory()
    rel = assertion.relationship
    with pytest.raises(ImmutableRecordError, match="ordered relationship"):
        build_part(
            AssertionParticipant,
            assertion,
            side=ParticipantSide.SUBJECT,
            entity=rel.object,
        ).save()
    build_part(
        AssertionParticipant, assertion, side=ParticipantSide.OBJECT, entity=rel.object
    ).save()
    assert assertion.participants.count() == 1


def test_a_participant_is_written_as_the_source_stated_it(db):
    assertion = EvidenceAssertionFactory()
    for value, message in (("", "never blank"), ("x" * 256, "at most 255")):
        with pytest.raises(ImmutableRecordError, match=message):
            build_part(
                AssertionParticipant, assertion, identifier_as_stated=value
            ).save()
    with pytest.raises(ImmutableRecordError, match="resolution_basis: is one of"):
        build_part(AssertionParticipant, assertion, resolution_basis="guessed").save()
    assert not AssertionParticipant.objects.exists()


def test_an_isoform_entry_maps_a_named_isoform_to_its_entry(registry):
    entry, partner = proteins()
    isoform = entity_of("protein_isoform")
    rel = relationship("physical_interaction", entry, partner)
    assertion = EvidenceAssertionFactory(
        relationship=rel, interaction_level=InteractionLevel.NOT_STATED
    )
    with pytest.raises(ImmutableRecordError, match="exactly when"):
        build_part(
            AssertionParticipant,
            assertion,
            entity=entry,
            resolution_basis=ResolutionBasis.ISOFORM_ENTRY,
        ).save()
    with pytest.raises(ImmutableRecordError, match="stated_entity: of an isoform"):
        build_part(
            AssertionParticipant,
            assertion,
            entity=entry,
            stated_entity=partner,
            resolution_basis=ResolutionBasis.ISOFORM_ENTRY,
        ).save()
    part = build_part(
        AssertionParticipant,
        assertion,
        side=(
            ParticipantSide.SUBJECT if entry == rel.subject else ParticipantSide.OBJECT
        ),
        entity=entry,
        stated_entity=isoform,
        resolution_basis=ResolutionBasis.ISOFORM_ENTRY,
        identifier_as_stated="P04637-2",
    )
    part.save()
    assert stored(part)["stated_entity_id"] == isoform.pk


def test_the_database_ties_a_stated_isoform_to_its_basis(db):
    """graph_part_isoform_stated, reached past the guard."""
    part = AssertionParticipantFactory()
    with pytest.raises(IntegrityError, match="graph_part_isoform_stated"):
        with transaction.atomic():
            AssertionParticipant._base_manager.filter(pk=part.pk).update(
                resolution_basis=ResolutionBasis.ISOFORM_ENTRY
            )


# ── Methods, publications and contexts ──────────────────────────────────────


@pytest.mark.parametrize(
    "role,curie,ok",
    [
        (MethodRole.DETECTION, "MI:0018", True),
        (MethodRole.PARTICIPANT_IDENTIFICATION, "MI:0018", True),
        (MethodRole.EVIDENCE_CODE, "ECO:0000353", True),
        (MethodRole.DETECTION, "ECO:0000353", False),
        (MethodRole.PARTICIPANT_IDENTIFICATION, "ECO:0000353", False),
        (MethodRole.EVIDENCE_CODE, "MI:0018", False),
    ],
)
def test_a_methods_term_comes_from_its_roles_ontology(db, role, curie, ok):
    part = build_part(
        EvidenceMethod,
        EvidenceAssertionFactory(),
        role=role,
        term=TermFactory(curie=curie),
    )
    if ok:
        EvidenceMethod.objects.bulk_create([part])
        assert EvidenceMethod.objects.count() == 1
    else:
        with pytest.raises(ImmutableRecordError, match="EvidenceMethod.term"):
            EvidenceMethod.objects.bulk_create([part])
        assert not EvidenceMethod.objects.exists()


def _synthetic_paper(release):
    paper = Publication(
        local_dataset=release.dataset, local_key="synthetic-1", is_synthetic=True
    )
    paper.save()
    return paper


def test_a_releases_evidence_cites_papers_on_its_side_of_the_synthetic_line(db):
    synthetic = DatasetReleaseFactory(is_synthetic=True)
    constructs = RelationshipFactory(
        subject=entity_of("gene", 32630), object=entity_of("gene", 32630)
    )
    synthetic_assertion = EvidenceAssertionFactory(
        relationship=constructs, release=synthetic
    )
    real = EvidenceAssertionFactory()
    synthetic_paper, real_paper = _synthetic_paper(synthetic), PublicationFactory()
    for assertion, paper in (
        (synthetic_assertion, real_paper),
        (real, synthetic_paper),
    ):
        with pytest.raises(ImmutableRecordError, match="synthetic"):
            build_part(EvidencePublication, assertion, publication=paper).save()
    build_part(
        EvidencePublication, synthetic_assertion, publication=synthetic_paper
    ).save()
    build_part(EvidencePublication, real, publication=real_paper).save()
    assert EvidencePublication.objects.count() == 2


def test_a_local_publication_is_cited_by_its_own_datasets_releases(db):
    assertion = EvidenceAssertionFactory()
    elsewhere = Publication(
        local_dataset=DatasetReleaseFactory().dataset, local_key="p1"
    )
    elsewhere.save()
    with pytest.raises(ImmutableRecordError, match="local to dataset"):
        build_part(EvidencePublication, assertion, publication=elsewhere).save()
    own = Publication(local_dataset=assertion.release.dataset, local_key="p1")
    own.save()
    build_part(EvidencePublication, assertion, publication=own).save()
    assert assertion.publications.count() == 1


def test_one_paper_reports_an_assertion(db):
    assertion = EvidenceAssertionFactory()
    EvidencePublicationFactory(assertion=assertion)
    # graph_evpub_one_reported_in; SQLite names the column, not the index.
    with pytest.raises(IntegrityError), transaction.atomic():
        EvidencePublicationFactory(assertion=assertion)
    EvidencePublicationFactory(assertion=assertion, role="cited")
    assert assertion.publications.count() == 2


def test_a_host_taxon_is_on_its_releases_side_of_the_synthetic_line(db):
    synthetic = DatasetReleaseFactory(is_synthetic=True)
    constructs = RelationshipFactory(
        subject=entity_of("gene", 32630), object=entity_of("gene", 32630)
    )
    synthetic_assertion = EvidenceAssertionFactory(
        relationship=constructs, release=synthetic
    )
    real = EvidenceAssertionFactory()
    human, construct = taxon_of(9606), taxon_of(32630)
    host = ContextFacet.HOST_TAXON
    for assertion, taxon in ((synthetic_assertion, human), (real, construct)):
        with pytest.raises(ImmutableRecordError, match="EvidenceContext.taxon"):
            build_part(
                EvidenceContext, assertion, facet=host, taxon=taxon, raw_value="x"
            ).save()
    # A synthetic release's host is the construct, or unregistered.
    EvidenceContext.objects.bulk_create(
        [
            build_part(
                EvidenceContext,
                synthetic_assertion,
                facet=host,
                taxon=construct,
                raw_value="32630",
            ),
            build_part(
                EvidenceContext, synthetic_assertion, facet=host, raw_value="-1"
            ),
            build_part(
                EvidenceContext, real, facet=host, taxon=human, raw_value="9606"
            ),
        ]
    )
    assert EvidenceContext.objects.count() == 3


def test_a_context_names_a_taxon_only_as_its_host(db):
    assertion = EvidenceAssertionFactory()
    with pytest.raises(ImmutableRecordError, match="host_taxon only"):
        build_part(EvidenceContext, assertion, taxon=taxon_of(9606)).save()
    with pytest.raises(ImmutableRecordError, match="never a term"):
        build_part(
            EvidenceContext,
            assertion,
            facet=ContextFacet.HOST_TAXON,
            term=TermFactory(curie="MI:0018"),
        ).save()
    with pytest.raises(ImmutableRecordError, match="raw_value: is never blank"):
        build_part(EvidenceContext, assertion, raw_value=" ").save()
    assert not EvidenceContext.objects.exists()
    # The database refuses a taxon on another facet too.
    context = EvidenceContextFactory(assertion=assertion)
    with pytest.raises(IntegrityError, match="graph_context_taxon_host"):
        with transaction.atomic():
            EvidenceContext._base_manager.filter(pk=context.pk).update(taxon_id=9606)


def test_a_genetic_assertion_carries_its_signed_score(db):
    """The model stores what graph.claims checked: a sign beside a signed
    score whose raw value keeps the measured sign."""
    genes = sorted((entity_of("gene"), entity_of("gene")), key=lambda e: e.pk)
    genetic = RelationshipFactory(
        relationship_type=genetic_type(), subject=genes[0], object=genes[1]
    )
    assertion = EvidenceAssertionFactory(
        relationship=genetic, genetic_sign=GeneticSign.NEGATIVE
    )
    EvidenceScoreFactory(
        assertion=assertion,
        score_type=signed_score_type(),
        raw_value="-0.31",
        value=-0.31,
    )
    assert list(assertion.scores.values_list("raw_value", "value")) == [
        ("-0.31", -0.31)
    ]


def test_the_test_only_types_exercise_what_the_registry_has_none_of(db):
    assert genetic_type().polarity == "genetic_sign"
    pair = protein_pair_type()
    assert (pair.orientation, pair.self_pairs_allowed) == ("symmetric", False)
    assert set(pair.allowed_subject_types.values_list("slug", flat=True)) == {"protein"}
    assert EntityFactory().entity_type_id == "gene"
