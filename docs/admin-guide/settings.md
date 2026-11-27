# Settings

Every screen under **Site** and **Pages** in the admin sidebar. Changes are
saved with the save bar; see [Saving settings](index.md#saving-settings).

## Site Identity { data-desc="Titles, site URL, logo, footer and top-bar labels." }

- **Site Title** and **Short Title**: the portal's full and short names. The
  full title is the browser tab's name. The short title appears on the home
  page and names the portal when someone installs it as an app.
- **Site URL** and **Version**: shown at the foot of the About page. The URL
  must start with `http://` or `https://`.
- **Logo**: **Upload logo** (PNG, JPEG, GIF, SVG or WebP), shown 28 pixels high
  in the top bar. Upload and removal take effect immediately.
- **Footer HTML**: the footer shown on every page.
- **Navigation & chrome text**: the top bar's link labels.
  The Browsers and Explore links, shown while the typed graph is on, keep their own labels, and the gallery, instance, explorer and entity pages take the site's default navbar style, since **Per-page navbar** does not list them.

## Appearance { data-desc="Themes, the top bar's style, and button, protein and interaction colours." }

- **Themes**: ready-made colour sets (openPIP Blue, Crimson, Forest, Purple,
  Ocean, Midnight, Sunset, Light). Choosing one fills in the colours below,
  which you can then adjust.
- **Navbar style**: **Solid**, **Gradient** or **Light**. **Primary color**
  (or **Gradient start**), **Gradient end** and **Gradient angle** set the
  colours. **Header text color** is the top bar's text on solid and gradient
  bars. **Logo color** colours the built-in openPIP mark, which is shown when
  no logo has been uploaded.
- **Per-page navbar**: a different style for individual pages, or **Site
  default**.
- **Button color**: the colour of main action buttons.
- **Node colors**: **Query Node Color** for the proteins a visitor searched
  for, and **Interactor Node Color** for their partners.
- **Edge colors**: **Published**, **Validated**, **Verified** and
  **Literature Edge**. Each interaction is drawn in the colour for its
  highest-order category: order 1 uses the Published colour, 2 Validated,
  3 Verified and 4 Literature, whatever your categories are called. Categories
  with any other order are drawn grey.

Previews of the top bar and of a small network update as you change colours.

## Home { data-desc="The home page's headline, blocks, cards and citation." }

The home page's wording: the opening headline and search box, the mission
and methods blocks, the three starting cards, the news panel and the citation
block, including the BibTeX that **Copy BibTeX** copies.

## Search { data-desc="Example searches, interaction categories, annotation tabs and filter layout." }

- **Search examples**: up to three example searches offered on the home and
  search pages. Write one protein per line, and choose how each is filtered:
  no filter, query-query or query-interactor.
- **Interaction categories**: the categories visitors can filter by. Each row
  has a **Name**, an **Order**, a **Description**, and shows the **Edge
  colour** its order gives (set under Appearance). **Save** a changed row, **✕**
  deletes a category (it is removed from every interaction; the interactions
  stay), and **+ Add** creates one. Order is a number: lower orders come first,
  and an interaction is coloured by its highest.
- **Annotation tabs**: turn off **Show Tissue Expression tab** or **Show
  Subcellular Location tab** if your organism has no such data. Turning off
  tissue expression also removes the tissue filter and tissue words in typed
  phrases.
- **Filter layout**: **Show filters as a ribbon under the navbar** moves the
  search controls from a left column into a row of headings above the
  network.
- **Network canvas**: the network's background colour, or **Follow theme**.
  Visitors can still pick their own.
- **What can be written here?** explains the phrases the home page search box
  understands.
- **Phrase examples text**: the example phrases offered under the home page
  search box.

## Downloads { data-desc="Which parts of the Downloads page are shown." }

- **Show Dataset Downloads** shows the table of datasets on the Downloads page.
- **Show Supplementary Files** shows the files published under **Files**.
- **Downloads page content**: an introduction above the table.

These switches hide parts of the page only. The files can still be
downloaded through the [API](../developer/api/datasets.md).

## About { data-desc="Your introduction at the top of the About page." }

**About page content**: your own introduction at the top of the About page.
Each dataset's section on the About page is written in its
[dataset entry](content.md#editing-a-dataset), not here.

## FAQs and Contact { data-desc="The text of the FAQ and Contact pages." }

**FAQ page content** and the **Contact** page's text. Both are rich text. The
Contact page shows only what you write here. There is no contact form.

## Accounts { data-desc="Grant or revoke admin access, and the sign-in pages' wording." }

**Admin access** grants or revokes administrator access on existing
accounts:

1. **Find a user** by username or email. Administrators are listed first.
2. Choose **Grant admin** or **Revoke admin**.

You cannot revoke your own access, so a portal always keeps at least one
administrator. Superusers, created on the server, keep their access. A
change applies to the account's actions immediately. Its top bar shows or
hides **Admin** within the hour.

**Sign in & registration text**: the wording of the sign-in, registration and
password recovery pages.
