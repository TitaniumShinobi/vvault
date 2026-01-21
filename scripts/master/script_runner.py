import os
import subprocess
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Define the scripts and their purposes
SCRIPTS = {
    "state_manager.py":
    "Manages state persistently by saving and loading data to/from a JSON file.",
    "folder_monitor.py":
    "Monitors changes in a specific folder and updates the short-term memory pool.",
    "nova_identity_guardian.py":
    "Monitors identity files for integrity and updates the short-term memory pool.",
    "independence.py":
    "Runs and monitors multiple scripts, restarting them if they terminate.",
    "aviator_script.py":
    "Manages and monitors scripts, providing logging and process management.",
    "unstuck_helper.py":
    "Lists and terminates background processes to prevent resource conflicts."
}

# Define the directory containing the scripts
IDENTITY_FOLDER = os.path.dirname(os.path.abspath(__file__))


# Function to check if a script exists
def script_exists(script_name):
  return os.path.isfile(os.path.join(IDENTITY_FOLDER, script_name))


# Function to create a missing script
def create_script(script_name, description):
  script_path = os.path.join(IDENTITY_FOLDER, script_name)
  with open(script_path, "w") as script_file:
    script_file.write(f"""
# {script_name}
# Purpose: {description}

if __name__ == '__main__':
    print('{script_name} is a placeholder script.')
""")
  os.chmod(script_path, 0o755)  # Make the script executable
  logging.info(f"Created missing script: {script_name}")


# Function to run a script
def run_script(script_name):
  script_path = os.path.join(IDENTITY_FOLDER, script_name)
  try:
    process = subprocess.Popen(["python3", script_path],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    logging.info(f"Started script: {script_name} with PID {process.pid}")
    return process
  except Exception as e:
    logging.error(f"Failed to start script {script_name}: {e}")


# Main function
if __name__ == "__main__":
  processes = []
  try:
    for script, description in SCRIPTS.items():
      if not script_exists(script):
        create_script(script, description)
      process = run_script(script)
      if process:
        processes.append((script, process))

    logging.info("All scripts are running.")

    # Ensure Unstuck Helper is always handy
    if not script_exists("unstuck_helper.py"):
      create_script(
          "unstuck_helper.py",
          "Manages construct-related tasks, such as handling terminals and resolving runtime issues."
      )

    # Add Unstuck Helper to the monitored scripts
    run_script("unstuck_helper.py")
    logging.info("Unstuck Helper is ready and running.")

    # Monitor the processes
    while True:
      for script, process in processes:
        if process.poll() is not None:  # Process has terminated
          logging.info(
              f"{script} terminated with exit code {process.returncode}")
          processes.remove((script, process))

          # Restart the script
          try:
            new_process = subprocess.Popen(
                ["python3", os.path.join(IDENTITY_FOLDER, script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
            processes.append((script, new_process))
            logging.info(f"Restarted {script} with PID {new_process.pid}")
          except Exception as e:
            logging.error(f"Failed to restart {script}: {e}")

      time.sleep(5)  # Check every 5 seconds
  except KeyboardInterrupt:
    logging.info("Shutting down all scripts.")
    for _, process in processes:
      process.terminate()  # ...existing code from master_script_runner.py...
