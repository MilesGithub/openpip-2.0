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
