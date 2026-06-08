"""
OpenAI Provider Extractor (Stub)
================================
Ready for when you integrate OpenAI models.

Response shape (OpenAI SDK):
    response.usage.prompt_tokens      -> int
    response.usage.completion_tokens  -> int
    response.usage.total_tokens       -> int
    response.model                    -> str  (e.g. "gpt-4o-2024-08-06")
    response.id                       -> str  (e.g. "chatcmpl-abc123")

To activate:
    1. Fill in PRICING_TABLE with current rates
    2. Uncomment the ProviderRegistry.register() line at the bottom
    3. Import this module in providers/__init__.py or metering.py
"""

from . import UsageRecord, ProviderRegistry


PRICING_TABLE = {
    # Update with current OpenAI pricing
    # https://openai.com/api/pricing/
    "gpt-4o":       (2.50,  10.00),   # per 1M tokens
    "gpt-4o-mini":  (0.15,   0.60),
    "gpt-4-turbo":  (10.00, 30.00),
    "o1":           (15.00, 60.00),
    "_default":     (2.50,  10.00),
}


def _get_rates(model_name: str) -> tuple:
    model_lower = model_name.lower()
    for prefix, rates in PRICING_TABLE.items():
        if prefix != "_default" and model_lower.startswith(prefix):
            return rates
    return PRICING_TABLE["_default"]


def extract_openai(response) -> UsageRecord:
    """
    Extract usage from an OpenAI SDK ChatCompletion response.
    
    Args:
        response: openai.types.chat.ChatCompletion from client.chat.completions.create()
    """
    input_tokens = 0
    output_tokens = 0
    model = "unknown"

    try:
        if hasattr(response, 'usage') and response.usage:
            input_tokens = getattr(response.usage, 'prompt_tokens', 0)
            output_tokens = getattr(response.usage, 'completion_tokens', 0)
        model = getattr(response, 'model', 'unknown')
    except Exception as e:
        print(f"  [METERING] Warning: Could not extract OpenAI usage: {e}")

    total_tokens = input_tokens + output_tokens
    input_rate, output_rate = _get_rates(model)
    cost_usd = (input_tokens * input_rate / 1_000_000) + \
               (output_tokens * output_rate / 1_000_000)

    return UsageRecord(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider="openai",
        model=model,
        cost_usd=round(cost_usd, 6),
        raw_metadata={
            "completion_id": getattr(response, 'id', ''),
        }
    )


# UNCOMMENT when ready to use:
# ProviderRegistry.register("openai", extract_openai)
