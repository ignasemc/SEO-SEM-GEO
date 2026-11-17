"""Operaciones de la app sobre los datos de fuera de la web de un proyecto."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable

from . import acciones as acc
from . import almacen, importar, monitor, senales
from .capas import calcular, valores_cuestionario
from .cuestionario import POR_ID, PREGUNTAS

DEMO_DATOS = Path(__file__).parent.parent / "app" / "demo_datos"
MAX_EJECUCIONES = 12


def estado(proyecto: str) -> dict[str, Any]:
    ext = almacen.leer(proyecto)
    lista = acc.sincronizar(ext)
    almacen.guardar(proyecto, ext)
    mon = ext["monitor"]
    ultima = mon["ejecuciones"][-1] if mon["ejecuciones"] else None
    return {
        "proyecto": proyecto,
        "perfil": ext["perfil"],
        "cuestionario": {"preguntas": PREGUNTAS, "valores": valores_cuestionario(ext),
                         "declarados": {k: v.get("valor") for k, v in ext["cuestionario"].items()}},
        "ads": ext["ads"], "search_console": ext["search_console"],
        "monitor": {"config": mon["config"], "ultima": ultima,
                    "historial": [{"fecha": e["fecha"], "pct_mencion": e["resumen"].get("pct_mencion"),
                                   "simulado": e.get("simulado"), "motores": e["motores"]} for e in mon["ejecuciones"]]},
        "resenas": ext["resenas"], "wikidata": ext["wikidata"],
        "acciones": lista,
        "ajustes": almacen.ajustes_publicos(),
    }


def capas_de(analisis: dict[str, Any]) -> dict[str, Any]:
    ext = almacen.leer(analisis["proyecto"])
    return calcular(analisis["puntuaciones"], analisis["ramas"], ext, almacen.leer_ajustes()["pesos"])


def asegurar_perfil(proyecto: str, marca: str, dominio: str) -> None:
    def f(d):
        if not d["perfil"].get("marca") and marca:
            d["perfil"]["marca"] = marca
        if not d["perfil"].get("dominio") and dominio:
            d["perfil"]["dominio"] = dominio
    almacen.modificar(proyecto, f)


def guardar_perfil(proyecto: str, perfil: dict[str, Any]) -> None:
    def f(d):
        for k in ("marca", "dominio", "ciudad"):
            if k in perfil:
                d["perfil"][k] = (perfil[k] or "").strip()
        if "alias" in perfil:
            al = perfil["alias"]
            d["perfil"]["alias"] = [a.strip() for a in (al.split(",") if isinstance(al, str) else al) if a.strip()]
    almacen.modificar(proyecto, f)


def responder(proyecto: str, pid: str, valor: str | None) -> None:
    if pid not in POR_ID:
        raise ValueError("Pregunta desconocida")
    if valor not in (None, "", "si", "parcial", "no", "na"):
        raise ValueError("Valor no válido")
    if valor == "na" and not POR_ID[pid].get("admite_na"):
        raise ValueError("Esta pregunta no admite «No aplica»")

    def f(d):
        if valor:
            d["cuestionario"][pid] = {"valor": valor}
        else:
            d["cuestionario"].pop(pid, None)
    almacen.modificar(proyecto, f)


def importar_archivo(proyecto: str, nombre: str, contenido_b64: str) -> list[dict[str, Any]]:
    datos = base64.b64decode(contenido_b64.split(",", 1)[-1])
    if len(datos) > 30 * 1024 * 1024:
        raise importar.ErrorImportacion("El archivo es demasiado grande (máx. 30 MB).")
    resultados = importar.importar(nombre, datos)

    def f(d):
        for fuente, r in resultados:
            d[fuente][r["tipo"]] = r
    almacen.modificar(proyecto, f)
    return [{"fuente": fu, "tipo": r["tipo"], "filas": r.get("filas", r.get("dominios"))} for fu, r in resultados]


def borrar_importacion(proyecto: str, fuente: str, tipo: str) -> None:
    def f(d):
        if fuente in ("ads", "search_console"):
            d[fuente].pop(tipo, None)
    almacen.modificar(proyecto, f)


def guardar_config_monitor(proyecto: str, cfg: dict[str, Any]) -> None:
    def f(d):
        c = d["monitor"]["config"]
        if "preguntas" in cfg:
            c["preguntas"] = [p.strip() for p in cfg["preguntas"] if p and p.strip()][:50]
        if "competidores" in cfg:
            c["competidores"] = [{"nombre": (x.get("nombre") or "").strip(), "dominio": (x.get("dominio") or "").strip()}
                                 for x in cfg["competidores"] if (x.get("nombre") or x.get("dominio"))][:10]
        if "motores" in cfg:
            c["motores"] = [m for m in cfg["motores"] if m in ("gemini", "openai", "perplexity")]
        if "repeticiones" in cfg:
            c["repeticiones"] = max(1, min(int(cfg["repeticiones"] or 1), 5))
    almacen.modificar(proyecto, f)


def motores_disponibles(proyecto: str, config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    if proyecto == "demo":
        return {"simulado": ("", "")}
    aj = almacen.leer_ajustes()
    return {m: (aj["claves"][m], aj["modelos"][m]) for m in config.get("motores", [])
            if aj["claves"].get(m)}


def ejecutar_monitor(proyecto: str, progreso: Callable | None = None, cliente=None) -> dict[str, Any]:
    ext = almacen.leer(proyecto)
    cfg = ext["monitor"]["config"]
    motores = motores_disponibles(proyecto, cfg)
    if not motores:
        raise monitor.ErrorMotor("Ningún buscador de IA seleccionado tiene clave. Añádela en Ajustes.")
    perfil = ext["perfil"]
    if not perfil.get("marca"):
        raise monitor.ErrorMotor("Indica el nombre de tu marca.")
    res = monitor.ejecutar(cfg, perfil, motores, progreso, cliente, n_ejecucion=len(ext["monitor"]["ejecuciones"]))

    def f(d):
        ej = d["monitor"]["ejecuciones"]
        ej.append(res)
        for viejo in ej[:-3]:  # conserva el texto completo solo de las 3 últimas
            viejo.pop("respuestas", None)
        del ej[:-MAX_EJECUCIONES]
    almacen.modificar(proyecto, f)
    return res["resumen"]


def buscar_resenas(proyecto: str, consulta: str, cliente=None) -> dict[str, Any]:
    ext = almacen.leer(proyecto)
    comps = ext["monitor"]["config"].get("competidores", [])
    if not consulta.strip():
        consulta = " ".join(x for x in (ext["perfil"].get("marca"), ext["perfil"].get("ciudad")) if x)
    r = senales.resenas(consulta, comps, almacen.leer_ajustes()["claves"].get("places", ""), cliente,
                        simulado=proyecto == "demo")

    almacen.modificar(proyecto, lambda d: d.__setitem__("resenas", r))
    return r


def comprobar_wikidata(proyecto: str, cliente=None) -> dict[str, Any]:
    ext = almacen.leer(proyecto)
    r = senales.wikidata(ext["perfil"].get("marca", ""), cliente, simulado=proyecto == "demo")
    almacen.modificar(proyecto, lambda d: d.__setitem__("wikidata", r))
    return r


def actualizar_accion(proyecto: str, aid: str, cambios: dict[str, Any]) -> None:
    almacen.modificar(proyecto, lambda d: acc.actualizar(d, aid, cambios))


def acciones_csv(proyecto: str) -> bytes:
    return acc.a_csv(estado(proyecto)["acciones"])


def cargar_ejemplo(proyecto: str) -> None:
    """Rellena el proyecto de demostración con datos de ejemplo claramente marcados."""
    if proyecto != "demo":
        raise ValueError("Los datos de ejemplo solo están disponibles en la demostración")
    almacen.guardar(proyecto, almacen.vacio())
    for f in sorted(DEMO_DATOS.iterdir()):
        importar_archivo(proyecto, f.name, base64.b64encode(f.read_bytes()).decode())
    guardar_perfil(proyecto, {"marca": "Reformas Norte", "dominio": "reformasnorte.example", "ciudad": "Madrid"})
    for pid, v in {"conv_primarias": "parcial", "conv_mejoradas": "no",
                   "revision_terminos": "no", "merchant_ok": "na", "directorios": "parcial",
                   "prensa": "no", "comparativas": "no", "perfiles_oficiales": "si"}.items():
        responder(proyecto, pid, v)
    guardar_config_monitor(proyecto, {
        "preguntas": ["mejor empresa de reformas en Madrid", "empresa de reformas integrales con presupuesto cerrado",
                      "cuánto cuesta reformar una cocina en Madrid", "reforma de baño en Madrid opiniones",
                      "empresas de reformas de confianza en Madrid"],
        "competidores": [{"nombre": "Reformas Castellana", "dominio": "reformascastellana.example"},
                         {"nombre": "Obras Retiro", "dominio": "obrasretiro.example"},
                         {"nombre": "Casa Nueva Reformas", "dominio": "casanueva.example"},
                         {"nombre": "Reformas Sol", "dominio": "reformassol.example"}],
        "repeticiones": 3})
    ejecutar_monitor(proyecto)
    buscar_resenas(proyecto, "Reformas Norte Madrid")
    comprobar_wikidata(proyecto)
