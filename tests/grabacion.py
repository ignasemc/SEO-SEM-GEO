"""Grabar y reproducir rastreos reales para usarlos como fixtures (sin internet).

    python tests/grabacion.py https://petground.es tests/fixtures/petground-2026-11-20.jsonl.gz 400

La grabación solo hace peticiones GET de lectura, como cualquier visitante: no modifica la web.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

import httpx

CABECERAS = ("content-type", "location", "x-robots-tag", "link")
MARCAS = re.compile(r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]+|G-[A-Z0-9]{6,}|AW-\d{6,}"
                    r"|gtag\(\s*['\"]consent['\"][^)]*\)|fbq\(")


def aligerar(r: dict) -> dict:
    """Reduce el tamaño de la grabación sin cambiar lo que Tríada analiza: sin contenido de imágenes ni estilos, y
    los scripts grandes se sustituyen por sus marcas de seguimiento (GTM, GA4, Ads, Consent Mode, píxel de Meta).
    Los JSON-LD se conservan enteros."""
    ct = r["headers"].get("content-type", "")
    if not any(t in ct for t in ("html", "xml", "text/plain")):
        return r | {"body": ""}
    if "html" not in ct:
        return r

    def script(m):
        abre, cuerpo, cierra = m.group(1), m.group(2), m.group(3)
        if "ld+json" in abre.lower() or len(cuerpo) < 1500:
            return m.group(0)
        return abre + " ".join(dict.fromkeys(x.group(0) for x in MARCAS.finditer(cuerpo))) + cierra

    b = re.sub(r"(<script\b[^>]*>)(.*?)(</script>)", script, r["body"], flags=re.S | re.I)
    b = re.sub(r"(<style\b[^>]*>)(.*?)(</style>)", r"\1\3", b, flags=re.S | re.I)
    return r | {"body": b}


class Grabadora(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        import os

        # un transporte propio no lee el proxy del sistema: se le pasa explícitamente si lo hay
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self.real = httpx.AsyncHTTPTransport(retries=1, proxy=proxy) if proxy else httpx.AsyncHTTPTransport(retries=1)
        self.respuestas: dict[str, dict] = {}

    async def handle_async_request(self, peticion: httpx.Request) -> httpx.Response:
        r = await self.real.handle_async_request(peticion)
        cuerpo = await r.aread()
        self.respuestas[str(peticion.url)] = {
            "url": str(peticion.url), "status": r.status_code,
            "headers": {k: v for k, v in r.headers.items() if k.lower() in CABECERAS},
            "body": cuerpo.decode("utf-8", errors="replace"),
        }
        # el cuerpo ya está descomprimido: no se reenvían las cabeceras de codificación ni de longitud
        cabeceras = {k: v for k, v in r.headers.items()
                     if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
        return httpx.Response(r.status_code, headers=cabeceras, content=cuerpo, request=peticion)

    def guardar(self, ruta: Path) -> None:
        with gzip.open(ruta, "wt", encoding="utf-8", compresslevel=9) as f:
            for r in self.respuestas.values():
                f.write(json.dumps(aligerar(r), ensure_ascii=False) + "\n")


class Reproductor(httpx.AsyncBaseTransport):
    """Sirve las respuestas grabadas; lo que no se grabó responde 404."""

    def __init__(self, ruta: Path) -> None:
        self.respuestas = {}
        with gzip.open(ruta, "rt", encoding="utf-8") as f:
            for linea in f:
                r = json.loads(linea)
                self.respuestas[r["url"]] = r

    async def handle_async_request(self, peticion: httpx.Request) -> httpx.Response:
        r = self.respuestas.get(str(peticion.url))
        if r is None:
            return httpx.Response(404, request=peticion)
        return httpx.Response(r["status"], headers=r["headers"], content=r["body"].encode("utf-8"), request=peticion)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from triada.config import Config
    from triada.crawler import rastrear_sync

    url, salida = sys.argv[1], Path(sys.argv[2])
    g = Grabadora()
    site = rastrear_sync(Config(url=url, max_paginas=int(sys.argv[3]) if len(sys.argv) > 3 else 400), transport=g)
    g.guardar(salida)
    print(f"{len(site.pages)} páginas rastreadas · {len(g.respuestas)} respuestas grabadas en {salida} "
          f"({salida.stat().st_size / 1e6:.1f} MB)")
