# OmniParser + automatización visual

Este repositorio parte de [Microsoft OmniParser](https://github.com/microsoft/OmniParser) y añade un prototipo local para detectar elementos en pantalla y automatizar la navegación de cuestionarios web en Windows.

## Qué incluye

- `automation.py`: captura la pantalla, solicita decisiones a un chatbot mediante su interfaz y ejecuta acciones con `pyautogui`.
- `detector.py`: combina el detector YOLO de OmniParser con OCR para identificar elementos de la interfaz.
- `util/`: utilidades originales y ajustes locales para inicializar OCR opcionalmente.

El flujo actual usa coordenadas locales para operar la interfaz, además de detección visual. Está pensado como prototipo y puede requerir ajustes para cada pantalla y resolución.

## Requisitos

- Windows 10/11, Python 3.12 y Chrome o Edge.
- Dependencias de Python de `requirements.txt`.
- Tesseract OCR instalado en Windows. El script espera encontrarlo en `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- Pesos del detector YOLO guardados en `weights/icon_detect/model.pt`.

## Instalación y uso

Desde la carpeta del proyecto:

```powershell
conda create -n omni python=3.12
conda activate omni
pip install -r requirements.txt
winget install UB-Mannheim.TesseractOCR
hf download microsoft/OmniParser-v2.0 icon_detect/model.pt --local-dir weights
python automation.py
```

Al iniciar, elige **1** para registrar las posiciones de los controles en tu pantalla; después elige **2** para ejecutar la automatización. Las posiciones se guardan en `coordenadas_ui.json` y son específicas de tu configuración local. Durante el registro puedes añadir la coordenada `QUIZ_SUBMIT` para el botón Submit del encabezado del quiz; los modos Quiz la requieren. La pantalla posterior de confirmación se puede atender manualmente o mediante un ID del parser.

Al ejecutar, selecciona **SmartBook**, **Quiz** o **Assignments**. Cada mensaje enviado al chatbot incluye el parseado, una captura anotada y un recordatorio de las reglas del modo activo: SmartBook, Quiz o Assignments. La captura marca texto en verde, controles en rojo y sus IDs; el archivo original no se modifica. En Assignments, antes de cada captura el programa enfoca la página y realiza un único scroll grande hacia abajo, con una pausa antes y después; si acaba de abrir un desplegable, omite el clic y el scroll una vez para que las opciones sigan visibles. Assignments permite interacciones flexibles y vuelve a analizar la pantalla después de cada paso. El chatbot puede agrupar selecciones de desplegables ya mapeadas (`S:id:texto_exacto;S:id:texto_exacto`), que se aplican seguidas. Para abrir un desplegable, usa `C:id`; en el siguiente turno selecciona con `S:id:texto_exacto`. Al completar una pregunta pendiente, responde `B:question_done` y el script avanza. Al terminar todas las preguntas de Assignments, responde `B:finished`; el programa cierra sin pulsar Submit. En Quiz, `B:submit` usa la coordenada `QUIZ_SUBMIT` calibrada. Copia el prompt completo para el chatbot desde [`PROMPT_CHATBOT.md`](PROMPT_CHATBOT.md).

En Quiz, si el parser reconoce que el contador aún no está en la última pregunta, bloquea `B:submit` para evitar el envío prematuro. En Assignments, ignora `B:submit` y `B:confirm` siempre; `B:finished` cierra la ejecución.

También puedes elegir la opción **5** del menú para copiar solo el bloque de instrucciones del chatbot de `PROMPT_CHATBOT.md` al portapapeles (sin el texto explicativo ni los delimitadores Markdown). Luego pégalo con Ctrl+V. La opción **6** sale del programa.

El parser usa perfiles en `DETECTION_PROFILES` dentro de `detector.py`. **SMARTBOOK** conserva sus umbrales históricos. Quiz y Assignments mantienen sus ajustes de controles y navegación, pero comparten los mismos ajustes OCR de SmartBook. Si calibras un parámetro OCR desde el perfil Quiz, el cambio también se aplicará a SmartBook; los parámetros YOLO y de agrupación siguen separados.

Al iniciar una ejecución se usa **PaddleOCR** de forma predeterminada. Responde `n` en el menú para elegir EasyOCR. Cada motor conserva su propio procesamiento; la primera carga de PaddleOCR puede descargar los modelos necesarios.

En **QUIZ**, las etiquetas OCR `Next`, `Prev` y `Previous` se conservan como elementos de texto independientes para que no se fusionen con el contador de páginas. Si el OCR entrega el texto del pie como `Prev of 22 Next`, el parser separa el `Next` final y lo añade como elemento independiente. En la vista previa pueden superponerse una caja verde de OCR y una roja de YOLO: la verde marca el texto; la roja, el control clickeable.

Puedes calibrar desde la opción **4** del menú. Elige perfil y parámetro; `text_threshold` ajusta las cajas verdes OCR y `min_size` permite detectar texto de menor tamaño. `low_text` recupera regiones débiles y `link_threshold` cambia cómo se conectan regiones. `x_gap_max` controla cuántos píxeles pueden separar dos textos para que se unan: redúcelo si encabezados vecinos se mezclan. El threshold YOLO solo afecta las cajas rojas de controles. Compara la misma captura y pulsa **s** para guardar o **q** para cancelar; los valores quedan separados por perfil en `detector_thresholds.json`. El perfil QUIZ usa `min_size=10` y `x_gap_max=50` por defecto; SMARTBOOK conserva su configuración.

Para ejecutar la demo original de OmniParser, consulta [`demo.ipynb`](demo.ipynb) y [`gradio_demo.py`](gradio_demo.py).

### Florence-2 opcional

Al iniciar una ejecución, responde **s** a la pregunta de Florence-2 para enriquecer la clasificación de controles ambiguos. Si faltan los pesos, descárgalos una sola vez desde [OmniParser-v2.0](https://huggingface.co/microsoft/OmniParser-v2.0/tree/main/icon_caption):

```powershell
hf download microsoft/OmniParser-v2.0 icon_caption/config.json --local-dir weights
hf download microsoft/OmniParser-v2.0 icon_caption/generation_config.json --local-dir weights
hf download microsoft/OmniParser-v2.0 icon_caption/model.safetensors --local-dir weights
Rename-Item -Path weights/icon_caption -NewName icon_caption_florence
```

Florence-2 ayuda a describir/clasificar algunos iconos y controles detectados; no reemplaza ni mejora directamente el OCR de texto, que usa PaddleOCR por defecto o EasyOCR si lo eliges. El tamaño del lote se ajusta según la RAM o VRAM disponible para equilibrar memoria y velocidad; en CPU puede tardar bastante más y muestra el avance. El parser imprime el tiempo dedicado a OCR, YOLO, Florence y postprocesamiento para identificar qué etapa limita cada equipo.

Sin Florence, el detector comparte la matriz de imagen con el motor OCR para evitar una copia completa adicional. EasyOCR reconoce los recortes en lotes de dos; PaddleOCR usa su propia canalización.

## Compartir en GitHub

Puedes publicar el código en un repositorio propio o en un *fork* de OmniParser. No subas los pesos de modelos, capturas de pantalla ni `coordenadas_ui.json`; son archivos locales y pueden ser grandes o contener información de tu pantalla. Instala los pesos por separado siguiendo las instrucciones anteriores. Revisa también los cambios locales y las licencias de OmniParser y de los modelos antes de redistribuirlos.

Este proyecto automatiza acciones sobre una interfaz; úsalo solo en sitios y cuentas donde tengas autorización.

## Créditos y licencia

El proyecto base es [OmniParser de Microsoft](https://github.com/microsoft/OmniParser), distribuido bajo la licencia [MIT](LICENSE). Los pesos de modelos pueden tener condiciones de uso propias; consulta la página de cada modelo antes de redistribuirlos.
