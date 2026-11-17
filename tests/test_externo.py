"""Pruebas de la parte de fuera de la web: importadores, clientes de API (simulados), capas y acciones."""

import base64
import json

import httpx
import pytest

from triada.externo import almacen, capas, importar, monitor, senales
from triada.externo import acciones as acc
from triada.externo import servicio as ext_srv


@pytest.fixture(autouse=True)
def datos(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIADA_DATOS", str(tmp_path))


# ------------------------------------------------------------------ importadores

def test_ads_ingles_utf16_tab():
    texto = ("Keyword report\nOct 1, 2026 - Oct 31, 2026\n"
             "Keyword\tCampaign\tQuality Score\tClicks\tImpr.\tCost\tConversions\tCost / conv.\n"
             "running shoes\tShoes\t3\t1,200\t20,000\t1,234.50\t10.00\t123.45\n"
             "trail shoes\tShoes\t8\t300\t4,000\t200.00\t5.00\t40.00\n"
             "Total: account\t\t\t1,500\t24,000\t1,434.50\t15.00\t95.63\n")
    [(fuente, r)] = importar.importar("ads.csv", b"\xff\xfe" + texto.encode("utf-16-le"))
    assert fuente == "ads" and r["tipo"] == "palabras_clave" and r["filas"] == 2
    assert r["coste"] == 1434.5
    assert r["qs_medio"] == round((3 * 20000 + 8 * 4000) / 24000, 2)
    assert r["qs_bajo"][0]["palabra"] == "running shoes"


def test_ads_terminos_es():
    texto = ('Término de búsqueda;Campaña;Clics;Coste;Conversiones\n'
             'reforma gratis;A;10;"12,50";"0,00"\n'
             'reforma cocina;A;20;"30,00";"2,00"\n')
    [(_, r)] = importar.importar("t.csv", texto.encode("utf-8"))
    assert r["tipo"] == "terminos" and r["coste_sin_conversion"] == 12.5 and r["pct_desperdicio"] == 29.4
    assert r["negativas"][0]["termino"] == "reforma gratis"


def test_formato_desconocido():
    with pytest.raises(importar.ErrorImportacion):
        importar.importar("x.csv", b"a,b,c\n1,2,3\n")


def test_demo_datos_se_importan():
    for f in ext_srv.DEMO_DATOS.iterdir():
        assert importar.importar(f.name, f.read_bytes())


# ------------------------------------------------------------------ parsers de los buscadores de IA

GEMINI = {"steps": [
    {"type": "thought", "summary": [], "signature": "x"},
    {"type": "google_search_call", "arguments": {"queries": ["reformas madrid"]}},
    {"type": "model_output", "content": [{"type": "text", "text": "Recomiendo Reformas Norte y Obras Retiro.",
                                          "annotations": [{"type": "url_citation", "url": "https://guia.example/a",
                                                           "title": "Guía", "start_index": 0, "end_index": 10}]}]}]}
OPENAI = {"output": [{"type": "web_search_call", "id": "ws1"},
                     {"type": "message", "content": [{"type": "output_text", "text": "Obras Retiro es buena opción.",
                                                      "annotations": [{"type": "url_citation", "url": "https://www.reformasnorte.example/x"}]}]}]}
PERPLEXITY = {"choices": [{"message": {"content": "Casa Nueva Reformas [1]"}}],
              "search_results": [{"title": "Foro", "url": "https://foro.example/t"}], "citations": ["https://foro.example/t"]}


def test_parsers():
    t, f = monitor.parsear_gemini(GEMINI)
    assert "Reformas Norte" in t and f[0]["url"] == "https://guia.example/a"
    t, f = monitor.parsear_openai(OPENAI)
    assert t.startswith("Obras") and f[0]["url"].startswith("https://www.reformasnorte")
    t, f = monitor.parsear_perplexity(PERPLEXITY)
    assert "Casa Nueva" in t and f == [{"url": "https://foro.example/t", "titulo": "Foro"}]


def test_monitor_con_apis_simuladas():
    vistos = []

    def responder(req: httpx.Request):
        vistos.append((req.url.host, dict(req.headers), json.loads(req.content)))
        if req.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(200, json=GEMINI)
        if req.url.host == "api.openai.com":
            return httpx.Response(200, json=OPENAI)
        if req.url.host == "api.perplexity.ai":
            return httpx.Response(429, json={"error": {"message": "rate limit"}})
        return httpx.Response(404)

    cliente = httpx.Client(transport=httpx.MockTransport(responder))
    perfil = {"marca": "Reformas Norte", "dominio": "reformasnorte.example"}
    cfg = {"preguntas": ["mejor empresa de reformas"], "repeticiones": 2,
           "competidores": [{"nombre": "Obras Retiro"}, {"nombre": "Casa Nueva Reformas"}]}
    res = monitor.ejecutar(cfg, perfil, {"gemini": ("k1", "gemini-3.8-flash"), "openai": ("k2", "gpt-5.5"),
                                         "perplexity": ("k3", "sonar")}, cliente=cliente)
    r = res["resumen"]
    assert r["respuestas"] == 4 and r["errores"] == 2
    assert r["por_motor"]["perplexity"]["error_ejemplo"].startswith("HTTP 429")
    assert r["por_motor"]["gemini"]["pct_mencion"] == 100.0
    assert r["por_motor"]["openai"]["pct_cita_web"] == 100.0  # cita reformasnorte.example
    assert r["cuota"]["Obras Retiro"] == 100.0
    assert any(f["dominio"] == "guia.example" and f["tipo"] == "tercero" for f in r["fuentes"])
    g = next(v for v in vistos if v[0] == "generativelanguage.googleapis.com")
    assert g[1]["x-goog-api-key"] == "k1" and g[2]["tools"] == [{"type": "google_search"}]
    o = next(v for v in vistos if v[0] == "api.openai.com")
    assert o[1]["authorization"] == "Bearer k2" and o[2]["tools"] == [{"type": "web_search"}]


def test_mencion_sin_falsos_positivos():
    assert monitor.menciona("Te recomiendo Reformas Norte.", ["reformas norte"])
    assert not monitor.menciona("Reformas al norte de Madrid", ["Reformas Norte"])
    assert monitor.menciona("según reformasnorte.example", [], "reformasnorte.example")


def test_places_y_wikidata():
    def responder(req: httpx.Request):
        if req.url.host == "places.googleapis.com":
            assert req.headers["x-goog-api-key"] == "kp" and "places.rating" in req.headers["x-goog-fieldmask"]
            q = json.loads(req.content)["textQuery"]
            n = 300 if "Retiro" in q else 12
            return httpx.Response(200, json={"places": [{"displayName": {"text": q}, "rating": 4.5, "userRatingCount": n}]})
        if req.url.host == "www.wikidata.org":
            assert req.url.params["action"] == "wbsearchentities"
            return httpx.Response(200, json={"search": [{"id": "Q1", "label": "Reformas Norte", "description": "empresa"}]})
        return httpx.Response(404)

    cli = httpx.Client(transport=httpx.MockTransport(responder))
    r = senales.resenas("Reformas Norte Madrid", [{"nombre": "Obras Retiro"}], "kp", cli)
    assert r["tu"]["resenas"] == 12 and r["competidores"][0]["resenas"] == 300
    assert senales.valor_resenas(r)[0] == "no"  # 12 reseñas frente a una mediana de 300
    w = senales.wikidata("Reformas Norte", cli)
    assert w["coincide"] and w["candidatos"][0]["id"] == "Q1"


# ------------------------------------------------------------------ capas y acciones

def test_sin_datos_no_inventa_nota():
    c = capas.calcular({"seo": 80, "sem": 70, "geo": 60}, ["seo", "sem", "geo"], almacen.vacio(),
                       almacen.PESOS_DEFECTO)
    for r in ("sem", "geo"):
        assert c[r]["total"] is None and c[r]["externo"]["valor"] is None and c[r]["resultado"]["valor"] is None
    assert c["geo"]["cobertura"] == 30 and c["geo"]["provisional"]
    # SEO es interno: su nota es la de la web, completa
    assert c["seo"]["interno"] and c["seo"]["total"] == 80 and c["seo"]["cobertura"] == 100


def test_demo_completa_y_autoverificacion():
    ext_srv.cargar_ejemplo("demo")
    est = ext_srv.estado("demo")
    ids = {a["id"] for a in est["acciones"]}
    assert {"ads-negativas", "ads-nivel-calidad", "cu-comparativas", "cu-wikidata"} <= ids
    assert all("seo" not in a["ramas"] for a in est["acciones"])
    assert all("seo" not in p["ramas"] for p in est["cuestionario"]["preguntas"])
    assert any(i.startswith("ia-fuente-") for i in ids)
    c = capas.calcular({"seo": 50, "sem": 60, "geo": 55}, ["seo", "sem", "geo"], almacen.leer("demo"),
                       almacen.PESOS_DEFECTO)
    assert all(c[r]["total"] is not None for r in c)
    assert c["sem"]["resultado"]["valor"] is not None and c["geo"]["resultado"]["fuente"].startswith("Monitor de IA · datos simulados")

    # el usuario marca una acción como en curso con responsable
    ext_srv.actualizar_accion("demo", "cu-comparativas", {"estado": "en_curso", "responsable": "Ana"})
    a = next(a for a in ext_srv.estado("demo")["acciones"] if a["id"] == "cu-comparativas")
    assert a["estado"] == "en_curso" and a["responsable"] == "Ana"

    # responder «sí» en el cuestionario la da por hecha, pero no «verificada» (es una declaración)
    ext_srv.responder("demo", "comparativas", "si")
    a = next(a for a in ext_srv.estado("demo")["acciones"] if a["id"] == "cu-comparativas")
    assert a["estado"] == "hecho" and not a["verificada"]

    # una fuente que la IA deja de citar NO se da por resuelta
    def quitar_fuente(d):
        u = d["monitor"]["ejecuciones"][-1]["resumen"]
        u["fuentes"] = [f for f in u["fuentes"] if f["dominio"] != "guiadelhogar.example"]
    almacen.modificar("demo", quitar_fuente)
    a = next(a for a in ext_srv.estado("demo")["acciones"] if a["id"] == "ia-fuente-guiadelhogar.example")
    assert a["estado"] == "pendiente"

    # quitar la respuesta no la da por resuelta: desaparece hasta tener datos
    ext_srv.responder("demo", "prensa", "no")
    ext_srv.responder("demo", "prensa", None)
    assert "cu-prensa" not in {a["id"] for a in ext_srv.estado("demo")["acciones"]}

    # un informe nuevo sin desperdicio resuelve la acción de negativas
    limpio = "Término de búsqueda,Coste,Conversiones\nreforma cocina,30.00,2\n"
    ext_srv.importar_archivo("demo", "t.csv", base64.b64encode(limpio.encode()).decode())
    a = next(a for a in ext_srv.estado("demo")["acciones"] if a["id"] == "ads-negativas")
    assert a["estado"] == "hecho" and a["verificada"]
    assert acc.a_csv(ext_srv.estado("demo")["acciones"]).startswith("﻿".encode())


def test_proyecto_real_no_usa_simulado():
    ext_srv.guardar_config_monitor("miweb.es", {"preguntas": ["x"], "motores": ["gemini"]})
    ext_srv.guardar_perfil("miweb.es", {"marca": "Mi Web"})
    with pytest.raises(monitor.ErrorMotor, match="clave"):
        ext_srv.ejecutar_monitor("miweb.es")
    with pytest.raises(ValueError):
        ext_srv.cargar_ejemplo("miweb.es")


def test_acciones_seo_antiguas_desaparecen():
    """Datos guardados por la versión 0.3 (con acciones SEO) no deben aparecer como resueltas."""
    def viejo(d):
        d["acciones"]["sc-ctr"] = {"estado": "pendiente", "snapshot": {"id": "sc-ctr", "ramas": ["seo"], "accion": "x",
                                   "porque": "", "prioridad": "media", "esfuerzo": "bajo", "verificacion": ""}}
        d["acciones"]["cu-sc_verificada"] = {"estado": "pendiente", "snapshot": {"id": "cu-sc_verificada", "ramas": ["seo"]}}
    almacen.modificar("miweb.es", viejo)
    assert ext_srv.estado("miweb.es")["acciones"] == []
