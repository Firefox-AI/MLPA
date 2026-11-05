"""
Carry LiteLLM's blocked and moved end users over to Otari, and check the signup cap's claims.

Reads LiteLLM_EndUserTable with the settings MLPA's litellm pool uses (DB_HOST,
DB_PORT, DB_USERNAME, DB_PASSWORD, LITELLM_DB_NAME), and writes to Otari's
end-user API at OTARI_API_BASE with OTARI_SERVICE_KEY, and guests with
OTARI_GUEST_SERVICE_KEY. GATEWAY_BACKEND does not
matter, so it runs before or after MLPA is switched.

- A blocked user is blocked in Otari, and a user on a budget other than its
  service type's is put on that budget. Users with neither are skipped: Otari
  creates them on their first request. --all creates every one now.
- Spend is not carried: every budget starts a fresh period in Otari.
- Every capped base identity in LiteLLM should hold a signup-cap claim. Missing
  ones are reported, and inserted with --fill-claims.

A dry run is the default: it reads LiteLLM, the claims and each target's end
user in Otari, and prints what --apply would change. Re-running changes nothing
already right, and it never unblocks a user or moves one back to its default
budget: whatever Otari holds beyond LiteLLM is left alone. The cutover is described in mozilla-ai/otari-firefox-pilot (docs/cutover.md).

    uv run python scripts/migrate_litellm_to_otari.py [--dry-run | --apply] [--all] [--fill-claims]
"""

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from mlpa.core.config import USER_CAPACITY_ID, env, is_guest_service_type
from mlpa.core.errors import otari_code
from mlpa.core.services.app_attest_pg_service import AppAttestPGService
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from mlpa.core.services.otari_service import OtariService

END_USER_BUDGET_NOT_ALLOWED = "end_user_budget_not_allowed"


@dataclass(frozen=True)
class Target:
    """What Otari should hold for one of LiteLLM's end users.

    ``budget_id`` is the budget a missing end user is created on. When ``moved``,
    an existing end user is put on it too; otherwise its budget is left alone.
    """

    user_id: str
    budget_id: str
    blocked: bool = False
    moved: bool = False


@dataclass
class Plan:
    """LiteLLM's end users, sorted into what to carry to Otari and what not to.

    ``default_budgets`` maps each service type to its budget id. With
    ``include_all``, every recognized end user is a target, so Otari creates the
    ones it does not have yet; otherwise only blocked and moved ones are.
    """

    default_budgets: Mapping[str, str]
    include_all: bool = False
    targets: list[Target] = field(default_factory=list)
    # End users with nothing to carry: Otari creates them on their first request.
    skipped: int = 0
    # (user_id, budget_id) on a budget no service type has: the move is not carried.
    unknown_budget: list[tuple[str, str]] = field(default_factory=list)
    # Ids that are not "<identity>:<service type>" for one of MLPA's service types.
    unrecognized: list[str] = field(default_factory=list)

    def add(self, user_id: str, budget_id: str | None, blocked: bool) -> None:
        _identity, _sep, service_type = user_id.partition(":")
        default = self.default_budgets.get(service_type)
        if default is None:
            self.unrecognized.append(user_id)
            return
        moved = False
        if budget_id is not None and budget_id != default:
            if budget_id in self.default_budgets.values():
                moved = True
            else:
                self.unknown_budget.append((user_id, budget_id))
        if blocked or moved or self.include_all:
            self.targets.append(
                Target(
                    user_id=user_id,
                    budget_id=budget_id if moved and budget_id else default,
                    blocked=blocked,
                    moved=moved,
                )
            )
        else:
            self.skipped += 1

    @property
    def blocked(self) -> list[Target]:
        return [t for t in self.targets if t.blocked]

    @property
    def moved(self) -> list[Target]:
        return [t for t in self.targets if t.moved]

    @property
    def plain(self) -> list[Target]:
        """Targets with nothing to carry, there only to be created (``include_all``)."""
        return [t for t in self.targets if not t.blocked and not t.moved]


def plan_end_users(
    rows: Iterable[Mapping[str, Any]],
    default_budgets: Mapping[str, str],
    include_all: bool = False,
) -> Plan:
    """A ``Plan`` for LiteLLM_EndUserTable rows (``user_id``, ``budget_id``, ``blocked``)."""
    plan = Plan(default_budgets, include_all)
    for row in rows:
        plan.add(row["user_id"], row.get("budget_id"), bool(row.get("blocked")))
    return plan


def changes(
    target: Target, current: Mapping[str, Any] | None
) -> list[tuple[str, dict[str, Any]]]:
    """The calls that bring Otari's end user (None when missing) to ``target``, in order.

    Empty when Otari already holds it. A missing end user is created on its
    budget with ``PUT``, then blocked; an existing one is blocked or moved with
    one ``PATCH``.
    """
    if current is None:
        calls: list[tuple[str, dict[str, Any]]] = [
            ("PUT", {"budget_id": target.budget_id})
        ]
        if target.blocked:
            calls.append(("PATCH", {"blocked": True}))
        return calls
    patch: dict[str, Any] = {}
    if target.moved and current.get("budget_id") != target.budget_id:
        patch["budget_id"] = target.budget_id
    if target.blocked and not current.get("blocked"):
        patch["blocked"] = True
    return [("PATCH", patch)] if patch else []


Result = Literal["unchanged", "created", "updated", "refused", "failed"]


@dataclass(frozen=True)
class Outcome:
    """What happened to one target. In a dry run, created and updated mean "would be"."""

    user_id: str
    result: Result
    detail: str = ""


def _end_user_params(user_id: str) -> dict[str, str]:
    """Names the end user as a query parameter: iOS ids often contain "/"."""
    return {"external_id": user_id}


def _is_guest(user_id: str) -> bool:
    return is_guest_service_type(user_id.partition(":")[2])


def _write_headers(user_id: str) -> dict[str, str]:
    """Guests are written with the guest key: only it may assign the guest budgets.

    Reads need no other key: both keys' end users belong to the same owner.
    """
    if _is_guest(user_id):
        return {"Authorization": f"Bearer {env.OTARI_GUEST_SERVICE_KEY}"}
    return {}


def _refusal(target: Target, response: httpx.Response) -> Outcome:
    code = otari_code(response.headers, response.text)
    if response.status_code == 403 and code == END_USER_BUDGET_NOT_ALLOWED:
        return Outcome(
            target.user_id,
            "refused",
            f"the key may not assign budget {target.budget_id!r}",
        )
    return Outcome(
        target.user_id,
        "failed",
        f"{response.status_code} {code or ''} {response.text[:200]}".strip(),
    )


async def migrate_end_user(
    client: httpx.AsyncClient, target: Target, apply: bool = False
) -> Outcome:
    """Bring Otari's end user to ``target``, reading it first; with ``apply`` False, only read.

    ``client`` is authenticated with MLPA's service key, rooted at Otari's
    ``/api/v1``. A budget the key may not assign is ``refused``, naming the
    budget; any other error is ``failed``. Neither raises.
    """
    params = _end_user_params(target.user_id)
    try:
        response = await client.get("/end-users", params=params)
        if not response.is_success:
            return _refusal(target, response)
        current = (response.json() or [None])[0]

        calls = changes(target, current)
        if not calls:
            return Outcome(target.user_id, "unchanged")
        result: Result = "created" if current is None else "updated"
        if not apply:
            return Outcome(target.user_id, result)
        for method, body in calls:
            response = await client.request(
                method,
                "/end-users",
                params=params,
                json=body,
                headers=_write_headers(target.user_id),
            )
            if not response.is_success:
                return _refusal(target, response)
        return Outcome(target.user_id, result)
    except httpx.HTTPError as e:
        return Outcome(target.user_id, "failed", f"{type(e).__name__}: {e}")


def missing_claims(capped_identities: Iterable[str], claimed: set[str]) -> list[str]:
    """Base identities LiteLLM has a capped end user for but the signup cap has no claim for."""
    return sorted(set(capped_identities) - claimed)


PAGE_SIZE = 10_000


async def litellm_end_users(litellm: LiteLLMPGService) -> AsyncIterator[dict]:
    """Every row of LiteLLM_EndUserTable, a page at a time in user_id order."""
    after = ""
    while True:
        async with litellm.statement_timeout(env.PG_ADMIN_READ_TIMEOUT_MS) as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, budget_id, blocked
                FROM "LiteLLM_EndUserTable"
                WHERE user_id > $1
                ORDER BY user_id
                LIMIT $2
                """,
                after,
                PAGE_SIZE,
            )
        if not rows:
            return
        for row in rows:
            yield dict(row)
        after = rows[-1]["user_id"]


def show(title: str, items: list, count: int) -> None:
    print(f"  {title}: {len(items)}")
    for item in items[:count]:
        print(f"    {item}")
    if len(items) > count:
        print(f"    ... and {len(items) - count} more")


def print_plan(plan: Plan, count: int) -> None:
    print("End users in LiteLLM:")
    show("to block", [t.user_id for t in plan.blocked], count)
    show("to move", [f"{t.user_id} -> {t.budget_id}" for t in plan.moved], count)
    if plan.include_all:
        show("to create (--all)", [t.user_id for t in plan.plain], count)
    else:
        print(f"  skipped, created by Otari on first use: {plan.skipped}")
    show(
        "on a budget no service type has (move NOT carried)",
        [f"{user_id} on {budget_id}" for user_id, budget_id in plan.unknown_budget],
        count,
    )
    show("not <identity>:<service type> (skipped)", plan.unrecognized, count)


async def check_budgets(otari: OtariService, plan: Plan) -> bool:
    """Say which budget the plan needs that its key may not assign; False if a key is refused.

    Guests are written with OTARI_GUEST_SERVICE_KEY, everyone else with
    OTARI_SERVICE_KEY, and each key lists only the budgets it may assign.
    """
    needed: dict[str, set[str]] = {}
    for target in plan.targets:
        name = (
            "OTARI_GUEST_SERVICE_KEY"
            if _is_guest(target.user_id)
            else "OTARI_SERVICE_KEY"
        )
        needed.setdefault(name, set()).add(target.budget_id)
    for name, budget_ids in sorted(needed.items()):
        key = getattr(env, name)
        if not key:
            print(f"Set {name}: the plan has its users", file=sys.stderr)
            return False
        response = await otari.client.get(
            "/end-user-budgets", headers={"Authorization": f"Bearer {key}"}
        )
        if not response.is_success:
            print(
                f"Otari refused /end-user-budgets ({response.status_code}): "
                f"check OTARI_API_BASE and {name}",
                file=sys.stderr,
            )
            return False
        assignable = {b["budget_id"] for b in response.json()}
        for budget_id in sorted(budget_ids - assignable):
            print(
                f"WARNING: {name} may not assign {budget_id!r}; its users will be refused. "
                "List it in the key's end_user_budget_ids in Otari's config.yml"
            )
    return True


async def migrate(
    otari: OtariService, targets: list[Target], apply: bool, concurrency: int
) -> list[Outcome]:
    """Migrate every target, ``concurrency`` at a time, printing progress to stderr."""
    pending = iter(targets)
    outcomes: list[Outcome] = []
    every = max(1, len(targets) // 20)

    async def worker() -> None:
        # One iterator shared by every worker: each target is taken once.
        for target in pending:
            outcomes.append(await migrate_end_user(otari.client, target, apply))
            if len(outcomes) % every == 0 or len(outcomes) == len(targets):
                print(f"  {len(outcomes)}/{len(targets)}", file=sys.stderr)

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return outcomes


async def claimed_identities(app_attest: AppAttestPGService) -> set[str]:
    async with app_attest.statement_timeout(
        env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS
    ) as conn:
        rows = await conn.fetch(
            "SELECT base_identity FROM mlpa_user_capacity_identities WHERE capacity_id = $1",
            USER_CAPACITY_ID,
        )
    return {row["base_identity"] for row in rows}


async def fill_claims(app_attest: AppAttestPGService, missing: Iterable[str]) -> None:
    """Insert the missing signed-in claims and recount their ``current_identities``, as startup's reconcile does.

    Guest claims are MLPA's own under either gateway, so they are left as they are.

    Under the capacity row's lock, which admissions and releases take too, so the
    count is the claim table's however many MLPAs are admitting meanwhile. An
    identity admitted since the check is not inserted twice.
    """
    async with app_attest.statement_timeout(
        env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS
    ) as conn:
        if not await conn.fetchval(
            "SELECT 1 FROM mlpa_user_capacity WHERE id = $1 FOR UPDATE",
            USER_CAPACITY_ID,
        ):
            raise RuntimeError(
                "mlpa_user_capacity has no row; start MLPA once to seed it"
            )
        await conn.execute(
            """
            INSERT INTO mlpa_user_capacity_identities (base_identity, capacity_id)
            SELECT unnest($1::text[]), $2
            ON CONFLICT (base_identity, capacity_id) DO NOTHING
            """,
            list(missing),
            USER_CAPACITY_ID,
        )
        row = await conn.fetchrow(
            """
            UPDATE mlpa_user_capacity
            SET current_identities = (
                    SELECT COUNT(*) FROM mlpa_user_capacity_identities
                    WHERE capacity_id = $1
                ),
                updated_at = NOW()
            WHERE id = $1
            RETURNING current_identities, max_identities
            """,
            USER_CAPACITY_ID,
        )
    print(
        f"  claims now {row['current_identities']} of {row['max_identities']}"
        + (
            " (over the cap: no new signups)"
            if row["current_identities"] >= row["max_identities"]
            else ""
        )
    )


async def check_claims(
    litellm: LiteLLMPGService,
    app_attest: AppAttestPGService,
    fill: bool,
    count: int,
) -> int:
    """Report capped base identities with no claim, inserting them with ``fill``; returns how many lacked one."""
    capped = await litellm.list_managed_base_identities(
        list(env.MLPA_CAPPED_SERVICE_TYPES)
    )
    claimed = await claimed_identities(app_attest)
    missing = missing_claims(capped, claimed)
    print(
        f"Signup cap ({', '.join(sorted(env.MLPA_CAPPED_SERVICE_TYPES))}): "
        f"{len(set(capped))} base identities in LiteLLM, {len(claimed)} claims"
    )
    show("without a claim", missing, count)
    print(
        f"  claims with no capped end user in LiteLLM (admitted since, or freed): "
        f"{len(claimed - set(capped))}"
    )
    if missing and fill:
        await fill_claims(app_attest, missing)
    return len(missing)


def print_summary(outcomes: list[Outcome], apply: bool, count: int) -> bool:
    """Print what happened; True when nothing failed or was refused."""
    results = Counter(o.result for o in outcomes)
    prefix = "" if apply else "would be "
    print("Summary:" if apply else "Summary (dry run, nothing written):")
    print(f"  unchanged: {results['unchanged']}")
    print(f"  {prefix}created: {results['created']}")
    print(f"  {prefix}updated: {results['updated']}")
    problems = [o for o in outcomes if o.result in ("refused", "failed")]
    show(
        "refused",
        [f"{o.user_id}: {o.detail}" for o in problems if o.result == "refused"],
        count,
    )
    show(
        "failed",
        [f"{o.user_id}: {o.detail}" for o in problems if o.result == "failed"],
        count,
    )
    return not problems


async def main(args: argparse.Namespace) -> int:
    if not env.OTARI_SERVICE_KEY:
        print("Set OTARI_SERVICE_KEY (and OTARI_API_BASE)", file=sys.stderr)
        return 2
    default_budgets = {
        service_type: config["budget_id"]
        for service_type, config in env.service_type_config.items()
    }
    litellm = LiteLLMPGService()
    app_attest = AppAttestPGService(litellm)
    otari = OtariService()
    await litellm.connect()
    await app_attest.connect()
    await otari.connect()
    try:
        if not args.apply:
            print("DRY RUN: nothing is written; pass --apply to write.\n")
        plan = Plan(default_budgets, args.all)
        async for row in litellm_end_users(litellm):
            plan.add(row["user_id"], row["budget_id"], bool(row["blocked"]))
        print_plan(plan, args.show)

        if not await check_budgets(otari, plan):
            return 2
        print(
            f"\nReading{' and writing' if args.apply else ''} {len(plan.targets)} end users in Otari:"
        )
        outcomes = await migrate(otari, plan.targets, args.apply, args.concurrency)

        print()
        missing = 0
        if not args.skip_claims:
            missing = await check_claims(
                litellm, app_attest, args.apply and args.fill_claims, args.show
            )
            print()
        ok = print_summary(outcomes, args.apply, args.show)
        if missing and not (args.apply and args.fill_claims):
            print(
                f"  signup claims missing: {missing} (--apply --fill-claims inserts them)"
            )
        return 0 if ok else 1
    finally:
        await otari.disconnect()
        await app_attest.disconnect()
        await litellm.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="read only and print what --apply would change (the default)",
    )
    mode.add_argument("--apply", action="store_true", help="write to Otari")
    parser.add_argument(
        "--all",
        action="store_true",
        help="also create every other end user now, rather than on its first request",
    )
    parser.add_argument(
        "--fill-claims",
        action="store_true",
        help="with --apply, insert the signup-cap claims LiteLLM's capped users lack",
    )
    parser.add_argument(
        "--skip-claims",
        action="store_true",
        help="do not check the signup cap's claims",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="end users read or written in Otari at once (default 8)",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=10,
        help="how many of each kind to list (default 10)",
    )
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    sys.exit(asyncio.run(main(args)))
