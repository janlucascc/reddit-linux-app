"""
Componentes visuais reutilizáveis do aplicativo Reddit Linux.
"""

from __future__ import annotations
import shutil
import subprocess
from typing import Callable, Optional
import webbrowser

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from reddit_api import CommentItem, PostItem


SUBREDDIT_COLORS = [
    "#FF4500",
    "#0079D3",
    "#46D160",
    "#FFB000",
    "#EA0027",
    "#7193FF",
    "#24A0ED",
    "#FF66AC",
]


def color_for_name(name: str) -> str:
    h = sum(ord(c) for c in name.lower())
    return SUBREDDIT_COLORS[h % len(SUBREDDIT_COLORS)]


def open_video_stream(url: str) -> None:
    """Abre um stream de vídeo nativo no player do sistema (mpv/vlc/xdg-open) ou navegador (BUG-012)."""
    if not url:
        return
    for player in ("mpv", "vlc", "celluloid", "totem"):
        if shutil.which(player):
            try:
                subprocess.Popen(
                    [player, url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass
    webbrowser.open(url)


def create_rounded_pixmap(
    raw_bytes: bytes, max_width: int = 680, max_height: int = 420, radius: int = 12
) -> Optional[QPixmap]:
    """
    Converte bytes de imagem em QPixmap redimensionado com cantos arredondados.
    Trata imagens verticais extremas (infográficos/tirinhas) preservando a largura
    legível e recortando o topo para a prévia (BUG-015).
    """
    pix = QPixmap()
    if not pix.loadFromData(raw_bytes) or pix.width() <= 0 or pix.height() <= 0:
        return None

    aspect = pix.height() / float(pix.width())
    if aspect > 2.0:
        # Infográfico / imagem vertical longa: ajusta pela largura e recorta o topo
        target_w = min(pix.width(), max_width)
        scaled_w = pix.scaledToWidth(
            target_w, Qt.TransformationMode.SmoothTransformation
        )
        crop_h = min(scaled_w.height(), max_height)
        scaled = scaled_w.copy(0, 0, scaled_w.width(), crop_h)
    else:
        scaled = pix.scaled(
            QSize(max_width, max_height),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    out = QPixmap(scaled.size())
    out.fill(Qt.GlobalColor.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, scaled.width(), scaled.height()), radius, radius
    )
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return out


class RedditLogoWidget(QWidget):
    """Desenha o ícone vetorial laranja do Reddit (Snoo minimalista)."""

    def __init__(self, size: int = 34, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FF4500"))
        painter.drawEllipse(QRectF(1, 1, w - 2, h - 2))

        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(w * 0.20, h * 0.34, w * 0.60, h * 0.44))

        painter.drawEllipse(QRectF(w * 0.14, h * 0.34, w * 0.16, h * 0.16))
        painter.drawEllipse(QRectF(w * 0.70, h * 0.34, w * 0.16, h * 0.16))

        pen = QPen(QColor("#FFFFFF"), max(2.0, w * 0.06))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(
            int(w * 0.50), int(h * 0.34), int(w * 0.58), int(h * 0.18)
        )
        painter.drawLine(
            int(w * 0.58), int(h * 0.18), int(w * 0.70), int(h * 0.21)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(w * 0.66, h * 0.15, w * 0.12, h * 0.12))

        painter.setBrush(QColor("#FF4500"))
        painter.drawEllipse(QRectF(w * 0.33, h * 0.47, w * 0.10, h * 0.11))
        painter.drawEllipse(QRectF(w * 0.57, h * 0.47, w * 0.10, h * 0.11))
        painter.end()


class CommunityAvatarWidget(QWidget):
    """Ícone circular colorido para identificar uma comunidade r/."""

    def __init__(
        self, name: str, size: int = 24, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._name = name.removeprefix("r/").strip() or "R"
        self._color = QColor(color_for_name(self._name))
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(0, 0, w, h))

        painter.setPen(QColor("#FFFFFF"))
        font = QFont(self.font())
        font.setPixelSize(max(10, int(h * 0.48)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, w, h),
            Qt.AlignmentFlag.AlignCenter,
            "r/",
        )
        painter.end()


class ImageViewerDialog(QDialog):
    """
    Janela modal para visualizar imagens em alta resolução com controle de zoom
    e suporte legível a infográficos verticais (BUG-015).
    """

    def __init__(
        self,
        title: str,
        raw_bytes: bytes,
        source_url: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(title[:80] or "Visualizador de Imagem")
        self.resize(1020, 780)
        self.setModal(True)

        self._orig_pixmap = QPixmap()
        self._orig_pixmap.loadFromData(raw_bytes)
        self._zoom_factor: float = 1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top_bar = QHBoxLayout()
        lbl_title = QLabel(title)
        lbl_title.setWordWrap(True)
        lbl_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        top_bar.addWidget(lbl_title, 1)

        # Controles de Zoom
        btn_zoom_out = QPushButton("🔍 −")
        btn_zoom_out.setProperty("class", "PillButton")
        btn_zoom_out.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_zoom_out.clicked.connect(lambda: self._adjust_zoom(0.8))
        top_bar.addWidget(btn_zoom_out)

        btn_fit_w = QPushButton("↔ Ajustar Largura")
        btn_fit_w.setProperty("class", "PillButton")
        btn_fit_w.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_fit_w.clicked.connect(self._fit_to_width)
        top_bar.addWidget(btn_fit_w)

        btn_orig_size = QPushButton("100%")
        btn_orig_size.setProperty("class", "PillButton")
        btn_orig_size.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_orig_size.clicked.connect(self._reset_zoom)
        top_bar.addWidget(btn_orig_size)

        btn_zoom_in = QPushButton("🔍 +")
        btn_zoom_in.setProperty("class", "PillButton")
        btn_zoom_in.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_zoom_in.clicked.connect(lambda: self._adjust_zoom(1.25))
        top_bar.addWidget(btn_zoom_in)

        if source_url:
            btn_open = QPushButton("🌐 Abrir no Navegador")
            btn_open.setProperty("class", "PillButton")
            btn_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_open.clicked.connect(lambda: webbrowser.open(source_url))
            top_bar.addWidget(btn_open)

        btn_close = QPushButton("✕ Fechar")
        btn_close.setProperty("class", "PillButtonAccent")
        btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_close.clicked.connect(self.accept)
        top_bar.addWidget(btn_close)
        layout.addLayout(top_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.img_lbl)
        layout.addWidget(self.scroll, 1)

        self._fit_to_width()

    def _fit_to_width(self) -> None:
        if self._orig_pixmap.isNull() or self._orig_pixmap.width() <= 0:
            return
        avail_w = max(600, self.width() - 64)
        if self._orig_pixmap.width() > avail_w:
            self._zoom_factor = avail_w / float(self._orig_pixmap.width())
        else:
            self._zoom_factor = 1.0
        self._render_pixmap()

    def _reset_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._render_pixmap()

    def _adjust_zoom(self, mult: float) -> None:
        self._zoom_factor = max(0.15, min(4.0, self._zoom_factor * mult))
        self._render_pixmap()

    def _render_pixmap(self) -> None:
        if self._orig_pixmap.isNull():
            return
        target_w = max(1, int(self._orig_pixmap.width() * self._zoom_factor))
        scaled = self._orig_pixmap.scaledToWidth(
            target_w, Qt.TransformationMode.SmoothTransformation
        )
        self.img_lbl.setPixmap(scaled)


class PostCardWidget(QFrame):
    """Card de postagem inspirado diretamente no feed do Reddit Web."""

    open_post_requested = Signal(object)  # PostItem
    open_subreddit_requested = Signal(str)  # nome do subreddit
    translate_post_requested = Signal(object, object)  # PostItem, PostCardWidget
    toggle_save_requested = Signal(object)  # PostItem
    vote_requested = Signal(object, int)  # PostItem, direction (1, -1, 0)

    def __init__(
        self,
        post: PostItem,
        is_saved: bool = False,
        on_request_image: Optional[Callable[[str, Callable[[bytes], None]], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.post = post
        self.is_saved = is_saved
        self._raw_image_bytes: Optional[bytes] = None
        self.setProperty("class", "PostCard")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(10)

        # 1. Cabeçalho: Avatar + r/subreddit • u/autor • tempo + Flair
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        avatar = CommunityAvatarWidget(post.subreddit, size=22)
        header_row.addWidget(avatar)

        btn_sub = QPushButton(post.subreddit_prefixed)
        btn_sub.setProperty("class", "SubredditLink")
        btn_sub.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_sub.clicked.connect(
            lambda: self.open_subreddit_requested.emit(self.post.subreddit)
        )
        header_row.addWidget(btn_sub)

        meta_lbl = QLabel(f"• u/{post.author} • {post.relative_time}")
        meta_lbl.setStyleSheet("color: #8BA2AD; font-size: 12px;")
        header_row.addWidget(meta_lbl)

        if post.stickied:
            pin_lbl = QLabel("📌 Fixado")
            pin_lbl.setStyleSheet(
                "background-color: #1A3A2A; color: #46D160; font-size: 11px; font-weight: 700; "
                "padding: 2px 8px; border-radius: 9px;"
            )
            header_row.addWidget(pin_lbl)

        if post.is_video:
            vid_lbl = QLabel("🎬 Vídeo")
            vid_lbl.setStyleSheet(
                "background-color: #3A1C12; color: #FF6B35; font-size: 11px; font-weight: 700; "
                "padding: 2px 8px; border-radius: 9px;"
            )
            header_row.addWidget(vid_lbl)

        if post.over_18:
            nsfw_lbl = QLabel("18+")
            nsfw_lbl.setStyleSheet(
                "background-color: #4A151B; color: #FF585B; font-size: 11px; font-weight: 700; "
                "padding: 2px 7px; border-radius: 9px;"
            )
            header_row.addWidget(nsfw_lbl)

        if post.flair:
            flair_lbl = QLabel(post.flair[:32])
            flair_lbl.setStyleSheet(
                "background-color: #24373E; color: #D7DADC; font-size: 11px; font-weight: 600; "
                "padding: 2px 9px; border-radius: 10px;"
            )
            header_row.addWidget(flair_lbl)

        self.translated_badge = QLabel("🌐 Traduzido")
        self.translated_badge.setStyleSheet(
            "background-color: #102E4A; color: #4DB8FF; font-size: 11px; font-weight: 700; "
            "padding: 2px 8px; border-radius: 9px;"
        )
        self.translated_badge.setVisible(post.show_translated)
        header_row.addWidget(self.translated_badge)

        header_row.addStretch(1)
        main_layout.addLayout(header_row)

        # 2. Título clicável do Post
        self.title_label = QLabel(post.display_title)
        self.title_label.setWordWrap(True)
        self.title_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.title_label.setStyleSheet(
            "font-size: 16px; font-weight: 700; line-height: 1.35;"
        )
        self.title_label.mousePressEvent = self._on_title_clicked
        main_layout.addWidget(self.title_label)

        # 3. Prévia do texto (selftext) se houver
        self.body_label = QLabel()
        self.body_label.setWordWrap(True)
        self.body_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.body_label.setStyleSheet("color: #B8C7CE; font-size: 13px;")
        self.body_label.mousePressEvent = self._on_title_clicked
        main_layout.addWidget(self.body_label)
        self._update_body_preview()

        # 4. Link externo (quando não é post de texto nem imagem direta)
        if (
            not post.is_self
            and post.url
            and not post.preview_image_url
            and "reddit.com" not in post.domain
        ):
            link_btn = QPushButton(f"🔗 {post.domain or post.url[:55]} ↗")
            link_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            link_btn.setStyleSheet(
                "QPushButton { background-color: #18272C; color: #4DB8FF; border: 1px solid #24373E; "
                "border-radius: 10px; padding: 8px 12px; text-align: left; font-size: 12px; font-weight: 600; }"
                "QPushButton:hover { border-color: #4DB8FF; }"
            )
            link_btn.clicked.connect(lambda: webbrowser.open(self.post.url))
            main_layout.addWidget(link_btn)

        # 5. Prévia de imagem (carregada de forma assíncrona)
        self.image_container = QWidget()
        img_layout = QVBoxLayout(self.image_container)
        img_layout.setContentsMargins(0, 4, 0, 4)
        img_layout.setSpacing(4)

        self.image_label = QLabel("Carregando imagem...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(180)
        self.image_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.image_label.setStyleSheet(
            "background-color: #0E171A; color: #647C87; border-radius: 12px; padding: 12px; font-size: 12px;"
        )
        self.image_label.mousePressEvent = self._on_image_clicked
        img_layout.addWidget(self.image_label)

        if post.is_video:
            vid_hint = QLabel("▶ Clique para reproduzir o vídeo")
            vid_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vid_hint.setStyleSheet(
                "color: #FF6B35; font-size: 12px; font-weight: 700;"
            )
            img_layout.addWidget(vid_hint)
        elif len(post.gallery_images) > 1:
            gal_badge = QLabel(
                f"🖼️ Galeria com {len(post.gallery_images)} imagens (clique no post para navegar)"
            )
            gal_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            gal_badge.setStyleSheet(
                "color: #8BA2AD; font-size: 11px; font-weight: 600;"
            )
            img_layout.addWidget(gal_badge)

        if post.preview_image_url:
            main_layout.addWidget(self.image_container)
            if on_request_image:
                on_request_image(post.preview_image_url, self.set_preview_image)
        else:
            self.image_container.setVisible(False)

        # 6. Barra de ações inferior estilo Reddit Web (pílulas arredondadas)
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        # Pílula interativa de Voto
        self._vote_state: int = 0
        self._base_score: int = post.score

        vote_pill = QFrame()
        vote_pill.setProperty("class", "PillButton")
        vp_layout = QHBoxLayout(vote_pill)
        vp_layout.setContentsMargins(6, 0, 6, 0)
        vp_layout.setSpacing(6)

        self.btn_upvote = QPushButton("▲")
        self.btn_upvote.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_upvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; padding: 2px;")
        self.btn_upvote.clicked.connect(lambda: self._on_vote_clicked(1))
        vp_layout.addWidget(self.btn_upvote)

        self.lbl_score = QLabel(post.formatted_score)
        self.lbl_score.setStyleSheet("font-weight: 700; font-size: 12px;")
        vp_layout.addWidget(self.lbl_score)

        self.btn_downvote = QPushButton("▼")
        self.btn_downvote.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_downvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; padding: 2px;")
        self.btn_downvote.clicked.connect(lambda: self._on_vote_clicked(-1))
        vp_layout.addWidget(self.btn_downvote)

        actions_row.addWidget(vote_pill)

        comm_btn = QPushButton(f"💬  {post.formatted_comments}")
        comm_btn.setProperty("class", "PillButton")
        comm_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        comm_btn.setToolTip("Abrir postagem e ler comentários")
        comm_btn.clicked.connect(lambda: self.open_post_requested.emit(self.post))
        actions_row.addWidget(comm_btn)

        if post.is_video:
            play_btn = QPushButton("▶ Assistir Vídeo")
            play_btn.setProperty("class", "PillButtonAccent")
            play_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            play_btn.clicked.connect(
                lambda: open_video_stream(self.post.video_url or self.post.url)
            )
            actions_row.addWidget(play_btn)

        self.translate_btn = QPushButton()
        self.translate_btn.setProperty("class", "TranslateButton")
        self.translate_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.translate_btn.clicked.connect(
            lambda: self.translate_post_requested.emit(self.post, self)
        )
        self._sync_translate_button()
        actions_row.addWidget(self.translate_btn)

        self.save_btn = QPushButton()
        self.save_btn.setProperty("class", "PillButton")
        self.save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._sync_save_button()
        self.save_btn.clicked.connect(self._on_save_clicked)
        actions_row.addWidget(self.save_btn)

        share_btn = QPushButton("🔗 Copiar Link")
        share_btn.setProperty("class", "PillButton")
        share_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        share_btn.clicked.connect(lambda: self._copy_link(share_btn))
        actions_row.addWidget(share_btn)

        actions_row.addStretch(1)
        main_layout.addLayout(actions_row)

    def _update_body_preview(self) -> None:
        if not isValid(self) or not isValid(self.body_label):
            return
        txt = self.post.display_selftext
        if txt:
            preview = txt[:320].strip()
            if len(txt) > 320:
                preview += "…"
            self.body_label.setText(preview)
            self.body_label.setVisible(True)
        else:
            self.body_label.setVisible(False)

    def _sync_translate_button(self) -> None:
        if not isValid(self) or not isValid(self.translate_btn):
            return
        if self.post.show_translated:
            self.translate_btn.setText("🔄 Ver Original")
            self.translate_btn.setProperty("translated", "true")
        else:
            self.translate_btn.setText("🌐 Traduzir")
            self.translate_btn.setProperty("translated", "false")
        self.translate_btn.style().unpolish(self.translate_btn)
        self.translate_btn.style().polish(self.translate_btn)

    def _sync_save_button(self) -> None:
        if not isValid(self) or not isValid(self.save_btn):
            return
        if self.is_saved:
            self.save_btn.setText("★ Salvo")
        else:
            self.save_btn.setText("🔖 Salvar")

    def _on_vote_clicked(self, direction: int) -> None:
        if self._vote_state == direction:
            self._vote_state = 0
        else:
            self._vote_state = direction
        self._sync_vote_ui()
        self.vote_requested.emit(self.post, self._vote_state)

    def _sync_vote_ui(self) -> None:
        if not isValid(self) or not isValid(self.btn_upvote) or not isValid(self.btn_downvote):
            return
        adj_score = self._base_score + self._vote_state
        self.post.score = adj_score
        self.lbl_score.setText(self.post.formatted_score)

        if self._vote_state == 1:
            self.btn_upvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; color: #FF4500;")
            self.btn_downvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; color: #8BA2AD;")
            self.lbl_score.setStyleSheet("font-weight: 800; font-size: 12px; color: #FF4500;")
        elif self._vote_state == -1:
            self.btn_upvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; color: #8BA2AD;")
            self.btn_downvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; color: #7193FF;")
            self.lbl_score.setStyleSheet("font-weight: 800; font-size: 12px; color: #7193FF;")
        else:
            self.btn_upvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; color: #8BA2AD;")
            self.btn_downvote.setStyleSheet("background: transparent; border: none; font-weight: 800; font-size: 13px; color: #8BA2AD;")
            self.lbl_score.setStyleSheet("font-weight: 700; font-size: 12px;")

    def _on_save_clicked(self) -> None:
        self.is_saved = not self.is_saved
        self._sync_save_button()
        self.toggle_save_requested.emit(self.post)

    def _copy_link(self, btn: QPushButton) -> None:
        """Copia o link do post e restaura o texto original após 2 segundos (BUG-007)."""
        perm = self.post.permalink
        if perm.startswith("http"):
            full_url = perm
        else:
            if not perm.startswith("/"):
                perm = "/" + perm
            full_url = f"https://www.reddit.com{perm}"
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(full_url)
        if isValid(btn):
            btn.setText("✓ Copiado!")
            QTimer.singleShot(
                2000,
                lambda: btn.setText("🔗 Copiar Link") if isValid(btn) else None,
            )

    def _on_title_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_post_requested.emit(self.post)

    def _on_image_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.post.is_video:
                open_video_stream(self.post.video_url or self.post.url)
            elif len(self.post.gallery_images) > 1 or not self._raw_image_bytes:
                self.open_post_requested.emit(self.post)
            else:
                dlg = ImageViewerDialog(
                    self.post.display_title,
                    self._raw_image_bytes,
                    self.post.highres_image_url or self.post.url,
                    self.window(),
                )
                dlg.exec()

    def set_preview_image(self, raw_bytes: bytes) -> None:
        """Atualiza a imagem com proteção contra C++ object deleted (BUG-001)."""
        if not isValid(self) or not isValid(self.image_label):
            return
        self._raw_image_bytes = raw_bytes
        pix = create_rounded_pixmap(raw_bytes, max_width=660, max_height=390, radius=12)
        if pix:
            self.image_label.setText("")
            self.image_label.setPixmap(pix)
            self.image_label.setMinimumHeight(pix.height())
        elif isValid(self.image_container):
            self.image_container.setVisible(False)

    def set_translating_state(self, loading: bool) -> None:
        """Atualiza estado de tradução com proteção contra C++ object deleted (BUG-001)."""
        if not isValid(self) or not isValid(self.translate_btn):
            return
        if loading:
            self.translate_btn.setText("⏳ Traduzindo...")
            self.translate_btn.setEnabled(False)
        else:
            self.translate_btn.setEnabled(True)
            self.refresh_translation_ui()

    def refresh_translation_ui(self) -> None:
        if not isValid(self):
            return
        if isValid(self.title_label):
            self.title_label.setText(self.post.display_title)
        self._update_body_preview()
        if isValid(self.translated_badge):
            self.translated_badge.setVisible(self.post.show_translated)
        self._sync_translate_button()


class CommentCardWidget(QFrame):
    """Card de comentário estilo árvore de discussão do Reddit Web."""

    translate_comment_requested = Signal(object, object)  # CommentItem, CommentCardWidget

    def __init__(
        self, comment: CommentItem, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.comment = comment
        self.setProperty("class", "CommentCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        author_lbl = QLabel(f"u/{comment.author}")
        author_lbl.setStyleSheet("font-weight: 700; font-size: 12px;")
        top_row.addWidget(author_lbl)

        if comment.is_submitter:
            op_badge = QLabel("OP")
            op_badge.setStyleSheet(
                "background-color: #0079D3; color: #FFFFFF; font-size: 10px; font-weight: 800; "
                "padding: 1px 6px; border-radius: 6px;"
            )
            top_row.addWidget(op_badge)

        time_lbl = QLabel(f"• {comment.relative_time}")
        time_lbl.setStyleSheet("color: #8BA2AD; font-size: 11px;")
        top_row.addWidget(time_lbl)

        self.tr_badge = QLabel("🌐 Traduzido")
        self.tr_badge.setStyleSheet(
            "background-color: #102E4A; color: #4DB8FF; font-size: 10px; font-weight: 700; "
            "padding: 1px 6px; border-radius: 6px;"
        )
        self.tr_badge.setVisible(comment.show_translated)
        top_row.addWidget(self.tr_badge)

        top_row.addStretch(1)

        self.tr_btn = QPushButton()
        self.tr_btn.setProperty("class", "TranslateButton")
        self.tr_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.tr_btn.setFixedHeight(26)
        self.tr_btn.clicked.connect(
            lambda: self.translate_comment_requested.emit(self.comment, self)
        )
        self._sync_btn()
        top_row.addWidget(self.tr_btn)

        layout.addLayout(top_row)

        self.body_lbl = QLabel(comment.display_body)
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.body_lbl.setStyleSheet("font-size: 13px; line-height: 1.45;")
        layout.addWidget(self.body_lbl)

    def _sync_btn(self) -> None:
        if not isValid(self) or not isValid(self.tr_btn):
            return
        if self.comment.show_translated:
            self.tr_btn.setText("🔄 Original")
            self.tr_btn.setProperty("translated", "true")
        else:
            self.tr_btn.setText("🌐 Traduzir")
            self.tr_btn.setProperty("translated", "false")
        self.tr_btn.style().unpolish(self.tr_btn)
        self.tr_btn.style().polish(self.tr_btn)

    def set_loading(self, loading: bool) -> None:
        if not isValid(self) or not isValid(self.tr_btn):
            return
        if loading:
            self.tr_btn.setText("⏳...")
            self.tr_btn.setEnabled(False)
        else:
            self.tr_btn.setEnabled(True)
            self.refresh_ui()

    def refresh_ui(self) -> None:
        if not isValid(self):
            return
        if isValid(self.body_lbl):
            self.body_lbl.setText(self.comment.display_body)
        if isValid(self.tr_badge):
            self.tr_badge.setVisible(self.comment.show_translated)
        self._sync_btn()
