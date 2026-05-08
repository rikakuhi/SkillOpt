"""ReflACT Evaluation -- candidate skill validation and model selection.

Analogous to validation-based early stopping and model selection in neural
network training: evaluates candidate skills on held-out selection sets and
decides whether to accept or reject proposed updates.
"""
from reflact.evaluation.gate import evaluate_gate, GateAction, GateResult  # noqa: F401
