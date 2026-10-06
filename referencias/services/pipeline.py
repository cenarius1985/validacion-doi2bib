# -*- coding: utf-8 -*-
"""Orquestador del lote: fases A (con DOI) y B (búsqueda por nombre), informe
markdown y .bib corregido REPARADO.

Política del autor (2026-09-04, ampliada 2026-10-05): en discrepancia de
campos, MANDA DOI.ORG (doi2bib); el objetivo es REPARAR referencias
incorrectas, no solo marcarlas. Se corrigen campos, jamás claves. El .bib
original nunca se modifica: `corregido.bib` es una propuesta descargable.

Anti-invención: ningún DOI se acepta sin resolver PRIMERO en doi2bib.org.
La coincidencia EXACTA (determinista, título sim>=0.90 + año + autores)
sigue siendo la puerta principal; lo que no entra exacto pero está cerca
(título>=0.60, año±1, autores compatibles) lo ADJUDICA Bonsai (LLM local),
que puede aceptarlo como el mismo trabajo pero jamás genera datos. Si Bonsai
está caído, todo sigue funcionando sin LLM (degradación elegante).
"""
import asyncio
import re
import traceback
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import close_old_connections
from django.utils import timezone
from playwright.async_api import async_playwright

from . import bib, bonsai, buscador
from .doi2bib import CHROMIUM_ARGS, ClienteDoi2Bib, RateLimitGlobal
from ..models import Lote, Referencia

CAMPOS_AUTORIDAD = ("author", "journal", "year", "volume", "number", "pages", "doi")


async def _db(fn, *args, **kwargs):
    """Toda operación ORM dentro del pipeline async pasa por un hilo:
    Django prohíbe ORM síncrono en contexto async."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _log(lote, msg):
    marca = datetime.now().strftime("%H:%M:%S")
    lote.log = (lote.log + f"\n[{marca}] {msg}").lstrip("\n")
    await _db(lote.save, update_fields=["log"])


async def _avanzar(lote):
    n = await _db(lambda: lote.referencias.exclude(estado="PENDIENTE").count())
    lote.procesadas = n
    await _db(lote.save, update_fields=["procesadas"])


# --------------------------------------------------------------- fase A y B

async def _procesar_con_doi(ref, cliente, sem, bonsai_on, lote,
                            sem_bonsai=None, context=None, sem_web=None):
    try:
        async with sem:
            r = await cliente.consultar(ref.doi)
        bibtex, fuente = None, "doi2bib"
        if r["estado"] == "bibtex":
            bibtex = r["bibtex"]
        elif r["estado"] in ("not_found", "error"):
            # doi2bib es un front de Crossref: cuando no sirve un DOI que
            # sí existe (o se cuelga), la autoridad es el registro oficial;
            # y si tampoco, la página real del artículo (doi.org → editor).
            cr = await asyncio.to_thread(buscador.crossref_obtener, ref.doi)
            if cr:
                bibtex, fuente = cr, "Crossref (doi2bib no lo sirve)"
            elif context is not None:
                pag = await buscador.metadatos_pagina(context, sem_web, ref.doi)
                if pag:
                    bibtex, fuente = pag, "página del artículo vía doi.org"
        if bibtex:
            ref.doi2bib_bibtex = bibtex
            res = bib.comparar(ref.cuerpo, bibtex)
            ref.similitud = res["similitud"]
            ref.estado, ref.detalle = res["estado"], "; ".join(res["detalles"])
            if fuente != "doi2bib":
                ref.detalle = ("verificado vía %s; %s" %
                               (fuente, ref.detalle)).strip("; ")
            if res["borde"] and bonsai_on:
                campos = {"titulo": ref.titulo, "autor": ref.autor,
                          "anio": ref.anio, "journal": ref.journal}
                if sem_bonsai is not None:
                    async with sem_bonsai:
                        v = await asyncio.to_thread(
                            bonsai.adjudicar_borde, campos, bibtex,
                            res["similitud"], res["detalles"])
                else:
                    v = await asyncio.to_thread(
                        bonsai.adjudicar_borde, campos, bibtex,
                        res["similitud"], res["detalles"])
                if v:
                    ref.veredicto_bonsai = v
                    if v.get("veredicto") == "mismo" and ref.estado == "MISMATCH":
                        ref.estado = "WARN"
                        ref.detalle += " | Bonsai: mismo trabajo (%s)" % v.get("motivo", "")
                    elif v.get("veredicto") == "distinto" and ref.estado != "MISMATCH":
                        ref.estado = "MISMATCH"
                        ref.detalle += " | Bonsai: trabajos distintos"
        elif r["estado"] == "rate_limit":
            ref.estado = "RATE_LIMIT"
            ref.detalle = r["detalle"] + " (usar Reprocesar más tarde)"
        elif r["estado"] == "not_found":
            ref.estado = "BROKEN"
            ref.detalle = ("no resuelve en doi2bib, Crossref ni la página "
                           "del artículo (%s)" % r["detalle"])
        else:
            ref.estado, ref.detalle = "ERROR", r["detalle"]
    except Exception as e:  # noqa: BLE001
        ref.estado, ref.detalle = "ERROR", ("excepción: %s" % e)[:250]
    finally:
        ref.verificada = timezone.now()
        await _db(ref.save)
        await _avanzar(lote)


async def _procesar_busqueda(ref, context, sem_web, sem_tabs, cliente,
                             bonsai_on, lote, delay, n_verificar, sem_bonsai):
    estado_inicial = ref.estado  # SIN_DOI, BROKEN o MISMATCH
    try:
        reutilizados = bool(ref.candidatos)
        if reutilizados:
            # Reproceso: reutiliza los candidatos ya recaudados y rankeados
            # (la búsqueda web es cara y propensa a bloqueos; los DOI no
            # cambian). Solo quedan las adjudicaciones de cercanos.
            cands = ref.candidatos
        else:
            cands = await buscador.candidatos(context, sem_web, delay,
                                              ref.titulo, ref.autor, ref.anio)
        if not cands:
            ref.estado = estado_inicial
            ref.detalle = ((ref.detalle + " | ") if ref.detalle else "") + \
                "búsqueda por nombre sin candidatos"
        else:
            if estado_inicial == "BROKEN" and ref.doi and (
                    not cands or cands[0]["doi"].lower() != ref.doi.lower()):
                # Antes de buscar reemplazo: reintentar el DOI original con
                # la cascada (doi2bib → Crossref → página real); varios
                # "rotos" son falsos que doi2bib no sirve.
                cands = [{"doi": ref.doi, "fuente": "reintento-doi-original"}] + list(cands)
            if bonsai_on and len(cands) > 1 and not reutilizados:
                campos = {"titulo": ref.titulo, "autor": ref.autor,
                          "anio": ref.anio, "journal": ref.journal,
                          "clave": ref.clave}
                async with sem_bonsai:
                    ordenados, v = await asyncio.to_thread(
                        bonsai.rankear_candidatos, campos, cands)
                if ordenados:
                    cands = ordenados
                    ref.veredicto_bonsai = {"ranking": v}
            ref.candidatos = cands[:12]
            aceptado, descartes, consultas_bonsai = None, [], 0
            for c in cands[:n_verificar]:
                async with sem_tabs:
                    rr = await cliente.consultar(c["doi"])
                if rr["estado"] == "rate_limit":
                    ref.estado = "RATE_LIMIT"
                    ref.detalle = rr["detalle"] + " (usar Reprocesar más tarde)"
                    break
                bibtex, fuente = None, "doi2bib"
                if rr["estado"] == "bibtex":
                    bibtex = rr["bibtex"]
                else:
                    # Candidato que doi2bib no sirve: registro oficial Crossref
                    cr = await asyncio.to_thread(buscador.crossref_obtener,
                                                 c["doi"])
                    if cr:
                        bibtex, fuente = cr, "Crossref"
                if not bibtex:
                    descartes.append("%s: %s" % (c["doi"], rr["estado"]))
                    continue
                if (c["doi"].lower() == (ref.doi or "").lower()
                        and estado_inicial == "MISMATCH"):
                    # El candidato ES el DOI en disputa (apuntaba a otro
                    # trabajo): verificar contra sí mismo no resuelve nada.
                    # (Para BROKEN sí se reintenta: ahí el DOI no era el
                    # problema, el servicio que no lo servía.)
                    descartes.append("%s: es el DOI ya verificado en desacuerdo"
                                     % c["doi"])
                    continue
                ok, res = bib.coincide_busqueda(
                    ref.cuerpo, bibtex,
                    mismo_doi=c["doi"].lower() == (ref.doi or "").lower())
                if ok:
                    aceptado = (c, rr, res, None, bibtex, fuente)
                    break
                if (res.get("cercano") and bonsai_on
                        and consultas_bonsai < 2):
                    consultas_bonsai += 1
                    campos = {"titulo": ref.titulo, "autor": ref.autor,
                              "anio": ref.anio, "journal": ref.journal}
                    async with sem_bonsai:
                        v = await asyncio.to_thread(
                            bonsai.adjudicar_borde, campos, bibtex,
                            res["similitud"], [res.get("motivo_cercano", "")])
                    if v and v.get("veredicto") == "mismo":
                        # El DOI resolvió (doi2bib/Crossref) y Bonsai adjudicó
                        # la equivalencia pese a diferencias menores.
                        aceptado = (c, rr, res, v, bibtex, fuente)
                        break
                    if v:
                        descartes.append("%s: Bonsai %s" % (c["doi"],
                                                            v.get("veredicto")))
                    else:
                        descartes.append("%s: Bonsai sin respuesta"
                                         % c["doi"])
                    continue
                descartes.append("%s: sim %.2f" % (c["doi"], res["similitud"]))
            if aceptado:
                c, rr, res, v, bibtex, fuente = aceptado
                ref.estado = "HALLADO"
                ref.doi_propuesto = c["doi"]
                ref.doi2bib_bibtex = bibtex
                ref.similitud = res["similitud"]
                if c["doi"].lower() == (ref.doi or "").lower():
                    origen = "el DOI original SÍ resuelve (falso roto; verificado vía %s)" % fuente
                    # El año del .bib podía ser el erróneo: doi2bib manda.
                    extra = "; ".join(d for d in res["detalles"]
                                      if d.startswith("year"))
                    if extra:
                        origen += "; %s — corregido según doi2bib" % extra
                elif estado_inicial == "MISMATCH":
                    origen = "reemplaza DOI que apuntaba a otro trabajo"
                elif estado_inicial == "BROKEN":
                    origen = "reemplaza DOI roto"
                else:
                    origen = "entrada no tenía DOI"
                if v:
                    ref.veredicto_bonsai = v
                    ref.detalle = ("DOI verificado vía %s y ACEPTADO POR BONSAI "
                                   "(título sim %.2f; %s); %s" %
                                   (fuente, res["similitud"],
                                    v.get("motivo", ""), origen))
                else:
                    ref.detalle = ("DOI hallado vía %s y VERIFICADO en %s "
                                   "(título sim %.2f); %s" %
                                   (c.get("fuente", "web"), fuente,
                                    res["similitud"], origen))
            elif ref.estado != "RATE_LIMIT":
                ref.estado = "MANUAL"
                ref.detalle = ("candidatos hallados pero ninguno verificado "
                               "(%s); revisar a mano" % "; ".join(descartes[:4]))
    except Exception as e:  # noqa: BLE001
        ref.estado, ref.detalle = "ERROR", ("excepción: %s" % e)[:250]
    finally:
        ref.verificada = timezone.now()
        await _db(ref.save)
        await _avanzar(lote)


# ------------------------------------------------------------------- dedupe

# Calidad para elegir el sobreviviente de un grupo de duplicados.
_CALIDAD = {"OK": 0, "WARN": 1, "HALLADO": 2, "MISMATCH": 3, "BROKEN": 4,
            "RATE_LIMIT": 5, "ERROR": 6, "MANUAL": 7, "SIN_DOI": 8,
            "PENDIENTE": 9, "ELIMINAR": 10, "DUPLICADO": 11}


def _marcar_eliminables(lote):
    """Cierre de la política del autor (2026-10-06): si tras la búsqueda
    por NOMBRE no hay ninguna coincidencia verificable, la referencia está
    mal — probable alucinación — y se ELIMINA de corregido.bib (el
    original no se toca; el informe la lista para revisión). Cubre las
    MANUAL/MISMATCH que sobrevivieron al reintento y las SIN_DOI cuya
    búsqueda por nombre no arrojó candidatos."""
    qs = (lote.referencias.filter(estado__in=["MANUAL", "MISMATCH"])
          | lote.referencias.filter(estado="SIN_DOI",
                                    detalle__icontains="sin candidatos"))
    n = 0
    for r in qs:
        r.estado = "ELIMINAR"
        r.detalle = ((r.detalle + " | ") if r.detalle else "") + \
            ("sin coincidencia verificable por nombre: probable referencia "
             "inválida (alucinación); eliminada de corregido.bib")
        r.save(update_fields=["estado", "detalle"])
        n += 1
    return n


def _doi_final(ref):
    """DOI de identidad: el de la entrada o el propuesto (hallado y
    verificado) — ambos identifican el mismo trabajo. Minúsculas porque el
    DOI es case-insensitive (10.1359/JBMR.040134 == 10.1359/jbmr.040134)."""
    return (ref.doi or ref.doi_propuesto or "").strip().lower()


def deduplicar(lote):
    """Marca DUPLICADO a las entradas que comparten DOI.

    Política del autor (2026-10-06): el DOI es el ÚNICO identificador
    válido para eliminar repetidos — nunca se deduplica por título/autor.
    De cada grupo sobrevive la entrada mejor clasificada (desempate: la
    primera en el .bib); las demás se eliminan de corregido.bib.
    Devuelve la cantidad marcadas.
    """
    refs = list(lote.referencias.order_by("orden"))
    grupos = {}
    for r in refs:
        doi = _doi_final(r)
        if doi:
            grupos.setdefault(doi, []).append(r)
    n = 0
    for doi, grupo in grupos.items():
        if len(grupo) < 2:
            continue
        grupo.sort(key=lambda r: (_CALIDAD.get(r.estado, 11), r.orden))
        keeper = grupo[0]
        for r in grupo[1:]:
            if r.estado == "DUPLICADO":
                continue
            r.estado = "DUPLICADO"
            r.detalle = ((r.detalle + " | ") if r.detalle else "") + \
                ("duplicado por DOI de `%s` (%s); eliminado en corregido.bib"
                 % (keeper.clave, doi))
            r.save(update_fields=["estado", "detalle"])
            n += 1
    return n


# ------------------------------------------------------------------- orquesta

async def _reintentar_por_nombre(lote, refs, context, sem_web, sem_tabs,
                                 cliente, bonsai_on, sem_bonsai):
    """Cierre para MANUAL/MISMATCH con DOI: manda el NOMBRE.

    Si el título no coincide con lo que trae el DOI, el DOI está mal: se
    re-busca en la web POR EL NOMBRE (búsqueda fresca, no los candidatos
    viejos), se verifica cada candidato en la cascada doi2bib → Crossref →
    página real y se conserva lo que traiga el DOI CORRECTO (fase B
    estándar, con más candidatos verificados). Lo que no encuentre un DOI
    verificable queda en MANUAL para revisión humana — jamás se adopta el
    DOI equivocado (política del autor, 2026-10-06).
    """
    pendientes = [r for r in refs
                  if r.estado in ("MANUAL", "MISMATCH") and r.doi]
    if not pendientes:
        return 0
    for r in pendientes:
        r.estado = "MISMATCH"   # el DOI apuntaba a otro trabajo
        r.candidatos = []       # fuerza búsqueda fresca por el nombre local
        await _db(r.save, update_fields=["estado", "candidatos"])
    await asyncio.gather(*[
        _procesar_busqueda(r, context, sem_web, sem_tabs, cliente, bonsai_on,
                           lote, settings.BUSCADOR_DELAY,
                           settings.CANDIDATOS_A_VERIFICAR + 4, sem_bonsai)
        for r in pendientes])
    return sum(1 for r in pendientes if r.estado == "HALLADO")


async def _correr(lote_id):
    lote = await _db(Lote.objects.get, pk=lote_id)
    lote.estado = "en_curso"
    lote.iniciado = timezone.now()
    lote.procesadas = 0
    await _db(lote.save, update_fields=["estado", "iniciado", "procesadas"])
    refs = await _db(lambda: list(lote.referencias.order_by("orden")))

    limiter = RateLimitGlobal(settings.DOI2BIB_MIN_INTERVALO,
                              settings.DOI2BIB_PAUSA_RATE_LIMIT)
    async with async_playwright() as p:
        # HEADLESS SIEMPRE: sin ventana en el host, sin excepciones.
        browser = await p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        context = await browser.new_context(
            locale="en-US", user_agent=buscador.UA_NAVEGADOR)
        cliente = ClienteDoi2Bib(context, limiter, settings.DOI2BIB_REINTENTOS)
        sem_tabs = asyncio.Semaphore(max(1, lote.tabs))
        sem_web = asyncio.Semaphore(max(1, settings.BUSCADOR_TABS))
        # Bonsai en paralelo ilimitado satura el host (53 refs a la vez):
        # se serializa por semáforo global configurable.
        sem_bonsai = asyncio.Semaphore(max(1, settings.BONSAI_CONCURRENCIA))

        bonsai_on = lote.usar_bonsai and await asyncio.to_thread(bonsai.disponible)
        await _log(lote, "bonsai=%s (modelo %s)" %
                   ("activo" if bonsai_on else "OFF, degradación elegante",
                    settings.BONSAI_MODEL))
        await _log(lote, "doi2bib: %d pestañas, intervalo global %.1fs" %
                   (lote.tabs, settings.DOI2BIB_MIN_INTERVALO))

        # Fase A: referencias con DOI pendientes de verificar
        fase_a = [r for r in refs if r.doi and r.estado == "PENDIENTE"]
        if fase_a:
            await asyncio.gather(*[
                _procesar_con_doi(r, cliente, sem_tabs, bonsai_on, lote,
                                  sem_bonsai, context, sem_web)
                for r in fase_a])

        # Fase B: sin DOI, DOI roto, o DOI que quedó en MISMATCH (apunta a
        # otro trabajo): se busca el DOI correcto por nombre para reparar.
        if lote.buscar_por_nombre:
            fase_b = [r for r in refs if r.estado in ("SIN_DOI", "BROKEN",
                                                      "MISMATCH")]
            if fase_b:
                await _log(lote, "fase B: %d referencias a buscar por nombre"
                           % len(fase_b))
                await asyncio.gather(*[
                    _procesar_busqueda(r, context, sem_web, sem_tabs, cliente,
                                       bonsai_on, lote, settings.BUSCADOR_DELAY,
                                       settings.CANDIDATOS_A_VERIFICAR,
                                       sem_bonsai)
                    for r in fase_b])

        # Fase de cierre para MANUAL/MISMATCH con DOI: el DOI apuntaba a
        # otro trabajo, así que manda el NOMBRE — re-búsqueda fresca en la
        # web y verificación en doi2bib del DOI real (2026-10-06).
        refs = await _db(lambda: list(
            lote.referencias.filter(estado__in=["MANUAL", "MISMATCH"])
            .exclude(doi="")))
        n = await _reintentar_por_nombre(lote, refs, context, sem_web,
                                         sem_tabs, cliente, bonsai_on,
                                         sem_bonsai)
        if n:
            await _log(lote, "cierre por nombre: %d referencias recuperadas "
                       "con su DOI real" % n)
        await browser.close()

    n_elim = await _db(_marcar_eliminables, lote)
    if n_elim:
        await _log(lote, "cierre: %d referencias sin coincidencia verificable "
                   "marcadas para eliminar (probable alucinación)" % n_elim)
    n_dups = await _db(deduplicar, lote)
    if n_dups:
        await _log(lote, "dedupe: %d duplicados por DOI (eliminados en "
                   "corregido.bib)" % n_dups)
    await _db(generar_informe, lote)
    await _db(generar_corregido, lote)
    lote.estado = "finalizado"
    lote.terminado = timezone.now()
    await _db(lote.save)
    await _log(lote, "lote finalizado")


def ejecutar_lote(lote_id):
    """Punto de entrada síncrono (hilo de la vista o comando de gestión)."""
    close_old_connections()
    try:
        asyncio.run(_correr(lote_id))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        lote = Lote.objects.get(pk=lote_id)
        lote.estado = "error"
        lote.terminado = timezone.now()
        lote.log = (lote.log + "\n[ERROR] " + traceback.format_exc()[-800:]).lstrip("\n")
        lote.save()
    finally:
        close_old_connections()


def preparar_reproceso(lote):
    """Resetea lo reintentable para una nueva corrida.

    Regla de destino: con DOI y RATE_LIMIT/ERROR/MISMATCH -> PENDIENTE
    (fase A re-verifica con las reglas vigentes); MANUAL con DOI -> BROKEN
    (re-búsqueda); sin DOI -> SIN_DOI (fase B).
    TODO en un solo bucle: un .update() masivo previo cambiaría los estados
    y el filtro posterior no encontraría las filas (bug corregido 2026-10-05).
    """
    qs = (lote.referencias.filter(
              estado__in=["RATE_LIMIT", "ERROR", "MANUAL", "MISMATCH"])
          | lote.referencias.filter(estado="PENDIENTE", doi=""))
    for r in qs:
        if r.estado == "MANUAL":
            r.estado = "BROKEN" if r.doi else "SIN_DOI"
        elif not r.doi:
            r.estado = "SIN_DOI"
        else:
            r.estado = "PENDIENTE"
        r.detalle = (r.detalle + " | reintento").lstrip(" |")
        r.save(update_fields=["estado", "detalle"])
    lote.estado = "pendiente"
    lote.save(update_fields=["estado"])


# ------------------------------------------------------------------ informes

def ruta_reportes(lote):
    d = Path(settings.MEDIA_ROOT) / "reportes" / str(lote.pk)
    d.mkdir(parents=True, exist_ok=True)
    return d


def generar_informe(lote):
    refs = list(lote.referencias.order_by("orden"))
    notas = [n.strip() for n in (lote.log or "").splitlines() if n.strip()]
    L = ["# Verificación de DOIs contra doi2bib.org", "",
         "- Fecha: %s" % timezone.localtime().strftime("%Y-%m-%d %H:%M"),
         "- .bib: `%s`" % lote.nombre,
         "- Modo: %d pestañas doi2bib · Bonsai: %s · Búsqueda por nombre: %s"
         % (lote.tabs,
            "activado" if lote.usar_bonsai else "desactivado",
            "activada" if lote.buscar_por_nombre else "desactivada"),
         "- Entradas comprobadas: %d" % len(refs), "",
         "| # | Clave | DOI | Estado | Detalle |", "|---|---|---|---|---|"]
    for i, r in enumerate(refs, 1):
        doi = r.doi or r.doi_propuesto or "—"
        det = (r.detalle or "").replace("|", "/").replace("\n", " ")[:300]
        L.append("| %d | `%s` | %s | %s %s | %s |" % (i, r.clave, doi, r.icono,
                                                      r.estado, det))
    hallados = [r for r in refs if r.estado == "HALLADO"]
    if hallados:
        L += ["", "## DOI hallados (propuestas ya verificadas en doi2bib)", ""]
        for r in hallados:
            L.append("- `%s` → **%s** (%s)" % (r.clave, r.doi_propuesto, r.detalle))
    con_bonsai = [r for r in refs if _aceptada_bonsai(r)]
    if con_bonsai:
        L += ["", "## Aceptadas con ayuda de Bonsai (sin coincidencia exacta)", "",
              "El DOI resolvió en doi2bib.org y Bonsai adjudicó que es el mismo",
              "trabajo pese a diferencias menores. Reparadas con campos de doi2bib.", ""]
        for r in con_bonsai:
            L.append("- `%s` (%s): %s" % (r.clave, r.estado,
                                          (r.veredicto_bonsai or {}).get("motivo", "")))
    dups = [r for r in refs if r.estado == "DUPLICADO"]
    if dups:
        L += ["", "## Duplicados eliminados (mismo DOI)", "",
              "El DOI es el único identificador único: los repetidos se "
              "quitan de `corregido.bib` (el original no se toca).", ""]
        for r in dups:
            L.append("- `%s` — %s" % (r.clave, r.detalle))
    elim = [r for r in refs if r.estado == "ELIMINAR"]
    if elim:
        L += ["", "## Eliminadas: no verificables (probable alucinación)", "",
              "La búsqueda por NOMBRE no halló ningún trabajo verificable "
              "en doi2bib/Crossref: según la política del autor, se "
              "eliminan de `corregido.bib`. **Revísalas** — si una es real "
              "(p. ej. un libro sin DOI), re agrégala a mano.", ""]
        for r in elim:
            L.append("- `%s` — %s" % (r.clave,
                                      (r.titulo or r.detalle)[:120]))
    por_clave = {}
    for r in refs:
        if r.estado != "DUPLICADO":
            por_clave.setdefault(r.clave, []).append(r)
    repes = {k: v for k, v in por_clave.items() if len(v) > 1}
    if repes:
        L += ["", "## Claves repetidas en el .bib (trabajos distintos)", "",
              "BibTeX solo usa la primera aparición: la segunda es invisible "
              "para `\\cite{}`. En `corregido.bib` la 2ª aparición lleva "
              "sufijo (b, c...) para que ambas queden disponibles.", ""]
        for k, v in repes.items():
            L.append("- `%s`: %s" % (k, " · ".join(
                "#%d %s (%s)" % (r.orden, r.estado,
                                 (r.doi or r.doi_propuesto or "sin doi")[:40])
                for r in v)))
    revisar = [r for r in refs
               if r.estado in ("MISMATCH", "BROKEN", "MANUAL", "RATE_LIMIT", "ERROR")]
    if revisar:
        L += ["", "## Revisar a mano (MISMATCH/BROKEN/MANUAL/RATE_LIMIT/ERROR)", ""]
        for r in revisar:
            L.append("- `%s` (%s) — %s: %s" % (r.clave, r.doi or "sin doi",
                                               r.estado, r.detalle))
    n = {}
    for r in refs:
        n[r.estado] = n.get(r.estado, 0) + 1
    orden = ["OK", "WARN", "MISMATCH", "BROKEN", "SIN_DOI", "HALLADO",
             "DUPLICADO", "ELIMINAR", "MANUAL", "RATE_LIMIT", "ERROR",
             "PENDIENTE"]
    L += ["", "**Resumen:** " + " · ".join(
        "%s %d %s" % (Referencia.ICONOS.get(k, ""), n[k], k)
        for k in orden if n.get(k))]
    if notas:
        L += ["", "## Notas de la corrida", ""] + [
            "- %s" % (x.split("] ", 1)[-1] if x.startswith("[") else x)
            for x in notas]
    ruta = ruta_reportes(lote) / "doi_check_report.md"
    ruta.write_text("\n".join(L) + "\n", encoding="utf-8")
    return ruta


def _bibtex_seguro(v):
    """Normaliza guiones unicode que romperían pdflatex (– → --, — → ---)."""
    return (v.replace("\u2013", "--").replace("\u2014", "---")
             .replace("\u2212", "-").replace("\u00a0", " "))


def _reconstruir(ref, titulo_remoto=False, clave=None):
    """Entrada corregida según política: manda doi.org en campos de autoridad,
    se conserva la clave (salvo desambiguación de claves repetidas). El título
    es el local si coincide >=0.90; en aceptaciones de Bonsai con sim menor,
    manda el título de doi2bib."""
    m = re.search(r"@(\w+)\s*\{\s*[^,]+,(.*)\Z", ref.doi2bib_bibtex, re.S)
    cuerpo_remoto = m.group(2) if m else ""
    lineas = ["@%s{%s," % (ref.tipo or "article", clave or ref.clave)]
    t_loc = bib.valor_campo(ref.cuerpo, "title")
    t_rem = bib.valor_campo(cuerpo_remoto, "title")
    if titulo_remoto and t_rem:
        lineas.append("  title = {%s}," % _bibtex_seguro(t_rem.strip()))
    elif t_loc:
        lineas.append("  title = {%s}," % _bibtex_seguro(t_loc.strip()))
    for campo in CAMPOS_AUTORIDAD:
        v = bib.valor_campo(cuerpo_remoto, campo) or bib.valor_campo(ref.cuerpo, campo)
        if campo == "doi" and not v and ref.doi_propuesto:
            # Crossref/página no devuelven el campo doi en el cuerpo:
            # el DOI hallado y verificado entra igual a la propuesta.
            v = ref.doi_propuesto
        if v:
            lineas.append("  %s = {%s}," % (campo, _bibtex_seguro(v.strip())))
    vistos = {"title"} | set(CAMPOS_AUTORIDAD)
    for m2 in re.finditer(r"(?:^|[,{\n])\s*(\w+)\s*=\s*[{\"]", ref.cuerpo):
        nombre = m2.group(1).lower()
        if nombre in vistos or nombre == "abstract":
            continue
        v = bib.valor_campo(ref.cuerpo, nombre)
        if v:
            lineas.append("  %s = {%s}," % (nombre, _bibtex_seguro(v.strip())))
            vistos.add(nombre)
    lineas.append("}")
    return "\n".join(lineas)


def _aceptada_bonsai(r):
    v = r.veredicto_bonsai if isinstance(r.veredicto_bonsai, dict) else {}
    return v.get("veredicto") == "mismo"


def generar_corregido(lote):
    """Propuesta de .bib corregido. El original JAMÁS se modifica.

    Se REPARAN (campos de autoridad desde doi2bib.org):
      - HALLADO: se reescribe además el DOI propuesto.
      - WARN: verificado el mismo trabajo con discrepancias menores.
      - MISMATCH: solo si la similitud >=0.90 o Bonsai lo adjudicó como el
        mismo trabajo (si difiere más, queda para revisión manual).

    Cada bloque del .bib se empareja con SU referencia por orden de
    aparición (claves repetidas: Du_2013/Li_2014/Ma_2018 en el .bib real;
    un dict por clave mezclaba bloques y perdía entradas buenas). Las
    claves repetidas que SOBREVIVEN (trabajos distintos) se desambiguan
    SOLO en la propuesta con sufijo b/c...: la 1ª aparición conserva la
    clave — BibTeX hoy ignora la 2ª, así que ningún \\cite{} se rompe.
    """
    try:
        texto = Path(lote.archivo.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    entradas = bib.parsear_bib(texto)
    colas = {}
    for r in lote.referencias.order_by("orden"):
        colas.setdefault(r.clave, []).append(r)
    usadas = {}
    trozos = []
    for e in entradas:
        bloque = texto[e["inicio"]:e["fin"]].rstrip()
        cola = colas.get(e["clave"])
        r = cola.pop(0) if cola else None
        if r:
            if r.estado in ("DUPLICADO", "ELIMINAR"):
                # Propuesta de eliminación: el repetido (mismo DOI) o la
                # referencia no verificable no entran al .bib corregido.
                continue
            reparar = False
            titulo_remoto = False
            if r.estado == "HALLADO" and r.doi_propuesto:
                reparar = True
            elif r.estado == "WARN" and r.doi2bib_bibtex:
                reparar = True
            elif (r.estado == "MISMATCH" and r.doi2bib_bibtex
                  and ((r.similitud or 0) >= bib.SIM_ACEPTA
                       or _aceptada_bonsai(r))):
                reparar = True
            # Clave repetida con trabajo distinto: desambiguar en la propuesta
            n = usadas.get(e["clave"], 0) + 1
            usadas[e["clave"]] = n
            clave_out = e["clave"] if n == 1 else "%s%s" % (
                e["clave"], chr(ord("a") + n - 1))
            if reparar:
                # Aceptación por Bonsai con título no idéntico: doi2bib manda.
                titulo_remoto = ((r.similitud or 0) < bib.SIM_ACEPTA
                                 and r.estado in ("HALLADO", "MISMATCH"))
                bloque = _reconstruir(r, titulo_remoto=titulo_remoto,
                                      clave=clave_out)
            elif clave_out != e["clave"]:
                bloque = re.sub(r"(@\w+\s*\{\s*)[^,\n]+",
                                r"\g<1>%s" % clave_out, bloque, count=1)
        trozos.append(bloque)
    ruta = ruta_reportes(lote) / "corregido.bib"
    ruta.write_text("\n\n".join(trozos) + "\n", encoding="utf-8")
    return ruta


# ------------------------------------------------------------------- creación

def crear_lote_desde_texto(nombre, texto, tabs=10, usar_bonsai=True,
                           buscar_por_nombre=True):
    lote = Lote(nombre=(nombre or "bibliografia.bib")[:200], tabs=tabs,
                usar_bonsai=usar_bonsai, buscar_por_nombre=buscar_por_nombre)
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    lote.archivo.save("lote-%s.bib" % sello,
                      ContentFile(texto.encode("utf-8")), save=True)
    entradas = bib.parsear_bib(texto)
    refs = []
    doi_visto = {}  # dedupe temprano: no verificar el mismo DOI dos veces
    for i, e in enumerate(entradas):
        cuerpo = e["cuerpo"]
        doi = bib.doi_limpio(bib.valor_campo(cuerpo, "doi"))
        estado, detalle = ("PENDIENTE", "") if doi else ("SIN_DOI", "entrada sin campo doi")
        if doi:
            llave = doi.lower()
            if llave in doi_visto:
                estado = "DUPLICADO"
                detalle = ("duplicado por DOI de `%s`; eliminado en "
                           "corregido.bib" % doi_visto[llave])
            else:
                doi_visto[llave] = e["clave"][:200]
        refs.append(Referencia(
            lote=lote, orden=i, clave=e["clave"][:200], tipo=e["tipo"],
            doi=doi or "",
            titulo=(bib.valor_campo(cuerpo, "title") or "").strip()[:600],
            autor=(bib.valor_campo(cuerpo, "author") or "").strip()[:900],
            anio=bib.anio_de(bib.valor_campo(cuerpo, "year"),
                             bib.valor_campo(cuerpo, "date")) or "",
            journal=(bib.valor_campo(cuerpo, "journal") or "").strip()[:400],
            cuerpo=cuerpo,
            estado=estado,
            detalle=detalle,
        ))
    Referencia.objects.bulk_create(refs)
    lote.total = len(refs)
    lote.save()
    return lote
