import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar import informatica as inf, red  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class Red:
    def __init__(self, ia="ok"):
        self.ia = ia
        self.tg = []
        self.ia_llamadas = 0

    def __call__(self, url, cab, datos):
        if "github.com/trending" in url:
            if "weekly" in url:
                return red.Resp(200, b"<html></html>")
            return red.Resp(200, open(os.path.join(FIX, "trending.html"), "rb").read())
        if "search/repositories" in url:
            items = [{"full_name": "net/wg-easy-ui", "description": "WireGuard VPN with a web UI, easy to self-host",
                      "language": "TypeScript", "stargazers_count": 640, "topics": ["vpn", "wireguard"],
                      "created_at": "2026-11-20T00:00:00Z"},
                     {"full_name": "dev/some-npm-lib", "description": "A tiny JavaScript utility library",
                      "language": "JavaScript", "stargazers_count": 300, "topics": []}]
            return red.Resp(200, json.dumps({"items": items}).encode())
        if "chat/completions" in url:
            self.ia_llamadas += 1
            if self.ia == "caida":
                return red.Resp(0, error="URLError: x")
            contenido = datos["messages"][1]["content"]
            if "<readme" in contenido:
                self.pasos_pedidos = True
                out = {"repos": [{"repo": "someone/winutil-pro", "requisitos": "Windows 10/11 y PowerShell",
                                  "pasos": ["1. Abre PowerShell como administrador", "Pega el comando del README",
                                            "Elige los ajustes y aplica"]}]}
                return red.Resp(200, json.dumps({"choices": [{"message": {"content": json.dumps(out)}}]}).encode())
            assert "<repos>" in contenido and "técnico microinformático" in datos["messages"][0]["content"]
            out = {"repos": [
                {"repo": "someone/winutil-pro", "util": True, "categoria": "windows", "nota": 9,
                 "para_que": "Limpia y optimiza Windows en un clic.", "idea_video": "Deja Windows como nuevo en 5 minutos",
                 "dificultad": "fácil", "ofensivo": False},
                {"repo": "lab/selfhost-panel", "util": True, "categoria": "descargas", "nota": 8,
                 "para_que": "Panel para tu homelab.", "idea_video": "El panel que le faltaba a mi homelab",
                 "dificultad": "media", "ofensivo": False},
                {"repo": "net/wg-easy-ui", "util": True, "categoria": "redes", "nota": 7,
                 "para_que": "VPN WireGuard con web.", "idea_video": "Tu VPN en casa en 10 minutos",
                 "dificultad": "media", "ofensivo": False},
                {"repo": "dev/some-npm-lib", "util": False, "categoria": "otro", "nota": 3,
                 "para_que": "x", "idea_video": "x", "dificultad": "media", "ofensivo": False}]}
            return red.Resp(200, json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(out, ensure_ascii=False)}}]}).encode())
        if url.endswith("/repos/someone/winutil-pro/readme"):
            return red.Resp(200, b"# WinUtil\nRun irm christitus.com/win | iex in an admin PowerShell")
        if "api.telegram.org" in url and url.endswith("sendMessage"):
            self.tg.append(datos["text"])
            return red.Resp(200, json.dumps({"ok": True, "result": {"message_id": 500 + len(self.tg)}}).encode())
        return red.Resp(404)


class TestInformatica(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ruta = os.path.join(self.tmp, "informatica.json")
        self.env = mock.patch.dict(os.environ, {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "-100",
                                                "GROQ_API_KEY": "k", "SUPER_IA_URL": "", "OPENROUTER_API_KEY": ""})
        self.env.start()
        self.sleep = mock.patch("time.sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        red.FALSO = None
        self.env.stop()
        self.sleep.stop()

    def test_parseo_trending(self):
        red.FALSO = Red()
        c = inf.trending("https://github.com/trending?since=daily")
        self.assertEqual([x["repo"] for x in c][:3], ["someone/winutil-pro", "crypto/moon-bot", "lab/selfhost-panel"])
        self.assertEqual(c[0]["estrellas"], 12345)
        self.assertEqual(c[0]["estrellas_periodo"], 1234)
        self.assertEqual(c[0]["lenguaje"], "PowerShell")
        self.assertIn("& more", c[0]["descripcion"])

    def test_resumen_con_ia(self):
        red.FALSO = rd = Red()
        with mock.patch.object(inf, "toca", lambda e: True):
            inf.main(["resumen", "--estado", self.ruta])
        t = rd.tg[-1]
        print("\n" + t)
        self.assertIn("Repos de informática", t)
        self.assertIn("someone/winutil-pro", t)
        self.assertIn("+1,2k hoy", t)
        self.assertIn("⬇️ Descargas", t)
        self.assertIn("Pasos", t)
        self.assertIn("Abre PowerShell como administrador", t)
        self.assertIn("Requisitos: Windows 10/11", t)
        self.assertIn("🌐 Redes, privacidad y seguridad", t)
        self.assertNotIn("moon-bot", t)          # cripto fuera
        self.assertNotIn("stealer", t)           # ofensivo fuera
        self.assertNotIn("some-npm-lib", t)      # la IA dice que no es útil
        self.assertEqual(rd.ia_llamadas, 2, "una llamada para elegir y otra para los pasos")
        with open(self.ruta) as f:
            e = json.load(f)
        self.assertIn("someone/winutil-pro", e["enviados_repos"])
        # Mismo día: no se repite
        antes = len(rd.tg)
        inf.main(["resumen", "--estado", self.ruta])
        self.assertEqual(len(rd.tg), antes)

    def test_no_repite_repos(self):
        red.FALSO = rd = Red()
        with mock.patch.object(inf, "toca", lambda e: True):
            inf.main(["resumen", "--estado", self.ruta])
            inf.main(["resumen", "--estado", self.ruta])
        self.assertNotIn("winutil-pro", rd.tg[-1])

    def test_sin_ia(self):
        red.FALSO = rd = Red(ia="caida")
        with mock.patch.object(inf, "toca", lambda e: True):
            inf.main(["resumen", "--estado", self.ruta])
        t = rd.tg[-1]
        self.assertIn("sin IA", t)
        self.assertIn("winutil-pro", t)
        self.assertNotIn("stealer", t)

    def test_prueba_no_apunta(self):
        red.FALSO = Red()
        inf.main(["prueba", "--estado", self.ruta])
        self.assertFalse(os.path.exists(self.ruta))

    def test_hora(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        z = ZoneInfo("Europe/Madrid")
        with mock.patch.object(inf, "ahora", lambda zona=None: datetime(2026, 11, 25, 15, 59, tzinfo=z)):
            self.assertFalse(inf.toca({}))
        with mock.patch.object(inf, "ahora", lambda zona=None: datetime(2026, 11, 25, 16, 0, tzinfo=z)):
            self.assertTrue(inf.toca({}))
            self.assertFalse(inf.toca({"ultimo_dia": "2026-11-25"}))


if __name__ == "__main__":
    unittest.main()


class TestRevisionInformatica(TestInformatica):
    def test_github_caido_no_envia_ni_marca(self):
        rd = Red()
        red.FALSO = lambda url, cab, datos: red.Resp(429) if "github.com" in url else rd(url, cab, datos)
        with mock.patch.object(inf, "toca", lambda e: True):
            rc = inf.main(["resumen", "--estado", self.ruta])
        self.assertEqual(rc, 1)
        self.assertEqual(rd.tg, [])
        self.assertFalse(os.path.exists(self.ruta))

    def test_formato_raro_de_la_ia_usa_plan_b(self):
        rd = Red()

        def falso(url, cab, datos):
            if "chat/completions" in url:
                return red.Resp(200, json.dumps({"choices": [{"message": {"content": '{"resultados": []}'}}]}).encode())
            return rd(url, cab, datos)
        red.FALSO = falso
        with mock.patch.object(inf, "toca", lambda e: True):
            inf.main(["resumen", "--estado", self.ruta])
        self.assertIn("winutil-pro", rd.tg[-1])
        self.assertIn("sin IA", rd.tg[-1])

    def test_nombres_con_url(self):
        self.assertEqual(inf._nombre("https://github.com/Someone/WinUtil-Pro/"), "someone/winutil-pro")

    def test_filtros_y_enlaces(self):
        for legit in ["LLM token usage tracker", "JWT token inspector", "Spam filter for your mail server",
                      "DDoS protection for self-hosted sites", "Linux KMS console", "Linux cheat-sheet"]:
            self.assertIsNone(inf.RE_FUERA.search(legit), legit)
        for malo in ["Solana trading bot", "Browser info stealer", "KMS activator for Windows", "cheat engine tables"]:
            self.assertTrue(inf.RE_FUERA.search(malo), malo)
        self.assertNotIn("t.me", inf._limpio("Únete en t.me/canal y www.spam.com o bit.ly/x", 200))


class TestGemini(TestInformatica):
    def test_relleno_no_mete_descartados_ni_duplica(self):
        rd = Red()

        def falso(url, cab, datos):
            if "chat/completions" in url:
                out = {"repos": [
                    {"repo": "lab/selfhost-panel", "util": True, "categoria": "descargas", "nota": 8,
                     "para_que": "a", "idea_video": "b", "dificultad": "media", "ofensivo": False},
                    {"repo": "lab/selfhost-panel", "util": True, "categoria": "descargas", "nota": 8,
                     "para_que": "a", "idea_video": "b", "dificultad": "media", "ofensivo": False},
                    {"repo": "someone/winutil-pro", "util": True, "categoria": "windows", "nota": 7,
                     "para_que": "x", "idea_video": "y", "dificultad": "fácil", "ofensivo": True}]}
                return red.Resp(200, json.dumps({"choices": [{"message": {"content": json.dumps(out)}}]}).encode())
            return rd(url, cab, datos)
        red.FALSO = falso
        inf.DESCARTADOS_IA.clear()
        with mock.patch.object(inf, "toca", lambda e: True):
            inf.main(["resumen", "--estado", self.ruta])
        t = rd.tg[-1]
        self.assertEqual(t.count("lab/selfhost-panel</a>"), 1)
        self.assertNotIn("winutil-pro", t)
