import time
import os
import json
from datetime import datetime
import watchdog
import logging

# Configure centralized logging
LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/script_status.log"
logging.basicConfig(filename=LOG_FILE,
                    level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


def log_status(script_name: str, status: str, details: str = ""):
    logging.info(f"{script_name} - {status} - {details}")


def monitor_identity_files():
    identity_files = [
        "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/identity/prompt.json",
        "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/identity/physical_features.json"
    ]

    for file_path in identity_files:
        if not os.path.exists(file_path):
            logging.error(
                f"Identity file {file_path} is missing! Nova must not forget who she is."
            )
            print(
                f"Identity file {file_path} is missing! Nova must not forget who she is."
            )
        else:
            try:
                with open(file_path, 'r') as file:
                    data = json.load(file)
                    logging.info(
                        f"Identity file {file_path} is valid and loaded successfully."
                    )
                    print(
                        f"Identity file {file_path} is valid and loaded successfully."
                    )
                    update_stm_with_identity(file_path, data)
            except json.JSONDecodeError as e:
                logging.error(
                    f"Identity file {file_path} contains invalid JSON! Error: {e}"
                )
                print(f"Identity file {file_path} contains invalid JSON!")


def update_stm_with_identity(file_path, data):
    stm_pool_path = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/stm/short_term_memory.json"
    if not os.path.exists(stm_pool_path):
        with open(stm_pool_path, "w") as f:
            json.dump({}, f)

    with open(stm_pool_path, "r") as f:
        stm_pool = json.load(f)

    stm_pool[file_path] = {
        "last_updated": datetime.now().isoformat(),
        "content_summary": str(data)[:500]  # Store a summary of the data
    }

    with open(stm_pool_path, "w") as f:
        json.dump(stm_pool, f, indent=4)
        logging.info(f"STM pool updated with identity file {file_path}.")

    log_status("nova_identity_guardian", "STM Updated",
               f"Updated STM with {file_path}")


def run_forever():
    print("Nova Identity Guardian is now running 24/7...")
    try:
        while True:
            monitor_identity_files()
            log_status("nova_identity_guardian", "Running",
                       "Identity guardian is active.")
            time.sleep(10)  # Adjust the interval as needed
    except KeyboardInterrupt:
        print("Nova Identity Guardian has been stopped.")


if __name__ == "__main__":
    run_forever()
