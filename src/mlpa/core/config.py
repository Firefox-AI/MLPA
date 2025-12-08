from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Env(BaseSettings):
    MLPA_DEBUG: bool = False
    APP_ATTEST_PRODUCTION: bool = False
    PORT: int | None = 8080

    # LiteLLM
    MASTER_KEY: str = "sk-default"  # Bypasses LiteLLM.max_budget, use MLPA_VIRTUAL_KEY (virtual key) for completion requests
    MLPA_VIRTUAL_KEY: str = "sk-virtual"  # Enforces LiteLLM.max_budget
    OPENAI_API_KEY: str = "sk-add-your-key"
    LITELLM_API_BASE: str = "http://localhost:4000"
    CHALLENGE_EXPIRY_SECONDS: int = 300  # 5 minutes

    # User Feature Budget - AI service type
    USER_FEATURE_BUDGET_AI_BUDGET_ID: str = "end-user-budget-ai"
    USER_FEATURE_BUDGET_AI_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_AI_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_AI_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_AI_BUDGET_DURATION: str = "1m"

    # User Feature Budget - S2S service type
    USER_FEATURE_BUDGET_S2S_BUDGET_ID: str = "end-user-budget-s2s"
    USER_FEATURE_BUDGET_S2S_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_S2S_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_S2S_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_S2S_BUDGET_DURATION: str = "1d"

    @property
    def user_feature_budget(self) -> dict[str, dict]:
        """
        User feature budget configuration by service type.
        Returns a nested dictionary with service types (ai, s2s) as keys.
        Constructed from individual environment variables.
        """
        return {
            "ai": {
                "budget_id": self.USER_FEATURE_BUDGET_AI_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_AI_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AI_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AI_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_AI_BUDGET_DURATION,
            },
            "s2s": {
                "budget_id": self.USER_FEATURE_BUDGET_S2S_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_S2S_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_S2S_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_S2S_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_S2S_BUDGET_DURATION,
            },
        }

    @property
    def valid_service_types(self) -> list[str]:
        """
        Returns a list of valid service types from user_feature_budget configuration.
        """
        return list(self.user_feature_budget.keys())

    # Logging
    LOG_JSON: bool = False  # Set to True for GKE deployment
    LOGURU_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/mlpa.log"
    LOG_ROTATION: str = "500 MB"
    LOG_COMPRESSION: str = "zip"
    HTTPX_LOGGING: bool = True
    ASYNCPG_LOGGING: bool = True

    # App Attest
    APP_BUNDLE_ID: str = "org.example.app"
    APP_DEVELOPMENT_TEAM: str = "TEAMID1234"
    # NOTE: Should be False in production
    # only use it for local testing
    APP_ATTEST_QA: bool = False  # Set to True to use QA test certificates
    APP_ATTEST_QA_CERT_DIR: str = "./qa_certificates"
    APP_ATTEST_QA_BUCKET: str | None = None
    APP_ATTEST_QA_BUCKET_PREFIX: str | None = None
    APP_ATTEST_QA_GCP_PROJECT_ID: str | None = None

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
    PG_POOL_MIN_SIZE: int = 1
    PG_POOL_MAX_SIZE: int = 10

    # LLM request default values
    MODEL_NAME: str = "openai/gpt-4o"
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
LITELLM_MASTER_AUTH_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {env.MASTER_KEY}",
}

LITELLM_COMPLETION_AUTH_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {env.MLPA_VIRTUAL_KEY}",
}

ERROR_CODE_BUDGET_LIMIT_EXCEEDED: int = 1
ERROR_CODE_RATE_LIMIT_EXCEEDED: int = 2

RATE_LIMIT_ERROR_RESPONSE = {
    429: {
        "description": "Too Many Requests - Budget or rate limit exceeded",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": "Error code: 1 for budget limit exceeded, 2 for rate limit exceeded",
                        }
                    },
                    "required": ["error"],
                },
                "examples": {
                    "budget_exceeded": {
                        "summary": "Budget limit exceeded",
                        "value": {"error": ERROR_CODE_BUDGET_LIMIT_EXCEEDED},
                        "description": "Budget limit exceeded. Check Retry-After header (86400 seconds = 1 day).",
                    },
                    "rate_limit_exceeded": {
                        "summary": "Rate limit exceeded",
                        "value": {"error": ERROR_CODE_RATE_LIMIT_EXCEEDED},
                        "description": "Rate limit exceeded (TPM/RPM). Check Retry-After header (60 seconds).",
                    },
                },
            }
        },
        "headers": {
            "Retry-After": {
                "description": "Number of seconds to wait before retrying",
                "schema": {"type": "string", "example": "86400"},
            }
        },
    }
}
