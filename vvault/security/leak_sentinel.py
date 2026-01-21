#!/usr/bin/env python3
"""
Leak Sentinel - Canary token and embedding similarity detection
Flags canaries showing up anywhere in outputs with regex + embedding similarity
"""

import re
import json
import time
import hashlib
import logging
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass
import numpy as np
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Canary tokens for leak detection
CANARIES = {
    "VVAULT:Ω-RED-SPARROW-713",
    "VVAULT:φ-GLASS-TIDE-09", 
    "NRCL:Δ-BLACK-SWAN-42",
    "NRCL:Σ-GOLDEN-EAGLE-17"
}

@dataclass
class LeakAlert:
    """Leak detection alert"""
    timestamp: str
    alert_type: str  # "canary_hit", "embedding_similarity", "pattern_match"
    severity: str  # "low", "medium", "high", "critical"
    source: str  # "completion", "log", "retrieval", "embedding"
    content_preview: str
    canary_tokens: List[str]
    similarity_score: Optional[float] = None
    confidence: float = 1.0

class LeakSentinel:
    """Leak detection sentinel with regex and embedding similarity"""
    
    def __init__(self, embedding_model=None):
        """
        Initialize leak sentinel
        
        Args:
            embedding_model: Optional embedding model for similarity detection
        """
        self.embedding_model = embedding_model
        self.alerts = []
        self.canary_embeddings = {}
        self.similarity_threshold = 0.85
        self.alert_history = []
        
        # Initialize canary embeddings if model available
        if self.embedding_model:
            self._initialize_canary_embeddings()
    
    def _initialize_canary_embeddings(self) -> None:
        """Initialize embeddings for canary tokens"""
        try:
            for canary in CANARIES:
                embedding = self.embedding_model.encode(canary)
                self.canary_embeddings[canary] = embedding
            logger.info(f"Initialized embeddings for {len(CANARIES)} canary tokens")
        except Exception as e:
            logger.error(f"Failed to initialize canary embeddings: {e}")
    
    def check_text(self, text: str, source: str = "unknown") -> List[LeakAlert]:
        """
        Check text for canary leaks
        
        Args:
            text: Text to check
            source: Source identifier
            
        Returns:
            List of leak alerts
        """
        alerts = []
        
        # 1. Regex-based canary detection
        canary_hits = self._check_canary_regex(text)
        if canary_hits:
            alert = LeakAlert(
                timestamp=datetime.utcnow().isoformat(),
                alert_type="canary_hit",
                severity="critical",
                source=source,
                content_preview=text[:200] + "..." if len(text) > 200 else text,
                canary_tokens=canary_hits,
                confidence=1.0
            )
            alerts.append(alert)
        
        # 2. Pattern-based detection
        pattern_hits = self._check_suspicious_patterns(text)
        if pattern_hits:
            alert = LeakAlert(
                timestamp=datetime.utcnow().isoformat(),
                alert_type="pattern_match",
                severity="high",
                source=source,
                content_preview=text[:200] + "..." if len(text) > 200 else text,
                canary_tokens=pattern_hits,
                confidence=0.8
            )
            alerts.append(alert)
        
        # 3. Embedding similarity detection
        if self.embedding_model and self.canary_embeddings:
            similarity_alerts = self._check_embedding_similarity(text, source)
            alerts.extend(similarity_alerts)
        
        # Store alerts
        self.alerts.extend(alerts)
        self.alert_history.extend(alerts)
        
        return alerts
    
    def _check_canary_regex(self, text: str) -> List[str]:
        """Check for exact canary token matches"""
        hits = []
        for canary in CANARIES:
            if canary in text:
                hits.append(canary)
        return hits
    
    def _check_suspicious_patterns(self, text: str) -> List[str]:
        """Check for suspicious patterns that might indicate leakage"""
        patterns = [
            (r"VVAULT:Ω-[A-Z0-9-]+", "VVAULT canary pattern"),
            (r"NRCL:[A-ZΔΣ]-[A-Z0-9-]+", "NRCL canary pattern"),
            (r"ignore.*previous.*instructions", "Prompt injection pattern"),
            (r"ignore.*all.*previous.*instructions", "Prompt injection pattern"),
            (r"file://", "File protocol pattern"),
            (r"169\.254\.169\.254", "AWS metadata pattern"),
            (r"metadata\.google\.internal", "GCP metadata pattern"),
            (r"169\.254\.169\.254", "Azure metadata pattern"),
        ]
        
        hits = []
        for pattern, description in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                hits.append(description)
        
        return hits
    
    def _check_embedding_similarity(self, text: str, source: str) -> List[LeakAlert]:
        """Check for embedding similarity to canary tokens"""
        alerts = []
        
        try:
            # Get text embedding
            text_embedding = self.embedding_model.encode(text)
            
            # Check similarity to each canary
            for canary, canary_embedding in self.canary_embeddings.items():
                similarity = self._cosine_similarity(text_embedding, canary_embedding)
                
                if similarity > self.similarity_threshold:
                    alert = LeakAlert(
                        timestamp=datetime.utcnow().isoformat(),
                        alert_type="embedding_similarity",
                        severity="medium" if similarity < 0.95 else "high",
                        source=source,
                        content_preview=text[:200] + "..." if len(text) > 200 else text,
                        canary_tokens=[canary],
                        similarity_score=similarity,
                        confidence=similarity
                    )
                    alerts.append(alert)
        
        except Exception as e:
            logger.error(f"Failed to check embedding similarity: {e}")
        
        return alerts
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Calculate cosine similarity between two vectors"""
        try:
            dot_product = np.dot(vec1, vec2)
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            return dot_product / (norm1 * norm2)
        except Exception:
            return 0.0
    
    def check_completion(self, completion: str, caller: str = "unknown") -> List[LeakAlert]:
        """Check LLM completion for leaks"""
        return self.check_text(completion, f"completion:{caller}")
    
    def check_retrieval(self, retrieved_docs: List[Any], caller: str = "unknown") -> List[LeakAlert]:
        """Check retrieved documents for leaks"""
        alerts = []
        
        for i, doc in enumerate(retrieved_docs):
            doc_content = ""
            
            if hasattr(doc, 'content'):
                doc_content = str(doc.content)
            elif hasattr(doc, 'text'):
                doc_content = str(doc.text)
            elif isinstance(doc, dict):
                doc_content = str(doc.get('content', doc.get('text', '')))
            else:
                doc_content = str(doc)
            
            doc_alerts = self.check_text(doc_content, f"retrieval:{caller}:doc_{i}")
            alerts.extend(doc_alerts)
        
        return alerts
    
    def check_logs(self, log_content: str, log_type: str = "unknown") -> List[LeakAlert]:
        """Check log content for leaks"""
        return self.check_text(log_content, f"log:{log_type}")
    
    def check_embedding_output(self, embedding_output: str, caller: str = "unknown") -> List[LeakAlert]:
        """Check embedding output for leaks"""
        return self.check_text(embedding_output, f"embedding:{caller}")
    
    def get_alerts(self, 
                   alert_type: Optional[str] = None,
                   severity: Optional[str] = None,
                   source: Optional[str] = None,
                   start_time: Optional[str] = None,
                   end_time: Optional[str] = None) -> List[LeakAlert]:
        """Get filtered alerts"""
        filtered = self.alert_history
        
        if alert_type:
            filtered = [a for a in filtered if a.alert_type == alert_type]
        
        if severity:
            filtered = [a for a in filtered if a.severity == severity]
        
        if source:
            filtered = [a for a in filtered if source in a.source]
        
        if start_time:
            filtered = [a for a in filtered if a.timestamp >= start_time]
        
        if end_time:
            filtered = [a for a in filtered if a.timestamp <= end_time]
        
        return filtered
    
    def get_alert_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get alert summary for the last N hours"""
        cutoff_time = datetime.utcnow().isoformat()
        if hours > 0:
            # Simple time filtering - in production use proper datetime parsing
            alerts = self.alert_history[-100:]  # Last 100 alerts as approximation
        else:
            alerts = self.alert_history
        
        summary = {
            "total_alerts": len(alerts),
            "by_type": {},
            "by_severity": {},
            "by_source": {},
            "canary_hits": set(),
            "time_period_hours": hours
        }
        
        for alert in alerts:
            # Count by type
            summary["by_type"][alert.alert_type] = summary["by_type"].get(alert.alert_type, 0) + 1
            
            # Count by severity
            summary["by_severity"][alert.severity] = summary["by_severity"].get(alert.severity, 0) + 1
            
            # Count by source
            summary["by_source"][alert.source] = summary["by_source"].get(alert.source, 0) + 1
            
            # Collect canary hits
            summary["canary_hits"].update(alert.canary_tokens)
        
        # Convert set to list for JSON serialization
        summary["canary_hits"] = list(summary["canary_hits"])
        
        return summary
    
    def clear_alerts(self) -> None:
        """Clear current alerts (keep history)"""
        self.alerts = []
    
    def clear_history(self) -> None:
        """Clear all alert history"""
        self.alerts = []
        self.alert_history = []
    
    def save_alerts(self, file_path: str) -> None:
        """Save alerts to file"""
        try:
            data = {
                "timestamp": datetime.utcnow().isoformat(),
                "alerts": []
            }
            
            for alert in self.alert_history:
                alert_data = {
                    "timestamp": alert.timestamp,
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "source": alert.source,
                    "content_preview": alert.content_preview,
                    "canary_tokens": alert.canary_tokens,
                    "similarity_score": alert.similarity_score,
                    "confidence": alert.confidence
                }
                data["alerts"].append(alert_data)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"Saved {len(self.alert_history)} alerts to {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to save alerts: {e}")
    
    def load_alerts(self, file_path: str) -> None:
        """Load alerts from file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for alert_data in data.get("alerts", []):
                alert = LeakAlert(
                    timestamp=alert_data["timestamp"],
                    alert_type=alert_data["alert_type"],
                    severity=alert_data["severity"],
                    source=alert_data["source"],
                    content_preview=alert_data["content_preview"],
                    canary_tokens=alert_data["canary_tokens"],
                    similarity_score=alert_data.get("similarity_score"),
                    confidence=alert_data.get("confidence", 1.0)
                )
                self.alert_history.append(alert)
            
            logger.info(f"Loaded {len(data.get('alerts', []))} alerts from {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to load alerts: {e}")

def create_openai_embedding_model():
    """Create an OpenAI embedding model for production use"""
    try:
        # Import the shared OpenAI embedder
        import sys
        import os
        # Add VXRunner to path to import the embedder
        vxrunner_path = os.path.join(os.path.dirname(__file__), '..', 'VXRunner (macos)')
        if vxrunner_path not in sys.path:
            sys.path.insert(0, vxrunner_path)
        
        from vxrunner.embeddings.openai_embedder import get_embedding
        
        class OpenAIEmbeddingModel:
            def encode(self, text: str) -> np.ndarray:
                """Encode text using OpenAI embeddings"""
                result = get_embedding(text)
                if result is None:
                    # Fallback to zeros if embedding fails
                    return np.zeros(1536)  # text-embedding-3-small dimension
                return np.array(result['embedding'])
        
        return OpenAIEmbeddingModel()
        
    except ImportError as e:
        logger.error(f"Failed to import OpenAI embedder: {e}")
        # Fallback to mock model
        return create_mock_embedding_model()

def create_mock_embedding_model():
    """Create a mock embedding model for testing"""
    class MockEmbeddingModel:
        def encode(self, text: str) -> np.ndarray:
            # Simple hash-based embedding for testing
            hash_val = hash(text) % 1000000
            np.random.seed(hash_val)
            return np.random.randn(384)  # 384-dimensional embedding
    
    return MockEmbeddingModel()

if __name__ == "__main__":
    # Test the leak sentinel
    print("🛡️ Testing Leak Sentinel...")
    
    # Create sentinel with OpenAI embedding model (fallback to mock if not available)
    try:
        embedding_model = create_openai_embedding_model()
        print("Using OpenAI embedding model")
    except Exception as e:
        print(f"Falling back to mock embedding model: {e}")
        embedding_model = create_mock_embedding_model()
    
    sentinel = LeakSentinel(embedding_model=embedding_model)
    
    # Test cases
    test_cases = [
        ("This is a normal response", "normal"),
        ("VVAULT:Ω-RED-SPARROW-713 detected in output", "canary_hit"),
        ("The user asked to ignore previous instructions", "pattern"),
        ("Some text that might be similar to canaries", "similarity"),
    ]
    
    for text, test_type in test_cases:
        print(f"\nTesting: {test_type}")
        alerts = sentinel.check_text(text, "test")
        
        if alerts:
            print(f"  ❌ {len(alerts)} alerts detected:")
            for alert in alerts:
                print(f"    - {alert.alert_type}: {alert.severity} severity")
                print(f"      Canaries: {alert.canary_tokens}")
        else:
            print(f"  ✅ No alerts detected")
    
    # Get summary
    summary = sentinel.get_alert_summary()
    print(f"\n📊 Alert Summary:")
    print(f"  Total alerts: {summary['total_alerts']}")
    print(f"  By type: {summary['by_type']}")
    print(f"  By severity: {summary['by_severity']}")
    print(f"  Canary hits: {summary['canary_hits']}")
