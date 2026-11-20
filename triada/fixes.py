"""Generación de correcciones concretas a partir de los issues.

Todas las propuestas son deterministas (sin IA) y van marcadas para revisión humana cuando
contienen texto redactado o datos que hay que confirmar. Los marcadores «REVISAR» indican
valores que el motor no puede saber.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape

from .audits.base import paginas_contenido
from .audits.geo import BOTS_BUSQUEDA, BOTS_ENTRENAMIENTO, estado_bots
from .config import Config
from .imagenes import alt_valido, clave_imagen
from .models import Fix, Imagen, Issue, Page, SiteData
from .urls import path_de

REVISAR = "«REVISAR»"


def _recortar(texto: str, maximo: int) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= maximo:
        return texto
    corte = texto[: maximo - 1].rsplit(" ", 1)[0].rstrip(",.;:-–| ")
    return corte + "…"


def _quitar_marca(texto: str, marca: str) -> str:
    if not marca:
        return texto
    return re.sub(rf"\s*[|\-–—·:]\s*{re.escape(marca)}\s*$", "", texto, flags=re.I).strip()


def proponer_titulo(p: Page, cfg: Config) -> str:
    base = (p.h1[0] if p.h1 else None) or _quitar_marca(p.title or "", cfg.marca) or _desde_url(p.final_url)
    base = _quitar_marca(base, cfg.marca)
    camp = cfg.campana_de(path_de(p.final_url))
    if camp and camp.palabras_clave:
        kw = camp.palabras_clave[0]
        if kw.lower() not in base.lower():
            base = f"{kw[:1].upper()}{kw[1:]}: {base}"
    sufijo = f" | {cfg.marca}" if cfg.marca else ""
    if len(base) + len(sufijo) > 60:
        return _recortar(base, 60 - len(sufijo)) + sufijo if len(sufijo) < 25 else _recortar(base, 60)
    return base + sufijo


def proponer_descripcion(p: Page, cfg: Config) -> str:
    fuente = p.primer_parrafo or p.meta_description or ""
    if not fuente:
        return f"{REVISAR} Describe en 70-160 caracteres qué ofrece esta página y por qué elegiros."
    return _recortar(fuente, 155)


def _desde_url(url: str) -> str:
    nombre = PurePosixPath(unquote(urlparse(url).path)).stem or "Inicio"
    return re.sub(r"[-_]+", " ", nombre).strip().capitalize()


def jsonld_organizacion(site: SiteData, cfg: Config) -> dict:
    o = cfg.organizacion
    home = site.home()
    datos = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": cfg.marca or (home.title if home and home.title else REVISAR),
        "url": site.url_base + "/",
        "logo": o.logo or REVISAR,
        "sameAs": o.redes or [REVISAR + " https://www.linkedin.com/company/…"],
    }
    if o.nombre_legal:
        datos["legalName"] = o.nombre_legal
    if o.fundacion:
        datos["foundingDate"] = o.fundacion
    if o.telefono or o.email:
        datos["contactPoint"] = {"@type": "ContactPoint", "contactType": "customer service",
                                 **({"telephone": o.telefono} if o.telefono else {}),
                                 **({"email": o.email} if o.email else {})}
    if o.direccion:
        datos["address"] = o.direccion
    return datos


def jsonld_producto(p: Page) -> dict:
    existente = (p.nodos_schema("Product") or [{}])[0]
    precio = REVISAR
    if p.precio_detectado:
        num = re.sub(r"[^\d,\.]", "", p.precio_detectado)
        if "," in num and "." in num:
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", ".")
        precio = num or REVISAR
    imagen = existente.get("image") or next((i.src for i in p.imagenes if i.src), REVISAR)
    offers = existente.get("offers") if isinstance(existente.get("offers"), dict) else {}
    return {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": existente.get("name") or (p.h1[0] if p.h1 else REVISAR),
        "description": existente.get("description") or p.meta_description or _recortar(p.primer_parrafo, 300) or REVISAR,
        "image": imagen,
        "sku": existente.get("sku", REVISAR),
        "gtin13": existente.get("gtin13", REVISAR + " (EAN, si lo tiene)"),
        "brand": existente.get("brand") or {"@type": "Brand", "name": REVISAR},
        "offers": {
            "@type": "Offer",
            "url": p.final_url,
            "price": offers.get("price") or precio,
            "priceCurrency": offers.get("priceCurrency") or "EUR",
            "availability": offers.get("availability") or "https://schema.org/InStock",
        },
    }


def jsonld_breadcrumb(p: Page, site: SiteData) -> dict:
    partes = [x for x in urlparse(p.final_url).path.split("/") if x]
    items = [{"@type": "ListItem", "position": 1, "name": "Inicio", "item": site.url_base + "/"}]
    ruta = ""
    for n, parte in enumerate(partes, start=2):
        ruta += "/" + parte
        nombre = (p.h1[0] if p.h1 else _desde_url(ruta)) if n == len(partes) + 1 else _desde_url(ruta)
        items.append({"@type": "ListItem", "position": n, "name": nombre, "item": site.url_base + ruta})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def _grupos_robots(texto: str) -> tuple[list[tuple[list[str], list[str]]], list[str]]:
    """Divide robots.txt en grupos (agentes, reglas) y líneas sueltas (Sitemap, comentarios)."""
    grupos: list[tuple[list[str], list[str]]] = []
    sueltas: list[str] = []
    agentes: list[str] = []
    reglas: list[str] = []
    for bruta in texto.splitlines():
        linea = bruta.strip()
        if not linea:
            continue
        clave = linea.split(":", 1)[0].strip().lower()
        if clave == "user-agent":
            if reglas:
                grupos.append((agentes, reglas))
                agentes, reglas = [], []
            agentes.append(linea.split(":", 1)[1].strip())
        elif clave in ("allow", "disallow", "crawl-delay") and agentes:
            reglas.append(linea)
        else:
            sueltas.append(linea)
    if agentes:
        grupos.append((agentes, reglas))
    return grupos, sueltas


def robots_propuesto(site: SiteData) -> str:
    """Conserva las reglas existentes y desbloquea los bots de búsqueda de IA (y Googlebot/Bingbot).

    Los bots de entrenamiento no se tocan: bloquearlos es una decisión de negocio.
    """
    bots = estado_bots(site)
    desbloquear = {b.lower() for b in ["Googlebot", "Bingbot"] + BOTS_BUSQUEDA if not bots[b]}
    grupos, sueltas = _grupos_robots(site.robots_txt or "")
    if not grupos:
        grupos = [(["*"], ["Allow: /"])]

    salida: list[str] = []
    nuevos: list[str] = []
    for agentes, reglas in grupos:
        afectados = [a for a in agentes if a.lower() in desbloquear]
        resto = [a for a in agentes if a.lower() not in desbloquear]
        nuevos.extend(afectados)
        if resto:
            salida.append("\n".join(f"User-agent: {a}" for a in resto) + "\n" + "\n".join(reglas))
    # bots bloqueados por el grupo "*" (sin grupo propio)
    con_grupo = {a.lower() for ags, _ in grupos for a in ags}
    nuevos.extend(b for b in ["Googlebot", "Bingbot"] + BOTS_BUSQUEDA if b.lower() in desbloquear
                  and b.lower() not in con_grupo)
    if nuevos:
        # heredan las restricciones parciales del grupo "*" (p. ej. /admin/), sin el bloqueo total
        general = next((r for ags, r in grupos if "*" in ags), [])
        reglas = [r for r in general if r.replace(" ", "").lower() != "disallow:/"] or ["Allow: /"]
        salida.append("# Permitidos por Tríada: necesarios para aparecer en buscadores y respuestas de IA\n"
                      + "\n".join(f"User-agent: {a}" for a in dict.fromkeys(nuevos)) + "\n" + "\n".join(reglas))
    if not any(s.lower().startswith("sitemap:") for s in sueltas):
        sueltas.append(f"Sitemap: {site.url_base}/sitemap.xml")
    return "\n\n".join(salida) + "\n\n" + "\n".join(sueltas) + "\n"


def urls_para_sitemap(site: SiteData, excluir: set[str] | None = None) -> list[Page]:
    """Una URL entra en el sitemap solo si responde 200, es indexable, es su propia canonical y no lleva parámetros."""
    excluir = excluir or set()
    return sorted((p for p in paginas_contenido(site)
                   if p.indexable and not p.variante_de and "?" not in p.final_url and p.final_url not in excluir),
                  key=lambda p: p.final_url)


def sitemap_propuesto(site: SiteData, excluir: set[str] | None = None) -> str:
    filas = []
    for p in urls_para_sitemap(site, excluir):
        lastmod = f"<lastmod>{escape(p.fecha_modificacion[:25])}</lastmod>" if p.fecha_modificacion else ""
        filas.append(f"  <url><loc>{escape(p.final_url)}</loc>{lastmod}</url>")
    cuerpo = "\n".join(filas)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + cuerpo + "\n</urlset>\n")


SNIPPET_CONSENT = """<!-- Consent Mode v2: valores por defecto ANTES de cargar gtag/GTM.
     Tu banner de cookies (CMP) debe llamar a gtag('consent','update',{...}) al aceptar. -->
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('consent', 'default', {
    'ad_storage': 'denied',
    'ad_user_data': 'denied',
    'ad_personalization': 'denied',
    'analytics_storage': 'denied',
    'wait_for_update': 500
  });
</script>"""


def generar(issues: list[Issue], site: SiteData, cfg: Config) -> tuple[list[Fix], dict[str, str]]:
    """Devuelve (fixes, archivos_generados {nombre: contenido})."""
    por_url = {p.final_url: p for p in paginas_contenido(site)}
    por_url.update({p.url: p for p in site.pages.values()})
    fixes: list[Fix] = []
    archivos: dict[str, str] = {}
    hechos: set[tuple[str | None, str]] = set()
    # en modo combinado, las landings solo-anuncios salen del sitemap
    excluir = {u for i in issues if i.codigo == "landing_ads_indexable" for u in i.urls}

    def add(fix: Fix) -> None:
        clave = (fix.url, fix.tipo)
        if clave not in hechos:
            hechos.add(clave)
            fixes.append(fix)

    for i in issues:
        paginas = [por_url[u] for u in i.urls if u in por_url]
        cod = i.codigo
        if cod in ("titulo_falta", "titulo_largo", "titulo_corto", "titulo_duplicado", "mensaje_no_coincide"):
            for p in paginas:
                add(Fix(cod, p.final_url, "titulo", p.title, proponer_titulo(p, cfg),
                        nota="Título generado a partir del H1, la palabra clave de campaña y la marca."))
        elif cod in ("meta_desc_falta", "meta_desc_larga", "meta_desc_corta", "meta_desc_duplicada"):
            for p in paginas:
                add(Fix(cod, p.final_url, "meta_description", p.meta_description, proponer_descripcion(p, cfg),
                        nota="Extraída del primer párrafo. Reescríbela con un beneficio y una llamada a la acción."))
        elif cod == "h1_falta":
            for p in paginas:
                add(Fix(cod, p.final_url, "h1", None,
                        _quitar_marca(p.title or "", cfg.marca) or _desde_url(p.final_url)))
        elif cod == "canonical_falta":
            for p in paginas:
                add(Fix(cod, p.final_url, "canonical", None, f'<link rel="canonical" href="{p.final_url}">',
                        requiere_revision=False))
        elif cod == "lang_falta":
            for p in paginas:
                add(Fix(cod, p.final_url, "lang", None, '<html lang="es">', nota="Ajusta si el idioma no es español."))
        elif cod == "alt_falta":
            # una corrección por imagen (no por página): en WordPress se cambia en la biblioteca de medios
            por_imagen: dict[str, tuple[Imagen, list[str]]] = {}
            for p in paginas:
                for img in p.imagenes:
                    if img.falta_alt:
                        k = clave_imagen(img.src)
                        if k in por_imagen:
                            por_imagen[k][1].append(p.final_url)
                        else:
                            por_imagen[k] = (img, [p.final_url])
            for img, urls in por_imagen.values():
                fiable = img.propuesta if alt_valido(img.propuesta) else None
                nota = ("Tomado del nombre del producto o del pie de foto. " if fiable else
                        "No hay una descripción fiable de esta imagen: escríbela tú (o usa «Optimizar con IA»). "
                        "Si es decorativa, déjala vacía (alt=\"\"). ")
                nota += f"Aparece en {len(urls)} página(s)." + (f" Adjunto de WordPress n.º {img.adjunto}: cámbialo "
                                                                "una vez en la biblioteca de medios." if img.adjunto else "")
                add(Fix(cod, urls[0], f"alt:{img.src}", img.alt, fiable or REVISAR, nota=nota, paginas=urls[1:],
                        objeto={"tipo": "attachment", "id": img.adjunto} if img.adjunto else None))
        elif cod in ("schema_producto",):
            for p in paginas:
                nombre = f"jsonld/producto-{_slug(p.final_url)}.json"
                contenido = json.dumps(jsonld_producto(p), ensure_ascii=False, indent=2)
                archivos[nombre] = contenido
                add(Fix(cod, p.final_url, "jsonld", None, contenido, archivo=nombre,
                        nota="Completa los «REVISAR». Debe coincidir con el precio visible y con Merchant Center."))
        elif cod == "breadcrumb_falta":
            for p in paginas:
                add(Fix(cod, p.final_url, "jsonld_breadcrumb", None,
                        json.dumps(jsonld_breadcrumb(p, site), ensure_ascii=False, indent=2),
                        nota="Los nombres salen de la URL: ajústalos a los de tu menú."))
        elif cod in ("organizacion_falta", "organizacion_incompleta"):
            contenido = json.dumps(jsonld_organizacion(site, cfg), ensure_ascii=False, indent=2)
            archivos["jsonld/organizacion.json"] = contenido
            add(Fix(cod, site.url_base + "/", "jsonld", None, contenido, archivo="jsonld/organizacion.json",
                    nota="Insértalo en la home dentro de <script type=\"application/ld+json\">."))
        elif cod in ("bloquea_bots_busqueda_ia", "robots_txt_falta", "bloquea_googlebot", "bloquea_bingbot"):
            contenido = robots_propuesto(site)
            archivos["robots.txt"] = contenido
            add(Fix(cod, site.url_base + "/robots.txt", "archivo", site.robots_txt, contenido, archivo="robots.txt",
                    nota="Revisa antes de publicar. Los bots de entrenamiento se dejan como estén (decisión tuya)."))
        elif cod in ("sitemap_falta", "sitemap_sucio", "fuera_sitemap", "landing_ads_indexable"):
            # Si la web ya tiene un generador (Yoast, Rank Math, WordPress…), no se sustituye su sitemap:
            # se recomienda ajustarlo ahí (va en la recomendación del problema).
            if not site.generador_sitemap:
                contenido = sitemap_propuesto(site, excluir)
                archivos["sitemap.xml"] = contenido
                add(Fix(cod, site.url_base + "/sitemap.xml", "archivo", None, contenido, archivo="sitemap.xml",
                        requiere_revision=False,
                        nota="Solo URLs que responden 200, indexables, canónicas y sin parámetros."))
            if cod == "landing_ads_indexable":
                for p in paginas:
                    add(Fix(cod, p.final_url, "meta_robots", p.meta_robots or None,
                            '<meta name="robots" content="noindex, follow">', requiere_revision=False,
                            nota="Landing solo para anuncios: fuera del índice orgánico."))
        elif cod == "sin_consent_mode":
            archivos["snippets/consent-mode-v2.html"] = SNIPPET_CONSENT
            for p in paginas:
                add(Fix(cod, p.final_url, "snippet_head", None, SNIPPET_CONSENT,
                        archivo="snippets/consent-mode-v2.html",
                        nota="Va al principio del <head>, antes de gtag.js/GTM. Conéctalo a tu banner de cookies "
                             "(lo ideal es una CMP certificada por Google)."))

    ramas_por_codigo = {i.codigo: sorted(i.ramas) for i in issues}
    for n, f in enumerate(fixes, start=1):
        f.id = f"F{n:03d}"
        f.ramas = ramas_por_codigo.get(f.codigo_issue, [])
    return fixes, archivos


def _slug(url: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", urlparse(url).path.lower()).strip("-")
    return s[:60] or "inicio"
