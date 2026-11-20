"""Estado persistente (JSON compacto). Es público: nunca guardar secretos ni chat_id."""
import json
import os
import time

BASE = {
    "v": 1, "ronda": 0, "sembrado_global": False,
    "fw": {},                       # sony_max, explotable_max (enteros: 13.60 -> 1360)
    "repos": {},                    # "host:owner/repo" -> datos por repo
    "descubiertos": {},             # repos que vienen de listas o de wololo
    "listas": {},                   # url -> {"sembrada": bool, "repos": [...]}
    "busqueda": {},                 # "owner/repo" -> {"estrellas", "umbrales": []}
    "vistos": {},                   # clave -> ts
    "pendientes": [],               # novedades para el resumen diario
    "cola": [],                     # 🔥 listos para enviar (sobreviven a una ronda cortada)
    "enviados": [],                 # [{"clave","msg_id","ts","repo"}]
    "tg": {"ultimo_msg_id": 0, "en_curso": None},
    "resumen": {"ultimo_dia": ""},
    "yt": {"canales": {}, "videos": []},
    "feeds": {},                    # wololo, sony...
    "ia": {"fallos": 0},
    "salud": {},
    "renombres": {},                # enlace viejo -> nombre actual del repo                    # fuente -> {"ok": ts, "fallo": ts, "error": str}
}


def cargar(ruta):
    e = json.loads(json.dumps(BASE))
    if ruta and os.path.exists(ruta):
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
            e.update(datos)
            for k, v in BASE.items():          # claves nuevas en versiones futuras
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        e[k].setdefault(kk, vv)
        except Exception as ex:
            print(f"[estado] no se pudo leer ({ex}); empiezo de cero")
    return e


def guardar(e, ruta):
    if not ruta:
        return
    podar(e)
    d = os.path.dirname(ruta)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, ruta)


def podar(e, ahora=None):
    ahora = ahora or time.time()
    v = {k: t for k, t in e["vistos"].items() if ahora - t < 90 * 86400}
    if len(v) > 1500:
        v = dict(sorted(v.items(), key=lambda kv: kv[1])[-1500:])
    e["vistos"] = v
    e["pendientes"] = e["pendientes"][-60:]
    e["cola"] = e["cola"][-20:]
    e["enviados"] = e["enviados"][-200:]
    e["yt"]["videos"] = [x for x in e["yt"]["videos"] if ahora - x.get("ts", 0) < 14 * 86400][-300:]


def salud(e, fuente, ok, error=""):
    s = e["salud"].setdefault(fuente, {})
    if ok:
        s["ok"] = int(time.time())
        s.pop("error", None)
    else:
        s["fallo"] = int(time.time())
        s["error"] = (error or "")[:160]
