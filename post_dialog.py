"""
Diálogo nativo para criação e envio de postagens para o Reddit (Sprint 5 & Fase 6).
Implementa:
- Modal acessível sem estilos hexadecimais inline (CR-03)
- Etapas visuais claras: 1. Tipo e Comunidade, 2. Conteúdo, 3. Opções e Tags, 4. Publicação
- Labels persistentes acima de cada campo + validação inline com foco no primeiro erro
- 4 Abas de publicação: Post (Markdown), Imagens e vídeo, Link/URL, Enquete (com adicionar/remover opções e duração)
- Confirmação de descarte ao cancelar com conteúdo digitado
- Preservação do conteúdo em caso de erro de rede; botão desabilitado apenas durante o envio
"""

from __future__ import annotations
import threading
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
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
    _submit_finished = Signal(str, bool, str, object)  # (subreddit, ok, msg, post_url)

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
        self.setAccessibleName("Criar nova publicação no Reddit")
        self.setMinimumSize(660, 650)
        self.resize(700, 680)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._submit_finished.connect(self._on_submit_finished)

        self._current_tab: str = "self"  # "self" | "media" | "link" | "poll"
        self._preview_active: bool = False
        self._is_submitting: bool = False
        self._tag_oc: bool = False
        self._tag_spoiler: bool = False
        self._tag_nsfw: bool = False

        clean_initial = (
            initial_subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        )
        if clean_initial.lower() in ("popular", "all", "home"):
            clean_initial = "linux"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        # Cabeçalho
        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        logo = RedditLogoWidget(size=30)
        header_row.addWidget(logo)

        lbl_header = QLabel("Criar Publicação")
        lbl_header.setProperty("role", "title")
        header_row.addWidget(lbl_header, 1)

        btn_close = UiSkillsButton("✕")
        btn_close.setProperty("class", "PillButton")
        btn_close.setAccessibleName("Fechar janela de criação de publicação")
        btn_close.setToolTip("Fechar (Esc)")
        btn_close.setFixedSize(32, 32)
        btn_close.clicked.connect(self._request_cancel)
        header_row.addWidget(btn_close)
        layout.addLayout(header_row)

        # Etapa 1 — Tipo de Post e Comunidade
        lbl_step1 = QLabel("1. COMUNIDADE E TIPO DE PUBLICAÇÃO")
        lbl_step1.setProperty("role", "form_step")
        layout.addWidget(lbl_step1)

        sub_block = QVBoxLayout()
        sub_block.setSpacing(3)
        lbl_sub = QLabel("Comunidade *")
        lbl_sub.setProperty("role", "field_label")
        sub_block.addWidget(lbl_sub)

        self.sub_combo = QComboBox()
        self.sub_combo.setEditable(True)
        self.sub_combo.setAccessibleName("Comunidade de destino")
        self.sub_combo.setAccessibleDescription(
            "Escolha ou digite o nome do subreddit onde a postagem será publicada"
        )
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

        self.sub_combo.currentTextChanged.connect(lambda _t: self._clear_field_error(self.lbl_sub_err))
        sub_block.addWidget(self.sub_combo)

        self.lbl_sub_err = QLabel("")
        self.lbl_sub_err.setProperty("role", "field_error")
        self.lbl_sub_err.setVisible(False)
        sub_block.addWidget(self.lbl_sub_err)
        layout.addLayout(sub_block)

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(8)

        self.btn_tab_self = UiSkillsButton("Post (Markdown)")
        self.btn_tab_self.setProperty("class", "SortButton")
        self.btn_tab_self.setAccessibleName("Tipo: Post de texto com Markdown")
        self.btn_tab_self.clicked.connect(lambda: self._switch_tab("self"))
        tabs_row.addWidget(self.btn_tab_self)

        self.btn_tab_media = UiSkillsButton("Imagens e vídeo")
        self.btn_tab_media.setProperty("class", "SortButton")
        self.btn_tab_media.setAccessibleName("Tipo: Imagens e vídeo")
        self.btn_tab_media.clicked.connect(lambda: self._switch_tab("media"))
        tabs_row.addWidget(self.btn_tab_media)

        self.btn_tab_link = UiSkillsButton("Link/URL")
        self.btn_tab_link.setProperty("class", "SortButton")
        self.btn_tab_link.setAccessibleName("Tipo: Link ou URL")
        self.btn_tab_link.clicked.connect(lambda: self._switch_tab("link"))
        tabs_row.addWidget(self.btn_tab_link)

        self.btn_tab_poll = UiSkillsButton("Enquete")
        self.btn_tab_poll.setProperty("class", "SortButton")
        self.btn_tab_poll.setAccessibleName("Tipo: Enquete")
        self.btn_tab_poll.clicked.connect(lambda: self._switch_tab("poll"))
        tabs_row.addWidget(self.btn_tab_poll)

        tabs_row.addStretch(1)
        layout.addLayout(tabs_row)

        # Etapa 2 — Conteúdo
        lbl_step2 = QLabel("2. CONTEÚDO")
        lbl_step2.setProperty("role", "form_step")
        layout.addWidget(lbl_step2)

        # Campo de Título com Contador Dinâmico 0/300 (normal | warn | limit)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)

        t_header = QHBoxLayout()
        lbl_title_tag = QLabel("Título *")
        lbl_title_tag.setProperty("role", "field_label")
        t_header.addWidget(lbl_title_tag)
        t_header.addStretch(1)

        self.lbl_char_count = QLabel("0/300")
        self.lbl_char_count.setProperty("role", "char_counter")
        self.lbl_char_count.setProperty("state", "normal")
        t_header.addWidget(self.lbl_char_count)
        title_box.addLayout(t_header)

        self.title_input = QLineEdit()
        self.title_input.setAccessibleName("Título da publicação")
        self.title_input.setAccessibleDescription("Campo obrigatório com até 300 caracteres")
        self.title_input.setPlaceholderText("Um título claro e descritivo…")
        self.title_input.setMaxLength(300)
        self.title_input.textChanged.connect(self._on_title_changed)
        title_box.addWidget(self.title_input)

        self.lbl_title_err = QLabel("")
        self.lbl_title_err.setProperty("role", "field_error")
        self.lbl_title_err.setVisible(False)
        title_box.addWidget(self.lbl_title_err)
        layout.addLayout(title_box)

        # Área de Conteúdo Empilhada para as 4 Abas
        self.tab_stack = QStackedWidget()

        # --- Aba 0: Post (Markdown) com Toolbar + Preview ---
        page_self = QWidget()
        ps_layout = QVBoxLayout(page_self)
        ps_layout.setContentsMargins(0, 0, 0, 0)
        ps_layout.setSpacing(5)

        body_hdr = QHBoxLayout()
        lbl_body_label = QLabel("Texto da publicação (opcional — Markdown suportado)")
        lbl_body_label.setProperty("role", "field_label")
        body_hdr.addWidget(lbl_body_label, 1)

        self.btn_toggle_preview = UiSkillsButton("Pré-visualizar")
        self.btn_toggle_preview.setProperty("class", "TranslateButton")
        self.btn_toggle_preview.setAccessibleName("Alternar pré-visualização do texto")
        self.btn_toggle_preview.setFixedHeight(26)
        self.btn_toggle_preview.clicked.connect(self._toggle_markdown_preview)
        body_hdr.addWidget(self.btn_toggle_preview)
        ps_layout.addLayout(body_hdr)

        md_toolbar = QHBoxLayout()
        md_toolbar.setSpacing(6)
        for label_txt, prefix, suffix, tip in (
            ("B", "**", "**", "Negrito (**texto**)"),
            ("I", "*", "*", "Itálico (*texto*)"),
            ("S", "~~", "~~", "Riscado (~~texto~~)"),
            ("Código", "`", "`", "Código inline (`código`)"),
            ("Citação", "\n> ", "", "Bloco de citação (> texto)"),
            ("Lista", "\n- ", "", "Lista de itens (- item)"),
        ):
            btn_fmt = UiSkillsButton(label_txt)
            btn_fmt.setProperty("class", "PillButton")
            btn_fmt.setAccessibleName(f"Formatar texto: {tip}")
            btn_fmt.setFixedHeight(26)
            btn_fmt.setToolTip(tip)
            btn_fmt.clicked.connect(
                lambda _checked=False, p=prefix, s=suffix: self._insert_markdown(p, s)
            )
            md_toolbar.addWidget(btn_fmt)
        md_toolbar.addStretch(1)
        ps_layout.addLayout(md_toolbar)

        self.editor_stack = QStackedWidget()
        self.body_text = QTextEdit()
        self.body_text.setAccessibleName("Texto da publicação")
        self.body_text.setPlaceholderText(
            "Escreva sua postagem aqui… Use os botões acima se desejar formatar."
        )
        self.editor_stack.addWidget(self.body_text)

        self.preview_browser = QTextBrowser()
        self.preview_browser.setAccessibleName("Pré-visualização do conteúdo")
        self.preview_browser.setOpenExternalLinks(True)
        self.editor_stack.addWidget(self.preview_browser)

        ps_layout.addWidget(self.editor_stack, 1)
        self.tab_stack.addWidget(page_self)

        # --- Aba 1: Imagens e vídeo ---
        page_media = QWidget()
        pm_layout = QVBoxLayout(page_media)
        pm_layout.setContentsMargins(0, 6, 0, 0)
        pm_layout.setSpacing(6)

        lbl_media_title = QLabel("Imagem ou vídeo (URL direta) *")
        lbl_media_title.setProperty("role", "field_label")
        pm_layout.addWidget(lbl_media_title)

        self.media_url_input = QLineEdit()
        self.media_url_input.setAccessibleName("URL da imagem ou vídeo")
        self.media_url_input.setPlaceholderText(
            "https://i.imgur.com/exemplo.png ou https://exemplo.com/video.mp4"
        )
        self.media_url_input.textChanged.connect(lambda _t: self._clear_field_error(self.lbl_media_err))
        pm_layout.addWidget(self.media_url_input)

        self.lbl_media_err = QLabel("")
        self.lbl_media_err.setProperty("role", "field_error")
        self.lbl_media_err.setVisible(False)
        pm_layout.addWidget(self.lbl_media_err)

        lbl_media_hint = QLabel(
            "Informe um link começando com http:// ou https:// para uma imagem ou vídeo público."
        )
        lbl_media_hint.setProperty("role", "caption")
        lbl_media_hint.setWordWrap(True)
        pm_layout.addWidget(lbl_media_hint)
        pm_layout.addStretch(1)
        self.tab_stack.addWidget(page_media)

        # --- Aba 2: Link/URL ---
        page_link = QWidget()
        pl_layout = QVBoxLayout(page_link)
        pl_layout.setContentsMargins(0, 6, 0, 0)
        pl_layout.setSpacing(6)

        lbl_link_title = QLabel("URL *")
        lbl_link_title.setProperty("role", "field_label")
        pl_layout.addWidget(lbl_link_title)

        self.link_input = QLineEdit()
        self.link_input.setAccessibleName("URL do link")
        self.link_input.setPlaceholderText("https://exemplo.com/noticia-ou-projeto")
        self.link_input.textChanged.connect(lambda _t: self._clear_field_error(self.lbl_link_err))
        pl_layout.addWidget(self.link_input)

        self.lbl_link_err = QLabel("")
        self.lbl_link_err.setProperty("role", "field_error")
        self.lbl_link_err.setVisible(False)
        pl_layout.addWidget(self.lbl_link_err)
        pl_layout.addStretch(1)
        self.tab_stack.addWidget(page_link)

        # --- Aba 3: Enquete ---
        page_poll = QWidget()
        pp_layout = QVBoxLayout(page_poll)
        pp_layout.setContentsMargins(0, 4, 0, 0)
        pp_layout.setSpacing(6)

        lbl_poll_desc = QLabel("Pergunta ou contexto (opcional)")
        lbl_poll_desc.setProperty("role", "field_label")
        pp_layout.addWidget(lbl_poll_desc)

        self.poll_context_input = QLineEdit()
        self.poll_context_input.setAccessibleName("Pergunta ou contexto da enquete")
        self.poll_context_input.setPlaceholderText(
            "Explique brevemente a pergunta da sua enquete…"
        )
        pp_layout.addWidget(self.poll_context_input)

        opts_hdr = QHBoxLayout()
        lbl_opts = QLabel("Opções de voto (mínimo 2 opções) *")
        lbl_opts.setProperty("role", "field_label")
        opts_hdr.addWidget(lbl_opts, 1)

        lbl_dur = QLabel("Duração:")
        lbl_dur.setProperty("role", "caption")
        opts_hdr.addWidget(lbl_dur)

        self.poll_duration_combo = QComboBox()
        self.poll_duration_combo.setAccessibleName("Duração da enquete")
        for days, d_label in [(1, "1 dia"), (3, "3 dias"), (7, "7 dias")]:
            self.poll_duration_combo.addItem(d_label, days)
        self.poll_duration_combo.setCurrentIndex(1)
        opts_hdr.addWidget(self.poll_duration_combo)
        pp_layout.addLayout(opts_hdr)

        self.poll_options_layout = QVBoxLayout()
        self.poll_options_layout.setSpacing(4)
        self.poll_option_inputs: List[QLineEdit] = []
        self.poll_option_rows: List[QWidget] = []
        pp_layout.addLayout(self.poll_options_layout)

        for _ in range(3):
            self._add_poll_option_row()

        poll_actions = QHBoxLayout()
        self.btn_add_poll_opt = UiSkillsButton("+ Adicionar opção")
        self.btn_add_poll_opt.setProperty("class", "PillButton")
        self.btn_add_poll_opt.setAccessibleName("Adicionar nova opção de enquete")
        self.btn_add_poll_opt.clicked.connect(self._add_poll_option_row)
        poll_actions.addWidget(self.btn_add_poll_opt)
        poll_actions.addStretch(1)
        pp_layout.addLayout(poll_actions)

        self.lbl_poll_err = QLabel("")
        self.lbl_poll_err.setProperty("role", "field_error")
        self.lbl_poll_err.setVisible(False)
        pp_layout.addWidget(self.lbl_poll_err)

        pp_layout.addStretch(1)
        self.tab_stack.addWidget(page_poll)

        layout.addWidget(self.tab_stack, 1)

        # Etapa 3 — Opções e Tags
        lbl_step3 = QLabel("3. OPÇÕES E TAGS")
        lbl_step3.setProperty("role", "form_step")
        layout.addWidget(lbl_step3)

        tags_row = QHBoxLayout()
        tags_row.setSpacing(8)

        self.btn_tag_oc = UiSkillsButton("+ OC (Conteúdo Original)")
        self.btn_tag_oc.setProperty("class", "PillButton")
        self.btn_tag_oc.setToolTip("Marca a publicação como conteúdo criado por você")
        self.btn_tag_oc.setAccessibleName("Tag Conteúdo Original: desativada")
        self.btn_tag_oc.clicked.connect(self._toggle_tag_oc)
        tags_row.addWidget(self.btn_tag_oc)

        self.btn_tag_spoiler = UiSkillsButton("+ Spoiler")
        self.btn_tag_spoiler.setProperty("class", "PillButton")
        self.btn_tag_spoiler.setToolTip("Oculta prévias para evitar revelar surpresas ou enredos")
        self.btn_tag_spoiler.setAccessibleName("Tag Spoiler: desativada")
        self.btn_tag_spoiler.clicked.connect(self._toggle_tag_spoiler)
        tags_row.addWidget(self.btn_tag_spoiler)

        self.btn_tag_nsfw = UiSkillsButton("+ 18+ (NSFW)")
        self.btn_tag_nsfw.setProperty("class", "PillButton")
        self.btn_tag_nsfw.setToolTip("Restringe a visualização para maiores de 18 anos")
        self.btn_tag_nsfw.setAccessibleName("Tag 18+ NSFW: desativada")
        self.btn_tag_nsfw.clicked.connect(self._toggle_tag_nsfw)
        tags_row.addWidget(self.btn_tag_nsfw)

        tags_row.addStretch(1)
        layout.addLayout(tags_row)

        # Status e Mensagens de Feedback
        self.lbl_status = QLabel("")
        self.lbl_status.setProperty("role", "status_ok")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setVisible(False)
        layout.addWidget(self.lbl_status)

        # Etapa 4 — Publicação
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(12)
        btn_bar.addStretch(1)

        self.btn_cancel = UiSkillsButton("Cancelar")
        self.btn_cancel.setProperty("class", "PillButton")
        self.btn_cancel.setAccessibleName("Cancelar criação de publicação")
        self.btn_cancel.clicked.connect(self._request_cancel)
        btn_bar.addWidget(self.btn_cancel)

        self.btn_submit = UiSkillsButton("Publicar no Reddit")
        self.btn_submit.setProperty("class", "PillButtonAccent")
        self.btn_submit.setAccessibleName("Publicar no Reddit")
        self.btn_submit.clicked.connect(self._submit)
        btn_bar.addWidget(self.btn_submit)

        layout.addLayout(btn_bar)

        self._switch_tab("self")
        self.title_input.setFocus()

    def _add_poll_option_row(self) -> None:
        if len(self.poll_option_inputs) >= 6:
            return
        idx = len(self.poll_option_inputs) + 1
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(6)

        opt_edit = QLineEdit()
        opt_edit.setAccessibleName(f"Opção {idx} da enquete")
        opt_edit.setPlaceholderText(
            f"Opção {idx}" + (" (obrigatória)" if idx <= 2 else " (opcional)")
        )
        opt_edit.textChanged.connect(lambda _t: self._clear_field_error(self.lbl_poll_err))
        row_l.addWidget(opt_edit, 1)

        btn_rem = UiSkillsButton("✕")
        btn_rem.setProperty("class", "FavRemoveButton")
        btn_rem.setFixedSize(26, 26)
        btn_rem.setAccessibleName(f"Remover opção {idx}")
        btn_rem.setToolTip("Remover opção")
        btn_rem.clicked.connect(lambda _c=False, rw=row_w, ed=opt_edit: self._remove_poll_option_row(rw, ed))
        row_l.addWidget(btn_rem)

        self.poll_option_inputs.append(opt_edit)
        self.poll_option_rows.append(row_w)
        self.poll_options_layout.addWidget(row_w)
        self._sync_poll_option_controls()

    def _remove_poll_option_row(self, row_w: QWidget, opt_edit: QLineEdit) -> None:
        if len(self.poll_option_inputs) <= 2:
            return
        if opt_edit in self.poll_option_inputs:
            self.poll_option_inputs.remove(opt_edit)
        if row_w in self.poll_option_rows:
            self.poll_option_rows.remove(row_w)
        self.poll_options_layout.removeWidget(row_w)
        row_w.setParent(None)
        row_w.deleteLater()
        self._sync_poll_option_controls()

    def _sync_poll_option_controls(self) -> None:
        if hasattr(self, "btn_add_poll_opt") and isValid(self.btn_add_poll_opt):
            self.btn_add_poll_opt.setEnabled(len(self.poll_option_inputs) < 6)
        for idx, inp in enumerate(self.poll_option_inputs, start=1):
            if isValid(inp):
                inp.setAccessibleName(f"Opção {idx} da enquete")
                inp.setPlaceholderText(
                    f"Opção {idx}" + (" (obrigatória)" if idx <= 2 else " (opcional)")
                )

    def _has_unsaved_content(self) -> bool:
        if self.title_input.text().strip():
            return True
        if self.body_text.toPlainText().strip():
            return True
        if self.media_url_input.text().strip() or self.link_input.text().strip():
            return True
        if self.poll_context_input.text().strip():
            return True
        return any(inp.text().strip() for inp in self.poll_option_inputs if isValid(inp))

    def _request_cancel(self) -> None:
        if self._is_submitting:
            return
        if self._has_unsaved_content():
            ans = QMessageBox.question(
                self,
                "Descartar rascunho?",
                "Você digitou conteúdo nesta publicação. Deseja descartar as alterações e fechar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.reject()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._request_cancel()
            return
        super().keyPressEvent(event)

    def _clear_field_error(self, lbl: QLabel) -> None:
        if isValid(lbl):
            lbl.setText("")
            lbl.setVisible(False)

    def _set_field_error(self, lbl: QLabel, message: str) -> None:
        if isValid(lbl):
            lbl.setText(message)
            lbl.setVisible(bool(message))

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
            is_active = key == tab_key
            btn.setProperty("active", "true" if is_active else "false")
            btn.setAccessibleDescription("Selecionado" if is_active else "Não selecionado")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

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
            self.btn_toggle_preview.setText("Editar texto")
            self.btn_toggle_preview.setProperty("translated", "true")
        else:
            self.editor_stack.setCurrentIndex(0)
            self.btn_toggle_preview.setText("Pré-visualizar")
            self.btn_toggle_preview.setProperty("translated", "false")
        self.btn_toggle_preview.style().unpolish(self.btn_toggle_preview)
        self.btn_toggle_preview.style().polish(self.btn_toggle_preview)

    def _toggle_tag_oc(self) -> None:
        self._tag_oc = not self._tag_oc
        self._sync_tag_pill(
            self.btn_tag_oc,
            self._tag_oc,
            "✓ OC (Ativo)",
            "+ OC (Conteúdo Original)",
            "Tag Conteúdo Original",
        )

    def _toggle_tag_spoiler(self) -> None:
        self._tag_spoiler = not self._tag_spoiler
        self._sync_tag_pill(
            self.btn_tag_spoiler,
            self._tag_spoiler,
            "✓ Spoiler (Ativo)",
            "+ Spoiler",
            "Tag Spoiler",
        )

    def _toggle_tag_nsfw(self) -> None:
        self._tag_nsfw = not self._tag_nsfw
        self._sync_tag_pill(
            self.btn_tag_nsfw,
            self._tag_nsfw,
            "✓ 18+ NSFW (Ativo)",
            "+ 18+ (NSFW)",
            "Tag 18+ NSFW",
        )

    def _sync_tag_pill(
        self,
        btn: UiSkillsButton,
        active: bool,
        active_txt: str,
        idle_txt: str,
        a11y_base: str,
    ) -> None:
        btn.setText(active_txt if active else idle_txt)
        btn.setAccessibleName(f"{a11y_base}: {'ativada' if active else 'desativada'}")
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
        if text.strip():
            self._clear_field_error(self.lbl_title_err)

    def _validate_inline_and_focus(self) -> bool:
        """Valida todos os campos do tipo selecionado, exibe erros inline e foca o primeiro erro."""
        for lbl in (
            self.lbl_sub_err,
            self.lbl_title_err,
            self.lbl_media_err,
            self.lbl_link_err,
            self.lbl_poll_err,
        ):
            self._clear_field_error(lbl)

        first_invalid: Optional[QWidget] = None

        raw_sub = (
            self.sub_combo.currentText()
            .strip()
            .lstrip("/")
            .removeprefix("r/")
            .strip("/")
        )
        if not raw_sub or not is_valid_subreddit_name(raw_sub):
            self._set_field_error(
                self.lbl_sub_err,
                "Informe uma comunidade válida (use apenas letras, números e underline).",
            )
            first_invalid = first_invalid or self.sub_combo

        if not self.title_input.text().strip():
            self._set_field_error(
                self.lbl_title_err, "Adicione um título para publicar."
            )
            first_invalid = first_invalid or self.title_input

        if self._current_tab == "media":
            u = self.media_url_input.text().strip()
            if not u.startswith(("http://", "https://")):
                self._set_field_error(
                    self.lbl_media_err,
                    "Informe uma URL válida começando com http:// ou https://.",
                )
                first_invalid = first_invalid or self.media_url_input
        elif self._current_tab == "link":
            u = self.link_input.text().strip()
            if not u.startswith(("http://", "https://")):
                self._set_field_error(
                    self.lbl_link_err,
                    "Informe uma URL válida começando com http:// ou https://.",
                )
                first_invalid = first_invalid or self.link_input
        elif self._current_tab == "poll":
            filled = [
                inp.text().strip()
                for inp in self.poll_option_inputs
                if isValid(inp) and inp.text().strip()
            ]
            if len(filled) < 2:
                self._set_field_error(
                    self.lbl_poll_err,
                    "Preencha pelo menos 2 opções de voto para publicar a enquete.",
                )
                for inp in self.poll_option_inputs:
                    if isValid(inp) and not inp.text().strip():
                        first_invalid = first_invalid or inp
                        break

        if first_invalid is not None:
            first_invalid.setFocus()
            return False
        return True

    def _set_status(self, message: str, role: str = "status_ok") -> None:
        if not isValid(self.lbl_status):
            return
        self.lbl_status.setText(message)
        self.lbl_status.setProperty("role", role)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)
        self.lbl_status.setVisible(bool(message))

    def _submit(self) -> None:
        if self._is_submitting:
            return
        if not self._validate_inline_and_focus():
            self._set_status(
                "Corrija os campos destacados acima antes de publicar.",
                "status_error",
            )
            return

        raw_sub = (
            self.sub_combo.currentText()
            .strip()
            .lstrip("/")
            .removeprefix("r/")
            .strip("/")
        )
        title = self.title_input.text().strip()

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
            days = self.poll_duration_combo.currentData() or 3
            opts = [
                inp.text().strip()
                for inp in self.poll_option_inputs
                if isValid(inp) and inp.text().strip()
            ]
            md_lines = []
            if ctx:
                md_lines.append(ctx)
                md_lines.append("")
            md_lines.append(f"### Opções da Enquete ({days} dias):")
            for idx, opt in enumerate(opts, start=1):
                md_lines.append(f"- **{idx}.** {opt}")
            md_lines.append("\n*(Vote e comente sua escolha abaixo!)*")
            content = "\n".join(md_lines)

        self._is_submitting = True
        self.btn_submit.setEnabled(False)
        self.btn_submit.setText("Publicando…")
        self.btn_cancel.setEnabled(False)
        self._set_status("Enviando publicação para o Reddit…", "status_warn")

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
            if isValid(self):
                self._submit_finished.emit(raw_sub, ok, msg, post_url)

        threading.Thread(target=_run, daemon=True).start()

    def _on_submit_finished(
        self, raw_sub: str, ok: bool, msg: str, post_url: object
    ) -> None:
        if not isValid(self):
            return
        self._is_submitting = False
        if ok:
            self._set_status("✓ Publicado com sucesso no Reddit!", "status_ok")
            self.post_created.emit(raw_sub, str(post_url or ""))
            QTimer.singleShot(700, lambda: self.accept() if isValid(self) else None)
        else:
            self._set_status(
                f"Não foi possível publicar: {msg}. Seu texto foi preservado — você pode tentar novamente.",
                "status_error",
            )
            self.btn_submit.setEnabled(True)
            self.btn_submit.setText("Tentar publicar novamente")
            self.btn_cancel.setEnabled(True)
