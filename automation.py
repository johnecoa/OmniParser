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

from detector import UIDetector

# ============================================================
# CONFIGURACIÓN
# ============================================================
DEBUG = True
CAPTURA_MODO = 'printscreen'
CAPTURA_ESPERA_APERTURA = 1
CAPTURA_ESPERA_POST_CLICK = 0.3
VERIFICAR_CADA_N_PREGUNTAS = 3
SCROLL_ANTES_PRIORIDAD = True
SCROLL_ANTES_NEXT = True

# Auto-scroll al detectar bloqueo
SCROLL_PASO = -800
SCROLL_MAX_INTENTOS = 5

ULTIMA_CAPTURA = {'ancho': 0, 'alto': 0, 'offset': (0, 0), 'escala': 1.0}

coordenadas = {
    'High': None, 'Medium': None, 'Low': None,
    'PEGAR': None, 'ENVIAR': None, 'Next_Question': None,
    'ENFOCAR': None, 'INICIO': None, 'FINAL': None,
}

detector = None
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
    try:
        import win32clipboard
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, "BMP")
        data = buf.getvalue()[14:]
        buf.close()
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


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

    for tipo in ['High', 'Medium', 'Low']:
        coordenadas[tipo] = capturar_coordenada(
            tipo, [f"Ve a la opción {tipo}", "Cursor encima"])
        guardar_coordenadas_json()  # ← auto-save por si acaso

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


def build_payload(elementos):
    lineas = []
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


def _tiene_read_about(elementos):
    """¿Está visible el botón 'Read about the concept'?"""
    for el in elementos:
        txt = (el['content'] or '').lower()
        if 'read about the concept' in txt:
            return True
    return False


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
        pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
        pyautogui.scroll(SCROLL_PASO); time.sleep(0.1)
        if not capturar_y_copiar_pantalla(activar_ventana=False):
            return False, elementos
        elementos = detector.detect('temp_capture.png')
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
            acciones.append({'tipo': 'FINISHED'}); continue

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
    global FINISHED_FLAG
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

    elif tipo == 'K':
        p = accion['tecla'].split('+')
        if len(p) == 1:
            pyautogui.press(p[0])
        else:
            pyautogui.hotkey(*p)
        print(f"⌨️ Tecla '{accion['tecla']}'")

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

    elif tipo == 'FINISHED':
        print("🏁 Asignación completada")
        FINISHED_FLAG = True

    time.sleep(0.2)


# ============================================================
# CHATBOT
# ============================================================
def pegar_captura_en_chatbot():
    try:
        x, y = coordenadas['PEGAR']
        pyautogui.click(x, y); time.sleep(0.1)
        pyautogui.click(x, y); time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'v'); time.sleep(0.1)
        return True
    except Exception:
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


def pasar_a_siguiente_pregunta():
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

    if not all(coordenadas.values()):
        print("❌ Faltan coordenadas. Ejecuta validación primero.")
        return

    print("\n" + "=" * 60)
    print("AUTOMATIZACIÓN (IDs + auto-scroll en bloqueo)")
    print("=" * 60)

    try:
        n = 1
        reintentos_bloqueo = 0

        while True:
            if FINISHED_FLAG:
                print("🏁 Detenido por fin de asignación")
                break

            print(f"\n🔹 Iteración {n}")
            print("-" * 40)

            # 1. Enfocar + capturar
            pyautogui.click(*coordenadas['ENFOCAR']); time.sleep(0.1)
            if not capturar_y_copiar_pantalla():
                n += 1; continue

            # 2. Detectar elementos
            print("🔍 Detectando elementos...")
            try:
                ELEMENTOS_ACTUALES = detector.detect('temp_capture.png')
            except Exception as e:
                print(f"❌ Error detector: {e}")
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

            # 3. Enviar al chatbot
            payload = build_payload(ELEMENTOS_ACTUALES)
            if DEBUG:
                print(f"📤 Payload ({len(payload)} chars):")
                print(payload[:500] + ("..." if len(payload) > 500 else ""))

            pyperclip.copy(payload)
            pegar_captura_en_chatbot()
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

    faltantes = [k for k in coordenadas if not data.get(k)]
    if faltantes:
        print(f"⚠️ {COORDENADAS_FILE} incompleto. Faltan: {faltantes}")
        return False

    for k in coordenadas:
        v = data[k]
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


def mostrar_menu():
    print("\n" + "=" * 60)
    print("🎯 SISTEMA AUTOMATIZADO (IDs + auto-scroll)")
    print("=" * 60)
    print("1. Validar coordenadas UI")
    print("2. Ejecutar automatización")
    print("3. Borrar coordenadas guardadas")
    print("4. Salir")
    while True:
        op = input("\nOpción (1-4): ").strip()
        if op in '1234':
            return op

def main():
    global detector
    print("🔄 Cargando detector (YOLO + EasyOCR)...")
    detector = UIDetector()
    print("✅ Detector listo\n")

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
            if not all(coordenadas.values()):
                print("❌ Faltan coordenadas. Usa la opción 1 primero.")
                continue
            FINISHED_FLAG = True
            print("\n🚀 Iniciando en 3s...")
            time.sleep(3)
            automatizacion_completa()
        elif op == '3':
            borrar_coordenadas_json()
        elif op == '4':
            print("👋 ¡Hasta luego!")
            break

if __name__ == "__main__":
    main()