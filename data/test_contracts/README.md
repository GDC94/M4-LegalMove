# Contratos de prueba (fase 1)

Dos pares mínimos, uno por cada formato de adenda que acepta LegalMove (plan §2 y §6.5). Son los pares finales de la fase 1. Los documentos son **sintéticos**: las partes, CUIT, domicilios y correos son ficticios. El texto lo redactó un LLM a pedido del autor y el autor lo revisó.

Los dos pares comparten el mismo contrato original: un contrato de prestación de servicios en la nube (alojamiento, soporte y respaldo) de una página, entre NUBESUR S.A. y COMERCIAL ANDINA S.R.L., con siete cláusulas, de "Cláusula Primera" a "Cláusula Séptima". Cambia solo la adenda.

| Par | Tipo de contrato | `amendment_kind` | Cambios introducidos por la adenda | Degradaciones |
|---|---|---|---|---|
| `pair_a` | Prestación de servicios | `restated` (texto ordenado completo) | Cláusula Tercera: el precio mensual pasa de $ 1.500.000 a $ 1.850.000. Se agrega la Cláusula Octava, de confidencialidad. | Ninguna |
| `pair_b` | Prestación de servicios | `delta` (solo instrucciones de cambio) | Cláusula Segunda: el plazo pasa de 12 a 24 meses. Se deja sin efecto la Cláusula Quinta (penalidades). | Ninguna |

## Resultado esperado

Las listas son las esperadas y verificadas por los tests de integración (`tests/test_cli_integration.py`). El texto de `summary_of_the_change` depende de las descripciones que redacta el modelo, así que no se fija.

| Par | `sections_changed` | `topics_touched` | `change_type` por sección | Señales blandas en Langfuse |
|---|---|---|---|---|
| `pair_a` | `["3", "new:8"]` | `["price_payment", "confidentiality"]` | `3`: `modified`; `new:8`: `added` | `diff_agreement` y `other_topic_rate`. Sin `kind_agreement`: la heurística de formato es inconclusa en una adenda `restated` (N/A) |
| `pair_b` | `["2", "5"]` | `["term_duration", "penalties"]` | `2`: `modified`; `5`: `removed` | `kind_agreement = 1` y `other_topic_rate`. Sin `diff_agreement`: una adenda `delta` no se puede segmentar (N/A) |

Para correr cada par desde la raíz del repo:

```bash
uv run python src/main.py data/test_contracts/pair_a/original.png data/test_contracts/pair_a/amendment.png
uv run python src/main.py data/test_contracts/pair_b/original.png data/test_contracts/pair_b/amendment.png
```

## Degradaciones

Ninguna. Las imágenes son texto negro sobre fondo blanco, sin rotación, ruido ni desenfoque: la fase 1 prueba el pipeline, no la robustez de la lectura. Las degradaciones que simulan un escaneo real (plan §6.4) llegan con los golden cases de la fase 2.

## Archivos por par

| Archivo | Contenido |
|---|---|
| `original.png` | Contrato original renderizado |
| `amendment.png` | Adenda renderizada |
| `source_original.txt` | Texto fuente del contrato original (referencia para medir la fidelidad del parsing) |
| `source_amendment.txt` | Texto fuente de la adenda |

## Cómo se generan las imágenes

```bash
uv run python evals/render_plain.py
```

`evals/render_plain.py` renderiza cada `source_<nombre>.txt` como `<nombre>.png` en la misma carpeta: lienzo de 1240×1754 px (A4 a 150 dpi), texto negro sobre fondo blanco y sin degradaciones. Usa una fuente TrueType del sistema (DejaVu Sans en Linux, Arial en macOS o Windows) porque la fuente por defecto de Pillow no tiene glifos para tildes, eñes ni la raya (—). Si el texto no entra en una página, el script falla en lugar de cortar el documento.

Las imágenes se commitean porque el render de fuentes puede variar entre sistemas operativos. En la fase 2, los golden cases Standard reemplazan a estos pares.
