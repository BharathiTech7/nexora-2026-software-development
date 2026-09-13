"""Business service layer coordinating data loading and ranking strategies."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Sequence
import pandas as pd

from src.data.loader import DataLoader
from src.ranking.base import RankingStrategy


class ServiceError(Exception):
    """Base domain exception for service layer operations."""
    pass


class RankingNotRunError(ServiceError):
    """Raised when ranking retrieval is attempted before running predictions."""
    pass


class WeekNotFoundError(ServiceError):
    """Raised when the requested week is not present in available ranking results."""
    pass


class GatewayNotFoundError(ServiceError):
    """Raised when the requested gateway is not found in ranking results for the week."""
    pass


class InvalidInputError(ServiceError):
    """Raised when input parameters (e.g. date format, gateway id) are invalid."""
    pass


_BARE_HEX = re.compile(r"^[0-9A-Fa-f]{12}$")
_COLON_HEX = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def normalize_gateway_id(val: str) -> str:
    """Normalize gateway ID format to uppercase bare 12 hex string.

    Accepts 12-char hex (e.g. '001122334455') or colon-separated MAC ('00:11:22:33:44:55').
    """
    cleaned = str(val).strip()
    if _BARE_HEX.match(cleaned):
        return cleaned.upper()
    if _COLON_HEX.match(cleaned):
        return cleaned.replace(":", "").upper()
    raise InvalidInputError(
        f"Invalid gateway_id format: '{val}'. Must be 12 hex characters or colon-separated."
    )


def parse_week_date(val: str | dt.date | dt.datetime) -> dt.date:
    """Parse and validate date/string into a date object."""
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    if isinstance(val, str):
        cleaned = val.strip()
        try:
            return dt.date.fromisoformat(cleaned)
        except ValueError as err:
            raise InvalidInputError(
                f"Invalid week date format: '{val}'. Expected YYYY-MM-DD."
            ) from err
    raise InvalidInputError(f"Unsupported week type: {type(val).__name__}")


class RankingService:
    """Coordinates data loading, ranking strategy execution, and result retrieval."""

    def __init__(
        self,
        strategy: RankingStrategy,
        loader: DataLoader | None = None,
    ) -> None:
        self.strategy = strategy
        self.loader = loader or DataLoader()
        self._predictions: pd.DataFrame | None = None

    @property
    def is_ready(self) -> bool:
        """Indicates whether ranking predictions have been computed and are available."""
        return self._predictions is not None and not self._predictions.empty

    def run(
        self,
        force_reload_data: bool = True,
        weeks: Sequence[dt.date] | None = None,
    ) -> pd.DataFrame:
        """Load telemetry data, execute the ranking strategy, and cache results.

        Args:
            force_reload_data: If True, forces data loader to re-read parquet data from disk.
            weeks: Specific weeks to score. If None, strategy defaults are used.

        Returns:
            DataFrame of generated predictions.
        """
        telemetry_df = self.loader.load_telemetry(force_reload=force_reload_data)
        target_weeks = list(weeks) if weeks is not None else None
        predictions = self.strategy.build_predictions(telemetry_df, weeks=target_weeks)

        # Ensure normalized gateway_id column for indexed lookup
        predictions_copy = predictions.copy()
        predictions_copy["norm_gateway_id"] = predictions_copy["gateway_id"].apply(
            lambda x: normalize_gateway_id(str(x))
        )
        self._predictions = predictions_copy
        return predictions

    def get_available_weeks(self) -> list[str]:
        """Return sorted list of available week dates in ISO format."""
        if self._predictions is None:
            raise RankingNotRunError("Ranking has not been run yet. Please run ranking first.")
        return sorted(self._predictions["week_start"].unique().tolist())

    def get_rankings_for_week(self, week: str | dt.date) -> list[dict[str, Any]]:
        """Retrieve top ranked gateways for a given week.

        Args:
            week: Week date (e.g. '2026-03-23' or dt.date(2026, 3, 23)).

        Returns:
            List of ranking dictionaries sorted by rank.

        Raises:
            RankingNotRunError: If ranking has not been run.
            WeekNotFoundError: If requested week is not in available predictions.
            InvalidInputError: If date format is malformed.
        """
        if self._predictions is None:
            raise RankingNotRunError("Ranking has not been run yet. Please execute /run first.")

        week_date = parse_week_date(week)
        week_str = week_date.isoformat()

        available_weeks = self.get_available_weeks()
        if week_str not in available_weeks:
            raise WeekNotFoundError(
                f"No rankings found for week '{week_str}'. Available weeks: {', '.join(available_weeks)}"
            )

        week_rows = self._predictions[self._predictions["week_start"] == week_str].sort_values("rank")
        results: list[dict[str, Any]] = []
        for row in week_rows.itertuples(index=False):
            results.append(
                {
                    "week": row.week_start,
                    "rank": int(row.rank),
                    "gateway_id": row.gateway_id,
                    "score": float(row.score),
                    "reason": str(row.reason),
                }
            )
        return results

    def get_gateway_explanation(
        self,
        gateway_id: str,
        week: str | dt.date,
    ) -> dict[str, Any]:
        """Retrieve explanation and ranking details for a specific gateway and week.

        Args:
            gateway_id: Gateway identifier (12 hex chars or colon-separated).
            week: Week date string or date object.

        Returns:
            Dictionary containing gateway_id, week, rank, score, and reason.

        Raises:
            RankingNotRunError: If ranking has not been run.
            InvalidInputError: If gateway_id or week format is invalid.
            WeekNotFoundError: If week is not available.
            GatewayNotFoundError: If gateway is not found in rankings for the week.
        """
        if self._predictions is None:
            raise RankingNotRunError("Ranking has not been run yet. Please execute /run first.")

        norm_gw = normalize_gateway_id(gateway_id)
        week_date = parse_week_date(week)
        week_str = week_date.isoformat()

        available_weeks = self.get_available_weeks()
        if week_str not in available_weeks:
            raise WeekNotFoundError(
                f"No rankings found for week '{week_str}'. Available weeks: {', '.join(available_weeks)}"
            )

        matched = self._predictions[
            (self._predictions["week_start"] == week_str)
            & (self._predictions["norm_gateway_id"] == norm_gw)
        ]

        if matched.empty:
            raise GatewayNotFoundError(
                f"Gateway '{gateway_id}' is not in the ranked results for week {week_str}."
            )

        row = matched.iloc[0]
        return {
            "gateway_id": row["gateway_id"],
            "week": row["week_start"],
            "rank": int(row["rank"]),
            "score": float(row["score"]),
            "reason": str(row["reason"]),
        }
