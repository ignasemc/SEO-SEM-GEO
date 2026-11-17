"""Interfaz de línea de comandos.

Ejemplos:
    triada auditar https://miempresa.com
    triada auditar https://miempresa.com --ramas seo,geo
    triada auditar https://miempresa.com --combinado --config triada.toml
    triada config-ejemplo > triada.toml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .audits import auditar, puntuaciones
from .audits.base import paginas_contenido
from .config import cargar
from .crawler import rastrear_sync
from .fixes import generar
from .models import RAMAS
from .report import construir_datos, escribir

CONFIG_EJEMPLO = """\
# Configuración de Tríada
[sitio]
url = "https://www.miempresa.com"
marca = "Mi Empresa"
max_paginas = 200
concurrencia = 5

[organizacion]            # se usa para generar el schema Organization (entidad de marca)
nombre_legal = "Mi Empresa S.L."
logo = "https://www.miempresa.com/logo.png"
telefono = "+34 900 000 000"
email = "hola@miempresa.com"
direccion = "Calle Ejemplo 1, 28001 Madrid"
fundacion = "2015"
redes = [
  "https://www.linkedin.com/company/miempresa",
  "https://www.instagram.com/miempresa",
]

# Una entrada por landing de Google Ads
[[sem.campanas]]
landing = "/oferta-reformas"
palabras_clave = ["reformas integrales madrid", "reforma de cocina"]
solo_anuncios = true      # true = página solo para anuncios (se recomendará noindex en modo combinado)
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triada", description="Auditoría y optimización SEO, SEM y GEO")
    ap.add_argument("--version", action="version", version=f"triada {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auditar", help="Rastrea una web y genera informe y correcciones")
    a.add_argument("url", nargs="?", help="URL de la web (o usa la del archivo de configuración)")
    a.add_argument("--ramas", default="seo,sem,geo", help="Vertientes a auditar, separadas por comas")
    a.add_argument("--combinado", action="store_true",
                   help="Optimiza las vertientes a la vez y resuelve conflictos entre ellas")
    a.add_argument("--config", help="Archivo TOML de configuración")
    a.add_argument("--salida", default="informe-triada", help="Carpeta de salida")
    a.add_argument("--max-paginas", type=int, help="Límite de páginas a rastrear")
    a.add_argument("--marca", help="Nombre de la marca (para títulos y schema)")

    sub.add_parser("config-ejemplo", help="Imprime un archivo de configuración de ejemplo")
    ap_app = sub.add_parser("app", help="Abre la aplicación con panel en el navegador")
    ap_app.add_argument("--puerto", type=int, default=8765)
    ap_app.add_argument("--sin-navegador", action="store_true")
    sub.add_parser("conectar-claude", help="Añade Tríada como conector de Claude Desktop")
    sub.add_parser("claude", help="Arranca el conector para Claude (lo lanza Claude, no hace falta a mano)")

    args = ap.parse_args(argv)
    if args.cmd == "config-ejemplo":
        print(CONFIG_EJEMPLO)
        return 0
    if args.cmd == "conectar-claude":
        from .instalar_claude import main as conectar
        return conectar()
    if args.cmd == "claude":
        from .conector_claude import main as conector
        conector()
        return 0
    if args.cmd == "app":
        from .app.server import iniciar
        iniciar(args.puerto, abrir_navegador=not args.sin_navegador)
        return 0

    cfg = cargar(args.config)
    if args.url:
        cfg.url = args.url
    if args.max_paginas:
        cfg.max_paginas = args.max_paginas
    if args.marca:
        cfg.marca = args.marca
    if not cfg.url:
        ap.error("Indica la URL o un archivo de configuración con [sitio] url = ...")

    ramas = [r.strip().lower() for r in args.ramas.split(",") if r.strip()]
    malas = [r for r in ramas if r not in RAMAS]
    if malas:
        ap.error(f"Ramas no válidas: {', '.join(malas)} (usa seo, sem, geo)")

    print(f"Rastreando {cfg.url} (máx. {cfg.max_paginas} páginas)…", file=sys.stderr)
    site = rastrear_sync(cfg)
    total = len(paginas_contenido(site))
    if total == 0:
        print("No se pudo descargar ninguna página HTML. Revisa la URL o si la web bloquea rastreadores.",
              file=sys.stderr)
        return 2

    issues = auditar(site, cfg, set(ramas), args.combinado)
    fixes, archivos = generar(issues, site, cfg)
    datos = construir_datos(site, issues, fixes, ramas, args.combinado, cfg)
    ruta = escribir(Path(args.salida), datos, archivos)

    pts = puntuaciones(issues, total)
    print(f"\n{total} páginas · {len(issues)} problemas · {len(fixes)} correcciones")
    for r in ramas:
        print(f"  {r.upper()}: {pts[r]}/100")
    print(f"\nInforme: {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
