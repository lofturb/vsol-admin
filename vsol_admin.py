# -*- coding: utf-8 -*-
"""
VSOL Admin - App de escritorio para administrar un router VSOL V2804AX
(ONT GPON, firmware "boaform"). Solo usa la biblioteca estandar (tkinter + urllib).

Funcionalidades:
  * Estado del dispositivo y señal optica PON
  * Listado de dispositivos conectados (WiFi) con NOMBRES personalizados por MAC
    (los nombres se guardan localmente, el router no almacena hostnames)
  * Ajustes WiFi 2.4G/5G (SSID, clave, canal, ancho, potencia, ocultar)
  * Ajustes de red LAN / DHCP
  * Ver enlaces WAN (solo lectura)
  * Abrir la interfaz web del router y reiniciarlo
"""

import os
import sys
import re
import json
import base64
import threading
import http.cookiejar
import urllib.request
import urllib.error
import urllib.parse
import tkinter as tk
from tkinter import ttk, messagebox

APP_VERSION = "0.2.0"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

SV_TTK = None
try:
    import sv_ttk as SV_TTK
except Exception:
    SV_TTK = None

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "VsolAdmin")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

ACCENT = "#3d7eff"        # azul accent (tambien usado en dark header)
DARK_BG = "#1e1f22"
DARK_HEADER = "#171818"
LIGHT_BG = "#f5f6f8"


def apply_theme(app):
    """Aplica el tema moderno Sun Valley (claro/oscuro) o un estilo plano basico."""
    theme = app.conf.get("theme", "dark")
    app.theme_name = "dark" if theme == "dark" else "light"
    if SV_TTK is not None:
        try:
            SV_TTK.set_theme(app.theme_name, root=app)
            app["bg"] = DARK_BG if app.theme_name == "dark" else LIGHT_BG
            polish_style(app)
            return
        except Exception:
            pass
    fallback_style(app)


def polish_style(app):
    style = ttk.Style(app)
    try:
        style.configure("Treeview", rowheight=30)
    except tk.TclError:
        pass


def fallback_style(app):
    """Estilo minimo moderno si falta el tema empotrado (sin crash)."""
    style = ttk.Style(app)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    try:
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        font=("Segoe UI", 10, "bold"))
    except tk.TclError:
        pass


def setup_fonts():
    fam = "Segoe UI"
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        try:
            f = tk.font.nametofont(name)
            f.configure(family=fam, size=10)
        except Exception:
            pass
    try:
        f = tk.font.nametofont("TkHeadingFont")
        f.configure(family=fam, size=10, weight="bold")
    except Exception:
        pass
    try:
        f = tk.font.nametofont("TkFixedFont")
        f.configure(family="Consolas", size=10)
    except Exception:
        pass


def load_config():
    cfg = {"host": "192.168.1.8", "user": "admin", "password": "", "names": {}}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in cfg})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Parser de respuestas /boaform/getASPdata
# ----------------------------------------------------------------------------
def _norm_mac(m):
    """Normaliza una MAC a 'aa-bb-cc-dd-ee-ff' (minusculas). None si no es valida."""
    h = re.sub(r"[^0-9a-fA-F]", "", m or "")
    if len(h) != 12:
        return None
    return "-".join(h[i:i + 2] for i in range(0, 12, 2)).lower()


def parse_flat(s):
    """'a=1&b=2' -> {'a':'1','b':'2'}"""
    d = {}
    for kv in s.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            d[k] = v
    return d


def parse_groups(s):
    """Combina secciones ('seccion=x=1&y=2') y lineas planas.

    Devuelve {'__flat__': {...}} + {seccion: {...}}.
    Para lineas con corchetes como 'wlan_cli[aa:bb]=...' la clave
    pasa a ser 'wlan_cli[aa:bb]'.
    """
    groups = {}
    flat = {}
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        if "&" in line:
            head, rest = line.split("=", 1)
            groups.setdefault(head, {}).update(parse_flat(rest))
        elif "=" in line:
            k, v = line.split("=", 1)
            flat[k] = v
    if flat:
        groups["__flat__"] = flat
    return groups


# ----------------------------------------------------------------------------
# Cliente HTTP de sesion hacia el router
# ----------------------------------------------------------------------------
class LoginError(Exception):
    pass


class RouterAPI(object):
    def __init__(self, host, user, password):
        host = host.strip().rstrip("/")
        if not host.startswith("http://") and not host.startswith("https://"):
            host = "http://" + host
        self.host = host
        self.user = user
        self.password = password
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )

    @staticmethod
    def _is_login_page(body):
        return ("formLogin" in body) and ("login_form" in body)

    def _open(self, url, data=None):
        req = urllib.request.Request(
            url, data=data, headers={"User-Agent": "VsolAdmin/%s" % APP_VERSION}
        )
        timeout = 40 if data is not None else 12
        try:
            resp = self.opener.open(req, timeout=timeout)
            return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.read().decode("utf-8", errors="replace")

    def login(self):
        """Inicia sesion. Generala LoginError si el servidor bloquea."""
        self._open(self.host + "/")
        body = self.post("/boaform/admin/formLogin",
                         {"username": self.user, "password": self.password},
                         relogin=False)
        if "LOGINED_ERROR" in body:
            idx = body.find("LOGINED_ERROR_")
            tail = body[idx + len("LOGINED_ERROR_"):].strip()
            code = tail.split("=")[0] if "=" in tail else tail
            rest = tail.split("=", 1)[1].strip() if "=" in tail else ""
            raise LoginError("LOGINED_ERROR_%s%s" % (code, ("=%s" % rest) if rest else ""))

    def logout(self):
        """Cierra la sesion actual en el router (libera el acceso para otros)."""
        return self.post("/boaform/admin/formLogout", {}, relogin=False)

    def get(self, path, relogin=True):
        body = self._open(self.host + path)
        if relogin and self._is_login_page(body):
            self.login()
            body = self._open(self.host + path)
        return body

    def post(self, path, fields, relogin=True):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        body = self._open(self.host + path, data)
        if relogin and self._is_login_page(body):
            self.login()
            data = urllib.parse.urlencode(fields).encode("utf-8")
            body = self._open(self.host + path, data)
        return body

    def csrf(self):
        return self.get("/boaform/getASPdata/FMask").strip()

    def post_form(self, endpoint, fields):
        fields = dict(fields)
        fields.setdefault("csrfMask", self.csrf())
        return self.post("/boaform/getASPdata/" + endpoint, fields).strip()

    # ----- lecturas -----------------------------------------------------
    def get_device_info(self):
        return parse_groups(self.get("/boaform/getASPdata/dev_basic_info")).get("basic_info", {})

    def get_resource(self):
        return parse_groups(self.get("/boaform/getASPdata/dev_resource_info")).get("dev_resource_info", {})

    def get_pon(self):
        return parse_groups(self.get("/boaform/getASPdata/ponGetStatus")).get("pon_info", {})

    def get_wifi_clients(self):
        """Lista de clientes WiFi vistos por el AP."""
        out = []
        for line in self.get("/boaform/getASPdata/wirelessClientList").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            if "]" in line:
                rec = parse_flat(line.split("]", 1)[1].lstrip("="))
            else:
                rec = parse_flat(line)
            out.append(rec)
        return out

    def get_client_details(self):
        """Detalle por MAC (host, ip, rssi, rates, online...)."""
        out = {}
        for line in self.get("/boaform/getASPdata/getWlanCliDetailInfo").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = parse_flat(line)
            m = rec.get("mac", "").strip().lower()
            if m:
                out[m] = rec
        return out

    def get_dhcp_clients(self):
        g = parse_groups(self.get("/boaform/getASPdata/E8BDhcpClientList"))
        out = []
        for k, v in g.items():
            if k == "__flat__":
                out.append(v)
            elif "dhcp_cli" in k:
                out.append(v)
        return out

    def get_static_leases(self):
        g = parse_groups(self.get("/boaform/getASPdata/showMACBaseTable"))
        out = []
        for k, v in g.items():
            if k != "__flat__":
                out.append(v)
        return out

    def get_wifi_settings(self, band2g=True):
        tag = "2G" if band2g else "5G"
        data = parse_groups(self.get("/boaform/getASPdata/wlanPageInit_" + tag))
        flat = dict(data.get("__flat__", {}))
        wpa = {}
        for k, v in parse_groups(self.get("/boaform/getASPdata/initWlWpa_data_" + tag)).items():
            if k != "__flat__":
                wpa[k] = v
        return flat, wpa

    def get_lan(self):
        return parse_groups(self.get("/boaform/getASPdata/init_dhcpmain_page")).get("__flat__", {})

    def get_wan_links(self):
        g = parse_groups(self.get("/boaform/getASPdata/initPageEth"))
        return {k: v for k, v in g.items() if k != "__flat__"}

    def get_survey(self):
        g = parse_groups(self.get("/boaform/getASPdata/wlStatus_parm"))
        return [v for k, v in g.items() if k.startswith("wlan_info")]

    # ----- escrituras ----------------------------------------------------
    def save_wifi_basic(self, band2g, fields):
        idx = "0" if band2g else "1"
        base = {"wlan_idx": idx, "Band2G5GSupport": "1" if band2g else "2"}
        base.update(fields)
        return self.post_form("formWlanSetup", base)

    def save_wifi_security(self, band2g, fields):
        base = {"wlan_idx": "0" if band2g else "1"}
        base.update(fields)
        return self.post_form("formWlEncrypt", base)

    def save_lan(self, fields):
        return self.post_form("formDhcpd", fields)

    def add_static_lease(self, mac, ip):
        return self.post_form("formMacAddrBase",
                              {"macAddr_Dhcp_a": mac, "ipAddr_Dhcp_a": ip, "action": "sv"})

    def get_mac_filter(self):
        """Estado del filtro MAC (lista negra/blanca) del router."""
        status = parse_groups(self.get("/boaform/getASPdata/rteMacFilterStatus")).get("__flat__", {})
        entries = []
        for k, v in parse_groups(self.get("/boaform/getASPdata/rteMacFilterList")).items():
            if k != "__flat__":
                entries.append(v)
        return {"enable": status.get("EnableMac", "0").strip(),
                "mode": status.get("macFilterMode", "0").strip(),
                "entries": entries}

    def mac_filter_set_black_on(self):
        """Activa el filtro MAC en modo lista negra."""
        return self.post_form("formRteMacFilter", {"EnableMac": "1", "macFilterMode": "0"})

    def mac_filter_add(self, mac, mac_type="0"):
        return self.post_form("formRteMacFilter",
                              {"action": "ad", "Mac_Addr": mac, "mac_type": mac_type})

    def mac_filter_remove(self, mac):
        return self.post_form("formRteMacFilter", {"action": "rm", "Mac_Addr": mac})

    def reboot(self):
        return self.post_form("formNewReboot", {})


# ----------------------------------------------------------------------------
# Utilidades de UI
# ----------------------------------------------------------------------------
def safe_int(s, default=0):
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


class InfoPanel(ttk.Labelframe):
    """Panel de etiquetas nombre -> valor.

    `rows` es un dict {etiqueta_visible: clave_dato}.
    """

    def __init__(self, parent, title, rows):
        super().__init__(parent, text=title)
        self.rows = rows
        self.vars = {}
        grid = ttk.Frame(self, padding=(8, 4))
        grid.pack(fill="x")
        for i, label in enumerate(rows):
            ttk.Label(grid, text=label, anchor="w").grid(row=i, column=0, sticky="w", padx=(0, 12), pady=1)
            var = tk.StringVar(value="-")
            ttk.Label(grid, textvariable=var, anchor="w").grid(row=i, column=1, sticky="w")
            self.vars[label] = var

    def set(self, data):
        for label, var in self.vars.items():
            key = self.rows[label]
            var.set(str(data.get(key, "-")))


class App(tk.Tk):
    def __init__(self, config):
        super().__init__()
        setup_fonts()
        self.conf = config
        self.api = None
        self.theme_name = "dark"
        self.wifi_vars = {}
        self.wifi_combos = {}
        self.psk_read_only = {}
        self.sec_psk_readonly = {}
        self.black_macs = set()
        apply_theme(self)
        self.title("VSOL Admin %s" % APP_VERSION)
        self.geometry("1020x720")
        self.minsize(860, 600)
        try:
            ico = os.path.join(APP_DIR, "icon.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_header()
        self._build_conn_bar()
        self._build_tabs()
        self.status_var = tk.StringVar(value="Sin conectar. Pulsa 'Conectar'.")
        style = ttk.Style(self)
        style.configure("Status.TLabel", padding=(8, 5))
        ttk.Label(self, textvariable=self.status_var, anchor="w",
                  style="Status.TLabel").pack(side="bottom", fill="x")
        self.log("Listo. Introduce usuario/contraseña y pulsa Conectar.")

    # ---- construccion de la interfaz -----------------------------------
    def _build_header(self):
        bg = DARK_HEADER if self.theme_name == "dark" else "#dfe3ea"
        fg = "#f2f4f8" if self.theme_name == "dark" else "#1d2129"
        sub = "#9aa2b2" if self.theme_name == "dark" else "#4d5563"
        self.header = tk.Frame(self, bg=bg, height=56)
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)
        self.hdr_spacer = tk.Frame(self.header, bg=bg, width=18)
        self.hdr_spacer.pack(side="left", fill="y")
        self.hdr_col = tk.Frame(self.header, bg=bg)
        self.hdr_col.pack(side="left", fill="y")
        self.title_lbl = tk.Label(self.hdr_col, text="VSOL Admin", bg=bg, fg=fg,
                                  font=("Segoe UI", 15, "bold"))
        self.title_lbl.pack(anchor="w")
        self.sub_lbl = tk.Label(self.hdr_col,
                                text="Gestor para router VSOL V2804AX   •   v%s" % APP_VERSION,
                                bg=bg, fg=sub, font=("Segoe UI", 9))
        self.sub_lbl.pack(anchor="w")
        self.hdr_right = tk.Frame(self.header, bg=bg)
        self.hdr_right.pack(side="right", fill="y", padx=10)
        ttk.Button(self.hdr_right, text="Actualizar", style="Accent.TButton",
                   command=self.refresh_all).pack(side="left", padx=3)
        self.theme_btn = ttk.Button(self.hdr_right, text="\u263C Tema", command=self.toggle_theme)
        self.theme_btn.pack(side="left", padx=3)
        ttk.Button(self.hdr_right, text="Abrir web", command=self.open_web).pack(side="left", padx=3)
        ttk.Button(self.hdr_right, text="Reiniciar", command=self.reboot).pack(side="left", padx=3)
        ttk.Button(self.hdr_right, text="Cerrar sesión", command=self.logout).pack(side="left", padx=3)

    def _restyle_header(self):
        bg = DARK_HEADER if self.theme_name == "dark" else "#dfe3ea"
        fg = "#f2f4f8" if self.theme_name == "dark" else "#1d2129"
        sub = "#9aa2b2" if self.theme_name == "dark" else "#4d5563"
        for w in (self.header, self.hdr_spacer, self.hdr_col, self.hdr_right):
            w.configure(bg=bg)
        self.title_lbl.configure(bg=bg, fg=fg)
        self.sub_lbl.configure(bg=bg, fg=sub)
        self.theme_btn.configure(text="\u263E Tema" if self.theme_name == "dark" else "\u263C Tema")

    def toggle_theme(self):
        if SV_TTK is None:
            messagebox.showinfo("VSOL Admin",
                                "El tema moderno no esta disponible (falta la carpeta 'sv_ttk').")
            return
        SV_TTK.toggle_theme(self)
        cur = SV_TTK.get_theme(self)
        self.theme_name = cur
        self.conf["theme"] = cur
        self["bg"] = DARK_BG if cur == "dark" else LIGHT_BG
        self._restyle_header()
        save_config(self.conf)

    def refresh_all(self):
        if self.api is None:
            self.log("Conecta primero.")
            return
        self.refresh_status()
        self.refresh_devices()
        self.load_wifi(True)
        self.load_wifi(False)
        self.load_lan()
        self.load_static_leases()
        self.refresh_wan()
        self.load_blacklist()

    def load_all(self):
        self.refresh_status()
        self.refresh_devices()
        self.load_wifi(True)
        self.load_wifi(False)
        self.load_lan()
        self.load_static_leases()
        self.refresh_wan()
        self.load_blacklist()

    def open_web(self):
        import webbrowser
        webbrowser.open("http://%s" % self.host_var.get().strip())

    def logout(self):
        if self.api is None:
            self.log("No hay sesion activa.")
            return

        def do():
            self.api.logout()
            return None

        def ok(_):
            self.api = None
            self.title("VSOL Admin %s" % APP_VERSION)
            self.log("Sesión cerrada. Ya puedes entrar desde otro dispositivo (host %s)."
                     % self.host_var.get().strip())

        def err(m):
            self.log("Error al cerrar sesion: %s" % m)

        self.run_async(do, ok=ok, err=err)

    def _build_conn_bar(self):
        card = ttk.Frame(self, padding=(12, 8))
        card.pack(fill="x")
        row = ttk.Frame(card)
        row.pack(fill="x")
        ttk.Label(row, text="Host").pack(side="left")
        self.host_var = tk.StringVar(value=self.conf.get("host", "192.168.1.8"))
        ttk.Entry(row, textvariable=self.host_var, width=16).pack(side="left", padx=(6, 14))
        ttk.Label(row, text="Usuario").pack(side="left")
        self.user_var = tk.StringVar(value=self.conf.get("user", "admin"))
        ttk.Entry(row, textvariable=self.user_var, width=10).pack(side="left", padx=(6, 14))
        ttk.Label(row, text="Contraseña").pack(side="left")
        self.pass_var = tk.StringVar(value=self.conf.get("password", ""))
        ttk.Entry(row, textvariable=self.pass_var, show="*", width=14).pack(side="left", padx=(6, 14))
        ttk.Button(row, text="Conectar", style="Accent.TButton",
                   command=self.connect).pack(side="left", padx=(0, 6))

    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=(6, 4))

        self.tab_status = ttk.Frame(nb, padding=8)
        self.tab_devices = ttk.Frame(nb, padding=8)
        self.tab_wifi2 = ttk.Frame(nb, padding=8)
        self.tab_wifi5 = ttk.Frame(nb, padding=8)
        self.tab_lan = ttk.Frame(nb, padding=8)
        self.tab_wan = ttk.Frame(nb, padding=8)

        nb.add(self.tab_status, text="  Estado  ")
        nb.add(self.tab_devices, text="  Dispositivos  ")
        nb.add(self.tab_wifi2, text="  WiFi 2.4G  ")
        nb.add(self.tab_wifi5, text="  WiFi 5G  ")
        nb.add(self.tab_lan, text="  LAN / DHCP  ")
        nb.add(self.tab_wan, text="  WAN  ")

        self._build_status_tab()
        self._build_devices_tab()
        self._build_wifi_tab(self.tab_wifi2, band2g=True)
        self._build_wifi_tab(self.tab_wifi5, band2g=False)
        self._build_lan_tab()
        self._build_wan_tab()

    def _build_status_tab(self):
        f = ttk.Frame(self.tab_status)
        f.pack(fill="both", expand=True)
        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True)

        self.info_dev = InfoPanel(left, "Dispositivo", {
            "Modelo": "devModel", "Firmware": "stVer", "Hardware": "hdVer",
            "SN GPON": "gpon_sn", "MAC": "mac_address", "Uptime": "web_uptime",
            "Memoria": "totalMem",
        })
        self.info_dev.pack(fill="x", pady=(0, 8))

        self.info_res = InfoPanel(left, "Recursos", {"CPU %": "cpUsage", "Mem %": "memUsage"})
        self.info_res.pack(fill="x")

        self.info_pon = InfoPanel(right, "Estado optico (PON)", {
            "Modo": "pon_mode", "Estado": "pon_connect_status", "Alarma": "pon-alarm",
            "Temperatura": "temperature", "Voltaje": "voltage",
            "Tx power": "tx-power", "Rx power": "rx-power",
        })
        self.info_pon.pack(fill="x")

        self.survey_tree = ttk.Treeview(right, columns=("ssid", "chan", "mode", "enc", "bssid"),
                                        show="headings", height=5)
        for col, txt, w in (("ssid", "SSID", 120), ("chan", "Canal", 70),
                            ("mode", "Modo", 130), ("enc", "Cifrado", 90), ("bssid", "BSSID", 150)):
            self.survey_tree.heading(col, text=txt)
            self.survey_tree.column(col, anchor="w", width=w)
        self.survey_tree.pack(fill="x", pady=(8, 0))

        ttk.Button(f, text="Refrescar estado", command=self.refresh_status).pack(anchor="w", pady=(8, 0))

    def _build_devices_tab(self):
        f = ttk.Frame(self.tab_devices)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)

        btns = ttk.Frame(f)
        btns.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(btns, text="Renombrar dispositivo...", style="Accent.TButton",
                   command=self.rename_selected_device).pack(side="left")
        ttk.Button(btns, text="Bloquear", command=self.block_selected_device).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Desbloquear", command=self.unblock_selected_device).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Actualizar", command=self.refresh_devices).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Copiar MAC", command=self.copy_mac).pack(side="left", padx=(8, 0))

        top = ttk.LabelFrame(f, text="Dispositivos conectados", padding=4)
        top.grid(row=1, column=0, sticky="nsew")
        f.rowconfigure(1, weight=1)
        cols = ("name", "mac", "ip", "ssid", "rssi", "rate", "time", "online")
        self.dev_tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        for col, txt, w in (("name", "Nombre", 150), ("mac", "MAC", 140), ("ip", "IP", 100),
                            ("ssid", "Red", 100), ("rssi", "RSSI", 55), ("rate", "Veloc.", 70),
                            ("time", "Conectado (s)", 95), ("online", "Online", 55)):
            self.dev_tree.heading(col, text=txt)
            self.dev_tree.column(col, anchor="w", width=w)
        self.dev_tree.pack(side="left", fill="both", expand=True)
        dev_sb = ttk.Scrollbar(top, orient="vertical", command=self.dev_tree.yview)
        dev_sb.pack(side="right", fill="y")
        self.dev_tree.configure(yscrollcommand=dev_sb.set)
        self.dev_tree.bind("<Double-1>", self.rename_selected_device)
        self.dev_tree.bind("<Configure>", self._on_tree_resize)

        black = ttk.LabelFrame(f, text="Lista negra (filtro MAC del router)", padding=4)
        black.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        f.rowconfigure(2, weight=0)
        fstatus = ttk.Frame(black)
        fstatus.pack(fill="x")
        self.black_status_var = tk.StringVar(value="Lista negra: desconocida (conecta y actualiza).")
        ttk.Label(fstatus, textvariable=self.black_status_var).pack(side="left")
        ttk.Button(fstatus, text="Actualizar filtro", command=self.load_blacklist).pack(side="right")

        bcols = ("name", "mac", "ip")
        self.black_tree = ttk.Treeview(black, columns=bcols, show="headings", selectmode="browse", height=5)
        for col, txt, w in (("name", "Nombre", 150), ("mac", "MAC", 140), ("ip", "IP", 100)):
            self.black_tree.heading(col, text=txt)
            self.black_tree.column(col, anchor="w", width=w)
        self.black_tree.pack(side="left", fill="both", expand=True, pady=(4, 0))
        b_sb = ttk.Scrollbar(black, orient="vertical", command=self.black_tree.yview)
        b_sb.pack(side="right", fill="y", pady=(4, 0))
        self.black_tree.configure(yscrollcommand=b_sb.set)
        self.black_tree.bind("<Double-1>", self.rename_selected_device)
        self.black_tree.bind("<Configure>", self._on_tree_resize)

        tip = ttk.Label(f, foreground="#8a919c", padding=(0, 6), wraplength=900,
                        text="Los nombres se guardan de forma local (este router NO almacena nombres de "
                             "clientes WiFi; el campo 'host' es de solo-lectura). Doble clic para renombrar. "
                             "Los dispositivos bloqueados NO se muestran arriba: aparecen solo en la lista negra "
                             "de abajo. Bloqéalo desde una lista y desbloquéalo seleccionándolo abajo y pulsando "
                             "Desbloquear.")
        tip.grid(row=3, column=0, sticky="ew")

    @staticmethod
    def _on_tree_resize(event):
        try:
            event.widget.update_idletasks()
        except Exception:
            pass

    def _build_wifi_tab(self, tab, band2g):
        tag = "2.4G" if band2g else "5G"
        f = ttk.Frame(tab)
        f.pack(fill="both", expand=True)
        g = ttk.LabelFrame(f, text="Ajustes basicos WiFi %s" % tag, padding=8)
        g.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        g2l = ttk.LabelFrame(f, text="Seguridad WiFi %s" % tag, padding=8)
        g2l.grid(row=0, column=1, sticky="nsew")
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        sv = self.wifi_vars.setdefault(("2G" if band2g else "5G"), {})
        combos = self.wifi_combos.setdefault(("2G" if band2g else "5G"), {})

        row = 0
        sv["enabled"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="Radio habilitada", variable=sv["enabled"]).grid(row=row, column=0,
                                                                                 columnspan=2, sticky="w")
        row += 1
        sv["ssid"] = tk.StringVar()
        self._labeled_entry(g, row, "SSID:", sv["ssid"]); row += 1
        sv["hidden"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="Ocultar SSID", variable=sv["hidden"]).grid(row=row, column=0, sticky="w")
        row += 1
        sv["channel"] = tk.StringVar(value="0")
        ttk.Label(g, text="Canal:").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Combobox(g, textvariable=sv["channel"], width=10, values=self._channel_list(band2g)).grid(
            row=row, column=1, sticky="w", pady=2)
        row += 1
        sv["width"] = tk.StringVar(value="1")
        ttk.Label(g, text="Ancho de banda:").grid(row=row, column=0, sticky="w", pady=2)
        w_values = ["0 (20 MHz)", "1 (20/40 MHz)", "2 (80 MHz)"] if not band2g else ["0 (20 MHz)", "1 (20/40 MHz)"]
        combos["width"] = ttk.Combobox(g, textvariable=sv["width"], width=12, values=w_values)
        combos["width"].grid(row=row, column=1, sticky="w", pady=2)
        row += 1
        sv["power"] = tk.StringVar(value="0")
        ttk.Label(g, text="Potencia TX:").grid(row=row, column=0, sticky="w", pady=2)
        p_values = ["0 (Alta)", "1", "2", "3", "4", "5", "6 (Baja)"]
        combos["power"] = ttk.Combobox(g, textvariable=sv["power"], width=12, values=p_values)
        combos["power"].grid(row=row, column=1, sticky="w", pady=2)
        row += 1

        sv["psk"] = tk.StringVar()
        self.psk_read_only[("2G" if band2g else "5G")] = sv["psk"]
        self._labeled_entry(g, row, "Clave (PSK):", sv["psk"]); row += 1
        sv["psk_format"] = tk.StringVar(value="0")
        ttk.Label(g, text="Formato PSK:").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Combobox(g, textvariable=sv["psk_format"], width=12,
                     values=["0 (Passphrase)", "1 (HEX)"]).grid(row=row, column=1, sticky="w", pady=2)
        row += 1

        ttk.Button(g, text="Guardar ajustes WiFi", style="Accent.TButton",
                   command=lambda: self.save_wifi(band2g)).grid(
            row=row, column=0, columnspan=2, pady=(10, 0))

        srow = 0
        sv["sec_method"] = tk.StringVar(value="4 (WPA2)")
        ttk.Label(g2l, text="Metodo:").grid(row=srow, column=0, sticky="w", pady=2)
        ttk.Combobox(g2l, textvariable=sv["sec_method"], width=16, state="readonly",
                     values=["0 (Abierta)", "2 (WPA)", "4 (WPA2)", "6 (WPA2 Mixto)",
                             "16 (WPA3)", "20 (WPA2/WPA3)"]).grid(row=srow, column=1, sticky="we", pady=2)
        srow += 1
        sv["wpa_auth"] = tk.StringVar(value="2")
        ttk.Label(g2l, text="Autenticacion:").grid(row=srow, column=0, sticky="w", pady=2)
        ttk.Label(g2l, text="2 = Personal (PSK)").grid(row=srow, column=1, sticky="w", pady=2)
        srow += 1
        sv["sec_psk"] = tk.StringVar()
        self._labeled_entry(g2l, srow, "Clave WPA:", sv["sec_psk"]); srow += 1
        self.sec_psk_readonly[("2G" if band2g else "5G")] = sv["sec_psk"]

        sv["psk"].trace_add("write", lambda *_: sv["sec_psk"].set(sv["psk"].get()))
        sv["sec_psk"].trace_add("write", lambda *_: sv["psk"].set(sv["sec_psk"].get()))

        ttk.Button(g2l, text="Guardar seguridad WiFi", style="Accent.TButton",
                   command=lambda: self.save_wifi_security(band2g)).grid(
            row=srow, column=0, columnspan=2, pady=(10, 0))

        ttk.Label(f, foreground="#8a919c", text="  La clave PSK del router ya cargada aparece en el campo "
                   "'Clave (PSK)'. Cambialo y pulsa 'Guardar ajustes WiFi'.",
                   wraplength=700).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    @staticmethod
    def _channel_list(band2g):
        if band2g:
            return ["0 (Auto)", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13"]
        return ["0 (Auto)", "36", "40", "44", "48", "52", "56", "60", "64",
                "100", "104", "108", "112", "116", "120", "124", "128", "132",
                "136", "140", "149", "153", "157", "161", "165"]

    def _labeled_entry(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=26).grid(row=row, column=1, sticky="we", pady=2)

    def _build_lan_tab(self):
        f = ttk.Frame(self.tab_lan)
        f.pack(fill="both", expand=True)
        g = ttk.LabelFrame(f, text="Red LAN / DHCP", padding=10)
        g.pack(fill="x", anchor="n")
        self.lan_vars = {}
        sv = self.lan_vars
        rows = [("IP LAN", "uIp"), ("Mascara", "uMask"),
                ("Inicio rango", "dhcpRangeStart"), ("Fin rango", "dhcpRangeEnd"),
                ("DNS 1", "Ipv4Dns1"), ("DNS 2", "Ipv4Dns2")]
        for i, (label, name) in enumerate(rows):
            sv[name] = tk.StringVar()
            ttk.Label(g, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 6), pady=3)
            ttk.Entry(g, textvariable=sv[name], width=18).grid(row=i // 2, column=(i % 2) * 2 + 1,
                                                               sticky="w", padx=(0, 18), pady=3)

        r = 3
        sv["dhcp_mode"] = tk.StringVar(value="1")
        ttk.Label(g, text="Servidor DHCP:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(g, textvariable=sv["dhcp_mode"], width=14, state="readonly",
                     values=[("1", "Activado"), ("2", "Relay")]).grid(row=r, column=1, sticky="w", pady=3)
        sv["dhcp_lease"] = tk.StringVar(value="86400")
        ttk.Label(g, text="Tiempo de concesion (s):").grid(row=r, column=2, sticky="w", pady=3)
        ttk.Combobox(g, textvariable=sv["dhcp_lease"], width=12, state="readonly",
                     values=["60", "3600", "86400", "604800"]).grid(row=r, column=3, sticky="w", pady=3)
        r = 4
        sv["dns_mode"] = tk.StringVar(value="2")
        ttk.Label(g, text="Modo DNS:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(g, textvariable=sv["dns_mode"], width=14, state="readonly",
                     values=[("0", "Router (Proxy)"), ("1", "Estatica"), ("2", "Del ISP")]).grid(
            row=r, column=1, sticky="w", pady=3)
        r = 5
        sv["uServerIp"] = tk.StringVar(value="0.0.0.0")
        ttk.Label(g, text="Servidor DHCP relay:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(g, textvariable=sv["uServerIp"], width=18).grid(row=r, column=1, sticky="w", pady=3)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Guardar LAN", style="Accent.TButton",
                   command=self.save_lan).pack(side="left")

        g2 = ttk.LabelFrame(f, text="Reserva estatica (MAC -> IP)", padding=10)
        g2.pack(fill="both", expand=True, pady=(8, 0))
        self.static_tree = ttk.Treeview(g2, columns=("mac", "ip"), show="headings", height=5)
        self.static_tree.heading("mac", text="MAC")
        self.static_tree.column("mac", width=180)
        self.static_tree.heading("ip", text="IP reservada")
        self.static_tree.column("ip", width=140)
        self.static_tree.pack(fill="x")
        f2 = ttk.Frame(g2)
        f2.pack(fill="x", pady=(6, 0))
        ttk.Label(f2, text="MAC:").pack(side="left")
        self.lan_mac_entry = ttk.Entry(f2, width=20)
        self.lan_mac_entry.pack(side="left", padx=(0, 10))
        ttk.Label(f2, text="IP:").pack(side="left")
        self.lan_ip_entry = ttk.Entry(f2, width=16)
        self.lan_ip_entry.pack(side="left", padx=(0, 10))
        ttk.Button(f2, text="Agregar reservation", command=self.add_static_lease).pack(side="left")
        ttk.Button(f2, text="Actualizar", command=self.load_static_leases).pack(side="left", padx=(8, 0))

    def _build_wan_tab(self):
        f = ttk.Frame(self.tab_wan)
        f.pack(fill="both", expand=True)
        self.wan_text = tk.Text(f, height=14, wrap="word", state="disabled", font=("Consolas", 10))
        self.wan_text.pack(fill="both", expand=True)
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Actualizar", command=self.refresh_wan).pack(side="left")
        ttk.Label(btns, foreground="#8a919c",
                  text="  Solo lectura. Para editar WAN usa la interfaz web del router.").pack(
            side="left", padx=(10, 0))

    # ---- helpers UI -----------------------------------------------------
    def log(self, msg):
        self.status_var.set(msg)

    def run_async(self, func, ok=None, err=None):
        def worker():
            try:
                result = func()
                if ok:
                    self.after(0, lambda: ok(result))
            except LoginError as e:
                if err:
                    self.after(0, lambda: err(str(e)))
                else:
                    self.after(0, lambda: self.log("Login bloqueado: %s" % e))
            except Exception as e:
                if err:
                    self.after(0, lambda: err(str(e)))
                else:
                    self.after(0, lambda: self.log("Error: %s" % e))
        threading.Thread(target=worker, daemon=True).start()

    def _ensure_api(self):
        if self.api is None:
            raise RuntimeError("No conectado aun.")

    def connect(self):
        host = self.host_var.get().strip()
        user = self.user_var.get().strip()
        password = self.pass_var.get()
        if not host or not user:
            messagebox.showwarning("VSOL Admin", "Introduce host y usuario.")
            return
        self.conf["host"] = host
        self.conf["user"] = user
        self.conf["password"] = password
        save_config(self.conf)
        self.log("Conectando a %s..." % host)
        self.api = RouterAPI(host, user, password)

        def do_login():
            self.api.login()
            return None

        def ok(_):
            self.title("VSOL Admin %s - %s" % (APP_VERSION, host))
            self.log("Conectado a %s. Cargando datos del router..." % host)
            self.load_all()

        def err(m):
            self.log("Fallo de login: %s" % m)

        self.run_async(do_login, ok=ok, err=err)

    def on_close(self):
        self.conf["host"] = self.host_var.get().strip()
        self.conf["user"] = self.user_var.get().strip()
        self.conf["password"] = self.pass_var.get()
        save_config(self.conf)
        self.destroy()

    # ---- Estado -----------------------------------------------------------
    def refresh_status(self):
        self.set_status("Obteniendo datos de estado...")
        try:
            dev = self.api.get_device_info()
            res = self.api.get_resource()
            pon = self.api.get_pon()
            survey = self.api.get_survey()

            def fill():
                self.info_dev.set(dev)
                self.info_res.set(res)
                self.info_pon.set(pon)
                for row in self.survey_tree.get_children():
                    self.survey_tree.delete(row)
                for s in survey:
                    self.survey_tree.insert("", "end", values=(
                        s.get("ssidname", "-"), s.get("channel", "-"),
                        s.get("mode", "-"), s.get("encryptState", "-"),
                        s.get("BSSID", "-")))
                self.log("Estado actualizado.")
            self.after(0, fill)
        except Exception as e:
            self.after(0, lambda: self.log("Error al leer estado: %s" % e))

    def set_status(self, msg):
        self.after(0, lambda: self.log(msg))

    # ---- Dispositivos ------------------------------------------------------
    def refresh_devices(self):
        self.set_status("Obteniendo lista de dispositivos...")

        def fetch():
            wifi = self.api.get_wifi_clients()
            details = self.api.get_client_details()
            dhcp = self.api.get_dhcp_clients()
            return wifi, details, dhcp

        def ok(data):
            wifi, details, dhcp = data
            for row in self.dev_tree.get_children():
                self.dev_tree.delete(row)
            seen = set()
            for c in wifi:
                mac = c.get("mac_addr", "").lower()
                if _norm_mac(mac) in self.black_macs:
                    continue
                seen.add(mac)
                d = details.get(mac, {})
                name = self.conf["names"].get(mac, "") or (d.get("host") or "") or "-"
                self.dev_tree.insert("", "end", values=(
                    name, mac, d.get("ip", c.get("ip", "-")) or "-",
                    c.get("ssidname", d.get("linkSSID", "-")),
                    d.get("rssi", c.get("rssi", "-")),
                    d.get("mcs_tx_rate", c.get("txrate", "-")),
                    d.get("link_time", "-"),
                    "Sí" if d.get("online") == "1" else "No"))
            for c in dhcp:
                mac = c.get("macAddr", "").lower()
                if mac in seen or _norm_mac(mac) in self.black_macs:
                    continue
                seen.add(mac)
                name = self.conf["names"].get(mac, "") or c.get("nickname", "-")
                self.dev_tree.insert("", "end", values=(
                    name, mac, c.get("ipAddr", "-"), "-", "-", "-",
                    c.get("liveTime", "-"), "Sí"))
            self.log("Dispositivos actualizados (%d)." % len(self.dev_tree.get_children()))

        self.run_async(fetch, ok=ok)

    def load_blacklist(self, then=None):
        self.set_status("Cargando lista negra (filtro MAC)...")
        state = {"ok": False}

        def fetch():
            flt = self.api.get_mac_filter()
            details = self.api.get_client_details()
            dhcp = self.api.get_dhcp_clients()
            return flt, details, dhcp

        def ok(data):
            state["ok"] = True
            flt, details, dhcp = data
            self.black_macs = {_norm_mac(e.get("Mac_Addr", ""))
                               for e in flt["entries"]
                               if e.get("mac_type", "").strip() == "0"}
            self.black_macs.discard(None)

            ips = {}
            nicks = {}
            for mac, d in details.items():
                ips.setdefault(mac, (d.get("ip") or "-"))
            for c in dhcp:
                m = c.get("macAddr", "").lower()
                ips.setdefault(m, c.get("ipAddr") or "-")
                nn = c.get("nickname")
                if nn:
                    nicks.setdefault(m, nn)

            for row in self.black_tree.get_children():
                self.black_tree.delete(row)
            for e in flt["entries"]:
                if e.get("mac_type", "").strip() != "0":
                    continue
                m = _norm_mac(e.get("Mac_Addr", ""))
                if not m:
                    continue
                ml = m.lower()
                d = details.get(ml, {})
                name = self.conf["names"].get(ml, "") or (d.get("host") or "") or nicks.get(ml, "") or "-"
                self.black_tree.insert("", "end", values=(name, m, ips.get(ml, "-")))

            cmap = {c: i for i, c in enumerate(self.dev_tree["columns"])}
            for row in self.dev_tree.get_children():
                vals = self.dev_tree.item(row, "values")
                if len(vals) > cmap["mac"] and _norm_mac(str(vals[cmap["mac"]])) in self.black_macs:
                    self.dev_tree.delete(row)

            mode_txt = "lista negra" if flt.get("mode", "0") == "0" else "lista blanca"
            self.black_status_var.set(
                "Filtro MAC: %s (%s)  |  lista negra con %d dispositivo(s) (doble clic para renombrar, "
                "Desbloquear para quitarlo)"
                % ("ACTIVO" if flt["enable"] == "1" else "desactivado",
                   mode_txt, len(self.black_macs)))
            self.log("Lista negra cargada (%d dispositivo(s), filtro %s, %s)."
                     % (len(self.black_macs),
                        flt["enable"] == "1" and "activo" or "inactivo", mode_txt))
            if then:
                then()

        def err(msg):
            state["ok"] = False
            self.set_status("Error al cargar la lista negra: %s" % msg)
            self.log("Error al cargar la lista negra: %s" % msg)
            if then:
                then()

        self.run_async(fetch, ok=ok, err=err)

    def block_selected_device(self):
        dev = self.selected_device()
        if not dev:
            messagebox.showinfo("VSOL Admin", "Selecciona un dispositivo de la lista.")
            return
        name, mac = dev
        norm = _norm_mac(mac)
        if not norm:
            messagebox.showwarning("VSOL Admin", "No se pudo interpretar la MAC: %s" % mac)
            return
        if norm in self.black_macs:
            messagebox.showinfo("VSOL Admin", "El dispositivo %s ya esta en la lista negra." % name)
            return
        if not messagebox.askyesno(
                "VSOL Admin - Bloquear",
                "Bloquear %s (%s)?\n\nSe activara el filtro MAC en modo LISTA NEGRA y se "
                "agregara su MAC. Con lista negra activa, SOLO quedan bloqueados los "
                "dispositivos de la lista." % (name, norm.upper())):
            return

        def fetch():
            self.api.mac_filter_set_black_on()
            return self.api.mac_filter_add(norm, "0")

        def ok(resp):
            self.log("Dispositivo bloqueado (%s)." % resp)
            if name and name != "-":
                self.conf["names"].setdefault(norm.lower(), name)
                save_config(self.conf)
            self.load_blacklist(then=self.refresh_devices)

        self.run_async(fetch, ok=ok)

    def unblock_selected_device(self):
        dev = self.selected_device()
        if not dev:
            messagebox.showinfo("VSOL Admin", "Selecciona un dispositivo de la lista.")
            return
        name, mac = dev
        norm = _norm_mac(mac)
        if not norm:
            messagebox.showwarning("VSOL Admin", "No se pudo interpretar la MAC: %s" % mac)
            return
        if norm not in self.black_macs:
            messagebox.showinfo("VSOL Admin", "El dispositivo %s no esta en la lista negra." % name)
            return
        if not messagebox.askyesno("VSOL Admin - Desbloquear",
                                   "Quitar %s (%s) de la lista negra?" % (name, norm.upper())):
            return

        def fetch():
            return self.api.mac_filter_remove(norm)

        def ok(resp):
            self.log("Dispositivo desbloqueado (%s)." % resp)
            self.load_blacklist(then=self.refresh_devices)

        self.run_async(fetch, ok=ok)

    def selected_device(self):
        tree = self.dev_tree if self.dev_tree.selection() else self.black_tree
        sel = tree.selection()
        if not sel:
            return None
        vals = tree.item(sel[0], "values")
        if len(vals) < 2:
            return None
        return vals[0], vals[1]

    def rename_selected_device(self, _event=None):
        dev = self.selected_device()
        if not dev:
            messagebox.showinfo("VSOL Admin", "Selecciona un dispositivo de la lista.")
            return
        name_now, mac = dev
        dialog = tk.Toplevel(self)
        dialog.title("Renombrar dispositivo")
        dialog.resizable(False, False)
        ttk.Label(dialog, text="Nombre para MAC %s:" % mac).pack(padx=10, pady=(10, 4))
        entry = ttk.Entry(dialog, width=32)
        entry.insert(0, name_now if name_now != "-" else "")
        entry.pack(padx=10)
        entry.focus_set()
        result = {}

        def ok():
            result["v"] = entry.get().strip()
            dialog.destroy()

        bf = ttk.Frame(dialog)
        bf.pack(pady=(8, 10))
        ttk.Button(bf, text="Guardar", command=ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Quitar nombre", command=lambda: (result.update(v=""), dialog.destroy())).pack(
            side="left", padx=4)
        ttk.Button(bf, text="Cancelar", command=dialog.destroy).pack(side="left", padx=4)
        self.wait_window(dialog)
        if "v" not in result:
            return
        if result["v"]:
            self.conf["names"][mac] = result["v"]
        else:
            self.conf["names"].pop(mac, None)
        save_config(self.conf)
        self.refresh_devices()

    def copy_mac(self):
        dev = self.selected_device()
        if not dev:
            messagebox.showinfo("VSOL Admin", "Selecciona un dispositivo primero.")
            return
        self.clipboard_clear()
        self.clipboard_append(dev[1])

    # ---- WiFi --------------------------------------------------------------
    def load_wifi(self, band2g):
        tag = "2G" if band2g else "5G"
        self.set_status("Cargando WiFi %s..." % tag)

        def fetch():
            return self.api.get_wifi_settings(band2g)

        def ok(data):
            flat, wpa = data
            sv = self.wifi_vars[tag]
            ssid = flat.get("ssid", "")
            sv["ssid"].set(ssid)
            sv["enabled"].set(str(flat.get("wlanDisabled", "0")).strip() == "0")
            sv["hidden"].set(str(flat.get("wlHide", "0")).strip() == "1")
            sv["channel"].set(str(flat.get("chan", "0")).strip())
            combos = self.wifi_combos[tag]
            for v in combos["width"].cget("values"):
                if v.startswith(str(flat.get("chanwid", "1")).strip()):
                    sv["width"].set(v)
                    break
            for v in combos["power"].cget("values"):
                if v.startswith(str(flat.get("txpower", "0")).strip()):
                    sv["power"].set(v)
                    break
            sec = {}
            for k, v in wpa.items():
                sec = v
                break
            psk = sec.get("pskValue", flat.get("pskValue", ""))
            sv["psk"].set(psk)
            sv["psk_format"].set(str(sec.get("pskFormat", flat.get("pskFormat", "0"))).strip())
            methods = [str(i) for i in (0, 2, 4, 6, 16, 20)]
            m = str(sec.get("security_method", "4")).strip()
            sv["sec_method"].set(m + " " + self._method_label(m))
            sv["wpa_auth"].set(str(sec.get("wpaAuth", "2")).strip())
            sv["sec_psk"].set(psk)
            self.log("WiFi %s cargado (SSID %s)." % (tag, ssid))
        self.run_async(fetch, ok=ok)

    @staticmethod
    def _method_label(m):
        d = {"0": "(Abierta)", "2": "(WPA)", "4": "(WPA2)", "6": "(WPA2 Mixto)",
             "16": "(WPA3)", "20": "(WPA2/WPA3)"}
        return d.get(m, "")

    def save_wifi(self, band2g):
        tag = "2G" if band2g else "5G"
        if not messagebox.askyesno("VSOL Admin",
                                   "Se aplicaran los nuevos ajustes WiFi %s. La red se cortara unos segundos. Continuar?" % tag):
            return
        sv = self.wifi_vars[tag]

        def fetch():
            cur, wpa = self.api.get_wifi_settings(band2g)
            fields = {
                "wlanDisabled": "0" if sv["enabled"].get() else "1",
                "band": cur.get("band", "0"),
                "mode": cur.get("mode", "0"),
                "ssid": sv["ssid"].get(),
                "wlHide": "1" if sv["hidden"].get() else "0",
                "wl_access": cur.get("wl_access", "0"),
                "wl_wmm_func": cur.get("wl_wmm_func", "1"),
                "chanwid": sv["width"].get().split(" ")[0],
                "ctlband": "0",
                "chan": sv["channel"].get().split(" ")[0],
                "txpower": sv["power"].get().split(" ")[0],
                "regdomain_demo": cur.get("regdomain_demo", "14"),
                "wl_stanum": cur.get("wl_stanum", "0"),
                "basicrates": cur.get("basicrates", ""),
                "operrates": cur.get("operrates", ""),
                "WiFiTest": "0",
            }
            resp = self.api.save_wifi_basic(band2g, fields)
            resp2 = self.api.save_wifi_security(band2g, self._wifi_sec_fields(band2g))
            return resp, resp2

        def ok(res):
            self.log("WiFi %s ajustes guardados (%s | %s). Se aplica..." % (tag, res[0], res[1]))

        self.run_async(fetch, ok=ok)

    def _wifi_sec_fields(self, band2g):
        """Campos del formulario de seguridad (formWlEncrypt) con la clave actual.

        Important: el router espera 'wpaSSID' = EL INDICE de la entrada WPA
        (p.ej. '0'), NO el nombre del SSID.
        """
        tag = "2G" if band2g else "5G"
        sv = self.wifi_vars[tag]
        cur, wpa = self.api.get_wifi_settings(band2g)
        sec = {}
        for k, v in wpa.items():
            sec = v
            break
        return {
            "wpaSSID": sec.get("index", "0"),
            "security_method": sv["sec_method"].get().split(" ")[0],
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
            "pskFormat": sv["psk_format"].get().split(" ")[0],
            "pskValue": sv["sec_psk"].get(),
            "gk_rekey": sec.get("gk_rekey", "86400"),
            "radiusIP": sec.get("radiusIP", "0.0.0.0"),
            "radiusPort": sec.get("radiusPort", "1812"),
            "radiusPass": sec.get("radiusPass", ""),
            "wepKeyLen": sec.get("wepKeyLen", "0"),
        }

    def save_wifi_security(self, band2g):
        tag = "2G" if band2g else "5G"
        if not messagebox.askyesno("VSOL Admin",
                                   "Aplicar cambios de seguridad WiFi %s (clave/metodo)? La red se reiniciara unos segundos." % tag):
            return

        def fetch():
            fields = self._wifi_sec_fields(band2g)
            resp = self.api.save_wifi_security(band2g, fields)
            return resp

        def ok(resp):
            self.log("Seguridad WiFi %s guardada (%s)." % (tag, resp))

        self.run_async(fetch, ok=ok)

    # ---- LAN ---------------------------------------------------------------
    def load_lan(self):
        self.set_status("Cargando LAN...")

        def fetch():
            return self.api.get_lan()

        def ok(data):
            sv = self.lan_vars
            sv["uIp"].set(data.get("uIp", ""))
            sv["uMask"].set(data.get("uMask", ""))
            sv["dhcpRangeStart"].set(data.get("dhcpRangeStart", ""))
            sv["dhcpRangeEnd"].set(data.get("dhcpRangeEnd", ""))
            sv["Ipv4Dns1"].set(data.get("Ipv4Dns1", ""))
            sv["Ipv4Dns2"].set(data.get("Ipv4Dns2", ""))
            sv["dhcp_mode"].set(str(data.get("uDhcpType", "1")).strip())
            sv["dhcp_lease"].set(str(data.get("ulTime", "86400")).strip())
            sv["dns_mode"].set(str(data.get("ipv4landnsmode", "2")).strip())
            sv["uServerIp"].set(data.get("uServerIp", "0.0.0.0"))
            self.log("LAN cargada (IP %s)." % data.get("uIp", "-"))

        self.run_async(fetch, ok=ok)

    def save_lan(self):
        if not messagebox.askyesno("VSOL Admin",
                                   "Aplicar cambios de red LAN/DHCP?"):
            return
        sv = self.lan_vars

        def fetch():
            fields = {
                "uIp": sv["uIp"].get(),
                "uMask": sv["uMask"].get(),
                "uDhcpType": sv["dhcp_mode"].get(),
                "dhcpRangeStart": sv["dhcpRangeStart"].get(),
                "dhcpRangeEnd": sv["dhcpRangeEnd"].get(),
                "ulTime": sv["dhcp_lease"].get(),
                "ipv4landnsmode": sv["dns_mode"].get(),
                "Ipv4Dns1": sv["Ipv4Dns1"].get(),
                "Ipv4Dns2": sv["Ipv4Dns2"].get(),
                "uServerIp": sv["uServerIp"].get() or "0.0.0.0",
                "v4_dhcp_relay": "1" if sv["dhcp_mode"].get() == "2" else "0",
            }
            return self.api.save_lan(fields)

        def ok(resp):
            self.log("LAN guardada (%s)." % resp)

        self.run_async(fetch, ok=ok)

    def load_static_leases(self):
        self.set_status("Cargando reservas estaticas...")

        def fetch():
            return self.api.get_static_leases()

        def ok(leases):
            for row in self.static_tree.get_children():
                self.static_tree.delete(row)
            for l in leases:
                self.static_tree.insert("", "end", values=(l.get("mac", "-"), l.get("ip", "-")))
            self.log("Reservas estaticas cargadas (%d)." % len(leases))

        self.run_async(fetch, ok=ok)

    def add_static_lease(self):
        mac = self.lan_mac_entry.get().strip()
        ip = self.lan_ip_entry.get().strip()
        if not mac or not ip:
            messagebox.showinfo("VSOL Admin", "Indica MAC e IP.")
            return
        if not messagebox.askyesno("VSOL Admin",
                                   "Agregar reserva estatica:\nMAC %s\nIP %s" % (mac, ip)):
            return

        def fetch():
            return self.api.add_static_lease(mac, ip)

        def ok(resp):
            self.log("Reserva agregada (%s)." % resp)
            self.load_static_leases()

        self.run_async(fetch, ok=ok)

    # ---- WAN ---------------------------------------------------------------
    def refresh_wan(self):
        self.set_status("Cargando enlaces WAN...")

        def fetch():
            return self.api.get_wan_links()

        def ok(links):
            lines = []
            if not links:
                lines.append("(sin enlaces / el router no gestiona WAN)")
            for name, rec in sorted(links.items()):
                lines.append("== %s ==" % name)
                for k, v in rec.items():
                    lines.append("   %s = %s" % (k, v))
            self.wan_text.configure(state="normal")
            self.wan_text.delete("1.0", "end")
            self.wan_text.insert("1.0", "\n".join(lines))
            self.wan_text.configure(state="disabled")
            self.log("WAN cargada (%d enlace/s)." % len(links))

        self.run_async(fetch, ok=ok)

    # ---- Reinicio ----------------------------------------------------------
    def reboot(self):
        if not self.api:
            messagebox.showinfo("VSOL Admin", "Conecta primero.")
            return
        if not messagebox.askyesno("VSOL Admin",
                                   "ADVERTENCIA: reiniciar el router ahora? La red se cortara ~90 segundos."):
            return

        def fetch():
            return self.api.reboot()

        def ok(resp):
            self.log("Reinicio enviado (%s). Espera ~90 s y vuelve a conectar." % resp)

        self.run_async(fetch, ok=ok)


def main():
    config = load_config()
    app = App(config)
    menu = tk.Menu(app)
    app.configure(menu=menu)
    menum = tk.Menu(menu, tearoff=0)
    menu.add_cascade(label="Acciones", menu=menum)
    menum.add_command(label="Actualizar todo", command=app.refresh_all)
    menum.add_command(label="Reiniciar router...", command=app.reboot)
    menum.add_separator()
    menum.add_command(label="Salir", command=app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()