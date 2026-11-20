import json

import pytest

from triada.audits import auditar, puntuaciones
from triada.audits.base import paginas_contenido
from triada.config import Campana, Config
from triada.crawler import rastrear_sync
from triada.fixes import generar, proponer_titulo
from triada.report import construir_datos, escribir

from servidor import arrancar


@pytest.fixture(scope="module")
def sitio():
    srv, url = arrancar()
    cfg = Config(url=url, marca="Reformas Norte", max_paginas=50)
    cfg.campanas = [Campana("/oferta-reformas/", ["reformas integrales madrid", "reforma cocina"], True)]
    site = rastrear_sync(cfg)
    yield cfg, site
    srv.shutdown()


def codigos(issues):
    return {i.codigo for i in issues}


def test_rastreo(sitio):
    cfg, site = sitio
    paginas = paginas_contenido(site)
    assert len(paginas) >= 7
    tipos = {p.tipo for p in paginas}
    assert {"home", "producto", "servicio", "articulo", "landing", "contacto"} <= tipos
    assert site.sitemaps and site.robots_txt


def test_seo(sitio):
    cfg, site = sitio
    c = codigos(auditar(site, cfg, {"seo"}, combinado=False))
    for esperado in ("enlace_roto", "titulo_duplicado", "titulo_largo", "meta_desc_falta", "meta_desc_corta",
                     "alt_falta", "pagina_huerfana", "contenido_escaso", "sitemap_sucio", "depende_js",
                     "schema_producto", "organizacion_falta", "quienes_somos_falta"):
        assert esperado in c, esperado
    # sin reglas exclusivas de otras ramas
    assert "bloquea_bots_busqueda_ia" not in c and "landing_sin_ads_tag" not in c


def test_sem(sitio):
    cfg, site = sitio
    issues = auditar(site, cfg, {"sem"}, combinado=False)
    c = codigos(issues)
    assert "mensaje_no_coincide" in c
    assert "sin_consent_mode" in c
    assert "landing_sin_cta" not in c  # la landing tiene formulario
    assert all(i.ramas == {"sem"} for i in issues)


def test_geo(sitio):
    cfg, site = sitio
    issues = auditar(site, cfg, {"geo"}, combinado=False)
    c = codigos(issues)
    assert "bloquea_bots_busqueda_ia" in c and "bloquea_bots_entrenamiento" in c
    assert "autor_falta" in c and "faq_falta" in c
    i = next(i for i in issues if i.codigo == "bloquea_bots_busqueda_ia")
    assert "OAI-SearchBot" in i.mensaje and "PerplexityBot" in i.mensaje


def test_combinado_resuelve_conflicto(sitio):
    cfg, site = sitio
    landing = next(p.final_url for p in paginas_contenido(site) if p.tipo == "landing")
    indep = auditar(site, cfg, {"seo", "sem", "geo"}, combinado=False)
    comb = auditar(site, cfg, {"seo", "sem", "geo"}, combinado=True)
    escaso_indep = next(i for i in indep if i.codigo == "contenido_escaso")
    assert landing in escaso_indep.urls
    escaso_comb = next((i for i in comb if i.codigo == "contenido_escaso"), None)
    assert escaso_comb is None or landing not in escaso_comb.urls
    conflicto = next(i for i in comb if i.codigo == "landing_ads_indexable")
    assert conflicto.urls == [landing] and conflicto.ramas == {"seo", "sem"}


def test_correcciones(sitio, tmp_path):
    cfg, site = sitio
    issues = auditar(site, cfg, {"seo", "sem", "geo"}, combinado=True)
    fixes, archivos = generar(issues, site, cfg)
    tipos = {f.tipo for f in fixes}
    assert {"titulo", "meta_description", "jsonld", "archivo", "meta_robots", "snippet_head"} <= tipos
    # robots propuesto permite el bot de búsqueda de OpenAI (avisando de editar su grupo)
    assert "OAI-SearchBot" in archivos["robots.txt"]
    # JSON-LD de producto válido y con el precio detectado
    prod = json.loads(next(v for k, v in archivos.items() if k.startswith("jsonld/producto")))
    assert prod["offers"]["price"] == "129.00"
    # el título de la landing incorpora la palabra clave de la campaña y respeta 60 caracteres
    landing = next(p for p in paginas_contenido(site) if p.tipo == "landing")
    t = proponer_titulo(landing, cfg)
    assert "reformas integrales madrid" in t.lower() and len(t) <= 60
    datos = construir_datos(site, issues, fixes, ["seo", "sem", "geo"], True, cfg)
    ruta = escribir(tmp_path, datos, archivos)
    assert (tmp_path / "informe-sem.html").exists()
    assert all(f.id and f.ramas for f in fixes)
    html = ruta.read_text()
    assert "Prioridades" in html and "OAI-SearchBot" in html
    assert (tmp_path / "correcciones.csv").exists()
    pts = puntuaciones(issues, len(paginas_contenido(site)))
    assert all(0 <= v <= 100 for v in pts.values())


def test_robots_con_comodines():
    from triada.urls import ReglasRobots

    r = ReglasRobots("User-agent: Baiduspider\nDisallow: /\n\nUser-agent: *\nDisallow: /*filter_category=\n"
                     "Disallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\nDisallow: /*.pdf$\n")
    assert r.permite("TriadaBot", "https://x.es/tienda/")
    assert not r.permite("TriadaBot", "https://x.es/tienda/?filter_category=3")
    assert r.permite("TriadaBot", "https://x.es/wp-admin/admin-ajax.php")
    assert not r.permite("TriadaBot", "https://x.es/wp-admin/opciones")
    assert not r.permite("TriadaBot", "https://x.es/doc.pdf") and r.permite("TriadaBot", "https://x.es/doc.pdf?v=2")
    assert not r.permite("Baiduspider", "https://x.es/")


def test_imagenes_carga_diferida_pixeles_y_alt():
    from bs4 import BeautifulSoup

    from triada import imagenes

    html = """<main>
      <div class="product-wrapper"><a class="product-image-link" href="/p/">
        <img src="data:image/svg+xml,%3Csvg%3E" data-lazy-src="/up/cama-300x300.jpg" alt=""></a>
        <div class="hover-img"><img src="/up/cama-2.jpg" alt=""></div>
        <h3 class="wd-entities-title"><a href="/p/">Cama de Césped Natural</a></h3></div>
      <noscript><img height="1" width="1" src="https://www.facebook.com/tr?id=1&ev=PageView"></noscript>
      <img src="/themes/x/lazy.svg" data-src="/up/foto.jpg">
    </main><footer><img src="/up/logo_blanco_260x.png" alt=""></footer>"""
    soup = BeautifulSoup(html, "lxml")
    tarjeta, hover, pixel, foto, logo = soup.find_all("img")
    base = "https://x.es/"
    assert imagenes.src_real(tarjeta, base) == "https://x.es/up/cama-300x300.jpg"
    assert imagenes.src_real(foto, base) == "https://x.es/up/foto.jpg"
    assert imagenes.es_seguimiento(pixel, imagenes.src_real(pixel, base))
    assert imagenes.es_contenido(tarjeta) and not imagenes.es_contenido(hover) and not imagenes.es_contenido(logo)
    assert imagenes.alt_fiable(tarjeta, "", None, "home") == "Cama de Césped Natural"
    assert imagenes.clave_imagen("https://x.es/up/cama-300x300.jpg") == imagenes.clave_imagen("https://x.es/up/cama.jpg")
    for malo in ("Svg&gt;", "Tr", "Logo blanco x", "foto 1024x1024", "img_123.jpg", "1. petground empapdor perro pxxl x"):
        assert not imagenes.alt_valido(malo), malo
    assert imagenes.alt_valido("Cama de césped natural para perros")
