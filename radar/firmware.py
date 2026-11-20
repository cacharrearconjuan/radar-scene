"""Detección de firmwares de PS5 en textos (notas de versión, README, titulares).

Los firmwares se guardan como enteros: 13.60 -> 1360, 4.03 -> 403.
"""
import re
from dataclasses import dataclass, field

_N = r"(?<![\w.])(\d{1,2})\.(\d{2}|xx|XX)(?![\d.]\d)"
_SEP = r"\s*(?:-|–|—|~|to|through|thru|a|al|hasta|until)\s*"
_N1 = r"(?<![\w.])(\d{1,2})\.(\d{1,2}|xx|XX)(?![\d.]\d)"   # «12.7» = 12.70 (solo en rangos)
RE_RANGO = re.compile(_N + _SEP + _N1, re.I)
RE_NUM = re.compile(_N, re.I)
RE_CONTEXTO = re.compile(
    r"\b(fw|firmwares?|ofw|sys(?:tem)? ?software|up ?to|and (?:below|lower|up|above|higher)|"
    r"or (?:lower|below|higher)|hasta|supports?|supported|soporte|compatible|through|"
    r"ps5)\b|≤|<=|\+", re.I)
RE_SONY = re.compile(r"(?<!\d)(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})(?!\d)")


@dataclass
class Fw:
    lista: list = field(default_factory=list)

    @property
    def max(self):
        return max(self.lista) if self.lista else 0

    @property
    def min(self):
        return min(self.lista) if self.lista else 0

    def texto(self):
        if not self.lista:
            return ""
        if self.min == self.max:
            return fmt(self.max)
        return f"{fmt(self.min)} – {fmt(self.max)}"


def fmt(n):
    return f"{n // 100}.{n % 100:02d}"


def _num(a, b):
    b = "00" if b.lower() == "xx" else b
    if len(b) == 1:
        b += "0"
    return int(a) * 100 + int(b)


def _tag_nums(tag):
    """Números del tag (v1.11, 2.6b...) que NO son firmwares."""
    out = set()
    for m in RE_NUM.finditer(tag or ""):
        out.add(_num(m.group(1), m.group(2)))
    return out


RE_NEGATIVO = re.compile(
    r"\bnot\b|n't\b|\bno\b|\bunsupported|\bdo not update|\bavoid\b|\bbeyond\b|\bwip\b|\btodo\b|"
    r"\bsoon\b|\bplanned\b|\bcoming\b|\bfuture\b|\bblocked\b|\bpatch(?:ed|es)\b|\bfixed in\b|"
    r"\bwill\b|\bnot yet\b|\btest(?:ing)?\b|\bexperimental\b|\bpartial\b|"
    r"\bremoved?\b|\bdropp(?:ed|ing)\b|\brequires?\b|\brequired\b|\bbroken\b|\bunstable\b|"
    r"\bdeprecated\b|❌|✗|✖|🚫|⛔|"
    r"\bno (?:es|está) compatible|\bsin soporte|\bno funciona|\bno actualic", re.I)
RE_PS4 = re.compile(r"\bps4\b|orbis", re.I)
RE_PS5 = re.compile(r"\bps5\b|prospero", re.I)
RE_VERSION = re.compile(r"(?i)\b(v|ver\.?|version|versión|release|build)\s*\d+(\.\d+)+")
RE_FW_ANTES = re.compile(r"(?i)(firmware|fw|ofw|system software|software del sistema)\s*$")


def _frases(texto):
    """Trozos con su posición: frases, líneas y viñetas."""
    pos = 0
    for trozo in re.split(r"(?<=[.;!?])\s+|\n+", texto):
        ini = texto.find(trozo, pos)
        if ini < 0:
            ini = pos
        yield ini, trozo
        pos = ini + len(trozo)


def _quitar_versiones(texto):
    """Borra «v1.11», «version 2.50»… pero no «firmware version 14.00»."""
    def sub(m):
        antes = texto[max(0, m.start() - 25):m.start()]
        return m.group(0) if RE_FW_ANTES.search(antes) else " " * len(m.group(0))
    return RE_VERSION.sub(sub, texto)


def extraer(texto, tag=None, techo=None, estricto=False):
    """Firmwares que el texto dice SOPORTAR. `techo` = máximo creíble (sony_max + 100).

    Se descartan las frases negativas o de futuro («not supported», «do not update
    beyond 14.22», «WIP», «coming soon»…) y las que hablan solo de PS4. Con
    `estricto` también las de «experimental/testing» (no se usa por defecto).
    """
    texto = str(texto or "")
    techo = techo or 2000
    excluir = _tag_nums(tag)
    vals = set()
    limpio = _quitar_versiones(texto)
    for ini, frase in _frases(limpio):
        neg = RE_NEGATIVO.search(frase)
        if neg and (estricto or not re.fullmatch(r"(?i)test(ing)?|experimental|partial", neg.group(0))):
            continue
        if RE_PS4.search(frase) and not RE_PS5.search(frase):
            continue
        ocupado = []
        for m in RE_RANGO.finditer(frase):
            a, b = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
            if 100 <= a <= techo and 100 <= b <= techo and a <= b:
                vals.update((a, b))
                ocupado.append(m.span())
        for m in RE_NUM.finditer(frase):
            if any(s <= m.start() < e for s, e in ocupado):
                continue
            n = _num(m.group(1), m.group(2))
            if n in excluir or not (100 <= n <= techo):
                continue
            ventana = frase[max(0, m.start() - 30): m.end() + 30]
            if RE_CONTEXTO.search(ventana):
                vals.add(n)
    return Fw(sorted(vals))


def sony(texto):
    """Versiones de la página oficial: «26.07-14.22.00» -> 1422."""
    out = set()
    for m in RE_SONY.finditer(texto or ""):
        out.add(int(m.group(3)) * 100 + int(m.group(4)))
    return sorted(out)
