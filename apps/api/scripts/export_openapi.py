import json
from pathlib import Path

from forge_erp.main import app

path = Path(__file__).resolve().parents[3] / "docs/api/openapi.json"
path.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
print(f"Exported {path.name}")
