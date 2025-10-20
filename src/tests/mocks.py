from datetime import datetime

from proxy.core.classes import AuthorizedChatRequest, ChatRequest
from tests.consts import SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN, TEST_USER_ID


async def mock_app_attest_auth(request):
	return {"username": "testuser"}


async def mock_verify_assert(key_id, assertion_obj, payload: ChatRequest):
	return {"status": "success"}


async def mock_get_or_create_user(mock_litellm_pg, user_id: str):
	user = await mock_litellm_pg.get_user(user_id)
	if not user:
		await mock_litellm_pg.store_user(user_id, {"data": "testdata"})
		user = await mock_litellm_pg.get_user(user_id)
		return user, True
	return [{"user_id": user_id, "data": "testdata"}, False]


async def mock_get_completion(authorized_chat_request: AuthorizedChatRequest):
	return SUCCESSFUL_CHAT_RESPONSE


class MockAppAttestPGService:
	def __init__(self):
		self.pg = "MOCK NOT IMPLEMENTED"
		self.db_name = "test"
		self.db_url = "test_app_attest"
		self.connected = True
		self.challenges = {}
		self.keys = {}

	async def connect(self):
		pass

	async def disconnect(self):
		pass

	def check_status(self):
		return self.connected

	async def store_challenge(self, key_id: str, challenge: str):
		self.challenges[key_id] = {
			"created_at": datetime.now(),
			"challenge": challenge.encode(),
		}

	async def get_challenge(self, key_id: str) -> dict | None:
		return self.challenges.get(key_id)

	async def delete_challenge(self, key_id: str):
		try:
			del self.challenges[key_id]
		except:
			pass

	async def store_key(self, key_id: str, public_key: str):
		self.keys[key_id] = public_key

	async def get_key(self, key_id: str) -> str | None:
		return self.keys.get(key_id)

	async def delete_key(self, key_id: str):
		del self.keys[key_id]


class MockLiteLLMPGService:
	def __init__(self):
		self.db_name = "test"
		self.db_url = "test_litellm"
		self.connected = True
		self.users = {}

	async def connect(self):
		print("mock connect called")
		pass

	async def disconnect(self):
		pass

	def check_status(self):
		return self.connected

	async def get_user(self, user_id: str):
		print("mock get_user called with user_id:", user_id)
		return self.users.get(user_id)

	async def store_user(self, user_id: str, data: dict):
		print("mock store_user called with user_id:", user_id, "data:", data)
		self.users[user_id] = data


class MockFxAService:
	def __init__(self, client_id: str, client_secret: str, fxa_url: str):
		self.client_id = client_id
		self.client_secret = client_secret
		self.fxa_url = fxa_url

	def verify_token(self, token: str, scope: str = "profile"):
		if token == TEST_FXA_TOKEN:
			return {"user": TEST_USER_ID}
		return {"error": "Invalid token"}
