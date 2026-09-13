"""Tests for DataLoader and RankingService using synthetic in-memory data."""

import datetime as dt
import pathlib
import numpy as np
import pandas as pd
import pytest

from src.data.loader import (
    DataLoader,
    DataNotFoundError,
    DataValidationError,
)
from src.ranking.base import RankingStrategy
from src.ranking.baseline import Baseline3SigmaRanking
from src.services.ranking_service import (
    RankingService,
    RankingNotRunError,
    WeekNotFoundError,
    GatewayNotFoundError,
    InvalidInputError,
    normalize_gateway_id,
    parse_week_date,
)


@pytest.fixture
def synthetic_telemetry_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a temporary parquet dataset for DataLoader testing."""
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir()

    timestamps = pd.date_range(
        start="2026-01-01 00:00:00",
        periods=24 * 30,
        freq="1h",
        tz="UTC",
    )
    records = []
    for gw in ["001122334401", "001122334402"]:
        for ts in timestamps:
            records.append(
                {
                    "gateway_id": gw,
                    "ts_utc": ts.isoformat(),
                    "offline_duration_sec": 10.0,
                    "disconnection_cnt": 1.0,
                    "reboot_cnt": 0.0,
                }
            )
    df = pd.DataFrame(records)
    df.to_parquet(telemetry_dir / "part-0.parquet")
    return tmp_path


# ==========================================
# DATA LOADER TESTS
# ==========================================


def test_data_loader_success(synthetic_telemetry_file: pathlib.Path):
    """Verify DataLoader successfully loads and formats telemetry data."""
    loader = DataLoader(data_dir=synthetic_telemetry_file)
    df = loader.load_telemetry()

    assert not df.empty
    assert "ts" in df.columns
    assert "ts_utc" not in df.columns
    assert "gateway_id" in df.columns
    assert "offline_duration_sec" in df.columns


def test_data_loader_caching(synthetic_telemetry_file: pathlib.Path):
    """Verify DataLoader caches loaded telemetry and reloads when force_reload=True."""
    loader = DataLoader(data_dir=synthetic_telemetry_file)
    df1 = loader.load_telemetry()
    df2 = loader.load_telemetry(force_reload=False)
    assert df1 is df2  # Same object reference in memory

    df3 = loader.load_telemetry(force_reload=True)
    assert df1 is not df3  # Reloaded new object


def test_data_loader_missing_dir(tmp_path: pathlib.Path):
    """Verify DataLoader raises DataNotFoundError for non-existent path."""
    non_existent = tmp_path / "non_existent_folder"
    loader = DataLoader(data_dir=non_existent)
    with pytest.raises(DataNotFoundError, match="Data directory does not exist"):
        loader.load_telemetry()


def test_data_loader_missing_telemetry_folder(tmp_path: pathlib.Path):
    """Verify DataLoader raises DataNotFoundError when telemetry folder is missing."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    loader = DataLoader(data_dir=empty_dir)
    with pytest.raises(DataNotFoundError, match="Telemetry dataset not found"):
        loader.load_telemetry()


def test_data_loader_missing_required_column(tmp_path: pathlib.Path):
    """Verify DataLoader raises DataValidationError if a required metric is missing."""
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir()

    df = pd.DataFrame(
        {
            "gateway_id": ["001122334401"],
            "ts_utc": ["2026-01-01T00:00:00Z"],
            # missing offline_duration_sec, disconnection_cnt, reboot_cnt
        }
    )
    df.to_parquet(telemetry_dir / "bad.parquet")

    loader = DataLoader(data_dir=tmp_path)
    with pytest.raises(DataValidationError, match="missing required column"):
        loader.load_telemetry()


# ==========================================
# SERVICE UTILITY TESTS
# ==========================================


def test_gateway_id_normalization():
    """Verify normalization handles bare hex and colon-separated MACs case-insensitively."""
    assert normalize_gateway_id("001122334455") == "001122334455"
    assert normalize_gateway_id("00:11:22:33:44:55") == "001122334455"
    assert normalize_gateway_id("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"
    assert normalize_gateway_id("  aabbccddeeff  ") == "AABBCCDDEEFF"

    with pytest.raises(InvalidInputError, match="Invalid gateway_id format"):
        normalize_gateway_id("invalid-id")


def test_parse_week_date():
    """Verify date parsing handles strings and date objects."""
    d = parse_week_date("2026-02-02")
    assert d == dt.date(2026, 2, 2)

    d2 = parse_week_date(dt.date(2026, 2, 2))
    assert d2 == dt.date(2026, 2, 2)

    with pytest.raises(InvalidInputError, match="Invalid week date format"):
        parse_week_date("not-a-date")


# ==========================================
# RANKING SERVICE TESTS
# ==========================================


class DummyMockStrategy(RankingStrategy):
    """Mock strategy returning deterministic predictable rankings for testing service."""

    def rank_week(self, frame: pd.DataFrame, monday: dt.date) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"gateway_id": "001122334401", "flagged_hours": 10, "worst_metric": "reboot_cnt"},
                {"gateway_id": "001122334402", "flagged_hours": 5, "worst_metric": "disconnection_cnt"},
            ]
        )

    def build_predictions(
        self,
        frame: pd.DataFrame,
        weeks: list[dt.date] | None = None,
    ) -> pd.DataFrame:
        target_weeks = weeks or [dt.date(2026, 2, 2)]
        rows = []
        for w in target_weeks:
            rows.append(
                {
                    "week_start": w.isoformat(),
                    "rank": 1,
                    "gateway_id": "001122334401",
                    "score": 10.0,
                    "reason": "10 hour(s) beyond 3 sigma",
                }
            )
            rows.append(
                {
                    "week_start": w.isoformat(),
                    "rank": 2,
                    "gateway_id": "001122334402",
                    "score": 5.0,
                    "reason": "5 hour(s) beyond 3 sigma",
                }
            )
        return pd.DataFrame(rows)


def test_service_not_ready_behavior():
    """Verify service raises RankingNotRunError when queried before execution."""
    service = RankingService(strategy=DummyMockStrategy())
    assert not service.is_ready

    with pytest.raises(RankingNotRunError, match="Ranking has not been run yet"):
        service.get_available_weeks()

    with pytest.raises(RankingNotRunError, match="Ranking has not been run yet"):
        service.get_rankings_for_week("2026-02-02")

    with pytest.raises(RankingNotRunError, match="Ranking has not been run yet"):
        service.get_gateway_explanation("001122334401", "2026-02-02")


def test_service_run_and_retrieval(synthetic_telemetry_file: pathlib.Path):
    """Verify service successfully runs strategy, stores results, and retrieves weeks/gateways."""
    loader = DataLoader(data_dir=synthetic_telemetry_file)
    strategy = DummyMockStrategy()
    service = RankingService(strategy=strategy, loader=loader)

    predictions = service.run(weeks=[dt.date(2026, 2, 2), dt.date(2026, 2, 9)])
    assert service.is_ready
    assert len(predictions) == 4
    assert service.get_available_weeks() == ["2026-02-02", "2026-02-09"]

    # Retrieve rankings for week
    rankings = service.get_rankings_for_week("2026-02-02")
    assert len(rankings) == 2
    assert rankings[0]["gateway_id"] == "001122334401"
    assert rankings[0]["rank"] == 1
    assert rankings[1]["gateway_id"] == "001122334402"
    assert rankings[1]["rank"] == 2

    # Retrieve gateway explanation (with colon-separated format)
    explanation = service.get_gateway_explanation("00:11:22:33:44:01", "2026-02-02")
    assert explanation["gateway_id"] == "001122334401"
    assert explanation["rank"] == 1
    assert explanation["score"] == 10.0
    assert "beyond 3 sigma" in explanation["reason"]


def test_service_week_not_found(synthetic_telemetry_file: pathlib.Path):
    """Verify service raises WeekNotFoundError for an uncalculated week."""
    loader = DataLoader(data_dir=synthetic_telemetry_file)
    service = RankingService(strategy=DummyMockStrategy(), loader=loader)
    service.run(weeks=[dt.date(2026, 2, 2)])

    with pytest.raises(WeekNotFoundError, match="No rankings found for week"):
        service.get_rankings_for_week("2026-03-23")


def test_service_gateway_not_found(synthetic_telemetry_file: pathlib.Path):
    """Verify service raises GatewayNotFoundError for an unknown gateway."""
    loader = DataLoader(data_dir=synthetic_telemetry_file)
    service = RankingService(strategy=DummyMockStrategy(), loader=loader)
    service.run(weeks=[dt.date(2026, 2, 2)])

    with pytest.raises(GatewayNotFoundError, match="is not in the ranked results"):
        service.get_gateway_explanation("998877665544", "2026-02-02")
