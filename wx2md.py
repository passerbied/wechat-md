#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wx2md.py —— 微信公众号文章 -> 本地 Markdown 归档（仅 Python 标准库）

用法:
    python3 wx2md.py "https://mp.weixin.qq.com/s/ARTICLE_TOKEN"
    python3 wx2md.py -f urls.txt -o ./wechat-md
    python3 wx2md.py --discover "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" >> urls.txt

不依赖 requests / lxml / 无头浏览器。正文抓取不使用 Cookie。
遇验证码或"环境异常"页面时如实报错，不做绕过。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import struct
import tempfile
import uuid
from pathlib import Path
from threading import Event
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from html import unescape

__version__ = "1.1.0rc1"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REFERER = "https://mp.weixin.qq.com/"

VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "source", "area", "base", "col", "embed", "track", "wbr"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside", "figure", "figcaption",
    "ul", "ol", "table", "thead", "tbody", "tr", "td", "th", "dl", "dt", "dd", "center", "fieldset", "form",
}
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# 正文里常见的公众号 UI 噪音行，整行匹配即丢弃
NOISE_PATTERNS = [
    r"^预览时标签不可点$",
    r"^继续滑动看下一个$",
    r"^轻触阅读原文$",
    r"^向上滑动看下一个$",
    r"^微信扫一扫$",
    r"^取消\s*允许$",
    r"^知道了$",
    r"^长按识别二维码关注$",
    r"^点击上方.{0,12}关注.{0,12}$",
    r"^在小说阅读器读本章$",
    r"^去阅读$",
    r"^在公众号小说中沉浸阅读$",
    r"^视频\s*小程序\s*赞\s*分享\s*留言\s*收藏\s*听过$",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS))


class WeChatFetchError(RuntimeError):
    kind = "fetch"


class AccessBlocked(WeChatFetchError):
    kind = "blocked"


class InvalidURL(WeChatFetchError):
    kind = "invalid_url"


IMAGE_HOSTS = {"mmbiz.qpic.cn", "mmbiz.qlogo.cn", "wx.qlogo.cn"}
MAX_HTML = 8 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
SHANGHAI = timezone(timedelta(hours=8))


def validate_url(url, kind="article"):
    """Validate before every request, including redirects. No arbitrary hosts."""
    if not isinstance(url, str) or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        raise InvalidURL("URL 含空白或非法字符")
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.username is not None or parts.password is not None:
            raise ValueError()
        if parts.port not in (None, 80 if parts.scheme == "http" else 443):
            raise ValueError()
        host = parts.hostname
    except ValueError:
        raise InvalidURL("仅接受无账号信息、使用标准端口的 HTTP(S) URL")
    if kind == "article":
        if host != "mp.weixin.qq.com":
            raise InvalidURL("文章地址必须来自 mp.weixin.qq.com")
        if re.search(r"captcha|verify", parts.path, re.I):
            raise AccessBlocked("跳转到验证页面，停止本批请求；原因尚不能确定")
        if parts.path in ("/s", "/s/"):
            query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
            if not all(query.get(k) and len(query[k]) == 1 and query[k][0] for k in ("__biz", "mid")):
                raise InvalidURL("长文章链接需要唯一的 __biz 和 mid 参数")
            if not query["mid"][0].isdigit():
                raise InvalidURL("mid 必须是数字")
            for key in ("idx", "sn"):
                if key in query and (len(query[key]) != 1 or not query[key][0]):
                    raise InvalidURL("文章身份参数重复或为空")
            if "idx" in query and not query["idx"][0].isdigit():
                raise InvalidURL("idx 必须是数字")
        elif not re.fullmatch(r"/s/[A-Za-z0-9_-]{8,}", parts.path):
            raise InvalidURL("不是支持的微信文章链接")
    elif kind == "image":
        if host not in IMAGE_HOSTS:
            raise InvalidURL("图片主机不在可信 CDN 范围，保留远程引用")
    else:
        raise InvalidURL("未知请求类型")
    return urllib.parse.urlunsplit(("https", host, parts.path, parts.query, ""))


def article_key(url):
    parts = urllib.parse.urlsplit(validate_url(url))
    query = urllib.parse.parse_qs(parts.query)
    if parts.path.rstrip("/") == "/s":
        identity = [query["__biz"][0], query["mid"][0], query.get("idx", ["1"])[0]]
    else:
        identity = [parts.path]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()[:20]


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        kind = getattr(req, "wx_kind", "article")
        target = validate_url(newurl, kind)
        redirected = super().redirect_request(req, fp, code, msg, headers, target)
        if redirected is not None:
            redirected.wx_kind = kind
        return redirected


def build_opener(insecure=False):
    handlers = [SafeRedirect()]
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", UA), ("Accept-Encoding", "identity"),
                         ("Accept-Language", "zh-CN,zh;q=0.9"), ("Connection", "close")]
    return opener


def fetch(url, opener, kind="article", timeout=30, retries=3, verbose=False, referer=REFERER):
    url = validate_url(url, kind)
    limit = MAX_HTML if kind == "article" else MAX_IMAGE
    if retries < 1 or retries > 5 or timeout <= 0:
        raise ValueError("请求次数需为 1–5，超时需大于零")
    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Referer": referer, "User-Agent": UA})
            req.wx_kind = kind
            with opener.open(req, timeout=timeout) as resp:
                if hasattr(resp, "geturl"):
                    validate_url(resp.geturl(), kind)
                length = resp.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > limit:
                    raise WeChatFetchError("响应超过体积上限：%d 字节" % limit)
                data = resp.read(limit + 1)
                if len(data) > limit:
                    raise WeChatFetchError("响应超过体积上限：%d 字节" % limit)
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if kind == "article" and ctype and ctype not in ("text/html", "application/xhtml+xml"):
                    raise WeChatFetchError("文章响应不是 HTML：" + ctype)
                return data, ctype
        except WeChatFetchError:
            raise
        except urllib.error.HTTPError as exc:
            code = exc.code
            if exc.fp is not None:
                exc.close()
            if code in (401, 403, 429):
                raise AccessBlocked("HTTP %d：访问受限，停止本批请求" % code)
            if code not in (500, 502, 503, 504):
                raise WeChatFetchError("HTTP %d：请求失败，不重试" % code)
            last_error = "HTTP %d" % code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason).upper():
                raise WeChatFetchError("TLS 证书校验失败；请检查证书链、系统时间和本机信任配置")
            last_error = str(reason)
        if attempt + 1 < retries:
            delay = 2 ** (attempt + 1)
            if verbose:
                print("    暂时请求失败，%d 秒后重试" % delay, file=sys.stderr)
            time.sleep(delay)
    raise WeChatFetchError("请求失败：%s" % last_error)


def http_get(url, opener, referer=REFERER, timeout=30, retries=3, verbose=False):
    return fetch(url, opener, timeout=timeout, retries=retries, verbose=verbose, referer=referer)[0]


def detect_block(html_text):
    try:
        if extract_body_html(html_text).strip():
            return
    except WeChatFetchError:
        pass
    if re.search(r"环境异常|验证码|captcha|访问过于频繁|完成验证", html_text, re.I):
        raise AccessBlocked("检测到验证或访问受限页面，停止本批请求；具体原因无法仅凭响应确定")
    raise WeChatFetchError("未找到有效正文容器 js_content，页面可能失效或结构已改变")


# ----------------------------------------------------------------------------
# 元数据提取
# ----------------------------------------------------------------------------
def meta_first(html_text: str, patterns) -> str:
    for pat in patterns:
        m = re.search(pat, html_text, re.S)
        if m:
            val = unescape(m.group(1)).strip()
            if val:
                return val
    return ""


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.fields = {}
        self.target = None
        self.target_tag = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.meta.setdefault(key, attrs["content"])
        if attrs.get("id") in ("activity-name", "publish_time", "js_name"):
            self.target, self.target_tag = attrs["id"], tag
            self.fields.setdefault(self.target, "")

    def handle_endtag(self, tag):
        if tag == self.target_tag:
            self.target = self.target_tag = None

    def handle_data(self, data):
        if self.target:
            self.fields[self.target] += data


def extract_meta(html_text):
    parser = MetadataParser()
    parser.feed(html_text)
    title = parser.meta.get("og:title") or parser.fields.get("activity-name") or meta_first(html_text, [
        r"var\s+msg_title\s*=\s*[\"'](.*?)[\"']\s*;"
    ])
    author = parser.meta.get("og:article:author") or parser.meta.get("author") or parser.fields.get("js_name") or meta_first(html_text, [
        r"var\s+(?:nickname|js_name)\s*=\s*[\"'](.*?)[\"']\s*;"
    ])
    ct = meta_first(html_text, [r"var\s+(?:ct|create_time)\s*=\s*[\"']?(\d{9,11})"])
    pub_date = ""
    if ct:
        try:
            pub_date = datetime.fromtimestamp(int(ct), SHANGHAI).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            pass
    if not pub_date:
        raw = parser.fields.get("publish_time", "")
        match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
        if match:
            try:
                pub_date = datetime(*map(int, match.groups())).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return dict(title=norm_text(title).strip(), author=norm_text(author).strip(),
                cover=parser.meta.get("og:image", ""), pub_date=pub_date,
                published_timestamp=int(ct) if ct else None)


class BodyExtractor(HTMLParser):
    """Locate the content element using parsed attributes, preserving inner HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tag = None
        self.depth = 0
        self.done = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.tag is None:
            if dict(attrs).get("id") == "js_content":
                self.tag, self.depth = tag, 1
            return
        self.parts.append(self.get_starttag_text())
        if tag == self.tag:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.tag and not self.done:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if not self.tag or self.done:
            return
        if tag == self.tag:
            self.depth -= 1
            if self.depth == 0:
                self.done = True
                return
        self.parts.append("</%s>" % tag)

    def handle_data(self, data):
        if self.tag and not self.done:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data("&%s;" % name)

    def handle_charref(self, name):
        self.handle_data("&#%s;" % name)


def extract_body_html(html_text):
    parser = BodyExtractor()
    parser.feed(html_text)
    parser.close()
    if not parser.done:
        raise WeChatFetchError("未找到正常闭合的正文容器 js_content")
    return "".join(parser.parts)


# ----------------------------------------------------------------------------
# HTML -> Markdown
# ----------------------------------------------------------------------------
class TreeBuilder(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": {k: (v or "") for k, v in attrs}, "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in VOID_TAGS:
            if len(self.stack) >= 128:
                raise WeChatFetchError("HTML 嵌套超过 128 层")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1]["children"].append(
            {"tag": tag, "attrs": {k: (v or "") for k, v in attrs}, "children": []}
        )

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " "))


def collect_text(node) -> str:
    if isinstance(node, str):
        return node
    return "".join(collect_text(c) for c in node["children"])


def md_escape(text):
    return re.sub(r'([\\`*_\[\]<>#!|])', r'\\\1', text)


def reference_url(url):
    url = url.strip()
    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('/'):
        url = urllib.parse.urljoin(REFERER, url)
    if re.search(r'[\x00-\x1f\x7f]', url):
        return ''
    return url if urllib.parse.urlsplit(url).scheme.lower() in ('https', 'http', 'mailto') else ''


def md_url(url):
    return urllib.parse.quote(url, safe='/:?=&%#@+~,;!$*-_.')


def hidden(node):
    attrs = node['attrs']
    style = re.sub(r'\s+', '', attrs.get('style', '').lower())
    return node['tag'] in ('script', 'style', 'noscript', 'template') or 'hidden' in attrs or bool(re.search(r'(?:^|;)display:none(?:!important)?(?:;|$)', style))


def visible_text(node):
    if isinstance(node, str):
        return node
    if hidden(node):
        return ''
    return ' '.join(visible_text(c) for c in node['children'])


class Converter:
    def __init__(self, image_map=None, verbose=False, clean=False):
        self.image_map = image_map or {}
        self.verbose = verbose
        self.clean = clean
        self.images = []
        self.protected = {}
        self.prefix = '\ue000WX' + uuid.uuid4().hex
        self.bold_depth = 0

    def protect(self, content):
        key = '%s_%d\ue001' % (self.prefix, len(self.protected))
        self.protected[key] = content
        return key

    def restore(self, text):
        for key, content in reversed(list(self.protected.items())):
            text = text.replace(key, content)
        return text

    def children(self, node):
        # Merge adjacent bold nodes before adding delimiters, without touching text/code.
        result, run = [], []
        for child in node['children']:
            if isinstance(child, dict) and child['tag'] in ('strong', 'b') and not hidden(child):
                run.extend(child['children'])
                continue
            if run:
                result.append(self.render({'tag': 'strong', 'attrs': {}, 'children': run}))
                run = []
            result.append(self.render(child))
        if run:
            result.append(self.render({'tag': 'strong', 'attrs': {}, 'children': run}))
        merged = ''
        for piece in result:
            if merged.endswith('**') and not merged.endswith('***') and piece.startswith('**') and not piece.startswith('***'):
                merged = merged[:-2] + piece[2:]
            else:
                merged += piece
        return merged

    def render(self, node):
        if isinstance(node, str):
            if self.clean and NOISE_RE.fullmatch(node.strip()):
                return ''
            return md_escape(norm_text(node))
        tag, attrs = node['tag'], node['attrs']
        if hidden(node):
            return ''
        if tag == 'pre':
            code = collect_text(node)
            classes = attrs.get('class', '') + ' ' + ' '.join(
                c['attrs'].get('class', '') for c in node['children'] if isinstance(c, dict))
            lang = re.search(r'language-([\w+#-]+)', classes)
            fence = '`' * max(3, max([len(x) + 1 for x in re.findall(r'`+', code)] or [3]))
            block = fence + (lang.group(1) if lang else '') + '\n' + code
            if not code.endswith('\n'):
                block += '\n'
            return '\n\n' + self.protect(block + fence) + '\n\n'
        if tag == 'code':
            code = collect_text(node).replace('\n', ' ')
            if not code:
                return ''
            fence = '`' * max([len(x) + 1 for x in re.findall(r'`+', code)] or [1])
            pad = ' ' if code.startswith(('`', ' ')) or code.endswith(('`', ' ')) else ''
            return self.protect(fence + pad + code + pad + fence)
        if tag == 'img':
            src = reference_url(attrs.get('data-src') or attrs.get('src') or '')
            if not src or not src.startswith(('http://', 'https://')):
                return '\n\n> [图片未提取：缺少可用 HTTP(S) 地址]\n\n'
            if src not in self.images:
                self.images.append(src)
            alt = md_escape(norm_text(attrs.get('alt') or '图片'))
            return '\n\n![%s](%s)\n\n' % (alt, md_url(self.image_map.get(src, src)))
        if tag == 'br':
            return '  \n'
        if tag == 'hr':
            return '\n\n---\n\n'
        if tag in ('mpvoice', 'audio', 'mpvideo', 'mp-common-videosnap', 'video', 'iframe'):
            name = md_escape(attrs.get('name') or ('音频' if tag in ('mpvoice', 'audio') else '视频/嵌入内容'))
            source = reference_url(attrs.get('data-src') or attrs.get('src') or '')
            identifier = attrs.get('voice_encode_fileid') or attrs.get('vid') or attrs.get('data-vid')
            detail = md_url(source) if source else ('标识：' + md_escape(identifier) if identifier else '未提取到可播放地址')
            return '\n\n> [%s] %s\n\n' % (name, detail)
        if tag in ('strong', 'b'):
            nested = self.bold_depth > 0
            self.bold_depth += 1
            try:
                inner = self.children(node)
            finally:
                self.bold_depth -= 1
            return inner if nested or not inner.strip() else '**' + inner.strip() + '**'
        if tag in ('ul', 'ol'):
            try:
                counter = int(attrs.get('start', '1'))
            except ValueError:
                counter = 1
            lines = []
            for child in node['children']:
                if not isinstance(child, dict) or child['tag'] != 'li' or hidden(child):
                    continue
                if tag == 'ol' and re.fullmatch(r'-?\d+', child['attrs'].get('value', '')):
                    counter = int(child['attrs']['value'])
                prefix = ('%d. ' % counter) if tag == 'ol' else '- '
                content = self.restore(self.children(child)).strip()
                content = content.replace('\n', '\n' + ' ' * len(prefix))
                lines.append(self.protect(prefix + content))
                counter += 1
            return '\n\n' + '\n'.join(lines) + '\n\n'
        if tag == 'table':
            rows = []
            def visit(parent):
                for child in parent['children']:
                    if isinstance(child, dict) and not hidden(child):
                        if child['tag'] == 'tr':
                            rows.append([c for c in child['children'] if isinstance(c, dict) and c['tag'] in ('td', 'th') and not hidden(c)])
                        elif child['tag'] != 'table':
                            visit(child)
            visit(node)
            if not rows or not any(rows):
                return ''
            values = [[self.restore(self.children(c)).strip().replace('|', '\\|').replace('\n', '<br>') for c in row] for row in rows]
            complex_table = any(c['attrs'].get('rowspan', '1') != '1' or c['attrs'].get('colspan', '1') != '1' for row in rows for c in row)
            if complex_table:
                text = '\n\n> 表格含合并单元格，以下按行展开。\n\n' + '\n\n'.join(' / '.join(row) for row in values)
                return self.protect(text) + '\n\n'
            width = max(map(len, values))
            if not any(c['tag'] == 'th' for c in rows[0]):
                values.insert(0, [''] * width)
            values = [row + [''] * (width - len(row)) for row in values]
            values.insert(1, ['---'] * width)
            return '\n\n' + self.protect('\n'.join('| ' + ' | '.join(row) + ' |' for row in values)) + '\n\n'
        inner = self.children(node)
        if tag in ('em', 'i', 'del', 's', 'strike'):
            mark = '*' if tag in ('em', 'i') else '~~'
            return mark + inner.strip() + mark if inner.strip() else ''
        if tag == 'a':
            href = reference_url(attrs.get('href', ''))
            return '[%s](%s)' % (inner.strip(), md_url(href)) if href and inner.strip() else inner
        if tag in HEADING_TAGS:
            return '\n\n' + '#' * HEADING_TAGS[tag] + ' ' + inner.strip() + '\n\n' if inner.strip() else ''
        if tag == 'blockquote':
            # Protect expanded code as well as quote prefixes from global whitespace cleanup.
            content = self.restore(inner).strip()
            return '\n\n' + self.protect('> ' + content.replace('\n', '\n> ')) + '\n\n' if content else ''
        if tag in BLOCK_TAGS or tag == 'li':
            return '\n\n' + inner.strip() + '\n\n' if inner.strip() else ''
        return inner


def html_to_markdown(body_html, image_map=None, verbose=False, clean=False):
    builder = TreeBuilder()
    builder.feed(body_html)
    builder.close()
    conv = Converter(image_map, verbose, clean)
    md = conv.render(builder.root)
    # Protected code/list/table content is restored only after layout cleanup.
    md = re.sub(r'\n{3,}', '\n\n', md).strip()
    return conv.restore(md) + '\n', conv.images


# ----------------------------------------------------------------------------
# 图片本地化
# ----------------------------------------------------------------------------
def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.wx2md-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def image_extension(data, ctype):
    """Basic format validation only; this does not decode the entire image."""
    if len(data) >= 24 and data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR' and all(struct.unpack('>II', data[16:24])):
        detected, ext = 'image/png', '.png'
    elif len(data) >= 4 and data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        detected, ext = 'image/jpeg', '.jpg'
    elif len(data) >= 10 and data[:6] in (b'GIF87a', b'GIF89a') and all(struct.unpack('<HH', data[6:10])):
        detected, ext = 'image/gif', '.gif'
    elif len(data) >= 16 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        detected, ext = 'image/webp', '.webp'
    else:
        raise WeChatFetchError('图片为空、格式不支持或文件特征不合法')
    if ctype == 'image/jpg':
        ctype = 'image/jpeg'
    if ctype not in ('', 'application/octet-stream', detected):
        raise WeChatFetchError('图片响应类型与文件特征不一致：' + ctype)
    return ext


class ImageResults(dict):
    def __init__(self):
        super().__init__()
        self.failures = []
        self.blocked = False


def download_images(urls, images_dir, opener, workers=2, verbose=False, timeout=30, retries=3):
    result = ImageResults()
    stopped = Event()
    urls = list(dict.fromkeys(urls))

    def one(url):
        if stopped.is_set():
            return url, url, '访问受限后停止请求', False
        try:
            validate_url(url, 'image')
            data, ctype = fetch(url, opener, kind='image', timeout=timeout, retries=retries, verbose=verbose)
            ext = image_extension(data, ctype)
            name = hashlib.sha256(data).hexdigest()[:24] + ext
            atomic_write(Path(images_dir) / name, data)
            return url, 'images/' + name, '', False
        except (WeChatFetchError, OSError) as exc:
            blocked = isinstance(exc, AccessBlocked)
            if blocked:
                stopped.set()
            return url, url, str(exc), blocked

    if urls:
        with ThreadPoolExecutor(max_workers=min(4, max(1, workers))) as pool:
            for url, local, error, blocked in pool.map(one, urls):
                result[url] = local
                if error:
                    result.failures.append({'url': url, 'error': error})
                result.blocked = result.blocked or blocked
    if verbose and urls:
        print('    图片：%d / %d 成功' % (len(urls) - len(result.failures), len(urls)), file=sys.stderr)
    return result


# ----------------------------------------------------------------------------
# Atomic archives and recoverable CSV index
# ----------------------------------------------------------------------------
BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
INDEX_FIELDS = ['标题', '公众号', '发布日期', '链接', '本地文件', '字数', '文章ID', '状态', '图片总数', '图片失败数', '内容SHA256']


def safe_name(s, fallback='untitled', limit=80):
    s = re.sub(r'\s+', ' ', BAD_CHARS_RE.sub('_', s or '')).strip(' .')
    s = s[:limit]
    while len(s.encode('utf-8')) > 180:
        s = s[:-1]
    s = s.rstrip(' .') or fallback
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', s, re.I):
        s = '_' + s
    return s


def yaml_escape(s):
    return json.dumps(s or '', ensure_ascii=False)


def index_rows(out_root):
    path = Path(out_root) / '文章清单.csv'
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {'链接', '本地文件'}.issubset(reader.fieldnames):
            raise WeChatFetchError('文章清单格式不受支持，请检查后再运行')
        return list(reader)


def row_key(row):
    try:
        return article_key(row.get('链接', ''))
    except WeChatFetchError:
        return None


def contained_path(root, relative):
    base = Path(root).resolve()
    target = (base / relative).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise WeChatFetchError('归档路径越出输出目录')
    return target


def source_matches(path, url):
    if not path.is_file():
        return False
    try:
        with path.open(encoding='utf-8') as stream:
            if stream.readline().strip() != '---':
                return False
            for _ in range(40):
                line = stream.readline()
                if line.strip() == '---':
                    break
                if line.startswith('source: '):
                    return article_key(json.loads(line[8:])) == article_key(url)
    except (OSError, ValueError, WeChatFetchError):
        pass
    return False


def write_article(out_root, meta, body_md, url, args, opener):
    identifier = article_key(url)
    title = meta['title'] or '无标题'
    dirname = safe_name((meta['pub_date'] or '0000-00-00') + '-' + title, limit=100) + '-' + identifier
    relative = Path(safe_name(meta['author'] or '未知公众号', limit=60)) / 'articles' / dirname / (safe_name(title, limit=100) + '.md')
    md_path = contained_path(out_root, relative)
    # Preserve existing paths on a same-article update, including legacy archives.
    for row in index_rows(out_root):
        if row_key(row) == identifier:
            candidate = contained_path(out_root, row['本地文件'])
            if source_matches(candidate, url):
                md_path = candidate
                break
    if md_path.exists() and not source_matches(md_path, url):
        raise WeChatFetchError('目标文件属于其他来源或无法确认来源，已停止覆盖')
    art_dir = md_path.parent
    images = meta.get('_images')
    if images is None:
        images = re.findall(r'!\[[^\]]*\]\((https?://[^)\s]+)\)', body_md)
    mapping = ImageResults()
    if not args.no_images:
        image_dir = contained_path(out_root, str(art_dir.relative_to(Path(out_root).resolve()) / 'images'))
        mapping = download_images(images, image_dir, opener, workers=args.image_workers,
                                  verbose=args.verbose, timeout=args.timeout, retries=getattr(args, 'retries', 3))
    if '_body_html' in meta:
        md, _ = html_to_markdown(meta['_body_html'], mapping, clean=args.clean)
    else:
        md = body_md
        for original, local in mapping.items():
            md = md.replace('](%s)' % md_url(original), '](%s)' % md_url(local))
    meta['images_total'] = len(images)
    meta['images_failed'] = mapping.failures
    meta['blocked'] = mapping.blocked
    meta['status'] = 'remote' if args.no_images and images else ('partial' if mapping.failures else 'complete')
    meta['article_id'] = identifier
    text = meta.get('_text', body_md)
    meta['word_count'] = len(re.findall(r'[\u4e00-\u9fff]', text)) + len(re.findall(r'[A-Za-z]+', text))
    front = {key: meta.get(key, '') for key in ('title', 'author')}
    front.update(date=meta['pub_date'], source=url, article_id=identifier, status=meta['status'],
                 fetched_at=datetime.now(timezone.utc).isoformat(), published_timestamp=meta.get('published_timestamp'), word_count=meta['word_count'],
                 images_total=len(images), images_failed=len(mapping.failures))
    content = '---\n' + ''.join('%s: %s\n' % (key, json.dumps(value, ensure_ascii=False)) for key, value in front.items())
    content += '---\n\n# ' + md_escape(title) + '\n\n' + md
    atomic_write(md_path, content.encode('utf-8'))
    return str(art_dir), sum(local.startswith('images/') for local in mapping.values()), str(md_path)


def csv_cell(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def append_index(out_root, meta, url, md_path):
    identifier = article_key(url)
    rows = [row for row in index_rows(out_root) if row_key(row) != identifier]
    rows.append(dict(zip(INDEX_FIELDS, [csv_cell(meta['title']), csv_cell(meta['author']), meta['pub_date'], url,
                                      str(Path(md_path).resolve().relative_to(Path(out_root).resolve())), meta.get('word_count', ''), identifier,
                                      meta['status'], meta['images_total'], len(meta['images_failed']),
                                      hashlib.sha256(Path(md_path).read_bytes()).hexdigest()])))
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=INDEX_FIELDS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(Path(out_root) / '文章清单.csv', output.getvalue().encode('utf-8-sig'))


def url_seen(out_root, url, require_images=True):
    identifier = article_key(url)
    for row in index_rows(out_root):
        if row_key(row) != identifier:
            continue
        if row.get('状态') not in ('complete', 'remote') or require_images and row.get('状态') == 'remote':
            return False
        path = contained_path(out_root, row['本地文件'])
        if not source_matches(path, url):
            return False
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row.get('内容SHA256'):
            return False
        if require_images:
            for local in re.findall(r'!\[[^\n]*?\]\((images/[^)]+)\)', data.decode('utf-8')):
                if not contained_path(path.parent, urllib.parse.unquote(local)).is_file():
                    return False
        return True
    return False


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def process_url(url, args, opener):
    result = {'url': url, 'ok': False, 'status': 'failed', 'error': '', 'title': '', 'warnings': []}
    try:
        url = validate_url(url)
        raw = http_get(url, opener, timeout=args.timeout, retries=args.retries, verbose=args.verbose)
        html_text = raw.decode('utf-8-sig', errors='strict')
        detect_block(html_text)
        meta = extract_meta(html_text)
        result['title'] = meta['title']
        body_html = extract_body_html(html_text)
        body_md, images = html_to_markdown(body_html, clean=args.clean)
        if not body_md.strip():
            raise WeChatFetchError('正文解析为空')
        builder = TreeBuilder()
        builder.feed(body_html)
        meta.update(_body_html=body_html, _images=images, _text=visible_text(builder.root))
        art_dir, count, md_path = write_article(args.output, meta, body_md, url, args, opener)
        append_index(args.output, meta, url, md_path)
        result.update(ok=True, status=meta['status'], dir=art_dir, file=md_path, images=count,
                      images_total=len(images), images_failed=meta['images_failed'], words=meta['word_count'],
                      warnings=meta['images_failed'], blocked=meta['blocked'])
    except (WeChatFetchError, OSError, ValueError, RecursionError) as exc:
        result.update(error=str(exc), error_type=getattr(exc, 'kind', 'local_or_parse'), blocked=isinstance(exc, AccessBlocked))
    return result


class LinkFinder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            value = dict(attrs).get('href', '')
            if value:
                self.urls.append(urllib.parse.urljoin(REFERER, value))


def discover(url, args, opener):
    raw = http_get(url, opener, timeout=args.timeout, retries=args.retries, verbose=args.verbose)
    text = raw.decode('utf-8-sig', errors='strict')
    detect_block(text)
    parser = LinkFinder()
    parser.feed(extract_body_html(text))
    seen, links = {article_key(url)}, []
    for candidate in parser.urls:
        try:
            candidate = validate_url(candidate)
            identifier = article_key(candidate)
        except WeChatFetchError:
            continue
        if identifier not in seen:
            seen.add(identifier)
            links.append(candidate)
    print('# 发现 %d 个正文引用文章链接' % len(links), file=sys.stderr)
    for link in links:
        print(link)
    return 0


def load_urls_file(path):
    with open(path, encoding='utf-8-sig') as stream:
        return [line.strip() for line in stream if line.strip() and not line.lstrip().startswith('#')]


def run_batch(urls, args, opener):
    summary = dict(complete=0, partial=0, failed=0, skipped=0, remote=0)
    requested = False
    stopped = []
    for i, url in enumerate(urls):
        if not args.discover and not args.force and url_seen(args.output, url, require_images=not args.no_images):
            summary['skipped'] += 1
            print('[%d/%d] 已归档，跳过' % (i + 1, len(urls)))
            continue
        if requested:
            time.sleep(args.delay)
        requested = True
        if args.discover:
            try:
                discover(url, args, opener)
                continue
            except (WeChatFetchError, OSError, ValueError) as exc:
                result = {'ok': False, 'blocked': isinstance(exc, AccessBlocked), 'error': str(exc)}
        else:
            print('[%d/%d] %s' % (i + 1, len(urls), url))
            result = process_url(url, args, opener)
        if result['ok']:
            summary[result['status']] += 1
            print('    %s：%s；图片 %d/%d；%s' % (result['status'], result['title'], result['images'], result['images_total'], result['file']))
            for warning in result['warnings']:
                print('    图片未本地化：%s (%s)' % (warning['url'], warning['error']), file=sys.stderr)
        else:
            summary['failed'] += 1
            print('    失败：' + result['error'], file=sys.stderr)
        if result.get('blocked'):
            stopped = urls[i:]
            if not args.discover:
                atomic_write(Path(args.output) / '待处理链接.txt', ('\n'.join(stopped) + '\n').encode('utf-8'))
            print('检测到访问受限，已停止。待处理链接：\n' + '\n'.join(stopped), file=sys.stderr)
            break
    if not args.discover:
        print('完成：完整 %(complete)d，保留远程图片 %(remote)d，部分完成 %(partial)d，失败 %(failed)d，跳过 %(skipped)d' % summary)
    return 1 if stopped or summary['failed'] or summary['partial'] else 0


def main(argv=None):
    p = argparse.ArgumentParser(description='微信公众号文章 → 本地 Markdown（仅标准库）')
    p.add_argument('urls', nargs='*', help='文章链接')
    p.add_argument('-f', '--file', help='链接列表，每行一个；支持 UTF-8 BOM 和 # 注释')
    p.add_argument('-o', '--output', default='./wechat-md', help='输出目录，默认 ./wechat-md')
    p.add_argument('--no-images', action='store_true', help='主动保留远程图片，状态记为 remote')
    p.add_argument('--image-workers', type=int, default=2, help='图片并发数，1–4，默认 2')
    p.add_argument('--delay', type=float, default=2, help='文章之间间隔秒数，至少 2，含 discover')
    p.add_argument('--timeout', type=int, default=30, help='文章及图片单次请求超时秒数，默认 30')
    p.add_argument('--retries', type=int, default=3, help='暂时失败时最多尝试次数（含首次），1–5，默认 3')
    p.add_argument('--force', action='store_true', help='重新抓取并更新相同文章')
    p.add_argument('--clean', dest='clean', action='store_true', default=True, help='清理普通文本节点中的 UI 噪音（默认）')
    p.add_argument('--no-clean', dest='clean', action='store_false', help='保留普通文本中的 UI 噪音')
    p.add_argument('--discover', action='store_true', help='只输出正文引用的微信文章链接')
    p.add_argument('--insecure', action='store_true', help='跳过 TLS 证书验证，有连接被冒充的风险')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('--version', action='version', version='wx2md ' + __version__)
    args = p.parse_args(argv)
    if not math.isfinite(args.delay) or args.delay < 2:
        p.error('--delay 必须为至少 2 秒的有限数值')
    if args.timeout <= 0 or not 1 <= args.retries <= 5 or not 1 <= args.image_workers <= 4:
        p.error('--timeout 需大于 0，--retries 为 1–5，--image-workers 为 1–4')
    try:
        urls = args.urls + (load_urls_file(args.file) if args.file else [])
        if not urls:
            p.error('请提供文章链接或 -f 文件')
        unique = {}
        for url in urls:
            normalized = validate_url(url)
            unique.setdefault(article_key(normalized), normalized)
        urls = list(unique.values())
    except (OSError, WeChatFetchError, ValueError) as exc:
        p.error(str(exc))
    if args.insecure:
        print('警告：TLS 证书验证已关闭，连接可能被冒充。', file=sys.stderr)
    args.output = str(Path(args.output).expanduser().resolve())
    opener = build_opener(args.insecure)
    lock = None
    try:
        if not args.discover:
            Path(args.output).mkdir(parents=True, exist_ok=True)
            lock_path = Path(args.output) / '.wx2md.lock'
            try:
                lock = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise WeChatFetchError('输出目录已被占用；若进程异常退出，请确认无人使用后移除 .wx2md.lock')
            os.write(lock, str(os.getpid()).encode())
        return run_batch(urls, args, opener)
    except (WeChatFetchError, OSError, ValueError) as exc:
        print('错误：' + str(exc), file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            os.close(lock)
            lock_path.unlink()


if __name__ == '__main__':
    sys.exit(main())
