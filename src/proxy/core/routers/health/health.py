import httpx
from fastapi import APIRouter

from proxy.core.config import LITELLM_HEADERS, LITELLM_READINESS_URL
from proxy.core.pg_services.services import app_attest_pg, litellm_pg

router = APIRouter()


@router.get("/liveness", tags=["Health"])
async def liveness_probe():
	return {"status": "alive"}


@router.get("/readiness", tags=["Health"])
async def readiness_probe():
	# todo add check to PG and LiteLLM status here
	pg_status = litellm_pg.check_status()
	app_attest_pg_status = app_attest_pg.check_status()
	litellm_status = {}
	async with httpx.AsyncClient() as client:
		response = await client.get(
			LITELLM_READINESS_URL, headers=LITELLM_HEADERS, timeout=3
		)
		data = response.json()
		litellm_status = data
	return {
		"status": "connected",
		"pg_server_dbs": {
			"postgres": "connected" if pg_status else "offline",
			"app_attest": "connected" if app_attest_pg_status else "offline",
		},
		"litellm": litellm_status,
	}
