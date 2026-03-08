#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import engine
from app.models import Base

Base.metadata.create_all(bind=engine)
print("Database tables created")
