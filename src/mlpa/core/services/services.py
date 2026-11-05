from mlpa.core.config import USE_OTARI
from mlpa.core.services.app_attest_pg_service import AppAttestPGService
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from mlpa.core.services.otari_service import OtariService
from mlpa.core.services.redis_service import RedisService

# With GATEWAY_BACKEND=otari, users live in Otari and are reached through its API;
# OtariService answers every call the LiteLLM database service does.
litellm_pg: LiteLLMPGService | OtariService = (
    OtariService() if USE_OTARI else LiteLLMPGService()
)
app_attest_pg = AppAttestPGService(litellm_pg)
redis_service = RedisService()
