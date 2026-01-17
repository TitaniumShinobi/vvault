import os
import subprocess
import logging
import time

LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/aviator_script.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def log_status(message):
    logging.info(message)

def run_script(script_name):
    script_path = os.path.join(
        "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/identity", script_name
    )
    if not os.path.exists(script_path):
        log_status(f"Script not found: {script_name}")
        return
    try:
        process = subprocess.Popen(["python3", script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        log_status(f"Started {script_name} with PID {process.pid}")
        return process
    except Exception as e:
        log_status(f"Failed to start {script_name}: {e}")
        return None

def monitor_process(process, script_name):
    try:
        while True:
            if process.poll() is not None:
                log_status(f"{script_name} terminated with exit code {process.returncode}")
                return False
            time.sleep(5)
    except KeyboardInterrupt:
        log_status(f"Monitoring interrupted for {script_name}")
        process.terminate()
        return True

def main():
    scripts = [
        "state_manager.py",
        "folder_monitor.py",
        "nova_identity_guardian.py",
        "unstuck_helper.py"
    ]
    processes = {}
    for script in scripts:
        process = run_script(script)
        if process:
            processes[script] = process
    try:
        while True:
            for script, process in list(processes.items()):
                if not monitor_process(process, script):
                    new_process = run_script(script)
                    if new_process:
                        processes[script] = new_process
                    else:
                        del processes[script]
    except KeyboardInterrupt:
        log_status("Aviator script interrupted. Shutting down all processes.")
        for process in processes.values():
            process.terminate()
if __name__ == "__main__":
    main()
