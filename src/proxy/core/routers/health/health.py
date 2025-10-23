import httpx
from fastapi import APIRouter

from ...config import GATEWAY_HEADERS, GATEWAY_READINESS_URL
from ...pg_services.services import app_attest_pg, gateway_pg

router = APIRouter()


@router.get("/liveness", tags=["Health"])
async def liveness_probe():
	return {"status": "alive"}


@router.get("/readiness", tags=["Health"])
async def readiness_probe():
	pg_status = gateway_pg.check_status()
	app_attest_pg_status = app_attest_pg.check_status()
	gateway_status = {}
	async with httpx.AsyncClient() as client:
		response = await client.get(
			GATEWAY_READINESS_URL, headers=GATEWAY_HEADERS, timeout=3
		)
		data = response.json()
		gateway_status = data
	return {
		"status": "connected",
		"pg_server_dbs": {
			"postgres": "connected" if pg_status else "offline",
			"app_attest": "connected" if app_attest_pg_status else "offline",
		},
		"any_llm_gateway": gateway_status,
	}
