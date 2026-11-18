"""Persistencia por proyecto (una empresa/web) y ajustes globales, en la carpeta de datos local."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_bloqueo = threading.RLock()

PESOS_DEFECTO = {"sem": 0.4, "geo": 0.3}  # peso de "en la web"; el resto es "fuera de la web". SEO es solo interno
MODELOS_DEFECTO = {"gemini": "gemini-3.8-flash", "openai": "gpt-5.5", "perplexity": "sonar", "claude": "claude-opus-5-5"}
CLAVES = ("gemini", "openai", "perplexity", "places", "anthropic")


def carpeta_datos() -> Path:
    ruta = Path(os.environ.get("TRIADA_DATOS", Path.home() / "Triada"))
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def clave_proyecto(url_o_host: str, demo: bool = False) -> str:
    if demo:
        return "demo"
    host = urlparse(url_o_host).netloc if "://" in url_o_host else url_o_host
    host = host.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return re.sub(r"[^a-z0-9.\-]", "_", host) or "sin-nombre"


def _ruta(proyecto: str) -> Path:
    if not re.fullmatch(r"[a-z0-9.\-_]+", proyecto) or ".." in proyecto:
        raise ValueError("Proyecto no válido")
    d = carpeta_datos() / "proyectos" / proyecto
    d.mkdir(parents=True, exist_ok=True)
    return d / "externo.json"


def vacio() -> dict[str, Any]:
    return {
        "perfil": {"marca": "", "alias": [], "dominio": "", "ciudad": ""},
        "cuestionario": {},
        "ads": {},
        "search_console": {},
        "monitor": {"config": {"preguntas": [], "competidores": [], "motores": ["gemini"], "repeticiones": 2},
                    "ejecuciones": []},
        "resenas": None,
        "wikidata": None,
        "acciones": {},
    }


def leer(proyecto: str) -> dict[str, Any]:
    with _bloqueo:
        ruta = _ruta(proyecto)
        if not ruta.exists():
            return vacio()
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        base = vacio()
        base.update(datos)
        return base


def guardar(proyecto: str, datos: dict[str, Any]) -> None:
    with _bloqueo:
        ruta = _ruta(proyecto)
        tmp = ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(ruta)


def modificar(proyecto: str, fn) -> dict[str, Any]:
    """Lee, aplica `fn(datos)` y guarda de forma atómica."""
    with _bloqueo:
        d = leer(proyecto)
        fn(d)
        guardar(proyecto, d)
        return d


# ------------------------------------------------------------------ ajustes globales

def _ruta_ajustes() -> Path:
    return carpeta_datos() / "ajustes.json"


def leer_ajustes() -> dict[str, Any]:
    a = {"claves": {}, "modelos": dict(MODELOS_DEFECTO), "pesos": dict(PESOS_DEFECTO)}
    r = _ruta_ajustes()
    if r.exists():
        guardado = json.loads(r.read_text(encoding="utf-8"))
        a["claves"].update(guardado.get("claves", {}))
        a["modelos"].update(guardado.get("modelos", {}))
        a["pesos"].update(guardado.get("pesos", {}))
    return a


def guardar_ajustes(nuevos: dict[str, Any]) -> dict[str, Any]:
    with _bloqueo:
        a = leer_ajustes()
        for k, v in (nuevos.get("claves") or {}).items():
            if k in CLAVES and v is not None:
                a["claves"][k] = v.strip()
        for k, v in (nuevos.get("modelos") or {}).items():
            if k in MODELOS_DEFECTO and v:
                a["modelos"][k] = v.strip()
        for k, v in (nuevos.get("pesos") or {}).items():
            if k in PESOS_DEFECTO and v is not None:
                a["pesos"][k] = min(1.0, max(0.0, float(v)))
        _ruta_ajustes().write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")
        return a


def ajustes_publicos() -> dict[str, Any]:
    """Ajustes sin exponer las claves (solo si están configuradas)."""
    a = leer_ajustes()
    return {"claves": {k: bool(a["claves"].get(k)) for k in CLAVES},
            "modelos": a["modelos"], "pesos": a["pesos"]}
