"""El rastreo se limita a la web indicada: nunca analiza ni corrige páginas de otros dominios."""

import http.server
import threading

from triada.audits.base import paginas_contenido
from triada.config import Config
from triada.crawler import rastrear_sync
from triada.urls import mismo_sitio


def _servidor(rutas: dict[str, tuple[int, str, dict]]):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            estado, cuerpo, cabeceras = rutas.get(self.path, (404, "no", {}))
            datos = cuerpo.encode()
            self.send_response(estado)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for k, v in cabeceras.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _pagina(titulo: str, enlaces: list[str]) -> str:
    a = "".join(f'<a href="{e}">{e}</a>' for e in enlaces)
    return f"<html lang='es'><head><title>{titulo}</title></head><body><h1>{titulo}</h1>{a}</body></html>"


def test_mismo_sitio_con_y_sin_www():
    assert mismo_sitio("https://www.petground.es/producto/x/", "petground.es")
    assert mismo_sitio("https://petground.es/", "www.petground.es")
    assert not mismo_sitio("https://otra-empresa.com/", "petground.es")
    assert not mismo_sitio("https://petground.es.otra.com/", "petground.es")


def test_no_rastrea_otras_webs():
    otra = _servidor({"/": (200, _pagina("Otra empresa", ["/sus-productos/"]), {}),
                      "/sus-productos/": (200, _pagina("Productos de otra empresa", []), {})})
    url_otra = f"http://127.0.0.1:{otra.server_port}"
    # «localhost» y «127.0.0.1» son hosts distintos: la web propia y la ajena no comparten dominio
    propia = _servidor({
        "/": (200, _pagina("Inicio", ["/tienda/", "/salir", url_otra + "/"]), {}),
        "/tienda/": (200, _pagina("Tienda", ["/"]), {}),
        "/salir": (302, "", {"Location": url_otra + "/"}),  # enlace interno que redirige a otra web
    })
    try:
        site = rastrear_sync(Config(url=f"http://localhost:{propia.server_port}/", max_paginas=50))
        urls = [p.final_url for p in paginas_contenido(site)]
        assert urls and all(mismo_sitio(u, site.host) for u in urls)
        assert not any("Otra empresa" in (p.title or "") or "otra empresa" in (p.title or "") for p in site.pages.values())
        assert any("Redirige fuera del sitio" in e for e in site.errores_rastreo)
    finally:
        otra.shutdown()
        propia.shutdown()
