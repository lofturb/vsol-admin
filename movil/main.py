# -*- coding: utf-8 -*-
"""VSOL Admin movil - app Kivy para administrar el router VSOL V2804AX.

Reutiliza vrouter.py (misma capa de red que la version de escritorio).
Rediseno: tema oscuro moderno, tarjetas redondeadas, dashboard en Estado,
buscador y nombres locales en Dispositivos, ojito para claves WiFi.
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
from kivy.graphics import Color, InstructionGroup, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

import vrouter as V

# ---------------------------------------------------------------------------
# Tema
# ---------------------------------------------------------------------------
ACCENT = (0.29, 0.54, 1.0, 1.0)          # azul de marca
ACCENT_DOWN = (0.20, 0.40, 0.85, 1.0)    # azul pulsado
BG = (0.11, 0.115, 0.125, 1.0)           # fondo app
CARD = (0.165, 0.17, 0.185, 1.0)         # tarjetas
FIELD = (0.13, 0.135, 0.15, 1.0)         # inputs
TEXT = (0.94, 0.95, 0.97, 1.0)
SUB = (0.60, 0.63, 0.68, 1.0)
DANGER = (0.86, 0.33, 0.32, 1.0)
OK = (0.24, 0.78, 0.44, 1.0)
HINT = (0.72, 0.75, 0.79, 1.0)            # texto de placeholder (mas claro que SUB)
R = dp(12)                                # radio de esquinas
RF = dp(16)                               # radio de esquinas de los inputs

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


def mac_key(m):
    n = V._norm_mac(m)
    return n.lower() if n else None


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
class Worker(object):
    """Ejecuta fn en un hilo y programa on_done/on_error en el hilo de UI."""

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


def section(text, color=SUB):
    return Label(text=text.upper(), bold=True, color=color, font_name=FONT,
                 font_size=dp(11), halign="left", size_hint_y=None, height=dp(26))


class Rounded(BoxLayout):
    """BoxLayout con fondo redondeado (base de tarjetas)."""

    def __init__(self, bg=CARD, radius=R, **kw):
        super().__init__(**kw)
        self._bg = bg
        with self.canvas.before:
            self._col = Color(*bg)
            self._rect = RoundedRectangle(radius=[radius, radius, radius, radius])
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_bg(self, c):
        self._bg = c
        self._col.rgba = c


class Card(Rounded):
    def __init__(self, **kw):
        # Si no se indica orientacion, las tarjetas apilan en vertical. Sin
        # esto, Card() heredaba el horizontal de BoxLayout y las 4 filas se
        # colocaban una al lado de otra con minimum_height = max(fila):
        # texto superpuesto en las tarjetas de Estado.
        kw.setdefault("orientation", "vertical")
        kw.setdefault("padding", [dp(12), dp(10)])
        kw.setdefault("spacing", dp(4))
        super().__init__(bg=CARD, **kw)
        # Las tarjetas usan SIEMPRE su tamano de contenido (si no, en una
        # columna con minimum_height se colapsan y sus hijos se superponen).
        self.size_hint_y = None
        self.height = self.minimum_height
        self.bind(minimum_height=self.setter("height"))


def field(hint="", **kw):
    """TextInput estilo app (fondo oscuro redondeado).

    IMPORTANTE:
    - El relleno se INSERTA al principio de canvas.before. El TextInput de
      Kivy dibuja el texto con el color GL que deja el ultimo Color de
      canvas.before (ver style.kv), asi que appender aqui rompe el color de
      las letras (texto oscuro sobre fondo oscuro).
    - background_color=(0,0,0,0) para anular la textura blanca por defecto que
      Kivy pinta aunque background_normal="" (cuadro blanco sobre el fondo).
    """
    kw.setdefault("size_hint_y", None)
    kw.setdefault("height", dp(48))
    kw.setdefault("font_name", FONT)
    kw.setdefault("hint_text_color", HINT)
    kw.setdefault("foreground_color", (0.97, 0.98, 1.0, 1.0))
    kw.setdefault("cursor_color", ACCENT)
    kw.setdefault("padding", [dp(12), dp(12), dp(12), dp(12)])
    ti = TextInput(background_normal="", background_active="",
                   background_color=(0, 0, 0, 0), hint_text=hint, **kw)
    grp = InstructionGroup()
    grp.add(Color(*FIELD))
    rect = RoundedRectangle(radius=[RF, RF, RF, RF], pos=ti.pos, size=ti.size)
    grp.add(rect)
    ti.canvas.before.insert(0, grp)

    def _redraw(*a):
        rect.pos = ti.pos
        rect.size = ti.size

    ti.bind(pos=_redraw, size=_redraw)
    return ti


def accented_btn(text, on_release=None, bg=ACCENT, **kw):
    """Boton redondeado con fondo solido."""
    kw.setdefault("size_hint_y", None)
    kw.setdefault("height", dp(46))
    kw.setdefault("font_name", FONT)
    kw.setdefault("font_size", dp(14))
    b = Button(text=text, background_normal="", background_down="",
               background_color=(0, 0, 0, 0), color=(1, 1, 1, 1), **kw)
    with b.canvas.before:
        b._col = Color(*bg)
        b._rect = RoundedRectangle(radius=[R, R, R, R])

    def _redraw(*a):
        b._rect.pos = b.pos
        b._rect.size = b.size

    b.bind(pos=_redraw, size=_redraw)
    b._on_press = lambda *a: setattr(b._col, "rgba", ACCENT_DOWN if bg == ACCENT else bg)
    b._on_release = lambda *a: setattr(b._col, "rgba", bg)
    b.bind(on_press=b._on_press, on_release=b._on_release)
    if on_release:
        b.bind(on_release=on_release)
    return b


def quiet_btn(text, color=SUB, **kw):
    """Boton sin fondo (texto de color)."""
    kw.setdefault("font_name", FONT)
    kw.setdefault("font_size", dp(13))
    return Button(text=text, background_normal="", background_down="",
                  background_color=(0, 0, 0, 0), color=color, **kw)


def dark_popup(title, content, size_hint=(0.9, 0.5)):
    """Popup con marco oscuro (el Popup por defecto es blanco y las letras
    claras no se leen)."""
    frame = Rounded(bg=(0.145, 0.15, 0.165, 1), padding=dp(14),
                    spacing=dp(6), orientation="vertical")
    if title:
        frame.add_widget(Label(text=title, bold=True, color=TEXT, font_name=FONT,
                               size_hint_y=None, height=dp(28)))
    frame.add_widget(content)
    pop = Popup(title="", content=frame, size_hint=size_hint, auto_dismiss=True,
                background="", background_color=(0, 0, 0, 0),
                separator_color=(0, 0, 0, 0))
    return pop


def confirm(app, title, text, on_ok):
    content = BoxLayout(orientation="vertical", padding=dp(0), spacing=dp(12))
    content.add_widget(Label(text=text, color=TEXT, font_name=FONT))
    btns = BoxLayout(orientation="horizontal", spacing=dp(12),
                     size_hint_y=None, height=dp(46))
    cancel = accented_btn("Cancelar", bg=(0.28, 0.30, 0.34, 1), size_hint_x=1)
    ok = accented_btn("OK", bg=DANGER, size_hint_x=1)
    btns.add_widget(cancel)
    btns.add_widget(ok)
    content.add_widget(btns)
    pop = dark_popup(title, content, size_hint=(0.9, 0.42))
    cancel.bind(on_release=pop.dismiss)
    ok.bind(on_release=lambda *a: (pop.dismiss(), on_ok() if on_ok else None))
    pop.open()


def pw_field(parent, hint, default="", **kw):
    """Input de contrasena con boton ojito (Mostrar/Ocultar)."""
    ti = field(hint, text=default, password=True, **kw)
    eye = quiet_btn("Mostrar", color=ACCENT, width=dp(96), size_hint=(None, 1))

    def tog(*a):
        ti.password = not ti.password
        eye.text = "Ocultar" if not ti.password else "Mostrar"

    eye.bind(on_release=tog)
    row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
    row.add_widget(ti)
    row.add_widget(eye)
    parent.add_widget(row)
    return ti


def name_of(app, mac, fallback):
    m = mac_key(mac)
    return (app.names.get(m) if m else None) or fallback or ""


# ---------------------------------------------------------------------------
# Dispositivos: recopilacion + tarjeta + acciones
# ---------------------------------------------------------------------------
def collect_devices(app, wifi, details, dhcp):
    """Une clientes wifi y DHCP en filas {mac, name, ip, red}, sin bloqueados."""
    rows = []
    seen = set()
    for c in wifi:
        k = mac_key(c.get("mac_addr", ""))
        if not k or k in app.black() or k in seen:
            continue
        seen.add(k)
        d = details.get(c.get("mac_addr", "").lower(), {})
        rows.append({
            "mac": k, "ip": d.get("ip", c.get("ip", "-")) or "-",
            "red": d.get("linkSSID", "-") or "-",
            "name": name_of(app, k, d.get("host", "") or ""),
        })
    for c in dhcp:
        k = mac_key(c.get("macAddr", ""))
        if not k or k in app.black() or k in seen:
            continue
        seen.add(k)
        rows.append({
            "mac": k, "ip": c.get("ipAddr", "-") or "-", "red": "-",
            "name": name_of(app, k, c.get("nickname", "") or ""),
        })
    return rows


def fetch_devices(app):
    return collect_devices(app, app.api.get_wifi_clients(),
                           app.api.get_client_details(),
                           app.api.get_dhcp_clients())


def render_devices(col, rows, rename_cb_maker, block_cb_maker):
    col.clear_widgets()
    if not rows:
        col.add_widget(Label(text="Sin dispositivos", color=SUB,
                             font_name=FONT, size_hint_y=None, height=dp(40)))
        return
    for r in rows:
        d = DeviceCard(r["name"], r["mac"], r["ip"], r["red"],
                       rename_cb_maker(r), block_cb_maker(r))
        col.add_widget(d)


class DeviceCard(Card):
    """Tarjeta de dispositivo conectado: nombre, MAC, IP, red y acciones."""

    def __init__(self, name, mac, ip, red, rename_cb, block_cb, **kw):
        super().__init__(orientation="vertical", spacing=dp(4), **kw)
        top = BoxLayout(orientation="horizontal", spacing=dp(6),
                        size_hint_y=None, height=dp(26))
        top.add_widget(Label(text=name or "-", bold=True, color=TEXT, font_name=FONT,
                             font_size=dp(14), halign="left"))
        self.add_widget(top)
        self.add_widget(Label(text="%s  ·  %s  ·  %s" % (mac.upper(), ip, red),
                              color=SUB, font_name=FONT, font_size=dp(12),
                              halign="left", size_hint_y=None, height=dp(20)))
        btns = BoxLayout(orientation="horizontal", spacing=dp(10),
                         size_hint_y=None, height=dp(36))
        rb = quiet_btn("Renombrar", color=ACCENT,
                       size_hint=(None, 1), width=dp(130), font_size=dp(13))
        rb.bind(on_release=lambda *a: rename_cb())
        bb = quiet_btn("Bloquear", color=DANGER,
                       size_hint=(None, 1), width=dp(120), font_size=dp(13))
        bb.bind(on_release=lambda *a: block_cb())
        btns.add_widget(rb)
        btns.add_widget(bb)
        self.add_widget(btns)


def ask_rename(app, mac, current, on_done):
    content = BoxLayout(orientation="vertical", spacing=dp(12))
    content.add_widget(Label(text="Nombre para %s" % mac.upper(),
                             color=TEXT, font_name=FONT))
    ti = field("Nombre o alias", text=current)
    content.add_widget(ti)
    btns = BoxLayout(orientation="horizontal", spacing=dp(12),
                     size_hint_y=None, height=dp(46))
    cancel = accented_btn("Cancelar", bg=(0.28, 0.30, 0.34, 1), size_hint_x=1)
    ok = accented_btn("Guardar", bg=ACCENT, size_hint_x=1)
    btns.add_widget(cancel)
    btns.add_widget(ok)
    content.add_widget(btns)
    pop = dark_popup("Renombrar", content, size_hint=(0.9, 0.5))

    def save(*_):
        name = ti.text.strip()
        if name:
            app.names[mac] = name
        else:
            app.names.pop(mac, None)
        app.cfg["names"] = app.names
        app.save_cfg()
        pop.dismiss()
        on_done(name)

    cancel.bind(on_release=pop.dismiss)
    ok.bind(on_release=save)
    pop.open()
    Clock.schedule_once(lambda dt: setattr(ti, "focus", True), 0.2)


def block_device(app, mac, name, after):
    norm = norm_mac(mac)
    if not norm:
        return
    norm = norm.lower()

    def act():
        app.api.mac_filter_set_black_on()
        app.api.mac_filter_add(norm, "0")

    def done(resp):
        after()

    confirm(app, "Bloquear", "Bloquear %s (%s)?" % (name or "-", norm.upper()),
            lambda: app.run_net(act, done))


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
        Window.clearcolor = BG
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
        content = BoxLayout(orientation="vertical", spacing=dp(8))
        content.add_widget(Label(text="Ocurrio un error:", color=(1, 0.4, 0.4, 1),
                                 size_hint_y=None, height=dp(26)))
        ti = TextInput(text=msg, readonly=False, font_size=dp(9), font_name=FONT)
        content.add_widget(ti)
        close = accented_btn("Cerrar")
        content.add_widget(close)
        pop = dark_popup("VSOL Admin - Error", content, size_hint=(0.95, 0.92))
        close.bind(on_release=pop.dismiss)
        pop.open()

    def on_start(self):
        try:
            cfg = self.cfg
        except AttributeError:
            cfg = {"host": "", "user": "admin", "password": "", "names": {},
                   "save_creds": True}
        ls = getattr(self, "login_screen", None)
        if ls is not None and getattr(ls, "remember", None) is not None:
            try:
                ls.remember.active = bool(cfg.get("save_creds", True))
                if cfg.get("host"):
                    ls.fill(cfg)
            except Exception:
                pass

    # ---- config ----
    def load_cfg(self):
        self.cfg = {"host": "192.168.1.8", "user": "admin", "password": "",
                    "names": {}, "save_creds": True}
        try:
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("host", "user", "password", "names", "save_creds"):
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
    def do_login(self, host, user, password, err_label, remember):
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
                "password": password if remember else "",
                "names": self.names,
                "save_creds": remember,
            })
            self.save_cfg()
            self.login_screen.remember.active = remember
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
        root = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        brand = Rounded(bg=CARD, orientation="vertical", padding=[dp(16), dp(14)],
                        spacing=dp(4), size_hint_y=None, height=dp(96))
        brand.add_widget(Label(text="VSOL Admin", bold=True, font_name=FONT,
                               font_size=dp(22), color=TEXT))
        brand.add_widget(Label(text="Administra tu ONT VSOL desde el movil",
                               font_name=FONT, font_size=dp(13), color=SUB))
        root.add_widget(brand)

        self.host_ti = field("IP del router", text="192.168.1.8")
        self.user_ti = field("Usuario", text="admin")
        root.add_widget(self.host_ti)
        root.add_widget(self.user_ti)
        self.pass_ti = pw_field(root, "Contraseña", "")
        remrow = BoxLayout(orientation="horizontal", size_hint=(1, None), height=dp(34))
        self.remember = CheckBox(active=True, color=ACCENT,
                                 size_hint=(None, 1), width=dp(40))
        remrow.add_widget(self.remember)
        remrow.add_widget(Label(text="Recordar usuario y contraseña en este movil",
                                color=SUB, font_name=FONT, font_size=dp(12),
                                halign="left"))
        root.add_widget(remrow)
        self.err = Label(text="", color=DANGER, size_hint_y=None, height=dp(30),
                         font_name=FONT)
        root.add_widget(self.err)
        root.add_widget(accented_btn("Conectar", self._go))
        root.add_widget(Label(text="", size_hint_y=1))
        self.add_widget(root)

    def fill(self, cfg):
        self.host_ti.text = cfg.get("host", "192.168.1.8")
        self.user_ti.text = cfg.get("user", "admin")
        self.remember.active = bool(cfg.get("save_creds", True))
        self.pass_ti.text = (cfg.get("password", "")
                             if cfg.get("save_creds", True) else "")

    def _go(self, *_):
        app = App.get_running_app()
        app.do_login(self.host_ti.text, self.user_ti.text,
                     self.pass_ti.text, self.err, self.remember.active)


# ---------------------------------------------------------------------------
# Pantalla principal con navegacion inferior
# ---------------------------------------------------------------------------
class MainScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical")

        bar = BoxLayout(padding=[dp(14), dp(4), dp(14), dp(4)], spacing=dp(8),
                        size_hint_y=None, height=dp(52))
        bar.add_widget(Label(text="VSOL Admin", bold=True, color=TEXT,
                             font_name=FONT, font_size=dp(17), halign="left"))
        salir = quiet_btn("Salir", color=SUB, size_hint=(None, 1), width=dp(70))
        salir.bind(on_release=lambda *a: App.get_running_app().do_logout())
        bar.add_widget(salir)
        root.add_widget(bar)

        self.inner = ScreenManager()
        self.estado = EstadoScreen(name="estado")
        self.dispositivos = DevicesScreen(name="disp")
        self.negra = BlackScreen(name="negra")
        self.wifi = WifiScreen(name="wifi")
        self.mas = MoreScreen(name="mas")
        for s in (self.estado, self.dispositivos, self.negra, self.wifi, self.mas):
            self.inner.add_widget(s)
        root.add_widget(self.inner)

        nav = BoxLayout(orientation="horizontal", spacing=dp(6),
                        padding=[dp(10), dp(6), dp(10), dp(10)],
                        size_hint_y=None, height=dp(56))
        names = [("Estado", "estado"), ("Disp.", "disp"), ("Negra", "negra"),
                 ("WiFi", "wifi"), ("Más", "mas")]
        self.nav_btns = {}
        for text, key in names:
            b = NavToggle(text=text, group="nav")
            b.bind(on_release=lambda _b, k=key: self.go_tab(k))
            nav.add_widget(b)
            self.nav_btns[key] = b
        root.add_widget(nav)
        self.add_widget(root)
        self.go_tab("estado")

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
        self.estado._clear_msg()


class NavToggle(ToggleButton):
    """Pestana de navegacion con fondo redondeado y estado activo en azul."""

    def __init__(self, **kw):
        kw.setdefault("font_name", FONT)
        super().__init__(background_normal="", background_down="",
                         background_color=(0, 0, 0, 0), color=SUB, **kw)
        with self.canvas.before:
            self._col = Color(*CARD)
            self._rect = RoundedRectangle(radius=[dp(10), dp(10), dp(10), dp(10)])
        self.bind(pos=self._redraw, size=self._redraw, state=self._sync)
        self._sync()

    def _redraw(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def _sync(self, *a):
        down = self.state == "down"
        self._col.rgba = ACCENT if down else CARD
        self.color = (1, 1, 1, 1) if down else SUB


# ---------------------------------------------------------------------------
# Estado (dashboard)
# ---------------------------------------------------------------------------
class EstadoScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(10))
        self.msg = Label(text="", color=SUB, font_name=FONT, font_size=dp(12),
                         size_hint_y=None, height=dp(24), halign="center")
        root.add_widget(self.msg)
        sv = ScrollView()
        col = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        col.bind(minimum_height=col.setter("height"))

        col.add_widget(section("Dispositivo"))
        rc = Card()
        self.dev = [InfoRow("Modelo"), InfoRow("Firmware"),
                    InfoRow("Tiempo activo"), InfoRow("MAC")]
        for r in self.dev:
            rc.add_widget(r)
        col.add_widget(rc)

        col.add_widget(section("Optica PON"))
        pc = Card()
        self.pon = [InfoRow("Temperatura"), InfoRow("Voltaje"),
                    InfoRow("Tx power"), InfoRow("Rx power")]
        for r in self.pon:
            pc.add_widget(r)
        col.add_widget(pc)

        col.add_widget(section("Recursos"))
        msc = Card(orientation="horizontal", spacing=dp(12))
        self.cpu_lbl = StatBox("CPU")
        self.mem_lbl = StatBox("Mem")
        msc.add_widget(self.cpu_lbl)
        msc.add_widget(self.mem_lbl)
        col.add_widget(msc)

        col.add_widget(section("Dispositivos conectados"))
        self.dcol = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.dcol.bind(minimum_height=self.dcol.setter("height"))
        col.add_widget(self.dcol)

        col.add_widget(Label(size_hint_y=None, height=dp(8)))
        sv.add_widget(col)
        root.add_widget(sv)
        root.add_widget(accented_btn("Refrescar estado", self._go_refresh))
        self.add_widget(root)

    def set_msg(self, txt):
        self.msg.text = txt

    def _clear_msg(self):
        try:
            Clock.unschedule(self._msg_timer)
        except Exception:
            pass
        self._msg_timer = Clock.schedule_once(lambda dt: self.set_msg(""), 3.5)

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
                    app.api.get_resource(), fetch_devices(app))

        def done(res):
            info, pon, resv, rows = res
            self.set_msg("%d dispositivo(s) conectado(s)" % len(rows))
            for row, key in zip(self.dev, ("devModel", "stVer", "web_uptime", "mac_address")):
                row.val.text = (info.get(key) or "-").upper() if key == "mac_address" else (info.get(key) or "-")
            for row, key in zip(self.pon, ("temperature", "voltage", "tx-power", "rx-power")):
                row.val.text = (pon.get(key) or "-")
            self.cpu_lbl.val.text = pct(resv.get("cpUsage"))
            self.mem_lbl.val.text = pct(resv.get("memUsage"))
            render_devices(self.dcol, rows,
                           lambda r: (lambda: self._rename(r)),
                           lambda r: (lambda: self._block(r)))

        def err(msg):
            self.set_msg("Error: %s" % msg)

        Worker(work, done, err)

    def _rename(self, r):
        app = App.get_running_app()
        ask_rename(app, r["mac"], r["name"] or "",
                   lambda *_a: (self.refresh(), app.main_screen.dispositivos.refresh()))

    def _block(self, r):
        app = App.get_running_app()
        block_device(app, r["mac"], r["name"],
                     lambda: (self.refresh(), app.main_screen.dispositivos.refresh(),
                              app.main_screen.negra.refresh()))


def pct(v):
    v = v or "-"
    if isinstance(v, str) and v.endswith("%"):
        return v
    return "%s%%" % v if v != "-" else "-"


class StatBox(Card):
    """Panel de recurso (CPU o memoria) con su valor grande."""

    def __init__(self, label, **kw):
        super().__init__(orientation="vertical", spacing=dp(2), **kw)
        self.add_widget(Label(text=label, color=SUB, font_name=FONT,
                              font_size=dp(11), size_hint_y=None, height=dp(18)))
        self.val = Label(text="-", bold=True, color=TEXT, font_name=FONT,
                         font_size=dp(20), size_hint_y=None, height=dp(34))
        self.add_widget(self.val)


class InfoRow(BoxLayout):
    def __init__(self, label, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(28), **kw)
        self.add_widget(Label(text=label, size_hint_x=0.5, halign="left",
                              color=SUB, font_name=FONT))
        self.val = Label(text="-", halign="left", color=TEXT, font_name=FONT)
        self.add_widget(self.val)


# ---------------------------------------------------------------------------
# Dispositivos conectados (buscador + renombrar)
# ---------------------------------------------------------------------------
class DevicesScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(6))
        self.msg = Label(text="", color=SUB, font_name=FONT, font_size=dp(12),
                         size_hint_y=None, height=dp(22))
        root.add_widget(self.msg)
        self.search_ti = field("Buscar por nombre, MAC o IP...")
        self.search_ti.height = dp(44)
        self.search_ti.bind(text=lambda *_a: self._rerender())
        root.add_widget(self.search_ti)
        sv = ScrollView()
        self.col = BoxLayout(orientation="vertical", spacing=dp(8),
                             size_hint_y=None, padding=[0, dp(2)])
        self.col.bind(minimum_height=self.col.setter("height"))
        sv.add_widget(self.col)
        root.add_widget(sv)
        root.add_widget(accented_btn("Actualizar", self._go))
        self.add_widget(root)
        self.rows = []

    def _go(self, *_):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        if not app.api:
            return
        self.set_msg("Cargando...")

        def work():
            return fetch_devices(app)

        def done(rows):
            self.rows = rows
            self.set_msg("%d dispositivo(s)" % len(rows))
            self._rerender()

        def err(msg):
            self.set_msg("Error: %s" % msg)

        Worker(work, done, err)

    def set_msg(self, txt):
        self.msg.text = txt

    def _rerender(self):
        q = self.search_ti.text.strip().lower()
        filtered = [r for r in self.rows
                    if not q or q in (r["name"] or "").lower()
                    or q in r["mac"] or q in (r["ip"] or "").lower()]
        render_devices(self.col, filtered,
                       lambda r: (lambda: self._rename(r)),
                       lambda r: (lambda: self._block(r)))
        if self.rows and not filtered:
            self.col.clear_widgets()
            self.col.add_widget(Label(text="Sin coincidencias", color=SUB,
                                      font_name=FONT, size_hint_y=None, height=dp(40)))

    def _rename(self, r):
        app = App.get_running_app()
        ask_rename(app, r["mac"], r["name"] or "",
                   lambda *_a: (self.refresh(), app.main_screen.estado.refresh(
                       silent=True)))

    def _block(self, r):
        app = App.get_running_app()
        block_device(app, r["mac"], r["name"],
                     lambda: (self.refresh(), app.main_screen.estado.refresh(
                         silent=True), app.main_screen.negra.refresh()))


# ---------------------------------------------------------------------------
# Lista negra
# ---------------------------------------------------------------------------
class BlackScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(6))
        self.msg = Label(text="", color=SUB, font_name=FONT, font_size=dp(12),
                         size_hint_y=None, height=dp(22))
        root.add_widget(self.msg)
        sv = ScrollView()
        self.col = BoxLayout(orientation="vertical", spacing=dp(8),
                             size_hint_y=None, padding=[0, dp(2)])
        self.col.bind(minimum_height=self.col.setter("height"))
        sv.add_widget(self.col)
        root.add_widget(sv)
        addrow = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=dp(46), spacing=dp(6))
        self.ti = field("MAC a bloquear (aa:bb:cc:dd:ee:ff)")
        addrow.add_widget(self.ti)
        btn = accented_btn("Añadir", bg=ACCENT, size_hint=(None, 1), width=dp(110),
                           height=dp(46))
        btn.bind(on_release=lambda _b: self.add_manual())
        addrow.add_widget(btn)
        root.add_widget(addrow)
        root.add_widget(accented_btn("Actualizar", self._go))
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
                    n = mac_key(e.get("Mac_Addr", ""))
                    if n:
                        app.blacklist.add(n)
            mode = "lista negra" if flt.get("mode", "0") == "0" else "lista blanca"
            act = "ACTIVO" if flt["enable"] == "1" else "desactivado"
            self.set_msg("Filtro MAC: %s (%s) - %d bloqueados"
                         % (act, mode, len(app.blacklist)))
            self.col.clear_widgets()
            for e in flt["entries"]:
                if e.get("mac_type", "").strip() != "0":
                    continue
                k = mac_key(e.get("Mac_Addr", ""))
                if not k:
                    continue
                n = k.upper()
                row = Card(orientation="horizontal", spacing=dp(8))
                nm = name_of(app, k, "")
                row.add_widget(Label(text="%s\n%s" % (nm or "-", n),
                                     color=TEXT, font_name=FONT, font_size=dp(12),
                                     halign="left", size_hint_y=None,
                                     height=dp(48)))
                q = quiet_btn("Quitar", color=DANGER, size_hint=(None, 1),
                              width=dp(96), font_size=dp(13))
                q.bind(on_release=lambda _b, mm=k: self.unblock(mm))
                row.add_widget(q)
                self.col.add_widget(row)
            if not self.col.children:
                self.col.add_widget(Label(text="Sin bloqueados",
                                          color=SUB, font_name=FONT,
                                          size_hint_y=None, height=dp(40)))

        Worker(work, done)

    def add_manual(self):
        app = App.get_running_app()
        k = mac_key(self.ti.text)
        if not k:
            self.set_msg("MAC no valida")
            return
        self.ti.text = ""

        def act():
            app.api.mac_filter_set_black_on()
            return app.api.mac_filter_add(k, "0")

        def done(resp):
            self.refresh()
            app.main_screen.estado.refresh(silent=True)
            app.main_screen.dispositivos.refresh()

        app.run_net(act, done)

    def unblock(self, k):
        app = App.get_running_app()

        def act():
            return app.api.mac_filter_remove(k)

        def done(resp):
            self.refresh()
            app.main_screen.estado.refresh(silent=True)
            app.main_screen.dispositivos.refresh()

        confirm(app, "Desbloquear", "Quitar %s de la lista negra?" % k.upper(),
                lambda: app.run_net(act, done))


# ---------------------------------------------------------------------------
# WiFi
# ---------------------------------------------------------------------------
class WifiScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.band = True   # True = 2.4G
        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        self.msg = Label(text="", color=SUB, font_name=FONT, font_size=dp(12),
                         size_hint_y=None, height=dp(22))
        root.add_widget(self.msg)
        bands = BoxLayout(orientation="horizontal", size_hint_y=None,
                          height=dp(44), spacing=dp(8))
        self.b24 = NavToggle(text="2.4G", group="band")
        self.b5 = NavToggle(text="5G", group="band")
        bands.add_widget(self.b24)
        bands.add_widget(self.b5)
        self.b24.bind(on_release=self._set_band24)
        self.b5.bind(on_release=self._set_band5)
        root.add_widget(bands)
        self.b24.state = "down"

        self.ssid_ti = field("SSID")
        root.add_widget(self.ssid_ti)
        self.psk_ti = pw_field(root, "Clave (PSK)")
        root.add_widget(accented_btn("Guardar cambios (reinicia la red)", self._go_save))
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
            self.set_msg("Banda activa: %s" % ("2.4G" if self.band else "5G"))
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
            self.set_msg("WiFi %s guardado. La red se cortara unos segundos."
                         % tag)

        confirm(app, "Guardar WiFi",
                "Aplicar cambios WiFi %s?\nLa red se reiniciara unos segundos."
                % tag, lambda: app.run_net(work, done))


# ---------------------------------------------------------------------------
# Mas
# ---------------------------------------------------------------------------
class MoreScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        root = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        root.add_widget(Label(text="Opciones", bold=True, color=TEXT, font_size=dp(16),
                              font_name=FONT, size_hint_y=None, height=dp(34)))
        root.add_widget(accented_btn("Abrir web del router", self._web, bg=(0.20, 0.22, 0.25, 1)))
        root.add_widget(accented_btn("Reiniciar router", self._reboot, bg=DANGER))
        root.add_widget(accented_btn("Cerrar sesion", self._logout, bg=(0.30, 0.33, 0.38, 1)))
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
                "Cerrar la sesion en el router?",
                App.get_running_app().do_logout)


if __name__ == "__main__":
    sys.excepthook = _excepthook
    ExceptionManager.add_handler(ErrorReporter())
    VsolApp().run()