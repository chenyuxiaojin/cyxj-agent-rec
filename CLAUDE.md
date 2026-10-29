# CLAUDE.md

`rec`:给 agent 用的后台终端录屏工具。用法看 README.md 或 `rec -h`。

## 结构

| 文件 | 管什么 |
|---|---|
| `agentrec/session.py` | 会话:独立 tmux 服务(`-L agent-rec`,配置 `agentrec/tmux.conf`)+ asciinema 录 `rec.cast`;打字、按键、粘贴、截屏文字、标记、停止 |
| `agentrec/term.py` | 终端模拟(pyte):补了 dim、剔除 `\e[>…` `\e[<…` 私有序列;读 asciicast v2/v3 |
| `agentrec/render.py` | 画格子:CoreText(pyobjc)按 Terminal.app 规则画字,字体回退交给系统;配色主题;macOS 26 Terminal 窗口框(`assets/corner@2x.png` 是真实窗口的圆角) |
| `agentrec/export.py` | 重放采样 → 时间轴(空闲压缩/转圈加速)→ 逐帧画 → 原始像素直接喂 ffmpeg |
| `agentrec/cli.py` | 命令行 |
| `agentrec/window.py` + `capture/`(Swift) | `rec win`:ScreenCaptureKit 按窗口 / 按 App 真实录屏(`rec-capture` 二进制,`swift build -c release`) |

测试:`.venv/bin/python -m unittest discover -s tests`

## 坑(都踩过)

- tmux 3.6a 在配置文件里写 `set -g window-size manual` 会让服务直接崩(server exited unexpectedly);改在会话建好后 `resize-window`,它会自动设成 manual。
- tmux 把**以分号结尾的参数**当命令分隔符,`send-keys -l ";"` 会被吞掉,要转成 `\;`(`session._lit`)。
- 指向窗格要写 `=名字:`(带冒号);`=名字` 只能用在 has-session / kill-session。
- 不去掉 `TMUX` 环境变量,Claude Code 会降成 256 色(logo 变粉);`TERM` 改成 `xterm-256color`、带上外面的 `TERM_PROGRAM`,输出才和真实 Terminal.app 一致。
- Claude Code auto 主题靠问终端底色(OSC 11)判断深浅;tmux 会按窗口的 `window-style` 回答,所以开会话前先设好 `window-style`(用 `wait-for` 卡住,设完再放行 asciinema)。
- tmux 会把窗格里的 `TERM_PROGRAM` 改成 `tmux`(`new-session -e` 传进去也会被盖掉),要在内层命令里再 export;不然 Claude Code 认不出 Terminal.app,logo 会换成带黑底的方块字版本。
- 和真实终端对像素的办法:`rec win shot --window-id <你的终端窗口>` 截图 + AppleScript `contents of selected tab` 拿同一屏的字,只比黑字行。截图是 Display P3 的数值,比颜色前先转 sRGB(Claude 橙 215,119,87 在截图里是 202,124,94,其实是同一个颜色)。
- Terminal.app 的格子规则(11pt SF Mono):字宽 6.80 四舍五入成 7pt;行高 = 上沿 10.47 进位 11 + 下沿 2.65 进位 3 = 14pt;基线在格子顶下 11pt;中文等宽字符靠左画、不居中;字体平滑开着(关掉明显偏细)。框线、方块字符都是字体里的字形,不用自己画。
- 只换 Pillow 画字时误差 6.7/255(中文偏右 3 像素、笔画偏细、行高 27 而不是 28),换 CoreText 后 0.12。
- pyte 原版会把 `\e[>4;2m`(modifyOtherKeys)当成下划线,把 `\e[<u` 的 `u` 当字打出来。
- Claude Code 2.1.3xx 的转圈行已经没有「esc to interrupt」,判断「跑完」靠画面静止(转圈和计时器一直在动)。
- ScreenCaptureKit 画面不变就不出帧,录出来是可变帧率、尾巴会短;导出时 `tpad` 补到 `rec stop` 的时刻并转恒定帧率。
- `--desktop` 用「按窗口列表」过滤(不是按 App):菜单栏 `Menubar` 窗口没有所属 App,按 App 过滤会漏掉。
- 台前调度侧边栏里的窗口,`desktopIndependentWindow` 录到的是缩略图尺寸。
