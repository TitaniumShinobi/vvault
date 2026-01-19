import os
import json
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging
import watchdog
import time  # Added to fix missing import for time.sleep
import sys

# Dynamically adjust the Python path to include the parent directory
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from meta.runtime_identity_injection import load_identity_context  # Import after adjusting path

# Configure centralized logging
LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/script_status.log"
logging.basicConfig(filename=LOG_FILE,
                    level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def log_status(script_name: str, status: str, details: str = ""):
    logging.info(f"{script_name} - {status} - {details}")


def update_stm_with_identity(file_path):
    stm_pool_path = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/stm/short_term_memory.json"
    if not os.path.exists(stm_pool_path):
        with open(stm_pool_path, "w") as f:
            json.dump({}, f)

    with open(stm_pool_path, "r") as f:
        stm_pool = json.load(f)

    try:
        with open(file_path, "r") as file:
            data = file.read()
        stm_pool[file_path] = {
            "last_updated": datetime.now().isoformat(),
            "content_summary": data[:500]  # Store a summary of the data
        }
        logging.info(f"STM pool updated with file {file_path}.")
    except Exception as e:
        stm_pool[file_path] = {
            "last_updated": datetime.now().isoformat(),
            "content_summary": f"Error reading file: {e}"
        }
        logging.error(
            f"Failed to update STM pool with file {file_path}. Error: {e}")

    with open(stm_pool_path, "w") as f:
        json.dump(stm_pool, f, indent=4)


class FolderMonitorHandler(FileSystemEventHandler):

    def on_modified(self, event):
        if event.is_directory:
            return  # ignore directory events to avoid read errors
        print(f"File modified: {event.src_path}")
        update_stm_with_identity(event.src_path)
        log_status("folder_monitor", "File Modified",
                   f"Modified: {event.src_path}")

    def on_created(self, event):
        if event.is_directory:
            return
        print(f"File created: {event.src_path}")
        update_stm_with_identity(event.src_path)
        log_status("folder_monitor", "File Created",
                   f"Created: {event.src_path}")

    def on_deleted(self, event):
        if event.is_directory:
            return
        print(f"File deleted: {event.src_path}")
        log_status("folder_monitor", "File Deleted",
                   f"Deleted: {event.src_path}")


def monitor_folder(folder_path):
    logging.info(f"Starting to monitor folder: {folder_path}")
    event_handler = FolderMonitorHandler()
    observer = Observer()
    try:
        observer.schedule(event_handler, folder_path, recursive=True)
        observer.start()
        logging.info(
            f"Monitoring started successfully for folder: {folder_path}")
        while True:
            time.sleep(1)  # avoid a tight loop that pegs CPU
    except Exception as e:
        logging.error(f"Error while monitoring folder {folder_path}: {e}")
        logging.debug("Exception details:", exc_info=True)
    finally:
        observer.stop()
        observer.join()
        logging.info(f"Stopped monitoring folder: {folder_path}")


# Initialize logging
logging.basicConfig(
    filename=
    "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/identity_monitor.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s")


def initialize_identity():
    try:
        logging.info("Initializing identity context...")
        identity_context = load_identity_context()
        logging.info("Identity context loaded successfully.")
        logging.info(f"Loaded Identity Context: {identity_context}")
    except Exception as e:
        logging.error(f"Failed to load identity context: {e}")
        logging.debug("Exception details:", exc_info=True)


# Call initialize_identity at the start of the script
initialize_identity()

print("Starting folder_monitor.py..."
      )  # Debug statement to confirm script execution

if __name__ == "__main__":
    observer = Observer()
    event_handler = FileSystemEventHandler()
    observer.schedule(
        event_handler,
        path=
        "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001",
        recursive=True)
    observer.start()
    try:
        while True:
            log_status("folder_monitor", "Running",
                       "Folder monitor is active.")
            time.sleep(10)  # Adjust the interval as needed
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
