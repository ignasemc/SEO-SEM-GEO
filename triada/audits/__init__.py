"""Orquestación de auditorías: modo independiente o combinado."""

from __future__ import annotations

from ..config import Config
from ..models import RAMAS, SEVERIDAD_PESO, Issue, SiteData
from ..urls import path_de
from . import comun, geo, sem, seo
from .base import Coleccion, paginas_contenido

# issues SEO que no tienen sentido en una landing pensada solo para anuncios
SEO_NO_APLICA_LANDING_ADS = {
    "contenido_escaso", "pagina_huerfana", "fuera_sitemap", "titulo_largo", "titulo_corto",
    "meta_desc_falta", "meta_desc_corta", "meta_desc_larga", "canonical_falta", "breadcrumb_falta", "noindex",
}


def auditar(site: SiteData, cfg: Config, ramas: set[str], combinado: bool) -> list[Issue]:
    # Se ejecutan todas las reglas (son baratas) y luego se filtra por rama: así una regla
    # etiquetada {"geo", "seo"} también aparece en una auditoría solo SEO.
    c = Coleccion()
    for modulo in (comun, seo, sem, geo):
        modulo.auditar(site, cfg, c)

    issues = c.lista()
    if combinado:
        issues = resolver_conflictos(issues, site, cfg)
    # quedarse solo con las ramas pedidas
    filtrados = []
    for i in issues:
        i.ramas &= ramas
        if i.ramas:
            filtrados.append(i)
    total = len(paginas_contenido(site))
    filtrados.sort(key=lambda i: i.prioridad(total), reverse=True)
    return filtrados


def resolver_conflictos(issues: list[Issue], site: SiteData, cfg: Config) -> list[Issue]:
    """Ajusta recomendaciones que se contradicen entre vertientes.

    Caso principal: una landing creada solo para anuncios (corta, sin enlaces, orientada a conversión)
    dispara avisos SEO (contenido escaso, huérfana...). Optimizarla para SEO empeoraría la conversión.
    La solución coherente para las tres ramas es marcarla noindex,follow y sacarla del sitemap.
    """
    landings_ads = set()
    landings_ads_indexables = []
    for p in paginas_contenido(site):
        camp = cfg.campana_de(path_de(p.final_url))
        if camp and camp.solo_anuncios:
            landings_ads.add(p.final_url)
            if p.indexable:
                landings_ads_indexables.append(p.final_url)

    resultado = []
    for i in issues:
        if i.codigo in SEO_NO_APLICA_LANDING_ADS and i.urls:
            quitadas = [u for u in i.urls if u in landings_ads]
            if quitadas:
                i.urls = [u for u in i.urls if u not in landings_ads]
                i.nota_conflicto = (f"Se han excluido {len(quitadas)} landing(s) de anuncios: en ellas manda la "
                                    "conversión (SEM), no el posicionamiento orgánico.")
                if not i.urls:
                    continue
        resultado.append(i)

    if landings_ads_indexables:
        resultado.append(Issue(
            codigo="landing_ads_indexable",
            ramas={"seo", "sem"},
            severidad="media",
            mensaje="Landings exclusivas de anuncios que Google puede indexar.",
            recomendacion="Márcalas con <meta name=\"robots\" content=\"noindex, follow\"> y sácalas del sitemap. "
                          "Evitas contenido escaso o duplicado en el índice sin afectar a Google Ads.",
            urls=landings_ads_indexables,
            nota_conflicto="Conflicto SEO↔SEM resuelto a favor de SEM. Si quieres que además posicionen, "
                           "pon solo_anuncios = false en su campaña y Tríada tratará la página como SEO.",
        ))
    return resultado


def puntuaciones(issues: list[Issue], total_paginas: int) -> dict[str, int]:
    """Indicador interno 0-100 por rama (no es una métrica oficial de Google)."""
    res = {}
    for rama in RAMAS:
        penal = 0.0
        for i in issues:
            if rama in i.ramas:
                alcance = 1.0 if not i.urls else min(1.0, len(i.urls) / max(total_paginas, 1))
                penal += SEVERIDAD_PESO[i.severidad] * (0.5 + 0.5 * alcance)
        res[rama] = max(0, round(100 - penal))
    return res
