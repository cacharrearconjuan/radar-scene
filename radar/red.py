"""Peticiones HTTP con la librería estándar (sin dependencias).

`pedir()` devuelve siempre un `Resp`; nunca lanza excepciones de red (status 0 =
no hubo respuesta). En las pruebas se sustituye `FALSO` por un diccionario
url -> Resp o por una función.
"""
import gzip
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
FALSO = None          # pruebas: dict o función(url, cabeceras, datos) -> Resp
LOG = []              # (url, status) de esta ronda, para el modo diagnóstico


@dataclass
class Resp:
    status: int = 0
    cuerpo: bytes = b""
    etag: str = ""
    url: str = ""
    error: str = ""
    cabeceras: dict = field(default_factory=dict)

    @property
    def texto(self):
        return self.cuerpo.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.cuerpo.decode("utf-8", "replace"))

    @property
    def ok(self):
        return 200 <= self.status < 300


def _cabeceras_github(url):
    tok = os.environ.get("GH_TOKEN", "")
    c = {"Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    if tok and url.startswith("https://api.github.com/"):
        c["Authorization"] = "Bearer " + tok
    return c


def pedir(url, etag=None, timeout=15, cabeceras=None, datos=None, metodo=None):
    c = {"User-Agent": UA, "Accept-Encoding": "gzip",
         "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
    if "api.github.com" in url:
        c.update(_cabeceras_github(url))
    if cabeceras:
        c.update(cabeceras)
    if etag:
        c["If-None-Match"] = etag
    if FALSO is not None:
        r = FALSO(url, c, datos) if callable(FALSO) else FALSO.get(url, Resp(404, url=url))
        LOG.append((url, r.status))
        return r
    cuerpo = None
    if datos is not None:
        cuerpo = datos if isinstance(datos, bytes) else json.dumps(datos).encode()
        c.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=cuerpo, headers=c, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            b = f.read()
            if f.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
            r = Resp(f.status, b, f.headers.get("ETag", ""), f.geturl(),
                     cabeceras=dict(f.headers))
    except urllib.error.HTTPError as e:
        b = b""
        try:
            b = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
        except Exception:
            pass
        r = Resp(e.code, b, e.headers.get("ETag", "") if e.headers else "", url,
                 str(e), dict(e.headers or {}))
    except Exception as e:  # DNS, conexión, tiempo agotado...
        r = Resp(0, b"", "", url, f"{type(e).__name__}: {e}")
    LOG.append((url, r.status))
    return r
