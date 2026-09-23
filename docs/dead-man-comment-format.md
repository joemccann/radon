# Dead-man comment format

The contract for every comment an agent posts on a rolling dead-man issue
(`testing-nightly`, `reliability-nightly`, `ci-performance-nightly`,
`documentation-nightly`). The wrapper's one-line phase comment is not covered
here; this governs the write-up the agent authors.

## The problem this fixes

Comments grew into unscannable walls: 60KB of retained inventory, bare file
paths with line ranges inline in prose, acceptance criteria and evidence
repeated verbatim every night, and no way to tell in five seconds whether the
runner is healthy, what broke, or whether anyone needs to act.

An operator reads these on a phone, at 7am, once. The comment has one job:
**make the state of the loop obvious before any scrolling.**

## The five-block shape

Every authored comment is exactly these blocks, in this order. Nothing else
goes at the top level — everything longer collapses into `<details>`.

````markdown
> [!NOTE]
> **Healthy** · testing · 2026-09-23 · audit → remediate → deliver

### What broke

**In plain language.** The dividend-yield test suite trusts the clock, so it
fails on slow machines even when the code is correct.

**Technically.** `scripts/tests/test_divyield.py` asserts wall-clock
`elapsed < 1.0` against a 0.15s sweep budget. Under load it read 2.53s.

### The fix

**In plain language.** The test now checks what the sweep actually did — how
many tickers it dropped — instead of how long it took.

**Technically.** Replaced the elapsed-time assertion with
`errors >= len(tickers) - FETCH_WORKERS` and a hang call-count of 1.
Verified red before, green after, at the unchanged implementation.

### Needs you

- [ ] Merge #663 — CI is green, no other blocker.

<details>
<summary>Open findings (3)</summary>

| ID | Sev | What it means | Where |
|---|---|---|---|
| T-502 | P2 | Scoring tests would pass if every metric were hardcoded to a perfect 1.0 | [`slm/eval.py`](…) |
| T-503 | P2 | Two benchmark arms can silently run different prompts | [`predict_ladder.py`](…) |
| T-504 | P2 | Promotion gates can all be bypassed without a test failing | [`bakeoff.py`](…) |

</details>

<details>
<summary>Run record</summary>

`audited-through: 0833758b` · 246 commits / 785 paths · 104 deselected /
1 failed · full ledger in `TEST_AUDIT.md`

</details>
````

### Block 1 — the verdict banner

A GitHub alert, one line, always first. Choose by outcome:

| Outcome | Alert | Lead word |
|---|---|---|
| Ran clean, nothing broke | `> [!NOTE]` | **Healthy** |
| Fixed something this run | `> [!TIP]` | **Fixed** |
| Something needs the operator | `> [!WARNING]` | **Needs you** |
| Phase could not complete | `> [!CAUTION]` | **Incomplete** |

Then `· loop · date · phase`. No SHAs, no counts, no file paths in the banner.

### Block 2 — "What broke"

Two paragraphs, both required, in this order and with these exact bold leads:

- **In plain language.** One or two sentences. No identifiers, no file names,
  no severities, no acronyms. A reader who has never opened this repo must
  understand what stopped working and why it matters.
- **Technically.** One or two sentences naming *one* symbol or file and the
  concrete observation (the number that was wrong, the mutation that passed).

If nothing broke: `Nothing broke. The audit verified N findings and found no
new actionable defect.` — then skip straight to Block 5.

### Block 3 — "The fix"

Same two-paragraph pairing, same bold leads. Describe only what **this run**
changed. If nothing changed this run, say `Nothing this run.` and say in one
clause why (out of scope, blocked, zero findings).

The technical paragraph must state the red/green evidence, because that is the
only thing that distinguishes a fix from a claim.

### Block 4 — "Needs you"

A GitHub task list, one box per item, only for work that cannot happen inside
CI. Each line is an imperative with the object first and the reason after an
em dash. Link PRs and issues by `#number` so GitHub renders their state inline.

Omit the whole block when there is nothing. Never put "monitor", "consider",
or "keep an eye on" in it — if the loop can do it, the loop does it.

### Block 5 — collapsed detail

Everything else lives inside `<details>`, never at the top level:

- **Open findings** — one table, one row per finding, four columns exactly:
  `ID | Sev | What it means | Where`. "What it means" is plain language, one
  line, under 90 characters. "Where" is a single linked file, no line range.
- **Run record** — cursor SHA (short), commit/path counts, test tallies, and a
  pointer to the ledger file that holds evidence and acceptance criteria.

## Hard rules

1. **Under 40 lines before any `<details>`.** If it does not fit, it belongs
   in a collapsed block or the ledger.
2. **Evidence and acceptance criteria never appear in a comment.** They live
   in the ledger (`TEST_AUDIT.md`, `RELIABILITY_AUDIT.md`, …). The comment
   links to the ledger; it does not reproduce it.
3. **Never re-post carried-forward inventory.** A finding that was already
   reported gets a row in the collapsed table — its ID, severity, and one
   plain-language line. Nothing else. Retention lives in the ledger.
4. **One file path per finding, maximum, and always linked.** Write
   `[`positionUtils.ts`](../../blob/main/web/lib/positionUtils.ts#L845)`, not
   `web/lib/positionUtils.ts:845-861`. Never list three paths in a sentence.
5. **Every technical statement is paired with a plain-language one.** Neither
   substitutes for the other, and the plain one comes first.
6. **Backticks are for identifiers only** — symbols, files, env vars, commands.
   Not for prose emphasis, not for numbers, not for status words.
7. **No status dumps.** `RADON_WEEKEND_REDUCED=1`, `terminal DONE 1`,
   `1 failed / 104 deselected in 0.41s` are run-record facts. They go in the
   collapsed run record, never in a narrative sentence.
8. **No pointers to a machine.** A path under `~/radon-weekend/` is not a
   result. Post the result.

## Plain-language test

Before posting, read only the banner and the two plain-language paragraphs.
If a reader who has never seen the repository cannot answer *"is it working,
what went wrong, and do I have to do anything?"* — rewrite them. That is the
whole point of the format.

Avoid in plain-language paragraphs: mutation, fixture, assertion, deselected,
cursor, delta, checkpoint, monotonic, regression (as a noun), P0/P1/P2, and
every `T-###` identifier.

## Before / after

<details>
<summary>What this format replaces</summary>

Before — one paragraph, 7 identifiers, no verdict, no reader:

> Remediate 2026-09-23 is INCOMPLETE. RADON_WEEKEND_REDUCED=1: only P0/P1 are
> in remediation scope; P2 inventory is retained without closure. The inherited
> runner prerequisite blocker remains: the runner venv executed
> test_weekend_lock_hygiene.py::TestLockHygieneL1ToL5::test_case5_directory_live_pid_must_not_steal[testing],
> yielding 1 failed / 104 deselected in 0.41s, terminal DONE 1.

After:

> [!CAUTION]
> **Incomplete** · testing · 2026-09-23 · remediate
>
> ### What broke
>
> **In plain language.** The runner cannot inspect its own processes in the
> sandbox, so the check that stops two nightly runs colliding could not be
> verified. No fix shipped tonight.
>
> **Technically.** `test_case5_directory_live_pid_must_not_steal` fails with
> `PermissionError` executing `/bin/ps`; the sandbox forbids the escalation.

</details>
