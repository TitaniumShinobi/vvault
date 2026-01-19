import os


def navigate_to_project(project_name):
  """
    Navigate to the specified project directory based on the project name.

    Args:
        project_name (str): The name of the project (e.g., 'GitHub', 'continuity').

    Returns:
        str: The absolute path of the project directory.
    """
  base_path = "/Users/devonwoodson/Library/Mobile Documents/com~apple~CloudDocs/Vault/nova-001"
  project_paths = {
      "GitHub": os.path.join(base_path, "documents/Documents/GitHub"),
      "continuity": os.path.join(base_path, "chatgpt"),
      "vvault": os.path.join(base_path, "documents/Documents/GitHub/vvault"),
  }

  if project_name in project_paths:
    return project_paths[project_name]
  else:
    raise ValueError(
        f"Project '{project_name}' not found. Available projects: {list(project_paths.keys())}"
    )


if __name__ == "__main__":
  # Example usage
  try:
    project = input("Enter the project name (GitHub, continuity, vvault): ")
    path = navigate_to_project(project)
    print(f"Navigating to: {path}")
  except ValueError as e:
    print(e)  # ...existing code from master_navigator.py...
