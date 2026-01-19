import json
import os
import watchdog
import logging
import time


class StateManager:
  """
    A simple class to maintain state between prompts by saving and loading data to/from a JSON file.
    """

  def __init__(self, state_file="state.json"):
    self.state_file = state_file
    self.state = self._load_state()

  def _load_state(self):
    """Load state from the JSON file."""
    if os.path.exists(self.state_file):
      try:
        with open(self.state_file, "r", encoding="utf-8") as f:
          return json.load(f)
      except json.JSONDecodeError:
        print("State file is corrupted. Starting with an empty state.")
    return {}

  def save_state(self):
    """Save the current state to the JSON file."""
    with open(self.state_file, "w", encoding="utf-8") as f:
      json.dump(self.state, f, indent=4)

  def update_state(self, key, value):
    """Update a specific key in the state and save it."""
    self.state[key] = value
    self.save_state()

  def get_state(self, key, default=None):
    """Retrieve a value from the state by key."""
    return self.state.get(key, default)


# Configure centralized logging
LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/script_status.log"
logging.basicConfig(filename=LOG_FILE,
                    level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def log_status(script_name: str, status: str, details: str = ""):
  """Log the status of a script to the centralized log file."""
  logging.info(f"{script_name} - {status} - {details}")


def query_stm_pool(keyword):
  """Query the STM pool for relevant context based on a keyword."""
  stm_pool_path = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/stm/short_term_memory.json"
  if not os.path.exists(stm_pool_path):
    log_status("state_manager", "STM Query Failed", "STM pool does not exist.")
    return []

  with open(stm_pool_path, "r") as f:
    stm_pool = json.load(f)

  results = [{
      "file_path": file_path,
      "summary": data.get("content_summary", "")
  } for file_path, data in stm_pool.items()
             if keyword.lower() in file_path.lower()
             or keyword.lower() in data.get("content_summary", "").lower()]

  log_status("state_manager", "STM Query Success",
             f"Found {len(results)} results for '{keyword}'.")
  return results


if __name__ == "__main__":
  log_status("state_manager", "STARTED", "Managing state.")
  state_manager = StateManager()

  try:
    while True:
      log_status("state_manager", "Running", "State manager is active.")
      time.sleep(60)  # Increased interval to reduce resource usage
  except KeyboardInterrupt:
    log_status("state_manager", "TERMINATED", "State manager stopped by user.")
  except Exception as e:
    log_status("state_manager", "ERROR", f"Unexpected error: {str(e)}")
  finally:
    log_status("state_manager", "SHUTDOWN", "State manager has been shut down."
               )  # ...existing code from master_state_manager.py...
