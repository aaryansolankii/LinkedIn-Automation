"""Excel storage layer for LinkedIn content automation data."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import RLock

import pandas as pd

logger = logging.getLogger(__name__)

DB_FILE = Path(__file__).resolve().with_name("content_db.xlsx")
DB_COLUMNS = [
    "title",
    "hook",
    "about",
    "generation_date",
    "post_content",
    "approved",
    "when_to_post",
    "posted",
]
_LOCK = RLock()


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure required columns, order, and clean string values."""
    normalized = df.copy()
    for column in DB_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized = normalized[DB_COLUMNS]
    normalized = normalized.fillna("")
    return normalized.astype(str)


def initialize_excel() -> None:
    """Create the local Excel database file when it does not exist."""
    with _LOCK:
        if DB_FILE.exists():
            try:
                existing_df = pd.read_excel(
                    DB_FILE,
                    engine="openpyxl",
                    dtype=str,
                    keep_default_na=False,
                )
                normalized = _normalize_dataframe(existing_df)
                if normalized.columns.tolist() != existing_df.columns.tolist():
                    save_dataframe(normalized)
            except Exception:
                logger.exception("Excel file exists but could not be read: %s", DB_FILE)
                raise
            return

        logger.info("Creating Excel database: %s", DB_FILE)
        empty_df = pd.DataFrame(columns=DB_COLUMNS)
        save_dataframe(empty_df)


def get_all_rows() -> pd.DataFrame:
    """Load all rows from Excel as a pandas DataFrame."""
    with _LOCK:
        initialize_excel()
        try:
            df = pd.read_excel(
                DB_FILE,
                engine="openpyxl",
                dtype=str,
                keep_default_na=False,
            )
        except Exception:
            logger.exception("Failed to read Excel database file.")
            raise
        return _normalize_dataframe(df)


def save_dataframe(df: pd.DataFrame) -> None:
    """Persist a DataFrame to Excel safely."""
    with _LOCK:
        normalized = _normalize_dataframe(df)
        
        try:
            # Write directly to the file to avoid Windows PermissionError on os.replace
            normalized.to_excel(DB_FILE, index=False, engine="openpyxl")
        except Exception:
            logger.exception("Failed writing Excel database file.")
            raise


def append_row(title: str, hook: str, about: str, generation_date: str) -> None:
    """Append one content planning row with default workflow values."""
    with _LOCK:
        df = get_all_rows()
        new_row = {
            "title": str(title).strip(),
            "hook": str(hook).strip(),
            "about": str(about).strip(),
            "generation_date": str(generation_date).strip(),
            "post_content": "",
            "approved": "pending",
            "when_to_post": str(generation_date).strip(),
            "posted": "no",
        }
        updated_df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_dataframe(updated_df)
        logger.info("Appended idea row to Excel with title: %s", new_row["title"])


def update_cell(row_index: int, column_name: str, value: str) -> None:
    """Update one cell by 1-based row index and column name."""
    if row_index < 1:
        raise ValueError("row_index must be >= 1.")
    if column_name not in DB_COLUMNS:
        raise ValueError(f"Unsupported column name: {column_name}")

    with _LOCK:
        df = get_all_rows()
        zero_based_index = row_index - 1
        if zero_based_index >= len(df):
            raise IndexError(f"Row index {row_index} is out of range.")

        df.at[zero_based_index, column_name] = str(value)
        save_dataframe(df)
        logger.info("Updated Excel row %s column '%s'.", row_index, column_name)


initialize_excel()
