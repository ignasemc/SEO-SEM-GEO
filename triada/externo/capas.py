"""Modelo de tres capas por vertiente: en la web, señales externas y resultado real.

Regla principal: si falta un dato, se muestra "sin datos"; nunca se rellena con un 0 o un 100.
La nota global solo se calcula si hay datos de fuera de la web, y siempre va con su cobertura.
"""

from __future__ import annotations

from typing import Any

from .cuestionario import PREGUNTAS, VALOR


def _p(v) -> str | None:
    return None if v is None else f"{v:g}".replace(".", ",") + " %"


def _d(v) -> str | None:
    return None if v is None else f"{v:g}".replace(".", ",")
from .senales import valor_resenas


def valores_cuestionario(ext: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Valor de cada señal: medido/importado (si hay datos) o declarado por el usuario."""
    res: dict[str, dict[str, Any]] = {}
    for pid, r in (ext.get("cuestionario") or {}).items():
        if r.get("valor") in ("si", "parcial", "no", "na"):
            res[pid] = {"valor": r["valor"], "origen": "declarado", "detalle": ""}

    def medir(pid, valor, origen, detalle):
        if valor is None:
            return
        declarado = res.get(pid, {}).get("valor")
        res[pid] = {"valor": valor, "origen": origen, "detalle": detalle, "declarado": declarado}

    sc = ext.get("search_console") or {}
    if (enl := sc.get("enlaces")):
        n = enl.get("dominios", 0)
        medir("enlaces_sector", "si" if n >= 20 else "parcial" if n >= 5 else "no", "importado",
              f"{n} dominios enlazan a tu web (Search Console).")
    rs = ext.get("resenas")
    if rs:
        etiqueta = "ejemplo" if rs.get("simulado") else "medido"
        v, txt = valor_resenas(rs)
        medir("resenas", v, etiqueta, txt)
        if rs.get("tu"):
            medir("perfil_google", "si", etiqueta, f"Encontrado en Google Maps: {rs['tu'].get('nombre', '')}.")
        else:
            medir("perfil_google", "no", etiqueta, "No se encontró en Google Maps con esa búsqueda.")
    wd = ext.get("wikidata")
    if wd and not wd.get("simulado"):
        if wd.get("coincide"):
            c = next(c for c in wd["candidatos"] if c["nombre"])
            medir("wikidata", "si", "medido", f"Coincidencia exacta en Wikidata ({c['id']}): verifica que es tu empresa.")
        else:
            medir("wikidata", "no", "medido", "No hay ninguna entrada con ese nombre exacto en Wikidata.")
    elif wd and wd.get("simulado"):
        medir("wikidata", "no", "ejemplo", "Sin entrada en Wikidata (dato de ejemplo).")
    return res


def _externo(rama: str, vals: dict[str, dict]) -> dict[str, Any]:
    items, num, den_datos, den_total = [], 0.0, 0.0, 0.0
    for p in PREGUNTAS:
        if rama not in p["ramas"]:
            continue
        v = vals.get(p["id"])
        valor = v["valor"] if v else None
        items.append({"id": p["id"], "pregunta": p["pregunta"], "valor": valor,
                      "origen": v["origen"] if v else None, "detalle": v.get("detalle", "") if v else ""})
        if valor == "na":
            continue
        den_total += p["peso"]
        if valor in VALOR:
            den_datos += p["peso"]
            num += p["peso"] * VALOR[valor]
    return {"valor": round(100 * num / den_datos) if den_datos else None,
            "cobertura": round(den_datos / den_total, 2) if den_total else 0.0, "items": items}


def _resultado(rama: str, ext: dict[str, Any]) -> dict[str, Any]:
    met: list[dict[str, Any]] = []
    partes: list[float] = []
    fuente = None
    if rama == "sem":
        ads = ext.get("ads") or {}
        pk, tb = ads.get("palabras_clave"), ads.get("terminos")
        if pk or tb:
            fuente = "Google Ads (CSV importado)"
        if pk:
            if pk.get("qs_medio") is not None:
                partes.append(pk["qs_medio"] * 10)
            cpa = round(pk["coste"] / pk["conversiones"], 2) if pk.get("conversiones") else None
            met += [{"nombre": "Nivel de Calidad medio", "valor": _d(pk.get("qs_medio"))},
                    {"nombre": "% gasto con NC ≤ 4", "valor": _p(pk.get("pct_coste_qs_bajo"))},
                    {"nombre": "Conversiones", "valor": _d(pk.get("conversiones"))},
                    {"nombre": "Coste por conversión", "valor": (_d(cpa) + " €") if cpa else None}]
        if tb:
            if tb.get("pct_desperdicio") is not None:
                partes.append(max(0.0, 100 - tb["pct_desperdicio"]))
            met += [{"nombre": "Gasto sin conversión",
                     "valor": f"{_d(tb['coste_sin_conversion'])} € ({_p(tb['pct_desperdicio'])})"}]
    elif rama == "geo":
        ejec = (ext.get("monitor") or {}).get("ejecuciones") or []
        if ejec:
            u = ejec[-1]
            r = u["resumen"]
            fuente = ("Monitor de IA · datos simulados" if u.get("simulado") else "Monitor de IA") + f" · {u['fecha'][:10]}"
            if r.get("pct_mencion") is not None:
                partes.append(r["pct_mencion"])
            citas = [m["pct_cita_web"] for m in r["por_motor"].values() if m.get("pct_cita_web") is not None]
            met += [{"nombre": "Respuestas que te mencionan", "valor": _p(r.get("pct_mencion"))},
                    {"nombre": "Respuestas que citan tu web", "valor": _p(round(sum(citas) / len(citas), 1)) if citas else None},
                    {"nombre": "Respuestas analizadas", "valor": r.get("respuestas")}]
    return {"valor": round(sum(partes) / len(partes)) if partes else None, "metricas": met, "fuente": fuente}


def calcular(puntuaciones_web: dict[str, int], ramas: list[str], ext: dict[str, Any],
             pesos: dict[str, float]) -> dict[str, Any]:
    vals = valores_cuestionario(ext)
    res = {}
    # SEO es interno: su nota es la de la web, sin capas externas
    web_seo = puntuaciones_web.get("seo") if "seo" in ramas else None
    res["seo"] = {"web": web_seo, "interno": True, "externo": {"valor": None, "cobertura": 0.0, "items": []},
                  "resultado": {"valor": None, "metricas": [], "fuente": None},
                  "fuera": {"valor": None, "cobertura": 0}, "peso_web": 1.0, "total": web_seo,
                  "cobertura": 100 if web_seo is not None else 0, "provisional": False}
    for r in ("sem", "geo"):
        web = puntuaciones_web.get(r) if r in ramas else None
        exterior = _externo(r, vals)
        resultado = _resultado(r, ext)
        disponibles = [x for x in (exterior["valor"], resultado["valor"]) if x is not None]
        fuera = round(sum(disponibles) / len(disponibles)) if disponibles else None
        cob_fuera = (exterior["cobertura"] + (1.0 if resultado["valor"] is not None else 0.0)) / 2
        w = pesos.get(r, 0.5)
        total = round(w * web + (1 - w) * fuera) if (web is not None and fuera is not None) else None
        cobertura = round(100 * (w + (1 - w) * cob_fuera)) if web is not None else round(100 * (1 - w) * cob_fuera)
        res[r] = {"web": web, "interno": False, "externo": exterior, "resultado": resultado,
                  "fuera": {"valor": fuera, "cobertura": round(100 * cob_fuera)},
                  "peso_web": w, "total": total, "cobertura": cobertura, "provisional": cobertura < 70}
    return res
