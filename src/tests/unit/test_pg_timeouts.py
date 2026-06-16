import os
from unittest.mock import AsyncMock, MagicMock, patch

from mlpa.core.config import Env, env
from mlpa.core.pg_services.app_attest_pg_service import AppAttestPGService
from mlpa.core.pg_services.pg_service import PGService


def test_pg_timeout_config_from_env():
    """PG timeout settings are overridable via environment variables."""
    env_vars = {
        "PG_STATEMENT_TIMEOUT_MS": "5000",
        "PG_IDLE_IN_TX_TIMEOUT_MS": "15000",
        "PG_MAINTENANCE_STATEMENT_TIMEOUT_MS": "45000",
        "PG_ADMIN_READ_TIMEOUT_MS": "20000",
        "PG_COMMAND_TIMEOUT_S": "6.5",
    }

    with patch.dict(os.environ, env_vars):
        test_env = Env()

        assert test_env.PG_STATEMENT_TIMEOUT_MS == 5000
        assert test_env.PG_IDLE_IN_TX_TIMEOUT_MS == 15000
        assert test_env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS == 45000
        assert test_env.PG_ADMIN_READ_TIMEOUT_MS == 20000
        assert test_env.PG_COMMAND_TIMEOUT_S == 6.5


def test_pg_timeout_defaults():
    """Sane defaults: tight statement timeout, larger idle-in-tx reaper, no client backstop."""
    assert env.PG_STATEMENT_TIMEOUT_MS == 3000
    assert env.PG_IDLE_IN_TX_TIMEOUT_MS == 10000
    assert env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS == 30000
    assert env.PG_ADMIN_READ_TIMEOUT_MS == 15000
    assert env.PG_COMMAND_TIMEOUT_S is None


async def test_connect_passes_timeout_server_settings(mocker):
    """The pool is created with server-enforced statement / idle-in-tx timeouts."""
    create_pool = mocker.patch(
        "mlpa.core.pg_services.pg_service.asyncpg.create_pool",
        new=AsyncMock(return_value=MagicMock()),
    )

    service = PGService("some_db")
    await service.connect()

    _, kwargs = create_pool.call_args
    server_settings = kwargs["server_settings"]
    assert server_settings["statement_timeout"] == str(env.PG_STATEMENT_TIMEOUT_MS)
    assert server_settings["idle_in_transaction_session_timeout"] == str(
        env.PG_IDLE_IN_TX_TIMEOUT_MS
    )
    assert server_settings["application_name"] == "mlpa:some_db"
    assert kwargs["command_timeout"] == env.PG_COMMAND_TIMEOUT_S


async def test_connect_respects_per_pool_statement_timeout_override(mocker):
    """A subclass/per-pool override flows into server_settings."""
    create_pool = mocker.patch(
        "mlpa.core.pg_services.pg_service.asyncpg.create_pool",
        new=AsyncMock(return_value=MagicMock()),
    )

    service = PGService("some_db", statement_timeout_ms=1234)
    await service.connect()

    _, kwargs = create_pool.call_args
    assert kwargs["server_settings"]["statement_timeout"] == "1234"


def _mock_maintenance_conn():
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value={"?column?": 1})
    conn.fetchval = AsyncMock(return_value=0)
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    return conn


def _mock_pool(conn):
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire_cm
    return pool


def _set_local_calls(conn, guc):
    return [
        call.args[0]
        for call in conn.execute.call_args_list
        if call.args and f"SET LOCAL {guc}" in call.args[0]
    ]


async def test_admission_transaction_sets_lock_and_statement_timeout(mocker):
    """admission_transaction lifts lock_timeout and a statement_timeout above it."""
    conn = _mock_maintenance_conn()
    mocker.patch.object(PGService, "pool", new=_mock_pool(conn))

    service = PGService("some_db")
    async with service.admission_transaction() as yielded:
        assert yielded is conn

    assert _set_local_calls(conn, "lock_timeout")
    stmt_calls = _set_local_calls(conn, "statement_timeout")
    assert stmt_calls
    # statement_timeout must exceed lock_timeout so the lock wait is governed by
    # lock_timeout, not silently capped by the tight pool-wide statement_timeout.
    expected = env.MLPA_ADMISSION_LOCK_TIMEOUT_MS + env.PG_STATEMENT_TIMEOUT_MS
    assert str(expected) in stmt_calls[0]


async def test_statement_timeout_sets_only_statement_timeout(mocker):
    """statement_timeout() lifts statement_timeout but not idle-in-tx (read-only helper)."""
    conn = _mock_maintenance_conn()
    mocker.patch.object(PGService, "pool", new=_mock_pool(conn))

    service = PGService("some_db")
    async with service.statement_timeout(15000) as yielded:
        assert yielded is conn

    stmt_calls = _set_local_calls(conn, "statement_timeout")
    assert stmt_calls
    assert "15000" in stmt_calls[0]
    assert not _set_local_calls(conn, "idle_in_transaction_session_timeout")


async def test_count_users_by_service_type_uses_admin_read_timeout(mocker):
    """The unindexable full-table GROUP BY runs under the admin-read timeout, not 3s."""
    from mlpa.core.pg_services.litellm_pg_service import LiteLLMPGService

    conn = _mock_maintenance_conn()
    mocker.patch.object(PGService, "pool", new=_mock_pool(conn))

    service = LiteLLMPGService()
    await service.count_users_by_service_type()

    timeout_calls = _set_local_calls(conn, "statement_timeout")
    assert timeout_calls
    assert str(env.PG_ADMIN_READ_TIMEOUT_MS) in timeout_calls[0]
    conn.fetch.assert_awaited_once()


async def test_list_users_uses_admin_read_timeout(mocker):
    """The full-table COUNT(*) + deep OFFSET page run under the admin-read timeout."""
    from mlpa.core.pg_services.litellm_pg_service import LiteLLMPGService

    conn = _mock_maintenance_conn()
    mocker.patch.object(PGService, "pool", new=_mock_pool(conn))

    service = LiteLLMPGService()
    await service.list_users()

    timeout_calls = _set_local_calls(conn, "statement_timeout")
    assert timeout_calls
    assert str(env.PG_ADMIN_READ_TIMEOUT_MS) in timeout_calls[0]


async def test_list_managed_base_identities_uses_maintenance_timeout(mocker):
    """The heavy reconciliation read runs under the maintenance timeout, not the 3s default."""
    from mlpa.core.pg_services.litellm_pg_service import LiteLLMPGService

    conn = _mock_maintenance_conn()
    mocker.patch.object(PGService, "pool", new=_mock_pool(conn))

    service = LiteLLMPGService()
    await service.list_managed_base_identities(["ai"])

    timeout_calls = _set_local_calls(conn, "statement_timeout")
    assert timeout_calls
    assert str(env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS) in timeout_calls[0]
    conn.fetch.assert_awaited_once()


async def test_ensure_capacity_state_reads_identities_before_reconcile_transaction(
    mocker,
):
    """The cross-pool read must not be issued inside the reconcile transaction.

    The session must never sit idle-in-transaction across the cross-pool await,
    so the litellm read has to land AFTER the (self-contained) seed transaction
    and BEFORE the destructive claim rebuild (DELETE ...).
    """
    order: list[str] = []

    litellm_pg = MagicMock()

    async def _list(*_args, **_kwargs):
        order.append("read")
        return []

    litellm_pg.list_managed_base_identities = _list

    conn = _mock_maintenance_conn()

    async def _execute(sql, *_args, **_kwargs):
        order.append(f"exec:{sql.strip()}")

    conn.execute = AsyncMock(side_effect=_execute)
    mocker.patch.object(PGService, "pool", new=_mock_pool(conn))

    service = AppAttestPGService(litellm_pg)
    await service.ensure_capacity_state()

    assert order.count("read") == 1
    read_idx = order.index("read")

    # The claim rebuild (DELETE) — the first statement of the reconcile
    # transaction — must come strictly after the cross-pool read.
    delete_idx = next(
        i for i, step in enumerate(order) if step.startswith("exec:DELETE")
    )
    assert read_idx < delete_idx

    # The seed (INSERT/UPDATE on mlpa_user_capacity) runs in its own transaction
    # and commits before the read, so no cross-pool await spans an open tx.
    assert any(
        step.startswith("exec:INSERT INTO mlpa_user_capacity ") for step in order
    )


async def test_ensure_capacity_state_raises_maintenance_timeout(mocker):
    """Reconciliation lifts the tight pool timeout via SET LOCAL for its transaction."""
    litellm_pg = MagicMock()
    litellm_pg.list_managed_base_identities = AsyncMock(return_value=[])

    service = AppAttestPGService(litellm_pg)

    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"?column?": 1})
    conn.fetchval = AsyncMock(return_value=0)

    # async context managers for pool.acquire() and conn.transaction()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire.return_value = acquire_cm
    mocker.patch.object(PGService, "pool", new=pool)

    await service.ensure_capacity_state()

    set_local_calls = [
        call.args[0]
        for call in conn.execute.call_args_list
        if call.args and "SET LOCAL statement_timeout" in call.args[0]
    ]
    assert set_local_calls, "expected a SET LOCAL statement_timeout in reconciliation"
    assert str(env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS) in set_local_calls[0]
