#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

COST_MAP_FILE="model_cost_map.custom.json"
OVERRIDES_FILE="region_overrides.json"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    exit 1
  fi
}

require_cmd curl
require_cmd jq

if [[ ! -f "${OVERRIDES_FILE}" ]]; then
  echo "Missing overrides file: ${OVERRIDES_FILE}"
  exit 1
fi

echo "Preparing LiteLLM model cost map override..."
curl -fsSL \
  https://raw.githubusercontent.com/BerriAI/litellm/refs/tags/v1.82.0-stable/model_prices_and_context_window.json \
  -o "${COST_MAP_FILE}"

jq --slurpfile overrides "${OVERRIDES_FILE}" '
  reduce ($overrides[0] | to_entries[]) as $item
    (.;
      .[$item.key] = (
        (.[$item.key] // {})
        | .supported_regions = (
          ((.supported_regions // []) + $item.value) | unique
        )
      )
    )
' "${COST_MAP_FILE}" > "/tmp/${COST_MAP_FILE}" && mv "/tmp/${COST_MAP_FILE}" "${COST_MAP_FILE}"

echo "Wrote patched cost map to ${COST_MAP_FILE}"
