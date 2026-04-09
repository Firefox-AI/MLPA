# patch-cost-map

- `patch-model-cost-map.sh`: downloads the upstream LiteLLM model cost map and applies the region overrides.
- `region_overrides.json`: maps model IDs to additional supported regions.

## How the Cost Map Override Works

1. `patch-model-cost-map.sh` downloads LiteLLM's upstream `model_prices_and_context_window.json` into `model_cost_map.custom.json`.
2. It patches keys defined in `region_overrides.json`, for example:

   `{vertex_ai/qwen/qwen3-235b-a22b-instruct-2507-maas: ["us-south1"]}`

3. The patch adds `us-south1` to the model's `supported_regions`.
4. The resulting `model_cost_map.custom.json` can be mounted or referenced by your LiteLLM deployment as needed.
