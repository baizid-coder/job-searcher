"""Data contract Discover uses for the first batch and later swipes.

preference_profile is versioned. The first batch hard-filters on constraints,
then keeps about 70 percent of jobs near confirmed preferences and about 30
percent deliberately different. A swipe writes the same profile and bumps
the version. This module does not render a swipe page.
"""

from __future__ import annotations

from app.onboarding.service import first_batch_for, record_swipe, snapshot


def preference_profile() -> dict:
    return snapshot()


def build_first_batch(jobs: list[dict], target: int = 20) -> dict:
    return first_batch_for(jobs, target)


def apply_swipe(verdict: str, posting: dict) -> dict:
    return record_swipe(verdict, posting)
