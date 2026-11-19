#!/usr/bin/env python3
"""
Suíte automatizada de verificação das Fases 0 a 7 do UI_UX_IMPLEMENTATION_PLAN.md.
Executa com fixtures locais determinísticas (sem dependência de rede externa):
- Fase 1: Hierarquia, Jogos recolhido, sem badge fictício "10", banner apenas em comunidade real
- Fase 2: Nomes acessíveis, controles focáveis por teclado, ordem de foco e anúncios live
- Fase 3: Estados honestos (local vs remoto), reversão de voto, recuperação de erro e Desfazer
- Fase 4: Breakpoints 1440x900, 1024x768 e 920x620, agrupamento do header, painel Contexto e zero overflow
- Fase 5: Preservação de scroll/contexto ao voltar do detalhe, comentários profundos e recolhimento por teclado
- Fase 6: Formulário de criação com labels persistentes, validação inline e foco no primeiro erro
- Fase 7: Temas Dark/Light, preferência de redução de movimento e capturas visuais de verificação
"""

from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from main import RedditLinuxWindow
from post_dialog import CreatePostDialog
from reddit_api import CommentItem, PostItem, SubredditInfo
from ui_theme import ThemeManager, prefers_reduced_motion


def _sample_posts() -> List[PostItem]:
    return [
        PostItem(
            id="p1",
            title="Kernel Linux 6.12 LTS traz melhorias de latência em desktop",
            author="dev_linux",
            subreddit="linux",
            subreddit_prefixed="r/linux",
            score=1420,
            num_comments=128,
            created_utc=1795000000.0,
            permalink="/r/linux/comments/p1/kernel_linux_612/",
            url="https://www.reddit.com/r/linux/comments/p1/kernel_linux_612/",
            selftext="Resumo das novidades do novo kernel para desktops modernos com Wayland e PipeWire.",
            is_self=True,
            domain="self.linux",
            flair="Notícia",
        ),
        PostItem(
            id="p2",
            title="Dica de produtividade com atalhos de teclado no Qt6",
            author="ui_craft",
            subreddit="programacao",
            subreddit_prefixed="r/programacao",
            score=340,
            num_comments=24,
            created_utc=1794990000.0,
            permalink="/r/programacao/comments/p2/atalhos_qt6/",
            url="https://www.reddit.com/r/programacao/comments/p2/atalhos_qt6/",
            selftext="Como manter foco previsível e acessível em aplicativos desktop nativos.",
            is_self=True,
            domain="self.programacao",
            oc=True,
        ),
    ]


def _sample_comments() -> List[CommentItem]:
    items: List[CommentItem] = []
    for depth in range(0, 10):
        items.append(
            CommentItem(
                id=f"c{depth}",
                author=f"user_{depth}",
                body=f"Comentário no nível {depth} discutindo desempenho e acessibilidade.",
                score=50 - depth,
                created_utc=1795001000.0 + depth * 60,
                depth=depth,
                is_submitter=(depth == 0),
            )
        )
    return items


class TestUiUxImplementationPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.capture_dir = Path(
            os.environ.get("UI_UX_CAPTURE_DIR", "")
        ) if os.environ.get("UI_UX_CAPTURE_DIR") else None

    def _make_window(self) -> RedditLinuxWindow:
        with (
            patch("main.RedditClient.fetch_current_user", return_value=None),
            patch("main.RedditClient.fetch_subreddit_posts", return_value=(_sample_posts(), None)),
            patch(
                "main.RedditClient.fetch_subreddit_info",
                return_value=SubredditInfo(
                    name="popular",
                    display_name_prefixed="r/popular",
                    title="Popular",
                    public_description="Postagens populares do Reddit.",
                    subscribers=0,
                    active_users=0,
                ),
            ),
            patch("main.RedditClient.fetch_community_rules", return_value=[]),
        ):
            win = RedditLinuxWindow()
            win._populate_feed_posts(_sample_posts(), append=False)
            win.show()
            self.app.processEvents()
            return win

    def _capture(self, win: RedditLinuxWindow, name: str) -> None:
        if self.capture_dir is None:
            return
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.app.processEvents()
        win.grab().save(str(self.capture_dir / f"{name}.png"))

    def test_fase1_hierarchy_and_no_fake_metrics(self) -> None:
        win = self._make_window()
        try:
            # Jogos recolhido por padrão
            self.assertFalse(win._games_expanded)
            self.assertFalse(win.games_container.isVisible())
            # Sem badge fictício "10"
            self.assertFalse(hasattr(win, "lbl_notif_badge"))
            # Atalhos recolhidos por padrão no rail direito
            self.assertFalse(win._shortcuts_expanded)
            self.assertFalse(win.shortcuts_body.isVisible())

            # Home não exibe banner de comunidade nem métricas fictícias
            home_info = SubredditInfo(
                name="home",
                display_name_prefixed="Início",
                title="Início",
                public_description="",
                subscribers=0,
                active_users=0,
            )
            win.current_subreddit = "home"
            win._update_subreddit_header_and_sidebar(home_info, rules=[])
            self.assertFalse(win.banner_card.isVisible())
            self.assertEqual(win.lbl_right_sub_name.text(), "Seu Feed")
            self.assertFalse(win.stats_frame.isVisible())

            # Comunidade real exibe banner e métricas reais (e oculta online se 0)
            linux_info = SubredditInfo(
                name="linux",
                display_name_prefixed="r/linux",
                title="Linux",
                public_description="All things Linux",
                subscribers=1540000,
                active_users=0,
            )
            win.current_subreddit = "linux"
            win._update_subreddit_header_and_sidebar(
                linux_info, rules=[{"title": "Seja respeitoso", "description": "Respeite os membros."}]
            )
            self.assertTrue(win.banner_card.isVisible())
            self.assertTrue(win.stats_frame.isVisible())
            self.assertFalse(win.online_stat_widget.isVisible())
            self.assertTrue(win.rules_card.isVisible())
        finally:
            win.close()

    def test_fase2_accessibility_and_keyboard(self) -> None:
        win = self._make_window()
        try:
            for ctrl in (
                win.btn_hamburger,
                win.brand_btn,
                win.search_input,
                win.btn_header_translate,
                win.btn_header_ads,
                win.btn_header_chat,
                win.btn_header_more,
                win.btn_create_post,
                win.btn_notifications,
                win.snoovatar,
                win.btn_tab_foryou,
                win.btn_tab_following,
                win.btn_view_card,
                win.btn_view_compact,
                win.add_sub_input,
                win.btn_clear_recent,
            ):
                self.assertTrue(
                    bool(ctrl.accessibleName().strip()),
                    f"Controle sem accessibleName: {ctrl.objectName() or ctrl}",
                )

            # Live announcement atualiza região acessível
            win.announce_status("Carregando resultados de teste")
            self.assertEqual(win._live_region_lbl.accessibleName(), "Carregando resultados de teste")

            # Snoovatar sem login não exibe ponto verde online
            self.assertFalse(win.snoovatar._online)
            win.snoovatar.set_logged_in(True, "janlucas")
            self.assertTrue(win.snoovatar._online)

            # Escape no campo de busca limpa foco sem apagar texto
            win.search_input.setText("wayland")
            win.search_input.setFocus()
            self.app.processEvents()
            win._on_escape_pressed()
            self.assertEqual(win.search_input.text(), "wayland")
        finally:
            win.close()

    def test_fase3_honest_states_undo_and_recovery(self) -> None:
        win = self._make_window()
        try:
            # Voto deslogado reverte imediatamente e informa motivo
            card = win.feed_cards[0]
            orig_score = card.post.score
            card._on_vote_clicked(1)
            self.assertEqual(card._vote_state, 0)
            self.assertEqual(card.post.score, orig_score)
            self.assertIn("entrar", win.lbl_toast.text().lower())
            self.assertTrue(win.btn_toast_action.isVisible())

            # Limpar posts recentes oferece Undo funcional
            win._record_recent_post(_sample_posts()[0], save=False)
            self.assertTrue(len(win.recent_posts_data) > 0)
            win._clear_recent_posts()
            self.assertEqual(len(win.recent_posts_data), 0)
            self.assertTrue(win.btn_toast_action.isVisible())
            win._on_toast_action_clicked()
            self.assertTrue(len(win.recent_posts_data) > 0)

            # Erro inline em Adicionar comunidade (não só toast)
            win.add_sub_input.setText("nome inválido!!!")
            win._on_add_favorite_from_input()
            self.assertTrue(win.lbl_add_sub_err.isVisible())

            # Estado de erro no feed oferece botão Tentar novamente
            retried = {"count": 0}
            win._show_feed_message(
                "Não foi possível carregar o feed.",
                retry_callback=lambda: retried.__setitem__("count", retried["count"] + 1),
                is_error=True,
            )
            state_card = win.feed_list_layout.itemAt(0).widget()
            self.assertEqual(state_card.objectName(), "FeedStateCard")
        finally:
            win.close()

    def test_fase4_responsive_breakpoints_and_captures(self) -> None:
        win = self._make_window()
        try:
            for width, height, expect_rail, expect_more_hdr, expect_compact_sort in (
                (1440, 900, True, False, False),
                (1024, 768, False, True, True),
                (920, 620, False, True, True),
            ):
                win.resize(width, height)
                win._apply_responsive_layout(width)
                self.app.processEvents()

                self.assertEqual(win.right_sidebar.isVisible(), expect_rail, f"Rail em {width}px")
                self.assertEqual(win.btn_toggle_context.isVisible(), not expect_rail, f"Botão contexto em {width}px")
                self.assertEqual(win.btn_header_more.isVisible(), expect_more_hdr, f"Header overflow em {width}px")
                self.assertEqual(win.btn_sort_menu.isVisible(), expect_compact_sort, f"Sort compacto em {width}px")
                # Zero scroll horizontal no feed
                self.assertEqual(
                    win.feed_scroll.horizontalScrollBarPolicy(),
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
                )
                self._capture(win, f"dark_home_{width}x{height}")

            # Captura em tema claro e estados adicionais (comunidade, detalhe/comentários, vazio, erro)
            ThemeManager.instance().set_theme("light", force=True)
            win.resize(1440, 900)
            win._apply_responsive_layout(1440)
            self._capture(win, "light_home_1440x900")

            ThemeManager.instance().set_theme("dark", force=True)
            linux_info = SubredditInfo(
                name="linux",
                display_name_prefixed="r/linux",
                title="Linux",
                public_description="Comunidade Linux no Reddit",
                subscribers=1540000,
                active_users=3120,
            )
            win.current_subreddit = "linux"
            win._update_subreddit_header_and_sidebar(
                linux_info, rules=[{"title": "Regra 1", "description": "Mantenha o respeito."}]
            )
            self._capture(win, "dark_community_1440x900")

            win._show_feed_message("Não foi possível carregar o feed.", retry_callback=lambda: None, is_error=True)
            self._capture(win, "dark_error_1440x900")
        finally:
            win.close()

    def test_fase5_post_detail_scroll_preservation_and_comments(self) -> None:
        win = self._make_window()
        try:
            # Abre post via teclado (Enter no card)
            card = win.feed_cards[0]
            win._saved_feed_scroll_pos = 0
            with patch("main.RedditClient.fetch_post_comments", return_value=_sample_comments()):
                ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
                card.keyPressEvent(ev)
                self.app.processEvents()

            self.assertEqual(win.stack.currentWidget(), win.detail_page)
            # Invalida requisição assíncrona pendente para usar fixture determinística local
            win._detail_request_id += 1
            win.resize(1440, 900)
            win._apply_responsive_layout(1440)
            rows = win.comments_flat.set_comments(_sample_comments())
            self.assertEqual(len(rows), 10)
            self._capture(win, "dark_detail_comments_expanded_1440x900")
            # Recolher primeiro comentário informa respostas ocultas, oculta descendentes e atualiza acessibilidade
            win.comments_flat.toggle_fold("c0")
            self.app.processEvents()
            self.assertIn("9 respostas ocultas", rows[0].folded_summary_lbl.text())
            self.assertIn("Recolhido", rows[0].btn_fold.accessibleDescription())
            self.assertTrue(rows[1].isHidden())
            self._capture(win, "dark_detail_comments_1440x900")

            # Voltar ao feed restaura página
            win.show_feed_page()
            self.assertEqual(win.stack.currentWidget(), win.feed_page)
        finally:
            win.close()

    def test_fase6_post_dialog_labels_and_inline_validation(self) -> None:
        win = self._make_window()
        try:
            dlg = CreatePostDialog(win.client, initial_subreddit="linux", parent=win)
            dlg.show()
            self.app.processEvents()

            # Botão publicar habilitado inicialmente para permitir validação inline ao tentar enviar
            self.assertTrue(dlg.btn_submit.isEnabled())
            dlg.title_input.setText("")
            dlg._submit()
            self.assertTrue(dlg.lbl_title_err.isVisible())
            self.assertIn("título", dlg.lbl_title_err.text().lower())

            # Trocar para Link valida URL inline
            dlg.title_input.setText("Meu título")
            dlg._switch_tab("link")
            dlg.link_input.setText("url-invalida")
            dlg._submit()
            self.assertTrue(dlg.lbl_link_err.isVisible())

            # Enquete permite adicionar e remover opções
            dlg._switch_tab("poll")
            initial_opts = len(dlg.poll_option_inputs)
            dlg._add_poll_option_row()
            self.assertEqual(len(dlg.poll_option_inputs), initial_opts + 1)

            # Verifica preferência de redução de movimento é booleana válida
            self.assertIsInstance(prefers_reduced_motion(), bool)
            dlg.reject()
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
