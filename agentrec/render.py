"""把终端格子画成图。

字交给苹果自己的排字引擎 CoreText 画(Terminal.app 用的就是它),格子大小、基线、
字体回退、字体平滑都按 Terminal.app 的规则来;窗口框照 macOS 26 上的 Terminal.app 量出来。
对照真实 Terminal.app 截图校准过:正文逐像素平均误差 0.12 / 255(截图本身的噪声级别)。

默认样子:Terminal.app「Basic」描述文件(白底黑字、SF Mono 11pt)。
"""
import glob
import math
import os

import AppKit
import CoreFoundation
import CoreText
import Quartz
from Foundation import NSURL
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------- 配色 ----------

_NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]


def _hex(s):
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _palette(hexes):
    pal = {}
    for i, n in enumerate(_NAMES):
        pal[n] = _hex(hexes[i])
        pal["bright" + n] = _hex(hexes[i + 8])
    return pal


THEMES = {
    # Terminal.app Basic(浅色模式);窗口框颜色量自真实截图
    "terminal": {
        "fg": "000000", "bg": "ffffff", "cursor": "7f7f7f",
        "palette": ["000000", "990000", "00a600", "999900", "0000b2", "b200b2", "00a6b2", "bfbfbf",
                    "666666", "e50000", "00d900", "e5e500", "0000ff", "e500e5", "00e5e5", "e5e5e5"],
        "chrome": {"bar": "ffffff", "line": "e6e6e6", "title": "505050", "backdrop": "e9ebf0"},
    },
    # Terminal.app Basic(深色模式,近似)
    "terminal-dark": {
        "fg": "ffffff", "bg": "1e1e1e", "cursor": "8c8c8c",
        "palette": ["000000", "990000", "00a600", "999900", "0000b2", "b200b2", "00a6b2", "bfbfbf",
                    "666666", "e50000", "00d900", "e5e500", "0000ff", "e500e5", "00e5e5", "e5e5e5"],
        "chrome": {"bar": "1e1e1e", "line": "3a3a3a", "title": "c8c8c8", "backdrop": "16171b"},
    },
    # 和之前 agg 出图一致
    "monokai": {
        "fg": "f8f8f2", "bg": "272822", "cursor": "f8f8f0",
        "palette": ["272822", "f92672", "a6e22e", "f4bf75", "66d9ef", "ae81ff", "a1efe4", "f8f8f2",
                    "75715e", "f92672", "a6e22e", "f4bf75", "66d9ef", "ae81ff", "a1efe4", "f9f8f5"],
        "chrome": {"bar": "272822", "line": "35362f", "title": "c8c8c0", "backdrop": "15161a"},
    },
}

# pyte 把 256 色里的前 16 色存成 xterm 的 hex,映射回调色板名,好用主题自己的颜色
_XTERM16 = ["000000", "cd0000", "00cd00", "cdcd00", "0000ee", "cd00cd", "00cdcd", "e5e5e5",
            "7f7f7f", "ff0000", "00ff00", "ffff00", "5c5cff", "ff00ff", "00ffff", "ffffff"]
_XTERM16_NAME = {h: (("bright" if i >= 8 else "") + _NAMES[i % 8]) for i, h in enumerate(_XTERM16)}


class Theme:
    def __init__(self, name):
        if name not in THEMES:
            raise SystemExit(f"不认识的主题 {name},可选:{', '.join(THEMES)}")
        t = THEMES[name]
        self.name = name
        self.fg, self.bg, self.cursor = _hex(t["fg"]), _hex(t["bg"]), _hex(t["cursor"])
        self.palette = _palette(t["palette"])
        self.chrome = {k: _hex(v) for k, v in t["chrome"].items()}
        self._cache = {}

    def color(self, name, default):
        key = (name, default)
        c = self._cache.get(key)
        if c is None:
            dim = name.startswith("~")
            n = name[1:] if dim else name
            if n == "default":
                rgb = default
            elif n in self.palette:
                rgb = self.palette[n]
            elif len(n) == 6:
                rgb = self.palette[_XTERM16_NAME[n]] if n in _XTERM16_NAME else _hex(n)
            else:
                rgb = default
            c = self._cache[key] = (rgb, dim)
        return c


def _mix(a, b, k):
    return tuple(round(a[i] * (1 - k) + b[i] * k) for i in range(3))


# ---------- CoreText 画布 ----------

_SRGB = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)


class _Canvas:
    """sRGB 位图;坐标按 CoreGraphics 的习惯,原点在左下角。"""

    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.buf = bytearray(w * h * 4)
        self.ctx = Quartz.CGBitmapContextCreate(self.buf, w, h, 8, w * 4, _SRGB,
                                                Quartz.kCGImageAlphaPremultipliedLast)
        # 字体平滑 = Terminal 的字重(关掉会明显偏细)
        Quartz.CGContextSetAllowsFontSmoothing(self.ctx, True)
        Quartz.CGContextSetShouldSmoothFonts(self.ctx, True)
        if bg is not None:
            self.fill(bg, 0, 0, w, h)

    def color(self, rgb):
        Quartz.CGContextSetRGBFillColor(self.ctx, rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1)

    def fill(self, rgb, x, y, w, h):
        self.color(rgb)
        Quartz.CGContextFillRect(self.ctx, Quartz.CGRectMake(x, y, w, h))

    def text(self, line, rgb, x, baseline):
        self.color(rgb)
        Quartz.CGContextSetTextPosition(self.ctx, x, baseline)
        CoreText.CTLineDraw(line, self.ctx)

    def image(self, mode="RGB"):
        # 位图是预乘 alpha 的(RGBa),读出来还原成普通 RGBA
        im = Image.frombuffer("RGBA", (self.w, self.h), bytes(self.buf), "raw", "RGBa", 0, 1)
        return im if mode == "RGBA" else im.convert(mode)


def _line(text, font, color_from_context=True):
    attrs = {CoreText.kCTFontAttributeName: font}
    if color_from_context:
        attrs[CoreText.kCTForegroundColorFromContextAttributeName] = True
    return CoreText.CTLineCreateWithAttributedString(CoreFoundation.CFAttributedStringCreate(None, text, attrs))


def _width(line):
    return CoreText.CTLineGetTypographicBounds(line, None, None, None)[0]


# ---------- 字体 ----------

_TERMAPP = "/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts"
_registered = set()


def _register(path):
    if path not in _registered:
        CoreText.CTFontManagerRegisterFontsForURL(NSURL.fileURLWithPath_(path),
                                                  CoreText.kCTFontManagerScopeProcess, None)
        _registered.add(path)


def _sfmono_ready():
    """SF Mono 只装在 Terminal.app 里,系统字体列表里没有,先登记给本进程。"""
    files = glob.glob("/Library/Fonts/SF-Mono-*.otf") or glob.glob(f"{_TERMAPP}/SF-Mono-*.otf")
    for p in files:
        _register(p)
    return bool(files)


# 四个变体:regular / bold / italic / bolditalic(PostScript 名)
FONT_FAMILIES = {
    "SF Mono": {"regular": "SFMono-Regular", "bold": "SFMono-Bold",
                "italic": "SFMono-RegularItalic", "bolditalic": "SFMono-BoldItalic"},
    "Menlo": {"regular": "Menlo-Regular", "bold": "Menlo-Bold",
              "italic": "Menlo-Italic", "bolditalic": "Menlo-BoldItalic"},
}

# Terminal.app「Basic」的字号;格子按这个字号算,再整体放大(Basic 默认就是 11pt)
PT = 11


def _fonts(family, font_path, size):
    if font_path:
        _register(font_path)
        descs = CoreText.CTFontManagerCreateFontDescriptorsFromURL(NSURL.fileURLWithPath_(font_path)) or []
        if not descs:
            raise SystemExit(f"读不了字体文件 {font_path}")
        f = CoreText.CTFontCreateWithFontDescriptor(descs[0], size, None)
        return {k: f for k in ("regular", "bold", "italic", "bolditalic")}
    if family not in FONT_FAMILIES:
        raise SystemExit(f"不认识的字体 {family},可选:{', '.join(FONT_FAMILIES)},或用 --font-file 指定文件")
    if family == "SF Mono" and not _sfmono_ready():
        family = "Menlo"
    return {k: CoreText.CTFontCreateWithName(n, size, None) for k, n in FONT_FAMILIES[family].items()}


# ---------- 渲染器 ----------

class Renderer:
    """size = 字号(像素)。size 22 = 11pt 的 Retina 两倍图,和真实终端截图逐像素对齐。"""

    def __init__(self, size, family="SF Mono", theme="terminal", font_path=None):
        self.size = size
        self.theme = Theme(theme) if isinstance(theme, str) else theme
        self.fonts = _fonts(family, font_path, size)
        # 格子:Terminal 在 11pt 下把字宽四舍五入、上下高度各自进位(6.80→7,10.47+2.65→11+3)
        k = self.k = size / PT
        ref = CoreText.CTFontCreateCopyWithAttributes(self.fonts["regular"], PT, None, None)
        asc, desc = math.ceil(CoreText.CTFontGetAscent(ref)), math.ceil(CoreText.CTFontGetDescent(ref))
        self.cw = max(1, round(round(_width(_line("M", ref))) * k))
        self.ascent = round(asc * k)
        self.ch = round((asc + desc) * k)
        f = self.fonts["regular"]
        self._ul = (CoreText.CTFontGetUnderlinePosition(f), max(1.0, CoreText.CTFontGetUnderlineThickness(f)))
        self._lines = {}

    def _variant(self, bold, italic):
        return self.fonts[("bold" if bold else "") + ("italic" if italic else "") or "regular"]

    def line(self, text, bold, italic):
        key = (text, bold, italic)
        ln = self._lines.get(key)
        if ln is None:
            ln = self._lines[key] = _line(text, self._variant(bold, italic))
        return ln

    def cell_colors(self, c):
        th = self.theme
        fg, fdim = th.color(c.fg, th.fg)
        bg, _ = th.color(c.bg, th.bg)
        if c.reverse:
            fg, bg = bg, fg
        if fdim:
            fg = _mix(fg, bg, 0.5)
        return fg, bg

    def render_row(self, cells):
        cols = len(cells)
        cv = _Canvas(cols * self.cw, self.ch, self.theme.bg)
        base = self.ch - self.ascent
        ul_pos, ul_thick = self._ul
        for x, c in enumerate(cells):
            if c.data == "":
                continue  # 宽字符占的第二格
            n = 2 if x + 1 < cols and cells[x + 1].data == "" else 1
            fg, bg = self.cell_colors(c)
            x0 = x * self.cw
            if bg != self.theme.bg:
                cv.fill(bg, x0, 0, n * self.cw, self.ch)
            if c.data.strip():
                # 每个字从自己格子的左边起画(中文也是靠左,不居中),字体回退交给 CoreText
                cv.text(self.line(c.data, c.bold, c.italics), fg, x0, base)
            if c.underscore:
                cv.fill(fg, x0, base + ul_pos - ul_thick / 2, n * self.cw, ul_thick)
            if c.strikethrough:
                cv.fill(fg, x0, base + round(self.size * 0.3), n * self.cw, ul_thick)
        return cv.image()

    def render_cursor(self, cell, wide):
        """光标格:实心块,字反色。"""
        cv = _Canvas(self.cw * (2 if wide else 1), self.ch, self.theme.cursor)
        if cell.data.strip():
            cv.text(self.line(cell.data, cell.bold, cell.italics), self.theme.bg, 0, self.ch - self.ascent)
        return cv.image()


# ---------- 画面布局:内容区 + 可选窗口框 + 背景 ----------

# 窗口框尺寸(点),量自 macOS 26 上 Terminal.app 的真实截图
BAR, LINE = 67, 1                       # 标题栏高度、下面那条分隔线
PAD_X, PAD_TOP, PAD_BOTTOM = 10, 7, 10  # 内容区四周留白
LIGHT_X, LIGHT_Y, LIGHT_D, LIGHT_GAP = 9, 9, 14, 23  # 红黄绿三个灯:左上角、直径、间距
# 灯的颜色(截图是 Display P3,已换成 sRGB):填充、外圈
LIGHTS = [((255, 75, 88), (230, 30, 40)), ((255, 198, 0), (235, 175, 0)), ((0, 202, 72), (0, 175, 30))]
CLOSE_DOT = ((139, 38, 45), 5)          # 关闭键中间的点(窗口里有程序在跑时 Terminal 会显示)
ICON_X, ICON_TOP, ICON = 82, 7, 16      # 标题前的文件夹图标
TITLE_X, TITLE_BASE, TITLE_PT = 102.5, 21, 13.6

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
PINGFANG = (sorted(glob.glob("/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc"))
            or ["/System/Library/Fonts/PingFang.ttc"])[0]


def _ui_font(size, bold=False):
    if os.path.exists(PINGFANG):
        return ImageFont.truetype(PINGFANG, size, index=11 if bold else 7)
    return ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", size, index=2 if bold else 0)


def _corner_mask(ww, wh, k):
    """真实窗口的圆角(连续曲率,不是正圆),按截图里的左上角镜像出四个角。"""
    c = Image.open(os.path.join(_ASSETS, "corner@2x.png")).convert("L")
    n = max(1, round(c.width * k / 2))
    c = c.resize((n, n), Image.LANCZOS)
    m = Image.new("L", (ww, wh), 255)
    m.paste(c, (0, 0))
    m.paste(c.transpose(Image.FLIP_LEFT_RIGHT), (ww - n, 0))
    m.paste(c.transpose(Image.FLIP_TOP_BOTTOM), (0, wh - n))
    m.paste(c.transpose(Image.ROTATE_180), (ww - n, wh - n))
    return m


# Finder 颜色标签 → 文件夹图标染成的色相(macOS 26 会这样染;绿色量自真实截图,其余按标签色估)
_TAG_HUE = {2: 0.396, 3: 0.78, 5: 0.14, 6: 0.0, 7: 0.08}


def _folder_icon(path, px):
    """目录的图标(RGBA,px 见方);目录不在了就用普通文件夹图标。"""
    ws = AppKit.NSWorkspace.sharedWorkspace()
    ok = bool(path) and os.path.isdir(path)
    icon = ws.iconForFile_(path) if ok else \
        ws.iconForFileType_(AppKit.NSFileTypeForHFSTypeCode(int.from_bytes(b"fldr", "big")))
    cv = _Canvas(px, px, None)
    cg = icon.CGImageForProposedRect_context_hints_(None, None, None)[0]
    Quartz.CGContextDrawImage(cv.ctx, Quartz.CGRectMake(0, 0, px, px), cg)
    im = cv.image("RGBA")
    label = 0
    if ok:
        _, label, _ = NSURL.fileURLWithPath_(path).getResourceValue_forKey_error_(None, AppKit.NSURLLabelNumberKey, None)
    if label == 1 or label in _TAG_HUE:
        # 系统给的图标没带标签色,照 Finder 的样子染:换色相,饱和度和亮度略降
        h, s, v = im.convert("RGB").convert("HSV").split()
        if label == 1:
            s = s.point(lambda x: 0)
        else:
            h = h.point(lambda x: round(_TAG_HUE[label] * 255))
            s = s.point(lambda x: round(x * 0.73))
        v = v.point(lambda x: round(x * 0.94))
        tinted = Image.merge("HSV", (h, s, v)).convert("RGB")
        tinted.putalpha(im.getchannel("A"))
        im = tinted
    return im


class Layout:
    def __init__(self, renderer, cols, rows, frame=True, fit=None, backdrop=None, cwd=None, proc=None):
        """cwd / proc:标题栏里的目录(图标 + 名字)和正在跑的程序,照 Terminal 的标题格式拼。"""
        r = self.r = renderer
        k = self.k = r.size / PT
        self.cols, self.rows = cols, rows
        self.frame = frame
        self.cwd, self.proc = cwd, proc
        self.folder = os.path.basename(os.path.normpath(cwd)) if cwd else None
        px = lambda pt: round(pt * k)  # noqa: E731
        self.bar = px(BAR + LINE) if frame else 0
        win_w = cols * r.cw + 2 * px(PAD_X)
        win_h = self.bar + px(PAD_TOP) + rows * r.ch + px(PAD_BOTTOM)
        margin = round(r.ch * 1.6) if frame else 0
        if fit:
            W, H = fit
        else:
            W, H = win_w + 2 * margin, win_h + 2 * margin
        W += W % 2
        H += H % 2
        self.size = (W, H)
        self.win = ((W - win_w) // 2, (H - win_h) // 2, win_w, win_h)
        wx, wy = self.win[0], self.win[1]
        self.origin = (wx + px(PAD_X), wy + self.bar + px(PAD_TOP))
        chrome = r.theme.chrome
        self.backdrop = backdrop or (chrome["backdrop"] if frame else r.theme.bg)
        self.base = self._base()
        self._titles = {}

    @staticmethod
    def needed(renderer, cols, rows, frame):
        r = renderer
        k = r.size / PT
        bar = round((BAR + LINE) * k) if frame else 0
        margin = round(r.ch * 1.6) if frame else 0
        w = cols * r.cw + 2 * round(PAD_X * k) + 2 * margin
        h = bar + round(PAD_TOP * k) + rows * r.ch + round(PAD_BOTTOM * k) + 2 * margin
        return w, h

    def _base(self):
        W, H = self.size
        r, k = self.r, self.k
        img = Image.new("RGB", (W, H), self.backdrop)
        wx, wy, ww, wh = self.win
        if not self.frame:
            ImageDraw.Draw(img).rectangle([wx, wy, wx + ww - 1, wy + wh - 1], fill=r.theme.bg)
            return img
        mask = _corner_mask(ww, wh, k)
        # 阴影
        sh = Image.new("L", (W, H), 0)
        off = round(r.ch * 0.35)
        sh.paste(mask.point(lambda v: v * 70 // 255), (wx, wy + off))
        sh = sh.filter(ImageFilter.GaussianBlur(r.ch * 0.8))
        img.paste((0, 0, 0), (0, 0, W, H), sh)
        # 窗口本体:标题栏 + 分隔线 + 红黄绿灯(CoreGraphics 画,和系统一样的抗锯齿)
        chrome = r.theme.chrome
        cv = _Canvas(ww, wh, r.theme.bg)
        top = lambda pt: wh - pt * k  # noqa: E731  从窗口顶往下 pt 点,换成左下原点的 y
        cv.fill(chrome["bar"], 0, top(BAR), ww, BAR * k)
        cv.fill(chrome["line"], 0, top(BAR + LINE), ww, LINE * k)
        ctx = cv.ctx
        for i, (fill, rim) in enumerate(LIGHTS):
            x = (LIGHT_X + i * LIGHT_GAP) * k
            rect = Quartz.CGRectMake(x, top(LIGHT_Y + LIGHT_D), LIGHT_D * k, LIGHT_D * k)
            cv.color(rim)
            Quartz.CGContextFillEllipseInRect(ctx, rect)
            cv.color(fill)
            Quartz.CGContextFillEllipseInRect(ctx, Quartz.CGRectInset(rect, 0.5 * k, 0.5 * k))
        dot, d = CLOSE_DOT
        c = (LIGHT_X + LIGHT_D / 2) * k
        cv.color(dot)
        Quartz.CGContextFillEllipseInRect(ctx, Quartz.CGRectMake(c - d * k / 2, top(LIGHT_Y + LIGHT_D / 2) - d * k / 2,
                                                                 d * k, d * k))
        img.paste(cv.image(), (wx, wy), mask)
        return img

    def title_text(self, text):
        """照 Terminal 的标题格式拼:目录 — 程序自己设的标题 — 正在跑的程序 — 列×行。"""
        parts = [p for p in (self.folder, text, self.proc) if p]
        return " — ".join(parts + [f"{self.cols}×{self.rows}"])

    def title_image(self, text):
        """标题栏里图标 + 文字那一条(按标题缓存)。"""
        if not self.frame:
            return None
        im = self._titles.get(text)
        if im is None:
            k = self.k
            wx, wy, ww, wh = self.win
            w = max(1, ww - round(ICON_X * k) - round(PAD_X * k))
            h = round(BAR * k)
            cv = _Canvas(w, h, self.r.theme.chrome["bar"])
            font = AppKit.NSFont.boldSystemFontOfSize_(TITLE_PT * k)
            x = (TITLE_X - ICON_X) * k
            ln = _line(self.title_text(text), font)
            if _width(ln) > w - x:
                token = _line("…", font)
                ln = CoreText.CTLineCreateTruncatedLine(ln, w - x, CoreText.kCTLineTruncationEnd, token) or ln
            cv.text(ln, self.r.theme.chrome["title"], x, h - TITLE_BASE * k)
            im = cv.image()
            icon = _folder_icon(self.cwd, round(ICON * k))
            im.paste(icon, (0, round(ICON_TOP * k)), icon)
            self._titles[text] = im
        return im

    def title_pos(self):
        return (self.win[0] + round(ICON_X * self.k), self.win[1])

    def row_pos(self, y):
        return (self.origin[0], self.origin[1] + y * self.r.ch)

    def badge(self, img, text):
        """右上角加速角标。"""
        r = self.r
        f = _ui_font(round(r.ch * 0.8), bold=True)
        tw = f.getlength(text)
        h = round(r.ch * 1.3)
        w = round(tw + r.ch * 1.2)
        wx, wy, ww, _ = self.win
        x1 = wx + ww - round(r.ch * 0.6)
        y0 = wy + round((LIGHT_Y + LIGHT_D / 2) * self.k) - h // 2 if self.frame else wy + round(r.ch * 0.4)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x1 - w, y0, x1, y0 + h], h // 2, fill=(30, 30, 30))
        d.text((x1 - w / 2, y0 + h / 2), text, font=f, fill=(255, 255, 255), anchor="mm")


def fit_renderer(cols, rows, fit, family, theme, frame, font_path=None):
    """找能塞进 fit 尺寸的最大字号。"""
    lo, hi, best = 6, 200, None
    while lo <= hi:
        mid = (lo + hi) // 2
        r = Renderer(mid, family, theme, font_path)
        w, h = Layout.needed(r, cols, rows, frame)
        if w <= fit[0] and h <= fit[1]:
            best, lo = r, mid + 1
        else:
            hi = mid - 1
    if best is None:
        raise SystemExit(f"{fit[0]}x{fit[1]} 放不下 {cols}x{rows} 的终端")
    return best
