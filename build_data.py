#!/usr/bin/env python3
"""
Ensambla public/data/all.json + errors.json a partir de:
  data/providers/<id>.json  (base models.dev, vía fetch_models.py)
  data/extras/opencode-go.json, data/extras/groq.json (opcionales)

Cruce por normalize_name / modelId. Los extras que no cruzan se conservan
como filas propias del proveedor (p.ej. modelos Go sin equivalente en api.json).

Uso:
    python3 build_data.py --data data --out public/data
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import normalize_name, parse_family_version
from schema import PROVIDERS, model_to_row


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def num(v) -> float:
    """Convierte a float tratando None/'' como 0 (para comparaciones)."""
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def le(new, old) -> bool:
    """True si el precio nuevo es <= al antiguo; si falta alguno, no bloquea."""
    if new is None or old is None:
        return True
    return num(new) <= num(old)


def ge(new, old) -> bool:
    """True si el contexto nuevo es >= al antiguo; si falta alguno, no bloquea."""
    if new is None or old is None:
        return True
    return num(new) >= num(old)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="public/data")
    args = ap.parse_args()

    errors = []
    try:
        meta = load_json(os.path.join(args.data, "meta.json"))
        errors.extend(meta.get("errors", []))
    except FileNotFoundError:
        errors.append({"provider": "*", "message": "falta data/meta.json (¿falló fetch?)", "date": datetime.now(timezone.utc).isoformat()})

    try:
        extras_go = load_json(os.path.join(args.data, "extras", "opencode-go.json"))
    except FileNotFoundError:
        extras_go = None
        errors.append({"provider": "opencode-go", "message": "sin extras (enricher falló o no ejecutado)", "date": datetime.now(timezone.utc).isoformat()})
    try:
        extras_groq = load_json(os.path.join(args.data, "extras", "groq.json"))
    except FileNotFoundError:
        extras_groq = None
        errors.append({"provider": "groq", "message": "sin extras (enricher falló o no ejecutado)", "date": datetime.now(timezone.utc).isoformat()})

    # Indexar extras Go por (plan, norm)
    go_by_plan_norm = {}
    if extras_go:
        for r in extras_go:
            go_by_plan_norm.setdefault((r.get("plan", ""), r.get("norm", "")), []).append(r)

    groq_by_id = {}
    if extras_groq:
        for r in extras_groq:
            if r.get("modelId"):
                groq_by_id[r["modelId"].lower()] = r

    rows = []
    providers_ok = {}
    for pid in PROVIDERS:
        path = os.path.join(args.data, "providers", f"{pid}.json")
        try:
            pdata = load_json(path)
        except FileNotFoundError:
            errors.append({"provider": pid, "message": f"sin base ({path} no existe)", "date": datetime.now(timezone.utc).isoformat()})
            continue
        models = pdata.get("models") or {}
        n = 0
        for mkey, m in models.items():
            row = model_to_row(pid, mkey, m)
            norm = normalize_name(row["name"])
            if pid == "opencode-go" and extras_go is not None:
                # Unificar los planes (Go / Go Plus) del mismo modelo en una
                # sola fila; el detalle mostrará un bloque por plan.
                matched = []
                for (plan, pn), recs in go_by_plan_norm.items():
                    if pn == norm:
                        matched.extend(recs)
                if matched:
                    # Ordenar Go antes que Go Plus
                    matched.sort(key=lambda r: 0 if r.get("plan") == "Go" else 1)
                    plans = [{
                        "plan": rec.get("plan", ""),
                        "condition": rec.get("condition", ""),
                        "monthlyBudget": rec.get("monthlyBudget", ""),
                        "req5h": rec.get("req5h", ""),
                        "reqWeekly": rec.get("reqWeekly", ""),
                        "reqMonthly": rec.get("reqMonthly", ""),
                        "ocModelId": rec.get("modelId", ""),
                        "retention": rec.get("retention", ""),
                        "temporal": rec.get("temporal", False),
                        "promo": rec.get("promo", ""),
                    } for rec in matched]
                    row["extras"] = {
                        "plans": plans,
                        "plan": ", ".join(p["plan"] for p in plans if p["plan"]),
                        "monthlyBudget": ", ".join(p["monthlyBudget"] for p in plans if p["monthlyBudget"]),
                        "promo": next((p["promo"] for p in plans if p["promo"]), ""),
                        "temporal": any(p["temporal"] for p in plans),
                    }
                    rows.append(row)
                    n += 1
                else:
                    rows.append(row)
                    n += 1
            elif pid == "groq" and extras_groq is not None:
                g = groq_by_id.get((row["modelId"] or "").lower())
                if g:
                    row["extras"] = {
                        "speed": g.get("speed", ""),
                        "limits": g.get("limits", ""),
                        "maxCompletion": g.get("maxCompletion", ""),
                        "maxFile": g.get("maxFile", ""),
                        "groqStatus": g.get("status", ""),
                        "enterprise": g.get("enterprise", False),
                    }
                    if g.get("status"):
                        row["status"] = g["status"]
                rows.append(row)
                n += 1
            else:
                rows.append(row)
                n += 1
        providers_ok[pid] = n

    # Detección de modelos superados, por proveedor: si existe una versión
    # superior de la misma familia que iguala o mejora en todo (precios <=,
    # contexto >=), se marca con supersededBy (badge rojo en la tabla).
    for pid in providers_ok:
        idxs = [i for i, r in enumerate(rows) if r["provider"] == pid]
        direct = {}
        for i in idxs:
            f1, v1 = parse_family_version(rows[i]["name"])
            if v1 <= 0:
                continue
            best, best_ver = None, v1
            for j in idxs:
                if i == j:
                    continue
                f2, v2 = parse_family_version(rows[j]["name"])
                if f2 != f1 or v2 <= best_ver:
                    continue
                a, b = rows[i], rows[j]
                if (le(b.get("input"), a.get("input")) and le(b.get("output"), a.get("output"))
                        and le(b.get("cacheRead"), a.get("cacheRead"))
                        and le(b.get("cacheWrite"), a.get("cacheWrite"))
                        and ge(b.get("context"), a.get("context"))):
                    best, best_ver = j, v2
            if best is not None:
                direct[i] = best

        # Resolver cadenas A->B->C para apuntar siempre al último no superado
        for i in list(direct):
            cur, seen = direct[i], {i}
            while cur in direct and cur not in seen:
                seen.add(cur)
                cur = direct[cur]
            rows[i]["supersededBy"] = rows[cur]["name"]

    os.makedirs(args.out, exist_ok=True)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "providers": providers_ok,
        "total": len(rows),
        "rows": rows,
    }
    with open(os.path.join(args.out, "all.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    with open(os.path.join(args.out, "errors.json"), "w", encoding="utf-8") as f:
        json.dump(errors, f, ensure_ascii=False, indent=1)
    print(f"all.json: {len(rows)} filas, proveedores={providers_ok}")
    print(f"errors.json: {len(errors)} errores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
