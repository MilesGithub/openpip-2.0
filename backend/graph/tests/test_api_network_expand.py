"""GET /api/graph/v1/network with expand=reference (architecture §4.6,
§7.3): the parameter and its 400s, the paths each derived seed lists, the
joins after the ranked edges, what a hidden, superseded or pinned mapping
release shows, the cap on looked-up seeds and what it reports, what a request
costs, the 503 of a closure past its deadline and the site budget, on X1's
factory worlds and on the demonstration. The closure and the service are
tested in test_reference_expansion, and the walk over every hidden release,
expand's samples included, in test_api_visibility.
"""

import json
import time

import pytest
from rest_framework.test import APIClient

import graph.neighbourhood
import graph.reference_closure
from graph.api.limits import CANDIDATES_PER_SEED, DEFAULT_MAX_EDGES
from graph.api.serializers import NetworkQuerySerializer
from graph.api.throttling import GraphNetworkBudgetThrottle
from graph.api.views import NetworkView
from graph.models import SYNTHETIC_TAXON, EvidenceBasis
from graph.tests.api_helpers import (
    HIDDEN_KINDS,
    NETWORK,
    OPEN_SCOPES,
    capped_world,
    fan_out,
    ids,
    instance,
    link,
    mapping_release,
    network_url,
    pin,
    reference_world,
    state,
    synthetic_release,
    uniprot_release,
)
from graph.tests.factories import entity_of
from graph.tests.test_api_network import (
    BLOCK,
    NOT_CUT,
    NOTHING_HIDDEN,
    PI,
    TR,
    answered,
    by_id,
    counted,
    edge_ids,
    hidden,
    hidden_warning,
    history,
    network,
    problem,
    ref,
    refused,
    stored,
    timed_out,
    warning,
)
from graph.tests.test_provenance import PUBLISHED, advance

EXPANDED = {"expand": "reference"}
REFERENCE_LAYER = (
    "layers names a reference type; expand follows reference links itself."
)


def refs(kind, *rows) -> list:
    return [ref(kind, row) for row in rows]


def via(seed, *steps) -> dict:
    """One derived_from entry: the named seed's ref and the path's."""
    return {"seed": ref("entity", seed), "via": refs("relationship", *steps)}


def derived(body) -> dict:
    """{node ref: derived_from} of the nodes a path made derived seeds."""
    return {
        node["id"]: node["derived_from"]
        for node in body["nodes"]
        if node["derived_from"]
    }


def marks_name_what_is_listed(body) -> bool:
    """Every derived_from names a node the caller named and listed edges, and
    no named seed is derived (§4.4)."""
    edges = set(edge_ids(body))
    named = {node["id"] for node in body["nodes"] if node["is_seed"]}
    return all(
        not (node["is_seed"] and node["derived_from"])
        and all(
            entry["seed"] in named and set(entry["via"]) <= edges
            for entry in node["derived_from"]
        )
        for node in body["nodes"]
    )


# ── The parameter ───────────────────────────────────────────────────────────


def test_expand_is_read_and_echoed_after_layers(registry, visitor_client):
    assert NetworkView.allowed_params[1:4] == ("layers", "expand", "direction")
    assert list(NetworkQuerySerializer().fields)[1:4] == [
        "layers",
        "expand",
        "direction",
    ]
    body = visitor_client.get(f"{NETWORK}?seeds=entity:1&expand=reference").json()
    assert body["query"]["expand"] == "reference"
    assert list(body["query"]) == list(NetworkQuerySerializer().fields)
    assert (body["nodes"], body["edges"], body["page"], body["warnings"]) == (
        [],
        [],
        NOT_CUT,
        [],
    )
    plain = visitor_client.get(f"{NETWORK}?seeds=entity:1").json()
    assert plain["query"]["expand"] is None


@pytest.mark.parametrize(
    "layers,piece",
    [
        ("family:reference", "family:reference"),
        ("encodes", "encodes"),
        ("isoform_of", "isoform_of"),
        (f"{TR},encodes", "encodes"),
        ("encodes,isoform_of", "encodes"),
        ("family:physical,family:reference", "family:reference"),
    ],
)
def test_a_layer_naming_a_reference_type_is_refused_with_expand(
    layers, piece, registry, visitor_client
):
    assert refused(
        visitor_client, f"seeds=entity:1&expand=reference&layers={layers}"
    ) == {"detail": REFERENCE_LAYER, "errors": [problem("layers", piece)]}


def test_without_expand_a_reference_layer_is_walked_as_before(registry, visitor_client):
    response = visitor_client.get(f"{NETWORK}?seeds=entity:1&layers=encodes")
    assert response.status_code == 200
    response = visitor_client.get(
        f"{NETWORK}?seeds=entity:1&expand=reference&layers=family:physical,{TR}"
    )
    assert response.status_code == 200
    assert response.json()["query"]["layers"] == ["family:physical", TR]


def test_a_refused_expand_reads_no_vocabulary_and_spends_no_budget(
    registry, visitor_client, monkeypatch, django_assert_num_queries
):
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "2/min"}
    )
    with django_assert_num_queries(0):
        refused(visitor_client, "seeds=bad&expand=x")
    # The one read is layers' own.
    with django_assert_num_queries(1):
        refused(visitor_client, "seeds=entity:1&expand=reference&layers=encodes")
    assert history("graph_network_site", "site") == []


def test_while_off_expand_is_404(
    db, settings, visitor_client, django_assert_num_queries
):
    settings.TYPED_GRAPH_ENABLED = False
    with django_assert_num_queries(0):
        response = visitor_client.get(f"{NETWORK}?seeds=entity:1&expand=reference")
    assert (response.status_code, response.json()) == (404, {"detail": "Not found."})


# ── The acceptance on factory rows ──────────────────────────────────────────


def test_a_gene_draws_its_entry_and_isoform_as_derived_seeds_joined_by_labelled_links(
    registry, visitor_client
):
    w = reference_world()
    body = network(visitor_client, w.g1, **EXPANDED)
    assert ids(body["nodes"]) == refs("entity", w.g1, w.e, w.i, w.p, w.t)
    assert [node["type"] for node in body["nodes"]] == [
        "gene",
        "protein",
        "protein_isoform",
        "protein",
        "gene",
    ]
    assert [node["id"] for node in body["nodes"] if node["is_seed"]] == [
        ref("entity", w.g1)
    ]
    assert derived(body) == {
        ref("entity", w.e): [via(w.g1, w.enc1)],
        ref("entity", w.i): [via(w.g1, w.enc1, w.iso)],
    }
    assert edge_ids(body) == refs("relationship", w.phys, w.reg1, w.enc1, w.iso)
    assert [
        (
            e["type"],
            e["family"],
            e["orientation"],
            e["source"],
            e["target"],
            e["releases"],
            e["shown"],
            e["assertion_count"],
            e["by_basis"],
            e["effect"],
        )
        for e in body["edges"][2:]
    ] == [
        (
            "encodes",
            "reference",
            "role_ordered",
            ref("entity", w.g1),
            ref("entity", w.e),
            [ref("release", w.mapping)],
            True,
            1,
            {"unknown": 1},
            "not_applicable",
        ),
        (
            "isoform_of",
            "reference",
            "role_ordered",
            ref("entity", w.i),
            ref("entity", w.e),
            [ref("release", w.uniprot)],
            True,
            1,
            {"unknown": 1},
            "not_applicable",
        ),
    ]
    assert ids(body["releases"]) == refs("release", w.mapping, w.uniprot, w.evidence)
    assert (body["page"], body["warnings"]) == (NOT_CUT, [])
    assert marks_name_what_is_listed(body)


def test_one_gene_search_shows_its_regulation_edges_and_its_entrys_physical_partners(
    registry, visitor_client
):
    w = reference_world()
    assert edge_ids(network(visitor_client, w.g1)) == refs("relationship", w.reg1)
    body = network(visitor_client, w.g1, **EXPANDED)
    assert {ref("relationship", w.reg1), ref("relationship", w.phys)} <= set(
        edge_ids(body)
    )
    assert ref("entity", w.p) in ids(body["nodes"])
    assert {e["family"] for e in body["edges"][:2]} == {"regulation", "physical"}


def test_a_gene_search_never_reaches_another_gene_through_a_shared_entry(
    registry, visitor_client
):
    w = reference_world()
    body = network(visitor_client, w.g1, **EXPANDED)
    assert set(refs("entity", w.g2, w.g3, w.gp, w.t2)).isdisjoint(ids(body["nodes"]))
    assert set(refs("relationship", w.reg2, w.enc2, w.enc3, w.encp)).isdisjoint(
        edge_ids(body)
    )
    body = network(visitor_client, w.e, **EXPANDED)
    assert derived(body) == {
        ref("entity", w.g1): [via(w.e, w.enc1)],
        ref("entity", w.g2): [via(w.e, w.enc2)],
        ref("entity", w.g3): [via(w.e, w.enc3)],
        ref("entity", w.i): [via(w.e, w.iso)],
    }
    assert ref("entity", w.gp) not in ids(body["nodes"])


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_a_hidden_mapping_release_adds_no_encodes_join(
    kind, registry, staff_user, visitor_client
):
    w = reference_world(kind, staff=staff_user)
    plain = network(visitor_client, w.g1)
    body = network(visitor_client, w.g1, **EXPANDED)
    assert body["query"] == {**plain["query"], "expand": "reference"}
    assert body | {"query": None} == plain | {"query": None}
    assert edge_ids(body) == refs("relationship", w.reg1)
    assert ids(body["releases"]) == refs("release", w.evidence)
    assert counted(visitor_client, network_url(w.g1)) == 15 + BLOCK
    assert counted(visitor_client, network_url(w.g1, **EXPANDED)) == 16 + BLOCK


@pytest.mark.parametrize("kind", HIDDEN_KINDS)
def test_entry_isoform_joins_stay_while_the_mapping_release_is_hidden(
    kind, registry, staff_user, visitor_client
):
    w = reference_world(kind, staff=staff_user)
    body = network(visitor_client, w.e, **EXPANDED)
    assert edge_ids(body) == refs("relationship", w.phys, w.iso)
    assert derived(body) == {ref("entity", w.i): [via(w.e, w.iso)]}
    assert ids(body["nodes"]) == refs("entity", w.e, w.i, w.p)
    assert ids(body["releases"]) == refs("release", w.uniprot, w.evidence)
    body = network(visitor_client, w.i, **EXPANDED)
    assert edge_ids(body) == refs("relationship", w.phys, w.iso)
    assert derived(body) == {ref("entity", w.e): [via(w.i, w.iso)]}


def test_a_superseded_mapping_release_adds_nothing_by_default_and_is_flagged_when_pinned(
    registry, visitor_client
):
    w = reference_world("superseded")
    assert network(visitor_client, w.g1, **EXPANDED) | {"query": None} == network(
        visitor_client, w.g1
    ) | {"query": None}
    body = network(
        visitor_client, w.g1, **EXPANDED, **pin(w.mapping, w.uniprot, w.evidence)
    )
    assert edge_ids(body) == refs("relationship", w.phys, w.reg1, w.enc1, w.iso)
    assert derived(body) == {
        ref("entity", w.e): [via(w.g1, w.enc1)],
        ref("entity", w.i): [via(w.g1, w.enc1, w.iso)],
    }
    assert [(r["id"], r["status"], r["superseded"]) for r in body["releases"]] == [
        (ref("release", w.mapping), "superseded", True),
        (ref("release", w.uniprot), "published", False),
        (ref("release", w.evidence), "published", False),
    ]
    assert by_id(body)[ref("relationship", w.enc1)]["releases"] == [
        ref("release", w.mapping)
    ]
    assert body["query"]["releases"] == refs(
        "release", w.mapping, w.uniprot, w.evidence
    )


def test_a_hidden_seed_with_expand_reads_like_a_nonexistent_one(
    registry, staff_user, visitor_client, staff_client
):
    w = reference_world("unapproved", staff=staff_user)
    for client in (visitor_client, staff_client):
        status, content, queries = answered(client, network_url(w.g3, **EXPANDED))
        same = answered(
            client, f"{NETWORK}?seeds=entity:{w.g3.pk + 1_000_000}&expand=reference"
        )
        assert status == same[0] == 200
        assert json.loads(content) | {"query": None} == json.loads(same[1]) | {
            "query": None
        }
        assert (json.loads(content)["nodes"], json.loads(content)["edges"]) == ([], [])
        assert queries == same[2] == counted(client, network_url(w.g3)) == 4 + BLOCK


# ── The joins and the caps ──────────────────────────────────────────────────


def test_the_joins_follow_the_ranked_edges_outside_max_edges(registry, visitor_client):
    w = reference_world()
    body = network(visitor_client, w.g1, **EXPANDED, max_edges="1")
    assert edge_ids(body) == refs("relationship", w.phys, w.enc1, w.iso)
    assert body["page"] == {
        "truncated": True,
        "max_edges": 1,
        "omitted": {"edges": 1},
        "hidden": NOTHING_HIDDEN,
    }
    assert body["warnings"] == [
        warning("edges_truncated", "1 ranked edges omitted: max_edges is 1.")
    ]
    assert ids(body["nodes"]) == refs("entity", w.g1, w.e, w.i, w.p)


def test_a_gene_and_its_entry_named_together_are_joined_by_their_link(
    registry, visitor_client
):
    w = reference_world()
    body = network(visitor_client, w.gp, w.p, **EXPANDED)
    assert ref("relationship", w.encp) in edge_ids(body)
    assert derived(body) == {}


def test_a_link_through_an_entry_the_cap_dropped_is_not_drawn(registry, visitor_client):
    m, u = mapping_release(), uniprot_release()
    g = entity_of("gene", SYNTHETIC_TAXON, created_by_release=m.release)
    entries = [
        entity_of("protein", SYNTHETIC_TAXON, created_by_release=u.release)
        for _ in range(26)
    ]
    i = entity_of("protein_isoform", SYNTHETIC_TAXON, created_by_release=u.release)
    for entry in entries:
        state(m, link("encodes", g, entry), evidence_basis=EvidenceBasis.UNKNOWN)
    iso = link("isoform_of", i, entries[-1])
    state(u, iso, evidence_basis=EvidenceBasis.UNKNOWN)
    for ev in (m, u):
        advance(ev.release, PUBLISHED)
    body = network(visitor_client, g, i, **EXPANDED)
    nodes = set(ids(body["nodes"]))
    # 24 derived seeds beside the two named: the cap drops the last two entries.
    assert body["page"]["omitted"]["derived_seeds"] == 2
    assert ref("entity", entries[-1]) not in nodes
    assert all(e["source"] in nodes and e["target"] in nodes for e in body["edges"])
    assert ref("relationship", iso) not in edge_ids(body)


def test_the_closure_ignores_the_requests_filters_and_its_joins_stay_shown(
    registry, visitor_client
):
    w = reference_world()
    for parameters, ranked, nodes, held, warned in (
        (
            {"evidence_basis": "experimental"},
            (w.phys, w.reg1),
            (w.g1, w.e, w.i, w.p, w.t),
            NOTHING_HIDDEN,
            [],
        ),
        (
            {"outcome": "tested_absent"},
            (),
            (w.g1, w.e, w.i),
            hidden(supported=2),
            [hidden_warning("supported", 2)],
        ),
        (
            {"direction": "out"},
            (w.phys, w.reg1),
            (w.g1, w.e, w.i, w.p, w.t),
            NOTHING_HIDDEN,
            [],
        ),
        ({"taxon": "9606"}, (), (w.g1, w.e, w.i), NOTHING_HIDDEN, []),
        ({"layers": TR}, (w.reg1,), (w.g1, w.e, w.i, w.t), NOTHING_HIDDEN, []),
    ):
        body = network(visitor_client, w.g1, **EXPANDED, **parameters)
        assert edge_ids(body) == refs("relationship", *ranked, w.enc1, w.iso)
        assert ids(body["nodes"]) == refs("entity", *nodes)
        assert body["page"]["hidden"] == held
        assert body["warnings"] == warned
        assert all(edge["shown"] for edge in body["edges"])
        assert by_id(body)[ref("relationship", w.enc1)]["by_basis"] == {"unknown": 1}
        assert derived(body) == {
            ref("entity", w.e): [via(w.g1, w.enc1)],
            ref("entity", w.i): [via(w.g1, w.enc1, w.iso)],
        }


def test_the_closure_reads_the_selections_releases(registry, visitor_client):
    w = reference_world()
    scoped = instance(w.uniprot, w.evidence)
    for selection in ({"browser": scoped.slug}, pin(w.uniprot, w.evidence)):
        # The mapping release is not selected, so g1 follows no link.
        body = network(visitor_client, w.g1, **EXPANDED, **selection)
        assert edge_ids(body) == refs("relationship", w.reg1)
        assert derived(body) == {}
        body = network(visitor_client, w.e, **EXPANDED, **selection)
        assert edge_ids(body) == refs("relationship", w.phys, w.iso)
        assert derived(body) == {ref("entity", w.i): [via(w.e, w.iso)]}
        if "browser" in selection:
            assert body["query"]["browser"] == scoped.slug
        else:
            assert body["query"]["releases"] == refs("release", w.uniprot, w.evidence)
    every = instance(w.mapping, w.uniprot, w.evidence)
    body = network(visitor_client, w.g1, **EXPANDED, browser=every.slug)
    assert edge_ids(body) == refs("relationship", w.phys, w.reg1, w.enc1, w.iso)


def test_an_entry_past_the_cap_reports_the_derived_seeds_it_dropped(
    registry, visitor_client, monkeypatch
):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    c = capped_world()
    body = network(visitor_client, c.e, **EXPANDED)
    assert body["page"] == {
        "truncated": True,
        "max_edges": DEFAULT_MAX_EDGES,
        "omitted": {"derived_seeds": 6},
        "hidden": NOTHING_HIDDEN,
    }
    assert body["warnings"] == [
        warning(
            "derived_seeds_truncated",
            "6 derived seeds omitted: a network looks up at most 26 seeds, named "
            "and derived together, or a step of the closure reached its row bound.",
        )
    ]
    assert set(derived(body)) == set(refs("entity", c.g, *c.isoforms[:24]))
    assert set(refs("entity", *c.isoforms[24:])).isdisjoint(ids(body["nodes"]))
    assert edge_ids(body) == refs(
        "relationship", c.phys, *c.regulation, c.enc, *c.isoform_of[:24]
    )
    assert marks_name_what_is_listed(body)
    assert counted(visitor_client, network_url(c.e, **EXPANDED)) == 44 + BLOCK


def test_a_derived_seeds_cut_lookup_gets_its_own_warning(registry, visitor_client):
    m, r = mapping_release(), synthetic_release(*OPEN_SCOPES)
    gene = entity_of("gene", SYNTHETIC_TAXON, created_by_release=m.release)
    entry = entity_of("protein", SYNTHETIC_TAXON, created_by_release=m.release)
    state(m, link("encodes", gene, entry), evidence_basis=EvidenceBasis.UNKNOWN)
    fan_out(gene, CANDIDATES_PER_SEED + 1, r)
    for ev in (m, r):
        advance(ev.release, PUBLISHED)
    body = network(visitor_client, entry, **EXPANDED)
    assert body["page"] == {
        "truncated": True,
        "max_edges": DEFAULT_MAX_EDGES,
        "omitted": {"candidates": 1},
        "hidden": NOTHING_HIDDEN,
    }
    assert body["warnings"] == [
        warning(
            "seed_truncated",
            f"{ref('entity', gene)}: more than {CANDIDATES_PER_SEED} relationships "
            f"have covered evidence; the {CANDIDATES_PER_SEED} most covered were "
            "ranked.",
        )
    ]
    assert len(body["edges"]) == CANDIDATES_PER_SEED + 1
    assert body["edges"][-1]["type"] == "encodes"


def test_a_cut_closure_statement_is_counted_and_warned(
    registry, visitor_client, monkeypatch
):
    c = capped_world()
    monkeypatch.setattr(graph.reference_closure, "CLOSURE_ROW_LIMIT", 2)
    body = network(visitor_client, c.e, **EXPANDED)
    assert body["page"]["omitted"] == {"derived_seeds": 1}
    assert body["page"]["truncated"] is True
    assert body["warnings"] == [
        warning(
            "derived_seeds_truncated",
            "1 derived seeds omitted: a network looks up at most 26 seeds, named "
            "and derived together, or a step of the closure reached its row bound.",
        )
    ]
    assert derived(body) == {
        ref("entity", c.g): [via(c.e, c.enc)],
        ref("entity", c.isoforms[0]): [via(c.e, c.isoform_of[0])],
    }


# ── What a request costs ────────────────────────────────────────────────────


def test_a_factory_expanded_request_costs_the_enumerated_statements(
    registry, visitor_client
):
    w = reference_world()
    assert counted(visitor_client, network_url(w.g1)) == 15 + BLOCK
    assert counted(visitor_client, network_url(w.g1, **EXPANDED)) == 22 + BLOCK
    assert counted(visitor_client, network_url(w.e, **EXPANDED)) == 23 + BLOCK
    assert counted(visitor_client, network_url(w.i, **EXPANDED)) == 24 + BLOCK
    # A seed with no visible reference link.
    assert (
        counted(visitor_client, network_url(w.t, **EXPANDED))
        == counted(visitor_client, network_url(w.t)) + 1
        == 16 + BLOCK
    )


def test_every_filter_kind_with_expand_costs_the_enumerated_statements(
    registry, visitor_client, monkeypatch
):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    w = reference_world(filtered=True)
    filtered = {
        "evidence_basis": "experimental",
        "method": "MI:0018",
        "score": "stats:test_value::",
        "taxon": str(SYNTHETIC_TAXON),
    }
    assert counted(visitor_client, network_url(w.g1, **filtered)) == 17 + BLOCK
    body = network(visitor_client, w.g1, **EXPANDED, **filtered)
    assert edge_ids(body) == refs("relationship", w.phys, w.reg1, w.enc1, w.iso)
    assert all(edge["shown"] for edge in body["edges"])
    assert (
        counted(visitor_client, network_url(w.g1, **EXPANDED, **filtered)) == 24 + BLOCK
    )


def test_the_bound_holds_at_the_cap_with_every_step_sent(
    registry, visitor_client, monkeypatch, django_assert_max_num_queries
):
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 60_000)
    c = capped_world()
    for seeds in ([c.g], [c.g, c.e, *c.targets[1:]]):
        url = network_url(*seeds, **EXPANDED, include_partner_edges="true")
        with django_assert_max_num_queries(46 + BLOCK) as captured:
            response = visitor_client.get(url)
        assert response.status_code == 200
        # The service's bound, 37 + BLOCK, and the route's nine.
        assert len(captured) == 46 + BLOCK


# ── The deadline and the budget ─────────────────────────────────────────────


def test_a_closure_past_its_deadline_is_a_503_and_spends_the_budget(
    registry, staff_client, monkeypatch
):
    w = reference_world()
    rows = graph.reference_closure._closure_rows
    calls, sent = [], []

    def slow(arms, **kwargs):
        calls.append(arms)
        time.sleep(0.05)
        return rows(arms, **kwargs)

    def recorded(name):
        function = getattr(graph.neighbourhood, name)

        def record(*args, **kwargs):
            sent.append(name)
            return function(*args, **kwargs)

        return record

    monkeypatch.setattr(graph.reference_closure, "_closure_rows", slow)
    for name in ("summarize", "_candidate_rows", "_hidden_counts", "_partner_rows"):
        monkeypatch.setattr(graph.neighbourhood, name, recorded(name))
    monkeypatch.setattr(graph.neighbourhood, "STATEMENT_TIMEOUT_MS", 20)
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "1/min"}
    )
    timed_out(staff_client.get(network_url(w.g1, **EXPANDED)))
    assert len(calls) <= 1
    assert sent == []
    assert len(history("graph_network_site", "site")) == 1


def test_an_expanded_request_spends_one_unit_of_the_site_budget(
    registry, visitor_client, monkeypatch
):
    w = reference_world()
    monkeypatch.setattr(
        GraphNetworkBudgetThrottle, "THROTTLE_RATES", {"graph_network_site": "2/min"}
    )
    network(visitor_client, w.g1, **EXPANDED)
    assert len(history("graph_network_site", "site")) == 1
    network(visitor_client, w.g1)
    assert len(history("graph_network_site", "site")) == 2
    response = visitor_client.get(network_url(w.g1, **EXPANDED))
    assert response.status_code == 429
    assert response.json()["detail"].startswith(
        "The portal's network budget is spent for the moment."
    )


# ── The demonstration ───────────────────────────────────────────────────────


def demo_rows(demo):
    """5.4's demonstration rows, built the same way with stored."""
    rows = {
        name.lower().replace("syn-", "").replace("-", "_"): demo.entity(name)
        for name in ("SYN-TF1", "SYN-G1", "SYN-G4", "SYN-P1", "SYN-P1-1", "SYN-P2")
    }
    rows.update(
        encodes=stored(demo, "encodes", "SYN-TF1", "SYN-P1"),
        isoform_of=stored(demo, "isoform_of", "SYN-P1-1", "SYN-P1"),
        pair=stored(demo, PI, "SYN-P1", "SYN-P2"),
        forward=stored(demo, TR, "SYN-TF1", "SYN-G1"),
        backward=stored(demo, TR, "SYN-G1", "SYN-TF1"),
        unknown=stored(demo, TR, "SYN-TF1", "SYN-G4"),
    )
    return rows


def test_syn_tf1s_gene_entry_and_isoform_are_three_nodes_joined_by_labelled_links(
    demo, visitor_client
):
    d = demo_rows(demo)
    tf1, g1, g4, p1, p1_1, p2 = (d[k] for k in ("tf1", "g1", "g4", "p1", "p1_1", "p2"))
    encodes, isoform_of, pair = d["encodes"], d["isoform_of"], d["pair"]
    regulation = sorted(
        (d["backward"], d["forward"], d["unknown"]), key=lambda row: row.pk
    )
    v2 = ref("release", demo.v2)
    nodes = refs("entity", *sorted((g1, g4, tf1, p1, p1_1, p2), key=lambda row: row.pk))
    edges = refs("relationship", pair, *regulation, encodes, isoform_of)
    body = network(demo.staff, tf1, **EXPANDED)
    assert ids(body["nodes"]) == nodes
    assert [node["id"] for node in body["nodes"] if node["is_seed"]] == [
        ref("entity", tf1)
    ]
    assert derived(body) == {
        ref("entity", p1): [via(tf1, encodes)],
        ref("entity", p1_1): [via(tf1, encodes, isoform_of)],
    }
    assert edge_ids(body) == edges
    assert [(e["type"], e["family"], e["releases"]) for e in body["edges"][-2:]] == [
        ("encodes", "reference", [v2]),
        ("isoform_of", "reference", [v2]),
    ]
    assert ids(body["releases"]) == [v2]
    assert body["page"]["hidden"] == hidden(tested_absent=1)
    assert body["warnings"] == [hidden_warning("tested_absent", 1)]
    for seed, marks in (
        (
            p1,
            {
                ref("entity", tf1): [via(p1, encodes)],
                ref("entity", p1_1): [via(p1, isoform_of)],
            },
        ),
        (
            p1_1,
            {
                ref("entity", tf1): [via(p1_1, isoform_of, encodes)],
                ref("entity", p1): [via(p1_1, isoform_of)],
            },
        ),
    ):
        body = network(demo.staff, seed, **EXPANDED)
        assert (ids(body["nodes"]), edge_ids(body)) == (nodes, edges)
        assert derived(body) == marks
    body = network(demo.staff, p2, **EXPANDED)
    assert body | {"query": None} == network(demo.staff, p2) | {"query": None}
    seen = network(visitor_client, tf1, **EXPANDED)
    assert (seen["nodes"], seen["edges"], seen["releases"], seen["warnings"]) == (
        [],
        [],
        [],
        [],
    )
    assert seen["page"] == NOT_CUT
    assert seen["query"]["expand"] == "reference"


def test_syn_tf1_pinned_to_v1_keeps_its_joins_flagged(demo):
    d = demo_rows(demo)
    regulation = sorted(
        (d["backward"], d["forward"], d["unknown"]), key=lambda row: row.pk
    )
    v1 = ref("release", demo.v1)
    body = network(demo.staff, d["tf1"], **EXPANDED, **pin(demo.v1))
    assert edge_ids(body) == refs(
        "relationship", d["pair"], *regulation, d["encodes"], d["isoform_of"]
    )
    assert [edge["releases"] for edge in body["edges"]] == [[v1]] * 6
    assert [(r["id"], r["status"], r["superseded"]) for r in body["releases"]] == [
        (v1, "superseded", True)
    ]
    assert by_id(body)[ref("relationship", d["forward"])]["effect"] == "activating"
    assert body["query"]["releases"] == [v1]


def test_the_demonstrations_expanded_requests_cost_the_enumerated_statements(demo):
    d = demo_rows(demo)
    tf1, p1, p1_1, p2 = d["tf1"], d["p1"], d["p1_1"], d["p2"]
    assert counted(demo.staff, network_url(tf1)) == 15 + BLOCK
    assert counted(demo.staff, network_url(tf1, **EXPANDED)) == 22 + BLOCK
    assert counted(demo.staff, network_url(p1, **EXPANDED)) == 21 + BLOCK
    assert counted(demo.staff, network_url(p1_1, **EXPANDED)) == 22 + BLOCK
    assert counted(demo.staff, network_url(p2, **EXPANDED)) == 16 + BLOCK
    assert counted(demo.staff, network_url(p2)) == 15 + BLOCK
    assert counted(demo.staff, network_url(tf1, **EXPANDED, outcome="all")) == (
        21 + BLOCK
    )
    assert counted(demo.staff, network_url(tf1, **EXPANDED, **pin(demo.v1))) == (
        22 + BLOCK
    )
    assert counted(APIClient(), network_url(tf1, **EXPANDED)) == 4 + BLOCK
