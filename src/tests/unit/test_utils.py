import base64

import pytest
from fastapi import HTTPException

from proxy.core.utils import b64decode_safe


def test_b64decode_safe():
	# Valid base64 string
	original_data = b"Test data for base64"
	encoded_data = base64.b64encode(original_data).decode("utf-8")
	decoded_data = b64decode_safe(encoded_data)
	assert decoded_data == original_data

	# Invalid base64 string
	invalid_encoded_data = "Invalid@@Base64!!"
	data_name = "custom_name"
	with pytest.raises(HTTPException) as exc_info:
		b64decode_safe(invalid_encoded_data, data_name)

	assert exc_info.value.status_code == 400
	assert "Invalid Base64" in exc_info.value.detail[data_name]
