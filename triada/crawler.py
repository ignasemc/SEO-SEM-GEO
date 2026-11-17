"""Rastreador asíncrono: respeta robots.txt, lee sitemaps y recorre enlaces internos."""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .config import Config
from .models import Page, SiteData
from .parser import parsear
from .urls import normalizar, parece_html, path_de


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
        for loc in raiz.iter():
            if not loc.tag.endswith("loc") or not loc.text:
                continue
            u = loc.text.strip()
            if raiz.tag.endswith("sitemapindex"):
                pendientes.append(u)
            elif (n := normalizar(u)):
                site.urls_sitemap.add(n)


async def rastrear(cfg: Config, progreso=None) -> SiteData:
    inicio = normalizar(cfg.url)
    if not inicio:
        raise ValueError(f"URL no válida: {cfg.url}")
    host = urlparse(inicio).netloc
    raiz = f"{urlparse(inicio).scheme}://{host}"
    site = SiteData(url_base=raiz, host=host)

    limits = httpx.Limits(max_connections=cfg.concurrencia)
    async with httpx.AsyncClient(
        headers={"User-Agent": cfg.user_agent, "Accept-Language": "es,en;q=0.8"},
        follow_redirects=True,
        timeout=cfg.timeout,
        limits=limits,
    ) as client:
        # robots.txt
        rp = RobotFileParser()
        r = await _get(client, urljoin(raiz, "/robots.txt"))
        sitemaps_robots: list[str] = []
        if r is not None:
            site.robots_status = r.status_code
            if r.status_code == 200:
                site.robots_txt = r.text
                rp.parse(r.text.splitlines())
                sitemaps_robots = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
            else:
                rp.parse([])
        else:
            rp.parse([])

        await _leer_sitemaps(client, site, sitemaps_robots or [urljoin(raiz, "/sitemap.xml")], cfg.max_paginas)

        cola: list[str] = [inicio] + sorted(u for u in site.urls_sitemap if urlparse(u).netloc == host)
        encolados = set(cola)
        sem = asyncio.Semaphore(cfg.concurrencia)

        async def procesar(url: str) -> Page | None:
            if not rp.can_fetch(cfg.user_agent, url):
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
            if resp.status_code == 200 and "html" in page.content_type:
                parsear(page, resp.text, host)
            return page

        while cola and len(site.pages) < cfg.max_paginas:
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
                    if enlace not in encolados and parece_html(enlace):
                        encolados.add(enlace)
                        cola.append(enlace)

        # comprobar enlaces internos rotos que no se llegaron a rastrear (máx. 50)
        enlazados = {e for p in site.pages.values() for e in p.enlaces_internos if parece_html(e)}
        sin_comprobar = [e for e in enlazados if e not in site.pages][:50]
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


def rastrear_sync(cfg: Config, progreso=None) -> SiteData:
    return asyncio.run(rastrear(cfg, progreso))
