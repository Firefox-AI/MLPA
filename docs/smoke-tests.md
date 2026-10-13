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
tests use a real FxA bearer token from `SMOKE_FXA_TOKEN`. Every deployment
verifies tokens against prod FxA, so this must be a prod FxA token. The suite
fails if it is not set.

## Play Integrity

There is no stable deployed (remote) Play Integrity testing path yet. The in-process smoke
test exercises `/verify/play` with a mocked decoder and then calls
`/v1/chat/completions` with `use-play-integrity: true`.

For post-deploy dev/stage smoke runs, Play Integrity requires a real
`SMOKE_PLAY_INTEGRITY_TOKEN`. There is no deployed bypass path for this
flow; the suite is skipped until that fixture exists.
