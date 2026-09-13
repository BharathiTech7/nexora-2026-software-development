"""FastAPI application for NEXORA 2026 gateway ranking service."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.data.loader import (
    DataLoader,
    DataNotFoundError,
    DataValidationError,
)
from src.ranking.baseline import Baseline3SigmaRanking
from src.services.ranking_service import (
    GatewayNotFoundError,
    InvalidInputError,
    RankingNotRunError,
    RankingService,
    WeekNotFoundError,
)

logger = logging.getLogger("nexora.api")

# =====================================================================
# Pydantic Response Models
# =====================================================================


class RankingItem(BaseModel):
    """Individual ranked gateway record."""

    week: str = Field(
        ...,
        description="Target week start in YYYY-MM-DD format",
        json_schema_extra={"example": "2026-03-23"},
    )
    rank: int = Field(
        ...,
        description="Gateway priority rank for field visit (1-15)",
        json_schema_extra={"example": 1},
    )
    gateway_id: str = Field(
        ...,
        description="Unique gateway identifier",
        json_schema_extra={"example": "001122334455"},
    )
    score: float = Field(
        ...,
        description="Anomaly score (flagged hours count)",
        json_schema_extra={"example": 18.0},
    )
    reason: str = Field(
        ...,
        description="Concise operational reason explaining the ranking score",
        json_schema_extra={
            "example": "18 hour(s) beyond 3 sigma of this gateway's own 28-day baseline in the last 7 days; first breach on reboot_cnt"
        },
    )


class RankingsResponse(BaseModel):
    """Response containing top ranked gateways for a requested week."""

    week: str = Field(
        ...,
        description="Week date in YYYY-MM-DD format",
        json_schema_extra={"example": "2026-03-23"},
    )
    total_ranked: int = Field(
        ...,
        description="Number of ranked gateways returned",
        json_schema_extra={"example": 15},
    )
    gateways: list[RankingItem] = Field(
        ...,
        description="List of top ranked gateways",
    )


class GatewayExplanationResponse(BaseModel):
    """Explanation and diagnostic details for a specific gateway."""

    gateway_id: str = Field(
        ...,
        description="Gateway identifier",
        json_schema_extra={"example": "001122334455"},
    )
    week: str = Field(
        ...,
        description="Week date in YYYY-MM-DD format",
        json_schema_extra={"example": "2026-03-23"},
    )
    rank: int = Field(
        ...,
        description="Gateway priority rank for field visit (1-15)",
        json_schema_extra={"example": 1},
    )
    score: float = Field(
        ...,
        description="Anomaly score (flagged hours count)",
        json_schema_extra={"example": 18.0},
    )
    reason: str = Field(
        ...,
        description="Operational reason explaining the rank and score",
    )


class RunResponse(BaseModel):
    """Response payload returned when ranking calculation is triggered."""

    status: str = Field(
        ...,
        description="Execution status",
        json_schema_extra={"example": "success"},
    )
    message: str = Field(
        ...,
        description="Informative status message",
        json_schema_extra={"example": "Ranking computation completed successfully."},
    )
    total_rows: int = Field(
        ...,
        description="Total prediction rows generated across all weeks",
        json_schema_extra={"example": 120},
    )
    weeks_count: int = Field(
        ...,
        description="Total number of scored weeks",
        json_schema_extra={"example": 8},
    )
    available_weeks: list[str] = Field(
        ...,
        description="List of available scored weeks",
        json_schema_extra={"example": ["2026-02-02", "2026-03-23"]},
    )


class ErrorResponse(BaseModel):
    """Standardized error response payload."""

    error: str = Field(..., description="Error category name")
    detail: str = Field(..., description="Human-readable error details")


class HealthResponse(BaseModel):
    """Application health and readiness status."""

    status: str = Field(
        ...,
        description="Service health status",
        json_schema_extra={"example": "healthy"},
    )
    is_ranking_ready: bool = Field(
        ...,
        description="Whether rankings have been computed and are ready for queries",
    )
    available_weeks: list[str] = Field(
        default_factory=list,
        description="Currently available weeks",
    )


# =====================================================================
# Application Lifecycle & Service Singleton
# =====================================================================

_ranking_service: RankingService | None = None


def get_ranking_service() -> RankingService:
    """Dependency provider for RankingService."""
    global _ranking_service
    if _ranking_service is None:
        loader = DataLoader()
        strategy = Baseline3SigmaRanking()
        _ranking_service = RankingService(strategy=strategy, loader=loader)
    return _ranking_service


def set_ranking_service(service: RankingService) -> None:
    """Setter for RankingService instance (useful for testing)."""
    global _ranking_service
    _ranking_service = service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager attempting initial ranking computation at startup."""
    service = get_ranking_service()
    try:
        service.run(force_reload_data=False)
        logger.info("Startup: Initial ranking computed successfully.")
    except Exception as exc:
        logger.warning(
            "Startup: Telemetry data not yet loaded (%s). Rankings can be generated via POST /run.",
            exc,
        )
    yield


# =====================================================================
# FastAPI App Initialization
# =====================================================================

app = FastAPI(
    title="NEXORA 2026 - Gateway Anomaly Ranking API",
    description=(
        "Production-style Web API for ranking the top 15 gateways needing weekly field visits "
        "and providing actionable explanations for operations teams."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# =====================================================================
# Domain Exception Handlers (Translates Domain Errors -> HTTP Responses)
# =====================================================================


@app.exception_handler(RankingNotRunError)
async def ranking_not_run_handler(request: Request, exc: RankingNotRunError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": "RankingNotReady", "detail": str(exc)},
    )


@app.exception_handler(WeekNotFoundError)
async def week_not_found_handler(request: Request, exc: WeekNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "WeekNotFound", "detail": str(exc)},
    )


@app.exception_handler(GatewayNotFoundError)
async def gateway_not_found_handler(request: Request, exc: GatewayNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "GatewayNotFound", "detail": str(exc)},
    )


@app.exception_handler(InvalidInputError)
async def invalid_input_handler(request: Request, exc: InvalidInputError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "InvalidInput", "detail": str(exc)},
    )


@app.exception_handler(DataNotFoundError)
async def data_not_found_handler(request: Request, exc: DataNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": "DataNotFound", "detail": str(exc)},
    )


@app.exception_handler(DataValidationError)
async def data_validation_handler(request: Request, exc: DataValidationError):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "DataValidationError", "detail": str(exc)},
    )


# =====================================================================
# API Endpoints
# =====================================================================


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health and Readiness Check",
    tags=["Health"],
)
async def health_check(
    service: RankingService = Depends(get_ranking_service),
) -> HealthResponse:
    """Return current service health and readiness status."""
    available = service.get_available_weeks() if service.is_ready else []
    return HealthResponse(
        status="healthy",
        is_ranking_ready=service.is_ready,
        available_weeks=available,
    )


@app.get(
    "/rankings/{week}",
    response_model=RankingsResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Week not found in available rankings"},
        422: {"model": ErrorResponse, "description": "Malformed week date format"},
        503: {"model": ErrorResponse, "description": "Ranking computation not ready"},
    },
    summary="Get Weekly Top 15 Ranked Gateways",
    tags=["Rankings"],
)
async def get_weekly_rankings(
    week: str,
    service: RankingService = Depends(get_ranking_service),
) -> RankingsResponse:
    """Retrieve the top 15 gateways prioritized for field visits in the specified week.

    - **week**: Target Monday date in ISO format `YYYY-MM-DD` (e.g. `2026-03-23`).
    """
    gateways_data = service.get_rankings_for_week(week)
    items = [RankingItem(**gw) for gw in gateways_data]
    return RankingsResponse(
        week=week,
        total_ranked=len(items),
        gateways=items,
    )


@app.get(
    "/gateways/{gateway_id}/explanation",
    response_model=GatewayExplanationResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Gateway or week not found"},
        422: {"model": ErrorResponse, "description": "Malformed gateway ID or week date"},
        503: {"model": ErrorResponse, "description": "Ranking computation not ready"},
    },
    summary="Get Gateway Ranking Explanation",
    tags=["Rankings"],
)
async def get_gateway_explanation(
    gateway_id: str,
    week: str = Query(..., description="Target week date in YYYY-MM-DD format", examples=["2026-03-23"]),
    service: RankingService = Depends(get_ranking_service),
) -> GatewayExplanationResponse:
    """Retrieve explanation and anomaly diagnostic details for a specific gateway.

    - **gateway_id**: 12-digit hex gateway identifier (e.g. `001122334455` or `00:11:22:33:44:55`).
    - **week**: Target Monday date in `YYYY-MM-DD` format.
    """
    explanation_data = service.get_gateway_explanation(gateway_id=gateway_id, week=week)
    return GatewayExplanationResponse(**explanation_data)


@app.post(
    "/run",
    response_model=RunResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Data validation or computation error"},
        503: {"model": ErrorResponse, "description": "Telemetry source data not found"},
    },
    summary="Trigger Ranking Run",
    tags=["Operations"],
)
async def trigger_ranking_run(
    service: RankingService = Depends(get_ranking_service),
) -> RunResponse:
    """Execute or refresh the gateway ranking process using current telemetry data.

    Reloads the mounted telemetry parquet dataset, computes anomaly rankings across all scored weeks,
    and caches the results in memory for immediate API querying.
    """
    predictions_df = service.run(force_reload_data=True)
    available_weeks = service.get_available_weeks()
    return RunResponse(
        status="success",
        message="Ranking computation completed successfully.",
        total_rows=len(predictions_df),
        weeks_count=len(available_weeks),
        available_weeks=available_weeks,
    )
