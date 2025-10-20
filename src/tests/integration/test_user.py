from proxy.core.config import env
from tests.consts import TEST_FXA_TOKEN, TEST_USER_ID


def test_user_info_wrong_params(mocked_client_integration):
	response = mocked_client_integration.get(
		"/user/",
		headers={"x-fxa-authorization": "Bearer " + TEST_FXA_TOKEN},
	)
	assert response.status_code == 404

	response = mocked_client_integration.get(
		"/user/invalid/user-id",
		headers={"x-fxa-authorization": "Bearer " + TEST_FXA_TOKEN},
	)
	assert response.status_code == 404


def test_user_info_endpoint_for_missing_user(mocked_client_integration, httpx_mock):
	httpx_mock.add_response(
		method="GET",
		url=f"{env.LITELLM_API_BASE}/customer/info?end_user_id={TEST_USER_ID}",
		status_code=200,
		json={"detail": "User not found"},
	)

	response = mocked_client_integration.get(
		f"/user/{TEST_USER_ID}",
		headers={"x-fxa-authorization": "Bearer " + TEST_FXA_TOKEN},
	)
	assert response.json() == {"detail": "User not found"}
	assert response.status_code == 200  # litellm returns 200 even if user not found


def test_user_info_endpoint_for_existing_user(mocked_client_integration, httpx_mock):
	httpx_mock.add_response(
		method="GET",
		url=f"{env.LITELLM_API_BASE}/customer/info?end_user_id={TEST_USER_ID}",
		status_code=200,
		json={
			"user_id": TEST_USER_ID,
			"blocked": False,
			"alias": None,
			"spend": 0,
			"allowed_model_region": None,
			"default_model": None,
			"litellm_budget_table": None,
		},
	)

	response = mocked_client_integration.get(
		f"/user/{TEST_USER_ID}",
		headers={"x-fxa-authorization": "Bearer " + TEST_FXA_TOKEN},
	)
	assert response.status_code == 200
	assert response.json() == {
		"user_id": TEST_USER_ID,
		"blocked": False,
		"alias": None,
		"spend": 0.0,
		"allowed_model_region": None,
		"default_model": None,
		"litellm_budget_table": None,
	}
