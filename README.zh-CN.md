[English](README.md) | **中文文档**

# cyxj-agent-rec（命令 `rec`）

> cyxj-agent-rec 是给 AI 编程 agent 用的 macOS 后台终端录屏工具：agent 在一个独立的 tmux 服务里自己开终端、打字、按键、看屏幕，最后导出和 Terminal.app 真实窗口看不出区别的 MP4。全程不碰你的键盘、鼠标和窗口，它录它的，你照常用电脑。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/macOS-only-black)](#环境要求)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](#环境要求)
[![YouTube](https://img.shields.io/badge/YouTube-@cyxj__ai-red)](https://www.youtube.com/@cyxj_ai)

**v0.1.0 · 2026-10-29 更新 · 在 macOS 26.7.1、tmux 3.6a、asciinema 3.2.1、ffmpeg 8.1、Claude Code 2.1.312 上测试** · [更新记录](CHANGELOG.md)

<p align="center"><img src="docs/demo.gif" width="720" alt="cyxj-agent-rec：agent 在后台终端里给 Claude Code 打字提问，导出画面和 macOS 终端窗口一样"></p>

<sub>这段就是 Claude Code 自己用 `rec start` → `rec type` → `rec wait --claude` → `rec export` 录的，录的时候人在别的窗口照常干活。</sub>

---

## 它做什么

agent 本来就能跑命令，但没法把过程「拍」下来：系统录屏要窗口在最前面、占着整块屏幕；脚本式录屏要提前把每一步写死。`rec` 给 agent 一个能一步步操作的终端，外加一台一直开着的摄像机：

- **操作**：`rec type` 像人一样一个字一个字打（默认 30 字/秒，中文直接打），`rec key` 按键（`Enter`、`Down*3`、`S-Tab`、`C-c` …），`rec paste` 括号粘贴，多行不会被回车拆开。
- **看**：`rec screen` 把当前屏幕打成文字；`rec shot` 渲染成 PNG；`rec wait` 等某段字出现 / 消失、画面静止，或者加 `--claude` 等 Claude Code 弹选择题、跑完这一轮。
- **录**：每个字节都由 asciinema 录下；`rec mark` 打时间点。
- **导出**：`rec export` 重放录像，用苹果自己的排字引擎 CoreText（Terminal.app 用的就是它）逐帧画，原始像素直接喂 ffmpeg。默认 4K、真彩色，不经过 GIF。
- **不打扰人**：全部走独立的 tmux 服务（`tmux -L agent-rec`），不发系统按键、不动鼠标、不改任何窗口。想旁观就在自己终端里 `rec attach <名字>`（只读）。

<p align="center"><img src="docs/screenshot.png" width="720" alt="rec shot：Retina 两倍图，Terminal.app Basic 描述文件 + macOS 26 窗口框"></p>

## 和其他录法比

| | macOS 系统录屏 | [asciinema](https://asciinema.org) + 转 GIF | [VHS](https://github.com/charmbracelet/vhs) | cyxj-agent-rec |
|---|---|---|---|---|
| 谁在操作终端 | 人 | 人 | 提前写好的 `.tape` 脚本 | agent，看着屏幕一步步来 |
| 窗口要不要在前台 | 要 | 要（录的是你的终端） | 不要 | 不要，独立 tmux 服务，屏幕上什么都不出现 |
| 产出 | 真实像素 | 文字录像 → GIF | GIF / MP4 / WebM | MP4（默认 4K）+ 章节清单 + PNG 单帧 |
| 像不像 Terminal.app | 就是它 | 自己的画法 | 自己的画法 | 像：CoreText + Terminal.app 的格子规则和窗口框 |
| 长时间等待 | 手动剪 | 空闲时长上限 | 脚本写死 | 空闲压缩 + 转圈加速，并记下原始时间方便剪辑对回去 |

## 环境要求

- macOS（画字用 PyObjC 调 CoreText；在 macOS 26.7.1 上测试）
- Python 3.11+
- `tmux`、`asciinema`、`ffmpeg`：`brew install tmux asciinema ffmpeg`
- 要用 `rec win`（图形窗口录屏）：Swift 工具链（Xcode 或 Command Line Tools），终端 App 有「屏幕录制」权限

## 快速开始

```sh
git clone https://github.com/chenyuxiaojin/cyxj-agent-rec.git
cd cyxj-agent-rec
uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -e .
ln -sf "$PWD/.venv/bin/rec" ~/.local/bin/rec        # 或者 PATH 里任意位置
```

要用 `-e`（可编辑安装）：`rec win` 会到克隆目录里的 `capture/.build/release/rec-capture` 找 Swift 小程序。

录一段 Claude Code 干活：

```sh
rec start demo --cwd ~/code/demo -- claude
rec wait demo --claude                    # 等它起来（或弹出信任文件夹的选择题）
rec key demo Down Enter                   # 选「信任」
rec mark demo "提问"                       # 时间点 → 导出时变章节
rec type demo "写一个 squares.py,打印 1 到 5 的平方,然后运行" --enter
rec wait demo --claude --timeout 600      # 弹选择题/确认框、或画面静止 3 秒就返回，顺带打印屏幕
rec stop demo
rec export demo --from 提问 --speedup -o ~/Movies/demo.mp4
```

录像和导出默认放 `~/.agent-rec/<名字>/`（`AGENT_REC_HOME` 可改）。

## 命令

| 命令 | 作用 |
|---|---|
| `start <名字> [--size 140x34] [--cwd] [--env K=V] [--theme] -- <命令>` | 后台开终端并开始录 |
| `type <名字> "文字" [--enter] [--cps 30] [--instant]` | 打字，中文直接打 |
| `key <名字> Enter Down Esc S-Tab C-c …` | 按键；`Down*3` = 连按 3 次 |
| `paste <名字> "多行文字"` / `--file` | 括号粘贴，多行不会被回车拆开 |
| `screen <名字> [--json] [--ansi]` | 看屏幕文字 |
| `wait <名字> --until 字 / --regex / --gone 字 / --idle 秒 / --claude [--timeout 300]` | 等条件成立；超时退出码 2 |
| `mark <名字> "标签"` | 打时间点 |
| `stop <名字>` | 结束并收好录像 |
| `export <名字或 .cast> [--speedup] [--badge] [--fit 3840x2160] [--from/--to 秒或标记]` | 出 MP4 + `<名字>.章节.txt` |
| `shot <名字或 .cast> [--at 秒]` | 渲染一张 PNG |
| `attach <名字>` | 人在自己的终端里旁观（只读） |
| `list` / `rm <名字>` | 列出 / 删除会话 |
| `win list / shot / start` | 按窗口 / 按 App 真实录屏（见下文） |
| `notify <事件名>` | 给 App 发一条系统分布式通知（App 自己约定的开关） |

## 导出效果

- **和真实 Terminal.app 对得上**：字用 CoreText 画，格子照 Terminal.app 在 SF Mono 11pt 下的规则（字宽 7pt、行高 14pt、基线在格子顶下 11pt、中文等宽字符靠左、字体平滑开着）。窗口框（标题栏、红黄绿灯、圆角、文件夹图标、标题格式「目录 — 标题 — 程序 — 列×行」）照 macOS 26 的 Terminal.app 量。对照真实截图，正文逐像素平均误差低于 0.2/255，是截图本身的噪声级别。`--no-frame` 去掉窗口框。
- **让程序以为自己在 Terminal.app 里**：开会话时告诉里面的程序「底色是白的」（OSC 11），Claude Code 的 auto 主题就会选浅色；还会去掉 tmux 痕迹（`TMUX`、`TERM_PROGRAM=tmux`），Claude Code 用真彩色、logo 也按 Terminal.app 的画法。
- **空闲压缩**（默认开）：画面完全不动超过 2 秒的地方压成 2 秒。
- **转圈加速**（`--speedup`）：只有一到三行在变（转圈、计时器）、且持续超过 4 秒的等待，头 1 秒、尾 0.5 秒原速，中间 8 倍，再长就压到 6 秒内。打字、按键前后不会被加速。`--badge` 在加速段右上角显示「▶▶ 8×」。
- **章节**：`mark` 打的点写进 MP4 章节，另出一份 `<名字>.章节.txt`（成片时间 + 哪些段被压缩过、原始时间是多少，方便剪辑对回去）。
- **旧录像重新渲染**：`rec export 某某.cast --theme terminal-dark`。主题：`terminal`（Basic 浅色）、`terminal-dark`、`monokai`。
- `rec shot` 默认字号 22 = 11pt 的 Retina 两倍图，和真实终端截图一样大。

## 图形窗口：真实录屏（`rec win`）

终端以外的东西（菜单栏 App、桌面 App、浏览器）用这个。这是 ScreenCaptureKit 录的**真实像素**，但只抓指定的窗口 / App，屏幕上别的窗口不进画面；不动鼠标、不抢焦点。

```sh
cd capture && swift build -c release && cd ..   # 第一次编译
rec win list                                    # 屏幕上有哪些窗口
rec win shot --app Chrome -o a.png              # 截一张看看
rec win start web --app Chrome                  # 录 Chrome 最大的那个窗口（被挡住也照样录）
rec win start panel --app 某App --desktop        # 这个 App + 壁纸 + 菜单栏，其它窗口都不要
rec mark web "第一步"; rec stop web; rec export web
```

- 系统录出来是可变帧率（画面不动不出帧），导出时转成恒定 30 帧，尾巴按 `rec stop` 的时刻补齐。
- 台前调度收在侧边的窗口，录到的是缩略图大小；要录正常大小，窗口得在台上。
- 要鼠标悬停才出来的界面，需要 App 自己留一个开关，`rec notify <事件名>` 就是发这个开关的。例如 [cyxj-notch（刘海台）](https://github.com/chenyuxiaojin/cyxj-notch) 收到 `com.xiaochen.notchdesk.open` 就展开面板。

## 源码结构

```
agentrec/session.py   独立 tmux 服务 + asciinema；打字、按键、粘贴、看屏幕、标记、停止
agentrec/term.py      终端模拟（pyte），补了 dim、过滤私有序列；读 asciicast v2/v3
agentrec/render.py    CoreText 画格子、配色主题、macOS 26 终端窗口框
agentrec/export.py    重放 → 时间轴（空闲压缩 / 转圈加速）→ 逐帧画 → 原始像素喂 ffmpeg
agentrec/cli.py       命令行
agentrec/window.py    rec win，调 Swift 小程序
capture/              rec-capture：ScreenCaptureKit 录屏（Swift）
tests/                单元测试
```

测试：`.venv/bin/python -m unittest discover -s tests`（7 个，全部通过）。

## 常见问题

**怎么让 AI agent 自己录一段终端演示视频？**
给它装上 `rec`。agent 用 `rec start` 开任意命令（Claude Code、Codex、shell），用 `rec type` / `rec key` 操作，用 `rec screen` 或 `rec wait` 看进度，最后 `rec stop`、`rec export`。每一步都是普通的命令行，能跑命令的 agent 都能用。

**怎么在 Mac 上后台录 Claude Code？**
`rec start demo -- claude`，每次提问后 `rec wait demo --claude`。`--claude` 会在 Claude Code 弹出选择题 / 确认框、或画面静止 3 秒时返回（它干活时转圈和计时器一直在动）。

**这算真实录屏吗？**
终端部分严格说不是：录的是程序输出的字和时间，再照 Terminal.app 的规则逐帧重画。内容 100% 真实，画面是忠实重画，所以窗口根本不用出现在屏幕上。图形 App 用 `rec win`，那是真实像素。

**会往我的窗口里打字、动我的鼠标吗？**
不会。按键只发给独立的 tmux 服务；`rec win` 通过 ScreenCaptureKit 读像素，从不发输入。

**Linux / Windows 能用吗？**
不能。导出依赖 CoreText，`rec win` 依赖 ScreenCaptureKit，都只有 macOS 有。

## 给 AI 编程助手

[`llms.txt`](llms.txt) 列出了值得读的文件，每个一行。`rec -h`、`rec <命令> -h` 能看到全部参数。

## 关于

作者 [@cyxj_ai（陈与小金）](https://www.youtube.com/@cyxj_ai)，非程序员，用 Claude Code 做工具。更多项目：[cyxj-notch](https://github.com/chenyuxiaojin/cyxj-notch) · [cyxj-groksearch](https://github.com/chenyuxiaojin/cyxj-groksearch) · [cyxj-hyperframes](https://github.com/chenyuxiaojin/cyxj-hyperframes)

## 许可

[MIT](LICENSE)
