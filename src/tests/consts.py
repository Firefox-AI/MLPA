from mlpa.core.classes import AuthorizedChatRequest, ChatRequest

TEST_USER_ID = "test-user-id"
TEST_KEY_ID_B64 = "dGVzdC1rZXktaWQ="
TEST_KEY_ID = "test-key-id"
TEST_FXA_TOKEN = "test-fxa-token"
TEST_BLOCKED_USER_ID = "blocked-user-id"

SAMPLE_REQUEST = AuthorizedChatRequest(
    user="test-user-123",
    model="test-model",
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.7,
    top_p=0.9,
    max_completion_tokens=150,
)

MOCK_MODEL_NAME = "mock-gpt"

SAMPLE_CHAT_REQUEST = ChatRequest(
    model=MOCK_MODEL_NAME,
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.7,
    top_p=0.9,
    max_completion_tokens=150,
)

SUCCESSFUL_CHAT_RESPONSE = {
    "id": "2834283423498234",
    "created": 1750000000,
    "model": "test-model",
    "object": "chat.completion",
    "choices": [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {
                "content": "I'd be happy to help with that!",
                "role": "assistant",
            },
        }
    ],
    "usage": {"completion_tokens": 27, "prompt_tokens": 18, "total_tokens": 45},
}

MOCK_CHAT_RESPONSE = {
    "choices": [{"message": {"content": "mock completion response"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    "model": "mock-gpt",
}

MOCK_STREAMING_CHUNKS = [
    'data: {"choices":[{"delta":{"content":"mock token 1"}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"mock token 2"}}]}\n\n',
    "data: [DONE]\n\n",
]

MOCK_JWKS_RESPONSE = {
    "keys": [
        {"kty": "RSA", "kid": "test-key-id", "use": "sig", "n": "test-n", "e": "AQAB"}
    ]
}

MOCK_FXA_USER_DATA = {
    "user": TEST_USER_ID,
    "client_id": "test-client-id",
    "scope": ["profile"],
    "generation": 1,
    "profile_changed_at": 1234567890,
}
