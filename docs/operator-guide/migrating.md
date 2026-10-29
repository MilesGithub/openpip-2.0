# Migrating from the original openPIP

A portal running the original openPIP (PHP and MySQL) can move its data into
openPIP 2.0 with `migration/migrate_legacy.py`. The script copies the
database's tables into the new PostgreSQL schema, which keeps the original
table and column names.

!!! warning "Work from a copy"
    Never point the script at your live MySQL server. Export a dump and load it
    into a separate MySQL instance used only for the migration, as below.

!!! danger "Run it only on a new installation you can recreate"
    The script cannot load seven tables into the current schema, and with
    `--reset` it empties tables before it copies into them. Run it only
    against a new openPIP 2.0 database that you have backed up, never against
    a portal that already holds data. See
    [Tables the script cannot load](#tables-the-script-cannot-load).

## What is migrated

Copied, in an order that respects foreign keys: site settings,
announcements, proteins and their identifiers, isoforms, organisms, interaction
categories, interactions, datasets, domains, complexes, annotations, saved
networks, supporting information, data files, dataset requests and external
links. Seven of these tables currently fail to load: see
[Tables the script cannot load](#tables-the-script-cannot-load).

Not copied:

- **User accounts.** The original openPIP's password format cannot be carried
  over. Users register again, and you recreate administrators (see
  [Installation](installation.md#create-the-first-administrator)).
- Tables that exist only in 2.0, such as interaction participants and
  published files. They start empty.

Only columns that exist in both schemas are copied.

## Steps

### 1. Prepare openPIP 2.0

Install openPIP 2.0 as in [Installation](installation.md), and start it once
so the database tables exist. The script connects to Postgres from the host
on `localhost:5432`, using `DATABASE_URL` from the repository's `.env`:

```ini
DATABASE_URL=postgres://openpip:<DB_PASSWORD>@localhost:5432/openpip
```

The development compose file publishes port 5432 on the host. If yours does
not, run the migration on a development copy and move the result to
production with a [database backup and restore](maintenance.md#backups).

Then take a [database backup](maintenance.md#backups) of this new
installation, so you can return to it if the run fails.

### 2. Load the old database into a temporary MySQL

Export the original database with `mysqldump`, then load it into a temporary
MySQL 8 container:

```bash
docker run -d --name legacy-mysql -e MYSQL_ROOT_PASSWORD=secret -e MYSQL_DATABASE=huri mysql:8
# wait until it accepts connections, then:
docker exec -i legacy-mysql mysql -uroot -psecret huri < openpip-legacy.sql
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' legacy-mysql
```

The last command prints the container's address.

### 3. Point the script at it

The script reads the MySQL connection from environment variables:

| Variable | Default |
|---|---|
| `MYSQL_HOST` | `127.0.0.1` |
| `MYSQL_USER` | `root` |
| `MYSQL_PASS` | `secret` |
| `MYSQL_DB` | `huri` |

Set `MYSQL_HOST` to the address from step 2, and the other three to match your
temporary container, for example `export MYSQL_HOST=172.18.0.3`. The script
connects on MySQL's default port, 3306.

### 4. Run it

```bash
python3 -m venv .venv-migrate
.venv-migrate/bin/pip install -r migration/requirements.txt
.venv-migrate/bin/python migration/migrate_legacy.py --reset
```

`--reset` empties each target table before copying into it, so that the
default site settings and four interaction categories openPIP 2.0 created on
its first start are replaced by your own. It also empties every table that
refers to the one it empties, and it commits the emptying before it copies
anything. The script prints a line per table and exits with an error if any
table failed. A table that failed after `--reset` is left empty: see
[Tables the script cannot load](#tables-the-script-cannot-load).

### 5. Finish

- Link proteins to their organisms, which the original openPIP did not
  record (see
  [Linking proteins to organisms](maintenance.md#linking-proteins-to-organisms)).
- Create an administrator, and check the site settings and dataset citations
  in the admin panel.
- Remove the temporary container: `docker rm -f legacy-mysql`.

## Tables the script cannot load

openPIP 2.0 added required columns to seven of the copied tables, or renamed
them, and the script copies only the columns whose names match. Loading these
tables therefore fails whenever the old database has rows in them:

| Table | Required 2.0 columns the old table does not have |
|---|---|
| `admin_settings` | `show_tissue_expression`, `show_subcellular_location`, `horizontal_filter_bar`, `protein_link_target` |
| `organism` | `name`, `taxonomy_id` (the old table has `taxid_id` and `common_name`) |
| `interaction` | `negative` |
| `dataset` | `publication_status`, `show_on_about`, `about_order` |
| `interaction_network` | `note` |
| `data_file` | `file_name`, `file_path` (the old table has `name` and `path`) |
| `external_link` | `url`, `name` (the old table has `link` and `database_name`) |

Without `--reset`, these tables are not copied, and the script exits with an
error.

With `--reset`, data is lost as well:

- Each of these tables is emptied before its copy fails, so it is left empty.
- Emptying a table also empties every table that refers to it. Emptying
  `protein` empties `interaction` and everything attached to interactions.
  Emptying `dataset` empties its links to interactions, users, data files and
  dataset requests. Most of these tables are copied again later in the run;
  `interaction` is not, because its own copy fails.
- Four 2.0 tables are emptied this way and never copied back:
  `interaction_participant`, `user_datasets`, `user_interaction_networks` and
  `user_protein`.
- The script turns foreign-key checks off while it copies, so tables copied
  after a failed one can hold rows that point at rows that no longer exist,
  such as links between datasets and interactions that are gone.

If a run reports `FAIL` for any table, do not use the result. Restore the
backup you took before the run.
