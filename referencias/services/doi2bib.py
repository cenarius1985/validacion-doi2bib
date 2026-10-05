# -*- coding: utf-8 -*-
"""Cliente de https://www.doi2bib.org/ con Playwright (async, N pestañas).

Flujo verificado (skill paper-mri-us-reviewer, probado 2026-09):
  - input con placeholder `Enter a doi, PMCID, or arXiv ID`
  - botón `get BibTeX`
  - resultado en elemento `[class*=bibtex]`
  - DOI inexistente: "Not Found" y NO crea el elemento bibtex
  - tras ~60-70 consultas seguidas: "Too many requests"

Este cliente es SIEMPRE headless: la navegación vive dentro del contenedor
Docker y jamás abre una ventana en el host del usuario.
"""
import asyncio
import time

from playwright.async_api import TimeoutError as PWTimeout

DOI2BIB_URL = "https://www.doi2bib.org/"
INPUT_PH = "Enter a doi, PMCID, or arXiv ID"
BUTTON = "get BibTeX"

# argumentos chromium obligatorios dentro del contenedor (root, /dev/shm chico)
CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]


class RateLimitGlobal:
    """Espaciado global de consultas + pausa colectiva ante rate-limit.

    doi2bib limita por IP (~60-70 consultas en ráfaga), no por pestaña:
    el limiter es único para todas las pestañas. Las N pestañas solapan la
    latencia de red, no saltan el rate-limit.
    """

    def __init__(self, min_intervalo=1.0, pausa_rate_limit=60.0):
        self.min_intervalo = min_intervalo
        self.pausa_rate_limit = pausa_rate_limit
        self._lock = asyncio.Lock()
        self._proximo = 0.0
        self._pausa_hasta = 0.0

    async def adquirir(self):
        while True:
            async with self._lock:
                ahora = time.monotonic()
                t = max(self._proximo, ahora, self._pausa_hasta)
                self._proximo = t + self.min_intervalo
            espera = t - time.monotonic()
            if espera > 0:
                await asyncio.sleep(espera)
            return

    def activar_pausa(self):
        """Ante 'Too many requests': TODOS los workers esperan."""
        self._pausa_hasta = max(self._pausa_hasta,
                                time.monotonic() + self.pausa_rate_limit)


class ClienteDoi2Bib:
    """Una pestaña nueva por consulta (las pestañas son baratas y desechables)."""

    def __init__(self, context, limiter, reintentos=2):
        self.context = context
        self.limiter = limiter
        self.reintentos = reintentos

    async def consultar(self, doi):
        """Devuelve dict(estado=..., bibtex=..., detalle=...).

        estado: 'bibtex' | 'not_found' | 'rate_limit' | 'error'
        """
        page = await self.context.new_page()
        try:
            for intento in range(self.reintentos + 1):
                try:
                    await self.limiter.adquirir()
                    await page.goto(DOI2BIB_URL, timeout=45000)
                    await page.get_by_placeholder(INPUT_PH).fill(doi)
                    await page.get_by_role("button", name=BUTTON).click()
                    try:
                        await page.wait_for_selector("[class*=bibtex]", timeout=25000)
                        await page.wait_for_function(
                            "document.querySelector('[class*=bibtex]') && "
                            "document.querySelector('[class*=bibtex]').innerText.includes('@')",
                            timeout=10000,
                        )
                        remoto = ""
                        for el in await page.query_selector_all("[class*=bibtex]"):
                            t = await el.inner_text()
                            if t and "@" in t:
                                remoto = t
                                break
                        if remoto:
                            return {"estado": "bibtex", "bibtex": remoto, "detalle": ""}
                        return {"estado": "error", "bibtex": "",
                                "detalle": "doi2bib no devolvio BibTeX"}
                    except PWTimeout:
                        cuerpo = (await page.inner_text("body") or "").lower()
                        if "too many" in cuerpo:
                            self.limiter.activar_pausa()
                            if intento < self.reintentos:
                                continue
                            return {"estado": "rate_limit", "bibtex": "",
                                    "detalle": "doi2bib: Too many requests"}
                        if "not found" in cuerpo or "invalid" in cuerpo:
                            return {"estado": "not_found", "bibtex": "",
                                    "detalle": "doi2bib: Not Found / DOI invalido"}
                        if intento < self.reintentos:
                            continue
                        return {"estado": "error", "bibtex": "",
                                "detalle": "timeout sin respuesta clara"}
                except Exception as e:  # noqa: BLE001
                    if intento < self.reintentos:
                        await asyncio.sleep(3)
                        continue
                    return {"estado": "error", "bibtex": "", "detalle": str(e)[:200]}
            return {"estado": "error", "bibtex": "", "detalle": "reintentos agotados"}
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
