"""Unit tests for ranking strategies using synthetic in-memory data."""

import datetime as dt
import numpy as np
import pandas as pd
import pytest

from src.ranking.base import RankingStrategy
from src.ranking.baseline import Baseline3SigmaRanking


def generate_synthetic_telemetry(
    gateways: list[str],
    start_date: dt.date,
    days: int = 35,
    anomaly_gateway: str | None = None,
) -> pd.DataFrame:
    """Generate synthetic hourly telemetry for testing ranking strategies in memory."""
    timestamps = pd.date_range(
        start=pd.Timestamp(start_date, tz="UTC"),
        periods=days * 24,
        freq="1h",
    )
    records = []
    rng = np.random.default_rng(seed=42)

    for gw in gateways:
        base_offline = rng.normal(loc=10.0, scale=2.0, size=len(timestamps))
        base_disconn = rng.normal(loc=1.0, scale=0.5, size=len(timestamps))
        base_reboot = rng.normal(loc=0.1, scale=0.05, size=len(timestamps))

        base_offline = np.clip(base_offline, 0, None)
        base_disconn = np.clip(base_disconn, 0, None)
        base_reboot = np.clip(base_reboot, 0, None)

        if gw == anomaly_gateway:
            # Inject anomalies in the last 7 days (last 168 hours)
            base_offline[-10:] += 50.0  # 10 massive spikes (well over 3 sigma)
            base_disconn[-5:] += 15.0

        for i, ts in enumerate(timestamps):
            records.append(
                {
                    "gateway_id": gw,
                    "ts": ts,
                    "offline_duration_sec": float(base_offline[i]),
                    "disconnection_cnt": float(base_disconn[i]),
                    "reboot_cnt": float(base_reboot[i]),
                }
            )

    return pd.DataFrame(records)


def test_ranking_strategy_subclass():
    """Verify Baseline3SigmaRanking implements RankingStrategy abstract interface."""
    strategy = Baseline3SigmaRanking()
    assert isinstance(strategy, RankingStrategy)


def test_rank_week_detects_synthetic_anomaly():
    """Verify that rank_week correctly detects anomaly gateway and identifies worst metric."""
    target_monday = dt.date(2026, 2, 2)
    gateways = [f"0011223344{i:02d}" for i in range(16)]
    anomaly_gw = gateways[3]

    # Generate 35 days ending at target_monday
    start_date = target_monday - dt.timedelta(days=35)
    df = generate_synthetic_telemetry(gateways, start_date=start_date, days=35, anomaly_gateway=anomaly_gw)

    strategy = Baseline3SigmaRanking()
    ranked = strategy.rank_week(df, target_monday)

    assert not ranked.empty
    assert ranked.iloc[0]["gateway_id"] == anomaly_gw
    assert ranked.iloc[0]["flagged_hours"] >= 10
    assert ranked.iloc[0]["worst_metric"] in ["offline_duration_sec", "disconnection_cnt", "reboot_cnt"]


def test_rank_week_empty_window():
    """Verify rank_week returns expected empty DataFrame schema if no data matches window."""
    target_monday = dt.date(2026, 2, 2)
    df = pd.DataFrame(columns=["gateway_id", "ts", "offline_duration_sec", "disconnection_cnt", "reboot_cnt"])

    strategy = Baseline3SigmaRanking()
    ranked = strategy.rank_week(df, target_monday)

    assert list(ranked.columns) == ["gateway_id", "flagged_hours", "worst_metric"]
    assert len(ranked) == 0


def test_build_predictions_schema_and_constraints():
    """Verify build_predictions produces valid schema, ranks 1..15, and reason length within bounds."""
    target_monday = dt.date(2026, 2, 2)
    gateways = [f"AA00112233{i:02d}" for i in range(16)]
    start_date = target_monday - dt.timedelta(days=35)
    df = generate_synthetic_telemetry(gateways, start_date=start_date, days=35, anomaly_gateway=gateways[0])

    strategy = Baseline3SigmaRanking()
    predictions = strategy.build_predictions(df, weeks=[target_monday])

    assert len(predictions) == 15
    assert list(predictions.columns) == ["week_start", "rank", "gateway_id", "score", "reason"]
    assert list(predictions["rank"]) == list(range(1, 16))
    assert (predictions["score"] >= 0).all()
    assert (predictions["reason"].str.len() <= 300).all()
    assert (predictions["reason"].str.len() > 0).all()


def test_deterministic_output():
    """Verify that multiple runs with identical data produce identical results."""
    target_monday = dt.date(2026, 2, 2)
    gateways = [f"EE00112233{i:02d}" for i in range(16)]
    start_date = target_monday - dt.timedelta(days=35)
    df = generate_synthetic_telemetry(gateways, start_date=start_date, days=35)

    strategy = Baseline3SigmaRanking()
    res1 = strategy.build_predictions(df, weeks=[target_monday])
    res2 = strategy.build_predictions(df, weeks=[target_monday])

    pd.testing.assert_frame_equal(res1, res2)
