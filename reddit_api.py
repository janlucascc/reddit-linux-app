"""
Cliente de API do Reddit nativo para Linux.
Combina endpoints OAuth (oauth.reddit.com) e feeds Atom/RSS oficiais
com sessão persistente, cookies unificados, backoff exponencial e cache em memória.
"""

from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
import html
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import requests


USER_AGENT = "linux:br.janlucas.redditdesktop:v1.1.0 (by /u/janlucas)"
SUBREDDIT_NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
SESSION_FILE = Path.home() / ".config" / "reddit-linux-app" / "session.json"


def is_valid_subreddit_name(name: str) -> bool:
    """Valida se o nome de comunidade segue o padrão permitido pelo Reddit (BUG-010)."""
    clean = name.strip().lstrip("/").removeprefix("r/").strip("/")
    return bool(SUBREDDIT_NAME_RE.match(clean))


def format_relative_time(timestamp_utc: float) -> str:
    """Formata um timestamp UTC em texto relativo amigável em português."""
    if not timestamp_utc:
        return "agora"
    now = time.time()
    diff = max(0, int(now - timestamp_utc))
    if diff < 60:
        return "agora mesmo"
    minutes = diff // 60
    if minutes < 60:
        return f"há {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"há {hours} h"
    days = hours // 24
    if days < 30:
        return f"há {days} d"
    months = days // 30
    if months < 12:
        return f"há {months} m"
    years = days // 365
    return f"há {years} a"


def format_count(num: int) -> str:
    """Formata contagens numéricas no estilo Reddit Web (ex: 14.2 mil)."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f} mi"
    if num >= 1_000:
        return f"{num / 1_000:.1f} mil"
    return str(num)


def parse_iso_timestamp(iso_str: str) -> float:
    """Converte string ISO 8601 de feeds Atom em timestamp Unix."""
    if not iso_str:
        return time.time()
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return time.time()


def strip_html_to_text(raw_html: str) -> str:
    """Converte HTML de comentários/posts RSS em texto limpo preservando quebras de parágrafo."""
    if not raw_html:
        return ""
    text = raw_html
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li\s*>", "• ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(
        r"\s*submitted by\s+/u/[^\s]+\s*(\[link\])?\s*(\[comments\])?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class PostItem:
    id: str
    subreddit: str
    subreddit_prefixed: str
    author: str
    title: str
    selftext: str
    score: int
    num_comments: int
    created_utc: float
    permalink: str
    url: str
    domain: str = ""
    flair: str = ""
    is_video: bool = False
    video_url: str = ""
    video_dash_url: str = ""
    video_hls_url: str = ""
    is_self: bool = False
    over_18: bool = False
    spoiler: bool = False
    oc: bool = False
    stickied: bool = False
    upvote_ratio: float = 1.0
    likes: Optional[bool] = None
    thumbnail_url: str = ""
    preview_image_url: str = ""
    highres_image_url: str = ""
    preview_width: int = 0
    preview_height: int = 0
    gallery_images: List[str] = field(default_factory=list)
    translated_title: Optional[str] = None
    translated_selftext: Optional[str] = None
    show_translated: bool = False

    @property
    def display_title(self) -> str:
        if self.show_translated and self.translated_title:
            return self.translated_title
        return self.title

    @property
    def display_selftext(self) -> str:
        if self.show_translated and self.translated_selftext:
            return self.translated_selftext
        return self.selftext

    @property
    def relative_time(self) -> str:
        return format_relative_time(self.created_utc)

    @property
    def formatted_score(self) -> str:
        return format_count(self.score)

    @property
    def formatted_comments(self) -> str:
        return format_count(self.num_comments)


@dataclass
class CommentItem:
    id: str
    author: str
    body: str
    created_utc: float
    score: int = 1
    depth: int = 0
    is_submitter: bool = False
    parent_id: str = ""
    distinguished: str = ""
    author_flair_text: str = ""
    likes: Optional[bool] = None
    children_ids: List[str] = field(default_factory=list)
    translated_body: Optional[str] = None
    show_translated: bool = False

    @property
    def display_body(self) -> str:
        if self.show_translated and self.translated_body:
            return self.translated_body
        return self.body

    @property
    def relative_time(self) -> str:
        return format_relative_time(self.created_utc)


@dataclass
class SubredditInfo:
    name: str
    display_name_prefixed: str
    title: str
    public_description: str
    subscribers: int = 0
    active_users: int = 0
    icon_url: str = ""
    banner_url: str = ""
    primary_color: str = "#FF4500"
    translated_title: Optional[str] = None
    translated_description: Optional[str] = None
    show_translated: bool = False


class RedditClient:
    """Gerencia requisições de feed, busca, detalhes de comunidade e comentários."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._token_v2: Optional[str] = None
        self._token_expiry: float = 0.0
        self._oauth_access_token: Optional[str] = None
        self._oauth_refresh_token: Optional[str] = None
        self._oauth_client_id: str = ""
        self._lock = threading.Lock()
        self._sub_cache: Dict[str, SubredditInfo] = {}
        self._image_cache: OrderedDict[str, bytes] = OrderedDict()
        self._logged_in_user: Optional[Dict[str, Any]] = None

        self._load_saved_session()

    def _load_saved_session(self) -> None:
        try:
            if SESSION_FILE.exists():
                saved = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self.apply_session_payload(saved)
        except Exception:
            pass

    def apply_session_payload(self, data: Dict[str, Any]) -> None:
        """Aplica dados de autenticação (OAuth tokens ou Cookies de sessão)."""
        if "access_token" in data:
            with self._lock:
                self._oauth_access_token = str(data["access_token"])
                self._token_v2 = self._oauth_access_token
                self._oauth_refresh_token = data.get("refresh_token")
                self._oauth_client_id = str(data.get("client_id", ""))
                expires_in = data.get("expires_in", 3600)
                self._token_expiry = time.time() + float(expires_in) - 60
            self.fetch_current_user(force=True)
        else:
            self.apply_saved_session(data)

    def apply_saved_session(self, cookies: Dict[str, str]) -> None:
        """Aplica os cookies capturados na tela de login oficial."""
        with self._lock:
            self._session.cookies.update(cookies)
            if "token_v2" in cookies:
                self._token_v2 = cookies["token_v2"]
                self._token_expiry = time.time() + 86400 * 30  # Sessão de longa duração
        self.fetch_current_user(force=True)

    def fetch_current_user(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Verifica se há um usuário autenticado retornando dados de perfil."""
        if not force and self._logged_in_user is not None:
            return self._logged_in_user

        try:
            resp = self._oauth_get("/api/v1/me")
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, dict) and data.get("name"):
                    info = {
                        "username": str(data["name"]),
                        "karma": int(data.get("total_karma", 0) or 0),
                        "comment_karma": int(data.get("comment_karma", 0) or 0),
                        "link_karma": int(data.get("link_karma", 0) or 0),
                        "icon": str(data.get("icon_img") or data.get("snoovatar_img") or ""),
                    }
                    self._logged_in_user = info
                    return info
        except Exception:
            pass

        self._logged_in_user = None
        return None

    def vote(self, item_id: str, direction: int) -> bool:
        """
        Vota em um post ou comentário (dir: 1 = upvote, 0 = neutral, -1 = downvote).
        Retorna True se o voto foi aceito pelo Reddit.
        """
        token = self._ensure_session()
        fullname = item_id if (item_id.startswith("t3_") or item_id.startswith("t1_")) else f"t3_{item_id}"
        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}" if token else "",
        }
        try:
            resp = self._session.post(
                "https://oauth.reddit.com/api/vote",
                headers=headers,
                data={"id": fullname, "dir": direction},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def submit_post(
        self,
        subreddit: str,
        title: str,
        kind: str = "self",
        text_or_url: str = "",
        nsfw: bool = False,
        spoiler: bool = False,
        oc: bool = False,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Publica um post em um subreddit (kind: 'self' para texto, 'link' para URL).
        Retorna (sucesso, mensagem_ou_erro, url_do_post).
        """
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        if not clean_sub:
            return False, "Informe o nome da comunidade (ex: linux, brasil).", None
        if not title.strip():
            return False, "O título da publicação é obrigatório.", None

        token = self._ensure_session()
        if not token or not self._logged_in_user:
            return False, "Você precisa estar conectado à sua conta para criar postagens.", None

        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
        data: Dict[str, Any] = {
            "api_type": "json",
            "sr": clean_sub,
            "title": title.strip(),
            "kind": kind,
            "resubmit": "true",
            "sendreplies": "true",
        }
        if nsfw:
            data["nsfw"] = "true"
        if spoiler:
            data["spoiler"] = "true"
        if oc:
            data["original_content"] = "true"

        if kind == "self":
            data["text"] = text_or_url
        else:
            data["url"] = text_or_url

        try:
            resp = self._session.post(
                "https://oauth.reddit.com/api/submit",
                headers=headers,
                data=data,
                timeout=15,
            )
            if resp.status_code == 200:
                payload = resp.json()
                errors = payload.get("json", {}).get("errors", [])
                if errors:
                    err_msg = ", ".join(e[1] if len(e) > 1 else str(e) for e in errors)
                    return False, f"Erro do Reddit: {err_msg}", None
                post_data = payload.get("json", {}).get("data", {})
                post_url = post_data.get("url") or f"https://reddit.com/r/{clean_sub}"
                return True, "Publicado com sucesso!", post_url
            return False, f"Falha ao publicar (HTTP {resp.status_code}): {resp.text[:120]}", None
        except Exception as e:
            return False, f"Erro de conexão: {e}", None

    def submit_comment(
        self,
        parent_fullname: str,
        text: str,
    ) -> Tuple[bool, str, Optional[CommentItem]]:
        """
        Publica um comentário ou resposta (parent_fullname: 't3_...' ou 't1_...').
        Retorna (sucesso, mensagem_ou_erro, CommentItem).
        """
        if not text.strip():
            return False, "Digite o conteúdo do comentário antes de enviar.", None

        token = self._ensure_session()
        if not token or not self._logged_in_user:
            return False, "Faça login na sua conta do Reddit para comentar.", None

        if not (parent_fullname.startswith("t3_") or parent_fullname.startswith("t1_")):
            parent_fullname = f"t1_{parent_fullname}"

        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
        data = {
            "api_type": "json",
            "thing_id": parent_fullname,
            "text": text.strip(),
        }
        try:
            resp = self._session.post(
                "https://oauth.reddit.com/api/comment",
                headers=headers,
                data=data,
                timeout=15,
            )
            if resp.status_code == 200:
                payload = resp.json()
                errors = payload.get("json", {}).get("errors", [])
                if errors:
                    err_msg = ", ".join(e[1] if len(e) > 1 else str(e) for e in errors)
                    return False, f"Erro do Reddit: {err_msg}", None
                things = payload.get("json", {}).get("data", {}).get("things", [])
                c_id = str(int(time.time()))
                if things and isinstance(things[0], dict):
                    td = things[0].get("data", {})
                    c_id = str(td.get("id") or c_id).replace("t1_", "")
                username = self._logged_in_user.get("username", "voce")
                new_item = CommentItem(
                    id=c_id,
                    author=username,
                    body=text.strip(),
                    created_utc=time.time(),
                    score=1,
                    depth=0,
                    parent_id=parent_fullname,
                )
                return True, "Comentário publicado!", new_item
            return False, f"Falha ao comentar (HTTP {resp.status_code})", None
        except Exception as exc:
            return False, f"Erro de conexão: {exc}", None

    def fetch_user_subreddits(self) -> List[str]:
        """
        Retorna a lista de nomes de subreddits inscritos do usuário logado.
        """
        token = self._ensure_session()
        if not token or not self._logged_in_user:
            return []
        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
        try:
            resp = self._session.get(
                "https://oauth.reddit.com/subreddits/mine/subscriber?limit=100",
                headers=headers,
                timeout=12,
            )
            if resp.status_code == 200:
                payload = resp.json()
                children = payload.get("data", {}).get("children", [])
                return [
                    item.get("data", {}).get("display_name", "")
                    for item in children
                    if item.get("data", {}).get("display_name")
                ]
        except Exception:
            pass
        return []

    def subscribe(self, subreddit: str, action: str = "sub") -> bool:
        """
        Inscreve ('sub') ou desinscreve ('unsub') o usuário em um subreddit.
        """
        token = self._ensure_session()
        if not token or not self._logged_in_user:
            return False
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
        try:
            resp = self._session.post(
                "https://oauth.reddit.com/api/subscribe",
                headers=headers,
                data={"action": action, "sr_name": clean_sub},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def logout(self) -> None:
        """Encerra a sessão ativa e redefine para modo anônimo."""
        with self._lock:
            self._logged_in_user = None
            self._token_v2 = None
            self._oauth_access_token = None
            self._oauth_refresh_token = None
            self._token_expiry = 0
            self._session.cookies.clear()
            try:
                if SESSION_FILE.exists():
                    SESSION_FILE.unlink()
            except Exception:
                pass
        self._ensure_session(force=True)

    def _ensure_session(self, force: bool = False) -> Optional[str]:
        with self._lock:
            now = time.time()
            if self._oauth_access_token and self._oauth_refresh_token and now >= self._token_expiry:
                try:
                    resp = requests.post(
                        "https://www.reddit.com/api/v1/access_token",
                        auth=(self._oauth_client_id, ""),
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": self._oauth_refresh_token,
                        },
                        headers={"User-Agent": USER_AGENT},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        payload = resp.json()
                        new_tok = payload.get("access_token")
                        if new_tok:
                            self._token_v2 = new_tok
                            self._oauth_access_token = new_tok
                            self._token_expiry = now + float(payload.get("expires_in", 3600)) - 60
                            return self._token_v2
                except Exception:
                    pass

            if not force and self._token_v2 and now < self._token_expiry:
                return self._token_v2
            try:
                self._session.get("https://old.reddit.com/login/", timeout=10)
                token = self._session.cookies.get("token_v2")
                if token:
                    self._token_v2 = token
                    self._token_expiry = now + 3600
                return self._token_v2
            except Exception:
                return self._token_v2

    def _oauth_get(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> requests.Response:
        """Executa GET autenticado usando self._session para preservar Cookie Jar (BUG-003)."""
        token = self._ensure_session()
        req_params = dict(params or {})
        req_params["raw_json"] = 1
        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}" if token else "",
        }
        url = f"https://oauth.reddit.com{path}"
        resp = self._session.get(url, params=req_params, headers=headers, timeout=12)
        if resp.status_code in (401, 403):
            token = self._ensure_session(force=True)
            headers["Authorization"] = f"Bearer {token}" if token else ""
            resp = self._session.get(url, params=req_params, headers=headers, timeout=12)
        return resp

    def fetch_subreddit_posts(
        self,
        subreddit: str = "popular",
        sort: str = "hot",
        time_filter: str = "day",
        after: Optional[str] = None,
        limit: int = 25,
    ) -> Tuple[List[PostItem], Optional[str]]:
        """Busca postagens de um subreddit via oauth.reddit.com."""
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        if not clean_sub:
            clean_sub = "popular"

        encoded_sub = quote(clean_sub, safe="")
        encoded_sort = quote(sort.strip(), safe="")

        params: Dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        if sort in ("top", "controversial"):
            params["t"] = time_filter

        if clean_sub == "home":
            path = f"/{encoded_sort}" if encoded_sort != "hot" else "/"
            resp = self._oauth_get(path, params=params)
        else:
            resp = self._oauth_get(f"/r/{encoded_sub}/{encoded_sort}", params=params)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Não foi possível carregar r/{clean_sub} (HTTP {resp.status_code})"
            )

        data = resp.json().get("data", {})
        children = data.get("children", [])
        next_after = data.get("after")

        posts: List[PostItem] = []
        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "t3":
                continue
            item = self._parse_post_json(child.get("data") or {})
            posts.append(item)

        return posts, next_after

    def _parse_post_json(self, d: Dict[str, Any]) -> PostItem:
        post_id = str(d.get("id", ""))
        subreddit = str(d.get("subreddit", "reddit"))
        sub_prefixed = str(d.get("subreddit_name_prefixed", f"r/{subreddit}"))
        author = str(d.get("author", "deleted"))
        title = html.unescape(str(d.get("title", "")))
        selftext = html.unescape(str(d.get("selftext", ""))).strip()
        score = int(d.get("score", 0) or 0)
        num_comments = int(d.get("num_comments", 0) or 0)
        created_utc = float(d.get("created_utc", 0.0) or 0.0)
        permalink = str(d.get("permalink", f"/r/{subreddit}/comments/{post_id}/"))
        url = str(d.get("url_overridden_by_dest") or d.get("url") or "")
        domain = str(d.get("domain", ""))
        flair = html.unescape(str(d.get("link_flair_text") or "")).strip()
        is_video = bool(d.get("is_video", False))
        is_self = bool(d.get("is_self", False))
        over_18 = bool(d.get("over_18", False))
        spoiler = bool(d.get("spoiler", False))
        oc = bool(d.get("is_original_content", False))
        stickied = bool(d.get("stickied", False))
        upvote_ratio = float(d.get("upvote_ratio", 1.0) or 1.0)
        likes_raw = d.get("likes")
        likes: Optional[bool] = bool(likes_raw) if isinstance(likes_raw, bool) else None

        # Extração de URL de vídeo nativo do Reddit (BUG-012 + CR-06 DASH/HLS)
        video_url = ""
        video_dash_url = ""
        video_hls_url = ""
        preview_width = 0
        preview_height = 0
        media_obj = d.get("secure_media") or d.get("media")
        if isinstance(media_obj, dict):
            rv = media_obj.get("reddit_video")
            if isinstance(rv, dict):
                video_url = html.unescape(
                    str(rv.get("fallback_url") or rv.get("hls_url") or "")
                )
                video_dash_url = html.unescape(str(rv.get("dash_url") or ""))
                video_hls_url = html.unescape(str(rv.get("hls_url") or ""))
                preview_width = int(rv.get("width", 0) or 0)
                preview_height = int(rv.get("height", 0) or 0)
                if video_url:
                    is_video = True
        if is_video and not video_url and url.startswith("http"):
            video_url = url

        thumb = str(d.get("thumbnail") or "")
        if not thumb.startswith("http"):
            thumb = ""

        preview_img = ""
        highres_img = ""
        gallery_imgs: List[str] = []

        # 1. Galeria de múltiplas imagens (is_gallery + media_metadata)
        if d.get("is_gallery") and isinstance(d.get("media_metadata"), dict):
            meta = d["media_metadata"]
            gallery_items = (
                d.get("gallery_data", {}).get("items", [])
                if isinstance(d.get("gallery_data"), dict)
                else []
            )
            media_ids = [
                g.get("media_id")
                for g in gallery_items
                if isinstance(g, dict) and g.get("media_id")
            ] or list(meta.keys())

            for mid in media_ids:
                m_info = meta.get(mid)
                if not isinstance(m_info, dict) or m_info.get("status") != "valid":
                    continue
                s_info = m_info.get("s")
                if isinstance(s_info, dict):
                    u = html.unescape(str(s_info.get("u") or s_info.get("gif") or ""))
                    if u.startswith("http"):
                        gallery_imgs.append(u)
                    if not preview_width:
                        preview_width = int(s_info.get("x", 0) or 0)
                        preview_height = int(s_info.get("y", 0) or 0)
                if not preview_img:
                    previews = m_info.get("p")
                    if isinstance(previews, list) and previews:
                        chosen = previews[min(len(previews) - 1, 3)]
                        if isinstance(chosen, dict):
                            pu = html.unescape(str(chosen.get("u", "")))
                            if pu.startswith("http"):
                                preview_img = pu
            if gallery_imgs:
                highres_img = gallery_imgs[0]
                if not preview_img:
                    preview_img = gallery_imgs[0]

        # 2. Preview padrão do Reddit
        if not preview_img and isinstance(d.get("preview"), dict):
            images = d["preview"].get("images")
            if isinstance(images, list) and images:
                first_img = images[0]
                if isinstance(first_img, dict):
                    source_obj = first_img.get("source")
                    if isinstance(source_obj, dict):
                        source_url = html.unescape(str(source_obj.get("url", "")))
                        if source_url.startswith("http"):
                            highres_img = source_url
                        if not preview_width:
                            preview_width = int(source_obj.get("width", 0) or 0)
                            preview_height = int(source_obj.get("height", 0) or 0)
                    resolutions = first_img.get("resolutions")
                    if isinstance(resolutions, list) and resolutions:
                        for res in resolutions:
                            if isinstance(res, dict) and int(res.get("width", 0) or 0) >= 640:
                                preview_img = html.unescape(str(res.get("url", "")))
                                break
                        if not preview_img and isinstance(resolutions[-1], dict):
                            preview_img = html.unescape(
                                str(resolutions[-1].get("url", ""))
                            )
                    if not preview_img:
                        preview_img = highres_img

        # 3. URL direta de imagem
        if not preview_img and any(
            url.lower().endswith(ext)
            for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
        ):
            preview_img = url
            highres_img = url

        if not highres_img and preview_img:
            highres_img = preview_img

        return PostItem(
            id=post_id,
            subreddit=subreddit,
            subreddit_prefixed=sub_prefixed,
            author=author,
            title=title,
            selftext=selftext,
            score=score,
            num_comments=num_comments,
            created_utc=created_utc,
            permalink=permalink,
            url=url,
            domain=domain,
            flair=flair,
            is_video=is_video,
            video_url=video_url,
            video_dash_url=video_dash_url,
            video_hls_url=video_hls_url,
            is_self=is_self,
            over_18=over_18,
            spoiler=spoiler,
            oc=oc,
            stickied=stickied,
            upvote_ratio=upvote_ratio,
            likes=likes,
            thumbnail_url=thumb,
            preview_image_url=preview_img,
            highres_image_url=highres_img,
            preview_width=preview_width,
            preview_height=preview_height,
            gallery_images=gallery_imgs,
        )

    def fetch_subreddit_info(self, subreddit: str) -> SubredditInfo:
        """Busca metadados sobre um subreddit (título, descrição, membros, ícone)."""
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        key = clean_sub.lower()
        if key in ("popular", "all", "home"):
            if key == "home":
                return SubredditInfo(
                    name="home",
                    display_name_prefixed="Início",
                    title="Seu Feed Personalizado",
                    public_description="Postagens das comunidades em que você está inscrito.",
                )
            if key == "popular":
                return SubredditInfo(
                    name="popular",
                    display_name_prefixed="r/popular",
                    title="Posts Populares no Reddit",
                    public_description="As postagens mais ativas, votadas e comentadas de todo o Reddit agora.",
                )
            return SubredditInfo(
                name="all",
                display_name_prefixed="r/all",
                title="Tudo no Reddit",
                public_description="Feed global agregando postagens de todas as comunidades do Reddit.",
            )

        if key in self._sub_cache:
            return self._sub_cache[key]

        try:
            encoded_sub = quote(clean_sub, safe="")
            resp = self._oauth_get(f"/r/{encoded_sub}/about")
            if resp.status_code == 200:
                d = resp.json().get("data", {})
                icon = html.unescape(
                    str(d.get("community_icon") or d.get("icon_img") or "")
                )
                banner = html.unescape(
                    str(d.get("banner_background_image") or d.get("banner_img") or "")
                )
                info = SubredditInfo(
                    name=str(d.get("display_name", clean_sub)),
                    display_name_prefixed=str(
                        d.get("display_name_prefixed", f"r/{clean_sub}")
                    ),
                    title=html.unescape(str(d.get("title") or f"r/{clean_sub}")),
                    public_description=html.unescape(
                        str(d.get("public_description") or "")
                    ).strip(),
                    subscribers=int(d.get("subscribers", 0) or 0),
                    active_users=int(
                        d.get("accounts_active") or d.get("active_user_count") or 0
                    ),
                    icon_url=icon if icon.startswith("http") else "",
                    banner_url=banner if banner.startswith("http") else "",
                    primary_color=str(d.get("primary_color") or "#FF4500") or "#FF4500",
                )
                self._sub_cache[key] = info
                return info
        except Exception:
            pass

        return SubredditInfo(
            name=clean_sub,
            display_name_prefixed=f"r/{clean_sub}",
            title=f"Comunidade r/{clean_sub}",
            public_description="",
        )

    def search_subreddits(self, query: str, limit: int = 8) -> List[SubredditInfo]:
        """Busca comunidades pelo nome ou tema."""
        if not query.strip():
            return []
        try:
            resp = self._oauth_get(
                "/subreddits/search", params={"q": query.strip(), "limit": limit}
            )
            if resp.status_code == 200:
                children = resp.json().get("data", {}).get("children", [])
                results: List[SubredditInfo] = []
                for c in children:
                    d = c.get("data", {}) if isinstance(c, dict) else {}
                    name = str(d.get("display_name", ""))
                    if not name:
                        continue
                    icon = html.unescape(
                        str(d.get("community_icon") or d.get("icon_img") or "")
                    )
                    info = SubredditInfo(
                        name=name,
                        display_name_prefixed=str(
                            d.get("display_name_prefixed", f"r/{name}")
                        ),
                        title=html.unescape(str(d.get("title") or name)),
                        public_description=html.unescape(
                            str(d.get("public_description") or "")
                        ).strip(),
                        subscribers=int(d.get("subscribers", 0) or 0),
                        icon_url=icon if icon.startswith("http") else "",
                    )
                    results.append(info)
                return results
        except Exception:
            pass
        return []

    def search_posts(
        self,
        query: str,
        subreddit: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 25,
    ) -> Tuple[List[PostItem], Optional[str]]:
        """
        Busca postagens via feed Atom oficial (search.rss) com suporte a paginação
        via cursor `after` (BUG-016) e validação explícita de erros XML (BUG-014).
        """
        if not query.strip():
            return [], None
        self._ensure_session()
        params: Dict[str, Any] = {
            "q": query.strip(),
            "limit": limit,
            "sort": "relevance",
        }
        if after:
            params["after"] = after

        if subreddit and subreddit.lower() not in ("popular", "all"):
            clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
            encoded_sub = quote(clean_sub, safe="")
            url = f"https://www.reddit.com/r/{encoded_sub}/search.rss"
            params["restrict_sr"] = "on"
        else:
            url = "https://www.reddit.com/search.rss"

        resp = self._session.get(url, params=params, timeout=12)
        if resp.status_code == 429:
            time.sleep(0.8)
            self._ensure_session(force=True)
            resp = self._session.get(url, params=params, timeout=12)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Falha ao pesquisar no Reddit (HTTP {resp.status_code})"
            )

        return self._parse_atom_posts(resp.text)

    def _parse_atom_posts(self, xml_text: str) -> Tuple[List[PostItem], Optional[str]]:
        posts: List[PostItem] = []
        last_fullname: Optional[str] = None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise RuntimeError(
                f"Resposta inválida recebida do servidor de busca: {exc}"
            ) from exc

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "media": "http://search.yahoo.com/mrss/",
        }
        for entry in root.findall("atom:entry", ns):
            raw_id = entry.findtext("atom:id", default="", namespaces=ns).strip()
            # Ignora entradas de comunidades (t5_...) retornadas no search.rss
            if raw_id.startswith("t5_"):
                continue

            post_id = raw_id.replace("t3_", "").strip() or str(len(posts))
            last_fullname = f"t3_{post_id}"

            title = html.unescape(
                entry.findtext("atom:title", default="", namespaces=ns)
            )
            author_raw = entry.findtext(
                "atom:author/atom:name", default="/u/reddit", namespaces=ns
            )
            author = author_raw.replace("/u/", "").strip()
            cat_el = entry.find("atom:category", ns)
            sub = (
                cat_el.attrib.get("term", "reddit")
                if cat_el is not None
                else "reddit"
            )
            link_el = entry.find("atom:link", ns)
            href = link_el.attrib.get("href", "") if link_el is not None else ""
            permalink = (
                href.split("reddit.com")[-1]
                if "reddit.com" in href
                else f"/r/{sub}/comments/{post_id}/"
            )
            if not permalink.startswith("/"):
                permalink = "/" + permalink

            updated_str = entry.findtext("atom:updated", default="", namespaces=ns)
            created_utc = parse_iso_timestamp(updated_str)

            content_html = entry.findtext("atom:content", default="", namespaces=ns)
            selftext = strip_html_to_text(content_html)

            thumb_el = entry.find("media:thumbnail", ns)
            thumb_url = (
                thumb_el.attrib.get("url", "") if thumb_el is not None else ""
            )
            img_match = re.search(
                r'href="(https://i\.redd\.it/[^"]+|https://preview\.redd\.it/[^"]+)"',
                content_html,
            )
            preview_img = (
                html.unescape(img_match.group(1)) if img_match else thumb_url
            )

            posts.append(
                PostItem(
                    id=post_id,
                    subreddit=sub,
                    subreddit_prefixed=f"r/{sub}",
                    author=author,
                    title=title,
                    selftext=selftext,
                    score=1,
                    num_comments=0,
                    created_utc=created_utc,
                    permalink=permalink,
                    url=href,
                    thumbnail_url=thumb_url,
                    preview_image_url=preview_img,
                    highres_image_url=preview_img,
                )
            )

        next_cursor = last_fullname if posts else None
        return posts, next_cursor

    def fetch_community_rules(self, subreddit: str) -> List[Dict[str, str]]:
        """Busca as regras de uma comunidade via /r/{sub}/about/rules."""
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        if not clean_sub or clean_sub.lower() in ("popular", "all", "home"):
            return []
        try:
            encoded_sub = quote(clean_sub, safe="")
            resp = self._oauth_get(f"/r/{encoded_sub}/about/rules")
            if resp.status_code == 200:
                rules_raw = resp.json().get("rules", [])
                result: List[Dict[str, str]] = []
                for idx, r in enumerate(rules_raw, start=1):
                    if not isinstance(r, dict):
                        continue
                    short_name = html.unescape(str(r.get("short_name") or f"Regra {idx}")).strip()
                    desc = html.unescape(str(r.get("description") or "")).strip()
                    result.append({"title": f"{idx}. {short_name}", "description": desc})
                return result
        except Exception:
            pass
        return []

    def _flatten_comment_tree(
        self,
        children: List[Any],
        post_author: str,
        out: List[CommentItem],
        depth: int = 0,
    ) -> List[str]:
        """Percorre recursivamente a árvore JSON de comentários em pré-ordem."""
        direct_ids: List[str] = []
        for child in children:
            if not isinstance(child, dict) or child.get("kind") != "t1":
                continue
            d = child.get("data") or {}
            c_id = str(d.get("id") or "")
            body = html.unescape(str(d.get("body") or "")).strip()
            if not c_id or not body:
                continue
            author = str(d.get("author") or "deleted")
            score = int(d.get("score", 1) or 1)
            created_utc = float(d.get("created_utc", 0.0) or 0.0)
            parent_id = str(d.get("parent_id") or "")
            distinguished = str(d.get("distinguished") or "")
            flair = html.unescape(str(d.get("author_flair_text") or "")).strip()
            is_sub = bool(d.get("is_submitter")) or (author.lower() == post_author.lower())
            likes_raw = d.get("likes")
            likes: Optional[bool] = bool(likes_raw) if isinstance(likes_raw, bool) else None

            item = CommentItem(
                id=c_id,
                author=author,
                body=body,
                created_utc=created_utc,
                score=score,
                depth=depth,
                is_submitter=is_sub,
                parent_id=parent_id,
                distinguished=distinguished,
                author_flair_text=flair,
                likes=likes,
            )
            out.append(item)
            direct_ids.append(c_id)

            replies = d.get("replies")
            if isinstance(replies, dict):
                rep_children = replies.get("data", {}).get("children", [])
                if isinstance(rep_children, list) and rep_children:
                    sub_ids = self._flatten_comment_tree(
                        rep_children, post_author, out, depth + 1
                    )
                    item.children_ids.extend(sub_ids)
        return direct_ids

    def fetch_post_comments(
        self, post: PostItem, limit: int = 80
    ) -> List[CommentItem]:
        """
        Busca a árvore de comentários de um post via OAuth JSON (com depth, parent_id,
        score, badges e replies aninhados) e fallback para feed Atom (.rss) (BUG-006).
        """
        self._ensure_session()
        permalink = post.permalink or f"/r/{post.subreddit}/comments/{post.id}/"
        if permalink.startswith("http") and "reddit.com" in permalink:
            permalink = permalink.split("reddit.com", 1)[-1]
        if not permalink.startswith("/"):
            permalink = "/" + permalink
        permalink = permalink.rstrip("/")

        # 1. Tentativa via OAuth JSON para preservar hierarquia completa (CR-05)
        try:
            oauth_resp = self._oauth_get(
                permalink, params={"limit": limit, "depth": 10, "sort": "confidence"}
            )
            if oauth_resp.status_code == 200:
                payload = oauth_resp.json()
                if isinstance(payload, list) and len(payload) >= 2:
                    # Atualiza selftext do post se vazio
                    post_children = payload[0].get("data", {}).get("children", [])
                    if post_children and isinstance(post_children[0], dict):
                        pd = post_children[0].get("data") or {}
                        if not post.selftext and pd.get("selftext"):
                            post.selftext = html.unescape(str(pd.get("selftext", ""))).strip()
                    comment_children = payload[1].get("data", {}).get("children", [])
                    flat_comments: List[CommentItem] = []
                    self._flatten_comment_tree(
                        comment_children, post.author, flat_comments, depth=0
                    )
                    if flat_comments:
                        return flat_comments
        except Exception:
            pass

        # 2. Fallback via feed Atom (.rss) com retry e backoff exponencial
        url = f"https://www.reddit.com{permalink}/.rss"

        resp: Optional[requests.Response] = None
        for attempt in range(3):
            resp = self._session.get(url, params={"limit": limit}, timeout=12)
            if resp.status_code == 200:
                break
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.8 * (2 ** attempt))
                self._ensure_session(force=True)
            else:
                break

        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "sem resposta"
            raise RuntimeError(
                f"Não foi possível carregar comentários (HTTP {code})"
            )

        comments: List[CommentItem] = []
        try:
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)

            for idx, entry in enumerate(entries):
                entry_id = entry.findtext("atom:id", default="", namespaces=ns)
                if idx == 0 and entry_id.startswith("t3_"):
                    if not post.selftext:
                        c_html = entry.findtext(
                            "atom:content", default="", namespaces=ns
                        )
                        extracted = strip_html_to_text(c_html)
                        if extracted:
                            post.selftext = extracted
                    continue

                author_raw = entry.findtext(
                    "atom:author/atom:name", default="/u/deleted", namespaces=ns
                )
                author = author_raw.replace("/u/", "").strip()
                updated_str = entry.findtext(
                    "atom:updated", default="", namespaces=ns
                )
                created_utc = parse_iso_timestamp(updated_str)
                content_html = entry.findtext(
                    "atom:content", default="", namespaces=ns
                )
                body = strip_html_to_text(content_html)
                if not body:
                    continue

                comments.append(
                    CommentItem(
                        id=entry_id.replace("t1_", "") or str(idx),
                        author=author,
                        body=body,
                        created_utc=created_utc,
                        is_submitter=(author.lower() == post.author.lower()),
                        parent_id=f"t3_{post.id}",
                    )
                )
        except Exception as exc:
            raise RuntimeError(f"Erro ao processar comentários: {exc}") from exc

        return comments

    def download_image(self, url: str) -> Optional[bytes]:
        """Baixa bytes de uma imagem com cache LRU limitado em memória."""
        if not url or not url.startswith("http"):
            return None
        with self._lock:
            cached = self._image_cache.get(url)
            if cached is not None:
                self._image_cache.move_to_end(url)
                return cached

        try:
            resp = self._session.get(url, timeout=10)
            if resp.status_code == 200 and resp.content:
                with self._lock:
                    self._image_cache[url] = resp.content
                    while len(self._image_cache) > 250:
                        self._image_cache.popitem(last=False)
                return resp.content
        except Exception:
            pass
        return None
