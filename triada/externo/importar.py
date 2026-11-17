"""Importación de informes CSV de Google Ads y Google Search Console.

Acepta los formatos habituales de exportación: UTF-8 o UTF-16, coma/punto y coma/tabulador,
cabeceras en español o inglés, filas de título antes de la cabecera y filas de totales.
Search Console exporta un .zip con varios CSV: también se acepta tal cual.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from datetime import datetime
from typing import Any


class ErrorImportacion(Exception):
    pass


# ------------------------------------------------------------------ lectura

def _texto(datos: bytes) -> str:
    if datos[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return datos.decode("utf-16")
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(cod)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


def _filas(texto: str) -> list[list[str]]:
    muestra = "\n".join(texto.splitlines()[:15])
    delim = max([",", ";", "\t"], key=lambda d: muestra.count(d))
    return [fila for fila in csv.reader(io.StringIO(texto), delimiter=delim) if any(c.strip() for c in fila)]


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c)).strip(' "')


ALIAS = {
    "termino": ["termino de busqueda", "search term", "terminos de busqueda", "search terms"],
    "palabra": ["palabra clave", "keyword", "palabra clave de busqueda", "search keyword", "palabras clave"],
    "qs": ["nivel de calidad", "quality score", "puntuacion de calidad"],
    "coste": ["coste", "costo", "cost", "importe"],
    "conv": ["conversiones", "conversions", "conv.", "todas las conversiones"],
    "clics": ["clics", "clicks"],
    "impr": ["impr.", "impresiones", "impressions", "impr"],
    "campana": ["campana", "campaign"],
    # Search Console
    "consulta": ["consultas principales", "top queries", "consulta", "query", "consultas"],
    "pagina": ["paginas principales", "top pages", "pagina", "page"],
    "ctr": ["ctr"],
    "posicion": ["posicion", "position", "posicion media", "average position"],
    "sitio": ["sitios web con mas enlaces", "top linking sites", "sitio", "site", "sitios que enlazan",
              "dominio", "domain"],
    "enlazantes": ["paginas enlazantes", "linking pages", "enlaces", "links", "paginas que enlazan"],
}


def _mapear(cabecera: list[str]) -> dict[str, int]:
    norm = [_norm(c) for c in cabecera]
    res: dict[str, int] = {}
    for clave, alias in ALIAS.items():
        for i, c in enumerate(norm):
            if c in alias:
                res.setdefault(clave, i)
    # coincidencia parcial, evitando columnas derivadas (coste/conv., valor de conv., tasa…)
    prohibidas = ("/", "valor", "value", "tasa", "rate", "por conv", "per conv", "%", "anterior", "previous")
    for clave, alias in ALIAS.items():
        if clave in res:
            continue
        for i, c in enumerate(norm):
            if any(c.startswith(a) for a in alias) and not any(p in c for p in prohibidas):
                res[clave] = i
                break
    return res


def _cabecera(filas: list[list[str]], requeridas: list[set[str]]) -> tuple[int, dict[str, int]] | None:
    for i, fila in enumerate(filas[:15]):
        m = _mapear(fila)
        for req in requeridas:
            if req <= m.keys():
                return i, m
    return None


def _decimal_coma(valores: list[str]) -> bool:
    """Detecta si el archivo usa coma decimal (formato español)."""
    coma = punto = 0
    for v in valores:
        v = v.strip()
        if re.search(r"\d,\d{1,2}(\D*)$", v) or re.search(r"\d\.\d{3},\d", v):
            coma += 1
        elif re.search(r"\d\.\d{1,2}(\D*)$", v) or re.search(r"\d,\d{3}\.\d", v):
            punto += 1
    return coma > punto


def _num(v: str, coma: bool) -> float | None:
    v = (v or "").strip()
    if v in ("", "--", "-", "—", "< 10", "<10"):
        return None
    v = re.sub(r"[^\d,.\-]", "", v)
    if not v or v in "-.,":
        return None
    v = v.replace(".", "").replace(",", ".") if coma else v.replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def _total(fila: list[str]) -> bool:
    primero = _norm(fila[0]) if fila else ""
    return primero.startswith(("total", "totales")) or primero == ""


# ------------------------------------------------------------------ Google Ads

def _ads_palabras(filas, i, m, coma) -> dict[str, Any]:
    datos = []
    for f in filas[i + 1:]:
        if len(f) <= max(m.values()) or _total(f):
            continue
        datos.append({
            "palabra": f[m["palabra"]].strip(),
            "campana": f[m["campana"]].strip() if "campana" in m else "",
            "qs": _num(f[m["qs"]], coma) if "qs" in m else None,
            "coste": _num(f[m["coste"]], coma) or 0.0 if "coste" in m else 0.0,
            "conv": _num(f[m["conv"]], coma) or 0.0 if "conv" in m else 0.0,
            "clics": _num(f[m["clics"]], coma) or 0.0 if "clics" in m else 0.0,
            "impr": _num(f[m["impr"]], coma) or 0.0 if "impr" in m else 0.0,
        })
    if not datos:
        raise ErrorImportacion("El informe de palabras clave no tiene filas de datos.")
    con_qs = [d for d in datos if d["qs"] is not None]
    peso = "impr" if any(d["impr"] for d in con_qs) else "coste" if any(d["coste"] for d in con_qs) else None
    if con_qs:
        if peso:
            tot = sum(d[peso] for d in con_qs) or 1
            qs_medio = sum(d["qs"] * d[peso] for d in con_qs) / tot
        else:
            qs_medio = sum(d["qs"] for d in con_qs) / len(con_qs)
    else:
        qs_medio = None
    coste = sum(d["coste"] for d in datos)
    coste_qs_bajo = sum(d["coste"] for d in con_qs if d["qs"] <= 4)
    bajas = sorted([d for d in con_qs if d["qs"] <= 4], key=lambda d: d["coste"], reverse=True)[:20]
    return {
        "tipo": "palabras_clave", "filas": len(datos), "coste": round(coste, 2),
        "conversiones": round(sum(d["conv"] for d in datos), 2),
        "clics": sum(d["clics"] for d in datos), "impresiones": sum(d["impr"] for d in datos),
        "qs_medio": round(qs_medio, 2) if qs_medio is not None else None,
        "qs_ponderado_por": peso, "con_qs": len(con_qs),
        "pct_coste_qs_bajo": round(100 * coste_qs_bajo / coste, 1) if coste else None,
        "qs_bajo": [{"palabra": d["palabra"], "campana": d["campana"], "qs": d["qs"], "coste": round(d["coste"], 2)}
                    for d in bajas],
    }


def _ads_terminos(filas, i, m, coma) -> dict[str, Any]:
    datos = []
    for f in filas[i + 1:]:
        if len(f) <= max(m.values()) or _total(f):
            continue
        datos.append({
            "termino": f[m["termino"]].strip(),
            "campana": f[m["campana"]].strip() if "campana" in m else "",
            "coste": _num(f[m["coste"]], coma) or 0.0,
            "conv": _num(f[m["conv"]], coma) or 0.0 if "conv" in m else 0.0,
            "clics": _num(f[m["clics"]], coma) or 0.0 if "clics" in m else 0.0,
        })
    if not datos:
        raise ErrorImportacion("El informe de términos de búsqueda no tiene filas de datos.")
    if "conv" not in m:
        raise ErrorImportacion("El informe de términos de búsqueda necesita la columna Conversiones.")
    coste = sum(d["coste"] for d in datos)
    sin_conv = [d for d in datos if d["conv"] == 0 and d["coste"] > 0]
    desperdicio = sum(d["coste"] for d in sin_conv)
    return {
        "tipo": "terminos", "filas": len(datos), "coste": round(coste, 2),
        "conversiones": round(sum(d["conv"] for d in datos), 2),
        "coste_sin_conversion": round(desperdicio, 2),
        "pct_desperdicio": round(100 * desperdicio / coste, 1) if coste else None,
        "negativas": [{"termino": d["termino"], "campana": d["campana"], "coste": round(d["coste"], 2),
                       "clics": d["clics"]}
                      for d in sorted(sin_conv, key=lambda d: d["coste"], reverse=True)[:30]],
    }


# ------------------------------------------------------------------ Search Console

def _sc_consultas(filas, i, m, coma) -> dict[str, Any]:
    datos = []
    for f in filas[i + 1:]:
        if len(f) <= max(m.values()):
            continue
        clics, impr = _num(f[m["clics"]], coma) or 0, _num(f[m["impr"]], coma) or 0
        pos = _num(f[m["posicion"]], coma) if "posicion" in m else None
        datos.append({"consulta": f[m["consulta"]].strip(), "clics": clics, "impr": impr, "pos": pos})
    if not datos:
        raise ErrorImportacion("El informe de consultas no tiene filas.")
    impr = sum(d["impr"] for d in datos)
    clics = sum(d["clics"] for d in datos)
    con_pos = [d for d in datos if d["pos"] is not None and d["impr"]]
    pos = sum(d["pos"] * d["impr"] for d in con_pos) / sum(d["impr"] for d in con_pos) if con_pos else None
    umbral = sorted(d["impr"] for d in datos)[int(len(datos) * 0.75)] if len(datos) >= 4 else 0
    oportunidades = [d for d in con_pos if d["pos"] <= 10 and d["impr"] >= max(umbral, 50)
                     and d["clics"] / d["impr"] < 0.02]
    return {
        "tipo": "consultas", "filas": len(datos), "clics": clics, "impresiones": impr,
        "ctr": round(100 * clics / impr, 2) if impr else None,
        "posicion_media": round(pos, 1) if pos is not None else None,
        "oportunidades": [{"consulta": d["consulta"], "impresiones": d["impr"], "clics": d["clics"],
                           "posicion": d["pos"]} for d in sorted(oportunidades, key=lambda d: -d["impr"])[:15]],
    }


def _sc_enlaces(filas, i, m, coma) -> dict[str, Any]:
    sitios = []
    for f in filas[i + 1:]:
        if len(f) <= m["sitio"] or not f[m["sitio"]].strip():
            continue
        n = _num(f[m["enlazantes"]], coma) if "enlazantes" in m and len(f) > m["enlazantes"] else None
        sitios.append({"sitio": f[m["sitio"]].strip(), "enlaces": n})
    return {"tipo": "enlaces", "dominios": len(sitios), "top": sitios[:15]}


# ------------------------------------------------------------------ punto de entrada

def _detectar(filas: list[list[str]]) -> tuple[str, dict[str, Any]]:
    coma = _decimal_coma([c for f in filas[:200] for c in f])
    if (h := _cabecera(filas, [{"termino", "coste"}])):
        return "ads", _ads_terminos(filas, *h, coma)
    if (h := _cabecera(filas, [{"palabra", "qs"}, {"palabra", "coste"}])):
        return "ads", _ads_palabras(filas, *h, coma)
    if (h := _cabecera(filas, [{"consulta", "clics", "impr"}])):
        return "search_console", _sc_consultas(filas, *h, coma)
    if (h := _cabecera(filas, [{"sitio"}])):
        return "search_console", _sc_enlaces(filas, *h, coma)
    raise ErrorImportacion(
        "No reconozco el formato. Sube el informe de Palabras clave o de Términos de búsqueda de Google Ads, "
        "o la exportación de Rendimiento (Consultas) o de Enlaces (Sitios web con más enlaces) de Search Console.")


def importar(nombre: str, datos: bytes) -> list[tuple[str, dict[str, Any]]]:
    """Devuelve una lista de (fuente, resumen). Un .zip de Search Console puede dar varios."""
    resultados = []
    if datos[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            for n in z.namelist():
                if not n.lower().endswith(".csv"):
                    continue
                try:
                    resultados.append(_detectar(_filas(_texto(z.read(n)))))
                except ErrorImportacion:
                    continue
        if not resultados:
            raise ErrorImportacion("El .zip no contiene ningún informe reconocible.")
    else:
        resultados.append(_detectar(_filas(_texto(datos))))
    fecha = datetime.now().isoformat(timespec="seconds")
    for _, r in resultados:
        r["archivo"] = nombre
        r["fecha"] = fecha
    return resultados
