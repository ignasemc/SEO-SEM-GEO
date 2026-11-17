"""Prueba de extremo a extremo de la app: demo → analizar → solucionar → reanalizar → deshacer."""

import json
import threading
import time
import urllib.request

import pytest


@pytest.fixture(scope="module")
def app(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("TRIADA_DATOS", str(tmp_path_factory.mktemp("datos")))
    from triada.app.server import crear_servidor

    srv = crear_servidor(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def llamar(base, ruta, cuerpo=None, metodo=None, cabecera=True):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(base + ruta, data=datos, method=metodo or ("POST" if datos else "GET"))
    req.add_header("Content-Type", "application/json")
    if cabecera:
        req.add_header("X-Triada", "1")
    try:
        with urllib.request.urlopen(req) as r:
            cuerpo = r.read()
            return r.status, (json.loads(cuerpo) if r.headers.get_content_type() == "application/json" else cuerpo)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def analizar(base, config):
    estado, r = llamar(base, "/api/analizar", config)
    assert estado == 200, r
    for _ in range(100):
        _, t = llamar(base, f"/api/trabajo/{r['trabajo']}")
        if t["estado"] != "rastreando":
            break
        time.sleep(0.1)
    assert t["estado"] == "terminado", t
    return llamar(base, f"/api/analisis/{t['analisis']}")[1]


def test_flujo_completo(app):
    assert llamar(app, "/")[0] == 200
    # sin cabecera de seguridad, las acciones se rechazan
    assert llamar(app, "/api/demo", {}, cabecera=False)[0] == 403

    _, demo = llamar(app, "/api/demo", {})
    a1 = analizar(app, demo["config"])
    assert a1["total_paginas"] >= 7 and set(a1["puntuaciones"]) == {"seo", "sem", "geo"}

    # aplicar todas las correcciones sin «REVISAR» de la rama SEO
    ids = [f["id"] for f in a1["fixes"] if "seo" in f["ramas"] and "«REVISAR»" not in f["valor_propuesto"]]
    _, res = llamar(app, "/api/solucionar", {"analisis": a1["id"], "ids": ids, "destino": "carpeta",
                                             "carpeta": demo["carpeta"]})
    assert len(res["aplicados"]) >= len(ids) * 0.8, res["omitidos"]

    # las que llevan «REVISAR» se omiten salvo que se editen
    revisar = next(f for f in a1["fixes"] if "«REVISAR»" in f["valor_propuesto"])
    _, res2 = llamar(app, "/api/solucionar", {"analisis": a1["id"], "ids": [revisar["id"]], "destino": "carpeta",
                                              "carpeta": demo["carpeta"]})
    assert res2["omitidos"] and not res2["aplicados"]

    a2 = analizar(app, demo["config"])
    assert a2["puntuaciones"]["seo"] > a1["puntuaciones"]["seo"]

    # deshacer devuelve la web a su estado original
    _, d = llamar(app, "/api/deshacer", {"aplicacion": res["id"]})
    assert d["deshecha"]
    a3 = analizar(app, demo["config"])
    assert a3["puntuaciones"]["seo"] == a1["puntuaciones"]["seo"]

    # exportaciones
    for fmt in ("html", "csv", "json", "zip"):
        estado, _ = llamar(app, f"/api/exportar/{a1['id']}/{fmt}")
        assert estado == 200, fmt
    estado, html = llamar(app, f"/api/exportar/{a1['id']}/html?rama=geo&ver=1")
    assert estado == 200 and "Buscadores con IA" in html.decode()

    # paquete descargable
    _, p = llamar(app, "/api/solucionar", {"analisis": a1["id"], "ids": ids, "destino": "paquete",
                                           "carpeta": demo["carpeta"]})
    estado, z = llamar(app, p["descarga"])
    assert estado == 200 and z[:2] == b"PK"

    _, lista = llamar(app, "/api/analisis")
    assert len(lista) == 3
