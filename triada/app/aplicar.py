"""Aplicación de correcciones.

Dos destinos:
- "carpeta": modifica directamente los archivos de un sitio estático (o una copia exportada del sitio),
  guardando antes una copia de seguridad de cada archivo para poder deshacer.
- "paquete": genera un .zip con los archivos corregidos, el CSV de cambios e instrucciones por CMS.

Los conectores con CMS (WordPress, Shopify…) se añadirán con la misma interfaz.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..fixes import REVISAR
from ..imagenes import clave_imagen, src_real
from ..report import escribir_csv
from .servicio import carpeta_analisis, carpeta_datos, leer_analisis, nuevo_id


class ErrorAplicacion(Exception):
    pass


# ---------------------------------------------------------------- utilidades

def ruta_archivo(raiz: Path, url: str) -> Path | None:
    """Traduce una URL del sitio al archivo HTML correspondiente dentro de `raiz`."""
    path = urlparse(url).path or "/"
    rel = path.lstrip("/")
    candidatos = []
    if path.endswith("/"):
        candidatos.append(raiz / rel / "index.html")
    else:
        candidatos += [raiz / rel, raiz / f"{rel}.html", raiz / rel / "index.html"]
    raiz_res = raiz.resolve()
    for c in candidatos:
        try:
            c_res = c.resolve()
        except OSError:
            continue
        if c_res.is_file() and raiz_res in c_res.parents:
            return c_res
    return None


def _atributo(html: str, attr: str) -> str | None:
    m = re.search(rf'{attr}\s*=\s*"([^"]*)"', html)
    return m.group(1) if m else None


def _tipo_jsonld(texto: str) -> str | None:
    try:
        d = json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return None
    t = d.get("@type") if isinstance(d, dict) else None
    return t if isinstance(t, str) else None


def _cabeza(soup: BeautifulSoup):
    if soup.head is None:
        head = soup.new_tag("head")
        (soup.html or soup).insert(0, head)
    return soup.head


def _meta(soup: BeautifulSoup, nombre: str, contenido: str) -> None:
    tag = soup.find("meta", attrs={"name": re.compile(f"^{nombre}$", re.I)})
    if tag is None:
        tag = soup.new_tag("meta")
        tag["name"] = nombre
        _cabeza(soup).append(tag)
    tag["content"] = contenido


# ---------------------------------------------------------------- cambios en HTML

def aplicar_en_html(html: str, url: str, fixes: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Aplica una lista de correcciones sobre el HTML de una página. Devuelve (html, ids_aplicados)."""
    soup = BeautifulSoup(html, "html.parser")
    hechos: list[str] = []
    for f in fixes:
        tipo, valor = f["tipo"], f["valor_propuesto"]
        if tipo == "titulo":
            if soup.title is None:
                t = soup.new_tag("title")
                _cabeza(soup).insert(0, t)
            soup.title.string = valor
        elif tipo == "meta_description":
            _meta(soup, "description", valor)
        elif tipo == "meta_robots":
            _meta(soup, "robots", _atributo(valor, "content") or valor)
        elif tipo == "canonical":
            href = _atributo(valor, "href") or url
            link = soup.find("link", rel=lambda v: v and "canonical" in v)
            if link is None:
                link = soup.new_tag("link", rel="canonical")
                _cabeza(soup).append(link)
            link["href"] = href
        elif tipo == "lang":
            if soup.html is not None:
                soup.html["lang"] = _atributo(valor, "lang") or "es"
            else:
                continue
        elif tipo == "h1":
            h1 = soup.new_tag("h1")
            h1.string = valor
            destino = soup.find("main") or soup.body
            if destino is None:
                continue
            destino.insert(0, h1)
        elif tipo.startswith("alt:"):
            # la misma imagen aunque use carga diferida, otro tamaño o su copia en <noscript>
            objetivo = clave_imagen(tipo[4:])
            ok = False
            for img in soup.find_all("img"):
                src = src_real(img, url)
                if src and clave_imagen(src) == objetivo and not (img.get("alt") or "").strip():
                    img["alt"] = valor
                    ok = True
            if not ok:
                continue
        elif tipo in ("jsonld", "jsonld_breadcrumb"):
            nuevo_tipo = _tipo_jsonld(valor)
            if nuevo_tipo is None:
                raise ErrorAplicacion(f"{f['id']}: el JSON-LD no es válido")
            existente = None
            for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
                if _tipo_jsonld(s.string or "") == nuevo_tipo:
                    existente = s
                    break
            if existente is None:
                existente = soup.new_tag("script", type="application/ld+json")
                _cabeza(soup).append(existente)
            existente.string = "\n" + valor + "\n"
        elif tipo == "snippet_head":
            if "consent" in valor and "'consent', 'default'" in str(soup):
                hechos.append(f["id"])
                continue
            fragmento = BeautifulSoup(valor, "html.parser")
            cabeza = _cabeza(soup)
            for nodo in reversed(list(fragmento.contents)):
                cabeza.insert(0, nodo)
        else:
            continue
        hechos.append(f["id"])
    return str(soup), hechos


# ---------------------------------------------------------------- aplicar / deshacer

def _validar(f: dict[str, Any]) -> str | None:
    if REVISAR in (f.get("valor_propuesto") or ""):
        return "Contiene datos «REVISAR» sin completar. Edítalos antes de aplicar."
    if not (f.get("valor_propuesto") or "").strip():
        return "El valor propuesto está vacío."
    # un JSON-LD mal escrito se omite solo él, sin bloquear el resto de cambios de la página
    if f.get("tipo") in ("jsonld", "jsonld_breadcrumb") and _tipo_jsonld(f["valor_propuesto"]) is None:
        return "El JSON-LD no es válido. Revisa comas, comillas y llaves."
    return None


def seleccionar(analisis: dict[str, Any], ids: list[str], ediciones: dict[str, str]) -> list[dict[str, Any]]:
    por_id = {f["id"]: dict(f) for f in analisis["fixes"]}
    sel = []
    for i in ids:
        if i in por_id:
            f = por_id[i]
            if i in ediciones and ediciones[i] is not None:
                f["valor_propuesto"] = ediciones[i]
            sel.append(f)
    return sel


def aplicar_en_carpeta(aid: str, ids: list[str], ediciones: dict[str, str], carpeta: str) -> dict[str, Any]:
    analisis = leer_analisis(aid)
    if analisis is None:
        raise ErrorAplicacion("Análisis no encontrado")
    raiz = Path(carpeta).expanduser()
    if not raiz.is_dir():
        raise ErrorAplicacion(f"La carpeta no existe: {raiz}")

    app_id = nuevo_id()
    copia = carpeta_datos() / "aplicaciones" / app_id / "copia"
    registro: list[dict[str, Any]] = []   # archivos tocados
    aplicados: list[str] = []
    omitidos: list[dict[str, str]] = []

    def respaldar(destino: Path) -> None:
        rel = destino.resolve().relative_to(raiz.resolve())
        if any(r["ruta"] == str(rel) for r in registro):
            return
        existia = destino.exists()
        if existia:
            (copia / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destino, copia / rel)
        registro.append({"ruta": str(rel), "existia": existia})

    sel = seleccionar(analisis, ids, ediciones)
    por_archivo: dict[Path, list[dict[str, Any]]] = {}
    url_de: dict[Path, str] = {}
    for f in sel:
        motivo = _validar(f)
        if motivo:
            omitidos.append({"id": f["id"], "motivo": motivo})
            continue
        if f["tipo"] == "archivo":
            nombre = Path(urlparse(f["url"] or "").path).name
            if nombre not in ("robots.txt", "sitemap.xml"):
                omitidos.append({"id": f["id"], "motivo": "Tipo de archivo no soportado"})
                continue
            destino = raiz / nombre
            respaldar(destino)
            destino.write_text(f["valor_propuesto"], encoding="utf-8")
            aplicados.append(f["id"])
            continue
        if not f.get("url"):
            omitidos.append({"id": f["id"], "motivo": "Sin página asociada"})
            continue
        encontrados = [(a, u) for u in [f["url"]] + list(f.get("paginas") or []) if (a := ruta_archivo(raiz, u))]
        if not encontrados:
            omitidos.append({"id": f["id"], "motivo": "No se encontró el archivo HTML de esta página en la carpeta"})
            continue
        for archivo, u in encontrados:
            por_archivo.setdefault(archivo, []).append(f)
            url_de[archivo] = u

    for archivo, lista in por_archivo.items():
        respaldar(archivo)
        try:
            html, hechos = aplicar_en_html(archivo.read_text(encoding="utf-8"), url_de[archivo], lista)
        except ErrorAplicacion as e:
            omitidos.extend({"id": f["id"], "motivo": str(e)} for f in lista)
            continue
        archivo.write_text(html, encoding="utf-8")
        aplicados.extend(hechos)
        omitidos.extend({"id": f["id"], "motivo": "No se encontró el elemento a modificar"}
                        for f in lista if f["id"] not in hechos)

    info = {
        "id": app_id, "analisis": aid, "fecha": datetime.now().isoformat(timespec="seconds"),
        "destino": "carpeta", "carpeta": str(raiz), "aplicados": aplicados, "omitidos": omitidos,
        "archivos": registro, "deshecha": False,
    }
    (carpeta_datos() / "aplicaciones" / app_id).mkdir(parents=True, exist_ok=True)
    (carpeta_datos() / "aplicaciones" / app_id / "aplicacion.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def deshacer(app_id: str) -> dict[str, Any]:
    base = carpeta_datos() / "aplicaciones" / app_id
    ruta = base / "aplicacion.json"
    if "/" in app_id or ".." in app_id or not ruta.exists():
        raise ErrorAplicacion("Aplicación no encontrada")
    info = json.loads(ruta.read_text(encoding="utf-8"))
    if info["deshecha"]:
        raise ErrorAplicacion("Esta aplicación ya se deshizo")
    raiz = Path(info["carpeta"])
    for r in info["archivos"]:
        destino = raiz / r["ruta"]
        if r["existia"]:
            shutil.copy2(base / "copia" / r["ruta"], destino)
        elif destino.exists():
            destino.unlink()
    info["deshecha"] = True
    info["fecha_deshecha"] = datetime.now().isoformat(timespec="seconds")
    ruta.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def listar_aplicaciones(aid: str | None = None) -> list[dict[str, Any]]:
    res = []
    for d in sorted((carpeta_datos() / "aplicaciones").iterdir(), reverse=True):
        f = d / "aplicacion.json"
        if f.exists():
            info = json.loads(f.read_text(encoding="utf-8"))
            if aid is None or info["analisis"] == aid:
                res.append(info)
    return res


# ---------------------------------------------------------------- paquete descargable

INSTRUCCIONES = """CAMBIOS DE TRÍADA · {host} · {fecha}
================================================

Contenido del paquete
- correcciones.csv   Lista de cambios: página, valor actual y valor propuesto (ábrelo con Excel).
- archivos/          Archivos completos listos para subir (robots.txt, sitemap.xml, JSON-LD, fragmentos).
- paginas/           (si se indicó carpeta del sitio) Copias de las páginas HTML ya corregidas.

Dónde va cada cambio según tu plataforma
- WordPress con Yoast o Rank Math: título y meta descripción en la caja SEO de cada página.
  robots.txt y sitemap: los gestiona el plugin SEO; copia las reglas propuestas en su editor de robots.txt.
  JSON-LD: el plugin ya genera Organization; añade lo que falte en sus ajustes de "Schema".
- Shopify: título y descripción en "Vista previa del motor de búsqueda" de cada página/producto.
  robots.txt: Tienda online > Temas > Editar código > robots.txt.liquid.
- Web estática o código propio: sustituye los archivos o aplica los cambios en las plantillas.
- Consent Mode v2: pega el fragmento al principio del <head>, antes de gtag.js / GTM, y conéctalo a tu banner de cookies.

Antes de publicar, revisa los valores marcados como «REVISAR».
"""


def generar_paquete(aid: str, ids: list[str], ediciones: dict[str, str], carpeta: str | None = None) -> Path:
    analisis = leer_analisis(aid)
    if analisis is None:
        raise ErrorAplicacion("Análisis no encontrado")
    sel = seleccionar(analisis, ids, ediciones) if ids else list(analisis["fixes"])
    salida = carpeta_datos() / "paquetes"
    salida.mkdir(exist_ok=True)
    zip_ruta = salida / f"cambios-{analisis['host'].replace(':', '_')}-{nuevo_id()}.zip"
    tmp_csv = salida / f"{zip_ruta.stem}.csv"
    escribir_csv(tmp_csv, sel)
    with zipfile.ZipFile(zip_ruta, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEEME.txt", INSTRUCCIONES.format(host=analisis["host"], fecha=analisis["fecha"][:16]))
        z.write(tmp_csv, "correcciones.csv")
        for f in sel:
            if f["tipo"] == "archivo":
                z.writestr(f"archivos/{Path(urlparse(f['url']).path).name}", f["valor_propuesto"])
            elif f["tipo"] in ("jsonld", "jsonld_breadcrumb"):
                z.writestr(f"archivos/jsonld/{f['id']}-{f['tipo']}.json", f["valor_propuesto"])
        snippets = carpeta_analisis(aid) / "archivos" / "snippets"
        if snippets.exists() and any(f["tipo"] == "snippet_head" for f in sel):
            for s in snippets.iterdir():
                z.write(s, f"archivos/snippets/{s.name}")
        if carpeta and Path(carpeta).is_dir():
            raiz = Path(carpeta)
            por_archivo: dict[Path, list[dict[str, Any]]] = {}
            for f in sel:
                if f.get("url") and f["tipo"] != "archivo" and not _validar(f):
                    a = ruta_archivo(raiz, f["url"])
                    if a:
                        por_archivo.setdefault(a, []).append(f)
            for a, lista in por_archivo.items():
                html, _ = aplicar_en_html(a.read_text(encoding="utf-8"), lista[0]["url"], lista)
                z.writestr(f"paginas/{a.relative_to(raiz.resolve())}", html)
    tmp_csv.unlink(missing_ok=True)
    return zip_ruta


def zip_modificados_carpeta(aid: str) -> bytes:
    """Versión actual de los archivos que Tríada ha cambiado en la carpeta del sitio (sin deshacer)."""
    import io

    buf = io.BytesIO()
    tocados: dict[str, Path] = {}
    for info in listar_aplicaciones(aid):
        if info["deshecha"]:
            continue
        raiz = Path(info["carpeta"])
        for r in info["archivos"]:
            ruta = raiz / r["ruta"]
            if ruta.is_file():
                tocados[r["ruta"]] = ruta
    if not tocados:
        raise ErrorAplicacion("Todavía no se ha aplicado ningún cambio en la carpeta del sitio.")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, ruta in sorted(tocados.items()):
            z.write(ruta, rel)
    return buf.getvalue()
