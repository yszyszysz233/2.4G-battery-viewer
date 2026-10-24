# -*- coding: utf-8 -*-
"""我的设备电量 —— 小米 2.4G 鼠标 + 雷蛇耳机 + 赛睿耳机的简洁面板

用法：
    python my_devices.py            打开界面（默认自动刷新）
    python my_devices.py --once     命令行读一次就退出
    python my_devices.py --once -v  命令行读一次，并打印收发的原始报文（排错用）

界面有三块卡片，各显示一类设备的电量，可自动刷新。
读取逻辑在 devices.py，协议在 xiaomi_proto.py / razer_proto.py / steelseries_proto.py。
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Optional

import devices
import tray_common as tc
import tray_prefs as prefs
import win_link


# ==========================================================================
# 读取层（在 devices.py 里，这里只是转一下名字）
# ==========================================================================
Reading = devices.Reading
read_all = devices.read_all


# ==========================================================================
# 命令行模式
# ==========================================================================
def cli_once(verbose: bool = False) -> None:
    if verbose:
        import hid_win as hw
        import razer_proto
        import steelseries_proto
        import xiaomi_proto
        ifaces = hw.enumerate_hid()
        for mod in (xiaomi_proto, razer_proto, steelseries_proto):
            res = mod.read_battery(ifaces)
            print("\n".join(res.diag) or f"[{mod.__name__}] {res.detail}")
        print()
    for r in read_all():
        if r.ok:
            st = f"  {r.status}" if r.status else ""
            print(f"{r.title}: {r.level_text}{st}   {r.subtitle}   {r.detail}")
            if r.extra:
                print(f"    {r.extra}")
        else:
            head = f"{r.status}，电量暂不可读" if r.status else "读取失败"
            print(f"{r.title}: {head} —— {r.subtitle}  {r.error}")


# ==========================================================================
# 界面
# ==========================================================================
BG = "#1b1c1f"
PANEL = "#232529"
PANEL2 = "#2b2e33"
FG = "#e8e8ea"
MUTED = "#9aa0a6"
FONT = ("Microsoft YaHei UI", 10)

LEVEL_STYLE = [
    (40, "#5ddc8a", "电量充足"),
    (11, "#f5c451", "电量中等"),
    (0,  "#ff6b6b", "电量偏低"),
]


def level_color(level: Optional[int]) -> str:
    if level is None:
        return MUTED
    for threshold, color, _ in LEVEL_STYLE:
        if level >= threshold:
            return color
    return LEVEL_STYLE[-1][1]


class DeviceCard:
    """一块设备卡片。"""

    def __init__(self, parent, title: str):
        import tkinter as tk
        from tkinter import ttk

        self.frame = ttk.Frame(parent, style="Card.TFrame", padding=16)
        self.lab_title = ttk.Label(self.frame, text=title, style="CardTitle.TLabel")
        self.lab_title.pack(anchor="w")
        self.lab_sub = ttk.Label(self.frame, text="正在读取…", style="CardSub.TLabel")
        self.lab_sub.pack(anchor="w", pady=(2, 0))

        self.lab_level = ttk.Label(self.frame, text="--", style="CardBig.TLabel")
        self.lab_level.pack(anchor="w", pady=(10, 0))
        self.lab_status = ttk.Label(self.frame, text="", style="CardSub.TLabel")
        self.lab_status.pack(anchor="w")

        self.bar = ttk.Progressbar(self.frame, mode="determinate", maximum=100,
                                   style="Card.Horizontal.TProgressbar")
        self.bar.pack(fill="x", pady=(10, 8))

        self.lab_detail = ttk.Label(self.frame, text="", style="CardSub.TLabel",
                                    wraplength=230, justify="left")
        self.lab_detail.pack(anchor="w")
        self.lab_extra = ttk.Label(self.frame, text="", style="CardSub.TLabel",
                                   wraplength=230, justify="left")
        self.lab_extra.pack(anchor="w", pady=(4, 0))

    def update(self, r: Reading) -> None:
        self.lab_sub.configure(text=r.subtitle)
        if r.ok:
            self.lab_level.configure(text=r.level_text,
                                     foreground=level_color(r.level))
            st = r.status if r.status not in ("未知", "状态未知") else ""
            self.lab_status.configure(text=st)
            self.bar["value"] = r.level or 0
            self.lab_detail.configure(text=r.detail)
            self.lab_extra.configure(text=r.extra)
        else:
            self.lab_level.configure(text="--", foreground=MUTED)
            self.lab_status.configure(text=r.status)
            self.bar["value"] = 0
            self.lab_detail.configure(text=r.error)
            self.lab_extra.configure(text="")


def build_ui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(tc.UI_TITLE)
    root.configure(bg=BG)
    root.geometry("900x480")
    root.minsize(820, 450)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, font=FONT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("H1.TLabel", background=BG, foreground=FG,
                    font=("Microsoft YaHei UI", 15, "bold"))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
    style.configure("CardTitle.TLabel", background=PANEL, foreground=FG,
                    font=("Microsoft YaHei UI", 12, "bold"))
    style.configure("CardSub.TLabel", background=PANEL, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
    style.configure("CardBig.TLabel", background=PANEL, foreground="#5ddc8a",
                    font=("Microsoft YaHei UI", 40, "bold"))
    style.configure("Card.Horizontal.TProgressbar", background="#4f8cff",
                    troughcolor=PANEL2, borderwidth=0, thickness=8)
    style.configure("TButton", background=PANEL2, foreground=FG, borderwidth=0,
                    focusthickness=0, padding=(14, 7), font=FONT)
    style.map("TButton", background=[("active", "#3a3f47"), ("disabled", "#2a2c30")],
              foreground=[("disabled", "#6b7076")])
    style.configure("Accent.TButton", background="#4f8cff", foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#6ea1ff")])
    style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT)
    style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                    foreground=FG)

    state = {"busy": False, "auto": True, "interval": 60, "after_id": None,
             "starting": False, "started_at": 0.0}
    # Tkinter 不是线程安全的：后台线程只往队列里放结果，主线程统一取出来处理
    results: "queue.Queue[tuple]" = queue.Queue()

    cfg = tc.load_config()
    state["interval"] = int(cfg["interval"])

    # ---- 顶部 ----
    head = ttk.Frame(root, padding=(18, 16, 18, 8))
    head.pack(fill="x")
    ttk.Label(head, text=tc.UI_TITLE, style="H1.TLabel").pack(side="left")

    v_auto = tk.BooleanVar(value=True)
    v_interval = tk.StringVar(
        value=dict((s, l) for s, l in tc.INTERVAL_CHOICES).get(state["interval"], "1 分钟"))

    def on_auto():
        state["auto"] = v_auto.get()
        _reschedule()

    btn = ttk.Button(head, text="立即刷新", style="Accent.TButton")
    btn.pack(side="right")
    ttk.Checkbutton(head, text="自动刷新", variable=v_auto,
                    command=on_auto).pack(side="right", padx=(0, 12))

    v_tray_pct = tk.BooleanVar(value=bool(cfg.get("tray_show_percent", True)))

    def on_tray_pct():
        want = bool(v_tray_pct.get())
        cfg["tray_show_percent"] = want
        tc.save_config(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        if want and not tc.is_tray_running():
            # 勾选后若托盘未运行，后台拉起，否则看不到百分比图标
            state["starting"] = True
            state["started_at"] = time.time()
            btn_tray.configure(state="disabled", text="正在启动…")
            lab_tray.configure(text="正在启动托盘程序…")

            def work():
                try:
                    ok = tc.start_tray_app()
                except Exception:
                    traceback.print_exc()
                    ok = False
                results.put(("tray", ok))

            threading.Thread(target=work, daemon=True).start()
        notify("托盘将显示电量百分比" if want else "托盘恢复为仅图标（不显示百分比）")

    ttk.Checkbutton(head, text="在系统托盘显示电量百分比", variable=v_tray_pct,
                    command=on_tray_pct).pack(side="right", padx=(0, 12))

    # ---- 卡片 ----
    body = ttk.Frame(root, padding=(18, 4, 18, 4))
    body.pack(fill="both", expand=True)
    body.rowconfigure(0, weight=1)
    cards = []
    n = len(devices.TITLES)
    for col, title in enumerate(devices.TITLES):
        body.columnconfigure(col, weight=1, uniform="card")
        card = DeviceCard(body, title)
        card.frame.grid(row=0, column=col, sticky="nsew",
                        padx=(0 if col == 0 else 6, 0 if col == n - 1 else 6))
        cards.append(card)

    foot = ttk.Label(root, text="正在读取…", style="Muted.TLabel", padding=(18, 4))
    foot.pack(fill="x", side="bottom")

    # ---- 设置区 ----
    setbar = ttk.Frame(root, style="Panel.TFrame", padding=(14, 10))
    setbar.pack(fill="x", padx=18, pady=(2, 8))

    def refresh_tray_state():
        if state.get("starting"):
            return                              # 正在启动，别覆盖提示文字
        if state.get("notice") and time.time() < state.get("notice_until", 0):
            lab_tray.configure(text=state["notice"])   # 临时提示优先显示
            btn_tray.configure(state="normal",
                               text="退出托盘程序" if tc.is_tray_running()
                               else "启动托盘程序")
            return
        state["notice"] = ""
        running = tc.is_tray_running()
        btn_tray.configure(state="normal",
                           text="退出托盘程序" if running else "启动托盘程序")
        lab_tray.configure(text="托盘运行中：右键托盘图标即可打开本窗口"
                                if running else "托盘未运行：不会后台自动刷新")

    def notify(text: str, seconds: float = 8.0) -> None:
        """在状态标签上显示一条临时提示。"""
        state["notice"] = text
        state["notice_until"] = time.time() + seconds
        refresh_tray_state()

    def on_interval(_event=None):
        sec = dict((label, sec) for sec, label in tc.INTERVAL_CHOICES)[v_interval.get()]
        cfg["interval"] = sec
        tc.save_config(cfg)
        state["interval"] = sec
        tc.post_to_tray(tc.MSG_RELOAD)      # 让托盘立刻换间隔
        _reschedule()
        refresh()

    def on_boot():
        want = bool(v_boot.get())
        ok = tc.set_autostart(want)
        cfg["autostart"] = want
        tc.save_config(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        if want and not ok:
            notify("开机自启设置失败：无法创建启动文件夹快捷方式")
        elif want:
            notify("已加入开机自启（任务管理器 →「启动」标签里能看到 电量托盘）", 12)
        else:
            notify("已取消开机自启")

    def tray_done(ok: bool):
        state["starting"] = False
        refresh_tray_state()
        if not ok:
            notify("启动失败：可双击桌面「电量托盘」再试", 12)

    def on_links():
        btn_link.configure(state="disabled", text="正在创建…")

        def work():
            try:
                r = tc.install_desktop_links()
            except Exception as e:
                traceback.print_exc()
                r = {"folder": "", "links": {}, "errors": [str(e)]}
            results.put(("links", r))

        threading.Thread(target=work, daemon=True).start()

    def links_done(r: dict):
        import tkinter.messagebox as mb
        btn_link.configure(state="normal", text="重建桌面链接")
        bits = []
        kind = r.get("folder") or ""
        if kind:
            bits.append(f"文件夹→{win_link.KIND_LABEL.get(kind, kind)}")
        for name, k in (r.get("links") or {}).items():
            bits.append(f"{name}→{win_link.KIND_LABEL.get(k, k)}")
        if bits:
            notify("桌面链接已更新：" + "；".join(bits), 12)
        else:
            notify("桌面链接创建失败", 12)
        if r.get("errors"):
            mb.showwarning("桌面链接", "\n".join(r["errors"]))

    def on_tray_toggle():
        if tc.is_tray_running():
            tc.post_to_tray(tc.MSG_QUIT)
            for delay in (600, 1500, 3000):
                root.after(delay, refresh_tray_state)
            return

        # 启动要等托盘窗口真正出现（pythonw 是静默进程），放后台线程做，别卡界面
        state["starting"] = True
        state["started_at"] = time.time()
        btn_tray.configure(state="disabled", text="正在启动…")
        lab_tray.configure(text="正在启动托盘程序…")

        def work():
            try:
                ok = tc.start_tray_app()
            except Exception:
                traceback.print_exc()
                ok = False
            results.put(("tray", ok))          # 交给主线程处理

        threading.Thread(target=work, daemon=True).start()

    # 设置区：第一行放设置项，第二行放托盘相关按钮与状态
    row_a = ttk.Frame(setbar, style="Panel.TFrame")
    row_a.pack(fill="x")
    row_b = ttk.Frame(setbar, style="Panel.TFrame")
    row_b.pack(fill="x", pady=(9, 0))

    ttk.Label(row_a, text="设置", style="CardTitle.TLabel").pack(side="left")
    ttk.Label(row_a, text="刷新间隔", style="CardSub.TLabel").pack(side="left", padx=(16, 6))
    combo = ttk.Combobox(row_a, textvariable=v_interval, width=8, state="readonly",
                         values=[label for _, label in tc.INTERVAL_CHOICES])
    combo.pack(side="left")
    combo.bind("<<ComboboxSelected>>", on_interval)

    v_boot = tk.BooleanVar(value=tc.is_autostart_enabled())
    ttk.Checkbutton(row_a, text="开机自启（任务管理器可见）", variable=v_boot,
                    command=on_boot).pack(side="left", padx=(18, 0))

    v_start_min = tk.BooleanVar(value=bool(cfg.get("start_minimized", False)))

    def on_start_min():
        cfg["start_minimized"] = bool(v_start_min.get())
        tc.save_config(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        if v_start_min.get():
            notify("已开启：启动时直接最小化到托盘（需托盘在运行）", 10)
        else:
            notify("已关闭：启动时显示主界面")

    ttk.Checkbutton(row_a, text="开机自启直接最小化", variable=v_start_min,
                    command=on_start_min).pack(side="left", padx=(18, 0))

    v_close_tray = tk.BooleanVar(value=bool(cfg.get("close_to_tray", False)))

    def on_close_to_tray():
        want = bool(v_close_tray.get())
        cfg["close_to_tray"] = want
        tc.save_config(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        if want:
            if not tc.is_tray_running():
                state["starting"] = True
                state["started_at"] = time.time()
                btn_tray.configure(state="disabled", text="正在启动…")
                lab_tray.configure(text="正在启动托盘程序…")

                def work():
                    try:
                        ok = tc.start_tray_app()
                    except Exception:
                        traceback.print_exc()
                        ok = False
                    results.put(("tray", ok))

                threading.Thread(target=work, daemon=True).start()
            notify("关闭窗口将最小化到托盘；请在托盘右键菜单选「关闭程式」真正退出", 12)
        else:
            notify("关闭窗口将退出程序")

    ttk.Checkbutton(row_a, text="关闭ui=最小化到系统托盘", variable=v_close_tray,
                    command=on_close_to_tray).pack(side="left", padx=(18, 0))

    btn_tray = ttk.Button(row_b, text="退出托盘程序", command=on_tray_toggle)
    btn_tray.pack(side="right")
    btn_link = ttk.Button(row_b, text="重建桌面链接", command=on_links)
    btn_link.pack(side="right", padx=(0, 8))
    lab_tray = ttk.Label(row_b, text="", style="CardSub.TLabel")
    lab_tray.pack(side="right", padx=(0, 12))
    refresh_tray_state()

    # ---- 刷新逻辑 ----
    def apply(readings: list[Reading]):
        for card, r in zip(cards, readings):
            card.update(r)
        ok = sum(1 for r in readings if r.ok)
        foot.configure(text=f"更新于 {time.strftime('%H:%M:%S')}   "
                            f"成功读取 {ok}/{len(readings)} 个设备"
                            + ("" if state["auto"] else "   （自动刷新已关闭）"))
        state["busy"] = False
        btn.configure(state="normal")

    def worker():
        try:
            readings = read_all()
        except Exception:
            traceback.print_exc()
            readings = [Reading(t, "", error="读取异常") for t in devices.TITLES]
        results.put(("readings", readings))

    def refresh():
        if state["busy"]:
            return
        state["busy"] = True
        btn.configure(state="disabled")
        foot.configure(text="正在读取…（耳机的协议要几秒钟）")
        threading.Thread(target=worker, daemon=True).start()

    def _reschedule():
        if state["after_id"]:
            root.after_cancel(state["after_id"])
            state["after_id"] = None
        if state["auto"]:
            state["after_id"] = root.after(state["interval"] * 1000, _tick)

    def _tick():
        refresh()
        _reschedule()

    btn.configure(command=refresh)

    root.after(300, refresh)
    _reschedule()

    # 主线程统一处理后台线程的结果 + 跟随托盘状态
    def pump():
        try:
            while True:
                kind, payload = results.get_nowait()
                if kind == "readings":
                    apply(payload)
                elif kind == "tray":
                    tray_done(payload)
                elif kind == "links":
                    links_done(payload)
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
            state["busy"] = False
            state["starting"] = False
            btn.configure(state="normal")

        # 兜底：启动流程万一卡住，20 秒后放开按钮，别让它永久禁用
        if state["starting"] and time.time() - state["started_at"] > 20:
            state["starting"] = False
            refresh_tray_state()

        refresh_tray_state()        # FindWindowW 很便宜，顺带同步托盘状态
        root.after(1000, pump)

    root.after(1000, pump)
    # 托盘右键打开时保证窗口到前台
    def raise_me():
        try:
            root.lift()
            root.focus_force()
        except Exception:
            pass
    root.after(250, raise_me)

    def on_close():
        """关闭按钮：可选藏到托盘，而不是退出。"""
        if bool(v_close_tray.get()):
            if not tc.is_tray_running():
                try:
                    tc.start_tray_app()
                except Exception:
                    traceback.print_exc()
            try:
                root.withdraw()
            except Exception:
                pass
            notify("已最小化到托盘（右键托盘图标 → 关闭程式 可真正退出）", 10)
            return
        try:
            root.destroy()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", on_close)
    # 供 main() 启动时决定是否 withdraw
    root._battery_start_minimized = bool(cfg.get("start_minimized", False))  # type: ignore[attr-defined]
    root._battery_ensure_tray = tc.start_tray_app  # type: ignore[attr-defined]
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="我的设备电量")
    parser.add_argument("--once", action="store_true", help="命令行读一次就退出")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="配合 --once：打印收发的原始报文")
    parser.add_argument("--install-links", action="store_true",
                        help="在桌面重建软连接（项目文件夹 + 启动脚本）")
    parser.add_argument("--autostart", choices=["on", "off", "status"],
                        help="开机自启：on 打开 / off 关闭 / status 查看")
    parser.add_argument("--minimized", action="store_true",
                        help="启动时直接最小化到托盘（不显示主窗口）")
    args = parser.parse_args()

    if args.install_links:
        r = tc.install_desktop_links()
        print("桌面链接：")
        print(f"  项目文件夹 {tc.DESKTOP_LINK_NAME} -> "
              f"{win_link.KIND_LABEL.get(r['folder'], r['folder'] or '创建失败')}")
        for name, kind in r["links"].items():
            print(f"  {name} -> {win_link.KIND_LABEL.get(kind, kind)}")
        for msg in r["errors"]:
            print("  注意：", msg)
        return

    if args.autostart:
        if args.autostart == "status":
            st = tc.autostart_status()
            print("开机自启：", "已启用" if st["enabled"] else "未启用")
            print("  方式：", st["method"])
            print("  快捷方式：", st["shortcut"],
                  "(存在)" if st["shortcut_exists"] else "(不存在)")
            print("  任务管理器状态：", st["taskmanager_state"])
            print("  目标：", st["target"])
            print("  旧注册表项：", "还在" if st["legacy_registry"] else "已清理")
        else:
            ok = tc.set_autostart(args.autostart == "on")
            print("已" + ("加入" if args.autostart == "on" else "取消") + "开机自启",
                  "" if ok else "（失败）")
        return

    if args.once:
        cli_once(args.verbose)
        return

    # 面板单实例：已打开则恢复/置前台，绝不第二窗口
    ok, _ui_mutex = tc.claim_ui_single_instance()
    if not ok:
        if tc.activate_existing_ui():
            print("简洁界面已在运行，已切换到前台")
        else:
            print("简洁界面似乎已在运行（未能定位窗口）")
        return

    try:
        root = build_ui()
    except Exception:
        print("无法启动图形界面，改用命令行模式：")
        traceback.print_exc()
        cli_once()
        return

    cfg = tc.load_config()
    if prefs.should_start_minimized(cfg, argv_minimized=bool(args.minimized)):
        try:
            if not tc.is_tray_running():
                tc.start_tray_app()
            root.withdraw()
        except Exception:
            traceback.print_exc()
    root.mainloop()


if __name__ == "__main__":
    main()
