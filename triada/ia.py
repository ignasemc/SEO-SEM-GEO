"""Optimización con IA (Claude): palabras clave recomendadas y valores para los «REVISAR».

Para cada página de la vertiente elegida:
1. Se descarga su HTML de la web (o se usa el archivo que subió el usuario).
2. Se envía a Claude el texto de la página, sus datos estructurados y las correcciones propuestas por Tríada.
3. Claude devuelve las palabras clave recomendadas, el valor final de cada corrección (títulos, descripciones,
   H1, textos alternativos y datos estructurados con los «REVISAR» resueltos) y nuevos títulos/descripciones
   cuando los actuales no incluyen la palabra clave principal.

Reglas: los «REVISAR» se completan solo con datos que aparecen en la página o en sus datos estructurados. Si un
dato no está (GTIN/EAN, SKU, marca…), se quita ese campo opcional: nunca se inventan identificadores, precios,
valoraciones, reseñas, teléfonos ni direcciones. Todo queda marcado como «sugerido por IA» y se puede editar.

Las palabras clave son una recomendación de la IA a partir del contenido (y de los datos de Google Ads o
Search Console si se importaron): no son volúmenes de búsqueda medidos.

En el proyecto de demostración se usa un motor simulado (sin coste, marcado como tal).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from bs4 import BeautifulSoup

from .app import servicio, subidas
from .app.aplicar import ErrorAplicacion, _tipo_jsonld
from .externo import almacen
from .fixes import REVISAR
from .models import Issue

MODELO_DEFECTO = "claude-opus-5-5"
# $ por millón de tokens (entrada, salida), según la tabla de precios de Anthropic (nov. 2026)
PRECIOS = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
           "claude-fable-5-2": (8.0, 40.0)}
TOKENS_ESTIMADOS = (6000, 3000)  # por página (entrada, salida con razonamiento), para la estimación previa
MAX_PAGINAS_DEFECTO = 200
HILOS = 4
TIPOS_IA = ("titulo", "meta_description", "h1", "jsonld", "jsonld_breadcrumb")
CODIGO_NUEVAS = "palabras_clave"


class ErrorIA(ErrorAplicacion):
    pass


ESQUEMA = {
    "type": "object",
    "properties": {
        "palabras_clave": {
            "type": "object",
            "properties": {
                "principal": {"type": "string"},
                "secundarias": {"type": "array", "items": {"type": "string"}},
                "intencion": {"type": "string"},
            },
            "required": ["principal", "secundarias", "intencion"],
            "additionalProperties": False,
        },
        "correcciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "valor": {"type": "string"},
                               "explicacion": {"type": "string"}},
                "required": ["id", "valor", "explicacion"],
                "additionalProperties": False,
            },
        },
        "nuevas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tipo": {"type": "string", "enum": ["titulo", "meta_description"]},
                               "valor": {"type": "string"}, "explicacion": {"type": "string"}},
                "required": ["tipo", "valor", "explicacion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["palabras_clave", "correcciones", "nuevas"],
    "additionalProperties": False,
}

SISTEMA = """\
Eres un consultor experto en SEO, SEM (Google Ads) y GEO (visibilidad en buscadores con IA) para webs en español.
Recibes una página de la web de una empresa y las correcciones que propone la herramienta Tríada para ella.
Tu trabajo es dejar cada corrección lista para publicar y recomendar las palabras clave de la página.

Palabras clave
- Elige una palabra clave principal y 2-5 secundarias que un cliente real buscaría para llegar a esta página,
  según su contenido, el tipo de página y el negocio. Indica la intención de búsqueda (informativa, comercial,
  transaccional o de navegación).
- Si se te dan términos de Google Ads o consultas de Search Console, priorízalos cuando encajen con la página.
  No apuntes a los términos marcados como «sin conversión» (se pagaron sin resultado).
- No uses la misma palabra clave principal que otra página del sitio si puedes evitarlo (canibalización).

Correcciones (devuelve TODAS las recibidas, con su id)
- Título: 30-60 caracteres, con la palabra clave principal al principio de forma natural y la marca al final
  si cabe. Único y atractivo.
- Meta descripción: 120-155 caracteres, con la palabra clave principal y una secundaria, un beneficio claro y
  una llamada a la acción.
- H1: claro, con la palabra clave principal, distinto del título.
- Texto alternativo (alt): describe la imagen en pocas palabras; incluye la palabra clave solo si encaja.
- Datos estructurados (JSON-LD): devuelve el JSON completo y válido, con el mismo @type.
  Sustituye cada «REVISAR» por el dato real que aparezca en el texto de la página o en sus datos estructurados
  existentes (precio, moneda, disponibilidad, marca, SKU…). Si el dato no aparece, ELIMINA esa propiedad:
  nunca inventes GTIN/EAN, SKU, MPN, precios, valoraciones, reseñas, teléfonos, direcciones ni fechas.
- No repitas palabras clave de forma forzada. Escribe en el idioma de la página.
- En «explicacion» di en una frase, para alguien que no sabe de programación, qué has hecho y por qué.

Nuevas correcciones
- Si el título o la meta descripción actuales no incluyen la palabra clave principal y no hay ya una corrección
  de ese tipo, propón uno nuevo en «nuevas». Si están bien, deja «nuevas» vacío.
"""


# ------------------------------------------------------------------ datos de entrada

def texto_pagina(html: str, max_texto: int = 6000) -> dict[str, Any]:
    """Lo que la IA necesita de una página: títulos, texto visible y datos estructurados existentes."""
    soup = BeautifulSoup(html, "lxml")
    jsonld = [s.get_text()[:3000] for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)})]
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    titulo = soup.title.get_text(" ", strip=True) if soup.title else ""
    for t in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        t.decompose()
    cuerpo = soup.find("main") or soup.body or soup
    texto = " ".join(cuerpo.get_text(" ").split())
    return {
        "titulo": titulo,
        "meta_description": (meta.get("content") or "").strip() if meta else "",
        "h1": [h.get_text(" ", strip=True) for h in soup.find_all("h1")][:3],
        "h2": [h.get_text(" ", strip=True) for h in soup.find_all("h2")][:12],
        "texto": texto[:max_texto],
        "datos_estructurados": jsonld[:4],
    }


def contexto_palabras(a: dict[str, Any]) -> dict[str, Any]:
    """Datos de palabras clave disponibles: campañas configuradas y lo importado de Google Ads / Search Console."""
    cfg = a.get("config") or {}
    ctx: dict[str, Any] = {"marca": cfg.get("marca") or "", "campanas": [
        {"landing": c.get("landing"), "palabras_clave": c.get("palabras_clave") or []} for c in cfg.get("campanas") or []]}
    try:
        ext = almacen.leer(a["proyecto"])
    except (ValueError, KeyError):
        ext = {}
    ads, sc = ext.get("ads") or {}, ext.get("search_console") or {}
    if (pc := ads.get("palabras_clave")):
        ctx["google_ads_nivel_calidad_bajo"] = [x["palabra"] for x in pc.get("qs_bajo", [])][:20]
    if (te := ads.get("terminos")):
        ctx["google_ads_sin_conversion"] = [x["termino"] for x in te.get("negativas", [])][:30]
    if (co := sc.get("consultas")):
        ctx["search_console_oportunidades"] = [x["consulta"] for x in co.get("oportunidades", [])][:15]
    return ctx


def _apta(f: dict[str, Any]) -> bool:
    return f["tipo"] in TIPOS_IA or f["tipo"].startswith("alt:")


def paginas_objetivo(a: dict[str, Any], rama: str, max_paginas: int | None = None) -> list[str]:
    """Páginas a optimizar: primero las que tienen «REVISAR», luego las demás con correcciones y,
    en SEO, el resto de páginas indexables (para revisar sus palabras clave)."""
    hechas = set(subidas.estado(a["id"])["aplicados"])
    con_fix: dict[str, bool] = {}
    for f in a["fixes"]:
        if rama in f.get("ramas", []) and f.get("url") and _apta(f) and f["id"] not in hechas:
            con_fix[f["url"]] = con_fix.get(f["url"], False) or REVISAR in f["valor_propuesto"]
    orden = [u for u, r in con_fix.items() if r] + [u for u, r in con_fix.items() if not r]
    if rama == "seo":
        orden += [p["url"] for p in a["paginas"]
                  if p.get("indexable") and p.get("status") == 200 and p["url"] not in con_fix]
    return orden[: max_paginas or MAX_PAGINAS_DEFECTO]


def estimacion(aid: str, rama: str) -> dict[str, Any]:
    a = _analisis(aid)
    aj = almacen.leer_ajustes()
    modelo = aj["modelos"].get("claude") or MODELO_DEFECTO
    n = len(paginas_objetivo(a, rama, aj.get("ia_max_paginas")))
    simulado = bool(a.get("demo"))
    pe, ps = PRECIOS.get(modelo, PRECIOS[MODELO_DEFECTO])
    coste = 0.0 if simulado else n * (TOKENS_ESTIMADOS[0] * pe + TOKENS_ESTIMADOS[1] * ps) / 1e6
    return {"paginas": n, "modelo": modelo, "coste_aprox_usd": round(coste, 2), "simulado": simulado,
            "clave_configurada": bool(aj["claves"].get("anthropic"))}


# ------------------------------------------------------------------ motores

def _cliente_claude(cliente=None):
    if cliente is not None:
        return cliente
    clave = almacen.leer_ajustes()["claves"].get("anthropic")
    if not clave:
        raise ErrorIA("Falta la clave de Claude: añádela en Ajustes → Claves de API → Claude (Anthropic).")
    import anthropic

    return anthropic.Anthropic(api_key=clave)


def _con_fallbacks(modelo: str) -> bool:
    return modelo.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5"))


def preguntar_claude(cliente, modelo: str, sistema: str, pagina: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if _con_fallbacks(modelo) else {}
    r = cliente.beta.messages.create(
        model=modelo,
        max_tokens=16000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ESQUEMA}},
        system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": json.dumps(pagina, ensure_ascii=False)}],
        **extra,
    )
    if r.stop_reason == "refusal":
        raise ErrorIA("La IA no ha podido procesar esta página.")
    if r.stop_reason == "max_tokens":
        raise ErrorIA("La respuesta de la IA para esta página fue demasiado larga.")
    texto = next((b.text for b in r.content if b.type == "text"), "")
    return json.loads(texto), r.usage


def _vacio(v) -> bool:
    """Un objeto que se ha quedado sin datos (solo @type) o una lista vacía."""
    return (isinstance(v, dict) and not (set(v) - {"@type", "@context"})) or (isinstance(v, list) and not v)


def _quitar_revisar(obj):
    """Motor simulado: elimina las propiedades que siguen con «REVISAR» (no inventa nada)."""
    if isinstance(obj, dict):
        limpio = {k: _quitar_revisar(v) for k, v in obj.items() if not (isinstance(v, str) and REVISAR in v)}
        return {k: v for k, v in limpio.items() if not _vacio(v)}
    if isinstance(obj, list):
        limpio = [_quitar_revisar(v) for v in obj if not (isinstance(v, str) and REVISAR in v)]
        return [v for v in limpio if not _vacio(v)]
    return obj


def preguntar_simulado(pagina: dict[str, Any]) -> tuple[dict[str, Any], None]:
    base = (pagina["h1"] or [pagina["titulo"]] or [""])[0] or pagina["url"].rstrip("/").rsplit("/", 1)[-1]
    principal = " ".join(re.findall(r"\w+", base.lower())[:3]) or "inicio"
    correcciones = []
    for f in pagina["correcciones"]:
        valor = f["valor_propuesto"]
        if f["tipo"] in ("jsonld", "jsonld_breadcrumb") and REVISAR in valor:
            try:
                valor = json.dumps(_quitar_revisar(json.loads(valor)), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                continue
        correcciones.append({"id": f["id"], "valor": valor.replace(REVISAR, "").strip(),
                             "explicacion": "Simulado (demo): se quitan los datos que no aparecen en la página."})
    tipos = {f["tipo"] for f in pagina["correcciones"]}
    nuevas = []
    if "titulo" not in tipos and principal not in pagina["titulo"].lower():
        nuevas.append({"tipo": "titulo", "valor": f"{base[:45]} | {pagina['marca']}".strip(" |")[:60],
                       "explicacion": "Simulado (demo): título con la palabra clave principal."})
    return {"palabras_clave": {"principal": principal, "secundarias": [], "intencion": "simulada"},
            "correcciones": correcciones, "nuevas": nuevas}, None


# ------------------------------------------------------------------ proceso

def _analisis(aid: str) -> dict[str, Any]:
    a = servicio.leer_analisis(aid)
    if a is None:
        raise ErrorIA("Análisis no encontrado")
    return a


def _valida(f: dict[str, Any], valor: str) -> bool:
    if not valor.strip() or REVISAR in valor:
        return False
    if f["tipo"] in ("jsonld", "jsonld_breadcrumb"):
        return _tipo_jsonld(valor) is not None and _tipo_jsonld(valor) == _tipo_jsonld(f["valor_propuesto"].replace(
            REVISAR, "x"))
    if f["tipo"] == "titulo":
        return len(valor) <= 75
    if f["tipo"] == "meta_description":
        return len(valor) <= 200
    return True


def optimizar(aid: str, rama: str, cliente=None, progreso: Callable[[int, int], None] | None = None,
              max_paginas: int | None = None, cliente_web=None) -> dict[str, Any]:
    a = _analisis(aid)
    simulado = bool(a.get("demo"))
    aj = almacen.leer_ajustes()
    modelo = aj["modelos"].get("claude") or MODELO_DEFECTO
    claude = None if simulado else _cliente_claude(cliente)
    urls = paginas_objetivo(a, rama, max_paginas or aj.get("ia_max_paginas"))
    if not urls:
        return {"paginas": 0, "mensaje": "No hay páginas que optimizar en esta vertiente."}

    # 1. HTML de cada página (lo subido por el usuario o descargado de la web)
    subidas.traer_de_la_web(aid, urls, cliente_web)
    archivos = {x["url"]: x["nombre"] for x in subidas.estado(aid)["archivos"] if x["url"]}
    base = subidas._base(aid) / "original"
    ediciones = servicio.leer_ediciones(aid)
    hechas = set(subidas.estado(aid)["aplicados"])
    por_url: dict[str, list[dict[str, Any]]] = {}
    for f in a["fixes"]:
        if rama in f.get("ramas", []) and f.get("url") and _apta(f) and f["id"] not in hechas:
            por_url.setdefault(f["url"], []).append(f)
    info_pag = {p["url"]: p for p in a["paginas"]}

    ctx = contexto_palabras(a)
    otras = [p["title"] for p in a["paginas"] if p.get("title")][:60]
    sistema = SISTEMA + "\nContexto del sitio:\n" + json.dumps(
        {"web": a["url"], **ctx, "titulos_de_otras_paginas": otras}, ensure_ascii=False, indent=1)

    try:
        import anthropic
        fatales: tuple[type[BaseException], ...] = (anthropic.AuthenticationError, anthropic.PermissionDeniedError)
        de_api: tuple[type[BaseException], ...] = (anthropic.APIError,)
    except ImportError:  # pragma: no cover
        fatales, de_api = (), ()

    def una(url: str) -> tuple[str, dict[str, Any] | None, Any, str]:
        nombre = archivos.get(url)
        if not nombre or not (base / nombre).exists():
            return url, None, None, "No se pudo obtener el HTML de la página"
        datos = texto_pagina(subidas._texto((base / nombre).read_bytes()))
        fixes = por_url.get(url, [])
        pagina = {"url": url, "tipo": info_pag.get(url, {}).get("tipo"), "marca": ctx["marca"], **datos,
                  "correcciones": [{"id": f["id"], "tipo": f["tipo"], "valor_actual": f.get("valor_actual"),
                                    "valor_propuesto": ediciones.get(f["id"], f["valor_propuesto"]),
                                    "nota": f.get("nota") or ""} for f in fixes]}
        try:
            if simulado:
                res, uso = preguntar_simulado(pagina)
            else:
                res, uso = preguntar_claude(claude, modelo, sistema, pagina)
        except fatales:
            raise
        except ErrorIA as e:
            return url, None, None, str(e)
        except (json.JSONDecodeError, KeyError, TypeError):
            return url, None, None, "La respuesta de la IA no se pudo leer"
        except de_api as e:
            return url, None, None, f"Error de la API de Claude: {getattr(e, 'message', e)}"
        return url, res, uso, ""

    resultados = []
    total = len(urls)
    if progreso:
        progreso(0, total)
    with ThreadPoolExecutor(max_workers=1 if simulado else HILOS) as ex:
        try:
            for i, r in enumerate(ex.map(una, urls), 1):
                resultados.append(r)
                if progreso:
                    progreso(i, total)
        except fatales as e:
            raise ErrorIA("La clave de Claude no es válida o no tiene permiso. Revísala en Ajustes.") from e

    # 2. guardar: ediciones, palabras clave y nuevas correcciones
    a = _analisis(aid)  # releer por si cambió mientras tanto
    por_id = {f["id"]: f for f in a["fixes"]}
    nuevas_ed: dict[str, str] = {}
    estado_ia = _leer_ia(aid)
    errores, tokens = [], [0, 0, 0, 0]
    n_revisar = n_nuevas = 0
    existentes_ia = {(f["url"], f["tipo"]): f["id"] for f in a["fixes"] if f.get("codigo_issue") == CODIGO_NUEVAS}
    urls_nuevas = []
    for url, res, uso, error in resultados:
        if error:
            errores.append({"url": url, "motivo": error})
            continue
        if uso is not None:
            tokens[0] += getattr(uso, "input_tokens", 0) or 0
            tokens[1] += getattr(uso, "output_tokens", 0) or 0
            tokens[2] += getattr(uso, "cache_read_input_tokens", 0) or 0
            tokens[3] += getattr(uso, "cache_creation_input_tokens", 0) or 0
        estado_ia["paginas"][url] = {"palabras_clave": res["palabras_clave"], "rama": rama}
        validos = {f["id"] for f in por_url.get(url, [])}
        for c in res["correcciones"]:
            f = por_id.get(c["id"])
            if c["id"] not in validos or f is None or not _valida(f, c["valor"]):
                continue
            if REVISAR in ediciones.get(f["id"], f["valor_propuesto"]):
                n_revisar += 1
            nuevas_ed[c["id"]] = c["valor"].strip()
            estado_ia["explicaciones"][c["id"]] = c["explicacion"]
        # tipos que ya tienen corrección en esta página (de cualquier vertiente, aplicada o no), salvo las de la IA
        tipos_hay = {f["tipo"] for f in a["fixes"] if f.get("url") == url and f.get("codigo_issue") != CODIGO_NUEVAS}
        actual = info_pag.get(url, {})
        for n in res["nuevas"]:
            if n["tipo"] in tipos_hay or not n["valor"].strip() or not _valida({"tipo": n["tipo"], "valor_propuesto": ""},
                                                                                  n["valor"]):
                continue
            fid = existentes_ia.get((url, n["tipo"]))
            if fid is None:
                fid = f"IA{sum(1 for f in a['fixes'] if f.get('codigo_issue') == CODIGO_NUEVAS) + 1:03d}"
                a["fixes"].append({
                    "codigo_issue": CODIGO_NUEVAS, "url": url, "tipo": n["tipo"],
                    "valor_actual": actual.get("title" if n["tipo"] == "titulo" else "meta_description"),
                    "valor_propuesto": n["valor"].strip(), "requiere_revision": True,
                    "nota": "Propuesta de la IA para incluir la palabra clave principal.", "archivo": None,
                    "id": fid, "ramas": sorted({"seo", rama}),
                })
                existentes_ia[(url, n["tipo"])] = fid
            else:
                nuevas_ed[fid] = n["valor"].strip()
            estado_ia["explicaciones"][fid] = n["explicacion"]
            urls_nuevas.append(url)
            n_nuevas += 1

    if urls_nuevas:
        iss = next((i for i in a["issues"] if i["codigo"] == CODIGO_NUEVAS), None)
        if iss is None:
            nuevo = Issue(CODIGO_NUEVAS, {"seo", rama}, "media",
                          "Títulos y descripciones sin la palabra clave principal (propuesta de la IA).",
                          "Incluye la palabra clave recomendada en el título y la meta descripción de cada página.",
                          sorted(set(urls_nuevas)))
            a["issues"].append(nuevo.to_dict() | {"prioridad": nuevo.prioridad(a["total_paginas"])})
        else:
            iss["urls"] = sorted(set(iss["urls"]) | set(urls_nuevas))
            iss["ramas"] = sorted(set(iss["ramas"]) | {"seo", rama})
        servicio.guardar_analisis(aid, a)
    if nuevas_ed:
        servicio.guardar_ediciones(aid, nuevas_ed)

    pe, ps = PRECIOS.get(modelo, PRECIOS[MODELO_DEFECTO])
    coste = (tokens[0] * pe + tokens[1] * ps + tokens[2] * pe * 0.1 + tokens[3] * pe * 1.25) / 1e6
    estado_ia["ejecuciones"].append({
        "fecha": datetime.now().isoformat(timespec="seconds"), "rama": rama, "modelo": "simulado" if simulado else modelo,
        "paginas": total, "errores": len(errores), "coste_aprox_usd": round(coste, 4),
    })
    _guardar_ia(aid, estado_ia)
    return {"paginas": total, "paginas_con_error": errores, "correcciones_mejoradas": len(nuevas_ed),
            "revisar_completados": n_revisar, "nuevas_correcciones": n_nuevas, "simulado": simulado,
            "modelo": "simulado" if simulado else modelo, "coste_aprox_usd": round(coste, 4)}


def optimizar_y_aplicar(aid: str, rama: str, cliente=None, progreso=None, cliente_web=None) -> dict[str, Any]:
    """Todo en un paso: IA → aplicar todas las correcciones listas de la vertiente al HTML → .zip descargable."""
    ia = optimizar(aid, rama, cliente, progreso, cliente_web=cliente_web)
    a = _analisis(aid)
    ed = servicio.leer_ediciones(aid)
    antes = set(subidas.estado(aid)["aplicados"])
    listas = [f["id"] for f in a["fixes"] if rama in f.get("ramas", [])
              and REVISAR not in ed.get(f["id"], f["valor_propuesto"]) and ed.get(f["id"], f["valor_propuesto"]).strip()]
    ids = listas
    if antes & set(ed):
        # alguna corrección ya aplicada ha cambiado: se parte de los originales y se vuelve a aplicar todo
        subidas.restablecer(aid)
        ids = list(dict.fromkeys(list(antes) + listas))
    elif antes:
        ids = [i for i in listas if i not in antes]
    r = subidas.aplicar(aid, ids, ed, desde_web=True, cliente=cliente_web) if ids else \
        {"aplicados": [], "omitidos": [], "descargadas": 0, "fallidas": [], "subidas": subidas.estado(aid)}
    return {"ia": ia, "aplicados": r["aplicados"], "omitidos": r["omitidos"], "descargadas": r["descargadas"],
            "fallidas": r["fallidas"], "subidas": r["subidas"]}


def agregar_correccion(aid: str, url: str, tipo: str, valor: str, rama: str = "seo",
                       explicacion: str = "") -> dict[str, Any]:
    """Añade (o actualiza) una corrección de título/meta descripción propuesta por la IA o por Claude."""
    if tipo not in ("titulo", "meta_description"):
        raise ErrorIA("Solo se pueden crear correcciones de título o meta descripción.")
    if not _valida({"tipo": tipo, "valor_propuesto": ""}, valor):
        raise ErrorIA("El texto está vacío o es demasiado largo.")
    a = _analisis(aid)
    pagina = next((p for p in a["paginas"] if p["url"] == url), None)
    if pagina is None:
        raise ErrorIA("Esa URL no es una página de este análisis.")
    existente = next((f for f in a["fixes"] if f.get("url") == url and f["tipo"] == tipo), None)
    if existente is not None:
        servicio.guardar_ediciones(aid, {existente["id"]: valor.strip()})
        fid = existente["id"]
    else:
        fid = f"IA{sum(1 for f in a['fixes'] if f.get('codigo_issue') == CODIGO_NUEVAS) + 1:03d}"
        a["fixes"].append({"codigo_issue": CODIGO_NUEVAS, "url": url, "tipo": tipo,
                           "valor_actual": pagina.get("title" if tipo == "titulo" else "meta_description"),
                           "valor_propuesto": valor.strip(), "requiere_revision": True,
                           "nota": "Propuesta de la IA para incluir la palabra clave principal.", "archivo": None,
                           "id": fid, "ramas": sorted({"seo", rama})})
        iss = next((i for i in a["issues"] if i["codigo"] == CODIGO_NUEVAS), None)
        if iss is None:
            nuevo = Issue(CODIGO_NUEVAS, {"seo", rama}, "media",
                          "Títulos y descripciones sin la palabra clave principal (propuesta de la IA).",
                          "Incluye la palabra clave recomendada en el título y la meta descripción de cada página.", [url])
            a["issues"].append(nuevo.to_dict() | {"prioridad": nuevo.prioridad(a["total_paginas"])})
        else:
            iss["urls"] = sorted(set(iss["urls"]) | {url})
            iss["ramas"] = sorted(set(iss["ramas"]) | {"seo", rama})
        servicio.guardar_analisis(aid, a)
    if explicacion:
        est = _leer_ia(aid)
        est["explicaciones"][fid] = explicacion
        _guardar_ia(aid, est)
    return {"id": fid, "guardada": True, "existia": existente is not None}


def _ruta_ia(aid: str):
    return servicio.carpeta_analisis(aid) / "ia.json"


def _leer_ia(aid: str) -> dict[str, Any]:
    f = _ruta_ia(aid)
    base: dict[str, Any] = {"paginas": {}, "explicaciones": {}, "ejecuciones": []}
    if f.exists():
        base.update(json.loads(f.read_text(encoding="utf-8")))
    return base


def _guardar_ia(aid: str, datos: dict[str, Any]) -> None:
    _ruta_ia(aid).write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def estado(aid: str) -> dict[str, Any]:
    return _leer_ia(aid)
