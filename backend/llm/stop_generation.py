# backend/llm/stop_generation.py

"""
Comprehensive Stop Generation Mechanisms for LLM Response Control
Provides advanced stop detection, response completion, and quality control
"""

import re
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass


@dataclass
class StopConfig:
    """Configuration for stop generation behavior"""
    enable_answer_completion: bool = True
    enable_redundancy_detection: bool = True
    enable_quality_control: bool = True
    max_repetitions: int = 3
    min_answer_length: int = 20
    max_answer_length: int = 2000
    enable_early_stop: bool = True
    early_stop_confidence: float = 0.85
    enable_aggressive_short_answer_stop: bool = True  # New setting for short answers


# ============================================================
# COMPREHENSIVE STOP TOKENS
# ============================================================

# Basic prompt echo prevention (existing)
PROMPT_ECHO_STOP_MARKERS = (
    "<|eot_id|>",
    "<|start_header_id|>",
    "<|end_header_id|>",
    "OUTPUT STYLE:",
    "\nCONTEXT:\n",
    "\nQUESTION:\n",
)

# Answer completion markers
ANSWER_COMPLETION_MARKERS = (
    "I hope this helps",
    "I hope this answers your question",
    "Let me know if you need more information",
    "Hope this helps",
    "This should answer your question",
    "Please let me know if you need clarification",
    "Is there anything else you'd like to know",
)

# Redundancy and repetition markers
# Only true repetition/wrap-up phrases that are NEVER mid-answer.
# "Therefore,", "Thus,", "In summary," etc. are valid answer phrases and
# must NOT be used as stop signals.
REDUNDANCY_MARKERS = (
    "As mentioned earlier,",
    "As previously stated,",
    "As I said before,",
    "Once again,",
)

# Quality control markers (indicates poor response)
QUALITY_ISSUE_MARKERS = (
    "I cannot answer",
    "I'm unable to answer",
    "I don't have information",
    "No information available",
    "Not provided in the document",
    "I am an AI",
    "As an AI",
    "As a language model",
)

# Model internal markers
INTERNAL_MODEL_MARKERS = (
    "REFINED ANSWER:",
    "END OF RESPONSE",
    "SYSTEM:",
    "ASSISTANT:",
    "USER:",
    "HUMAN:",
    "AI:",
)

# Conversation turn markers
CONVERSATION_MARKERS = (
    "Question:",
    "Answer:",
    "Follow-up:",
    "Next question:",
    "Another question:",
)

# ============================================================
# ADVANCED STOP DETECTION
# ============================================================

class StopGenerationManager:
    """
    Advanced stop generation manager with multiple detection strategies
    """
    
    def __init__(self, config: Optional[StopConfig] = None):
        self.config = config or StopConfig()
        self._stop_cache = {}
        
    def get_comprehensive_stop_tokens(self) -> Tuple[str, ...]:
        """
        Get all stop tokens combined for comprehensive coverage
        """
        all_markers = (
            PROMPT_ECHO_STOP_MARKERS +
            ANSWER_COMPLETION_MARKERS +
            INTERNAL_MODEL_MARKERS +
            CONVERSATION_MARKERS
        )
        return tuple(sorted(set(all_markers)))
    
    def detect_answer_completion(self, text: str) -> bool:
        """
        Detect if the answer appears complete based on completion markers
        """
        if not self.config.enable_answer_completion:
            return False
            
        text_lower = text.lower()
        for marker in ANSWER_COMPLETION_MARKERS:
            if marker.lower() in text_lower:
                return True
        return False
    
    def detect_redundancy(self, text: str) -> bool:
        """
        Detect redundant or repetitive content that indicates completion
        """
        if not self.config.enable_redundancy_detection:
            return False
            
        # Check for redundancy markers
        text_lower = text.lower()
        for marker in REDUNDANCY_MARKERS:
            if marker.lower() in text_lower:
                return True
        
        # Check for sentence repetition instead of word repetition
        sentences = [s.strip() for s in text.split('.') if s.strip()]
        if len(sentences) > 5:
            # Check if last few sentences are similar
            recent_sentences = sentences[-3:]
            unique_sentences = set(recent_sentences)
            if len(unique_sentences) < len(recent_sentences):
                return True
        
        return False
    
    def detect_quality_issues(self, text: str) -> bool:
        """
        Detect quality issues that might require stopping
        """
        if not self.config.enable_quality_control:
            return False
            
        text_lower = text.lower()
        for marker in QUALITY_ISSUE_MARKERS:
            if marker.lower() in text_lower:
                return True
        return False
    
    def detect_early_stop(self, text: str, confidence_score: float = 0.0) -> bool:
        """
        Detect if generation should stop early based on confidence and content
        """
        if not self.config.enable_early_stop:
            return False
            
        # Stop if confidence is high and answer is substantial
        if confidence_score >= self.config.early_stop_confidence:
            text_length = len(text.strip())
            if text_length >= self.config.min_answer_length:
                return True
        
        return False
    
    def detect_short_answer_completion(self, text: str) -> bool:
        """
        Detect if a short factual answer appears complete and should stop immediately.
        This prevents duplication issues like "STBLPDSTBLPD".

        IMPORTANT: The buffer accumulates ALL tokens so far.  We must NOT fire
        on a short buffer that is simply the first few tokens of a longer answer.
        Minimum 80 chars ensures at least one full sentence has been seen before
        we consider stopping on punctuation alone.
        """
        if not self.config.enable_aggressive_short_answer_stop:
            return False

        text = text.strip()
        text_length = len(text)
        if not text:
            return False

        # Never stop on a heading — it is the START of the answer, not the end.
        if text.endswith(":"):
            return False

        # Do NOT trigger on very short buffers — too early in streaming to know
        # whether the answer is really done.  Previous threshold of 2-20 chars
        # was stopping generation after the very first token pair.
        if text_length < 80:
            return False

        return False
    
    def check_length_constraints(self, text: str) -> Tuple[bool, str]:
        """
        Check if text meets length constraints
        Returns (should_stop, reason)
        """
        text_length = len(text.strip())
        
        if text_length > self.config.max_answer_length:
            return True, "Maximum answer length exceeded"
        
        if text_length < self.config.min_answer_length:
            return False, "Minimum answer length not reached"
        
        return False, "Length constraints satisfied"
    
    def detect_stop_condition(
        self,
        text: str,
        confidence_score: float = 0.0,
        check_all: bool = True
    ) -> Tuple[bool, str]:
        """
        Comprehensive stop condition detection
        Returns (should_stop, reason)
        """
        reasons = []
        
        # Check short answer completion first (highest priority for factual answers)
        if self.detect_short_answer_completion(text):
            reasons.append("Short answer completion detected")
            return True, "Short answer completion detected"
        
        # Check answer completion
        if self.detect_answer_completion(text):
            reasons.append("Answer completion detected")
        
        # Check redundancy
        if self.detect_redundancy(text):
            reasons.append("Redundancy detected")
        
        # Check quality issues
        if self.detect_quality_issues(text):
            reasons.append("Quality issue detected")
        
        # Check early stop conditions
        if self.detect_early_stop(text, confidence_score):
            reasons.append("Early stop condition met")
        
        # Check length constraints
        should_stop_length, length_reason = self.check_length_constraints(text)
        if should_stop_length:
            reasons.append(length_reason)
        
        should_stop = len(reasons) > 0 if check_all else bool(reasons)
        
        if should_stop:
            return True, "; ".join(reasons)
        
        return False, "No stop condition detected"
    
    def clean_response(self, text: str) -> str:
        """
        Clean response by removing content after any stop markers
        """
        all_markers = self.get_comprehensive_stop_tokens()
        
        for marker in all_markers:
            if marker in text:
                text = text.split(marker)[0]
        
        # Remove trailing whitespace and common artifacts
        text = text.strip()
        
        # NOTE: Do NOT truncate incomplete sentences here.
        # The LLM often ends its answer with a line that has no trailing period
        # (e.g. after a list item or a number).  Truncating it silently drops
        # the last — often most relevant — part of the answer.
        return text
    
    def validate_response_quality(self, text: str) -> Dict[str, Any]:
        """
        Validate response quality and return metrics
        """
        text_length = len(text.strip())
        sentence_count = len([s for s in text.split('.') if s.strip()])
        word_count = len(text.split())
        
        quality_metrics = {
            "length": text_length,
            "sentence_count": sentence_count,
            "word_count": word_count,
            "has_quality_issues": self.detect_quality_issues(text),
            "has_redundancy": self.detect_redundancy(text),
            "is_complete": self.detect_answer_completion(text),
            "meets_min_length": text_length >= self.config.min_answer_length,
            "exceeds_max_length": text_length > self.config.max_answer_length,
        }
        
        # Calculate overall quality score
        quality_score = 1.0
        
        if quality_metrics["has_quality_issues"]:
            quality_score -= 0.5
        
        if quality_metrics["has_redundancy"]:
            quality_score -= 0.2
        
        if not quality_metrics["meets_min_length"]:
            quality_score -= 0.3
        
        if quality_metrics["exceeds_max_length"]:
            quality_score -= 0.1
        
        quality_metrics["quality_score"] = max(0.0, quality_score)
        
        return quality_metrics


# ============================================================
# STREAM-AWARE STOP DETECTION
# ============================================================

class StreamingStopDetector:
    """
    Real-time stop detection for streaming generation
    """
    
    def __init__(self, config: Optional[StopConfig] = None):
        self.manager = StopGenerationManager(config)
        self.buffer = ""
        self.repetition_count = 0
        self.last_sentence = ""
        
    def process_token(
        self,
        token: str,
        confidence_score: float = 0.0
    ) -> Tuple[bool, str, str]:
        """
        Process a streaming token and determine if generation should stop
        Returns (should_stop, reason, cleaned_text)
        """
        self.buffer += token
        
        # Check for immediate stop markers
        for marker in PROMPT_ECHO_STOP_MARKERS:
            if marker in self.buffer:
                cleaned = self.manager.clean_response(self.buffer)
                return True, f"Stop marker detected: {marker}", cleaned
        
        # Word-level repetition checks (like 'that that') have been removed
        # here because they shouldn't instantly abort the entire generation stream.
        
        # Check for stop conditions
        should_stop, reason = self.manager.detect_stop_condition(
            self.buffer,
            confidence_score,
            check_all=False  # Check progressively
        )
        
        if should_stop:
            cleaned = self.manager.clean_response(self.buffer)
            return True, reason, cleaned
        
        return False, "", ""
    
    def reset(self):
        """Reset the detector state"""
        self.buffer = ""
        self.repetition_count = 0
        self.last_sentence = ""
    
    def get_final_cleaned_text(self) -> str:
        """Get the final cleaned text"""
        return self.manager.clean_response(self.buffer)


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_stop_tokens_for_model(model_type: str) -> Tuple[str, ...]:
    """
    Get appropriate stop tokens based on model type
    """
    manager = StopGenerationManager()
    
    if model_type == "gguf":
        # GGUF models benefit from comprehensive stop tokens
        return manager.get_comprehensive_stop_tokens()
    elif model_type == "hf":
        # HF models need internal markers
        return PROMPT_ECHO_STOP_MARKERS + INTERNAL_MODEL_MARKERS
    elif model_type == "net":
        # Net models need conversation markers
        return PROMPT_ECHO_STOP_MARKERS + CONVERSATION_MARKERS
    else:
        return PROMPT_ECHO_STOP_MARKERS


def should_stop_generation(
    text: str,
    confidence_score: float = 0.0,
    config: Optional[StopConfig] = None
) -> Tuple[bool, str]:
    """
    Convenience function to check if generation should stop
    """
    manager = StopGenerationManager(config)
    return manager.detect_stop_condition(text, confidence_score)


def clean_llm_response(
    text: str,
    config: Optional[StopConfig] = None
) -> str:
    """
    Convenience function to clean LLM response
    """
    manager = StopGenerationManager(config)
    return manager.clean_response(text)


# ============================================================
# GLOBAL INSTANCES
# ============================================================

_default_manager = StopGenerationManager()
_default_detector = StreamingStopDetector()


def get_default_stop_manager() -> StopGenerationManager:
    """Get the default stop generation manager"""
    return _default_manager


def get_default_streaming_detector() -> StreamingStopDetector:
    """Get the default streaming stop detector"""
    return _default_detector
