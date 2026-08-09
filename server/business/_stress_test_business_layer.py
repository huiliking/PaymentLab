"""
_stress_test_business_layer.py — concurrency stress test for Sprint 5's
BusinessLayer (server/business/service.py).

Direct precedent: server/ai_agents/_test_concurrent_lock.py (Sprint 4),
which proved the tool registry's file-based cross-process lock under real
concurrent OS processes rather than back-to-back curl calls. Sprint 5 never
exercised concurrency at all: BusinessLayer._conn() does a plain
sqlite3.connect() per call (Python's stdlib default gives it a 5-second
busy-timeout, but no WAL mode, no explicit busy_timeout tuning) against a
DB that has no cross-process lock of its own — unlike registry.json, which
Sprint 4 built _CrossProcessLock specifically to protect. This script is
the first real check of whether that gap matters in practice.

**Expect a non-zero "database is locked" count to be a real finding, not a
test bug.** If it happens, don't treat it as flaky — it's the SQLite
default-timeout window actually being exceeded under load, and the
candidate fix (a longer/explicit busy_timeout, and/or PRAGMA
journal_mode=WAL, on BusinessLayer._conn()) should be written up with the
failure evidence in SPRINT5_TEST_REPORT.md.

Always operates on a throwaway copy of payment_lab.db (and registry.json)
in a temp dir — NEVER the real dev DB. Uses real OS processes
(multiprocessing, not threading — matches _test_concurrent_lock.py's
reasoning: this is meant to approximate gunicorn's multi-worker-process
model), synchronized with multiprocessing.Barrier so calls genuinely land
at the same instant rather than being back-to-back.

Run from server/:
    python business/_stress_test_business_layer.py [--n N]

Exits 0 with [PASS] lines if every scenario's final state is valid (not
necessarily every attempted write "winning" — see each scenario's own pass
criteria). Exits 1 with [FAIL] lines on corruption or an unexpected
exception. Always prints the total lock-contention count, and exits 0 even
if that count is non-zero (a locked error that's cleanly surfaced as an
exception, not silent corruption, is a finding to report — not a crash).
"""
import argparse
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _SERVER_DIR)

REAL_DB = os.path.join(_SERVER_DIR, "payment_lab.db")
REAL_REGISTRY = os.path.join(_SERVER_DIR, "ai_agents", "registry.json")

DEFAULT_N = 12


# ── Worker bodies (run in child processes — re-import everything, matching
#    _test_concurrent_lock.py's pattern for pickling/spawn safety) ─────────

def _worker_grant(db_path, registry_path, tier_id, tool_id, barrier, result_queue):
    from business.service import BusinessLayer, BusinessError
    from ai_agents.tool_registry import ToolRegistry
    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    barrier.wait()
    try:
        result = business.grant_tool(tier_id, tool_id)
        result_queue.put(("ok", tool_id, result))
    except BusinessError as e:
        result_queue.put(("business_error", tool_id, str(e)))
    except sqlite3.OperationalError as e:
        result_queue.put(("locked" if "locked" in str(e).lower() else "sqlite_error", tool_id, str(e)))
    except Exception as e:
        result_queue.put(("error", tool_id, f"{type(e).__name__}: {e}"))


def _worker_read(db_path, registry_path, merchant_id, iterations, barrier, result_queue):
    from business.service import BusinessLayer, BusinessError
    from ai_agents.tool_registry import ToolRegistry
    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    barrier.wait()
    ok, locked, other_errors = 0, 0, 0
    for _ in range(iterations):
        try:
            names = business.resolve_allowed_tools(merchant_id)
            if not isinstance(names, list):
                result_queue.put(("corrupt", merchant_id, f"non-list result: {names!r}"))
                return
            ok += 1
        except BusinessError:
            # a stale grant mid-toggle is an expected, clean outcome here —
            # the writer intentionally revokes/re-grants during this scenario
            ok += 1
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                locked += 1
            else:
                other_errors += 1
        except Exception as e:
            other_errors += 1
    result_queue.put(("read_summary", merchant_id, {"ok": ok, "locked": locked, "other_errors": other_errors}))


def _worker_write_toggle(db_path, registry_path, tier_id, tool_id, iterations, barrier, result_queue):
    from business.service import BusinessLayer
    from ai_agents.tool_registry import ToolRegistry
    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    barrier.wait()
    locked = 0
    for _ in range(iterations):
        try:
            business.grant_tool(tier_id, tool_id)
            business.revoke_tool(tier_id, tool_id)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                locked += 1
    result_queue.put(("write_summary", tool_id, {"locked": locked}))


def _worker_assign(db_path, registry_path, merchant_id, tier_id, barrier, result_queue):
    from business.service import BusinessLayer
    from ai_agents.tool_registry import ToolRegistry
    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    barrier.wait()
    try:
        business.assign_tier(merchant_id, tier_id)
        result_queue.put(("ok", tier_id, None))
    except sqlite3.OperationalError as e:
        result_queue.put(("locked" if "locked" in str(e).lower() else "sqlite_error", tier_id, str(e)))
    except Exception as e:
        result_queue.put(("error", tier_id, f"{type(e).__name__}: {e}"))


# ── Scenario runners (main process) ─────────────────────────────────────────

def scenario_concurrent_grants(db_path, registry_path, n):
    print(f"\n[SCENARIO] Concurrent grants — {n} processes granting distinct tool_ids at once")
    from ai_agents.tool_registry import ToolRegistry
    from business.service import BusinessLayer

    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    tier = business.create_tier("zz_stress_grants")
    tier_id = tier["id"]

    active_ids = [t["tool_id"] for t in registry.get_active_tools()]
    tool_ids = (active_ids * ((n // len(active_ids)) + 1))[:n]  # wrap around if n > active count

    barrier = multiprocessing.Barrier(n)
    queue = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(target=_worker_grant, args=(db_path, registry_path, tier_id, tid, barrier, queue))
        for tid in tool_ids
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    results = [queue.get(timeout=5) for _ in procs]
    locked_count = sum(1 for status, *_ in results if status == "locked")
    errors = [r for r in results if r[0] not in ("ok", "locked")]

    granted = business.get_tier_tools(tier_id)
    granted_ids = {g["tool_id"] for g in granted}
    expected_ids = set(tool_ids)  # duplicates collapse to the same set either way — composite PK

    ok = errors == [] and granted_ids == expected_ids
    print(f"  attempted tool_ids: {sorted(set(tool_ids))}")
    print(f"  persisted tool_ids: {sorted(granted_ids)}")
    print(f"  lock-contention errors: {locked_count}")
    if errors:
        print(f"  [FAIL] unexpected errors: {errors}")
    print("  [PASS] all attempted grants persisted, no corruption" if ok else "  [FAIL] scenario failed")
    return ok, locked_count


def scenario_concurrent_reads_during_write(db_path, registry_path, n):
    print(f"\n[SCENARIO] Concurrent reads during a write — {n-1} readers vs 1 writer toggling a grant")
    from ai_agents.tool_registry import ToolRegistry
    from business.service import BusinessLayer

    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    merchant = business.get_merchant_by_registration_id("merchant_alpha")
    merchant_id = merchant["id"]
    default_tier_id = merchant["tier_id"]
    active_ids = [t["tool_id"] for t in registry.get_active_tools()]
    toggle_tool_id = active_ids[0]
    # start from a known state: not granted (readers will see clean toggling either way)
    business.revoke_tool(default_tier_id, toggle_tool_id)

    n_readers = max(1, n - 1)
    iterations = 50
    barrier = multiprocessing.Barrier(n_readers + 1)
    queue = multiprocessing.Queue()

    reader_procs = [
        multiprocessing.Process(
            target=_worker_read, args=(db_path, registry_path, merchant_id, iterations, barrier, queue)
        )
        for _ in range(n_readers)
    ]
    writer_proc = multiprocessing.Process(
        target=_worker_write_toggle,
        args=(db_path, registry_path, default_tier_id, toggle_tool_id, iterations, barrier, queue),
    )
    procs = reader_procs + [writer_proc]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    results = [queue.get(timeout=5) for _ in procs]
    corrupt = [r for r in results if r[0] == "corrupt"]
    locked_count = sum(
        r[2]["locked"] for r in results if r[0] in ("read_summary", "write_summary")
    )
    other_errors = sum(r[2].get("other_errors", 0) for r in results if r[0] == "read_summary")

    # restore the tool grant so this scenario leaves default tier as it found it
    business.grant_tool(default_tier_id, toggle_tool_id)

    ok = not corrupt and other_errors == 0
    print(f"  reader/writer iterations: {iterations} each")
    print(f"  lock-contention errors: {locked_count}")
    print(f"  other read errors: {other_errors}")
    if corrupt:
        print(f"  [FAIL] corrupt read result(s): {corrupt}")
    print("  [PASS] no corrupt reads, no unexpected errors" if ok else "  [FAIL] scenario failed")
    return ok, locked_count


def scenario_concurrent_tier_reassignment(db_path, registry_path, n):
    print(f"\n[SCENARIO] Concurrent tier reassignment — {n} processes reassigning the same merchant at once")
    from ai_agents.tool_registry import ToolRegistry
    from business.service import BusinessLayer

    registry = ToolRegistry(registry_path)
    business = BusinessLayer(db_path, registry)
    merchant = business.get_merchant_by_registration_id("merchant_beta")
    merchant_id = merchant["id"]
    original_tier_id = merchant["tier_id"]

    tiers = [business.create_tier(f"zz_stress_reassign_{i}")["id"] for i in range(n)]

    barrier = multiprocessing.Barrier(n)
    queue = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(target=_worker_assign, args=(db_path, registry_path, merchant_id, tid, barrier, queue))
        for tid in tiers
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    results = [queue.get(timeout=5) for _ in procs]
    locked_count = sum(1 for status, *_ in results if status == "locked")
    errors = [r for r in results if r[0] not in ("ok", "locked")]

    final = business.get_merchant(merchant_id)
    final_tier_id = final["tier_id"]

    # restore original assignment
    business.assign_tier(merchant_id, original_tier_id)

    ok = errors == [] and final_tier_id in tiers
    print(f"  attempted tier_ids: {tiers}")
    print(f"  final tier_id: {final_tier_id}")
    print(f"  lock-contention errors: {locked_count}")
    if errors:
        print(f"  [FAIL] unexpected errors: {errors}")
    print(
        "  [PASS] final state is exactly one of the attempted assignments, no corruption"
        if ok else "  [FAIL] scenario failed"
    )
    return ok, locked_count


def main():
    parser = argparse.ArgumentParser(description="Sprint 5 BusinessLayer concurrency stress test")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="concurrent processes per scenario")
    args = parser.parse_args()

    if not (os.path.exists(REAL_DB) and os.path.exists(REAL_REGISTRY)):
        print(f"[FAIL] missing {REAL_DB} or {REAL_REGISTRY} to copy from")
        sys.exit(1)

    tmp_dir = tempfile.mkdtemp(prefix="sprint5_stress_")
    tmp_db = os.path.join(tmp_dir, "payment_lab.db")
    tmp_registry = os.path.join(tmp_dir, "registry.json")

    print(f"[SETUP] working copy: {tmp_dir}")
    all_ok = True
    total_locked = 0

    try:
        for scenario in (
            scenario_concurrent_grants,
            scenario_concurrent_reads_during_write,
            scenario_concurrent_tier_reassignment,
        ):
            shutil.copy(REAL_DB, tmp_db)
            shutil.copy(REAL_REGISTRY, tmp_registry)
            ok, locked = scenario(tmp_db, tmp_registry, args.n)
            all_ok = all_ok and ok
            total_locked += locked
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"\n[CLEANUP] removed {tmp_dir}")

    print(f"\n{'='*60}")
    print(f"TOTAL lock-contention ('database is locked') errors: {total_locked}")
    if total_locked:
        print(
            "[FINDING] BusinessLayer._conn() has no WAL mode / explicit busy_timeout — "
            "this is real contention under concurrent load, not a fluke. Candidate fix: "
            "PRAGMA journal_mode=WAL and/or a longer sqlite3.connect(..., timeout=N) in "
            "server/business/service.py:_conn(). See SPRINT5_TEST_REPORT.md."
        )
    print(f"OVERALL: {'[PASS]' if all_ok else '[FAIL]'}")
    print(f"{'='*60}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
