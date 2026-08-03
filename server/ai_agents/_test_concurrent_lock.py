"""
_test_concurrent_lock.py — throwaway verification script for Sprint 4's
cross-process registry lock (tool_registry.py:_CrossProcessLock).

Spawns two separate OS processes (not threads — multiprocessing's default
start method creates real child processes), each with its own independently
loaded ToolRegistry instance, and has them call propose_tool() for distinct
dummy tools at the same instant (synchronized via a multiprocessing.Barrier).
This reproduces the exact race KNOWN_ISSUES.md #1 described: two workers,
each with their own in-memory copy of registry.json, writing concurrently.

Run with the Flask server STOPPED — this script edits registry.json
directly, bypassing Flask entirely. See SPRINT4_TEST_PLAN.md Phase 3.

Exits 0 and prints [PASS] lines if both writes survived and the file is
still valid JSON; exits 1 with [FAIL] lines otherwise. Cleans up its own
dummy tools from registry.json on the way out either way.
"""
import json
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registry.json")
DUMMY_TOOLS = ["zz_concurrent_test_a", "zz_concurrent_test_b"]


def _propose_dummy(name, barrier):
    from tool_registry import ToolRegistry
    registry = ToolRegistry(REGISTRY_PATH)
    barrier.wait()  # release both processes into propose_tool() at ~the same instant
    result = registry.propose_tool({
        "name": name,
        "category": "transaction_context",
        "status": "proposed",
        "source": "external",
        "description": "concurrency test",
        "detects": "concurrency test",
        "input_schema": {"type": "object", "properties": {}},
        "references": [],
    })
    print(f"[{name}] propose_tool result: {result}")


def _cleanup():
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    before = len(data["tools"])
    data["tools"] = [t for t in data["tools"] if t["name"] not in DUMMY_TOOLS]
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    removed = before - len(data["tools"])
    if removed:
        print(f"[cleanup] removed {removed} dummy tool(s) from registry.json")


def main():
    barrier = multiprocessing.Barrier(2)
    procs = [
        multiprocessing.Process(target=_propose_dummy, args=(name, barrier))
        for name in DUMMY_TOOLS
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    ok = True
    for p in procs:
        if p.exitcode != 0:
            print(f"[FAIL] subprocess {p.pid} exited with code {p.exitcode}")
            ok = False

    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[FAIL] registry.json is not valid JSON after concurrent writes: {e}")
        print("Manual inspection needed -- not attempting automatic cleanup of a corrupt file.")
        sys.exit(1)

    names = {t["name"] for t in data["tools"]}
    missing = [n for n in DUMMY_TOOLS if n not in names]

    if missing:
        print(f"[FAIL] lost update -- missing tool(s) after concurrent proposes: {missing}")
        ok = False
    else:
        print(f"[PASS] both dummy tools present after concurrent writes: {DUMMY_TOOLS}")

    lock_path = REGISTRY_PATH + ".lock"
    if os.path.exists(lock_path):
        print(f"[PASS] lock sidecar file created: {lock_path}")
    else:
        print(f"[WARN] lock sidecar file not found at {lock_path} (unexpected)")

    _cleanup()

    sys.exit(0 if ok and not missing else 1)


if __name__ == "__main__":
    main()
