# Validador DOI2BIB

Valida **una a una** las referencias de un `.bib` contra
<https://www.doi2bib.org> con **10 pestañas en paralelo** (configurable),
busca por nombre (Google → DuckDuckGo → Bing, más Crossref) las entradas sin
DOI o con DOI roto, y usa el LLM local **Bonsai** solo para clasificar casos
dudosos y rankear candidatos.

**La navegación es siempre headless dentro del contenedor Docker: no se abre
ninguna ventana de navegador en tu pantalla.** **Nada se inventa**: todo DOI
propuesto fue verificado antes en doi2bib (título coincidente determinista).

## Puesta en marcha (rama `todo-en-uno`: TODO incluido)

```bash
docker compose up -d --build
# abre http://localhost:8090
```

Eso es todo: el compose de esta rama **incluye el LLM Bonsai dentro del
mismo stack** (servicio `bonsai`, imagen oficial de llama.cpp). En el
primer arranque se descarga el modelo desde HuggingFace una única vez
(`Ternary-Bonsai-8B-PQ2_0.gguf`, ~2 GB, queda persistido en el volumen
`bonsai_models`; los arranques siguientes no vuelven a bajar nada). La
inferencia es CPU (`-ngl 0`): no hace falta GPU.

- Web: <http://localhost:8090> · Bonsai interno (debug): <http://localhost:4688>
- Puertos ocupados: `WEB_PORT=9090 docker compose up -d --build`
- Reintentar sin Bonsai o con otro modelo: ver variables en `.env.example`
  (`BONSAI_BASE_URL`, `BONSAI_GGUF`, `BONSAI_HF_REPO`, `BONSAI_CTX`).

**¿HuggingFace bloqueado en tu red?** Dos salidas:

```bash
# Opción A: carpeta local con el GGUF (pendrive/mirror corporativo)
mkdir models && cp /ruta/a/Ternary-Bonsai-8B-PQ2_0.gguf models/
BONSAI_MODELS_BIND=./models docker compose up -d --build

# Opción B: copiarlo directo al volumen ya creado
docker cp Ternary-Bonsai-8B-PQ2_0.gguf <contenedor-bonsai>:/models/
docker compose restart bonsai
```

Sin Bonsai el sistema funciona igual (veredictos LLM quedan vacíos): si el
servicio `bonsai` está caído, la validación sigue determinista.

## Uso

- **Web**: subir el `.bib` en el formulario → progreso en vivo → tabla con
  estados, informe `doi_check_report.md` descargable y `corregido.bib`
  (propuesta; el original nunca se toca).
- **CLI (solo resultados, sin UI)**:

```bash
docker compose exec web python manage.py validar --bib /app/sample/muestra.bib
docker compose exec web python manage.py validar --lote 1 --reprocesar
```

## Estados por referencia

| Estado | Significado |
|---|---|
| ✅ OK | doi2bib resolvió y todo coincide |
| ⚠️ WARN | diferencia menor (journal/volumen abreviado, algún apellido) |
| ❌ MISMATCH | título/autores/año contradictorios con doi.org |
| ⛔ BROKEN | el DOI no resuelve (Not Found) |
| ➖ SIN_DOI | la entrada no trae DOI |
| 🔎 HALLADO | se halló el DOI por nombre y **fue verificado en doi2bib** |
| 🖐️ MANUAL | hubo candidatos pero ninguno pasó la verificación (revisar) |
| ⏳ RATE_LIMIT | doi2bib cortó el ritmo; botón *Reprocesar* más tarde |
| ❓ ERROR | fallo técnico (reintentar) |

## Políticas

1. **En discrepancia, manda doi.org**: `corregido.bib` corrige
   año/journal/volumen/número/páginas/autores desde doi2bib, **jamás
   renombra claves** (política del autor del paper, 2026-09-04, ampliada
   2026-10-05: el objetivo es REPARAR referencias incorrectas). Se reparan
   HALLADO/WARN y MISMATCH aceptados; títulos contradictorios quedan
   marcados para revisión.
2. **Cortesía con doi2bib**: intervalo global entre consultas
   (`DOI2BIB_MIN_INTERVALO`, default 1 s) compartido por todas las pestañas;
   ante "Too many requests" todos los workers pausan
   (`DOI2BIB_PAUSA_RATE_LIMIT`, default 60 s) y reintenta.
3. **Bonsai valida lo que no es coincidencia exacta** (2026-10-05): la
   puerta principal sigue siendo determinista (título ≥0.90 + año igual +
   autores coincidentes; el año ±1 cuenta como print-vs-online y los
   subtítulos añadidos/quitados se toleran). Lo que falla por poco
   (título ≥0.60, año ±1, autores compatibles, solapamiento de palabras
   ≥0.60/0.55) lo adjudica Bonsai: si es el mismo trabajo se acepta y se
   repara con campos de doi2bib. Todo DOI aceptado resolvió ANTES en una
   fuente de autoridad; Bonsai nunca genera datos (respuesta JSON estricta).
4. **Cascada de autoridad para DOI "rotos"** (2026-10-06): si doi2bib.org
   no sirve un DOI, se consulta el registro oficial de
   **api.crossref.org/works/<doi>** y, en última instancia, la **página
   real del artículo** vía doi.org con Playwright (meta tags `citation_*`;
   Cloudflare u otras protecciones se detectan y descartan). Varios
   "BROKEN" eran falsos. Si el DOI original resuelve y el título coincide,
   el año mal citado del .bib se corrige según doi2bib (el DOI manda).
5. **DOI que apunta a otro trabajo**: los MISMATCH se re-buscan por nombre
   (fase B) para proponer el DOI correcto, y el *Reprocesar* los reincluye
   junto a RATE_LIMIT/ERROR/MANUAL/BROKEN.
6. **Deduplicación por DOI** (2026-10-06): el DOI es el ÚNICO identificador
   único válido para quitar repetidos (política del autor); nunca se
   deduplica por título/autor. Se compara el DOI final (el propio o el
   hallado y verificado, en minúsculas). De cada grupo sobrevive la entrada
   mejor clasificada; las repetidas se marcan 🔁 DUPLICADO y **se eliminan
   de `corregido.bib`** (el original no se toca). El dedupe corre al final
   del lote y también al crearlo (para no verificar el mismo DOI dos veces).

## Variables (ver `.env.example`)

`DOI2BIB_TABS` (10) · `DOI2BIB_MIN_INTERVALO` (1.0 s) · `DOI2BIB_REINTENTOS`
(2) · `DOI2BIB_PAUSA_RATE_LIMIT` (60 s) · `BONSAI_BASE_URL`
(`http://host.docker.internal:4687/v1`) · `BONSAI_MODEL`
(`ternary-bonsai-8b`) · `BUSCADOR_TABS` (2) · `BUSCADOR_DELAY` (2.5 s) ·
`CANDIDATOS_A_VERIFICAR` (4).

## Documentación completa

En el vault Obsidian `documentacion-minsal`, carpeta
`VALIDACION-DOI2BIB-MINSAL` (PRD, arquitectura, despliegue y runbook).
