"""Codespec evaluator — LLM-based feature precision/recall scoring.

Ground truth format
-------------------
A JSON file (array) where each entry corresponds to one dataset item::

    [
        {
            "id": "task_001",
            "category": "feature_type",
            "gt_code": "optional ground truth code snippet",
            "feature": "Description of a specific feature that should be in design.md"
        },
        ...
    ]

Multiple entries may share the same ``id`` — each row is one feature.

Evaluation flow
---------------
1. Load ground truth features for the current item id.
2. Parse the generated ``design.md`` to extract proposed features.
3. Ask the LLM to judge each ground-truth feature against the design
   (recall) and each design feature against ground truth (precision).
4. Return precision, recall, and F1.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ── Ground-truth loading ────────────────────────────────────────────────────


def load_ground_truth(gt_path: str) -> dict[str, list[dict]]:
    """Load ground truth JSON and group features by item id.

    Returns
    -------
    dict[str, list[dict]]
        Mapping from item id to a list of feature records.
    """
    path = Path(gt_path)
    if not path.exists():
        raise FileNotFoundError(f"Ground truth file not found: {gt_path}")

    with path.open(encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON array in {gt_path}, got {type(raw).__name__}")

    grouped: dict[str, list[dict]] = {}
    for entry in raw:
        item_id = str(entry.get("id", "")).strip()
        if not item_id:
            continue
        grouped.setdefault(item_id, []).append({
            "category": str(entry.get("category", "")).strip(),
            "gt_code": str(entry.get("gt_code", "")).strip(),
            "feature": str(entry.get("feature", "")).strip(),
        })
    return grouped


# ── Design.md feature extraction ────────────────────────────────────────────


def _extract_design_features(design_text: str) -> list[str]:
    """Heuristically extract feature descriptions from design.md.

    Looks for markdown headings, bullet points, or numbered lists that
    describe individual features.
    """
    features: list[str] = []
    current = ""
    for line in design_text.splitlines():
        stripped = line.strip()
        # New heading → flush current feature
        if re.match(r"^#{1,6}\s", stripped):
            if current.strip():
                features.append(current.strip())
            current = stripped
        # Bullet or numbered list item
        elif re.match(r"^[-*]\s", stripped) or re.match(r"^\d+\.\s", stripped):
            if current.strip():
                features.append(current.strip())
            current = stripped
        else:
            current += "\n" + stripped

    if current.strip():
        features.append(current.strip())

    return features if features else [design_text.strip()]


# ── LLM-based feature matching ──────────────────────────────────────────────

_RECALL_SYSTEM = """You are a precise technical evaluator. Your job is to determine whether specific software features described in a ground-truth specification are adequately covered in a generated design document.

For each ground-truth feature, output a JSON object with:
- "feature_index": the 0-based index
- "covered": true or false
- "reason": a brief explanation (1 sentence)

Output ONLY a JSON array of results, no other text."""

_RECALL_USER_TEMPLATE = """## Ground Truth Features
{gt_features}

## Generated Design Document
{design_text}

---

Evaluate whether EACH ground-truth feature (by index) is covered in the design document.
A feature is "covered" if the design document describes the same functionality or behavior, even if worded differently.
Output a JSON array of {{"feature_index": int, "covered": bool, "reason": str}}."""

_PRECISION_SYSTEM = """You are a precise technical evaluator. Your job is to determine whether features proposed in a generated design document correspond to real features in the ground-truth specification.

For each design feature, output a JSON object with:
- "feature_index": the 0-based index
- "valid": true or false
- "reason": a brief explanation (1 sentence)

Output ONLY a JSON array of results, no other text."""

_PRECISION_USER_TEMPLATE = """## Design Document Features
{design_features}

## Ground Truth Specification
{gt_features}

---

Evaluate whether EACH design feature (by index) corresponds to a real feature in the ground truth.
A design feature is "valid" if it matches or is a reasonable sub-component of a ground-truth feature.
Output a JSON array of {{"feature_index": int, "valid": bool, "reason": str}}."""


def _parse_json_array(text: str) -> list[dict]:
    """Best-effort extraction of a JSON array from LLM output."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Find the first [...] block
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return []


def _evaluate_recall(
    gt_features: list[str],
    design_text: str,
    chat_fn,
    max_completion_tokens: int = 4096,
) -> tuple[int, int]:
    """Return (covered_count, total_gt_count)."""
    if not gt_features:
        return 0, 0

    gt_list = "\n".join(f"{i}. {feat}" for i, feat in enumerate(gt_features))
    user = _RECALL_USER_TEMPLATE.format(gt_features=gt_list, design_text=design_text)

    resp, _ = chat_fn(
        system=_RECALL_SYSTEM,
        user=user,
        max_completion_tokens=max_completion_tokens,
        retries=3,
        stage="eval_recall",
    )

    results = _parse_json_array(resp)
    covered = 0
    for item in results:
        if isinstance(item, dict) and item.get("covered", False):
            covered += 1

    # Fallback: if LLM parsing failed entirely, score 0
    return covered, len(gt_features)


def _evaluate_precision(
    design_features: list[str],
    gt_features_text: str,
    chat_fn,
    max_completion_tokens: int = 4096,
) -> tuple[int, int]:
    """Return (valid_count, total_design_count)."""
    if not design_features:
        return 0, 0

    feat_list = "\n".join(f"{i}. {feat}" for i, feat in enumerate(design_features))
    user = _PRECISION_USER_TEMPLATE.format(
        design_features=feat_list, gt_features=gt_features_text
    )

    resp, _ = chat_fn(
        system=_PRECISION_SYSTEM,
        user=user,
        max_completion_tokens=max_completion_tokens,
        retries=3,
        stage="eval_precision",
    )

    results = _parse_json_array(resp)
    valid = 0
    for item in results:
        if isinstance(item, dict) and item.get("valid", False):
            valid += 1

    return valid, len(design_features)


# ── Public API ───────────────────────────────────────────────────────────────


def evaluate(
    design_text: str,
    gt_entries: list[dict],
    chat_fn=None,
    max_completion_tokens: int = 4096,
) -> dict[str, Any]:
    """Evaluate a generated design.md against ground truth features.

    Parameters
    ----------
    design_text : str
        Full text of the generated ``design.md``.
    gt_entries : list[dict]
        Ground truth feature records for this item (from :func:`load_ground_truth`).
    chat_fn : callable
        LLM call function with signature
        ``(system, user, max_completion_tokens, retries, stage) -> (text, tokens)``.
        Typically ``skillopt.model.chat_optimizer``.
    max_completion_tokens : int
        Token budget for each LLM judge call.

    Returns
    -------
    dict
        ``{precision, recall, f1, hard, soft, gt_feature_count,
          design_feature_count, covered_count, valid_count}``
    """
    gt_feature_texts = [e["feature"] for e in gt_entries if e.get("feature")]
    gt_full = "\n".join(f"- {f}" for f in gt_feature_texts)

    design_features = _extract_design_features(design_text)

    # Default to zero scores if no LLM available
    if chat_fn is None or not gt_feature_texts:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "hard": 0,
            "soft": 0.0,
            "gt_feature_count": len(gt_feature_texts),
            "design_feature_count": len(design_features),
            "covered_count": 0,
            "valid_count": 0,
        }

    covered, total_gt = _evaluate_recall(
        gt_feature_texts, design_text, chat_fn, max_completion_tokens
    )
    valid, total_design = _evaluate_precision(
        design_features, gt_full, chat_fn, max_completion_tokens
    )

    recall = covered / total_gt if total_gt > 0 else 0.0
    precision = valid / total_design if total_design > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "hard": 1 if f1 >= 0.5 else 0,
        "soft": round(f1, 4),
        "gt_feature_count": total_gt,
        "design_feature_count": total_design,
        "covered_count": covered,
        "valid_count": valid,
    }
