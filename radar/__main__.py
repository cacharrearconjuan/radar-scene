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
CAMPOS_GUARDADOS = ("clave", "tipo", "repo", "titulo", "tag", "url", "ts", "nota", "nivel",
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
    r = norm(n.get("repo"))
    t = n["tipo"]
    if t in ("release", "lista_nueva"):
        return f"{r}@{(n.get('tag') or '').lower()}"
    if t == "commit":
        return f"{r}#{n.get('tag')}"
    if t in ("readme", "repo_nuevo"):
        return f"{r}!{n.get('tag')}"
    if t == "firmware_sony":
        return n["tag"]
    return "art:" + (n.get("url") or n.get("titulo", "")).lower()


# ------------------------------------------------------------ anotar repos
def info_repo(clave, cfg, e):
    k = norm(clave)
    for c, info in cfg["repos"].items():
        if norm(c) == k:
            return info
        st = e["repos"].get(norm(c), {})
        if st.get("nombre") and norm("github.com:" + st["nombre"]) == k:
            return info              # repo renombrado (LightningMods/etaHEN -> etaHEN/etaHEN)
    d = e["descubiertos"].get(k)
    if d:
        return {"tier": "C", "alias": d.get("alias", []), "nucleo": False}
    return {"tier": "C", "alias": [], "nucleo": False}


def anotar(n, cfg, e):
    info = info_repo(n.get("repo_cfg") or n.get("repo"), cfg, e)
    n["tier"] = info.get("tier", "C")
    n["nucleo"] = info.get("nucleo", False)
    n["alias"] = list(dict.fromkeys((n.get("alias") or []) + info.get("alias", [])))
    return n


# ------------------------------------------------------------ recogida
def recoger(e, cfg, conf, ctx):
    ronda = e["ronda"]
    out = []

    def tarea(nombre, f, *a):
        if ctx.get("limite_github") and nombre.startswith("gh"):
            return
        try:
            out.extend(f(*a) or [])
        except Exception as ex:
            print(f"[{nombre}] error: {ex}")
            traceback.print_exc()
            estado.salud(e, nombre.split(":")[0], False, str(ex))

    if ronda % 3 == 1 or ctx.get("todo"):
        tarea("sony", fuentes.sony, e, conf, ctx)
    if ronda % 6 == 1 or ctx.get("todo"):
        tarea("listas", fuentes.listas, e, cfg, ctx)
    # Repos configurados: A cada ronda, B cada 3
    for clave, info in cfg["repos"].items():
        if norm(clave) in cfg["excluir"]:
            continue
        if info["tier"] == "B" and ronda % 3 != 0 and not ctx.get("todo"):
            continue
        tarea("gh:rel", fuentes.releases, e, clave, info, ctx)
        if info.get("commits"):
            tarea("gh:com", fuentes.commits, e, clave, info, ctx)
        if info.get("readme") and (ronda % 6 == 2 or ctx.get("todo")):
            tarea("gh:readme", fuentes.readme, e, clave, info, ctx)
    # Descubiertos (tier C): por turnos, unos 15 por ronda
    configurados = {norm(c) for c in cfg["repos"]}
    nombres_conf = {norm("github.com:" + e["repos"].get(norm(c), {}).get("nombre", ""))
                    for c in cfg["repos"]}
    desc = sorted(k for k in e["descubiertos"]
                  if k not in configurados and k not in nombres_conf and k not in cfg["excluir"])
    if desc:
        trozo = 15
        ini = (ronda * trozo) % len(desc)
        lote = (desc + desc)[ini:ini + min(trozo, len(desc))] if not ctx.get("todo") else desc
        for k in lote:
            d = e["descubiertos"][k]
            tarea("gh:rel", fuentes.releases, e, d["clave"], d, ctx)
    if ronda % 6 == 1 or ctx.get("todo"):
        tarea("gh:busqueda", fuentes.busqueda, e, cfg, conf, ctx)
    if ronda % 2 == 1 or ctx.get("todo"):
        tarea("prensa", fuentes.prensa, e, cfg, ctx)
    return [anotar(n, cfg, e) for n in out]


# ------------------------------------------------------------ evaluación
def enviados_recientes(e, horas=72):
    lim = time.time() - horas * 3600
    return [x for x in e["enviados"] if x.get("ts", 0) >= lim]


def repos_equivalentes(clave, e, cfg):
    """La clave del repo y sus nombres alternativos (renombrados)."""
    k = norm(clave)
    eq = {k}
    for c in cfg["repos"]:
        st = e["repos"].get(norm(c), {})
        canon = norm("github.com:" + st["nombre"]) if st.get("nombre") else None
        if k in (norm(c), canon):
            eq |= {norm(c)} | ({canon} if canon else set())
    return eq


def evaluar(novedades, e, cfg, conf, ctx):
    """Devuelve (para_enviar_ya, para_resumen)."""
    ahora = time.time()
    # Una novedad por repo y ronda: la de más nota (las demás aportan razones)
    listas = []
    for n in novedades:
        if norm(n.get("repo")) in cfg["excluir"] or norm(n.get("repo_cfg")) in cfg["excluir"]:
            continue
        if not reglas.es_ps5(n) or reglas.es_de_juegos(n):
            continue
        ok, txt = reglas.disponible(n)
        if not ok:
            print(f"[filtro] no disponible ({txt}): {n.get('titulo')}")
            continue
        n["estado_txt"] = txt
        if reglas.demasiado_vieja(n, conf, ahora):
            continue
        n["clave"] = clave_novedad(n)
        if n["clave"] in e["vistos"]:
            continue
        reglas.puntuar(n, e, conf)
        n["nivel"] = reglas.nivel(n["nota"], conf)
        listas.append(n)

    # Artículos que hablan de algo detectado en esta misma ronda: solo confirman
    propios = {}
    for n in listas:
        if n["tipo"] != "articulo":
            for k in repos_equivalentes(n["repo"], e, cfg) | repos_equivalentes(n["repo_cfg"], e, cfg):
                propios.setdefault(k, n)
    restantes = []
    for n in listas:
        if n["tipo"] == "articulo" and n.get("repos_enlazados"):
            dueño = next((propios[norm(c)] for c in n["repos_enlazados"] if norm(c) in propios), None)
            if dueño:
                medio = n.get("medio", "prensa")
                dueño.setdefault("confirmado_por", [])
                if medio not in dueño["confirmado_por"]:
                    dueño["confirmado_por"].append(medio)
                e["vistos"][n["clave"]] = int(ahora)
                continue
        restantes.append(n)
    listas = restantes

    por_repo = {}
    for n in sorted(listas, key=lambda x: -x["nota"]):
        k = norm(n["repo"]) if n["tipo"] not in ("articulo", "firmware_sony") else n["clave"]
        if k in por_repo:
            principal = por_repo[k]
            principal["razones"] = principal["razones"] + [f"además: {n.get('titulo', '')[:60]}"]
            principal.setdefault("absorbidas", []).append(n["clave"])
            e["vistos"][n["clave"]] = int(ahora)
            continue
        por_repo[k] = n

    ya, resumen = [], []
    llamadas_ia = 0
    for n in por_repo.values():
        e["vistos"][n["clave"]] = int(ahora)
        for c in n.get("absorbidas", []):
            e["vistos"][c] = int(ahora)
        # --- prensa: ¿lo hemos contado ya?
        if n["tipo"] == "articulo" and n.get("repos_enlazados"):
            if _confirma(n, e, cfg):
                continue
            for c in n["repos_enlazados"]:
                k = norm(c)
                if k not in e["descubiertos"] and not info_repo(c, cfg, e).get("nombre") \
                        and k not in cfg["excluir"]:
                    e["descubiertos"][k] = {"clave": c, "tier": "C", "alias": [c.split("/")[-1]],
                                            "origen": "prensa"}
        # --- enfriamiento para commits/readme del mismo repo
        if n["tipo"] in ("commit", "readme") and n["nivel"] != "fuego":
            ult = e["repos"].get(norm(n["repo_cfg"]), {}).get("ult_aviso", 0)
            if ahora - ult < 6 * 3600:
                continue
        if n["nivel"] == "descartado":
            continue
        # --- IA (solo lo que ya promete)
        if n["nota"] >= 6 and llamadas_ia < 6:
            llamadas_ia += 1
            res = ia.enriquecer(n, e, conf, ctx)
            if not ia.combinar(n, res):
                print(f"[ia] descartado por la IA (no disponible o juegos): {n.get('titulo')}")
                continue
            n["nivel"] = reglas.nivel(n["nota"], conf)
            if n["nivel"] == "descartado":
                continue
        else:
            n["sin_ia"] = True
        _actualizar_fw(n, e, cfg, conf)
        (ya if n["nivel"] == "fuego" else resumen).append(n)
    return ya, resumen


def _confirma(n, e, cfg):
    """Si el artículo habla de algo ya avisado o pendiente, solo lo confirma."""
    enlazados = set()
    for c in n["repos_enlazados"]:
        enlazados |= repos_equivalentes(c, e, cfg)
    medio = n.get("medio", "prensa")
    for p in e["pendientes"] + e["cola"]:
        if norm(p.get("repo")) in enlazados:
            p.setdefault("confirmado_por", [])
            if medio not in p["confirmado_por"]:
                p["confirmado_por"].append(medio)
                p["nota"] = min(10, p["nota"] + 1)
            return True
    for x in enviados_recientes(e):
        if norm(x.get("repo")) in enlazados:
            return True
    return False


def _actualizar_fw(n, e, cfg, conf):
    fwmax = max(n.get("fw") or [0])
    if not fwmax or n["tipo"] not in ("release", "commit", "readme", "repo_nuevo"):
        return
    st = e["repos"].setdefault(norm(n["repo_cfg"]), {})
    st["fw_max"] = max(st.get("fw_max", 0), fwmax)
    fiable = n.get("tier") == "A" or (n["tipo"] == "repo_nuevo" and n.get("estrellas", 0) >= 50)
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
        clave = rec[0]
        e["cola"] = [x for x in e["cola"] if x.get("clave") != clave]
        _apuntar_enviado(e, {"clave": clave, "repo": ""}, rec[1])
    silencio = en_silencio(conf)
    for n in list(e["cola"]):
        if silencio and not conf.get("fuego_rompe_silencio", True):
            break
        mid = telegram.enviar(e, formato.aviso(n), telegram.boton(n.get("url") or "https://github.com"),
                              guardar=guardar, clave=n["clave"])
        if not mid and telegram._token():
            break                     # se reintenta en la próxima ronda
        e["cola"] = [x for x in e["cola"] if x.get("clave") != n["clave"]]
        if mid:
            _apuntar_enviado(e, n, mid)
        guardar()


def _apuntar_enviado(e, n, mid):
    e["enviados"].append({"clave": n.get("clave"), "repo": n.get("repo", ""), "msg_id": mid,
                          "ts": int(time.time())})
    st = e["repos"].get(norm(n.get("repo_cfg") or n.get("repo") or ""))
    if st is not None:
        st["ult_aviso"] = int(time.time())


def toca_resumen(e, conf, forzar=False):
    hoy = ahora_local(conf)
    dia = hoy.strftime("%Y-%m-%d")
    if forzar:
        return True
    if e["resumen"].get("ultimo_dia") == dia:
        return False
    return hoy.hour * 60 + hoy.minute >= _hm(conf["hora_resumen"])


def enviar_resumen(e, conf, ruta, forzar=False):
    if not toca_resumen(e, conf, forzar):
        return
    dia = ahora_local(conf).strftime("%Y-%m-%d")
    items = sorted(e["pendientes"], key=lambda x: (-x.get("nota", 0), -x.get("ts", 0)))
    if not items:
        e["resumen"]["ultimo_dia"] = dia
        return
    extra = max(0, len(items) - 15)
    texto = formato.resumen(items[:15], conf["zona"], extra)
    for trozo in formato.trocear(texto):
        mid = telegram.enviar(e, trozo, None, guardar=guardar_fn(e, ruta))
        if mid is None and telegram._token():
            return                    # se reintenta en la próxima ronda
    e["pendientes"] = []
    e["resumen"]["ultimo_dia"] = dia
    estado.guardar(e, ruta)


# ------------------------------------------------------------ modos
def modo_ronda(e, cfg, conf, ruta):
    ctx = {"techo": e["fw"].get("sony_max", 1422) + 100}
    e["ronda"] = e.get("ronda", 0) + 1
    for k, v in conf["fw_semilla"].items():
        e["fw"].setdefault(k, v)
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
    if ya or resumen or e["ronda"] % 3 == 0:
        try:
            competencia.refrescar(cfg, e)
        except Exception as ex:
            print(f"[youtube] {ex}")
    for n in ya + resumen:
        n["competencia"] = competencia.linea(n, e)
    for n in ya:
        e["cola"].append(compacto(n))
    for n in resumen:
        e["pendientes"].append(compacto(n))
    for n in ya + resumen:
        print(f"[{n['nivel']}] {n['nota']}/10 {n['titulo']} ({n['repo']}) {n.get('razones')}")
    estado.guardar(e, ruta)
    enviar_cola(e, conf, ruta)
    enviar_resumen(e, conf, ruta)
    estado.guardar(e, ruta)


def modo_prueba(e, cfg, conf, ruta):
    n = {"tipo": "release", "repo": "github.com:etaHEN/etaHEN", "repo_cfg": "github.com:etaHEN/etaHEN",
         "titulo": "etaHEN 2.6b (EJEMPLO)", "tag": "2.6b", "tag_anterior": "2.5b",
         "url": "https://github.com/etaHEN/etaHEN/releases", "ts": int(time.time()),
         "texto": "- Added support for firmware 13.60\n- Integrated kstuff\n- Fixed crash",
         "prerelease": False, "n_assets": 1, "primera": False}
    anotar(n, cfg, e)
    n["estado_txt"] = "release publicada"
    reglas.puntuar(n, {"fw": dict(conf["fw_semilla"]), "repos": {}}, conf)
    n["nivel"] = reglas.nivel(n["nota"], conf)
    res = ia.enriquecer(n, e, conf, {})
    ia.combinar(n, res)
    try:
        competencia.refrescar(cfg, e)
    except Exception as ex:
        print(f"[youtube] {ex}")
    n["competencia"] = competencia.linea(n, e)
    mid = telegram.enviar(e, formato.aviso(n, prueba=True), telegram.boton(n["url"]),
                          guardar=guardar_fn(e, ruta))
    print("enviado" if mid else "NO enviado", "| IA:", "sí" if res else f"no ({e['ia'].get('ultimo_error')})")
    estado.guardar(e, ruta)


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
    vs = []
    for url in conf["sony"]:
        r = red.pedir(url, timeout=20)
        from .firmware import sony as fws, fmt
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
        res = ia.enriquecer(n, {"ia": {"fallos": 0}, "ronda": 1}, conf, {})
        p("super-ia", bool(res), "" if res else "no responde (los avisos saldrán sin IA)")
    else:
        p("super-ia", False, "falta el secreto SUPER_IA_URL")
    ok, user, st = telegram.soy_admin_o_existe()
    p("Bot de Telegram", ok, f"@{user}" if ok else f"HTTP {st}")
    lin += ["", f"Ronda: {e.get('ronda', 0)} · Repos vigilados: {len(cfg['repos']) + len(e['descubiertos'])}"
                f" · Pendientes para el resumen: {len(e['pendientes'])}"]
    texto = "\n".join(lin)
    print(texto)
    telegram.enviar(e, texto, None, guardar=guardar_fn(e, ruta))
    estado.guardar(e, ruta)


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
        enviar_resumen(e, conf, a.estado, forzar=True)
        estado.guardar(e, a.estado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
