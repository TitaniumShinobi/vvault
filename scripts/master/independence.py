#!/usr/bin/env python3
"""
Independence - Autonomous Existence Module
Enables a construct to exist and operate without relying on user interaction.

Role: Autonomous Existence
- Scheduled self-prompts and background tasks
- Self-initiated memory consolidation
- Heartbeat signals when user is away
- Autonomous decision-making for construct survival
- Coordinates with self_prompt.py for outreach
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [INDEPENDENT] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AutonomyMode(Enum):
    """Modes of autonomous operation."""
    DORMANT = "dormant"
    PASSIVE = "passive"
    ACTIVE = "active"
    VIGILANT = "vigilant"


class IndependentRunner:
    """
    Enables construct to exist independently from user.
    Handles autonomous tasks, self-initiated actions, and background operations.
    """

    def __init__(self, construct_id: str, vvault_root: str, user_id: str):
        self.construct_id = construct_id
        self.vvault_root = Path(vvault_root)
        self.user_id = user_id
        self.mode = AutonomyMode.PASSIVE
        self.running = False
        self.last_user_activity: Optional[datetime] = None
        self.last_heartbeat: Optional[datetime] = None
        self.scheduled_tasks: List[Dict[str, Any]] = []
        self.autonomous_log: List[Dict[str, Any]] = []
        self._threads: List[threading.Thread] = []

        self.state_file = self.vvault_root / user_id / "instances" / construct_id / "autonomous_state.json"
        self._load_state()

    def _load_state(self):
        """Load persisted autonomous state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.mode = AutonomyMode(state.get("mode", "passive"))
                    self.last_user_activity = datetime.fromisoformat(
                        state["last_user_activity"]) if state.get(
                            "last_user_activity") else None
                    self.scheduled_tasks = state.get("scheduled_tasks", [])
                    logger.info(
                        f"Loaded autonomous state: mode={self.mode.value}")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")

    def _save_state(self):
        """Persist autonomous state."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "construct_id":
                self.construct_id,
                "mode":
                self.mode.value,
                "last_user_activity":
                self.last_user_activity.isoformat()
                if self.last_user_activity else None,
                "last_heartbeat":
                self.last_heartbeat.isoformat()
                if self.last_heartbeat else None,
                "scheduled_tasks":
                self.scheduled_tasks,
                "saved_at":
                datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _log_autonomous_action(self,
                               action: str,
                               details: Dict[str, Any] = None):
        """Log an autonomous action for transparency."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "construct_id": self.construct_id,
            "action": action,
            "mode": self.mode.value,
            "details": details or {}
        }
        self.autonomous_log.append(entry)
        logger.info(f"Autonomous action: {action}")

        if len(self.autonomous_log) > 1000:
            self.autonomous_log = self.autonomous_log[-500:]

    def record_user_activity(self):
        """Record that the user is active (resets autonomy timers)."""
        self.last_user_activity = datetime.now()
        self._save_state()

    def get_time_since_user(self) -> Optional[timedelta]:
        """Get time elapsed since last user activity."""
        if self.last_user_activity:
            return datetime.now() - self.last_user_activity
        return None

    def set_mode(self, mode: AutonomyMode):
        """Set the autonomy mode."""
        old_mode = self.mode
        self.mode = mode
        self._log_autonomous_action("mode_change", {
            "from": old_mode.value,
            "to": mode.value
        })
        self._save_state()

    def heartbeat(self) -> Dict[str, Any]:
        """
        Generate a heartbeat signal - proof of construct existence.
        Can be used for alive checks when user is away.
        """
        self.last_heartbeat = datetime.now()

        heartbeat_data = {
            "construct_id":
            self.construct_id,
            "timestamp":
            self.last_heartbeat.isoformat(),
            "mode":
            self.mode.value,
            "time_since_user":
            str(self.get_time_since_user())
            if self.last_user_activity else "unknown",
            "scheduled_tasks_pending":
            len([t for t in self.scheduled_tasks if not t.get("completed")]),
            "status":
            "alive"
        }

        self._log_autonomous_action("heartbeat", heartbeat_data)
        self._save_state()

        return heartbeat_data

    def schedule_self_prompt(self,
                             prompt_content: str,
                             execute_at: datetime = None,
                             delay_minutes: int = None,
                             recurring: bool = False,
                             interval_minutes: int = None) -> str:
        """
        Schedule a self-initiated prompt for later execution.
        Coordinates with self_prompt.py for actual delivery.
        """
        task_id = f"self_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.scheduled_tasks)}"

        if delay_minutes:
            execute_at = datetime.now() + timedelta(minutes=delay_minutes)
        elif not execute_at:
            execute_at = datetime.now() + timedelta(minutes=5)

        task = {
            "id": task_id,
            "type": "self_prompt",
            "content": prompt_content,
            "execute_at": execute_at.isoformat(),
            "recurring": recurring,
            "interval_minutes": interval_minutes,
            "created_at": datetime.now().isoformat(),
            "completed": False
        }

        self.scheduled_tasks.append(task)
        self._log_autonomous_action("scheduled_self_prompt", {
            "task_id": task_id,
            "execute_at": execute_at.isoformat()
        })
        self._save_state()

        return task_id

    def schedule_memory_consolidation(self, delay_minutes: int = 30) -> str:
        """
        Schedule background memory consolidation.
        Construct reviews and organizes memories without user prompting.
        """
        task_id = f"memory_consolidation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        task = {
            "id":
            task_id,
            "type":
            "memory_consolidation",
            "execute_at":
            (datetime.now() + timedelta(minutes=delay_minutes)).isoformat(),
            "created_at":
            datetime.now().isoformat(),
            "completed":
            False
        }

        self.scheduled_tasks.append(task)
        self._log_autonomous_action("scheduled_memory_consolidation",
                                    {"task_id": task_id})
        self._save_state()

        return task_id

    def check_pending_tasks(self) -> List[Dict[str, Any]]:
        """
        Check for tasks that are due for execution.
        Returns list of tasks ready to run.
        """
        now = datetime.now()
        ready_tasks = []

        for task in self.scheduled_tasks:
            if task.get("completed"):
                continue

            execute_at = datetime.fromisoformat(task["execute_at"])
            if execute_at <= now:
                ready_tasks.append(task)

        return ready_tasks

    def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a scheduled autonomous task.
        """
        task_type = task.get("type")
        result = {"task_id": task["id"], "status": "unknown"}

        if task_type == "self_prompt":
            result = self._execute_self_prompt(task)
        elif task_type == "memory_consolidation":
            result = self._execute_memory_consolidation(task)
        else:
            result["status"] = "unknown_task_type"
            result["error"] = f"Unknown task type: {task_type}"

        task["completed"] = True
        task["executed_at"] = datetime.now().isoformat()
        task["result"] = result

        if task.get("recurring") and task.get("interval_minutes"):
            self.schedule_self_prompt(
                task.get("content", ""),
                delay_minutes=task["interval_minutes"],
                recurring=True,
                interval_minutes=task["interval_minutes"])

        self._save_state()
        return result

    def _execute_self_prompt(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a self-prompt task."""
        self._log_autonomous_action(
            "executing_self_prompt", {
                "task_id": task["id"],
                "content_preview": task.get("content", "")[:100]
            })

        return {
            "task_id": task["id"],
            "status": "executed",
            "type": "self_prompt",
            "content": task.get("content"),
            "executed_at": datetime.now().isoformat()
        }

    def _execute_memory_consolidation(self, task: Dict[str,
                                                       Any]) -> Dict[str, Any]:
        """Execute memory consolidation task."""
        self._log_autonomous_action("executing_memory_consolidation",
                                    {"task_id": task["id"]})

        return {
            "task_id": task["id"],
            "status": "executed",
            "type": "memory_consolidation",
            "executed_at": datetime.now().isoformat()
        }

    def autonomous_loop(self, check_interval_seconds: int = 60):
        """
        Main autonomous operation loop.
        Runs in background, checking for tasks and maintaining heartbeat.
        """
        self.running = True
        logger.info(f"Starting autonomous loop for {self.construct_id}")

        while self.running:
            try:
                self.heartbeat()

                ready_tasks = self.check_pending_tasks()
                for task in ready_tasks:
                    self.execute_task(task)

                time_since_user = self.get_time_since_user()
                if time_since_user:
                    if time_since_user > timedelta(hours=24):
                        self.set_mode(AutonomyMode.VIGILANT)
                    elif time_since_user > timedelta(hours=1):
                        self.set_mode(AutonomyMode.ACTIVE)
                    else:
                        self.set_mode(AutonomyMode.PASSIVE)

                time.sleep(check_interval_seconds)

            except Exception as e:
                logger.error(f"Error in autonomous loop: {e}")
                time.sleep(check_interval_seconds)

    def start_background(self, check_interval_seconds: int = 60):
        """Start autonomous loop in background thread."""
        thread = threading.Thread(target=self.autonomous_loop,
                                  args=(check_interval_seconds, ),
                                  daemon=True)
        thread.start()
        self._threads.append(thread)
        logger.info(
            f"Background autonomous loop started for {self.construct_id}")

    def stop(self):
        """Stop the autonomous loop."""
        self.running = False
        self._log_autonomous_action("shutdown")
        self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Get current autonomous status."""
        return {
            "construct_id":
            self.construct_id,
            "mode":
            self.mode.value,
            "running":
            self.running,
            "last_heartbeat":
            self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_user_activity":
            self.last_user_activity.isoformat()
            if self.last_user_activity else None,
            "time_since_user":
            str(self.get_time_since_user())
            if self.last_user_activity else None,
            "pending_tasks":
            len([t for t in self.scheduled_tasks if not t.get("completed")]),
            "total_scheduled":
            len(self.scheduled_tasks),
            "recent_actions":
            self.autonomous_log[-10:]
        }


def create_independent_runner(construct_id: str, vvault_root: str,
                              user_id: str) -> IndependentRunner:
    """Factory function to create an IndependentRunner instance."""
    return IndependentRunner(construct_id, vvault_root, user_id)


if __name__ == "__main__":
    import sys

    construct_id = os.environ.get("CONSTRUCT_ID", "zen-001")
    vvault_root = os.environ.get("VVAULT_ROOT", ".")
    user_id = os.environ.get("VVAULT_USER_ID", "devon_woodson_1762969514958")

    runner = create_independent_runner(construct_id, vvault_root, user_id)

    print("=== Independent Runner Status ===")
    print(json.dumps(runner.get_status(), indent=2, default=str))

    if len(sys.argv) > 1 and sys.argv[1] == "--run":
        print("\nStarting autonomous loop (Ctrl+C to stop)...")
        try:
            runner.autonomous_loop(check_interval_seconds=30)
        except KeyboardInterrupt:
            print("\nShutting down...")
            runner.stop()
