"""
Serviço e diálogo de autenticação do Reddit via Navegador Padrão do Usuário.
Detecta e sincroniza automaticamente a sessão do Reddit aberta no navegador (Brave, Chrome, Firefox),
ou orienta o login na tela oficial sem exigir criação de apps de desenvolvedor.
"""

from __future__ import annotations
import glob
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import webbrowser

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import requests
from shiboken6 import isValid

from widgets import RedditLogoWidget, UiSkillsButton


SESSION_FILE = Path.home() / ".config" / "reddit-linux-app" / "session.json"
USER_AGENT = "linux:br.janlucas.redditdesktop:v1.1.0 (by /u/janlucas)"


def _get_keyring_password(app_name: str) -> bytes:
    """Busca a chave mestra de criptografia do navegador no Secret Service (GNOME Keyring)."""
    candidates = [app_name, app_name.lower(), app_name.capitalize(), "chromium", "chrome"]
    for cand in candidates:
        try:
            out = subprocess.check_output(
                ["secret-tool", "lookup", "application", cand],
                stderr=subprocess.DEVNULL,
            )
            val = out.strip()
            if val:
                return val
        except Exception:
            pass
    return b"peanuts"


def _extract_chromium_cookies(db_path: Path, app_name: str) -> Dict[str, str]:
    """Extrai e descriptografa cookies do Reddit de navegadores baseados em Chromium."""
    if not db_path.exists():
        return {}

    tmp_path = Path("/tmp") / f"reddit_cookies_{app_name}_{os.getpid()}_{time.time_ns()}.sqlite"
    try:
        shutil.copy(db_path, tmp_path)
        conn = sqlite3.connect(tmp_path)
        c = conn.cursor()
        c.execute("""
            SELECT name, encrypted_value 
            FROM cookies 
            WHERE host_key LIKE '%reddit.com%' AND name IN ('token_v2', 'reddit_session', 'loid')
        """)
        rows = c.fetchall()
        conn.close()
    except Exception:
        return {}
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    if not rows:
        return {}

    password = _get_keyring_password(app_name)
    salt = b"saltysalt"
    key = hashlib.pbkdf2_hmac("sha1", password, salt, 1, 16)
    iv = b" " * 16

    cookies: Dict[str, str] = {}
    for name, enc in rows:
        if not enc or len(enc) < 4:
            continue
        try:
            is_v11 = enc.startswith(b"v11")
            raw_payload = enc[3:]
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()
            dec = decryptor.update(raw_payload) + decryptor.finalize()
            pad = dec[-1]
            val_bytes = dec[:-pad]
            if is_v11:
                val_bytes = val_bytes[32:]  # Descarta cabeçalho HMAC de 32 bytes do v11
            val = val_bytes.decode("utf-8", errors="ignore").strip()
            if val:
                cookies[name] = val
        except Exception:
            pass

    return cookies


def _extract_firefox_cookies() -> Dict[str, str]:
    """Extrai cookies do Firefox (armazenados sem criptografia em cookies.sqlite)."""
    ff_profiles = list(Path.home().glob(".mozilla/firefox/*.default*/cookies.sqlite"))
    for db_path in ff_profiles:
        if not db_path.exists():
            continue
        tmp_path = Path("/tmp") / f"ff_cookies_{os.getpid()}_{time.time_ns()}.sqlite"
        try:
            shutil.copy(db_path, tmp_path)
            conn = sqlite3.connect(tmp_path)
            c = conn.cursor()
            c.execute("""
                SELECT name, value 
                FROM moz_cookies 
                WHERE host LIKE '%reddit.com%' AND name IN ('token_v2', 'reddit_session', 'loid')
            """)
            rows = c.fetchall()
            conn.close()
            cookies = {r[0]: r[1] for r in rows if r[1]}
            if "token_v2" in cookies or "reddit_session" in cookies:
                return cookies
        except Exception:
            pass
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
    return {}


def detect_browser_reddit_session() -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, Any]]]:
    """
    Tenta detectar automaticamente uma sessão ativa do Reddit nos navegadores instalados
    (Brave, Chrome, Chromium ou Firefox) e valida com a API do Reddit.
    Retorna (cookies, user_info) ou (None, None).
    """
    browser_targets = [
        (Path.home() / ".config/BraveSoftware/Brave-Browser/Default/Cookies", "brave"),
        (Path.home() / ".config/google-chrome/Default/Cookies", "chrome"),
        (Path.home() / ".config/chromium/Default/Cookies", "chromium"),
    ]

    for db_path, app_name in browser_targets:
        cookies = _extract_chromium_cookies(db_path, app_name)
        if cookies and ("token_v2" in cookies or "reddit_session" in cookies):
            user_info = verify_reddit_token(cookies.get("token_v2"))
            if user_info:
                return cookies, user_info

    # Tenta Firefox
    ff_cookies = _extract_firefox_cookies()
    if ff_cookies and ("token_v2" in ff_cookies or "reddit_session" in ff_cookies):
        user_info = verify_reddit_token(ff_cookies.get("token_v2"))
        if user_info:
            return ff_cookies, user_info

    return None, None


def verify_reddit_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Verifica se um token_v2 é válido chamando o endpoint do usuário no Reddit."""
    if not token:
        return None
    try:
        resp = requests.get(
            "https://oauth.reddit.com/api/v1/me",
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Bearer {token}",
            },
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and data.get("name"):
                return {
                    "username": str(data["name"]),
                    "karma": int(data.get("total_karma", 0) or 0),
                    "comment_karma": int(data.get("comment_karma", 0) or 0),
                    "link_karma": int(data.get("link_karma", 0) or 0),
                    "icon": str(data.get("icon_img") or data.get("snoovatar_img") or ""),
                }
    except Exception:
        pass
    return None


class AuthSignals(QObject):
    login_completed = Signal(dict)
    status_updated = Signal(str)


class BrowserAuthHandler(http.server.BaseHTTPRequestHandler):
    """Tratador HTTP para receber o retorno da sincronização no navegador."""

    server: "BrowserAuthServer"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/login"):
            self._serve_sync_page()
        elif path == "/success":
            self._serve_success_page()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            self._handle_sync_request()
        elif parsed.path == "/api/session":
            self._handle_manual_session()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_sync_page(self) -> None:
        port = self.server.server_port
        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Conectar Reddit Linux Desktop</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #0B1416;
      color: #F2F4F5;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      padding: 24px 16px;
    }}
    .container {{
      background-color: #131F23;
      border: 1px solid #24373E;
      border-radius: 18px;
      max-width: 520px;
      width: 100%;
      padding: 32px 28px;
      box-shadow: 0 16px 36px rgba(0,0,0,0.5);
    }}
    .header {{
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 24px;
      border-bottom: 1px solid #24373E;
      padding-bottom: 18px;
    }}
    .logo {{
      width: 44px;
      height: 44px;
      background: #FF4500;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-weight: 900;
      font-size: 24px;
    }}
    h1 {{ font-size: 20px; font-weight: 800; }}
    p.sub {{ color: #8BA2AD; font-size: 13px; margin-top: 4px; }}
    .card {{
      background-color: #1A282D;
      border: 1px solid #2B3D44;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 18px;
    }}
    .card h2 {{
      font-size: 15px;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .card p {{
      font-size: 13px;
      color: #A2B6C0;
      line-height: 1.5;
      margin-bottom: 16px;
    }}
    .btn {{
      display: inline-block;
      width: 100%;
      text-align: center;
      padding: 12px 18px;
      background-color: #FF4500;
      color: #FFFFFF;
      font-size: 14px;
      font-weight: 700;
      border-radius: 9999px;
      text-decoration: none;
      border: none;
      cursor: pointer;
      transition: background-color 0.2s;
    }}
    .btn:hover {{ background-color: #E03D00; }}
    .btn-secondary {{
      background-color: #24373E;
      color: #F2F4F5;
      margin-top: 10px;
    }}
    .btn-secondary:hover {{ background-color: #2F454E; }}
    #status {{
      margin-top: 12px;
      font-size: 13px;
      text-align: center;
      font-weight: 600;
      min-height: 20px;
    }}
    .footer-note {{
      font-size: 11px;
      color: #627A86;
      text-align: center;
      margin-top: 14px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">r/</div>
      <div>
        <h1>Reddit Linux Desktop</h1>
        <p class="sub">Conectar sua conta do Reddit</p>
      </div>
    </div>

    <div class="card">
      <h2>🌐 Conectar com sua Conta</h2>
      <p>
        Certifique-se de que você está conectado ao <b>reddit.com</b> neste navegador e clique no botão abaixo para sincronizar instantaneamente com o aplicativo:
      </p>
      <button class="btn" id="btnSync" onclick="syncNow()">🔄 Conectar Conta deste Navegador</button>
      <a class="btn btn-secondary" href="https://www.reddit.com/login" target="_blank">↗ Abrir Reddit.com para Fazer Login</a>
      <div id="status"></div>
    </div>

    <div class="footer-note">
      🔒 Sincronização direta e segura via armazenamento local. Nenhuma senha trafega pela internet ou terceiros.
    </div>
  </div>

  <script>
    async function syncNow() {{
      const st = document.getElementById('status');
      st.innerHTML = '⏳ Verificando sessão do Reddit...';
      st.style.color = '#FF8555';
      try {{
        const r = await fetch('http://127.0.0.1:{port}/api/sync', {{method: 'POST'}});
        const d = await r.json();
        if (d.ok) {{
          st.innerHTML = '✓ Conectado como <b>u/' + d.username + '</b>! Redirecionando...';
          st.style.color = '#46D160';
          setTimeout(() => location.href = '/success', 800);
        }} else {{
          st.innerHTML = '⚠️ Nenhuma sessão ativa detectada. Faça login no reddit.com e tente novamente.';
          st.style.color = '#FF585B';
        }}
      }} catch (e) {{
        st.innerHTML = 'Erro de comunicação com o app.';
        st.style.color = '#FF585B';
      }}
    }}
  </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_success_page(self) -> None:
        html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Login Concluído!</title>
  <style>
    body { background-color: #0B1416; color: #F2F4F5; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .box { background: #131F23; border: 1px solid #24373E; border-radius: 16px; padding: 36px 30px; text-align: center; max-width: 440px; box-shadow: 0 12px 30px rgba(0,0,0,0.6); }
    .check { font-size: 52px; color: #46D160; margin-bottom: 12px; }
    h1 { font-size: 22px; margin-bottom: 10px; }
    p { color: #8BA2AD; font-size: 14px; line-height: 1.5; margin-bottom: 24px; }
    .btn { display: inline-block; padding: 10px 24px; background: #FF4500; color: white; text-decoration: none; font-weight: 700; border-radius: 9999px; font-size: 14px; }
  </style>
</head>
<body>
  <div class="box">
    <div class="check">✓</div>
    <h1>Login Concluído com Sucesso!</h1>
    <p>Sua conta foi sincronizada com o <b>Reddit Linux Desktop</b>.<br>Você já pode fechar esta aba e voltar para o aplicativo.</p>
    <a class="btn" href="javascript:window.close();">Fechar esta Aba</a>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _handle_sync_request(self) -> None:
        cookies, user = detect_browser_reddit_session()
        if cookies and user:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            self.server.signals.login_completed.emit(cookies)
            res = json.dumps({"ok": True, "username": user["username"]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
        else:
            res = json.dumps({"ok": False}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)

    def _handle_manual_session(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            cookies_raw = data.get("cookies", "")

            cookie_dict: Dict[str, str] = {}
            for part in cookies_raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookie_dict[k.strip()] = v.strip()

            if "token_v2" in cookie_dict or "reddit_session" in cookie_dict:
                SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                SESSION_FILE.write_text(json.dumps(cookie_dict, indent=2), encoding="utf-8")
                self.server.signals.login_completed.emit(cookie_dict)
                res = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(res)))
                self.end_headers()
                self.wfile.write(res)
                return

            res = json.dumps({"ok": False, "error": "Cookies inválidos."}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(res)
        except Exception as e:
            res = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(res)


class BrowserAuthServer(http.server.ThreadingHTTPServer):
    """Servidor HTTP temporário para loopback de autenticação."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        super().__init__((host, port), BrowserAuthHandler)
        self.signals = AuthSignals()


class BrowserLoginDialog(QDialog):
    """
    Diálogo modal no desktop que detecta automaticamente a sessão do Reddit no navegador
    ou abre o portal local com stepper visual de 4 estágios e porta dinâmica (CR-07).
    """

    login_completed = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conectar ao Reddit via Navegador")
        self.setFixedSize(540, 410)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._server: Optional[BrowserAuthServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._bound_port: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        logo = RedditLogoWidget(size=34)
        header_row.addWidget(logo)

        lbl_title = QLabel("Conectar Conta do Reddit")
        lbl_title.setProperty("role", "title")
        header_row.addWidget(lbl_title, 1)

        btn_close = UiSkillsButton("✕")
        btn_close.setProperty("class", "PillButton")
        btn_close.setFixedSize(30, 30)
        btn_close.clicked.connect(self.reject)
        header_row.addWidget(btn_close)
        layout.addLayout(header_row)

        self.lbl_desc = QLabel(
            "Verificando sessão ativa no seu navegador padrão (Brave, Chrome ou Firefox)..."
        )
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setProperty("role", "body")
        layout.addWidget(self.lbl_desc)

        # Stepper Visual de 4 Estágios (Sprint 5 & CR-07)
        stepper_frame = QFrame()
        stepper_frame.setProperty("class", "InfoPanel")
        sf_layout = QVBoxLayout(stepper_frame)
        sf_layout.setContentsMargins(14, 10, 14, 10)
        sf_layout.setSpacing(6)

        self.step_labels = []
        for idx, step_txt in enumerate(
            (
                "1. Detectando sessão existente nos navegadores locais",
                "2. Iniciando servidor local de sincronização (127.0.0.1)",
                "3. Aguardando confirmação no navegador padrão",
                "4. Validação de token e sincronização concluída",
            ),
            start=1,
        ):
            lbl_s = QLabel(f"○  {step_txt}")
            lbl_s.setProperty("role", "caption")
            sf_layout.addWidget(lbl_s)
            self.step_labels.append(lbl_s)

        layout.addWidget(stepper_frame)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("🔍 Detectando sessão no navegador...")
        self.lbl_status.setProperty("role", "status_warn")
        layout.addWidget(self.lbl_status)

        # Painel expansível de Fallback para inserção manual de cookie (CR-07)
        self.manual_panel = QFrame()
        self.manual_panel.setProperty("class", "InfoPanel")
        self.manual_panel.setVisible(False)
        mp_layout = QHBoxLayout(self.manual_panel)
        mp_layout.setContentsMargins(10, 8, 10, 8)
        mp_layout.setSpacing(8)

        self.manual_cookie_input = QLineEdit()
        self.manual_cookie_input.setPlaceholderText(
            "Cole seu token_v2 ou cookie reddit_session aqui..."
        )
        mp_layout.addWidget(self.manual_cookie_input, 1)

        btn_apply_cookie = UiSkillsButton("Validar")
        btn_apply_cookie.setProperty("class", "PillButtonAccent")
        btn_apply_cookie.clicked.connect(self._apply_manual_cookie_fallback)
        mp_layout.addWidget(btn_apply_cookie)

        layout.addWidget(self.manual_panel)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_reopen = UiSkillsButton("🌐 Abrir no Navegador")
        self.btn_reopen.setProperty("class", "PillButtonAccent")
        self.btn_reopen.clicked.connect(self._open_browser)
        btn_row.addWidget(self.btn_reopen)

        self.btn_manual_toggle = UiSkillsButton("🔑 Cookie Manual")
        self.btn_manual_toggle.setProperty("class", "PillButton")
        self.btn_manual_toggle.setToolTip(
            "Alternativa caso seu navegador esteja isolado em sandbox Flatpak/Snap"
        )
        self.btn_manual_toggle.clicked.connect(
            lambda: self.manual_panel.setVisible(not self.manual_panel.isVisible())
        )
        btn_row.addWidget(self.btn_manual_toggle)

        btn_row.addStretch(1)

        self.btn_cancel = UiSkillsButton("Cancelar")
        self.btn_cancel.setProperty("class", "PillButton")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        layout.addLayout(btn_row)

        self._set_active_step(1)
        QTimer.singleShot(250, self._check_auto_detect_or_start_server)

    def _set_active_step(self, active_step: int, port_info: str = "") -> None:
        base_texts = [
            "1. Detectando sessão existente nos navegadores locais",
            f"2. Servidor local ativo em {port_info}"
            if port_info
            else "2. Iniciando servidor local de sincronização (127.0.0.1)",
            "3. Aguardando confirmação no navegador padrão",
            "4. Validação de token e sincronização concluída",
        ]
        for idx, lbl in enumerate(self.step_labels, start=1):
            if not isValid(lbl):
                continue
            txt = base_texts[idx - 1]
            if idx < active_step:
                lbl.setText(f"✓  {txt}")
                lbl.setProperty("role", "status_ok")
            elif idx == active_step:
                lbl.setText(f"●  {txt}")
                lbl.setProperty("role", "subtitle")
            else:
                lbl.setText(f"○  {txt}")
                lbl.setProperty("role", "caption")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _set_status(self, text: str, role: str = "status_warn") -> None:
        if not isValid(self.lbl_status):
            return
        self.lbl_status.setText(text)
        self.lbl_status.setProperty("role", role)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)

    def _check_auto_detect_or_start_server(self) -> None:
        if not isValid(self):
            return
        cookies, user = detect_browser_reddit_session()
        if cookies and user:
            uname = user.get("username", "Usuário")
            self._set_active_step(5)
            self.lbl_desc.setText(
                f"Conta identificada automaticamente no seu navegador: <b>u/{uname}</b>!"
            )
            self._set_status(f"✓ Conectado como u/{uname}!", "status_ok")
            self.progress.setVisible(False)

            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")

            self.login_completed.emit(cookies)
            QTimer.singleShot(600, self.accept)
            return

        self.lbl_desc.setText(
            "Abra o Reddit no seu navegador padrão para sincronizar sua conta com um clique."
        )
        self._start_server_and_open_browser()

    def _start_server_and_open_browser(self) -> None:
        # Escaneia portas 8765..8779 com fallback para porta efêmera 0 do SO (CR-07)
        for p in [*range(8765, 8780), 0]:
            try:
                self._server = BrowserAuthServer(port=p)
                self._bound_port = int(self._server.server_port)
                break
            except OSError:
                continue

        if not self._server:
            self._set_status(
                "⚠️ Porta local indisponível. Use 'Cookie Manual' abaixo.",
                "status_error",
            )
            self.manual_panel.setVisible(True)
            return

        addr_str = f"127.0.0.1:{self._bound_port}"
        self._set_active_step(3, port_info=addr_str)
        self._set_status(
            f"⏳ Servidor local ativo em {addr_str} — aguardando navegador...",
            "status_warn",
        )

        self._server.signals.login_completed.connect(self._on_success)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._server_thread.start()

        self._open_browser()

    def _apply_manual_cookie_fallback(self) -> None:
        raw = self.manual_cookie_input.text().strip()
        if not raw:
            return
        cookie_dict: Dict[str, str] = {}
        if "=" in raw:
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookie_dict[k.strip()] = v.strip()
        else:
            cookie_dict["token_v2"] = raw

        user = verify_reddit_token(cookie_dict.get("token_v2"))
        if user or "reddit_session" in cookie_dict:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(
                json.dumps(cookie_dict, indent=2), encoding="utf-8"
            )
            self._on_success(cookie_dict)
        else:
            self._set_status(
                "⚠️ Token ou cookie inválido. Verifique e tente novamente.",
                "status_error",
            )

    def _open_browser(self) -> None:
        if self._server and self._bound_port > 0:
            webbrowser.open(f"http://127.0.0.1:{self._bound_port}/login")
        else:
            webbrowser.open("https://www.reddit.com/login")

    def _on_success(self, data: dict) -> None:
        if not isValid(self):
            return
        addr_str = f"127.0.0.1:{self._bound_port}" if self._bound_port else ""
        self._set_active_step(5, port_info=addr_str)
        self._set_status("✓ Conectado com sucesso!", "status_ok")
        self.progress.setVisible(False)
        self.login_completed.emit(data)
        QTimer.singleShot(600, self.accept)

    def closeEvent(self, event) -> None:
        self._shutdown_server()
        super().closeEvent(event)

    def reject(self) -> None:
        self._shutdown_server()
        super().reject()

    def accept(self) -> None:
        self._shutdown_server()
        super().accept()

    def _shutdown_server(self) -> None:
        if self._server:
            srv = self._server
            self._server = None
            threading.Thread(target=srv.shutdown, daemon=True).start()
