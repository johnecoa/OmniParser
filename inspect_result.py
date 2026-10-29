import json

with open('test_result.json', encoding='utf-8') as f:
    data = json.load(f)

# Solo elementos del área del cuestionario (y > 0.15)
relevantes = [e for e in data if e['bbox'][1] > 0.15]

print(f"Total: {len(data)} | Relevantes (y>0.15): {len(relevantes)}\n")

for e in relevantes:
    x1, y1, x2, y2 = e['bbox']
    w_px = int((x2 - x1) * 1920)
    h_px = int((y2 - y1) * 1020)
    content = e.get('content') or '(vacio)'
    print(f"{e['type']:5s} bbox_norm=[{x1:.2f},{y1:.2f},{x2:.2f},{y2:.2f}] "
          f"size_px={w_px}x{h_px} | {content[:80]}")