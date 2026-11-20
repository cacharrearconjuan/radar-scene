"""¿Lo ha sacado ya la competencia en YouTube?"""
import re
import time
import xml.etree.ElementTree as ET

from . import red
from .estado import salud
from .fuentes import iso_ts

COOKIE = {"Cookie": "SOCS=CAI; CONSENT=YES+cb"}
RE_ID = re.compile(r'(?:"externalId"|"channelId")\s*:\s*"(UC[\w-]{22})"|/channel/(UC[\w-]{22})')
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def resolver(canal, e):
    if canal.get("channel_id"):
        return canal["channel_id"]
    cache = e["yt"]["canales"]
    h = canal["handle"]
    if cache.get(h):
        return cache[h]
    r = red.pedir("https://www.youtube.com/" + h, cabeceras=COOKIE, timeout=20)
    m = RE_ID.search(r.texto) if r.ok else None
    if not m:
        salud(e, "youtube", False, f"no resuelvo {h}: {r.status}")
        return None
    cid = m.group(1) or m.group(2)
    cache[h] = cid
    return cid


def refrescar(cfg, e):
    vistos = {v["id"] for v in e["yt"]["videos"]}
    ok = False
    for canal in cfg["youtube"]:
        cid = resolver(canal, e)
        if not cid:
            continue
        r = red.pedir(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", timeout=20)
        if not r.ok:
            salud(e, "youtube", False, f"{canal['nombre']} RSS {r.status}")
            continue
        try:
            raiz = ET.fromstring(r.cuerpo)
        except ET.ParseError:
            continue
        ok = True
        for en in raiz.findall("a:entry", NS):
            vid = en.findtext("yt:videoId", namespaces=NS) or ""
            if not vid or vid in vistos:
                continue
            vistos.add(vid)
            e["yt"]["videos"].append({
                "id": vid, "canal": canal["nombre"],
                "titulo": (en.findtext("a:title", namespaces=NS) or "")[:200],
                "ts": iso_ts(en.findtext("a:published", namespaces=NS)),
            })
    if ok:
        salud(e, "youtube", True)
    e["yt"]["ok"] = ok
    return ok


def _terminos(n):
    nombres = list(n.get("alias") or [])
    repo = (n.get("repo") or "").split(":", 1)[-1]
    if "/" in repo:
        nombres.append(repo.split("/")[1])
    nombres = [x for x in nombres if len(x) >= 4]
    version = ""
    m = re.search(r"\d+\.\d+[\w.]*", n.get("tag") or "")
    if m:
        version = m.group(0)
    if n["tipo"] == "firmware_sony":
        m = re.search(r"\d+\.\d{2}", n.get("titulo") or "")
        nombres = [m.group(0)] if m else []      # «14.30»
    return nombres, version


def linea(n, e):
    if not e["yt"].get("ok") and not e["yt"]["videos"]:
        return "sin datos"
    nombres, version = _terminos(n)
    if not nombres:
        return "nadie aún"
    desde = (n.get("ts") or time.time()) - 3600
    seguro, posible = [], []
    for v in e["yt"]["videos"]:
        if v["ts"] < desde:
            continue
        t = v["titulo"].lower()
        if any(x.lower() in t for x in nombres):
            hace = max(0, int((time.time() - v["ts"]) / 3600))
            txt = f"{v['canal']} ({'hace ' + str(hace) + ' h' if hace else 'hace menos de 1 h'})"
            (seguro if version and version.lower() in t else posible).append(txt)
    if seguro:
        return "ya lo sacó " + ", ".join(dict.fromkeys(seguro))
    if posible:
        return "posible: " + ", ".join(dict.fromkeys(posible))
    return "nadie aún"
