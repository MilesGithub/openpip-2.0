"""The synthetic demonstration's acceptance (G6; architecture D12 and §4.5):
every case of D12's matrix, asserted at service level on the demonstration
graph.demo.load_demo loads, through G3's summary module (summarize,
evidence, covered_assertions), G1's resolver (resolve), graph.claims
(canonical_relationship, find_relationship) and
graph.visibility.visible_release_ids.

Each test that reads the demonstration loads it once, as alice, an active
staff account: its releases carry internal approvals only, so staff alone see
them. v2 is published and supersedes v1, so the default view is v2's, and v1
is read by selecting it. In D12's order: the header rules, the stored
reference chain and the label cases; the default view, edge by edge; v1 out
of the default view and readable when selected; the summary rules of §4.5 on
every edge and filter; the rows reported and never imported; and D12's
identifier in an undeclared namespace, which a synthetic release refuses
whole (owner decision O1), on a file the test builds.

The §4.5 rules the demonstration's rows cannot express, and the G3 tests of
test_summary.py that cover them on factory rows:

- the inconclusive and not_significant outcomes, which no row states:
  test_not_significant_and_inconclusive_are_counted_apart_and_never_support;
- activating beside unknown reading activating, which no edge holds:
  test_activating_with_unknown_reads_activating;
- independence by detection method, and the detection-method filter: the
  demonstration states no method (owner decision O2), so the records of one
  origin are always one observation:
  test_only_detection_methods_decide_independence,
  test_methods_decide_which_records_of_one_paper_are_one_observation,
  test_detection_methods_match_any_one_named;
- evidence groups: the demonstration's files have no evidence_group column:
  test_one_evidence_group_is_one_observation,
  test_one_group_label_in_two_releases_is_two_groups;
- level_assigned true, a level resting only on openPIP's assignment (B14),
  which canonical_tsv_v1 never makes:
  test_level_assigned_says_the_level_rests_on_openpips_assignment;
- the genetic-sign values: registry v1 has no type with sign polarity, so
  every edge here reads not_applicable: test_the_genetic_sign_summary;
- two datasets citing one paper, the demonstration being one dataset:
  test_two_datasets_citing_one_paper_with_the_same_methods_count_once;
- observation_key, which M1 adds; and ranking for truncation, which is A2's,
  over the covered set covered_assertions gives (asserted here, and in
  test_covered_assertions_is_c_over_every_relationship).

The D12 clauses that are not service-level, and their owners: "dashed when
shown" and "Drawn dotted" are F2's rendering of the outcome and the evidence
basis asserted here; the relationship page and its evidence list are A2's
endpoints and F3's panels, over the summaries and evidence asserted here;
"stays live" is B1's availability, computed from the selected set asserted
here; expand=reference is X1's, following the chain asserted here; the MSW
mirror is F1's; the "synthetic demonstration data" badge is F4's and X1's;
"headline counts exclude them" is H1's, reading the is_synthetic asserted
here; and "never on a public hub without an explicit recorded decision" is
met by recording internal approvals only, asserted in test_load_demo, where
a visitor's sets are empty.
"""

import ast
import dataclasses
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db.models import Q
from django.test import RequestFactory

from graph.claims import ClaimError, canonical_relationship, find_relationship
from graph.demo import DATASET_SLUG, DEMO_DIR, SOURCE_SLUG, load_demo
from graph.ingest.commit import CommitError, commit_release
from graph.ingest.dry_run import dry_run
from graph.ingest.registration import Options, register_release
from graph.licensing import latest_licences
from graph.models import (
    SYNTHETIC_TAXON,
    DataSourceKind,
    Entity,
    EntityIdentifier,
    EntityStatus,
    EvidenceAssertion,
    EvidencePublicationRole,
    IdentifierRole,
    ImportMode,
    ImportRun,
    ImportState,
    Outcome,
    ParticipantSide,
    Publication,
    RecordStatus,
    Relationship,
    ReleaseStatus,
    SourceRecord,
    Taxon,
    Term,
)
from graph.releases import withdraw_release
from graph.resolution import (
    AMBIGUOUS,
    CANDIDATE,
    RESOLVED,
    UNMATCHED,
    Candidate,
    Match,
    Resolution,
    resolve,
)
from graph.summary import (
    ALL_OUTCOMES,
    EFFECT_KEYS,
    LEVEL_KEYS,
    SIGN_KEYS,
    EdgeSummary,
    EvidenceFilter,
    ScoreRange,
    covered_assertions,
    evidence,
    summarize,
    term_label,
)
from graph.tests.factories import (
    DataSourceFactory,
    SourceDatasetFactory,
    StaffUserFactory,
)
from graph.visibility import Purpose, ReleaseSet, visible_release_ids

TR, PI = "transcriptional_regulation", "physical_interaction"
ENCODES, ISOFORM_OF = "encodes", "isoform_of"
SUBJECT, OBJECT = ParticipantSide.SUBJECT, ParticipantSide.OBJECT
REPORTED_IN = EvidencePublicationRole.REPORTED_IN

# The thirteen entities v1 creates, by identifier: their namespace, type and
# label.
GENES = (
    "SYN-TF1",
    "SYN-TF2",
    "SYN-TF3",
    "SYN-G1",
    "SYN-G2",
    "SYN-G3",
    "SYN-G4",
    "SYN-G5",
    "SYN-G6",
)
PROTEINS = ("SYN-P1", "SYN-P2", "SYN-P3")
ISOFORMS = ("SYN-P1-1",)
NAMESPACES = {
    **dict.fromkeys(GENES, "synthetic_gene"),
    **dict.fromkeys(PROTEINS + ISOFORMS, "synthetic_protein"),
}
TYPES = {
    **dict.fromkeys(GENES, "gene"),
    **dict.fromkeys(PROTEINS, "protein"),
    **dict.fromkeys(ISOFORMS, "protein_isoform"),
}
LABELS = {"SYN-G3": "SYN-ALPHA", "SYN-G6": "SYN-SHARED", "SYN-P3": "SYN-SHARED"}

# The fourteen relationships the demonstration stores, by the D12 case each
# states, as (type, subject, object).
EDGES = {
    "direction": (TR, "SYN-TF1", "SYN-G1"),
    "direction reversed": (TR, "SYN-G1", "SYN-TF1"),
    "mixed effect": (TR, "SYN-TF2", "SYN-G2"),
    "unknown effect": (TR, "SYN-TF1", "SYN-G4"),
    "no effect stated": (TR, "SYN-TF3", "SYN-G4"),
    "tested absent": (TR, "SYN-TF1", "SYN-G2"),
    "autoregulation": (TR, "SYN-TF2", "SYN-TF2"),
    "prediction": (TR, "SYN-TF3", "SYN-G5"),
    "one label": (TR, "SYN-TF3", "SYN-G3"),
    "one row, two papers": (TR, "SYN-TF3", "SYN-G6"),
    "symmetric physical": (PI, "SYN-P1", "SYN-P2"),
    "level and tested for": (PI, "SYN-P3", "SYN-P2"),
    "encodes": (ENCODES, "SYN-TF1", "SYN-P1"),
    "isoform_of": (ISOFORM_OF, "SYN-P1-1", "SYN-P1"),
}
REGULATION = {case for case, (slug, _, _) in EDGES.items() if slug == TR}
# Each type's family and orientation (registry v1).
SHAPES = {
    TR: ("regulation", "directed"),
    PI: ("physical", "symmetric"),
    ENCODES: ("reference", "role_ordered"),
    ISOFORM_OF: ("reference", "role_ordered"),
}

# v1's lines reported and never imported (D12's "must be reported" cases),
# each issue as its code and the cell it names.
REJECTED = [
    (19, "syn-x1", [("bad_identifier", "object_id")]),
    (20, "syn-x2", [("unknown_relationship_type", "relationship_type")]),
    (21, "syn-x3", [("unknown_entity_type", "subject_type")]),
    (22, "syn-x4", [("claim_rule_7", "")]),
    (23, "syn-x5", [("claim_rule_8", "")]),
    (24, "syn-x6", [("claim_rule_3", "")]),
    (25, "syn-x7", [("negative_effect", "effect")]),
    (26, "syn-x8", [("conflicting_duplicate", "record_id")]),
]
UNSTORED = [(27, "syn-x8", [("conflicting_duplicate", "record_id")])]

# The modules this one's docstring names beside G3's tests.
MODULES = {"test_summary", "test_load_demo"}


@pytest.fixture
def loaded(registry):
    """The demonstration as graph.demo.load_demo leaves it, loaded by alice;
    her default set, and her sets of releases she selects."""
    alice = StaffUserFactory(username="alice")
    result = load_demo(loaded_by=alice)
    staff = request_for(alice)
    return SimpleNamespace(
        alice=alice,
        v1=result.releases["v1"],
        v2=result.releases["v2"],
        default=visible_release_ids(staff, Purpose.VIEW),
        selected=lambda *releases: visible_release_ids(
            staff, Purpose.VIEW, pinned=[release.pk for release in releases]
        ),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def request_for(user):
    request = RequestFactory().get("/")
    request.user = AnonymousUser() if user is None else user
    return request


def entity(value) -> Entity:
    """The entity whose primary identifier is value, in the synthetic
    namespace of its type, through G1's resolver."""
    found = resolve(value, namespace=NAMESPACES[value])
    assert found.status == RESOLVED, (value, found)
    [candidate] = found.candidates
    return candidate.entity


def relationship(slug, subject, object_):
    """The stored relationship of this type from subject to object, named by
    their identifiers, through graph.claims; None when none is stored."""
    return find_relationship(
        canonical_relationship(slug, entity(subject), entity(object_))
    )


def stored_edges() -> dict:
    """{case: its relationship}: each of EDGES is stored, and nothing else."""
    edges = {case: relationship(*stated) for case, stated in EDGES.items()}
    assert [case for case, found in edges.items() if found is None] == []
    assert sorted(found.pk for found in edges.values()) == sorted(
        Relationship.objects.values_list("pk", flat=True)
    )
    return edges


def view(edges, releases, **filters) -> dict:
    """{case: its summary} for every stored relationship with a visible
    assertion, under EvidenceFilter(**filters)."""
    cases = {found.pk: case for case, found in edges.items()}
    found = summarize(list(cases), releases=releases, filters=EvidenceFilter(**filters))
    return {cases[pk]: summarized for pk, summarized in found.items()}


def summary(edge, releases, **filters) -> EdgeSummary | None:
    """The edge's summary under EvidenceFilter(**filters), or None when it
    has no visible assertion."""
    found = summarize([edge.pk], releases=releases, filters=EvidenceFilter(**filters))
    return found.get(edge.pk)


def outcomes(**counts) -> dict:
    """Counts by outcome, every outcome present."""
    return {**dict.fromkeys(Outcome.values, 0), **counts}


def effects(**counts) -> dict:
    """Counts by effect, not_stated included, every key present."""
    return {**dict.fromkeys(EFFECT_KEYS, 0), **counts}


def signs(**counts) -> dict:
    return {**dict.fromkeys(SIGN_KEYS, 0), **counts}


def levels(**counts) -> dict:
    """Counts by physical interaction level, every level present."""
    return {**dict.fromkeys(LEVEL_KEYS, 0), **counts}


def expected(edge, release_ids, supported=1, *, observations=1, **fields):
    """The summary of an edge whose visible assertions are `supported`
    supporting ones, all covered, of `observations` independent
    observations, in these releases, none outside the filters. Each is
    experimental and states no effect, sign, paper or level unless `fields`
    says otherwise: a regulation edge then reads unknown, any other
    not_applicable."""
    family, orientation = SHAPES[edge.relationship_type_id]
    return EdgeSummary(
        **{
            "relationship_id": edge.pk,
            "relationship_type": edge.relationship_type_id,
            "family": family,
            "orientation": orientation,
            "subject_id": edge.subject_id,
            "object_id": edge.object_id,
            "shown": True,
            "outcome_counts": outcomes(supported=supported),
            "independent_outcome_counts": outcomes(supported=observations),
            "assertion_count": supported,
            "evidence_count": observations,
            "effect": "unknown" if family == "regulation" else "not_applicable",
            "effect_counts": effects(not_stated=supported),
            "genetic_sign": "not_applicable",
            "sign_counts": signs(not_stated=supported),
            "level": None,
            "level_counts": {},
            "level_assigned": None,
            "tested_levels": {},
            "by_basis": {"experimental": supported},
            "distinct_publications": 0,
            "distinct_releases": len(release_ids),
            "distinct_datasets": 1,
            "release_ids": tuple(release_ids),
            "superseded_release_ids": (),
            "outside_filters": 0,
            "outside_outcome_counts": outcomes(),
            **fields,
        }
    )


def tallies() -> tuple:
    """What an import writes, counted."""
    return tuple(
        model.objects.count()
        for model in (Entity, Relationship, EvidenceAssertion, Publication, Term)
    )


# ── What the demonstration is made of ───────────────────────────────────────


def test_the_header_rules_the_reference_chain_and_the_label_cases(loaded):
    v1, v2 = loaded.v1, loaded.v2
    # D12's choice: two synthetic releases, v2 superseding v1 and current,
    # under the repository's MIT licence.
    assert (v1.status, v2.status, v2.supersedes_id) == (
        ReleaseStatus.SUPERSEDED,
        ReleaseStatus.PUBLISHED,
        v1.pk,
    )
    assert (v1.is_synthetic, v2.is_synthetic) == (True, True)
    assert {
        pk: licence.licence_name
        for pk, licence in latest_licences([v1.pk, v2.pk]).items()
    } == {v1.pk: "MIT License", v2.pk: "MIT License"}
    # Labels prefixed SYN-; the synthetic namespaces, each entity's one
    # identifier its primary; taxon 32630, "synthetic construct"; every
    # entity provisional, created by v1.
    assert Taxon.objects.get(pk=SYNTHETIC_TAXON).scientific_name == (
        "synthetic construct"
    )
    entities = {value: entity(value) for value in NAMESPACES}
    assert Entity.objects.count() == len(entities) == 13
    for value, found in entities.items():
        assert found.label.startswith("SYN-")
        assert (
            found.entity_type_id,
            found.label,
            found.taxon_id,
            found.status,
            found.created_by_release_id,
        ) == (
            TYPES[value],
            LABELS.get(value, value),
            SYNTHETIC_TAXON,
            EntityStatus.PROVISIONAL,
            v1.pk,
        )
        assert list(
            found.identifiers.values_list("namespace_id", "value", "scope_key", "role")
        ) == [
            (NAMESPACES[value], value, f"t:{SYNTHETIC_TAXON}", IdentifierRole.PRIMARY)
        ]
    # Two title-only papers marked synthetic, local to the dataset, cited as
    # ref:<key>: no PMID, DOI, PMCID or IMEx id that could name a real one.
    assert list(
        Publication.objects.order_by("local_key").values_list(
            "local_key",
            "local_dataset_id",
            "title",
            "year",
            "is_synthetic",
            "pmid",
            "doi",
            "pmcid",
            "imex_id",
        )
    ) == [
        (
            f"syn-paper-{n}",
            v1.dataset_id,
            f"SYN demonstration paper {n} (synthetic: not a real publication)",
            2026,
            True,
            "",
            "",
            "",
            "",
        )
        for n in (1, 2)
    ]

    # The stored reference chain. Gene, protein entry and isoform are
    # separate entities; no identifier or label joins the layers.
    gene, entry, isoform, partner = (
        entities[value] for value in ("SYN-TF1", "SYN-P1", "SYN-P1-1", "SYN-P2")
    )
    assert len({gene.pk, entry.pk, isoform.pk, partner.pk}) == 4
    assert resolve("SYN-TF1", namespace="synthetic_protein") == Resolution(
        UNMATCHED, ()
    )
    assert resolve("SYN-P1", namespace="synthetic_gene") == Resolution(UNMATCHED, ())
    assert [candidate.entity for candidate in resolve("SYN-P1").candidates] == [entry]
    # The links are stored, each in its roles' order, and the physical edge
    # beyond them, lower entity first.
    encodes = relationship(ENCODES, "SYN-TF1", "SYN-P1")
    isoform_of = relationship(ISOFORM_OF, "SYN-P1-1", "SYN-P1")
    physical = relationship(PI, "SYN-P1", "SYN-P2")
    assert (encodes.subject_id, encodes.object_id, encodes.ordered) == (
        gene.pk,
        entry.pk,
        True,
    )
    assert (isoform_of.subject_id, isoform_of.object_id, isoform_of.ordered) == (
        isoform.pk,
        entry.pk,
        True,
    )
    assert (physical.subject_id, physical.object_id, physical.ordered) == (
        min(entry.pk, partner.pk),
        max(entry.pk, partner.pk),
        False,
    )
    # Their order is the claim: reversed, each is refused (rule 2), never
    # swapped.
    for slug, stated in ((ENCODES, gene), (ISOFORM_OF, isoform)):
        with pytest.raises(ClaimError) as raised:
            canonical_relationship(slug, entry, stated)
        subject_type = "gene" if slug == ENCODES else "protein_isoform"
        assert str(raised.value) == (
            f"Rule 2 (endpoint types): a {slug} subject is a {subject_type}; entity "
            f"{entry.pk} is a protein. The order is the claim: a direction the type "
            "contradicts is refused, never swapped. Nothing was written."
        )
    # By default each link is visible evidence: the reference family, its
    # roles' order, no effect and no level; its participants are the
    # entities as stated.
    links = summarize([encodes.pk, isoform_of.pk], releases=loaded.default)
    for link in (encodes, isoform_of):
        found = links[link.pk]
        assert (
            found.shown,
            found.family,
            found.orientation,
            found.effect,
            found.level,
            found.level_counts,
        ) == (True, "reference", "role_ordered", "not_applicable", None, {})
    [stated] = evidence(encodes.pk, releases=loaded.default)
    assert [
        (p.side, p.entity_id, p.identifier_as_stated, p.resolution_basis)
        for p in stated.participants.all()
    ] == [
        (SUBJECT, gene.pk, "SYN-TF1", "primary"),
        (OBJECT, entry.pk, "SYN-P1", "primary"),
    ]
    # Nothing else joins the layers: SYN-TF1's relationships are its four
    # regulation edges and encodes. SYN-P2 is reached only through encodes
    # and the physical edge, which is X1's walk: "SYN-TF1 encodes SYN-P2", an
    # identity the types allow, is not stored.
    g1, g2, g4 = (entities[value] for value in ("SYN-G1", "SYN-G2", "SYN-G4"))
    assert sorted(
        Relationship.objects.filter(Q(subject=gene) | Q(object=gene)).values_list(
            "relationship_type_id", "subject_id", "object_id"
        )
    ) == sorted(
        [
            (TR, gene.pk, g1.pk),
            (TR, g1.pk, gene.pk),
            (TR, gene.pk, g2.pk),
            (TR, gene.pk, g4.pk),
            (ENCODES, gene.pk, entry.pk),
        ]
    )
    assert relationship(ENCODES, "SYN-TF1", "SYN-P2") is None

    # The label cases, through the resolver with no namespace, which reads
    # every entity whatever the release set.
    tf1, g3, g6, p3 = (
        entities[value] for value in ("SYN-TF1", "SYN-G3", "SYN-G6", "SYN-P3")
    )
    # Label and identifier: SYN-TF1 is its gene's identifier and label, one
    # entity matched twice: resolved.
    assert resolve("SYN-TF1") == Resolution(
        RESOLVED,
        (
            Candidate(
                tf1,
                (
                    Match("primary", "synthetic_gene", "SYN-TF1", True, False),
                    Match("label", None, "SYN-TF1", True, False),
                ),
                True,
            ),
        ),
    )
    # One label: SYN-ALPHA labels SYN-G3 alone: candidate, never resolved.
    assert resolve("SYN-ALPHA") == Resolution(
        CANDIDATE,
        (Candidate(g3, (Match("label", None, "SYN-ALPHA", True, False),), False),),
    )
    # Shared label: SYN-SHARED labels a gene and a protein: ambiguous, the
    # gene listed first; narrowed to genes it is a candidate, never one
    # entity resolved.
    shared = (Match("label", None, "SYN-SHARED", True, False),)
    assert resolve("SYN-SHARED") == Resolution(
        AMBIGUOUS, (Candidate(g6, shared, False), Candidate(p3, shared, False))
    )
    assert resolve("SYN-SHARED", entity_type="gene") == Resolution(
        CANDIDATE, (Candidate(g6, shared, False),)
    )
    # A case variant in a case-exact namespace: syn-tf1 matches SYN-TF1's
    # identifier and label only by ignoring case: candidate.
    assert resolve("syn-tf1") == Resolution(
        CANDIDATE,
        (
            Candidate(
                tf1,
                (
                    Match("primary", "synthetic_gene", "SYN-TF1", False, False),
                    Match("label", None, "SYN-TF1", False, False),
                ),
                False,
            ),
        ),
    )


# ── The default view ────────────────────────────────────────────────────────


def test_the_default_view_shows_v2_edge_by_edge(loaded):
    v1, v2, default = loaded.v1, loaded.v2, loaded.default
    assert (default.ids, default.superseded) == ({v2.pk}, frozenset())
    edges = stored_edges()
    on_v2 = (v2.pk,)
    # Direction: SYN-TF1 -> SYN-G1 and SYN-G1 -> SYN-TF1 are two
    # relationships, each in the order stated.
    tf1, g1 = entity("SYN-TF1"), entity("SYN-G1")
    forward, backward = edges["direction"], edges["direction reversed"]
    assert (forward.subject_id, forward.object_id, forward.ordered) == (
        tf1.pk,
        g1.pk,
        True,
    )
    assert (backward.subject_id, backward.object_id, backward.ordered) == (
        g1.pk,
        tf1.pk,
        True,
    )
    # Autoregulation: a self-loop.
    loop = edges["autoregulation"]
    assert loop.subject_id == loop.object_id == entity("SYN-TF2").pk
    # Symmetric physical: one relationship whichever order is stated, the
    # lower entity first.
    p1, p2 = entity("SYN-P1"), entity("SYN-P2")
    physical = edges["symmetric physical"]
    assert canonical_relationship(PI, p1, p2) == canonical_relationship(PI, p2, p1)
    assert (physical.subject_id, physical.object_id, physical.ordered) == (
        min(p1.pk, p2.pk),
        max(p1.pk, p2.pk),
        False,
    )

    # Every summary of the default view, in full: v2's records alone.
    assert view(edges, default) == {
        # Direction: v2's effects, both inhibiting.
        "direction": expected(
            forward, on_v2, effect="inhibiting", effect_counts=effects(inhibiting=1)
        ),
        "direction reversed": expected(
            backward, on_v2, effect="inhibiting", effect_counts=effects(inhibiting=1)
        ),
        # Mixed effect: two records citing two papers, two observations.
        "mixed effect": expected(
            edges["mixed effect"],
            on_v2,
            2,
            observations=2,
            effect="mixed",
            effect_counts=effects(activating=1, inhibiting=1),
            distinct_publications=2,
        ),
        # Unknown and not stated: both read unknown; the counts tell them
        # apart.
        "unknown effect": expected(
            edges["unknown effect"], on_v2, effect_counts=effects(unknown=1)
        ),
        "no effect stated": expected(edges["no effect stated"], on_v2),
        # Tested absent: summarized but hidden by default, and never part
        # of the effect summary.
        "tested absent": expected(
            edges["tested absent"],
            (),
            0,
            observations=0,
            shown=False,
            outcome_counts=outcomes(tested_absent=1),
            independent_outcome_counts=outcomes(tested_absent=1),
            effect=None,
            by_basis={},
            distinct_datasets=0,
        ),
        "autoregulation": expected(
            loop, on_v2, effect="activating", effect_counts=effects(activating=1)
        ),
        # Prediction: its basis, no effect stated.
        "prediction": expected(
            edges["prediction"], on_v2, by_basis={"computational_prediction": 1}
        ),
        "one label": expected(
            edges["one label"],
            on_v2,
            effect="activating",
            effect_counts=effects(activating=1),
        ),
        # One row citing two papers: two assertions, two observations.
        "one row, two papers": expected(
            edges["one row, two papers"],
            on_v2,
            2,
            observations=2,
            effect="activating",
            effect_counts=effects(activating=2),
            distinct_publications=2,
        ),
        # Symmetric physical: two records of one origin are one
        # observation; the strongest supported level, stated, not assigned.
        "symmetric physical": expected(
            physical,
            on_v2,
            2,
            level="direct_interaction",
            level_counts=levels(direct_interaction=1, physical_association=1),
            level_assigned=False,
        ),
        # Level and tested for: only support sets the level, and the
        # direct-interaction test against the edge is counted and shown as
        # tested for.
        "level and tested for": expected(
            edges["level and tested for"],
            on_v2,
            outcome_counts=outcomes(supported=1, tested_absent=1),
            independent_outcome_counts=outcomes(supported=1, tested_absent=1),
            level="not_stated",
            level_counts=levels(not_stated=1),
            level_assigned=False,
            tested_levels={"tested_absent": ("direct_interaction",)},
        ),
        # Reference links, of unknown basis.
        "encodes": expected(edges["encodes"], on_v2, by_basis={"unknown": 1}),
        "isoform_of": expected(edges["isoform_of"], on_v2, by_basis={"unknown": 1}),
    }

    # Tested absent: shown when its outcome is asked for, still with no
    # effect; its one assertion is tested_absent, and never covered by
    # default.
    absent = edges["tested absent"]
    asked = summary(absent, default, outcomes={"tested_absent"})
    assert (asked.shown, asked.effect, asked.assertion_count, asked.release_ids) == (
        True,
        None,
        0,
        on_v2,
    )
    [row] = evidence(absent.pk, releases=default)
    assert (row.source_record.record_key, row.outcome, row.effect) == (
        "syn-07",
        Outcome.TESTED_ABSENT,
        None,
    )
    assert not covered_assertions(releases=default).filter(relationship=absent).exists()
    assert set(
        covered_assertions(
            releases=default, filters=EvidenceFilter(outcomes={"tested_absent"})
        ).values_list("relationship_id", flat=True)
    ) == {absent.pk, edges["level and tested for"].pk}

    # Basis and method are separate fields: the prediction is an automatic
    # one, the reference links rest on an unknown basis.
    assert Counter(
        covered_assertions(
            releases=default, filters=EvidenceFilter(outcomes=ALL_OUTCOMES)
        ).values_list("evidence_basis", "assertion_method")
    ) == {
        ("experimental", "manual_assertion"): 15,
        ("computational_prediction", "automatic_assertion"): 1,
        ("unknown", "manual_assertion"): 2,
    }

    # Two typed scores, both kept on one assertion as stated, never pooled:
    # a range applies to its own type only.
    prediction = edges["prediction"]
    [predicted] = evidence(prediction.pk, releases=default)
    assert (predicted.evidence_basis, predicted.assertion_method) == (
        "computational_prediction",
        "automatic_assertion",
    )
    assert [
        (score.score_type_id, score.raw_value, score.value)
        for score in predicted.scores.all()
    ] == [("synthetic:confidence", "0.8", 0.8), ("synthetic:rank", "1", 1.0)]
    ranked = summary(
        prediction, default, scores=(ScoreRange("synthetic:rank", max=0.95),)
    )
    assert (ranked.shown, ranked.outside_filters, ranked.outside_outcome_counts) == (
        False,
        1,
        outcomes(supported=1),
    )
    confident = summary(
        prediction, default, scores=(ScoreRange("synthetic:confidence", max=0.95),)
    )
    assert (confident.shown, confident.assertion_count, confident.outside_filters) == (
        True,
        1,
        0,
    )

    # Symmetric physical: the participants keep the sides as stated, and
    # each interaction type, a crosswalk term (owner decision O2), is shown
    # by the crosswalk's label, whatever the set, even a visitor's, which
    # holds nothing: never by the release that first stated it, the
    # synthetic v1.
    assert [
        (
            row.source_record.record_key,
            [(p.side, p.entity_id) for p in row.participants.all()],
            row.interaction_type_id,
            term_label(row.interaction_type, releases=default),
            row.interaction_level,
        )
        for row in evidence(physical.pk, releases=default)
    ] == [
        (
            "syn-10",
            [(SUBJECT, p1.pk), (OBJECT, p2.pk)],
            "MI:0915",
            "physical association",
            "physical_association",
        ),
        (
            "syn-11",
            [(SUBJECT, p2.pk), (OBJECT, p1.pk)],
            "MI:0407",
            "direct interaction",
            "direct_interaction",
        ),
    ]
    nobody = visible_release_ids(request_for(None), Purpose.VIEW)
    assert nobody.ids == frozenset()
    assert {
        curie: (term_label(curie, releases=nobody), label_release)
        for curie, label_release in Term.objects.values_list("curie", "label_release")
    } == {
        "MI:0407": ("direct interaction", v1.pk),
        "MI:0915": ("physical association", v1.pk),
    }

    # Level and tested for: under a direct-interaction filter the edge has
    # no support left, and is shown only when the test against it is asked
    # for: tested for direct interaction, at no level.
    tested_for = edges["level and tested for"]
    direct = summary(tested_for, default, levels={"direct_interaction"})
    assert (
        direct.shown,
        direct.outcome_counts,
        direct.outside_filters,
        direct.outside_outcome_counts,
        direct.tested_levels,
    ) == (
        False,
        outcomes(tested_absent=1),
        1,
        outcomes(supported=1),
        {"tested_absent": ("direct_interaction",)},
    )
    direct = summary(
        tested_for, default, levels={"direct_interaction"}, outcomes={"tested_absent"}
    )
    assert (
        direct.shown,
        direct.assertion_count,
        direct.level,
        direct.level_counts,
        direct.tested_levels,
        direct.release_ids,
    ) == (True, 0, None, levels(), {"tested_absent": ("direct_interaction",)}, on_v2)

    # Two publications: the mixed edge's two records cite two papers, the
    # same two Publication rows in v1 and v2, which v2 created none of.
    papers = dict(Publication.objects.values_list("local_key", "pk"))
    mixed = edges["mixed effect"]

    def cited(edge, releases):
        """The edge's assertions: their release, record, line and key, and
        the papers each is linked to."""
        return [
            (
                row.release_id,
                row.source_record.record_key,
                row.source_record.line_number,
                row.assertion_key,
                [(link.publication_id, link.role) for link in row.publications.all()],
            )
            for row in evidence(edge.pk, releases=releases)
        ]

    one, two = papers["syn-paper-1"], papers["syn-paper-2"]
    for release, releases in ((v2, default), (v1, loaded.selected(v1))):
        assert cited(mixed, releases) == [
            (release.pk, "syn-03", 4, "main", [(one, REPORTED_IN)]),
            (release.pk, "syn-04", 5, "main", [(two, REPORTED_IN)]),
        ]
    [commit] = ImportRun.objects.filter(release=v2, mode=ImportMode.COMMIT)
    assert commit.report["counts"]["created"]["publications"] == 0
    # One row, two papers: syn-15 gives two assertions, keyed by paper,
    # each reported in one of them.
    assert cited(edges["one row, two papers"], default) == [
        (v2.pk, "syn-15", 16, "main|pub:local:syn-paper-1", [(one, REPORTED_IN)]),
        (v2.pk, "syn-15", 16, "main|pub:local:syn-paper-2", [(two, REPORTED_IN)]),
    ]


# ── v1, selected ────────────────────────────────────────────────────────────


def test_v1_leaves_the_default_view_and_stays_readable_when_selected(loaded):
    v1, v2, default = loaded.v1, loaded.v2, loaded.default
    only_v1, both = loaded.selected(v1), loaded.selected(v1, v2)
    assert (only_v1.ids, only_v1.superseded) == ({v1.pk}, {v1.pk})
    assert (both.ids, both.superseded) == ({v1.pk, v2.pk}, {v1.pk})
    edges = stored_edges()
    corrected, prediction = edges["direction"], edges["prediction"]

    def rows(releases):
        return [
            (
                row.release_id,
                row.source_record.record_key,
                row.source_record.line_number,
                row.effect,
            )
            for row in evidence(corrected.pk, releases=releases)
        ]

    # Release separation. v2 changes one effect: by default the edge and
    # its evidence show v2's alone, never mixed ...
    found = summary(corrected, default)
    assert (found.effect, found.effect_counts, found.release_ids) == (
        "inhibiting",
        effects(inhibiting=1),
        (v2.pk,),
    )
    assert rows(default) == [(v2.pk, "syn-01", 2, "inhibiting")]
    # ... selected, v1 shows v1's effect, flagged superseded, and its record
    # stays readable, with its key and line ...
    found = summary(corrected, only_v1)
    assert (
        found.effect,
        found.effect_counts,
        found.release_ids,
        found.superseded_release_ids,
    ) == ("activating", effects(activating=1), (v1.pk,), (v1.pk,))
    assert rows(only_v1) == [(v1.pk, "syn-01", 2, "activating")]
    # ... and with both selected the edge is mixed: two assertions of one
    # observation.
    found = summary(corrected, both)
    assert (
        found.effect,
        found.effect_counts,
        found.assertion_count,
        found.evidence_count,
    ) == ("mixed", effects(activating=1, inhibiting=1), 2, 1)
    # v1's view is v2's but for that effect, since v2 restates every other
    # mapped row.
    by_default = view(edges, default)

    def as_v1(found):
        flagged = (v1.pk,) if found.release_ids else ()
        changes = {"release_ids": flagged, "superseded_release_ids": flagged}
        if found.relationship_id == corrected.pk:
            changes.update(effect="activating", effect_counts=effects(activating=1))
        return dataclasses.replace(found, **changes)

    assert view(edges, only_v1) == {
        case: as_v1(found) for case, found in by_default.items()
    }

    # No publication: the prediction, in both releases, has one origin, the
    # dataset, so a restatement never counts again ...
    found = summary(prediction, both)
    assert (
        found.assertion_count,
        found.evidence_count,
        found.release_ids,
        found.superseded_release_ids,
        found.distinct_releases,
        found.distinct_datasets,
        found.distinct_publications,
    ) == (2, 1, (v1.pk, v2.pk), (v1.pk,), 2, 1, 0)
    found = summary(prediction, default)
    assert (found.assertion_count, found.evidence_count, found.release_ids) == (
        1,
        1,
        (v2.pk,),
    )
    # ... and on every edge: with both releases selected, twice the
    # assertions, the same independent evidence (§4.5's origin rule).
    doubled = {
        case: (
            {outcome: 2 * n for outcome, n in found.outcome_counts.items()},
            found.independent_outcome_counts,
            2 * found.assertion_count,
            found.evidence_count,
            found.distinct_publications,
            found.distinct_datasets,
        )
        for case, found in by_default.items()
    }
    assert {
        case: (
            found.outcome_counts,
            found.independent_outcome_counts,
            found.assertion_count,
            found.evidence_count,
            found.distinct_publications,
            found.distinct_datasets,
        )
        for case, found in view(edges, both).items()
    } == doubled

    # A set selected before v1 is withdrawn reads nothing of v1 after it:
    # each read checks the release's status again.
    withdraw_release(v1, reason="Test.", withdrawn_by=loaded.alice)
    assert summary(corrected, only_v1) is None
    assert rows(only_v1) == []
    assert summary(corrected, both).release_ids == (v2.pk,)
    assert loaded.selected(v1).ids == frozenset()


# ── The summary rules (§4.5) ────────────────────────────────────────────────


def test_the_summary_rules_hold_on_every_edge_and_filter(loaded):
    v2, default = loaded.v2, loaded.default
    edges = stored_edges()
    everything = set(EDGES)
    views = {}

    def seen(**filters):
        """The default view under these filters, kept for the checks every
        summary must pass."""
        found = view(edges, default, **filters)
        views[repr(filters)] = found
        return found

    def shown(**filters):
        return {case for case, found in seen(**filters).items() if found.shown}

    # Which edges appear: one whose only evidence is tested absent appears
    # when its outcome is asked for; one without visible evidence has no
    # summary at all, whether it names no relationship or the set holds
    # nothing (a visitor's set selecting v2, which has no public_view).
    assert shown() == everything - {"tested absent"}
    assert shown(outcomes={"tested_absent"}) == {
        "tested absent",
        "level and tested for",
    }
    assert shown(outcomes=ALL_OUTCOMES) == everything
    ids = sorted(found.pk for found in edges.values())
    assert summarize([ids[-1] + 1], releases=default) == {}
    nobody = visible_release_ids(request_for(None), Purpose.VIEW, pinned=[v2.pk])
    assert nobody.ids == frozenset()
    assert summarize(ids, releases=nobody) == {}
    assert not evidence(ids[0], releases=nobody).exists()
    assert not covered_assertions(releases=nobody).exists()
    # Only a set visible_release_ids issued is read: one built by hand, or
    # derived from an issued one, is refused.
    for releases in (
        ReleaseSet(frozenset({v2.pk}), frozenset()),
        dataclasses.replace(default),
    ):
        with pytest.raises(TypeError, match="visible_release_ids"):
            summarize(ids, releases=releases)
        with pytest.raises(TypeError, match="visible_release_ids"):
            evidence(ids[0], releases=releases)
        with pytest.raises(TypeError, match="visible_release_ids"):
            covered_assertions(releases=releases)

    # Outcome counts are over every filter but the outcome filter, so a
    # shown edge shows the evidence against it; the other counts are over
    # covered support, and only support sets the level: the edge stays at
    # not_stated, tested for direct interaction.
    found = seen()["level and tested for"]
    assert (
        found.shown,
        found.outcome_counts,
        found.assertion_count,
        found.evidence_count,
        found.level,
        found.tested_levels,
    ) == (
        True,
        outcomes(supported=1, tested_absent=1),
        1,
        1,
        "not_stated",
        {"tested_absent": ("direct_interaction",)},
    )
    # What lies outside the filters is counted apart: experimental evidence
    # only leaves the prediction, and the reference links of unknown basis,
    # outside.
    assert shown(evidence_bases={"experimental"}) == everything - {
        "tested absent",
        "prediction",
        "encodes",
        "isoform_of",
    }
    found = seen(evidence_bases={"experimental"})["prediction"]
    assert (
        found.shown,
        found.outcome_counts,
        found.outside_filters,
        found.outside_outcome_counts,
    ) == (False, outcomes(), 1, outcomes(supported=1))
    # An effect filter applies to the supported assertions of regulation
    # only: the physical and reference edges stay shown. The edge it covers
    # in part is summarized over what it covers, the rest counted outside.
    assert shown(effects={"inhibiting"}) == {
        "direction",
        "direction reversed",
        "mixed effect",
        "symmetric physical",
        "level and tested for",
        "encodes",
        "isoform_of",
    }
    found = seen(effects={"inhibiting"})["mixed effect"]
    assert (
        found.effect,
        found.effect_counts,
        found.assertion_count,
        found.distinct_publications,
        found.outside_filters,
        found.outside_outcome_counts,
    ) == ("inhibiting", effects(inhibiting=1), 1, 1, 1, outcomes(supported=1))
    # It never moves the evidence against an edge out of its outcome counts,
    # and covers it when its outcome is asked for.
    found = seen(effects={"activating"})["tested absent"]
    assert (found.shown, found.outcome_counts, found.outside_filters) == (
        False,
        outcomes(tested_absent=1),
        0,
    )
    assert shown(effects={"activating"}, outcomes={"tested_absent"}) == {
        "tested absent",
        "level and tested for",
    }
    # A level filter applies to the physical family only: the regulation
    # and reference edges stay shown.
    assert shown(levels={"direct_interaction"}) == (REGULATION - {"tested absent"}) | {
        "symmetric physical",
        "encodes",
        "isoform_of",
    }
    found = seen(levels={"direct_interaction"})["symmetric physical"]
    assert (
        found.assertion_count,
        found.level,
        found.level_counts,
        found.outside_filters,
    ) == (1, "direct_interaction", levels(direct_interaction=1), 1)
    # A physical edge without covered support still reads not_applicable,
    # at no level.
    found = seen(outcomes={"tested_absent"})["symmetric physical"]
    assert (
        found.shown,
        found.effect,
        found.level,
        found.level_counts,
        found.level_assigned,
    ) == (False, "not_applicable", None, levels(), None)
    # The effect summaries the rows give, None for the tested-absent edge.
    assert {found.effect for found in seen().values()} == {
        "mixed",
        "activating",
        "inhibiting",
        "unknown",
        "not_applicable",
        None,
    }
    assert seen()["tested absent"].effect is None

    # Every summary of every view above: each count dict holds every key;
    # the genetic sign is not_applicable everywhere; a regulation edge reads
    # None exactly when nothing supports it, and any other edge
    # not_applicable; level fields belong to the physical family.
    assert len(views) == 8
    for found in (
        found for summaries in views.values() for found in summaries.values()
    ):
        assert set(found.outcome_counts) == set(Outcome.values)
        assert set(found.independent_outcome_counts) == set(Outcome.values)
        assert set(found.outside_outcome_counts) == set(Outcome.values)
        assert set(found.effect_counts) == set(EFFECT_KEYS)
        assert set(found.sign_counts) == set(SIGN_KEYS)
        assert found.genetic_sign == "not_applicable"
        if found.family == "regulation":
            assert (found.effect is None) == (found.assertion_count == 0)
        else:
            assert found.effect == "not_applicable"
        if found.family == "physical":
            assert set(found.level_counts) == set(LEVEL_KEYS)
        else:
            assert (
                found.level,
                found.level_counts,
                found.level_assigned,
                found.tested_levels,
            ) == (None, {}, None, {})


# ── Reported, never imported ────────────────────────────────────────────────


def test_the_rows_reported_are_never_imported(loaded):
    v1, v2 = loaded.v1, loaded.v2
    [dry1] = ImportRun.objects.filter(release=v1, mode=ImportMode.DRY_RUN)
    [dry2] = ImportRun.objects.filter(release=v2, mode=ImportMode.DRY_RUN)
    report = dry1.report

    def listed(entries):
        return [
            (
                entry["line"],
                entry["key"],
                [(issue["code"], issue["field"]) for issue in entry["issues"]],
            )
            for entry in entries
        ]

    # v1's dry run reports lines 19 to 26 as rejected and line 27 as not
    # stored, each with its reasons, at its line.
    assert report["counts"]["records"] == {
        "mapped": 17,
        "unresolved": 0,
        "ambiguous": 0,
        "rejected": 8,
        "skipped": 0,
    }
    assert report["counts"]["unstored"] == 1
    assert listed(report["rejected"]["entries"]) == REJECTED
    assert listed(report["unstored"]["entries"]) == UNSTORED
    for entry in report["rejected"]["entries"] + report["unstored"]["entries"]:
        assert {(issue["line"], issue["severity"]) for issue in entry["issues"]} == {
            (entry["line"], "error")
        }
    # The records of lines 19 to 26 are kept as the dry run wrote them,
    # rejected, with those issues; line 27 is stored nowhere.
    assert [
        (
            record.line_number,
            record.record_key,
            record.status,
            record.issues,
            record.import_run_id,
        )
        for record in SourceRecord.objects.filter(
            release=v1, record_key__startswith="syn-x"
        ).order_by("line_number")
    ] == [
        (entry["line"], entry["key"], RecordStatus.REJECTED, entry["issues"], dry1.pk)
        for entry in report["rejected"]["entries"]
    ]
    assert not SourceRecord.objects.filter(release=v1, line_number=27).exists()
    # Never imported: no assertion of either release rests on them ...
    every = covered_assertions(
        releases=loaded.selected(v1, v2), filters=EvidenceFilter(outcomes=ALL_OUTCOMES)
    )
    assert every.count() == EvidenceAssertion.objects.count() == 36
    assert not EvidenceAssertion.objects.filter(
        source_record__record_key__startswith="syn-x"
    ).exists()
    # ... and SYN-g7, the identifier failing its pattern, names no entity.
    assert resolve("SYN-g7") == Resolution(UNMATCHED, ())
    assert not EntityIdentifier.objects.filter(value__iexact="SYN-g7").exists()
    # v2 restates v1's mapped rows only: its dry run reports nothing.
    report = dry2.report
    assert (
        report["counts"]["records"]["rejected"],
        report["rejected"]["total"],
        report["unstored"]["total"],
    ) == (0, 0, 0)
    assert not SourceRecord.objects.filter(
        release=v2, record_key__startswith="syn-x"
    ).exists()


# ── An identifier in an undeclared namespace (owner decision O1) ────────────


def test_an_undeclared_namespace_refuses_a_synthetic_release_whole(registry, tmp_path):
    # The demonstration's dataset, as the loader creates it, and nothing
    # loaded, so the genes the valid row names exist nowhere yet.
    alice = StaffUserFactory(username="alice")
    source = DataSourceFactory(slug=SOURCE_SLUG, kind=DataSourceKind.THIS_PORTAL)
    SourceDatasetFactory(slug=DATASET_SLUG, data_source=source)
    # The header and line 2 of release_v1.tsv, then line 2 again as record
    # syn-o1 with its subject in synthetic_cell, a namespace no registry
    # declares.
    header, valid = (DEMO_DIR / "release_v1.tsv").read_bytes().decode().split("\n")[:2]
    columns = header.split("\t")
    row = dict(zip(columns, valid.split("\t")))
    row.update(record_id="syn-o1", subject_namespace="synthetic_cell")
    path = tmp_path / "release_o1.tsv"
    lines = [header, valid, "\t".join(row[column] for column in columns)]
    path.write_bytes(("\n".join(lines) + "\n").encode())
    counted = tallies()
    release = register_release(
        dataset=DATASET_SLUG,
        version="o1",
        adapter="canonical_tsv_v1",
        file=path,
        manifest=DEMO_DIR / "manifest.json",
        options=Options(is_synthetic=True, taxa=(SYNTHETIC_TAXON,)),
        registered_by=alice,
    ).release
    run = dry_run(release, triggered_by=alice, create_entities=True)
    report = run.report
    # The dry run completes, and refuses the release: line 3 is rejected as
    # a synthetic violation and an unknown namespace, and the synthetic
    # check names it.
    assert (run.state, report["verdict"], report["synthetic"]["passed"]) == (
        ImportState.COMPLETED,
        "refused",
        False,
    )
    assert report["counts"]["records"] == {
        "mapped": 1,
        "unresolved": 0,
        "ambiguous": 0,
        "rejected": 1,
        "skipped": 0,
    }
    assert [
        (
            entry["line"],
            entry["key"],
            sorted(issue["code"] for issue in entry["issues"]),
        )
        for entry in report["rejected"]["entries"]
    ] == [(3, "syn-o1", ["synthetic_violation", "unknown_namespace"])]
    assert [
        (entry["line"], entry["key"], [issue["code"] for issue in entry["issues"]])
        for entry in report["synthetic"]["violations"]["entries"]
    ] == [(3, "syn-o1", ["synthetic_violation"])]
    # Line 2 alone would create its two genes, but the commit refuses the
    # release whole (precondition 12), and nothing is imported.
    assert [entry["identity_key"] for entry in report["would_create"]["entries"]] == [
        "synthetic_gene|t:32630|SYN-TF1",
        "synthetic_gene|t:32630|SYN-G1",
    ]
    with pytest.raises(CommitError) as raised:
        commit_release(release, triggered_by=alice, create_entities=True)
    assert (raised.value.precondition, str(raised.value)) == (
        12,
        f"Release {release.pk}'s latest dry run, {run.pk}, was refused: run "
        "graph_import --dry-run again, with the options you will commit with. "
        "Nothing was written.",
    )
    release.refresh_from_db()
    assert release.status == ReleaseStatus.DRAFT
    assert list(ImportRun.objects.filter(release=release)) == [run]
    assert tallies() == counted
    assert not Entity.objects.filter(created_by_release=release).exists()
    # Its two records are the dry run's own.
    assert [
        (record.line_number, record.record_key, record.status, record.import_run_id)
        for record in SourceRecord.objects.filter(release=release).order_by(
            "line_number"
        )
    ] == [
        (2, "syn-01", RecordStatus.MAPPED, run.pk),
        (3, "syn-o1", RecordStatus.REJECTED, run.pk),
    ]


# ── This module's account of what the rows cannot express ───────────────────


def test_the_g3_tests_named_for_what_the_rows_cannot_express_exist():
    source = Path(__file__).with_name("test_summary.py").read_text(encoding="utf-8")
    defined = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }
    named = set(re.findall(r"\btest_\w+", __doc__)) - MODULES
    assert len(named) == 11
    assert named <= defined, sorted(named - defined)
