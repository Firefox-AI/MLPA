from mlpa.core.services.app_attest_pg_service import AppAttestPGService
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from mlpa.core.services.redis_service import RedisService

litellm_pg = LiteLLMPGService()
app_attest_pg = AppAttestPGService(litellm_pg)
redis_service = RedisService()
