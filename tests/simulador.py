"""Red falsa para las pruebas: GitHub, listas, Sony, wololo, YouTube, super-ia y Telegram."""
import json
import os
import re
import time
from datetime import datetime, timezone

from radar import red

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def leer(nombre, modo="r"):
    with open(os.path.join(FIX, nombre), modo) as f:
        return f.read()


def iso(hace_h):
    return datetime.fromtimestamp(time.time() - hace_h * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Telegram:
    """Grupo de Telegram con la semántica real de sendMessage / editMessageReplyMarkup."""

    def __init__(self):
        self.mensajes = {}           # id -> {"texto", "markup", "de_bot"}
        self.siguiente = 100
        self.fallos = []             # cola de comportamientos: "timeout_llega", "timeout_no", "429", "conexion"

    def otro_usuario_escribe(self):
        self.mensajes[self.siguiente] = {"texto": "hola", "markup": None, "de_bot": False}
        self.siguiente += 1

    def manejar(self, metodo, datos):
        if metodo == "getMe":
            return red.Resp(200, json.dumps({"ok": True, "result": {"username": "radar_test_bot"}}).encode())
        if metodo == "sendMessage":
            modo = self.fallos.pop(0) if self.fallos else "ok"
            if modo == "conexion":
                return red.Resp(0, error="URLError: <urlopen error [Errno 111] Connection refused>")
            if modo == "429":
                return red.Resp(429, json.dumps({"ok": False, "description": "Too Many Requests",
                                                 "parameters": {"retry_after": 0}}).encode())
            mid = self.siguiente
            self.siguiente += 1
            if modo == "timeout_no":
                return red.Resp(0, error="TimeoutError: The read operation timed out")
            self.mensajes[mid] = {"texto": datos["text"], "markup": datos.get("reply_markup"), "de_bot": True}
            if modo == "timeout_llega":
                return red.Resp(0, error="TimeoutError: The read operation timed out")
            return red.Resp(200, json.dumps({"ok": True, "result": {"message_id": mid}}).encode())
        if metodo == "editMessageReplyMarkup":
            m = self.mensajes.get(datos["message_id"])
            if not m:
                return red.Resp(400, json.dumps({"ok": False, "description":
                                                 "Bad Request: message to edit not found"}).encode())
            if not m["de_bot"]:
                return red.Resp(400, json.dumps({"ok": False, "description":
                                                 "Bad Request: message can't be edited"}).encode())
            if m["markup"] == datos.get("reply_markup"):
                return red.Resp(400, json.dumps({"ok": False, "description":
                                                 "Bad Request: message is not modified"}).encode())
            m["markup"] = datos.get("reply_markup")
            return red.Resp(200, json.dumps({"ok": True, "result": True}).encode())
        return red.Resp(404)

    @property
    def del_bot(self):
        return [m for m in self.mensajes.values() if m["de_bot"]]


class Mundo:
    """Estado de internet en cada momento de la prueba."""

    def __init__(self):
        self.gh = json.loads(leer("github.json"))
        self.releases = {}           # repo -> lista
        self.commits = {}
        self.readme = {}
        self.search = {"items": []}
        self.sony = leer("sony_es.html")
        self.wololo = leer("wololo_feed.xml").replace("</channel>", "").split("<item>")[0] + "</channel></rss>"
        self.yt = {}
        self.ia = "caida"            # caida | ok | dice_no
        self.ia_llamadas = 0
        self.tg = Telegram()
        self.peticiones = []
        self.etags = {}
        self.renombres = {"LightningMods/etaHEN": "etaHEN/etaHEN"}
        self.listas_extra = []       # entradas que se añaden a la lista oficial
        self.contenidos = {}         # repo -> lista de archivos de la raíz

    def __call__(self, url, cab, datos):
        self.peticiones.append(url)
        if "api.telegram.org" in url:
            metodo = url.rsplit("/", 1)[1]
            return self.tg.manejar(metodo, datos)
        if "chat/completions" in url:
            self.ia_llamadas += 1
            if self.ia == "caida":
                return red.Resp(0, error="URLError: timed out")
            contenido = datos["messages"][1]["content"]
            disp = self.ia != "dice_no"
            self.ultimo_prompt = contenido
            self.ultimo_modelo = datos.get("model")
            out = {"resumen": "Resumen de prueba en castellano.", "nota": 9, "disponible": disp,
                   "para_que_sirve": "Sirve para cargar homebrew <b>sin PC</b>.",
                   "idea_video": {"titulo": "etaHEN 3.0: así se instala", "gancho": "Ya está aquí."},
                   "pasos": ["1. Abre Payload Manager", "2) Carga etaHEN.bin https://x.y", "Espera la notificación"],
                   "firmwares": "", "es_juego": False}
            return red.Resp(200, json.dumps({"choices": [{"message": {
                "content": "Claro: " + json.dumps(out)}}]}).encode())
        m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/(releases|commits|readme|contents)", url)
        if m:
            repo, que = m.groups()
            datos = {"releases": self.releases, "commits": self.commits, "readme": self.readme}.get(que)
            if que == "contents":
                arch = self.contenidos.get(repo, ["README.md", "exploit.js"])
                return red.Resp(200, json.dumps([{"name": a} for a in arch]).encode())
            if que == "readme":
                if repo not in self.readme:
                    return red.Resp(404)
                return self._etag(url, cab, self.readme[repo].encode())
            if repo not in datos:
                return self._etag(url, cab, b"[]")
            return self._etag(url, cab, json.dumps(datos[repo]).encode())
        if "api.github.com/search/repositories" in url:
            return red.Resp(200, json.dumps(self.search).encode())
        if "api.github.com/rate_limit" in url:
            return red.Resp(200, json.dumps({"resources": {"core": {"remaining": 990, "limit": 1000}}}).encode())
        m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/?]+)$", url)
        if m:
            nombre = self.renombres.get(m.group(1), m.group(1))
            return red.Resp(200, json.dumps({"full_name": nombre}).encode())
        if url.endswith("payloads_v2.json"):
            d = json.loads(leer("payloads_v2.json"))
            d["payloads"] += self.listas_extra
            return self._etag(url, cab, json.dumps(d).encode())
        if url.endswith("mis-payloads-ps5/payloads.json"):
            return self._etag(url, cab, leer("payloads_propio.json", "rb"))
        if "playstation.com" in url:
            return red.Resp(200, self.sony.encode())
        if "wololo.net" in url:
            return red.Resp(200, self.wololo.encode())
        if url.startswith("https://www.youtube.com/@"):
            return red.Resp(200, leer("youtube_channel_page.html", "rb"))
        if "youtube.com/feeds" in url:
            return red.Resp(200, self.yt.get("feed", b"<feed xmlns='http://www.w3.org/2005/Atom'/>"))
        return red.Resp(404, url=url)

    def _etag(self, url, cab, cuerpo):
        import hashlib
        et = '"' + hashlib.md5(cuerpo).hexdigest() + '"'
        if cab.get("If-None-Match") == et:
            return red.Resp(304, etag=et)
        return red.Resp(200, cuerpo, etag=et, url=url)
