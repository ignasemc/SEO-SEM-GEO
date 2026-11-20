"""Servidor local de la app (solo escucha en 127.0.0.1).

No necesita dependencias extra: usa http.server de la biblioteca estándar.
"""

from __future__ import annotations

import functools
import http.server
import json
import mimetypes
import shutil
import socket
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import __version__
from .. import ia
from ..externo import almacen
from ..externo import servicio as externo
from ..externo.importar import ErrorImportacion
from ..externo.monitor import ErrorMotor
from ..report import render_html
from . import aplicar, servicio, subidas

ESTATICOS = Path(__file__).parent / "static"
DEMO_ORIGEN = Path(__file__).parent / "demo_sitio"

TRABAJOS: dict[str, dict[str, Any]] = {}
_DEMO: dict[str, Any] = {}
_bloqueo = threading.Lock()


# ------------------------------------------------------------------ demo

class _DemoHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def iniciar_demo() -> dict[str, Any]:
    """Copia la web de demostración (con errores intencionados) y la sirve en un puerto local."""
    destino = servicio.carpeta_datos() / "demo-sitio"
    with _bloqueo:
        if "servidor" not in _DEMO:
            destino.mkdir(parents=True, exist_ok=True)
            srv = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(_DemoHandler, directory=str(destino)))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _DEMO["servidor"] = srv
        puerto = _DEMO["servidor"].server_port
        # restaurar la web de demo a su estado original (con errores)
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(DEMO_ORIGEN, destino)
        sm = destino / "sitemap.xml"
        sm.write_text(sm.read_text(encoding="utf-8").replace("PUERTO", str(puerto)), encoding="utf-8")
        externo.cargar_ejemplo("demo")
    url = f"http://127.0.0.1:{puerto}/"
    return {
        "url": url,
        "carpeta": str(destino),
        "config": {
            "url": url, "marca": "Reformas Norte", "max_paginas": 50, "ramas": ["seo", "sem", "geo"],
            "combinado": True, "carpeta_sitio": str(destino), "demo": True,
            "organizacion": {"nombre_legal": "Reformas Norte S.L.", "logo": f"{url}img/logo.png",
                             "telefono": "+34 900 000 000", "email": "hola@reformasnorte.es",
                             "direccion": "", "fundacion": "2010",
                             "redes": ["https://www.linkedin.com/company/reformas-norte-demo"]},
            "campanas": [{"landing": "/oferta-reformas/", "palabras_clave": ["reformas integrales madrid"],
                          "solo_anuncios": True}],
        },
    }


# ------------------------------------------------------------------ trabajos

def lanzar_analisis(peticion: dict[str, Any]) -> str:
    tid = servicio.nuevo_id()
    TRABAJOS[tid] = {"id": tid, "estado": "rastreando", "paginas": 0, "max": int(peticion.get("max_paginas") or 200),
                     "analisis": None, "error": None}

    def progreso(n: int) -> None:
        TRABAJOS[tid]["paginas"] = n

    def correr() -> None:
        try:
            aid = servicio.ejecutar_analisis(peticion, progreso)
            TRABAJOS[tid].update(estado="terminado", analisis=aid)
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            traceback.print_exc()
            TRABAJOS[tid].update(estado="error", error=str(e))

    threading.Thread(target=correr, daemon=True).start()
    return tid


def lanzar_monitor(proyecto: str) -> str:
    tid = servicio.nuevo_id()
    TRABAJOS[tid] = {"id": tid, "estado": "ejecutando", "hecho": 0, "total": 0, "error": None, "tipo": "monitor"}

    def progreso(hecho: int, total: int) -> None:
        TRABAJOS[tid].update(hecho=hecho, total=total)

    def correr() -> None:
        try:
            externo.ejecutar_monitor(proyecto, progreso)
            TRABAJOS[tid]["estado"] = "terminado"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            TRABAJOS[tid].update(estado="error", error=str(e))

    threading.Thread(target=correr, daemon=True).start()
    return tid


def lanzar_ia(aid: str, rama: str) -> str:
    """Optimiza con IA las páginas de una vertiente y aplica todas las correcciones al HTML (en segundo plano)."""
    tid = servicio.nuevo_id()
    TRABAJOS[tid] = {"id": tid, "estado": "ejecutando", "hecho": 0, "total": 0, "error": None, "tipo": "ia",
                     "resultado": None}

    def progreso(hecho: int, total: int) -> None:
        TRABAJOS[tid].update(hecho=hecho, total=total)

    def correr() -> None:
        try:
            res = ia.optimizar_y_aplicar(aid, rama, progreso=progreso)
            TRABAJOS[tid].update(estado="terminado", resultado=res)
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            traceback.print_exc()
            TRABAJOS[tid].update(estado="error", error=str(e))

    threading.Thread(target=correr, daemon=True).start()
    return tid


def _con_externo(a: dict[str, Any]) -> dict[str, Any]:
    a["capas"] = externo.capas_de(a)
    return a


# ------------------------------------------------------------------ HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"Triada/{__version__}"

    def log_message(self, fmt, *args):
        pass

    # -- respuestas
    def _json(self, datos: Any, estado: int = 200) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _bytes(self, cuerpo: bytes, tipo: str, descarga: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        if descarga:
            self.send_header("Content-Disposition", f'attachment; filename="{descarga}"')
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, estado: int, mensaje: str) -> None:
        self._json({"error": mensaje}, estado)

    def _cuerpo(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _autorizado(self) -> bool:
        # Cabecera propia: obliga al navegador a hacer preflight CORS, que este servidor no acepta.
        # Así ninguna otra web abierta en tu navegador puede lanzar acciones contra la app.
        return self.headers.get("X-Triada") == "1"

    # -- GET
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        partes = [p for p in u.path.split("/") if p]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if not partes or partes == ["index.html"]:
                return self._bytes((ESTATICOS / "index.html").read_bytes(), "text/html; charset=utf-8")
            if partes[0] == "static" and len(partes) == 2 and "/" not in partes[1] and ".." not in partes[1]:
                f = ESTATICOS / partes[1]
                if f.is_file():
                    return self._bytes(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            if partes[0] != "api":
                return self._error(404, "No encontrado")
            ruta = partes[1:]
            if ruta == ["info"]:
                return self._json({"version": __version__, "datos": str(servicio.carpeta_datos()),
                                   "demo": _DEMO.get("servidor") is not None})
            if ruta == ["analisis"]:
                return self._json(servicio.listar_analisis())
            if len(ruta) == 2 and ruta[0] == "analisis":
                a = servicio.leer_analisis(ruta[1])
                if a is None:
                    return self._error(404, "Análisis no encontrado")
                a["aplicaciones"] = aplicar.listar_aplicaciones(ruta[1])
                a["ediciones"] = servicio.leer_ediciones(ruta[1])
                a["ia"] = ia.estado(ruta[1])
                return self._json(_con_externo(a))
            if len(ruta) == 3 and ruta[0] == "ia" and ruta[2] == "estimacion":
                return self._json(ia.estimacion(ruta[1], q.get("rama", "seo")))
            if ruta == ["ajustes"]:
                return self._json(almacen.ajustes_publicos())
            if len(ruta) >= 2 and ruta[0] == "subidas":
                aid = ruta[1]
                if len(ruta) == 2:
                    return self._json(subidas.estado(aid))
                if ruta[2:] == ["descargar"]:
                    return self._bytes(subidas.zip_modificados(aid), "application/zip", f"html-modificado-{aid[:15]}.zip")
                if ruta[2:] == ["archivo"]:
                    datos, nombre = subidas.archivo(aid, q.get("nombre", ""))
                    tipo = "text/html; charset=utf-8" if nombre.endswith((".html", ".htm")) else "text/plain; charset=utf-8"
                    return self._bytes(datos, tipo, nombre)
            if len(ruta) == 2 and ruta[0] == "modificados":
                return self._bytes(aplicar.zip_modificados_carpeta(ruta[1]), "application/zip",
                                   f"html-modificado-carpeta-{ruta[1][:15]}.zip")
            if len(ruta) == 2 and ruta[0] == "proyecto":
                return self._json(externo.estado(ruta[1]))
            if len(ruta) == 3 and ruta[0] == "proyecto" and ruta[2] == "acciones.csv":
                return self._bytes(externo.acciones_csv(ruta[1]), "text/csv; charset=utf-8",
                                   f"plan-de-acciones-{ruta[1]}.csv")
            if len(ruta) == 2 and ruta[0] == "trabajo":
                t = TRABAJOS.get(ruta[1])
                return self._json(t) if t else self._error(404, "Trabajo no encontrado")
            if len(ruta) == 3 and ruta[0] == "exportar":
                return self._exportar(ruta[1], ruta[2], q)
            if len(ruta) == 2 and ruta[0] == "descargar":
                nombre = ruta[1]
                f = servicio.carpeta_datos() / "paquetes" / nombre
                if "/" in nombre or ".." in nombre or not f.is_file():
                    return self._error(404, "Archivo no encontrado")
                return self._bytes(f.read_bytes(), "application/zip", nombre)
            return self._error(404, "Ruta no encontrada")
        except aplicar.ErrorAplicacion as e:
            return self._error(400, str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, str(e))

    def _exportar(self, aid: str, formato: str, q: dict[str, str]) -> None:
        a = servicio.leer_analisis(aid)
        if a is None:
            return self._error(404, "Análisis no encontrado")
        rama = q.get("rama") if q.get("rama") in a["ramas"] else None
        base = f"triada-{a['host'].replace(':', '_')}-{aid[:15]}{'-' + rama if rama else ''}"
        if formato == "html":
            est = externo.estado(a["proyecto"])
            acciones = [x for x in est["acciones"] if not rama or rama in x["ramas"]]
            html = render_html(a, rama, imprimir=q.get("imprimir") == "1", capas=externo.capas_de(a),
                               acciones=acciones)
            return self._bytes(html.encode("utf-8"), "text/html; charset=utf-8",
                               None if q.get("ver") == "1" or q.get("imprimir") == "1" else f"{base}.html")
        if formato == "csv":
            fixes = [f for f in a["fixes"] if not rama or rama in f.get("ramas", [])]
            tmp = servicio.carpeta_analisis(aid) / f"_export-{rama or 'todo'}.csv"
            aplicar.escribir_csv(tmp, fixes)
            datos = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
            return self._bytes(datos, "text/csv; charset=utf-8", f"{base}-correcciones.csv")
        if formato == "json":
            if rama:
                a = a | {"issues": [i for i in a["issues"] if rama in i["ramas"]],
                         "fixes": [f for f in a["fixes"] if rama in f.get("ramas", [])], "ramas": [rama]}
            return self._bytes(json.dumps(a, ensure_ascii=False, indent=2).encode("utf-8"),
                               "application/json", f"{base}.json")
        if formato == "zip":
            carpeta = servicio.carpeta_analisis(aid)
            tmp = servicio.carpeta_datos() / "paquetes"
            tmp.mkdir(exist_ok=True)
            zip_base = tmp / base
            shutil.make_archive(str(zip_base), "zip", carpeta)
            datos = Path(f"{zip_base}.zip").read_bytes()
            Path(f"{zip_base}.zip").unlink(missing_ok=True)
            return self._bytes(datos, "application/zip", f"{base}.zip")
        return self._error(400, "Formato no válido (html, csv, json, zip)")

    # -- POST
    def do_POST(self) -> None:  # noqa: N802
        if not self._autorizado():
            return self._error(403, "Petición no autorizada")
        ruta = [p for p in urlparse(self.path).path.split("/") if p][1:]
        try:
            cuerpo = self._cuerpo()
            if ruta == ["analizar"]:
                if not (cuerpo.get("url") or "").strip():
                    return self._error(400, "Indica la URL de la web")
                return self._json({"trabajo": lanzar_analisis(cuerpo)})
            if ruta == ["demo"]:
                return self._json(iniciar_demo())
            if ruta == ["solucionar"]:
                ids = cuerpo.get("ids") or []
                ediciones = cuerpo.get("ediciones") or {}
                if cuerpo.get("destino") == "paquete":
                    z = aplicar.generar_paquete(cuerpo["analisis"], ids, ediciones, cuerpo.get("carpeta") or None)
                    return self._json({"descarga": f"/api/descargar/{z.name}", "nombre": z.name})
                if not cuerpo.get("carpeta"):
                    return self._error(400, "Indica la carpeta del sitio donde aplicar los cambios")
                return self._json(aplicar.aplicar_en_carpeta(cuerpo["analisis"], ids, ediciones, cuerpo["carpeta"]))
            if ruta == ["ia", "probar"]:
                return self._json(ia.probar_conexion())
            if len(ruta) == 2 and ruta[0] == "ia":
                rama = cuerpo.get("rama")
                if rama not in ("seo", "sem", "geo"):
                    return self._error(400, "Vertiente no válida")
                if servicio.leer_analisis(ruta[1]) is None:
                    return self._error(404, "Análisis no encontrado")
                return self._json({"trabajo": lanzar_ia(ruta[1], rama)})
            if ruta == ["deshacer"]:
                return self._json(aplicar.deshacer(cuerpo["aplicacion"]))
            if ruta == ["ajustes"]:
                almacen.guardar_ajustes(cuerpo)
                return self._json(almacen.ajustes_publicos())
            if len(ruta) >= 3 and ruta[0] == "proyecto":
                return self._proyecto(ruta[1], ruta[2:], cuerpo)
            if len(ruta) == 3 and ruta[0] == "subidas":
                aid, accion = ruta[1], ruta[2]
                if accion == "subir":
                    return self._json(subidas.subir(aid, cuerpo.get("archivos") or []))
                if accion == "asignar":
                    return self._json(subidas.asignar(aid, cuerpo.get("nombre", ""), cuerpo.get("url")))
                if accion == "aplicar":
                    return self._json(subidas.aplicar(aid, cuerpo.get("ids") or [], cuerpo.get("ediciones") or {},
                                                   desde_web=bool(cuerpo.get("desde_web"))))
                if accion == "restablecer":
                    return self._json(subidas.restablecer(aid))
                if accion == "borrar":
                    return self._json(subidas.borrar(aid))
            return self._error(404, "Ruta no encontrada")
        except (aplicar.ErrorAplicacion, ErrorImportacion, ErrorMotor, ValueError) as e:
            return self._error(400, str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(500, str(e))

    def _proyecto(self, p: str, accion: list[str], c: dict[str, Any]) -> None:
        if accion == ["perfil"]:
            externo.guardar_perfil(p, c)
        elif accion == ["cuestionario"]:
            externo.responder(p, c.get("id", ""), c.get("valor"))
        elif accion == ["importar"]:
            return self._json({"importados": externo.importar_archivo(p, c.get("nombre", "archivo"), c.get("contenido", ""))})
        elif accion == ["importacion", "borrar"]:
            externo.borrar_importacion(p, c.get("fuente", ""), c.get("tipo", ""))
        elif accion == ["monitor", "config"]:
            externo.guardar_config_monitor(p, c)
        elif accion == ["monitor", "ejecutar"]:
            return self._json({"trabajo": lanzar_monitor(p)})
        elif accion == ["resenas"]:
            externo.buscar_resenas(p, c.get("consulta", ""))
        elif accion == ["wikidata"]:
            externo.comprobar_wikidata(p)
        elif accion == ["accion"]:
            externo.actualizar_accion(p, c.get("id", ""), c)
        elif accion == ["ejemplo"]:
            externo.cargar_ejemplo(p)
        else:
            return self._error(404, "Ruta no encontrada")
        return self._json(externo.estado(p))

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._autorizado():
            return self._error(403, "Petición no autorizada")
        ruta = [p for p in urlparse(self.path).path.split("/") if p][1:]
        if len(ruta) == 2 and ruta[0] == "analisis":
            return self._json({"borrado": servicio.borrar_analisis(ruta[1])})
        return self._error(404, "Ruta no encontrada")


def _puerto_libre(preferido: int) -> int:
    for p in [preferido] + list(range(preferido + 1, preferido + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def crear_servidor(puerto: int = 8765) -> http.server.ThreadingHTTPServer:
    return http.server.ThreadingHTTPServer(("127.0.0.1", _puerto_libre(puerto)), Handler)


def iniciar(puerto: int = 8765, abrir_navegador: bool = True) -> None:
    srv = crear_servidor(puerto)
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"\n  Tríada {__version__} está funcionando en {url}")
    print(f"  Tus análisis se guardan en: {servicio.carpeta_datos()}")
    print("  Para cerrar la aplicación, cierra esta ventana o pulsa Ctrl+C.\n")
    if abrir_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Tríada cerrada.")
    finally:
        srv.server_close()


if __name__ == "__main__":
    iniciar()
