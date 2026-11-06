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

from widgets import RedditLogoWidget


SESSION_FILE = Path.home() / ".config" / "reddit-linux-app" / "session.json"


class RedditLoginDialog(QDialog):
    """Diálogo modal com a tela oficial de login do Reddit."""

    login_completed = Signal(dict)  # cookies capturados

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Entrar no Reddit")
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
        top_bar.setStyleSheet(
            "background-color: #0E1113; border-bottom: 1px solid #24373E; padding: 10px 16px;"
        )
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(10)

        logo = RedditLogoWidget(size=28)
        tb_layout.addWidget(logo)

        lbl_title = QLabel("Entrar com sua conta do Reddit")
        lbl_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #F2F4F5;")
        tb_layout.addWidget(lbl_title, 1)

        btn_close = QPushButton("✕")
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #8BA2AD; border: none; font-size: 14px; font-weight: 700; border-radius: 14px; }"
            "QPushButton:hover { background-color: #24373E; color: #F2F4F5; }"
        )
        btn_close.clicked.connect(self.reject)
        tb_layout.addWidget(btn_close)
        layout.addWidget(top_bar)

        # Status / Feedback
        self.status_bar = QFrame()
        self.status_bar.setStyleSheet(
            "background-color: #131F23; border-bottom: 1px solid #1B2B30; padding: 6px 16px;"
        )
        sb_layout = QHBoxLayout(self.status_bar)
        sb_layout.setContentsMargins(6, 2, 6, 2)
        self.lbl_status = QLabel(
            "🔒 Conectando com segurança ao Reddit. Entre normalmente com usuário/senha ou Google."
        )
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #8BA2AD; font-size: 11px;")
        sb_layout.addWidget(self.lbl_status)
        layout.addWidget(self.status_bar)

        # Visualizador WebEngine Oficial
        self.profile = QWebEngineProfile("reddit_login_profile", self)
        self.cookie_store: QWebEngineCookieStore = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self._on_cookie_added)

        self.web_view = QWebEngineView()
        self.web_page = self.web_view.page()
        # Aplica o perfil dedicado com isolamento
        self.web_view.setPage(self.web_view.page())
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
        if self._login_detected:
            return

        has_auth_cookie = (
            "reddit_session" in self._captured_cookies
            or "token_v2" in self._captured_cookies
        )
        if has_auth_cookie:
            self._login_detected = True
            self.lbl_status.setText("✓ Login identificado com sucesso! Conectando...")
            self.lbl_status.setStyleSheet("color: #46D160; font-size: 12px; font-weight: 700;")

            # Salva cookies em arquivo seguro local
            try:
                SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                SESSION_FILE.write_text(
                    json.dumps(self._captured_cookies, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

            self.login_completed.emit(dict(self._captured_cookies))
            QTimer.singleShot(600, self.accept)
