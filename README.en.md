# wechat-md

[简体中文](README.md) | **English** | [日本語](README.ja.md)

**Bring WeChat Official Account articles into Codex and your local knowledge base.**

Convert public WeChat articles to Markdown, download their images, preserve source metadata, and record whether each archive is complete. Use it as a standalone CLI or as a Codex skill.

[![Tests](https://github.com/passerbied/wechat-md/actions/workflows/tests.yml/badge.svg)](https://github.com/passerbied/wechat-md/actions/workflows/tests.yml)

Python 3.9+ · Zero third-party runtime dependencies · [MIT License](LICENSE) · Current version: `1.1.0rc1`

## Why this project exists

You give Codex a WeChat article and ask it to summarize the argument, compare viewpoints, or add the material to a knowledge base—only to get stuck at the “read the article” step. `wechat-md` grew out of that practical problem.

Copying and pasting the article body may keep the conversation moving, but images, code formatting, and source metadata still need separate cleanup. The result may also be difficult to reuse in another session or tool.

`wechat-md` turns that manual step into a repeatable workflow: **provide a known public article URL, extract its text and images locally, save it with source metadata, and then let Codex or another tool work with the archived material.**

```text
WeChat article URL → wechat-md → Markdown + local images + archive manifest
                                           ↓
                              Codex analysis / knowledge base
```

Codex itself supports capabilities such as [web search](https://learn.chatgpt.com/docs/codex/cli), but access to a particular page still depends on the tools in use, network permissions, and the page's access state. `wechat-md` provides a local extraction path; it also requires network access and remains subject to verification pages and rate limits.

## What you get

- **Minimal setup.** The script only needs Python. It does not require third-party Python packages, a browser, an MCP server, an API key, or cookies.
- **Reusable local files.** Markdown, images, and source metadata stay in your own directory, ready for Codex, other agents, or knowledge-base tools.
- **Explicit archive status.** The manifest distinguishes complete archives, intentionally remote images, and incomplete image downloads. Missing content is reported.
- **Safe reruns.** Articles with identical titles do not overwrite one another. Reprocessing the same article updates its manifest entry, and interrupted or incomplete archives can be retried.

The script handles extraction and archiving. Summarization, translation, rewriting, and image understanding are left to Codex or another downstream tool.

## Quick start

```bash
git clone https://github.com/passerbied/wechat-md.git
cd wechat-md

# Replace the example URL with a real public WeChat article URL
python3 wx2md.py "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" -o ./archive
```

### Use it as a Codex skill

According to the [official OpenAI documentation](https://developers.openai.com/docs/build-skills), Codex's built-in `$skill-installer` can download skills from other repositories. Paste this instruction directly into Codex:

```text
$skill-installer Install the wechat-md skill from https://github.com/passerbied/wechat-md. Before installing, check whether a skill with the same name already exists. If it does, do not overwrite it; report its current state first. After installation, confirm that SKILL.md and wx2md.py are in the same skill directory, then check whether python3 --version reports Python 3.9 or later. If Python is missing or too old, do not install it immediately. First explain the installation method appropriate for the current operating system and obtain my explicit approval. Once Python is available, run python3 wx2md.py --version to verify the skill. Do not fetch any WeChat articles during setup.
```

Codex will select the user-level skill directory appropriate for the current environment. If the new skill does not appear after installation, restart Codex.

For manual installation, install the repository as a complete skill, keeping `SKILL.md` and `wx2md.py` in the same directory. On macOS or Linux, create a symlink from the repository root:

```bash
mkdir -p "$HOME/.codex/skills"
ln -s "$PWD" "$HOME/.codex/skills/wechat-md"
```

If a skill with the same name already exists at the destination, inspect it before replacing anything. On Windows, copy the repository into your skills directory and name the folder `wechat-md`.

You can then ask Codex:

> Use $wechat-md to read this WeChat article, summarize its main ideas, and preserve the source URL.

> Use $wechat-md to save these articles to my knowledge base and tell me which images could not be downloaded.

See [SKILL.md](SKILL.md) for the complete agent workflow. Your Codex account and network permissions still apply.

### Batch archiving and link discovery

```bash
# urls.txt accepts one URL per line, blank lines, # comments, and a UTF-8 BOM
python3 wx2md.py -f urls.txt -o ./archive --delay 3

# Find links to other WeChat articles referenced by one article
python3 wx2md.py --discover "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" > urls.txt

# Save the article body while intentionally leaving image URLs remote
python3 wx2md.py "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" -o ./archive --no-images
```

`--discover` only finds links referenced by the current article. It does not retrieve an account's complete article history.

## Choosing among related tools

Different tools optimize for different workflows. If you already have public WeChat article URLs and want local archives with few setup requirements, `wechat-md` is a good place to start.

| Tool | Primary use case | What to know before choosing it |
|---|---|---|
| **wechat-md** | Known WeChat article URLs → Markdown, images, and a local archive manifest | One Python script; no browser automation, video extraction, or full-history synchronization |
| [baoyu-url-to-markdown](https://github.com/JimLiu/baoyu-skills/blob/main/skills/baoyu-url-to-markdown/SKILL.md) | General web pages → Markdown, with browser-assisted waiting and media downloads | Requires Bun, related dependencies, and a Chrome/CDP environment; useful when you already have a browser workflow or need dynamic-page handling |
| [weixin-articles-mcp](https://github.com/jj-cheng25/weixin-articles-mcp#readme) | Return article content, native image blocks, and video keyframes directly to a model | No browser required; requires package installation and MCP configuration, plus FFmpeg for video features; its documented workflow focuses on immediate model input, so durable archiving needs an additional save step |
| [wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter#readme) | Previously offered account article-list synchronization and multiple export formats | The maintainer announced on July 30, 2026 that maintenance had ended because an upstream core API was shut down; read the maintenance notice before using or forking it |

This comparison is based on each project's public documentation, checked on September 15, 2026. It is not a same-environment benchmark of speed or success rate.

`wechat-md` does not depend on an article-history synchronization API; its scope is limited to URLs supplied by the user. It still needs updates when public article pages change and cannot guarantee successful extraction for every article.

## Output and archive status

```text
archive/
  文章清单.csv
  <account>/articles/<date>-<title>-<article-id>/
    <title>.md
    images/
```

The CSV filename `文章清单.csv` means “article manifest.” Each Markdown file includes the title, author, publication date, source URL, fetch time, article ID, and image counts. Images use relative paths so the article directory remains portable.

| Status | Meaning |
|---|---|
| `complete` | The article body and all recognized, downloadable images were saved |
| `remote` | `--no-images` was used, so image URLs were intentionally left remote |
| `partial` | The article body was saved, but some images were not localized; the output includes the reason |

Exit codes: `0` means the selected operation completed or an existing complete archive was skipped; `1` means failure, partial completion, or blocked access; `2` means invalid input.

**Rerun the same list to check and retry incomplete archives.** Complete, unchanged local files are skipped. Use `--force` when you need to fetch the online content again. If an article was previously archived with `--no-images`, rerunning without that flag will attempt to download its images.

<details>
<summary>Article identity, manifest behavior, and legacy compatibility</summary>

- Long URLs are identified by `__biz / mid / idx`; short URLs are identified by path. Short and long URLs are not automatically correlated, so the same article may be archived twice if supplied in both forms.
- The manifest uses UTF-8 with BOM, updates rows by article ID, and records status plus a SHA-256 hash of the Markdown content. `--force` preserves the existing path when updating the same article.
- Older manifests remain readable. Missing status and hash fields are filled during the next run. A legacy file is updated in place only when its source matches.
- Publication dates are calculated in UTC+8 while retaining the original timestamp when available. Word count includes Chinese characters and English words in the article body, excluding image URLs.
- Article files and the manifest are written atomically. Interrupted runs can be retried, but obsolete images are not deleted automatically.

</details>

## CLI options

| Option | Description |
|---|---|
| `-o, --output DIR` | Output directory; default: `./wechat-md` |
| `-f, --file FILE` | Read URLs from a text file |
| `--no-images` | Intentionally keep remote image URLs |
| `--image-workers N` | Concurrent image downloads, 1–4; default: 2 |
| `--delay N` | Delay between articles, at least 2 seconds; default: 2; also applies to discovery mode |
| `--timeout N` | Per-request timeout for articles and images; default: 30 seconds |
| `--retries N` | Maximum attempts for transient failures, including the first attempt; 1–5; default: 3 |
| `--force` | Refetch and update an existing article |
| `--no-clean` | Preserve UI noise in ordinary text nodes |
| `--discover` | Print only referenced WeChat article URLs |
| `--insecure` | Disable TLS certificate verification; risks connecting to an impersonated endpoint; off by default |
| `-v, --verbose` | Print retry backoff and download diagnostics |
| `--version` | Print the version |

Run `python3 wx2md.py --help` for the complete built-in help.

## Supported content and boundaries

- Supports headings, paragraphs, links, bold text, blockquotes, nested lists, ordered numbering, and simple tables. Tables with merged cells are annotated and flattened by row. Complex visual layouts are not reproduced exactly.
- Code blocks and inline code are processed separately to preserve asterisks and Chinese full-width spaces. Scripts, styles, and explicitly hidden nodes are excluded.
- Performs basic file-signature checks for PNG, JPEG, GIF, and WebP images. This is not a full image decode. Other formats, including SVG and AVIF, remain remote and mark the archive as partial.
- Downloads images only from `mmbiz.qpic.cn`, `mmbiz.qlogo.cn`, and `wx.qlogo.cn`. Images from other hosts remain remote and are reported. HTML responses are limited to 8 MiB and individual images to 20 MiB.
- Audio and video are represented only by any available URL, identifier, or placeholder. They are not downloaded or transcribed, and playback is not guaranteed.
- Does not use cookies, sign in, bypass CAPTCHAs, access private content or chat history, or retrieve a complete account history.

### Verification pages and rate limits

The batch stops after a verification page or HTTP 401, 403, or 429 response. Archive mode writes the remaining URLs to `待处理链接.txt` (“pending URLs”); discovery mode writes pending URLs to stderr. Requests already dispatched by the image worker pool may still finish. Avoid repeated retries, and do not assume an outbound IP is the cause without further evidence.

### Output directory is locked

Only one CLI process may write to an output directory at a time. If a process was forcibly terminated, first confirm that no other process is using the directory, then remove the stale `.wx2md.lock` file.

### Server deployment

The current release targets a local CLI and agent workflows. It has not undergone security validation for deployment as a multi-tenant network service. TLS verification is enabled by default, and input, redirect, and final URLs are checked for allowed schemes and hosts.

## Testing and contributing

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile wx2md.py
```

The 56 tests cover conversion fidelity, archive recovery, image status, redirect restrictions, and CLI behavior. They use synthetic HTML, mocked network responses, and temporary directories, so they do not connect to WeChat.

The [cross-platform CI run](https://github.com/passerbied/wechat-md/actions/runs/34968971941) passed on Linux with Python 3.9, 3.12, and 3.14, and on macOS and Windows with Python 3.12. Windows skips the timezone-switching test that depends on `tzset`. CI runs offline tests and syntax checks.

A single public article was also tested on macOS on September 15, 2026: the body and 5/5 images were archived, every image and GIF frame passed an independent decode check, and a repeated run correctly skipped the complete archive. This one sample does not establish compatibility with every article or network environment.

Bug reports and contributions are welcome through [Issues](https://github.com/passerbied/wechat-md/issues). Include your environment, error output, and a reproducible page structure when possible. Do not submit cookies, personal data, or full copies of articles that you are not authorized to share.

## License

This project is available under the [MIT License](LICENSE).

Copyright in archived articles, images, and other third-party material remains with the respective rights holders. Using this tool does not grant additional redistribution rights.
