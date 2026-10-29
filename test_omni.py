import json
import torch
import numpy as np
from PIL import Image
from util.utils import (
    get_yolo_model, get_caption_model_processor,
    check_ocr_box, predict_yolo,
)

device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f"Dispositivo: {device}")

print("Cargando YOLO...")
yolo_model = get_yolo_model(model_path='weights/icon_detect/model.pt')

image = Image.open('test.png').convert('RGB')
W, H = image.size
print(f"Imagen: {W}x{H}")

# ============================================================
# 1. OCR (EasyOCR) — textos del cuestionario
# ============================================================
print("\n1) Detectando texto con OCR...")
(ocr_texts, ocr_bboxes), _ = check_ocr_box(
    image, display_img=False, output_bb_format='xyxy', use_paddleocr=False,
)
print(f"   {len(ocr_texts)} bloques de texto")

# ============================================================
# 2. YOLO directo — solo íconos y botones
# ============================================================
print("\n2) Detectando íconos con YOLO...")
xyxy, conf, _ = predict_yolo(
    yolo_model, image,
    box_threshold=0.05,
    imgsz=(H, W),
    scale_img=False,
    iou_threshold=0.1,
)
xyxy = xyxy.cpu().numpy()
print(f"   {len(xyxy)} cajas crudas")

# Filtrar:
# - Fuera de la barra de Chrome (y < 0.15*H)
# - Área razonable (0.02% a 10% de la imagen) para descartar el bloque gigante
MIN_AREA = 0.0002 * W * H
MAX_AREA = 0.10 * W * H
valid_icons = []
for box in xyxy:
    x1, y1, x2, y2 = box
    area = (x2 - x1) * (y2 - y1)
    if y1 < 0.15 * H:
        continue
    if not (MIN_AREA < area < MAX_AREA):
        continue
    valid_icons.append([int(x1), int(y1), int(x2), int(y2)])
print(f"   {len(valid_icons)} íconos válidos tras filtrar")

# ============================================================
# 3. Combinar en una lista unificada de elementos
# ============================================================
elementos = []

# 3a. Textos OCR
for txt, bbox in zip(ocr_texts, ocr_bboxes):
    x1, y1, x2, y2 = bbox
    if y1 < 0.15 * H:
        continue
    if not txt.strip():
        continue
    elementos.append({
        'type': 'text',
        'content': txt,
        'center': [int((x1 + x2) / 2), int((y1 + y2) / 2)],
        'bbox': [int(x1), int(y1), int(x2), int(y2)],
    })

# 3b. Íconos YOLO (sin descripción, solo bbox)
for box in valid_icons:
    elementos.append({
        'type': 'icon',
        'content': '',
        'center': [int((box[0] + box[2]) / 2), int((box[1] + box[3]) / 2)],
        'bbox': box,
    })

# Ordenar de arriba a abajo, y de izquierda a derecha
elementos.sort(key=lambda e: (e['center'][1], e['center'][0]))

# Asignar IDs
for i, el in enumerate(elementos):
    el['id'] = i

# ============================================================
# 4. Mostrar y guardar
# ============================================================
print(f"\n✅ {len(elementos)} elementos finales:\n")
for el in elementos:
    print(f"[{el['id']:2d}] {el['type']:5s} center={el['center']} | {el['content'][:70]}")

with open('test_filtered.json', 'w', encoding='utf-8') as f:
    json.dump(elementos, f, indent=2, ensure_ascii=False)
print("\n💾 Guardado en test_filtered.json")