"""终端模拟:把录下来的输出流重放成「每一刻屏幕上是什么」。

基于 pyte,补了两处它不支持、但 Claude Code 天天在用的东西:
- 弱化文字(SGR 2,dim):pyte 的格子里没有这个字段,这里把它编码进前景色(前缀 "~")
- 带 < > = 前缀的私有 CSI(kitty 键盘协议 \\e[>5u、modifyOtherKeys \\e[>4;2m 等):
  pyte 会把 \\e[>4;2m 误当成「下划线」,把 \\e[<u 的 u 当字打出来,所以喂进去之前先剔掉
"""
import json
import re

import pyte

# 私有前缀 CSI:整条删掉
_CSI_PRIVATE = re.compile(r"\x1b\[[<>=][0-9;:]*[ -/]*[@-~]")
# 带 ? 的 CSI:pyte 只在 h/l/m/n/J/K 这几种上收 private 参数,其余(如 \e[?6G)会直接报错,删掉
_CSI_QUESTION = re.compile(r"\x1b\[\?[0-9;]*[ -/]*([@-~])")
# 带冒号子参数的 CSI(\e[4:3m 波浪下划线、\e[38:2::r:g:bm):SGR 转成分号写法,其余删掉
_CSI_COLON = re.compile(r"\x1b\[([0-9;]*:[0-9;:]*)([ -/]*[@-~])")
# 数据块末尾没写完的转义序列,留到下一块拼起来再处理
_INCOMPLETE = re.compile(r"\x1b(\[[0-9;:<>=?]*[ -/]*)?\Z")


def _fix_colon(m):
    params, final = m.group(1), m.group(2)
    if final != "m":
        return ""
    out = []
    for p in params.split(";"):
        if ":" not in p:
            out.append(p)
            continue
        parts = p.split(":")
        head = parts[0]
        if head == "4":
            out.append("24" if parts[1:] == ["0"] else "4")
        elif head in ("38", "48") and len(parts) >= 2 and parts[1] == "2":
            rgb = [x for x in parts[2:] if x != ""][-3:]
            if len(rgb) == 3:
                out.append(";".join([head, "2", *rgb]))
        elif head in ("38", "48") and len(parts) == 3 and parts[1] == "5":
            out.append(";".join([head, "5", parts[2]]))
        # 58(下划线颜色)等直接丢掉
    return "\x1b[" + ";".join(out) + "m" if out else ""


class EscapeFilter:
    def __init__(self):
        self.carry = ""

    def __call__(self, data):
        data = self.carry + data
        self.carry = ""
        m = _INCOMPLETE.search(data)
        if m:
            self.carry = data[m.start():]
            data = data[: m.start()]
        data = _CSI_PRIVATE.sub("", data)
        data = _CSI_QUESTION.sub(lambda m: m.group(0) if m.group(1) in "hlmnJK" else "", data)
        return _CSI_COLON.sub(_fix_colon, data)


class Screen(pyte.Screen):
    """多记一个 dim 状态;dim 的字前景色写成 "~颜色"。"""

    def __init__(self, columns, lines):
        self._dim = False
        super().__init__(columns, lines)

    def reset(self):
        super().reset()
        self._dim = False

    def report_device_status(self, mode, private=False):
        # \e[?6n 这类带 ? 的查询(Claude Code 在 Terminal.app 下会发),原版 pyte 不认就直接报错;录像重放不用回答
        if not private:
            super().report_device_status(mode)

    def select_graphic_rendition(self, *attrs, private=False):
        if private:
            return
        if not attrs or attrs == (0,):
            self._dim = False
            super().select_graphic_rendition(*attrs)
            return
        keep, a, i = [], list(attrs), 0
        while i < len(a):
            v = a[i]
            if v in (38, 48, 58) and i + 1 < len(a):
                n = {5: 3, 2: 5}.get(a[i + 1], 2)
                if v != 58:
                    keep.extend(a[i:i + n])
                i += n
                continue
            if v == 0:
                self._dim = False
            elif v == 2:
                self._dim = True
                i += 1
                continue
            elif v == 22:
                self._dim = False
            elif v == 58 or v == 59:
                i += 1
                continue
            keep.append(v)
            i += 1
        if keep:
            super().select_graphic_rendition(*keep)
        fg = self.cursor.attrs.fg
        base = fg[1:] if fg.startswith("~") else fg
        want = "~" + base if self._dim else base
        if want != fg:
            self.cursor.attrs = self.cursor.attrs._replace(fg=want)


class Terminal:
    """一个能喂数据、能取快照的虚拟终端。"""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen = Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.filter = EscapeFilter()

    def feed(self, data):
        data = self.filter(data)
        if data:
            self.stream.feed(data)

    def resize(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.screen.resize(rows, cols)
        self.screen.dirty.update(range(rows))

    def row(self, y):
        line = self.screen.buffer[y]
        default = self.screen.default_char
        return tuple(line.get(x, default) for x in range(self.cols))

    def cursor(self):
        c = self.screen.cursor
        return (c.x, c.y, bool(c.hidden))

    def title(self):
        return self.screen.title or ""


class RowTable:
    """行内容去重:同样的一行只存一份,快照里只放编号。"""

    def __init__(self):
        self.ids = {}
        self.rows = []

    def intern(self, row):
        i = self.ids.get(row)
        if i is None:
            i = len(self.rows)
            self.ids[row] = i
            self.rows.append(row)
        return i


def screen_text(term):
    return "\n".join(term.screen.display)


# ---------- 读 asciicast(v2 / v3) ----------

def load_cast(path):
    """返回 (header, events);events = [(绝对秒, 类型, 数据)],类型 o 输出 / r 改尺寸 / m 标记。"""
    with open(path, encoding="utf-8") as f:
        first = f.readline()
        header = json.loads(first)
        version = header.get("version", 2)
        if version == 3:
            term = header.get("term", {})
            header.setdefault("width", term.get("cols"))
            header.setdefault("height", term.get("rows"))
        events, t = [], 0.0
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                break  # 录制中被打断,最后一行可能没写完
            if version == 3:
                t += ev[0]
            else:
                t = ev[0]
            events.append((t, ev[1], ev[2]))
    return header, events
