"""Datos del análisis, informe HTML y archivos exportables."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from . import __version__
from .audits import puntuaciones
from .audits.base import paginas_contenido, variantes
from .audits.geo import BOTS_BUSQUEDA, BOTS_ENTRENAMIENTO, estado_bots
from .config import Config
from .models import RAMAS, Fix, Issue, SiteData

TITULOS = {"seo": "Posicionamiento orgánico", "sem": "Publicidad de pago", "geo": "Buscadores con IA"}


def _uso_bot(bot: str) -> str:
    if bot in ("Googlebot", "Bingbot"):
        return "Buscador clásico (también alimenta AI Overviews / Copilot)"
    if bot in BOTS_BUSQUEDA:
        return "Búsqueda y respuestas de IA (citas)"
    if bot in BOTS_ENTRENAMIENTO:
        return "Entrenamiento de modelos"
    return ""


def construir_datos(site: SiteData, issues: list[Issue], fixes: list[Fix], ramas: list[str], combinado: bool,
                    cfg: Config | None = None) -> dict[str, Any]:
    """Todo el resultado de un análisis en un dict serializable (lo usa la app y el informe)."""
    paginas = paginas_contenido(site)
    total = len(paginas)
    return {
        "url": site.url_base,
        "host": site.host,
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "ramas": ramas,
        "combinado": combinado,
        "total_paginas": total,
        "puntuaciones": puntuaciones(issues, total),
        "issues": [i.to_dict() | {"prioridad": i.prioridad(total)} for i in issues],
        "fixes": [f.to_dict() for f in fixes],
        "paginas": [{
            "url": p.final_url, "status": p.status, "tipo": p.tipo, "title": p.title,
            "meta_description": p.meta_description, "h1": p.h1[0] if p.h1 else None, "palabras": p.palabras,
            "tiempo_ms": p.tiempo_ms, "indexable": p.indexable, "en_sitemap": p.final_url in site.urls_sitemap,
            "enlaces_entrantes": site.enlaces_entrantes.get(p.final_url, 0), "tracking": p.tracking,
            "ctas": len(p.ctas), "formulario": p.tiene_formulario, "autor": p.autor,
            "schema": sorted(p.tipos_schema()), "depende_js": p.depende_js,
        } for p in paginas],
        "bots": estado_bots(site),
        "uso_bots": {b: _uso_bot(b) for b in estado_bots(site)},
        "errores": site.errores_rastreo,
        # URLs con canonical a otra (p. ej. ?per_page=): se informan, no cuentan como páginas ni como problemas
        "variantes": [{"url": p.final_url, "canonical": p.variante_de, "paginacion": p.es_paginacion}
                      for p in variantes(site)],
        "generador_sitemap": site.generador_sitemap,
        "config": asdict(cfg) if cfg else None,
        "version": __version__,
    }


def render_html(datos: dict[str, Any], rama: str | None = None, imprimir: bool = False,
                capas: dict[str, Any] | None = None, acciones: list[dict[str, Any]] | None = None) -> str:
    """Informe HTML. Con `rama` se limita a esa vertiente."""
    ramas = [rama] if rama else list(datos["ramas"])
    issues = [i for i in datos["issues"] if set(i["ramas"]) & set(ramas)]
    fixes = [f for f in datos["fixes"] if not f.get("ramas") or set(f["ramas"]) & set(ramas)]
    env = Environment(loader=PackageLoader("triada", "templates"), autoescape=select_autoescape(["html"]))
    env.tests["contains_rama"] = lambda valor, r: r in valor
    fecha = datetime.fromisoformat(datos["fecha"]).strftime("%d/%m/%Y %H:%M")
    return env.get_template("informe.html").render(
        host=datos["host"], fecha=fecha, total_paginas=datos["total_paginas"], combinado=datos["combinado"],
        ramas=ramas, puntuaciones=datos["puntuaciones"], titulos=TITULOS,
        conteo={r: sum(1 for i in issues if r in i["ramas"]) for r in ramas},
        issues=issues, fixes=fixes, bots=datos["bots"], uso_bots=datos["uso_bots"],
        errores=datos["errores"], version=datos.get("version", __version__), imprimir=imprimir,
        titulo_rama=TITULOS.get(rama) if rama else None, capas=capas, acciones=acciones,
    )


def escribir(salida: Path, datos: dict[str, Any], archivos: dict[str, str]) -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in archivos.items():
        ruta = salida / "archivos" / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(contenido, encoding="utf-8")

    (salida / "analisis.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    (salida / "problemas.json").write_text(json.dumps(datos["issues"], ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    (salida / "correcciones.json").write_text(json.dumps(datos["fixes"], ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    escribir_csv(salida / "correcciones.csv", datos["fixes"])
    ruta = salida / "informe.html"
    ruta.write_text(render_html(datos), encoding="utf-8")
    for r in datos["ramas"]:
        (salida / f"informe-{r}.html").write_text(render_html(datos, r), encoding="utf-8")
    return ruta


def escribir_csv(ruta: Path, fixes: list[dict[str, Any]]) -> None:
    with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["id", "ramas", "url", "problema", "tipo", "valor_actual", "valor_propuesto",
                    "requiere_revision", "nota", "archivo"])
        for f in fixes:
            w.writerow([f.get("id", ""), ",".join(f.get("ramas", [])), f["url"] or "", f["codigo_issue"], f["tipo"],
                        f["valor_actual"] or "", f["valor_propuesto"], "sí" if f["requiere_revision"] else "no",
                        f["nota"], f["archivo"] or ""])


__all__ = ["construir_datos", "render_html", "escribir", "escribir_csv", "RAMAS"]
