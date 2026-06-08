"""
Ollama Provider Extractor
=========================
Extracts token usage from Ollama API responses.

Response shape (Ollama /api/generate):
    response["prompt_eval_count"]  -> int  (input tokens)
    response["eval_count"]         -> int  (output tokens)
    response["model"]              -> str  (e.g. "llama3.2:1b")

Response shape (Ollama /api/chat):
    Same fields, same locations.

Pricing:
    Ollama runs locally — no API cost. We track tokens for
    comparison and capacity planning, but cost_usd = 0.
    If you ever proxy to a hosted Ollama (e.g. via Replicate),
    add rates to PRICING_TABLE.
"""

from . import UsageRecord, ProviderRegistry


# Local Ollama = free. Add hosted rates here if needed.
PRICING_TABLE = {
    "_default": (0.0, 0.0),  # (input_per_M, output_per_M)
}


def extract_ollama(response) -> UsageRecord:
    """
    Extract usage from an Ollama API response (dict from requests.json()).
    
    Args:
        response: dict from Ollama's /api/generate or /api/chat endpoint
    
    Returns:
        UsageRecord with token counts (cost = 0 for local)
    """
    if not isinstance(response, dict):
        return UsageRecord(provider="ollama", model="unknown")

    input_tokens = response.get("prompt_eval_count", 0) or 0
    output_tokens = response.get("eval_count", 0) or 0
    total_tokens = input_tokens + output_tokens
    model = response.get("model", "unknown")

    return UsageRecord(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider="ollama",
        model=model,
        cost_usd=0.0,
        raw_metadata={
            "total_duration_ns": response.get("total_duration", 0),
            "load_duration_ns": response.get("load_duration", 0),
            "done": response.get("done", False),
        }
    )


# Auto-register when this module is imported
ProviderRegistry.register("ollama", extract_ollama)
