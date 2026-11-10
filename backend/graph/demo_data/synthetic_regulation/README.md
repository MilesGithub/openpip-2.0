# Synthetic regulation: openPIP's demonstration release

Every row, entity, label and publication in this directory is synthetic. It
was invented for openPIP's tests and demonstrations, and it describes no real
biology: no gene, protein, interaction or paper named here exists. The files
are distributed under the repository's MIT licence (LICENSE, at the root of
the repository).

They are one dataset, `synthetic-regulation`, in two releases read by
`canonical_tsv_v1`:

- `release_v1.tsv`: 26 rows. 17 are imported; 9 are reported by the dry run
  and never imported.
- `release_v2.tsv`: v1's 17 imported rows, with one effect corrected (syn-01,
  SYN-TF1 to SYN-G1: activating in v1, inhibiting in v2). v2 supersedes v1.
- `manifest.json`: the two papers the rows cite, by local key (title-only, with
  a year), and the two PSI-MI interaction types they use, with the labels of
  openPIP's crosswalk. It is registered with both releases.

## Loading it

```text
python manage.py graph_load_demo --as USERNAME
```

The loader (graph/demo.py) registers both releases as synthetic, v2
superseding v1; dry-runs and commits each with --create-entities; records an
MIT licence snapshot and an internal approval for each; and publishes v1, then
v2. It then records two browser instances, each only if it is not there
already: `synthetic-regulation`, which selects v2 and cites the two papers, and
this portal's `portal_ppi` entry, `protein-interactions` unless the portal has
one. Staff see the demonstration and both instances. Visitors see the
demonstration's data once an operator records a public_view approval with
graph_approve. An instance is listed for them once an operator makes it
public, and they can open an unlisted one by its slug; a typed instance shows
them data only if they can open it and its release has public_view. Run
again, the loader completes a load that stopped, and writes nothing once the
demonstration is loaded. The operator guide's pages on releases and on
browser instances describe it (docs/operator-guide/typed-releases.md and
docs/operator-guide/browser-instances.md).

## The cases

The rows cover the cases of the architecture's D12
(docs/project/generalization/GENERALIZATION_ARCHITECTURE.md). Line numbers are
v1's; v2 has the same rows on lines 2 to 18. G is a gene of synthetic_gene, P
a protein entry of synthetic_protein, I an isoform; TR is
transcriptional_regulation, PI physical_interaction.

| Record | Line | Row | Case |
|---|---|---|---|
| syn-01 | 2 | G SYN-TF1 to G SYN-G1, TR, activating (v2: inhibiting) | Direction; label and identifier; release separation |
| syn-02 | 3 | G SYN-G1 to G SYN-TF1, TR, inhibiting | Direction |
| syn-03 | 4 | G SYN-TF2 to G SYN-G2, TR, activating, ref:syn-paper-1 | Mixed effect; two publications |
| syn-04 | 5 | G SYN-TF2 to G SYN-G2, TR, inhibiting, ref:syn-paper-2 | Mixed effect; two publications |
| syn-05 | 6 | G SYN-TF1 to G SYN-G4, TR, unknown | Unknown effect |
| syn-06 | 7 | G SYN-TF3 to G SYN-G4, TR, no effect | Effect not stated |
| syn-07 | 8 | G SYN-TF1 to G SYN-G2, TR, tested_absent | Tested absent |
| syn-08 | 9 | G SYN-TF2 to G SYN-TF2, TR, activating | Autoregulation |
| syn-09 | 10 | G SYN-TF3 to G SYN-G5, TR, computational_prediction, two scores | Prediction; no publication; two typed scores |
| syn-10 | 11 | P SYN-P1 and P SYN-P2, PI, MI:0915 | Symmetric physical; the reference chain's physical edge |
| syn-11 | 12 | P SYN-P2 and P SYN-P1, PI, MI:0407 | Symmetric physical, the other order; the strongest level |
| syn-12 | 13 | G SYN-TF1 to P SYN-P1, encodes | Reference links |
| syn-13 | 14 | I SYN-P1-1 to P SYN-P1, isoform_of | Reference links |
| syn-14 | 15 | G SYN-TF3 to G SYN-G3 labelled SYN-ALPHA, TR, activating | One label: candidate |
| syn-15 | 16 | G SYN-TF3 to G SYN-G6 labelled SYN-SHARED, TR, activating, two papers | Shared label (gene); one row citing two papers |
| syn-16 | 17 | P SYN-P3 labelled SYN-SHARED and P SYN-P2, PI, no interaction type | Shared label (protein); level not stated |
| syn-17 | 18 | P SYN-P3 and P SYN-P2, PI, tested_absent, MI:0407 | Only support sets the level; tested for; a shown edge shows the evidence against it |
| syn-x1 | 19 | G SYN-TF1 to SYN-g7 | Reported: identifier failing its pattern (bad_identifier) |
| syn-x2 | 20 | not_a_relationship_type | Reported: unknown type (unknown_relationship_type) |
| syn-x3 | 21 | SYN-TF3 as not_an_entity_type | Reported: unknown type (unknown_entity_type) |
| syn-x4 | 22 | PI with an effect | Reported: effect on a symmetric type (claim_rule_7) |
| syn-x5 | 23 | tested_absent with an effect | Reported: effect on a tested-absent row (claim_rule_8) |
| syn-x6 | 24 | TR with directed false | Reported: direction contradicting the type (claim_rule_3) |
| syn-x7 | 25 | effect negative | Reported: "negative" as an effect (negative_effect) |
| syn-x8 | 26, 27 | one key, two contents | Reported: duplicate key, different content (conflicting_duplicate; line 27 is not stored) |

The rows that are reported leave both label cells blank. D12's identifier in
an undeclared namespace is a synthetic violation, which refuses a synthetic
release whole, so it is not here: a test builds that file on its own.

## Never edited in place

Registration compares each release's files with the SHA-256 digests it
stored, so a release's files never change once a portal has loaded them. A
change ships as new release versions: a v3 that supersedes v2, with its own
files, and a new Version in graph/demo.py's VERSIONS. Editing these files
would make the loader refuse on every portal that loaded them.
