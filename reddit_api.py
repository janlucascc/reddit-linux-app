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
import xml.etree.ElementTree as ET
import requests


USER_AGENT = "linux:br.janlucas.redditdesktop:v1.1.0 (by /u/janlucas)"
SUBREDDIT_NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")


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
    is_self: bool = False
    over_18: bool = False
    stickied: bool = False
    upvote_ratio: float = 1.0
    thumbnail_url: str = ""
    preview_image_url: str = ""
    highres_image_url: str = ""
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
        self._lock = threading.Lock()
        self._sub_cache: Dict[str, SubredditInfo] = {}
        self._image_cache: OrderedDict[str, bytes] = OrderedDict()

    def _ensure_session(self, force: bool = False) -> Optional[str]:
        with self._lock:
            now = time.time()
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
        stickied = bool(d.get("stickied", False))
        upvote_ratio = float(d.get("upvote_ratio", 1.0) or 1.0)

        # Extração de URL de vídeo nativo do Reddit (BUG-012)
        video_url = ""
        media_obj = d.get("secure_media") or d.get("media")
        if isinstance(media_obj, dict):
            rv = media_obj.get("reddit_video")
            if isinstance(rv, dict):
                video_url = html.unescape(
                    str(rv.get("fallback_url") or rv.get("hls_url") or "")
                )
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
            is_self=is_self,
            over_18=over_18,
            stickied=stickied,
            upvote_ratio=upvote_ratio,
            thumbnail_url=thumb,
            preview_image_url=preview_img,
            highres_image_url=highres_img,
            gallery_images=gallery_imgs,
        )

    def fetch_subreddit_info(self, subreddit: str) -> SubredditInfo:
        """Busca metadados sobre um subreddit (título, descrição, membros, ícone)."""
        clean_sub = subreddit.strip().lstrip("/").removeprefix("r/").strip("/")
        key = clean_sub.lower()
        if key in ("popular", "all"):
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

    def fetch_post_comments(
        self, post: PostItem, limit: int = 60
    ) -> List[CommentItem]:
        """
        Busca os comentários de um post via feed Atom oficial (.rss)
        com retry e backoff exponencial contra HTTP 429 (BUG-006).
        """
        self._ensure_session()
        permalink = post.permalink or f"/r/{post.subreddit}/comments/{post.id}/"
        if permalink.startswith("http") and "reddit.com" in permalink:
            permalink = permalink.split("reddit.com", 1)[-1]
        if not permalink.startswith("/"):
            permalink = "/" + permalink
        permalink = permalink.rstrip("/")
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
