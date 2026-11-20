"""Análisis de petground.es reproduciendo el rastreo grabado el 20/11/2026 (sin internet)."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ), str(RAIZ / "tests")]

FIXTURE = RAIZ / "tests" / "fixtures" / "petground-2026-11-20.jsonl.gz"


def analizar(max_paginas: int = 400):
    from grabacion import Reproductor

    from triada.audits import auditar
    from triada.audits.base import paginas_contenido
    from triada.config import Config
    from triada.crawler import rastrear_sync
    from triada.fixes import generar

    cfg = Config(url="https://petground.es", marca="PetGround", max_paginas=max_paginas)
    site = rastrear_sync(cfg, transport=Reproductor(FIXTURE))
    issues = auditar(site, cfg, {"seo", "sem", "geo"}, True)
    fixes, archivos = generar(issues, site, cfg)
    return site, issues, fixes, archivos, paginas_contenido(site)


def resumen(site, issues, fixes, archivos, paginas) -> dict:
    return {
        "urls_rastreadas": len(site.pages),
        "paginas_analizadas": len(paginas),
        "problemas": {i.codigo: len(i.urls) for i in issues},
        "correcciones": dict(Counter(f.tipo.split(":")[0] for f in fixes)),
        "total_correcciones": len(fixes),
        "sitemap_con_parametros": archivos.get("sitemap.xml", "").count("?"),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(resumen(*analizar()), ensure_ascii=False, indent=1))
