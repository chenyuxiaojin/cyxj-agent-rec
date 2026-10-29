"""录像 → MP4:重放 → 算时间轴(压空闲 / 加速转圈)→ 逐帧画 → 直接喂 ffmpeg,不经过 GIF。"""
import json
import math
import os
import subprocess
import sys
import time

from .render import Layout, Renderer, fit_renderer
from .term import RowTable, Terminal, load_cast


def simulate(events, cols, rows, fps, t_from, t_to):
    """按 fps 采样,返回每个采样点的 (行编号元组, 光标, 标题) 和行表。"""
    term = Terminal(cols, rows)
    table = RowTable()
    ids = tuple(table.intern(term.row(y)) for y in range(rows))
    term.screen.dirty.clear()
    snaps = []
    n = max(1, int(math.floor((t_to - t_from) * fps)) + 1)
    ei = 0
    for k in range(n):
        t = t_from + k / fps
        while ei < len(events) and events[ei][0] <= t:
            _, kind, data = events[ei]
            if kind == "o":
                term.feed(data)
            ei += 1
        dirty = term.screen.dirty
        if dirty:
            lst = list(ids)
            for y in dirty:
                if y < rows:
                    lst[y] = table.intern(term.row(y))
            dirty.clear()
            ids = tuple(lst)
        snaps.append((ids, term.cursor(), term.title()))
    return snaps, table


def _runs(flags):
    """连续为 True 的区间 [起, 止)。"""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def plan_timeline(snaps, fps, rows, protected, idle_limit, speedup):
    """给每个采样点一个倍速(1 = 原速)。

    - 加速段(speedup):前后各 1 秒内只有 ≤ max_rows 行在变(转圈、计时器),且不在 agent 操作前后,
      持续超过 min_wait 秒 → 头 1 秒、尾 0.5 秒原速,中间按 factor 倍(再长就压到 max_len 秒内)
    - 空闲段(idle_limit):画面完全不动超过 idle_limit 秒 → 压成 idle_limit 秒
    """
    n = len(snaps)
    chg = [0] * n
    rowsets = [()] * n
    for k in range(1, n):
        a, b = snaps[k - 1][0], snaps[k][0]
        if a is not b and a != b:
            rs = tuple(y for y in range(rows) if a[y] != b[y])
            rowsets[k] = rs
            chg[k] = len(rs)
        elif snaps[k - 1][1] != snaps[k][1]:
            chg[k] = 0  # 只有光标在动不算变化
    rate = [1.0] * n
    speed_flags = [False] * n
    segments = []
    if speedup:
        half = int(fps * 1.0)
        prot = [False] * n
        for a, b in protected:
            for k in range(max(0, int(a * fps)), min(n, int(math.ceil(b * fps)) + 1)):
                prot[k] = True
        # 滑动窗口内变化行的并集
        quiet = [False] * n
        from collections import Counter
        win = Counter()
        lo = hi = 0
        for k in range(n):
            while hi < n and hi <= k + half:
                win.update(rowsets[hi])
                hi += 1
            while lo < k - half:
                win.subtract(rowsets[lo])
                lo += 1
            active = sum(1 for v in win.values() if v > 0)
            # 完全不动的交给空闲压缩;这里只抓「有东西在转、但只是一两行在变」的等待
            quiet[k] = 0 < active <= speedup["max_rows"] and not prot[k]
        head, tail = int(fps * 1.0), int(fps * 0.5)
        for a, b in _runs(quiet):
            if (b - a) / fps < speedup["min_wait"]:
                continue
            ma, mb = a + head, b - tail
            if mb <= ma:
                continue
            mid = (mb - ma) / fps
            r = speedup["factor"]
            if speedup["max_len"] and mid / r > speedup["max_len"]:
                r = mid / speedup["max_len"]
            for k in range(ma, mb):
                rate[k] = r
                speed_flags[k] = True
            segments.append({"kind": "speed", "src": (ma / fps, mb / fps), "rate": r})
    if idle_limit:
        static = [chg[k] == 0 and not speed_flags[k] for k in range(n)]
        for a, b in _runs(static):
            L = (b - a) / fps
            if L > idle_limit:
                r = L / idle_limit
                for k in range(a, b):
                    rate[k] = r
                segments.append({"kind": "idle", "src": (a / fps, b / fps), "rate": r})
    # 累计输出时间
    out_at = [0.0] * (n + 1)
    for k in range(n):
        out_at[k + 1] = out_at[k] + (1.0 / fps) / rate[k]
    frames, j = [], 0
    for k in range(n):
        while j / fps < out_at[k + 1] - 1e-9:
            frames.append(k)
            j += 1
    for s in segments:
        s["out"] = (out_at[int(round(s["src"][0] * fps))], out_at[min(n, int(round(s["src"][1] * fps)))])
    return frames, rate, speed_flags, out_at, segments


def fmt_ts(t):
    t = max(0, t)
    m, s = divmod(int(round(t)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _resolve_point(v, marks, default):
    """--from / --to:秒数,或标记名。"""
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        for m in marks:
            if m["label"] == v:
                return m["t"]
        raise SystemExit(f"找不到标记「{v}」,现有标记:{', '.join(m['label'] for m in marks) or '(无)'}")


def export(cast_path, out_path, *, marks=(), actions=(), stop_at=None, fps=30, fit=(3840, 2160),
           font_size=None, family="SF Mono", font_path=None, theme="terminal", frame=True, title=None,
           cwd=None, proc=None, t_from=None, t_to=None, idle_limit=2.0, speedup=None, badge=False, cursor=True,
           crf=18, encoder="x264", log=print):
    header, events = load_cast(cast_path)
    cols, rows = header["width"], header["height"]
    end = events[-1][0] if events else 0.0
    # asciinema 自带的标记(m 事件)也算章节
    marks = list(marks) + [{"t": t, "label": d or "标记"} for t, k, d in events if k == "m"]
    marks.sort(key=lambda m: m["t"])
    default_to = min(end, stop_at) if stop_at else end
    a = max(0.0, _resolve_point(t_from, marks, 0.0))
    b = min(end, _resolve_point(t_to, marks, default_to))
    if b <= a:
        raise SystemExit(f"截取区间为空:{a:.1f}s → {b:.1f}s")

    t0 = time.time()
    snaps, table = simulate(events, cols, rows, fps, a, b)
    prot = [(s - a, e - a) for s, e in actions]
    frames, rate, speed_flags, out_at, segments = plan_timeline(snaps, fps, rows, prot, idle_limit, speedup)
    log(f"重放完成:原长 {fmt_ts(b - a)} → 成片 {fmt_ts(len(frames) / fps)},{len(table.rows)} 种不同的行,"
        f"用时 {time.time() - t0:.1f}s")

    if font_size:
        r = Renderer(font_size, family, theme, font_path)
        lay = Layout(r, cols, rows, frame=frame, fit=None, cwd=cwd, proc=proc)
    else:
        r = fit_renderer(cols, rows, fit, family, theme, frame, font_path)
        lay = Layout(r, cols, rows, frame=frame, fit=fit, cwd=cwd, proc=proc)
    W, H = lay.size
    log(f"画面 {W}x{H},字号 {r.size}px,格子 {r.cw}x{r.ch}")

    chapters = []
    for m in marks:
        if a <= m["t"] <= b:
            k = min(len(snaps), int(round((m["t"] - a) * fps)))
            chapters.append({"t": out_at[k], "label": m["label"]})
    if chapters and chapters[0]["t"] > 0.5:
        chapters.insert(0, {"t": 0.0, "label": "开始"})  # 平台章节要求从 00:00 起
    meta_path = out_path + ".ffmeta"
    _write_ffmeta(meta_path, chapters, len(frames) / fps)

    enc = (["-c:v", "h264_videotoolbox", "-b:v", "24M" if W * H > 4e6 else "12M", "-allow_sw", "1"]
           if encoder == "videotoolbox" else
           ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-tune", "animation"])
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-i", meta_path, "-map", "0:v", "-map_metadata", "1", "-map_chapters", "1",
           *enc, "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    canvas = lay.base.copy()
    row_imgs = {}
    shown = [None] * rows
    shown_title = None
    cursor_row = None
    last_k, last_badge, last_bytes = None, None, None
    t1 = time.time()
    try:
        for j, k in enumerate(frames):
            want_badge = f"▶▶ {round(rate[k])}×" if badge and speed_flags[k] else None
            if k != last_k or want_badge != last_badge or last_bytes is None:
                ids, (cx, cy, hidden), ttl = snaps[k]
                show_cursor = cursor and not hidden and 0 <= cy < rows and 0 <= cx < cols
                for y in range(rows):
                    if ids[y] != shown[y] or y == cursor_row or (show_cursor and y == cy):
                        im = row_imgs.get(ids[y])
                        if im is None:
                            im = row_imgs[ids[y]] = r.render_row(table.rows[ids[y]])
                        canvas.paste(im, lay.row_pos(y))
                        shown[y] = ids[y]
                cursor_row = None
                if show_cursor:
                    cells = table.rows[ids[cy]]
                    wide = cx + 1 < cols and cells[cx + 1].data == ""
                    x0, y0 = lay.row_pos(cy)
                    canvas.paste(r.render_cursor(cells[cx], wide), (x0 + cx * r.cw, y0))
                    cursor_row = cy
                if frame:
                    tt = title if title is not None else ttl
                    if tt != shown_title:
                        canvas.paste(lay.title_image(tt), lay.title_pos())
                        shown_title = tt
                if want_badge:
                    img = canvas.copy()
                    lay.badge(img, want_badge)
                    last_bytes = img.tobytes()
                else:
                    last_bytes = canvas.tobytes()
                last_k, last_badge = k, want_badge
            proc.stdin.write(last_bytes)
            if j and j % (fps * 30) == 0:
                log(f"  已画 {fmt_ts(j / fps)} / {fmt_ts(len(frames) / fps)}")
        proc.stdin.close()
    except BrokenPipeError:
        pass
    rc = proc.wait()
    os.remove(meta_path)
    if rc != 0:
        raise SystemExit(f"ffmpeg 失败(退出码 {rc})")
    log(f"编码完成,用时 {time.time() - t1:.1f}s → {out_path}")

    side = os.path.splitext(out_path)[0] + ".章节.txt"
    with open(side, "w", encoding="utf-8") as f:
        f.write(f"# {os.path.basename(out_path)}  成片 {fmt_ts(len(frames) / fps)}(原长 {fmt_ts(b - a)})\n\n")
        f.write("## 章节(成片时间)\n")
        for c in chapters:
            f.write(f"{fmt_ts(c['t'])} {c['label']}\n")
        if not chapters:
            f.write("(没有打标记)\n")
        if segments:
            f.write("\n## 压缩过的段落(成片时间 ← 原始时间)\n")
            for s in sorted(segments, key=lambda s: s["src"][0]):
                kind = "加速" if s["kind"] == "speed" else "空闲压缩"
                f.write(f"{fmt_ts(s['out'][0])}–{fmt_ts(s['out'][1])} ← {fmt_ts(s['src'][0])}–{fmt_ts(s['src'][1])}"
                        f"  {kind} {s['rate']:.1f}×\n")
    return {"out": out_path, "chapters_file": side, "duration": len(frames) / fps, "size": (W, H),
            "chapters": chapters, "segments": segments}


def _write_ffmeta(path, chapters, total):
    def esc(s):
        return "".join("\\" + ch if ch in "=;#\\\n" else ch for ch in s)
    with open(path, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")
        pts = [c for c in chapters]
        for i, c in enumerate(pts):
            start = int(c["t"] * 1000)
            end = int((pts[i + 1]["t"] if i + 1 < len(pts) else total) * 1000)
            if end <= start:
                continue
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle={esc(c['label'])}\n")


def snapshot(cast_path, png_path, at=None, *, font_size=22, family="SF Mono", font_path=None,
             theme="terminal", frame=True, title=None, cwd=None, proc=None):
    """录像里某一刻(默认最后)画成 PNG。"""
    header, events = load_cast(cast_path)
    cols, rows = header["width"], header["height"]
    end = events[-1][0] if events else 0.0
    t = end if at is None else min(end, max(0.0, at))
    term = Terminal(cols, rows)
    for tt, kind, data in events:
        if tt > t:
            break
        if kind == "o":
            term.feed(data)
    r = Renderer(font_size, family, theme, font_path)
    lay = Layout(r, cols, rows, frame=frame, cwd=cwd, proc=proc)
    img = lay.base.copy()
    for y in range(rows):
        img.paste(r.render_row(term.row(y)), lay.row_pos(y))
    cx, cy, hidden = term.cursor()
    if not hidden and 0 <= cy < rows and 0 <= cx < cols:
        cells = term.row(cy)
        wide = cx + 1 < cols and cells[cx + 1].data == ""
        x0, y0 = lay.row_pos(cy)
        img.paste(r.render_cursor(cells[cx], wide), (x0 + cx * r.cw, y0))
    if frame:
        img.paste(lay.title_image(title if title is not None else term.title()), lay.title_pos())
    img.save(png_path)
    return png_path, t
