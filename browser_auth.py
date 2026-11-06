"""
Serviço e diálogo de autenticação do Reddit via Navegador Padrão do Usuário.
Abre uma página de login local no navegador padrão (Brave, Chrome, Firefox)
e captura o retorno via OAuth loopback ou sessão.
"""

from __future__ import annotations
import http.server
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote, urlparse
import webbrowser

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
import requests

from widgets import RedditLogoWidget


CONFIG_PATH = Path.home() / ".config" / "reddit-linux-app" / "config.json"
SESSION_FILE = Path.home() / ".config" / "reddit-linux-app" / "session.json"
USER_AGENT = "linux:br.janlucas.redditdesktop:v1.1.0 (by /u/janlucas)"

# Client ID padrão para installed app (ou configurável pelo usuário)
DEFAULT_CLIENT_ID = "v_b9c7HdQoA1Zg"  # Substituível pelo usuário na interface web


class AuthSignals(QObject):
    login_completed = Signal(dict)
    status_updated = Signal(str)


class BrowserAuthHandler(http.server.BaseHTTPRequestHandler):
    """Tratador HTTP para receber o retorno da autenticação no navegador."""

    server: "BrowserAuthServer"

    def log_message(self, format: str, *args: Any) -> None:
        # Silencia logs no terminal para não poluir
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/login"):
            self._serve_login_page()
        elif path == "/callback":
            self._handle_oauth_callback(query)
        elif path == "/success":
            self._serve_success_page()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/session":
            self._handle_session_post()
        elif parsed.path == "/api/config":
            self._handle_config_post()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_login_page(self) -> None:
        port = self.server.server_port
        state = self.server.oauth_state
        client_id = self.server.get_client_id()

        reddit_auth_url = (
            f"https://www.reddit.com/api/v1/authorize?"
            f"client_id={quote(client_id)}&"
            f"response_type=code&"
            f"state={quote(state)}&"
            f"redirect_uri={quote(f'http://127.0.0.1:{port}/callback')}&"
            f"duration=permanent&"
            f"scope=identity,read,vote,mysubreddits,history,save"
        )

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
      max-width: 540px;
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
      margin-top: 8px;
    }}
    .btn-secondary:hover {{ background-color: #2F454E; }}
    .bookmarklet-box {{
      background: #0B1416;
      border: 1px dashed #3D5560;
      border-radius: 8px;
      padding: 12px;
      text-align: center;
      margin: 10px 0;
    }}
    .bookmarklet {{
      display: inline-block;
      padding: 6px 14px;
      background: #24373E;
      color: #FF8555;
      border-radius: 6px;
      font-weight: 700;
      font-size: 12px;
      text-decoration: none;
      cursor: move;
    }}
    details {{
      margin-top: 14px;
      font-size: 12px;
      color: #8BA2AD;
    }}
    summary {{ cursor: pointer; font-weight: 600; padding: 4px 0; }}
    input[type="text"] {{
      width: 100%;
      padding: 10px 12px;
      background: #0B1416;
      border: 1px solid #364C55;
      border-radius: 8px;
      color: #F2F4F5;
      font-size: 13px;
      margin-top: 8px;
      margin-bottom: 10px;
    }}
    .footer-note {{
      font-size: 11px;
      color: #627A86;
      text-align: center;
      margin-top: 20px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">r/</div>
      <div>
        <h1>Reddit Linux Desktop</h1>
        <p class="sub">Conecte sua conta do Reddit com segurança no seu navegador</p>
      </div>
    </div>

    <!-- Opção 1: OAuth Direto no Reddit -->
    <div class="card">
      <h2>🔑 Opção 1: Autorizar via Reddit (Recomendado)</h2>
      <p>Clique no botão abaixo para abrir a página oficial de permissão da sua conta do Reddit.</p>
      <a class="btn" href="{reddit_auth_url}">Autorizar no Reddit</a>

      <details>
        <summary>⚙️ Usar seu próprio Client ID do Reddit</summary>
        <div style="margin-top: 8px;">
          <p style="margin-bottom: 8px;">
            Você pode registrar seu próprio app em <a href="https://www.reddit.com/prefs/apps" target="_blank" style="color: #FF6433;">reddit.com/prefs/apps</a> (tipo <i>installed app</i>, redirect: <code>http://127.0.0.1:{port}/callback</code>):
          </p>
          <input type="text" id="clientIdInput" value="{client_id}" placeholder="Cole seu Client ID aqui" />
          <button class="btn btn-secondary" onclick="saveClientId()">Salvar Client ID</button>
        </div>
      </details>
    </div>

    <!-- Opção 2: Conectar com 1-Clique da Sessão Atual do Navegador -->
    <div class="card">
      <h2>⚡ Opção 2: Conectar com Sessão do Navegador</h2>
      <p>Já está logado no <b>reddit.com</b> nesta aba ou janela? Arraste o botão abaixo para sua <b>Barra de Favoritos</b> e clique nele em qualquer página do Reddit:</p>
      <div class="bookmarklet-box">
        <a class="bookmarklet" href="javascript:(function(){{const c=document.cookie;if(!location.host.includes('reddit.com')){{alert('Abra primeiro qualquer página do reddit.com e clique neste favorito!');return;}}fetch('http://127.0.0.1:{port}/api/session',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{cookies:c}})}}).then(r=>r.json()).then(d=>{{if(d.ok){{alert('✓ Conectado com sucesso ao Reddit Linux Desktop!');window.location='http://127.0.0.1:{port}/success';}}else{{alert('Erro: '+d.error);}}}}).catch(e=>alert('Verifique se o Reddit Linux Desktop está aberto.'));}})();">📌 Conectar ao Reddit Linux</a>
      </div>
      <p style="font-size: 11px; color: #7B95A2; margin-top: 6px; margin-bottom: 0;">
        (Funciona instantaneamente lendo sua sessão já ativa sem precisar digitar senha).
      </p>
    </div>

    <div class="footer-note">
      🔒 Esta página está sendo servida localmente no seu computador (127.0.0.1). Nenhuma senha é salva por terceiros.
    </div>
  </div>

  <script>
    function saveClientId() {{
      const val = document.getElementById('clientIdInput').value.trim();
      if (!val) return;
      fetch('/api/config', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{client_id: val}})
      }}).then(() => location.reload());
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
    body {
      background-color: #0B1416;
      color: #F2F4F5;
      font-family: sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
    }
    .box {
      background: #131F23;
      border: 1px solid #24373E;
      border-radius: 16px;
      padding: 36px 30px;
      text-align: center;
      max-width: 440px;
      box-shadow: 0 12px 30px rgba(0,0,0,0.6);
    }
    .check {
      font-size: 52px;
      color: #46D160;
      margin-bottom: 12px;
    }
    h1 { font-size: 22px; margin-bottom: 10px; }
    p { color: #8BA2AD; font-size: 14px; line-height: 1.5; margin-bottom: 24px; }
    .btn {
      display: inline-block;
      padding: 10px 24px;
      background: #FF4500;
      color: white;
      text-decoration: none;
      font-weight: 700;
      border-radius: 9999px;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div class="box">
    <div class="check">✓</div>
    <h1>Login Concluído com Sucesso!</h1>
    <p>Sua conta do Reddit foi vinculada ao <b>Reddit Linux Desktop</b>.<br>Você já pode fechar esta aba do navegador e voltar para o aplicativo.</p>
    <a class="btn" href="javascript:window.close();">Fechar esta Aba</a>
  </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _handle_oauth_callback(self, query: Dict[str, list]) -> None:
        code_list = query.get("code")
        state_list = query.get("state")
        error_list = query.get("error")

        if error_list:
            err = error_list[0]
            self._serve_error_page(f"Autorização cancelada ou recusada pelo usuário ({err}).")
            return

        if not code_list or not state_list:
            self._serve_error_page("Código de autorização não recebido.")
            return

        code = code_list[0]
        state = state_list[0]

        if state != self.server.oauth_state:
            self._serve_error_page("Falha de segurança CSRF: estado de autorização inválido.")
            return

        client_id = self.server.get_client_id()
        redirect_uri = f"http://127.0.0.1:{self.server.server_port}/callback"

        try:
            # Troca o código pelo access_token no Reddit
            resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, ""),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            if resp.status_code == 200:
                payload = resp.json()
                if "access_token" in payload:
                    payload["client_id"] = client_id
                    # Salva sessão
                    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                    SESSION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

                    self.server.signals.login_completed.emit(payload)
                    self.send_response(302)
                    self.send_header("Location", "/success")
                    self.end_headers()
                    return
            self._serve_error_page(f"Falha ao validar token com o Reddit (HTTP {resp.status_code}): {resp.text}")
        except Exception as e:
            self._serve_error_page(f"Erro de conexão com o Reddit: {e}")

    def _handle_session_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            cookies_raw = data.get("cookies", "")

            # Parse de string de cookies
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

            res = json.dumps({"ok": False, "error": "Nenhum cookie de autenticação do Reddit encontrado."}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
        except Exception as e:
            res = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)

    def _handle_config_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            cid = str(data.get("client_id", "")).strip()
            if cid:
                cfg = {}
                if CONFIG_PATH.exists():
                    try:
                        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                cfg["reddit_client_id"] = cid
                CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            self.send_response(200)
            self.end_headers()
        except Exception:
            self.send_response(500)
            self.end_headers()

    def _serve_error_page(self, msg: str) -> None:
        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Erro no Login</title>
  <style>
    body {{ background: #0B1416; color: #F2F4F5; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .box {{ background: #131F23; border: 1px solid #482323; border-radius: 16px; padding: 32px; text-align: center; max-width: 460px; }}
    .err {{ font-size: 48px; color: #FF585B; margin-bottom: 10px; }}
    h1 {{ font-size: 20px; margin-bottom: 12px; }}
    p {{ color: #A2B6C0; font-size: 13px; line-height: 1.5; margin-bottom: 20px; }}
    a {{ color: #FF6433; text-decoration: none; font-weight: 700; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="err">⚠️</div>
    <h1>Não foi possível concluir o login</h1>
    <p>{msg}</p>
    <a href="/login">Tentar Novamente</a>
  </div>
</body>
</html>"""
        self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


class BrowserAuthServer(http.server.ThreadingHTTPServer):
    """Servidor HTTP temporário para loopback de autenticação."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        super().__init__((host, port), BrowserAuthHandler)
        self.oauth_state: str = secrets.token_urlsafe(16)
        self.signals = AuthSignals()

    def get_client_id(self) -> str:
        try:
            if CONFIG_PATH.exists():
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if cfg.get("reddit_client_id"):
                    return str(cfg["reddit_client_id"]).strip()
        except Exception:
            pass
        return DEFAULT_CLIENT_ID


class BrowserLoginDialog(QDialog):
    """Diálogo modal no desktop acompanhando o login no navegador padrão."""

    login_completed = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conectar ao Reddit via Navegador")
        self.setFixedSize(460, 290)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._server: Optional[BrowserAuthServer] = None
        self._server_thread: Optional[threading.Thread] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        logo = RedditLogoWidget(size=34)
        header_row.addWidget(logo)

        lbl_title = QLabel("Login no Navegador Padrão")
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #F2F4F5;")
        header_row.addWidget(lbl_title, 1)
        layout.addLayout(header_row)

        self.lbl_desc = QLabel(
            "Abrimos uma página de autorização no seu navegador padrão (Brave, Chrome ou Firefox).\n\n"
            "Complete a autorização na aba aberta para conectar sua conta."
        )
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setStyleSheet("color: #A2B6C0; font-size: 13px; line-height: 1.45;")
        layout.addWidget(self.lbl_desc)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # Indicador indeterminado animado
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar { background-color: #1A282D; border: none; border-radius: 2px; }"
            "QProgressBar::chunk { background-color: #FF4500; border-radius: 2px; }"
        )
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("⏳ Aguardando autorização no navegador...")
        self.lbl_status.setStyleSheet("color: #FF8555; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.lbl_status)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_reopen = QPushButton("🌐 Reabrir Navegador")
        self.btn_reopen.setProperty("class", "PillButton")
        self.btn_reopen.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_reopen.clicked.connect(self._open_browser)
        btn_row.addWidget(self.btn_reopen)

        btn_row.addStretch(1)

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setProperty("class", "PillButton")
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        layout.addLayout(btn_row)

        self._start_server_and_open_browser()

    def _start_server_and_open_browser(self) -> None:
        port = 8765
        for p in range(8765, 8780):
            try:
                self._server = BrowserAuthServer(port=p)
                port = p
                break
            except OSError:
                continue

        if not self._server:
            self.lbl_status.setText("⚠️ Não foi possível iniciar o servidor local de login.")
            return

        self._server.signals.login_completed.connect(self._on_success)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()

        self._open_browser()

    def _open_browser(self) -> None:
        if self._server:
            port = self._server.server_port
            url = f"http://127.0.0.1:{port}/login"
            webbrowser.open(url)

    def _on_success(self, data: dict) -> None:
        self.lbl_status.setText("✓ Conectado com sucesso!")
        self.lbl_status.setStyleSheet("color: #46D160; font-size: 13px; font-weight: 700;")
        self.progress.setVisible(False)
        self.login_completed.emit(data)
        QTimer.singleShot(700, self.accept)

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
