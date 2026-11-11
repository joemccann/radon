#!/usr/bin/env python3
"""
Context Constructor — Startup context pipeline for Radon.

Implements the Constructor stage from the context-engineering architecture:
1. Reads persistent facts from context/memory/fact/
2. Reads episodic summaries from context/memory/episodic/
3. Reads human annotations from context/human/
4. Assembles a token-budgeted context payload
5. Generates a manifest recording what was included/excluded

Also implements the Evaluator stage (--save-facts):
- Extracts facts from session and persists to context/memory/fact/
- Writes episodic session summary to context/memory/episodic/

Usage:
    # Constructor: load context at session start
    python3 scripts/context_constructor.py
    
    # Constructor: JSON output for programmatic use  
    python3 scripts/context_constructor.py --json
    
    # Evaluator: save a fact
    python3 scripts/context_constructor.py --save-fact "key" "value" --confidence 0.9 --source "session"
    
    # Evaluator: save session summary
    python3 scripts/context_constructor.py --save-episode "Session summary text" --session-id "abc"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Project root
ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = ROOT / "context"
MEMORY_DIR = CONTEXT_DIR / "memory"
HISTORY_DIR = CONTEXT_DIR / "history"
HUMAN_DIR = CONTEXT_DIR / "human"

# Token budget: rough estimate 1 token ≈ 4 chars
def estimate_tokens(text: str) -> int:
    return len(text) // 4


def load_json_file(path: Path) -> dict | None:
    """Load a JSON file, return None on failure."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_context_entries(directory: Path) -> list[dict]:
    """Load all JSON entries from a context directory."""
    entries = []
    if not directory.exists():
        return entries
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix == ".json":
            data = load_json_file(f)
            if data:
                data["_file"] = f.name
                entries.append(data)
    return entries


def log_transaction(operation: str, path_str: str, agent_id: str = "radon"):
    """Append to the transaction log."""
    log_path = HISTORY_DIR / "_transactions.jsonl"
    tx = {
        "txId": str(uuid.uuid4()),
        "operation": operation,
        "path": path_str,
        "agentId": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(tx) + "\n")


def construct_context(token_budget: int = 8000) -> dict:
    """
    Build context payload from persistent memory.
    
    Returns:
        {
            "context": str,          # Assembled context text
            "manifest": dict,        # What was included/excluded
            "facts_count": int,
            "episodes_count": int,
            "human_count": int,
            "tokens_used": int,
        }
    """
    manifest = {
        "manifestId": str(uuid.uuid4()),
        "taskId": "session-startup",
        "agentId": "radon",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tokenBudget": token_budget,
        "tokenUsed": 0,
        "included": [],
        "excluded": [],
    }
    
    sections = []
    tokens_remaining = token_budget
    facts_loaded = 0
    episodes_loaded = 0
    human_loaded = 0
    
    # 1. Load facts (highest priority — atomic, verified knowledge)
    facts = list_context_entries(MEMORY_DIR / "fact")
    if facts:
        fact_lines = []
        for fact in sorted(facts, key=lambda f: f.get("updatedAt", ""), reverse=True):
            key = fact.get("key", fact.get("_file", "unknown"))
            value = fact.get("value", "")
            confidence = fact.get("confidence", 1.0)
            line = f"- **{key}**: {value} (confidence: {confidence})"
            line_tokens = estimate_tokens(line)
            
            if line_tokens <= tokens_remaining:
                fact_lines.append(line)
                tokens_remaining -= line_tokens
                facts_loaded += 1
                manifest["included"].append({
                    "path": f"memory/fact/{fact['_file']}",
                    "tokens": line_tokens,
                    "reason": "persistent fact",
                })
            else:
                manifest["excluded"].append({
                    "path": f"memory/fact/{fact['_file']}",
                    "tokens": line_tokens,
                    "reason": "exceeded token budget",
                })
        
        if fact_lines:
            sections.append("### Persistent Facts\n" + "\n".join(fact_lines))
    
    # 2. Load episodic summaries (medium priority — session history)
    episodes = list_context_entries(MEMORY_DIR / "episodic")
    if episodes:
        episode_lines = []
        # Most recent first, limit to last 10 sessions
        for ep in sorted(episodes, key=lambda e: e.get("createdAt", ""), reverse=True)[:10]:
            session_id = ep.get("sessionId", ep.get("_file", "unknown"))
            summary = ep.get("summary", ep.get("content", ""))
            date = ep.get("createdAt", "")[:10]
            line = f"- **{date}** ({session_id}): {summary}"
            line_tokens = estimate_tokens(line)
            
            if line_tokens <= tokens_remaining:
                episode_lines.append(line)
                tokens_remaining -= line_tokens
                episodes_loaded += 1
                manifest["included"].append({
                    "path": f"memory/episodic/{ep['_file']}",
                    "tokens": line_tokens,
                    "reason": "recent session summary",
                })
            else:
                manifest["excluded"].append({
                    "path": f"memory/episodic/{ep['_file']}",
                    "tokens": line_tokens,
                    "reason": "exceeded token budget",
                })
        
        if episode_lines:
            sections.append("### Recent Session History\n" + "\n".join(episode_lines))
    
    # 3. Load human annotations (highest authority — overrides model output)
    annotations = list_context_entries(HUMAN_DIR)
    if annotations:
        human_lines = []
        for ann in sorted(annotations, key=lambda a: a.get("createdAt", ""), reverse=True):
            content = ann.get("content", ann.get("annotation", ""))
            priority = ann.get("priority", "normal")
            line = f"- {'⚠️ ' if priority == 'high' else ''}{content}"
            line_tokens = estimate_tokens(line)
            
            if line_tokens <= tokens_remaining:
                human_lines.append(line)
                tokens_remaining -= line_tokens
                human_loaded += 1
                manifest["included"].append({
                    "path": f"human/{ann['_file']}",
                    "tokens": line_tokens,
                    "reason": "human annotation (overrides model)",
                })
            else:
                manifest["excluded"].append({
                    "path": f"human/{ann['_file']}",
                    "tokens": line_tokens,
                    "reason": "exceeded token budget",
                })
        
        if human_lines:
            sections.append("### Human Annotations (Authoritative)\n" + "\n".join(human_lines))
    
    # 4. Load experiential memories (observation-action trajectories)
    experiential = list_context_entries(MEMORY_DIR / "experiential")
    if experiential:
        exp_lines = []
        for exp in sorted(experiential, key=lambda e: e.get("createdAt", ""), reverse=True)[:5]:
            observation = exp.get("observation", "")
            action = exp.get("action", "")
            outcome = exp.get("outcome", "")
            line = f"- Observed: {observation} → Action: {action} → Outcome: {outcome}"
            line_tokens = estimate_tokens(line)
            
            if line_tokens <= tokens_remaining:
                exp_lines.append(line)
                tokens_remaining -= line_tokens
                manifest["included"].append({
                    "path": f"memory/experiential/{exp['_file']}",
                    "tokens": line_tokens,
                    "reason": "experiential learning",
                })
        
        if exp_lines:
            sections.append("### Experiential Learnings\n" + "\n".join(exp_lines))
    
    # Assemble
    tokens_used = token_budget - tokens_remaining
    manifest["tokenUsed"] = tokens_used
    
    context = "\n\n".join(sections) if sections else ""
    
    # Log the construction
    if context:
        log_transaction("construct", "session-startup", "radon")
    
    return {
        "context": context,
        "manifest": manifest,
        "facts_count": facts_loaded,
        "episodes_count": episodes_loaded,
        "human_count": human_loaded,
        "tokens_used": tokens_used,
    }


# ─── Evaluator: Save Fact ───────────────────────────────────

def save_fact(key: str, value: str, confidence: float = 0.9, source: str = "session"):
    """Persist a fact to context/memory/fact/."""
    fact_dir = MEMORY_DIR / "fact"
    fact_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize key for filename
    safe_key = key.replace("/", "-").replace(" ", "-").replace(".", "-").lower()
    filename = f"{safe_key}.json"
    filepath = fact_dir / filename
    
    # Check for existing fact (update revision)
    existing = load_json_file(filepath)
    revision = (existing.get("revisionId", 0) + 1) if existing else 1
    
    now = datetime.now(timezone.utc).isoformat()
    
    entry = {
        "id": f"fact-{safe_key}",
        "key": key,
        "value": value,
        "confidence": confidence,
        "source": source,
        "extractedBy": "context-evaluator",
        "createdAt": existing.get("createdAt", now) if existing else now,
        "updatedAt": now,
        "revisionId": revision,
        "expiresAt": None,
    }
    
    filepath.write_text(json.dumps(entry, indent=2))
    log_transaction("write", f"memory/fact/{filename}", "radon")
    
    return entry


# ─── Evaluator: Save Episode ────────────────────────────────

def save_episode(summary: str, session_id: str | None = None):
    """Persist a session summary to context/memory/episodic/."""
    ep_dir = MEMORY_DIR / "episodic"
    ep_dir.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(timezone.utc)
    sid = session_id or f"session-{now.strftime('%Y%m%d-%H%M%S')}"
    filename = f"{sid}.json"
    filepath = ep_dir / filename
    
    entry = {
        "id": f"ep-{sid}",
        "sessionId": sid,
        "summary": summary,
        "createdAt": now.isoformat(),
        "tokenCount": estimate_tokens(summary),
    }
    
    filepath.write_text(json.dumps(entry, indent=2))
    log_transaction("write", f"memory/episodic/{filename}", "radon")
    
    return entry


def main():
    parser = argparse.ArgumentParser(description="Context Constructor — build session context from persistent memory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--budget", type=int, default=8000, help="Token budget (default: 8000)")
    parser.add_argument("--manifest-only", action="store_true", help="Show manifest only (no context)")
    
    # Evaluator subcommands
    parser.add_argument("--save-fact", nargs=2, metavar=("KEY", "VALUE"), help="Save a fact")
    parser.add_argument("--confidence", type=float, default=0.9, help="Fact confidence (0-1)")
    parser.add_argument("--source", default="session", help="Fact source identifier")
    
    parser.add_argument("--save-episode", metavar="SUMMARY", help="Save session episode summary")
    parser.add_argument("--session-id", help="Session ID for episode")
    
    args = parser.parse_args()
    
    # ─── Evaluator: save fact ───
    if args.save_fact:
        key, value = args.save_fact
        entry = save_fact(key, value, args.confidence, args.source)
        if args.json:
            print(json.dumps(entry, indent=2))
        else:
            print(f"✓ Saved fact: {key} = {value} (confidence: {args.confidence}, rev: {entry['revisionId']})")
        return
    
    # ─── Evaluator: save episode ───
    if args.save_episode:
        entry = save_episode(args.save_episode, args.session_id)
        if args.json:
            print(json.dumps(entry, indent=2))
        else:
            print(f"✓ Saved episode: {entry['sessionId']} ({entry['tokenCount']} tokens)")
        return
    
    # ─── Constructor: build context ───
    result = construct_context(args.budget)
    
    if args.json:
        print(json.dumps(result, indent=2))
        return
    
    if args.manifest_only:
        print(json.dumps(result["manifest"], indent=2))
        return
    
    # Human-readable output
    facts = result["facts_count"]
    episodes = result["episodes_count"]
    human = result["human_count"]
    tokens = result["tokens_used"]
    budget = args.budget
    
    if result["context"]:
        print(f"📚 Context loaded: {facts} facts, {episodes} episodes, {human} annotations ({tokens}/{budget} tokens)")
        print(result["context"])
    else:
        print(f"📚 Context: empty (no persistent memory yet)")
    
    # Show manifest summary
    excluded = result["manifest"]["excluded"]
    if excluded:
        print(f"\n⚠️  {len(excluded)} items excluded (token budget)")


if __name__ == "__main__":
    main()
