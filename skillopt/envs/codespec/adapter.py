"""Codespec environment adapter for SkillOpt.

Integrates the opencode ``/codespec/plan`` workflow into the SkillOpt
training / evaluation pipeline.  Each rollout item:

1. Copies the source repository to a working directory.
2. Checks out the task-specific commit.
3. Reads ``final.md`` as the requirement.
4. Invokes ``opencode run "/codespec/plan <requirement>"``.
5. Evaluates the generated ``design.md`` against ground truth features
   via an LLM judge (precision + recall → F1).
"""
from __future__ import annotations

import os
import tempfile

from skillopt.datasets.base import BatchSpec
from skillopt.envs.base import EnvAdapter
from skillopt.envs.codespec.dataloader import CodespecDataLoader
from skillopt.envs.codespec.rollout import run_batch


class CodespecAdapter(EnvAdapter):
    """Adapter for the Codespec benchmark."""

    def __init__(
        self,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "ratio",
        split_ratio: str = "2:1:7",
        split_seed: int = 42,
        split_output_dir: str = "",
        workers: int = 2,
        analyst_workers: int = 16,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        max_completion_tokens: int = 16384,
        # Codespec-specific parameters
        repo_path: str = "",
        gt_path: str = "",
        work_dir: str = "",
        opencode_exec: str = "opencode",
        opencode_timeout: int = 600,
        eval_max_tokens: int = 4096,
    ) -> None:
        self.workers = workers
        self.max_completion_tokens = int(max_completion_tokens)
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget

        # Codespec parameters
        self.repo_path = repo_path
        self.gt_path = gt_path
        self.work_dir = work_dir or os.path.join(tempfile.gettempdir(), "codespec_work")
        self.opencode_exec = opencode_exec
        self.opencode_timeout = int(opencode_timeout)
        self.eval_max_tokens = int(eval_max_tokens)

        self.dataloader = CodespecDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_ratio=split_ratio,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )

    def setup(self, cfg: dict) -> None:
        super().setup(cfg)

        # Resolve parameters from config if not set explicitly
        if not self.repo_path:
            self.repo_path = str(cfg.get("repo_path", "") or "").strip()
        if not self.gt_path:
            self.gt_path = str(cfg.get("gt_path", "") or "").strip()
        if not self.work_dir or self.work_dir.startswith(tempfile.gettempdir()):
            wd = str(cfg.get("work_dir", "") or "").strip()
            if wd:
                self.work_dir = wd
        if not self.opencode_exec or self.opencode_exec == "opencode":
            self.opencode_exec = str(cfg.get("opencode_exec", "opencode") or "opencode")
        if self.opencode_timeout == 600:
            self.opencode_timeout = int(cfg.get("opencode_timeout", 600) or 600)

        # Validate required paths
        if not self.repo_path:
            raise ValueError(
                "CodespecAdapter requires 'repo_path' — "
                "set it in the config under env.repo_path or pass it to __init__."
            )
        if not self.gt_path:
            raise ValueError(
                "CodespecAdapter requires 'gt_path' — "
                "set it in the config under env.gt_path or pass it to __init__."
            )

        self.dataloader.setup(cfg)

    def get_dataloader(self):
        return self.dataloader

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        return list(batch.payload or [])

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        batch = self.dataloader.build_train_batch(batch_size=batch_size, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        batch = self.dataloader.build_eval_batch(env_num=env_num, split=split, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        items: list[dict] = env_manager

        # Import chat_optimizer lazily so backend is already configured
        from skillopt.model import chat_optimizer

        return run_batch(
            items=items,
            out_root=out_dir,
            repo_path=self.repo_path,
            gt_path=self.gt_path,
            work_dir=self.work_dir,
            opencode_exec=self.opencode_exec,
            opencode_timeout=self.opencode_timeout,
            chat_fn=chat_optimizer,
            eval_max_tokens=self.eval_max_tokens,
            workers=self.workers,
            task_timeout=self.opencode_timeout + 300,
        )

    def get_task_types(self) -> list[str]:
        seen: list[str] = []
        for item in (
            self.dataloader.train_items
            + self.dataloader.val_items
            + self.dataloader.test_items
        ):
            task_type = str(item.get("task_type") or item.get("category") or "codespec")
            if task_type not in seen:
                seen.append(task_type)
        return seen or ["codespec"]
