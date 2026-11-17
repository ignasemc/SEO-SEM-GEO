"""Carga de la configuración (archivo TOML opcional)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Campana:
    landing: str
    palabras_clave: list[str] = field(default_factory=list)
    solo_anuncios: bool = True  # landing pensada solo para tráfico de pago


@dataclass
class Organizacion:
    nombre_legal: str = ""
    logo: str = ""
    telefono: str = ""
    email: str = ""
    direccion: str = ""
    fundacion: str = ""
    redes: list[str] = field(default_factory=list)


@dataclass
class Config:
    url: str = ""
    marca: str = ""
    max_paginas: int = 200
    concurrencia: int = 5
    timeout: float = 15.0
    user_agent: str = "TriadaBot/0.1 (+auditoria SEO/SEM/GEO)"
    organizacion: Organizacion = field(default_factory=Organizacion)
    campanas: list[Campana] = field(default_factory=list)

    def campana_de(self, path: str) -> Campana | None:
        for c in self.campanas:
            if c.landing.rstrip("/") == path.rstrip("/"):
                return c
        return None


def cargar(ruta: str | None) -> Config:
    cfg = Config()
    if not ruta:
        return cfg
    datos = tomllib.loads(Path(ruta).read_text(encoding="utf-8"))
    sitio = datos.get("sitio", {})
    cfg.url = sitio.get("url", cfg.url)
    cfg.marca = sitio.get("marca", cfg.marca)
    cfg.max_paginas = int(sitio.get("max_paginas", cfg.max_paginas))
    cfg.concurrencia = int(sitio.get("concurrencia", cfg.concurrencia))
    org = datos.get("organizacion", {})
    cfg.organizacion = Organizacion(**{k: v for k, v in org.items() if k in Organizacion.__dataclass_fields__})
    for c in datos.get("sem", {}).get("campanas", []):
        cfg.campanas.append(
            Campana(
                landing=c["landing"],
                palabras_clave=list(c.get("palabras_clave", [])),
                solo_anuncios=bool(c.get("solo_anuncios", True)),
            )
        )
    return cfg
