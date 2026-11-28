## Summary

Verified vendor CLI pin bump. Each manifest came from the pinned manifest host, each artifact was downloaded from an allowlisted publisher host and its SHA-512 matched that manifest, and the architecture matched the pin. The Darwin pin keeps Google's team id, which the installer's codesign check enforces at install time; this bump verifies no signature.

## What changed

`docker/app/vendor-clis/antigravity.json`, `docker/app/vendor-clis/package.json`, `docker/app/vendor-clis/package-lock.json`, and `scripts/runner/antigravity-darwin.json` when a newer release verified.

## Test plan

- [x] Focused local: `python3 scripts/ci/bump_vendor_cli_pins.py --apply` verified host, checksum, and arch before writing
- [x] CI: this workflow

## Risk and rollback

Merging to main runs CI, then the Production deploy starts with no approval step. Revert this commit to restore the previous pins. Checksum and signature checks stay in the installers.

## Operator Next Steps

None

## Checklist

- [x] Red/green TDD (failing test, then fix)
- [x] Stage only the files this change owns (no `git add -A`)
- [x] Owner doc updated, or the commit message has a `docs-skip:` reason
- [x] Previous main deploy finished before this push
