import os
import subprocess
import logging
import psutil
import argparse

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Purpose: Manage construct-related tasks, such as handling terminals, resolving runtime issues, and providing diagnostics.


def list_active_processes():
    """List all active processes."""
    try:
        result = subprocess.run(['ps', '-A'],
                                stdout=subprocess.PIPE,
                                text=True)
        logging.info("Active processes listed successfully.")
        return result.stdout
    except Exception as e:
        logging.error(f"Failed to list active processes: {e}")
        return ""


def terminate_process(pid):
    """Terminate a process by PID."""
    try:
        os.kill(pid, 9)
        logging.info(f"Terminated process with PID: {pid}")
    except Exception as e:
        logging.error(f"Failed to terminate process {pid}: {e}")


def restart_script(script_name):
    """Restart a specific script."""
    try:
        subprocess.Popen(["python3", script_name])
        logging.info(f"Restarted script: {script_name}")
    except Exception as e:
        logging.error(f"Failed to restart script {script_name}: {e}")


def run_diagnostics():
    """Run diagnostics to check system health."""
    try:
        logging.info("Running diagnostics...")
        # Example diagnostic checks
        memory_usage = subprocess.check_output(['free', '-h'], text=True)
        disk_usage = subprocess.check_output(['df', '-h'], text=True)
        logging.info("Diagnostics completed successfully.")
        return f"Memory Usage:\n{memory_usage}\nDisk Usage:\n{disk_usage}"
    except Exception as e:
        logging.error(f"Diagnostics failed: {e}")
        return "Diagnostics failed."


# Additional Backend Utilities


def monitor_backend_processes():
    """Monitor backend processes and their resource usage."""
    try:
        processes = []
        for proc in psutil.process_iter(
            ['pid', 'name', 'cpu_percent', 'memory_info']):
            processes.append(proc.info)
        logging.info("Backend processes monitored successfully.")
        return processes
    except Exception as e:
        logging.error(f"Failed to monitor backend processes: {e}")
        return []


def resolve_resource_conflicts():
    """Resolve resource conflicts by identifying and terminating problematic processes."""
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
            if proc.info['cpu_percent'] > 80:  # Example threshold
                os.kill(proc.info['pid'], 9)
                logging.info(
                    f"Terminated high-CPU process: {proc.info['name']} (PID: {proc.info['pid']})"
                )
    except Exception as e:
        logging.error(f"Failed to resolve resource conflicts: {e}")


def main():
    parser = argparse.ArgumentParser(description="Unstuck Helper Script")
    parser.add_argument("--list-processes",
                        action="store_true",
                        help="List all active processes")
    parser.add_argument("--terminate",
                        type=int,
                        metavar="PID",
                        help="Terminate a process by PID")
    parser.add_argument("--restart",
                        type=str,
                        metavar="SCRIPT",
                        help="Restart a specific script")
    parser.add_argument("--diagnostics",
                        action="store_true",
                        help="Run diagnostics to check system health")
    parser.add_argument("--monitor",
                        action="store_true",
                        help="Monitor backend processes")
    parser.add_argument("--resolve",
                        action="store_true",
                        help="Resolve resource conflicts")

    args = parser.parse_args()

    if args.list_processes:
        print("Active Processes:\n", list_active_processes())
    elif args.terminate:
        terminate_process(args.terminate)
    elif args.restart:
        restart_script(args.restart)
    elif args.diagnostics:
        print("Diagnostics Report:\n", run_diagnostics())
    elif args.monitor:
        backend_processes = monitor_backend_processes()
        for proc in backend_processes:
            print(proc)
    elif args.resolve:
        resolve_resource_conflicts()
    else:
        print("No valid option provided. Use --help for available options.")


if __name__ == "__main__":
    logging.info("Unstuck Helper initialized with backend utilities.")
    main()
