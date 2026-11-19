#!/usr/bin/env python3
"""
Módulo de Verificação de Atualizações e Suporte Multi-Formato Linux (.rpm, .deb, .appimage).
Comunicação assíncrona com GitHub Releases, detecção de ambiente e diálogo modal (CR-03).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from widgets import UiSkillsButton, create_reddit_vector_icon

APP_VERSION = "1.0.0"
GITHUB_REPO = "janlucascc/reddit-linux-app"


def detect_system_format() -> str:
    """
    Detecta o formato preferencial de pacote do usuário no Linux:
    - 'appimage': se estiver executando a partir de um bundle .AppImage
    - 'deb': distros Debian, Ubuntu, Linux Mint, Pop!_OS, etc.
    - 'rpm': distros Fedora, Red Hat, CentOS, openSUSE, etc.
    - 'arch': distros Arch Linux, Manjaro, EndeavourOS.
    - 'generic': qualquer outra distribuição Linux.
    """
    # 1. Verifica se está rodando dentro de um AppImage
    if os.environ.get("APPIMAGE") or "/.mount_" in os.path.abspath(sys.argv[0]):
        return "appimage"

    # 2. Inspeciona /etc/os-release da distribuição
    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                content = f.read().lower()
                if any(
                    dist in content
                    for dist in ["debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin"]
                ):
                    return "deb"
                if any(
                    dist in content
                    for dist in ["fedora", "rhel", "centos", "rocky", "alma", "suse", "opensuse"]
                ):
                    return "rpm"
                if any(dist in content for dist in ["arch", "manjaro", "endeavouros"]):
                    return "arch"
        except Exception:
            pass

    return "generic"


FORMAT_METADATA: Dict[str, Dict[str, str]] = {
    "appimage": {
        "label": "AppImage Portátil",
        "ext": ".AppImage",
        "badge": "📦 Executando como AppImage",
        "cmd": "chmod +x reddit-linux-*.AppImage && ./reddit-linux-*.AppImage",
    },
    "deb": {
        "label": "Pacote Debian / Ubuntu (.deb)",
        "ext": ".deb",
        "badge": "🐧 Sistema Debian / Ubuntu detectado (.deb)",
        "cmd": "sudo apt install ./reddit-linux_*.deb",
    },
    "rpm": {
        "label": "Pacote Fedora / Red Hat / openSUSE (.rpm)",
        "ext": ".rpm",
        "badge": "🐧 Sistema Fedora / Red Hat detectado (.rpm)",
        "cmd": "sudo dnf install ./reddit-linux-*.rpm",
    },
    "arch": {
        "label": "Pacote Arch Linux",
        "ext": ".AppImage",
        "badge": "🐧 Sistema Arch Linux detectado",
        "cmd": "Recomendado: Usar versão .AppImage ou AUR",
    },
    "generic": {
        "label": "Linux Universal",
        "ext": ".AppImage",
        "badge": "🐧 Sistema Linux Universal",
        "cmd": "chmod +x reddit-linux-*.AppImage && ./reddit-linux-*.AppImage",
    },
}


@dataclass
class UpdateAsset:
    name: str
    download_url: str
    size_bytes: int = 0
    format_type: str = "other"  # 'appimage', 'deb', 'rpm', 'tarball', 'other'

    @property
    def size_formatted(self) -> str:
        if self.size_bytes <= 0:
            return ""
        mb = self.size_bytes / (1024 * 1024)
        return f"{mb:.1f} MB"


@dataclass
class UpdateReleaseInfo:
    tag_name: str
    version: str
    title: str
    body: str
    html_url: str
    published_at: str
    assets: List[UpdateAsset] = field(default_factory=list)
    detected_format: str = "generic"
    is_newer: bool = False

    def get_asset_for_format(self, fmt: str) -> Optional[UpdateAsset]:
        target_fmt = fmt.lower()
        for a in self.assets:
            if a.format_type == target_fmt:
                return a
        # Se for arch ou generic e não achar nativo, oferece appimage
        if target_fmt in ("arch", "generic"):
            for a in self.assets:
                if a.format_type == "appimage":
                    return a
        return None


def parse_semantic_version(ver_str: str) -> Tuple[int, ...]:
    """Extrai números de versão como (major, minor, patch)."""
    cleaned = ver_str.strip().lstrip("vV")
    nums = re.findall(r"\d+", cleaned)
    if not nums:
        return (0, 0, 0)
    return tuple(int(n) for n in nums[:3])


def check_for_updates_online(
    current_version: str = APP_VERSION,
    repo: str = GITHUB_REPO,
    timeout: float = 6.0,
) -> Optional[UpdateReleaseInfo]:
    """
    Consulta a API pública do GitHub Releases de forma silenciosa e segura.
    Retorna UpdateReleaseInfo caso uma versão mais nova seja encontrada.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"RedditLinuxDesktop/{current_version}",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, Exception):
        return None

    tag = data.get("tag_name", "")
    if not tag:
        return None

    remote_ver = parse_semantic_version(tag)
    local_ver = parse_semantic_version(current_version)

    if remote_ver <= local_ver:
        return None

    # Classifica os assets (.AppImage, .deb, .rpm)
    assets: List[UpdateAsset] = []
    for raw_asset in data.get("assets", []):
        aname = raw_asset.get("name", "")
        aurl = raw_asset.get("browser_download_url", "")
        asize = raw_asset.get("size", 0)

        lower_name = aname.lower()
        if lower_name.endswith(".appimage"):
            ftype = "appimage"
        elif lower_name.endswith(".deb"):
            ftype = "deb"
        elif lower_name.endswith(".rpm"):
            ftype = "rpm"
        elif lower_name.endswith((".tar.gz", ".tar.xz", ".zip")):
            ftype = "tarball"
        else:
            ftype = "other"

        assets.append(
            UpdateAsset(
                name=aname,
                download_url=aurl,
                size_bytes=asize,
                format_type=ftype,
            )
        )

    det_format = detect_system_format()

    return UpdateReleaseInfo(
        tag_name=tag,
        version=tag.lstrip("vV"),
        title=data.get("name") or tag,
        body=data.get("body") or "Atualizações de desempenho e correções de erros.",
        html_url=data.get("html_url") or f"https://github.com/{repo}/releases",
        published_at=data.get("published_at", "")[:10],
        assets=assets,
        detected_format=det_format,
        is_newer=True,
    )


class UpdateDialog(QDialog):
    """
    Diálogo moderno e acessível (CR-03) para exibição e download de atualizações
    com suporte nativo aos formatos .rpm, .deb e .appimage.
    """

    def __init__(
        self,
        info: UpdateReleaseInfo,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("UpdateDialog")
        self.setWindowTitle(f"Atualização Disponível — Reddit for Linux {info.tag_name}")
        self.setMinimumWidth(520)
        self.setMaximumWidth(640)
        self.setMinimumHeight(440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("UpdateDialogCard")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 16, 16, 16)
        c_layout.setSpacing(10)

        # Cabeçalho: Título + Badge de versão
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        lbl_icon = QLabel()
        lbl_icon.setPixmap(create_reddit_vector_icon("rocket", size=24).pixmap(24, 24))
        header_row.addWidget(lbl_icon)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel(f"Nova Versão Disponível: {self.info.tag_name}")
        lbl_title.setObjectName("UpdateTitle")
        title_col.addWidget(lbl_title)

        lbl_sub = QLabel(
            f"Versão atual: v{APP_VERSION} • Lançada em: {self.info.published_at or 'Recente'}"
        )
        lbl_sub.setObjectName("UpdateSubtitle")
        title_col.addWidget(lbl_sub)
        header_row.addLayout(title_col, 1)
        c_layout.addLayout(header_row)

        # Badge do sistema detectado (.rpm, .deb, .appimage)
        fmt_meta = FORMAT_METADATA.get(self.info.detected_format, FORMAT_METADATA["generic"])
        badge_row = QHBoxLayout()
        lbl_badge = QLabel(fmt_meta["badge"])
        lbl_badge.setProperty("class", "FormatBadge")
        badge_row.addWidget(lbl_badge)
        badge_row.addStretch(1)
        c_layout.addLayout(badge_row)

        # Notas da versão (Release Notes / Changelog)
        lbl_notes_hdr = QLabel("O que mudou nesta atualização:")
        lbl_notes_hdr.setProperty("role", "caption")
        c_layout.addWidget(lbl_notes_hdr)

        self.notes_browser = QTextBrowser()
        self.notes_browser.setObjectName("UpdateNotesBrowser")
        self.notes_browser.setOpenExternalLinks(True)
        self.notes_browser.setMarkdown(self.info.body)
        self.notes_browser.setFixedHeight(160)
        c_layout.addWidget(self.notes_browser)

        # Botão principal de download (Baseado no formato preferencial detectado)
        primary_asset = self.info.get_asset_for_format(self.info.detected_format)
        if primary_asset:
            size_txt = f" ({primary_asset.size_formatted})" if primary_asset.size_formatted else ""
            btn_primary_txt = f" Baixar {primary_asset.name}{size_txt}"
            target_url = primary_asset.download_url
        else:
            btn_primary_txt = f" Ver Pacotes e Baixar {self.info.tag_name}"
            target_url = self.info.html_url

        self.btn_download_primary = QPushButton(btn_primary_txt)
        self.btn_download_primary.setProperty("class", "UpdateActionBtn")
        self.btn_download_primary.setIcon(create_reddit_vector_icon("download", size=16))
        self.btn_download_primary.setIconSize(QSize(16, 16))
        self.btn_download_primary.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_download_primary.clicked.connect(lambda: self._open_download(target_url))
        c_layout.addWidget(self.btn_download_primary)

        # Dica de comando para instalação rápida no terminal
        if fmt_meta.get("cmd"):
            lbl_cmd = QLabel(f"Instalação: {fmt_meta['cmd']}")
            lbl_cmd.setObjectName("FormatCmdHint")
            lbl_cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            c_layout.addWidget(lbl_cmd)

        main_layout.addWidget(card)

        # Outros formatos disponíveis (.rpm, .deb, .appimage)
        other_formats = [f for f in ["appimage", "deb", "rpm"] if f != self.info.detected_format]
        other_assets = [
            (f, self.info.get_asset_for_format(f))
            for f in other_formats
            if self.info.get_asset_for_format(f) is not None
        ]

        if other_assets:
            others_row = QHBoxLayout()
            lbl_others = QLabel("Outros formatos:")
            lbl_others.setProperty("role", "caption")
            others_row.addWidget(lbl_others)

            for fmt_key, asset in other_assets:
                if not asset:
                    continue
                label_fmt = FORMAT_METADATA.get(fmt_key, {}).get("ext", fmt_key)
                btn_fmt = UiSkillsButton(f"Baixar {label_fmt}")
                btn_fmt.setProperty("class", "UpdateSecondaryBtn")
                btn_fmt.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn_fmt.clicked.connect(lambda checked=False, u=asset.download_url: self._open_download(u))
                others_row.addWidget(btn_fmt)

            others_row.addStretch(1)
            main_layout.addLayout(others_row)

        # Rodapé: Ver no GitHub + Fechar
        footer_row = QHBoxLayout()
        btn_github = UiSkillsButton("Ver no GitHub ↗")
        btn_github.setProperty("class", "UpdateSecondaryBtn")
        btn_github.clicked.connect(lambda: self._open_download(self.info.html_url))
        footer_row.addWidget(btn_github)

        footer_row.addStretch(1)

        btn_close = QPushButton("✕ Fechar")
        btn_close.setProperty("class", "UpdateSecondaryBtn")
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.clicked.connect(self.close)
        footer_row.addWidget(btn_close)

        main_layout.addLayout(footer_row)

    def _open_download(self, url: str) -> None:
        """Abre o link de download direto ou da release no navegador padrão."""
        if url:
            QDesktopServices.openUrl(QUrl(url))
