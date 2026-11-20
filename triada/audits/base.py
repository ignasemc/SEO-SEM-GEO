"""Utilidades comunes a las auditorías."""

from __future__ import annotations

from collections import defaultdict

from ..models import Issue, Page, SiteData


def _html_ok(site: SiteData) -> list[Page]:
    """Páginas HTML 200 del sitio, únicas por URL final (descarta duplicados por redirección o codificación)."""
    from ..urls import clave_url

    vistas: dict[str, Page] = {}
    for p in site.pages.values():
        if p.status == 200 and "html" in p.content_type and not p.fuera_del_sitio:
            vistas.setdefault(clave_url(p.final_url), p)
    return list(vistas.values())


def paginas_contenido(site: SiteData) -> list[Page]:
    """Páginas que cuentan como páginas: las canónicas. Una URL con canonical a otra (?per_page=, ?shop_view=…)
    es una variante de esa otra y no se analiza aparte."""
    return [p for p in _html_ok(site) if not p.variante_de and not p.es_paginacion]


def variantes(site: SiteData) -> list[Page]:
    """URLs que declaran como canonical otra URL, o páginas 2, 3… de un listado (se informan, no son problemas)."""
    return [p for p in _html_ok(site) if p.variante_de or p.es_paginacion]


def agrupar_duplicados(paginas: list[Page], clave) -> dict[str, list[str]]:
    grupos: dict[str, list[str]] = defaultdict(list)
    for p in paginas:
        v = clave(p)
        if v:
            grupos[v.strip().lower()].append(p.final_url)
    return {k: v for k, v in grupos.items() if len(v) > 1}


class Coleccion:
    """Acumula issues fusionando los que comparten código."""

    def __init__(self) -> None:
        self._issues: dict[str, Issue] = {}

    def add(self, codigo, ramas, severidad, mensaje, recomendacion, urls=None):
        urls = list(dict.fromkeys(urls or []))
        if codigo in self._issues:
            i = self._issues[codigo]
            i.ramas |= set(ramas)
            i.urls = list(dict.fromkeys(i.urls + urls))
            return
        self._issues[codigo] = Issue(codigo, set(ramas), severidad, mensaje, recomendacion, urls)

    def add_si(self, condicion_urls, *args):
        """Añade el issue solo si hay URLs afectadas."""
        if condicion_urls:
            codigo, ramas, sev, msg, rec = args
            self.add(codigo, ramas, sev, msg, rec, condicion_urls)

    def lista(self) -> list[Issue]:
        return list(self._issues.values())
