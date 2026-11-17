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
