# Open questions

Unresolved scientific, licensing, identity and product questions for the
generalization project.

Each question has a **safe default**: what the work does unless the owner
decides otherwise. A default never invents data, permissions or mappings, and
never changes behaviour on the existing portals.

"Needed by" names the first issue in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) that depends on the answer.

**How to answer.** Note the decision next to the question, or in the PR that
needs it, and the documents are updated to match. A question left unanswered
takes its default.

**Decided on 2026-10-29.** Questions the owner settled are marked
**Decided**, with the decision in place of the default. Architecture §15
lists where each decision changed the design.

## 1. Departures from the brief, and design choices to confirm

The reasons are in
[GENERALIZATION_ARCHITECTURE.md](GENERALIZATION_ARCHITECTURE.md) §12 and in the
decision named.

| Id | Question | Safe default | Needed by |
|---|---|---|---|
| A1 | Should effect and biological context be part of a relationship's identity? Brief §4 suggests including them "as needed" | No, for all MVP families. Effect and context stay on each assertion, and edges show a computed summary, so contradictions remain on one edge. A future type whose claim is defined by its effect or context can declare it as an identity qualifier (D3) | G3 |
| A2 | Gallery at `/browsers` instead of `/datasets`? | Yes. `/browsers` and `/browsers/:slug` hold instances, and `/datasets` is the typed catalogue of datasets, releases and licences (D15) | F4, C1 |
| A3 | Extend the legacy `Dataset` table, or build a new hierarchy? | A new `SourceDataset` → `DatasetRelease` hierarchy. The legacy `Dataset`, with its citation and About fields, stays as it is. It is bridged only if an existing PPI portal adopts the typed graph (L1, on demand; D10) | G2 |
| A4 | Map existing proteins to typed entities in Phase 1 (brief issue 4)? | **Decided:** no. The bridge (L1, L2) is deferred to an on-demand track, built only when an existing PPI portal adopts the typed graph. The unified resource imports PPI datasets directly as typed releases (M1, R2), and HuRI and YeRI stay unchanged (D11) | L1 (on demand) |
| A5 | One `physical_interaction` type for the PSI-MI MI:0914 subtree, instead of the brief's `physical_association`? | Yes. The PSI-MI type is kept on each assertion, and the edge label shows the strongest stated level, so co-purification is never shown as binding. `protein_interaction_unspecified` holds rows with no stated type (D20) | G3 |
| A6 | Outcome values `supported`, `tested_absent` and `inconclusive`, instead of "observed / not observed"? | Yes. "Not observed" can be read as "not tested", and a prediction is not an observation (D17) | G3 |
| A7 | Add a fourth approval scope, `external_services`, for sending identifiers to services such as g:Profiler? | Yes, and it stays off for every release until approved (D9) | G2 |
| A8 | Seed the registries (types, namespaces, taxa, score types) with an operator command rather than a migration? | Yes. Portals that never enable the typed graph get empty tables. A portal that does runs `graph_sync_registry` after each upgrade, and the import commands refuse on a version mismatch (D5) | G1 |
| A9 | API prefix: `/api/graph/v1/`, or the brief's "v2" (`/api/v2/`)? | `/api/graph/v1/`. "v2" reads as "the openPIP 2.0 API", would take the natural name for a future legacy API version, and becomes `/v2/api/v2/…` under a `/v2` prefix (D16) | A1 |
| A10 | Model names `SourceDataset`, `Entity` and `Relationship` instead of `Dataset`, `BiologicalEntity` and `BiologicalRelationship`? | Yes. This avoids a second `Dataset` class beside `datasets.Dataset`; user-facing text still says "dataset" | G1 |
| A11 | Brief §8 puts a "small synthetic fixture and read API" in Phase 1. May the read API wait for Phase 2? | Yes. The fixture lands in Phase 1 (G6) and is verified at service level; the API is A1–A2 in Phase 2, after CI on PostgreSQL (T1) | A1 |
| A12 | Brief §8 lists "site-specific fields" in Phase 4. Does that mean sequence-site fields (binding or modification positions), or fields specific to each browser? | Sequence sites stay in P2 (Phase 5). Per-browser fields are covered by B1's presentation schema. If sequence sites are meant, P2 moves into Phase 4 | P2 |

## 2. Scientific semantics

| Id | Question | Safe default | Needed by |
|---|---|---|---|
| B1 | Should tested-absent evidence be visible by default in the explorer? | Hidden by default, with a visible warning, a count and a per-view toggle. Never counted as support | A2 |
| B2 | Should a mixed effect be one edge, or split into parallel activating and inhibiting edges? | One edge with per-effect counts and a diamond arrowhead. A "split by effect" view toggle can come later | F2 |
| B3 | Should genetic interactions be modelled between genes, with alleles as qualifiers, or between alleles as entities? | Gene-level relationships, with alleles, perturbations and screen details on each assertion, until an approved genetic dataset has been analysed | N1 |
| B4 | Some PPI data identify interactors only by gene ids. In a typed import, should endpoints be protein entities or gene entities? | Protein entities, but a gene id is never mapped to a protein automatically, not even through a unique `encodes` link. Such a row is reported as unresolved, listing the protein entries that the visible mapping release links to the gene, and is imported only if a reviewed resolution file maps it (architecture §4.1). A mapping fixed into evidence at import would also survive the withdrawal of the mapping release. Never gene–gene physical edges, and never one protein entity per source. A shared "protein product of gene X" entity needs your decision first | R2, M1 |
| B5 | Should `/explore` summarize protein evidence onto genes? | No automatic projection. If one is added later, it is a separate, labelled projection mode. Following reference links from a gene to its proteins (architecture §4.6) is not a projection: the nodes stay separate and the links are drawn | X1 |
| B6 | May an import create entities for valid identifiers not yet in the database? | Not by default. Allowed per import, with `--create-entities`, only for namespaces that define identity for the declared type. The dry run lists them first. Entities from a release that is not a reference release are created as `provisional` (architecture §4.1) | G4 |
| B7 | How should site positions be anchored when the source does not state the sequence version? | Store the position with the version marked unknown, flag it in the panel, and never transfer it to another isoform | P2 |
| B8 | Is a source's explicit "unknown" effect different from an effect it does not report? | Yes in storage (`unknown` versus NULL, with the verbatim text kept). Both summarize as "unknown" on the edge, and the counts keep them apart | G3 |
| B9 | What does the legacy dataset "Test-Space" represent: tested pairs, retests or something else (`docs/project/dataset-audit-2026-06-14.md`)? | If bridged, treat it like any other legacy dataset and flag it for curator review. No tested-space meaning is inferred | L1 (on demand) |
| B10 | How should inconclusive and not-significant results be shown? | Counted separately on the edge. Never support and never tested-absent. Edges with only inconclusive or not-significant evidence are hidden by default, behind the same toggle as tested-absent ones, and say which they are | A2 |
| B11 | The two legacy `P04637` rows (protein ids 5319 and 11911) are one accession, and the owner decided on 2026-10-02 not to merge them (`docs/BUGS.md:201-211`). Should the bridge link both to one typed entity, which combines their interactions in typed views? | No. Duplicate legacy rows for one accession are reported and left unlinked, and each legacy primary key stays visible on federated edges | L1 (on demand) |
| B12 | BioPlex assembles its data at gene level but lists UniProt accessions, some with isoform suffixes (`Q8N7W2-2`). How should a suffixed accession from such a source be imported? | **Decided (2026-10-29):** BioPlex is protein-product association evidence; no isoform-specific interaction is assumed unless the study supports it. The adapter declares the source not isoform-specific, and each suffixed accession, `-1` included, maps to its UniProt entry-level protein only through a verified `isoform_of` link from an approved UniProt release (basis `isoform_entry`). The original accession and the mapping provenance are kept; isoform entities are never merged, and an isoform with no verified link is reported (architecture §4.1) | R2 |
| B13 | Genetic-interaction screens (Costanzo, Horlbeck) report every tested pair. Should pairs below the significance threshold become `tested_absent` assertions? | **Decided (2026-10-29):** No. A non-significant score is never a confirmed non-interaction. If a release imports such pairs, their outcome is `not_significant`, distinct from `tested_absent` (experimentally established absence), with the scores, the threshold and the statistics kept. A negative genetic interaction (`genetic_sign = negative`) stays distinct from negative evidence. A first release imports only pairs that pass one declared threshold tier (architecture D17, §4.3) | N1 |
| B14 | BioPlex states its assay (AP-MS, co-purification) but no PSI-MI interaction type. How should its interactions be classified? | **Decided (2026-10-29):** as AP-MS co-complex association evidence, not as direct binary interactions: `physical_interaction` at level `association`, labelled "co-complex association (AP-MS)" with the type marked as assigned by openPIP from the stated assay. The ontology keeps binary and co-complex evidence apart by stored level, detection method, spoke expansion and bait and prey roles (architecture D20) | R2 |

## 3. Licensing and data

| Id | Question | Safe default | Needed by |
|---|---|---|---|
| C1 | Which real datasets come first, and do their terms allow internal use, public view and bulk download? Your priorities: TRRUST v2 (regulation), BioPlex 3.0 (physical), Costanzo et al. 2016 and Horlbeck et al. 2018 (genetic), and Helwak et al. 2013 (miRNA). Research-only findings are in [DATASET_DUE_DILIGENCE.md](DATASET_DUE_DILIGENCE.md): TRRUST's data are CC BY-SA 4.0 (C13); BioPlex's site states no data licence, though its NDEx deposits carry a CC0 field (C14); Costanzo is CC0 on Dryad; Horlbeck is CC BY 4.0 on Mendeley Data; HGNC is CC0 and UniProt CC BY 4.0 | Only the synthetic release until then. No third-party row is imported until a complete due-diligence record and an `internal` approval exist; the approval service enforces the first, and an approval stops being effective if the record is reopened. Licence fields stay "unknown" rather than guessed. This covers the reference releases too: HGNC, UniProt and the gene-to-protein mapping (RF1a) | RF1a, R1, R2 |
| C2 | Who may approve public view and bulk download? Does bulk download need a second approver? | The deployment's `is_staff` admin, with a recorded rationale and evidence. Bulk download needs redistribution recorded as permitted. A curator role and an optional two-person rule come in CU1 | G2 |
| C3 | When an existing PPI portal enables the typed graph, should its legacy PPI appear in `/explore`? | Off. Each bridged legacy dataset stays invisible in typed views until an operator records an approval with that dataset's own rationale. There is no blanket flag, because legacy portals also hold literature-curated third-party data such as Lit-BM | L2 (on demand) |
| C4 | Should anonymous visitors see verbatim source records? | Only for releases approved for public view; staff otherwise | A2 |
| C5 | Which mapping links typed genes to proteins, and under what licence? | **Decided:** an approved, versioned gene-to-protein mapping release joins the layers in milestone RD (RF1a). HGNC IDs are the gene identity. The mapping is its own release, approved separately from the HGNC gene set and the UniProt proteome, so withdrawing it never withdraws gene identity. Candidate sources are compared in [DATASET_DUE_DILIGENCE.md](DATASET_DUE_DILIGENCE.md). NCBI gene2accession is not one: it pairs Gene IDs with GenBank and RefSeq accessions, not UniProtKB entries *(verify)*. Links come only from an approved release, never from shared symbols. The synthetic MVP uses the fixture's own links. Terms are still to be verified ([DATASET_DUE_DILIGENCE.md](DATASET_DUE_DILIGENCE.md)), and if none clears review, the cross-layer demonstration stays synthetic | RF1a |
| C6 | Is the repository's MIT licence right for the synthetic fixture? | Yes. It is written in the repository, contains no third-party data, and its README says it is synthetic | G6 |
| C7 | Is NCBI taxon 32630 ("synthetic construct") acceptable for synthetic entities? | Yes, after checking the label against NCBI Taxonomy, with synthetic badges everywhere. G1 already seeds it in `taxa.json` | G1 |
| C8 | What retention and size limits apply to release files? | Keep everything; withdrawal hides a release, it does not delete it. 2 GB per file from the command line, and later 100 MB per web upload. Files are stored outside `MEDIA_ROOT`, which DEBUG serves, and are never served (architecture §3.5) | G4 |
| C9 | Do the HuRI, YeRI and FlyBi maintainers want gallery entries, and what are their verified citations, URLs and organisms? | No entries until the maintainers confirm. Then `external` entries with verified metadata only. The YeRI paper in particular is to be verified, as the brief notes | F4 |
| C10 | Should a typed PPI import (for example BioPlex) and legacy PPI for the same pair be merged for display? | No. They stay parallel edges labelled with their provenance until links exist and a labelled grouping is designed | L2 (on demand) |
| C11 | Which PSI-MI and ECO releases are loaded as vocabulary references, and under what terms? | **Decided:** load nothing for now; V1 is on the on-demand track (owner's decision, 2026-10-29). Each adapter's explicit list of accepted PSI-MI terms is checked in review against a named PSI-MI release, and sets each physical assertion's stored D20 level. Terms exist only as the CURIE and label a source supplies | M1 |
| C12 | May statements a source makes once for a whole release (its paper, assay, interaction type or organism) be recorded on each of its rows? | Only for a single-study release, only for what due diligence quotes from the source, and labelled "stated for the whole release" in the panel. Never for an aggregator. Otherwise the statement stays release metadata | R1, R2 |
| C13 | TRRUST's data are under CC BY-SA 4.0. How does ShareAlike apply to exports, to views that combine TRRUST with other releases, and to the hub's database (the licence's Section 4)? | **Decided (2026-10-29):** TRRUST's CC BY-SA 4.0 attribution and terms are kept per release, with a note of changes. No combined export containing TRRUST is enabled until the ShareAlike implications have been evaluated: an export that would combine a release whose licence snapshot has `share_alike` set to `yes` or `unknown` with any other release is refused. Downloads stay restrictable by dataset and release. The Section 4 question still goes to a licensing reviewer before any public hub launch (architecture §3.7) | R1, E1 |
| C14 | BioPlex's website states no data licence, while the paper's NDEx deposits carry a CC0 rights field set by an NDEx curator. How should BioPlex's redistribution terms be settled, and is there an alternative? | **Decided (2026-10-29):** a short inquiry to the BioPlex authors is drafted ([DATASET_DUE_DILIGENCE.md](DATASET_DUE_DILIGENCE.md) Appendix A) and is sent only with your approval. An alternative publication-specific protein interaction dataset is researched in parallel. Until the terms are confirmed, BioPlex gets no `public_view` or `public_bulk` approval | R2 |

## 4. Identity and organisms

| Id | Question | Safe default | Needed by |
|---|---|---|---|
| D1 | Which taxon ids should yeast entities and YeRI use: species 4932 or strain 559292? | Keep whatever the source states and never rewrite it. Match strain to species through `Taxon.parent`, and verify the ids before adding a yeast namespace or preset | N1, RF1b, O1 |
| D2 | Which g:Profiler, KEGG, STRING and GeneMANIA organism codes apply to each taxon? | Only codes verified against each service's documentation go into `organisms.ts`, starting with human and fly. For an unknown taxon the feature is hidden with a notice | O1 |
| D3 | May the bridge assume one taxon for legacy proteins with no organism link? | Only with an explicit `--assume-taxon`, recorded in `BridgeRun`. Otherwise those rows stay unlinked and appear as `ppi-protein` nodes | L1 (on demand) |
| D4 | Have each namespace's case, version and scope rules been checked against their authority? | Each rule cites the authority document it was copied from, and the reviewer checks it in the PR that adds the namespace. Unverified namespaces are not added, and there is no generic, multi-organism symbol namespace | G1 |
| D5 | May independent installs define their own entity or relationship types? | Not in the MVP. Later, `local_` slugs that must map to a core family | — |
| D6 | A legacy interaction with no stated type, but a two-hybrid detection method, is inferred by openPIP's MITAB output to be MI:0915. Should federation show it as `physical_interaction`? | No. It is `protein_interaction_unspecified`, and the inference is shown as metadata labelled "inferred by openPIP" | L2 (on demand) |
| D7 | Which namespace defines yeast gene identity, for Costanzo et al. 2016 (N1)? | None until you decide. Candidates: SGD ids (`SGD:S000000001`), or systematic ORF names (`YAL001C`). Costanzo's files key on systematic names, so either choice maps them through an approved SGD release, whose files are still to be checked ([DATASET_DUE_DILIGENCE.md](DATASET_DUE_DILIGENCE.md) §2.3, §3.4). No yeast gene entity is created before you decide | N1 |
| D8 | Which namespace defines human gene identity? | **Decided:** HGNC IDs. NCBI Gene and Ensembl Gene are verified cross-references. Records are never merged on symbols alone, and an identifier that cannot be mapped unambiguously is reported, not guessed (architecture §4.1) | G1 |
| D9 | Are UniProt isoform accessions, `-1` included, separate from their entry? | **Decided:** yes. Entry-level `protein` records and isoform-specific `protein_isoform` records stay apart and are never collapsed. They are joined only by `isoform_of` links from a verified UniProt release | G1 |
| D10 | TRRUST v2 also covers mouse. Should R1 import the mouse part? | No. R1 imports the human part only, until a mouse gene identity namespace is decided (MGI, for example; *verify*) | R1 |

## 5. Deployment, repository and process

| Id | Question | Safe default | Needed by |
|---|---|---|---|
| E1 | Which deployment becomes the hub? | A new, separate deployment with `TYPED_GRAPH_ENABLED=1`. HuRI, YeRI and FlyBi keep it off, and appear in the gallery as external entries only once their maintainers confirm (C9) | A1 |
| E2 | Which code do HuRI, YeRI and FlyBi run: this fork, upstream `Helmy-Lab/openpip-2.0`, or `MilesGithub/HuRI`? Will they ever pull this fork? | Assume every migration may reach all three. Everything stays safe with the switch off | G1 |
| E3 | Should the organism settings (O1) be proposed upstream as `AdminSettings` fields, or kept fork-only? | Fork-only, in a fork-owned `portal_profile` app, so the fork has no `admin_panel` migration that could collide with upstream's numbering. Upstream gets a proposal only when the owner asks | O1 |
| E4 | Add CI (T1) to the fork? | **Decided and done:** merged into the fork's `main` with your approval on 2026-10-29 (PR #1, `b23119e`), every check green on GitHub | T1 |
| E5 | Enable GitHub Issues on the fork for the breakdown? | Not yet. The breakdown stays in IMPLEMENTATION_PLAN.md until the owner enables Issues | — |
| E6 | What happens to the unmerged `feature/species-config` branch? | It stays unmerged, superseded by O1, and is deleted only on request | O1 |
| E7 | Does "old `/api/search` response remains unchanged" also forbid additive keys? | Yes, byte for byte. Any additive change is a separate, opt-in issue | G1 |
| E8 | Should the hub get a CSP and HTML sanitization before launch? | New features accept no HTML. SEC1 lands before any rich-text instance content and before a public hub launch | SEC1 |
| E9 | Should typed Celery imports use a dedicated queue? | No. Small chained batches on the existing queue, so legacy imports interleave | I1 |
| E10 | Should the gallery work on a portal that does not enable the typed graph, for example one that only lists external portals? | One switch covers both, split only if asked | B1 |
| E11 | Does the MVP need saved and shared explorer views, or are linkable URLs enough? | URLs only in the MVP; saved views in S1 | X1 |
| E12 | Should saved explorer views pin releases or follow the latest one? | Pin, with a banner when a pinned release is superseded or withdrawn | S1 |
| E13 | Should gallery entries for independent portals fetch live metadata or counts? | No. Static metadata, entered and verified by an admin, and no outbound calls | B1 |
| E14 | Which defects from the audit (assessment §8) get their own PRs on the fork? Most exist upstream too | Two are planned: ML1 and SEC1. ML1 is planned because the script cannot load seven tables into today's schema, and a `--reset` run leaves them and four 2.0 tables empty; that was checked on a scratch database without running the script. SEC1 comes before any rich text. A docs-only warning is open as PR #2 on the fork (BUG-052), and the script is unchanged. The rest wait for the owner, and none is fixed silently inside another PR. Until ML1 lands, nobody runs `--reset` against a database on the current schema | ML1 |
