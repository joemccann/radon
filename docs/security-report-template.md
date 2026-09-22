# Private security report template

Every phase of the `security-nightly` and `security-deepsec` loops writes one
report for the operator, published by the wrapper to the private repository
`joemccann/radon-security-reports` and rendered by GitHub. This file is the
template and the formatting contract. It contains no findings; the reports
do. The skills point here; `scripts/tests/test_security_report_template.py`
pins the headings and rules.

## Formatting rules

1. GitHub-flavoured Markdown, rendered on GitHub. One `#` title, `##`
   sections in the order below, `###` only inside Findings and Verification.
2. Lead with the summary table. A reader must know the outcome, the counts
   and what they must do from the first screen.
3. Tabular facts go in tables, never in `key: value` line dumps. Every table
   has a header row and a separator row. One fact per cell, one sentence at
   most, no line breaks inside a cell.
4. Prose goes in short paragraphs or bullets. One idea per bullet. No
   run-record lines, no `stage:` prefixes, no transcript copy.
5. Raw tool output (gate counts, scanner tail, rc files) goes in a fenced
   code block, trimmed to the lines that carry the decision, never the whole
   log.
6. Identifiers are code spans: finding ids, SHAs (short, 8 chars), branch
   names, `path:line`, commands, environment variables. Severity is plain
   text (`P1`), never an emoji or colour word.
7. Sentence-case headings. No emoji anywhere. Bold only the first words of a
   bullet, never a whole sentence. No em dashes.
8. Blank line before and after every heading, table, list and code block.
9. Dates are ISO (`2026-09-19`), times UTC with a `Z`. Durations in minutes.
10. Secret literals never appear (rail 6): name the variable or secret class
    and its location. The wrapper's redaction is a backstop, not permission.
11. Nothing is omitted for brevity. Every candidate the engines produced this
    phase appears in Findings or Rejected with its reason. Complete beats
    short.

## Template

````markdown
# <loop> <phase> <YYYY-MM-DD>

## Summary

| Field | Value |
|---|---|
| Run id | `<run-id>` |
| Cycle | `<wrapper STAMP>` |
| Status | COMPLETE / INCOMPLETE / OPERATOR_REQUIRED |
| Head | `<8-char sha>` |
| Last audited | `<8-char sha>` |
| Range | `<from>..<to>` (<n> commits) |
| Started / ended | `<UTC>Z` / `<UTC>Z` (<minutes> min) |
| Findings | <verified> verified (<P0> P0, <P1> P1, <P2> P2, <P3> P3), <rejected> rejected |
| Fixed | <done>/<verified> (<n> public on `<branch>`, <n> private) |
| PR | <url or none> (CI: <state>) |
| Operator actions | <count> (see below) |

## Operator actions

1. **<Verb first>**: exact command or decision, with the path or URL.

## Stages

| Stage | Outcome | Detail |
|---|---|---|
| Preflight | complete | tool pin, auth method, route, markers |
| <engine> | complete / OPERATOR_REQUIRED / INCOMPLETE | rc values, counts, timing |
| Verification | complete | candidates, groups, agents |
| Archive | complete | <n> files, checksum-verified, path |
| Audited SHA | advanced to `<sha>` / not advanced (<why>) | |

## Findings

| Id | Severity | CWE | Location | Summary | Disposition |
|---|---|---|---|---|---|
| `DS-...` | P1 | CWE-59 | `path:line` | One sentence. | verified, private until released |

### `<finding id>`: <short title>

- **Attacker and access**: who, from where, with what.
- **Path**: entry point, trust boundary, sink, as `path:line` references.
- **Impact**: concrete effect.
- **Proof**: the source evidence or minimal reproduction.
- **Refutation considered**: the best false-positive argument and why it fails.
- **Fix shape**: chokepoint and regression test.

## Rejected

| Candidate | Reason |
|---|---|
| `C00` | One sentence, with the `path:line` that refutes it. |

## Fixes

| Finding | Commit | Branch | Regression test | Gates |
|---|---|---|---|---|
| `DS-...` | `<sha>` | `<branch>` | `path` | <suite>: <passed>/<failed> |

## Gate results

```text
<trimmed tool output>
```

## Resume state

- **Next fire**: what it picks up (branch, PR, range, unverified queue).
````

Phases without a section leave it out rather than writing "none": an audit
has no Fixes, a deliver has no Rejected. Summary, Operator actions and
Resume state are always present.
