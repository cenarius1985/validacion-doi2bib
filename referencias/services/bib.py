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
    s = re.sub(r"\\['`^\"~=.]\s*\{(\w)\}", r"\1", s)  # \"{o} -> o (K\"{o}hler:
    # sin esta variante quedaba 'k o hler' y el apellido no intersectaba)
    s = re.sub(r"\\['`^\"~=.]\s*(\w)", r"\1", s)      # \'i -> i suelto
    s = s.replace("\u2019", "").replace("'", "")      # D'Lima == Dlima
    # Guiones unicode que NFKD no descompone (Crossref: "Three‐Dimensional"
    # con U+2010 quedaba "threedimensional" y no matcheaba "three dimensional")
    for g in ("\u2010", "\u2011", "\u2012", "\u2015"):
        s = s.replace(g, "-")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.replace("{", " ").replace("}", " ")
    s = re.sub(r"\\[a-zA-Z]+", " ", s)     # comandos latex residuales
    s = re.sub(r"[^0-9a-zA-Z]+", " ", s)
    return " ".join(s.lower().split())


PARTICULAS = ("de ", "del ", "la ", "van ", "von ", "der ", "den ", "di ",
              "da ", "dos ", "el ")
# "Melton III", "Smith Jr." y variantes: el sufijo no es parte del apellido
# y rompía la intersección con doi2bib (bug real del lote 321: melton iii).
SUFIJOS = {"jr", "sr", "ii", "iii", "iv", "v", "jr.", "sr."}


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
        palabras = fam.split()
        while palabras and palabras[-1].lower().strip(".") in SUFIJOS:
            palabras.pop()
        fam = " ".join(palabras)
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


def _sin_subtitulo(titulo):
    """Parte antes de ':' de un título crudo ('' si no hay subtítulo)."""
    if not titulo or ":" not in titulo:
        return ""
    return titulo.split(":", 1)[0]


def parecido_titulo(t_loc_raw, t_rem_raw):
    """Similitud de títulos tolerante a subtítulos añadidos/quitados.

    doi2bib/Crossref suelen traer 'Titulo: subtitulo' cuando el .bib local
    solo tiene 'Titulo' (o viceversa): un cambio menor que el ratio completo
    penalizaba por debajo del umbral. Se toma el mejor ratio entre el
    completo y las variantes sin subtítulo de cualquiera de los dos lados.
    """
    a, b = norm(t_loc_raw), norm(t_rem_raw)
    if not a or not b:
        return 0.0
    mejor = parecido(a, b)
    sa, sb = _sin_subtitulo(t_loc_raw), _sin_subtitulo(t_rem_raw)
    if sa:
        mejor = max(mejor, parecido(norm(sa), b))
    if sb:
        mejor = max(mejor, parecido(a, norm(sb)))
    if sa and sb:
        mejor = max(mejor, parecido(norm(sa), norm(sb)))
    return mejor


# ---------------------------------------------------------------- comparador

# Umbral mínimo de similitud de título para que valga la pena consultar a
# Bonsai: por debajo, los trabajos son visiblemente distintos. Bonsai SOLO
# clasifica (WARN/MISMATCH) o acepta candidatos cercanos ya resueltos en
# doi2bib.org; nunca genera datos.
BONSAI_SIM_MIN = 0.60

# Umbral de aceptación determinista plena (sin LLM) del título.
SIM_ACEPTA = 0.90

# Guardia de solapamiento de palabras antes de que Bonsai acepte un
# candidato: el título más corto debe estar mayormente contenido en el
# largo (>=0.60; las siglas desarrolladas —MRI/UTE— bajan el ratio) y el
# largo conservar una mayoría (>=0.55). Con min() a secas, un título remoto
# ultra-corto ("Osteoporosis") dentro de uno local largo daba 1.0 y era un
# capítulo de enciclopedia distinto (falso real del lote 321).
SOLAPA_CORTO = 0.60
SOLAPA_LARGO = 0.55

# Vía para palabras intercaladas/reordenadas: SequenceMatcher castiga una
# sola palabra en el medio ("...MRI FRACTURE (Fast Field Echo...") a sim
# 0.47 aunque el 90% de las palabras coincidan (caso real Cui2023).
SIM_SOLAPA_MIN = 0.45
SOLAPA_FUERTE = 0.80

_STOPWORDS = {"of", "the", "a", "an", "in", "for", "and", "with", "to",
              "on", "by", "from", "at", "as", "de", "la", "el", "y", "en"}


def solapamiento_palabras(a_raw, b_raw):
    """(solapa_local, solapa_remoto): fracción de palabras útiles de CADA
    título presentes en el otro. Bidireccional porque un título remoto
    ultra-corto contenido en el local ("Osteoporosis" dentro de "Osteoporosis
    imaging: state of the art...") da containment 1.0 con min() y era un
    capítulo de enciclopedia distinto (falso real del lote 321)."""
    ta = {t for t in norm(a_raw).split() if len(t) >= 3 and t not in _STOPWORDS}
    tb = {t for t in norm(b_raw).split() if len(t) >= 3 and t not in _STOPWORDS}
    if not ta or not tb:
        return 0.0, 0.0
    inter = len(ta & tb)
    return inter / len(ta), inter / len(tb)


def _titulos_compatibles(t_raw_loc, t_raw_rem):
    """Guardia determinista antes de que Bonsai acepte un candidato:
    los títulos deben contenerse mutuamente en proporción razonable, y un
    'Book Review' del trabajo citado no es el trabajo citado."""
    l = norm(t_raw_loc)
    r = norm(t_raw_rem)
    if "book review" in r and "book review" not in l:
        return False
    s_loc, s_rem = solapamiento_palabras(t_raw_loc, t_raw_rem)
    corto, largo = (s_loc, s_rem) if s_loc <= s_rem else (s_rem, s_loc)
    return corto >= SOLAPA_CORTO and largo >= SOLAPA_LARGO


def _anios_cercanos(y_loc, y_rem):
    """±1 año cuenta como compatible (doi2bib devuelve `issued` online y el
    .bib suele tener el año print; causa #1 de falsos MISMATCH)."""
    try:
        return abs(int(y_loc) - int(y_rem)) <= 1
    except (TypeError, ValueError):
        return y_loc == y_rem


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

    t_raw_loc = valor_campo(cuerpo_local, "title")
    t_raw_rem = valor_campo(cuerpo_remoto, "title")
    t_loc, t_rem = norm(t_raw_loc or ""), norm(t_raw_rem or "")
    sim = parecido_titulo(t_raw_loc, t_raw_rem) if t_loc and t_rem else 0.0
    if t_loc and t_rem:
        if sim < SIM_ACEPTA:
            estado = "MISMATCH"
            detalles.append("title local='%s' vs doi2bib='%s' (sim %.2f)"
                            % (t_loc[:70], t_rem[:70], sim))
        elif sim < 0.985:
            detalles.append("title casi identico (sim %.2f)" % sim)

    a_loc = apellidos(valor_campo(cuerpo_local, "author"))
    a_rem = apellidos(valor_campo(cuerpo_remoto, "author"))
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
        if _anios_cercanos(y_loc, y_rem):
            # print vs online: mismo trabajo, campo a reparar con doi2bib.
            if estado == "OK":
                estado = "WARN"
            detalles.append("year local=%s vs doi2bib=%s (±1, online vs print)"
                            % (y_loc, y_rem))
        elif sim >= SIM_ACEPTA and (a_loc & a_rem):
            # DOI original con título y autores coincidentes: es el mismo
            # trabajo con el año mal citado en el .bib (kanis2009meta:
            # citado 2009, real 2004) — manda doi2bib, se repara.
            if estado == "OK":
                estado = "WARN"
            detalles.append("year local=%s vs doi2bib=%s (año local erróneo; "
                            "corregir con doi2bib)" % (y_loc, y_rem))
        else:
            estado = "MISMATCH"
            detalles.append("year local=%s vs doi2bib=%s" % (y_loc, y_rem))

    for f in ("journal", "volume", "pages"):
        lv, rv = campo(cuerpo_local, f), campo(cuerpo_remoto, f)
        if lv and rv and parecido(lv, rv) < 0.75:
            if estado == "OK":
                estado = "WARN"
            detalles.append("%s local='%s' vs doi2bib='%s'" % (f, lv[:50], rv[:50]))

    # Bonsai se consulta en la franja dudosa del título y en CUALQUIER
    # MISMATCH con título razonablemente parecido Y que comparta palabras
    # (los MISMATCH por año/autores con sim 0.99-1.00 eran los falsos más
    # comunes; el solapamiento descarta títulos de sujetos distintos).
    if t_loc and t_rem and sim >= BONSAI_SIM_MIN:
        borde = _titulos_compatibles(t_raw_loc, t_raw_rem)
    elif t_loc and t_rem and sim >= SIM_SOLAPA_MIN:
        s_loc, s_rem = solapamiento_palabras(t_raw_loc, t_raw_rem)
        borde = s_loc >= SOLAPA_FUERTE and s_rem >= SOLAPA_FUERTE
    else:
        borde = False
    borde = bool(borde and (sim < 0.985 or estado == "MISMATCH"))
    return {"estado": estado, "detalles": detalles, "similitud": round(sim, 3),
            "borde": borde}


def coincide_busqueda(cuerpo_local, cuerpo_remoto, mismo_doi=False):
    """Aceptación de un candidato hallado por nombre.

    Devuelve (aceptado, res). aceptado=True SOLO con coincidencia
    determinista plena (política anti-invención): título >=0.90, año igual
    y algún autor coincidente (si ambos lados listan autores).

    Con mismo_doi=True el candidato ES el DOI que ya traía la entrada
    (reintento de un "roto"): el DOI define el trabajo, así que el año
    local deja de ser bloqueante — si el título y autores coinciden, el
    año del .bib era el erróneo y lo corrige doi2bib (kanis2009meta:
    citado 2009, real 2004).

    res["cercano"]=True marca candidatos que fallaron por poco (título
    >=0.60, año ±1, autores compatibles): esos van a Bonsai, que puede
    aceptarlos como el mismo trabajo (siempre ya resueltos en doi2bib).
    """
    res = comparar(cuerpo_local, cuerpo_remoto)
    t_raw_loc = valor_campo(cuerpo_local, "title") or ""
    t_raw_rem = valor_campo(cuerpo_remoto, "title") or ""
    sim = parecido_titulo(t_raw_loc, t_raw_rem)
    # Piso mínimo absoluto: por debajo ni la vía de solapamiento aplica.
    if sim < SIM_SOLAPA_MIN:
        return False, res
    a_loc = apellidos(valor_campo(cuerpo_local, "author"))
    a_rem = apellidos(valor_campo(cuerpo_remoto, "author"))
    autores_ok = not (a_loc and a_rem) or bool(a_loc & a_rem)
    y_loc = anio_de(valor_campo(cuerpo_local, "year"))
    y_rem = anio_de(valor_campo(cuerpo_remoto, "year"), valor_campo(cuerpo_remoto, "issued"))
    anio_igual = not (y_loc and y_rem) or y_loc == y_rem
    anio_cercano = not (y_loc and y_rem) or _anios_cercanos(y_loc, y_rem)

    if sim >= SIM_ACEPTA and autores_ok and (anio_igual or mismo_doi):
        return True, res
    s_loc, s_rem = solapamiento_palabras(t_raw_loc, t_raw_rem)
    por_solapamiento = (s_loc >= SOLAPA_FUERTE and s_rem >= SOLAPA_FUERTE
                        and sim >= SIM_SOLAPA_MIN)
    if ((sim >= BONSAI_SIM_MIN and autores_ok and anio_cercano
            and _titulos_compatibles(t_raw_loc, t_raw_rem))
            or (por_solapamiento and autores_ok and anio_cercano)):
        motivos = []
        if sim < SIM_ACEPTA:
            motivos.append("título sim %.2f < %.2f" % (sim, SIM_ACEPTA))
        if not anio_igual:
            motivos.append("año %s vs %s" % (y_loc, y_rem))
        res["cercano"] = True
        res["motivo_cercano"] = "; ".join(motivos) or "diferencias menores"
    return False, res
