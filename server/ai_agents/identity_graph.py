"""
check_identity_graph — Graph-Based Fraud Ring Detection
Week 4: PaymentLab Fraud Investigation Agent

Inspired by: Luo et al. (arxiv.org/abs/2509.09928)
"Fraud detection via LLM + Graph Convolutional Networks"

Key insight from the paper: Fraud signals emerge from RELATIONSHIPS,
not just individual entity features. A card that looks clean in isolation
becomes high-risk when it shares an address with 14 other cards.

Architecture:
  - Ego-graph traversal: start from the transaction's entities, hop
    outward via shared attributes (email, card, address, IP, device)
  - Cluster scoring: density + known-bad co-occurrence → risk score
  - Output: graph summary with node/edge counts, fraud ring indicators,
    and a risk score the Claude agent can reason over

Drop-in method for InvestigationTools class.
Also works standalone for unit testing (see __main__ block).
"""

import sqlite3
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from typing import Optional
import os


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    id: str                        # e.g. "card:4111...", "email:x@y.com"
    node_type: str                 # card | email | address | ip | device
    value: str                     # raw attribute value
    txn_count: int = 0             # how many transactions touch this node
    fraud_count: int = 0           # how many of those are known fraud
    risk_score: float = 0.0        # pre-computed node risk [0,1]

@dataclass
class GraphEdge:
    source: str                    # node id
    target: str                    # node id
    shared_txn_ids: list = field(default_factory=list)  # transactions that create this link

@dataclass
class IdentityGraphResult:
    transaction_id: str
    seed_entities: dict            # entities extracted from the target transaction
    nodes: list                    # list of GraphNode dicts
    edges: list                    # list of GraphEdge dicts
    
    # Aggregate risk signals
    cluster_size: int = 0          # total unique cards in the connected component
    shared_email_count: int = 0    # emails shared across >1 card
    shared_address_count: int = 0  # addresses shared across >1 card
    shared_ip_count: int = 0
    shared_device_count: int = 0
    known_fraud_neighbors: int = 0 # cards in cluster with fraud verdicts
    max_card_reuse: int = 0        # max times any single entity is reused
    
    risk_score: float = 0.0        # 0.0–1.0
    risk_label: str = "LOW"        # LOW | MEDIUM | HIGH | CRITICAL
    fraud_ring_detected: bool = False
    synthetic_identity_signals: list = field(default_factory=list)
    geographic_profile: dict = field(default_factory=dict)
    summary: str = ""


# ---------------------------------------------------------------------------
# Core graph builder
# ---------------------------------------------------------------------------

class IdentityGraphBuilder:
    """
    Builds an ego-graph around a transaction by traversing shared entities
    in the transactions database.

    PaymentLab schema (actual column names from fraud_investigator.py queries):
      transactions(
        id TEXT PRIMARY KEY,
        card_last4 TEXT,
        customer_email TEXT,
        shipping_address TEXT,
        billing_country TEXT,
        ip_country TEXT,
        device_fingerprint TEXT,
        amount_cents INTEGER,
        currency TEXT,
        status TEXT,
        fraud_verdict TEXT,
        created_at TEXT
      )
    """

    # ip_country excluded: country-level granularity is too coarse — "JP" connects
    # every Japanese transaction into one cluster. Use full IP addresses if available.
    HOP_ATTRIBUTES = ["customer_email", "card_last4", "shipping_address", "device_fingerprint"]
    MAX_HOPS = 2          # 2-hop ego graph (matches GCN receptive field in paper)
    MAX_NODES = 200       # guard against huge clusters in real deployments

    # Map DB column names to human-readable node types for the graph output
    COLUMN_TO_NODE_TYPE = {
        "customer_email": "email",
        "card_last4": "card",
        "shipping_address": "address",
        "ip_country": "ip",
        "device_fingerprint": "device",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_seed_transaction(self, conn, txn_id: str) -> Optional[sqlite3.Row]:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ? OR id LIKE ? LIMIT 1",
            (txn_id, f"{txn_id}%")
        ).fetchone()
        return row

    def _find_transactions_sharing_attribute(
        self, conn, attr: str, value: str, exclude_txn_id: str
    ) -> list:
        """Return all transactions that share `attr`=`value` (excluding seed)."""
        if not value:
            return []
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE {attr} = ? AND id != ? LIMIT 50",
            (value, exclude_txn_id)
        ).fetchall()
        return [dict(r) for r in rows]

    def _analyze_geography(self, conn, visited_txns: set) -> dict:
        """Aggregate geographic signals across all transactions in the cluster."""
        if not visited_txns:
            return {"coherence_score": 1.0, "merchant_count": 1}

        placeholders = ",".join("?" for _ in visited_txns)
        rows = conn.execute(
            f"""SELECT card_country, billing_country, ip_country, browser_locale, merchant_id
                FROM transactions WHERE id IN ({placeholders})""",
            list(visited_txns)
        ).fetchall()

        card_countries = set()
        billing_countries = set()
        ip_countries = set()
        locales = set()
        merchants = set()

        for r in rows:
            if r["card_country"]:
                card_countries.add(r["card_country"])
            if r["billing_country"]:
                billing_countries.add(r["billing_country"])
            if r["ip_country"]:
                ip_countries.add(r["ip_country"])
            if r["browser_locale"]:
                locales.add(r["browser_locale"])
            if r["merchant_id"]:
                merchants.add(r["merchant_id"])

        all_countries = card_countries | billing_countries | ip_countries
        unique_count = len(all_countries) if all_countries else 1
        coherence_score = round(max(0.0, 1.0 - (unique_count - 1) / 5), 2)

        return {
            "card_issuing_countries": sorted(card_countries),
            "billing_countries": sorted(billing_countries),
            "ip_countries": sorted(ip_countries),
            "browser_locales": sorted(locales),
            "merchants": sorted(merchants),
            "merchant_count": len(merchants),
            "total_unique_countries": unique_count,
            "coherence_score": coherence_score,
        }

    def build(self, txn_id: str) -> IdentityGraphResult:
        result = IdentityGraphResult(
            transaction_id=txn_id,
            seed_entities={},
            nodes=[],
            edges=[]
        )

        try:
            conn = self._get_conn()
        except Exception as e:
            result.summary = f"DB connection failed: {e}"
            return result

        try:
            seed = self._get_seed_transaction(conn, txn_id)
            if not seed:
                result.summary = f"Transaction {txn_id} not found in database."
                return result

            seed = dict(seed)
            actual_id = seed["id"]

            # ---- Seed entity extraction ----
            result.seed_entities = {
                self.COLUMN_TO_NODE_TYPE.get(attr, attr): seed.get(attr, "")
                for attr in self.HOP_ATTRIBUTES
                if seed.get(attr)
            }

            # ---- BFS traversal ----
            # visited_txns: set of txn ids already in graph
            # node_map: entity_id -> GraphNode
            # edge_set: frozenset({src,tgt}) -> GraphEdge
            visited_txns = {actual_id}
            node_map: dict[str, GraphNode] = {}
            edge_map: dict[frozenset, GraphEdge] = {}

            def entity_id(attr, val):
                node_type = self.COLUMN_TO_NODE_TYPE.get(attr, attr)
                return f"{node_type}:{val}"

            def ensure_node(attr, val, txn_row):
                eid = entity_id(attr, val)
                if eid not in node_map:
                    node_map[eid] = GraphNode(
                        id=eid,
                        node_type=self.COLUMN_TO_NODE_TYPE.get(attr, attr),
                        value=val,
                    )
                node_map[eid].txn_count += 1
                verdict = txn_row.get("fraud_verdict", "") or ""
                if verdict.lower() in ("fraudulent", "flagged"):
                    node_map[eid].fraud_count += 1
                return eid

            def ensure_edge(src_id, tgt_id, txn_id_link):
                key = frozenset([src_id, tgt_id])
                if key not in edge_map:
                    edge_map[key] = GraphEdge(source=src_id, target=tgt_id)
                if txn_id_link not in edge_map[key].shared_txn_ids:
                    edge_map[key].shared_txn_ids.append(txn_id_link)

            # Seed transaction: add all its entity nodes
            seed_node_ids = []
            for attr in self.HOP_ATTRIBUTES:
                val = seed.get(attr, "")
                if val:
                    nid = ensure_node(attr, val, seed)
                    seed_node_ids.append(nid)

            # Link seed entity nodes to each other (they co-occur in same txn)
            for i in range(len(seed_node_ids)):
                for j in range(i + 1, len(seed_node_ids)):
                    ensure_edge(seed_node_ids[i], seed_node_ids[j], actual_id)

            # BFS queue: (txn_dict, hop_number)
            queue = deque([(seed, 0)])
            
            while queue and len(visited_txns) < self.MAX_NODES:
                current_txn, hop = queue.popleft()
                if hop >= self.MAX_HOPS:
                    continue

                for attr in self.HOP_ATTRIBUTES:
                    val = current_txn.get(attr, "")
                    if not val:
                        continue

                    neighbors = self._find_transactions_sharing_attribute(
                        conn, attr, val, current_txn["id"]
                    )

                    for neighbor in neighbors:
                        nid = neighbor["id"]
                        
                        # Add neighbor's entity nodes
                        neighbor_node_ids = []
                        for n_attr in self.HOP_ATTRIBUTES:
                            n_val = neighbor.get(n_attr, "")
                            if n_val:
                                n_nid = ensure_node(n_attr, n_val, neighbor)
                                neighbor_node_ids.append(n_nid)

                        # Link: the shared attribute node connects to this txn's other entities
                        shared_nid = entity_id(attr, val)
                        for n_nid in neighbor_node_ids:
                            if n_nid != shared_nid:
                                ensure_edge(shared_nid, n_nid, nid)

                        if nid not in visited_txns:
                            visited_txns.add(nid)
                            if hop + 1 < self.MAX_HOPS:
                                queue.append((neighbor, hop + 1))

            # ---- Compute node risk scores ----
            for node in node_map.values():
                if node.txn_count > 0:
                    node.risk_score = min(1.0, (
                        0.4 * min(node.txn_count / 10, 1.0) +
                        0.6 * (node.fraud_count / node.txn_count)
                    ))

            # ---- Aggregate cluster signals ----
            card_nodes = [n for n in node_map.values() if n.node_type == "card"]
            email_nodes = [n for n in node_map.values() if n.node_type == "email"]
            addr_nodes  = [n for n in node_map.values() if n.node_type == "address"]
            ip_nodes    = [n for n in node_map.values() if n.node_type == "ip"]
            dev_nodes   = [n for n in node_map.values() if n.node_type == "device"]

            result.cluster_size         = len(card_nodes)
            result.shared_email_count   = sum(1 for n in email_nodes   if n.txn_count > 1)
            result.shared_address_count = sum(1 for n in addr_nodes    if n.txn_count > 1)
            result.shared_ip_count      = sum(1 for n in ip_nodes      if n.txn_count > 1)
            result.shared_device_count  = sum(1 for n in dev_nodes     if n.txn_count > 1)
            result.known_fraud_neighbors= sum(1 for n in card_nodes    if n.fraud_count > 0)
            result.max_card_reuse       = max((n.txn_count for n in node_map.values()), default=0)

            # ---- Geographic profile (cross-merchant + country analysis) ----
            result.geographic_profile = self._analyze_geography(conn, visited_txns)
            conn.close()

            # ---- Synthetic identity signals ----
            signals = []
            if result.shared_email_count >= 3:
                signals.append(f"{result.shared_email_count} emails shared across multiple cards - possible account farming")
            if result.shared_address_count >= 2:
                signals.append(f"{result.shared_address_count} addresses reused across cards - drop address pattern")
            if result.shared_ip_count >= 3:
                signals.append(f"{result.shared_ip_count} IPs shared - possible botnet or proxy cluster")
            if result.shared_device_count >= 2:
                signals.append(f"{result.shared_device_count} devices shared - device reuse / emulator pool")
            if result.known_fraud_neighbors >= 2:
                signals.append(f"{result.known_fraud_neighbors} neighboring cards have fraud verdicts")
            if result.cluster_size >= 5:
                signals.append(f"Large cluster: {result.cluster_size} cards connected to this transaction's entities")
            geo = result.geographic_profile
            if len(geo.get("card_issuing_countries", [])) >= 3:
                signals.append(f"Cards issued in {len(geo['card_issuing_countries'])} different countries - possible stolen card aggregation")
            if geo.get("merchant_count", 0) >= 2:
                signals.append(f"Activity spans {geo['merchant_count']} merchants - cross-merchant fraud pattern")
            if geo.get("coherence_score", 1.0) < 0.3:
                signals.append(f"Low geographic coherence ({geo['coherence_score']:.2f}) - countries in card, billing, IP, and locale are highly divergent")
            result.synthetic_identity_signals = signals

            # ---- Risk scoring (weighted formula) ----
            score = 0.0
            score += min(result.cluster_size / 20, 1.0)          * 0.20
            score += min(result.shared_email_count / 5, 1.0)     * 0.15
            score += min(result.shared_address_count / 5, 1.0)   * 0.15
            score += min(result.known_fraud_neighbors / 5, 1.0)  * 0.20
            score += min(result.shared_device_count / 3, 1.0)    * 0.10
            score += (1.0 - geo.get("coherence_score", 1.0))     * 0.10
            score += min(geo.get("merchant_count", 1) / 3, 1.0)  * 0.10
            result.risk_score = round(min(score, 1.0), 3)

            if result.risk_score >= 0.75:
                result.risk_label = "CRITICAL"
                result.fraud_ring_detected = True
            elif result.risk_score >= 0.50:
                result.risk_label = "HIGH"
                result.fraud_ring_detected = len(signals) >= 2
            elif result.risk_score >= 0.25:
                result.risk_label = "MEDIUM"
            else:
                result.risk_label = "LOW"

            # ---- Serialize nodes/edges for output ----
            result.nodes = [asdict(n) for n in node_map.values()]
            result.edges = [asdict(e) for e in edge_map.values()]

            # ---- Human-readable summary ----
            result.summary = _build_summary(result)

        except Exception as e:
            import traceback
            result.summary = f"Graph build error: {e}\n{traceback.format_exc()}"

        return result


def _build_summary(r: IdentityGraphResult) -> str:
    lines = [
        f"Identity graph for transaction {r.transaction_id}:",
        f"  Cluster size: {r.cluster_size} card(s) | {len(r.nodes)} total nodes | {len(r.edges)} edges",
        f"  Shared emails: {r.shared_email_count} | Shared addresses: {r.shared_address_count}",
        f"  Shared IPs: {r.shared_ip_count} | Shared devices: {r.shared_device_count}",
        f"  Known-fraud neighbors: {r.known_fraud_neighbors}",
        f"  Risk score: {r.risk_score:.2f} ({r.risk_label})",
    ]
    geo = r.geographic_profile
    if geo:
        lines.append(f"  Geographic: {geo.get('total_unique_countries', 0)} countries | "
                      f"{geo.get('merchant_count', 0)} merchant(s) | "
                      f"coherence {geo.get('coherence_score', 0):.2f}")
    if r.fraud_ring_detected:
        lines.append("  ** FRAUD RING DETECTED **")
    if r.synthetic_identity_signals:
        lines.append("  Signals:")
        for s in r.synthetic_identity_signals:
            lines.append(f"    - {s}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Drop-in method for InvestigationTools
# Paste this into the InvestigationTools class in fraud_investigator.py
# ---------------------------------------------------------------------------

def check_identity_graph(self, txn_id: str, hops: int = 2) -> dict:
    """
    Build an entity relationship graph around the transaction and detect
    fraud rings or synthetic identity clusters.

    Args:
        txn_id: The transaction to investigate
        hops: Graph traversal depth (1 or 2, default 2)

    Returns:
        dict with graph structure, cluster stats, risk score, and summary
    """
    db_path = getattr(self, "db_path", None) or os.environ.get(
        "PAYMENTLAB_DB", "server/database/transactions.db"
    )

    builder = IdentityGraphBuilder(db_path=db_path)
    builder.MAX_HOPS = max(1, min(hops, 2))  # cap at 2 for performance

    result = builder.build(txn_id)

    return {
        "transaction_id": result.transaction_id,
        "seed_entities": result.seed_entities,
        "graph": {
            "node_count": len(result.nodes),
            "edge_count": len(result.edges),
            "nodes": result.nodes,
            "edges": result.edges,
        },
        "cluster": {
            "size": result.cluster_size,
            "shared_emails": result.shared_email_count,
            "shared_addresses": result.shared_address_count,
            "shared_ips": result.shared_ip_count,
            "shared_devices": result.shared_device_count,
            "known_fraud_neighbors": result.known_fraud_neighbors,
            "max_entity_reuse": result.max_card_reuse,
        },
        "risk": {
            "score": result.risk_score,
            "label": result.risk_label,
            "fraud_ring_detected": result.fraud_ring_detected,
            "synthetic_identity_signals": result.synthetic_identity_signals,
        },
        "geographic_profile": result.geographic_profile,
        "summary": result.summary,
    }


# ---------------------------------------------------------------------------
# Unit test / standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile, datetime, random, string

    print("=== check_identity_graph — standalone demo ===\n")

    # Build a temp SQLite DB that mimics the PaymentLab schema
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()

    conn = sqlite3.connect(db.name)
    conn.execute("""
        CREATE TABLE transactions (
            id TEXT PRIMARY KEY,
            card_last4 TEXT,
            customer_email TEXT,
            shipping_address TEXT,
            billing_country TEXT,
            ip_country TEXT,
            device_fingerprint TEXT,
            amount_cents INTEGER,
            currency TEXT,
            status TEXT,
            fraud_verdict TEXT,
            created_at TEXT
        )
    """)

    def rand_id():
        return ''.join(random.choices(string.hexdigits, k=8))

    now = datetime.datetime.now(datetime.UTC).isoformat()

    # Seed transaction — this is the "legitimate-looking" one we investigate
    seed_id = "txn-seed-0001"
    seed_email = "buyer@example.com"
    seed_address = "1-2-3 Shibuya, Tokyo 150-0002"  # the infamous drop address
    seed_card = "8101"
    seed_ip = "JP"

    rows = [
        # Seed transaction
        (seed_id, seed_card, seed_email, seed_address, "US", seed_ip, "device-A", 4999, "USD", "success", None, now),
    ]

    # 12 other cards that share the same drop address → fraud ring
    for i in range(12):
        rows.append((
            f"txn-ring-{i:04d}",
            f"{1000+i}",
            f"ring{i}@yopmail.com",
            seed_address,           # ← shared drop address
            "US",
            "JP",
            f"device-{i}",
            random.randint(1500, 15000),
            "USD", "success",
            "fraudulent" if i < 8 else None,
            now
        ))

    # 3 cards sharing the same IP country
    for i in range(3):
        rows.append((
            f"txn-ip-{i:04d}",
            f"{2000+i}",
            f"ip{i}@gmail.com",
            f"Some Street {i}, New York",
            "US",
            seed_ip,                # ← shared IP country
            f"device-ip-{i}",
            random.randint(2000, 20000),
            "USD", "success", None, now
        ))

    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()

    # Run the tool
    builder = IdentityGraphBuilder(db_path=db.name)
    result = builder.build(seed_id)

    print(result.summary)
    print(f"\nNodes: {len(result.nodes)}")
    print(f"Edges: {len(result.edges)}")
    print(f"\nTop nodes by txn_count:")
    top = sorted(result.nodes, key=lambda n: n["txn_count"], reverse=True)[:5]
    for n in top:
        print(f"  [{n['node_type']:18s}] {n['value'][:40]:40s} txns={n['txn_count']} fraud={n['fraud_count']}")

    os.unlink(db.name)
    print("\n=== demo complete ===")
