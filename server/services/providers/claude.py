"""
Claude Provider Extractor
=========================
Extracts token usage from Anthropic SDK response objects.

Response shape (Anthropic SDK):
    response.usage.input_tokens                -> int
    response.usage.output_tokens               -> int
    response.usage.cache_read_input_tokens     -> int  (0 if no cache hit)
    response.usage.cache_creation_input_tokens -> int  (0 if no cache write)
    response.model                             -> str  (e.g. "claude-sonnet-4-20250514")
    response.id                                -> str  (message ID)
    response.stop_reason                       -> str  ("end_turn", "tool_use", etc.)

Pricing (as of May 2026):
    Claude Sonnet 4   : $3.00 / M input,  $15.00 / M output
    Claude Haiku  4.5 : $0.80 / M input,   $4.00 / M output
    Claude Opus   4   : $15.00 / M input,  $75.00 / M output

    Prompt cache pricing (applied to input_rate):
        Cache writes : 1.25× input rate  (charged once to build the cache)
        Cache reads  : 0.10× input rate  (~90% savings vs uncached)

    Update PRICING_TABLE when Anthropic publishes new rates.
"""

from . import UsageRecord, ProviderRegistry


# Pricing per 1M tokens (USD)
# Source: https://docs.anthropic.com/en/docs/about-claude/models
PRICING_TABLE = {
    # Model string prefix → (input_per_M, output_per_M)
    "claude-sonnet-4":     (3.00,  15.00),
    "claude-4-5-sonnet":   (3.00,  15.00),   # alternate naming
    "claude-haiku-4":      (0.80,   4.00),
    "claude-4-5-haiku":    (0.80,   4.00),
    "claude-opus-4":       (15.00, 75.00),
    # Fallback for unknown Claude models
    "_default":            (3.00,  15.00),
}


def _get_rates(model_name: str) -> tuple:
    """Look up per-token rates for a model, falling back to default"""
    model_lower = model_name.lower()
    for prefix, rates in PRICING_TABLE.items():
        if prefix != "_default" and model_lower.startswith(prefix):
            return rates
    return PRICING_TABLE["_default"]


def extract_claude(response) -> UsageRecord:
    """
    Extract usage from an Anthropic SDK Message response.
    
    Args:
        response: anthropic.types.Message object from client.messages.create()
    
    Returns:
        UsageRecord with token counts, cost, and metadata
    """
    # Extract token counts from SDK response
    input_tokens = 0
    output_tokens = 0
    model = "unknown"

    cache_read_tokens = 0
    cache_creation_tokens = 0

    try:
        if hasattr(response, 'usage'):
            input_tokens = int(getattr(response.usage, 'input_tokens', 0) or 0)
            output_tokens = int(getattr(response.usage, 'output_tokens', 0) or 0)
            cache_read_tokens = int(getattr(response.usage, 'cache_read_input_tokens', 0) or 0)
            cache_creation_tokens = int(getattr(response.usage, 'cache_creation_input_tokens', 0) or 0)

        model = getattr(response, 'model', 'unknown')
    except Exception as e:
        print(f"  [METERING] Warning: Could not extract Claude usage: {e}")

    total_tokens = input_tokens + output_tokens

    # Calculate cost with correct cache pricing.
    # Per Anthropic API docs, input_tokens is ALREADY the uncached-only count —
    # it does NOT include cache_read or cache_creation tokens. Those are billed
    # separately at their own rates.
    input_rate, output_rate = _get_rates(model)
    cost_usd = (
        input_tokens          * input_rate          / 1_000_000 +   # uncached input
        cache_creation_tokens * input_rate * 1.25   / 1_000_000 +   # cache write: 1.25× input
        cache_read_tokens     * input_rate * 0.10   / 1_000_000 +   # cache read:  0.10× input
        output_tokens         * output_rate          / 1_000_000     # output unchanged
    )

    # Count tool_use blocks in content (if any)
    tool_calls = 0
    stop_reason = ""
    try:
        if hasattr(response, 'content'):
            for block in response.content:
                if hasattr(block, 'type') and block.type == 'tool_use':
                    tool_calls += 1
        stop_reason = getattr(response, 'stop_reason', '')
    except:
        pass

    return UsageRecord(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider="claude",
        model=model,
        cost_usd=round(cost_usd, 6),
        raw_metadata={
            "message_id": getattr(response, 'id', ''),
            "stop_reason": stop_reason,
            "tool_calls": tool_calls,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
        }
    )


# Auto-register when this module is imported
ProviderRegistry.register("claude", extract_claude)
