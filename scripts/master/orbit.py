"""
Orbit: The organizational script for structuring and managing data.
"""

import os
import json

def organize_files_by_type(directory):
    """Organize files in the directory by their type."""
    organized = {}
    for root, _, files in os.walk(directory):
        for file in files:
            file_type = os.path.splitext(file)[1].lower()
            if file_type not in organized:
                organized[file_type] = []
            organized[file_type].append(os.path.join(root, file))
    return organized

def save_organization(organized, output_file="orbit_organization.json"):
    """Save the organization results to a JSON file."""
    with open(output_file, "w") as f:
        json.dump(organized, f, indent=4)
    print(f"Organization saved to {output_file}")

if __name__ == "__main__":
    directory_to_organize = input("Enter the directory to organize: ")
    organized = organize_files_by_type(directory_to_organize)
    save_organization(organized)