from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    MLPA_DEBUG: bool = False
    APP_ATTEST_PRODUCTION: bool = False
    PORT: int = 8080

    # User signup capacity gate (managed service types only)
    # Controlled by infra (env var), enforced without race conditions in DB.
    MLPA_MAX_SIGNED_IN_USERS: int = 1000000
    MLPA_ENFORCE_SIGNIN_CAP: bool = False
    # Service types that share admission capacity.
    # Example: `ai` and `memories` are coupled; `s2s`/`s2s-android` can bypass.
    MLPA_CAPPED_SERVICE_TYPES: set[str] = {"ai", "memories"}
    MLPA_ADMISSION_LOCK_TIMEOUT_MS: int = 5000

    # Purpose header enforcement/backwards-compatibility:
    # when false (default), the `purpose` header is optional for all service types.
    # when true, the `purpose` header becomes mandatory for service types that
    # have a non-empty configured purpose allowlist.
    MLPA_REQUIRE_PURPOSE_HEADER: bool = False

    # LiteLLM
    MASTER_KEY: str = "sk-default"  # Bypasses LiteLLM.max_budget, use MLPA_VIRTUAL_KEY (virtual key) for completion requests
    # Read-only admin dashboard (`/admin`) and GET /user/counts-by-service-type; not LiteLLM's master key
    MLPA_UI_ACCESS_KEY: str = "sk-ui-access-default"
    MLPA_VIRTUAL_KEY: str = "sk-virtual"  # Enforces LiteLLM.max_budget

    OPENAI_API_KEY: str = "sk-add-your-key"  # for local LiteLLM testing
    EXA_API_KEY: str = "sk-add-your-key"  # for local LiteLLM testing

    LITELLM_API_BASE: str = "http://localhost:4000"
    CHALLENGE_EXPIRY_SECONDS: int = 300  # 5 minutes

    # User Feature Budget - AI service type
    USER_FEATURE_BUDGET_AI_BUDGET_ID: str = "end-user-budget-ai"
    USER_FEATURE_BUDGET_AI_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_AI_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_AI_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_AI_BUDGET_DURATION: str = "1d"

    # User Feature Budget - S2S service type
    USER_FEATURE_BUDGET_S2S_BUDGET_ID: str = "end-user-budget-s2s"
    USER_FEATURE_BUDGET_S2S_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_S2S_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_S2S_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_S2S_BUDGET_DURATION: str = "1d"

    # User Feature Budget - S2S Android service type (same values as s2s)
    USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_ID: str = "end-user-budget-s2s-android"
    USER_FEATURE_BUDGET_S2S_ANDROID_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_S2S_ANDROID_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_S2S_ANDROID_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_DURATION: str = "1d"

    # User Feature Budget - memories service type
    USER_FEATURE_BUDGET_MEMORIES_BUDGET_ID: str = "end-user-budget-memories"
    USER_FEATURE_BUDGET_MEMORIES_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_MEMORIES_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_MEMORIES_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_MEMORIES_BUDGET_DURATION: str = "1d"

    # User Feature Budget - ai-dev service type (experimentation, batch predictions)
    USER_FEATURE_BUDGET_AI_DEV_BUDGET_ID: str = "end-user-budget-ai-dev"
    USER_FEATURE_BUDGET_AI_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_AI_DEV_RPM_LIMIT: int = 200
    USER_FEATURE_BUDGET_AI_DEV_TPM_LIMIT: int = 10000
    USER_FEATURE_BUDGET_AI_DEV_BUDGET_DURATION: str = "1d"

    # User Feature Budget - memories-dev service type (experimentation)
    USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_ID: str = "end-user-budget-memories-dev"
    USER_FEATURE_BUDGET_MEMORIES_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_MEMORIES_DEV_RPM_LIMIT: int = 50
    USER_FEATURE_BUDGET_MEMORIES_DEV_TPM_LIMIT: int = 5000
    USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_DURATION: str = "1d"

    USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_ID: str = "end-user-budget-mochi-dev"
    USER_FEATURE_BUDGET_MOCHI_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_MOCHI_DEV_RPM_LIMIT: int = 200
    USER_FEATURE_BUDGET_MOCHI_DEV_TPM_LIMIT: int = 10000
    USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_DURATION: str = "1d"

    USER_FEATURE_BUDGET_SEARCH_BUDGET_ID: str = "end-user-budget-search"
    USER_FEATURE_BUDGET_SEARCH_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_SEARCH_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_SEARCH_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_SEARCH_BUDGET_DURATION: str = "1d"

    @cached_property
    def user_feature_budget(self) -> dict[str, dict]:
        """
        User feature budget configuration by service type.
        Returns a nested dictionary with service types (ai, s2s, s2s-android, memories, ai-dev, memories-dev, mochi-dev) as keys.
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
            "s2s-android": {
                "budget_id": self.USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_S2S_ANDROID_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_S2S_ANDROID_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_S2S_ANDROID_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_DURATION,
            },
            "memories": {
                "budget_id": self.USER_FEATURE_BUDGET_MEMORIES_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_MEMORIES_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_MEMORIES_BUDGET_DURATION,
            },
            "ai-dev": {
                "budget_id": self.USER_FEATURE_BUDGET_AI_DEV_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_AI_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AI_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AI_DEV_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_AI_DEV_BUDGET_DURATION,
            },
            "memories-dev": {
                "budget_id": self.USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_MEMORIES_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_DEV_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_DURATION,
            },
            "mochi-dev": {
                "budget_id": self.USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_MOCHI_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MOCHI_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MOCHI_DEV_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_DURATION,
            },
            "search": {
                "budget_id": self.USER_FEATURE_BUDGET_SEARCH_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_SEARCH_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_SEARCH_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_SEARCH_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_SEARCH_BUDGET_DURATION,
            },
        }

    @cached_property
    def valid_service_types(self) -> list[str]:
        """
        Returns a list of valid service types from user_feature_budget configuration.
        """
        return list(self.user_feature_budget.keys())

    @cached_property
    def service_type_purposes(self) -> dict[str, list[str]]:
        """
        Valid purpose values per service type (for the purpose header and Prometheus).
        AI-related types: chat, title-generation, convo-starters-sidebar.
        Memories types: memory-generation.
        s2s types: no purposes (empty list → purpose header not required, use empty value).
        """
        ai_purposes = ["chat", "title-generation", "convo-starters-sidebar"]
        memories_purposes = ["memory-generation"]
        return {
            "ai": ai_purposes,
            "ai-dev": ai_purposes,
            "mochi-dev": ai_purposes,
            "memories": memories_purposes,
            "memories-dev": memories_purposes,
            "s2s": [],
            "s2s-android": [],
            "search": [],
        }

    def valid_purposes_for_service_type(self, service_type: str) -> list[str]:
        """Return valid purpose values for a service type (empty if purpose not used)."""
        return self.service_type_purposes.get(service_type, [])

    def service_type_requires_purpose(self, service_type: str) -> bool:
        """True if the purpose header is mandatory for this service type."""
        return len(self.valid_purposes_for_service_type(service_type)) > 0

    @cached_property
    def forced_model_service_type_pairs(self) -> dict[str, list[str]]:
        """
        Returns a dictionary mapping model names to their valid service types.
        """
        # Force certain models to use certain service types
        return {"exa": ["search"]}

    def valid_service_type_for_model(self, service_type: str, model: str) -> bool:
        """Check if a service type is valid for a specific model."""
        valid_service_types = self.forced_model_service_type_pairs.get(model)
        if valid_service_types is None:
            return True  # Return true if not explicitly configured above
        return service_type in valid_service_types

    # Logging
    LOG_JSON: bool = False  # Set to True for GKE deployment
    LOGURU_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/mlpa.log"
    LOG_ROTATION: str = "500 MB"
    LOG_COMPRESSION: str = "zip"
    HTTPX_LOGGING: bool = True
    ASYNCPG_LOGGING: bool = True

    # App Attest
    APP_DEVELOPMENT_TEAM: str = "TEAMID1234"
    # NOTE: Should be False in production
    # only use it for local testing
    APP_ATTEST_QA: bool = False  # Set to True to use QA test certificates
    APP_ATTEST_QA_CERT_DIR: str = "./qa_certificates"
    APP_ATTEST_QA_BUCKET: str | None = None
    APP_ATTEST_QA_BUCKET_PREFIX: str | None = None
    APP_ATTEST_QA_GCP_PROJECT_ID: str | None = None

    # Play Integrity
    PLAY_INTEGRITY_PACKAGE_NAME: str = "org.mozilla.firefox"
    PLAY_INTEGRITY_REQUEST_TIMEOUT_SECONDS: int = 30
    PLAY_INTEGRITY_QUOTA_PROJECT: str | None = None
    ALLOWED_PACKAGE_NAMES: set[str] = {
        "org.mozilla.fenix",
        "org.mozilla.fenix.debug",
        "org.mozilla.firefox",
        "org.mozilla.firefox_beta",
    }

    # Access token
    MLPA_ACCESS_TOKEN_SECRET: str = "mlpa-dev-secret"
    MLPA_ACCESS_TOKEN_TTL_SECONDS: int = 86400

    # Dev / experimentation auth (x-dev-authorization header)
    MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN: str = "secret-dev-token"

    # FxA
    CLIENT_ID: str = "default-client-id"
    CLIENT_SECRET: str = "default-client-secret"
    ADDITIONAL_FXA_SCOPE_1: str | None = None
    ADDITIONAL_FXA_SCOPE_2: str | None = None
    ADDITIONAL_FXA_SCOPE_3: str | None = None

    # PostgreSQL
    LITELLM_DB_NAME: str = "litellm"
    APP_ATTEST_DB_NAME: str = "app_attest"
    DB_USERNAME: str = "litellm"
    DB_PASSWORD: str = "litellm"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    PG_POOL_MIN_SIZE: int = 1
    PG_POOL_MAX_SIZE: int = 10
    PG_PREPARED_STMT_CACHE_MAX_SIZE: int = 100

    # LLM request default values
    MODEL_NAME: str = "openai/gpt-4o"
    TEMPERATURE: float = 0.1
    MAX_COMPLETION_TOKENS: int = 8192
    TOP_P: float = 0.01
    MAX_REQUEST_SIZE_BYTES: int = 3 * 1024 * 1024  # 3 MB default

    # Request Timeouts (in seconds)
    STREAMING_TIMEOUT_SECONDS: int = 300
    HTTPX_CONNECT_TIMEOUT_SECONDS: int = 30
    HTTPX_READ_TIMEOUT_SECONDS: int = 30
    HTTPX_WRITE_TIMEOUT_SECONDS: int = 30
    HTTPX_POOL_TIMEOUT_SECONDS: int = 5
    HTTPX_MAX_CONNECTIONS: int = 200
    HTTPX_MAX_KEEPALIVE_CONNECTIONS: int = 50
    HTTPX_KEEPALIVE_EXPIRY_SECONDS: int = 15

    # Security Headers
    SECURITY_HEADERS_ENABLED: bool = True
    HSTS_MAX_AGE: int = 31536000  # 1 year in seconds - a standard value
    HSTS_INCLUDE_SUBDOMAINS: bool = True

    # Sentry
    SENTRY_DSN: str = ""

    # Mock settings
    MOCK_TTFT_MS: int = 200  # time to first token
    MOCK_STREAMING_CHUNK_LATENCY_MS: int = (
        50  # latency between streaming chunks (50 corresponds to ~20 stream chunks/sec)
    )
    PG_DB_URL: str | None = None

    model_config = SettingsConfigDict(env_file=".env")

    def __init__(self):
        super().__init__()
        self.PG_DB_URL = f"postgresql://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}"


env = Env()

LITELLM_READINESS_URL = f"{env.LITELLM_API_BASE}/health/readiness"
LITELLM_COMPLETIONS_URL = f"{env.LITELLM_API_BASE}/v1/chat/completions"
LITELLM_SEARCH_URL = f"{env.LITELLM_API_BASE}/v1/search"
LITELLM_MASTER_AUTH_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {env.MASTER_KEY}",
}

LITELLM_VIRTUAL_AUTH_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {env.MLPA_VIRTUAL_KEY}",
}

# LiteLLM proxy response headers (lowercase names for httpx Headers.get)
# https://docs.litellm.ai/docs/proxy/response_headers
LITELLM_HEADER_MODEL_API_BASE = "x-litellm-model-api-base"
LITELLM_HEADER_ATTEMPTED_FALLBACKS = "x-litellm-attempted-fallbacks"
LITELLM_HEADER_ATTEMPTED_RETRIES = "x-litellm-attempted-retries"
LITELLM_HEADER_RESPONSE_DURATION_MS = "x-litellm-response-duration-ms"
LITELLM_HEADER_RESPONSE_COST = "x-litellm-response-cost"

ERROR_CODE_BUDGET_LIMIT_EXCEEDED: int = 1
ERROR_CODE_RATE_LIMIT_EXCEEDED: int = 2
ERROR_CODE_REQUEST_TOO_LARGE: int = 3
ERROR_CODE_MAX_USERS_REACHED: int = 4
ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED: int = 5
# Returned by the Fastly WAF in front of MLPA (not generated by this service).
ERROR_CODE_FASTLY_WAF_RATE_LIMIT: int = 6

RATE_LIMIT_ERROR_RESPONSE = {
    429: {
        "description": (
            "Too Many Requests — budget or rate limit exceeded from MLPA/LiteLLM, "
            "or HTTP 429 from the Fastly WAF in front of MLPA (body `error`: 6)."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": (
                                "Error code: 1 budget limit exceeded, 2 rate limit (TPM/RPM), "
                                "5 upstream provider rate limit, 6 Fastly WAF rate limit (edge; not from MLPA)"
                            ),
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
                    "upstream_rate_limit_exceeded": {
                        "summary": "Upstream rate limit exceeded",
                        "value": {"error": ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED},
                        "description": "Upstream provider rate limit exceeded.",
                    },
                    "fastly_waf_rate_limit": {
                        "summary": "Fastly WAF rate limit",
                        "value": {"error": ERROR_CODE_FASTLY_WAF_RATE_LIMIT},
                        "description": (
                            "HTTP 429 returned at the Fastly edge (WAF) before the request reaches MLPA. "
                        ),
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
    },
    413: {
        "description": "Payload Too Large - Request body or context exceeded",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": "Error code: 3 for request/context too large",
                        }
                    },
                    "required": ["error"],
                },
                "example": {"error": ERROR_CODE_REQUEST_TOO_LARGE},
            }
        },
    },
    403: {
        "description": "Forbidden - Maximum signed-in users reached",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": "Error code: 4 for maximum signed-in users reached",
                        }
                    },
                    "required": ["error"],
                },
                "examples": {
                    "max_users_reached": {
                        "summary": "Maximum signed-in users reached",
                        "value": {"error": ERROR_CODE_MAX_USERS_REACHED},
                        "description": "New sign-ins for cap-managed service types are rejected because capacity is full.",
                    }
                },
            }
        },
    },
}

SENSITIVE_FIELDS_TO_SCRUB_FROM_SENTRY = ["messages"]
