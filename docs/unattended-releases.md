# Unattended maintenance releases

The operator has granted standing release authority for completed maintenance
fixes to `com.radon.operator-releases`, a separate operator LaunchAgent. The
nightly machine account remains unable to merge main or reach production. The
controller's installed policy, code, SSH configuration and GitHub login are
outside the bot's writable home. Models receive the diff as data, with no tools,
GitHub token, SSH credentials, user config, repository hooks or project rules.

## Release sequence

Every five minutes the controller first reconciles the current deployed main,
then resumes its durable release journal before considering new work. Eligible branches and authors come from the installed
owner-controlled policy. Discovery rejects non-main targets before even updating
a candidate branch; fresh merge and already-merged recovery reads bind both the
expected head SHA and the main target. Drafts, forks, absent or incomplete checks, cancelled
checks, stale heads, missing independent Sol/Luna approvals, and a production
release still running all defer release. Every expected test shard, both
coverage gates, cloud tests, perimeter smoke, secret scan and PR-body gate must
be present and successful. Other applicable checks must also complete.
A candidate that changes anything under `.github/` is never reviewed or
merged unattended: pull-request CI runs the candidate's own workflow files, so
those checks cannot judge it. It waits for a human merge.

Reviews are cached by model, immutable head SHA and complete diff digest.
The controller copies the Homebrew CLI into an owner-only snapshot, verifies
its OpenAI Developer ID signature, and binds review receipts to its SHA-256.
Before each uncached review, a fake-credential local transport probes the same
binary, schema and restricted model catalog and rejects advertised tools.
Live inference then uses the private model gateway's native Responses bridge;
only transport/auth settings change. Model capacity comes from the gateway's
bounded projection of its own Codex CLI cache, not the controller's HOME.
Missing verified capacity remains a deferral. Explicit GPT reviewers are
Codex-only and the terminal response must identify the requested model;
a shared fallback cannot impersonate two independent reviewers. Version-5
receipts invalidate earlier transport evidence while retaining prior concerns.
The GitHub merge API receives that exact head SHA as a compare-and-swap for
the head. Immediately before a new merge request, the controller verifies that
main requires strict up-to-date status checks, enforces protection for
administrators, and retains app-bound checks for every installed-policy required
name. Missing, unreadable or weakened protection defers merging. The controller
never selects an administrator bypass. When strengthening protection, retain
all existing required check names, app IDs and other protection fields.
[GitHub documents strict base freshness and administrator enforcement](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

Strict protection rejects an outdated base at the server even if another actor
merges after the controller's final local check; additional SHA reads alone
cannot close that race. This protects the reviewed head against a stale base.
It does not provide a deployment mutex for every other authorized merge actor.
The controller defers its own work while observed main CI/deployment is pending.

The controller waits for successful main CI and deployment, then reconciles both
app and broker root control-plane bundles against that exact main SHA over the
operator's existing SSH access. This runs for externally merged main changes
even with no eligible maintenance PR. Host success requires an installed-SHA
receipt from each host and unchanged GitHub main after both receipts. Complete
receipts persist in the private WAL and avoid repeated SSH for unchanged main
across restarts. A partial host failure or moving main records no new success
and retries reconciliation before another controller merge.
No bot-controlled directory or mutable release marker becomes root authority.

P1 fixes are exported to `validation/fix/<base>/<head>` in the existing private
security-report repository. The controller verifies that repository remains
private, opens private CI validation, and obtains both independent reviews.
Only after rechecking CI and refs does it publish the identical Git objects as
a public maintenance PR. The published commit object's headers must be
`tree`, then any number of `parent` lines, then `author` and `committer`.
Any other header, or a line with no space, is refused before the push.
Public CI and the ordinary release sequence still
apply. P0 findings remain under explicit incident coordination. Source contents
and raw findings never belong in public controller status or notifications.

## Private validation feedback

The controller checkpoints bounded diagnostic feedback before sending it to an
immutable orphan `validation/feedback/<base>/<head>` ref in the same private
archive. Its tree contains only `feedback.json`. Failed delivery retries from
the owner-held checkpoint; existing feedback refs are verified, never rewritten.
CI failure tails and independent model concerns are redacted and remain private.
A released result requires the controller's persisted green release and verified
app/broker receipts; a merged PR alone cannot produce it.

A candidate with more than one commit ahead of its recorded base is terminally
rejected as `private-review-invalid`, using the existing feedback schema. The
bounded diagnostic tells the repair bot to reduce the change to one independently
tested commit and export the new head. No private diff is fetched or published;
later ticks retain the rejection and retry only immutable feedback delivery.

The bot polls with `scripts/private_security_release.py --status --base <sha>
--head <sha>` using its existing private-report deploy key. The reader checks
ref identity, orphan parentlessness, fixed path/mode, schema, source SHAs and
size before returning JSON. It never checks out or executes the feedback.
The nightly prompts checkpoint the result, independently reproduce concerns,
repair and retest a new immutable head, and resume pending work next fire.
Feedback is diagnostic data, never approval authority; missing or invalid data
cannot close an incomplete run. An unpublished candidate whose immutable base is no
longer current main receives private invalid-candidate feedback naming the
current public main. The security loop must independently revalidate the repair
and export a new single-commit candidate with that base. The controller does not
fetch its source, rebase it, create workflows, or publish it. Existing feedback
and publication receipts are preserved unchanged. Review receipts bind the
complete immutable review input, including bounded source context, rather than a bot verdict.
Reviews include complete current runtime, configuration and prompt source,
complete baseline source for deleted files, and an untruncated local diff derived
from both SHA-verified blobs. Both sides retain blob SHA, mode and size metadata.
Runtime blobs are limited to 512,000 bytes each and 2,000,000 verified bytes
combined. Only `tasks/todo.md`, `tasks/lessons.md` and ordinary Markdown under
`docs/` use complete derived prose hunks without unchanged history: their full
blobs are verified first, bounded to 2,000,000 bytes each and 8,000,000 combined.
Instruction files, skills, prompts and executable files retain runtime treatment.
The complete encoded input is limited to 3,000,000 bytes.

Before real inference, the same signed CLI must emit that exact full prompt in
one local fake-credential Responses request with no tools, truncation or
compaction. Inputs unsupported by the actual CLI remain held; the hard bounds
are not a claim that every bounded input fits. Backend capacity rejection never
grants approval or inflates model limits. Transient probe, backend or result
schema failures leave the candidate unmarked so the same head retries next fire.
Invalid, incomplete or oversized source context fails closed with private
diagnostics. Review cache receipts bind this complete-input representation;
older positive receipts cannot authorize it.
Owner-only review cache files are flushed before atomic publication, so an interrupted write cannot expose a partial receipt.

## Installed files and recovery

`scripts/install_unattended_release.py` installs immutable code under
`~/.local/share/radon-operator-releases/releases`, preserves owner policy and
credentials across refreshes, and registers the existing-user LaunchAgent.
`hosts.json` names the operator SSH aliases; no production key is copied into
the bot account or the repository. Only `PUSHOVER_USER` and `PUSHOVER_TOKEN` are
copied to its mode-0600 notification file.

After successful production deployment, the controller refreshes only its
fixed Python module allowlist and the installed operator updater's code from
that immutable main SHA. Downloaded blobs must match their Git object hashes
and parse as Python before atomic activation for the next tick. Existing
release policy, SSH aliases, notification credentials and updater policy remain
separate from these code refreshes.

The state directory holds `findings.jsonl`, `state.json` and `SUMMARY.md`.
An exclusive controller lock serializes runs. WAL writes are fsynced before
atomic snapshots. Merge status is queried after an interruption; claimed heads
are revalidated before a new merge attempt. SHA-bound host sync is repeatable.

Pushover cannot provide exactly-once delivery across a lost response. The
controller claims notification attempts durably before sending, records accepted
responses, and quarantines uncertain attempts instead of blindly duplicating
an accepted page. Release completion is represented by CI and host receipts,
not by notification delivery. The nightly loop retains its ordinary PR-green
page. For public PRs created by the private-validation bridge, the controller
queues a separate `radon PR green` page after exact-head public CI and all
legacy commit-status contexts succeed,
even when subsequent model review rejects the candidate. Its durable PR/head
event survives restarts, uses the same accepted/rejected/uncertain delivery
handling, and drains even while validation waits. The controller sends a
separate release-verification page after production and host reconciliation.

## Toolchain and root-owned runner updates

See [Nightly toolchain updates](nightly-toolchain-updates.md). The existing
operator updater manages each application's update separately. DeepSec repairs
its private dependencies before source review on a normal nightly fire, using
the private model gateway for inference smoke. Its actual Codex SDK review
commands use an ephemeral authenticated loopback bridge, without reading the
bot's native grants. Missing service capability remains an incomplete run.
Model CLI/SDK pins advance through reviewed CI artifacts.

The root-owned runner uses a shared lifecycle lock. Scheduled root refreshes
accept only public main with completed successful production CI, take the
exclusive maintenance lock, stage root-owned source and reinstall all seven
cooperative loop wrappers. A partial installation does not publish the readiness
marker. No job updates a CLI underneath another job using it.

Installing this root updater on an existing Mac requires one legitimate macOS
administrator authorization. There is no passwordless root bridge in the old
runner; adding one through a bot-writable path would recreate the vulnerability
being fixed. The rootless release controller and DeepSec preflight do not depend
on that bootstrap. Subsequent reviewed main updates need no terminal command.
