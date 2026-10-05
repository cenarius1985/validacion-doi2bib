# -*- coding: utf-8 -*-
"""Parser de .bib, normalización y comparador determinista.

Portado y extendido desde el script VERIFICADO
`paper-mri-us-reviewer/scripts/check_dois.py` (lógica de doi2bib probada en
producción 2026-09). No cambiar los umbrales sin volver a verificar contra
doi2bib.org.
"""
import difflib
import html as htmllib
import re
import unicodedata

# --------------------------------------------------------------- parseo .bib

_TIPOS_IGNORAR = {"comment", "string", "preamble", "article-no-entry"}


def parsear_bib(texto):
    """Devuelve lista de dicts: clave, tipo, cuerpo, inicio, fin.

    Soporta entradas multi-línea y de una sola línea con abstracts enormes
    (trampa conocida del Bibliography.bib). Sanea claves malformadas tipo
    `@article{[MinonzioBDATMeasurements2022,` (corchete inicial).
    """
    entradas = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\n]+?)\s*,(.*?)(?=\n@|\Z)", texto, re.S):
        tipo = m.group(1).lower()
        if tipo in _TIPOS_IGNORAR:
            continue
        clave = m.group(2).strip().lstrip("[").strip()
        entradas.append({
            "clave": clave,
            "tipo": tipo,
            "cuerpo": m.group(3),
            "inicio": m.start(),
            "fin": m.end(),
        })
    return entradas


def valor_campo(cuerpo, campo):
    """Extrae el valor de un campo con balanceo de llaves; None si no está."""
    m = re.search(r"(?:^|[,{\n])\s*%s\s*=\s*" % campo, cuerpo, re.I)
    if not m:
        return None
    rest = cuerpo[m.end():].lstrip()
    if rest[:1] == "{":
        depth, i = 0, 0
        while i < len(rest):
            c = rest[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return rest[1:i]
            i += 1
        return rest[1:]
    if rest[:1] == '"':
        end = rest.find('"', 1)
        return rest[1:end if end > 0 else len(rest)]
    return rest.split(",")[0].strip()


# ------------------------------------------------------------- normalización

def norm(s):
    """Normaliza un string BibTeX/doi2bib para comparación (verbatim del
    script verificado: quita HTML, acentos, llaves y comandos LaTeX)."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)          # doi2bib mete <i>, <sub>, <sup>
    s = htmllib.unescape(s)
    s = re.sub(r"\{\\['`^\"~=.]\s*(\w)\}", r"\1", s)  # {\'i} -> i (Garc{\'i}a)
    s = re.sub(r"\\['`^\"~=.]\s*(\w)", r"\1", s)      # \'i -> i suelto
    s = s.replace("\u2019", "").replace("'", "")      # D'Lima == Dlima
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.replace("{", " ").replace("}", " ")
    s = re.sub(r"\\[a-zA-Z]+", " ", s)     # comandos latex residuales
    s = re.sub(r"[^0-9a-zA-Z]+", " ", s)
    return " ".join(s.lower().split())


PARTICULAS = ("de ", "del ", "la ", "van ", "von ", "der ", "den ", "di ",
              "da ", "dos ", "el ")


def apellidos(campo_autor):
    """Set de apellidos normalizados de un campo `author` BibTeX."""
    if not campo_autor:
        return set()
    out = set()
    for a in re.split(r"\s+and\s+", campo_autor):
        a = a.strip()
        if not a or a.lower() in ("others",):
            continue
        fam = a.split(",")[0] if "," in a else a.split()[-1]
        low = fam.lower()
        changed = True
        while changed:
            changed = False
            for p in PARTICULAS:
                if low.startswith(p):
                    fam = fam[len(p):]
                    low = fam.lower()
                    changed = True
        if fam:
            out.add(norm(fam))
    return {x for x in out if x}


def doi_limpio(campo_doi):
    """Extrae el DOI de un campo doi (o de texto suelto); None si no hay."""
    if not campo_doi:
        return None
    m = re.search(r"(10\.\d{4,9}/\S+)", campo_doi.strip())
    if not m:
        return None
    return m.group(1).rstrip(".,;)")


def anio_de(*campos):
    for f in campos:
        if f:
            m = re.search(r"\d{4}", f)
            if m:
                return m.group(0)
    return None


def parecido(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- comparador

# Franja donde el veredicto determinista es dudoso y se consulta a Bonsai
# (solo clasifica entre WARN/MISMATCH; nunca genera datos).
BORDE_MIN, BORDE_MAX = 0.85, 0.92


def comparar(cuerpo_local, cuerpo_remoto):
    """Compara una entrada local contra el BibTeX de doi2bib.

    Devuelve dict: estado (OK/WARN/MISMATCH), detalles[], similitud,
    borde (True si conviene consulta LLM).
    """
    detalles = []
    estado = "OK"

    def campo(b, nombre):
        v = valor_campo(b, nombre)
        return norm(v) if v else None

    t_loc, t_rem = campo(cuerpo_local, "title"), campo(cuerpo_remoto, "title")
    sim = parecido(t_loc, t_rem) if t_loc and t_rem else 0.0
    if t_loc and t_rem:
        if sim < 0.90:
            estado = "MISMATCH"
            detalles.append("title local='%s' vs doi2bib='%s' (sim %.2f)"
                            % (t_loc[:70], t_rem[:70], sim))
        elif sim < 0.985:
            detalles.append("title casi identico (sim %.2f)" % sim)

    a_loc = apellidos(valor_campo(cuerpo_local, "author"))
    a_rem = apellidos(valor_campo(cuerpo_remoto, "author"))
    autores_dudosos = False
    if a_loc and a_rem:
        faltan = a_loc - a_rem
        if faltan and not (a_loc & a_rem):
            estado = "MISMATCH"
            detalles.append("ningun autor coincide: local-%s vs doi2bib-%s"
                            % (sorted(a_loc), sorted(a_rem)))
        elif faltan:
            if estado == "OK":
                estado = "WARN"
            detalles.append("apellidos local-%s no estan en doi2bib-%s"
                            % (sorted(faltan), sorted(a_rem)))

    y_loc = anio_de(valor_campo(cuerpo_local, "year"), valor_campo(cuerpo_local, "date"))
    y_rem = anio_de(valor_campo(cuerpo_remoto, "year"), valor_campo(cuerpo_remoto, "issued"))
    if y_loc and y_rem and y_loc != y_rem:
        estado = "MISMATCH"
        detalles.append("year local=%s vs doi2bib=%s" % (y_loc, y_rem))

    for f in ("journal", "volume", "pages"):
        lv, rv = campo(cuerpo_local, f), campo(cuerpo_remoto, f)
        if lv and rv and parecido(lv, rv) < 0.75:
            if estado == "OK":
                estado = "WARN"
            detalles.append("%s local='%s' vs doi2bib='%s'" % (f, lv[:50], rv[:50]))

    borde = bool(t_loc and t_rem and BORDE_MIN <= sim < BORDE_MAX) or autores_dudosos
    return {"estado": estado, "detalles": detalles, "similitud": round(sim, 3),
            "borde": borde}


def coincide_busqueda(cuerpo_local, cuerpo_remoto):
    """Aceptación determinista de un candidato hallado por nombre.

    Política anti-invención: SOLO se acepta si el título coincide (>=0.90),
    el año no se contradice y algún autor coincide (si hay autores en ambos).
    """
    res = comparar(cuerpo_local, cuerpo_remoto)
    t_loc = norm(valor_campo(cuerpo_local, "title") or "")
    t_rem = norm(valor_campo(cuerpo_remoto, "title") or "")
    if not t_loc or not t_rem or parecido(t_loc, t_rem) < 0.90:
        return False, res
    a_loc = apellidos(valor_campo(cuerpo_local, "author"))
    a_rem = apellidos(valor_campo(cuerpo_remoto, "author"))
    if a_loc and a_rem and not (a_loc & a_rem):
        return False, res
    y_loc = anio_de(valor_campo(cuerpo_local, "year"))
    y_rem = anio_de(valor_campo(cuerpo_remoto, "year"), valor_campo(cuerpo_remoto, "issued"))
    if y_loc and y_rem and y_loc != y_rem:
        return False, res
    return True, res
