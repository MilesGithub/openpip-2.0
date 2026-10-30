"""Typed scores: the strict grammar raw values are read by (graph.scores), the
values each score type allows, the checks that keep NaN and infinity out of
the database, the registry's score-type rules, and what a claim's scores
become when graph.claims records them."""

import dataclasses
import math
import re
import sys

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from graph.claims import Claim, ClaimError, Endpoint, ScoreValue, record_claims
from graph.models import (
    EvidenceAssertion,
    EvidenceScore,
    ImmutableRecordError,
    Relationship,
    ScoreType,
)
from graph.registry import load_registry, validate
from graph.scores import parse_score, raw_value_problem, score_value_problem
from graph.tests.factories import (
    DatasetReleaseFactory,
    EvidenceAssertionFactory,
    EvidenceScoreFactory,
    RelationshipFactory,
    ScoreTypeFactory,
    entity_of,
    evidence_release,
    p_value_score_type,
    signed_score_type,
)

# ── The grammar ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,value",
    [
        ("0.5", 0.5),
        ("+1", 1.0),
        ("-2.5e-3", -0.0025),
        (".5", 0.5),
        ("5.", 5.0),
        ("1E3", 1000.0),
        (" 0.25\t", 0.25),
        ("0", 0.0),
        ("-0.0e5", -0.0),
        ("1.7976931348623157e308", sys.float_info.max),
    ],
)
def test_a_number_in_the_grammar_is_read_as_its_value(raw, value):
    parsed = parse_score(raw)
    assert (parsed.value, parsed.note) == (value, "")


@pytest.mark.parametrize(
    "raw",
    [
        "NaN",
        "nan",
        "-NaN",
        "inf",
        "-Inf",
        "+infinity",
        "Infinity",
        "∞",
        "1e400",
        "-1e400",
        "<2.2e-16",
        "< 2.2e-16",
        "<=0.05",
        ">1",
        ">= 3",
        # Digits of other scripts, which float() reads.
        "٣",
        "３.５",
        "0x1p3",
        "1_000",
        "1,5",
        "5%",
        "e5",
        ".",
        "abc",
    ],
)
def test_anything_else_keeps_its_raw_value_with_a_null_value_and_a_note(raw):
    parsed = parse_score(raw)
    assert parsed.value is None
    assert repr(raw) in parsed.note


@pytest.mark.parametrize(
    "raw,why",
    [
        ("nan", "not a finite number"),
        ("-Infinity", "not a finite number"),
        ("1e400", "overflows"),
        ("<2.2e-16", "censored"),
        (">= 3", "censored"),
        ("٣", "not a number openPIP reads"),
    ],
)
def test_the_note_says_why_the_value_is_null(raw, why):
    assert why in parse_score(raw).note


@pytest.mark.parametrize("raw", ["1e-400", "-1e-400", "5e-324", "1e-310"])
def test_a_nonzero_value_that_underflows_keeps_its_value_with_a_note(raw):
    parsed = parse_score(raw)
    assert parsed.value is not None and abs(parsed.value) < sys.float_info.min
    assert "underflows" in parsed.note
    # A stated zero is no underflow.
    assert parse_score("0e-400").note == ""


@pytest.mark.parametrize(
    "raw,problem",
    [
        ("", "never blank"),
        ("  ", "never blank"),
        ("0.5\x00", "NUL"),
        ("1" * 256, "at most 255"),
        (0.5, "is text"),
        (None, "is text"),
    ],
)
def test_a_raw_value_is_a_short_non_blank_text(raw, problem):
    assert problem in raw_value_problem(raw)
    assert raw_value_problem("1" * 255) is None


# ── Kinds and ranges ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind,good,bad",
    [
        ("probability", [0.0, 0.5, 1.0], [-0.1, 1.5]),
        ("p_value", [0.0, 1e-300, 1.0], [-1e-9, 1.01]),
        ("q_value", [0.05], [2.0]),
        ("rank", [1.0, 2.0, 1e6], [0.0, 1.5, -1.0]),
        ("count", [0.0, 3.0], [-1.0, 2.5]),
        ("continuous", [-1e300, 0.0, 1e300], []),
    ],
)
def test_a_value_suits_its_kind(kind, good, bad):
    for value in good:
        assert score_value_problem(kind, None, None, value) is None, value
    for value in bad:
        assert score_value_problem(kind, None, None, value), value


def test_a_value_lies_within_its_types_range():
    assert score_value_problem("continuous", -1.0, 1.0, 0.0) is None
    assert "at least -1.0" in score_value_problem("continuous", -1.0, 1.0, -1.5)
    assert "at most 1.0" in score_value_problem("continuous", -1.0, 1.0, 1.5)
    assert "at most 10.0" in score_value_problem("rank", 1.0, 10.0, 11.0)


# ── EvidenceScore ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["save", "bulk_create"])
@pytest.mark.parametrize(
    "kind,value",
    [
        ("probability", 1.5),
        ("p_value", -0.01),
        ("rank", 0.0),
        ("rank", 2.5),
        ("count", -1.0),
    ],
)
def test_a_score_outside_its_kind_is_refused_on_every_path(db, path, kind, value):
    score_type = ScoreTypeFactory(value_kind=kind)
    score = EvidenceScoreFactory.build(
        assertion=EvidenceAssertionFactory(),
        score_type=score_type,
        value=value,
        raw_value=str(value),
    )
    with pytest.raises(ImmutableRecordError, match="EvidenceScore.value"):
        if path == "save":
            score.save()
        else:
            EvidenceScore.objects.bulk_create([score])
    assert not EvidenceScore.objects.exists()


def test_a_score_lies_within_its_types_range(db):
    bounded = ScoreTypeFactory(range_min=-1.0, range_max=1.0)
    assertion = EvidenceAssertionFactory()
    with pytest.raises(ImmutableRecordError, match="at most 1.0"):
        EvidenceScoreFactory(
            assertion=assertion, score_type=bounded, value=1.5, raw_value="1.5"
        )
    EvidenceScoreFactory(
        assertion=assertion, score_type=bounded, value=1.0, raw_value="1"
    )
    assert assertion.scores.count() == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "0.5", 10**400])
def test_a_stored_value_is_a_finite_number_or_null(db, value):
    with pytest.raises(ImmutableRecordError, match="EvidenceScore.value: is a finite"):
        EvidenceScoreFactory(value=value, raw_value="x")
    # NULL, with the raw value kept, is how an unreadable value is stored.
    score = EvidenceScoreFactory(value=None, raw_value="NaN")
    assert (score.value, score.raw_value) == (None, "NaN")


@pytest.mark.parametrize("raw", ["", " ", "a\x00", "1" * 256])
def test_a_score_keeps_a_short_non_blank_raw_value(db, raw):
    with pytest.raises(ImmutableRecordError, match="EvidenceScore.raw_value"):
        EvidenceScoreFactory(raw_value=raw, value=None)
    assert not EvidenceScore.objects.exists()


def test_one_score_per_type_on_an_assertion(db):
    score = EvidenceScoreFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        EvidenceScoreFactory(assertion=score.assertion, score_type=score.score_type)
    EvidenceScoreFactory(
        assertion=score.assertion,
        score_type=p_value_score_type(),
        value=0.01,
        raw_value="0.01",
    )
    assert score.assertion.scores.count() == 2


@pytest.mark.postgres
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_the_database_keeps_nan_and_infinity_out(db, value):
    """graph_score_value_finite, reached past the guard through the base
    manager's plain update."""
    score = EvidenceScoreFactory()
    with pytest.raises(IntegrityError, match="graph_score_value_finite"):
        with transaction.atomic():
            EvidenceScore._base_manager.filter(pk=score.pk).update(value=value)
    EvidenceScore._base_manager.filter(pk=score.pk).update(value=sys.float_info.max)
    assert EvidenceScore.objects.get(pk=score.pk).value == sys.float_info.max


@pytest.mark.postgres
@pytest.mark.parametrize("field", ["range_min", "range_max"])
def test_the_database_keeps_a_score_types_range_finite(db, field):
    score_type = ScoreTypeFactory()
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(IntegrityError, match=f"graph_scoretype_{field[6:]}_finite"):
            with transaction.atomic():
                ScoreType.objects.filter(pk=score_type.pk).update(**{field: value})


def test_the_database_keeps_a_score_types_range_ordered(db):
    score_type = ScoreTypeFactory()
    with pytest.raises(IntegrityError, match="graph_scoretype_range"):
        with transaction.atomic():
            ScoreType.objects.filter(pk=score_type.pk).update(range_min=2, range_max=1)


def test_a_synthetic_release_scores_with_synthetic_and_stats_types_only(db):
    synthetic = DatasetReleaseFactory(is_synthetic=True)
    constructs = RelationshipFactory(
        subject=entity_of("gene", 32630), object=entity_of("gene", 32630)
    )
    assertion = EvidenceAssertionFactory(relationship=constructs, release=synthetic)
    real = EvidenceAssertionFactory()
    source = ScoreTypeFactory(slug="lab:confidence")
    synthetic_type = ScoreTypeFactory(slug="synthetic:test_confidence")
    for on, score_type in ((assertion, source), (real, synthetic_type)):
        with pytest.raises(ImmutableRecordError, match="EvidenceScore.score_type"):
            EvidenceScoreFactory(assertion=on, score_type=score_type)
    for on, score_type in (
        (assertion, synthetic_type),
        (assertion, signed_score_type()),
        (real, source),
        (real, ScoreTypeFactory(slug="stats:test_other")),
    ):
        EvidenceScoreFactory(
            assertion=on, score_type=score_type, value=0.1, raw_value="0.1"
        )
    assert EvidenceScore.objects.count() == 4


@pytest.mark.postgres
def test_a_score_holds_its_type_in_shared_mode_before_writing(db):
    assertion = EvidenceAssertionFactory()
    score_type = ScoreTypeFactory()
    with CaptureQueriesContext(connection) as queries:
        EvidenceScore.objects.bulk_create(
            [EvidenceScoreFactory.build(assertion=assertion, score_type=score_type)]
        )
    sql = [query["sql"] for query in queries.captured_queries]
    shared = [
        n
        for n, text in enumerate(sql)
        if "FOR SHARE" in text and "graph_scoretype" in text
    ]
    inserting = [
        n for n, text in enumerate(sql) if 'INSERT INTO "graph_evidencescore"' in text
    ]
    assert shared and inserting and shared[0] < inserting[0]


# ── The registry's score types ──────────────────────────────────────────────


def test_the_synthetic_score_types_are_the_two_d12_needs():
    types = {t["slug"]: t for t in load_registry().score_types}
    assert {
        slug: (t["value_kind"], t["direction"], t["range_min"], t["range_max"])
        for slug, t in types.items()
    } == {
        "synthetic:confidence": ("probability", "higher_is_stronger", 0, 1),
        "synthetic:rank": ("rank", "lower_is_stronger", 1, None),
    }
    for score_type in types.values():
        assert score_type["defining_document"].startswith(
            "docs/project/generalization/GENERALIZATION_ARCHITECTURE.md#d12"
        )
        assert score_type["comparable_across_datasets"] is False


def _with_score_type(slug, /, **changes):
    loaded = load_registry()
    entries = [
        {**entry, **changes} if entry["slug"] == slug else entry
        for entry in loaded.score_types
    ]
    return validate(dataclasses.replace(loaded, score_types=tuple(entries)))


@pytest.mark.parametrize(
    "changes,problem",
    [
        ({"range_min": math.nan}, "range_min is a finite number or null"),
        ({"range_max": math.inf}, "range_max is a finite number or null"),
        ({"range_max": -math.inf}, "range_max is a finite number or null"),
        ({"range_min": True}, "range_min is a finite number or null"),
        ({"range_min": "0"}, "range_min is a finite number or null"),
        ({"range_max": 10**400}, "range_max is a finite number or null"),
        ({"range_min": 1, "range_max": 0}, "range_min is at most range_max"),
        ({"range_max": 2}, "a probability's range lies within [0, 1]"),
        ({"range_min": -0.5}, "a probability's range lies within [0, 1]"),
        (
            {"value_kind": "p_value"},
            "a p_value is lower_is_stronger",
        ),
        (
            {"direction": "signed"},
            "a signed score is continuous",
        ),
        ({"value_kind": "ratio"}, "value_kind 'ratio' is not one of"),
        ({"direction": "up"}, "direction 'up' is not one of"),
        ({"defining_document": ""}, "needs a defining_document"),
        ({"label": " "}, "needs a label and a definition"),
        ({"comparable_across_datasets": "no"}, "comparable_across_datasets is true"),
        ({"slug": "Synthetic:confidence"}, "a slug is <source>:<name>"),
        ({"slug": "confidence"}, "a slug is <source>:<name>"),
    ],
)
def test_a_score_type_that_breaks_a_rule_is_rejected(changes, problem):
    problems = _with_score_type("synthetic:confidence", **changes)
    assert any(problem in line for line in problems), problems


def test_a_signed_score_states_its_null_model():
    signed = {
        "value_kind": "continuous",
        "direction": "signed",
        "range_min": None,
        "range_max": None,
    }
    problems = _with_score_type("synthetic:confidence", **signed)
    assert any("states the null_model" in line for line in problems), problems
    assert (
        _with_score_type("synthetic:confidence", **signed, null_model="Multiplicative.")
        == []
    )


# ── Scores a claim states (graph.claims) ────────────────────────────────────


@pytest.fixture
def ev(registry):
    return evidence_release(records=2)


def scored(ev, *scores, at=0):
    """Record one regulation claim with these scores."""
    claim = Claim(
        relationship_type="transcriptional_regulation",
        subject=Endpoint(entity_of("gene"), "SYN-A", "primary"),
        object=Endpoint(entity_of("gene"), "SYN-B", "primary"),
        outcome="supported",
        evidence_basis="experimental",
        assertion_method="manual_assertion",
        scores=scores,
    )
    return record_claims(ev.records[at], [claim], import_run=ev.run)


def nothing_written():
    return not (
        Relationship.objects.exists()
        or EvidenceAssertion.objects.exists()
        or EvidenceScore.objects.exists()
    )


@pytest.mark.parametrize(
    "raw,why",
    [
        ("NaN", "is not a finite number"),
        ("-nan", "is not a finite number"),
        ("inf", "is not a finite number"),
        ("-Infinity", "is not a finite number"),
        ("1e400", "overflows a double"),
        ("-1.5e309", "overflows a double"),
        ("<2.2e-16", "is censored"),
        ("< 0.05", "is censored"),
        (">=3", "is censored"),
        # Digits of other scripts: float() reads them, the grammar does not.
        ("٣", "is not a number openPIP reads"),
        ("３.５", "is not a number openPIP reads"),
        ("0x1p3", "is not a number openPIP reads"),
        ("n/a", "is not a number openPIP reads"),
    ],
)
def test_a_raw_value_without_a_finite_value_is_kept_with_a_null_value(ev, raw, why):
    score_type = ScoreTypeFactory(slug="stats:test_value")
    result = scored(ev, ScoreValue(score_type, raw))
    score = result.assertions[0].assertion.scores.get()
    assert (score.value, score.raw_value) == (None, raw)
    assert result.notes == (
        f"Claim 1: score stats:test_value: {parse_score(raw).note}",
    )
    assert why in result.notes[0]


@pytest.mark.parametrize(
    "raw,value", [("1e-400", 0.0), ("-1e-400", -0.0), ("5e-324", 5e-324)]
)
def test_a_value_that_underflows_is_kept_with_a_note(ev, raw, value):
    result = scored(ev, ScoreValue(p_value_score_type(), raw))
    score = result.assertions[0].assertion.scores.get()
    assert (score.value, score.raw_value) == (value, raw)
    assert "underflows a double" in result.notes[0]


def test_a_raw_value_is_stored_verbatim(ev):
    result = scored(ev, ScoreValue(signed_score_type(), " -0.25 \t"))
    score = result.assertions[0].assertion.scores.get()
    assert (score.value, score.raw_value) == (-0.25, " -0.25 \t")
    assert result.notes == ()


@pytest.mark.parametrize(
    "kind,raw,message",
    [
        ("probability", "1.5", "a probability lies in [0, 1]"),
        ("p_value", "-0.01", "a p_value lies in [0, 1]"),
        ("q_value", "2", "a q_value lies in [0, 1]"),
        ("rank", "0", "a rank is an integer from 1"),
        ("rank", "2.5", "a rank is an integer from 1"),
        ("count", "-1", "a count is an integer from 0"),
        ("count", "0.5", "a count is an integer from 0"),
    ],
)
def test_a_value_unsuited_to_its_kind_refuses_the_claim(ev, kind, raw, message):
    score_type = ScoreTypeFactory(value_kind=kind)
    with pytest.raises(ClaimError, match=r"Rule 14 \(scores\).*" + re.escape(message)):
        scored(ev, ScoreValue(score_type, raw))
    assert nothing_written()


def test_a_value_outside_its_types_range_refuses_the_claim(ev):
    bounded = ScoreTypeFactory(range_min=-1.0, range_max=1.0)
    for raw, message in (("1.5", "at most 1.0"), ("-2", "at least -1.0")):
        with pytest.raises(ClaimError, match=f"Rule 14.*{message}"):
            scored(ev, ScoreValue(bounded, raw))
    assert nothing_written()
    # The bounds themselves are in range, and NULL is never checked.
    scored(ev, ScoreValue(bounded, "1"), at=0)
    scored(ev, ScoreValue(bounded, "-Inf"), at=1)
    assert sorted(EvidenceScore.objects.values_list("raw_value", flat=True)) == [
        "-Inf",
        "1",
    ]


def test_a_claim_has_one_score_per_type(ev):
    score_type = ScoreTypeFactory()
    with pytest.raises(ClaimError, match=f"Rule 14.*{score_type.slug} is given twice"):
        scored(ev, ScoreValue(score_type, "0.1"), ScoreValue(score_type.slug, "0.1"))
    assert nothing_written()


@pytest.mark.parametrize(
    "score,message",
    [
        (ScoreValue("stats:no_such_type", "1"), "names no score type"),
        (ScoreValue(ScoreType(slug="stats:unsaved"), "1"), "names no score type"),
        (ScoreValue(None, "1"), "names no score type"),
        (ScoreValue("stats:test_value", ""), "never blank"),
        (ScoreValue("stats:test_value", "  "), "never blank"),
        (ScoreValue("stats:test_value", "1\x00"), "NUL"),
        (ScoreValue("stats:test_value", "1" * 256), "at most 255"),
        (ScoreValue("stats:test_value", 0.5), "is text"),
    ],
)
def test_a_score_names_a_stored_type_and_a_short_raw_value(ev, score, message):
    ScoreTypeFactory(slug="stats:test_value")
    with pytest.raises(ClaimError, match=f"Rule 14.*{message}"):
        scored(ev, score)
    assert nothing_written()
