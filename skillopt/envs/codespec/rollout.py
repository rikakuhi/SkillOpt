"""Codespec rollout — repo setup, codespec execution, and evaluation.

Workflow per item
-----------------
1. Copy the source repo to a working directory.
2. ``git checkout <commit_id>`` to the task-specific branch/commit.
3. Read ``final.md`` from the working copy (this is the requirement).
4. Invoke ``opencode run "/codespec/plan <requirement>"`` via subprocess.
5. Collect the generated ``task.md``, ``design.md``, ``spec.md``.
6. Evaluate ``design.md`` against ground truth features via LLM judge.

Public API
----------
- :func:`run_single` — process one item
- :func:`run_batch`  — parallel execution of a list of items
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from skillopt.envs.codespec.evaluator import evaluate, load_ground_truth


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_read(path: str) -> str:
    """Read a file, returning empty string if it doesn't exist."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def _prepare_work_repo(repo_path: str, work_dir: str, item_id: str) -> str:
    """Copy *repo_path* into *work_dir*/*item_id* and return the copy path.

    If the target already exists, it is removed first to ensure a clean state.
    """
    target = os.path.join(work_dir, item_id)
    if os.path.exists(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(repo_path, target)
    return target


def _git_checkout(repo_dir: str, commit_id: str) -> None:
    """Run ``git checkout <commit_id>`` inside *repo_dir*."""
    result = subprocess.run(
        ["git", "checkout", commit_id],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git checkout {commit_id!r} failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def _run_codespec(
    repo_dir: str,
    requirement: str,
    opencode_exec: str = "opencode",
    timeout: int = 600,
) -> tuple[str, int]:
    """Invoke ``opencode run`` with the codespec slash command.

    Returns (stdout_text, return_code).
    """
    # Build the command: opencode run "/codespec/plan <requirement>"
    prompt = f"/codespec/plan {requirement}"
    cmd = [opencode_exec, "run", prompt]

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        return f"TIMEOUT after {timeout}s: {exc}", -1
    except FileNotFoundError:
        return f"opencode executable not found: {opencode_exec!r}", -2


def _collect_outputs(repo_dir: str) -> dict[str, str]:
    """Read the three expected output files from the repo directory."""
    outputs: dict[str, str] = {}
    for fname in ("task.md", "design.md", "spec.md"):
        fpath = os.path.join(repo_dir, fname)
        outputs[fname] = _safe_read(fpath)
    return outputs


# ── Single-item rollout ─────────────────────────────────────────────────────


def run_single(
    item: dict,
    *,
    repo_path: str,
    gt_path: str,
    work_dir: str,
    opencode_exec: str = "opencode",
    opencode_timeout: int = 600,
    chat_fn=None,
    eval_max_tokens: int = 4096,
    out_root: str = "",
) -> dict[str, Any]:
    """Process one codespec item end-to-end.

    Returns a result dict with keys: ``id``, ``hard``, ``soft``,
    ``precision``, ``recall``, ``f1``, and diagnostic fields.
    """
    item_id = str(item.get("id", "unknown"))
    commit_id = str(item.get("commit_id", ""))
    result: dict[str, Any] = {
        "id": item_id,
        "hard": 0,
        "soft": 0.0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "n_turns": 1,
        "agent_ok": False,
    }

    # Per-item output directory
    pred_dir = ""
    if out_root:
        pred_dir = os.path.join(out_root, "predictions", item_id)
        os.makedirs(pred_dir, exist_ok=True)

    try:
        # Step 1: Prepare working copy
        work_repo = _prepare_work_repo(repo_path, work_dir, item_id)

        # Step 2: Checkout specific commit/branch
        if commit_id:
            _git_checkout(work_repo, commit_id)

        # Step 3: Read requirement from final.md
        requirement = _safe_read(os.path.join(work_repo, "final.md"))
        if not requirement.strip():
            result["fail_reason"] = "final.md is empty or not found in repo"
            return result

        # Save requirement to pred_dir
        if pred_dir:
            Path(os.path.join(pred_dir, "requirement.txt")).write_text(
                requirement, encoding="utf-8"
            )

        # Step 4: Run codespec
        t0 = time.time()
        codespec_output, rc = _run_codespec(
            work_repo,
            requirement,
            opencode_exec=opencode_exec,
            timeout=opencode_timeout,
        )
        elapsed = time.time() - t0

        result["codespec_rc"] = rc
        result["codespec_elapsed"] = round(elapsed, 2)

        if pred_dir:
            Path(os.path.join(pred_dir, "codespec_output.txt")).write_text(
                codespec_output, encoding="utf-8"
            )

        if rc != 0:
            result["fail_reason"] = f"codespec failed (rc={rc}): {codespec_output[:500]}"
            return result

        # Step 5: Collect generated files
        outputs = _collect_outputs(work_repo)

        # Save outputs to pred_dir
        if pred_dir:
            for fname, content in outputs.items():
                Path(os.path.join(pred_dir, fname)).write_text(
                    content, encoding="utf-8"
                )

        design_text = outputs.get("design.md", "")
        if not design_text.strip():
            result["fail_reason"] = "design.md was not generated by codespec"
            return result

        result["agent_ok"] = True

        # Step 6: Evaluate against ground truth
        gt_data = load_ground_truth(gt_path)
        gt_entries = gt_data.get(item_id, [])

        if not gt_entries:
            # No ground truth for this item — skip evaluation
            result["fail_reason"] = f"no ground truth features found for id={item_id!r}"
            result["hard"] = 0
            result["soft"] = 0.0
            return result

        eval_result = evaluate(
            design_text=design_text,
            gt_entries=gt_entries,
            chat_fn=chat_fn,
            max_completion_tokens=eval_max_tokens,
        )

        result.update(eval_result)

        # Save evaluation details
        if pred_dir:
            Path(os.path.join(pred_dir, "eval_result.json")).write_text(
                json.dumps(eval_result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Build conversation trace
        conversation = [
            {"type": "requirement", "content": requirement},
            {"type": "codespec_output", "content": codespec_output[:4000]},
            {"type": "design_md", "content": design_text},
            {"type": "eval_result", "content": json.dumps(eval_result, ensure_ascii=False)},
        ]
        if pred_dir:
            Path(os.path.join(pred_dir, "conversation.json")).write_text(
                json.dumps(conversation, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        if eval_result["f1"] < 0.5:
            result["fail_reason"] = (
                f"f1={eval_result['f1']:.3f} "
                f"(precision={eval_result['precision']:.3f}, "
                f"recall={eval_result['recall']:.3f})"
            )

    except Exception as e:  # noqa: BLE001
        result["fail_reason"] = f"error: {e}\n{traceback.format_exc()[-500:]}"

    return result


def _error_result(item: dict, exc: Exception) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "unknown")),
        "hard": 0,
        "soft": 0.0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "fail_reason": f"error: {exc}",
    }


def _timeout_result(item: dict) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "unknown")),
        "hard": 0,
        "soft": 0.0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "fail_reason": "TIMEOUT",
    }


# ── Batch rollout ───────────────────────────────────────────────────────────


def run_batch(
    items: list[dict],
    out_root: str,
    *,
    repo_path: str,
    gt_path: str,
    work_dir: str,
    opencode_exec: str = "opencode",
    opencode_timeout: int = 600,
    chat_fn=None,
    eval_max_tokens: int = 4096,
    workers: int = 2,
    task_timeout: int | None = 900,
) -> list[dict]:
    """Run codespec rollout for a batch of items in parallel.

    Parameters
    ----------
    items : list[dict]
        Dataset items, each with ``id`` and ``commit_id``.
    out_root : str
        Output root directory for predictions.
    repo_path : str
        Path to the original code repository.
    gt_path : str
        Path to the ground truth JSON file.
    work_dir : str
        Directory where working copies of the repo are created.
    opencode_exec : str
        Path or name of the opencode executable.
    opencode_timeout : int
        Timeout in seconds for each codespec invocation.
    chat_fn : callable | None
        LLM call function for evaluation (e.g. ``chat_optimizer``).
    eval_max_tokens : int
        Max completion tokens for LLM judge calls.
    workers : int
        Number of parallel workers.
    task_timeout : int | None
        Overall timeout per task (including evaluation).

    Returns
    -------
    list[dict]
        Result dicts, one per item.
    """
    os.makedirs(work_dir, exist_ok=True)
    if out_root:
        os.makedirs(out_root, exist_ok=True)

    predictions_dir = os.path.join(out_root, "predictions") if out_root else ""
    jsonl_path = ""
    outf = None
    if out_root:
        jsonl_path = os.path.join(out_root, "results.jsonl")
        outf = open(jsonl_path, "a", encoding="utf-8")  # noqa: SIM115

    total = len(items)
    completed = 0
    success_count = 0
    results: list[dict] = []

    def _process(item: dict) -> dict:
        return run_single(
            item,
            repo_path=repo_path,
            gt_path=gt_path,
            work_dir=work_dir,
            opencode_exec=opencode_exec,
            opencode_timeout=opencode_timeout,
            chat_fn=chat_fn,
            eval_max_tokens=eval_max_tokens,
            out_root=out_root,
        )

    max_workers = max(1, min(workers, total))
    started_at: dict[str, float] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_process, item): item for item in items}
        pending_futs = set(futs.keys())

        try:
            while pending_futs:
                done, _ = wait(pending_futs, timeout=5, return_when=FIRST_COMPLETED)
                now = time.time()

                # Check for timed-out tasks
                timed_out = []
                if task_timeout is not None:
                    timed_out = [
                        fut for fut in pending_futs - done
                        if str(futs[fut].get("id", "")) in started_at
                        and now - started_at[str(futs[fut]["id"])] >= task_timeout
                    ]

                for fut in done:
                    pending_futs.discard(fut)
                    item = futs[fut]
                    try:
                        res = fut.result()
                    except Exception as e:  # noqa: BLE001
                        res = _error_result(item, e)
                    results.append(res)
                    completed += 1
                    if res.get("hard", 0):
                        success_count += 1
                    acc = success_count / completed if completed else 0
                    print(
                        f"    [rollout] {completed}/{total} "
                        f"(f1_mean={acc:.3f}) id={res['id']} "
                        f"f1={res.get('f1', 0):.3f}",
                        flush=True,
                    )
                    if outf:
                        outf.write(json.dumps(res, ensure_ascii=False) + "\n")
                        outf.flush()

                for fut in timed_out:
                    pending_futs.discard(fut)
                    res = _timeout_result(futs[fut])
                    results.append(res)
                    completed += 1
                    acc = success_count / completed if completed else 0
                    print(
                        f"    [rollout] {completed}/{total} "
                        f"(f1_mean={acc:.3f}) id={res['id']} TIMEOUT",
                        flush=True,
                    )
                    if outf:
                        outf.write(json.dumps(res, ensure_ascii=False) + "\n")
                        outf.flush()
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
            if outf:
                outf.close()

    return results
