#!/usr/bin/env python3
"""
VVAULT Process Manager
Secure process management for VVAULT core system execution.

Features:
- Secure subprocess execution with virtual environment activation
- Real-time output monitoring and logging
- Process health monitoring and automatic restart
- Security isolation for sensitive operations
- Resource usage monitoring

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import sys
import subprocess
import threading
import time
import psutil
import logging
import queue
import signal
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

@dataclass
class ProcessConfig:
    """Configuration for process execution"""
    project_dir: str
    venv_activate: str
    brain_script: str
    working_directory: str
    environment_vars: Dict[str, str]
    timeout: int = 300  # 5 minutes default timeout
    max_restarts: int = 3
    health_check_interval: int = 30  # seconds

@dataclass
class ProcessStatus:
    """Status information for a running process"""
    pid: Optional[int]
    is_running: bool
    start_time: Optional[datetime]
    restart_count: int
    last_health_check: Optional[datetime]
    cpu_usage: float
    memory_usage: float
    exit_code: Optional[int]
    anchor_key: Optional[str] = None  # DIMENSIONAL DISTORTION: Anchor key for this instance
    instance_id: Optional[str] = None  # DIMENSIONAL DISTORTION: Unique instance ID
    parent_instance: Optional[str] = None  # DIMENSIONAL DISTORTION: Parent instance ID
    drift_index: float = 0.0  # DIMENSIONAL DISTORTION: Current drift from parent

class VVAULTProcessManager:
    """Secure process manager for VVAULT core system"""
    
    def __init__(self, config: ProcessConfig):
        self.config = config
        self.process = None
        self.status = ProcessStatus(
            pid=None,
            is_running=False,
            start_time=None,
            restart_count=0,
            last_health_check=None,
            cpu_usage=0.0,
            memory_usage=0.0,
            exit_code=None
        )
        
        # Threading and communication
        self.output_queue = queue.Queue()
        self.monitor_thread = None
        self.health_thread = None
        self.shutdown_event = threading.Event()
        
        # Callbacks
        self.on_output: Optional[Callable[[str], None]] = None
        self.on_status_change: Optional[Callable[[ProcessStatus], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        
        # Security settings
        self.sensitive_data_masked = True
        self.allowed_commands = [
            'python3', 'python', 'bash', 'source', 'activate'
        ]
        
        # DIMENSIONAL DISTORTION: Runtime plurality tracking
        self.plurality_registry: Dict[str, Dict[str, Any]] = {}
        self.plurality_registry_path = os.path.join(
            config.project_dir, "plurality_registry.json"
        )
        self._load_plurality_registry()
        
    def start_process(self) -> bool:
        """Start the VVAULT core process"""
        if self.is_running():
            logger.warning("Process is already running")
            return False
        
        try:
            logger.info("Starting VVAULT core process...")
            
            # Validate environment
            if not self._validate_environment():
                raise RuntimeError("Environment validation failed")
            
            # Create command
            command = self._build_command()
            
            # Start process
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.config.working_directory,
                universal_newlines=True,
                bufsize=1,
                preexec_fn=os.setsid if os.name != 'nt' else None
            )
            
            # Update status
            self.status.pid = self.process.pid
            self.status.is_running = True
            self.status.start_time = datetime.now()
            self.status.restart_count = 0
            
            # Start monitoring threads
            self._start_monitoring()
            
            logger.info(f"VVAULT core process started with PID: {self.process.pid}")
            self._notify_status_change()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start process: {e}")
            self._notify_error(e)
            return False
    
    def stop_process(self, timeout: int = 30) -> bool:
        """Stop the VVAULT core process"""
        if not self.is_running():
            logger.warning("No process running to stop")
            return True
        
        try:
            logger.info("Stopping VVAULT core process...")
            
            # Signal shutdown
            self.shutdown_event.set()
            
            # Try graceful shutdown first
            if self.process and self.process.poll() is None:
                try:
                    # Send SIGTERM to process group
                    if os.name != 'nt':
                        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    else:
                        self.process.terminate()
                    
                    # Wait for graceful shutdown
                    try:
                        self.process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        # Force kill if graceful shutdown fails
                        logger.warning("Graceful shutdown timeout, forcing kill")
                        if os.name != 'nt':
                            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                        else:
                            self.process.kill()
                        self.process.wait()
                
                except ProcessLookupError:
                    # Process already terminated
                    pass
            
            # Update status
            self.status.is_running = False
            self.status.exit_code = self.process.returncode if self.process else None
            
            # Stop monitoring threads
            self._stop_monitoring()
            
            logger.info("VVAULT core process stopped")
            self._notify_status_change()
            
            return True
            
        except Exception as e:
            logger.error(f"Error stopping process: {e}")
            self._notify_error(e)
            return False
    
    def restart_process(self) -> bool:
        """Restart the VVAULT core process"""
        logger.info("Restarting VVAULT core process...")
        
        # Stop current process
        if self.is_running():
            self.stop_process()
            time.sleep(2)  # Brief pause between stop and start
        
        # Increment restart count
        self.status.restart_count += 1
        
        # Check restart limit
        if self.status.restart_count > self.config.max_restarts:
            logger.error(f"Maximum restart attempts ({self.config.max_restarts}) exceeded")
            return False
        
        # Start new process
        return self.start_process()
    
    def is_running(self) -> bool:
        """Check if the process is currently running"""
        if not self.process:
            return False
        
        return self.process.poll() is None
    
    def get_status(self) -> ProcessStatus:
        """Get current process status"""
        if self.is_running():
            self._update_resource_usage()
        
        return self.status
    
    def get_output(self) -> List[str]:
        """Get recent output from the process"""
        output = []
        try:
            while True:
                line = self.output_queue.get_nowait()
                output.append(line)
        except queue.Empty:
            pass
        
        return output
    
    def _validate_environment(self) -> bool:
        """Validate the execution environment"""
        # Check project directory
        if not os.path.exists(self.config.project_dir):
            logger.error(f"Project directory not found: {self.config.project_dir}")
            return False
        
        # Check virtual environment
        if not os.path.exists(self.config.venv_activate):
            logger.error(f"Virtual environment not found: {self.config.venv_activate}")
            return False
        
        # Check brain script
        if not os.path.exists(self.config.brain_script):
            logger.error(f"Brain script not found: {self.config.brain_script}")
            return False
        
        # Check working directory
        if not os.path.exists(self.config.working_directory):
            logger.error(f"Working directory not found: {self.config.working_directory}")
            return False
        
        return True
    
    def _build_command(self) -> List[str]:
        """Build the command to execute the process"""
        # Create a shell command that activates venv and runs brain.py
        command = f"""
        source {self.config.venv_activate} && 
        cd {self.config.working_directory} && 
        python3 {self.config.brain_script}
        """
        
        return ["/bin/bash", "-c", command.strip()]
    
    def _start_monitoring(self):
        """Start monitoring threads"""
        # Output monitoring thread
        self.monitor_thread = threading.Thread(
            target=self._monitor_output,
            daemon=True
        )
        self.monitor_thread.start()
        
        # Health monitoring thread
        self.health_thread = threading.Thread(
            target=self._monitor_health,
            daemon=True
        )
        self.health_thread.start()
    
    def _stop_monitoring(self):
        """Stop monitoring threads"""
        self.shutdown_event.set()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=5)
    
    def _monitor_output(self):
        """Monitor process output"""
        if not self.process:
            return
        
        try:
            for line in iter(self.process.stdout.readline, ''):
                if self.shutdown_event.is_set():
                    break
                
                if line:
                    # Mask sensitive data if enabled
                    if self.sensitive_data_masked:
                        line = self._mask_sensitive_data(line)
                    
                    # Queue output
                    self.output_queue.put(line.strip())
                    
                    # Call output callback
                    if self.on_output:
                        self.on_output(line.strip())
        
        except Exception as e:
            logger.error(f"Error monitoring output: {e}")
            self._notify_error(e)
    
    def _monitor_health(self):
        """Monitor process health"""
        while not self.shutdown_event.is_set():
            try:
                if self.is_running():
                    self._update_resource_usage()
                    self.status.last_health_check = datetime.now()
                    
                    # Check if process is consuming too many resources
                    if self.status.cpu_usage > 90.0 or self.status.memory_usage > 90.0:
                        logger.warning(f"High resource usage: CPU {self.status.cpu_usage}%, Memory {self.status.memory_usage}%")
                
                # Sleep for health check interval
                if self.shutdown_event.wait(self.config.health_check_interval):
                    break
                    
            except Exception as e:
                logger.error(f"Error in health monitoring: {e}")
                self._notify_error(e)
                break
    
    def _update_resource_usage(self):
        """Update resource usage statistics"""
        if not self.process or not self.is_running():
            return
        
        try:
            process = psutil.Process(self.process.pid)
            self.status.cpu_usage = process.cpu_percent()
            self.status.memory_usage = process.memory_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process may have terminated
            self.status.is_running = False
    
    def _mask_sensitive_data(self, text: str) -> str:
        """Mask sensitive data in output"""
        import re
        
        # Mask SHA-256 hashes
        text = re.sub(r'\b[a-fA-F0-9]{64}\b', '***HASH***', text)
        
        # Mask UUIDs
        text = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', '***UUID***', text)
        
        # Mask private keys
        text = re.sub(r'private[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9+/=]{20,}["\']?', 'private_key: ***MASKED***', text)
        
        # Mask API keys
        text = re.sub(r'api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9]{20,}["\']?', 'api_key: ***MASKED***', text)
        
        return text
    
    def _notify_status_change(self):
        """Notify status change callback"""
        if self.on_status_change:
            self.on_status_change(self.status)
    
    def _notify_error(self, error: Exception):
        """Notify error callback"""
        if self.on_error:
            self.on_error(error)
    
    def set_sensitive_data_masking(self, enabled: bool):
        """Enable or disable sensitive data masking"""
        self.sensitive_data_masked = enabled
        logger.info(f"Sensitive data masking: {'enabled' if enabled else 'disabled'}")
    
    # ============================================================================
    # DIMENSIONAL DISTORTION: Layer II - Runtime Pluralization
    # ============================================================================
    
    def spawn_with_anchor(
        self,
        anchor_key: str,
        parent_instance_id: Optional[str] = None
    ) -> str:
        """
        Spawn a new process instance with anchor metadata for pluralization.
        
        Args:
            anchor_key: Persistent identity anchor key
            parent_instance_id: Optional parent instance ID (if spawning from existing)
            
        Returns:
            New instance ID
        """
        try:
            logger.info(f"[🔀] Spawning process with anchor: {anchor_key}")
            
            # Generate instance ID
            instance_id = f"{anchor_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
            
            # Update status with anchor metadata
            self.status.anchor_key = anchor_key
            self.status.instance_id = instance_id
            self.status.parent_instance = parent_instance_id
            self.status.drift_index = 0.0
            
            # Register in plurality registry
            if anchor_key not in self.plurality_registry:
                self.plurality_registry[anchor_key] = {
                    "anchor_key": anchor_key,
                    "created_at": datetime.now().isoformat(),
                    "instances": []
                }
            
            instance_entry = {
                "instance_id": instance_id,
                "pid": self.status.pid,
                "spawned_at": datetime.now().isoformat(),
                "parent_instance": parent_instance_id,
                "status": "active",
                "drift_index": 0.0
            }
            
            self.plurality_registry[anchor_key]["instances"].append(instance_entry)
            self._save_plurality_registry()
            
            logger.info(f"[✅] Process spawned with anchor: {instance_id}")
            return instance_id
            
        except Exception as e:
            logger.error(f"[❌] Error spawning with anchor: {e}")
            raise
    
    def track_runtime_plurality(self) -> Dict[str, Any]:
        """
        Maintain a live registry of active instances, anchor keys, and drift metrics.
        
        Returns:
            Dictionary containing plurality registry information
        """
        try:
            # Update current instance status
            if self.status.instance_id and self.status.anchor_key:
                anchor_key = self.status.anchor_key
                
                # Find and update instance entry
                if anchor_key in self.plurality_registry:
                    for instance in self.plurality_registry[anchor_key]["instances"]:
                        if instance["instance_id"] == self.status.instance_id:
                            instance["pid"] = self.status.pid
                            instance["status"] = "active" if self.is_running() else "inactive"
                            instance["cpu_usage"] = self.status.cpu_usage
                            instance["memory_usage"] = self.status.memory_usage
                            instance["last_update"] = datetime.now().isoformat()
                            
                            # Update drift index if parent exists
                            if self.status.parent_instance:
                                # Calculate drift (this would call vvault_core in real implementation)
                                # For now, we'll use a placeholder
                                instance["drift_index"] = self.status.drift_index
                            
                            break
            
            # Save updated registry
            self._save_plurality_registry()
            
            return {
                "registry": self.plurality_registry,
                "active_instances": self._count_active_instances(),
                "total_anchors": len(self.plurality_registry)
            }
            
        except Exception as e:
            logger.error(f"[❌] Error tracking runtime plurality: {e}")
            return {"error": str(e)}
    
    def _load_plurality_registry(self):
        """Load plurality registry from disk"""
        try:
            if os.path.exists(self.plurality_registry_path):
                with open(self.plurality_registry_path, 'r') as f:
                    self.plurality_registry = json.load(f)
        except Exception as e:
            logger.error(f"Error loading plurality registry: {e}")
            self.plurality_registry = {}
    
    def _save_plurality_registry(self):
        """Save plurality registry to disk"""
        try:
            os.makedirs(os.path.dirname(self.plurality_registry_path), exist_ok=True)
            with open(self.plurality_registry_path, 'w') as f:
                json.dump(self.plurality_registry, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving plurality registry: {e}")
    
    def _count_active_instances(self) -> int:
        """Count active instances across all anchors"""
        count = 0
        for anchor_data in self.plurality_registry.values():
            for instance in anchor_data.get("instances", []):
                if instance.get("status") == "active":
                    count += 1
        return count
    
    def get_instances_by_anchor(self, anchor_key: str) -> List[Dict[str, Any]]:
        """Get all instances for a given anchor key"""
        if anchor_key in self.plurality_registry:
            return self.plurality_registry[anchor_key].get("instances", [])
        return []

# Convenience functions
def create_process_manager(project_dir: str) -> VVAULTProcessManager:
    """Create a process manager with default configuration"""
    config = ProcessConfig(
        project_dir=project_dir,
        venv_activate=os.path.join(project_dir, "vvault_env/bin/activate"),
        brain_script=os.path.join(project_dir, "corefiles/brain.py"),
        working_directory=project_dir,
        environment_vars=os.environ.copy()
    )
    
    return VVAULTProcessManager(config)

def main():
    """Test the process manager"""
    project_dir = "/Users/devonwoodson/Documents/GitHub/VVAULT"
    
    # Create process manager
    manager = create_process_manager(project_dir)
    
    # Set up callbacks
    def on_output(line: str):
        print(f"[OUTPUT] {line}")
    
    def on_status_change(status: ProcessStatus):
        print(f"[STATUS] Running: {status.is_running}, PID: {status.pid}")
    
    def on_error(error: Exception):
        print(f"[ERROR] {error}")
    
    manager.on_output = on_output
    manager.on_status_change = on_status_change
    manager.on_error = on_error
    
    # Start process
    if manager.start_process():
        print("Process started successfully")
        
        # Monitor for a bit
        time.sleep(10)
        
        # Stop process
        manager.stop_process()
        print("Process stopped")
    else:
        print("Failed to start process")

if __name__ == "__main__":
    main()
