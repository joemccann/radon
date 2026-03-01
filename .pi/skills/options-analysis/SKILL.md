---
description: On-demand options pricing and structure analysis for the Convex Scavenger trading agent
---

# Options Analysis Skill

## Description
On-demand options pricing and structure analysis for the Convex Scavenger trading agent.

## Capabilities
- Fetch and parse options chains for a given ticker
- Calculate implied volatility rank and percentile
- Evaluate convexity profile of candidate structures (calls, puts, vertical spreads)
- Estimate P(ITM), conditional settlement value, and expected value for each strike
- Compare structures: naked options vs. spreads for optimal convexity

## Usage
Invoke when evaluating a specific ticker's options chain as part of the /evaluate workflow.

## Dependencies
- scripts/fetch_options.py — data retrieval
- scripts/kelly.py — position sizing
- data/portfolio.json — current exposure context
