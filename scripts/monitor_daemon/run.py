#!/usr/bin/env python3
"""
Monitor Daemon Runner

Main entry point for the extensible monitoring service.

Usage:
  # Run once (for testing or manual execution)
  python3 -m monitor_daemon.run --once
  
  # Run as daemon (continuous loop)
  python3 -m monitor_daemon.run --daemon
  
  # Status check
  python3 -m monitor_daemon.run --status
  
  # List handlers
  python3 -m monitor_daemon.run --list-handlers
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers import FillMonitorHandler, ExitOrdersHandler

# Paths
PROJECT_DIR = Path(__file__).parent.parent.parent
STATE_FILE = PROJECT_DIR / "data" / "daemon_state.json"
LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "monitor-daemon.log"

# Configure logging
def setup_logging(verbose: bool = False):
    LOG_DIR.mkdir(exist_ok=True)
    
    level = logging.DEBUG if verbose else logging.INFO
    
    # File handler
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    ))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s'
    ))
    
    # Root logger
    logging.root.setLevel(logging.DEBUG)
    logging.root.addHandler(file_handler)
    logging.root.addHandler(console_handler)


def create_daemon() -> MonitorDaemon:
    """Create and configure the daemon with all handlers."""
    daemon = MonitorDaemon(
        state_file=STATE_FILE,
        respect_market_hours=True,
        loop_interval=30  # Check every 30 seconds
    )
    
    # Register handlers
    daemon.register(FillMonitorHandler(
        ib_port=4001,
        client_id=70,
        send_notifications=True
    ))
    
    daemon.register(ExitOrdersHandler(
        ib_port=4001,
        client_id=71,
        max_gap_pct=0.40
    ))
    
    # Load previous state
    daemon.load_state()
    
    return daemon


def run_once(daemon: MonitorDaemon) -> dict:
    """Run all handlers once."""
    print(f"\n{'='*60}")
    print(f"Monitor Daemon - Single Run")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    results = daemon.run_once()
    
    for name, result in results.items():
        status = result.get("status", "unknown")
        icon = "✓" if status == "ok" else "✗"
        elapsed = result.get("elapsed_ms", 0)
        print(f"{icon} {name}: {status} ({elapsed}ms)")
        
        if status == "ok" and result.get("data"):
            data = result["data"]
            # Handler-specific summaries
            if name == "fill_monitor":
                print(f"   Orders: {len(data.get('orders', []))}")
                print(f"   New: {data.get('new_orders', 0)}")
                print(f"   Partial fills: {data.get('partial_fills', 0)}")
                print(f"   Complete fills: {data.get('complete_fills', 0)}")
            elif name == "exit_orders":
                print(f"   Checked: {data.get('orders_checked', 0)}")
                print(f"   Placed: {data.get('orders_placed', 0)}")
                print(f"   Skipped: {data.get('orders_skipped', 0)}")
    
    return results


def show_status(daemon: MonitorDaemon):
    """Show daemon status."""
    status = daemon.status()
    
    print(f"\n{'='*60}")
    print("Monitor Daemon Status")
    print(f"{'='*60}\n")
    
    print(f"Market Hours: {status['market_hours']}")
    print(f"Running: {status['running']}")
    print()
    
    print("Handlers:")
    print("-" * 60)
    for h in status["handlers"]:
        due_icon = "🔔" if h["is_due"] else "⏳"
        enabled_icon = "✓" if h["enabled"] else "✗"
        last = h["last_run"] or "Never"
        print(f"  {enabled_icon} {h['name']}")
        print(f"      Interval: {h['interval']}s")
        print(f"      Last run: {last}")
        print(f"      Due: {due_icon}")
        print()


def list_handlers():
    """List available handlers."""
    print("\nAvailable Handlers:")
    print("-" * 40)
    print("  fill_monitor    - Monitor orders for fills (60s)")
    print("  exit_orders     - Place pending exit orders (300s)")
    print()
    print("Add new handlers by:")
    print("  1. Create scripts/monitor_daemon/handlers/my_handler.py")
    print("  2. Inherit from BaseHandler")
    print("  3. Implement execute() method")
    print("  4. Register in run.py create_daemon()")


def main():
    parser = argparse.ArgumentParser(
        description="Extensible monitoring daemon for Convex Scavenger"
    )
    
    parser.add_argument("--once", action="store_true",
        help="Run all handlers once and exit")
    parser.add_argument("--daemon", action="store_true",
        help="Run continuously as daemon")
    parser.add_argument("--status", action="store_true",
        help="Show daemon status")
    parser.add_argument("--list-handlers", action="store_true",
        help="List available handlers")
    parser.add_argument("--verbose", "-v", action="store_true",
        help="Verbose logging")
    parser.add_argument("--ignore-market-hours", action="store_true",
        help="Run even outside market hours")
    
    args = parser.parse_args()
    
    # Default to status if no action specified
    if not any([args.once, args.daemon, args.status, args.list_handlers]):
        args.status = True
    
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    if args.list_handlers:
        list_handlers()
        return
    
    # Create daemon
    daemon = create_daemon()
    
    if args.ignore_market_hours:
        daemon.respect_market_hours = False
    
    if args.status:
        show_status(daemon)
    elif args.once:
        logger.info("Running single pass")
        results = run_once(daemon)
        daemon.save_state()
        print(f"\n✓ Complete. State saved to {STATE_FILE}")
    elif args.daemon:
        logger.info("Starting daemon loop")
        print(f"Monitor daemon starting...")
        print(f"Log file: {LOG_FILE}")
        print(f"State file: {STATE_FILE}")
        print(f"Press Ctrl+C to stop\n")
        try:
            daemon.run_loop()
        except KeyboardInterrupt:
            pass
        finally:
            daemon.save_state()
            print("\n✓ Daemon stopped. State saved.")


if __name__ == "__main__":
    main()
