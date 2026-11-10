# Typed releases

A release of typed data is registered and dry-run as
[Typed data format](typed-data-format.md) describes. This page describes
what follows: approving the release, committing it (importing its evidence
into the graph), publishing, withdrawing and discarding it, checking the
graph for what needs an operator, and loading the synthetic demonstration.

The typed graph's API shows each release to those who may see it
([Typed graph](../developer/api/typed-graph.md)), and the portal's pages come
later. Approvals and publishing decide what each release may show.

Everything here needs `TYPED_GRAPH_ENABLED` on
([Configuration reference](configuration.md#backend-variables)). While it is
off, every command on this page refuses before it reads a file or writes
anything.

The examples use `dc` for the production compose command, as on the
[Maintenance](maintenance.md) page:

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod'
```

Always use it on a production server. Plain `docker compose` reads only the
development file, whose backend reads the checkout's `backend/graph_files`
instead of the `graph_files` volume, so every stored release file would look
missing to it.

## The life of a release

| Status | What it means | Moved on by |
|---|---|---|
| `draft` | Registered, with its files. Its dry runs store its source records. | A commit stages it. |
| `staged` | Committed: its evidence is in the graph and its statistics are recorded, but it is in no default view. | Publishing it, discarding it back to `draft`, or withdrawing it. |
| `published` | Current: it is in the default views of those who may see it. | Publishing a release that supersedes it, or withdrawing it. |
| `superseded` | Corrected by a published release. It leaves the default views, and its rows stay, readable where it is selected. | Withdrawing it. |
| `withdrawn` | Hidden from every view and export at once, with its rows kept. Final. | Nothing. |

- `graph_import --commit` moves a draft to `staged`.
- `graph_publish_release` moves a staged release to `published`, and the
  published release it supersedes to `superseded`.
- `graph_withdraw_release` moves a staged, published or superseded release
  to `withdrawn`.
- `graph_discard_release` moves a staged release back to `draft`, without
  its evidence.

Each dry run and commit of a release is an **import run**: `running` while
it works, then `completed`, or `failed` with its error. A commit run that is
`running` or `failed` is the release's **open commit run**, which the next
commit resumes; a release has at most one. A discard marks the commit run it
gives up `abandoned`.

## Approving a release

Every approval decision is recorded for one release and one **scope**:
`internal` (staff work with the release), `public_view` (visitors may view
it), `public_bulk` (visitors may download it) or `external_services`
(its identifiers may be sent to outside services).

```text
python manage.py graph_approve --release ID \
    --scope {internal,public_view,public_bulk,external_services} \
    --decision {approved,denied} --rationale TEXT [--evidence TEXT] \
    --as USERNAME
python manage.py graph_approve --replace APPROVAL --rationale TEXT \
    [--evidence TEXT] --as USERNAME
```

| Option | Meaning |
|---|---|
| `--release ID` | The release, by its id. With `--scope` and `--decision`, unless `--replace` is given. |
| `--scope SCOPE` | `internal`, `public_view`, `public_bulk` or `external_services`. |
| `--decision DECISION` | `approved` or `denied`. |
| `--replace APPROVAL` | The id of an active decision to replace: it is revoked, and the same decision is recorded again, relying on the release's latest licence snapshot, in one transaction, so the release never lacks it. It takes no `--release`, `--scope` or `--decision`. |
| `--rationale TEXT` | Why the decision is made. Required. |
| `--evidence TEXT` | A link or text recording the evidence. An approval of a public scope needs it. |
| `--as USERNAME` | The active staff account making the decision. Required. |

```text
python manage.py graph_revoke --approval ID --reason TEXT --as USERNAME
```

| Option | Meaning |
|---|---|
| `--approval ID` | The decision, by its id. Required. |
| `--reason TEXT` | Why it is revoked. Required. |
| `--as USERNAME` | The active staff account revoking it. Required. |

On a production server:

```bash
dc exec backend python manage.py graph_approve --release 12 --scope internal \
    --decision approved \
    --rationale "Terms allow internal use; due diligence is complete." --as alice
dc exec backend python manage.py graph_revoke --approval 31 \
    --reason "The source withdrew its terms." --as alice
```

A decision relies on the release's latest licence snapshot, and is never
edited: revoking it records who revoked it, when and why, and a new
decision is a new row. A scope keeps one active decision, so a second one,
an approval or a denial, is refused until the first is revoked with
`graph_revoke` or replaced with `--replace`. Recording an approval is also
refused, with nothing written, unless:

- the release is not withdrawn;
- the release's latest licence snapshot records its terms: a licence name or
  quoted terms, with where and when they were read;
- for a release that is not synthetic, its dataset's due diligence is
  complete, its terms are not time-limited, and it was confirmed after that
  snapshot was recorded;
- the scope's prerequisite has an active approval: `public_view` and
  `external_services` need `internal`, and `public_bulk` needs
  `public_view`;
- for `public_view`, the snapshot records redistribution as permitted, and
  for `public_bulk` redistribution and modification;
- for a public scope, `--evidence` is given.

A denial needs none of these: saying no is always safe. Each command prints
the decision, its scope and release, the licence snapshot it relies on, and
whether the scope is effective for the release now, with the release's
effective scopes. A scope is **effective** while its decision is
active, its prerequisite is effective, the release is not withdrawn and, for
a release that is not synthetic, its dataset's due diligence is complete
with terms that are not time-limited; `public_bulk` also needs the latest
snapshot to permit redistribution and modification. Under an embargo only
`internal` is. Revoking `internal` therefore takes every scope that needs it
away at once.

Two refusals name the functions that record what is missing. Do it in the
Django admin, under **Typed relationship graph**
([Licence, due diligence and approval](typed-data-format.md#licence-due-diligence-and-approval)):

| The refusal names | In the Django admin |
|---|---|
| `graph.licensing.record_licence` | **Release licences**, then **Add**: record the release's terms. |
| `graph.licensing.confirm_due_diligence` | **Due diligence records**: select the dataset's record, and run the action **Confirm due diligence covers the current terms**. |

A synthetic release needs no approval to be dry-run or committed, but it
needs a licence snapshot that records terms, and an `internal` approval, to
be published.

New terms are recorded as a new licence snapshot, and the approvals that
rely on an older one stay effective. `graph_check` lists them among its
problems until each is refreshed with `graph_approve --replace`, or revoked.

## Committing a release

A commit imports the release as its latest dry run reported it: the
entities, relationships, publications and terms its mapped records need,
and each mapped record's evidence. It then stages the release.

```text
python manage.py graph_import --release ID --commit [--create-entities] \
    [--resolutions FILE] --as USERNAME
```

| Option | Meaning |
|---|---|
| `--release ID` | The release, by the id registration printed. Required. |
| `--commit` | The mode: import the release as its latest dry run reported it, and stage it. Run again, it resumes a commit that stopped. |
| `--create-entities` | Create the entities that match no stored entity, as the dry run with this option counted them. The commit needs the option exactly when its dry run had it. |
| `--resolutions FILE` | The reviewed [resolution file](typed-data-format.md#resolution-files) its dry run had, checked by its SHA-256: keep the file until the release is committed. |
| `--as USERNAME` | The active staff account running the commit. Required. |

`--report-file` goes with `--dry-run` only: a commit keeps its report with
its run. On a production server:

```bash
dc exec backend python manage.py graph_import --release 12 --commit --as alice
```

A commit given a resolution file reads it from the host, so mount its folder
as registration does:

```bash
dc run --rm -v "$PWD/incoming:/incoming:ro" backend \
    python manage.py graph_import --release 12 --commit \
    --resolutions /incoming/resolutions.json --as alice
```

### Before you commit

The commit checks, in this order, and refuses with nothing written when:

1. `TYPED_GRAPH_ENABLED` is off;
2. `--as` is not an active staff account;
3. the database's registry is at another version than this code's: run
   `python manage.py graph_sync_registry` after an upgrade;
4. the release does not exist, or is withdrawn: a withdrawal is final;
5. the resolution file is unreadable or invalid;
6. the release is not a draft. When its latest commit run completed with
   the same `--create-entities` and resolution file, it is committed
   already: the command says so and writes nothing. Otherwise a staged
   release is refused (discard it to commit it with other options), and so
   is a published or superseded release, which a new release corrects
   instead;
7. the release does not have exactly one data file and at most one manifest;
8. the release is not synthetic and has no effective `internal` approval;
9. a dry run, commit or discard of the release is running
   ([One commit at a time](#one-commit-at-a-time));
10. the release has an open commit run that cannot be resumed: one started
    under another adapter version, mapping version, kind, registry version
    or dry run (discard it), or with other options (run the same command
    with the options the refusal names). A `running` run is never resumed
    on a database without the run lock;
11. this code's adapter has another version, mapping version or kind than
    the release was registered with:
    [register it again](typed-data-format.md#registering-again);
12. the release's latest dry run is not one a commit trusts. It must have
    completed with the verdict `ok`, under the release's adapter, versions
    and kind and the database's registry version, after the release's last
    commit or discard, and with the same `--create-entities` and resolution
    file. Otherwise dry-run it again, with the options you will commit
    with;
13. the file is refused, as a dry run would refuse it: a stored copy that is
    missing or changed, an invalid manifest, a file error, more than 250,000
    distinct identifiers, or, in a synthetic release, a violation on any
    line;
14. the release's records are not those its latest dry run stored: a record
    the file gives is not stored, or is stored from another line or content,
    or the release holds more records than the file gives;
15. a record now maps to another status than its latest dry run gave it.
    The refusal names up to 5 of them: dry-run the release again and review
    its report.

Once the commit holds the run lock (9), it marks failed the release's runs
that a process which ended left running, then reads the release again: a
commit that finished meanwhile means the release is committed already. A
refusal after that marking says which runs it marked instead of "Nothing
was written." While the release has an open commit run, refusals 11 to 15
say to discard it, since a dry run and a registration both refuse while it
is open. Checks 13 to 15 read the whole file before anything is written.

### What a commit does

1. **Planning.** It reads the stored data file as its dry run read it, in
   the same batches and through the same mapping, once, or twice when a
   later batch changes how earlier rows map. It writes nothing, and checks
   13 to 15 on what it finds.
2. **The run.** It records its commit run, with its options, the registry
   version and the dry run it trusts, or resumes the release's open commit
   run.
3. **Writing.** It reads the file a last time, in batches of at most 500
   rows and 32 MiB, and writes each batch in one transaction. A batch first
   checks again that the switch is on, the release is a draft read by the
   run's adapter version and kind, the run is still the release's open
   commit run, a release of real data still has an effective `internal`
   approval, and the registry is still at the run's version. Then, for the
   batch's mapped records:
    - with `--create-entities`, it creates the entities they need, each with
      the label and identifier its dry run's report showed;
    - it finds or creates each paper they cite: a `pmid:` or `doi:` paper by
      its identifier, and a `ref:` paper as a publication of the release's
      dataset, with the manifest's title and year (one the dataset holds
      already keeps its title and year, and is given only one it lacks, such
      as a missing year; the report lists each difference it keeps);
    - it records each term they use that openPIP did not hold when it
      planned, with the label the manifest declares, or, for a term that
      openPIP's PSI-MI crosswalk pins, such as an interaction type, the
      crosswalk's;
    - it creates the relationships they name that do not exist yet;
    - it records each record's evidence, which is a no-op for a record
      recorded already;
    - it saves its **checkpoint**, the batch's last line, with its report.
4. **Finishing.** In one transaction it checks that every mapped record holds
   evidence, records the release's [statistics](#release-statistics),
   stages the release and completes the run.

What a commit never does:

- it writes no source record: each keeps the status and issues its dry run
  stored;
- it records nothing from an unresolved, ambiguous, rejected or unstored
  row;
- it never changes an existing entity, its label included: an entity that
  another release created after the dry run keeps that release's label,
  which the commit's count of created entities shows;
- it never replaces a stored term's label, or a stored paper's title or
  year: it fills only a `ref:` paper's blank title or year from the
  manifest. Its report lists each term, and each `ref:` paper, that its
  mapped records name and that keeps another label, title or year than the
  manifest states, whenever openPIP stored it, before the dry run or since;
- it stores no identifier but each new entity's own.

Entities it creates are `provisional`, and name the release as the one that
created them. Releases that commit at the same time and share entities,
relationships, publications or terms wait for each other's batches, so
neither writes a duplicate.

A paper's identifier that another publication holds already is never
written: the commit's report lists it, and the batch goes on. A
`canonical_tsv_v1` row names each paper by one identifier, so its commits
meet none. A commit keeps the same identifier caches as its dry run, but
not its tally of relationships ([Limits](typed-data-format.md#limits)).

### The summary and the report

The command prints a summary:

```text
Commit run 8 of release 12: completed, and the release is staged.
It imported the records of dry run 7: 2 mapped, 1 unresolved, 0 ambiguous, 0 rejected, 0 skipped.
Recorded 2 records (0 already recorded), with 2 assertions.
Created 0 entities, 2 relationships, 1 publication and 3 terms.
Next: publish it with graph_publish_release, or discard it with graph_discard_release.
```

This is the commit of the example file of
[Typed data format](typed-data-format.md#an-example), after its dry run 7
([The report](typed-data-format.md#the-report)), on a portal holding its
four genes and proteins and none of its terms: its paper and its three terms
are new. A resumed commit says "completed after 1 resume". A line is added
when terms keep a label other than the manifest's, when papers keep a title
or year other than the manifest's, and when publication identifiers are
held by other publications; `graph_check` lists them. A release committed
already gets one line instead:

```text
Release 12 was committed by run 8, and is staged: nothing was written.
```

The full report is stored with the run (`ImportRun.report`), which the
Django admin does not show yet. Read it from a Django shell:

| Section | What it holds |
|---|---|
| `dry_run` | The id of the dry run it imported. |
| `adapter`, `registry`, `release` | As in the dry run's report: the adapter's name, versions and kind, the registry versions, and the release. |
| `create_entities`, `resolutions` | The commit's options: whether it had `--create-entities`, and the resolution file's SHA-256 (empty without one). |
| `planning_readings` | How many times the planning read the file: 1 or 2. |
| `counts` | The records by status, as planned; then, over every attempt, the batches written, the records recorded and those recorded already, the assertions created, and the entities, relationships, publications and terms created. |
| `term_labels` | Each term the mapped records name that keeps another label than the manifest's: one openPIP held when the commit's planning read the file, whenever it was stored, a term the PSI-MI crosswalk pins, or one another release stored since. The CURIE, the label stated and the one kept, and the line. |
| `publication_conflicts` | Each publication identifier that another publication holds: the publication, the field, the value, the publication holding it, and the line. |
| `paper_differences` | Each `ref:` paper the mapped records cite that the dataset held already, whenever it was stored, with another title or year than the manifest's: the publication, its key, the title and year it keeps and those the manifest states, and the line. |
| `resumed` | Each resume: when, by whom, the error the run had stopped with, and its checkpoint's line. |
| `discard` | Added by a discard ([Discarding a release](#discarding-a-release)). |

Each list gives its `total`, whether it is `truncated`, and at most 1,000
`entries`. A truncated `term_labels` or `paper_differences` list also gives
its `keys`, every CURIE or paper key it counted, so that a resumed commit
counts none of them twice.

### A commit that stopped

A batch that fails is rolled back whole, and the attempt stops: a refusal
of the evidence rules, an entity that another release created meanwhile
with a conflicting identifier, a lapsed approval, a registry upgrade, or an
error of the database (a deadlock, a lost connection). Its run is marked
`failed`, with the error, and its report and checkpoint stay as the last
batch written left them. The command says:

```text
The commit stopped, and its run 9 is marked failed: <the cause> Run the same command again to resume it once the cause is fixed, or discard it with graph_discard_release.
```

To **resume** it, fix the cause and run the same command again, with the same
options and the same resolution file. The commit plans the whole file again,
skips every batch up to its checkpoint, and writes the others; a record that
an earlier attempt recorded is a no-op. Its report records each resume. A
rerun with other options is refused, naming the options the run was started
with. While a commit run is open, the release is not dry-run or registered
again.

A process that is killed marks nothing: its run stays `running`, until the
release's next commit or discard marks it failed ("Its process ended before
the run finished"). That commit then resumes it. `graph_check` lists the
run meanwhile. A commit whose database connection was lost cannot mark its
run either, and says so: "could not be marked failed, since the database
connection was lost". On a database without the run lock, it says instead
that the run stays `running` and names the discard, since nothing marks
the run there and a commit never resumes a `running` one
([One commit at a time](#one-commit-at-a-time)).

A run that cannot be resumed (the refusals of
[Before you commit](#before-you-commit) say which) is given up with
`graph_discard_release`; then dry-run the release again and commit it.

A resume plans against the entities its earlier attempts created. In an
identity namespace where the case or the version of an identifier tells two
entities apart, another case or version of an identifier the run created is
then a candidate, not a new entity, and the resume is refused (15): discard
the release and dry-run it again. No identity namespace openPIP registers
today has either rule (`hgnc` and `uniprotkb` are matched ignoring case, the
synthetic ones take capitals only, and `ensembl_gene`, which splits off
versions, defines no entity), so every file a portal can import today
resumes.

### One commit at a time

One dry run, commit or discard of a release runs at a time. Each takes the
release's **run lock** once its earlier checks pass (check 9 of a commit,
the last of a dry run's) and holds it until it returns, and a second one is
refused: "A dry run, commit or discard of release 12 is running: one runs
at a time." The lock is a PostgreSQL session lock. It holds across the
run's transactions and ends with its process, so a process that is killed
frees it.

- **It needs PostgreSQL.** On a portal or tool run without `DATABASE_URL`,
  which uses SQLite, there is no lock. A release still has at most one open
  commit run there, and a commit never resumes a `running` one: discard it
  if no process runs it. Nothing marks a dry run left `running` there
  failed, so `graph_check` keeps listing it, and exits 1: once no process
  runs it, mark it failed from a Django shell, as below.
- **It needs a direct connection.** A connection pooler in transaction mode,
  such as PgBouncer's, would break it. The compose files have none.

To mark failed a dry run left `running` on a database without the run lock,
with the run's id for `N`:

```bash
python manage.py shell -c "from django.utils import timezone; from graph.models import ImportRun; run = ImportRun.objects.get(pk=N, mode='dry_run', state='running'); run.state = 'failed'; run.error = 'Its process ended before the run finished.'; run.finished_at = timezone.now(); run.save(update_fields=['state', 'error', 'finished_at'])"
```

To find the process that holds a release's lock, with the release's id for
`12`, and the portal's database user and name for `openpip`:

```bash
dc exec db psql -U openpip -d openpip -c "SELECT a.pid, a.state, a.query_start FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid WHERE l.locktype = 'advisory' AND l.classid = 1196573489 AND l.objid = 12 AND l.objsubid = 2 AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())"
```

`graph_check` shows the same backend's pid under `live_runs`. To stop it,
press Ctrl-C in its terminal, or end its backend:

```bash
dc exec db psql -U openpip -d openpip -c "SELECT pg_terminate_backend(4242)"
```

with its pid for `4242`. Either leaves its run `failed` or `running`, and
the release's next commit or discard marks it failed if it is still
running. The next commit resumes a commit run; a dry run is never resumed,
so dry-run the release again.

## Publishing a release

```text
python manage.py graph_publish_release --release ID --as USERNAME
```

| Option | Meaning |
|---|---|
| `--release ID` | The release, by its id. Required. |
| `--as USERNAME` | The active staff account publishing it. Required. |

```bash
dc exec backend python manage.py graph_publish_release --release 12 --as alice
```

Publishing makes a staged release current, so it joins the default views of
those who may see it. It needs an effective `internal` approval, not
`public_view`: publishing is not public, and a portal can publish and
supersede releases with `internal` approvals alone. Whether visitors may
see a release is decided only by its `public_view` approval.

A release registered with `--supersedes` corrects another release of its
dataset. Publishing it marks that release `superseded`, in the same
transaction, if it is published; one that was withdrawn stays withdrawn.
Publishing is refused, with nothing written, for a release that is not
staged, one without an effective `internal` approval, a correction of a
release that cannot be superseded (one never published, or corrected
already), and a correction that would hide public data: while the release
it corrects has an effective `public_view` approval, the correction needs
one too. The command says what the release superseded, and whether
visitors may see it.

## Withdrawing a release

```text
python manage.py graph_withdraw_release --release ID --reason TEXT \
    --as USERNAME
```

| Option | Meaning |
|---|---|
| `--release ID` | The release, by its id. Required. |
| `--reason TEXT` | Why it is withdrawn, kept with the release. Required. |
| `--as USERNAME` | The active staff account withdrawing it. Required. |

```bash
dc exec backend python manage.py graph_withdraw_release --release 12 \
    --reason "The source retracted this version." --as alice
```

A staged, published or superseded release is withdrawn at once: it leaves
every view and export, and its approvals stop being effective. Its rows are
kept, for the record. A withdrawal is final and restores nothing: the
release it superseded stays superseded, and only publishing another
correction makes a release of the dataset current again. A draft is never
withdrawn, since nothing shows it.

Withdraw a release to hide it for good. Discard a staged release to import
it again, or to give up a commit.

## Discarding a release

```text
python manage.py graph_discard_release --release ID --reason TEXT \
    --as USERNAME
```

| Option | Meaning |
|---|---|
| `--release ID` | The release, by its id. Required. |
| `--reason TEXT` | Why it is discarded, kept in its commit run's report. Required. |
| `--as USERNAME` | The active staff account discarding it. Required. |

```bash
dc exec backend python manage.py graph_discard_release --release 12 \
    --reason "Re-import under the new mapping." --as alice
```

A discard applies to a staged release, and to a draft whose commit cannot
or should not resume: one with an open commit run, or holding evidence. It:

- returns a staged release to `draft`, and resets its statistics;
- deletes the release's evidence assertions, with their participants,
  methods, publications, scores and contexts, and then its source records;
- marks its open commit run, or a staged release's completed one,
  `abandoned`, with who discarded it, when, why, and what was deleted, in the
  run's report.

It keeps the release row, with its versions and options, its files, licence
snapshots, approvals and import runs, and every entity with its
identifiers, every relationship, publication and term. Entities the release
created stay `provisional`, and `graph_check` lists those that no evidence
names.

A discard is refused, with nothing written, while a dry run, commit or
discard of the release is running. On PostgreSQL, once it holds the
release's run lock, it marks failed the release's runs that a process which
ended left running, as a commit does. It is then refused for a published,
superseded or withdrawn release, and for a draft with no open commit run
and no evidence, and a refusal after that marking says which runs it marked
instead of "Nothing was written." So a discard of a plain draft clears a
dry run that `graph_check` lists as left running. The command prints what
it deleted and kept, the run it abandoned, and the next steps.

### Importing it again

After a discard, the release's next commit needs a new dry run: dry-run it,
then commit it. To read it with this code's adapter version, after an
upgrade, first register its files again with the same options
([Registering again](typed-data-format.md#registering-again)): the draft
takes the new version. A published release is never imported again in
place: register its files as a new version that supersedes it, then
dry-run, commit and publish that.

## Checking the graph

```text
python manage.py graph_check
```

```bash
dc exec backend python manage.py graph_check
```

`graph_check` reads, and writes nothing; it takes no `--as`. It prints ten
sections, each with its title, its name and how many findings it has, then
at most 20 of them ("and n more" for the rest). The first five are
**problems**; the last five are entries **for review**:

| Section | Kind | What it lists |
|---|---|---|
| `registry` | problem | The registry against this code's: its versions, what `graph_sync_registry` would change or refuse, and rows the code no longer has. |
| `files` | problem | Release files whose stored copy is missing or changed. Registering the files again with the same options puts the copy back. |
| `unnamed_files` | problem | What lies under the release-file root that no row names: every entry of `incoming/`, a directory of a release or a file that does not exist, and anything else, symbolic links included, which are listed and never followed. |
| `runs` | problem | Runs left `running` by a process that ended, which the release's next commit or discard marks failed (listed under `live_runs` instead while a holder of the release's run lock waits to start its own run), and failed commit runs, to resume or discard, but for one whose release's run lock a commit or a discard holds (`live_runs` names it then). On a database without the run lock, every `running` run; nothing marks a dry run among them failed ([One commit at a time](#one-commit-at-a-time)). |
| `approvals` | problem | Approvals of releases that are not withdrawn that rely on an older licence snapshot than their release's latest: refresh each with `graph_approve --replace`, or revoke it. |
| `publication_conflicts` | review | The publication identifiers that commits did not write, since another publication holds them, and the `ref:` papers that keep another title or year than a release's manifest states, from the commits' reports. |
| `term_labels` | review | The terms that keep another label than a release's manifest declared, from the commits' reports. |
| `merge_candidates` | review | A primary identifier of one entity that another entity of the same type and taxon holds in another role: candidates for a merge. |
| `provisional_entities` | review | Provisional entities that no evidence names, which a discard leaves by design. |
| `live_runs` | review | The runs under way, each with the backend that holds its release's run lock, and the run locks held by a commit still reading its file, or by a discard, before any run of the release is running, with the release's failed commit run if it has one. A run left `running` by a process that ended is named here, with the holder's backend, while a commit, discard or dry run that holds its release's run lock waits for the release's row (held by another session: an approval or a publication under way, or a session left open in a transaction) before it marks that run failed or starts its own. Its line then says that the backend waits on a lock, which a commit waiting on another commit's batch shows too; the query in [One commit at a time](#one-commit-at-a-time) shows that backend. |

It exits with status 1 when a problem section has a finding: "graph_check
found 2 problems; 3 entries are listed for review." Otherwise it exits 0,
with "No problems; 3 entries are listed for review." or "No problems, and
nothing to review." Entries for review never change the exit status, so a
scheduled check can alert on its exit status alone.

- **It reads every stored byte.** The `files` section checks each stored
  copy's size and SHA-256, which takes minutes per 10 GiB of release files.
- **Nothing is deleted.** A copy that a registration is writing under
  `incoming/` right now looks the same as one an interrupted registration
  left behind: delete what `unnamed_files` lists only while no registration
  runs.

## Release statistics

A commit records the release's statistics (`DatasetRelease.stats`) as it
stages it, counted from its rows. They are fixed once the release is
published, and a discard resets them. Statistics of the format page's
example, as committed above:

```json
{
  "version": 1,
  "records": {"mapped": 2, "unresolved": 1, "ambiguous": 0, "rejected": 0, "skipped": 0},
  "assertions": 2,
  "assertions_by_outcome": {"supported": 2, "tested_absent": 0, "inconclusive": 0, "not_significant": 0},
  "relationships": 2,
  "entities": 4,
  "publications": 1,
  "entities_created": 0,
  "relationship_types": {
    "physical_interaction": {"assertions": 1, "relationships": 1},
    "transcriptional_regulation": {"assertions": 1, "relationships": 1}
  }
}
```

| Key | What it counts | Added up across releases |
|---|---|---|
| `version` | The version of this shape: 1. | no: it is not a count |
| `records` | The release's source records, by status. | yes |
| `assertions` | Its evidence assertions. | yes |
| `assertions_by_outcome` | Its assertions, by outcome. | yes |
| `relationships` | The distinct relationships its assertions name. | no: one relationship can hold several releases' evidence |
| `entities` | The distinct entities its assertions' participants name. | no: distinct within the release |
| `publications` | The distinct publications its assertions cite. | no: distinct within the release |
| `entities_created` | The entities that name the release as the one that created them. | yes |
| `relationship_types.<type>.assertions` | Its assertions of each relationship type. | yes |
| `relationship_types.<type>.relationships` | The distinct relationships of each type its assertions name. | no: distinct within the release |

A total over several releases adds up only the keys marked yes. What one
commit created (relationships, publications and terms, which several
releases share) is in its run's report, not here.

## Loading the synthetic demonstration

openPIP ships a synthetic demonstration of the typed graph: one dataset,
`synthetic-regulation`, in two synthetic releases whose rows cover the cases
the typed graph must handle, such as direction, mixed and unknown effects,
tested absence, predictions, symmetric physical interactions, the
`encodes` and `isoform_of` links between a gene, its protein entry and an
isoform, and shared labels. Every gene, protein, label and paper in it is
made up and describes no real biology, and every label starts with `SYN-`.
Its files are in `backend/graph/demo_data/synthetic_regulation/`, whose
README lists the case each row covers:

- `release_v1.tsv`, v1: 26 rows in
  [`canonical_tsv_v1`](typed-data-format.md#the-canonical_tsv_v1-format). 17
  are imported; the other 9 are rows that a dry run must report and never
  import, such as an identifier that fails its pattern or "negative" given
  as an effect.
- `release_v2.tsv`, v2: v1's 17 imported rows, with one effect corrected
  (SYN-TF1 → SYN-G1, activating in v1 and inhibiting in v2). v2 supersedes
  v1.
- `manifest.json`: the two synthetic papers the rows cite, with a title and
  a year and no PMID or DOI, and the two PSI-MI interaction types the rows
  use, with the labels of openPIP's crosswalk.

```text
python manage.py graph_load_demo --as USERNAME
```

| Option | Meaning |
|---|---|
| `--as USERNAME` | The active staff account that registers, commits, approves and publishes the demonstration, and records its browser instances. Required. |

On a production server, the backend image already holds the files, so no
folder needs mounting:

```bash
dc exec backend python manage.py graph_load_demo --as alice
```

`graph_load_demo` creates its data source, `openpip-synthetic` (of kind
`this_portal`), and its dataset, `synthetic-regulation`, so neither is
created in the Django admin. Then, for v1 and then v2, it does what an
operator does for a synthetic release, through the same services as the
commands: it registers the
release with `--synthetic` (v2 with `--supersedes v1`), dry-runs it and
commits it with `--create-entities` and no resolution file, records its
licence snapshot and its `internal` approval, and publishes it. v1's commit
creates the demonstration's 13 entities, which are `provisional`, its 14
relationships, its two papers and, unless openPIP holds them already, the
two interaction types; v2's creates none, and publishing v2 supersedes v1.
Last it records two browser instances, for staff only: `synthetic-regulation`,
which selects v2 and cites the two papers, and, unless this portal has a
`portal_ppi` entry already, `protein-interactions`
([The demonstration's rows](browser-instances.md#the-demonstrations-rows)).
It prints one line for the data source and dataset, one line per release
with what each step did, one line for the browser instances, and a last line
naming the current release and who may see it.

Each release's licence snapshot records the repository's MIT License, with
its grant and condition quoted from `LICENSE`, as read on the day of the
load by the account `--as` names. Only `internal` approvals are recorded,
so the demonstration is for staff. On a public hub, every staff member's
default view through `/api/graph/v1/` shows it once the loader has run, and
visitors see it only after a `public_view` approval is recorded with
`graph_approve`
([Approving a release](#approving-a-release)). Both browser instances are
`staff` too, so neither is listed for visitors until an operator makes it
`public`.

### Running it again

`graph_load_demo` is safe to run again. Once the demonstration and its two
browser instances are loaded, it writes nothing and says so. A portal
loaded before the browser instances existed gets them on its next run, and
one that was deleted is created again. A load that stopped is completed
from where it stopped: a commit that stopped is resumed, with no second dry
run; a
staged release is published once its commit is found to have had
`--create-entities` and no resolution file; a release discarded back to
draft is dry-run and committed again, and keeps its licence snapshot and
approval; and a licence snapshot or an `internal` approval that was never
recorded is recorded.

It never overrides an operator. A browser instance that exists is kept as
it stands. A release that is published or superseded is left as it is, and
its line reports what an operator did to it: an
`internal` approval revoked, or a denial; a latest licence snapshot that
records no terms; or an approval that relies on an older snapshot than the
latest (refresh it with `graph_approve --replace` once the latest snapshot
records terms, or revoke it with `graph_revoke`). A v1 withdrawn after it
was published, or whose `internal` approval was revoked, stays so, and v2
is still loaded and published, unless an operator's decision on v2 stops
it (below). It refuses only what it cannot do without overriding an
operator, or what a step's own command refuses. It prints what it did
before, then the refusal, which says what to do before running it again:

- a release still to import or publish has an active `internal` denial,
  only revoked `internal` decisions, or a latest licence snapshot that
  records no terms: nothing is written for that release. While the release
  has no snapshot that records terms, which the loader records only after
  the commit, `graph_approve` refuses an approval, so the refusal says to
  record the terms in the Django admin first (**Release licences**, then
  **Add**); the loader keeps that snapshot instead of recording its own;
- a release was committed, or its commit started and stopped, with other
  options than the loader's: discard it with `graph_discard_release`;
- v1 has an effective `public_view` approval and v2 has none, so publishing
  v2 would hide public data: approve `public_view` for v2 with
  `graph_approve`, once an active `public_view` denial of v2 is revoked
  with `graph_revoke`;
- v2 is withdrawn, or v1 was withdrawn before it was published: this is
  final;
- its data source or dataset slug names other data than the
  demonstration's;
- the slug `synthetic-regulation` names a browser instance of another kind,
  or one selecting a release outside the demonstration's dataset; or this
  portal has no `portal_ppi` entry and the slug `protein-interactions` names
  an instance of another kind. The releases are loaded by then, and neither
  browser instance is written;
- its files differ from those a release past draft was registered with,
  or a draft whose commit stopped was registered under an older version
  of `canonical_tsv_v1` (see below);
- a step is refused as its command would refuse it: for example, the
  database's registry is at another version than this code's (run
  `python manage.py graph_sync_registry`); a commit of the demonstration
  was left `running` on a database without the run lock
  ([One commit at a time](#one-commit-at-a-time)); or a commit that stopped
  cannot be resumed while what it reads has changed, such as a record that
  now maps differently: run `graph_load_demo` again once that no longer
  holds, which resumes it, or first discard the release with
  `graph_discard_release`, after which the loader dry-runs and commits it
  as it maps now.

The demonstration's files are never edited in place. Registration compares
them with the SHA-256 of the copies it stored, so a change ships as new
release versions, such as a v3 that supersedes v2, which a later version of
openPIP adds to the loader. A newer version of `canonical_tsv_v1` does not
stop it: a release past draft keeps the version it was committed with, and
a draft is registered again and takes the new one. A draft whose commit
stopped (it holds evidence or an open commit run) is refused at
registration instead: discard it with `graph_discard_release`, then run
`graph_load_demo` again, which registers, dry-runs and commits it under the
new version.

### Hiding or removing it

To hide the demonstration and keep it reloadable, revoke both releases'
internal approvals (`graph_revoke --approval N`); `graph_approve` records
them again. To remove it for good, withdraw v2, then v1, with
`graph_withdraw_release` (either order works; withdrawing v2 first takes the
current data out of the default view at once). This is final:
`graph_load_demo` then refuses on this portal, since the demonstration ships
as v1 and v2 only. Either way the evidence leaves every view and the 13
entities stay `provisional`, and the browser instances stay, selecting the
hidden releases. To keep one out of a visitor's gallery, leave its
`visibility` at `staff`; retiring it (`status` `retired`) keeps it listed, as
`retired`. After revoking, a run recreates one that is deleted; after
withdrawing, the run is refused before it reaches them. `graph_check` does
not list the entities, because the withdrawn or unapproved releases'
assertions still name them, and the portal's resolver still finds them at
service level, but the API's `resolve` answers only for the entities a caller
may see.

Both ways assume that the load finished, with v2 published. After a load
that stopped earlier, v2 may be missing, or a draft or staged release with
no `internal` approval to revoke, and a draft is never withdrawn. Acting on
v1 alone does not stop the next `graph_load_demo` from completing v2 and
publishing it into every staff member's default view. Run `graph_load_demo`
again first, to complete the load, then hide or remove the demonstration as
above. Or, to keep a registered v2 from being loaded without completing it,
record an `internal` denial of it:
`graph_approve --release ID --scope internal --decision denied --rationale TEXT --as USERNAME`.
The loader then refuses, with nothing written for v2.

## What is refused

- **An approval** that the approval rules do not allow, with the rule it
  breaks named, and any decision on a scope that has an active one.
- **A commit** that fails a check of
  [Before you commit](#before-you-commit), with nothing written. Once it
  writes, a batch that fails rolls back whole and stops the commit, which
  resumes when it is run again.
- **Publishing** a release that is not staged, has no effective `internal`
  approval, or would hide public data as a correction.
- **Withdrawing** a draft, or a release withdrawn already.
- **Discarding** a published, superseded or withdrawn release, or a draft
  with no open commit run and no evidence.
- **Running two at once.** A dry run, commit or discard of a release while
  another runs ([One commit at a time](#one-commit-at-a-time)).
- **Loading the synthetic demonstration** over an operator's decision, over
  data or a browser instance that are not the demonstration's, from files
  that changed under a release past draft, or once a withdrawal has made it
  final
  ([Running it again](#running-it-again)).
- **Every command** while `TYPED_GRAPH_ENABLED` is off, and every command
  that writes without `--as` naming an active staff account.
