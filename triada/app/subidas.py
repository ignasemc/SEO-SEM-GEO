"""Solucionar sobre archivos HTML subidos.

El usuario sube páginas sueltas (.html/.htm) o un .zip con la web. Cada archivo se asigna a una página
del análisis (automáticamente por canonical, ruta, título o H1, o a mano). Las correcciones se aplican
sobre una copia: los originales se conservan para poder restablecerlos, y el HTML modificado se descarga
como archivo suelto o en un .zip.
"""

from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor
import json
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import Config

from .aplicar import ErrorAplicacion, _validar, aplicar_en_html, seleccionar
from .servicio import carpeta_datos, leer_analisis

EXT_HTML = (".html", ".htm")
MAX_TOTAL = 60 * 1024 * 1024


def _base(aid: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z\-]+", aid or ""):
        raise ErrorAplicacion("Análisis no válido")
    return carpeta_datos() / "subidas" / aid


def _leer_estado(aid: str) -> dict[str, Any]:
    f = _base(aid) / "estado.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"archivos": [], "aplicados": {}, "historial": []}


def _guardar_estado(aid: str, est: dict[str, Any]) -> None:
    b = _base(aid)
    b.mkdir(parents=True, exist_ok=True)
    (b / "estado.json").write_text(json.dumps(est, ensure_ascii=False, indent=2), encoding="utf-8")


def _nombre_seguro(nombre: str) -> str | None:
    """Ruta relativa limpia (sin '..' ni rutas absolutas)."""
    partes = [p for p in PurePosixPath(nombre.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not partes or any(p == ".." for p in partes):
        return None
    return "/".join(partes)


def _norm(t: str | None) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def _path(u: str) -> str:
    p = urlparse(u).path or "/"
    p = re.sub(r"/index\.html?$", "/", p)
    return p.rstrip("/") or "/"


def _ruta_desde_nombre(nombre: str) -> str:
    """'blog/post/index.html' -> '/blog/post' · 'contacto.html' -> '/contacto' · 'index.html' -> '/'."""
    p = "/" + nombre
    p = re.sub(r"(^|/)index\.html?$", r"\1", p)
    p = re.sub(r"\.html?$", "", p)
    return p.rstrip("/") or "/"


def emparejar(html: str, nombre: str, paginas: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Busca a qué página del análisis corresponde un archivo. Devuelve (url, método)."""
    por_ruta = {_path(p["url"]): p["url"] for p in paginas}
    soup = BeautifulSoup(html, "html.parser")
    can = soup.find("link", rel=lambda v: v and "canonical" in v)
    if can and can.get("href"):
        destino = _path(urljoin(paginas[0]["url"] if paginas else "http://x/", can["href"]))
        if destino in por_ruta:
            return por_ruta[destino], "canonical"
    ruta = _ruta_desde_nombre(nombre)
    if ruta in por_ruta:
        return por_ruta[ruta], "ruta del archivo"
    # solo el nombre del archivo (subidas sueltas sin carpetas): 'reformas.html' -> '/servicios/reformas'
    hoja = ruta.rsplit("/", 1)[-1]
    if hoja:
        candidatos = [u for r, u in por_ruta.items() if r.rsplit("/", 1)[-1] == hoja]
        if len(candidatos) == 1:
            return candidatos[0], "nombre del archivo"
    titulo = _norm(soup.title.string if soup.title and soup.title.string else "")
    if titulo:
        c = [p["url"] for p in paginas if _norm(p.get("title")) == titulo]
        if len(c) == 1:
            return c[0], "título"
    h1 = soup.find("h1")
    if h1:
        t = _norm(h1.get_text(" "))
        c = [p["url"] for p in paginas if _norm(p.get("h1")) == t]
        if len(c) == 1:
            return c[0], "H1"
    return None, ""


def _decodificar(texto_b64: str) -> bytes:
    return base64.b64decode(texto_b64.split(",", 1)[-1])


def _texto(datos: bytes) -> str:
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(cod)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


def subir(aid: str, archivos: list[dict[str, str]]) -> dict[str, Any]:
    analisis = leer_analisis(aid)
    if analisis is None:
        raise ErrorAplicacion("Análisis no encontrado")
    base = _base(aid)
    (base / "original").mkdir(parents=True, exist_ok=True)
    (base / "modificado").mkdir(parents=True, exist_ok=True)
    est = _leer_estado(aid)
    existentes = {a["nombre"]: a for a in est["archivos"]}
    nuevos: list[tuple[str, bytes]] = []
    total = 0
    for a in archivos:
        datos = _decodificar(a.get("contenido", ""))
        total += len(datos)
        if total > MAX_TOTAL:
            raise ErrorAplicacion("Los archivos superan el máximo de 60 MB.")
        nombre = a.get("nombre", "")
        if nombre.lower().endswith(".zip") or datos[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(datos)) as z:
                # se descartan rutas peligrosas (../) y carpetas de sistema
                internos = [(n, seg) for n in z.namelist()
                            if n.lower().endswith(EXT_HTML) and not n.startswith("__MACOSX")
                            and (seg := _nombre_seguro(n))]
                # si todo el zip cuelga de una carpeta raíz común, se quita
                raiz = ""
                if internos and all("/" in seg for _, seg in internos):
                    primeras = {seg.split("/", 1)[0] for _, seg in internos}
                    if len(primeras) == 1:
                        raiz = primeras.pop() + "/"
                for n, seg in internos:
                    nuevos.append((seg[len(raiz):], z.read(n)))
        elif nombre.lower().endswith(EXT_HTML):
            seguro = _nombre_seguro(PurePosixPath(nombre).name)
            if seguro:
                nuevos.append((seguro, datos))
        else:
            raise ErrorAplicacion(f"{nombre}: solo se admiten archivos .html, .htm o .zip")
    if not nuevos:
        raise ErrorAplicacion("No se encontró ningún archivo HTML.")

    for nombre, datos in nuevos:
        for carpeta in ("original", "modificado"):
            destino = base / carpeta / nombre
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(datos)
        url, metodo = emparejar(_texto(datos), nombre, analisis["paginas"])
        existentes[nombre] = {"nombre": nombre, "url": url, "metodo": metodo or None, "bytes": len(datos)}
        est["aplicados"].pop(nombre, None)  # un archivo re-subido empieza de cero
    est["archivos"] = sorted(existentes.values(), key=lambda x: x["nombre"])
    _guardar_estado(aid, est)
    return estado(aid)


def asignar(aid: str, nombre: str, url: str | None) -> dict[str, Any]:
    analisis = leer_analisis(aid)
    est = _leer_estado(aid)
    a = next((x for x in est["archivos"] if x["nombre"] == nombre), None)
    if a is None:
        raise ErrorAplicacion("Archivo no encontrado")
    if url and url not in {p["url"] for p in analisis["paginas"]}:
        raise ErrorAplicacion("Página no válida")
    a["url"], a["metodo"] = (url or None), ("manual" if url else None)
    _guardar_estado(aid, est)
    return estado(aid)


def _nombre_desde_url(url: str) -> str | None:
    """'https://web.com/producto/silla/' -> 'producto/silla/index.html' · 'https://web.com/' -> 'index.html'."""
    ruta = unquote(urlparse(url).path or "/")
    if not ruta.lower().endswith(EXT_HTML):
        ruta = ruta.rstrip("/") + "/index.html"
    return _nombre_seguro(ruta)


def traer_de_la_web(aid: str, urls: list[str] | None = None,
                    cliente: httpx.Client | None = None) -> dict[str, Any]:
    """Descarga de la web el HTML de las páginas del análisis que no tienen archivo subido.

    Así se pueden aplicar todas las correcciones y exportar el HTML modificado aunque el usuario no
    tenga los archivos (p. ej. una web en WordPress). Se guardan como si se hubieran subido: el original
    se conserva y se puede restablecer.
    """
    analisis = leer_analisis(aid)
    if analisis is None:
        raise ErrorAplicacion("Análisis no encontrado")
    est = _leer_estado(aid)
    asignadas = {a["url"] for a in est["archivos"] if a["url"]}
    pedidas = set(urls) if urls is not None else None
    objetivo = [p["url"] for p in analisis["paginas"]
                if p.get("status") == 200 and p["url"] not in asignadas and (pedidas is None or p["url"] in pedidas)]
    propio = cliente is None
    if propio:
        cliente = httpx.Client(headers={"User-Agent": Config.user_agent, "Accept-Language": "es,en;q=0.8"},
                               follow_redirects=True, timeout=20)

    def bajar(url: str) -> tuple[str, bytes | None, str]:
        try:
            r = cliente.get(url)
        except httpx.HTTPError as e:
            return url, None, f"No se pudo descargar ({type(e).__name__})"
        if r.status_code != 200:
            return url, None, f"La web respondió {r.status_code}"
        if "html" not in r.headers.get("content-type", "html").lower():
            return url, None, "No es una página HTML"
        if len(r.content) > MAX_TOTAL:
            return url, None, "Página demasiado grande"
        return url, r.content, ""

    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            resultados = list(ex.map(bajar, objetivo))
    finally:
        if propio:
            cliente.close()

    base = _base(aid)
    existentes = {a["nombre"]: a for a in est["archivos"]}
    descargadas, fallidas = 0, []
    for url, datos, error in resultados:
        nombre = _nombre_desde_url(url)
        if datos is None or not nombre:
            fallidas.append({"url": url, "motivo": error or "Ruta no válida"})
            continue
        if nombre in existentes:  # ya hay un archivo con ese nombre asignado a otra página
            raiz, ext = nombre.rsplit(".", 1)
            n = 2
            while f"{raiz}-{n}.{ext}" in existentes:
                n += 1
            nombre = f"{raiz}-{n}.{ext}"
        for carpeta in ("original", "modificado"):
            destino = base / carpeta / nombre
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(datos)
        existentes[nombre] = {"nombre": nombre, "url": url, "metodo": "descargada de la web", "bytes": len(datos)}
        est["aplicados"].pop(nombre, None)
        descargadas += 1
    est["archivos"] = sorted(existentes.values(), key=lambda x: x["nombre"])
    _guardar_estado(aid, est)
    return estado(aid) | {"descargadas": descargadas, "fallidas": fallidas}


def aplicar(aid: str, ids: list[str], ediciones: dict[str, str], desde_web: bool = False,
            cliente: httpx.Client | None = None) -> dict[str, Any]:
    """Aplica las correcciones a los archivos subidos.

    Con desde_web=True, antes se descarga de la web el HTML de las páginas afectadas que no tienen archivo.
    """
    analisis = leer_analisis(aid)
    if analisis is None:
        raise ErrorAplicacion("Análisis no encontrado")
    traidas: dict[str, Any] = {"descargadas": 0, "fallidas": []}
    if desde_web:
        urls = [f["url"] for f in seleccionar(analisis, ids, ediciones)
                if f.get("url") and f["tipo"] != "archivo" and not _validar(f)]
        traidas = traer_de_la_web(aid, urls, cliente)
    est = _leer_estado(aid)
    if not est["archivos"] and not desde_web:
        raise ErrorAplicacion("Primero sube los archivos HTML.")
    base = _base(aid)
    por_url: dict[str, list[dict]] = {}
    for a in est["archivos"]:
        if a["url"]:
            por_url.setdefault(a["url"], []).append(a)
    aplicados: list[str] = []
    omitidos: list[dict[str, str]] = []
    pendientes: dict[str, list[dict]] = {}
    for f in seleccionar(analisis, ids, ediciones):
        motivo = _validar(f)
        if motivo:
            omitidos.append({"id": f["id"], "motivo": motivo})
            continue
        if f["tipo"] == "archivo":
            nombre = PurePosixPath(urlparse(f["url"] or "").path).name
            if nombre in ("robots.txt", "sitemap.xml"):
                (base / "modificado" / nombre).write_text(f["valor_propuesto"], encoding="utf-8")
                est["aplicados"].setdefault(nombre, [])
                if f["id"] not in est["aplicados"][nombre]:
                    est["aplicados"][nombre].append(f["id"])
                aplicados.append(f["id"])
            else:
                omitidos.append({"id": f["id"], "motivo": "Tipo de archivo no soportado"})
            continue
        # una corrección agrupada (p. ej. el alt de una imagen que sale en 30 páginas) se aplica en todas
        destinos = [a for u in [f.get("url") or ""] + list(f.get("paginas") or []) for a in por_url.get(u, [])]
        if not destinos:
            omitidos.append({"id": f["id"], "motivo": "Ningún archivo subido corresponde a esta página"})
            continue
        for a in destinos:
            if f["id"] in est["aplicados"].get(a["nombre"], []):
                continue  # ya aplicada a este archivo: no se duplica (p. ej. un H1)
            pendientes.setdefault(a["nombre"], []).append(f)
    for nombre, lista in pendientes.items():
        ruta = base / "modificado" / nombre
        try:
            url_archivo = next((a["url"] for a in est["archivos"] if a["nombre"] == nombre), None) or lista[0]["url"]
            html, hechos = aplicar_en_html(_texto(ruta.read_bytes()), url_archivo, lista)
        except ErrorAplicacion as e:
            omitidos.extend({"id": f["id"], "motivo": str(e)} for f in lista)
            continue
        ruta.write_text(html, encoding="utf-8")
        est["aplicados"].setdefault(nombre, []).extend(hechos)
        aplicados.extend(hechos)
        omitidos.extend({"id": f["id"], "motivo": "No se encontró el elemento a modificar"}
                        for f in lista if f["id"] not in hechos)
    aplicados = list(dict.fromkeys(aplicados))
    est["historial"].append({"fecha": datetime.now().isoformat(timespec="seconds"), "aplicados": aplicados})
    _guardar_estado(aid, est)
    return {"aplicados": aplicados, "omitidos": omitidos, "subidas": estado(aid),
            "descargadas": traidas["descargadas"], "fallidas": traidas["fallidas"]}


def restablecer(aid: str) -> dict[str, Any]:
    base = _base(aid)
    if (base / "modificado").exists():
        shutil.rmtree(base / "modificado")
    if (base / "original").exists():
        shutil.copytree(base / "original", base / "modificado")
    est = _leer_estado(aid)
    est["aplicados"] = {}
    est["historial"].append({"fecha": datetime.now().isoformat(timespec="seconds"), "restablecido": True})
    _guardar_estado(aid, est)
    return estado(aid)


def borrar(aid: str) -> dict[str, Any]:
    base = _base(aid)
    if base.exists():
        shutil.rmtree(base)
    return estado(aid)


def estado(aid: str) -> dict[str, Any]:
    est = _leer_estado(aid)
    extra = [n for n in est["aplicados"] if n in ("robots.txt", "sitemap.xml")]
    ids = sorted({i for lista in est["aplicados"].values() for i in lista})
    return {
        "archivos": [a | {"cambios": len(est["aplicados"].get(a["nombre"], []))} for a in est["archivos"]],
        "generados": extra, "aplicados": ids,
        "modificados": sum(1 for v in est["aplicados"].values() if v),
    }


def archivo(aid: str, nombre: str) -> tuple[bytes, str]:
    seguro = _nombre_seguro(nombre)
    est = _leer_estado(aid)
    validos = {a["nombre"] for a in est["archivos"]} | {"robots.txt", "sitemap.xml"}
    ruta = _base(aid) / "modificado" / (seguro or "")
    if not seguro or seguro not in validos or not ruta.is_file():
        raise ErrorAplicacion("Archivo no encontrado")
    return ruta.read_bytes(), PurePosixPath(seguro).name


RESERVADOS_WINDOWS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def nombre_de_archivo(titulo: str, max_largo: int = 90) -> str:
    """Convierte el título de una página en un nombre de archivo válido en Windows, Mac y Linux."""
    t = re.sub(r"\s*[|/\\]\s*", " - ", titulo or "")  # «Marca | Página» → «Marca - Página»
    t = re.sub(r'[<>:"?*\x00-\x1f]', " ", t)
    t = re.sub(r"\s+", " ", t).strip().strip(".")
    if len(t) > max_largo:
        t = t[:max_largo].rsplit(" ", 1)[0].rstrip(" .-–|")
    if not t or t.upper() in RESERVADOS_WINDOWS:
        t = (t + " pagina").strip() if t else "pagina"
    return t


def _titulo_html(datos: bytes) -> str:
    soup = BeautifulSoup(_texto(datos), "html.parser")
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(" ", strip=True)
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def zip_por_titulo(aid: str) -> bytes:
    """HTML modificado con un archivo por página, llamado como la página («Título de la página.html»).

    Así el cliente reconoce cada página y puede pasarle el archivo a una IA para que cambie su web. Se usa el título
    que la página tiene ahora en la web (el que el cliente ve en su gestor de contenidos). Solo van las páginas con
    cambios, más LEEME.txt con la relación archivo → dirección de la página → cambios.
    """
    est = _leer_estado(aid)
    if not est["archivos"]:
        raise ErrorAplicacion("No hay archivos subidos.")
    base = _base(aid)
    usados: set[str] = set()
    filas = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in sorted(est["archivos"], key=lambda x: x["url"] or x["nombre"]):
            cambios = est["aplicados"].get(a["nombre"]) or []
            if not cambios:
                continue
            original = base / "original" / a["nombre"]
            modificado = base / "modificado" / a["nombre"]
            titulo = _titulo_html(original.read_bytes()) if original.exists() else ""
            ruta = urlparse(a["url"]).path if a["url"] else a["nombre"]
            nombre = nombre_de_archivo(titulo or ruta.strip("/").replace("/", " - ") or "Inicio")
            if nombre.lower() in usados:  # dos páginas con el mismo título: se añade su dirección
                ruta_corta = ruta.strip("/").rsplit("/", 1)[-1] or "inicio"
                nombre = nombre_de_archivo(f"{nombre} ({ruta_corta})")
                n = 2
                while nombre.lower() in usados:
                    nombre = nombre_de_archivo(f"{nombre} {n}")
                    n += 1
            usados.add(nombre.lower())
            z.write(modificado, f"{nombre}.html")
            filas.append((f"{nombre}.html", a["url"] or a["nombre"], _titulo_html(modificado.read_bytes()), cambios))
        for extra in ("robots.txt", "sitemap.xml"):
            if extra in est["aplicados"] and (base / "modificado" / extra).exists():
                z.write(base / "modificado" / extra, extra)
                filas.append((extra, "(archivo de la raíz de la web)", "", est["aplicados"][extra]))
        lineas = [
            "HTML MODIFICADO POR TRÍADA",
            "==========================",
            "",
            "Cada archivo se llama como la página de tu web a la que corresponde (su título actual).",
            "",
            "Cómo pasar los cambios a tu web con una IA (ChatGPT, Claude…):",
            "  1. Abre el archivo de la página que quieras actualizar.",
            "  2. Pídele a la IA: «Este es el HTML corregido de mi página <dirección>. Dime qué tengo que cambiar",
            "     en mi web (título, descripción, encabezados, datos estructurados…) y cómo hacerlo en <WordPress,",
            "     Shopify…>», y pega el contenido del archivo.",
            "  3. Revisa los cambios antes de publicar.",
            "",
            "Archivo → página → título nuevo → cambios aplicados (ID de Tríada)",
            "",
        ]
        for archivo, url, titulo_nuevo, cambios in filas:
            lineas += [archivo, f"   Página: {url}"] + ([f"   Título nuevo: {titulo_nuevo}"] if titulo_nuevo else []) + \
                      [f"   Cambios: {', '.join(cambios)}", ""]
        if not filas:
            lineas.append("Todavía no se ha aplicado ningún cambio.")
        z.writestr("LEEME.txt", "\r\n".join(lineas) + "\r\n")
    return buf.getvalue()


def zip_modificados(aid: str) -> bytes:
    est = _leer_estado(aid)
    base = _base(aid) / "modificado"
    if not est["archivos"]:
        raise ErrorAplicacion("No hay archivos subidos.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in est["archivos"]:
            z.write(base / a["nombre"], a["nombre"])
        for extra in ("robots.txt", "sitemap.xml"):
            if extra in est["aplicados"] and (base / extra).exists():
                z.write(base / extra, extra)
        cambios = "\n".join(f"{n}: {', '.join(v)}" for n, v in est["aplicados"].items() if v) or "Sin cambios aplicados."
        z.writestr("CAMBIOS.txt", "Correcciones aplicadas por Tríada (ID por archivo)\n\n" + cambios + "\n")
    return buf.getvalue()
