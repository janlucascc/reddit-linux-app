#!/usr/bin/env python3
"""
Reddit Linux Desktop — Aplicativo gráfico nativo em PySide6 (Qt6)
com visual inspirado no Reddit Web e tradução integrada (individual, em lote e automática).
"""

from __future__ import annotations
import os
os.environ["MANGOHUD"] = "0"

from dataclasses import asdict
import json
from pathlib import Path
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional
import webbrowser

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QCloseEvent, QCursor, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from browser_auth import BrowserLoginDialog
from post_dialog import CreatePostDialog
from reddit_api import (
    CommentItem,
    PostItem,
    RedditClient,
    SubredditInfo,
    format_count,
    is_valid_subreddit_name,
)
from translator import SUPPORTED_LANGUAGES, TranslationService
from ui_theme import build_qt_palette, build_stylesheet
from widgets import (
    CommentCardWidget,
    CommunityAvatarWidget,
    EmbeddedVideoWidget,
    ImageViewerDialog,
    PostCardWidget,
    RedditLogoWidget,
    create_rounded_pixmap,
    open_video_stream,
)


CONFIG_PATH = Path.home() / ".config" / "reddit-linux-app" / "config.json"

DEFAULT_SUBREDDITS = [
    "linux",
    "brasil",
    "technology",
    "programacao",
    "worldnews",
    "games",
    "science",
    "AskReddit",
]


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class AsyncTask(QRunnable):
    """Executa chamadas de rede/tradução em background sem travar a interface gráfica."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            res = self.fn()
            try:
                self.signals.result.emit(res)
            except RuntimeError:
                pass
        except Exception as exc:
            traceback.print_exc()
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                pass
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass


class RedditLinuxWindow(QMainWindow):
    """Janela principal do cliente Reddit nativo para Linux."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Reddit Linux Desktop")
        self.resize(1340, 860)
        self.setMinimumSize(960, 620)

        self._is_closing: bool = False
        self._feed_request_id: int = 0
        self._detail_request_id: int = 0

        self.client = RedditClient()
        self.config = self._load_config()
        self.dark_mode: bool = bool(self.config.get("dark_mode", True))
        self.auto_translate: bool = bool(self.config.get("auto_translate", False))
        self.target_lang: str = str(self.config.get("target_lang", "pt"))

        raw_favs = list(self.config.get("favorites", DEFAULT_SUBREDDITS))
        self.favorites: List[str] = [
            f for f in raw_favs if isinstance(f, str) and is_valid_subreddit_name(f)
        ] or list(DEFAULT_SUBREDDITS)

        self.saved_posts_data: Dict[str, Dict[str, Any]] = dict(
            self.config.get("saved_posts", {})
        )

        self.translator = TranslationService(target_lang=self.target_lang)
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(8)
        self._active_workers: List[AsyncTask] = []

        # Estado de navegação
        self.current_subreddit: str = "popular"
        self.current_sort: str = "hot"
        self.current_time_filter: str = "day"
        self.next_after: Optional[str] = None
        self.is_viewing_saved: bool = False
        self.is_searching: bool = False
        self.current_search_query: str = ""

        self.feed_cards: List[PostCardWidget] = []
        self.current_post: Optional[PostItem] = None
        self.current_comments: List[CommentItem] = []
        self.comment_cards: List[CommentCardWidget] = []
        self.current_sub_info: Optional[SubredditInfo] = None
        self._gallery_index: int = 0
        self._detail_raw_image: Optional[bytes] = None
        self.current_user: Optional[Dict[str, Any]] = self.client.fetch_current_user()

        self._build_ui()
        self._update_user_ui()
        self._apply_theme()
        self._setup_shortcuts()

        # Verifica sessão em segundo plano
        self._check_login_status_on_startup()

        # Carrega feed inicial (Início se logado, ou Popular)
        initial_sub = "home" if self.current_user else "popular"
        self.load_subreddit(initial_sub, sort="hot")

    def _load_config(self) -> Dict[str, Any]:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {
            "dark_mode": True,
            "auto_translate": False,
            "target_lang": "pt",
            "favorites": DEFAULT_SUBREDDITS,
            "saved_posts": {},
        }

    def _save_config(self) -> None:
        """Salva configurações de forma atômica via arquivo temporário + os.replace (BUG-008)."""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "dark_mode": self.dark_mode,
                "auto_translate": self.auto_translate,
                "target_lang": self.target_lang,
                "favorites": self.favorites,
                "saved_posts": self.saved_posts_data,
            }
            payload = json.dumps(data, ensure_ascii=False, indent=2)
            tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, CONFIG_PATH)
        except Exception:
            pass

    def closeEvent(self, event: QCloseEvent) -> None:
        """Encerramento limpo cancelando tarefas pendentes no QThreadPool (BUG-013)."""
        self._is_closing = True
        self._feed_request_id += 1
        self._detail_request_id += 1
        if hasattr(self, "video_player"):
            self.video_player.stop()
        self.thread_pool.clear()
        self._save_config()
        self.thread_pool.waitForDone(500)
        super().closeEvent(event)

    def _run_async(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        if self._is_closing:
            return
        worker = AsyncTask(fn)
        self._active_workers.append(worker)

        def _cleanup() -> None:
            if worker in self._active_workers:
                self._active_workers.remove(worker)

        def _safe_success(res: Any) -> None:
            if not self._is_closing and isValid(self):
                on_success(res)

        def _safe_error(err: str) -> None:
            if not self._is_closing and isValid(self) and on_error:
                on_error(err)

        worker.signals.result.connect(_safe_success)
        if on_error:
            worker.signals.error.connect(_safe_error)
        worker.signals.finished.connect(_cleanup)
        self.thread_pool.start(worker)

    def _request_image_async(
        self, url: str, callback: Callable[[bytes], None]
    ) -> None:
        def _dl() -> Optional[bytes]:
            return self.client.download_image(url)

        def _done(raw: Optional[bytes]) -> None:
            if raw and not self._is_closing:
                callback(raw)

        self._run_async(_dl, _done)

    # -------------------------------------------------------------------------
    # Construção da Interface
    # -------------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Barra Superior (Header estilo Reddit Web)
        header = QFrame()
        header.setObjectName("TopHeader")
        header.setFixedHeight(60)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 8, 18, 8)
        header_layout.setSpacing(12)

        logo_widget = RedditLogoWidget(size=34)
        header_layout.addWidget(logo_widget)

        brand_btn = QPushButton("reddit")
        brand_btn.setObjectName("BrandButton")
        brand_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        brand_btn.clicked.connect(lambda: self.load_subreddit("popular"))
        header_layout.addWidget(brand_btn)

        linux_tag = QLabel("LINUX")
        linux_tag.setStyleSheet(
            "background-color: #FF4500; color: #FFFFFF; font-size: 10px; font-weight: 800; "
            "padding: 2px 6px; border-radius: 6px;"
        )
        header_layout.addWidget(linux_tag)

        header_layout.addSpacing(16)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("GlobalSearch")
        self.search_input.setPlaceholderText(
            "🔍  Pesquisar no Reddit ou digite r/comunidade (ex: r/linux)..."
        )
        self.search_input.setMinimumWidth(340)
        self.search_input.setMaximumWidth(560)
        self.search_input.returnPressed.connect(self._on_search_submitted)
        header_layout.addWidget(self.search_input, 1)

        btn_search = QPushButton("Buscar")
        btn_search.setProperty("class", "PillButton")
        btn_search.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_search.clicked.connect(self._on_search_submitted)
        header_layout.addWidget(btn_search)

        header_layout.addStretch(1)

        self.btn_translate_page = QPushButton("🌐 Traduzir Página")
        self.btn_translate_page.setProperty("class", "TranslateButton")
        self.btn_translate_page.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_translate_page.setToolTip(
            "Traduz todos os posts ou comentários visíveis na tela (Ctrl+T)"
        )
        self.btn_translate_page.clicked.connect(self.translate_current_view)
        header_layout.addWidget(self.btn_translate_page)

        self.btn_auto_translate = QPushButton()
        self.btn_auto_translate.setProperty("class", "TranslateButton")
        self.btn_auto_translate.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_auto_translate.setToolTip(
            "Quando ativado, traduz automaticamente feeds e comentários ao abrir"
        )
        self.btn_auto_translate.clicked.connect(self._toggle_auto_translate)
        self._sync_auto_translate_btn()
        header_layout.addWidget(self.btn_auto_translate)

        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName("LangSelector")
        self.lang_combo.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        for code, label in SUPPORTED_LANGUAGES:
            self.lang_combo.addItem(f"🌐 {label}", code)
        idx = self.lang_combo.findData(self.target_lang)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        header_layout.addWidget(self.lang_combo)

        self.btn_theme = QPushButton()
        self.btn_theme.setProperty("class", "PillButton")
        self.btn_theme.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_theme.clicked.connect(self._toggle_theme)
        self._sync_theme_btn()
        header_layout.addWidget(self.btn_theme)

        # Botão Criar Post
        self.btn_create_post = QPushButton("✏️ Criar Post")
        self.btn_create_post.setProperty("class", "PillButtonAccent")
        self.btn_create_post.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_create_post.setToolTip("Criar uma nova publicação no Reddit")
        self.btn_create_post.clicked.connect(self.open_create_post_dialog)
        header_layout.addWidget(self.btn_create_post)

        # Área de Usuário / Login
        self.user_area = QWidget()
        self.user_area_layout = QHBoxLayout(self.user_area)
        self.user_area_layout.setContentsMargins(0, 0, 0, 0)
        self.user_area_layout.setSpacing(8)

        self.btn_login = QPushButton("👤 Entrar")
        self.btn_login.setProperty("class", "PillButtonAccent")
        self.btn_login.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_login.clicked.connect(self.open_login_dialog)
        self.user_area_layout.addWidget(self.btn_login)

        header_layout.addWidget(self.user_area)

        root_layout.addWidget(header)

        # 2. Corpo Principal: Barra Esquerda + Área Central + Barra Direita
        body_widget = QWidget()
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 2A. Sidebar Esquerda (Navegação estilo Reddit Web)
        sidebar = QFrame()
        sidebar.setObjectName("LeftSidebar")
        sidebar.setFixedWidth(240)
        sidebar_outer = QVBoxLayout(sidebar)
        sidebar_outer.setContentsMargins(10, 10, 10, 10)
        sidebar_outer.setSpacing(6)

        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_content = QWidget()
        self.sidebar_layout = QVBoxLayout(sidebar_content)
        self.sidebar_layout.setContentsMargins(2, 2, 2, 2)
        self.sidebar_layout.setSpacing(3)

        lbl_feeds = QLabel("FEEDS")
        lbl_feeds.setObjectName("SidebarSectionTitle")
        self.sidebar_layout.addWidget(lbl_feeds)

        self.feeds_container = QWidget()
        self.feeds_layout = QVBoxLayout(self.feeds_container)
        self.feeds_layout.setContentsMargins(0, 0, 0, 0)
        self.feeds_layout.setSpacing(2)
        self.sidebar_layout.addWidget(self.feeds_container)

        self.nav_buttons: Dict[str, QPushButton] = {}
        self._rebuild_sidebar_feeds()

        lbl_favs = QLabel("COMUNIDADES")
        lbl_favs.setObjectName("SidebarSectionTitle")
        self.sidebar_layout.addWidget(lbl_favs)

        add_sub_row = QHBoxLayout()
        add_sub_row.setSpacing(4)
        self.add_sub_input = QLineEdit()
        self.add_sub_input.setPlaceholderText("Adicionar r/...")
        self.add_sub_input.returnPressed.connect(self._on_add_favorite_from_input)
        add_sub_row.addWidget(self.add_sub_input, 1)

        btn_add_sub = QPushButton("+")
        btn_add_sub.setProperty("class", "PillButton")
        btn_add_sub.setFixedSize(28, 28)
        btn_add_sub.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_add_sub.clicked.connect(self._on_add_favorite_from_input)
        add_sub_row.addWidget(btn_add_sub)
        self.sidebar_layout.addLayout(add_sub_row)

        self.favs_container = QWidget()
        self.favs_layout = QVBoxLayout(self.favs_container)
        self.favs_layout.setContentsMargins(0, 4, 0, 4)
        self.favs_layout.setSpacing(2)
        self.sidebar_layout.addWidget(self.favs_container)

        self.sidebar_layout.addStretch(1)
        sidebar_scroll.setWidget(sidebar_content)
        sidebar_outer.addWidget(sidebar_scroll, 1)

        body_layout.addWidget(sidebar)
        self._rebuild_favorites_sidebar()

        # 2B. Área Central com QStackedWidget
        center_wrapper = QWidget()
        center_layout = QHBoxLayout(center_wrapper)
        center_layout.setContentsMargins(18, 14, 18, 14)
        center_layout.setSpacing(18)

        self.stack = QStackedWidget()
        self._build_feed_page()
        self._build_detail_page()
        center_layout.addWidget(self.stack, 1)

        # 2C. Sidebar Direita
        self._build_right_sidebar()
        center_layout.addWidget(self.right_sidebar)

        body_layout.addWidget(center_wrapper, 1)
        root_layout.addWidget(body_widget, 1)

    def _build_feed_page(self) -> None:
        self.feed_page = QWidget()
        page_layout = QVBoxLayout(self.feed_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(12)

        banner_card = QFrame()
        banner_card.setObjectName("SubredditBannerCard")
        banner_layout = QVBoxLayout(banner_card)
        banner_layout.setContentsMargins(18, 14, 18, 14)
        banner_layout.setSpacing(10)

        top_sub_row = QHBoxLayout()
        top_sub_row.setSpacing(12)

        self.feed_avatar_holder = QHBoxLayout()
        self.feed_avatar_widget = CommunityAvatarWidget("popular", size=38)
        self.feed_avatar_holder.addWidget(self.feed_avatar_widget)
        top_sub_row.addLayout(self.feed_avatar_holder)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.lbl_feed_title = QLabel("r/popular")
        self.lbl_feed_title.setStyleSheet("font-size: 20px; font-weight: 800;")
        title_col.addWidget(self.lbl_feed_title)

        self.lbl_feed_subtitle = QLabel("As postagens mais populares do Reddit agora")
        self.lbl_feed_subtitle.setStyleSheet("color: #8BA2AD; font-size: 12px;")
        title_col.addWidget(self.lbl_feed_subtitle)
        top_sub_row.addLayout(title_col, 1)

        self.btn_fav_toggle = QPushButton("☆ Favoritar")
        self.btn_fav_toggle.setProperty("class", "PillButton")
        self.btn_fav_toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_fav_toggle.clicked.connect(self._toggle_current_subreddit_favorite)
        top_sub_row.addWidget(self.btn_fav_toggle)

        btn_refresh = QPushButton("↻ Atualizar")
        btn_refresh.setProperty("class", "PillButton")
        btn_refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_refresh.clicked.connect(self.refresh_current_feed)
        top_sub_row.addWidget(btn_refresh)

        banner_layout.addLayout(top_sub_row)

        self.sort_bar_widget = QWidget()
        sort_row = QHBoxLayout(self.sort_bar_widget)
        sort_row.setContentsMargins(0, 0, 0, 0)
        sort_row.setSpacing(6)

        self.sort_buttons: Dict[str, QPushButton] = {}
        for sort_key, label in [
            ("hot", "🔥 Em Alta"),
            ("new", "✨ Novos"),
            ("top", "🏆 Topo"),
            ("rising", "📈 Subindo"),
        ]:
            b = QPushButton(label)
            b.setProperty("class", "SortButton")
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.clicked.connect(
                lambda checked=False, sk=sort_key: self._on_sort_selected(sk)
            )
            sort_row.addWidget(b)
            self.sort_buttons[sort_key] = b

        self.time_filter_combo = QComboBox()
        self.time_filter_combo.setObjectName("LangSelector")
        for t_key, t_label in [
            ("day", "Hoje"),
            ("week", "Esta Semana"),
            ("month", "Este Mês"),
            ("year", "Este Ano"),
            ("all", "Desde Sempre"),
        ]:
            self.time_filter_combo.addItem(t_label, t_key)
        self.time_filter_combo.setVisible(False)
        self.time_filter_combo.currentIndexChanged.connect(
            self._on_time_filter_changed
        )
        sort_row.addWidget(self.time_filter_combo)

        sort_row.addStretch(1)
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #8BA2AD; font-size: 12px;")
        sort_row.addWidget(self.lbl_status)

        banner_layout.addWidget(self.sort_bar_widget)
        page_layout.addWidget(banner_card)

        self.sub_search_bar = QFrame()
        self.sub_search_bar.setProperty("class", "DetailCard")
        self.sub_search_bar.setVisible(False)
        self.sub_search_layout = QHBoxLayout(self.sub_search_bar)
        self.sub_search_layout.setContentsMargins(12, 8, 12, 8)
        self.sub_search_layout.setSpacing(8)
        page_layout.addWidget(self.sub_search_bar)

        self.feed_scroll = QScrollArea()
        self.feed_scroll.setWidgetResizable(True)
        self.feed_list_container = QWidget()
        self.feed_list_layout = QVBoxLayout(self.feed_list_container)
        self.feed_list_layout.setContentsMargins(0, 0, 4, 16)
        self.feed_list_layout.setSpacing(12)
        self.feed_list_layout.addStretch(1)
        self.feed_scroll.setWidget(self.feed_list_container)
        page_layout.addWidget(self.feed_scroll, 1)

        self.stack.addWidget(self.feed_page)

    def _build_detail_page(self) -> None:
        self.detail_page = QWidget()
        d_layout = QVBoxLayout(self.detail_page)
        d_layout.setContentsMargins(0, 0, 0, 0)
        d_layout.setSpacing(12)

        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(10)

        btn_back = QPushButton("←  Voltar ao Feed")
        btn_back.setProperty("class", "PillButton")
        btn_back.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_back.clicked.connect(self.show_feed_page)
        nav_bar.addWidget(btn_back)

        self.btn_translate_all_detail = QPushButton(
            "🌐  Traduzir Post e Todos os Comentários"
        )
        self.btn_translate_all_detail.setProperty("class", "TranslateButton")
        self.btn_translate_all_detail.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self.btn_translate_all_detail.clicked.connect(
            self.translate_post_and_all_comments
        )
        nav_bar.addWidget(self.btn_translate_all_detail)

        self.btn_detail_play_video = QPushButton("▶  Assistir Vídeo")
        self.btn_detail_play_video.setProperty("class", "PillButtonAccent")
        self.btn_detail_play_video.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self.btn_detail_play_video.setVisible(False)
        self.btn_detail_play_video.clicked.connect(
            lambda: open_video_stream(
                (self.current_post.video_url or self.current_post.url)
                if self.current_post
                else ""
            )
        )
        nav_bar.addWidget(self.btn_detail_play_video)

        nav_bar.addStretch(1)

        btn_open_web = QPushButton("↗  Abrir no Reddit Web")
        btn_open_web.setProperty("class", "PillButton")
        btn_open_web.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open_web.clicked.connect(self._open_current_post_in_browser)
        nav_bar.addWidget(btn_open_web)

        d_layout.addLayout(nav_bar)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        detail_content = QWidget()
        self.detail_content_layout = QVBoxLayout(detail_content)
        self.detail_content_layout.setContentsMargins(0, 0, 4, 20)
        self.detail_content_layout.setSpacing(14)

        self.detail_post_card = QFrame()
        self.detail_post_card.setProperty("class", "DetailCard")
        dp_layout = QVBoxLayout(self.detail_post_card)
        dp_layout.setContentsMargins(20, 18, 20, 18)
        dp_layout.setSpacing(12)

        self.lbl_detail_meta = QLabel("")
        self.lbl_detail_meta.setStyleSheet(
            "color: #8BA2AD; font-size: 12px; font-weight: 600;"
        )
        dp_layout.addWidget(self.lbl_detail_meta)

        self.lbl_detail_title = QLabel("")
        self.lbl_detail_title.setWordWrap(True)
        self.lbl_detail_title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_detail_title.setStyleSheet(
            "font-size: 20px; font-weight: 800; line-height: 1.35;"
        )
        dp_layout.addWidget(self.lbl_detail_title)

        self.lbl_detail_body = QLabel("")
        self.lbl_detail_body.setWordWrap(True)
        self.lbl_detail_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_detail_body.setStyleSheet("font-size: 14px; line-height: 1.55;")
        dp_layout.addWidget(self.lbl_detail_body)

        self.detail_image_box = QWidget()
        dib_layout = QVBoxLayout(self.detail_image_box)
        dib_layout.setContentsMargins(0, 4, 0, 4)
        dib_layout.setSpacing(8)

        self.lbl_detail_image = QLabel()
        self.lbl_detail_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detail_image.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.lbl_detail_image.mousePressEvent = self._on_detail_image_clicked
        dib_layout.addWidget(self.lbl_detail_image)

        self.gallery_nav_bar = QWidget()
        gal_row = QHBoxLayout(self.gallery_nav_bar)
        gal_row.setContentsMargins(0, 0, 0, 0)
        gal_row.addStretch(1)
        self.btn_gal_prev = QPushButton("◀ Anterior")
        self.btn_gal_prev.setProperty("class", "PillButton")
        self.btn_gal_prev.clicked.connect(lambda: self._step_gallery(-1))
        gal_row.addWidget(self.btn_gal_prev)

        self.lbl_gal_counter = QLabel("1 / 1")
        self.lbl_gal_counter.setStyleSheet("font-weight: 700; padding: 0 10px;")
        gal_row.addWidget(self.lbl_gal_counter)

        self.btn_gal_next = QPushButton("Próxima ▶")
        self.btn_gal_next.setProperty("class", "PillButton")
        self.btn_gal_next.clicked.connect(lambda: self._step_gallery(1))
        gal_row.addWidget(self.btn_gal_next)
        gal_row.addStretch(1)
        dib_layout.addWidget(self.gallery_nav_bar)

        dp_layout.addWidget(self.detail_image_box)

        self.video_player = EmbeddedVideoWidget()
        self.video_player.setVisible(False)
        dp_layout.addWidget(self.video_player)

        self.lbl_detail_stats = QLabel("")
        self.lbl_detail_stats.setStyleSheet(
            "color: #8BA2AD; font-size: 12px; font-weight: 600;"
        )
        dp_layout.addWidget(self.lbl_detail_stats)

        self.detail_content_layout.addWidget(self.detail_post_card)

        self.lbl_comments_header = QLabel("💬 Comentários")
        self.lbl_comments_header.setStyleSheet(
            "font-size: 16px; font-weight: 800; padding-top: 4px;"
        )
        self.detail_content_layout.addWidget(self.lbl_comments_header)

        self.comments_container = QWidget()
        self.comments_layout = QVBoxLayout(self.comments_container)
        self.comments_layout.setContentsMargins(0, 0, 0, 0)
        self.comments_layout.setSpacing(10)
        self.detail_content_layout.addWidget(self.comments_container)

        self.detail_content_layout.addStretch(1)
        self.detail_scroll.setWidget(detail_content)
        d_layout.addWidget(self.detail_scroll, 1)

        self.stack.addWidget(self.detail_page)

    def _build_right_sidebar(self) -> None:
        self.right_sidebar = QFrame()
        self.right_sidebar.setObjectName("RightCommunityCard")
        self.right_sidebar.setFixedWidth(290)
        r_layout = QVBoxLayout(self.right_sidebar)
        r_layout.setContentsMargins(18, 18, 18, 18)
        r_layout.setSpacing(12)

        lbl_about = QLabel("SOBRE A COMUNIDADE")
        lbl_about.setStyleSheet(
            "color: #8BA2AD; font-size: 11px; font-weight: 800; letter-spacing: 0.8px;"
        )
        r_layout.addWidget(lbl_about)

        self.lbl_right_sub_name = QLabel("r/popular")
        self.lbl_right_sub_name.setWordWrap(True)
        self.lbl_right_sub_name.setStyleSheet("font-size: 17px; font-weight: 800;")
        r_layout.addWidget(self.lbl_right_sub_name)

        self.lbl_right_sub_desc = QLabel(
            "As postagens mais ativas e votadas de todo o Reddit agora."
        )
        self.lbl_right_sub_desc.setWordWrap(True)
        self.lbl_right_sub_desc.setStyleSheet(
            "color: #B8C7CE; font-size: 13px; line-height: 1.45;"
        )
        r_layout.addWidget(self.lbl_right_sub_desc)

        self.btn_translate_sub_desc = QPushButton("🌐 Traduzir Descrição")
        self.btn_translate_sub_desc.setProperty("class", "TranslateButton")
        self.btn_translate_sub_desc.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self.btn_translate_sub_desc.clicked.connect(
            self._translate_current_sub_description
        )
        r_layout.addWidget(self.btn_translate_sub_desc)

        stats_frame = QFrame()
        stats_frame.setStyleSheet(
            "border-top: 1px solid #24373E; border-bottom: 1px solid #24373E; padding: 8px 0px;"
        )
        stats_row = QHBoxLayout(stats_frame)
        stats_row.setContentsMargins(0, 4, 0, 4)

        self.lbl_right_members = QLabel("—\nMembros")
        self.lbl_right_members.setStyleSheet("font-size: 12px; font-weight: 700;")
        stats_row.addWidget(self.lbl_right_members)

        self.lbl_right_online = QLabel("● Online")
        self.lbl_right_online.setStyleSheet(
            "color: #46D160; font-size: 12px; font-weight: 700;"
        )
        stats_row.addWidget(self.lbl_right_online)
        r_layout.addWidget(stats_frame)

        tips_title = QLabel("ATALHOS E RECURSOS")
        tips_title.setStyleSheet(
            "color: #8BA2AD; font-size: 11px; font-weight: 800; letter-spacing: 0.8px; padding-top: 6px;"
        )
        r_layout.addWidget(tips_title)

        tips_lbl = QLabel(
            "• <b>Ctrl+T</b>: Traduz toda a tela atual\n"
            "• <b>Ctrl+F</b>: Focar na busca global\n"
            "• <b>F5</b>: Atualizar o feed atual\n"
            "• <b>Esc / Alt+←</b>: Voltar ao feed\n"
            "• Clique em qualquer imagem para ampliar"
        )
        tips_lbl.setWordWrap(True)
        tips_lbl.setStyleSheet("color: #8BA2AD; font-size: 12px; line-height: 1.5;")
        r_layout.addWidget(tips_lbl)

        r_layout.addStretch(1)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search_input.setFocus)
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_current_feed)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.translate_current_view)
        QShortcut(QKeySequence("Escape"), self, activated=self.show_feed_page)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.show_feed_page)

    # -------------------------------------------------------------------------
    # Tema e Configurações Visuais
    # -------------------------------------------------------------------------
    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app:
            app.setPalette(build_qt_palette(dark=self.dark_mode))
        self.setStyleSheet(build_stylesheet(dark=self.dark_mode))

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._sync_theme_btn()
        self._apply_theme()
        self._save_config()

    def _sync_theme_btn(self) -> None:
        if self.dark_mode:
            self.btn_theme.setText("🌙 Escuro")
        else:
            self.btn_theme.setText("☀️ Claro")

    def _toggle_auto_translate(self) -> None:
        self.auto_translate = not self.auto_translate
        self._sync_auto_translate_btn()
        self._save_config()
        if self.auto_translate:
            self.translate_current_view()

    def _sync_auto_translate_btn(self) -> None:
        if self.auto_translate:
            self.btn_auto_translate.setText("🌐 Auto-Traduzir: ON")
            self.btn_auto_translate.setProperty("translated", "true")
        else:
            self.btn_auto_translate.setText("🌐 Auto-Traduzir: OFF")
            self.btn_auto_translate.setProperty("translated", "false")
        self.btn_auto_translate.style().unpolish(self.btn_auto_translate)
        self.btn_auto_translate.style().polish(self.btn_auto_translate)

    def _on_language_changed(self, index: int) -> None:
        code = self.lang_combo.itemData(index)
        if code:
            self.target_lang = str(code)
            self.translator.target_lang = self.target_lang
            self._save_config()
            for card in self.feed_cards:
                if isValid(card):
                    card.post.translated_title = None
                    card.post.translated_selftext = None
            for c_card in self.comment_cards:
                if isValid(c_card):
                    c_card.comment.translated_body = None
            if self.auto_translate:
                self.translate_current_view()

    # -------------------------------------------------------------------------
    # Sessão de Usuário e Autenticação
    # -------------------------------------------------------------------------
    def _rebuild_sidebar_feeds(self) -> None:
        if not hasattr(self, "feeds_layout"):
            return
        while self.feeds_layout.count():
            item = self.feeds_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.feeds_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        feed_items = []
        if self.current_user:
            feed_items.append(("home", "🏠  Início (Meu Feed)"))
        feed_items.extend([
            ("popular", "🔥  Popular"),
            ("all", "🌍  Tudo (r/all)"),
            ("__saved__", "🔖  Posts Salvos"),
        ])

        self.nav_buttons = {}
        for sub_id, title in feed_items:
            btn = QPushButton(title)
            btn.setProperty("class", "NavButton")
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if sub_id == "__saved__":
                btn.clicked.connect(self.show_saved_posts)
            else:
                btn.clicked.connect(
                    lambda checked=False, s=sub_id: self.load_subreddit(s)
                )
            self.feeds_layout.addWidget(btn)
            self.nav_buttons[sub_id] = btn

        for key, btn in self.nav_buttons.items():
            active = False
            if key == "__saved__" and self.is_viewing_saved:
                active = True
            elif (
                not self.is_viewing_saved
                and not self.is_searching
                and self.current_subreddit.lower() == key.lower()
            ):
                active = True
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _update_user_ui(self) -> None:
        if not hasattr(self, "user_area_layout"):
            return
        while self.user_area_layout.count():
            item = self.user_area_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.user_area_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        if self.current_user:
            uname = self.current_user.get("username", "Usuário")
            karma = self.current_user.get("karma", 0)

            user_pill = QFrame()
            user_pill.setProperty("class", "PillButton")
            up_layout = QHBoxLayout(user_pill)
            up_layout.setContentsMargins(8, 2, 8, 2)
            up_layout.setSpacing(6)

            lbl_name = QLabel(f"👤 u/{uname}")
            lbl_name.setStyleSheet("font-weight: 700; font-size: 12px;")
            up_layout.addWidget(lbl_name)

            lbl_karma = QLabel(f"• {format_count(karma)} karma")
            lbl_karma.setStyleSheet("color: #FF4500; font-weight: 700; font-size: 11px;")
            up_layout.addWidget(lbl_karma)

            self.user_area_layout.addWidget(user_pill)

            btn_logout = QPushButton("Sair")
            btn_logout.setProperty("class", "PillButton")
            btn_logout.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_logout.setToolTip("Desconectar conta do Reddit")
            btn_logout.clicked.connect(self._on_logout_clicked)
            self.user_area_layout.addWidget(btn_logout)
        else:
            self.btn_login = QPushButton("👤 Entrar")
            self.btn_login.setProperty("class", "PillButtonAccent")
            self.btn_login.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_login.clicked.connect(self.open_login_dialog)
            self.user_area_layout.addWidget(self.btn_login)

    def _check_login_status_on_startup(self) -> None:
        def _task() -> Optional[Dict[str, Any]]:
            return self.client.fetch_current_user()

        def _done(user: Optional[Dict[str, Any]]) -> None:
            if not self._is_closing and isValid(self):
                self.current_user = user
                self._update_user_ui()
                self._rebuild_sidebar_feeds()

        self._run_async(_task, _done)

    def open_login_dialog(self) -> None:
        dialog = BrowserLoginDialog(self)
        dialog.login_completed.connect(self._on_login_completed)
        dialog.exec()

    def _on_login_completed(self, data: Dict[str, Any]) -> None:
        self.client.apply_session_payload(data)
        self.current_user = self.client.fetch_current_user(force=True)
        self._update_user_ui()
        self._rebuild_sidebar_feeds()
        self.load_subreddit("home", sort="hot")

    def _on_logout_clicked(self) -> None:
        self.client.logout()
        self.current_user = None
        self._update_user_ui()
        self._rebuild_sidebar_feeds()
        if self.current_subreddit.lower() == "home":
            self.load_subreddit("popular", sort="hot")

    def _on_card_vote_requested(self, post: PostItem, direction: int) -> None:
        if not self.current_user:
            self.open_login_dialog()
            return

        def _task() -> bool:
            return self.client.vote(post.id, direction)

        def _done(ok: bool) -> None:
            if not ok and not self._is_closing and isValid(self):
                self.lbl_status.setText("⚠️ Não foi possível registrar o voto.")

        self._run_async(_task, _done)

    def open_create_post_dialog(self) -> None:
        """Abre o diálogo modal para criar e publicar um novo post no Reddit."""
        if not self.current_user:
            self.open_login_dialog()
            return

        initial_sub = self.current_subreddit
        if initial_sub.lower() in ("popular", "all", "home"):
            initial_sub = "linux"

        dialog = CreatePostDialog(
            client=self.client,
            initial_subreddit=initial_sub,
            available_subreddits=self.favorites,
            parent=self,
        )
        dialog.post_created.connect(self._on_post_created)
        dialog.exec()

    def _on_post_created(self, subreddit: str, post_url: str) -> None:
        self.lbl_status.setText(f"✓ Post publicado com sucesso em r/{subreddit}!")
        self.load_subreddit(subreddit, sort="new")

    # -------------------------------------------------------------------------
    # Favoritos e Barra Lateral (com validação e botão de remoção BUG-010)
    # -------------------------------------------------------------------------
    def _rebuild_favorites_sidebar(self) -> None:
        while self.favs_layout.count():
            item = self.favs_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.favs_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        for sub in self.favorites:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            btn = QPushButton(f"r/{sub}")
            btn.setProperty("class", "NavButton")
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            is_active = (
                not self.is_viewing_saved
                and not self.is_searching
                and self.current_subreddit.lower() == sub.lower()
            )
            btn.setProperty("active", "true" if is_active else "false")
            btn.clicked.connect(
                lambda checked=False, s=sub: self.load_subreddit(s)
            )
            row_layout.addWidget(btn, 1)

            btn_del = QPushButton("✕")
            btn_del.setToolTip(f"Remover r/{sub} dos favoritos")
            btn_del.setFixedSize(22, 22)
            btn_del.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_del.setStyleSheet(
                "QPushButton { background: transparent; color: #647C87; border: none; border-radius: 6px; font-size: 11px; }"
                "QPushButton:hover { background-color: #3A1C12; color: #FF585B; }"
            )
            btn_del.clicked.connect(
                lambda checked=False, s=sub: self._remove_favorite_subreddit(s)
            )
            row_layout.addWidget(btn_del)

            self.favs_layout.addWidget(row_widget)

        for key, btn in self.nav_buttons.items():
            active = False
            if key == "__saved__" and self.is_viewing_saved:
                active = True
            elif (
                not self.is_viewing_saved
                and not self.is_searching
                and self.current_subreddit.lower() == key.lower()
            ):
                active = True
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _remove_favorite_subreddit(self, sub: str) -> None:
        self.favorites = [f for f in self.favorites if f.lower() != sub.lower()]
        self._save_config()
        self._sync_fav_button()
        self._rebuild_favorites_sidebar()

    def _on_add_favorite_from_input(self) -> None:
        raw = (
            self.add_sub_input.text()
            .strip()
            .lstrip("/")
            .removeprefix("r/")
            .strip("/")
        )
        if not raw:
            return
        if not is_valid_subreddit_name(raw):
            self.lbl_status.setText("⚠️ Nome inválido (use apenas letras, números e _)")
            return
        self.add_sub_input.clear()
        if raw.lower() not in [f.lower() for f in self.favorites]:
            self.favorites.append(raw)
            self._save_config()
        self.load_subreddit(raw)

    def _toggle_current_subreddit_favorite(self) -> None:
        sub = self.current_subreddit
        if sub.lower() in ("popular", "all", "home") or not is_valid_subreddit_name(sub):
            return
        existing = [f for f in self.favorites if f.lower() == sub.lower()]
        if existing:
            for e in existing:
                self.favorites.remove(e)
        else:
            self.favorites.append(sub)
        self._save_config()
        self._sync_fav_button()
        self._rebuild_favorites_sidebar()

    def _sync_fav_button(self) -> None:
        sub = self.current_subreddit
        if (
            sub.lower() in ("popular", "all", "home")
            or self.is_viewing_saved
            or self.is_searching
            or not is_valid_subreddit_name(sub)
        ):
            self.btn_fav_toggle.setVisible(False)
            return
        self.btn_fav_toggle.setVisible(True)
        is_fav = any(f.lower() == sub.lower() for f in self.favorites)
        if is_fav:
            self.btn_fav_toggle.setText("★ Favorito")
        else:
            self.btn_fav_toggle.setText("☆ Favoritar")

    # -------------------------------------------------------------------------
    # Carregamento de Feeds e Busca (protegidos contra Race Condition BUG-002)
    # -------------------------------------------------------------------------
    def load_subreddit(
        self,
        subreddit: str,
        sort: Optional[str] = None,
        append: bool = False,
    ) -> None:
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        if not clean_sub:
            clean_sub = "popular"

        self._feed_request_id += 1
        req_id = self._feed_request_id

        self.is_viewing_saved = False
        self.is_searching = False
        self.sub_search_bar.setVisible(False)
        self.sort_bar_widget.setVisible(True)

        self.current_subreddit = clean_sub
        if sort:
            self.current_sort = sort

        if not append:
            self.next_after = None
            self.stack.setCurrentWidget(self.feed_page)
            self._clear_feed_cards()
            if clean_sub == "home":
                self.lbl_feed_title.setText("Início")
                self.lbl_feed_subtitle.setText("Postagens das comunidades em que você está inscrito")
            else:
                self.lbl_feed_title.setText(f"r/{clean_sub}")
                self.lbl_feed_subtitle.setText(f"Carregando postagens de r/{clean_sub}...")
            self._replace_feed_avatar(clean_sub)

        self._sync_sort_buttons()
        self._sync_fav_button()
        self._rebuild_favorites_sidebar()
        self.lbl_status.setText("⏳ Carregando feed...")

        after_token = self.next_after if append else None
        sort_mode = self.current_sort
        t_filter = self.current_time_filter

        def _task() -> Any:
            posts, next_tok = self.client.fetch_subreddit_posts(
                subreddit=clean_sub,
                sort=sort_mode,
                time_filter=t_filter,
                after=after_token,
                limit=25,
            )
            sub_info = self.client.fetch_subreddit_info(clean_sub)
            return posts, next_tok, sub_info

        def _on_done(payload: Any) -> None:
            if req_id != self._feed_request_id:
                return
            posts, next_tok, sub_info = payload
            self.next_after = next_tok
            self.lbl_status.setText(f"{len(self.feed_cards) + len(posts)} posts")
            self._update_subreddit_header_and_sidebar(sub_info)
            self._populate_feed_posts(posts, append=append)
            if self.auto_translate:
                self._translate_feed_cards_batch(
                    self.feed_cards[-len(posts) :] if posts else []
                )

        def _on_err(msg: str) -> None:
            if req_id != self._feed_request_id:
                return
            self.lbl_status.setText("⚠️ Erro ao carregar")
            self._show_feed_message(f"Não foi possível carregar r/{clean_sub}:\n{msg}")

        self._run_async(_task, _on_done, _on_err)

    def _replace_feed_avatar(self, name: str) -> None:
        while self.feed_avatar_holder.count():
            item = self.feed_avatar_holder.takeAt(0)
            w = item.widget()
            if w is not None:
                self.feed_avatar_holder.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
        self.feed_avatar_widget = CommunityAvatarWidget(name, size=38)
        self.feed_avatar_holder.addWidget(self.feed_avatar_widget)

    def _update_subreddit_header_and_sidebar(self, info: SubredditInfo) -> None:
        self.current_sub_info = info
        self.lbl_feed_title.setText(info.display_name_prefixed)
        subtitle = info.title if info.title != info.display_name_prefixed else ""
        if info.subscribers:
            subtitle = (
                f"{subtitle} • {format_count(info.subscribers)} membros"
                if subtitle
                else f"{format_count(info.subscribers)} membros"
            )
        self.lbl_feed_subtitle.setText(subtitle or "Comunidade do Reddit")

        self.lbl_right_sub_name.setText(info.title or info.display_name_prefixed)
        desc = info.public_description or "Sem descrição pública disponível."
        self.lbl_right_sub_desc.setText(desc)
        self.btn_translate_sub_desc.setVisible(bool(info.public_description))
        self.btn_translate_sub_desc.setText("🌐 Traduzir Descrição")
        self.btn_translate_sub_desc.setProperty("translated", "false")

        if info.subscribers:
            self.lbl_right_members.setText(
                f"{format_count(info.subscribers)}\nMembros"
            )
        else:
            self.lbl_right_members.setText("Global\nFeed")

        if info.active_users:
            self.lbl_right_online.setText(
                f"● {format_count(info.active_users)} online"
            )
        else:
            self.lbl_right_online.setText("● Ativo agora")

    def _translate_current_sub_description(self) -> None:
        info = self.current_sub_info
        if not info or not info.public_description:
            return
        if info.show_translated and info.translated_description:
            info.show_translated = False
            self.lbl_right_sub_name.setText(info.title or info.display_name_prefixed)
            self.lbl_right_sub_desc.setText(info.public_description)
            self.btn_translate_sub_desc.setText("🌐 Traduzir Descrição")
            self.btn_translate_sub_desc.setProperty("translated", "false")
            self.btn_translate_sub_desc.style().unpolish(self.btn_translate_sub_desc)
            self.btn_translate_sub_desc.style().polish(self.btn_translate_sub_desc)
            return

        self.btn_translate_sub_desc.setText("⏳ Traduzindo...")
        self.btn_translate_sub_desc.setEnabled(False)

        def _task() -> Any:
            t_title = self.translator.translate(info.title)
            t_desc = self.translator.translate(info.public_description)
            return t_title, t_desc

        def _done(res: Any) -> None:
            t_title, t_desc = res
            info.translated_title = t_title
            info.translated_description = t_desc
            info.show_translated = True
            self.lbl_right_sub_name.setText(t_title)
            self.lbl_right_sub_desc.setText(t_desc)
            self.btn_translate_sub_desc.setEnabled(True)
            self.btn_translate_sub_desc.setText("🔄 Ver Original")
            self.btn_translate_sub_desc.setProperty("translated", "true")
            self.btn_translate_sub_desc.style().unpolish(self.btn_translate_sub_desc)
            self.btn_translate_sub_desc.style().polish(self.btn_translate_sub_desc)

        self._run_async(
            _task, _done, lambda _: self.btn_translate_sub_desc.setEnabled(True)
        )

    def _sync_sort_buttons(self) -> None:
        for k, b in self.sort_buttons.items():
            b.setProperty("active", "true" if k == self.current_sort else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        self.time_filter_combo.setVisible(self.current_sort == "top")

    def _on_sort_selected(self, sort_key: str) -> None:
        if self.is_viewing_saved or self.is_searching:
            return
        self.load_subreddit(self.current_subreddit, sort=sort_key)

    def _on_time_filter_changed(self, index: int) -> None:
        t_val = self.time_filter_combo.itemData(index)
        if t_val and self.current_sort == "top":
            self.current_time_filter = str(t_val)
            self.load_subreddit(self.current_subreddit, sort="top")

    def refresh_current_feed(self) -> None:
        if self.is_viewing_saved:
            self.show_saved_posts()
        elif self.is_searching and self.current_search_query:
            self._execute_search(self.current_search_query, append=False)
        else:
            self.load_subreddit(self.current_subreddit, sort=self.current_sort)

    def _on_search_submitted(self) -> None:
        q = self.search_input.text().strip()
        if not q:
            return
        if q.lower().startswith("r/") and " " not in q:
            self.load_subreddit(q)
            return
        self._execute_search(q, append=False)

    def _execute_search(self, query: str, append: bool = False) -> None:
        """Executa pesquisa global com suporte a paginação `after` (BUG-016) e proteção contra corrida (BUG-002)."""
        self._feed_request_id += 1
        req_id = self._feed_request_id

        self.is_searching = True
        self.is_viewing_saved = False
        self.current_search_query = query
        self.stack.setCurrentWidget(self.feed_page)
        self.sort_bar_widget.setVisible(False)
        self._sync_fav_button()
        self._rebuild_favorites_sidebar()

        after_cursor = self.next_after if append else None
        if not append:
            self.next_after = None
            self._clear_feed_cards()
            self.lbl_feed_title.setText(f"Resultados para “{query}”")
            self.lbl_feed_subtitle.setText(
                "Buscando postagens e comunidades no Reddit..."
            )
            self._replace_feed_avatar("search")

        def _task() -> Any:
            subs = (
                self.client.search_subreddits(query, limit=6)
                if not append
                else []
            )
            posts, next_cur = self.client.search_posts(
                query, after=after_cursor, limit=25
            )
            return subs, posts, next_cur

        def _done(res: Any) -> None:
            if req_id != self._feed_request_id:
                return
            subs, posts, next_cur = res
            self.next_after = next_cur
            total_shown = len(self.feed_cards) + len(posts)
            if not append:
                self._render_sub_search_bar(subs)
                self.lbl_feed_subtitle.setText(
                    f"{total_shown} postagens e {len(subs)} comunidades encontradas"
                )
            else:
                self.lbl_feed_subtitle.setText(
                    f"{total_shown} postagens encontradas para “{query}”"
                )
            self._populate_feed_posts(posts, append=append)
            if self.auto_translate and posts:
                self._translate_feed_cards_batch(
                    self.feed_cards[-len(posts) :]
                )

        def _err(msg: str) -> None:
            if req_id != self._feed_request_id:
                return
            self._show_feed_message(f"Erro ao buscar por '{query}':\n{msg}")

        self._run_async(_task, _done, _err)

    def _render_sub_search_bar(self, subs: List[SubredditInfo]) -> None:
        while self.sub_search_layout.count():
            item = self.sub_search_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.sub_search_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        if not subs:
            self.sub_search_bar.setVisible(False)
            return

        lbl = QLabel("Comunidades:")
        lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #8BA2AD;")
        self.sub_search_layout.addWidget(lbl)

        for s in subs[:5]:
            btn = QPushButton(
                f"{s.display_name_prefixed} ({format_count(s.subscribers)})"
            )
            btn.setProperty("class", "PillButton")
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(
                lambda checked=False, name=s.name: self.load_subreddit(name)
            )
            self.sub_search_layout.addWidget(btn)

        self.sub_search_layout.addStretch(1)
        self.sub_search_bar.setVisible(True)

    def show_saved_posts(self) -> None:
        self._feed_request_id += 1
        self.is_viewing_saved = True
        self.is_searching = False
        self.next_after = None
        self.stack.setCurrentWidget(self.feed_page)
        self.sort_bar_widget.setVisible(False)
        self.sub_search_bar.setVisible(False)
        self._sync_fav_button()
        self._rebuild_favorites_sidebar()

        self._clear_feed_cards()
        self._replace_feed_avatar("saved")
        self.lbl_feed_title.setText("🔖 Posts Salvos")
        posts: List[PostItem] = []
        for raw in reversed(list(self.saved_posts_data.values())):
            try:
                # Filtra chaves legadas/desconhecidas com segurança
                valid_keys = PostItem.__dataclass_fields__.keys()
                filtered = {k: v for k, v in raw.items() if k in valid_keys}
                posts.append(PostItem(**filtered))
            except Exception:
                pass
        self.lbl_feed_subtitle.setText(
            f"{len(posts)} postagens salvas localmente neste computador"
        )
        if not posts:
            self._show_feed_message(
                "Você ainda não salvou nenhuma postagem.\nClique em '🔖 Salvar' em qualquer post para guardá-lo aqui."
            )
        else:
            self._populate_feed_posts(posts, append=False)

    # -------------------------------------------------------------------------
    # Renderização dos Cards de Post no Feed (com correção de layout BUG-005)
    # -------------------------------------------------------------------------
    def _clear_feed_cards(self) -> None:
        self.feed_cards.clear()
        while self.feed_list_layout.count():
            item = self.feed_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.feed_list_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
        self.feed_list_layout.addStretch(1)
        self.feed_scroll.verticalScrollBar().setValue(0)

    def _show_feed_message(self, text: str) -> None:
        self._clear_feed_cards()
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #8BA2AD; font-size: 14px; padding: 40px 20px;")
        self.feed_list_layout.insertWidget(0, lbl)

    def _populate_feed_posts(
        self, posts: List[PostItem], append: bool = False
    ) -> None:
        # Remove imediatamente o botão "Carregar mais" do layout antes de calcular insert_pos (BUG-005)
        for i in reversed(range(self.feed_list_layout.count())):
            w = self.feed_list_layout.itemAt(i).widget()
            if w is not None and w.objectName() == "LoadMoreButton":
                self.feed_list_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        insert_pos = max(0, self.feed_list_layout.count() - 1)
        for post in posts:
            cached_t = self.translator.get_cached(post.title)
            if cached_t:
                post.translated_title = cached_t

            is_saved = post.id in self.saved_posts_data
            card = PostCardWidget(
                post=post,
                is_saved=is_saved,
                on_request_image=self._request_image_async,
            )
            card.open_post_requested.connect(self.open_post_detail)
            card.open_subreddit_requested.connect(self.load_subreddit)
            card.translate_post_requested.connect(self.translate_single_post_card)
            card.toggle_save_requested.connect(self._toggle_save_post)
            card.vote_requested.connect(self._on_card_vote_requested)

            self.feed_cards.append(card)
            self.feed_list_layout.insertWidget(insert_pos, card)
            insert_pos += 1

        if self.next_after and not self.is_viewing_saved:
            btn_more = QPushButton("⬇  Carregar mais postagens")
            btn_more.setObjectName("LoadMoreButton")
            btn_more.setProperty("class", "PillButtonAccent")
            btn_more.setFixedHeight(40)
            btn_more.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if self.is_searching:
                btn_more.clicked.connect(
                    lambda: self._execute_search(
                        self.current_search_query, append=True
                    )
                )
            else:
                btn_more.clicked.connect(
                    lambda: self.load_subreddit(
                        self.current_subreddit, sort=self.current_sort, append=True
                    )
                )
            self.feed_list_layout.insertWidget(insert_pos, btn_more)

    def _toggle_save_post(self, post: PostItem) -> None:
        if post.id in self.saved_posts_data:
            del self.saved_posts_data[post.id]
        else:
            self.saved_posts_data[post.id] = asdict(post)
        self._save_config()

    # -------------------------------------------------------------------------
    # Visualização de Post Completo + Comentários (protegida contra Race Condition BUG-009)
    # -------------------------------------------------------------------------
    def show_feed_page(self) -> None:
        self._detail_request_id += 1
        if hasattr(self, "video_player"):
            self.video_player.stop()
        self.stack.setCurrentWidget(self.feed_page)

    def open_post_detail(self, post: PostItem) -> None:
        self._detail_request_id += 1
        req_id = self._detail_request_id

        self.current_post = post
        self.current_comments = []
        self.comment_cards = []
        self._gallery_index = 0
        self._detail_raw_image = None

        self.stack.setCurrentWidget(self.detail_page)
        self.detail_scroll.verticalScrollBar().setValue(0)

        self.btn_detail_play_video.setVisible(post.is_video)
        self.btn_translate_all_detail.setEnabled(True)
        self.btn_translate_all_detail.setText(
            "🌐  Traduzir Post e Todos os Comentários"
        )
        self.btn_translate_all_detail.setProperty("translated", "false")
        self.btn_translate_all_detail.style().unpolish(
            self.btn_translate_all_detail
        )
        self.btn_translate_all_detail.style().polish(self.btn_translate_all_detail)

        self._refresh_detail_post_texts()

        if post.is_video and post.video_url:
            self.video_player.setVisible(True)
            self.video_player.load_video(post.video_url)
            self.detail_image_box.setVisible(False)
        else:
            self.video_player.setVisible(False)
            self.video_player.stop()
            images = (
                post.gallery_images
                if post.gallery_images
                else ([post.highres_image_url] if post.highres_image_url else [])
            )
            if images:
                self.detail_image_box.setVisible(True)
                self.gallery_nav_bar.setVisible(len(images) > 1)
                self._load_detail_gallery_image(images[0], 0, len(images), req_id)
            else:
                self.detail_image_box.setVisible(False)

        while self.comments_layout.count():
            item = self.comments_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.comments_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        self.lbl_comments_header.setText("💬 Carregando comentários...")

        def _task() -> List[CommentItem]:
            return self.client.fetch_post_comments(post, limit=60)

        def _done(comments: List[CommentItem]) -> None:
            # Garante que o usuário ainda está visualizando esta mesma postagem (BUG-009)
            if (
                req_id != self._detail_request_id
                or not self.current_post
                or self.current_post.id != post.id
            ):
                return
            self.current_comments = comments
            self._refresh_detail_post_texts()
            self.lbl_comments_header.setText(
                f"💬 Comentários ({len(comments)} carregados)"
            )
            if not comments:
                empty_lbl = QLabel("Nenhum comentário disponível nesta postagem.")
                empty_lbl.setStyleSheet("color: #8BA2AD; padding: 16px;")
                self.comments_layout.addWidget(empty_lbl)
                return

            for c in comments:
                c_card = CommentCardWidget(c)
                c_card.translate_comment_requested.connect(
                    self.translate_single_comment_card
                )
                self.comment_cards.append(c_card)
                self.comments_layout.addWidget(c_card)

            if self.auto_translate:
                self.translate_post_and_all_comments()

        def _err(msg: str) -> None:
            if (
                req_id != self._detail_request_id
                or not self.current_post
                or self.current_post.id != post.id
            ):
                return
            self.lbl_comments_header.setText("💬 Comentários")
            err_lbl = QLabel(f"Não foi possível carregar os comentários: {msg}")
            err_lbl.setStyleSheet("color: #FF585B; padding: 12px;")
            self.comments_layout.addWidget(err_lbl)

        self._run_async(_task, _done, _err)

    def _refresh_detail_post_texts(self) -> None:
        post = self.current_post
        if not post:
            return
        tr_tag = " • 🌐 Traduzido" if post.show_translated else ""
        self.lbl_detail_meta.setText(
            f"{post.subreddit_prefixed}  •  Postado por u/{post.author}  •  {post.relative_time}{tr_tag}"
        )
        self.lbl_detail_title.setText(post.display_title)
        body = post.display_selftext
        if body:
            self.lbl_detail_body.setText(body)
            self.lbl_detail_body.setVisible(True)
        else:
            self.lbl_detail_body.setVisible(False)
        self.lbl_detail_stats.setText(
            f"⬆ {post.formatted_score} pontos ({int(post.upvote_ratio * 100)}% positivo)   •   💬 {post.formatted_comments} comentários"
        )

    def _load_detail_gallery_image(
        self, url: str, index: int, total: int, req_id: Optional[int] = None
    ) -> None:
        active_req = req_id if req_id is not None else self._detail_request_id
        self.lbl_detail_image.setText("Carregando imagem em alta resolução...")
        self.lbl_detail_image.setPixmap(QPixmap())
        self.lbl_gal_counter.setText(f"{index + 1} / {total}")
        self.btn_gal_prev.setEnabled(index > 0)
        self.btn_gal_next.setEnabled(index < total - 1)

        def _cb(raw: bytes) -> None:
            if active_req != self._detail_request_id or not isValid(
                self.lbl_detail_image
            ):
                return
            self._detail_raw_image = raw
            pix = create_rounded_pixmap(
                raw, max_width=760, max_height=540, radius=12
            )
            if pix:
                self.lbl_detail_image.setText("")
                self.lbl_detail_image.setPixmap(pix)

        self._request_image_async(url, _cb)

    def _step_gallery(self, delta: int) -> None:
        post = self.current_post
        if not post or not post.gallery_images:
            return
        total = len(post.gallery_images)
        new_idx = max(0, min(total - 1, self._gallery_index + delta))
        if new_idx != self._gallery_index:
            self._gallery_index = new_idx
            self._load_detail_gallery_image(
                post.gallery_images[new_idx],
                new_idx,
                total,
                self._detail_request_id,
            )

    def _on_detail_image_clicked(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.current_post
        ):
            if self.current_post.is_video:
                open_video_stream(
                    self.current_post.video_url or self.current_post.url
                )
            elif self._detail_raw_image:
                dlg = ImageViewerDialog(
                    self.current_post.display_title,
                    self._detail_raw_image,
                    self.current_post.highres_image_url or self.current_post.url,
                    self,
                )
                dlg.exec()

    def _open_current_post_in_browser(self) -> None:
        if self.current_post:
            perm = self.current_post.permalink
            if perm.startswith("http"):
                webbrowser.open(perm)
            else:
                if not perm.startswith("/"):
                    perm = "/" + perm
                webbrowser.open(f"https://www.reddit.com{perm}")

    # -------------------------------------------------------------------------
    # Tradução (Individual, em Lote e Automática com validação shiboken6 BUG-001)
    # -------------------------------------------------------------------------
    def translate_single_post_card(
        self, post: PostItem, card: PostCardWidget
    ) -> None:
        if not isValid(card):
            return
        if post.show_translated:
            post.show_translated = False
            card.refresh_translation_ui()
            return

        if post.translated_title and (
            not post.selftext or post.translated_selftext
        ):
            post.show_translated = True
            card.refresh_translation_ui()
            return

        card.set_translating_state(True)

        def _task() -> Any:
            t_title = self.translator.translate(post.title)
            t_body = (
                self.translator.translate(post.selftext[:1500])
                if post.selftext
                else ""
            )
            return t_title, t_body

        def _done(res: Any) -> None:
            t_title, t_body = res
            post.translated_title = t_title
            if post.selftext:
                post.translated_selftext = t_body
            post.show_translated = True
            if isValid(card):
                card.set_translating_state(False)

        self._run_async(
            _task,
            _done,
            lambda _: card.set_translating_state(False) if isValid(card) else None,
        )

    def translate_single_comment_card(
        self, comment: CommentItem, card: CommentCardWidget
    ) -> None:
        if not isValid(card):
            return
        if comment.show_translated:
            comment.show_translated = False
            card.refresh_ui()
            return

        if comment.translated_body:
            comment.show_translated = True
            card.refresh_ui()
            return

        card.set_loading(True)

        def _task() -> str:
            return self.translator.translate(comment.body)

        def _done(tr: str) -> None:
            comment.translated_body = tr
            comment.show_translated = True
            if isValid(card):
                card.set_loading(False)

        self._run_async(
            _task,
            _done,
            lambda _: card.set_loading(False) if isValid(card) else None,
        )

    def translate_current_view(self) -> None:
        """Traduz todos os itens da tela aberta (Feed ou Post+Comentários)."""
        if self.stack.currentWidget() == self.detail_page:
            self.translate_post_and_all_comments()
        else:
            self._translate_feed_cards_batch(self.feed_cards)

    def _translate_feed_cards_batch(
        self, cards: List[PostCardWidget]
    ) -> None:
        valid_cards = [c for c in cards if isValid(c)]
        if not valid_cards:
            return
        self.btn_translate_page.setText("⏳ Traduzindo Feed...")
        self.btn_translate_page.setEnabled(False)

        titles = [c.post.title for c in valid_cards]
        bodies = [
            c.post.selftext[:450] if c.post.selftext else "" for c in valid_cards
        ]

        def _task() -> Any:
            tr_titles = self.translator.translate_batch(titles)
            non_empty_indices = [i for i, b in enumerate(bodies) if b.strip()]
            non_empty_bodies = [bodies[i] for i in non_empty_indices]
            tr_bodies_sub = (
                self.translator.translate_batch(non_empty_bodies)
                if non_empty_bodies
                else []
            )
            tr_bodies_map = dict(zip(non_empty_indices, tr_bodies_sub))
            return tr_titles, tr_bodies_map

        def _done(res: Any) -> None:
            tr_titles, tr_bodies_map = res
            for idx, card in enumerate(valid_cards):
                if not isValid(card):
                    continue
                if idx < len(tr_titles):
                    card.post.translated_title = tr_titles[idx]
                if idx in tr_bodies_map:
                    card.post.translated_selftext = tr_bodies_map[idx]
                card.post.show_translated = True
                card.refresh_translation_ui()

            if isValid(self.btn_translate_page):
                self.btn_translate_page.setEnabled(True)
                self.btn_translate_page.setText("🌐 Traduzir Página")

        def _err(_: str) -> None:
            if isValid(self.btn_translate_page):
                self.btn_translate_page.setEnabled(True)
                self.btn_translate_page.setText("🌐 Traduzir Página")

        self._run_async(_task, _done, _err)

    def translate_post_and_all_comments(self) -> None:
        post = self.current_post
        if not post:
            return

        req_id = self._detail_request_id
        self.btn_translate_all_detail.setText("⏳ Traduzindo post e comentários...")
        self.btn_translate_all_detail.setEnabled(False)

        valid_c_cards = [c for c in self.comment_cards if isValid(c)]
        comment_bodies = [c.comment.body for c in valid_c_cards]

        def _task() -> Any:
            t_title = self.translator.translate(post.title)
            t_body = (
                self.translator.translate(post.selftext)
                if post.selftext
                else ""
            )
            t_comments = (
                self.translator.translate_batch(comment_bodies)
                if comment_bodies
                else []
            )
            return t_title, t_body, t_comments

        def _done(res: Any) -> None:
            if req_id != self._detail_request_id:
                return
            t_title, t_body, t_comments = res
            post.translated_title = t_title
            if post.selftext:
                post.translated_selftext = t_body
            post.show_translated = True
            self._refresh_detail_post_texts()

            for fc in self.feed_cards:
                if isValid(fc) and fc.post.id == post.id:
                    fc.refresh_translation_ui()

            for idx, c_card in enumerate(valid_c_cards):
                if isValid(c_card) and idx < len(t_comments):
                    c_card.comment.translated_body = t_comments[idx]
                    c_card.comment.show_translated = True
                    c_card.refresh_ui()

            if isValid(self.btn_translate_all_detail):
                self.btn_translate_all_detail.setEnabled(True)
                self.btn_translate_all_detail.setText(
                    "✓ Post e Comentários Traduzidos"
                )
                self.btn_translate_all_detail.setProperty("translated", "true")
                self.btn_translate_all_detail.style().unpolish(
                    self.btn_translate_all_detail
                )
                self.btn_translate_all_detail.style().polish(
                    self.btn_translate_all_detail
                )

        def _err(_: str) -> None:
            if isValid(self.btn_translate_all_detail):
                self.btn_translate_all_detail.setEnabled(True)
                self.btn_translate_all_detail.setText(
                    "🌐 Traduzir Post e Todos os Comentários"
                )

        self._run_async(_task, _done, _err)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Reddit Linux Desktop")
    app.setDesktopFileName("reddit-linux-desktop")
    window = RedditLinuxWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
