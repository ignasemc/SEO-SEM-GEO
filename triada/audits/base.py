"""Utilidades comunes a las auditorías."""

from __future__ import annotations

from collections import defaultdict

from ..models import Issue, Page, SiteData


def paginas_contenido(site: SiteData) -> list[Page]:
    """Páginas HTML 200 únicas por URL final (descarta duplicados por redirección)."""
    vistas: dict[str, Page] = {}
    for p in site.pages.values():
        if p.status == 200 and "html" in p.content_type and not p.fuera_del_sitio and p.final_url not in vistas:
            vistas[p.final_url] = p
    return list(vistas.values())


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
