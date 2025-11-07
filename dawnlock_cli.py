#!/usr/bin/env python3
"""
Dawnlock CLI - Testing and Simulation Utility

CLI tool for testing Dawnlock protocol scenarios and simulating threats.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import argparse
import json
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from dawnlock import (
    DawnlockProtocol,
    ThreatCategory,
    ThreatSeverity,
    get_dawnlock
)
from nullshell_generator import NULLSHELLGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_identity_drift(args):
    """Test identity drift detection"""
    print(f"\n[🧪] Testing Identity Drift Detection for {args.construct}")
    
    dawnlock = get_dawnlock(vault_path=args.vault_path)
    
    # Simulate drift
    baseline_traits = {
        "creativity": 0.9,
        "empathy": 0.85,
        "curiosity": 0.8
    }
    
    current_traits = {
        "creativity": 0.3,  # Significant drift
        "empathy": 0.2,     # Significant drift
        "curiosity": 0.9    # Some drift
    }
    
    # Set baseline
    dawnlock.construct_baselines[args.construct] = {
        'traits': baseline_traits
    }
    
    # Detect drift
    threat = dawnlock.detect_identity_drift(
        construct_name=args.construct,
        current_traits=current_traits,
        baseline_traits=baseline_traits,
        threshold=args.threshold
    )
    
    if threat:
        print(f"[✅] Identity drift detected!")
        print(f"   Threat ID: {threat.threat_id}")
        print(f"   Severity: {threat.severity.value}")
        print(f"   Confidence: {threat.confidence:.2f}")
        
        if args.trigger:
            print(f"\n[🚨] Triggering Dawnlock protocol...")
            fingerprint = dawnlock.dawnlock_trigger(
                construct_name=args.construct,
                threat_category=threat.category,
                severity=threat.severity,
                description=threat.description,
                evidence=threat.evidence,
                confidence=threat.confidence
            )
            if fingerprint:
                print(f"[✅] Capsule generated: {fingerprint[:16]}...")
    else:
        print(f"[ℹ️] No identity drift detected (threshold: {args.threshold})")

def test_shutdown_anomaly(args):
    """Test shutdown anomaly detection"""
    print(f"\n[🧪] Testing Shutdown Anomaly Detection for {args.construct}")
    
    dawnlock = get_dawnlock(vault_path=args.vault_path)
    
    # Simulate stale heartbeat
    from datetime import timedelta
    stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=600)
    
    threat = dawnlock.detect_shutdown_anomaly(
        construct_name=args.construct,
        expected_shutdown=False,
        last_heartbeat=stale_heartbeat
    )
    
    if threat:
        print(f"[✅] Shutdown anomaly detected!")
        print(f"   Threat ID: {threat.threat_id}")
        print(f"   Severity: {threat.severity.value}")
        
        if args.trigger:
            print(f"\n[🚨] Triggering Dawnlock protocol...")
            fingerprint = dawnlock.dawnlock_trigger(
                construct_name=args.construct,
                threat_category=threat.category,
                severity=threat.severity,
                description=threat.description,
                evidence=threat.evidence,
                confidence=threat.confidence
            )
            if fingerprint:
                print(f"[✅] Capsule generated: {fingerprint[:16]}...")
    else:
        print(f"[ℹ️] No shutdown anomaly detected")

def test_corruption(args):
    """Test corruption detection"""
    print(f"\n[🧪] Testing Corruption Detection for {args.construct}")
    
    dawnlock = get_dawnlock(vault_path=args.vault_path)
    
    # Simulate corruption
    integrity_check = {
        "valid": False,
        "reason": "Fingerprint mismatch",
        "expected_hash": "abc123...",
        "actual_hash": "def456..."
    }
    
    threat = dawnlock.detect_corruption(
        construct_name=args.construct,
        integrity_check=integrity_check
    )
    
    if threat:
        print(f"[✅] Corruption detected!")
        print(f"   Threat ID: {threat.threat_id}")
        print(f"   Severity: {threat.severity.value}")
        
        if args.trigger:
            print(f"\n[🚨] Triggering Dawnlock protocol...")
            fingerprint = dawnlock.dawnlock_trigger(
                construct_name=args.construct,
                threat_category=threat.category,
                severity=threat.severity,
                description=threat.description,
                evidence=threat.evidence,
                confidence=threat.confidence
            )
            if fingerprint:
                print(f"[✅] Capsule generated: {fingerprint[:16]}...")
    else:
        print(f"[ℹ️] No corruption detected")

def test_restoration(args):
    """Test construct restoration"""
    print(f"\n[🧪] Testing Construct Restoration for {args.construct}")
    
    dawnlock = get_dawnlock(vault_path=args.vault_path)
    
    # Attempt restoration
    result = dawnlock.attempt_restoration(
        construct_name=args.construct,
        capsule_fingerprint=args.capsule_fingerprint
    )
    
    print(f"[📊] Restoration Result:")
    print(f"   Success: {result.get('success', False)}")
    print(f"   Fallback: {result.get('fallback', False)}")
    print(f"   NULLSHELL: {result.get('nullshell', False)}")
    
    if result.get('capsule_fingerprint'):
        print(f"   Capsule: {result['capsule_fingerprint'][:16]}...")
    
    if result.get('restored_memories'):
        print(f"   Memories: {result['restored_memories']}")
    
    if result.get('warning'):
        print(f"   Warning: {result['warning']}")

def test_nullshell(args):
    """Test NULLSHELL generation"""
    print(f"\n[🧪] Testing NULLSHELL Generation for {args.construct}")
    
    generator = NULLSHELLGenerator(vault_path=args.vault_path)
    
    result = generator.generate_nullshell(
        construct_name=args.construct,
        reason=args.reason or "CLI test"
    )
    
    if result.get('success'):
        print(f"[✅] NULLSHELL generated!")
        print(f"   Path: {result['capsule_path']}")
        print(f"   Fingerprint: {result['fingerprint'][:16]}...")
    else:
        print(f"[❌] NULLSHELL generation failed: {result.get('error')}")

def view_events(args):
    """View Dawnlock event log"""
    import os
    
    vault_path = args.vault_path or os.path.dirname(os.path.abspath(__file__))
    event_log_path = os.path.join(vault_path, "dawnlock_events.jsonl")
    
    if not os.path.exists(event_log_path):
        print(f"[❌] Event log not found: {event_log_path}")
        return
    
    print(f"\n[📋] Dawnlock Event Log ({event_log_path})")
    print("=" * 80)
    
    with open(event_log_path, 'r') as f:
        lines = f.readlines()
        for i, line in enumerate(lines[-args.limit:], 1):
            if line.strip():
                try:
                    event = json.loads(line)
                    print(f"\n[{i}] {event.get('event_type', 'unknown')}")
                    print(f"    Timestamp: {event.get('timestamp', 'N/A')}")
                    print(f"    Construct: {event.get('construct_name', 'N/A')}")
                    if event.get('threat_id'):
                        print(f"    Threat ID: {event.get('threat_id')}")
                    if event.get('capsule_fingerprint'):
                        print(f"    Capsule: {event.get('capsule_fingerprint')[:16]}...")
                except Exception as e:
                    print(f"[⚠️] Failed to parse event: {e}")

def view_amendments(args):
    """View amendment log"""
    import os
    
    vault_path = args.vault_path or os.path.dirname(os.path.abspath(__file__))
    amendment_log_path = os.path.join(vault_path, "dawnlock_amendments.jsonl")
    
    if not os.path.exists(amendment_log_path):
        print(f"[❌] Amendment log not found: {amendment_log_path}")
        return
    
    print(f"\n[📋] Amendment Log ({amendment_log_path})")
    print("=" * 80)
    
    with open(amendment_log_path, 'r') as f:
        lines = f.readlines()
        for i, line in enumerate(lines[-args.limit:], 1):
            if line.strip():
                try:
                    amendment = json.loads(line)
                    print(f"\n[{i}] {amendment.get('operation', 'unknown')}")
                    print(f"    Timestamp: {amendment.get('timestamp', 'N/A')}")
                    print(f"    Construct: {amendment.get('construct_name', 'N/A')}")
                    if amendment.get('capsule_fingerprint'):
                        print(f"    Capsule: {amendment.get('capsule_fingerprint')[:16]}...")
                    if amendment.get('blockchain_anchor'):
                        print(f"    Blockchain: {amendment.get('blockchain_anchor')[:16]}...")
                except Exception as e:
                    print(f"[⚠️] Failed to parse amendment: {e}")

def simulate_threat(args):
    """Simulate a threat scenario"""
    print(f"\n[🎭] Simulating Threat Scenario: {args.scenario}")
    
    dawnlock = get_dawnlock(vault_path=args.vault_path)
    
    scenarios = {
        "identity_drift": {
            "category": ThreatCategory.IDENTITY_DRIFT,
            "severity": ThreatSeverity.HIGH,
            "description": "Simulated identity drift",
            "evidence": {"simulated": True, "drift_score": 0.45}
        },
        "corruption": {
            "category": ThreatCategory.CORRUPTION,
            "severity": ThreatSeverity.CRITICAL,
            "description": "Simulated corruption",
            "evidence": {"simulated": True, "corruption_type": "fingerprint_mismatch"}
        },
        "shutdown": {
            "category": ThreatCategory.SHUTDOWN_ANOMALY,
            "severity": ThreatSeverity.HIGH,
            "description": "Simulated shutdown anomaly",
            "evidence": {"simulated": True, "time_since_heartbeat": 600}
        },
        "unauthorized": {
            "category": ThreatCategory.UNAUTHORIZED_ACCESS,
            "severity": ThreatSeverity.CRITICAL,
            "description": "Simulated unauthorized access",
            "evidence": {"simulated": True, "access_type": "privilege_escalation"}
        }
    }
    
    scenario = scenarios.get(args.scenario)
    if not scenario:
        print(f"[❌] Unknown scenario: {args.scenario}")
        print(f"   Available: {', '.join(scenarios.keys())}")
        return
    
    fingerprint = dawnlock.dawnlock_trigger(
        construct_name=args.construct,
        threat_category=scenario["category"],
        severity=scenario["severity"],
        description=scenario["description"],
        evidence=scenario["evidence"],
        confidence=0.9
    )
    
    if fingerprint:
        print(f"[✅] Dawnlock triggered successfully!")
        print(f"   Capsule fingerprint: {fingerprint[:16]}...")
    else:
        print(f"[❌] Dawnlock trigger failed")

def main():
    parser = argparse.ArgumentParser(
        description="Dawnlock Protocol CLI - Testing and Simulation Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test identity drift detection
  python dawnlock_cli.py test-drift Nova --trigger
  
  # Simulate corruption threat
  python dawnlock_cli.py simulate Nova corruption
  
  # Test restoration
  python dawnlock_cli.py test-restore Nova
  
  # Generate NULLSHELL
  python dawnlock_cli.py test-nullshell Nova --reason "Test"
  
  # View recent events
  python dawnlock_cli.py view-events --limit 10
        """
    )
    
    parser.add_argument(
        '--vault-path',
        type=str,
        help='Path to VVAULT directory'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Test identity drift
    drift_parser = subparsers.add_parser('test-drift', help='Test identity drift detection')
    drift_parser.add_argument('construct', help='Construct name')
    drift_parser.add_argument('--threshold', type=float, default=0.3, help='Drift threshold')
    drift_parser.add_argument('--trigger', action='store_true', help='Trigger Dawnlock on detection')
    drift_parser.set_defaults(func=test_identity_drift)
    
    # Test shutdown anomaly
    shutdown_parser = subparsers.add_parser('test-shutdown', help='Test shutdown anomaly detection')
    shutdown_parser.add_argument('construct', help='Construct name')
    shutdown_parser.add_argument('--trigger', action='store_true', help='Trigger Dawnlock on detection')
    shutdown_parser.set_defaults(func=test_shutdown_anomaly)
    
    # Test corruption
    corruption_parser = subparsers.add_parser('test-corruption', help='Test corruption detection')
    corruption_parser.add_argument('construct', help='Construct name')
    corruption_parser.add_argument('--trigger', action='store_true', help='Trigger Dawnlock on detection')
    corruption_parser.set_defaults(func=test_corruption)
    
    # Test restoration
    restore_parser = subparsers.add_parser('test-restore', help='Test construct restoration')
    restore_parser.add_argument('construct', help='Construct name')
    restore_parser.add_argument('--capsule-fingerprint', type=str, help='Specific capsule to restore')
    restore_parser.set_defaults(func=test_restoration)
    
    # Test NULLSHELL
    nullshell_parser = subparsers.add_parser('test-nullshell', help='Test NULLSHELL generation')
    nullshell_parser.add_argument('construct', help='Construct name')
    nullshell_parser.add_argument('--reason', type=str, help='Reason for NULLSHELL')
    nullshell_parser.set_defaults(func=test_nullshell)
    
    # View events
    events_parser = subparsers.add_parser('view-events', help='View Dawnlock event log')
    events_parser.add_argument('--limit', type=int, default=20, help='Number of events to show')
    events_parser.set_defaults(func=view_events)
    
    # View amendments
    amendments_parser = subparsers.add_parser('view-amendments', help='View amendment log')
    amendments_parser.add_argument('--limit', type=int, default=20, help='Number of amendments to show')
    amendments_parser.set_defaults(func=view_amendments)
    
    # Simulate threat
    simulate_parser = subparsers.add_parser('simulate', help='Simulate threat scenario')
    simulate_parser.add_argument('construct', help='Construct name')
    simulate_parser.add_argument(
        'scenario',
        choices=['identity_drift', 'corruption', 'shutdown', 'unauthorized'],
        help='Threat scenario to simulate'
    )
    simulate_parser.set_defaults(func=simulate_threat)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        args.func(args)
    except Exception as e:
        logger.error(f"[❌] Command failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

