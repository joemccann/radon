# Trusted subscription CLI payloads

The Python image contains root-owned Claude, Codex, Grok and Antigravity
executables. A container that holds the secret-store key group (`radon-api`)
must never bind-mount host CLI binaries or load host npm modules. The image's unprivileged build smoke checks all four
versions and confirms the executable and its parent are unwritable.

The npm package and complete lockfile pin official distributions and native
optional payloads. Lifecycle hooks are disabled. `install-native.cjs` copies
Claude's native payload, decompresses Grok's Brotli native payload, and retains
Codex's launcher and bundled native resources. Node is copied from the pinned
Docker Official Image; npm itself is absent from the final image.

`antigravity.json` records the version, immutable artifact URLs and SHA512 values
from Google's official installer manifests for Linux amd64 and arm64.
`install-antigravity.py` verifies the complete download before copying the
regular `antigravity` archive member. The mutable installer is never executed.

Primary distribution sources:
- https://registry.npmjs.org/@anthropic-ai/claude-code
- https://registry.npmjs.org/@openai/codex
- https://registry.npmjs.org/@xai-official/grok
- https://antigravity.google/cli/install.sh
- https://github.com/google-antigravity/antigravity-cli

To update, change exact package versions and regenerate the lock with
`npm install --package-lock-only --ignore-scripts --no-audit --no-fund` in this
directory. Update Antigravity artifact URLs and hashes together from the official
manifests. Update the image's version smoke expectations. CI must build and
verify the image before deployment.
