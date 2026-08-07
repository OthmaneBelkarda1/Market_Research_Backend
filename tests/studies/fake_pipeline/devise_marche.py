"""Faux devise_marche : table deterministe, comme le vrai."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_module import run_resolveur

raise SystemExit(
    run_resolveur(
        "devise_marche", {"geo": "MA", "devise": "MAD", "nom": "dirham marocain", "source": "table"}
    )
)
