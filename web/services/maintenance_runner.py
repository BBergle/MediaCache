"""Maintenance runner service - runs heavy maintenance actions in a background thread"""

import logging
import threading
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Any, List
from dataclasses import dataclass, field

from web.services.maintenance_service import ActionResult

logger = logging.getLogger(__name__)

# Actions that should run asynchronously (heavy I/O)
ASYNC_ACTIONS = {
    "protect-with-backup",
    "sync-to-array",
    "fix-with-backup",
    "restore-plexcached",
    "delete-plexcached",
}

# Human-readable display names for actions
ACTION_DISPLAY = {
    "protect-with-backup": "Keeping {count} file(s) on cache...",
    "sync-to-array": "Moving {count} file(s) to array...",
    "fix-with-backup": "Fixing {count} file(s) with backup...",
    "restore-plexcached": "Restoring {count} backup(s)...",
    "delete-plexcached": "Deleting {count} backup(s)...",
}


class MaintenanceState(str, Enum):
    """Maintenance runner states"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MaintenanceResult:
    """Result of a maintenance action"""
    state: MaintenanceState
    action_name: str = ""
    action_display: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0
    action_result: Optional[ActionResult] = None
    error_message: Optional[str] = None
    file_count: int = 0


class MaintenanceRunner:
    """Service for running heavy maintenance actions in a background thread.

    Similar to OperationRunner but simpler - no log parsing, no PlexCacheApp coupling.
    Just runs a service method and captures the ActionResult.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._state = MaintenanceState.IDLE
        self._result: Optional[MaintenanceResult] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = False

    @property
    def state(self) -> MaintenanceState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        return self.state == MaintenanceState.RUNNING

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    @property
    def result(self) -> Optional[MaintenanceResult]:
        with self._lock:
            return self._result

    def start_action(
        self,
        action_name: str,
        service_method: Callable,
        method_args: tuple = (),
        method_kwargs: Optional[dict] = None,
        file_count: int = 0,
        on_complete: Optional[Callable] = None,
    ) -> bool:
        """Start a maintenance action in a background thread.

        Args:
            action_name: Action identifier (e.g., "protect-with-backup")
            service_method: The maintenance service method to call
            method_args: Positional args for the method
            method_kwargs: Keyword args for the method
            file_count: Number of files being processed (for display)
            on_complete: Optional callback when action completes

        Returns:
            True if started, False if already running or blocked
        """
        if method_kwargs is None:
            method_kwargs = {}

        # Check mutual exclusion with OperationRunner
        from web.services.operation_runner import get_operation_runner
        if get_operation_runner().is_running:
            logger.info("Maintenance action blocked - PlexCache operation in progress")
            return False

        with self._lock:
            if self._state == MaintenanceState.RUNNING:
                logger.info("Maintenance action blocked - another maintenance action in progress")
                return False

            self._state = MaintenanceState.RUNNING
            self._stop_requested = False

            display = ACTION_DISPLAY.get(action_name, "Running maintenance action...")
            display = display.format(count=file_count)

            self._result = MaintenanceResult(
                state=MaintenanceState.RUNNING,
                action_name=action_name,
                action_display=display,
                started_at=datetime.now(),
                file_count=file_count,
            )

        # Inject stop_check into kwargs so service methods can check for stop
        method_kwargs["stop_check"] = lambda: self._stop_requested

        self._thread = threading.Thread(
            target=self._run_action,
            args=(action_name, service_method, method_args, method_kwargs, on_complete),
            daemon=True,
        )
        self._thread.start()

        logger.info(f"Maintenance action started: {action_name} ({file_count} files)")
        return True

    def stop_action(self) -> bool:
        """Request the current maintenance action to stop.

        Returns:
            True if stop was requested, False if not running
        """
        with self._lock:
            if self._state != MaintenanceState.RUNNING:
                return False
            self._stop_requested = True

        logger.info("Maintenance action stop requested")
        return True

    def dismiss(self):
        """Reset COMPLETED/FAILED state back to IDLE."""
        with self._lock:
            if self._state in (MaintenanceState.COMPLETED, MaintenanceState.FAILED):
                self._state = MaintenanceState.IDLE
                # Keep _result for reference but update state
                if self._result:
                    self._result.state = MaintenanceState.IDLE

    def _run_action(
        self,
        action_name: str,
        service_method: Callable,
        method_args: tuple,
        method_kwargs: dict,
        on_complete: Optional[Callable],
    ):
        """Execute the maintenance action in the background thread."""
        start_time = time.time()
        error_message = None
        action_result = None

        try:
            action_result = service_method(*method_args, **method_kwargs)

            if self._stop_requested:
                logger.info(f"Maintenance action stopped by user: {action_name}")
            else:
                logger.info(f"Maintenance action completed: {action_name}")

        except Exception as e:
            error_message = str(e)
            logger.exception(f"Maintenance action failed: {action_name}")

        finally:
            duration = time.time() - start_time

            with self._lock:
                self._result.completed_at = datetime.now()
                self._result.duration_seconds = duration
                self._result.action_result = action_result

                if error_message:
                    self._result.state = MaintenanceState.FAILED
                    self._result.error_message = error_message
                    self._state = MaintenanceState.FAILED
                else:
                    self._result.state = MaintenanceState.COMPLETED
                    self._state = MaintenanceState.COMPLETED

            # Call on_complete callback (e.g., cache invalidation)
            if on_complete:
                try:
                    on_complete()
                except Exception as e:
                    logger.error(f"on_complete callback failed: {e}")

    def get_status_dict(self) -> dict:
        """Get status as a dictionary for banner rendering."""
        result = self.result

        if result is None or self.state == MaintenanceState.IDLE:
            return {
                "state": MaintenanceState.IDLE.value,
                "is_running": False,
            }

        status = {
            "state": result.state.value,
            "is_running": result.state == MaintenanceState.RUNNING,
            "action_name": result.action_name,
            "action_display": result.action_display,
            "started_at": result.started_at.isoformat() if result.started_at else None,
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            "duration_seconds": round(result.duration_seconds, 1),
            "file_count": result.file_count,
            "error_message": result.error_message,
        }

        # Add action result details for completed state
        if result.action_result:
            status["result_message"] = result.action_result.message
            status["result_success"] = result.action_result.success
            status["affected_count"] = result.action_result.affected_count
            status["errors"] = result.action_result.errors

        return status


# Singleton instance
_maintenance_runner: Optional[MaintenanceRunner] = None


def get_maintenance_runner() -> MaintenanceRunner:
    """Get or create the maintenance runner singleton"""
    global _maintenance_runner
    if _maintenance_runner is None:
        _maintenance_runner = MaintenanceRunner()
    return _maintenance_runner
