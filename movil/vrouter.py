# -*- coding: utf-8 -*-
"""Capa de red hacia el router VSOL (reutilizada de la app de escritorio).

Solo usa la biblioteca estandar (urllib + http.cookiejar), valida para
Android/Kivy y para PC.
"""

import re
import http.cookiejar
import urllib.request
import urllib.error
import urllib.parse

APP_VERSION = "0.3.0-m"


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

    @staticmethod
    def ua():
        return "VsolAdmin/%s" % APP_VERSION

    def _open(self, url, data=None):
        req = urllib.request.Request(
            url, data=data, headers={"User-Agent": self.ua()}
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
        if "ANOTHER_USER_LOGINED" in body:
            raise LoginError("ANOTHER_USER_LOGINED: el router ya tiene una sesion "
                             "abierta (solo admite una). Cierra la otra app/pagina "
                             "o reinicia el router.")
        if "LOGINED_ERROR" in body:
            idx = body.find("LOGINED_ERROR_")
            tail = body[idx + len("LOGINED_ERROR_"):].strip()
            code = tail.split("=")[0] if "=" in tail else tail
            rest = tail.split("=", 1)[1].strip() if "=" in tail else ""
            raise LoginError("LOGINED_ERROR_%s%s" % (code, ("=%s" % rest) if rest else ""))

    def logout(self):
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
        return [v for k, v in g.items() if k != "__flat__"]

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
        status = parse_groups(self.get("/boaform/getASPdata/rteMacFilterStatus")).get("__flat__", {})
        entries = []
        for k, v in parse_groups(self.get("/boaform/getASPdata/rteMacFilterList")).items():
            if k != "__flat__":
                entries.append(v)
        return {"enable": status.get("EnableMac", "0").strip(),
                "mode": status.get("macFilterMode", "0").strip(),
                "entries": entries}

    def mac_filter_set_black_on(self):
        return self.post_form("formRteMacFilter", {"EnableMac": "1", "macFilterMode": "0"})

    def mac_filter_add(self, mac, mac_type="0"):
        return self.post_form("formRteMacFilter",
                              {"action": "ad", "Mac_Addr": mac, "mac_type": mac_type})

    def mac_filter_remove(self, mac):
        return self.post_form("formRteMacFilter", {"action": "rm", "Mac_Addr": mac})

    def reboot(self):
        return self.post_form("formNewReboot", {})