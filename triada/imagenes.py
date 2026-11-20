"""Imágenes: cuál es la imagen real, cuáles se evalúan y qué texto alternativo es fiable.

Problemas que resuelve (rastreo de petground.es, 20/11/2026):
- Carga diferida: el `src` es un marcador (`data:image/svg+xml…`, `lazy.svg`) y la imagen real está en
  `data-lazy-src`, `data-src`, `data-srcset`… Antes se evaluaba el marcador y se proponía `alt="Svg&gt;"`.
- `<noscript><img>`: copia de la misma imagen para navegadores sin JavaScript. No se evalúa aparte.
- Píxeles de seguimiento (facebook.com/tr, 1×1…): no necesitan `alt`. Antes se proponía `alt="Tr"`.
- `alt=""` es correcto en imágenes decorativas: no es un error.
- No se genera `alt` a partir del nombre del archivo («Logo blanco x»): solo con fuentes fiables (nombre del
  producto en su imagen principal, pie de foto, título descriptivo). Si no hay, queda para revisión humana o IA.
- La misma imagen aparece en decenas de páginas: se corrige una vez (el adjunto de la biblioteca de medios).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

ATRIBUTOS_DIFERIDOS = ("data-lazy-src", "data-src", "data-original", "data-wood-src", "data-lazyload", "data-orig-src")
ATRIBUTOS_SRCSET = ("data-lazy-srcset", "data-srcset", "srcset")
RE_SEGUIMIENTO = re.compile(
    r"facebook\.com/tr|google-analytics\.com|googletagmanager\.com|doubleclick\.net|bat\.bing\.com|"
    r"analytics\.tiktok|px\.ads\.linkedin|t\.co/i/adsct|/pixel(\.gif|\?)|/collect\?", re.I)
RE_TAMANO = re.compile(r"-\d{2,5}x\d{2,5}(?=\.[a-z0-9]+$)", re.I)
RE_ADJUNTO = re.compile(r"\bwp-image-(\d+)\b")


def _es_marcador(src: str) -> bool:
    if not src or src.startswith("data:"):
        return True
    nombre = PurePosixPath(urlparse(src).path).name.lower()
    return nombre in ("lazy.svg", "lazy.png", "lazy.gif", "blank.gif", "placeholder.png", "placeholder.svg",
                      "transparent.gif", "spacer.gif") or "lazy" in nombre and nombre.endswith(".svg")


def src_real(img, base: str) -> str:
    """URL de la imagen que ve el visitante (resuelve la carga diferida). Cadena vacía si no hay ninguna."""
    candidatos = [img.get("src") or ""] + [img.get(a) or "" for a in ATRIBUTOS_DIFERIDOS]
    for a in ATRIBUTOS_SRCSET:
        if img.get(a):
            candidatos.append(img.get(a).split(",")[0].strip().split(" ")[0])
    for c in candidatos:
        c = c.strip()
        if c and not _es_marcador(c):
            return urljoin(base, c)
    return ""


def es_seguimiento(img, src: str) -> bool:
    if RE_SEGUIMIENTO.search(src or ""):
        return True
    ancho, alto = (img.get("width") or "").strip(), (img.get("height") or "").strip()
    return ancho in ("0", "1") and alto in ("0", "1")


def adjunto(img) -> int | None:
    """Id del adjunto de WordPress (clase wp-image-123), si lo hay."""
    clases = img.get("class") or []
    m = RE_ADJUNTO.search(" ".join(clases) if isinstance(clases, list) else str(clases))
    return int(m.group(1)) if m else None


def clave_imagen(src: str) -> str:
    """La misma imagen en distintos tamaños (foto-150x150.jpg, foto.jpg) cuenta como una."""
    p = urlparse(src)
    return f"{p.netloc.lower()}{RE_TAMANO.sub('', unquote(p.path))}"


# ------------------------------------------------------------------ validación de textos alternativos

RE_ALT_MALO = re.compile(
    r"\d{2,5}\s*x\s*\d{0,5}\b"                   # tamaños: 260x, 1024x1024
    r"|\.(jpe?g|png|gif|webp|svg|avif)\b"        # extensiones
    r"|&[a-z]+;|&#\d+;|[<>]"                     # entidades o etiquetas escapadas
    r"|(^|\s)x$"                                 # «x» suelta al final («Logo blanco x»)
    r"|^(img|image|imagen|foto|photo|dsc|tr|svg)\b[\s\d_-]*$",
    re.I)


def alt_valido(texto: str | None) -> bool:
    """Un texto alternativo propuesto es aceptable si describe algo: sin tamaños, extensiones, entidades ni restos de
    nombres de archivo, y con al menos una palabra de tres letras o más."""
    if not texto or not texto.strip():
        return False
    t = texto.strip()
    if RE_ALT_MALO.search(t) or re.search(r"[_]|\w-\w+-\w", t):
        return False
    palabras = re.findall(r"[^\W\d_]{3,}", t)
    return bool(palabras) and len(t) <= 150


RE_TARJETA = re.compile(r"\b(product-wrapper|product-grid-item|type-product|wc-block-grid__product|product-small)\b")
RE_TITULO_TARJETA = re.compile(r"\b(wd-entities-title|woocommerce-loop-product__title|product-title|"
                               r"wc-block-grid__product-title|product-name)\b")
RE_DECORATIVA = re.compile(r"\b(hover-img|wd-hover|secondary-image|swap-image|bg-image|icon|logo)\b", re.I)
RE_CONTENIDO = re.compile(r"\b(product-image-link|woocommerce-product-gallery|product-images|entry-content|"
                          r"post-content|wp-block-image|woocommerce-loop-product__link)\b")


def _clases(nodo) -> str:
    c = nodo.get("class") or []
    return " ".join(c) if isinstance(c, list) else str(c)


def es_contenido(img) -> bool:
    """¿Imagen de contenido (foto de producto, imagen de un artículo)? Una de contenido con alt="" es un error;
    una decorativa (efecto al pasar el ratón, iconos, logos de adorno) con alt="" es correcta."""
    if RE_DECORATIVA.search(_clases(img)):
        return False
    for padre in img.parents:
        if padre.name in ("header", "footer", "nav", "aside"):
            return False
        clases = _clases(padre)
        if RE_DECORATIVA.search(clases):
            return False
        if RE_CONTENIDO.search(clases) or padre.name in ("article", "figure"):
            return True
    return False


def nombre_en_tarjeta(img) -> str | None:
    """Nombre del producto en la tarjeta del listado donde está la imagen (WooCommerce, Woodmart…)."""
    enlace = img.find_parent("a")
    if enlace is not None:
        etiqueta = (enlace.get("aria-label") or "").strip()
        if alt_valido(etiqueta):
            return etiqueta
    tarjeta = img.find_parent(class_=RE_TARJETA)
    if tarjeta is None:
        return None
    titulo = tarjeta.find(class_=RE_TITULO_TARJETA) or tarjeta.find(["h2", "h3"])
    if titulo is not None:
        t = " ".join(titulo.get_text(" ").split())
        if alt_valido(t):
            return t
    return None


def alt_fiable(img, src: str, h1: str | None, tipo_pagina: str | None) -> str | None:
    """Texto alternativo a partir de fuentes fiables; None si no hay ninguna (no se inventa)."""
    # 1. pie de foto
    figura = img.find_parent("figure")
    if figura is not None and (cap := figura.find("figcaption")) is not None:
        t = " ".join(cap.get_text(" ").split())
        if alt_valido(t):
            return t[:125]
    # 2. imagen principal de una ficha de producto → nombre del producto
    clases = " ".join(img.get("class") or []) if isinstance(img.get("class"), list) else str(img.get("class") or "")
    en_galeria = img.find_parent(class_=re.compile(r"woocommerce-product-gallery|product-images|product-gallery")) is not None
    if tipo_pagina == "producto" and h1 and ("wp-post-image" in clases or en_galeria) and alt_valido(h1):
        return h1[:125]
    # 3. miniatura de un producto en un listado → nombre del producto en su tarjeta
    if (nombre := nombre_en_tarjeta(img)):
        return nombre[:125]
    # 4. título de la imagen si es descriptivo (no un nombre de archivo)
    titulo = (img.get("title") or "").strip()
    if alt_valido(titulo) and " " in titulo:
        return titulo[:125]
    return None
