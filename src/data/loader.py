"""Telemetry data loading and validation module."""

from __future__ import annotations

import pathlib
from typing import Sequence
import pandas as pd


class DataError(Exception):
    """Base exception for data loading and validation errors."""
    pass


class DataNotFoundError(DataError):
    """Raised when the specified data directory or telemetry files do not exist."""
    pass


class DataValidationError(DataError):
    """Raised when loaded data fails schema or integrity validation."""
    pass


DEFAULT_REQUIRED_COLUMNS = (
    "gateway_id",
    "ts_utc",
    "offline_duration_sec",
    "disconnection_cnt",
    "reboot_cnt",
)


def get_default_data_dir() -> pathlib.Path:
    """Resolve default data directory relative to repository root."""
    # Assuming loader is in src/data/, repo root is two levels up
    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    return repo_root / "data"


class DataLoader:
    """Loads and validates gateway telemetry datasets with in-memory caching support."""

    def __init__(
        self,
        data_dir: pathlib.Path | str | None = None,
        required_columns: Sequence[str] = DEFAULT_REQUIRED_COLUMNS,
    ) -> None:
        if data_dir is None:
            self.data_dir = get_default_data_dir()
        else:
            self.data_dir = pathlib.Path(data_dir)
        self.required_columns = list(required_columns)
        self._cached_telemetry: pd.DataFrame | None = None

    def validate_source(self) -> pathlib.Path:
        """Validate that the telemetry data source exists.

        Returns:
            Resolved Path to telemetry directory or file.

        Raises:
            DataNotFoundError: If data directory or telemetry dataset is missing.
        """
        if not self.data_dir.exists():
            raise DataNotFoundError(f"Data directory does not exist: {self.data_dir}")

        telemetry_path = self.data_dir / "telemetry"
        if not telemetry_path.exists():
            # If telemetry subfolder not present, check if data_dir itself is the parquet folder/file
            if self.data_dir.is_file() or any(self.data_dir.glob("*.parquet")):
                return self.data_dir
            raise DataNotFoundError(
                f"Telemetry dataset not found at expected path: {telemetry_path}"
            )

        return telemetry_path

    def load_telemetry(self, force_reload: bool = False) -> pd.DataFrame:
        """Load, validate, and parse telemetry data.

        Args:
            force_reload: If True, bypasses cache and reloads from disk.
                          The cache is cleared at the start of a force-reload attempt so
                          that a failed reload never leaves stale data silently accessible
                          via a subsequent non-forced call.

        Returns:
            Validated telemetry DataFrame with UTC timestamp column 'ts'.

        Raises:
            DataNotFoundError: When directory/files are missing.
            DataValidationError: When required columns are missing or malformed.
        """
        if self._cached_telemetry is not None and not force_reload:
            return self._cached_telemetry

        # Clear the cache BEFORE attempting to reload so that if the load
        # fails, the stale cache is not silently returned on the next call.
        if force_reload:
            self._cached_telemetry = None

        telemetry_source = self.validate_source()

        try:
            # Read telemetry schema / parquet
            frame = pd.read_parquet(telemetry_source)
        except Exception as err:
            raise DataValidationError(
                f"Failed to read parquet telemetry data from {telemetry_source}: {err}"
            ) from err

        # Schema validation
        missing_cols = [col for col in self.required_columns if col not in frame.columns]
        if missing_cols:
            raise DataValidationError(
                f"Telemetry data missing required column(s): {', '.join(missing_cols)}"
            )

        if frame.empty:
            raise DataValidationError("Telemetry dataset is empty.")

        # Timestamp normalization (matching baseline behavior)
        try:
            frame["ts"] = pd.to_datetime(frame["ts_utc"], utc=True)
        except Exception as err:
            raise DataValidationError(
                f"Failed to parse 'ts_utc' column to datetime: {err}"
            ) from err

        # Filter down to required metric columns + gateway_id + ts
        metric_cols = [c for c in self.required_columns if c not in ("gateway_id", "ts_utc")]
        clean_frame = frame[["gateway_id", "ts", *metric_cols]].copy()

        self._cached_telemetry = clean_frame
        return clean_frame

    def clear_cache(self) -> None:
        """Clear cached telemetry in memory."""
        self._cached_telemetry = None
