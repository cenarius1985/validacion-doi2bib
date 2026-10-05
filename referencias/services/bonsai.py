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


def _post_chat(system, user, timeout=None):
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
                "max_tokens": 500,
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
    """Para casos dudosos (similitud 0.85-0.92) decide WARN o MISMATCH.

    Devuelve dict(veredicto, motivo) o None. El veredicto NUNCA crea datos:
    solo degrada/mejora la clasificación con argumentos.
    """
    user = (
        "¿Estas dos referencias bibliograficas son el MISMO trabajo?\n\n"
        f"LOCAL: {json.dumps(ref, ensure_ascii=False)}\n"
        f"DOI2BIB (autoridad): {bibtex_doi2bib[:900]}\n"
        f"Similitud de titulo calculada: {similitud:.3f}\n"
        f"Diferencias detectadas: {diferencias}\n\n"
        'Responde: {"veredicto": "mismo" | "distinto" | "indeciso", '
        '"motivo": "<explicacion breve en espanol>"}'
    )
    contenido = _post_chat(SYSTEM, user)
    v = _json_de_respuesta(contenido)
    if not v or v.get("veredicto") not in ("mismo", "distinto", "indeciso"):
        return None
    return v
