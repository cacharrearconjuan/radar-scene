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
            "Poopsploit payload requires 12.00 or lower firmware PS5": "12.00",
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
                  "PS5 kernel exploit announced for 14.10"]:
            ok, _ = reglas.disponible({"tipo": "articulo", "titulo": t, "repos_enlazados": ["github.com:a/b"]})
            self.assertFalse(ok, t)
        ok, _ = reglas.disponible({"tipo": "articulo", "titulo": "PS5: etaHEN 2.6b released",
                                   "repos_enlazados": ["github.com:a/b"]})
        self.assertTrue(ok)
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
        textos = [m["texto"] for m in self.m.tg.del_bot]
        juntos = "\n".join(textos)
        print("\n---- AVISOS ----\n" + "\n=====\n".join(textos))
        self.assertIn("14.30", juntos, "firmware nuevo de Sony")
        self.assertIn("etaHEN", juntos, "etaHEN 2.6b es de un repo clave")
        self.assertIn("kstuff", juntos, "kstuff con firmware 14.10")
        self.assertIn("PS5-Kernel-Exploit-1422", juntos, "repo nuevo con 640 estrellas")
        self.assertNotIn("faker", juntos)
        self.assertNotIn("ps5-theme", juntos)
        self.assertNotIn("teases", juntos)
        self.assertNotIn("Translations", juntos)
        self.assertNotIn("Y2JB-1.3.0", juntos, "los borradores no cuentan")
        self.assertNotIn("typo", juntos)
        self.assertIn("(sin IA)", juntos, "la IA está caída y los avisos salen igual")
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

    def test_ia_funciona_y_ia_dice_no(self):
        self.ronda()
        self.m.ia = "ok"
        self.m.releases["etaHEN/etaHEN"].append(rel(10, "3.0", "- Added support for firmware 13.60 HEN", 1))
        self.ronda()
        t = self.m.tg.del_bot[-1]["texto"]
        self.assertIn("Resumen de prueba", t)
        self.assertNotIn("(sin IA)", t)
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
