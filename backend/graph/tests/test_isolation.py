"""The typed apps cannot reach, alter or depend on the pre-existing tables.

Every check except the import scan runs over NEW_APPS. The PR that creates a
new app adds it here, in dependency order, with the apps its migrations may
depend on: browsers (B1), portal_profile (O1) and, if it is ever built,
graph_bridge (L1), whose declared legacy links are its only exception. The
same tests, the schema snapshot and the round trip to zero included, then
cover it.
"""

import ast
import io
import json
import runpy
from pathlib import Path

import environ
import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader
from django.urls import Resolver404, URLResolver, get_resolver, resolve

NEW_APPS = ["graph"]
# Besides itself and the user model.
MAY_DEPEND_ON = {"graph": set()}

ALLOWED_OPERATIONS = (
    migrations.CreateModel,
    migrations.AddField,
    migrations.AddIndex,
    migrations.AddConstraint,
)
# Never, whatever the allow-list admits later.
DENIED_OPERATIONS = (
    migrations.RunPython,
    migrations.RunSQL,
    migrations.DeleteModel,
    migrations.RemoveField,
    migrations.RenameModel,
    migrations.RenameField,
    migrations.AlterModelTable,
    migrations.SeparateDatabaseAndState,
)
LEGACY_APPS = (
    "proteins",
    "interactions",
    "datasets",
    "admin_panel",
    "sharing",
    "psicquic",
)
MANIFEST = Path(__file__).with_name("legacy_migrations.json")
GRAPH_DIR = Path(apps.get_app_config("graph").path)
USER_APP = get_user_model()._meta.app_label


def new_models():
    for app in NEW_APPS:
        yield from apps.get_app_config(app).get_models(include_auto_created=True)


def new_tables() -> set[str]:
    return {model._meta.db_table for model in new_models()}


def new_app_migrations():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    for (app, name), migration in sorted(loader.disk_migrations.items()):
        if app in NEW_APPS:
            yield app, name, migration


def every_operation(operations):
    for operation in operations:
        yield operation
        # SeparateDatabaseAndState nests operations: look inside it too.
        for nested in ("state_operations", "database_operations"):
            yield from every_operation(getattr(operation, nested, ()))


# ── Foreign keys ────────────────────────────────────────────────────────────


def test_new_models_point_only_at_new_apps_or_the_user_model():
    user_model = get_user_model()
    for model in new_models():
        for field in [*model._meta.local_fields, *model._meta.local_many_to_many]:
            target = field.related_model
            if target is None:
                continue
            assert (
                target is user_model or target._meta.app_label in NEW_APPS
            ), f"{model._meta.label}.{field.name} points at {target._meta.label}"


@pytest.mark.django_db
def test_new_tables_reference_only_new_tables_or_user_in_the_database():
    # Wider than migrate_legacy.TABLES: a reload truncates with CASCADE, which
    # also empties every table with a key into a truncated one.
    allowed = new_tables() | {get_user_model()._meta.db_table}
    with connection.cursor() as cursor:
        for table in sorted(new_tables()):
            constraints = connection.introspection.get_constraints(cursor, table)
            for name, constraint in constraints.items():
                if constraint["foreign_key"]:
                    target = constraint["foreign_key"][0]
                    assert target in allowed, f"{table}.{name} references {target}"


# ── Migrations ──────────────────────────────────────────────────────────────


def test_new_app_migrations_use_only_allowed_operations_on_their_own_models():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    state = loader.project_state()
    checked = 0
    for app, name, migration in new_app_migrations():
        for operation in migration.operations:
            assert (
                type(operation) in ALLOWED_OPERATIONS
            ), f"{app}.{name}: {type(operation).__name__} is not allowed"
            model = getattr(operation, "model_name", None) or operation.name
            assert (app, model.lower()) in state.models, f"{app}.{name}: {model}"
            checked += 1
    assert checked


def test_new_app_migrations_contain_no_denied_operation():
    for app, name, migration in new_app_migrations():
        for operation in every_operation(migration.operations):
            assert not isinstance(
                operation, DENIED_OPERATIONS
            ), f"{app}.{name}: {type(operation).__name__} is never allowed"


def test_new_app_migrations_depend_only_on_the_apps_they_may_use():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    user_migration = next(
        name
        for (app, name), migration in loader.disk_migrations.items()
        if app == USER_APP
        and any(
            isinstance(op, migrations.CreateModel)
            and op.name == get_user_model().__name__
            for op in migration.operations
        )
    )
    assert set(MAY_DEPEND_ON) == set(NEW_APPS)
    for app, name, migration in new_app_migrations():
        assert not migration.run_before, f"{app}.{name} orders itself before others"
        for dependency_app, dependency in migration.dependencies:
            if dependency_app == app:
                continue
            if dependency_app == USER_APP:
                assert dependency in (
                    "__first__",
                    user_migration,
                ), f"{app}.{name} depends on {dependency_app}.{dependency}"
                continue
            # Another new app, created by a migration earlier in NEW_APPS.
            assert (
                dependency_app in MAY_DEPEND_ON[app]
            ), f"{app}.{name} depends on {dependency_app}.{dependency}"
            assert NEW_APPS.index(dependency_app) < NEW_APPS.index(app)


def test_graph_0002_is_checked_and_depends_only_on_0001_and_the_user_model():
    # The checks above cover every migration the loader finds; this pins that
    # they find the provenance and licensing one.
    found = {name: migration for app, name, migration in new_app_migrations()}
    migration = found["0002_provenance_licensing"]
    assert set(migration.dependencies) == {
        ("graph", "0001_initial"),
        (USER_APP, "__first__"),
    }


def test_the_pre_existing_apps_migrations_match_the_manifest():
    # A fork-only migration in a shared app would collide with upstream's
    # numbering. An upstream sync that brings new migrations updates the
    # manifest in that PR.
    loader = MigrationLoader(None, ignore_no_migrations=True)
    found = {}
    for app, name in loader.disk_migrations:
        if app not in NEW_APPS:
            found.setdefault(app, []).append(name)
    found = {app: sorted(names) for app, names in found.items()}
    assert found == json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_graph_imports_no_legacy_app():
    for path in sorted(GRAPH_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert (
                    module.split(".")[0] not in LEGACY_APPS
                ), f"{path.relative_to(GRAPH_DIR.parent)} imports {module}"


# ── Migrating forward, back and forward again ───────────────────────────────


def migrate(*args):
    call_command("migrate", *args, verbosity=0, stdout=io.StringIO())


def migrate_new_apps_to_zero():
    for app in reversed(NEW_APPS):
        migrate(app, "zero")


def project_apps():
    """The pre-existing apps that are this project's own, not Django's or a
    third party's."""
    root = Path(settings.BASE_DIR)
    return [
        config
        for config in apps.get_app_configs()
        if config.label not in NEW_APPS and root in Path(config.path).parents
    ]


def row_counts() -> dict[str, int]:
    counts = {}
    with connection.cursor() as cursor:
        for config in project_apps():
            for model in config.get_models(include_auto_created=True):
                table = model._meta.db_table
                if model._meta.proxy or table in counts:
                    continue
                cursor.execute(
                    f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}"
                )
                counts[table] = cursor.fetchone()[0]
    return counts


def bookkeeping() -> dict[str, list]:
    """Django's own tables, without the new apps' rows. The app is the first
    column of each query."""
    queries = {
        "django_migrations": "SELECT app, name FROM django_migrations",
        "django_content_type": "SELECT app_label, id, model FROM django_content_type",
        "auth_permission": (
            "SELECT c.app_label, p.id, p.codename, p.name FROM auth_permission p"
            " JOIN django_content_type c ON c.id = p.content_type_id"
        ),
    }
    rows = {}
    with connection.cursor() as cursor:
        for table, sql in queries.items():
            cursor.execute(sql)
            rows[table] = sorted(
                row for row in cursor.fetchall() if row[0] not in NEW_APPS
            )
    return rows


def legacy_schema() -> dict[str, list]:
    """Columns, types, nullability, defaults, constraints and indexes of every
    table that is not a new app's, from PostgreSQL's catalogue."""
    queries = {
        "columns": """
            SELECT table_name, ordinal_position, column_name, data_type,
                   character_maximum_length, numeric_precision, numeric_scale,
                   is_nullable, column_default, is_identity, identity_generation
            FROM information_schema.columns
            WHERE table_schema = current_schema()""",
        "constraints": """
            SELECT rel.relname, con.conname, con.contype,
                   pg_get_constraintdef(con.oid)
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = current_schema()""",
        "indexes": """
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = current_schema()""",
    }
    excluded = new_tables()
    schema = {}
    with connection.cursor() as cursor:
        for part, sql in queries.items():
            cursor.execute(sql)
            schema[part] = sorted(
                tuple(map(str, row))
                for row in cursor.fetchall()
                if row[0] not in excluded
            )
    return schema


def tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_the_legacy_schema_and_rows_are_unchanged_by_the_new_apps_round_trip():
    # Rows in pre-existing tables, so a migration that dropped or added rows
    # would show. The import fence keeps legacy models out of graph's imports,
    # so these come from the app registry.
    get_user_model().objects.create_user("snapshot", "snapshot@example.com", "x" * 12)
    apps.get_model("proteins", "Protein").objects.create(gene_name="TP53")
    apps.get_model("datasets", "Dataset").objects.create(name="Snapshot")

    snapshots = []
    try:
        snapshots.append(
            ("graph applied", legacy_schema(), row_counts(), bookkeeping())
        )
        migrate_new_apps_to_zero()
        assert not new_tables() & tables()
        snapshots.append(
            ("before forward", legacy_schema(), row_counts(), bookkeeping())
        )
        migrate()
        snapshots.append(("forward", legacy_schema(), row_counts(), bookkeeping()))
        migrate_new_apps_to_zero()
        snapshots.append(("back to zero", legacy_schema(), row_counts(), bookkeeping()))
    finally:
        migrate()
    snapshots.append(("forward again", legacy_schema(), row_counts(), bookkeeping()))
    assert new_tables() <= tables()

    first_name, *first = snapshots[0]
    assert first[0]["columns"] and first[1]["protein"] == 1
    for name, *snapshot in snapshots[1:]:
        for part, before, after in zip(
            ("schema", "rows", "bookkeeping"), first, snapshot
        ):
            assert after == before, f"{part} differs between {first_name} and {name}"


@pytest.mark.django_db(transaction=True)
def test_each_new_app_migrates_to_zero_and_forward_again():
    try:
        migrate_new_apps_to_zero()
        assert not new_tables() & tables()
    finally:
        migrate()
    assert new_tables() <= tables()


def tables_at(app, migration) -> set[str]:
    """The app's tables once it is migrated to `migration`."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    state = loader.project_state((app, migration))
    models = state.apps.get_app_config(app).get_models(include_auto_created=True)
    return {model._meta.db_table for model in models}


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_the_legacy_schema_and_rows_are_unchanged_by_one_step_back_and_forward():
    # graph back to 0001, its first migration, removes only 0002's
    # provenance and licensing tables and Entity.created_by_release.
    get_user_model().objects.create_user("snapshot", "snapshot@example.com", "x" * 12)
    apps.get_model("proteins", "Protein").objects.create(gene_name="TP53")
    apps.get_model("datasets", "Dataset").objects.create(name="Snapshot")
    first_tables = tables_at("graph", "0001_initial")

    snapshots = []
    try:
        snapshots.append(
            ("graph applied", legacy_schema(), row_counts(), bookkeeping())
        )
        migrate("graph", "0001")
        assert new_tables() & tables() == first_tables
        snapshots.append(
            ("graph at 0001", legacy_schema(), row_counts(), bookkeeping())
        )
    finally:
        migrate()
    snapshots.append(("forward again", legacy_schema(), row_counts(), bookkeeping()))
    assert new_tables() <= tables()

    first_name, *first = snapshots[0]
    assert first[0]["columns"] and first[1]["protein"] == 1
    for name, *snapshot in snapshots[1:]:
        for part, before, after in zip(
            ("schema", "rows", "bookkeeping"), first, snapshot
        ):
            assert after == before, f"{part} differs between {first_name} and {name}"


@pytest.mark.django_db(transaction=True)
def test_graph_steps_back_to_0001_and_forward_again():
    first_tables = tables_at("graph", "0001_initial")
    # G1's tables, and not the ones 0002 adds.
    assert "graph_entity" in first_tables
    assert "graph_datasetrelease" not in first_tables
    assert first_tables < new_tables()
    try:
        migrate("graph", "0001")
        assert new_tables() & tables() == first_tables
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(
                cursor, "graph_entity"
            )
        assert "created_by_release_id" not in {column.name for column in columns}
    finally:
        migrate()
    assert new_tables() <= tables()


# ── Off by default, and nothing reachable ───────────────────────────────────


def test_the_switch_is_off_by_default(monkeypatch):
    # Evaluate the settings file afresh, with the variable unset and the root
    # .env not read, as on a portal that never set it.
    monkeypatch.delenv("TYPED_GRAPH_ENABLED", raising=False)
    monkeypatch.setattr(environ.Env, "read_env", classmethod(lambda cls, *a, **k: None))
    base = Path(settings.BASE_DIR) / "openpip" / "settings" / "base.py"
    assert runpy.run_path(str(base))["TYPED_GRAPH_ENABLED"] is False
    # Compose passes an unset variable as "", which is off too.
    monkeypatch.setenv("TYPED_GRAPH_ENABLED", "")
    assert runpy.run_path(str(base))["TYPED_GRAPH_ENABLED"] is False


def test_no_url_pattern_starts_with_api_graph():
    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            yield route
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, route)

    def normalized(route):
        # A re_path under path("api/", ...) joins as "api/^graph/...".
        return route.replace("^", "").replace("$", "").lstrip("/")

    routes = list(walk(get_resolver().url_patterns))
    assert routes
    assert not [route for route in routes if normalized(route).startswith("api/graph")]
    with pytest.raises(Resolver404):
        resolve("/api/graph/")
