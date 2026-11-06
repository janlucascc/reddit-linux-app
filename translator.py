"""
Serviço de tradução otimizado com cache LRU limitado em memória e suporte a lote (batch)
usando requisições HTTP POST na API gratuita do Google Translate (gtx).
"""

from __future__ import annotations
from collections import OrderedDict
import re
import threading
from typing import List, Optional, Tuple
import requests


MAX_CACHE_ENTRIES = 4000

SUPPORTED_LANGUAGES: List[Tuple[str, str]] = [
    ("pt", "Português (BR)"),
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("ja", "日本語"),
]


class TranslationService:
    """Cliente de tradução rápido com sessão HTTP persistente e cache LRU limitado."""

    def __init__(self, target_lang: str = "pt") -> None:
        self.target_lang = target_lang
        self._cache: OrderedDict[Tuple[str, str], str] = OrderedDict()
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
        })

    def _get_cache_locked(self, key: Tuple[str, str]) -> Optional[str]:
        val = self._cache.get(key)
        if val is not None:
            self._cache.move_to_end(key)
        return val

    def _put_cache_locked(self, key: Tuple[str, str], value: str) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def get_cached(self, text: str, target_lang: Optional[str] = None) -> Optional[str]:
        if not text or not text.strip():
            return text
        lang = target_lang or self.target_lang
        with self._lock:
            return self._get_cache_locked((lang, text.strip()))

    def translate(self, text: str, target_lang: Optional[str] = None) -> str:
        """Traduz um texto individual via HTTP POST (evitando erro HTTP 414) com cache LRU."""
        if not text or not text.strip():
            return text
        lang = target_lang or self.target_lang
        clean_text = text.strip()

        with self._lock:
            cached = self._get_cache_locked((lang, clean_text))
            if cached is not None:
                return cached

        # Usa POST com payload no corpo para suportar textos longos sem estourar a URL (BUG-004)
        query_text = clean_text[:8000]
        try:
            resp = self._session.post(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": "auto",
                    "tl": lang,
                    "dt": "t",
                },
                data={"q": query_text},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and data[0]:
                    translated = "".join(
                        part[0] for part in data[0] if part and part[0]
                    ).strip()
                    if translated:
                        with self._lock:
                            self._put_cache_locked((lang, clean_text), translated)
                        return translated
        except Exception:
            pass

        return text

    def translate_batch(
        self, texts: List[str], target_lang: Optional[str] = None
    ) -> List[str]:
        """
        Traduz vários textos curtos (ex: títulos de posts ou comentários)
        agrupando-os com um separador estruturado e aplicando limite de cache (BUG-011).
        """
        if not texts:
            return []
        lang = target_lang or self.target_lang
        results: List[Optional[str]] = [None] * len(texts)
        missing_indices: List[int] = []
        missing_texts: List[str] = []

        with self._lock:
            for idx, t in enumerate(texts):
                if not t or not t.strip():
                    results[idx] = t
                else:
                    cached = self._get_cache_locked((lang, t.strip()))
                    if cached is not None:
                        results[idx] = cached
                    else:
                        missing_indices.append(idx)
                        missing_texts.append(t.strip())

        if not missing_texts:
            return [r if r is not None else "" for r in results]

        separator = "\n [[__SEP__]] \n"
        split_pattern = re.compile(r"\[\[\s*__SEP__\s*\]\]", re.IGNORECASE)
        chunk_indices: List[int] = []
        chunk_strs: List[str] = []
        current_len = 0

        def flush_chunk(c_idx: List[int], c_str: List[str]) -> None:
            if not c_idx:
                return
            if len(c_idx) == 1:
                results[c_idx[0]] = self.translate(c_str[0], lang)
                return
            joined = separator.join(c_str)
            tr_joined = self.translate(joined, lang)
            parts = [p.strip() for p in split_pattern.split(tr_joined)]
            if len(parts) == len(c_idx):
                with self._lock:
                    for i, orig, tr_part in zip(c_idx, c_str, parts):
                        self._put_cache_locked((lang, orig), tr_part)
                        results[i] = tr_part
            else:
                for i, orig in zip(c_idx, c_str):
                    results[i] = self.translate(orig, lang)

        for idx, txt in zip(missing_indices, missing_texts):
            if current_len + len(txt) > 3800 and chunk_indices:
                flush_chunk(chunk_indices, chunk_strs)
                chunk_indices = []
                chunk_strs = []
                current_len = 0
            chunk_indices.append(idx)
            chunk_strs.append(txt)
            current_len += len(txt) + len(separator)

        if chunk_indices:
            flush_chunk(chunk_indices, chunk_strs)

        return [
            results[i] if results[i] is not None else texts[i]
            for i in range(len(texts))
        ]
