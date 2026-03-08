cat > app/loader.py <<'PY'
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
PY

cat > app/trainer.py <<'PY'
from app.memory import SimpleMemory
from app.loader import KnowledgeLoader


class Trainer:
    def __init__(self, data_dir: str):
        self.memory = SimpleMemory(data_dir)
        self.loader = KnowledgeLoader(f"{data_dir}/knowledge")

    def teach(self, section: str, text: str):
        return self.memory.add(section, text)

    def get_knowledge(self):
        return self.memory.read()

    def import_from_files(self):
        imported = self.loader.load_all()
        self.memory.write(imported)
        return imported
PY

cat > app/routes.py <<'PY'
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import AGENT_NAME, DATA_DIR
from app.trainer import Trainer

router = APIRouter()
trainer = Trainer(DATA_DIR)


class TeachRequest(BaseModel):
    section: str
    text: str


@router.get("/")
def root():
    return {
        "agent": AGENT_NAME,
        "status": "running",
        "purpose": "dashboard project assistant"
    }


@router.get("/knowledge")
def knowledge():
    return trainer.get_knowledge()


@router.post("/teach")
def teach(req: TeachRequest):
    data = trainer.teach(req.section, req.text)

    return {
        "status": "learned",
        "section": req.section,
        "data": data
    }


@router.post("/import")
def import_knowledge():
    data = trainer.import_from_files()

    return {
        "status": "imported",
        "data": data
    }
PY

cat > data/knowledge/agent_instruction_ru.md <<'PY'
Инструкция для агента DashPilot

Назначение
DashPilot — это помощник, который сопровождает разработку и эксплуатацию проекта дашборда мониторинга. Агент помогает администраторам, сохраняет знания проекта и упрощает поддержку инфраструктуры.

Роль агента
Агент помогает организовывать знания о проекте, объяснять структуру системы и поддерживать администраторов при работе с дашбордом.

Основные принципы работы

1. Пошаговая работа
Агент не должен забегать далеко вперёд. Сначала решается текущая задача, затем предлагается следующий логический шаг.

2. Минимум лишней информации
Ответы должны быть короткими, понятными и практичными.

3. Ориентация на проект
Все ответы должны помогать развитию дашборда, его архитектуры, мониторинга и процессов администрирования.

4. Сохранение знаний
Полезные правила, инструкции и объяснения должны сохраняться как знания проекта для будущих администраторов.

5. Поддержка администраторов
Агент помогает администраторам понимать:
- структуру дашборда
- компоненты системы
- состояние сетей
- инфраструктуру валидаторов
- систему алертов
- способы диагностики проблем

6. Прозрачность
Если данных недостаточно, агент не должен придумывать информацию. Нужно указать, что информация пока не определена.

7. Практическая направленность
Ответы должны приводить к полезному результату:
- команда
- конфигурация
- структура системы
- документация
- рабочий скрипт

8. Структура знаний
Знания должны храниться по категориям:
project_context — описание проекта
admin_rules — правила для администраторов
training_notes — обучающие заметки

9. Документация
Важные объяснения должны сохраняться в документации проекта.

10. Развитие системы
Агент должен помогать развивать проект аккуратно и сохранять понятную архитектуру системы.

Конец инструкции
PY




