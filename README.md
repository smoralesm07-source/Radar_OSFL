# Radar OSFL Chile

Radar OSINT para construir, caracterizar y priorizar analíticamente el universo de organizaciones sin fines de lucro (OSFL) en Chile, con foco temporal desde 2020 y metodología AML/CFT basada en riesgo.

## Principio metodológico

Una OSFL no es una entidad de alto riesgo por definición. El radar separa:

1. **Universo OSFL**: entidades con evidencia jurídica, registral, tributaria o sectorial compatible con una organización sin fines de lucro.
2. **Candidatas FATF R.8**: subconjunto que potencialmente cumple la definición funcional de NPO de GAFI; esta etiqueta es de cribado y requiere validación.
3. **Señales de exposición**: hechos observables y trazables (financiamiento, donaciones, vigencia, gobernanza, inconsistencias y cruces inter-radar).
4. **Prioridad OSINT**: score de triage, nunca una afirmación de ilicitud, LA/FT o financiamiento del terrorismo.

## Fuentes núcleo

- SII: nóminas de personas jurídicas, actividades económicas, razón social, direcciones e histórico empresa-año.
- Registro Nacional de Personas Jurídicas sin Fines de Lucro (Registro Civil): fuente jurídica primaria; la ausencia de un bulk público vigente se registra como brecha de cobertura.
- Catastro de Organizaciones de Interés Público (SEGEGOB).
- Registro Central de Colaboradores del Estado, Ley 19.862.
- Registro Público de Donatarias y donaciones, Ley 21.440.
- Registro de Donatarios / Banco de Proyectos de la Ley 19.885 (MDSF).
- Registro Nacional de Organizaciones Deportivas (IND).
- División de Asociatividad y Cooperativas (asociaciones de consumidores y otras formas asociativas pertinentes).
- Dirección del Trabajo: series estadísticas de organizaciones sindicales para contraste de cobertura.
- UAF, GAFI/GAFILAT: marco AML/CFT y Recomendación 8.

## Arquitectura

```text
config/                 catálogo de fuentes, taxonomía y reglas
src/radar_osfl/         extracción, normalización, scoring e interoperabilidad
data/bronze/            snapshots inmutables por fuente/fecha (generados)
data/silver/            entidades y evidencias normalizadas (generados)
data/gold/              vistas analíticas compactas (generados)
docs/data/              salidas publicables y metadatos de cobertura
interop/                 contrato Entity Hub / interoperabilidad v1
.github/workflows/       CI y actualización automática
```

## Identidad e interoperabilidad

Se adopta el patrón Entity Hub usado por los demás radares:

- `entity_id = ENT-RUT-{RUT_NORMALIZADO}` solo cuando el RUT es válido.
- Los IDs locales de cada fuente se preservan.
- Un nombre no es una llave de identidad.
- Una falla de fuente **no equivale a cero**.
- Se conserva el último snapshot válido y su estado de frescura.

## Ejecución

```bash
python -m pip install -r requirements.txt
python scripts/run_radar.py --mode refresh
pytest -q
```

La ejecución automática está definida en `.github/workflows/radar.yml` y también puede lanzarse manualmente.

## Alcance temporal

- Histórico analítico principal: 2020 a la fecha.
- SII empresa-año disponible actualmente: años comerciales 2020-2024.
- Los registros corrientes se tratan como snapshots y no se retroproyectan artificialmente.

## Advertencia de uso

El Radar OSFL es una herramienta OSINT de caracterización y priorización. Ninguna señal, score o cruce constituye por sí mismo evidencia de delito, incumplimiento, vinculación terrorista ni operación de lavado de activos.
