"""Puerta de «ya disponible» y puntuación por reglas (funciona sin IA)."""
import re
import time

from . import firmware

# --- titulares de prensa: solo lo ya publicado
RE_PRENSA_SI = re.compile(
    r"\b(released?|releases|out now|is out|now available|available now|available|download|"
    r"publicad[oa]|disponible|ya está|lanzad[oa]|update[sd]? to|v\d+(\.\d+)+)\b", re.I)
RE_PRENSA_NO = re.compile(
    r"\b(could|may|might|coming|soon|teases?|teased|teasing|claims?|claimed|rumou?rs?|leak(s|ed)?|"
    r"preview|wip|work in progress|in progress|progress|announce[sd]?|announcement|plans?|planned|"
    r"showcase[sd]?|demo(ed|nstrat\w+)?|eta|hints?|hinted|tease|upcoming|"
    r"possibl[ey]|potential(ly)?|reportedly|report says|en camino|próximamente|podría|"
    r"se espera|anuncia|promete|avance|will|to be|won't|not|yet|next (?:week|month)|"
    r"after|video|write-?up|shows?|showing|details|explained|teaser|confirm(s|ed)?|"
    r"working on|in the works|delay(ed)?|soon™)\b|\?", re.I)
# Repos que solo prometen (descripción): lista corta y específica
RE_PROMESA = re.compile(r"coming soon|star (this|to|for)|notif(y|ied|ication)|release soon|releasing soon|"
                        r"\bwip\b|work in progress|placeholder|stay tuned|próximamente|en desarrollo|"
                        r"\bsoon\b|follow (me|us) for", re.I)
RE_JUEGOS = re.compile(r"\b(CUSA|PPSA)\d{5}\b|dlpsgame|game ?dump(s|er|ers)?\b|app ?dumper|"
                       r"\bdumper\b|game ?backups?\b|game[- ]?compressor|free games? download|"
                       r"\bwarez\b|pirat", re.I)

RE_TRAD = re.compile(r"translat|languag|locali[sz]|i18n|crowdin|typo|readme|docs?\b|"
                     r"documentation|changelog|idioma|traducc", re.I)
RE_FIX = re.compile(r"\bfix|bug|crash|stabil|cleanup|clean up|refactor|\bci\b|bump|minor|"
                    r"improv|tweak|polish|log(s|ging)?\b|ui\b|typo", re.I)
RE_FEAT = re.compile(r"\badd(ed|s)?\b|\bnew\b|support|implement|\bnow\b|offsets?|\bport(ed)?\b|"
                     r"introduc|enabl|release", re.I)
RE_FUERTE = re.compile(r"exploit|kernel|jailbreak|\bhen\b|hypervisor|\blinux\b|backport|"
                       r"emulat|\bjit\b|userland|kexp|kstuff|debug settings", re.I)
RE_PRE = re.compile(r"(beta|rc\d*|alpha|pre|test|nightly|dev)", re.I)


def parse_ver(tag):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", tag or "")
    if not m or len(m.group(1)) > 3:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def salto(tag, anterior):
    a, b = parse_ver(tag), parse_ver(anterior)
    if not a or not b:
        return None
    if a[0] > b[0]:
        return "major"
    if a[:2] > b[:2]:
        return "minor"
    if a > b:
        return "patch"
    return "igual"


def clasificar_lineas(texto):
    lineas = [l.strip(" -*•\t") for l in (texto or "").splitlines()]
    lineas = [l for l in lineas if l and not l.startswith(("#", "**Full Changelog", "Full Changelog"))
              and len(l) > 3]
    c = {"trad": 0, "fix": 0, "feat": 0, "otras": 0}
    for l in lineas:
        if RE_TRAD.search(l):
            c["trad"] += 1
        elif RE_FEAT.search(l) and not RE_FIX.match(l):
            c["feat"] += 1
        elif RE_FIX.search(l):
            c["fix"] += 1
        else:
            c["otras"] += 1
    return c


def disponible(n):
    """¿Se puede usar ya? Devuelve (bool, texto_estado)."""
    t = n["tipo"]
    if t == "release":
        if n.get("prerelease") or RE_PRE.search(n.get("tag", "")):
            return True, "beta publicada"
        return True, "release publicada"
    if t in ("commit", "readme"):
        return True, "código publicado"
    if t == "firmware_sony":
        return True, "firmware oficial publicado"
    if t == "repo_nuevo":
        return True, "código publicado (repo nuevo)"
    if t == "articulo":
        tit = n.get("titulo", "")
        if RE_PRENSA_NO.search(tit) or not RE_PRENSA_SI.search(tit):
            return False, "teaser o rumor"
        if not n.get("repos_enlazados"):
            return False, "sin enlace a la descarga"
        if not n.get("descarga"):
            return False, "enlaza repos, pero sin release reciente ni enlace de descarga"
        return True, f"publicado ({n.get('medio', 'prensa')} enlaza la descarga)"
    return False, "desconocido"


def es_ps5(n):
    """La prensa mezcla PS4, Switch... Solo PS5."""
    if n["tipo"] != "articulo":
        return True
    return bool(re.search(r"\bps5\b|playstation 5", n.get("titulo", ""), re.I))


def puntuar(n, e, conf):
    """Nota 0-10 y razones. Modifica n."""
    raz = []
    fw_e = e["fw"]
    expl_max = fw_e.get("explotable_max", conf["fw_semilla"]["explotable_max"])
    sony_max = fw_e.get("sony_max", conf["fw_semilla"]["sony_max"])
    tier = n.get("tier", "C")
    t = n["tipo"]
    texto = f"{n.get('titulo', '')}\n{n.get('texto', '')}"
    fw = firmware.extraer(texto, tag=n.get("tag"), techo=sony_max + 100)
    n["fw"] = fw.lista
    st_repo = e["repos"].get((n.get("repo_cfg") or "").lower(), {})

    if t == "firmware_sony":
        nota = 9
        if n["fw_nuevo"] // 100 > n["fw_previo"] // 100:
            nota = 10
            raz.append("cambio de versión mayor")
        raz.append("firmware nuevo de Sony: ¿actualizar o no?")
        return _fin(n, nota, raz)

    if t == "repo_nuevo":
        u = n.get("umbral", 50)
        estrellas = n.get("estrellas", 0)
        nota = 9 if u >= 1000 else 8 if u >= 200 else 6
        raz.append(f"repo nuevo con {estrellas}★")
        if fw.max > expl_max:
            raz.insert(0, f"dice soportar {firmware.fmt(fw.max)} (por encima de lo conocido)")
            if estrellas >= 1000:
                nota = 10
            else:
                nota = min(max(nota, 7), 8)      # las estrellas se compran: sin verificar
        if estrellas < 1000:
            nota = min(nota, 8)
            n["tope"] = 8
            n["sin_verificar"] = True
        return _fin(n, nota, raz)

    base = {"A": 7, "B": 5}.get(tier, 4)
    nota = base
    lineas = clasificar_lineas(n.get("texto", ""))

    if t == "release":
        s = salto(n.get("tag"), n.get("tag_anterior"))
        if n.get("primera"):
            nota += 2
            raz.append("primera release")
        elif s == "major":
            nota += 2
            raz.append("versión mayor")
        elif s == "minor":
            nota += 1
            raz.append("versión menor")
        elif s is None and not parse_ver(n.get("tag")):
            nota = min(nota, 3)
            raz.append("build automática")
        if n.get("prerelease") or RE_PRE.search(n.get("tag", "")):
            nota -= 1
        if n.get("nucleo") and s in ("major", "minor") and lineas["feat"]:
            nota = max(nota, 9)
            raz.append("repo clave con novedades")
    elif t == "commit":
        if n.get("solo_actividad"):
            nota = 3
            raz.append("mucha actividad")
        else:
            nota = 7 if tier == "A" else 6
            raz.append("cambios en el código")
            if not (lineas["feat"] or fw.lista or RE_FUERTE.search(texto)):
                nota -= 2
    elif t == "readme":
        nota = 6

    propios = " ".join([str(n.get("repo") or "")] + [str(a) for a in n.get("alias") or []]).lower()
    utiles = [n.get("titulo", "")] + [l for l in str(n.get("texto", "")).splitlines()
                                      if RE_FEAT.search(l) and not RE_FIX.match(l.strip(" -*•\t"))]
    fuertes = {m.group(0).lower() for l in utiles for m in RE_FUERTE.finditer(l)} - \
        {w for w in ("exploit", "kernel", "jailbreak", "hen", "hypervisor", "linux", "backport", "emulat",
                     "jit", "userland", "kexp", "kstuff", "debug settings") if w in propios}
    if fuertes and t != "articulo":
        nota += min(2, len(fuertes))
        raz.append("exploit/HEN/kernel")

    fiable = t == "release" or (t == "readme" and n.get("nucleo"))
    if fw.lista:
        if fw.max > expl_max:
            if tier == "A" and fiable:
                nota = 10
                raz.insert(0, f"¡firmware {firmware.fmt(fw.max)}, por encima de lo explotable hasta "
                              f"ahora ({firmware.fmt(expl_max)})!")
            else:
                nota = min(max(nota + 2, 7), 8)
                n["tope"] = 8
                n["sin_verificar"] = True
                raz.insert(0, f"menciona {firmware.fmt(fw.max)} (sin confirmar como exploit)")
        elif fw.max > st_repo.get("fw_max", 0) and st_repo.get("fw_max"):
            nota += 2
            raz.insert(0, f"amplía soporte hasta {firmware.fmt(fw.max)}")

    total = sum(lineas.values())
    if total and lineas["trad"] == total:
        nota = min(nota, 2)
        raz.append("solo traducciones/documentación")
    elif total and lineas["feat"] == 0 and lineas["fix"] and not fw.lista:
        nota = min(nota, 5 if tier == "A" else 4)
        raz.append("solo arreglos")

    if t == "commit":
        nota = min(nota, 8)              # un push no es una versión: nunca 🔥 por sí solo
        n["tope"] = 8
    if t == "articulo":
        nota = min(max(nota, 6), 7)
        n["tope"] = 7
        raz.append(f"publicado en {n.get('medio', 'prensa')}")
        # Exploit nuevo que solo llega por prensa fiable, con descarga y para un firmware
        # por encima de lo conocido: 🔥 (no mueve el listón de lo explotable)
        tit = n.get("titulo", "")
        fw_t = firmware.extraer(tit, techo=sony_max + 100)
        if n.get("enlace_descarga") and re.search(r"exploit|jailbreak|kernel|\bhen\b", tit, re.I) \
                and fw_t.max > expl_max:
            nota = 9
            n["tope"] = 9
            n["sin_verificar"] = True
            raz.insert(0, f"exploit para {firmware.fmt(fw_t.max)} con descarga, según {n.get('medio', 'prensa')}")
    return _fin(n, nota, raz)


def nivel(nota, conf):
    if nota >= conf["umbral_fuego"]:
        return "fuego"
    if nota >= 6:
        return "interesante"
    if nota >= conf["umbral_resumen"]:
        return "menor"
    return "descartado"


def _fin(n, nota, raz):
    n["nota"] = max(0, min(10, nota))
    n["razones"] = raz
    return n


def demasiado_vieja(n, conf, ahora=None):
    ahora = ahora or time.time()
    return n.get("ts") and ahora - n["ts"] > conf["max_antiguedad_horas"] * 3600


def es_de_juegos(n):
    """Descargas o volcados de juegos: fuera. En releases solo se mira el título y el repo
    (el changelog de un HEN puede citar «backups» o «fpkg» sin ser de juegos)."""
    base = f"{n.get('titulo', '')} {n.get('repo', '')}"
    if n["tipo"] in ("articulo", "repo_nuevo"):
        base += " " + str(n.get("texto", ""))
    return bool(RE_JUEGOS.search(base))
