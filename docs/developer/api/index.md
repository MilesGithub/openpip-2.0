# REST API overview

Everything the openPIP web interface does goes through a JSON REST API, and
the same API is open to your own scripts. This section documents every
endpoint the server provides. A test in the backend suite compares this
reference with the server's URL configuration, so an endpoint cannot be added
or removed without this reference failing that test.

For bulk interaction data in a standard format, also see the
[PSICQUIC service](../psicquic.md), which speaks the protocol used by IntAct,
BioGRID and other interaction databases.

## Base URL

All paths in this reference are relative to the portal's address, so on this
portal `GET /api/search` means:

```text
{{ portal_url }}/api/search?q=TP53
```

Example responses are real, taken from the
**reference portal** at [openpip.usask.ca](https://openpip.usask.ca),
which hosts the Human Reference Interactome datasets. Another portal returns
its own data in the same shape. The [Typed graph](typed-graph.md) page is the
exception: its examples come from the synthetic demonstration.

## Trailing slashes

Most paths have **no** trailing slash. Paths are matched exactly, so
`/api/search/` returns 404. The exceptions, which **require** a trailing slash,
are written with one in this reference: `/api/schema/`, `/api/docs/`,
`/api/datasets/download/`, `/api/upload/`, and the saved views, shares and
notifications endpoints. Without the slash, the server answers with a 301
redirect to the slashed path. That is harmless for `GET`, but most HTTP
clients repeat a redirected `POST` as a `GET` without its body, so always send
`POST`, `PUT`, `PATCH` and `DELETE` to the exact path shown.

## Authentication

Public data needs no authentication. Searching, protein and dataset details,
downloads and PSICQUIC all work anonymously.

Personal endpoints (saved networks, sharing, profiles) and administrative
endpoints need a login token, sent as a header:

```text
Authorization: Bearer <access token>
```

Get tokens from [`POST /api/auth/login`](authentication.md#post-apiauthlogin).
An access token lasts **1 hour**. Use the refresh token, which lasts 7 days, to
get a new pair from
[`POST /api/auth/token/refresh`](authentication.md#post-apiauthtokenrefresh).

!!! note
    If you send an `Authorization` header with an expired or invalid token, the
    request fails with 401 even on public endpoints. Omit the header for
    anonymous requests. `GET /api/graph/v1/status` reads no token, so it
    answers either way.

Administrative endpoints additionally require the account to be a site
administrator. A request from any other account gets 403.

## Errors

Errors use HTTP status codes with a JSON body. Most bodies have a single
`detail` message:

```json
{"detail": "Not found."}
```

Validation errors name the offending fields instead:

```json
{"category_id": "No such category."}
```

| Status | Meaning |
|---|---|
| 400 | The request is invalid. The body says why. |
| 401 | No valid login token was sent and the endpoint needs one, or the token sent is invalid or expired. |
| 403 | You are logged in but not allowed to do this, usually because it is admin-only. |
| 404 | No such object, or an object you may not see. |
| 429 | Rate limit exceeded. The body says how many seconds to wait, when the limit can tell. |

The typed graph API ([Typed graph](typed-graph.md)) adds an `errors` list
naming each parameter and its allowed values, and its `network` and
relationship routes can answer 503 when a request took too long
([Typed graph](typed-graph.md#errors)).

PSICQUIC errors are plain text, not JSON. See the
[PSICQUIC page](../psicquic.md#errors).

## Rate limits

| Caller | Limit |
|---|---|
| Anonymous, per IP address | 600 requests per minute |
| Logged in, per account | 1,200 requests per minute |
| PSICQUIC, per IP address, or per account when logged in | 60 requests per minute |
| Typed graph reads, per IP address, or per account when logged in | 300 requests per minute |
| Typed graph `resolve`, per IP address, or per account when logged in | 120 requests per minute |
| Typed graph `network` and relationship pages, per IP address, or per account when logged in | 60 requests per minute |
| Typed graph `network`, from everyone combined | 120 requests per minute |
| Security-question answers, per IP address, or per account when logged in | 10 per hour |
| Security-question answers about any one email address, from everyone combined | 10 per hour |

A request over the limit gets 429.

## Pagination

Two endpoints are paginated:
[`GET /api/proteins`](proteins-and-search.md#get-apiproteins) takes `limit` and
`offset`, and the typed graph's evidence list
([`GET /api/graph/v1/relationships/{relationship_ref}/evidence`](typed-graph.md#get-apigraphv1relationshipsrelationship_refevidence))
takes `after` and `page_size`. Every other list returns all its results in one
response. Lists that could grow large are bounded: search returns the networks
for the proteins you ask for, notifications return the newest 50, user search
returns at most 10 matches, the typed graph's `resolve` returns at most 20
candidates per term, and its `network` is capped and reports what it cut.

## Cross-origin requests

The API and PSICQUIC accept browser requests from any origin (CORS). Login
tokens are sent in a header, not as cookies, so a third-party page cannot act
for a logged-in user.

## Machine-readable schema

### GET /api/schema/

An OpenAPI 3 schema generated from the code, as YAML. Add `?format=json` for
JSON. No authentication.

!!! warning "The generated schema is incomplete"
    The schema lists every path and method, but for most endpoints it does not
    describe the request parameters or the response. Those endpoints are written
    in a style the generator cannot inspect. Use this reference for request and
    response details.

### GET /api/docs/

An interactive Swagger UI page for the schema above. It loads its scripts from
the jsDelivr CDN.
