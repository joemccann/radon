#!/usr/bin/env python3
"""WULF LEAP Strike Comparison — Kelly-sized position analysis."""


def main():
    stock_price = 16.28
    bankroll = 981353
    max_pct = 0.025  # 2.5% hard cap

    strikes = {
        "$15 (Slightly ITM)": {"strike": 15, "mid": 6.08, "bid": 5.90, "ask": 6.25, "oi": 30886},
        "$17 (Slightly OTM)": {"strike": 17, "mid": 5.45, "bid": 5.20, "ask": 5.70, "oi": 6913},
        "$20 (OTM)": {"strike": 20, "mid": 4.53, "bid": 4.20, "ask": 4.85, "oi": 17710},
    }

    print("=== WULF LEAP Strike Comparison ===")
    print(f"Current Stock Price: ${stock_price:.2f}")
    print(f"Bankroll: ${bankroll:,.0f}")
    print(f"Max Position: ${bankroll * max_pct:,.0f} (2.5%)\n")

    # Assume WULF targets based on IV (126.7% HV20 = stock could move 126% in a year)
    # Conservative: +50% ($24.42), Base: +75% ($28.49), Aggressive: +100% ($32.56)

    targets = {
        "Conservative (+50%)": stock_price * 1.50,
        "Base Case (+75%)": stock_price * 1.75,
        "Aggressive (+100%)": stock_price * 2.00,
    }

    print("=" * 90)
    print(f"{'Strike':<22} {'Premium':<10} {'Spread%':<10} {'OI':<10} {'Breakeven':<12} {'% to BE':<10}")
    print("=" * 90)

    for name, data in strikes.items():
        premium = data["mid"]
        spread_pct = (data["ask"] - data["bid"]) / data["mid"] * 100
        breakeven = data["strike"] + premium
        pct_to_be = (breakeven - stock_price) / stock_price * 100
        print(f"{name:<22} ${premium:<9.2f} {spread_pct:<9.1f}% {data['oi']:<10} ${breakeven:<11.2f} {pct_to_be:<9.1f}%")

    print("\n" + "=" * 90)
    print("Return Scenarios (% gain on premium):")
    print("=" * 90)
    print(f"{'Strike':<22}", end="")
    for scenario in targets:
        print(f" {scenario:<20}", end="")
    print()

    for name, data in strikes.items():
        premium = data["mid"]
        print(f"{name:<22}", end="")
        for scenario, target_price in targets.items():
            if target_price > data["strike"]:
                intrinsic = target_price - data["strike"]
                pct_return = (intrinsic - premium) / premium * 100
            else:
                pct_return = -100
            print(f" {pct_return:>+19.0f}%", end="")
        print()

    print("\n" + "=" * 90)
    print("RECOMMENDATION:")
    print("=" * 90)

    print("""
 BEST TRADE: $15 CALL @ $6.08 mid (limit at $6.10-6.15)

 Rationale:
 - BEST liquidity   30,886 OI, tightest spread (5.8%)
 - Lowest breakeven   $21.08 vs $22.45 for $17 strike
 - Higher delta   more participation in early moves
 - Trade execution will be cleaner with tighter bid-ask

 The $17 strike has slightly more "convexity" (all time value) but the
 $15's superior liquidity and lower breakeven make it the better risk-adjusted
 choice.

 ALTERNATIVE: $17 CALL @ $5.45 if you want more leverage for same capital
 """)

    # Position sizing
    max_position = bankroll * max_pct
    print(f"\n{'CONTRACT DETAILS':<30}")
    print("-" * 50)
    print(f"{'Max Position Size:':<30} ${max_position:,.0f}")
    print(f"{'$15 Strike @ $6.10:':<30} {int(max_position / 610)} contracts = ${int(max_position / 610) * 610:,}")
    print(f"{'$17 Strike @ $5.45:':<30} {int(max_position / 545)} contracts = ${int(max_position / 545) * 545:,}")


if __name__ == "__main__":
    main()
