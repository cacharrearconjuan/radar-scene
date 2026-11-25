"""Textos de los avisos (HTML de Telegram)."""
import re as _re
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

from . import firmware

ICONO = {"fuego": "🔥", "interesante": "⭐", "menor": "📋"}
ANGULO = {
    "firmware_sony": "¿Actualizo o no? Qué pierdes si actualizas a este firmware",
    "repo_nuevo": "Ha aparecido algo nuevo y está pegando fuerte: qué es y a quién le sirve",
    "release": "Qué trae la versión nueva y cómo instalarla",
    "commit": "Cambios recientes en el código: qué significan para tu consola",
    "readme": "Amplía los firmwares compatibles: ¿entra la tuya?",
    "lista_nueva": "Payload nuevo en Payload Manager: qué hace y cómo cargarlo",
    "articulo": "Novedad ya publicada: qué es y cómo probarla",
}




def titulo_bonito(n):
    """«v0.8.4» -> «ps5-webkit-autoloader v0.8.4»: el título solo con la versión no dice nada."""
    t = str(n.get("titulo") or "").strip()
    if not t or _re.fullmatch(r"(?i)v?\d[\w.\-+]*", t) or t == n.get("tag"):
        nombre = (n.get("alias") or [None])[0] or nombre_repo(n).split("/")[-1]
        return f"{nombre} {n.get('tag') or t}".strip()
    return t


def corta(s, n=240):
    s = str(s or "")
    if len(s) <= n:
        return s
    c = s[:n]
    p = c.rfind(". ")
    return c[:p + 1] if p > n * 0.5 else c[:c.rfind(" ")].rstrip(",;:") + "…"


def nombre_repo(n):
    r = n.get("repo") or ""
    if r == "sony":
        return "Sony"
    return r.split(":", 1)[-1]


def aviso(n, prueba=False):
    ic = ICONO.get(n.get("nivel"), "📋")
    tit = escape(titulo_bonito(n))
    rep = escape(nombre_repo(n))
    cab = f"{ic} <b>{n['nota']}/10 · {tit}</b>"
    if rep and rep.lower() not in tit.lower() and n["tipo"] != "firmware_sony":
        cab += f"\n<i>{rep}</i>"
    if n.get("tag") and n["tipo"] == "release" and escape(n["tag"]) not in tit:
        cab += f" <code>{escape(n['tag'])}</code>"
    lin = [("🧪 <b>PRUEBA</b>\n" if prueba else "") + cab, ""]
    lin.append(f"📌 Estado: {escape(n.get('estado_txt', ''))}"
               + (" · ⚠️ sin verificar" if n.get("sin_verificar") else ""))
    fw = firmware.Fw(n.get("fw") or [])
    if n["tipo"] == "firmware_sony":
        lin.append(f"🎯 Firmware: {firmware.fmt(n['fw_nuevo'])} (antes {firmware.fmt(n['fw_previo'])})")
    elif fw.lista:
        lin.append(f"🎯 Firmware: {fw.texto()}")
    elif n.get("fw_ia"):
        lin.append(f"🎯 Firmware: {escape(n['fw_ia'])} (según la IA)")
    if n.get("confirmado_por"):
        lin.append("📰 Lo confirma: " + escape(", ".join(n["confirmado_por"])))
    lin.append(f"📺 Competencia: {escape(n.get('competencia', 'sin datos'))}")
    if n.get("resumen"):
        lin += ["", f"📝 <b>Qué es:</b> {escape(n['resumen'])}"]
    if n.get("para_que"):
        lin.append(f"🛠️ <b>Para qué sirve:</b> {escape(n['para_que'])}")
    if n.get("idea_titulo"):
        lin += ["", f"🎬 <b>Idea de vídeo:</b> «{escape(n['idea_titulo'])}»"]
        if n.get("angulo"):
            lin.append(f"🎙️ Gancho: {escape(n['angulo'])}")
    else:
        angulo = n.get("angulo") or ANGULO.get(n["tipo"], "")
        lin.append(f"💡 Ángulo: {escape(angulo)}")
    if n.get("pasos"):
        lin += ["", "👣 <b>Paso a paso</b> <i>(sacado del README por IA: compruébalo antes de grabar)</i>"]
        lin += [f"{i}. {escape(p)}" for i, p in enumerate(n["pasos"], 1)]
    if n.get("razones"):
        lin.append("<i>Por qué: " + escape("; ".join(n["razones"][:3])) + "</i>")
    if n.get("sin_ia"):
        lin.append(f"<i>(sin IA: {escape(n['ia_motivo'])})</i>" if n.get("ia_motivo") else "<i>(sin IA)</i>")
    return "\n".join(lin)


def _fw_texto(x):
    fw = firmware.Fw(x.get("fw") or [])
    if fw.lista:
        return fw.texto()
    return x.get("fw_ia") or ""


def resumen(items, zona, extra=0, repetido=False, tarde=False, desde=""):
    hoy = datetime.now(ZoneInfo(zona)).strftime("%d/%m")
    cab = "Actualización de la tarde" if tarde else "Resumen de la scene"
    lin = [f"🗞️ <b>{cab} · {hoy}</b>" + (" <i>(repetido, con la IA ajustada)</i>" if repetido else "")]
    if tarde and desde:
        lin.append(f"<i>Lo nuevo desde el resumen de las {escape(desde)}</i>")
    for niv, titulo in (("interesante", "⭐ Interesante"), ("menor", "📋 Menor")):
        grupo = [x for x in items if x.get("nivel") == niv]
        if not grupo:
            continue
        while lin and lin[-1] == "":
            lin.pop()
        lin += ["", f"<b>{titulo}</b>"]
        for x in grupo:
            t = escape(titulo_bonito(x))
            r = escape(nombre_repo(x))
            linea = f"• <a href=\"{escape(x.get('url') or '', quote=True)}\">{t}</a>"
            if r and r.split("/")[-1].lower() not in t.lower() and r.lower() not in t.lower():
                linea += f" — <i>{r}</i>"
            linea += f" ({x['nota']}/10)"
            if len(x.get("versiones") or []) > 1:
                linea += f" · {len(x['versiones'])} versiones: {escape(', '.join(x['versiones'][-4:]))}"
            if niv == "interesante":
                linea += _detalle(x)
            else:
                if x.get("competencia") and x["competencia"].startswith("ya lo sacó"):
                    linea += " · 📺 " + escape(x["competencia"])
                texto = x.get("frase") or x.get("para_que") or x.get("resumen")
                if texto:
                    linea += f"\n  {escape(corta(texto, 240))}"
            lin.append(linea)
            if niv == "interesante":
                lin.append("")                 # aire entre los ⭐, que ahora son más largos
    while lin and lin[-1] == "":
        lin.pop()
    sin = [x for x in items if x.get("sin_ia")]
    if sin and len(sin) == len(items):
        motivo = next((x.get("ia_motivo") for x in sin if x.get("ia_motivo")), "")
        lin += ["", "⚠️ <i>Todo ha salido sin IA" + (f": {escape(motivo)}" if motivo else "") + "</i>"]
    if extra:
        lin += ["", f"… y {extra} más."]
    return "\n".join(lin)


def _detalle(x):
    """Bloque largo de un ⭐: idea de vídeo, qué es, para qué sirve, firmware, competencia y pasos."""
    out = []
    if x.get("idea_titulo"):
        out.append(f"🎬 <b>Vídeo:</b> «{escape(x['idea_titulo'])}»")
        if x.get("angulo"):
            out.append(f"🎙️ Gancho: {escape(corta(x['angulo'], 200))}")
    que = x.get("resumen") or x.get("frase")
    if que:
        out.append(f"📝 <b>Qué es:</b> {escape(corta(que, 400))}")
    if x.get("para_que") and x.get("para_que") != que:
        out.append(f"🛠️ <b>Para qué sirve:</b> {escape(corta(x['para_que'], 400))}")
    fw = _fw_texto(x)
    if fw:
        out.append(f"🎯 Firmware: {escape(fw)}")
    if x.get("estado_txt"):
        out.append(f"📌 {escape(x['estado_txt'])}" + (" · ⚠️ sin verificar" if x.get("sin_verificar") else ""))
    if x.get("competencia"):
        out.append(f"📺 Competencia: {escape(x['competencia'])}")
    pasos = [p for p in (x.get("pasos") or []) if p][:4]
    if pasos:
        out.append("👣 <b>Pasos</b> <i>(según el README, compruébalo)</i>:")
        out += [f"   {i}. {escape(corta(p, 160))}" for i, p in enumerate(pasos, 1)]
    if x.get("sin_ia"):
        out.append("<i>(sin IA)</i>")
    return "".join("\n  " + o for o in out)


def trocear(texto, limite=3500):
    if len(texto) <= limite:
        return [texto]
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite and actual:
            partes.append(actual)
            actual = ""
        actual += ("\n" if actual else "") + linea
    if actual:
        partes.append(actual)
    return partes
