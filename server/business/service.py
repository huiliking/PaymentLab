"""
business/service.py
====================
Business layer: merchant -> tier -> granted tools resolution.

Owns commercial *policy* (which tools a tier grants). The tool registry
(ai_agents/tool_registry.py) owns *truth* about which tools exist. This
module is the only place that knows both tiers and tools — the registry
never learns tiers exist, and this module never imports it directly. A
ToolRegistry-like object (get_tool_by_id) is passed into the constructor
instead, so the dependency stays one-way: business -> function.

Scope of that rule: it applies to *this module* (the business layer
itself), not to every file that happens to sit in business/. The
concurrency script alongside it, _stress_test_business_layer.py,
legitimately imports ToolRegistry — it's test scaffolding standing in for
the production caller (routes/fraud.py), which is what constructs the
registry and injects it here. So the boundary check is
"does service.py import it", not "does anything under business/ mention
it"; a directory-wide grep gives false positives (including on this very
docstring). The automated version of the check lives in
tests/test_sprint5_business_layer.py::test_service_module_does_not_import_tool_registry
and inspects this file's real import statements via `ast`.
"""

import sqlite3
from typing import Any, Dict, List, Optional


class BusinessError(Exception):
    """Raised for invalid merchant/tier/tool-grant operations."""


class BusinessLayer:
    def __init__(self, db_path: str, tool_registry: Any):
        """
        Args:
            db_path: Path to the SQLite DB holding merchants/tiers/tier_tools.
            tool_registry: Any object exposing get_tool_by_id(tool_id) -> dict|None.
        """
        self.db_path = db_path
        self.registry = tool_registry

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Merchant lookups ────────────────────────────────────────────────

    def get_merchant(self, merchant_id: int) -> Optional[Dict]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_merchant_by_registration_id(self, registration_id: str) -> Optional[Dict]:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM merchants WHERE registration_id = ?", (registration_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def list_merchants(self) -> List[Dict]:
        """
        Merchant listing for admin endpoints. Deliberately omits the
        internal integer `id` — registration_id is the only externally
        safe handle (ids are sequential/guessable).
        """
        conn = self._conn()
        rows = conn.execute("""
            SELECT m.registration_id, m.tier_id, t.name AS tier_name
            FROM merchants m JOIN tiers t ON m.tier_id = t.id
            ORDER BY m.registration_id
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Core resolution ─────────────────────────────────────────────────

    def resolve_allowed_tools(self, merchant_id: int) -> List[str]:
        """
        merchant -> tier -> tier_tools -> tool_ids -> tool names.

        Raises BusinessError if the merchant is unknown, or if any granted
        tool_id no longer resolves to a real, active tool (drift since
        the grant was made) — a grant is never silently dropped.
        """
        merchant = self.get_merchant(merchant_id)
        if not merchant:
            raise BusinessError(f"Unknown merchant_id: {merchant_id}")

        conn = self._conn()
        rows = conn.execute(
            "SELECT tool_id FROM tier_tools WHERE tier_id = ?", (merchant["tier_id"],)
        ).fetchall()
        conn.close()

        names = []
        for row in rows:
            tool = self.registry.get_tool_by_id(row["tool_id"])
            if not tool or tool.get("status") != "active":
                state = "inactive" if tool else "missing"
                raise BusinessError(
                    f"Tier {merchant['tier_id']} grants tool_id {row['tool_id']} which is {state} "
                    f"in the registry — fix the grant before this merchant can be investigated"
                )
            names.append(tool["name"])
        return names

    # ── Tier management ─────────────────────────────────────────────────

    def list_tiers(self) -> List[Dict]:
        conn = self._conn()
        tiers = [dict(r) for r in conn.execute("SELECT * FROM tiers ORDER BY id").fetchall()]
        conn.close()
        for tier in tiers:
            tier["tools"] = self.get_tier_tools(tier["id"])
        return tiers

    def create_tier(self, name: str) -> Dict:
        conn = self._conn()
        try:
            cur = conn.execute("INSERT INTO tiers (name) VALUES (?)", (name,))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            raise BusinessError(f"Tier '{name}' already exists")
        tier_id = cur.lastrowid
        conn.close()
        return {"id": tier_id, "name": name, "tools": []}

    def get_tier_tools(self, tier_id: int) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute("SELECT tool_id FROM tier_tools WHERE tier_id = ?", (tier_id,)).fetchall()
        conn.close()
        out = []
        for row in rows:
            tool = self.registry.get_tool_by_id(row["tool_id"])
            out.append({
                "tool_id": row["tool_id"],
                "name": tool["name"] if tool else None,
                "status": tool["status"] if tool else "missing",
            })
        return out

    def grant_tool(self, tier_id: int, tool_id: int) -> Dict:
        """
        Add a tool grant to a tier. Validated against the live catalog at
        this assignment step (fast admin feedback) — resolve_allowed_tools
        re-validates at investigation time to catch drift.
        """
        if not self._tier_exists(tier_id):
            raise BusinessError(f"Unknown tier_id: {tier_id}")
        tool = self.registry.get_tool_by_id(tool_id)
        if not tool:
            raise BusinessError(f"Unknown tool_id: {tool_id}")
        if tool.get("status") != "active":
            raise BusinessError(
                f"Tool '{tool['name']}' (tool_id={tool_id}) is not active "
                f"(status: {tool['status']}) — only active tools can be granted"
            )
        conn = self._conn()
        conn.execute(
            "INSERT OR IGNORE INTO tier_tools (tier_id, tool_id) VALUES (?, ?)", (tier_id, tool_id)
        )
        conn.commit()
        conn.close()
        return {"tier_id": tier_id, "tool_id": tool_id, "name": tool["name"]}

    def revoke_tool(self, tier_id: int, tool_id: int) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM tier_tools WHERE tier_id = ? AND tool_id = ?", (tier_id, tool_id))
        conn.commit()
        conn.close()

    def assign_tier(self, merchant_id: int, tier_id: int) -> Dict:
        if not self.get_merchant(merchant_id):
            raise BusinessError(f"Unknown merchant_id: {merchant_id}")
        if not self._tier_exists(tier_id):
            raise BusinessError(f"Unknown tier_id: {tier_id}")
        conn = self._conn()
        conn.execute("UPDATE merchants SET tier_id = ? WHERE id = ?", (tier_id, merchant_id))
        conn.commit()
        conn.close()
        return self.get_merchant(merchant_id)

    def _tier_exists(self, tier_id: int) -> bool:
        conn = self._conn()
        row = conn.execute("SELECT 1 FROM tiers WHERE id = ?", (tier_id,)).fetchone()
        conn.close()
        return row is not None
