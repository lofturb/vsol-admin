# -*- coding: utf-8 -*-
"""VSOL Admin movil - app Kivy para administrar el router VSOL V2804AX.

Reutiliza vrouter.py (misma capa de red que la version de escritorio).
"""

import json
import os
import sys
import threading
import traceback
import webbrowser

from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

import vrouter as V

ACCENT_HEX = (0.239, 0.494, 1.0, 1)      # #3d7eff
BG_DARK = (0.117, 0.121, 0.133, 1)       # #1e1f22
BG_CARD = (0.16, 0.165, 0.18, 1)
TEXT_HEX = (0.93, 0.94, 0.96, 1)
SUB_HEX = (0.55, 0.58, 0.63, 1)

FONT = "Roboto"

CONFIG_FILE = "vsol_config.json"
LOG_FILE = "vsol_errors.log"


# ---------------------------------------------------------------------------
# Reporte de errores en pantalla (depuracion en el movil, sin adb)
# ---------------------------------------------------------------------------
class ErrorReporter(ExceptionHandler):
    """Muestra en pantalla cualquier excepcion que Kivy propague."""

    def handle_exception(self, inst):
        try:
            msg = "".join(traceback.format_exception(*sys.exc_info()))
        except Exception:
            msg = repr(inst)
        _report_error(msg)
        return ExceptionManager.PASS


def _report_error(msg):
    try:
        sys.stderr.write(msg + "\n")
    except Exception:
        pass
    try:
        app = App.get_running_app()
        if app is not None:
            app.log_error(msg)
            if app.root is not None:
                Clock.schedule_once(lambda dt: app.show_error(msg), 0)
    except Exception:
        pass


def _thread_hook(args):
    _report_error("".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback)))


threading.excepthook = _thread_hook


def _excepthook(t, v, tb):
    _report_error("".join(traceback.format_exception(t, v, tb)))
    sys.__excepthook__(t, v, tb)


def norm_mac(m):
    n = V._norm_mac(m)
    return n.upper() if n else None


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
class Worker(object):
    """Ejecuta fn en un hilo y programa on_done en el hilo de UI."""

    def __init__(self, fn, on_done, on_error=None):
        def run():
            try:
                result = fn()
                Clock.schedule_once(lambda dt: on_done(result), 0)
            except V.LoginError as e:
                Clock.schedule_once(lambda dt: (on_error or self._log)(str(e)), 0)
            except Exception as e:
                Clock.schedule_once(lambda dt: (on_error or self._log)(repr(e)), 0)

        self._log = lambda msg: None
        threading.Thread(target=run, daemon=True).start()


class InfoRow(BoxLayout):
    def __init__(self, label, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(30), **kw)
        self.add_widget(Label(text=label, size_hint_x=0.5, halign="left",
                              color=SUB_HEX, font_name=FONT))
        self.val = Label(text="-", halign="left", color=TEXT_HEX, font_name=FONT)
        self.add_widget(self.val)


def sub_label(text):
    return Label(text=text, color=SUB_HEX, size_hint_y=None, height=dp(24),
                 font_name=FONT, font_size=dp(13))


def section(title):
    return Label(text=title, bold=True, color=TEXT_HEX, size_hint_y=None,
                 height=dp(26), halign="left", font_name=FONT, font_size=dp(14))


def make_button(text, on_release=None):
    b = Button(text=text, font_name=FONT, background_color=(0.239, 0.494, 1.0, 1),
               color=(1, 1, 1, 1), size_hint_y=None, height=dp(44))
    if on_release:
        b.bind(on_release=on_release)
    return b


class ColoredToggle(ToggleButton):
    """Toggle con color solido: azul cuando esta activo (state=down)."""

    ACTIVE = (0.239, 0.494, 1.0, 1)
    INACTIVE = (0.2, 0.22, 0.25, 1)

    def __init__(self, **kw):
        kw.setdefault("font_name", FONT)
        kw.setdefault("background_normal", "")
        kw.setdefault("background_down", "")
        kw.setdefault("color", TEXT_HEX)
        super().__init__(**kw)
        self.bind(state=self._sync_color)
        self._sync_color()

    def _sync_color(self, *a):
        down = self.state == "down"
        self.background_color = self.ACTIVE if down else self.INACTIVE
        self.color = (1, 1, 1, 1) if down else TEXT_HEX


def card():
    return Card()


class Card(BoxLayout):
    def __init__(self, **kw):
        kw.setdefault("padding", dp(10))
        kw.setdefault("spacing", dp(4))
        super().__init__(**kw)
        with self.canvas.before:
            self._col = Color(*BG_CARD)
            self._rect = Rectangle()
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size


def confirm(app, title, text, on_ok):
    content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
    content.add_widget(Label(text=text, color=TEXT_HEX, font_name=FONT))
    btns = BoxLayout(orientation="horizontal", spacing=dp(12), size_hint_y=None, height=dp(48))
    cancel = Button(text="Cancelar", font_name=FONT, background_color=(0.25, 0.27, 0.30, 1))
    ok = Button(text="OK", font_name=FONT, background_color=(0.9, 0.3, 0.3, 1))
    btns.add_widget(cancel)
    btns.add_widget(ok)
    content.add_widget(btns)
    pop = Popup(title=title, content=content, size_hint=(0.85, 0.4), auto_dismiss=True)
    cancel.bind(on_release=pop.dismiss)
    ok.bind(on_release=lambda *a: (pop.dismiss(), on_ok() if on_ok else None))
    pop.open()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
class VsolApp(App):
    title = "VSOL Admin"

    def build(self):
        try:
            return self._build()
        except BaseException:
            msg = "".join(traceback.format_exception(*sys.exc_info()))
            self.log_error(msg)
            Clock.schedule_once(lambda dt: self.show_error(msg), 0)
            return self._err_root(msg)

    def _err_root(self, msg):
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        box.add_widget(Label(text="Error al iniciar VSOL Admin:",
                             color=(1, 0.4, 0.4, 1), size_hint_y=None, height=dp(30),
                             font_name=FONT))
        box.add_widget(TextInput(text=msg, readonly=False, font_size=dp(9), font_name=FONT))
        return box

    def _build(self):
        Window.clearcolor = BG_DARK
        self.api = None
        self.names = {}
        self.blacklist = set()
        self.cfg_path = os.path.join(self.user_data_dir, CONFIG_FILE)
        self.load_cfg()
        self.auto_timer = None

        self.sm = ScreenManager()
        self.login_screen = LoginScreen(name="login")
        self.main_screen = MainScreen(name="main")
        self.sm.add_widget(self.login_screen)
        self.sm.add_widget(self.main_screen)
        return self.sm

    def log_error(self, msg):
        try:
            with open(os.path.join(self.user_data_dir, LOG_FILE), "a",
                      encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass

    def show_error(self, msg):
        content = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
        content.add_widget(Label(text="Ocurrio un error:", color=(1, 0.4, 0.4, 1),
                                 size_hint_y=None, height=dp(26)))
        ti = TextInput(text=msg, readonly=False, font_size=dp(9), font_name=FONT)
        content.add_widget(ti)
        close = Button(text="Cerrar", size_hint_y=None, height=dp(44),
                       font_name=FONT, background_color=(0.239, 0.494, 1.0, 1))
        content.add_widget(close)
        pop = Popup(title="VSOL Admin - Error", content=content,
                    size_hint=(0.95, 0.92))
        close.bind(on_release=pop.dismiss)
        pop.open()

    def on_start(self):
        if self.cfg.get("host"):
            self.login_screen.fill(self.cfg)

    # ---- config ----
    def load_cfg(self):
        self.cfg = {"host": "192.168.1.8", "user": "admin", "password": "", "names": {}}
        try:
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("host", "user", "password", "names"):
                    if k in data:
                        self.cfg[k] = data[k]
        except (OSError, ValueError):
            pass
        self.names = self.cfg.get("names", {}) or {}

    def save_cfg(self):
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---- login ----
    def do_login(self, host, user, password, err_label):
        err_label.text = "Conectando..."

        def work():
            api = V.RouterAPI(host, user, password)
            api.login()
            info = api.get_device_info()
            return api, info

        def done(res):
            api, info = res
            self.api = api
            self.cfg.update({
                "host": host.strip(),
                "user": user,
                "password": password,
                "names": self.names,
            })
            self.save_cfg()
            self.main_screen.on_logged_in(info)
            self.sm.current = "main"
            self.start_auto_refresh()

        def err(msg):
            err_label.text = "Error: %s" % msg

        Worker(work, done, err)

    def start_auto_refresh(self):
        self.stop_auto_refresh()
        self.auto_timer = Clock.schedule_interval(
            lambda dt: self.main_screen.estado.refresh(silent=True), 20)

    def stop_auto_refresh(self):
        if self.auto_timer:
            self.auto_timer.cancel()
            self.auto_timer = None

    def do_logout(self):
        if self.api:
            try:
                self.api.logout()
            except Exception:
                pass
        self.api = None
        self.stop_auto_refresh()
        self.sm.current = "login"

    def black(self):
        return self.blacklist

    def run_net(self, fn, on_done):
        def err(msg):
            self.main_screen.toast("Error: %s" % msg)
        Worker(fn, on_done, err)


# ---------------------------------------------------------------------------
# Pantalla login
# ---------------------------------------------------------------------------
class LoginScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(10))
        root.add_widget(Label(text="VSOL Admin", font_name=FONT, bold=True,
                              font_size=dp(24), color=TEXT_HEX, size_hint_y=None, height=dp(60)))
        root.add_widget(sub_label("Administra tu ONT VSOL desde el movil"))

        self.host_ti = self._f("IP del router", "192.168.1.8")
        self.user_ti = self._f("Usuario", "admin")
        self.pass_ti = TextInput(password=True, font_name=FONT, size_hint_y=None, height=dp(48),
                                 hint_text="Contraseña")
        self.err = Label(text="", color=(0.9, 0.35, 0.35, 1), font_name=FONT,
                         size_hint_y=None, height=dp(36))

        root.add_widget(self.host_ti)
        root.add_widget(self.user_ti)
        root.add_widget(self.pass_ti)
        root.add_widget(self.err)
        root.add_widget(make_button("Conectar", self._go))
        root.add_widget(Label(text="", size_hint_y=1))
        self.add_widget(root)

    @staticmethod
    def _f(hint, default):
        ti = TextInput(text=default, hint_text=hint, font_name=FONT,
                       size_hint_y=None, height=dp(48))
        return ti

    def fill(self, cfg):
        self.host_ti.text = cfg.get("host", "192.168.1.8")
        self.user_ti.text = cfg.get("user", "admin")
        self.pass_ti.text = cfg.get("password", "")

    def _go(self, *_):
        app = App.get_running_app()
        app.do_login(self.host_ti.text, self.user_ti.text,
                     self.pass_ti.text, self.err)


# ---------------------------------------------------------------------------
# Pantalla principal con navegacion inferior
# ---------------------------------------------------------------------------
class MainScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical")
        self.inner = ScreenManager()
        self.estado = EstadoScreen(name="estado")
        self.dispositivos = DevicesScreen(name="disp")
        self.negra = BlackScreen(name="negra")
        self.wifi = WifiScreen(name="wifi")
        self.mas = MoreScreen(name="mas")
        for s in (self.estado, self.dispositivos, self.negra, self.wifi, self.mas):
            self.inner.add_widget(s)
        root.add_widget(self.inner)

        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(54))
        names = [("Estado", "estado"), ("Disp.", "disp"), ("Negra", "negra"),
                 ("WiFi", "wifi"), ("Más", "mas")]
        self.nav_btns = {}
        for text, key in names:
            b = ColoredToggle(text=text, group="nav", font_name=FONT)
            b.bind(on_release=lambda _b, k=key: self.go_tab(k))
            nav.add_widget(b)
            self.nav_btns[key] = b
        root.add_widget(nav)
        self.add_widget(root)
        self.go_tab("estado")
        self._update_interval = None

    def go_tab(self, key):
        self.inner.current = key
        for k, b in self.nav_btns.items():
            b.state = "down" if k == key else "normal"

    def on_logged_in(self, info):
        self.refresh_all()

    def refresh_all(self):
        self.estado.refresh()
        self.dispositivos.refresh()
        self.negra.refresh()
        self.wifi.refresh()

    def toast(self, msg):
        self.estado.set_msg(msg)


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
class EstadoScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(8))
        self.msg = sub_label("")
        root.add_widget(self.msg)
        sv = ScrollView()
        col = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(4), size_hint_y=None)
        col.bind(minimum_height=col.setter("height"))
        col.add_widget(section("Dispositivo"))
        self.dev = [InfoRow("Modelo"), InfoRow("Firmware"), InfoRow("Uptime"),
                    InfoRow("MAC")]
        for r in self.dev:
            col.add_widget(r)
        col.add_widget(section("Optica PON"))
        self.pon = [InfoRow("Temperatura"), InfoRow("Voltaje"),
                    InfoRow("Tx power"), InfoRow("Rx power")]
        for r in self.pon:
            col.add_widget(r)
        col.add_widget(section("Recursos"))
        self.res = [InfoRow("CPU %"), InfoRow("Mem %")]
        for r in self.res:
            col.add_widget(r)
        col.add_widget(Label(size_hint_y=None, height=dp(8)))
        sv.add_widget(col)
        root.add_widget(sv)
        root.add_widget(make_button("Refrescar estado", self._go_refresh))
        self.add_widget(root)
        self._on_done = None

    def set_msg(self, txt):
        self.msg.text = txt

    def _go_refresh(self, *_):
        self.refresh()

    def refresh(self, silent=False):
        app = App.get_running_app()
        if not app.api:
            return
        if not silent:
            self.set_msg("Actualizando...")

        def work():
            return (app.api.get_device_info(), app.api.get_pon(),
                    app.api.get_resource())

        def done(res):
            info, pon, resv = res
            self.set_msg("")
            for row, key in zip(self.dev, ("devModel", "stVer", "web_uptime", "mac_address")):
                row.val.text = (info.get(key) or "-")
            for row, key in zip(self.pon, ("temperature", "voltage", "tx-power", "rx-power")):
                row.val.text = (pon.get(key) or "-")
            for row, key in zip(self.res, ("cpUsage", "memUsage")):
                row.val.text = (resv.get(key) or "-")

        def err(msg):
            self.set_msg("Error: %s" % msg)

        Worker(work, done, err)


# ---------------------------------------------------------------------------
# Dispositivos conectados
# ---------------------------------------------------------------------------
class DevicesScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        self.msg = sub_label("")
        root.add_widget(self.msg)
        sv = ScrollView()
        self.col = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.col.bind(minimum_height=self.col.setter("height"))
        sv.add_widget(self.col)
        root.add_widget(sv)
        root.add_widget(make_button("Actualizar", self._go))
        self.add_widget(root)

    def _go(self, *_):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        if not app.api:
            return
        self.set_msg("Cargando...")

        def work():
            return (app.api.get_wifi_clients(), app.api.get_client_details(),
                    app.api.get_dhcp_clients())

        def done(data):
            wifi, details, dhcp = data
            self.set_msg("")
            self.col.clear_widgets()
            for c in wifi:
                mac = c.get("mac_addr", "").lower()
                if norm_mac(mac) in app.black():
                    continue
                d = details.get(mac, {})
                self._row(app, name_of(app, mac, d.get("host") or ""),
                          mac, d.get("ip", c.get("ip", "-")) or "-",
                          d.get("linkSSID", "-"))
            for c in dhcp:
                mac = c.get("macAddr", "").lower()
                if norm_mac(mac) in app.black():
                    continue
                self._row(app, name_of(app, mac, c.get("nickname", "")),
                          mac, c.get("ipAddr", "-"), "-")
            if not self.col.children:
                self.col.add_widget(Label(text="Sin dispositivos",
                                          color=SUB_HEX, font_name=FONT))

        Worker(work, done)

    def set_msg(self, txt):
        self.msg.text = txt

    def _row(self, app, name, mac, ip, red):
        name = name or "-"
        row = Card(orientation="vertical", spacing=dp(2),
                   size_hint_y=None, height=dp(62))
        top = BoxLayout(orientation="horizontal", spacing=dp(6))
        top.add_widget(Label(text=name, bold=True, color=TEXT_HEX, font_name=FONT,
                             halign="left"))
        btn = Button(text="Bloquear", size_hint=(None, 1), width=dp(92),
                     font_name=FONT, font_size=dp(12),
                     background_color=(0.85, 0.3, 0.3, 1))
        btn.bind(on_release=lambda _b, m=mac, n=name: self.block(app, m, n))
        top.add_widget(btn)
        row.add_widget(top)
        row.add_widget(Label(text="%s  %s  %s" % (mac, ip, red), color=SUB_HEX,
                             font_name=FONT, font_size=dp(12), halign="left"))
        self.col.add_widget(row)

    def block(self, app, mac, name):
        norm = norm_mac(mac)
        if not norm:
            return

        def act():
            app.api.mac_filter_set_black_on()
            app.api.mac_filter_add(norm, "0")

        def done(resp):
            if name and name != "-":
                app.names.setdefault(norm.lower(), name)
                app.cfg["names"] = app.names
                app.save_cfg()
            app.main_screen.negra.refresh()
            self.refresh()

        confirm(app, "Bloquear", "Bloquear %s (%s)?" % (name, norm), lambda: app.run_net(act, done))


def name_of(app, mac, fallback):
    m = mac.lower()
    return app.names.get(m) or fallback or ""


# ---------------------------------------------------------------------------
# Lista negra
# ---------------------------------------------------------------------------
class BlackScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        self.msg = sub_label("")
        root.add_widget(self.msg)
        sv = ScrollView()
        self.col = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.col.bind(minimum_height=self.col.setter("height"))
        sv.add_widget(self.col)
        root.add_widget(sv)
        addrow = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(6))
        self.ti = TextInput(hint_text="MAC a bloquear (ej. aa:bb:cc:dd:ee:ff)",
                            font_name=FONT, font_size=dp(12))
        btn = Button(text="Añadir", size_hint=(None, 1), width=dp(110),
                     font_name=FONT, background_color=(0.239, 0.494, 1.0, 1))
        btn.bind(on_release=lambda _b: self.add_manual())
        addrow.add_widget(self.ti)
        addrow.add_widget(btn)
        root.add_widget(addrow)
        root.add_widget(make_button("Actualizar", self._go))
        self.add_widget(root)

    def _go(self, *_):
        self.refresh()

    def set_msg(self, txt):
        self.msg.text = txt

    def refresh(self):
        app = App.get_running_app()
        if not app.api:
            return
        self.set_msg("Cargando...")

        def work():
            return app.api.get_mac_filter()

        def done(flt):
            app.blacklist = set()
            for e in flt["entries"]:
                if e.get("mac_type", "").strip() == "0":
                    n = norm_mac(e.get("Mac_Addr", ""))
                    if n:
                        app.blacklist.add(n)
            mode = "lista negra" if flt.get("mode", "0") == "0" else "lista blanca"
            act = "ACTIVO" if flt["enable"] == "1" else "desactivado"
            self.set_msg("Filtro MAC: %s (%s) - %d bloqueados" % (act, mode, len(app.blacklist)))
            self.col.clear_widgets()
            for e in flt["entries"]:
                if e.get("mac_type", "").strip() != "0":
                    continue
                m = e.get("Mac_Addr", "")
                n = norm_mac(m)
                if not n:
                    continue
                row = Card(orientation="horizontal", spacing=dp(8),
                           size_hint_y=None, height=dp(54))
                row.add_widget(Label(text="%s\n%s" % (name_of(app, n.lower(), ""), n.upper()),
                                     color=TEXT_HEX, font_name=FONT, font_size=dp(12),
                                     halign="left"))
                q = Button(text="Quitar", size_hint=(None, 1), width=dp(96),
                           font_name=FONT, font_size=dp(12),
                           background_color=(0.85, 0.3, 0.3, 1))
                q.bind(on_release=lambda _b, mm=n: self.unblock(mm))
                row.add_widget(q)
                self.col.add_widget(row)

        Worker(work, done)

    def add_manual(self):
        app = App.get_running_app()
        norm = norm_mac(self.ti.text)
        if not norm:
            self.set_msg("MAC no valida")
            return
        self.ti.text = ""

        def act():
            app.api.mac_filter_set_black_on()
            return app.api.mac_filter_add(norm, "0")

        def done(resp):
            self.refresh()

        app.run_net(act, done)

    def unblock(self, norm):
        app = App.get_running_app()

        def act():
            return app.api.mac_filter_remove(norm)

        def done(resp):
            self.refresh()

        confirm(app, "Desbloquear", "Quitar %s de la lista negra?" % norm,
                lambda: app.run_net(act, done))


# ---------------------------------------------------------------------------
# WiFi
# ---------------------------------------------------------------------------
class WifiScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.band = True   # True = 2.4G
        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
        self.msg = sub_label("")
        root.add_widget(self.msg)
        bands = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        self.b24 = ColoredToggle(text="2.4G", group="band", font_name=FONT)
        self.b5 = ColoredToggle(text="5G", group="band", font_name=FONT)
        self.b24.state = "down"
        self.b24.bind(on_release=self._set_band24)
        self.b5.bind(on_release=self._set_band5)
        bands.add_widget(self.b24)
        bands.add_widget(self.b5)
        root.add_widget(bands)
        self.ssid_ti = TextInput(hint_text="SSID", font_name=FONT, size_hint_y=None, height=dp(48))
        self.psk_ti = TextInput(hint_text="Clave (PSK)", font_name=FONT, size_hint_y=None,
                                height=dp(48), password=True)
        root.add_widget(self.ssid_ti)
        root.add_widget(self.psk_ti)
        self.save_btn = make_button("Guardar cambios (reinicia la red)", self._go_save)
        root.add_widget(self.save_btn)
        root.add_widget(Label(text="", size_hint_y=1))
        self.add_widget(root)

    def _set_band24(self, *_):
        self.band = True
        self.refresh()

    def _set_band5(self, *_):
        self.band = False
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        if not app.api:
            return
        self.set_msg("Cargando...")

        def work():
            f, w = app.api.get_wifi_settings(self.band)
            sec = {}
            for k, v in w.items():
                sec = v
                break
            return f, sec

        def done(res):
            f, sec = res
            self.set_msg("")
            self.ssid_ti.text = f.get("ssid", "")
            self.psk_ti.text = sec.get("pskValue", "")

        Worker(work, done)

    def set_msg(self, txt):
        self.msg.text = txt

    def _go_save(self, *_):
        app = App.get_running_app()
        tag = "2.4G" if self.band else "5G"

        def work():
            cur, wpa = app.api.get_wifi_settings(self.band)
            sec = {}
            for k, v in wpa.items():
                sec = v
                break
            basic = {
                "wlanDisabled": "0",
                "band": cur.get("band", "0"),
                "mode": cur.get("mode", "0"),
                "ssid": self.ssid_ti.text,
                "wlHide": "0",
                "wl_access": cur.get("wl_access", "0"),
                "wl_wmm_func": cur.get("wl_wmm_func", "1"),
                "chanwid": cur.get("chanwid", ""),
                "ctlband": "0",
                "chan": cur.get("chan", "0"),
                "txpower": cur.get("txpower", "100"),
                "regdomain_demo": cur.get("regdomain_demo", "14"),
                "wl_stanum": cur.get("wl_stanum", "0"),
                "basicrates": cur.get("basicrates", ""),
                "operrates": cur.get("operrates", ""),
                "WiFiTest": "0",
            }
            sec_fields = {
                "wpaSSID": sec.get("index", "0"),
                "security_method": sec.get("security_method", "20"),
                "wpaAuth": "2",
                "use1x": "0",
                "auth_type": sec.get("auth_type", "2"),
                "length0": sec.get("length0", "0"),
                "format0": sec.get("format0", "1"),
                "key0": sec.get("key0", ""),
                "ciphersuite_t": sec.get("ciphersuite_t", "0"),
                "ciphersuite_a": sec.get("ciphersuite_a", "0"),
                "wpa2ciphersuite_t": sec.get("wpa2ciphersuite_t", "0"),
                "wpa2ciphersuite_a": sec.get("wpa2ciphersuite_a", "1"),
                "wpa3ciphersuite_a": sec.get("wpa3ciphersuite_a", "2"),
                "pskFormat": sec.get("pskFormat", "0"),
                "pskValue": self.psk_ti.text,
                "gk_rekey": sec.get("gk_rekey", "86400"),
                "radiusIP": sec.get("radiusIP", "0.0.0.0"),
                "radiusPort": sec.get("radiusPort", "1812"),
                "radiusPass": sec.get("radiusPass", ""),
                "wepKeyLen": sec.get("wepKeyLen", "0"),
            }
            r1 = app.api.save_wifi_basic(self.band, basic)
            r2 = app.api.save_wifi_security(self.band, sec_fields)
            return r1, r2

        def done(res):
            self.set_msg("WiFi %s guardado. La red se cortara unos segundos." % tag)

        confirm(app, "Guardar WiFi", "Aplicar cambios WiFi %s?\nLa red se reiniciara unos segundos." % tag,
                lambda: app.run_net(work, done))


# ---------------------------------------------------------------------------
# Mas
# ---------------------------------------------------------------------------
class MoreScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        root.add_widget(Label(text="Opciones", bold=True, color=TEXT_HEX, font_size=dp(16),
                              font_name=FONT, size_hint_y=None, height=dp(30)))
        root.add_widget(make_button("Abrir web del router", self._web))
        root.add_widget(make_button("Reiniciar router", self._reboot))
        root.add_widget(make_button("Cerrar sesion", self._logout))
        root.add_widget(Label(text="", size_hint_y=1))
        self.add_widget(root)

    def _web(self, *_):
        app = App.get_running_app()
        if app.api:
            webbrowser.open(app.api.host)

    def _reboot(self, *_):
        app = App.get_running_app()

        def act():
            return app.api.reboot()

        def done(resp):
            app.main_screen.toast("Reiniciando router...")

        confirm(app, "Reiniciar", "Reiniciar el router? Se cortara todo unos minutos.",
                lambda: app.run_net(act, done))

    def _logout(self, *_):
        confirm(App.get_running_app(), "Cerrar sesion",
                "Cerrar la sesion en el router?", App.get_running_app().do_logout)


if __name__ == "__main__":
    sys.excepthook = _excepthook
    ExceptionManager.add_handler(ErrorReporter())
    VsolApp().run()