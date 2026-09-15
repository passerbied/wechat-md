# wechat-md

**让公众号文章进入 Codex 和你的本地知识库。**

把公开的微信公众号文章转成 Markdown，下载图片，保留来源，并记录归档是否完整。既可以在命令行独立运行，也可以作为 Codex skill 使用。

[![Tests](https://github.com/passerbied/wechat-md/actions/workflows/tests.yml/badge.svg)](https://github.com/passerbied/wechat-md/actions/workflows/tests.yml)

Python 3.9+ · 零第三方运行依赖 · [MIT License](LICENSE) · 当前版本 `1.1.0rc1`

## 为什么做这个项目

把公众号链接交给 Codex，希望它总结要点、比较观点或整理进知识库，却停在了“获取文章正文”这一步。`wechat-md` 就起于这样的实际使用问题。

手工粘贴正文能继续对话，但图片、代码格式和来源信息还得另外整理。下一次换个会话或工具，这份材料也未必能直接复用。

`wechat-md` 把这一步做成了一个可重复的流程：**提供已知的公开文章链接，在本机提取正文和图片，保存为带来源信息的本地资料，再交给 Codex 阅读和处理。**

```text
公众号链接 → wechat-md → Markdown + 本地图片 + 文章清单
                              ↓
                    Codex 阅读、总结 / 知识库归档
```

Codex 本身具有[网页搜索等能力](https://learn.chatgpt.com/docs/codex/cli)，具体链接的读取结果取决于所用工具、网络权限和页面访问状态。`wechat-md` 提供本地抓取方式，同样需要联网，也会受到验证页和访问限制影响。

## 你会得到什么

- **少做环境准备。** 运行脚本只需要 Python，无需安装第三方 Python 包、启动浏览器或配置 MCP 服务；抓取不需要 API Key，也不使用 Cookie。
- **可以长期使用的文件。** Markdown、图片和来源信息保存在自己的目录里，可以继续交给 Codex、其他 agent 或知识库工具处理。
- **明确的完成状态。** 完整归档、主动保留远程图片、图片未下载完整分别记录，缺失内容会在输出中提示。
- **可以重跑的归档流程。** 不同文章的同名标题不会互相覆盖；相同文章更新清单，文件丢失或图片下载失败后可再次抓取。

脚本负责提取和归档。总结、翻译、改写与图片理解由后续的 Codex 或其他工具完成。

## 快速开始

```bash
git clone https://github.com/passerbied/wechat-md.git
cd wechat-md

# 将示例 URL 替换为真实的公众号文章链接
python3 wx2md.py "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" -o ./archive
```

### 在 Codex 中使用

将项目目录作为一个完整 skill 安装，保持 `SKILL.md` 与 `wx2md.py` 在同一目录。macOS/Linux 用户可在项目根目录建立软链接：

```bash
mkdir -p "$HOME/.codex/skills"
ln -s "$PWD" "$HOME/.codex/skills/wechat-md"
```

如果目标位置已有同名 skill，请先检查已有安装。Windows 用户可将项目复制到自己的 skills 目录，文件夹命名为 `wechat-md`。

安装后可以这样提问：

> 用 $wechat-md 阅读这篇公众号文章，总结主要观点并保留原文链接。

> 用 $wechat-md 把这些文章保存到我指定的知识库目录，告诉我哪些图片未下载成功。

完整的 agent 工作流见 [SKILL.md](SKILL.md)。Codex 的账号与网络权限要求仍然适用。

### 批量归档与链接发现

```bash
# urls.txt 每行一个链接，支持空行、# 注释和 UTF-8 BOM
python3 wx2md.py -f urls.txt -o ./archive --delay 3

# 提取一篇文章正文中引用的其他公众号文章链接
python3 wx2md.py --discover "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" > urls.txt

# 只保存正文，主动保留远程图片
python3 wx2md.py "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" -o ./archive --no-images
```

`--discover` 只发现当前正文里的引用链接，不提供公众号完整历史列表。

## 与其他方案如何选择

已有工具各有侧重。如果你主要处理已知的公开公众号链接，希望用较少的环境依赖完成本地归档，可以从 `wechat-md` 开始。

| 方案 | 主要用途 | 使用前需要了解 |
|---|---|---|
| **wechat-md** | 已知公众号链接 → Markdown、图片和本地归档清单 | 单个 Python 脚本；不提供浏览器交互、视频解析或完整历史同步 |
| [baoyu-url-to-markdown](https://github.com/JimLiu/baoyu-skills/blob/main/skills/baoyu-url-to-markdown/SKILL.md) | 通用网页转 Markdown，支持浏览器交互等待和媒体下载 | 需要 Bun、相关依赖及 Chrome/CDP 环境；适合已经使用浏览器工作流或需要处理动态页面的场景 |
| [weixin-articles-mcp](https://github.com/jj-cheng25/weixin-articles-mcp#readme) | 向模型直接返回文章、原生图片内容块和视频关键帧 | 无需浏览器；需要安装包、配置 MCP，视频功能还需要 ffmpeg；文档以即时返回为目标，长期归档需另接保存流程 |
| [wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter#readme) | 曾提供公众号文章列表同步及多格式导出 | 维护者于 2026-07-30 公告，因所依赖的上游核心接口关闭而停止维护；使用或 fork 前应先阅读其维护公告 |

比较依据为各项目公开文档，核对日期：2026-09-15；未进行同环境成功率或速度对测。

`wechat-md` 不依赖历史列表同步接口，范围限定为用户提供的文章链接。它仍需适配公开文章页面的变化，也不承诺每篇文章都能抓取。

## 输出与完成状态

```text
archive/
  文章清单.csv
  <公众号>/articles/<日期>-<标题>-<文章ID>/
    <标题>.md
    images/
```

Markdown 包含标题、作者、发布日期、来源链接、抓取时间、文章 ID 和图片统计。图片使用相对路径引用，移动文章目录时可以一起保留。

| 状态 | 含义 |
|---|---|
| `complete` | 正文和已识别的可下载图片已保存 |
| `remote` | 指定了 `--no-images`，主动保留远程图片 |
| `partial` | 正文已保存，部分图片未本地化，输出中会给出原因 |

退出码：`0` 表示按所选模式完成或跳过已有归档，`1` 表示失败、部分完成或访问受阻，`2` 表示输入错误。

**再次运行同一列表即可检查并补抓不完整的归档。** 完整且未变化的本地文件会跳过；需要重新获取线上内容时使用 `--force`。之前指定过 `--no-images` 的文章，之后不带该参数运行会补抓图片。

<details>
<summary>归档标识、清单与旧版本兼容</summary>

- 长链接按 `__biz / mid / idx` 识别文章，短链接按路径识别。短长链接尚未自动关联，可能各保留一份。
- 清单使用 UTF-8 BOM，按文章 ID 更新，保存状态和 Markdown 内容哈希。`--force` 更新相同文章时保留已有路径。
- 旧版清单可继续读取，下次抓取会补齐状态与哈希。只有来源匹配的旧文件才会原位更新。
- 发布日期按 UTC+8 计算，保留能提取到的原始时间戳。字数统计为正文汉字数加英文词数，不计图片 URL。
- 文件与清单分别原子写入，中途失败可重跑恢复。旧图片不会自动删除。

</details>

## 参数

| 参数 | 说明 |
|---|---|
| `-o, --output DIR` | 输出目录，默认 `./wechat-md` |
| `-f, --file FILE` | 从文本文件读取链接 |
| `--no-images` | 主动保留远程图片 |
| `--image-workers N` | 图片并发 1–4，默认 2 |
| `--delay N` | 文章间隔，至少 2 秒，默认 2；发现模式同样适用 |
| `--timeout N` | 文章与图片单次请求超时，默认 30 秒 |
| `--retries N` | 暂时失败时最多尝试次数，包含首次；1–5，默认 3 |
| `--force` | 重新抓取并更新相同文章 |
| `--no-clean` | 保留普通文本节点中的 UI 噪音 |
| `--discover` | 只输出正文引用的文章链接 |
| `--insecure` | 关闭 TLS 证书验证，有连接被冒充的风险；默认不启用 |
| `-v, --verbose` | 输出退避与下载诊断信息 |
| `--version` | 显示版本 |

更多帮助：`python3 wx2md.py --help`。

## 支持范围与边界

- 支持标题、段落、链接、加粗、引用、嵌套列表、有序编号和简单表格。合并单元格表格会标注后按行展开，复杂网页排版不作完整还原。
- 代码块与行内代码独立处理，保留星号和中文空格；脚本、样式和明确隐藏的节点排除。
- 图片支持 PNG、JPEG、GIF、WebP 的基本文件特征检查；该检查不等于完整图片解码。SVG、AVIF 等其他格式保留远程引用并标记部分完成。
- 图片下载限于 `mmbiz.qpic.cn`、`mmbiz.qlogo.cn`、`wx.qlogo.cn`；范围外的图片保留远程引用并提示。HTML 上限 8 MiB，单张图片上限 20 MiB。
- 音视频只保留能取得的地址、标识或占位提示，不下载、不转写、不保证可播放。
- 不使用 Cookie、不登录、不绕过验证码；不提供私有内容、聊天记录或完整历史文章采集。

### 遇到验证页或限流

检测到验证页或 HTTP 401/403/429 后停止本批请求。归档模式写出 `待处理链接.txt`；发现模式将待处理链接输出到 stderr。图片并发中已经发出的请求可能仍会结束。不要连续重试或将异常直接归因于出口 IP。

### 输出目录提示被占用

同一目录一次只允许一个 CLI 进程写入。若进程曾被强行终止，确认没有其他任务使用该目录后，再移除遗留的 `.wx2md.lock`。

### 部署到服务端

当前面向本地 CLI 和 agent 工作流，未针对多租户网络服务部署做安全验证。默认启用 TLS 验证；输入、重定向和最终 URL 均检查协议及主机。

## 测试与贡献

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile wx2md.py
```

56 项测试覆盖转换保真、归档恢复、图片状态、重定向限制和命令行行为，使用合成 HTML、模拟网络响应和临时目录，无需连接微信。

[跨平台 CI](https://github.com/passerbied/wechat-md/actions/runs/34968971941) 已在 Linux / Python 3.9、3.12、3.14，以及 macOS、Windows / Python 3.12 上通过。Windows 跳过依赖 `tzset` 的时区切换用例。CI 覆盖离线测试和语法检查。

另于 2026-09-15 在 macOS 上完成一篇公开文章实测：正文与 5/5 张图片归档成功，图片及 GIF 全部帧经独立解码验证，重复运行正确跳过。单篇结果不代表所有文章或网络环境。

欢迎通过 [Issues](https://github.com/passerbied/wechat-md/issues) 反馈问题，或提交附带回归测试的改进。反馈时请说明运行环境、错误信息及可复现的页面结构；不要提交 Cookie、个人信息或未经授权的完整文章。

## 许可证

本项目采用 [MIT License](LICENSE)。

抓取的文章、图片及其他第三方内容，其权利仍属于原权利人，不因使用本工具而获得额外的再分发许可。
