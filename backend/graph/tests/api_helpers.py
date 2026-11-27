"""What the tests of /api/graph/v1/ share. pytest does not collect this file:
its name does not start with test_.
"""

import copy
import datetime
import itertools
import re
from types import SimpleNamespace
from urllib.parse import urlencode

from django.db import connection
from django.urls import URLResolver, get_resolver
from django.utils import timezone

from browsers.models import (
    BrowserInstance,
    BrowserInstancePublication,
    BrowserInstanceRelease,
)
from browsers.rules import validate_facets, validate_selection
from graph.models import (
    SYNTHETIC_TAXON,
    ApprovalScope,
    AssertionMethod,
    AssertionParticipant,
    Entity,
    EntityType,
    EvidenceAssertion,
    EvidenceBasis,
    InteractionLevel,
    Outcome,
    ParticipantSide,
    Relationship,
    RelationshipType,
    ReleaseKind,
    ResolutionBasis,
    SourceRecord,
)
from graph.releases import discard_release, withdraw_release
from graph.stats import release_stats
from graph.tests.factories import (
    FAILED_CRITERION,
    AssertionParticipantFactory,
    EvidenceAssertionFactory,
    EvidenceContextFactory,
    EvidenceMethodFactory,
    EvidenceScoreFactory,
    RelationshipFactory,
    ReleaseLicenceFactory,
    SourceRecordFactory,
    approvable_release,
    approve,
    entity_of,
    evidence_release,
    mapped_record,
)
from graph.tests.test_provenance import PUBLISHED, STAGED, SUPERSEDED, advance

BASE = "/api/graph/v1/"
STATUS = f"{BASE}status"
VOCABULARIES = f"{BASE}vocabularies"
RESOLVE = f"{BASE}resolve"
ENTITY = f"{BASE}entities/{{entity_id}}"
NETWORK = f"{BASE}network"
RELATIONSHIP = f"{BASE}relationships/{{relationship_ref}}"
EVIDENCE = f"{RELATIONSHIP}/evidence"
BROWSERS = f"{BASE}browsers"
BROWSER = f"{BASE}browsers/{{slug}}"
# Every path a test names by hand. typed_routes() finds what is served.
PATHS = {
    STATUS,
    VOCABULARIES,
    RESOLVE,
    ENTITY,
    NETWORK,
    RELATIONSHIP,
    EVIDENCE,
    BROWSERS,
    BROWSER,
}

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def resolve_url(*terms, **parameters) -> str:
    """The resolve request for these terms, as one value of q."""
    return f"{RESOLVE}?{urlencode({'q': ','.join(terms), **parameters})}"


def entity_url(entity, **parameters) -> str:
    """The page of this row."""
    url = f"{BASE}entities/entity:{entity.pk}"
    return f"{url}?{urlencode(parameters)}" if parameters else url


def network_url(*seeds, **parameters) -> str:
    """The network around these rows, as one value of seeds. A parameter
    given a list is repeated, as score is."""
    seeds = ",".join(f"entity:{seed.pk}" for seed in seeds)
    return f"{NETWORK}?{urlencode({'seeds': seeds, **parameters}, doseq=True)}"


def relationship_url(relationship, **parameters) -> str:
    """The page of this row. A parameter given a list is repeated."""
    url = f"{BASE}relationships/relationship:{relationship.pk}"
    return f"{url}?{urlencode(parameters, doseq=True)}" if parameters else url


def evidence_url(relationship, **parameters) -> str:
    """The evidence list of this row."""
    url = f"{BASE}relationships/relationship:{relationship.pk}/evidence"
    return f"{url}?{urlencode(parameters, doseq=True)}" if parameters else url


def browsers_url(**parameters) -> str:
    """The gallery under these filters."""
    return f"{BROWSERS}?{urlencode(parameters)}" if parameters else BROWSERS


def browser_url(slug) -> str:
    """The page of the instance this slug names, as given."""
    return f"{BASE}browsers/{slug}"


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
    NETWORK: [
        lambda world: network_url(world.h),
        lambda world: network_url(world.s),
        lambda world: network_url(world.v),
        lambda world: network_url(world.v, layers="encodes"),
        lambda world: network_url(world.h, layers="family:reference"),
        lambda world: network_url(
            world.h, world.s, world.v, outcome="all", include_partner_edges="true"
        ),
        lambda world: network_url(world.v, expand="reference"),
        lambda world: network_url(world.h, expand="reference"),
        lambda world: network_url(
            world.h,
            world.s,
            world.v,
            expand="reference",
            outcome="all",
            include_partner_edges="true",
        ),
    ],
    RELATIONSHIP: [
        lambda world: relationship_url(world.hidden_edges[0]),
        lambda world: relationship_url(world.hidden_edges[1]),
        lambda world: relationship_url(world.both_state),
    ],
    EVIDENCE: [
        lambda world: evidence_url(world.hidden_edges[0]),
        lambda world: evidence_url(world.hidden_edges[1]),
        lambda world: evidence_url(world.both_state),
    ],
    BROWSERS: [
        lambda world: BROWSERS,
        lambda world: browsers_url(availability="metadata_only"),
        lambda world: browsers_url(q=world.instance.title),
    ],
    BROWSER: [
        lambda world: browser_url(world.instance.slug),
        lambda world: browser_url(world.staff_instance.slug),
    ],
}
# The query a served path needs to answer at all, so that a test adding one
# parameter tests only that.
REQUIRED = {RESOLVE: "q=x", NETWORK: "seeds=entity:1"}


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

    return re.sub(r"\b(entity|relationship|release|evidence):(\d+)", numbered, text)


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


def state(ev, relationship, *, record=None, participants=None, **fields):
    """An assertion of a release that is still a draft, with its participants,
    which the assertion's factory does not write. `ev` is evidence_release's.
    `record` is a mapped_record() row to state from instead of a fresh one,
    for a large raw record or a release that stores none; `participants`
    (side, entity, stated_entity, resolution_basis) tuples written in the
    given order, the relationship's subject then its object unless given."""
    made = EvidenceAssertionFactory(
        relationship=relationship,
        release=ev.release,
        import_run=ev.run,
        source_record=record or mapped_record(ev.release, ev.run),
        **fields,
    )
    if participants is None:
        AssertionParticipantFactory(assertion=made, side=ParticipantSide.SUBJECT)
        AssertionParticipantFactory(assertion=made, side=ParticipantSide.OBJECT)
        return made
    for side, entity, stated_entity, basis in participants:
        AssertionParticipantFactory(
            assertion=made,
            side=side,
            entity=entity,
            stated_entity=stated_entity,
            resolution_basis=basis,
        )
    return made


# Tags one fan_out call's rows, so several calls write distinct keys.
_FAN_OUTS = itertools.count()


def fan_out(seed, n, ev, *, assertions_per_edge=1, outcome=Outcome.SUPPORTED):
    """The bulk fixture of a high-degree seed: `n` partners of `seed`, a
    transcriptional_regulation relationship from the seed to each,
    `assertions_per_edge` assertions of `outcome` on each in `ev`'s draft
    release (evidence_release's), and both participants of every assertion,
    in a fixed number of statements whatever `n`. Returns the relationships
    in partner order.

    The assertions go through the base manager, since the guarded one refuses
    bulk_create; they are valid by construction, and the guard's derive()
    sets nothing an assertion has. The participants go through the guarded
    manager, and make the seed and the partners visible once the release is
    published. On PostgreSQL the two tables the ranking reads are then
    ANALYZEd: rows bulk-loaded inside a test transaction carry no statistics,
    and without them the planner reads the (release, outcome) index, hundreds
    of times slower. The release stays a draft, so a test may call this
    several times before it publishes with advance(ev.release, PUBLISHED)."""
    tag = next(_FAN_OUTS)
    gene = EntityType.objects.get(slug="gene")
    regulation = RelationshipType.objects.get(slug="transcriptional_regulation")
    partners = Entity.objects.bulk_create(
        [
            Entity(
                entity_type=gene,
                identity_key=f"fan||{tag}|{index}",
                label=f"FAN-{tag}-{index}",
                label_key=f"FAN-{tag}-{index}",
                taxon_id=seed.taxon_id,
            )
            for index in range(n)
        ],
        batch_size=1000,
    )
    relationships = Relationship.objects.bulk_create(
        [
            Relationship(
                relationship_type=regulation, subject=seed, object=partner, ordered=True
            )
            for partner in partners
        ],
        batch_size=1000,
    )
    records = SourceRecord.objects.bulk_create(
        [
            SourceRecordFactory.build(
                release=ev.release,
                file=ev.file,
                import_run=ev.run,
                record_key=f"fan-{tag}-{index}-{repeat}",
                line_number=2 + index * assertions_per_edge + repeat,
            )
            for index in range(n)
            for repeat in range(assertions_per_edge)
        ],
        batch_size=1000,
    )
    assertions = EvidenceAssertion._base_manager.bulk_create(
        [
            EvidenceAssertion(
                relationship=relationships[index],
                release=ev.release,
                source_record=records[index * assertions_per_edge + repeat],
                import_run=ev.run,
                assertion_key="main",
                outcome=outcome,
                evidence_basis=EvidenceBasis.EXPERIMENTAL,
                assertion_method=AssertionMethod.MANUAL_ASSERTION,
                qualifiers=(
                    copy.deepcopy(FAILED_CRITERION)
                    if outcome == Outcome.NOT_SIGNIFICANT
                    else {}
                ),
            )
            for index in range(n)
            for repeat in range(assertions_per_edge)
        ],
        batch_size=1000,
    )
    AssertionParticipant.objects.bulk_create(
        [
            AssertionParticipant(
                assertion=assertion,
                release=ev.release,
                side=side,
                entity=entity,
                identifier_as_stated=entity.label,
                resolution_basis=ResolutionBasis.PRIMARY,
            )
            for assertion in assertions
            for side, entity in (
                (ParticipantSide.SUBJECT, seed),
                (ParticipantSide.OBJECT, assertion.relationship.object),
            )
        ],
        batch_size=1000,
    )
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE graph_relationship, graph_evidenceassertion")
    return relationships


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


# ── Reference releases and X1's worlds ──────────────────────────────────────
# A synthetic release of its own for each kind of link, so a test states
# encodes and isoform_of apart from the evidence, as RF1a's mapping and
# UniProt releases will (IMPLEMENTATION_PLAN.md, X1 and RF1a). A synthetic
# release states synthetic constructs only (graph.models, synthetic
# separation), so every entity of these worlds is of SYNTHETIC_TAXON.

# The scopes of a release that staff and visitors both see.
OPEN_SCOPES = (ApprovalScope.INTERNAL, ApprovalScope.PUBLIC_VIEW)
# The isoforms of capped_world's entry: more than MAX_EXPANDED_SEEDS reach it.
CAPPED_ISOFORMS = 30


def synthetic_release(*scopes, release_kind=ReleaseKind.EVIDENCE, **fields):
    """A synthetic draft of this kind, ready for the evidence a test states
    with state(). With scopes, an open-terms snapshot, then an active
    approval of each, recorded after its file: evidence_release(synthetic=True)
    records none, and a synthetic release needs one to be seen; only its due
    diligence is waived (graph.approvals). Other fields go to the release."""
    ev = evidence_release(records=0, synthetic=True, kind=release_kind, **fields)
    if scopes:
        ReleaseLicenceFactory(release=ev.release, open_terms=True)
        approve(ev.release, *scopes)
    return ev


def _reference_release(kind):
    """A synthetic draft of kind reference: approved for OPEN_SCOPES, or made
    as hidden_evidence makes one of HIDDEN_KINDS, for hide() to hide once its
    evidence is stated."""
    if kind == "unapproved":
        return synthetic_release(release_kind=ReleaseKind.REFERENCE)
    fields = {}
    if kind == "embargoed":
        fields["embargo_until"] = timezone.now() + datetime.timedelta(days=30)
    return synthetic_release(*OPEN_SCOPES, release_kind=ReleaseKind.REFERENCE, **fields)


def mapping_release(kind="approved"):
    """A synthetic gene-to-protein mapping release, of kind reference, for the
    encodes links a test states: approved for OPEN_SCOPES, or a draft of one
    of HIDDEN_KINDS for hide(). RF1a's route-level tests reuse it."""
    return _reference_release(kind)


def uniprot_release(kind="approved"):
    """A synthetic UniProt-style release, of kind reference, for the
    isoform_of links a test states, as mapping_release is."""
    return _reference_release(kind)


def reference_world(mapping="approved", *, staff=None, filtered=False):
    """X1's factory world (architecture §4.6). Mapping release M states that
    genes g1, g2 and g3 encode the protein entry e, and gene gp the protein p;
    UniProt-style release U that isoform i is an isoform of e; evidence
    release R that e and p interact, that g1 regulates t and g2 regulates t2.
    M and U state basis unknown, as reference evidence does. Each entity is a
    synthetic construct created by M (the genes), U (e, i and p) or R (t and
    t2), labelled as the factory labels it, and made in that order, so ids
    rise from g1 to t2; the relationships are made in the order of the
    namespace returned. `mapping` is M's state: approved, published;
    superseded, published and then superseded, so the default set leaves it
    out; or one of HIDDEN_KINDS, which hide() reaches with `staff`, the
    account that withdraws or discards. U and R are published. With
    `filtered`, each assertion R states also names the detection method
    MI:0018, a stats:test_value score of 0.7 and the tissue liver, which the
    EXISTS-backed filters match."""
    if mapping not in ("approved", "superseded", *HIDDEN_KINDS):
        raise ValueError(
            f"mapping is approved, superseded or a hidden kind, not {mapping!r}."
        )
    hidden = mapping in HIDDEN_KINDS
    m = mapping_release(mapping if hidden else "approved")
    u, r = uniprot_release(), synthetic_release(*OPEN_SCOPES)
    g1, g2, g3, gp = (
        entity_of("gene", SYNTHETIC_TAXON, created_by_release=m.release)
        for _ in range(4)
    )
    e, i, p = (
        entity_of(type_slug, SYNTHETIC_TAXON, created_by_release=u.release)
        for type_slug in ("protein", "protein_isoform", "protein")
    )
    t, t2 = (
        entity_of("gene", SYNTHETIC_TAXON, created_by_release=r.release)
        for _ in range(2)
    )
    enc1, enc2, enc3, encp = (
        link("encodes", gene, entry)
        for gene, entry in ((g1, e), (g2, e), (g3, e), (gp, p))
    )
    iso = link("isoform_of", i, e)
    phys = link("physical_interaction", e, p)
    reg1 = link("transcriptional_regulation", g1, t)
    reg2 = link("transcriptional_regulation", g2, t2)
    for edge in (enc1, enc2, enc3, encp):
        state(m, edge, evidence_basis=EvidenceBasis.UNKNOWN)
    state(u, iso, evidence_basis=EvidenceBasis.UNKNOWN)
    stated = [
        state(r, phys, interaction_level=InteractionLevel.NOT_STATED),
        state(r, reg1),
        state(r, reg2),
    ]
    if filtered:
        for made in stated:
            EvidenceMethodFactory(assertion=made)
            EvidenceScoreFactory(assertion=made, raw_value="0.7", value=0.7)
            EvidenceContextFactory(assertion=made, raw_value="liver")
    for ev in (u, r):
        advance(ev.release, PUBLISHED)
    if hidden:
        hide(m, mapping, staff)
    else:
        advance(m.release, SUPERSEDED if mapping == "superseded" else PUBLISHED)
    for ev in (m, u, r):
        ev.release.refresh_from_db()
    return SimpleNamespace(
        mapping=m.release,
        uniprot=u.release,
        evidence=r.release,
        g1=g1,
        g2=g2,
        g3=g3,
        gp=gp,
        e=e,
        i=i,
        p=p,
        t=t,
        t2=t2,
        enc1=enc1,
        enc2=enc2,
        enc3=enc3,
        encp=encp,
        iso=iso,
        phys=phys,
        reg1=reg1,
        reg2=reg2,
    )


def capped_world():
    """A world past MAX_EXPANDED_SEEDS. Mapping release M states that gene g
    encodes the entry e; UniProt-style release U that e has CAPPED_ISOFORMS
    isoforms; evidence release R that e interacts with p and that g regulates
    each of nine targets. From e the closure finds g and every isoform in one
    step; from g, e in one step and the isoforms in two. Ids rise from g to
    e, the isoforms in order, p and the targets; the relationships are made
    in the order of the namespace returned."""
    m, u = mapping_release(), uniprot_release()
    r = synthetic_release(*OPEN_SCOPES)
    g = entity_of("gene", SYNTHETIC_TAXON, created_by_release=m.release)
    e = entity_of("protein", SYNTHETIC_TAXON, created_by_release=u.release)
    isoforms = [
        entity_of("protein_isoform", SYNTHETIC_TAXON, created_by_release=u.release)
        for _ in range(CAPPED_ISOFORMS)
    ]
    p = entity_of("protein", SYNTHETIC_TAXON, created_by_release=u.release)
    targets = [
        entity_of("gene", SYNTHETIC_TAXON, created_by_release=r.release)
        for _ in range(9)
    ]
    enc = link("encodes", g, e)
    isoform_of = [link("isoform_of", isoform, e) for isoform in isoforms]
    phys = link("physical_interaction", e, p)
    regulation = [link("transcriptional_regulation", g, target) for target in targets]
    state(m, enc, evidence_basis=EvidenceBasis.UNKNOWN)
    for edge in isoform_of:
        state(u, edge, evidence_basis=EvidenceBasis.UNKNOWN)
    state(r, phys, interaction_level=InteractionLevel.NOT_STATED)
    for edge in regulation:
        state(r, edge)
    for ev in (m, u, r):
        advance(ev.release, PUBLISHED)
    return SimpleNamespace(
        mapping=m.release,
        uniprot=u.release,
        evidence=r.release,
        g=g,
        e=e,
        isoforms=isoforms,
        p=p,
        targets=targets,
        enc=enc,
        isoform_of=isoform_of,
        phys=phys,
        regulation=regulation,
    )


def with_stats(release):
    """The release with its stats recounted from its rows, as a commit writes
    them. They change only while it is a draft or staged."""
    release.stats = release_stats(release)
    release.save()
    return release


# ── Browser instances ───────────────────────────────────────────────────────

# Numbers the instances' slugs and titles, so each is distinct.
_INSTANCES = itertools.count()
# The many-to-many fields instance() sets after the row is saved.
_FACETS = ("taxa", "relationship_types", "entity_types", "assays")


def instance(
    *releases,
    kind="typed",
    status="available",
    visibility="public",
    slug=None,
    **fields,
):
    """A browser instance selecting these releases, in the order given, each
    of role primary, checked as every write path checks one: full_clean(),
    then validate_facets and validate_selection. taxa, relationship_types,
    entity_types and assays take rows; publications the papers of its header
    citation, in order. A public, available typed instance unless told
    otherwise."""
    number = next(_INSTANCES)
    facets = {name: list(fields.pop(name, ())) for name in _FACETS}
    papers = list(fields.pop("publications", ()))
    fields.setdefault("title", f"Instance {number}")
    row = BrowserInstance(
        slug=slug or f"instance-{number}",
        kind=kind,
        status=status,
        visibility=visibility,
        **fields,
    )
    row.full_clean()
    validate_facets(
        taxa=facets["taxa"], primary_taxon=row.primary_taxon, assays=facets["assays"]
    )
    validate_selection(row, releases)
    row.save()
    for name, values in facets.items():
        getattr(row, name).set(values)
    BrowserInstanceRelease.objects.bulk_create(
        BrowserInstanceRelease(instance=row, release=release, order=order)
        for order, release in enumerate(releases)
    )
    BrowserInstancePublication.objects.bulk_create(
        BrowserInstancePublication(instance=row, publication=paper, order=order)
        for order, paper in enumerate(papers)
    )
    return row
