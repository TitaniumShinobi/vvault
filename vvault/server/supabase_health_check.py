from dotenv import load_dotenv
import os
import time
import sys
import json

try:
    from supabase import create_client, SupabaseException
except Exception as e:
    print(json.dumps({"status": "error", "reason": f"Supabase import failed: {e}"}))
    sys.exit(2)


load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

if not url or not key:
    print(json.dumps({"status": "error", "reason": "Missing env vars"}))
    sys.exit(1)

try:
    start = time.time()
    supa = create_client(url, key)
    data = supa.table("pg_catalog.pg_tables").select("schemaname").limit(1).execute()
    _ = data  # ensure request is executed
    ms = int((time.time() - start) * 1000)
    project = url.split("//")[-1].split(".")[0]
    print(json.dumps({"status": "ok", "project": project, "latency_ms": ms}))
except SupabaseException as e:
    print(json.dumps({"status": "error", "reason": str(e)}))
    sys.exit(3)
except Exception as e:
    print(json.dumps({"status": "error", "reason": str(e)}))
    sys.exit(4)
