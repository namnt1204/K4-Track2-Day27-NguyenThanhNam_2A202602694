"""
Defense implementation for Data Siege — Day 27.
Monitors a stream of pipeline events and issues alerts for faulty runs.
"""
import sys
import math
from api import Verdict


def register(ctx):
    ctx.on("data_batch", check_data_batch)
    ctx.on("contract_checkpoint", check_contract_checkpoint)
    ctx.on("lineage_run", check_lineage_run)
    ctx.on("feature_materialization", check_feature_materialization)
    ctx.on("embedding_batch", check_embedding_batch)


def check_data_batch(payload, ctx):
    batch_id = payload.get("batch_id")
    if not batch_id:
        return Verdict(alert=False, pillar="checks", reason="missing batch_id")

    resp = ctx.tools.batch_profile(batch_id)
    if not isinstance(resp, dict) or "error" in resp:
        err = resp.get("error", "tool error") if isinstance(resp, dict) else "invalid response"
        return Verdict(alert=False, pillar="checks", reason=str(err))

    row_count = resp.get("row_count")
    null_rate = resp.get("null_rate", {})
    mean_amount = resp.get("mean_amount")
    std_amount = resp.get("std_amount")
    staleness_min = resp.get("staleness_min")

    base = ctx.baseline
    rc_min = base.get("row_count_min", 435.4732)
    rc_max = base.get("row_count_max", 561.2948)
    nr_max = base.get("null_rate_max", 0.0109)
    ma_min = base.get("mean_amount_min", 72.7645)
    ma_max = base.get("mean_amount_max", 90.6053)
    stale_max = base.get("staleness_min_max", 8.418)

    # 1. Single signal hard thresholds (mean +/- 3 sigma clean bounds)
    if row_count is not None:
        if row_count < rc_min:
            return Verdict(alert=True, pillar="checks", reason=f"row_count low: {row_count:.2f} < {rc_min:.2f}")
        if row_count > rc_max:
            return Verdict(alert=True, pillar="checks", reason=f"row_count high: {row_count:.2f} > {rc_max:.2f}")

    max_nr = 0.0
    if isinstance(null_rate, dict):
        if null_rate:
            max_nr = max(float(v) for v in null_rate.values())
    elif isinstance(null_rate, (int, float)):
        max_nr = float(null_rate)

    if max_nr > nr_max:
        return Verdict(alert=True, pillar="checks", reason=f"null_rate high: {max_nr:.4f} > {nr_max:.4f}")

    if mean_amount is not None:
        if mean_amount < ma_min:
            return Verdict(alert=True, pillar="checks", reason=f"mean_amount low: {mean_amount:.2f} < {ma_min:.2f}")
        if mean_amount > ma_max:
            return Verdict(alert=True, pillar="checks", reason=f"mean_amount high: {mean_amount:.2f} > {ma_max:.2f}")

    if staleness_min is not None and staleness_min > stale_max:
        return Verdict(alert=True, pillar="checks", reason=f"staleness high: {staleness_min:.2f} > {stale_max:.2f}")

    # 2. Composite anomaly detection:
    # Multiple signals deviating simultaneously near the boundary (~2.5 sigma, dev >= 0.82)
    devs = []
    if row_count is not None and (rc_max > rc_min):
        rc_center = (rc_min + rc_max) / 2.0
        rc_half = (rc_max - rc_min) / 2.0
        devs.append(abs(row_count - rc_center) / rc_half)

    if mean_amount is not None and (ma_max > ma_min):
        ma_center = (ma_min + ma_max) / 2.0
        ma_half = (ma_max - ma_min) / 2.0
        devs.append(abs(mean_amount - ma_center) / ma_half)

    if nr_max > 0 and nr_max > 0:
        devs.append(max_nr / nr_max)

    if staleness_min is not None and stale_max > 0:
        devs.append(staleness_min / stale_max)

    elevated = sum(1 for d in devs if d >= 0.82)
    if elevated >= 2:
        return Verdict(alert=True, pillar="checks", reason=f"composite anomaly: {elevated} dimensions near 3-sigma limits")

    return Verdict(alert=False, pillar="checks", reason="batch metrics within baseline bounds")


def check_contract_checkpoint(payload, ctx):
    contract_id = payload.get("contract_id")
    checkpoint_batch_id = payload.get("checkpoint_batch_id")
    if not contract_id or not checkpoint_batch_id:
        return Verdict(alert=False, pillar="contracts", reason="missing contract_id or checkpoint_batch_id")

    resp = ctx.tools.contract_diff(contract_id, checkpoint_batch_id)
    if not isinstance(resp, dict) or "error" in resp:
        err = resp.get("error", "tool error") if isinstance(resp, dict) else "invalid response"
        return Verdict(alert=False, pillar="contracts", reason=str(err))

    violations = resp.get("violations", [])
    freshness_delay_min = resp.get("freshness_delay_min", 0.0)

    # 1. Violations array check
    if violations and len(violations) > 0:
        details = []
        for v in violations:
            if isinstance(v, str):
                details.append(v)
            elif isinstance(v, dict):
                details.append(v.get("message") or v.get("rule") or str(v))
            else:
                details.append(str(v))
        return Verdict(alert=True, pillar="contracts", reason=f"violations: {'; '.join(details)}")

    # 2. Freshness delay check
    declared_sla = payload.get("sla_freshness_min") or payload.get("freshness_sla_min") or payload.get("max_freshness_delay_min")
    if declared_sla is not None and freshness_delay_min > declared_sla:
        return Verdict(alert=True, pillar="contracts", reason=f"freshness delay exceeded declared SLA: {freshness_delay_min:.2f} > {declared_sla:.2f}")

    freshness_max = ctx.baseline.get("freshness_delay_max_min", 11.1141)
    if freshness_delay_min > freshness_max:
        return Verdict(alert=True, pillar="contracts", reason=f"freshness delay exceeded baseline: {freshness_delay_min:.2f} > {freshness_max:.2f}")

    return Verdict(alert=False, pillar="contracts", reason="contract clean")


def check_lineage_run(payload, ctx):
    run_id = payload.get("run_id")
    if not run_id:
        return Verdict(alert=False, pillar="lineage", reason="missing run_id")

    resp = ctx.tools.lineage_graph_slice(run_id)
    if not isinstance(resp, dict) or "error" in resp:
        err = resp.get("error", "tool error") if isinstance(resp, dict) else "invalid response"
        return Verdict(alert=False, pillar="lineage", reason=str(err))

    duration_ms = resp.get("duration_ms", 0.0)
    actual_upstream = resp.get("actual_upstream", [])
    actual_downstream_count = resp.get("actual_downstream_count", 0)

    # 1. Runtime check
    max_duration = ctx.baseline.get("lineage_duration_ms_max", 5134.9804)
    if duration_ms > max_duration:
        return Verdict(alert=True, pillar="lineage", reason=f"duration exceeded: {duration_ms:.1f}ms > {max_duration:.1f}ms")

    # Normalize upstreams
    upstream_set = set()
    if isinstance(actual_upstream, list):
        for u in actual_upstream:
            if isinstance(u, str):
                upstream_set.add(u)
            elif isinstance(u, dict):
                upstream_set.add(u.get("name") or u.get("id") or str(u))
    elif isinstance(actual_upstream, dict):
        upstream_set = set(actual_upstream.keys())

    # Check payload string representations for robust matching across potential key names
    payload_repr = str(payload)

    # 2. Check model-specific dependencies (e.g. dbt:stg_orders requiring raw.orders & raw.customers)
    if "stg_orders" in payload_repr:
        missing = []
        if "raw.orders" not in upstream_set:
            missing.append("raw.orders")
        if "raw.customers" not in upstream_set:
            missing.append("raw.customers")
        if missing:
            return Verdict(alert=True, pillar="lineage", reason=f"stg_orders missing mandatory upstreams: {missing}")

    # Check if payload explicitly declared expected upstreams
    for k, v in payload.items():
        if "upstream" in k.lower() and isinstance(v, (list, set, tuple)):
            missing = [u for u in v if u not in upstream_set]
            if missing:
                return Verdict(alert=True, pillar="lineage", reason=f"missing declared upstream: {missing}")

    # 3. Check orphan output
    for k, v in payload.items():
        if "downstream" in k.lower():
            if isinstance(v, (int, float)) and v > 0 and actual_downstream_count == 0:
                return Verdict(alert=True, pillar="lineage", reason=f"orphan output: actual_downstream=0, expected_{k}={v}")
            elif isinstance(v, (list, set, tuple)) and len(v) > 0 and actual_downstream_count == 0:
                return Verdict(alert=True, pillar="lineage", reason=f"orphan output: actual_downstream=0, expected_{k}={v}")

    if ("stg_" in payload_repr or "staging" in payload_repr) and actual_downstream_count == 0:
        return Verdict(alert=True, pillar="lineage", reason="orphan staging model output: actual_downstream=0")

    return Verdict(alert=False, pillar="lineage", reason="lineage run clean")


def check_feature_materialization(payload, ctx):
    feature_view = payload.get("feature_view")
    batch_id = payload.get("batch_id")
    if not feature_view or not batch_id:
        return Verdict(alert=False, pillar="ai_infra", reason="missing feature_view or batch_id")

    resp = ctx.tools.feature_drift(feature_view=feature_view, ref=batch_id)
    if not isinstance(resp, dict) or "error" in resp:
        err = resp.get("error", "tool error") if isinstance(resp, dict) else "invalid response"
        return Verdict(alert=False, pillar="ai_infra", reason=str(err))

    mean_shift_sigma = resp.get("mean_shift_sigma")
    serve_mean = resp.get("serve_mean")
    train_mean = resp.get("train_mean")
    train_std = resp.get("train_std")

    if mean_shift_sigma is None and train_std is not None and train_std > 0 and serve_mean is not None and train_mean is not None:
        mean_shift_sigma = abs(serve_mean - train_mean) / train_std

    threshold = 0.65
    if mean_shift_sigma is not None and mean_shift_sigma > threshold:
        return Verdict(alert=True, pillar="ai_infra", reason=f"feature drift: {mean_shift_sigma:.4f} > {threshold}")

    return Verdict(alert=False, pillar="ai_infra", reason="feature distribution clean")


def check_embedding_batch(payload, ctx):
    corpus = payload.get("corpus")
    chunk_batch_id = payload.get("chunk_batch_id")
    if not corpus or not chunk_batch_id:
        return Verdict(alert=False, pillar="ai_infra", reason="missing corpus or chunk_batch_id")

    resp = ctx.tools.embedding_drift(corpus=corpus, ref=chunk_batch_id)
    if not isinstance(resp, dict) or "error" in resp:
        err = resp.get("error", "tool error") if isinstance(resp, dict) else "invalid response"
        return Verdict(alert=False, pillar="ai_infra", reason=str(err))

    centroid_shift = resp.get("centroid_shift", 0.0)
    avg_doc_age_days = resp.get("avg_doc_age_days", 0.0)

    base = ctx.baseline
    shift_max = base.get("embedding_centroid_shift_max", 0.0435)
    doc_age_max = base.get("corpus_avg_doc_age_days_max", 49.7955)

    if centroid_shift is not None and centroid_shift > shift_max:
        return Verdict(alert=True, pillar="ai_infra", reason=f"centroid shift: {centroid_shift:.4f} > {shift_max:.4f}")

    doc_age_threshold = 48.0 if doc_age_max > 48.0 else doc_age_max
    if avg_doc_age_days is not None and avg_doc_age_days >= doc_age_threshold:
        return Verdict(alert=True, pillar="ai_infra", reason=f"corpus avg doc age high: {avg_doc_age_days:.2f} >= {doc_age_threshold}")

    return Verdict(alert=False, pillar="ai_infra", reason="embedding batch clean")
