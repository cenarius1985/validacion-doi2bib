# Runbook de Validación de Referencias

**Proyecto:** Validador DOI2BIB · **Última actualización:** 2026-10-06
**Objetivo:** que ninguna referencia falsa (inexistente o mal asignada)
llegue al `.bib` final, con un proceso trazable, auditable y con política
explícita de reintentos, eliminación y reportes.

---

## 1. Qué garantiza el sistema — y qué NO

### Garantías reales (por diseño)

1. **Anti-invención**: todo DOI que entra a `corregido.bib` resolvió
   antes en una fuente de autoridad (doi2bib.org → Crossref → página real
   del artículo con meta tags `citation_*`). El LLM y las búsquedas web
   solo *proponen*; nunca crean un DOI.
2. **Puerta determinista primero**: aceptación plena exige título ≥0.90 +
   apellidos coincidentes + año igual (±1 cuenta como edición online vs
   impresa). Sin LLM de por medio.
3. **Trazabilidad total**: cada decisión queda en el campo `detalle` de la
   referencia y en el informe (`doi_check_report.md`), con la fuente que
   la respaldó.
4. **El original nunca se modifica**: `corregido.bib` es una propuesta;
   la revisión humana es siempre posible.
5. **Fracaso explícito**: lo que no se pudo verificar **no se adivina** —
   queda en MANUAL (revisar) o ELIMINAR (fuera de la propuesta).

### Lo que NINGÚN proceso automático garantiza (honestidad)

- **100% de certeza** no existe: dos trabajos pueden tener títulos casi
  idénticos; las propias fuentes (Crossref/doi2bib) pueden traer
  metadatos mal cargados por la editorial; un modelo de lenguaje puede
  equivocar su veredicto.
- Por eso el diseño no persigue "cero revisión humana" sino **acotarla**:
  el informe marca exactamente las ~30 entradas por lote que merecen
  ojos humanos (aceptaciones no exactas, eliminadas, claves cambiadas),
  en lugar de 300.

---

## 2. Cadena de confianza por capas

| Capa | Qué decide | Regla | Riesgo residual |
|---|---|---|---|
| 1. Determinista | aceptación plena | título ≥0.90 + autores ∩ + año igual | títulos casi idénticos de obras distintas (raro) |
| 2. Tolerancias | mismo trabajo con cambios menores | año ±1 = print/online; subtítulos; sufijos Jr/III; guiones unicode; acentos LaTeX | erratas graves del usuario no se detectan como tales |
| 3. Solapamiento (guardia) | filtrar lo que NO va a Bonsai | palabras del título: corto ≥0.60 contenido, largo ≥0.55; o ambos ≥0.80 con sim ≥0.45 | — (solo descarta) |
| 4. Bonsai (LLM 8B) | adjudicar "¿mismo trabajo?" | evidencia pre-calculada + regla: autores compatibles y año ±1 → "mismo" salvo evidencia en contra | puede aceptar un falso (se mitiga con capa 3 y auditoría del informe) |
| 5. Cierre por nombre | última oportunidad | búsqueda fresca; manda el nombre, no el DOI equivocado | búsqueda web bloqueada → menos candidatos |
| 6. Eliminación | purgar lo inverificable | sin coincidencia tras cierre → ELIMINAR | puede eliminar libros reales sin DOI (el informe lo lista) |
| 7. Dedupe | identidad única | DOI final (propio o hallado) en minúsculas | preprint y publicado no se fusionan (DOIs distintos) |

---

## 3. Flujos de decisión

### 3.1 Fase A — entrada CON DOI

```
DOI de la entrada
   │
   ▼
doi2bib.org ──responde──► BibTeX de autoridad ──────────┐
   │ no responde (roto/error)                           │
   ▼                                                    │
api.crossref.org/works/<doi> ──responde──► BibTeX ──────┤
   │ tampoco                                            │
   ▼                                                    │
doi.org/<doi> (Playwright, meta citation_*)             │
   │ challenge/bloqueo detectado → se descarta          │
   ▼                                                    │
⛔ BROKEN (nada lo sirvió)          ◄───────────────────┘
                                        │
                                        ▼
                            COMPARADOR (bib.py)
                        normaliza ambos y compara:
                          título ≥0.90 · autores ∩ · año
                                        │
        ┌───────────────────────────────┼─────────────────────────┐
        ▼                               ▼                         ▼
    todo coincide              coincidencia cercana        contradicción
       ✅ OK                     (ver 3.2 Bonsai)          ❌ MISMATCH
                                 ⚠️ WARN si mismo      → FASE B (nombre manda)
   WARN/MISMATCH-aceptado: corregido.bib REPARA campos y CLAVE
   con los valores de la autoridad (la clave cambiada avisa
   que hay que revisar el \cite en el paper).
```

### 3.2 Adjudicación con Bonsai (solo clasifica, jamás crea datos)

```
caso "cercano" (NO entra exacto):
   título ≥0.60 con palabras compatibles
   + autores sin contradicción total
   + año ±1
   + guardia de solapamiento superada
        │
        ▼
evidencia PRE-CALCULADA en código (no el LLM):
   apellidos en común · diferencia de años · solapamiento bidireccional
        │
        ▼
Bonsai (ternary-bonsai-8b, temperatura 0.5, thinking off):
   prompt con la evidencia + regla explícita:
   "autores compatibles y año ±1 → 'mismo' salvo evidencia
    clara de otro trabajo en el título"
        │
        ├─ "mismo"    → se acepta y repara con campos de la autoridad
        ├─ "distinto" → queda MISMATCH → FASE B
        └─ "indeciso" → no cambia nada (prudencia)
```

**Nunca se le pide a Bonsai un DOI, un año o un título: solo un
veredicto entre tres opciones sobre datos ya verificados.**

### 3.3 Fase B — sin DOI / roto / MISMATCH

```
búsqueda por NOMBRE (entre comillas) + primer autor + "doi"
   Google → (bloqueo) → DuckDuckGo → Bing  +  API Crossref
        │
        ▼
candidatos (máx. 12, deduplicados)
   ├─ Bonsai los RANKEA por similitud (si hay >1)
   ▼
por cada candidato (hasta 4 por defecto):
   ├── verificación en cascada doi2bib → Crossref
   ├── ¿es el DOI propio de una entrada MISMATCH? → saltar
   │   (verificarlo contra sí mismo no resuelve nada)
   ├── coincide pleno (título ≥0.90 + autores + año) → 🔎 HALLADO
   ├── cercano → Bonsai adjudica (ver 3.2) → puede ser HALLADO
   └── rate limit → ⏳ pausa global y reintento
nada verificado → 🖐️ MANUAL
```

### 3.4 Cierre — manda el nombre; lo no verificable se elimina

```
toda MANUAL/MISMATCH con DOI:
   │ el DOI apuntaba a otro trabajo: el DOI es el error
   ▼
búsqueda FRESCA por nombre (no reutiliza candidatos viejos)
+ hasta 8 candidatos verificados + Bonsai
   ├── DOI real encontrado y verificado → 🔎 HALLADO
   │      corregido conserva lo que trae ESE DOI desde doi2bib
   └── nada verificable
          ▼
   🗑️ ELIMINAR (probable alucinación)
      + las SIN_DOI cuya búsqueda nunca arrojó candidatos
      → salen de corregido.bib; el informe las lista con su
        título para rescate manual (p. ej. libro real sin DOI)
```

### 3.5 Dedupe

```
agrupar por DOI final (propio o doi_propuesto) en MINÚSCULAS
   grupo con >1 → sobrevive el mejor estado
   (OK > WARN > HALLADO > MISMATCH > … > ELIMINAR > DUPLICADO;
    desempate: menor orden en el .bib)
   los demás → 🔁 DUPLICADO, fuera de corregido.bib
Corre al crear el lote y al final de cada corrida.
```

---

## 4. Estados y su semántica

| Estado | Semántica | ¿Entra a corregido? |
|---|---|---|
| ✅ OK | autoridad coincide plenamente | sí, intacta |
| ⚠️ WARN | verificado; diferencias menores ya reparadas | sí, reconstruida (campos+clave de autoridad) |
| 🔎 HALLADO | DOI hallado por nombre y verificado | sí, reconstruida (+DOI) |
| ❌ MISMATCH | contradice a la autoridad | solo si sim ≥0.90 o Bonsai "mismo" |
| ⛔ BROKEN | el DOI no resuelve en ninguna fuente | no |
| ➖ SIN_DOI | sin DOI propio | según resultado de fase B |
| 🔁 DUPLICADO | mismo DOI que otra | no (sobra) |
| 🗑️ ELIMINAR | inverificable por nombre: probable alucinación | no |
| 🖐️ MANUAL | candidatos pero ninguno verificado | no |
| ⏳ RATE_LIMIT / ❓ ERROR | técnicos, reintentables | no |

---

## 5. Política de reintentos

1. **Rate limit de doi2bib**: intervalo global entre consultas
   (`DOI2BIB_MIN_INTERVALO`, 1 s) + pausa global de 60 s ante
   "Too many requests"; los trabajadores afectados quedan ⏳ y el botón
   *Reprocesar* los reintenta más tarde.
2. **Reprocesar selectivo**: reincluye RATE_LIMIT/ERROR/MANUAL/MISMATCH/
   BROKEN; **no** re-verifica OK/WARN/HALLADO (ahorro) y reutiliza los
   candidatos ya recaudados (los DOI no cambian).
3. **Cierre por nombre**: búsqueda FRESCA (sin candidatos viejos) y más
   candidatos verificados (4→8): segunda oportunidad con criterio
   distinto — el nombre manda, no el DOI.
4. **Reintentos por consulta**: 2 reintentos con backoff en el cliente
   headless antes de declarar ERROR.

---

## 6. Política de eliminación

**Cuándo corresponde**: tras fase B + cierre, sin ninguna coincidencia
verificable por nombre → 🗑️ ELIMINAR (probable alucinación). También las
SIN_DOI sin candidatos.

**Salvaguardas**:
- El `.bib` original jamás se toca: la eliminación vive solo en la
  propuesta `corregido.bib`.
- El informe lista cada eliminada con su título para rescate manual.
- Los libros reales sin DOI (p. ej. González & Woods) CAEN aquí por
  diseño: es una decisión del dueño del proceso (2026-10-06); si pesa
  más no perderlos, revisar la sección 9 (mejora F2-4: lista blanca).

---

## 7. Reportes y cómo auditarlos

`doi_check_report.md` — leer SIEMPRE en este orden:

1. **Resumen** (conteos por estado): salud general del lote.
2. **DOI hallados**: cada propuesta fue verificada; spot-check de 2-3
   abriendo `https://doi.org/<doi>`.
3. **Aceptadas con ayuda de Bonsai**: las de mayor riesgo residual —
   leer el motivo del veredicto una por una.
4. **Claves reemplazadas**: `vieja → nueva` — actualizar los `\cite{}`
   del paper; la clave cambiada ES la señal de re-lectura.
5. **Duplicados eliminados**: verificar que el sobreviviente sea el que
   tu texto cita (mismo DOI, distinta clave).
6. **Eliminadas**: revisar título por título; rescatar las reales.
7. **Revisar a mano**: lo que nadie pudo resolver automáticamente.

`corregido.bib` — solo entra lo verificado; compilar tu paper con él y
resolver `\cite` faltantes usando las secciones 4-7 del informe.

---

## 8. Runbook operativo

### 8.1 Correr un lote nuevo
```bash
python coordinador.py            # o docker compose up -d --build
# web http://localhost:8090 → nombre de proyecto (MRI, US…) + subir .bib
# CLI: docker compose exec web python manage.py validar --bib /app/sample/muestra.bib
```
Al terminar: descargar informe + corregido.bib; auditar según §7.

### 8.2 Reprocesar (rate limit, errores, difíciles)
Web → botón *Reprocesar* · CLI: `--lote <id> --reprocesar`.
No borra resultados buenos; reintenta solo lo reintentable.

### 8.3 Auditar una referencia sospechosa a mano
1. Abrir `https://doi.org/<doi>` → ¿el artículo es el citado?
2. Buscar el título EXACTO en `https://search.crossref.org` y en Google
   Scholar → ¿existe? ¿los autores/año/journal cuadran?
3. Si el DOI apunta a otro trabajo: buscar el correcto y anotarlo a mano
   en el .bib (el validador no inventa; tampoco debe el humano).
4. Si no existe en ninguna parte → es una alucinación: eliminarla del
   .bib original y del paper.

### 8.4 Rescatar una ELIMINAR que es real
Copiar la entrada del `.bib` original al corregido a mano + anotar en el
informe por qué (libro sin DOI). Futuro (F2-4): lista blanca por clave.

### 8.5 Actualizar el sistema
```bash
git pull && docker compose up -d --build   # o python coordinador.py
```
Tras cada actualización de lógica de comparación, **Reprocesar** los
lotes existentes (el reprocesado re-verifica con las reglas vigentes).

---

## 9. Evaluación crítica — riesgos residuales conocidos

| ID | Riesgo | Prob. | Impacto | Mitigación actual | Pendiente |
|---|---|---|---|---|---|
| R1 | Bonsai acepta un falso ("mismo" erróneo) | media | alto | guardia de solapamiento + evidencia pre-calculada + auditoría del informe | F2-1 doble confirmación |
| R2 | Títulos casi idénticos de obras distintas (Part I/II, erratas) | baja | alto | umbral 0.90 + autores + año | F2-2 rechazar tipos Erratum/Corrigendum |
| R3 | Preprint vs versión publicada (DOIs distintos) | media | medio | — | F2-3 preferir editorial sobre arXiv |
| R4 | Metadatos erróneos en la propia fuente | baja | medio | — | F3-1 segunda fuente (OpenAlex) |
| R5 | Búsqueda web bloqueada → menos candidatos | media | medio | 3 motores + Crossref | F3-3 health del buscador en el informe |
| R6 | ELIMINAR se lleva libros reales sin DOI | alta | medio | informe para rescate | F2-4 lista blanca |
| R7 | Errata de tipeo del usuario impide encontrar el trabajo | baja | medio | tolerancias de normalización | F3-2 búsqueda fuzzy con autores |
| R8 | Preprint+publicado no se deduplican | media | bajo | — | F2-3 mismo fix |
| R9 | Cambio de formato de doi2bib/Crossref rompe el parseo | baja | alto | cascada de 3 fuentes | F3-4 golden tests |

---

## 10. Plan de mejora

### Fase 2 — endurecimiento ✅ IMPLEMENTADA 2026-10-06
- **F2-1 Doble confirmación de no-exactos ✅**: toda aceptación sin
  coincidencia exacta (fase A MISMATCH→WARN y fase B HALLADO con
  sim <0.90) exige una segunda verificación INDEPENDIENTE (OpenAlex;
  fallback Crossref) concordando título (solapamiento fuerte) y año ±1.
  Si falla → el ascenso se rechaza. *Criterio cumplido: el informe
  reporta "HALLADO<0.90 sin doble confirmación: 0" y lista excepciones.*
- **F2-2 Rechazo de no-artículos ✅**: candidatos con título tipo
  Erratum, Corrigendum, Publisher's note, Author accepted manuscript,
  Retraction o Discussion → descartados siempre (`buscador.es_no_articulo`).
- **F2-3 Preprint vs publicado ✅**: candidatos arXiv (10.48550) van al
  final de la lista salvo que la cita sea arXiv → la editorial gana.
- **F2-4 Lista blanca ✅**: `CLAVES_PROTEGIDAS` (env) — esas claves jamás
  se marcan ELIMINAR y su bloque original se conserva en `corregido.bib`.

### Fase 3 — verificación cruzada (parcial ✅ 2026-10-06)
- **F3-1 OpenAlex como segunda fuente ✅**: `buscador.openalex_obtener`
  alimenta la doble confirmación de F2-1; HALLADO no-exacto sólido =
  fuente principal + OpenAlex de acuerdo.
- **F3-2 Búsqueda fuzzy con autores** cuando el título no arroja nada —
  ⏳ PENDIENTE.
- **F3-3 Salud del buscador ✅**: bloqueos/fallos de motores se registran
  por corrida (`buscador.resumen_salud()`), se anuncian en el log y en el
  informe ("Salud de buscadores web").
- **F3-4 Golden tests ✅**: `python tests/golden_test.py` — 17 casos de
  regresión con los incidentes reales del proyecto (Köhler, Cui2023,
  kanis2009meta, reseña Gonzalez-Woods, enciclopedia link2009, Lu2023,
  año print/online). Ejecutar ANTES de cada cambio de `bib.py`/
  `buscador.py`.

### Fase 4 — métricas ✅ IMPLEMENTADA 2026-10-06
- **F4-1 Panel de calidad por lote ✅**: el informe incluye "Calidad del
  lote": % verificado pleno, DOI hallados, aceptaciones Bonsai (con y sin
  doble confirmación) y excepciones sin doble confirmación.
- **F4-2 Export JSON de evidencia ✅**: `lote/<id>/evidencia.json` — por
  referencia: estado, DOI, similitud, veredicto Bonsai completo, detalle
  y timestamp; para auditoría externa.

---

## 11. Checklist de auditoría manual (una referencia dudosa)

- [ ] `https://doi.org/<doi>` abre ¿el trabajo citado?
- [ ] Título en Crossref Search = título del .bib (palabra por palabra)
- [ ] Primer autor y año coinciden
- [ ] Journal/volumen/páginas plausibles
- [ ] No es Erratum/Corrigendum/comentario del trabajo citado
- [ ] No es la versión preprint de lo ya publicado
- [ ] En el paper, el `\cite` apunta a la clave correcta (ver sección
      "Claves reemplazadas" del informe)

---

## 12. Registro de decisiones de política

| Fecha | Decisión |
|---|---|
| 2026-09-04 | En discrepancia de campos, manda doi.org |
| 2026-10-05 | La coincidencia exacta no debe descartar cambios menores: Bonsai adjudica lo cercano |
| 2026-10-06 | Cascada doi2bib → Crossref → página real para DOI "rotos" |
| 2026-10-06 | Deduplicación SOLO por DOI (identificador único válido) |
| 2026-10-06 | Si el título contradice al DOI, manda el NOMBRE: re-búsqueda y verificación del DOI real |
| 2026-10-06 | Lo no verificable por nombre se elimina (probable alucinación), con lista de rescate en el informe |
| 2026-10-06 | Las entradas reparadas adoptan la clave de doi2bib (la clave cambiada fuerza re-lectura del `\cite`) |
| 2026-10-06 | F2: doble confirmación independiente (OpenAlex) obligatoria para toda aceptación no exacta |
| 2026-10-06 | F2: erratas/correcciones/retracciones jamás se aceptan como el trabajo citado |
| 2026-10-06 | F2: preferencia editorial sobre arXiv; lista blanca CLAVES_PROTEGIDAS |
| 2026-10-06 | F3/F4: golden tests (17 casos), métricas por lote y evidencia.json |
