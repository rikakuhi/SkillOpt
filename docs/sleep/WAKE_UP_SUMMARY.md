# Wake-up summary — SkillOpt-Sleep (built overnight, 2026-06-07)

你睡觉时我离线把第一版做完了。下面是 TL;DR、怎么跑、关键决定、以及等你醒来要回答的问题。

## TL;DR — what exists now

A working **Claude Code plugin + Python engine** that gives your local Claude
agent a nightly **sleep cycle**: it reviews your past sessions offline, replays
recurring tasks on your own budget, and consolidates what it learns into
**validated** memory (`CLAUDE.md`) and skills (`SKILL.md`) — keeping a change
only if it improves a held-out replay score, and only after you adopt it.

It fuses the three things you gave me:
- **SkillOpt** → the gate. I reuse `skillopt.evaluation.gate.evaluate_gate` verbatim; nightly edits are bounded and validation-gated, exactly like the paper.
- **Claude Dreams** → the safety model. Offline consolidation over past sessions; the input is never mutated; output is staged and reviewed, then adopted or discarded.
- **Agent Sleep paper** → the framing. Nightly replay turns short-term episodes into long-term competence; cross-night `slow_memory` is the long-term store.

**It is proven to work** (deterministically, no API spend):
- researcher persona: held-out **0.33 → 1.00**
- programmer persona: held-out **0.32 → 1.00**
- the gate **rejects an injected harmful edit** in both runs
- 13 stdlib tests pass, including full cycle → stage → **adopt-with-backup**, and parsing of your **real** on-disk transcripts.

## Where it lives

- **Worktree:** `/home/azureuser/yifan/Code_workspace/my_repo/SkillOpt-sleep`
- **Branch:** `feat/claude-code-sleep-plugin` (2 commits, **NOT pushed**)
- Your fork's `main` was fast-forwarded locally to microsoft `upstream/main`
  (it was 40 behind; now 0/0). `origin` (GitHub) untouched — nothing pushed.

```
skillopt/sleep/                     # the engine (import-light, py>=3.10)
  harvest.py mine.py replay.py backend.py consolidate.py
  memory.py staging.py cycle.py state.py config.py types.py __main__.py
  experiments/  personas.py  run_experiment.py
skillopt-sleep-plugin/              # the Claude Code plugin
  .claude-plugin/plugin.json  commands/sleep.md  skills/skillopt-sleep/SKILL.md
  hooks/  scripts/sleep.sh  scripts/install-cron.sh  README.md
tests/test_sleep_engine.py          # 13 tests, stdlib unittest
docs/superpowers/specs/2026-06-07-...-design.md   # full design + open questions
docs/sleep/experiment_results.md    # recorded proof output
```

## Try it yourself in 60 seconds (no API spend)

```bash
cd /home/azureuser/yifan/Code_workspace/my_repo/SkillOpt-sleep

# 1) deterministic proof it improves + gate blocks regressions
python3.12 -m skillopt.sleep.experiments.run_experiment --persona researcher --assert-improves
python3.12 -m skillopt.sleep.experiments.run_experiment --persona programmer  --assert-improves

# 2) see it mine YOUR real recent sessions (read-only)
python3.12 -m skillopt.sleep harvest --project /home/azureuser/yifan/Code_workspace --scope invoked

# 3) full run on this project (mock backend, stages a proposal, touches nothing live)
python3.12 -m skillopt.sleep run --project "$(pwd)" --scope invoked --backend mock
python3.12 -m skillopt.sleep status --project "$(pwd)"

# 4) all tests
python3.12 -m unittest tests.test_sleep_engine
```

(The `python3.12` is because the repo needs ≥3.10 and this box's default
`python3` is 3.8. The plugin's `scripts/sleep.sh` auto-picks a good interpreter.)

## Key decisions I made (so you can veto them)

1. **Reused the real SkillOpt gate**, didn't reinvent it. `consolidate.py`
   imports `skillopt.evaluation.gate`. That module imports cleanly without
   `openai`; the heavy optimizer/reflect modules (which need `openai`) are only
   touched by the future real-API path, so the mock path is dependency-free.
2. **Two backends.** `mock` = deterministic, no key, used for tests + the
   acceptance experiment. `anthropic` = real lift via your `claude` CLI / SDK
   (wired but Phase-3-shallow). Default is `mock` so nothing spends money
   without you asking.
3. **Review-gated adoption by default.** A night **stages** `proposed_CLAUDE.md`
   / `proposed_SKILL.md` + a `report.md` into `<project>/.skillopt-sleep/staging/<date>/`
   and changes **nothing live** until `/sleep adopt` (which backs up first).
   `--auto-adopt` exists for power users but is off.
4. **Edits live in a protected, marked block** inside SKILL.md/CLAUDE.md, so the
   cycle never clobbers your hand-written content.
5. **Phase boundary I hit honestly:** mining your *real* free-text transcripts
   yields tasks with no exact checkable reference, so on real data the mock
   judge can't score lift (night → reject, 0 lift — correct, not a bug). Real
   lift on real transcripts needs the **LLM miner + judge (Phase 3)** to attach
   checkable references. The deterministic *proof* runs on persona fixtures that
   do have exact refs. This is documented, not hidden.

## What I deliberately did NOT do

- **Did not push** anything (you said offline only).
- **Did not** spend your `ANTHROPIC_API_KEY` — every run above is `mock`.
  (Your key IS set; if you want, I can run the `--backend anthropic` demo next.)
- **Did not** build the Codex version (you deferred it; architecture keeps the
  backend pluggable).
- **Did not** touch your live `~/.claude/CLAUDE.md` or `~/.claude/skills/*`.

## 5 questions for you (from the design doc)

1. **Adopt policy:** keep default *review-gated*, or enable `auto_adopt` on your machine?
2. **Scope:** harvest only the invoked project, or *all* projects in `~/.claude/projects`?
3. **Real-API demo:** want me to spend live budget on the `--backend anthropic` persona demo to show genuine (non-mock) lift?
4. **Skill target:** evolve a *new* managed `skillopt-sleep-learned` skill (current default), or also edit your existing hand-written skills?
5. **Paper:** make this a SkillOpt arXiv section/figure — "deployment-time continual skill optimization = SkillOpt gate ⊕ Dream consolidation ⊕ Sleep"? I think it's a strong story: SkillOpt provides the *safe update rule* that Dreams/Sleep lack.

## Suggested next steps (when you're back)

- **Phase 3** (highest value): real `AnthropicBackend` miner+judge so it lifts on
  your *actual* transcripts, not just personas; + `fresh` worktree replay.
- Wire `slow_memory` cross-night consolidation (state.py already stores it).
- `pip install pytest openai anthropic` in this env if you want the upstream
  test suite + real backend to run here (3 upstream tests currently error only
  because `pytest` isn't installed — unrelated to this branch).

Everything is committed on the branch. Nothing is pushed. Sleep well 😴
