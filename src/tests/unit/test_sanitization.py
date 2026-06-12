import json

from mlpa.core.sanitization import (
    sanitize_request_body,
    strip_unpaired_surrogates,
)


def _utf8_encodable(value) -> bool:
    """True if value round-trips through json the way httpx encodes request bodies."""
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def test_strip_unpaired_surrogates_drops_lone_surrogate():
    # Text truncated mid-emoji leaves a lone high surrogate.
    assert strip_unpaired_surrogates("summarize this \ud83e") == "summarize this "


def test_strip_unpaired_surrogates_preserves_valid_text_and_emoji():
    # A complete emoji is a single code point in a Python str and must survive.
    assert strip_unpaired_surrogates("hello 🧠 world") == "hello 🧠 world"
    assert strip_unpaired_surrogates("") == ""


def test_strip_unpaired_surrogates_recurses_into_containers():
    payload = {
        "messages": [
            {"role": "user", "content": "bad \ud83e"},
            {"role": "assistant", "content": "fine"},
        ],
        "model": "test-model",
        "n": 3,
        "nested": [["deep \udca9"]],
    }
    cleaned = strip_unpaired_surrogates(payload)

    assert cleaned["messages"][0]["content"] == "bad "
    assert cleaned["messages"][1]["content"] == "fine"
    assert cleaned["model"] == "test-model"
    assert cleaned["n"] == 3  # non-str scalars untouched
    assert cleaned["nested"] == [["deep "]]
    assert _utf8_encodable(cleaned)


def test_strip_unpaired_surrogates_sanitizes_dict_keys():
    # A lone surrogate in a *key* (e.g. a client-supplied logit_bias or tool-schema
    # property name) must also be stripped, otherwise httpx still fails to encode.
    cleaned = strip_unpaired_surrogates({"bad\ud83ekey": "value", "ok": 1})

    assert "bad\ud83ekey" not in cleaned
    assert cleaned["badkey"] == "value"
    assert cleaned["ok"] == 1
    assert _utf8_encodable(cleaned)


def test_strip_unpaired_surrogates_returns_input_unchanged_when_clean():
    # Clean payloads (the common case) must not be rebuilt or re-encoded.
    text = "hello 🧠 world"
    assert strip_unpaired_surrogates(text) is text

    body = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    assert strip_unpaired_surrogates(body) is body


def test_strip_unpaired_surrogates_ascii_fast_path_returns_same_object():
    # Long all-ASCII strings hit the isascii() fast path and are returned as-is.
    text = "plain english text without anything fancy. " * 200
    assert strip_unpaired_surrogates(text) is text


def test_sanitize_request_body_runs_registered_sanitizers():
    # The single outbound entry point currently strips unpaired surrogates.
    body = {
        "messages": [{"role": "user", "content": "remember this \ud83e"}],
        "model": "test-model",
    }
    cleaned = sanitize_request_body(body)

    assert cleaned["messages"][0]["content"] == "remember this "
    assert _utf8_encodable(cleaned)


def test_sanitize_request_body_applies_each_step(monkeypatch):
    # Adding a sanitizer to the registry takes effect without touching call sites.
    import mlpa.core.sanitization as sanitization

    monkeypatch.setattr(
        sanitization,
        "BODY_SANITIZERS",
        (sanitization.strip_unpaired_surrogates, lambda b: {**b, "added": True}),
    )

    result = sanitization.sanitize_request_body({"query": "ok"})

    assert result == {"query": "ok", "added": True}
