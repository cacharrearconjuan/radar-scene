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


def extraer(texto, tag=None, techo=None):
    """Firmwares mencionados. `techo` = máximo creíble (sony_max + 100)."""
    texto = texto or ""
    techo = techo or 2000
    excluir = _tag_nums(tag)
    vals, ocupado = set(), []
    # Versiones de programas: «v1.11», «version 2.50», «Y2JB 1.3»: fuera
    limpio = re.sub(r"(?i)\b(v|ver\.?|version|versión|release|build)\s*\d+(\.\d+)+", " ", texto)
    for m in RE_RANGO.finditer(limpio):
        a, b = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        if 100 <= a <= techo and 100 <= b <= techo and a <= b:
            vals.update((a, b))
            ocupado.append(m.span())
    for m in RE_NUM.finditer(limpio):
        if any(s <= m.start() < e for s, e in ocupado):
            continue
        n = _num(m.group(1), m.group(2))
        if n in excluir or not (100 <= n <= techo):
            continue
        ventana = limpio[max(0, m.start() - 25): m.end() + 25]
        if RE_CONTEXTO.search(ventana):
            vals.add(n)
    return Fw(sorted(vals))


def sony(texto):
    """Versiones de la página oficial: «26.07-14.22.00» -> 1422."""
    out = set()
    for m in RE_SONY.finditer(texto or ""):
        out.add(int(m.group(3)) * 100 + int(m.group(4)))
    return sorted(out)
