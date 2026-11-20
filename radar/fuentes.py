"""Lectura de fuentes. Cada función devuelve una lista de novedades (dicts).

Ninguna avisa de nada en la primera lectura de una fuente («siembra»): solo
apunta lo que ya existe para no volcar el histórico.
"""
import base64
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

from . import firmware, red
from .estado import salud

API = "https://api.github.com"


# ----------------------------------------------------------------- utilidades
def iso_ts(s):
    if not s:
        return 0
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def rss_ts(s):
    from email.utils import parsedate_to_datetime
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        return iso_ts(s)


RE_FUENTE = re.compile(r"https?://(github\.com|git\.etawen\.dev|[\w.-]+)/([\w.-]+)/([\w.-]+)")


def clave_de_url(url):
    """'https://github.com/a/b/releases' -> 'github.com:a/b'."""
    m = RE_FUENTE.match(url or "")
    if not m:
        return None
    host, o, r = m.groups()
    r = re.sub(r"\.git$", "", r)
    if host.endswith("github.io") or host in ("www.youtube.com", "youtube.com"):
        return None
    return f"{host.lower()}:{o}/{r}"


def clave_repo(nombre):
    if nombre.startswith("http"):
        return clave_de_url(nombre)
    return "github.com:" + nombre


def norm(clave):
    return (clave or "").lower()


# ------------------------------------------------------------ fuentes.txt
def leer_config(ruta="fuentes.txt"):
    cfg = {"repos": {}, "listas": [], "excluir": set(), "youtube": [], "prensa": []}
    seccion = None
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.split("#", 1)[0].strip() if not linea.strip().startswith("http") \
                else re.sub(r"\s+#.*$", "", linea).strip()
            if not linea:
                continue
            m = re.fullmatch(r"\[(\w+)\]", linea)
            if m:
                seccion = m.group(1).lower()
                continue
            partes = linea.split()
            if seccion in ("a", "b"):
                clave = clave_repo(partes[0])
                if not clave:
                    continue
                opts, alias = set(), []
                resto = " ".join(partes[1:])
                ma = re.search(r"alias=(.+)$", resto)
                if ma:
                    alias = [a.strip() for a in ma.group(1).split(",") if a.strip()]
                    resto = resto[:ma.start()]
                opts = set(resto.split())
                cfg["repos"][clave] = {"tier": seccion.upper(), "commits": "commits" in opts,
                                       "readme": "readme" in opts, "nucleo": "nucleo" in opts,
                                       "alias": alias, "nombre": clave.split(":", 1)[1]}
            elif seccion == "listas":
                cfg["listas"].append(partes[0])
            elif seccion == "excluir":
                c = clave_repo(partes[0])
                if c:
                    cfg["excluir"].add(norm(c))
            elif seccion == "youtube":
                d = {"handle": partes[0]}
                resto = " ".join(partes[1:])
                for k in ("nombre", "channel_id"):
                    mm = re.search(k + r"=(.+?)(?=\s+\w+=|$)", resto)
                    if mm:
                        d[k] = mm.group(1).strip()
                d.setdefault("nombre", partes[0].lstrip("@"))
                cfg["youtube"].append(d)
            elif seccion == "prensa":
                cfg["prensa"].append(partes[0])
    return cfg


# ------------------------------------------------------------ releases
def _url_api(clave, que):
    host, nombre = clave.split(":", 1)
    if host == "github.com":
        base = f"{API}/repos/{nombre}"
        return {"releases": base + "/releases?per_page=6",
                "commits": base + "/commits?per_page=10",
                "readme": base + "/readme",
                "raiz": base + "/contents"}[que]
    base = f"https://{host}/api/v1/repos/{nombre}"
    return {"releases": base + "/releases?limit=6",
            "commits": base + "/commits?limit=10",
            "readme": base + "/raw/README.md",
            "raiz": base + "/contents"}[que]


def _canonico(clave, html_url):
    c = clave_de_url(html_url or "")
    return c or clave


def releases(e, clave, info, ctx):
    """Releases nuevas de un repo de GitHub o Gitea."""
    st = e["repos"].setdefault(norm(clave), {})
    r = red.pedir(_url_api(clave, "releases"), etag=st.get("etag_rel"))
    if r.status == 304:
        salud(e, "github", True)
        return []
    if not r.ok:
        if r.status in (403, 429):
            ctx["limite_github"] = True
        salud(e, "github" if "github.com" in clave else clave.split(":")[0],
              False, f"{clave} {r.status} {r.error}")
        return []
    salud(e, "github" if "github.com" in clave else clave.split(":")[0], True)
    try:
        lista = r.json()
    except ValueError:
        return []
    if not isinstance(lista, list):
        return []
    st["etag_rel"] = r.etag
    vistos = st.setdefault("ids", [])
    lista = [x for x in lista if not x.get("draft")]
    lista.sort(key=lambda x: iso_ts(x.get("published_at") or x.get("created_at")))
    nuevas = []
    sembrado = st.get("sembrado")
    avisar_ultima = st.pop("avisar_ultima", False)
    for i, rel in enumerate(lista):
        rid = str(rel.get("id") or rel.get("tag_name"))
        if rid in vistos:
            continue
        vistos.append(rid)
        ts = iso_ts(rel.get("published_at") or rel.get("created_at"))
        es_ultima = i == len(lista) - 1
        if not sembrado and not (avisar_ultima and es_ultima):
            continue
        canon = _canonico(clave, rel.get("html_url"))
        nuevas.append({
            "tipo": "release", "repo": canon, "repo_cfg": clave,
            "titulo": (rel.get("name") or rel.get("tag_name") or "").strip(),
            "tag": rel.get("tag_name") or "", "tag_anterior": st.get("tag", ""),
            "url": rel.get("html_url") or "", "ts": ts,
            "texto": (rel.get("body") or "")[:6000],
            "prerelease": bool(rel.get("prerelease")),
            "n_assets": len(rel.get("assets") or []),
            "primera": not st.get("tag"),
        })
    st["ids"] = vistos[-40:]
    if lista:
        st["tag"] = lista[-1].get("tag_name") or st.get("tag", "")
        st["nombre"] = _canonico(clave, lista[-1].get("html_url")).split(":", 1)[1]
    if not sembrado:
        st["sembrado"] = True
    return nuevas


# ------------------------------------------------------------ commits
RE_COMMIT_RUIDO = re.compile(
    r"^(merge|update readme|readme|typo|format|lint|ci|bump|chore|docs?|\.github|"
    r"update \S+\.md|create \S+\.md|delete|revert|wip)\b", re.I)


def commits(e, clave, info, ctx):
    st = e["repos"].setdefault(norm(clave), {})
    r = red.pedir(_url_api(clave, "commits"), etag=st.get("etag_com"))
    if r.status == 304 or not r.ok:
        if not r.ok and r.status != 304:
            salud(e, "github", False, f"{clave} commits {r.status}")
        return []
    try:
        lista = r.json()
    except ValueError:
        return []
    if not isinstance(lista, list) or not lista:
        return []
    st["etag_com"] = r.etag
    ultimo = st.get("sha")
    st["sha"] = lista[0].get("sha", "")
    if not ultimo:
        return []                        # siembra
    nuevos = []
    for c in lista:
        if c.get("sha") == ultimo:
            break
        nuevos.append(c)
    if not nuevos:
        return []
    mensajes = [((c.get("commit") or {}).get("message") or "").strip() for c in nuevos]
    utiles = [m for m in mensajes if m and not RE_COMMIT_RUIDO.match(m)]
    st["commits_24h"] = [t for t in st.get("commits_24h", []) if time.time() - t < 86400] + \
        [int(time.time())] * len(nuevos)
    if not utiles and len(st["commits_24h"]) < 10:
        return []
    ts = max(iso_ts(((c.get("commit") or {}).get("author") or {}).get("date")) for c in nuevos)
    primera = mensajes[0].splitlines()[0][:120] if mensajes else ""
    return [{
        "tipo": "commit", "repo": clave, "repo_cfg": clave,
        "titulo": primera, "tag": nuevos[0].get("sha", "")[:7], "tag_anterior": "",
        "url": nuevos[0].get("html_url") or f"https://{clave.replace(':', '/', 1)}/commits",
        "ts": ts or int(time.time()), "texto": "\n".join(utiles or mensajes)[:4000],
        "prerelease": False, "n_assets": 0, "primera": False,
        "solo_actividad": not utiles,
    }]


def readme(e, clave, info, ctx):
    """Avisa si el README pasa a declarar un firmware mayor."""
    st = e["repos"].setdefault(norm(clave), {})
    cab = {"Accept": "application/vnd.github.raw+json"} if clave.startswith("github.com") else None
    r = red.pedir(_url_api(clave, "readme"), etag=st.get("etag_readme"), cabeceras=cab)
    if r.status == 304 or not r.ok:
        return []
    st["etag_readme"] = r.etag
    texto = r.texto
    if texto.lstrip().startswith("{"):          # por si llega en JSON base64
        try:
            texto = base64.b64decode(r.json().get("content", "")).decode("utf-8", "replace")
        except Exception:
            pass
    fw = firmware.extraer(texto, techo=ctx["techo"])
    previo = st.get("fw_readme")
    st["fw_readme"] = max(fw.max, previo or 0)
    if previo is None or fw.max <= previo:
        return []
    return [{
        "tipo": "readme", "repo": clave, "repo_cfg": clave,
        "titulo": f"El README ahora indica firmware hasta {firmware.fmt(fw.max)}",
        "tag": f"fw{fw.max}", "tag_anterior": "",
        "url": f"https://{clave.replace(':', '/', 1)}",
        "ts": int(time.time()), "texto": f"Firmware soportado: {fw.texto()}",
        "prerelease": False, "n_assets": 0, "primera": False,
    }]


# ------------------------------------------------------------ listas de payloads
def listas(e, cfg, ctx):
    """Descubre repos en las listas de payloads. Un payload nuevo en la lista es novedad."""
    out = []
    for url in cfg["listas"]:
        st = e["listas"].setdefault(url, {"repos": []})
        r = red.pedir(url, etag=st.get("etag"))
        if r.status == 304:
            salud(e, "listas", True)
            continue
        if not r.ok:
            salud(e, "listas", False, f"{url} {r.status} {r.error}")
            continue
        salud(e, "listas", True)
        try:
            datos = r.json()
        except ValueError:
            continue
        st["etag"] = r.etag
        pays = datos.get("payloads", []) if isinstance(datos, dict) else datos
        conocidos = set(st["repos"])
        for p in pays:
            clave = clave_de_url(p.get("source") or p.get("url") or "")
            if not clave:
                continue
            k = norm(clave)
            if k in cfg["excluir"]:
                continue
            nombre = (p.get("name") or "").strip()
            if k not in e["descubiertos"] and k not in {norm(c) for c in cfg["repos"]}:
                e["descubiertos"][k] = {"clave": clave, "tier": "C", "alias": [nombre] if nombre else [],
                                        "origen": "lista"}
            if k not in conocidos:
                st["repos"].append(k)
                if st.get("sembrada") and k not in {norm(c) for c in cfg["repos"]}:
                    out.append({
                        "tipo": "lista_nueva", "repo": clave, "repo_cfg": clave,
                        "titulo": f"{nombre} entra en la lista de Payload Manager",
                        "tag": p.get("version") or "", "tag_anterior": "",
                        "url": p.get("source") or "", "ts": int(time.time()),
                        "texto": p.get("description") or "", "prerelease": False,
                        "n_assets": 1, "primera": True, "alias": [nombre] if nombre else [],
                    })
        st["sembrada"] = True
    return out


# ------------------------------------------------------------ repos nuevos (búsqueda)
RE_RELEVANTE = re.compile(
    r"jailbreak|exploit|kernel|payload|homebrew|webkit|\bhen\b|kstuff|elf ?loader|"
    r"\bjb\b|hypervisor|userland|firmware|\bfw\b|backport|emulator|\blinux\b|autoloader|\bpkg\b", re.I)


def busqueda(e, cfg, conf, ctx):
    desde = time.strftime("%Y-%m-%d", time.gmtime(time.time() - conf["busqueda_dias"] * 86400))
    q = quote(f"ps5 in:name,description,topics created:>={desde} fork:false")
    r = red.pedir(f"{API}/search/repositories?q={q}&sort=stars&order=desc&per_page=40")
    if not r.ok:
        salud(e, "busqueda", False, f"{r.status} {r.error}")
        return []
    salud(e, "busqueda", True)
    try:
        items = r.json().get("items", [])
    except ValueError:
        return []
    out = []
    umbrales = conf["umbrales_estrellas"]
    conocidos = {norm(c) for c in cfg["repos"]} | set(e["descubiertos"]) | cfg["excluir"]
    for it in items:
        nombre = it.get("full_name", "")
        clave = "github.com:" + nombre
        k = norm(clave)
        texto = f"{nombre} {it.get('description') or ''} {' '.join(it.get('topics') or [])}"
        if it.get("fork") or not RE_RELEVANTE.search(texto) or k in conocidos:
            continue
        estrellas = int(it.get("stargazers_count") or 0)
        st = e["busqueda"].setdefault(nombre, {"umbrales": []})
        st["estrellas"] = estrellas
        cruzados = [u for u in umbrales if estrellas >= u and u not in st["umbrales"]]
        if not cruzados:
            continue
        if not e.get("sembrado_global"):
            st["umbrales"] += cruzados
            continue
        if not st.get("con_codigo"):
            if int(it.get("size") or 0) == 0 or not _tiene_codigo(clave):
                continue                      # sin código todavía: se reintenta
            st["con_codigo"] = True
        st["umbrales"] += cruzados
        u = max(cruzados)
        out.append({
            "tipo": "repo_nuevo", "repo": clave, "repo_cfg": clave,
            "titulo": f"Repo nuevo: {nombre} ({estrellas}★)",
            "tag": f"{u}stars", "tag_anterior": "", "url": it.get("html_url") or "",
            "ts": iso_ts(it.get("created_at")), "texto": it.get("description") or "",
            "prerelease": False, "n_assets": 0, "primera": True, "estrellas": estrellas,
            "umbral": u,
        })
        # A partir de ahora también se vigilan sus releases
        e["descubiertos"].setdefault(k, {"clave": clave, "tier": "C", "alias": [nombre.split("/")[1]],
                                         "origen": "busqueda"})
    return out


RE_NO_CODIGO = re.compile(r"^(readme|license|licence|copying|\.gitignore|\.gitattributes|"
                          r".*\.(md|txt|png|jpe?g|gif|webp|svg))$", re.I)


def _tiene_codigo(clave):
    r = red.pedir(_url_api(clave, "raiz"))
    if not r.ok:
        return False
    try:
        lista = r.json()
    except ValueError:
        return False
    return any(not RE_NO_CODIGO.match(x.get("name", "")) for x in lista if isinstance(x, dict))


# ------------------------------------------------------------ Sony
def sony(e, conf, ctx):
    st = e["feeds"].setdefault("sony", {})
    versiones, error = [], ""
    for url in conf["sony"]:
        r = red.pedir(url, timeout=20)
        versiones = firmware.sony(r.texto) if r.ok else []
        if versiones:
            break
        error = f"{url} {r.status} {r.error}"
    if not versiones:
        salud(e, "sony", False, error or "sin versiones en la página")
        return []
    salud(e, "sony", True)
    nueva = max(versiones)
    previa = e["fw"].get("sony_max", 0)
    if not st.get("sembrado"):
        st["sembrado"] = True
        e["fw"]["sony_max"] = max(previa, nueva)
        return []
    if nueva <= previa:
        return []
    e["fw"]["sony_max"] = nueva
    return [{
        "tipo": "firmware_sony", "repo": "sony", "repo_cfg": "sony",
        "titulo": f"Sony publica el firmware {firmware.fmt(nueva)} de PS5",
        "tag": f"sony{nueva}", "tag_anterior": firmware.fmt(previa), "url": url,
        "ts": int(time.time()), "texto": "", "prerelease": False, "n_assets": 0,
        "primera": False, "fw_nuevo": nueva, "fw_previo": previa,
    }]


# ------------------------------------------------------------ prensa (RSS)
RE_ENLACE = re.compile(r"https?://(?:github\.com|git\.etawen\.dev)/[\w.-]+/[\w.-]+")


def prensa(e, cfg, ctx):
    out = []
    for url in cfg["prensa"]:
        st = e["feeds"].setdefault(url, {"guids": []})
        r = red.pedir(url, etag=st.get("etag"))
        if r.status == 304:
            salud(e, "prensa", True)
            continue
        if not r.ok:
            salud(e, "prensa", False, f"{url} {r.status} {r.error}")
            continue
        salud(e, "prensa", True)
        st["etag"] = r.etag
        try:
            raiz = ET.fromstring(r.cuerpo)
        except ET.ParseError:
            continue
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        sembrado = st.get("sembrado")
        for it in raiz.iter("item"):
            titulo = (it.findtext("title") or "").strip()
            enlace = (it.findtext("link") or "").strip()
            guid = (it.findtext("guid") or enlace).strip()
            if guid in st["guids"]:
                continue
            st["guids"].append(guid)
            if not sembrado:
                continue
            cuerpo = (it.findtext("content:encoded", namespaces=ns) or "") + " " + \
                     (it.findtext("description") or "")
            repos = []
            for m in RE_ENLACE.finditer(cuerpo):
                c = clave_de_url(m.group(0))
                if c and c not in repos:
                    repos.append(c)
            out.append({
                "tipo": "articulo", "repo": repos[0] if repos else "prensa", "repo_cfg": "prensa",
                "titulo": titulo, "tag": "", "tag_anterior": "", "url": enlace,
                "ts": rss_ts(it.findtext("pubDate")) or int(time.time()),
                "texto": re.sub(r"<[^>]+>", " ", cuerpo)[:3000], "prerelease": False,
                "n_assets": 0, "primera": False, "repos_enlazados": repos,
                "medio": re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", url),
            })
        st["guids"] = st["guids"][-200:]
        st["sembrado"] = True
    return out
