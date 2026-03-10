# IBC Remote Access

This document is the durable Phase 1 reference for remotely controlling the secure machine-local IBC service from an iPhone.

Related report:
- [IBC remote-control and cloud report](../reports/ibc-remote-control-and-cloud-options-2026-03-10.html)

## Canonical service surface

The canonical IBC service commands are the secure machine-local wrappers in `/Users/joemccann/ibc/bin/`:

- `/Users/joemccann/ibc/bin/start-secure-ibc-service.sh`
- `/Users/joemccann/ibc/bin/stop-secure-ibc-service.sh`
- `/Users/joemccann/ibc/bin/restart-secure-ibc-service.sh`
- `/Users/joemccann/ibc/bin/status-secure-ibc-service.sh`

The repo helper is optional convenience only:

- [`scripts/ibc_remote_control.sh`](../scripts/ibc_remote_control.sh)

## Current approach

Phase 1 keeps IB Gateway / IBC on the local Mac and uses standard macOS SSH over the Tailscale network.

Important implementation note:
- This Mac uses the GUI `Tailscale.app` variant.
- Per Tailscale's current macOS documentation, Tailscale SSH server support on macOS requires the open-source `tailscaled` variant, not the GUI app variant.
- Phase 1 therefore uses standard macOS SSH over Tailscale, not Tailscale SSH server mode.

## Dependencies

Required:

- `Tailscale.app` installed on the Mac and connected to the target tailnet
- `Remote Login` enabled on macOS so SSH listens on port `22`
- The secure IBC service wrappers present in `/Users/joemccann/ibc/bin/`
- Tailscale installed on the iPhone and connected to the same tailnet
- An iPhone SSH client such as Termius, Blink Shell, or Prompt

Optional:

- A dedicated SSH client key added to `~/.ssh/authorized_keys` if you want key-based auth instead of password auth

## What the repo helper does

`scripts/ibc_remote_control.sh` provides:

- `check`
  - Shows Tailscale install/login state, SSH readiness, SSH auth readiness, and IBC service state
- `tailscale-status`
  - Shows the current Tailscale status output from the installed app bundle
- `tailscale-login [--open]`
  - Starts Tailscale login flow and prints the auth URL
- `ibc-status`
  - Runs the secure machine-local IBC status wrapper
- `ibc-start`
  - Runs the secure machine-local IBC start wrapper
- `ibc-stop`
  - Runs the secure machine-local IBC stop wrapper
- `ibc-restart`
  - Runs the secure machine-local IBC restart wrapper
- `remote-help`
  - Prints example SSH commands using both the canonical secure wrappers and the repo helper

## Validated working configuration on 2026-03-10

The following path was validated successfully:

- Tailscale connected on both the Mac and iPhone
- iPhone SSH client: Termius
- SSH target: `macbook-pro.tail8bfbd8.ts.net` and `100.98.36.17`
- SSH auth mode: password-based macOS login
- Secure IBC command executed successfully from the phone after login
- The secure `local.ibc-gateway` service remained the canonical command surface throughout

## Validation commands

Current readiness:

```bash
./scripts/ibc_remote_control.sh check
```

Current secure IBC service state:

```bash
./scripts/ibc_remote_control.sh ibc-status
```

Direct secure service status:

```bash
~/ibc/bin/status-secure-ibc-service.sh
```

## iPhone login mode

Because `~/.ssh/authorized_keys` is currently absent, iPhone SSH will be password-based unless you later add a dedicated client public key.

This is optional for Phase 1. Password auth should work because the current SSH server advertises `password` and `keyboard-interactive` as valid methods.

## Remote usage flow

1. Make sure the iPhone is connected to the same Tailscale tailnet.
2. Use an SSH client on the iPhone, for example Termius.
3. Connect to the Mac over the tailnet using your local macOS username.
4. Run either the canonical secure commands or the repo convenience helper.

Direct secure commands:

```bash
ssh joemccann@macbook-pro '~/ibc/bin/status-secure-ibc-service.sh'
ssh joemccann@macbook-pro '~/ibc/bin/start-secure-ibc-service.sh'
ssh joemccann@macbook-pro '~/ibc/bin/stop-secure-ibc-service.sh'
ssh joemccann@macbook-pro '~/ibc/bin/restart-secure-ibc-service.sh'
```

Repo convenience wrapper:

```bash
ssh joemccann@macbook-pro 'cd /Users/joemccann/dev/apps/finance/convex-scavenger && ./scripts/ibc_remote_control.sh ibc-status'
ssh joemccann@macbook-pro 'cd /Users/joemccann/dev/apps/finance/convex-scavenger && ./scripts/ibc_remote_control.sh ibc-start'
ssh joemccann@macbook-pro 'cd /Users/joemccann/dev/apps/finance/convex-scavenger && ./scripts/ibc_remote_control.sh ibc-stop'
ssh joemccann@macbook-pro 'cd /Users/joemccann/dev/apps/finance/convex-scavenger && ./scripts/ibc_remote_control.sh ibc-restart'
```

Current Tailscale IP fallback:

```bash
ssh joemccann@100.98.36.17 '~/ibc/bin/status-secure-ibc-service.sh'
```

## Optional next hardening step

If you want key-based login from the iPhone instead of password auth:

1. Generate or import a client SSH key in the iPhone SSH app.
2. Add that public key to `~/.ssh/authorized_keys` on this Mac.

## Operational notes

- This only works while the Mac is powered on and reachable.
- If the Mac sleeps, remote SSH access may fail until the machine wakes.
- Do not expose IBC command server port `7462` publicly.
- Do not expose IB API ports publicly.
- Phase 2 is the private web controller over the Tailscale network.
