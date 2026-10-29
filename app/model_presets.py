"""Model presets and provider proxy for keyless chat model selection."""

import os
from typing import Any

# Provider configurations with their endpoints and available models
PROVIDERS = {
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "models": [
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B Versatile", "free": True},
            {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B Versatile", "free": True},
            {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B", "free": True},
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "models": [
            {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash (Free)", "free": True},
            {"id": "google/gemini-flash-1.5", "name": "Gemini 1.5 Flash", "free": True},
            {"id": "meta-llama/llama-3.2-3b-instruct:free", "name": "Llama 3.2 3B (Free)", "free": True},
        ],
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
        "models": [
            {"id": "gemini-2.0-flash-exp", "name": "Gemini 2.0 Flash", "free": True},
            {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "free": True},
        ],
    },
}


def get_presets() -> list[dict[str, Any]]:
    """Return list of model presets grouped by provider."""
    presets = []
    for provider_id, provider_config in PROVIDERS.items():
        for model in provider_config["models"]:
            presets.append({
                "provider_id": provider_id,
                "provider_name": provider_config["name"],
                "model_id": model["id"],
                "model_name": model["name"],
                "free": model.get("free", False),
            })
    
    return presets


def get_default_preset() -> dict[str, str]:
    """Return the default preset (first free Groq model)."""
    # Default to Groq's fastest free model
    return {
        "provider_id": "groq",
        "model_id": "llama-3.3-70b-versatile",
    }


def resolve_preset(provider_id: str, model_id: str) -> dict[str, str] | None:
    """Resolve a preset to base_url and model name."""
    provider_config = PROVIDERS.get(provider_id)
    if not provider_config:
        return None
    
    # Check if this model exists in the provider
    model_exists = any(m["id"] == model_id for m in provider_config["models"])
    if not model_exists:
        return None
    
    return {
        "base_url": provider_config["base_url"],
        "model": model_id,
    }


