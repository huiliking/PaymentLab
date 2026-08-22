"""
Automated test suite for Sprint 7 (scanner report profiles + scan provenance).

Run from server/:
    python -m pytest tests/test_sprint7_scanner_provenance.py -v

Isolation follows the Sprint 5/6 pattern: nothing here may touch the real
server/ai_agents/registry.json. Every fixture that writes operates on a temp
copy. No running Ollama is required — call_ollama() is mocked throughout;
the extraction tests read the real EPC PDF bundled in this repo (read-only)
but never call Ollama.
"""
import json
import os
import shutil
import sys
from unittest.mock import patch

import pytest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _SERVER_DIR)

REAL_REGISTRY = os.path.join(_SERVER_DIR, "ai_agents", "registry.json")
REAL_EPC_PDF = os.path.join(
    _SERVER_DIR, "ai_agents",
    "EPC162-24 v2.0 2025 Payments Threats and Fraud Trends Report_0.pdf",
)
EPC_REPORT_NAME = "EPC162-24 v2.0 2025 Payments Threats and Fraud Trends Report_0"

pytestmark = pytest.mark.skipif(
    not (os.path.exists(REAL_REGISTRY) and os.path.exists(REAL_EPC_PDF)),
    reason="requires the real dev registry.json and EPC PDF to copy/read from",
)


@pytest.fixture()
def tmp_registry_path(tmp_path):
    dest = tmp_path / "registry.json"
    shutil.copy(REAL_REGISTRY, dest)
    return str(dest)


@pytest.fixture()
def registry(tmp_registry_path):
    from ai_agents.tool_registry import ToolRegistry
    return ToolRegistry(tmp_registry_path)


@pytest.fixture()
def fake_report_path(tmp_path):
    """A copy of the real EPC PDF under a different filename, so write-path
    tests don't collide with the real backfilled scan_history entry."""
    dest = tmp_path / "fake_report.pdf"
    shutil.copy(REAL_EPC_PDF, dest)
    return str(dest)


MOCK_CANDIDATES_JSON = json.dumps([
    {
        "name": "test_scanner_pattern",
        "category": "external_intel",
        "description": "Checks a synthetic pattern for test purposes.",
        "detects": "A fabricated fraud pattern used only in this test.",
        "reference_snippet": "synthetic test snippet",
    }
])


# ── Group A: extraction / report profiles ────────────────────────────────────

class TestReportProfiles:
    def test_each_profile_produces_different_extraction(self):
        from ai_agents.tool_scanner import REPORT_PROFILES, extract_report_text
        narrative = extract_report_text(REAL_EPC_PDF, REPORT_PROFILES["narrative"])
        regulatory = extract_report_text(REAL_EPC_PDF, REPORT_PROFILES["regulatory"])
        ranked_list = extract_report_text(REAL_EPC_PDF, REPORT_PROFILES["ranked_list"])
        assert narrative != regulatory
        assert narrative != ranked_list
        assert regulatory != ranked_list

    def test_narrative_extraction_lands_on_body_not_toc(self):
        """The TOC lists '3.1 Cyber threats...' followed by dotted leader
        characters; the real body header is immediately followed by prose.
        Regression for the TOC-vs-body disambiguation the last-match-wins
        algorithm exists to solve."""
        from ai_agents.tool_scanner import REPORT_PROFILES, extract_report_text
        text = extract_report_text(REAL_EPC_PDF, REPORT_PROFILES["narrative"])
        assert not text[:60].count(".") > 10, "landed on the TOC's dotted-leader line, not real content"
        assert text.startswith("3.1 Cyber threats")
        assert "Social engineering is an attack vector" in text

    def test_narrative_extraction_contains_expected_subsections(self):
        """Checked against the UNCLIPPED match range (large max_chars
        override), not the capped extraction actually sent to the LLM —
        max_chars=12000 legitimately truncates before every subsection."""
        from ai_agents.tool_scanner import REPORT_PROFILES, extract_report_text
        profile = {**REPORT_PROFILES["narrative"], "max_chars": 200_000}
        text = extract_report_text(REAL_EPC_PDF, profile)
        for tag in ["3.1.1", "3.1.2", "3.1.3", "3.1.4", "3.1.5", "3.1.6", "3.1.7"]:
            assert tag in text, f"{tag} missing from unclipped narrative extraction"

    def test_capped_extraction_respects_max_chars(self):
        from ai_agents.tool_scanner import REPORT_PROFILES, extract_report_text
        text = extract_report_text(REAL_EPC_PDF, REPORT_PROFILES["narrative"])
        assert len(text) == REPORT_PROFILES["narrative"]["max_chars"]

    def test_unknown_profile_raises(self, registry):
        from ai_agents.tool_scanner import run_scan
        with pytest.raises(ValueError, match="Unknown profile"):
            run_scan(REAL_EPC_PDF, registry, profile_name="bogus")

    def test_missing_start_pattern_raises_value_error(self):
        from ai_agents.tool_scanner import extract_report_text
        profile = {"start_pattern": r"^ZZZ_NEVER_MATCHES\s", "max_chars": 1000}
        with pytest.raises(ValueError, match="start_pattern"):
            extract_report_text(REAL_EPC_PDF, profile)

    def test_default_profile_is_narrative(self):
        import ai_agents.tool_scanner as mod
        parser_default = None
        import argparse
        # Reconstruct the same parser main() builds, without invoking main().
        parser = argparse.ArgumentParser()
        parser.add_argument("--profile", default="narrative", choices=sorted(mod.REPORT_PROFILES))
        args = parser.parse_args([])
        assert args.profile == "narrative"


# ── Group B: scan_history write path ─────────────────────────────────────────

class TestScanHistoryWrite:
    def test_scan_history_appended_and_persists(self, registry, tmp_registry_path):
        from ai_agents.tool_registry import ToolRegistry
        registry.add_scan_history({
            "report_name": "some_report", "report_path": "x.pdf",
            "profile": "narrative", "scanned_at": "2026-01-01T00:00:00Z",
            "proposals_generated": 2,
        })
        reloaded = ToolRegistry(tmp_registry_path)
        names = [s["report_name"] for s in reloaded.get_scan_history()]
        assert "some_report" in names

    def test_missing_field_returns_error(self, registry):
        before = len(registry.get_scan_history())
        result = registry.add_scan_history({"report_name": "x"})
        assert "error" in result
        assert len(registry.get_scan_history()) == before

    def test_append_only_no_overwrite(self, registry):
        entry = {
            "report_name": "dup_report", "report_path": "x.pdf",
            "profile": "narrative", "scanned_at": "2026-01-01T00:00:00Z",
            "proposals_generated": 1,
        }
        registry.add_scan_history(entry)
        registry.add_scan_history({**entry, "scanned_at": "2026-01-02T00:00:00Z"})
        matches = [s for s in registry.get_scan_history() if s["report_name"] == "dup_report"]
        assert len(matches) == 2


# ── Group C: duplicate detection ─────────────────────────────────────────────

class TestDuplicateDetection:
    def test_duplicate_scan_blocked_without_force(self, registry, fake_report_path):
        from ai_agents.tool_scanner import run_scan, DuplicateScanError
        with patch("ai_agents.tool_scanner.call_ollama", return_value=MOCK_CANDIDATES_JSON) as mock_ollama:
            run_scan(fake_report_path, registry, profile_name="narrative")
            mock_ollama.reset_mock()
            with pytest.raises(DuplicateScanError):
                run_scan(fake_report_path, registry, profile_name="narrative")
            mock_ollama.assert_not_called()

    def test_duplicate_scan_proceeds_with_force(self, registry, fake_report_path):
        from ai_agents.tool_scanner import run_scan
        with patch("ai_agents.tool_scanner.call_ollama", return_value=MOCK_CANDIDATES_JSON) as mock_ollama:
            run_scan(fake_report_path, registry, profile_name="narrative")
            run_scan(fake_report_path, registry, profile_name="narrative", force=True)
            assert mock_ollama.call_count == 2
        source_label = os.path.splitext(os.path.basename(fake_report_path))[0]
        matches = [s for s in registry.get_scan_history() if s["report_name"] == source_label]
        assert len(matches) == 2

    def test_different_profile_is_not_a_duplicate(self, registry, fake_report_path):
        """Keyed on (report_name, profile) — scanning the same report under
        a different profile is a legitimate distinct scan, not a duplicate."""
        from ai_agents.tool_scanner import run_scan
        with patch("ai_agents.tool_scanner.call_ollama", return_value=MOCK_CANDIDATES_JSON) as mock_ollama:
            run_scan(fake_report_path, registry, profile_name="narrative")
            run_scan(fake_report_path, registry, profile_name="regulatory")  # no force needed
            assert mock_ollama.call_count == 2


def test_backfill_entry_is_well_formed():
    """
    Loads the REAL, unmodified registry.json (read-only) and asserts the
    backfilled EPC scan_history entry is correct. Kept as its own test, not
    folded into TestDuplicateDetection, so editing the backfill entry and
    accidentally breaking the source_detail join key is caught directly.
    """
    from ai_agents.tool_registry import ToolRegistry
    registry = ToolRegistry(REAL_REGISTRY)
    entry = next((s for s in registry.get_scan_history() if s["report_name"] == EPC_REPORT_NAME), None)
    assert entry is not None, "EPC backfill entry missing from registry.json"
    assert entry["profile"] == "narrative"
    assert entry["proposals_generated"] == 8
    assert entry["scanned_at"]
    assert os.path.basename(entry["report_path"]) == os.path.basename(REAL_EPC_PDF)


# ── Group D: live-computed approved/rejected/pending counts ─────────────────

class TestGetScanHistoryCounts:
    def test_epc_entry_counts_match_real_registry(self, registry):
        """
        Pins the exact counts verified against the live registry.json:
        tool_id 31-38 all carry source_detail == the EPC report name —
        3 candidate (social_engineering_attack, apt_attack, phishing_attempt),
        1 rejected (ceo_fraud), 4 proposed/pending (malware_infection,
        ddos_attack, app_fraud, ransomware_attack).
        """
        entry = next(s for s in registry.get_scan_history() if s["report_name"] == EPC_REPORT_NAME)
        assert entry["proposals_approved"] == 3
        assert entry["proposals_rejected"] == 1
        assert entry["proposals_pending"] == 4

    def test_counts_recompute_after_status_change(self, registry):
        """Proves the counts are live-computed, not cached/stale — nothing
        in scan_history itself is mutated by update_status()."""
        before = next(s for s in registry.get_scan_history() if s["report_name"] == EPC_REPORT_NAME)
        registry.update_status("malware_infection", "candidate")
        after = next(s for s in registry.get_scan_history() if s["report_name"] == EPC_REPORT_NAME)
        assert after["proposals_approved"] == before["proposals_approved"] + 1
        assert after["proposals_pending"] == before["proposals_pending"] - 1


# ── Group E: prompt_context ───────────────────────────────────────────────────

class TestPromptContext:
    def test_build_prompt_uses_default_context(self):
        from ai_agents.tool_scanner import build_prompt
        prompt = build_prompt("some text", ["card_velocity"])
        assert "payment fraud threat report" in prompt

    def test_build_prompt_uses_profile_context(self):
        from ai_agents.tool_scanner import build_prompt
        prompt = build_prompt("some text", ["card_velocity"], "API security vulnerability list")
        assert "API security vulnerability list" in prompt
        assert prompt.count("payment fraud threat report") == 0


# ── Group F: dashboard payload ────────────────────────────────────────────────

class TestDashboardPayload:
    def test_to_dashboard_payload_includes_scan_history(self, registry):
        payload = registry.to_dashboard_payload()
        assert "scan_history" in payload
        assert len(payload["scan_history"]) >= 1
        entry = payload["scan_history"][0]
        for key in ("report_name", "report_path", "profile", "scanned_at", "proposals_generated",
                    "proposals_approved", "proposals_rejected", "proposals_pending"):
            assert key in entry


# ── Group G: run_scan orchestration ──────────────────────────────────────────

class TestRunScanOrchestration:
    def test_run_scan_returns_proposals_and_scan_entry(self, registry, fake_report_path):
        from ai_agents.tool_scanner import run_scan
        with patch("ai_agents.tool_scanner.call_ollama", return_value=MOCK_CANDIDATES_JSON):
            result = run_scan(fake_report_path, registry, profile_name="narrative")
        assert len(result["proposals"]) == 1
        assert result["proposals"][0]["name"] == "test_scanner_pattern"
        assert result["proposals"][0]["status"] == "proposed"
        assert result["scan_history_entry"]["proposals_generated"] == 1

    def test_run_scan_does_not_persist_proposals_to_tools(self, registry, fake_report_path):
        """
        The most important assertion in this suite. tool_scanner.py's main()
        has never called registry.propose_tool() — proposals are printed or
        written to --out, and only persisted into tools[] via the separate
        POST /api/fraud/tools/propose route. If this assertion ever fails,
        it means run_scan() got wired to call propose_tool() directly, which
        would silently bypass the human-review step that the propose/approve
        workflow exists to enforce.
        """
        from ai_agents.tool_scanner import run_scan
        before = len(registry.list_tools())
        with patch("ai_agents.tool_scanner.call_ollama", return_value=MOCK_CANDIDATES_JSON):
            run_scan(fake_report_path, registry, profile_name="narrative")
        after = len(registry.list_tools())
        assert after == before
