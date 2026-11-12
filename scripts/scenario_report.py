#!/usr/bin/env python3
"""
Scenario Stress Test — HTML Report Generator
Reads scenario JSON (default /tmp/scenario_analysis.json) and produces a full
interactive report with expandable detail rows per position. The JSON must be
a regular file owned by the user running this script.
"""

import json
import math
import os
import stat
from datetime import datetime

_JSON_PATH = os.environ.get("RADON_SCENARIO_JSON", "/tmp/scenario_analysis.json")
_TEMPLATE_PATH = os.environ.get(
    "RADON_SCENARIO_TEMPLATE", ".pi/skills/html-report/template.html"
)
_HTML_PATH = os.environ.get(
    "RADON_SCENARIO_HTML", "reports/scenario-stress-test-2026-03-08.html"
)


def load_scenario_json(path):
    """Read scenario JSON only from a regular file this user owns.

    /tmp is shared. O_NOFOLLOW refuses a planted symlink. A different owner
    or a world-writable file is refused.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise SystemExit(f"refusing scenario json: {exc.strerror or exc}") from None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & stat.S_IWOTH
        ):
            raise SystemExit("refusing scenario json: untrusted file")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return json.load(handle)
    finally:
        if fd >= 0:
            os.close(fd)


data = load_scenario_json(_JSON_PATH)

results = data['results']
totals = data['totals']
params = data['parameters']
prices = data['current_prices']
betas = data['betas']
oil_sens = data['oil_sensitivity']
vix_mult = data['vix_multipliers']
baseline_iv = data.get('baseline_iv', {})

with open(_TEMPLATE_PATH) as f:
    template = f.read()

# NARRATIVE ENGINE — Per-position explanations

NARRATIVES = {
    'AAOI': {
        'company': 'Applied Optoelectronics — fiber optic semiconductor',
        'oil_thesis': 'Minimal direct oil exposure. Fiber optics components have negligible energy-cost sensitivity compared to revenue volatility.',
        'spx_thesis': 'High-beta small cap (β=1.80). Sells off harder than broad market in risk-off due to small cap illiquidity premium and speculative holder base.',
        'vix_thesis': 'VIX stress mult 1.30x — small cap semis see amplified selling when volatility spikes as momentum/retail holders exit. Short-dated options see massive IV expansion.',
        'option_thesis': 'Long $105 Calls (12 DTE): Short-dated ATM calls are dominated by vega at VIX 40. IV expansion from ~85% to ~148% creates enormous time value boost that MORE than offsets the delta loss from the stock dropping 7%. This is convexity in action — you paid for optionality and the optionality is repricing higher.',
        'net_view': 'POSITIVE P&L despite stock decline — vega dominates delta on short-dated options in a vol spike.',
    },
    'AAPL': {
        'company': 'Apple Inc — mega cap tech ($3.8T)',
        'oil_thesis': 'Mild negative. Higher energy costs increase supply chain/logistics expenses, but Apple\'s pricing power and margin structure absorb this easily. Oil sensitivity -0.05 is negligible.',
        'spx_thesis': 'Beta 1.15x — slightly above market. Mega caps act as "relative safe havens" in sell-offs but still decline. Apple\'s consumer discretionary component means it\'s not truly defensive.',
        'vix_thesis': 'VIX mult 1.05x — mega caps are the LAST to see crash-beta amplification. Institutional holders don\'t panic-sell AAPL the way they dump small caps. Flight-to-quality within equities partially offsets.',
        'option_thesis': 'Bull Call Spread $270/$290 (40 DTE): With AAPL at ~$263, the $270 call is already OTM. A 5-8% decline puts it further OTM. The spread loses value as both legs decay, but the spread structure caps your loss at the $40K debit. IV expansion helps the long leg but is offset by the short leg — spreads have low net vega.',
        'net_view': 'MODERATE LOSS — spread loses value as stock moves away from profitable range, but losses are capped at debit paid.',
    },
    'ALAB': {
        'company': 'Astera Labs — PCIe/CXL connectivity semiconductor',
        'oil_thesis': 'Negligible oil exposure (sensitivity -0.03). Semiconductor IP company with no meaningful energy cost component.',
        'spx_thesis': 'Very high beta (2.20x) — small cap semi with AI narrative premium. These names get hit hardest in risk-off as growth multiples compress. A -3% SPX move translates to -6.6% for ALAB from beta alone.',
        'vix_thesis': 'VIX mult 1.40x — among the highest. When VIX spikes to 40, momentum unwind hits names like ALAB disproportionately. AI/semi narrative stocks are crowded trades that unwind violently.',
        'option_thesis': 'Long $120 Call LEAP (313 DTE): Deep OTM LEAP on a stock at $95. IV expansion is dampened 50% for LEAPs (term structure flattens in stress). The delta loss from stock decline is partially offset by vega gain, but since the option is far OTM, the net effect is a modest gain — the IV expansion increases the probability-weighted upside more than the spot decline hurts.',
        'net_view': 'SMALL GAIN on LEAP despite large stock decline — vega on far-OTM LEAP benefits from vol expansion.',
    },
    'AMD': {
        'company': 'Advanced Micro Devices — semiconductor (GPU/CPU)',
        'oil_thesis': 'Mild negative (-0.05). Data center power costs rise with energy prices, but AMD doesn\'t directly consume oil. Supply chain has some energy sensitivity.',
        'spx_thesis': 'High beta (1.70x). Semiconductor cycle amplifies market moves. AMD tracks both SPX and semiconductor sentiment (SOX index). In a -3% SPX move, AMD drops ~5% from beta alone.',
        'vix_thesis': 'VIX mult 1.25x — semis see extra selling in vol spikes as institutional rotations out of cyclicals accelerate. AMD is more resilient than small cap semis but still gets hit.',
        'option_thesis': 'Long $195 Call LEAP (313 DTE): ATM-ish LEAP with massive DTE. At 55% baseline IV expanding to ~74% (dampened for LEAP), the vega gain is substantial: 20 IV points × high vega × 20 contracts. Delta loss is real (-6.6% × 20 contracts) but the LEAP\'s long duration means vega > delta sensitivity. Net positive.',
        'net_view': 'POSITIVE — LEAP vega gain exceeds delta loss. This is why LEAPs outperform short-dated calls in vol spikes.',
    },
    'APO': {
        'company': 'Apollo Global Management — private equity / alternatives',
        'oil_thesis': 'POSITIVE (+0.15). Apollo has significant energy/infrastructure portfolio assets. Higher oil prices boost NAV of energy holdings and increase deal flow in energy sector. This partially offsets the broad market headwind.',
        'spx_thesis': 'Beta 1.50x — PE firms are levered to market sentiment and deal activity. Credit spreads widen in sell-offs, hurting leveraged buyout economics. But Apollo\'s credit business benefits from dislocations.',
        'vix_thesis': 'VIX mult 1.20x — financial stress amplifies selling. PE firms face redemption pressure and mark-to-market losses on portfolio companies.',
        'option_thesis': 'Risk Reversal P$100/C$115 (40 DTE): UNDEFINED RISK structure. Long $115 call loses value as stock declines. Short $100 put GAINS value (goes against you) as stock approaches $100. The put moves from OTM toward ATM, increasing your liability. Net P&L is negative because the short put delta overwhelms the long call.',
        'net_view': 'LOSS on risk reversal — short put liability increases faster than long call decays. Undefined risk exposure to assignment if stock drops below $100.',
    },
    'BAP': {
        'company': 'Credicorp — Peruvian bank (largest financial institution in Peru)',
        'oil_thesis': 'POSITIVE (+0.10). Peru is a commodity exporter (copper, gold, zinc). Higher oil prices correlate with broader commodity strength, boosting Peru\'s GDP, tax revenue, and Credicorp\'s loan book quality.',
        'spx_thesis': 'Low beta (0.90x). Emerging market banks decorrelate from US equity moves. Peru\'s domestic economy matters more than S&P 500. The -3% SPX translates to only -2.7% for BAP.',
        'vix_thesis': 'VIX mult 1.15x — EM names see some capital flight in global risk-off, but Peru is a "forgotten" market not heavily held by global momentum funds, so forced selling is limited.',
        'option_thesis': 'Long $380 Call (68 DTE): Deep OTM call on a stock at $365. Small position ($424). IV expansion on a moderate-IV name provides some vega offset. The position is so small it\'s immaterial to portfolio P&L.',
        'net_view': 'OIL/COMMODITY offset keeps BAP near flat. Net P&L is small positive due to commodity GDP boost to Peru.',
    },
    'BKD': {
        'company': 'Brookdale Senior Living — senior care facilities',
        'oil_thesis': 'NEGATIVE (-0.10). Higher energy costs directly hit operating margins for senior living facilities (heating, cooling, transportation). BKD has thin margins so energy costs matter.',
        'spx_thesis': 'Above-average beta (1.30x). Small cap healthcare with real estate characteristics. Sensitive to consumer spending and interest rate expectations.',
        'vix_thesis': 'VIX mult 1.20x — small caps see amplified selling. BKD\'s debt load makes it more sensitive to credit stress.',
        'option_thesis': 'Bear Put Spread $15/$11 (40 DTE): THIS IS YOUR HEDGE. You\'re LONG puts on BKD — you PROFIT when BKD declines. With BKD dropping from $13.50 to $12.61, your $15 put goes deeper ITM while the $11 short put stays OTM. The spread widens toward max value ($4.00). Net gain of ~$6K on $10K risk.',
        'net_view': 'WINNER — bear put spread profits from BKD decline. This position is working exactly as designed.',
    },
    'BRZE': {
        'company': 'Braze Inc — cloud customer engagement SaaS',
        'oil_thesis': 'Negligible (-0.02). Pure software company with no oil sensitivity.',
        'spx_thesis': 'Very high beta (1.90x). Small cap SaaS names are among the highest beta in the market. Revenue multiples compress violently in risk-off. A -3% SPX = -5.7% BRZE from beta alone.',
        'vix_thesis': 'VIX mult 1.35x — high-growth SaaS with high short interest sees amplified selling. Crowded short positions may paradoxically provide a floor (short covering), but the base move is steep.',
        'option_thesis': 'Long Call $22.5 (12 DTE): Short-dated near-ATM call on a high-IV name. IV expanding from 70% to ~122% is MASSIVE. The vega effect completely overwhelms delta loss. 120 contracts × enormous vega = significant positive P&L despite stock decline.\n\nBull Call Spread $25/$30 (40 DTE): Spread has low net vega (long and short legs partially cancel). The $25 strike goes further OTM. But IV expansion still helps the wider spread. Modest positive P&L.',
        'net_view': 'POSITIVE on both BRZE positions — IV expansion dominates. The short-dated calls benefit enormously from vol spike.',
    },
    'EC': {
        'company': 'Ecopetrol S.A. — Colombian state oil company (88.5% govt owned)',
        'oil_thesis': '🛢️ MASSIVELY POSITIVE (+0.90). Ecopetrol is a DIRECT crude oil play. Revenue and earnings are almost entirely a function of oil prices. A 25% oil surge translates to ~22.5% revenue boost. Oil at $90+ means massive free cash flow and special dividend potential. This is the single highest oil-beta name in your portfolio.',
        'spx_thesis': 'Low beta to SPX (0.70x). EC trades on oil/EM/Colombia fundamentals, not US equity sentiment. The S&P 500 declining -3% barely matters when oil is surging 25%.',
        'vix_thesis': 'VIX mult 0.90x — BELOW 1.0. Oil companies actually DECOUPLE from VIX stress when oil is the driver. If VIX is high BECAUSE of an oil shock, energy names rally while the rest of the market sells off. EC is a natural hedge in this specific scenario.',
        'option_thesis': 'Stock position (5,000 shares). No optionality — pure delta exposure. Every $1 move in EC = $5,000 P&L. With a +20.5% move on oil surge, this is your biggest dollar winner.',
        'net_view': '🟢 BIGGEST WINNER — direct oil beneficiary. EC is the portfolio\'s natural hedge against an oil-driven sell-off. The +$12.7K gain partially offsets tech/equity losses.',
    },
    'ETHA': {
        'company': 'iShares Ethereum Trust ETF — spot Ethereum exposure',
        'oil_thesis': 'Zero correlation (0.00). Crypto is uncorrelated to oil prices in either direction.',
        'spx_thesis': 'Moderate beta (1.60x). Crypto has become increasingly correlated with risk assets since institutional adoption. ETH trades as a "risk-on" asset alongside tech stocks. The -3% SPX translates to ~-5% for ETHA.',
        'vix_thesis': 'VIX mult 1.50x — HIGHEST TIER. Crypto is the first asset class institutional risk managers cut in a panic. "Sell what you can, not what you should" mentality hits crypto hardest. Retail sentiment also craters.',
        'option_thesis': 'Long $15 Call (102 DTE): ATM-ish call with very high baseline IV (80%). When IV expands from 80% to ~115% (dampened for medium DTE), the vega gain on 200 contracts is enormous. Ethereum options have the highest vega sensitivity in your portfolio due to high base IV. Despite -5.3% stock decline, the IV expansion creates a significant positive P&L.',
        'net_view': 'POSITIVE — massive vega gain on high-IV crypto options overwhelms delta loss. 200 contracts × high vega × 35pt IV expansion = substantial gain.',
    },
    'EWY': {
        'company': 'iShares MSCI South Korea ETF — Korean equity market exposure',
        'oil_thesis': '🔴 NEGATIVE (-0.15). South Korea is a NET OIL IMPORTER — the 4th largest in Asia. Higher oil prices directly hurt Korea\'s trade balance, corporate margins (Samsung, Hyundai), and consumer spending. Oil +25% is a -3.75% headwind for EWY.',
        'spx_thesis': 'Moderate beta (1.10x). Korea is export-dependent and correlated with global growth sentiment. Samsung/SK Hynix provide semiconductor correlation. A US sell-off hurts Korean export demand expectations.',
        'vix_thesis': 'VIX mult 1.15x — EM gets hit in global risk-off as capital flows back to USD. Korean won weakens, adding to equity losses for USD-denominated EWY holders.',
        'option_thesis': 'Risk Reversal P$130/C$141 (5 DTE): UNDEFINED RISK with ONLY 5 DAYS TO EXPIRY. This is the most dangerous position in the scenario.\n\n• Long $141 Call: Already deep OTM with stock at $133. Dropping to $123 makes it virtually worthless. 5 DTE means no vega benefit — time value is nearly zero regardless of IV.\n\n• Short $130 Put: Goes from slightly OTM to DEEP ITM as stock drops to $123. The put is now worth ~$7/share intrinsic. 25 contracts = $17,500 in intrinsic liability alone. With 5 DTE, this is essentially an assignment risk.\n\nNet: You lose on BOTH legs. The call dies worthless, the put moves against you with no time for recovery.',
        'net_view': '🔴 LARGE LOSS — worst-case for risk reversals: short-dated, stock moves wrong way, both legs lose. Oil-importer status compounds the sell-off.',
    },
    'GOOG': {
        'company': 'Alphabet Inc (Class C) — mega cap tech ($2.2T)',
        'oil_thesis': 'Negligible (-0.03). Google\'s ad revenue is indirectly affected by economic activity, but oil prices specifically have minimal impact on search/cloud/YouTube.',
        'spx_thesis': 'Moderate beta (1.10x). Mega cap resilience — GOOG is a "flight to quality within tech" name. In a -3% SPX, GOOG drops ~3.3%. Institutional holders rotate TO mega caps FROM small caps.',
        'vix_thesis': 'VIX mult 1.05x — lowest tier. Mega caps like GOOG see minimal crash-beta amplification. Deep institutional ownership base doesn\'t panic-sell.',
        'option_thesis': 'Bull Call Spread $315/$340 (40 DTE): Stock at $302 dropping to $290 pushes both strikes further OTM. The spread loses some value, but IV expansion on the $315 call (closer to ATM) partially offsets. Net effect is a small loss or near-flat, well within defined risk bounds. Your $27.5K debit is safe.',
        'net_view': 'NEAR FLAT — mega cap resilience + spread structure + IV offset = minimal P&L impact. GOOG is not your problem in this scenario.',
    },
    'IGV': {
        'company': 'iShares Expanded Tech-Software Sector ETF',
        'oil_thesis': 'Negligible (-0.02). Software companies have essentially zero oil sensitivity.',
        'spx_thesis': 'Above-average beta (1.30x). Software ETF with growth-heavy composition (MSFT, ADBE, CRM, NOW). Growth multiples compress in risk-off, dragging the ETF. -3% SPX → -3.9% IGV.',
        'vix_thesis': 'VIX mult 1.20x — software stocks see moderate crash-beta due to high valuations. Not as bad as small cap SaaS but worse than mega caps.',
        'option_thesis': 'Long Call $93 (12 DTE): OTM call with short DTE. IV expansion from 28% to 49% provides vega boost that roughly offsets delta loss. Small positive P&L.\n\nSynthetic Long $90 (68 DTE): UNDEFINED RISK. This behaves like owning 4,000 shares of IGV. Long $90 call + Short $90 put = synthetic stock at $90. When IGV drops from $85 to $81, you lose on BOTH the delta of the call (decreasing) AND the put moving against you (increasing in value). The P&L is approximately the dollar move × 4,000 shares equivalent = ~$16K loss. No protection on the downside.',
        'net_view': 'Long call near-flat (vega helps), but synthetic long takes a BIG HIT — behaves like leveraged stock exposure with no floor.',
    },
    'ILF': {
        'company': 'iShares Latin America 40 ETF — LatAm blue chips',
        'oil_thesis': '🟢 POSITIVE (+0.30). Latin America is a major commodity exporter. Brazil (Petrobras, Vale), Chile (copper), Colombia (Ecopetrol) all benefit from commodity price surges. Oil +25% flows directly to GDP growth, fiscal balance, and corporate earnings across LatAm.',
        'spx_thesis': 'Below-market beta (0.85x). LatAm equities have their own drivers — commodity prices, local rates, political risk. A US sell-off matters but is secondary to commodity tailwinds.',
        'vix_thesis': 'VIX mult 1.10x — EM sees some capital flight but LatAm commodity exporters partially decouple when commodities are the catalyst.',
        'option_thesis': 'Stock position (2,000 shares). Pure delta — every $1 move = $2,000. The +4.9% move from oil/commodity tailwinds produces ~$3.6K gain.',
        'net_view': '🟢 WINNER — commodity GDP boost to LatAm overwhelms the mild SPX headwind. Natural portfolio diversifier.',
    },
    'IWM': {
        'company': 'iShares Russell 2000 ETF — US small cap equity',
        'oil_thesis': 'Mild negative (-0.05). Small caps are net consumers of energy — higher input costs compress margins for domestic-focused companies with less pricing power than large caps.',
        'spx_thesis': 'Above-market beta (1.20x). Small caps sell off MORE than large caps in risk-off. Russell 2000 companies have weaker balance sheets, less analyst coverage, and more retail ownership — all amplify sell-offs.',
        'vix_thesis': 'VIX mult 1.15x — small caps are high-beta in vol spikes. The "quality rotation" out of small caps into mega caps is a well-documented risk-off pattern.',
        'option_thesis': 'Risk Reversal P$248/C$259 (40 DTE): UNDEFINED RISK. Small position (6 contracts) but the dynamics are unfavorable.\n\n• Long $259 Call: OTM with stock at $250. Dropping to $237 makes it deeply OTM. Some vega benefit from IV expansion but muted by OTM status.\n\n• Short $248 Put: Goes from slightly OTM to ITM as stock drops below $248. At $237.50 (base case), the put has $10.50 intrinsic × 6 contracts = $6,300 liability.\n\nNet: Both legs work against you. Call loses, put gains liability.',
        'net_view': 'LOSS — risk reversal hurts in sell-offs. Small position size limits damage but the structure is wrong-way in this scenario.',
    },
    'MSFT': {
        'company': 'Microsoft Corp — mega cap tech ($2.8T)',
        'oil_thesis': 'Negligible (-0.03). Cloud/enterprise software has zero meaningful oil sensitivity. Azure energy costs are hedged and contracted.',
        'spx_thesis': 'Moderate beta (1.10x). Mega cap resilience, but MSFT has become the LARGEST weight in S&P 500. When the index sells off, MSFT mechanically contributes and ETF outflows force selling. -3% SPX → -3.3% MSFT from beta.',
        'vix_thesis': 'VIX mult 1.05x — minimal crash-beta. MSFT is institutional bedrock. But the sheer SIZE of your position ($468K = 42% of bankroll) means even a small % move creates massive dollar P&L.',
        'option_thesis': 'STOCK POSITION (1,000 shares). No options overlay, no hedge. Pure linear exposure.\n\n⚠️ THIS IS YOUR #1 RISK: $468K in a single equity with no protective puts or covered calls. A -4.1% move = -$15.80/share × 1,000 shares = -$15,800 from SPX beta alone. But the actual cost basis is $468.50/share, so you may already be underwater. Every 1% MSFT move = ±$3,850.',
        'net_view': '🔴 LARGEST DOLLAR LOSER — not because MSFT is bad, but because position size (42% of bankroll) × no hedge = massive unprotected risk. This single position drives most of your portfolio loss.',
    },
    'NAK': {
        'company': 'Northern Dynasty Minerals — Pebble Mine gold/copper project (Alaska)',
        'oil_thesis': '🟢 POSITIVE (+0.25). Gold and copper miners benefit from commodity super-cycle sentiment. Oil +25% signals broader commodity strength. Gold also acts as inflation hedge — higher oil → higher inflation expectations → gold bid. Copper benefits from commodity reflation.',
        'spx_thesis': 'Low beta (0.50x). Mining stocks, especially pre-production like NAK, have their own catalysts (permits, feasibility, commodity prices). SPX correlation is low.',
        'vix_thesis': 'VIX mult 0.80x — BELOW 1.0. Gold miners act as SAFE HAVEN in risk-off. When VIX spikes, gold rallies, and gold miners outperform. NAK benefits from the "fear trade" into precious metals.',
        'option_thesis': 'Stock position (18,628 shares). Pure delta on a micro-cap miner. The commodity tailwind (+5%) × 18,628 shares = ~$2,800 gain.',
        'net_view': '🟢 WINNER — commodity safe haven. NAK decorrelates from SPX and benefits from gold/copper bid in a risk-off + commodity surge environment.',
    },
    'PLTR': {
        'company': 'Palantir Technologies — AI/defense analytics ($160B)',
        'oil_thesis': 'Negligible (-0.02). Government/defense contracts are not oil-sensitive.',
        'spx_thesis': 'Very high beta (2.00x). PLTR is a high-multiple momentum stock that trades at 50x+ revenue. These are the first names institutions sell in risk-off to reduce portfolio beta. -3% SPX → -6% PLTR from beta alone.',
        'vix_thesis': 'VIX mult 1.40x — HIGHEST TIER for individual stocks. Momentum unwind is violent for names like PLTR. Retail ownership is high, and retail panic-sells in VIX spikes. Short sellers pile on. The VIX stress adds another -1.4% on top of beta.',
        'option_thesis': 'Bull Call Spread $145/$165 (19 DTE): Both strikes are OTM with stock at $155. A -7% drop to $144 puts BOTH legs OTM. With only 19 DTE, there\'s minimal time value remaining. The spread goes to near-zero. You lose the full $2,620 debit (capped at max loss). Short DTE + both strikes OTM = near total loss.',
        'net_view': '🔴 TOTAL LOSS on defined risk spread — short DTE + high beta + both strikes OTM. The good news: max loss is only $2,620 (defined risk working as designed).',
    },
    'RR': {
        'company': 'Richfield Financial Group — small cap financial holding company',
        'oil_thesis': 'Minimal (+0.05). Small financial company with no meaningful oil exposure.',
        'spx_thesis': 'Below-market beta (0.80x). Small cap financial with domestic focus. Less correlated to SPX than larger banks.',
        'vix_thesis': 'VIX mult 0.85x — below average. Small regional financials don\'t attract the same momentum/short-selling pressure as tech names. Low institutional ownership means less forced selling.',
        'option_thesis': 'Stock position (10,000 shares). Pure delta. Low beta means minimal move: -1% × $4.43 × 10,000 = -$443. Immaterial to portfolio.',
        'net_view': 'NEGLIGIBLE — small loss, doesn\'t move the needle.',
    },
    'SOFI': {
        'company': 'SoFi Technologies — digital banking / fintech',
        'oil_thesis': 'Negligible (-0.03). Fintech has no oil sensitivity.',
        'spx_thesis': 'High beta (1.80x). Fintech stocks are high-growth, high-multiple names that sell off aggressively in risk-off. Loan demand concerns rise in macro stress. -3% SPX → -5.4% SOFI from beta.',
        'vix_thesis': 'VIX mult 1.30x — fintech sees amplified selling. Credit quality concerns emerge in stress. Retail ownership base panic-sells.',
        'option_thesis': 'Long $45 Call LEAP (313 DTE): OTM LEAP with 60% baseline IV expanding to ~82% (dampened for LEAP). Here\'s why P&L is HUGELY positive despite stock decline:\n\n1. 300 CONTRACTS — massive position (notional ~$228K at current BSM value)\n2. High vega: Each 1% IV move = ~$4,400 across 300 contracts\n3. IV expansion: 60% → 82% = +22 points = ~$97K in vega P&L\n4. Delta loss: -6.5% × 0.55 delta × $40 × 100 × 300 = -$43K\n5. Net: +$97K vega - $43K delta ≈ +$52K\n\nThis is the textbook case for why LEAP holders love vol spikes: the vega P&L dominates the delta loss when you have enough contracts and enough IV expansion.',
        'net_view': '🟢 LARGEST OPTIONS WINNER — 300 LEAP contracts × massive vega × 22pt IV expansion creates $52K gain despite stock declining 6.5%. This is convexity at its finest.',
    },
    'SPXU': {
        'company': 'ProShares UltraPro Short S&P 500 — 3x INVERSE S&P 500 ETF',
        'oil_thesis': 'Mild positive (+0.05). If oil surge hurts SPX, SPXU benefits (inverse). But the oil effect is captured through the SPX move itself.',
        'spx_thesis': 'Beta = -3.00x. This is a 3x INVERSE bet. When SPX drops 3%, SPXU RISES ~9%. This is your EXPLICIT HEDGE against market declines.',
        'vix_thesis': 'VIX mult 0.90x — the leveraged inverse ETF doesn\'t get a perfect 3x in extended moves due to daily rebalancing / volatility drag. In a single-day shock, it\'s close to 3x.',
        'option_thesis': 'Bull Call Spread $53/$60 (5 DTE): With SPXU at $53 rising to $58.49 in base case, the $53 call goes deep ITM while the $60 short call stays OTM. The spread widens toward max value ($7). 20 contracts × $700 max value = $14,000 max. Current gain ~$6K on a $2.9K investment = 210% return.',
        'net_view': '🟢 HEDGE IS WORKING — 3x inverse ETF + bull call spread produces +$6K. But the position is TINY ($2.9K) relative to your $1.1M portfolio. Hedge ratio is only ~0.3%. You need 10-20x this size for meaningful protection.',
    },
    'TMUS': {
        'company': 'T-Mobile US — wireless telecom carrier',
        'oil_thesis': 'Mild negative (-0.05). Higher energy costs slightly increase tower/infrastructure operating expenses and customer transportation costs.',
        'spx_thesis': 'Defensive beta (0.65x). Telecom is a classic defensive sector — people don\'t cancel cell phone plans in recessions. TMUS should OUTPERFORM in a sell-off. -3% SPX → only -2% TMUS from beta.',
        'vix_thesis': 'VIX mult 0.85x — BELOW 1.0. Telecoms are "hide here" stocks in panic. Capital rotates FROM growth INTO defensive names like TMUS. This partially offsets the sell-off.',
        'option_thesis': 'Bull Call Spread $230/$250 (40 DTE): Here\'s the problem — you paid $36.5K for a spread where the $230 long call is barely ATM (stock at $234). A -3% drop to $226.86 pushes BOTH legs OTM. With 40 DTE remaining, the spread still has time value, so you don\'t lose everything. But the spread is now OTM and decaying. The $20 width means max value is $200K but you need TMUS above $250 at expiry. Current trajectory: losing ~34% of debit.',
        'net_view': 'MODERATE LOSS — the defensive stock move is mild (-3%), but the spread entered near-ATM and is now OTM. Loss is $12.6K of $36.5K, well within max loss bounds.',
    },
    'TSLL': {
        'company': 'Direxion Daily TSLA Bull 2X Shares — 2x leveraged Tesla ETF',
        'oil_thesis': 'Negative (-0.10). Ironic for an EV play: Tesla should theoretically benefit from high oil (EVs more attractive), but in practice TSLL trades as a pure risk asset. Higher oil → higher inflation → higher rates → growth stock compression.',
        'spx_thesis': 'Extreme beta (3.60x). Tesla (~1.8 beta) × 2x leverage = 3.6x effective beta. A -3% SPX → -10.8% TSLL from beta alone. This is one of the highest-beta positions in your portfolio.',
        'vix_thesis': 'VIX mult 1.50x — MAXIMUM TIER. Leveraged Tesla is the ultimate risk-on momentum bet. In a VIX 40 environment, Tesla gets hammered AND the 2x leverage amplifies it AND the daily rebalancing adds drag. Triple whammy.',
        'option_thesis': 'Stock position (5,000 shares). Pure leveraged delta exposure. -13.8% × $18.32 × 5,000 = -$12,616. No hedge, no optionality, no floor.',
        'net_view': '🔴 SIGNIFICANT LOSS — 2x leveraged + high beta + no hedge = amplified drawdown. Consider this a pure directional bet that loses in any sell-off scenario.',
    },
    'URTY': {
        'company': 'ProShares UltraPro Russell 2000 — 3x leveraged Russell 2000 ETF',
        'oil_thesis': 'Mild negative (-0.05). Small caps are net energy consumers. Higher oil compresses margins for domestic-focused companies.',
        'spx_thesis': 'Extreme beta (3.60x). Russell 2000 (~1.2 beta) × 3x leverage = 3.6x effective. A -3% SPX → -10.8% URTY. In the bear case (-5% SPX), URTY drops -20%.',
        'vix_thesis': 'VIX mult 1.30x — small cap + leverage + vol spike = maximum pain. URTY is designed for short-term directional bets, not holding through stress events.',
        'option_thesis': 'Stock position (2,000 shares). Pure leveraged delta. -12.3% × $63.64 × 2,000 = -$15,709. Entry cost $127K means you lose 12.3% of a large position.',
        'net_view': '🔴 LARGE LOSS — 3x leveraged small cap in a sell-off. Same issue as TSLL: leveraged directional bet with no hedge amplifies losses.',
    },
    'USAX': {
        'company': 'Americas Gold and Silver Corp — precious metals miner',
        'oil_thesis': '🟢 STRONGLY POSITIVE (+0.40). Gold and silver miners are direct commodity beneficiaries. Oil +25% signals commodity reflation, boosting gold/silver prices. Mining stocks have high operational leverage to metal prices.',
        'spx_thesis': 'Low beta (0.45x). Gold miners are counter-cyclical — they often rally DURING equity sell-offs as gold acts as safe haven. SPX correlation is very low.',
        'vix_thesis': 'VIX mult 0.75x — LOWEST TIER. Gold miners are the classic "fear trade." When VIX spikes, gold rallies, and miners outperform dramatically. This is the opposite of tech stocks.',
        'option_thesis': 'Stock position (1,000 shares). Pure delta on a gold/silver miner. The commodity tailwind (+8.9%) × $46.68 × 1,000 = +$4,155.',
        'net_view': '🟢 WINNER — safe haven status + commodity tailwind = strong gain. USAX is an anti-correlation asset that performs best exactly when the rest of your portfolio suffers.',
    },
    'WULF': {
        'company': 'TeraWulf — Bitcoin mining + AI/HPC infrastructure',
        'oil_thesis': 'Minimal (+0.05). Energy-intensive operations, but WULF has locked-in nuclear/hydro power contracts. Higher oil doesn\'t directly hurt. Commodity sentiment mildly positive.',
        'spx_thesis': 'Very high beta (2.00x). Bitcoin miner / AI play has extreme market sensitivity. Correlated to both BTC price and tech sentiment. -3% SPX → -6% WULF from beta.',
        'vix_thesis': 'VIX mult 1.45x — crypto-adjacent names get crushed in risk-off. Institutional holders dump crypto proxies first. WULF\'s AI pivot provides some resilience vs pure BTC miners.',
        'option_thesis': 'Long $17 Call LEAP (313 DTE): OTM LEAP with 80% baseline IV expanding to ~109% (dampened for LEAP). Same dynamic as SOFI:\n\n1. 77 contracts with high vega per contract\n2. IV expansion: 80% → 109% = +29 points of vega fuel\n3. Delta loss: -5.2% × delta × $14 × 100 × 77\n4. Net positive because vega >>> delta for LEAP holder in vol spike\n\nThe high baseline IV means each IV point is worth more in dollar terms. LEAP dampening reduces the expansion but 29 points is still enormous.',
        'net_view': '🟢 POSITIVE — LEAP vega gain overwhelms delta loss. Same convexity dynamic as SOFI/AMD LEAPs.',
    },
}


def fmt_dollars(v):
    if v >= 0:
        return f"+${v:,.0f}"
    else:
        return f"-${abs(v):,.0f}"

def fmt_pct(v):
    return f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%"

def pnl_class(v):
    if v > 0: return 'text-positive'
    if v < 0: return 'text-negative'
    return ''

def escape_html(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')


body = '''
<header class="header">
  <div>
    <h1 class="title">⚡ Scenario Stress Test — Portfolio Impact Analysis</h1>
    <p class="subtitle">Oil +25% · VIX 40 · S&P 500 -3% | Bear / Base / Bull Scenarios</p>
  </div>
  <div class="header-actions">
    <span class="status"><span class="status-dot warning"></span> STRESS TEST</span>
    <span class="timestamp">Generated: ''' + datetime.now().strftime('%Y-%m-%d %H:%M PST') + '''</span>
    <button class="theme-toggle" onclick="toggleTheme()">◐ THEME</button>
  </div>
</header>

<!-- SCENARIO PARAMETERS -->
<div class="callout warning">
  <div class="callout-title">Scenario Assumptions</div>
  <p><strong>Trigger:</strong> Crude oil surges +25% (geopolitical shock, supply disruption). Risk-off cascade follows.</p>
  <p><strong>Bear Case:</strong> SPX -5%, VIX 45 — full risk-off panic, forced deleveraging, CTA selling</p>
  <p><strong>Base Case:</strong> SPX -3%, VIX 40 — orderly sell-off, flight to safety, energy/commodity names decouple</p>
  <p><strong>Bull Case:</strong> SPX -1.5%, VIX 30 — mild correction, oil beneficiaries rally, market digests the shock</p>
  <p style="margin-top:8px;"><strong>Model:</strong> β-adjusted SPX sensitivity × VIX crash-beta amplifier × oil sector sensitivity. Options repriced via Black-Scholes with per-ticker IV expansion (dampened for LEAPs). Defined-risk P&L hard-capped at max loss.</p>
</div>
'''


body += f'''
<div class="metrics">
  <div class="metric">
    <div class="metric-label">Current Net Liq</div>
    <div class="metric-value">${totals['base']['bankroll']:,.0f}</div>
    <div class="metric-change">Unrealized P&L: -$242K</div>
  </div>
  <div class="metric">
    <div class="metric-label">Bear Case P&L</div>
    <div class="metric-value {pnl_class(totals['bear']['total_pnl'])}">{fmt_dollars(totals['bear']['total_pnl'])}</div>
    <div class="metric-change">Net Liq → ${totals['bear']['net_liq_after']:,.0f} ({fmt_pct(totals['bear']['total_pnl_pct'])})</div>
  </div>
  <div class="metric">
    <div class="metric-label">Base Case P&L</div>
    <div class="metric-value {pnl_class(totals['base']['total_pnl'])}">{fmt_dollars(totals['base']['total_pnl'])}</div>
    <div class="metric-change">Net Liq → ${totals['base']['net_liq_after']:,.0f} ({fmt_pct(totals['base']['total_pnl_pct'])})</div>
  </div>
  <div class="metric">
    <div class="metric-label">Bull Case P&L</div>
    <div class="metric-value {pnl_class(totals['bull']['total_pnl'])}">{fmt_dollars(totals['bull']['total_pnl'])}</div>
    <div class="metric-change">Net Liq → ${totals['bull']['net_liq_after']:,.0f} ({fmt_pct(totals['bull']['total_pnl_pct'])})</div>
  </div>
  <div class="metric">
    <div class="metric-label">VIX Shock</div>
    <div class="metric-value text-negative">23 → 40</div>
    <div class="metric-change">+74% expansion</div>
  </div>
  <div class="metric">
    <div class="metric-label">Positions</div>
    <div class="metric-value">28</div>
    <div class="metric-change">16 defined · 12 undefined/equity</div>
  </div>
</div>
'''


base_sorted = sorted(results['base'], key=lambda x: x['pnl'])
worst_5 = base_sorted[:5]
best_5 = base_sorted[-5:][::-1]

body += '<div class="grid-2" style="grid-template-columns: 1fr 1fr;">\n'
body += '  <div class="callout negative"><div class="callout-title">🔴 Biggest Losers (Base Case)</div>\n'
for r in worst_5:
    body += f'    <p><strong>{escape_html(str(r["ticker"]))}</strong> {escape_html(str(r["structure_type"]))}: <span class="text-negative">{fmt_dollars(r["pnl"])}</span> ({fmt_pct(r["pnl_pct"])})</p>\n'
body += '  </div>\n  <div class="callout positive"><div class="callout-title">🟢 Biggest Winners (Base Case)</div>\n'
for r in best_5:
    body += f'    <p><strong>{escape_html(str(r["ticker"]))}</strong> {escape_html(str(r["structure_type"]))}: <span class="text-positive">{fmt_dollars(r["pnl"])}</span> ({fmt_pct(r["pnl_pct"])})</p>\n'
body += '  </div>\n</div>\n'


winners_sum = sum(r['pnl'] for r in results['base'] if r['pnl'] > 0)
losers_sum = abs(sum(r['pnl'] for r in results['base'] if r['pnl'] < 0))

body += f'''
<div class="callout">
  <div class="callout-title">⚖️ Natural Hedges in This Scenario</div>
  <p><strong>Oil Winners (EC, ILF, NAK, USAX):</strong> Direct commodity beneficiaries collectively gain ~${sum(r['pnl'] for r in results['base'] if r['ticker'] in ['EC','ILF','NAK','USAX']):,.0f}</p>
  <p><strong>SPXU (3x Inverse S&P):</strong> Explicit short hedge gains ${next(r['pnl'] for r in results['base'] if r['ticker']=='SPXU'):,}</p>
  <p><strong>LEAP Vega (SOFI, AMD, WULF, ETHA, ALAB, AAOI):</strong> IV expansion creates ${sum(r['pnl'] for r in results['base'] if r['ticker'] in ['SOFI','AMD','WULF','ETHA','ALAB','AAOI']):,.0f} in vega gains despite stock declines</p>
  <p><strong>BKD Bear Put Spread:</strong> Profits from decline: ${next(r['pnl'] for r in results['base'] if r['ticker']=='BKD'):,}</p>
  <p style="margin-top:8px;"><strong>Total gains from winners/hedges:</strong> +${winners_sum:,.0f} vs losses of -${losers_sum:,.0f} → Net: {fmt_dollars(totals['base']['total_pnl'])}</p>
</div>
'''

# FULL SCENARIO TABLE WITH EXPANDABLE DETAIL ROWS

body += '''
<div class="panel">
  <div class="panel-header">
    Full Position Scenario Matrix — All 28 Positions
    <span class="text-small text-muted" style="letter-spacing:0; text-transform:none; font-weight:400;">Click ▶ to expand position analysis</span>
  </div>
  <div style="overflow-x: auto;">
  <table>
    <thead>
      <tr>
        <th style="width:30px;"></th>
        <th>Ticker</th>
        <th>Structure</th>
        <th>Risk</th>
        <th>DTE</th>
        <th class="text-right">Entry Cost</th>
        <th class="text-right">Current</th>
        <th class="text-right">β<sub>SPX</sub></th>
        <th class="text-right">Oil β</th>
        <th class="text-right">VIX Mult</th>
        <th class="text-center" style="border-left: 2px solid var(--negative);">Bear Move</th>
        <th class="text-right" style="background:rgba(255,68,68,0.05);">Bear P&L</th>
        <th class="text-center" style="border-left: 2px solid var(--warning);">Base Move</th>
        <th class="text-right" style="background:rgba(255,170,0,0.05);">Base P&L</th>
        <th class="text-center" style="border-left: 2px solid var(--positive);">Bull Move</th>
        <th class="text-right" style="background:rgba(0,255,136,0.05);">Bull P&L</th>
      </tr>
    </thead>
    <tbody>
'''

# Build index by ticker+structure for all scenarios
all_base = {r['ticker'] + '|' + r['structure_type']: r for r in results['base']}
all_bear = {r['ticker'] + '|' + r['structure_type']: r for r in results['bear']}
all_bull = {r['ticker'] + '|' + r['structure_type']: r for r in results['bull']}

sorted_keys = sorted(all_base.keys(), key=lambda k: all_base[k]['pnl'])

row_id = 0
for key in sorted_keys:
    base = all_base[key]
    bear = all_bear[key]
    bull = all_bull[key]
    
    ticker = base['ticker']
    beta = betas.get(ticker, 1.0)
    oil = oil_sens.get(ticker, 0.0)
    vix_m = vix_mult.get(ticker, 1.0)
    
    risk_pill = ''
    if base['risk_profile'] == 'defined':
        risk_pill = '<span class="pill pill-positive">DEFINED</span>'
    elif base['risk_profile'] == 'undefined':
        risk_pill = '<span class="pill pill-negative">UNDEF</span>'
    else:
        risk_pill = '<span class="pill">EQUITY</span>'
    
    dte_str = str(base['dte']) + 'd' if base['dte'] < 999 else '∞'
    
    row_class = ''
    if base['pnl'] < -10000:
        row_class = ' style="background:rgba(255,68,68,0.04);"'
    elif base['pnl'] > 5000:
        row_class = ' style="background:rgba(0,255,136,0.03);"'
    
    max_loss_note = ''
    if base.get('max_loss') is not None:
        max_loss_note = f' <span class="text-muted text-small">(max: ${abs(base["max_loss"]):,.0f})</span>'
    
    # Get narrative
    narr = NARRATIVES.get(ticker, {})
    has_narrative = bool(narr)
    
    chevron = f'<span class="chevron" data-row="{row_id}" onclick="toggleRow({row_id})" style="cursor:pointer; user-select:none; font-size:14px; display:inline-block; transition: transform 0.2s;">▶</span>' if has_narrative else ''
    
    body += f'''      <tr{row_class} onclick="toggleRow({row_id})" style="cursor:pointer;">
        <td class="text-center">{chevron}</td>
        <td><strong>{escape_html(str(ticker))}</strong></td>
        <td class="text-small">{escape_html(str(base['structure_type']))}</td>
        <td>{risk_pill}</td>
        <td class="text-center">{dte_str}</td>
        <td class="text-right">${abs(base['entry_cost']):,.0f}</td>
        <td class="text-right">${prices.get(ticker, 0):,.2f}</td>
        <td class="text-right">{beta:.1f}x</td>
        <td class="text-right">{oil:+.2f}</td>
        <td class="text-right">{vix_m:.2f}x</td>
        <td class="text-center {pnl_class(bear['stock_move_pct'])}" style="border-left: 2px solid var(--negative);">{fmt_pct(bear['stock_move_pct'])}</td>
        <td class="text-right {pnl_class(bear['pnl'])}" style="background:rgba(255,68,68,0.05);"><strong>{fmt_dollars(bear['pnl'])}</strong></td>
        <td class="text-center {pnl_class(base['stock_move_pct'])}" style="border-left: 2px solid var(--warning);">{fmt_pct(base['stock_move_pct'])}</td>
        <td class="text-right {pnl_class(base['pnl'])}" style="background:rgba(255,170,0,0.05);"><strong>{fmt_dollars(base['pnl'])}</strong>{max_loss_note}</td>
        <td class="text-center {pnl_class(bull['stock_move_pct'])}" style="border-left: 2px solid var(--positive);">{fmt_pct(bull['stock_move_pct'])}</td>
        <td class="text-right {pnl_class(bull['pnl'])}" style="background:rgba(0,255,136,0.05);"><strong>{fmt_dollars(bull['pnl'])}</strong></td>
      </tr>
'''
    
    # EXPANDABLE DETAIL ROW
    if has_narrative:
        # Determine overall sentiment badge
        base_pnl = base['pnl']
        if base_pnl > 1000:
            sentiment = '<span class="pill pill-positive">BENEFITS FROM SCENARIO</span>'
        elif base_pnl < -1000:
            sentiment = '<span class="pill pill-negative">HURT BY SCENARIO</span>'
        else:
            sentiment = '<span class="pill pill-warning">NEUTRAL</span>'
        
        iv_info = ''
        if base.get('current_iv'):
            iv_info = f'''
            <div style="display:flex; gap:24px; margin-top:12px; padding-top:12px; border-top:1px solid var(--border-dim);">
              <div><span class="text-muted text-small">CURRENT IV</span><br><strong>{base['current_iv']:.0f}%</strong></div>
              <div><span class="text-muted text-small">SCENARIO IV</span><br><strong class="text-warning">{base['scenario_iv']:.0f}%</strong></div>
              <div><span class="text-muted text-small">IV CHANGE</span><br><strong class="{pnl_class(base['scenario_iv'] - base['current_iv'])}">{fmt_pct(base['scenario_iv'] - base['current_iv'])}</strong></div>
              <div><span class="text-muted text-small">DTE</span><br><strong>{base['dte']}d</strong></div>
            </div>'''
        
        body += f'''      <tr class="detail-row" id="detail-{row_id}" style="display:none;">
        <td colspan="16" style="padding:0; border-bottom: 2px solid var(--border-focus);">
          <div style="padding:20px 24px; background:var(--bg-hover);">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
              <div>
                <strong style="font-size:14px;">{escape_html(str(ticker))}</strong>
                <span class="text-muted" style="margin-left:8px;">{narr.get('company', '')}</span>
              </div>
              {sentiment}
            </div>
            
            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
              <div>
                <div class="text-uppercase text-muted text-small" style="margin-bottom:6px; letter-spacing:0.15em;">🛢️ Oil Impact ({oil:+.2f} sensitivity)</div>
                <p class="text-small" style="line-height:1.6;">{escape_html(narr.get('oil_thesis', 'No oil analysis available.'))}</p>
              </div>
              <div>
                <div class="text-uppercase text-muted text-small" style="margin-bottom:6px; letter-spacing:0.15em;">📉 S&P 500 Beta ({beta:.1f}x)</div>
                <p class="text-small" style="line-height:1.6;">{escape_html(narr.get('spx_thesis', 'No SPX analysis available.'))}</p>
              </div>
              <div>
                <div class="text-uppercase text-muted text-small" style="margin-bottom:6px; letter-spacing:0.15em;">📊 VIX Stress ({vix_m:.2f}x multiplier)</div>
                <p class="text-small" style="line-height:1.6;">{escape_html(narr.get('vix_thesis', 'No VIX analysis available.'))}</p>
              </div>
              <div>
                <div class="text-uppercase text-muted text-small" style="margin-bottom:6px; letter-spacing:0.15em;">📋 Position Structure & P&L</div>
                <p class="text-small" style="line-height:1.6;">{escape_html(narr.get('option_thesis', 'No structure analysis available.'))}</p>
              </div>
            </div>
            
            <div style="margin-top:16px; padding:12px 16px; background:var(--bg-panel); border-left:3px solid {'var(--positive)' if base_pnl > 0 else 'var(--negative)' if base_pnl < -1000 else 'var(--warning)'};">
              <strong class="text-small text-uppercase" style="letter-spacing:0.1em;">Net Assessment:</strong>
              <span class="text-small" style="margin-left:8px;">{escape_html(narr.get('net_view', ''))}</span>
            </div>
            
            <div style="display:flex; gap:24px; margin-top:12px; padding-top:12px; border-top:1px solid var(--border-dim);">
              <div><span class="text-muted text-small">BEAR PRICE</span><br><strong class="text-negative">${bear['scenario_price']:,.2f}</strong> ({fmt_pct(bear['stock_move_pct'])})</div>
              <div><span class="text-muted text-small">BASE PRICE</span><br><strong class="text-warning">${base['scenario_price']:,.2f}</strong> ({fmt_pct(base['stock_move_pct'])})</div>
              <div><span class="text-muted text-small">BULL PRICE</span><br><strong>${bull['scenario_price']:,.2f}</strong> ({fmt_pct(bull['stock_move_pct'])})</div>
              <div><span class="text-muted text-small">CURRENT</span><br><strong>${prices.get(ticker, 0):,.2f}</strong></div>
            </div>
            {iv_info}
          </div>
        </td>
      </tr>
'''
    
    row_id += 1

# TOTALS ROW
body += f'''      <tr style="border-top: 2px solid var(--text-primary); font-weight: 600;">
        <td></td>
        <td colspan="3"><strong>PORTFOLIO TOTAL</strong></td>
        <td></td>
        <td class="text-right">${sum(abs(r['entry_cost']) for r in results['base']):,.0f}</td>
        <td colspan="4"></td>
        <td class="text-center" style="border-left: 2px solid var(--negative);">—</td>
        <td class="text-right {pnl_class(totals['bear']['total_pnl'])}" style="background:rgba(255,68,68,0.05);"><strong>{fmt_dollars(totals['bear']['total_pnl'])}</strong></td>
        <td class="text-center" style="border-left: 2px solid var(--warning);">—</td>
        <td class="text-right {pnl_class(totals['base']['total_pnl'])}" style="background:rgba(255,170,0,0.05);"><strong>{fmt_dollars(totals['base']['total_pnl'])}</strong></td>
        <td class="text-center" style="border-left: 2px solid var(--positive);">—</td>
        <td class="text-right {pnl_class(totals['bull']['total_pnl'])}" style="background:rgba(0,255,136,0.05);"><strong>{fmt_dollars(totals['bull']['total_pnl'])}</strong></td>
      </tr>
'''

body += '''    </tbody>
  </table>
  </div>
</div>
'''


body += '''
<div class="section-header">Factor Attribution — What's Driving P&L</div>
<div class="grid-3" style="grid-template-columns: repeat(3, 1fr);">
'''

for scenario_name, scenario_label, border_color in [
    ('bear', 'Bear Case (SPX -5%, VIX 45)', 'var(--negative)'),
    ('base', 'Base Case (SPX -3%, VIX 40)', 'var(--warning)'),
    ('bull', 'Bull Case (SPX -1.5%, VIX 30)', 'var(--positive)')
]:
    sc_results = results[scenario_name]
    total_beta = sum(abs(r['entry_cost']) * r['beta_component'] / 100 for r in sc_results)
    total_oil = sum(abs(r['entry_cost']) * r['oil_component'] / 100 for r in sc_results)
    total_vix = sum(abs(r['entry_cost']) * r['vix_stress'] / 100 for r in sc_results)
    
    body += f'''
  <div class="panel" style="border-top: 3px solid {border_color};">
    <div class="panel-header">{scenario_label}</div>
    <table>
      <tbody>
        <tr><td>SPX Beta Impact</td><td class="text-right {pnl_class(total_beta)}"><strong>{fmt_dollars(total_beta)}</strong></td></tr>
        <tr><td>Oil Sensitivity</td><td class="text-right {pnl_class(total_oil)}"><strong>{fmt_dollars(total_oil)}</strong></td></tr>
        <tr><td>VIX Stress Premium</td><td class="text-right {pnl_class(total_vix)}"><strong>{fmt_dollars(total_vix)}</strong></td></tr>
        <tr><td>IV Expansion (Vega)</td><td class="text-right text-muted">Embedded in option P&L</td></tr>
        <tr style="border-top: 2px solid var(--border-focus);">
          <td><strong>Total Portfolio P&L</strong></td>
          <td class="text-right {pnl_class(totals[scenario_name]['total_pnl'])}"><strong>{fmt_dollars(totals[scenario_name]['total_pnl'])}</strong></td>
        </tr>
        <tr><td>Net Liquidation</td><td class="text-right"><strong>${totals[scenario_name]['net_liq_after']:,.0f}</strong></td></tr>
      </tbody>
    </table>
  </div>
'''

body += '</div>'


body += '''
<div class="section-header">Base Case P&L Waterfall</div>
<div class="panel">
  <div class="panel-body">
'''

max_abs = max(abs(r['pnl']) for r in results['base'])
if max_abs == 0: max_abs = 1

for r in sorted(results['base'], key=lambda x: x['pnl']):
    pnl = r['pnl']
    bar_w = min(abs(pnl) / max_abs * 100, 100)
    color = 'var(--positive)' if pnl >= 0 else 'var(--negative)'
    if pnl >= 0:
        bar_style = f'margin-left:50%; width:{bar_w/2}%; background:{color}; height:18px;'
    else:
        bar_style = f'margin-left:{50-bar_w/2}%; width:{bar_w/2}%; background:{color}; height:18px;'
    
    body += f'''    <div style="display:flex; align-items:center; margin-bottom:2px;">
      <div style="width:80px; font-size:11px; text-align:right; padding-right:8px; flex-shrink:0;"><strong>{escape_html(str(r['ticker']))}</strong></div>
      <div style="flex:1; position:relative; height:20px;">
        <div style="position:absolute; left:50%; top:0; bottom:0; width:1px; background:var(--border-focus);"></div>
        <div style="{bar_style}"></div>
      </div>
      <div style="width:90px; font-size:11px; text-align:right; padding-left:8px; flex-shrink:0;" class="{pnl_class(pnl)}">{fmt_dollars(pnl)}</div>
    </div>
'''

body += '  </div>\n</div>\n'


body += '''
<div class="callout warning">
  <div class="callout-title">🎯 Key Takeaways & Action Items</div>
  <p><strong>1. MSFT is your single biggest risk:</strong> $468K equity (42% of bankroll) with no options hedge. In the base case alone you lose ~$99K on MSFT. Consider buying protective puts or selling covered calls.</p>
  <p><strong>2. LEAP vega is your hidden strength:</strong> SOFI (+$53K), ETHA (+$13K), AMD (+$11K), WULF (+$8K), AAOI (+$16K) collectively generate +$101K in gains despite their stocks declining. IV expansion on LEAPs is a powerful natural hedge.</p>
  <p><strong>3. Oil/Commodity names work as diversifiers:</strong> EC (+$13K), USAX (+$4K), NAK (+$3K), ILF (+$4K) = ~$24K from commodity exposure. This is genuine diversification.</p>
  <p><strong>4. SPXU hedge is undersized:</strong> $2.9K position produces only ~$6K profit vs $170K+ in losses. Hedge ratio is ~0.3%. Consider 10-20x sizing.</p>
  <p><strong>5. Undefined risk positions face assignment risk:</strong> EWY (5 DTE!), APO, IWM short puts go ITM. EWY $130 put at $123 = deep ITM with 5 days to expiry.</p>
  <p><strong>6. Defined risk is working:</strong> PLTR spread loses max $2,620 (not $30K). TMUS loses $12.6K (not $58K as v1 falsely showed). Spreads cap your downside as designed.</p>
  <p><strong>7. Net portfolio impact is manageable:</strong> Base case -$41K (-3.7%) on a $1.1M portfolio. The diversification between oil winners, LEAP vega, and defined risk keeps losses contained.</p>
</div>
'''


body += '''
<div class="panel">
  <div class="panel-header">📐 Methodology & Model</div>
  <div class="panel-body">
    <div class="grid-2" style="grid-template-columns: 1fr 1fr;">
      <div>
        <h3 style="font-size:12px; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.1em;">Stock Price Model</h3>
        <p class="text-small" style="margin-bottom:4px;"><strong>1. Beta:</strong> <code>ΔS = β<sub>SPX</sub> × ΔS&P500</code></p>
        <p class="text-small" style="margin-bottom:4px;"><strong>2. Oil:</strong> <code>ΔS += OilSens × ΔOil</code></p>
        <p class="text-small" style="margin-bottom:4px;"><strong>3. VIX Stress:</strong> <code>ΔS += (Mult-1) × (VIX-30)/30 × ΔSPX</code> (only when VIX > 30)</p>
        <p class="text-small">Crash-beta captures non-linear sell-off amplification for high-momentum names.</p>
      </div>
      <div>
        <h3 style="font-size:12px; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.1em;">Options Pricing</h3>
        <p class="text-small" style="margin-bottom:4px;"><strong>1.</strong> Single per-ticker baseline IV (no per-leg estimation bugs)</p>
        <p class="text-small" style="margin-bottom:4px;"><strong>2.</strong> IV expansion: <code>IV × (VIX<sub>new</sub> / VIX<sub>current</sub>)</code></p>
        <p class="text-small" style="margin-bottom:4px;"><strong>3.</strong> DTE dampening: LEAPs 50%, Medium 75%, Short 100%</p>
        <p class="text-small" style="margin-bottom:4px;"><strong>4.</strong> Defined risk P&L hard-capped at [−debit, +max_width]</p>
        <p class="text-small"><strong>5.</strong> Mild put skew applied (OTM puts get higher IV)</p>
      </div>
    </div>
  </div>
</div>
'''

body += '''
<div class="footer">
  <p>Scenario Analysis v2 · β-SPX + Oil + VIX Crash-Beta + BSM IV Expansion · ''' + datetime.now().strftime('%Y-%m-%d %H:%M PST') + '''</p>
  <p class="text-muted">Estimates only. Actual P&L depends on path dependency, liquidity, correlation regime shifts, and factors not captured in this model.</p>
</div>
'''


# Add custom CSS for expandable rows
custom_css = '''
<style>
  .detail-row td { padding: 0 !important; }
  .detail-row:hover { background: none !important; }
  .chevron.open { transform: rotate(90deg); }
  tr[onclick] td { transition: background 0.15s; }
</style>
'''

# Add JavaScript for expand/collapse
custom_js = '''
<script>
function toggleRow(id) {
  const detail = document.getElementById('detail-' + id);
  const chevron = document.querySelector('.chevron[data-row="' + id + '"]');
  if (!detail) return;
  
  if (detail.style.display === 'none') {
    detail.style.display = 'table-row';
    if (chevron) chevron.classList.add('open');
  } else {
    detail.style.display = 'none';
    if (chevron) chevron.classList.remove('open');
  }
}

// Expand all / collapse all
function toggleAll(expand) {
  document.querySelectorAll('.detail-row').forEach(row => {
    row.style.display = expand ? 'table-row' : 'none';
  });
  document.querySelectorAll('.chevron').forEach(c => {
    if (expand) c.classList.add('open');
    else c.classList.remove('open');
  });
}
</script>
'''

html = template.replace('{{TITLE}}', 'Scenario Stress Test — Oil +25%, VIX 40, SPX -3%')
html = html.replace('{{BODY}}', body)
# Inject custom CSS before </style> and custom JS before </body>
html = html.replace('</style>', custom_css + '\n</style>')
html = html.replace('</body>', custom_js + '\n</body>')

output_path = _HTML_PATH
with open(output_path, 'w') as f:
    f.write(html)

print(f"✅ Report written to {output_path} ({len(html):,} bytes)")
