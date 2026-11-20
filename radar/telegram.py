"""Envío a Telegram sin duplicados.

Si Telegram tarda y el envío se da por fallido aunque sí llegó, antes de
reintentar se comprueba si ya está en el grupo: se intenta editar los botones de
los mensajes posteriores al último conocido con los mismos botones; Telegram
responde «message is not modified» cuando encuentra el nuestro.
"""
import json
import os
import re
import time

from . import red

API = "https://api.telegram.org/bot{}/{}"


def _token():
    return os.environ.get("TELEGRAM_TOKEN", "")


def _chat():
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def llamar(metodo, datos, timeout=30):
    r = red.pedir(API.format(_token(), metodo), datos=datos, timeout=timeout, metodo="POST")
    try:
        j = r.json() if r.cuerpo else {}
    except ValueError:
        j = {}
    return r, j


def _no_llego(r):
    """Error al conectar: seguro que no se envió."""
    return r.status == 0 and r.error.startswith("URLError")


def boton(url, texto="🔗 Abrir"):
    return {"inline_keyboard": [[{"text": texto, "url": url}]]}


def ya_llego(e, markup, limite=60):
    """Busca nuestro mensaje tras el último conocido. Devuelve su id o None."""
    ultimo = int(e["tg"].get("ultimo_msg_id") or 0)
    if not ultimo:
        return None
    seguidos_no_existe = seguidos_sin_red = 0
    for mid in range(ultimo + 1, ultimo + 1 + limite):
        r, j = llamar("editMessageReplyMarkup",
                      {"chat_id": _chat(), "message_id": mid, "reply_markup": markup}, timeout=15)
        desc = (j.get("description") or "").lower()
        if r.status == 0:
            seguidos_sin_red += 1
            if seguidos_sin_red >= 3:
                return None      # Telegram colgado: no agotar el tiempo del job
            continue
        seguidos_sin_red = 0
        if "not modified" in desc:
            return mid
        if j.get("ok"):
            # Era otro mensaje del bot sin apuntar (prueba o diagnóstico, que no llevan
            # botones): se le devuelve tal cual estaba y se sigue buscando
            llamar("editMessageReplyMarkup", {"chat_id": _chat(), "message_id": mid,
                                              "reply_markup": {"inline_keyboard": []}}, timeout=15)
            seguidos_no_existe = 0
            continue
        if "not found" in desc:
            seguidos_no_existe += 1
            if seguidos_no_existe >= 3:
                return None
        else:
            seguidos_no_existe = 0   # «can't be edited»: mensaje de otra persona
    return None


PERMANENTE = -1
# Errores del propio mensaje (no del grupo): reintentar no sirve de nada
RE_MENSAJE_MALO = re.compile(r"can't parse|entities|message is too long|text must be non-empty|"
                             r"button|reply markup|url|wrong http url|unsupported url", re.I)


def enviar(e, texto, markup=None, intentos=4, guardar=None, clave=None, registrar=True):
    """Devuelve el message_id, None (fallo pasajero: reintentar más tarde) o
    PERMANENTE (Telegram lo rechaza siempre: no insistir)."""
    if not _token() or not _chat():
        print("[tg] faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID; mensaje:\n" + texto)
        return None
    datos = {"chat_id": _chat(), "text": texto, "parse_mode": "HTML",
             "disable_web_page_preview": True}
    if markup:
        datos["reply_markup"] = markup
    if registrar and markup:
        e["tg"]["en_curso"] = {"markup": markup, "clave": clave, "ts": int(time.time())}
        if guardar:
            guardar()
    quitado_boton = False
    for intento in range(intentos):
        r, j = llamar("sendMessage", datos)
        if j.get("ok"):
            return _hecho(e, j["result"]["message_id"], guardar, registrar)
        if r.status == 429:
            espera = min(60, int((j.get("parameters") or {}).get("retry_after", 5)))
            time.sleep(espera + 1)
            continue
        desc = (j.get("description") or "").lower()
        if r.status == 400 and "parse" in desc and datos.get("parse_mode"):
            datos["text"] = re.sub(r"<[^>]+>", "", texto)
            datos.pop("parse_mode", None)
            continue
        if r.status == 400 and ("button" in desc or "reply markup" in desc) and not quitado_boton:
            datos.pop("reply_markup", None)
            quitado_boton = True
            continue
        if _no_llego(r) or r.status >= 500:
            time.sleep(5 * (intento + 1))
            continue
        if r.status == 0:                 # pudo llegar sin respuesta
            if markup:
                mid = ya_llego(e, markup)
                if mid:
                    print(f"[tg] ya había llegado (mensaje {mid}); no se repite")
                    return _hecho(e, mid, guardar, registrar)
                time.sleep(5)
                continue
            print("[tg] sin respuesta y sin forma de comprobarlo; no se reintenta para no duplicar")
            return None
        print(f"[tg] error {r.status}: {j.get('description') or r.error}")
        if r.status == 400 and RE_MENSAJE_MALO.search(desc):
            if registrar:
                e["tg"]["en_curso"] = None
            return PERMANENTE      # el mensaje en sí no vale: no bloquear la cola
        if r.status in (400, 403) and ("chat" in desc or "rights" in desc or "upgraded" in desc):
            print("[tg] ⚠️ problema con el grupo (¿cambió el chat_id o el bot no está?): "
                  "revisa el secreto TELEGRAM_CHAT_ID. Se reintenta en la próxima ronda.")
        return None                # token/chat mal o caído: reintentar más tarde
    return None


def _hecho(e, mid, guardar, registrar=True):
    e["tg"]["ultimo_msg_id"] = max(int(e["tg"].get("ultimo_msg_id") or 0), int(mid))
    if registrar:
        e["tg"]["en_curso"] = None
    if guardar:
        guardar()
    return mid


def recuperar_en_curso(e):
    """Si la ronda anterior murió enviando, mira si llegó.

    Devuelve (clave, message_id) si llegó, (clave, None) si no, o None si no había nada."""
    ec = e["tg"].get("en_curso")
    if not ec:
        return None
    mid = ya_llego(e, ec.get("markup")) if ec.get("markup") else None
    e["tg"]["en_curso"] = None
    if mid:
        e["tg"]["ultimo_msg_id"] = max(int(e["tg"].get("ultimo_msg_id") or 0), mid)
    return ec.get("clave"), mid


def soy_admin_o_existe():
    r, j = llamar("getMe", {})
    return j.get("ok"), (j.get("result") or {}).get("username", ""), r.status


def json_corto(x):
    return json.dumps(x, ensure_ascii=False)[:300]
