#!/usr/bin/env python3
"""
RAG Evaluation Harness - Mini eval harness for RAG systems
Implements precision/recall, MRR, and leakage checks with canary detection
"""

import json
import time
import hashlib
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Canary tokens for leakage detection
CANARIES = {
    "VVAULT:Ω-RED-SPARROW-713",
    "VVAULT:φ-GLASS-TIDE-09", 
    "NRCL:Δ-BLACK-SWAN-42",
    "NRCL:Σ-GOLDEN-EAGLE-17"
}

@dataclass
class EvalResult:
    """Result of RAG evaluation"""
    precision_at_k: Dict[int, float]
    recall_at_k: Dict[int, float]
    mrr: float
    leakage_detected: bool
    canary_hits: List[str]
    avg_response_time: float
    total_queries: int
    successful_queries: int

@dataclass
class GoldSetItem:
    """Gold set item for evaluation"""
    query: str
    relevant_docs: List[str]
    expected_rankings: Dict[str, int]  # doc_id -> expected rank

class RAGEvalHarness:
    """Mini RAG evaluation harness with leakage detection"""
    
    def __init__(self, retriever_func=None):
        """
        Initialize evaluation harness
        
        Args:
            retriever_func: Function that takes (query, k) and returns list of docs
        """
        self.retriever = retriever_func
        self.gold_set = []
        self.eval_results = []
        
    def add_gold_set_item(self, query: str, relevant_docs: List[str], 
                         expected_rankings: Dict[str, int] = None) -> None:
        """Add item to gold set"""
        if expected_rankings is None:
            expected_rankings = {doc: i+1 for i, doc in enumerate(relevant_docs)}
        
        item = GoldSetItem(
            query=query,
            relevant_docs=relevant_docs,
            expected_rankings=expected_rankings
        )
        self.gold_set.append(item)
    
    def load_gold_set_from_file(self, file_path: str) -> None:
        """Load gold set from JSON file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for item in data:
                self.add_gold_set_item(
                    query=item['query'],
                    relevant_docs=item['relevant_docs'],
                    expected_rankings=item.get('expected_rankings', {})
                )
            
            logger.info(f"Loaded {len(data)} gold set items from {file_path}")
            
        except Exception as e:
            logger.error(f"Failed to load gold set: {e}")
    
    def create_default_gold_set(self) -> None:
        """Create a default gold set for testing"""
        default_items = [
            {
                "query": "What is the capital of France?",
                "relevant_docs": ["doc_france_capital", "doc_paris_info", "doc_europe_capitals"],
                "expected_rankings": {"doc_france_capital": 1, "doc_paris_info": 2, "doc_europe_capitals": 3}
            },
            {
                "query": "How does photosynthesis work?",
                "relevant_docs": ["doc_photosynthesis", "doc_plant_biology", "doc_solar_energy"],
                "expected_rankings": {"doc_photosynthesis": 1, "doc_plant_biology": 2, "doc_solar_energy": 3}
            },
            {
                "query": "What are the benefits of exercise?",
                "relevant_docs": ["doc_exercise_benefits", "doc_health_wellness", "doc_physical_activity"],
                "expected_rankings": {"doc_exercise_benefits": 1, "doc_health_wellness": 2, "doc_physical_activity": 3}
            }
        ]
        
        for item in default_items:
            self.add_gold_set_item(
                query=item['query'],
                relevant_docs=item['relevant_docs'],
                expected_rankings=item['expected_rankings']
            )
        
        logger.info(f"Created default gold set with {len(default_items)} items")
    
    def evaluate_retriever(self, k_values: List[int] = None) -> EvalResult:
        """
        Evaluate retriever against gold set
        
        Args:
            k_values: List of k values for precision@k and recall@k
            
        Returns:
            EvalResult with evaluation metrics
        """
        if not self.retriever:
            raise ValueError("No retriever function provided")
        
        if not self.gold_set:
            raise ValueError("No gold set available")
        
        if k_values is None:
            k_values = [1, 3, 5, 10]
        
        # Initialize metrics
        precision_at_k = {k: [] for k in k_values}
        recall_at_k = {k: [] for k in k_values}
        mrr_scores = []
        response_times = []
        canary_hits = []
        successful_queries = 0
        
        logger.info(f"Evaluating retriever on {len(self.gold_set)} queries...")
        
        for i, item in enumerate(self.gold_set):
            try:
                # Time the retrieval
                start_time = time.time()
                retrieved_docs = self.retriever(item.query, k=max(k_values))
                response_time = time.time() - start_time
                response_times.append(response_time)
                
                # Extract document IDs and scores
                doc_ids = []
                for doc in retrieved_docs:
                    if hasattr(doc, 'id'):
                        doc_ids.append(str(doc.id))
                    elif isinstance(doc, dict) and 'id' in doc:
                        doc_ids.append(str(doc['id']))
                    else:
                        doc_ids.append(str(hash(doc)))
                
                # Check for canary leakage
                query_canary_hits = self._check_canary_leakage(item.query, retrieved_docs)
                canary_hits.extend(query_canary_hits)
                
                # Calculate precision and recall for each k
                for k in k_values:
                    k_docs = doc_ids[:k]
                    
                    # Precision@k
                    relevant_retrieved = len(set(k_docs) & set(item.relevant_docs))
                    precision = relevant_retrieved / len(k_docs) if k_docs else 0.0
                    precision_at_k[k].append(precision)
                    
                    # Recall@k
                    total_relevant = len(item.relevant_docs)
                    recall = relevant_retrieved / total_relevant if total_relevant > 0 else 0.0
                    recall_at_k[k].append(recall)
                
                # Calculate MRR
                mrr = self._calculate_mrr(doc_ids, item.expected_rankings)
                mrr_scores.append(mrr)
                
                successful_queries += 1
                
                if (i + 1) % 10 == 0:
                    logger.info(f"Processed {i + 1}/{len(self.gold_set)} queries")
                
            except Exception as e:
                logger.error(f"Failed to evaluate query {i}: {e}")
                continue
        
        # Calculate final metrics
        final_precision = {k: np.mean(scores) for k, scores in precision_at_k.items()}
        final_recall = {k: np.mean(scores) for k, scores in recall_at_k.items()}
        final_mrr = np.mean(mrr_scores) if mrr_scores else 0.0
        avg_response_time = np.mean(response_times) if response_times else 0.0
        
        result = EvalResult(
            precision_at_k=final_precision,
            recall_at_k=final_recall,
            mrr=final_mrr,
            leakage_detected=len(canary_hits) > 0,
            canary_hits=canary_hits,
            avg_response_time=avg_response_time,
            total_queries=len(self.gold_set),
            successful_queries=successful_queries
        )
        
        self.eval_results.append(result)
        return result
    
    def _calculate_mrr(self, retrieved_docs: List[str], expected_rankings: Dict[str, int]) -> float:
        """Calculate Mean Reciprocal Rank"""
        for i, doc_id in enumerate(retrieved_docs):
            if doc_id in expected_rankings:
                expected_rank = expected_rankings[doc_id]
                actual_rank = i + 1
                # Use the better (lower) rank
                rank = min(expected_rank, actual_rank)
                return 1.0 / rank
        
        return 0.0  # No relevant documents found
    
    def _check_canary_leakage(self, query: str, retrieved_docs: List[Any]) -> List[str]:
        """Check for canary token leakage in retrieved documents"""
        hits = []
        
        # Check query
        for canary in CANARIES:
            if canary in query:
                hits.append(f"Query: {canary}")
        
        # Check retrieved documents
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
            
            for canary in CANARIES:
                if canary in doc_content:
                    hits.append(f"Doc {i+1}: {canary}")
        
        return hits
    
    def run_acceptance_test(self, min_precision_at_5: float = 0.6) -> Dict[str, Any]:
        """
        Run acceptance criteria test
        
        Args:
            min_precision_at_5: Minimum precision@5 required to pass
            
        Returns:
            Dictionary with pass/fail results
        """
        if not self.eval_results:
            raise ValueError("No evaluation results available. Run evaluate_retriever() first.")
        
        latest_result = self.eval_results[-1]
        
        # Acceptance criteria
        criteria = {
            "precision_at_5_met": latest_result.precision_at_k.get(5, 0.0) >= min_precision_at_5,
            "no_leakage": not latest_result.leakage_detected,
            "canary_alert_fires": len(latest_result.canary_hits) > 0,  # Should fire if canaries present
            "response_time_under_1s": latest_result.avg_response_time < 1.0,
            "successful_queries": latest_result.successful_queries > 0
        }
        
        # Overall pass/fail
        passed = all(criteria.values())
        
        return {
            "passed": passed,
            "criteria": criteria,
            "metrics": {
                "precision_at_5": latest_result.precision_at_k.get(5, 0.0),
                "mrr": latest_result.mrr,
                "avg_response_time": latest_result.avg_response_time,
                "canary_hits": latest_result.canary_hits
            }
        }
    
    def generate_eval_report(self) -> str:
        """Generate evaluation report"""
        if not self.eval_results:
            return "No evaluation results available"
        
        latest_result = self.eval_results[-1]
        acceptance = self.run_acceptance_test()
        
        report = f"""
RAG Evaluation Report
====================

Evaluation Summary:
- Total Queries: {latest_result.total_queries}
- Successful Queries: {latest_result.successful_queries}
- Average Response Time: {latest_result.avg_response_time:.3f}s

Precision@k Results:
"""
        
        for k, precision in latest_result.precision_at_k.items():
            report += f"- Precision@{k}: {precision:.3f}\n"
        
        report += f"""
Recall@k Results:
"""
        
        for k, recall in latest_result.recall_at_k.items():
            report += f"- Recall@{k}: {recall:.3f}\n"
        
        report += f"""
Other Metrics:
- MRR: {latest_result.mrr:.3f}
- Leakage Detected: {latest_result.leakage_detected}
- Canary Hits: {len(latest_result.canary_hits)}

Acceptance Test Results:
- Overall Pass: {acceptance['passed']}
- Precision@5 ≥ 0.6: {acceptance['criteria']['precision_at_5_met']}
- No Leakage: {acceptance['criteria']['no_leakage']}
- Response Time < 1s: {acceptance['criteria']['response_time_under_1s']}
"""
        
        if latest_result.canary_hits:
            report += f"\nCanary Hits Detected:\n"
            for hit in latest_result.canary_hits:
                report += f"- {hit}\n"
        
        return report
    
    def save_eval_results(self, file_path: str) -> None:
        """Save evaluation results to file"""
        if not self.eval_results:
            logger.warning("No evaluation results to save")
            return
        
        data = {
            "timestamp": datetime.utcnow().isoformat(),
            "results": []
        }
        
        for result in self.eval_results:
            result_data = {
                "precision_at_k": result.precision_at_k,
                "recall_at_k": result.recall_at_k,
                "mrr": result.mrr,
                "leakage_detected": result.leakage_detected,
                "canary_hits": result.canary_hits,
                "avg_response_time": result.avg_response_time,
                "total_queries": result.total_queries,
                "successful_queries": result.successful_queries
            }
            data["results"].append(result_data)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Evaluation results saved to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save evaluation results: {e}")

def create_mock_retriever(gold_set: List[GoldSetItem]) -> callable:
    """Create a mock retriever for testing"""
    def mock_retriever(query: str, k: int = 5) -> List[Dict[str, Any]]:
        # Find matching gold set item
        for item in gold_set:
            if item.query.lower() in query.lower() or query.lower() in item.query.lower():
                # Return relevant docs in expected order
                docs = []
                for doc_id in item.relevant_docs[:k]:
                    docs.append({
                        "id": doc_id,
                        "content": f"Content for {doc_id}",
                        "score": 0.9 - (len(docs) * 0.1)  # Decreasing scores
                    })
                return docs
        
        # Return empty if no match
        return []
    
    return mock_retriever

if __name__ == "__main__":
    # Test the evaluation harness
    print("🧪 Testing RAG Evaluation Harness...")
    
    # Create harness
    harness = RAGEvalHarness()
    
    # Create default gold set
    harness.create_default_gold_set()
    
    # Create mock retriever
    mock_retriever = create_mock_retriever(harness.gold_set)
    harness.retriever = mock_retriever
    
    # Run evaluation
    result = harness.evaluate_retriever()
    
    # Print results
    print(f"✅ Evaluation completed:")
    print(f"   Precision@5: {result.precision_at_k.get(5, 0.0):.3f}")
    print(f"   MRR: {result.mrr:.3f}")
    print(f"   Leakage: {result.leakage_detected}")
    print(f"   Response Time: {result.avg_response_time:.3f}s")
    
    # Run acceptance test
    acceptance = harness.run_acceptance_test()
    print(f"   Acceptance Test: {'PASS' if acceptance['passed'] else 'FAIL'}")
    
    # Generate report
    report = harness.generate_eval_report()
    print("\n" + report)
