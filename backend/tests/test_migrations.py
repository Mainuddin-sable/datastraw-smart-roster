import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def migration_config(database_path: Path) -> tuple[Config, str]:
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config, database_url


def test_upgrade_backfills_legacy_rows_and_downgrade_preserves_them(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config, database_url = migration_config(database_path)

    command.upgrade(config, "0001_initial_schema")
    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO organizations (id, name) VALUES (1, 'Legacy Org')")
        connection.execute(
            "INSERT INTO employees (id, organization_id, full_name, email) "
            "VALUES (1, 1, 'Legacy Employee', 'legacy@example.test')"
        )
        connection.execute(
            "INSERT INTO shifts (id, organization_id, title, starts_at, ends_at) "
            "VALUES (1, 1, 'Legacy Shift', '2026-02-01 09:00:00', '2026-02-01 17:00:00')"
        )
        connection.execute("INSERT INTO assignments (id, employee_id, shift_id) VALUES (1, 1, 1)")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM pods WHERE organization_id=1")).scalar_one() == "Default"
        assert connection.execute(text("SELECT pod_id FROM employees WHERE id=1")).scalar_one() == 1
        assert connection.execute(text("SELECT is_active FROM employees WHERE id=1")).scalar_one() == 1
        assert connection.execute(text("SELECT organization_id FROM assignments WHERE id=1")).scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        tables = set(inspect(connection).get_table_names())
        assert "roster_work_assignments" in tables
        assert "generation_runs" in tables
    engine.dispose()
    command.check(config)

    command.downgrade(config, "0001_initial_schema")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert {"organizations", "employees", "shifts", "assignments"} <= tables
        assert "pods" not in tables
        assert connection.execute(text("SELECT email FROM employees WHERE id=1")).scalar_one() == "legacy@example.test"
        assert "pod_id" not in {column["name"] for column in inspect(connection).get_columns("employees")}
        assert "is_active" not in {column["name"] for column in inspect(connection).get_columns("employees")}
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_upgrade_rejects_cross_organization_legacy_assignments_before_ddl(tmp_path: Path) -> None:
    database_path = tmp_path / "invalid-legacy-data.db"
    config, database_url = migration_config(database_path)
    command.upgrade(config, "0001_initial_schema")
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO organizations (id, name) VALUES (?, ?)", [(1, "Org A"), (2, "Org B")]
        )
        connection.execute(
            "INSERT INTO employees (id, organization_id, full_name, email) VALUES (1, 1, 'A', 'a@example.test')"
        )
        connection.execute(
            "INSERT INTO shifts (id, organization_id, title, starts_at, ends_at) "
            "VALUES (1, 2, 'B shift', '2026-02-01 09:00:00', '2026-02-01 17:00:00')"
        )
        connection.execute("INSERT INTO assignments (id, employee_id, shift_id) VALUES (1, 1, 1)")

    with pytest.raises(RuntimeError, match="cross organizations"):
        command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert "pods" not in tables
        assert "employees" in tables
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_initial_schema"
    engine.dispose()
