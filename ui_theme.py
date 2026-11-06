"""
Estilos QSS e Paletas do Reddit Linux Desktop.
Design fielmente inspirado no Reddit Web moderno (Shreddit),
com suporte completo a Modo Escuro (padrão) e Modo Claro.
"""

from typing import Dict
from PySide6.QtGui import QColor, QPalette


DARK_PALETTE: Dict[str, str] = {
    "bg_app": "#0B1416",
    "bg_header": "#0E1113",
    "bg_sidebar": "#0E1113",
    "bg_card": "#131F23",
    "bg_card_hover": "#18272C",
    "bg_input": "#1A282D",
    "bg_pill": "#1E2F35",
    "bg_pill_hover": "#2A4048",
    "border": "#24373E",
    "border_subtle": "#1B2B30",
    "text_primary": "#F2F4F5",
    "text_secondary": "#8BA2AD",
    "text_muted": "#647C87",
    "text_body": "#D7DADC",
    "accent": "#FF4500",
    "accent_hover": "#FF571A",
    "accent_soft": "#3A1C12",
    "translate_active": "#0079D3",
    "translate_soft": "#102E4A",
    "success": "#46D160",
}

LIGHT_PALETTE: Dict[str, str] = {
    "bg_app": "#F2F4F5",
    "bg_header": "#FFFFFF",
    "bg_sidebar": "#FFFFFF",
    "bg_card": "#FFFFFF",
    "bg_card_hover": "#F8FAFB",
    "bg_input": "#EAEDEF",
    "bg_pill": "#EAEDEF",
    "bg_pill_hover": "#DCE1E5",
    "border": "#D6DADC",
    "border_subtle": "#E5E9EC",
    "text_primary": "#0F1A1C",
    "text_secondary": "#576F76",
    "text_muted": "#789098",
    "text_body": "#222222",
    "accent": "#FF4500",
    "accent_hover": "#E03D00",
    "accent_soft": "#FFF0EB",
    "translate_active": "#0066B8",
    "translate_soft": "#E6F2FF",
    "success": "#2E9E44",
}


def build_qt_palette(dark: bool = True) -> QPalette:
    """Cria uma QPalette nativa completa alinhada com o tema para que componentes do sistema fiquem no tema escuro/claro."""
    p = DARK_PALETTE if dark else LIGHT_PALETTE
    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, QColor(p["bg_app"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(p["text_primary"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(p["bg_card"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p["bg_pill"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p["bg_card"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p["text_primary"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(p["text_primary"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(p["bg_pill"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(p["text_primary"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(p["accent"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(p["accent"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(p["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))

    return palette


def build_stylesheet(dark: bool = True) -> str:
    p = DARK_PALETTE if dark else LIGHT_PALETTE
    return f"""
    /* Estilos Globais do Sistema */
    QWidget {{
        color: {p['text_primary']};
        font-family: "Inter", "Cantarell", "Noto Sans", "Ubuntu", sans-serif;
    }}

    QMainWindow, QWidget#RootWidget, QDialog {{
        background-color: {p['bg_app']};
        color: {p['text_primary']};
    }}

    /* Barra Superior estilo Reddit Web */
    QFrame#TopHeader {{
        background-color: {p['bg_header']};
        border-bottom: 1px solid {p['border']};
    }}

    QPushButton#BrandButton {{
        background: transparent;
        color: {p['text_primary']};
        border: none;
        font-size: 21px;
        font-weight: 800;
        padding: 0px;
    }}
    QPushButton#BrandButton:hover {{
        color: {p['accent']};
    }}

    QLineEdit {{
        background-color: {p['bg_input']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        border-radius: 12px;
        padding: 6px 12px;
        font-size: 13px;
        selection-background-color: {p['accent']};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus {{
        border: 1px solid {p['accent']};
        background-color: {p['bg_card']};
    }}

    QLineEdit#GlobalSearch {{
        border-radius: 19px;
        padding: 7px 16px;
    }}

    /* ComboBox */
    QComboBox {{
        background-color: {p['bg_pill']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        border-radius: 16px;
        padding: 5px 12px;
        font-size: 12px;
        font-weight: 600;
    }}
    QComboBox:hover {{
        background-color: {p['bg_pill_hover']};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {p['bg_card']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        selection-background-color: {p['accent']};
        selection-color: #FFFFFF;
        padding: 4px;
        outline: none;
    }}

    /* Barra Lateral Esquerda */
    QFrame#LeftSidebar {{
        background-color: {p['bg_sidebar']};
        border-right: 1px solid {p['border']};
    }}

    QLabel#SidebarSectionTitle {{
        color: {p['text_muted']};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.8px;
        padding: 10px 12px 4px 12px;
    }}

    QPushButton.NavButton {{
        background-color: transparent;
        color: {p['text_primary']};
        border: none;
        border-radius: 10px;
        text-align: left;
        padding: 9px 12px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton.NavButton:hover {{
        background-color: {p['bg_pill']};
    }}
    QPushButton.NavButton[active="true"] {{
        background-color: {p['bg_pill_hover']};
        color: {p['accent']};
        font-weight: 700;
    }}

    /* Links de Subreddit (r/...) */
    QPushButton.SubredditLink {{
        background: transparent;
        color: {p['text_primary']};
        border: none;
        font-weight: 700;
        font-size: 12px;
        padding: 0px;
    }}
    QPushButton.SubredditLink:hover {{
        color: {p['accent']};
        text-decoration: underline;
    }}

    /* Cards de Postagem e Painéis */
    QFrame.PostCard {{
        background-color: {p['bg_card']};
        border: 1px solid {p['border']};
        border-radius: 14px;
    }}
    QFrame.PostCard:hover {{
        border: 1px solid {p['text_muted']};
        background-color: {p['bg_card_hover']};
    }}

    QFrame.DetailCard {{
        background-color: {p['bg_card']};
        border: 1px solid {p['border']};
        border-radius: 14px;
    }}

    QFrame.CommentCard {{
        background-color: {p['bg_card']};
        border-left: 3px solid {p['border']};
        border-top: 1px solid {p['border_subtle']};
        border-right: 1px solid {p['border_subtle']};
        border-bottom: 1px solid {p['border_subtle']};
        border-radius: 10px;
    }}
    QFrame.CommentCard:hover {{
        border-left: 3px solid {p['accent']};
    }}

    QFrame#RightCommunityCard, QFrame#SubredditBannerCard {{
        background-color: {p['bg_card']};
        border: 1px solid {p['border']};
        border-radius: 14px;
    }}

    /* Botões em Formato Pílula (PillButtons) */
    QPushButton.PillButton {{
        background-color: {p['bg_pill']};
        color: {p['text_primary']};
        border: 1px solid {p['border_subtle']};
        border-radius: 16px;
        padding: 6px 13px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton.PillButton:hover {{
        background-color: {p['bg_pill_hover']};
        border-color: {p['border']};
    }}

    QPushButton.PillButtonAccent {{
        background-color: {p['accent']};
        color: #FFFFFF;
        border: none;
        border-radius: 16px;
        padding: 6px 15px;
        font-size: 12px;
        font-weight: 700;
    }}
    QPushButton.PillButtonAccent:hover {{
        background-color: {p['accent_hover']};
    }}

    QPushButton.TranslateButton {{
        background-color: {p['bg_pill']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        border-radius: 16px;
        padding: 6px 13px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton.TranslateButton:hover {{
        background-color: {p['bg_pill_hover']};
        border: 1px solid {p['translate_active']};
    }}
    QPushButton.TranslateButton[translated="true"] {{
        background-color: {p['translate_soft']};
        color: #4DB8FF;
        border: 1px solid {p['translate_active']};
    }}

    QPushButton.SortButton {{
        background-color: transparent;
        color: {p['text_secondary']};
        border: 1px solid transparent;
        border-radius: 16px;
        padding: 6px 14px;
        font-size: 13px;
        font-weight: 600;
    }}
    QPushButton.SortButton:hover {{
        background-color: {p['bg_pill']};
        color: {p['text_primary']};
    }}
    QPushButton.SortButton[active="true"] {{
        background-color: {p['bg_pill_hover']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
    }}

    /* ScrollAreas e ScrollBars */
    QScrollArea {{
        border: none;
        background-color: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border']};
        min-height: 30px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p['text_muted']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p['border']};
        min-width: 30px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p['text_muted']};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* Menus de Contexto e Tooltips */
    QMenu {{
        background-color: {p['bg_card']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 20px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {p['accent']};
        color: #FFFFFF;
    }}

    QToolTip {{
        background-color: {p['bg_card']};
        color: {p['text_primary']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 11px;
    }}
    """
