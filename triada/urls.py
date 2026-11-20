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


def host_base(url_o_host: str) -> str:
    """Host sin «www.» (y en minúsculas) para comparar si dos URLs son del mismo sitio."""
    host = urlparse(url_o_host).netloc if "//" in url_o_host else url_o_host
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def mismo_sitio(url: str, host: str) -> bool:
    """True si la URL es del sitio analizado (mismo dominio, con o sin www). Otros dominios quedan fuera."""
    return host_base(url) == host_base(host)


def path_de(url: str) -> str:
    return urlparse(url).path or "/"


def clave_url(url: str) -> str:
    """Forma comparable de una URL: sin codificar (%f0%9f… = 🐶), host en minúsculas, sin barra final ni #."""
    from urllib.parse import unquote

    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    ruta = unquote(p.path or "/").rstrip("/") or "/"
    consulta = f"?{unquote(p.query)}" if p.query else ""
    return f"{p.scheme.lower()}://{p.netloc.lower()}{ruta}{consulta}"


def tiene_parametros(url: str) -> bool:
    return bool(urlparse(url).query)


class ReglasRobots:
    """Lector de robots.txt con comodines (* y $), como lo interpreta Google.

    El RobotFileParser de Python no entiende «Disallow: /*filter_category=», así que se rastreaban URLs prohibidas.
    """

    def __init__(self, texto: str | None) -> None:
        self.grupos: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agentes: list[str] = []
        reglas: list[tuple[bool, str]] = []
        leyendo_reglas = False
        for linea in (texto or "").splitlines():
            linea = linea.split("#", 1)[0].strip()
            if ":" not in linea:
                continue
            campo, valor = (x.strip() for x in linea.split(":", 1))
            campo = campo.lower()
            if campo == "user-agent":
                if leyendo_reglas:
                    self.grupos.append((agentes, reglas))
                    agentes, reglas, leyendo_reglas = [], [], False
                agentes.append(valor.lower())
            elif campo in ("allow", "disallow"):
                leyendo_reglas = True
                if valor or campo == "allow":
                    reglas.append((campo == "allow", valor))
        if agentes:
            self.grupos.append((agentes, reglas))

    def _reglas(self, agente: str) -> list[tuple[bool, str]]:
        agente = agente.lower()
        especificas = [r for ags, r in self.grupos if any(a != "*" and a in agente for a in ags)]
        if especificas:
            return [x for r in especificas for x in r]
        return [x for ags, r in self.grupos if "*" in ags for x in r]

    @staticmethod
    def _coincide(patron: str, ruta: str) -> bool:
        import re

        if not patron:
            return False
        rx = re.escape(patron).replace(r"\*", ".*")
        if rx.endswith(r"\$"):
            rx = rx[:-2] + "$"
        return re.match(rx, ruta) is not None

    def permite(self, agente: str, url: str) -> bool:
        p = urlparse(url)
        ruta = (p.path or "/") + (f"?{p.query}" if p.query else "")
        mejor: tuple[int, bool] | None = None  # la regla más larga gana; a igualdad, Allow
        for permitir, patron in self._reglas(agente):
            if self._coincide(patron, ruta):
                clave = (len(patron), permitir)
                if mejor is None or clave > mejor:
                    mejor = clave
        return True if mejor is None else mejor[1]
