"""
Metering Engine — Standalone Validation Test
=============================================
Run this without the full PaymentLab stack to verify:
  1. Provider extractors produce valid UsageRecords
  2. MeteringService writes and reads correctly
  3. Aggregation math is accurate
  4. Failure isolation works (bad data doesn't crash)

Usage:
    cd server
    python -m services.test_metering

Or from repo root:
    python server/services/test_metering.py
"""

import sys
import os
import json
import uuid
from datetime import datetime, timezone, timedelta

# Add server/ to path so imports work regardless of where you run from
_this_dir = os.path.dirname(os.path.abspath(__file__))
_server_dir = os.path.dirname(_this_dir)  # go up from services/ to server/
sys.path.insert(0, _server_dir)

from services.providers import UsageRecord, ProviderRegistry
from services.providers.claude import extract_claude
from services.providers.ollama import extract_ollama
from services.metering import MeteringService


# ── Test Fixtures ──────────────────────────────────────────────

class MockClaudeUsage:
    """Mimics anthropic.types.Usage"""
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

class MockClaudeTextBlock:
    def __init__(self):
        self.type = "text"
        self.text = "This is a test response."

class MockClaudeToolUseBlock:
    def __init__(self):
        self.type = "tool_use"
        self.id = "toolu_test"
        self.name = "get_email_history"
        self.input = {"email": "test@example.com"}

class MockClaudeResponse:
    """Mimics anthropic.types.Message"""
    def __init__(self, input_tokens=16633, output_tokens=1047, 
                 model="claude-sonnet-4-20250514", tool_calls=0):
        self.usage = MockClaudeUsage(input_tokens, output_tokens)
        self.model = model
        self.id = f"msg_{uuid.uuid4().hex[:12]}"
        self.stop_reason = "tool_use" if tool_calls > 0 else "end_turn"
        self.content = [MockClaudeTextBlock()]
        for _ in range(tool_calls):
            self.content.append(MockClaudeToolUseBlock())


def mock_ollama_response(input_tokens=500, output_tokens=150, model="llama3.2:1b"):
    """Mimics Ollama /api/generate JSON response"""
    return {
        "model": model,
        "prompt_eval_count": input_tokens,
        "eval_count": output_tokens,
        "total_duration": 2_500_000_000,
        "load_duration": 100_000_000,
        "done": True,
        "response": "Test response from Ollama"
    }


# ── Tests ──────────────────────────────────────────────────────

def test_provider_registry():
    """Test that providers auto-registered on import"""
    print("\n[TEST 1] Provider Registry")

    assert ProviderRegistry.is_registered("claude"), "Claude not registered"
    assert ProviderRegistry.is_registered("ollama"), "Ollama not registered"
    assert not ProviderRegistry.is_registered("openai"), "OpenAI should not be registered (stub)"

    providers = ProviderRegistry.list_providers()
    print(f"  Registered: {providers}")
    print(f"  ✓ PASS")


def test_claude_extractor():
    """Test Claude response → UsageRecord extraction"""
    print("\n[TEST 2] Claude Extractor")

    # Simulate Investigation 1 from Week 2 logs
    response = MockClaudeResponse(
        input_tokens=16633, output_tokens=1047,
        model="claude-sonnet-4-20250514", tool_calls=3
    )

    record = extract_claude(response)

    assert record.provider == "claude"
    assert record.input_tokens == 16633
    assert record.output_tokens == 1047
    assert record.total_tokens == 17680
    assert record.model == "claude-sonnet-4-20250514"

    # Verify cost: (16633 × $3/M) + (1047 × $15/M)
    expected_cost = (16633 * 3.0 / 1_000_000) + (1047 * 15.0 / 1_000_000)
    assert abs(record.cost_usd - round(expected_cost, 6)) < 0.0001, \
        f"Cost mismatch: {record.cost_usd} vs {expected_cost}"

    assert record.raw_metadata["tool_calls"] == 3
    assert record.is_valid

    print(f"  Tokens: {record.input_tokens:,} in + {record.output_tokens:,} out")
    print(f"  Cost:   ${record.cost_usd:.6f}")
    print(f"  Tools:  {record.raw_metadata['tool_calls']}")
    print(f"  ✓ PASS")


def test_ollama_extractor():
    """Test Ollama response → UsageRecord extraction"""
    print("\n[TEST 3] Ollama Extractor")

    response = mock_ollama_response(input_tokens=500, output_tokens=150)
    record = extract_ollama(response)

    assert record.provider == "ollama"
    assert record.input_tokens == 500
    assert record.output_tokens == 150
    assert record.total_tokens == 650
    assert record.cost_usd == 0.0  # Local = free
    assert record.model == "llama3.2:1b"
    assert record.is_valid

    print(f"  Tokens: {record.input_tokens} in + {record.output_tokens} out")
    print(f"  Cost:   ${record.cost_usd} (local)")
    print(f"  ✓ PASS")


def test_metering_service():
    """Test full record → query cycle"""
    print("\n[TEST 4] Metering Service (Record + Query)")

    db_path = "/tmp/test_metering.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    meter = MeteringService(db_path=db_path)
    meter.init_db()

    # Simulate a 3-turn investigation
    session_id = str(uuid.uuid4())
    customer_id = "merchant_acme"

    turn_data = [
        (16633, 1047, 3),   # Turn 1: initial analysis + 3 tool calls
        (18327, 892, 4),    # Turn 2: follow-up + 4 tool calls
        (20150, 1346, 0),   # Turn 3: final verdict, no tools
    ]

    total_cost = 0.0
    total_input = 0
    total_output = 0

    for turn, (inp, out, tools) in enumerate(turn_data, 1):
        response = MockClaudeResponse(
            input_tokens=inp, output_tokens=out, tool_calls=tools
        )
        record = meter.record(
            response=response,
            provider="claude",
            customer_id=customer_id,
            event_type="fraud_investigation",
            session_id=session_id,
            metadata={"turn": turn, "transaction_id": "txn_test_123"},
        )
        assert record is not None, f"Turn {turn} recording failed"
        total_cost += record.cost_usd
        total_input += inp
        total_output += out

    # Query back
    events = meter.get_usage_by_customer(customer_id)
    assert len(events) == 3, f"Expected 3 events, got {len(events)}"

    # Check summary
    summary = meter.get_usage_summary(customer_id, period_days=1)
    assert summary["totals"]["total_events"] == 3
    assert summary["totals"]["total_sessions"] == 1
    assert summary["totals"]["total_input_tokens"] == total_input
    assert summary["totals"]["total_output_tokens"] == total_output
    assert abs(summary["totals"]["total_cost_usd"] - total_cost) < 0.001

    # Check session detail
    detail = meter.get_session_detail(session_id)
    assert detail["totals"]["turns"] == 3
    assert detail["totals"]["total_tool_calls"] == 7  # 3+4+0

    print(f"  Events recorded:  {len(events)}")
    print(f"  Total tokens:     {total_input + total_output:,}")
    print(f"  Total cost:       ${total_cost:.4f}")
    print(f"  Session turns:    {detail['totals']['turns']}")
    print(f"  Session tools:    {detail['totals']['total_tool_calls']}")
    print(f"  ✓ PASS")

    # Cleanup
    os.remove(db_path)


def test_failure_isolation():
    """Test that metering failures don't propagate"""
    print("\n[TEST 5] Failure Isolation")

    db_path = "/tmp/test_metering_fail.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    meter = MeteringService(db_path=db_path)
    meter.init_db()

    # Test 1: Bad provider name → returns None, no crash
    result = meter.record(
        response={"garbage": True},
        provider="nonexistent_provider",
        customer_id="test",
    )
    assert result is None, "Should return None for unknown provider"

    # Test 2: Invalid response shape → returns None, no crash
    result = meter.record(
        response=None,
        provider="claude",
        customer_id="test",
    )
    assert result is None, "Should return None for bad response"

    # Test 3: Empty Ollama response → returns None (0 tokens = invalid)
    result = meter.record(
        response={},
        provider="ollama",
        customer_id="test",
    )
    assert result is None, "Should return None for empty response"

    print(f"  Bad provider:  handled gracefully ✓")
    print(f"  None response: handled gracefully ✓")
    print(f"  Empty response: handled gracefully ✓")
    print(f"  ✓ PASS — metering failures are silent")

    os.remove(db_path)


def test_cost_breakdown():
    """Test time-series cost breakdown for charting"""
    print("\n[TEST 6] Cost Breakdown (Time Series)")

    db_path = "/tmp/test_metering_breakdown.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    meter = MeteringService(db_path=db_path)
    meter.init_db()

    # Record 5 events across 2 event types
    for i in range(3):
        response = MockClaudeResponse(input_tokens=10000, output_tokens=500)
        meter.record(response, provider="claude",
                     customer_id="merchant_x", event_type="fraud_investigation")

    for i in range(2):
        response = mock_ollama_response(input_tokens=800, output_tokens=200)
        meter.record(response, provider="ollama",
                     customer_id="merchant_x", event_type="address_generation")

    # Breakdown by event type
    breakdown = meter.get_cost_breakdown("merchant_x", group_by="event_type")
    assert len(breakdown) == 2, f"Expected 2 event types, got {len(breakdown)}"

    fraud_row = next(r for r in breakdown if r["period"] == "fraud_investigation")
    assert fraud_row["events"] == 3

    addr_row = next(r for r in breakdown if r["period"] == "address_generation")
    assert addr_row["events"] == 2
    assert addr_row["cost_usd"] == 0.0  # Ollama is free

    print(f"  Event types:   {len(breakdown)}")
    print(f"  Fraud events:  {fraud_row['events']} (${fraud_row['cost_usd']:.4f})")
    print(f"  Address events: {addr_row['events']} (${addr_row['cost_usd']:.4f})")
    print(f"  ✓ PASS")

    os.remove(db_path)


def test_health():
    """Test health endpoint data"""
    print("\n[TEST 7] Health Check")

    db_path = "/tmp/test_metering_health.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    meter = MeteringService(db_path=db_path)
    meter.init_db()

    # Record one event
    response = MockClaudeResponse(input_tokens=5000, output_tokens=300)
    meter.record(response, provider="claude", customer_id="health_test")

    health = meter.health()
    assert health["status"] == "healthy"
    assert health["total_events"] == 1
    assert health["total_customers"] == 1
    assert "claude" in health["active_providers"]
    assert "claude" in health["registered_providers"]
    assert "ollama" in health["registered_providers"]

    print(f"  Status:     {health['status']}")
    print(f"  Events:     {health['total_events']}")
    print(f"  Providers:  {health['registered_providers']}")
    print(f"  DB size:    {health['database_size_bytes']} bytes")
    print(f"  ✓ PASS")

    os.remove(db_path)


# ── Run All ────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("METERING ENGINE — VALIDATION TEST SUITE")
    print("=" * 60)

    tests = [
        test_provider_registry,
        test_claude_extractor,
        test_ollama_extractor,
        test_metering_service,
        test_failure_isolation,
        test_cost_breakdown,
        test_health,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed}/{passed + failed} passed")
    if failed == 0:
        print("All tests passed ✓")
    else:
        print(f"{failed} test(s) FAILED ✗")
    print("=" * 60)
