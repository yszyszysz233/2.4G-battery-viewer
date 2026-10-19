# -*- coding: utf-8 -*-
"""
设备电量查看器 —— 读取 2.4G 无线外设（鼠标 / 键盘 / 耳机 / 手柄）的电量

用法：
    python app.py            启动图形界面
    python app.py --list     命令行列出所有 HID 设备与接口
    python app.py --battery  命令行读取所有设备电量
    python app.py --dump     命令行转储所有 Feature Report

零第三方依赖：只用 ctypes 调用 Windows 自带的 hid.dll / setupapi.dll。
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import traceback
from typing import Optional

import hid_win as hw
import battery as bt
import gm3323d_proto as gm
import bt_battery as btb
from hid_win import HidDevice, HidError, HidInterface

APP_TITLE = "设备电量查看器 · 2.4G 无线外设电池检测"
ACCENT = "#4f8cff"


# ==========================================================================
# 命令行模式
# ==========================================================================
def cli_list() -> None:
    devs = hw.enumerate_hid()
    print(f"共发现 {len(devs)} 个 HID 接口\n")
    print(f"{'VID:PID':>9}  {'UsagePage:Usage':>16}  {'IN':>3} {'OUT':>3} {'FEAT':>4}  "
          f"{'厂商':<24} 产品名")
    print("-" * 108)
    for d in sorted(devs, key=lambda x: (x.group, x.usage_page)):
        flag = " [系统独占]" if d.reserved else ""
        print(f"{d.vid_pid:>9}  {d.describe_usage():>16}  {d.input_len:>3} "
              f"{d.output_len:>3} {d.feature_len:>4}  {(d.manufacturer or '-')[:24]:<24} "
              f"{d.display_name}{flag}")


def cli_battery(listen: float = 3.0) -> None:
    devs = [d for d in hw.enumerate_hid()
            if gm.matches(d) or ((d.vid, d.pid) != (gm.VID, gm.PID)
                                and (d.is_vendor_page or d.num_feature_value_caps
                                     or d.num_input_value_caps))]
    print(f"检查 {len(devs)} 个候选接口……\n")
    for d in devs:
        attempts = bt.read_battery(d, listen_seconds=listen)
        best = bt.best_result(attempts)
        if best and best.ok:
            level = f"{best.level}%" if best.level is not None else "未知"
            print(f"[电量 {level}] {d.display_name} ({d.vid_pid}) "
                  f"{d.describe_usage()}  ← {best.source}")
            print(f"          {best.detail}")
        else:
            reason = best.detail if best else "没有可用的电量读取方式"
            print(f"[  --  ] {d.display_name} ({d.vid_pid}) {d.describe_usage()}  {reason}")

    bt_devs = btb.displayable_devices()
    print(f"\n蓝牙设备（HFP 电量）：")
    if not bt_devs:
        print("  未发现蓝牙电量节点（耳机需开机并连上电脑）")
    for d in bt_devs:
        lvl = f"{d.battery}%" if d.battery else "无数据"
        print(f"[电量 {lvl}] {d.name}  {d.reason}")


def cli_bt() -> None:
    print("=== 所有蓝牙 HID/音频节点 ===")
    for d in btb.list_bluetooth_devices():
        mark = "🔋" if (d.has_property and d.battery) else "  "
        print(f" {mark} {d.name}\n      电量={d.battery}  {d.reason}\n      {d.instance_id}")


def cli_dump() -> None:
    for d in hw.enumerate_hid():
        if not d.feature_len:
            continue
        rows = bt.dump_feature_reports(d)
        if not rows:
            continue
        print(f"\n== {d.display_name} ({d.vid_pid}) {d.describe_usage()} "
              f"FeatureReport 长度={d.feature_len}")
        for rid, data, note in rows:
            hexs = data.hex(" ") if data else ""
            print(f"   rid=0x{rid:02X}  {note:<10} {hexs[:180]}")


# ==========================================================================
# 图形界面
# ==========================================================================
def build_ui():
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext

    BG = "#1b1c1f"
    PANEL = "#232529"
    PANEL2 = "#2b2e33"
    FG = "#e8e8ea"
    MUTED = "#9aa0a6"
    FONT = ("Microsoft YaHei UI", 10)
    FONT_SMALL = ("Consolas", 9)

    root = tk.Tk()
    root.title(APP_TITLE)
    root.configure(bg=BG)
    root.geometry("1180x760")
    root.minsize(980, 620)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, font=FONT)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("Panel.TLabel", background=PANEL, foreground=FG)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("H1.TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 15, "bold"))
    style.configure("Big.TLabel", background=PANEL, foreground=ACCENT,
                    font=("Microsoft YaHei UI", 34, "bold"))
    style.configure("TButton", background=PANEL2, foreground=FG, borderwidth=0,
                    focusthickness=0, padding=(12, 7), font=FONT)
    style.map("TButton",
              background=[("active", "#3a3f47"), ("disabled", "#2a2c30")],
              foreground=[("disabled", "#6b7076")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#6ea1ff"), ("disabled", "#33455f")])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                    padding=(16, 8), font=FONT)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL2)],
              foreground=[("selected", FG)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=25, borderwidth=0, font=FONT)
    style.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9, "bold"), borderwidth=0)
    style.map("Treeview", background=[("selected", "#35507e")],
              foreground=[("selected", "#ffffff")])
    style.configure("TEntry", fieldbackground=PANEL2, background=PANEL2,
                    foreground=FG, insertcolor=FG, borderwidth=0)
    style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=FG)
    style.configure("TCheckbutton", background=PANEL, foreground=FG, font=FONT)
    style.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=PANEL,
                    borderwidth=0, arrowcolor=MUTED)

    # ---------------- 状态 ----------------
    state = {
        "devices": [],            # list[HidInterface]
        "by_iid": {},             # tree item id -> HidInterface
        "bt_by_iid": {},          # tree item id -> BluetoothDevice
        "reading": False,
        "monitor_stop": None,
        "monitor_thread": None,
    }
    ui_queue: "queue.Queue[tuple]" = queue.Queue()

    # ---------------- 顶部 ----------------
    header = ttk.Frame(root, padding=(18, 14, 18, 8))
    header.pack(fill="x")
    ttk.Label(header, text="设备电量查看器", style="H1.TLabel").pack(side="left")
    ttk.Label(header, text="  2.4G 无线外设 / HID 电量读取",
              style="Muted.TLabel").pack(side="left", pady=(6, 0))

    btn_scan = ttk.Button(header, text="① 扫描设备")
    btn_scan.pack(side="right", padx=(8, 0))
    btn_read = ttk.Button(header, text="② 读取选中设备电量", style="Accent.TButton")
    btn_read.pack(side="right")

    # ---------------- 主体 ----------------
    body = ttk.Frame(root, padding=(18, 6, 18, 6))
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=3, minsize=520)
    body.columnconfigure(1, weight=4)
    body.rowconfigure(0, weight=1)

    # 左：设备树
    left = ttk.Frame(body, style="Panel.TFrame", padding=1)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)

    cols = ("vidpid", "usage", "reports", "level")
    tree = ttk.Treeview(left, columns=cols, show="tree headings", selectmode="browse")
    tree.heading("#0", text="设备 / 接口")
    tree.heading("vidpid", text="VID:PID")
    tree.heading("usage", text="UsagePage:Usage")
    tree.heading("reports", text="IN/OUT/FEAT")
    tree.heading("level", text="电量")
    tree.column("#0", width=250, minwidth=180)
    tree.column("vidpid", width=80, anchor="center", stretch=False)
    tree.column("usage", width=120, anchor="center", stretch=False)
    tree.column("reports", width=90, anchor="center", stretch=False)
    tree.column("level", width=70, anchor="center", stretch=False)
    tree.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
    sb.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=sb.set)
    tree.tag_configure("device", foreground="#ffffff")
    tree.tag_configure("iface", foreground="#a9b0b8")
    tree.tag_configure("bt", foreground="#9ecbff")
    tree.tag_configure("hasbattery", foreground="#6ee7a8")

    # 右：详情
    right = ttk.Frame(body, style="Panel.TFrame", padding=1)
    right.grid(row=0, column=1, sticky="nsew")
    right.rowconfigure(0, weight=1)
    right.columnconfigure(0, weight=1)

    nb = ttk.Notebook(right)
    nb.grid(row=0, column=0, sticky="nsew")

    # --- Tab 1: 电量 ---
    tab_bat = ttk.Frame(nb, style="Panel.TFrame", padding=16)
    nb.add(tab_bat, text="电量")
    tab_bat.columnconfigure(0, weight=1)

    ttk.Label(tab_bat, text="当前设备", style="Panel.TLabel",
              foreground=MUTED, font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
    lab_dev = ttk.Label(tab_bat, text="（未选择）", style="Panel.TLabel",
                        font=("Microsoft YaHei UI", 12, "bold"))
    lab_dev.grid(row=1, column=0, sticky="w", pady=(0, 10))

    lab_level = ttk.Label(tab_bat, text="--", style="Big.TLabel")
    lab_level.grid(row=2, column=0, sticky="w")
    lab_status = ttk.Label(tab_bat, text="", style="Panel.TLabel",
                           font=("Microsoft YaHei UI", 11))
    lab_status.grid(row=3, column=0, sticky="w", pady=(0, 6))

    progress = ttk.Progressbar(tab_bat, mode="determinate", maximum=100,
                               style="Battery.Horizontal.TProgressbar")
    style.configure("Battery.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=PANEL2, borderwidth=0)
    progress.grid(row=4, column=0, sticky="ew", pady=(2, 14))

    txt_detail = scrolledtext.ScrolledText(tab_bat, height=16, font=FONT_SMALL,
                                           bg=PANEL2, fg=FG, insertbackground=FG,
                                           relief="flat", wrap="word")
    txt_detail.grid(row=5, column=0, sticky="nsew")
    tab_bat.rowconfigure(5, weight=1)
    txt_detail.configure(state="disabled")

    # --- Tab 2: 接口详情 ---
    tab_if = ttk.Frame(nb, style="Panel.TFrame", padding=12)
    nb.add(tab_if, text="接口详情")
    tab_if.rowconfigure(0, weight=1)
    tab_if.columnconfigure(0, weight=1)
    cols2 = ("page", "usage", "in", "out", "feat", "caps", "note")
    tree_if = ttk.Treeview(tab_if, columns=cols2, show="headings")
    for c, t, w in (("page", "UsagePage", 90), ("usage", "Usage", 90),
                    ("in", "IN", 50), ("out", "OUT", 50), ("feat", "FEAT", 55),
                    ("caps", "值能力表", 90), ("note", "说明", 220)):
        tree_if.heading(c, text=t)
        tree_if.column(c, width=w, anchor="center" if w < 120 else "w")
    tree_if.grid(row=0, column=0, sticky="nsew")

    # --- Tab 3: 原始探测 ---
    tab_raw = ttk.Frame(nb, style="Panel.TFrame", padding=12)
    nb.add(tab_raw, text="原始探测")
    tab_raw.columnconfigure(0, weight=1)
    tab_raw.rowconfigure(3, weight=1)

    row1 = ttk.Frame(tab_raw, style="Panel.TFrame")
    row1.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    btn_dump = ttk.Button(row1, text="转储全部 Feature Report")
    btn_dump.pack(side="left")
    btn_mon = ttk.Button(row1, text="开始监听输入报告")
    btn_mon.pack(side="left", padx=8)
    btn_rids = ttk.Button(row1, text="自动探测可用 Report ID")
    btn_rids.pack(side="left")

    v_rid_raw = tk.StringVar(value="08")
    v_cmd = tk.StringVar(value="")
    row2 = ttk.Frame(tab_raw, style="Panel.TFrame")
    row2.grid(row=1, column=0, sticky="ew", pady=(0, 6))
    ttk.Label(row2, text="Output Report ID(hex)", style="Panel.TLabel").pack(side="left")
    ttk.Entry(row2, textvariable=v_rid_raw, width=6).pack(side="left", padx=(6, 14))
    ttk.Label(row2, text="命令字节(hex，不含 Report ID)", style="Panel.TLabel").pack(side="left")
    ttk.Entry(row2, textvariable=v_cmd, width=34).pack(side="left", padx=(6, 14))
    btn_send = ttk.Button(row2, text="试发命令")
    btn_send.pack(side="left")

    ttk.Label(tab_raw, text="给不认识的设备用：先自动探出 Report ID，再试发命令看有没有回复，"
                            "最后到「手动标定」里把电量字节的位置填进去。",
              style="Panel.TLabel", foreground=MUTED,
              font=("Microsoft YaHei UI", 9)).grid(row=2, column=0, sticky="w", pady=(0, 8))

    txt_raw = scrolledtext.ScrolledText(tab_raw, height=18, font=FONT_SMALL,
                                        bg="#141517", fg="#b8f0c0",
                                        insertbackground=FG, relief="flat", wrap="none")
    txt_raw.grid(row=3, column=0, sticky="nsew")
    txt_raw.configure(state="disabled")

    # --- Tab 4: 手动标定 ---
    tab_cal = ttk.Frame(nb, style="Panel.TFrame", padding=12)
    nb.add(tab_cal, text="手动标定")
    tab_cal.columnconfigure(1, weight=1)

    ttk.Label(tab_cal, text="给不认识的设备定义读取方式，测试通过后保存，下次自动生效。",
              style="Panel.TLabel", foreground=MUTED,
              font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, columnspan=2,
                                                   sticky="w", pady=(0, 10))

    v_iface = tk.StringVar()
    v_kind = tk.StringVar(value="feature")
    v_rid = tk.StringVar(value="05")
    v_req = tk.StringVar(value="")
    v_len = tk.StringVar(value="64")
    v_off = tk.StringVar(value="9")
    v_scale = tk.StringVar(value="1")
    v_wait = tk.StringVar(value="150")
    v_chg = tk.StringVar(value="")
    v_name = tk.StringVar(value="我的设备")

    def field(row, label, var, hint=""):
        ttk.Label(tab_cal, text=label, style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        ttk.Entry(tab_cal, textvariable=var, width=34).grid(
            row=row, column=1, sticky="w", pady=3)
        if hint:
            ttk.Label(tab_cal, text=hint, style="Panel.TLabel", foreground=MUTED,
                      font=("Microsoft YaHei UI", 8)).grid(row=row, column=2,
                                                           sticky="w", padx=8)

    ttk.Label(tab_cal, text="接口", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=3)
    cb_iface = ttk.Combobox(tab_cal, textvariable=v_iface, width=60, state="readonly")
    cb_iface.grid(row=1, column=1, columnspan=2, sticky="we", pady=3)

    ttk.Label(tab_cal, text="报文类型", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=3)
    cb_kind = ttk.Combobox(tab_cal, textvariable=v_kind, width=20, state="readonly",
                           values=("feature", "output"))
    cb_kind.grid(row=2, column=1, sticky="w", pady=3)
    ttk.Label(tab_cal, text="feature=读 Feature Report；output=发 Output Report 再收 Input Report",
              style="Panel.TLabel", foreground=MUTED,
              font=("Microsoft YaHei UI", 8)).grid(row=2, column=2, sticky="w", padx=8)

    field(3, "Report ID (hex)", v_rid, "比如 05 / 0E / A1")
    field(4, "请求字节 (hex)", v_req, "不含 Report ID，比如：15 00 00 01")
    field(5, "报文总长度", v_len, "含 Report ID")
    field(6, "电量字节下标", v_off, "从 0 开始，含 Report ID 那一字节")
    field(7, "倍率", v_scale, "最终电量 = 该字节 × 倍率")
    field(8, "等待回复 (ms)", v_wait, "")
    field(9, "充电状态字节下标", v_chg, "可留空")
    field(10, "协议名称", v_name, "")

    row_b = ttk.Frame(tab_cal, style="Panel.TFrame")
    row_b.grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 8))
    btn_test = ttk.Button(row_b, text="测试读取", style="Accent.TButton")
    btn_test.pack(side="left")
    btn_save = ttk.Button(row_b, text="保存为协议")
    btn_save.pack(side="left", padx=8)
    btn_del = ttk.Button(row_b, text="清空自定义协议")
    btn_del.pack(side="left")

    txt_cal = scrolledtext.ScrolledText(tab_cal, height=12, font=FONT_SMALL,
                                        bg=PANEL2, fg=FG, insertbackground=FG,
                                        relief="flat", wrap="word")
    txt_cal.grid(row=12, column=0, columnspan=3, sticky="nsew")
    tab_cal.rowconfigure(12, weight=1)
    txt_cal.configure(state="disabled")

    # ---------------- 状态栏 ----------------
    status = ttk.Label(root, text="就绪", style="Muted.TLabel", padding=(18, 6))
    status.pack(fill="x", side="bottom")

    # ======================================================================
    # 工具函数
    # ======================================================================
    def set_text(widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def append_text(widget, content):
        widget.configure(state="normal")
        widget.insert("end", content + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def run_bg(fn, on_done=None):
        def worker():
            try:
                result = fn()
                if on_done:
                    ui_queue.put(("cb", on_done, result, None))
            except Exception as e:
                ui_queue.put(("err", None, e, traceback.format_exc()))
        threading.Thread(target=worker, daemon=True).start()

    def pump():
        try:
            while True:
                kind, cb, payload, tb = ui_queue.get_nowait()
                if kind == "cb" and cb:
                    cb(payload)
                elif kind == "err":
                    append_text(txt_detail, f"出错：{payload}\n{tb or ''}")
                    status.configure(text=f"出错：{payload}")
        except queue.Empty:
            pass
        root.after(120, pump)

    # ======================================================================
    # 扫描
    # ======================================================================
    def scan():
        status.configure(text="正在扫描 HID 设备……")
        btn_scan.configure(state="disabled")

        def done(devs):
            state["devices"] = devs
            tree.delete(*tree.get_children())
            state["by_iid"].clear()

            groups: dict[str, list[HidInterface]] = {}
            for d in devs:
                groups.setdefault(d.group, []).append(d)

            for gkey, items in sorted(groups.items(), key=lambda kv: kv[1][0].display_name):
                items.sort(key=lambda x: (x.usage_page, x.usage))
                name = hw.pick_device_name(items)
                head = items[0]
                vendor = next((i.manufacturer for i in items if i.manufacturer), "未知厂商")
                parent = tree.insert(
                    "", "end",
                    text=f"{name}",
                    values=(head.vid_pid, vendor, f"{len(items)} 个接口", ""),
                    open=len(items) <= 6, tags=("device",),
                )
                for d in items:
                    label = {
                        (0x0001, 0x0002): "鼠标", (0x0001, 0x0006): "键盘",
                        (0x0001, 0x0007): "小键盘", (0x0001, 0x0004): "摇杆",
                        (0x0001, 0x0005): "手柄", (0x0001, 0x0080): "系统控制",
                        (0x000C, 0x0001): "多媒体键", (0x000D, 0x0005): "精密触摸板",
                    }.get((d.usage_page, d.usage), "")
                    if gm.matches(d):
                        label = "GM3323D 电量接口（只读）"
                    elif d.is_vendor_page:
                        label = "厂商专用接口"
                    elif d.num_feature_value_caps or d.num_input_value_caps:
                        label = label or "含可解析字段"
                    flag = "（系统独占）" if d.reserved else ""
                    iid = tree.insert(
                        parent, "end",
                        text=f"  {label or '接口'} {flag}",
                        values=(d.vid_pid, d.describe_usage(),
                                f"{d.input_len}/{d.output_len}/{d.feature_len}", ""),
                        tags=("iface",),
                    )
                    state["by_iid"][iid] = d

            # ---- 蓝牙设备（电量来自 Windows 未公开的 HFP PnP 属性）----
            state["bt_by_iid"].clear()
            bt_root = tree.insert(
                "", "end", text="蓝牙设备（HFP 电量）",
                values=("", "", "", ""), open=True, tags=("device",),
            )
            bt_devs = btb.displayable_devices()
            if not bt_devs:
                tree.insert(bt_root, "end", tags=("iface",),
                            text="  未发现蓝牙电量节点（耳机需开机并连上电脑）",
                            values=("", "", "", ""))
            for bd in bt_devs:
                iid = tree.insert(
                    bt_root, "end", tags=("bt",),
                    text="  " + bd.name,
                    values=("", "蓝牙 HFP", "",
                            f"{bd.battery}%" if bd.battery else ""),
                )
                state["bt_by_iid"][iid] = bd

            btn_scan.configure(state="normal")
            status.configure(text=f"扫描完成：{len(groups)} 个 HID 设备 / {len(devs)} 个接口"
                                  f" / {len(bt_devs)} 个蓝牙电量节点")

        run_bg(hw.enumerate_hid, done)

    def selected_interface() -> Optional[HidInterface]:
        sel = tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid in state["by_iid"]:
            return state["by_iid"][iid]
        # 选中的是设备节点 → 交给调用方处理
        return None

    def selected_bt():
        sel = tree.selection()
        if sel and sel[0] in state["bt_by_iid"]:
            return state["bt_by_iid"][sel[0]]
        return None

    def selected_group_interfaces() -> list[HidInterface]:
        sel = tree.selection()
        if not sel:
            return []
        iid = sel[0]
        if iid in state["by_iid"]:
            return [state["by_iid"][iid]]
        return [state["by_iid"][c] for c in tree.get_children(iid) if c in state["by_iid"]]

    # ======================================================================
    # 详情展示
    # ======================================================================
    def show_details(ifaces: list[HidInterface]):
        tree_if.delete(*tree_if.get_children())
        if not ifaces:
            btdev = selected_bt()
            if btdev is not None:
                lab_dev.configure(text=f"{btdev.name}   （蓝牙 HFP）")
                set_text(txt_detail, "\n".join([
                    f"设备名：{btdev.name}",
                    f"节点：{btdev.instance_id}",
                    f"电量：{btdev.battery if btdev.battery is not None else '-'}",
                    f"说明：{btdev.reason}",
                ]))
            return
        for d in ifaces:
            tree_if.insert("", "end", values=(
                f"0x{d.usage_page:04X}", f"0x{d.usage:04X}",
                d.input_len, d.output_len, d.feature_len,
                f"{d.num_input_value_caps}/{d.num_feature_value_caps}",
                ("系统独占，无法发送指令" if d.reserved else "") + (d.error or ""),
            ))
        first = ifaces[0]
        lab_dev.configure(text=f"{first.display_name}   ({first.vid_pid})")
        lines = [f"厂商：{first.manufacturer or '未知'}",
                 f"VID:PID：{first.vid_pid}    版本：0x{first.version:04X}",
                 f"序列号：{first.serial or '-'}", ""]
        for d in ifaces:
            lines.append(f"接口 {d.describe_usage()}  "
                         f"IN={d.input_len} OUT={d.output_len} FEAT={d.feature_len}"
                         + ("  [系统独占]" if d.reserved else ""))
            lines.append(f"    {d.path}")
        set_text(txt_detail, "\n".join(lines))

    # ======================================================================
    # 读取电量
    # ======================================================================
    def read_bt_battery(btdev) -> None:
        """蓝牙设备：读 Windows 藏在 HFP 节点上的电量属性。"""
        if state["reading"]:
            return
        state["reading"] = True
        btn_read.configure(state="disabled")
        status.configure(text="正在读取蓝牙耳机电量……")
        set_text(txt_detail, "")
        lab_level.configure(text="--")
        lab_status.configure(text="")
        progress["value"] = 0

        def work():
            return btb.refresh(btdev)

        def done(d):
            state["reading"] = False
            btn_read.configure(state="normal")
            lab_dev.configure(text=f"{d.name}   （蓝牙 HFP）")
            if d.battery:
                lab_level.configure(text=f"{d.battery}%")
                lab_status.configure(text=f"来源：蓝牙 HFP 电量上报   {d.reason}")
                progress["value"] = d.battery
            else:
                lab_level.configure(text="未知")
                lab_status.configure(text=d.reason)
                progress["value"] = 0
            sel = tree.selection()
            if sel:
                vals = list(tree.item(sel[0], "values"))
                if len(vals) >= 4:
                    vals[3] = f"{d.battery}%" if d.battery else ""
                    tree.item(sel[0], values=vals)
            set_text(txt_detail, "\n".join([
                f"设备名：{d.name}",
                f"节点：{d.instance_id}",
                f"电量：{d.battery if d.battery is not None else '-'}",
                f"说明：{d.reason}",
                "",
                "这个数值是 Windows 蓝牙协议栈收到耳机上报后写入的未公开 PnP 属性：",
                "  {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2",
                "只有当耳机开机并连上电脑（走 HFP 免提通道）时才有值。",
            ]))
            status.configure(text="读取完成")

        run_bg(work, done)

    def read_battery():
        btdev = selected_bt()
        if btdev is not None:
            read_bt_battery(btdev)
            return
        ifaces = selected_group_interfaces()
        if not ifaces:
            status.configure(text="请先在左侧选择一个设备或接口")
            return
        if state["reading"]:
            return
        state["reading"] = True
        btn_read.configure(state="disabled")
        status.configure(text="正在读取电量……")
        set_text(txt_detail, "")
        lab_level.configure(text="--")
        lab_status.configure(text="")
        progress["value"] = 0

        # 优先尝试：厂商接口 → 有值能力表的接口 → 其它可读接口
        def rank(d: HidInterface):
            return (0 if d.is_vendor_page else 1,
                    0 if (d.num_feature_value_caps or d.num_input_value_caps) else 1,
                    1 if d.reserved else 0)

        gm_ifaces = [d for d in ifaces if gm.matches(d)]
        ordered = gm_ifaces or sorted(ifaces, key=rank)

        def work():
            log: list[str] = []
            best: Optional[bt.BatteryResult] = None
            for d in ordered:
                log.append(f"—— 接口 {d.describe_usage()} "
                           f"(IN={d.input_len} OUT={d.output_len} FEAT={d.feature_len})"
                           + ("【系统独占】" if d.reserved else ""))
                attempts = bt.read_battery(d, listen_seconds=3.0)
                if not attempts:
                    log.append("    没有可用的读取方式（无电量字段 / 无回复）")
                for a in attempts:
                    mark = "命中 ✔" if a.ok else "未命中"
                    log.append(f"    [{mark}] {a.source}：{a.detail}")
                if attempts and attempts[0].ok:
                    best = attempts[0]
                    break
            return best, log

        def done(result):
            best, log = result
            state["reading"] = False
            btn_read.configure(state="normal")
            if best and best.ok and best.level is not None:
                lab_level.configure(text=f"{best.level}%")
                lab_status.configure(text=f"{best.status_text}   来源：{best.source}")
                progress["value"] = best.level
                sel = tree.selection()
                if sel:
                    vals = list(tree.item(sel[0], "values"))
                    if len(vals) >= 4:
                        vals[3] = f"{best.level}%"
                        tree.item(sel[0], values=vals)
            elif best and best.ok:
                lab_level.configure(text="电压模式")
                lab_status.configure(text=f"{best.detail}   来源：{best.source}")
            else:
                lab_level.configure(text="未知")
                lab_status.configure(text="这台设备没有提供可读的电量（或需要手动标定）")
                progress["value"] = 0
            if best and best.raw:
                log.append("")
                log.append("原始报文：" + best.raw.hex(" "))
            set_text(txt_detail, "\n".join(log))
            status.configure(text="读取完成")

        run_bg(work, done)

    # ======================================================================
    # 原始探测
    # ======================================================================
    def dump_features():
        ifaces = selected_group_interfaces()
        if not ifaces:
            status.configure(text="请先选择设备")
            return
        set_text(txt_raw, "")
        status.configure(text="正在转储 Feature Report……")

        def work():
            out = []
            for d in ifaces:
                out.append(f"===== {d.display_name} {d.describe_usage()} "
                           f"FeatureReport 长度={d.feature_len}")
                rows = bt.dump_feature_reports(d)
                if not rows:
                    out.append("    （该接口没有 Feature Report）")
                for rid, data, note in rows:
                    out.append(f"  rid=0x{rid:02X}  {note}")
                    if data:
                        out.append("    " + data.hex(" "))
            return out

        def done(lines):
            set_text(txt_raw, "\n".join(lines))
            status.configure(text="转储完成")

        run_bg(work, done)

    def toggle_monitor():
        if state["monitor_thread"] and state["monitor_thread"].is_alive():
            state["monitor_stop"].set()
            btn_mon.configure(text="开始监听输入报告")
            status.configure(text="已停止监听")
            return
        iface = selected_interface() or (selected_group_interfaces() or [None])[0]
        if iface is None:
            status.configure(text="请先选择设备")
            return
        if not iface.input_len:
            status.configure(text=f"{iface.describe_usage()} 这个接口没有输入报告")
            return
        set_text(txt_raw, "")
        stop = threading.Event()
        state["monitor_stop"] = stop
        btn_mon.configure(text="停止监听")
        status.configure(text=f"正在监听 {iface.describe_usage()} 的输入报告……请操作一下设备")

        def work():
            dev = HidDevice(iface.path)
            try:
                decoded = []
                for cap in dev.value_caps(hw.HidP_Input):
                    if any(cap.UsagePage == p and cap.u.NotRange.Usage == u
                           for p, u, _ in hw.BATTERY_USAGES):
                        decoded.append(cap)
                if decoded:
                    ui_queue.put(("cb", lambda _=None: append_text(
                        txt_raw, "该接口声明了电量字段，命中会直接标出 →\n"), None, None))
                dev.start_reader()
                dev.drain()
                count = 0
                while not stop.is_set():
                    r = dev.read_input(timeout_ms=200, stop_flag=stop.is_set)
                    if not r:
                        continue
                    count += 1
                    line = f"[{count:>4}] {r.hex(' ')}"
                    for cap in decoded:
                        v = dev.usage_value(hw.HidP_Input, r, cap)
                        if v is not None:
                            line += (f"   ← 电量字段 UsagePage=0x{cap.UsagePage:04X} "
                                     f"Usage=0x{cap.u.NotRange.Usage:04X} 原始值={v} "
                                     f"换算={bt._scale(v, cap.LogicalMax)}%")
                    hits = [f"byte[{i}]=0x{b:02X}({b})" for i, b in enumerate(r)
                            if 1 <= b <= 100]
                    if hits and not decoded:
                        line += "   ← 疑似电量：" + ", ".join(hits[:6])
                    ui_queue.put(("cb", lambda s=line: append_text(txt_raw, s), None, None))
            finally:
                dev.close()
                ui_queue.put(("cb", lambda: btn_mon.configure(text="开始监听输入报告"), None, None))

        state["monitor_thread"] = threading.Thread(target=work, daemon=True)
        state["monitor_thread"].start()

    # ======================================================================
    # 手动标定
    # ======================================================================
    def probe_rids():
        iface = selected_interface() or (selected_group_interfaces() or [None])[0]
        if iface is None or not iface.output_len:
            status.configure(text="请选择一个带 Output Report 的接口")
            return
        append_text(txt_raw, f"正在探测 {iface.describe_usage()} 的可用 Report ID……")

        def work():
            ids = bt.probe_report_ids(iface)
            return ids

        def done(ids):
            if not ids:
                append_text(txt_raw, "  没有探测到可用的 Report ID（可能被系统独占或需要写权限）")
                status.configure(text="探测完成")
                return
            append_text(txt_raw, "  可用 Report ID：" + ", ".join(f"0x{i:02X}" for i in ids))
            v_rid_raw.set(f"{ids[0]:02X}")
            v_rid.set(f"{ids[0]:02X}")
            status.configure(text=f"探测完成，共 {len(ids)} 个可用 Report ID")

        run_bg(work, done)

    def send_cmd():
        iface = selected_interface() or (selected_group_interfaces() or [None])[0]
        if iface is None:
            status.configure(text="请先选择设备")
            return
        try:
            rid = int(v_rid_raw.get(), 16)
            payload = hexbytes(v_cmd.get())
        except Exception as e:
            append_text(txt_raw, f"参数有误：{e}")
            return
        append_text(txt_raw, f"\n试发 → {iface.describe_usage()}  ReportID=0x{rid:02X} "
                             f"payload={[hex(b) for b in payload]}")

        def work():
            replies = bt.send_command(iface, rid, payload, kind="output",
                                      wait_ms=400, repeat=2)
            return replies

        def done(replies):
            if not replies:
                append_text(txt_raw, "  没有回复（设备可能休眠 / 协议不对）")
            for i, r in enumerate(replies):
                if not r:
                    append_text(txt_raw, f"  第 {i+1} 次：发送或读取失败")
                    continue
                line = f"  第 {i+1} 次回复（{len(r)} 字节）：{r.hex(' ')}"
                hits = [f"byte[{j}]={b}" for j, b in enumerate(r) if 1 <= b <= 100]
                if hits:
                    line += "\n       疑似电量：" + ", ".join(hits[:8])
                append_text(txt_raw, line)

        run_bg(work, done)

    def hexbytes(text: str) -> list[int]:
        text = text.replace(",", " ").replace("0x", " ").replace("0X", " ")
        out = []
        for tok in text.split():
            out.append(int(tok, 16) & 0xFF)
        return out

    def build_spec() -> bt.ProtocolSpec:
        idx = cb_iface.current()
        if idx < 0:
            raise ValueError("请先选择接口")
        iface = state["cal_ifaces"][idx]
        return bt.ProtocolSpec(
            name=v_name.get() or "自定义协议",
            vid=iface.vid,
            usage_page=iface.usage_page,
            report_id=int(v_rid.get(), 16),
            payload=hexbytes(v_req.get()),
            length=int(v_len.get()),
            level_offset=int(v_off.get()),
            wait_ms=int(v_wait.get()),
            scale=int(v_scale.get()),
            charging_offset=int(v_chg.get()) if v_chg.get().strip() else None,
            notes="用户自定义（手动标定）",
        )

    def test_cal():
        try:
            spec = build_spec()
        except Exception as e:
            append_text(txt_cal, f"参数有误：{e}")
            return
        set_text(txt_cal, f"测试：{spec.name}  ReportID=0x{spec.report_id:02X} "
                          f"请求={spec.payload}\n")

        def work():
            iface = state["cal_ifaces"][cb_iface.current()]
            lines = []
            kind = "feature" if iface.feature_len else "output"
            try:
                with HidDevice(iface.path, write=True) as dev:
                    for i in range(2):
                        report = bt._read_report_after_request(dev, spec, kind)
                        lines.append(f"第 {i+1} 次回复（{len(report)} 字节）："
                                     + (report.hex(" ") if report else "（无回复）"))
                        if report:
                            res = bt._parse_spec_response(spec, report, iface, kind)
                            lines.append(f"    → 电量 = {res.level}%"
                                         f"（原始字节 {res.raw_level}）  状态={res.status_text}")
            except Exception as e:
                lines.append(f"出错：{e}")
            if kind == "output":
                lines.insert(0, "提示：该接口没有 Feature Report，已按 Output→Input 方式发送。")
            return lines

        run_bg(work, lambda lines: set_text(txt_cal, "\n".join(lines)))

    def save_cal():
        try:
            spec = build_spec()
        except Exception as e:
            append_text(txt_cal, f"参数有误：{e}")
            return
        bt.save_user_protocol(spec)
        append_text(txt_cal, f"已保存：{spec.name}（{bt.USER_PROTOCOL_FILE}）")

    def clear_cal():
        import os
        if os.path.exists(bt.USER_PROTOCOL_FILE):
            os.remove(bt.USER_PROTOCOL_FILE)
            append_text(txt_cal, "已清空所有自定义协议。")
        else:
            append_text(txt_cal, "没有自定义协议。")

    # ======================================================================
    # 事件绑定
    # ======================================================================
    def on_select(_event=None):
        ifaces = selected_group_interfaces()
        show_details(ifaces)
        state["cal_ifaces"] = ifaces
        cb_iface.configure(values=[f"{i}: {d.display_name} {d.vid_pid} "
                                    f"{d.describe_usage()} "
                                    f"(IN{d.input_len}/OUT{d.output_len}/FEAT{d.feature_len})"
                                    for i, d in enumerate(ifaces)])
        if ifaces:
            cb_iface.current(0)
            v_name.set(ifaces[0].display_name[:24])
            v_kind.set("feature" if ifaces[0].feature_len else "output")

    tree.bind("<<TreeviewSelect>>", on_select)
    tree.bind("<Double-1>", lambda e: read_battery())
    btn_scan.configure(command=scan)
    btn_read.configure(command=read_battery)
    btn_dump.configure(command=dump_features)
    btn_mon.configure(command=toggle_monitor)
    btn_rids.configure(command=probe_rids)
    btn_send.configure(command=send_cmd)
    btn_test.configure(command=test_cal)
    btn_save.configure(command=save_cal)
    btn_del.configure(command=clear_cal)

    state["cal_ifaces"] = []
    pump()
    root.after(200, scan)
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="2.4G 无线设备电量查看器")
    parser.add_argument("--list", action="store_true", help="列出所有 HID 设备与接口")
    parser.add_argument("--battery", action="store_true", help="读取所有设备电量")
    parser.add_argument("--bt", action="store_true", help="列出蓝牙设备及其电量")
    parser.add_argument("--dump", action="store_true", help="转储 Feature Report")
    parser.add_argument("--listen", type=float, default=3.0,
                        help="监听输入报告的秒数（配合 --battery）")
    args = parser.parse_args()

    if args.list:
        cli_list()
    elif args.battery:
        cli_battery(args.listen)
    elif args.bt:
        cli_bt()
    elif args.dump:
        cli_dump()
    else:
        try:
            root = build_ui()
        except Exception:
            print("无法启动图形界面，改用命令行模式。")
            traceback.print_exc()
            cli_list()
            return
        root.mainloop()


if __name__ == "__main__":
    main()
