"""FastAPI endpoint tests using synthetic in-memory test fixtures and TestClient."""

import datetime as dt
import pathlib
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from src.api.main import app, set_ranking_service
from src.data.loader import DataLoader
from src.ranking.base import RankingStrategy
from src.services.ranking_service import RankingService


class SyntheticMockStrategy(RankingStrategy):
    """Mock strategy returning deterministic predictable rankings for API tests."""

    def rank_week(self, frame: pd.DataFrame, monday: dt.date) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"gateway_id": f"0011223344{i:02d}", "flagged_hours": 20 - i, "worst_metric": "offline_duration_sec"}
                for i in range(15)
            ]
        )

    def build_predictions(
        self,
        frame: pd.DataFrame,
        weeks: list[dt.date] | None = None,
    ) -> pd.DataFrame:
        target_weeks = weeks or [dt.date(2026, 2, 2), dt.date(2026, 2, 9)]
        rows = []
        for w in target_weeks:
            for rank in range(1, 16):
                gw = f"0011223344{rank:02d}"
                rows.append(
                    {
                        "week_start": w.isoformat(),
                        "rank": rank,
                        "gateway_id": gw,
                        "score": float(20 - rank),
                        "reason": f"{20 - rank} hour(s) beyond 3 sigma on offline_duration_sec",
                    }
                )
        return pd.DataFrame(rows)


@pytest.fixture
def mock_api_client(tmp_path: pathlib.Path) -> TestClient:
    """Provide a TestClient configured with an isolated synthetic RankingService."""
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir()

    # Create dummy parquet file
    df = pd.DataFrame(
        {
            "gateway_id": ["001122334401"],
            "ts_utc": ["2026-01-01T00:00:00Z"],
            "offline_duration_sec": [10.0],
            "disconnection_cnt": [1.0],
            "reboot_cnt": [0.0],
        }
    )
    df.to_parquet(telemetry_dir / "test.parquet")

    loader = DataLoader(data_dir=tmp_path)
    strategy = SyntheticMockStrategy()
    service = RankingService(strategy=strategy, loader=loader)
    service.run(weeks=[dt.date(2026, 2, 2), dt.date(2026, 2, 9)])

    set_ranking_service(service)
    return TestClient(app)


def test_health_endpoint(mock_api_client: TestClient):
    """Verify health endpoint returns healthy status and available weeks."""
    response = mock_api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["is_ranking_ready"] is True
    assert "2026-02-02" in data["available_weeks"]


def test_get_rankings_success(mock_api_client: TestClient):
    """Verify GET /rankings/{week} returns top 15 gateways matching schema."""
    response = mock_api_client.get("/rankings/2026-02-02")
    assert response.status_code == 200
    data = response.json()
    assert data["week"] == "2026-02-02"
    assert data["total_ranked"] == 15
    assert len(data["gateways"]) == 15

    top_gw = data["gateways"][0]
    assert top_gw["rank"] == 1
    assert top_gw["gateway_id"] == "001122334401"
    assert top_gw["score"] == 19.0
    assert "beyond 3 sigma" in top_gw["reason"]


def test_get_rankings_invalid_date_format(mock_api_client: TestClient):
    """Verify GET /rankings/{week} returns 422 for invalid date strings."""
    response = mock_api_client.get("/rankings/invalid-date")
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "InvalidInput"
    assert "Expected YYYY-MM-DD" in data["detail"]


def test_get_rankings_unavailable_week(mock_api_client: TestClient):
    """Verify GET /rankings/{week} returns 404 for uncalculated weeks."""
    response = mock_api_client.get("/rankings/2026-12-25")
    assert response.status_code == 404
    data = response.json()
    assert data["error"] == "WeekNotFound"


def test_get_gateway_explanation_success(mock_api_client: TestClient):
    """Verify GET /gateways/{id}/explanation returns ranking details."""
    response = mock_api_client.get("/gateways/001122334401/explanation?week=2026-02-02")
    assert response.status_code == 200
    data = response.json()
    assert data["gateway_id"] == "001122334401"
    assert data["week"] == "2026-02-02"
    assert data["rank"] == 1
    assert data["score"] == 19.0
    assert "beyond 3 sigma" in data["reason"]


def test_get_gateway_explanation_colon_format(mock_api_client: TestClient):
    """Verify GET /gateways/{id}/explanation handles colon-separated MAC address."""
    response = mock_api_client.get("/gateways/00:11:22:33:44:01/explanation?week=2026-02-02")
    assert response.status_code == 200
    data = response.json()
    assert data["gateway_id"] == "001122334401"
    assert data["rank"] == 1


def test_get_gateway_explanation_unknown_gateway(mock_api_client: TestClient):
    """Verify GET /gateways/{id}/explanation returns 404 for unknown gateway."""
    response = mock_api_client.get("/gateways/998877665544/explanation?week=2026-02-02")
    assert response.status_code == 404
    data = response.json()
    assert data["error"] == "GatewayNotFound"


def test_get_gateway_explanation_invalid_id(mock_api_client: TestClient):
    """Verify GET /gateways/{id}/explanation returns 422 for malformed gateway ID."""
    response = mock_api_client.get("/gateways/short-id/explanation?week=2026-02-02")
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "InvalidInput"


def test_post_run_success(mock_api_client: TestClient):
    """Verify POST /run triggers ranking execution and returns summary."""
    response = mock_api_client.post("/run")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total_rows"] == 30
    assert data["weeks_count"] == 2
    assert "2026-02-02" in data["available_weeks"]


def test_uninitialized_ranking_returns_503(tmp_path: pathlib.Path):
    """Verify queries before running ranking return 503 Service Unavailable."""
    loader = DataLoader(data_dir=tmp_path)
    strategy = SyntheticMockStrategy()
    service = RankingService(strategy=strategy, loader=loader)  # Not run yet!
    set_ranking_service(service)

    client = TestClient(app)
    response = client.get("/rankings/2026-02-02")
    assert response.status_code == 503
    data = response.json()
    assert data["error"] == "RankingNotReady"
