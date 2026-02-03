"""
Nautilus: The deep view script for introspection and analysis.
"""

import os
import json

def deep_scan(directory):
    """Perform a deep scan of the directory to analyze internal states."""
    results = {}
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                with open(file_path, 'r', errors='ignore') as f:
                    content = f.read()
                    results[file_path] = {
                        "size": os.path.getsize(file_path),
                        "lines": len(content.splitlines()),
                    }
            except Exception as e:
                results[file_path] = {"error": str(e)}
    return results

def save_results(results, output_file="nautilus_results.json"):
    """Save the deep scan results to a JSON file."""
    with open(output_file, "w") as f:
        json.dump(results, f, indent=4)
    print(f"Results saved to {output_file}")

if __name__ == "__main__":
    directory_to_scan = input("Enter the directory to scan: ")
    results = deep_scan(directory_to_scan)
    save_results(results)