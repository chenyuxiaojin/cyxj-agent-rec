// rec-capture:按窗口 / 按 App 录真实画面(ScreenCaptureKit)。由 rec 调用,也能单独用。
//
//   rec-capture list [--json]
//   rec-capture record --out a.mov (--window-id N | --app 名字 [--title 字]) [--only-app] [--with App]...
//                      [--region x,y,w,h] [--pad 点] [--fps 30] [--cursor] [--started-file f]
//   rec-capture shot   --out a.png  (同上的选择参数)
//   rec-capture notify 通知名
//
// 只抓指定的窗口 / App,别的窗口不进画面;不动鼠标、不改焦点。
import AppKit
import CoreMedia
import Foundation
import ScreenCaptureKit
import UniformTypeIdentifiers

struct Opts {
    var out = ""
    var windowID: UInt32?
    var app: String?
    var title: String?
    var onlyApp = false
    var desktop = false
    var with: [String] = []
    var region: CGRect?
    var pad: CGFloat = 0
    var fps = 30
    var cursor = false
    var startedFile: String?
    var json = false

    init(_ a: [String]) throws {
        var i = 0
        func next() throws -> String {
            i += 1
            guard i < a.count else { throw Fail("\(a[i - 1]) 后面缺参数") }
            return a[i]
        }
        while i < a.count {
            switch a[i] {
            case "--out": out = try next()
            case "--window-id": windowID = UInt32(try next())
            case "--app": app = try next()
            case "--title": title = try next()
            case "--only-app": onlyApp = true
            case "--desktop": onlyApp = true; desktop = true
            case "--with": with.append(try next())
            case "--region":
                let p = try next().split(separator: ",").compactMap { Double($0) }
                guard p.count == 4 else { throw Fail("--region 写成 x,y,w,h(点,左上角为原点)") }
                region = CGRect(x: p[0], y: p[1], width: p[2], height: p[3])
            case "--pad": pad = CGFloat(Double(try next()) ?? 0)
            case "--fps": fps = Int(try next()) ?? 30
            case "--cursor": cursor = true
            case "--started-file": startedFile = try next()
            case "--json": json = true
            default: throw Fail("不认识的参数 \(a[i])")
            }
            i += 1
        }
    }
}

struct Fail: Error, CustomStringConvertible {
    let description: String
    init(_ s: String) { description = s }
}

func matches(_ w: SCWindow, _ o: Opts) -> Bool {
    if let id = o.windowID { return w.windowID == id }
    if let app = o.app?.lowercased() {
        let name = (w.owningApplication?.applicationName ?? "").lowercased()
        let bid = (w.owningApplication?.bundleIdentifier ?? "").lowercased()
        if !(name.contains(app) || bid.contains(app)) { return false }
    }
    if let t = o.title, !(w.title ?? "").contains(t) { return false }
    return o.app != nil || o.title != nil
}

func appMatches(_ a: SCRunningApplication, _ key: String) -> Bool {
    let k = key.lowercased()
    return a.applicationName.lowercased().contains(k) || a.bundleIdentifier.lowercased().contains(k)
}

/// 选定要抓的东西,返回 (过滤器, 画面区域(点), 像素倍率)
func makeFilter(_ o: Opts) async throws -> (SCContentFilter, CGRect?, CGFloat) {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    let hits = content.windows.filter { matches($0, o) }
    guard !hits.isEmpty else { throw Fail("没找到符合条件的窗口(rec-capture list 看看有哪些)") }

    if !o.onlyApp {
        // 单窗口:被别的窗口挡住也照样录
        let w = hits.filter { $0.isOnScreen }.max { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }
            ?? hits[0]
        let f = SCContentFilter(desktopIndependentWindow: w)
        return (f, nil, CGFloat(f.pointPixelScale))
    }

    // 整个 App(可再加别的 App、桌面壁纸和菜单栏):只合成这些窗口,其它窗口一律不进画面
    guard let owner = hits.first?.owningApplication else { throw Fail("找不到窗口所属的 App") }
    let onScreen = hits.filter { $0.isOnScreen }
    var area = o.region ?? onScreen.reduce(CGRect.null) { $0.union($1.frame) }
    if area.isNull { throw Fail("这个 App 现在没有在屏幕上的窗口") }
    area = area.insetBy(dx: -o.pad, dy: -o.pad)
    let display = content.displays.first { $0.frame.intersects(area) } ?? content.displays[0]
    let local = area.offsetBy(dx: -display.frame.minX, dy: -display.frame.minY)
        .intersection(CGRect(origin: .zero, size: display.frame.size))

    let visible = content.windows.filter { $0.isOnScreen && $0.frame.intersects(display.frame) }
    var keep = visible.filter { $0.owningApplication?.processID == owner.processID }
    for key in o.with {
        let more = visible.filter { w in w.owningApplication.map { appMatches($0, key) } ?? false }
        if more.isEmpty { throw Fail("没找到 App「\(key)」在屏幕上的窗口") }
        keep += more
    }
    if o.desktop {
        keep += visible.filter(isDesktop)
    }
    let f = SCContentFilter(display: display, including: keep)
    return (f, local, CGFloat(f.pointPixelScale))
}

/// 桌面背景:壁纸、菜单栏、菜单栏右侧图标
func isDesktop(_ w: SCWindow) -> Bool {
    let bid = w.owningApplication?.bundleIdentifier ?? ""
    let title = w.title ?? ""
    if bid == "com.apple.wallpaper.agent" { return true }
    if bid == "com.apple.dock" && title.hasPrefix("Wallpaper") { return true }
    if w.owningApplication == nil && (title == "Menubar" || title == "underbelly") { return true }
    return w.windowLayer == 25 && w.frame.minY <= 1 && w.frame.height <= 60
}

func makeConfig(_ f: SCContentFilter, _ rect: CGRect?, _ scale: CGFloat, _ o: Opts) -> SCStreamConfiguration {
    let c = SCStreamConfiguration()
    let size = rect?.size ?? f.contentRect.size
    // H.264 要求偶数宽高
    c.width = Int(size.width * scale) / 2 * 2
    c.height = Int(size.height * scale) / 2 * 2
    if let r = rect { c.sourceRect = r }
    c.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(o.fps))
    c.showsCursor = o.cursor
    c.capturesAudio = false
    c.queueDepth = 6
    c.colorSpaceName = CGColorSpace.sRGB
    return c
}

final class RecDelegate: NSObject, SCRecordingOutputDelegate {
    var started: ((Double) -> Void)?
    var finished: ((Error?) -> Void)?
    func recordingOutputDidStartRecording(_ r: SCRecordingOutput) { started?(Date().timeIntervalSince1970) }
    func recordingOutput(_ r: SCRecordingOutput, didFailWithError e: Error) { finished?(e) }
    func recordingOutputDidFinishRecording(_ r: SCRecordingOutput) { finished?(nil) }
}

func record(_ o: Opts) async throws {
    guard !o.out.isEmpty else { throw Fail("缺 --out") }
    let (filter, rect, scale) = try await makeFilter(o)
    let config = makeConfig(filter, rect, scale, o)
    let stream = SCStream(filter: filter, configuration: config, delegate: nil)
    let rc = SCRecordingOutputConfiguration()
    rc.outputURL = URL(fileURLWithPath: o.out)
    rc.outputFileType = o.out.lowercased().hasSuffix(".mp4") ? .mp4 : .mov
    rc.videoCodecType = .h264
    let delegate = RecDelegate()
    let output = SCRecordingOutput(configuration: rc, delegate: delegate)
    try stream.addRecordingOutput(output)

    let done = AsyncStream<Error?> { cont in
        delegate.finished = { cont.yield($0); cont.finish() }
    }
    delegate.started = { t in
        if let f = o.startedFile { try? String(format: "%.6f", t).write(toFile: f, atomically: true, encoding: .utf8) }
        print("开始录:\(config.width)x\(config.height) → \(o.out)")
        fflush(stdout)
    }
    try await stream.startCapture()

    // 等 SIGINT / SIGTERM 再收尾,保证文件写完整
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let stop = AsyncStream<Void> { cont in
        for s in [SIGINT, SIGTERM] {
            let src = DispatchSource.makeSignalSource(signal: s, queue: .main)
            src.setEventHandler { cont.yield(); cont.finish() }
            src.resume()
            objc_setAssociatedObject(delegate, "sig\(s)", src, .OBJC_ASSOCIATION_RETAIN)
        }
    }
    for await _ in stop { break }
    try await stream.stopCapture()
    for await err in done {
        if let e = err { throw e }
        break
    }
    print("已保存:\(o.out)")
}

func shot(_ o: Opts) async throws {
    guard !o.out.isEmpty else { throw Fail("缺 --out") }
    let (filter, rect, scale) = try await makeFilter(o)
    let config = makeConfig(filter, rect, scale, o)
    let img = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    let url = URL(fileURLWithPath: o.out) as CFURL
    guard let dest = CGImageDestinationCreateWithURL(url, UTType.png.identifier as CFString, 1, nil) else {
        throw Fail("写不了 \(o.out)")
    }
    CGImageDestinationAddImage(dest, img, nil)
    CGImageDestinationFinalize(dest)
    print(o.out)
}

func list(_ o: Opts) async throws {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    let rows = content.windows
        .filter { $0.frame.width >= 20 && $0.frame.height >= 20 }
        .sorted { ($0.isOnScreen ? 0 : 1, $0.owningApplication?.applicationName ?? "") < ($1.isOnScreen ? 0 : 1, $1.owningApplication?.applicationName ?? "") }
    if o.json {
        let arr: [[String: Any]] = rows.map { w in
            ["id": w.windowID, "app": w.owningApplication?.applicationName ?? "", "bundle": w.owningApplication?.bundleIdentifier ?? "",
             "title": w.title ?? "", "onScreen": w.isOnScreen, "layer": w.windowLayer,
             "frame": [w.frame.minX, w.frame.minY, w.frame.width, w.frame.height]]
        }
        let data = try JSONSerialization.data(withJSONObject: arr, options: [.prettyPrinted])
        print(String(data: data, encoding: .utf8)!)
        return
    }
    for w in rows {
        let f = w.frame
        print(String(format: "%6u  %@  %-22@  %5.0fx%-5.0f @%5.0f,%-5.0f  层%-3d  %@", w.windowID,
                     w.isOnScreen ? "显示中" : "隐藏  ", (w.owningApplication?.applicationName ?? "?") as NSString,
                     f.width, f.height, f.minX, f.minY, w.windowLayer, (w.title ?? "") as NSString))
    }
}

let args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else {
    fputs("用法:rec-capture list|record|shot|notify …\n", stderr)
    exit(64)
}
// 让进程连上窗口服务器(命令行程序默认不连)
_ = NSApplication.shared
let task = Task {
    do {
        switch cmd {
        case "list": try await list(Opts(Array(args.dropFirst())))
        case "record": try await record(Opts(Array(args.dropFirst())))
        case "shot": try await shot(Opts(Array(args.dropFirst())))
        case "notify":
            guard args.count >= 2 else { throw Fail("notify 后面跟通知名") }
            DistributedNotificationCenter.default().postNotificationName(
                NSNotification.Name(args[1]), object: nil, userInfo: nil, deliverImmediately: true)
        default: throw Fail("不认识的命令 \(cmd)")
        }
        exit(0)
    } catch {
        fputs("rec-capture: \(error)\n", stderr)
        exit(1)
    }
}
RunLoop.main.run()
