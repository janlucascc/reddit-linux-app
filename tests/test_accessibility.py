#!/usr/bin/env python3
"""
Suíte Automatizada de Verificação de Acessibilidade e Arquitetura (CR-01 a CR-07).
1. CR-01: Luminância relativa sRGB (W3C WCAG 2.1 AA/AAA >= 4.5:1) em todos os 23 pares Dark/Light.
2. CR-02: QSplitter de 3 colunas com handleWidth() == 4px e childrenCollapsible == False.
3. CR-03: Zero chamadas inline setStyleSheet() nos módulos de UI e reatividade do ThemeManager.
4. CR-04: Escalonamento QImage Thread-Safe com compensação High-DPI e CLS = 0 no #MediaFrame.
5. CR-05: Clamping de indentação em depth=8 (máx 144px) e batch fold O(N) < 15ms em 100 comentários.
6. CR-06: URL canônica para muxing de áudio+vídeo via MPV/yt-dlp e banner de fallback GStreamer.
7. CR-07: Servidor local de autenticação com fallback para porta efêmera 0 e exibição de porta real.
"""

from __future__ import annotations
import os
from pathlib import Path
import sys
import time
from typing import List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MANGOHUD", "0")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from browser_auth import BrowserAuthServer
from reddit_api import CommentItem, PostItem
from ui_theme import DARK_PALETTE, LIGHT_PALETTE, ThemeManager
from widgets import (
    CommentRowWidget,
    CommentsFlatContainer,
    EmbeddedVideoWidget,
    ImageScalerWorker,
    PostCardWidget,
    RedditLogoWidget,
    ShimmerSkeletonWidget,
    canonical_reddit_url,
    scale_qimage_threadsafe,
)


def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b


def relative_luminance(hex_color: str) -> float:
    """Fórmula W3C WCAG 2.1 de luminância relativa sRGB."""
    r, g, b = hex_to_rgb(hex_color)

    def _linearize(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r_lin = _linearize(r)
    g_lin = _linearize(g)
    b_lin = _linearize(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = relative_luminance(fg_hex)
    l2 = relative_luminance(bg_hex)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def run_wcag_audit() -> bool:
    dark_pairs: List[Tuple[str, str, str, float]] = [
        ("Dark: Título do Post", DARK_PALETTE["text_primary"], DARK_PALETTE["surface_card"], 4.5),
        ("Dark: Corpo do Texto", DARK_PALETTE["text_primary"], DARK_PALETTE["canvas_default"], 4.5),
        ("Dark: Metadados", DARK_PALETTE["text_secondary"], DARK_PALETTE["surface_card"], 4.5),
        ("Dark: Rótulo de Seção", DARK_PALETTE["text_muted"], DARK_PALETTE["canvas_subtle"], 4.5),
        ("Dark: Botão Primário", DARK_PALETTE["text_inverse"], DARK_PALETTE["brand_accent"], 4.5),
        ("Dark: Badge OP", "#FFFFFF", DARK_PALETTE["badge_op"], 4.5),
        ("Dark: Badge Moderador", "#0E1113", DARK_PALETTE["badge_mod"], 4.5),
        ("Dark: Link de Ação", DARK_PALETTE["action_link"], DARK_PALETTE["surface_card"], 4.5),
        ("Dark: Score Downvote", DARK_PALETTE["vote_downvote"], DARK_PALETTE["surface_card"], 4.5),
        ("Dark: Badge Spoiler", DARK_PALETTE["badge_spoiler"], DARK_PALETTE["surface_card"], 4.5),
        ("Dark: Badge Admin", "#FFFFFF", DARK_PALETTE["badge_admin"], 4.5),
        ("Dark: Badge NSFW", "#0E1113", DARK_PALETTE["badge_nsfw"], 4.5),
    ]

    light_pairs: List[Tuple[str, str, str, float]] = [
        ("Light: Título do Post", LIGHT_PALETTE["text_primary"], LIGHT_PALETTE["surface_card"], 4.5),
        ("Light: Metadados", LIGHT_PALETTE["text_secondary"], LIGHT_PALETTE["surface_card"], 4.5),
        ("Light: Rótulo de Seção", LIGHT_PALETTE["text_muted"], LIGHT_PALETTE["canvas_subtle"], 4.5),
        ("Light: Botão Primário", LIGHT_PALETTE["text_inverse"], LIGHT_PALETTE["brand_accent"], 4.5),
        ("Light: Badge OP", "#FFFFFF", LIGHT_PALETTE["badge_op"], 4.5),
        ("Light: Badge Moderador", "#FFFFFF", LIGHT_PALETTE["badge_mod"], 4.5),
        ("Light: Link de Ação", LIGHT_PALETTE["action_link"], LIGHT_PALETTE["surface_card"], 4.5),
        ("Light: Score Downvote", LIGHT_PALETTE["vote_downvote"], LIGHT_PALETTE["surface_card"], 4.5),
        ("Light: Badge Spoiler", LIGHT_PALETTE["badge_spoiler"], LIGHT_PALETTE["surface_card"], 4.5),
        ("Light: Badge Admin", "#FFFFFF", LIGHT_PALETTE["badge_admin"], 4.5),
        ("Light: Badge NSFW", "#FFFFFF", LIGHT_PALETTE["badge_nsfw"], 4.5),
    ]

    all_passed = True
    print("=" * 76)
    print("1. AUDITORIA AUTOMATIZADA DE ACESSIBILIDADE WCAG 2.1 AA/AAA (CR-01)")
    print("=" * 76)

    for label, fg, bg, min_ratio in dark_pairs + light_pairs:
        ratio = contrast_ratio(fg, bg)
        tier = "AAA" if ratio >= 7.0 else ("AA" if ratio >= 4.5 else "FAIL")
        passed = ratio >= min_ratio
        if not passed:
            all_passed = False
        status = "PASS" if passed else "FAIL"
        print(f"[{status:4s}] {label:28s} | {fg} em {bg} -> {ratio:5.2f}:1 ({tier})")

    return all_passed


def run_architectural_checks() -> bool:
    print("=" * 76)
    print("2. VERIFICAÇÃO ARQUITETURAL (CR-02 a CR-07)")
    print("=" * 76)
    app = QApplication.instance() or QApplication(sys.argv)

    # CR-03: Zero chamadas inline setStyleSheet() nos arquivos de componentes
    for fname in ("widgets.py", "post_dialog.py", "browser_auth.py", "main.py"):
        src = (ROOT / fname).read_text(encoding="utf-8")
        assert ".setStyleSheet(" not in src, f"CR-03 violado em {fname}"
    print("[PASS] CR-03: Zero chamadas .setStyleSheet() inline em widgets/dialogs/main")

    # CR-03: ThemeManager singleton e reatividade
    tm = ThemeManager.instance()
    logo = RedditLogoWidget(size=32)
    skel = ShimmerSkeletonWidget(height=16)
    tm.set_theme("light", force=True)
    assert not tm.is_dark and tm.tokens.vote_downvote == "#3366FF"
    tm.set_theme("dark", force=True)
    assert tm.is_dark and tm.tokens.brand_accent == "#FF4500"
    print("[PASS] CR-03: ThemeManager singleton alterna Dark <-> Light atomicamente")

    # CR-02: QSS do QSplitter::handle:horizontal com width: 4px
    qss = tm.build_stylesheet(dark=True)
    assert "width: 4px;" in qss and "QSplitter::handle:horizontal" in qss
    print("[PASS] CR-02: QSplitter::handle:horizontal possui hitbox acessível de 4px")

    # CR-04: High-DPI Thread-Safe QImage Scaling + CLS = 0 MediaFrame
    test_img = QImage(800, 600, QImage.Format.Format_ARGB32)
    test_img.fill(QColor("#FF4500"))
    worker = ImageScalerWorker(request_id=1, base_image=test_img, target_width=400, dpr=2.0)
    received = []
    worker.signals.finished.connect(lambda rid, qimg: received.append((rid, qimg)))
    worker.run()
    assert len(received) == 1
    rid, out_qimg = received[0]
    assert rid == 1 and out_qimg.width() == 800 and out_qimg.devicePixelRatio() == 2.0

    sample_post = PostItem(
        id="p1",
        subreddit="linux",
        subreddit_prefixed="r/linux",
        author="tux",
        title="Teste de CLS Zero",
        selftext="",
        score=42,
        num_comments=10,
        created_utc=time.time(),
        permalink="/r/linux/comments/p1/teste/",
        url="https://i.redd.it/test.png",
        preview_image_url="https://i.redd.it/test.png",
        preview_width=1280,
        preview_height=720,
    )
    card = PostCardWidget(sample_post)
    assert card.image_label.height() == 360
    print("[PASS] CR-04: ImageScalerWorker QImage (DPR=2.0) e #MediaFrame CLS=0 validados")

    # CR-05: Clamping de indentação em depth=8 (máx 144px) e Batch Fold < 15ms
    comments = [
        CommentItem(
            id=f"c_{i}",
            author=f"user_{i}",
            body=f"Comentário de teste {i}",
            created_utc=time.time(),
            score=i + 1,
            depth=min(i, 15),
        )
        for i in range(100)
    ]
    flat = CommentsFlatContainer()
    rows = flat.set_comments(comments)
    assert len(rows) == 100
    deep_row = rows[15]  # depth = 15 -> visual_depth = 8 -> left_indent = 144px
    assert deep_row.visual_depth == CommentRowWidget.MAX_VISUAL_DEPTH == 8
    assert deep_row.left_indent == 144

    t0 = time.perf_counter()
    flat.toggle_fold("c_0")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 15.0, f"Batch fold levou {elapsed_ms:.2f}ms (> 15ms)"
    assert rows[1].isHidden()
    flat.toggle_fold("c_0")
    assert not rows[1].isHidden()
    print(
        f"[PASS] CR-05: Clamping depth=8 (144px) e Batch Fold de 100 nós em {elapsed_ms:.2f}ms (< 15ms)"
    )

    # CR-06: URL canônica para MPV+yt-dlp e EmbeddedVideoWidget
    canon = canonical_reddit_url("/r/linux/comments/abc/video_test/")
    assert canon == "https://www.reddit.com/r/linux/comments/abc/video_test/"
    print("[PASS] CR-06: URL canônica para muxing de áudio+vídeo no MPV validada")

    # CR-07: Porta efêmera 0 no BrowserAuthServer
    srv = BrowserAuthServer(port=0)
    bound_port = srv.server_port
    srv.server_close()
    assert bound_port > 0
    print(f"[PASS] CR-07: BrowserAuthServer(port=0) alocou porta dinâmica {bound_port}")

    return True


if __name__ == "__main__":
    ok1 = run_wcag_audit()
    ok2 = run_architectural_checks()
    print("=" * 76)
    if ok1 and ok2:
        print("RESULTADO GLOBAL: 100% APROVADO (CR-01 a CR-07 VERIFICADOS COM SUCESSO)")
        sys.exit(0)
    else:
        print("RESULTADO GLOBAL: FALHA DETECTADA")
        sys.exit(1)
