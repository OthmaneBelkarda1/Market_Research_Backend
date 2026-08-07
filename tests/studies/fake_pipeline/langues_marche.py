"""Faux langues_marche : table deterministe, comme le vrai."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_module import run_resolveur

raise SystemExit(
    run_resolveur(
        "langues_marche",
        {
            "geo": "MA",
            "codes": ["fr"],
            "langues": [{"code": "fr", "role": "principale"}],
            "reserve": "Marche a arbitrer (simule).",
            "source": "table",
        },
    )
)
