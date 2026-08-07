"""Faux agent_tendances : contrat CLI du vrai module, sans reseau ni LLM."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _fake_module import run

raise SystemExit(run("agent_tendances"))
