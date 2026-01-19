import os
import subprocess
import logging
import time
import sys

# Configure logging
LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/aviator_script.log"
logging.basicConfig(filename=LOG_FILE,
                    level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def log_status(message):
    """Log the status of the aviator script."""
    logging.info(message)


def run_script(script_name):
    """Run a specific script and monitor its status."""
    script_path = os.path.join(
        "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/identity",
        script_name)
    if not os.path.exists(script_path):
        log_status(f"Script not found: {script_name}")
        return

    try:
        process = subprocess.Popen(["python3", script_path],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        log_status(f"Started {script_name} with PID {process.pid}")
        return process
    except Exception as e:
        log_status(f"Failed to start {script_name}: {e}")
        return None


def monitor_process(process, script_name):
    """Monitor a running process and restart if it terminates."""
    try:
        while True:
            if process.poll() is not None:  # Process has terminated
                log_status(
                    f"{script_name} terminated with exit code {process.returncode}"
                )
                return False
            time.sleep(5)  # Check every 5 seconds
    except KeyboardInterrupt:
        log_status(f"Monitoring interrupted for {script_name}")
        process.terminate()
        return True


def handle_user_prompt(prompt):
    """Handle user prompts to dynamically adjust scripts or tasks."""
    if prompt.startswith("tag "):
        # Extract directory to tag from the prompt
        directory = prompt[4:].strip()
        log_status(f"Received tagging request for directory: {directory}")
        # Trigger navigator.py for tagging
        try:
            process = subprocess.Popen(["python3", "navigator.py"],
                                       stdin=subprocess.PIPE)
            process.communicate(input=directory.encode())
            log_status(f"Navigator triggered for tagging: {directory}")
        except Exception as e:
            log_status(f"Failed to trigger navigator.py: {e}")
    else:
        log_status(f"Unknown prompt: {prompt}")


def main():
    """Main function to run and monitor scripts."""
    scripts = [
        "state_manager.py", "folder_monitor.py", "nova_identity_guardian.py",
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
                    # Restart the script if it terminated
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
    if len(sys.argv) > 1:
        user_prompt = " ".join(sys.argv[1:])
        handle_user_prompt(user_prompt)
    else:
        main()
