#!/usr/bin/env python3
"""
Esquema canónico interno para el rework basado en models.dev.

Mapea el formato de https://models.dev/api.json a filas planas para la tabla
genérica. Campos núcleo + bolsa `extras` por proveedor.
"""

import re

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
    "cloudflare-workers-ai",
]

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
    "cloudflare-workers-ai": "Cloudflare Workers AI",
}

API_URL = "https://models.dev/api.json"
LOGO_URL = "https://models.dev/logos/{provider}.svg"

# Los embeddings y los rerankers no se distinguen por modalidades: models.dev
# los declara texto->texto, igual que un modelo de chat.
EMBEDDING_RE = re.compile(r"embed|rerank|bge", re.IGNORECASE)
# Modelos cuyo oficio es generar imágenes, frente a agentes que devuelven texto
# e imágenes (p. ej. Deep Research): ambos declaran salida text+image.
IMAGE_MODEL_RE = re.compile(r"image|imagine|flux|banana|imagen|dall", re.IGNORECASE)


def classify_kind(modalities: dict, model_id: str = "", name: str = "") -> str:
    """
    Tipo visible de la fila: qué produce el modelo (eje salida).

    Deliberadamente NO describe la entrada ni las capacidades: un modelo de
    visión es `Texto` que acepta imágenes, y `reasoning`/`tool_call` son
    capacidades, no tipos. La entrada se muestra aparte (`Acepta`) y el detalle
    lista las modalidades exactas.
    """
    sin = set(modalities.get("input") or [])
    sout = set(modalities.get("output") or [])
    if EMBEDDING_RE.search(model_id or "") or EMBEDDING_RE.search(name or ""):
        return "Embeddings"
    if "video" in sout:
        return "Vídeo"
    if "image" in sout and ("text" not in sout or IMAGE_MODEL_RE.search(model_id or "")):
        return "Imagen"
    if "audio" in sout:
        return "Voz"
    # Audio -> texto sin más entradas: transcribe, no conversa.
    if sin == {"audio"}:
        return "Transcripción"
    return "Texto"


def model_to_row(provider_id: str, model_key: str, m: dict) -> dict:
    """Convierte un modelo de models.dev a fila canónica."""
    cost = m.get("cost") or {}
    limit = m.get("limit") or {}
    modalities = m.get("modalities") or {}
    row = {
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
        "kind": classify_kind(modalities, m.get("id") or model_key, m.get("name") or model_key),
        "status": m.get("status") or "",
        "openWeights": bool(m.get("open_weights")),
        "canonicalId": m.get("canonical_model_id") or "",
        "lastUpdated": m.get("last_updated") or m.get("release_date") or "",
        # Estado del precio: `free` = 0 real, `priceUnavailable` = el proveedor
        # no lo publica (los precios pasan a None para mostrarse como "-").
        "free": False,
        "priceUnavailable": False,
        # Ids alternativos colapsados en esta fila (alias "-latest", snapshots...)
        "aliases": [],
        "extras": {},
        "notes": "",
    }
    return row
