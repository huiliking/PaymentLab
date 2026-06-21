"""
Unit tests for server/services/providers/claude.py

Run from server/ directory:
    python -m pytest tests/test_claude_provider.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import MagicMock
from services.providers.claude import extract_claude, _get_rates, PRICING_TABLE


# ── Helpers ────────────────────────────────────────────────────────────────

def make_response(
    input_tokens=1000,
    output_tokens=200,
    cache_read_input_tokens=0,
    cache_creation_input_tokens=0,
    model="claude-sonnet-4-20260101",
    stop_reason="end_turn",
    content=None,
    message_id="msg_test123",
):
    """Build a mock Anthropic SDK Message response."""
    resp = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    resp.usage.cache_read_input_tokens = cache_read_input_tokens
    resp.usage.cache_creation_input_tokens = cache_creation_input_tokens
    resp.model = model
    resp.stop_reason = stop_reason
    resp.id = message_id
    resp.content = content or []
    return resp


def make_tool_use_block():
    block = MagicMock()
    block.type = "tool_use"
    return block


# ── _get_rates ──────────────────────────────────────────────────────────────

class TestGetRates:
    def test_sonnet4_prefix(self):
        rates = _get_rates("claude-sonnet-4-20260101")
        assert rates == (3.00, 15.00)

    def test_haiku4_prefix(self):
        rates = _get_rates("claude-haiku-4-20260101")
        assert rates == (0.80, 4.00)

    def test_opus4_prefix(self):
        rates = _get_rates("claude-opus-4-20260101")
        assert rates == (15.00, 75.00)

    def test_case_insensitive(self):
        assert _get_rates("CLAUDE-SONNET-4-xyz") == _get_rates("claude-sonnet-4-xyz")

    def test_unknown_model_falls_back_to_default(self):
        rates = _get_rates("claude-unknown-future-model")
        assert rates == PRICING_TABLE["_default"]

    def test_unknown_string_falls_back_to_default(self):
        assert _get_rates("gpt-4o") == PRICING_TABLE["_default"]


# ── extract_claude — token extraction ──────────────────────────────────────

class TestExtractTokens:
    def test_basic_token_extraction(self):
        resp = make_response(input_tokens=500, output_tokens=100)
        record = extract_claude(resp)
        assert record.input_tokens == 500
        assert record.output_tokens == 100
        assert record.total_tokens == 600

    def test_cache_tokens_extracted_to_metadata(self):
        resp = make_response(cache_read_input_tokens=300, cache_creation_input_tokens=150)
        record = extract_claude(resp)
        assert record.raw_metadata["cache_read_tokens"] == 300
        assert record.raw_metadata["cache_creation_tokens"] == 150

    def test_no_cache_tokens_defaults_to_zero(self):
        resp = make_response()
        record = extract_claude(resp)
        assert record.raw_metadata["cache_read_tokens"] == 0
        assert record.raw_metadata["cache_creation_tokens"] == 0

    def test_none_cache_tokens_treated_as_zero(self):
        """API may return None instead of 0 for cache fields."""
        resp = make_response()
        resp.usage.cache_read_input_tokens = None
        resp.usage.cache_creation_input_tokens = None
        record = extract_claude(resp)
        assert record.raw_metadata["cache_read_tokens"] == 0
        assert record.raw_metadata["cache_creation_tokens"] == 0

    def test_provider_is_claude(self):
        record = extract_claude(make_response())
        assert record.provider == "claude"

    def test_model_extracted(self):
        record = extract_claude(make_response(model="claude-opus-4-20260101"))
        assert record.model == "claude-opus-4-20260101"

    def test_tool_calls_counted(self):
        content = [make_tool_use_block(), make_tool_use_block(), MagicMock(type="text")]
        record = extract_claude(make_response(content=content))
        assert record.raw_metadata["tool_calls"] == 2

    def test_stop_reason_in_metadata(self):
        record = extract_claude(make_response(stop_reason="tool_use"))
        assert record.raw_metadata["stop_reason"] == "tool_use"

    def test_message_id_in_metadata(self):
        record = extract_claude(make_response(message_id="msg_abc"))
        assert record.raw_metadata["message_id"] == "msg_abc"


# ── extract_claude — cost calculation ──────────────────────────────────────

class TestCostCalculation:
    """
    Sonnet 4 rates: $3.00/M input, $15.00/M output
    Cache write: 1.25× input = $3.75/M
    Cache read:  0.10× input = $0.30/M
    """
    INPUT_RATE = 3.00
    OUTPUT_RATE = 15.00

    def test_no_cache_cost(self):
        """Pure uncached: input_tokens billed at full input rate."""
        resp = make_response(input_tokens=1_000_000, output_tokens=0)
        record = extract_claude(resp)
        expected = 1_000_000 * self.INPUT_RATE / 1_000_000
        assert abs(record.cost_usd - expected) < 0.000001

    def test_output_only_cost(self):
        resp = make_response(input_tokens=0, output_tokens=1_000_000)
        record = extract_claude(resp)
        expected = 1_000_000 * self.OUTPUT_RATE / 1_000_000
        assert abs(record.cost_usd - expected) < 0.000001

    def test_cache_read_costs_ten_percent_of_input_rate(self):
        """1M cache_read tokens should cost $0.30 (0.10 × $3.00)."""
        resp = make_response(input_tokens=0, output_tokens=0,
                             cache_read_input_tokens=1_000_000)
        record = extract_claude(resp)
        expected = 1_000_000 * self.INPUT_RATE * 0.10 / 1_000_000  # $0.30
        assert abs(record.cost_usd - expected) < 0.000001

    def test_cache_creation_costs_125_percent_of_input_rate(self):
        """1M cache_creation tokens should cost $3.75 (1.25 × $3.00)."""
        resp = make_response(input_tokens=0, output_tokens=0,
                             cache_creation_input_tokens=1_000_000)
        record = extract_claude(resp)
        expected = 1_000_000 * self.INPUT_RATE * 1.25 / 1_000_000  # $3.75
        assert abs(record.cost_usd - expected) < 0.000001

    def test_input_tokens_not_double_charged_when_cache_present(self):
        """
        KEY TEST: Per Anthropic API, input_tokens is already the uncached-only
        count. Cache tokens are reported separately and must NOT be subtracted
        from input_tokens before billing — that would under-charge.
        """
        resp = make_response(
            input_tokens=500,
            output_tokens=0,
            cache_read_input_tokens=300,
            cache_creation_input_tokens=0,
        )
        record = extract_claude(resp)
        # input: 500 × $3/M = $0.0015
        # cache_read: 300 × $0.30/M = $0.00009
        expected = (500 * self.INPUT_RATE / 1_000_000 +
                    300 * self.INPUT_RATE * 0.10 / 1_000_000)
        assert abs(record.cost_usd - expected) < 0.000001

    def test_mixed_all_token_types(self):
        """All four token types billed correctly in one response."""
        resp = make_response(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=5000,
            cache_creation_input_tokens=2000,
            model="claude-sonnet-4-test",
        )
        record = extract_claude(resp)
        expected = (
            1000 * self.INPUT_RATE          / 1_000_000 +
            2000 * self.INPUT_RATE * 1.25   / 1_000_000 +
            5000 * self.INPUT_RATE * 0.10   / 1_000_000 +
            200  * self.OUTPUT_RATE          / 1_000_000
        )
        assert abs(record.cost_usd - expected) < 0.000001

    def test_cache_saves_money_vs_no_cache(self):
        """Re-reading 1M tokens from cache should cost less than reading them fresh."""
        no_cache  = extract_claude(make_response(input_tokens=1_000_000, output_tokens=0))
        with_cache = extract_claude(make_response(input_tokens=0, output_tokens=0,
                                                  cache_read_input_tokens=1_000_000))
        assert with_cache.cost_usd < no_cache.cost_usd

    def test_haiku_uses_different_rate(self):
        sonnet = extract_claude(make_response(input_tokens=1_000_000, model="claude-sonnet-4-x"))
        haiku  = extract_claude(make_response(input_tokens=1_000_000, model="claude-haiku-4-x"))
        assert haiku.cost_usd < sonnet.cost_usd


# ── Resilience ─────────────────────────────────────────────────────────────

class TestResilience:
    def test_missing_usage_attribute_returns_zero_cost(self):
        """If response has no .usage, should not raise — return zero-cost record."""
        resp = MagicMock(spec=[])  # no attributes at all
        resp.model = "claude-sonnet-4-test"
        resp.id = "msg_x"
        resp.stop_reason = "end_turn"
        resp.content = []
        record = extract_claude(resp)
        assert record.cost_usd == 0.0
        assert record.input_tokens == 0

    def test_corrupted_response_does_not_raise(self):
        """extract_claude should never throw — metering must be silent on errors."""
        resp = MagicMock()
        resp.usage.input_tokens = "not_an_int"  # bad type
        try:
            record = extract_claude(resp)
            # if it returns, cost should be 0 or some safe value
        except Exception as e:
            pytest.fail(f"extract_claude raised unexpectedly: {e}")
