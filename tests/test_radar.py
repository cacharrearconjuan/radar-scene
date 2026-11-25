import copy
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import __main__ as radar, estado, firmware, red, reglas, telegram  # noqa: E402
from tests.simulador import Mundo, iso, leer  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rel(rid, tag, body, hace_h=1, pre=False, draft=False, repo="etaHEN/etaHEN"):
    return {"id": rid, "tag_name": tag, "name": f"{repo.split('/')[1]} {tag}", "draft": draft,
            "prerelease": pre, "published_at": iso(hace_h),
            "html_url": f"https://github.com/{repo}/releases/tag/{tag}", "body": body,
            "assets": [{"name": "x.elf"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ruta = os.path.join(self.tmp, "estado.json")
        self.m = Mundo()
        red.FALSO = self.m
        self.env = mock.patch.dict(os.environ, {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-100",
                                                "SUPER_IA_URL": "https://ia.example/v1",
                                                "SUPER_IA_KEY": "k", "RADAR_TODO": "1"})
        self.env.start()
        self.sleep = mock.patch("time.sleep", lambda s: None)
        self.sleep.start()
        # Antes de las 9: así el resumen no salta salvo que la prueba lo pida
        self.hora = mock.patch.object(radar, "toca_resumen", lambda e, c, forzar=False: forzar)
        self.hora.start()
        # Mundo inicial (lo que ya existía antes de arrancar el bot)
        self.m.releases = {
            "etaHEN/etaHEN": [rel(1, "2.5b", "bug fixes", 24 * 40)],
            "itsPLK/ps5-payload-manager": [rel(2, "v0.6.3", "x", 24 * 30, repo="itsPLK/ps5-payload-manager")],
            "EchoStretch/kstuff": [rel(3, "v1.6.7", "x", 24 * 60, repo="EchoStretch/kstuff")],
            "BrinooTk/PS5X360": [rel(4, "v1.0.2", "x", 24 * 5, repo="BrinooTk/PS5X360")],
        }
        self.m.commits = {"ntfargo/Relapse-Exploit": [
            {"sha": "c0ffee0", "html_url": "u", "commit": {"message": "init", "author": {"date": iso(500)}}}]}
        self.m.readme = {"ntfargo/Relapse-Exploit": "Supported firmware: 7.00 through 13.60."}
        self.m.sony = self.m.sony  # 14.22

    def tearDown(self):
        red.FALSO = None
        self.env.stop()
        self.sleep.stop()
        self.hora.stop()

    def ronda(self):
        radar.main(["ronda", "--estado", self.ruta])
        return estado.cargar(self.ruta)


class TestFirmware(unittest.TestCase):
    def test_bateria(self):
        casos = {
            "Supported firmware: 7.00 through 13.60.": "7.00 – 13.60",
            "- Supports PS5 firmware 1.00-5.50": "1.00 – 5.50",
            "**Firmware 4.03 – 12.40** →": "4.03 – 12.40",
            "Poopsploit payload requires 12.00 or lower firmware PS5": "",   # «requires»: prudente
            "Removed support for firmware 14.00": "",
            "| PS5 14.00 | ❌ |": "",
            "IP 192.168.1.100": "",
            "kstuff-lite v1.11 released": "",
            "Experimental support for FW 14.00 and 14.10.": "14.00 – 14.10",
            "Y2JB 1.3 supported alongside 1.4": "",
            "version 01.000.030 PKG": "",
            "Supported firmware: 4.03 – 12.7": "4.03 – 12.70",
        }
        for t, esperado in casos.items():
            self.assertEqual(firmware.extraer(t, tag="v1.11", techo=1522).texto(), esperado, t)

    def test_sony(self):
        self.assertEqual(max(firmware.sony(leer("sony_es.html"))), 1422)


class TestReglas(unittest.TestCase):
    conf = radar.cargar_conf()

    def e(self):
        return {"fw": dict(self.conf["fw_semilla"]), "repos": {}}

    def nov(self, **kw):
        n = {"tipo": "release", "repo": "github.com:a/b", "repo_cfg": "github.com:a/b", "titulo": "x",
             "tag": "v1.1.0", "tag_anterior": "v1.0.0", "texto": "", "prerelease": False, "tier": "B",
             "nucleo": False, "primera": False}
        n.update(kw)
        return n

    def test_solo_traducciones(self):
        n = reglas.puntuar(self.nov(texto="- Translations: updated\n- New Languages: Polish"), self.e(), self.conf)
        self.assertEqual(reglas.nivel(n["nota"], self.conf), "descartado")

    def test_solo_fixes_es_menor(self):
        n = reglas.puntuar(self.nov(tag="v1.0.1", texto="- Fixed crash\n- Minor fixes"), self.e(), self.conf)
        self.assertEqual(reglas.nivel(n["nota"], self.conf), "menor")

    def test_firmware_nuevo_tier_a(self):
        n = reglas.puntuar(self.nov(tier="A", texto="Experimental support for FW 14.00 and 14.10."),
                           self.e(), self.conf)
        self.assertEqual(n["nota"], 10)

    def test_firmware_nuevo_tier_c_no_es_fuego(self):
        n = reglas.puntuar(self.nov(tier="C", texto="Works on firmware 4.03-14.22"), self.e(), self.conf)
        self.assertLess(n["nota"], 9)

    def test_teasers_fuera(self):
        for t in ["PS5: Gezine teases game dump on firmware 14.22, could lead to jailbreak",
                  "PS5 14.22 jailbreak coming soon?", "Developer shows PS5 Linux progress",
                  "PS5 kernel exploit announced for 14.10",
                  "PS5: kernel exploit for 14.40 to be released next week",
                  "PS5 14.30 exploit will not be released", "PS5 14.30 exploit: no public release available yet",
                  "Gezine releases video of 14.50 jailbreak", "PS5 14.22 kernel exploit writeup released, no code yet"]:
            ok, _ = reglas.disponible({"tipo": "articulo", "titulo": t, "repos_enlazados": ["github.com:a/b"]})
            self.assertFalse(ok, t)
        ok, _ = reglas.disponible({"tipo": "articulo", "titulo": "PS5: etaHEN 2.6b released",
                                   "repos_enlazados": ["github.com:a/b"], "descarga": True})
        self.assertTrue(ok)
        ok, _ = reglas.disponible({"tipo": "articulo", "titulo": "PS5: etaHEN 2.6b released",
                                   "repos_enlazados": ["github.com:a/b"], "descarga": False})
        self.assertFalse(ok, "enlazar un repo sin descarga reciente no basta")
        ok, _ = reglas.disponible({"tipo": "articulo", "titulo": "PS5: etaHEN 2.6b released", "repos_enlazados": []})
        self.assertFalse(ok)

    def test_juegos(self):
        self.assertTrue(reglas.es_de_juegos({"tipo": "articulo", "titulo": "Download PPSA01234 fpkg"}))
        self.assertFalse(reglas.es_de_juegos({"tipo": "release", "titulo": "fpkg support"}))


class TestRondas(Base):
    def test_flujo_completo(self):
        # Ronda 1: siembra silenciosa
        e = self.ronda()
        self.assertTrue(e["sembrado_global"])
        self.assertEqual(len(self.m.tg.del_bot), 1, "la primera ronda solo saluda")
        self.assertIn("activado", self.m.tg.del_bot[0]["texto"])
        self.assertGreater(len(e["descubiertos"]), 20, "autodescubre repos de las listas")

        # Pasa el tiempo: novedades reales
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "2.6b", "- Added support for firmware 13.60\n"
                                                    "- Integrated kstuff 1.7\n- New toolbox options", 1))
        self.m.releases["itsPLK/ps5-payload-manager"].append(
            rel(11, "v0.6.4", "- Translations: Updated localization files\n- New Languages: Polish", 1,
                repo="itsPLK/ps5-payload-manager"))
        self.m.releases["EchoStretch/kstuff"].append(
            rel(12, "v1.7.0-beta", "Experimental support for FW 14.00 and 14.10.", 0.5, pre=True,
                repo="EchoStretch/kstuff"))
        self.m.releases["BrinooTk/PS5X360"].append(rel(13, "v1.0.3", "- Minor fixes", 1, repo="BrinooTk/PS5X360"))
        self.m.releases["Gezine/Y2JB"] = [rel(14, "Y2JB-1.3.0", "draft 14.22", 1, draft=True, repo="Gezine/Y2JB")]
        self.m.commits["ntfargo/Relapse-Exploit"].insert(0, {
            "sha": "c0ffee1", "html_url": "https://github.com/ntfargo/Relapse-Exploit/commit/c0ffee1",
            "commit": {"message": "typo in README", "author": {"date": iso(0.5)}}})
        self.m.sony = self.m.sony.replace("26.07-14.22.00", "26.08-14.30.00")
        self.m.search = {"items": [
            {"full_name": "newdev/PS5-Kernel-Exploit-1422", "html_url": "https://github.com/newdev/PS5-Kernel-Exploit-1422",
             "description": "PS5 kernel exploit for firmware 14.22", "stargazers_count": 640, "fork": False,
             "size": 820, "created_at": iso(14)},
            {"full_name": "faker/ps5-jailbreak-1422-free", "html_url": "x",
             "description": "PS5 jailbreak 14.22 coming soon, star to get notified", "stargazers_count": 90,
             "fork": False, "size": 0, "created_at": iso(11)},
            {"full_name": "someone/ps5-theme", "html_url": "x", "description": "A PS5 inspired website theme",
             "stargazers_count": 70, "fork": False, "size": 50, "created_at": iso(30)}]}
        feed = leer("wololo_feed.xml").replace("Fri, 20 Nov 2026 09:10:00 +0000",
                                                time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()))
        feed = feed.replace("Developer LightningMods has released etaHEN 2.6b.",
                            "Developer LightningMods has released etaHEN 2.6b. https://github.com/etaHEN/etaHEN/releases")
        self.m.wololo = feed

        e = self.ronda()
        self.assertEqual(e["fw"]["explotable_max"], 1410, "los repos nuevos no mueven el listón")
        textos = [m["texto"] for m in self.m.tg.del_bot]
        juntos = "\n".join(textos)
        print("\n---- AVISOS ----\n" + "\n=====\n".join(textos))
        self.assertIn("14.30", juntos, "firmware nuevo de Sony")
        self.assertIn("etaHEN", juntos, "etaHEN 2.6b es de un repo clave")
        self.assertIn("kstuff", juntos, "kstuff con firmware 14.10")
        self.assertNotIn("PS5-Kernel-Exploit-1422", juntos, "640★ se pueden comprar: no es 🔥")
        nuevo = [p for p in e["pendientes"] if "PS5-Kernel-Exploit-1422" in p["titulo"]]
        self.assertTrue(nuevo and nuevo[0].get("sin_verificar"), "va al resumen, sin verificar")
        self.assertNotIn("faker", juntos)
        self.assertNotIn("ps5-theme", juntos)
        self.assertNotIn("teases", juntos)
        self.assertNotIn("Translations", juntos)
        self.assertNotIn("Y2JB-1.3.0", juntos, "los borradores no cuentan")
        self.assertNotIn("typo", juntos)
        self.assertIn("(sin IA", juntos, "la IA está caída y los avisos salen igual")
        # wololo confirma etaHEN en vez de repetir aviso
        self.assertEqual(sum("etaHEN 2.6b" in t for t in textos), 1)
        self.assertIn("Lo confirma: wololo.net", juntos)
        self.assertFalse(any("etaHEN" in p["titulo"] for p in e["pendientes"]), "el artículo no se repite")
        # PS5X360 (solo fixes) espera al resumen
        self.assertTrue(any("PS5X360" in p["titulo"] for p in e["pendientes"]))
        self.assertEqual(e["fw"]["sony_max"], 1430)
        self.assertGreaterEqual(e["fw"]["explotable_max"], 1410)

        # Ronda 3: nada nuevo -> ningún aviso más
        antes = len(self.m.tg.del_bot)
        e = self.ronda()
        self.assertEqual(len(self.m.tg.del_bot), antes, "sin repetidos")

        # Resumen diario
        radar.main(["resumen", "--estado", self.ruta])
        self.assertIn("Resumen de la scene", self.m.tg.del_bot[-1]["texto"])
        self.assertIn("PS5X360", self.m.tg.del_bot[-1]["texto"])
        e = estado.cargar(self.ruta)
        self.assertEqual(e["pendientes"], [])

    def test_telegram_tarda_pero_llega(self):
        self.ronda()
        self.m.tg.otro_usuario_escribe()
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.m.tg.fallos = ["timeout_llega"]
        self.ronda()
        self.assertEqual(sum("etaHEN" in m["texto"] for m in self.m.tg.del_bot), 1, "un solo mensaje")

    def test_telegram_timeout_sin_llegar_reintenta(self):
        self.ronda()
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.m.tg.fallos = ["timeout_no", "429"]
        self.ronda()
        self.assertEqual(sum("etaHEN" in m["texto"] for m in self.m.tg.del_bot), 1)

    def test_ronda_cortada_no_pierde_ni_duplica(self):
        self.ronda()
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.m.tg.fallos = ["conexion"] * 4      # Telegram caído toda la ronda
        e = self.ronda()
        self.assertEqual(sum("etaHEN" in m["texto"] for m in self.m.tg.del_bot), 0)
        self.assertEqual(len(e["cola"]), 1, "queda en cola")
        e = self.ronda()                          # Telegram vuelve
        self.assertEqual(sum("etaHEN" in m["texto"] for m in self.m.tg.del_bot), 1)
        self.assertEqual(e["cola"], [])

    def test_en_curso_huerfano(self):
        """La ronda anterior murió justo después de enviar: no se repite."""
        self.ronda()
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        orig = radar.enviar_cola
        radar.enviar_cola = lambda *a: None
        try:
            e = self.ronda()
        finally:
            radar.enviar_cola = orig
        n = e["cola"][0]
        # Simula: se envió (llegó) pero el proceso murió antes de apuntarlo
        e["tg"]["en_curso"] = {"clave": n["clave"], "markup": telegram.boton(n["url"]), "ts": 0}
        self.m.tg.mensajes[self.m.tg.siguiente] = {"texto": "etaHEN (ya enviado)",
                                                   "markup": telegram.boton(n["url"]), "de_bot": True}
        self.m.tg.siguiente += 1
        estado.guardar(e, self.ruta)
        e = self.ronda()
        self.assertEqual(sum("etaHEN" in m["texto"] for m in self.m.tg.del_bot), 1)
        self.assertEqual(e["cola"], [])

    def test_502_y_pasarela_caida_no_prueba_el_respaldo(self):
        self.ronda()
        self.m.ia = "502"
        self.m.releases["etaHEN/etaHEN"].append(rel(11, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.ronda()
        self.assertEqual(self.m.modelos_pedidos[-1:], ["groq"], "no se pierde tiempo con auto")
        self.assertIn("sin conexión con super-ia", self.m.tg.del_bot[-1]["texto"])

    def test_cortacircuitos_se_recupera_con_un_sondeo(self):
        e = self.ronda()
        e["ia"]["fallos"] = 5
        e["ronda"] = 6 * 100                          # la siguiente (601) es de las que antes no intentaban
        estado.guardar(e, self.ruta)
        self.m.ia = "ok"                              # la LXC ha vuelto
        self.m.releases["etaHEN/etaHEN"].append(rel(11, "3.0", "- Added support for firmware 13.60 HEN", 1))
        e = self.ronda()
        self.assertEqual(self.m.sondeos, 1)
        self.assertEqual(e["ia"]["fallos"], 0, "la IA vuelve en la misma ronda")

    def test_groq_sin_cupo_pasa_a_auto(self):
        self.ronda()
        self.m.ia = "groq_429"
        self.m.releases["etaHEN/etaHEN"].append(rel(11, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.ronda()
        t = self.m.tg.del_bot[-1]["texto"]
        self.assertIn("Resumen de prueba", t)
        self.assertEqual(self.m.modelos_pedidos[-2:], ["groq", "auto"])
        self.assertEqual(self.m.ia_datos[-1]["max_tokens"], 8000, "el respaldo conserva su margen")
        self.assertNotIn("reasoning_effort", self.m.ia_datos[-1])

    def test_ia_funciona_y_ia_dice_no(self):
        self.ronda()
        self.m.ia = "ok"
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.ronda()
        t = self.m.tg.del_bot[-1]["texto"]
        self.assertIn("Resumen de prueba", t)
        self.assertNotIn("(sin IA)", t)
        self.assertEqual(self.m.ultimo_modelo, "groq")
        self.assertEqual(self.m.ia_datos[-1]["max_tokens"], 2500, "Groq va con max_tokens bajo")
        self.assertEqual(self.m.ia_datos[-1]["reasoning_effort"], "low")
        self.assertIn("Para qué sirve", t)
        self.assertIn("&lt;b&gt;sin PC&lt;/b&gt;", t, "lo que dice la IA se escapa")
        self.assertIn("Idea de vídeo", t)
        self.assertIn("1. Abre Payload Manager", t)
        self.assertIn("2. Carga etaHEN.bin", t)
        self.assertNotIn("https://x.y", t, "sin enlaces de la IA")
        self.assertIn("<readme>", self.m.ultimo_prompt)
        # La IA no puede tumbar una release real, pero sí un artículo
        self.m.ia = "dice_no"
        self.m.wololo = leer("wololo_feed.xml").replace(
            "Fri, 20 Nov 2026 10:30:00 +0000", time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
        ).replace("is available now.", "is available now. https://github.com/someone/relapse-1410")
        e = self.ronda()
        self.assertFalse(any("Relapse exploit now available" in m["texto"] for m in self.m.tg.del_bot))
        self.assertFalse(any("Relapse exploit now available" in p["titulo"] for p in e["pendientes"]))


class TestSilencio(unittest.TestCase):
    def test_silencio(self):
        conf = radar.cargar_conf()
        from zoneinfo import ZoneInfo
        z = ZoneInfo("Europe/Madrid")
        self.assertTrue(radar.en_silencio(conf, datetime(2026, 11, 20, 3, 0, tzinfo=z)))
        self.assertFalse(radar.en_silencio(conf, datetime(2026, 11, 20, 9, 0, tzinfo=z)))
        self.assertTrue(radar.en_silencio(conf, datetime(2026, 11, 20, 0, 45, tzinfo=z)))


if __name__ == "__main__":
    unittest.main()


class TestRevision(Base):
    """Regresiones de la revisión independiente."""

    def avisos(self, palabra):
        return sum(palabra in m["texto"] for m in self.m.tg.del_bot)

    def test_url_ia_sin_https_no_bloquea(self):
        self.ronda()
        with mock.patch.dict(os.environ, {"SUPER_IA_URL": "super-ia.ejemplo.ts.net/v1"}):
            red.FALSO = None              # red real: la URL mala tiene que fallar sin excepción
            r = red.pedir("super-ia.ejemplo.ts.net/v1/chat/completions", datos={}, metodo="POST")
            red.FALSO = self.m
            self.assertEqual(r.status, 0)
            self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
            self.m.ia = "ok"
            orig = self.m.__class__.__call__

            def llamar(slf, url, cab, datos):
                if "chat/completions" in url:
                    return red.pedir.__wrapped__(url) if hasattr(red.pedir, "__wrapped__") else \
                        red.Resp(0, error="ValueError")
                return orig(slf, url, cab, datos)
            with mock.patch.object(self.m.__class__, "__call__", llamar):
                e = self.ronda()
        self.assertEqual(self.avisos("etaHEN"), 1)
        self.assertEqual(e["ronda"], 2)
        self.assertNotIn("ejemplo", json.dumps(e), "el hostname de la IA no acaba en el estado público")

    def test_lista_con_version_numerica_y_payload_nuevo(self):
        self.ronda()
        self.m.listas_extra = [{"name": "NewKex", "version": 1.2, "source": "https://github.com/dev/newkex/releases",
                                "description": "PS5 kernel exploit loader"}]
        self.m.releases["dev/newkex"] = [rel(50, "v1.0", "- Kernel exploit for firmware 13.60", 2, repo="dev/newkex")]
        e = self.ronda()
        todo = "\n".join(m["texto"] for m in self.m.tg.del_bot) + json.dumps(e["pendientes"])
        self.assertIn("newkex", todo.lower())
        self.assertIn("Payload Manager", todo)

    def test_negacion_no_da_fuego(self):
        self.ronda()
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "2.5c", "- Fixed crash\n- Firmware 14.00 and above are "
                                                    "NOT supported yet", 1))
        e = self.ronda()
        self.assertEqual(self.avisos("etaHEN"), 0)
        self.assertEqual(e["fw"]["explotable_max"], 1360)

    def test_repo_nuevo_mayor_de_48h_y_umbrales(self):
        self.ronda()
        item = {"full_name": "newdev/PS5-Kernel-Exploit", "html_url": "https://github.com/newdev/PS5-Kernel-Exploit",
                "description": "PS5 kernel exploit 13.00", "stargazers_count": 60, "fork": False, "size": 800,
                "created_at": iso(72)}
        self.m.search = {"items": [dict(item)]}
        e = self.ronda()
        self.assertTrue(any("PS5-Kernel-Exploit" in p["titulo"] for p in e["pendientes"]), "⭐ a 60★")
        self.m.search = {"items": [dict(item, stargazers_count=1500)]}
        self.ronda()
        self.assertEqual(self.avisos("PS5-Kernel-Exploit"), 1, "🔥 al pasar de 1.000★")

    def test_repo_solo_con_binario(self):
        self.ronda()
        self.m.contenidos["dev/binjb"] = ["README.md", "LICENSE"]
        self.m.releases["dev/binjb"] = [rel(60, "v1", "jb", 1, repo="dev/binjb")]
        self.m.search = {"items": [{"full_name": "dev/binjb", "html_url": "https://github.com/dev/binjb",
                                    "description": "PS5 jailbreak payload", "stargazers_count": 250,
                                    "fork": False, "size": 10, "created_at": iso(5)}]}
        e = self.ronda()
        todo = "\n".join(m["texto"] for m in self.m.tg.del_bot) + json.dumps(e["pendientes"])
        self.assertIn("dev/binjb", todo)

    def test_error_permanente_no_bloquea_la_cola(self):
        self.ronda()
        e = estado.cargar(self.ruta)
        e["cola"] = [{"clave": "malo", "tipo": "release", "repo": "github.com:a/b", "titulo": "malo", "nota": 10,
                      "nivel": "fuego", "url": "javascript:alert(1)"},
                     {"clave": "bueno", "tipo": "release", "repo": "github.com:a/c", "titulo": "kstuff bueno",
                      "nota": 10, "nivel": "fuego", "url": "https://github.com/a/c"}]
        estado.guardar(e, self.ruta)
        orig = self.m.tg.manejar

        def manejar(metodo, datos):
            if metodo == "sendMessage" and "malo" in datos["text"]:
                return red.Resp(400, json.dumps({"ok": False, "description": "Bad Request: BUTTON_URL_INVALID"}).encode())
            return orig(metodo, datos)
        self.m.tg.manejar = manejar
        e = self.ronda()
        self.assertEqual(self.avisos("kstuff bueno"), 1)
        self.assertEqual(e["cola"], [])

    def test_articulo_de_algo_nuevo_que_enlaza_etahen(self):
        self.ronda()
        e = estado.cargar(self.ruta)
        e["pendientes"].append({"clave": "github.com:etahen/etahen@2.5c", "tipo": "release",
                                "repo": "github.com:etaHEN/etaHEN", "repo_cfg": "github.com:etaHEN/etaHEN",
                                "titulo": "etaHEN 2.5c", "nota": 6, "nivel": "interesante", "alias": ["etaHEN"]})
        estado.guardar(e, self.ruta)
        self.m.wololo = ("<rss><channel><item><title>PS5: New kernel exploit released for 13.80</title>"
                         "<link>https://wololo.net/x</link><pubDate>" +
                         time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()) + "</pubDate>"
                         "<description><![CDATA[Get it at https://github.com/newdev/kexp1380/releases and load it "
                         "with https://github.com/etaHEN/etaHEN]]></description></item></channel></rss>")
        e = self.ronda()
        todo = "\n".join(m["texto"] for m in self.m.tg.del_bot) + json.dumps(e["pendientes"])
        self.assertIn("13.80", todo)
        self.assertIn("github.com:newdev/kexp1380", e["descubiertos"])

    def test_enlace_a_repo_renombrado_no_duplica(self):
        self.ronda()
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.ronda()
        self.m.wololo = ("<rss><channel><item><title>PS5: etaHEN 3.0 released</title>"
                         "<link>https://wololo.net/y</link><pubDate>" +
                         time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()) + "</pubDate>"
                         "<description><![CDATA[https://github.com/LightningMods/etaHEN/releases]]></description>"
                         "</item></channel></rss>")
        e = self.ronda()
        self.assertEqual(self.avisos("etaHEN"), 1)
        self.assertFalse(any("etaHEN" in p["titulo"] for p in e["pendientes"]))
        self.assertNotIn("github.com:lightningmods/etahen", e["descubiertos"])

    def test_resumen_a_trozos_no_repite(self):
        self.ronda()
        e = estado.cargar(self.ruta)
        e["pendientes"] = [{"clave": f"k{i}", "tipo": "release", "repo": f"github.com:a/r{i}",
                            "titulo": f"Herramienta {i} " + "x" * 300, "nota": 5, "nivel": "menor",
                            "url": "https://github.com/a/b", "ts": 0} for i in range(15)]
        estado.guardar(e, self.ruta)
        self.m.tg.fallos = ["ok", "conexion", "conexion", "conexion", "conexion"]
        radar.main(["resumen", "--estado", self.ruta])
        primero = self.avisos("Resumen de la scene")
        radar.main(["resumen", "--estado", self.ruta])
        self.assertEqual(self.avisos("Resumen de la scene"), primero, "el trozo 1 no se repite")
        self.assertEqual(estado.cargar(self.ruta)["pendientes"], [])

    def test_commit_en_enfriamiento_no_se_come_la_release(self):
        self.ronda()
        e = estado.cargar(self.ruta)
        e["repos"]["github.com:ntfargo/relapse-exploit"]["ult_aviso"] = int(time.time())
        estado.guardar(e, self.ruta)
        self.m.commits["ntfargo/Relapse-Exploit"].insert(0, {"sha": "abc", "html_url": "u", "commit": {
            "message": "Add new kernel offsets", "author": {"date": iso(0.2)}}})
        self.m.releases["ntfargo/Relapse-Exploit"] = [rel(70, "v1.0.1", "- Fixed crash", 0.2,
                                                          repo="ntfargo/Relapse-Exploit")]
        e = self.ronda()
        todo = "\n".join(m["texto"] for m in self.m.tg.del_bot) + json.dumps(e["pendientes"])
        self.assertIn("v1.0.1", todo)


class TestRevision2(Base):
    def avisos(self, palabra):
        return sum(palabra in m["texto"] for m in self.m.tg.del_bot)

    def test_rotacion_por_tiers(self):
        with mock.patch.dict(os.environ, {"RADAR_TODO": ""}):
            self.ronda()                      # ronda 1 (siembra: todo)
            self.m.peticiones.clear()
            self.ronda()                      # ronda 2: A sí, B no
        pide = "\n".join(self.m.peticiones)
        self.assertIn("etaHEN/etaHEN/releases", pide)
        self.assertNotIn("LightningMods/Itemzflow/releases", pide)
        self.assertNotIn("playstation.com", pide)

    def test_ia_no_tumba_tus_repos_ni_baja_un_fuego(self):
        self.ronda()
        self.m.ia = "ok"
        orig = self.m.__class__.__call__

        def llamar(slf, url, cab, datos):
            if "chat/completions" in url:
                out = {"resumen": "r", "nota": 3, "disponible": True, "firmwares": "", "angulo": "a",
                       "es_juego": True}
                return red.Resp(200, json.dumps({"choices": [{"message": {"content": json.dumps(out)}}]}).encode())
            return orig(slf, url, cab, datos)
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        with mock.patch.object(self.m.__class__, "__call__", llamar):
            self.ronda()
        self.assertEqual(self.avisos("etaHEN"), 1)

    def test_commits_no_inundan(self):
        self.ronda()
        for i in range(4):
            self.m.commits["ntfargo/Relapse-Exploit"].insert(0, {"sha": f"s{i}", "html_url": "u", "commit": {
                "message": f"Add new feature {i}", "committer": {"date": iso(0.1)}}})
            e = self.ronda()
        self.assertEqual(sum("Relapse" in p.get("repo", "") for p in e["pendientes"]), 1)

    def test_repo_nuevo_que_solo_promete(self):
        self.ronda()
        self.m.search = {"items": [{"full_name": "faker/PS5-Jailbreak-1430", "html_url": "x",
                                    "description": "PS5 jailbreak 14.30 coming soon, star to get notified",
                                    "stargazers_count": 300, "fork": False, "size": 30, "created_at": iso(3)}]}
        e = self.ronda()
        self.assertFalse(any("faker" in p["titulo"] for p in e["pendientes"]))
        self.assertEqual(self.avisos("faker"), 0)

    def test_exploit_solo_por_prensa_es_fuego(self):
        self.ronda()
        self.m.wololo = ("<rss><channel><item><title>PS5: Kernel exploit for firmware 14.20 released</title>"
                         "<link>https://wololo.net/z</link><pubDate>" +
                         time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()) + "</pubDate>"
                         "<description><![CDATA[Download: https://github.com/dev/k1420/releases/tag/v1]]></description>"
                         "</item></channel></rss>")
        e = self.ronda()
        self.assertEqual(self.avisos("14.20"), 1)
        self.assertEqual(e["fw"]["explotable_max"], 1360, "la prensa no mueve el listón")

    def test_resumen_congelado(self):
        self.ronda()
        e = estado.cargar(self.ruta)
        e["pendientes"] = [{"clave": f"k{i}", "tipo": "release", "repo": f"github.com:a/r{i}",
                            "titulo": f"Herramienta {i} " + "x" * 300, "nota": 5, "nivel": "menor",
                            "url": "https://github.com/a/b", "ts": 0} for i in range(15)]
        estado.guardar(e, self.ruta)
        self.m.tg.fallos = ["ok", "conexion", "conexion", "conexion", "conexion"]
        radar.main(["resumen", "--estado", self.ruta])
        e = estado.cargar(self.ruta)
        e["pendientes"].append({"clave": "top", "tipo": "release", "repo": "github.com:a/top", "titulo": "TOPNUEVO",
                                "nota": 8, "nivel": "interesante", "url": "u", "ts": 0})
        estado.guardar(e, self.ruta)
        radar.main(["resumen", "--estado", self.ruta])     # termina el congelado
        e = estado.cargar(self.ruta)
        self.assertTrue(any(p["clave"] == "top" for p in e["pendientes"]), "lo nuevo espera al siguiente")
        todos = "\n".join(m["texto"] for m in self.m.tg.del_bot)
        for i in range(15):
            self.assertIn(f"Herramienta {i} ", todos)

    def test_ya_llego_tras_un_mensaje_de_prueba(self):
        self.ronda()
        radar.main(["prueba", "--estado", self.ruta])    # no guarda estado ni lleva botón
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.m.tg.fallos = ["timeout_llega"]
        self.ronda()
        self.assertEqual(sum("etaHEN 3.0" in m["texto"] for m in self.m.tg.del_bot), 1)
        prueba = [m for m in self.m.tg.del_bot if "PRUEBA" in m["texto"]][0]
        self.assertIn(prueba["markup"], (None, {"inline_keyboard": []}))


class TestVerificacion(unittest.TestCase):
    conf = radar.cargar_conf()

    def test_ia_respeta_topes_y_fuegos(self):
        from radar import ia as IA
        sube = {"resumen": "r", "nota": 10, "disponible": True, "firmwares": "", "angulo": "a", "es_juego": False}
        baja = dict(sube, nota=3)
        commit = {"tipo": "commit", "nota": 8, "tope": 8, "tier": "A"}
        IA.combinar(commit, sube, 9)
        self.assertEqual(commit["nota"], 8, "la IA no convierte un commit en 🔥")
        prensa = {"tipo": "articulo", "nota": 9, "tope": 9, "tier": "C"}
        self.assertTrue(IA.combinar(prensa, baja, 9))
        self.assertEqual(prensa["nota"], 9, "la IA no le quita el 🔥 al exploit de prensa")

    def test_descripciones_normales_no_son_promesas(self):
        for d in ["PS5 kernel exploit for FW 4.03. Not affiliated with Sony.", "PS5 payload that shows FPS overlay",
                  "PS5 jailbreak, see writeup for details", "Does not work after 2.70"]:
            self.assertIsNone(reglas.RE_PROMESA.search(d), d)
        self.assertTrue(reglas.RE_PROMESA.search("PS5 jailbreak 14.30 coming soon, star to get notified"))


class TestIA(Base):
    def test_json_dentro_del_razonamiento(self):
        self.ronda()
        orig = self.m.__class__.__call__

        def llamar(slf, url, cab, datos):
            if "chat/completions" in url:
                out = {"resumen": "r", "nota": 9, "disponible": True, "para_que_sirve": "p",
                       "idea_video": {"titulo": "Titulo del video", "gancho": "g"}, "pasos": ["uno"],
                       "firmwares": "", "es_juego": False}
                return red.Resp(200, json.dumps({"choices": [{"finish_reason": "stop", "message": {
                    "content": "", "reasoning_content": "Pienso... " + json.dumps(out)}}]}).encode())
            return orig(slf, url, cab, datos)
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        with mock.patch.object(self.m.__class__, "__call__", llamar):
            self.ronda()
        self.assertIn("Titulo del video", self.m.tg.del_bot[-1]["texto"])

    def test_motivo_visible_si_falla(self):
        self.ronda()
        self.m.ia = "caida"
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.ronda()
        t = self.m.tg.del_bot[-1]["texto"]
        self.assertIn("sin IA: groq: sin conexión con super-ia)", t)
        self.assertNotIn("ejemplo", t)

    def test_json_con_adornos(self):
        from radar import ia as IA
        self.assertEqual(IA._extraer_json('```json\n{"nota": 7, "resumen": "a\nb"}\n```')["nota"], 7)
        self.assertIsNone(IA._extraer_json('{"nota": 7, "resumen": "cort'))


class TestAjustesIA(Base):
    def test_titulos_versiones_y_frases(self):
        from radar import formato, ia as IA
        self.assertEqual(formato.titulo_bonito({"titulo": "v0.8.4", "tag": "v0.8.4", "repo": "github.com:itsPLK/ps5-webkit-autoloader",
                                                "alias": ["WebKit Autoloader"]}), "WebKit Autoloader v0.8.4")
        self.assertEqual(formato.titulo_bonito({"titulo": "etaHEN 2.6b", "tag": "2.6b", "repo": "x"}), "etaHEN 2.6b")
        f = IA._frase("Permite jugar sin depender del inglés y ver las traducciones superpuestas en una televisión "
                      "compatible, sin anuncios ni suscripciones y con muchas más cosas que no caben aquí", 120)
        self.assertTrue(f.endswith("…") or f.endswith("."))
        self.assertNotIn("l…", f[-3:])

    def test_agrupa_versiones(self):
        items = [{"clave": f"k{i}", "tipo": "release", "repo": "github.com:phantomptr/ps5upload", "tag": f"v12.6.{i}",
                  "titulo": f"v12.6.{i}", "url": f"u{i}", "nota": 5, "nivel": "menor", "ts": i} for i in (22, 23, 24)]
        g = radar.agrupar(items)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["tag"], "v12.6.24")
        self.assertEqual(g[0]["versiones"], ["v12.6.22", "v12.6.23", "v12.6.24"])

    def test_repetir_resumen_del_23(self):
        """El resumen del 23/11: fuera el traductor (no es scene), una línea por repo."""
        self.ronda()
        self.m.ia = "ok"
        self.m.releases["itsPLK/ps5-webkit-autoloader"] = [rel(81, "v0.8.4", "- Added support for firmware 3.00-13.60\n- New payload settings", 30,
                                                                repo="itsPLK/ps5-webkit-autoloader")]
        self.m.releases["phantomptr/ps5upload"] = [rel(90 + i, f"v12.6.2{i}", "- Minor fixes", 30 - i, repo="phantomptr/ps5upload")
                                                   for i in (2, 3)]
        self.m.releases["yunusdemirbag/nino-game-translator"] = [rel(99, "v4.5.0", "- New overlay translator languages", 20,
                                                                      repo="yunusdemirbag/nino-game-translator")]
        e = estado.cargar(self.ruta)
        ahora = int(time.time())
        for k in ["github.com:itsplk/ps5-webkit-autoloader@v0.8.4", "github.com:phantomptr/ps5upload@v12.6.22",
                  "github.com:phantomptr/ps5upload@v12.6.23", "github.com:yunusdemirbag/nino-game-translator@v4.5.0"]:
            e["vistos"][k] = ahora - 3600
        e["resumen"].pop("anterior", None)
        estado.guardar(e, self.ruta)
        radar.main(["repetir_resumen", "--estado", self.ruta])
        t = self.m.tg.del_bot[-1]["texto"]
        print("\n" + t)
        self.assertIn("repetido", t)
        self.assertIn("ps5-webkit-autoloader v0.8.4", t)
        self.assertNotIn("nino", t.lower())
        self.assertEqual(sum("ps5upload" in l for l in t.splitlines() if l.startswith("•")), 1)
        self.assertIn("2 versiones", t)


class TestTurnosResumen(unittest.TestCase):
    def setUp(self):
        self.conf = radar.cargar_conf()

    def _a_las(self, hh, mm, dia=23):
        from zoneinfo import ZoneInfo
        dt = datetime(2026, 11, dia, hh, mm, tzinfo=ZoneInfo("Europe/Madrid"))
        return mock.patch.object(radar, "ahora_local", lambda c: dt)

    def test_manana_y_tarde(self):
        e = {}
        e["resumen"] = {"ultimo_dia": "2026-11-22", "ultimo_turno": "2026-11-22 18:01"}
        with self._a_las(8, 59):
            self.assertFalse(radar.toca_resumen(e, self.conf))
        with self._a_las(9, 1):
            self.assertTrue(radar.toca_resumen(e, self.conf))
            radar._marcar_resumen(e, self.conf, "2026-11-23")
            self.assertFalse(radar.toca_resumen(e, self.conf))
        with self._a_las(17, 59):
            self.assertFalse(radar.toca_resumen(e, self.conf))
        with self._a_las(18, 0):
            self.assertTrue(radar.toca_resumen(e, self.conf), "a las 18 toca la actualización")
            radar._marcar_resumen(e, self.conf, "2026-11-23")
        with self._a_las(23, 0):
            self.assertFalse(radar.toca_resumen(e, self.conf))

    def test_estado_antiguo_solo_con_ultimo_dia(self):
        e = {"resumen": {"ultimo_dia": "2026-11-23"}}     # ya salió el de las 9 con la versión anterior
        with self._a_las(12, 0):
            self.assertFalse(radar.toca_resumen(e, self.conf))
        with self._a_las(18, 5):
            self.assertTrue(radar.toca_resumen(e, self.conf))

    def test_detalle_de_los_interesantes(self):
        from radar import formato
        x = {"nivel": "interesante", "nota": 7, "titulo": "PSWloader", "repo": "github.com:a/pswloader",
             "url": "https://github.com/a/pswloader", "idea_titulo": "Windows 11 en la PS5",
             "angulo": "¿Windows en tu PS5?", "resumen": "Arranca Windows 11 nativo.",
             "para_que": "Para usar la PS5 como PC.", "fw_ia": "3.00-4.51", "pasos": ["Carga el payload", "Reinicia"],
             "competencia": "nadie aún", "estado_txt": "release publicada"}
        t = formato.resumen([x], "Europe/Madrid", tarde=True, desde="09:02")
        for trozo in ("Actualización de la tarde", "desde el resumen de las 09:02", "🎬", "Qué es", "Para qué sirve",
                      "Firmware: 3.00-4.51", "Competencia: nadie aún", "1. Carga el payload", "Gancho"):
            self.assertIn(trozo, t)


class TestAvisoIA(unittest.TestCase):
    def test_avisa_una_vez_y_al_volver(self):
        conf = radar.cargar_conf()
        e = {"ia": {"fallos": 3, "ultimo_error": "groq: sin conexión con super-ia"}}
        enviados = []
        with mock.patch.object(radar.telegram, "enviar", lambda e, t, m=None, **k: enviados.append(t) or 1), \
             mock.patch.object(radar, "en_silencio", lambda c: False), \
             mock.patch.object(radar.estado, "guardar", lambda e, r: None):
            radar.avisar_ia(e, conf, "x")
            radar.avisar_ia(e, conf, "x")
            self.assertEqual(len(enviados), 1)
            self.assertIn("Revisa la pasarela", enviados[0])
            e["ia"]["fallos"] = 0
            radar.avisar_ia(e, conf, "x")
            self.assertEqual(len(enviados), 2)
            self.assertIn("vuelve a responder", enviados[1])
