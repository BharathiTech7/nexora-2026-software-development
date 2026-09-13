"""Phase 5: Deliberate error handling and edge-case tests.

Covers:
- DataLoader: missing dir, missing telemetry, missing column, empty dataset,
  malformed timestamps, NaN values, force_reload cache behavior
- RankingService: not-ready state, unavailable week, unknown gateway,
  data refresh cycle, repeated run
- API: all input validation, error translation, missing params, run lifecycle
- Bug regression: stale-cache-after-failed-reload (genuine bug found Phase 5)
"""

import datetime as dt
import pathlib

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api.main import app, set_ranking_service
from src.data.loader import DataLoader, DataNotFoundError, DataValidationError
from src.ranking.base import RankingStrategy
from src.services.ranking_service import (
    GatewayNotFoundError,
    InvalidInputError,
    RankingNotRunError,
    RankingService,
    WeekNotFoundError,
    normalize_gateway_id,
    parse_week_date,
)


# ─────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────


def _write_good_telemetry(tel_dir: pathlib.Path) -> None:
    """Write a minimal valid parquet file to `tel_dir`."""
    timestamps = pd.date_range("2026-01-01", periods=24 * 30, freq="1h", tz="UTC")
    rows = []
    for gw in ["AABBCCDDEEFF", "112233445566"]:
        for ts in timestamps:
            rows.append(
                {
                    "gateway_id": gw,
                    "ts_utc": ts.isoformat(),
                    "offline_duration_sec": 5.0,
                    "disconnection_cnt": 1.0,
                    "reboot_cnt": 0.0,
                }
            )
    pd.DataFrame(rows).to_parquet(tel_dir / "part-0.parquet")


@pytest.fixture
def good_data_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture: valid telemetry data in a temporary directory."""
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    _write_good_telemetry(tel_dir)
    return tmp_path


class FixedMockStrategy(RankingStrategy):
    """Deterministic mock returning 15 gateways for any requested week."""

    def rank_week(self, frame: pd.DataFrame, monday: dt.date) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "gateway_id": f"AABB0000{i:04d}",
                    "flagged_hours": 15 - i,
                    "worst_metric": "offline_duration_sec",
                }
                for i in range(15)
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
            for rank_i in range(1, 16):
                gw = f"AABB0000{rank_i:04d}"
                rows.append(
                    {
                        "week_start": w.isoformat(),
                        "rank": rank_i,
                        "gateway_id": gw,
                        "score": float(16 - rank_i),
                        "reason": f"{16 - rank_i} hour(s) beyond 3 sigma on offline_duration_sec",
                    }
                )
        return pd.DataFrame(rows)


@pytest.fixture
def api_client(good_data_dir: pathlib.Path) -> TestClient:
    """TestClient with a fully populated synthetic RankingService."""
    loader = DataLoader(data_dir=good_data_dir)
    service = RankingService(strategy=FixedMockStrategy(), loader=loader)
    service.run(weeks=[dt.date(2026, 2, 2), dt.date(2026, 2, 9)])
    set_ranking_service(service)
    return TestClient(app)


@pytest.fixture
def unready_api_client(tmp_path: pathlib.Path) -> TestClient:
    """TestClient whose service has NOT been run yet."""
    service = RankingService(strategy=FixedMockStrategy(), loader=DataLoader(data_dir=tmp_path))
    set_ranking_service(service)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# BUG REGRESSION TEST
# Found Phase 5: When force_reload=True raises an exception during reload,
# the old cached DataFrame remains accessible via a subsequent non-forced call.
# This means POST /run could appear to fail but the API would silently keep
# serving stale results.
# Fix: clear _cached_telemetry = None BEFORE the reload attempt so a failed
# reload leaves the cache empty, not stale.
# ─────────────────────────────────────────────────────────────────────────────


def test_bug_regression_stale_cache_after_failed_force_reload(tmp_path: pathlib.Path):
    """Regression: failed force_reload must NOT leave stale data in cache.

    BUG (found Phase 5): DataLoader.load_telemetry(force_reload=True) did not
    clear _cached_telemetry before attempting to re-read parquet.  When reading
    failed (e.g. corrupted file placed by an operator), a subsequent non-forced
    call returned the old cached data, silently hiding the failure.

    FIX: cache is cleared to None at the start of a force_reload so that a
    failed reload results in an empty cache, not stale results.
    """
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    _write_good_telemetry(tel_dir)

    loader = DataLoader(data_dir=tmp_path)

    # First successful load populates the cache
    df1 = loader.load_telemetry()
    assert len(df1) > 0
    assert loader._cached_telemetry is not None

    # Corrupt the parquet on disk (simulates operator writing a bad file)
    (tel_dir / "part-0.parquet").write_bytes(b"THIS IS NOT PARQUET")

    # A force_reload should raise — the cache should be cleared to None
    with pytest.raises(DataValidationError):
        loader.load_telemetry(force_reload=True)

    # After a failed force_reload the cache must be cleared (not stale)
    assert loader._cached_telemetry is None, (
        "BUG: stale cache was retained after a failed force_reload. "
        "A subsequent non-forced call would silently return old data."
    )

    # A subsequent non-forced call must also raise (no stale data to serve)
    with pytest.raises(DataValidationError):
        loader.load_telemetry(force_reload=False)


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADER — additional edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_loader_empty_dataset_raises(tmp_path: pathlib.Path):
    """Verify DataLoader raises DataValidationError for an empty parquet file."""
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    empty_df = pd.DataFrame(
        columns=["gateway_id", "ts_utc", "offline_duration_sec", "disconnection_cnt", "reboot_cnt"]
    )
    empty_df.to_parquet(tel_dir / "empty.parquet")

    loader = DataLoader(data_dir=tmp_path)
    with pytest.raises(DataValidationError, match="empty"):
        loader.load_telemetry()


def test_loader_malformed_timestamps_raises(tmp_path: pathlib.Path):
    """Verify DataLoader raises DataValidationError for unparseable ts_utc values."""
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    # Store ts_utc as a non-datetime object type that Pandas cannot coerce to UTC
    df = pd.DataFrame(
        {
            "gateway_id": ["001122334401"],
            "ts_utc": ["not-a-timestamp"],
            "offline_duration_sec": [5.0],
            "disconnection_cnt": [1.0],
            "reboot_cnt": [0.0],
        }
    )
    df.to_parquet(tel_dir / "bad_ts.parquet")

    loader = DataLoader(data_dir=tmp_path)
    with pytest.raises(DataValidationError, match="ts_utc"):
        loader.load_telemetry()


def test_loader_nan_metric_values_accepted(tmp_path: pathlib.Path):
    """Verify DataLoader does NOT reject rows with NaN metric values.

    The existing 3-sigma baseline handles NaN values itself (std=0 → NaN → fillna(False)).
    The DataLoader should not silently discard these rows or reject the entire dataset;
    delegating NaN handling to the ranking algorithm is the deliberate design decision.
    """
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    timestamps = pd.date_range("2026-01-01", periods=24, freq="1h", tz="UTC")
    rows = []
    for ts in timestamps:
        rows.append(
            {
                "gateway_id": "001122334401",
                "ts_utc": ts.isoformat(),
                "offline_duration_sec": None,  # NaN
                "disconnection_cnt": 1.0,
                "reboot_cnt": 0.0,
            }
        )
    pd.DataFrame(rows).to_parquet(tel_dir / "nan.parquet")

    loader = DataLoader(data_dir=tmp_path)
    df = loader.load_telemetry()
    # DataLoader must accept it — NaN handling belongs to the ranking logic
    assert not df.empty
    assert df["offline_duration_sec"].isna().any()


def test_loader_force_reload_refreshes_data(tmp_path: pathlib.Path):
    """Verify that force_reload=True causes DataLoader to read fresh data."""
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    _write_good_telemetry(tel_dir)

    loader = DataLoader(data_dir=tmp_path)
    df1 = loader.load_telemetry()

    # Append more rows to the parquet (simulate new data arriving)
    extra = pd.DataFrame(
        {
            "gateway_id": ["FFEEDDCCBBAA"],
            "ts_utc": ["2026-02-01T00:00:00+00:00"],
            "offline_duration_sec": [99.0],
            "disconnection_cnt": [9.0],
            "reboot_cnt": [3.0],
        }
    )
    (tel_dir / "part-0.parquet").unlink()
    combined = pd.concat([
        pd.read_parquet(tel_dir) if any(tel_dir.iterdir()) else pd.DataFrame(),
        extra
    ]).reset_index(drop=True)
    _write_good_telemetry(tel_dir)  # rewrite base
    combined_full = pd.concat([pd.read_parquet(tel_dir), extra]).reset_index(drop=True)
    combined_full.to_parquet(tel_dir / "part-1.parquet")

    # Without force_reload, cache is returned
    df2 = loader.load_telemetry(force_reload=False)
    assert df1 is df2

    # With force_reload, fresh data with more rows is loaded
    df3 = loader.load_telemetry(force_reload=True)
    assert df1 is not df3
    assert len(df3) > len(df1)


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE — additional edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_service_repeated_run_updates_results(good_data_dir: pathlib.Path):
    """Verify that calling run() again replaces previous predictions."""
    service = RankingService(strategy=FixedMockStrategy(), loader=DataLoader(data_dir=good_data_dir))

    # Run for week 1
    service.run(weeks=[dt.date(2026, 2, 2)])
    assert service.get_available_weeks() == ["2026-02-02"]

    # Run again for a different week set
    service.run(weeks=[dt.date(2026, 2, 9), dt.date(2026, 2, 16)])
    weeks_after = service.get_available_weeks()
    assert "2026-02-02" not in weeks_after
    assert "2026-02-09" in weeks_after
    assert "2026-02-16" in weeks_after


def test_service_explanation_week_not_found(good_data_dir: pathlib.Path):
    """Verify get_gateway_explanation raises WeekNotFoundError before GatewayNotFoundError."""
    service = RankingService(strategy=FixedMockStrategy(), loader=DataLoader(data_dir=good_data_dir))
    service.run(weeks=[dt.date(2026, 2, 2)])

    with pytest.raises(WeekNotFoundError):
        service.get_gateway_explanation("AABB00000001", "2026-03-23")


def test_service_run_propagates_data_errors(tmp_path: pathlib.Path):
    """Verify that service.run() propagates DataLoader errors rather than silently swallowing them."""
    non_existent = tmp_path / "no_data_here"
    loader = DataLoader(data_dir=non_existent)
    service = RankingService(strategy=FixedMockStrategy(), loader=loader)

    with pytest.raises(DataNotFoundError):
        service.run()

    # Service should not be in ready state after a failed run
    assert not service.is_ready


# ─────────────────────────────────────────────────────────────────────────────
# INPUT VALIDATION — gateway ID and date helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_gateway_empty_string():
    """Verify empty string is rejected as an invalid gateway ID."""
    with pytest.raises(InvalidInputError, match="Invalid gateway_id format"):
        normalize_gateway_id("")


def test_normalize_gateway_too_short():
    """Verify gateway ID shorter than 12 hex chars is rejected."""
    with pytest.raises(InvalidInputError, match="Invalid gateway_id format"):
        normalize_gateway_id("0011")


def test_normalize_gateway_too_long():
    """Verify gateway ID longer than 12 hex chars is rejected."""
    with pytest.raises(InvalidInputError, match="Invalid gateway_id format"):
        normalize_gateway_id("001122334455FF")


def test_parse_week_date_datetime_input():
    """Verify parse_week_date extracts date from a datetime object."""
    result = parse_week_date(dt.datetime(2026, 2, 2, 12, 30, 0))
    assert result == dt.date(2026, 2, 2)


def test_parse_week_date_unsupported_type():
    """Verify parse_week_date raises InvalidInputError for unsupported types."""
    with pytest.raises(InvalidInputError, match="Unsupported week type"):
        parse_week_date(20260202)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# API — additional endpoint edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_api_rankings_before_run_returns_503(unready_api_client: TestClient):
    """Verify /rankings/{week} returns 503 before rankings are computed."""
    res = unready_api_client.get("/rankings/2026-02-02")
    assert res.status_code == 503
    assert res.json()["error"] == "RankingNotReady"


def test_api_explanation_before_run_returns_503(unready_api_client: TestClient):
    """Verify /gateways/{id}/explanation returns 503 before rankings are computed."""
    res = unready_api_client.get("/gateways/001122334401/explanation?week=2026-02-02")
    assert res.status_code == 503
    assert res.json()["error"] == "RankingNotReady"


def test_api_rankings_valid_week(api_client: TestClient):
    """Verify /rankings/{week} returns 15 gateways with correct schema."""
    res = api_client.get("/rankings/2026-02-02")
    assert res.status_code == 200
    data = res.json()
    assert data["total_ranked"] == 15
    assert data["week"] == "2026-02-02"
    gateways = data["gateways"]
    assert len(gateways) == 15
    assert gateways[0]["rank"] == 1
    assert gateways[-1]["rank"] == 15
    for gw in gateways:
        assert "gateway_id" in gw
        assert "score" in gw
        assert "reason" in gw


def test_api_rankings_invalid_date(api_client: TestClient):
    """Verify /rankings/{week} returns appropriate errors for unparseable date strings.

    Notes:
    - '2026/02/02': FastAPI parses the '/' as a URL path separator, so the path
      becomes '/rankings/2026' — no route matches, so FastAPI itself returns 404.
      This is correct routing behavior, not a domain error.
    - 'february-2026', 'not-a-date', 'hello': passed as a single path segment,
      reach our handler, fail date parsing, and return 422 with our error schema.
    - Python 3.11+ accepts '20260202' as a valid compact ISO date (YYYYMMDD),
      so it parses successfully but returns 404 if that week has no rankings.
    """
    # Valid single path segments that fail date parsing -> 422 domain error
    for bad in ["february-2026", "not-a-date", "hello"]:
        res = api_client.get(f"/rankings/{bad}")
        assert res.status_code == 422, f"Expected 422 for '{bad}', got {res.status_code}"
        assert res.json()["error"] == "InvalidInput"

    # '2026/02/02': slash interpreted as URL path separators -> FastAPI 404 (route not found)
    res = api_client.get("/rankings/2026/02/02")
    assert res.status_code == 404

    # '20260202': valid ISO compact date -> parsed as 2026-02-02, result 200 or 404 by week availability
    res = api_client.get("/rankings/20260202")
    assert res.status_code in (200, 404)


def test_api_rankings_unavailable_week(api_client: TestClient):
    """Verify /rankings/{week} returns 404 for a valid date that was not scored."""
    res = api_client.get("/rankings/2099-01-01")
    assert res.status_code == 404
    assert res.json()["error"] == "WeekNotFound"


def test_api_explanation_success(api_client: TestClient):
    """Verify /gateways/{id}/explanation returns correct fields for a valid gateway."""
    res = api_client.get("/gateways/AABB00000001/explanation?week=2026-02-02")
    assert res.status_code == 200
    data = res.json()
    assert data["gateway_id"] == "AABB00000001"
    assert data["rank"] == 1
    assert data["week"] == "2026-02-02"
    assert isinstance(data["score"], float)
    assert len(data["reason"]) > 0


def test_api_explanation_lowercase_gateway_id(api_client: TestClient):
    """Verify /gateways/{id}/explanation accepts lowercase gateway IDs."""
    res = api_client.get("/gateways/aabb00000001/explanation?week=2026-02-02")
    assert res.status_code == 200
    assert res.json()["gateway_id"] == "AABB00000001"


def test_api_explanation_colon_gateway_id(api_client: TestClient):
    """Verify /gateways/{id}/explanation accepts colon-separated gateway IDs."""
    res = api_client.get("/gateways/AA:BB:00:00:00:01/explanation?week=2026-02-02")
    assert res.status_code == 200
    assert res.json()["gateway_id"] == "AABB00000001"


def test_api_explanation_malformed_gateway_id(api_client: TestClient):
    """Verify /gateways/{id}/explanation returns 422 for malformed gateway IDs."""
    for bad_id in ["short", "toolong1234567890", "ZZ:XX:YY:ZZ:XX:YY"]:
        res = api_client.get(f"/gateways/{bad_id}/explanation?week=2026-02-02")
        assert res.status_code == 422, f"Expected 422 for '{bad_id}', got {res.status_code}"
        assert res.json()["error"] == "InvalidInput"


def test_api_explanation_unknown_gateway(api_client: TestClient):
    """Verify /gateways/{id}/explanation returns 404 for a valid but unknown gateway."""
    res = api_client.get("/gateways/FFFFFFFFFFFF/explanation?week=2026-02-02")
    assert res.status_code == 404
    assert res.json()["error"] == "GatewayNotFound"


def test_api_explanation_unavailable_week(api_client: TestClient):
    """Verify /gateways/{id}/explanation returns 404 for an unscored week."""
    res = api_client.get("/gateways/AABB00000001/explanation?week=2099-12-31")
    assert res.status_code == 404
    assert res.json()["error"] == "WeekNotFound"


def test_api_explanation_missing_week_param(api_client: TestClient):
    """Verify /gateways/{id}/explanation returns 422 when week query param is absent."""
    res = api_client.get("/gateways/AABB00000001/explanation")
    # FastAPI returns 422 Unprocessable Entity for missing required Query params
    assert res.status_code == 422


def test_api_explanation_invalid_week_format(api_client: TestClient):
    """Verify /gateways/{id}/explanation returns 422 for a malformed week string."""
    res = api_client.get("/gateways/AABB00000001/explanation?week=not-a-date")
    assert res.status_code == 422
    assert res.json()["error"] == "InvalidInput"


def test_api_run_success(api_client: TestClient):
    """Verify POST /run returns success summary and refreshes available weeks."""
    res = api_client.post("/run")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total_rows"] > 0
    assert data["weeks_count"] > 0
    assert isinstance(data["available_weeks"], list)
    assert len(data["available_weeks"]) == data["weeks_count"]


def test_api_run_makes_rankings_available(good_data_dir: pathlib.Path):
    """Verify POST /run followed by GET /rankings/{week} returns results."""
    service = RankingService(strategy=FixedMockStrategy(), loader=DataLoader(data_dir=good_data_dir))
    set_ranking_service(service)

    client = TestClient(app)
    # Rankings not ready yet
    assert not service.is_ready

    # POST /run — note: strategy defaults to [2026-02-02]
    run_res = client.post("/run")
    assert run_res.status_code == 200

    # Now GET /rankings should succeed
    week = run_res.json()["available_weeks"][0]
    rank_res = client.get(f"/rankings/{week}")
    assert rank_res.status_code == 200
    assert rank_res.json()["total_ranked"] == 15


def test_api_error_responses_have_consistent_schema(api_client: TestClient):
    """Verify all error responses contain 'error' and 'detail' fields."""
    error_endpoints = [
        ("/rankings/invalid-date", "GET"),
        ("/rankings/2099-01-01", "GET"),
        ("/gateways/bad/explanation?week=2026-02-02", "GET"),
        ("/gateways/FFFFFFFFFFFF/explanation?week=2026-02-02", "GET"),
    ]
    for path, method in error_endpoints:
        res = api_client.request(method, path)
        assert res.status_code in (400, 404, 422, 503), f"Unexpected status {res.status_code} for {path}"
        body = res.json()
        assert "error" in body, f"Missing 'error' key in response from {path}: {body}"
        assert "detail" in body, f"Missing 'detail' key in response from {path}: {body}"
