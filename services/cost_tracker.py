"""Cost tracking for LLM API calls."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (as of latest OpenAI pricing)
MODEL_PRICING = {
    "gpt-4o-mini": {
        "input": 0.15,      # $0.15 per 1M input tokens
        "output": 0.60,     # $0.60 per 1M output tokens
    },
    "gpt-4o": {
        "input": 2.50,      # $2.50 per 1M input tokens
        "output": 10.00,    # $10.00 per 1M output tokens
    },
    "gpt-4-turbo": {
        "input": 10.00,     # $10.00 per 1M input tokens
        "output": 30.00,    # $30.00 per 1M output tokens
    },
}


@dataclass
class APICallCost:
    """Represents the cost of a single API call."""
    
    model: str
    input_tokens: int
    output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    
    def __str__(self) -> str:
        return (
            f"Model: {self.model} | "
            f"Input: {self.input_tokens} tokens (${self.input_cost:.6f}) | "
            f"Output: {self.output_tokens} tokens (${self.output_cost:.6f}) | "
            f"Total: ${self.total_cost:.6f}"
        )


class CostTracker:
    """Tracks cumulative cost across multiple API calls."""
    
    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
        self.calls_log: list[APICallCost] = []
    
    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> APICallCost:
        """Calculate cost for a single API call."""
        if model not in MODEL_PRICING:
            logger.warning(f"Unknown model '{model}'. Using gpt-4o-mini pricing as fallback.")
            model = "gpt-4o-mini"
        
        pricing = MODEL_PRICING[model]
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        total_cost = input_cost + output_cost
        
        return APICallCost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
        )
    
    def track_call(self, model: str, input_tokens: int, output_tokens: int) -> APICallCost:
        """Track a single API call and log the cost."""
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost.total_cost
        self.call_count += 1
        self.calls_log.append(cost)
        
        # Log the cost
        logger.info(f"[API Call #{self.call_count}] {cost}")
        
        return cost
    
    def get_summary(self) -> dict:
        """Get a summary of all tracked costs."""
        return {
            "total_calls": self.call_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            "average_cost_per_call": self.total_cost / self.call_count if self.call_count > 0 else 0,
        }
    
    def log_summary(self):
        """Log a summary of cumulative costs."""
        summary = self.get_summary()
        logger.info(
            f"[COST SUMMARY] Calls: {summary['total_calls']} | "
            f"Input: {summary['total_input_tokens']} tokens | "
            f"Output: {summary['total_output_tokens']} tokens | "
            f"Total Cost: ${summary['total_cost']:.6f} | "
            f"Avg/Call: ${summary['average_cost_per_call']:.6f}"
        )
    
    def reset(self):
        """Reset all tracking data."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
        self.calls_log = []


# Global cost tracker instance
_cost_tracker = CostTracker()


def get_cost_tracker() -> CostTracker:
    """Get the global cost tracker instance."""
    return _cost_tracker
