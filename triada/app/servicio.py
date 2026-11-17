"""Capa de servicio de la app: ejecutar análisis y guardar/leer resultados en disco."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..audits import auditar
from ..audits.base import paginas_contenido
from ..config import Campana, Config, Organizacion
from ..crawler import rastrear_sync
from ..fixes import generar
from ..externo import almacen
from ..externo import servicio as externo
from ..report import construir_datos, escribir


def carpeta_datos() -> Path:
    ruta = Path(os.environ.get("TRIADA_DATOS", Path.home() / "Triada"))
    (ruta / "analisis").mkdir(parents=True, exist_ok=True)
    (ruta / "aplicaciones").mkdir(parents=True, exist_ok=True)
    return ruta


def nuevo_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def config_desde_dict(d: dict[str, Any]) -> Config:
    cfg = Config()
    cfg.url = (d.get("url") or "").strip()
    if cfg.url and not cfg.url.startswith(("http://", "https://")):
        cfg.url = "https://" + cfg.url
    cfg.marca = (d.get("marca") or "").strip()
    cfg.max_paginas = max(1, min(int(d.get("max_paginas") or 200), 2000))
    org = d.get("organizacion") or {}
    cfg.organizacion = Organizacion(**{k: org.get(k, "") if k != "redes" else list(org.get(k) or [])
                                       for k in Organizacion.__dataclass_fields__})
    for c in d.get("campanas") or []:
        if not c.get("landing"):
            continue
        kws = c.get("palabras_clave") or []
        if isinstance(kws, str):
            kws = [k.strip() for k in kws.split(",") if k.strip()]
        cfg.campanas.append(Campana(landing=c["landing"].strip(), palabras_clave=kws,
                                    solo_anuncios=bool(c.get("solo_anuncios", True))))
    return cfg


def ejecutar_analisis(peticion: dict[str, Any], progreso=None) -> str:
    """Rastrea, audita y guarda. Devuelve el id del análisis."""
    cfg = config_desde_dict(peticion)
    ramas = [r for r in (peticion.get("ramas") or ["seo", "sem", "geo"]) if r in ("seo", "sem", "geo")]
    combinado = bool(peticion.get("combinado", True))
    site = rastrear_sync(cfg, progreso)
    if not paginas_contenido(site):
        raise RuntimeError("No se pudo descargar ninguna página HTML. Revisa la URL o si la web bloquea rastreadores.")
    issues = auditar(site, cfg, set(ramas), combinado)
    fixes, archivos = generar(issues, site, cfg)
    datos = construir_datos(site, issues, fixes, ramas, combinado, cfg)
    aid = nuevo_id()
    datos["id"] = aid
    datos["carpeta_sitio"] = peticion.get("carpeta_sitio") or ""
    datos["demo"] = bool(peticion.get("demo"))
    datos["proyecto"] = almacen.clave_proyecto(site.host, datos["demo"])
    escribir(carpeta_datos() / "analisis" / aid, datos, archivos)
    externo.asegurar_perfil(datos["proyecto"], cfg.marca, "" if datos["demo"] else site.host)
    return aid


def leer_analisis(aid: str) -> dict[str, Any] | None:
    if not aid or "/" in aid or ".." in aid:
        return None
    ruta = carpeta_datos() / "analisis" / aid / "analisis.json"
    if not ruta.exists():
        return None
    a = json.loads(ruta.read_text(encoding="utf-8"))
    a.setdefault("proyecto", almacen.clave_proyecto(a["host"], bool(a.get("demo"))))
    return a


def carpeta_analisis(aid: str) -> Path:
    return carpeta_datos() / "analisis" / aid


def listar_analisis() -> list[dict[str, Any]]:
    res = []
    for d in sorted((carpeta_datos() / "analisis").iterdir(), reverse=True):
        f = d / "analisis.json"
        if not f.exists():
            continue
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        a.setdefault("proyecto", almacen.clave_proyecto(a.get("host", ""), bool(a.get("demo"))))
        res.append({k: a.get(k) for k in ("id", "host", "url", "fecha", "ramas", "combinado", "puntuaciones",
                                          "total_paginas", "demo", "proyecto")}
                   | {"problemas": len(a.get("issues", [])), "correcciones": len(a.get("fixes", []))})
    return res


def borrar_analisis(aid: str) -> bool:
    if leer_analisis(aid) is None:
        return False
    shutil.rmtree(carpeta_analisis(aid))
    return True
