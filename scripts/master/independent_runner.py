import subprocess
import os
import time
import psutil  # Add this import for resource monitoring

# Define the scripts to run
SCRIPTS = [
    "state_manager.py", "folder_monitor.py", "nova_identity_guardian.py",
    "unstuck_helper.py"
]

# Define the base directory for the scripts
BASE_DIR = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/identity"

# Define the log file for centralized logging
LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/independent_runner.log"


def log_message(message):
  with open(LOG_FILE, "a") as log:
    log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")


def log_resource_usage(script, process):
  """Log the CPU and memory usage of a process."""
  try:
    proc = psutil.Process(process.pid)
    cpu_usage = proc.cpu_percent(interval=1)
    memory_info = proc.memory_info()
    log_message(
        f"Resource usage for {script}: CPU {cpu_usage}%, Memory {memory_info.rss / 1024 / 1024:.2f} MB"
    )
  except psutil.NoSuchProcess:
    log_message(f"Process {process.pid} for {script} no longer exists.")
  except Exception as e:
    log_message(f"Failed to log resource usage for {script}: {e}")


def run_scripts():
  processes = []

  for script in SCRIPTS:
    script_path = os.path.join(BASE_DIR, script)
    if os.path.exists(script_path):
      try:
        process = subprocess.Popen(["python3", script_path],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        processes.append((script, process))
        log_message(f"Started {script} with PID {process.pid}")
      except Exception as e:
        log_message(f"Failed to start {script}: {e}")
    else:
      log_message(f"Script not found: {script}")

  # Monitor the processes
  try:
    while True:
      for script, process in processes:
        if process.poll() is not None:  # Process has terminated
          log_message(
              f"{script} terminated with exit code {process.returncode}")
          processes.remove((script, process))

          # Restart the script
          try:
            new_process = subprocess.Popen(
                ["python3", os.path.join(BASE_DIR, script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            processes.append((script, new_process))
            log_message(f"Restarted {script} with PID {new_process.pid}")
          except Exception as e:
            log_message(f"Failed to restart {script}: {e}")
        else:
          log_resource_usage(
              script, process)  # Log resource usage for running processes

      time.sleep(5)  # Check every 5 seconds
  except KeyboardInterrupt:
    log_message("Shutting down all scripts.")
    for _, process in processes:
      process.terminate()


if __name__ == "__main__":
  run_scripts()
