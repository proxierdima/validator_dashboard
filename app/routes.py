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
