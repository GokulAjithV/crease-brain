"""
Utility script to test connection and document indexing to your cloud-hosted OpenSearch cluster.
"""

import os
import sys
import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

sys.path.append(os.path.dirname(__file__))
from services.opensearch_logger import (
    OPENSEARCH_HOST,
    OPENSEARCH_USERNAME,
    OPENSEARCH_PASSWORD,
    OPENSEARCH_VERIFY_CERTS,
    OPENSEARCH_INDEX_PREFIX,
    _init_opensearch_client
)

print("=" * 60)
print("  CREASE Brain — OpenSearch Cloud Connection Diagnostic")
print("=" * 60)
print(f"  Target Host      : {OPENSEARCH_HOST}")
print(f"  Username         : {OPENSEARCH_USERNAME or '(none)'}")
print(f"  Index Prefix     : {OPENSEARCH_INDEX_PREFIX}")
print(f"  Verify SSL Certs : {OPENSEARCH_VERIFY_CERTS}")
print("-" * 60)

if "YOUR_OPENSEARCH_CLOUD_HOST" in OPENSEARCH_HOST:
    print("[!] Action Required: Please update OPENSEARCH_HOST in crease-brain/.env with your actual cloud endpoint!")
    sys.exit(1)

print("[1/3] Connecting to cloud OpenSearch cluster...")
client = _init_opensearch_client()
if not client:
    print("[X] Failed to initialize OpenSearch client.")
    sys.exit(1)

try:
    info = client.info()
    print("[OK] Connected successfully to Cloud OpenSearch Cluster!")
    print(f"     Cluster Name   : {info.get('cluster_name')}")
    print(f"     Version Number : {info.get('version', {}).get('number')}")

    print("\n[2/3] Checking cluster health status...")
    health = client.cluster.health()
    print(f"     Health Status  : {health.get('status').upper()}")
    print(f"     Active Nodes   : {health.get('number_of_nodes')}")

    print("\n[3/3] Sending test diagnostic log record...")
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    test_index = f"{OPENSEARCH_INDEX_PREFIX}-diagnostic"
    
    test_doc = {
        "@timestamp": now_utc,
        "service": "crease-brain",
        "environment": "cloud-test",
        "level": "INFO",
        "logger": "diagnostic.test",
        "message": "OpenSearch cloud connection verification test log from CREASE Brain",
        "telemetry": {
            "method": "TEST",
            "path": "/api/health",
            "status_code": 200,
            "process_time_ms": 1.25
        }
    }

    res = client.index(index=test_index, body=test_doc, refresh=True)
    print(f"[OK] Diagnostic log indexed successfully!")
    print(f"     Index Name  : {res.get('_index')}")
    print(f"     Document ID : {res.get('_id')}")
    print(f"     Result      : {res.get('result')}")
    print("\n" + "=" * 60)
    print(" SUCCESS: Your Cloud OpenSearch cluster is fully configured & working!")
    print("=" * 60)

except Exception as e:
    print(f"\n[X] Error connecting to Cloud OpenSearch: {e}")
    sys.exit(1)
