import json
from detector import UIDetector

det = UIDetector()
elementos = det.detect('test.png')

print(f"\n✅ {len(elementos)} elementos:\n")
for el in elementos:
    print(f"[{el['id']:2d}] {el['type']:5s} center={el['center']} | {el['content'][:80]}")

with open('test_filtered.json', 'w', encoding='utf-8') as f:
    json.dump(elementos, f, indent=2, ensure_ascii=False)

