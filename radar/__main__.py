"""Radar de la scene de PS5 -> Telegram.

Uso: python -m radar {ronda|prueba|diagnostico|resumen} [--estado ruta]
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from . import competencia, estado, formato, fuentes, ia, red, reglas, telegram
from .fuentes import norm

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMPOS_GUARDADOS = ("clave", "tipo", "repo", "repo_cfg", "titulo", "tag", "url", "ts", "nota", "nivel",
                    "estado_txt", "fw", "fw_ia", "resumen", "angulo", "competencia", "razones",
                    "sin_ia", "confirmado_por", "sin_verificar", "fw_nuevo", "fw_previo", "alias")


def cargar_conf():
    with open(os.path.join(RAIZ, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def ahora_local(conf):
    return datetime.now(ZoneInfo(conf["zona"]))


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def en_silencio(conf, dt=None):
    dt = dt or ahora_local(conf)
    a, b = (_hm(x) for x in conf["silencio"])
    t = dt.hour * 60 + dt.minute
    return a <= t < b if a <= b else (t >= a or t < b)


def clave_novedad(n):
    r = norm(str(n.get("repo") or ""))
    t = n["tipo"]
    tag = str(n.get("tag") or "").lower()
    if t == "release":
        return f"{r}@{tag}"
    if t == "commit":
        return f"{r}#{tag}"
    if t in ("readme", "repo_nuevo"):
        return f"{r}!{tag}"
    if t == "firmware_sony":
        return tag
    return "art:" + str(n.get("url") or n.get("titulo", "")).lower()


# ------------------------------------------------------------ equivalencias de repos
class Equivalencias:
    """Agrupa los nombres de un mismo repo (config, renombrado, descubierto)."""

    def __init__(self, e, cfg):
        self.e, self.cfg = e, cfg
        self.grupo = {}
        for c in cfg["repos"]:
            self._unir(norm(c), norm(c))
        for k, st in e["repos"].items():
            if ":" not in k:
                continue
            base = self.grupo.get(k, k)
            self._unir(k, base)
            if st.get("nombre"):
                self._unir(norm(k.split(":")[0] + ":" + st["nombre"]), base)
        self.reales = set(self.grupo.values())     # grupos que ya vigilamos
        for k, canon in e.get("renombres", {}).items():
            self._unir(k, self.grupo.get(canon, canon))

    def _unir(self, k, base):
        self.grupo.setdefault(k, base)

    def de(self, clave):
        k = norm(clave)
        return self.grupo.get(k, k)

    def conocido(self, clave):
        return self.de(clave) in self.reales

    def resolver_enlace(self, clave):
        """Para un enlace de prensa desconocido: pregunta a GitHub su nombre actual (una vez)."""
        k = norm(clave)
        if k in self.grupo or not k.startswith("github.com:"):
            return self.de(k)
        ren = self.e.setdefault("renombres", {})
        if k not in ren:
            r = red.pedir(f"{fuentes.API}/repos/{clave.split(':', 1)[1]}")
            try:
                ren[k] = norm("github.com:" + r.json()["full_name"]) if r.ok else k
            except (ValueError, KeyError, TypeError):
                ren[k] = k
            if len(ren) > 400:
                for x in list(ren)[:100]:
                    ren.pop(x, None)
        canon = ren[k]
        base = self.grupo.get(canon, canon)
        self.grupo[k] = base
        return base


# ------------------------------------------------------------ anotar repos
def info_repo(clave, cfg, e, eq=None):
    k = norm(clave)
    grupo = eq.de(k) if eq else k
    for c, info in cfg["repos"].items():
        if norm(c) in (k, grupo):
            return info
    d = e["descubiertos"].get(k) or e["descubiertos"].get(grupo)
    if d:
        return {"tier": "C", "alias": d.get("alias", []), "nucleo": False}
    return {"tier": "C", "alias": [], "nucleo": False}


def anotar(n, cfg, e, eq):
    info = info_repo(n.get("repo_cfg") or n.get("repo"), cfg, e, eq)
    n["tier"] = info.get("tier", "C")
    n["nucleo"] = info.get("nucleo", False)
    n["alias"] = list(dict.fromkeys((n.get("alias") or []) + list(info.get("alias", []))))
    return n


# ------------------------------------------------------------ recogida
def recoger(e, cfg, conf, ctx):
    ronda = e["ronda"]
    out = []
    hechos = set()

    def tarea(nombre, f, *a):
        if ctx.get("limite_github") and nombre.startswith("gh"):
            return
        try:
            out.extend(f(*a) or [])
        except Exception as ex:
            print(f"[{nombre}] error: {type(ex).__name__}: {ex}")
            traceback.print_exc()
            estado.salud(e, nombre.split(":")[0], False, type(ex).__name__)

    todo = ctx.get("todo")
    if ronda % 3 == 1 or todo:
        tarea("sony", fuentes.sony, e, conf, ctx)
    if ronda % 6 == 1 or todo:
        tarea("listas", fuentes.listas, e, cfg, ctx)
    # Repos configurados: A cada ronda, B cada 3
    for clave, info in cfg["repos"].items():
        if norm(clave) in cfg["excluir"]:
            continue
        if info["tier"] == "B" and ronda % 3 != 0 and not todo:
            continue
        hechos.add(norm(clave))
        tarea("gh:rel", fuentes.releases, e, clave, info, ctx)
        if info.get("commits"):
            tarea("gh:com", fuentes.commits, e, clave, info, ctx)
        if info.get("readme") and (ronda % 6 == 2 or todo):
            tarea("gh:readme", fuentes.readme, e, clave, info, ctx)
    # Descubiertos (tier C): primero los recién entrados en una lista; luego por turnos
    eq = Equivalencias(e, cfg)
    configurados = {norm(c) for c in cfg["repos"]}
    for k in list(e["descubiertos"]):
        if e["repos"].get(k, {}).get("muerto", 0) >= 3:      # 404 repetido: fuera
            e["descubiertos"].pop(k, None)
    desc = sorted(k for k in e["descubiertos"]
                  if k not in cfg["excluir"] and eq.de(k) not in configurados and k not in configurados)
    prioridad = [k for k in ctx.get("prioridad", []) if k in e["descubiertos"]]
    if desc:
        trozo = 15
        ini = (ronda * trozo) % len(desc)
        lote = desc if todo else (desc + desc)[ini:ini + min(trozo, len(desc))]
        for k in dict.fromkeys(prioridad + lote):
            if k in hechos:
                continue
            hechos.add(k)
            d = e["descubiertos"][k]
            tarea("gh:rel", fuentes.releases, e, d["clave"], d, ctx)
    if ronda % 6 == 1 or todo:
        tarea("gh:busqueda", fuentes.busqueda, e, cfg, conf, ctx)
    if ronda % 2 == 1 or todo:
        tarea("prensa", fuentes.prensa, e, cfg, ctx)
    eq = Equivalencias(e, cfg)
    return [anotar(n, cfg, e, eq) for n in out]


# ------------------------------------------------------------ evaluación
def enviados_recientes(e, horas=72):
    lim = time.time() - horas * 3600
    return [x for x in e["enviados"] if x.get("ts", 0) >= lim]


def _menciona(n, titulo):
    t = (titulo or "").lower()
    nombres = list(n.get("alias") or []) + [str(n.get("repo") or "").split("/")[-1]]
    return any(len(x) >= 4 and x.lower() in t for x in nombres)


def _descarga_reciente(n, e, eq):
    """¿El artículo enlaza una descarga o un repo con release en las últimas 72 h?"""
    if n.get("enlace_descarga"):
        return True
    lim = time.time() - 72 * 3600
    for c in n.get("repos_enlazados", []):
        g = eq.de(c)
        for k, st in e["repos"].items():
            if (k == g or eq.de(k) == g) and st.get("ult_rel_ts", 0) >= lim:
                return True
    return False


def evaluar(novedades, e, cfg, conf, ctx):
    """Devuelve (para_enviar_ya, para_resumen)."""
    ahora = time.time()
    eq = Equivalencias(e, cfg)
    candidatas = []
    for n in novedades:
        try:
            if n["tipo"] == "articulo":
                n["repos_enlazados"] = list(dict.fromkeys(eq.resolver_enlace(c)
                                                          for c in n.get("repos_enlazados", [])))
                n["descarga"] = _descarga_reciente(n, e, eq)
                if n["repos_enlazados"]:
                    n["repo"] = n["repos_enlazados"][0]
            grupo = eq.de(n.get("repo_cfg") or n.get("repo") or "")
            if grupo in cfg["excluir"] or norm(n.get("repo")) in cfg["excluir"]:
                continue
            if not reglas.es_ps5(n) or reglas.es_de_juegos(n):
                continue
            ok, txt = reglas.disponible(n)
            if not ok:
                print(f"[filtro] no disponible ({txt}): {n.get('titulo')}")
                continue
            n["estado_txt"] = txt
            if reglas.demasiado_vieja(n, conf, ahora) and not n.get("exento_antiguedad"):
                continue
            n["clave"] = clave_novedad(n)
            if n["clave"] in e["vistos"]:
                continue
            reglas.puntuar(n, e, conf)
            if n.get("nuevo_en_lista"):
                n["razones"].insert(0, "acaba de entrar en Payload Manager")
            n["nivel"] = reglas.nivel(n["nota"], conf)
            n["grupo"] = grupo
            # Enfriamiento de commits/README del mismo repo (antes de agrupar)
            if n["tipo"] in ("commit", "readme") and n["nivel"] != "fuego":
                ult = e["repos"].get(norm(n["repo_cfg"]), {}).get("ult_aviso", 0)
                if ahora - ult < 6 * 3600:
                    continue
            candidatas.append(n)
        except Exception as ex:
            print(f"[evaluar] {type(ex).__name__}: {ex} en {n.get('titulo')!r}")
            traceback.print_exc()

    # Artículos sobre algo detectado en esta misma ronda: solo confirman
    propias = {n["grupo"]: n for n in candidatas if n["tipo"] != "articulo"}
    restantes = []
    for n in candidatas:
        if n["tipo"] == "articulo" and n.get("repos_enlazados"):
            principal = eq.de(n["repos_enlazados"][0])
            dueño = propias.get(principal)
            if not dueño:
                dueño = next((propias[eq.de(c)] for c in n["repos_enlazados"]
                              if eq.de(c) in propias and _menciona(propias[eq.de(c)], n["titulo"])), None)
            if dueño:
                medio = n.get("medio", "prensa")
                dueño.setdefault("confirmado_por", [])
                if medio not in dueño["confirmado_por"]:
                    dueño["confirmado_por"].append(medio)
                e["vistos"][n["clave"]] = int(ahora)
                _descubrir(n, e, cfg, eq)
                continue
        restantes.append(n)

    # Una novedad por repo y ronda: la de más nota (las demás aportan razones)
    por_repo = {}
    for n in sorted(restantes, key=lambda x: -x["nota"]):
        k = n["grupo"] if n["tipo"] not in ("articulo", "firmware_sony") else n["clave"]
        if k in por_repo:
            p = por_repo[k]
            p["razones"] = p["razones"] + [f"además: {str(n.get('titulo', ''))[:60]}"]
            p.setdefault("absorbidas", []).append(n)
            continue
        por_repo[k] = n

    ya, resumen = [], []
    llamadas_ia = 0
    for n in por_repo.values():
        try:
            e["vistos"][n["clave"]] = int(ahora)
            for a in n.get("absorbidas", []):
                e["vistos"][a["clave"]] = int(ahora)
            if n["tipo"] == "articulo":
                _descubrir(n, e, cfg, eq)
                if _confirma(n, e, cfg, eq, conf):
                    continue
            if n["nivel"] == "descartado":
                continue
            # IA (solo lo que ya promete)
            if n["nota"] >= 6 and llamadas_ia < 6:
                llamadas_ia += 1
                res = ia.enriquecer(n, e, conf, ctx)
                if not ia.combinar(n, res):
                    print(f"[ia] descartado por la IA (no disponible o de juegos): {n.get('titulo')}")
                    continue
                n["nivel"] = reglas.nivel(n["nota"], conf)
                if n["nivel"] == "descartado":
                    continue
            else:
                n["sin_ia"] = True
            _actualizar_fw(n, e, cfg, conf)
            (ya if n["nivel"] == "fuego" else resumen).append(n)
        except Exception as ex:
            print(f"[evaluar] {type(ex).__name__}: {ex} en {n.get('titulo')!r}")
            traceback.print_exc()
    return ya, resumen


def _descubrir(n, e, cfg, eq):
    """Los repos enlazados por la prensa que no conocemos pasan a vigilarse."""
    for c in n.get("repos_enlazados", []):
        k = norm(c)
        if eq.conocido(k) or k in e["descubiertos"] or k in cfg["excluir"]:
            continue
        e["descubiertos"][k] = {"clave": c, "tier": "C", "alias": [c.split("/")[-1]], "origen": "prensa"}


def _confirma(n, e, cfg, eq, conf):
    """Si el artículo habla de algo ya avisado o pendiente, solo lo confirma."""
    principal = eq.de(n["repos_enlazados"][0]) if n.get("repos_enlazados") else None
    enlazados = {eq.de(c) for c in n.get("repos_enlazados", [])}
    medio = n.get("medio", "prensa")

    def es_suyo(p):
        g = eq.de(p.get("repo_cfg") or p.get("repo") or "")
        return g == principal or (g in enlazados and _menciona(p, n["titulo"]))

    for p in list(e["pendientes"]) + list(e["cola"]):
        if es_suyo(p):
            p.setdefault("confirmado_por", [])
            if medio not in p["confirmado_por"]:
                p["confirmado_por"].append(medio)
                p["nota"] = min(10, p.get("nota", 0) + 1)
                if p in e["pendientes"] and p["nota"] >= conf["umbral_fuego"]:
                    p["nivel"] = "fuego"
                    e["pendientes"].remove(p)
                    e["cola"].append(p)
            return True
    for x in enviados_recientes(e):
        if es_suyo(x):
            return True
    return False


def _actualizar_fw(n, e, cfg, conf):
    fwmax = max(n.get("fw") or [0])
    if not fwmax or n["tipo"] not in ("release", "commit", "readme", "repo_nuevo"):
        return
    st = e["repos"].setdefault(norm(n["repo_cfg"]), {})
    st["fw_max"] = max(st.get("fw_max", 0), fwmax)
    if n.get("sin_verificar"):
        return
    fiable = (n.get("tier") == "A" and (n["tipo"] == "release" or (n["tipo"] == "readme" and n.get("nucleo")))) \
        or (n["tipo"] == "repo_nuevo" and n.get("estrellas", 0) >= 50)
    if fiable and fwmax <= e["fw"].get("sony_max", 9999):
        e["fw"]["explotable_max"] = max(e["fw"].get("explotable_max", 0), fwmax)


# ------------------------------------------------------------ envío
def guardar_fn(e, ruta):
    return lambda: estado.guardar(e, ruta)


def compacto(n):
    return {k: n[k] for k in CAMPOS_GUARDADOS if k in n}


def enviar_cola(e, conf, ruta):
    guardar = guardar_fn(e, ruta)
    rec = telegram.recuperar_en_curso(e)
    if rec and rec[1]:
        hecho = next((x for x in e["cola"] if x.get("clave") == rec[0]), {"clave": rec[0]})
        e["cola"] = [x for x in e["cola"] if x.get("clave") != rec[0]]
        _apuntar_enviado(e, hecho, rec[1])
        guardar()
    silencio = en_silencio(conf)
    for n in list(e["cola"]):
        if silencio and not conf.get("fuego_rompe_silencio", True):
            break
        try:
            texto = formato.aviso(n)
            boton = telegram.boton(n.get("url") or "https://github.com")
        except Exception as ex:
            print(f"[cola] no se puede formatear ({type(ex).__name__}); se aparta: {n.get('clave')}")
            e["cola"] = [x for x in e["cola"] if x.get("clave") != n.get("clave")]
            continue
        mid = telegram.enviar(e, texto, boton, guardar=guardar, clave=n["clave"])
        if mid is None and telegram._token():
            break                     # fallo pasajero: se reintenta en la próxima ronda
        e["cola"] = [x for x in e["cola"] if x.get("clave") != n["clave"]]
        if mid and mid != telegram.PERMANENTE:
            _apuntar_enviado(e, n, mid)
        guardar()


def _apuntar_enviado(e, n, mid):
    e["enviados"].append({"clave": n.get("clave"), "repo": n.get("repo", ""), "repo_cfg": n.get("repo_cfg", ""),
                          "alias": n.get("alias", []), "msg_id": mid, "ts": int(time.time())})
    st = e["repos"].get(norm(n.get("repo_cfg") or n.get("repo") or ""))
    if st is not None:
        st["ult_aviso"] = int(time.time())


def toca_resumen(e, conf, forzar=False):
    hoy = ahora_local(conf)
    if forzar:
        return True
    if e["resumen"].get("ultimo_dia") == hoy.strftime("%Y-%m-%d"):
        return False
    return hoy.hour * 60 + hoy.minute >= _hm(conf["hora_resumen"])


def enviar_resumen(e, cfg, conf, ruta, forzar=False):
    if not toca_resumen(e, conf, forzar):
        return
    dia = ahora_local(conf).strftime("%Y-%m-%d")
    items = sorted(e["pendientes"], key=lambda x: (-x.get("nota", 0), -x.get("ts", 0)))
    if not items:
        e["resumen"]["ultimo_dia"] = dia
        return
    try:                               # competencia al día justo antes de enviar
        competencia.refrescar(cfg, e)
        for x in items:
            x["competencia"] = competencia.linea(x, e)
    except Exception as ex:
        print(f"[youtube] {type(ex).__name__}")
    extra = max(0, len(items) - 15)
    trozos = formato.trocear(formato.resumen(items[:15], conf["zona"], extra))
    r = e["resumen"]
    if r.get("dia_trozos") != dia:
        r["dia_trozos"], r["trozos_enviados"] = dia, 0
    for i, trozo in enumerate(trozos):
        if i < r.get("trozos_enviados", 0):
            continue                    # ya salió en una ronda anterior
        url = items[0].get("url") or "https://github.com"
        boton = telegram.boton(url, f"🗞️ Resumen {i + 1}/{len(trozos)} · {dia}")
        mid = telegram.enviar(e, trozo, boton, guardar=guardar_fn(e, ruta), clave=f"resumen:{dia}:{i}")
        if mid is None and telegram._token():
            return                      # se reintenta en la próxima ronda
        r["trozos_enviados"] = i + 1
        estado.guardar(e, ruta)
    e["pendientes"] = []
    r["ultimo_dia"] = dia
    r["trozos_enviados"] = 0
    estado.guardar(e, ruta)


# ------------------------------------------------------------ modos
def modo_ronda(e, cfg, conf, ruta):
    ctx = {"techo": e["fw"].get("sony_max", 1422) + 100}
    e["ronda"] = e.get("ronda", 0) + 1
    estado.guardar(e, ruta)                  # que la ronda avance aunque algo falle después
    primera = not e.get("sembrado_global")
    if primera or os.environ.get("RADAR_TODO"):
        ctx["todo"] = True
    novedades = recoger(e, cfg, conf, ctx)
    print(f"[ronda {e['ronda']}] novedades en bruto: {len(novedades)}")
    if primera:
        e["sembrado_global"] = True
        e["resumen"]["ultimo_dia"] = ahora_local(conf).strftime("%Y-%m-%d")
        print("[ronda] primera ronda: todo sembrado en silencio")
        estado.guardar(e, ruta)
        vigilados = len(cfg["repos"]) + len(e["descubiertos"])
        telegram.enviar(e, "📡 <b>Radar de la scene activado</b>\n"
                        f"Vigilando {vigilados} repos, Sony, prensa y {len(cfg['youtube'])} canales. "
                        "Solo avisaré de lo que ya esté publicado.", None, guardar=guardar_fn(e, ruta))
        estado.guardar(e, ruta)
        return
    ya, resumen = evaluar(novedades, e, cfg, conf, ctx)
    if ya or resumen:
        try:
            competencia.refrescar(cfg, e)
        except Exception as ex:
            print(f"[youtube] {type(ex).__name__}")
    for n in ya + resumen:
        try:
            n["competencia"] = competencia.linea(n, e)
        except Exception:
            n["competencia"] = "sin datos"
    for n in ya:
        e["cola"].append(compacto(n))
    for n in resumen:
        e["pendientes"].append(compacto(n))
    for n in ya + resumen:
        print(f"[{n['nivel']}] {n['nota']}/10 {n['titulo']} ({n['repo']}) {n.get('razones')}")
    estado.guardar(e, ruta)
    enviar_cola(e, conf, ruta)
    enviar_resumen(e, cfg, conf, ruta)
    estado.guardar(e, ruta)


def modo_prueba(e, cfg, conf, ruta):
    n = {"tipo": "release", "repo": "github.com:etaHEN/etaHEN", "repo_cfg": "github.com:etaHEN/etaHEN",
         "titulo": "etaHEN 2.6b (EJEMPLO)", "tag": "2.6b", "tag_anterior": "2.5b",
         "url": "https://github.com/etaHEN/etaHEN/releases", "ts": int(time.time()),
         "texto": "- Added support for firmware 13.60\n- Integrated kstuff\n- Fixed crash",
         "prerelease": False, "n_assets": 1, "primera": False}
    anotar(n, cfg, e, Equivalencias(e, cfg))
    n["estado_txt"] = "release publicada"
    reglas.puntuar(n, {"fw": dict(conf["fw_semilla"]), "repos": {}}, conf)
    n["nivel"] = reglas.nivel(n["nota"], conf)
    res = ia.enriquecer(n, e, conf, {})
    ia.combinar(n, res)
    try:
        competencia.refrescar(cfg, e)
    except Exception as ex:
        print(f"[youtube] {type(ex).__name__}")
    n["competencia"] = competencia.linea(n, e)
    mid = telegram.enviar(e, formato.aviso(n, prueba=True), telegram.boton(n["url"]), registrar=False)
    print("enviado" if mid else "NO enviado", "| IA:", "sí" if res else f"no ({e['ia'].get('ultimo_error')})")


def modo_diagnostico(e, cfg, conf, ruta):
    lin = ["🩺 <b>Diagnóstico del radar</b>", ""]

    def p(nombre, ok, extra=""):
        lin.append(f"{'✅' if ok else '❌'} {nombre}{(' — ' + extra) if extra else ''}")

    r = red.pedir("https://api.github.com/rate_limit")
    try:
        core = r.json()["resources"]["core"]
        p("GitHub API", r.ok, f"quedan {core['remaining']}/{core['limit']} peticiones esta hora")
    except Exception:
        p("GitHub API", False, f"HTTP {r.status}")
    r = red.pedir("https://api.github.com/repos/ntfargo/Relapse-Exploit")
    p("Repo de ejemplo (Relapse)", r.ok, f"HTTP {r.status}")
    for url in cfg["listas"]:
        r = red.pedir(url)
        p("Lista " + url.split("/")[2], r.ok, f"HTTP {r.status}")
    from .firmware import sony as fws, fmt
    for url in conf["sony"]:
        r = red.pedir(url, timeout=20)
        vs = fws(r.texto) if r.ok else []
        p("Sony " + url.split("/")[3], bool(vs), f"última {fmt(max(vs))}" if vs else f"HTTP {r.status}")
        if vs:
            break
    for url in cfg["prensa"]:
        r = red.pedir(url)
        p("Prensa " + url.split("/")[2], r.ok, f"HTTP {r.status}")
    for canal in cfg["youtube"]:
        cid = competencia.resolver(canal, e)
        p("YouTube " + canal["nombre"], bool(cid), "" if cid else "no se pudo resolver el canal")
    if os.environ.get("SUPER_IA_URL"):
        n = {"tipo": "release", "repo": "prueba", "titulo": "Prueba", "tag": "1.0", "texto": "Prueba"}
        tmp = {"ia": {"fallos": 0}, "ronda": 1}
        res = ia.enriquecer(n, tmp, conf, {})
        p("super-ia", bool(res), "" if res else
          f"no responde ({tmp['ia'].get('ultimo_error', '')}); los avisos saldrán sin IA")
    else:
        p("super-ia", False, "falta el secreto SUPER_IA_URL (los avisos saldrán sin IA)")
    ok, user, st = telegram.soy_admin_o_existe()
    p("Bot de Telegram", ok, f"@{user}" if ok else f"HTTP {st}")
    lin += ["", f"Ronda: {e.get('ronda', 0)} · Repos vigilados: {len(cfg['repos']) + len(e['descubiertos'])}"
                f" · Pendientes para el resumen: {len(e['pendientes'])} · En cola: {len(e['cola'])}"]
    texto = "\n".join(lin)
    print(texto)
    telegram.enviar(e, texto, None, registrar=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", nargs="?", default="ronda",
                    choices=["ronda", "prueba", "diagnostico", "resumen"])
    ap.add_argument("--estado", default=os.path.join(RAIZ, "_estado", "estado.json"))
    ap.add_argument("--fuentes", default=os.path.join(RAIZ, "fuentes.txt"))
    a = ap.parse_args(argv)
    conf = cargar_conf()
    cfg = fuentes.leer_config(a.fuentes)
    e = estado.cargar(a.estado)
    for k, v in conf["fw_semilla"].items():
        e["fw"].setdefault(k, v)
    if a.modo == "ronda":
        modo_ronda(e, cfg, conf, a.estado)
    elif a.modo == "prueba":
        modo_prueba(e, cfg, conf, a.estado)
    elif a.modo == "diagnostico":
        modo_diagnostico(e, cfg, conf, a.estado)
    elif a.modo == "resumen":
        enviar_resumen(e, cfg, conf, a.estado, forzar=True)
        estado.guardar(e, a.estado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
