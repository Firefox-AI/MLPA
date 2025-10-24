from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Env(BaseSettings):
	DEBUG: bool = False
	METRICS_LOG_FILE: str = "metrics.jsonl"

	# PostgreSQL url (no /database)
	PG_DB_URL: str = "postgresql://litellm:litellm@localhost:5432"

	# LiteLLM
	MASTER_KEY: str = "sk-default"
	OPENAI_API_KEY: str = "sk-add-your-key"
	LITELLM_API_BASE: str = "http://localhost:4000"
	LITELLM_DB_NAME: str = "litellm"
	CHALLENGE_EXPIRY_SECONDS: int = 300  # 5 minutes
	PORT: int | None = 8080

	# App Attest
	APP_BUNDLE_ID: str = "org.example.app"
	APP_DEVELOPMENT_TEAM: str = "TEAMID1234"
	APP_ATTEST_DB_NAME: str = "app_attest"

	# FxA
	CLIENT_ID: str = "default-client-id"
	CLIENT_SECRET: str = "default-client-secret"

	# LLM request default values
	MODEL_NAME: str = "gpt-4"
	TEMPERATURE: float = 0.1
	MAX_COMPLETION_TOKENS: int = 1024
	TOP_P: float = 0.01

	# Sentry
	SENTRY_DSN: str = ""

	# Mock settings
	MOCK_TTFT_MS: int = 200  # time to first token
	MOCK_STREAMING_CHUNK_LATENCY_MS: int = (
		50  # latency between streaming chunks (50 corresponds to ~20 stream chunks/sec)
	)

	model_config = ConfigDict(env_file=".env")


env = Env()

LITELLM_READINESS_URL = f"{env.LITELLM_API_BASE}/health/readiness"
LITELLM_COMPLETIONS_URL = f"{env.LITELLM_API_BASE}/v1/chat/completions"
LITELLM_HEADERS = {
	"Content-Type": "application/json",
	"X-LiteLLM-Key": f"Bearer {env.MASTER_KEY}",
}
