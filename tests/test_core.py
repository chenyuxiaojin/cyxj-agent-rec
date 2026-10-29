import unittest

from agentrec.export import plan_timeline
from agentrec.term import EscapeFilter, Terminal


class TermTest(unittest.TestCase):
    def test_private_csi_stripped(self):
        # \e[>4;2m 不能被当成下划线,\e[<u 的 u 不能被打出来,拆在两块里也一样
        t = Terminal(20, 2)
        t.feed("\x1b[>4;2m\x1b[<")
        t.feed("uab")
        self.assertEqual(t.screen.display[0].rstrip(), "ab")
        self.assertFalse(t.row(0)[0].underscore)

    def test_dim(self):
        t = Terminal(20, 2)
        t.feed("\x1b[2ma\x1b[22mb\x1b[2;38;2;1;2;3mc\x1b[0md")
        cells = t.row(0)
        self.assertEqual(cells[0].fg, "~default")
        self.assertEqual(cells[1].fg, "default")
        self.assertEqual(cells[2].fg, "~010203")  # 38;2 里的 2 不算 dim 开关
        self.assertEqual(cells[3].fg, "default")

    def test_colon_sgr(self):
        f = EscapeFilter()
        self.assertEqual(f("\x1b[4:3mx\x1b[38:2::10:20:30my"), "\x1b[4mx\x1b[38;2;10;20;30my")

    def test_wide(self):
        t = Terminal(10, 1)
        t.feed("中a")
        cells = t.row(0)
        self.assertEqual((cells[0].data, cells[1].data, cells[2].data), ("中", "", "a"))

    def test_question_csi(self):
        # Claude Code 在 Terminal.app 下会发 \e[?6n;pyte 原版遇到带 ? 的 n/A/G 等直接报错
        t = Terminal(20, 1)
        t.feed("a\x1b[?6nb\x1b[?5Ac\x1b[?25ld")
        self.assertEqual(t.screen.display[0].rstrip(), "abcd")


class RenderTest(unittest.TestCase):
    def test_terminal_cell(self):
        # 11pt 的 Retina 两倍图:格子和真实 Terminal.app 截图一致(14×28,基线在第 22 像素)
        from agentrec.render import Renderer
        r = Renderer(22)
        self.assertEqual((r.cw, r.ch, r.ascent), (14, 28, 22))
        t = Terminal(4, 1)
        t.feed("中a")
        self.assertEqual(r.render_row(t.row(0)).size, (56, 28))


class TimelineTest(unittest.TestCase):
    def test_idle_and_speedup(self):
        fps, rows = 10, 3
        snaps, k = [], 0
        # 0–2s 有输出,2–12s 只有第 0 行在转圈,12–22s 完全不动
        for i in range(220):
            if i < 20:
                ids = (i, i, i)
            elif i < 120:
                ids = (1000 + i % 4, 19, 19)
            else:
                ids = (1003, 19, 19)
            snaps.append((ids, (0, 0, True), ""))
        speed = {"factor": 8, "min_wait": 4, "max_len": 0, "max_rows": 1}
        frames, rate, flags, out_at, segs = plan_timeline(snaps, fps, rows, [], 2.0, speed)
        kinds = sorted(s["kind"] for s in segs)
        self.assertEqual(kinds, ["idle", "speed"])
        total = len(frames) / fps
        # 前后各看 1 秒,所以转圈从第 3 秒才算「在等」:
        # 0–4s 原速(含加速段的头 1 秒)+ 4–12.5s 共 8.5s 按 8× + 之后不动的 9.5s 压到 2s
        self.assertAlmostEqual(total, 4 + 8.5 / 8 + 2, delta=0.15)


if __name__ == "__main__":
    unittest.main()
