#!/usr/bin/env python3
"""
Esquema canónico interno para el rework basado en models.dev.

Mapea el formato de https://models.dev/api.json a filas planas para la tabla
genérica. Campos núcleo + bolsa `extras` por proveedor.
"""

# Proveedores controlados (ids tal cual en models.dev api.json)
PROVIDERS = [
    "opencode-go",
    "github-copilot",
    "anthropic",
    "openai",
    "google",
    "mistral",
    "groq",
    "xai",
    "nvidia",
]

# Alias históricos -> id canónico models.dev
PROVIDER_ALIASES = {
    "copilot": "github-copilot",
    "claude": "anthropic",
    "opencode": "opencode-go",
    "cloudflare": "cloudflare-workers-ai",
}

# Nombre bonito para mostrar
PROVIDER_LABELS = {
    "opencode-go": "OpenCode Go",
    "github-copilot": "GitHub Copilot",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "mistral": "Mistral",
    "groq": "Groq",
    "xai": "xAI",
    "nvidia": "Nvidia",
}

API_URL = "https://models.dev/api.json"
LOGO_URL = "https://models.dev/logos/{provider}.svg"


def model_to_row(provider_id: str, model_key: str, m: dict) -> dict:
    """Convierte un modelo de models.dev a fila canónica."""
    cost = m.get("cost") or {}
    limit = m.get("limit") or {}
    modalities = m.get("modalities") or {}
    return {
        "provider": provider_id,
        "providerName": PROVIDER_LABELS.get(provider_id, provider_id),
        "modelId": m.get("id") or model_key,
        "name": m.get("name") or model_key,
        "family": m.get("family") or "",
        "description": m.get("description") or "",
        "input": cost.get("input"),
        "cacheRead": cost.get("cache_read"),
        "cacheWrite": cost.get("cache_write"),
        "output": cost.get("output"),
        "context": limit.get("context"),
        "outputLimit": limit.get("output"),
        "reasoning": bool(m.get("reasoning")),
        "toolCall": bool(m.get("tool_call")),
        "modalities": modalities,
        "status": m.get("status") or "",
        "openWeights": bool(m.get("open_weights")),
        "canonicalId": m.get("canonical_model_id") or "",
        "lastUpdated": m.get("last_updated") or m.get("release_date") or "",
        "extras": {},
        "notes": "",
    }
