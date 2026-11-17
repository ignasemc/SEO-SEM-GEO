"""Normalización de URLs."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PARAMS_SEGUIMIENTO = {"gclid", "fbclid", "msclkid", "gbraid", "wbraid"}
EXTENSIONES_NO_HTML = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".mp4", ".mp3",
    ".css", ".js", ".ico", ".xml", ".txt", ".woff", ".woff2", ".ttf", ".avif",
)


def normalizar(url: str) -> str | None:
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in PARAMS_SEGUIMIENTO]
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, "", urlencode(query), ""))


def parece_html(url: str) -> bool:
    return not urlparse(url).path.lower().endswith(EXTENSIONES_NO_HTML)


def path_de(url: str) -> str:
    return urlparse(url).path or "/"
