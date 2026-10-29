# Dataset due diligence

Research-only due diligence on the first candidate datasets and the reference
sources they need, as the brief's licensing gate asks (brief §7). It feeds
milestone RD and Phase 4 in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md),
and open questions C1 and C5, and B12, B13, B14, C13 and C14 (decided on
2026-10-29), in
[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

**Status: research only, 2026-10-29.**

- No data file was downloaded, no account was created, and no maintainer was
  contacted.
- Every fact comes from a page read on that date, and every licence statement
  is quoted with its URL. Facts that no fetched page confirms are marked
  *(unverified)*.
- What a licence permits is a reading for your review, not legal advice.
- Nothing here approves a release. Each dataset still needs its
  due-diligence record marked complete, and each release its licence
  snapshot and your recorded decision for each scope (architecture §3.7).

**How it was checked.** One researcher per dataset family read publication
pages, landing and download pages, documentation and licence pages, never
data files. A second, adversarial reviewer then re-read the cited sources and
checked each claim. Section 7 lists what that check changed.

## 1. Summary

| Dataset | Scientific suitability | Format | Mapping requirements | Licensing limitations | Difficulty |
|---|---|---|---|---|---|
| **TRRUST v2**, human part (R1 lead) | Good for directed TF → target regulation with a stated effect: activation, repression or unknown, each row backed by PubMed ids. Literature-curated from sentences and abstracts, so edges are "literature-curated regulation", never proof of direct binding | One small TSV, dated 16/04/2018, plus a BioC XML file. Four header-less columns *(unverified; inferred from one third-party parser)* | Symbols only. Needs the HGNC reference release first; a symbol maps only when it is unique across approved, previous and alias symbols, and drifted or ambiguous symbols are reported | Data under **CC BY-SA 4.0**. Internal use and public view are permitted with attribution and a note of changes. Bulk export only under the same licence, and on a cautious reading never mixed with incompatible terms. Whether ShareAlike reaches the whole database (Section 4) is open (C13) | Medium |
| **BioPlex 3.0**, HEK293T and HCT116 (R2 lead) | Good for co-complex association (AP-MS, PSI-MI MI:0914), never binding. Three classifier scores per pair, per cell line; only pairs at or above pInt 0.75; no negatives | Two TSVs (12 MB and 7.4 MB), named Dec 2019 but modified 2024-01-19 with no changelog. Nine columns *(unverified; inferred from BioPlexR and BioPlexPy code)* | BioPlex's own identity is the Entrez Gene id; rows also give UniProt accessions from 2013–2019, some with isoform suffixes. Needs RF1a's HGNC and UniProt releases; suffixed accessions map to entries through verified `isoform_of` links (B12) | **No licence on the BioPlex site.** Only the Explorer website and its images are CC BY-SA 4.0. The paper's NDEx deposits carry a CC0 rights field, set by an NDEx curator for Harvard Medical School, which may or may not extend to the website files. Public view and redistribution wait for the BioPlex team to confirm (C14) | Medium technically; **blocked on terms** |
| **Costanzo et al. 2016**, yeast SGA (N1) | Good for genetic interactions: ε against a multiplicative null, a P-value, and a sign. Raw files hold every tested pair; rows are allele-level (deletion, temperature-sensitive, DAmP) | Pair-wise 11-column file in a 385–521 MB zip, about 23 million double mutants, plus matrix and similarity files | Systematic ORF names, and strain or allele names. Needs an SGD reference release (RF1b), the yeast identity namespace (D7), the taxon choice (559292 or 4932) and allele parsing | Dryad copy under **CC0**: internal use, public view and redistribution permitted. Other copies carry no licence or an MIT statement | High |
| **Horlbeck et al. 2018**, human CRISPRi (N1) | Good for genetic interactions: a gene-level GI score against a quadratic-fit null, positive "buffering", negative "synergistic"; 472 × 472 genes in K562 and Jurkat; full matrices | Mendeley Data zips (145 MB and 94 MB) of processed maps; raw reads in GEO | 2018 gene symbols; needs only the HGNC release (RF1a), with previous-symbol resolution (TMEM261 is now DMAC1). No protein mapping | Mendeley copy under **CC BY 4.0**: all scopes permitted with attribution and a note of changes | Medium |
| **Helwak et al. 2013**, CLASH (N2) | Good for miRNA–target binding: each AGO1 chimera is a direct binding observation, never evidence of repression. 18,514 interactions, with base-pairing class, seed type and folding energy; AGO1 in one cell line only | Three small text supplements (Data S1 4.4 MB, the main file); processed GEO files from a different pipeline | Frozen references: miRBase release 15 names to map to MIMAT accessions, and Ensembl release 60 transcripts to map through stable-id history, with binding sites tied to old transcript sequences. Needs mature-miRNA and transcript reference releases | Article **CC BY 4.0**, which likely but not certainly covers the supplements; GEO places no NCBI restrictions | Medium |

**Recommendations.**

1. **R1: keep TRRUST v2 (human) as the lead.** It is scientifically suitable
   and its terms allow internal use and public view now. As decided (C13),
   its CC BY-SA 4.0 attribution travels with every release, and its data are
   exported only on their own, never combined with other releases, until the
   ShareAlike implications have been evaluated.
2. **R2: BioPlex 3.0 cannot yet be shown or exported.** The BioPlex site
   states no data licence, but the paper's NDEx deposits carry a CC0 field
   set by an NDEx curator. One short question to the BioPlex team, whether
   that waiver is authorised and covers the website files, could settle it.
   As decided (C14), a short inquiry is drafted in Appendix A and goes out
   only with your approval, and an alternative publication-specific protein
   interaction dataset is researched in parallel. Scientifically, BioPlex is
   co-complex association evidence (B14), and its isoform accessions map to
   UniProt entries through verified links (B12).
3. **RF1a: HGNC is a strong reference.** Its data are CC0, its ids are
   stable, and it gives NCBI Gene and Ensembl cross-references. For the
   gene-to-protein mapping, HGNC's UniProt column is the lead candidate,
   imported as its own release; UniProt's own HGNC cross-references are the
   alternative (§3.3).
4. **N1: start with Horlbeck, then Costanzo.** Horlbeck needs only RF1a's HGNC
   release and is CC BY 4.0, and it adds a human genetic layer that joins the
   same gene entities as R1. Costanzo needs a yeast reference release, D7 and
   allele handling first.
5. **N2: Helwak is suitable but needs reference work.** Its article is
   CC BY 4.0 and its chimeras are clean binding evidence, but its miRBase 15
   names and Ensembl 60 transcripts must be mapped to current releases first,
   and miRBase 15 itself is not on the miRBase website.
6. **Scores never pool across sources.** SGA ε, CRISPRi GI and BioPlex pInt
   are different score types with different null models or meanings, and
   their magnitudes are never compared.

## 2. Datasets

Each record follows the brief's checklist (brief §7). **No dataset is
approved for any scope.** Each needs your decision per release.

### 2.1 TRRUST v2, human part (R1 lead)

**Publication.** Han H, Cho JW, Lee S, et al. "TRRUST v2: an expanded
reference database of human and mouse transcriptional regulatory
interactions." *Nucleic Acids Research* 2018;46(D1):D380–D386. DOI
10.1093/nar/gkx1013; PMID 29087512; PMCID PMC5753191. The predecessor, Han et
al., *Sci Rep* 2015;5:11432 (DOI 10.1038/srep11432; PMID 26066708), documents
the curation rules. The site asks users to "cite the original TRRUST
publication", which could mean either paper; its "How to cite" block shows
v2, so citing both is the safe choice.

**Data and version.**

- Download page: https://www.grnpedia.org/trrust/downloadnetwork.php. The
  human row lists `trrust_rawdata.human.tsv` and
  `trrust_rawdata.human.bioc.xml`, dated 16/04/2018, with the release note
  "Some typological errors in the data have been fixed."
- No sizes, checksums, column definitions or README are published. The
  sha256 is recorded at retrieval, and a later different hash is a new
  release.
- The home page's update log adds "2021.11.23 Some mismatches between network
  and search table have been fixed". Whether that changed the download files
  is unknown.
- Counts disagree: the paper and home page give 8,444 human interactions for
  800 TFs; the download page gives 8,427 links for 795 TF genes. Table 1 of
  the paper gives 4,866 of the 8,444 a mode of regulation. (The download
  page's "non-TF genes" and Table 1's target genes are different measures.)

**Licence, as stated.**

- TRRUST home page (https://www.grnpedia.org/trrust/), in its About
  introduction: "TRRUST network data are freely available for both academic
  and commercial use under the Creative Commons Attribution-ShareAlike 4.0
  International (CC BY-SA 4.0) license. Users may use, share and adapt the
  data, provided that appropriate attribution is given and adapted material
  is distributed under the same license, where applicable." A separate
  footer, after the Funding section, adds "This work is licensed under a
  Creative Commons Attribution-ShareAlike 4.0 International License."
- The v2 paper: the interactions "are freely available as bulk downloadable
  files" "under a Creative Commons Attribution-ShareAlike 4.0 International
  license".
- The article itself is under a different licence, which covers the text and
  figures, not the data: its licence line names the "Creative Commons
  Attribution License" but links to by-nc/4.0 and says it "permits
  non-commercial re-use".
- The superseded v1 page said the data were "freely available for
  non-commercial research". The explicit v2 statements allow commercial use;
  record the old wording as an ambiguity, not a blocker.
- The download page carries no licence text, and whether the files carry a
  notice is unknown.
- No SPDX identifier is stated, so the field stays blank.

**What it permits** (a reading for your review):

| Scope | Reading |
|---|---|
| `internal` | Permitted |
| `public_view` | Permitted, with credit (TRRUST and its papers), the licence's name and link, and an indication of what was changed: symbols mapped to HGNC ids, modes recoded, PMIDs split (Section 3(a)). Displayed TRRUST-derived edges are shared adapted material under CC BY-SA 4.0, and the page says so (Section 3(b)) |
| `public_bulk` | Permitted with conditions. An export with TRRUST-derived rows is offered under CC BY-SA 4.0, a later version or a BY-SA-compatible licence, with its link and no added terms or technical measures (Section 3(b)). As decided (C13), TRRUST is exported only on its own, never in a combined export, until the ShareAlike implications have been evaluated |
| `external_services` | The licence sets no restriction |

Suggested licence snapshot: redistribution `permitted_with_conditions`;
commercial use permitted; modification permitted under ShareAlike.

Section 4 of CC BY-SA 4.0 applies only "Where the Licensed Rights include Sui
Generis Database Rights that apply to Your use of the Licensed Material".
Then a database in which the user has such rights, and which includes "all
or a substantial portion" of the licensed database, is itself adapted
material for ShareAlike (Section 4(b)). Both conditions are open: whether
TRRUST's licensor holds such rights, and whether an openPIP hub would. That
is a question for a licensing reviewer (C13).

**Contents.** An edge table: TF symbol, target symbol, mode (Activation,
Repression or Unknown; the v2 paper names only activation and repression,
so "Unknown" rests on the v1 rule and third-party code) and PubMed ids separated by `;`, in a header-less
four-column TSV *(unverified; inferred from OmnipathR's reader, the only
third-party code found that parses the file in active code)*. There is no record id, score, cell type, method or tested-negative
row. A pair with evidence for both modes appears twice, once per mode. What
the BioC file holds, such as abstract sentences, is undocumented, so it is
not imported until its content and copyright are checked.

**How rows were made.** Text mining only found and ranked candidate
sentences in Medline abstracts; people curated the interactions. For the v1
rows, "All primarily identified TF-target regulatory interactions were
confirmed by a more experienced curator". For the human rows added in v2,
the paper says only "we have performed manual curation for an additional top
4,000 candidate sentences for humans", without saying who confirmed them.
The v1 acceptance criteria include promoter binding alone and a change in
the target's expression after the TF changes, which may be indirect. The
database is not exhaustive: "We conducted manual curation for only the top
∼28 000 and ∼24 000 sentences for humans and mice, respectively" (v2).

**Organism and identifiers.** Human (taxon 9606; TRRUST states no taxon id).
Both endpoints are gene symbols from curation between 2014 and 2018. v1
required candidate sentences to contain "official gene symbols", but names
no HGNC release. How many are now previous symbols, aliases or withdrawn can
only be measured in a dry run. HGNC is renaming placeholder symbols
(`C%orf#`, `FAM#`, `KIAA#`; for example C9orf131 became SPATA31G1), which are
the likeliest sources of drift.

**Mapping plan.**

1. Load the HGNC reference release first (RF1a), preferably a dated quarterly
   archive with the withdrawn file of the same date (§3.1).
2. Resolve each symbol in the human symbol namespace, `hgnc_symbol`, whose
   roles keep approved (`symbol`), previous (`secondary`) and alias symbols
   apart. A symbol maps only when exactly one gene holds it in any of the
   three roles (basis `alias_unique`), and the report says which role
   matched. A value that is one gene's approved symbol and another gene's
   previous symbol or alias is `ambiguous`: both candidates are reported and
   neither is imported. Alias-only matches deserve the closest review,
   because HGNC says aliases "may be from literature, from other databases or
   may be added to represent membership of a gene group". Withdrawn entries
   are `unresolved`; a symbol withdrawn by a merge is a candidate for review.
   A match that differs only in case is a `candidate`, never `resolved`.
3. Report symbol drift with HGNC's `date_symbol_changed`. Resolving against an
   HGNC snapshot from early 2018 and carrying genes forward by HGNC id would
   reduce ambiguity, if such a quarterly file still exists *(unverified)*.
4. Both endpoints are genes. The TF is never upgraded to its protein
   (architecture §4.2), and TRRUST needs no protein or `encodes` link.
5. One assertion per PubMed id, with role `reported_in`. Activation →
   `activating`, Repression → `inhibiting`, Unknown → `unknown`; the raw mode
   is kept, and any other value rejects the row.
6. The record key is the line number in the checksummed file.
7. Expect a pair with both modes (two records, summary `mixed`), possible
   duplicate rows, possible self-regulation rows, and `MIR*` gene symbols,
   which stay genes, never mature miRNAs.

**Fit with the typed model.** `transcriptional_regulation`, ordered from the
TF's gene to the target gene. Outcome `supported`; effect from the mode.
TRRUST is a literature-curated resource, not a single study, so under open
question C12's default its resource-level statements ("small-scale
experimental studies", which only the website says, and manual curation) stay release metadata, shown in the
panel, and each row's `evidence_basis` and `assertion_method` are `unknown`.
You can decide otherwise under C12. The basis is never `text_mining`: text
mining only ranked sentences. Independent evidence counts distinct papers,
which suits TRRUST. Edges are labelled "literature-curated TF → target
regulation"; a missing edge is not evidence against regulation.

**Mouse part.** Excluded by open question D10's default. It would need an MGI
reference release (MGI data are CC BY 4.0, and MGI is "the authoritative
source of official names for mouse genes"), a mouse gene identity namespace,
and a case-exact mouse symbol namespace, because TRRUST's mouse names came
from NCBI Gene and differ from human ones mainly by letter case.

**Difficulty: medium.** The file is small and the vocabulary has three
values. The work is the HGNC prerequisite, symbol drift, the undocumented
layout, and ShareAlike.

**Still unknown.** The exact layout; which count matches the file; whether
the 2021 fix changed the files; how many symbols have drifted; how complex
names such as NF-κB became gene rows; what the BioC file contains; the
attribution wording TRRUST prefers beyond citing the paper.

### 2.2 BioPlex 3.0 (R2 lead)

**Publication.** Huttlin EL, Bruckner RJ, Navarrete-Perea J, et al. "Dual
proteome-scale networks reveal cell-specific remodeling of the human
interactome." *Cell* 2021;184(11):3022–3040.e28. DOI
10.1016/j.cell.2021.04.011; PMID 33961781; PMCID PMC8165030 (author
manuscript). Earlier versions: BioPlex 1.0 (*Cell* 2015, PMID 26186194) and
2.0 (*Nature* 2017, PMID 28514442); 3.0 re-analyses their AP-MS data.

**Data and version.**

- https://bioplex.hms.harvard.edu/interactions.php links files under
  https://bioplex.hms.harvard.edu/data/:
  - `BioPlex_293T_Network_10K_Dec_2019.tsv` (12 MB) and
    `BioPlex_HCT116_Network_5.5K_Dec_2019.tsv` (7.4 MB), both last modified
    2024-01-19, with no changelog;
  - directed bait–prey files (2022-09-09) and bait lists (2022-12-15), whose
    columns are undocumented;
  - unfiltered lists (1.1 GB and 774 MB), which carry "WARNING: this list has
    not been filtered to remove background and nonspecific interactors. Use
    with caution." They are never imported.
- The paper's own URLs (`downloadInteractions.php`, `downloadData.php`) return
  404. Its printed HCT116 NDEx DOI (10.18113/N9N012) also returns 404;
  10.18119/N9N012 resolves to "BioPlex 3 - HCT116", so the printed prefix is
  a typo.
- NDEx holds the paper's deposits (version APR-2021, keyed by NCBI Gene):
  "BioPlex 3 - HEK293T" (127,732 edges), "BioPlex 3 - HCT116" (75,346) and
  "BioPlex 3 - Shared Interactions" (35,704). They are not the same as the
  website files: besides the pairs at pInt ≥ 0.75 they include
  low-stringency pairs (cut-off 0.1) that passed in the other cell line
  (9,570 for 293T, 4,380 for HCT116), which an import would drop.
- The `/data/` listing also holds undocumented `.sql` dumps, never used as
  import sources, and BioPlex's own Explorer calls the second network
  "HCT116 (v1.0)".
- The paper's Table S1 (A–E) holds the same networks as an `.xlsb` file in
  the PMC author manuscript.
- The same page offers unpublished quarterly releases, with the request that
  users "refrain from publishing large-scale analysis or data mining studies
  using this data until we have published its contents". They are out of
  scope.

**Licence, as stated.**

- **No data licence statement was found** on the home, interactions,
  download, CompPASS or contact pages, in the `/data/` listing (no LICENSE or
  README), or in the paper's data statement. The download page says
  "Citations for the below data, please cite appropriately", and every page
  carries "© 2017-2023 Gygi Lab & The President and Fellows of Harvard
  College".
- The BioPlex Explorer help page (https://bioplex.hms.harvard.edu/explorer/help):
  "The BioPlex Display website was developed and is licensed under the
  Creative Commons Attribution-ShareAlike 4.0 International License", and so
  are the images it generates. This names the website and its images, not the
  files under `/data/`.
- The article: "© 2021 Elsevier Inc.", under the Elsevier user licence from
  2022-05-27, which lets users "access, download, copy, translate, text and
  data mine (but may not redistribute, display or adapt) the articles for
  non-commercial purposes". This covers the article; whether it also covers
  the supplementary tables is not stated.
- The bioRxiv preprint: "All rights reserved. No reuse allowed without
  permission."

- The NDEx deposits the paper cites carry the metadata field rights
  "Waiver-No rights reserved (CC0)", rightsHolder "Harvard Medical School".
  The field was set by an NDEx curator account, not published by the BioPlex
  team, and it covers the NDEx copies, not the website's `/data/` files.

**What it permits:** nothing on the BioPlex site grants a licence for the
data. The NDEx CC0 field is a strong lead: if it is authorised, the NDEx
copies could be used in every scope. The files are also offered openly with
a request to cite, which may be enough for internal use on a private stack;
that is your judgement. Keep the release unapproved for `public_view` and
`public_bulk` until the BioPlex team confirms in writing whether the CC0
waiver is authorised and whether it extends to the website files (C14).

**Contents.** Bait–prey pairs only, with reciprocal pairs merged ("When pairs
of baits were found to associate reciprocally, their CompPASS-Plus scores
were merged"; the formula is not given). Columns *(unverified)*: `GeneA`,
`GeneB` (Entrez Gene ids), `UniprotA`, `UniprotB`, `SymbolA`, `SymbolB`, `pW`,
`pNI`, `pInt`. The three scores are a Naive Bayes classifier's likelihoods of
a wrong identification, non-specific background and a "bona fide interacting
partner", trained per 96-well plate within each cell line. Pairs are kept at
pInt ≥ 0.75, "the top ~2% of candidate interactions". No source calls pInt
calibrated. There are no tested-negative records.

**Scale.** 293T: "118,162 interactions among 14,586 proteins" from 10,128
baits. HCT116: 5,522 immunoprecipitations; about 71,000 interactions among
10,531 proteins (website), 70,966 (BioPlexR vignette). 35,704 interactions are
shared.

**Organism and identifiers.** Human proteins in two human cell lines,
HEK293T (Cellosaurus CVCL_0063) and HCT 116 (CVCL_0291). BioPlex's own unit
of identity is the gene: "All subsequent analysis was performed on data
assembled at the Gene ID level" "to address complications due to protein
isoforms", and the two networks were merged as "a pair of linked Entrez Gene
ID's". The UniProt columns come from a 2013 search database and 2019
annotation; most are entry accessions, and some are isoform identifiers such
as `Q8N7W2-2`.

**Mapping plan.**

1. Pin each file by sha256 and retrieval date, and check it against Table S1
   B and D.
2. Gene layer: map `GeneA` and `GeneB` to HGNC ids through the HGNC release's
   NCBI Gene cross-references (RF1a). Report ids missing from that release,
   replaced since 2019, or reaching more than one HGNC record.
3. Protein layer: resolve `UniprotA` and `UniprotB` against a pinned UniProt
   release (RF1a). An accession that is now secondary is a `candidate`, and a
   deleted one is `unmatched`.
4. **Isoform-suffixed accessions** (decided, B12). BioPlex's analysis is
   gene-level, so a suffix such as `Q8N7W2-2` is not an isoform-specific
   observation, and BioPlex is modelled as protein-product evidence. The
   adapter declares the source not isoform-specific, and each suffixed
   accession, `-1` included, maps to its UniProt entry only through a
   verified `isoform_of` link from the approved UniProt release (basis
   `isoform_entry`). The participant keeps the accession as written, the
   import run records the UniProt release used, the isoform entity is never
   merged into the entry, and an isoform with no verified link is reported.
   The suffix is never stripped as a string, as BioPlexR and BioPlexPy do for
   lookup.
5. `encodes` links come only from the approved mapping release, never from
   the gene–UniProt pairing in BioPlex rows.
6. Bait and prey roles come from the directed files or bait lists, never from
   column order in the undirected lists.
7. One release per cell line, because they are separate experiments. If 1.0
   or 2.0 is ever imported, it must not count as independent evidence beside
   3.0, which re-analyses the same immunoprecipitations.

**Fit with the typed model** (decided, B14). AP-MS co-complex association
evidence: `physical_interaction` at level `association`, labelled "co-complex
association (AP-MS)", with the type marked as assigned by openPIP from the
stated assay, because BioPlex states no PSI-MI type. Never physical
association, direct interaction or a binary interaction. The quoted assay is
recorded for the whole release (C12). Spoke expansion (MI:1060); detection anti-tag
coimmunoprecipitation (MI:0007, under MI:0019 coimmunoprecipitation); identification by mass spectrometry
(MI:0427). Outcome `supported`; effect `not_applicable`. Three typed scores
per cell line, compared only within a cell line; the 0.75 threshold is quoted
for the whole release.

**Difficulty: medium** technically: small files and one score family. The
work is the missing licence, gene-level identity with mixed UniProt
accessions, unversioned files, undocumented role columns and the reciprocal
merge.

**Still unknown.** Whether the NDEx CC0 waiver is authorised and covers `/data/`; whether CC BY-SA 4.0 extends to `/data/`;
the exact headers; the directed files' columns; the reciprocal merge formula;
why some rows carry isoform accessions, and which UniProt and NCBI Gene
snapshots annotated them; whether the 2024 files equal the Dec 2019 data; the
licence of the supplementary tables.

### 2.3 Costanzo et al. 2016, yeast global genetic interaction network (N1)

**Publication.** Costanzo M, VanderSluis B, Koch EN, et al. "A global genetic
interaction network maps a wiring diagram of cellular function." *Science*
2016;353(6306):aaf1420. DOI 10.1126/science.aaf1420; PMID 27708008; PMCID
PMC5661885 (author manuscript). Score semantics come from Costanzo et al.
2010 (*Science* 327:425–431) and Baryshnikova et al. 2010 (*Nat Methods*
7:1017–1024). The distribution site is described in Usaj et al. 2017 (*G3*
7:1539–1549).

**Data and version.** Four copies exist, and their sizes differ, so whether
their contents are identical is unknown:

- **Dryad, the recommended copy:** doi:10.5061/dryad.4291s. Version 3 of
  2025-06-09 (539.57 MB in total; Data File S1 zip 431.58 MB) added a file
  of strain ids and single-mutant fitness; version of 2017-08-27 (S1 zip
  385.15 MB).
- TheCellMap.org (https://thecellmap.org/yeast/costanzo2016/, "updated May
  2016"): a pair-wise zip (521 MB), a matrix zip (35 MB) and profile-similarity
  matrices (145 MB). The URL the paper cites now returns 404.
- The Boone lab supplementary site: Data Files S1–S17.
- BioGRID's re-host: a stringent, gene-collapsed subset and a copy of the raw
  pair-wise file.

**Licence, as stated.**

- Dryad metadata: `"license": "https://spdx.org/licenses/CC0-1.0.html"`.
  Dryad's terms: submitters "make the Dataset available to the public under a
  CC0 instrument"; users "are allowed and encouraged to reuse Datasets"; "It
  is hoped and expected that Users will follow accepted scholarly norms for
  citing data."
- TheCellMap.org and the Boone lab site: no licence statement; footer "© Boone
  Lab 2026".
- BioGRID: "All data provided are 100% freely available to both academic and
  commercial users under the MIT License". Whether BioGRID can license the
  authors' raw file is not established; the Dryad copy avoids the question.
- The Science article page returned HTTP 403, so the journal's terms were not
  read. The PMC copy is an author manuscript.

**What it permits** (Dryad copy): all four scopes, with citation by scholarly
norm. SPDX `CC0-1.0` is stated by the source.

**Contents.** Pair-wise rows of one strain pair each: query and array strain
or allele, array type (DMA or TSA) and temperature (26 or 30 °C), ε, P-value,
both single-mutant fitnesses, double-mutant fitness and its standard
deviation. The supplementary site says the raw files hold "raw interaction
data corresponding to all tested gene pairs", with one filter: reciprocal
pairs that are both significant at the intermediate tier but opposite in sign
are removed. BioGRID, however, describes the same raw file as a "Full data set
of 550,000 negative and 350,000 positive genetic interactions", so whether it
really holds every tested pair is checked in the dry run before any
`tested_absent` decision (B13). Scale: "~23 million double mutants
encompassing 5,416 different genes"; "~550,000 negative and ~350,000 positive
genetic interactions". BioGRID's own tab2 copy keeps one score per gene pair
(the strongest allele), so it is a lossy view and never the source of truth.

- **Score:** the SGA genetic interaction score ε against a multiplicative
  null: "a significant deviation in fitness compared with the expected
  multiplicative effect of combining two single mutants" (Costanzo et al.
  2010). The explicit formula and how P-values are computed were not found
  *(unverified)*.
- **Sign:** negative is "a fitness defect that is more extreme than
  expected"; positive is "a fitness that is greater than expected".
- **Thresholds:** lenient (P < 0.05); intermediate (P < 0.05 and |ε| > 0.08,
  the paper's figures and TheCellMap's default); stringent (P < 0.05 with
  ε < −0.12 or ε > 0.16).
- Also in the files: DAmP and HSP90 subsets; profile-similarity matrices (a
  derived functional association, not a genetic interaction); suppression
  scores (a separate, ordered type if ever imported).

**Organism and identifiers.** *Saccharomyces cerevisiae*; the pages name only
the species, and the strain background is in the unreadable supplement. Two
column descriptions exist: strain id and allele name (TheCellMap, BioGRID) or
ORF and gene name (Dryad README, Boone site). Gene identity rests on SGD
systematic ORF names, such as `YLR135W`; there are no SGD ids or NCBI Gene
ids. Allele types come from strain labels (`sn`, `tsq`, `damp`) and the array
type; "-supp" marks strains carrying a suppressor.

**Mapping plan.**

1. An approved SGD reference release (RF1b) for S288C: systematic name, SGD
   id, standard name, aliases and feature status. SGD's download server reset
   the connection on 2026-10-29, so its files are unverified.
2. Settle D7, the yeast identity namespace: SGD ids or systematic names.
   Either way an ORF maps through the SGD release, and gene names never
   define identity.
3. Inspect the header of the chosen copy before writing the adapter, because
   the copies describe the columns differently. Parse ORF, allele name and
   allele type without guessing.
4. Report ORFs that SGD has since merged, deleted or marks dubious; never
   remap them by hand. BioGRID dropped "21,039 merged/dubious ORFs" from its
   subset.
5. Taxon: SGD maps its gene ids to 559292 ("specific to the S288C strain used
   for the S. cerevisiae reference genome sequence"), which NCBI ranks as a
   strain under species 4932. The manifest records which id it uses and why, and
   `Taxon.parent` links the two (RF1b).

**Fit with the typed model.** `genetic_interaction` (symmetric) between yeast
genes, with alleles as context (open question B3). `genetic_sign` from the
sign of ε, on supported assertions only, relative to a score type that states
the multiplicative null. Scores: ε, P-value, both single-mutant fitnesses,
double-mutant fitness and its standard deviation, raw values kept (P-values of
0 occur). Context: allele, allele type, query or array role, array type,
temperature. Each strain-pair row is one assertion: reciprocal A–B and B–A
strains are separate measurements, and several alleles of one gene can
disagree in sign, so an edge may summarize as `mixed` (BioGRID counts 1,156
such pairs in its stringent set). Import one declared threshold tier as
`supported`. As decided (B13), a sub-threshold pair is never a confirmed
non-interaction: if a release imports such pairs, they are `not_significant`,
with ε, the P-value and the threshold kept, never `tested_absent`.

**Difficulty: high.** No yeast reference release exists yet, D7 is
undecided, the rows are allele-level, the copies disagree on columns, and the
raw file is large. A thresholded import of the intermediate tier from Dryad
would be about medium once the SGD reference exists.

**Still unknown.** Whether the copies are identical; the real header and
strain id format; how P-values were computed; the strain background; which
SGD annotation the authors used; Science's terms for the supplement.

### 2.4 Horlbeck et al. 2018, human CRISPRi genetic interaction maps (N1)

**Publication.** Horlbeck MA, Xu A, Wang M, et al. "Mapping the Genetic
Landscape of Human Cells." *Cell* 2018;174(4):953–967.e22. DOI
10.1016/j.cell.2018.06.010; PMID 30033366; PMCID PMC6426455 (author
manuscript).

**Data and version.**

- **Mendeley Data, the recommended copy:** "Mapping the genetic landscape of
  human cells", version 1, 21 July 2018, doi:10.17632/rdzk59n6j4.1. Its public
  metadata lists 11 files, including `K562.zip` (145,276,911 bytes),
  `Jurkat.zip` (94,300,357 bytes), TreeView maps and cluster annotations. The
  paper says the processed maps are there "in tab-delimited text format".
- Raw reads: GEO GSE116198. The GEO page returned only a browser-verification
  page.
- Cell supplementary tables S1–S7, including S5, "sgRNA-level and gene-level
  GI scores and correlations". The publisher pages returned HTTP 403.
- Code: https://github.com/mhorlbeck/GImap_tools (MIT).
- A readable copy of the author manuscript is in eScholarship (UCSF), item
  0465k3fx.

**Licence, as stated.**

- Mendeley Data: "CC BY 4.0" (landing page); metadata `short_name` "CC BY
  4.0", `url` "http://creativecommons.org/licenses/by/4.0". Its description
  notes that third-party content may need further permission.
- GEO, through NCBI's policy: "NCBI itself places no restrictions on the use
  or distribution of the data contained therein", while submitters "may claim
  patent, copyright, or other intellectual property rights".
- Cell supplementary tables: terms unknown; use the Mendeley copy.

**What it permits** (Mendeley copy): all four scopes, with attribution, a
licence link and an indication of changes.

**Contents.** Dual-sgRNA CRISPRi growth screens in K562 and Jurkat cells: "a
dual sgRNA library of 1,044,484 pairwise CRISPRi genetic perturbations
targeting 222,784 gene pairs" "(472 genes × 472 genes)". Genes were chosen for
a growth phenotype in an earlier screen, so they are not a random sample.

- **Score:** "GIs were then calculated by subtracting the expected pair
  phenotype" from a quadratic fit of single versus pair phenotypes,
  z-standardized to negative-control pairs, made symmetric, and averaged over
  sgRNA pairs to give gene-level GIs. The null is an empirical quadratic fit,
  neither multiplicative nor additive.
- **Sign:** positive is buffering and negative is synergistic (SSL). This
  follows from GI = measured − expected, with slower growth negative, and
  matches BioGRID's labels; the figure legend that states it could not be
  re-read *(unverified as a quote)*.
- **Thresholds:** a 5% FDR (the numeric cut-off was not found) and a "strong"
  cut-off of ±3, which BioGRID uses. Both were calibrated on K562's negative
  controls and applied to Jurkat by the authors' choice. Strong GIs are "just
  2.2% of total gene-gene interactions measured".
- The maps are full symmetric matrices, so non-interacting pairs are present
  as near-zero scores *(the files were not inspected)*.
- Caveat from the paper: CRISPRi "can repress expression of TSSs within ~1kb
  of the target site", and Table S2 annotates pairs with a possible neighbour
  effect.

**Organism and identifiers.** Human (9606). Endpoints are 2018 gene symbols:
`TMEM261`, named in the abstract, is now `DMAC1` (HGNC:30536, per the Human
Protein Atlas). HGNC lists TMEM261 as a previous symbol, changed on
2017-06-01, before the paper appeared, so even at publication the symbols
were not all current. Perturbations are sgRNA
ids. The identifier columns in the zips were not inspected.

**Mapping plan.** The HGNC release (RF1a) only, with previous-symbol
resolution under the same rules as TRRUST; no protein mapping, because the
claims are gene-level. One unordered pair per matrix cell above the diagonal,
never counted twice; self-pairs flagged or dropped. One assertion per cell
line; the combined K562 and Jurkat map is not imported as independent
evidence. The neighbour-TSS annotation becomes a flag, never a remapping.

**Fit with the typed model.** `genetic_interaction` (symmetric) between human
genes. `genetic_sign` from the gene-level GI, relative to a score type that
states the quadratic-fit null, the z-standardization and the gene-level
averaging; never pooled or compared with yeast ε. Context: cell line;
perturbation "CRISPRi (dCas9-KRAB) knockdown" with sgRNA ids; pooled growth
screen. Labels: "CRISPRi GI (K562)", "buffering" and "synergistic (SSL)". GI
correlations are profile similarity, not genetic interactions. Pairs below
the threshold, if imported, are `not_significant` with their scores, never
`tested_absent` (B13).

**Difficulty: medium.** Small and licence-clear, with documented score
semantics. The work is the uninspected file layout, symbol drift, matrix to
pairs, the threshold choice and the neighbour-TSS flag.

**Still unknown.** The zips' layout and identifier columns; the numeric FDR
cut-off; the number of drifted symbols; the genome build behind the sgRNA
ids; GEO's file list; the Cell supplement's terms.

### 2.5 Helwak et al. 2013, human CLASH miRNA interactome (N2)

**Publication.** Helwak A, Kudla G, Dudnakova T, Tollervey D. "Mapping the
Human miRNA Interactome by CLASH Reveals Frequent Noncanonical Binding."
*Cell* 2013;153(3):654–665. DOI 10.1016/j.cell.2013.03.043; PMID 23622248;
PMCID PMC3650559. The analysis pipeline is described in Travis et al.,
*Methods* 2014 (Hyb; CC BY 3.0).

**Data and version.**

- Journal supplements on PMC: Data S1 (`mmc1.txt`, 4.4 MB), "Detailed
  Annotation of All Identified miRNA-mRNA Interactions", the primary import
  file; Data S2 (`mmc2.txt`, 8.5 MB), base pairing with mRNA targets; Data S3
  (`mmc3.txt`, 2.1 MB), base pairing with non-mRNA targets. The article does
  not document Data S1's columns, and the file was not opened.
- GEO GSE50452: processed `.hyb` files for experiments E1–E6 (0.2–3.3 MB
  each), built with the Hyb pipeline against its "hOH7" database, whose
  Ensembl and miRBase releases are undocumented. They may not match Data S1
  one-to-one. Raw reads: SRA SRP029351.
- GEO GSE46039 is a separate miR-92a knockdown microarray.

**Licence, as stated.**

- PMC and Europe PMC: "This work is licensed under a Creative Commons
  Attribution 4.0 International License, which allows reusers to distribute,
  remix, adapt, and build upon the material in any medium or format, so long
  as attribution is given to the creator. The license allows for commercial
  use." Elsevier's Crossref record dates CC BY 4.0 for the version of record
  from 2013-07-16, 106 days after publication, with only an Elsevier
  text-mining licence before that. The publisher's own pages returned HTTP
  403.
- Whether CC BY 4.0 covers the supplementary files is not stated anywhere
  read; likely, but unconfirmed.
- GEO, read through NCBI's policies and a mirror of the GEO disclaimer: "NCBI
  places no restrictions on the use or distribution of the GEO data", with
  the caveat that submitters may claim rights and NCBI has none to transfer.

**What it permits:** internal use; public view and redistribution with
attribution, provided Data S1 falls under the article's CC BY 4.0, which is
the remaining question for review.

**Contents.** "The 18,514 miRNA-mRNA interactions identified from chimeric
reads", covering "399 different miRNAs and 6,959 different protein-coding
genes", from six experiments with "slightly differing protocols" analysed
together. Each chimera is an miRNA and a target fragment ligated inside AGO1,
so it is a direct binding observation; the authors conclude that
"interactions recovered by CLASH were predominately formed in vivo". The paper
classifies duplexes into five base-pairing classes, calls seeds canonical or
noncanonical, and computes folding energies with UNAFold; 60% of targets lie
in coding sequence and 35% in 3′ UTRs. It cites read support ("supported by
275 chimeric reads", "found in five of six CLASH experiments"), but whether
Data S1 has count columns is unverified. About 11% of duplexes fall in a
weak-energy component that may include non-specific ligations. Functional
repression was tested only for miR-92a.

**Organism and identifiers.** Human; Flp-In T-REx 293 (HEK293) cells
expressing tagged AGO1 only. miRNAs come from "miRBase release 15" (April
2010). Targets are Ensembl transcripts: "The mRNA sequences and coordinates
in the database match those of ENST entries from ENSEMBL Release 60", on
GRCh37, with binding sites in transcript coordinates. Name formats in Data S1
are unverified.

**Mapping plan.**

1. A mature-miRNA reference release (N2): miRBase, current release 23.1
   (September 2026), with MIMAT accessions as identity and case-exact names
   as labels ("miR" is mature, "mir" the hairpin). Names are not stable and
   accessions are, so each release-15 name must be mapped to its MIMAT
   accession. miRBase's website offers previous releases 20 to 22 only, not
   15; the legacy FTP was not checked, and `miRNA.dead` lists retired
   entries. A name that cannot be mapped is reported, never guessed.
2. A transcript reference release (N2): Ensembl release 60 is gone from the
   web archive but still on the Ensembl FTP (`pub/release-60/`), so the
   original transcripts and binding sites can be kept as stated, then mapped
   to a pinned current release through stable-ID history. A transcript whose
   sequence changed is never silently re-mapped: either the site stays on
   the release-60 transcript or it is re-located by sequence, a decision for
   N2.
3. Genes: transcripts join genes through `transcribed_from` from the
   transcript release, and genes are HGNC entities, reached through HGNC's
   Ensembl cross-references. Retired or multiply mapped gene ids are
   reported.
4. Non-mRNA targets (Data S3) wait for an `ncrna` entity type.

**Fit with the typed model.** `mirna_binding`, from a mature miRNA to a
transcript; a binding observation with no effect, never repression. Outcome
`supported`; no `tested_absent`, because CLASH only reports what it recovers.
Folding energy is a typed score (a prediction, lower is stronger); class,
seed type and site region are attributes; context is the cell line and AGO1.
The miR-92a knockdown results could support a few separate `mirna_repression`
claims, from a separate import.

**Difficulty: medium.** The files are small and the article licence is
clear. The work is the undocumented Data S1 layout, frozen references
(miRBase 15 and Ensembl 60) with miRNA renaming since 2010, binding-site
coordinates tied to old transcripts, and the gap between Data S1 and the GEO
files.

**Still unknown.** Data S1's columns; whether CC BY 4.0 covers the
supplements; where to get miRBase release 15; whether the GEO files reproduce
the 18,514 set; whether to flag the weak-energy component.

## 3. Reference sources

### 3.1 HGNC: human gene reference (RF1a)

**What it provides.** Stable HGNC ids ("HGNC IDs remain stable even if a name
or symbol changes"), approved symbols, previous symbols, alias symbols,
status (Approved, Entry withdrawn, Symbol withdrawn by merge), NCBI Gene and
Ensembl gene cross-references, and UniProt accessions. HGNC's 2026 paper
recommends that "all databases track approved symbols using HGNC IDs".

**Licence.** https://www.genenames.org/about/license/: "all data is released
under the Creative Commons Public Domain (CC0) License. This means that any
form of reuse of the content is permitted." Attribution is recommended, not
required. All four scopes are permitted. The page names no CC0 version or
SPDX id, so the field stays blank. Whether CC0 also covers the mapped
cross-references that other resources supply is not stated.

**Files and versions.** TSV and JSON in a Google Cloud bucket, updated "on
each Tuesday and Friday". Monthly and quarterly archives of the complete set
and the withdrawn file are produced; monthly files over 365 days old are
deleted, quarterly files are kept. A quarterly file, with its date and
sha256, makes the best reference release. The REST API asks for at most 10
requests per second.

**Cautions.**

- UniProt links are to Swiss-Prot entries only ("We do not map to TrEMBL
  entries within UniProt"). HGNC's Custom Downloads lists its UniProt column
  under mapped data, "The UniProt identifier, provided by the EBI", and warns
  that mapped data "are not subject to our strict checking and curation
  procedures". The complete set's `uniprot_ids` column probably holds the
  same data, but HGNC does not say so *(unverified)*.
- Previous symbols can point at several genes, and aliases are not unique,
  so both yield candidates only.
- HGNC's own symbol checker ignores case by default; openPIP's human symbol
  namespace stays case-exact, and a case-only match is a `candidate`.
- The complete set is probably approved genes only, with withdrawn reports in
  a separate file, but its status field also lists "Entry Withdrawn"; the
  dry run checks.

### 3.2 UniProt: protein entries and isoforms (RF1a)

**What it provides.** Accessions: "6 or 10 alphanumerical characters", a
primary accession per entry and secondary accessions kept after merges and
splits; deleted accessions listed in `delac_sp.txt` and `delac_tr.txt.gz`.
Isoforms: "composed of the primary accession number of the entry, followed by
a dash and a number". The canonical sequence is chosen "for display", and in
UniProt's examples it is `-1`, but no fetched text says it always is, so
`-1` is never assumed to be the entry (architecture §4.1). Isoforms of one
gene can sit in different entries. `isoform_of` links come from the entries'
alternative-products records, which can name another entry's isoform.

**Licence.** https://www.uniprot.org/help/license: CC BY 4.0 for "all
copyrightable parts of our databases", with the caveat "We cannot provide
unrestricted permission regarding the use of the data" because "some data
may be covered by patents or other rights". All four scopes are permitted
with attribution.

**Files and versions.** Release 2026_03 of 2 September 2026. Releases come
"every 2-3 months", previous releases are archived for at least two years,
and after that only each year's first release (`YYYY_01`), which UniProt
recommends for reproducibility. The human reference proteome UP000005640 has
`UP000005640_9606.fasta.gz` (7.4 MB), an additional-isoform FASTA (40 MB), a
`gene2acc` file (901 KB) and an `idmapping` file (26 MB). Per-organism ID
mapping files exist (`HUMAN_9606_idmapping.dat.gz`, 37 MB). UniProt's NCBI
GeneID mapping is RefSeq-centric: a protein with no identical RefSeq protein
gets no GeneID.

### 3.3 Gene-to-protein mapping candidates (RF1a)

The mapping is its own release, approved separately, and it states only
`encodes` links from a gene to a UniProtKB entry (architecture D7).

| Candidate | What it states | Licence | Assessment |
|---|---|---|---|
| **HGNC's UniProt column** (lead) | HGNC id → Swiss-Prot accessions, in the same file as the gene set | CC0; whether HGNC can place CC0 on a column the EBI supplies is not stated | Gene-centric and reviewed entries only. HGNC treats it as mapped data supplied by the EBI, to be used "with some caution", so each `encodes` link is labelled with its source and "mapped, not curated by HGNC". Whether isoform accessions ever appear is unverified; an isoform would never be the target of `encodes` |
| **UniProt's HGNC cross-references** (alternative or cross-check) | UniProtKB accession → HGNC id, in the per-organism or proteome ID mapping files *(unverified that HGNC ids appear there)* | CC BY 4.0 | Protein-centric and can include unreviewed entries. Useful to check the HGNC column; a disagreement is reported, never resolved by guessing |
| NCBI gene2accession | Gene ids with GenBank and RefSeq accessions *(per NCBI's README; unverified here)* | NCBI's policy | **Not suitable:** it states no UniProtKB accession, so using it would need a second, inferred hop |

Either source gives a many-to-many link set: one gene can list several
entries and one entry several genes. Every such case is reported in the dry
run.

### 3.4 SGD: yeast gene reference (RF1b, for Costanzo)

**Licence.** "SGD operates under the Creative Commons Attribution 4.0
International license (CC BY 4.0)." All scopes are permitted with
attribution; SGD asks to cite the current genome release.

**Identifiers.** SGD ids (`SGD:S000000001`), systematic names (`YAL001C`;
"generated by the systematic sequencing project"), standard names ("three
uppercase letters followed by a number", with older exceptions) and aliases.
Reference strain S288C, annotation release R64.5.1 (May 2024). Since March
2014 SGD has used taxon 559292 for its gene ids. The download server was
unreachable on 2026-10-29, so file names and columns are unverified.

### 3.5 Smaller references

| Source | Used for | Licence, as stated |
|---|---|---|
| NCBI Taxonomy | Taxon rows and parents: 9606; 4932 and its child 559292 ("Saccharomyces cerevisiae S288C", rank strain) | "NCBI itself places no restrictions on the use or distribution of the data" |
| Cellosaurus | Cell lines: CVCL_0063 HEK293T, CVCL_0291 HCT 116 | "We have chosen to apply to Cellosaurus the Creative Commons Attribution 4.0 International License (CC BY 4.0)." |
| PSI-MI | Term labels and definitions for each adapter's pinned list | The repository's LICENSE file is the Creative Commons Attribution 4.0 International Public License. OLS shows no version, so a commit is pinned |
| MGI | Only for TRRUST's mouse part, which is excluded (D10). Its pages show a release label ("MGI 6.29") that can pin a reference release | "MGI data and annotations are licensed under a Creative Commons Attribution 4.0 International License (CC-BY)". Images are excluded: they are "the copyrighted work of the attributed author or publisher" |
| miRBase | Mature miRNAs for Helwak (N2): MIMAT accessions, case-exact names; release 23.1 (September 2026) | "miRBase is in the public domain. It is not copyrighted. You may freely modify, redistribute, or use it for any purpose." |
| Ensembl | Transcripts and genes for Helwak (N2). Release 116 (June 2026) was the last on the old platform; old releases remain on the FTP | "Ensembl imposes no restrictions on access to, or use of, the data provided", with a caveat that some data may carry third-party constraints. No terms page for the new 2026 platform was found |

## 4. What this changes in the plan

- **R1** keeps TRRUST v2 (human) as the lead. Its public view needs the
  attribution and note of changes above, and its bulk export stays per
  release, never combined (C13).
- **R2** cannot reach public view with BioPlex until C14 is settled; the NDEx
  CC0 field makes a short confirmation from the BioPlex team the likeliest
  route. Until then, milestone RD can run on a private stack only if you
  judge internal use acceptable. An alternative source is being researched
  in parallel (C14). Its rows are co-complex association evidence (B14), and
  its isoform accessions map to entries through verified links (B12).
- **N2** needs mature-miRNA (miRBase) and transcript (Ensembl) reference
  releases, and a source for miRBase release 15, before Helwak can be
  imported.
- **RF1a's mapping** uses HGNC's UniProt column, or UniProt's HGNC
  cross-references, as its own release. NCBI gene2accession is dropped
  (open question C5).
- **N1** starts with Horlbeck, which needs only RF1a. Costanzo waits for an
  SGD reference release (RF1b) and D7.
- **E1** refuses any combined export that includes a ShareAlike release,
  and carries each release's licence, attribution and note of changes (C13).

## 5. Questions this raised, now decided

These are in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md), marked **Decided**
(2026-10-29):

- **B12.** BioPlex is protein-product evidence: isoform accessions map to
  UniProt entries through verified `isoform_of` links, keeping the original
  identifiers and the mapping provenance.
- **B13.** Non-significant genetic-interaction scores are never confirmed
  non-interactions: they are `not_significant`, with their scores and
  statistics, apart from `tested_absent`.
- **B14.** BioPlex is AP-MS co-complex association evidence, kept apart from
  binary interactions.
- **C13.** TRRUST keeps its CC BY-SA 4.0 attribution per release, and no
  combined export includes it until ShareAlike has been evaluated.
- **C14.** The inquiry to the BioPlex team is drafted in Appendix A and is
  not sent without your approval; an alternative dataset is researched in
  parallel.

## 6. Sources

All read on 2026-10-29.

**TRRUST:** https://www.grnpedia.org/trrust/;
https://www.grnpedia.org/trrust/downloadnetwork.php;
https://pmc.ncbi.nlm.nih.gov/articles/PMC5753191/;
https://academic.oup.com/nar/article/46/D1/D380/4566018;
https://pubmed.ncbi.nlm.nih.gov/29087512/; the v1 article through Europe PMC
(PMC4464350); https://www.grnpedia.org/trrust/v1/;
https://github.com/saezlab/OmnipathR/blob/master/R/trrust.R;
https://creativecommons.org/licenses/by-sa/4.0/legalcode.en.

**BioPlex:** https://bioplex.hms.harvard.edu/ and its `interactions.php`,
`data/`, `download.php`, `comppass.php`, `contact.php` and `explorer/help`
pages; Europe PMC and Crossref records for DOI 10.1016/j.cell.2021.04.011;
the PMC author manuscript (PMC8165030); the BioStudies listing S-EPMC8165030;
https://www.biorxiv.org/content/10.1101/2020.01.19.905109v1;
http://www.elsevier.com/open-access/userlicense/1.0/; the BioPlexR vignette
and source; the BioPlexPy documentation and source.

**Costanzo:** https://pubmed.ncbi.nlm.nih.gov/27708008/;
https://pmc.ncbi.nlm.nih.gov/articles/PMC5661885/;
http://boonelab.ccbr.utoronto.ca/supplement/costanzo2016/;
https://thecellmap.org/yeast/costanzo2016/;
https://datadryad.org/stash/dataset/doi:10.5061/dryad.4291s and its API
record; https://datadryad.org/terms;
https://downloads.thebiogrid.org/BioGRID/Published-Datasets/Costanzo2016/;
PMC5600254 (Costanzo 2010); PMC3117325 (Baryshnikova 2010); PMC5427489 (Usaj
2017).

**Horlbeck:** https://pubmed.ncbi.nlm.nih.gov/30033366/;
https://pmc.ncbi.nlm.nih.gov/articles/PMC6426455/;
https://data.mendeley.com/datasets/rdzk59n6j4/1 and its public metadata;
https://github.com/mhorlbeck/GImap_tools;
https://www.ncbi.nlm.nih.gov/home/about/policies/;
https://www.proteinatlas.org/ENSG00000137038-DMAC1/summary/gene.

**Helwak:** https://pmc.ncbi.nlm.nih.gov/articles/PMC3650559/ and its Europe
PMC full-text record; https://api.crossref.org/works/10.1016/j.cell.2013.03.043;
the NCBI FTP listing and BioStudies record for GSE50452 (E-GEOD-50452);
https://www.ncbi.nlm.nih.gov/home/about/policies/; Travis et al. 2014
(PMC3969109); https://www.mirbase.org/download/LICENSE, the miRBase README and
https://mirbase.org/download/PREVIOUS_RELEASES/;
https://jun2026.archive.ensembl.org/info/about/legal/disclaimer.html, the
Ensembl stable-id and archive pages, and https://ftp.ensembl.org/pub/release-60/.

**BioPlex on NDEx:** the network summaries at
https://www.ndexbio.org/v2/network/{id}/summary for the three DOIs
10.18119/N9RP5D, 10.18119/N9N012 and 10.18119/N9H887.

**References:** https://www.genenames.org/about/license/ and the HGNC
download, archive and help pages; the UniProt help pages through
https://rest.uniprot.org/help/ and the UniProt FTP READMEs;
https://sites.google.com/view/yeastgenome-help and
https://www.yeastgenome.org/blog/changes-to-the-sgd-gaf-file-of-gene-ontology-annotations;
the NCBI Taxonomy pages for 559292, 4932 and 9606;
https://www.cellosaurus.org/description.html;
https://github.com/HUPO-PSI/psi-mi-CV;
https://www.informatics.jax.org/mgihome/other/copyright.shtml.

## 7. What the verification changed

The adversarial check re-read the cited sources for every record. It
confirmed most claims and changed these:

- **BioPlex:** it found the CC0 rights field on the paper's NDEx deposits,
  which the first pass had not read, and that those copies add
  low-stringency pairs. It confirmed that the printed HCT116 DOI is a typo
  and placed MI:0007 under MI:0019.
- **TRRUST:** the sentence about a curator confirming every row describes
  the mouse curation; for the human rows added in v2 the paper says only that
  they were manually curated. The column layout rests on one parser, not
  two. Section 4 of CC BY-SA 4.0 has two conditions, both open, and "never
  mix with incompatible terms" is a reading, not a quoted term. Under open
  question C12, TRRUST's evidence basis defaults to `unknown`. An approved
  symbol held by another gene as a previous symbol or alias is ambiguous.
  "Unknown" mode and "small-scale experimental studies" come from v1 and the
  website, not the v2 paper.
- **HGNC:** HGNC lists its UniProt column as mapped data supplied by the EBI;
  that the complete set's column is the same data is probable, not stated.
  This review also led to the `symbol` identifier role, so a match says
  whether it hit an approved, previous or alias symbol.
- **Costanzo:** BioGRID describes the raw file differently from the authors
  ("full data set" of interactions versus "all tested pairs"), to be checked
  in a dry run. NCBI now ranks 559292 as a strain.
- **Horlbeck:** the sign convention is an inference, consistent with BioGRID,
  because the legend could not be re-read. TMEM261 is an HGNC previous
  symbol, changed in 2017. The thresholds were calibrated on K562 only.
- **Helwak:** CC BY 4.0 applies to the version of record from 2013-07-16
  (Crossref). miRBase release 15 is not on the miRBase website, while
  Ensembl release 60 is still on the Ensembl FTP.

Claims that rest only on pages that blocked the checker on 2026-10-29 (some
PMC, GEO and publisher pages) stay marked *(unverified)*.

## Appendix A. Draft inquiry to the BioPlex team

**Not sent.** It goes out only with your approval, from you, and you fill in
the signature. Contacts are on https://bioplex.hms.harvard.edu/contact.php.

> **Subject:** Reuse terms for the BioPlex 3.0 and HCT116 interaction files
>
> Dear BioPlex team,
>
> I am building an open-source resource, based on the openPIP 2.0 interaction
> portal, that integrates published interaction datasets with record-level
> attribution and a recorded licence for each dataset. I would like to
> include the BioPlex 3.0 networks (Huttlin et al., Cell 2021), credited to
> your paper, and I want to be sure of the terms first.
>
> I could not find a licence statement for the interaction files at
> https://bioplex.hms.harvard.edu/data/, such as
> BioPlex_293T_Network_10K_Dec_2019.tsv and
> BioPlex_HCT116_Network_5.5K_Dec_2019.tsv. The NDEx records the paper cites
> (doi:10.18119/N9RP5D, 10.18119/N9N012 and 10.18119/N9H887) list their
> rights as "Waiver-No rights reserved (CC0)", with Harvard Medical School as
> rights holder, and the BioPlex Explorer website is licensed under
> CC BY-SA 4.0.
>
> Could you confirm:
>
> 1. Is the CC0 waiver on the NDEx records authorised, and does it also
>    cover the interaction files on the BioPlex website?
> 2. If not, under what terms may the published 293T and HCT116 networks be
>    used internally, displayed publicly with attribution, and redistributed
>    in downloadable exports?
> 3. Is there an attribution wording you prefer, beyond citing Huttlin et al.
>    2021?
>
> I would not use the unpublished quarterly releases.
>
> Thank you for your time.
>
> [Name, affiliation, contact]
