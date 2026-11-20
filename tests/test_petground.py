"""Regresión con el rastreo real de petground.es (20/11/2026, grabado en tests/fixtures; sin internet).

Comprueba que no vuelven los fallos del brief (sección 5): variantes con canonical contadas como páginas,
sitemap con parámetros, alt inventados a partir de marcadores, píxeles o nombres de archivo.
"""

import re

import pytest

from fixture_petground import FIXTURE, analizar

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="falta el fixture de petground")


@pytest.fixture(scope="module")
def petground():
    site, issues, fixes, archivos, paginas = analizar()
    return {"site": site, "issues": {i.codigo: i for i in issues}, "fixes": fixes, "archivos": archivos,
            "paginas": paginas}


# ------------------------------------------------------------------ 1.4 canonical y sitemap

def test_paginas_canonicas_coinciden_con_el_sitemap(petground):
    from triada.urls import clave_url

    site, paginas = petground["site"], petground["paginas"]
    indexables = {clave_url(p.final_url) for p in paginas if p.indexable}
    assert indexables == {clave_url(u) for u in site.urls_sitemap}  # las 123 URLs del sitemap de Yoast
    assert len(indexables) == 123
    # el resto son páginas de sistema con noindex (carrito, mi cuenta, recuperar contraseña…)
    resto = [p for p in paginas if not p.indexable]
    assert any(p.final_url.endswith("/mi-cuenta/recuperar/") for p in resto)
    assert all("noindex" in p.meta_robots for p in resto)


def test_variantes_con_parametros_no_son_paginas(petground):
    from triada.audits.base import variantes

    vs = variantes(petground["site"])
    con_parametros = [p for p in vs if "?" in p.final_url]
    assert len(con_parametros) >= 23  # ?per_page=, ?shop_view=… con canonical a la categoría
    assert all(p.variante_de for p in con_parametros)
    assert not any("?" in p.final_url and p.variante_de for p in petground["paginas"])


def test_sin_imagenes_del_sitemap_como_paginas(petground):
    site = petground["site"]
    assert not any(re.search(r"\.(jpe?g|png|webp|gif|svg)$", u, re.I) for u in site.urls_sitemap)
    assert not any(re.search(r"\.(jpe?g|png|webp|gif|svg)$", u, re.I) for u in site.pages)


def test_respeta_robots_con_comodines(petground):
    # robots.txt de petground: Disallow: /*filter_category= y /*?orderby=
    assert not any("filter_category=" in u or "?orderby=" in u for u in petground["site"].pages)


def test_sitemap_no_se_sustituye_si_lo_genera_yoast(petground):
    assert petground["site"].generador_sitemap == "Yoast SEO"
    assert "sitemap.xml" not in petground["archivos"]
    assert "fuera_sitemap" not in petground["issues"]  # ninguna canónica indexable falta en el sitemap


def test_sitemap_propuesto_sin_parametros(petground):
    from triada.fixes import sitemap_propuesto

    xml = sitemap_propuesto(petground["site"])
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    assert len(locs) == 123 and not any("?" in u for u in locs)


def test_titulos_duplicados_solo_entre_canonicas(petground):
    dup = petground["issues"]["titulo_duplicado"].urls
    assert not any("?" in u or "/page/" in u for u in dup)
    assert len(dup) < 30  # antes: 165, inflado por las variantes


# ------------------------------------------------------------------ 1.1 texto alternativo

def _alts(petground):
    return [f for f in petground["fixes"] if f.tipo.startswith("alt:")]


def test_ningun_alt_basura(petground):
    for f in _alts(petground):
        v = f.valor_propuesto
        assert not re.fullmatch(r"Svg&gt;|Svg>|Tr|Img x", v), v
        # tamaños de archivo (260x, 1024x1024); una medida del producto como «84x84cm» sí es válida
        assert not re.search(r"\b\d{2,5}x(\d{2,5})?\b(?!\s*(cm|mm|m)\b)", v) and not v.endswith(" x"), v


def test_sin_alt_para_marcadores_ni_pixeles(petground):
    for f in _alts(petground):
        src = f.tipo[4:]
        assert not src.startswith("data:") and not src.endswith("lazy.svg") and "facebook.com/tr" not in src


def test_alt_agrupado_por_imagen_y_con_nombre_de_producto(petground):
    alts = _alts(petground)
    assert len(alts) < 150  # antes: 3.005 (una por imagen y página, con marcadores y píxeles)
    claves = [f.tipo for f in alts]
    assert len(claves) == len(set(claves))
    recambio = [f for f in alts if "recambio-xl" in f.tipo]
    assert recambio and recambio[0].valor_propuesto == "Recambio de Césped Natural" and len(recambio[0].paginas) > 10


def test_alt_vacio_en_imagen_decorativa_no_es_error(petground):
    # el logo del pie (alt="") y la segunda imagen de las tarjetas (efecto al pasar el ratón) son decorativas
    assert not any("logo_blanco" in f.tipo for f in _alts(petground))
