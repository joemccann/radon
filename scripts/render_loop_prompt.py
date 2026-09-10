#!/usr/bin/env python3
"""Render a nightly loop's Claude skill for the CLIs that are not Claude Code.

The four non-security loops moved off the claude.ai subscription on
2026-09-06: they run on codex, then grok, then NVIDIA, then Cerebras. Two
artifacts come out of one source, `.claude/skills/<skill>/SKILL.md`.

**Portable prompts** (`.claude/portable-prompts/<skill>.<phase>.md`) are what
the wrapper actually pipes into a rung. A phase must run the same way every
night whether or not the CLI decided a skill was relevant, so the driver stays
an explicit prompt rather than a discovery-time suggestion. Each is a
deterministic concatenation of four parts:

    PREAMBLE   how a generic agent CLI should read what follows
    BODY       SKILL.md, frontmatter stripped, byte for byte
    OVERRIDES  the Claude-only machinery the body assumes, and its replacement
    CONTRACT   the completion strings the wrapper greps for

**Native codex skills** (`.codex/skills/<skill>/`) are the same manual in the
layout codex's own skill discovery expects: a `SKILL.md` with `name` and
`description` frontmatter, plus `agents/openai.yaml` carrying the interface
block. Probed on this runner (codex-cli 0.153.4, 2026-09-08): codex reads
`./.codex/skills/` relative to its working directory and does NOT read
`.claude/skills/`. Without this, anything inside a run that reaches for the
loop's own manual by name — a nested invocation, a `$skill` reference — finds
nothing.

grok needs no rendering at all: it scans `./.claude/skills/` natively at repo
scope (`~/.grok/docs/user-guide/08-skills.md`, and probed on this runner), so
the Claude skills already load there under their own names. NVIDIA and
Cerebras run through the grok CLI and inherit that.

Both artifacts are committed rather than built at run time: a nightly at 00:00
must not depend on this script, a network fetch, or a writable tree.
`scripts/tests/test_portable_prompt_sync.py` fails when either drifts from a
fresh render, which is what keeps editing SKILL.md honest.

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
CODEX_SKILL_DIR = REPO / ".codex" / "skills"

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


# The phase-agnostic head of a native codex skill. The portable prompt names
# its phase because the wrapper pipes one file per phase; a skill is one
# document covering all three, and the phase arrives in the invocation.
CODEX_HEAD = """\
---
name: {skill}
description: {description}
---

# {title}

You are running as a NON-INTERACTIVE agent CLI. There is no human to ask: a
question asked here is a night lost. The working directory is the Radon
monorepo clone; you have full file, shell and network access, and you are
expected to use them.

This manual covers three phases — **audit**, **remediate** and **deliver**.
Run only the phase you were asked for.

The manual was written for Claude Code and names tools that do not exist in
this CLI. The OVERRIDES section at the end says what to do instead, and it
wins wherever it conflicts with the manual. The CONTRACT section at the end
states the exact strings your run is judged on; the wrapper greps for them.

---

"""

# codex reads the interface block from agents/openai.yaml, not from the
# frontmatter. Shape copied from an installed skill rather than invented.
CODEX_AGENT_YAML = """\
interface:
  display_name: "{display}"
  short_description: "{short}"
  default_prompt: "Use ${skill} to run the {slug} nightly loop's audit, remediate or deliver phase."
"""

CODEX_DISPLAY = {
    "reliability-weekend": ("Radon Reliability Weekend",
                            "Nightly reliability delta-audit and remediation"),
    "testing-weekend": ("Radon Testing Weekend",
                        "Nightly test-suite audit and remediation"),
    "documentation-nightly": ("Radon Documentation Nightly",
                              "Nightly documentation audit and remediation"),
    "ci-performance": ("Radon CI Performance",
                       "Nightly CI and deploy critical-path optimizer"),
}


def _frontmatter(skill: str) -> dict:
    """`name:` and `description:` from SKILL.md, as written.

    Deliberately not a YAML parse: these two keys are single-line scalars in
    every loop skill, and a dependency here would be one more thing between an
    edited SKILL.md and a rendered artifact.
    """
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    out = {}
    if not text.startswith("---\n"):
        return out
    end = text.index("\n---\n", 3)
    for line in text[4:end].splitlines():
        for key in ("name", "description"):
            if line.startswith(key + ": "):
                out[key] = line[len(key) + 2 :].strip()
    return out


def _title(skill: str) -> str:
    """The manual's own H1, so the native skill opens the way the manual does."""
    for line in _body(skill).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return skill


def render_codex_skill(skill: str) -> str:
    slug = LOOPS[skill]
    fm = _frontmatter(skill)
    contract = (
        CONTRACT_COMMON
        + CONTRACT_COMMIT.format(prefix=BRANCH_PREFIX[skill])
        + CONTRACT_DELIVER.format(slug=slug)
    )
    return (
        CODEX_HEAD.format(
            skill=skill,
            description=fm.get("description", skill),
            title=_title(skill),
        )
        + _body(skill)
        + OVERRIDES.format(contract=contract)
    )


def render_codex_agent(skill: str) -> str:
    display, short = CODEX_DISPLAY[skill]
    return CODEX_AGENT_YAML.format(display=display, short=short, skill=skill,
                                   slug=LOOPS[skill])


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


def codex_targets(skill: str) -> list[tuple[Path, str]]:
    d = CODEX_SKILL_DIR / skill
    return [
        (d / "SKILL.md", render_codex_skill(skill)),
        (d / "agents" / "openai.yaml", render_codex_agent(skill)),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="(re)write the files")
    g.add_argument("--check", action="store_true", help="fail on any drift")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    drift = []
    pending: list[tuple[Path, str]] = []
    for skill in sorted(LOOPS):
        for phase in PHASES:
            pending.append((target(skill, phase), render(skill, phase)))
        pending.extend(codex_targets(skill))
    for path, fresh in pending:
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fresh, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO)} ({len(fresh)} bytes)")
        else:
            have = path.read_text(encoding="utf-8") if path.exists() else ""
            if have != fresh:
                drift.append(str(path.relative_to(REPO)))
    if drift:
        print("stale rendered skills (run --write):", file=sys.stderr)
        for d in drift:
            print(f"  {d}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
