"""Conector de Tríada para Claude (servidor MCP local).

Permite que Claude (Claude Desktop o Claude Code) analice una web y aplique las correcciones de SEO, SEM y GEO
usando Tríada en el ordenador del usuario. Todo se ejecuta en local: los datos y los archivos se quedan en la
carpeta de datos de Tríada (~/Triada o $TRIADA_DATOS).

    python -m triada.conector_claude            # arranca el servidor (lo lanza Claude, no hace falta a mano)
    triada conectar-claude                      # lo registra en Claude Desktop

Necesita el paquete opcional «mcp»: pip install -e ".[claude]"
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import anyio

from . import __version__
from .app import aplicar, servicio, subidas
from . import ia
from .app.aplicar import ErrorAplicacion
from .externo import servicio as externo
from .fixes import REVISAR
from .report import render_html

try:  # paquete opcional: pip install -e ".[claude]"
    from mcp.server.mcpserver import Context, MCPServer
    from mcp_types import ToolAnnotations
except ImportError:  # la lógica de abajo se puede usar (y probar) sin él
    Context = MCPServer = ToolAnnotations = None  # type: ignore[assignment,misc]

Rama = Literal["seo", "sem", "geo"]
SEVERIDADES = ("critica", "alta", "media", "baja")

INSTRUCCIONES = """\
Tríada audita y corrige webs de empresa en tres vertientes: SEO (posicionamiento orgánico), SEM (Google Ads)
y GEO (visibilidad en buscadores con IA). Se ejecuta en el ordenador del usuario. Responde siempre en español.

Flujo habitual (el usuario puede no saber de programación: hazlo tú de principio a fin):
1. analizar_web con la página principal (rastrea todas las páginas del dominio: landings, quiénes somos, contacto…).
2. ver_correcciones de la vertiente pedida. Para cada página con correcciones, ver_pagina para leer su contenido.
3. Recomienda la palabra clave principal y 2-5 secundarias de cada página y úsalas: reescribe títulos (30-60
   caracteres, palabra clave al principio, marca al final), meta descripciones (120-155, con llamada a la acción) y
   H1 con editar_correccion; si el título o la descripción actuales no llevan la palabra clave y no hay corrección,
   crea una con nueva_correccion.
4. Resuelve tú los «REVISAR» con editar_correccion: usa los datos que aparecen en la página o en sus datos
   estructurados (precio, marca, SKU…). Si un dato no aparece, quita esa propiedad del JSON-LD.
5. aplicar_correcciones (destino html) y explica el resultado, las palabras clave elegidas y dónde está el .zip.

Reglas que hay que respetar:
- Trabaja cada vertiente por separado salvo que el usuario pida varias.
- Nunca inventes GTIN/EAN, SKU, precios, valoraciones, reseñas, teléfonos ni direcciones: o están en la página, o
  te los da el usuario, o se quita el campo. Las palabras clave son recomendaciones tuyas a partir del contenido,
  no volúmenes de búsqueda medidos: dilo así.
- Las puntuaciones son un indicador interno para priorizar: no prometas posiciones en Google ni citas en la IA.
- Antes de aplicar en una carpeta del ordenador del usuario, confirma con él la carpeta: se modifican sus archivos
  (con copia de seguridad y opción de deshacer).
- Solo se analiza el dominio indicado; nunca otras webs.
"""


def _crear_servidor():
    if MCPServer is None:  # pragma: no cover - mensaje para el usuario
        raise SystemExit('Falta el paquete «mcp». Instálalo con: pip install -e ".[claude]"')

    mcp = MCPServer("Tríada", instructions=INSTRUCCIONES, version=__version__)
    lectura = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def analizar_web(url: str, ramas: list[Rama] | None = None, max_paginas: int = 200, marca: str = "",
                           combinado: bool = True, ctx: Context | None = None) -> dict[str, Any]:
        """Rastrea la web indicada (solo su dominio) y la audita en SEO, SEM y GEO.

        Devuelve el id del análisis (úsalo en el resto de herramientas), las puntuaciones internas y los
        problemas más importantes. Puede tardar unos minutos en webs grandes.

        Args:
            url: Dirección de la web, p. ej. https://www.miempresa.com
            ramas: Vertientes a auditar (por defecto las tres).
            max_paginas: Máximo de páginas a rastrear (1-2000).
            marca: Nombre de la marca, para títulos y datos estructurados.
            combinado: Resolver conflictos entre vertientes (recomendado).
        """
        peticion = {"url": url, "ramas": ramas or ["seo", "sem", "geo"], "max_paginas": max_paginas,
                    "marca": marca, "combinado": combinado}
        avance: dict[str, Any] = {"n": 0, "aid": None}

        async def correr() -> None:
            avance["aid"] = await anyio.to_thread.run_sync(
                lambda: servicio.ejecutar_analisis(peticion, lambda n: avance.update(n=n)))

        async with anyio.create_task_group() as tg:
            tg.start_soon(correr)
            while avance["aid"] is None:
                await anyio.sleep(2)
                if ctx is not None and avance["aid"] is None:
                    await ctx.report_progress(avance["n"], max_paginas, f"{avance['n']} páginas rastreadas")
        return resumen_analisis(avance["aid"])

    @mcp.tool(annotations=lectura)
    def listar_analisis() -> list[dict[str, Any]]:
        """Lista los análisis guardados (más recientes primero) con su id, web, fecha y puntuaciones."""
        return [{k: a.get(k) for k in ("id", "host", "fecha", "ramas", "puntuaciones", "total_paginas",
                                       "problemas", "correcciones", "demo")}
                for a in servicio.listar_analisis()]

    @mcp.tool(annotations=lectura)
    def ver_problemas(analisis: str, rama: Rama | None = None, severidad: str | None = None,
                      limite: int = 50) -> dict[str, Any]:
        """Problemas detectados en un análisis, ordenados por prioridad.

        Args:
            analisis: Id del análisis.
            rama: Filtrar por vertiente (seo, sem o geo).
            severidad: Filtrar por severidad (critica, alta, media o baja).
            limite: Número máximo de problemas.
        """
        return ver_problemas_de(analisis, rama, severidad, limite)

    @mcp.tool(annotations=lectura)
    def ver_correcciones(analisis: str, rama: Rama | None = None,
                         estado: Literal["todas", "listas", "necesitan_datos", "aplicadas", "pendientes"] = "pendientes",
                         desde: int = 0, limite: int = 50) -> dict[str, Any]:
        """Correcciones propuestas para un análisis, con su valor actual y el propuesto.

        «listas» se pueden aplicar ya; «necesitan_datos» contienen «REVISAR» y hay que completarlas con
        editar_correccion antes de aplicarlas. Usa desde/limite para paginar si hay muchas.

        Args:
            analisis: Id del análisis.
            rama: Filtrar por vertiente.
            estado: pendientes (listas + necesitan_datos, sin aplicar), listas, necesitan_datos, aplicadas o todas.
            desde: Posición desde la que empezar (paginación).
            limite: Número máximo de correcciones.
        """
        return ver_correcciones_de(analisis, rama, estado, desde, limite)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                          openWorldHint=False))
    def editar_correccion(analisis: str, id: str, valor: str) -> dict[str, Any]:
        """Cambia el valor propuesto de una corrección (p. ej. para completar los «REVISAR» o mejorar un título).

        Usa solo datos que te haya dado el usuario o que se vean en su web; no inventes SKU, GTIN, precios ni marcas.

        Args:
            analisis: Id del análisis.
            id: Id de la corrección (F001…).
            valor: Nuevo valor completo (texto, o el JSON-LD entero si es un dato estructurado).
        """
        return editar(analisis, id, valor)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def aplicar_correcciones(analisis: str, rama: Rama, ids: list[str] | None = None,
                                   destino: Literal["html", "paquete", "carpeta"] = "html",
                                   carpeta: str | None = None) -> dict[str, Any]:
        """Aplica las correcciones de una vertiente y genera el resultado.

        Sin «ids» se aplican todas las que están listas en esa vertiente (las «REVISAR» sin completar se omiten).

        Destinos:
        - html (por defecto): descarga de la web el HTML de las páginas afectadas, lo corrige y deja un .zip con
          el HTML modificado. No toca la web del usuario.
        - paquete: .zip con la lista de cambios, archivos generados e instrucciones para WordPress/Shopify.
        - carpeta: modifica los archivos de una web estática en el ordenador del usuario (con copia de seguridad
          y opción de deshacer). Confirma la carpeta con el usuario antes.

        Args:
            analisis: Id del análisis.
            rama: Vertiente cuyas correcciones se aplican.
            ids: Ids concretos (F001…); si se omite, todas las listas de la vertiente.
            destino: html, paquete o carpeta.
            carpeta: Ruta de la carpeta de la web (solo con destino «carpeta»).
        """
        return await anyio.to_thread.run_sync(lambda: aplicar_rama(analisis, rama, ids, destino, carpeta))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    def exportar_informe(analisis: str, rama: Rama | None = None,
                         formato: Literal["html", "csv", "json"] = "html") -> dict[str, Any]:
        """Guarda el informe del análisis (completo o de una vertiente) y devuelve la ruta del archivo.

        Args:
            analisis: Id del análisis.
            rama: Solo esa vertiente (opcional).
            formato: html (informe para abrir en el navegador o imprimir a PDF), csv (correcciones, para Excel) o json.
        """
        return exportar(analisis, rama, formato)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
    async def ver_pagina(analisis: str, url: str) -> dict[str, Any]:
        """Contenido de una página del análisis: título, meta descripción, encabezados, texto visible y datos
        estructurados existentes. Úsalo para elegir palabras clave y para completar los «REVISAR» con datos reales.

        Args:
            analisis: Id del análisis.
            url: URL de la página (tal como aparece en ver_correcciones).
        """
        return await anyio.to_thread.run_sync(lambda: ver_pagina_de(analisis, url))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    def nueva_correccion(analisis: str, url: str, tipo: Literal["titulo", "meta_description"], valor: str,
                         rama: Rama = "seo", explicacion: str = "") -> dict[str, Any]:
        """Crea una corrección nueva de título o meta descripción para una página (p. ej. para incluir la palabra
        clave principal cuando la actual no la lleva). Si ya existe una de ese tipo para la página, edítala con
        editar_correccion.

        Args:
            analisis: Id del análisis.
            url: URL de la página.
            tipo: titulo o meta_description.
            valor: Texto nuevo.
            rama: Vertiente en la que debe aparecer (además de SEO).
            explicacion: Por qué se propone (palabra clave elegida), en una frase sencilla.
        """
        return ia.agregar_correccion(analisis, url, tipo, valor, rama, explicacion)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
    def deshacer_aplicacion(aplicacion: str) -> dict[str, Any]:
        """Deshace una aplicación en carpeta (restaura la copia de seguridad).

        Args:
            aplicacion: Id de la aplicación que devolvió aplicar_correcciones con destino «carpeta».
        """
        info = aplicar.deshacer(aplicacion)
        return {"aplicacion": info["id"], "deshecha": True, "archivos_restaurados": len(info["archivos"])}

    return mcp


# ------------------------------------------------------------------ lógica (sin dependencia de mcp, testeable)

def _analisis(aid: str) -> dict[str, Any]:
    a = servicio.leer_analisis(aid)
    if a is None:
        raise ErrorAplicacion(f"No existe el análisis «{aid}». Usa listar_analisis para ver los disponibles.")
    return a


leer_ediciones = servicio.leer_ediciones


def _valor(f: dict[str, Any], ediciones: dict[str, str]) -> str:
    return ediciones.get(f["id"], f["valor_propuesto"])


def _lista(f: dict[str, Any], ediciones: dict[str, str]) -> bool:
    v = _valor(f, ediciones)
    return REVISAR not in v and v.strip() != ""


def _aplicadas(a: dict[str, Any]) -> set[str]:
    ids = set(subidas.estado(a["id"])["aplicados"])
    for info in aplicar.listar_aplicaciones(a["id"]):
        if not info["deshecha"]:
            ids.update(info["aplicados"])
    return ids


def resumen_analisis(aid: str) -> dict[str, Any]:
    a = _analisis(aid)
    return {
        "analisis": aid, "web": a["url"], "paginas_analizadas": a["total_paginas"], "ramas": a["ramas"],
        "puntuaciones": {r: a["puntuaciones"][r] for r in a["ramas"]},
        "nota_puntuaciones": "Indicador interno de 0 a 100 para priorizar; no es una métrica de Google.",
        "problemas": len(a["issues"]),
        "correcciones": {r: sum(1 for f in a["fixes"] if r in f.get("ramas", [])) for r in a["ramas"]},
        "principales_problemas": ver_problemas_de(aid, None, None, 10)["problemas"],
        "paginas_fuera_del_sitio_ignoradas": sum(1 for e in a.get("errores", []) if "fuera del sitio" in e),
    }


def ver_problemas_de(aid: str, rama: str | None, severidad: str | None, limite: int) -> dict[str, Any]:
    a = _analisis(aid)
    lista = [i for i in a["issues"] if (not rama or rama in i["ramas"]) and (not severidad or i["severidad"] == severidad)]
    return {"total": len(lista), "problemas": [{
        "codigo": i["codigo"], "ramas": sorted(i["ramas"]), "severidad": i["severidad"], "mensaje": i["mensaje"],
        "recomendacion": i["recomendacion"], "paginas_afectadas": len(i.get("urls") or []),
        "ejemplos": (i.get("urls") or [])[:5], **({"nota": i["nota_conflicto"]} if i.get("nota_conflicto") else {}),
    } for i in lista[:max(1, limite)]]}


def ver_correcciones_de(aid: str, rama: str | None, estado: str, desde: int, limite: int) -> dict[str, Any]:
    a = _analisis(aid)
    ed, hechas = leer_ediciones(aid), _aplicadas(a)
    filas = []
    for f in a["fixes"]:
        if rama and rama not in f.get("ramas", []):
            continue
        aplicada, lista = f["id"] in hechas, _lista(f, ed)
        est = "aplicada" if aplicada else ("lista" if lista else "necesita_datos")
        if estado == "listas" and est != "lista" or estado == "necesitan_datos" and est != "necesita_datos" \
                or estado == "aplicadas" and not aplicada or estado == "pendientes" and aplicada:
            continue
        filas.append({"id": f["id"], "ramas": f.get("ramas", []), "tipo": f["tipo"], "pagina": f.get("url"),
                      "problema": f.get("codigo_issue"), "estado": est, "valor_actual": f.get("valor_actual"),
                      "valor_propuesto": _valor(f, ed), "editada": f["id"] in ed,
                      **({"nota": f["nota"]} if f.get("nota") else {})})
    c = Counter(x["estado"] for x in filas)
    return {"total": len(filas), "desde": desde, "por_estado": dict(c),
            "correcciones": filas[max(0, desde):max(0, desde) + max(1, limite)]}


def ver_pagina_de(aid: str, url: str, cliente=None) -> dict[str, Any]:
    a = _analisis(aid)
    if url not in {p["url"] for p in a["paginas"]}:
        raise ErrorAplicacion("Esa URL no es una página de este análisis.")
    subidas.traer_de_la_web(aid, [url], cliente)
    nombre = next((x["nombre"] for x in subidas.estado(aid)["archivos"] if x["url"] == url), None)
    ruta = subidas._base(aid) / "original" / (nombre or "")
    if not nombre or not ruta.is_file():
        raise ErrorAplicacion("No se pudo descargar la página.")
    pagina = next(p for p in a["paginas"] if p["url"] == url)
    return {"url": url, "tipo": pagina.get("tipo"), **ia.texto_pagina(subidas._texto(ruta.read_bytes())),
            "palabras_clave_de_contexto": ia.contexto_palabras(a)}


def editar(aid: str, fid: str, valor: str) -> dict[str, Any]:
    a = _analisis(aid)
    f = next((x for x in a["fixes"] if x["id"] == fid), None)
    if f is None:
        raise ErrorAplicacion(f"No existe la corrección «{fid}» en este análisis.")
    if not valor.strip():
        raise ErrorAplicacion("El valor no puede estar vacío.")
    servicio.guardar_ediciones(aid, {fid: valor})
    motivo = aplicar._validar(f | {"valor_propuesto": valor})
    return {"id": fid, "guardada": True, "lista_para_aplicar": motivo is None, **({"aviso": motivo} if motivo else {})}


def _carpeta_exportaciones() -> Path:
    d = servicio.carpeta_datos() / "exportaciones"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resumen_omitidos(omitidos: list[dict[str, str]]) -> dict[str, int]:
    return dict(Counter(o["motivo"] for o in omitidos).most_common())


def aplicar_rama(aid: str, rama: str, ids: list[str] | None, destino: str, carpeta: str | None,
                 cliente=None) -> dict[str, Any]:
    a = _analisis(aid)
    ed, hechas = leer_ediciones(aid), _aplicadas(a)
    de_rama = {f["id"]: f for f in a["fixes"] if rama in f.get("ramas", [])}
    if ids:
        fuera = [i for i in ids if i not in de_rama]
        if fuera:
            raise ErrorAplicacion(f"Estas correcciones no son de {rama.upper()}: {', '.join(fuera)}")
        elegidas = list(ids)
    else:
        elegidas = [i for i, f in de_rama.items() if _lista(f, ed) and i not in hechas]
    if not elegidas:
        return {"aplicados": 0, "mensaje": f"No hay correcciones pendientes listas para aplicar en {rama.upper()}."}
    base = f"{a['host'].replace(':', '_')}-{rama}-{servicio.nuevo_id()}"

    if destino == "html":
        r = subidas.aplicar(aid, elegidas, ed, desde_web=True, cliente=cliente)
        res = {"aplicados": len(r["aplicados"]), "paginas_descargadas_de_la_web": r["descargadas"],
               "omitidos": _resumen_omitidos(r["omitidos"]),
               "paginas_que_no_se_pudieron_descargar": r["fallidas"][:20]}
        if r["subidas"]["archivos"]:
            zip_ruta = _carpeta_exportaciones() / f"html-modificado-{base}.zip"
            zip_ruta.write_bytes(subidas.zip_por_titulo(aid))
            res["archivo"] = str(zip_ruta)
            res["nota"] = ("El .zip tiene un archivo por página, llamado como el título de la página, y LEEME.txt con la "
                           "dirección de cada una y los cambios. "
                           "En WordPress o Shopify los cambios se pasan desde su editor o con el destino «paquete».")
        return res
    if destino == "paquete":
        zip_ruta = aplicar.generar_paquete(aid, elegidas, ed)
        return {"aplicados": 0, "correcciones_en_el_paquete": len(elegidas), "archivo": str(zip_ruta),
                "nota": "El paquete no modifica nada: incluye la lista de cambios, archivos e instrucciones (LEEME.txt)."}
    if destino == "carpeta":
        if not carpeta:
            raise ErrorAplicacion("Indica la carpeta de la web (y confírmala con el usuario).")
        info = aplicar.aplicar_en_carpeta(aid, elegidas, ed, carpeta)
        return {"aplicados": len(info["aplicados"]), "omitidos": _resumen_omitidos(info["omitidos"]),
                "aplicacion": info["id"], "carpeta": info["carpeta"],
                "nota": "Se guardó copia de seguridad: deshacer_aplicacion la restaura."}
    raise ErrorAplicacion("Destino no válido: usa html, paquete o carpeta.")


def exportar(aid: str, rama: str | None, formato: str) -> dict[str, Any]:
    a = _analisis(aid)
    if rama and rama not in a["ramas"]:
        raise ErrorAplicacion(f"Este análisis no incluye {rama.upper()}.")
    nombre = f"triada-{a['host'].replace(':', '_')}-{aid[:15]}{'-' + rama if rama else ''}"
    salida = _carpeta_exportaciones()
    if formato == "html":
        est = externo.estado(a["proyecto"])
        acciones = [x for x in est["acciones"] if not rama or rama in x["ramas"]]
        ruta = salida / f"{nombre}.html"
        ruta.write_text(render_html(a, rama, capas=externo.capas_de(a), acciones=acciones), encoding="utf-8")
    elif formato == "csv":
        ruta = salida / f"{nombre}-correcciones.csv"
        aplicar.escribir_csv(ruta, [f for f in a["fixes"] if not rama or rama in f.get("ramas", [])])
    elif formato == "json":
        if rama:
            a = a | {"issues": [i for i in a["issues"] if rama in i["ramas"]],
                     "fixes": [f for f in a["fixes"] if rama in f.get("ramas", [])], "ramas": [rama]}
        ruta = salida / f"{nombre}.json"
        ruta.write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        raise ErrorAplicacion("Formato no válido: usa html, csv o json.")
    return {"archivo": str(ruta)}


def main() -> None:
    _crear_servidor().run("stdio")


if __name__ == "__main__":
    main()
