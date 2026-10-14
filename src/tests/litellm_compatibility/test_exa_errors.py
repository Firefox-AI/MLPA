"""
mlpa_exa_errors_total reads Exa's error tag out of LiteLLM's error text. If
LiteLLM rewords it, every Exa error silently becomes `untagged`. The exa_stub
service always fails with Exa's 503 SERVICE_OVERLOADED body.
"""

import pytest

from mlpa.core.utils import clamp_exa_error_labels, extract_exa_error_tag
from tests.helpers import CHAT_COMPLETIONS_PATH, chat_request


@pytest.mark.parametrize(
    "path,body",
    [
        (CHAT_COMPLETIONS_PATH, chat_request(model="exa-stub")),
        ("/v1/search/exa-search-stub", {"query": "weather", "max_results": 1}),
    ],
    ids=["answer", "search"],
)
def test_litellm_passes_exa_error_tag_through(proxy, path, body):
    response = proxy.post(path, json=body)

    assert response.is_error, response.text
    tag = extract_exa_error_tag(response.text)
    assert clamp_exa_error_labels(tag, response.status_code) == (
        "503",
        "SERVICE_OVERLOADED",
    ), response.text
