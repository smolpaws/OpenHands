# PR #18465 — typed session-key forwarding, before/after evidence

## What this adds on top of #18465

#18465 forwards a typed hosting session key to the same-origin `/mars/rpc/*`
bridge, but only for a registered backend whose `kind === "local"` on the
served origin. `ApiKeyEntryScreen` (the first-run/auth-gate form) persisted the
typed key through `BackendForm`, which inferred the kind from the hostname via
`inferKindFromHost` — so on a Cloud hostname (`*.all-hands.dev`,
`*.openhands.dev`) the hosting key was persisted as `kind: "cloud"` and the
bridge skipped it. Result: every `/mars/rpc/*` call — including the
`getAuthState` probe the DigitalOcean panel makes on open — returned
`401 {"error":{"message":"session key required"}}`.

Fix (commit on this branch): pin `ApiKeyEntryScreen` to `fixedKind="local"`.
A hosting session key typed at the auth gate is always a local agent-server
credential for the served origin, never a Cloud backend. The hosting key
authenticates the bridge; the DO PAT authenticates the provider. The hosting
key is never copied into a guest backend's `apiKey`.

## Method (identical prerequisites on both sides)

- Production build only: `VITE_MOCK_API=false`, no MSW/service worker.
- Served with `--host 0.0.0.0` + `--auth-required`, **no baked key**.
  A typed hosting key is the only credential the page holds.
- Browser origin (non-loopback): `https://work-1-….prod-runtime.all-hands.dev/`.
- Isolated base vs head: the **only** difference is #18465's bridge
  (`mars-web-bridge.ts`) — base uses baked-only `getAgentServerHeaders()`,
  head uses #18465's typed-key forwarding. The `fixedKind="local"` change is
  present on BOTH sides so the flow can reach the DigitalOcean panel.

## Result

| Step | BEFORE (base bridge) | AFTER (#18465 head) |
|---|---|---|
| Type hosting key, submit | persisted (kind "local") | persisted (kind "local") |
| `POST /mars/rpc/getAuthState` | **401** `session key required` (×3) | **200** (auth state) |
| `POST /mars/rpc/savePat` | n/a (blocked by 401) | **200** |
| `POST /mars/rpc/listAgentConfigs` | n/a | **200** — real configs listed |
| `POST /mars/rpc/listSessions` / `listConfigSessions` | n/a | **200** |
| DigitalOcean panel | unauthenticated (cannot list agents) | authenticated, real agent config `pr18604-live` listed |

The wrong-key case is also shown: an invalid typed key is refused up front
("Invalid API key…") and does **not** reach the bridge.

## Screenshots (commit-pinned, see below)

- `before-01-hosting-key-gate.png` — auth gate prompts for hosting key
- `before-02-wrong-key-refused.png` — invalid key refused up front
- `before-03-do-panel-401.png` — DO panel stuck unauthenticated (401)
- `after-01-hosting-key-gate.png` — same gate (same precondition)
- `after-02-do-panel.png` — DO panel authenticated after typed key forwarded
- `after-03-do-agents-authenticated.png` — real agent config listed

No secrets are present in any screenshot: key/token inputs are `type="password"`
and raw network payloads (which contained resource IDs) are not published.
