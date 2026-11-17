"""Servidor HTTP local para la web de prueba (sustituye PUERTO en el sitemap)."""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

RAIZ = Path(__file__).parent.parent / "triada" / "app" / "demo_sitio"


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silencioso
        pass

    def do_GET(self):
        if self.path == "/sitemap.xml":
            cuerpo = (RAIZ / "sitemap.xml").read_text().replace("PUERTO", str(self.server.server_port)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        super().do_GET()


def arrancar() -> tuple[http.server.ThreadingHTTPServer, str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(RAIZ)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/"


if __name__ == "__main__":
    s, url = arrancar()
    print(url)
    threading.Event().wait()
