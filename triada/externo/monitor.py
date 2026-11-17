"""Monitor de visibilidad en buscadores con IA.

Lanza preguntas típicas de clientes contra las APIs oficiales (con búsqueda web activada) y mide:
- en qué porcentaje de respuestas aparece tu marca y la de cada competidor,
- si la IA cita tu web como fuente,
- qué webs cita la IA cuando habla de tu sector (ahí es donde tienes que estar).

Limitaciones: las respuestas varían entre ejecuciones y no son idénticas a las que ve un usuario en la
app de cada asistente. Por eso cada pregunta se repite y se miran tendencias, no casos sueltos.
Solo se usan APIs oficiales.

Formatos según la documentación oficial (noviembre de 2026):
- Gemini: POST /v1beta/interactions con tools [{"type": "google_search"}]; citas en
  steps[type=model_output].content[].annotations[type=url_citation].
- OpenAI: POST /v1/responses con tools [{"type": "web_search"}]; citas en
  output[type=message].content[type=output_text].annotations[type=url_citation].
- Perplexity: POST /chat/completions (modelo sonar); fuentes en search_results[] / citations[].
"""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

TIMEOUT = 90.0
NOMBRES = {"gemini": "Gemini (Google)", "openai": "ChatGPT (OpenAI)", "perplexity": "Perplexity",
           "simulado": "Simulado (solo demo)"}


class ErrorMotor(Exception):
    pass


# ------------------------------------------------------------------ clientes

def _post(cliente: httpx.Client, url: str, headers: dict, cuerpo: dict) -> dict:
    try:
        r = cliente.post(url, headers=headers, json=cuerpo, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise ErrorMotor(f"Error de conexión: {e}") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else msg
        except ValueError:
            msg = r.text[:200]
        raise ErrorMotor(f"HTTP {r.status_code}: {msg}")
    return r.json()


def parsear_gemini(d: dict) -> tuple[str, list[dict]]:
    textos, fuentes = [], []
    for paso in d.get("steps", []) or []:
        if paso.get("type") != "model_output":
            continue
        for bloque in paso.get("content", []) or []:
            if bloque.get("type") == "text":
                textos.append(bloque.get("text", ""))
                for a in bloque.get("annotations", []) or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        fuentes.append({"url": a["url"], "titulo": a.get("title", "")})
    # compatibilidad con el formato anterior (generateContent + groundingMetadata)
    for cand in d.get("candidates", []) or []:
        for parte in (cand.get("content") or {}).get("parts", []) or []:
            if parte.get("text"):
                textos.append(parte["text"])
        for chunk in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) or []:
            web = chunk.get("web") or {}
            if web.get("uri"):
                fuentes.append({"url": web["uri"], "titulo": web.get("title", "")})
    return "\n".join(textos), fuentes


def parsear_openai(d: dict) -> tuple[str, list[dict]]:
    textos, fuentes = [], []
    for item in d.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content", []) or []:
            if c.get("type") == "output_text":
                textos.append(c.get("text", ""))
                for a in c.get("annotations", []) or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        fuentes.append({"url": a["url"], "titulo": a.get("title", "")})
    return "\n".join(textos), fuentes


def parsear_perplexity(d: dict) -> tuple[str, list[dict]]:
    texto = ""
    if d.get("choices"):
        texto = ((d["choices"][0] or {}).get("message") or {}).get("content", "") or ""
    fuentes = [{"url": s["url"], "titulo": s.get("title", "")} for s in d.get("search_results") or [] if s.get("url")]
    if not fuentes:
        fuentes = [{"url": u, "titulo": ""} for u in d.get("citations") or [] if isinstance(u, str)]
    return texto, fuentes


def preguntar(motor: str, pregunta: str, clave: str, modelo: str, cliente: httpx.Client) -> tuple[str, list[dict]]:
    if motor == "gemini":
        d = _post(cliente, "https://generativelanguage.googleapis.com/v1beta/interactions",
                  {"x-goog-api-key": clave, "Content-Type": "application/json"},
                  {"model": modelo, "input": pregunta, "tools": [{"type": "google_search"}]})
        return parsear_gemini(d)
    if motor == "openai":
        d = _post(cliente, "https://api.openai.com/v1/responses",
                  {"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
                  {"model": modelo, "input": pregunta, "tools": [{"type": "web_search"}]})
        return parsear_openai(d)
    if motor == "perplexity":
        d = _post(cliente, "https://api.perplexity.ai/chat/completions",
                  {"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
                  {"model": modelo, "messages": [{"role": "user", "content": pregunta}]})
        return parsear_perplexity(d)
    raise ErrorMotor(f"Motor desconocido: {motor}")


# ------------------------------------------------------------------ motor simulado (demo)

FUENTES_DEMO = ["guiadelhogar.example", "comparadorreformas.example", "foro-casas.example",
                "periodicolocal.example", "directorio-empresas.example", "blogdecasas.example"]


def simular(pregunta: str, rep: int, marca: str, competidores: list[dict], ejecucion: int) -> tuple[str, list[dict]]:
    """Respuesta inventada y reproducible para la demostración. Nunca se usa con datos reales."""
    semilla = int(hashlib.md5(f"{pregunta}|{rep}|{ejecucion}".encode()).hexdigest(), 16)
    rnd = random.Random(semilla)
    nombres = [c["nombre"] for c in competidores if c.get("nombre")]
    elegidos = rnd.sample(nombres, k=min(len(nombres), rnd.randint(1, 3))) if nombres else []
    # la marca aparece poco al principio y algo más en ejecuciones posteriores
    if marca and rnd.random() < min(0.15 + 0.1 * ejecucion, 0.6):
        elegidos.insert(rnd.randint(0, len(elegidos)), marca)
    texto = (f"Para «{pregunta}», estas son algunas opciones valoradas: " + ", ".join(elegidos) +
             ". Conviene comparar presupuestos cerrados, plazos y opiniones de clientes." if elegidos else
             f"Para «{pregunta}», compara presupuestos cerrados, plazos y opiniones de clientes.")
    pesos = [5, 4, 3, 2, 2, 1]
    fuentes = []
    for dom in rnd.choices(FUENTES_DEMO, weights=pesos, k=rnd.randint(2, 4)):
        fuentes.append({"url": f"https://{dom}/articulo-{rnd.randint(1, 9)}", "titulo": dom})
    return texto, fuentes


# ------------------------------------------------------------------ detección y agregado

def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def dominio(url: str) -> str:
    h = urlparse(url if "://" in url else "https://" + url).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def menciona(texto: str, nombres: list[str], dominio_web: str = "") -> bool:
    t = _norm(texto)
    for n in nombres:
        n = _norm(n).strip()
        if len(n) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t):
            return True
    if dominio_web:
        base = _norm(dominio_web)
        if base and base in t:
            return True
    return False


def analizar_respuesta(texto: str, fuentes: list[dict], perfil: dict, competidores: list[dict]) -> dict[str, Any]:
    nombres = [perfil.get("marca", "")] + list(perfil.get("alias") or [])
    dom = dominio(perfil.get("dominio", "")) if perfil.get("dominio") else ""
    doms_fuentes = [dominio(f["url"]) for f in fuentes]
    comp = []
    for c in competidores:
        cn = [c.get("nombre", "")] + list(c.get("alias") or [])
        cd = dominio(c["dominio"]) if c.get("dominio") else ""
        if menciona(texto, cn, cd) or (cd and cd in doms_fuentes):
            comp.append(c.get("nombre") or cd)
    return {
        "menciona_marca": menciona(texto, nombres, dom) or bool(dom and dom in doms_fuentes),
        "cita_tu_web": bool(dom and any(d == dom or d.endswith("." + dom) for d in doms_fuentes)),
        "competidores": comp,
        "dominios": sorted(set(doms_fuentes)),
    }


def agregar(respuestas: list[dict], perfil: dict, competidores: list[dict]) -> dict[str, Any]:
    ok = [r for r in respuestas if not r.get("error")]
    n = len(ok)
    por_motor: dict[str, dict] = {}
    for m in sorted({r["motor"] for r in respuestas}):
        rs = [r for r in ok if r["motor"] == m]
        errores = [r["error"] for r in respuestas if r["motor"] == m and r.get("error")]
        por_motor[m] = {
            "respuestas": len(rs), "errores": len(errores), "error_ejemplo": errores[0] if errores else None,
            "pct_mencion": round(100 * sum(r["menciona_marca"] for r in rs) / len(rs), 1) if rs else None,
            "pct_cita_web": round(100 * sum(r["cita_tu_web"] for r in rs) / len(rs), 1) if rs else None,
        }
    cuota = {perfil.get("marca") or "Tu marca": round(100 * sum(r["menciona_marca"] for r in ok) / n, 1) if n else None}
    for c in competidores:
        nombre = c.get("nombre") or c.get("dominio")
        cuota[nombre] = round(100 * sum(nombre in r["competidores"] for r in ok) / n, 1) if n else None

    propios = {dominio(perfil["dominio"])} if perfil.get("dominio") else set()
    comp_doms = {dominio(c["dominio"]) for c in competidores if c.get("dominio")}
    conteo: Counter[str] = Counter()
    con_marca: Counter[str] = Counter()
    for r in ok:
        for d in r["dominios"]:
            conteo[d] += 1
            if r["menciona_marca"]:
                con_marca[d] += 1
    fuentes = [{"dominio": d, "respuestas": c, "pct": round(100 * c / n, 1) if n else 0,
                "tipo": "tu web" if d in propios else "competidor" if d in comp_doms else "tercero",
                "con_tu_marca": con_marca[d]}
               for d, c in conteo.most_common(25)]

    por_pregunta: dict[str, dict] = defaultdict(lambda: {"respuestas": 0, "marca": 0, "competidores": Counter()})
    for r in ok:
        p = por_pregunta[r["pregunta"]]
        p["respuestas"] += 1
        p["marca"] += r["menciona_marca"]
        p["competidores"].update(r["competidores"])
    preguntas = [{"pregunta": q, "respuestas": v["respuestas"],
                  "pct_marca": round(100 * v["marca"] / v["respuestas"], 1),
                  "competidores": [c for c, _ in v["competidores"].most_common(3)]}
                 for q, v in por_pregunta.items()]
    return {
        "respuestas": n, "errores": len(respuestas) - n,
        "pct_mencion": cuota[next(iter(cuota))], "por_motor": por_motor, "cuota": cuota,
        "fuentes": fuentes, "preguntas": preguntas,
    }


def ejecutar(config: dict, perfil: dict, motores: dict[str, tuple[str, str]], progreso: Callable | None = None,
             cliente: httpx.Client | None = None, n_ejecucion: int = 0) -> dict[str, Any]:
    """`motores` = {nombre: (clave, modelo)}; el motor "simulado" no necesita clave."""
    preguntas = [p.strip() for p in config.get("preguntas", []) if p.strip()]
    competidores = [c for c in config.get("competidores", []) if c.get("nombre") or c.get("dominio")]
    reps = max(1, min(int(config.get("repeticiones") or 1), 5))
    if not preguntas:
        raise ErrorMotor("Añade al menos una pregunta.")
    if not motores:
        raise ErrorMotor("No hay ningún buscador de IA configurado. Añade una clave en Ajustes.")
    propio = cliente is None
    cliente = cliente or httpx.Client()
    respuestas = []
    total = len(preguntas) * len(motores) * reps
    try:
        for motor, (clave, modelo) in motores.items():
            for q in preguntas:
                for rep in range(reps):
                    r: dict[str, Any] = {"motor": motor, "pregunta": q, "rep": rep}
                    try:
                        if motor == "simulado":
                            texto, fuentes = simular(q, rep, perfil.get("marca", ""), competidores, n_ejecucion)
                        else:
                            texto, fuentes = preguntar(motor, q, clave, modelo, cliente)
                        r.update(texto=texto[:4000], fuentes=fuentes[:20],
                                 **analizar_respuesta(texto, fuentes, perfil, competidores))
                    except ErrorMotor as e:
                        r["error"] = str(e)
                    respuestas.append(r)
                    if progreso:
                        progreso(len(respuestas), total)
    finally:
        if propio:
            cliente.close()
    return {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "motores": list(motores), "simulado": "simulado" in motores,
        "config": {"preguntas": preguntas, "competidores": competidores, "repeticiones": reps},
        "resumen": agregar(respuestas, perfil, competidores),
        "respuestas": respuestas,
    }
