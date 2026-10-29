"""Detector de elementos UI: YOLO + EasyOCR + heurística de clasificación."""
import torch
import numpy as np
from PIL import Image
from util.utils import get_yolo_model, check_ocr_box, predict_yolo


class UIDetector:
    def __init__(self, yolo_path='weights/icon_detect/model.pt', use_florence=False):
        """
        use_florence:
          - False (default): heurística rápida por color/forma (~5 s/iteración).
          - True: además usa Florence-2 SOLO sobre iconos del panel central
                  (~25-30 s/iteración). Mejor precisión, mucho más lento.
        """
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"   Dispositivo: {self.device}")
        self.yolo = get_yolo_model(model_path=yolo_path)
        self._ocr_warmed = False
        self.use_florence = use_florence

        if use_florence:
            from util.utils import get_caption_model_processor
            print("   Cargando Florence-2 (modo lento)...")
            self.caption_processor = get_caption_model_processor(
                model_name="florence2",
                model_name_or_path="weights/icon_caption_florence",
            )
            print("   Florence-2 listo")

    # ------------------------------------------------------------
    def detect(self, image, min_y_pct=0.18, max_y_pct=0.95,
               y_tol=22, x_gap_max=150):
        if isinstance(image, str):
            image = Image.open(image)
        image = image.convert('RGB')
        img_np = np.array(image)  # RGB
        W, H = image.size

        # 1. OCR
        (texts, bboxes), _ = check_ocr_box(
            image, display_img=False, output_bb_format='xyxy',
            use_paddleocr=False,
        )
        self._ocr_warmed = True

        # 2. YOLO
        xyxy, _, _ = predict_yolo(
            self.yolo, image, box_threshold=0.05,
            imgsz=(H, W), scale_img=False, iou_threshold=0.1,
        )
        xyxy = xyxy.cpu().numpy()

        min_area = 0.0002 * W * H
        max_area = 0.10 * W * H
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
        if self.use_florence:
            iconos_clasificados = self._enrich_with_florence(
                iconos_clasificados, img_np, W, H)

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
        elementos = self._merge_lines(elementos, y_tol, x_gap_max)

        # 5. Filtrar banda vertical
        y_min, y_max = min_y_pct * H, max_y_pct * H
        elementos = [e for e in elementos
                     if y_min < e['center'][1] < y_max]

        # 6. Ordenar y asignar IDs
        elementos.sort(key=lambda e: (e['center'][1], e['center'][0]))
        for i, el in enumerate(elementos):
            el['id'] = i
        return elementos

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

        # Llamar Florence-2 en batch (batch_size=32 por seguridad de memoria)
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

    def _caption_crops(self, crops, batch_size=32):
        """Genera captions con Florence-2 en batches."""
        from util.utils import get_parsed_content_icon

        model = self.caption_processor['model']
        processor = self.caption_processor['processor']
        device = model.device
        prompt = "<CAPTION>"

        captions = []
        for i in range(0, len(crops), batch_size):
            batch = crops[i:i+batch_size]
            inputs = processor(
                images=batch,
                text=[prompt] * len(batch),
                return_tensors="pt",
            ).to(device=device)

            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=20, num_beams=1, do_sample=False,
            )
            texts = processor.batch_decode(generated_ids, skip_special_tokens=True)
            captions.extend([t.strip() for t in texts])

        return captions

    # ------------------------------------------------------------
    def _merge_lines(self, elementos, y_tol, x_gap_max):
        texts = [e for e in elementos if e['type'] == 'text']
        others = [e for e in elementos if e['type'] != 'text']
        texts.sort(key=lambda e: (e['center'][1], e['bbox'][0]))
        merged, used = [], [False] * len(texts)

        for i, t in enumerate(texts):
            if used[i]:
                continue
            used[i] = True
            cur = {'type': 'text', 'content': t['content'],
                   'bbox': list(t['bbox']), 'center': list(t['center'])}

            changed = True
            while changed:
                changed = False
                for j, o in enumerate(texts):
                    if used[j]:
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