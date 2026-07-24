# Smoke Tests

The smoke suite lives in `src/tests/smoke/` and covers one fast happy path for each
auth/data flow that MLPA owns:

- App Attest QA path, including `use-qa-certificates: true`
- Play Integrity
- FxA
- SmartWindow chat data flow
- Memories data flow

These tests assert HTTP status and OpenAI-compatible response shape. They do not
assert model text. Local smoke uses a completion mock. Post-deploy
smoke runs the same request shape against the deployed `/v1/chat/completions`
path via mocked mock `mock`.

## Remote FxA Tokens

When `SMOKE_BASE_URL` is set, the FxA, SmartWindow, and memories smoke
tests use a real FxA bearer token. If `SMOKE_FXA_TOKEN` is provided, the
suite uses that token directly.

If `SMOKE_FXA_TOKEN` is not provided, the suite creates one FxA test user,
fetches the verification email through `TestEmailAccount`, retrieves a bearer
token, reuses that token across the FxA-backed smoke tests, and deletes the test
user during pytest teardown. The default target is FxA stage.

Useful overrides:

- `SMOKE_FXA_ENV`: `stage`;
- `SMOKE_FXA_CLIENT_ID`: OAuth client id; defaults to the smoke client
- `SMOKE_FXA_SCOPES`: space-separated scopes; defaults to `profile`

## Play Integrity

There is no stable deployed (remote) Play Integrity testing path yet. The in-process smoke
test exercises `/verify/play` with a mocked decoder and then calls
`/v1/chat/completions` with `use-play-integrity: true`.

For post-deploy dev/stage smoke runs, Play Integrity requires a real
`SMOKE_PLAY_INTEGRITY_TOKEN`. There is no deployed bypass path for this
flow; the suite is skipped until that fixture exists.
