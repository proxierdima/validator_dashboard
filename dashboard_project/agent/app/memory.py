from pathlib import Path
import json

class SimpleMemory:

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

        self.file = self.base / "memory.json"

        if not self.file.exists():
            self.file.write_text(json.dumps({
                "project_context": [],
                "admin_rules": [],
                "training_notes": []
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    def read(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def write(self, data):
        self.file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def add(self, section: str, text: str):
        data = self.read()

        if section not in data:
            data[section] = []

        data[section].append(text)

        self.write(data)

        return data
