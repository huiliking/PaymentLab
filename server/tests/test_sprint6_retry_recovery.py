"""
Automated test suite for Sprint 6 (retry-recovery for hallucinated tool calls).

Run from server/:
    python -m pytest tests/test_sprint6_retry_recovery.py -v

Isolation follows the Sprint 5 pattern exactly: nothing here may touch the
real server/payment_lab.db or server/ai_agents/registry.json. Every fixture
operates on a temp copy (see `tmp_paths`).

No running Ollama and no ANTHROPIC_API_KEY are required — the LLM-dependent
paths are exercised by mocking `_llm_plan()` / `_llm_verdict()` for the Ollama
loop and the Anthropic client for the Claude loop.

test_sprint5_business_layer.py is NOT modified. The backwards-compat proof
(TestSprint5BackwardsCompat) re-runs the Sprint 5 gate assertions against the
modified _dispatch() here.
"""
import difflib
import itertools
import json
import os
import shutil
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _SERVER_DIR)

REAL_DB = os.path.join(_SERVER_DIR, "payment_lab.db")
REAL_REGISTRY = os.path.join(_SERVER_DIR, "ai_agents", "registry.json")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(REAL_DB) and os.path.exists(REAL_REGISTRY)),
    reason="requires the real dev payment_lab.db and registry.json to copy from",
)

# A name that is not in the registry under any status — the canonical
# hallucination, observed live (the real tool is get_email_history).
BAD_NAME = "check_email_history"
# Its correct counterpart, for the self-correction cases.
GOOD_NAME = "get_email_history"
# In the registry, status=candidate — a real name, so NOT a hallucination.
INACTIVE_NAME = "check_amount_anomaly"


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_paths(tmp_path):
    tmp_db = tmp_path / "payment_lab.db"
    tmp_registry = tmp_path / "registry.json"
    shutil.copy(REAL_DB, tmp_db)
    shutil.copy(REAL_REGISTRY, tmp_registry)
    return {"db": str(tmp_db), "registry": str(tmp_registry)}


def _make_investigator(tmp_paths, **kwargs):
    from ai_agents.fraud_investigator import FraudInvestigator
    kwargs.setdefault("db_path", tmp_paths["db"])
    kwargs.setdefault("registry_path", tmp_paths["registry"])
    return FraudInvestigator(**kwargs)


@pytest.fixture()
def investigator(tmp_paths):
    """Same grant as the Sprint 5 dispatch fixture, deliberately."""
    return _make_investigator(
        tmp_paths,
        allowed_tools=["get_transaction_details", "check_locale_consistency"],
    )


@pytest.fixture()
def unrestricted(tmp_paths):
    """No merchant resolved — the CLI case."""
    return _make_investigator(tmp_paths)


@pytest.fixture()
def active_names(tmp_paths):
    with open(tmp_paths["registry"], encoding="utf-8") as f:
        data = json.load(f)
    return [t["name"] for t in data["tools"] if t["status"] == "active"]


def _a_transaction(db_path):
    conn = sqlite3.connect(db_path)
    txn_id = conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()[0]
    conn.close()
    return txn_id


def _flagged_transaction(db_path, registry_path):
    """A transaction the pre-screen actually flags, so investigate() doesn't
    short-circuit before reaching the LLM loop."""
    from ai_agents.fraud_investigator import RuleEngine
    engine = RuleEngine(db_path)
    conn = sqlite3.connect(db_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM transactions").fetchall()]
    conn.close()
    for txn_id in ids:
        if engine.pre_screen(txn_id):
            return txn_id
    pytest.skip("fixture DB has no transaction that trips the pre-screen")


def _fresh_context(investigator, txn_id):
    """The context dict investigate() builds, without running investigate()."""
    txn = investigator.tools.get_transaction_details(txn_id).get("result", {})
    return {
        "transaction": txn,
        "pre_screen_triggers": [{"rule": "TEST", "risk": "HIGH", "detail": ""}],
        "evidence_gathered": [],
        "tools_called": [],
        "corrections": [],
    }


# ── Group A: the _dispatch() classifier ─────────────────────────────────────

class TestDispatchClassification:
    def test_dispatch_classifies_unknown_tool_as_hallucination(self, investigator):
        result = investigator._dispatch(BAD_NAME, {"email": "x@y.com"})
        assert result["error_class"] == "unknown_tool"
        assert BAD_NAME in result["error"]
        assert "is not a valid tool" in result["error"]
        # Alternatives come from the merchant's grant, never the full
        # registry — the business layer owns policy, this layer consumes it.
        assert "get_transaction_details" in result["error"]
        assert "check_locale_consistency" in result["error"]
        assert "check_velocity" not in result["error"]

    def test_dispatch_classifies_out_of_grant_as_not_granted(self, investigator):
        result = investigator._dispatch("check_velocity", {"card_last4": "4242"})
        assert result["error_class"] == "not_granted"
        # Message must be byte-identical to Sprint 5's.
        assert result["error"] == "Tool 'check_velocity' is not granted for this merchant's tier"

    def test_dispatch_hallucination_without_allowed_tools(self, unrestricted, active_names):
        """The hallucination check fires regardless of merchant resolution."""
        result = unrestricted._dispatch(BAD_NAME, {})
        assert result["error_class"] == "unknown_tool"
        # With no grant to filter by, all active tools are offered.
        for name in active_names:
            assert name in result["error"]

    def test_no_not_granted_class_when_allowed_tools_is_none(self, unrestricted):
        """
        With allowed_tools=None there is no grant to be outside of, so
        "not_granted" is unreachable: a real name goes straight to execute.
        """
        result = unrestricted._dispatch("check_velocity", {"card_last4": "4242"})
        assert result.get("error_class") != "not_granted"

    def test_dispatch_inactive_tool_is_not_hallucination(self, unrestricted):
        """A candidate-status tool is a real name, not a fumble."""
        result = unrestricted._dispatch(INACTIVE_NAME, {})
        assert result.get("error_class") != "unknown_tool"
        assert "error" in result
        assert "not active" in result["error"]

    def test_dispatch_in_grant_tool_still_executes(self, investigator, tmp_paths):
        txn_id = _a_transaction(tmp_paths["db"])
        result = investigator._dispatch("get_transaction_details", {"txn_id": txn_id})
        assert "error" not in result
        assert "error_class" not in result

    def test_hallucination_never_reaches_registry_execute(self, investigator):
        """
        Ordering rule: classification is a read-only lookup that must happen
        before any execution path.
        """
        investigator.registry.execute = MagicMock()
        investigator._dispatch(BAD_NAME, {})
        investigator.registry.execute.assert_not_called()


class TestPerNameRepeatCap:
    def test_per_name_repeat_cap(self, investigator):
        first = investigator._dispatch(BAD_NAME, {})
        investigator._note_hallucination(BAD_NAME)
        second = investigator._dispatch(BAD_NAME, {})
        investigator._note_hallucination(BAD_NAME)
        third = investigator._dispatch(BAD_NAME, {})

        assert "Available tools:" in first["error"]
        assert "already corrected" in second["error"]
        assert "already corrected" in third["error"]
        assert all(r["error_class"] == "unknown_tool" for r in (first, second, third))

    def test_cap_is_name_sensitive_not_params_sensitive(self, investigator):
        """Same bad name, different params = one bad name, one correction."""
        attempts_1, terminal_1 = investigator._note_hallucination(BAD_NAME)
        attempts_2, terminal_2 = investigator._note_hallucination(BAD_NAME)
        assert (attempts_1, terminal_1) == (1, False)
        assert (attempts_2, terminal_2) == (2, True)
        assert investigator._hallucination_attempts == {BAD_NAME: 2}

    def test_distinct_bad_names_tracked_separately(self, investigator):
        investigator._note_hallucination(BAD_NAME)
        investigator._note_hallucination("check_chargeback_history")
        assert investigator._hallucination_attempts == {
            BAD_NAME: 1, "check_chargeback_history": 1
        }


# ── Group B: the fuzzy resolution threshold ─────────────────────────────────

def _prefix_swap(name):
    """The dominant real-world fumble: check_ <-> get_ on the same noun."""
    if name.startswith("get_"):
        return "check_" + name[4:]
    if name.startswith("check_"):
        return "get_" + name[6:]
    return None


class TestFuzzyResolutionThreshold:
    """
    Pins HALLUCINATION_MATCH_CUTOFF against the live registry.

    The invariant is scoped to hallucination→real pairs only. Real tool names
    are never the left-hand argument of the matcher — the fuzzy check runs
    solely for names absent from the registry — so active/active similarity
    is irrelevant here and deliberately not asserted. (Several legitimate
    active pairs do exceed the cutoff: get_email_history/get_device_history
    scores 0.800. That is not a defect.)
    """

    def test_fuzzy_resolution_threshold(self, active_names):
        from ai_agents.fraud_investigator import HALLUCINATION_MATCH_CUTOFF as CUT

        checked = 0
        for target in active_names:
            fumble = _prefix_swap(target)
            if fumble is None or fumble in active_names:
                continue
            checked += 1

            hit = difflib.SequenceMatcher(None, fumble, target).ratio()
            assert hit >= CUT, (
                f"fumble {fumble!r} no longer resolves to its intended target "
                f"{target!r} ({hit:.3f} < {CUT}) — the cutoff needs revisiting"
            )

            for other in active_names:
                if other == target:
                    continue
                ratio = difflib.SequenceMatcher(None, fumble, other).ratio()
                assert ratio < CUT, (
                    f"fumble {fumble!r} now also matches unrelated tool "
                    f"{other!r} ({ratio:.3f} >= {CUT}) — the cutoff no longer "
                    f"separates; a hallucination would resolve to the wrong tool"
                )

        assert checked >= 5, "expected the registry to yield several prefix-swap pairs"

    @pytest.mark.parametrize("unrelated", [
        "get_customer_profile",
        "check_chargeback_history",
        "do_something_entirely_different",
    ])
    def test_unrelated_names_resolve_to_nothing(self, active_names, unrelated):
        from ai_agents.fraud_investigator import HALLUCINATION_MATCH_CUTOFF as CUT
        best = max(
            difflib.SequenceMatcher(None, unrelated, name).ratio()
            for name in active_names
        )
        assert best < CUT, (
            f"{unrelated!r} has no real counterpart but matched at {best:.3f}"
        )


# ── Group C: caveat population and confidence cap ───────────────────────────

def _report_with(confidence=0.95, summary="Base summary."):
    from ai_agents.fraud_investigator import InvestigationReport
    r = InvestigationReport(transaction_id="txn_test")
    r.confidence = confidence
    r.summary = summary
    return r


class TestReportCaveats:
    def test_report_unresolved_checks_populated(self, investigator):
        report = _report_with()
        context = {"tools_called": ["get_transaction_details"]}
        investigator._hallucination_attempts = {BAD_NAME: 2}

        investigator._apply_unresolved_caveats(context, report)

        assert report.unresolved_checks == [
            {"tool_name": BAD_NAME, "attempts": 2, "error_class": "unknown_tool"}
        ]
        assert "could not be completed" in report.summary
        assert report.to_dict()["unresolved_checks"] == report.unresolved_checks

    def test_confidence_capped_at_085_with_one_unresolved(self, investigator):
        report = _report_with(confidence=0.95)
        investigator._hallucination_attempts = {BAD_NAME: 1}
        investigator._apply_unresolved_caveats({"tools_called": []}, report)
        assert report.confidence == 0.85

    def test_confidence_capped_at_07_with_two_unresolved(self, investigator):
        report = _report_with(confidence=0.95)
        investigator._hallucination_attempts = {BAD_NAME: 1, "check_chargeback_history": 1}
        investigator._apply_unresolved_caveats({"tools_called": []}, report)
        assert report.confidence == 0.7

    def test_low_confidence_is_capped_not_raised(self, investigator):
        """Cap, don't set — a model that returned 0.5 stays at 0.5."""
        report = _report_with(confidence=0.5)
        investigator._hallucination_attempts = {BAD_NAME: 1}
        investigator._apply_unresolved_caveats({"tools_called": []}, report)
        assert report.confidence == 0.5

    def test_self_corrected_name_is_resolved(self, investigator):
        """
        Fumbled check_email_history, then successfully called
        get_email_history → the intended check happened. No entry, no cap.
        """
        report = _report_with(confidence=0.95, summary="All evidence gathered.")
        context = {"tools_called": ["get_transaction_details", GOOD_NAME]}
        investigator._hallucination_attempts = {BAD_NAME: 1}

        investigator._apply_unresolved_caveats(context, report)

        assert report.unresolved_checks == []
        assert report.confidence == 0.95
        assert report.summary == "All evidence gathered."

    def test_resolution_matches_through_skipped_dup_suffix(self, investigator):
        """The Ollama dedup suffix must be stripped, not skipped."""
        report = _report_with(confidence=0.95)
        context = {"tools_called": [f"{GOOD_NAME} (skipped-dup)"]}
        investigator._hallucination_attempts = {BAD_NAME: 1}

        investigator._apply_unresolved_caveats(context, report)

        assert report.unresolved_checks == []
        assert report.confidence == 0.95

    def test_unrelated_successful_call_does_not_resolve(self, investigator):
        report = _report_with(confidence=0.95)
        context = {"tools_called": ["get_ip_reputation", "check_velocity"]}
        investigator._hallucination_attempts = {BAD_NAME: 1}

        investigator._apply_unresolved_caveats(context, report)

        assert [e["tool_name"] for e in report.unresolved_checks] == [BAD_NAME]
        assert report.confidence == 0.85

    def test_out_of_grant_does_not_caveat(self, investigator):
        """
        An out-of-grant refusal records nothing, so caveat population is a
        no-op: no entry, no cap, no summary note.
        """
        investigator._dispatch("check_velocity", {"card_last4": "4242"})
        assert investigator._hallucination_attempts == {}

        report = _report_with(confidence=0.95, summary="Base summary.")
        investigator._apply_unresolved_caveats({"tools_called": []}, report)

        assert report.unresolved_checks == []
        assert report.confidence == 0.95
        assert report.summary == "Base summary."


# ── Group D: Ollama loop integration ────────────────────────────────────────

def _stub_ollama(investigator, plans, verdict=None):
    """
    Drive _ollama_investigate without a live model. `plans` is the sequence
    _llm_plan returns, one per iteration.
    """
    seq = list(plans)

    def fake_plan(context, iteration):
        return seq[iteration] if iteration < len(seq) else {"tool": "CONCLUDE", "reasoning": "done"}

    investigator._llm_plan = fake_plan
    investigator._llm_verdict = lambda context: (verdict or {
        "risk_level": "HIGH", "verdict": "SUSPICIOUS",
        "confidence": 0.95, "summary": "Stub verdict.",
    })


class TestOllamaLoop:
    def test_hallucination_does_not_pollute_evidence(self, investigator, tmp_paths):
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        _stub_ollama(investigator, [
            {"tool": BAD_NAME, "params": {}, "reasoning": "fumble"},
            {"tool": "CONCLUDE", "reasoning": "done"},
        ])
        # Keep the mandatory-graph fallback out of this assertion.
        investigator.allowed_tools = ["get_transaction_details"]
        investigator._ollama_investigate(context, report)

        assert context["evidence_gathered"] == []
        assert context["tools_called"] == []
        assert report.tool_results == {}
        assert not any(s.tool_used == BAD_NAME for s in report.steps)

        phases = [s.phase for s in report.steps]
        assert "CORRECTION" in phases
        gather_steps = [s for s in report.steps if s.phase == "GATHER"]
        assert all(s.tool_used != BAD_NAME for s in gather_steps)

    def test_correction_added_to_context_channel(self, investigator, tmp_paths):
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        _stub_ollama(investigator, [{"tool": BAD_NAME, "params": {}, "reasoning": "fumble"}])
        investigator.allowed_tools = ["get_transaction_details"]
        investigator._ollama_investigate(context, report)

        assert len(context["corrections"]) == 1
        correction = context["corrections"][0]
        assert correction["tool_name"] == BAD_NAME
        assert correction["terminal"] is False
        assert "Available tools:" in correction["message"]

    def test_corrections_surface_in_next_prompt(self, investigator, tmp_paths):
        """_llm_plan must show the model what it got wrong."""
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        context["corrections"].append({
            "tool_name": BAD_NAME,
            "message": f"'{BAD_NAME}' is not a valid tool. Available tools: get_transaction_details",
            "terminal": False,
        })

        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["prompt"] = json["prompt"]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "REASONING: x\nTOOL: CONCLUDE\nPARAMS: none"}
            return resp

        import ai_agents.fraud_investigator as mod
        original = mod.requests.post
        mod.requests.post = fake_post
        try:
            investigator._llm_plan(context, 0)
        finally:
            mod.requests.post = original

        assert "TOOL NAME CORRECTIONS:" in captured["prompt"]
        assert BAD_NAME in captured["prompt"]

    def test_terminal_corrections_are_not_shown_in_prompt(self, investigator, tmp_paths):
        """
        KNOWN_ISSUES #12 regression: a name that already went terminal must
        stop appearing in the prompt. The model got the corrective message
        once and the terminal message once — re-displaying the bad name after
        that only gives a name-starved model something to copy back out, which
        is exactly what a live Ollama run did (attempts climbed from an
        injected 2 to an observed 4).

        A non-terminal correction for a DIFFERENT name must still render —
        this is a filter on terminal entries, not a blanket suppression of
        the whole block.
        """
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        context["corrections"].append({
            "tool_name": BAD_NAME,
            "message": f"'{BAD_NAME}' is not a valid tool. This tool was already "
                       f"corrected and remains unavailable. Proceeding without it.",
            "terminal": True,
        })
        context["corrections"].append({
            "tool_name": "check_card_velocity",
            "message": "'check_card_velocity' is not a valid tool. Available tools: get_transaction_details",
            "terminal": False,
        })

        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["prompt"] = json["prompt"]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "REASONING: x\nTOOL: CONCLUDE\nPARAMS: none"}
            return resp

        import ai_agents.fraud_investigator as mod
        original = mod.requests.post
        mod.requests.post = fake_post
        try:
            investigator._llm_plan(context, 0)
        finally:
            mod.requests.post = original

        assert BAD_NAME not in captured["prompt"]
        assert "TOOL NAME CORRECTIONS:" in captured["prompt"]
        assert "check_card_velocity" in captured["prompt"]

    def test_all_terminal_corrections_suppresses_whole_block(self, investigator, tmp_paths):
        """When every correction on record is terminal, the block must not
        render at all — not an empty 'TOOL NAME CORRECTIONS:' header."""
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        context["corrections"].append({
            "tool_name": BAD_NAME,
            "message": f"'{BAD_NAME}' is not a valid tool. This tool was already "
                       f"corrected and remains unavailable. Proceeding without it.",
            "terminal": True,
        })

        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["prompt"] = json["prompt"]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "REASONING: x\nTOOL: CONCLUDE\nPARAMS: none"}
            return resp

        import ai_agents.fraud_investigator as mod
        original = mod.requests.post
        mod.requests.post = fake_post
        try:
            investigator._llm_plan(context, 0)
        finally:
            mod.requests.post = original

        assert "TOOL NAME CORRECTIONS:" not in captured["prompt"]
        assert BAD_NAME not in captured["prompt"]

    def test_correction_shown_at_most_once_across_calls(self, investigator, tmp_paths):
        """
        Regression for the actual KNOWN_ISSUES #12 root cause: the terminal
        filter alone did NOT stop the bad name from reappearing, because
        context["corrections"] was never pruned — the first (always
        non-terminal, since the repeat cap is 1) entry for a name stayed in
        the list for the rest of the investigation and kept re-entering
        every future prompt. A live run confirmed the literal corrective
        string present at 7 of 8 turns despite the terminal filter.

        _llm_plan must consume corrections on read: a name shown on one call
        must not appear on the next call, even with no new correction added
        in between.
        """
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        context["corrections"].append({
            "tool_name": BAD_NAME,
            "message": f"'{BAD_NAME}' is not a valid tool. Available tools: get_transaction_details",
            "terminal": False,
        })

        prompts = []

        def fake_post(url, json=None, timeout=None):
            prompts.append(json["prompt"])
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "REASONING: x\nTOOL: CONCLUDE\nPARAMS: none"}
            return resp

        import ai_agents.fraud_investigator as mod
        original = mod.requests.post
        mod.requests.post = fake_post
        try:
            investigator._llm_plan(context, 0)
            investigator._llm_plan(context, 1)
            investigator._llm_plan(context, 2)
        finally:
            mod.requests.post = original

        assert len(prompts) == 3
        assert BAD_NAME in prompts[0]
        assert BAD_NAME not in prompts[1]
        assert BAD_NAME not in prompts[2]
        assert context["corrections"] == []

    def test_no_corrections_block_when_clean(self, investigator, tmp_paths):
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["prompt"] = json["prompt"]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "REASONING: x\nTOOL: CONCLUDE\nPARAMS: none"}
            return resp

        import ai_agents.fraud_investigator as mod
        original = mod.requests.post
        mod.requests.post = fake_post
        try:
            investigator._llm_plan(context, 0)
        finally:
            mod.requests.post = original

        assert "TOOL NAME CORRECTIONS:" not in captured["prompt"]

    def test_hallucination_does_not_increment_consecutive_skips(self, investigator, tmp_paths):
        """
        Three hallucinations in a row must not trip the 2-consecutive-skips
        termination — that guard is for repeated *valid* calls.
        """
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        _stub_ollama(investigator, [
            {"tool": BAD_NAME, "params": {}, "reasoning": "fumble 1"},
            {"tool": "check_chargeback_history", "params": {}, "reasoning": "fumble 2"},
            {"tool": "get_customer_profile", "params": {}, "reasoning": "fumble 3"},
            {"tool": "get_transaction_details", "params": {"txn_id": txn_id}, "reasoning": "real"},
        ])
        investigator.allowed_tools = ["get_transaction_details"]
        investigator._ollama_investigate(context, report)

        # The loop survived all three and still made the real call.
        assert context["tools_called"] == ["get_transaction_details"]
        assert len(context["corrections"]) == 3

    def test_ollama_self_correction_end_to_end(self, investigator, tmp_paths):
        """Fumble, then the correct name → no caveat, confidence untouched."""
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        investigator.allowed_tools = ["get_transaction_details", GOOD_NAME]
        _stub_ollama(investigator, [
            {"tool": BAD_NAME, "params": {}, "reasoning": "fumble"},
            {"tool": GOOD_NAME, "params": {"email": "a@b.com"}, "reasoning": "corrected"},
            {"tool": "CONCLUDE", "reasoning": "done"},
        ])
        investigator._ollama_investigate(context, report)

        assert GOOD_NAME in context["tools_called"]
        assert report.unresolved_checks == []
        assert report.confidence == 0.95
        assert any(s.phase == "CORRECTION" for s in report.steps)

    def test_ollama_unresolved_end_to_end(self, investigator, tmp_paths):
        """Fumble with no recovery → caveat and cap."""
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        investigator.allowed_tools = ["get_transaction_details"]
        _stub_ollama(investigator, [
            {"tool": "get_customer_profile", "params": {}, "reasoning": "fumble"},
            {"tool": "get_transaction_details", "params": {"txn_id": txn_id}, "reasoning": "real"},
            {"tool": "CONCLUDE", "reasoning": "done"},
        ])
        investigator._ollama_investigate(context, report)

        assert [e["tool_name"] for e in report.unresolved_checks] == ["get_customer_profile"]
        assert report.confidence == 0.85
        assert "could not be completed" in report.summary

    def test_out_of_grant_still_lands_in_evidence(self, investigator, tmp_paths):
        """not_granted behavior is unchanged: the model should see the block."""
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        investigator.allowed_tools = ["get_transaction_details"]
        _stub_ollama(investigator, [
            {"tool": "check_velocity", "params": {"card_last4": "4242"}, "reasoning": "blocked"},
            {"tool": "CONCLUDE", "reasoning": "done"},
        ])
        investigator._ollama_investigate(context, report)

        assert "check_velocity" in context["tools_called"]
        assert any(e["tool"] == "check_velocity" for e in context["evidence_gathered"])
        assert report.unresolved_checks == []


# ── Group E: Claude loop integration ────────────────────────────────────────

def _block(type_, **kwargs):
    b = MagicMock()
    b.type = type_
    for k, v in kwargs.items():
        setattr(b, k, v)
    return b


def _response(blocks, stop_reason):
    r = MagicMock()
    r.content = blocks
    r.stop_reason = stop_reason
    r.usage = MagicMock(input_tokens=10, output_tokens=5,
                        cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return r


def _stub_claude(investigator, responses):
    investigator.use_claude = True
    investigator.claude = MagicMock()
    investigator.claude.messages.create = MagicMock(side_effect=responses)
    return investigator.claude.messages.create


VERDICT_TEXT = (
    "RISK_LEVEL: HIGH\nVERDICT: SUSPICIOUS\nCONFIDENCE: 0.95\n"
    "SUMMARY: Stub verdict."
)


class TestClaudeLoop:
    def test_claude_hallucination_returns_correction_as_tool_result(self, investigator, tmp_paths):
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        create = _stub_claude(investigator, [
            _response([_block("tool_use", id="tu_1", name=BAD_NAME, input={})], "tool_use"),
            _response([_block("text", text=VERDICT_TEXT)], "end_turn"),
        ])
        investigator._claude_investigate(context, report)

        # The follow-up user message carries a tool_result for the tool_use id.
        second_call_messages = create.call_args_list[1].kwargs["messages"]
        tool_results = [
            block
            for msg in second_call_messages if isinstance(msg["content"], list)
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert len(tool_results) == 1
        assert tool_results[0]["tool_use_id"] == "tu_1"
        assert "is not a valid tool" in tool_results[0]["content"]
        assert "Available tools:" in tool_results[0]["content"]

        assert context["evidence_gathered"] == []
        assert context["tools_called"] == []
        assert any(s.phase == "CORRECTION" for s in report.steps)

    def test_parallel_batch_hallucination_and_valid_call(self, investigator, tmp_paths):
        """
        One bad name plus one valid, DISSIMILAR tool in the same turn. The
        valid call must execute unaffected; both results ride back together.
        A dissimilar tool is used deliberately so this asserts independent
        processing without also asserting that a hedged batch caveats.
        """
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        create = _stub_claude(investigator, [
            _response([
                _block("tool_use", id="tu_bad", name=BAD_NAME, input={}),
                _block("tool_use", id="tu_ok", name="get_transaction_details",
                       input={"txn_id": txn_id}),
            ], "tool_use"),
            _response([_block("text", text=VERDICT_TEXT)], "end_turn"),
        ])
        investigator._claude_investigate(context, report)

        second_call_messages = create.call_args_list[1].kwargs["messages"]
        tool_results = [
            block
            for msg in second_call_messages if isinstance(msg["content"], list)
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        # Every tool_use id got a result — the API contract.
        assert {b["tool_use_id"] for b in tool_results} == {"tu_bad", "tu_ok"}

        # The valid call ran and was not blocked or delayed by the fumble.
        assert context["tools_called"] == ["get_transaction_details"]
        assert "get_transaction_details" in report.tool_results
        assert BAD_NAME not in report.tool_results

        # The fumble is unresolved (dissimilar valid call doesn't resolve it).
        assert [e["tool_name"] for e in report.unresolved_checks] == [BAD_NAME]

    def test_claude_hedged_batch_resolves(self, investigator, tmp_paths):
        """
        Model hedges: emits both the bad and the correct name in one turn.
        The correct call lands in tools_called, so the fumble resolves — no
        caveat, no cap.
        """
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)
        investigator.allowed_tools = ["get_transaction_details", GOOD_NAME]

        _stub_claude(investigator, [
            _response([
                _block("tool_use", id="tu_bad", name=BAD_NAME, input={}),
                _block("tool_use", id="tu_ok", name=GOOD_NAME, input={"email": "a@b.com"}),
            ], "tool_use"),
            _response([_block("text", text=VERDICT_TEXT)], "end_turn"),
        ])
        investigator._claude_investigate(context, report)

        assert GOOD_NAME in context["tools_called"]
        assert report.unresolved_checks == []
        assert report.confidence == 0.95

    def test_claude_out_of_grant_unchanged(self, investigator, tmp_paths):
        """not_granted is serialized into the tool_result exactly as before."""
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        _stub_claude(investigator, [
            _response([_block("tool_use", id="tu_1", name="check_velocity",
                              input={"card_last4": "4242"})], "tool_use"),
            _response([_block("text", text=VERDICT_TEXT)], "end_turn"),
        ])
        investigator._claude_investigate(context, report)

        assert "check_velocity" in context["tools_called"]
        assert report.unresolved_checks == []
        assert report.confidence == 0.95


# ── Group F: max_steps ──────────────────────────────────────────────────────

class TestMaxSteps:
    def test_defaults_are_per_backend(self, tmp_paths):
        inv = _make_investigator(tmp_paths)
        assert inv.max_steps == 6          # Claude
        assert inv.max_steps_ollama == 8   # Ollama

    def test_explicit_max_steps_overrides_both(self, tmp_paths):
        inv = _make_investigator(tmp_paths, max_steps=4)
        assert inv.max_steps == 4
        assert inv.max_steps_ollama == 4

    def test_explicit_max_steps_of_six_is_honored_for_ollama(self, tmp_paths):
        """The None sentinel's whole point: 6 passed != 6 defaulted."""
        inv = _make_investigator(tmp_paths, max_steps=6)
        assert inv.max_steps_ollama == 6

    def test_max_steps_ollama_overrides_independently(self, tmp_paths):
        inv = _make_investigator(tmp_paths, max_steps_ollama=12)
        assert inv.max_steps == 6
        assert inv.max_steps_ollama == 12

    def test_ollama_loop_runs_eight_iterations(self, investigator, tmp_paths):
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        iterations = []

        def counting_plan(ctx, iteration):
            iterations.append(iteration)
            return {"tool": "get_customer_profile", "params": {}, "reasoning": "fumble"}

        investigator._llm_plan = counting_plan
        investigator._llm_verdict = lambda ctx: {
            "risk_level": "LOW", "verdict": "LEGITIMATE",
            "confidence": 0.9, "summary": "s",
        }
        investigator.allowed_tools = ["get_transaction_details"]
        investigator._ollama_investigate(context, report)

        assert iterations == list(range(8))

    def test_prompt_shows_ollama_ceiling(self, investigator, tmp_paths):
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["prompt"] = json["prompt"]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"response": "REASONING: x\nTOOL: CONCLUDE\nPARAMS: none"}
            return resp

        import ai_agents.fraud_investigator as mod
        original = mod.requests.post
        mod.requests.post = fake_post
        try:
            investigator._llm_plan(context, 0)
        finally:
            mod.requests.post = original

        assert "ITERATION: 1 of 8" in captured["prompt"]


# ── Group G: regression / backwards compatibility ───────────────────────────

class TestSprint5BackwardsCompat:
    """
    The load-bearing proof: the Sprint 5 gate assertions, re-run verbatim
    against the modified _dispatch(). Same fixture grant, same assertions.
    """

    @pytest.fixture()
    def sprint5_investigator(self, tmp_paths):
        from ai_agents.fraud_investigator import FraudInvestigator
        return FraudInvestigator(
            db_path=tmp_paths["db"],
            registry_path=tmp_paths["registry"],
            allowed_tools=["get_transaction_details", "check_locale_consistency"],
        )

    def test_dispatch_allows_in_grant_tool(self, sprint5_investigator, tmp_paths):
        txn_id = _a_transaction(tmp_paths["db"])
        result = sprint5_investigator._dispatch("get_transaction_details", {"txn_id": txn_id})
        assert "error" not in result

    def test_dispatch_blocks_out_of_grant_tool_and_never_calls_underlying_method(
        self, sprint5_investigator
    ):
        mock_method = MagicMock(return_value={"tool": "check_velocity", "risk": "LOW"})
        sprint5_investigator.tools.check_velocity = mock_method

        result = sprint5_investigator._dispatch("check_velocity", {"card_last4": "4242"})

        assert "error" in result
        assert "not granted" in result["error"]
        mock_method.assert_not_called()


class TestNoRegression:
    def test_no_regression_clean_investigation(self, investigator, tmp_paths):
        """
        Zero hallucinations, zero refusals → the Sprint 5 shape exactly:
        same calls, empty unresolved_checks, untouched confidence and summary.
        """
        from ai_agents.fraud_investigator import InvestigationReport

        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(investigator, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        investigator.allowed_tools = ["get_transaction_details", "check_locale_consistency"]
        _stub_ollama(investigator, [
            {"tool": "get_transaction_details", "params": {"txn_id": txn_id}, "reasoning": "r"},
            {"tool": "check_locale_consistency", "params": {"txn_id": txn_id}, "reasoning": "r"},
            {"tool": "CONCLUDE", "reasoning": "done"},
        ])
        investigator._ollama_investigate(context, report)

        assert context["tools_called"] == ["get_transaction_details", "check_locale_consistency"]
        assert report.unresolved_checks == []
        assert report.confidence == 0.95
        assert report.summary == "Stub verdict."
        assert context["corrections"] == []
        assert not any(s.phase == "CORRECTION" for s in report.steps)
        assert investigator._hallucination_attempts == {}

    def test_to_dict_shape_is_additive_only(self, investigator):
        report = _report_with()
        keys = set(report.to_dict().keys())
        expected = {
            "transaction_id", "risk_level", "verdict", "confidence", "summary",
            "evidence", "steps", "created_at", "tool_results", "unresolved_checks",
        }
        assert keys == expected

    def test_attempts_reset_between_investigations(self, investigator, tmp_paths):
        """
        The CLI's --investigate-all reuses one investigator across a batch;
        one transaction's fumble must not caveat the next transaction.
        """
        investigator._hallucination_attempts = {BAD_NAME: 2}
        txn_id = _flagged_transaction(tmp_paths["db"], tmp_paths["registry"])

        _stub_ollama(investigator, [{"tool": "CONCLUDE", "reasoning": "done"}])
        investigator.allowed_tools = ["get_transaction_details"]
        report = investigator.investigate(txn_id)

        assert report.unresolved_checks == []


class TestAutoEscalation:
    def test_mandatory_graph_fallback_still_runs(self, tmp_paths):
        """
        The Ollama loop's post-loop check_identity_graph fallback must still
        fire, and its call must count toward resolution.
        """
        from ai_agents.fraud_investigator import InvestigationReport

        inv = _make_investigator(tmp_paths)  # unrestricted
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(inv, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        _stub_ollama(inv, [{"tool": "CONCLUDE", "reasoning": "done"}])
        inv._ollama_investigate(context, report)

        assert "check_identity_graph" in context["tools_called"]
        assert "check_identity_graph" in report.tool_results

    def test_fallback_skipped_when_out_of_grant(self, tmp_paths):
        from ai_agents.fraud_investigator import InvestigationReport

        inv = _make_investigator(tmp_paths, allowed_tools=["get_transaction_details"])
        txn_id = _a_transaction(tmp_paths["db"])
        context = _fresh_context(inv, txn_id)
        report = InvestigationReport(transaction_id=txn_id)

        _stub_ollama(inv, [{"tool": "CONCLUDE", "reasoning": "done"}])
        inv._ollama_investigate(context, report)

        assert "check_identity_graph" not in context["tools_called"]
        # An excluded tool is correct scope, not a gap.
        assert report.unresolved_checks == []
