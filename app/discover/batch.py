"""Pick a batch of about 20 real postings, spread across role families."""

from __future__ import annotations

from app.discover.classify import BATCH_SIZE, FAMILIES, FAMILY_LABELS, present_card
from app.discover.summary import signal_kind

# Share of the batch aimed at low-confidence or conflicting families.
EXPLORE_FRACTION = 0.7


def thin_families(available: dict[str, int], target: int = BATCH_SIZE) -> list[str]:
    """Families with no live postings. `target` stays for callers that pass a batch size."""
    if target < 0:
        return []
    return [family for family in FAMILIES if available.get(family, 0) <= 0]


def _take(grouped: dict[str, list[dict]], families: list[str], slots: int, seen: set[str]) -> list[dict]:
    picked: list[dict] = []
    if slots <= 0 or not families:
        return picked
    indexes = {family: 0 for family in families}
    while len(picked) < slots:
        progressed = False
        for family in families:
            if len(picked) >= slots:
                break
            bucket = grouped.get(family) or []
            index = indexes[family]
            while index < len(bucket) and bucket[index]["posting_id"] in seen:
                index += 1
            indexes[family] = index
            if index < len(bucket):
                item = bucket[index]
                picked.append(item)
                seen.add(item["posting_id"])
                indexes[family] = index + 1
                progressed = True
        if not progressed:
            break
    return picked


def _round_robin(grouped: dict[str, list[dict]], target: int) -> list[dict]:
    return _take(grouped, list(FAMILIES), target, set())


def select_postings(
    postings: list[dict],
    family_counts: dict[str, dict],
    confirmed_prefers: set[str],
    *,
    target: int = BATCH_SIZE,
    has_verdicts: bool,
) -> tuple[list[dict], list[str]]:
    grouped = {family: [] for family in FAMILIES}
    for posting in postings:
        family = posting.get("role_family")
        if family in grouped:
            grouped[family].append(posting)
    available = {family: len(grouped[family]) for family in FAMILIES}
    thin = thin_families(available, target)

    if not has_verdicts:
        # The first batch uses the onboarding profile when one exists:
        # hard filters first, then about 70% near that profile and 30% different.
        from app.onboarding.batch import select_for_discover

        picked = select_for_discover(postings, target)
        if picked is not None:
            return picked, thin
        return _round_robin(grouped, target), thin

    explore: list[str] = []
    prefer: list[str] = []
    rest: list[str] = []
    for family in FAMILIES:
        counts = family_counts.get(family) or {"yes": 0, "no": 0, "maybe": 0}
        kind = signal_kind(counts)
        if kind in {"unseen", "explore"}:
            explore.append(family)
        elif family in confirmed_prefers:
            prefer.append(family)
        else:
            rest.append(family)

    def live(families: list[str]) -> list[str]:
        return [family for family in families if available.get(family, 0) > 0]

    explore_live = live(explore)
    prefer_live = live(prefer)
    rest_live = live(rest)
    seen: set[str] = set()
    picked: list[dict] = []

    if explore_live and prefer_live:
        explore_slots = round(target * EXPLORE_FRACTION)
        prefer_slots = target - explore_slots
        picked.extend(_take(grouped, explore_live, explore_slots, seen))
        picked.extend(_take(grouped, prefer_live, prefer_slots, seen))
    elif explore_live and rest_live:
        explore_slots = round(target * EXPLORE_FRACTION)
        picked.extend(_take(grouped, explore_live, explore_slots, seen))
        picked.extend(_take(grouped, rest_live, target - len(picked), seen))
    elif explore_live:
        picked.extend(_take(grouped, explore_live, target, seen))
    elif prefer_live:
        picked.extend(_take(grouped, prefer_live, target, seen))
    else:
        picked.extend(_take(grouped, rest_live, target, seen))

    if len(picked) < target:
        filler = explore_live + rest_live + prefer_live
        picked.extend(_take(grouped, filler, target - len(picked), seen))
    return picked[:target], thin


def present_batch(postings: list[dict], thin: list[str]) -> dict:
    return {
        "postings": [present_card(posting) for posting in postings],
        "thin_families": thin,
        "thin_family_labels": [FAMILY_LABELS.get(family, family) for family in thin],
    }
