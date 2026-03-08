import os
from dotenv import load_dotenv

load_dotenv()

AGENT_NAME = os.getenv("AGENT_NAME", "DashPilot")
AGENT_MODE = os.getenv("AGENT_MODE", "project")
DATA_DIR = os.getenv("DATA_DIR", "./data")
