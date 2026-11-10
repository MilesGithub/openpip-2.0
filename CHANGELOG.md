# Changelog

All notable changes to openPIP 2.0. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

No version has been tagged yet. Everything below is in development towards
**2.0.0**, the first release of the rebuilt platform.

## [Unreleased]

### Added

**Searching and exploring**

- Search by one or more identifiers, separated by commas, spaces or new lines,
  with gene-name autocomplete on the home and search pages.
- Typed phrases in the home page search, with tissue-name suggestions.
- Interactive network (Cytoscape.js) with confidence, evidence-category,
  tissue (several at once, combined as in the original openPIP) and annotation
  filters, a choice of layouts, highlighting, and a canvas background setting.
- Protein and interaction detail panels with links to external databases
  (including BioGRID, KEGG and UniProt), and a 3D structure viewer showing the
  AlphaFold model (with model confidence) or a PDB structure.
- Term enrichment, subcellular location and tissue expression tabs.
- A browsable, filterable list of every protein.
- Network exports for everyone, including PNG and JPG images and FASTA
  sequences.

**Saving and sharing**

- Saved networks, and saved views that re-run their search on the current data.
- Sharing saved views with colleagues, with notes, a discussion thread and
  in-portal notifications.
- Public links that open a saved view without logging in.
- User profiles with optional details, an avatar, and a public profile page.
- Account recovery through security questions set at registration. openPIP
  sends no email.

**Data**

- Background dataset import (Celery and Redis) with a progress view.
- PSI-MI TAB and simple CSV uploads. Uploads keep detection methods,
  interaction types, participant roles, organisms, annotations and the
  negative flag.
- Automatic enrichment of new proteins from UniProt and Ensembl, and of new
  organisms from NCBI Taxonomy.
- A citation and an About-page entry for every dataset, with lookup by PubMed
  ID or DOI.
- A command to link existing proteins to their organisms from UniProt.

**Programmatic access**

- A JSON REST API, with a generated OpenAPI schema and Swagger UI.
- A PSICQUIC service: PSI-MI TAB 2.5, 2.6, 2.7 and 2.8, MIQL with boolean
  operators and the `id`, `idA`, `idB`, `taxidA`, `taxidB`, `species`, `pubid` and
  `pubauth` fields, ROGID/RIGID checksums, and `formats`/`version` endpoints.
- Rate limits on the public API and PSICQUIC.

**Administration**

- An admin panel for site identity, appearance and network colours, page
  content, example searches, interaction categories, announcements,
  supplementary files and datasets.
- Every piece of interface text can be changed through a site-text registry.
- Administrators can grant and revoke admin access.
- The browser tab and installed-app branding follow the site settings.
- Options to turn off tissue-expression and subcellular-location annotations
  for organisms that have none.

**Documentation**

- This documentation site, served at `/docs/` on every portal, with an
  operator guide and a complete API reference that tests keep in step with the
  code. Its examples show the portal's own address.

**Development**

- Continuous integration in GitHub Actions. Every push and pull request runs
  the backend tests on PostgreSQL 16, ruff, black and the migration check, the
  frontend lint, tests and build, and the strict documentation build. A run
  stops if the tests would use any database other than PostgreSQL, or if it
  selects the parity tests.

**Typed relationship graph**

- Groundwork tables for typed entities and identifiers, behind
  `TYPED_GRAPH_ENABLED` (off by default). Existing pages, APIs and data are
  unchanged.
- Provenance and licensing records for typed data: data sources, datasets
  and publications, versioned releases with their files, import runs and
  source records, due-diligence records with their history, licence
  snapshots, approval decisions, and the release that introduced each entity.
- Approval and visibility rules: nothing typed is visible without an
  effective approval, viewing, bulk download and external services are each
  approved on their own, a correction never hides public data, and a
  withdrawn release is hidden at once. Still behind `TYPED_GRAPH_ENABLED`;
  with it on, staff see the catalogue and licensing records in the Django
  admin.
- Relationships between typed entities, and the evidence each release states
  for them: typed relationships (directed, or one per pair for symmetric
  types), relationship and score types, vocabulary terms, and evidence
  assertions tied to their source records, with outcomes (supported, tested
  and not found, inconclusive, not significant), effects, genetic signs,
  physical-interaction levels, methods, publications, scores, contexts and
  the participants as the source named them. Each relationship's summary
  counts only evidence from releases the viewer may see, and counts records
  of one observation once. Recording a record's evidence again changes
  nothing, and contradictory findings are kept side by side. Still behind
  `TYPED_GRAPH_ENABLED`; with it on, staff see the relationship types, score
  types and terms in the Django admin, read-only.
- Registering and checking releases of typed data, described in the
  operator guide's new Typed data format page. `manage.py
  graph_register_release` registers a release with the adapter that reads
  it and stores its data file and manifest in private storage, which nothing
  serves: in production, a third volume, `graph_files`, to back up with the
  database. `manage.py graph_import --dry-run` reads a release in openPIP's
  own tab-separated format, `canonical_tsv_v1`, maps every identifier
  without guessing (an identifier that is only a candidate maps through a
  reviewed resolution file alone), checks every row against the evidence
  rules, and reports rejected rows by line number, unresolved and ambiguous
  identifiers with their candidates, the entities and relationships an
  import would create, and conflicts with other releases. A dry run writes
  only its run and the release's source records; a release of real data
  needs an approval first, and a synthetic release is checked, not trusted.
  Still behind `TYPED_GRAPH_ENABLED`.
- Importing releases of typed data, and their lifecycle, described in the
  operator guide's new Typed releases page. `manage.py graph_import
  --commit` imports what a release's latest dry run reported, with the same
  options: it first checks that the file still maps as that dry run
  reported, then writes the entities, relationships, publications, terms
  and evidence batch by batch, one transaction each, so that releases
  committing at the same time share rows without duplicates; run again, a
  commit that stopped resumes from its checkpoint. It stages the release
  with its statistics. `graph_approve` and `graph_revoke` record and revoke
  approval decisions, `graph_publish_release` publishes a staged release
  and supersedes the release it corrects, `graph_withdraw_release` hides a
  release for good, `graph_discard_release` returns a staged release to a
  draft to import it again, and `graph_check` lists what needs an operator,
  exiting 1 for a problem. One dry run, commit or discard of a release runs
  at a time, and a file that states more than 250,000 distinct identifiers
  is refused. Still behind `TYPED_GRAPH_ENABLED`.
- A synthetic demonstration of the typed graph, described in the operator
  guide's Typed releases page: one dataset in two synthetic releases, v2
  correcting one effect of v1, whose rows cover the cases the typed graph
  must handle, with made-up `SYN-` genes and proteins and two made-up
  papers, and rows that a dry run must report and never import. `manage.py
  graph_load_demo` registers, dry-runs, commits and publishes both, with an
  MIT licence snapshot and an internal approval for each, so that only
  staff see them, and records two browser instances for staff, the
  demonstration's and a `portal_ppi` entry. Run again, it completes a load
  that stopped and writes nothing once the demonstration is loaded, and it
  never overrides an
  operator's decision. Still behind `TYPED_GRAPH_ENABLED`.
- A read API for the typed graph under `/api/graph/v1/`, described in the
  developer guide's new Typed graph page: `status`, `vocabularies`, `resolve`
  and `entities/{entity_id}`. While `TYPED_GRAPH_ENABLED` is off, every route
  but `status` answers 404. With it on, visitors see only releases with a
  `public_view` approval, so the demonstration stays with staff until an
  operator records one.
- The typed graph's network, relationship and evidence routes under
  `/api/graph/v1/`, described in the developer guide's Typed graph page:
  `network` around up to ten seeds, ranked, capped and summarized by the
  evidence rules under a statement timeout and a request deadline,
  `relationships/{relationship_ref}` and its paginated `evidence`; the entity
  page's reference edges take the shared edge shape. Still behind
  `TYPED_GRAPH_ENABLED`.
- Browser instances: the gallery's entries, each a view of chosen typed
  releases, this portal's search, another portal or a citation card, edited
  in the Django admin as the operator guide's new Browser instances page
  describes. `/api/graph/v1/browsers` lists those a caller may list, with
  eight filters, and `browsers/{slug}` serves one; availability is computed
  for each caller on every request, so a withdrawal shows at once. A new
  `browser` parameter on `resolve`, `entities/{entity_id}`, `network` and both
  relationship routes reads only the releases an instance selects. The
  synthetic demonstration's loader also records two instances, for staff
  only. Still behind `TYPED_GRAPH_ENABLED`.
- The frontend's typed client for `/api/graph/v1/`: types for every documented
  response, React Query hooks for all nine routes with the `browser` selection,
  and test fixtures recorded from the API on the synthetic demonstration, which
  a backend test keeps true. No page uses them yet.

### Fixed

From the October 2026 audit (see `docs/BUGS.md` for details):

- Production refuses to start with the default secret key, which could be
  used to sign administrator tokens.
- Avatar uploads can no longer be used to serve HTML from the site.
- An empty search returns no results instead of the whole database.
- Uploaded interactions that already exist are credited to every dataset they
  appear in.
- Networks with long gene lists can be saved.
- Public media paths can no longer reach private uploads.
- Registration enforces the 8-character password minimum. Login handles
  passwords the same way registration does.
- A password reset signs the account out everywhere.
- Long share and comment notifications no longer fail.
- A demoted administrator loses the admin interface at the next token
  refresh, and deactivated accounts can no longer refresh their tokens.
- Search summaries are shown as text, not interpreted as HTML.
- Interaction categories sort by number.
- Invalid upload parameters return clear errors instead of server errors.
- Dataset downloads leave out removed interactions.
- PSICQUIC pages come in a stable order, and `maxResults` is capped at 2,500.
- The all-datasets download is built once and cached instead of being rebuilt
  on every request.

From the documentation review (October 2026):

- The production compose file no longer publishes the database or Redis, and
  the web and API containers listen on 127.0.0.1 only.
- Security-question answers and PSICQUIC are rate-limited for signed-in
  callers too, answers are also limited per account, and rate limits trust
  only the address the HTTPS proxy sets.
- Uploads load rows with bare gene names, accept CSV headers in any case, match
  existing proteins by their own UniProt, Ensembl and Entrez fields (no more
  duplicate proteins), add the chosen category to reused interactions and CSV
  rows, and keep protein interaction counts current. The upload preview counts
  existing proteins the way the import does.
- The Django admin and its styles work in production, under any URL prefix.
- The search page can export the network as PNG or JPG again, and its
  downloads now contain what the filters show.
- Saved networks open as the snapshot they were saved as. Saved and shared
  views reopen on their result tab.
- Hidden announcements stay off the home page, the upload wizard's category
  list no longer shows blank names, the sharing buttons are styled, and the
  Button color setting applies.
- Interface text no longer describes features openPIP does not have, such as
  a personal API key or an installable Python SDK.
- Tissue specificity is shaded in the portal's own node colours instead of
  fixed blue and red.
- The sidebar summary and the tissue and subcellular tabs follow the filters.
- Saved networks keep their tissue filter and categories, and reopen with
  them.
- The profile asks before deleting a saved view or network.
- Signing in returns you to the page you were on.
- The About page links to the documentation site, and the old in-app guide's
  address redirects there.

### Changed

- A deployment is configured by one setting, `PUBLIC_URL`, its public address.
  The allowed host, trusted origin, URL prefix, HTTPS enforcement, the
  documentation's address and the first Site URL follow from it. Left empty,
  openPIP runs on the local machine at `http://localhost:8080`. A fresh copy no
  longer defaults to the reference portal's domain and `/v2` path.
- The repository moved to [Helmy-Lab/openpip-2.0](https://github.com/Helmy-Lab/openpip-2.0).
- The reference portal moved from `openpip.usask.ca/v2` to
  [openpip.usask.ca](https://openpip.usask.ca), and the original PHP openPIP
  to `openpip.usask.ca/legacy/`. The parity tests use the new legacy address.
- The user and admin guides are laid out as help pages: a tab per page and a
  card per section that opens on click. Every documentation page uses the
  portal's own navigation bar and colours.

### Removed

- Interface code that was never displayed: a second search toolbar with five
  menus, an export dialog, a loading overlay and several home-page components.
- The `CORS_ALLOWED_ORIGINS` setting, which had no effect: browser access is
  deliberately open to every origin.

### Known issues

Open issues are tracked in `docs/BUGS.md`. Uploads before this fix created
empty duplicates of about 8,200 proteins, and the HuRI dataset is attached to
those duplicates rather than to the original proteins (BUG-034). By decision,
the data is not merged: HuRI and HI-III interactions stay separate. New
uploads no longer create duplicates.

`migration/migrate_legacy.py` cannot load seven tables into the current
schema. With `--reset` it leaves them empty, together with some tables that
refer to them (BUG-052). Run it only on a new installation that you have
backed up; see the migration guide.
