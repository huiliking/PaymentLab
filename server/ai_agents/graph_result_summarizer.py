"""
Graph Result Summarizer for Claude API Context
===============================================
File: server/ai_agents/graph_result_summarizer.py

Strips raw nodes/edges from check_identity_graph results before
they enter Claude's conversation history. Preserves the full result
for React D3 visualization via report.tool_results.

Drop-in: one import + one line change in fraud_investigator.py

    from graph_result_summarizer import summarize_for_llm

    # In _claude_investigate(), replace:
    #     result_str = json.dumps(tool_result, default=str)
    # With:
    #     llm_result = summarize_for_llm(tool_name, tool_result)
    #     result_str = json.dumps(llm_result, default=str)
"""


def summarize_for_llm(tool_name: str, tool_result: dict) -> dict:
    """
    Summarize tool results before they enter Claude's conversation history.
    
    Currently handles:
    - check_identity_graph: strips nodes/edges (~7,500 tokens → ~280 tokens)
    
    All other tools pass through unchanged.
    
    Args:
        tool_name: Name of the tool that produced this result
        tool_result: Raw result dict from registry.execute()
    
    Returns:
        Summarized result dict (safe to serialize into Claude messages)
    """
    if tool_name == "check_identity_graph":
        return _summarize_identity_graph(tool_result)
    
    # Future: add other large-payload tools here
    # if tool_name == "get_address_history":
    #     return _summarize_address_history(tool_result)
    
    return tool_result


def _summarize_identity_graph(result: dict) -> dict:
    """
    Strip raw graph data (nodes/edges arrays) from identity graph result.
    
    What Claude needs for reasoning:
    ✓ cluster stats (size, shared counts, fraud neighbors)
    ✓ risk assessment (score, label, fraud ring, signals)
    ✓ human-readable summary
    ✓ node/edge counts (for scale awareness)
    
    What Claude does NOT need:
    ✗ Individual node objects (id, type, label, properties)
    ✗ Individual edge objects (source, target, type, properties)
    ✗ Full adjacency structure
    
    The full result (with nodes/edges) is still stored in
    report.tool_results for the React D3 visualization.
    """
    graph = result.get("graph", {})
    
    # Build a compact graph summary instead of raw arrays
    graph_summary = {
        "node_count": graph.get("node_count", len(graph.get("nodes", []))),
        "edge_count": graph.get("edge_count", len(graph.get("edges", []))),
    }
    
    # If nodes are present, compute type distribution (useful context, tiny footprint)
    nodes = graph.get("nodes", [])
    if nodes:
        type_counts = {}
        fraud_cards = 0
        for node in nodes:
            ntype = node.get("type", "unknown")
            type_counts[ntype] = type_counts.get(ntype, 0) + 1
            if ntype == "card" and node.get("properties", {}).get("fraud_verdict"):
                fraud_cards += 1
        graph_summary["node_types"] = type_counts
        graph_summary["fraud_flagged_cards"] = fraud_cards
    
    # Return everything EXCEPT the raw nodes/edges
    return {
        "transaction_id": result.get("transaction_id"),
        "seed_entities": result.get("seed_entities"),
        "graph": graph_summary,
        "cluster": result.get("cluster"),
        "risk": result.get("risk"),
        "summary": result.get("summary"),
    }


# --- Self-test ---
if __name__ == "__main__":
    import json
    
    # Simulate a 33-card cluster result (minimal version for testing)
    sample = {
        "transaction_id": "54ce08b8-test",
        "seed_entities": {"card": "****4242"},
        "graph": {
            "node_count": 55,
            "edge_count": 157,
            "nodes": [{"id": f"card_{i}", "type": "card", "label": f"***{i}", 
                       "properties": {"fraud_verdict": "FRAUDULENT" if i < 20 else None}}
                      for i in range(33)] +
                     [{"id": f"email_{i}", "type": "email", "label": f"u{i}@temp.org",
                       "properties": {}} for i in range(10)],
            "edges": [{"source": f"card_{i}", "target": f"email_{i%10}", 
                       "type": "used_email", "properties": {}} for i in range(33)],
        },
        "cluster": {"size": 33, "shared_emails": 10, "known_fraud_neighbors": 19},
        "risk": {"score": 0.91, "label": "CRITICAL", "fraud_ring_detected": True,
                 "synthetic_identity_signals": ["Disposable email cluster"]},
        "summary": "Identity graph: 33 cards, 19 fraud neighbors, FRAUD RING"
    }
    
    full_json = json.dumps(sample)
    summarized = summarize_for_llm("check_identity_graph", sample)
    summary_json = json.dumps(summarized)
    
    print(f"Full:       {len(full_json):>6,} chars  ≈ {len(full_json)//4:>5,} tokens")
    print(f"Summarized: {len(summary_json):>6,} chars  ≈ {len(summary_json)//4:>5,} tokens")
    print(f"Reduction:  {(1 - len(summary_json)/len(full_json))*100:.1f}%")
    print(f"\nSummarized output:")
    print(json.dumps(summarized, indent=2))
    
    # Verify no data loss for reasoning
    assert summarized["cluster"]["size"] == 33
    assert summarized["risk"]["fraud_ring_detected"] == True
    assert summarized["graph"]["node_count"] == 55
    assert summarized["graph"]["fraud_flagged_cards"] == 20
    assert "nodes" not in summarized["graph"]
    assert "edges" not in summarized["graph"]
    print("\n✓ All assertions passed")
