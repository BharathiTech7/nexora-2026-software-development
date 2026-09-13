"""Abstract base class for ranking strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
import datetime as dt
import pandas as pd


class RankingStrategy(ABC):
    """Abstract interface defining the contract for gateway ranking strategies."""

    @abstractmethod
    def rank_week(self, frame: pd.DataFrame, monday: dt.date) -> pd.DataFrame:
        """Calculate gateway rankings and metrics for a single given Monday.

        Args:
            frame: Telemetry DataFrame containing gateway metrics with a 'ts' timestamp column.
            monday: Target Monday date for which the ranking is computed.

        Returns:
            DataFrame containing ranked gateways with scores and diagnostic fields.
        """
        pass

    @abstractmethod
    def build_predictions(
        self,
        frame: pd.DataFrame,
        weeks: list[dt.date] | None = None,
    ) -> pd.DataFrame:
        """Generate formatted predictions table matching the challenge schema for specified weeks.

        Args:
            frame: Telemetry DataFrame.
            weeks: List of Monday dates to score. If None, default scored window is used.

        Returns:
            DataFrame with columns: week_start, rank, gateway_id, score, reason.
        """
        pass
