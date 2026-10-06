# Runbook Retroactivo — Construcción del Validador DOI2BIB

**Última actualización:** 2026-10-06
Este documento reconstruye, por fases, cómo se construyó la aplicación
completa. El desarrollo **no fue tradicional** (no hubo PRD ni diseño
previo): fue un ciclo iterativo guiado por incidentes reales sobre una
bibliografía de 321 referencias (Bibliography.bib). Cada fase nace de un
problema observado con datos, y cada política vigente tiene su acta de
nacimiento aquí.

> Complementos: [`ARQUITECTURA.md`](ARQUITECTURA.md) (cómo es por dentro),
> [`REGLAS-NEGOCIO.md`](REGLAS-NEGOCIO.md) (las reglas exactas) y
> [`RUNBOOK-VALIDACION.md`](RUNBOOK-VALIDACION.md) (operación, riesgos y
> plan de mejora).

---

## Fase 0 — El script verificado (2026-09)

**Objetivo:** comprobar a mano que los DOI de un paper MRI-US apuntaban al
artículo correcto.

- Nació `check_dois.py` en el repo `paper-mri-us-reviewer`: visitaba
  doi2bib.org una referencia por vez y comparaba títulos.
- Se **verificó en producción** con papers reales durante septiembre.
- Sus rutinas de normalización (quitar HTML, acentos LaTeX, llaves,
  comandos) se portaron casi verbatim al módulo `bib.py` del validador.

**Lección de fase:** primero verificador humano, después automatización.
Los umbrales originales (título ≥0.90) se heredaron de esa etapa.

---

## Fase 1 — La app: Django + Playwright headless (2026-10-05, commit efb7f6a)

**Objetivo:** validar lotes completos con una web, sin ventanas visibles.

- Django + SQLite (`Lote`/`Referencia`), Playwright headless con
  **10 pestañas en paralelo** contra doi2bib.org.
- Cliente headless con reintentos y **rate limit global**
  (`DOI2BIB_MIN_INTERVALO`, pausa de 60 s ante "Too many requests").
- Búsqueda por nombre para entradas sin DOI (Google → DuckDuckGo → Bing +
  Crossref API) y un LLM local **Bonsai** (ternary-bonsai-8b) que solo
  rankeaba candidatos.
- Informe markdown + `corregido.bib` propuesto (el original intocable).
- Docker compose propio; volumen para datos y media.

**Política fundacional (2026-09-04):** en discrepancia de campos manda
doi.org; se corrigen campos, jamás claves. *(Esta política evolucionó
después: ver Fase 6.)*

---

## Fase 2 — La crisis de las 140 referencias (2026-10-05, commit 0f840c8)

**Incidente:** al validar el lote real de 321 referencias, ~140 quedaron
descartadas (MISMATCH/MANUAL). El usuario exigió: la coincidencia exacta
no puede descartar cambios menores; **Bonsai debe validar lo no exacto**.

**Causas raíz encontradas (todas reales):**
1. **Año print vs online**: 2015 vs 2014 marcaba MISMATCH (caso
   Chang2015UTE).
2. **Acentos LaTeX con llaves**: doi2bib trae `K\"{o}hler`; el
   normalizador lo partía en `k o hler` (Weiger_2012).
3. **Sufijos**: "Melton, L. J., III" no intersectaba con "Melton".
4. **Subtítulos**: doi2bib añade ": a review" y el ratio caía <0.90.
5. **Bonsai solo ranqueaba**: nunca participaba en la *aceptación*.

**Implementado:**
- Año ±1 = WARN (print/online), no MISMATCH.
- Normalización de `\"{o}`, sufijos Jr/III, tolerancia a subtítulos.
- Bonsai adjudica casos "cercanos" con **evidencia pre-calculada en
  código** (apellidos comunes, diferencia de años) y regla explícita:
  "autores compatibles y año ±1 → mismo salvo evidencia en contra".
- `corregido.bib` pasa de "proponer" a **REPARAR** (WARN/HALLADO).
- Guardia de solapamiento de palabras (detectó dos falsos de Bonsai: una
  reseña "Book Review:" y un capítulo de enciclopedia "Osteoporosis").
- **Incidente operativo:** llamar a Bonsai sin límite desde 53 corutinas
  colapsó el engine de Docker del host. Fix: semáforo
  `BONSAI_CONCURRENCIA=4`.

**Lección de fase:** la coincidencia exacta era la debilidad, pero el LLM
sin evidencia estructurada también falla (calificó de "distinto" un caso
claro y de "mismo" un falso). El arreglo fue *clasificación con
evidencia pre-calculada + guardias deterministas*.

---

## Fase 3 — Cascada de autoridad y deduplicación (2026-10-06, commit 636e4bd)

**Incidente 1:** referencias marcadas ⛔ BROKEN abrían bien en el
navegador. **Causa:** doi2bib es solo un front de Crossref y a veces no
sirve DOIs existentes.

**Incidente 2:** `kanis2009meta` tenía DOI correcto y título idéntico
pero año mal citado (2009 vs 2004): la regla "manda doi2bib" exige
reparar, no descartar.

**Implementado:**
- **Cascada de autoridad**: doi2bib → `api.crossref.org/works/<doi>` →
  página real vía doi.org con Playwright (meta tags `citation_*`,
  challenges Cloudflare detectados y descartados).
- **"El DOI manda"**: DOI propio + título ≥0.90 + autores → el año
  erróneo del .bib se repara (kanis2009meta 2009→2004; NIH2001 rescatada
  vía Crossref).
- **Deduplicación por DOI** (único identificador válido, minúsculas,
  incluye `doi_propuesto`): 25 duplicados reales en el lote (el artículo
  de Granke estaba 3 veces).
- **Claves repetidas con trabajos distintos** (Li_2014, Ma_2018):
  emparejamiento bloque↔referencia **por orden** (un dict por clave
  perdía entradas buenas) + sufijo b/c solo en la propuesta.

---

## Fase 4 — Apertura del proyecto (2026-10-06, commits c8957fc, 3535db5)

**Objetivo:** compartir con colegas que no tienen el LLM local.

- Auditoría de credenciales de **toda la historia git** (limpia) antes
  de publicar.
- Descubrimiento clave: el GGUF ternario **no corre en llama.cpp
  estándar** (cuantización custom, ggml type 142) — requiere el **fork
  PrismML** (binario precompilado del release `prism-b10735-842b188`).
- Stack **todo-en-uno**: servicio `bonsai` vendido en `./bonsai/Dockerfile`
  + entrypoint que baja el GGUF (~2 GB) de HuggingFace una vez
  (`BONSAI_MODELS_BIND` para redes con HF bloqueado — el caso del PC del
  trabajo).
- Ramas: `main` (todo-en-uno) y `main-externo` (Bonsai del host).
- README público con capturas reales (Playwright), LICENSE MIT.
- **Incidente:** un `git add -A` arrastró exportaciones de datos
  (`_export_*.txt`) al repo público → retiradas, patrón ignorado, y
  lección: nunca `add -A` en un repo público.

---

## Fase 5 — Operación para humanos (2026-10-06, commit d98b7f7, 49ba6bb)

- `coordinador.py`: un comando construye, espera, **abre el navegador**
  y reporta el estado del LLM; `--detener`, `--puerto`, `--no-navegador`.
- Web: **nombre de proyecto** por lote (MRI, US…), **paginador** (10/pág)
  y **eliminación** de lotes con confirmación (bloqueada en curso).
- **Incidente:** backticks en el mensaje de un commit hicieron que bash
  ejecutara `docker compose up` sobre producción. Restaurado desde git;
  lección: mensajes de commit sin expansiones de shell.

---

## Fase 6 — Cierre, claves y eliminación (2026-10-06, commits 63fa60e, c2d87ea)

Refinamiento de la política por el dueño del proceso, tras ver casos:
1. **Si el título contradice al DOI, el DOI está mal → manda el NOMBRE**:
   re-búsqueda fresca en la web y verificación del DOI real (se descartó
   la variante "reemplazar con el DOI equivocado").
2. **Lo no verificable se elimina** (🗑️ ELIMINAR): probable alucinación;
   sale de `corregido.bib`, el informe la lista para rescate (50 en el
   lote 5, incluidos libros reales sin DOI — decisión consciente).
3. **La entrada reparada adopta la clave de doi2bib** (revoca "jamás
   claves"): la clave cambiada obliga a revisar el `\cite` en el paper
   (50 cambios en el lote 5; el informe los lista). `GonzalezWoods2018DIP4e`
   → `Granke2015` es el caso emblemático.
4. Web de lotes: paginador + eliminación (ver Fase 5).

---

## Fase 7 — Endurecimiento y métricas (2026-10-06, commits f3f6953, a30a000)

Se formalizó todo en [`RUNBOOK-VALIDACION.md`](RUNBOOK-VALIDACION.md)
(garantías, riesgos R1–R9, flujos) y se implementó el plan:

- **F2-1** Doble confirmación independiente (OpenAlex; fallback Crossref)
  para toda aceptación sin coincidencia exacta; si falla, se rechaza.
- **F2-2** Erratum/Corrigendum/Retraction/etc. jamás se aceptan.
- **F2-3** arXiv al final: gana la versión de editorial.
- **F2-4** `CLAVES_PROTEGIDAS` (lista blanca de eliminación).
- **F3-1** OpenAlex como segunda fuente; **F3-3** salud de motores web
  por corrida; **F3-4** golden tests (`tests/golden_test.py`, 17 casos
  con los incidentes reales: Köhler, Cui2023, kanis, Granke×3, Lu2023).
- **F4-1** "Calidad del lote" en el informe; **F4-2** `evidencia.json`
  por lote para auditoría externa.

Estado post-Fase 7 (lote 5): 195 OK · 21 WARN · 30 HALLADO · 25
DUPLICADO · 50 ELIMINAR → `corregido.bib` con 246 entradas verificadas,
sin claves ni DOIs repetidos, doble confirmación en 100% de las
aceptaciones no exactas.

---

## Cómo se replica esta construcción (para otro dominio)

1. Empieza con un verificador manual y hazlo script (Fase 0): los
   umbrales salen de datos reales, no de intuición.
2. Automatiza solo cuando el proceso manual ya es aburrido y confiable.
3. Cada incidente de datos produce exactamente una regla nueva con su
   caso de golden test.
4. Las políticas se escriben con fecha y motivo (ver §Registro en el
   runbook de validación); las revocaciones también se documentan.
5. Publica solo tras auditar la historia git completa.
6. Nada entra al resultado final sin pasar por una fuente de autoridad;
   el LLM solo clasifica con evidencia pre-calculada.
