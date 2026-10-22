# Radon Security Audit Playbook

A reusable, extensible process for auditing Radon's security. Re-run it on a
cadence (quarterly + before major releases) and **add to it** as the app grows.

## Assets

| Asset | Purpose |
|---|---|
| `.claude/workflows/security-audit.mjs` | The audit engine. Fans out one finder per dimension, adversarially verifies every finding, runs a completeness + regression critic. Returns structured JSON. |
| `scripts/security/gen_security_report.py` | Deterministic JSON → HTML report renderer. |
| `docs/security-audit-playbook.md` | This file — methodology, dimension catalog, regression invariants, audit log. |
| `docs/security-approved-tools.md` | Tool versions the nightly security loops were last checked against (informational since 2026-09-19; only gitleaks is still pinned, in CI) and how each is re-derived. |
| `docs/security-audit-<date>.html` | One rendered report per run. **This repo is PUBLIC — the report enumerates the attack surface and quotes secret/topology strings, so it is gitignored (`docs/security-audit-*.html`) and MUST be filed in the private `radon-cloud:security-archive`, never committed here.** |

## How to run

1. **Audit** (in Claude Code): `Workflow({ name: "security-audit", args: { repoRoot: "<clone root>" } })`.
   The Workflow runtime has no filesystem or environment, so pass the clone
   root (`node .claude/workflows/resolveRepoRoot.mjs --print`; the nightly
   runner exports it as `$RADON_REPO_ROOT`). Without it, each agent uses its
   own `git rev-parse --show-toplevel`. Subset:
   `args: { focus: ["authn-authz","sqli"] }`. Extend on the fly:
   `args: { extraDimensions: [{ key, label, scope }] }`.
2. **Capture** the workflow's returned JSON (the Workflow tool writes it to the
   task-output file).
3. **Render**:
   `python3 scripts/security/gen_security_report.py <task-output.json> docs/security-audit-<date>.html --date <date> [--fixes fixes.json]`
   (`--fixes` is an optional list of `{id,sev,title,finding,fix,files[],tests}`
   documenting patches you shipped for that run.)
4. **Triage & patch** — see policy below. **Re-verify** every patch with red/green tests.
5. **Record** the run in the Audit Log table + update memories/lessons.

## Dimension catalog

Edit the `DIMENSIONS` array in the workflow to extend. Keep each scope concrete:
name the files to read and the exact weaknesses to hunt.

1. `authn-authz` — Clerk/JWT, middleware default-deny, trusted-local bypass, ALLOWED_USER_IDS, IDOR, admin routes
2. `sqli` — every db.execute / hrana_execute / cursor.execute call site; parameterization
3. `cmd-path-ssrf` — subprocess/PI allowlists, spawn, path traversal in file routes, SSRF in server fetch
4. `secrets` — leakage to client/logs/errors, committed secrets, git history
5. `client-xss` — dangerouslySetInnerHTML, CSP, security headers, CORS, open redirect
6. `api-idor-validation` — input validation, mass assignment, IDOR, rate limiting on mutating routes
7. `vps-infra-deploy` — GitHub Actions, Docker, Caddy, SSH/deploy, exposed ports, SHA-pinning
8. `dependencies` — npm/pip audit, runtime-reachability triage, lockfile integrity
9. `websocket-relay` — ticket auth, forwarding-header trust, origin check, subscription limits, maxPayload
10. `data-pii` — share routes (all 5 pairs), demo isolation + demo-mirror scripts, account figures reachable without authz
11. `cloud-archive` — Backblaze B2 portfolio cold-archive: bucket/object ACL, delete-before-verify data loss, S3 credential leakage
12. `regression` — re-checks that every durable invariant below still holds (report ONLY on regression)

## Durable regression invariants (re-checked every run)

Each fixed issue becomes an invariant the `regression` dimension verifies. **Add a
line here whenever you ship a security fix.**

- **WS relay trust** — `scripts/lib/wsTrust.js:shouldSkipTicketValidation` must
  return false when a forwarding header is present (Caddy proxies `/ws*` to
  loopback, so peer address alone is untrustworthy). The relay must call it.
  (`feedback_ws_relay_trust_forwarding_headers`)
- **Share-content allowlist** — the 5 `*/share/content` routes serve ONLY
  `tweet-<type>-<date>[-card-N].html` from directly inside `reports/`, scoped per
  type. Never widen to arbitrary `reports/` paths.
  (`feedback_share_content_allowlist_not_arbitrary_reports`, `web/lib/shareReportPath.ts`)
- **AUTH_EXEMPT_PATHS** — must NOT contain `/docs` or `/openapi.json` (trusted-local
  only); pinned by `test_health_payload.py` + `test_route_authz_matrix.py`.
  (`feedback_auth_exempt_paths_double_pin`)
- **Middleware perimeter** — default-deny (`if(!isPublicRoute)`), ALLOWED_USER_IDS
  operator gate, the `/api` matcher always running, `PUBLIC_SHARE_API_ROUTES` a
  FIXED explicit list (no `/api/**/share` regex).
  (`feedback_middleware_is_the_perimeter`, `project_prod_perimeter_operator_allowlist`)
- **Mutating Next API calls are same-origin.** `web/middleware.ts` calls
  `sameOriginMutationResponse` before the session gate, and `requireRouteAccess`
  calls `decideSameOriginMutation`. The only browser `Sec-Fetch-Site` value that
  passes is `same-origin`. `none` is rejected. When that header is absent,
  `Origin` has to match the allowlist in `web/lib/sameOrigin.ts`.
  (`web/tests/same-origin-mutation.test.ts`)
- **Trust-scoped surfaces** — `/health` and `/api/service-health` must never emit
  account IDs, IB topology, or secret-shaped error strings to anonymous callers
  (service-health runs `scrubSecrets`).
  (`feedback_health_endpoint_public_leak_and_trust_chokepoint`)
- **No Next.js spawn with user args**; subprocess + PI exec allowlists intact.
- **Hosted MCP env isolation** — `radon-mcp.service` (the one unit terminating
  anonymous internet traffic) loads only `/etc/radon/mcp.env` (Clerk verification
  inputs, `ALLOWED_USER_IDS`, `RADON_MCP_*`), never `/etc/radon/env`, and runs
  sandboxed with `/etc/radon/env` inaccessible.
  (`cloud/tests/test_systemd_services.py::TestHostedMcp`, `cloud/tests/test_deploy_resilience.py`)
- **Hosted MCP resource bounds** — POST `/mcp` bodies over `MAX_REQUEST_BYTES` are
  413'd before parsing, an unknown-kid JWKS refetch is limited to one per
  `JWKS_REFRESH_COOLDOWN_SECONDS`, and every upstream read runs off the event loop.
  (`scripts/tests/test_mcp_hosted.py`)
- **CSP enforced (not Report-Only)** — `web/middleware.ts:buildCspWithNonce` ships a
  per-request nonce with NO `'unsafe-inline'`/`'unsafe-eval'` in `script-src`, and
  `next.config.mjs` emits NO CSP header. Must keep `worker-src 'self' blob:` and the
  Clerk host allowlist (NOT `'strict-dynamic'`, which blocks Clerk's loader).
  (`feedback_csp_strict_dynamic_breaks_clerk_loader`, `web/tests/csp-nonce.test.ts`)
- **Public unauth routes rate-limited** — the 5 `*/share` POST generators + GET
  `/api/share/pnl` call `rateLimit()` (`web/lib/rateLimit.ts`) before any heavy work.
  (`web/tests/rate-limit.test.ts`)
- **Unauthenticated render inputs are bounded, not just rate-limited** — a request
  limiter counts requests, not the cost of one request, so every caller-supplied
  value an image route hands to satori is bounded BEFORE layout: the canvas by
  `web/lib/ctaImageLayout.ts`, and the strings by `clampCardText`
  (`web/lib/sharePnlHero.ts`) in BOTH dimensions — length, and character class.
  The character class matters because `next/og` always installs satori's
  `loadAdditionalAsset`, so a grapheme the bundled faces cannot resolve turns a
  render into an outbound fetch plus a process-wide cache entry keyed on the
  caller's text. A new unauthenticated renderer input without both bounds is a
  finding even when the route is rate-limited.
  (`web/tests/share-image-bounds.test.ts`, `web/tests/share-pnl-text-bounds.test.ts`)
- **Ops admin routes fail CLOSED** — `/api/admin/{stack/restart,ib/restart,
  ib/reset-backoff,services/[unit]/[action]}` call
  `requireRouteAccess(..., { operatorOnly: true })` and 403 when the allowlist is
  empty (middleware `isAuthorizedUser` fails OPEN by design; these must not
  inherit that). `admin/demo-users` is the one exception, gated by
  `requireDemoAdmin()` on `DEMO_ADMIN_USER_IDS`.
  (`web/tests/api-routes-smoke-admin.test.ts`)
- **CI supply chain** — third-party GitHub Actions SHA-pinned; the gitleaks binary
  download is SHA256-verified; `requirements.txt` pinned to the VPS's running
  versions. (`feedback_pin_requirements_to_vps_not_laptop`)
- **Weekly full-history secret scan** — `.github/workflows/gitleaks-history.yml`
  rescans the entire fetched history every Monday, pages via a `security`-labeled
  GitHub issue on failure, and never blocks a merge.
- **CORS explicit allowlist** — FastAPI CORS uses `allow_origins=[...]` (explicit
  hosts), NOT a `https://.*\.radon\.run` wildcard regex (which a subdomain
  takeover could ride). `allow_credentials` stays False.
  (`scripts/api/tests/test_cors_allowlist.py`)
- **Order idempotency** — `/api/orders/place` dedups concurrent/just-repeated
  identical placements (in-flight + short-TTL content hash, or an explicit
  client `idempotencyKey`) so a double-click never doubles a real-money position.
  (`web/lib/orders/orderIdempotency.ts`, `web/tests/order-place-idempotency-route.test.ts`)
- **Share allowlist filesystem pin** — `PUBLIC_SHARE_API_ROUTES` exactly matches the
  `*/share` + `*/share/content` route files on disk; a new share route can't ship
  unlisted (nor a listed route go missing).
  (`web/tests/middleware-share-allowlist.test.ts`)
- **New read admin routes gated** — `/api/admin/{host-metrics,slo,reliability,
  edge-health}` enforce operator authz and never leak host telemetry to a signed-in
  demo user, same as the mutating admin routes.
- **B2 archive delete-safety** — `scripts/archive_portfolio_snapshots.py` never
  deletes Turso snapshot rows before the B2 partition write is verified, never sets
  a public bucket/object ACL, and never logs `RADON_ARCHIVE_S3_*` credentials.
  (`scripts/tests/test_archive_portfolio_snapshots.py`)
- **Public repo hygiene** — the GitHub `radon` repo is PUBLIC. No secret may be
  committed; git *history* must stay clean (2026-07-05 sweep found creds pending
  rotation — verify purge status each run).
  (`project_public_repo_leak_sweep_2026_07_05`)
- **Stored-credential egress is vendor-pinned** — any validator/probe that sends a
  credential which the server merged from the SecretStore/env (not submitted by the
  caller) must hardcode or allowlist the vendor destination; a caller-supplied URL
  field is an egress decision, never preference (Turso: https + `*.turso.io` only).
  (`scripts/tests/test_credential_validators.py::TestTursoEgressPin`,
  `scripts/api/tests/test_credentials_routes.py::TestValidateEgressPin`)
- **Long-held validator threads are bounded** — any route that runs a vendor
  validator/probe off-loop (tens of seconds per call) caps what is in flight and
  refuses a repeat for the same target inside a cooldown with 429 + `Retry-After`;
  the refused request makes no vendor call.
  (`scripts/api/tests/test_credentials_routes.py::TestValidatorThrottle`)
- **Scan cooldowns are keyed on the route** — a cache miss inside a scan's
  cooldown window (different `preset`, explicit-ticker overwrite) is refused with
  429 + `Retry-After`, never re-spawned; a request parameter must not be able to
  open a fresh subprocess per distinct value.
  (`scripts/api/tests/test_leap_route.py::test_leap_cooldown_is_keyed_on_route_not_preset`,
  `scripts/api/tests/test_garch_convergence_route.py::test_garch_cooldown_is_keyed_on_route_not_preset`)
- **Spawn-and-persist forms are pinned as mutations** — a route whose parameter
  turns a cached read into a subprocess that writes Turso must expose that form as
  its own (method, path) pinned `mutate.*` in `assistant_catalog.py`, never as a
  query flag on a `read` pin (the assistant strips the query before matching).
  (`scripts/api/tests/test_assistant_catalog.py::TestAssistantCatalog::test_backtest_refresh_is_a_mutation_not_a_read`)
- **Path-interpolated symbols are validated at the boundary** — any query/path
  value that is interpolated into an outbound vendor URL path is matched against
  the module's existing ticker pattern and refused with 400 before any client is
  constructed.
  (`scripts/api/tests/test_internals_skew_history_route.py`)
- **Transient secret-bearing scratch files are gitignored** — any wrapper or
  script that stages a filtered copy of an env file (e.g. `web/.env.scrub`
  between create and rm in the nightly wrappers) must write it under a
  gitignored pattern, so a crash inside the window cannot leave a
  commit-visible secret file in a PR-opening clone. Pinned by
  `cloud/tests/test_env_example.py::TestGitignore`.
- **A unit that renders third-party content gets least-privilege env and network**
  — a container whose workload parses or renders untrusted external content
  (e.g. the newsfeed's sandbox-disabled Chromium) receives only the env keys
  its own code reads, never the full `/etc/radon/env` set, and runs on an
  isolated bridge network, never `--network host`. Pinned by
  `cloud/tests/test_app_runtime.py::test_run_newsfeed_env_file_carries_only_its_allowlisted_keys`
  and the per-unit network assertions in
  `test_run_allowlisted_unit_uses_host_net_and_radon_user`.
- **Root provisioning never dereferences a path an unprivileged account can replace**
  — `cloud/scripts/setup-vps.sh` runs as root; every chmod/chown of the env file passes `require_regular_file` (a symlink is refused, never followed)
  and every unit, drop-in, journald conf, Caddyfile, helper, sudoers and polkit
  install from the radon-owned checkout goes through `stage_from_checkout`
  (regular-file check, root-only 0600 staging copy, byte re-check, then install
  at the final mode). `/etc/radon` is root-owned (`root:radon 1770`), links under
  `/home/radon/.ssh` and at `/var/lib/radon/media` are refused,
  a one-shot `-L` check never guards a later path-based `chmod`/`setfacl`
  (root changes the media mode only through `chmod_dir_nofollow`, and every
  ACL change inside media, including the physical `-R -P` walks, runs as
  `radon`), and the Docker,
  NodeSource, and Caddy apt keys are fingerprint-pinned. The canonical env file is 0640 root:radon
  (group radon reads; the service account cannot rewrite it).
  Invariant: root never acts BY NAME inside a radon-owned directory
  (`/var/lib/radon` and everything under it). A check-then-use pair there is a
  race radon wins by renaming an entry. Root writes, chmods and renames there
  through an `O_NOFOLLOW` fd with `dir_fd` renames (`install_state_file`,
  bootstrap `atomic_install`, `chmod_dir_nofollow`); ACL changes inside
  `media/` run as radon, the owner. Pinned by
  `cloud/tests/test_refresh_control_plane.py::test_marker_publish_never_acts_on_a_radon_swappable_name`
  and `cloud/tests/test_caddy_media_group.py::test_grant_never_follows_a_media_dir_swapped_mid_grant`
  / `::test_grant_does_not_follow_media_swapped_for_a_link_after_the_check`.
  Corollary for container bind sources, where no fd can be handed to the
  engine: the engine resolves the source BY NAME when it mounts, so the
  verified device, inode, and inode generation (`FS_IOC_GETVERSION`, when the
  filesystem provides it) are recorded at validation and re-resolved as the
  last statement before the engine runs. A directory recreated at the same
  inode is still a different directory.
  (`radon-app-runtime` `resolve_bind_source` / `recheck_bind_sources`). That
  narrows the window to the engine's own mount; closing it needs an
  inode-pinned staging mount under a root-owned anchor and is an operator
  action. Pinned by
  `cloud/tests/test_app_runtime.py::test_run_refuses_a_bind_source_replaced_after_validation`
  and `::test_bind_source_recheck_is_the_last_step_before_the_engine`.
  Invariant: `radon` is NOT in the docker group (Docker socket access is
  root-equivalent). Gateway container operations go through the root-owned
  `radon-docker-gw` shim; `setup-vps.sh` strips any legacy membership and
  refuses to re-add it. Finding `radon` in group `docker` on a host is a
  defect, not an accepted risk.
  Invariant: `caddy` is NOT in the radon group. Group radon reads
  `/etc/radon/env` (0640 root:radon). Caddy serves `/var/lib/radon/media`
  via dedicated group `radon-media` and a parent traverse ACL; setup and
  `publish-caddy` migrate existing hosts.
  (`cloud/tests/test_caddy_media_group.py`)
  (`cloud/tests/test_setup_vps_privileged_paths.py`,
  `cloud/tests/test_grok_responder_env.py::test_root_never_operates_by_name_inside_radon_owned_trees`)
- **Startup never dies on stored data, and nothing unexportable persists** — a
  credential value `os.environ` cannot hold is refused at the validation
  chokepoint before any store write, and every boot-time env-export site skips
  and logs a row it cannot export instead of aborting the FastAPI lifespan.
  (`scripts/api/tests/test_credential_value_env_safety.py`)
- **Order-limit inputs fail closed** — a supplied quantity/price/strike that is
  not a finite parseable number refuses the order (never coerces to 0/None and
  skips a bound); a future with no valid contract multiplier refuses; a stock
  leg in a BAG never nulls the combo loss computation.
  A SELL BAG prices each leg with its side inverted, and a leg with no action
  stays long. A leg labelled STK that also carries an expiry, a right, or a
  strike above zero is refused (`ORDER_COMBO_LEG_SEC_TYPE`) rather than skipped.
  A future modify keeps that order's contract multiplier and refuses when the
  multiplier is missing (`ORDER_FUTURE_MULTIPLIER`).
  (`scripts/tests/test_order_limits_fail_open_inputs.py`)
- **Unattended agent push authority is enforced by git, not prompt text** — a
  push-capable Grok responder cycle installs the `fix/*`-only `pre-push` guard
  (no deletes, no tags) before launching the agent and stands down when it
  cannot; the PR helper refuses merge-shaped `gh` invocations by token scan;
  page-derived summaries are flattened before entering PR metadata.
  (`scripts/tests/test_grok_push_guard.py`)
- **An automated publish gate scans everything the push publishes** — the IR
  gate (`scripts/ir_push_gate.py`) scans every commit's own patch (not the
  aggregate diff), with `--text --no-textconv` so branch attributes cannot
  hide a blob, every added path and the branch name; content it cannot read
  as text refuses the push. Findings never echo the matched value.
  (`scripts/tests/test_ir_push_gate.py`)
- **Subscription credentials stop at the trust boundary of what consumes them**
  — an unpinned third-party agent CLI subprocess gets an explicit env
  allowlist, never `os.environ` (`subscription_tokens.CLI_ENV_ALLOWLIST`,
  `model_ladder._ANTIGRAVITY_CLI_ENV_ALLOWLIST`); operator credential dirs
  bind only into containers whose unit actually runs a ladder rung; secrets
  never ride a subprocess argv.
  (`scripts/tests/test_model_ladder.py`, `cloud/tests/test_app_runtime.py`,
  `scripts/tests/test_fetch_x_xai_token.py`)
- **A pooled socket is retired, never parked, when a response is left
  unconsumed** — both bounded Turso transports read a response under a hard
  byte ceiling, so an over-ceiling body leaves `http.client` mid-response; that
  connection is closed instead of returned to the thread-local pool, the
  truncated body is refused rather than parsed, and the request guard covers
  `http.client.HTTPException` (`CannotSendRequest` / `ResponseNotReady` are not
  `OSError`) so a socket poisoned by any route still takes the single
  reconnect instead of wedging that thread for the process lifetime.
  (`scripts/tests/test_hrana_keepalive_oversized_body.py`)

- **Every edge handle fronting an app process declares a body cap** — Caddy is
  the only size bound in front of Next.js: `web/middleware.ts` never reads the
  body, and almost every route handler calls `request.json()` with no size
  check, so whatever the edge forwards buffers whole in the single Node process
  that also serves the live trading UI. Each `handle` in `cloud/caddy/Caddyfile`
  that dials `127.0.0.1:3000` carries `request_body { max_size 12MB }` (12MB is
  the largest payload any route legitimately accepts, `/api/assistant*`), and
  `/api/ib/*`, `/mcp*` and the TradingView webhook keep their tighter caps. A
  new edge handle without a cap is a finding, not a default.
  (`cloud/tests/test_caddyfile.py::TestEveryNextjsHandleBoundsTheBody`)

- **A changed-path list is read NUL-separated, never line-split** — `git diff
  --name-only` C-quotes any path carrying a non-ASCII byte, a quote or a
  newline, so the emitted line is `".github/workflows/\303\251.yml"` and a
  prefix test against it matches nothing. The same root cause produced a
  publication-refusal bypass in `scripts/grok_fix_pickup.py` and a silently
  skipped CI gate in `scripts/ci/path_filter.py`. Every consumer passes `-z`
  and splits on NUL, and `--no-renames` with it: rename detection is on by
  default and a detected rename emits only the DESTINATION, so moving a
  protected file OUT of `.github/` showed the permitted destination alone, and
  moving a runtime file out of `scripts/` selected no python gate. A new
  `--name-only` call without both flags is a finding.
  (`scripts/tests/test_grok_fix_pickup.py`, `scripts/tests/test_path_filter.py`)
- **An unattended gate never judges a change to itself** — pull-request CI runs
  the candidate's own workflow files, so the release coordinator and the
  private bridge refuse review for any candidate whose base or head tree
  differs under `.github/` and leave it to a human merge. Any new automated
  merge or deploy authority must answer "can the candidate change the gate that
  judges it?" before it ships. (`scripts/tests/test_unattended_release.py`)

- **A risk guard prices on the field the executing path reads** — the notional
  cap, Gate 3 and the Kelly guard all preferred `limitPrice`, but a stop order
  is built as `StopOrder(stopPrice=...)` and its limit never reaches IB, so a
  decoy limit alongside a real stop bounded the order on a value with no
  effect. All three now share `order_limits.effective_order_price`, which takes
  the worse of the two prices for both stop forms: a stop order has no
  execution price, so neither supplied field may lower the bound. A schema that
  accepts a price field the placement funnel ignores is a finding whenever a
  cap reads it. (`scripts/tests/test_order_limits.py`,
  `scripts/tests/test_bankroll_pref_and_debit_loss.py`)
- **A demo quota is priced on what the request costs upstream, not on its
  method** — `POST /api/previous-close` reads prices, but one request reaches
  up to 30 symbols and each uncached symbol costs a quote plus a fallback, so
  it pays a dedicated fan-out tier instead of the ordinary-read ceiling. Not
  the producer tier either: it fires automatically off the price stream, and
  the producer daily bucket is shared with every scan, so a backfill burst
  would lock operator controls out for the day. An unresolvable symbol is
  remembered briefly, because the day cache stores only resolved closes and a
  replayed batch otherwise re-issued the whole fan-out; a source that failed
  rather than answered is never remembered. Every daily ceiling carries
  `Retry-After` so a refused client waits the window out instead of re-polling
  at its maximum backoff. (`web/tests/demo-rate-tier.test.ts`,
  `web/tests/previous-close-miss-cache.test.ts`, `web/tests/demo-gate.test.ts`)
- **The isolated Grok responder executes root-owned code (DS-2026-10-17-16).**
  `radon-grok-responder.service` runs
  `/usr/bin/python3.13 -E -S /usr/local/lib/radon/grok-responder/scripts/grok_page_responder.py`.
  That tree is installed root-owned by `install-grok-responder-code.sh` from
  the provision store (`setup-vps.sh`, every `install-units`, and responder
  account prepare/refresh). It is on `ReadOnlyPaths` and absent from
  `ReadWritePaths`. `/var/lib/radon-responder/repo` stays writable as the
  worktree (`RADON_GROK_REPO`); the service does not execute it. A host whose
  installed unit still points at the repo needs one
  `setup-grok-responder-account.sh` refresh from a trusted stage after this
  release is in the provision store. A green deploy alone does not swap that
  unit.
  (`cloud/tests/test_grok_responder_code.py`)

## Triage & patch policy

- Severity = verifier-revised, real-world impact for a **single-operator** app, not
  raw scanner output. Adversarial verification routinely downgrades scanner
  "criticals" that don't apply to this app's default-deny architecture — trust it,
  but confirm the reasoning.
- Patch order: **exploitable** first, then defense-in-depth. Patch the safe,
  high-confidence ones with red/green tests; **defer** anything needing an
  environment-specific verification step (production build or deployed VPS configuration) and document it.
- **Do NOT auto-push.** `git push origin main` IS a production deploy (CI SSHes to
  Hetzner). Land perimeter/relay changes only on the operator's explicit go; after
  deploy, verify the relay + a real authed page render.
- Don't blind-bump a framework (next/@clerk) just because a scanner flags it —
  verify the exploit applies, then gate the bump on a full build + perimeter-smoke.
- **Never quote the literal you found.** An audit report describes a credential
  finding by naming the variable and its location, never by reproducing the
  value. Writing `TWS_PASSWORD=<the real value>` into a finding turns the report
  itself into the leak — and reports get committed. This is not hypothetical:
  the 2026-07-18 report did it, the value went into a PUBLIC repo, the secret
  scan flagged it, it was allowlisted as "already public, not a new exposure",
  and a later rebase renumbered that commit, orphaned the allowlist, failed the
  scan and silently skipped the deploy for hours. One quoted string, five hours
  of no deploys and a permanent history entry.

  This is enforced, not just requested: `scripts/security/gen_security_report.py`
  redacts credential literals inside `esc()`, which every rendered field passes
  through, so a finding cannot carry a value into the HTML no matter how it is
  worded or which field it lands in. The variable name and prose survive so the
  finding stays actionable. Verified both directions — the raw findings JSON
  trips gitleaks, the rendered report does not.
  Tests: `scripts/tests/test_security_report_redaction.py`.
  The canonical scrubber `scripts/credential_redaction.py` and the public
  issue/PR sanitizer also redact multi-line private-key blocks, including a
  block cut off before its END line, and both cover cookie headers,
  session/CSRF keys and U, DU and F IB account ids. Cookie coverage is not just
  the raw `Set-Cookie: ...` header line: `cookie` is a credential key name in
  its own right, so a serialized header (`{"Cookie": "..."}`, a dict dump, an
  env line) is redacted and detected by the same generic assignment rules as
  any other credential-named key. Detection reads every `name=value` pair on a
  cookie header, not only the first. Both also share one triple-delimited
  assignment rule (`TOKEN = """` on one line, the value on the next), so a
  Python or TOML multi-line value is not mistaken for an empty two-quote value
  with its real credential left in the body. They also share the YAML
  block-scalar rule (`password: |`, the value on the following lines); an
  excerpt that is quoted (`> `) or commented (`# `) repeats that lead on every
  continuation line, and the body is still redacted. The scrubber's multi-line
  key alphabet includes `authorization`.
  The scrubber's own patterns are a denial-of-service surface, because the
  publication gate runs them over branch text an outside contributor shapes:
  every repeated character class in them is bounded, so a long run of
  identifier characters cannot drive a quadratic backtracking scan and stall
  the gate.

  Redaction is a backstop for the renderer, not a licence to put secrets in the
  JSON. If a value is genuinely needed to reproduce a finding, reference where
  it lives (`docker/ib-gateway/.env:4`) and leave it there.

## Audit log

| Date | Dimensions | Raw → Confirmed (exploitable) | FP | Report | Notes |
|---|---|---|---|---|---|
| 2026-06-28 | 10 + critic | 58 → 48 (8) | 12 | `radon-cloud:security-archive/docs/security-audit-2026-06-28.html` | 8 fixes shipped (WS relay bypass, share-content disclosure, ws bump, /docs gate, service-health scrub, path guards, CI hardening, HSTS). Deferred next/@clerk bumps + VPS config. `radon-cloud:security-archive/tasks/security-audit-2026-06-28.md`. |
| 2026-06-29 | 11 + critic | 58 → 26 (**0**) | 34 | `radon-cloud:security-archive/docs/security-audit-2026-06-29.html` | Re-audit after the lower-priority block landed (CSP enforce, rate-limit, Actions SHA-pin, deps pin, media HSTS). **Zero exploitable** (24 info, 2 low). Critic confirmed all prior fixes intact. Closed the 2 lows + stale repo HSTS: gitleaks SHA256 check, admin routes fail-closed, `docker/caddy/Caddyfile` media HSTS. |
| 2026-07-18 | 12 (+`cloud-archive`) + critic | 38 → 31 (**1**) | 8 | `radon-cloud:security-archive/docs/security-audit-2026-07-18.html` (rendered locally; the repo is PUBLIC so the report is gitignored — file it in the private archive) | Post-B2-archive + share-route-proliferation audit. Playbook extended: new `cloud-archive` dimension, PUBLIC-repo framing, 4 new regression invariants (share allowlist FS pin, read-admin gating, B2 delete-safety, public-repo hygiene). The **1 exploitable/high is non-code**: real portfolio figures still resolvable in PUBLIC git history (HEAD-only scrub `772c6493`, no `filter-repo`). **7 fixes shipped** (preset traversal, rate-limit rightmost-XFF, `/admin/services` operator gate, secret-scrub broaden ×2 chokepoints, share-iframe sandbox, IBKR username redact at HEAD, prune docstring footgun). **Deferred to operator** (require VPS/history/rotation): git-history purge + **credential rotation** (VNC/IB/IBKR-username — still in history & closed PR #7 blob), `wsTrust`/relay 0.0.0.0 hardening, `menthorq/cta` Next.js `spawn`, CSP `https:` wildcard, `requirements-api.txt` ceilings, lockfile dedupe. |
