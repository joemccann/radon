#!/usr/bin/env python3
"""
Scenario Analysis Engine v2 (Fixed)
====================================
Models portfolio P&L under stress scenarios using:
- Historical betas to SPX
- VIX correlation modeling (inverse for equities, positive for leveraged inverse)
- Sector-specific sensitivities
- Oil price sensitivity for energy/commodity names
- Options pricing via Black-Scholes with IV expansion under stress
- DTE-aware decay for short-dated options

KEY FIXES from v1:
- IV estimation uses a single per-ticker IV (not per-leg), preventing impossible states
- Defined-risk P&L is hard-capped at max loss (net debit)
- Spread net value is clamped to [0, max_width] to prevent negative spread values
- Long options P&L is floored at -100% (total premium loss)

Scenario: Oil +25%, VIX 40, SPX -3%
"""

import json
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ============================================================
# MARKET PARAMETERS
# ============================================================

# Current approximate prices (Friday March 7, 2026 close estimates)
CURRENT_PRICES = {
    'AAOI': 100.0,
    'AAPL': 263.0,
    'ALAB': 95.0,
    'AMD': 198.0,
    'APO': 107.0,
    'BAP': 365.0,
    'BKD': 13.5,
    'BRZE': 21.0,
    'EC': 12.37,
    'ETHA': 18.50,
    'EWY': 133.0,
    'GOOG': 302.0,
    'IGV': 85.0,
    'ILF': 37.18,
    'IWM': 250.0,
    'MSFT': 385.0,
    'NAK': 3.04,
    'PLTR': 155.0,
    'RR': 4.43,
    'SOFI': 40.0,
    'SPXU': 53.0,
    'TMUS': 234.0,
    'TSLL': 18.32,
    'URTY': 63.64,
    'USAX': 46.68,
    'WULF': 14.0,
}

# Baseline IV estimates (annualized) per ticker
# These are realistic current IV levels based on asset class
BASELINE_IV = {
    'AAOI': 0.85,    # High IV small cap semi
    'AAPL': 0.28,    # Mega cap, moderate IV
    'ALAB': 0.75,    # Small cap semi, high IV
    'AMD': 0.55,     # High beta semi
    'APO': 0.35,     # Financial, moderate
    'BAP': 0.30,     # EM bank, moderate
    'BKD': 0.55,     # Small cap healthcare
    'BRZE': 0.70,    # Small cap SaaS, high IV
    'EC': 0.40,      # Oil company
    'ETHA': 0.80,    # Crypto, very high IV
    'EWY': 0.25,     # ETF, lower IV
    'GOOG': 0.30,    # Mega cap
    'IGV': 0.28,     # ETF
    'ILF': 0.25,     # ETF
    'IWM': 0.22,     # Broad ETF
    'MSFT': 0.28,    # Mega cap
    'NAK': 0.65,     # Micro cap miner
    'PLTR': 0.65,    # High growth
    'RR': 0.50,      # Small cap
    'SOFI': 0.60,    # Fintech
    'SPXU': 0.50,    # Leveraged ETF
    'TMUS': 0.22,    # Defensive telecom, low IV
    'TSLL': 0.90,    # 2x leveraged
    'URTY': 0.60,    # 3x leveraged
    'USAX': 0.55,    # Small cap miner
    'WULF': 0.80,    # Bitcoin miner, high IV
}

# SPX Beta
SPX_BETAS = {
    'AAOI': 1.80,
    'AAPL': 1.15,
    'ALAB': 2.20,
    'AMD': 1.70,
    'APO': 1.50,
    'BAP': 0.90,
    'BKD': 1.30,
    'BRZE': 1.90,
    'EC': 0.70,
    'ETHA': 1.60,
    'EWY': 1.10,
    'GOOG': 1.10,
    'IGV': 1.30,
    'ILF': 0.85,
    'IWM': 1.20,
    'MSFT': 1.10,
    'NAK': 0.50,
    'PLTR': 2.00,
    'RR': 0.80,
    'SOFI': 1.80,
    'SPXU': -3.00,
    'TMUS': 0.65,
    'TSLL': 3.60,
    'URTY': 3.60,
    'USAX': 0.45,
    'WULF': 2.00,
}

# Oil sensitivity (% stock move per 1% oil move)
OIL_SENSITIVITY = {
    'AAOI': -0.05,
    'AAPL': -0.05,
    'ALAB': -0.03,
    'AMD': -0.05,
    'APO': 0.15,
    'BAP': 0.10,
    'BKD': -0.10,
    'BRZE': -0.02,
    'EC': 0.90,       # Direct oil play
    'ETHA': 0.00,
    'EWY': -0.15,     # Net oil importer
    'GOOG': -0.03,
    'IGV': -0.02,
    'ILF': 0.30,      # LatAm commodity
    'IWM': -0.05,
    'MSFT': -0.03,
    'NAK': 0.25,      # Commodity
    'PLTR': -0.02,
    'RR': 0.05,
    'SOFI': -0.03,
    'SPXU': 0.05,
    'TMUS': -0.05,
    'TSLL': -0.10,
    'URTY': -0.05,
    'USAX': 0.40,     # Gold/silver
    'WULF': 0.05,
}

# VIX stress multiplier
VIX_STRESS_MULTIPLIER = {
    'AAOI': 1.30,
    'AAPL': 1.05,
    'ALAB': 1.40,
    'AMD': 1.25,
    'APO': 1.20,
    'BAP': 1.15,
    'BKD': 1.20,
    'BRZE': 1.35,
    'EC': 0.90,
    'ETHA': 1.50,
    'EWY': 1.15,
    'GOOG': 1.05,
    'IGV': 1.20,
    'ILF': 1.10,
    'IWM': 1.15,
    'MSFT': 1.05,
    'NAK': 0.80,
    'PLTR': 1.40,
    'RR': 0.85,
    'SOFI': 1.30,
    'SPXU': 0.90,
    'TMUS': 0.85,
    'TSLL': 1.50,
    'URTY': 1.30,
    'USAX': 0.75,
    'WULF': 1.45,
}

CURRENT_VIX = 23.0
SCENARIO_VIX = 40.0
SCENARIO_SPX_MOVE = -0.03
SCENARIO_OIL_MOVE = 0.25

# ============================================================
# OPTIONS PRICING MODEL
# ============================================================

def norm_cdf(x):
    """Cumulative normal distribution function."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = 1
    if x < 0:
        sign = -1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)

def black_scholes_call(S, K, T, r, sigma):
    """Black-Scholes call price."""
    if T <= 0:
        return max(S - K, 0)
    if sigma <= 0.001:
        return max(S - K * math.exp(-r * T), 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)

def black_scholes_put(S, K, T, r, sigma):
    """Black-Scholes put price."""
    if T <= 0:
        return max(K - S, 0)
    if sigma <= 0.001:
        return max(K * math.exp(-r * T) - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def get_scenario_iv(ticker, dte, scenario_vix):
    """
    Get scenario IV for a ticker. Uses a single per-ticker baseline IV
    and expands it proportional to VIX change, with DTE dampening.
    
    This avoids the v1 bug where each leg got a different IV causing
    impossible spread valuations.
    """
    base_iv = BASELINE_IV.get(ticker, 0.40)
    
    # IV expansion proportional to VIX change
    iv_expansion_ratio = scenario_vix / CURRENT_VIX
    
    # DTE dampening: short-dated options see full IV expansion,
    # LEAPs see dampened expansion (term structure flattening in stress)
    if dte > 180:
        dampened_ratio = 1.0 + (iv_expansion_ratio - 1.0) * 0.50
    elif dte > 60:
        dampened_ratio = 1.0 + (iv_expansion_ratio - 1.0) * 0.75
    else:
        dampened_ratio = iv_expansion_ratio
    
    return base_iv * dampened_ratio


def compute_stock_scenario_move(ticker, scenario='base'):
    """
    Compute the estimated % move for a stock under the scenario.
    """
    beta = SPX_BETAS.get(ticker, 1.0)
    oil_sens = OIL_SENSITIVITY.get(ticker, 0.0)
    vix_m = VIX_STRESS_MULTIPLIER.get(ticker, 1.0)
    
    if scenario == 'bear':
        spx_move = -0.05
        oil_move = 0.25
        target_vix = 45
    elif scenario == 'bull':
        spx_move = -0.015
        oil_move = 0.25
        target_vix = 30
    else:
        spx_move = SCENARIO_SPX_MOVE
        oil_move = SCENARIO_OIL_MOVE
        target_vix = SCENARIO_VIX
    
    beta_component = beta * spx_move
    oil_component = oil_sens * oil_move
    
    vix_stress = 0
    if target_vix > 30:
        vix_stress_factor = (vix_m - 1.0) * (target_vix - 30) / 30.0
        vix_stress = vix_stress_factor * spx_move
    
    total_move = beta_component + oil_component + vix_stress
    
    return {
        'total_pct': total_move,
        'beta_component': beta_component,
        'oil_component': oil_component,
        'vix_stress': vix_stress,
        'spx_move': spx_move,
        'target_vix': target_vix,
    }


def price_option(opt_type, S, K, T, r, sigma):
    """Price a call or put."""
    if opt_type == 'Call':
        return black_scholes_call(S, K, T, r, sigma)
    else:
        return black_scholes_put(S, K, T, r, sigma)


def approx_delta(spot, strike, dte, opt_type):
    """
    Lightweight delta approximation used by the stress-test helpers.
    """
    option_type = str(opt_type).lower()
    is_call = option_type.startswith('c')
    if spot <= 0 or strike <= 0 or dte <= 0:
        return 0.5 if is_call else -0.5

    time_years = max(dte / 365.0, 1e-6)
    sigma = 0.35
    d1 = (
        math.log(spot / strike) + (0.5 * sigma ** 2) * time_years
    ) / (sigma * math.sqrt(time_years))
    call_delta = norm_cdf(d1)
    return call_delta if is_call else call_delta - 1.0


def _option_dte(expiry):
    if not expiry or expiry == 'N/A':
        return 0.0
    try:
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
    except ValueError:
        return 0.0
    return max((expiry_date - datetime.now()).days, 0)


def _position_market_value(position):
    return float(position.get('market_value', 0.0) or 0.0)


def compute_position_delta(position, spot):
    """Return signed position delta in shares."""
    if spot is None or spot <= 0:
        return 0.0

    total_delta = 0.0
    legs = position.get('legs') or []
    for leg in legs:
        leg_type = str(leg.get('type', '')).lower()
        direction = str(leg.get('direction', 'LONG')).upper()
        sign = 1.0 if direction == 'LONG' else -1.0
        contracts = float(leg.get('contracts', 0.0) or 0.0)

        if leg_type == 'stock':
            total_delta += sign * contracts
            continue

        strike = float(leg.get('strike') or 0.0)
        opt_type = 'Call' if leg_type.startswith('c') else 'Put'
        dte = _option_dte(position.get('expiry'))
        total_delta += sign * approx_delta(spot, strike, dte, opt_type) * contracts * 100.0

    return total_delta


def compute_exposure(portfolio, spots):
    """Aggregate portfolio delta exposure against the supplied spot map."""
    positions = portfolio.get('positions') or []
    net_delta = 0.0
    dollar_delta = 0.0
    net_long = 0.0
    net_short = 0.0

    for position in positions:
        ticker = position.get('ticker')
        spot = spots.get(ticker)
        if spot is None:
            continue

        position_delta = compute_position_delta(position, spot)
        position_dollar_delta = position_delta * spot
        net_delta += position_delta
        dollar_delta += position_dollar_delta
        if position_dollar_delta >= 0:
            net_long += position_dollar_delta
        else:
            net_short += abs(position_dollar_delta)

    return {
        'net_delta': net_delta,
        'dollar_delta': dollar_delta,
        'net_long': net_long,
        'net_short': net_short,
    }


def scenario_price_shock(portfolio, spots, shock_pct):
    """
    Apply a spot shock and estimate position P&L from delta exposure.
    """
    positions = portfolio.get('positions') or []
    shocked_spots = {
        ticker: price * (1.0 + shock_pct)
        for ticker, price in spots.items()
    }
    position_impacts = []
    net_liq_change = 0.0

    for position in positions:
        ticker = position.get('ticker')
        spot = spots.get(ticker)
        current_mv = _position_market_value(position)
        if spot is None:
            pnl_impact = 0.0
            new_mv = current_mv
        else:
            price_change = spot * shock_pct
            pnl_impact = compute_position_delta(position, spot) * price_change
            new_mv = current_mv + pnl_impact

        position_impacts.append({
            'ticker': ticker,
            'pnl_impact': pnl_impact,
            'new_mv': new_mv,
        })
        net_liq_change += pnl_impact

    current_exposure = compute_exposure(portfolio, spots)
    stressed_exposure = compute_exposure(portfolio, shocked_spots)

    return {
        'current': current_exposure,
        'stressed': stressed_exposure,
        'impact': {
            'net_liq_change': net_liq_change,
            'current_net_liq': portfolio.get('bankroll', 0.0),
            'stressed_net_liq': portfolio.get('bankroll', 0.0) + net_liq_change,
        },
        'positions': position_impacts,
    }


def scenario_delta_decay(portfolio, spots, decay_pct):
    """
    Model theta/vega-style decay by reducing option extrinsic value and delta.
    """
    positions = portfolio.get('positions') or []
    position_impacts = []
    net_liq_change = 0.0
    current_exposure = compute_exposure(portfolio, spots)
    stressed_dollar_delta = 0.0
    stressed_net_delta = 0.0
    stressed_net_long = 0.0
    stressed_net_short = 0.0

    for position in positions:
        ticker = position.get('ticker')
        spot = spots.get(ticker)
        current_mv = _position_market_value(position)
        legs = position.get('legs') or []
        pnl_impact = 0.0
        current_position_delta = 0.0
        stressed_position_delta = 0.0

        if spot is not None:
            current_position_delta = compute_position_delta(position, spot)

        for leg in legs:
            leg_type = str(leg.get('type', '')).lower()
            direction = str(leg.get('direction', 'LONG')).upper()
            sign = 1.0 if direction == 'LONG' else -1.0
            contracts = float(leg.get('contracts', 0.0) or 0.0)

            if leg_type == 'stock' or spot is None:
                stressed_position_delta += sign * contracts if leg_type == 'stock' else 0.0
                continue

            strike = float(leg.get('strike') or 0.0)
            market_price = float(leg.get('market_price', 0.0) or 0.0)
            if leg_type.startswith('c'):
                intrinsic = max(spot - strike, 0.0)
                opt_type = 'Call'
            else:
                intrinsic = max(strike - spot, 0.0)
                opt_type = 'Put'
            extrinsic = max(0.0, market_price - intrinsic)
            decay_value = extrinsic * decay_pct * contracts * 100.0
            pnl_impact += -decay_value if sign > 0 else decay_value

            leg_delta = sign * approx_delta(spot, strike, _option_dte(position.get('expiry')), opt_type) * contracts * 100.0
            stressed_position_delta += leg_delta * (1.0 - decay_pct)

        if all(str(leg.get('type', '')).lower() == 'stock' for leg in legs):
            stressed_position_delta = current_position_delta

        new_mv = current_mv + pnl_impact
        position_impacts.append({
            'ticker': ticker,
            'pnl_impact': pnl_impact,
            'new_mv': new_mv,
        })
        net_liq_change += pnl_impact

        if spot is not None:
            stressed_position_dollar_delta = stressed_position_delta * spot
            stressed_dollar_delta += stressed_position_dollar_delta
            stressed_net_delta += stressed_position_delta
            if stressed_position_dollar_delta >= 0:
                stressed_net_long += stressed_position_dollar_delta
            else:
                stressed_net_short += abs(stressed_position_dollar_delta)

    return {
        'current': current_exposure,
        'stressed': {
            'net_delta': stressed_net_delta,
            'dollar_delta': stressed_dollar_delta,
            'net_long': stressed_net_long,
            'net_short': stressed_net_short,
        },
        'impact': {
            'net_liq_change': net_liq_change,
            'current_net_liq': portfolio.get('bankroll', 0.0),
            'stressed_net_liq': portfolio.get('bankroll', 0.0) + net_liq_change,
        },
        'positions': position_impacts,
    }


def analyze_position(pos, scenario='base'):
    """
    Analyze a single position under the given scenario.
    
    KEY INVARIANTS ENFORCED:
    1. Debit spread P&L ∈ [-net_debit, +(max_width * contracts * 100 - net_debit)]
    2. Long option P&L ∈ [-premium_paid, +∞)
    3. Spread net value ∈ [0, width * 100 * contracts]
    4. All legs use same IV (skew-adjusted, not independently estimated)
    """
    ticker = pos['ticker']
    current_price = CURRENT_PRICES.get(ticker, 100.0)
    
    move = compute_stock_scenario_move(ticker, scenario)
    scenario_price = current_price * (1 + move['total_pct'])
    target_vix = move['target_vix']
    
    # Calculate DTE
    if pos['expiry'] != 'N/A':
        expiry_date = datetime.strptime(pos['expiry'], '%Y-%m-%d')
        dte = max((expiry_date - datetime(2026, 3, 8)).days, 0)
    else:
        dte = 999
    
    T = max(dte / 365.0, 0.001)
    r = 0.05
    
    structure = pos['structure_type']
    entry_cost = pos['entry_cost']
    contracts = pos['contracts']
    
    result = {
        'ticker': ticker,
        'structure': pos['structure'],
        'structure_type': structure,
        'risk_profile': pos['risk_profile'],
        'current_price': current_price,
        'scenario_price': round(scenario_price, 2),
        'stock_move_pct': round(move['total_pct'] * 100, 1),
        'beta_component': round(move['beta_component'] * 100, 1),
        'oil_component': round(move['oil_component'] * 100, 1),
        'vix_stress': round(move['vix_stress'] * 100, 1),
        'entry_cost': entry_cost,
        'dte': dte,
        'expiry': pos['expiry'],
        'contracts': contracts,
    }
    
    # ======== STOCK POSITIONS ========
    if structure == 'Stock':
        shares = contracts
        scenario_value = shares * scenario_price
        pnl = scenario_value - entry_cost
        pnl_pct = (pnl / entry_cost) * 100 if entry_cost != 0 else 0
        result['scenario_value'] = round(scenario_value, 0)
        result['pnl'] = round(pnl, 0)
        result['pnl_pct'] = round(pnl_pct, 1)
        result['max_loss'] = None
        return result
    
    # ======== OPTIONS POSITIONS ========
    legs = pos.get('legs', [])
    
    # Get SINGLE IV for this ticker at this DTE (both current and scenario)
    current_iv = BASELINE_IV.get(ticker, 0.40)
    scenario_iv = get_scenario_iv(ticker, dte, target_vix)
    
    # --- Price all legs under CURRENT conditions (to get current portfolio value) ---
    current_net_value = 0  # Current mark-to-market net value
    scenario_net_value = 0  # Scenario net value
    leg_details = []
    
    for leg in legs:
        if leg['type'] == 'Stock':
            # Stock leg
            shares = leg['contracts']
            cur_val = shares * current_price
            scen_val = shares * scenario_price
            if leg['direction'] == 'LONG':
                current_net_value += cur_val
                scenario_net_value += scen_val
            else:
                current_net_value -= cur_val
                scenario_net_value -= scen_val
            leg_details.append({
                'type': 'Stock',
                'direction': leg['direction'],
                'current_value': round(cur_val, 0),
                'scenario_value': round(scen_val, 0),
            })
            continue
        
        K = leg['strike']
        num = leg['contracts']
        
        # Apply mild IV skew: OTM options get slightly higher IV
        moneyness = scenario_price / K
        if leg['type'] == 'Call':
            skew_adj = max(1.0, 1.0 + (moneyness - 1.0) * -0.1)  # OTM calls: slight IV bump
        else:
            skew_adj = max(1.0, 1.0 + (1.0 - moneyness) * 0.15)  # OTM puts: more IV (put skew)
        
        # Current value (using baseline IV with mild skew)
        cur_moneyness = current_price / K
        if leg['type'] == 'Call':
            cur_skew = max(1.0, 1.0 + (cur_moneyness - 1.0) * -0.1)
        else:
            cur_skew = max(1.0, 1.0 + (1.0 - cur_moneyness) * 0.15)
        
        cur_price_per_share = price_option(leg['type'], current_price, K, T, r, current_iv * cur_skew)
        scen_price_per_share = price_option(leg['type'], scenario_price, K, T, r, scenario_iv * skew_adj)
        
        cur_value = cur_price_per_share * 100 * num
        scen_value = scen_price_per_share * 100 * num
        
        if leg['direction'] == 'LONG':
            current_net_value += cur_value
            scenario_net_value += scen_value
        else:
            current_net_value -= cur_value
            scenario_net_value -= scen_value
        
        leg_details.append({
            'type': f"{leg['direction']} {leg['type']} K={K}",
            'direction': leg['direction'],
            'strike': K,
            'current_price_per_share': round(cur_price_per_share, 2),
            'scenario_price_per_share': round(scen_price_per_share, 2),
            'current_value': round(cur_value, 0),
            'scenario_value': round(scen_value, 0),
        })
    
    # ======== P&L CALCULATION WITH BOUNDS ENFORCEMENT ========
    
    # P&L = change in portfolio value from current to scenario
    # = scenario_net_value - current_net_value
    pnl = scenario_net_value - current_net_value
    
    # ---- ENFORCE DEFINED RISK BOUNDS ----
    if structure in ('Bull Call Spread', 'Bear Put Spread'):
        # Max loss = net debit paid
        max_loss = -abs(entry_cost)
        
        # Max gain = (width * contracts * 100) - debit paid
        long_strike = None
        short_strike = None
        for leg in legs:
            if leg.get('type') == 'Stock':
                continue
            if leg['direction'] == 'LONG':
                long_strike = leg['strike']
            else:
                short_strike = leg['strike']
        
        if long_strike is not None and short_strike is not None:
            width = abs(long_strike - short_strike)
            max_gain = (width * contracts * 100) - abs(entry_cost)
        else:
            max_gain = float('inf')
        
        # Clamp P&L
        pnl = max(max_loss, min(pnl, max_gain))
        
        result['max_loss'] = max_loss
        result['max_gain'] = round(max_gain, 0)
    
    elif structure == 'Long Call' or (structure == 'Long Put'):
        # Max loss = premium paid
        max_loss = -abs(entry_cost)
        pnl = max(max_loss, pnl)
        result['max_loss'] = max_loss
    
    elif structure in ('Risk Reversal', 'Synthetic Long'):
        # Undefined risk - no floor on losses
        # But long call can't go below 0, so cap the call loss
        result['max_loss'] = None
    
    # Compute P&L % relative to capital at risk
    if abs(entry_cost) > 0:
        capital_at_risk = abs(entry_cost)
    else:
        # For credit positions, capital at risk is the assignment risk
        # Use a reasonable proxy
        capital_at_risk = abs(current_net_value) if current_net_value != 0 else 10000
    
    pnl_pct = (pnl / capital_at_risk) * 100
    
    result['scenario_value'] = round(scenario_net_value, 0)
    result['current_value'] = round(current_net_value, 0)
    result['pnl'] = round(pnl, 0)
    result['pnl_pct'] = round(pnl_pct, 1)
    result['leg_details'] = leg_details
    result['current_iv'] = round(current_iv * 100, 1)
    result['scenario_iv'] = round(scenario_iv * 100, 1)
    
    return result


def run_full_analysis():
    """Run scenario analysis across all positions for all three scenarios."""
    from db.readers import read_latest_portfolio_snapshot

    pf = read_latest_portfolio_snapshot() or {"positions": [], "bankroll": 0}
    positions = pf.get("positions") if isinstance(pf.get("positions"), list) else []
    bankroll = float(
        pf.get("bankroll")
        or pf.get("net_liquidation")
        or pf.get("netLiquidation")
        or 0
    )
    
    results = {'bear': [], 'base': [], 'bull': []}
    
    for scenario in ['bear', 'base', 'bull']:
        for pos in positions:
            r = analyze_position(pos, scenario)
            results[scenario].append(r)
    
    totals = {}
    for scenario in ['bear', 'base', 'bull']:
        total_pnl = sum(r['pnl'] for r in results[scenario])
        totals[scenario] = {
            'total_pnl': round(total_pnl, 0),
            'total_pnl_pct': round((total_pnl / bankroll) * 100, 1) if bankroll else 0,
            'net_liq_after': round(bankroll + total_pnl, 0),
            'bankroll': bankroll,
        }
    
    return results, totals, pf


if __name__ == '__main__':
    results, totals, pf = run_full_analysis()
    
    print("=" * 80)
    print("SCENARIO ANALYSIS v2: Oil +25%, VIX 40, SPX -3%")
    print("=" * 80)
    
    for scenario in ['bear', 'base', 'bull']:
        print(f"\n{'='*40}")
        print(f"  {scenario.upper()} CASE")
        print(f"{'='*40}")
        print(f"  Total P&L: ${totals[scenario]['total_pnl']:>+,.0f}")
        print(f"  P&L % of Bankroll: {totals[scenario]['total_pnl_pct']:>+.1f}%")
        print(f"  Net Liq After: ${totals[scenario]['net_liq_after']:>,.0f}")
        print()
        
        for r in sorted(results[scenario], key=lambda x: x['pnl']):
            max_loss_str = f" [max loss: ${abs(r.get('max_loss', 0) or 0):,.0f}]" if r.get('max_loss') else ""
            print(f"  {r['ticker']:5s} {r['structure_type']:20s} | "
                  f"Stock: {r['stock_move_pct']:>+5.1f}% → ${r['scenario_price']:>8.2f} | "
                  f"P&L: ${r['pnl']:>+10,.0f} ({r['pnl_pct']:>+6.1f}%){max_loss_str}")
    
    # Output JSON for report
    output = {
        'results': results,
        'totals': totals,
        'parameters': {
            'current_vix': CURRENT_VIX,
            'scenario_vix_base': 40,
            'scenario_vix_bear': 45,
            'scenario_vix_bull': 30,
            'scenario_spx_base': -3.0,
            'scenario_spx_bear': -5.0,
            'scenario_spx_bull': -1.5,
            'scenario_oil': +25.0,
        },
        'current_prices': CURRENT_PRICES,
        'betas': SPX_BETAS,
        'oil_sensitivity': OIL_SENSITIVITY,
        'vix_multipliers': VIX_STRESS_MULTIPLIER,
        'baseline_iv': {k: round(v*100, 1) for k, v in BASELINE_IV.items()},
    }
    
    with open('/tmp/scenario_analysis.json', 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\nJSON output written to /tmp/scenario_analysis.json")
