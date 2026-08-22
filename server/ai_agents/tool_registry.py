"""
Tool Registry for Fraud Investigation Agent

Config-driven tool management. Loads tool definitions from registry.json,
provides Claude API schemas, dispatches execution, and serves metadata
for the dashboard.

Usage:
    from tool_registry import ToolRegistry

    registry = ToolRegistry("tools/registry.json")

    # Get Claude tool schemas (replaces hardcoded CLAUDE_TOOLS)
    schemas = registry.get_schemas()

    # Execute a tool call from Claude (replaces manual dispatch)
    result = registry.execute("get_card_history", {"card_last4": "4242", "hours": 24}, tools_instance)

    # List all tools for dashboard API
    all_tools = registry.list_tools()

MCP Compatibility:
    Each tool's schema (name + description + input_schema) maps directly
    to MCP's tool definition format. To expose as an MCP server, iterate
    registry.get_mcp_tools() and register each with your MCP framework.
"""

import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class _CrossProcessLock:
    """
    Exclusive file lock usable across OS processes — e.g. multiple gunicorn
    workers on Render (Linux), each with their own in-memory ToolRegistry.
    A threading.Lock only synchronizes within one process, so it can't
    prevent one worker's write from silently overwriting another's.

    Locks a sidecar `<registry>.lock` file rather than registry.json itself,
    since registry.json gets swapped out via os.replace() during _persist().

    Windows (dev) uses msvcrt.locking; POSIX (Render) uses fcntl.flock.
    """

    def __init__(self, path: str):
        self._path = path
        self._fh = None

    def __enter__(self):
        self._fh = open(self._path, "a+b")
        if sys.platform == "win32":
            self._fh.seek(0, os.SEEK_END)
            if self._fh.tell() == 0:
                # msvcrt.locking needs at least one byte to lock.
                self._fh.write(b"\0")
                self._fh.flush()
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if sys.platform == "win32":
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
        return False


class ToolRegistry:
    """
    Config-driven tool registry.

    Loads tool definitions from a JSON file and provides:
    - get_schemas()      → CLAUDE_TOOLS list for the Anthropic API
    - get_mcp_tools()    → MCP-compatible tool definitions
    - execute()          → dispatches tool calls to implementation functions
    - list_tools()       → full metadata for the dashboard
    - list_categories()  → category metadata for the dashboard
    - get_tool()         → single tool lookup by name
    - get_active_tools() → only tools with status="active"
    """

    def __init__(self, registry_path: str = "registry.json"):
        """
        Load registry from JSON file.

        Args:
            registry_path: Path to registry.json
        """
        self._registry_path = registry_path
        self._lock_path = registry_path + ".lock"
        self._data = {}
        self._tools_by_name = {}
        self._tools_by_id = {}
        self._tools_by_category = {}
        self._categories_by_id = {}
        self.load()

    def load(self):
        """Load or reload registry from disk."""
        if not os.path.exists(self._registry_path):
            raise FileNotFoundError(f"Registry not found: {self._registry_path}")

        with open(self._registry_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        # Index tools by name
        self._tools_by_name = {t["name"]: t for t in self._data["tools"]}

        # Index tools by id — the immutable PK business-layer grants reference
        # (name is a mutable label; renaming a tool must not break a grant).
        self._tools_by_id = {t["tool_id"]: t for t in self._data["tools"] if "tool_id" in t}

        # Index tools by category
        self._tools_by_category = {}
        for tool in self._data["tools"]:
            cat = tool["category"]
            if cat not in self._tools_by_category:
                self._tools_by_category[cat] = []
            self._tools_by_category[cat].append(tool)

        # Index categories by id
        self._categories_by_id = {c["id"]: c for c in self._data["categories"]}

    # ── Schema Generation ──────────────────────────────────────────────────

    def get_schemas(self, status: str = "active", allowed: Optional[List[str]] = None) -> List[Dict]:
        """
        Generate Claude API tool schemas.

        Returns a list in the exact format Claude's tool_use expects:
            [{"name": ..., "description": ..., "input_schema": {...}}, ...]

        Args:
            status: Filter by status. "active" = only deployed tools.
                    "all" = include candidates (for testing).
            allowed: Optional tool-name allowlist (business layer resolves
                     a merchant's tier grants to this). None = no
                     restriction, i.e. all tools matching `status`.
                     Every name is validated against the live catalog —
                     an unknown or inactive name raises rather than being
                     silently dropped, since a grant referencing a
                     tool that's disappeared or been deactivated since
                     assignment is a business-facing bug, not a filter.

        Returns:
            List of tool schema dicts ready for the Anthropic API.
        """
        if allowed is not None:
            allowed = list(allowed)
            unknown = [n for n in allowed if n not in self._tools_by_name]
            if unknown:
                raise ValueError(f"allowed list references unknown tool(s): {sorted(unknown)}")
            inactive = [n for n in allowed if self._tools_by_name[n]["status"] != "active"]
            if inactive:
                raise ValueError(f"allowed list references inactive tool(s): {sorted(inactive)}")

        schemas = []
        for tool in self._data["tools"]:
            if status != "all" and tool["status"] != status:
                continue
            if allowed is not None and tool["name"] not in allowed:
                continue
            schemas.append({
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
            })
        return schemas

    def get_mcp_tools(self, status: str = "active") -> List[Dict]:
        """
        Generate MCP-compatible tool definitions.

        MCP tool format is nearly identical to Claude's:
            {"name": ..., "description": ..., "inputSchema": {...}}

        Note the camelCase "inputSchema" vs Claude's "input_schema".

        Args:
            status: Filter by status.

        Returns:
            List of MCP tool definition dicts.
        """
        tools = []
        for tool in self._data["tools"]:
            if status != "all" and tool["status"] != status:
                continue
            tools.append({
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": tool["input_schema"],
            })
        return tools

    # ── Tool Execution ─────────────────────────────────────────────────────

    def execute(self, tool_name: str, params: Dict[str, Any], tools_instance: Any) -> Dict:
        """
        Execute a tool call by dispatching to the implementation.

        Looks up the method on tools_instance by name and calls it with params.
        This replaces the old InvestigationTools.execute_tool() method.

        Args:
            tool_name:       Name of the tool (e.g., "get_card_history")
            params:          Dict of parameters from Claude's tool_use block
            tools_instance:  Instance of InvestigationTools (has the actual methods)

        Returns:
            Dict with tool results, or {"error": ...} on failure.
        """
        # Validate tool exists in registry
        if tool_name not in self._tools_by_name:
            return {"error": f"Unknown tool: {tool_name}"}

        tool_def = self._tools_by_name[tool_name]

        # Only allow execution of active tools
        if tool_def["status"] != "active":
            return {
                "error": f"Tool '{tool_name}' is not active (status: {tool_def['status']}). "
                         f"Only active tools can be executed."
            }

        # Dispatch to the implementation method
        method = getattr(tools_instance, tool_name, None)
        if not method:
            return {
                "error": f"Tool '{tool_name}' is registered but has no implementation. "
                         f"Add {tool_name}() to InvestigationTools."
            }

        try:
            return method(**params)
        except TypeError as e:
            return {"error": f"Parameter error for {tool_name}: {str(e)}"}
        except Exception as e:
            return {"error": f"Tool execution error ({tool_name}): {str(e)}"}

    # ── Dashboard / API Queries ────────────────────────────────────────────

    def list_tools(self, category: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
        """
        List tools with full metadata for the dashboard.

        Args:
            category: Filter by category ID (e.g., "card_velocity")
            status:   Filter by status ("active", "candidate", "proposed")

        Returns:
            List of tool dicts with all metadata.
        """
        tools = self._data["tools"]

        if category:
            tools = [t for t in tools if t["category"] == category]
        if status:
            tools = [t for t in tools if t["status"] == status]

        return tools

    def list_categories(self) -> List[Dict]:
        """
        List all categories with their metadata.

        Returns:
            List of category dicts with id, name, icon, description, question.
        """
        return self._data["categories"]

    def get_tool(self, name: str) -> Optional[Dict]:
        """Get a single tool by name, or None if not found."""
        return self._tools_by_name.get(name)

    def get_tool_by_id(self, tool_id: int) -> Optional[Dict]:
        """
        Get a single tool by its immutable tool_id, or None if not found.
        This is the lookup the business layer uses to resolve tier_tools
        grants — tool_id survives a name change, unlike get_tool(name).
        """
        return self._tools_by_id.get(tool_id)

    def get_active_tools(self) -> List[Dict]:
        """Shortcut for list_tools(status='active')."""
        return self.list_tools(status="active")

    def get_category_tools(self, category_id: str) -> List[Dict]:
        """Get all tools in a category."""
        return self._tools_by_category.get(category_id, [])

    # ── Statistics ─────────────────────────────────────────────────────────

    def get_statistics(self) -> Dict:
        """
        Get registry statistics (matches the 'statistics' block in registry.json,
        but computed live from the actual data).
        """
        tools = self._data["tools"]
        active = [t for t in tools if t["status"] == "active"]
        candidate = [t for t in tools if t["status"] == "candidate"]
        proposed = [t for t in tools if t["status"] == "proposed"]
        rejected = [t for t in tools if t["status"] == "rejected"]
        builtin = [t for t in tools if t["source"] == "builtin"]
        external = [t for t in tools if t["source"] == "external"]

        by_category = {}
        for cat in self._data["categories"]:
            cat_tools = self._tools_by_category.get(cat["id"], [])
            by_category[cat["id"]] = {
                "total": len(cat_tools),
                "active": len([t for t in cat_tools if t["status"] == "active"]),
                "candidate": len([t for t in cat_tools if t["status"] == "candidate"]),
                "proposed": len([t for t in cat_tools if t["status"] == "proposed"]),
                "rejected": len([t for t in cat_tools if t["status"] == "rejected"]),
            }

        return {
            "total_tools": len(tools),
            "active": len(active),
            "candidate": len(candidate),
            "proposed": len(proposed),
            "rejected": len(rejected),
            "by_source": {
                "builtin": len(builtin),
                "external": len(external),
            },
            "by_category": by_category,
        }

    # ── Registry Mutation (scanner proposals / dashboard approval) ─────────

    REQUIRED_TOOL_FIELDS = ("name", "category", "status", "source", "description", "detects", "input_schema", "references")

    # Only these status transitions are allowed via update_status(). Both
    # approve and reject only make sense starting from "proposed" — this
    # blocks a stray/replayed call from silently deactivating an already
    # "active" tool or re-flipping an already-decided one.
    VALID_STATUS_TRANSITIONS = {
        "proposed": {"candidate", "rejected"},
    }

    def _persist(self):
        """
        Write self._data back to registry.json and re-index.

        Writes to a temp file in the same directory and atomically renames
        it into place (os.replace), so a crash or disk-full error mid-write
        can't leave registry.json truncated/corrupt — the old file stays
        valid until the new one is fully written.
        """
        directory = os.path.dirname(self._registry_path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".registry_", suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self._registry_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        self.load()

    def propose_tool(self, tool_dict: Dict) -> Dict:
        """
        Add a new tool entry (must have status: "proposed") to the registry
        and persist to disk. Validates required fields, category ID, and
        that the entry is actually unreviewed (status == "proposed") —
        anything else would skip the human-approval step this endpoint
        exists to enforce.

        The whole read-check-write cycle runs under the cross-process lock
        and reloads from disk first, so a concurrent write from another
        gunicorn worker (which has its own in-memory copy of self._data)
        can't be silently lost — see _CrossProcessLock.

        Returns the stored tool dict, or {"error": ...} if invalid.
        """
        missing = [f for f in self.REQUIRED_TOOL_FIELDS if f not in tool_dict]
        if missing:
            return {"error": f"Missing required fields: {', '.join(missing)}"}

        if tool_dict["status"] != "proposed":
            return {"error": f"New tools must be proposed with status 'proposed', got '{tool_dict['status']}'"}

        with _CrossProcessLock(self._lock_path):
            self.load()

            if tool_dict["category"] not in self._categories_by_id:
                return {"error": f"Unknown category: {tool_dict['category']}"}

            if tool_dict["name"] in self._tools_by_name:
                return {"error": f"Tool '{tool_dict['name']}' already exists in registry"}

            # tool_id is registry-owned, not caller-supplied — assign (or
            # reassign, ignoring any value the caller passed in) so ids
            # stay unique and sequential regardless of proposal source.
            tool_dict["tool_id"] = self._next_tool_id()

            self._data["tools"].append(tool_dict)
            self._persist()
            return tool_dict

    def _next_tool_id(self) -> int:
        existing_ids = [t["tool_id"] for t in self._data["tools"] if "tool_id" in t]
        return max(existing_ids, default=0) + 1

    # ── Scan Provenance (tool_scanner.py) ───────────────────────────────────

    def add_scan_history(self, entry: Dict) -> Dict:
        """
        Append a scan_history entry and persist. Append-only — never
        overwrite/replace a prior entry for the same (report_name, profile);
        repeat scans over time (e.g. after prompt tuning) are legitimate
        history, not corrections to a previous run.

        Required keys: report_name, report_path, profile, scanned_at,
        proposals_generated. Uses the same lock/reload/persist pattern as
        propose_tool()/update_status() — the scanner runs as a separate CLI
        process, not a Flask request, so this needs the cross-process file
        lock, not the Flask-only in-memory _registry_lock in routes/fraud.py.

        Returns the stored entry, or {"error": ...} if invalid.
        """
        required = ("report_name", "report_path", "profile", "scanned_at", "proposals_generated")
        missing = [f for f in required if f not in entry]
        if missing:
            return {"error": f"Missing required scan_history fields: {', '.join(missing)}"}

        with _CrossProcessLock(self._lock_path):
            self.load()
            self._data.setdefault("scan_history", [])
            self._data["scan_history"].append(entry)
            self._persist()
            return entry

    def get_scan_history(self) -> List[Dict]:
        """
        Returns scan_history entries enriched with live-computed
        proposals_approved/rejected/pending, joined against self._data["tools"]
        by source_detail == entry["report_name"]. Never persisted — computed
        fresh on every call so it can't go stale relative to status changes
        made via update_status().

        Two known, disclosed limitations, not fixed here:
          - The join depends on the exact filename passed at the CLI
            (tool_scanner.py's source_label). Rescanning the same PDF from a
            different path/filename produces a new, zero-count report_name
            instead of joining to prior history.
          - The join is report-level, not (report, profile)-level: if a
            report is ever scanned under two different profiles, both
            resulting scan_history entries show identical aggregate counts,
            since no tool records which profile produced it. Fixing that
            fully would mean adding a source_profile field to the tool
            schema itself — out of scope while the real data never has more
            than one scan_history entry per report.
        """
        out = []
        for entry in self._data.get("scan_history", []):
            matching = [t for t in self._data["tools"] if t.get("source_detail") == entry["report_name"]]
            out.append({
                **entry,
                "proposals_approved": sum(1 for t in matching if t["status"] == "candidate"),
                "proposals_rejected": sum(1 for t in matching if t["status"] == "rejected"),
                "proposals_pending": sum(1 for t in matching if t["status"] == "proposed"),
            })
        return out

    def update_status(self, name: str, new_status: str) -> Dict:
        """
        Update a tool's status (e.g. "proposed" -> "candidate") and persist.
        Only transitions listed in VALID_STATUS_TRANSITIONS are allowed.

        Reloads from disk under the cross-process lock before checking the
        current status and writing, for the same lost-update reason as
        propose_tool().

        Returns the updated tool dict, or {"error": ...} if not found or
        the transition isn't allowed from the tool's current status.
        """
        with _CrossProcessLock(self._lock_path):
            self.load()

            tool = self._tools_by_name.get(name)
            if not tool:
                return {"error": f"Unknown tool: {name}"}

            current_status = tool["status"]
            allowed = self.VALID_STATUS_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                return {
                    "error": f"Cannot transition '{name}' from '{current_status}' to '{new_status}'. "
                             f"Allowed from '{current_status}': {sorted(allowed) or 'none'}"
                }

            tool["status"] = new_status
            self._persist()
            return self._tools_by_name[name]

    # ── Registry Metadata ──────────────────────────────────────────────────

    @property
    def version(self) -> str:
        return self._data.get("version", "unknown")

    @property
    def last_updated(self) -> str:
        return self._data.get("last_updated", "unknown")

    @property
    def name(self) -> str:
        return self._data.get("name", "Tool Registry")

    def to_dashboard_payload(self) -> Dict:
        """
        Full payload for the /api/fraud/tools endpoint.
        Contains everything the React dashboard needs in one call.
        """
        return {
            "name": self.name,
            "version": self.version,
            "last_updated": self.last_updated,
            "categories": self.list_categories(),
            "tools": self.list_tools(),
            "statistics": self.get_statistics(),
            "scan_history": self.get_scan_history(),
        }

    # ── String Representation ──────────────────────────────────────────────

    def __repr__(self) -> str:
        stats = self.get_statistics()
        return (
            f"ToolRegistry(v{self.version}, "
            f"{stats['total_tools']} tools: "
            f"{stats['active']} active, "
            f"{stats['candidate']} candidate)"
        )
