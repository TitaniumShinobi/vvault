# Terminal/memory_check.py

import os
from datetime import datetime, timedelta
import dateutil.parser  # pip install python-dateutil
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from Terminal.logger import setup_logger
from .chroma_config import get_chroma_client, get_collection

frame_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
LOGS_FOLDER = os.path.join(frame_ROOT, "Vault", "Logs")
os.makedirs(LOGS_FOLDER, exist_ok=True)

logger = setup_logger("memory_check", os.path.join(LOGS_FOLDER, "memory_check.log"))

def main():
    client = get_chroma_client()

    collections = client.list_collections()
    if not collections:
        print("No collections found in the vault.")
        return

    short_count = 0
    long_count = 0
    short_recent = 0
    short_old = 0
    now = datetime.now()
    window = timedelta(days=7)

    print(f"Found {len(collections)} collection(s):")
    for col in collections:
        print(f"\nCollection: {col.name}")
        c = get_collection(col.name)
        d = c.get()
        docs = d['documents']
        metas = d['metadatas']
        print(f"  Total memories: {len(docs)}")

        if col.name == "short_term_memory":
            for meta in metas:
                ts = meta.get("timestamp")
                if ts:
                    try:
                        dt = dateutil.parser.parse(ts)
                        if now - dt <= window:
                            short_recent += 1
                        else:
                            short_old += 1
                    except Exception:
                        # If timestamp is missing or invalid, count as old
                        short_old += 1
                else:
                    short_old += 1
            short_count = len(docs)
            print(f"    Recent (<=7d): {short_recent}")
            print(f"    Old (>7d):    {short_old}")
        elif col.name == "long_term_memory":
            long_count = len(docs)

    print("\nSummary:")
    print(f"  Short-term memories: {short_count} (recent: {short_recent} | old: {short_old})")
    print(f"  Long-term memories:  {long_count}")

if __name__ == "__main__":
    main()