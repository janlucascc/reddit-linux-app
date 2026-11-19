"""
Janela de Login Oficial do Reddit via WebEngine nativo.
Permite login direto e familiar (usuário/senha, Google ou Apple)
sem exigir criação de apps de desenvolvedor.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWebEngineCore import (
    QWebEngineCookieStore,
    QWebEnginePage,
    QWebEngineProfile,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from widgets import RedditLogoWidget


SESSION_FILE = Path.home() / ".config" / "reddit-linux-app" / "session.json"


class RedditLoginDialog(QDialog):
    """Diálogo modal com a tela oficial de login do Reddit."""

    login_completed = Signal(dict)  # cookies capturados

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Entrar no Reddit")
        self.setAccessibleName("Entrar na conta do Reddit")
        self.resize(520, 720)
        self.setMinimumSize(440, 580)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._captured_cookies: Dict[str, str] = {}
        self._login_detected: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Barra Superior
        top_bar = QFrame()
        top_bar.setObjectName("TopHeader")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(16, 10, 16, 10)
        tb_layout.setSpacing(10)

        logo = RedditLogoWidget(size=28)
        tb_layout.addWidget(logo)

        lbl_title = QLabel("Entrar com sua conta do Reddit")
        lbl_title.setProperty("role", "subtitle")
        tb_layout.addWidget(lbl_title, 1)

        btn_close = QPushButton("✕")
        btn_close.setProperty("class", "IconButton")
        btn_close.setAccessibleName("Fechar janela de login")
        btn_close.setToolTip("Fechar (Esc)")
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.setFixedSize(28, 28)
        btn_close.clicked.connect(self.reject)
        tb_layout.addWidget(btn_close)
        layout.addWidget(top_bar)

        # Status / Feedback
        self.status_bar = QFrame()
        self.status_bar.setObjectName("MediaCodecFallbackBanner")
        sb_layout = QHBoxLayout(self.status_bar)
        sb_layout.setContentsMargins(16, 6, 16, 6)
        self.lbl_status = QLabel(
            "Conectando com segurança ao Reddit. Entre normalmente com usuário/senha ou Google."
        )
        self.lbl_status.setAccessibleName("Status da conexão de login")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setProperty("role", "caption")
        sb_layout.addWidget(self.lbl_status)
        layout.addWidget(self.status_bar)

        # Visualizador WebEngine Oficial com perfil dedicado
        self.profile = QWebEngineProfile("reddit_login_profile", self)
        self.cookie_store: QWebEngineCookieStore = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)

        self.web_view = QWebEngineView()
        self.web_view.setAccessibleName("Página oficial de login do Reddit")
        self.web_page = QWebEnginePage(self.profile, self.web_view)
        self.web_view.setPage(self.web_page)
        layout.addWidget(self.web_view, 1)

        self.web_view.urlChanged.connect(self._on_url_changed)
        self.web_view.load(QUrl("https://www.reddit.com/login/"))

    def _on_cookie_added(self, cookie) -> None:
        try:
            name = bytearray(cookie.name()).decode("utf-8", errors="ignore")
            value = bytearray(cookie.value()).decode("utf-8", errors="ignore")
            self._captured_cookies[name] = value

            # Se recebeu os cookies que comprovam autenticação de usuário
            if ("reddit_session" in self._captured_cookies or "token_v2" in self._captured_cookies) and not self._login_detected:
                # Verifica se não é sessão anônima
                if name == "token_v2" or name == "reddit_session":
                    QTimer.singleShot(1200, self._check_successful_login)
        except Exception:
            pass

    def _on_url_changed(self, url: QUrl) -> None:
        url_str = url.toString()
        # Ao concluir o login, o Reddit redireciona para a home ou página anterior
        if "reddit.com" in url_str and "/login" not in url_str and "/register" not in url_str:
            QTimer.singleShot(1000, self._check_successful_login)

    def _check_successful_login(self) -> None:
        if not isValid(self) or self._login_detected:
            return

        has_auth_cookie = (
            "reddit_session" in self._captured_cookies
            or "token_v2" in self._captured_cookies
        )
        if has_auth_cookie:
            self._login_detected = True
            if isValid(self.lbl_status):
                self.lbl_status.setText("✓ Login identificado com sucesso! Conectando...")
                self.lbl_status.setProperty("role", "subtitle")

            # Salva cookies em arquivo seguro local
            try:
                SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                SESSION_FILE.write_text(
                    json.dumps(self._captured_cookies, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

            self.login_completed.emit(dict(self._captured_cookies))
            QTimer.singleShot(600, lambda: self.accept() if isValid(self) else None)
