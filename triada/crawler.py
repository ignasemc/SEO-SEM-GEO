"""Rastreador asíncrono: respeta robots.txt, lee sitemaps y recorre enlaces internos."""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urljoin, urlparse

import httpx

from .config import Config
from .models import Page, SiteData
from .parser import parsear
from .urls import ReglasRobots, clave_url, mismo_sitio, normalizar, parece_html, path_de, tiene_parametros

MAX_VARIANTES = 2  # variantes con parámetros que se comprueban por ruta


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await client.get(url)
    except httpx.HTTPError:
        return None


async def _leer_sitemaps(client: httpx.AsyncClient, site: SiteData, iniciales: list[str], limite: int) -> None:
    pendientes = list(dict.fromkeys(iniciales))
    vistos: set[str] = set()
    while pendientes and len(site.urls_sitemap) < limite * 5 and len(vistos) < 50:
        sm = pendientes.pop(0)
        if sm in vistos:
            continue
        vistos.add(sm)
        r = await _get(client, sm)
        if r is None or r.status_code != 200:
            continue
        site.sitemaps.append(sm)
        try:
            raiz = ET.fromstring(r.content)
        except ET.ParseError:
            site.errores_rastreo.append(f"Sitemap mal formado: {sm}")
            continue
        texto = r.text[:5000] + r.text[-500:]
        if not site.generador_sitemap:
            site.generador_sitemap = _generador(sm, texto)
        indice = _local(raiz.tag) == "sitemapindex"
        # solo <url><loc> y <sitemap><loc>: las <image:loc> de Yoast/WooCommerce son imágenes, no páginas
        for entrada in raiz:
            if _local(entrada.tag) not in ("url", "sitemap"):
                continue
            loc = next((h for h in entrada if _local(h.tag) == "loc" and h.text), None)
            if loc is None:
                continue
            u = loc.text.strip()
            if indice:
                pendientes.append(u)
            elif (n := normalizar(u)):
                site.urls_sitemap.add(n)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _generador(url: str, texto: str) -> str | None:
    """Qué genera el sitemap del sitio (si es un plugin, el sitemap se ajusta ahí, no se sustituye)."""
    t = texto.lower()
    if "yoast" in t:
        return "Yoast SEO"
    if "rank math" in t or "rankmath" in t:
        return "Rank Math"
    if "all in one seo" in t or "aioseo" in t:
        return "All in One SEO"
    if "seopress" in t:
        return "SEOPress"
    if "wp-sitemap" in url or "wp-sitemap" in t:
        return "WordPress"
    if "shopify" in t:
        return "Shopify"
    return None


async def rastrear(cfg: Config, progreso=None, transport: httpx.AsyncBaseTransport | None = None) -> SiteData:
    """Rastrea la web. `transport` permite grabar o reproducir un rastreo (tests con fixtures)."""
    inicio = normalizar(cfg.url)
    if not inicio:
        raise ValueError(f"URL no válida: {cfg.url}")
    limits = httpx.Limits(max_connections=cfg.concurrencia)
    async with httpx.AsyncClient(
        headers={"User-Agent": cfg.user_agent, "Accept-Language": "es,en;q=0.8"},
        follow_redirects=True,
        timeout=cfg.timeout,
        limits=limits,
        transport=transport,
    ) as client:
        # El sitio es el dominio de la URL indicada. Si esa URL redirige a otro dominio (p. ej. cambio de
        # marca o de http a https), se toma el de destino. Nunca se rastrean páginas de otros dominios.
        r0 = await _get(client, inicio)
        if r0 is not None and r0.history and (destino := normalizar(str(r0.url))):
            inicio = destino
        host = urlparse(inicio).netloc
        raiz = f"{urlparse(inicio).scheme}://{host}"
        site = SiteData(url_base=raiz, host=host)
        # robots.txt
        r = await _get(client, urljoin(raiz, "/robots.txt"))
        sitemaps_robots: list[str] = []
        if r is not None:
            site.robots_status = r.status_code
            if r.status_code == 200:
                site.robots_txt = r.text
                sitemaps_robots = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
        rp = ReglasRobots(site.robots_txt)

        await _leer_sitemaps(client, site, sitemaps_robots or [urljoin(raiz, "/sitemap.xml")], cfg.max_paginas)

        cola: list[str] = [inicio] + sorted(u for u in site.urls_sitemap if mismo_sitio(u, host) and parece_html(u))
        encolados = {clave_url(u) for u in cola}
        # Las variantes con parámetros (?per_page=, ?shop_view=, ?product-page=…) van al final y como mucho
        # MAX_VARIANTES por ruta: suelen tener canonical a la página base y solo hace falta comprobarlo.
        variantes: list[str] = []
        por_ruta: dict[str, int] = {}
        sem = asyncio.Semaphore(cfg.concurrencia)

        async def procesar(url: str) -> Page | None:
            if not rp.permite(cfg.user_agent, url):
                site.errores_rastreo.append(f"Bloqueada por robots.txt para el rastreador: {url}")
                return None
            async with sem:
                t0 = time.perf_counter()
                resp = await _get(client, url)
                ms = int((time.perf_counter() - t0) * 1000)
            if resp is None:
                site.errores_rastreo.append(f"Sin respuesta: {url}")
                return None
            page = Page(
                url=url,
                status=resp.status_code,
                final_url=normalizar(str(resp.url)) or str(resp.url),
                redirecciones=[str(h.url) for h in resp.history],
                tiempo_ms=ms,
                bytes=len(resp.content),
                content_type=resp.headers.get("content-type", ""),
                x_robots=resp.headers.get("x-robots-tag", "").lower(),
            )
            if not mismo_sitio(page.final_url, host):
                # una URL del sitio que redirige a otra web (otra empresa, red social, tienda externa...):
                # se anota, pero esa otra web no se analiza ni se corrige
                page.fuera_del_sitio = True
                page.content_type = ""
                site.errores_rastreo.append(f"Redirige fuera del sitio (no se analiza): {url} → {page.final_url}")
                return page
            if resp.status_code == 200 and "html" in page.content_type:
                parsear(page, resp.text, host)
            return page

        while (cola or variantes) and len(site.pages) < cfg.max_paginas:
            if not cola:
                cola, variantes = variantes, []
            lote = cola[: cfg.concurrencia]
            cola = cola[cfg.concurrencia :]
            resultados = await asyncio.gather(*(procesar(u) for u in lote))
            for page in resultados:
                if page is None:
                    continue
                site.pages[page.url] = page
                if progreso:
                    progreso(len(site.pages))
                for enlace in page.enlaces_internos:
                    clave = clave_url(enlace)
                    if clave in encolados or not parece_html(enlace):
                        continue
                    encolados.add(clave)
                    if tiene_parametros(enlace):
                        ruta = urlparse(enlace).path
                        if por_ruta.get(ruta, 0) < MAX_VARIANTES:
                            por_ruta[ruta] = por_ruta.get(ruta, 0) + 1
                            variantes.append(enlace)
                    else:
                        cola.append(enlace)

        # comprobar enlaces internos rotos que no se llegaron a rastrear (máx. 50)
        enlazados = {e for p in site.pages.values() for e in p.enlaces_internos if parece_html(e)}
        rastreadas = {clave_url(u) for u in site.pages}
        sin_comprobar = sorted(e for e in enlazados if clave_url(e) not in rastreadas and not tiene_parametros(e))[:50]
        for page in await asyncio.gather(*(procesar(u) for u in sin_comprobar)):
            if page is not None:
                site.pages[page.url] = page

    # enlaces entrantes (por URL final)
    conteo: Counter[str] = Counter()
    for p in site.pages.values():
        for e in set(p.enlaces_internos):
            if e != p.url:
                conteo[e] += 1
    finales = {p.url: p.final_url for p in site.pages.values()}
    site.enlaces_entrantes = {}
    for u, n in conteo.items():
        destino = finales.get(u, u)
        site.enlaces_entrantes[destino] = site.enlaces_entrantes.get(destino, 0) + n

    # marcar landings de campañas
    for p in site.pages.values():
        if cfg.campana_de(path_de(p.final_url)):
            p.tipo = "landing"
    return site


def rastrear_sync(cfg: Config, progreso=None, transport: httpx.AsyncBaseTransport | None = None) -> SiteData:
    return asyncio.run(rastrear(cfg, progreso, transport))
