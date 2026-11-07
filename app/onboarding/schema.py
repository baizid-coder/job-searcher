"""JSON Schema for the profile proposal the model must return."""

from __future__ import annotations

INFERENCE_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OnboardingInference",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "role_families",
        "skills",
        "industries",
        "seniority_guess",
        "strengths",
        "gaps",
    ],
    "properties": {
        "role_families": {"type": "array", "items": {"$ref": "#/$defs/confident"}},
        "skills": {"type": "array", "items": {"$ref": "#/$defs/skill"}},
        "industries": {"type": "array", "items": {"$ref": "#/$defs/confident"}},
        "seniority_guess": {"$ref": "#/$defs/confident"},
        "strengths": {"type": "array", "items": {"$ref": "#/$defs/named"}},
        "gaps": {"type": "array", "items": {"$ref": "#/$defs/named"}},
    },
    "$defs": {
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "confident": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "confidence", "evidence"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": {"$ref": "#/$defs/evidence"},
            },
        },
        "skill": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "level", "evidence"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "level": {"type": "string", "minLength": 1},
                "evidence": {"$ref": "#/$defs/evidence"},
            },
        },
        "named": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "evidence"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "evidence": {"$ref": "#/$defs/evidence"},
            },
        },
    },
}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence_errors(value, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{path}.evidence must be a non-empty array of quotes"]
    errors = []
    for index, quote in enumerate(value):
        if not _nonempty(quote):
            errors.append(f"{path}.evidence[{index}] must be a non-empty string")
    return errors


def _confident_errors(value, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object with name, confidence, and evidence"]
    errors = []
    extra = set(value) - {"name", "confidence", "evidence"}
    if extra:
        errors.append(f"{path} has unsupported fields: {', '.join(sorted(extra))}")
    if "name" not in value or not _nonempty(value.get("name")):
        errors.append(f"{path}.name must be a non-empty string")
    confidence = value.get("confidence")
    if not _number(confidence) or not 0 <= float(confidence) <= 1:
        errors.append(f"{path}.confidence must be a number from 0 to 1")
    errors.extend(_evidence_errors(value.get("evidence"), path))
    return errors


def _named_errors(value, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object with name and evidence"]
    errors = []
    extra = set(value) - {"name", "evidence"}
    if extra:
        errors.append(f"{path} has unsupported fields: {', '.join(sorted(extra))}")
    if "name" not in value or not _nonempty(value.get("name")):
        errors.append(f"{path}.name must be a non-empty string")
    errors.extend(_evidence_errors(value.get("evidence"), path))
    return errors


def _skill_errors(value, path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object with name, level, and evidence"]
    errors = []
    extra = set(value) - {"name", "level", "evidence"}
    if extra:
        errors.append(f"{path} has unsupported fields: {', '.join(sorted(extra))}")
    if "name" not in value or not _nonempty(value.get("name")):
        errors.append(f"{path}.name must be a non-empty string")
    if "level" not in value or not _nonempty(value.get("level")):
        errors.append(f"{path}.level must be a non-empty string")
    errors.extend(_evidence_errors(value.get("evidence"), path))
    return errors


def schema_errors(data) -> list[str]:
    """Return human-readable errors. An empty list means the object matches INFERENCE_SCHEMA."""
    if not isinstance(data, dict):
        return ["The model output must be a JSON object"]
    errors = []
    extra = set(data) - set(INFERENCE_SCHEMA["required"])
    if extra:
        errors.append("Unsupported fields: " + ", ".join(sorted(extra)))
    for key in INFERENCE_SCHEMA["required"]:
        if key not in data:
            errors.append(f"Missing {key}")
    if errors and any(key not in data for key in INFERENCE_SCHEMA["required"]):
        return errors
    errors.extend(_array_errors(data, "role_families", _confident_errors))
    errors.extend(_array_errors(data, "skills", _skill_errors))
    errors.extend(_array_errors(data, "industries", _confident_errors))
    if "seniority_guess" in data:
        errors.extend(_confident_errors(data.get("seniority_guess"), "seniority_guess"))
    errors.extend(_array_errors(data, "strengths", _named_errors))
    errors.extend(_array_errors(data, "gaps", _named_errors))
    return errors


def _array_errors(data: dict, key: str, check) -> list[str]:
    if key not in data:
        return []
    value = data[key]
    if not isinstance(value, list):
        return [f"{key} must be an array"]
    errors = []
    for index, item in enumerate(value):
        errors.extend(check(item, f"{key}[{index}]"))
    return errors
