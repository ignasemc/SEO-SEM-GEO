"""Extracción de señales SEO/SEM/GEO a partir del HTML."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import imagenes
from .models import Imagen, Page
from .urls import mismo_sitio, normalizar

RE_CTA = re.compile(
    r"\b(comprar|compra|añadir al carrito|anadir al carrito|contactar|contacta|contáctanos|"
    r"solicitar|solicita|reservar|reserva|pedir|pide|presupuesto|llamar|llama|empieza|"
    r"prueba gratis|suscríbete|suscribete|regístrate|registrate|descargar|agenda|book|buy|"
    r"contact|get started|sign up|request)\b",
    re.I,
)
RE_PRECIO = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{2})?)\s?(€|eur|euros)|(€|\$)\s?(\d+(?:[.,]\d{2})?)", re.I)
RE_CIFRA = re.compile(r"\b\d+(?:[.,]\d+)?\s?(%|€|\$|años|clientes|usuarios|millones|mil|k\b)", re.I)
RE_FAQ = re.compile(r"preguntas frecuentes|faq|dudas frecuentes", re.I)


def parsear(page: Page, html: str, host: str) -> Page:
    soup = BeautifulSoup(html, "lxml")
    base = page.final_url

    # --- JSON-LD (antes de limpiar scripts)
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            page.jsonld.append(json.loads(tag.string or ""))
        except (json.JSONDecodeError, TypeError):
            page.jsonld_errores += 1

    scripts = soup.find_all("script")
    page.scripts = len(scripts)
    texto_scripts = " ".join((s.get("src") or "") + " " + (s.string or "") for s in scripts)
    page.tracking = {
        "gtm": bool(re.search(r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]+", texto_scripts)),
        "ga4": bool(re.search(r"G-[A-Z0-9]{6,}", texto_scripts)),
        "google_ads": bool(re.search(r"AW-\d{6,}", texto_scripts)),
        "consent_mode": bool(re.search(r"gtag\(\s*['\"]consent['\"]", texto_scripts)),
        "meta_pixel": "fbq(" in texto_scripts,
    }

    html_tag = soup.find("html")
    page.lang = html_tag.get("lang") if html_tag else None

    if soup.title and soup.title.string:
        page.title = " ".join(soup.title.string.split())

    for meta in soup.find_all("meta"):
        nombre = (meta.get("name") or meta.get("property") or "").lower()
        contenido = (meta.get("content") or "").strip()
        if nombre == "description":
            page.meta_description = contenido
        elif nombre == "robots":
            page.meta_robots = contenido.lower()
        elif nombre == "viewport":
            page.viewport = True
        elif nombre == "author" and contenido:
            page.autor = contenido
        elif nombre in ("article:modified_time", "og:updated_time") and contenido:
            page.fecha_modificacion = contenido

    link_canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    if link_canonical and link_canonical.get("href"):
        page.canonical = urljoin(base, link_canonical["href"])

    page.h1 = [" ".join(h.get_text(" ").split()) for h in soup.find_all("h1")]
    page.h2 = [" ".join(h.get_text(" ").split()) for h in soup.find_all("h2")]

    # --- enlaces
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:")):
            page.ctas.append(href.split(":", 1)[0])
            continue
        if href.startswith(("#", "javascript:")):
            continue
        absoluto = normalizar(urljoin(base, href))
        if not absoluto:
            continue
        if mismo_sitio(absoluto, host):
            page.enlaces_internos.append(absoluto)
        else:
            page.enlaces_externos.append(absoluto)
        texto = a.get_text(" ").strip()
        if texto and RE_CTA.search(texto):
            page.ctas.append(texto[:60])
    for b in soup.find_all(["button", "input"]):
        texto = b.get_text(" ").strip() or (b.get("value") or "")
        if texto and RE_CTA.search(texto):
            page.ctas.append(texto[:60])
    page.ctas = list(dict.fromkeys(page.ctas))
    page.tiene_formulario = soup.find("form") is not None

    # --- imágenes (ver imagenes.py: carga diferida, <noscript>, píxeles, alt fiables)
    tipo_provisional = clasificar(page, host)
    vistas: set[str] = set()
    for img in soup.find_all("img"):
        if img.find_parent("noscript") is not None:
            continue  # copia para navegadores sin JavaScript de una imagen que ya se evalúa
        src = imagenes.src_real(img, base)
        if not src or imagenes.es_seguimiento(img, src):
            continue
        clave = imagenes.clave_imagen(src)
        if clave in vistas:
            continue
        vistas.add(clave)
        alt = img.get("alt")
        contenido = imagenes.es_contenido(img)
        falta = alt is None or (contenido and not alt.strip())
        page.imagenes.append(Imagen(
            src=src, alt=alt, adjunto=imagenes.adjunto(img), contenido=contenido,
            propuesta=imagenes.alt_fiable(img, src, page.h1[0] if page.h1 else None, tipo_provisional)
            if falta else None))

    # --- autor y fecha en el HTML
    if not page.autor:
        rel_autor = soup.find(attrs={"rel": "author"}) or soup.find(class_=re.compile(r"\b(author|autor|byline)\b", re.I))
        if rel_autor:
            page.autor = " ".join(rel_autor.get_text(" ").split())[:80] or None
    for nodo in _nodos_con(page, "author"):
        a = nodo.get("author")
        if isinstance(a, dict):
            page.autor = page.autor or a.get("name")
        elif isinstance(a, list) and a and isinstance(a[0], dict):
            page.autor = page.autor or a[0].get("name")
        elif isinstance(a, str):
            page.autor = page.autor or a
    for nodo in _nodos_con(page, "dateModified"):
        page.fecha_modificacion = page.fecha_modificacion or str(nodo["dateModified"])
    if not page.fecha_modificacion:
        t = soup.find("time", datetime=True)
        if t:
            page.fecha_modificacion = t["datetime"]

    # --- texto principal (sin plantillas)
    cuerpo = soup.find("main") or soup.find("article") or soup.body or soup
    for basura in cuerpo.find_all(["script", "style", "noscript", "nav", "footer", "header", "svg"]):
        basura.decompose()
    texto = " ".join(cuerpo.get_text(" ").split())
    page.palabras = len(texto.split())
    page.cifras = len(RE_CIFRA.findall(texto))
    page.tiene_faq = bool(RE_FAQ.search(" ".join(page.h2) + " " + " ".join(page.h1))) or "FAQPage" in page.tipos_schema()
    for p in cuerpo.find_all("p"):
        t = " ".join(p.get_text(" ").split())
        if len(t) >= 40:
            page.primer_parrafo = t
            break
    m = RE_PRECIO.search(texto)
    if m:
        page.precio_detectado = m.group(0)

    # --- ¿el contenido depende de JavaScript?
    raiz_vacia = any(
        (d := soup.find(id=i)) is not None and len(d.get_text(strip=True)) == 0
        for i in ("root", "app", "__next", "__nuxt")
    )
    page.depende_js = page.palabras < 60 and (raiz_vacia or page.scripts >= 8)

    page.tipo = clasificar(page, host)
    return page


def _nodos_con(page: Page, clave: str):
    from .models import _nodos

    for bloque in page.jsonld:
        for nodo in _nodos(bloque):
            if clave in nodo:
                yield nodo


RUTAS_TIPO = [
    ("contacto", re.compile(r"/(contacto|contact|contactanos)/?$", re.I)),
    ("quienes_somos", re.compile(r"/(quienes-somos|sobre-nosotros|nosotros|about|about-us|empresa)/?$", re.I)),
    ("servicio", re.compile(r"/(servicios?|services?)(/|$)", re.I)),
    ("producto", re.compile(r"/(productos?|products?|tienda|shop|p)/[^/]+", re.I)),
    ("articulo", re.compile(r"/(blog|noticias|news|articulos?|guias?)/[^/]+", re.I)),
]


def clasificar(page: Page, host: str) -> str:
    path = urlparse(page.final_url).path or "/"
    if path in ("", "/", "/index.html", "/es/", "/es"):
        return "home"
    tipos = page.tipos_schema()
    if "Product" in tipos:
        return "producto"
    if tipos & {"Article", "BlogPosting", "NewsArticle"}:
        return "articulo"
    if "Service" in tipos:
        return "servicio"
    for tipo, patron in RUTAS_TIPO:
        if patron.search(path):
            return tipo
    return "generica"
