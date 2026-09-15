"""Offline behavior tests; no real network access or private input."""
import base64
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from test_regressions import wx, args, article, Response, Opener

ARTICLE = 'https://mp.weixin.qq.com/s/aaaaaaaa'
IMAGE = 'https://mmbiz.qpic.cn/a.png'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWioAAAAASUVORK5CYII=')


class RoutedOpener(Opener):
    def __init__(self, body, image=PNG):
        super().__init__(article(body))
        self.image = image

    def open(self, req, timeout=None):
        self.calls.append({'url': req.full_url, 'timeout': timeout})
        if req.full_url.startswith('https://mp.weixin.qq.com/'):
            return Response(self.payload)
        if isinstance(self.image, Exception):
            raise self.image
        return Response(self.image, 'image/png')


class Network(unittest.TestCase):
    def test_invalid_urls_never_reach_opener(self):
        bad = ['file:///tmp/test', 'ftp://mp.weixin.qq.com/s/aaaaaaaa',
               'https://mp.weixin.qq.com.evil.invalid/s/aaaaaaaa',
               'https://mp.weixin.qq.com@evil.invalid/s/aaaaaaaa',
               'https://user@mp.weixin.qq.com/s/aaaaaaaa',
               'https://mp.weixin.qq.com:444/s/aaaaaaaa',
               'https://mp.weixin.qq.com/s/aaaa\naaaa',
               'https://mp.weixin.qq.com/s?mid=123',
               'https://mp.weixin.qq.com/s?__biz=x&mid=1&mid=2']
        opener = Opener(article())
        for url in bad:
            with self.subTest(url=url), self.assertRaises(wx.InvalidURL):
                wx.http_get(url, opener)
        self.assertEqual([], opener.calls)

    def test_redirect_boundaries(self):
        handler = wx.SafeRedirect()
        for kind, source, bad in [('article', ARTICLE, 'https://example.invalid/x'),
                                  ('image', IMAGE, 'http://127.0.0.1/pic'),
                                  ('article', ARTICLE, 'file:///tmp/test')]:
            req = urllib.request.Request(source)
            req.wx_kind = kind
            with self.subTest(kind=kind, bad=bad), self.assertRaises(wx.InvalidURL):
                handler.redirect_request(req, None, 302, '', {}, bad)
        req = urllib.request.Request(IMAGE)
        req.wx_kind = 'image'
        follow = handler.redirect_request(req, None, 302, '', {}, 'https://mmbiz.qpic.cn/next.png')
        self.assertEqual('image', follow.wx_kind)

    def test_verification_redirect_is_blocked(self):
        req = urllib.request.Request(ARTICLE)
        with self.assertRaises(wx.AccessBlocked):
            wx.SafeRedirect().redirect_request(req, None, 302, '', {}, 'https://mp.weixin.qq.com/mp/verify')

    def test_final_url_revalidated(self):
        class WrongFinal(Opener):
            def open(self, req, timeout=None):
                response = Response(article())
                response.geturl = lambda: 'https://example.invalid/x'
                return response
        with self.assertRaises(wx.InvalidURL):
            wx.http_get(ARTICLE, WrongFinal(article()))

    def test_size_limit(self):
        with patch.object(wx, 'MAX_HTML', 4), self.assertRaises(wx.WeChatFetchError):
            wx.http_get(ARTICLE, Opener(b'12345'))
        with patch.object(wx, 'MAX_IMAGE', 4), self.assertRaises(wx.WeChatFetchError):
            wx.fetch(IMAGE, Opener(PNG, 'image/png'), kind='image')

    def test_blocked_http_is_not_retried(self):
        for status in (401, 403, 429):
            class Blocked(Opener):
                def open(self, req, timeout=None):
                    self.calls.append(req.full_url)
                    raise urllib.error.HTTPError(req.full_url, status, '', {}, io.BytesIO())
            opener = Blocked(b'')
            with self.subTest(status=status), self.assertRaises(wx.AccessBlocked):
                wx.http_get(ARTICLE, opener)
            self.assertEqual(1, len(opener.calls))

    def test_temporary_error_has_bounded_retry(self):
        class Temporary(Opener):
            def open(self, req, timeout=None):
                self.calls.append(req.full_url)
                raise urllib.error.HTTPError(req.full_url, 503, '', {}, io.BytesIO())
        opener = Temporary(b'')
        with patch.object(wx.time, 'sleep') as sleep, self.assertRaises(wx.WeChatFetchError):
            wx.http_get(ARTICLE, opener, retries=3)
        self.assertEqual(3, len(opener.calls))
        self.assertEqual([2, 4], [c.args[0] for c in sleep.call_args_list])

    def test_untrusted_image_no_request(self):
        opener = Opener(PNG, 'image/png')
        with tempfile.TemporaryDirectory() as root:
            result = wx.download_images(['http://127.0.0.1/private'], root, opener)
        self.assertFalse(opener.calls)
        self.assertEqual(1, len(result.failures))


class Conversion(unittest.TestCase):
    def test_metadata_attribute_order_and_single_quotes(self):
        source = "<meta content='标题 &amp; 测试' property='og:title'><meta content='作者' name='author'><em id='publish_time'>2026年9月5日</em>"
        meta = wx.extract_meta(source)
        self.assertEqual(('标题 & 测试', '作者', '2026-09-05'), (meta['title'], meta['author'], meta['pub_date']))

    def test_pre_preserves_noise_and_nested_fences(self):
        source = '```python\nprint("****")\n```\n知道了\n中文 空格\n'
        md, _ = wx.html_to_markdown('<pre><code class="language-python">' + source + '</code></pre>', clean=True)
        self.assertTrue(md.startswith('````python\n'))
        self.assertIn(source, md)
        self.assertTrue(md.rstrip().endswith('````'))

    def test_inline_code_preserves_asterisks_and_spaces(self):
        md, _ = wx.html_to_markdown('<p><code>**** 中文 空格 `</code></p>', clean=True)
        self.assertIn('`` **** 中文 空格 ` ``', md)

    def test_adjacent_and_nested_bold(self):
        md, _ = wx.html_to_markdown('<strong>A</strong><b>B<strong>C</strong></b>')
        self.assertEqual('**ABC**\n', md)

    def test_bold_across_transparent_spans(self):
        md, _ = wx.html_to_markdown('<span><strong>●</strong></span><span><strong>最新消息</strong></span>')
        self.assertEqual('**●最新消息**\n', md)

    def test_nested_lists(self):
        md, _ = wx.html_to_markdown('<ol start="3"><li>A<ul><li>B</li></ul></li><li>C</li></ol>')
        self.assertIn('3. A', md)
        self.assertIn('   - B', md)
        self.assertIn('4. C', md)

    def test_hidden_scripts_excluded(self):
        md, _ = wx.html_to_markdown('<p>visible</p><script>secret</script><style>hidden</style><p style="display: none">hidden</p><p hidden>hidden</p>')
        self.assertEqual('visible\n', md)

    def test_parenthesis_url_and_image_alt(self):
        md, images = wx.html_to_markdown('<img data-src="//mmbiz.qpic.cn/a(b).png" alt="[图]">')
        self.assertIn('a%28b%29.png', md)
        self.assertIn('\\[图\\]', md)
        self.assertEqual(['https://mmbiz.qpic.cn/a(b).png'], images)

    def test_complex_table_explicit_fallback(self):
        md, _ = wx.html_to_markdown('<table><tr><td colspan="2">A</td></tr></table>')
        self.assertIn('按行展开', md)
        self.assertIn('A', md)

    def test_media_placeholder(self):
        md, _ = wx.html_to_markdown('<mpvideo></mpvideo><mpvoice name="测试" voice_encode_fileid="abc"></mpvoice>')
        self.assertIn('未提取到可播放地址', md)
        self.assertIn('标识：abc', md)

    def test_excessive_nesting_rejected(self):
        with self.assertRaises(wx.WeChatFetchError):
            wx.html_to_markdown('<div>' * 150 + 'text' + '</div>' * 150)


class Archive(unittest.TestCase):
    def test_image_archive_and_missing_image_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            opener = RoutedOpener('<p>正文</p><img src="%s">' % IMAGE)
            result = wx.process_url(ARTICLE, args(root, no_images=False), opener)
            self.assertEqual('complete', result['status'])
            self.assertTrue(wx.url_seen(root, ARTICLE))
            image = next(Path(root).rglob('*.png'))
            self.assertEqual(PNG, image.read_bytes())
            image.unlink()
            self.assertFalse(wx.url_seen(root, ARTICLE))
            again = wx.process_url(ARTICLE, args(root, no_images=False), opener)
            self.assertEqual('complete', again['status'])
            self.assertTrue(wx.url_seen(root, ARTICLE))
            self.assertEqual(1, len(wx.index_rows(root)))

    def test_remote_mode_does_not_skip_later_offline_request(self):
        with tempfile.TemporaryDirectory() as root:
            result = wx.process_url(ARTICLE, args(root), RoutedOpener('<img src="%s">' % IMAGE))
            self.assertEqual('remote', result['status'])
            self.assertFalse(wx.url_seen(root, ARTICLE))
            self.assertTrue(wx.url_seen(root, ARTICLE, require_images=False))

    def test_partial_then_recover(self):
        with tempfile.TemporaryDirectory() as root:
            bad = RoutedOpener('<p>正文</p><img src="%s">' % IMAGE, image=b'<html>bad</html>')
            result = wx.process_url(ARTICLE, args(root, no_images=False), bad)
            self.assertEqual('partial', result['status'])
            self.assertFalse(wx.url_seen(root, ARTICLE))
            good = RoutedOpener('<p>正文</p><img src="%s">' % IMAGE)
            result = wx.process_url(ARTICLE, args(root, no_images=False), good)
            self.assertEqual('complete', result['status'])
            self.assertEqual(1, len(wx.index_rows(root)))

    def test_image_does_not_rewrite_ordinary_link(self):
        with tempfile.TemporaryDirectory() as root:
            body = '<a href="%s">原图链接</a><img src="%s">' % (IMAGE, IMAGE)
            result = wx.process_url(ARTICLE, args(root, no_images=False), RoutedOpener(body))
            text = Path(result['file']).read_text()
            self.assertIn('[原图链接](%s)' % IMAGE, text)
            self.assertIn('](images/', text)

    def test_long_titles_portable_components(self):
        with tempfile.TemporaryDirectory() as root:
            result = wx.process_url(ARTICLE, args(root), Opener(article('正文', title='中' * 120)))
            self.assertTrue(result['ok'], result)
            self.assertTrue(all(len(p.encode()) <= 255 for p in Path(result['file']).parts))
            self.assertEqual('_CON', wx.safe_name('CON'))

    def test_word_count_independent_of_images(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            body = '<p>中文 two words</p><img src="%s">' % IMAGE
            remote = wx.process_url(ARTICLE, args(a), RoutedOpener(body))
            local = wx.process_url(ARTICLE, args(b, no_images=False), RoutedOpener(body))
            self.assertEqual(4, remote['words'])
            self.assertEqual(remote['words'], local['words'])

    def test_tracking_links_deduplicate(self):
        self.assertEqual(wx.article_key(ARTICLE), wx.article_key(ARTICLE + '?scene=2#read'))
        one = 'https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1&sn=signed&scene=1'
        two = 'https://mp.weixin.qq.com/s?mid=123&__biz=abc&sn=other&idx=1'
        self.assertEqual(wx.article_key(one), wx.article_key(two))
        self.assertNotEqual(wx.article_key(one), wx.article_key(two.replace('idx=1', 'idx=2')))

    def test_legacy_archive_updates_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'legacy.md'
            path.write_text('---\nsource: ' + json.dumps(ARTICLE) + '\n---\nOLD')
            with (Path(root) / '文章清单.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['标题', '公众号', '发布日期', '链接', '本地文件', '字数'])
                writer.writerow(['旧', '旧', '', ARTICLE, 'legacy.md', 1])
            result = wx.process_url(ARTICLE, args(root), Opener(article('NEW')))
            self.assertEqual(path.resolve(), Path(result['file']))
            self.assertIn('NEW', path.read_text())
            self.assertEqual(1, len(wx.index_rows(root)))

    def test_atomic_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'archive.md'
            path.write_bytes(b'OLD')
            with patch.object(wx.os, 'replace', side_effect=OSError('synthetic disk failure')):
                with self.assertRaises(OSError):
                    wx.atomic_write(path, b'NEW')
            self.assertEqual(b'OLD', path.read_bytes())
            self.assertEqual([path], list(Path(root).iterdir()))

    def test_escape_outside_output_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(wx.WeChatFetchError):
                wx.contained_path(root, '../outside')

    def test_csv_formula_text(self):
        with tempfile.TemporaryDirectory() as root:
            result = wx.process_url(ARTICLE, args(root), Opener(article(title='=SUM(1,2)')))
            self.assertTrue(result['ok'])
            self.assertEqual("'=SUM(1,2)", wx.index_rows(root)[0]['标题'])
            self.assertIn('title: "=SUM(1,2)"', Path(result['file']).read_text())


class CommandLine(unittest.TestCase):
    def test_invalid_options(self):
        for option, value in [('--delay', '0'), ('--delay', 'nan'), ('--timeout', '0'), ('--retries', '0'), ('--image-workers', '20')]:
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                wx.main([ARTICLE, option, value])
            self.assertEqual(2, caught.exception.code)

    def test_partial_exit_status_and_lock_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            opener = RoutedOpener('<img src="%s">' % IMAGE, image=b'bad')
            with patch.object(wx, 'build_opener', return_value=opener), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = wx.main([ARTICLE, '-o', root])
            self.assertEqual(1, rc)
            self.assertFalse((Path(root) / '.wx2md.lock').exists())

    def test_existing_lock_prevents_network_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            lock = Path(root) / '.wx2md.lock'
            lock.write_text('123')
            opener = Opener(article())
            with patch.object(wx, 'build_opener', return_value=opener), contextlib.redirect_stderr(io.StringIO()):
                rc = wx.main([ARTICLE, '-o', root])
            self.assertEqual(1, rc)
            self.assertEqual([], opener.calls)
            self.assertTrue(lock.exists())

    def test_discovery_only_links_in_body(self):
        body = '<a href="/s/bbbbbbbb?scene=1">B</a><a href="/s/bbbbbbbb?scene=2">B</a><a href="https://mp.weixin.qq.com/s?mid=123">invalid</a>'
        opener = Opener(article(body) + b'<script>"https://mp.weixin.qq.com/s/cccccccc"</script>')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            wx.discover(ARTICLE, args('.'), opener)
        self.assertEqual(['https://mp.weixin.qq.com/s/bbbbbbbb?scene=1'], output.getvalue().splitlines())

    def test_bom_url_list(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'urls.txt'
            path.write_text('# comment\n' + ARTICLE + '\n', encoding='utf-8-sig')
            self.assertEqual([ARTICLE], wx.load_urls_file(path))

    def test_blocked_image_stops_remaining_articles(self):
        with tempfile.TemporaryDirectory() as root:
            error = urllib.error.HTTPError(IMAGE, 429, '', {}, io.BytesIO())
            opener = RoutedOpener('<img src="%s">' % IMAGE, image=error)
            with patch.object(wx, 'build_opener', return_value=opener), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = wx.main([ARTICLE, 'https://mp.weixin.qq.com/s/bbbbbbbb', '-o', root])
            self.assertEqual(1, rc)
            article_calls = [c for c in opener.calls if c['url'].startswith('https://mp.weixin.qq.com/')]
            self.assertEqual(1, len(article_calls))
            self.assertEqual(2, len((Path(root) / '待处理链接.txt').read_text().splitlines()))


if __name__ == '__main__':
    unittest.main()
