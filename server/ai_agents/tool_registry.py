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
from typing import Any, Dict, List, Optional


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
        self._data = {}
        self._tools_by_name = {}
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

    def get_schemas(self, status: str = "active") -> List[Dict]:
        """
        Generate Claude API tool schemas.

        Returns a list in the exact format Claude's tool_use expects:
            [{"name": ..., "description": ..., "input_schema": {...}}, ...]

        Args:
            status: Filter by status. "active" = only deployed tools.
                    "all" = include candidates (for testing).

        Returns:
            List of tool schema dicts ready for the Anthropic API.
        """
        schemas = []
        for tool in self._data["tools"]:
            if status != "all" and tool["status"] != status:
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
        builtin = [t for t in tools if t["source"] == "builtin"]
        external = [t for t in tools if t["source"] == "external"]

        by_category = {}
        for cat in self._data["categories"]:
            cat_tools = self._tools_by_category.get(cat["id"], [])
            by_category[cat["id"]] = {
                "total": len(cat_tools),
                "active": len([t for t in cat_tools if t["status"] == "active"]),
                "candidate": len([t for t in cat_tools if t["status"] == "candidate"]),
            }

        return {
            "total_tools": len(tools),
            "active": len(active),
            "candidate": len(candidate),
            "proposed": len(proposed),
            "by_source": {
                "builtin": len(builtin),
                "external": len(external),
            },
            "by_category": by_category,
        }

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
