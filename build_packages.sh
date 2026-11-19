#!/usr/bin/env bash
set -e

echo "=== Compilador de Pacotes Linux: Reddit for Linux (.rpm, .deb, .AppImage) ==="

VERSION="1.0.0"
echo "Versão: ${VERSION}"

# 1. Garante dependências Python
pip install -r requirements.txt
pip install pyinstaller

# 2. Compila binário nativo com PyInstaller
echo "-> Gerando binário compilado via PyInstaller..."
pyinstaller --noconfirm --onedir --windowed \
  --name "reddit-linux" \
  --add-data "reddit-linux.svg:." \
  --add-data "reddit-linux.desktop:." \
  main.py

echo "✓ Binário gerado em dist/reddit-linux/"
echo "-> Para gerar os pacotes no GitHub, basta criar uma tag:"
echo "   git tag v${VERSION} && git push origin v${VERSION}"
