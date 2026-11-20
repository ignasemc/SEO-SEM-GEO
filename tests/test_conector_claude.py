"""Conector de Tríada para Claude (servidor MCP): una conversación real por stdio, como la haría Claude."""

import json
import os
import sys
import zipfile
from pathlib import Path

import anyio
import pytest

from servidor import arrancar

pytest.importorskip("mcp.server.mcpserver")
from mcp.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402


def _datos(res):
    assert not res.is_error, res.content
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def test_conversacion_completa(tmp_path):
    srv, url = arrancar()
    params = StdioServerParameters(command=sys.executable, args=["-m", "triada.conector_claude"],
                                   env={**os.environ, "TRIADA_DATOS": str(tmp_path)},
                                   cwd=str(Path(__file__).parent.parent))

    async def conversacion():
        async with Client(params) as c:
            nombres = {t.name for t in (await c.list_tools()).tools}
            assert {"analizar_web", "ver_problemas", "ver_correcciones", "editar_correccion",
                    "aplicar_correcciones", "exportar_informe", "listar_analisis", "deshacer_aplicacion",
                    "ver_pagina", "nueva_correccion"} <= nombres

            r = _datos(await c.call_tool("analizar_web", {"url": url, "max_paginas": 50, "marca": "Reformas Norte"}))
            aid = r["analisis"]
            assert r["paginas_analizadas"] >= 5 and set(r["puntuaciones"]) == {"seo", "sem", "geo"}

            pr = _datos(await c.call_tool("ver_problemas", {"analisis": aid, "rama": "seo", "limite": 5}))
            assert pr["problemas"] and all("seo" in p["ramas"] for p in pr["problemas"])

            nd = _datos(await c.call_tool("ver_correcciones", {"analisis": aid, "rama": "seo",
                                                               "estado": "necesitan_datos"}))
            assert nd["total"] >= 1 and all("«REVISAR»" in x["valor_propuesto"] for x in nd["correcciones"])
            # completar un «REVISAR» con un dato dado por el usuario
            f = nd["correcciones"][0]
            nuevo = f["valor_propuesto"].replace("«REVISAR»", "DATO-DEL-USUARIO")
            ed = _datos(await c.call_tool("editar_correccion", {"analisis": aid, "id": f["id"], "valor": nuevo}))
            assert ed["lista_para_aplicar"]

            # Claude lee una página y añade un título con la palabra clave
            pag = _datos(await c.call_tool("ver_pagina", {"analisis": aid, "url": url}))
            assert pag["texto"] and pag["h1"]
            nc = _datos(await c.call_tool("nueva_correccion", {
                "analisis": aid, "url": url + "contacto/", "tipo": "titulo",
                "valor": "Contacto reformas Madrid | Reformas Norte", "explicacion": "Palabra clave local."}))
            assert nc["guardada"]

            ap = _datos(await c.call_tool("aplicar_correcciones", {"analisis": aid, "rama": "seo"}))
            assert ap["aplicados"] >= 10 and ap["paginas_descargadas_de_la_web"] >= 5
            z = zipfile.ZipFile(ap["archivo"])
            # un archivo por página, llamado como su título (sin caracteres prohibidos en Windows)
            assert "Reformas Norte - Reformas integrales en Madrid.html" in z.namelist()
            assert f["id"] in z.read("LEEME.txt").decode()

            # lo aplicado ya no sale como pendiente; GEO tiene su propia selección
            pend = _datos(await c.call_tool("ver_correcciones", {"analisis": aid, "rama": "seo", "estado": "listas"}))
            assert pend["total"] == 0
            inf = _datos(await c.call_tool("exportar_informe", {"analisis": aid, "rama": "geo"}))
            assert Path(inf["archivo"]).read_text(encoding="utf-8").startswith("<!")

            mal = await c.call_tool("ver_problemas", {"analisis": "no-existe"})
            assert mal.is_error

    try:
        anyio.run(conversacion)
    finally:
        srv.shutdown()


def test_registrar_en_claude_desktop_conserva_otros_conectores(tmp_path):
    from triada import instalar_claude

    ruta = tmp_path / "Claude" / "claude_desktop_config.json"
    ruta.parent.mkdir()
    ruta.write_text(json.dumps({"mcpServers": {"otro": {"command": "x"}}, "tema": "oscuro"}), encoding="utf-8")
    copia = instalar_claude.registrar(ruta, instalar_claude.entrada_servidor("/ruta/python"))
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["tema"] == "oscuro" and datos["mcpServers"]["otro"] == {"command": "x"}
    assert datos["mcpServers"]["triada"]["args"] == ["-m", "triada.conector_claude"]
    assert copia and copia.exists()
