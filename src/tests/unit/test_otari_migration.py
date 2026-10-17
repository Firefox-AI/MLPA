"""Carrying LiteLLM's blocked and moved end users to Otari: planning, idempotency and refusals."""

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import httpx
import pytest

from mlpa.core.config import OTARI_API_ROOT, env
from mlpa.core.services.otari_service import OtariService


def _load_migration_script():
    path = (
        Path(__file__).resolve().parents[3] / "scripts" / "migrate_litellm_to_otari.py"
    )
    spec = spec_from_file_location("migrate_litellm_to_otari", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    # Registered first, so its dataclasses resolve their module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migration = _load_migration_script()
Target = migration.Target
changes = migration.changes
migrate_end_user = migration.migrate_end_user
missing_claims = migration.missing_claims
plan_end_users = migration.plan_end_users

DEFAULTS = {
    "ai": "end-user-budget-ai",
    "memories": "end-user-budget-memories",
    "ai-dev": "end-user-budget-ai-dev",
}


def _row(user_id, budget_id, blocked=False):
    return {"user_id": user_id, "budget_id": budget_id, "blocked": blocked}


def test_only_blocked_and_moved_users_are_carried():
    plan = plan_end_users(
        [
            _row("plain:ai", "end-user-budget-ai"),
            _row("nobudget:ai", None),
            _row("bad:ai", "end-user-budget-ai", blocked=True),
            _row("tester:ai", "end-user-budget-ai-dev"),
            _row("both:memories", "end-user-budget-ai-dev", blocked=True),
        ],
        DEFAULTS,
    )

    assert plan.targets == [
        Target("bad:ai", "end-user-budget-ai", blocked=True),
        Target("tester:ai", "end-user-budget-ai-dev", moved=True),
        Target("both:memories", "end-user-budget-ai-dev", blocked=True, moved=True),
    ]
    assert plan.skipped == 2
    assert [t.user_id for t in plan.blocked] == ["bad:ai", "both:memories"]
    assert [t.user_id for t in plan.moved] == ["tester:ai", "both:memories"]


def test_all_creates_every_recognized_user_on_its_default_budget():
    plan = plan_end_users(
        [_row("plain:ai", "end-user-budget-ai"), _row("nobudget:memories", None)],
        DEFAULTS,
        include_all=True,
    )

    assert plan.plain == [
        Target("plain:ai", "end-user-budget-ai"),
        Target("nobudget:memories", "end-user-budget-memories"),
    ]
    assert plan.skipped == 0


def test_a_budget_no_service_type_has_is_reported_and_not_moved():
    plan = plan_end_users(
        [
            _row("custom:ai", "litellm-only-budget"),
            _row("custom-blocked:ai", "litellm-only-budget", blocked=True),
        ],
        DEFAULTS,
    )

    assert plan.unknown_budget == [
        ("custom:ai", "litellm-only-budget"),
        ("custom-blocked:ai", "litellm-only-budget"),
    ]
    # The block is still carried, on the service type's budget.
    assert plan.targets == [
        Target("custom-blocked:ai", "end-user-budget-ai", blocked=True)
    ]
    assert plan.skipped == 1


def test_ids_without_a_known_service_type_are_unrecognized():
    plan = plan_end_users(
        [_row("no-service-type", None, True), _row("x:retired", None, True)],
        DEFAULTS,
    )

    assert plan.unrecognized == ["no-service-type", "x:retired"]
    assert plan.targets == []


def test_a_missing_end_user_is_created_on_its_budget_then_blocked():
    target = Target("t:ai", "end-user-budget-ai-dev", blocked=True, moved=True)

    assert changes(target, None) == [
        ("PUT", {"budget_id": "end-user-budget-ai-dev"}),
        ("PATCH", {"blocked": True}),
    ]


@pytest.mark.parametrize(
    ("target", "current", "expected"),
    [
        # Already right: nothing to do.
        (
            Target("t:ai", "end-user-budget-ai-dev", blocked=True, moved=True),
            {"budget_id": "end-user-budget-ai-dev", "blocked": True},
            [],
        ),
        # Moved, not yet blocked.
        (
            Target("t:ai", "end-user-budget-ai-dev", blocked=True, moved=True),
            {"budget_id": "end-user-budget-ai-dev", "blocked": False},
            [("PATCH", {"blocked": True})],
        ),
        # Created by its first request on the default budget, not yet moved.
        (
            Target("t:ai", "end-user-budget-ai-dev", moved=True),
            {"budget_id": "end-user-budget-ai", "blocked": False},
            [("PATCH", {"budget_id": "end-user-budget-ai-dev"})],
        ),
        # Blocked in Otari since: never unblocked.
        (
            Target("t:ai", "end-user-budget-ai"),
            {"budget_id": "end-user-budget-ai", "blocked": True},
            [],
        ),
        # Not moved in LiteLLM, moved in Otari since: never moved back.
        (
            Target("t:ai", "end-user-budget-ai", blocked=True),
            {"budget_id": "end-user-budget-ai-dev", "blocked": True},
            [],
        ),
    ],
)
def test_an_existing_end_user_gets_only_what_it_lacks(target, current, expected):
    assert changes(target, current) == expected


def test_missing_claims_are_the_capped_identities_without_one():
    assert missing_claims(["a", "b", "b", "c"], {"a", "z"}) == ["b", "c"]


@pytest.fixture
async def client():
    service = OtariService()
    await service.connect()
    yield service.client
    await service.disconnect()


# The end user is named in the query: iOS ids often contain "/".
URL = httpx.URL(f"{OTARI_API_ROOT}/end-users", params={"external_id": "tester:ai"})
MOVED_AND_BLOCKED = Target("tester:ai", "end-user-budget-ai-dev", True, True)


async def test_an_end_user_already_right_is_only_read(client, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=URL,
        json=[{"budget_id": "end-user-budget-ai-dev", "blocked": True}],
    )

    outcome = await migrate_end_user(client, MOVED_AND_BLOCKED, apply=True)

    assert outcome.result == "unchanged"
    assert [r.method for r in httpx_mock.get_requests()] == ["GET"]


async def test_a_missing_end_user_is_put_on_its_budget_and_blocked(client, httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, json=[])
    httpx_mock.add_response(method="PUT", url=URL, status_code=201, json={})
    httpx_mock.add_response(method="PATCH", url=URL, json={})

    outcome = await migrate_end_user(client, MOVED_AND_BLOCKED, apply=True)

    assert outcome.result == "created"
    _get, put, patch = httpx_mock.get_requests()
    assert json.loads(put.content) == {"budget_id": "end-user-budget-ai-dev"}
    assert json.loads(patch.content) == {"blocked": True}


async def test_a_dry_run_only_reads(client, httpx_mock):
    httpx_mock.add_response(
        method="GET", url=URL, json=[{"budget_id": "end-user-budget-ai"}]
    )

    outcome = await migrate_end_user(client, MOVED_AND_BLOCKED)

    assert outcome.result == "updated"
    assert [r.method for r in httpx_mock.get_requests()] == ["GET"]


async def test_a_budget_the_key_may_not_assign_is_refused_and_named(client, httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, json=[])
    httpx_mock.add_response(
        method="PUT",
        url=URL,
        status_code=403,
        json={"detail": "reworded at will", "code": "end_user_budget_not_allowed"},
        headers={"Otari-Error-Code": "end_user_budget_not_allowed"},
    )

    outcome = await migrate_end_user(client, MOVED_AND_BLOCKED, apply=True)

    assert outcome.result == "refused"
    assert "end-user-budget-ai-dev" in outcome.detail


async def test_any_other_error_fails_without_raising(client, httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, status_code=500, text="boom")

    outcome = await migrate_end_user(client, MOVED_AND_BLOCKED, apply=True)

    assert outcome.result == "failed"
    assert outcome.detail.startswith("500")


async def test_a_network_error_fails_without_raising(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=URL)

    outcome = await migrate_end_user(client, MOVED_AND_BLOCKED, apply=True)

    assert outcome.result == "failed"
    assert "ConnectError" in outcome.detail


async def test_a_guest_is_written_with_the_guest_key(client, httpx_mock, monkeypatch):
    """Only the guest key may assign the guest budgets; reads stay on the service key."""
    monkeypatch.setattr(env, "OTARI_GUEST_SERVICE_KEY", "tk-guest")
    url = httpx.URL(f"{OTARI_API_ROOT}/end-users", params={"external_id": "g:ai-guest"})
    httpx_mock.add_response(method="GET", url=url, json=[])
    httpx_mock.add_response(method="PUT", url=url, status_code=201, json={})
    httpx_mock.add_response(method="PATCH", url=url, json={})

    target = Target("g:ai-guest", "end-user-budget-ai-guest", blocked=True)
    outcome = await migrate_end_user(client, target, apply=True)

    assert outcome.result == "created"
    get, put, patch = httpx_mock.get_requests()
    assert get.headers["Authorization"] != "Bearer tk-guest"
    assert put.headers["Authorization"] == "Bearer tk-guest"
    assert patch.headers["Authorization"] == "Bearer tk-guest"
