#!/usr/bin/env python3
"""Render a nightly loop's Claude skill into a self-contained prompt file.

The four non-security loops moved off the claude.ai subscription on
2026-09-06: they run on codex, then grok, then NVIDIA, then Cerebras. None of
those CLIs can load a Claude Code skill or resolve a `/testing-weekend audit`
slash command, so the skill body has to reach them as plain prompt text.

A rendered prompt is a deterministic concatenation of four parts:

    PREAMBLE   how a generic agent CLI should read what follows
    BODY       SKILL.md, frontmatter stripped, byte for byte
    OVERRIDES  the Claude-only machinery the body assumes, and its replacement
    CONTRACT   the completion strings the wrapper greps for

Rendered files are committed under `.claude/portable-prompts/` rather than
built at run time: a nightly at 00:00 must not depend on this script, a
network fetch, or a writable tree. `scripts/tests/test_portable_prompt_sync.py`
fails when a rendered file drifts from a fresh render, which is what keeps
editing SKILL.md honest.

    python3 scripts/render_loop_prompt.py --check    # CI / test path
    python3 scripts/render_loop_prompt.py --write    # after editing a SKILL.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / ".claude" / "skills"
OUT_DIR = REPO / ".claude" / "portable-prompts"

PHASES = ("audit", "remediate", "deliver")

# The security loop is deliberately absent. It stays claude-exclusive: it is
# the one loop whose output is sanitized before it reaches a public issue, and
# a botched sanitization is worse than a missed night. Its wrapper refuses a
# non-claude rung outright, so it has no portable prompt to drift.
LOOPS = {
    "reliability-weekend": "reliability",
    "testing-weekend": "testing",
    "documentation-nightly": "documentation",
    "ci-performance": "ci-performance",
}

PREAMBLE = """\
# {skill} — {phase} phase (portable prompt)

You are running as a NON-INTERACTIVE agent CLI. There is no human to ask: a
question asked here is a night lost. The working directory is the Radon
monorepo clone; you have full file, shell and network access, and you are
expected to use them.

Execute the **{phase}** phase of the manual below, and only that phase.

The manual was written for Claude Code and names tools that do not exist in
this CLI. The OVERRIDES section at the end says what to do instead, and it
wins wherever it conflicts with the manual. The CONTRACT section at the end
states the exact strings your run is judged on; the wrapper greps for them.

---

"""

OVERRIDES = """

---

# OVERRIDES — read these as amendments to everything above

These win over the manual on every conflict.

1. **No subagents, no fan-out, no worktree swarm.** The manual's `Task` tool,
   `Agent` tool, `Workflow` tool, subagent dispatch and parallel worktree
   patterns do not exist here. Do the work serially, in this one session.

2. **No Claude-only tools.** `SlashCommand`, `Skill`, MCP tools (`mcp__*`),
   plugin skills and `chrome-cdp` are unavailable. Where the manual calls for
   `chrome-cdp`, use Playwright (`web/playwright.config.ts`). Where it invokes
   another slash command, do that work inline.

3. **Long commands must not block the session.** For anything over about two
   minutes (full test suites, builds, CI waits), launch it detached, poll a
   file, and read the result:

       nohup <cmd> > /tmp/<name>.log 2>&1 &
       echo $! > /tmp/<name>.pid
       # poll: test -s /tmp/<name>.log && tail -5 /tmp/<name>.log

   Write a `DONE <rc>` sentinel as the command's last act and poll for it,
   rather than waiting on the foreground.

4. **Remediation scope on a reduced-capability rung.** When the environment
   variable `RADON_WEEKEND_REDUCED` is `1`, remediate ONLY P0 and P1 findings,
   and say so in the phase's own report. At any other time remediate the full
   verified set exactly as the manual describes.

5. **Never widen a gate to make something pass.** Every rail, refusal and
   "stop, name the gate" instruction in the manual applies here unchanged. If
   you cannot complete the phase honestly, print the contract's INCOMPLETE
   form and stop. A false green is the one unrecoverable outcome.

{contract}
"""

CONTRACT_COMMON = """
---

# CONTRACT — what the wrapper reads

The wrapper does not read your prose. It reads these signals, and nothing
else decides whether tonight counted:
"""

CONTRACT_COMMIT = """
- **audit / remediate:** the phase counts as complete only if you have made at
  least one commit on the branch `{prefix}<YYYY-MM-DD>` (today's date, the
  branch the manual tells you to use). An exit without a commit is scored
  INCOMPLETE, whatever you print.
"""

CONTRACT_DELIVER = """
- **deliver:** your FINAL line of stdout must be exactly one of

      NIGHTLY DELIVER READY: loop={slug} prs=<n> <space-separated PR urls>
      NIGHTLY DELIVER INCOMPLETE: loop={slug} <one-line reason>

  and you must also record the branch and PR through
  `python3 scripts/nightly_deliver.py record ...` exactly as the manual
  describes. READY means CI is green on every PR you are naming. Never print
  READY for a PR whose checks are pending, failing, or unknown.
"""

BRANCH_PREFIX = {
    "reliability-weekend": "reliability/",
    "testing-weekend": "testing/",
    "documentation-nightly": "documentation/",
    "ci-performance": "ci-performance/",
}


def _body(skill: str) -> str:
    """SKILL.md with its YAML frontmatter removed, byte for byte otherwise."""
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return text
    end = text.index("\n---\n", 3)
    return text[end + len("\n---\n") :].lstrip("\n")


def render(skill: str, phase: str) -> str:
    slug = LOOPS[skill]
    if phase == "deliver":
        contract = CONTRACT_COMMON + CONTRACT_DELIVER.format(slug=slug)
    else:
        contract = CONTRACT_COMMON + CONTRACT_COMMIT.format(
            prefix=BRANCH_PREFIX[skill]
        )
    return (
        PREAMBLE.format(skill=skill, phase=phase)
        + _body(skill)
        + OVERRIDES.format(contract=contract)
    )


def target(skill: str, phase: str) -> Path:
    return OUT_DIR / f"{skill}.{phase}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="(re)write the files")
    g.add_argument("--check", action="store_true", help="fail on any drift")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    drift = []
    for skill in sorted(LOOPS):
        for phase in PHASES:
            path = target(skill, phase)
            fresh = render(skill, phase)
            if args.write:
                path.write_text(fresh, encoding="utf-8")
                print(f"wrote {path.relative_to(REPO)} ({len(fresh)} bytes)")
            else:
                have = path.read_text(encoding="utf-8") if path.exists() else ""
                if have != fresh:
                    drift.append(str(path.relative_to(REPO)))
    if drift:
        print("stale portable prompts (run --write):", file=sys.stderr)
        for d in drift:
            print(f"  {d}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
