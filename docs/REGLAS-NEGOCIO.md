# Reglas de Negocio — Validador DOI2BIB

**Última actualización:** 2026-10-06
Todas las reglas vigentes del validador, con su motivación y dónde están
implementadas. Cambiar cualquiera de estas reglas requiere: (1) editar el
código indicado, (2) actualizar este documento con fecha, (3) agregar o
ajustar el caso en `tests/golden_test.py`, (4) Reprocesar los lotes
existentes. Registro histórico completo:
[`RUNBOOK-VALIDACION.md` §12](RUNBOOK-VALIDACION.md).

---

## R1 — Anti-invención (la regla madre)

Ningún DOI entra a `corregido.bib` sin haber resuelto antes en una fuente
de autoridad: doi2bib.org → `api.crossref.org/works/<doi>` → página real
del artículo vía doi.org (meta `citation_*`). El LLM y los buscadores solo
*proponen* candidatos; el LLM **jamás** genera un DOI, título o año.
*Implementación:* `pipeline._procesar_busqueda`, `buscador.*`.

## R2 — En discrepancia, manda la autoridad (campos, clave y TIPO)

Si la entrada local difiere de lo que dice la autoridad en campos de
autoridad (author, journal, year, volume, number, pages, doi), el
`corregido.bib` reconstruye la entrada con los valores de la autoridad.
Desde 2026-10-06 (v2) el comparador también vigila el campo **number**
(una entrada real tenía las páginas guardadas en `number`: 292-307 vs el
issue 3) y el **tipo de entrada** (`@book` para un artículo y viceversa
son incompatibles → WARN y reparación: la entrada reconstruida adopta el
tipo de la autoridad).
Desde 2026-10-06 la clave también se reemplaza por la de doi2bib: una
clave cambiada avisa que la entrada fue reescrita y obliga a revisar el
`\cite` (el informe lista `vieja → nueva`). Excepciones: cuerpos
sintéticos (crossref/página/openalex) conservan la clave local; colisiones
post-reemplazo se desambiguan con sufijo `b`/`c`. El `.bib` original
**jamás** se modifica.
*Implementación:* `pipeline._reconstruir`, `generar_corregido`,
`_clave_de_autoridad`.

## R3 — Puerta de aceptación determinista

Aceptación plena sin LLM exige: **título ≥0.90** (`parecido_titulo`,
tolerante a subtítulos) **+ intersección de apellidos** (si ambos lados
listan autores) **+ año igual**. Esta puerta corre siempre primero; Bonsai
solo participa si falla por poco.
*Implementación:* `bib.coincide_busqueda` (`SIM_ACEPTA = 0.90`).

## R4 — Tolerancias de normalización (cambios que NO son otro trabajo)

- **Año ±1** = edición online vs impresa → WARN reparable, no MISMATCH.
- **Año muy distinto con título ≥0.90 y autores coincidentes** = año mal
  citado en el `.bib` → WARN reparable (kanis2009meta: 2009 → 2004). Con
  `mismo_doi` además se acepta directo.
- **Subtítulos** añadidos/quitados no penalizan (`parecido_titulo` toma el
  mejor ratio completo vs base sin subtítulo).
- **Normalización**: acentos LaTeX (`\'i`, `\"{o}`), guiones unicode
  U+2010–U+2015, HTML, apóstrofes, sufijos Jr/II/III, partículas
  (van/von/de…), mayúsculas y puntuación.
*Implementación:* `bib.norm`, `bib.apellidos`, `bib.parecido_titulo`,
`bib._anios_cercanos`, `bib.comparar`.

## R5 — Guardia de solapamiento de palabras

Antes de dejar pasar un caso a Bonsai, los títulos deben contenerse en
palabras: lado corto ≥0.60 y lado largo ≥0.55 (`SOLAPA_CORTO/LARGO`); o
ambos lados ≥0.80 (`SOLAPA_FUERTE`) con sim ≥0.45 (`SIM_SOLAPA_MIN`) para
palabras intercaladas/reordenadas. Además, un "Book Review" del trabajo
citado nunca es el trabajo citado.
*Motivación:* falsos reales detectados — la reseña de Gonzalez-Woods, el
capítulo "Osteoporosis" de link2009, Lu2023 (parte de libro), el
adversario "Magnetic resonance imaging".
*Implementación:* `bib.solapamiento_palabras`, `_titulos_compatibles`.

## R6 — Bonsai: solo clasifica, con evidencia pre-calculada

- **Cuándo se consulta**: caso "cercano" (título ≥0.60, autores sin
  contradicción total, año ±1, guardia R5 superada) — para aceptar; o
  MISMATCH dudoso — para reclasificar. También rankea candidatos.
- **Entrada del prompt**: títulos, apellidos en común, diferencia de
  años, solapamientos — todo pre-calculado en código, nunca parseado por
  el LLM. Regla explícita: "autores compatibles y año ±1 → 'mismo' salvo
  evidencia clara de otro trabajo en el título".
- **Veredictos**: `mismo` (acepta y repara), `distinto` (queda
  MISMATCH), `indeciso` (no cambia nada).
- **Límites**: temperatura 0.5, thinking off, respuesta JSON estricta,
  concurrencia 4 (`BONSAI_CONCURRENCIA`). Si el LLM está caído, todo el
  flujo funciona igual (degradación elegante).
*Implementación:* `bonsai.adjudicar_borde`, `bonsai.rankear_candidatos`.

## R7 — Doble confirmación independiente (F2-1)

Toda aceptación **sin** coincidencia exacta (fase A: MISMATCH→WARN; fase
B: HALLADO con sim <0.90) exige una segunda fuente independiente
(**OpenAlex**; fallback Crossref) que concuerde: solapamiento fuerte de
título en ambos sentidos + sim ≥0.45 + año ±1. Si falla → la aceptación
se rechaza (queda MISMATCH/descarte). La traza queda en
`veredicto_bonsai["doble"]` y en el detalle.
*Implementación:* `buscador.confirmacion_independiente`, en ambos fases
del `pipeline`. Criterio del informe: "HALLADO<0.90 sin doble
confirmación: 0".

## R8 — No-artículos: jamás son el trabajo citado (F2-2)

Candidatos cuyo título contiene Erratum, Corrigendum, Correction,
Publisher's note, Author accepted manuscript, Retraction o Discussion →
descartados siempre, con constancia en los descartes.
*Implementación:* `buscador.es_no_articulo` + filtro en fase B.

## R9 — Preprint vs versión de editorial (F2-3)

Los candidatos arXiv (`10.48550/...`) van al **final** de la lista de
verificación, salvo que la cita sea propiamente un arXiv. La versión de
editorial gana con igual título.
*Implementación:* orden estable en `pipeline._procesar_busqueda`.

## R10 — Fase B: búsqueda por nombre con verificación

Candidatos de Google→DuckDuckGo→Bing + Crossref (máx. 12 guardados),
rankeados por Bonsai, verificados uno a uno en la cascada (hasta
`CANDIDATOS_A_VERIFICAR`=4; en el cierre 8). El DOI propio de una
MISMATCH **no** se verifica contra sí mismo (no resuelve nada); en BROKEN
sí se reintenta primero. El aceptado → 🔎 HALLADO; el resto 🖐️ MANUAL.
*Implementación:* `pipeline._procesar_busqueda`, `buscador.candidatos`.

## R11 — Cierre: manda el nombre; el DOI equivocado se descarta

Si el título no coincide con lo que trae el DOI, **el DOI está mal**:
re-búsqueda FRESCA por el nombre local (sin reutilizar candidatos viejos)
y verificación del DOI real. Se conserva lo que traiga **ese** DOI
correcto desde doi2bib.
*Implementación:* `pipeline._reintentar_por_nombre`.

## R12 — Eliminación de lo inverificable (🗑️ ELIMINAR)

Tras fase B + cierre, sin ninguna coincidencia verificable por nombre →
probable alucinación → **fuera de `corregido.bib`**; el informe la lista
con su título para rescate manual. También SIN_DOI sin candidatos.
Salvaguardas: el original nunca se toca; `CLAVES_PROTEGIDAS` (F2-4) exime
claves designadas (libros reales sin DOI, normas, software), que conservan
su bloque original en el corregido.
*Implementación:* `pipeline._marcar_eliminables`, `generar_corregido`.

## R13 — Deduplicación: SOLO por DOI

El identificador único es el DOI final (propio o `doi_propuesto`, en
minúsculas — el DOI es case-insensitive). Jamás por título/autor. De cada
grupo sobrevive la entrada de mejor estado (OK > WARN > HALLADO >
MISMATCH > … ; desempate por orden en el `.bib`); las demás → 🔁
DUPLICADO, fuera del corregido. Corre al crear el lote y al final de cada
corrida.
*Implementación:* `pipeline.deduplicar`, `_doi_final`, `_CALIDAD`.

## R14 — Reintentos

- **429 de doi2bib**: pausa global 60 s (`DOI2BIB_PAUSA_RATE_LIMIT`),
  estado ⏳, reintento vía *Reprocesar*.
- **Reprocesar**: reincluye RATE_LIMIT/ERROR/MANUAL/MISMATCH/BROKEN; NO
  repite OK/WARN/HALLADO; reutiliza candidatos guardados.
- **Cierre**: segunda oportunidad con búsqueda fresca y el doble de
  candidatos.
- **Cliente headless**: 2 reintentos con backoff por consulta.
*Implementación:* `doi2bib.py`, `pipeline.preparar_reproceso`,
`_reintentar_por_nombre`.

## R15 — Degradación elegante

Sin Bonsai (caído o sin modelo) todo funciona: los veredictos LLM quedan
vacíos y las decisiones son 100% deterministas. Sin motor de búsqueda web
queda Crossref. Sin ninguna fuente para un DOI → BROKEN honesto.
*Implementación:* `bonsai.disponible`, `buscador.buscar_en_web`.

## R16 — Cortesía con doi2bib.org

Intervalo global mínimo entre consultas (1 s) compartido por todas las
pestañas y pausa colectiva ante 429. Números configurable
(`DOI2BIB_TABS`, `DOI2BIB_MIN_INTERVALO`, `DOI2BIB_PAUSA_RATE_LIMIT`).
*Implementación:* `doi2bib.RateLimitGlobal`.

## R17 — Trazabilidad y reportes

Cada referencia guarda su cuerpo original, el BibTeX de la autoridad,
candidatos con fuente, veredicto Bonsai (con doble confirmación),
similitud y timestamp. Salidas por lote: `doi_check_report.md` (decisiones
ordenadas para auditoría + métricas F4-1 + salud de buscadores F3-3),
`corregido.bib` (propuesta reparada) y `evidencia.json` (F4-2, evidencia
completa por referencia).
*Implementación:* `pipeline.generar_informe/generar_corregido`,
`views.evidencia`.

## R18 — Ámbito local

La app es de uso local (DEBUG y ALLOWED_HOSTS abiertos por comodidad):
no exponer a internet sin endurecer. Sin cuentas, sin telemetría, sin
claves de API.
*Implementación:* `config/settings.py`.
