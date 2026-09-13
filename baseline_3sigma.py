#!/usr/bin/env python3
"""A 3-sigma anomaly baseline. THIS SHIPS TO STUDENTS.

This is close to what our production notificator actually does, and it is the bar your
service is measured against. It is deliberately simple; it is not deliberately bad.

Method, for each Monday in the scored window:

  1. Take the trailing 28 days of telemetry for each gateway, strictly before that Monday.
  2. Per gateway, compute the mean and standard deviation of `offline_duration_sec`,
     `disconnection_cnt` and `reboot_cnt`.
  3. Flag any hour in the trailing 7 days where any of the three exceeds its own gateway's
     mean by more than three standard deviations.
  4. Rank gateways by flagged-hour count and take the top 15.

Usage:

    python baseline_3sigma.py --data path/to/data --out predictions.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib

import numpy as np
import pandas as pd

METRICS = ["offline_duration_sec", "disconnection_cnt", "reboot_cnt"]
SCORED_WEEKS = [dt.date(2026, 2, 2) + dt.timedelta(days=7 * i) for i in range(8)]
VISITS_PER_WEEK = 15
BASELINE_DAYS = 28
RECENT_DAYS = 7
SIGMA = 3.0


def load(data_dir: pathlib.Path) -> pd.DataFrame:
    frame = pd.read_parquet(
        data_dir / "telemetry", columns=["gateway_id", "ts_utc", *METRICS]
    )
    frame["ts"] = pd.to_datetime(frame["ts_utc"], utc=True)
    return frame.drop(columns=["ts_utc"])


def rank_week(frame: pd.DataFrame, monday: dt.date) -> pd.DataFrame:
    # `dt.timedelta` rather than `pd.Timedelta`: on pandas 2.2 with numpy 2.5 the pandas form
    # emits a NumPy deprecation warning about generic timedelta units. The code is correct
    # either way, but a script we hand over should not print warnings at a candidate on day one.
    end = pd.Timestamp(monday, tz="UTC")
    window = frame[(frame["ts"] >= end - dt.timedelta(days=BASELINE_DAYS)) & (frame["ts"] < end)]
    if window.empty:
        return pd.DataFrame(columns=["gateway_id", "flagged_hours", "worst_metric"])

    stats = window.groupby("gateway_id")[METRICS].agg(["mean", "std"])
    recent = window[window["ts"] >= end - dt.timedelta(days=RECENT_DAYS)].copy()

    flags = pd.Series(0, index=recent.index, dtype=int)
    worst = pd.Series("", index=recent.index, dtype=object)
    for metric in METRICS:
        mean = recent["gateway_id"].map(stats[(metric, "mean")])
        std = recent["gateway_id"].map(stats[(metric, "std")]).replace(0, np.nan)
        exceeded = (recent[metric] - mean) > SIGMA * std
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


def build_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for monday in SCORED_WEEKS:
        ranked = rank_week(frame, monday)
        # If fewer than fifteen gateways flag anything, fill the rest with the gateways that
        # were quietest about it — the cap has to be used in full either way.
        if len(ranked) < VISITS_PER_WEEK:
            raise SystemExit(f"only {len(ranked)} gateways have data before {monday}")
        for rank, row in enumerate(ranked.head(VISITS_PER_WEEK).itertuples(index=False), 1):
            metric = row.worst_metric or "no metric over 3 sigma"
            rows.append(
                {
                    "week_start": monday.isoformat(),
                    "rank": rank,
                    "gateway_id": row.gateway_id,
                    "score": float(row.flagged_hours),
                    "reason": (
                        f"{row.flagged_hours} hour(s) beyond 3 sigma of this gateway's own "
                        f"28-day baseline in the last 7 days; first breach on {metric}"
                    ),
                }
            )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    here = pathlib.Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    # Works both from this repository and from the copy shipped alongside the data.
    default_data = here / "data" if (here / "data").exists() else here.parent / "student-brief" / "data"
    parser.add_argument("--data", type=pathlib.Path, default=default_data)
    parser.add_argument("--out", type=pathlib.Path, default=here / "predictions_baseline.csv")
    args = parser.parse_args(argv)

    frame = load(args.data)
    predictions = build_predictions(frame)
    predictions.to_csv(args.out, index=False)
    print(f"wrote {args.out} — {len(predictions)} rows over {predictions.week_start.nunique()} weeks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
