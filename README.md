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

Al iniciar, elige **1** para registrar las posiciones de los controles en tu pantalla; después elige **2** para ejecutar la automatización. Las posiciones se guardan en `coordenadas_ui.json` y son específicas de tu configuración local.

Para ejecutar la demo original de OmniParser, consulta [`demo.ipynb`](demo.ipynb) y [`gradio_demo.py`](gradio_demo.py). Florence-2 es opcional para el detector personalizado y requiere sus propios pesos en `weights/icon_caption_florence/`.

## Compartir en GitHub

Puedes publicar el código en un repositorio propio o en un *fork* de OmniParser. No subas los pesos de modelos, capturas de pantalla ni `coordenadas_ui.json`; son archivos locales y pueden ser grandes o contener información de tu pantalla. Instala los pesos por separado siguiendo las instrucciones anteriores. Revisa también los cambios locales y las licencias de OmniParser y de los modelos antes de redistribuirlos.

Este proyecto automatiza acciones sobre una interfaz; úsalo solo en sitios y cuentas donde tengas autorización.

## Créditos y licencia

El proyecto base es [OmniParser de Microsoft](https://github.com/microsoft/OmniParser), distribuido bajo la licencia [MIT](LICENSE). Los pesos de modelos pueden tener condiciones de uso propias; consulta la página de cada modelo antes de redistribuirlos.
