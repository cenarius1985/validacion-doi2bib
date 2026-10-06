# -*- coding: utf-8 -*-
"""Cliente del LLM local Bonsai (Ternary Bonsai 8B, API OpenAI-compatible).

Servicio existente en el compose `ollamaLocal` del host, puerto 4687
(README verificado 2026-10-02): POST /v1/chat/completions, modelo
`ternary-bonsai-8b`, sin API key.

REGLA ANTI-INVENCION: Bonsai SOLO clasifica y rankea con la información que
se le entrega. Nunca se le pide (ni se le acepta) metadata generada; todo
DOI que el sistema acepta fue verificado antes en doi2bib.org.
"""
import json
import re

import requests
from django.conf import settings


def _post_chat(system, user, timeout=None, max_tokens=None):
    try:
        r = requests.post(
            settings.BONSAI_BASE_URL.rstrip("/") + "/chat/completions",
            json={
                "model": settings.BONSAI_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                # El análisis paso a paso de adjudicar_borde consume tokens
                # antes del JSON: 500 se quedaba corto y cortaba la respuesta.
                "max_tokens": max_tokens or 500,
                "stream": False,
            },
            timeout=timeout or settings.BONSAI_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return None


def _json_de_respuesta(texto):
    """Extrae el primer objeto JSON balanceado; soporta <think>...</think>."""
    if not texto:
        return None
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    ini = texto.find("{")
    while ini != -1:
        depth = 0
        for i in range(ini, len(texto)):
            if texto[i] == "{":
                depth += 1
            elif texto[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(texto[ini:i + 1])
                    except json.JSONDecodeError:
                        break
        ini = texto.find("{", ini + 1)
    return None


def disponible():
    """Ping liviano a /models para saber si Bonsai está arriba."""
    try:
        r = requests.get(settings.BONSAI_BASE_URL.rstrip("/") + "/models",
                         timeout=4)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


SYSTEM = ("Eres un asistente bibliografico. Respondes EXCLUSIVAMENTE con un "
          "objeto JSON valido, sin texto adicional. Nunca inventas datos: si "
          "no puedes decidir con la informacion dada, respondes "
          '{"decision": "indeciso"}.')


def rankear_candidatos(ref, candidatos):
    """Ordena candidatos DOI para una entrada sin DOI.

    ref: dict(titulo, autor, anio, journal, clave)
    candidatos: lista de dicts con doi/titulo/fuente...
    Devuelve (lista_ordenada, veredicto_json) o (None, None) si falla.
    """
    if not candidatos:
        return None, None
    cand_txt = json.dumps(
        [{"i": i, "doi": c["doi"], "titulo": c.get("titulo", ""),
          "autor": c.get("autor", ""), "anio": c.get("anio", ""),
          "fuente": c.get("fuente", "")} for i, c in enumerate(candidatos)],
        ensure_ascii=False)
    user = (
        "Entrada BibTeX sin DOI verificable:\n"
        f"titulo: {ref.get('titulo') or ''}\n"
        f"autor: {ref.get('autor') or ''}\n"
        f"anio: {ref.get('anio') or ''}\n"
        f"journal: {ref.get('journal') or ''}\n\n"
        f"Candidatos (JSON): {cand_txt}\n\n"
        'Responde: {"orden": [indices por similitud], '
        '"motivo": "<explicacion breve en espanol>"}'
    )
    contenido = _post_chat(SYSTEM, user)
    veredicto = _json_de_respuesta(contenido)
    if not veredicto or "orden" not in veredicto:
        return None, None
    orden = [i for i in veredicto["orden"]
             if isinstance(i, int) and 0 <= i < len(candidatos)]
    if not orden:
        return None, None
    resto = [i for i in range(len(candidatos)) if i not in orden]
    return [candidatos[i] for i in orden + resto], veredicto


def adjudicar_borde(ref, bibtex_doi2bib, similitud, diferencias):
    """Para casos sin coincidencia exacta decide si es el MISMO trabajo.

    Se usa en dos sitios: (a) fase A, veredictos WARN/MISMATCH dudosos;
    (b) fase B, candidatos "cercanos" que la puerta determinista no aceptó.
    Devuelve dict(veredicto, motivo) o None. El veredicto NUNCA crea datos:
    solo reclasifica; todo DOI aceptado ya resolvió en doi2bib.org.

    La evidencia (autores comunes, diferencia de años) se pre-calcula aquí
    de forma determinista: el modelo pequeño acierta mucho más clasificando
    diferencias ya enumeradas que parseando BibTeX crudo.
    """
    from . import bib as _bib
    ap_loc = _bib.apellidos(ref.get("autor", ""))
    ap_rem = _bib.apellidos(_bib.valor_campo(bibtex_doi2bib, "author") or "")
    comunes = sorted(ap_loc & ap_rem) if ap_loc and ap_rem else []
    t_rem = _bib.valor_campo(bibtex_doi2bib, "title") or ""
    s_loc, s_rem = _bib.solapamiento_palabras(ref.get("titulo", ""), t_rem)
    y_loc = _bib.anio_de(ref.get("anio", ""))
    y_rem = _bib.anio_de(_bib.valor_campo(bibtex_doi2bib, "year") or "",
                         _bib.valor_campo(bibtex_doi2bib, "issued") or "")
    dif_anio = ""
    if y_loc and y_rem and y_loc != y_rem:
        try:
            dif_anio = "%s vs %s (diferencia %d)" % (y_loc, y_rem,
                                                     abs(int(y_loc) - int(y_rem)))
        except ValueError:
            dif_anio = "%s vs %s" % (y_loc, y_rem)

    user = (
        "¿Estas dos referencias bibliograficas son el MISMO trabajo?\n\n"
        f"TITULO LOCAL: {ref.get('titulo', '')}\n"
        f"TITULO DOI2BIB: {t_rem}\n"
        f"Solapamiento de palabras del titulo: local {s_loc:.2f} / doi2bib "
        f"{s_rem:.2f} (altos = mismas palabras, p.ej. solo cambia un "
        "subtitulo; bajo en un lado = un titulo esta contenido en otro y "
        "puede ser un trabajo distinto, p.ej. un capitulo o una resena)\n"
        f"APELLIDOS EN COMUN: {comunes or 'ninguno'} "
        f"(local: {sorted(ap_loc)}; doi2bib: {sorted(ap_rem)})\n"
        f"ANO LOCAL: {y_loc or '?'}   ANO DOI2BIB: {y_rem or '?'} "
        f"{('  DIFIEREN ' + dif_anio + ' = cambio MENOR, online vs impresa') if dif_anio else '(iguales)'}\n"
        f"Similitud de titulo calculada: {similitud:.3f}\n"
        f"Diferencias detectadas por el comparador: {diferencias}\n\n"
        "Ya verificado antes de consultarte (NO lo uses como evidencia en "
        "contra): los autores son compatibles"
        f"{' (apellidos en comun: ' + ', '.join(comunes) + ')' if comunes else ''}"
        f" y el ano difiere a lo sumo en 1{' (' + dif_anio + ')' if dif_anio else ''}, "
        "ambos son cambios MENORES que no cambian la identidad de un trabajo.\n\n"
        "Tu unica decision: ¿la diferencia de TITULO es un cambio menor "
        "(puntuacion, mayusculas/acentos, subtitulo anadido o quitado, "
        "abreviaturas, palabras recortadas) o revela OTRO trabajo distinto "
        "(otro metodo, otra parte del cuerpo, otra poblacion, 'part 2', "
        "titulo sustancialmente diferente)?\n\n"
        "REGLA: con autores compatibles y ano ±1, responde 'mismo' salvo "
        "evidencia clara de un trabajo distinto en el titulo. Si no puedes "
        "decidir, 'indeciso'.\n\n"
        'Responde: {"veredicto": "mismo" | "distinto" | "indeciso", '
        '"motivo": "<explicacion breve en espanol>"}'
    )
    contenido = _post_chat(SYSTEM, user, max_tokens=900, timeout=None)
    v = _json_de_respuesta(contenido)
    if not v or v.get("veredicto") not in ("mismo", "distinto", "indeciso"):
        return None
    return v
