# -*- coding: utf-8 -*-
"""我的设备电量 — 可切换设备卡片、实时电量及托盘控制。"""
from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import device_service as ds
import tray_common as tc

BG = "#101517"
PANEL = "#192124"
FIELD = "#222d31"
LINE = "#2b383c"
FG = "#eef4f2"
MUTED = "#8d9f9f"
ACCENT = "#8ee1bd"
FONT = ("Microsoft YaHei UI", -14)


def level_color(level):
    if level is None:
        return MUTED
    return ACCENT if level >= 60 else "#e8c579" if level >= 30 else "#f3a18f"


def label(parent, text="", size=14, color=FG, bg=None, **kwargs):
    return tk.Label(parent, text=text, font=("Microsoft YaHei UI", -size),
                    fg=color, bg=bg or parent.cget("bg"), **kwargs)


class DeviceCard:
    def __init__(self, parent, slot, on_select):
        self.frame = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        self.content = tk.Frame(self.frame, bg=PANEL)
        self.content.pack(fill="both", expand=True, padx=25, pady=23)
        top = tk.Frame(self.content, bg=PANEL)
        top.pack(fill="x")
        label(top, f"设备 0{slot + 1}", 12, MUTED).pack(side="left")
        self.badge = label(top, "未选择", 12, MUTED, bg=FIELD, padx=10, pady=4)
        self.badge.pack(side="right")
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(self.content, state="readonly", textvariable=self.choice,
                                  style="Device.TCombobox", font=("Microsoft YaHei UI", -16))
        self.combo.pack(fill="x", pady=(20, 9), ipady=5)
        self.combo.bind("<<ComboboxSelected>>", lambda _: on_select(slot, self.choice.get()))
        self.connection = label(self.content, "选择你想关注的设备", 12, MUTED)
        self.connection.pack(anchor="w")
        hero = tk.Frame(self.content, bg=PANEL)
        hero.pack(fill="x", pady=(12, 3))
        self.number = label(hero, "—", 76)
        self.number.pack(side="left")
        self.percent = label(hero, "", 25, MUTED)
        self.percent.pack(side="left", anchor="s", pady=(0, 16))
        self.battery = tk.Canvas(hero, bg=PANEL, width=100, height=70, highlightthickness=0)
        self.battery.pack(side="right", padx=(4, 0))
        self.status = label(self.content, "等待选择设备", 14, MUTED)
        self.status.pack(anchor="w")
        self.bar = tk.Canvas(self.content, bg=PANEL, height=7, highlightthickness=0)
        self.bar.pack(fill="x", pady=(18, 12))
        self.detail = label(self.content, "在上方的列表中选择设备，即可查看电量。", 13, MUTED,
                            justify="left", anchor="nw", wraplength=330)
        self.detail.pack(fill="both", expand=True)
        self.updated = label(self.content, "", 12, MUTED)
        self.updated.pack(anchor="w", pady=(15, 0))
        self.level = None
        self.bar.bind("<Configure>", lambda _: self.draw())
        self.content.bind("<Configure>", lambda e: self.detail.configure(wraplength=max(160, e.width)))
        self.draw()

    def set_options(self, options, key, missing_title=""):
        self.combo["values"] = list(options)
        fallback = f"{missing_title or key} · 未连接" if key else "不显示设备"
        self.choice.set(next((name for name, value in options.items() if value == key), fallback))

    def draw(self):
        color = level_color(self.level)
        self.bar.delete("all")
        width = max(1, self.bar.winfo_width())
        self.bar.create_line(3, 3, width - 3, 3, fill=LINE, width=6, capstyle="round")
        if self.level is not None and self.level > 0:
            self.bar.create_line(3, 3, max(3, (width - 3) * self.level / 100), 3,
                                 fill=color, width=6, capstyle="round")
        self.battery.delete("all")
        self.battery.create_rectangle(7, 17, 86, 53, outline=color, width=2)
        self.battery.create_rectangle(89, 27, 94, 43, outline="", fill=color)
        if self.level is not None:
            self.battery.create_rectangle(12, 22, 12 + round(69 * self.level / 100), 48,
                                           outline="", fill=color)
        else:
            self.battery.create_line(33, 35, 60, 35, fill=LINE, width=3)

    def loading(self, key):
        self.level = None
        self.number.configure(text="—", fg=FG)
        self.percent.configure(text="")
        self.badge.configure(text="读取中" if key else "未选择", fg=MUTED)
        self.status.configure(text="正在读取设备…" if key else "等待选择设备", fg=MUTED)
        self.detail.configure(text="正在连接，请稍候。" if key else "在上方的列表中选择设备，即可查看电量。")
        self.updated.configure(text="")
        self.connection.configure(text="正在连接所选设备…" if key else "这个位置可以留空，也可以随时切换设备")
        self.draw()

    def update(self, reading):
        if reading is None:
            self.loading("")
            return
        self.level = reading.level
        self.connection.configure(text=reading.subtitle)
        if reading.ok:
            color = level_color(reading.level)
            self.number.configure(text=str(reading.level), fg=FG)
            self.percent.configure(text="%")
            badge = "充电中" if reading.status.startswith("充电") else (
                "电量充足" if reading.level >= 60 else "电量中等" if reading.level >= 30 else "电量偏低")
            self.badge.configure(text=badge, fg=color)
            self.status.configure(text=reading.status or "已连接", fg=color)
            hint = "电量充足，随时就绪。" if reading.level >= 60 else (
                "电量正常，可以继续使用。" if reading.level >= 30 else "电量偏低，方便时给设备充电。")
            if reading.status.startswith("充电"):
                hint = "设备正在充电，电量会自动更新。"
            self.detail.configure(text=hint)
            self.updated.configure(text=f"最近更新  {time.strftime('%H:%M:%S')}")
        else:
            self.number.configure(text="—", fg=MUTED)
            self.percent.configure(text="")
            pending = reading.state == "unsupported"
            self.badge.configure(text="待适配" if pending else "未连接" if reading.state == "offline" else "暂无电量", fg=MUTED)
            self.status.configure(text="已发现设备" if pending else "暂时无法读取", fg=MUTED)
            self.detail.configure(text="\n".join(v for v in (reading.error, reading.extra) if v))
            self.updated.configure(text="完成协议适配后即可读取" if pending else "移动鼠标或重新连接后刷新")
        self.draw()


class Dashboard:
    def __init__(self, root):
        self.root = root
        self.cfg = tc.load_config()
        self.slots = list(self.cfg["selected_devices"] or ["", ""])
        self.initialize_selection = self.cfg["selected_devices"] is None
        self.devices = {}
        self.options = {"不显示设备": ""}
        self.results = queue.Queue()
        self.generation = 0
        self.busy = self.pending = self.tray_starting = False
        self.timer = None
        self.settings = None
        self.device_window = None
        self.tray_vars = {}
        self.device_inventory = None
        root.title(tc.UI_TITLE)
        root.configure(bg=BG)
        root.geometry("1000x720")
        root.minsize(920, 680)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.setup_style()
        self.build()
        root.bind("<F5>", lambda _: self.refresh())
        root.after(150, self.refresh)
        root.after(150, self.pump)
        self.schedule()

    def setup_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TButton", font=FONT, padding=(15, 9), background=FIELD,
                        foreground=FG, borderwidth=0, focusthickness=0)
        style.map("TButton", background=[("active", "#314246"), ("disabled", PANEL)],
                  foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG)
        style.map("Accent.TButton", background=[("active", "#b0f0d2"), ("disabled", FIELD)],
                  foreground=[("disabled", MUTED)])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=FG, arrowcolor=MUTED, bordercolor=LINE, padding=(10, 7))
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        style.configure("Device.TCombobox", padding=(12, 9))
        style.configure("TCheckbutton", background=BG, foreground=MUTED, font=FONT)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG)])
        style.configure("Device.TCheckbutton", background=PANEL, foreground=FG)
        style.map("Device.TCheckbutton", background=[("active", PANEL)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#3a5e51")
        self.root.option_add("*TCombobox*Listbox.font", FONT)

    def build(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=32, pady=(27, 22))
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        label(brand, tc.UI_TITLE, 28, ACCENT).pack(anchor="w")
        label(brand, "你的无线设备，一目了然。", 13, MUTED).pack(anchor="w", pady=(4, 0))
        actions = tk.Frame(header, bg=BG)
        actions.pack(side="right")
        self.refresh_button = ttk.Button(actions, text="立即刷新", style="Accent.TButton", command=self.refresh)
        self.refresh_button.pack(side="right")
        ttk.Button(actions, text="设备与托盘", command=self.show_devices).pack(side="right", padx=(0, 10))
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x", padx=32)
        intro = tk.Frame(self.root, bg=BG)
        intro.pack(fill="x", padx=32, pady=(21, 15))
        label(intro, "我的设备", 18).pack(side="left")
        self.summary = label(intro, "正在寻找设备…", 12, MUTED)
        self.summary.pack(side="right")
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=32)
        body.columnconfigure((0, 1), weight=1, uniform="card")
        body.rowconfigure(0, weight=1)
        self.cards = [DeviceCard(body, slot, self.select) for slot in range(2)]
        for slot, card in enumerate(self.cards):
            card.frame.grid(row=0, column=slot, sticky="nsew", padx=(0, 9) if slot == 0 else (9, 0))
            card.set_options(self.options, "")
        controls = tk.Frame(self.root, bg=BG)
        controls.pack(fill="x", padx=32, pady=(20, 12))
        self.auto = tk.BooleanVar(value=self.cfg["auto_refresh"])
        ttk.Checkbutton(controls, text="自动刷新", variable=self.auto, command=self.on_auto).pack(side="left")
        self.interval = tk.StringVar(value=self.interval_label())
        combo = ttk.Combobox(controls, state="readonly", textvariable=self.interval,
                            values=[text for _, text in tc.INTERVAL_CHOICES], width=9, font=FONT)
        combo.pack(side="left", padx=(12, 0))
        combo.bind("<<ComboboxSelected>>", self.on_interval)
        self.tray_button = ttk.Button(controls, text="开启后台托盘", command=self.toggle_tray)
        self.tray_button.pack(side="right")
        ttk.Button(controls, text="设置", command=self.show_settings).pack(side="right", padx=(0, 10))
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=32, pady=(0, 18))
        self.foot = label(footer, "正在连接设备…", 12, MUTED)
        self.foot.pack(side="left")
        label(footer, "本机读取 · 无需云端", 12, MUTED).pack(side="right")
        # Reserve the controls before letting the cards consume remaining space.
        body.pack_forget()
        controls.pack_forget()
        footer.pack_forget()
        footer.pack(side="bottom", fill="x", padx=32, pady=(0, 18))
        controls.pack(side="bottom", fill="x", padx=32, pady=(20, 12))
        body.pack(fill="both", expand=True, padx=32)
        self.update_tray_button()

    def interval_label(self):
        return dict(tc.INTERVAL_CHOICES).get(self.cfg["interval"], f"{self.cfg['interval']} 秒")

    def persist(self, **changes):
        try:
            self.cfg = tc.update_config(**changes)
            tc.post_to_tray(tc.MSG_RELOAD)
            return True
        except OSError as error:
            self.foot.configure(text=f"设置保存失败：{error}")
            return False

    def update_options(self):
        self.options = {"不显示设备": ""}
        for key, device in self.devices.items():
            suffix = " · 未连接" if not device.connected else " · 待适配" if not device.supported else ""
            name = device.title + suffix
            if name in self.options:
                name += f" ({key})"
            self.options[name] = key
        for card, key in zip(self.cards, self.slots):
            card.set_options(self.options, key, ds.remembered_title(key, self.cfg["device_labels"]))

    def select(self, slot, option):
        self.set_slot(slot, self.options.get(option, ""))

    def set_slot(self, slot, key):
        self.initialize_selection = False
        self.slots = ds.switch_slot(self.slots, slot, key)
        self.generation += 1
        labels = dict(self.cfg.get("device_labels", {}))
        labels.update({k: self.devices[k].title for k in self.slots if k in self.devices})
        self.persist(selected_devices=self.slots, device_labels=labels)
        self.update_options()
        for card, selected in zip(self.cards, self.slots):
            card.loading(selected)
        self.refresh()

    def refresh(self):
        if self.busy:
            self.pending = True
            return
        self.busy = True
        self.refresh_button.configure(state="disabled", text="读取中…")
        self.summary.configure(text="正在更新设备…")
        self.foot.configure(text="正在读取设备电量…")
        generation, slots = self.generation, list(self.slots)
        cfg, initialize = dict(self.cfg), self.initialize_selection
        for card, key in zip(self.cards, slots):
            card.loading(key)

        def work():
            try:
                devices = ds.discover_devices()
                chosen = ds.selected_devices(cfg, devices) if initialize else slots
                readings = ds.read_selected(chosen, devices, cfg["device_labels"])
                self.results.put(("read", generation, devices, chosen, readings, ""))
            except Exception as error:
                self.results.put(("read", generation, {}, slots, {}, str(error)))
        threading.Thread(target=work, daemon=True).start()

    def apply_read(self, generation, devices, slots, readings, error):
        self.busy = False
        self.refresh_button.configure(state="normal", text="立即刷新")
        if generation == self.generation:
            if error:
                self.foot.configure(text=f"扫描失败：{error}")
                for card, key in zip(self.cards, self.slots):
                    card.update(ds.Reading(key, key, error="扫描设备失败，请重新刷新。") if key else None)
            else:
                self.devices, self.slots = devices, slots
                changes = {}
                if self.initialize_selection:
                    self.initialize_selection = False
                    changes["selected_devices"] = slots
                if self.cfg["tray_devices"] is None:
                    changes["tray_devices"] = ds.tray_device_keys(self.cfg, devices)
                labels = dict(self.cfg["device_labels"])
                labels.update({key: device.title for key, device in devices.items()})
                if labels != self.cfg["device_labels"]:
                    changes["device_labels"] = labels
                if changes:
                    self.persist(**changes)
                self.update_options()
                self.render_device_rows()
                self.update_tray_choices()
                for card, key in zip(self.cards, slots):
                    card.update(readings.get(key))
                ok = sum(reading.ok for reading in readings.values())
                total = sum(bool(key) for key in slots)
                self.summary.configure(text=f"{ok} 台设备已就绪" if ok else "选择设备，开始查看电量")
                self.foot.configure(text=f"更新于 {time.strftime('%H:%M:%S')}  ·  成功读取 {ok}/{total} 台设备")
        if self.pending or generation != self.generation:
            self.pending = False
            self.refresh()

    def on_auto(self):
        self.cfg["auto_refresh"] = self.auto.get()
        self.persist(auto_refresh=self.auto.get())
        self.schedule()

    def on_interval(self, _=None):
        interval = next((seconds for seconds, text in tc.INTERVAL_CHOICES if text == self.interval.get()), 60)
        self.cfg["interval"] = interval
        self.persist(interval=interval)
        self.schedule()

    def schedule(self):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.cfg["auto_refresh"]:
            self.timer = self.root.after(self.cfg["interval"] * 1000, self.tick)

    def tick(self):
        self.refresh()
        self.schedule()

    def update_tray_button(self):
        if not self.tray_starting:
            self.tray_button.configure(text="退出后台托盘" if tc.is_tray_running() else "开启后台托盘", state="normal")

    def toggle_tray(self):
        if tc.is_tray_running():
            tc.post_to_tray(tc.MSG_QUIT)
            return
        self.start_tray()

    def start_tray(self):
        if self.tray_starting or tc.is_tray_running():
            return
        self.tray_starting = True
        self.tray_button.configure(state="disabled", text="正在启动…")

        def work():
            try:
                ok = tc.start_tray_app()
            except Exception:
                ok = False
            self.results.put(("tray", ok))
        threading.Thread(target=work, daemon=True).start()

    def set_tray_device(self, key, enabled):
        if key not in self.devices:
            return False
        keys = ds.tray_device_keys(self.cfg, self.devices)
        if enabled and key not in keys:
            keys.append(key)
        elif not enabled:
            keys = [item for item in keys if item != key]
        labels = dict(self.cfg["device_labels"])
        labels[key] = self.devices[key].title
        if not self.persist(tray_devices=keys, device_labels=labels):
            return False
        self.update_tray_choices()
        if enabled:
            self.start_tray()
        return True

    def update_tray_choices(self):
        if self.device_window is None or not self.device_window.winfo_exists():
            return
        keys = ds.tray_device_keys(self.cfg, self.devices)
        for key, variable in self.tray_vars.items():
            variable.set(key in keys)
        state = "后台运行中" if tc.is_tray_running() else "后台未启动"
        present = sum(key in self.devices for key in keys)
        missing = len(keys) - present
        text = f"当前检测到 {len(self.devices)} 台设备 · {present} 台启用托盘 · {state}"
        if missing:
            text += f" · {missing} 台未连接设备的设置已保留"
        self.tray_notice.configure(text=text)

    def show_devices(self):
        if self.device_window is not None and self.device_window.winfo_exists():
            self.device_window.lift()
            self.refresh()
            return
        window = self.device_window = tk.Toplevel(self.root)
        window.title(f"设备列表 · {tc.UI_TITLE}")
        window.configure(bg=BG)
        window.geometry("800x580")
        window.minsize(760, 480)
        window.transient(self.root)
        heading = tk.Frame(window, bg=BG)
        heading.pack(fill="x", padx=24, pady=(23, 6))
        label(heading, "设备与托盘", 21).pack(side="left")
        ttk.Button(heading, text="重新扫描", command=self.refresh).pack(side="right")
        label(window, "仅列出当前检测到的实体 HID 设备，名称使用设备自身上报值。", 12,
              MUTED, wraplength=700, justify="left").pack(anchor="w", padx=24, pady=(0, 6))
        label(window, "逐台勾选「托盘显示」，数量不限；接收器和有线设备分别列出。", 12,
              MUTED, wraplength=700, justify="left").pack(anchor="w", padx=24, pady=(0, 15))
        self.tray_notice = label(window, "", 12, MUTED)
        self.tray_notice.pack(side="bottom", anchor="w", padx=24, pady=(0, 18))
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        canvas = self.device_canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=(0, 4))
        canvas.configure(yscrollcommand=scrollbar.set)
        rows = self.device_rows = tk.Frame(canvas, bg=BG)
        item = canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        window.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
        window.bind("<Escape>", lambda _: window.destroy())
        window.bind("<F5>", lambda _: self.refresh())
        self.device_inventory = None
        self.render_device_rows()
        self.update_tray_choices()
        self.refresh()

    def render_device_rows(self):
        if self.device_window is None or not self.device_window.winfo_exists():
            return
        inventory = tuple((key, device.title, device.provider, device.connection)
                          for key, device in self.devices.items())
        if inventory == self.device_inventory:
            return
        self.device_inventory = inventory
        scroll_position = self.device_canvas.yview()[0]
        rows = self.device_rows
        for child in rows.winfo_children():
            child.destroy()
        self.tray_vars = {}
        keys = ds.tray_device_keys(self.cfg, self.devices)
        if not self.devices:
            label(rows, "未检测到实体 HID 设备，连接后点击「重新扫描」。", 14,
                  MUTED, wraplength=650, justify="left").pack(anchor="w", pady=20)
        for key, device in self.devices.items():
            row = tk.Frame(rows, bg=PANEL, padx=14, pady=13)
            row.pack(fill="x", pady=(0, 8))
            controls = tk.Frame(row, bg=PANEL)
            controls.pack(side="right", padx=(12, 0))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            label(text, device.title, 14, wraplength=350, justify="left").pack(anchor="w")
            status = "可读取电量" if device.supported else "电量待适配"
            label(text, f"{status} · {key.upper()} · {device.connection}", 12, MUTED,
                  wraplength=350, justify="left").pack(anchor="w", pady=(4, 0))
            variable = tk.BooleanVar(value=key in keys)
            self.tray_vars[key] = variable

            def on_toggle(k=key, value=variable):
                self.set_tray_device(k, value.get())
                self.update_tray_choices()  # Restore the checkbox if saving failed.
            ttk.Checkbutton(controls, text="托盘显示", variable=variable,
                            style="Device.TCheckbutton", command=on_toggle).pack(side="left", padx=(0, 12))
            for slot in (0, 1):
                ttk.Button(controls, text=f"放入 {slot + 1}", width=6,
                           command=lambda s=slot, k=key: self.set_slot(s, k)).pack(side="left", padx=(5, 0))
        self.device_canvas.yview_moveto(scroll_position)

    def show_settings(self):
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.lift()
            return
        window = self.settings = tk.Toplevel(self.root)
        window.title(f"设置 · {tc.UI_TITLE}")
        window.configure(bg=BG)
        window.geometry("500x300")
        window.resizable(False, False)
        window.transient(self.root)
        label(window, "使用偏好", 22).pack(anchor="w", padx=26, pady=(23, 17))
        boot = tk.BooleanVar(value=tc.is_autostart_enabled())
        notice = label(window, "在「设备与托盘」中逐台启用托盘，设置会自动保存。", 12, MUTED, wraplength=440, justify="left")

        def on_boot():
            want = boot.get()
            try:
                ok = tc.set_autostart(want)
                actual = tc.is_autostart_enabled()
                boot.set(actual)
                self.persist(autostart=actual)
                notice.configure(text="开机启动设置已保存。" if ok else "设置失败，请重试。")
            except Exception as error:
                boot.set(tc.is_autostart_enabled())
                notice.configure(text=f"设置失败：{error}")
        ttk.Checkbutton(window, text="登录 Windows 后启动后台托盘", variable=boot,
                        command=on_boot).pack(anchor="w", padx=26)
        notice.pack(anchor="w", padx=26, pady=(12, 18))

        def links():
            try:
                result = tc.install_desktop_links()
                notice.configure(text="；".join(result.get("errors", [])) or "桌面入口已创建。")
            except Exception as error:
                notice.configure(text=f"创建失败：{error}")
        ttk.Button(window, text="创建桌面入口", command=links).pack(anchor="w", padx=26)
        window.bind("<Escape>", lambda _: window.destroy())

    def pump(self):
        try:
            while True:
                result = self.results.get_nowait()
                if result[0] == "read":
                    self.apply_read(*result[1:])
                elif result[0] == "tray":
                    self.tray_starting = False
                    self.update_tray_button()
                    if not result[1]:
                        self.foot.configure(text="托盘启动失败，请重试。")
        except queue.Empty:
            pass
        self.update_tray_button()
        self.update_tray_choices()
        self.root.after(200, self.pump)

    def close(self):
        if self.timer:
            self.root.after_cancel(self.timer)
        self.root.destroy()


def read_all():
    cfg = tc.load_config()
    devices = ds.discover_devices()
    slots = ds.selected_devices(cfg, devices)
    return list(ds.read_selected(slots, devices, cfg["device_labels"]).values())


def cli_once():
    readings = read_all()
    if not readings:
        print("没有选择设备，请先在主页中选择。")
    for reading in readings:
        print(f"{reading.title}: {reading.level}% · {reading.status}" if reading.ok
              else f"{reading.title}: {reading.error}")


def build_ui():
    try:
        tc.USER32.SetProcessDPIAware()
    except OSError:
        pass
    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.0)
    root.dashboard = Dashboard(root)
    return root


def main():
    parser = argparse.ArgumentParser(description=tc.UI_TITLE)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--list", action="store_true", help="列出可选择的设备")
    parser.add_argument("--install-links", action="store_true")
    parser.add_argument("--autostart", choices=["on", "off", "status"])
    args = parser.parse_args()
    if args.install_links:
        print(tc.install_desktop_links())
    elif args.autostart:
        if args.autostart == "status":
            print(tc.autostart_status())
        else:
            print("设置成功" if tc.set_autostart(args.autostart == "on") else "设置失败")
    elif args.list:
        for key, device in ds.discover_devices().items():
            print(key, device.title, "已发现" if device.connected else "未连接",
                  "可读取" if device.supported else "待适配")
    elif args.once:
        cli_once()
    else:
        build_ui().mainloop()


if __name__ == "__main__":
    main()
