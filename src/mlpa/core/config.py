from functools import cached_property
from typing import Annotated, Any, Literal, TypedDict

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class TrafficContractConfig(TypedDict):
    feature: str
    rpm_limit: int
    tpm_limit: int


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

    # Guest capacity gate (AIPLAT-1447): same admission as above, own counter
    # (mlpa_user_capacity row 2). Infra deletes guest claims (capacity_id = 2)
    # older than the token TTL and must then set row 2's current_identities to
    # the remaining COUNT(*), otherwise slots only free up on MLPA restart.
    # Turn MLPA_ENFORCE_GUEST_CAP on only after every pod runs this image, and
    # off before rolling back: older images' reconcile deletes all claims,
    # guest ones included, and never recounts row 2.
    MLPA_MAX_GUESTS: int = 100000
    MLPA_ENFORCE_GUEST_CAP: bool = False
    MLPA_GUEST_SERVICE_TYPES: set[str] = {"ai-guest", "memories-guest", "search-guest"}
    # Kill switch for GET /verify/v1/guest
    MLPA_GUEST_ENABLED: bool = False

    @model_validator(mode="after")
    def _guest_and_user_caps_are_disjoint(self):
        # A service type in both sets would admit one new user into both rows.
        overlap = self.MLPA_GUEST_SERVICE_TYPES & self.MLPA_CAPPED_SERVICE_TYPES
        if overlap:
            raise ValueError(
                f"MLPA_GUEST_SERVICE_TYPES and MLPA_CAPPED_SERVICE_TYPES overlap: {sorted(overlap)}"
            )
        return self

    @model_validator(mode="after")
    def _guest_secret_is_set(self):
        # The default is in the public repo, anyone could sign guest tokens with it.
        if (
            self.MLPA_GUEST_ENABLED
            and not self.MLPA_DEBUG
            and self.MLPA_GUEST_TOKEN_SIGNING_SECRET == "mlpa-guest-dev-secret"
        ):
            raise ValueError(
                "MLPA_GUEST_ENABLED needs MLPA_GUEST_TOKEN_SIGNING_SECRET set"
            )
        return self

    # Countries with their own Grafana breakout (AIPLAT-1266). Deliberately a
    # small set, distinct from the full ISO country_codes.COUNTRY_CODES list,
    # so the per-country latency/TTFT/availability metrics stay cheap.
    # Everything else clamps to "other" (see utils.clamp_launch_country).
    # Grafana's country/region dropdown reads the same values.
    # `NoDecode` + the validator below let ops set this as a plain
    # comma-separated string (e.g. "US,CA,FR,DE,GB"); pydantic-settings'
    # default complex-type decoding otherwise requires JSON syntax and would
    # crash the app on startup for the intuitive comma-separated format.
    MLPA_LAUNCH_COUNTRIES: Annotated[set[str], NoDecode] = {"US", "CA", "FR", "DE"}

    @field_validator("MLPA_LAUNCH_COUNTRIES", mode="before")
    @classmethod
    def _parse_launch_countries(cls, raw: str | set[str]) -> set[str]:
        if isinstance(raw, str):
            return {code.strip() for code in raw.split(",") if code.strip()}
        return raw

    # Purpose header enforcement/backwards-compatibility:
    # when false (default), the `purpose` header is optional for all service types.
    # when true, the `purpose` header becomes mandatory for service types that
    # have a non-empty configured purpose allowlist.
    MLPA_REQUIRE_PURPOSE_HEADER: bool = False

    # LiteLLM
    MASTER_KEY: str = "sk-default"  # Bypasses LiteLLM.max_budget, use MLPA_VIRTUAL_KEY (virtual key) for completion requests
    OPENAI_API_KEY: str = "sk-add-your-key"  # for local LiteLLM testing
    EXA_API_KEY: str = "sk-add-your-key"  # for local LiteLLM testing
    LINER_API_KEY: str = "sk-add-your-key"  # for local LiteLLM testing
    LITELLM_API_BASE: str = "http://localhost:4000"
    CHALLENGE_EXPIRY_SECONDS: int = 300  # 5 minutes
    # Custom virtual key passthrough: when true, a request may carry a
    # `litellm-virtual-key` header, which MLPA forwards as the upstream Bearer
    # token in place of MLPA_VIRTUAL_KEY. That selects a LiteLLM key with its own
    # budget / rate-limit / model configuration instead configured values.
    ALLOW_CUSTOM_VIRTUAL_KEY: bool = False

    # Which gateway MLPA sends inference to. "otari" replaces LiteLLM with Otari:
    # one Otari service key for every service type, users read and managed through
    # Otari's API rather than LiteLLM's tables.
    GATEWAY_BACKEND: Literal["litellm", "otari"] = "litellm"
    OTARI_API_BASE: str = "http://localhost:8000"
    # MLPA's Otari service key: the secret Otari's config.yml declares for it
    # (MLPA_SERVICE_KEY in scripts/otari_config.py's output). It serves requests
    # and manages MLPA's end users; MLPA needs no other Otari credential.
    OTARI_SERVICE_KEY: str = ""
    # MLPA's Otari key for guests (`*-guest` service types), the counterpart of
    # MLPA_GUEST_VIRTUAL_KEY. Declared with the same owner as OTARI_SERVICE_KEY,
    # so the service key still manages guests, with its own ceiling as the guest
    # spend cap and only the guest budgets to assign. Unset, guests get 503.
    OTARI_GUEST_SERVICE_KEY: str = ""
    # The Otari user that owns the service key and every end user, and the key's name.
    OTARI_OWNER_USER: str = "mlpa"
    # End users known to exist in Otari, remembered so the signup cap is checked
    # without a lookup on every request.
    OTARI_KNOWN_USERS_CACHE_SIZE: int = 100_000

    # Privacy Filter
    PRIVACY_FILTER_ENABLED: bool = False
    PRIVACY_FILTER_MASTER_KEY: str = "sk-default"
    PRIVACY_FILTER_API_BASE: str = "http://localhost:5000"

    # Read-only admin dashboard (`/admin`) and GET /user/counts-by-service-type; not LiteLLM's master key.
    # Under Otari its default is refused: set a real one.
    MLPA_UI_ACCESS_KEY: str = "sk-ui-access-default"
    # MLPA's admin routes (block, unblock, budget moves, user lists). Unset, they
    # take LiteLLM's MASTER_KEY under LiteLLM, and are closed under Otari, where
    # MASTER_KEY no longer exists and its default would otherwise open them.
    MLPA_ADMIN_KEY: str = ""
    MLPA_VIRTUAL_KEY: str = "sk-virtual"  # Enforces LiteLLM.max_budget
    # Separate key for `*-guest` service types (AIPLAT-1449). Its LiteLLM
    # max_budget is the total guest spend cap, so running it out never touches
    # FxA users. Unset means guest requests get 503, never MLPA_VIRTUAL_KEY.
    MLPA_GUEST_VIRTUAL_KEY: str | None = None

    # User Feature Budget - AI service type
    USER_FEATURE_BUDGET_AI_BUDGET_ID: str = "end-user-budget-ai"
    USER_FEATURE_BUDGET_AI_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_AI_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_AI_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_AI_TPM_LIMIT: int = 2000

    # User Feature Budget - S2S service type
    USER_FEATURE_BUDGET_S2S_BUDGET_ID: str = "end-user-budget-s2s"
    USER_FEATURE_BUDGET_S2S_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_S2S_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_S2S_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_S2S_TPM_LIMIT: int = 2000

    # User Feature Budget - S2S Android service type (same values as s2s)
    USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_ID: str = "end-user-budget-s2s-android"
    USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_S2S_ANDROID_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_S2S_ANDROID_RPM_LIMIT: int = 40
    USER_FEATURE_BUDGET_S2S_ANDROID_TPM_LIMIT: int = 2000

    # User Feature Budget - memories service type
    USER_FEATURE_BUDGET_MEMORIES_BUDGET_ID: str = "end-user-budget-memories"
    USER_FEATURE_BUDGET_MEMORIES_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_MEMORIES_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_MEMORIES_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_MEMORIES_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_SEARCH_BUDGET_ID: str = "end-user-budget-search"
    USER_FEATURE_BUDGET_SEARCH_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_SEARCH_MAX_BUDGET: float = 0.01
    USER_FEATURE_BUDGET_SEARCH_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_SEARCH_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_ANSWER_BUDGET_ID: str = "end-user-budget-answer"
    USER_FEATURE_BUDGET_ANSWER_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_ANSWER_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_ANSWER_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_ANSWER_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_SW_ANSWER_BUDGET_ID: str = "end-user-budget-sw-answer"
    USER_FEATURE_BUDGET_SW_ANSWER_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_SW_ANSWER_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_SW_ANSWER_TPM_LIMIT: int = 2000
    USER_FEATURE_BUDGET_SW_ANSWER_BUDGET_DURATION: str = "1d"

    USER_FEATURE_BUDGET_LINER_ANSWERS_BUDGET_ID: str = "end-user-budget-liner-answer"
    USER_FEATURE_BUDGET_LINER_ANSWERS_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_LINER_ANSWERS_MAX_BUDGET: float = 0.06
    USER_FEATURE_BUDGET_LINER_ANSWERS_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_LINER_ANSWERS_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_TELEMETRY_BUDGET_ID: str = "end-user-budget-telemetry"
    USER_FEATURE_BUDGET_TELEMETRY_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_TELEMETRY_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_TELEMETRY_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_TELEMETRY_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_AGENT_BUDGET_ID: str = "end-user-budget-agent"
    USER_FEATURE_BUDGET_AGENT_BUDGET_DURATION: str = "7d"
    USER_FEATURE_BUDGET_AGENT_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_AGENT_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_AGENT_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_AGENT_SEARCH_BUDGET_ID: str = "end-user-budget-agent-search"
    USER_FEATURE_BUDGET_AGENT_SEARCH_BUDGET_DURATION: str = "7d"
    USER_FEATURE_BUDGET_AGENT_SEARCH_MAX_BUDGET: float = 0.1
    USER_FEATURE_BUDGET_AGENT_SEARCH_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_AGENT_SEARCH_TPM_LIMIT: int = 2000

    # User Feature Budget - ai-dev service type (experimentation, batch predictions)
    USER_FEATURE_BUDGET_AI_DEV_BUDGET_ID: str = "end-user-budget-ai-dev"
    USER_FEATURE_BUDGET_AI_DEV_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_AI_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_AI_DEV_RPM_LIMIT: int = 200
    USER_FEATURE_BUDGET_AI_DEV_TPM_LIMIT: int = 10000

    # User Feature Budget - memories-dev service type (experimentation)
    USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_ID: str = "end-user-budget-memories-dev"
    USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_MEMORIES_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_MEMORIES_DEV_RPM_LIMIT: int = 50
    USER_FEATURE_BUDGET_MEMORIES_DEV_TPM_LIMIT: int = 5000

    # User Feature Budget - guest service types (AIPLAT-1449). Clients never
    # send these, MLPA appends "-guest" to the service-type header when the
    # request carries an MLPA guest token.
    USER_FEATURE_BUDGET_AI_GUEST_BUDGET_ID: str = "end-user-budget-ai-guest"
    USER_FEATURE_BUDGET_AI_GUEST_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_AI_GUEST_MAX_BUDGET: float = 0.05
    USER_FEATURE_BUDGET_AI_GUEST_RPM_LIMIT: int = 20
    USER_FEATURE_BUDGET_AI_GUEST_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_MEMORIES_GUEST_BUDGET_ID: str = "end-user-budget-memories-guest"
    USER_FEATURE_BUDGET_MEMORIES_GUEST_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_MEMORIES_GUEST_MAX_BUDGET: float = 0.05
    USER_FEATURE_BUDGET_MEMORIES_GUEST_RPM_LIMIT: int = 10
    USER_FEATURE_BUDGET_MEMORIES_GUEST_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_SEARCH_GUEST_BUDGET_ID: str = "end-user-budget-search-guest"
    USER_FEATURE_BUDGET_SEARCH_GUEST_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_SEARCH_GUEST_MAX_BUDGET: float = 0.01
    USER_FEATURE_BUDGET_SEARCH_GUEST_RPM_LIMIT: int = 5
    USER_FEATURE_BUDGET_SEARCH_GUEST_TPM_LIMIT: int = 2000

    USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_ID: str = "end-user-budget-mochi-dev"
    USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_MOCHI_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_MOCHI_DEV_RPM_LIMIT: int = 200
    USER_FEATURE_BUDGET_MOCHI_DEV_TPM_LIMIT: int = 10000

    USER_FEATURE_BUDGET_SEARCH_DEV_BUDGET_ID: str = "end-user-budget-search-dev"
    USER_FEATURE_BUDGET_SEARCH_DEV_BUDGET_DURATION: str = "1d"
    USER_FEATURE_BUDGET_SEARCH_DEV_MAX_BUDGET: float = 1.0
    USER_FEATURE_BUDGET_SEARCH_DEV_RPM_LIMIT: int = 200
    USER_FEATURE_BUDGET_SEARCH_DEV_TPM_LIMIT: int = 10000

    # Feature Traffic Contracts
    ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT: bool = False
    TRAFFIC_CONTRACT_REDIS_KEY_PREFIX: str = "mlpa:traffic_contract"
    TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS: int = 60
    TRAFFIC_CONTRACT_TPM_WINDOW_SECONDS: int = 60
    TRAFFIC_CONTRACT_COUNTER_TTL_SECONDS: int = 120
    TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR: bool = True

    # Global RPM/TPM limit (0 means no limit)
    TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT: int = 0
    TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT: int = 0

    FEATURE_SMART_WINDOW: str = "smart-window"
    SMART_WINDOW_TRAFFIC_CONTRACT_RPM_LIMIT: int = 0
    SMART_WINDOW_TRAFFIC_CONTRACT_TPM_LIMIT: int = 0

    FEATURE_S2S: str = "s2s"
    S2S_TRAFFIC_CONTRACT_RPM_LIMIT: int = 0
    S2S_TRAFFIC_CONTRACT_TPM_LIMIT: int = 0

    FEATURE_S2S_ANDROID: str = "s2s-android"
    S2S_ANDROID_TRAFFIC_CONTRACT_RPM_LIMIT: int = 0
    S2S_ANDROID_TRAFFIC_CONTRACT_TPM_LIMIT: int = 0

    @cached_property
    def service_type_config(self) -> dict[str, dict]:
        """
        User feature budget configuration by service type.
        Returns a nested dictionary keyed by service type.
        Constructed from individual environment variables.
        """
        return {
            "ai": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_AI_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_AI_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_AI_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AI_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AI_TPM_LIMIT,
            },
            "s2s": {
                "feature": self.FEATURE_S2S,
                "budget_id": self.USER_FEATURE_BUDGET_S2S_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_S2S_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_S2S_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_S2S_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_S2S_TPM_LIMIT,
            },
            "s2s-android": {
                "feature": self.FEATURE_S2S_ANDROID,
                "budget_id": self.USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_S2S_ANDROID_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_S2S_ANDROID_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_S2S_ANDROID_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_S2S_ANDROID_TPM_LIMIT,
            },
            "memories": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_MEMORIES_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_MEMORIES_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_MEMORIES_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_TPM_LIMIT,
            },
            "search": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_SEARCH_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_SEARCH_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_SEARCH_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_SEARCH_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_SEARCH_TPM_LIMIT,
            },
            "answer": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_ANSWER_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_ANSWER_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_ANSWER_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_ANSWER_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_ANSWER_TPM_LIMIT,
            },
            "sw-answer": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_SW_ANSWER_BUDGET_ID,
                "max_budget": self.USER_FEATURE_BUDGET_SW_ANSWER_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_SW_ANSWER_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_SW_ANSWER_TPM_LIMIT,
                "budget_duration": self.USER_FEATURE_BUDGET_SW_ANSWER_BUDGET_DURATION,
            },
            "liner-answer": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_LINER_ANSWERS_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_LINER_ANSWERS_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_LINER_ANSWERS_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_LINER_ANSWERS_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_LINER_ANSWERS_TPM_LIMIT,
            },
            "telemetry": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_TELEMETRY_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_TELEMETRY_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_TELEMETRY_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_TELEMETRY_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_TELEMETRY_TPM_LIMIT,
            },
            "agent": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_AGENT_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_AGENT_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_AGENT_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AGENT_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AGENT_TPM_LIMIT,
            },
            "agent-search": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_AGENT_SEARCH_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_AGENT_SEARCH_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_AGENT_SEARCH_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AGENT_SEARCH_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AGENT_SEARCH_TPM_LIMIT,
            },
            "ai-dev": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_AI_DEV_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_AI_DEV_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_AI_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AI_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AI_DEV_TPM_LIMIT,
            },
            "memories-dev": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_MEMORIES_DEV_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_MEMORIES_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_DEV_TPM_LIMIT,
            },
            "ai-guest": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_AI_GUEST_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_AI_GUEST_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_AI_GUEST_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_AI_GUEST_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_AI_GUEST_TPM_LIMIT,
            },
            "memories-guest": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_MEMORIES_GUEST_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_MEMORIES_GUEST_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_MEMORIES_GUEST_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_GUEST_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MEMORIES_GUEST_TPM_LIMIT,
            },
            "search-guest": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_SEARCH_GUEST_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_SEARCH_GUEST_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_SEARCH_GUEST_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_SEARCH_GUEST_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_SEARCH_GUEST_TPM_LIMIT,
            },
            "mochi-dev": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_MOCHI_DEV_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_MOCHI_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_MOCHI_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_MOCHI_DEV_TPM_LIMIT,
            },
            "search-dev": {
                "feature": self.FEATURE_SMART_WINDOW,
                "budget_id": self.USER_FEATURE_BUDGET_SEARCH_DEV_BUDGET_ID,
                "budget_duration": self.USER_FEATURE_BUDGET_SEARCH_DEV_BUDGET_DURATION,
                "max_budget": self.USER_FEATURE_BUDGET_SEARCH_DEV_MAX_BUDGET,
                "rpm_limit": self.USER_FEATURE_BUDGET_SEARCH_DEV_RPM_LIMIT,
                "tpm_limit": self.USER_FEATURE_BUDGET_SEARCH_DEV_TPM_LIMIT,
            },
        }

    @cached_property
    def traffic_contract_config(self) -> dict[str, TrafficContractConfig]:
        """
        Per-feature traffic contracts keyed by service type.

        Redis stores live counters by the feature name so the
        same service-type contract protects each model's basket independently.
        """

        TRAFFIC_CONTRACT_CONFIG = {
            "smart-window": {
                "rpm_limit": self.SMART_WINDOW_TRAFFIC_CONTRACT_RPM_LIMIT,
                "tpm_limit": self.SMART_WINDOW_TRAFFIC_CONTRACT_TPM_LIMIT,
            },
            "s2s": {
                "rpm_limit": self.S2S_TRAFFIC_CONTRACT_RPM_LIMIT,
                "tpm_limit": self.S2S_TRAFFIC_CONTRACT_TPM_LIMIT,
            },
            "s2s-android": {
                "rpm_limit": self.S2S_ANDROID_TRAFFIC_CONTRACT_RPM_LIMIT,
                "tpm_limit": self.S2S_ANDROID_TRAFFIC_CONTRACT_TPM_LIMIT,
            },
        }
        return {
            service_type: {
                "feature": budget["feature"],
                "rpm_limit": TRAFFIC_CONTRACT_CONFIG[budget["feature"]]["rpm_limit"],
                "tpm_limit": TRAFFIC_CONTRACT_CONFIG[budget["feature"]]["tpm_limit"],
            }
            for service_type, budget in self.service_type_config.items()
        }

    @cached_property
    def valid_service_types(self) -> list[str]:
        """
        Returns a list of valid service types from user_feature_budget configuration.
        """
        return list(self.service_type_config.keys())

    @cached_property
    def valid_service_types_set(self) -> set[str]:
        """
        Valid service types for O(1) membership checks.

        Keep `valid_service_types` as the ordered list for OpenAPI enum and error
        message stability.
        """
        return set(self.valid_service_types)

    @cached_property
    def client_service_types(self) -> list[str]:
        """Values a client may send in the service-type header (no `-guest`)."""
        return [st for st in self.valid_service_types if not st.endswith("-guest")]

    @cached_property
    def service_type_purposes(self) -> dict[str, list[str]]:
        """
        Valid purpose values per service type (for the purpose header and Prometheus).
        """
        ai_purposes = [
            "chat",
            "title-generation",
            "convo-starters-sidebar",
            "smart-form-fill",
            "aitab",
            "auto-tab-grouping",
        ]
        memories_purposes = ["memory-generation"]
        return {
            "ai": ai_purposes,
            "ai-dev": ai_purposes,
            "mochi-dev": ai_purposes,
            "memories": memories_purposes,
            "memories-dev": memories_purposes,
            "ai-guest": ai_purposes,
            "memories-guest": memories_purposes,
            "search-guest": [],
            "s2s": [],
            "s2s-android": [],
            "search": [],
            "answer": [],
            "sw-answer": [],
            "liner-answer": [],
            "search-dev": [],
            "telemetry": ["chat"],
            "agent": ["monitor", "research"],
            "agent-search": ["research"],
        }

    def valid_purposes_for_service_type(self, service_type: str) -> list[str]:
        """Return valid purpose values for a service type (empty if purpose not used)."""
        return self.service_type_purposes.get(service_type, [])

    @cached_property
    def valid_purposes_set(self) -> set[str]:
        """
        Valid purpose label values across service types.

        Empty purpose is an intentional bounded value for service types that do not
        use a purpose header.
        """
        return set(
            {
                purpose
                for purposes in self.service_type_purposes.values()
                for purpose in purposes
            }
            | {""}
        )

    @cached_property
    def valid_major_fx_versions_set(self) -> set[str]:
        return {str(n) for n in range(100, 200)}

    def service_type_requires_purpose(self, service_type: str) -> bool:
        """True if the purpose header is mandatory for this service type."""
        return len(self.valid_purposes_for_service_type(service_type)) > 0

    @cached_property
    def forced_model_service_type_pairs(self) -> dict[str, list[str]]:
        """
        Returns a dictionary mapping model names to their valid service types.
        """
        # Force certain models to use certain service types
        return {
            "exa-search": ["search", "search-dev", "agent-search", "search-guest"],
            "exa": ["answer", "sw-answer"],
            "liner": ["liner-answer"],
        }

    def valid_service_type_for_model(self, service_type: str, model: str) -> bool:
        """Check if a service type is valid for a specific model."""
        valid_service_types = self.forced_model_service_type_pairs.get(model)
        if valid_service_types is not None:
            return service_type in valid_service_types

        return not any(
            service_type in service_types
            for service_types in self.forced_model_service_type_pairs.values()
        )

    @cached_property
    def valid_model_labels(self) -> set[str]:
        """
        Valid model label values for Prometheus.

        Keep this in sync with the LiteLLM model list/search tools. It is defined
        here rather than parsed from LiteLLM config because the MLPA pod should
        not depend on that file existing at runtime.
        """
        return {
            "exa",
            "exa-search",
            "gemini-2.5-flash-lite",
            "gemini-3.1-flash-lite",
            "liner-answers",
            "openai/gpt-4o",
            "mistral-small-2503",
            "mistral-small-2603",
            "moz-summarization",
            "qwen3-235b-a22b-instruct-2507-maas",
            "vertex_ai/mistral-small-2503",
        }

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
    MLPA_GUEST_TOKEN_TTL_SECONDS: int = 604800
    MLPA_GUEST_TOKEN_SIGNING_SECRET: str = "mlpa-guest-dev-secret"

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
    READINESS_CHECK_TIMEOUT_S: float = 2.0
    # Server-enforced query timeout (ms, 0 = unlimited): kills a runaway query
    # even if the client or event loop hangs, so connections don't pile up.
    PG_STATEMENT_TIMEOUT_MS: int = 3000
    # Reaps sessions left idle mid-transaction (releasing their locks). Keep this
    # >= statement_timeout, since a transaction can legitimately span round-trips.
    PG_IDLE_IN_TX_TIMEOUT_MS: int = 10000
    # Raised budget for heavy startup reconciliation, applied per-transaction via SET LOCAL.
    PG_MAINTENANCE_STATEMENT_TIMEOUT_MS: int = 30000
    # Raised budget for admin reads that full-scan the user table (listing, counts).
    PG_ADMIN_READ_TIMEOUT_MS: int = 15000
    # Optional asyncpg client-side timeout (seconds, None = off). Unlike the SET
    # LOCAL budgets above, this one isn't relaxed by them, so keep it above
    # PG_MAINTENANCE_STATEMENT_TIMEOUT_MS or it'll cancel those queries.
    PG_COMMAND_TIMEOUT_S: float | None = None

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # LLM request default values
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
    DISCONNECT_POLL_INTERVAL_SECONDS: float = 0.1

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

USER_CAPACITY_ID = 1
GUEST_CAPACITY_ID = 2


# env attribute names for (enforced, max identities, service types) per
# mlpa_user_capacity row. Names, not values, so tests can patch env.
CAPACITY_SETTINGS = {
    USER_CAPACITY_ID: (
        "MLPA_ENFORCE_SIGNIN_CAP",
        "MLPA_MAX_SIGNED_IN_USERS",
        "MLPA_CAPPED_SERVICE_TYPES",
    ),
    GUEST_CAPACITY_ID: (
        "MLPA_ENFORCE_GUEST_CAP",
        "MLPA_MAX_GUESTS",
        "MLPA_GUEST_SERVICE_TYPES",
    ),
}


def capacity_settings(capacity_id: int) -> tuple[bool, int, set[str]]:
    """(enforced, max identities, service types) for a mlpa_user_capacity row."""
    enforced, max_identities, service_types = (
        getattr(env, name) for name in CAPACITY_SETTINGS[capacity_id]
    )
    return enforced, max_identities, service_types


def capacity_id_for_service_type(service_type: str) -> int | None:
    """Capacity row a new user of this service type must be admitted to, if any."""
    for capacity_id in CAPACITY_SETTINGS:
        enforced, _max, service_types = capacity_settings(capacity_id)
        if enforced and service_type in service_types:
            return capacity_id
    return None


USE_OTARI = env.GATEWAY_BACKEND == "otari"
OTARI_API_ROOT = f"{env.OTARI_API_BASE.rstrip('/')}/api/v1"


_DEFAULT_UI_ACCESS_KEY = "sk-ui-access-default"


def admin_key() -> str | None:
    """The key MLPA's admin routes accept, or None when they are closed."""
    if env.MLPA_ADMIN_KEY:
        return env.MLPA_ADMIN_KEY
    return None if USE_OTARI else env.MASTER_KEY


def ui_access_key() -> str | None:
    """The key the read-only admin UI accepts, or None when it is closed."""
    if USE_OTARI and env.MLPA_UI_ACCESS_KEY in ("", _DEFAULT_UI_ACCESS_KEY):
        return None
    return env.MLPA_UI_ACCESS_KEY


# The upstream gateway's URLs. The LITELLM_ names are kept for the call sites;
# with GATEWAY_BACKEND=otari they point at Otari's equivalents.
if USE_OTARI:
    LITELLM_READINESS_URL = f"{OTARI_API_ROOT}/health/readiness"
    # Otari's readiness body carries its version, so it doubles as the info URL.
    LITELLM_INFO_URL = LITELLM_READINESS_URL
    LITELLM_COMPLETIONS_URL = f"{OTARI_API_ROOT}/chat/completions"
    LITELLM_SEARCH_URL = f"{OTARI_API_ROOT}/search"
else:
    LITELLM_READINESS_URL = f"{env.LITELLM_API_BASE}/health/readiness"
    LITELLM_INFO_URL = f"{env.LITELLM_API_BASE}/public/model_hub/info"
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


def is_guest_service_type(service_type: str | None) -> bool:
    """Whether MLPA serves this service type to guests (set only after guest auth)."""
    return (service_type or "").endswith("-guest")


def otari_service_key(service_type: str | None) -> str:
    """MLPA's Otari key for a service type: the guest key for guests, never the other's."""
    if is_guest_service_type(service_type):
        if not env.OTARI_GUEST_SERVICE_KEY:
            raise RuntimeError(
                "No Otari guest key; set OTARI_GUEST_SERVICE_KEY to the secret Otari's config.yml declares"
            )
        return env.OTARI_GUEST_SERVICE_KEY
    if not env.OTARI_SERVICE_KEY:
        raise RuntimeError(
            "No Otari service key; set OTARI_SERVICE_KEY to the secret Otari's config.yml declares"
        )
    return env.OTARI_SERVICE_KEY


def resolve_litellm_virtual_auth_headers(
    custom_virtual_key: str | None = None,
    service_type: str | None = None,
) -> dict[str, str]:
    """
    Resolve virtual key to be forwarded to litellm, if passing custom virtual key
    the custom key will be used. Otherwise, the default LITELLM_VIRTUAL_AUTH_HEADERS
    will be used. With GATEWAY_BACKEND=otari the default is MLPA's Otari service
    key (its guest key for guests), with the budget of the request's service
    type, which Otari puts a new end user on.
    """
    if custom_virtual_key is None and USE_OTARI:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {otari_service_key(service_type)}",
        }
        config = env.service_type_config.get(service_type or "")
        if config is not None:
            headers[OTARI_HEADER_END_USER_BUDGET] = config["budget_id"]
        return headers
    if custom_virtual_key is None:
        return LITELLM_VIRTUAL_AUTH_HEADERS
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {custom_virtual_key}",
    }


# LiteLLM proxy response headers (lowercase names for httpx Headers.get)
# https://docs.litellm.ai/docs/proxy/response_headers
LITELLM_HEADER_MODEL_API_BASE = "x-litellm-model-api-base"
LITELLM_HEADER_ATTEMPTED_FALLBACKS = "x-litellm-attempted-fallbacks"
LITELLM_HEADER_ATTEMPTED_RETRIES = "x-litellm-attempted-retries"
LITELLM_HEADER_RESPONSE_DURATION_MS = "x-litellm-response-duration-ms"
LITELLM_HEADER_RESPONSE_COST = "x-litellm-response-cost"
# Otari's equivalents (Otari sends the cost in the body, as usage.cost_usd).
OTARI_HEADER_PROVIDER = "otari-provider"
OTARI_HEADER_ATTEMPT_COUNT = "otari-attempt-count"
OTARI_HEADER_FALLBACK = "otari-fallback"
OTARI_HEADER_RESPONSE_DURATION_MS = "otari-response-duration-ms"

# Otari's stable refusal codes (Otari-Error-Code), and the headers beside them.
OTARI_HEADER_ERROR_CODE = "otari-error-code"
# Names the budget a new end user starts on, and, on a response, the one it is on.
OTARI_HEADER_END_USER_BUDGET = "otari-end-user-budget"
OTARI_HEADER_BUDGET_SCOPE = "otari-budget-scope"
# A provider's own failure, on a search Otari answers 502 (Exa's status and tag).
OTARI_HEADER_ERROR_STATUS = "otari-error-status"
OTARI_HEADER_ERROR_TAG = "otari-error-tag"

# Privacy Filter
PRIVACY_FILTER_READINESS_URL = f"{env.PRIVACY_FILTER_API_BASE}/readyz"
PRIVACY_FILTER_URL = f"{env.PRIVACY_FILTER_API_BASE}/privacy-filter"
PRIVACY_FILTER_MASTER_AUTH_HEADERS = {
    "Content-Type": "application/json",
    "x-pf-api-key": env.PRIVACY_FILTER_MASTER_KEY,
}

ERROR_CODE_BUDGET_LIMIT_EXCEEDED: int = 1
ERROR_CODE_RATE_LIMIT_EXCEEDED: int = 2
ERROR_CODE_REQUEST_TOO_LARGE: int = 3
ERROR_CODE_MAX_USERS_REACHED: int = 4
ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED: int = 5
# Returned by the Fastly WAF in front of MLPA (not generated by this service).
ERROR_CODE_FASTLY_WAF_RATE_LIMIT: int = 6
# Reserve error code 7 for fastly blocked 406 response (not returned by this service)

ERROR_CODE_INVALID_MODEL_NAME: int = 8
ERROR_CODE_INVALID_REQUEST: int = 9
ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED: int = 10
# Guest virtual key (MLPA_GUEST_VIRTUAL_KEY) hit its LiteLLM max_budget (AIPLAT-1449)
ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED: int = 11
ERROR_CODE_MAX_GUESTS_REACHED: int = 12

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
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
                                "5 upstream provider rate limit, 6 Fastly WAF rate limit (edge; not from MLPA), "
                                "10 global budget limit exceeded, 11 guest budget limit exceeded"
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
                    "guest_budget_exceeded": {
                        "summary": "Guest budget limit exceeded",
                        "value": {"error": ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED},
                        "description": "Shared budget for all guests is used up. Check Retry-After header (3600 seconds = 1 hour).",
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
    400: {
        "description": "Bad Request - Invalid model name or malformed request body",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": "Error code: 8 invalid model name, 9 invalid request body",
                        }
                    },
                    "required": ["error"],
                },
                "examples": {
                    "invalid_model_name": {
                        "summary": "Invalid model name",
                        "value": {"error": ERROR_CODE_INVALID_MODEL_NAME},
                        "description": "Requested model is not configured in LiteLLM.",
                    },
                    "invalid_request": {
                        "summary": "Invalid request body",
                        "value": {"error": ERROR_CODE_INVALID_REQUEST},
                        "description": "Upstream rejected the request as malformed (e.g. invalid JSON body).",
                    },
                },
            }
        },
    },
    401: {
        "description": (
            "Unauthorized - authentication failed or was missing. Either the "
            "Authorization header is absent, the FxA / App Attest / Play Integrity "
            "credentials did not verify, or the x-dev-authorization token is wrong "
            "for a dev service type."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "string",
                            "description": "Human-readable reason the request was not authorized.",
                        }
                    },
                    "required": ["detail"],
                },
                "examples": {
                    "missing_authorization_header": {
                        "summary": "Missing authorization header",
                        "value": {"detail": "Missing authorization header"},
                    },
                    "invalid_fxa_auth": {
                        "summary": "Invalid FxA auth",
                        "value": {"detail": "Invalid FxA auth"},
                    },
                    "invalid_dev_authorization": {
                        "summary": "Invalid dev authorization",
                        "value": {"detail": "Invalid x-dev-authorization"},
                    },
                },
            }
        },
    },
    403: {
        "description": "Forbidden - Maximum signed-in users or guests reached",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": "Error code: 4 for maximum signed-in users reached, 12 for maximum guests reached",
                        }
                    },
                    "required": ["error"],
                },
                "examples": {
                    "max_users_reached": {
                        "summary": "Maximum signed-in users reached",
                        "value": {"error": ERROR_CODE_MAX_USERS_REACHED},
                        "description": "New sign-ins for cap-managed service types are rejected because capacity is full.",
                    },
                    "max_guests_reached": {
                        "summary": "Maximum guests reached",
                        "value": {"error": ERROR_CODE_MAX_GUESTS_REACHED},
                        "description": "New guests for guest service types are rejected because guest capacity is full.",
                    },
                },
            }
        },
    },
    500: {
        "description": "Internal Server Error",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "integer",
                            "description": "Error code: 10 for global budget exceeded",
                        }
                    },
                    "required": ["error"],
                },
                "examples": {
                    "global_budget_exceeded": {
                        "summary": "Global budget limit exceeded",
                        "value": {"error": ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED},
                        "description": "Global LiteLLM virtual-key budget limit exceeded. Check Retry-After header (300 seconds = 5 minutes).",
                    },
                },
            }
        },
    },
}

# Reusable response docs for admin endpoints, which carry their own auth header
# (master_key / mlpa_ui_access_key) and return FastAPI's default detail envelope.
ADMIN_UNAUTHORIZED_RESPONSE: dict[int | str, dict[str, Any]] = {
    401: {
        "description": (
            "Unauthorized - the master_key or mlpa_ui_access_key header is "
            "missing or wrong."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "object",
                            "properties": {"error": {"type": "string"}},
                        }
                    },
                    "required": ["detail"],
                },
                "example": {"detail": {"error": "Unauthorized"}},
            }
        },
    },
}

USER_NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "User not found.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                    "required": ["detail"],
                },
                "example": {"detail": "User not found"},
            }
        },
    },
}

UNKNOWN_SERVICE_TYPE_RESPONSE: dict[int | str, dict[str, Any]] = {
    422: {
        "description": (
            "Unprocessable Entity. Two things return this status: an unknown "
            "service type in the payload, where `detail` is an object like "
            '`{"error": ...}`, and FastAPI body validation, where `detail` is a '
            'list like `[{"loc", "msg", ...}]`.'
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "properties": {"error": {"type": "string"}},
                                },
                                {"type": "array", "items": {"type": "object"}},
                            ]
                        }
                    },
                    "required": ["detail"],
                },
                "examples": {
                    "unknown_service_type": {
                        "summary": "Unknown service type (object detail)",
                        "value": {
                            "detail": {
                                "error": "Unknown service type: foo. Valid values: ai, ai-dev, ..."
                            }
                        },
                    },
                    "request_validation": {
                        "summary": "Request body validation (list detail)",
                        "value": {
                            "detail": [
                                {
                                    "loc": ["body", "service_type"],
                                    "msg": "Field required",
                                    "type": "missing",
                                }
                            ]
                        },
                    },
                },
            }
        },
    },
}

# Response docs for the Play Integrity verification endpoint, which is itself the
# authentication step (no inbound auth header) and rejects bad payloads with 401/403.
PLAY_VERIFY_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": (
            "Unauthorized - the Play Integrity check did not pass. The token, "
            "package name, request hash, or the app/device verdicts failed "
            "verification. `detail` is a string for verdict failures, or an object "
            'like `{"error": ...}` when the upstream Play Integrity API rejects the '
            "token."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {"error": {"type": "string"}},
                                },
                            ]
                        }
                    },
                    "required": ["detail"],
                },
                "examples": {
                    "verdict_failed": {
                        "summary": "Verdict failed (string detail)",
                        "value": {"detail": "Invalid Play Integrity token"},
                    },
                    "upstream_rejected": {
                        "summary": "Upstream Play API rejected the token (object detail)",
                        "value": {
                            "detail": {"error": "Upstream service returned an error"}
                        },
                    },
                },
            }
        },
    },
    403: {
        "description": "Forbidden - the requested package name is not allowed.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                    "required": ["detail"],
                },
                "example": {"detail": "Package name not allowed"},
            }
        },
    },
    422: {
        "description": (
            "Validation Error - the request body did not match the schema, for "
            "example a missing or wrong-typed field. FastAPI returns this before "
            "the handler runs, so it is not an auth failure (those return 401)."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/HTTPValidationError"}
            }
        },
    },
    502: {
        "description": "Bad Gateway - the Play Integrity validation service is unavailable.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "object",
                            "properties": {"error": {"type": "string"}},
                        }
                    },
                    "required": ["detail"],
                },
                "example": {
                    "detail": {"error": "Play Integrity validation service unavailable"}
                },
            }
        },
    },
}

SENSITIVE_FIELDS_TO_SCRUB_FROM_SENTRY = ["messages"]

SENSITIVE_HEADERS_TO_SCRUB_FROM_SENTRY = ["litellm-virtual-key"]
