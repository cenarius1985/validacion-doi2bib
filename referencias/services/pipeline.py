# -*- coding: utf-8 -*-
"""Orquestador del lote: fases A (con DOI) y B (búsqueda por nombre), informe
markdown y .bib corregido propuesto.

Política del autor (2026-09-04): en discrepancia de campos, MANDA DOI.ORG
(doi2bib). Se corrigen campos, jamás claves. El .bib original nunca se
modifica: `corregido.bib` es una propuesta descargable.

Anti-invención: ningún DOI se acepta sin resolver en doi2bib Y coincidir
determinísticamente en título (ver services/bib.coincide_busqueda). Bonsai
solo clasifica/rankea; si está caído, todo sigue funcionando sin LLM.
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

async def _procesar_con_doi(ref, cliente, sem, bonsai_on, lote):
    try:
        async with sem:
            r = await cliente.consultar(ref.doi)
        if r["estado"] == "bibtex":
            ref.doi2bib_bibtex = r["bibtex"]
            res = bib.comparar(ref.cuerpo, r["bibtex"])
            ref.similitud = res["similitud"]
            ref.estado, ref.detalle = res["estado"], "; ".join(res["detalles"])
            if res["borde"] and bonsai_on:
                campos = {"titulo": ref.titulo, "autor": ref.autor,
                          "anio": ref.anio, "journal": ref.journal}
                v = await asyncio.to_thread(
                    bonsai.adjudicar_borde, campos, r["bibtex"],
                    res["similitud"], res["detalles"])
                if v:
                    ref.veredicto_bonsai = v
                    if v.get("veredicto") == "mismo" and ref.estado == "MISMATCH":
                        ref.estado = "WARN"
                        ref.detalle += " | Bonsai: mismo trabajo (%s)" % v.get("motivo", "")
                    elif v.get("veredicto") == "distinto" and ref.estado != "MISMATCH":
                        ref.estado = "MISMATCH"
                        ref.detalle += " | Bonsai: trabajos distintos"
        elif r["estado"] == "not_found":
            ref.estado, ref.detalle = "BROKEN", r["detalle"]
        elif r["estado"] == "rate_limit":
            ref.estado = "RATE_LIMIT"
            ref.detalle = r["detalle"] + " (usar Reprocesar más tarde)"
        else:
            ref.estado, ref.detalle = "ERROR", r["detalle"]
    except Exception as e:  # noqa: BLE001
        ref.estado, ref.detalle = "ERROR", ("excepción: %s" % e)[:250]
    finally:
        ref.verificada = timezone.now()
        await _db(ref.save)
        await _avanzar(lote)


async def _procesar_busqueda(ref, context, sem_web, sem_tabs, cliente,
                             bonsai_on, lote, delay, n_verificar):
    estado_inicial = ref.estado  # SIN_DOI o BROKEN
    try:
        cands = await buscador.candidatos(context, sem_web, delay,
                                          ref.titulo, ref.autor, ref.anio)
        if not cands:
            ref.estado = estado_inicial
            ref.detalle = ((ref.detalle + " | ") if ref.detalle else "") + \
                "búsqueda por nombre sin candidatos"
        else:
            if bonsai_on and len(cands) > 1:
                campos = {"titulo": ref.titulo, "autor": ref.autor,
                          "anio": ref.anio, "journal": ref.journal,
                          "clave": ref.clave}
                ordenados, v = await asyncio.to_thread(
                    bonsai.rankear_candidatos, campos, cands)
                if ordenados:
                    cands = ordenados
                    ref.veredicto_bonsai = {"ranking": v}
            ref.candidatos = cands[:12]
            aceptado, descartes = None, []
            for c in cands[:n_verificar]:
                async with sem_tabs:
                    rr = await cliente.consultar(c["doi"])
                if rr["estado"] == "rate_limit":
                    ref.estado = "RATE_LIMIT"
                    ref.detalle = rr["detalle"] + " (usar Reprocesar más tarde)"
                    break
                if rr["estado"] != "bibtex":
                    descartes.append("%s: %s" % (c["doi"], rr["estado"]))
                    continue
                ok, res = bib.coincide_busqueda(ref.cuerpo, rr["bibtex"])
                if ok:
                    aceptado = (c, rr, res)
                    break
                descartes.append("%s: sim %.2f" % (c["doi"], res["similitud"]))
            if aceptado:
                c, rr, res = aceptado
                ref.estado = "HALLADO"
                ref.doi_propuesto = c["doi"]
                ref.doi2bib_bibtex = rr["bibtex"]
                ref.similitud = res["similitud"]
                ref.detalle = ("DOI hallado vía %s y VERIFICADO en doi2bib "
                               "(título sim %.2f); %s" %
                               (c.get("fuente", "web"), res["similitud"],
                                "reemplaza DOI roto" if estado_inicial == "BROKEN"
                                else "entrada no tenía DOI"))
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


# ------------------------------------------------------------------ orquesta

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
                _procesar_con_doi(r, cliente, sem_tabs, bonsai_on, lote)
                for r in fase_a])

        # Fase B: sin DOI o DOI roto, con búsqueda por nombre
        if lote.buscar_por_nombre:
            fase_b = [r for r in refs if r.estado in ("SIN_DOI", "BROKEN")]
            if fase_b:
                await _log(lote, "fase B: %d referencias a buscar por nombre" % len(fase_b))
                await asyncio.gather(*[
                    _procesar_busqueda(r, context, sem_web, sem_tabs, cliente,
                                       bonsai_on, lote, settings.BUSCADOR_DELAY,
                                       settings.CANDIDATOS_A_VERIFICAR)
                    for r in fase_b])
        await browser.close()

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
    """Resetea lo reintentable (RATE_LIMIT/ERROR/MANUAL) para una nueva corrida."""
    lote.referencias.filter(estado__in=["RATE_LIMIT", "ERROR"]).update(
        estado="PENDIENTE", detalle="reintento")
    lote.referencias.filter(estado="MANUAL").update(detalle="reintento búsqueda")
    for r in lote.referencias.filter(estado__in=["RATE_LIMIT", "ERROR", "MANUAL"]):
        if not r.doi:
            r.estado = "SIN_DOI"
        elif r.estado == "MANUAL":
            r.estado = "BROKEN"
        r.save(update_fields=["estado"])
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
             "MANUAL", "RATE_LIMIT", "ERROR", "PENDIENTE"]
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


def _reescribir_doi(cuerpo, nuevo_doi):
    """Reemplaza/agrega el campo doi en un cuerpo BibTeX (balanceando llaves).

    OJO: el cuerpo parseado INCLUYE la llave de cierre de la entrada; si hay
    que agregar el campo, se inserta antes de esa llave final.
    """
    m = re.search(r"(?:^|[,{\n])\s*doi\s*=\s*", cuerpo, re.I)
    if m:
        rest = cuerpo[m.end():].lstrip()
        if rest[:1] == "{":
            depth, i = 0, 0
            while i < len(rest):
                if rest[i] == "{":
                    depth += 1
                elif rest[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            return cuerpo[:m.end()] + "{" + nuevo_doi + "}" + rest[i + 1:]
        if rest[:1] == '"':
            fin = rest.find('"', 1)
            return cuerpo[:m.end()] + '"' + nuevo_doi + '"' + rest[fin + 1:]
        fin = rest.find(",")
        resto = rest[fin:] if fin >= 0 else ""
        return cuerpo[:m.end()] + nuevo_doi + resto
    base = cuerpo.rstrip()
    cierre = ""
    if base.endswith("}"):
        base, cierre = base[:-1].rstrip(), "\n}"
    sep = "" if (not base or base.endswith(",")) else ","
    return base + sep + "\n  doi = {%s}," % nuevo_doi + cierre


def _reconstruir(ref):
    """Entrada corregida según política: manda doi.org en campos de autoridad,
    se conserva la clave y el título local (si el título coincide >=0.90)."""
    m = re.search(r"@(\w+)\s*\{\s*[^,]+,(.*)\Z", ref.doi2bib_bibtex, re.S)
    cuerpo_remoto = m.group(2) if m else ""
    lineas = ["@%s{%s," % (ref.tipo or "article", ref.clave)]
    t_loc = bib.valor_campo(ref.cuerpo, "title")
    if t_loc:
        lineas.append("  title = {%s}," % _bibtex_seguro(t_loc.strip()))
    for campo in CAMPOS_AUTORIDAD:
        v = bib.valor_campo(cuerpo_remoto, campo) or bib.valor_campo(ref.cuerpo, campo)
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


def generar_corregido(lote):
    """Propuesta de .bib corregido. El original JAMÁS se modifica."""
    try:
        texto = Path(lote.archivo.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    entradas = bib.parsear_bib(texto)
    refs = {r.clave: r for r in lote.referencias.all()}
    trozos = []
    for e in entradas:
        bloque = texto[e["inicio"]:e["fin"]].rstrip()
        r = refs.get(e["clave"])
        if r:
            if r.estado == "HALLADO" and r.doi_propuesto:
                bloque = "@%s{%s,%s" % (e["tipo"], e["clave"],
                                        _reescribir_doi(e["cuerpo"], r.doi_propuesto))
            elif (r.estado == "MISMATCH" and r.doi2bib_bibtex
                  and (r.similitud or 0) >= 0.90):
                bloque = _reconstruir(r)
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
    for i, e in enumerate(entradas):
        cuerpo = e["cuerpo"]
        doi = bib.doi_limpio(bib.valor_campo(cuerpo, "doi"))
        refs.append(Referencia(
            lote=lote, orden=i, clave=e["clave"][:200], tipo=e["tipo"],
            doi=doi or "",
            titulo=(bib.valor_campo(cuerpo, "title") or "").strip()[:600],
            autor=(bib.valor_campo(cuerpo, "author") or "").strip()[:900],
            anio=bib.anio_de(bib.valor_campo(cuerpo, "year"),
                             bib.valor_campo(cuerpo, "date")) or "",
            journal=(bib.valor_campo(cuerpo, "journal") or "").strip()[:400],
            cuerpo=cuerpo,
            estado="PENDIENTE" if doi else "SIN_DOI",
            detalle="" if doi else "entrada sin campo doi",
        ))
    Referencia.objects.bulk_create(refs)
    lote.total = len(refs)
    lote.save()
    return lote
