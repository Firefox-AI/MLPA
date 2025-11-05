from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Env(BaseSettings):
    MLPA_DEBUG: bool = False
    PORT: int | None = 8080

    # LiteLLM
    MASTER_KEY: str = "sk-default"
    OPENAI_API_KEY: str = "sk-add-your-key"
    LITELLM_API_BASE: str = "http://localhost:4000"
    CHALLENGE_EXPIRY_SECONDS: int = 300  # 5 minutes

    # Logging
    LOG_JSON: bool = False  # Set to True for GKE deployment
    LOGURU_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/mlpa.log"
    LOG_ROTATION: str = "500 MB"
    LOG_COMPRESSION: str = "zip"
    HTTPX_LOGGING: bool = True
    ASYNCPG_LOGGING: bool = True
    MEMORY_PROFILING: bool = False

    # App Attest
    APP_BUNDLE_ID: str = "org.example.app"
    APP_DEVELOPMENT_TEAM: str = "TEAMID1234"

    # FxA
    CLIENT_ID: str = "default-client-id"
    CLIENT_SECRET: str = "default-client-secret"

    # PostgreSQL
    LITELLM_DB_NAME: str = "litellm"
    APP_ATTEST_DB_NAME: str = "app_attest"
    DB_USERNAME: str = "litellm"
    DB_PASSWORD: str = "litellm"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432

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
    PG_DB_URL: str | None = None

    model_config = ConfigDict(env_file=".env")

    def __init__(self):
        super().__init__()
        self.PG_DB_URL = f"postgresql://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}"


env = Env()

LITELLM_READINESS_URL = f"{env.LITELLM_API_BASE}/health/readiness"
LITELLM_COMPLETIONS_URL = f"{env.LITELLM_API_BASE}/v1/chat/completions"
LITELLM_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {env.MASTER_KEY}",
}
