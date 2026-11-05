# Maintenance

Routine tasks for a running portal. Every command is run from the repository
checkout on the server. For readability this page uses `dc` as shorthand for
the production compose command:

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile prod'
```

## Starting, stopping and logs

```bash
dc up -d          # start, or apply configuration changes
dc stop           # stop, keeping the containers
dc ps             # status of each service
dc logs -f backend celery   # follow the logs
```

Prefer `dc stop` to `dc down`. `down` removes the containers. Your data
survives because it lives in volumes, but there is rarely a reason to remove
the containers.

The backend logs warnings and errors. The `celery` log shows each import as it
runs.

## Backups

A portal's state lives in three volumes: the database, the uploaded media,
and the release files of the typed relationship graph. Back up all three,
and back up and restore the release files together with the database.

Docker Compose names volumes after the project, which is the checkout's folder
name. List them with:

```bash
docker volume ls | grep -E '_(postgres_data|media|graph_files)$'
```

The examples below use the names Compose gives a checkout in a folder named
`openpip-2.0`: `openpip-20_media`, `openpip-20_postgres_data` and
`openpip-20_graph_files`.

### Database

```bash
dc exec -T db pg_dump -U openpip -Fc openpip > openpip-$(date +%F).dump
```

This takes a consistent snapshot while the site keeps running. On the
reference portal (about 123,000 interactions) it takes about 8 seconds and
writes a 33 MB file.

### Media

```bash
docker run --rm -v openpip-20_media:/media:ro -v "$PWD":/backup alpine \
  tar czf /backup/media-$(date +%F).tar.gz -C /media .
```

The media volume holds the logo, user avatars, files published on the
Downloads page, and the cached all-datasets archive (`cache/`), which is
rebuilt on demand and need not be kept.

### Typed graph release files

```bash
docker run --rm -v openpip-20_graph_files:/graph_files:ro -v "$PWD":/backup alpine \
  tar czf /backup/graph-files-$(date +%F).tar.gz -C /graph_files .
```

The `graph_files` volume holds the files of each release registered for the
typed relationship graph ([Typed data format](typed-data-format.md#where-release-files-live)).
It is empty on a portal that does not use the typed graph. The database
records each file's path, size and SHA-256, so take this backup together
with the database's, while no release is being registered or committed. A
database restored without its release files leaves releases whose dry runs
and commits are refused; registering each file again with the same options
puts it back. Files left under `incoming/` by an interrupted registration
need not be kept.

### Restoring

Stop the services that write to the database, restore, then start again:

```bash
dc stop backend celery
dc exec -T db pg_restore -U openpip -d openpip --clean --if-exists < openpip-2026-10-01.dump
docker run --rm -v openpip-20_media:/media -v "$PWD":/backup alpine \
  tar xzf /backup/media-2026-10-01.tar.gz -C /media
docker run --rm -v openpip-20_graph_files:/graph_files -v "$PWD":/backup alpine \
  tar xzf /backup/graph-files-2026-10-01.tar.gz -C /graph_files
dc up -d
```

Restore the release files from the same day's backup as the database.
Restoring a database also restores its user accounts and passwords as they
were at the time of the backup.

A database backed up while a release was being committed holds that
commit's open run. Restored, the run is `running` and no process holds its
lock, so the release's next commit marks it failed, then resumes it
([A commit that stopped](typed-releases.md#a-commit-that-stopped)).
`graph_check` lists it until then.

## Upgrading

```bash
git pull
dc up -d --build
```

The backend applies any new database migrations as it starts. Back up first,
and read the release notes for anything that needs doing by hand.

Rebuilding recreates the containers, which interrupts the site for a few
seconds. After an upgrade, check:

```bash
dc ps
dc exec backend printenv DJANGO_SETTINGS_MODULE    # must be openpip.settings.prod
curl -s https://openpip.example.org/api/counts
```

## Managing users

Administrators promote and demote accounts in **Admin → Accounts**.
Tasks that need the server:

### Creating an administrator

```bash
dc exec backend python manage.py createsuperuser
```

### Resetting a user's password

For a user who has forgotten their password and has no security questions,
which is the case for accounts created before security questions existed and
for accounts made with `createsuperuser`:

```bash
dc exec backend python manage.py changepassword <username>
```

Changing the password signs that account out of every browser within an hour.

## The Django admin

Superusers can also manage the database directly at `/django-admin/` (on this
portal, `{{ portal_url }}/django-admin/`). Use it with
care: it edits tables directly, without the checks the admin panel makes.

## Changing the database password

`DB_PASSWORD` in `.env` is only read when the database volume is first
created. To change it on an existing portal:

1. Set the new password inside Postgres:

    ```bash
    dc exec db psql -U openpip -d openpip -c "ALTER USER openpip PASSWORD 'new-password';"
    ```

2. Put the same value in `.env` as `DB_PASSWORD=new-password`.
3. Recreate the services that connect to the database:

    ```bash
    dc up -d --force-recreate backend celery
    ```

Until step 3 finishes, the backend keeps working on its existing connections
but cannot open new ones, so do the three steps together.

## Changing the secret key

Edit `SECRET_KEY` in `.env`, then:

```bash
dc up -d --force-recreate backend celery
```

Every user is signed out, because tokens signed with the old key stop working.

## Housekeeping

Retired sign-in tokens are kept in the database until they expire. Remove the
expired ones occasionally:

```bash
dc exec backend python manage.py flushexpiredtokens
```

On a portal that uses the typed relationship graph, check it now and then,
and after a restore or an upgrade:

```bash
dc exec backend python manage.py graph_check
```

It writes nothing. It exits with status 1 when it finds a problem: a
registry that differs from the code's, a release file that is missing,
changed or left behind, a run whose process ended, a failed commit, or an
approval relying on an older licence snapshot. It exits 0 otherwise,
whatever it lists for review, so a scheduled check can alert on its exit
status. It reads every stored release file, which takes minutes per 10 GiB
([Checking the graph](typed-releases.md#checking-the-graph)).

## Linking proteins to organisms

Data migrated from the original openPIP has no organism information, so the
taxon columns of PSICQUIC output show `-`. This command looks each protein's
organism up in UniProt and links it. It needs internet access, and you can
run it again safely:

```bash
dc exec backend python manage.py backfill_protein_organisms --dry-run   # report only
dc exec backend python manage.py backfill_protein_organisms
```

| Option | Meaning |
|---|---|
| `--dry-run` | Report what would change without saving. |
| `--limit N` | Process at most N proteins. |
| `--sleep S` | Seconds to wait between UniProt requests. Default 1. |

Proteins added through the upload wizard are linked automatically during the
import.
