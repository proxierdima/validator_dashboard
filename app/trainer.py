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
