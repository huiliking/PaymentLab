"""
Provider Registry — Plug-and-Play Token Extraction
====================================================
Each provider (Claude, OpenAI, Ollama, etc.) returns usage data in a
different response shape. This module normalises them all into a single
UsageRecord so the metering layer never has to know which LLM was called.

Adding a new provider
---------------------
1. Create  providers/<name>.py  with an  extract(response) -> UsageRecord  function
2. Register it:  ProviderRegistry.register("<name>", extract)
3. Done — no changes to the metering service, decorator, or database.

Option C — Metered Client Wrapper (future upgrade)
---------------------------------------------------
Instead of calling  meter.record(response, provider="claude")  inside your
business logic (Option B, current implementation), you can wrap the API
client itself so every call is metered automatically:

    class MeteredAnthropicClient:
        def __init__(self, real_client, metering_service, customer_id):
            self._client = real_client
            self._meter  = metering_service
            self._customer_id = customer_id

        def create(self, **kwargs):
            response = self._client.messages.create(**kwargs)
            record   = ProviderRegistry.extract("claude", response)
            self._meter.record_usage(record, self._customer_id)
            return response                     # caller gets normal response

Usage in FraudInvestigator:
    self.client = MeteredAnthropicClient(
        real_client  = anthropic.Anthropic(),
        metering_svc = metering_service,
        customer_id  = merchant_id,
    )
    # Then every  self.client.create(...)  is auto-metered.
    # No  meter.record()  calls needed in the investigation loop.

Pros vs Option B:
  + Zero metering code in business logic
  + Impossible to forget — every API call is metered
  + Easy to swap in/out (swap client reference)
  - Adds an indirection layer
  - Harder to attach per-call metadata (tool names, turn number)
  - Needs one wrapper class per provider SDK

This is how LangSmith, Helicone, and OpenAI's built-in usage tracking
instrument LLM calls — they wrap the client, not the function.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, Callable, Dict
import uuid


@dataclass
class UsageRecord:
    """
    Provider-agnostic usage record.
    Every extractor returns one of these, regardless of which LLM was called.
    """
    # Token counts
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Model identification
    provider: str = ""          # "claude", "openai", "ollama"
    model: str = ""             # "claude-sonnet-4-20250514", "gpt-4o", "llama3.2:1b"

    # Cost (USD) — calculated by the extractor using the pricing table
    cost_usd: float = 0.0

    # Metadata — extractors can attach provider-specific info
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    # Auto-populated
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def is_valid(self) -> bool:
        """Sanity check — at minimum we need tokens and a provider"""
        return self.total_tokens > 0 and self.provider != ""


class ProviderRegistry:
    """
    Maps provider names to extractor functions.
    
    An extractor takes a raw API response and returns a UsageRecord.
    This is the only place provider-specific logic lives.
    """

    _extractors: Dict[str, Callable[[Any], UsageRecord]] = {}

    @classmethod
    def register(cls, provider_name: str, extractor_fn: Callable[[Any], UsageRecord]):
        """Register an extractor for a provider"""
        cls._extractors[provider_name] = extractor_fn
        print(f"  [METERING] Registered provider: {provider_name}")

    @classmethod
    def extract(cls, provider_name: str, response: Any) -> UsageRecord:
        """Extract a UsageRecord from a provider's raw response"""
        if provider_name not in cls._extractors:
            raise ValueError(
                f"Unknown provider '{provider_name}'. "
                f"Registered: {list(cls._extractors.keys())}. "
                f"Add an extractor in providers/{provider_name}.py"
            )
        return cls._extractors[provider_name](response)

    @classmethod
    def list_providers(cls) -> list:
        """List all registered providers"""
        return list(cls._extractors.keys())

    @classmethod
    def is_registered(cls, provider_name: str) -> bool:
        return provider_name in cls._extractors


# ── Auto-register built-in providers on package import ─────────────────
# Each provider module calls ProviderRegistry.register() at import time.
# Adding these imports here means `from services.providers import ...`
# automatically registers all built-in extractors.
from . import claude   # noqa: F401, E402
from . import ollama   # noqa: F401, E402
# from . import openai  # uncomment when ready
