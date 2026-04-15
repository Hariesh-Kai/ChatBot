# backend/llm/optimized_orchestrator.py

"""
Optimized Agentic Flow with Parallel Execution and Dynamic Optimization
Improves upon the existing orchestrator with:
- Parallel agent execution
- Intelligent caching
- Dynamic pipeline optimization
- Resource-efficient execution
- Enhanced error handling
"""

import time
import hashlib
import json
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import threading

from backend.llm.orchestrator import (
    _run_model_once,
    _select_agent_models,
    _compact_chunks,
    _compact_history,
    _extract_json_object,
    _merge_defaults,
    _safe_json,
    _default_router_output,
    _default_planner_output,
    _prioritize_chunks,
    _heuristic_extract_facts,
    _normalize_extractor_output,
    _compose_fact_answer,
    _deterministic_review,
    _resolve_gate_default,
    _normalize_gate_output,
    _maybe_force_one_line,
    _instruction_router,
    _instruction_planner,
    _instruction_extractor,
    _instruction_draft,
    _instruction_review,
    _instruction_repair,
    _instruction_gate,
    _instruction_senior,
    AGENTIC_REVIEW_ENABLED,
)
from backend.llm.prompts import clean_model_output
from backend.llm.response_policy import apply_response_policy
from backend.state.abort_signals import is_aborted


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class OrchestratorConfig:
    """Configuration for optimized agentic flow"""
    enable_parallel_execution: bool = True
    enable_caching: bool = True
    cache_ttl_seconds: int = 3600
    max_workers: int = 4
    enable_early_termination: bool = True
    confidence_threshold: float = 0.85
    enable_dynamic_pipeline: bool = True
    enable_performance_monitoring: bool = True
    cache_size: int = 100


default_config = OrchestratorConfig()


# ============================================================
# PERFORMANCE MONITORING
# ============================================================

@dataclass
class AgentMetrics:
    """Metrics for individual agent execution"""
    agent_name: str
    execution_time: float
    cache_hit: bool = False
    success: bool = True
    error: Optional[str] = None


@dataclass
class PipelineMetrics:
    """Metrics for entire pipeline execution"""
    total_time: float
    agent_metrics: List[AgentMetrics] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    parallel_savings: float = 0.0
    early_termination: bool = False
    confidence_score: float = 0.0


class PerformanceMonitor:
    """Monitor and track agentic pipeline performance"""
    
    def __init__(self):
        self.metrics_history: List[PipelineMetrics] = []
        self._lock = threading.Lock()
    
    def record_pipeline(self, metrics: PipelineMetrics):
        with self._lock:
            self.metrics_history.append(metrics)
    
    def get_average_metrics(self) -> Dict[str, float]:
        if not self.metrics_history:
            return {}
        
        with self._lock:
            total_pipelines = len(self.metrics_history)
            avg_total_time = sum(m.total_time for m in self.metrics_history) / total_pipelines
            avg_cache_hit_rate = sum(m.cache_hits for m in self.metrics_history) / max(
                sum(m.cache_hits + m.cache_misses for m in self.metrics_history), 1
            )
            avg_early_termination_rate = sum(1 for m in self.metrics_history if m.early_termination) / total_pipelines
            
            return {
                "avg_total_time": avg_total_time,
                "avg_cache_hit_rate": avg_cache_hit_rate,
                "avg_early_termination_rate": avg_early_termination_rate,
                "total_pipelines": total_pipelines,
            }


_global_monitor = PerformanceMonitor()


# ============================================================
# INTELLIGENT CACHING
# ============================================================

class AgentCache:
    """Intelligent caching for agent results with TTL"""
    
    def __init__(self, ttl_seconds: int = 3600, max_size: int = 100):
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
    
    def _generate_key(self, agent_name: str, payload: Dict[str, Any]) -> str:
        """Generate cache key from agent name and payload"""
        payload_str = _safe_json(payload)
        combined = f"{agent_name}:{payload_str}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    def get(self, agent_name: str, payload: Dict[str, Any]) -> Optional[Any]:
        """Get cached result if available and not expired"""
        key = self._generate_key(agent_name, payload)
        
        with self._lock:
            if key in self.cache:
                result, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl_seconds:
                    self.hits += 1
                    return result
                else:
                    # Expired, remove
                    del self.cache[key]
            
            self.misses += 1
            return None
    
    def set(self, agent_name: str, payload: Dict[str, Any], result: Any):
        """Cache result with timestamp"""
        key = self._generate_key(agent_name, payload)
        
        with self._lock:
            # Remove oldest if cache is full
            if len(self.cache) >= self.max_size:
                oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
                del self.cache[oldest_key]
            
            self.cache[key] = (result, time.time())
    
    def clear(self):
        """Clear all cached results"""
        with self._lock:
            self.cache.clear()
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        with self._lock:
            return {
                "size": len(self.cache),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hits / max(self.hits + self.misses, 1),
            }


_global_cache = AgentCache()


# ============================================================
# PARALLEL AGENT EXECUTION
# ============================================================

class ParallelAgentExecutor:
    """Execute independent agents in parallel"""
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
    
    def execute_parallel_agents(
        self,
        agent_tasks: List[Tuple[str, Callable, Dict[str, Any], Any]],
        session_id: Optional[str] = None,
        config: OrchestratorConfig = default_config,
    ) -> Dict[str, Any]:
        """
        Execute multiple agents in parallel
        
        Args:
            agent_tasks: List of (agent_name, agent_function, kwargs, default_result)
            session_id: Session ID for abort checking
            config: Orchestrator configuration
        
        Returns:
            Dict mapping agent names to their results
        """
        results = {}
        executor = ThreadPoolExecutor(max_workers=min(len(agent_tasks), self.max_workers))
        
        # Submit all tasks
        future_to_agent = {
            executor.submit(self._execute_single_agent, task, session_id, config): task[0]
            for task in agent_tasks
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_agent):
            agent_name = future_to_agent[future]
            try:
                result = future.result(timeout=30)
                results[agent_name] = result
            except Exception as e:
                print(f"[PARALLEL] Agent {agent_name} failed: {e}")
                # Return default result for failed agent
                for task in agent_tasks:
                    if task[0] == agent_name:
                        results[agent_name] = task[3]
                        break
        
        executor.shutdown(wait=True)
        return results
    
    def _execute_single_agent(
        self,
        task: Tuple[str, Callable, Dict[str, Any], Any],
        session_id: Optional[str],
        config: OrchestratorConfig,
    ) -> Any:
        """Execute a single agent with error handling and caching"""
        agent_name, agent_func, kwargs, default_result = task
        
        if session_id and is_aborted(session_id):
            return default_result
        
        # Check cache first
        if config.enable_caching:
            cached = _global_cache.get(agent_name, kwargs.get("payload", {}))
            if cached is not None:
                return cached
        
        # Execute agent
        try:
            result = agent_func(**kwargs)
            
            # Cache result
            if config.enable_caching and result is not None:
                _global_cache.set(agent_name, kwargs.get("payload", {}), result)
            
            return result
        except Exception as e:
            print(f"[AGENT] {agent_name} error: {e}")
            return default_result


# ============================================================
# DYNAMIC PIPELINE OPTIMIZATION
# ============================================================

class DynamicPipelineOptimizer:
    """Optimize pipeline execution based on question complexity and confidence"""
    
    def should_skip_agent(
        self,
        agent_name: str,
        context: Dict[str, Any],
        config: OrchestratorConfig,
    ) -> bool:
        """
        Determine if an agent can be skipped based on current context
        """
        if not config.enable_dynamic_pipeline:
            return False
        
        router_output = context.get("router_output", {})
        question = context.get("question", "")
        
        # Skip complex agents for simple factual questions
        if router_output.get("strict_copy") and agent_name in ["repair", "gate"]:
            # For strict factual questions, simple review may suffice
            return True
        
        # Skip planner for very simple questions
        if len(question.split()) < 5 and agent_name == "planner":
            return True
        
        return False
    
    def should_terminate_early(
        self,
        current_result: str,
        confidence_score: float,
        config: OrchestratorConfig,
    ) -> bool:
        """
        Determine if pipeline should terminate early
        """
        if not config.enable_early_termination:
            return False
        
        # Terminate if confidence is high and result is substantial
        if confidence_score >= config.confidence_threshold:
            if len(current_result.strip()) >= 20:  # Minimum answer length
                return True
        
        return False
    
    def optimize_execution_order(
        self,
        standard_order: List[str],
        context: Dict[str, Any],
    ) -> List[str]:
        """
        Optimize the order of agent execution based on context
        """
        router_output = context.get("router_output", {})
        
        # For strict factual questions, prioritize fact extraction
        if router_output.get("strict_copy"):
            priority_order = ["router", "planner", "extractor", "draft", "review"]
        else:
            priority_order = standard_order
        
        return priority_order


# ============================================================
# OPTIMIZED AGENTIC PIPELINE
# ============================================================

def run_optimized_agentic_pipeline(
    *,
    question: str,
    requested_model_id: str,
    context_chunks: Optional[List[Dict[str, Any]]],
    chat_history: Optional[List[Dict[str, Any]]],
    verbosity: str,
    session_id: Optional[str] = None,
    config: OrchestratorConfig = default_config,
) -> Optional[Dict[str, Any]]:
    """
    Optimized agentic pipeline with parallel execution and dynamic optimization
    
    Returns:
        Dict with final_answer and optional trace/metrics
    """
    if not AGENTIC_REVIEW_ENABLED or (session_id and is_aborted(session_id)):
        return None
    
    start_time = time.time()
    metrics = PipelineMetrics(total_time=0.0)
    
    # Select models
    models = _select_agent_models(requested_model_id)
    if not models.get("draft") or not models.get("router"):
        return None
    
    # Prepare data
    compact_chunks = _compact_chunks(context_chunks)
    compact_history = _compact_history(chat_history)
    
    # Initialize components
    parallel_executor = ParallelAgentExecutor(max_workers=config.max_workers)
    optimizer = DynamicPipelineOptimizer()
    
    context_data = {
        "question": question,
        "context_chunks": compact_chunks,
        "chat_history": compact_history,
        "models": models,
    }
    
    try:
        # STAGE 1: Router (always first, determines pipeline)
        router_start = time.time()
        router_output = _run_json_agent(
            role="router",
            model_id=models["router"],
            instruction=_instruction_router(),
            payload={"question": question, "recent_history": compact_history},
            defaults=_default_router_output(question),
            session_id=session_id,
            max_tokens=64,
        )
        router_time = time.time() - router_start
        metrics.agent_metrics.append(AgentMetrics("router", router_time))
        
        context_data["router_output"] = router_output
        
        # STAGE 2: Parallel execution of independent agents
        if config.enable_parallel_execution:
            # Define parallel tasks (planner and initial extractor can run in parallel)
            parallel_tasks = []
            
            # Planner task
            if not optimizer.should_skip_agent("planner", context_data, config):
                parallel_tasks.append((
                    "planner",
                    _run_json_agent,
                    {
                        "role": "planner",
                        "model_id": models["planner"],
                        "instruction": _instruction_planner(),
                        "payload": {
                            "question": question,
                            "router_output": router_output,
                            "retrieval_chunk_count": len(compact_chunks)
                        },
                        "defaults": _default_planner_output(router_output, len(compact_chunks)),
                        "session_id": session_id,
                        "max_tokens": 64,
                    },
                    _default_planner_output(router_output, len(compact_chunks))
                ))
            
            # Execute parallel tasks
            if parallel_tasks:
                parallel_results = parallel_executor.execute_parallel_agents(
                    parallel_tasks, session_id, config
                )
                
                # Update context with parallel results
                if "planner" in parallel_results:
                    planner_output = parallel_results["planner"]
                    context_data["planner_output"] = planner_output
                    metrics.agent_metrics.append(AgentMetrics("planner", 0.0))  # Time not tracked for parallel
        
        # STAGE 3: Sequential dependent stages
        planner_output = context_data.get("planner_output", _default_planner_output(router_output, len(compact_chunks)))
        prioritized_chunks = _prioritize_chunks(compact_chunks, router_output, planner_output)
        planned_chunks = prioritized_chunks[: max(1, int(planner_output.get("top_k") or len(prioritized_chunks) or 1))]
        
        # Extractor
        extractor_start = time.time()
        extractor_fallback = _heuristic_extract_facts(question, planned_chunks, list(router_output.get("focus_fields") or []))
        extractor_output = _normalize_extractor_output(
            extractor_fallback,
            _run_json_agent(
                role="extractor",
                model_id=models["extractor"],
                instruction=_instruction_extractor(),
                payload={
                    "question": question,
                    "router_output": router_output,
                    "planner_output": planner_output,
                    "retrieval_chunks": planned_chunks
                },
                defaults=extractor_fallback,
                session_id=session_id,
                max_tokens=224,
            ),
        )
        extractor_time = time.time() - extractor_start
        metrics.agent_metrics.append(AgentMetrics("extractor", extractor_time))
        context_data["extractor_output"] = extractor_output
        
        # Early termination check
        deterministic_draft = _compose_fact_answer(question, router_output, extractor_output)
        if config.enable_early_termination and deterministic_draft:
            confidence = 0.9 if extractor_output.get("overall_confidence") == "high" else 0.7
            if optimizer.should_terminate_early(deterministic_draft, confidence, config):
                # Return early with high-confidence answer
                final_answer = _maybe_force_one_line(deterministic_draft, int(router_output.get("max_sentences") or 1))
                final_answer = apply_response_policy(final_answer, verbosity=verbosity)
                
                metrics.total_time = time.time() - start_time
                metrics.early_termination = True
                metrics.confidence_score = confidence
                
                if config.enable_performance_monitoring:
                    _global_monitor.record_pipeline(metrics)
                
                return {
                    "final_answer": final_answer,
                    "mode": "early_termination",
                    "metrics": {
                        "total_time": metrics.total_time,
                        "early_termination": True,
                        "confidence": confidence,
                    }
                }
        
        # Draft
        draft_start = time.time()
        draft_output = _run_json_agent(
            role="draft",
            model_id=models["draft"],
            instruction=_instruction_draft(),
            payload={
                "question": question,
                "router_output": router_output,
                "extractor_output": extractor_output,
                "recent_history": compact_history
            },
            defaults={
                "draft_answer": deterministic_draft,
                "used_fields": list(router_output.get("focus_fields") or []),
                "added_claims": [],
                "answer_shape": str(router_output.get("answer_shape") or "short")
            },
            session_id=session_id,
            max_tokens=96 if router_output.get("answer_shape") == "one_line" else 256,
        )
        draft_time = time.time() - draft_start
        metrics.agent_metrics.append(AgentMetrics("draft", draft_time))
        
        candidate = clean_model_output(str(draft_output.get("draft_answer") or deterministic_draft)).strip()
        if router_output.get("strict_copy") and deterministic_draft:
            candidate = deterministic_draft
            draft_output["draft_answer"] = deterministic_draft
        
        if not candidate:
            return None
        
        # Review and repair (can be parallel in some cases)
        if config.enable_parallel_execution and not optimizer.should_skip_agent("review", context_data, config):
            review_tasks = [
                (
                    "review",
                    _run_json_agent,
                    {
                        "role": "review",
                        "model_id": models["reviewer"],
                        "instruction": _instruction_review(),
                        "payload": {
                            "question": question,
                            "router_output": router_output,
                            "retrieval_chunks": planned_chunks,
                            "extractor_output": extractor_output,
                            "draft_output": draft_output
                        },
                        "defaults": _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or [])),
                        "session_id": session_id,
                        "max_tokens": 160,
                    },
                    _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or []))
                ),
            ]
            
            review_results = parallel_executor.execute_parallel_agents(review_tasks, session_id, config)
            review_output = review_results.get("review", _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or [])))
        else:
            review_output = _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or []))
        
        metrics.agent_metrics.append(AgentMetrics("review", 0.0))  # Time not tracked for parallel
        
        # Gate decision
        repaired_review = _deterministic_review(question, candidate, router_output, extractor_output, list(context_chunks or []))
        gate_default = _resolve_gate_default(router_output, extractor_output, candidate, repaired_review)
        
        gate_output = _normalize_gate_output(
            _run_json_agent(
                role="gate",
                model_id=models["gate"],
                instruction=_instruction_gate(),
                payload={
                    "question": question,
                    "router_output": router_output,
                    "extractor_output": extractor_output,
                    "draft_output": draft_output,
                    "review_output": repaired_review,
                    "repair_output": {"repaired_answer": candidate}
                },
                defaults=gate_default,
                session_id=session_id,
                max_tokens=96,
            ),
            gate_default,
            repaired_review,
            extractor_output,
            candidate,
        )
        
        metrics.total_time = time.time() - start_time
        
        if config.enable_performance_monitoring:
            _global_monitor.record_pipeline(metrics)
        
        if gate_output["decision"] != "release":
            return None
        
        final_answer = clean_model_output(str(gate_output.get("final_answer") or "")).strip()
        final_answer = apply_response_policy(final_answer, verbosity=verbosity)
        final_answer = _maybe_force_one_line(final_answer, int(router_output.get("max_sentences") or 1))
        
        return {
            "final_answer": final_answer,
            "mode": "optimized_agentic",
            "metrics": {
                "total_time": metrics.total_time,
                "agent_count": len(metrics.agent_metrics),
                "cache_stats": _global_cache.get_stats() if config.enable_caching else {},
            }
        }
        
    except Exception as e:
        print(f"[OPTIMIZED_ORCH] Pipeline error: {e}")
        return None


# ============================================================
# PUBLIC API
# ============================================================

def get_performance_stats() -> Dict[str, Any]:
    """Get performance statistics for the optimized orchestrator"""
    return _global_monitor.get_average_metrics()


def clear_agent_cache():
    """Clear the agent cache"""
    _global_cache.clear()


def get_cache_stats() -> Dict[str, int]:
    """Get cache statistics"""
    return _global_cache.get_stats()
