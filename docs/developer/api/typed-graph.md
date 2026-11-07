# Typed graph

The typed graph holds relationships between typed entities (genes, proteins
and protein isoforms), with the evidence each release of a dataset states for
them. An operator loads it release by release, as
[Typed data format](../../operator-guide/typed-data-format.md) and
[Typed releases](../../operator-guide/typed-releases.md) describe. This page
documents the API that reads it, under `/api/graph/v1/`.

The API answers only while `TYPED_GRAPH_ENABLED` is on
([Configuration reference](../../operator-guide/configuration.md#backend-variables)).
While it is off, every route but `status` answers 404, and
[`status`](#get-apigraphv1status) says so.

The examples on this page come from the **synthetic demonstration**, which
`graph_load_demo` loads
([Loading the synthetic demonstration](../../operator-guide/typed-releases.md#loading-the-synthetic-demonstration)),
as a staff account sees it, because no reference portal serves the typed
graph. They are synthetic data, and their ids are those of a first load.

Paths have no trailing slash, and responses are JSON. Reads need no login, and
a logged-in staff account sees more
([What you may see](#what-you-may-see)). An invalid or expired token gets 401,
as on every public endpoint ([Authentication](index.md#authentication)),
except on `status`, which reads no token.

## Conventions

### Ids

Ids are opaque strings of the form `kind:number`: `entity:12`,
`relationship:40`, `release:3`. Use them as the API writes them. A bare
number, a sign, a space or a leading zero is malformed, and gets 400.

### Selecting releases

[`resolve`](#get-apigraphv1resolve) and
[`entities/{entity_id}`](#get-apigraphv1entitiesentity_id) read a set of
releases. The `releases` parameter selects it: comma-separated refs, which may
also be spread over repeated parameters. A selection *replaces* the default
set, so selecting only releases you may not see shows nothing.

| `releases` | What you get |
|---|---|
| absent | The default set: the published releases you may see. A superseded release contributes nothing. |
| `release:1,release:2`, or repeated | Those you may see: published and superseded releases, each superseded one flagged `superseded: true` in the response's `releases`. A staff account may also select a staged release that has an `internal` approval. Visitors never see one. |
| A ref that names nothing; a withdrawn, draft or unapproved release; for a visitor also an embargoed or staged one | Dropped without a word: `resolve` answers `unmatched`, and the entity page answers 404. |
| A malformed ref, an empty `releases=`, or more than 50 distinct refs | 400 ([Errors](#errors)). |

A value that is malformed or over the cap is refused, but a well-formed ref is
a selection, and is never checked against what exists.

### What you may see

Visitors, logged in or not, see releases with an effective `public_view`
approval, and a logged-in staff account sees releases with an effective
`internal` approval. An embargoed release is hidden from visitors, whatever
`public_view` says. The default set is the published releases among those,
and a selection may name others, as the table above says. An entity exists for
you only if a release in the set created it or names it in evidence. You see
only what you may see; nothing says why: an entity you may not see answers
exactly as one that does not exist. A label shows only to those who may read
the release that wrote it, and otherwise the identifier does. What you may
read does not depend on what you selected. Responses differ by caller, and
carry `Vary: Authorization`.

### Entity nodes

Every entity in a response is this node, which `resolve` lists as a
candidate's `entity` and the entity page as `entity` and in `nodes`:

| Key | Contents |
|---|---|
| `id` | The entity's ref. |
| `type` | The entity type's slug, from `vocabularies`. |
| `label` | The entity's label, when you may read the release that wrote it. Otherwise the value of its primary identifier, or its ref if it has none. Never `null`. |
| `taxon` | An NCBI taxid, or `null`. |
| `status` | `active`, `provisional`, `merged` or `retired`. Every node has a value, but the type is a string or `null`, so a client must accept `null`. |
| `primary_identifier` | `{namespace, value}`, both strings, or `null` for an entity with none. |

### Release entries

Every response that reads a set of releases carries a `releases` list, sorted
by id, with one entry for each release it names, so its attribution and
licence travel with the data:

| Key | Contents |
|---|---|
| `id` | The release's ref. |
| `dataset` | `{slug, title}`, both strings. |
| `version` | The release's version. |
| `status` | `published`, `superseded` or `staged`. |
| `superseded` | `true` when a published correction replaced the release. |
| `is_synthetic` | `true` for synthetic data. |
| `licence` | `{name, spdx_id, url}`, always an object. Each value is a string or `null`. |
| `attribution` | A string, possibly `""`. |
| `changes_note` | What openPIP's import changes in the source's data, as CC BY and CC BY-SA require beside attribution: a string, or `null` when the release names no adapter that this portal has. |
| `scopes` | Every effective approval of the release, of `internal`, `public_view`, `public_bulk` and `external_services`, in that order. Every caller sees them. |

## Errors

A bad request gets one 400 that lists every problem: unknown names first, then
the path, then the route's parameters in the order its table lists them. An
unknown parameter or value is never answered with an empty result.
`GET /api/graph/v1/resolve?q=x&foo=1&entity_type=chemical` answers:

```json
{
  "detail": "Unknown parameter 'foo'. Allowed: q, namespace, taxon, entity_type, releases. Unknown entity_type 'chemical'. Allowed: gene, protein, protein_isoform.",
  "errors": [
    {
      "parameter": "foo",
      "value": null,
      "allowed": ["q", "namespace", "taxon", "entity_type", "releases"],
      "limit": null
    },
    {
      "parameter": "entity_type",
      "value": "chemical",
      "allowed": ["gene", "protein", "protein_isoform"],
      "limit": null
    }
  ]
}
```

| Key | Contents |
|---|---|
| `detail` | One sentence per problem, each naming the parameter, and the allowed values or the limit when there is one. |
| `errors` | One entry per problem, at most 20. Past that, `detail` ends with `and more.` |
| `errors[].parameter` | The parameter's name. |
| `errors[].value` | The value refused, as a string, or `null` when no one value is at fault: a name that is unknown or given twice, a list over its cap, a parameter given with nothing in it, or a required one left out. |
| `errors[].allowed` | The allowed values, as a list of strings, or `null` when the parameter has no such list. |
| `errors[].limit` | The cap, as an integer, or `null` when none applies. |

| Status | Body |
|---|---|
| 400 | As above. More than 1,000 query parameters get `{"detail": "Too many query parameters.", "errors": []}`. |
| 401 | An invalid or expired token, on every route but `status`. |
| 404 | `{"detail": "Not found."}`, on every route but `status` while the typed graph is off, and for an entity that does not exist or that you may not see, which read alike. It is never a 403. |
| 406 | An `Accept` header the API cannot satisfy. |
| 429 | Over a rate limit. The body says how many seconds to wait, and so does the `Retry-After` header. |

A request line has its own limit. Behind the shipped gunicorn it holds 4,094
bytes, counting the method, the path, the query string as sent, with its
percent-encoding, and the protocol. A longer request never reaches the API,
whatever the caps on this page allow. It gets the web server's own HTML
error, with no `detail` or `errors`: gunicorn's 400 up to 8,190 bytes and
nginx's 414 beyond that. A proxy in front that speaks HTTP/2 may instead
close the connection with no status at all. So one `resolve` request holds
20 terms of 200 ASCII characters, or 25 of 161 joined by plain commas, and
fewer non-ASCII characters, since each byte of one takes three: 25 terms of
26 Greek or 17 Chinese characters. Split a longer list over several requests.

The API defines no `format` parameter of its own, since the name is reserved
by the framework: `?format=json` changes nothing, `?format=api` gives the
browsable page, as on every other endpoint, and any other value gets 404.
While the typed graph is off, the 404 of every route but `status` is JSON,
whatever is asked.

## Rate limits

Two limits apply per caller, that is per IP address, or per account when
logged in: one to `resolve`, and one that `vocabularies` and
`entities/{entity_id}` share. [The rate table](index.md#rate-limits) gives
their numbers. They come on top of the anonymous and logged-in limits, and a
request they refuse does not count against those. A request over a limit gets
429 with a `Retry-After` header. `status` has neither limit: it reads no
token, so it is counted per IP address at the logged-in rate.

!!! note
    Per-caller limits rely on a host proxy that sets `X-Forwarded-For`.
    Without one, callers that send no such header share one limit, and a
    caller that sends one chooses its own, so the per-caller limits stop
    nobody. The per-request caps hold either way. The header can also name an
    account's counter, so such a caller can use up a logged-in user's limits.

## Status

### GET /api/graph/v1/status

Whether the typed graph is on, and what this version serves. This is the one
route that answers while the typed graph is off.

| Parameter | Notes |
|---|---|
| none | |

With the typed graph on:

```json
{
  "typed_graph": true,
  "api_version": "graph/v1",
  "registry_version": {"code": "1.1", "database": "1.1"},
  "features": {
    "resolve": true,
    "entities": true,
    "network": false,
    "relationships": false,
    "browsers": false,
    "datasets": false,
    "summary": false,
    "exports": false
  },
  "limits": {
    "q_max_chars": 200,
    "resolve_max_terms": 25,
    "resolve_max_candidates": 20,
    "max_releases": 50,
    "throttles": {"graph_read": "300/min", "graph_resolve": "120/min"}
  }
}
```

And off:

```json
{
  "typed_graph": false,
  "api_version": "graph/v1",
  "registry_version": {"code": "1.1", "database": null},
  "features": {
    "resolve": false,
    "entities": false,
    "network": false,
    "relationships": false,
    "browsers": false,
    "datasets": false,
    "summary": false,
    "exports": false
  },
  "limits": {
    "q_max_chars": 200,
    "resolve_max_terms": 25,
    "resolve_max_candidates": 20,
    "max_releases": 50,
    "throttles": {"graph_read": "300/min", "graph_resolve": "120/min"}
  }
}
```

| Key | Contents |
|---|---|
| `typed_graph` | `true` while `TYPED_GRAPH_ENABLED` is on. |
| `api_version` | `graph/v1`. |
| `registry_version` | `{code, database}`: the registry version of this code, a string, and that of the portal's tables, a string or `null`. `database` is `null` while the typed graph is off, and until an operator has run `graph_sync_registry`. |
| `features` | One boolean per group of routes, as below. |
| `limits` | The caps on one request and the rate limits, as below. Present whether the typed graph is on or off. |

| `features` key | Covers |
|---|---|
| `resolve` | Resolving identifiers ([`resolve`](#get-apigraphv1resolve)). |
| `entities` | Entity pages ([`entities/{entity_id}`](#get-apigraphv1entitiesentity_id)). |
| `network` | Networks around chosen entities. |
| `relationships` | Relationship pages and their evidence. |
| `browsers` | The gallery of dataset-specific browsers. |
| `datasets` | The catalogue of datasets and releases. |
| `summary` | Figures for the whole portal. |
| `exports` | Filtered exports. |

`true` means this version serves the group and the typed graph is on. `false`
means this version does not serve it, or the typed graph is off, when every
key is `false`.

| `limits` key | Contents |
|---|---|
| `q_max_chars` | The most characters in one term of `q`: a limit per term, not on the whole value. A request line has its own limit ([Errors](#errors)). |
| `resolve_max_terms` | The most terms in one `resolve` request. |
| `resolve_max_candidates` | The most candidates listed for one term. |
| `max_releases` | The most distinct releases a selection names. |
| `throttles` | The rate of each limit that a served route uses, by its name: `graph_read` and `graph_resolve`. |

| Status | Body |
|---|---|
| 200 | Any well-formed `GET`, with the typed graph on or off, whatever the token. |
| 400 | An unknown parameter ([Errors](#errors)). |
| 404 | `?format=xml`, or any other `format` the API does not have. |
| 406 | An `Accept` header the API cannot satisfy. |
| 429 | Over the limit ([Rate limits](#rate-limits)). |

A client treats anything but 200 as "features unknown", not as "off".

## Vocabularies

### GET /api/graph/v1/vocabularies

The registries as the portal's tables hold them, and the closed sets of values
that typed responses use. It answers 404 while the typed graph is off.

| Parameter | Notes |
|---|---|
| none | |

The example is shortened to one relationship type and one closed set:

```json
{
  "relationship_types": [
    {
      "slug": "encodes",
      "label": "Encodes",
      "definition": "A reference link: the gene encodes the protein entry, as an approved, versioned reference or mapping release states it. It joins the gene layer to the protein layer and is not evidence of any interaction or regulation. It is never inferred from shared identifiers or labels.",
      "family": "reference",
      "orientation": "role_ordered",
      "polarity": "none",
      "allowed_subject_types": ["gene"],
      "allowed_object_types": ["protein"],
      "subject_role_label": "gene",
      "object_role_label": "protein entry",
      "self_pairs_allowed": false,
      "psi_mi_anchors": [],
      "display_token": null,
      "is_reference": true
    }
  ],
  "outcomes": [
    {"value": "supported", "label": "supported"},
    {"value": "tested_absent", "label": "tested absent"},
    {"value": "inconclusive", "label": "inconclusive"},
    {"value": "not_significant", "label": "not significant"}
  ]
}
```

| Key | Contents |
|---|---|
| `api_version` | `graph/v1`. |
| `registry_version` | `{code, database}`, as in [`status`](#get-apigraphv1status). |
| `taxa` | Each with `taxid`, `scientific_name`, `common_name`, `rank`, `parent` and `merged_into`. `common_name` and `rank` are strings or `null`; `parent` and `merged_into` are taxids or `null`. |
| `entity_types` | Each with `slug`, `label`, `definition`, `taxon_policy`, `identity_scheme`, `assembly_required` and `display_token`, a string or `null`. |
| `namespaces` | Each with `slug`, `label`, `curie_prefix`, `allowed_entity_types`, `scope`, `fixed_taxon`, a taxid or `null`, `case_policy`, `version_policy`, `defines_identity`, `pattern`, `url_template`, where `""` means no link, and `authority`. |
| `relationship_types` | Each with `slug`, `label`, `definition`, `family`, `orientation`, `polarity`, `allowed_subject_types`, `allowed_object_types`, `subject_role_label`, `object_role_label`, `self_pairs_allowed`, `psi_mi_anchors`, `display_token`, a string or `null`, and `is_reference`. |
| `score_types` | Each with `slug`, `label`, `definition`, `value_kind`, `direction`, `range_min` and `range_max`, numbers or `null`, `unit` and `null_model`, strings or `null`, `defining_document` and `comparable_across_datasets`. |
| `outcomes`, `evidence_bases`, `assertion_methods`, `context_facets`, `identifier_roles` | Closed sets of `{value, label}`, the label being the value in words. |
| `effects`, `genetic_signs` | Closed sets of `{value, label, stored}`. `stored` is `false` for a value only a summary gives, which the database never holds. |
| `interaction_levels` | A closed set of `{value, label, strength}`, strongest first. |

The lists of rows are sorted by `taxid` or `slug`, and the closed sets keep the
order they are declared in. The `allowed_*_types` are sorted slugs. Vocabulary
terms are not registries, and are left out.

`url_template` is the registry's text. Use `identifiers[].url` from the
[entity page](#get-apigraphv1entitiesentity_id), and never build a link from a
template.

Two more sets of values belong to the API itself:

| Enumeration | Values |
|---|---|
| A term's `status` | `resolved`, `candidate`, `ambiguous`, `unmatched` |
| A match's `kind` | `primary`, `secondary`, `xref`, `symbol`, `alias`, `label` |

## Resolving identifiers

### GET /api/graph/v1/resolve

Which entities each term names, of those you may see. It answers 404 while the
typed graph is off.

| Parameter | Notes |
|---|---|
| `q` | Required. The terms, at most 25 in all, each at most 200 characters. Repeatable. A request line has its own limit ([Errors](#errors)). |
| `namespace` | A namespace's slug, from `vocabularies`. Every term is read in that namespace only, with or without its prefix, and labels are not searched. |
| `taxon` | An NCBI taxid, from `vocabularies`. Only entities of this taxon. |
| `entity_type` | An entity type's slug, from `vocabularies`. Only entities of this type. |
| `releases` | The releases to read ([Selecting releases](#selecting-releases)). |

`q` splits on commas, tabs and new lines, never on spaces, because labels may
hold spaces. `q` cannot name a label that holds a comma, tab or new line; use
an identifier. Each term is trimmed, empty ones are dropped, and a term given
twice gets its result twice.

A term may name its namespace with a prefix, as in `uniprotkb:P04637`. The
prefix is the namespace's `curie_prefix` from `vocabularies`, which may differ
from its slug: `ensembl_gene` has the prefix `ensembl`. The term is then read
in that namespace only, and a prefix that names no namespace is `unmatched`.
Any other term is read in every namespace whose rules accept it, and compared
with labels.

A prefix is a letter, then any letters, digits, `.`, `_` or `-`, then a colon
with text after it. `q` cannot name a label that starts that way either, such
as `Ras:GTP complex`, because it is read as a prefix and a value; use an
identifier.

`status` counts distinct entities you may see, so an entity matched in several
ways counts once:

- `resolved`: one entity, and the term is its primary identifier, as that
  identifier's namespace reads it;
- `candidate`: one entity, matched some other way, to be confirmed: an alias,
  a symbol, a cross-reference, a secondary accession or a label, or a primary
  identifier that states a different version from the term, or that differs
  in case where case tells identifiers apart;
- `ambiguous`: two or more entities, of which none is chosen. You choose;
- `unmatched`: no entity you may see.

A namespace's `case_policy`, from `vocabularies`, says what case means in it.
Under `exact`, case tells identifiers apart: a term in another case is a
`candidate` with `case_exact: false`, as `syn-tf1` is for `SYN-TF1`, unless the
namespace holds the term as written. Under `upper` and `insensitive`, case
changes nothing: `p04637` is the identifier `P04637`, so it is `resolved` with
`exact_primary: true`. `case_exact` is then `true` under `upper`, and under
`insensitive` it says whether the term's case is the stored value's.

Candidates list exact primary matches first, then the rest by entity type,
taxon and match.

`status` counts every match, but `candidates` lists at most 20. A term that
was cut has `truncated: true`, and also shows in `page`, where
`page.omitted.terms` counts the distinct terms cut, and in `warnings`, with one
`candidates_truncated` warning per distinct term.

`releases` lists each release a candidate names, with its licence, attribution
and note of changes.

`GET /api/graph/v1/resolve?q=SYN-TF1,SYN-SHARED` on the demonstration, where
`SYN-SHARED` is the label of a gene and of a protein:

```json
{
  "api_version": "graph/v1",
  "query": {
    "q": ["SYN-TF1", "SYN-SHARED"],
    "namespace": null,
    "taxon": null,
    "entity_type": null,
    "releases": null
  },
  "results": [
    {
      "term": "SYN-TF1",
      "status": "resolved",
      "truncated": false,
      "candidates": [
        {
          "entity": {
            "id": "entity:7",
            "type": "gene",
            "label": "SYN-TF1",
            "taxon": 32630,
            "status": "provisional",
            "primary_identifier": {
              "namespace": "synthetic_gene",
              "value": "SYN-TF1"
            }
          },
          "exact_primary": true,
          "matches": [
            {
              "kind": "primary",
              "namespace": "synthetic_gene",
              "value": "SYN-TF1",
              "case_exact": true,
              "version_mismatch": false
            },
            {
              "kind": "label",
              "namespace": null,
              "value": "SYN-TF1",
              "case_exact": true,
              "version_mismatch": false
            }
          ],
          "releases": ["release:2"]
        }
      ]
    },
    {
      "term": "SYN-SHARED",
      "status": "ambiguous",
      "truncated": false,
      "candidates": [
        {
          "entity": {
            "id": "entity:6",
            "type": "gene",
            "label": "SYN-SHARED",
            "taxon": 32630,
            "status": "provisional",
            "primary_identifier": {
              "namespace": "synthetic_gene",
              "value": "SYN-G6"
            }
          },
          "exact_primary": false,
          "matches": [
            {
              "kind": "label",
              "namespace": null,
              "value": "SYN-SHARED",
              "case_exact": true,
              "version_mismatch": false
            }
          ],
          "releases": ["release:2"]
        },
        {
          "entity": {
            "id": "entity:13",
            "type": "protein",
            "label": "SYN-SHARED",
            "taxon": 32630,
            "status": "provisional",
            "primary_identifier": {
              "namespace": "synthetic_protein",
              "value": "SYN-P3"
            }
          },
          "exact_primary": false,
          "matches": [
            {
              "kind": "label",
              "namespace": null,
              "value": "SYN-SHARED",
              "case_exact": true,
              "version_mismatch": false
            }
          ],
          "releases": ["release:2"]
        }
      ]
    }
  ],
  "releases": [
    {
      "id": "release:2",
      "dataset": {
        "slug": "synthetic-regulation",
        "title": "Synthetic regulation example"
      },
      "version": "v2",
      "status": "published",
      "superseded": false,
      "is_synthetic": true,
      "licence": {
        "name": "MIT License",
        "spdx_id": null,
        "url": "https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE"
      },
      "attribution": "Copyright (c) 2016-2026 The openPIP authors",
      "changes_note": "Identifiers are normalized by their namespace's rules and resolved to typed entities; an identifier may be mapped by a reviewed resolution file (basis curated); a row that cites several papers becomes one assertion per paper. No value is inferred, merged or corrected.",
      "scopes": ["internal"]
    }
  ],
  "page": {"truncated": false, "max_candidates": 20, "omitted": {}},
  "warnings": []
}
```

| Key | Contents |
|---|---|
| `api_version` | `graph/v1`. |
| `query` | The request as read: `q`, a list of the terms as given, trimmed, with duplicates; `namespace` and `entity_type`, slugs or `null`; `taxon`, an integer or `null`; `releases`, the distinct refs named, sorted by id, or `null`. Never reduced to what you may see. |
| `results` | One entry per term of `query.q`, in that order. |
| `results[].term` | The term. |
| `results[].status` | The term's status, as above. |
| `results[].truncated` | `true` when more than 20 entities match, and 20 are listed. |
| `results[].candidates` | At most 20, each as below. `[]` when the term is `unmatched`. |
| `candidates[].entity` | The [entity node](#entity-nodes). |
| `candidates[].exact_primary` | `true` when the term is this entity's primary identifier, as that identifier's namespace reads it. |
| `candidates[].matches` | The ways the term matched, each `{kind, namespace, value, case_exact, version_mismatch}`. `namespace` is `null` for a label. |
| `candidates[].releases` | The refs of the releases that created or name the entity, sorted by id. Each has an entry in the top-level `releases`. |
| `releases` | The [release entries](#release-entries) of every release a candidate lists. `[]` when every term is `unmatched`. |
| `page` | `{truncated, max_candidates, omitted}`. `omitted` is `{}`, or `{"terms": n}` when `n` distinct terms were cut. |
| `warnings` | Each `{code, detail}`, and `[]` when nothing was cut. |

| Status | Body |
|---|---|
| 400 | A term over the cap or holding a control character, no term at all, more than 25 terms, an unknown value of `namespace`, `taxon` or `entity_type`, a parameter given twice that takes one value, or a bad `releases`. See [Errors](#errors). |
| 404 | `{"detail": "Not found."}` while the typed graph is off. |

## Entities

### GET /api/graph/v1/entities/{entity_id}

One entity, as you may see it: its identity, its identifiers, its reference
relationships, how many relationships of each type it has, and the releases
that say so. It answers 404 while the typed graph is off.

| Parameter | Notes |
|---|---|
| `entity_id` | In the path: the entity's ref, as in `entities/entity:12`. Any other value gets 400, unless it is empty or holds a slash, percent-encoded or not: such a path matches no route, and gets the server's own 404, which is not JSON. |
| `releases` | The releases to read ([Selecting releases](#selecting-releases)). |

`GET /api/graph/v1/entities/entity:7` on the demonstration, the page of the
gene SYN-TF1, with the default set:

```json
{
  "api_version": "graph/v1",
  "query": {"entity_id": "entity:7", "releases": null},
  "entity": {
    "id": "entity:7",
    "type": "gene",
    "label": "SYN-TF1",
    "taxon": 32630,
    "status": "provisional",
    "primary_identifier": {"namespace": "synthetic_gene", "value": "SYN-TF1"}
  },
  "description": "",
  "created_by_release": null,
  "identifiers": [
    {
      "namespace": "synthetic_gene",
      "value": "SYN-TF1",
      "version": null,
      "role": "primary",
      "url": null
    }
  ],
  "reference_relationships": [
    {
      "id": "relationship:1",
      "type": "encodes",
      "family": "reference",
      "orientation": "role_ordered",
      "source": "entity:7",
      "target": "entity:10",
      "releases": ["release:2"],
      "is_synthetic": true
    }
  ],
  "nodes": [
    {
      "id": "entity:7",
      "type": "gene",
      "label": "SYN-TF1",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {
        "namespace": "synthetic_gene",
        "value": "SYN-TF1"
      }
    },
    {
      "id": "entity:10",
      "type": "protein",
      "label": "SYN-P1",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {
        "namespace": "synthetic_protein",
        "value": "SYN-P1"
      }
    }
  ],
  "relationship_counts": {"encodes": 1, "transcriptional_regulation": 3},
  "releases": [
    {
      "id": "release:2",
      "dataset": {
        "slug": "synthetic-regulation",
        "title": "Synthetic regulation example"
      },
      "version": "v2",
      "status": "published",
      "superseded": false,
      "is_synthetic": true,
      "licence": {
        "name": "MIT License",
        "spdx_id": null,
        "url": "https://github.com/Helmy-Lab/openpip-2.0/blob/main/LICENSE"
      },
      "attribution": "Copyright (c) 2016-2026 The openPIP authors",
      "changes_note": "Identifiers are normalized by their namespace's rules and resolved to typed entities; an identifier may be mapped by a reviewed resolution file (basis curated); a row that cites several papers becomes one assertion per paper. No value is inferred, merged or corrected.",
      "scopes": ["internal"]
    }
  ]
}
```

| Key | Contents |
|---|---|
| `api_version` | `graph/v1`. |
| `query` | `{entity_id, releases}`: the ref as requested, and the distinct refs named, sorted by id, or `null`. |
| `entity` | The [entity node](#entity-nodes). |
| `description` | The entity's description, when you may read the release that wrote it, otherwise `""`. Never `null`. |
| `created_by_release` | The ref of the release that created the entity, only when `releases` lists it, otherwise `null`. Here v1 created the entity, and v2 supersedes v1, so it is outside the default set. |
| `identifiers` | Each `{namespace, value, version, role, url}`, primary first. `version` is a string or `null`. `url` is as below. |
| `reference_relationships` | The reference relationships that touch the entity in the selected releases, sorted by id, each `{id, type, family, orientation, source, target, releases, is_synthetic}`. `source` and `target` are entity refs, the stored subject and object. `releases` lists the refs of the releases that state it, and `is_synthetic` is `true` when any of those is synthetic. `[]` when there are none. |
| `nodes` | The [entity node](#entity-nodes) of each end of `reference_relationships`, once each, sorted by numeric id. `[]` when there are no reference relationships. |
| `relationship_counts` | How many relationships of each type touch the entity, by type slug, sorted. `{}` when there are none. |
| `releases` | The [release entries](#release-entries) of the selected releases that created or name the entity, or state one of its `reference_relationships`. |

Until a later release records which release states an alias, every stored
identifier of a visible entity is listed. `url` is the identifier's address
at its authority, and is an `http` or `https` address or `null`.

`relationship_counts` count relationships with supporting evidence in the
selected releases. Here SYN-TF1's relationship with SYN-G2, tested and not
found, is not counted.

| Status | Body |
|---|---|
| 301 | The entity was merged into another, and you may see both. The body is empty, and `Location` names the survivor's page, with your query string kept. |
| 400 | A malformed `entity_id`, an unknown parameter or a bad `releases`. See [Errors](#errors). |
| 404 | `{"detail": "Not found."}`: no such entity, or one you may not see, a merged entity whose survivor you may not see included. |
