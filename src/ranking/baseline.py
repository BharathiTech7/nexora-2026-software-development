"""Baseline 3-Sigma anomaly ranking implementation."""

from __future__ import annotations

import datetime as dt
from typing import Sequence

import numpy as np
import pandas as pd

from src.ranking.base import RankingStrategy

DEFAULT_METRICS = ("offline_duration_sec", "disconnection_cnt", "reboot_cnt")
DEFAULT_SCORED_WEEKS = tuple(dt.date(2026, 2, 2) + dt.timedelta(days=7 * i) for i in range(8))
DEFAULT_VISITS_PER_WEEK = 15
DEFAULT_BASELINE_DAYS = 28
DEFAULT_RECENT_DAYS = 7
DEFAULT_SIGMA = 3.0


class Baseline3SigmaRanking(RankingStrategy):
    """3-sigma anomaly ranking strategy adhering strictly to the baseline methodology.

    Methodology:
      1. Uses trailing `baseline_days` (default: 28) strictly prior to Monday for gateway baseline stats.
      2. Computes per-gateway mean and standard deviation across configured metrics.
      3. Examines trailing `recent_days` (default: 7) and flags hours exceeding mean by > `sigma` std devs.
      4. Aggregates flagged hours per gateway, sorts descending, and returns top `visits_per_week` (default: 15).
    """

    def __init__(
        self,
        metrics: Sequence[str] = DEFAULT_METRICS,
        baseline_days: int = DEFAULT_BASELINE_DAYS,
        recent_days: int = DEFAULT_RECENT_DAYS,
        sigma: float = DEFAULT_SIGMA,
        visits_per_week: int = DEFAULT_VISITS_PER_WEEK,
        scored_weeks: Sequence[dt.date] = DEFAULT_SCORED_WEEKS,
    ) -> None:
        self.metrics = list(metrics)
        self.baseline_days = baseline_days
        self.recent_days = recent_days
        self.sigma = sigma
        self.visits_per_week = visits_per_week
        self.scored_weeks = list(scored_weeks)

    def rank_week(self, frame: pd.DataFrame, monday: dt.date) -> pd.DataFrame:
        """Compute gateway ranking and diagnostics for a specific Monday."""
        end = pd.Timestamp(monday, tz="UTC")
        window = frame[
            (frame["ts"] >= end - dt.timedelta(days=self.baseline_days)) & (frame["ts"] < end)
        ]
        if window.empty:
            return pd.DataFrame(columns=["gateway_id", "flagged_hours", "worst_metric"])

        stats = window.groupby("gateway_id")[self.metrics].agg(["mean", "std"])
        recent = window[window["ts"] >= end - dt.timedelta(days=self.recent_days)].copy()

        flags = pd.Series(0, index=recent.index, dtype=int)
        worst = pd.Series("", index=recent.index, dtype=object)

        for metric in self.metrics:
            mean = recent["gateway_id"].map(stats[(metric, "mean")])
            std = recent["gateway_id"].map(stats[(metric, "std")]).replace(0, np.nan)
            exceeded = (recent[metric] - mean) > self.sigma * std
            exceeded = exceeded.fillna(False)
            flags = flags + exceeded.astype(int)
            worst = worst.where(~exceeded | (worst != ""), metric)

        recent["flagged"] = flags
        recent["worst_metric"] = worst
        grouped = recent.groupby("gateway_id").agg(
            flagged_hours=("flagged", "sum"),
            worst_metric=("worst_metric", lambda s: next((v for v in s if v), "")),
        )
        return grouped.sort_values("flagged_hours", ascending=False).reset_index()

    def build_predictions(
        self,
        frame: pd.DataFrame,
        weeks: list[dt.date] | None = None,
    ) -> pd.DataFrame:
        """Build the full predictions DataFrame formatted for submission."""
        target_weeks = weeks if weeks is not None else self.scored_weeks
        rows = []

        for monday in target_weeks:
            ranked = self.rank_week(frame, monday)
            if len(ranked) < self.visits_per_week:
                raise ValueError(f"only {len(ranked)} gateways have data before {monday}")

            top_gateways = ranked.head(self.visits_per_week)
            for rank, row in enumerate(top_gateways.itertuples(index=False), 1):
                metric = row.worst_metric or "no metric over 3 sigma"
                reason_text = (
                    f"{row.flagged_hours} hour(s) beyond 3 sigma of this gateway's own "
                    f"{self.baseline_days}-day baseline in the last {self.recent_days} days; first breach on {metric}"
                )
                rows.append(
                    {
                        "week_start": monday.isoformat(),
                        "rank": rank,
                        "gateway_id": row.gateway_id,
                        "score": float(row.flagged_hours),
                        "reason": reason_text,
                    }
                )
        return pd.DataFrame(rows)
