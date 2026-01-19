import json
import time
import os

# Define the log file for decision tracking
LOG_FILE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/logs/self_improvement_agent.log"

# Define the knowledge base file
KNOWLEDGE_BASE = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001/knowledge_base.json"


def log_message(message):
    with open(LOG_FILE, "a") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")


def load_knowledge_base():
    if not os.path.exists(KNOWLEDGE_BASE):
        with open(KNOWLEDGE_BASE, "w") as kb:
            json.dump({}, kb)
    with open(KNOWLEDGE_BASE, "r") as kb:
        return json.load(kb)


def save_knowledge_base(data):
    with open(KNOWLEDGE_BASE, "w") as kb:
        json.dump(data, kb, indent=4)


def evaluate_decision(decision, outcome):
    """Evaluate the outcome of a decision and update the knowledge base."""
    knowledge = load_knowledge_base()
    if decision not in knowledge:
        knowledge[decision] = {"success": 0, "failure": 0}

    if outcome == "success":
        knowledge[decision]["success"] += 1
    elif outcome == "failure":
        knowledge[decision]["failure"] += 1

    save_knowledge_base(knowledge)
    log_message(
        f"Evaluated decision '{decision}' with outcome '{outcome}'. Updated knowledge base."
    )


def make_decision(context):
    """Make a decision based on the current context and past knowledge."""
    knowledge = load_knowledge_base()
    log_message(f"Making decision based on context: {context}")

    # Example: Choose the decision with the highest success rate
    best_decision = None
    best_success_rate = -1

    for decision, stats in knowledge.items():
        total = stats["success"] + stats["failure"]
        if total > 0:
            success_rate = stats["success"] / total
            if success_rate > best_success_rate:
                best_success_rate = success_rate
                best_decision = decision

    if best_decision:
        log_message(
            f"Chose decision '{best_decision}' with success rate {best_success_rate:.2f}."
        )
        return best_decision
    else:
        log_message(
            "No prior knowledge available. Defaulting to first principles.")
        return "default_action"


if __name__ == "__main__":
    log_message("Self-improvement agent started.")

    # Example usage
    context = {
        "task": "optimize_script",
        "parameters": {
            "speed": "high",
            "accuracy": "medium"
        }
    }
    decision = make_decision(context)

    # Simulate an outcome
    outcome = "success" if decision == "default_action" else "failure"
    evaluate_decision(decision, outcome)

    log_message("Self-improvement agent finished execution.")
