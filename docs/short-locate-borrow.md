# Short Locate / Borrow Playbook

Written 2026-06-12 after the SPCX (SpaceX IPO-day) short rejection. Covers: pre-short checks,
every IB locate/borrow surface, IPO-specific behavior, what "Order Inactive" means in Radon,
and borrow-cost math.

---

## 1. Pre-short checklist (run BEFORE submitting a SELL with no position)

1. **Shortable tier** — IB tick 46 (generic tick 236). Documented bands (code against bands, not literals):
   - `> 2.5` (in practice 3.0): easy to borrow, ≥1000 shares available
   - `> 1.5` (in practice 2.0): **locate required** (hard-to-borrow) — API shorts will likely reject
   - `≤ 1.5` (in practice 1.0): not shortable
2. **Shortable shares** — tick 89 (same generic tick 236, TWS Build 974+). `NaN`/never-ticks = IB publishing zero inventory.
3. **Fee/rebate rate** — NOT available via the TWS API. Sources: Client Portal SLB tool, TWS SLB Rates window, the shortstock FTP file, or UW `/api/shorts/{ticker}/data` (mirrors the IB file). Validate freshness AND instrument name — recycled tickers serve stale rows (SPCX returned 2026-04-07 data for a defunct SPAC ETF).
4. **SSR (Rule 201)** — only triggers on a −10% intraday drop vs prior close; restricts to above-bid pricing that day + next. Irrelevant for day-one IPOs (no prior close) and for stocks trading up.
5. **Carry math** — see §5. A 100% APR borrow costs ~1.4%/week; the trade needs to outrun that.

## 2. Where to check locate/borrow at IB

| Surface | How | Gives |
|---|---|---|
| Client Portal | Get Help (?) → "Short Securities (SLB) Availability" | qty available, # lenders, indicative borrow rate, history, bulk lookup |
| TWS | Mosaic → New Window → "SLB Rates" (Classic: Analytical Tools → SLB Rates); also "Shortable"/"Fee Rate" columns | charted daily rate history, intraday loan time & sales |
| Shortstock file | `ftp://shortstock:@ftp3.interactivebrokers.com/usa.txt` (FTP port 21, ~15-min updates) | `#SYM\|CUR\|NAME\|CON\|ISIN\|REBATERATE\|FEERATE\|AVAILABLE\|` |
| TWS API | `reqMktData(contract, "236", snapshot=False)` — streaming ONLY, never snapshot+generic (commit d6cc6ce) | tick 46 (difficulty), tick 89 (shares). **No fee-rate tick exists.** |
| UW mirror | `uw_client.get_short_data(ticker)` (`scripts/clients/uw_client.py:557`) | IB file fields (`short_shares_available`, `fee_rate`, `rebate_rate`) — check timestamp + name |

Caveat (verified 2026-06-12): FTP port 21 to ftp3/ftp2.interactivebrokers.com timed out from both
the laptop and the Hetzner VPS. Treat the file as a fallback; UW is the workable fee source today.

## 3. IPO-day rules of thumb

- **Day one: assume no borrow.** Reg SHO 203(b)(1) bars a broker from accepting a short without a
  locate; IPO allocations (priced after 4:30 pm ET) settle T+2, so no shares have reached custodial
  accounts and no lender holds them. The underwriting syndicate is itself net short via the
  greenshoe. IB marks the name non-shortable until its SLB system shows real inventory.
- **Borrow appears** days-to-weeks post-IPO: settlement + custodian enrollment into lending
  programs; flipped shares landing in margin accounts become lendable first.
- **Early fee is extreme** — scarcity pricing, routinely tens-of-percent to triple-digit APR for hot
  deals; eases as supply enrolls, spikes again into lockup expiry.
- Day-one shorting you see on the tape is 203(b)(2)-exempt market makers and negotiated
  institutional borrows, not locate-based retail/API shorts.
- Synthetic alternative: options usually list a few days post-IPO; puts embed the same borrow cost.

## 4. "Order Inactive" in Radon — what it means, where the reason lives

**IB semantics:** `Inactive` = order received but not working (rejected or held). The reason arrives
as a SEPARATE async errorEvent — classically code 201 "Order rejected — reason: ... shares not
available for short sale / locate required". `whyHeld` is essentially never populated on 201 rejects.
(Note: `docs/ib_tws_api.md:411` describes Inactive only as "outside market hours" — incomplete.)

**Why Radon shows no reason (as of 2026-06-12):** `scripts/ib_place_order.py` breaks its confirm
poll the moment status hits a terminal state (or permId is assigned), checks its errorEvent buffer
exactly once, and if the 201 hasn't landed yet returns the bare `"Order Inactive"`. FastAPI
`/orders/place` (`scripts/api/server.py:1411`) wraps it in a 502 with that string as detail and logs
nothing else. So when the race is lost, **the reason text exists nowhere on disk** (IB Gateway's own
logs are `.ibgzenc`-encrypted).

**What you can check today:**
```bash
# Confirm the place call + timing (only the access line will be there):
ssh root@ib-gateway journalctl -u radon-api --since "today" | grep "orders/place"
# Live shortability probe (read-only) — the substance behind a short reject:
#   reqMktData(Stock(SYM,'SMART','USD'), "236", snapshot=False), read ticker.shortable (46)
#   and ticker.shortableShares (89) for a few seconds, then cancelMktData + disconnect.
# radon-monitor sees order errorEvents only if connected at that instant (IB routes order
# errors to the placing clientId + client 0 only):
ssh root@ib-gateway journalctl -u radon-monitor --since "today" | grep -i "error\|reject"
```

**SPCX case (2026-06-12):** SELL 1 @ 170.81 DAY, permId 52686244 → final state at IB `Cancelled`,
zero filled. Live probe: tick 46 = 2.0 (locate required), tick 89 never published (no inventory).
UW mirror had only stale rows for the prior SPCX instrument. Classic IPO-day no-borrow rejection;
the literal 201 string was lost to the race above.

## 5. Borrow-cost math (IB methodology)

- Collateral = prior settlement price × 1.02, **rounded UP to the whole dollar**, × shares.
- Daily fee = (Collateral × Fee Rate APR) / 360, accrued every calendar day. Short-proceeds rebate
  (often negative for HTB) is on top.

Example — 100 sh of a $170.29 HTB name at 100% APR, held 5 days:
- Mark: 170.29 × 1.02 = 173.70 → round up → $174 → collateral $17,400
- Daily: 17,400 × 1.00 / 360 = **$48.33/day** → 5 days ≈ **$242** = ~1.4% of the $17,029 position
  per week. A 100% APR borrow needs ~74% annualized price decline just to cover carry.
- The round-up rule penalizes low-priced HTB names proportionally more.

Pre-borrow program (Portfolio Margin only, TWS SLB page, orders 6:45–14:45 ET): locks the borrow
the day you submit instead of at settlement; you pay fees on pre-borrowed shares in excess of your
short. It cannot conjure supply — useless on IPO day.
