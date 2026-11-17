"""Prueba de extremo a extremo de la interfaz con Playwright (opcional, no la ejecuta pytest).

    pip install playwright && playwright install chromium
    python tests/e2e_interfaz.py

Recorre la demostración: panel, ramas, solucionar en carpeta y con archivos subidos, exportaciones,
plan de acciones, datos externos, monitor de IA (simulado) y ajustes. Guarda capturas en ./capturas.
"""

import os
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["TRIADA_DATOS"] = tempfile.mkdtemp(prefix="triada-e2e-")

from playwright.sync_api import sync_playwright  # noqa: E402

from triada.app.server import crear_servidor  # noqa: E402

DEMO = RAIZ / "triada" / "app" / "demo_sitio"
CAPTURAS = RAIZ / "capturas"


def main() -> int:
    CAPTURAS.mkdir(exist_ok=True)
    srv = crear_servidor(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/"
    errores: list[str] = []
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pg = nav.new_page(viewport={"width": 1360, "height": 900}, accept_downloads=True)
        pg.on("console", lambda m: m.type == "error" and errores.append(m.text))
        pg.on("pageerror", lambda e: errores.append(str(e)))
        pg.on("dialog", lambda d: d.accept())

        pg.goto(base)
        pg.click("text=Empezar la demostración")
        pg.wait_for_selector(".score2", timeout=30000)
        pg.screenshot(path=str(CAPTURAS / "1-panel.png"), full_page=True)
        assert "Fuera de la web" not in pg.locator(".score2").first.inner_text(), "SEO debe ser solo interno"

        # 1) Solucionar subiendo archivos HTML y exportar el HTML modificado (sobre el primer análisis)
        pg.goto(base + "#/rama/seo/solucionar")
        pg.check("input[name=dest] >> nth=0")
        tmp = Path(tempfile.mkdtemp())
        for origen, destino in [("index.html", "index.html"), ("productos/silla-roble/index.html", "silla.html")]:
            shutil.copy(DEMO / origen, tmp / destino)
        pg.set_input_files("input[type=file][accept='.html,.htm,.zip']", [str(f) for f in tmp.iterdir()])
        pg.wait_for_selector("text=Archivos HTML subidos (2)", timeout=10000)
        pg.click("button:has-text('Solucionar automáticamente')")
        pg.wait_for_selector(".banner.ok", timeout=10000)
        with pg.expect_download() as d:
            pg.locator(".exportar a:has-text('Archivos subidos')").click()
        assert "silla.html" in zipfile.ZipFile(d.value.path()).namelist()
        pg.screenshot(path=str(CAPTURAS / "2-subidos.png"), full_page=True)

        # 2) Solucionar en carpeta (la demo tiene carpeta) y volver a analizar
        pg.check("input[name=dest] >> nth=1")
        pg.click("button:has-text('Solucionar automáticamente')")
        pg.wait_for_selector("text=Volver a analizar y ver la mejora", timeout=10000)
        pg.click("text=Volver a analizar y ver la mejora")
        pg.wait_for_selector(".delta", timeout=30000)
        pg.screenshot(path=str(CAPTURAS / "3-mejora.png"), full_page=True)

        # Fuera de la web
        for ruta, nombre in [("#/rama/geo/fuera", "4-geo-fuera"), ("#/acciones", "5-acciones"),
                             ("#/externo/cuestionario", "6-cuestionario"), ("#/externo/importar", "7-importar"),
                             ("#/externo/resenas", "8-resenas"), ("#/ajustes", "9-ajustes"), ("#/informes", "10-informes")]:
            pg.goto(base + ruta)
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(CAPTURAS / f"{nombre}.png"), full_page=True)
        pg.goto(base + "#/externo/monitor")
        pg.click("button:has-text('Ejecutar monitor')")
        pg.wait_for_function("!document.querySelector('.progreso')", timeout=20000)
        pg.screenshot(path=str(CAPTURAS / "11-monitor.png"), full_page=True)
        nav.close()
    srv.shutdown()
    if errores:
        print("Errores en la consola del navegador:", *errores, sep="\n  ")
        return 1
    print(f"OK. Capturas en {CAPTURAS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
