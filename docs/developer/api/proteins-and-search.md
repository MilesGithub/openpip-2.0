# Proteins and search

Public endpoints for looking up proteins and their interaction networks. None
of them needs authentication.

## Searching for networks

### GET /api/search

The search behind the portal's search page. Give it one or more protein
identifiers. It returns the matching proteins, their interaction partners and
the interactions between them, ready to draw as a network.

| Parameter | Notes |
|---|---|
| `q` | One or more identifiers, separated by commas, spaces or new lines. Each is looked up in the portal's identifier table. Matching is exact but ignores capitalisation (`tp53` finds `TP53`; `TP5` finds nothing). An empty `q` returns an empty result. |
| `filter` | Which proteins and interactions to include. Default `None`. See below. |

| `filter` | Proteins returned | Interactions returned |
|---|---|---|
| `None` | The query proteins and every protein that interacts with one of them | Every interaction among all of those proteins, including partner–partner |
| `query_interactor` | The same | Only interactions with a query protein on at least one side |
| `query_query` (or any other value) | Only the query proteins | Only interactions between two query proteins |

Interactions marked as removed are never returned.

Which identifiers find a protein depends on the data the portal loaded. On the
reference deployment, gene names and Ensembl gene IDs find the interaction
network, and Entrez IDs find nothing. A UniProt accession often matches a
separate, empty record for the same protein instead of the one carrying its
interactions. `P04637` returns a single protein with no interactions, while
`TP53` returns its network of 113 proteins. Prefer gene names. This is
tracked as BUG-034.

```json
{
  "all_proteins": [ ... ],
  "all_interactions": [ ... ],
  "query_protein_id_array": [5319],
  "search_term": "TP53",
  "found_protein_summary": "TP53",
  "unfound_protein_summary": "",
  "domains": "",
  "complexes": ""
}
```

| Key | Contents |
|---|---|
| `all_proteins` | Protein objects (below). The query proteins come last. |
| `all_interactions` | Interaction objects (below). Interactions between two query proteins come first, then those with one query protein, then partner–partner interactions. |
| `query_protein_id_array` | Database IDs of the proteins your terms matched. |
| `search_term` | `q` as you sent it. |
| `found_protein_summary`, `unfound_protein_summary` | Your terms that did and did not match, joined with `<br>`. This format is kept from the original openPIP. Split on `<br>`, and never insert these strings into a page as HTML. |
| `domains`, `complexes` | Always empty strings. Kept for compatibility with the original openPIP. |

#### Protein object

```json
{
  "protein_id": 5319,
  "protein_uniprot_id": "P04637",
  "protein_ensembl_id": "ENSG00000141510",
  "protein_entrez_id": "7157",
  "protein_gene_name": "TP53",
  "protein_protein_name": "Cellular tumor antigen p53",
  "protein_description": "Acts as a tumor suppressor in many tumor types...",
  "protein_sequence": "MEEPQSDPSVEPPLSQETFSDLWKLLPENNVLSPLPSQAM...",
  "number_of_interactions_in_database": 113,
  "annotation_array": {"tissue_expression": "{...}", "subcellular_location": "{...}"},
  "tissue_expression_array": {"adipose_subcutaneous": "...", "adrenal_gland": "..."},
  "subcellular_location_expression_array": { ... },
  "tissue_specificity_array": { ... }
}
```

Missing text values are empty strings, never `null`. `annotation_array` maps
each annotation type to its raw stored value. The three `*_array` keys are
those annotations already parsed from JSON. They are `{}` when the protein
has no such annotation. Which annotations exist depends on the data the
portal loaded.

#### Interaction object

An interaction from the reference portal (`GET /api/search?q=TP53`):

```json
{
  "interaction_id": 64197,
  "interactor_A": {"protein_id": 5319, "protein_uniprot_id": "P04637",
                   "protein_gene_name": "TP53", "protein_ensembl_id": "ENSG00000141510"},
  "interactor_B": {"protein_id": 4700, "protein_uniprot_id": "Q00987-11",
                   "protein_gene_name": "MDM2", "protein_ensembl_id": "ENSG00000135679"},
  "score": null,
  "annotation_array": {"litbm_interaction": ["{\"pmid\":\"19656744\",\"experiment_type\":\"0997\",\"binary_type\":\"non_binary\"}"]},
  "experiment_array": [],
  "dataset_array": [
    {"dataset_reference": "25416956", "dataset_author": "Rolland T et al.",
     "year": "2014", "description": "", "interaction_status": "published",
     "name": "Lit-BM"}
  ],
  "interaction_category_array": {
    "highest_category_status": "Validated",
    "highest_order": 2,
    "interaction_category_array": [{"category_name": "Validated", "order": 2}]
  }
}
```

The `annotation_array` list is shortened here. This interaction has many
`litbm_interaction` entries.

| Key | Contents |
|---|---|
| `interactor_A`, `interactor_B` | When exactly one side is a query protein, it is always `interactor_A`. |
| `score` | Confidence score as a number, or `null` when the interaction has none. |
| `annotation_array` | Interaction annotations by type. Each value is a list of the raw stored strings. |
| `experiment_array` | Detection methods recorded for the interaction. |
| `dataset_array` | Every dataset the interaction appears in. `dataset_reference` is the PubMed ID (or `""`). `dataset_author` is `"Unpublished Dataset"` when no author is recorded. |
| `interaction_category_array` | The interaction's categories, and the one with the highest `order`. The portal colours edges by that one. With no categories, `highest_category_status` is `""` and `highest_order` is `0`. |

### POST /api/search/interactors

The same search as `GET /api/search`, taking a JSON body instead of query
parameters. The web interface uses it when you change the network filter.

```json
{"searchTerm": "TP53, MDM2", "filterParameter": "None"}
```

The response is identical to `GET /api/search`. One difference:
`filterParameter: "query_interactor"` is treated as `query_query` here.

### GET /api/home/network

The network for one protein picked at random from proteins that have
interactions. The portal's home page uses it for its preview. The response has
the same shape as `GET /api/search`. On a portal with no interaction data it
returns only empty `all_proteins`, `all_interactions` and
`query_protein_id_array`.

## Proteins

These endpoints accept an identifier in `{identifier}`, ignoring
capitalisation. It is looked up first in the identifier table, then in each
protein's own gene name, UniProt, Ensembl and Entrez fields, and the first match
wins. As with search, a UniProt accession can reach an empty duplicate record
on the reference portal (BUG-034).

### GET /api/proteins

Browse and filter all proteins. This is the only legacy endpoint that
paginates; the typed graph's evidence list
([Typed graph](typed-graph.md#get-apigraphv1relationshipsrelationship_refevidence))
takes `after` and `page_size`.

| Parameter | Notes |
|---|---|
| `q` | Matches part of a gene name, protein name, UniProt accession or any identifier. Results rank an exact gene-name match first, then gene names starting with `q`. |
| `ordering` | `gene` (default), `-gene`, `interactions` or `-interactions` (by number of interactions). |
| `include_empty` | `true` to include proteins with no gene name. By default they are left out. |
| `has_interactions` | `true` for only proteins with at least one interaction. |
| `has_sequence` | `true` for only proteins with a stored sequence. |
| `has_structure` | `true` for only proteins with a UniProt accession, which is needed to fetch an AlphaFold model. |
| `limit` | Page size. Default 100, maximum 500. |
| `offset` | Rows to skip. |

Boolean parameters accept `1`, `true`, `yes` or `on`.

`GET /api/proteins?limit=1` on the reference portal:

```json
{
  "count": 11602,
  "next": "https://openpip.usask.ca/api/proteins?limit=1&offset=1",
  "previous": null,
  "results": [
    {"protein_id": 5945, "protein_gene_name": "A1CF",
     "protein_protein_name": "APOBEC1 complementation factor",
     "protein_uniprot_id": "Q9NQ94-1",
     "number_of_interactions_in_database": 22, "has_sequence": true}
  ]
}
```

### GET /api/proteins/{identifier}

One protein's full record.

```json
{
  "protein_id": 5319,
  "protein_gene_name": "TP53",
  "protein_protein_name": "Cellular tumor antigen p53",
  "protein_uniprot_id": "P04637",
  "protein_ensembl_id": "ENSG00000141510",
  "protein_entrez_id": "7157",
  "protein_description": "Acts as a tumor suppressor in many tumor types...",
  "protein_sequence": "MEEPQSDPSVEPPLSQETFSDLWKLLPENNVLSPLPSQAM...",
  "number_of_interactions_in_database": 113,
  "annotation_array": {"tissue_expression": ["{...}"]},
  "tissue_expression_array": { ... },
  "subcellular_location_expression_array": { ... },
  "identifiers": [
    {"identifier": "TP53", "naming_convention": "gene_name"},
    {"identifier": "ENSG00000141510", "naming_convention": "ensembl"}
  ]
}
```

Unlike the search response, `annotation_array` here maps each type to a
**list** of values.

| Status | Body |
|---|---|
| 404 | `{"detail": "Not found."}` |

### GET /api/proteins/{identifier}/interactors

A protein's interaction partners, ranked by how many interaction records link
each partner to it.

| Parameter | Notes |
|---|---|
| `limit` | How many partners to return. Default 10, maximum 100. |

`GET /api/proteins/TP53/interactors?limit=1` on the reference portal:

```json
{
  "count": 112,
  "results": [
    {"protein_id": 60, "protein_gene_name": "CREBBP",
     "protein_protein_name": "CREB-binding protein",
     "protein_uniprot_id": "Q92793",
     "number_of_interactions_in_database": 54, "shared_interaction_count": 1}
  ]
}
```

`count` is the total number of partners, whatever `limit` is. A protein's
interaction with itself is not counted as a partner.

| Status | Body |
|---|---|
| 404 | `{"detail": "Not found."}` |

### GET /api/proteins/autocomplete

Suggestions for a search box.

| Parameter | Notes |
|---|---|
| `q` | At least 2 characters, otherwise the result is empty. |

Returns up to 20 identifier strings of any kind: those starting with `q`
first, then those containing it, each group alphabetical.

`GET /api/proteins/autocomplete?q=TP53` on the reference portal:

```json
["TP53", "TP53BP1", "TP53BP2", "TP53I13", "TP53I3", "TP53INP1", "TP53INP2", "TP53RK"]
```

## Interaction categories

### GET /api/interactions/categories

Every interaction category, in display order.

```json
[
  {"id": 1, "category_name": "Published", "order": "1"},
  {"id": 2, "category_name": "Validated", "order": "2"},
  {"id": 3, "category_name": "Verified", "order": "3"},
  {"id": 4, "category_name": "Literature", "order": "4"}
]
```

`order` is a string, and the list is sorted by its numeric value. The same
categories, with colours and descriptions, are available from
[`GET /api/interaction-categories`](administration.md#get-apiinteraction-categories).
