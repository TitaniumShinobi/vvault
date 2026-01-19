import os
import subprocess
import time


def list_open_terminals():
  """List all open terminal sessions."""
  result = subprocess.run(['ps', 'aux'], stdout=subprocess.PIPE, text=True)
  processes = result.stdout.splitlines()
  terminals = [
      line for line in processes if 'Terminal' in line or 'zsh' in line
  ]
  return terminals


def close_terminal(pid):
  """Close a terminal session by its PID."""
  try:
    os.kill(pid, 9)  # Force kill the process
    print(f"Closed terminal with PID: {pid}")
  except ProcessLookupError:
    print(f"No such process with PID: {pid}")
  except Exception as e:
    print(f"Failed to close terminal with PID: {pid}. Error: {e}")


def manage_terminals_autonomously():
  """Automatically close unused terminals."""
  terminals = list_open_terminals()
  if not terminals:
    print("No open terminals found.")
    return

  print("Automatically closing unused terminals...")
  for terminal in terminals:
    pid = int(terminal.split()[1])
    close_terminal(pid)


if __name__ == "__main__":
  while True:
    manage_terminals_autonomously()
    time.sleep(
        60
    )  # Check every 60 seconds# ...existing code from master_terminal_manager.py...
