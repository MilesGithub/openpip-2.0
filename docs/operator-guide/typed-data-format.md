# Typed data format

The typed relationship graph holds relationships between typed entities
(genes, proteins and protein isoforms), with the evidence each release of a
dataset states for them. It is still being built, and visitors see nothing
of it yet. This page describes:

- registering a release of typed data, with its files;
- `canonical_tsv_v1`, the format of its data file;
- checking it with a **dry run**, which reads every row, maps it to the
  graph's entities and reports what a commit would do.

[Typed releases](typed-releases.md) describes what follows: approving a
release, committing it, which imports its evidence into the graph, and
publishing, withdrawing and discarding it. Nothing on either page changes
what visitors see.

Everything here needs `TYPED_GRAPH_ENABLED` on
([Configuration reference](configuration.md#backend-variables)). While it is
off, every command on both pages refuses before it reads a file or writes
anything.

The examples use `dc` for the production compose command, as on the
[Maintenance](maintenance.md) page:

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod'
```

## Releases

- A **data source**, such as a lab, a consortium or a database, publishes
  **datasets**.
- A dataset has **releases**. Each is one version of the dataset's data,
  with its own files, licence snapshot and approvals, and each is registered
  as a **draft**.
- A release names the **adapter** that reads its data file. There is one,
  `canonical_tsv_v1`, the format on this page.
- A release may have a **manifest**: the papers its rows cite that have no
  PMID or DOI, and the vocabulary terms its rows use.
- A dry run stores each row of the data file that it can read as a **source
  record**, with a status and its issues. A later dry run of the same
  release updates them.
- A **commit** imports what the release's latest dry run reported: the
  entities, relationships, publications and terms its mapped records need,
  and their evidence. It **stages** the release, which is then
  **published** to make it current, or **discarded** back to a draft to
  import it again ([Typed releases](typed-releases.md)).
- A release of real data is dry-run and committed only once it has an
  `internal` approval. A **synthetic** release, made-up demonstration data,
  needs no approval for either and is checked instead.

The steps, for a release of real data:

1. Create the data source and the dataset in the Django admin.
2. Register the release with its files: `graph_register_release`.
3. Record its licence snapshot and its dataset's due diligence in the Django
   admin, and its `internal` approval with `graph_approve`.
4. Dry-run it, `graph_import --dry-run`, and read the report. To change the
   data, register the corrected file as a new version.
5. Commit it with the options of its dry run, `graph_import --commit`: the
   release is staged.
6. Publish it, `graph_publish_release`
   ([Typed releases](typed-releases.md#publishing-a-release)).

## Creating the data source and dataset

In the Django admin (`/django-admin/`; on this portal,
`{{ portal_url }}/django-admin/`), under **Typed relationship graph**:

- **Data sources**, then **Add**: the source's slug, name and kind.
- **Source datasets**, then **Add**: the dataset's slug, which you register
  releases under, its title and its data source.
- For real data, **Due diligence records**, then **Add**: the dataset's one
  record of the licence review.

The admin shows releases and approval decisions, read-only.

## Registering a release

```text
python manage.py graph_register_release --dataset SLUG --version V \
    --adapter canonical_tsv_v1 --file PATH [--manifest PATH] \
    [--source-url URL] [--release-date YYYY-MM-DD] [--supersedes VERSION] \
    [--taxon N ...] [--embargo-until ISO8601] [--synthetic] [--no-raw-rows] \
    --as USERNAME
```

| Option | Meaning |
|---|---|
| `--dataset SLUG` | The dataset, by its slug. Required. |
| `--version V` | The release's version, as the source labels it and URL-safe: a letter or digit, then up to 63 letters, digits or `.` `_` `+` `-`, such as `v1` or `2026-10`. One release per version of a dataset. Required. |
| `--adapter NAME` | The adapter that reads the data file: `canonical_tsv_v1`. Required. |
| `--file PATH` | The data file. Required. |
| `--manifest PATH` | The release manifest. |
| `--source-url URL` | Where the file came from: an `http` or `https` URL of at most 1,000 characters. |
| `--release-date YYYY-MM-DD` | The source's release date. |
| `--supersedes VERSION` | The release of the same dataset that this one corrects. Both are synthetic, or neither is. |
| `--taxon N ...` | The taxa the release declares: NCBI taxids that openPIP registers. Give several, or repeat the option. |
| `--embargo-until ISO8601` | An embargo, as a date and time with its UTC offset, such as `2027-01-01T00:00:00+00:00`. Until then only an `internal` approval is effective. |
| `--synthetic` | Synthetic demonstration data, checked by every dry run and commit. See [Synthetic releases](#synthetic-releases). |
| `--no-raw-rows` | Keep only each record's file, line number and checksum, not its column values. |
| `--as USERNAME` | The active staff account registering the release. Required. |

On a production server the backend container cannot see the host's files.
Put the release's files in a folder of the checkout, here `incoming/`, and
mount it read-only for one run:

```bash
mkdir -p incoming
cp ~/lab-release/release.tsv ~/lab-release/manifest.json incoming/
dc run --rm -v "$PWD/incoming:/incoming:ro" backend \
    python manage.py graph_register_release \
    --dataset example-lab --version v1 --adapter canonical_tsv_v1 \
    --file /incoming/release.tsv --manifest /incoming/manifest.json \
    --taxon 9606 --as alice
```

Never stage release files under `backend/mediafiles/` or
`backend/staticfiles/`, or in the `media` volume: the portal serves files
from those.
Registration copies each file into private storage
([Where release files live](#where-release-files-live)), so the staged copies
can be deleted afterwards.

Registration:

1. Refuses, before it reads any file, if the switch is off, if `--as` is not
   an active staff account, the dataset or adapter is unknown, the version
   is malformed, `--supersedes` names no release of the dataset, a `--taxon`
   is not registered, or the source URL, release date or embargo is
   malformed.
2. Copies the data file and the manifest into storage, checksumming them as
   it goes. It refuses a file that is not a regular file, one larger than
   its limit ([Limits](#limits)), and one that would leave less than 64 MiB
   free on the storage volume. It checks the manifest
   ([The release manifest](#the-release-manifest)) before it stores
   anything.
3. Creates the release, a draft, together with its files, in one
   transaction. The release records the adapter's name, version, mapping
   version and kind (`evidence` for `canonical_tsv_v1`), and every option;
   a release that supersedes another has its synthetic flag. If anything
   fails, nothing is written and no file is left behind.
4. Prints the release's id, and each stored file's size and SHA-256.

A release's files are added only while no one has decided on it, so register
it before you record any approval.

### Registering again

Running the same registration again is safe:

- **The same files and options**, compared by SHA-256: nothing is written,
  and it says the release is already registered. A stored copy that is
  missing, or no longer matches its checksum, is put back from the file you
  give, whatever the release's status.
- **A newer adapter**: when openPIP's `canonical_tsv_v1` has a new version or
  mapping version since the release was registered, a draft whose records
  hold no evidence, with no import under way, takes the new version, and
  its source records are deleted, to be read again by the next dry run. It
  prints the old and new versions. A staged release, or a draft that holds
  evidence or has an open commit run, is refused: discard it first with
  `graph_discard_release`
  ([Discarding a release](typed-releases.md#discarding-a-release)), then
  register it again, or register its files as a new version. A published,
  superseded or withdrawn release is refused: register its files as a new
  version.
- **Anything else that differs**, such as another data file, manifest,
  adapter or option: refused, naming the difference. Register changed data
  as a new version.

## Licence, due diligence and approval

A release that is not synthetic is dry-run and committed only once it has an
effective `internal` approval. An approval needs the release's licence
snapshot, and its dataset's due diligence, complete and confirmed after that
snapshot was recorded. In the Django admin, under **Typed relationship
graph**:

1. **Release licences**, then **Add**: choose the release and record its
   terms as you read them. A snapshot is never edited; record a new one when
   the terms change.
2. **Due diligence records**: open the dataset's record, fill in the
   reviewer and the notes link, leave *terms time limited* unticked, and
   save. Then select it in the list and run the action **Confirm due
   diligence covers the current terms**.

Then record the `internal` approval with `graph_approve`
([Approving a release](typed-releases.md#approving-a-release)):

```bash
dc exec backend python manage.py graph_approve --release 12 --scope internal \
    --decision approved \
    --rationale "Terms allow internal use; due diligence is complete." --as alice
```

`graph_approve` refuses an approval that the approval rules do not allow,
such as one before due diligence is confirmed, and says why; it then records
nothing. The admin lists every decision under **Usage approvals**. An
`internal` approval lets staff work with the release; it shows visitors
nothing.

## The canonical_tsv_v1 format

A `canonical_tsv_v1` file is UTF-8 text with one record per line, after a
header line that names the columns.

- A single tab separates the columns. There is no quoting, so a value never
  holds a tab or a line break.
- Lines end with LF or CRLF, and the last line may have neither. A carriage
  return anywhere else, or any other control character but the tab, makes
  the line unreadable.
- A leading UTF-8 byte order mark is ignored. A file in UTF-16 or UTF-32 is
  refused.
- Blank lines are skipped, and counted in the report.
- The header names each column once, in any order. Optional columns may be
  left out; a column the format does not know is refused.
- Every value is kept exactly as written, and a blank value means "not
  stated". Nothing is inferred, merged or corrected.
- Reports give physical line numbers: the header is line 1.

### Columns

| Column | Required | What it holds |
|---|---|---|
| `record_id` | yes | The source's own id for the record, which keys it: a letter or digit, then up to 254 letters, digits or `.` `_` `:` `-`. |
| `subject_namespace` | yes | The subject's identifier namespace, from openPIP's registry ([Identifiers](#identifiers)). |
| `subject_id` | yes | The subject's identifier, as the source writes it. |
| `subject_type` | yes | The subject's entity type: `gene`, `protein` or `protein_isoform`. |
| `subject_taxon` | yes | The subject's NCBI taxid: digits, from 1 to 2147483647, without a sign or leading zeros. Every entity type needs one. |
| `subject_label` | no | A label for the subject, at most 255 characters, used only for an entity the release would create. An existing entity keeps its label, and a different one is noted; one that another release creates between the dry run and the commit keeps that release's label, without a note. |
| `object_namespace` | yes | The object's, as for the subject. |
| `object_id` | yes | The object's, as for the subject. |
| `object_type` | yes | The object's, as for the subject. |
| `object_taxon` | yes | The object's, as for the subject. |
| `object_label` | no | The object's, as for the subject. |
| `relationship_type` | yes | A relationship type of the registry ([Relationship types](#relationship-types)). Subject and object are in the order the type gives them; a direction the type contradicts is refused, never swapped. |
| `directed` | no | `true`, `false` or blank. Only checked against the relationship type: a directed type is never `false`, and a symmetric one never `true`. |
| `outcome` | yes | `supported`, `tested_absent` (tested and not detected), `inconclusive` or `not_significant` (measured, below the source's threshold). |
| `evidence_basis` | yes | `experimental`, `computational_prediction`, `computational_inference`, `statistical`, `text_mining` or `unknown`. |
| `assertion_method` | yes | `manual_assertion`, `automatic_assertion` or `unknown`. |
| `effect` | no | `activating`, `inhibiting` or `unknown`; blank when the source states none. Only on a supported row of a type with effect polarity, such as `transcriptional_regulation`. `negative` is refused, because it is ambiguous. |
| `genetic_sign` | no | `negative` or `positive`. Only on a supported row of a type with genetic-sign polarity, of which the registry has none yet. |
| `interaction_type` | no | One PSI-MI interaction type from the format's list ([Interaction types](#interaction-types)), which sets the physical-interaction level. |
| `method` | no | Detection methods: PSI-MI CURIEs joined by `\|`, at most 50. |
| `eco` | no | One ECO CURIE, such as `ECO:0000353`. |
| `publications` | no | The papers that report the row, joined by `\|`, at most 50 ([Publications](#publications)). |
| `evidence_group` | no | The source's own label for a group of rows, kept as text. |
| `threshold` | no | On a `not_significant` row, the significance criteria the result failed ([Threshold](#threshold)). |
| `score:<score type>` | no | A score of that registry score type ([Scores](#scores)). |
| `context:<facet>` | no | A context the row states ([Contexts and qualifiers](#contexts-and-qualifiers)). |
| `qualifier:<key>` | no | Any further field of the source, kept as text ([Contexts and qualifiers](#contexts-and-qualifiers)). |

Each row states one claim about the relationship between its subject and its
object. A row that cites several papers gives one assertion per paper.
Besides the format's own checks, each row is checked by the evidence rules
that a commit applies, and a refusal is reported in their words
([Issue codes](#issue-codes)).

### Identifiers

An identifier is valid for the entity type and taxon the row states only if
its namespace allows them and the value has the namespace's form.

| Namespace | Identifies | Taxon | Form |
|---|---|---|---|
| `hgnc` | `gene` | 9606 only | `HGNC:` and digits, such as `HGNC:11998`. The prefix is required: a bare number could be an NCBI Gene ID. |
| `uniprotkb` | `protein`, `protein_isoform` | any registered taxon | A UniProtKB accession, matched ignoring case. An entry accession (`P04637`) is a `protein`; an accession with an isoform suffix, `-1` included (`P04637-2`), is a `protein_isoform`. |
| `ncbigene` | `gene` | any registered taxon | Digits, without leading zeros. It defines no gene, so its values map only through a resolution file. |
| `ensembl_gene` | `gene` | any registered taxon | `ENS`, any letters, `G` and 11 digits, matched ignoring case, with an optional version after a dot (`ENSG00000141510.21`), which is kept as the identifier's version. It defines no gene, so its values map only through a resolution file. |
| `synthetic_gene` | `gene` | 32630 only | `SYN-`, then a capital letter and capitals or digits. Synthetic releases only. |
| `synthetic_protein` | `protein`, `protein_isoform` | 32630 only | As `synthetic_gene`, with an optional isoform suffix such as `-1`. Synthetic releases only. |

The registered taxa are 9606 (human), 7227 (fly) and 32630 (synthetic
construct). The Django admin lists the registry under **Identifier
namespaces**, **Entity types** and **Taxons**.

### Relationship types

| Type | Subject | Object | Notes |
|---|---|---|---|
| `transcriptional_regulation` | `gene` or `protein`, the regulator | `gene`, the target | Directed, so A→B and B→A are different relationships. Carries an effect. |
| `physical_interaction` | `protein` or `protein_isoform` | `protein` or `protein_isoform` | Symmetric: A–B is B–A. Its level comes from `interaction_type`. |
| `protein_interaction_unspecified` | `protein` or `protein_isoform` | `protein` or `protein_isoform` | Symmetric, for rows whose source states no interaction type: such a row gives none. |
| `encodes` | `gene` | `protein` | A reference link, accepted only in a synthetic release. |
| `isoform_of` | `protein_isoform` | `protein` | A reference link, accepted only in a synthetic release. |

### Interaction types

`interaction_type` accepts these PSI-MI terms, each at its level:

| CURIE | PSI-MI name | Level |
|---|---|---|
| `MI:0914` | association | `association` |
| `MI:0915` | physical association | `physical_association` |
| `MI:0407` | direct interaction | `direct_interaction` |

Any other interaction type is refused. There is no level column: a physical
row without an interaction type has the level `not_stated`. A physical
interaction type of openPIP's PSI-MI crosswalk, such as `MI:0915`, is
refused in `method`, since it is not a detection method.

### Publications

Each item of `publications` is one paper:

- `pmid:<n>`: a PubMed ID, a number from 1;
- `doi:<doi>`: a DOI, `10.<registrant>/<suffix>`, matched ignoring the case
  of its letters;
- `ref:<key>`: a paper the release manifest declares by its local key, for a
  paper with no PMID or DOI.

Name each paper once, by one identifier. Two items that name one paper are
refused (`duplicate_paper`): the same identifier twice, or a `pmid:` and a
`doi:` of one paper that openPIP already holds. A row that cites both a
`pmid:` and a `doi:` paper that openPIP does not hold as one gets a note, in
case they are one paper.

### Threshold

A `not_significant` row states the significance criteria its result failed,
and needs them: criteria joined by `;`, each `<score type> <operator>
<number>`, such as `synthetic:confidence >= 0.5`. The operator is one of `<`, `<=`,
`>`, `>=`, `abs<`, `abs<=`, `abs>` and `abs>=`; spaces are optional, and the
number is kept as written. A result is significant when its scores meet
every criterion, so a not-significant row's scores fail at least one. Each
criterion names a score the row gives in a `score:` column. A threshold on a
row of any other outcome is refused.

### Scores

A `score:<score type>` column names a score type of the registry, and each
non-blank value is a score of that type:

- a number (a sign, ASCII digits with an optional fraction, and an optional
  exponent) is kept exactly as written;
- a censored value, an operator and a number such as `<2.2e-16`, is kept as
  written with no numeric value, and the row gets a note;
- NaN or infinity, in any spelling, is refused, and so is any other text.

A value must suit its type: a probability or a p-value lies in [0, 1], and a
rank is an integer from 1. The registry's score types today,
`synthetic:confidence` and `synthetic:rank`, are for synthetic releases only,
so a release of real data states no scores yet.

### Contexts and qualifiers

- `context:<facet>` holds a context as raw text, of at most 255 characters.
  The facets are `host_taxon`, `tissue`, `cell_type`, `cell_line`,
  `condition`, `perturbation`, `compartment`, `assay_system`,
  `developmental_stage`, `timepoint`, `genome_assembly` and `other`. A value
  that is a PSI-MI or ECO CURIE, such as `MI:0492`, names that term.
  `context:host_taxon` takes an NCBI taxid; one that openPIP does not
  register is kept as raw text only.
- `qualifier:<key>` keeps any further field of the source as text, under its
  key: a lower-case letter, then up to 63 lower-case letters, digits or `_`,
  never `threshold`.

### Terms

Every CURIE a row uses, in `interaction_type`, `method`, `eco` or a
`context:` column, must be declared in the manifest's `terms`, with the
label the source gives it, whether or not openPIP already holds the term. A
row that uses an undeclared CURIE is rejected. A dry run stores no term. A
commit stores each new term with the label the manifest declares, or, for
a term that openPIP's PSI-MI crosswalk pins, such as an interaction type,
the crosswalk's; a term openPIP already holds keeps its label. The dry
run's report lists any difference, and the commit's report lists every
one, a term that another release stored after the dry run included
([The summary and the report](typed-releases.md#the-summary-and-the-report)).

### An example

A file of three records, shown here one column per table row. In the file,
the header is line 1, and each record is one line of values separated by
tabs.

| Column | Line 2 | Line 3 | Line 4 |
|---|---|---|---|
| `record_id` | `lab-0001` | `lab-0002` | `lab-0003` |
| `subject_namespace` | `hgnc` | `uniprotkb` | `ncbigene` |
| `subject_id` | `HGNC:11998` | `P04637` | `7157` |
| `subject_type` | `gene` | `protein` | `gene` |
| `subject_taxon` | `9606` | `9606` | `9606` |
| `subject_label` | `TP53` | | |
| `object_namespace` | `hgnc` | `uniprotkb` | `hgnc` |
| `object_id` | `HGNC:6973` | `Q00987` | `HGNC:6973` |
| `object_type` | `gene` | `protein` | `gene` |
| `object_taxon` | `9606` | `9606` | `9606` |
| `object_label` | `MDM2` | | `MDM2` |
| `relationship_type` | `transcriptional_regulation` | `physical_interaction` | `transcriptional_regulation` |
| `directed` | `true` | | |
| `outcome` | `supported` | `supported` | `inconclusive` |
| `evidence_basis` | `experimental` | `experimental` | `experimental` |
| `assertion_method` | `manual_assertion` | `manual_assertion` | `manual_assertion` |
| `effect` | `activating` | | |
| `interaction_type` | | `MI:0915` | |
| `method` | `MI:0402` | `MI:0018` | `MI:0402` |
| `publications` | `ref:lab-2026` | `ref:lab-2026` | `ref:lab-2026` |
| `evidence_group` | `ChIP series 1` | | |

- Line 2: TP53 activates MDM2's transcription, by a chromatin
  immunoprecipitation assay.
- Line 3: the TP53 and MDM2 proteins associate physically in a two-hybrid
  test.
- Line 4: the source names TP53 by its NCBI Gene ID. It defines no gene, so
  the record stays `unresolved` until a [resolution file](#resolution-files)
  maps it.

The manifest of [the next section](#the-release-manifest) declares the paper
`lab-2026` and the three terms.

### Limits

| Limit | Value |
|---|---|
| A line, the header included, without its line ending | 1,048,576 bytes (1 MiB) |
| Columns | 200 |
| Characters in one value | 10,000 |
| Papers or methods in one cell | 50 |
| Lines after the header, blank ones included | 5,000,000 |
| A data file | 2 GiB (2,147,483,648 bytes) |
| A manifest or a resolution file | 8 MiB (8,388,608 bytes) |
| Entries in a resolution file | 10,000 |
| Distinct identifiers a file states | 250,000 |
| Free space the storage volume keeps after a file is stored | 64 MiB |

A longer line is read to its end in small pieces, never held in memory, and
reported by its line number.

A dry run reads its file in batches, but remembers each distinct identifier
the file states, with how it maps, for the whole run: about 2.2 KB of memory
per identifier, and up to 2.7 KB at its peak, so up to about 700 MB for a
file at the limit of 250,000. A refused value is remembered by its first 200
characters only. Its report also counts each distinct relationship that its
mapped rows state, at about 600 bytes each, which only the line limit
bounds: 5,000,000 lines that each state another pair take about 3 GB. A
commit keeps the same identifier caches, but not that count. A file near
the line limit therefore needs more memory than the 2 GB that
[Installation](installation.md#requirements) asks for all the containers.

## The release manifest

The manifest declares the papers the rows cite as `ref:<key>`, and the terms
the rows use. It is a JSON file:

```json
{
  "publications": {
    "lab-2026": {
      "title": "Example Lab: TP53 and MDM2 assays",
      "year": 2026,
      "is_synthetic": false
    }
  },
  "terms": {
    "MI:0018": "two hybrid",
    "MI:0402": "chromatin immunoprecipitation assay",
    "MI:0915": "physical association"
  }
}
```

- Both sections are optional. A release without a manifest declares nothing,
  so its rows cite no `ref:` paper and use no CURIE.
- A local key is a letter or digit, then up to 99 letters, digits or `.` `_`
  `:` `-`. It names the same paper in every release of the dataset.
- A paper has a `title`, not blank and at most 10,000 characters, an
  optional `year` from 1 to 32767 (or `null`), and `is_synthetic`, which
  equals the release's flag: a synthetic release's manifest declares only
  synthetic papers, and any other release's none.
- A term is a PSI-MI (`MI:`) or ECO (`ECO:`) CURIE, declared once, with the
  label the source gives it: not blank, at most 255 characters.

The file is read strictly. It is refused if it is not UTF-8 or starts with a
byte order mark, gives a key twice in one object, holds a number with a
fraction or an exponent, `NaN` or `Infinity`, an integer of more than 19
digits, a string with a control character, or any key not shown above.
Registration refuses an invalid manifest before it stores anything.

## Resolution files

Some identifiers cannot be mapped by their namespace alone: a value that
matches an entity only as a cross-reference or an alias, one that several
entities share, or one in a namespace that defines no entity. A person who
has reviewed such a value can map it with a resolution file, given to the
dry run with `--resolutions`, then to the commit, and to any rerun that
resumes the commit:

```json
{
  "resolutions": [
    {
      "namespace": "ncbigene",
      "value": "7157",
      "entity_type": "gene",
      "taxon": 9606,
      "entity": 42,
      "target_namespace": "hgnc",
      "target_value": "HGNC:11998",
      "rationale": "NCBI Gene 7157 is TP53, whose HGNC ID is HGNC:11998.",
      "reviewer": "A. Curator"
    }
  ]
}
```

Each entry maps the identifier a row states (`namespace`, `value`,
`entity_type`, `taxon`) to the entity with id `entity`, which
`target_namespace` and `target_value` name. The file is read as strictly as
the manifest, and is checked before the run starts; an invalid one is
refused, naming the entries at fault, and nothing is written:

- every entry has exactly these keys; `value` and `target_value` are at most
  255 characters, `reviewer` at most 150, and `rationale` is not blank and at
  most 10,000; `taxon` is a taxid or `null`;
- the stated identifier is valid for its type and taxon, so an isoform is
  never mapped to its entry this way;
- the target identifier names exactly the entity `entity`, which is active
  or provisional, and of the entry's type and taxon. A mistyped id is
  caught;
- no two entries map one identifier.

An entry applies only where the namespace alone settles nothing ([Mapping
identifiers](#mapping-identifiers)). The run keeps the file's SHA-256 and
its entries, and every record mapped through it gets a note (basis
`curated`) and is listed in the report with the entry's rationale and
reviewer. A commit is given the same file as its dry run, checked by its
SHA-256, and so is any rerun that resumes the commit: keep the file until
the release is committed.

## Dry runs

```text
python manage.py graph_import --release ID --dry-run [--create-entities] \
    [--resolutions FILE] [--report-file PATH] --as USERNAME
```

| Option | Meaning |
|---|---|
| `--release ID` | The release, by the id registration printed. Required. |
| `--dry-run` | The mode: map and report, writing only the run and the source records. `--commit` imports the release instead ([Committing a release](typed-releases.md#committing-a-release)). |
| `--create-entities` | Count the records whose identifiers need new entities as mapped, as a commit with this option would create them. Without it they are `unresolved`. The report lists those entities either way. The commit takes the option exactly when its dry run had it. |
| `--resolutions FILE` | A reviewed [resolution file](#resolution-files). |
| `--report-file PATH` | Also write every entry of the report's lists to `PATH`, as JSON Lines ([The report](#the-report)). |
| `--as USERNAME` | The active staff account running the dry run. Required. |

On a production server:

```bash
dc exec backend python manage.py graph_import --release 12 --dry-run --as alice
```

A report file is written inside the container, so mount a folder for it:

```bash
mkdir -p reports
dc run --rm -v "$PWD/reports:/reports" backend \
    python manage.py graph_import --release 12 --dry-run --create-entities \
    --report-file /reports/release-12.jsonl --as alice
```

The container runs as root, so on a Linux host the report file belongs to
root, and only root can read it (mode 0600, since it quotes the release's
values). To make it yours:

```bash
sudo chown "$(id -u):$(id -g)" reports/release-12.jsonl
```

Running the container as yourself instead does not work: the dry run reads
the stored release file, which only root may read.

### Before it starts

The dry run checks, in this order, and refuses with nothing written when:

1. `TYPED_GRAPH_ENABLED` is off;
2. `--as` is not an active staff account;
3. the database's registry is at another version than this code's: run
   `python manage.py graph_sync_registry` after an upgrade;
4. the release does not exist, is not a draft, does not have exactly one
   data file and at most one manifest, or has an open commit run: resume
   that commit with `graph_import --commit`, or discard it with
   `graph_discard_release`
   ([A commit that stopped](typed-releases.md#a-commit-that-stopped));
5. the release is not synthetic and has no effective `internal` approval:
   record one with `graph_approve`;
6. the release was registered with another version, mapping version or kind
   of its adapter than this code carries:
   [register it again](#registering-again);
7. the resolution file is unreadable or invalid;
8. the report file's folder does not exist, the report file lies under the
   media, the static or the release-file root, or something that is not a
   regular file is at its path already: a link, a FIFO or a device such as
   `/dev/null` or `/dev/stdout`;
9. a dry run, commit or discard of the release is running: one runs at a
   time ([One commit at a time](typed-releases.md#one-commit-at-a-time)).

Once it holds the release's run lock (9), it checks 4 again, since a commit
may have started or finished meanwhile. A dry run marks no other run: one
that a process which ended left running stays so until the release's next
commit or discard marks it failed, on PostgreSQL. Without the run lock
nothing tells that its process ended, so nothing marks it:
[One commit at a time](typed-releases.md#one-commit-at-a-time) says how to
mark it failed.

### What it does

1. It records the run, with its options and the resolution file's entries.
2. It opens the stored data file and manifest, checking each against its
   size and SHA-256, and reads the manifest. A missing or changed file, or an
   invalid manifest, ends the run with the verdict `refused`, and no record
   is written.
3. It reads the file in batches of at most 500 rows and 32 MiB, maps each
   row and writes the batch's source records in one short transaction.
   Before each batch is written it checks again that the switch is on and
   that the release is still a draft, with no import started, still read
   by the adapter version the run started with (registering it again
   under a new version deletes its records), still approved, and on the
   code's registry version; if not, the run stops as failed, keeping what
   earlier batches wrote.
4. If a later batch changes how an earlier batch's rows map (a later line
   repeats a `record_id` with other content, or states an identifier at
   another taxon than earlier rows), it reads the file once more, so the
   records and the report follow from the whole file, whatever batches the
   lines fall in.
5. It stores the report with the run, once it has checked that the release
   is still read by the run's adapter version, and prints a summary.

A dry run writes only its run and the release's source records: never an
entity, relationship, publication or term. Run it as often as you like. A
later run updates each record's status and issues, leaves a record whose
result is unchanged as it was, and keeps its own report. A record that
evidence was already recorded from stays `mapped`; the report lists it under
`would_change` if its result is now different.

### Mapping identifiers

Each identifier a row states is checked in this order. Each refusal rejects
the row with its own issue code; an identifier is never left unresolved
silently.

1. Its namespace, entity type and taxon are in the registry, and the value
   is an identifier of that namespace for that type and taxon
   ([Identifiers](#identifiers)).
2. A UniProtKB accession that is the primary identifier of an entity of
   another taxon or type than the row states is refused
   (`taxon_contradiction`). A secondary accession, cross-reference or alias
   held at another taxon or type does not refuse the row.
3. It is looked up in its namespace, for its type and taxon:
    - **resolved**: one entity holds it as its own identifier. It maps to
      that entity. A resolution entry that maps it to another entity
      rejects the row; one that maps it to the same entity is reported as
      redundant.
    - **candidate**: one entity matches it, but only in another way, such
      as a cross-reference, an alias or another version. **ambiguous**:
      several entities match. Either maps only through a resolution entry
      that names one of the matching entities. Otherwise the record is
      `unresolved` (candidate) or `ambiguous`, with every match listed.
      Cross-references and aliases are not accepted on their own.
    - **unmatched**: no entity matches. It maps through a resolution entry.
      Otherwise, if its namespace defines entities of its type and taxon
      (HGNC IDs for human genes, UniProtKB accessions for proteins and
      isoforms, and the synthetic namespaces), it is an entity a commit
      with `--create-entities` would create; if not, the record is
      `unresolved`, with the reason.

Each identifier is looked up once per run, and each entity a commit would
create is planned once, whichever rows and sides name it. If the file
states it at two types or taxa, every row that states it is rejected
(`taxon_contradiction`) and it is not created: one value names one entity,
and the file does not say which. It would take the first non-blank label a mapped
row gives it, otherwise its identifier as written; a later, different label
is reported and never used. An existing entity's label never changes, and a
row that states another one gets a note.

### Statuses

| Status | When |
|---|---|
| `mapped` | Every endpoint maps to an entity, or to one a commit would create when the run has `--create-entities`, and nothing rejects the row. |
| `unresolved` | An identifier matches no entity and none would be created (or the run lacks `--create-entities`), or it is a candidate that no resolution entry accepts. |
| `ambiguous` | An identifier matches several entities, and no resolution entry chooses one. |
| `rejected` | The row breaks a rule: of the format, of the mapping, of the evidence rules, or of a synthetic release. |
| `skipped` | Not used by `canonical_tsv_v1`. |

A record with several problems takes the first status in this order:
`rejected`, `ambiguous`, `unresolved`, `mapped`.

Rows without a trustworthy record id or values are **unstored**: they get no
source record, and the report lists them by line number. These are lines that
cannot be read (too long, not UTF-8), lines with a control character, lines
that have the wrong number of fields, a value over 10,000 characters or a
malformed `record_id`, and later lines of a `record_id` already read. A
synthetic release still checks those whose values split into the header's
columns (see [Synthetic releases](#synthetic-releases)). A later line
identical to the first is a plain duplicate. One that differs rejects the
first line's record too, naming both lines: the file states two versions of
one record, and neither is imported.

### The verdict

The report's verdict is `ok`, or `refused` when:

- a stored file is missing or changed, or the manifest is invalid;
- the file is refused as a whole: its encoding, a missing or invalid header,
  more than 5,000,000 lines, or more than 250,000 distinct identifiers;
- the release is synthetic and any row breaks the synthetic rules.

A file refused at its header gets no record. One refused later keeps the
records read before the problem, except those of the batch that passed the
identifier limit. A release whose latest dry run was refused is not ready
to import: `graph_import --commit` refuses it.

### The report

The command prints a summary:

```text
Dry run 7 of release 12: ok.
Lines 4, blank 0; records: 2 mapped, 1 unresolved, 0 ambiguous, 0 rejected, 0 skipped; unstored rows 0.
Records created 3, updated 0.
File errors 0; rejected 0; identifiers 5; curated 0.
Entities a commit with --create-entities would create: 0.
Relationships: 0 existing, 2 new; assertions: 0 existing, 2 new; conflicts 0.
```

This is the example file's first dry run, on a portal holding its four
genes and proteins and no evidence. The full report is stored with the run
(`ImportRun.report`), which the Django admin does not show yet: ask for a
report file, or read it from a Django shell.

| Section | What it holds |
|---|---|
| `verdict` | `ok` or `refused`. |
| `truncated` | Whether the report passed its 16 MiB budget, so that its lists were shortened. |
| `adapter` | The adapter's name, version, mapping version and kind, whether it is isoform-specific, and its note of the changes it makes to the source's data. |
| `registry` | The registry version of the code and of the database. |
| `release` | The release's id, dataset, version, status, synthetic flag, whether it keeps raw rows, and the release it supersedes. |
| `licence` | The latest licence snapshot: its id, licence name, redistribution, modification, share-alike, and whether it records terms. |
| `approvals` | The release's effective approval scopes. |
| `create_entities` | Whether the run had `--create-entities`. |
| `counts` | Lines (the header included), blank lines, records by status, unstored rows, records the run created and updated, and mapped records by relationship type, outcome, effect, genetic sign and level. |
| `file_errors` | Why the file was refused. |
| `rejected` | Each rejected record: line, record id and issues. |
| `unstored` | Each unstored row: line, record id if it has one, and issues. |
| `identifiers` | Each identifier the rows state: the values as written, how it was found (resolved, candidate, ambiguous or unmatched), how it maps (primary, curated, planned, unmapped or refused, with the reason), its candidates with their primary identifiers, and its first lines. Those that need a person come first. |
| `curated` | Every record mapped through the resolution file, with the entry, rationale and reviewer; and the entries that were unused, redundant, contradicted, or that could not apply. |
| `would_create` | The entities a commit with `--create-entities` would create, from rows that would then be mapped: identifier, identity key, the label chosen and its line, and the rows naming it. Listed with or without the option. |
| `label_conflicts` | Other labels that rows give an entity a commit would create. |
| `label_differs` | Rows whose label differs from the existing entity's. |
| `relationships` | Over mapped records: how many relationships exist already and how many are new, and for each its type and endpoints (entity ids, or the identity keys of entities a commit would create), records and first lines. |
| `assertions` | How many mapped records already hold exactly the evidence they state, and how many do not. |
| `publications` | Each cited paper, found (with its id) or new, and the rows citing it; and manifest papers whose stored title or year differs. |
| `terms` | Each CURIE the rows use: stored or new, the manifest's label, and any conflict with a stored label or one that openPIP pins. |
| `reference_links` | The `encodes` and `isoform_of` rows of a synthetic release. |
| `conflicts` | For information: mapped records that disagree with other releases' evidence on the same relationship: opposite effects or genetic signs, or supported against tested and absent. Per relationship and other release, with each side's outcomes, effects and signs, and for a physical type its levels and detection methods; marked when the other is the release this one corrects. Inconclusive and not-significant evidence is listed apart, under `other_outcomes`. Only releases that are published, superseded, or staged with an effective `internal` approval are compared, synthetic only with synthetic. |
| `synthetic` | Whether the synthetic rules were checked and passed, and the rows that break them. |
| `would_change` | Records that evidence was recorded from, which stay `mapped`, with the status they would now get. |
| `notes` | The evidence rules' notes on each row. |

Each list gives its `total`, whether it is `truncated`, and at most 1,000
`entries`. An entry carries at most 5 issues, 20 candidates and its first 5
line numbers, and labels of at most 200 characters. Every record mapped
through the resolution file, and every candidate list, is also noted on the
record itself, and the report file has every entry.

`--report-file` writes the complete lists. Its first line is the summary,
`{"section": "summary", ...}`, with each list's total; every other line is
one entry, `{"section": "<list>", ...}`. It is written in one step when the
run completes, and a failed run leaves none.

## Issue codes

Each issue has a code, a message that quotes the value at fault, the
column, the line number and a severity. An error rejects the record; a note
does not. A record keeps its first 20 issues, then a count of the rest
(`more`). A `conflicting_duplicate` is always among those kept, in place of
the last of the others.

### Unreadable rows

These rows are unstored.

| Code | Meaning |
|---|---|
| `line_too_long` | The line is longer than 1,048,576 bytes without its line ending. In the header, it refuses the file. |
| `bad_utf8` | The line is not valid UTF-8. |
| `control_character` | The line holds a control character: a carriage return that does not end it, another C0 control but the tab, DEL, or a C1 control. In the header, it refuses the file. |
| `field_count` | The line has another number of tab-separated values than the header has columns. |
| `field_too_long` | A value is longer than 10,000 characters. |
| `bad_record_id` | The `record_id` is not a letter or digit followed by up to 254 letters, digits or `.` `_` `:` `-`. |
| `duplicate_record` | An earlier line has the same `record_id` and the same content. |
| `conflicting_duplicate` | An earlier line has the same `record_id` and other content: neither is imported, and the earlier record is rejected. |

### Rejected rows

| Code | Meaning |
|---|---|
| `too_many_values` | A cell lists more than 50 papers or methods. |
| `unknown_namespace` | The namespace is not in the registry. |
| `namespace_not_for_type` | The namespace does not identify entities of the stated type. |
| `bad_identifier` | The value is not an identifier of its namespace. |
| `hgnc_without_prefix` | An `hgnc` value without `HGNC:`. |
| `bad_taxon` | The taxon is not an NCBI taxid, or it is missing where the entity type needs one. |
| `unknown_taxon` | The taxid is not in the registry. |
| `taxon_contradiction` | The taxon contradicts the namespace's fixed taxon, or the taxon or type of the entity whose primary identifier the value is; or the file states the value at another type or taxon too, so the entity it would create is not created. |
| `isoform_form` | The value's form names the other type: an isoform accession stated as a `protein`, or an entry accession stated as a `protein_isoform`. |
| `unknown_entity_type` | The entity type is not in the registry. |
| `unknown_relationship_type` | The relationship type is not in the registry. |
| `reference_type_not_allowed` | `encodes` or `isoform_of` in a release that is not synthetic. |
| `bad_value` | A column with a fixed set of values holds another value. |
| `negative_effect` | The effect is `negative`, which is ambiguous: state `inhibiting`, a `genetic_sign`, or the outcome `tested_absent`. |
| `interaction_type_not_listed` | The interaction type is not in the format's list. |
| `method_is_interaction_type` | `method` names a physical interaction type of openPIP's PSI-MI crosswalk, which belongs in `interaction_type`. |
| `term_undeclared` | A CURIE the row uses is not declared in the manifest's `terms`. |
| `bad_curie` | A value is not a CURIE of an ontology openPIP accepts: PSI-MI (`MI:` and 4 digits) or ECO (`ECO:` and 7 digits). |
| `score_not_finite` | A score is NaN or infinity. |
| `score_not_a_number` | A score is text that is neither a number nor a censored value. |
| `bad_threshold` | The threshold does not follow its form, or names a score type the row gives no score of. |
| `unknown_ref` | A `ref:` key is not declared in the manifest's `publications`. |
| `bad_paper` | A cited paper is not `pmid:<n>`, `doi:<doi>` or `ref:<key>`. |
| `duplicate_paper` | Two items of `publications` name one paper. |
| `label_too_long` | A label is longer than 255 characters. |
| `synthetic_violation` | A synthetic release names something that is not synthetic ([Synthetic releases](#synthetic-releases)). |
| `resolution_contradicts` | The identifier names one entity, and the resolution file maps it to another. |
| `claim_conflict` | The record holds evidence that its row no longer gives exactly: a record's evidence never changes. |
| `claim_rule_1` | Relationship type and endpoints: the type exists, and each endpoint is an entity that can carry evidence. |
| `claim_rule_2` | Endpoint types: each endpoint is of a type the relationship type allows on its side. A gene stated where the type needs a protein is refused here. |
| `claim_rule_3` | Stated direction: `directed` agrees with the type. |
| `claim_rule_4` | Self-pairs: the type relates two different entities, and the row names one twice. |
| `claim_rule_5` | Canonical endpoints: the relationship's endpoints in the type's order. |
| `claim_rule_6` | Closed values: `outcome`, `evidence_basis` and `assertion_method`. |
| `claim_rule_7` | Polarity: an effect only on a type with effect polarity, and a genetic sign only on a type with genetic-sign polarity, with a signed score that agrees with it. |
| `claim_rule_8` | Outcome: only a supported row carries an effect or a sign, and only a not-significant row states a threshold, which it needs and fails. |
| `claim_rule_9` | Interaction level: only on physical types, and above `association` only from a stated interaction type. |
| `claim_rule_10` | Terms: each CURIE is a term of the ontology its column takes. |
| `claim_rule_11` | Publications and assertion keys: the cited papers, one assertion each. |
| `claim_rule_12` | Participants: the endpoints as the row states them. |
| `claim_rule_13` | Synthetic separation: synthetic constructs (taxon 32630) only in synthetic releases, and only they there. |
| `claim_rule_14` | Scores: one per score type, of a type the release may use, with a value that suits it. |
| `claim_rule_15` | Contexts: known facets, values of at most 255 characters, and a host taxon that is a taxid. |
| `claim_rule_16` | Text and qualifiers: plain text and valid JSON, within their limits. |

### Why an identifier is not created

These codes explain, in the report's `identifiers` list, why an unmatched
identifier gives no entity; the record is `unresolved`, with the note
`unmatched`.

| Code | Meaning |
|---|---|
| `not_identity_namespace` | No entity of this type and taxon is created from this namespace: another namespace defines them, or none does. |
| `identity_conflict` | The value already names, or is held by, another entity. |
| `entity_exists` | An entity with this identity exists already. |
| `release_cannot_introduce` | The release may not introduce entities: it is past staged, it has no effective `internal` approval, or it is synthetic and the entity is not a synthetic construct. |
| `label_blank` | The entity would have no label. A dry run uses the identifier when no row gives a label, so this does not occur. |

### Notes

| Code | Meaning |
|---|---|
| `censored_score` | A score is a bound, such as `<2.2e-16`: kept as written, with no numeric value. |
| `curated_mapping` | The identifier was mapped by the resolution file. |
| `candidate` | The identifier matched entities that nothing accepted; they are listed. |
| `unmatched` | The identifier matches no entity: a commit with `--create-entities` would create one, or none is created, for the reason given. |
| `label_differs` | The stated label differs from the existing entity's, which never changes. |
| `pmid_and_doi` | The row cites a `pmid:` paper and a `doi:` paper: check that they are not one paper. |

### File errors

These refuse the file: the verdict is `refused`.

| Code | Meaning |
|---|---|
| `encoding` | The file is not UTF-8: it starts with a UTF-16 or UTF-32 byte order mark, or its header does not decode. |
| `no_header` | The file is empty, or its first line is blank. |
| `header_unknown_column` | A column is not one of the format's, or a `qualifier:` key is malformed or `threshold`. |
| `header_missing_column` | A required column is missing. |
| `header_duplicate_column` | A column is named twice. |
| `too_many_columns` | The header has more than 200 columns. |
| `unknown_score_type` | A `score:` column names a score type that is not in the registry. |
| `unknown_facet` | A `context:` column names a facet that does not exist. |
| `too_many_lines` | The file has more than 5,000,000 lines after its header. |
| `too_many_identifiers` | The file states more than 250,000 distinct identifiers, which a run does not keep: split it into releases. |
| `manifest_invalid` | The release manifest is not valid. |
| `stored_file_mismatch` | A stored release file is missing, or its size or SHA-256 differs from its record. |

### Other

| Code | Meaning |
|---|---|
| `more` | The record has further issues, counted and not listed. |

## What is refused

- **Registration** refuses an unknown dataset or adapter, a malformed option,
  a file over its limit or not a regular file, an invalid manifest, and a
  known version with any difference. Nothing is written.
- **A dry run** does not start without the switch, an active staff account,
  the code's registry version, a draft release with its data file, an
  approval for real data, and the adapter version the release was
  registered with; nor with an invalid resolution file or report-file path,
  nor while a dry run, commit or discard of the release runs. Nothing is
  written.
- **A file** is refused, with the verdict `refused`, for its encoding, its
  header, its length, more than 250,000 distinct identifiers, a missing or
  changed stored copy, an invalid manifest, or, in a synthetic release,
  anything that is not synthetic. A commit refuses it too.
- **A commit**, publishing, withdrawing and discarding refuse as
  [Typed releases](typed-releases.md#what-is-refused) says.
- **A row** that cannot be read is unstored, and one that breaks a rule is
  stored as `rejected`. Neither stops the run: every row is reported, with
  its line number.
- **Nothing is guessed.** An identifier maps only by its namespace's own
  identifier or by a reviewed resolution file. A symbol, a cross-reference
  or an alias alone never maps a row, and an isoform is never mapped to its
  entry.

## Synthetic releases

A release registered with `--synthetic` is made-up demonstration data. Its
flag is checked by every dry run and commit, never trusted, so real data can
never skip due diligence by being declared synthetic. A synthetic release
needs no licence snapshot, due diligence or approval to be dry-run or
committed. To be published it needs a licence snapshot that records terms,
and an `internal` approval
([Approving a release](typed-releases.md#approving-a-release)).

Every readable row is checked, rejected rows included, and so are rows
that are not stored but whose values could be read: a value over 10,000
characters, a malformed `record_id`, a control character in a value, or a
later line that repeats a `record_id` with other content. A line that is
too long, is not UTF-8 or has another number of fields than the header
has no values that line up with the header's columns, so it cannot be
checked; it is listed as unstored. The checks:

- each endpoint is in a synthetic namespace, `synthetic_gene` or
  `synthetic_protein` (the namespaces fixed to taxon 32630), at taxon 32630,
  NCBI's "synthetic construct";
- each label starts with `SYN-`, so none reads as a real gene's or
  protein's;
- every `publications` item is a `ref:<key>` item, a synthetic paper of
  its manifest (a key the manifest does not declare is `unknown_ref`).
  Any other item is a violation, a PMID or a DOI however it is written
  included (`pmid:`, `PMID 17081976`, a bare number, `doi:`, a `doi.org`
  address, a bare `10.` DOI), since any made-up PMID or DOI is someone's
  paper;
- each `context:host_taxon` is 32630, or a taxid openPIP does not register;
- each resolution entry a row meets maps to an entity of taxon 32630.

Any violation rejects its row (`synthetic_violation`) and makes the verdict
`refused`: the release is not synthetic data. Its scores are of
`synthetic:` score types, which no other release may use. Reference links,
`encodes` and `isoform_of`, are accepted only in a synthetic release, and
the report lists them.

## Where release files live

Release files are kept in private storage, under the setting
`GRAPH_FILES_ROOT`, which is fixed in the code and read from no environment
variable:

- in development, `backend/graph_files/` in the checkout;
- in production, `/app/graph_files` in the `graph_files` volume, which the
  `backend` and `celery` containers share.

Each file is stored as `releases/<release id>/<sha256>/content`, with
directories of mode 0700 and files of mode 0600. Copies being registered are
written to `incoming/` first. The name a file came with is kept only as
text, and never becomes part of a path.

- **Nothing serves them.** No address of the portal leads to release files,
  which are kept outside the media and static files.
- **They never belong in a commit or an image.** `.gitignore` and
  `backend/.dockerignore` leave out `backend/graph_files/`.
- **Disk use.** Every registered file is kept, once per release, for good:
  nothing deletes a release's files. Plan for the total size of your
  releases' files, plus room for the largest while it is copied in:
  registration refuses a file that would leave less than 64 MiB free. A
  registration that was killed may leave a partial copy, `file-*.part`,
  under `incoming/`, which you may delete while no registration runs.
  `graph_check` lists such leftovers, with anything else under the root
  that no row names
  ([Checking the graph](typed-releases.md#checking-the-graph)).
- **Back them up with the database.** The database records each file's
  path, size and SHA-256, so back up and restore the `graph_files` volume
  together with the database ([Maintenance](maintenance.md#backups)). A dry
  run or a commit refuses a release whose stored copy is missing or changed,
  `graph_check` lists it, and registering the same file again with the same
  options puts it back.
