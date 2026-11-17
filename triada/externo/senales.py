"""Señales externas medibles: reseñas de Google (Places API) y entidad en Wikidata."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any

import httpx

from .monitor import ErrorMotor

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
# rating y userRatingCount pertenecen al SKU "Enterprise" de Text Search (más caro): solo pedimos lo necesario
PLACES_CAMPOS = "places.displayName,places.rating,places.userRatingCount,places.googleMapsUri,places.formattedAddress"
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
UA = "TriadaBot/0.3 (herramienta local de auditoria; contacto: usuario local)"


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def buscar_lugar(consulta: str, clave: str, cliente: httpx.Client) -> dict[str, Any] | None:
    try:
        r = cliente.post(PLACES_URL, headers={"Content-Type": "application/json", "X-Goog-Api-Key": clave,
                                              "X-Goog-FieldMask": PLACES_CAMPOS},
                         json={"textQuery": consulta, "languageCode": "es"}, timeout=30)
    except httpx.HTTPError as e:
        raise ErrorMotor(f"Error de conexión con Google Places: {e}") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message")
        except ValueError:
            msg = r.text[:200]
        raise ErrorMotor(f"Google Places HTTP {r.status_code}: {msg}")
    lugares = r.json().get("places") or []
    if not lugares:
        return None
    p = lugares[0]
    return {"consulta": consulta, "nombre": (p.get("displayName") or {}).get("text", ""),
            "direccion": p.get("formattedAddress", ""), "nota": p.get("rating"),
            "resenas": p.get("userRatingCount") or 0, "url": p.get("googleMapsUri", "")}


def resenas(consulta: str, competidores: list[dict], clave: str, cliente: httpx.Client | None = None,
            simulado: bool = False) -> dict[str, Any]:
    if simulado:
        tu = {"consulta": consulta, "nombre": "Reformas Norte (ejemplo)", "direccion": "Madrid", "nota": 4.1,
              "resenas": 23, "url": ""}
        comps = [{"consulta": c["nombre"], "nombre": c["nombre"], "direccion": "Madrid", "nota": n, "resenas": k,
                  "url": ""} for c, (n, k) in zip(competidores, [(4.6, 210), (4.4, 96), (4.7, 154), (4.2, 61)])]
        return {"fecha": datetime.now().isoformat(timespec="seconds"), "simulado": True, "tu": tu,
                "competidores": comps}
    if not clave:
        raise ErrorMotor("Añade la clave de Google Places en Ajustes.")
    propio = cliente is None
    cliente = cliente or httpx.Client()
    try:
        tu = buscar_lugar(consulta, clave, cliente)
        comps = []
        for c in competidores[:6]:
            q = c.get("consulta") or c.get("nombre")
            if q:
                lugar = buscar_lugar(q, clave, cliente)
                if lugar:
                    comps.append(lugar)
    finally:
        if propio:
            cliente.close()
    return {"fecha": datetime.now().isoformat(timespec="seconds"), "simulado": False, "tu": tu,
            "competidores": comps}


def valor_resenas(r: dict[str, Any] | None) -> tuple[str | None, str]:
    """Convierte las reseñas medidas en un valor del cuestionario (criterio de Tríada)."""
    if not r:
        return None, ""
    tu = r.get("tu")
    if not tu:
        return "no", "No se encontró tu empresa en Google Maps con esa búsqueda."
    n, nota = tu.get("resenas") or 0, tu.get("nota") or 0
    comps = [c["resenas"] for c in r.get("competidores", []) if c.get("resenas")]
    ref = sorted(comps)[len(comps) // 2] if comps else 50
    txt = f"{n} reseñas, nota {nota:.1f}".replace(".", ",") + (f" · mediana de competidores: {ref}" if comps else "")
    if n >= ref and nota >= 4.3:
        return "si", txt
    if n >= ref / 3 and nota >= 4.0:
        return "parcial", txt
    return "no", txt


def wikidata(marca: str, cliente: httpx.Client | None = None, simulado: bool = False) -> dict[str, Any]:
    if simulado:
        return {"fecha": datetime.now().isoformat(timespec="seconds"), "consulta": marca, "candidatos": [],
                "coincide": False, "simulado": True}
    if not marca.strip():
        raise ErrorMotor("Indica el nombre de la marca.")
    propio = cliente is None
    cliente = cliente or httpx.Client()
    try:
        r = cliente.get(WIKIDATA_URL, params={"action": "wbsearchentities", "search": marca, "language": "es",
                                              "uselang": "es", "type": "item", "limit": 7, "format": "json"},
                        headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        datos = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ErrorMotor(f"No se pudo consultar Wikidata: {e}") from e
    finally:
        if propio:
            cliente.close()
    cands = [{"id": c.get("id"), "nombre": c.get("label", ""), "descripcion": c.get("description", ""),
              "url": f"https://www.wikidata.org/wiki/{c.get('id')}"} for c in datos.get("search", [])]
    return {"fecha": datetime.now().isoformat(timespec="seconds"), "consulta": marca, "candidatos": cands,
            "coincide": any(_norm(c["nombre"]) == _norm(marca) for c in cands), "simulado": False}
