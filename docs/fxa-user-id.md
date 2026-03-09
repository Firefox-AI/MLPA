# Get FxA user_id

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Clone this repo, then run:
   ```bash
   make setup
   ```

## Usage

From the repo root:

**With a Bearer token**
```bash
make fxa-user-id ARGS="--token YOUR_BEARER_TOKEN"
```

**With email and password**
```bash
make fxa-user-id ARGS="--email you@example.com --password YOUR_PASSWORD"
```

Output is the FxA user_id (a single line).

## Options

| Option | Short | Description |
|--------|--------|-------------|
| `--token` | `-t` | FxA OAuth Bearer token. Prints the user_id for that token. |
| `--email` | `-e` | FxA account email. Requires `--password`. |
| `--password` | `-p` | FxA account password. Use with `--email`. |

You must provide either `--token` or both `--email` and `--password`.
