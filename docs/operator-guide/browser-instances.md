# Browser instances

A browser instance is one entry of the portal's gallery, and will have its
own page at `/browsers/<slug>`. The portal has neither the gallery nor those
pages yet: for now, an admin creates and edits instances in the Django admin
(**Browser instances**), and the typed graph's API serves them
([Browser instances](../developer/api/typed-graph.md#browser-instances)).

An instance holds its own title, text, citation and display settings, and for
typed data the releases it reads. It holds no data of its own, and it never
widens what anyone may see: the release gate still decides that
([Typed releases](typed-releases.md)).

Everything here needs `TYPED_GRAPH_ENABLED` on
([Configuration reference](configuration.md#backend-variables)). While it is
off, the Django admin shows no instance, and refuses every page of them, to
superusers too.

## Kinds

| Kind | In the admin | What it shows |
|---|---|---|
| `typed` | Typed data | The releases selected below it. |
| `portal_ppi` | This portal's search | This portal's own protein-interaction search, at `/search`. A portal has one. |
| `external` | External portal | Another portal, at its URL. It needs an https URL, and no other kind has one. |
| `metadata_only` | Citation card | Its header citation, with no data. |

## Status and availability

An admin stores `status`: `planned`, `available` or `retired`. What the
instance can show a reader, its availability, is worked out for each reader
on each request and never stored, so a change to a release shows on the next
request. It takes the first row that applies:

| Stored `status` | `kind` | Availability |
|---|---|---|
| `retired` | any | `retired` |
| `planned` | any | `planned` |
| `available` | `external` | `external` |
| `available` | `portal_ppi` | `live` |
| `available` | `metadata_only` | `metadata_only` |
| `available` | `typed` | By its selected releases, as below. |

An available typed instance is `live` only while it selects at least one
release and every selected release, of either role, is published or
superseded and visible to the reader, so an instance pinned to v1 stays
`live` after v2 supersedes it. Otherwise it is `metadata_only`, which a
client shows as "data unavailable". Withdrawing a release or revoking its
approval changes this on the next request.

| Selected release | Visitor | Staff |
|---|---|---|
| Published, with `internal` and `public_view` in effect | `live` | `live` |
| Published, with `internal` only | `metadata_only` | `live` |
| Published under embargo | `metadata_only` | `live` |
| Superseded, with both in effect | `live` | `live` |
| Staged | `metadata_only` | `metadata_only` |
| Draft, withdrawn or discarded | `metadata_only` | `metadata_only` |
| `internal` revoked | `metadata_only` | `metadata_only` |
| `public_view` revoked | `metadata_only` | `live` |
| Due diligence reopened, on real data | `metadata_only` | `metadata_only` |
| Two releases, one hidden from you | `metadata_only` | `metadata_only` |
| None selected | `metadata_only` | `metadata_only` |

A selected release that is staged makes the instance `metadata_only` for
staff too, until it is published.

## Visibility

`visibility` is one of `staff`, `unlisted` and `public`. Only `public`
instances are listed for visitors, and staff list every instance.

An instance's `visibility` and `status` decide who sees the instance, not its
data. Its releases stay readable by everyone the release gate admits, through
`releases=` and, for a reachable instance, through `browser=`. To hide data,
withdraw the release or revoke its approval. `unlisted` keeps an instance out
of the gallery, but anyone who knows or guesses its slug can open it. Header
publications and an external URL are shown as stored to everyone who may see
the instance.

## Creating an instance

In the Django admin, open **Browser instances**, then **Add**.

### The fields

| Field | What it holds |
|---|---|
| `slug` | The instance's address, `/browsers/<slug>`: lower-case letters and digits in groups joined by single hyphens, at most 50 characters, such as `synthetic-regulation`. It is fixed once the instance is saved, since links keep it. |
| `title`, `short_title`, `summary` | Plain text on one line. Markup is kept as typed and shown as text, never as HTML. |
| `description`, `methods_text` | Plain text, with line breaks, at most 20,000 characters each. |
| `kind`, `status`, `visibility` | As above. A new instance starts `planned` and `staff`. |
| `external_url` | An `external` instance's https address (see [External URLs](#external-urls)). A URL typed with no scheme is taken as https. |
| `is_synthetic` | See [The synthetic flag](#the-synthetic-flag). |
| `taxa`, `primary_taxon` | The organisms covered, and the one shown first, which is one of `taxa` when both are given. Left empty, `taxa` means what the selected releases a reader may see declare. |
| `relationship_types` | The relationship types covered. Left empty, the non-reference types the selected releases a reader may see hold. |
| `entity_types` | The entity types covered, as stated here: these are never read from the releases. |
| `assays` | Detection methods, as PSI-MI terms such as `MI:0018`. The admin cannot check that a method applies. |
| `tags` | At most 10 of the [tags](#tags), as checkboxes. |
| `presentation` | Display settings, as JSON (see [Presentation](#presentation)). Empty, or `{}`, means every default. |
| `allow_external_services` | Will let a typed instance's pages send identifiers to outside services, while it is live for the reader and every selected release has an effective `external_services` approval. No page sends any yet: today the flag sets only what the API reports, in `allow_external_services` and `external_services_effective`. |
| `display_order` | The gallery's order, lower first, then by slug. |

### The citation and the selected releases

Two tables sit below the fields.

The **header citation** lists the instance's papers, each with a role and an
order. A paper cited here cannot be deleted while an instance cites it.

The **selected releases** are what a typed instance reads. Each has an order
and a role: `primary`, the study's own releases, or `context`, releases that
support them, such as a reference or mapping release. Both roles count for
availability, and both are read through `browser=`. Only a draft release can
be deleted at all, and not while an instance selects it. An instance selects
at most 50 releases. Only a typed instance selects releases.

### What an editor needs

An editor needs the instance's own Django permissions:
`browsers.add_browserinstance` to add one, `browsers.change_browserinstance`
to change one and its two tables, `browsers.view_browserinstance` to read
one, and `browsers.delete_browserinstance` to delete one. The two tables
follow the instance's permissions, and need none of their own.

The **selected releases** table lists every release by dataset, version and
status, drafts and withdrawn ones included, to anyone who may add or change
an instance, whether or not they hold `graph.view_datasetrelease`.

The search boxes need their registries' view permissions as well:
`graph.view_taxon` for `taxa` and `primary_taxon`, `graph.view_term` for
`assays`, and `graph.view_publication` for the header citation.

### What is refused

| The form says | Because |
|---|---|
| A slug is lower-case letters and digits in groups joined by single hyphens, such as synthetic-regulation. | The slug is not of that form. |
| 'new' is reserved. | The slug is one of the [reserved words](#reserved-slugs). |
| This text holds a control character. | A control character in a title, summary, description or methods text. |
| This text holds a character that reorders or breaks it. | A character that reverses the text around it, or a line separator in a line. |
| This text is at most 20,000 characters. | A description or methods text is too long. |
| An external instance needs an https URL. | An `external` instance has no URL. |
| Only an external instance has a URL. | Another kind has a URL. |
| An external URL is an https URL. | The URL is not https, or is not a URL. |
| An external URL holds no user name or password. | The URL names an account before its host. |
| Write the host in its ASCII (xn--) form. | The host holds a non-ASCII letter. |
| An external URL holds no control or formatting character. | An invisible character in the URL. |
| The primary taxon is one of the taxa. | `taxa` is stated and leaves it out. |
| An assay is a PSI-MI term, such as MI:0018. | An `assays` term is of another ontology. |
| An available typed instance selects at least one release. | A typed instance is `available`, with no release selected. |
| Only a typed instance selects releases. | Another kind has a selected release. |
| An instance selects at most 50 releases. | Too many selected releases. |
| This portal already has a portal_ppi entry, protein-interactions. | A second `portal_ppi` entry, here naming the one that exists. |
| presentation is at most 8,192 characters. | The JSON is too long. |
| presentation is not valid JSON. | The text is not JSON. |
| presentation is an object. | The JSON is not an object. |

A `presentation` value that breaks its [list](#presentation) is refused with
a sentence naming the key and the value.

## Presentation

`presentation` is a JSON object of schema v1. `{}` means every default, a key
left out takes its default, and a key not listed is refused. No value is free
text, a URL, HTML or CSS: each is a name from a list here or from a registry.
It changes nothing the typed graph's API returns; the API gives it as stored,
for the instance's pages to read once the portal has them.

| Key | What it holds |
|---|---|
| `version` | The number 1, required once any other key is given. |
| `default_layers` | At most 20 distinct names, each a registered relationship type's slug or `family:<name>` of a family that some type has. |
| `default_filters` | An object of at most these filters: `direction`, `outcome`, `evidence_basis`, `effect`, `level`. Each is valued as the network's parameter of that name, and all but the first are lists. |
| `filter_panels` | Distinct ids from these, every one unless given: `layers`, `direction`, `outcome`, `evidence_basis`, `effect`, `method`, `level`, `score`, `context`, `taxon`. |
| `layout` | One of these, the first unless given: `cose`, `cola`, `concentric`, `circle`, `grid`, `breadthfirst`. |
| `evidence_panels` | Distinct ids from these, every one unless given: `summary`, `evidence`, `publications`, `scores`, `contexts`, `record`. |
| `node_label_namespace` | A registered identifier namespace's slug, or `null`, which shows the entity's own label. |
| `accent` | One of these, the first unless given: `blue`, `teal`, `green`, `amber`, `red`, `purple`, `grey`. |

The form holds at most 8,192 characters of it.

## Tags

An instance names at most 10 tags, for the gallery's tag filter.

| Tag | The biology area |
|---|---|
| `protein_interaction` | Protein interactions. |
| `gene_regulation` | Gene regulation. |
| `rna_regulation` | RNA regulation. |
| `genetic_interaction` | Genetic interactions. |
| `signaling` | Signalling. |
| `ligand_receptor` | Ligands and receptors. |
| `protein_modification` | Protein modification. |
| `disease` | Disease. |
| `host_pathogen` | Hosts and pathogens. |
| `phenotype` | Phenotypes. |

## Reserved slugs

These words cannot be a slug, since a later page under `/browsers/` may need
them as its address: `new`, `edit`, `admin`, `api`, `all`, `search`,
`explore`, `static`, `media`, `settings`.

## External URLs

An `external` instance needs an https URL. It is shown as stored and never
fetched, so nothing checks that it answers, or says what it holds. It may hold
no user name or password, since `https://trusted.example@evil.example/` opens
`evil.example`; its host is in ASCII, so a look-alike letter cannot pass; and
it holds no control or formatting character.

## Cards with no releases

External and citation cards hold no release. Their header citation can name
only papers that a commit or the registries already hold, since the admin has
no way yet to add a paper or an assay term, and their `taxa` only taxa in the
registry. `assays` are detection methods, which the admin cannot check.

## The synthetic flag

`is_synthetic` states that an instance holds invented data, for a card with
no releases. An instance also reads as synthetic to anyone who may see a
selected release that is synthetic, so a typed instance needs the flag only
if it should read as synthetic to those who see none of its releases.

## Retiring an instance

Set `status` to `retired`, rather than deleting an instance: a retired
instance stays at its address and says so, and the links to it keep working.
An instance may be deleted, and that removes its citation and selected
releases, but no release, paper or data.

## The demonstration's rows

`graph_load_demo` records two instances once the demonstration's releases are
loaded ([Loading the synthetic demonstration](typed-releases.md#loading-the-synthetic-demonstration)):

- `synthetic-regulation`, of kind `typed`, `available` and `staff`, which
  selects v2, cites the demonstration's two papers and is synthetic;
- a `portal_ppi` entry, `available` and `staff`: the portal's first such
  entry, by `display_order` and slug, or else a new one with the slug
  `protein-interactions`.

Both are for staff only. The typed instance's data are visible to staff alone
until an operator records `public_view`, and the loader cannot know whether
this portal's `/search` serves any data, since a new hub may hold no legacy
rows. An operator makes either one `public`, and a typed instance that
visitors should see needs `public_view` on its release as well.

Run again, the loader keeps each row as it stands, an operator's edits
included, and writes nothing. It refuses, after the release lines and with
neither row written, when `synthetic-regulation` is an instance of another
kind or selects a release outside the demonstration's dataset, or when this
portal has no `portal_ppi` entry and `protein-interactions` is an instance of
another kind. A row that was deleted is created again by the next run that
reaches this step, so rather than delete one, retire it, which keeps it
listed, as `retired`, or leave its `visibility` at `staff`, which keeps it out
of a visitor's gallery. A later version of the demonstration leaves
`synthetic-regulation` selecting the release it selects.

## Rolling back

To remove this feature from a portal, keep the rows you want first, then take
the tables away before the code that reads them:

1. On a portal with instances to keep, run
   `python manage.py dumpdata browsers --indent 2 > browsers.json`.
   `loaddata` restores them once this version is deployed again.
2. Run `python manage.py migrate browsers zero` while the code is still
   deployed. From then until the next step is deployed, the two browsers
   routes, every request with `browser=`, the **Browser instances** admin and
   `graph_load_demo` answer 500, except that the admin's list shows Django's
   "Database error" page and its add page opens but cannot be saved: run both
   steps together, or turn `TYPED_GRAPH_ENABLED` off for the window.
3. Deploy the earlier version.
4. Run `python manage.py remove_stale_contenttypes --include-stale-apps`, and
   confirm only `browsers` rows.
