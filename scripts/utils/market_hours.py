#!/usr/bin/env python3
"""
Market Hours Utility

Check if US options markets are open.
Options trade 9:30 AM - 4:00 PM Eastern Time, Monday-Friday.
Does not account for market holidays (simplified).

Usage:
    from utils.market_hours import is_market_open, get_market_status
"""

from datetime import datetime, time
import pytz
from typing import Tuple


EASTERN = pytz.timezone('America/New_York')

MARKET_OPEN = time(9, 30)   # 9:30 AM ET
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET


def get_eastern_now() -> datetime:
    return datetime.now(EASTERN)


def is_market_open(dt: datetime = None) -> bool:
    if dt is None:
        dt = get_eastern_now()
    else:
        if dt.tzinfo is None:
            dt = EASTERN.localize(dt)
        else:
            dt = dt.astimezone(EASTERN)
    
    # Check weekday (0=Monday, 6=Sunday)
    if dt.weekday() >= 5:  # Saturday or Sunday
        return False
    
    current_time = dt.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE


def get_market_status(dt: datetime = None) -> Tuple[bool, str]:
    if dt is None:
        dt = get_eastern_now()
    else:
        if dt.tzinfo is None:
            dt = EASTERN.localize(dt)
        else:
            dt = dt.astimezone(EASTERN)
    
    is_open = is_market_open(dt)
    
    if is_open:
        close_time = datetime.combine(dt.date(), MARKET_CLOSE)
        close_time = EASTERN.localize(close_time)
        mins_to_close = int((close_time - dt).total_seconds() / 60)
        hours = mins_to_close // 60
        mins = mins_to_close % 60
        if hours > 0:
            return True, f"OPEN ({hours}h {mins}m to close)"
        else:
            return True, f"OPEN ({mins}m to close)"
    else:
        weekday = dt.weekday()
        current_time = dt.time()
        
        if weekday >= 5:
            return False, "CLOSED (weekend)"
        elif current_time < MARKET_OPEN:
            mins_to_open = int((datetime.combine(dt.date(), MARKET_OPEN) - datetime.combine(dt.date(), current_time)).total_seconds() / 60)
            hours = mins_to_open // 60
            mins = mins_to_open % 60
            if hours > 0:
                return False, f"CLOSED (pre-market, {hours}h {mins}m to open)"
            else:
                return False, f"CLOSED (pre-market, {mins}m to open)"
        else:
            return False, "CLOSED (after hours)"


def get_last_market_close(dt: datetime = None) -> datetime:
    if dt is None:
        dt = get_eastern_now()
    else:
        if dt.tzinfo is None:
            dt = EASTERN.localize(dt)
        else:
            dt = dt.astimezone(EASTERN)
    
    current_date = dt.date()
    current_time = dt.time()
    weekday = dt.weekday()
    
    if weekday >= 5:
        days_back = weekday - 4  # Saturday=5 -> 1 day, Sunday=6 -> 2 days
        close_date = current_date.replace() - __import__('datetime').timedelta(days=days_back)
    elif current_time < MARKET_OPEN:
        if weekday == 0:  # Monday
            days_back = 3  # Go back to Friday
        else:
            days_back = 1
        close_date = current_date - __import__('datetime').timedelta(days=days_back)
    elif current_time >= MARKET_CLOSE:
        close_date = current_date
    else:
        if weekday == 0:  # Monday
            days_back = 3  # Go back to Friday
        else:
            days_back = 1
        close_date = current_date - __import__('datetime').timedelta(days=days_back)
    
    return EASTERN.localize(datetime.combine(close_date, MARKET_CLOSE))


if __name__ == "__main__":
    is_open, status = get_market_status()
    eastern_now = get_eastern_now()
    
    print(f"Current time (ET): {eastern_now.strftime('%A %Y-%m-%d %H:%M:%S %Z')}")
    print(f"Market status: {status}")
    print(f"is_market_open(): {is_open}")
    
    last_close = get_last_market_close()
    print(f"Last market close: {last_close.strftime('%Y-%m-%d %H:%M %Z')}")
