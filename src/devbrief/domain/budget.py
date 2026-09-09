from __future__ import annotations

from datetime import datetime

from devbrief.domain.contracts import ExecutionBudget
from devbrief.domain.errors import DevBriefError, ErrorCode


def consume_step(budget: ExecutionBudget, now: datetime) -> ExecutionBudget:
    """Consume one lifecycle step after enforcing the immutable budget."""
    if now >= budget.deadline_at:
        raise DevBriefError(
            ErrorCode.DEADLINE_EXCEEDED, "execution deadline has passed"
        )
    if budget.consumed_steps >= budget.max_steps:
        raise DevBriefError(ErrorCode.BUDGET_EXHAUSTED, "maximum harness steps reached")
    return budget.model_copy(update={"consumed_steps": budget.consumed_steps + 1})


def consume_model_usage(
    budget: ExecutionBudget,
    now: datetime,
    *,
    tokens: int,
    cost: float,
) -> ExecutionBudget:
    """Record model usage without allowing calls to exceed their fixed budget."""
    if tokens < 0 or cost < 0:
        raise DevBriefError(
            ErrorCode.VALIDATION_ERROR, "usage values must be non-negative"
        )
    if now >= budget.deadline_at:
        raise DevBriefError(
            ErrorCode.DEADLINE_EXCEEDED, "execution deadline has passed"
        )
    next_tokens = budget.consumed_model_tokens + tokens
    next_cost = budget.consumed_cost + cost
    if next_tokens > budget.max_model_tokens:
        raise DevBriefError(ErrorCode.BUDGET_EXHAUSTED, "maximum model tokens reached")
    if next_cost > budget.max_cost:
        raise DevBriefError(ErrorCode.COST_EXHAUSTED, "maximum model cost reached")
    return budget.model_copy(
        update={"consumed_model_tokens": next_tokens, "consumed_cost": next_cost}
    )
