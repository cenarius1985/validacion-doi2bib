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
    """Google → DuckDuckGo → Bing. Devuelve (dois[], fuentes[])."""
    async with sem:
        # el user-agent ya viene en el context (lo fija el pipeline)
        page = await context.new_page()
        try:
            fuentes, todos = [], []
            for motor in (_google, _duckduckgo, _bing):
                try:
                    dois, fuente = await motor(page, consulta)
                except Exception:  # noqa: BLE001
                    dois, fuente = [], f"{motor.__name__}_error"
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
