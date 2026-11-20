"""Update only the Compose LiteLLM image after testing its immutable digest."""

import argparse
import re
from pathlib import Path

IMAGE_REPOSITORY = (
    "us-docker.pkg.dev/moz-fx-llm-proxy-prod-b0b8/llm-proxy-prod/litellm-gateway"
)


def update_image(compose: str, image: str) -> str:
    if not re.fullmatch(re.escape(IMAGE_REPOSITORY) + r"@sha256:[0-9a-f]{64}", image):
        raise ValueError("Expected a gateway image pinned to its sha256 digest")
    # Preserve comments, anchors and formatting in the rest of the Compose file.
    pattern = r"(?m)^(  litellm:\n    image: )[^\n]+$"
    updated, count = re.subn(pattern, lambda match: match[1] + image, compose)
    if count != 1:
        raise ValueError("Expected exactly one services.litellm.image entry")
    return updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    path = Path("litellm_docker_compose.yaml")
    path.write_text(update_image(path.read_text(), args.image))
