"""Comprobaciones que afectan a varias vertientes a la vez."""

from __future__ import annotations

from ..config import Config
from ..models import SiteData
from .base import Coleccion, paginas_contenido

UMBRAL_MS = 800          # respuesta del servidor (aprox. TTFB + descarga del HTML)
UMBRAL_BYTES = 1_500_000  # peso del HTML


def auditar(site: SiteData, cfg: Config, c: Coleccion) -> None:
    paginas = paginas_contenido(site)

    if site.url_base.startswith("http://"):
        c.add("sin_https", {"seo", "sem", "geo"}, "critica",
              "El sitio se sirve por HTTP, sin cifrado.",
              "Instala un certificado TLS y redirige todo el tráfico a HTTPS con 301.")

    rotas = [p.url for p in site.pages.values() if p.status >= 400]
    c.add_si(rotas, "enlace_roto", {"seo", "sem"}, "alta",
             "Hay URLs enlazadas internamente que devuelven error (4xx/5xx).",
             "Corrige o elimina los enlaces, o redirige con 301 a la página equivalente.")

    lentas = [p.final_url for p in paginas if p.tiempo_ms > UMBRAL_MS or p.bytes > UMBRAL_BYTES]
    c.add_si(lentas, "respuesta_lenta", {"seo", "sem"}, "media",
             f"Páginas con respuesta del servidor > {UMBRAL_MS} ms o HTML > 1,5 MB.",
             "Activa caché/CDN, reduce el HTML y revisa Core Web Vitals (LCP ≤ 2,5 s, INP ≤ 200 ms, "
             "CLS ≤ 0,1) en PageSpeed Insights. Afecta al ranking y al Nivel de Calidad de Ads.")

    sin_viewport = [p.final_url for p in paginas if not p.viewport]
    c.add_si(sin_viewport, "sin_viewport", {"seo", "sem"}, "alta",
             "Páginas sin meta viewport: no se adaptan al móvil.",
             'Añade <meta name="viewport" content="width=device-width, initial-scale=1">.')

    js = [p.final_url for p in paginas if p.depende_js]
    c.add_si(js, "depende_js", {"seo", "geo"}, "alta",
             "El contenido principal solo aparece tras ejecutar JavaScript.",
             "Usa renderizado en servidor (SSR) o pre-renderizado. Muchos rastreadores de IA no ejecutan JS.")

    # schema de producto: SEO (resultados enriquecidos), SEM (coherencia con Merchant Center), GEO (datos citables)
    incompletos = []
    for p in paginas:
        if p.tipo != "producto":
            continue
        nodos = p.nodos_schema("Product")
        if not nodos:
            incompletos.append(p.final_url)
            continue
        n = nodos[0]
        offers = n.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not (n.get("name") and n.get("image") and offers.get("price") and offers.get("availability")):
            incompletos.append(p.final_url)
    c.add_si(incompletos, "schema_producto", {"seo", "sem", "geo"}, "alta",
             "Fichas de producto sin schema Product completo (nombre, imagen, precio, disponibilidad).",
             "Añade JSON-LD Product con offers.price, priceCurrency y availability. Debe coincidir "
             "con el feed de Merchant Center.")
