"""rec:给 agent 用的后台终端录屏工具。rec -h 看全部命令。"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import session as S
from . import window as W

EPILOG = """\
典型流程(agent 一边操作一边录,人照常用电脑):
  rec start demo --cwd ~/code/mod-demo -- claude --settings '{"env":{"CLAUDE_CODE_PLUGIN_DIRS":""}}'
  rec wait demo --idle 3                   # 等 Claude Code 起来
  rec type demo "做一个文件树 mod" --enter   # 像人一样一个字一个字打,再回车
  rec mark demo "开始做文件树"               # 打时间点,导出时变成章节
  rec wait demo --claude --timeout 600     # 等它弹选择题或这一轮跑完,返回时顺带打印屏幕
  rec key demo Down Enter                  # 选第二项
  rec screen demo                          # 随时看屏幕文字
  rec shot demo                            # 当前画面渲染成 PNG(看效果用)
  rec stop demo
  rec export demo --speedup -o ~/Movies/文件树.mp4

人想旁观:在自己的终端里 rec attach demo(只读,不会抢输入)
"""

CLAUDE_PROMPT = ["Enter to select", "Enter to confirm", "Esc to cancel", "Do you want to"]


def _p(*a):
    print(*a, flush=True)


def cmd_start(a):
    cols, rows = _size(a.size)
    meta = S.start(a.name, a.cmd, cols, rows, a.cwd, a.env, a.theme, as_terminal=not a.plain_term)
    _p(f"已开会话「{a.name}」({cols}x{rows}),正在录:{meta['cast']}")
    _p(f"  命令:{' '.join(meta['cmd'])}")
    _p(f"  人想旁观:rec attach {a.name}")


def _size(s):
    m = re.fullmatch(r"(\d+)[xX×](\d+)", s)
    if not m:
        raise SystemExit("--size 写成 140x34 这样")
    return int(m.group(1)), int(m.group(2))


def cmd_type(a):
    text = a.text if a.text is not None else sys.stdin.read()
    S.send_text(a.name, text, 0 if a.instant else a.cps, a.enter)
    _p(f"已输入 {len(text)} 个字" + (" + 回车" if a.enter else ""))


def cmd_key(a):
    seq = S.send_keys(a.name, a.keys, a.delay / 1000)
    _p("已按:" + " ".join(seq))


def cmd_paste(a):
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = a.text if a.text is not None else sys.stdin.read()
    S.paste(a.name, text)
    _p(f"已粘贴 {len(text)} 个字")


def _trim(text):
    return "\n".join(line.rstrip() for line in text.rstrip("\n").split("\n")).rstrip("\n")


def cmd_screen(a):
    text = S.capture(a.name, ansi=a.ansi)
    if a.json:
        info = S.pane_info(a.name) or {}
        _p(json.dumps({"alive": not info.get("dead", True), "exit_status": info.get("status"),
                       "cursor": info.get("cursor"), "lines": text.rstrip("\n").split("\n")},
                      ensure_ascii=False))
        return
    _p(text if a.ansi else _trim(text))
    info = S.pane_info(a.name)
    if info and info["dead"]:
        _p(f"\n[程序已退出,退出码 {info['status']}]")


def cmd_wait(a):
    until = list(a.until or [])
    regex = [re.compile(r) for r in (a.regex or [])]
    gone = list(a.gone or [])
    idle = a.idle
    prompts = []
    if a.claude:
        prompts = CLAUDE_PROMPT
        idle = idle or 3.0
    if not (until or regex or gone or idle or prompts):
        raise SystemExit("至少给一个条件:--until / --regex / --gone / --idle / --claude")
    deadline = time.time() + a.timeout
    last, stable_since = None, time.time()
    result = None
    while True:
        if not S.is_running(a.name):
            raise SystemExit(f"会话「{a.name}」没在运行")
        text = S.capture(a.name)
        now = time.time()
        hit = next((u for u in until if u in text), None) or next((r.pattern for r in regex if r.search(text)), None)
        if hit:
            result = f"出现了:{hit}"
        elif prompts and any(p in text for p in prompts):
            result = "prompt(有选择题 / 确认框在等你)"
        elif gone and all(g not in text for g in gone):
            result = "已消失:" + " / ".join(gone)
        if text != last:
            last, stable_since = text, now
        elif idle and now - stable_since >= idle and not result:
            result = f"idle(画面 {idle:g} 秒没变)"
        info = S.pane_info(a.name)
        if not result and info and info["dead"]:
            result = f"exited(程序已退出,退出码 {info['status']})"
        if result:
            _p(f"[{result}]")
            if not a.quiet:
                _p(_trim(text))
            return
        if now > deadline:
            _p(f"[timeout:{a.timeout:g} 秒内没等到]")
            if not a.quiet:
                _p(_trim(text))
            sys.exit(2)
        time.sleep(a.interval)


def cmd_mark(a):
    t = S.mark(a.name, a.label)
    m, s = divmod(t, 60)
    _p(f"已打标记 {int(m):02d}:{s:04.1f} {a.label}")


def cmd_stop(a):
    if S.load_meta(a.name).get("kind") == "window":
        meta = W.stop(a.name)
        _p(f"已停止「{a.name}」,录像:{meta['video']}")
        _p(f"导出:rec export {a.name} [-o 文件.mp4]")
        return
    meta = S.stop(a.name)
    _p(f"已停止「{a.name}」,录像:{meta['cast']}")
    _p(f"导出:rec export {a.name} [--speedup] [-o 文件.mp4]")


def _running(name):
    return S.is_running(name) or W.is_running(name)


def cmd_list(a):
    rows = S.list_sessions()
    if not rows:
        _p("(还没有会话)")
        return
    for m in rows:
        win = m.get("kind") == "window"
        state = "录制中" if (W.is_running(m["name"]) if win else m["running"]) else "已停止"
        start = time.strftime("%m-%d %H:%M", time.localtime(m["created"]))
        what = "窗口 " + " ".join(m["target"]) if win else f"终端 {m['cols']}x{m['rows']}  {' '.join(m['cmd'])}"
        _p(f"{m['name']:<20} {state}  {start}  {what[:70]}")


def cmd_attach(a):
    if not S.is_running(a.name):
        raise SystemExit(f"会话「{a.name}」没在运行")
    args = ["tmux", "-u", "-L", S.SOCKET, "attach", "-t", "=" + S.tname(a.name)]
    if not a.control:
        args.append("-r")
    os.execvp("tmux", args)


def cmd_rm(a):
    if _running(a.name):
        raise SystemExit(f"「{a.name}」还在录,先 rec stop {a.name}")
    d = S.sdir(a.name)
    if not os.path.isdir(d):
        raise SystemExit(f"没有「{a.name}」")
    shutil.rmtree(d)
    _p(f"已删除 {d}")


def _source(name_or_cast):
    """会话名 → (cast, meta, 标记, 操作区间);也可以直接给 .cast 文件。"""
    if name_or_cast.endswith(".cast") and os.path.exists(name_or_cast):
        return os.path.abspath(name_or_cast), {}, [], []
    meta = S.load_meta(name_or_cast)
    t0 = meta.get("t0", meta["created"])
    marks = [{"t": m["t"], "label": m["label"]} for m in S.read_jsonl(name_or_cast, "marks.jsonl")]
    acts = [(x["start"] - t0, x["end"] - t0 + 1.5) for x in S.read_jsonl(name_or_cast, "actions.jsonl")]
    return meta["cast"], meta, marks, acts


def _proc(meta):
    """标题栏里「正在跑的程序」:会话启动的命令名(没写命令就是 shell)。"""
    if not meta:
        return None
    cmd = meta.get("cmd") or [os.environ.get("SHELL", "zsh")]
    return os.path.basename(cmd[0])


def cmd_export(a):
    from .export import export
    if not a.name.endswith(".cast") and S.load_meta(a.name).get("kind") == "window":
        meta = S.load_meta(a.name)
        if W.is_running(a.name):
            raise SystemExit(f"「{a.name}」还在录;先 rec stop {a.name}")
        out = os.path.abspath(os.path.expanduser(a.output or os.path.join(S.sdir(a.name), f"{a.name}.mp4")))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        marks = [{"t": m["t"], "label": m["label"]} for m in S.read_jsonl(a.name, "marks.jsonl")]
        out, side = W.export(meta, out, marks, a.start_at, a.end_at, log=_p)
        _p(f"成片:{out}")
        _p(f"章节清单:{side}")
        return
    cast, meta, marks, acts = _source(a.name)
    if meta and S.is_running(a.name) and not a.live:
        raise SystemExit(f"「{a.name}」还在录;先 rec stop {a.name},或加 --live 导出到目前为止的部分")
    out = a.output or os.path.splitext(cast)[0] + ".mp4"
    if meta and not a.output:
        out = os.path.join(S.sdir(a.name), f"{a.name}.mp4")
    out = os.path.abspath(os.path.expanduser(out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    speed = None
    if a.speedup:
        speed = {"factor": a.speedup_factor, "min_wait": a.speedup_min, "max_len": a.speedup_max,
                 "max_rows": a.speedup_rows}
    theme = a.theme or meta.get("theme", "terminal")
    res = export(cast, out, marks=marks, actions=acts, stop_at=meta.get("stop_at"), fps=a.fps,
                 fit=_size(a.fit) if not a.font_size else None, font_size=a.font_size, family=a.font,
                 font_path=a.font_file, theme=theme, frame=not a.no_frame, title=a.title,
                 cwd=meta.get("cwd"), proc=_proc(meta), t_from=a.start_at, t_to=a.end_at, idle_limit=a.idle_limit, speedup=speed, badge=a.badge,
                 cursor=not a.no_cursor, crf=a.crf, encoder=a.encoder, log=_p)
    _p(f"成片:{res['out']}")
    _p(f"章节清单:{res['chapters_file']}")


def cmd_shot(a):
    from .export import snapshot
    cast, meta, _, _ = _source(a.name)
    out = a.output or os.path.join(os.path.dirname(cast), f"shot-{time.strftime('%H%M%S')}.png")
    out = os.path.abspath(os.path.expanduser(out))
    if meta and S.is_running(a.name) and a.at is None:
        # 录制中:用 tmux 现在的画面,不受录像文件写入延迟影响
        from .term import Terminal
        from .render import Layout, Renderer
        text = S.capture(a.name, ansi=True).rstrip("\n")
        term = Terminal(meta["cols"], meta["rows"])
        term.feed(text.replace("\n", "\r\n"))
        r = Renderer(a.font_size, a.font, a.theme or meta.get("theme", "terminal"), a.font_file)
        lay = Layout(r, meta["cols"], meta["rows"], frame=not a.no_frame, cwd=meta.get("cwd"), proc=_proc(meta))
        img = lay.base.copy()
        for y in range(meta["rows"]):
            img.paste(r.render_row(term.row(y)), lay.row_pos(y))
        info = S.pane_info(a.name) or {}
        cx, cy = info.get("cursor", (0, 0))
        if info.get("cursor_visible") and cy < meta["rows"] and cx < meta["cols"]:
            cells = term.row(cy)
            wide = cx + 1 < meta["cols"] and cells[cx + 1].data == ""
            x0, y0 = lay.row_pos(cy)
            img.paste(r.render_cursor(cells[cx], wide), (x0 + cx * r.cw, y0))
        if not a.no_frame:
            ttl = a.title if a.title is not None else (info.get("title") or a.name)
            img.paste(lay.title_image(ttl), lay.title_pos())
        img.save(out)
        _p(out)
        return
    path, t = snapshot(cast, out, a.at, font_size=a.font_size, family=a.font, font_path=a.font_file,
                       theme=a.theme or meta.get("theme", "terminal"), frame=not a.no_frame, title=a.title,
                       cwd=meta.get("cwd"), proc=_proc(meta))
    _p(f"{path}(第 {t:.1f} 秒)")


def build():
    p = argparse.ArgumentParser(prog="rec", description="给 agent 用的后台终端录屏:看得见、录得下、不打扰人。",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True, metavar="命令")

    s = sub.add_parser("start", help="后台开一个终端并开始录(不弹窗、不抢焦点)")
    s.add_argument("name")
    s.add_argument("--size", default="140x34", help="终端大小,默认 140x34")
    s.add_argument("--cwd", help="工作目录,默认当前目录")
    s.add_argument("--env", action="append", default=[], metavar="K=V", help="额外环境变量,可重复")
    s.add_argument("--theme", default="terminal", choices=["terminal", "terminal-dark", "monokai"],
                   help="底色(会告诉里面的程序是深色还是浅色),默认 terminal = 白底")
    s.add_argument("--plain-term", action="store_true", help="不冒充当前终端(默认会带上 TERM_PROGRAM)")
    s.add_argument("cmd", nargs="*", help="-- 后面跟要跑的命令;不写就开默认 shell")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("type", help="打字(默认一个字一个字打,像人在输入)")
    s.add_argument("name")
    s.add_argument("text", nargs="?", help="要打的字;不写就从标准输入读")
    s.add_argument("--enter", action="store_true", help="打完停 0.5 秒再回车")
    s.add_argument("--cps", type=float, default=30, help="每秒几个字,默认 30")
    s.add_argument("--instant", action="store_true", help="一次性打进去,不做打字效果")
    s.set_defaults(func=cmd_type)

    s = sub.add_parser("key", help="按键:Enter Down Up Esc Tab S-Tab C-c Space BSpace…;Down*3 = 连按 3 次")
    s.add_argument("name")
    s.add_argument("keys", nargs="+")
    s.add_argument("--delay", type=float, default=150, help="两次按键间隔毫秒,默认 150")
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("paste", help="整段粘贴(括号粘贴,多行不会被回车拆开)")
    s.add_argument("name")
    s.add_argument("text", nargs="?")
    s.add_argument("--file", help="从文件读")
    s.set_defaults(func=cmd_paste)

    s = sub.add_parser("screen", help="看当前屏幕文字")
    s.add_argument("name")
    s.add_argument("--ansi", action="store_true", help="带颜色转义")
    s.add_argument("--json", action="store_true", help="JSON:是否还活着、光标、每行文字")
    s.set_defaults(func=cmd_screen)

    s = sub.add_parser("wait", help="等到屏幕出现/消失某段字、或画面静止;返回时打印屏幕")
    s.add_argument("name")
    s.add_argument("--until", action="append", help="出现这段字就返回(可重复,任一命中)")
    s.add_argument("--regex", action="append", help="正则命中就返回")
    s.add_argument("--gone", action="append", help="这些字都消失了就返回")
    s.add_argument("--idle", type=float, help="画面连续 N 秒没变就返回")
    s.add_argument("--claude", action="store_true",
                   help="Claude Code 预设:弹出选择题/确认框,或画面静止 3 秒(这一轮跑完)就返回")
    s.add_argument("--timeout", type=float, default=300, help="最多等几秒,默认 300;超时退出码 2")
    s.add_argument("--interval", type=float, default=0.4)
    s.add_argument("-q", "--quiet", action="store_true", help="返回时不打印屏幕")
    s.set_defaults(func=cmd_wait)

    s = sub.add_parser("mark", help="打时间点(导出时变成章节)")
    s.add_argument("name")
    s.add_argument("label")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("stop", help="结束会话,收好录像")
    s.add_argument("name")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("list", help="列出所有会话")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("attach", help="人在自己的终端里旁观(默认只读)")
    s.add_argument("name")
    s.add_argument("--control", action="store_true", help="可操作(不加就只读)")
    s.set_defaults(func=cmd_attach)

    s = sub.add_parser("rm", help="删除一个已停止的会话和它的录像")
    s.add_argument("name")
    s.set_defaults(func=cmd_rm)

    def look(s):
        s.add_argument("--theme", choices=["terminal", "terminal-dark", "monokai"],
                       help="配色,默认跟开会话时一致(terminal = Terminal.app 白底)")
        s.add_argument("--font", default="SF Mono", help="SF Mono(默认)或 Menlo")
        s.add_argument("--font-file", help="直接指定字体文件")
        s.add_argument("--no-frame", action="store_true", help="不画 macOS 窗口框")
        s.add_argument("--title", help="窗口标题,默认用程序自己设的标题")

    s = sub.add_parser("export", help="导出 MP4(直接出,不经过 GIF)+ 章节清单")
    s.add_argument("name", help="会话名,或直接给一个 .cast 文件")
    s.add_argument("-o", "--output")
    s.add_argument("--fit", default="3840x2160", help="画面尺寸,字号自动取能放下的最大值;默认 3840x2160")
    s.add_argument("--font-size", type=int, help="固定字号(像素),画面尺寸随之而定")
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--from", dest="start_at", help="从第几秒或哪个标记开始")
    s.add_argument("--to", dest="end_at", help="到第几秒或哪个标记结束(默认到 rec stop 那一刻)")
    s.add_argument("--idle-limit", type=float, default=2.0, help="画面完全不动超过这么多秒就压短,0 = 不压;默认 2")
    s.add_argument("--speedup", action="store_true", help="自动加速长时间转圈等待的段落")
    s.add_argument("--speedup-factor", type=float, default=8, help="加速倍数,默认 8")
    s.add_argument("--speedup-min", type=float, default=4, help="等待超过几秒才加速,默认 4")
    s.add_argument("--speedup-max", type=float, default=6, help="每段加速后最多留几秒,0 = 不限;默认 6")
    s.add_argument("--speedup-rows", type=int, default=3, help="只有不超过几行在变才算「在等」,默认 3")
    s.add_argument("--badge", action="store_true", help="加速段右上角显示「▶▶ 8×」")
    s.add_argument("--no-cursor", action="store_true")
    s.add_argument("--crf", type=int, default=18, help="x264 画质,越小越清楚,默认 18")
    s.add_argument("--encoder", choices=["x264", "videotoolbox"], default="x264")
    s.add_argument("--live", action="store_true", help="会话还在录也导出(到目前为止)")
    look(s)
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("shot", help="把画面渲染成 PNG(录制中 = 当前画面;已停止 = 最后一刻或 --at 秒)")
    s.add_argument("name", help="会话名,或 .cast 文件")
    s.add_argument("-o", "--output")
    s.add_argument("--at", type=float, help="第几秒")
    s.add_argument("--font-size", type=int, default=22, help="字号(像素);22 = 11pt 的 Retina 两倍图,和真实终端一样大")
    look(s)
    s.set_defaults(func=cmd_shot)

    # ---- 图形窗口(真实录屏) ----
    s = sub.add_parser("win", help="图形窗口真实录屏:只录指定窗口 / App,别的窗口不进画面(rec win -h)")
    wsub = s.add_subparsers(dest="wcmd", required=True, metavar="子命令")

    def target(s):
        s.add_argument("--app", help="App 名字或 bundle id 里的字,如 刘海台、Chrome")
        s.add_argument("--window-id", type=int, help="窗口编号(rec win list 里第一列)")
        s.add_argument("--title", help="窗口标题里的字")
        s.add_argument("--desktop", action="store_true",
                       help="录这个 App 的全部窗口 + 壁纸 + 菜单栏(刘海台这类贴在屏幕上的面板用)")
        s.add_argument("--with", dest="with_apps", action="append", default=[], metavar="APP",
                       help="再加上别的 App 的窗口,可重复")
        s.add_argument("--region", help="只录这块区域 x,y,w,h(点,屏幕左上角为原点);默认取这个 App 窗口的范围")
        s.add_argument("--pad", type=float, help="区域四周多留几个点")

    w = wsub.add_parser("list", help="列出屏幕上的窗口")
    w.add_argument("--all", action="store_true", help="连隐藏的、系统的也列出来")
    w.set_defaults(func=cmd_win_list)

    w = wsub.add_parser("start", help="开始录(后台录,不动鼠标键盘);rec stop 结束")
    w.add_argument("name")
    target(w)
    w.add_argument("--fps", type=int, default=30)
    w.add_argument("--cursor", action="store_true", help="把鼠标指针也录进去")
    w.set_defaults(func=cmd_win_start)

    w = wsub.add_parser("shot", help="截一张(看窗口现在长什么样)")
    target(w)
    w.add_argument("-o", "--output")
    w.set_defaults(func=cmd_win_shot)

    s = sub.add_parser("notify", help="给 App 发一条系统通知(App 自己约定的开关,比如让刘海台展开)")
    s.add_argument("event")
    s.set_defaults(func=lambda a: (W.notify(a.event), _p(f"已发送:{a.event}")))
    return p


def _targ(a):
    return W.target_args(a.app, a.window_id, a.title, a.desktop, a.with_apps, a.region, a.pad)


def cmd_win_list(a):
    for r in W.list_windows(a.all):
        x, y, w, h = r["frame"]
        state = "" if r["onScreen"] else "(隐藏)"
        _p(f"{r['id']:>6}  {r['app'][:16]:<16} {int(w):>5}x{int(h):<5} @{int(x)},{int(y)}  {r['title'][:60]}{state}")


def cmd_win_start(a):
    meta = W.start(a.name, _targ(a), a.fps, a.cursor)
    with open(os.path.join(S.sdir(a.name), "capture.log"), encoding="utf-8") as f:
        first = f.readline().strip()
    _p(f"已开始录「{a.name}」:{first}")
    _p(f"  打标记:rec mark {a.name} \"…\"   结束:rec stop {a.name}")


def cmd_win_shot(a):
    out = os.path.abspath(os.path.expanduser(a.output or f"win-{time.strftime('%H%M%S')}.png"))
    _p(W.shot(out, _targ(a)))


def main(argv=None):
    p = build()
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if argv[:1] == ["start"] and "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)
    if a.command == "start":
        a.cmd = cmd or a.cmd
    try:
        a.func(a)
    except S.RecError as e:
        print(f"rec: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
