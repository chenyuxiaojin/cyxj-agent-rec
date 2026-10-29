"""图形窗口真实录屏:调 capture/ 里的 rec-capture(ScreenCaptureKit),只录指定窗口 / App。"""
import json
import os
import signal
import subprocess
import time

from . import session as S

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "capture", ".build", "release", "rec-capture")


def helper():
    if not os.path.exists(HELPER):
        raise S.RecError(f"没编译窗口录制组件:cd {os.path.dirname(os.path.dirname(os.path.dirname(HELPER)))} "
                         f"&& swift build -c release")
    return HELPER


def target_args(app=None, window_id=None, title=None, desktop=False, with_apps=(), region=None, pad=None,
                only_app=False):
    a = []
    if window_id:
        a += ["--window-id", str(window_id)]
    if app:
        a += ["--app", app]
    if title:
        a += ["--title", title]
    if desktop:
        a.append("--desktop")
    elif only_app or with_apps or region:
        a.append("--only-app")
    for w in with_apps:
        a += ["--with", w]
    if region:
        a += ["--region", region]
    if pad:
        a += ["--pad", str(pad)]
    if not (window_id or app or title):
        raise S.RecError("要指定录谁:--app 名字 / --window-id 编号 / --title 标题里的字(rec win list 看有哪些)")
    return a


def list_windows(show_all=False):
    args = [helper(), "list"] + ([] if show_all else [])
    p = subprocess.run(args + ["--json"], capture_output=True, text=True)
    if p.returncode != 0:
        raise S.RecError(p.stderr.strip())
    rows = json.loads(p.stdout)
    if not show_all:
        rows = [r for r in rows if r["onScreen"] and r["app"] and r["layer"] >= 0]
    return rows


def shot(out, targ):
    p = subprocess.run([helper(), "shot", "--out", out, *targ], capture_output=True, text=True)
    if p.returncode != 0:
        raise S.RecError(p.stderr.strip())
    return out


def start(name, targ, fps=30, cursor=False):
    exe = helper()
    if S.is_running(name) or _alive(name):
        raise S.RecError(f"会话「{name}」已经在录")
    d = S.sdir(name)
    if os.path.exists(os.path.join(d, "rec.mov")):
        raise S.RecError(f"「{name}」已经录过({d});换个名字,或 rec rm {name}")
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "rec.mov")
    started = os.path.join(d, "started")
    log = open(os.path.join(d, "capture.log"), "w")
    cmd = [exe, "record", "--out", out, "--started-file", started, "--fps", str(fps), *targ]
    if cursor:
        cmd.append("--cursor")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    meta = {"name": name, "kind": "window", "pid": proc.pid, "target": targ, "video": out,
            "cmd": cmd[1:], "created": time.time(), "cols": 0, "rows": 0}
    S.save_meta(name, meta)
    for _ in range(100):
        if os.path.exists(started):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if not os.path.exists(started):
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        with open(os.path.join(d, "capture.log"), encoding="utf-8") as f:
            raise S.RecError("窗口录制没起来:" + f.read().strip())
    with open(started) as f:
        meta["t0"] = float(f.read().strip())
    S.save_meta(name, meta)
    return meta


def _alive(name):
    try:
        meta = S.load_meta(name)
    except S.RecError:
        return False
    if meta.get("kind") != "window" or meta.get("stopped"):
        return False
    try:
        os.kill(meta["pid"], 0)
        return True
    except OSError:
        return False


def is_running(name):
    return _alive(name)


def stop(name, timeout=15):
    meta = S.load_meta(name)
    if not _alive(name):
        return meta
    meta["stop_at"] = time.time() - meta.get("t0", meta["created"])
    os.kill(meta["pid"], signal.SIGINT)
    deadline = time.time() + timeout
    while time.time() < deadline and _alive(name):
        time.sleep(0.2)
    meta["stopped"] = time.time()
    S.save_meta(name, meta)
    return meta


def export(meta, out, marks, t_from=None, t_to=None, log=print):
    """录好的 .mov → MP4(不重新压缩,画质不变),带章节。"""
    from .export import _write_ffmeta, fmt_ts, _resolve_point
    src = meta["video"]
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                               capture_output=True, text=True).stdout.strip() or 0)
    # 画面不变时系统不出新帧,文件会比真实时长短(尾巴丢掉);按 rec stop 的时刻补齐
    real = max(dur, meta.get("stop_at") or dur)
    a = max(0.0, _resolve_point(t_from, marks, 0.0))
    b = min(real, _resolve_point(t_to, marks, real))
    chapters = [{"t": m["t"] - a, "label": m["label"]} for m in marks if a <= m["t"] <= b]
    if chapters and chapters[0]["t"] > 0.5:
        chapters.insert(0, {"t": 0.0, "label": "开始"})
    ffmeta = out + ".ffmeta"
    _write_ffmeta(ffmeta, chapters, b - a)
    fps = next((int(x) for i, x in enumerate(meta["cmd"]) if i and meta["cmd"][i - 1] == "--fps"), 30)
    # 统一转成恒定帧率(系统录出来是可变帧率,达芬奇等剪辑软件容易对不齐)
    vf = [f"tpad=stop_mode=clone:stop_duration={max(0.0, real - dur) + 0.5:.3f}", f"fps={fps}"]
    w, h = (int(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0",
         src], capture_output=True, text=True).stdout.strip().split(",")[:2])
    enc = (["-c:v", "libx264", "-preset", "fast", "-crf", "16"] if w * h <= 4_200_000 else
           ["-c:v", "h264_videotoolbox", "-b:v", "40M"])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-i", ffmeta,
           "-map", "0:v", "-map_metadata", "1", "-map_chapters", "1", "-vf", ",".join(vf),
           "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", *enc, "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    p = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(ffmeta)
    if p.returncode != 0:
        raise S.RecError("导出失败:" + p.stderr.strip())
    side = os.path.splitext(out)[0] + ".章节.txt"
    with open(side, "w", encoding="utf-8") as f:
        f.write(f"# {os.path.basename(out)}  {fmt_ts(b - a)}(真实录屏,未压缩时长)\n\n## 章节\n")
        for c in chapters:
            f.write(f"{fmt_ts(c['t'])} {c['label']}\n")
        if not chapters:
            f.write("(没有打标记)\n")
    log(f"导出完成:{fmt_ts(b - a)} → {out}")
    return out, side


def notify(name):
    p = subprocess.run([helper(), "notify", name], capture_output=True, text=True)
    if p.returncode != 0:
        raise S.RecError(p.stderr.strip())
