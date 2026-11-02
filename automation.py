import pyautogui
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
import cv2
import numpy as np
import time
import json
import pyperclip
import random
from PIL import ImageGrab, Image

from detector import (
    DETECTION_PROFILES,
    UIDetector,
    guardar_thresholds,
)
from util.utils import inicializar_paddleocr

# ============================================================
# CONFIGURACIÓN
# ============================================================
DEBUG = True
CAPTURA_MODO = 'printscreen'
CAPTURA_ESPERA_APERTURA = 1
CAPTURA_ESPERA_POST_CLICK = 0.3
ESPERA_POST_PEGADO_CHATBOT = 2.0
VERIFICAR_CADA_N_PREGUNTAS = 3
SCROLL_ANTES_PRIORIDAD = True
SCROLL_ANTES_NEXT = True

# SMARTBOOK usa prioridad; QUIZ avanza sin prioridad; ASSIGNMENTS
# procesa una sola interacción por turno y vuelve a inspeccionar la pantalla.
FLUJO_ACTUAL = 'SMARTBOOK'

# Auto-scroll al detectar bloqueo
SCROLL_PASO = -800
SCROLL_MAX_INTENTOS = 5
ASSIGNMENTS_SCROLL_AMOUNT = -3000
ASSIGNMENTS_FOCUS_DELAY = 0.25
ASSIGNMENTS_POST_SCROLL_DELAY = 0.25

ULTIMA_CAPTURA = {'ancho': 0, 'alto': 0, 'offset': (0, 0), 'escala': 1.0}

coordenadas = {
    'High': None, 'Medium': None, 'Low': None,
    'PEGAR': None, 'ENVIAR': None, 'Next_Question': None,
    'ENFOCAR': None, 'INICIO': None, 'FINAL': None,
}

COORDENADAS_BASE = ['PEGAR', 'ENVIAR', 'ENFOCAR', 'Next_Question', 'INICIO', 'FINAL']
COORDENADAS_PRIORIDAD = ['High', 'Medium', 'Low']

detector = None
USAR_FLORENCE = False
USAR_PADDLEOCR = True
COORDENADAS_FILE = 'coordenadas_ui.json'
ELEMENTOS_ACTUALES = []
FINISHED_FLAG = False


# ============================================================
# MONITOR Y CAPTURA
# ============================================================
def _monitor_del_cursor():
    try:
        import win32api
        x, y = win32api.GetCursorPos()
        for hm, hdc, rect in win32api.EnumDisplayMonitors():
            left, top, right, bottom = rect
            if left <= x < right and top <= y < bottom:
                return (left, top, right - left, bottom - top)
    except Exception:
        pass
    w, h = pyautogui.size()
    return (0, 0, w, h)


def _abrir_snipping():
    if CAPTURA_MODO == 'winshifts':
        pyautogui.hotkey('win', 'shift', 's')
    else:
        pyautogui.press('printscreen')
    time.sleep(CAPTURA_ESPERA_APERTURA)


def capturar_pantalla_snipping(activar_ventana=None, guardar_debug=True):
    try:
        if activar_ventana:
            pyautogui.click(activar_ventana[0], activar_ventana[1])
            time.sleep(0.1)
        _abrir_snipping()
        if activar_ventana:
            pyautogui.click(activar_ventana[0], activar_ventana[1])
        elif coordenadas.get('ENFOCAR'):
            pyautogui.click(*coordenadas['ENFOCAR'])
        else:
            pyautogui.click(pyautogui.position())
        time.sleep(CAPTURA_ESPERA_POST_CLICK)
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            if guardar_debug:
                try:
                    img.save('temp_capture.png')
                except Exception:
                    pass
            return img
        return None
    except Exception as e:
        print(f"   ❌ Error en captura: {e}")
        return None


def _copiar_imagen_a_portapapeles(img):
    win32clipboard = None
    clipboard_abierto = False
    try:
        import win32clipboard
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, "BMP")
        data = buf.getvalue()[14:]
        buf.close()
        win32clipboard.OpenClipboard()
        clipboard_abierto = True
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        return True
    except Exception:
        return False
    finally:
        if clipboard_abierto:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass


def _registrar_captura(img):
    ox, oy, mw, mh = _monitor_del_cursor()
    ULTIMA_CAPTURA['ancho'] = img.size[0]
    ULTIMA_CAPTURA['alto'] = img.size[1]
    ULTIMA_CAPTURA['offset'] = (ox, oy)
    ULTIMA_CAPTURA['escala'] = 1.0


def capturar_y_copiar_pantalla(activar_ventana=True):
    print("📸 Capturando pantalla...")
    ventana = coordenadas.get('ENFOCAR') if activar_ventana else None
    img = capturar_pantalla_snipping(activar_ventana=ventana)
    if img is not None and isinstance(ImageGrab.grabclipboard(), Image.Image):
        _registrar_captura(img)
        return True
    print("⚠️ Snipping falló, usando pyautogui.screenshot()...")
    try:
        screenshot = pyautogui.screenshot()
        screenshot.save('temp_capture.png')
        ok = _copiar_imagen_a_portapapeles(screenshot)
        if ok:
            _registrar_captura(screenshot)
        return ok
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def capturar_pantalla_inmediata():
    """Captura el monitor activo sin abrir Snipping ni hacer clics adicionales."""
    try:
        ox, oy, ancho, alto = _monitor_del_cursor()
        imagen = ImageGrab.grab(
            bbox=(ox, oy, ox + ancho, oy + alto), all_screens=True)
        imagen.save('temp_capture.png')
        _registrar_captura(imagen)
        return True
    except Exception as e:
        print(f"❌ Error en captura inmediata: {e}")
        return False


# ============================================================
# VALIDACIÓN DE COORDENADAS UI
# ============================================================
def capturar_coordenada(nombre, instrucciones):
    print(f"\n📍 Para {nombre}:")
    for linea in instrucciones:
        print(f"   - {linea}")
    input("   Presiona Enter cuando el cursor esté posicionado...")
    x, y = pyautogui.position()
    print(f"   ✅ {nombre}: ({x}, {y})")
    return (x, y)


def validar_coordenadas_completas():
    print("\n" + "=" * 60)
    print("VALIDACIÓN DE COORDENADAS DE LA UI")
    print("   (se guardan automáticamente en", COORDENADAS_FILE, ")")
    print("=" * 60)

    configurar_prioridad = input(
        "¿Este cuestionario usa prioridad High/Medium/Low (SmartBook)? (s/n): "
    ).strip().lower() == 's'
    for tipo in COORDENADAS_PRIORIDAD:
        if configurar_prioridad:
            coordenadas[tipo] = capturar_coordenada(
                tipo, [f"Ve a la opción {tipo}", "Cursor encima"])
        else:
            coordenadas[tipo] = None
        guardar_coordenadas_json()

    for tipo, desc in [('PEGAR', 'campo para pegar en chatbot'),
                       ('ENVIAR', 'botón enviar')]:
        coordenadas[tipo] = capturar_coordenada(tipo, [desc, "Cursor encima"])
        guardar_coordenadas_json()

    coordenadas['ENFOCAR'] = capturar_coordenada(
        'ENFOCAR', ["Página del cuestionario", "Punto para enfocar"])
    guardar_coordenadas_json()

    coordenadas['Next_Question'] = capturar_coordenada(
        'Next_Question', ["Botón Next Question"])
    guardar_coordenadas_json()

    coordenadas['INICIO'] = capturar_coordenada(
        'INICIO', ["Ventana chatbot", "Esquina sup. izq. de la respuesta"])
    guardar_coordenadas_json()

    coordenadas['FINAL'] = capturar_coordenada(
        'FINAL', ["Ventana chatbot", "Esquina inf. der. de la respuesta"])
    guardar_coordenadas_json()

    print("\n" + "=" * 60)
    print("🎯 RESUMEN:")
    for k, v in coordenadas.items():
        print(f"   {k}: {v}")
    print(f"\n💾 Persistidas en: {COORDENADAS_FILE}")
    return True


# ============================================================
# HELPERS DE ELEMENTOS
# ============================================================
def _elem_por_id(eid):
    for el in ELEMENTOS_ACTUALES:
        if el['id'] == eid:
            return el
    return None


def build_payload(elementos, flujo=None):
    lineas = []
    flujo = flujo or FLUJO_ACTUAL
    if flujo == 'SMARTBOOK':
        nombre_flujo = 'SMARTBOOK (el script elige prioridad y pulsa Next)'
        recordatorio = (
            'RECORDATORIO SMARTBOOK: Resuelve usando el enunciado y el texto de las opciones. '
            'No elijas prioridad ni pulses Next; el script gestiona ambos. '
            'Si aparece un bloqueo de recurso, sigue las instrucciones de bloqueo del prompt.'
        )
    elif flujo == 'ASSIGNMENTS':
        nombre_flujo = ('ASSIGNMENTS (interacción flexible; reanaliza después de cada paso; '
                        'B:question_done avanza a Next)')
        recordatorio = (
            'RECORDATORIO ASSIGNMENTS: Usa el enunciado y las opciones del texto pegado para resolver. '
            'Elige la acción adecuada para el tipo de respuesta (opciones, desplegable, input o arrastre). '
            'Tras completar la pregunta, devuelve B:question_done para avanzar. '
            'El script detecta el final con el indicador Part n of m y se detiene sin enviar el cuestionario: '
            'no devuelvas B:finished, B:submit ni B:confirm. Si un control revela opciones al abrirse, '
            'pide una nueva captura antes de elegir.'
        )
    else:
        nombre_flujo = ('QUIZ (sin prioridad: responde y el script avanza; '
                        'al terminar se detiene sin enviar)')
        recordatorio = (
            'RECORDATORIO QUIZ: Resuelve usando el enunciado y el texto de las opciones. '
            'No pulses prioridad ni Next; el script avanza después de responder. '
            'El script detecta el final por el contador o por el botón Submit y se detiene '
            'sin enviar el cuestionario: no devuelvas B:finished, B:submit ni B:confirm.'
        )
    lineas.append(f'MODO DE CUESTIONARIO: {nombre_flujo}')
    lineas.append(recordatorio)
    lineas.append('Elementos detectados:')
    for el in elementos:
        txt = el['content'][:120] if el['content'] else f"({el['type']})"
        lineas.append(f"[{el['id']}] {el['type']}: {txt}")
    return "\n".join(lineas)


# ============================================================
# DETECCIÓN DE BLOQUEO + AUTO-SCROLL
# ============================================================
def _contiene_bloqueo(elementos):
    """Detecta si la pantalla muestra el mensaje de bloqueo real."""
    texto = ' '.join(
        (el['content'] or '').lower()
        for el in elementos if el['type'] == 'text'
    )
    indicadores = [
        'before moving on',
        'select a concept resource to continue',
        'must review resource',
    ]
    return any(ind in texto for ind in indicadores)


def _contador_preguntas(elementos):
    """Lee el indicador inferior 'actual of total', si el OCR lo detectó."""
    import re

    alto = ULTIMA_CAPTURA.get('alto', 0)
    textos = [
        e for e in elementos
        if e.get('type') == 'text'
        and (not alto or e.get('center', [0, 0])[1] >= alto * 0.86)
    ]
    textos.sort(key=lambda e: (e.get('center', [0, 0])[1], e['bbox'][0]))
    candidatos = [e.get('content', '') for e in textos]
    for contenido in candidatos + [' '.join(candidatos)]:
        coincidencia = re.search(
            r'(?<!\d)(\d{1,3})\s*(?:of|0f|/)\s*(\d{1,3})(?!\d)',
            contenido, re.IGNORECASE)
        if coincidencia:
            return int(coincidencia.group(1)), int(coincidencia.group(2))
    return None


def _progreso_assignment(elementos):
    """Lee 'Part n of m' en cualquier zona de la pantalla, no solo el pie."""
    import re

    for elemento in elementos:
        if elemento.get('type') != 'text':
            continue
        texto = elemento.get('content', '') or ''
        coincidencia = re.search(
            r'\bpart\s+(\d{1,3})\s*(?:of|0f|/)\s*(\d{1,3})\b',
            texto, re.IGNORECASE)
        if coincidencia:
            actual, total = map(int, coincidencia.groups())
            if 1 <= actual <= total:
                return actual, total
    return None


def _tiene_read_about(elementos):
    """¿Está visible el botón 'Read about the concept'?"""
    for el in elementos:
        txt = (el['content'] or '').lower()
        if 'read about the concept' in txt:
            return True
    return False


def _perfil_detector():
    """Elige un perfil distinto sin tocar los umbrales históricos de SmartBook."""
    return 'SMARTBOOK' if FLUJO_ACTUAL == 'SMARTBOOK' else 'QUIZ'


def _scroll_y_re_detectar(elementos, max_intentos=SCROLL_MAX_INTENTOS):
    """
    Hace scroll hasta encontrar 'Read about the concept'.
    Devuelve (encontrado: bool, elementos_actualizados: list).
    """
    for i in range(max_intentos):
        if _tiene_read_about(elementos):
            print(f"   ✅ 'Read about the concept' visible (intento {i})")
            return True, elementos
        print(f"   ⬇️ Scroll {i+1}/{max_intentos} buscando 'Read about the concept'")
        if FLUJO_ACTUAL != 'ASSIGNMENTS':
            pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
        pyautogui.scroll(SCROLL_PASO); time.sleep(0.1)
        capturada = (
            capturar_pantalla_inmediata()
            if FLUJO_ACTUAL == 'ASSIGNMENTS'
            else capturar_y_copiar_pantalla(activar_ventana=False)
        )
        if not capturada:
            return False, elementos
        elementos = detector.detect(
            'temp_capture.png', profile=_perfil_detector())
        print(f"      {len(elementos)} elementos tras scroll")
    # último chequeo
    return _tiene_read_about(elementos), elementos


# ============================================================
# PARSER
# ============================================================
def procesar_mensaje_chatbot(mensaje):
    if not mensaje or not mensaje.strip():
        return None

    acciones = []
    for bruto in mensaje.strip().split(';'):
        bruto = bruto.strip()
        if not bruto:
            continue
        up = bruto.upper()

        if up.startswith('B:SKIP'):
            acciones.append({'tipo': 'SKIP'}); continue
        if up.startswith('B:BLOCK_READ'):
            acciones.append({'tipo': 'BLOCK_READ'}); continue
        if up.startswith('B:BLOCK_TO_Q'):
            acciones.append({'tipo': 'BLOCK_TO_Q'}); continue
        if up.startswith('B:BLOCK_NEXT'):
            acciones.append({'tipo': 'BLOCK_NEXT'}); continue
        if up.startswith('B:SCROLL'):
            acciones.append({'tipo': 'SCROLL'}); continue
        if up.startswith('B:FINISHED') or up.startswith('B:END'):
            print("ℹ️ B:finished/B:end ignorado: el final del cuestionario lo decide el programa")
            continue
        if up.startswith(('B:SUBMIT', 'B:CONFIRM')):
            print("ℹ️ B:submit/B:confirm ignorados: el programa nunca envía el cuestionario")
            continue
        if up.startswith('B:QUESTION_DONE'):
            acciones.append({'tipo': 'QUESTION_DONE'}); continue

        if up.startswith('K:'):
            acciones.append({'tipo': 'K', 'tecla': bruto[2:].strip()}); continue

        if up.startswith('D:'):
            try:
                a, b = bruto[2:].split('>')
                acciones.append({'tipo': 'D',
                                 'id_origen': int(a.strip()),
                                 'id_destino': int(b.strip())})
            except Exception:
                print(f"⚠️ D inválida: {bruto}")
            continue

        if up.startswith('S:'):
            try:
                id_str, opcion = bruto[2:].split(':', 1)
                opcion = opcion.strip()
                if not opcion:
                    raise ValueError
                acciones.append({'tipo': 'SELECT_OPTION',
                                 'id': int(id_str.strip()),
                                 'opcion': opcion})
            except Exception:
                print(f"⚠️ S inválida: {bruto}")
            continue

        if up.startswith('W!:') or up.startswith('W:'):
            con_enter = up.startswith('W!:')
            resto = bruto[3:] if con_enter else bruto[2:]
            try:
                id_str, texto = resto.split(':', 1)
                acciones.append({'tipo': 'W', 'id': int(id_str.strip()),
                                 'texto': texto, 'enter': con_enter})
            except Exception:
                print(f"⚠️ W inválida: {bruto}")
            continue

        if up.startswith('C:'):
            resto = bruto[2:]
            try:
                ids = [int(x.strip()) for x in resto.split(',')]
                acciones.append({'tipo': 'C', 'ids': ids})
            except Exception:
                print(f"⚠️ C inválida: {bruto}")
            continue

        print(f"⚠️ Desconocida: {bruto}")

    return acciones if acciones else None


# ============================================================
# EJECUTOR
# ============================================================
def ejecutar_accion_automatica(accion):
    if isinstance(accion, list):
        for a in accion:
            ejecutar_accion_individual(a)
            time.sleep(0.1)
        return
    ejecutar_accion_individual(accion)


def ejecutar_accion_individual(accion):
    tipo = accion['tipo']

    if tipo == 'C':
        for eid in accion['ids']:
            el = _elem_por_id(eid)
            if el:
                pyautogui.click(*el['center'])
                print(f"🖱️ Click id={eid} → {el['center']} | {el['content'][:50]}")
                time.sleep(0.1)
            else:
                print(f"❌ No existe id={eid}")

    elif tipo == 'W':
        el = _elem_por_id(accion['id'])
        if el:
            pyautogui.click(*el['center']); time.sleep(0.1)
            pyautogui.write(accion['texto'], interval=0.05)
            if accion.get('enter'):
                pyautogui.press('enter')
            print(f"⌨️ id={accion['id']} ← '{accion['texto']}'")
        else:
            print(f"❌ No existe id={accion['id']}")

    elif tipo == 'D':
        o = _elem_por_id(accion['id_origen'])
        d = _elem_por_id(accion['id_destino'])
        if o and d:
            pyautogui.moveTo(*o['center'], duration=0.5)
            pyautogui.mouseDown(); time.sleep(0.1)
            pyautogui.moveTo(*d['center'], duration=1)
            pyautogui.mouseUp()
            print(f"🔧 Drag {accion['id_origen']}→{accion['id_destino']}")
        else:
            print("❌ Drag: faltan ids")

    elif tipo == 'SELECT_OPTION':
        el = _elem_por_id(accion['id'])
        if el:
            # Al pegar la captura en el chatbot, el desplegable puede cerrarse.
            # Reabrirlo y elegir por texto evita depender de IDs efímeros del menú.
            pyautogui.click(*el['center'])
            time.sleep(0.15)
            pyautogui.write(accion['opcion'], interval=0.02)
            pyautogui.press('enter')
            print(f"🧾 Selección en desplegable id={accion['id']}: {accion['opcion']}")
        else:
            print(f"❌ No existe id={accion['id']} para el desplegable")

    elif tipo == 'K':
        p = accion['tecla'].split('+')
        if len(p) == 1:
            pyautogui.press(p[0])
        else:
            pyautogui.hotkey(*p)
        print(f"⌨️ Tecla '{accion['tecla']}'")

    elif tipo == 'QUESTION_DONE':
        print("✅ El chatbot indica que la pregunta está completa")

    elif tipo == 'SCROLL':
        print("⬇️ SCROLL (solicitado por chatbot)")
        pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
        pyautogui.scroll(SCROLL_PASO); time.sleep(0.1)

    elif tipo == 'SKIP':
        print("⏭️ SKIP: error skippable, saltando a siguiente pregunta")
        pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
        if SCROLL_ANTES_NEXT:
            pyautogui.scroll(-3000); time.sleep(0.1)
        pyautogui.click(*coordenadas['Next_Question']); time.sleep(0.1)

    elif tipo == 'BLOCK_READ':
        print("📖 Bloqueo real: 'Read about the concept' (click via C:id)")

    elif tipo == 'BLOCK_TO_Q':
        print("🔙 Bloqueo real: 'To Questions' (click via C:id)")

    elif tipo == 'BLOCK_NEXT':
        print("⏭️ Bloqueo real: siguiente pregunta")
        pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
        if SCROLL_ANTES_NEXT:
            pyautogui.scroll(-3000); time.sleep(0.1)
        pyautogui.click(*coordenadas['Next_Question']); time.sleep(0.1)

    time.sleep(0.2)


# ============================================================
# CHATBOT
# ============================================================
def _anotar_captura_para_chatbot(imagen, elementos):
    """Dibuja cajas e IDs grandes en una copia de la captura, sin tocar el original."""
    from PIL import ImageDraw, ImageFont
    from pathlib import Path
    import os

    anotada = imagen.copy().convert('RGB')
    draw = ImageDraw.Draw(anotada)
    font_size = max(16, min(24, round(anotada.width / 100)))
    font_path = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'arial.ttf'
    try:
        font = ImageFont.truetype(str(font_path), font_size)
    except OSError:
        font = ImageFont.load_default()

    border_width = max(2, round(font_size / 8))
    margin = max(2, round(font_size / 7))
    for elemento in elementos:
        x1, y1, x2, y2 = (int(v) for v in elemento['bbox'])
        es_texto = elemento.get('type') == 'text'
        color = '#16a34a' if es_texto else '#ef2929'
        draw.rectangle((x1, y1, x2, y2), outline=color, width=border_width)

        # La lista pegada ya contiene el tipo; aquí basta el ID para evitar
        # etiquetas anchas que tapen opciones cercanas.
        etiqueta = str(elemento['id'])
        caja_texto = draw.textbbox((0, 0), etiqueta, font=font)
        ancho = caja_texto[2] - caja_texto[0]
        alto = caja_texto[3] - caja_texto[1]
        etiqueta_y = y1 - alto - margin * 2
        if etiqueta_y < 0:
            etiqueta_y = min(y1 + border_width, max(0, anotada.height - alto - margin * 2))
        etiqueta_x = min(max(0, x1), max(0, anotada.width - ancho - margin * 2))
        draw.rectangle(
            (etiqueta_x, etiqueta_y, etiqueta_x + ancho + margin * 2,
             etiqueta_y + alto + margin * 2), fill=color)
        draw.text((etiqueta_x + margin, etiqueta_y + margin - caja_texto[1]),
                  etiqueta, fill='white', font=font)
    return anotada


def pegar_contexto_en_chatbot(payload):
    """Pega la captura anotada y el parseado de esa captura en el mismo mensaje."""
    try:
        with Image.open('temp_capture.png') as captura:
            imagen = _anotar_captura_para_chatbot(
                captura.convert('RGB'), ELEMENTOS_ACTUALES)
        if not _copiar_imagen_a_portapapeles(imagen):
            print("❌ No se pudo copiar la captura al portapapeles; no se envió el mensaje.")
            print("   Comprueba que pywin32 esté instalado: pip install pywin32")
            return False

        x, y = coordenadas['PEGAR']
        pyautogui.click(x, y); time.sleep(0.1)
        pyautogui.click(x, y); time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'v')  # Adjunta la captura
        time.sleep(2.3)  # Da tiempo a la interfaz para procesar la imagen

        pyperclip.copy(payload)
        pyautogui.hotkey('ctrl', 'v')  # Añade el parseado al mismo mensaje
        time.sleep(ESPERA_POST_PEGADO_CHATBOT)
        return True
    except Exception as e:
        print(f"❌ Error al preparar captura y parseado para el chatbot: {e}")
        return False


def enviar_pregunta_chatbot():
    try:
        pyautogui.click(*coordenadas['ENVIAR'])
        return True
    except Exception:
        return False


def obtener_respuesta_chatbot():
    try:
        pyautogui.click(*coordenadas['INICIO']); time.sleep(0.1)
        sx, sy = coordenadas['INICIO']
        ex, ey = coordenadas['FINAL']
        pyautogui.moveTo(sx, sy); pyautogui.mouseDown()
        pyautogui.moveTo(ex, ey, duration=0.1); pyautogui.mouseUp()
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'c'); time.sleep(0.1)
        msg = pyperclip.paste()
        return msg if msg.strip() else None
    except Exception:
        return None


# ============================================================
# VALIDACIÓN Y NAVEGACIÓN
# ============================================================
def validar_respuesta_correcta():
    try:
        imagen = captura_pantalla_automatica()
        textos = procesar_imagen_automatica(imagen, 0, 0)
        if coordenadas['ENFOCAR']:
            rx, ry = coordenadas['ENFOCAR']
            malas = [t for t in textos
                     if any(p in t['texto'].lower()
                            for p in ['incorrect', 'wrong', 'error'])
                     and ((t['x'] - rx) ** 2 + (t['y'] - ry) ** 2) ** 0.5 <= 283]
            if malas:
                return False
            buenas = [t for t in textos
                      if any(p in t['texto'].lower()
                             for p in ['correct', 'right', 'exacto'])
                      and ((t['x'] - rx) ** 2 + (t['y'] - ry) ** 2) ** 0.5 <= 100]
            if buenas:
                return True
        return True
    except Exception:
        return True


def _progreso_actual():
    """Progreso visible del cuestionario: (pregunta actual, total).

    Quiz muestra el contador en el pie; Assignments usa 'Part n of m' en
    cualquier zona de la pantalla. Se consulta primero el indicador propio de
    cada modo y después el otro, para no confundir 'Part n of m' de una sección
    con el número de pregunta.
    """
    if FLUJO_ACTUAL == 'ASSIGNMENTS':
        return (_progreso_assignment(ELEMENTOS_ACTUALES)
                or _contador_preguntas(ELEMENTOS_ACTUALES))
    return (_contador_preguntas(ELEMENTOS_ACTUALES)
            or _progreso_assignment(ELEMENTOS_ACTUALES))


def _es_ultima_pregunta():
    """¿El contador visible indica que ya estamos en la última pregunta?"""
    progreso = _progreso_actual()
    return bool(progreso and progreso[0] >= progreso[1])


def _pantalla_final(elementos):
    """¿La pantalla muestra un mensaje de cuestionario completado?"""
    frases = ('great work', "you've completed", 'you have completed',
              'completed this assignment', 'completed this quiz',
              'all questions answered')
    texto = ' '.join(
        (el.get('content') or '').lower()
        for el in elementos if el.get('type') == 'text'
    )
    return any(frase in texto for frase in frases)


def _next_es_submit():
    """¿El control calibrado como Next_Question muestra 'Submit' ahora?

    En la última pregunta de un quiz el botón de avanzar ocupa el mismo lugar
    que el de entregar; pulsarlo enviaría el cuestionario.
    """
    siguiente = coordenadas.get('Next_Question')
    if not siguiente:
        return False
    sx, sy = siguiente
    margen = 45
    for el in ELEMENTOS_ACTUALES:
        texto = (el.get('content') or '').strip().lower()
        if not texto.startswith(('submit', 'enviar', 'entregar')):
            continue
        x0, y0, x1, y1 = el.get('bbox') or (0, 0, 0, 0)
        if (x0 - margen <= sx <= x1 + margen
                and y0 - margen <= sy <= y1 + margen):
            return True
    return False


def pasar_a_siguiente_pregunta():
    global FINISHED_FLAG
    if FLUJO_ACTUAL in ('QUIZ', 'ASSIGNMENTS') and (
            _es_ultima_pregunta() or _next_es_submit()):
        FINISHED_FLAG = True
        print("🏁 Última pregunta del cuestionario: se detiene la automatización "
              "(no se pulsa Next ni se envía nada).")
        return
    if coordenadas['Next_Question']:
        pyautogui.click(*coordenadas['ENFOCAR'])
        if SCROLL_ANTES_NEXT:
            pyautogui.scroll(-3000); time.sleep(0.1)
        pyautogui.click(*coordenadas['Next_Question'])
    time.sleep(0.1)


def finalizar_pregunta():
    opciones = [k for k in ['High', 'Medium', 'Low'] if coordenadas[k]]
    if opciones:
        sel = random.choice(opciones)
        pyautogui.click(*coordenadas['ENFOCAR'])
        if SCROLL_ANTES_PRIORIDAD:
            pyautogui.scroll(-3000); time.sleep(0.1)
        pyautogui.click(*coordenadas[sel])
        print(f"✅ Prioridad {sel}")


# ============================================================
# BUCLE PRINCIPAL
# ============================================================
def automatizacion_completa():
    global ELEMENTOS_ACTUALES, FINISHED_FLAG

    FINISHED_FLAG = False

    requeridas = COORDENADAS_BASE + (
        COORDENADAS_PRIORIDAD if FLUJO_ACTUAL == 'SMARTBOOK' else [])
    faltantes = [k for k in requeridas if not coordenadas.get(k)]
    if faltantes:
        print(f"❌ Faltan coordenadas: {', '.join(faltantes)}. Ejecuta validación primero.")
        return

    print("\n" + "=" * 60)
    print("AUTOMATIZACIÓN (IDs + auto-scroll en bloqueo)")
    print("=" * 60)

    try:
        n = 1
        reintentos_bloqueo = 0
        assignments_omitir_scroll_una_vez = False

        while True:
            if FINISHED_FLAG:
                print("🏁 Detenido por fin de asignación")
                break

            print(f"\n🔹 Iteración {n}")
            print("-" * 40)

            # 1. Enfocar + capturar. En Assignments no abrir Snipping ni
            # hacer otro clic después del enfoque, para preservar overlays.
            if FLUJO_ACTUAL == 'ASSIGNMENTS':
                if assignments_omitir_scroll_una_vez:
                    print("↕️ Se conserva abierto el desplegable para capturar sus opciones")
                    assignments_omitir_scroll_una_vez = False
                else:
                    pyautogui.click(*coordenadas['ENFOCAR'])
                    time.sleep(ASSIGNMENTS_FOCUS_DELAY)
                    if SCROLL_ANTES_NEXT:
                        pyautogui.scroll(ASSIGNMENTS_SCROLL_AMOUNT)
                        time.sleep(ASSIGNMENTS_POST_SCROLL_DELAY)
                        print(
                            "↕️ Scroll grande de Assignments tras enfocar "
                            f"({ASSIGNMENTS_SCROLL_AMOUNT})"
                        )
                captura_ok = capturar_pantalla_inmediata()
            else:
                pyautogui.click(*coordenadas['ENFOCAR'])
                time.sleep(0.1)
                captura_ok = capturar_y_copiar_pantalla()
            if not captura_ok:
                n += 1; continue

            # 2. Detectar elementos
            print("🔍 Detectando elementos...")
            try:
                ELEMENTOS_ACTUALES = detector.detect(
                    'temp_capture.png', profile=_perfil_detector())
            except Exception as e:
                print(f"❌ Error detector: {e}")
                if USAR_PADDLEOCR:
                    print("🛑 Se detiene la ejecución porque PaddleOCR falló; "
                          "no se repetirá el mismo error en cada iteración.")
                    break
                n += 1; continue
            print(f"   {len(ELEMENTOS_ACTUALES)} elementos")

            # 2b. Si hay bloqueo → auto-scroll hasta ver "Read about the concept"
            if _contiene_bloqueo(ELEMENTOS_ACTUALES):
                print("🚨 BLOQUEO detectado en pantalla → scroll para ver recursos")
                encontrado, ELEMENTOS_ACTUALES = _scroll_y_re_detectar(ELEMENTOS_ACTUALES)
                if not encontrado:
                    print("⚠️ No apareció 'Read about the concept' tras scroll.")
                    print("   Saltando a Next Question como fallback.")
                    pasar_a_siguiente_pregunta()
                    n += 1
                    reintentos_bloqueo = 0
                    continue
                print(f"   📋 {len(ELEMENTOS_ACTUALES)} elementos tras scroll")

            # 2c. Pantalla de cuestionario completado → parar sin enviar nada
            if (FLUJO_ACTUAL in ('QUIZ', 'ASSIGNMENTS')
                    and _pantalla_final(ELEMENTOS_ACTUALES)):
                FINISHED_FLAG = True
                print("🏁 La pantalla indica que el cuestionario está completo: "
                      "se detiene la automatización sin enviar nada.")
                break

            # 3. Enviar al chatbot
            payload = build_payload(ELEMENTOS_ACTUALES, FLUJO_ACTUAL)
            if DEBUG:
                print(f"📤 Payload ({len(payload)} chars):")
                print(payload[:500] + ("..." if len(payload) > 500 else ""))

            if not pegar_contexto_en_chatbot(payload):
                n += 1
                continue
            time.sleep(0.1)
            enviar_pregunta_chatbot()
            print("⏳ Esperando respuesta...")
            time.sleep(3)

            # 4. Obtener respuesta
            msg = obtener_respuesta_chatbot()
            if not msg:
                print("❌ Sin respuesta")
                n += 1; continue
            print(f"📨 Chatbot: {msg[:200]}")

            # 5. Parsear
            accion = procesar_mensaje_chatbot(msg)
            if not accion:
                print("❌ No se pudo parsear")
                n += 1; continue

            # 5b. Fallback: B:block_read sin C:id → scroll y reintentar (sin avanzar n)
            if (any(a['tipo'] == 'BLOCK_READ' for a in accion) and
                    not any(a['tipo'] == 'C' for a in accion) and
                    reintentos_bloqueo < SCROLL_MAX_INTENTOS):
                reintentos_bloqueo += 1
                print(f"⚠️ B:block_read sin C:id (reintento {reintentos_bloqueo}/{SCROLL_MAX_INTENTOS})")
                print("   → scroll + re-detectar y volver a preguntar")
                pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
                pyautogui.scroll(SCROLL_PASO); time.sleep(0.1)
                continue  # mismo n

            reintentos_bloqueo = 0  # resetear tras éxito

            # 6. Ejecutar
            if FLUJO_ACTUAL == 'ASSIGNMENTS':
                # Un grupo completo de selecciones S se aplica de corrido.
                if accion and all(a['tipo'] == 'SELECT_OPTION' for a in accion):
                    for accion_seleccion in accion:
                        ejecutar_accion_individual(accion_seleccion)
                    acciones_completadas = True
                # También admite una respuesta seguida de B:question_done,
                # por ejemplo C:67;B:question_done, como una secuencia única.
                elif (accion[-1]['tipo'] == 'QUESTION_DONE'
                      and len(accion) > 1
                      and all(a['tipo'] in ('C', 'SELECT_OPTION', 'W', 'D')
                              for a in accion[:-1])):
                    for accion_respuesta in accion[:-1]:
                        ejecutar_accion_individual(accion_respuesta)
                    acciones_completadas = True
                else:
                    acciones_completadas = False

                if acciones_completadas:
                    pasar_a_siguiente_pregunta()
                    n += 1
                    continue

                # Para cualquier otro tipo de acción, conserva el ciclo de
                # una interacción y una captura.
                accion_unica = accion[0]
                if accion_unica['tipo'] == 'QUESTION_DONE':
                    pasar_a_siguiente_pregunta()
                    n += 1
                    continue
                if accion_unica['tipo'] == 'C' and len(accion_unica['ids']) > 1:
                    accion_unica = {
                        'tipo': 'C', 'ids': [accion_unica['ids'][0]]}
                ejecutar_accion_individual(accion_unica)
                if FINISHED_FLAG:
                    break
                if accion_unica['tipo'] == 'C':
                    assignments_omitir_scroll_una_vez = any(
                        'click to select' in (_elem_por_id(eid) or {}).get(
                            'content', '').lower()
                        for eid in accion_unica['ids']
                    )
                # La acción acaba de devolver el foco a la página del
                # cuestionario; la próxima captura debe ser directa, sin
                # un clic adicional en ENFOCAR que pueda cerrar un overlay.
                if accion_unica['tipo'] == 'SKIP':
                    n += 1
                    continue
                n += 1
                continue

            ejecutar_accion_automatica(accion)

            # 7. Determinar flujo
            if FINISHED_FLAG:
                break

            if any(a['tipo'] == 'SKIP' for a in accion):
                n += 1
                continue

            es_bloqueo = any(a['tipo'].startswith('BLOCK_') for a in accion)
            if es_bloqueo:
                if any(a['tipo'] == 'BLOCK_TO_Q' for a in accion):
                    time.sleep(0.1)
                    pasar_a_siguiente_pregunta()
                n += 1
                continue

            if FLUJO_ACTUAL == 'QUIZ':
                # Un turno equivale a una respuesta: el script avanza con Next.
                # En la última pregunta pasar_a_siguiente_pregunta() detiene la
                # automatización en lugar de enviar el cuestionario.
                pasar_a_siguiente_pregunta()
                n += 1
                continue

            # 8. Flujo normal
            finalizar_pregunta()
            if validar_respuesta_correcta():
                print("✅ Correcta")
                pasar_a_siguiente_pregunta()
            else:
                print("❌ Incorrecta: próxima iteración el chatbot decidirá skip o block")

            n += 1

    except KeyboardInterrupt:
        print("\n🛑 Detenido por el usuario")


# ============================================================
# AUXILIARES
# ============================================================
def captura_pantalla_automatica():
    return cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2BGR)


def procesar_imagen_automatica(imagen, ox, oy):
    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    try:
        datos = pytesseract.image_to_data(
            gris, config=r'--oem 3 --psm 6',
            output_type=pytesseract.Output.DICT, lang='spa+eng')
    except Exception:
        datos = pytesseract.image_to_data(
            gris, config=r'--oem 3 --psm 6',
            output_type=pytesseract.Output.DICT)
    res = []
    for i in range(len(datos['text'])):
        if int(datos['conf'][i]) > 30:
            t = datos['text'][i].strip()
            if t:
                res.append({'texto': t, 'x': ox + datos['left'][i],
                            'y': oy + datos['top'][i],
                            'ancho': datos['width'][i],
                            'alto': datos['height'][i]})
    return res


def guardar_coordenadas_json():
    """Guarda SIEMPRE al mismo archivo (persistente entre sesiones)."""
    with open(COORDENADAS_FILE, 'w', encoding='utf-8') as f:
        json.dump(coordenadas, f, indent=4)
    print(f"💾 Coordenadas guardadas en {COORDENADAS_FILE}")
    return COORDENADAS_FILE


def cargar_coordenadas_json():
    """Carga coordenadas del disco si existen y están completas."""
    import os
    if not os.path.exists(COORDENADAS_FILE):
        return False
    try:
        with open(COORDENADAS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ No se pudo leer {COORDENADAS_FILE}: {e}")
        return False

    faltantes = [k for k in COORDENADAS_BASE if not data.get(k)]
    if faltantes:
        print(f"⚠️ {COORDENADAS_FILE} incompleto. Faltan: {faltantes}")
        return False

    for k in coordenadas:
        v = data.get(k)
        coordenadas[k] = tuple(v) if isinstance(v, list) else v
    return True


def borrar_coordenadas_json():
    """Borra el archivo de coordenadas (para forzar re-validación)."""
    import os
    if os.path.exists(COORDENADAS_FILE):
        os.remove(COORDENADAS_FILE)
        print(f"🗑️ {COORDENADAS_FILE} borrado")
    for k in coordenadas:
        coordenadas[k] = None


def configurar_detector(use_florence, use_paddleocr=True):
    """Carga el detector si cambian Florence o el motor OCR."""
    global detector, USAR_FLORENCE, USAR_PADDLEOCR

    if (detector is not None
            and detector.use_florence == use_florence
            and detector.use_paddleocr == use_paddleocr):
        USAR_FLORENCE = use_florence
        USAR_PADDLEOCR = use_paddleocr
        return True

    if use_florence:
        from pathlib import Path
        carpeta = Path('weights/icon_caption_florence')
        necesarios = ['config.json', 'generation_config.json', 'model.safetensors']
        faltan = [nombre for nombre in necesarios if not (carpeta / nombre).is_file()]
        if faltan:
            print(f"❌ Faltan pesos de Florence-2 en {carpeta}: {', '.join(faltan)}")
            print("   Consulta la sección Florence-2 opcional del README para descargarlos.")
            return False

    try:
        extras = []
        if use_florence:
            extras.append('Florence-2')
        if use_paddleocr:
            extras.append('PaddleOCR')
        print("🔄 Cargando detector" + (f" con {', '.join(extras)}..." if extras else "..."))
        nuevo_detector = UIDetector(
            use_florence=use_florence, use_paddleocr=use_paddleocr)
    except Exception as e:
        print(f"❌ No se pudo cargar el detector: {e}")
        return False

    detector = nuevo_detector
    USAR_FLORENCE = use_florence
    USAR_PADDLEOCR = use_paddleocr
    print("✅ Detector listo")
    return True


def calibrar_threshold():
    """Prueba thresholds YOLO/OCR de un perfil sobre una captura fija."""
    import os
    from PIL import ImageDraw

    if not coordenadas.get('ENFOCAR'):
        print("❌ Primero calibra las coordenadas UI (opción 1), incluido ENFOCAR.")
        return
    if detector is None and not configurar_detector(USAR_FLORENCE, USAR_PADDLEOCR):
        return

    print("\nPerfil a calibrar:")
    print("1. SMARTBOOK (conserva su configuración independiente)")
    print("2. QUIZ / ASSIGNMENTS (perfil de controles y navegación)")
    seleccion = input("Elige 1 o 2: ").strip()
    if seleccion not in ('1', '2'):
        print("❌ Opción inválida")
        return
    perfil = 'SMARTBOOK' if seleccion == '1' else 'QUIZ'
    print("\nParámetro a calibrar:")
    print("1. box_threshold (cajas de controles YOLO; no modifica el texto verde)")
    print("2. text_threshold (sensibilidad de detección de texto OCR)")
    print("3. low_text (recupera texto débil; puede añadir ruido)")
    print("4. link_threshold (cómo conecta regiones de texto cercanas)")
    print("5. min_size (tamaño mínimo de texto; bájalo para títulos pequeños)")
    print("6. x_gap_max (distancia horizontal para unir cajas; bájala para separar textos vecinos)")
    parametro_seleccion = input("Elige 1-6: ").strip()
    parametros = {
        '1': ('box_threshold', 'YOLO'),
        '2': ('text_threshold', 'OCR'),
        '3': ('low_text', 'OCR'),
        '4': ('link_threshold', 'OCR'),
        '5': ('min_size', 'OCR_INT'),
        '6': ('x_gap_max', 'MERGE_INT'),
    }
    if parametro_seleccion not in parametros:
        print("❌ Opción inválida")
        return
    parametro, tipo_parametro = parametros[parametro_seleccion]
    perfil_valores = (
        'SMARTBOOK'
        if perfil == 'QUIZ' and tipo_parametro in ('OCR', 'OCR_INT')
        else perfil
    )
    perfil_guardado = perfil_valores
    if perfil == 'QUIZ' and perfil_valores == 'SMARTBOOK':
        print("ℹ️ Quiz y Assignments comparten los ajustes OCR de SmartBook.")

    def leer_valor():
        if tipo_parametro == 'YOLO':
            return DETECTION_PROFILES[perfil][parametro]
        if tipo_parametro == 'MERGE_INT':
            return DETECTION_PROFILES[perfil][parametro]
        return DETECTION_PROFILES[perfil_valores]['ocr'][parametro]

    def escribir_valor(valor):
        if tipo_parametro in ('YOLO', 'MERGE_INT'):
            DETECTION_PROFILES[perfil][parametro] = valor
        else:
            DETECTION_PROFILES[perfil_valores]['ocr'][parametro] = valor

    valor_guardado = leer_valor()

    if not capturar_y_copiar_pantalla():
        print("❌ No se pudo capturar la pantalla del cuestionario.")
        return

    from pathlib import Path
    preview_dir = Path(__file__).resolve().parent
    print("\nSe reutiliza esta captura para comparar thresholds.")
    print("El parser y la vista previa se vuelven a generar para cada valor.")
    print("En la vista previa, verde = texto OCR; rojo = controles YOLO.")

    while True:
        try:
            elementos = detector.detect('temp_capture.png', profile=perfil)
        except Exception as e:
            escribir_valor(valor_guardado)
            print(f"❌ Falló el parser: {e}")
            return

        preview_path = preview_dir / f"calibracion_{perfil.lower()}_{time.time_ns()}.png"
        with Image.open('temp_capture.png') as captura:
            preview = captura.convert('RGB')
        draw = ImageDraw.Draw(preview)
        for el in elementos:
            x1, y1, x2, y2 = el['bbox']
            color = '#e53935' if el['type'] != 'text' else '#1e8e3e'
            draw.rectangle((x1, y1, x2, y2), outline=color, width=2)
            draw.text((x1, max(0, y1 - 14)), f"{el['id']} {el['type']}", fill=color)
        try:
            preview.save(str(preview_path))
            preview_ok = True
        except OSError as e:
            preview_ok = False
            print(f"⚠️ No se pudo guardar la vista previa: {e}")

        valor_actual = leer_valor()
        print(f"\nPerfil {perfil} | {parametro}={valor_actual}")
        if preview_ok:
            print(f"Elementos detectados: {len(elementos)} | vista previa: {preview_path}")
        else:
            print(f"Elementos detectados: {len(elementos)} | compara la lista con la pantalla capturada")
        for el in elementos:
            contenido = el['content'][:100] if el['content'] else ''
            print(f"[{el['id']}] {el['type']}: {contenido} | bbox={el['bbox']}")
        if preview_ok:
            try:
                os.startfile(str(preview_path))
            except Exception:
                print("Abre la vista previa indicada arriba para revisar las cajas.")

        opcion = input(
            "\nThreshold numérico (0.03, 0.05, 0.10...) | [s] guardar | [r] recapturar | [v] abrir preview | [q] cancelar: "
        ).strip().lower()
        if opcion == 's':
            if guardar_thresholds(perfil_guardado):
                print(f"✅ Guardado {perfil_guardado}: {parametro}={valor_actual:.3f}")
            return
        if opcion == 'q':
            escribir_valor(valor_guardado)
            print("Calibración cancelada; se conservó el valor anterior.")
            return
        if opcion == 'v':
            if not preview_ok:
                print("No hay vista previa guardada para esta prueba.")
            else:
                try:
                    os.startfile(str(preview_path))
                except Exception as e:
                    print(f"No se pudo abrir la vista previa: {e}")
            continue
        if opcion == 'r':
            if not capturar_y_copiar_pantalla():
                print("❌ No se pudo recapturar; continúa con la captura anterior.")
            continue
        try:
            if tipo_parametro in ('OCR_INT', 'MERGE_INT'):
                nuevo = int(opcion)
                limite = (5, 100) if tipo_parametro == 'OCR_INT' else (0, 500)
                if not limite[0] <= nuevo <= limite[1]:
                    raise ValueError
            else:
                nuevo = float(opcion)
                if not 0.0 <= nuevo <= 1.0 or (tipo_parametro == 'YOLO' and nuevo == 0.0):
                    raise ValueError
            escribir_valor(nuevo)
        except ValueError:
            if tipo_parametro == 'OCR_INT':
                print("Escribe un entero entre 5 y 100, o elige s, r, v o q.")
            elif tipo_parametro == 'MERGE_INT':
                print("Escribe un entero entre 0 y 500 píxeles, o elige s, r, v o q.")
            else:
                print("Escribe un valor entre 0 y 1 (por ejemplo 0.04 o 0.75), o elige s, r, v o q.")


def copiar_prompt_chatbot():
    """Copia solo el bloque de prompt, sin la explicación ni el Markdown externo."""
    from pathlib import Path
    import re

    ruta_prompt = Path(__file__).resolve().parent / 'PROMPT_CHATBOT.md'
    try:
        contenido = ruta_prompt.read_text(encoding='utf-8')
        coincidencia = re.search(r'```text\s*\n(.*?)\n```', contenido, re.DOTALL)
        if not coincidencia:
            print("❌ No encontré el bloque de prompt delimitado por ```text en PROMPT_CHATBOT.md")
            return
        prompt = coincidencia.group(1).strip()
        pyperclip.copy(prompt)
        print(f"✅ Prompt copiado al portapapeles ({len(prompt)} caracteres).")
        print("   Se copió solo el bloque de instrucciones, sin el resto del archivo.")
        print("   Ya puedes pegarlo en el chatbot con Ctrl+V.")
    except FileNotFoundError:
        print(f"❌ No se encontró el archivo: {ruta_prompt}")
    except Exception as e:
        print(f"❌ No se pudo copiar el prompt: {e}")


def mostrar_menu():
    print("\n" + "=" * 60)
    print("🎯 SISTEMA AUTOMATIZADO (IDs + auto-scroll)")
    print("=" * 60)
    print("1. Validar coordenadas UI")
    print("2. Ejecutar automatización")
    print("3. Borrar coordenadas guardadas")
    print("4. Calibrar threshold del parser")
    print("5. Copiar prompt del chatbot al portapapeles")
    print("6. Salir")
    while True:
        op = input("\nOpción (1-6): ").strip()
        if op in '123456':
            return op

def main():
    global FLUJO_ACTUAL
    print("El detector se carga al calibrar o iniciar una ejecución. Florence-2 es opcional y está desactivado por defecto.\n")

    # Intentar cargar coordenadas persistidas
    if cargar_coordenadas_json():
        print(f"✅ Coordenadas cargadas desde {COORDENADAS_FILE}:")
        for k, v in coordenadas.items():
            print(f"   {k}: {v}")
        print()
    else:
        print(f"⚠️ No hay coordenadas en {COORDENADAS_FILE} o están incompletas.")
        print("   Usa la opción 1 para validarlas (solo la primera vez).\n")

    while True:
        op = mostrar_menu()
        if op == '1':
            if input("   ¿Sobrescribir coordenadas actuales? (s/n): ").strip().lower() == 's':
                validar_coordenadas_completas()
        elif op == '2':
            print("\nTipo de cuestionario:")
            print("1. SmartBook (el script elige prioridad y pulsa Next)")
            print("2. Quiz (responde y avanza; al final se detiene sin enviar)")
            print("3. Assignments (interacción flexible y reanálisis por turno)")
            tipo_flujo = input("Elige 1, 2 o 3: ").strip()
            if tipo_flujo not in ('1', '2', '3'):
                print("❌ Opción inválida")
                continue
            FLUJO_ACTUAL = {
                '1': 'SMARTBOOK',
                '2': 'QUIZ',
                '3': 'ASSIGNMENTS',
            }[tipo_flujo]
            requeridas = COORDENADAS_BASE + (
                COORDENADAS_PRIORIDAD if FLUJO_ACTUAL == 'SMARTBOOK' else [])
            faltantes = [k for k in requeridas if not coordenadas.get(k)]
            if faltantes:
                print(f"❌ Faltan coordenadas: {', '.join(faltantes)}. Usa la opción 1 primero.")
                continue
            usar_florence = input(
                "¿Activar Florence-2 para clasificar controles ambiguos? (s/N): "
            ).strip().lower() == 's'
            respuesta_paddle = input(
                "¿Usar PaddleOCR para leer texto? (S/n, predeterminado: sí): "
            ).strip().lower()
            usar_paddleocr = respuesta_paddle not in ('n', 'no')
            if usar_paddleocr and not inicializar_paddleocr():
                continue
            if not configurar_detector(usar_florence, usar_paddleocr):
                continue
            print("\n🚀 Iniciando en 3s...")
            time.sleep(3)
            automatizacion_completa()
            if FINISHED_FLAG:
                print(f"✅ Ejecución de {FLUJO_ACTUAL} finalizada; regreso al menú.")
        elif op == '3':
            borrar_coordenadas_json()
        elif op == '5':
            copiar_prompt_chatbot()
        elif op == '4':
            calibrar_threshold()
        elif op == '6':
            print("👋 ¡Hasta luego!")
            break

if __name__ == "__main__":
    main()
