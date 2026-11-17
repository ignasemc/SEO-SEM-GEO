"""Registra el conector de Tríada en Claude Desktop (y muestra cómo añadirlo a Claude Code).

    triada conectar-claude

Añade la entrada «triada» a claude_desktop_config.json sin tocar el resto de conectores y guarda una copia
de seguridad del archivo anterior. Después hay que cerrar y volver a abrir Claude Desktop.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

NOMBRE = "triada"


def rutas_config() -> list[Path]:
    """Ubicaciones posibles del archivo de configuración de Claude Desktop en este sistema."""
    sistema = platform.system()
    if sistema == "Windows":
        rutas = []
        if appdata := os.environ.get("APPDATA"):
            rutas.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
        # versión de la Microsoft Store: guarda la configuración dentro de su paquete
        if local := os.environ.get("LOCALAPPDATA"):
            for paquete in sorted((Path(local) / "Packages").glob("Claude_*")):
                rutas.append(paquete / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
        return rutas
    if sistema == "Darwin":
        return [Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
    return [Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude" / "claude_desktop_config.json"]


def entrada_servidor(python: str | None = None) -> dict[str, Any]:
    entrada: dict[str, Any] = {"command": python or sys.executable, "args": ["-m", "triada.conector_claude"]}
    if os.environ.get("TRIADA_DATOS"):
        entrada["env"] = {"TRIADA_DATOS": os.environ["TRIADA_DATOS"]}
    return entrada


def registrar(ruta: Path, entrada: dict[str, Any]) -> Path | None:
    """Añade o actualiza la entrada en el archivo. Devuelve la copia de seguridad si había archivo previo."""
    copia = None
    datos: dict[str, Any] = {}
    if ruta.exists() and ruta.read_text(encoding="utf-8").strip():
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"  El archivo {ruta} no es JSON válido ({e}). Corrígelo o bórralo y vuelve a intentarlo.")
        copia = ruta.with_name(f"claude_desktop_config.antes-de-triada-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copy2(ruta, copia)
    datos.setdefault("mcpServers", {})[NOMBRE] = entrada
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return copia


def comprobar_mcp() -> bool:
    r = subprocess.run([sys.executable, "-c", "import mcp.server.mcpserver"], capture_output=True)
    return r.returncode == 0


def main() -> int:
    if not comprobar_mcp():
        print('  Falta el paquete «mcp». Instálalo con:  pip install -e ".[claude]"')
        return 1
    entrada = entrada_servidor()
    rutas = rutas_config()
    existentes = [r for r in rutas if r.parent.exists()] or rutas[:1]
    print()
    for ruta in existentes:
        copia = registrar(ruta, entrada)
        print(f"  ✔ Tríada añadida a Claude Desktop: {ruta}")
        if copia:
            print(f"    (copia de seguridad de tu configuración anterior: {copia.name})")
    comando = " ".join(f'"{x}"' if " " in x else x for x in [entrada["command"], *entrada["args"]])
    print(f"""
  Siguiente paso: cierra Claude Desktop del todo (también desde la bandeja del sistema / barra de menús)
  y vuelve a abrirlo. En un chat nuevo pídele, por ejemplo:

     «Analiza https://www.miempresa.com con Tríada y corrige el SEO»

  ¿Usas Claude Code? Añádelo con:

     claude mcp add {NOMBRE} -- {comando}
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
