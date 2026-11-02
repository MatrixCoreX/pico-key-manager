from datetime import datetime
import json
from pathlib import Path
import sys
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import __version__
from . import device as hardware

APP_ID = "io.github.guagua.PicoKeyManager"
ROOT = Path(__file__).resolve().parent.parent
STATE = Path(GLib.get_user_data_dir()) / "pico-key-manager"

CSS = """
.sidebar { background: alpha(@window_fg_color, 0.025); padding: 18px 12px; }
.sidebar-brand { padding: 6px 10px 22px; }
.sidebar list { background: transparent; }
.sidebar row { border-radius: 10px; margin: 3px 0; }
.sidebar row box { padding: 12px 10px; }
.sidebar-footer { padding: 16px 10px 0; font-size: 11px; }
.page-title { font-size: 27px; font-weight: 800; letter-spacing: -0.5px; }
.page-description { color: alpha(@window_fg_color, 0.6); margin-bottom: 8px; }
.hero { border-radius: 20px; padding: 26px; background-image: linear-gradient(120deg, alpha(#30ba9e,0.15), alpha(#71a9df,0.08)); border: 1px solid alpha(#30ba9e,0.2); }
.hero-title { font-size: 24px; font-weight: 800; }
.badge { border-radius: 999px; padding: 5px 12px; font-weight: 600; font-size: 12px; background: alpha(@window_fg_color,0.07); }
.badge.connected { color: #087f5b; background: #dff7ed; }
.badge.boot { color: #8a5400; background: #fff0ce; }
.metric { padding: 20px; border-radius: 14px; }
.metric-value { font-size: 22px; font-weight: 750; padding-top: 7px; }
.metric-label { font-size: 12px; color: alpha(@window_fg_color,0.58); }
.hint { font-size: 12px; color: alpha(@window_fg_color,0.65); }
.feature-icon { color: #159b82; }
.test-card { padding: 24px; border-radius: 16px; }
.test-title { font-size: 17px; font-weight: 700; }
.progress-strip { padding: 14px 24px; border-top: 1px solid alpha(@window_fg_color,0.1); background: alpha(@accent_bg_color,0.07); }
.credential-row { padding: 12px 16px; }
.credential-site { font-weight: 700; }
.empty-state { padding: 34px; }
.dialog-content { padding: 8px 0; }
"""


def label(text="", css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap)
    if css:
        widget.add_css_class(css)
    return widget


def button(text, callback, icon=None, css=None):
    widget = Gtk.Button(label=text)
    if icon:
        content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(Gtk.Label(label=text))
        widget.set_child(content)
    if css:
        widget.add_css_class(css)
    widget.connect("clicked", lambda *_: callback())
    return widget


class ManagerWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="Pico Key 管理器", default_width=1060, default_height=780)
        self.set_icon_name(APP_ID)
        self.refs = []
        self.ref = None
        self.info = None
        self.boot = None
        self.firmware = None
        self.busy = False
        self.cancellable = False
        self.cancel_event = threading.Event()
        self.operation_buttons = []
        self._updating = False
        self._device_signature = None
        self.last_test = None
        self._load_test()

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Pico Key", subtitle="本地设备管理器"))
        self.badge = label("正在检测", "badge")
        header.pack_start(self.badge)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="刷新设备")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self.refresh_button)
        menu = Gio.Menu()
        menu.append("导出诊断信息", "app.export")
        menu.append("关于 Pico Key", "app.about")
        menu.append("退出", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content = Gtk.Box(hexpand=True, vexpand=True)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=214)
        sidebar.add_css_class("sidebar")
        brand = Gtk.Box(spacing=10)
        brand.add_css_class("sidebar-brand")
        brand_image = Gtk.Image.new_from_file(str(ROOT / "assets/pico-key.svg"))
        brand_image.set_pixel_size(34)
        brand.append(brand_image)
        brand.append(label("Pico Key", "heading"))
        sidebar.append(brand)
        self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.navigation.add_css_class("navigation-sidebar")
        self.navigation.connect("row-selected", self._navigate)
        self.page_ids = ["overview", "pin", "credentials", "tests", "maintenance"]
        for title, icon in [("设备概览", "drive-removable-media-symbolic"), ("PIN 与验证", "dialog-password-symbolic"), ("Passkey", "dialog-information-symbolic"), ("签名测试", "emblem-ok-symbolic"), ("固件与维护", "applications-system-symbolic")]:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(spacing=12)
            row_box.append(Gtk.Image.new_from_icon_name(icon))
            row_box.append(label(title))
            row.set_child(row_box)
            self.navigation.append(row)
        sidebar.append(self.navigation)
        spacer = Gtk.Box(vexpand=True)
        sidebar.append(spacer)
        footer = label(f"设备数据仅在本机处理\nv{__version__}", "hint", True)
        footer.add_css_class("sidebar-footer")
        sidebar.append(footer)
        content.append(sidebar)
        content.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        content.append(self.stack)
        self._build_overview()
        self._build_pin()
        self._build_credentials()
        self._build_tests()
        self._build_maintenance()
        self.navigation.select_row(self.navigation.get_row_at_index(0))
        body.append(content)

        self.progress_bar = Gtk.Box(spacing=12)
        self.progress_bar.add_css_class("progress-strip")
        self.spinner = Gtk.Spinner()
        self.progress_bar.append(self.spinner)
        self.progress_label = label("", wrap=True)
        self.progress_label.set_hexpand(True)
        self.progress_bar.append(self.progress_label)
        self.cancel_button = button("取消", lambda: self.cancel_event.set())
        self.progress_bar.append(self.cancel_button)
        self.progress_bar.set_visible(False)
        body.append(self.progress_bar)
        self.toasts = Adw.ToastOverlay(child=body)
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)
        self.connect("close-request", self._close_requested)
        self.refresh()
        GLib.timeout_add_seconds(3, self._poll_devices)

    def _page(self, name, title, description):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, margin_top=30, margin_bottom=32, margin_start=30, margin_end=30)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heading.append(label(title, "page-title"))
        heading.append(label(description, "page-description", True))
        page.append(heading)
        clamp = Adw.Clamp(maximum_size=850, tightening_threshold=650, child=page)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        self.stack.add_named(scroll, name)
        return page

    def _operation(self, text, callback, css=None):
        widget = button(text, callback, css=css)
        self.operation_buttons.append(widget)
        return widget

    def _row(self, group, title, subtitle, action=None):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if action is not None:
            action.set_valign(Gtk.Align.CENTER)
            row.add_suffix(action)
        group.add(row)
        return row

    def _build_overview(self):
        page = self._page("overview", "设备概览", "查看连接状态与安全密钥功能，一切从这里开始。")
        self.selector = Gtk.DropDown(model=Gtk.StringList.new(["正在查找设备…"]))
        self.selector.connect("notify::selected", self._selection_changed)
        page.append(self.selector)
        hero = Gtk.Box(spacing=22)
        hero.add_css_class("hero")
        image = Gtk.Image.new_from_file(str(ROOT / "assets/pico-key.svg"))
        image.set_pixel_size(76)
        hero.append(image)
        hero_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True, valign=Gtk.Align.CENTER)
        self.device_title = label("连接你的 Pico", "hero-title")
        self.device_description = label("通过 USB 连接安全密钥，设备会自动显示。", wrap=True)
        hero_text.append(self.device_title)
        hero_text.append(self.device_description)
        hero.append(hero_text)
        page.append(hero)
        metrics = Gtk.Box(spacing=12, homogeneous=True)
        self.metrics = {}
        for name, title in [("firmware", "固件版本"), ("pin", "PIN 状态"), ("protocol", "认证协议")]:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            card.add_css_class("card")
            card.add_css_class("metric")
            card.append(label(title, "metric-label"))
            value = label("—", "metric-value")
            card.append(value)
            self.metrics[name] = value
            metrics.append(card)
        page.append(metrics)
        actions = Gtk.Box(spacing=10)
        actions.append(button("管理 PIN", lambda: self.go("pin"), "dialog-password-symbolic", "suggested-action"))
        actions.append(button("查看 Passkey", lambda: self.go("credentials")))
        page.append(actions)
        details = Adw.PreferencesGroup(title="设备信息")
        self.usb_row = self._row(details, "USB 连接", "等待设备连接")
        self.capabilities_row = self._row(details, "可用功能", "—")
        self.algorithms_row = self._row(details, "签名算法", "—")
        page.append(details)
        page.append(label("普通 Passkey 注册和 Daybreak 硬件密钥资格是不同的检查，最终以 OpenAI 页面结果为准。", "hint", True))

    def _build_pin(self):
        page = self._page("pin", "PIN 与验证", "PIN 用来保护密钥内的凭据，输入仅在本次操作中使用。")
        group = Adw.PreferencesGroup(title="PIN 管理")
        self.pin_status_row = self._row(group, "设备 PIN", "等待设备连接")
        self.set_pin_button = self._operation("设置 PIN", lambda: self._pin_dialog("设置设备 PIN", self._set_pin, new=True, current=False), "suggested-action")
        self._row(group, "首次设置", "为尚未设置 PIN 的安全密钥创建 PIN。", self.set_pin_button)
        self.change_pin_button = self._operation("修改 PIN", lambda: self._pin_dialog("修改设备 PIN", self._change_pin, new=True))
        self._row(group, "更换 PIN", "需要当前 PIN；不会删除已有 Passkey。", self.change_pin_button)
        self.verify_pin_button = self._operation("验证 PIN", lambda: self._pin_dialog("验证设备 PIN", self._verify_pin))
        self._row(group, "检查 PIN", "验证当前 PIN，不创建或删除任何凭据。", self.verify_pin_button)
        page.append(group)
        note = Adw.PreferencesGroup(title="忘记 PIN")
        self._row(note, "先确认备用登录方式", "忘记 PIN 通常需要重置 FIDO 功能，已保存的凭据会被清空。")
        page.append(note)

    def _build_credentials(self):
        page = self._page("credentials", "Passkey", "查看密钥中保存的网站与账号，按需删除单个凭据。")
        toolbar = Gtk.Box(spacing=10)
        self.credential_count = label("尚未解锁", "heading")
        self.credential_count.set_hexpand(True)
        toolbar.append(self.credential_count)
        self.unlock_button = self._operation("解锁并刷新", lambda: self._pin_dialog("解锁 Passkey 列表", self._list_credentials), "suggested-action")
        toolbar.append(self.unlock_button)
        page.append(toolbar)
        self.credential_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.credential_list.add_css_class("boxed-list")
        page.append(self.credential_list)
        self._credential_placeholder("输入设备 PIN 后显示已保存的 Passkey。")
        page.append(label("这里只显示驻留凭据。网站使用的非驻留凭据不会出现在列表中。", "hint", True))

    def _build_tests(self):
        page = self._page("tests", "签名测试", "在本机验证 PIN、物理按键与登录签名，不访问你的真实账号。")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.add_css_class("card")
        card.add_css_class("test-card")
        title = Gtk.Box(spacing=12)
        icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        icon.set_pixel_size(36)
        icon.add_css_class("feature-icon")
        title.append(icon)
        title.append(label("完整签名验证", "test-title"))
        card.append(title)
        card.append(label("输入 PIN 后，需要短按 BOOTSEL 两次：第一次创建临时凭据，第二次完成登录签名。每次等待窗口为 30 秒。", wrap=True))
        self.algorithm_selector = Gtk.DropDown(model=Gtk.StringList.new(["ES256 · 兼容性优先", "EdDSA · Ed25519"]))
        card.append(self.algorithm_selector)
        self.test_button = self._operation("开始签名测试", lambda: self._pin_dialog("签名测试", self._signature_test), "suggested-action")
        card.append(self.test_button)
        self.test_result = label("尚未在本工具中完成测试。", "hint", True)
        card.append(self.test_result)
        page.append(card)
        group = Adw.PreferencesGroup(title="检查内容")
        self._row(group, "PIN 授权", "验证设备确实接受你的 PIN。")
        self._row(group, "用户在场", "两次操作均要求按下并松开 BOOTSEL。")
        self._row(group, "签名正确性", "使用新凭据的公钥验证登录签名和站点绑定。")
        page.append(group)
        self._show_test_result()

    def _build_maintenance(self):
        page = self._page("maintenance", "固件与维护", "更新 Pico 2 W 固件，或在确有需要时重置 FIDO 功能。")
        group = Adw.PreferencesGroup(title="更新固件")
        self.boot_row = self._row(group, "烧录模式", "未发现 RP2350 烧录盘。")
        choose = button("选择 UF2", self._choose_firmware)
        self.firmware_row = self._row(group, "固件文件", "请选择适用于 Pico 2 W 的 UF2 文件。", choose)
        self.flash_button = button("写入固件", self._confirm_flash, css="suggested-action")
        self._row(group, "写入设备", "按住 BOOTSEL 插入 USB，出现 RP2350 磁盘后松开。", self.flash_button)
        page.append(group)
        danger = Adw.PreferencesGroup(title="重置")
        self.reset_button = self._operation("重置 FIDO", self._confirm_reset, "destructive-action")
        self._row(danger, "清空 PIN 与 FIDO 凭据", "会永久删除此密钥保存的 FIDO 凭据，请先保留其他登录方式。", self.reset_button)
        page.append(danger)
        diagnostics = Adw.PreferencesGroup(title="诊断")
        self._row(diagnostics, "导出设备信息", "包含版本和功能信息，不含 PIN、账号列表或凭据 ID。", button("导出 JSON", self.export_diagnostics))
        page.append(diagnostics)

    def _navigate(self, _list, row):
        if row is not None:
            self.stack.set_visible_child_name(self.page_ids[row.get_index()])

    def go(self, name):
        self.navigation.select_row(self.navigation.get_row_at_index(self.page_ids.index(name)))

    def _selection_changed(self, *_):
        if self._updating or self.busy:
            return
        index = self.selector.get_selected()
        if index < len(self.refs):
            self.ref = self.refs[index]
            self.info = None
            self._credential_placeholder("设备已切换，请重新解锁凭据列表。")
            self._read_info()

    def _poll_devices(self):
        if not self.busy:
            try:
                refs = hardware.discover()
                signature = tuple((ref.path, ref.fingerprint) for ref in refs)
                boot = hardware.boot_volume()
                if signature != self._device_signature or boot != self.boot:
                    self.refresh(refs)
            except Exception:
                pass
        return True

    def refresh(self, refs=None):
        if self.busy:
            return
        try:
            self.refs = hardware.discover() if refs is None else refs
            self.boot = hardware.boot_volume()
        except Exception as error:
            self._error(error)
            return
        self._device_signature = tuple((ref.path, ref.fingerprint) for ref in self.refs)
        previous = self.ref.fingerprint if self.ref else None
        index = next((i for i, ref in enumerate(self.refs) if ref.fingerprint == previous), 0)
        self._updating = True
        self.selector.set_model(Gtk.StringList.new([f"{ref.name} · {ref.path}" for ref in self.refs] or ["未发现 FIDO 安全密钥"]))
        self.selector.set_selected(index)
        self._updating = False
        self.ref = self.refs[index] if self.refs else None
        self.boot_row.set_subtitle(f"已挂载：{self.boot}" if self.boot else "未发现 RP2350 烧录盘。")
        if self.ref:
            self._read_info()
        else:
            self.info = None
            self._set_badge("烧录模式" if self.boot else "未连接", "boot" if self.boot else None)
            self.device_title.set_text("Pico 已进入烧录模式" if self.boot else "连接你的 Pico")
            self.device_description.set_text("可在“固件与维护”中写入 UF2 固件。" if self.boot else "通过 USB 连接安全密钥，设备会自动显示。")
            for metric in self.metrics.values():
                metric.set_text("—")
            self.pin_status_row.set_subtitle("等待设备连接")
            self.usb_row.set_subtitle("未发现已连接的 FIDO 安全密钥")
            self.capabilities_row.set_subtitle("—")
            self.algorithms_row.set_subtitle("—")
            self._credential_placeholder("连接设备并输入 PIN 后查看 Passkey。")
            self._show_test_result()
            self._update_actions()

    def _read_info(self):
        ref = self.ref
        self.info = None
        self.run_task("正在读取设备…", lambda *_: hardware.describe(ref), self._apply_info)

    def _apply_info(self, info):
        self.info = info
        self._set_badge("已连接", "connected")
        self.device_title.set_text("Pico Key")
        self.device_description.set_text(info["name"] + "  ·  USB 安全密钥")
        self.metrics["firmware"].set_text(info["firmware"])
        self.metrics["pin"].set_text("已设置" if info["pin_set"] else "未设置")
        self.metrics["protocol"].set_text(next((x.replace("FIDO_", "FIDO ").replace("_", ".") for x in reversed(info["versions"]) if x.startswith("FIDO_")), "U2F"))
        self.usb_row.set_subtitle(f"{info['usb_id']}  ·  {info['path']}")
        features = ["PIN 验证"] if info["pin_set"] is not None else []
        if info["resident_keys"]:
            features.append("驻留 Passkey")
        if info["credential_management"]:
            features.append("凭据管理")
        self.capabilities_row.set_subtitle(" / ".join(features) or "基础 FIDO 功能")
        algorithms = {-7: "ES256", -8: "EdDSA", -35: "ES384", -36: "ES512"}
        self.algorithms_row.set_subtitle(" / ".join(algorithms.get(x, str(x)) for x in info["algorithms"]))
        self.pin_status_row.set_subtitle("已设置 PIN，可修改或验证。" if info["pin_set"] else "尚未设置 PIN。建议先设置 PIN 再用于账号登录。")
        self._show_test_result()
        self._update_actions()

    def _set_badge(self, text, style=None):
        self.badge.set_text(text)
        for name in ("connected", "boot"):
            self.badge.remove_css_class(name)
        if style:
            self.badge.add_css_class(style)

    def _update_actions(self):
        ready = self.info is not None and not self.busy
        for widget in self.operation_buttons:
            widget.set_sensitive(ready)
        pin = bool(self.info and self.info.get("pin_set"))
        self.set_pin_button.set_sensitive(ready and not pin)
        for widget in (self.change_pin_button, self.verify_pin_button, self.unlock_button, self.test_button):
            widget.set_sensitive(ready and pin)
        if self.info and not self.info.get("credential_management"):
            self.unlock_button.set_sensitive(False)
        self.flash_button.set_sensitive(bool(self.boot and self.firmware and not self.busy))
        self.selector.set_visible(len(self.refs) > 1)
        self.selector.set_sensitive(not self.busy)
        self.refresh_button.set_sensitive(not self.busy)

    def _pin_dialog(self, title, callback, new=False, current=True):
        if self.busy or not self.ref:
            return
        dialog = Adw.AlertDialog(heading=title, body="PIN 仅用于当前操作，不会保存或写入日志。")
        fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        fields.add_css_class("dialog-content")
        entries = {}
        for key, placeholder in ([('current', '当前 PIN')] if current else []) + ([('new', '新 PIN（至少 8 个字符）'), ('confirm', '再次输入新 PIN')] if new else []):
            field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            field.append(label(placeholder, "hint"))
            entry = Gtk.PasswordEntry(placeholder_text=placeholder, show_peek_icon=True)
            entry.set_size_request(320, -1)
            field.append(entry)
            fields.append(field)
            entries[key] = entry
        hint = label("", "error", True)
        fields.append(hint)
        dialog.set_extra_child(fields)
        dialog.add_response("cancel", "取消")
        dialog.add_response("apply", "确认")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("apply")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("apply", False)

        def validate(*_):
            values = {key: entry.get_text() for key, entry in entries.items()}
            valid = all(values.values())
            if new and values.get("new"):
                try:
                    hardware.validate_pin(values['new'], values.get('confirm', ''), max(8, (self.info or {}).get('min_pin_length', 4)))
                    hint.set_text("")
                except hardware.DeviceError as error:
                    valid = False
                    hint.set_text(str(error) if values.get('confirm') else "")
            dialog.set_response_enabled("apply", valid)

        for entry in entries.values():
            entry.connect("changed", validate)

        def response(_dialog, response_id):
            values = {key: entry.get_text() for key, entry in entries.items()} if response_id == "apply" else {}
            for entry in entries.values():
                entry.set_text("")
            if response_id == "apply":
                callback(values)

        dialog.connect("response", response)
        dialog.present(self)
        next(iter(entries.values())).grab_focus()

    def _set_pin(self, values):
        ref = self.ref
        self.run_task("正在设置 PIN…", lambda *_: hardware.set_pin(ref, values['new']), lambda _: self._pin_success("PIN 已设置"))

    def _change_pin(self, values):
        ref = self.ref
        self.run_task("正在修改 PIN…", lambda *_: hardware.change_pin(ref, values['current'], values['new']), lambda _: self._pin_success("PIN 已修改，原有 Passkey 已保留"))

    def _verify_pin(self, values):
        ref = self.ref
        self.run_task("正在验证 PIN…", lambda *_: hardware.verify_pin(ref, values['current']), lambda _: self.toast("PIN 验证通过"))

    def _pin_success(self, text):
        self.toast(text)
        self.refresh()

    def _credential_placeholder(self, text):
        while child := self.credential_list.get_first_child():
            self.credential_list.remove(child)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        box.add_css_class("empty-state")
        icon = Gtk.Image.new_from_icon_name("dialog-password-symbolic")
        icon.set_pixel_size(36)
        box.append(icon)
        box.append(label(text, "hint", True))
        self.credential_list.append(box)
        self.credential_count.set_text("尚未解锁")

    def _list_credentials(self, values):
        ref = self.ref
        self.run_task("正在读取 Passkey…", lambda *_: hardware.list_credentials(ref, values['current']), self._show_credentials)

    def _show_credentials(self, data):
        if not data['credentials']:
            self._credential_placeholder("这把密钥还没有保存驻留 Passkey。")
        else:
            while child := self.credential_list.get_first_child():
                self.credential_list.remove(child)
            for credential in data['credentials']:
                row = Gtk.Box(spacing=12)
                row.add_css_class("credential-row")
                row.append(Gtk.Image.new_from_icon_name("dialog-password-symbolic"))
                texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
                texts.append(label(str(credential['site']), "credential-site", True))
                texts.append(label(str(credential['user']), "hint", True))
                row.append(texts)
                delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="删除此 Passkey", valign=Gtk.Align.CENTER)
                delete.add_css_class("flat")
                delete.connect("clicked", lambda _, item=credential: self._confirm_delete(item))
                row.append(delete)
                self.credential_list.append(row)
        remaining = f" · 可用约 {data['remaining']} 个" if data['remaining'] is not None else ""
        self.credential_count.set_text(f"已保存 {data['count']} 个{remaining}")

    def _confirm_delete(self, credential):
        if self.busy:
            return
        ref = self.ref
        dialog = Adw.AlertDialog(heading="删除这个 Passkey？", body=f"网站：{credential['site']}\n账号：{credential['user']}\n\n将永久删除密钥中的此凭据。请确认该账号还有其他登录方式。")
        dialog.add_response("cancel", "保留")
        dialog.add_response("delete", "删除 Passkey")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_, choice):
            if choice == "delete":
                def prompt():
                    self._pin_dialog("确认删除 Passkey", lambda values: self.run_task("正在删除选定凭据…", lambda *_: hardware.delete_credential(ref, values['current'], credential['credential_id']), lambda _: self._deleted()))
                    return False
                GLib.idle_add(prompt)
        dialog.connect("response", response)
        dialog.present(self)

    def _deleted(self):
        self._credential_placeholder("凭据已删除，重新解锁可刷新列表。")
        self.toast("选定的 Passkey 已删除")

    def _signature_test(self, values):
        ref = self.ref
        algorithm = -7 if self.algorithm_selector.get_selected() == 0 else -8
        self.run_task("正在准备测试，请留意按键提示…", lambda event, progress: hardware.signature_test(ref, values['current'], event, progress, algorithm), self._test_complete, cancellable=True)

    def _test_complete(self, result):
        self.last_test = dict(result, fingerprint=self.ref.fingerprint)
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "last-test.json").write_text(json.dumps(self.last_test, ensure_ascii=False, indent=2) + "\n")
        self._show_test_result()
        self.toast("测试通过：PIN、按键确认和签名均正常")

    def _load_test(self):
        try:
            self.last_test = json.loads((STATE / "last-test.json").read_text())
        except (OSError, ValueError):
            self.last_test = None

    def _show_test_result(self):
        if self.last_test and self.ref and self.last_test.get('fingerprint') == self.ref.fingerprint:
            self.test_result.set_text(f"上次测试通过 · {self.last_test['algorithm']}\n{self.last_test['time'][:19].replace('T', ' ')}")
        else:
            self.test_result.set_text("当前设备尚未在本工具中完成测试。")

    def _choose_firmware(self):
        if self.busy:
            return
        chooser = Gtk.FileDialog(title="选择 Pico 2 W 固件")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        file_filter = Gtk.FileFilter(name="UF2 固件")
        file_filter.add_pattern("*.uf2")
        filters.append(file_filter)
        chooser.set_filters(filters)

        def selected(dialog, result):
            try:
                file = dialog.open_finish(result)
                self.firmware = hardware.validate_uf2(file.get_path())
                self.firmware_row.set_subtitle(f"{file.get_basename()} · {self.firmware['bytes'] / 1024:.0f} KiB\nSHA-256：{self.firmware['sha256'][:24]}…")
                self._update_actions()
            except GLib.Error:
                pass
            except Exception as error:
                self.firmware = None
                self._update_actions()
                self._error(error)
        chooser.open(self, None, selected)

    def _confirm_flash(self):
        if self.busy or not self.firmware:
            return
        firmware = self.firmware.copy()
        dialog = Adw.AlertDialog(heading="写入选定的固件？", body=f"文件：{Path(firmware['path']).name}\n\n这会替换设备程序。凭据兼容性取决于固件，请先确认账号有其他登录方式。写入期间请勿断开 USB。")
        dialog.add_response("cancel", "取消")
        dialog.add_response("flash", "写入固件")
        dialog.set_response_appearance("flash", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, choice: self.run_task("正在写入固件…", lambda event, progress: hardware.flash_uf2(firmware['path'], firmware['sha256'], progress), lambda _: self._pin_success("固件已写入，设备将重新连接")) if choice == "flash" else None)
        dialog.present(self)

    def _confirm_reset(self):
        if self.busy:
            return
        ref = self.ref
        dialog = Adw.AlertDialog(heading="重置这把 FIDO 密钥？", body="PIN 与 FIDO 凭据将被永久清空。网站中的旧注册不会自动删除，重置后需要重新绑定。\n\n继续前，请确认你还有其他登录方式。输入“清空”以继续。")
        entry = Gtk.Entry(placeholder_text="输入：清空")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "保留设备")
        dialog.add_response("reset", "清空 FIDO 凭据")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_enabled("reset", False)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        entry.connect("changed", lambda *_: dialog.set_response_enabled("reset", entry.get_text() == "清空"))
        dialog.connect("response", lambda _, choice: self.run_task("等待拔插设备…", lambda event, progress: hardware.reset_when_reconnected(ref, event, progress), lambda _: self._pin_success("FIDO 功能已重置，请重新设置 PIN 并绑定账号"), cancellable=True) if choice == "reset" else None)
        dialog.present(self)

    def run_task(self, title, action, done, cancellable=False):
        if self.busy:
            self.toast("请等待当前操作结束")
            return
        self.busy = True
        self.cancellable = cancellable
        self.cancel_event = threading.Event()
        self.cancel_button.set_visible(cancellable)
        self.cancel_button.set_sensitive(True)
        self.progress_label.set_text(title)
        self.progress_bar.set_visible(True)
        self.spinner.start()
        self._update_actions()

        def progress(message):
            GLib.idle_add(lambda: self.progress_label.set_text(message))

        def worker():
            try:
                result = action(self.cancel_event, progress)
                GLib.idle_add(self._finish_task, result, None, done)
            except Exception as error:
                GLib.idle_add(self._finish_task, None, hardware.friendly_error(error), done)
        threading.Thread(target=worker, daemon=True).start()

    def _finish_task(self, result, error, done):
        self.busy = False
        self.progress_bar.set_visible(False)
        self.spinner.stop()
        self._update_actions()
        if error:
            if self.cancellable and self.cancel_event.is_set():
                self.toast("操作已取消")
            else:
                self._error(hardware.DeviceError(error))
        else:
            done(result)
        return False

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))

    def _error(self, error):
        dialog = Adw.AlertDialog(heading="操作未完成", body=hardware.friendly_error(error))
        dialog.add_response("close", "知道了")
        dialog.present(self)

    def _close_requested(self, *_):
        if self.busy and not self.cancellable:
            self.toast("请等待当前设备操作完成后再关闭")
            return True
        self.cancel_event.set()
        return False

    def export_diagnostics(self):
        dialog = Gtk.FileDialog(title="导出设备诊断", initial_name="pico-key-diagnostics.json")

        def save(chooser, result):
            try:
                file = chooser.save_finish(result)
                data = {"app_version": __version__, "exported_at": datetime.now().astimezone().isoformat(), "device": self.info, "last_test": {key: value for key, value in (self.last_test or {}).items() if key != "fingerprint"}}
                Path(file.get_path()).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                self.toast("诊断信息已导出")
            except GLib.Error:
                pass
            except OSError as error:
                self._error(error)
        dialog.save(self, None, save)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name("Pico Key 管理器")
        GLib.set_prgname(APP_ID)
        self.window = None
        for name, callback in [("quit", lambda *_: self.window.close() if self.window else self.quit()), ("about", self.about), ("export", lambda *_: self.window.export_diagnostics())]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self):
        if self.window is None:
            self.window = ManagerWindow(self)
        self.window.present()

    def about(self, *_):
        Adw.AboutDialog(application_name="Pico Key 管理器", application_icon=APP_ID, version=__version__, developer_name="本地 FIDO2 设备工具", comments="管理 Pico 安全密钥的 PIN、Passkey、签名测试与固件。基于 GTK 4 和 python-fido2。", website="https://github.com/polhenarejos/pico-fido").present(self.window)


def main():
    return Application().run(sys.argv)
