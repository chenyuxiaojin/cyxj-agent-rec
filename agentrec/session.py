"""后台会话:独立的 tmux 服务(-L agent-rec)里开终端,asciinema 在里面录。

一律走 tmux,不用系统级按键、不碰任何窗口,所以人照常用电脑互不干扰。
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time

SOCKET = "agent-rec"
HOME = os.path.expanduser(os.environ.get("AGENT_REC_HOME", "~/.agent-rec"))
CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmux.conf")

# 从 Claude Code 里调用时带着的「我在某个会话里」标记,不带进新终端,免得里面的程序以为自己是子会话
_DROP_PREFIX = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_CHILD",
                "CLAUDE_CODE_MESSAGING", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID",
                "CLAUDE_EFFORT", "CLAUDE_PLUGIN_DATA", "TMUX", "ASCIINEMA_")

KEY_ALIASES = {
    "enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "shift-tab": "BTab", "s-tab": "BTab", "btab": "BTab", "backspace": "BSpace", "bs": "BSpace",
    "delete": "DC", "del": "DC", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "PPage", "pgup": "PPage", "pagedown": "NPage",
    "pgdn": "NPage", "space": "Space",
}

THEME_STYLE = {  # 让终端里的程序(Claude Code 的 auto 主题)按这个底色判断深浅
    "terminal": "fg=#000000,bg=#ffffff",
    "terminal-dark": "fg=#ffffff,bg=#1e1e1e",
    "monokai": "fg=#f8f8f2,bg=#272822",
}


class RecError(Exception):
    pass


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(_DROP_PREFIX)}
    env.setdefault("LANG", "en_US.UTF-8")
    return env


def tmux(*args, check=True, capture=True):
    cmd = ["tmux", "-u", "-L", SOCKET, "-f", CONF, *args]
    p = subprocess.run(cmd, capture_output=capture, text=True, env=clean_env())
    if check and p.returncode != 0:
        raise RecError(f"tmux {' '.join(args[:2])} 失败:{(p.stderr or '').strip()}")
    return p


def tname(name):
    """tmux 会话名不能带 . 和 :"""
    return re.sub(r"[.:]", "_", name)


def pane(name):
    """精确指向这个会话的窗格(= 前缀防止前缀匹配到别的会话)"""
    return f"={tname(name)}:"


def sdir(name):
    return os.path.join(HOME, name)


def load_meta(name):
    p = os.path.join(sdir(name), "meta.json")
    if not os.path.exists(p):
        raise RecError(f"没有叫「{name}」的会话(rec list 看看有哪些)")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_meta(name, meta):
    with open(os.path.join(sdir(name), "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def is_running(name):
    return tmux("has-session", "-t", "=" + tname(name), check=False).returncode == 0


def pane_info(name):
    p = tmux("display-message", "-p", "-t", pane(name),
             "#{pane_dead} #{pane_dead_status} #{pane_pid} #{cursor_x} #{cursor_y} #{cursor_flag} #{pane_title}",
             check=False)
    if p.returncode != 0:
        return None
    dead, status, pid, cx, cy, flag, title = (p.stdout.rstrip("\n").split(" ", 6) + [""] * 7)[:7]
    return {"dead": dead == "1", "status": status, "pid": int(pid or 0),
            "cursor": (int(cx or 0), int(cy or 0)), "cursor_visible": flag == "1", "title": title}


def require_live(name):
    if not is_running(name):
        raise RecError(f"会话「{name}」没在运行")
    info = pane_info(name)
    if info and info["dead"]:
        raise RecError(f"会话「{name}」里的程序已经退出(退出码 {info['status']}),rec screen 还能看最后画面,"
                       f"rec stop 收尾")
    return info


def log_action(name, kind, start, end, detail=""):
    with open(os.path.join(sdir(name), "actions.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "start": start, "end": end, "detail": detail}, ensure_ascii=False) + "\n")


def start(name, cmd, cols=140, rows=34, cwd=None, env=(), theme="terminal", as_terminal=True):
    if not shutil.which("asciinema"):
        raise RecError("没装 asciinema:brew install asciinema")
    if is_running(name):
        raise RecError(f"会话「{name}」已经在运行;换个名字,或先 rec stop {name}")
    d = sdir(name)
    if os.path.exists(os.path.join(d, "rec.cast")):
        raise RecError(f"「{name}」已经录过({d});换个名字,或 rec rm {name} 后再开")
    os.makedirs(d, exist_ok=True)
    cwd = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    cast = os.path.join(d, "rec.cast")
    if not cmd:
        cmd = [os.environ.get("SHELL", "/bin/zsh"), "-l"]
    command = shlex.join(cmd)
    t = tname(name)
    go = f"go-{t}-{os.getpid()}"
    # 冒充真实终端:去掉 tmux 痕迹(不然 Claude Code 会降成 256 色)、TERM 用 Terminal.app 的。
    # TERM_PROGRAM 也得在这里设:tmux 会把窗格里的改成 tmux,Claude Code 认不出 Terminal.app,
    # 就会换一套画法(比如 logo 改用带黑底的方块字,和真实终端不一样)
    disguise = ""
    if as_terminal:
        prog = {"TERM_PROGRAM": os.environ.get("TERM_PROGRAM") or "Apple_Terminal",
                "TERM_PROGRAM_VERSION": os.environ.get("TERM_PROGRAM_VERSION")}
        if prog["TERM_PROGRAM"] == "tmux":
            prog = {"TERM_PROGRAM": "Apple_Terminal", "TERM_PROGRAM_VERSION": None}
        exports = "".join(f"export {k}={shlex.quote(v)}; " if v else f"unset {k}; " for k, v in prog.items())
        disguise = f"unset TMUX TMUX_PANE; export TERM=xterm-256color; {exports}"
    inner = (f"tmux -L {SOCKET} wait-for {shlex.quote(go)}; {disguise}"
             f"exec asciinema rec --quiet --overwrite --capture-env SHELL,TERM -c {shlex.quote(command)} "
             f"{shlex.quote(cast)}")
    envs = ["-e", "COLORTERM=truecolor"]
    for kv in env:
        envs += ["-e", kv]
    tmux("new-session", "-d", "-s", t, "-x", str(cols), "-y", str(rows), "-c", cwd, *envs,
         "/bin/sh", "-c", inner)
    tmux("set-option", "-t", t, "window-style", THEME_STYLE.get(theme, THEME_STYLE["terminal"]))
    tmux("resize-window", "-t", t, "-x", str(cols), "-y", str(rows))
    meta = {"name": name, "tmux": t, "cmd": cmd, "cwd": cwd, "cols": cols, "rows": rows, "theme": theme,
            "cast": cast, "created": time.time()}
    save_meta(name, meta)
    tmux("wait-for", "-S", go)
    t0 = time.time()
    # 录像真正开始的时刻:取 cast 文件的创建时间(比命令返回的时刻准)
    for _ in range(50):
        if os.path.exists(cast):
            st = os.stat(cast)
            t0 = getattr(st, "st_birthtime", t0)
            break
        time.sleep(0.05)
    meta["t0"] = t0
    save_meta(name, meta)
    return meta


def _lit(s):
    """tmux 把以分号结尾的参数当命令分隔符,转义掉"""
    return s[:-1] + "\\;" if s.endswith(";") else s


def send_text(name, text, cps=30.0, enter=False):
    require_live(name)
    t = pane(name)
    start_t = time.time()
    if cps and cps > 0:
        delay = 1.0 / cps
        for ch in text:
            if ch == "\n":
                tmux("send-keys", "-t", t, "Enter")
            else:
                tmux("send-keys", "-t", t, "-l", "--", _lit(ch))
            time.sleep(delay)
    else:
        for i, part in enumerate(text.split("\n")):
            if i:
                tmux("send-keys", "-t", t, "Enter")
            if part:
                tmux("send-keys", "-t", t, "-l", "--", _lit(part))
    if enter:
        time.sleep(0.5)
        tmux("send-keys", "-t", t, "Enter")
    log_action(name, "type", start_t, time.time(), text[:200])


def send_keys(name, keys, delay=0.15):
    require_live(name)
    t = pane(name)
    start_t = time.time()
    seq = []
    for k in keys:
        m = re.fullmatch(r"(.+?)\*(\d+)", k)
        k, n = (m.group(1), int(m.group(2))) if m else (k, 1)
        k = KEY_ALIASES.get(k.lower(), k)
        if re.fullmatch(r"ctrl[-+](.)", k, re.I):
            k = "C-" + k[-1].lower()
        seq += [k] * n
    for i, k in enumerate(seq):
        if i:
            time.sleep(delay)
        tmux("send-keys", "-t", t, k)
    log_action(name, "key", start_t, time.time(), " ".join(seq))
    return seq


def paste(name, text):
    """括号粘贴:多行文字整段贴进去(Claude Code 里不会被回车拆开)。"""
    require_live(name)
    t = pane(name)
    start_t = time.time()
    buf = f"agent-rec-{tname(name)}"
    p = subprocess.run(["tmux", "-u", "-L", SOCKET, "-f", CONF, "load-buffer", "-b", buf, "-"],
                       input=text, text=True, capture_output=True, env=clean_env())
    if p.returncode != 0:
        raise RecError(f"粘贴失败:{p.stderr.strip()}")
    tmux("paste-buffer", "-p", "-d", "-b", buf, "-t", t)
    log_action(name, "paste", start_t, time.time(), text[:200])


def capture(name, ansi=False):
    if not is_running(name):
        raise RecError(f"会话「{name}」没在运行")
    args = ["capture-pane", "-p", "-t", pane(name)]
    if ansi:
        args.insert(2, "-e")
    return tmux(*args).stdout


def mark(name, label):
    meta = load_meta(name)
    now = time.time()
    t = now - meta.get("t0", meta["created"])
    with open(os.path.join(sdir(name), "marks.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": t, "label": label, "wall": now}, ensure_ascii=False) + "\n")
    return t


def read_jsonl(name, fname):
    p = os.path.join(sdir(name), fname)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def stop(name, grace=4.0):
    meta = load_meta(name)
    if not is_running(name):
        return meta
    meta["stop_at"] = time.time() - meta.get("t0", meta["created"])
    try:
        with open(os.path.join(sdir(name), "final.txt"), "w", encoding="utf-8") as f:
            f.write(capture(name))
    except RecError:
        pass
    info = pane_info(name)
    if info and not info["dead"] and info["pid"]:
        # 先让里面的程序自己退出,asciinema 才能把录像写完整
        kids = subprocess.run(["pgrep", "-P", str(info["pid"])], capture_output=True, text=True).stdout.split()
        for k in kids:
            subprocess.run(["kill", "-HUP", k], capture_output=True)
        deadline = time.time() + grace
        while time.time() < deadline:
            i = pane_info(name)
            if not i or i["dead"]:
                break
            time.sleep(0.2)
    tmux("kill-session", "-t", "=" + tname(name), check=False)
    meta["stopped"] = time.time()
    save_meta(name, meta)
    return meta


def list_sessions():
    if not os.path.isdir(HOME):
        return []
    out = []
    for n in sorted(os.listdir(HOME)):
        if os.path.exists(os.path.join(HOME, n, "meta.json")):
            m = load_meta(n)
            m["running"] = is_running(n)
            out.append(m)
    return out
