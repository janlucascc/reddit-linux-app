#!/usr/bin/env python3
"""
Reddit Linux Desktop — Aplicativo gráfico nativo em PySide6 (Qt6).
Redesenho completo Front-End (Sprints 1 a 6):
- Arquitetura de 3 colunas redimensionáveis via QSplitter com hitbox de 4px (CR-02)
- Sidebar esquerda retrátil (240px <-> 64px Icon Rail) e breakpoints responsivos
- Zero estilos hexadecimais inline — 100% gerenciado por ThemeManager singleton (CR-03)
- Feed com alternância Card/Compact, skeletons shimmer e altura reservada de mídia (CR-04)
- Árvore de comentários flat O(N) com clamping em depth=8 e batch fold < 15ms (CR-05)
- Player de vídeo nativo com suporte a MPEG-DASH/HLS e integração MPV+yt-dlp (CR-06)
- Acordeão de Regras da Comunidade, Toast Notifications e atalhos de teclado (J/K/R/Ctrl+K)
"""

from __future__ import annotations
import os

os.environ["MANGOHUD"] = "0"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

from dataclasses import asdict
import json
from pathlib import Path
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional
import webbrowser

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QCursor,
    QKeySequence,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
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
from ui_theme import ThemeManager
from widgets import (
    CommentCardWidget,
    CommentRowWidget,
    CommentsFlatContainer,
    CommunityAvatarWidget,
    EmbeddedVideoWidget,
    ImageViewerDialog,
    PostCardSkeleton,
    PostCardWidget,
    RecentPostItemWidget,
    RedditLogoWidget,
    SnoovatarStatusWidget,
    UiSkillsButton,
    canonical_reddit_url,
    create_reddit_vector_icon,
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
    """Executa chamadas de rede/tradução em background sem bloquear a GUI thread."""

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


class RuleAccordionItem(QFrame):
    """Item expansível de regra da comunidade para a Sidebar Direita."""

    def __init__(
        self, title: str, description: str, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "InfoPanel")
        self._expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self.btn_header = QPushButton(f"▸  {title}")
        self.btn_header.setProperty("class", "SubredditLink")
        self.btn_header.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_header.clicked.connect(self._toggle)
        layout.addWidget(self.btn_header)

        self._raw_title = title
        self.lbl_desc = QLabel(description or "Sem detalhes adicionais.")
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setProperty("role", "caption")
        self.lbl_desc.setVisible(False)
        layout.addWidget(self.lbl_desc)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        prefix = "▾" if self._expanded else "▸"
        self.btn_header.setText(f"{prefix}  {self._raw_title}")
        self.lbl_desc.setVisible(self._expanded)


class RedditLinuxWindow(QMainWindow):
    """Janela principal do cliente Reddit nativo para Linux."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Reddit Linux Desktop")
        self.resize(1380, 880)
        self.setMinimumSize(920, 620)

        self._is_closing: bool = False
        self._feed_request_id: int = 0
        self._detail_request_id: int = 0
        self._sidebar_collapsed: bool = False
        self._auto_collapsed_by_viewport: bool = False

        self.client = RedditClient()
        self.config = self._load_config()
        self.dark_mode: bool = bool(self.config.get("dark_mode", True))
        self.auto_translate: bool = bool(self.config.get("auto_translate", False))
        self.target_lang: str = str(self.config.get("target_lang", "pt"))
        self.view_mode: str = str(self.config.get("view_mode", "card"))
        if self.view_mode not in ("card", "compact"):
            self.view_mode = "card"

        raw_favs = list(self.config.get("favorites", DEFAULT_SUBREDDITS))
        self.favorites: List[str] = [
            f for f in raw_favs if isinstance(f, str) and is_valid_subreddit_name(f)
        ] or list(DEFAULT_SUBREDDITS)

        self.saved_posts_data: Dict[str, Dict[str, Any]] = dict(
            self.config.get("saved_posts", {})
        )
        self.visited_post_ids: set[str] = set(
            self.config.get("visited_posts", [])
        )
        self.recent_posts_data: List[Dict[str, Any]] = list(
            self.config.get("recent_posts", [])
        )
        self._games_expanded: bool = True
        self._custom_feeds_expanded: bool = True
        self._communities_expanded: bool = True
        self._feed_tab_mode: str = "foryou"

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
        self.comment_cards: List[CommentRowWidget] = []
        self.current_sub_info: Optional[SubredditInfo] = None
        self._gallery_index: int = 0
        self._detail_raw_image: Optional[bytes] = None
        self.current_user: Optional[Dict[str, Any]] = self.client.fetch_current_user()
        self.user_subscribed_subs: set[str] = set()
        if self.current_user:
            try:
                subs = self.client.fetch_user_subreddits()
                self.user_subscribed_subs = {s.lower() for s in subs}
            except Exception:
                pass

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
            "view_mode": "card",
            "favorites": DEFAULT_SUBREDDITS,
            "saved_posts": {},
            "visited_posts": [],
            "recent_posts": [],
        }

    def _save_config(self) -> None:
        """Salva configurações de forma atômica via arquivo temporário + os.replace (BUG-008)."""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "dark_mode": self.dark_mode,
                "auto_translate": self.auto_translate,
                "target_lang": self.target_lang,
                "view_mode": self.view_mode,
                "favorites": self.favorites,
                "saved_posts": self.saved_posts_data,
                "visited_posts": list(self.visited_post_ids)[-300:],
                "recent_posts": self.recent_posts_data[-15:],
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
    # Toast Notification Flutuante (Sprint 6)
    # -------------------------------------------------------------------------
    def show_toast(self, message: str, variant: str = "info") -> None:
        """Exibe uma notificação toast flutuante no canto inferior direito."""
        if not hasattr(self, "toast_frame") or not isValid(self.toast_frame):
            return
        self.lbl_toast.setText(message)
        self.toast_frame.setProperty("variant", variant)
        self.toast_frame.style().unpolish(self.toast_frame)
        self.toast_frame.style().polish(self.toast_frame)
        self.toast_frame.adjustSize()
        self._reposition_toast()
        self.toast_frame.setVisible(True)
        self.toast_frame.raise_()
        self._toast_timer.start(3400)

    def _reposition_toast(self) -> None:
        if hasattr(self, "toast_frame") and isValid(self.toast_frame):
            tw = max(280, min(460, self.toast_frame.sizeHint().width() + 24))
            th = max(44, self.toast_frame.sizeHint().height())
            self.toast_frame.setFixedSize(tw, th)
            x = max(16, self.width() - tw - 24)
            y = max(16, self.height() - th - 24)
            self.toast_frame.move(x, y)

    # -------------------------------------------------------------------------
    # Construção da Interface (Sprint 2: Header 60px + QSplitter 3 Colunas)
    # -------------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Barra Superior (Header 56px estilo Reddit Shreddit — Imagem 4)
        header = QFrame()
        header.setObjectName("TopHeader")
        header.setFixedHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)
        header_layout.setSpacing(10)

        self.btn_hamburger = UiSkillsButton("☰")
        self.btn_hamburger.setObjectName("HamburgerButton")
        self.btn_hamburger.setToolTip("Recolher / Expandir barra lateral de navegação")
        self.btn_hamburger.clicked.connect(self.toggle_left_sidebar)
        header_layout.addWidget(self.btn_hamburger)

        brand_btn = QPushButton("reddit")
        brand_btn.setObjectName("BrandButton")
        brand_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        brand_btn.clicked.connect(lambda: self.load_subreddit("popular"))
        header_layout.addWidget(brand_btn)

        # Mantém controles internos instanciados para compatibilidade e menus rápidos
        self.quick_sub_combo = QComboBox()
        self.quick_sub_combo.setVisible(False)
        self.quick_sub_combo.activated.connect(self._on_quick_switcher_activated)

        self.btn_translate_page = UiSkillsButton("🌐 Traduzir Página")
        self.btn_translate_page.setVisible(False)
        self.btn_translate_page.clicked.connect(self.translate_current_view)

        self.btn_auto_translate = UiSkillsButton()
        self.btn_auto_translate.setVisible(False)
        self.btn_auto_translate.clicked.connect(self._toggle_auto_translate)

        self.lang_combo = QComboBox()
        self.lang_combo.setVisible(False)
        for code, label in SUPPORTED_LANGUAGES:
            self.lang_combo.addItem(f"🌐 {label}", code)
        idx = self.lang_combo.findData(self.target_lang)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)

        self.btn_theme = UiSkillsButton()
        self.btn_theme.setVisible(False)
        self.btn_theme.clicked.connect(self._toggle_theme)
        self._sync_theme_btn()
        self._sync_auto_translate_btn()

        header_layout.addStretch(1)

        # Pílula Central de Busca com Snoo Laranja Embutido (#SearchPillContainer — Imagem 4)
        self.search_pill = QFrame()
        self.search_pill.setObjectName("SearchPillContainer")
        self.search_pill.setFixedHeight(40)
        self.search_pill.setMinimumWidth(360)
        self.search_pill.setMaximumWidth(580)
        sp_layout = QHBoxLayout(self.search_pill)
        sp_layout.setContentsMargins(10, 0, 12, 0)
        sp_layout.setSpacing(8)

        snoo_search_icon = RedditLogoWidget(size=24)
        sp_layout.addWidget(snoo_search_icon)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("GlobalSearch")
        self.search_input.setPlaceholderText("Buscar no Reddit")
        self.search_input.returnPressed.connect(self._on_search_submitted)
        sp_layout.addWidget(self.search_input, 1)

        header_layout.addWidget(self.search_pill, 2)
        header_layout.addStretch(1)

        # Bloco Direito de Ações Globais (Foto 5: 文A, AD, 💬, ＋ Criar, 🔔 10, Avatar Snoovatar)
        self.btn_header_translate = UiSkillsButton()
        self.btn_header_translate.setObjectName("HeaderIconButton")
        self.btn_header_translate.setIcon(create_reddit_vector_icon("translate", size=20))
        self.btn_header_translate.setIconSize(QSize(20, 20))
        self.btn_header_translate.setToolTip(
            "Tradução da página, idioma e Auto-Traduzir (Ctrl+T)"
        )
        self.btn_header_translate.clicked.connect(self._show_translation_header_menu)
        header_layout.addWidget(self.btn_header_translate)

        self.btn_header_ads = UiSkillsButton()
        self.btn_header_ads.setObjectName("HeaderIconButton")
        self.btn_header_ads.setIcon(create_reddit_vector_icon("ads", size=20))
        self.btn_header_ads.setIconSize(QSize(20, 20))
        self.btn_header_ads.setToolTip("Reddit Ads (Anuncie no Reddit)")
        self.btn_header_ads.clicked.connect(
            lambda: webbrowser.open("https://ads.reddit.com")
        )
        header_layout.addWidget(self.btn_header_ads)

        self.btn_header_chat = UiSkillsButton()
        self.btn_header_chat.setObjectName("HeaderIconButton")
        self.btn_header_chat.setIcon(create_reddit_vector_icon("chat", size=20))
        self.btn_header_chat.setIconSize(QSize(20, 20))
        self.btn_header_chat.setToolTip("Abrir Reddit Chat")
        self.btn_header_chat.clicked.connect(
            lambda: webbrowser.open("https://chat.reddit.com")
        )
        header_layout.addWidget(self.btn_header_chat)

        self.btn_create_post = UiSkillsButton("Criar")
        self.btn_create_post.setObjectName("HeaderIconButton")
        self.btn_create_post.setIcon(create_reddit_vector_icon("create", size=18))
        self.btn_create_post.setIconSize(QSize(18, 18))
        self.btn_create_post.setToolTip("Criar uma nova publicação no Reddit (Ctrl+N)")
        self.btn_create_post.clicked.connect(self.open_create_post_dialog)
        header_layout.addWidget(self.btn_create_post)

        # Sino de Notificações com Badge Laranja "10" (Imagem 4 & 5)
        notif_wrap = QWidget()
        nw_layout = QHBoxLayout(notif_wrap)
        nw_layout.setContentsMargins(0, 0, 0, 0)
        nw_layout.setSpacing(0)
        self.btn_notifications = UiSkillsButton()
        self.btn_notifications.setObjectName("HeaderIconButton")
        self.btn_notifications.setIcon(create_reddit_vector_icon("bell", size=20))
        self.btn_notifications.setIconSize(QSize(20, 20))
        self.btn_notifications.setToolTip("Notificações e Posts Salvos")
        self.btn_notifications.clicked.connect(self._show_notifications_menu)
        nw_layout.addWidget(self.btn_notifications)

        self.lbl_notif_badge = QLabel("10")
        self.lbl_notif_badge.setObjectName("NotificationBadge")
        self.lbl_notif_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nw_layout.addWidget(
            self.lbl_notif_badge,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        header_layout.addWidget(notif_wrap)

        # Área de Usuário / Avatar Snoovatar com ponto verde online (Imagem 4 & 5)
        self.user_area = QWidget()
        self.user_area_layout = QHBoxLayout(self.user_area)
        self.user_area_layout.setContentsMargins(2, 0, 0, 0)
        self.user_area_layout.setSpacing(6)

        self.btn_login = UiSkillsButton("Entrar")
        self.btn_login.setProperty("class", "PillButtonAccent")
        self.btn_login.clicked.connect(self.open_login_dialog)
        self.user_area_layout.addWidget(self.btn_login)

        self.snoovatar = SnoovatarStatusWidget(size=34)
        self.snoovatar.clicked.connect(self._show_user_profile_menu)
        header_layout.addWidget(self.user_area)
        header_layout.addWidget(self.snoovatar)

        root_layout.addWidget(header)

        # 2. Corpo Principal com QSplitter de 3 Colunas (CR-02: handleWidth = 4px)
        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(4)

        # 2A. Coluna 1: Sidebar Esquerda Retrátil (250px <-> 64px Icon Rail — Imagem 1)
        self.left_sidebar = QFrame()
        self.left_sidebar.setObjectName("LeftSidebar")
        self.left_sidebar.setMinimumWidth(64)
        self.left_sidebar.setMaximumWidth(320)
        self.left_sidebar.resize(250, 700)
        sidebar_outer = QVBoxLayout(self.left_sidebar)
        sidebar_outer.setContentsMargins(8, 10, 8, 10)
        sidebar_outer.setSpacing(4)

        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_content = QWidget()
        self.sidebar_layout = QVBoxLayout(sidebar_content)
        self.sidebar_layout.setContentsMargins(2, 2, 2, 2)
        self.sidebar_layout.setSpacing(4)

        # Bloco Primário de Navegação (Feed principal, Popular, Notícias, Explorar, Criar comunidade)
        self.lbl_feeds_section = QLabel("")
        self.lbl_feeds_section.setVisible(False)

        self.feeds_container = QWidget()
        self.feeds_layout = QVBoxLayout(self.feeds_container)
        self.feeds_layout.setContentsMargins(0, 0, 0, 0)
        self.feeds_layout.setSpacing(2)
        self.sidebar_layout.addWidget(self.feeds_container)

        self.nav_buttons: Dict[str, QPushButton] = {}
        self._rebuild_sidebar_feeds()

        # Divisor 1
        self.div_sidebar_1 = QFrame()
        self.div_sidebar_1.setObjectName("SidebarDivider")
        self.div_sidebar_1.setFixedHeight(1)
        self.sidebar_layout.addWidget(self.div_sidebar_1)

        # Seção Colapsável 1: JOGOS NO REDDIT (Imagem 1)
        self.btn_games_section = QPushButton("JOGOS NO REDDIT                    ⌃")
        self.btn_games_section.setObjectName("SectionCollapseHeader")
        self.btn_games_section.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_games_section.clicked.connect(self._toggle_games_section)
        self.sidebar_layout.addWidget(self.btn_games_section)

        self.games_container = QWidget()
        games_layout = QVBoxLayout(self.games_container)
        games_layout.setContentsMargins(0, 2, 0, 4)
        games_layout.setSpacing(4)

        # Card Destacado Roxo (#7E3AF2): Color Maps — Daily Color Puzzles
        self.featured_game_card = QFrame()
        self.featured_game_card.setObjectName("FeaturedGameCard")
        self.featured_game_card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        fg_layout = QHBoxLayout(self.featured_game_card)
        fg_layout.setContentsMargins(12, 10, 12, 10)
        fg_layout.setSpacing(10)

        fg_avatar = CommunityAvatarWidget("ColorMaps", size=38)
        fg_layout.addWidget(fg_avatar)

        fg_text_col = QVBoxLayout()
        fg_text_col.setSpacing(1)
        lbl_fg_title = QLabel("Color Maps")
        lbl_fg_title.setObjectName("FeaturedGameTitle")
        lbl_fg_sub = QLabel("Daily Color Puzzles")
        lbl_fg_sub.setObjectName("FeaturedGameSub")
        lbl_fg_meta = QLabel("15 mil jogadores mensais")
        lbl_fg_meta.setObjectName("FeaturedGameMeta")
        fg_text_col.addWidget(lbl_fg_title)
        fg_text_col.addWidget(lbl_fg_sub)
        fg_text_col.addWidget(lbl_fg_meta)
        fg_layout.addLayout(fg_text_col, 1)
        self.featured_game_card.mousePressEvent = (
            lambda e: self.load_subreddit("games")
            if e.button() == Qt.MouseButton.LeftButton
            else None
        )
        games_layout.addWidget(self.featured_game_card)

        for g_icon, g_title, g_sub in [
            ("puzzle", "Merge Puzzle Game", "WebGames"),
            ("orbit", "Orbitals", "indiegames"),
            ("glyph", "Glyph", "puzzles"),
            ("gamepad", "Descubra mais", "games"),
        ]:
            g_btn = QPushButton(f"   {g_title}")
            g_btn.setIcon(create_reddit_vector_icon(g_icon, size=18))
            g_btn.setIconSize(QSize(18, 18))
            g_btn.setProperty("class", "NavButton")
            g_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            g_btn.clicked.connect(
                lambda checked=False, s=g_sub: self.load_subreddit(s)
            )
            games_layout.addWidget(g_btn)

        self.sidebar_layout.addWidget(self.games_container)

        # Divisor 2
        self.div_sidebar_2 = QFrame()
        self.div_sidebar_2.setObjectName("SidebarDivider")
        self.div_sidebar_2.setFixedHeight(1)
        self.sidebar_layout.addWidget(self.div_sidebar_2)

        # Seção Colapsável 2: FEEDS PERSONALIZADOS (Imagem 1)
        self.btn_custom_section = QPushButton("FEEDS PERSONALIZADOS         ⌃")
        self.btn_custom_section.setObjectName("SectionCollapseHeader")
        self.btn_custom_section.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_custom_section.clicked.connect(self._toggle_custom_feeds_section)
        self.sidebar_layout.addWidget(self.btn_custom_section)

        self.custom_feeds_container = QWidget()
        cf_layout = QVBoxLayout(self.custom_feeds_container)
        cf_layout.setContentsMargins(0, 0, 0, 2)
        cf_layout.setSpacing(2)

        btn_create_feed = QPushButton("   Criar feed personalizado")
        btn_create_feed.setIcon(create_reddit_vector_icon("plus", size=18))
        btn_create_feed.setIconSize(QSize(18, 18))
        btn_create_feed.setProperty("class", "NavButton")
        btn_create_feed.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_create_feed.clicked.connect(self._focus_add_community_input)
        cf_layout.addWidget(btn_create_feed)

        self.btn_saved_feed = QPushButton("   Posts Salvos")
        self.btn_saved_feed.setIcon(create_reddit_vector_icon("bookmark", size=18))
        self.btn_saved_feed.setIconSize(QSize(18, 18))
        self.btn_saved_feed.setProperty("class", "NavButton")
        self.btn_saved_feed.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_saved_feed.clicked.connect(self.show_saved_posts)
        cf_layout.addWidget(self.btn_saved_feed)

        self.sidebar_layout.addWidget(self.custom_feeds_container)

        # Divisor 3
        self.div_sidebar_3 = QFrame()
        self.div_sidebar_3.setObjectName("SidebarDivider")
        self.div_sidebar_3.setFixedHeight(1)
        self.sidebar_layout.addWidget(self.div_sidebar_3)

        # Seção Colapsável 3: COMUNIDADES
        self.lbl_favs_section = QPushButton("COMUNIDADES                          ⌃")
        self.lbl_favs_section.setObjectName("SectionCollapseHeader")
        self.lbl_favs_section.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.lbl_favs_section.clicked.connect(self._toggle_communities_section)
        self.sidebar_layout.addWidget(self.lbl_favs_section)

        self.add_sub_widget = QWidget()
        add_sub_row = QHBoxLayout(self.add_sub_widget)
        add_sub_row.setContentsMargins(0, 0, 0, 0)
        add_sub_row.setSpacing(4)
        self.add_sub_input = QLineEdit()
        self.add_sub_input.setPlaceholderText("Adicionar r/...")
        self.add_sub_input.returnPressed.connect(self._on_add_favorite_from_input)
        add_sub_row.addWidget(self.add_sub_input, 1)

        btn_add_sub = UiSkillsButton("+")
        btn_add_sub.setProperty("class", "PillButton")
        btn_add_sub.setFixedSize(28, 28)
        btn_add_sub.clicked.connect(self._on_add_favorite_from_input)
        add_sub_row.addWidget(btn_add_sub)
        self.sidebar_layout.addWidget(self.add_sub_widget)

        self.favs_container = QWidget()
        self.favs_layout = QVBoxLayout(self.favs_container)
        self.favs_layout.setContentsMargins(0, 4, 0, 4)
        self.favs_layout.setSpacing(2)
        self.sidebar_layout.addWidget(self.favs_container)

        self.sidebar_layout.addStretch(1)
        sidebar_scroll.setWidget(sidebar_content)
        sidebar_outer.addWidget(sidebar_scroll, 1)

        self.body_splitter.addWidget(self.left_sidebar)
        self._rebuild_favorites_sidebar()

        # 2B. Coluna 2: Área Central com QStackedWidget (Feed Page / Detail Page)
        center_container = QWidget()
        center_layout = QVBoxLayout(center_container)
        center_layout.setContentsMargins(16, 10, 16, 12)
        center_layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.setMaximumWidth(780)  # Largura de leitura confortável (estilo Shreddit)
        self._build_feed_page()
        self._build_detail_page()
        center_layout.addWidget(self.stack, 1, Qt.AlignmentFlag.AlignHCenter)

        self.body_splitter.addWidget(center_container)

        # 2C. Coluna 3: Sidebar Direita (POSTS RECENTES + Sobre a Comunidade + Regras)
        self._build_right_sidebar()
        self.body_splitter.addWidget(self.right_sidebar)

        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)
        self.body_splitter.setSizes([250, 810, 320])

        for i in range(1, self.body_splitter.count()):
            handle = self.body_splitter.handle(i)
            if handle is not None:
                handle.setCursor(QCursor(Qt.CursorShape.SplitHCursor))

        root_layout.addWidget(self.body_splitter, 1)

        # 3. Toast Notification Flutuante
        self.toast_frame = QFrame(self)
        self.toast_frame.setObjectName("ToastNotification")
        self.toast_frame.setProperty("variant", "info")
        self.toast_frame.setVisible(False)
        tf_layout = QHBoxLayout(self.toast_frame)
        tf_layout.setContentsMargins(14, 8, 14, 8)
        self.lbl_toast = QLabel("")
        self.lbl_toast.setProperty("role", "subtitle")
        tf_layout.addWidget(self.lbl_toast)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(
            lambda: self.toast_frame.setVisible(False)
            if isValid(self.toast_frame)
            else None
        )

    def _build_feed_page(self) -> None:
        self.feed_page = QWidget()
        page_layout = QVBoxLayout(self.feed_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(8)

        # 1. Sub-Barra Superior de Filtro "Para você" | "Seguindo" + Seletor de Densidade (Imagem 2)
        feed_top_bar = QWidget()
        ftb_layout = QHBoxLayout(feed_top_bar)
        ftb_layout.setContentsMargins(4, 2, 4, 2)
        ftb_layout.setSpacing(6)

        self.btn_tab_foryou = QPushButton("Para você")
        self.btn_tab_foryou.setObjectName("FeedTabButton")
        self.btn_tab_foryou.setProperty("active", "true")
        self.btn_tab_foryou.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_tab_foryou.clicked.connect(lambda: self._on_feed_tab_selected("foryou"))
        ftb_layout.addWidget(self.btn_tab_foryou)

        self.btn_tab_following = QPushButton("Seguindo")
        self.btn_tab_following.setObjectName("FeedTabButton")
        self.btn_tab_following.setProperty("active", "false")
        self.btn_tab_following.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_tab_following.clicked.connect(
            lambda: self._on_feed_tab_selected("following")
        )
        ftb_layout.addWidget(self.btn_tab_following)

        ftb_layout.addSpacing(12)

        self.sort_bar_widget = QWidget()
        sort_row = QHBoxLayout(self.sort_bar_widget)
        sort_row.setContentsMargins(0, 0, 0, 0)
        sort_row.setSpacing(4)

        self.sort_buttons: Dict[str, QPushButton] = {}
        for sort_key, label in [
            ("hot", "🔥 Em Alta"),
            ("new", "✨ Novos"),
            ("top", "🏆 Topo"),
            ("rising", "📈 Subindo"),
        ]:
            b = UiSkillsButton(label)
            b.setProperty("class", "SortButton")
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
        ftb_layout.addWidget(self.sort_bar_widget)

        ftb_layout.addStretch(1)

        self.lbl_status = QLabel("")
        self.lbl_status.setProperty("role", "caption")
        self.lbl_status.setVisible(False)
        ftb_layout.addWidget(self.lbl_status)

        # Alternância de Modo de Visualização estilo "日 ⌄" (Card vs Compact — Imagem 2)
        self.btn_view_card = UiSkillsButton("日 Cards")
        self.btn_view_card.setProperty("class", "SortButton")
        self.btn_view_card.setToolTip("Visualização completa em Cards (Imagem 2)")
        self.btn_view_card.clicked.connect(lambda: self._set_feed_view_mode("card"))
        ftb_layout.addWidget(self.btn_view_card)

        self.btn_view_compact = UiSkillsButton("☰ Lista")
        self.btn_view_compact.setProperty("class", "SortButton")
        self.btn_view_compact.setToolTip("Visualização densa em linhas")
        self.btn_view_compact.clicked.connect(
            lambda: self._set_feed_view_mode("compact")
        )
        ftb_layout.addWidget(self.btn_view_compact)
        self._sync_view_mode_buttons()

        page_layout.addWidget(feed_top_bar)

        # 2. Banner compacto de contexto da comunidade atual
        banner_card = QFrame()
        banner_card.setObjectName("SubredditBannerCard")
        banner_layout = QVBoxLayout(banner_card)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(6)

        top_sub_row = QHBoxLayout()
        top_sub_row.setSpacing(10)

        self.feed_avatar_holder = QHBoxLayout()
        self.feed_avatar_widget = CommunityAvatarWidget("popular", size=32)
        self.feed_avatar_holder.addWidget(self.feed_avatar_widget)
        top_sub_row.addLayout(self.feed_avatar_holder)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.lbl_feed_title = QLabel("r/popular")
        self.lbl_feed_title.setProperty("role", "title")
        title_col.addWidget(self.lbl_feed_title)

        self.lbl_feed_subtitle = QLabel("As postagens mais populares do Reddit agora")
        self.lbl_feed_subtitle.setProperty("role", "caption")
        title_col.addWidget(self.lbl_feed_subtitle)
        top_sub_row.addLayout(title_col, 1)

        self.btn_fav_toggle = UiSkillsButton("☆ Favoritar")
        self.btn_fav_toggle.setProperty("class", "PillButton")
        self.btn_fav_toggle.clicked.connect(self._toggle_current_subreddit_favorite)
        top_sub_row.addWidget(self.btn_fav_toggle)

        btn_refresh = UiSkillsButton("↻ Atualizar")
        btn_refresh.setProperty("class", "PillButton")
        btn_refresh.clicked.connect(self.refresh_current_feed)
        top_sub_row.addWidget(btn_refresh)

        banner_layout.addLayout(top_sub_row)
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
        self.feed_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed_list_container = QWidget()
        self.feed_list_layout = QVBoxLayout(self.feed_list_container)
        self.feed_list_layout.setContentsMargins(0, 0, 4, 16)
        self.feed_list_layout.setSpacing(8)
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

        btn_back = UiSkillsButton("←  Voltar ao Feed")
        btn_back.setProperty("class", "PillButton")
        btn_back.clicked.connect(self.show_feed_page)
        nav_bar.addWidget(btn_back)

        self.btn_translate_all_detail = UiSkillsButton(
            "🌐  Traduzir Post e Todos os Comentários"
        )
        self.btn_translate_all_detail.setProperty("class", "TranslateButton")
        self.btn_translate_all_detail.clicked.connect(
            self.translate_post_and_all_comments
        )
        nav_bar.addWidget(self.btn_translate_all_detail)

        self.btn_detail_play_video = UiSkillsButton("🔊  Assistir com Áudio no MPV")
        self.btn_detail_play_video.setProperty("class", "PillButtonAccent")
        self.btn_detail_play_video.setVisible(False)
        self.btn_detail_play_video.clicked.connect(
            lambda: open_video_stream(
                (self.current_post.video_url or self.current_post.url)
                if self.current_post
                else "",
                self.current_post.permalink if self.current_post else "",
            )
        )
        nav_bar.addWidget(self.btn_detail_play_video)

        nav_bar.addStretch(1)

        btn_open_web = UiSkillsButton("↗  Abrir no Reddit Web")
        btn_open_web.setProperty("class", "PillButton")
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
        self.lbl_detail_meta.setProperty("role", "caption")
        dp_layout.addWidget(self.lbl_detail_meta)

        self.lbl_detail_title = QLabel("")
        self.lbl_detail_title.setWordWrap(True)
        self.lbl_detail_title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_detail_title.setProperty("role", "hero")
        dp_layout.addWidget(self.lbl_detail_title)

        self.lbl_detail_body = QLabel("")
        self.lbl_detail_body.setWordWrap(True)
        self.lbl_detail_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_detail_body.setProperty("role", "body")
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
        self.btn_gal_prev = UiSkillsButton("◀ Anterior")
        self.btn_gal_prev.setProperty("class", "PillButton")
        self.btn_gal_prev.clicked.connect(lambda: self._step_gallery(-1))
        gal_row.addWidget(self.btn_gal_prev)

        self.lbl_gal_counter = QLabel("1 / 1")
        self.lbl_gal_counter.setProperty("role", "subtitle")
        gal_row.addWidget(self.lbl_gal_counter)

        self.btn_gal_next = UiSkillsButton("Próxima ▶")
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
        self.lbl_detail_stats.setProperty("role", "caption")
        dp_layout.addWidget(self.lbl_detail_stats)

        self.detail_content_layout.addWidget(self.detail_post_card)

        # Compositor de Comentário de Topo (Sprint 4)
        self.top_comment_card = QFrame()
        self.top_comment_card.setProperty("class", "DetailCard")
        tc_layout = QVBoxLayout(self.top_comment_card)
        tc_layout.setContentsMargins(14, 12, 14, 12)
        tc_layout.setSpacing(8)

        self.top_comment_input = QPlainTextEdit()
        self.top_comment_input.setPlaceholderText(
            "Adicione um comentário a esta discussão (suporta Markdown)..."
        )
        self.top_comment_input.setFixedHeight(76)
        tc_layout.addWidget(self.top_comment_input)

        tc_btn_row = QHBoxLayout()
        tc_btn_row.addStretch(1)
        self.btn_submit_top_comment = UiSkillsButton("💬 Publicar Comentário")
        self.btn_submit_top_comment.setProperty("class", "PillButtonAccent")
        self.btn_submit_top_comment.clicked.connect(self._on_submit_top_comment)
        tc_btn_row.addWidget(self.btn_submit_top_comment)
        tc_layout.addLayout(tc_btn_row)

        self.detail_content_layout.addWidget(self.top_comment_card)

        self.lbl_comments_header = QLabel("💬 Comentários")
        self.lbl_comments_header.setProperty("role", "title")
        self.detail_content_layout.addWidget(self.lbl_comments_header)

        # Container Flat O(N) para Árvore de Comentários (CR-05)
        self.comments_flat = CommentsFlatContainer()
        self.comments_flat.translate_comment_requested.connect(
            self.translate_single_comment_card
        )
        self.comments_flat.vote_comment_requested.connect(
            self._on_comment_vote_requested
        )
        self.comments_flat.reply_comment_submitted.connect(
            self._on_inline_reply_submitted
        )
        self.detail_content_layout.addWidget(self.comments_flat)

        self.lbl_comments_status_msg = QLabel("")
        self.lbl_comments_status_msg.setProperty("role", "caption")
        self.lbl_comments_status_msg.setVisible(False)
        self.detail_content_layout.addWidget(self.lbl_comments_status_msg)

        self.detail_content_layout.addStretch(1)
        self.detail_scroll.setWidget(detail_content)
        d_layout.addWidget(self.detail_scroll, 1)

        self.stack.addWidget(self.detail_page)

    def _build_right_sidebar(self) -> None:
        self.right_sidebar = QFrame()
        self.right_sidebar.setObjectName("RightCommunityCard")
        self.right_sidebar.setMinimumWidth(250)
        self.right_sidebar.setMaximumWidth(370)
        r_outer = QVBoxLayout(self.right_sidebar)
        r_outer.setContentsMargins(10, 10, 10, 10)
        r_outer.setSpacing(0)

        r_scroll = QScrollArea()
        r_scroll.setWidgetResizable(True)
        r_content = QWidget()
        r_layout = QVBoxLayout(r_content)
        r_layout.setContentsMargins(6, 4, 6, 4)
        r_layout.setSpacing(12)

        # 1. Card de "POSTS RECENTES" com botão azul "Limpar" (Imagem 5)
        self.recent_posts_card = QFrame()
        self.recent_posts_card.setObjectName("RecentPostsCard")
        rpc_layout = QVBoxLayout(self.recent_posts_card)
        rpc_layout.setContentsMargins(14, 12, 14, 12)
        rpc_layout.setSpacing(6)

        rpc_header = QHBoxLayout()
        rpc_header.setContentsMargins(0, 0, 0, 4)
        lbl_recent_title = QLabel("POSTS RECENTES")
        lbl_recent_title.setObjectName("SidebarSectionTitle")
        rpc_header.addWidget(lbl_recent_title)
        rpc_header.addStretch(1)

        self.btn_clear_recent = QPushButton("Limpar")
        self.btn_clear_recent.setObjectName("RecentClearBtn")
        self.btn_clear_recent.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_clear_recent.clicked.connect(self._clear_recent_posts)
        rpc_header.addWidget(self.btn_clear_recent)
        rpc_layout.addLayout(rpc_header)

        self.recent_posts_container = QWidget()
        self.recent_posts_layout = QVBoxLayout(self.recent_posts_container)
        self.recent_posts_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_posts_layout.setSpacing(0)
        rpc_layout.addWidget(self.recent_posts_container)

        r_layout.addWidget(self.recent_posts_card)
        self._rebuild_recent_posts_sidebar()

        # 2. Card "SOBRE A COMUNIDADE" + Regras em Acordeão
        lbl_about = QLabel("SOBRE A COMUNIDADE")
        lbl_about.setObjectName("SidebarSectionTitle")
        r_layout.addWidget(lbl_about)

        self.lbl_right_sub_name = QLabel("r/popular")
        self.lbl_right_sub_name.setWordWrap(True)
        self.lbl_right_sub_name.setProperty("role", "title")
        r_layout.addWidget(self.lbl_right_sub_name)

        self.lbl_right_sub_desc = QLabel(
            "As postagens mais ativas e votadas de todo o Reddit agora."
        )
        self.lbl_right_sub_desc.setWordWrap(True)
        self.lbl_right_sub_desc.setProperty("role", "body_preview")
        r_layout.addWidget(self.lbl_right_sub_desc)

        self.btn_translate_sub_desc = UiSkillsButton("🌐 Traduzir Descrição")
        self.btn_translate_sub_desc.setProperty("class", "TranslateButton")
        self.btn_translate_sub_desc.clicked.connect(
            self._translate_current_sub_description
        )
        r_layout.addWidget(self.btn_translate_sub_desc)

        stats_frame = QFrame()
        stats_frame.setProperty("class", "InfoPanel")
        stats_row = QHBoxLayout(stats_frame)
        stats_row.setContentsMargins(10, 8, 10, 8)

        self.lbl_right_members = QLabel("—\nMembros")
        self.lbl_right_members.setProperty("role", "subtitle")
        stats_row.addWidget(self.lbl_right_members)

        self.lbl_right_online = QLabel("● Online")
        self.lbl_right_online.setProperty("role", "status_ok")
        stats_row.addWidget(self.lbl_right_online)
        r_layout.addWidget(stats_frame)

        # Acordeão de Regras da Comunidade (Sprint 2)
        self.lbl_rules_title = QLabel("REGRAS DA COMUNIDADE")
        self.lbl_rules_title.setObjectName("SidebarSectionTitle")
        self.lbl_rules_title.setVisible(False)
        r_layout.addWidget(self.lbl_rules_title)

        self.rules_container = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_container)
        self.rules_layout.setContentsMargins(0, 0, 0, 0)
        self.rules_layout.setSpacing(4)
        self.rules_container.setVisible(False)
        r_layout.addWidget(self.rules_container)

        tips_title = QLabel("ATALHOS DE TECLADO")
        tips_title.setObjectName("SidebarSectionTitle")
        r_layout.addWidget(tips_title)

        tips_lbl = QLabel(
            "• <b>Ctrl+K / Ctrl+F</b>: Busca global\n"
            "• <b>Ctrl+T</b>: Traduzir tela atual\n"
            "• <b>Ctrl+N</b>: Criar publicação\n"
            "• <b>J / K</b>: Rolar feed abaixo / acima\n"
            "• <b>R / F5</b>: Atualizar feed\n"
            "• <b>Esc</b>: Voltar ao feed"
        )
        tips_lbl.setWordWrap(True)
        tips_lbl.setProperty("role", "caption")
        r_layout.addWidget(tips_lbl)

        r_layout.addStretch(1)
        r_scroll.setWidget(r_content)
        r_outer.addWidget(r_scroll, 1)

    def _record_recent_post(self, post: PostItem, save: bool = True) -> None:
        """Registra um post no histórico lateral 'POSTS RECENTES' (Imagem 5)."""
        try:
            payload = asdict(post)
            self.recent_posts_data = [
                p for p in self.recent_posts_data if p.get("id") != post.id
            ]
            self.recent_posts_data.insert(0, payload)
            self.recent_posts_data = self.recent_posts_data[:12]
            if save:
                self._save_config()
            self._rebuild_recent_posts_sidebar()
        except Exception:
            pass

    def _clear_recent_posts(self) -> None:
        self.recent_posts_data.clear()
        self._save_config()
        self._rebuild_recent_posts_sidebar()
        self.show_toast("Histórico de posts recentes limpo.", variant="info")

    def _rebuild_recent_posts_sidebar(self) -> None:
        if not hasattr(self, "recent_posts_layout") or not isValid(
            self.recent_posts_container
        ):
            return
        while self.recent_posts_layout.count():
            item = self.recent_posts_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.recent_posts_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        valid_keys = PostItem.__dataclass_fields__.keys()
        shown = 0
        for raw in self.recent_posts_data[:6]:
            try:
                filtered = {k: v for k, v in raw.items() if k in valid_keys}
                post_obj = PostItem(**filtered)
                item_w = RecentPostItemWidget(post_obj)
                item_w.open_post_requested.connect(self.open_post_detail)
                item_w.open_subreddit_requested.connect(self.load_subreddit)
                self.recent_posts_layout.addWidget(item_w)
                shown += 1
            except Exception:
                pass

        if shown == 0:
            empty_lbl = QLabel(
                "Os posts que você visualizar aparecerão aqui."
            )
            empty_lbl.setWordWrap(True)
            empty_lbl.setProperty("role", "caption")
            self.recent_posts_layout.addWidget(empty_lbl)

    def _show_translation_header_menu(self) -> None:
        menu = QMenu(self)
        act_now = menu.addAction("🌐 Traduzir Página Atual (Ctrl+T)")
        auto_state = "ON ✓" if self.auto_translate else "OFF"
        act_auto = menu.addAction(f"⚡ Auto-Traduzir ao Abrir: {auto_state}")
        menu.addSeparator()
        lang_Actions = {}
        for code, label in SUPPORTED_LANGUAGES:
            prefix = "● " if code == self.target_lang else "   "
            act = menu.addAction(f"{prefix}Idioma: {label}")
            lang_Actions[act] = code

        chosen = menu.exec(QCursor.pos())
        if chosen == act_now:
            self.translate_current_view()
        elif chosen == act_auto:
            self._toggle_auto_translate()
        elif chosen in lang_Actions:
            code = lang_Actions[chosen]
            idx = self.lang_combo.findData(code)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)

    def _show_quick_communities_menu(self) -> None:
        menu = QMenu(self)
        theme_txt = "☀️ Mudar para Tema Claro" if self.dark_mode else "🌙 Mudar para Tema Escuro"
        act_theme = menu.addAction(theme_txt)
        menu.addSeparator()
        act_home = menu.addAction("🏠 Feed principal")
        act_pop = menu.addAction("↗ Popular (r/popular)")
        act_all = menu.addAction("🫧 Explorar (r/all)")
        act_saved = menu.addAction("🔖 Posts Salvos")
        menu.addSeparator()
        fav_actions = {}
        for fav in self.favorites:
            act = menu.addAction(f"r/{fav}")
            fav_actions[act] = fav

        chosen = menu.exec(QCursor.pos())
        if chosen == act_theme:
            self._toggle_theme()
        elif chosen == act_home:
            self.load_subreddit("home" if self.current_user else "popular")
        elif chosen == act_pop:
            self.load_subreddit("popular")
        elif chosen == act_all:
            self.load_subreddit("all")
        elif chosen == act_saved:
            self.show_saved_posts()
        elif chosen in fav_actions:
            self.load_subreddit(fav_actions[chosen])

    def _show_notifications_menu(self) -> None:
        menu = QMenu(self)
        act_saved = menu.addAction(f"🔖 Ver Posts Salvos ({len(self.saved_posts_data)})")
        act_clear_badge = menu.addAction("✓ Marcar notificações como lidas")
        act_inbox = menu.addAction("🌐 Abrir Inbox no Reddit Web")
        chosen = menu.exec(QCursor.pos())
        if chosen == act_saved:
            self.show_saved_posts()
        elif chosen == act_clear_badge:
            if hasattr(self, "lbl_notif_badge") and isValid(self.lbl_notif_badge):
                self.lbl_notif_badge.setVisible(False)
            self.show_toast("Notificações marcadas como lidas.", variant="success")
        elif chosen == act_inbox:
            webbrowser.open("https://www.reddit.com/notifications")

    def _show_user_profile_menu(self) -> None:
        menu = QMenu(self)
        if self.current_user:
            uname = self.current_user.get("username", "Usuário")
            karma = self.current_user.get("karma", 0)
            hdr = menu.addAction(f"👤 u/{uname} ({format_count(karma)} karma)")
            hdr.setEnabled(False)
            menu.addSeparator()
            act_saved = menu.addAction("🔖 Posts Salvos")
            act_theme = menu.addAction(
                "☀️ Tema Claro" if self.dark_mode else "🌙 Tema Escuro"
            )
            menu.addSeparator()
            act_logout = menu.addAction("🚪 Encerrar Sessão (Sair)")
            chosen = menu.exec(QCursor.pos())
            if chosen == act_saved:
                self.show_saved_posts()
            elif chosen == act_theme:
                self._toggle_theme()
            elif chosen == act_logout:
                self._on_logout_clicked()
        else:
            act_login = menu.addAction("🔑 Entrar na Conta do Reddit...")
            act_theme = menu.addAction(
                "☀️ Tema Claro" if self.dark_mode else "🌙 Tema Escuro"
            )
            chosen = menu.exec(QCursor.pos())
            if chosen == act_login:
                self.open_login_dialog()
            elif chosen == act_theme:
                self._toggle_theme()

    def _toggle_games_section(self) -> None:
        self._games_expanded = not self._games_expanded
        arrow = "⌃" if self._games_expanded else "⌄"
        self.btn_games_section.setText(f"JOGOS NO REDDIT                    {arrow}")
        self.games_container.setVisible(
            self._games_expanded and not self._sidebar_collapsed
        )

    def _toggle_custom_feeds_section(self) -> None:
        self._custom_feeds_expanded = not self._custom_feeds_expanded
        arrow = "⌃" if self._custom_feeds_expanded else "⌄"
        self.btn_custom_section.setText(f"FEEDS PERSONALIZADOS         {arrow}")
        self.custom_feeds_container.setVisible(
            self._custom_feeds_expanded and not self._sidebar_collapsed
        )

    def _toggle_communities_section(self) -> None:
        self._communities_expanded = not self._communities_expanded
        arrow = "⌃" if self._communities_expanded else "⌄"
        self.lbl_favs_section.setText(f"COMUNIDADES                          {arrow}")
        self.add_sub_widget.setVisible(
            self._communities_expanded and not self._sidebar_collapsed
        )
        self.favs_container.setVisible(self._communities_expanded)

    def _focus_add_community_input(self) -> None:
        if self._sidebar_collapsed:
            self.toggle_left_sidebar()
        if not self._communities_expanded:
            self._toggle_communities_section()
        self.add_sub_input.setFocus()

    def _on_feed_tab_selected(self, tab_mode: str) -> None:
        self._feed_tab_mode = tab_mode
        for key, btn in (
            ("foryou", self.btn_tab_foryou),
            ("following", self.btn_tab_following),
        ):
            if isValid(btn):
                btn.setProperty("active", "true" if key == tab_mode else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        if tab_mode == "foryou":
            self.load_subreddit("popular", sort="hot")
        else:
            if self.current_user:
                self.load_subreddit("home", sort="hot")
            elif self.favorites:
                joined = "+".join(self.favorites[:8])
                self.load_subreddit(joined, sort="hot")
            else:
                self.load_subreddit("popular", sort="hot")

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search_input.setFocus)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.search_input.setFocus)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.open_create_post_dialog)
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_current_feed)
        QShortcut(QKeySequence("R"), self, activated=self._shortcut_refresh)
        QShortcut(QKeySequence("J"), self, activated=lambda: self._scroll_feed_step(280))
        QShortcut(QKeySequence("K"), self, activated=lambda: self._scroll_feed_step(-280))
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.translate_current_view)
        QShortcut(QKeySequence("Escape"), self, activated=self.show_feed_page)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.show_feed_page)

    def _shortcut_refresh(self) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QPlainTextEdit)):
            return
        self.refresh_current_feed()

    def _scroll_feed_step(self, delta_px: int) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QPlainTextEdit)):
            return
        active_scroll = (
            self.detail_scroll
            if self.stack.currentWidget() == self.detail_page
            else self.feed_scroll
        )
        sb = active_scroll.verticalScrollBar()
        sb.setValue(sb.value() + delta_px)

    # -------------------------------------------------------------------------
    # Responsividade, Sidebar Retrátil e Modo de Visualização (Sprint 2 & 3)
    # -------------------------------------------------------------------------
    def toggle_left_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        self._apply_sidebar_collapse_state()

    def _apply_sidebar_collapse_state(self) -> None:
        collapsed = self._sidebar_collapsed
        if isValid(self.left_sidebar):
            if collapsed:
                self.left_sidebar.setFixedWidth(64)
            else:
                self.left_sidebar.setMinimumWidth(190)
                self.left_sidebar.setMaximumWidth(320)
                self.left_sidebar.resize(250, self.left_sidebar.height())

        for w in (
            self.btn_games_section,
            self.games_container,
            self.div_sidebar_1,
            self.div_sidebar_2,
            self.div_sidebar_3,
            self.btn_custom_section,
            self.custom_feeds_container,
            self.lbl_favs_section,
            self.add_sub_widget,
        ):
            if isValid(w):
                if w is self.games_container:
                    w.setVisible((not collapsed) and self._games_expanded)
                elif w is self.custom_feeds_container:
                    w.setVisible((not collapsed) and self._custom_feeds_expanded)
                elif w is self.add_sub_widget:
                    w.setVisible((not collapsed) and self._communities_expanded)
                else:
                    w.setVisible(not collapsed)

        self._rebuild_sidebar_feeds()
        self._rebuild_favorites_sidebar()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reposition_toast()
        w = event.size().width()
        if hasattr(self, "right_sidebar") and isValid(self.right_sidebar):
            self.right_sidebar.setVisible(w >= 1060)
        if w < 1000 and not self._sidebar_collapsed:
            self._sidebar_collapsed = True
            self._auto_collapsed_by_viewport = True
            self._apply_sidebar_collapse_state()
        elif w >= 1180 and self._auto_collapsed_by_viewport and self._sidebar_collapsed:
            self._sidebar_collapsed = False
            self._auto_collapsed_by_viewport = False
            self._apply_sidebar_collapse_state()

    def _set_feed_view_mode(self, mode: str) -> None:
        if mode not in ("card", "compact"):
            return
        self.view_mode = mode
        self._sync_view_mode_buttons()
        self._save_config()
        for card in self.feed_cards:
            if isValid(card):
                card.set_view_mode(mode)

    def _sync_view_mode_buttons(self) -> None:
        for key, btn in (("card", self.btn_view_card), ("compact", self.btn_view_compact)):
            if isValid(btn):
                btn.setProperty("active", "true" if self.view_mode == key else "false")
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def _on_quick_switcher_activated(self, index: int) -> None:
        val = self.quick_sub_combo.itemData(index)
        if not val:
            return
        if val == "__saved__":
            self.show_saved_posts()
        else:
            self.load_subreddit(str(val))

    def _sync_quick_switcher(self) -> None:
        if not hasattr(self, "quick_sub_combo") or not isValid(self.quick_sub_combo):
            return
        self.quick_sub_combo.blockSignals(True)
        self.quick_sub_combo.clear()
        if self.current_user:
            self.quick_sub_combo.addItem("🏠 Início", "home")
        self.quick_sub_combo.addItem("🔥 r/popular", "popular")
        self.quick_sub_combo.addItem("🌍 r/all", "all")
        self.quick_sub_combo.addItem("🔖 Salvos", "__saved__")
        for fav in self.favorites:
            self.quick_sub_combo.addItem(f"r/{fav}", fav)

        target_key = "__saved__" if self.is_viewing_saved else self.current_subreddit
        idx = self.quick_sub_combo.findData(target_key)
        if idx >= 0:
            self.quick_sub_combo.setCurrentIndex(idx)
        self.quick_sub_combo.blockSignals(False)

    # -------------------------------------------------------------------------
    # Tema e Configurações Visuais (ThemeManager Singleton — CR-03)
    # -------------------------------------------------------------------------
    def _apply_theme(self) -> None:
        ThemeManager.instance().set_dark_mode(self.dark_mode, force=True)
        self._sync_header_icons()
        self._rebuild_sidebar_feeds()

    def _sync_header_icons(self) -> None:
        color = ThemeManager.instance().tokens.text_primary
        if hasattr(self, "btn_header_translate") and isValid(self.btn_header_translate):
            self.btn_header_translate.setIcon(
                create_reddit_vector_icon("translate", color, size=20)
            )
        if hasattr(self, "btn_header_ads") and isValid(self.btn_header_ads):
            self.btn_header_ads.setIcon(
                create_reddit_vector_icon("ads", color, size=20)
            )
        if hasattr(self, "btn_header_chat") and isValid(self.btn_header_chat):
            self.btn_header_chat.setIcon(
                create_reddit_vector_icon("chat", color, size=20)
            )
        if hasattr(self, "btn_create_post") and isValid(self.btn_create_post):
            self.btn_create_post.setIcon(
                create_reddit_vector_icon("create", color, size=18)
            )
        if hasattr(self, "btn_notifications") and isValid(self.btn_notifications):
            self.btn_notifications.setIcon(
                create_reddit_vector_icon("bell", color, size=20)
            )

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

        # Bloco Primário de Navegação (Foto 3: Ícones Minimalistas Vetoriais):
        # home, popular, news, explore, plus
        home_target = "home" if self.current_user else "popular"
        feed_items = [
            (home_target, "home", "Feed principal"),
            ("popular", "popular", "Popular"),
            ("worldnews", "news", "Notícias"),
            ("all", "explore", "Explorar"),
            ("__create_comm__", "plus", "Criar comunidade"),
        ]

        self.nav_buttons = {}
        for sub_id, icon_name, label_title in feed_items:
            display_txt = "" if self._sidebar_collapsed else f"   {label_title}"
            btn = QPushButton(display_txt)
            btn.setIcon(create_reddit_vector_icon(icon_name, size=20))
            btn.setIconSize(QSize(20, 20))
            btn.setProperty("class", "NavButton")
            btn.setProperty(
                "collapsed", "true" if self._sidebar_collapsed else "false"
            )
            btn.setToolTip(label_title)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if sub_id == "__create_comm__":
                btn.clicked.connect(self._focus_add_community_input)
            else:
                btn.clicked.connect(
                    lambda checked=False, s=sub_id: self.load_subreddit(s)
                )
            self.feeds_layout.addWidget(btn)
            key_name = "home_feed" if label_title == "Feed principal" else sub_id
            self.nav_buttons[key_name] = btn

        self._sync_sidebar_active_states()

    def _sync_sidebar_active_states(self) -> None:
        for key, btn in self.nav_buttons.items():
            if not isValid(btn):
                continue
            active = False
            if not self.is_viewing_saved and not self.is_searching:
                if key == "home_feed" and self.current_subreddit.lower() == "home":
                    active = True
                elif key.lower() == self.current_subreddit.lower():
                    active = True
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if hasattr(self, "btn_saved_feed") and isValid(self.btn_saved_feed):
            self.btn_saved_feed.setProperty(
                "active", "true" if self.is_viewing_saved else "false"
            )
            self.btn_saved_feed.style().unpolish(self.btn_saved_feed)
            self.btn_saved_feed.style().polish(self.btn_saved_feed)

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
            if hasattr(self, "snoovatar") and isValid(self.snoovatar):
                self.snoovatar.set_logged_in(True, username=uname)
                self.snoovatar.setToolTip(
                    f"u/{uname} • {format_count(karma)} karma\nClique para abrir perfil e opções"
                )
                self.snoovatar.setVisible(True)
        else:
            if hasattr(self, "snoovatar") and isValid(self.snoovatar):
                self.snoovatar.set_logged_in(False)
                self.snoovatar.setToolTip("Não conectado • Clique para entrar")
            self.btn_login = UiSkillsButton("Entrar")
            self.btn_login.setProperty("class", "PillButtonAccent")
            self.btn_login.clicked.connect(self.open_login_dialog)
            self.user_area_layout.addWidget(self.btn_login)

    def _check_login_status_on_startup(self) -> None:
        def _task() -> tuple[Optional[Dict[str, Any]], List[str]]:
            user = self.client.fetch_current_user()
            subs = self.client.fetch_user_subreddits() if user else []
            return user, subs

        def _done(res: tuple[Optional[Dict[str, Any]], List[str]]) -> None:
            if not self._is_closing and isValid(self):
                user, subs = res
                self.current_user = user
                self.user_subscribed_subs = {s.lower() for s in subs}
                self._update_user_ui()
                self._rebuild_sidebar_feeds()
                self._sync_quick_switcher()

        self._run_async(_task, _done)

    def open_login_dialog(self) -> None:
        dialog = BrowserLoginDialog(self)
        dialog.login_completed.connect(self._on_login_completed)
        dialog.exec()

    def _on_login_completed(self, data: Dict[str, Any]) -> None:
        self.client.apply_session_payload(data)
        self.current_user = self.client.fetch_current_user(force=True)
        user_subs = self.client.fetch_user_subreddits() if self.current_user else []
        self.user_subscribed_subs = {s.lower() for s in user_subs}
        self._update_user_ui()
        self._rebuild_sidebar_feeds()
        self._sync_quick_switcher()
        if self.current_user:
            self.show_toast(
                f"✓ Bem-vindo(a), u/{self.current_user.get('username')}!",
                variant="success",
            )
        self.load_subreddit("home", sort="hot")

    def _on_logout_clicked(self) -> None:
        self.client.logout()
        self.current_user = None
        self.user_subscribed_subs.clear()
        self._update_user_ui()
        self._rebuild_sidebar_feeds()
        self._sync_quick_switcher()
        self.show_toast("Sessão encerrada com sucesso.", variant="info")
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
                self.show_toast("⚠️ Não foi possível registrar o voto.", variant="error")

        self._run_async(_task, _done)

    def _on_comment_vote_requested(self, comment: CommentItem, direction: int) -> None:
        if not self.current_user:
            self.open_login_dialog()
            return
        cid = comment.id if comment.id.startswith("t1_") else f"t1_{comment.id}"

        def _task() -> bool:
            return self.client.vote(cid, direction)

        def _done(ok: bool) -> None:
            if not ok and not self._is_closing and isValid(self):
                self.show_toast("⚠️ Falha ao votar no comentário.", variant="error")

        self._run_async(_task, _done)

    def _on_submit_top_comment(self) -> None:
        if not self.current_user:
            self.open_login_dialog()
            return
        if not self.current_post:
            return
        text = self.top_comment_input.toPlainText().strip()
        if not text:
            return

        self.btn_submit_top_comment.setEnabled(False)
        parent_fullname = f"t3_{self.current_post.id}"

        def _task() -> Any:
            return self.client.submit_comment(parent_fullname, text)

        def _done(res: Any) -> None:
            if not isValid(self):
                return
            self.btn_submit_top_comment.setEnabled(True)
            ok, msg, new_item = res
            if ok and new_item:
                self.top_comment_input.clear()
                self.current_comments.insert(0, new_item)
                self.comment_cards = self.comments_flat.set_comments(
                    self.current_comments
                )
                self.show_toast("✓ Comentário publicado!", variant="success")
            else:
                self.show_toast(f"⚠️ {msg}", variant="error")

        self._run_async(_task, _done)

    def _on_inline_reply_submitted(
        self, parent_fullname: str, text: str, row_widget: CommentRowWidget
    ) -> None:
        if not self.current_user:
            self.open_login_dialog()
            return

        def _task() -> Any:
            return self.client.submit_comment(parent_fullname, text)

        def _done(res: Any) -> None:
            if not isValid(self):
                return
            ok, msg, new_item = res
            if ok and new_item and isValid(row_widget):
                row_widget.clear_and_close_reply()
                new_row = self.comments_flat.insert_reply_after(row_widget, new_item)
                self.comment_cards.append(new_row)
                self.show_toast("✓ Resposta publicada!", variant="success")
            else:
                self.show_toast(f"⚠️ {msg}", variant="error")

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
        self.show_toast(
            f"✓ Post publicado com sucesso em r/{subreddit}!", variant="success"
        )
        self.load_subreddit(subreddit, sort="new")

    # -------------------------------------------------------------------------
    # Favoritos e Barra Lateral (com validação e modo Icon Rail)
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
            row_layout.setContentsMargins(4, 0, 2, 0)
            row_layout.setSpacing(6)

            if not self._sidebar_collapsed:
                sub_avatar = CommunityAvatarWidget(sub, size=20)
                row_layout.addWidget(sub_avatar)

            label_txt = sub[:2].upper() if self._sidebar_collapsed else f"r/{sub}"
            btn = QPushButton(label_txt)
            btn.setProperty("class", "NavButton")
            btn.setProperty(
                "collapsed", "true" if self._sidebar_collapsed else "false"
            )
            btn.setToolTip(f"r/{sub}")
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

            if not self._sidebar_collapsed:
                btn_del = QPushButton("✕")
                btn_del.setProperty("class", "FavRemoveButton")
                btn_del.setToolTip(f"Remover r/{sub} dos favoritos")
                btn_del.setFixedSize(22, 22)
                btn_del.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn_del.clicked.connect(
                    lambda checked=False, s=sub: self._remove_favorite_subreddit(s)
                )
                row_layout.addWidget(btn_del)

            self.favs_layout.addWidget(row_widget)

        self._sync_sidebar_active_states()
        self._sync_quick_switcher()

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
            self.show_toast(
                "⚠️ Nome inválido (use apenas letras, números e _)",
                variant="warning",
            )
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
            self.show_toast(f"r/{sub} removido dos favoritos.", variant="info")
        else:
            self.favorites.append(sub)
            self.show_toast(f"★ r/{sub} adicionado aos favoritos!", variant="success")
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
    # Carregamento de Feeds, Skeletons Shimmer e Regras da Comunidade
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
            self._show_feed_skeletons(3)
            if clean_sub == "home":
                self.lbl_feed_title.setText("Início")
                self.lbl_feed_subtitle.setText(
                    "Postagens das comunidades em que você está inscrito"
                )
            else:
                self.lbl_feed_title.setText(f"r/{clean_sub}")
                self.lbl_feed_subtitle.setText(
                    f"Carregando postagens de r/{clean_sub}..."
                )
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
            rules = (
                self.client.fetch_community_rules(clean_sub)
                if not append
                else None
            )
            return posts, next_tok, sub_info, rules

        def _on_done(payload: Any) -> None:
            if req_id != self._feed_request_id:
                return
            posts, next_tok, sub_info, rules = payload
            self.next_after = next_tok
            if not append:
                self._clear_feed_cards()
            self.lbl_status.setText(f"{len(self.feed_cards) + len(posts)} posts")
            self._update_subreddit_header_and_sidebar(sub_info, rules)
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

    def _show_feed_skeletons(self, count: int = 3) -> None:
        for i in range(count):
            sk = PostCardSkeleton()
            self.feed_list_layout.insertWidget(i, sk)

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

    def _update_subreddit_header_and_sidebar(
        self,
        info: SubredditInfo,
        rules: Optional[List[Dict[str, str]]] = None,
    ) -> None:
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

        if rules is not None:
            self._populate_community_rules(rules)

    def _populate_community_rules(self, rules: List[Dict[str, str]]) -> None:
        while self.rules_layout.count():
            item = self.rules_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        has_rules = bool(rules)
        self.lbl_rules_title.setVisible(has_rules)
        self.rules_container.setVisible(has_rules)
        for r in rules[:10]:
            acc = RuleAccordionItem(
                r.get("title", "Regra"), r.get("description", "")
            )
            self.rules_layout.addWidget(acc)

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
            self._show_feed_skeletons(2)
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
            if not append:
                self._clear_feed_cards()
                self._render_sub_search_bar(subs)
            total_shown = len(self.feed_cards) + len(posts)
            if not append:
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
        lbl.setProperty("role", "caption")
        self.sub_search_layout.addWidget(lbl)

        for s in subs[:5]:
            btn = UiSkillsButton(
                f"{s.display_name_prefixed} ({format_count(s.subscribers)})"
            )
            btn.setProperty("class", "PillButton")
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
    # Renderização dos Cards de Post no Feed
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
        lbl.setProperty("role", "muted")
        self.feed_list_layout.insertWidget(0, lbl)

    def _populate_feed_posts(
        self, posts: List[PostItem], append: bool = False
    ) -> None:
        for i in reversed(range(self.feed_list_layout.count())):
            w = self.feed_list_layout.itemAt(i).widget()
            if w is not None and w.objectName() == "LoadMoreButton":
                self.feed_list_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        insert_pos = max(0, self.feed_list_layout.count() - 1)
        fav_lower = {f.lower() for f in self.favorites}
        for post in posts:
            cached_t = self.translator.get_cached(post.title)
            if cached_t:
                post.translated_title = cached_t

            is_saved = post.id in self.saved_posts_data
            is_visited = post.id in self.visited_post_ids
            is_joined = (
                post.subreddit.lower() in fav_lower
                or post.subreddit.lower() in self.user_subscribed_subs
            )
            card = PostCardWidget(
                post=post,
                is_saved=is_saved,
                on_request_image=self._request_image_async,
                view_mode=self.view_mode,
                is_visited=is_visited,
                is_joined=is_joined,
            )
            card.open_post_requested.connect(self.open_post_detail)
            card.open_subreddit_requested.connect(self.load_subreddit)
            card.translate_post_requested.connect(self.translate_single_post_card)
            card.toggle_save_requested.connect(self._toggle_save_post)
            card.toggle_join_requested.connect(self._on_card_join_toggled)
            card.vote_requested.connect(self._on_card_vote_requested)

            self.feed_cards.append(card)
            self.feed_list_layout.insertWidget(insert_pos, card)
            insert_pos += 1

        # Se a lista lateral 'POSTS RECENTES' ainda estiver vazia, preenche com os primeiros posts do feed (Imagem 5)
        if not self.recent_posts_data and posts:
            for p in reversed(posts[:5]):
                self._record_recent_post(p, save=False)
            self._save_config()

        if self.next_after and not self.is_viewing_saved:
            btn_more = UiSkillsButton("⬇  Carregar mais postagens")
            btn_more.setObjectName("LoadMoreButton")
            btn_more.setProperty("class", "PillButtonAccent")
            btn_more.setFixedHeight(40)
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

    def _on_card_join_toggled(self, sub: str) -> None:
        if not sub or not is_valid_subreddit_name(sub):
            return
        clean_sub = sub.strip().lstrip("/").removeprefix("r/").strip("/")
        clean_sub_lower = clean_sub.lower()

        is_now_joined = (
            clean_sub_lower in self.user_subscribed_subs
            or any(f.lower() == clean_sub_lower for f in self.favorites)
        )

        if is_now_joined:
            self.user_subscribed_subs.discard(clean_sub_lower)
            self.favorites = [f for f in self.favorites if f.lower() != clean_sub_lower]
            if self.current_user:
                self._run_async(lambda: self.client.subscribe(clean_sub, action="unsub"))
            self.show_toast(f"Você saiu de r/{clean_sub}.", variant="info")
        else:
            self.user_subscribed_subs.add(clean_sub_lower)
            if not any(f.lower() == clean_sub_lower for f in self.favorites):
                self.favorites.append(clean_sub)
            if self.current_user:
                self._run_async(lambda: self.client.subscribe(clean_sub, action="sub"))
            self.show_toast(f"✓ Você se uniu a r/{clean_sub}!", variant="success")

        self._save_config()
        self._sync_fav_button()
        self._rebuild_favorites_sidebar()
        for card in self.feed_cards:
            if hasattr(card, "post") and card.post.subreddit.lower() == clean_sub_lower:
                card.set_joined(not is_now_joined)

    def _toggle_save_post(self, post: PostItem) -> None:
        if post.id in self.saved_posts_data:
            del self.saved_posts_data[post.id]
            self.show_toast("Post removido dos salvos.", variant="info")
        else:
            self.saved_posts_data[post.id] = asdict(post)
            self.show_toast("🔖 Post salvo localmente!", variant="success")
        self._save_config()

    # -------------------------------------------------------------------------
    # Visualização de Post Completo + Comentários Flat (CR-05, CR-06)
    # -------------------------------------------------------------------------
    def show_feed_page(self) -> None:
        self._detail_request_id += 1
        if hasattr(self, "video_player"):
            self.video_player.stop()
        self.stack.setCurrentWidget(self.feed_page)

    def open_post_detail(self, post: PostItem) -> None:
        self._detail_request_id += 1
        req_id = self._detail_request_id

        self.visited_post_ids.add(post.id)
        self._record_recent_post(post, save=True)

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

        if post.is_video and (post.video_url or post.video_hls_url or post.video_dash_url):
            self.video_player.setVisible(True)
            self.video_player.load_video(
                post.video_url,
                dash_url=post.video_dash_url,
                hls_url=post.video_hls_url,
                permalink=post.permalink,
            )
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

        self.comments_flat.clear_comments()
        self.lbl_comments_status_msg.setVisible(False)
        self.lbl_comments_header.setText("💬 Carregando árvore de comentários...")

        def _task() -> List[CommentItem]:
            return self.client.fetch_post_comments(post, limit=80)

        def _done(comments: List[CommentItem]) -> None:
            if (
                req_id != self._detail_request_id
                or not self.current_post
                or self.current_post.id != post.id
            ):
                return
            self.current_comments = comments
            self._refresh_detail_post_texts()
            self.lbl_comments_header.setText(
                f"💬 Comentários ({len(comments)} carregados — clique nas linhas guia ou [−] para colapsar)"
            )
            if not comments:
                self.lbl_comments_status_msg.setText(
                    "Nenhum comentário disponível nesta postagem."
                )
                self.lbl_comments_status_msg.setVisible(True)
                return

            self.comment_cards = self.comments_flat.set_comments(comments)

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
            self.lbl_comments_status_msg.setText(
                f"Não foi possível carregar os comentários: {msg}"
            )
            self.lbl_comments_status_msg.setVisible(True)

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
        if event.button() == Qt.MouseButton.LeftButton and self.current_post:
            if self.current_post.is_video:
                open_video_stream(
                    self.current_post.video_url or self.current_post.url,
                    self.current_post.permalink,
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
            webbrowser.open(
                canonical_reddit_url(
                    self.current_post.permalink, self.current_post.url
                )
            )

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
        self, comment: CommentItem, card: CommentRowWidget
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
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Reddit Linux Desktop")
    app.setDesktopFileName("reddit-linux-desktop")
    window = RedditLinuxWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
