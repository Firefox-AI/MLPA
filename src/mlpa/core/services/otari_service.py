"""MLPA's users, budgets and limits kept in Otari, through Otari's end-user API.

A drop-in for ``LiteLLMPGService`` when ``GATEWAY_BACKEND=otari``: the same
method names and return shapes, so the user router, the signup cap and startup
call it unchanged.

How MLPA's model maps onto Otari's:

- One Otari service key, owned by the Otari user ``OTARI_OWNER_USER``, serves
  every service type. A request's ``user`` ("<identity>:<service type>") names an
  end user of that owner, which Otari creates on its first request on the budget
  the ``Otari-End-User-Budget`` header names: the service type's.
- Each service type's budget is an Otari budget under MLPA's own budget id, which
  the key lists among the budgets it may assign.
- Each service type's per-user RPM and TPM are that budget's ``rpm_limit`` and
  ``tpm_limit``, so moving a user to another budget moves its limits too.
- The service key also reads, blocks and moves its own end users
  (``/api/v1/end-users``), so MLPA holds no other Otari credential.

Otari's config.yml declares the budgets and the key (``budgets:`` and
``api_keys:``), and Otari applies them at every start. MLPA itself only reads
them.
"""

from collections import OrderedDict
from typing import Any

import httpx
from fastapi import HTTPException

from mlpa.core.config import (
    LITELLM_READINESS_URL,
    OTARI_API_ROOT,
    env,
)
from mlpa.core.logger import logger


class OtariService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._known_users: OrderedDict[str, None] = OrderedDict()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("OtariService is not connected")
        return self._client

    async def connect(self) -> None:
        # The service key manages the end users it bills on its own, so MLPA needs
        # no other credential for Otari.
        self._client = httpx.AsyncClient(
            base_url=OTARI_API_ROOT,
            headers={"Authorization": f"Bearer {env.OTARI_SERVICE_KEY}"},
            timeout=httpx.Timeout(env.PG_ADMIN_READ_TIMEOUT_MS / 1000),
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ping(self) -> bool:
        try:
            response = await self.client.get(
                LITELLM_READINESS_URL, timeout=env.READINESS_CHECK_TIMEOUT_S
            )
        except Exception:
            return False
        return response.status_code == 200

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    # Users

    def remember(self, user_id: str) -> None:
        """Note that ``user_id`` exists in Otari, so its next request skips the lookup."""
        self._known_users[user_id] = None
        self._known_users.move_to_end(user_id)
        while len(self._known_users) > env.OTARI_KNOWN_USERS_CACHE_SIZE:
            self._known_users.popitem(last=False)

    def is_known(self, user_id: str) -> bool:
        return user_id in self._known_users

    @staticmethod
    def _as_mlpa_user(user: dict[str, Any]) -> dict[str, Any]:
        """An Otari end user in the shape MLPA read from LiteLLM_EndUserTable."""
        return {
            "user_id": user.get("external_id"),
            "otari_user_id": user.get("user_id"),
            "budget_id": user.get("budget_id"),
            "blocked": bool(user.get("blocked")),
            "spend": user.get("spend", 0.0),
            "budget_started_at": user.get("budget_started_at"),
            "next_budget_reset_at": user.get("next_budget_reset_at"),
            "created_at": user.get("created_at"),
        }

    async def _end_user(
        self, method: str, user_id: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        """One call on the end user MLPA names ``user_id``, or None when Otari has none.

        The end user is named by the ``external_id`` query parameter rather than
        in the path, since iOS ids (App Attest key ids, in standard base64) often
        contain "/". A read is the list filtered to that one id.
        """
        params = {"external_id": user_id}
        if method == "GET":
            users = await self._request("GET", "/end-users", params=params)
            return users[0] if users else None
        response = await self.client.request(
            method, "/end-users", params=params, **kwargs
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        user = await self._end_user("GET", user_id)
        if user is None:
            return None
        self.remember(user_id)
        return self._as_mlpa_user(user)

    async def update_user_budget(self, user_id: str, budget_id: str) -> dict:
        updated = await self._end_user("PATCH", user_id, json={"budget_id": budget_id})
        if updated is None:
            raise HTTPException(status_code=404, detail="User not found.")
        logger.info(f"User {user_id} budget updated to {budget_id} successfully.")
        return self._as_mlpa_user(updated)

    async def block_user(self, user_id: str, blocked: bool = True) -> dict:
        updated = await self._end_user("PATCH", user_id, json={"blocked": blocked})
        if updated is None:
            raise HTTPException(status_code=404, detail="User not found.")
        logger.info(
            f"User {user_id} {'blocked' if blocked else 'unblocked'} successfully."
        )
        return self._as_mlpa_user(updated)

    async def list_users(self, limit: int = 50, offset: int = 0) -> dict:
        response = await self.client.get(
            "/end-users",
            params={"skip": offset, "limit": limit, "include_total": True},
        )
        response.raise_for_status()
        return {
            "users": [self._as_mlpa_user(user) for user in response.json()],
            "total": int(response.headers["otari-total-count"]),
            "limit": limit,
            "offset": offset,
        }

    async def count_users_by_service_type(self) -> dict:
        """Users per service type, counted as the users on each service type's budget.

        Otari counts users per budget, so a user moved to another service type's
        budget (a tester on ai-dev) counts under that one, where LiteLLM counted it
        under the service type in its id.
        """
        budgets = await self._request("GET", "/end-user-budgets")
        users_on = {b["budget_id"]: b["end_user_count"] for b in budgets}
        counts = {
            service_type: users_on.get(config["budget_id"], 0)
            for service_type, config in env.service_type_config.items()
        }
        counts = {st: count for st, count in counts.items() if count}
        return {"service_type_counts": counts, "total_users": sum(counts.values())}

    async def create_budget(self) -> None:
        """Say which service type's budget Otari's config does not give MLPA's key.

        Called at startup in place of the LiteLLM budget upsert, and read-only:
        Otari's config.yml is what declares budgets and the key.
        """
        try:
            budgets = await self._request("GET", "/end-user-budgets")
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Otari refused MLPA's service key ({e.response.status_code}); "
                "declare it in Otari's config.yml and set OTARI_SERVICE_KEY to its secret"
            )
            return
        except Exception as e:
            logger.error(f"Error reading MLPA's budgets from Otari: {e}")
            return
        assignable = {budget["budget_id"] for budget in budgets}
        for service_type, config in env.service_type_config.items():
            if config["budget_id"] not in assignable:
                logger.error(
                    f"MLPA's Otari key may not assign budget {config['budget_id']!r} "
                    f"(service type {service_type}); declare it and list it in the "
                    "key's end_user_budget_ids in Otari's config.yml"
                )
