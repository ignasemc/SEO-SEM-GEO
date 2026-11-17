"""Modelos de datos compartidos por todo el motor."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

RAMAS = ("seo", "sem", "geo")

SEVERIDAD_PESO = {"critica": 15.0, "alta": 8.0, "media": 4.0, "baja": 1.5}


@dataclass
class Imagen:
    src: str
    alt: str | None


@dataclass
class Page:
    """Una página rastreada y sus señales extraídas."""

    url: str
    status: int
    final_url: str
    redirecciones: list[str] = field(default_factory=list)
    tiempo_ms: int = 0
    bytes: int = 0
    content_type: str = ""
    x_robots: str = ""
    fuera_del_sitio: bool = False   # redirige a otro dominio: no se analiza

    title: str | None = None
    meta_description: str | None = None
    meta_robots: str = ""
    canonical: str | None = None
    lang: str | None = None
    viewport: bool = False
    h1: list[str] = field(default_factory=list)
    h2: list[str] = field(default_factory=list)
    palabras: int = 0
    primer_parrafo: str = ""
    enlaces_internos: list[str] = field(default_factory=list)
    enlaces_externos: list[str] = field(default_factory=list)
    imagenes: list[Imagen] = field(default_factory=list)
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    jsonld_errores: int = 0
    scripts: int = 0
    depende_js: bool = False
    tiene_formulario: bool = False
    ctas: list[str] = field(default_factory=list)
    tracking: dict[str, bool] = field(default_factory=dict)
    autor: str | None = None
    fecha_modificacion: str | None = None
    tiene_faq: bool = False
    cifras: int = 0
    precio_detectado: str | None = None
    tipo: str = "generica"  # home, producto, servicio, articulo, landing, contacto, quienes_somos, generica

    @property
    def indexable(self) -> bool:
        robots = f"{self.meta_robots} {self.x_robots}".lower()
        return self.status == 200 and "noindex" not in robots

    def tipos_schema(self) -> set[str]:
        tipos: set[str] = set()
        for bloque in self.jsonld:
            for nodo in _nodos(bloque):
                t = nodo.get("@type")
                if isinstance(t, list):
                    tipos.update(str(x) for x in t)
                elif t:
                    tipos.add(str(t))
        return tipos

    def nodos_schema(self, tipo: str) -> list[dict[str, Any]]:
        res = []
        for bloque in self.jsonld:
            for nodo in _nodos(bloque):
                t = nodo.get("@type")
                tipos = t if isinstance(t, list) else [t]
                if tipo in tipos:
                    res.append(nodo)
        return res


def _nodos(bloque: Any) -> list[dict[str, Any]]:
    if isinstance(bloque, list):
        out = []
        for b in bloque:
            out.extend(_nodos(b))
        return out
    if isinstance(bloque, dict):
        if "@graph" in bloque and isinstance(bloque["@graph"], list):
            return _nodos(bloque["@graph"])
        return [bloque]
    return []


@dataclass
class SiteData:
    """Todo lo recogido en el rastreo."""

    url_base: str
    host: str
    pages: dict[str, Page] = field(default_factory=dict)
    robots_txt: str | None = None
    robots_status: int | None = None
    sitemaps: list[str] = field(default_factory=list)
    urls_sitemap: set[str] = field(default_factory=set)
    enlaces_entrantes: dict[str, int] = field(default_factory=dict)
    errores_rastreo: list[str] = field(default_factory=list)

    def html_pages(self) -> list[Page]:
        return [p for p in self.pages.values() if "html" in p.content_type]

    def home(self) -> Page | None:
        for p in self.pages.values():
            if p.tipo == "home":
                return p
        return None


@dataclass
class Issue:
    """Un problema detectado. `ramas` indica a qué vertientes afecta."""

    codigo: str
    ramas: set[str]
    severidad: str
    mensaje: str
    recomendacion: str
    urls: list[str] = field(default_factory=list)  # vacío = problema de todo el sitio
    nota_conflicto: str | None = None

    def prioridad(self, total_paginas: int) -> float:
        peso = SEVERIDAD_PESO[self.severidad]
        alcance = 1.0 if not self.urls else min(1.0, len(self.urls) / max(total_paginas, 1))
        multi = 1 + 0.5 * (len(self.ramas) - 1)
        return round(peso * (0.5 + 0.5 * alcance) * multi, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ramas"] = sorted(self.ramas)
        return d


@dataclass
class Fix:
    """Un cambio propuesto, listo para revisar y aplicar."""

    codigo_issue: str
    url: str | None
    tipo: str  # titulo, meta_description, h1, alt, canonical, jsonld, meta_robots, archivo, snippet
    valor_actual: str | None
    valor_propuesto: str
    requiere_revision: bool = True
    nota: str = ""
    archivo: str | None = None  # nombre de archivo generado, si aplica
    id: str = ""
    ramas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
