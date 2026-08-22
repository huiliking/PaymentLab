"""
Tool Scanner — reads a fraud/security threat report (PDF) and proposes new
investigation tools for the registry.

Usage:
    python -m ai_agents.tool_scanner --report <path-to-pdf> [--out proposals.json]

Design:
    The scanner asks Ollama only to do what small local models are actually
    good at — identifying distinct fraud patterns in prose and describing
    them. It does NOT ask Ollama to hand-author the full registry.json
    schema (nested input_schema, category enum, etc.) — that structure is
    assembled deterministically in Python from a small extraction result,
    which keeps output valid even when the model output is messy.

Output entries are always status="proposed". A human must approve them via
the /tools dashboard (which flips status to "candidate") before they can be
promoted to "active" (which additionally requires an InvestigationTools
method to exist).

LIMITATION: for this one-shot proof, only a bounded window of the report
text (after the table of contents) is sent to the model in a single prompt.
A full pipeline would chunk the whole document and de-duplicate across
chunks; that's out of scope here.
"""

import sys
import os
import re
import json
import argparse
import datetime

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import requests
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_agents.tool_registry import ToolRegistry

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# Fallback defaults for any profile that doesn't specify its own
# skip_chars/max_chars (see REPORT_PROFILES below). Historically the only
# extraction constants this module had, before per-report profiles.
SKIP_CHARS = 8000
WINDOW_CHARS = 6000

# Per-report-type extraction configuration. Two extraction strategies:
#   - start_pattern/end_pattern (regex): locate a specific numbered section
#     by header and extract through to the next sibling section. Verified
#     against the real EPC162-24 PDF for "narrative" (see below) — the same
#     section number appears twice in the extracted text (once in the table
#     of contents, once as the real body header), so both patterns take the
#     LAST match before start / FIRST match after start respectively, since
#     a TOC always precedes the body it lists in a structured report.
#   - skip_chars (fallback): the original blind-offset approach, used by
#     "regulatory"/"ranked_list" below since no real EBA/ECB or OWASP PDF
#     exists in this repo yet to verify a section pattern against.
# max_chars caps whichever strategy is used — the model's default context
# window can't absorb a whole extracted section (the EPC "3.1" section alone
# is ~90,000 chars; llama3.2 via Ollama gets no num_ctx override in this
# codebase, so stays at Ollama's small default).
REPORT_PROFILES = {
    "narrative": {
        # Verified: start_pattern matches at chars [2406 (TOC), 53384 (real
        # body header, immediately followed by "3.1.1 Social Engineering...
        # Social engineering is an attack vector...")]. end_pattern's first
        # match after 53384 is at char 143589 ("3.2 Fraud per Payment-
        # Relevant Process"). The unclipped 53384:143589 window contains
        # subsections 3.1.1 through 3.1.7.
        "start_pattern": r"^3\.1\s+[A-Z]",
        "end_pattern": r"^3\.2\s+[A-Z]",
        "max_chars": 12000,
        "prompt_context": "payment fraud threat report",
    },
    "regulatory": {
        # UNVERIFIED — no EBA/ECB 2025 PDF in this repo yet. Falls back to
        # skip/window until a real report exists to tune start_pattern/
        # end_pattern against. Do not add patterns here without testing
        # against the actual file.
        "skip_chars": 4000,
        "max_chars": 8000,
        "prompt_context": "EU regulatory joint report on payment fraud and cybersecurity",
    },
    "ranked_list": {
        # UNVERIFIED, same caveat as "regulatory" — OWASP API Top 10 isn't
        # a narrative document at all, so numbered-section extraction may
        # not even apply the same way once a real report is available.
        "skip_chars": 1000,
        "max_chars": 10000,
        "prompt_context": "API security vulnerability list",
    },
}


class DuplicateScanError(Exception):
    """Raised when a (report_name, profile) pair has already been scanned
    and --force wasn't passed."""


def _now_iso() -> str:
    """Matches the timestamp convention already used elsewhere in this
    codebase (InvestigationReport.created_at in fraud_investigator.py)."""
    return datetime.datetime.now().isoformat() + "Z"


def default_registry_path() -> str:
    """
    Resolve registry.json the same way server/routes/fraud.py does, so a
    scan run against this default validates categories/existing-names
    against the exact file the live server will later write proposals to
    (rather than always falling back to the repo-local copy regardless of
    what TOOL_REGISTRY_PATH points the running server at).
    """
    ai_agents_dir = os.path.dirname(os.path.abspath(__file__))
    default = os.environ.get(
        "TOOL_REGISTRY_PATH",
        os.path.join(ai_agents_dir, "tools", "registry.json"),
    )
    if not os.path.exists(default):
        default = os.path.join(ai_agents_dir, "registry.json")
    return default


def extract_report_text(pdf_path: str, profile: dict) -> str:
    """
    Extract the relevant window of report text per the given profile.

    If the profile specifies start_pattern, locate that section by regex
    and extract through to end_pattern (or end of document). The LAST match
    of start_pattern is used, not the first — a table of contents lists the
    same numbered heading before the real body section does, so the first
    match is always the TOC entry, not the content. The FIRST match of
    end_pattern occurring after start_pattern is used, since that's the
    nearest real section boundary (any earlier occurrence of end_pattern
    is itself a TOC entry, already excluded by the after-start filter).

    Falls back to a blind skip_chars offset when no start_pattern is given
    (today's original behavior, used by profiles with no verified section
    pattern yet). Either way, the result is capped at max_chars.
    """
    reader = PdfReader(pdf_path)
    pages = [p.extract_text() or "" for p in reader.pages]
    full_text = "\n".join(pages)

    start_pattern = profile.get("start_pattern")
    end_pattern = profile.get("end_pattern")
    max_chars = profile.get("max_chars", WINDOW_CHARS)

    if start_pattern:
        start_matches = list(re.finditer(start_pattern, full_text, re.MULTILINE))
        if not start_matches:
            raise ValueError(f"start_pattern {start_pattern!r} not found in {pdf_path}")
        start = start_matches[-1].start()

        end = len(full_text)
        if end_pattern:
            end_matches = [m.start() for m in re.finditer(end_pattern, full_text, re.MULTILINE) if m.start() > start]
            if end_matches:
                end = end_matches[0]

        window = full_text[start:end]
    else:
        skip = profile.get("skip_chars", SKIP_CHARS)
        window = full_text[skip:]

    return window[:max_chars]


def strip_json_repair(text: str, open_char: str, close_char: str):
    """
    Port of the JSON extraction/repair routine from
    client/src/pages/AddressForm.jsx (lines 194-258): strip markdown fences,
    locate the outermost JSON container, try a direct parse, and if that
    fails, repair truncated output by cutting at the last complete element
    and closing any unbalanced brackets/braces.
    """
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text, flags=re.IGNORECASE)

    # The model is told not to add preamble, but small local models sometimes
    # do anyway (e.g. "Here are the patterns [from the report]:"), and a
    # stray open_char in that preamble would make a naive "first occurrence"
    # search anchor on the wrong spot. Try every occurrence in order and use
    # the first one that parses directly as valid JSON.
    starts = [i for i, ch in enumerate(text) if ch == open_char]
    if not starts:
        raise ValueError(f"No JSON {open_char!r} found in response")

    for start in starts:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue

    # None parsed directly (likely truncated output) — repair from the LAST
    # occurrence, since genuine content is more likely to follow any
    # preamble than precede it.
    text = text[starts[-1]:]

    last_complete = text.rfind("},{")
    last_field_end = text.rfind('"}')
    cut_point = max(last_complete, last_field_end)

    if cut_point <= 0:
        raise ValueError("Cannot find a repair point in truncated JSON")

    repaired = text[:cut_point + (2 if text[cut_point] != "," else 1)]
    braces = brackets = 0
    for ch in repaired:
        if ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1

    repaired = re.sub(r",\s*$", "", repaired)
    repaired += "]" * max(brackets, 0)
    repaired += "}" * max(braces, 0)

    return json.loads(repaired)


def build_prompt(report_text: str, category_ids: list, prompt_context: str = "payment fraud threat report") -> str:
    categories_list = ", ".join(category_ids)
    return f"""You are analyzing a {prompt_context} to identify distinct fraud
patterns that an automated fraud investigation tool could detect.

REPORT EXCERPT:
{report_text}

Extract 5 to 8 DISTINCT fraud patterns described in this excerpt. For each one,
output an object with these exact fields:
- "name": a short snake_case identifier, e.g. "check_synthetic_identity_velocity"
- "category": exactly one of: {categories_list}
- "description": one sentence describing what the tool would check
- "detects": one sentence describing what fraud pattern this catches
- "reference_snippet": a short quote or paraphrase (under 200 chars) from the excerpt that justifies this tool

Respond with ONLY a JSON array of these objects, nothing else. No markdown fences, no preamble."""


def call_ollama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 900},
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "unnamed_tool"


def build_registry_entry(raw: dict, category_ids: set, source_label: str, source_path: str, existing_names: set) -> dict:
    if not isinstance(raw, dict):
        return None

    # `.get(key, default)` only applies the default when the key is
    # *missing* — a malformed LLM response can emit `"name": null`
    # explicitly, which .get() would happily return as None and crash
    # NAME_RE.match() below. `or` catches both missing and None/empty.
    name = raw.get("name") or ""
    if not NAME_RE.match(name):
        name = slugify(raw.get("name") or raw.get("description") or "unnamed_tool")

    base_name = name
    suffix = 2
    while name in existing_names:
        name = f"{base_name}_v{suffix}"
        suffix += 1

    category = raw.get("category") or ""
    if category not in category_ids:
        return None

    description = (raw.get("description") or "").strip()
    detects = (raw.get("detects") or "").strip()
    if not description or not detects:
        return None

    return {
        "name": name,
        "category": category,
        "status": "proposed",
        "source": "external",
        "source_detail": source_label,
        "description": description,
        "detects": detects,
        "input_schema": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string", "description": "Transaction ID"}
            },
            "required": ["txn_id"],
        },
        "references": [
            {
                "name": source_label,
                "url": f"file:///{os.path.abspath(source_path).replace(os.sep, '/')}",
                "relevance": (raw.get("reference_snippet") or "").strip()[:200],
            }
        ],
    }


def run_scan(report_path: str, registry: ToolRegistry, profile_name: str = "narrative", force: bool = False) -> dict:
    """
    Orchestrates one scan: duplicate check, extraction, Ollama call,
    proposal validation, scan_history write.

    Raises DuplicateScanError/ValueError on hard failures instead of
    sys.exit()-ing, so this is directly callable from tests (with a real
    ToolRegistry and a mocked call_ollama) and from main(), which is the
    only thing that translates failures into exit codes.

    Note: this does NOT persist proposals into registry.json's tools[] —
    that only ever happened via the separate POST /api/fraud/tools/propose
    route, which this function doesn't touch. Recording scan_history is
    independent of whether the proposals it produced are later approved.

    Returns {"proposals": [...], "scan_history_entry": {...}, "report_text_len": int}.
    """
    if profile_name not in REPORT_PROFILES:
        raise ValueError(f"Unknown profile: {profile_name!r}. Choices: {sorted(REPORT_PROFILES)}")
    profile = REPORT_PROFILES[profile_name]

    category_ids = {c["id"] for c in registry.list_categories()}
    existing_names = {t["name"] for t in registry.list_tools()}
    source_label = os.path.splitext(os.path.basename(report_path))[0]

    # Duplicate check keyed on (report_name, profile), not report_name alone
    # — a report can have multiple scannable sections, each its own profile
    # (e.g. a future profile targeting a different section of the same PDF).
    # Checked before extraction/Ollama so a blocked duplicate costs nothing.
    existing_scan = next(
        (s for s in registry.get_scan_history()
         if s["report_name"] == source_label and s["profile"] == profile_name),
        None,
    )
    if existing_scan and not force:
        raise DuplicateScanError(
            f"'{source_label}' was already scanned with profile={profile_name!r} "
            f"at {existing_scan['scanned_at']} ({existing_scan['proposals_generated']} proposals). "
            f"Pass --force to re-scan."
        )

    print(f"[SCAN] Extracting text from {report_path} (profile={profile_name})")
    report_text = extract_report_text(report_path, profile)
    print(f"[SCAN] Using {len(report_text)} chars")

    prompt = build_prompt(report_text, sorted(category_ids), profile.get("prompt_context", "payment fraud threat report"))
    print(f"[SCAN] Calling Ollama ({OLLAMA_MODEL}) for pattern extraction...")
    raw_response = call_ollama(prompt)
    print(f"[SCAN] Raw response ({len(raw_response)} chars):")
    print(raw_response[:600])

    try:
        candidates = strip_json_repair(raw_response, "[", "]")
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to parse JSON from Ollama output: {e}") from e

    if not isinstance(candidates, list):
        raise ValueError("expected a JSON array of pattern objects")

    print(f"[SCAN] Extracted {len(candidates)} raw candidates, validating...")

    proposals = []
    for raw in candidates:
        try:
            entry = build_registry_entry(raw, category_ids, source_label, report_path, existing_names | {p["name"] for p in proposals})
        except Exception as e:
            # One malformed candidate (unexpected type/shape from the LLM)
            # should never sink the whole batch — drop it and keep going.
            print(f"  [DROP] Candidate raised {type(e).__name__}: {e} — {json.dumps(raw, default=str)[:150]}")
            continue
        if entry is None:
            print(f"  [DROP] Invalid candidate: {json.dumps(raw, default=str)[:150]}")
            continue
        print(f"  [OK] {entry['name']} ({entry['category']}) — {entry['description'][:80]}")
        proposals.append(entry)

    print(f"\n[SCAN] {len(proposals)} valid proposals produced")

    scan_entry = {
        "report_name": source_label,
        "report_path": report_path,
        "profile": profile_name,
        "scanned_at": _now_iso(),
        "proposals_generated": len(proposals),
    }
    result = registry.add_scan_history(scan_entry)
    if "error" in result:
        raise RuntimeError(result["error"])

    return {"proposals": proposals, "scan_history_entry": result, "report_text_len": len(report_text)}


def main():
    parser = argparse.ArgumentParser(description="Scan a fraud threat report and propose registry tools")
    parser.add_argument("--report", required=True, help="Path to the source PDF report")
    parser.add_argument("--out", help="Optional path to write the proposals JSON")
    parser.add_argument(
        "--registry",
        default=default_registry_path(),
        help="Path to registry.json (for category validation). Defaults to "
             "TOOL_REGISTRY_PATH env var if set, matching the live server's "
             "resolution — pass explicitly if scanning against a different registry.",
    )
    parser.add_argument(
        "--profile", default="narrative", choices=sorted(REPORT_PROFILES),
        help="Extraction profile matching the report's structure. Default: narrative.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-scan a report even if this (report, profile) pair was already scanned.",
    )
    args = parser.parse_args()

    registry = ToolRegistry(args.registry)

    try:
        result = run_scan(args.report, registry, profile_name=args.profile, force=args.force)
    except DuplicateScanError as e:
        print(f"[SCAN] BLOCKED: {e}")
        sys.exit(1)
    except (ValueError, RuntimeError) as e:
        print(f"[SCAN] FAILED: {e}")
        sys.exit(1)

    output = json.dumps(result["proposals"], indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[SCAN] Written to {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
