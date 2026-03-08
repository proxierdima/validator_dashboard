from pathlib import Path
from typing import Dict, List


class KnowledgeLoader:
    def __init__(self, knowledge_dir: str):
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)

    def list_files(self) -> List[Path]:
        result = []

        for pattern in ("*.md", "*.txt"):
            result.extend(self.knowledge_dir.glob(pattern))

        return sorted(result)

    def load_all(self) -> Dict[str, List[str]]:
        data = {
            "project_context": [],
            "admin_rules": [],
            "training_notes": []
        }

        files = self.list_files()

        for file_path in files:
            text = file_path.read_text(encoding="utf-8").strip()
            if not text:
                continue

            name = file_path.name.lower()

            if "admin" in name or "rule" in name:
                data["admin_rules"].append(f"[{file_path.name}] {text}")
            elif "train" in name or "note" in name:
                data["training_notes"].append(f"[{file_path.name}] {text}")
            else:
                data["project_context"].append(f"[{file_path.name}] {text}")

        return data
