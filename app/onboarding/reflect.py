"""Reflection questions taken from the user's own experiences."""

from __future__ import annotations

import re

NONE_CHIP = "none / not sure"
QUESTION_LIMIT = 3


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "item"


def _usable(experiences: list[dict]) -> list[dict]:
    picked = []
    seen_names: set[str] = set()
    for experience in experiences:
        name = " ".join(str(experience.get("name") or "").split())
        bullets = []
        seen_bullets: set[str] = set()
        for bullet in experience.get("bullets") or []:
            text = " ".join(str(bullet).split())
            if len(text) < 2 or text.casefold() in seen_bullets:
                continue
            seen_bullets.add(text.casefold())
            bullets.append(text)
            if len(bullets) == 4:
                break
        if not name or not bullets or name.casefold() in seen_names:
            continue
        seen_names.add(name.casefold())
        picked.append(
            {
                "kind": experience.get("kind") or "job",
                "name": name,
                "bullets": bullets,
            }
        )
    diverse = []
    seen_kinds: set[str] = set()
    for experience in picked:
        if experience["kind"] in seen_kinds:
            continue
        diverse.append(experience)
        seen_kinds.add(experience["kind"])
        if len(diverse) == QUESTION_LIMIT:
            return diverse
    for experience in picked:
        if experience in diverse:
            continue
        diverse.append(experience)
        if len(diverse) == QUESTION_LIMIT:
            break
    return diverse


def build_questions(experiences: list[dict]) -> list[dict]:
    """Up to six questions: more-of and avoid, each with chips from that experience."""
    questions = []
    for experience in _usable(experiences):
        chips = list(experience["bullets"][:4]) + [NONE_CHIP]
        stem = _slug(f"{experience['kind']}-{experience['name']}")
        pair = (
            ("more", f"In {experience['name']}, which part would you want more of?"),
            ("avoid", f"In {experience['name']}, which part would you avoid?"),
        )
        for polarity, prompt in pair:
            questions.append(
                {
                    "question_key": f"{polarity}:{stem}"[:180],
                    "experience_ref": experience["name"],
                    "experience_kind": experience["kind"],
                    "prompt": prompt,
                    "polarity": polarity,
                    "chips": chips,
                }
            )
    return questions[:6]


def items_from_answers(questions: list[dict], answers: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return stored answer rows and proposed preference lines. Skips and 'none' add no line."""
    by_key = {question["question_key"]: question for question in questions}
    rows = []
    items = []
    seen: set[str] = set()
    for answer in answers:
        key = str(answer.get("question_key") or "")
        question = by_key.get(key)
        if question is None:
            raise ValueError("That reflection question is not part of this step.")
        skipped = bool(answer.get("skipped"))
        choice = "" if skipped else " ".join(str(answer.get("choice") or "").split())
        if not skipped and choice and choice not in question["chips"]:
            raise ValueError("Choose one of the chips, or skip the question.")
        rows.append(
            {
                "question_key": key,
                "choice": choice,
                "skipped": skipped or not choice,
                "experience_ref": question["experience_ref"],
                "polarity": question["polarity"],
            }
        )
        if skipped or not choice or choice == NONE_CHIP:
            continue
        item_key = f"reflection:{question['polarity']}:{_slug(choice)}"[:180]
        if item_key in seen:
            continue
        seen.add(item_key)
        negative = question["polarity"] == "avoid"
        items.append(
            {
                "key": item_key,
                "kind": "reflection",
                "name": choice[:160],
                "level": "",
                "confidence": 0.6,
                "evidence": [choice],
                "status": "proposed",
                "locked": False,
                "weight": None,
                "source": "reflection",
                "document_backed": True,
                "writer": "reflection",
                "proposed_weight": -0.7 if negative else 0.7,
                "polarity": "avoid" if negative else "prefer",
                "experience_ref": question["experience_ref"],
                "edited": False,
            }
        )
    return rows, items
