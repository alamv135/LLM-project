"""Internet access for the model: web search and reading a web page.

Search backends
  * DuckDuckGo (default, free, no account). It reads DuckDuckGo's plain HTML results page, so it
    can stop working if DuckDuckGo changes that page or rate-limits you.
  * Ollama web search (https://ollama.com/settings/keys, free account, needs an API key). More
    reliable; used automatically when a key is set.

Safety: pages are fetched only from public internet addresses. A page cannot make the model read
something on your Mac or your home network (localhost, 192.168.x.x, ...).
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
DDG_HTML_URL = os.environ.get("LOCALCHAT_DDG_URL", "https://html.duckduckgo.com/html/")
DDG_LITE_URL = os.environ.get("LOCALCHAT_DDG_LITE_URL", "https://lite.duckduckgo.com/lite/")
OLLAMA_SEARCH_URL = "https://ollama.com/api/web_search"
OLLAMA_FETCH_URL = "https://ollama.com/api/web_fetch"
# Tests (and only tests) may allow fetching from this computer.
ALLOW_PRIVATE = os.environ.get("LOCALCHAT_ALLOW_PRIVATE_FETCH") == "1"
MAX_DOWNLOAD = 5_000_000


class WebError(Exception):
    pass


# ----- HTTP with safety checks -----------------------------------------------------------------
def _ssl_context() -> ssl.SSLContext:
    # Python from python.org on a Mac has no certificates until "Install Certificates.command"
    # is run; certifi (installed by start.sh) avoids that problem.
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def check_public_url(url: str) -> None:
    """Refuse URLs that point at this computer or a private network."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise WebError("Only http and https addresses can be read")
    host = parts.hostname
    if not host:
        raise WebError("That address has no host name")
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise WebError("Can't find the website %s" % host) from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
                or addr.is_multicast or addr.is_unspecified):
            raise WebError("Blocked: %s is a local or private address" % host)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 (stdlib name)
        check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_request(url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
                 timeout: float = 20.0, check: bool = True) -> Tuple[str, str, bytes]:
    """Returns (final_url, content_type, body). Reads at most MAX_DOWNLOAD bytes."""
    if check:
        check_public_url(url)
    hdrs = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    opener = urllib.request.build_opener(_SafeRedirect(), urllib.request.HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_DOWNLOAD + 1)[:MAX_DOWNLOAD]
            return resp.geturl(), resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        raise WebError("The site answered HTTP %d (%s)" % (exc.code, exc.reason)) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, WebError):
            raise reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise WebError("Certificate error. On a Mac run start.sh once (it installs certifi).") from exc
        raise WebError("Could not connect: %s" % reason) from exc
    except (OSError, ValueError) as exc:
        raise WebError("Could not connect: %s" % exc) from exc


def _decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4000], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    else:
        charset = m.group(1)
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


# ----- search ----------------------------------------------------------------------------------
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _ddg_target(href: str) -> str:
    """DuckDuckGo links go through //duckduckgo.com/l/?uddg=<real url>."""
    href = html.unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    parts = urllib.parse.urlsplit(href)
    if parts.netloc.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        real = urllib.parse.parse_qs(parts.query).get("uddg")
        if real:
            return real[0]
    return href


class _DDGParser(HTMLParser):
    """Reads results from html.duckduckgo.com (class result__a / result__snippet)
    and lite.duckduckgo.com (class result-link / result-snippet)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._field: Optional[str] = None
        self._tag: Optional[str] = None
        self._depth = 0
        self._buf: List[str] = []

    def handle_starttag(self, tag, attrs):
        if self._field:
            if tag == self._tag:
                self._depth += 1
            return
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "a" and ("result__a" in cls or "result-link" in cls):
            href = _ddg_target(a.get("href", ""))
            if "duckduckgo.com/y.js" in href or not href.startswith("http"):
                return  # an advert
            self.results.append({"title": "", "url": href, "snippet": ""})
            self._start("title", tag)
        elif ("result__snippet" in cls or "result-snippet" in cls) and self.results:
            self._start("snippet", tag)

    def _start(self, field: str, tag: str) -> None:
        self._field, self._tag, self._depth, self._buf = field, tag, 1, []

    def handle_endtag(self, tag):
        if self._field and tag == self._tag:
            self._depth -= 1
            if self._depth == 0:
                if self.results and not self.results[-1][self._field]:
                    self.results[-1][self._field] = _clean("".join(self._buf))
                self._field = None

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)


def parse_duckduckgo(page: str) -> List[Dict[str, str]]:
    parser = _DDGParser()
    parser.feed(page)
    seen, out = set(), []
    for r in parser.results:
        if r["url"] in seen or not r["title"]:
            continue
        seen.add(r["url"])
        out.append(r)
    return out


def search_duckduckgo(query: str, max_results: int = 6) -> List[Dict[str, str]]:
    errors = []
    for url in (DDG_HTML_URL, DDG_LITE_URL):
        try:
            data = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
            _, ctype, body = http_request(url, data=data, check=False,
                                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                                   "Referer": url})
            results = parse_duckduckgo(_decode(body, ctype))
            if results:
                return results[:max_results]
            page = body[:20000].decode("utf-8", "ignore").lower()
            if "anomaly" in page or "captcha" in page or "challenge" in page:
                errors.append("DuckDuckGo asked for a captcha (too many searches)")
            else:
                errors.append("no results")
        except WebError as exc:
            errors.append(str(exc))
    if errors and all(e == "no results" for e in errors):
        return []
    raise WebError("DuckDuckGo search failed: %s. For reliable search add a free Ollama API key "
                   "in Settings." % "; ".join(errors))


def search_ollama(query: str, api_key: str, max_results: int = 6) -> List[Dict[str, str]]:
    body = json.dumps({"query": query, "max_results": max(1, min(10, max_results))}).encode()
    _, _, raw = http_request(OLLAMA_SEARCH_URL, data=body, check=False, headers={
        "Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    try:
        items = json.loads(raw.decode("utf-8")).get("results") or []
    except ValueError as exc:
        raise WebError("Ollama web search sent an answer that is not JSON") from exc
    return [{"title": _clean(i.get("title", "")) or i.get("url", ""), "url": i.get("url", ""),
             "snippet": _clean(i.get("content", ""))[:600]} for i in items if i.get("url")]


def web_search(query: str, backend: str, api_key: str, max_results: int = 6) -> Tuple[List[Dict[str, str]], str]:
    """Returns (results, backend used). Each result: {title, url, snippet}."""
    if backend == "ollama" or (backend == "auto" and api_key):
        if not api_key:
            raise WebError("Ollama web search needs an API key (Settings → Web search)")
        try:
            return search_ollama(query, api_key, max_results), "ollama"
        except WebError:
            if backend == "ollama":
                raise
    return search_duckduckgo(query, max_results), "duckduckgo"


# ----- reading pages ---------------------------------------------------------------------------
_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "aside",
         "form", "button", "select", "canvas", "dialog", "head"}
_BLOCK = {"p", "div", "section", "article", "main", "br", "li", "tr", "table", "ul", "ol", "dl", "dd",
          "dt", "blockquote", "pre", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr"}
# class/id names that mark page furniture (only when the name STARTS with the word, so a wrapper
# like class="has-sidebar" that holds the article is kept)
_NOISE = re.compile(r"(^|\s)(cookie|consent|banner|sidebar|menu|navbar|breadcrumb|share|social|"
                    r"related|advert|promo|newsletter|subscribe|popup|modal|comments?)([\s_-]|$)", re.I)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip: List[Tuple[str, int]] = []     # (tag, depth) of the element being skipped
        self._main_depth = 0
        self.all: List[str] = []
        self.main: List[str] = []

    def _emit(self, s: str) -> None:
        self.all.append(s)
        if self._main_depth:
            self.main.append(s)

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.title:
            self._in_title = True
        if tag == "meta":
            a = dict(attrs)
            if a.get("property") == "og:title" and not self.title:
                self.title = _clean(a.get("content", ""))
        if self._skip:
            if tag == self._skip[-1][0] and tag not in _VOID:
                self._skip[-1] = (tag, self._skip[-1][1] + 1)
            return
        a = dict(attrs)
        marker = " ".join([a.get("class") or "", a.get("id") or ""])
        if tag in _SKIP or a.get("aria-hidden") == "true" or a.get("role") in ("navigation", "banner") \
                or (tag in ("div", "section", "ul", "header") and _NOISE.search(marker)):
            if tag not in _VOID:
                self._skip.append((tag, 1))
            return
        if tag in ("main", "article"):
            self._main_depth += 1
        if tag in _BLOCK:
            self._emit("\n")
        if tag in ("h1", "h2", "h3"):
            self._emit("## ")
        elif tag == "li":
            self._emit("- ")
        elif tag in ("td", "th"):
            self._emit(" | ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self._skip:
            if tag == self._skip[-1][0]:
                depth = self._skip[-1][1] - 1
                if depth == 0:
                    self._skip.pop()
                else:
                    self._skip[-1] = (tag, depth)
            return
        if tag in ("main", "article") and self._main_depth:
            self._main_depth -= 1
        if tag in _BLOCK:
            self._emit("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self._skip:
            self._emit(data)


def _tidy(parts: List[str]) -> str:
    text = "".join(parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    out: List[str] = []
    for line in lines:
        if line in ("-", "|", "##", "## "):
            continue
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(page: str) -> Tuple[str, str]:
    """Returns (title, readable text) of an HTML page."""
    parser = _TextExtractor()
    try:
        parser.feed(page)
        parser.close()
    except Exception:  # noqa: BLE001 - broken HTML: keep what was read so far
        pass
    main = _tidy(parser.main)
    text = main if len(main) >= 400 else _tidy(parser.all)
    return _clean(parser.title), text


def fetch_page(url: str, api_key: str = "") -> Dict[str, str]:
    """Download a page and return {url, title, text}. Handles HTML, plain text, JSON and PDF."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        final_url, ctype, body = http_request(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5"})
    except WebError as exc:
        if api_key and "Blocked" not in str(exc):
            return _fetch_ollama(url, api_key)
        raise
    ctype_low = ctype.lower()
    if "pdf" in ctype_low or body[:5] == b"%PDF-":
        from .files import pdf_to_text

        text, _ = pdf_to_text(body)
        return {"url": final_url, "title": final_url.rsplit("/", 1)[-1], "text": text}
    if "html" in ctype_low or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        title, text = html_to_text(_decode(body, ctype))
        if len(text) < 200 and api_key:      # e.g. a page built by JavaScript
            try:
                return _fetch_ollama(url, api_key)
            except WebError:
                pass
        return {"url": final_url, "title": title or final_url, "text": text}
    if ctype_low.startswith("text/") or "json" in ctype_low or "xml" in ctype_low:
        return {"url": final_url, "title": final_url, "text": _decode(body, ctype)}
    raise WebError("That address is not a web page or PDF (%s)" % (ctype or "unknown type"))


def _fetch_ollama(url: str, api_key: str) -> Dict[str, str]:
    _, _, raw = http_request(OLLAMA_FETCH_URL, data=json.dumps({"url": url}).encode(), check=False,
                             headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise WebError("Ollama web fetch sent an answer that is not JSON") from exc
    return {"url": url, "title": data.get("title") or url, "text": data.get("content") or ""}
