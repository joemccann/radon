---
name: long-horizon-jobs
description: Durability contract for long-running agent jobs (harvesters, scrapers, ingest, audits). Use when building or running any job that processes many items over a long horizon, when a job must survive kills/context limits, or when resuming a previously interrupted harvester. Covers state.json + findings.jsonl + SUMMARY.md checkpointing, the memory-observer, and the 10-tool-call checkpoint hook.
---

# Long-Horizon Jobs

Progress must never live only in the conversation. Context limits kill sessions;
SIGKILL kills processes. A job is durable only if a kill at ANY instant loses
zero items and duplicates zero work on resume.

## The contract — three files per job

Every long-horizon job owns a job directory (convention: `data/jobs/<job-name>/`)
containing exactly:

| File | Role | Write discipline |
|---|---|---|
| `findings.jsonl` | Append-only results. THE source of truth (the WAL). | One JSON line per completed item, `fsync` after every append, written BEFORE the item is marked complete. |
| `state.json` | Cursor + completed-item hashes + error queue + stats. | Atomic replace (tmp + fsync + `os.replace`) on every checkpoint. |
| `SUMMARY.md` | Rolling digest: counts, cursor, error tail, last 20 findings. | Regenerated atomically at each checkpoint. **The ONLY file an agent re-reads on resume.** |

Recovery invariant: on load, the completed set is rebuilt as the UNION of
`state.json.completed` and the `_hash` fields found in `findings.jsonl`. A kill
between the WAL append and the state write therefore cannot duplicate (the hash
is recovered from the WAL) and cannot lose (the finding is already on disk).
A torn final JSONL line from a mid-append kill is skipped, and its item re-runs.

## Implementation

Python: `scripts/lib/checkpoint.py` (`CheckpointedJob`). Contract tests:
`scripts/tests/test_checkpoint.py` (includes a 3x SIGKILL harness).

```python
from checkpoint import CheckpointedJob

job = CheckpointedJob("data/jobs/sydecar-docs", "sydecar-docs")
for item in list_items(start=job.cursor):          # cursor bounds re-listing
    if job.is_done(item.id):                       # hash-set skip, O(1)
        continue
    try:
        result = process(item)                     # the expensive part
        job.record_finding(item.id, result)        # WAL append -> mark done -> checkpoint
    except Exception as e:
        job.record_error(item.id, str(e))          # error queue, attempts counted
    job.set_cursor({"last": item.id})
job.finish()
```

Rules:

1. **Item keys must be stable across runs** (URL, document id, post id) — never
   list indices or timestamps. The key is hashed (`item_hash`) for the completed set.
2. **Do the expensive work between `is_done` and `record_finding`, nothing after.**
   Side effects (file downloads) go to a deterministic path derived from the item
   key, written atomically (tmp + rename), so a re-run overwrites rather than duplicates.
3. **Errors don't block progress.** `record_error` queues the item and the loop
   continues; a later successful `record_finding` clears it. Retry the error queue
   at the start of the next run, bounded by `attempts`.
4. **Never buffer batches in memory.** One item = one durable write. If an API
   forces batching, keep the batch small and checkpoint per batch.
5. **JS jobs** follow the same file contract: `fs.appendFileSync` + `fsync` for the
   WAL, write-tmp-then-`fs.renameSync` for state/summary.

## Agent resume protocol

When an agent (re)starts a long-horizon job:

1. Read `SUMMARY.md` ONLY. Do not re-read `findings.jsonl`, raw transcripts,
   task outputs, or prior conversation. The summary carries counts, cursor, and
   the error tail — everything needed to decide what to do next.
2. Launch/continue the worker process. The library skips completed items itself.
3. On context pressure or phase end, ensure the last checkpoint happened, then
   summarize position in one sentence. The next session starts at step 1.

The PostToolUse hook (`~/.claude/hooks/lh-observer-digest.sh`, registered in
`~/.claude/settings.json`) injects a checkpoint reminder every 10 tool calls —
when it fires mid-job, flush state before continuing.

## Memory observer

The `memory-observer` agent (`~/.claude/agents/memory-observer.md`) distills
durable observations at phase boundaries. It reads ONLY the pre-truncated event
digest at `~/.claude/observer/events/<session_id>.jsonl` (each event capped at
400 chars by the hook; observer reads `tail -c 24000`, ~6k tokens hard budget),
emits at most 10 observations per batch, and appends them to
`~/.claude/observer/observations.jsonl` BEFORE replying. Never feed it raw
transcripts; never raise its budget — bounded-and-lossy beats complete-and-dead.

## Anti-patterns (each one has already cost us)

- Progress tracked as a number in the conversation ("done 340 of 900") — the KB
  Phase-1 backfill's escalating `--limit` values were the only record of progress.
- Re-listing and re-diffing the whole corpus every run instead of a cursor
  (KB ingest re-reads ~4,900 docs hourly; crash = full re-fetch).
- Buffering expensive results in RAM until a batch write (KB ingest's in-flight
  200-doc batch discards up to 200 paid LLM calls on crash).
- Feeding an observer/summarizer untruncated tool output (claude-mem: unbounded
  `JSON.stringify` + never-compacted history = "Prompt is too long" on every
  batch, zero records captured).
- Rewriting a results file wholesale instead of appending (torn-write risk;
  newsfeed `posts.json` uses plain `writeFile`).
- Kill-testing skipped. A harvester is not done until it has been SIGKILLed
  mid-run at least 3 times and resumed with zero duplicates and zero losses
  (see `test_sigkill_mid_run_resumes_exactly_once`).
