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
`relationship:40`, `release:3`, `evidence:36`. Use them as the API writes
them. A bare number, a sign, a space or a leading zero is malformed, and gets
400.

### Selecting releases

[`resolve`](#get-apigraphv1resolve),
[`entities/{entity_id}`](#get-apigraphv1entitiesentity_id),
[`network`](#get-apigraphv1network),
[`relationships/{relationship_ref}`](#get-apigraphv1relationshipsrelationship_ref)
and its [`evidence`](#get-apigraphv1relationshipsrelationship_refevidence)
read a set of releases. The `releases` parameter selects it: comma-separated
refs, which may also be spread over repeated parameters. A selection
*replaces* the default set, so selecting only releases you may not see shows
nothing.

| `releases` | What you get |
|---|---|
| absent | The default set: the published releases you may see. A superseded release contributes nothing. |
| `release:1,release:2`, or repeated | Those you may see: published and superseded releases, each superseded one flagged `superseded: true` in the response's `releases`. A staff account may also select a staged release that has an `internal` approval. Visitors never see one. |
| A ref that names nothing; a withdrawn, draft or unapproved release; for a visitor also an embargoed or staged one | Dropped without a word: `resolve` answers `unmatched`, the entity page and the relationship routes answer 404, and `network` drops every seed and answers an empty network. |
| A malformed ref, an empty `releases=`, or more than 50 distinct refs | 400 ([Errors](#errors)). |

A value that is malformed or over the cap is refused, but a well-formed ref is
a selection, and is never checked against what exists.

### What you may see

Visitors, logged in or not, see releases with an effective `public_view`
approval, and a logged-in staff account sees releases with an effective
`internal` approval. An embargoed release is hidden from visitors, whatever
`public_view` says. The default set is the published releases among those,
and a selection may name others, as the table above says. An entity exists for
you only if a release in the set created it or names it in evidence. A
relationship exists for you only if a release in the set states evidence for
it. You see only what you may see; nothing says why: an entity you may not see
answers exactly as one that does not exist. A label shows only to those who
may read the release that wrote it, and otherwise the identifier does. What
you may read does not depend on what you selected. Responses differ by caller,
and carry `Vary: Authorization`.

### Entity nodes

Every entity in a response is this node, which `resolve` lists as a
candidate's `entity`, the entity page as `entity` and in `nodes`, the
relationship page in `nodes`, and `network` in `nodes` with `is_seed` added:

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

### Edges

Every relationship in a response is this edge, which `network` lists in
`edges`, the relationship page as `edge` and the entity page in
`reference_relationships`. Its figures are computed over the evidence you may
see in the selected releases that passes the request's filters: the
*covered* assertions are those with one of the requested outcomes, and the
*supporting* ones those among them whose outcome is `supported`.

| Key | Contents |
|---|---|
| `id` | The relationship's ref. |
| `type` | The relationship type's slug, from `vocabularies`. |
| `family` | The type's family, from `vocabularies`. |
| `orientation` | The type's orientation: `directed`, `role_ordered` or `symmetric`. |
| `source`, `target` | Entity refs: the stored subject and object. A symmetric pair is stored in canonical order, the lower id first. |
| `releases` | The refs of the releases whose covered evidence the response lists, sorted by id. Each has an entry in the response's `releases`. |
| `is_synthetic` | `true` when any of those releases is synthetic. |
| `qualifiers` | `{}` until identity qualifiers are recorded. |
| `shown` | Whether a covered assertion supports listing the edge under the request's filters: always `true` in a network, and `false` on the page of a relationship whose evidence has none of the requested outcomes. |
| `effect` | `activating`, `inhibiting`, `mixed`, `unknown`, `not_applicable` for a type without effect polarity, or `null` when no covered assertion supports the edge. |
| `effect_counts` | `{activating, inhibiting, unknown, not_stated}` over the covered supporting assertions. |
| `genetic_sign` | `negative`, `positive`, `mixed`, `not_stated`, `not_applicable` for a type without sign polarity, or `null` when no covered assertion supports the edge. |
| `sign_counts` | `{negative, positive, not_stated}` over the covered supporting assertions. |
| `outcome_counts` | `{supported, tested_absent, inconclusive, not_significant}` over the assertions that pass every filter but the outcome filter, so the evidence against the edge is always counted. |
| `independent_outcome_counts` | The same keys, counting independent evidence per outcome: records of one observation count once. |
| `assertion_count` | Every covered supporting assertion. |
| `evidence_count` | The independent evidence among them: records of one observation count once. |
| `distinct` | `{publications, releases, datasets}` over the covered supporting assertions. |
| `by_basis` | `{basis: n}` over the covered supporting assertions, present bases only, in the order `vocabularies` lists the evidence bases. |
| `level` | Physical family only: the strongest stored interaction level among the covered supporting assertions, `not_stated` being a level; `null` for other families and without support. |
| `level_counts` | `{direct_interaction, physical_association, association, not_stated}` for the physical family, `{}` otherwise. |
| `level_assigned` | `true` when the level rests only on openPIP's assignment from the stated assay, `false` otherwise; `null` outside the physical family or without support. |
| `tested_levels` | `{outcome: [levels, strongest first]}` of the non-supporting assertions that state a level, what the edge was tested for; `{}` otherwise. |
| `outside_filters` | How many of the relationship's visible assertions the request's filters left out. |
| `outside_outcome_counts` | The four outcome keys over those. |

An edge listed only because `outcome` names a kind other than `supported` has
`assertion_count` 0 and `effect` `null`; `outcome_counts` says which kinds its
evidence has, and `tested_levels` what was tested for.

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
| 404 | `{"detail": "Not found."}`, on every route but `status` while the typed graph is off, and for an entity or relationship that does not exist or that you may not see, which read alike. It is never a 403. |
| 406 | An `Accept` header the API cannot satisfy. |
| 429 | Over a rate limit. The body says how many seconds to wait, and so does the `Retry-After` header. |
| 503 | The request took too long to compute; narrow it before repeating it, and do not retry it unchanged. `network` and the relationship routes can answer it. |

On PostgreSQL each statement that reads relationships or evidence runs under a
5 s timeout, and a network sends no further such statement once 5 s have
passed since its first, so a request ends within about 10 s. A request that runs past either gets the 503, whose
`detail` names what to narrow: fewer seeds or layers, a lower `max_edges`, or
a smaller `page_size`. It carries no `Retry-After`, because a cancelled
statement is as likely a large request as a loaded server.

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

Three limits apply per caller, that is per IP address, or per account when
logged in: one to `resolve`, one to `network` and the relationship page, and
one that `vocabularies`, `entities/{entity_id}` and the evidence list share.
[The rate table](index.md#rate-limits) gives their numbers. They come on top
of the anonymous and logged-in limits, and a request they refuse does not
count against those. `network` also has a site-wide budget, counted from
everyone combined at twice the per-caller rate and spent only by requests
that reach the computation: a malformed or refused request spends nothing. A
request over a limit gets 429 with a `Retry-After` header, and one the budget
refuses a body that begins with "The portal's network budget is spent for the
moment." `status` has none of these limits: it reads no token, so it is
counted per IP address at the logged-in rate.

!!! note
    Per-caller limits rely on a host proxy that sets `X-Forwarded-For`.
    Without one, callers that send no such header share one limit, and a
    caller that sends one chooses its own, so the per-caller limits stop
    nobody. The per-request caps and the site-wide network budget hold either
    way. The header can also name an account's counter, so such a caller can
    use up a logged-in user's limits. The budget is site-wide only through the
    shared cache that `REDIS_URL` names; without one each worker counts
    alone, so the bound is three times the rate.

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
    "network": true,
    "relationships": true,
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
    "max_seeds": 10,
    "max_layers": 20,
    "max_context_facets": 4,
    "default_max_edges": 250,
    "max_edges": 1000,
    "candidates_per_seed": 200,
    "max_nodes": 500,
    "evidence_page_size": 50,
    "evidence_max_page_size": 200,
    "evidence_page_raw_chars": 2000000,
    "throttles": {
      "graph_read": "300/min",
      "graph_resolve": "120/min",
      "graph_network": "60/min",
      "graph_network_site": "120/min"
    }
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
    "max_seeds": 10,
    "max_layers": 20,
    "max_context_facets": 4,
    "default_max_edges": 250,
    "max_edges": 1000,
    "candidates_per_seed": 200,
    "max_nodes": 500,
    "evidence_page_size": 50,
    "evidence_max_page_size": 200,
    "evidence_page_raw_chars": 2000000,
    "throttles": {
      "graph_read": "300/min",
      "graph_resolve": "120/min",
      "graph_network": "60/min",
      "graph_network_site": "120/min"
    }
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
| `network` | Networks around chosen entities ([`network`](#get-apigraphv1network)). |
| `relationships` | Relationship pages and their evidence ([`relationships/{relationship_ref}`](#get-apigraphv1relationshipsrelationship_ref) and its [`evidence`](#get-apigraphv1relationshipsrelationship_refevidence)). |
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
| `max_seeds` | The most distinct entities `seeds` names in one `network` request. |
| `max_layers` | The most distinct layers `layers` names in one `network` request. |
| `max_context_facets` | The most `context.<facet>` parameters in one request. |
| `default_max_edges` | The edges a network holds unless `max_edges` asks for another number. |
| `max_edges` | The most edges `max_edges` may ask for. |
| `candidates_per_seed` | The most relationships ranked per seed: a seed with more has only its most covered ones ranked, and `warnings` say so. |
| `max_nodes` | The most nodes a network holds. |
| `evidence_page_size` | The rows an evidence page holds unless `page_size` asks for another number. |
| `evidence_max_page_size` | The most rows `page_size` may ask for. |
| `evidence_page_raw_chars` | The characters of readable `raw` records past which an evidence page ends early. |
| `throttles` | The rate of each limit that a served route uses, by its name: `graph_read`, `graph_resolve`, `graph_network` and `graph_network_site`. |

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
      "is_synthetic": true,
      "qualifiers": {},
      "shown": true,
      "effect": "not_applicable",
      "effect_counts": {
        "activating": 0,
        "inhibiting": 0,
        "unknown": 0,
        "not_stated": 1
      },
      "genetic_sign": "not_applicable",
      "sign_counts": {"negative": 0, "positive": 0, "not_stated": 1},
      "outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "independent_outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "assertion_count": 1,
      "evidence_count": 1,
      "distinct": {"publications": 0, "releases": 1, "datasets": 1},
      "by_basis": {"unknown": 1},
      "level": null,
      "level_counts": {},
      "level_assigned": null,
      "tested_levels": {},
      "outside_filters": 0,
      "outside_outcome_counts": {
        "supported": 0,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      }
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
| `reference_relationships` | The reference edges touching the entity, in the shared [edge shape](#edges), sorted by id: the reference relationships that a selected release states, each summarized over that evidence. `[]` when there are none. |
| `nodes` | The [entity node](#entity-nodes) of each end of `reference_relationships`, once each, sorted by numeric id. `[]` when there are no reference relationships. |
| `relationship_counts` | How many relationships of each type touch the entity, by type slug, sorted. `{}` when there are none. |
| `releases` | The [release entries](#release-entries) of the selected releases that created or name the entity, or state one of its `reference_relationships`. |

Until a later release records which release states an alias, every stored
identifier of a visible entity is listed. `url` is the identifier's address
at its authority, and is an `http` or `https` address or `null`.

`relationship_counts` count relationships with supporting evidence in the
selected releases. That agrees with the network's default, which shows a
relationship only when supporting evidence covers it. Here SYN-TF1's
relationship with SYN-G2, tested and not found, is not counted.

| Status | Body |
|---|---|
| 301 | The entity was merged into another, and you may see both. The body is empty, and `Location` names the survivor's page, with your query string kept. |
| 400 | A malformed `entity_id`, an unknown parameter or a bad `releases`. See [Errors](#errors). |
| 404 | `{"detail": "Not found."}`: no such entity, or one you may not see, a merged entity whose survivor you may not see included. |

## Networks

### GET /api/graph/v1/network

The relationships around up to ten entities, as you may see them: each seed's
relationships of the chosen layers, in the chosen direction, that evidence of
the requested outcomes covers, ranked by how much evidence covers them and
kept within the caps, with the nodes they touch. It answers 404 while the
typed graph is off.

| Parameter | Notes |
|---|---|
| `seeds` | Required. The entities to start from, as comma-separated entity refs; repeatable. A ref you may not see is dropped without a word, so a hidden seed reads like one that names nothing. |
| `layers` | The relationship types to walk, as comma-separated type slugs from `vocabularies` or `family:<name>` for every registered type of a family; repeatable. The distinct pieces are counted before a family expands. Reference types (`encodes`, `isoform_of`, or `family:reference`) are walked only when named. Unless given, every non-reference type. |
| `direction` | `out`, `in` or `both`, the default, from each seed. A directed or role-ordered relationship matches `out` from its subject and `in` from its object; a symmetric one matches from either end, whatever `direction` says. |
| `outcome` | The outcomes whose evidence covers an edge, comma-separated: `supported`, the default, `tested_absent`, `inconclusive`, `not_significant`, or `all`, which stands alone. A relationship whose visible evidence passes the other filters but has none of these outcomes is hidden, and counted in `page.hidden`. |
| `evidence_basis` | Evidence bases, comma-separated, from `vocabularies`. |
| `effect` | The stored effects, comma-separated: `activating`, `inhibiting`, `unknown` or `not_stated`. `mixed` and `not_applicable` are summaries, not stored values, so they are refused. |
| `method` | Detection methods, as comma-separated PSI-MI CURIEs such as `MI:0018`. A well-formed CURIE no assertion names matches nothing. |
| `level` | Interaction levels, comma-separated, from `vocabularies`. The filter applies to the physical family only. |
| `score` | `score_type:min:max`, with the type's slug from `vocabularies` and a bound left empty being open: `synthetic:confidence:0.9:` selects the assertions scoring at least 0.9 on that type. Each range applies to its own score type, and types are never pooled. Repeat it for another score type, once per type, rather than joining values with a comma: a value that holds a comma is refused. `synthetic:rank::` selects every assertion with a finite score of that type. |
| `context.host_taxon`, `context.tissue`, `context.cell_type`, `context.cell_line`, `context.condition`, `context.perturbation`, `context.compartment`, `context.assay_system`, `context.developmental_stage`, `context.timepoint`, `context.genome_assembly`, `context.other` | A stated context, by facet, at most four of them per request. `context.host_taxon` takes a registered taxid; the others a PSI-MI or ECO CURIE, which matches the term, or any other text, which matches the value as written. An assertion that states no such facet never matches. |
| `taxon` | An NCBI taxid, from `vocabularies`. Only edges whose partner, the end that is not the seed, is an entity of this taxon. The seed is never dropped for its own taxon. |
| `include_partner_edges` | `true` lists the edges among the partners too, within `max_edges` and adding no node. `false`, the default, lists the seeds' edges only. |
| `max_edges` | The most edges to list, a whole number; the default unless given. |
| `releases` | The releases to read ([Selecting releases](#selecting-releases)). |

`seeds` names at most 10 entities, `layers` at most 20 layers and `context.*`
at most 4 facets; a network holds at most 1,000 edges (`max_edges`, 250 unless
asked) and 500 nodes; each seed's 200 most covered relationships are ranked. A
value over a cap gets 400 ([Errors](#errors)); the node cap and the per-seed
limit cut instead, and say so in `page` and `warnings`. A `context.*` value is
at most 200 characters and holds no control character.

Each visible seed's candidates are the relationships of the layers that match
the direction and whose visible evidence, under the other filters, has one of
the requested outcomes. Only each seed's most covered relationships are
ranked, ties to the lowest id: a seed with more is named in `warnings` as
`seed_truncated`, and counted in `page.omitted.candidates`. The
candidates of every seed, each once, are ranked by how many assertions cover
them, the most covered first and ties by ascending id, and walked in that
order. An edge is kept while fewer than `max_edges` edges are kept and the
network stays within the node cap. The first edge refused for `max_edges`
ends the walk; an edge refused for the node cap is skipped and the walk goes
on, so every edge between two shown nodes that the edge budget allows is
shown. A seed is a node whether or not it has an edge. Two requests with the
same parameters on the same data give the same edges in the same order.

With `include_partner_edges=true`, once the seeds' edges are ranked and fewer
than `max_edges` were kept, the edges among the partners with covered evidence
are ranked the same way and appended within `max_edges`. They add no node,
`direction` does not apply to them, and `taxon` applies to both of their
ends.

`GET /api/graph/v1/network?seeds=entity:7` on the demonstration, the network
around the gene SYN-TF1 with the default set: three regulation edges, the
relationship with SYN-G2, which v2 tested and did not find, counted in
`page.hidden` and named in `warnings`, and no reference edge, since no
reference layer was named:

```json
{
  "api_version": "graph/v1",
  "query": {
    "seeds": ["entity:7"],
    "layers": null,
    "direction": null,
    "outcome": null,
    "evidence_basis": null,
    "effect": null,
    "method": null,
    "level": null,
    "score": null,
    "context": {},
    "taxon": null,
    "include_partner_edges": null,
    "max_edges": null,
    "releases": null
  },
  "nodes": [
    {
      "id": "entity:1",
      "type": "gene",
      "label": "SYN-G1",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {"namespace": "synthetic_gene", "value": "SYN-G1"},
      "is_seed": false
    },
    {
      "id": "entity:4",
      "type": "gene",
      "label": "SYN-G4",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {"namespace": "synthetic_gene", "value": "SYN-G4"},
      "is_seed": false
    },
    {
      "id": "entity:7",
      "type": "gene",
      "label": "SYN-TF1",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {
        "namespace": "synthetic_gene",
        "value": "SYN-TF1"
      },
      "is_seed": true
    }
  ],
  "edges": [
    {
      "id": "relationship:5",
      "type": "transcriptional_regulation",
      "family": "regulation",
      "orientation": "directed",
      "source": "entity:1",
      "target": "entity:7",
      "releases": ["release:2"],
      "is_synthetic": true,
      "qualifiers": {},
      "shown": true,
      "effect": "inhibiting",
      "effect_counts": {
        "activating": 0,
        "inhibiting": 1,
        "unknown": 0,
        "not_stated": 0
      },
      "genetic_sign": "not_applicable",
      "sign_counts": {"negative": 0, "positive": 0, "not_stated": 1},
      "outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "independent_outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "assertion_count": 1,
      "evidence_count": 1,
      "distinct": {"publications": 0, "releases": 1, "datasets": 1},
      "by_basis": {"experimental": 1},
      "level": null,
      "level_counts": {},
      "level_assigned": null,
      "tested_levels": {},
      "outside_filters": 0,
      "outside_outcome_counts": {
        "supported": 0,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      }
    },
    {
      "id": "relationship:6",
      "type": "transcriptional_regulation",
      "family": "regulation",
      "orientation": "directed",
      "source": "entity:7",
      "target": "entity:1",
      "releases": ["release:2"],
      "is_synthetic": true,
      "qualifiers": {},
      "shown": true,
      "effect": "inhibiting",
      "effect_counts": {
        "activating": 0,
        "inhibiting": 1,
        "unknown": 0,
        "not_stated": 0
      },
      "genetic_sign": "not_applicable",
      "sign_counts": {"negative": 0, "positive": 0, "not_stated": 1},
      "outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "independent_outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "assertion_count": 1,
      "evidence_count": 1,
      "distinct": {"publications": 0, "releases": 1, "datasets": 1},
      "by_basis": {"experimental": 1},
      "level": null,
      "level_counts": {},
      "level_assigned": null,
      "tested_levels": {},
      "outside_filters": 0,
      "outside_outcome_counts": {
        "supported": 0,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      }
    },
    {
      "id": "relationship:8",
      "type": "transcriptional_regulation",
      "family": "regulation",
      "orientation": "directed",
      "source": "entity:7",
      "target": "entity:4",
      "releases": ["release:2"],
      "is_synthetic": true,
      "qualifiers": {},
      "shown": true,
      "effect": "unknown",
      "effect_counts": {
        "activating": 0,
        "inhibiting": 0,
        "unknown": 1,
        "not_stated": 0
      },
      "genetic_sign": "not_applicable",
      "sign_counts": {"negative": 0, "positive": 0, "not_stated": 1},
      "outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "independent_outcome_counts": {
        "supported": 1,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      },
      "assertion_count": 1,
      "evidence_count": 1,
      "distinct": {"publications": 0, "releases": 1, "datasets": 1},
      "by_basis": {"experimental": 1},
      "level": null,
      "level_counts": {},
      "level_assigned": null,
      "tested_levels": {},
      "outside_filters": 0,
      "outside_outcome_counts": {
        "supported": 0,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      }
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
  "page": {
    "truncated": false,
    "max_edges": 250,
    "omitted": {},
    "hidden": {
      "supported": 0,
      "tested_absent": 1,
      "inconclusive": 0,
      "not_significant": 0
    }
  },
  "warnings": [
    {
      "code": "tested_absent_hidden",
      "detail": "1 hidden relationships have tested-absent evidence; outcome=tested_absent or outcome=all shows them."
    }
  ]
}
```

| Key | Contents |
|---|---|
| `api_version` | `graph/v1`. |
| `query` | The request as read, its keys in the order of the parameter table, never reduced to what you may see. `seeds`, the distinct refs named, sorted by id. `layers`, the distinct layers named, in order of first appearance, or `null`. `direction`, as given, or `null`. `outcome`, the distinct outcomes named, in order of first appearance, or `null`. `evidence_basis`, the distinct bases named, in order, or `null`. `effect`, the distinct effects named, in order, or `null`. `method`, the distinct methods named, in order, each as a normalized CURIE, or `null`. `level`, the distinct levels named, in order, or `null`. `score`, the values as given, in order, or `null`. `context`, `{facet: value}` of the facets given, `{}` when none. `taxon`, an integer or `null`. `include_partner_edges`, a boolean or `null`. `max_edges`, an integer or `null`. `releases`, the distinct refs named, sorted by id, or `null`. |
| `nodes` | One node per entity that is a seed you may see or an end of a listed edge, sorted by numeric id, each as below. `[]` when no seed is visible. |
| `edges` | The kept edges in rank order, each in the shared [edge shape](#edges), every one with `shown` `true`. `[]` when none. |
| `releases` | The [release entries](#release-entries) of every release an edge lists. |
| `page` | `{truncated, max_edges, omitted, hidden}`. `max_edges` is the cap in force. `omitted` is `{}`, or holds `candidates`, how many lookups the per-seed limit cut (one per truncated seed, one for the partner lookup), and `edges`, how many ranked candidates a cap refused; `truncated` is `true` when `omitted` is not empty. `hidden`: how many relationships of the seeds, per outcome, have evidence passing the other filters but none of the requested outcomes; exact, not limited to the ranked candidates. Always the four outcome keys. |
| `warnings` | Each `{code, detail}`, in this order: one `seed_truncated` per truncated seed, `partner_edges_truncated`, `edges_truncated` when `max_edges` refused edges, `nodes_truncated` when the node cap did, then one `<outcome>_hidden` per non-zero `page.hidden` key. The two caps' counts sum to `page.omitted.edges`. `[]` when nothing was cut or hidden. |

A network node is the [entity node](#entity-nodes) with one key added:

| `nodes[]` key | Contents |
|---|---|
| `id` | The entity node's `id`. |
| `type` | The entity node's `type`. |
| `label` | The entity node's `label`. Never `null`. |
| `taxon` | The entity node's `taxon`, or `null`. |
| `status` | The entity node's `status`, a string or `null`. |
| `primary_identifier` | The entity node's `primary_identifier`, or `null`. |
| `is_seed` | `true` for a seed you named and may see, `false` for a partner. |

A visitor sees only edges whose evidence a release approved for public view
states, so on the demonstration, whose approvals are internal, a visitor's
network is empty until an operator records `public_view`.

| Status | Body |
|---|---|
| 400 | A missing or malformed `seeds`, more seeds, layers or context facets than the caps allow, an unknown layer, value or score type, a score type given twice, a `context.*` value over the cap or holding a control character, a bad `max_edges`, a parameter given twice that takes one value, an unknown parameter or a bad `releases`. See [Errors](#errors). |
| 401 | An invalid or expired token. |
| 404 | `{"detail": "Not found."}` while the typed graph is off. |
| 406 | An `Accept` header the API cannot satisfy. |
| 429 | Over your own limit, or over the portal's network budget, whose body begins with "The portal's network budget is spent for the moment." ([Rate limits](#rate-limits)). |
| 503 | The request took too long to compute ([Errors](#errors)). |

## Relationships

### GET /api/graph/v1/relationships/{relationship_ref}

One relationship, as you may see it: its edge, summarized under the same
evidence-level filters as `network`, so a panel and the network it was opened
from agree; what its type means; its two ends; and its evidence counted per
release. It answers 404 while the typed graph is off.

| Parameter | Notes |
|---|---|
| `relationship_ref` | In the path: the relationship's ref, as in `relationships/relationship:9`. Any other value gets 400, unless it is empty or holds a slash, percent-encoded or not: such a path matches no route, and gets the server's own 404, which is not JSON. |
| `outcome` | As on [`network`](#get-apigraphv1network): the outcomes whose evidence covers the edge, `supported` unless given. A relationship whose visible evidence has none of them still answers, with `edge.shown` `false`. |
| `evidence_basis` | As on `network`. |
| `effect` | As on `network`. |
| `method` | As on `network`. |
| `level` | As on `network`. |
| `score` | As on `network`: once per score type. |
| `context.host_taxon`, `context.tissue`, `context.cell_type`, `context.cell_line`, `context.condition`, `context.perturbation`, `context.compartment`, `context.assay_system`, `context.developmental_stage`, `context.timepoint`, `context.genome_assembly`, `context.other` | As on `network`: at most four of them per request. |
| `releases` | The releases to read ([Selecting releases](#selecting-releases)). |

A relationship exists for you when a release in the set states any evidence
for it, whatever its outcome. One whose only visible evidence is tested-absent,
inconclusive or not significant answers 200: under the default `outcome` its
`edge` has `shown` `false`, `effect` `null` and its counts in
`outcome_counts`, and with an `outcome` that covers that evidence `shown` is
`true`. One with no visible evidence answers 404, exactly as one that does not
exist.

`GET /api/graph/v1/relationships/relationship:9` on the demonstration, the
page of SYN-TF2 → SYN-G2, which v2 states twice with opposite effects, so the
edge reads `mixed`:

```json
{
  "api_version": "graph/v1",
  "query": {
    "relationship_ref": "relationship:9",
    "outcome": null,
    "evidence_basis": null,
    "effect": null,
    "method": null,
    "level": null,
    "score": null,
    "context": {},
    "releases": null
  },
  "edge": {
    "id": "relationship:9",
    "type": "transcriptional_regulation",
    "family": "regulation",
    "orientation": "directed",
    "source": "entity:8",
    "target": "entity:2",
    "releases": ["release:2"],
    "is_synthetic": true,
    "qualifiers": {},
    "shown": true,
    "effect": "mixed",
    "effect_counts": {
      "activating": 1,
      "inhibiting": 1,
      "unknown": 0,
      "not_stated": 0
    },
    "genetic_sign": "not_applicable",
    "sign_counts": {"negative": 0, "positive": 0, "not_stated": 2},
    "outcome_counts": {
      "supported": 2,
      "tested_absent": 0,
      "inconclusive": 0,
      "not_significant": 0
    },
    "independent_outcome_counts": {
      "supported": 2,
      "tested_absent": 0,
      "inconclusive": 0,
      "not_significant": 0
    },
    "assertion_count": 2,
    "evidence_count": 2,
    "distinct": {"publications": 2, "releases": 1, "datasets": 1},
    "by_basis": {"experimental": 2},
    "level": null,
    "level_counts": {},
    "level_assigned": null,
    "tested_levels": {},
    "outside_filters": 0,
    "outside_outcome_counts": {
      "supported": 0,
      "tested_absent": 0,
      "inconclusive": 0,
      "not_significant": 0
    }
  },
  "definition": "The regulator, a gene or a protein as the source states it, controls the transcription of the target gene. Directed: A regulating B and B regulating A are different claims. Each assertion states the effect it reports (activating, inhibiting or unknown), or states none. It does not claim that the regulator binds the target's DNA, a binding motif alone is never evidence for it, and a regulator stated as a gene is never upgraded to its protein. A gene may regulate itself (autoregulation).",
  "nodes": [
    {
      "id": "entity:2",
      "type": "gene",
      "label": "SYN-G2",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {"namespace": "synthetic_gene", "value": "SYN-G2"}
    },
    {
      "id": "entity:8",
      "type": "gene",
      "label": "SYN-TF2",
      "taxon": 32630,
      "status": "provisional",
      "primary_identifier": {
        "namespace": "synthetic_gene",
        "value": "SYN-TF2"
      }
    }
  ],
  "by_release": [
    {
      "release": "release:2",
      "outcome_counts": {
        "supported": 2,
        "tested_absent": 0,
        "inconclusive": 0,
        "not_significant": 0
      }
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
  ]
}
```

| Key | Contents |
|---|---|
| `api_version` | `graph/v1`. |
| `query` | The request as read, never reduced to what you may see: `relationship_ref`, as given. `outcome`, the distinct outcomes named, in order of first appearance, or `null`. `evidence_basis`, the distinct bases named, in order, or `null`. `effect`, the distinct effects named, in order, or `null`. `method`, the distinct methods named, in order, each as a normalized CURIE, or `null`. `level`, the distinct levels named, in order, or `null`. `score`, the values as given, in order, or `null`. `context`, `{facet: value}` of the facets given, `{}` when none. `releases`, the distinct refs named, sorted by id, or `null`. |
| `edge` | The relationship in the shared [edge shape](#edges), summarized under the request's filters. `shown` is `false` when no covered assertion has a requested outcome. |
| `definition` | What the relationship type means, as `vocabularies` gives it. A string, possibly `""`. |
| `nodes` | The [entity node](#entity-nodes) of each end, sorted by numeric id: one node for a relationship of an entity with itself. |
| `by_release` | One entry per release that states evidence passing the filters other than `outcome`, sorted by release id, each as below. Their counts sum to `edge.outcome_counts`. |
| `by_release[].release` | The release's ref. Each has an entry in `releases`. |
| `by_release[].outcome_counts` | `{supported, tested_absent, inconclusive, not_significant}` of that release's assertions that pass the other filters. |
| `releases` | The [release entries](#release-entries) of the releases in `by_release`. |

| Status | Body |
|---|---|
| 400 | A malformed `relationship_ref`, an unknown parameter or value, a bad `score` or `context.*`, or a bad `releases`. See [Errors](#errors). |
| 401 | An invalid or expired token. |
| 404 | `{"detail": "Not found."}` while the typed graph is off, and for a relationship that does not exist or that you may not see, which read alike. |
| 406 | An `Accept` header the API cannot satisfy. |
| 429 | Over your own limit: the page shares `network`'s per-caller limit ([Rate limits](#rate-limits)). |
| 503 | The request took too long to compute ([Errors](#errors)). |

### GET /api/graph/v1/relationships/{relationship_ref}/evidence

The evidence one relationship has, row by row with its provenance, as you may
see it, in pages. Every outcome is listed unless `outcome` narrows it, so the
evidence against an edge is read beside the evidence for it. It answers 404
while the typed graph is off.

| Parameter | Notes |
|---|---|
| `relationship_ref` | In the path, as on the [relationship page](#get-apigraphv1relationshipsrelationship_ref). |
| `outcome` | As on [`network`](#get-apigraphv1network), but every outcome unless given. |
| `evidence_basis` | As on `network`. |
| `effect` | As on `network`. |
| `method` | As on `network`. |
| `level` | As on `network`. |
| `score` | As on `network`: once per score type. |
| `context.host_taxon`, `context.tissue`, `context.cell_type`, `context.cell_line`, `context.condition`, `context.perturbation`, `context.compartment`, `context.assay_system`, `context.developmental_stage`, `context.timepoint`, `context.genome_assembly`, `context.other` | As on `network`: at most four of them per request. |
| `after` | The ref of the row the page starts after, as in `after=evidence:36`: the page holds the rows with a greater id. Pass the previous page's `page.next`. It is a position, never checked against what exists. |
| `page_size` | Rows per page, a whole number; the default unless given. |
| `releases` | The releases to read ([Selecting releases](#selecting-releases)). |

Rows come in id order, and ids only grow, so a page reads the same however
often it is asked for, and walking `after` from page to page misses nothing.
Pages are bounded twice: an evidence page holds 50 rows unless asked, at most
200, and ends early when its raw records pass 2,000,000 characters, `page.next`
then naming the last row delivered. So a short page is not the last page:
follow `next` until it is `null`. A single record larger than the cap is still
delivered, alone.

`record.raw` is the record as the adapter stored it, an object of the file's
columns, for a staff account or when the row's release has an effective
`public_view` approval, and `null` otherwise, or when the release stores no raw
rows. Term labels show when the portal's own vocabulary gives them, or when
you may read the release that labelled the term, whatever the request
selects; otherwise they are `null`. The CURIE is always given.

`GET /api/graph/v1/relationships/relationship:9/evidence` on the
demonstration, the two rows v2 states for SYN-TF2 → SYN-G2, one per paper:

```json
{
  "api_version": "graph/v1",
  "query": {
    "relationship_ref": "relationship:9",
    "outcome": null,
    "evidence_basis": null,
    "effect": null,
    "method": null,
    "level": null,
    "score": null,
    "context": {},
    "after": null,
    "page_size": null,
    "releases": null
  },
  "evidence": [
    {
      "id": "evidence:21",
      "release": "release:2",
      "record": {
        "key": "syn-03",
        "line_number": 4,
        "raw": {
          "effect": "activating",
          "outcome": "supported",
          "directed": "",
          "object_id": "SYN-G2",
          "record_id": "syn-03",
          "subject_id": "SYN-TF2",
          "object_type": "gene",
          "object_label": "SYN-G2",
          "object_taxon": "32630",
          "publications": "ref:syn-paper-1",
          "subject_type": "gene",
          "subject_label": "SYN-TF2",
          "subject_taxon": "32630",
          "evidence_basis": "experimental",
          "assertion_method": "manual_assertion",
          "interaction_type": "",
          "object_namespace": "synthetic_gene",
          "relationship_type": "transcriptional_regulation",
          "subject_namespace": "synthetic_gene",
          "score:synthetic:rank": "",
          "score:synthetic:confidence": ""
        }
      },
      "outcome": "supported",
      "evidence_basis": "experimental",
      "assertion_method": "manual_assertion",
      "eco_term": null,
      "effect": "activating",
      "effect_as_stated": "",
      "genetic_sign": null,
      "interaction_type": null,
      "interaction_level": null,
      "relationship_as_stated": "",
      "methods": [],
      "scores": [],
      "contexts": [],
      "participants": [
        {
          "side": "subject",
          "entity": "entity:8",
          "stated_entity": null,
          "identifier_as_stated": "SYN-TF2",
          "resolution_basis": "primary"
        },
        {
          "side": "object",
          "entity": "entity:2",
          "stated_entity": null,
          "identifier_as_stated": "SYN-G2",
          "resolution_basis": "primary"
        }
      ],
      "qualifiers": {},
      "evidence_group": "",
      "publications": [
        {
          "role": "reported_in",
          "publication": {
            "pmid": null,
            "doi": null,
            "pmcid": null,
            "imex_id": null,
            "local_key": "syn-paper-1",
            "title": "SYN demonstration paper 1 (synthetic: not a real publication)",
            "authors": "",
            "journal": "",
            "year": 2026,
            "url": null,
            "is_synthetic": true
          }
        }
      ]
    },
    {
      "id": "evidence:22",
      "release": "release:2",
      "record": {
        "key": "syn-04",
        "line_number": 5,
        "raw": {
          "effect": "inhibiting",
          "outcome": "supported",
          "directed": "",
          "object_id": "SYN-G2",
          "record_id": "syn-04",
          "subject_id": "SYN-TF2",
          "object_type": "gene",
          "object_label": "SYN-G2",
          "object_taxon": "32630",
          "publications": "ref:syn-paper-2",
          "subject_type": "gene",
          "subject_label": "SYN-TF2",
          "subject_taxon": "32630",
          "evidence_basis": "experimental",
          "assertion_method": "manual_assertion",
          "interaction_type": "",
          "object_namespace": "synthetic_gene",
          "relationship_type": "transcriptional_regulation",
          "subject_namespace": "synthetic_gene",
          "score:synthetic:rank": "",
          "score:synthetic:confidence": ""
        }
      },
      "outcome": "supported",
      "evidence_basis": "experimental",
      "assertion_method": "manual_assertion",
      "eco_term": null,
      "effect": "inhibiting",
      "effect_as_stated": "",
      "genetic_sign": null,
      "interaction_type": null,
      "interaction_level": null,
      "relationship_as_stated": "",
      "methods": [],
      "scores": [],
      "contexts": [],
      "participants": [
        {
          "side": "subject",
          "entity": "entity:8",
          "stated_entity": null,
          "identifier_as_stated": "SYN-TF2",
          "resolution_basis": "primary"
        },
        {
          "side": "object",
          "entity": "entity:2",
          "stated_entity": null,
          "identifier_as_stated": "SYN-G2",
          "resolution_basis": "primary"
        }
      ],
      "qualifiers": {},
      "evidence_group": "",
      "publications": [
        {
          "role": "reported_in",
          "publication": {
            "pmid": null,
            "doi": null,
            "pmcid": null,
            "imex_id": null,
            "local_key": "syn-paper-2",
            "title": "SYN demonstration paper 2 (synthetic: not a real publication)",
            "authors": "",
            "journal": "",
            "year": 2026,
            "url": null,
            "is_synthetic": true
          }
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
  "page": {"page_size": 50, "next": null}
}
```

| Key | Contents |
|---|---|
| `api_version` | `graph/v1`. |
| `query` | The request as read, never reduced to what you may see: `relationship_ref`, as given. `outcome`, the distinct outcomes named, in order of first appearance, or `null`. `evidence_basis`, the distinct bases named, in order, or `null`. `effect`, the distinct effects named, in order, or `null`. `method`, the distinct methods named, in order, each as a normalized CURIE, or `null`. `level`, the distinct levels named, in order, or `null`. `score`, the values as given, in order, or `null`. `context`, `{facet: value}` of the facets given, `{}` when none. `after`, as given, or `null`. `page_size`, as given, or `null`. `releases`, the distinct refs named, sorted by id, or `null`. |
| `evidence` | The page's rows, in id order, each as below. `[]` when no row remains. |
| `evidence[].id` | The assertion's ref, as in `evidence:36`. |
| `evidence[].release` | The ref of the release that states the row. Each has an entry in `releases`. |
| `evidence[].record` | `{key, line_number, raw}`, the source record the row came from, or `null` for a row with no stored record. |
| `evidence[].record.key` | The record's key in its file. |
| `evidence[].record.line_number` | Its line in the file. |
| `evidence[].record.raw` | The record as the adapter stored it, an object of the file's columns as strings, when you are staff or the release has an effective `public_view` approval; otherwise `null`, and `null` when the release stores no raw rows. |
| `evidence[].outcome` | `supported`, `tested_absent`, `inconclusive` or `not_significant`. |
| `evidence[].evidence_basis` | An evidence basis, from `vocabularies`. |
| `evidence[].assertion_method` | An assertion method, from `vocabularies`. |
| `evidence[].eco_term` | `{curie, label}` of the ECO term, or `null`. |
| `evidence[].eco_term.curie` | The term's CURIE. |
| `evidence[].eco_term.label` | The term's label, when you may read the release that labelled it; otherwise `null`. |
| `evidence[].effect` | `activating`, `inhibiting`, `unknown`, or `null` when the row states none. |
| `evidence[].effect_as_stated` | The source's words for the effect, possibly `""`. |
| `evidence[].genetic_sign` | `negative`, `positive`, or `null`. |
| `evidence[].interaction_type` | `{curie, label}` of the PSI-MI interaction type, or `null`. |
| `evidence[].interaction_type.curie` | The term's CURIE. |
| `evidence[].interaction_type.label` | Its label, when you may read the release that labelled it; otherwise `null`. |
| `evidence[].interaction_level` | An interaction level, from `vocabularies`, or `null` outside the physical family. |
| `evidence[].relationship_as_stated` | The source's words for the relationship, possibly `""`. |
| `evidence[].methods` | The row's method terms, in id order, each as below. `[]` when none. |
| `evidence[].methods[].role` | `detection`, `participant_identification` or `evidence_code`. |
| `evidence[].methods[].curie` | The term's CURIE. |
| `evidence[].methods[].label` | Its label, when you may read the release that labelled it; otherwise `null`. |
| `evidence[].scores` | The row's scores, in id order, each as below. `[]` when none. |
| `evidence[].scores[].type` | The score type's slug, from `vocabularies`. |
| `evidence[].scores[].label` | The score type's label. |
| `evidence[].scores[].value` | The score as a finite number, or `null` when the source's value is not one. |
| `evidence[].scores[].raw_value` | The source's text. |
| `evidence[].scores[].value_kind` | The score type's kind of value, from `vocabularies`. |
| `evidence[].scores[].direction` | The score type's direction, from `vocabularies`. |
| `evidence[].scores[].unit` | The score type's unit, or `null`. |
| `evidence[].contexts` | The row's stated contexts, in id order, each as below. `[]` when none. |
| `evidence[].contexts[].facet` | A context facet, from `vocabularies`. |
| `evidence[].contexts[].term` | `{curie, label}` of the term the context names, or `null`. |
| `evidence[].contexts[].term.curie` | The term's CURIE. |
| `evidence[].contexts[].term.label` | Its label, when you may read the release that labelled it; otherwise `null`. |
| `evidence[].contexts[].taxon` | An NCBI taxid, for `host_taxon`, or `null`. |
| `evidence[].contexts[].label` | The source's label for the context, possibly `""`. |
| `evidence[].contexts[].raw_value` | The value as the source wrote it. |
| `evidence[].participants` | The row's two participants, the subject first, each as below. |
| `evidence[].participants[].side` | `subject` or `object`. |
| `evidence[].participants[].entity` | The entity's ref. |
| `evidence[].participants[].stated_entity` | The ref of the isoform the source named, when the row's entity is its protein entry and you may see the isoform; otherwise `null`. |
| `evidence[].participants[].identifier_as_stated` | The identifier as the source wrote it. |
| `evidence[].participants[].resolution_basis` | How the identifier resolved: `primary`, `xref_unique`, `alias_unique`, `isoform_entry`, `source_local` or `curated`. |
| `evidence[].qualifiers` | The stored object: `{}`, or the `threshold` a not-significant result failed. |
| `evidence[].evidence_group` | The group of rows the source states as one observation, possibly `""`. |
| `evidence[].publications` | The row's publications, in id order, each as below. `[]` when none. |
| `evidence[].publications[].role` | `reported_in`, `method_reference` or `cited`. |
| `evidence[].publications[].publication` | The publication, as below. Its descriptive fields are as the portal first recorded them for that paper. |
| `evidence[].publications[].publication.pmid` | The PubMed id, or `null`. |
| `evidence[].publications[].publication.doi` | The DOI, or `null`. |
| `evidence[].publications[].publication.pmcid` | The PubMed Central id, or `null`. |
| `evidence[].publications[].publication.imex_id` | The IMEx id, or `null`. |
| `evidence[].publications[].publication.local_key` | A source's own key for a paper that has no other identifier, or `null`. |
| `evidence[].publications[].publication.title` | The title, possibly `""`. |
| `evidence[].publications[].publication.authors` | The authors, possibly `""`. |
| `evidence[].publications[].publication.journal` | The journal, possibly `""`. |
| `evidence[].publications[].publication.year` | An integer, or `null`. |
| `evidence[].publications[].publication.url` | An `http` or `https` address, or `null`. |
| `evidence[].publications[].publication.is_synthetic` | `true` for a made-up paper. |
| `releases` | The [release entries](#release-entries) of the releases the page's rows name. |
| `page` | `{page_size, next}`: `page_size` is the row cap in force. `next` is the ref of the page's last row when more rows follow, or `null`. A page whose every row's release was withdrawn while it was read holds none; when more rows follow, its `next` names the last row it read. |

| Status | Body |
|---|---|
| 400 | A malformed `relationship_ref`, an unknown parameter or value, a bad `score` or `context.*`, a bad `after`, a `page_size` that is not a whole number within the cap, or a bad `releases`. See [Errors](#errors). |
| 401 | An invalid or expired token. |
| 404 | `{"detail": "Not found."}` while the typed graph is off, and for a relationship that does not exist or that you may not see, which read alike. |
| 406 | An `Accept` header the API cannot satisfy. |
| 429 | Over the limit that `vocabularies` and `entities/{entity_id}` share ([Rate limits](#rate-limits)). |
| 503 | The request took too long to compute ([Errors](#errors)). |
