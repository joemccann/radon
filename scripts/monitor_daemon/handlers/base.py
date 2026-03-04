#!/usr/bin/env python3
"""
Base Handler - Abstract base class for all monitor handlers.

Each handler must implement:
- name: Unique identifier
- interval_seconds: How often to run
- execute(): The actual monitoring logic
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseHandler(ABC):
    """Abstract base class for monitor daemon handlers."""
    
    # Subclasses must define these
    name: str = "base"
    interval_seconds: int = 60
    
    def __init__(self):
        self.last_run: Optional[datetime] = None
        self._enabled: bool = True
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
    
    def is_due(self) -> bool:
        """Check if this handler should run based on its interval."""
        if not self._enabled:
            return False
        
        if self.last_run is None:
            return True
        
        elapsed = datetime.now() - self.last_run
        return elapsed >= timedelta(seconds=self.interval_seconds)
    
    def run(self) -> Dict[str, Any]:
        """
        Execute the handler and wrap result with metadata.
        
        Returns:
            Dict with status, timestamp, and data from execute()
        """
        start_time = datetime.now()
        
        try:
            result = self.execute()
            self.last_run = datetime.now()
            
            elapsed_ms = (self.last_run - start_time).total_seconds() * 1000
            
            return {
                "status": "ok",
                "timestamp": self.last_run.isoformat(),
                "elapsed_ms": round(elapsed_ms, 2),
                "data": result
            }
        except Exception as e:
            logger.exception(f"Handler {self.name} failed: {e}")
            return {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "data": None
            }
    
    @abstractmethod
    def execute(self) -> Dict[str, Any]:
        """
        Perform the handler's monitoring task.
        
        Subclasses must implement this method.
        
        Returns:
            Dict with handler-specific results
        """
        pass
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get serializable state for persistence.
        
        Override in subclasses to include additional state.
        """
        return {
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "enabled": self._enabled
        }
    
    def set_state(self, state: Dict[str, Any]) -> None:
        """
        Restore handler state from persisted data.
        
        Override in subclasses to restore additional state.
        """
        last_run = state.get("last_run")
        if last_run:
            self.last_run = datetime.fromisoformat(last_run)
        else:
            self.last_run = None
        
        self._enabled = state.get("enabled", True)
