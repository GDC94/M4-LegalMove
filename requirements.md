# LegalMove — Agente Autónomo de Comparación de Contratos

## 📌 Contexto y objetivos

Imagina que trabajas como **AI Engineer** en una empresa de tecnología legal (**LegalMove**) que procesa miles de enmiendas de contratos cada mes.

Actualmente, el equipo de **Compliance** (Cumplimiento Legal) pasa más de **40 horas a la semana** comparando manualmente los contratos originales con sus enmiendas (adendas) para identificar qué cambió, evaluar el impacto legal y derivar los documentos para su revisión. Este proceso manual es muy lento, propenso a errores humanos y un cuello de botella que impide escalar el negocio.

Tu misión es construir un **Agente Autónomo de Comparación de Contratos**. Este sistema recibirá las imágenes escaneadas de ambos documentos, las leerá utilizando IA de visión, y utilizará un equipo de "analistas virtuales" (Agentes de IA) para entender el contexto y extraer exactamente qué cláusulas se modificaron, devolviendo un reporte estructurado y sin errores que los sistemas de la empresa puedan procesar automáticamente.

## 🎯 Objetivo

Desarrollar un **sistema multi-agente autónomo** que procese imágenes escaneadas de un contrato original y su adenda. El sistema deberá:

- Extraer el texto utilizando modelos fundacionales multimodales (Visión).
- Mediante la colaboración de **dos agentes especializados**, identificar, extraer y resumir los cambios legales aplicados.
- Producir un resultado final en formato estructurado (**JSON**) estrictamente validado.
- Garantizar **trazabilidad completa** de cada paso del proceso para su uso en entornos de producción.

## ⚙️ Stack técnico

| Tecnología | Uso |
| --- | --- |
| **OpenAI GPT-4o (Vision)** | Parsear las imágenes de contratos y convertirlas en texto estructurado |
| **LangChain** | Implementar y orquestar los dos agentes colaborativos |
| **Pydantic** | Validar y estructurar el output final del sistema |
| **Langfuse** | Instrumentar el trazado completo del workflow (image parsing, ejecución de agentes, validación) |
| **Python + python-dotenv** | Lenguaje base y manejo seguro de variables de entorno |

## 📝 Consigna resumen

Crear una aplicación en Python que reciba **dos imágenes** (contrato original y adenda). El sistema deberá:

1. Usar un LLM multimodal para leer los documentos.
2. Pasarlos por un flujo de dos agentes:
   - Uno que entiende el **contexto y estructura** del documento.
   - Otro que **extrae las diferencias**.
3. Devolver un JSON validado por Pydantic que contenga:
   - Las secciones modificadas.
   - Los temas legales afectados.
   - Un resumen preciso de los cambios.
4. Registrar todas las llamadas a la API y acciones de los agentes en un dashboard de **Langfuse**.

## 🛠️ Consigna técnica (pasos o etapas)

### Paso 1 — Parsing multimodal de imágenes

- Implementar una función `parse_contract_image()` que:
  - Reciba el path de una imagen (**JPEG/PNG**).
  - La codifique en **base64**.
  - Realice una llamada a la API de **GPT-4o** con capacidad multimodal.
- El prompt de visión debe instruir al modelo a extraer el **texto completo** del contrato de la forma más fiel posible.
- Este paso debe ejecutarse **dos veces**: una para el contrato original y otra para la enmienda.
- La observabilidad de estas ejecuciones (inputs, outputs, latencia y tokens) debe registrarse mediante **spans de Langfuse** dentro del pipeline principal (`main`).

### Paso 2 — Agente 1: Contextualización

- Implementar `ContextualizationAgent`, cuya responsabilidad es recibir los dos textos parseados y producir un **análisis de estructura comparada**:
  - Identificar qué secciones existen en ambos documentos.
  - Cómo se corresponden entre sí.
  - Cuál es el propósito general de cada bloque.
- El output puede ser texto estructurado (no necesariamente JSON) que funcione como **mapa contextual** para el siguiente agente.
- ⚠️ Este agente **no extrae cambios**; solo construye el contexto necesario para el análisis posterior.

### Paso 3 — Agente 2: Extracción de cambios

- Implementar `ExtractionAgent`, que recibe como input:
  - El output del Agente 1 (mapa contextual).
  - Ambos textos (original y enmienda).
- Tiene la responsabilidad exclusiva de **identificar, aislar y describir** cada cambio introducido por la enmienda.
- Debe distinguir entre **adiciones**, **eliminaciones** y **modificaciones**.
- Su output debe ser un **JSON estructurado** con los tres campos requeridos, listo para ser validado por Pydantic.

### Paso 4 — Validación con Pydantic

Definir el modelo `ContractChangeOutput` con los campos:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `sections_changed` | `List[str]` | Identificadores de secciones modificadas |
| `topics_touched` | `List[str]` | Categorías legales/comerciales afectadas |
| `summary_of_the_change` | `str` | Descripción detallada de los cambios |

El output del Agente 2 debe cumplir este schema y ser validado utilizando Pydantic, mediante alguna de estas opciones:

- Validación explícita con `model_validate()`.
- Structured outputs usando `response_format=ContractChangeOutput`.

### Paso 5 — Trazabilidad con Langfuse

- Instrumentar el pipeline con trazabilidad usando Langfuse.
- Se recomienda implementar un **span raíz** que represente la ejecución completa del pipeline y **spans hijos** para cada etapa principal.

Estructura sugerida:

```text
contract-analysis
├── parse_original_contract
├── parse_amendment_contract
├── contextualization_agent
└── extraction_agent
```

Cada span debe incluir información relevante como **input**, **output**, **latencia** y **metadata** útil para debugging o auditoría.

## 📦 Entregables del proyecto y requisitos de entrega

El proyecto se entrega mediante un **repositorio público de GitHub** que contiene:

| Entregable | Archivo | Descripción |
| --- | --- | --- |
| Script principal | `src/main.py` | Entry point que acepta dos paths de imágenes como argumentos y ejecuta el pipeline completo |
| Agente 1 | `src/agents/contextualization_agent.py` | Agente de contextualización con system prompt y lógica propios |
| Agente 2 | `src/agents/extraction_agent.py` | Agente de extracción con system prompt y lógica propios |
| Utilidades de imagen | `src/image_parser.py` | Funciones de validación, encoding y llamadas multimodales |
| Modelos Pydantic | `src/models.py` | Modelo `ContractChangeOutput` con los tres campos requeridos |
| Imágenes de prueba | `data/test_contracts/` | Mínimo 2 pares de contratos (4 imágenes) con README explicativo |
| README | `README.md` | Documentación completa con diagramas, arquitectura, setup, uso y decisiones técnicas |
| Dependencias | `requirements.txt` + `.env.example` | Dependencias con versiones fijadas y template de variables de entorno |
| Integración Langfuse | Instrumentación del pipeline principal | Traza que agrupe las etapas del análisis, con jerarquía de spans para cada etapa del pipeline |
