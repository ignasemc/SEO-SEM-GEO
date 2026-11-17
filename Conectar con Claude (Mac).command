#!/bin/bash
# Doble clic para conectar Tríada con Claude Desktop en macOS.
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  echo
  echo "  Tríada necesita Python 3.11 o superior y no se ha encontrado."
  echo "  Se abrirá la página de descarga. Instálalo y vuelve a abrir este archivo."
  open "https://www.python.org/downloads/"
  read -r -p "  Pulsa Enter para cerrar…"
  exit 1
fi

echo
echo "  Preparando el conector de Tríada para Claude. Tarda 1 o 2 minutos…"
echo
if { [ -x .venv/bin/python ] || "$PY" -m venv .venv; } \
   && .venv/bin/python -m pip install --upgrade pip -q \
   && .venv/bin/python -m pip install -e ".[claude]" -q; then
  .venv/bin/python -m triada.cli conectar-claude
else
  echo "  No se pudieron instalar los componentes. Comprueba tu conexión a internet y vuelve a intentarlo."
fi
read -r -p "  Pulsa Enter para cerrar…"
