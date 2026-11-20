"""Solucionar sobre archivos HTML subidos y exportar el HTML modificado."""

import base64
import io
import zipfile
from pathlib import Path

import pytest

DEMO = Path(__file__).parent.parent / "triada" / "app" / "demo_sitio"


@pytest.fixture()
def analisis(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIADA_DATOS", str(tmp_path))
    from triada.app import server, servicio

    demo = server.iniciar_demo()
    return servicio.ejecutar_analisis(demo["config"]), demo


def b64(datos: bytes) -> str:
    return "data:text/html;base64," + base64.b64encode(datos).decode()


def test_subir_sueltos_emparejar_aplicar_y_exportar(analisis):
    from triada.app import servicio, subidas

    aid, _ = analisis
    a = servicio.leer_analisis(aid)
    # archivos sueltos, como los subiría alguien desde su ordenador
    archivos = [
        {"nombre": "index.html", "contenido": b64((DEMO / "index.html").read_bytes())},
        {"nombre": "silla.html", "contenido": b64((DEMO / "productos/silla-roble/index.html").read_bytes())},
        {"nombre": "reformas.html", "contenido": b64((DEMO / "servicios/reformas/index.html").read_bytes())},
        {"nombre": "guia-reformas.html", "contenido": b64((DEMO / "blog/guia-reformas/index.html").read_bytes())},
        {"nombre": "otra-cosa.html", "contenido": b64(b"<html><head><title>Nada</title></head><body></body></html>")},
    ]
    est = subidas.subir(aid, archivos)
    por = {x["nombre"]: x for x in est["archivos"]}
    assert por["index.html"]["url"] == a["url"] + "/"
    assert por["reformas.html"]["url"].endswith("/servicios/reformas/") and por["reformas.html"]["metodo"] == "canonical"
    assert por["silla.html"]["url"].endswith("/productos/silla-roble/") and por["silla.html"]["metodo"] == "título"
    assert por["guia-reformas.html"]["metodo"] == "nombre del archivo"
    assert por["otra-cosa.html"]["url"] is None

    seo = [f["id"] for f in a["fixes"] if "seo" in f["ramas"] and "«REVISAR»" not in f["valor_propuesto"]]
    r = subidas.aplicar(aid, seo, {})
    assert r["aplicados"] and r["subidas"]["modificados"] >= 2
    # aplicar dos veces no duplica (p. ej. H1 o fragmentos)
    r2 = subidas.aplicar(aid, seo, {})
    assert r2["subidas"]["aplicados"] == r["subidas"]["aplicados"]

    datos, nombre = subidas.archivo(aid, "silla.html")
    html = datos.decode()
    assert nombre == "silla.html" and 'alt="' in html
    z = zipfile.ZipFile(io.BytesIO(subidas.zip_modificados(aid)))
    assert {"index.html", "silla.html", "reformas.html", "CAMBIOS.txt"} <= set(z.namelist())
    assert "sitemap.xml" in z.namelist()  # archivo generado aplicado

    # asignación manual y restablecer
    subidas.asignar(aid, "otra-cosa.html", por["index.html"]["url"])
    assert next(x for x in subidas.estado(aid)["archivos"] if x["nombre"] == "otra-cosa.html")["metodo"] == "manual"
    original = (DEMO / "productos/silla-roble/index.html").read_bytes()
    subidas.restablecer(aid)
    assert subidas.archivo(aid, "silla.html")[0] == original
    assert subidas.estado(aid)["aplicados"] == []


def test_subir_zip_con_carpetas_y_seguridad(analisis):
    from triada.app import subidas
    from triada.app.aplicar import ErrorAplicacion

    aid, _ = analisis
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in DEMO.rglob("*.html"):
            z.write(f, "miweb/" + str(f.relative_to(DEMO)))
        z.writestr("../../malo.html", "<html></html>")
    est = subidas.subir(aid, [{"nombre": "web.zip", "contenido": base64.b64encode(buf.getvalue()).decode()}])
    nombres = {x["nombre"] for x in est["archivos"]}
    assert "servicios/reformas/index.html" in nombres and "malo.html" not in " ".join(nombres)
    assert all(x["metodo"] in ("canonical", "ruta del archivo") for x in est["archivos"] if x["url"])
    with pytest.raises(ErrorAplicacion):
        subidas.subir(aid, [{"nombre": "x.exe", "contenido": base64.b64encode(b"MZ").decode()}])
    with pytest.raises(ErrorAplicacion):
        subidas.archivo(aid, "../estado.json")


def test_exportar_modificados_de_carpeta(analisis):
    from triada.app import aplicar, servicio
    from triada.app.aplicar import ErrorAplicacion

    aid, demo = analisis
    with pytest.raises(ErrorAplicacion):
        aplicar.zip_modificados_carpeta(aid)
    a = servicio.leer_analisis(aid)
    ids = [f["id"] for f in a["fixes"] if "seo" in f["ramas"] and "«REVISAR»" not in f["valor_propuesto"]]
    aplicar.aplicar_en_carpeta(aid, ids, {}, demo["carpeta"])
    z = zipfile.ZipFile(io.BytesIO(aplicar.zip_modificados_carpeta(aid)))
    assert "productos/silla-roble/index.html" in z.namelist()


def test_aplicar_trayendo_el_html_desde_la_web(analisis):
    """Sin archivos subidos (p. ej. web en WordPress): se descarga el HTML de la web y se aplica todo."""
    from urllib.parse import urlparse

    import httpx

    from triada.app import servicio, subidas

    aid, _ = analisis
    a = servicio.leer_analisis(aid)

    def responder(peticion: httpx.Request) -> httpx.Response:
        ruta = urlparse(str(peticion.url)).path.strip("/")
        archivo = DEMO / ruta / "index.html" if ruta else DEMO / "index.html"
        if not archivo.exists():
            return httpx.Response(404)
        return httpx.Response(200, content=archivo.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})

    cliente = httpx.Client(transport=httpx.MockTransport(responder))
    seo = [f["id"] for f in a["fixes"] if "seo" in f["ramas"] and "«REVISAR»" not in f["valor_propuesto"]]
    r = subidas.aplicar(aid, seo, {}, desde_web=True, cliente=cliente)
    assert r["descargadas"] >= 4 and not r["fallidas"]
    assert not [o for o in r["omitidos"] if "Ningún archivo" in o["motivo"]]
    nombres = {x["nombre"]: x for x in r["subidas"]["archivos"]}
    assert nombres["index.html"]["metodo"] == "descargada de la web"
    assert "productos/silla-roble/index.html" in nombres
    z = zipfile.ZipFile(io.BytesIO(subidas.zip_modificados(aid)))
    assert {"index.html", "productos/silla-roble/index.html", "CAMBIOS.txt"} <= set(z.namelist())
    # el original descargado se conserva para poder restablecerlo
    subidas.restablecer(aid)
    assert subidas.archivo(aid, "index.html")[0] == (DEMO / "index.html").read_bytes()


def test_zip_por_titulo_de_pagina(analisis):
    """Tras aplicar, el .zip tiene un archivo por página llamado como su título, sin repetir nombres."""
    import httpx

    from triada.app import servicio, subidas

    aid, _ = analisis
    a = servicio.leer_analisis(aid)

    def responder(peticion):
        from urllib.parse import urlparse

        ruta = urlparse(str(peticion.url)).path.strip("/")
        archivo = DEMO / ruta / "index.html" if ruta else DEMO / "index.html"
        return httpx.Response(200, content=archivo.read_bytes(), headers={"content-type": "text/html"}) \
            if archivo.exists() else httpx.Response(404)

    seo = [f["id"] for f in a["fixes"] if "seo" in f["ramas"] and "«REVISAR»" not in f["valor_propuesto"]]
    subidas.aplicar(aid, seo, {}, desde_web=True, cliente=httpx.Client(transport=httpx.MockTransport(responder)))
    z = zipfile.ZipFile(io.BytesIO(subidas.zip_por_titulo(aid)))
    nombres = z.namelist()
    html = [n for n in nombres if n.endswith(".html")]
    assert "Reformas Norte - Reformas integrales en Madrid.html" in html
    assert "Silla de roble macizo.html" in html
    # la home y el artículo del blog tienen el mismo título: el segundo lleva su dirección
    assert any(n.startswith("Reformas Norte - Reformas integrales en Madrid (") for n in html)
    assert len(html) == len(set(n.lower() for n in html))
    assert all(not set('<>:"|?*\\') & set(n) for n in nombres)
    leeme = z.read("LEEME.txt").decode()
    assert "/productos/silla-roble/" in leeme and "Silla de roble macizo.html" in leeme
    # el contenido es el HTML modificado, no el original
    assert z.read("Silla de roble macizo.html") != (DEMO / "productos/silla-roble/index.html").read_bytes()


def test_nombre_de_archivo_valido():
    from triada.app.subidas import nombre_de_archivo

    assert nombre_de_archivo('Camas: "Premium" | Petground') == "Camas Premium - Petground"
    assert nombre_de_archivo("CON") == "CON pagina" and nombre_de_archivo("   ") == "pagina"
    assert len(nombre_de_archivo("palabra " * 40)) <= 90
