"""Plan de acciones fuera de la web.

Las acciones se generan a partir de datos (cuestionario, monitor de IA, informes importados) y cada
una explica por qué te toca a ti. Su estado (pendiente, en curso, hecho, descartada), responsable,
fecha y evidencia se guardan por proyecto. Si una acción deja de generarse porque los datos nuevos
muestran que ya está resuelta, se marca como hecha y verificada automáticamente.
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime
from typing import Any

from .capas import valores_cuestionario
from .cuestionario import POR_ID, PREGUNTAS

ORDEN_PRIORIDAD = {"alta": 0, "media": 1, "baja": 2}
ORDEN_ESFUERZO = {"bajo": 0, "medio": 1, "alto": 2}
ORDEN_ESTADO = {"pendiente": 0, "en_curso": 1, "hecho": 2, "descartada": 3}
BAJAR = {"alta": "media", "media": "baja", "baja": "baja"}
# acciones de versiones anteriores que ya no existen (SEO pasó a ser solo interno)
OBSOLETAS = {"sc-ctr", "cu-sc_verificada"}


def _eur(v: float) -> str:
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v) -> str:
    return (f"{v:g}".replace(".", ",") + " %") if v is not None else "—"


def _n(n: int, sing: str, plur: str) -> str:
    return f"{n} {sing if n == 1 else plur}"


def generar(ext: dict[str, Any]) -> list[dict[str, Any]]:
    acciones: list[dict[str, Any]] = []
    vals = valores_cuestionario(ext)

    # 1) Cuestionario y señales medidas
    for p in PREGUNTAS:
        v = vals.get(p["id"])
        if not v or v["valor"] not in ("no", "parcial"):
            continue
        if v["origen"] == "declarado":
            porque = "Has indicado que no." if v["valor"] == "no" else "Has indicado que solo en parte."
        else:
            porque = v["detalle"]
        acciones.append({
            "id": f"cu-{p['id']}", "ramas": p["ramas"], "accion": p["accion"], "porque": porque,
            "detalle": [p["ayuda"]], "prioridad": p["prioridad"] if v["valor"] == "no" else BAJAR[p["prioridad"]],
            "esfuerzo": p["esfuerzo"], "origen": "cuestionario" if v["origen"] == "declarado" else v["origen"],
            "verificacion": "Manual (cuestionario)" if v["origen"] == "declarado" else f"Automática ({v['origen']})",
        })

    # 2) Monitor de IA
    ejec = (ext.get("monitor") or {}).get("ejecuciones") or []
    if ejec:
        u = ejec[-1]
        r = u["resumen"]
        sufijo = " (datos simulados de la demo)" if u.get("simulado") else ""
        n = r.get("respuestas") or 0
        for f in r.get("fuentes", []):
            if f["tipo"] != "tercero" or f["respuestas"] < 2 or f["pct"] < 10:
                continue
            if f["con_tu_marca"] / f["respuestas"] >= 0.5:
                continue
            acciones.append({
                "id": f"ia-fuente-{f['dominio']}", "ramas": ["geo"],
                "accion": f"Conseguir presencia en {f['dominio']}",
                "porque": f"La IA cita esta web en el {_pct(f['pct'])} de las respuestas sobre tu sector "
                          f"({f['respuestas']} de {n}) y casi nunca junto a tu marca{sufijo}.",
                "detalle": ["Revisa qué publica (listados, comparativas, guías) y propón tu inclusión con datos "
                            "verificables: precios, casos, reseñas. Si es un foro, participa de forma auténtica.",
                            "Nunca contenido pagado sin marcar como tal."],
                "prioridad": "alta" if f["pct"] >= 25 else "media", "esfuerzo": "medio", "origen": "monitor",
                "verificacion": "Automática: monitor de IA",
            })
            if sum(a["id"].startswith("ia-fuente-") for a in acciones) >= 6:
                break
        huecos = [q for q in r.get("preguntas", []) if q["pct_marca"] == 0 and q["competidores"]]
        for q in huecos[:5]:
            acciones.append({
                "id": "ia-preg-" + hashlib.md5(q["pregunta"].encode()).hexdigest()[:8], "ramas": ["geo"],
                "accion": f"Ganar visibilidad para «{q['pregunta']}»",
                "porque": f"La IA recomienda a {', '.join(q['competidores'])} y no a ti{sufijo}.",
                "detalle": ["Publica en tu web una respuesta completa a esta pregunta, con datos propios.",
                            "Consigue que las webs que cita la IA te mencionen en ese contexto."],
                "prioridad": "media", "esfuerzo": "medio", "origen": "monitor",
                "verificacion": "Automática: monitor de IA",
            })

    # 3) Google Ads
    ads = ext.get("ads") or {}
    tb = ads.get("terminos")
    if tb and tb.get("negativas"):
        neg = tb["negativas"]
        acciones.append({
            "id": "ads-negativas", "ramas": ["sem"],
            "accion": f"Añadir {_n(len(neg), 'término de búsqueda', 'términos de búsqueda')} como palabras clave negativas",
            "porque": f"{_eur(tb['coste_sin_conversion'])} ({_pct(tb['pct_desperdicio'])} del gasto) fueron a búsquedas "
                      "sin ninguna conversión.",
            "detalle": [f"{t['termino']} · {_eur(t['coste'])}" for t in neg[:15]] +
                       (["… y más en el informe exportado"] if len(neg) > 15 else []) +
                       ["Revisa cada término antes de excluirlo: alguno puede convertir con más datos."],
            "prioridad": "alta" if (tb.get("pct_desperdicio") or 0) >= 15 else "media", "esfuerzo": "bajo",
            "origen": "google_ads", "verificacion": "Automática: siguiente importación de términos de búsqueda",
        })
    pk = ads.get("palabras_clave")
    if pk and pk.get("qs_bajo"):
        bajas = pk["qs_bajo"]
        acciones.append({
            "id": "ads-nivel-calidad", "ramas": ["sem"],
            "accion": f"Mejorar el Nivel de Calidad de {_n(len(bajas), 'palabra clave', 'palabras clave')} (4 o menos)",
            "porque": f"El {_pct(pk['pct_coste_qs_bajo'])} del gasto va a palabras clave con Nivel de Calidad bajo: "
                      "pagas más por cada clic.",
            "detalle": [f"{k['palabra']} · NC {int(k['qs'])} · {_eur(k['coste'])}" for k in bajas[:15]] +
                       ["Agrúpalas en grupos de anuncios específicos, repite la palabra clave en el titular y "
                        "envíalas a una landing que hable exactamente de eso."],
            "prioridad": "alta" if (pk.get("pct_coste_qs_bajo") or 0) >= 30 else "media", "esfuerzo": "medio",
            "origen": "google_ads", "verificacion": "Automática: siguiente importación de palabras clave",
        })

    return acciones


def sincronizar(ext: dict[str, Any]) -> list[dict[str, Any]]:
    """Fusiona las acciones generadas con los estados guardados. Modifica `ext["acciones"]`."""
    guardadas: dict[str, dict] = ext.setdefault("acciones", {})
    actuales = generar(ext)
    ids = set()
    vals = valores_cuestionario(ext)
    ahora = datetime.now().isoformat(timespec="seconds")
    res = []
    for a in actuales:
        ids.add(a["id"])
        g = guardadas.setdefault(a["id"], {"estado": "pendiente", "creada": ahora})
        g["snapshot"] = a
        if g.get("verificada"):
            # había quedado resuelta y vuelve a aparecer: se reabre
            g.update(estado="pendiente", verificada=False)
        res.append(a | {k: g.get(k) for k in ("estado", "responsable", "fecha", "evidencia", "nota", "creada")}
                   | {"verificada": False})
    ultima = ((ext.get("monitor") or {}).get("ejecuciones") or [None])[-1]
    for aid, g in guardadas.items():
        if aid in ids or "snapshot" not in g:
            continue
        g["snapshot"]["ramas"] = [r for r in g["snapshot"].get("ramas", []) if r != "seo"]
        if not g["snapshot"]["ramas"]:
            continue
        snap = g["snapshot"]
        resuelta = _resuelta(aid, snap, vals, ultima)
        if resuelta is None:
            continue  # sin datos suficientes: ni abierta ni resuelta (p. ej. pregunta sin responder)
        if resuelta == "abierta":
            res.append(snap | {k: g.get(k) for k in ("estado", "responsable", "fecha", "evidencia", "nota", "creada")}
                       | {"verificada": False})
            continue
        if g.get("estado") != "descartada" and g.get("estado") != "hecho":
            g.update(estado="hecho", fecha_verificada=ahora)
        if resuelta == "verificada" and g.get("estado") == "hecho":
            g["verificada"] = True
        res.append(snap | {k: g.get(k) for k in ("estado", "responsable", "fecha", "evidencia", "nota", "creada")}
                   | {"verificada": bool(g.get("verificada"))})
    res.sort(key=lambda a: (ORDEN_ESTADO.get(a["estado"], 9), ORDEN_PRIORIDAD.get(a["prioridad"], 9),
                            ORDEN_ESFUERZO.get(a["esfuerzo"], 9)))
    return res


def _resuelta(aid: str, snap: dict, vals: dict, ultima: dict | None) -> str | None:
    """Decide qué pasa con una acción que ya no se genera.

    "verificada": un dato medido demuestra que está resuelta.
    "declarada": el usuario la ha dado por buena en el cuestionario (hecha, pero no verificada).
    "abierta": no hay evidencia suficiente de que esté resuelta; se mantiene como estaba.
    None: ya no aplica (p. ej. se quitó la respuesta o la pregunta del monitor).
    """
    if aid in OBSOLETAS or (aid.startswith("cu-") and aid[3:] not in POR_ID):
        return None
    if aid.startswith("cu-"):
        v = vals.get(aid[3:])
        if not v or v["valor"] not in ("si", "na"):
            return None
        return "declarada" if v["origen"] == "declarado" else "verificada"
    if aid.startswith("ia-"):
        if not ultima:
            return "abierta"
        r = ultima.get("resumen", {})
        if aid.startswith("ia-fuente-"):
            dom = aid[len("ia-fuente-"):]
            f = next((x for x in r.get("fuentes", []) if x["dominio"] == dom), None)
            if f and f["respuestas"] >= 2 and f["con_tu_marca"] / f["respuestas"] >= 0.5:
                return "verificada"
            return "abierta"  # que la IA no la cite esta vez no demuestra que ya estés en ella
        q = next((x for x in r.get("preguntas", []) if "ia-preg-" + hashlib.md5(x["pregunta"].encode()).hexdigest()[:8] == aid), None)
        if q is None:
            return None if ultima.get("config", {}).get("preguntas") is not None and snap else "abierta"
        return "verificada" if q["pct_marca"] >= 34 else "abierta"
    return "verificada"  # informes importados: si el problema desaparece del informe nuevo, está resuelto


def actualizar(ext: dict[str, Any], aid: str, cambios: dict[str, Any]) -> None:
    g = ext.setdefault("acciones", {}).setdefault(aid, {"estado": "pendiente"})
    for k in ("estado", "responsable", "fecha", "evidencia", "nota"):
        if k in cambios:
            v = cambios[k]
            if k == "estado" and v not in ORDEN_ESTADO:
                continue
            g[k] = (v or "").strip() if isinstance(v, str) else v
    if cambios.get("estado") in ("pendiente", "en_curso"):
        g["verificada"] = False


def a_csv(acciones: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Acción", "Vertientes", "Por qué", "Detalle", "Prioridad", "Esfuerzo", "Cómo se verifica",
                "Estado", "Verificada", "Responsable", "Fecha", "Evidencia", "Nota"])
    for a in acciones:
        w.writerow([a["accion"], ", ".join(r.upper() for r in a["ramas"]), a["porque"], " | ".join(a.get("detalle") or []),
                    a["prioridad"], a["esfuerzo"], a["verificacion"], a["estado"], "sí" if a.get("verificada") else "",
                    a.get("responsable") or "", a.get("fecha") or "", a.get("evidencia") or "", a.get("nota") or ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


__all__ = ["generar", "sincronizar", "actualizar", "a_csv", "POR_ID"]
