"""Codespec data loader.

Dataset format
--------------
A JSON file containing a list of items::

    [
        {"id": "task_001", "commit_id": "abc123def"},
        {"id": "task_002", "commit_id": "456xyz789"},
        ...
    ]

Each item must have at least:
- ``id``: unique identifier for the task
- ``commit_id``: the git commit hash to checkout in the repository
"""
from __future__ import annotations

import json
from pathlib import Path

from skillopt.datasets.base import SplitDataLoader


def _normalize_item(raw: dict) -> dict:
    """Normalize a raw dataset entry to a canonical codespec item."""
    return {
        "id": str(raw.get("id") or raw.get("task_id") or "").strip(),
        "commit_id": str(raw.get("commit_id") or raw.get("commit") or raw.get("branch") or "").strip(),
        "category": str(raw.get("category") or raw.get("type") or "codespec").strip() or "codespec",
        "task_type": str(raw.get("task_type") or raw.get("category") or "codespec").strip() or "codespec",
    }


class CodespecDataLoader(SplitDataLoader):
    """Data loader for the Codespec benchmark.

    Loads a JSON array of ``{id, commit_id}`` entries and splits them
    according to ``split_mode`` (ratio or split_dir).
    """

    def load_split_items(self, split_path: str) -> list[dict]:
        """Load items from one split directory.

        Looks for the first ``.json`` file under *split_path* and
        normalizes each entry via :func:`_normalize_item`.
        """
        path = Path(split_path)
        json_files = sorted(path.glob("*.json"))
        if not json_files:
            raise FileNotFoundError(f"No .json file found in {split_path}")

        with json_files[0].open(encoding="utf-8") as f:
            raw_items = json.load(f)

        if not isinstance(raw_items, list):
            raise ValueError(
                f"Expected JSON array in {json_files[0]}, got {type(raw_items).__name__}"
            )

        return [_normalize_item(item) for item in raw_items]
