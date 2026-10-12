#!/usr/bin/env python3
"""
Enricher OpenCode Go: extrae planes, budgets mensuales, estimaciones de
peticiones, modelId y retención desde la doc oficial.

Fuente: https://opencode.ai/v2/docs/console/go/
(la antigua /docs/es/go/ devuelve 404 desde 2026-10)

Salida: data/extras/opencode-go.json — lista de registros
{name, plan, monthlyBudget, req5h, reqWeekly, reqMonthly, modelId, retention}

Si falla, exit != 0 y el orquestador lo registra en errors.json
(proveedor aporta cero extras, pero la base models.dev sigue visible).
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import clean_text, fetch_html, normalize_name

PAGE_URL = "https://opencode.ai/v2/docs/console/go/"
BANNER_URLS = [
    "https://opencode.ai/go",
    "https://opencode.ai/zen",
]


def fetch_promo_banners() -> list:
    banners = []
    for url in BANNER_URLS:
        try:
            html = fetch_html(url)
        except Exception:
            continue
        for m in re.finditer(r'data-component="desktop-app-banner"', html):
            chunk = html[m.start():m.start() + 2000]
            tm = re.search(r'data-slot="text"[^>]*>([\s\S]*?)</span>', chunk)
            if tm:
                text = clean_text(tm.group(1))
                if text and text not in banners:
                    banners.append(text)
    return banners


def split_tables(html: str):
    return [m.group(0) for m in re.finditer(r'<table[\s\S]*?</table>', html, re.IGNORECASE)]


def table_headers(table: str) -> list:
    ths = re.findall(r'<th[\s\S]*?>([\s\S]*?)<\/th>', table, re.IGNORECASE)
    return [clean_text(x).lower() for x in ths]


def table_rows(table: str) -> list:
    rows = re.findall(r'<tr[\s\S]*?>([\s\S]*?)<\/tr>', table, re.IGNORECASE)
    out = []
    for r in rows[1:]:
        tds = [clean_text(x) for x in re.findall(r'<td[\s\S]*?>([\s\S]*?)<\/td>', r, re.IGNORECASE)]
        if tds:
            out.append(tds)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/extras")
    args = ap.parse_args()

    html = fetch_html(PAGE_URL)
    tables = split_tables(html)
    if not tables:
        raise RuntimeError(f"No se encontraron tablas en {PAGE_URL}")

    price_tables = []    # (headers, rows) con Monthly limit
    req_tables = []      # (headers, rows) con Requests per 5 hours
    endpoints_map = {}
    privacy_map = {}

    for table in tables:
        headers = table_headers(table)
        rows = table_rows(table)
        if not rows:
            continue
        h = " ".join(headers)
        if "monthly limit" in h or "monthly" in h and "input" in h:
            price_tables.append((headers, rows))
        elif "5 hour" in h or "requests per" in h:
            req_tables.append((headers, rows))
        elif "model id" in h:
            for tds in rows:
                if len(tds) >= 2:
                    endpoints_map[normalize_name(tds[0])] = tds[1]
        elif "retention" in h or "retenci" in h:
            for tds in rows:
                if len(tds) >= 3:
                    privacy_map[normalize_name(tds[0])] = tds[2]

    if not price_tables:
        raise RuntimeError("No se encontraron tablas de precios (Monthly limit)")
    # Primera tabla de precios = Go ($10/mes), segunda = Go Plus ($40/mes)
    plans = ["Go", "Go Plus"]
    price_by_plan = {}
    for i, (headers, rows) in enumerate(price_tables[:2]):
        plan = plans[i] if i < len(plans) else f"Plan{i}"
        price_by_plan[plan] = rows
    req_by_plan = {}
    for i, (headers, rows) in enumerate(req_tables[:2]):
        plan = plans[i] if i < len(plans) else f"Plan{i}"
        req_by_plan[plan] = {normalize_name(r[0]): r for r in rows if r}

    # Promos de banners (opcional)
    banner_promos = {}
    try:
        for banner in fetch_promo_banners():
            words = banner.split()
            for size in range(1, min(7, len(words) + 1)):
                for j in range(len(words) - size + 1):
                    banner_promos.setdefault(normalize_name(' '.join(words[j:j + size])), banner)
    except Exception as e:
        print(f"banners: omitidos ({e})")

    records = []
    for plan, rows in price_by_plan.items():
        reqs = req_by_plan.get(plan, {})
        for tds in rows:
            if len(tds) < 5:
                continue
            full_name = tds[0]
            m_cond = re.search(r'\((.*?)\)', full_name)
            cond = m_cond.group(1).strip() if m_cond else ""
            base_name = re.sub(r'\s*\(.*?\)', '', full_name).strip()
            norm = normalize_name(base_name)
            rq = reqs.get(norm, [])
            budget = tds[5] if len(tds) > 5 else ""
            temporal = bool(re.search(r'limited time|tiempo limitado', budget, re.IGNORECASE))
            if re.search(r'unlimited.*limited|ilimitado por tiempo', budget, re.IGNORECASE):
                budget = "Ilimitado"
            records.append({
                "name": base_name,
                "norm": norm,
                "plan": plan,
                "condition": cond,
                "monthlyBudget": budget,
                "req5h": rq[1] if len(rq) > 1 else "",
                "reqWeekly": rq[2] if len(rq) > 2 else "",
                "reqMonthly": rq[3] if len(rq) > 3 else "",
                "modelId": endpoints_map.get(norm, ""),
                "retention": privacy_map.get(norm, ""),
                "temporal": temporal,
                "promo": banner_promos.get(norm, ""),
            })

    if not records:
        raise RuntimeError("No se pudieron parsear filas de precios de OpenCode Go")

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "opencode-go.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"OpenCode Go extras: {len(records)} registros -> {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR enrich_opencode_go: {e}")
        sys.exit(1)
