"""
verdict_accuracy.py — Sprint 1 Task 4
======================================
Joins investigation_reports.verdict with transactions.metadata->fraud_label
to compute precision, recall, F1, and a confusion matrix per fraud pattern.

Disclaimer: Measures whether Claude correctly reasons over seeded synthetic
evidence — not real-world fraud detection accuracy.

Usage:
    python verdict_accuracy.py
    python verdict_accuracy.py --db path/to/payment_lab.db
    python verdict_accuracy.py --out results.json
    python verdict_accuracy.py --suspicious=fraud   (treat SUSPICIOUS as fraud prediction)
    python verdict_accuracy.py --suspicious=legit   (treat SUSPICIOUS as legit prediction)
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

FRAUD_LABELS = {"velocity_burst", "card_testing", "address_reuse", "geo_mismatch", "currency_hopping"}
DEFAULT_DB   = os.path.join(os.path.dirname(__file__), "payment_lab.db")

LABEL_DISPLAY = {
    "velocity_burst":   "Velocity Burst",
    "card_testing":     "Card Testing",
    "address_reuse":    "Address Reuse",
    "geo_mismatch":     "Geo Mismatch",
    "currency_hopping": "Currency Hopping",
    "__legitimate__":   "Legitimate (no fraud)",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_data(db_path: str, suspicious_as: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            t.id                                          AS txn_id,
            json_extract(t.metadata, '$.fraud_label')    AS fraud_label,
            json_extract(t.metadata, '$.pattern_id')     AS pattern_id,
            ir.verdict                                   AS verdict,
            ir.created_at                                AS investigated_at
        FROM investigation_reports ir
        JOIN transactions t ON t.id = ir.transaction_id
        ORDER BY t.id
    """).fetchall()
    conn.close()

    records = []
    for r in rows:
        label   = r["fraud_label"]
        verdict = r["verdict"]

        # Normalise ground truth
        is_fraud = label in FRAUD_LABELS

        # Normalise prediction
        if verdict == "FRAUDULENT":
            predicted_fraud = True
        elif verdict == "LEGITIMATE":
            predicted_fraud = False
        else:  # SUSPICIOUS
            predicted_fraud = (suspicious_as == "fraud")

        records.append({
            "txn_id":         r["txn_id"],
            "label":          label or "__legitimate__",
            "pattern_id":     r["pattern_id"],
            "verdict":        verdict,
            "is_fraud":       is_fraud,
            "predicted_fraud": predicted_fraud,
        })
    return records


def metrics(tp, fp, tn, fn) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    f1        = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    accuracy  = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) else 0.0
    return dict(precision=precision, recall=recall, f1=f1, accuracy=accuracy,
                tp=tp, fp=fp, tn=tn, fn=fn)


def per_pattern(records: list[dict]) -> dict:
    patterns = {}
    for r in records:
        lbl = r["label"]
        if lbl not in patterns:
            patterns[lbl] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        b = patterns[lbl]
        fraud = r["is_fraud"]
        pred  = r["predicted_fraud"]
        if fraud and pred:       b["tp"] += 1
        elif fraud and not pred: b["fn"] += 1
        elif not fraud and pred: b["fp"] += 1
        else:                    b["tn"] += 1
    return {lbl: metrics(**b) for lbl, b in patterns.items()}


# ── Rendering ─────────────────────────────────────────────────────────────────

BAR_WIDTH = 30

def bar(value: float, width: int = BAR_WIDTH) -> str:
    filled = round(value * width)
    return "#" * filled + "-" * (width - filled)


def pct(v: float) -> str:
    return f"{v * 100:5.1f}%"


def print_report(records: list[dict], suspicious_as: str):
    total = len(records)
    fraud_total = sum(1 for r in records if r["is_fraud"])
    legit_total = total - fraud_total

    tp = sum(1 for r in records if r["is_fraud"] and r["predicted_fraud"])
    fp = sum(1 for r in records if not r["is_fraud"] and r["predicted_fraud"])
    tn = sum(1 for r in records if not r["is_fraud"] and not r["predicted_fraud"])
    fn = sum(1 for r in records if r["is_fraud"] and not r["predicted_fraud"])
    m  = metrics(tp, fp, tn, fn)

    susp_mode = "counted as FRAUD" if suspicious_as == "fraud" else "counted as LEGIT"

    print()
    print("=" * 64)
    print("         FRAUD DETECTION -- VERDICT ACCURACY REPORT")
    print("=" * 64)
    print(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Dataset   : {total} investigated transactions "
          f"({fraud_total} fraud seeds, {legit_total} legitimate)")
    print(f"  SUSPICIOUS: {susp_mode}")
    print()

    # ── Confusion matrix ──
    print("  CONFUSION MATRIX")
    print("  " + "-" * 46)
    print("                        Predicted")
    print("                   FRAUD        LEGIT")
    print(f"  Actual  FRAUD  | TP = {tp:3d}    | FN = {fn:3d}    |")
    print(f"          LEGIT  | FP = {fp:3d}    | TN = {tn:3d}    |")
    print("  " + "-" * 46)
    print()

    # ── Overall metrics ──
    print("  OVERALL METRICS")
    print("  " + "-" * 46)
    metrics_rows = [
        ("Precision", m["precision"],
         "of flagged transactions that are truly fraud"),
        ("Recall",    m["recall"],
         "of actual fraud transactions caught"),
        ("F1 Score",  m["f1"],
         "harmonic mean of precision and recall"),
        ("Accuracy",  m["accuracy"],
         "of all predictions correct"),
    ]
    for name, val, desc in metrics_rows:
        print(f"  {name:<12} {pct(val)}  {bar(val)}  {desc}")
    print()

    # ── Per-pattern breakdown ──
    pattern_metrics = per_pattern(records)
    fraud_patterns  = {k: v for k, v in pattern_metrics.items() if k != "__legitimate__"}
    legit_pattern   = pattern_metrics.get("__legitimate__")

    print("  PER-PATTERN BREAKDOWN (fraud seeds)")
    print("  " + "-" * 62)
    print(f"  {'Pattern':<22} {'Prec':>6}  {'Recall':>6}  {'F1':>6}  {'TP':>3} {'FN':>3}  Bar")
    print("  " + "-" * 62)
    for lbl, pm in sorted(fraud_patterns.items(), key=lambda x: -x[1]["recall"]):
        display = LABEL_DISPLAY.get(lbl, lbl)
        n = pm["tp"] + pm["fn"]
        print(f"  {display:<22} {pct(pm['precision'])}  {pct(pm['recall'])}  "
              f"{pct(pm['f1'])}  {pm['tp']:>3} {pm['fn']:>3}  {bar(pm['recall'], 20)}")
    print()

    # ── Legitimate (false positive analysis) ──
    if legit_pattern:
        lm = legit_pattern
        fp_rate = lm["fp"] / (lm["fp"] + lm["tn"]) if (lm["fp"] + lm["tn"]) else 0
        print("  LEGITIMATE TRANSACTIONS (false positive analysis)")
        print("  " + "-" * 46)
        print(f"  Total legitimate : {lm['fp'] + lm['tn']}")
        print(f"  Correctly cleared: {lm['tn']}  (true negatives)")
        print(f"  Wrongly flagged  : {lm['fp']}  (false positives)")
        print(f"  False positive rate: {pct(fp_rate)}  {bar(fp_rate)}")
        print()

    # ── False negatives detail ──
    missed = [r for r in records if r["is_fraud"] and not r["predicted_fraud"]]
    if missed:
        print("  MISSED FRAUD (false negatives)")
        print("  " + "-" * 46)
        for r in missed:
            print(f"  txn={r['txn_id'][:16]}…  pattern={r['label']}  verdict={r['verdict']}")
        print()
    else:
        print("  [OK] No fraud transactions were missed (perfect recall)")
        print()

    # ── Disclaimer ──
    print("  " + "-" * 62)
    print("  NOTE: Measures whether Claude correctly reasons over seeded")
    print("  synthetic evidence -- not real-world fraud detection accuracy.")
    print("  " + "-" * 62)
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Verdict accuracy assessment")
    parser.add_argument("--db",         default=DEFAULT_DB,  help="Path to payment_lab.db")
    parser.add_argument("--out",        default=None,        help="Optional JSON output file")
    parser.add_argument("--suspicious", default="fraud",
                        choices=["fraud", "legit"],
                        help="How to count SUSPICIOUS verdicts (default: fraud)")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    records = load_data(args.db, args.suspicious)
    if not records:
        print("No investigation reports found.", file=sys.stderr)
        sys.exit(1)

    print_report(records, args.suspicious)

    if args.out:
        pattern_metrics = per_pattern(records)
        tp = sum(1 for r in records if r["is_fraud"] and r["predicted_fraud"])
        fp = sum(1 for r in records if not r["is_fraud"] and r["predicted_fraud"])
        tn = sum(1 for r in records if not r["is_fraud"] and not r["predicted_fraud"])
        fn = sum(1 for r in records if r["is_fraud"] and not r["predicted_fraud"])
        out = {
            "generated_at":  datetime.now().isoformat(),
            "suspicious_as": args.suspicious,
            "overall":       metrics(tp, fp, tn, fn),
            "per_pattern":   pattern_metrics,
        }
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"  JSON results written to: {args.out}")


if __name__ == "__main__":
    main()
