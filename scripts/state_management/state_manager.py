import json
import os

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

if __name__ == "__main__":
    # Example usage
    state_manager = StateManager()

    # Update state
    state_manager.update_state("last_prompt", "What is the meaning of life?")
    state_manager.update_state("last_response", "The meaning of life is subjective and varies for each individual.")

    # Retrieve state
    last_prompt = state_manager.get_state("last_prompt")
    last_response = state_manager.get_state("last_response")

    print("Last Prompt:", last_prompt)
    print("Last Response:", last_response)