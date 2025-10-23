from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Env(BaseSettings):
	DEBUG: bool = False
	METRICS_LOG_FILE: str = "metrics.jsonl"

	# PostgreSQL url (no /database)
	PG_DB_URL: str = "postgresql://gateway:gateway@localhost:5432"

	# any-llm-gateway
	MASTER_KEY: str = "sk-default"
	OPENAI_API_KEY: str = "sk-add-your-key"
	GATEWAY_API_BASE: str = "http://localhost:8000"
	GATEWAY_DB_NAME: str = "gateway"
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

	model_config = ConfigDict(env_file=".env")


env = Env()

GATEWAY_READINESS_URL = f"{env.GATEWAY_API_BASE}/health/readiness"
GATEWAY_COMPLETIONS_URL = f"{env.GATEWAY_API_BASE}/v1/chat/completions"
GATEWAY_USERS_URL = f"{env.GATEWAY_API_BASE}/v1/users"
GATEWAY_HEADERS = {
	"Content-Type": "application/json",
	"X-AnyLLM-Key": f"Bearer {env.MASTER_KEY}",
}
