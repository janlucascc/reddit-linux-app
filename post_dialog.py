"""
Diálogo nativo para criação e envio de postagens para o Reddit (Sprint 5).
Implementa:
- Modal de 680x640px sem estilos hexadecimais inline (CR-03)
- 4 Abas de publicação: Post (Markdown), Imagens & Vídeo, Link / URL, Enquete
- Barra de ferramentas Markdown + Alternância de Pré-visualização (Preview)
- Contador dinâmico de caracteres (0/300) com estados normal, warn (280-299) e limit (300)
- Pílulas de tags semânticas (+ OC, + Spoiler, + 18+ NSFW)
"""

from __future__ import annotations
import threading
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from reddit_api import RedditClient, is_valid_subreddit_name
from widgets import RedditLogoWidget, UiSkillsButton


class CreatePostDialog(QDialog):
    """Diálogo modal completo para compor e publicar uma nova postagem no Reddit."""

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
        self.setFixedSize(680, 640)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._current_tab: str = "self"  # "self" | "media" | "link" | "poll"
        self._preview_active: bool = False
        self._tag_oc: bool = False
        self._tag_spoiler: bool = False
        self._tag_nsfw: bool = False

        clean_initial = (
            initial_subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        )
        if clean_initial.lower() in ("popular", "all", "home"):
            clean_initial = "linux"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 1. Cabeçalho
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        logo = RedditLogoWidget(size=32)
        header_row.addWidget(logo)

        lbl_header = QLabel("Criar Publicação")
        lbl_header.setProperty("role", "title")
        header_row.addWidget(lbl_header, 1)

        btn_close = UiSkillsButton("✕")
        btn_close.setProperty("class", "PillButton")
        btn_close.setFixedSize(32, 32)
        btn_close.clicked.connect(self.reject)
        header_row.addWidget(btn_close)
        layout.addLayout(header_row)

        # 2. Seletor de Comunidade (Subreddit)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(10)

        lbl_sub = QLabel("Escolha uma comunidade:")
        lbl_sub.setProperty("role", "subtitle")
        sub_row.addWidget(lbl_sub)

        self.sub_combo = QComboBox()
        self.sub_combo.setEditable(True)
        self.sub_combo.setMinimumWidth(280)

        subs = list(
            available_subreddits
            or ["linux", "brasil", "technology", "programming", "opensource"]
        )
        if clean_initial not in subs:
            subs.insert(0, clean_initial)

        for s in subs:
            if s.lower() not in ("popular", "all", "home"):
                self.sub_combo.addItem(f"r/{s}", s)

        idx = self.sub_combo.findData(clean_initial)
        if idx >= 0:
            self.sub_combo.setCurrentIndex(idx)
        else:
            self.sub_combo.setEditText(f"r/{clean_initial}")

        self.sub_combo.currentTextChanged.connect(self._validate_form)
        sub_row.addWidget(self.sub_combo, 1)
        layout.addLayout(sub_row)

        # 3. 4 Abas de Tipo de Publicação
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(8)

        self.btn_tab_self = UiSkillsButton("📝 Post (Markdown)")
        self.btn_tab_self.setProperty("class", "SortButton")
        self.btn_tab_self.clicked.connect(lambda: self._switch_tab("self"))
        tabs_row.addWidget(self.btn_tab_self)

        self.btn_tab_media = UiSkillsButton("🖼️ Imagens & Vídeo")
        self.btn_tab_media.setProperty("class", "SortButton")
        self.btn_tab_media.clicked.connect(lambda: self._switch_tab("media"))
        tabs_row.addWidget(self.btn_tab_media)

        self.btn_tab_link = UiSkillsButton("🔗 Link / URL")
        self.btn_tab_link.setProperty("class", "SortButton")
        self.btn_tab_link.clicked.connect(lambda: self._switch_tab("link"))
        tabs_row.addWidget(self.btn_tab_link)

        self.btn_tab_poll = UiSkillsButton("📊 Enquete")
        self.btn_tab_poll.setProperty("class", "SortButton")
        self.btn_tab_poll.clicked.connect(lambda: self._switch_tab("poll"))
        tabs_row.addWidget(self.btn_tab_poll)

        tabs_row.addStretch(1)
        layout.addLayout(tabs_row)

        # 4. Campo de Título com Contador Dinâmico 0/300 (normal | warn | limit)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)

        t_header = QHBoxLayout()
        lbl_title_tag = QLabel("Título da publicação *")
        lbl_title_tag.setProperty("role", "subtitle")
        t_header.addWidget(lbl_title_tag)
        t_header.addStretch(1)

        self.lbl_char_count = QLabel("0/300")
        self.lbl_char_count.setProperty("role", "char_counter")
        self.lbl_char_count.setProperty("state", "normal")
        t_header.addWidget(self.lbl_char_count)
        title_box.addLayout(t_header)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Um título claro e interessante...")
        self.title_input.setMaxLength(300)
        self.title_input.textChanged.connect(self._on_title_changed)
        title_box.addWidget(self.title_input)
        layout.addLayout(title_box)

        # 5. Área de Conteúdo Empilhada para as 4 Abas
        self.tab_stack = QStackedWidget()

        # --- Aba 0: Post (Markdown) com Toolbar + Preview ---
        page_self = QWidget()
        ps_layout = QVBoxLayout(page_self)
        ps_layout.setContentsMargins(0, 0, 0, 0)
        ps_layout.setSpacing(6)

        md_toolbar = QHBoxLayout()
        md_toolbar.setSpacing(6)

        for label_txt, prefix, suffix, tip in (
            ("B", "**", "**", "Negrito (**texto**)"),
            ("I", "*", "*", "Itálico (*texto*)"),
            ("S", "~~", "~~", "Riscado (~~texto~~)"),
            ("</> Código", "`", "`", "Código inline (`código`)"),
            ("❝ Citação", "\n> ", "", "Bloco de citação (> texto)"),
            ("• Lista", "\n- ", "", "Lista de itens (- item)"),
        ):
            btn_fmt = UiSkillsButton(label_txt)
            btn_fmt.setProperty("class", "PillButton")
            btn_fmt.setFixedHeight(26)
            btn_fmt.setToolTip(tip)
            btn_fmt.clicked.connect(
                lambda _checked=False, p=prefix, s=suffix: self._insert_markdown(p, s)
            )
            md_toolbar.addWidget(btn_fmt)

        md_toolbar.addStretch(1)

        self.btn_toggle_preview = UiSkillsButton("👁️ Pré-visualizar")
        self.btn_toggle_preview.setProperty("class", "TranslateButton")
        self.btn_toggle_preview.setFixedHeight(26)
        self.btn_toggle_preview.clicked.connect(self._toggle_markdown_preview)
        md_toolbar.addWidget(self.btn_toggle_preview)

        ps_layout.addLayout(md_toolbar)

        self.editor_stack = QStackedWidget()
        self.body_text = QTextEdit()
        self.body_text.setPlaceholderText(
            "Escreva sua postagem aqui (opcional)... Suporta formatação completa em Markdown."
        )
        self.editor_stack.addWidget(self.body_text)

        self.preview_browser = QTextBrowser()
        self.preview_browser.setOpenExternalLinks(True)
        self.editor_stack.addWidget(self.preview_browser)

        ps_layout.addWidget(self.editor_stack, 1)
        self.tab_stack.addWidget(page_self)

        # --- Aba 1: Imagens & Vídeo ---
        page_media = QWidget()
        pm_layout = QVBoxLayout(page_media)
        pm_layout.setContentsMargins(0, 8, 0, 0)
        pm_layout.setSpacing(8)

        lbl_media_title = QLabel("URL Direta da Imagem ou Vídeo (i.redd.it, imgur, mp4, etc.) *:")
        lbl_media_title.setProperty("role", "subtitle")
        pm_layout.addWidget(lbl_media_title)

        self.media_url_input = QLineEdit()
        self.media_url_input.setPlaceholderText(
            "https://i.imgur.com/exemplo.png ou https://exemplo.com/video.mp4"
        )
        self.media_url_input.textChanged.connect(self._validate_form)
        pm_layout.addWidget(self.media_url_input)

        lbl_media_hint = QLabel(
            "💡 O Reddit criará automaticamente o player ou card de imagem de alta resolução a partir do link de mídia."
        )
        lbl_media_hint.setProperty("role", "caption")
        lbl_media_hint.setWordWrap(True)
        pm_layout.addWidget(lbl_media_hint)
        pm_layout.addStretch(1)
        self.tab_stack.addWidget(page_media)

        # --- Aba 2: Link / URL ---
        page_link = QWidget()
        pl_layout = QVBoxLayout(page_link)
        pl_layout.setContentsMargins(0, 8, 0, 0)
        pl_layout.setSpacing(8)

        lbl_link_title = QLabel("URL do Artigo, Repositório ou Notícia *:")
        lbl_link_title.setProperty("role", "subtitle")
        pl_layout.addWidget(lbl_link_title)

        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("https://exemplo.com/noticia-ou-projeto")
        self.link_input.textChanged.connect(self._validate_form)
        pl_layout.addWidget(self.link_input)
        pl_layout.addStretch(1)
        self.tab_stack.addWidget(page_link)

        # --- Aba 3: Enquete ---
        page_poll = QWidget()
        pp_layout = QVBoxLayout(page_poll)
        pp_layout.setContentsMargins(0, 4, 0, 0)
        pp_layout.setSpacing(6)

        lbl_poll_desc = QLabel("Contexto da Enquete (opcional):")
        lbl_poll_desc.setProperty("role", "subtitle")
        pp_layout.addWidget(lbl_poll_desc)

        self.poll_context_input = QLineEdit()
        self.poll_context_input.setPlaceholderText(
            "Descreva brevemente a pergunta da sua enquete..."
        )
        pp_layout.addWidget(self.poll_context_input)

        lbl_opts = QLabel("Opções de Voto (mínimo 2 opções):")
        lbl_opts.setProperty("role", "subtitle")
        pp_layout.addWidget(lbl_opts)

        self.poll_option_inputs: List[QLineEdit] = []
        for idx in range(1, 5):
            opt_edit = QLineEdit()
            opt_edit.setPlaceholderText(
                f"Opção {idx}" + (" *" if idx <= 2 else " (opcional)")
            )
            opt_edit.textChanged.connect(self._validate_form)
            self.poll_option_inputs.append(opt_edit)
            pp_layout.addWidget(opt_edit)

        pp_layout.addStretch(1)
        self.tab_stack.addWidget(page_poll)

        layout.addWidget(self.tab_stack, 1)

        # 6. Pílulas de Tags Semânticas (+ OC, + Spoiler, + 18+ NSFW)
        tags_row = QHBoxLayout()
        tags_row.setSpacing(8)

        lbl_tags = QLabel("Tags:")
        lbl_tags.setProperty("role", "caption")
        tags_row.addWidget(lbl_tags)

        self.btn_tag_oc = UiSkillsButton("+ OC (Conteúdo Original)")
        self.btn_tag_oc.setProperty("class", "PillButton")
        self.btn_tag_oc.clicked.connect(self._toggle_tag_oc)
        tags_row.addWidget(self.btn_tag_oc)

        self.btn_tag_spoiler = UiSkillsButton("+ Spoiler")
        self.btn_tag_spoiler.setProperty("class", "PillButton")
        self.btn_tag_spoiler.clicked.connect(self._toggle_tag_spoiler)
        tags_row.addWidget(self.btn_tag_spoiler)

        self.btn_tag_nsfw = UiSkillsButton("+ 18+ (NSFW)")
        self.btn_tag_nsfw.setProperty("class", "PillButton")
        self.btn_tag_nsfw.clicked.connect(self._toggle_tag_nsfw)
        tags_row.addWidget(self.btn_tag_nsfw)

        tags_row.addStretch(1)
        layout.addLayout(tags_row)

        # 7. Status e Mensagens de Feedback
        self.lbl_status = QLabel("")
        self.lbl_status.setProperty("role", "status_ok")
        self.lbl_status.setVisible(False)
        layout.addWidget(self.lbl_status)

        # 8. Barra Inferior de Ações
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(12)
        btn_bar.addStretch(1)

        self.btn_cancel = UiSkillsButton("Cancelar")
        self.btn_cancel.setProperty("class", "PillButton")
        self.btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_cancel)

        self.btn_submit = UiSkillsButton("Publicar no Reddit")
        self.btn_submit.setProperty("class", "PillButtonAccent")
        self.btn_submit.clicked.connect(self._submit)
        btn_bar.addWidget(self.btn_submit)

        layout.addLayout(btn_bar)

        self._switch_tab("self")
        self._validate_form()

    def _switch_tab(self, tab_key: str) -> None:
        self._current_tab = tab_key
        mapping = {
            "self": (0, self.btn_tab_self),
            "media": (1, self.btn_tab_media),
            "link": (2, self.btn_tab_link),
            "poll": (3, self.btn_tab_poll),
        }
        idx, _ = mapping.get(tab_key, (0, self.btn_tab_self))
        self.tab_stack.setCurrentIndex(idx)

        for key, (_, btn) in mapping.items():
            btn.setProperty("active", "true" if key == tab_key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._validate_form()

    def _insert_markdown(self, prefix: str, suffix: str) -> None:
        if self._preview_active:
            self._toggle_markdown_preview()
        cursor = self.body_text.textCursor()
        selected = cursor.selectedText() or "texto"
        cursor.insertText(f"{prefix}{selected}{suffix}")
        self.body_text.setFocus()

    def _toggle_markdown_preview(self) -> None:
        self._preview_active = not self._preview_active
        if self._preview_active:
            md_content = self.body_text.toPlainText().strip()
            self.preview_browser.setMarkdown(
                md_content or "*Nenhum conteúdo para pré-visualizar.*"
            )
            self.editor_stack.setCurrentIndex(1)
            self.btn_toggle_preview.setText("✏️ Editar Markdown")
            self.btn_toggle_preview.setProperty("translated", "true")
        else:
            self.editor_stack.setCurrentIndex(0)
            self.btn_toggle_preview.setText("👁️ Pré-visualizar")
            self.btn_toggle_preview.setProperty("translated", "false")
        self.btn_toggle_preview.style().unpolish(self.btn_toggle_preview)
        self.btn_toggle_preview.style().polish(self.btn_toggle_preview)

    def _toggle_tag_oc(self) -> None:
        self._tag_oc = not self._tag_oc
        self._sync_tag_pill(
            self.btn_tag_oc, self._tag_oc, "✓ OC Ativo", "+ OC (Conteúdo Original)"
        )

    def _toggle_tag_spoiler(self) -> None:
        self._tag_spoiler = not self._tag_spoiler
        self._sync_tag_pill(
            self.btn_tag_spoiler, self._tag_spoiler, "✓ Spoiler Ativo", "+ Spoiler"
        )

    def _toggle_tag_nsfw(self) -> None:
        self._tag_nsfw = not self._tag_nsfw
        self._sync_tag_pill(
            self.btn_tag_nsfw, self._tag_nsfw, "✓ 18+ (NSFW)", "+ 18+ (NSFW)"
        )

    def _sync_tag_pill(
        self, btn: UiSkillsButton, active: bool, active_txt: str, idle_txt: str
    ) -> None:
        btn.setText(active_txt if active else idle_txt)
        btn.setProperty("class", "PillButtonAccent" if active else "PillButton")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _on_title_changed(self, text: str) -> None:
        count = len(text)
        self.lbl_char_count.setText(f"{count}/300")
        if count >= 300:
            state = "limit"
        elif count >= 280:
            state = "warn"
        else:
            state = "normal"
        self.lbl_char_count.setProperty("state", state)
        self.lbl_char_count.style().unpolish(self.lbl_char_count)
        self.lbl_char_count.style().polish(self.lbl_char_count)
        self._validate_form()

    def _validate_form(self) -> None:
        raw_sub = (
            self.sub_combo.currentText()
            .strip()
            .lstrip("/")
            .removeprefix("r/")
            .strip("/")
        )
        sub_ok = bool(raw_sub) and is_valid_subreddit_name(raw_sub)
        title_ok = bool(self.title_input.text().strip())

        extra_ok = True
        if self._current_tab == "media":
            u = self.media_url_input.text().strip()
            extra_ok = u.startswith(("http://", "https://"))
        elif self._current_tab == "link":
            u = self.link_input.text().strip()
            extra_ok = u.startswith(("http://", "https://"))
        elif self._current_tab == "poll":
            filled = [
                inp.text().strip()
                for inp in self.poll_option_inputs
                if inp.text().strip()
            ]
            extra_ok = len(filled) >= 2

        if isValid(self.btn_submit):
            self.btn_submit.setEnabled(sub_ok and title_ok and extra_ok)

    def _set_status(self, message: str, role: str = "status_ok") -> None:
        if not isValid(self.lbl_status):
            return
        self.lbl_status.setText(message)
        self.lbl_status.setProperty("role", role)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)
        self.lbl_status.setVisible(bool(message))

    def _submit(self) -> None:
        raw_sub = (
            self.sub_combo.currentText()
            .strip()
            .lstrip("/")
            .removeprefix("r/")
            .strip("/")
        )
        if not raw_sub or not is_valid_subreddit_name(raw_sub):
            self._set_status(
                "⚠️ Informe uma comunidade válida (apenas letras, números e _).",
                "status_error",
            )
            return

        title = self.title_input.text().strip()
        if not title:
            self._set_status(
                "⚠️ O título da publicação é obrigatório.", "status_error"
            )
            self.title_input.setFocus()
            return

        kind = "self"
        content = ""
        if self._current_tab == "self":
            kind = "self"
            content = self.body_text.toPlainText().strip()
        elif self._current_tab == "media":
            kind = "link"
            content = self.media_url_input.text().strip()
        elif self._current_tab == "link":
            kind = "link"
            content = self.link_input.text().strip()
        elif self._current_tab == "poll":
            kind = "self"
            ctx = self.poll_context_input.text().strip()
            opts = [
                inp.text().strip()
                for inp in self.poll_option_inputs
                if inp.text().strip()
            ]
            md_lines = []
            if ctx:
                md_lines.append(ctx)
                md_lines.append("")
            md_lines.append("### 📊 Opções da Enquete:")
            for idx, opt in enumerate(opts, start=1):
                md_lines.append(f"- **{idx}.** {opt}")
            md_lines.append("\n*(Vote e comente sua escolha abaixo!)*")
            content = "\n".join(md_lines)

        self.btn_submit.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._set_status("⏳ Enviando publicação para o Reddit...", "status_warn")

        def _worker():
            return self.client.submit_post(
                subreddit=raw_sub,
                title=title,
                kind=kind,
                text_or_url=content,
                nsfw=self._tag_nsfw,
                spoiler=self._tag_spoiler,
                oc=self._tag_oc,
            )

        def _run():
            ok, msg, post_url = _worker()

            def _done():
                if not isValid(self):
                    return
                if ok:
                    self._set_status(
                        "✓ Publicado com sucesso no Reddit!", "status_ok"
                    )
                    self.post_created.emit(raw_sub, post_url or "")
                    QTimer.singleShot(700, self.accept)
                else:
                    self._set_status(f"⚠️ {msg}", "status_error")
                    self.btn_submit.setEnabled(True)
                    self.btn_cancel.setEnabled(True)

            QTimer.singleShot(0, _done)

        threading.Thread(target=_run, daemon=True).start()
