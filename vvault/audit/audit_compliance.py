#!/usr/bin/env python3
"""
VVAULT Blockchain Wallet Audit and Compliance

Comprehensive audit logging, compliance reporting, and security monitoring
for the VVAULT blockchain identity wallet.

Author: Devon Allen Woodson
Date: 2025-01-27
Version: 1.0.0
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Union, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum
import sqlite3
import csv
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

class AuditLevel(Enum):
    """Audit log levels"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    SECURITY = "security"

class ComplianceStandard(Enum):
    """Compliance standards"""
    GDPR = "gdpr"
    CCPA = "ccpa"
    SOX = "sox"
    PCI_DSS = "pci_dss"
    HIPAA = "hipaa"
    AML = "aml"
    KYC = "kyc"
    FINRA = "finra"

class RiskLevel(Enum):
    """Risk levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class AuditEvent:
    """Audit event record"""
    event_id: str
    timestamp: str
    user_id: str
    session_id: str
    event_type: str
    event_category: str
    audit_level: AuditLevel
    description: str
    resource: str
    action: str
    result: str
    ip_address: str
    user_agent: str
    metadata: Dict[str, Any]
    integrity_hash: str

@dataclass
class ComplianceReport:
    """Compliance report"""
    report_id: str
    standard: ComplianceStandard
    report_type: str
    generated_at: str
    period_start: str
    period_end: str
    total_events: int
    compliance_score: float
    violations: List[Dict[str, Any]]
    recommendations: List[str]
    metadata: Dict[str, Any]

@dataclass
class SecurityAlert:
    """Security alert"""
    alert_id: str
    timestamp: str
    alert_type: str
    risk_level: RiskLevel
    title: str
    description: str
    affected_resources: List[str]
    mitigation_actions: List[str]
    status: str
    metadata: Dict[str, Any]

class AuditLogger:
    """Audit logging system"""
    
    def __init__(self, audit_db_path: str, log_file_path: str = None):
        self.audit_db_path = audit_db_path
        self.log_file_path = log_file_path or os.path.join(os.path.dirname(audit_db_path), "audit.log")
        
        # Ensure audit directory exists
        os.makedirs(os.path.dirname(audit_db_path), exist_ok=True)
        
        # Initialize database
        self._init_audit_database()
        
        # Initialize logging
        self._init_audit_logging()
        
        logger.info(f"Audit logger initialized: {audit_db_path}")
    
    def _init_audit_database(self):
        """Initialize audit database"""
        conn = sqlite3.connect(self.audit_db_path)
        cursor = conn.cursor()
        
        # Create audit events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_category TEXT NOT NULL,
                audit_level TEXT NOT NULL,
                description TEXT NOT NULL,
                resource TEXT,
                action TEXT NOT NULL,
                result TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                metadata TEXT,
                integrity_hash TEXT NOT NULL
            )
        ''')
        
        # Create compliance reports table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS compliance_reports (
                report_id TEXT PRIMARY KEY,
                standard TEXT NOT NULL,
                report_type TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                total_events INTEGER NOT NULL,
                compliance_score REAL NOT NULL,
                violations TEXT,
                recommendations TEXT,
                metadata TEXT
            )
        ''')
        
        # Create security alerts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS security_alerts (
                alert_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                affected_resources TEXT,
                mitigation_actions TEXT,
                status TEXT NOT NULL,
                metadata TEXT
            )
        ''')
        
        # Create indexes for better performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_events(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_events(event_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_level ON audit_events(audit_level)')
        
        conn.commit()
        conn.close()
    
    def _init_audit_logging(self):
        """Initialize audit file logging"""
        audit_handler = logging.FileHandler(self.log_file_path)
        audit_handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        audit_handler.setFormatter(formatter)
        
        audit_logger = logging.getLogger('audit')
        audit_logger.addHandler(audit_handler)
        audit_logger.setLevel(logging.INFO)
    
    def log_event(
        self,
        user_id: str,
        session_id: str,
        event_type: str,
        event_category: str,
        audit_level: AuditLevel,
        description: str,
        resource: str = None,
        action: str = None,
        result: str = None,
        ip_address: str = None,
        user_agent: str = None,
        metadata: Dict[str, Any] = None
    ) -> str:
        """
        Log an audit event
        
        Args:
            user_id: User identifier
            session_id: Session identifier
            event_type: Type of event
            event_category: Category of event
            audit_level: Audit level
            description: Event description
            resource: Resource affected
            action: Action performed
            result: Result of action
            ip_address: IP address
            user_agent: User agent string
            metadata: Additional metadata
            
        Returns:
            Event ID
        """
        try:
            # Generate event ID
            event_id = self._generate_event_id()
            
            # Create audit event
            audit_event = AuditEvent(
                event_id=event_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_id=user_id,
                session_id=session_id,
                event_type=event_type,
                event_category=event_category,
                audit_level=audit_level,
                description=description,
                resource=resource or "",
                action=action or "",
                result=result or "",
                ip_address=ip_address or "",
                user_agent=user_agent or "",
                metadata=metadata or {},
                integrity_hash=""  # Will be calculated
            )
            
            # Calculate integrity hash
            audit_event.integrity_hash = self._calculate_integrity_hash(audit_event)
            
            # Store in database
            self._store_audit_event(audit_event)
            
            # Log to file
            self._log_to_file(audit_event)
            
            logger.info(f"Audit event logged: {event_id}")
            return event_id
            
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
            return ""
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID"""
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        random_suffix = hashlib.md5(os.urandom(16)).hexdigest()[:8]
        return f"audit_{timestamp}_{random_suffix}"
    
    def _calculate_integrity_hash(self, audit_event: AuditEvent) -> str:
        """Calculate integrity hash for audit event"""
        # Create hashable string from event data
        event_data = f"{audit_event.event_id}{audit_event.timestamp}{audit_event.user_id}{audit_event.event_type}{audit_event.description}{audit_event.action}{audit_event.result}"
        
        # Calculate SHA-256 hash
        hash_obj = hashlib.sha256(event_data.encode())
        return hash_obj.hexdigest()
    
    def _store_audit_event(self, audit_event: AuditEvent):
        """Store audit event in database"""
        conn = sqlite3.connect(self.audit_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO audit_events (
                event_id, timestamp, user_id, session_id, event_type,
                event_category, audit_level, description, resource, action,
                result, ip_address, user_agent, metadata, integrity_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            audit_event.event_id,
            audit_event.timestamp,
            audit_event.user_id,
            audit_event.session_id,
            audit_event.event_type,
            audit_event.event_category,
            audit_event.audit_level.value,
            audit_event.description,
            audit_event.resource,
            audit_event.action,
            audit_event.result,
            audit_event.ip_address,
            audit_event.user_agent,
            json.dumps(audit_event.metadata),
            audit_event.integrity_hash
        ))
        
        conn.commit()
        conn.close()
    
    def _log_to_file(self, audit_event: AuditEvent):
        """Log audit event to file"""
        audit_logger = logging.getLogger('audit')
        
        log_message = (
            f"EVENT_ID={audit_event.event_id} "
            f"USER_ID={audit_event.user_id} "
            f"SESSION_ID={audit_event.session_id} "
            f"EVENT_TYPE={audit_event.event_type} "
            f"CATEGORY={audit_event.event_category} "
            f"LEVEL={audit_event.audit_level.value} "
            f"ACTION={audit_event.action} "
            f"RESULT={audit_event.result} "
            f"RESOURCE={audit_event.resource} "
            f"IP={audit_event.ip_address} "
            f"DESCRIPTION={audit_event.description}"
        )
        
        if audit_event.audit_level == AuditLevel.SECURITY:
            audit_logger.critical(log_message)
        elif audit_event.audit_level == AuditLevel.CRITICAL:
            audit_logger.critical(log_message)
        elif audit_event.audit_level == AuditLevel.ERROR:
            audit_logger.error(log_message)
        elif audit_event.audit_level == AuditLevel.WARNING:
            audit_logger.warning(log_message)
        else:
            audit_logger.info(log_message)
    
    def get_audit_events(
        self,
        start_time: str = None,
        end_time: str = None,
        user_id: str = None,
        event_type: str = None,
        audit_level: AuditLevel = None,
        limit: int = 1000
    ) -> List[AuditEvent]:
        """Get audit events with filters"""
        conn = sqlite3.connect(self.audit_db_path)
        cursor = conn.cursor()
        
        # Build query
        query = "SELECT * FROM audit_events WHERE 1=1"
        params = []
        
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)
        
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        
        if audit_level:
            query += " AND audit_level = ?"
            params.append(audit_level.value)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to AuditEvent objects
        events = []
        for row in rows:
            event = AuditEvent(
                event_id=row[0],
                timestamp=row[1],
                user_id=row[2],
                session_id=row[3],
                event_type=row[4],
                event_category=row[5],
                audit_level=AuditLevel(row[6]),
                description=row[7],
                resource=row[8],
                action=row[9],
                result=row[10],
                ip_address=row[11],
                user_agent=row[12],
                metadata=json.loads(row[13]) if row[13] else {},
                integrity_hash=row[14]
            )
            events.append(event)
        
        conn.close()
        return events
    
    def verify_audit_integrity(self, event_id: str) -> bool:
        """Verify audit event integrity"""
        conn = sqlite3.connect(self.audit_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM audit_events WHERE event_id = ?", (event_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return False
        
        # Recreate event object
        event = AuditEvent(
            event_id=row[0],
            timestamp=row[1],
            user_id=row[2],
            session_id=row[3],
            event_type=row[4],
            event_category=row[5],
            audit_level=AuditLevel(row[6]),
            description=row[7],
            resource=row[8],
            action=row[9],
            result=row[10],
            ip_address=row[11],
            user_agent=row[12],
            metadata=json.loads(row[13]) if row[13] else {},
            integrity_hash=row[14]
        )
        
        # Calculate expected hash
        expected_hash = self._calculate_integrity_hash(event)
        
        conn.close()
        return event.integrity_hash == expected_hash

class ComplianceManager:
    """Compliance management system"""
    
    def __init__(self, audit_logger: AuditLogger):
        self.audit_logger = audit_logger
        self.compliance_rules = self._load_compliance_rules()
    
    def _load_compliance_rules(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load compliance rules for different standards"""
        return {
            ComplianceStandard.GDPR.value: [
                {
                    "rule_id": "gdpr_data_minimization",
                    "description": "Data minimization principle",
                    "check_function": self._check_data_minimization,
                    "severity": "high"
                },
                {
                    "rule_id": "gdpr_consent_management",
                    "description": "Consent management",
                    "check_function": self._check_consent_management,
                    "severity": "high"
                },
                {
                    "rule_id": "gdpr_data_retention",
                    "description": "Data retention policies",
                    "check_function": self._check_data_retention,
                    "severity": "medium"
                }
            ],
            ComplianceStandard.AML.value: [
                {
                    "rule_id": "aml_transaction_monitoring",
                    "description": "Transaction monitoring",
                    "check_function": self._check_transaction_monitoring,
                    "severity": "high"
                },
                {
                    "rule_id": "aml_suspicious_activity",
                    "description": "Suspicious activity reporting",
                    "check_function": self._check_suspicious_activity,
                    "severity": "critical"
                }
            ],
            ComplianceStandard.KYC.value: [
                {
                    "rule_id": "kyc_identity_verification",
                    "description": "Identity verification",
                    "check_function": self._check_identity_verification,
                    "severity": "high"
                },
                {
                    "rule_id": "kyc_document_verification",
                    "description": "Document verification",
                    "check_function": self._check_document_verification,
                    "severity": "high"
                }
            ]
        }
    
    def generate_compliance_report(
        self,
        standard: ComplianceStandard,
        period_start: str,
        period_end: str
    ) -> ComplianceReport:
        """Generate compliance report"""
        try:
            report_id = self._generate_report_id()
            
            # Get audit events for the period
            events = self.audit_logger.get_audit_events(period_start, period_end)
            
            # Run compliance checks
            violations = []
            recommendations = []
            
            if standard.value in self.compliance_rules:
                for rule in self.compliance_rules[standard.value]:
                    rule_violations, rule_recommendations = rule["check_function"](events)
                    violations.extend(rule_violations)
                    recommendations.extend(rule_recommendations)
            
            # Calculate compliance score
            total_checks = len(self.compliance_rules.get(standard.value, []))
            passed_checks = total_checks - len(violations)
            compliance_score = (passed_checks / total_checks * 100) if total_checks > 0 else 100
            
            # Create compliance report
            report = ComplianceReport(
                report_id=report_id,
                standard=standard,
                report_type="automated",
                generated_at=datetime.now(timezone.utc).isoformat(),
                period_start=period_start,
                period_end=period_end,
                total_events=len(events),
                compliance_score=compliance_score,
                violations=violations,
                recommendations=recommendations,
                metadata={
                    "generated_by": "VVAULT",
                    "version": "1.0.0"
                }
            )
            
            # Store report
            self._store_compliance_report(report)
            
            logger.info(f"Compliance report generated: {report_id}")
            return report
            
        except Exception as e:
            logger.error(f"Failed to generate compliance report: {e}")
            return None
    
    def _generate_report_id(self) -> str:
        """Generate unique report ID"""
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        random_suffix = hashlib.md5(os.urandom(16)).hexdigest()[:8]
        return f"compliance_{timestamp}_{random_suffix}"
    
    def _store_compliance_report(self, report: ComplianceReport):
        """Store compliance report in database"""
        conn = sqlite3.connect(self.audit_logger.audit_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO compliance_reports (
                report_id, standard, report_type, generated_at,
                period_start, period_end, total_events, compliance_score,
                violations, recommendations, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            report.report_id,
            report.standard.value,
            report.report_type,
            report.generated_at,
            report.period_start,
            report.period_end,
            report.total_events,
            report.compliance_score,
            json.dumps(report.violations),
            json.dumps(report.recommendations),
            json.dumps(report.metadata)
        ))
        
        conn.commit()
        conn.close()
    
    def _check_data_minimization(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check GDPR data minimization compliance"""
        violations = []
        recommendations = []
        
        # Check for excessive data collection
        data_collection_events = [e for e in events if e.event_type == "data_collection"]
        
        if len(data_collection_events) > 1000:  # Threshold
            violations.append({
                "rule_id": "gdpr_data_minimization",
                "description": "Excessive data collection detected",
                "severity": "high",
                "count": len(data_collection_events)
            })
            recommendations.append("Review data collection practices and implement data minimization")
        
        return violations, recommendations
    
    def _check_consent_management(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check GDPR consent management compliance"""
        violations = []
        recommendations = []
        
        # Check for consent events
        consent_events = [e for e in events if e.event_type == "consent_given"]
        consent_withdrawn_events = [e for e in events if e.event_type == "consent_withdrawn"]
        
        if len(consent_events) == 0:
            violations.append({
                "rule_id": "gdpr_consent_management",
                "description": "No consent management detected",
                "severity": "high"
            })
            recommendations.append("Implement proper consent management system")
        
        return violations, recommendations
    
    def _check_data_retention(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check GDPR data retention compliance"""
        violations = []
        recommendations = []
        
        # Check for data deletion events
        deletion_events = [e for e in events if e.event_type == "data_deletion"]
        
        if len(deletion_events) == 0:
            violations.append({
                "rule_id": "gdpr_data_retention",
                "description": "No data deletion events detected",
                "severity": "medium"
            })
            recommendations.append("Implement data retention and deletion policies")
        
        return violations, recommendations
    
    def _check_transaction_monitoring(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check AML transaction monitoring compliance"""
        violations = []
        recommendations = []
        
        # Check for transaction monitoring events
        monitoring_events = [e for e in events if e.event_type == "transaction_monitoring"]
        
        if len(monitoring_events) == 0:
            violations.append({
                "rule_id": "aml_transaction_monitoring",
                "description": "No transaction monitoring detected",
                "severity": "high"
            })
            recommendations.append("Implement transaction monitoring system")
        
        return violations, recommendations
    
    def _check_suspicious_activity(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check AML suspicious activity compliance"""
        violations = []
        recommendations = []
        
        # Check for suspicious activity events
        suspicious_events = [e for e in events if e.event_type == "suspicious_activity"]
        
        if len(suspicious_events) > 0:
            violations.append({
                "rule_id": "aml_suspicious_activity",
                "description": "Suspicious activity detected",
                "severity": "critical",
                "count": len(suspicious_events)
            })
            recommendations.append("Investigate and report suspicious activities")
        
        return violations, recommendations
    
    def _check_identity_verification(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check KYC identity verification compliance"""
        violations = []
        recommendations = []
        
        # Check for identity verification events
        verification_events = [e for e in events if e.event_type == "identity_verification"]
        
        if len(verification_events) == 0:
            violations.append({
                "rule_id": "kyc_identity_verification",
                "description": "No identity verification detected",
                "severity": "high"
            })
            recommendations.append("Implement identity verification system")
        
        return violations, recommendations
    
    def _check_document_verification(self, events: List[AuditEvent]) -> Tuple[List[Dict], List[str]]:
        """Check KYC document verification compliance"""
        violations = []
        recommendations = []
        
        # Check for document verification events
        doc_verification_events = [e for e in events if e.event_type == "document_verification"]
        
        if len(doc_verification_events) == 0:
            violations.append({
                "rule_id": "kyc_document_verification",
                "description": "No document verification detected",
                "severity": "high"
            })
            recommendations.append("Implement document verification system")
        
        return violations, recommendations

class SecurityMonitor:
    """Security monitoring system"""
    
    def __init__(self, audit_logger: AuditLogger):
        self.audit_logger = audit_logger
        self.alert_thresholds = self._load_alert_thresholds()
        self.active_alerts = {}
    
    def _load_alert_thresholds(self) -> Dict[str, Dict[str, Any]]:
        """Load security alert thresholds"""
        return {
            "failed_login_attempts": {
                "threshold": 5,
                "time_window": 300,  # 5 minutes
                "risk_level": RiskLevel.HIGH
            },
            "unusual_transaction_pattern": {
                "threshold": 10,
                "time_window": 3600,  # 1 hour
                "risk_level": RiskLevel.MEDIUM
            },
            "privilege_escalation": {
                "threshold": 1,
                "time_window": 86400,  # 24 hours
                "risk_level": RiskLevel.CRITICAL
            },
            "data_breach_attempt": {
                "threshold": 1,
                "time_window": 3600,  # 1 hour
                "risk_level": RiskLevel.CRITICAL
            }
        }
    
    def monitor_security_events(self):
        """Monitor security events and generate alerts"""
        try:
            # Get recent security events
            end_time = datetime.now(timezone.utc).isoformat()
            start_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            
            security_events = self.audit_logger.get_audit_events(
                start_time=start_time,
                end_time=end_time,
                audit_level=AuditLevel.SECURITY
            )
            
            # Check for security violations
            for threshold_name, threshold_config in self.alert_thresholds.items():
                self._check_security_threshold(
                    threshold_name,
                    threshold_config,
                    security_events
                )
            
            logger.info("Security monitoring completed")
            
        except Exception as e:
            logger.error(f"Security monitoring failed: {e}")
    
    def _check_security_threshold(
        self,
        threshold_name: str,
        threshold_config: Dict[str, Any],
        events: List[AuditEvent]
    ):
        """Check specific security threshold"""
        # Filter events based on threshold criteria
        relevant_events = self._filter_events_for_threshold(threshold_name, events)
        
        # Check if threshold is exceeded
        if len(relevant_events) >= threshold_config["threshold"]:
            # Generate security alert
            alert = self._generate_security_alert(
                threshold_name,
                threshold_config,
                relevant_events
            )
            
            # Store alert
            self._store_security_alert(alert)
            
            # Log alert
            logger.warning(f"Security alert generated: {alert.alert_id}")
    
    def _filter_events_for_threshold(self, threshold_name: str, events: List[AuditEvent]) -> List[AuditEvent]:
        """Filter events for specific threshold"""
        if threshold_name == "failed_login_attempts":
            return [e for e in events if e.event_type == "login_failed"]
        elif threshold_name == "unusual_transaction_pattern":
            return [e for e in events if e.event_type == "unusual_transaction"]
        elif threshold_name == "privilege_escalation":
            return [e for e in events if e.event_type == "privilege_escalation"]
        elif threshold_name == "data_breach_attempt":
            return [e for e in events if e.event_type == "data_breach_attempt"]
        
        return []
    
    def _generate_security_alert(
        self,
        threshold_name: str,
        threshold_config: Dict[str, Any],
        events: List[AuditEvent]
    ) -> SecurityAlert:
        """Generate security alert"""
        alert_id = self._generate_alert_id()
        
        # Determine alert details based on threshold
        alert_details = self._get_alert_details(threshold_name, events)
        
        alert = SecurityAlert(
            alert_id=alert_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            alert_type=threshold_name,
            risk_level=threshold_config["risk_level"],
            title=alert_details["title"],
            description=alert_details["description"],
            affected_resources=alert_details["affected_resources"],
            mitigation_actions=alert_details["mitigation_actions"],
            status="active",
            metadata={
                "threshold_config": threshold_config,
                "event_count": len(events),
                "generated_by": "VVAULT"
            }
        )
        
        return alert
    
    def _get_alert_details(self, threshold_name: str, events: List[AuditEvent]) -> Dict[str, Any]:
        """Get alert details based on threshold type"""
        if threshold_name == "failed_login_attempts":
            return {
                "title": "Multiple Failed Login Attempts",
                "description": f"Detected {len(events)} failed login attempts",
                "affected_resources": list(set([e.resource for e in events if e.resource])),
                "mitigation_actions": [
                    "Review login attempts",
                    "Consider account lockout",
                    "Investigate potential brute force attack"
                ]
            }
        elif threshold_name == "unusual_transaction_pattern":
            return {
                "title": "Unusual Transaction Pattern",
                "description": f"Detected {len(events)} unusual transactions",
                "affected_resources": list(set([e.resource for e in events if e.resource])),
                "mitigation_actions": [
                    "Review transaction patterns",
                    "Verify transaction legitimacy",
                    "Consider transaction limits"
                ]
            }
        elif threshold_name == "privilege_escalation":
            return {
                "title": "Privilege Escalation Attempt",
                "description": "Detected privilege escalation attempt",
                "affected_resources": list(set([e.resource for e in events if e.resource])),
                "mitigation_actions": [
                    "Immediately revoke elevated privileges",
                    "Investigate user account",
                    "Review access controls"
                ]
            }
        elif threshold_name == "data_breach_attempt":
            return {
                "title": "Data Breach Attempt",
                "description": "Detected potential data breach attempt",
                "affected_resources": list(set([e.resource for e in events if e.resource])),
                "mitigation_actions": [
                    "Immediately secure affected systems",
                    "Investigate breach attempt",
                    "Notify security team"
                ]
            }
        
        return {
            "title": "Security Alert",
            "description": "Security threshold exceeded",
            "affected_resources": [],
            "mitigation_actions": ["Investigate security event"]
        }
    
    def _generate_alert_id(self) -> str:
        """Generate unique alert ID"""
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        random_suffix = hashlib.md5(os.urandom(16)).hexdigest()[:8]
        return f"alert_{timestamp}_{random_suffix}"
    
    def _store_security_alert(self, alert: SecurityAlert):
        """Store security alert in database"""
        conn = sqlite3.connect(self.audit_logger.audit_db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO security_alerts (
                alert_id, timestamp, alert_type, risk_level, title,
                description, affected_resources, mitigation_actions,
                status, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            alert.alert_id,
            alert.timestamp,
            alert.alert_type,
            alert.risk_level.value,
            alert.title,
            alert.description,
            json.dumps(alert.affected_resources),
            json.dumps(alert.mitigation_actions),
            alert.status,
            json.dumps(alert.metadata)
        ))
        
        conn.commit()
        conn.close()
    
    def get_active_alerts(self) -> List[SecurityAlert]:
        """Get active security alerts"""
        conn = sqlite3.connect(self.audit_logger.audit_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM security_alerts WHERE status = 'active' ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        
        alerts = []
        for row in rows:
            alert = SecurityAlert(
                alert_id=row[0],
                timestamp=row[1],
                alert_type=row[2],
                risk_level=RiskLevel(row[3]),
                title=row[4],
                description=row[5],
                affected_resources=json.loads(row[6]) if row[6] else [],
                mitigation_actions=json.loads(row[7]) if row[7] else [],
                status=row[8],
                metadata=json.loads(row[9]) if row[9] else {}
            )
            alerts.append(alert)
        
        conn.close()
        return alerts

# Convenience functions
def create_audit_system(audit_db_path: str) -> Tuple[AuditLogger, ComplianceManager, SecurityMonitor]:
    """Create complete audit and compliance system"""
    audit_logger = AuditLogger(audit_db_path)
    compliance_manager = ComplianceManager(audit_logger)
    security_monitor = SecurityMonitor(audit_logger)
    
    return audit_logger, compliance_manager, security_monitor

if __name__ == "__main__":
    # Example usage
    print("🔍 VVAULT Audit and Compliance System")
    print("=" * 50)
    
    # Create audit system
    audit_logger, compliance_manager, security_monitor = create_audit_system("/tmp/audit.db")
    
    # Log some test events
    audit_logger.log_event(
        user_id="user123",
        session_id="session456",
        event_type="wallet_created",
        event_category="wallet_management",
        audit_level=AuditLevel.INFO,
        description="New wallet created",
        action="create_wallet",
        result="success"
    )
    
    audit_logger.log_event(
        user_id="user123",
        session_id="session456",
        event_type="transaction_sent",
        event_category="transaction",
        audit_level=AuditLevel.INFO,
        description="Transaction sent",
        action="send_transaction",
        result="success",
        resource="0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6"
    )
    
    # Generate compliance report
    end_time = datetime.now(timezone.utc).isoformat()
    start_time = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    
    report = compliance_manager.generate_compliance_report(
        ComplianceStandard.GDPR,
        start_time,
        end_time
    )
    
    if report:
        print(f"\n✅ Compliance report generated: {report.report_id}")
        print(f"   Standard: {report.standard.value}")
        print(f"   Compliance Score: {report.compliance_score:.1f}%")
        print(f"   Violations: {len(report.violations)}")
        print(f"   Recommendations: {len(report.recommendations)}")
    
    # Monitor security events
    security_monitor.monitor_security_events()
    
    # Get active alerts
    alerts = security_monitor.get_active_alerts()
    print(f"\n🔒 Active security alerts: {len(alerts)}")
    
    print("\n✅ Audit and compliance system ready!")


