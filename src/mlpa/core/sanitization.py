import re
from typing import Any

# NOTE: matches lone UTF-16 surrogates; used to skip the rebuild for clean strings.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def strip_unpaired_surrogates(value: Any) -> Any:
    """Recursively drop lone UTF-16 surrogates from strings in ``value``.

    Clients occasionally truncate text mid-emoji, leaving an unpaired surrogate
    (e.g. ``\\ud83e``). Any code point in the surrogate range is invalid on its
    own in a Python str, and serializers such as httpx encode request bodies with
    ``ensure_ascii=False`` before encoding as UTF-8 — so a lone surrogate raises
    ``UnicodeEncodeError`` and the payload never goes out (it surfaced in prod as
    a 502 "Failed to proxy request"). Dropping the bad code point lets the rest
    of the text through unharmed. Lists, dict keys and dict values are sanitized
    recursively; non-str scalars and clean values are returned unchanged (no copy).
    """
    if isinstance(value, str):
        # isascii() reads a cached flag (O(1)); a surrogate is never ASCII, so this
        # skips the regex scan for the common all-ASCII case.
        if value.isascii() or _SURROGATE_RE.search(value) is None:
            return value
        return value.encode("utf-8", "ignore").decode("utf-8")
    if isinstance(value, list):
        cleaned = [strip_unpaired_surrogates(item) for item in value]
        if all(new is old for new, old in zip(cleaned, value)):
            return value
        return cleaned
    if isinstance(value, dict):
        cleaned = {
            strip_unpaired_surrogates(key): strip_unpaired_surrogates(item)
            for key, item in value.items()
        }
        if cleaned.keys() == value.keys() and all(
            cleaned[key] is value[key] for key in value
        ):
            return value
        return cleaned
    return value


BODY_SANITIZERS = (strip_unpaired_surrogates,)
RESPONSE_SANITIZERS = (strip_unpaired_surrogates,)


def sanitize_request_body(body: Any) -> Any:
    """Run an outbound request body through every sanitizer in ``BODY_SANITIZERS``."""
    for sanitizer in BODY_SANITIZERS:
        body = sanitizer(body)
    return body


def sanitize_response_body(body: Any) -> Any:
    """Run an upstream response body through every sanitizer in ``RESPONSE_SANITIZERS``."""
    for sanitizer in RESPONSE_SANITIZERS:
        body = sanitizer(body)
    return body
