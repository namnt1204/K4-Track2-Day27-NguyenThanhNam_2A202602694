"""
Script to analyze Practice run results against practice_answer_key.json.
Runs outside the sandbox for diagnostics and calibration.
"""
import json
import sys


def analyze():
    with open("solution/practice_report.json", "r", encoding="utf-8") as f:
        report = json.load(f)

    with open("phases/practice_answer_key.json", "r", encoding="utf-8") as f:
        answers = json.load(f)

    verdicts = report.get("result", {}).get("verdicts", [])

    if len(verdicts) != len(answers):
        print(f"WARNING: Length mismatch! verdicts={len(verdicts)}, answers={len(answers)}")

    # Index by seq
    verdicts_by_seq = {v["seq"]: v for v in verdicts}
    answers_by_seq = {a["seq"]: a for a in answers}

    common_seqs = sorted(set(verdicts_by_seq.keys()) & set(answers_by_seq.keys()))
    if len(common_seqs) != len(answers):
        print(f"WARNING: Mismatched sequences! Common count: {len(common_seqs)}")

    tp = 0
    fp = 0
    fn = 0
    tn = 0

    fn_details = []
    fp_details = []

    per_pillar = {}

    for seq in common_seqs:
        v = verdicts_by_seq[seq]
        a = answers_by_seq[seq]

        pred = v["alert"]
        actual = a["is_faulty"]
        pillar = a["pillar"] or "clean"
        fault_key = a["fault_key"]

        if pillar not in per_pillar:
            per_pillar[pillar] = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}

        if pred and actual:
            tp += 1
            per_pillar[pillar]["TP"] += 1
        elif pred and not actual:
            fp += 1
            per_pillar[pillar]["FP"] += 1
            fp_details.append({"seq": seq, "fault_key": fault_key, "pillar": pillar})
        elif not pred and actual:
            fn += 1
            per_pillar[pillar]["FN"] += 1
            fn_details.append({"seq": seq, "fault_key": fault_key, "pillar": pillar})
        else:
            tn += 1
            per_pillar[pillar]["TN"] += 1

    tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    cost = report.get("result", {}).get("cost_ledger", 0.0)
    budget = report.get("result", {}).get("budget", 220.0)
    cost_overage = max(0.0, (cost - budget) / budget) if budget > 0 else 0.0
    score = 100.0 * (0.5 * tpr - 0.3 * fpr - 0.2 * min(cost_overage, 1.0))

    print(f"=== CONFUSION MATRIX & METRICS ===")
    print(f"Total Events: {len(common_seqs)}")
    print(f"TP: {tp}, FP: {fp}, FN: {fn}, TN: {tn}")
    print(f"TPR: {tpr:.4f} ({tp}/{tp+fn})")
    print(f"FPR: {fpr:.4f} ({fp}/{fp+tn})")
    print(f"Precision: {precision:.4f}")
    print(f"Spend: {cost:.1f} / {budget:.1f} (Overage: {cost_overage:.4f})")
    print(f"Official Score: {score:.4f}")
    print("\n=== PER PILLAR BREAKDOWN ===")
    for pil, stats in sorted(per_pillar.items()):
        print(f"Pillar: {pil} -> {stats}")

    print("\n=== FALSE NEGATIVES (Missed Faults) ===")
    for item in fn_details:
        print(f"Seq {item['seq']}: pillar={item['pillar']}, fault_key={item['fault_key']}")

    print("\n=== FALSE POSITIVES (False Alarms) ===")
    for item in fp_details:
        print(f"Seq {item['seq']}: pillar={item['pillar']}, fault_key={item['fault_key']}")


if __name__ == "__main__":
    analyze()
