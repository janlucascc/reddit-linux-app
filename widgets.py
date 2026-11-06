"""
Componentes visuais reutilizáveis do aplicativo Reddit Linux (Sprints 3 & 4).
Implementa:
- Zero estilos hexadecimais inline (100% guiado por ThemeManager QSS + Tokens semânticos — CR-03)
- High-DPI e Thread-Safety estrito com QImage off-thread e QPixmap na GUI thread (CR-04)
- Prevenção de Layout Shift (CLS = 0) com reserva de aspect-ratio no #MediaFrame (CR-04)
- Árvore de comentários flat O(N) com clamping em depth=8, guide lines interativas e batch fold (CR-05)
- Player de vídeo nativo com suporte a MPEG-DASH/HLS, fallback GStreamer e integração MPV+yt-dlp (CR-06)
"""

from __future__ import annotations
import shutil
import subprocess
from typing import Callable, Dict, List, Optional
import webbrowser

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Property,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from shiboken6 import isValid

from reddit_api import CommentItem, PostItem
from ui_theme import ThemeManager


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


def canonical_reddit_url(permalink: str, fallback_url: str = "") -> str:
    """Constrói a URL canônica do post no Reddit para muxing de vídeo+áudio via mpv/yt-dlp (CR-06)."""
    if permalink:
        if permalink.startswith("http"):
            return permalink
        clean = permalink if permalink.startswith("/") else f"/{permalink}"
        return f"https://www.reddit.com{clean}"
    return fallback_url


def open_video_stream(url: str, permalink: str = "") -> None:
    """
    Abre um stream de vídeo no player nativo do sistema (mpv/vlc/celluloid) ou navegador (CR-06).
    Quando o mpv está disponível e há permalink do post, passa a URL canônica do post primeiro
    para que o yt-dlp integrado ao mpv faça o muxing automático das faixas de vídeo + áudio DASH.
    """
    canonical = canonical_reddit_url(permalink, url)
    target_for_mpv = canonical or url
    if not target_for_mpv:
        return

    if shutil.which("mpv"):
        try:
            subprocess.Popen(
                ["mpv", "--force-window=immediate", target_for_mpv],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

    play_url = url or canonical
    for player in ("vlc", "celluloid", "totem"):
        if shutil.which(player):
            try:
                subprocess.Popen(
                    [player, play_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass
    webbrowser.open(canonical or play_url)


def scale_qimage_threadsafe(
    raw_bytes: bytes,
    max_width: int = 680,
    max_height: int = 420,
    radius: int = 12,
    dpr: float = 1.0,
) -> Optional[QImage]:
    """
    Decodifica e redimensiona bytes de imagem usando exclusivamente QImage (100% Thread-Safe, CR-04).
    Aplica compensação High-DPI (devicePixelRatioF) e recorte de cantos arredondados.
    Trata infográficos verticais (aspect > 2.0) ajustando pela largura e recortando o topo na prévia.
    """
    if not raw_bytes:
        return None
    img = QImage()
    if not img.loadFromData(raw_bytes) or img.width() <= 0 or img.height() <= 0:
        return None

    safe_dpr = max(1.0, float(dpr))
    phys_max_w = max(1, int(max_width * safe_dpr))
    phys_max_h = max(1, int(max_height * safe_dpr))
    phys_radius = radius * safe_dpr

    aspect = img.height() / float(img.width())
    if aspect > 2.0:
        target_w = min(img.width(), phys_max_w)
        scaled_w = img.scaledToWidth(
            target_w, Qt.TransformationMode.SmoothTransformation
        )
        crop_h = min(scaled_w.height(), phys_max_h)
        scaled = scaled_w.copy(0, 0, scaled_w.width(), crop_h)
    else:
        scaled = img.scaled(
            QSize(phys_max_w, phys_max_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    if radius <= 0:
        scaled.setDevicePixelRatio(safe_dpr)
        return scaled

    out = QImage(scaled.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, float(scaled.width()), float(scaled.height())),
        phys_radius,
        phys_radius,
    )
    painter.setClipPath(path)
    painter.drawImage(0, 0, scaled)
    painter.end()

    out.setDevicePixelRatio(safe_dpr)
    return out


def qimage_to_pixmap_gui(img: Optional[QImage]) -> Optional[QPixmap]:
    """Converte QImage em QPixmap estritamente na thread principal da GUI (CR-04)."""
    if img is None or img.isNull():
        return None
    return QPixmap.fromImage(img)


def create_rounded_pixmap(
    raw_bytes: bytes, max_width: int = 680, max_height: int = 420, radius: int = 12
) -> Optional[QPixmap]:
    """
    Wrapper compatível para chamada na GUI thread com suporte automático a High-DPI (CR-04).
    """
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app is not None else 1.0
    qimg = scale_qimage_threadsafe(
        raw_bytes, max_width=max_width, max_height=max_height, radius=radius, dpr=dpr
    )
    return qimage_to_pixmap_gui(qimg)


class UiSkillsButton(QPushButton):
    """
    Botão interativo com micro-interação tátil scale-on-press (0.96, 100ms OutCubic)
    e suporte completo a foco por teclado (:focus ring via QSS).
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._press_scale: float = 1.0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._anim = QPropertyAnimation(self, b"pressScale", self)
        self._anim.setDuration(100)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_press_scale(self) -> float:
        return self._press_scale

    def _set_press_scale(self, val: float) -> None:
        self._press_scale = float(val)
        self.update()

    pressScale = Property(float, _get_press_scale, _set_press_scale)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._anim.stop()
            self._anim.setStartValue(self._press_scale)
            self._anim.setEndValue(0.96)
            self._anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._press_scale != 1.0:
            self._anim.stop()
            self._anim.setStartValue(self._press_scale)
            self._anim.setEndValue(1.0)
            self._anim.start()
        super().mouseReleaseEvent(event)


class RedditLogoWidget(QWidget):
    """Desenha o ícone vetorial do Reddit (Snoo minimalista) conectado ao ThemeManager (CR-03)."""

    def __init__(self, size: int = 34, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        if isValid(self):
            self.update()

    def paintEvent(self, event) -> None:
        tokens = ThemeManager.instance().tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())

        brand = QColor(tokens.accent_brand)
        fg = QColor(tokens.text_inverse)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brand)
        painter.drawEllipse(QRectF(1, 1, w - 2, h - 2))

        painter.setBrush(fg)
        painter.drawEllipse(QRectF(w * 0.20, h * 0.34, w * 0.60, h * 0.44))

        painter.drawEllipse(QRectF(w * 0.14, h * 0.34, w * 0.16, h * 0.16))
        painter.drawEllipse(QRectF(w * 0.70, h * 0.34, w * 0.16, h * 0.16))

        pen = QPen(fg, max(2.0, w * 0.06))
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

        painter.setBrush(brand)
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
        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        if isValid(self):
            self.update()

    def set_community_name(self, name: str) -> None:
        self._name = name.removeprefix("r/").strip() or "R"
        self._color = QColor(color_for_name(self._name))
        if isValid(self):
            self.update()

    def paintEvent(self, event) -> None:
        tokens = ThemeManager.instance().tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(0, 0, w, h))

        painter.setPen(QColor(tokens.text_inverse))
        font = QFont(self.font())
        font.setPixelSize(max(10, int(h * 0.46)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, w, h),
            Qt.AlignmentFlag.AlignCenter,
            "r/",
        )
        painter.end()


class ShimmerSkeletonWidget(QWidget):
    """
    Bloco retangular com animação shimmer contínua conectado ao ThemeManager (CR-03).
    """

    def __init__(
        self,
        height: int = 16,
        radius: int = 8,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._radius = float(radius)
        self._phase: float = 0.0
        self.setFixedHeight(height)

        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._advance_shimmer)
        self._timer.start()

        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        if isValid(self):
            self.update()

    def _advance_shimmer(self) -> None:
        if not isValid(self) or not self.isVisible():
            return
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    def paintEvent(self, event) -> None:
        tokens = ThemeManager.instance().tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())

        grad = QLinearGradient(0.0, 0.0, w, 0.0)
        base_col = QColor(tokens.skeleton_base)
        hi_col = QColor(tokens.skeleton_highlight)

        p0 = max(0.0, self._phase - 0.25)
        p1 = self._phase
        p2 = min(1.0, self._phase + 0.25)

        grad.setColorAt(0.0, base_col)
        grad.setColorAt(p0, base_col)
        grad.setColorAt(p1, hi_col)
        grad.setColorAt(p2, base_col)
        grad.setColorAt(1.0, base_col)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
        painter.end()


class PostCardSkeleton(QFrame):
    """Card esqueleto (shimmer) exibido durante o carregamento inicial do feed."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "PostCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        top = ShimmerSkeletonWidget(height=14, radius=6)
        top.setMaximumWidth(240)
        layout.addWidget(top)

        title = ShimmerSkeletonWidget(height=20, radius=6)
        layout.addWidget(title)

        media = ShimmerSkeletonWidget(height=160, radius=10)
        layout.addWidget(media)

        bottom = ShimmerSkeletonWidget(height=28, radius=14)
        bottom.setMaximumWidth(320)
        layout.addWidget(bottom)


class _ScalerSignals(QObject):
    """Sinais para entrega assíncrona de QImage da thread de escala para a GUI thread."""

    finished = Signal(int, object)  # request_id, QImage


class ImageScalerWorker(QRunnable):
    """
    Worker QRunnable para redimensionamento de imagens de alta resolução fora da GUI thread (CR-04).
    Usa exclusivamente QImage no método run() e emite o resultado para conversão QPixmap na GUI.
    """

    def __init__(
        self,
        request_id: int,
        base_image: QImage,
        target_width: int,
        dpr: float = 1.0,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.base_image = base_image
        self.target_width = max(1, target_width)
        self.dpr = max(1.0, float(dpr))
        self.signals = _ScalerSignals()

    def run(self) -> None:
        if self.base_image.isNull():
            return
        phys_w = max(1, int(self.target_width * self.dpr))
        scaled = self.base_image.scaledToWidth(
            phys_w, Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(self.dpr)
        self.signals.finished.emit(self.request_id, scaled)


class _PanScrollArea(QScrollArea):
    """QScrollArea com suporte a Drag-to-Pan via clique/arraste e zoom via roda do mouse."""

    wheel_zoom_requested = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dragging = False
        self._last_pos = QPoint()
        self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.pos()
            self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            delta = event.pos() - self._last_pos
            self._last_pos = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y > 0:
                self.wheel_zoom_requested.emit(1.2)
            elif delta_y < 0:
                self.wheel_zoom_requested.emit(0.83)
            event.accept()
            return
        super().wheelEvent(event)


class ImageViewerDialog(QDialog):
    """
    Lightbox modal para visualizar imagens em alta resolução com:
    - Escalonamento QImage off-thread via ImageScalerWorker (CR-04)
    - Drag-to-Pan e Zoom por botões ou Ctrl+Scroll
    - Detecção automática de modo Infográfico (aspect > 2.0, BUG-015)
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
        self.resize(1060, 800)
        self.setModal(True)

        self._orig_image = QImage()
        self._orig_image.loadFromData(raw_bytes)
        self._zoom_factor: float = 1.0
        self._scale_req_id: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_title = QLabel(title)
        lbl_title.setWordWrap(True)
        lbl_title.setProperty("role", "card_title")
        top_bar.addWidget(lbl_title, 1)

        is_infographic = (
            not self._orig_image.isNull()
            and self._orig_image.width() > 0
            and (self._orig_image.height() / float(self._orig_image.width())) > 2.0
        )
        if is_infographic:
            info_badge = QLabel("📜 Modo Infográfico")
            info_badge.setProperty("class", "BadgeTranslated")
            top_bar.addWidget(info_badge)

        btn_zoom_out = UiSkillsButton("🔍 −")
        btn_zoom_out.setProperty("class", "PillButton")
        btn_zoom_out.clicked.connect(lambda: self._adjust_zoom(0.8))
        top_bar.addWidget(btn_zoom_out)

        self.lbl_zoom_pct = QLabel("100%")
        self.lbl_zoom_pct.setProperty("role", "caption")
        top_bar.addWidget(self.lbl_zoom_pct)

        btn_zoom_in = UiSkillsButton("🔍 +")
        btn_zoom_in.setProperty("class", "PillButton")
        btn_zoom_in.clicked.connect(lambda: self._adjust_zoom(1.25))
        top_bar.addWidget(btn_zoom_in)

        btn_fit_w = UiSkillsButton("↔ Ajustar Largura")
        btn_fit_w.setProperty("class", "PillButton")
        btn_fit_w.clicked.connect(self._fit_to_width)
        top_bar.addWidget(btn_fit_w)

        btn_orig_size = UiSkillsButton("1:1 (100%)")
        btn_orig_size.setProperty("class", "PillButton")
        btn_orig_size.clicked.connect(self._reset_zoom)
        top_bar.addWidget(btn_orig_size)

        if source_url:
            btn_open = UiSkillsButton("🌐 Original ↗")
            btn_open.setProperty("class", "PillButton")
            btn_open.clicked.connect(lambda: webbrowser.open(source_url))
            top_bar.addWidget(btn_open)

        btn_close = UiSkillsButton("✕ Fechar")
        btn_close.setProperty("class", "PillButtonAccent")
        btn_close.clicked.connect(self.accept)
        top_bar.addWidget(btn_close)
        layout.addLayout(top_bar)

        self.scroll = _PanScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.wheel_zoom_requested.connect(self._adjust_zoom)

        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.img_lbl)
        layout.addWidget(self.scroll, 1)

        self._fit_to_width()

    def _fit_to_width(self) -> None:
        if self._orig_image.isNull() or self._orig_image.width() <= 0:
            return
        avail_w = max(640, self.width() - 64)
        if self._orig_image.width() > avail_w:
            self._zoom_factor = avail_w / float(self._orig_image.width())
        else:
            self._zoom_factor = 1.0
        self._schedule_render()

    def _reset_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._schedule_render()

    def _adjust_zoom(self, mult: float) -> None:
        self._zoom_factor = max(0.15, min(4.0, self._zoom_factor * mult))
        self._schedule_render()

    def _schedule_render(self) -> None:
        if self._orig_image.isNull():
            return
        self._scale_req_id += 1
        req_id = self._scale_req_id
        if isValid(self.lbl_zoom_pct):
            self.lbl_zoom_pct.setText(f"{int(round(self._zoom_factor * 100))}%")

        target_w = max(1, int(self._orig_image.width() * self._zoom_factor))
        app = QGuiApplication.instance()
        dpr = app.devicePixelRatio() if app is not None else 1.0

        worker = ImageScalerWorker(req_id, self._orig_image, target_w, dpr=dpr)
        worker.signals.finished.connect(self._on_scaled_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_scaled_ready(self, req_id: int, scaled_qimg: QImage) -> None:
        if not isValid(self) or not isValid(self.img_lbl):
            return
        if req_id != self._scale_req_id:
            return
        pix = qimage_to_pixmap_gui(scaled_qimg)
        if pix:
            self.img_lbl.setPixmap(pix)


class PostCardWidget(QFrame):
    """
    Card de postagem inspirado na arquitetura semântica do Reddit moderno (Shreddit):
    - Coluna vertical esquerda de votação (#VotePillar, 44px) no modo Card
    - Modo Compacto (linha densa) alternável dinamicamente
    - Pre-reserva de altura de mídia (#MediaFrame) contra Layout Shift (CLS = 0, CR-04)
    - Superfície clicável unificada com marcação de post visitado
    """

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
        view_mode: str = "card",
        is_visited: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.post = post
        self.is_saved = is_saved
        self._view_mode = view_mode
        self._is_visited = is_visited
        self._raw_image_bytes: Optional[bytes] = None
        self.setProperty("class", "PostCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        if post.likes is True:
            self._vote_state = 1
        elif post.likes is False:
            self._vote_state = -1
        else:
            self._vote_state = 0
        self._base_score: int = post.score - self._vote_state

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 1. Coluna Vertical de Votação (#VotePillar — 44px)
        self.vote_pillar = QFrame()
        self.vote_pillar.setObjectName("VotePillar")
        self.vote_pillar.setFixedWidth(46)
        vp_layout = QVBoxLayout(self.vote_pillar)
        vp_layout.setContentsMargins(6, 12, 6, 12)
        vp_layout.setSpacing(4)

        self.btn_upvote = UiSkillsButton("▲")
        self.btn_upvote.setObjectName("VoteButton")
        self.btn_upvote.setToolTip("Upvote")
        self.btn_upvote.clicked.connect(lambda: self._on_vote_clicked(1))
        vp_layout.addWidget(self.btn_upvote, 0, Qt.AlignmentFlag.AlignHCenter)

        self.lbl_score = QLabel(post.formatted_score)
        self.lbl_score.setObjectName("VoteScore")
        self.lbl_score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vp_layout.addWidget(self.lbl_score)

        self.btn_downvote = UiSkillsButton("▼")
        self.btn_downvote.setObjectName("VoteButton")
        self.btn_downvote.setToolTip("Downvote")
        self.btn_downvote.clicked.connect(lambda: self._on_vote_clicked(-1))
        vp_layout.addWidget(self.btn_downvote, 0, Qt.AlignmentFlag.AlignHCenter)

        vp_layout.addStretch(1)
        outer_layout.addWidget(self.vote_pillar)

        # 2. Coluna Principal de Conteúdo
        content_widget = QWidget()
        main_layout = QVBoxLayout(content_widget)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(8)
        outer_layout.addWidget(content_widget, 1)

        # 2.1 Cabeçalho: Avatar + r/subreddit • u/autor • tempo + Badges Semânticas
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

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
        meta_lbl.setProperty("role", "caption")
        header_row.addWidget(meta_lbl)

        if post.stickied:
            pin_lbl = QLabel("📌 Fixado")
            pin_lbl.setProperty("class", "BadgeStickied")
            header_row.addWidget(pin_lbl)

        if post.is_video:
            vid_lbl = QLabel("🎬 Vídeo")
            vid_lbl.setProperty("class", "BadgeVideo")
            header_row.addWidget(vid_lbl)

        if post.over_18:
            nsfw_lbl = QLabel("18+")
            nsfw_lbl.setProperty("class", "BadgeNSFW")
            header_row.addWidget(nsfw_lbl)

        if getattr(post, "spoiler", False):
            sp_lbl = QLabel("SPOILER")
            sp_lbl.setProperty("class", "BadgeSpoiler")
            header_row.addWidget(sp_lbl)

        if getattr(post, "oc", False):
            oc_lbl = QLabel("OC")
            oc_lbl.setProperty("class", "BadgeOC")
            header_row.addWidget(oc_lbl)

        if post.flair:
            flair_lbl = QLabel(post.flair[:32])
            flair_lbl.setProperty("class", "BadgeFlair")
            header_row.addWidget(flair_lbl)

        self.translated_badge = QLabel("🌐 Traduzido")
        self.translated_badge.setProperty("class", "BadgeTranslated")
        self.translated_badge.setVisible(post.show_translated)
        header_row.addWidget(self.translated_badge)

        header_row.addStretch(1)
        main_layout.addLayout(header_row)

        # 2.2 Título clicável do Post (com estado visited)
        self.title_label = QLabel(post.display_title)
        self.title_label.setWordWrap(True)
        self.title_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.title_label.setProperty("role", "card_title")
        self.title_label.setProperty("visited", "true" if is_visited else "false")
        self.title_label.mousePressEvent = self._on_title_clicked
        main_layout.addWidget(self.title_label)

        # 2.3 Prévia do texto (selftext) se houver
        self.body_label = QLabel()
        self.body_label.setWordWrap(True)
        self.body_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.body_label.setProperty("role", "body_preview")
        self.body_label.mousePressEvent = self._on_title_clicked
        main_layout.addWidget(self.body_label)
        self._update_body_preview()

        # 2.4 Link externo (quando não é post de texto nem imagem direta)
        if (
            not post.is_self
            and post.url
            and not post.preview_image_url
            and "reddit.com" not in post.domain
        ):
            link_btn = UiSkillsButton(f"🔗 {post.domain or post.url[:55]} ↗")
            link_btn.setObjectName("ExternalLinkChip")
            link_btn.clicked.connect(lambda: webbrowser.open(self.post.url))
            main_layout.addWidget(link_btn)

        # 2.5 Prévia de imagem com altura pré-calculada (Zero Layout Shift — CR-04)
        self.image_container = QFrame()
        self.image_container.setObjectName("MediaFrame")
        img_layout = QVBoxLayout(self.image_container)
        img_layout.setContentsMargins(0, 0, 0, 0)
        img_layout.setSpacing(4)

        expected_h = self._compute_reserved_media_height(max_w=640, max_h=380)
        self.image_label = QLabel("Carregando mídia...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setFixedHeight(expected_h)
        self.image_label.setProperty("role", "muted")
        self.image_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.image_label.mousePressEvent = self._on_image_clicked
        img_layout.addWidget(self.image_label)

        if post.is_video:
            vid_hint = QLabel("▶ Clique para assistir com áudio")
            vid_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vid_hint.setProperty("class", "BadgeVideo")
            img_layout.addWidget(vid_hint)
        elif len(post.gallery_images) > 1:
            gal_badge = QLabel(
                f"🖼️ Galeria com {len(post.gallery_images)} imagens (clique para abrir)"
            )
            gal_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            gal_badge.setProperty("role", "caption")
            img_layout.addWidget(gal_badge)

        if post.preview_image_url:
            main_layout.addWidget(self.image_container)
            if on_request_image:
                on_request_image(post.preview_image_url, self.set_preview_image)
        else:
            self.image_container.setVisible(False)

        # 2.6 Barra de ações inferior estilo Reddit Shreddit
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        comm_btn = UiSkillsButton(f"💬  {post.formatted_comments}")
        comm_btn.setProperty("class", "PillButton")
        comm_btn.setToolTip("Abrir postagem e ler comentários")
        comm_btn.clicked.connect(lambda: self._emit_open_post())
        actions_row.addWidget(comm_btn)

        if post.is_video:
            play_btn = UiSkillsButton("▶ Assistir Vídeo")
            play_btn.setProperty("class", "PillButtonAccent")
            play_btn.clicked.connect(
                lambda: open_video_stream(
                    self.post.video_url or self.post.url, self.post.permalink
                )
            )
            actions_row.addWidget(play_btn)

        self.translate_btn = UiSkillsButton()
        self.translate_btn.setProperty("class", "TranslateButton")
        self.translate_btn.clicked.connect(
            lambda: self.translate_post_requested.emit(self.post, self)
        )
        self._sync_translate_button()
        actions_row.addWidget(self.translate_btn)

        self.save_btn = UiSkillsButton()
        self.save_btn.setProperty("class", "PillButton")
        self._sync_save_button()
        self.save_btn.clicked.connect(self._on_save_clicked)
        actions_row.addWidget(self.save_btn)

        share_btn = UiSkillsButton("🔗 Copiar Link")
        share_btn.setProperty("class", "PillButton")
        share_btn.clicked.connect(lambda: self._copy_link(share_btn))
        actions_row.addWidget(share_btn)

        actions_row.addStretch(1)
        main_layout.addLayout(actions_row)

        self._sync_vote_ui()
        self.set_view_mode(view_mode)

    def _compute_reserved_media_height(self, max_w: int = 640, max_h: int = 380) -> int:
        """Calcula a altura exata do container antes do download da imagem para CLS = 0 (CR-04)."""
        pw = int(getattr(self.post, "preview_width", 0) or 0)
        ph = int(getattr(self.post, "preview_height", 0) or 0)
        if pw > 0 and ph > 0:
            aspect = ph / float(pw)
            if aspect > 2.0:
                return max_h
            scale = min(1.0, max_w / float(pw), max_h / float(ph))
            calc_h = int(ph * scale)
            return max(160, min(max_h, calc_h))
        return 240

    def set_view_mode(self, mode: str) -> None:
        """Alterna dinamicamente entre visualização 'card' e 'compact'."""
        self._view_mode = mode
        is_compact = mode == "compact"
        if isValid(self.body_label):
            self.body_label.setVisible((not is_compact) and bool(self.post.display_selftext))
        if isValid(self.image_container):
            self.image_container.setVisible(
                (not is_compact) and bool(self.post.preview_image_url)
            )

    def mark_visited(self) -> None:
        self._is_visited = True
        if isValid(self.title_label):
            self.title_label.setProperty("visited", "true")
            self.title_label.style().unpolish(self.title_label)
            self.title_label.style().polish(self.title_label)

    def _emit_open_post(self) -> None:
        self.mark_visited()
        self.open_post_requested.emit(self.post)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_open_post()
            event.accept()
            return
        super().mousePressEvent(event)

    def _update_body_preview(self) -> None:
        if not isValid(self) or not isValid(self.body_label):
            return
        txt = self.post.display_selftext
        if txt and self._view_mode != "compact":
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
        if (
            not isValid(self)
            or not isValid(self.btn_upvote)
            or not isValid(self.btn_downvote)
            or not isValid(self.lbl_score)
        ):
            return
        adj_score = self._base_score + self._vote_state
        self.post.score = adj_score
        self.lbl_score.setText(self.post.formatted_score)

        if self._vote_state == 1:
            up_state, down_state, score_state = "up", "none", "up"
        elif self._vote_state == -1:
            up_state, down_state, score_state = "none", "down", "down"
        else:
            up_state, down_state, score_state = "none", "none", "none"

        self.btn_upvote.setProperty("voted", up_state)
        self.btn_downvote.setProperty("voted", down_state)
        self.lbl_score.setProperty("voted", score_state)

        for w in (self.btn_upvote, self.btn_downvote, self.lbl_score):
            w.style().unpolish(w)
            w.style().polish(w)

    def _on_save_clicked(self) -> None:
        self.is_saved = not self.is_saved
        self._sync_save_button()
        self.toggle_save_requested.emit(self.post)

    def _copy_link(self, btn: QPushButton) -> None:
        full_url = canonical_reddit_url(self.post.permalink, self.post.url)
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
            self._emit_open_post()

    def _on_image_clicked(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.post.is_video:
                self._emit_open_post()
            elif len(self.post.gallery_images) > 1 or not self._raw_image_bytes:
                self._emit_open_post()
            else:
                dlg = ImageViewerDialog(
                    self.post.display_title,
                    self._raw_image_bytes,
                    self.post.highres_image_url or self.post.url,
                    self.window(),
                )
                dlg.exec()

    def set_preview_image(self, raw_bytes: bytes) -> None:
        """Atualiza a imagem mantendo altura estável (CR-04) e proteção Shiboken (BUG-001)."""
        if not isValid(self) or not isValid(self.image_label):
            return
        self._raw_image_bytes = raw_bytes
        pix = create_rounded_pixmap(raw_bytes, max_width=640, max_height=380, radius=10)
        if pix:
            logical_h = int(pix.height() / max(1.0, pix.devicePixelRatio()))
            self.image_label.setText("")
            self.image_label.setPixmap(pix)
            self.image_label.setFixedHeight(max(140, logical_h))
        elif isValid(self.image_container):
            self.image_container.setVisible(False)

    def set_translating_state(self, loading: bool) -> None:
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


class CommentRowWidget(QFrame):
    """
    Linha de comentário otimizada para árvore flat (CR-05):
    - Clamping de indentação em depth=8 (máximo 144px) com badge 'Nível N'
    - Linhas guia verticais desenhadas via QPainter e clicáveis para colapsar subárvore
    - Barra de ações com Upvote/Downvote, Responder inline e Traduzir
    """

    MAX_VISUAL_DEPTH = 8
    INDENT_STEP_PX = 18

    translate_comment_requested = Signal(object, object)  # CommentItem, CommentRowWidget
    toggle_fold_requested = Signal(str)  # comment_id
    continue_thread_requested = Signal(str)  # comment_id
    vote_comment_requested = Signal(object, int)  # CommentItem, direction
    reply_comment_submitted = Signal(str, str, object)  # parent_fullname, text, CommentRowWidget

    def __init__(
        self,
        comment: CommentItem,
        descendant_count: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.comment = comment
        self.descendant_count = descendant_count
        self._is_folded: bool = False
        self._hover_guide: bool = False
        self.setProperty("class", "CommentCard")
        self.setMouseTracking(True)

        if comment.likes is True:
            self._vote_state = 1
        elif comment.likes is False:
            self._vote_state = -1
        else:
            self._vote_state = 0
        self._base_score = comment.score - self._vote_state

        self.visual_depth = min(max(0, comment.depth), self.MAX_VISUAL_DEPTH)
        self.left_indent = self.visual_depth * self.INDENT_STEP_PX

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.left_indent + 16, 10, 14, 10)
        layout.setSpacing(6)

        # 1. Cabeçalho do Comentário
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.btn_fold = QPushButton("[−]")
        self.btn_fold.setProperty("class", "PillButton")
        self.btn_fold.setFixedSize(28, 22)
        self.btn_fold.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_fold.setToolTip("Recolher/Expandir subárvore de comentários")
        self.btn_fold.clicked.connect(
            lambda: self.toggle_fold_requested.emit(self.comment.id)
        )
        top_row.addWidget(self.btn_fold)

        self.author_lbl = QLabel(f"u/{comment.author}")
        self.author_lbl.setProperty("role", "subtitle")
        top_row.addWidget(self.author_lbl)

        if comment.is_submitter:
            op_badge = QLabel("OP")
            op_badge.setProperty("class", "BadgeOP")
            top_row.addWidget(op_badge)

        if comment.distinguished == "moderator":
            mod_badge = QLabel("🛡️ MOD")
            mod_badge.setProperty("class", "BadgeMod")
            top_row.addWidget(mod_badge)
        elif comment.distinguished == "admin":
            adm_badge = QLabel("ADMIN")
            adm_badge.setProperty("class", "BadgeAdmin")
            top_row.addWidget(adm_badge)

        if comment.author_flair_text:
            flair_lbl = QLabel(comment.author_flair_text[:28])
            flair_lbl.setProperty("class", "BadgeFlair")
            top_row.addWidget(flair_lbl)

        if comment.depth >= self.MAX_VISUAL_DEPTH:
            depth_badge = QLabel(f"Nível {comment.depth}")
            depth_badge.setProperty("class", "BadgeTranslated")
            top_row.addWidget(depth_badge)

        self.time_lbl = QLabel(f"• {comment.relative_time}")
        self.time_lbl.setProperty("role", "caption")
        top_row.addWidget(self.time_lbl)

        self.folded_summary_lbl = QLabel("")
        self.folded_summary_lbl.setProperty("role", "muted")
        self.folded_summary_lbl.setVisible(False)
        top_row.addWidget(self.folded_summary_lbl)

        self.tr_badge = QLabel("🌐 Traduzido")
        self.tr_badge.setProperty("class", "BadgeTranslated")
        self.tr_badge.setVisible(comment.show_translated)
        top_row.addWidget(self.tr_badge)

        top_row.addStretch(1)

        self.tr_btn = UiSkillsButton()
        self.tr_btn.setProperty("class", "TranslateButton")
        self.tr_btn.setFixedHeight(24)
        self.tr_btn.clicked.connect(
            lambda: self.translate_comment_requested.emit(self.comment, self)
        )
        self._sync_btn()
        top_row.addWidget(self.tr_btn)

        layout.addLayout(top_row)

        # 2. Corpo e Ações (ocultados quando o comentário está colapsado)
        self.body_container = QWidget()
        body_layout = QVBoxLayout(self.body_container)
        body_layout.setContentsMargins(0, 2, 0, 0)
        body_layout.setSpacing(6)

        self.body_lbl = QLabel(comment.display_body)
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.body_lbl.setProperty("role", "comment_body")
        body_layout.addWidget(self.body_lbl)

        # Barra de ações do comentário
        act_row = QHBoxLayout()
        act_row.setSpacing(6)

        self.btn_up = UiSkillsButton("▲")
        self.btn_up.setObjectName("VoteButton")
        self.btn_up.setFixedSize(26, 24)
        self.btn_up.clicked.connect(lambda: self._on_vote(1))
        act_row.addWidget(self.btn_up)

        self.lbl_score = QLabel(str(comment.score))
        self.lbl_score.setObjectName("VoteScore")
        act_row.addWidget(self.lbl_score)

        self.btn_down = UiSkillsButton("▼")
        self.btn_down.setObjectName("VoteButton")
        self.btn_down.setFixedSize(26, 24)
        self.btn_down.clicked.connect(lambda: self._on_vote(-1))
        act_row.addWidget(self.btn_down)

        btn_reply = UiSkillsButton("💬 Responder")
        btn_reply.setProperty("class", "PillButton")
        btn_reply.setFixedHeight(26)
        btn_reply.clicked.connect(self._toggle_reply_box)
        act_row.addWidget(btn_reply)

        if comment.depth >= self.MAX_VISUAL_DEPTH and descendant_count > 0:
            btn_cont = UiSkillsButton(
                f"↗ Continuar esta discussão ({descendant_count}) →"
            )
            btn_cont.setProperty("class", "TranslateButton")
            btn_cont.setFixedHeight(26)
            btn_cont.clicked.connect(
                lambda: self.continue_thread_requested.emit(self.comment.id)
            )
            act_row.addWidget(btn_cont)

        act_row.addStretch(1)
        body_layout.addLayout(act_row)

        # 3. Compositor Inline de Resposta
        self.reply_box = QFrame()
        self.reply_box.setVisible(False)
        rb_layout = QVBoxLayout(self.reply_box)
        rb_layout.setContentsMargins(0, 6, 0, 4)
        rb_layout.setSpacing(6)

        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText(
            f"Responder a u/{comment.author} em Markdown..."
        )
        self.reply_input.setFixedHeight(84)
        rb_layout.addWidget(self.reply_input)

        rb_btns = QHBoxLayout()
        rb_btns.addStretch(1)

        btn_cancel_reply = UiSkillsButton("Cancelar")
        btn_cancel_reply.setProperty("class", "PillButton")
        btn_cancel_reply.clicked.connect(lambda: self.reply_box.setVisible(False))
        rb_btns.addWidget(btn_cancel_reply)

        self.btn_send_reply = UiSkillsButton("Publicar Resposta")
        self.btn_send_reply.setProperty("class", "PillButtonAccent")
        self.btn_send_reply.clicked.connect(self._submit_inline_reply)
        rb_btns.addWidget(self.btn_send_reply)

        rb_layout.addLayout(rb_btns)
        body_layout.addWidget(self.reply_box)

        layout.addWidget(self.body_container)
        self._sync_vote_ui()

        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode: str) -> None:
        if isValid(self):
            self.update()

    def set_folded(self, folded: bool, hidden_count: int = 0) -> None:
        self._is_folded = folded
        if isValid(self.btn_fold):
            self.btn_fold.setText("[+]" if folded else "[−]")
        if isValid(self.body_container):
            self.body_container.setHidden(folded)
        if isValid(self.folded_summary_lbl):
            if folded:
                plural = "respostas ocultas" if hidden_count != 1 else "resposta oculta"
                self.folded_summary_lbl.setText(f"• ({hidden_count} {plural})")
                self.folded_summary_lbl.setHidden(False)
            else:
                self.folded_summary_lbl.setHidden(True)
        self.update()

    def _on_vote(self, direction: int) -> None:
        if self._vote_state == direction:
            self._vote_state = 0
        else:
            self._vote_state = direction
        self._sync_vote_ui()
        self.vote_comment_requested.emit(self.comment, self._vote_state)

    def _sync_vote_ui(self) -> None:
        if not isValid(self):
            return
        self.comment.score = self._base_score + self._vote_state
        self.lbl_score.setText(str(self.comment.score))
        up_s = "up" if self._vote_state == 1 else "none"
        dn_s = "down" if self._vote_state == -1 else "none"
        sc_s = "up" if self._vote_state == 1 else ("down" if self._vote_state == -1 else "none")
        self.btn_up.setProperty("voted", up_s)
        self.btn_down.setProperty("voted", dn_s)
        self.lbl_score.setProperty("voted", sc_s)
        for w in (self.btn_up, self.btn_down, self.lbl_score):
            w.style().unpolish(w)
            w.style().polish(w)

    def _toggle_reply_box(self) -> None:
        if isValid(self.reply_box):
            vis = not self.reply_box.isVisible()
            self.reply_box.setVisible(vis)
            if vis and isValid(self.reply_input):
                self.reply_input.setFocus()

    def _submit_inline_reply(self) -> None:
        if not isValid(self.reply_input):
            return
        txt = self.reply_input.toPlainText().strip()
        if not txt:
            return
        fullname = (
            self.comment.id
            if self.comment.id.startswith("t1_")
            else f"t1_{self.comment.id}"
        )
        self.reply_comment_submitted.emit(fullname, txt, self)

    def clear_and_close_reply(self) -> None:
        if isValid(self.reply_input):
            self.reply_input.clear()
        if isValid(self.reply_box):
            self.reply_box.setVisible(False)

    def mouseMoveEvent(self, event) -> None:
        in_guide = event.position().x() <= (self.left_indent + 14)
        if in_guide != self._hover_guide:
            self._hover_guide = in_guide
            self.setCursor(
                QCursor(
                    Qt.CursorShape.PointingHandCursor
                    if in_guide
                    else Qt.CursorShape.ArrowCursor
                )
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_guide:
            self._hover_guide = False
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.position().x() <= (self.left_indent + 14)
        ):
            self.toggle_fold_requested.emit(self.comment.id)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 36:
            self.toggle_fold_requested.emit(self.comment.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.visual_depth <= 0 and not self._hover_guide:
            return
        tokens = ThemeManager.instance().tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        base_color = QColor(tokens.border_default)
        active_color = QColor(tokens.accent_brand)
        h = self.height()

        for level in range(1, self.visual_depth + 1):
            x = (level - 1) * self.INDENT_STEP_PX + 10
            is_last = level == self.visual_depth
            pen = QPen(
                active_color if (is_last and self._hover_guide) else base_color,
                2 if (is_last and self._hover_guide) else 1,
            )
            painter.setPen(pen)
            painter.drawLine(x, 6, x, h - 6)
        painter.end()

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


# Alias para compatibilidade retroativa
CommentCardWidget = CommentRowWidget


class CommentsFlatContainer(QWidget):
    """
    Container flat O(N) para árvore de comentários (CR-05):
    - Mantém todos os CommentRowWidget em um único QVBoxLayout sem QLayouts aninhados
    - Colapsa/expande subárvores inteiras em < 15ms usando setUpdatesEnabled(False)
    - Suporta navegação focada em subárvores profundas ('Continuar esta discussão →')
    """

    translate_comment_requested = Signal(object, object)
    vote_comment_requested = Signal(object, int)
    reply_comment_submitted = Signal(str, str, object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ordered_rows: List[CommentRowWidget] = []
        self._rows_by_id: Dict[str, int] = {}
        self._folded_roots: set[str] = set()
        self._focused_root_id: Optional[str] = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        # Barra de retorno quando focado em uma subárvore profunda
        self.breadcrumb_bar = QFrame()
        self.breadcrumb_bar.setProperty("class", "InfoPanel")
        self.breadcrumb_bar.setVisible(False)
        bb_layout = QHBoxLayout(self.breadcrumb_bar)
        bb_layout.setContentsMargins(12, 8, 12, 8)
        bb_layout.setSpacing(10)

        lbl_focus = QLabel("🔎 Visualizando subárvore de discussão específica")
        lbl_focus.setProperty("role", "subtitle")
        bb_layout.addWidget(lbl_focus, 1)

        btn_reset_tree = UiSkillsButton("← Voltar para todos os comentários")
        btn_reset_tree.setProperty("class", "PillButtonAccent")
        btn_reset_tree.clicked.connect(self.reset_subtree_focus)
        bb_layout.addWidget(btn_reset_tree)

        main_layout.addWidget(self.breadcrumb_bar)

        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        main_layout.addWidget(self.rows_widget)

    def clear_comments(self) -> None:
        self.setUpdatesEnabled(False)
        try:
            self._ordered_rows.clear()
            self._rows_by_id.clear()
            self._folded_roots.clear()
            self._focused_root_id = None
            if isValid(self.breadcrumb_bar):
                self.breadcrumb_bar.setVisible(False)
            while self.rows_layout.count():
                item = self.rows_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        finally:
            self.setUpdatesEnabled(True)

    def set_comments(self, comments: List[CommentItem]) -> List[CommentRowWidget]:
        """Popula a lista flat de comentários pré-calculando contagem de descendentes em O(N)."""
        self.clear_comments()
        if not comments:
            return []

        n = len(comments)
        desc_counts = [0] * n
        for i in range(n):
            base_depth = comments[i].depth
            cnt = 0
            for j in range(i + 1, n):
                if comments[j].depth > base_depth:
                    cnt += 1
                else:
                    break
            desc_counts[i] = cnt

        self.setUpdatesEnabled(False)
        try:
            for idx, c in enumerate(comments):
                row = CommentRowWidget(c, descendant_count=desc_counts[idx])
                row.translate_comment_requested.connect(self.translate_comment_requested.emit)
                row.toggle_fold_requested.connect(self.toggle_fold)
                row.continue_thread_requested.connect(self.focus_subtree)
                row.vote_comment_requested.connect(self.vote_comment_requested.emit)
                row.reply_comment_submitted.connect(self.reply_comment_submitted.emit)

                self._rows_by_id[c.id] = len(self._ordered_rows)
                self._ordered_rows.append(row)
                self.rows_layout.addWidget(row)
        finally:
            self.setUpdatesEnabled(True)

        return list(self._ordered_rows)

    def insert_reply_after(
        self, parent_row: CommentRowWidget, new_comment: CommentItem
    ) -> CommentRowWidget:
        """Insere um novo comentário logo abaixo do comentário pai na estrutura flat."""
        parent_idx = self._rows_by_id.get(parent_row.comment.id, -1)
        new_comment.depth = parent_row.comment.depth + 1
        row = CommentRowWidget(new_comment, descendant_count=0)
        row.translate_comment_requested.connect(self.translate_comment_requested.emit)
        row.toggle_fold_requested.connect(self.toggle_fold)
        row.continue_thread_requested.connect(self.focus_subtree)
        row.vote_comment_requested.connect(self.vote_comment_requested.emit)
        row.reply_comment_submitted.connect(self.reply_comment_submitted.emit)

        insert_pos = parent_idx + 1 if parent_idx >= 0 else len(self._ordered_rows)
        self._ordered_rows.insert(insert_pos, row)
        self._rows_by_id = {
            r.comment.id: i for i, r in enumerate(self._ordered_rows)
        }
        self.rows_layout.insertWidget(insert_pos, row)
        return row

    def toggle_fold(self, comment_id: str) -> None:
        """Colapsa ou expande subárvore em lote O(N) com setUpdatesEnabled(False) (< 15ms, CR-05)."""
        if comment_id not in self._rows_by_id:
            return
        if comment_id in self._folded_roots:
            self._folded_roots.remove(comment_id)
        else:
            self._folded_roots.add(comment_id)
        self._recompute_visibility()

    def focus_subtree(self, comment_id: str) -> None:
        """Foca a visualização apenas na subárvore iniciada em comment_id."""
        if comment_id not in self._rows_by_id:
            return
        self._focused_root_id = comment_id
        if isValid(self.breadcrumb_bar):
            self.breadcrumb_bar.setVisible(True)
        self._recompute_visibility()

    def reset_subtree_focus(self) -> None:
        self._focused_root_id = None
        if isValid(self.breadcrumb_bar):
            self.breadcrumb_bar.setVisible(False)
        self._recompute_visibility()

    def _recompute_visibility(self) -> None:
        self.setUpdatesEnabled(False)
        try:
            n = len(self._ordered_rows)
            start_idx = 0
            end_idx = n
            if self._focused_root_id and self._focused_root_id in self._rows_by_id:
                start_idx = self._rows_by_id[self._focused_root_id]
                root_d = self._ordered_rows[start_idx].comment.depth
                end_idx = start_idx + 1
                while (
                    end_idx < n
                    and self._ordered_rows[end_idx].comment.depth > root_d
                ):
                    end_idx += 1

            active_fold_depth: Optional[int] = None
            for i, row in enumerate(self._ordered_rows):
                if not isValid(row):
                    continue
                if i < start_idx or i >= end_idx:
                    if not row.isHidden():
                        row.setHidden(True)
                    continue

                d = row.comment.depth
                if active_fold_depth is not None:
                    if d > active_fold_depth:
                        if not row.isHidden():
                            row.setHidden(True)
                        continue
                    else:
                        active_fold_depth = None

                if row.isHidden():
                    row.setHidden(False)

                is_folded = row.comment.id in self._folded_roots
                if is_folded:
                    if not row._is_folded:
                        row.set_folded(True, row.descendant_count)
                    active_fold_depth = d
                else:
                    if row._is_folded:
                        row.set_folded(False, 0)
        finally:
            self.setUpdatesEnabled(True)

    @property
    def comment_rows(self) -> List[CommentRowWidget]:
        return list(self._ordered_rows)


class EmbeddedVideoWidget(QFrame):
    """
    Reprodutor de vídeo nativo do Reddit com suporte a MPEG-DASH/HLS,
    banner de fallback para áudio muxado via MPV+yt-dlp e controles integrados (CR-06).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("EmbeddedVideoContainer")
        self.setMinimumHeight(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. Barra Superior de Controles
        top_bar = QFrame()
        top_bar.setObjectName("VideoTopBar")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(12, 8, 12, 8)
        tb_layout.setSpacing(8)

        lbl_icon = QLabel("🎬  Reprodutor de Vídeo Reddit")
        lbl_icon.setProperty("role", "subtitle")
        tb_layout.addWidget(lbl_icon)

        tb_layout.addStretch(1)

        self.btn_ext = UiSkillsButton("🔊 Abrir com Áudio HD no MPV ↗")
        self.btn_ext.setProperty("class", "PillButtonAccent")
        self.btn_ext.setToolTip(
            "Abre no MPV usando yt-dlp para combinar automaticamente vídeo HD + trilha de áudio DASH"
        )
        self.btn_ext.clicked.connect(self._open_external)
        tb_layout.addWidget(self.btn_ext)

        layout.addWidget(top_bar)

        # 2. View WebEngine HTML5 com suporte a HLS / MP4 e aviso de áudio
        self.web_view = QWebEngineView()
        layout.addWidget(self.web_view, 1)

        # 3. Banner informativo de Codec / Áudio DASH (CR-06)
        self.codec_banner = QFrame()
        self.codec_banner.setObjectName("MediaCodecFallbackBanner")
        cb_layout = QHBoxLayout(self.codec_banner)
        cb_layout.setContentsMargins(12, 6, 12, 6)
        cb_layout.setSpacing(8)

        self.lbl_codec_info = QLabel(
            "💡 Dica: Vídeos v.redd.it transmitem áudio em trilha DASH separada. "
            "Se não ouvir som no player embutido, clique em 'Abrir com Áudio HD no MPV'."
        )
        self.lbl_codec_info.setProperty("role", "caption")
        self.lbl_codec_info.setWordWrap(True)
        cb_layout.addWidget(self.lbl_codec_info, 1)

        layout.addWidget(self.codec_banner)

        self._current_url: str = ""
        self._current_permalink: str = ""

    def load_video(
        self,
        video_url: str,
        dash_url: str = "",
        hls_url: str = "",
        permalink: str = "",
    ) -> None:
        self._current_url = video_url or hls_url or dash_url
        self._current_permalink = permalink
        if not self._current_url:
            self.stop()
            return

        tokens = ThemeManager.instance().tokens
        bg_hex = tokens.bg_canvas
        play_src = video_url or hls_url

        html_doc = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      background-color: {bg_hex};
      display: flex;
      justify-content: center;
      align-items: center;
      height: 100vh;
      overflow: hidden;
    }}
    video {{
      width: 100%;
      height: 100%;
      max-height: 100vh;
      object-fit: contain;
      outline: none;
    }}
  </style>
</head>
<body>
  <video id="rvid" src="{play_src}" controls autoplay playsinline loop></video>
</body>
</html>"""
        self.web_view.setHtml(html_doc)

    def stop(self) -> None:
        """Pausa e descarrega a mídia para liberar recursos."""
        if isValid(self.web_view):
            tokens = ThemeManager.instance().tokens
            self.web_view.setHtml(
                f"<html><body style='background:{tokens.bg_canvas};'></body></html>"
            )

    def _open_external(self) -> None:
        if self._current_url or self._current_permalink:
            open_video_stream(self._current_url, self._current_permalink)
