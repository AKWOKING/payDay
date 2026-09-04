import os
import pytest
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, inspect


def test_alembic_forward_and_backward_migrations(tmp_path):
    """
    Tests forward migration (alembic upgrade head) followed by backward migration
    (alembic downgrade base) and forward re-upgrade to guarantee schema reversibility
    and deployment pipeline stability.
    """
    test_db_path = tmp_path / "test_migration.db"
    test_db_url = f"sqlite:///{test_db_path}"
    test_async_url = f"sqlite+aiosqlite:///{test_db_path}"

    os.environ["DATABASE_URL"] = test_async_url

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", test_async_url)

    # 1. Forward Migration: Upgrade to Head
    command.upgrade(alembic_cfg, "head")

    sync_engine = create_engine(test_db_url)
    inspector = inspect(sync_engine)
    tables_after_upgrade = set(inspector.get_table_names())

    expected_tables = {
        "users",
        "wallets",
        "linked_external_accounts",
        "transactions",
        "notifications",
        "audit_logs",
        "alembic_version",
    }
    assert expected_tables.issubset(tables_after_upgrade), f"Missing tables: {expected_tables - tables_after_upgrade}"

    # 2. Backward Migration: Downgrade to Base
    command.downgrade(alembic_cfg, "base")

    inspector_after_downgrade = inspect(sync_engine)
    tables_after_downgrade = set(inspector_after_downgrade.get_table_names())
    # All domain tables must be dropped
    domain_tables = {"users", "wallets", "linked_external_accounts", "transactions", "notifications", "audit_logs"}
    assert len(domain_tables.intersection(tables_after_downgrade)) == 0, f"Tables remained after downgrade: {domain_tables.intersection(tables_after_downgrade)}"

    # 3. Re-Upgrade to Head
    command.upgrade(alembic_cfg, "head")
    inspector_re_upgrade = inspect(sync_engine)
    tables_re_upgrade = set(inspector_re_upgrade.get_table_names())
    assert expected_tables.issubset(tables_re_upgrade)

    sync_engine.dispose()
