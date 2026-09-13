"""End-to-end test: exercises the complete real application stack.

This test uses the REAL Baseline3SigmaRanking algorithm (not a mock),
synthetic telemetry constructed to reliably trigger 3-sigma anomalies,
and the FastAPI TestClient to cross the HTTP boundary end-to-end.

Flow exercised:
    Synthetic parquet telemetry
        ↓ DataLoader
        ↓ RankingService
        ↓ Baseline3SigmaRanking (real algorithm)
        ↓ FastAPI (POST /run)
        ↓ GET /rankings/{week}
        ↓ GET /gateways/{id}/explanation
"""

import datetime as dt
import pathlib

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api.main import app, set_ranking_service
from src.data.loader import DataLoader
from src.ranking.baseline import Baseline3SigmaRanking
from src.services.ranking_service import RankingService


def _build_e2e_telemetry(data_dir: pathlib.Path) -> dict:
    """Build synthetic telemetry with one clearly anomalous gateway.

    Returns metadata about the injected anomaly for later assertion.
    """
    tel_dir = data_dir / "telemetry"
    tel_dir.mkdir(parents=True, exist_ok=True)

    scored_monday = dt.date(2026, 2, 2)
    end = pd.Timestamp(scored_monday, tz="UTC")

    # 16 normal gateways + 1 anomaly gateway (need >=15 to rank)
    normal_gws = [f"CAFE{i:08X}" for i in range(16)]
    anomaly_gw = "DEADBEEF0001"

    rng = np.random.default_rng(seed=7)

    records = []

    # Build 35 days of data strictly before the monday (baseline window = 28 days)
    timestamps = pd.date_range(
        start=end - dt.timedelta(days=35),
        end=end - dt.timedelta(hours=1),
        freq="1h",
    )

    # Normal gateways: stable metrics
    for gw in normal_gws:
        offline = rng.normal(loc=30.0, scale=3.0, size=len(timestamps)).clip(0)
        disconn = rng.normal(loc=1.0, scale=0.3, size=len(timestamps)).clip(0)
        reboot = rng.normal(loc=0.05, scale=0.02, size=len(timestamps)).clip(0)
        for i, ts in enumerate(timestamps):
            records.append(
                {
                    "gateway_id": gw,
                    "ts_utc": ts.isoformat(),
                    "offline_duration_sec": float(offline[i]),
                    "disconnection_cnt": float(disconn[i]),
                    "reboot_cnt": float(reboot[i]),
                }
            )

    # Anomaly gateway: stable for 28 days, massive spikes in the last 7 days
    for i, ts in enumerate(timestamps):
        is_recent = ts >= end - dt.timedelta(days=7)
        offline_val = 3000.0 if is_recent and i >= len(timestamps) - 40 else float(rng.normal(30, 3))
        records.append(
            {
                "gateway_id": anomaly_gw,
                "ts_utc": ts.isoformat(),
                "offline_duration_sec": max(0.0, offline_val),
                "disconnection_cnt": float(rng.normal(1.0, 0.3)),
                "reboot_cnt": float(rng.normal(0.05, 0.02)),
            }
        )

    pd.DataFrame(records).to_parquet(tel_dir / "e2e.parquet")

    return {
        "scored_monday": scored_monday,
        "anomaly_gw": anomaly_gw,
    }


@pytest.fixture
def e2e_setup(tmp_path: pathlib.Path):
    """Create telemetry, configure the real RankingService, return TestClient + metadata."""
    meta = _build_e2e_telemetry(tmp_path)

    loader = DataLoader(data_dir=tmp_path)
    # Restrict scored_weeks to the one monday our synthetic data covers
    strategy = Baseline3SigmaRanking(scored_weeks=[meta["scored_monday"]])
    service = RankingService(strategy=strategy, loader=loader)

    # Service starts unready — must call /run first
    set_ranking_service(service)

    client = TestClient(app)
    return client, meta


# ─────────────────────────────────────────────────────────────────────────────
# TRUE END-TO-END TEST
# Crosses the real HTTP boundary through the complete application stack.
# ─────────────────────────────────────────────────────────────────────────────


def test_e2e_run_rank_explain_consistency(e2e_setup):
    """End-to-end: POST /run → GET /rankings → GET /explanation with real algorithm.

    This test exercises the full application stack:
      synthetic telemetry → DataLoader → Baseline3SigmaRanking → RankingService
      → POST /run → GET /rankings/{week} → GET /gateways/{id}/explanation

    It verifies that:
    1. The service starts unready.
    2. POST /run returns a success response with row and week counts.
    3. GET /rankings/{week} returns exactly 15 gateways with the correct schema.
    4. The anomaly gateway (known to have large spikes) appears in the top rankings.
    5. GET /gateways/{id}/explanation for that gateway returns data consistent
       with the ranking response (rank, score, week all match).
    6. The reason string mentions the expected metric breach.
    7. All ranks are sequential 1..15 with no duplicates.
    """
    client, meta = e2e_setup
    week_str = meta["scored_monday"].isoformat()
    anomaly_gw = meta["anomaly_gw"]

    # ── Step 0: Rankings not ready ────────────────────────────────────────────
    pre_run = client.get(f"/rankings/{week_str}")
    assert pre_run.status_code == 503, "Expected 503 before /run"
    assert pre_run.json()["error"] == "RankingNotReady"

    # ── Step 1: POST /run ─────────────────────────────────────────────────────
    run_res = client.post("/run")
    assert run_res.status_code == 200, f"POST /run failed: {run_res.text}"
    run_data = run_res.json()
    assert run_data["status"] == "success"
    assert run_data["total_rows"] > 0
    assert run_data["weeks_count"] >= 1
    assert week_str in run_data["available_weeks"]

    # ── Step 2: GET /rankings/{week} ─────────────────────────────────────────
    rank_res = client.get(f"/rankings/{week_str}")
    assert rank_res.status_code == 200, f"GET /rankings failed: {rank_res.text}"
    rank_data = rank_res.json()

    assert rank_data["week"] == week_str
    assert rank_data["total_ranked"] == 15
    gateways = rank_data["gateways"]
    assert len(gateways) == 15

    # Ranks must be sequential 1..15
    ranks_returned = [gw["rank"] for gw in gateways]
    assert ranks_returned == list(range(1, 16)), f"Ranks not sequential: {ranks_returned}"

    # All required fields present in every item
    for gw in gateways:
        assert "gateway_id" in gw
        assert "score" in gw
        assert "reason" in gw
        assert isinstance(gw["score"], float)
        assert len(gw["reason"]) > 0

    # Anomaly gateway must appear in results (it had strong spikes)
    gw_ids = [gw["gateway_id"] for gw in gateways]
    assert anomaly_gw in gw_ids, (
        f"Anomaly gateway {anomaly_gw} not found in rankings. "
        f"All returned: {gw_ids}"
    )

    # Anomaly gateway should be ranked high (top half) given its large spikes
    anomaly_rank = next(gw["rank"] for gw in gateways if gw["gateway_id"] == anomaly_gw)
    anomaly_score = next(gw["score"] for gw in gateways if gw["gateway_id"] == anomaly_gw)
    assert anomaly_rank <= 8, f"Anomaly gateway rank {anomaly_rank} unexpectedly low"
    assert anomaly_score > 0, "Anomaly gateway should have flagged hours"

    # ── Step 3: GET /gateways/{id}/explanation ────────────────────────────────
    exp_res = client.get(f"/gateways/{anomaly_gw}/explanation?week={week_str}")
    assert exp_res.status_code == 200, f"GET /explanation failed: {exp_res.text}"
    exp_data = exp_res.json()

    # Explanation must be internally consistent with the ranking response
    assert exp_data["gateway_id"] == anomaly_gw
    assert exp_data["week"] == week_str
    assert exp_data["rank"] == anomaly_rank, (
        f"Explanation rank {exp_data['rank']} != ranking rank {anomaly_rank}"
    )
    assert exp_data["score"] == anomaly_score, (
        f"Explanation score {exp_data['score']} != ranking score {anomaly_score}"
    )
    assert len(exp_data["reason"]) > 0
    assert len(exp_data["reason"]) <= 300, "Reason exceeds 300 character limit"

    # Reason should reference the sigma breach
    assert "sigma" in exp_data["reason"].lower(), (
        f"Reason does not mention sigma: {exp_data['reason']}"
    )


def test_e2e_run_twice_updates_results(tmp_path: pathlib.Path):
    """End-to-end: POST /run twice; second run uses refreshed data.

    Verifies the 'run again' requirement: after data changes in the data directory,
    POST /run reloads from disk rather than serving stale cached results.
    """
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()

    scored_monday = dt.date(2026, 2, 2)
    end = pd.Timestamp(scored_monday, tz="UTC")
    timestamps = pd.date_range(
        start=end - dt.timedelta(days=35),
        end=end - dt.timedelta(hours=1),
        freq="1h",
    )

    def write_telemetry(anomaly_gw: str) -> None:
        """Write 16 normal + 1 anomaly gateway."""
        normal_gws = [f"AAAA{i:08X}" for i in range(16)]
        rng = np.random.default_rng(seed=42)
        records = []
        for gw in normal_gws:
            offline = rng.normal(loc=30.0, scale=3.0, size=len(timestamps)).clip(0)
            for i, ts in enumerate(timestamps):
                records.append({
                    "gateway_id": gw,
                    "ts_utc": ts.isoformat(),
                    "offline_duration_sec": float(offline[i]),
                    "disconnection_cnt": 1.0,
                    "reboot_cnt": 0.05,
                })
        # Anomaly gateway with large spikes in recent 7 days
        for i, ts in enumerate(timestamps):
            is_recent = ts >= end - dt.timedelta(days=7)
            records.append({
                "gateway_id": anomaly_gw,
                "ts_utc": ts.isoformat(),
                "offline_duration_sec": 5000.0 if is_recent and i >= len(timestamps) - 30 else 30.0,
                "disconnection_cnt": 1.0,
                "reboot_cnt": 0.05,
            })
        # Overwrite existing parquet
        for f in tel_dir.glob("*.parquet"):
            f.unlink()
        pd.DataFrame(records).to_parquet(tel_dir / "data.parquet")

    # First run: anomaly_gw_1 is the spike gateway
    anomaly_gw_1 = "DEADBEEF0001"
    write_telemetry(anomaly_gw_1)

    loader = DataLoader(data_dir=tmp_path)
    # Restrict scored_weeks to the one monday our synthetic data covers
    service = RankingService(strategy=Baseline3SigmaRanking(scored_weeks=[scored_monday]), loader=loader)
    set_ranking_service(service)
    client = TestClient(app)

    run1 = client.post("/run")
    assert run1.status_code == 200
    week_str = scored_monday.isoformat()

    rank1 = client.get(f"/rankings/{week_str}")
    assert rank1.status_code == 200
    gw_ids_run1 = [gw["gateway_id"] for gw in rank1.json()["gateways"]]
    assert anomaly_gw_1 in gw_ids_run1, "Anomaly GW1 must appear in run1 rankings"

    # Replace data on disk: new anomaly gateway
    anomaly_gw_2 = "CAFEBABE0002"
    write_telemetry(anomaly_gw_2)

    # Second POST /run: must pick up new data
    run2 = client.post("/run")
    assert run2.status_code == 200

    rank2 = client.get(f"/rankings/{week_str}")
    assert rank2.status_code == 200
    gw_ids_run2 = [gw["gateway_id"] for gw in rank2.json()["gateways"]]
    assert anomaly_gw_2 in gw_ids_run2, "Anomaly GW2 must appear after run2 with new data"
