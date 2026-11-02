"""Detector de elementos UI: YOLO + EasyOCR + heurística de clasificación."""
import torch
import numpy as np
import json
import re
import time
from pathlib import Path
from PIL import Image
from util.utils import get_yolo_model, check_ocr_box, predict_yolo


# SmartBook conserva el perfil histórico. Quiz mantiene sus umbrales de controles
# y agrupación, pero comparte el OCR de SmartBook para que ambos modos lean texto
# con la misma configuración.
DETECTION_PROFILES = {
    'SMARTBOOK': {
        'box_threshold': 0.05,
        'iou_threshold': 0.1,
        'min_area_ratio': 0.0002,
        'max_area_ratio': 0.10,
        'min_y_pct': 0.18,
        'max_y_pct': 0.95,
        'y_tol': 22,
        'x_gap_max': 150,
        'ocr': {'text_threshold': 0.7, 'low_text': 0.4, 'link_threshold': 0.4,
                'min_size': 20, 'batch_size': 2},
        'isolate_navigation_labels': False,
    },
    'QUIZ': {
        'box_threshold': 0.03,
        'iou_threshold': 0.1,
        'min_area_ratio': 0.0001,
        'max_area_ratio': 0.15,
        'min_y_pct': 0.05,
        'max_y_pct': 0.98,
        'y_tol': 22,
        'x_gap_max': 50,
        'ocr': {'text_threshold': 0.7, 'low_text': 0.4, 'link_threshold': 0.4,
                'min_size': 10, 'batch_size': 2},
        'isolate_navigation_labels': True,
    },
}

DETECTION_THRESHOLD_FILE = Path(__file__).resolve().parent / 'detector_thresholds.json'


def cargar_thresholds_guardados():
    """Carga los thresholds por perfil, si el usuario ya calibró alguno."""
    try:
        saved = json.loads(DETECTION_THRESHOLD_FILE.read_text(encoding='utf-8'))
        for profile, values in saved.items():
            if profile not in DETECTION_PROFILES or not isinstance(values, dict):
                continue
            threshold = float(values.get('box_threshold', -1))
            if 0.001 <= threshold <= 1.0:
                DETECTION_PROFILES[profile]['box_threshold'] = threshold
            saved_ocr = values.get('ocr', {})
            for key in ('text_threshold', 'low_text', 'link_threshold'):
                if key in saved_ocr:
                    ocr_value = float(saved_ocr[key])
                    if 0.0 <= ocr_value <= 1.0:
                        DETECTION_PROFILES[profile]['ocr'][key] = ocr_value
            if 'min_size' in saved_ocr:
                min_size = int(saved_ocr['min_size'])
                if 5 <= min_size <= 100:
                    DETECTION_PROFILES[profile]['ocr']['min_size'] = min_size
            if 'batch_size' in saved_ocr:
                batch_size = int(saved_ocr['batch_size'])
                if 1 <= batch_size <= 16:
                    DETECTION_PROFILES[profile]['ocr']['batch_size'] = batch_size
            if 'x_gap_max' in values:
                x_gap = int(values['x_gap_max'])
                if 0 <= x_gap <= 500:
                    DETECTION_PROFILES[profile]['x_gap_max'] = x_gap
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ No se pudieron cargar los thresholds guardados: {e}")


def guardar_thresholds(profile):
    """Persiste el threshold de un perfil sin alterar los demás."""
    try:
        try:
            saved = json.loads(DETECTION_THRESHOLD_FILE.read_text(encoding='utf-8'))
        except FileNotFoundError:
            saved = {}
        saved_profile = saved.setdefault(profile, {})
        saved_profile['box_threshold'] = DETECTION_PROFILES[profile]['box_threshold']
        saved_profile['ocr'] = DETECTION_PROFILES[profile]['ocr'].copy()
        saved_profile['x_gap_max'] = DETECTION_PROFILES[profile]['x_gap_max']
        DETECTION_THRESHOLD_FILE.write_text(
            json.dumps(saved, indent=2), encoding='utf-8')
        return True
    except Exception as e:
        print(f"❌ No se pudo guardar {DETECTION_THRESHOLD_FILE.name}: {e}")
        return False


cargar_thresholds_guardados()


class UIDetector:
    def __init__(self, yolo_path='weights/icon_detect/model.pt', use_florence=False,
                 use_paddleocr=True):
        """
        use_florence:
          - False (default): heurística rápida por color/forma (~5 s/iteración).
          - True: además usa Florence-2 SOLO sobre iconos del panel central
                  para añadir descripciones y clasificar controles ambiguos.
                  No sustituye el motor OCR elegido ni cambia la lectura del texto; es más lento.
        """
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"   Dispositivo: {self.device}")
        self.yolo = get_yolo_model(model_path=yolo_path)
        self._ocr_warmed = False
        self.use_florence = use_florence
        self.use_paddleocr = use_paddleocr

        if use_florence:
            from util.utils import get_caption_model_processor
            print("   Cargando Florence-2 (modo lento)...")
            self.caption_processor = get_caption_model_processor(
                model_name="florence2",
                model_name_or_path="weights/icon_caption_florence",
            )
            print("   Florence-2 listo")

    # ------------------------------------------------------------
    def detect(self, image, min_y_pct=None, max_y_pct=None,
               y_tol=None, x_gap_max=None, profile='SMARTBOOK'):
        tiempo_inicio = time.perf_counter()
        if profile not in DETECTION_PROFILES:
            raise ValueError(f"Perfil de parser desconocido: {profile}")
        config = DETECTION_PROFILES[profile]
        config_ocr = (DETECTION_PROFILES['SMARTBOOK']['ocr']
                      if profile == 'QUIZ' else config['ocr'])
        min_y_pct = config['min_y_pct'] if min_y_pct is None else min_y_pct
        max_y_pct = config['max_y_pct'] if max_y_pct is None else max_y_pct
        y_tol = config['y_tol'] if y_tol is None else y_tol
        x_gap_max = config['x_gap_max'] if x_gap_max is None else x_gap_max

        if isinstance(image, str):
            image = Image.open(image)
        image = image.convert('RGB')
        img_np = np.array(image)  # RGB
        W, H = image.size

        # 1. OCR
        tiempo_fase = time.perf_counter()
        (texts, bboxes), _ = check_ocr_box(
            img_np, display_img=False, output_bb_format='xyxy',
            use_paddleocr=self.use_paddleocr,
            easyocr_args=config_ocr.copy(),
        )
        tiempo_ocr = time.perf_counter() - tiempo_fase
        self._ocr_warmed = True

        # 2. YOLO
        tiempo_fase = time.perf_counter()
        xyxy, _, _ = predict_yolo(
            self.yolo, image, box_threshold=config['box_threshold'],
            imgsz=(H, W), scale_img=False,
            iou_threshold=config['iou_threshold'],
        )
        xyxy = xyxy.cpu().numpy()
        tiempo_yolo = time.perf_counter() - tiempo_fase

        min_area = config['min_area_ratio'] * W * H
        max_area = config['max_area_ratio'] * W * H
        icons_raw = []
        for box in xyxy:
            x1, y1, x2, y2 = box
            area = (x2 - x1) * (y2 - y1)
            if y1 < min_y_pct * H:
                continue
            if not (min_area < area < max_area):
                continue
            icons_raw.append([int(x1), int(y1), int(x2), int(y2)])

        # 2b. Clasificar cada icono
        iconos_clasificados = []
        for box in icons_raw:
            tipo = self._clasificar(box, img_np)
            iconos_clasificados.append({'type': tipo, 'bbox': box, 'content': ''})

        # 2c. Si use_florence, enriquecer iconos "ambiguos" del panel central
        tiempo_florence = 0.0
        if self.use_florence:
            tiempo_fase = time.perf_counter()
            iconos_clasificados = self._enrich_with_florence(
                iconos_clasificados, img_np, W, H)
            tiempo_florence = time.perf_counter() - tiempo_fase

        # 3. Construir elementos
        elementos = []
        for txt, bbox in zip(texts, bboxes):
            x1, y1, x2, y2 = bbox
            if not txt.strip():
                continue
            elementos.append({
                'type': 'text', 'content': txt.strip(),
                'bbox': [int(x1), int(y1), int(x2), int(y2)],
                'center': [int((x1 + x2) / 2), int((y1 + y2) / 2)],
            })
        for ic in iconos_clasificados:
            box = ic['bbox']
            elementos.append({
                'type': ic['type'], 'content': ic['content'],
                'bbox': box,
                'center': [int((box[0] + box[2]) / 2),
                           int((box[1] + box[3]) / 2)],
            })

        # 4. Merge textos
        tiempo_fase = time.perf_counter()
        elementos = self._merge_lines(
            elementos, y_tol, x_gap_max,
            isolate_navigation_labels=config['isolate_navigation_labels'],
        )
        if config['isolate_navigation_labels']:
            elementos = self._separar_next_en_pie(elementos, H)

        # 5. Filtrar banda vertical
        y_min, y_max = min_y_pct * H, max_y_pct * H
        elementos = [e for e in elementos
                     if y_min < e['center'][1] < y_max]

        # 6. Ordenar y asignar IDs
        elementos.sort(key=lambda e: (e['center'][1], e['center'][0]))
        for i, el in enumerate(elementos):
            el['id'] = i
        tiempo_post = time.perf_counter() - tiempo_fase
        print(
            f"⏱️ Parser: OCR {tiempo_ocr:.1f}s | YOLO {tiempo_yolo:.1f}s | "
            f"Florence {tiempo_florence:.1f}s | post {tiempo_post:.1f}s | "
            f"total {time.perf_counter() - tiempo_inicio:.1f}s"
        )
        return elementos

    def _separar_next_en_pie(self, elementos, image_height):
        """Aísla un Next final que EasyOCR haya unido al texto de paginación."""
        patron = re.compile(r'^(.*?)\s+(next)\s*(?:[>›»→])?\s*$', re.IGNORECASE)
        resultado = []
        for el in elementos:
            texto = (el.get('content') or '').strip()
            if (el['type'] != 'text' or el['center'][1] < 0.85 * image_height):
                resultado.append(el)
                continue

            match = patron.match(texto)
            if not match or not match.group(1).strip():
                resultado.append(el)
                continue

            prefijo = match.group(1).strip()
            x1, y1, x2, y2 = el['bbox']
            alto_texto = max(10, y2 - y1)
            ancho_next = min(x2 - x1, max(24, int(alto_texto * 2.3)))
            corte = x2 - ancho_next
            caja_prefijo = [x1, y1, corte, y2]
            caja_next = [corte, y1, x2, y2]

            resultado.append({
                **el, 'content': prefijo, 'bbox': caja_prefijo,
                'center': [(x1 + corte) // 2, (y1 + y2) // 2],
            })
            resultado.append({
                **el, 'content': 'Next', 'bbox': caja_next,
                'center': [(corte + x2) // 2, (y1 + y2) // 2],
            })
        return resultado

    # ------------------------------------------------------------
    def _clasificar(self, box, img_np):
        """Clasifica un bbox como input/radio/button/icon por forma y color."""
        x1, y1, x2, y2 = box
        w, h = x2 - x1, y2 - y1
        if h <= 0 or w <= 0:
            return 'icon'
        ratio = w / h
        crop = img_np[y1:y2, x1:x2]
        if crop.size == 0:
            return 'icon'
        mean_rgb = crop.reshape(-1, 3).mean(axis=0)

        # Input: fondo muy blanco, ancho y bajo
        es_blanco = all(c > 245 for c in mean_rgb)
        if es_blanco and ratio > 2.0 and 20 < h < 70 and 80 < w < 600:
            return 'input'

        # Radio/checkbox: cuadrado pequeño
        if 0.7 < ratio < 1.4 and 12 < h < 35 and 12 < w < 35:
            return 'radio'

        # Botón: rectángulo mediano-grande con contenido
        if w > 100 and h > 30:
            return 'button'

        return 'icon'

    # ------------------------------------------------------------
    def _enrich_with_florence(self, iconos, img_np, W, H):
        """Usa Florence-2 SOLO en iconos del panel central (rápido selectivo)."""
        from util.utils import get_parsed_content_icon
        from torchvision.transforms import ToPILImage

        to_pil = ToPILImage()
        # Filtrar iconos del panel central que aún no sabemos qué son
        candidatos = []
        idxs = []
        for i, ic in enumerate(iconos):
            box = ic['bbox']
            cx = (box[0] + box[2]) / 2
            cy = (box[1] + box[3]) / 2
            # Solo panel central: x entre 5% y 95%, y entre 25% y 80%
            if not (0.05 * W < cx < 0.95 * W):
                continue
            if not (0.25 * H < cy < 0.80 * H):
                continue
            if ic['type'] in ('input', 'radio'):
                continue  # ya clasificado por heurística
            candidatos.append(ic)
            idxs.append(i)

        if not candidatos:
            return iconos

        print(f"   Florence-2 procesando {len(candidatos)} iconos del panel...")

        # Preparar crops
        crops = []
        for ic in candidatos:
            x1, y1, x2, y2 = ic['bbox']
            crop = img_np[y1:y2, x1:x2]
            if crop.size == 0:
                crops.append(to_pil(img_np[0:10, 0:10]))
                continue
            crops.append(to_pil(crop))

        # Procesar cada recorte individualmente: en CPU, el lote completo de
        # iconos puede disparar el uso de RAM y terminar el proceso sin traceback.
        try:
            captions = self._caption_crops(crops)
        except Exception as e:
            print(f"   ⚠️ Florence-2 falló: {e}")
            return iconos

        for idx, cap in zip(idxs, captions):
            cap_lower = cap.lower()
            # Reclasificar por caption
            if any(p in cap_lower for p in ['text input', 'text box',
                                            'text field', 'input field']):
                iconos[idx]['type'] = 'input'
            elif any(p in cap_lower for p in ['radio button', 'checkbox',
                                              'circular', 'circle']):
                iconos[idx]['type'] = 'radio'
            elif 'button' in cap_lower:
                iconos[idx]['type'] = 'button'
            iconos[idx]['content'] = cap[:60]  # guardar descripción breve

        return iconos

    def _caption_crops(self, crops, batch_size=None):
        """Genera captions en lotes pequeños para equilibrar RAM y velocidad."""
        import gc
        import ctypes
        import os

        model = self.caption_processor['model']
        processor = self.caption_processor['processor']
        device = model.device
        prompt = "<CAPTION>"

        memoria_libre_gb = None
        if device.type == 'cuda':
            try:
                memoria_libre_gb = torch.cuda.mem_get_info(device)[0] / (1024 ** 3)
            except Exception:
                pass
        elif os.name == 'nt':
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]

            try:
                memoria = MEMORYSTATUSEX()
                memoria.dwLength = ctypes.sizeof(memoria)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memoria)):
                    memoria_libre_gb = memoria.ullAvailPhys / (1024 ** 3)
            except Exception:
                pass

        if batch_size is None:
            if device.type == 'cuda':
                batch_size = 4 if memoria_libre_gb and memoria_libre_gb >= 8 else 2
            else:
                batch_size = (
                    3 if memoria_libre_gb and memoria_libre_gb >= 5 else
                    2 if memoria_libre_gb and memoria_libre_gb >= 2.5 else
                    1
                )
        print(
            f"   Florence-2 lote={batch_size}"
            + (f" | RAM/VRAM libre={memoria_libre_gb:.1f} GB" if memoria_libre_gb is not None else '')
        )

        captions = []
        for inicio in range(0, len(crops), batch_size):
            batch = crops[inicio:inicio + batch_size]
            inputs = processor(
                images=batch, text=[prompt] * len(batch), return_tensors="pt",
            ).to(device=device)
            with torch.inference_mode():
                generated_ids = model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=12, num_beams=1, do_sample=False,
                )
            texts = processor.batch_decode(generated_ids, skip_special_tokens=True)
            captions.extend(t.strip() for t in texts)
            procesados = min(inicio + len(batch), len(crops))
            print(f"   Florence-2: {procesados}/{len(crops)} iconos procesados")
            del inputs, generated_ids, texts
            if device.type == 'cpu':
                gc.collect()

        return captions

    # ------------------------------------------------------------
    def _merge_lines(self, elementos, y_tol, x_gap_max,
                     isolate_navigation_labels=False):
        texts = [e for e in elementos if e['type'] == 'text']
        others = [e for e in elementos if e['type'] != 'text']
        texts.sort(key=lambda e: (e['center'][1], e['bbox'][0]))
        merged, used = [], [False] * len(texts)

        def es_etiqueta_navegacion(elemento):
            texto = (elemento.get('content') or '').strip().lower()
            texto = texto.rstrip(' .,:;|>›»→-')
            return texto in {'next', 'prev', 'previous'}

        for i, t in enumerate(texts):
            if used[i]:
                continue
            used[i] = True
            if isolate_navigation_labels and es_etiqueta_navegacion(t):
                merged.append(t)
                continue
            cur = {'type': 'text', 'content': t['content'],
                   'bbox': list(t['bbox']), 'center': list(t['center'])}

            changed = True
            while changed:
                changed = False
                for j, o in enumerate(texts):
                    if used[j]:
                        continue
                    if isolate_navigation_labels and es_etiqueta_navegacion(o):
                        continue
                    if abs(o['center'][1] - cur['center'][1]) > y_tol:
                        continue
                    # derecha
                    if o['bbox'][0] >= cur['bbox'][2] - 10:
                        gap = o['bbox'][0] - cur['bbox'][2]
                        if gap <= x_gap_max:
                            between = any(
                                el['type'] == 'input'
                                and el['bbox'][0] >= cur['bbox'][2] - 5
                                and el['bbox'][2] <= o['bbox'][0] + 5
                                and abs(el['center'][1] - cur['center'][1]) < y_tol * 2
                                for el in elementos
                            )
                            if between:
                                continue
                            cur['content'] += ' ' + o['content']
                            cur['bbox'][2] = o['bbox'][2]
                            cur['center'][0] = (cur['bbox'][0] + cur['bbox'][2]) // 2
                            used[j] = True
                            changed = True
                    # izquierda
                    elif o['bbox'][2] <= cur['bbox'][0] + 10:
                        gap = cur['bbox'][0] - o['bbox'][2]
                        if gap <= x_gap_max:
                            between = any(
                                el['type'] == 'input'
                                and el['bbox'][0] >= o['bbox'][2] - 5
                                and el['bbox'][2] <= cur['bbox'][0] + 5
                                and abs(el['center'][1] - cur['center'][1]) < y_tol * 2
                                for el in elementos
                            )
                            if between:
                                continue
                            cur['content'] = o['content'] + ' ' + cur['content']
                            cur['bbox'][0] = o['bbox'][0]
                            cur['center'][0] = (cur['bbox'][0] + cur['bbox'][2]) // 2
                            used[j] = True
                            changed = True
            merged.append(cur)
        return merged + others
