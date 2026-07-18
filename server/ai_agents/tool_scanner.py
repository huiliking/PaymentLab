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

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import requests
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_agents.tool_registry import ToolRegistry

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# How much of the report (after skipping the TOC) to send to the model in
# one prompt. See module docstring LIMITATION note.
SKIP_CHARS = 8000
WINDOW_CHARS = 6000


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


def extract_report_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    pages = [p.extract_text() or "" for p in reader.pages]
    full_text = "\n".join(pages)
    return full_text[SKIP_CHARS:SKIP_CHARS + WINDOW_CHARS]


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


def build_prompt(report_text: str, category_ids: list) -> str:
    categories_list = ", ".join(category_ids)
    return f"""You are analyzing a payment fraud threat report to identify distinct fraud
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
    args = parser.parse_args()

    registry = ToolRegistry(args.registry)
    category_ids = {c["id"] for c in registry.list_categories()}
    existing_names = {t["name"] for t in registry.list_tools()}

    source_label = os.path.splitext(os.path.basename(args.report))[0]

    print(f"[SCAN] Extracting text from {args.report}")
    report_text = extract_report_text(args.report)
    print(f"[SCAN] Using {len(report_text)} chars (window after skipping first {SKIP_CHARS} chars of TOC/front matter)")

    prompt = build_prompt(report_text, sorted(category_ids))
    print(f"[SCAN] Calling Ollama ({OLLAMA_MODEL}) for pattern extraction...")
    raw_response = call_ollama(prompt)
    print(f"[SCAN] Raw response ({len(raw_response)} chars):")
    print(raw_response[:600])

    try:
        candidates = strip_json_repair(raw_response, "[", "]")
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[SCAN] FAILED to parse JSON from Ollama output: {e}")
        sys.exit(1)

    if not isinstance(candidates, list):
        print("[SCAN] FAILED: expected a JSON array of pattern objects")
        sys.exit(1)

    print(f"[SCAN] Extracted {len(candidates)} raw candidates, validating...")

    proposals = []
    for raw in candidates:
        try:
            entry = build_registry_entry(raw, category_ids, source_label, args.report, existing_names | {p["name"] for p in proposals})
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

    output = json.dumps(proposals, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[SCAN] Written to {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
