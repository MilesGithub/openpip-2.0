# Test plan

Regression guards for what openPIP does today, and tests for what the
generalization adds. It goes with
[GENERALIZATION_ARCHITECTURE.md](GENERALIZATION_ARCHITECTURE.md), and uses the
issue ids in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

**Status: proposed (Phase 0).** The baseline in section 2 was measured. The
tests in sections 4 and 5 are planned, issue by issue.

## 1. Rules

- **Never against a live database.** Every run uses a throwaway PostgreSQL 16
  container on a private Docker network with no published ports. Nothing
  connects to the HuRI or YeRI stacks or to any existing volume.
- **Always set `DATABASE_URL`.** Without it, the settings fall back silently
  to `sqlite:///dev.db` (`backend/openpip/settings/base.py:89-91`). The
  `DATABASE_URL` key that `backend/pytest.ini` used to carry had no effect,
  and T1 (PR #1, merged as `b23119e`) removed it. Since T1:
  - pytest's header shows the database engine (`-q` hides the header);
  - a run with `CI` set stops unless the engine is PostgreSQL;
  - any other run on SQLite says so at the end.

  The backend command in section 3 also asserts the engine before pytest
  starts.
- **Drop the copied `.env`.** The commands copy the checkout, and with it the
  git-ignored root `.env`. `backend/openpip/settings/base.py:11` reads that
  file for every variable the command does not set, so a developer's
  `TYPED_GRAPH_ENABLED=1` there would turn the switch on in the test run. The
  commands delete the copy before running anything.
- **Run from a full checkout.** The docs contract tests skip themselves unless
  `docs/` sits next to `backend/`
  (`backend/core/tests/test_docs_contract.py:17-20`). A run inside the backend
  image alone misses them. Since T1, they fail instead of skipping when
  `CI` is set.
- **Leave the parity suite alone.** `pytest -m parity` skips test-database
  creation, reads whatever `DATABASE_URL` points at, and calls the live legacy
  site (`backend/tests/parity/conftest.py`). It stays deselected
  (`backend/pytest.ini`) and is not part of this programme. Since T1,
  `backend/conftest.py` marks every test under `tests/parity` as parity, and
  a run with `CI` set stops if it selects one.
- **Frontend on Node 20.** Vite 8 and Vitest 4 need Node 20.19 or later, and
  the development host runs Node 18. Under CPU contention 27 tests time out
  against their 5 s limit, and they pass when re-run (assessment §2). CI runs
  Vitest one file at a time with 20 s per test. During T1's verification, one
  test still overran 5 s serially in a 2-CPU container, and passed on the next
  run.
- **One heavy job at a time.** On a workstation that also hosts the portal
  stacks, run the backend, frontend and documentation jobs one after another.
  Running them together overloaded Docker Desktop's API during T1's
  verification and cut off a download; no other container was affected.
- **Every PR runs everything.** That means its new tests, the full backend and
  frontend suites, `ruff`, `black --check`, `makemigrations --check` and
  `mkdocs build --strict`. Since T1, CI runs them on every push and pull
  request.

## 2. Baseline

Measured on `main` at `94345c3`, 2026-10-28 (assessment §2):

| Check | Result |
|---|---|
| Backend `pytest` (PostgreSQL 16) | 582 passed, 2 skipped, 80 parity deselected. The 2 skipped are the docs contract tests, which pass from a full checkout: **584 passing** |
| `ruff check .`, `black --check .` | Clean |
| `makemigrations --check` | No changes |
| Frontend lint and build (`tsc`) | Pass |
| Vitest | 582 tests. 27 timed out under CPU contention; all 10 affected files pass when re-run serially |
| `mkdocs build --strict` | Pass |

## 3. How to run the checks safely

This is the set-up T1's verification used: unique container names, an
internal network with no published ports and no route to the internet (the
backend suite needs none), and one job at a time. Run it from the repository
root in bash, because it uses bash arrays; on Git Bash for Windows, first
`export MSYS_NO_PATHCONV=1`. Each run copies a read-only mount, so tests never
write into the checkout.

```bash
NET=openpip-test-net; DB=openpip-test-db; REDIS=openpip-test-redis; IMG=openpip-test-backend
docker network create --internal "$NET"
docker run -d --name "$DB" --network "$NET" \
  -e POSTGRES_DB=openpip -e POSTGRES_USER=openpip -e POSTGRES_PASSWORD=openpip postgres:16
docker run -d --name "$REDIS" --network "$NET" redis:7-alpine
docker build -t "$IMG" backend/
docker exec "$DB" sh -c 'until pg_isready -h 127.0.0.1 -U openpip -q; do sleep 1; done'

# The key is at least 32 bytes, because login tokens are signed with it and
# PyJWT warns about shorter keys.
ENVS=(-e CI=true -e DJANGO_SETTINGS_MODULE=openpip.settings.dev
      -e DATABASE_URL=postgres://openpip:openpip@$DB:5432/openpip
      -e SECRET_KEY=test-only-signing-key-not-a-secret-0123456789
      -e REDIS_URL=redis://$REDIS:6379/0)

# Backend, on a copy of the working tree with docs/ beside backend/
docker run --rm --network "$NET" "${ENVS[@]}" -v "$PWD:/src:ro" "$IMG" sh -c '
  cp -r /src /repo && rm -f /repo/.env && cd /repo/backend \
  && python manage.py shell -c "from django.db import connection; print(connection.vendor); assert connection.vendor == \"postgresql\"" \
  && pytest -ra -p no:cacheprovider && ruff check . && black --check . \
  && python manage.py makemigrations --check --dry-run'

# Migration reversibility and registry sync, on the same scratch database (from G1 on)
docker run --rm --network "$NET" "${ENVS[@]}" -e TYPED_GRAPH_ENABLED=1 -v "$PWD:/src:ro" "$IMG" sh -c '
  cp -r /src /repo && rm -f /repo/.env && cd /repo/backend \
  && python manage.py migrate && python manage.py graph_sync_registry \
  && python manage.py graph_sync_registry --check && python manage.py migrate graph zero \
  && python manage.py migrate'

# Frontend, on a copy so node_modules never lands in the working tree
docker run --rm -v "$PWD:/src:ro" -e CI=true node:20-alpine sh -c '
  cp -r /src/frontend /work && cd /work && rm -rf node_modules dist && npm ci --no-audit --no-fund \
  && npm run lint && npx vitest run --no-file-parallelism --testTimeout=20000 && npm run build'

# Documentation
docker run --rm -v "$PWD:/src:ro" "$IMG" sh -c '
  mkdir -p /tmp/repo && cp -r /src/mkdocs.yml /src/docs /tmp/repo/ && cd /tmp/repo \
  && pip install -q -r docs/requirements.txt && mkdocs build --strict -d /tmp/site'

docker rm -f "$DB" "$REDIS"; docker network rm "$NET"
```

## 4. Regression guards for existing behaviour

The table lists each contract that the brief and the assessment (§6) name,
the tests that already cover it, and the guard this programme adds. A new
guard is written once and then runs in every later PR's default suite.

| Contract | Existing coverage | Added guard (issue) |
|---|---|---|
| `GET /api/search`: shape, ordering, `''` for missing text, query protein in `interactor_A` | `backend/interactions/tests/test_search_service.py` and `test_views.py`; the opt-in parity suite | `backend/tests/compat/test_legacy_contracts.py` (G1). It pins the shape in the default suite, and checks that responses are byte-identical before and after typed rows using the same strings are created |
| `POST /api/search/interactors`, `/api/proteins`, `/api/proteins/autocomplete`, `/api/proteins/{identifier}`, `/api/datasets`, `/api/counts` | Each app's `test_views.py` | The same file (G1): byte-identical with typed rows present |
| `GET /api/home/network` | `interactions/tests` | Key set only, because the endpoint samples with `ORDER BY RANDOM()` (G1) |
| PSICQUIC: MIQL subset, MITAB 2.5–2.8, column 36 from `negative`, round trips | `backend/psicquic/tests/` (`test_mitab.py`, `test_miql.py`, `test_negative_flag.py`, `test_upload_roundtrip.py`, `test_real_intact_file.py` and others) | Byte-identical `query?q=*&format=tab28` and `query/count` output with typed rows present (G1). Typed relationships never appear (G3) |
| Legacy importer rules: dataset found by name, pair reuse in either orientation, first writer's score | `backend/datasets/tests/test_upload_parser.py` (dataset by name, lines 141-168; first writer's score, lines 399-420), `test_upload.py` and `test_task.py`. Reuse in the reverse orientation is covered only by `backend/psicquic/tests/test_real_intact_file.py:49-55` | Unchanged. Legacy table row counts are asserted unchanged after every typed import (G4, G5) |
| Dataset downloads and `datasets.zip` | `backend/datasets/tests/test_download_public.py`, `test_views.py` | No typed code touches them, and the import fence keeps it that way (G1) |
| Saved networks; saved, shared and public views | `backend/interactions/tests/test_networks.py` (saved networks) and `backend/sharing/tests/test_views.py` (saved, shared and public views); the frontend sharing tests | Byte-identical `/api/networks` responses with typed rows present (G1). Every existing `ViewState` fixture restores exactly as before. A state without `kind` takes the legacy path, and the three view pages check `kind` before calling the search page (S1) |
| `/api/settings` keys | `backend/admin_panel/tests/test_views.py` | Unchanged. The organism settings live in the fork-owned `portal_profile` app with its own endpoint, unless upstream takes them (O1) |
| `/api/schema/` and `/api/docs/` render | `backend/core/tests/test_api_docs.py` | Still 200 with the typed switch at its default (off), so the switch gate never runs inside schema generation (A1) |
| Routes and environment variables documented | `backend/core/tests/test_docs_contract.py` | Runs from a full checkout in CI (T1). Every new route (one heading per method and path) and every new variable lands with its docs (G1, A1, …) |
| `migrate_legacy.py --reset` cannot reach typed data | none | Foreign-key fences from model metadata and from database constraint introspection: no foreign key from a new app into any pre-existing table except `user` (G1, over `NEW_APPS`; `browsers` joins in B1 and `portal_profile` in O1). If the bridge is built: a simulated reload (`TRUNCATE protein, dataset … CASCADE` on the scratch database) empties the bridge tables and no `graph` or `browsers` table. A re-sync then restores the same entity ids, and the approvals of the live-legacy releases whose dataset id and name both match (L1, on demand) |
| `migrate_legacy.py --reset` itself, which cannot load seven tables into today's schema; checked on a scratch database (architecture §6.3) | none | Against a scratch database only: a reset run from a small dump in the legacy schema loads every table, and a forced failure leaves every table as it was. A test fails when a table the script loads gains a NOT NULL column with no database default that its map does not fill (ML1, not started without approval) |
| Pre-existing tables are never modified or deleted | none | From G1, three checks. Every operation in a new app's migration is on the strict allow-list (`CreateModel`, `AddField`, `AddIndex`, `AddConstraint`) and none is on the deny-list, checked over `NEW_APPS` (B1 adds `browsers`, O1 `portal_profile`). A schema snapshot of every pre-existing table (columns, types, nullability, defaults, constraints, indexes) is identical before the new apps migrate forward, after they migrate back to zero, and after they migrate forward again; so are the row counts of the pre-existing apps' tables, while Django's bookkeeping tables change only by the new apps' rows. The round trip covers every app in `NEW_APPS`, in reverse dependency order. The pre-existing apps' migrations equal a pinned manifest |
| Migrations | `makemigrations --check` | New apps are schema-only and reversible: the suite migrates to zero and forward again, and a CI step on every push runs `migrate`, the registry sync, `graph_sync_registry --check` (which fails if the tables and the JSON differ), `migrate <app> zero` for each new app and `migrate` (G1 onward). There is no migration in `admin_panel` or any other shared app on the fork (O1). CI runs `makemigrations --check` after every upstream merge (T1) |
| With the switch off, nothing new is reachable | none | Every `/api/graph/v1/` route except `status` returns 404, parametrized over the URL resolver (A1). Typed commands refuse to write (G1 onward). Navbar and home page are unchanged by snapshot (F4, H1) |
| Legacy frontend: search page, network, panels, tables, admin, sharing | The existing Vitest suites | They run unchanged in every frontend PR. New pages are lazy routes. Legacy components change only where an issue names the change: the navigation links (F4, X1, C1), the new `adminNav.ts` items (AD1), the home-page hub section (H1), the `kind` check in the saved, shared and public view pages (S1), and the organism-specific links and enrichment request (O1). Each keeps today's behaviour with the switch off, the settings unset or a state without `kind`, checked by the snapshot and fixture tests named in the rows above and in §5.13 |

## 5. Tests for the new behaviour

Each group maps to the essential tests in brief §9.

### 5.1 Registries, identity and resolution (G1; later G3, G4, RF1a, P5)

- **Registry sync.** It is idempotent and has a dry run. It reports, and
  never deletes, removed entries, and refuses while the switch is off. On
  existing rows it updates only descriptive fields. It refuses a change to a
  field that stored rows depend on (a namespace's case policy, for example)
  while rows use it, and names them. Imports refuse when the database's
  registry version differs from the code's. Registry rows that data reference
  cannot be deleted (PROTECT).
- **At most one identity namespace per entity type and taxon.** No two global
  namespaces, and no two fixed to one taxon, define identity for one type.
  Human genes are created only from HGNC IDs. An NCBI Gene or Ensembl Gene
  value never creates a gene; it maps only through a cross-reference that an
  approved release states (RF1a). A gene in a taxon that no namespace covers,
  such as fly (7227) today, cannot be created at all. A value that an
  existing entity of the same type and taxon holds, in any role, never
  creates a second entity.
- **Isoforms.** `P04637-1` and `P04637-2` are `protein_isoform` entities,
  separate from the `P04637` entry. None is collapsed into the entry, and they
  are joined only by `isoform_of` links from a UniProt reference release
  (RF1a).
- **Uniqueness.**
  - One primary identifier per entity, and a primary identifier names at most
    one entity per (namespace, lookup, scope).
  - Cross-references, secondary accessions and aliases repeat freely and only
    produce candidates. That covers the UniProt split case and HGNC previous
    symbols.
  - `identity_key` makes creation idempotent, including from two concurrent
    transactions.
- **The four resolution statuses**, counted over distinct entities. One
  entity matched in several ways counts once. G1 creates the non-primary
  identifiers these tests need through factories.
  - One entity matched by an exact primary identifier, and no other entity,
    is `resolved`. An entity whose label equals its primary value, searched
    with no namespace, is still `resolved`.
  - One entity matched only in other ways (an alias, a cross-reference, a
    secondary accession or a label) is `candidate`, shown with its match
    kinds for confirmation. A symbol is always at best a `candidate`, because
    symbols never define identity. A label and an alias of one entity give
    one `candidate` with two matches.
  - Two or more distinct entities are `ambiguous`, grouped by entity type and
    taxon, whatever their match kinds. A value that is A's primary
    identifier and B's secondary accession is `ambiguous`, with A listed
    first, and never redirects. A symbol held in two organism-owned
    namespaces is `ambiguous` until a taxon is given, and then a `candidate`.
  - A term with no match is `unmatched`.
  - A label shared by a gene and a protein is `ambiguous`, never one entity.
  - Gene, transcript and protein are linked only by reference relationships
    (G3), never by sharing an identifier.
- **Imports never guess (G4).** A record maps only through `resolved`, a
  unique cross-reference or alias in a namespace the adapter declares, or a
  reviewed resolution file, and the dry-run report lists every record mapped
  through a candidate. A symbol maps only if it matches exactly one gene
  across the approved, previous and alias symbols of the loaded reference
  release. An `unmatched` identifier, or a candidate that nothing accepts,
  makes the record `unresolved`; an `ambiguous` one makes it `ambiguous`.
  Either way it is reported with its candidates and not imported. A gene
  identifier where the type needs a protein is never mapped through
  `encodes` (B4).
- **Case and version.**
  - A case-exact namespace keeps `h` and `H` (or `dl` and `Dl`) as distinct
    primary identifiers.
  - A single case-mismatched match there is a `candidate`, never `resolved`.
  - A case-insensitive namespace reports `case_exact: false`.
  - A single version mismatch is a `candidate`.
- **Prefixes.** An unknown prefix is unmatched, never re-typed as a gene name.
- **Taxa.** A taxon id must be positive. PSI-MI pseudo-taxa become
  `assay_system` context, never taxa (M1). With `Taxon.parent`, a strain
  matches its species without either being rewritten (RF1b).
- **Read-only resolver.** It writes nothing, and its query count is constant
  over 25 terms.
- **Indexes.** On PostgreSQL, with sequential scans turned off, `EXPLAIN`
  shows index use for a `label_key` prefix search and for the
  case-insensitive `lookup` match that finds case-mismatch candidates.
- **Identifier visibility (RF1a).** An alias or cross-reference that only an
  unapproved release states is neither matched by the release-filtered
  resolver nor returned by the function that lists an entity's visible
  identifiers; A1's routes use both. A primary identifier is visible exactly
  when its entity is. After RF1a migrates, the G6 entities still resolve by
  their primary identifiers, and existing identifiers get `origin =
  release`. A superseding reference release writes source rows for primary
  identifiers too, so its genes and proteins stay in the default set.
- **Merging (P5).** It is audited, and a merged entity answers 301.

### 5.2 Relationship identity (G3)

- Directed types give two relationships for A→B and B→A. Symmetric types give
  one, enforced by the `subject_id <= object_id` check.
- The same endpoints can carry several relationship types.
- A qualifier key distinguishes site-specific claims from site-agnostic ones
  (P2). Until then no type declares an identity qualifier, and a
  relationship with any key but `''` is refused (G3).
- Self-pairs are allowed only where the type allows them.
- Endpoint types are validated against the type: `encodes` runs from a gene
  to a protein, and `mirna_binding` from a mature miRNA (N2).
- An endpoint that is `merged` or `retired` is refused (G3).
- Two batches that create the same relationships in opposite orders never
  deadlock: missing relationships are created in canonical order (G3, on
  PostgreSQL).
- A registry sync and a commit that use the same relationship or score type
  serialize, and whichever locks second sees the other's result (G3, on
  PostgreSQL).

### 5.3 Evidence semantics (G3's summary module; G6 on the fixture; M1)

**The meanings of "negative", and results that are not significant**
(architecture §4.3, D17).

- Inhibitory effect, tested-absent outcome and negative genetic sign are
  separate fields.
- The check constraints reject an effect together with a genetic sign, and
  any effect or sign on an assertion that is not `supported`.
- A source effect written as "negative" is rejected.
- One encoding per polarity (G3): an effect only on types with effect
  polarity, a sign only on types with sign polarity, and `not_applicable` is
  never stored. A sign needs a score of a signed type with a null model on
  the same assertion, and one that disagrees with the score's value is
  refused.
- A measurement below the source's threshold is `not_significant`: it keeps
  its score, its measured sign in the score's raw value, the threshold and
  its statistics, carries no `genetic_sign`, is hidden by default, and never
  counts as support or as absence. It never becomes `tested_absent`, which
  needs an explicit negative result from the source (N1, B13). In G3 the
  threshold is the criterion the result failed, in `qualifiers["threshold"]`,
  and a result whose scores meet every stated criterion is refused.

**Basis and method.** Evidence basis and assertion method are independent
fields. A literature-curated experimental result is `experimental` plus
`manual_assertion`.

**Publications.**

- A row citing two papers gives two assertions, each with one `reported_in`
  publication.
- Co-referent identifiers of one paper in a row (`imex:…|pubmed:…`) give one
  publication, not two assertions.

**Summary rules** (architecture §4.5):

- `evidence_count` is the independent-evidence count of supporting
  assertions, and `assertion_count` counts every supporting assertion. A
  duplicate observation raises `assertion_count`, never `evidence_count`.
- Tested-absent and inconclusive assertions never feed the effect or sign
  summary.
- Physical edges carry the strongest stored `interaction_level` among the
  supporting assertions summarized, never a level derived from the PSI-MI
  term at read time. An association-only edge with one tested-absent
  direct-interaction row still reads "association", and an edge with only
  tested-absent evidence shows "tested for: …". A level that rests only on
  openPIP's assignment (B14) is flagged (`level_assigned`, G3), never at
  `not_stated`.
- The sets of architecture §4.5 (G3): a release set that
  `visible_release_ids` did not issue is refused, and the summary reads only
  assertions of the set's releases whose stored status is `published`,
  `superseded` or `staged`, so a stale set naming a release since withdrawn
  or returned to draft contributes nothing. Outcome counts are over every
  filter but the outcome filter, so a shown edge shows the evidence against
  it. Effect, sign and level filters leave other types' assertions alone;
  effect and sign filters also leave assertions that are not supported
  alone, since none carries either, so an effect or sign filter keeps the
  evidence against an edge in its outcome counts, and covers a requested
  outcome other than `supported`. Summaries are `None` without covered
  support.
- The effect summary is `mixed` only with both activating and inhibiting
  present. It is `activating` with activating plus unknown, and `unknown` when
  nothing is stated or every effect is unknown. `effect_counts` always
  separates `unknown` from `not_stated`.
- Under an evidence-level filter, the summary covers only the matching
  assertions, and the count outside the filter is reported.
- **Duplicates count once; independent experiments count separately.**
  Assertions are never merged or deleted. The independent-evidence count
  collapses only records of one observation:
  - two records with the same `observation_key` count once, and two with
    different keys count twice, even in one paper with one method (M1);
  - otherwise each (origin, detection methods, outcome) key counts once,
    where the origin is the `reported_in` publication or, without one, the
    source dataset. One experiment imported through two releases counts
    once, and both records remain. In G3 records of one origin and outcome
    whose method sets intersect, or of which one names none, are one
    observation, and so are records linked through others; disjoint methods
    in one paper count twice, as does one method named at two PSI-MI
    depths, until V1;
  - evidence without a publication is never merged across datasets, and a
    restatement in a later release of the same dataset does not count again;
  - one evidence group, scoped to its release, counts once;
  - the stated limit holds: a record with an observation key and a record of
    the same experiment without one count twice (M1);
  - the panel always shows the assertion count beside the independent count
    (F3);
  - two source datasets that cite one publication with the same methods give
    `evidence_count` 1, `assertion_count` 2 and distinct datasets 2.

**Scores** keep their raw value. NaN and infinity are rejected and never
emitted, and score filters never pool score types.

**Context.** An absent facet means "not stated"; no filter treats it as a
match for every context.

### 5.4 Provenance, idempotency and the release lifecycle (G2, G4, G5)

- **Uniqueness.** (dataset, version) is unique, and a known version with a
  different checksum is refused. (release, record key) is unique: re-importing
  the same release creates nothing, and the same key with different content in
  a committed release is an error.
- **Assertion keys** are content-derived. Reordering the claims a mapping
  extracts changes no key. In G3 idempotency is per record: recording a
  record's claims again is a no-op only when they give exactly its stored
  assertions, and one claim more or less, or the same key with other
  content, is refused (`ClaimConflict`) with nothing written. Keys come
  from each claim's key part and the identifiers the record states for its
  papers, so a publication gaining a PMID between two identical calls
  changes nothing. A staged release is re-imported under a new
  adapter or mapping version by discarding it back to draft and importing it
  again; a published release is never re-mapped in place.
- **Entity creation.** Without `--create-entities`, an import creates no
  entity, and its records stay `unresolved`. With it, entities from a
  non-reference release are `provisional`, and the dry run lists them first.
- **Resumable commits.** A commit killed midway, simulated by raising inside
  batch *n*, resumes from the checkpoint and ends with rows identical to an
  uninterrupted run. A release has at most one open commit run (`running` or
  `failed`), the one a rerun resumes (G2). A failed batch rolls back completely.
- **Race-safe shared rows.** Two releases that commit at once and share an
  entity, a pair and a publication create no duplicate or orphan rows, and
  neither fails a batch (G5). RF1a extends the test to a shared non-primary
  identifier. A record whose co-referent identifiers match
  two existing publications is attached by precedence, and the conflict is
  reported, not fatal.
- **Release statistics.** `DatasetRelease.stats` is written at commit and
  matches a recount.
- **Publishing.** `graph_publish_release` needs an effective `internal`
  approval and refuses without one. It also refuses to publish a release
  that supersedes one with an effective `public_view` unless the new release
  has one too, so a correction never hides public data. It does not need `public_view`:
  publishing an internal-only release makes it current for staff, and
  visitors still see nothing. These rules are G2's (`graph/releases.py`),
  tested there; G5 tests the commands that wrap them.
- **Supersession.** Publishing v2 marks v1 `superseded` in the same
  transaction, also when v2 is approved for internal use only. Withdrawing v2
  does not restore v1. v1's records, assertions
  and approvals stay readable on its own pages and when it is selected
  explicitly. A correction keeps v1's dataset and synthetic flag. One
  withdrawn before it was published frees v1, so a later correction names v1
  again; of two staged corrections naming v1, the second to be published is
  refused, and nothing changes (G2).
- **Withdraw and discard.** Withdrawing hides a release everywhere at once and
  keeps its rows. Discarding a staged release deletes its records and
  assertions and returns it to draft. It keeps the release row, its files,
  its licence snapshots, its import runs and its approval history, and it
  deletes no entity, relationship or primary identifier; `graph_check`
  reports provisional entities left without assertions.
- **Private files (G4).** The release file root is outside `MEDIA_ROOT` and
  `STATIC_ROOT`.
- **Synthetic is checked (G4).** A release registered with `--synthetic` is
  rejected if any identifier uses a non-synthetic namespace or taxon, or if
  it cites a PMID or DOI.

### 5.5 Licensing and visibility (G2, A1, A2, B1, C1, E1)

**Approvals.**

- With no decision recorded, a release is invisible to anonymous and non-staff
  callers.
- Approving view does not grant bulk download. Bulk is refused while the
  latest licence snapshot leaves redistribution or modification unknown or
  not permitted. Recording an approval is refused while the latest snapshot
  records no terms, `public_view` while its redistribution is not
  permitted, and either public scope without evidence; a denial is accepted
  (G2).
- Decisions are never edited. Revoking sets the revoked fields, once, and
  nothing else may change after insertion (CU1 later adds its confirmation
  fields to that list). A new decision is a new row with a rationale, and a
  second decision on a scope with an active one is refused until the first
  is revoked.
- `public_view` and `external_services` need an effective `internal`
  approval, and `public_bulk` an effective `public_view`. Revoking `internal`
  withdraws all three at once. Their decisions stay active, and are
  effective again once a new `internal` approval is recorded: revocation
  does not cascade (G2). Replacing a stale approval keeps the release
  visible throughout, and a replacement that is refused changes nothing.
- **Due diligence fails closed** (G2's approval service; G5's commands wrap
  it). For a non-synthetic release, every approval is refused unless the
  dataset's due-diligence record is complete, confirmed after the release's
  latest licence snapshot was recorded, and its terms are not marked
  time-limited, because approval expiry is deferred; a denial is accepted.
  Approve, then reopen or block the record or set the flag: the release
  becomes invisible at once, and no export or external call is allowed.
  Recording a newer snapshot reopens nothing (G2): approvals already
  recorded stay effective, bulk follows the newer snapshot at once, and a
  new approval needs due diligence confirmed again.
- Creating a due-diligence record, and every change to its status, flag,
  reviewer, review date, notes link or confirmation, writes one history row
  naming who made it, on every write path, the Django admin's included.
- Under embargo only `internal` is effective. The release is visible to staff
  only, and with an `external_services` approval recorded, nothing is sent
  to an outside service.
- Deleting the approver's account keeps the approval and its username
  snapshot.
- Deleting a release that has approvals is refused.
- A licence snapshot cannot be edited once recorded, whether or not an
  approval relies on it (G2). New terms become a new snapshot, and
  `graph_check` reports approvals whose snapshot is no longer the latest.
- With the switch off, the Django admin shows no typed record, to
  superusers too. With it on, it shows only the catalogue, licensing and
  registry records, with releases and approvals read-only (G2).

**Visibility.**

- A parametrized test walks every `/api/graph/v1/` route with an unapproved
  and a withdrawn release present. Nothing from them appears: no nodes, edges,
  evidence, counts, entity existence or catalogue counts.
- Staff preview needs an `internal` approval.
- **Default views use current releases.** With nothing pinned, superseded
  releases contribute nothing to the network, `resolve`, entity pages,
  relationship pages, evidence lists or the aggregate counts. With
  `releases=`, `browser=` (from B1) or a saved view that pins them, they are
  used and flagged. A corrected effect therefore never reads as `mixed` by
  default, in the network or in the relationship panel. For callers other
  than staff, a pinned set accepts only published or superseded releases,
  never a staged one. Staff may pin a staged release to view it, never to
  download it in bulk or send it out (G2).
- A dataset's status changes no release's visibility (G2).
- If the bridge is built: bridged legacy datasets are invisible in typed
  views until an approval with its own rationale is recorded, and show
  "changed since approval" once the dataset changes after it (L2, on demand).

**Exports (E1).** Exports return 403, naming the blocked releases, unless
every release in scope is bulk-approved. The manifest lists the releases with
checksums and adapter and mapping versions, each release's licence,
attribution and note of changes, and the approvals relied on. An export that
would combine a release whose licence snapshot has `share_alike` set to `yes`
or `unknown` with any other release is refused, naming it, while that release
alone exports with its own licence (C13). G2 tests both rules in
`bulk_export_refusals`, which E1 calls.

**Browser instances.** A typed instance computes as `live` only while every
selected release is published or superseded, and visible to the caller, so an
instance pinned to v1 stays live after v2 is published. Otherwise it computes
as `metadata_only`, shown as "data unavailable", as soon as a release is
withdrawn or an approval revoked.

**Enrichment (RF1b).** The typed enrichment action is not offered unless the
instance allows it and every visible release has an effective
`external_services` approval. With it off, no request reaches an outside
service (MSW assertion). Providers add descriptive fields only, never
identifiers.

### 5.6 API contract (A1, A2, B1, C1, E1)

- **Shapes.** Responses match the documented examples, checked by contract
  tests on the synthetic release.
- **Switch.** With the switch off, every route except `status` returns 404,
  and `/api/schema/` still returns 200.
- **Errors and caps.**
  - Unknown parameters or values give 400 with the allowed list.
  - Exceeding a cap gives 400.
  - Truncating to a cap sets `page.truncated` and a warning.
- **Ordering and paging.** Truncation is deterministic, and cursor pagination
  on evidence is stable across pages.
- **Direction.** A symmetric relationship matches a seed on either endpoint,
  whatever `direction` says. An `outcome` or `effect` filter ranks edges by
  the matching counts.
- **Gallery filters (B1, F4).** Each filter and the title search narrow
  `GET browsers`, filters combine, and unknown values give 400.
- **Resolution statuses (A1).** `resolve` returns `resolved`, `candidate`,
  `ambiguous` or `unmatched`, counted over distinct entities, for visible
  entities only. Each candidate is one entity with all its matches.
- **Entity routes (X1).** `/entities/:namespace/:value` redirects only on a
  `resolved` result. A single alias, label, case or version `candidate` is
  listed for confirmation, and an `ambiguous` result lists every candidate.
- **Following reference links (X1).** With `expand=reference`, a search for
  the SYN-TF1 gene returns its regulation edges and, through labelled
  `encodes` and `isoform_of` edges, the physical partner of its protein,
  with each derived seed marked by its path. With the flag off, the result
  equals plain depth 1. An `encodes` link from an unapproved or withdrawn
  mapping release adds nothing, while `isoform_of` links from the UniProt
  release stay. In the default set a superseded mapping release adds
  nothing; a view that pins it keeps the join, flagged. Partners' own
  reference links are not followed. The closure follows typed paths only:
  with one entry encoded by several genes (a factory case like histone H4),
  a search for one gene never makes the other genes derived seeds.
- **Query count.** It is bounded whatever the fan-out
  (`django_assert_max_num_queries`).
- **Statement timeout.** It fires on PostgreSQL (`postgres` marker), inside
  `transaction.atomic()`. Afterwards the same connection serves a legacy
  `/api/search` request with no timeout in force.
- **Throttles.** The scoped throttles and the site-wide network budget return
  429 at their rates.
- **Docs.** The docs contract passes with one heading per method and path,
  and drf-spectacular builds a schema without warnings for the new views.

### 5.7 Ingestion robustness (G4, G5, I1)

- **Invalid files** are reported per row, never fatal: wrong encoding, a
  missing header, unknown columns, oversized lines and fields, too many
  columns.
- **Malicious strings** are stored and rendered as text: HTML and script
  tags, CSV formula prefixes, path traversal in file names, extremely long
  identifiers.
- **Semantic rejections.** Each of these is rejected with a reason and a
  physical line number: direction mismatches, effects on symmetric types,
  unknown types, namespaces and taxa, and non-finite scores.
- **One committer.** Only one committing run is allowed per release; a
  concurrent second commit is refused by the partial unique constraint.
- **Celery (I1).** The payload is an id, never file content, and a worker
  restart resumes from the checkpoint.

### 5.8 Interaction types and the MITAB adapter (M1; V1 on demand)

- M1's explicit list maps each accepted PSI-MI interaction type to its D20
  level, and its tests pin the list. It is a subset of G3's crosswalk
  (`graph.terms.PHYSICAL_INTERACTION_LEVELS`), whose tests pin its anchors,
  MI:0195 at `direct_interaction`, and the terms it leaves out. Colocalization
  (MI:0403 and its subtree, which contains MI:2364 proximity) and genetic
  interaction (MI:2402 and its subtree, which contains MI:0208) never map to
  a physical type, and an unlisted type is rejected with a reason.
- Terms are never invented: an unknown CURIE is stored only with the source's
  own label, and is flagged. In G3 a term is recorded only by a commit that
  may record evidence, never by a dry run or an unapproved release; it
  keeps its first label, and a later label, or one that disagrees with the
  crosswalk's, is reported and never written. A crosswalk term keeps the
  crosswalk's label, and names the release that supplied it only when that
  release stated it; no write path names a release past draft. A label is
  shown (`term_label`) only while the release that supplied it is visible
  to the caller and not synthetic (D12).
- The record-level MITAB adapter (M1) gives one assertion per row and
  publication from the repository's IntAct sample
  (`backend/psicquic/tests/fixtures/intact_tab28_sample.tsv`), with no
  reciprocal collapse. The legacy round-trip tests stay green.
- M1 refuses a whole file if any row names `openPIP:openPIP` as its source
  database, so a portal's own PSICQUIC output is never re-imported, even
  mixed into another file.
- Every physical assertion stores the D20 level from its adapter's list
  (`interaction_level`), and `canonical_tsv_v1` has its own pinned list (G4).
  A level above `association` needs a stated interaction type, and
  `association` without one is assigned by openPIP and flagged (G3).
- If V1 is built: the loaded PSI-MI release gives the expected subtree
  (MI:0407 is under MI:0915, which is under MI:0914), and the crosswalk
  agrees with M1's list.

### 5.9 Rendering and accessibility (F2–F4, X1)

- **Arrowheads** follow the effect summary. Role-ordered non-causal types get
  a circle marker; symmetric types get no arrowhead.
- **Line styles.** Tested-absent edges are dashed and hidden by default.
  Predicted edges are dotted.
- **Multigraphs.** A→B and B→A are two curved edges, and self-loops render.
- **Ids.** String ids (`entity:12`, `ppi-interaction:42`) tap correctly.
- **Layout** re-runs only when the element id set changes, checked with a
  spy.
- **Physical edges** take their label from the strongest stored level that
  the API returns (A2). Edges with only association-level (MI:0914) evidence
  are labelled "association (co-purification)", never binding, and those
  with no stated level "physical interaction, level not stated".
- **Reference links** are drawn thin, grey and directed, each with a text
  label giving its type and the release that states it. Derived seeds carry
  a "via encodes" or "via isoform of" badge. The legend explains that
  `encodes` links come from a mapping release and `isoform_of` links from a
  UniProt release, and that gene, protein entry and isoform are separate
  records (X1).
- **Legend.** It lists only the channels present, in words, and colour is
  never the only channel.
- **Keyboard.** The edges table is fully usable from the keyboard, and opens
  the same panel.
- **Motion and layout.** `aria-live` announces truncation,
  `prefers-reduced-motion` disables layout animation, and the side panel
  collapses on narrow screens.
- **Text.** Source text with markup renders as literal text, and the
  synthetic badge appears wherever demo data are shown.
- **Gallery.** Its filters are kept in the URL, and a shared URL reproduces
  the filtered gallery (F4).

### 5.10 Saved and shared views (S1)

- Existing saved, shared and public view fixtures, which have no `kind`,
  render through the unchanged search page.
- Explorer states round-trip through save, share and the public link, with a
  non-empty display string in `query`.
- An unknown `kind` or version shows an upgrade notice.
- A pinned release that has since been superseded or withdrawn shows a
  banner.

### 5.11 Legacy bridge and federation (L1, L2; on demand)

These tests apply only if the bridge is built (architecture D11). Until then,
G1 and G3's legacy-contract tests are what keep HuRI and YeRI unchanged.

**Bridge sync.**

- It is idempotent.
- Duplicate legacy rows for one accession are reported and left unlinked:
  the two `P04637` rows, protein ids 5319 and 11911 (`docs/BUGS.md:201-211`).
- Rows without an accession, or with conflicting organisms, are reported and
  stay unlinked.
- `Protein.ensembl_id` and `entrez_id` never become protein identifiers or
  `encodes` links.
- A numeric `Dataset.pubmed_id` becomes a publication, and `HI-III` does not.

**After a simulated reload,** the sync finds the same entities by accession
and taxon. It re-attaches, with their approvals, only the live-legacy releases
whose dataset id and name both match.

- A dataset deleted and re-created under the same name gets a new release
  with no approvals.
- Datasets with a NULL or shared name are reported and left unlinked.
- A dataset with a typed re-import stays excluded after the reload and
  re-sync.
- `graph_check` flags a link whose stored accession no longer matches its
  protein row.

**Federation.**

- Federated edge ids equal the `/api/search` interaction ids for the same
  seed.
- The type comes from the stated `interaction_type` only. Inferred types
  appear as metadata labelled "inferred by openPIP", including the two-valued
  `MI:0914|MI:0915` case.
- Whatever the number of dataset links, the interaction's values (negative,
  type, score, roles, methods, annotations) appear at edge level as
  "contributing dataset unknown". `negative = true` never becomes a
  `tested_absent` assertion, and the edge is shown with a warning marker. The
  fixtures include an interaction whose creating dataset was deleted, leaving
  one crediting link, and one created by `/api/upload/` with no dataset and
  credited later.
- Removed rows (`removed != '0'`) never appear.
- Query capture shows no `INSERT`, `UPDATE` or `DELETE` on a legacy table.
- A legacy dataset whose live-legacy release a typed re-import supersedes is
  excluded.
- An upload to an approved legacy dataset after its approval makes it show
  "changed since approval" until a new decision is recorded.

### 5.12 Performance (A2, X1, P1)

- **Caps live in SQL.** Caps and per-seed pre-limits are applied inside SQL,
  not after building Python lists. A test with a high-degree seed (thousands
  of partners, made with factories) returns within the edge and per-seed caps
  in a bounded number of queries, and reports what the pre-limit cut.
- **Ranking** counts visible assertions through the
  `(relationship, release)` index, and is deterministic. It may favour an
  observation that several sources repeat (a stated trade-off), but every
  edge shown is still summarized by its independent evidence.
- **Reference expansion** adds a bounded number of queries per seed, and
  respects its own cap of 25 entities per seed.
- **Benchmark targets (P1).** P1 sets p95 targets on realistic data (R1 and
  R2) with production settings, because the development settings log every
  SQL statement. Stored per-relationship statistics, depth 2, `pg_trgm` and
  caching are added only when the numbers require them.

### 5.13 Organism track (O1)

- With unset settings, these are byte-identical to today's human values
  (snapshot tests): the g:Profiler request, the KEGG, STRING and NCBI links,
  and the table links.
- Taxon 7227 gives the verified fly values. An unknown taxon hides
  organism-specific links.
- With enrichment off, no request reaches g:Profiler (MSW assertion). Other
  outbound calls, such as AlphaFold, are outside the switch.
- In the fork-only form, `admin_panel` has no new migration, and a test
  asserts that the `admin_settings` column set is unchanged, so
  `migrate_legacy.py` behaves exactly as before. Its `admin_settings` insert
  already fails today (checked on a scratch database); ML1 would fix that.

### 5.14 Milestone RD: the first real cross-layer demonstration (RF1a, R1, M1, R2)

Every check runs on a private stack or in CI, never on the HuRI or YeRI
stacks. Third-party data enter only after your recorded approval; until then,
the tests use test-only reference and evidence fixtures written for the
repository.

- **Reference releases (RF1a).**
  - Gene entities come from the HGNC release and protein entities from the
    UniProt release. All are `active`, and each traces to its reference
    release.
  - The mapping release states `encodes`; the UniProt release states
    `isoform_of`. Each is approved separately.
  - A value held by more than one gene is never used to map; it is reported.
  - A withdrawn or unapproved mapping release adds no `encodes` relationship
    to the visible set, and the entry–isoform links stay.
  - A newer reference release supersedes the old one, and its genes and
    proteins stay in the default set.
- **Regulatory release (R1).**
  - Every edge traces to its source record and paper.
  - The source's regulation mode maps to `effect`, and "unknown" stays
    unknown.
  - Symbols map only when unique across HGNC's approved, previous and alias
    symbols. The import report lists every ambiguous and unresolved row, and
    none of them is imported.
- **Interaction release (R2).**
  - BioPlex's analysis is not isoform-specific, so each isoform accession,
    `-1` included, maps to its UniProt entry only through a verified
    `isoform_of` link from the approved UniProt release (basis
    `isoform_entry`). The participant keeps the accession as written, the
    import run records the UniProt release used, the isoform entity stays
    separate, and an isoform with no verified link is reported, never
    stripped (B12).
  - A row that gives only a gene identifier is reported as unresolved, with
    the candidate protein entries, unless a reviewed resolution file maps it
    (B4).
  - BioPlex rows are `physical_interaction` at level `association`,
    labelled "co-complex association (AP-MS)", with the type marked as
    assigned by openPIP from the stated assay. They are never shown as
    binding or as direct binary interactions, and a level or method filter
    separates them from binary evidence (B14).
  - A release-level statement, such as the study's assay, is recorded only
    where due diligence quotes it, and is labelled as such.
- **The demonstration.** On the private stack, a search for a transcription
  factor's gene from R1 shows:
  - its regulation edges;
  - labelled `encodes` and `isoform_of` links to its protein records;
  - their physical partners from R2.

  Gene, entry and isoform stay separate nodes. With the toggle off, only the
  gene's own edges appear.

## 6. Test data

- **Factories.** The existing ones (`proteins/tests/factories.py`,
  `interactions/tests/factories.py`, `datasets/tests/factories.py`), plus
  `graph/tests/factories.py` from G1. Registry rows come from the sync
  fixture.
- **Test-only namespaces** cover every case, scope and version policy. Tests
  therefore depend on no real-world nomenclature rule beyond the four that
  G1's review verifies (`uniprotkb`, `hgnc`, `ncbigene`, `ensembl_gene`) and
  those each later PR verifies when it adds a namespace.
- **The synthetic release (G6)** is the shared end-to-end fixture for the
  backend. Its MSW mirror serves the frontend. It uses `SYN-` labels, the
  `synthetic_gene` and `synthetic_protein` namespaces, and no PMID or DOI that
  could resolve to a real record. v2 supersedes v1, and it includes the
  reference chain from a SYN gene to its protein's physical partner. It
  stores no aliases; alias cases use factory rows (G1) and RF1a's reference
  fixtures.
- **Reference fixtures for RF1a's tests** are small files written for the
  repository, shaped like the real releases but with synthetic content. Real
  HGNC or UniProt files never enter the test suite.
- **No new third-party data** is added for tests, unless it is already in the
  repository with known provenance. M1 can reuse the IntAct sample that the
  PSICQUIC tests already use.
- **No real gene names** in synthetic rows, so a fixture cannot be mistaken
  for a finding.

## 7. Per-PR checklist

- [ ] New behaviour covered by tests in the issue's area above.
- [ ] Full backend suite on PostgreSQL from a full checkout, run recorded.
- [ ] `ruff`, `black --check`, `makemigrations --check`.
- [ ] New migrations use only allow-listed operations, in new apps only; CI's
  forward-and-back step and the legacy schema snapshot pass.
- [ ] Frontend lint, serial Vitest and build, if the frontend changed.
- [ ] `mkdocs build --strict`. New routes have one heading per method and
  path; new variables are documented; new pages are in the nav.
- [ ] Legacy contract tests green. No edits to legacy modules beyond the
  changes the issue names.
- [ ] `CHANGELOG.md` `[Unreleased]` entry.
- [ ] Rollback stated in the PR description.
