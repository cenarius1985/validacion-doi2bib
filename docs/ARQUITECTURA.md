# Arquitectura — Validador DOI2BIB

**Última actualización:** 2026-10-06 · Complementos:
[`REGLAS-NEGOCIO.md`](REGLAS-NEGOCIO.md) ·
[`RUNBOOK-VALIDACION.md`](RUNBOOK-VALIDACION.md) ·
[`RUNBOOK-RETROACTIVO.md`](RUNBOOK-RETROACTIVO.md)

## 1. Vista de contenedores (rama `main`, todo-en-uno)

```
┌──────────────────── host ─────────────────────────────────────┐
│  :8090 ──► ┌──────────────────────┐      ┌────────────────┐  │
│            │ web                  │      │ bonsai         │  │
│  :4688 ──► │ Django 5.2 + SQLite  │◄────►│ fork PrismML   │  │
│  (debug)   │ Playwright/Chromium  │ red  │ llama.cpp CPU  │  │
│            │ 10 pestañas headless │ comp.│ GGUF ternario  │  │
│            └──────┬───────────────┘      │ 2 GB (vol.)    │  │
│                   │                      └────────────────┘  │
│  volúmenes: datos (SQLite) · media (.bib/informes) · bonsai_models │
└───────────────────────────────────────────────────────────────┘
        │ solo salidas HTTP de consulta bibliográfica
        ▼
  doi2bib.org · api.crossref.org · api.openalex.org · doi.org
  google.com · duckduckgo.com · bing.com
```

- **`main`**: el LLM vive en el stack (`./bonsai/Dockerfile`, binario del
  fork PrismML porque el llama.cpp estándar no ejecuta GGUF ternarios,
  ggml type 142). `BONSAI_BASE_URL=http://bonsai:8080/v1`.
- **`main-externo`**: variante sin `bonsai`; apunta a
  `host.docker.internal:4687` (compose `ollamaLocal`).
- Arranque: `python coordinador.py` (levanta, espera, abre navegador) o
  `docker compose up -d --build`. `entrypoint.sh` del web ejecuta
  makemigrations+migrate y `runserver` (herramienta local, no exponer).

## 2. Módulos (Django app `referencias`)

| Módulo | Responsabilidad |
|---|---|
| `models.py` | `Lote` (archivo, estado, tabs, flags, log) y `Referencia` (clave, tipo, doi, titulo/autor/anio/journal, `cuerpo` BibTeX original, `doi2bib_bibtex` de la autoridad, `doi_propuesto`, `candidatos` JSON, `veredicto_bonsai` JSON, `similitud`, estado). 11 estados: PENDIENTE, OK, WARN, MISMATCH, BROKEN, SIN_DOI, HALLADO, DUPLICADO, ELIMINAR, MANUAL, RATE_LIMIT, ERROR (más RATE_LIMIT/ERROR = 12). |
| `services/bib.py` | Parser de `.bib` (balanceo de llaves, claves malformadas), **normalizador** (`norm`: HTML, acentos LaTeX `\'i`/`\"{o}`, guiones unicode U+2010-U+2015, apóstrofes, NFKD, latex residual), `apellidos` (partículas + sufijos Jr/III), `parecido_titulo` (tolerante a subtítulos), `solapamiento_palabras` (bidireccional), `comparar` (OK/WARN/MISMATCH + `borde`), `coincide_busqueda` (aceptación determinista + flag `cercano` + `mismo_doi`). Constantes: `SIM_ACEPTA=0.90`, `BONSAI_SIM_MIN=0.60`, `SIM_SOLAPA_MIN=0.45`, `SOLAPA_FUERTE=0.80`, `SOLAPA_CORTO=0.60`, `SOLAPA_LARGO=0.55`. |
| `services/doi2bib.py` | Cliente headless de doi2bib.org (`ClienteDoi2Bib.consultar(doi)` → bibtex/not_found/rate_limit/error), reintentos con backoff, `RateLimitGlobal` (intervalo global + pausa ante 429). |
| `services/buscador.py` | Fase B y segunda fuente: Google→DDG→Bing (headless, extracción de DOIs por regex), `crossref_candidatos` (búsqueda bibliográfica), `crossref_obtener(doi)` (registro oficial), `openalex_obtener(doi)` (fuente independiente, F3-1), `metadatos_pagina` (página real vía doi.org, meta `citation_*`, reintentos, detección de challenge), `es_no_articulo` (F2-2), `confirmacion_independiente` (F2-1), salud de motores (`FALLOS_WEB`/`resumen_salud`, F3-3). |
| `services/bonsai.py` | Cliente del LLM local (API OpenAI-compatible): `disponible` (ping), `rankear_candidatos` (orden por similitud), `adjudicar_borde` (¿mismo trabajo? con evidencia pre-calculada y regla explícita; 3 veredictos; **nunca genera datos**). `max_tokens` 900 para el análisis, timeout 180 s. |
| `services/pipeline.py` | Orquestador `_correr`: fase A (DOI con cascada) → fase B (búsqueda por nombre, guard de no-artículos, arXiv al final, Bonsai, doble confirmación) → cierre por nombre (`_reintentar_por_nombre`) → `_marcar_eliminables` → `deduplicar` → `generar_corregido` (repara y adopta clave de autoridad) → `generar_informe` (secciones + métricas F4-1 + salud F3-3). También `crear_lote_desde_texto` (dedupe temprano) y `preparar_reproceso`. |
| `views.py` / `urls.py` | Subida con nombre de proyecto, progreso (`estado.json`), tabla filtrable por estado, informe/corregido/evidencia.json descargables, reprocesar y eliminar (POST, bloqueado en curso). Paginación 10/página. |
| `templates/` | `index` (form + lotes paginados + eliminar), `lote` (chips de estado, tabla, descargas, autorefresco JS), `referencia` (detalle de una entrada), `base` (estilos). |
| `management/commands/validar.py` | Modo CLI sin UI. |
| `coordinador.py` (raíz) | Orquestador de arranque: detecta Docker (intenta lanzar Docker Desktop en Windows), `compose up -d --build`, espera la web, abre navegador, espera Bonsai, `--detener`. |
| `tests/golden_test.py` | 17 casos de regresión con incidentes reales (F3-4); corre sin Django ni red: `python tests/golden_test.py`. |
| `./bonsai/Dockerfile` | Ubuntu 24.04 + binario PrismML de llama.cpp + entrypoint que descarga el GGUF de HuggingFace (con `BONSAI_MODELS_BIND` para HF bloqueado). |

## 3. Flujo de decisión (resumen)

```
.bib → parseo + dedupe temprano
   ├─ FASE A (DOI propio): doi2bib →[no]→ Crossref →[no]→ página real
   │     └─ comparador: exacto → OK/repara · cercano → Bonsai · contradice → MISMATCH
   ├─ FASE B (sin DOI/roto/MISMATCH): web+Crossref → candidatos →
   │     rankeo Bonsai → verificación (saltando no-artículos y el DOI
   │     en disputa) → HALLADO / MANUAL
   ├─ CIERRE: MANUAL/MISMATCH con DOI → búsqueda fresca por NOMBRE
   │     (arXiv al final, 8 candidatos, Bonsai + doble confirmación)
   ├─ ELIMINAR: sin verificable → fuera de corregido (salvo lista blanca)
   ├─ DEDUPE: mismo DOI final (minúsculas) → sobrevive el mejor
   └─ SALIDAS: informe .md (+métricas+salud) · corregido.bib · evidencia.json
```

La descripción pormenorizada de cada decisión está en
[`RUNBOOK-VALIDACION.md` §3](RUNBOOK-VALIDACION.md); las reglas exactas
con umbrales en [`REGLAS-NEGOCIO.md`](REGLAS-NEGOCIO.md).

## 4. Datos y trazabilidad

- El `.bib` original se guarda íntegro en `media/bibs/` y **nunca** se
  modifica; `corregido.bib` es la propuesta (informe + reportes en
  `media/reportes/<lote>/`).
- Cada `Referencia` conserva: cuerpo original, BibTeX de la autoridad
  usado, candidatos y sus fuentes, veredicto Bonsai completo (con
  `doble` = fuente de la doble confirmación), similitud y timestamp.
- `evidencia.json` exporta todo lo anterior por lote (F4-2).

## 5. Configuración relevante

| Constante | Valor | Dónde |
|---|---|---|
| Umbral de aceptación de título | 0.90 | `bib.SIM_ACEPTA` |
| Mínimo para consultar a Bonsai | 0.60 | `bib.BONSAI_SIM_MIN` |
| Solapamiento (vía solapamiento) | ≥0.45 con ambos lados ≥0.80 | `bib.SIM_SOLAPA_MIN`, `SOLAPA_FUERTE` |
| Guardia de contención | corto ≥0.60, largo ≥0.55 | `bib.SOLAPA_CORTO/LARGO` |
| Pestañas doi2bib / intervalo / pausa 429 | 10 / 1.0 s / 60 s | `DOI2BIB_TABS`, `MIN_INTERVALO`, `PAUSA_RATE_LIMIT` |
| Candidatos verificados (normal / cierre) | 4 / 8 | `CANDIDATOS_A_VERIFICAR` |
| Concurrencia LLM / timeout | 4 / 180 s | `BONSAI_CONCURRENCIA`, `BONSAI_TIMEOUT` |
| Lista blanca de eliminación | (env, vacío) | `CLAVES_PROTEGIDAS` |

## 6. Seguridad y límites

Herramienta de uso **local**: `DEBUG=1` y `ALLOWED_HOSTS=["*"]` por
defecto — no exponer a internet sin endurecer (`DJANGO_DEBUG=0`,
`DJANGO_SECRET_KEY` propio, proxy con auth). Sin cuentas ni telemetría;
las únicas salidas de red son consultas bibliográficas y el LLM queda
dentro del host. Calidad asegurada por `tests/golden_test.py` (17 casos),
métricas del informe y evidencia exportable.
