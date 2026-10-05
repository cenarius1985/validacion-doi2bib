# Validador DOI2BIB

Valida **una a una** las referencias de un `.bib` contra
<https://www.doi2bib.org> con **10 pestañas en paralelo** (configurable),
busca por nombre (Google → DuckDuckGo → Bing, más Crossref) las entradas sin
DOI o con DOI roto, y usa el LLM local **Bonsai** solo para clasificar casos
dudosos y rankear candidatos.

**La navegación es siempre headless dentro del contenedor Docker: no se abre
ninguna ventana de navegador en tu pantalla.** **Nada se inventa**: todo DOI
propuesto fue verificado antes en doi2bib (título coincidente determinista).

## Puesta en marcha

```bash
docker compose up -d --build
# abre http://localhost:8090
```

Opcional (recomendado): levantar el LLM Bonsai del repo `ollamaLocal`:

```bash
docker compose -f C:/Users/fernando.ramirez/Documents/GitHub/ollamaLocal/docker-compose.yml up -d bonsai
curl http://localhost:4687/health   # debe responder ok
```

Sin Bonsai el sistema funciona igual (veredictos LLM quedan vacíos).

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
   renombra claves** (política del autor del paper, 2026-09-04). Solo
   reconstruye entradas cuyo título coincide ≥0.90 (mismo paper); títulos
   contradictorios quedan marcados para revisión.
2. **Cortesía con doi2bib**: intervalo global entre consultas
   (`DOI2BIB_MIN_INTERVALO`, default 1 s) compartido por todas las pestañas;
   ante "Too many requests" todos los workers pausan
   (`DOI2BIB_PAUSA_RATE_LIMIT`, default 60 s) y reintenta.
3. **Bonsai solo clasifica**: prompts con respuesta JSON estricta y
   prohibición de inventar; la aceptación final siempre es determinista
   contra doi2bib.

## Variables (ver `.env.example`)

`DOI2BIB_TABS` (10) · `DOI2BIB_MIN_INTERVALO` (1.0 s) · `DOI2BIB_REINTENTOS`
(2) · `DOI2BIB_PAUSA_RATE_LIMIT` (60 s) · `BONSAI_BASE_URL`
(`http://host.docker.internal:4687/v1`) · `BONSAI_MODEL`
(`ternary-bonsai-8b`) · `BUSCADOR_TABS` (2) · `BUSCADOR_DELAY` (2.5 s) ·
`CANDIDATOS_A_VERIFICAR` (4).

## Documentación completa

En el vault Obsidian `documentacion-minsal`, carpeta
`VALIDACION-DOI2BIB-MINSAL` (PRD, arquitectura, despliegue y runbook).
