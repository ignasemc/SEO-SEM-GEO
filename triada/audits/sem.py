"""Auditoría SEM: medición y páginas de destino de campañas de pago."""

from __future__ import annotations

import unicodedata

from ..config import Config
from ..models import SiteData
from ..urls import path_de
from .base import Coleccion, paginas_contenido

S = {"sem"}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def landings(site: SiteData, cfg: Config):
    """Landings declaradas en la config; si no hay, se usan productos y servicios como candidatas."""
    paginas = paginas_contenido(site)
    declaradas = [p for p in paginas if p.tipo == "landing"]
    if declaradas or cfg.campanas:
        return declaradas, True
    return [p for p in paginas if p.tipo in ("producto", "servicio")], False


def auditar(site: SiteData, cfg: Config, c: Coleccion) -> None:
    paginas = paginas_contenido(site)
    destino, declaradas = landings(site, cfg)

    if not declaradas:
        c.add("sin_campanas", S, "baja",
              "No hay campañas configuradas: se han revisado productos y servicios como posibles landings.",
              "Declara tus landings y palabras clave en triada.toml ([[sem.campanas]]) para comprobar la "
              "coherencia anuncio-página.")

    # landings declaradas que no se pudieron rastrear o dan error
    for camp in cfg.campanas:
        objetivo = camp.landing.rstrip("/") or "/"
        encontrada = [p for p in site.pages.values() if path_de(p.final_url).rstrip("/") == objetivo.rstrip("/")
                      or path_de(p.url).rstrip("/") == objetivo.rstrip("/")]
        if not encontrada:
            c.add("landing_no_encontrada", S, "alta", "Landings de campaña que no se encontraron al rastrear.",
                  "Comprueba la URL y enlázala o añádela al rastreo.", [camp.landing])
        elif all(p.status != 200 for p in encontrada):
            c.add("landing_error", S, "critica", "Landings de campaña que devuelven error.",
                  "Corrígelas ya: Google rechaza anuncios con destino roto y pagas clics perdidos.",
                  [encontrada[0].url])

    # --- medición
    home = site.home()
    revisadas = list({p.final_url: p for p in ([home] if home else []) + destino}.values())
    if paginas and not any(p.tracking.get("gtm") or p.tracking.get("ga4") or p.tracking.get("google_ads")
                           for p in paginas):
        c.add("sin_etiquetado", S, "critica", "No se detecta Google Tag Manager, GA4 ni etiqueta de Google Ads.",
              "Instala la etiqueta de Google (gtag.js) o GTM y configura las conversiones principales "
              "(venta o lead cualificado) como objetivo primario.")
    else:
        sin_ads = [p.final_url for p in destino if not (p.tracking.get("google_ads") or p.tracking.get("gtm"))]
        c.add_si(sin_ads, "landing_sin_ads_tag", S, "alta", "Landings sin etiqueta de Google Ads ni GTM.",
                 "Añade la etiqueta de conversiones de Google Ads (AW-…) o carga GTM en estas páginas.")
        sin_consent = [p.final_url for p in revisadas
                       if (p.tracking.get("ga4") or p.tracking.get("google_ads")) and not p.tracking.get("consent_mode")
                       and not p.tracking.get("gtm")]
        c.add_si(sin_consent, "sin_consent_mode", S, "alta",
                 "Etiquetas de Google sin Consent Mode detectado.",
                 "En el Espacio Económico Europeo, Consent Mode v2 es necesario para medir y usar audiencias "
                 "en Google Ads. Intégralo con tu banner de cookies (CMP certificada).")
        solo_gtm = [p.final_url for p in revisadas if p.tracking.get("gtm") and not p.tracking.get("consent_mode")]
        c.add_si(solo_gtm, "verificar_gtm", S, "baja",
                 "Se usa GTM: las etiquetas de Ads y el Consent Mode no se pueden verificar desde el HTML.",
                 "Revisa en GTM que existan la etiqueta de conversión, las conversiones mejoradas y el Consent Mode v2.")

    # --- calidad de landing
    sin_cta = [p.final_url for p in destino if not p.ctas and not p.tiene_formulario]
    c.add_si(sin_cta, "landing_sin_cta", S, "alta", "Landings sin llamada a la acción clara.",
             "Pon un CTA visible sin hacer scroll (comprar, pedir presupuesto, llamar) y repítelo al final.")

    for p in destino:
        camp = cfg.campana_de(path_de(p.final_url))
        if not camp or not camp.palabras_clave:
            continue
        zona = _norm(" ".join([p.title or "", " ".join(p.h1), p.primer_parrafo]))
        faltan = [k for k in camp.palabras_clave if _norm(k) not in zona]
        if len(faltan) == len(camp.palabras_clave):
            c.add("mensaje_no_coincide", S, "alta",
                  "Landings cuyo título, H1 y primer párrafo no contienen ninguna palabra clave de su campaña.",
                  "Haz que el titular repita la promesa del anuncio: mejora la relevancia, el Nivel de Calidad y "
                  "baja el CPC.", [p.final_url])

    productos = [p for p in paginas if p.tipo == "producto"]
    sin_gtin = []
    for p in productos:
        nodos = p.nodos_schema("Product")
        if nodos and not any(k in nodos[0] for k in ("gtin", "gtin13", "gtin8", "gtin12", "gtin14", "mpn")):
            sin_gtin.append(p.final_url)
    c.add_si(sin_gtin, "producto_sin_gtin", S, "baja", "Productos sin GTIN ni MPN en el schema.",
             "Añade el GTIN (EAN) o MPN: mejora la coincidencia en Merchant Center y Shopping.")
