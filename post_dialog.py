"""
Diálogo nativo para criação e envio de postagens para o Reddit.
Permite postar texto (Markdown) ou links em qualquer subreddit ou perfil.
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from reddit_api import RedditClient, is_valid_subreddit_name
from widgets import RedditLogoWidget


class CreatePostDialog(QDialog):
    """Diálogo modal para compor e enviar uma nova postagem no Reddit."""

    post_created = Signal(str, str)  # (subreddit, post_url)

    def __init__(
        self,
        client: RedditClient,
        initial_subreddit: str = "linux",
        available_subreddits: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Criar Nova Publicação — Reddit Linux Desktop")
        self.setFixedSize(580, 560)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        clean_initial = (
            initial_subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        )
        if clean_initial.lower() in ("popular", "all", "home"):
            clean_initial = "linux"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        # 1. Cabeçalho
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        logo = RedditLogoWidget(size=32)
        header_row.addWidget(logo)

        lbl_header = QLabel("Criar Publicação")
        lbl_header.setStyleSheet("font-size: 18px; font-weight: 800; color: #F2F4F5;")
        header_row.addWidget(lbl_header, 1)

        btn_close = QPushButton("✕")
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #8BA2AD; border: none; font-size: 14px; font-weight: 700; border-radius: 14px; }"
            "QPushButton:hover { background-color: #24373E; color: #F2F4F5; }"
        )
        btn_close.clicked.connect(self.reject)
        header_row.addWidget(btn_close)
        layout.addLayout(header_row)

        # 2. Escolha da Comunidade (Subreddit)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(10)

        lbl_sub = QLabel("Comunidade:")
        lbl_sub.setStyleSheet("font-weight: 700; font-size: 13px; color: #D7DADC;")
        sub_row.addWidget(lbl_sub)

        self.sub_combo = QComboBox()
        self.sub_combo.setEditable(True)
        self.sub_combo.setMinimumWidth(260)
        self.sub_combo.setStyleSheet(
            "QComboBox { background-color: #131F23; border: 1px solid #24373E; border-radius: 8px; padding: 6px 12px; color: #F2F4F5; font-size: 13px; font-weight: 600; }"
            "QComboBox::drop-down { border: none; }"
        )

        subs = list(available_subreddits or ["linux", "brasil", "technology", "programming"])
        if clean_initial not in subs:
            subs.insert(0, clean_initial)

        for s in subs:
            if s.lower() not in ("popular", "all", "home"):
                self.sub_combo.addItem(f"r/{s}", s)

        # Seleciona o inicial
        idx = self.sub_combo.findData(clean_initial)
        if idx >= 0:
            self.sub_combo.setCurrentIndex(idx)
        else:
            self.sub_combo.setEditText(f"r/{clean_initial}")

        sub_row.addWidget(self.sub_combo, 1)
        layout.addLayout(sub_row)

        # 3. Tipo de Publicação (Texto vs Link)
        type_row = QHBoxLayout()
        type_row.setSpacing(12)

        self.btn_type_text = QPushButton("📝  Texto (Post)")
        self.btn_type_text.setProperty("class", "PillButtonAccent")
        self.btn_type_text.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_type_text.clicked.connect(lambda: self._set_post_type("self"))
        type_row.addWidget(self.btn_type_text)

        self.btn_type_link = QPushButton("🔗  Link / URL")
        self.btn_type_link.setProperty("class", "PillButton")
        self.btn_type_link.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_type_link.clicked.connect(lambda: self._set_post_type("link"))
        type_row.addWidget(self.btn_type_link)

        type_row.addStretch(1)
        layout.addLayout(type_row)
        self._current_type: str = "self"

        # 4. Campo de Título
        title_box = QVBoxLayout()
        title_box.setSpacing(4)

        t_header = QHBoxLayout()
        lbl_title_tag = QLabel("Título *")
        lbl_title_tag.setStyleSheet("font-weight: 700; font-size: 13px; color: #D7DADC;")
        t_header.addWidget(lbl_title_tag)
        t_header.addStretch(1)

        self.lbl_char_count = QLabel("0/300")
        self.lbl_char_count.setStyleSheet("color: #8BA2AD; font-size: 11px;")
        t_header.addWidget(self.lbl_char_count)
        title_box.addLayout(t_header)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Título da sua publicação...")
        self.title_input.setMaxLength(300)
        self.title_input.setStyleSheet(
            "QLineEdit { background-color: #0B1416; border: 1px solid #24373E; border-radius: 8px; padding: 10px 12px; color: #F2F4F5; font-size: 14px; }"
            "QLineEdit:focus { border: 1px solid #FF4500; }"
        )
        self.title_input.textChanged.connect(self._on_title_changed)
        title_box.addWidget(self.title_input)
        layout.addLayout(title_box)

        # 5. Conteúdo (Texto Markdown ou Link)
        self.content_container = QWidget()
        c_layout = QVBoxLayout(self.content_container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(6)

        self.lbl_content_desc = QLabel("Conteúdo (opcional):")
        self.lbl_content_desc.setStyleSheet("font-weight: 700; font-size: 13px; color: #D7DADC;")
        c_layout.addWidget(self.lbl_content_desc)

        self.body_text = QTextEdit()
        self.body_text.setPlaceholderText("Escreva sua postagem aqui... Suporta formatação em Markdown.")
        self.body_text.setStyleSheet(
            "QTextEdit { background-color: #0B1416; border: 1px solid #24373E; border-radius: 8px; padding: 10px 12px; color: #F2F4F5; font-size: 13px; line-height: 1.45; }"
            "QTextEdit:focus { border: 1px solid #FF4500; }"
        )
        c_layout.addWidget(self.body_text)

        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("https://exemplo.com/artigo-ou-noticia")
        self.link_input.setStyleSheet(
            "QLineEdit { background-color: #0B1416; border: 1px solid #24373E; border-radius: 8px; padding: 10px 12px; color: #F2F4F5; font-size: 13px; }"
            "QLineEdit:focus { border: 1px solid #FF4500; }"
        )
        self.link_input.setVisible(False)
        c_layout.addWidget(self.link_input)

        layout.addWidget(self.content_container, 1)

        # 6. Status e Mensagens de Feedback
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.lbl_status.setVisible(False)
        layout.addWidget(self.lbl_status)

        # 7. Barra Inferior de Ações
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(12)

        btn_bar.addStretch(1)

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setProperty("class", "PillButton")
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_cancel)

        self.btn_submit = QPushButton("Publicar no Reddit")
        self.btn_submit.setProperty("class", "PillButtonAccent")
        self.btn_submit.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_submit.clicked.connect(self._submit)
        btn_bar.addWidget(self.btn_submit)

        layout.addLayout(btn_bar)

    def _set_post_type(self, ptype: str) -> None:
        self._current_type = ptype
        if ptype == "self":
            self.btn_type_text.setProperty("class", "PillButtonAccent")
            self.btn_type_link.setProperty("class", "PillButton")
            self.lbl_content_desc.setText("Conteúdo (opcional):")
            self.body_text.setVisible(True)
            self.link_input.setVisible(False)
        else:
            self.btn_type_text.setProperty("class", "PillButton")
            self.btn_type_link.setProperty("class", "PillButtonAccent")
            self.lbl_content_desc.setText("URL do Link *:")
            self.body_text.setVisible(False)
            self.link_input.setVisible(True)

        self.btn_type_text.style().unpolish(self.btn_type_text)
        self.btn_type_text.style().polish(self.btn_type_text)
        self.btn_type_link.style().unpolish(self.btn_type_link)
        self.btn_type_link.style().polish(self.btn_type_link)

    def _on_title_changed(self, text: str) -> None:
        count = len(text)
        self.lbl_char_count.setText(f"{count}/300")
        if count >= 300:
            self.lbl_char_count.setStyleSheet("color: #FF585B; font-weight: 700; font-size: 11px;")
        else:
            self.lbl_char_count.setStyleSheet("color: #8BA2AD; font-size: 11px;")

    def _submit(self) -> None:
        # Extrai subreddit
        raw_sub = self.sub_combo.currentText().strip().lstrip("/").removeprefix("r/").strip("/")
        if not raw_sub or not is_valid_subreddit_name(raw_sub):
            self.lbl_status.setText("⚠️ Informe uma comunidade válida (apenas letras, números e _).")
            self.lbl_status.setStyleSheet("color: #FF585B; font-size: 12px; font-weight: 700;")
            self.lbl_status.setVisible(True)
            return

        title = self.title_input.text().strip()
        if not title:
            self.lbl_status.setText("⚠️ O título da publicação é obrigatório.")
            self.lbl_status.setStyleSheet("color: #FF585B; font-size: 12px; font-weight: 700;")
            self.lbl_status.setVisible(True)
            self.title_input.setFocus()
            return

        content = ""
        if self._current_type == "self":
            content = self.body_text.toPlainText().strip()
        else:
            content = self.link_input.text().strip()
            if not content.startswith(("http://", "https://")):
                self.lbl_status.setText("⚠️ Informe um link válido começando com https:// ou http://")
                self.lbl_status.setStyleSheet("color: #FF585B; font-size: 12px; font-weight: 700;")
                self.lbl_status.setVisible(True)
                self.link_input.setFocus()
                return

        self.btn_submit.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText("⏳ Enviando publicação para o Reddit...")
        self.lbl_status.setStyleSheet("color: #FF8555; font-size: 12px; font-weight: 700;")
        self.lbl_status.setVisible(True)

        def _worker():
            return self.client.submit_post(
                subreddit=raw_sub,
                title=title,
                kind=self._current_type,
                text_or_url=content,
            )

        import threading

        def _run():
            ok, msg, post_url = _worker()

            def _done():
                if ok:
                    self.lbl_status.setText("✓ Publicado com sucesso no Reddit!")
                    self.lbl_status.setStyleSheet("color: #46D160; font-size: 12px; font-weight: 700;")
                    self.post_created.emit(raw_sub, post_url or "")
                    QTimer.singleShot(700, self.accept)
                else:
                    self.lbl_status.setText(f"⚠️ {msg}")
                    self.lbl_status.setStyleSheet("color: #FF585B; font-size: 12px; font-weight: 700;")
                    self.btn_submit.setEnabled(True)
                    self.btn_cancel.setEnabled(True)

            QTimer.singleShot(0, _done)

        threading.Thread(target=_run, daemon=True).start()
