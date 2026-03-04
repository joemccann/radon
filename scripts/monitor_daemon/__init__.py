"""
Monitor Daemon - Extensible background monitoring service.

Provides a plugin-based architecture for monitoring tasks like:
- Order fill detection
- Exit order placement
- Position alerts
- etc.

Usage:
    from monitor_daemon import MonitorDaemon
    from monitor_daemon.handlers import FillMonitorHandler, ExitOrdersHandler
    
    daemon = MonitorDaemon()
    daemon.register(FillMonitorHandler())
    daemon.register(ExitOrdersHandler())
    daemon.run_once()  # Single pass
    daemon.run_loop()  # Continuous
"""

from .daemon import MonitorDaemon

__all__ = ['MonitorDaemon']
