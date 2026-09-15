#!/usr/bin/env python3
"""Regression tests derived from the initial offline audit.

Only synthetic input, temporary output, and mocked network responses are used.
The reviewed module is loaded without writing bytecode or modifying its source.
"""
import contextlib
import csv
import html
import io
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
import urllib.error
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'wx2md.py'
wx = types.ModuleType('reviewed_wx2md')
exec(compile(SOURCE.read_text(), str(SOURCE), 'exec'), wx.__dict__)

def args(root, **changes):
    values = dict(output=str(root), force=False, verbose=False, no_images=True,
                  image_workers=1, timeout=7, retries=1, clean=True)
    values.update(changes)
    return types.SimpleNamespace(**values)


def article(body='正文', title='测试标题'):
    return ('<meta property="og:title" content="%s">'
            '<meta name="author" content="测试公众号">'
            '<div id="js_content">%s</div>' % (title, body)).encode()


class Response:
    def __init__(self, payload, ctype='text/html'):
        self.payload = payload
        self.headers = {'Content-Type': ctype}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


class Opener:
    def __init__(self, payload, ctype='text/html'):
        self.payload, self.ctype, self.calls = payload, ctype, []

    def open(self, req, timeout=None):
        self.calls.append({'url': req.full_url, 'timeout': timeout})
        return Response(self.payload, self.ctype)


def basic_conversion():
    got, images = wx.html_to_markdown('<h2>小节</h2><p>正文<strong>重点</strong></p><img data-src="https://mmbiz.qpic.cn/a.png">')
    assert '## 小节' in got and '**重点**' in got and images == ['https://mmbiz.qpic.cn/a.png'], repr(got)
    return got


def nested_div():
    got = wx.extract_body_html('<div id="js_content"><div>A<div>B</div>C</div>D</div><div>OUT</div>')
    assert got == '<div>A<div>B</div>C</div>D', got
    return got


def code_preservation():
    code = 'pattern = "****"\ntext = "中文 空格"\n\n\nend = 1'
    got, _ = wx.html_to_markdown('<pre class="language-python">' + html.escape(code) + '</pre>')
    assert code in got, 'Code changed: ' + repr(got)
    return got


def false_block():
    try:
        wx.detect_block(article('<p>出现环境异常时，请检查配置。</p>').decode())
    except wx.WeChatFetchError as exc:
        raise AssertionError('Valid article rejected: ' + str(exc))
    return 'accepted valid article'


def real_block():
    try:
        wx.detect_block('<html>环境异常，请验证</html>')
    except wx.WeChatFetchError:
        return 'verification page rejected'
    raise AssertionError('Verification page accepted')


def collision(force=False):
    with tempfile.TemporaryDirectory() as root:
        a = args(root, force=force)
        one = wx.process_url('https://mp.weixin.qq.com/s/aaaaaaaa', a, Opener(article('FIRST')))
        two = wx.process_url('https://mp.weixin.qq.com/s/bbbbbbbb', a, Opener(article('SECOND')))
        with open(Path(root) / '文章清单.csv', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))[1:]
        paths = [r[4] for r in rows]
        content = (Path(root) / paths[0]).read_text()
        assert len(set(paths)) == 2, 'Both ok=%s/%s; same file=%s; surviving body=%s' % (one['ok'], two['ok'], paths[0], 'FIRST' if 'FIRST' in content else 'SECOND')
        return paths


def missing_archive():
    with tempfile.TemporaryDirectory() as root:
        url = 'https://mp.weixin.qq.com/s/aaaaaaaa'
        result = wx.process_url(url, args(root), Opener(article()))
        for path in Path(root).rglob('*.md'):
            path.unlink()
        assert not wx.url_seen(root, url), 'url_seen=True after the archived Markdown was removed; CLI will skip recovery'
        return result


def force_index():
    with tempfile.TemporaryDirectory() as root:
        url = 'https://mp.weixin.qq.com/s/aaaaaaaa'
        wx.process_url(url, args(root), Opener(article('FIRST')))
        wx.process_url(url, args(root, force=True), Opener(article('SECOND')))
        with open(Path(root) / '文章清单.csv', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))[1:]
        assert len(rows) == 1, 'Same URL appears %d times after force update' % len(rows)
        return rows


def image_html():
    with tempfile.TemporaryDirectory() as root:
        mapping = wx.download_images(['https://mmbiz.qpic.cn/a.jpg'], root, Opener(b'<html>denied</html>'), workers=1)
        saved = list(Path(root).iterdir())
        assert not saved, 'Non-image response saved as %s; mapping=%s' % (saved[0].name, mapping)
        return mapping


def image_failure():
    class FailedImage:
        def open(self, req, timeout=None):
            if req.full_url.startswith('https://mp.weixin.qq.com/'):
                return Response(article('<p>正文</p><img src="https://mmbiz.qpic.cn/a.jpg">'))
            raise urllib.error.URLError('synthetic image failure')
    with tempfile.TemporaryDirectory() as root, patch.object(wx.time, 'sleep'):
        result = wx.process_url('https://mp.weixin.qq.com/s/aaaaaaaa', args(root, no_images=False), FailedImage())
        assert not result['ok'] or result.get('warnings') or result.get('images_failed'), 'Image failed but result has no partial/warning state: %s' % result
        return result


def image_timeout():
    with tempfile.TemporaryDirectory() as root:
        opener = Opener(b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x01' * 2, 'image/png')
        meta = dict(author='测试', title='图片', pub_date='2026-09-15')
        wx.write_article(root, meta, '![图](https://mmbiz.qpic.cn/a.png)', 'https://mp.weixin.qq.com/s/aaaaaaaa', args(root, no_images=False), opener)
        assert opener.calls[0]['timeout'] == 7, 'Configured timeout=7, image request timeout=%s' % opener.calls[0]['timeout']
        return opener.calls


def url_boundary():
    with tempfile.TemporaryDirectory() as root:
        opener = Opener(article())
        result = wx.process_url('http://127.0.0.1:8765/internal', args(root), opener)
        assert not opener.calls, 'Localhost URL reached mocked opener and ok=%s: %s' % (result['ok'], opener.calls)
        return result


def discover_delay():
    with patch.object(wx, 'build_opener', return_value=object()), patch.object(wx, 'discover', return_value=0) as discover, patch.object(wx.time, 'sleep') as sleep:
        wx.main(['--discover', '--delay', '3', 'https://mp.weixin.qq.com/s/aaaaaaaa', 'https://mp.weixin.qq.com/s/bbbbbbbb'])
        assert sleep.call_count >= 1, 'discover calls=%d; sleep calls=%d despite delay=3' % (discover.call_count, sleep.call_count)
        return sleep.call_args_list


def batch_block_stop():
    with tempfile.TemporaryDirectory() as root:
        opener = Opener('<html>环境异常，请验证</html>'.encode())
        with patch.object(wx, 'build_opener', return_value=opener), patch.object(wx.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            wx.main(['--no-images', '-o', root, 'https://mp.weixin.qq.com/s/aaaaaaaa', 'https://mp.weixin.qq.com/s/bbbbbbbb'])
        assert len(opener.calls) == 1, 'Verification response did not stop batch; fetched %d article URLs' % len(opener.calls)
        return opener.calls


def http_permanent_error():
    class Gone:
        def __init__(self):
            self.calls = 0

        def open(self, req, timeout=None):
            self.calls += 1
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)
    opener = Gone()
    with patch.object(wx.time, 'sleep'):
        try:
            wx.http_get('https://mp.weixin.qq.com/s/aaaaaaaa', opener)
        except wx.WeChatFetchError:
            pass
    assert opener.calls == 1, 'HTTP 404 requested %d times' % opener.calls
    return opener.calls


def table_structure():
    got, _ = wx.html_to_markdown('<table><tr><th>名称</th><th>值</th></tr><tr><td>A</td><td>1</td></tr></table>')
    assert '---' in got, 'No Markdown header delimiter: ' + repr(got)
    return got


def ordered_list():
    got, _ = wx.html_to_markdown('<ol start="3"><li>甲</li><li>乙</li></ol>')
    assert '3.' in got and '4.' in got, 'Ordered list became: ' + repr(got)
    return got


def single_quote_body():
    try:
        got = wx.extract_body_html("<div id='js_content'><p>正文</p></div>")
    except wx.WeChatFetchError as exc:
        raise AssertionError('Valid single-quoted attribute rejected: ' + str(exc))
    assert '正文' in got
    return got


def publication_timezone():
    if not hasattr(time, 'tzset'):
        raise unittest.SkipTest('tzset unavailable on this platform')
    old = os.environ.get('TZ')
    outputs = {}
    try:
        for zone in ('UTC', 'Asia/Shanghai'):
            os.environ['TZ'] = zone
            time.tzset()
            outputs[zone] = wx.extract_meta('var ct = "1704038400";')['pub_date']
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()
    assert len(set(outputs.values())) == 1, 'Same publish timestamp produces different dates: %s' % outputs
    return outputs


CASES = [
    ('basic_conversion', basic_conversion), ('nested_div', nested_div),
    ('code_preservation', code_preservation), ('false_block', false_block),
    ('real_block', real_block), ('same_title_collision', collision),
    ('force_collision', lambda: collision(True)), ('missing_archive_recovery', missing_archive),
    ('force_index_update', force_index), ('image_html_response', image_html),
    ('image_failure_status', image_failure), ('image_timeout', image_timeout),
    ('article_url_boundary', url_boundary), ('discover_delay', discover_delay),
    ('batch_block_stop', batch_block_stop), ('permanent_http_error', http_permanent_error),
    ('table_structure', table_structure), ('ordered_list', ordered_list),
    ('single_quote_body', single_quote_body), ('publication_timezone', publication_timezone),
]

class AuditRegressions(unittest.TestCase):
    """Original audit expectations, with request-compatible response doubles."""


def make_test(fn):
    def test(self):
        fn()
    return test


for name, fn in CASES:
    setattr(AuditRegressions, 'test_' + name, make_test(fn))

if __name__ == '__main__':
    unittest.main()
