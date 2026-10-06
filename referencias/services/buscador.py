# -*- coding: utf-8 -*-
"""Búsqueda de DOIs candidatos POR NOMBRE para entradas sin DOI (o con DOI roto).

Fuentes, todas reales (política anti-invención, nada se genera):
  1. Google (Playwright headless) con fallback DuckDuckGo HTML y Bing.
  2. api.crossref.org — registro oficial de DOIs, consulta por metadatos.

Todo candidato es luego VERIFICADO en doi2bib por el pipeline antes de
aceptarse; aquí solo se recaudan.
"""
import asyncio
import re
import urllib.parse

import requests

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>\\]+", re.I)
CROSSREF = "https://api.crossref.org/works"
UA_NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")


def limpiar_doi(s):
    return s.rstrip(".,;)]}\"'").rstrip()


def extraer_dois(texto):
    """DOIs únicos en orden de aparición desde texto/enlaces."""
    vistos, out = set(), []
    for m in DOI_RE.finditer(texto or ""):
        d = limpiar_doi(m.group(0))
        if d.lower() not in vistos:
            vistos.add(d.lower())
            out.append(d)
    return out


async def _texto_pagina(page, url, espera=2500):
    """Carga una URL y devuelve (url_final, texto_visible, html)."""
    resp = await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(espera)
    try:
        texto = await page.inner_text("body")
    except Exception:  # noqa: BLE001
        texto = ""
    return page.url, texto or "", await resp.text() if resp else ""


async def _google(page, consulta):
    q = urllib.parse.quote_plus(consulta)
    url_final, texto, _ = await _texto_pagina(
        page, f"https://www.google.com/search?q={q}&num=20&hl=en")
    if "/sorry" in url_final or "unusual traffic" in texto.lower():
        return [], "google_bloqueado"
    return extraer_dois(texto), "google"


async def _duckduckgo(page, consulta):
    q = urllib.parse.quote_plus(consulta)
    _, texto, html = await _texto_pagina(
        page, f"https://html.duckduckgo.com/html/?q={q}")
    return extraer_dois(texto + " " + html), "duckduckgo"


async def _bing(page, consulta):
    q = urllib.parse.quote_plus(consulta)
    _, texto, _ = await _texto_pagina(
        page, f"https://www.bing.com/search?q={q}&count=20")
    return extraer_dois(texto), "bing"


async def buscar_en_web(context, consulta, sem, delay):
    """Google → DuckDuckGo → Bing. Devuelve (dois[], fuentes[]).
    Los bloqueos/fallos de cada motor quedan registrados para la salud
    del lote (F3-3: reiniciar_salud() / resumen_salud())."""
    async with sem:
        # el user-agent ya viene en el context (lo fija el pipeline)
        page = await context.new_page()
        try:
            fuentes, todos = [], []
            for motor in (_google, _duckduckgo, _bing):
                try:
                    dois, fuente = await motor(page, consulta)
                except Exception as e:  # noqa: BLE001
                    dois, fuente = [], f"{motor.__name__}_error"
                    FALLOS_WEB.append(fuente)
                if fuente == "google_bloqueado":
                    FALLOS_WEB.append("google_bloqueado")
                if dois:
                    todos.extend(dois)
                    fuentes.append(fuente)
                if len(todos) >= 5 or (dois and motor is _google):
                    break
                await asyncio.sleep(delay)
            # dedupe preservando orden
            vistos, out = set(), []
            for d in todos:
                if d.lower() not in vistos:
                    vistos.add(d.lower())
                    out.append(d)
            return out, fuentes
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass


# F3-3: registro de fallos de motores web de la corrida en curso.
FALLOS_WEB = []


def reiniciar_salud():
    FALLOS_WEB.clear()


def resumen_salud():
    from collections import Counter
    return dict(Counter(FALLOS_WEB))


def crossref_candidatos(titulo, autor=None, anio=None, maximo=5):
    """Candidatos oficiales por metadatos (API pública Crossref). Sync."""
    try:
        r = requests.get(
            CROSSREF,
            params={
                "query.bibliographic": titulo or "",
                "query.author": autor or "",
                "rows": maximo,
                "select": "DOI,title,author,issued,container-title",
            },
            headers={"User-Agent": "validacion-doi2bib/1.0 (mailto:local@localhost)"},
            timeout=25,
        )
        r.raise_for_status()
        out = []
        for it in r.json().get("message", {}).get("items", []):
            autores = ", ".join(
                a.get("family", "") for a in it.get("author", [])[:6])
            out.append({
                "doi": it.get("DOI", ""),
                "titulo": (it.get("title") or [""])[0],
                "autor": autores,
                "anio": (it.get("issued", {}).get("date-parts") or [[None]])[0][0],
                "journal": (it.get("container-title") or [""])[0],
                "fuente": "crossref",
            })
        return out
    except Exception:  # noqa: BLE001
        return []


_UA_CR = {"User-Agent": "validacion-doi2bib/1.0 (mailto:local@localhost)"}


def _invertir_autores(nombre_compuesto):
    """'Given Family and Given2 Family2' -> 'Family, Given and ...'."""
    partes = []
    for a in (nombre_compuesto or "").split(" and "):
        trozos = a.strip().rsplit(" ", 1)
        partes.append("%s, %s" % (trozos[-1], trozos[0])
                      if len(trozos) == 2 else a.strip())
    return " and ".join(p for p in partes if p)


def _cuerpo_bibtex(clave, titulo, autores, anio, journal):
    campos = [("title", titulo), ("author", autores), ("year", str(anio or "")),
              ("journal", journal)]
    return "@article{%s,\n%s\n}" % (clave, ",\n".join(
        "  %s = {%s}" % (c, v) for c, v in campos if v))


# F2-2: estas "referencias" existen pero NO son el trabajo citado.
NO_ARTICULO_RE = re.compile(
    r"erratum|corrigendum|correction (?:to|for|of)|publisher'?s? note"
    r"|author accept(?:ed)? manuscript|retraction (?:of|for|notice)"
    r"|discussion (?:of|to)\b", re.I)


def es_no_articulo(titulo):
    """True si el título corresponde a una errata/corrección/retracción,
    que jamás debe aceptarse como el trabajo citado."""
    return bool(NO_ARTICULO_RE.search(titulo or ""))


def openalex_obtener(doi):
    """F3-1: registro INDEPENDIENTE del DOI en OpenAlex (segunda fuente que
    no depende de Crossref). Cuerpo BibTeX-like o None."""
    try:
        r = requests.get(
            "https://api.openalex.org/works/doi:" + doi.strip(),
            params={"mailto": "local@localhost"}, timeout=25)
        if r.status_code != 200:
            return None
        m = r.json()
        autores = " and ".join(
            (a.get("author", {}) or {}).get("display_name", "")
            for a in m.get("authorships", [])[:20])
        journal = ((m.get("primary_location", {}) or {}).get("source", {})
                   or {}).get("display_name", "") or ""
        return _cuerpo_bibtex("openalex", m.get("display_name", ""),
                              _invertir_autores(autores),
                              m.get("publication_year", ""), journal)
    except Exception:  # noqa: BLE001
        return None


def confirmacion_independiente(doi, bibtex_aceptado):
    """F2-1: segunda fuente independiente (OpenAlex; fallback Crossref)
    debe concordar con lo aceptado: solapamiento fuerte de palabras del
    título en ambos sentidos + año igual o ±1.

    Devuelve (resultado, fuente): resultado True/False; (None, motivo) si
    ninguna fuente independiente estuvo disponible."""
    from . import bib
    for getter, nombre in ((openalex_obtener, "OpenAlex"),
                           (crossref_obtener, "Crossref")):
        cuerpo = getter(doi)
        if not cuerpo:
            continue
        t_acept = bib.valor_campo(bibtex_aceptado, "title") or ""
        t_ind = bib.valor_campo(cuerpo, "title") or ""
        s_loc, s_rem = bib.solapamiento_palabras(t_acept, t_ind)
        y_acept = bib.anio_de(bib.valor_campo(bibtex_aceptado, "year"),
                              bib.valor_campo(bibtex_aceptado, "issued"))
        y_ind = bib.anio_de(bib.valor_campo(cuerpo, "year"),
                            bib.valor_campo(cuerpo, "issued"))
        anio_ok = not (y_acept and y_ind) or abs(int(y_acept) - int(y_ind)) <= 1
        ok = (min(s_loc, s_rem) >= bib.SOLAPA_FUERTE
              and bib.parecido_titulo(t_acept, t_ind) >= bib.SIM_SOLAPA_MIN
              and anio_ok)
        return ok, nombre
    return None, "sin fuente independiente disponible"


def _bibtex_de_crossref(m):
    """Arma un cuerpo BibTeX-like con los metadatos oficiales de Crossref."""
    def uno(campo):
        return (m.get(campo) or [""])[0] if isinstance(m.get(campo), list) else m.get(campo) or ""
    autores = " and ".join(
        "%s, %s" % (a.get("family", ""), a.get("given", ""))
        for a in m.get("author", []))
    anio = ((m.get("issued", {}) or {}).get("date-parts") or [[None]])[0][0] or ""
    campos = [
        ("title", uno("title")),
        ("author", autores),
        ("year", str(anio)),
        ("journal", uno("container-title")),
        ("volume", m.get("volume", "")),
        ("number", m.get("issue", "")),
        ("pages", m.get("page", "")),
    ]
    return "@article{crossref,\n%s\n}" % ",\n".join(
        "  %s = {%s}" % (c, v) for c, v in campos if v)


def crossref_obtener(doi):
    """Registro OFICIAL del DOI en Crossref como cuerpo BibTeX-like.

    doi2bib es solo un front de Crossref: cuando no sirve un DOI que sí
    existe (BROKEN falsos), este es el registro de autoridad. None si
    Crossref tampoco lo tiene.
    """
    import urllib.parse
    try:
        r = requests.get(CROSSREF + "/" + urllib.parse.quote(doi, safe="/"),
                         headers=_UA_CR, timeout=25)
        if r.status_code != 200:
            return None
        return _bibtex_de_crossref(r.json().get("message", {}) or {})
    except Exception:  # noqa: BLE001
        return None


_TITULOS_NO_ARTICULO = re.compile(
    r"just a moment|attention required|access denied|checking your browser"
    r"|redirecting|are you a robot|captcha|log ?in|sign in", re.I)


async def _leer_metas(page):
    return await page.evaluate(
        """() => {
            const g = (n) => {
                const m = document.querySelector('meta[name="' + n + '"]');
                return m ? m.content : '';
            };
            const autores = Array.from(
                document.querySelectorAll('meta[name="citation_author"]'))
                .map(m => m.content).join(' and ');
            return {
                t: g('citation_title') || document.title || '',
                a: autores,
                y: g('citation_publication_date')
                   || g('citation_date') || g('citation_online_date') || '',
                j: g('citation_journal_title')
                   || g('citation_conference_title') || '',
            };
        }""")


def _titulo_util(t):
    """True si parece un título de artículo y no un challenge/placeholder
    (Cloudflare 'Just a moment...', páginas de login, redirecciones)."""
    if not t:
        return False
    if _TITULOS_NO_ARTICULO.search(t):
        return False
    return len([p for p in t.split() if len(p) >= 3]) >= 3


async def metadatos_pagina(context, sem, doi):
    """Abre https://doi.org/<doi> y lee los meta tags citation_* de la
    página REAL del artículo (Wiley/Springer/Elsevier los publican).

    Última instancia cuando ni doi2bib ni Crossref sirven el DOI pero el
    navegador sí llega al artículo. Devuelve un cuerpo BibTeX-like o None.
    """
    async with sem:
        page = await context.new_page()
        try:
            await page.goto("https://doi.org/" + doi, timeout=35000,
                            wait_until="domcontentloaded")
            metas = None
            # Algunos editores (Wiley/Cloudflare) tardan en renderizar los
            # meta tags: dos lecturas, la segunda tras espera mayor.
            for espera in (2500, 9000):
                await page.wait_for_timeout(espera)
                try:
                    metas = await _leer_metas(page)
                except Exception:  # noqa: BLE001
                    return None
                if metas and _titulo_util(metas.get("t", "")):
                    break
                metas = None
            if not metas:
                return None
            # citation_author viene "Given Family": invertir a "Family, Given"
            autores = ""
            if metas.get("a"):
                partes = []
                for a in metas["a"].split(" and "):
                    trozos = a.rsplit(" ", 1)
                    partes.append("%s, %s" % (trozos[-1], trozos[0])
                                  if len(trozos) == 2 else a)
                autores = " and ".join(partes)
            m_anio = re.search(r"\d{4}", metas.get("y") or "")
            campos = [
                ("title", metas.get("t", "")),
                ("author", autores),
                ("year", m_anio.group(0) if m_anio else ""),
                ("journal", metas.get("j", "")),
            ]
            return "@article{pagina,\n%s\n}" % ",\n".join(
                "  %s = {%s}" % (c, v) for c, v in campos if v)
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass


async def candidatos(context, sem, delay, titulo, autor, anio):
    """Fusiona candidatos de buscador web + Crossref.

    Devuelve lista de dicts {doi, fuente} sin dedupe cruzado aún (el pipeline
    deduplica contra el ranking).
    """
    consulta = f'"{titulo}"'
    if autor:
        primer = re.split(r"\s+and\s+|,", autor)[0].strip()
        consulta += f" {primer}"
    consulta += " doi"
    dois_web, fuentes = await buscar_en_web(context, consulta, sem, delay)
    cr = await asyncio.to_thread(crossref_candidatos, titulo, autor, anio)

    vistos, out = set(), []
    for d in dois_web:
        if d.lower() not in vistos:
            vistos.add(d.lower())
            out.append({"doi": d, "fuente": "+".join(fuentes) or "web"})
    for c in cr:
        if c["doi"] and c["doi"].lower() not in vistos:
            vistos.add(c["doi"].lower())
            out.append({"doi": c["doi"], "fuente": "crossref", **c})
    return out
