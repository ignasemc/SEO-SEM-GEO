"""Optimización con IA: palabras clave, «REVISAR» resueltos sin inventar datos y aplicación al HTML."""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from servidor import arrancar

REVISAR = "«REVISAR»"


@pytest.fixture()
def analisis_real(tmp_path, monkeypatch):
    """Análisis de la web de prueba como si fuera una web real (no la demo: usa el cliente de Claude)."""
    monkeypatch.setenv("TRIADA_DATOS", str(tmp_path))
    from triada.app import servicio

    srv, url = arrancar()
    aid = servicio.ejecutar_analisis({"url": url, "marca": "Reformas Norte", "max_paginas": 50})
    yield aid
    srv.shutdown()


class ClaudeFalso:
    """Imita client.beta.messages.create y guarda las peticiones."""

    def __init__(self):
        self.peticiones = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))

    def _crear(self, **kw):
        self.peticiones.append(kw)
        if "system" not in kw:  # prueba de conexión
            return SimpleNamespace(stop_reason="end_turn", model=kw["model"],
                                   content=[SimpleNamespace(type="text", text="ok")],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=2))
        pagina = json.loads(kw["messages"][0]["content"])
        corr = []
        for f in pagina["correcciones"]:
            v = f["valor_propuesto"]
            if f["tipo"].startswith("jsonld"):
                from triada.ia import _quitar_revisar

                d = _quitar_revisar(json.loads(v))  # lo que no está en la página se quita
                if d.get("@type") == "Product":
                    d["sku"] = "SR-01"  # dato que «aparece en la página»
                v = json.dumps(d, ensure_ascii=False)
            elif f["tipo"] == "titulo":
                v = "Reformas integrales en Madrid | Reformas Norte"
            elif f["tipo"] == "meta_description":
                v = "Reformas integrales en Madrid con presupuesto cerrado y garantía. Pide tu visita gratis hoy."
            corr.append({"id": f["id"], "valor": v, "explicacion": "Incluye la palabra clave principal."})
        nuevas = [{"tipo": "meta_description", "valor": "Reformas integrales en Madrid con presupuesto cerrado. "
                   "Pide tu visita gratis hoy.", "explicacion": "Descripción con la palabra clave."}]
        res = {"palabras_clave": {"principal": "reformas integrales madrid", "secundarias": ["reforma cocina"],
                                  "intencion": "comercial"}, "correcciones": corr, "nuevas": nuevas}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(res))],
                               usage=SimpleNamespace(input_tokens=5000, output_tokens=2000,
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))


def test_optimizar_y_aplicar_con_claude(analisis_real):
    from triada import ia
    from triada.app import servicio, subidas

    aid = analisis_real
    a = servicio.leer_analisis(aid)
    con_revisar = [f["id"] for f in a["fixes"] if "seo" in f["ramas"] and REVISAR in f["valor_propuesto"]]
    assert con_revisar

    claude = ClaudeFalso()
    r = ia.optimizar_y_aplicar(aid, "seo", cliente=claude)
    assert "system" not in claude.peticiones[0]  # primero se comprueba la conexión
    p = claude.peticiones[1]
    assert p["model"] == "claude-opus-5-5" and p["fallbacks"] == "default"
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert "Reformas Norte" in p["system"][0]["text"]

    assert r["ia"]["revisar_completados"] >= 1 and r["ia"]["nuevas_correcciones"] >= 1
    assert r["ia"]["coste_aprox_usd"] > 0 and not r["ia"]["paginas_con_error"]
    ed = servicio.leer_ediciones(aid)
    assert all(REVISAR not in ed[i] for i in con_revisar if i in ed)
    # nuevas correcciones con palabra clave, en el análisis y con su grupo
    a = servicio.leer_analisis(aid)
    nuevas = [f for f in a["fixes"] if f["codigo_issue"] == "palabras_clave"]
    assert nuevas and any(i["codigo"] == "palabras_clave" for i in a["issues"])
    est = ia.estado(aid)
    assert any(v["palabras_clave"]["principal"] == "reformas integrales madrid" for v in est["paginas"].values())

    # todo aplicado al HTML: los «REVISAR» completados por la IA también
    assert set(con_revisar) & set(r["aplicados"])
    assert {f["id"] for f in nuevas} <= set(r["aplicados"])
    z = zipfile.ZipFile(io.BytesIO(subidas.zip_modificados(aid)))
    html = z.read("productos/silla-roble/index.html").decode()
    assert '"sku": "SR-01"' in html and REVISAR not in html
    assert "Reformas integrales en Madrid" in z.read("index.html").decode()

    # repetir no duplica las nuevas correcciones
    ia.optimizar(aid, "seo", cliente=ClaudeFalso())
    a2 = servicio.leer_analisis(aid)
    assert len([f for f in a2["fixes"] if f["codigo_issue"] == "palabras_clave"]) == len(nuevas)


def test_sin_clave_avisa(analisis_real):
    from triada import ia

    with pytest.raises(ia.ErrorIA, match="clave de Claude"):
        ia.optimizar(analisis_real, "seo")


def test_demo_usa_motor_simulado(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIADA_DATOS", str(tmp_path))
    from triada import ia
    from triada.app import server, servicio

    demo = server.iniciar_demo()
    aid = servicio.ejecutar_analisis(demo["config"])
    est = ia.estimacion(aid, "seo")
    assert est["simulado"] and est["coste_aprox_usd"] == 0
    r = ia.optimizar_y_aplicar(aid, "seo")
    assert r["ia"]["simulado"] and r["aplicados"]
    ed = servicio.leer_ediciones(aid)
    assert ed and all(REVISAR not in v for v in ed.values())


def _error_api(clase, estado, mensaje):
    import anthropic
    import httpx2

    r = httpx2.Response(estado, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
                        headers={"request-id": "req_prueba"})
    return getattr(anthropic, clase)(mensaje, response=r,
                                     body={"type": "error", "error": {"type": "x", "message": mensaje}})


class ClaudeConErrores(ClaudeFalso):
    """Rechaza las peticiones con fallbacks (como una cuenta sin acceso a esa beta) o todas."""

    def __init__(self, todas=None, solo_fallbacks=None):
        super().__init__()
        self.todas, self.solo_fallbacks = todas, solo_fallbacks

    def _crear(self, **kw):
        if self.todas:
            raise self.todas
        if self.solo_fallbacks and "fallbacks" in kw:
            raise self.solo_fallbacks
        return super()._crear(**kw)


def test_cuenta_sin_permiso_para_fallbacks_sigue_sin_ellos(analisis_real):
    from triada import ia

    ia._SIN_FALLBACKS.clear()
    claude = ClaudeConErrores(solo_fallbacks=_error_api(
        "PermissionDeniedError", 403, "Your API key does not have permission to use the specified resource."))
    assert ia.probar_conexion(claude)["ok"]
    r = ia.optimizar(analisis_real, "seo", cliente=claude)
    assert r["correcciones_mejoradas"] and not r["paginas_con_error"]
    assert "claude-opus-5-5" in ia._SIN_FALLBACKS
    assert all("fallbacks" not in p for p in claude.peticiones[1:])  # no se vuelve a intentar con fallbacks
    ia._SIN_FALLBACKS.clear()


@pytest.mark.parametrize("clase,estado,esperado", [
    ("AuthenticationError", 401, "no reconoce la clave"),
    ("PermissionDeniedError", 403, "no tiene permiso"),
    ("NotFoundError", 404, "no existe o tu cuenta no tiene acceso"),
    ("BadRequestError", 400, "saldo"),
])
def test_errores_de_la_api_se_explican(analisis_real, clase, estado, esperado):
    from triada import ia

    ia._SIN_FALLBACKS.clear()
    mensaje = "Your credit balance is too low." if estado == 400 else "Mensaje original de Anthropic"
    claude = ClaudeConErrores(todas=_error_api(clase, estado, mensaje))
    with pytest.raises(ia.ErrorIA) as e:
        ia.optimizar(analisis_real, "seo", cliente=claude)
    assert esperado in str(e.value) and mensaje in str(e.value) and "req_prueba" in str(e.value)
    ia._SIN_FALLBACKS.clear()


def test_clave_que_no_es_de_api_se_explica(analisis_real):
    from triada import ia
    from triada.externo import almacen

    almacen.guardar_ajustes({"claves": {"anthropic": "sk-ant-usr-1234567890"}})
    claude = ClaudeConErrores(todas=_error_api("AuthenticationError", 401, "invalid x-api-key"))
    with pytest.raises(ia.ErrorIA, match="«sk-ant-usr-…», y eso no es una clave de API"):
        ia.probar_conexion(claude)
