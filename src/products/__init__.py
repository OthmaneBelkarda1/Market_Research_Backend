"""``products`` domain -- the product sheet, typed by hand (F1) or extracted from a URL.

Import order matters here, which is why it is pinned in this ``__init__``.

``src/agents/product_extraction/config.py`` calls ``load_dotenv(override=True)`` at import
time: reaching the agent pushes every value of ``.env`` into ``os.environ``, *overwriting*
variables that were genuinely injected by the environment (a CI job exporting
``DATABASE_URL`` while a stale ``.env`` sits on disk).

Materializing the project's ``BaseSettings`` objects *first* makes the application
configuration deterministic regardless of that side effect: they are already built by the
time the agent can touch ``os.environ``. Python runs this module before any
``src.products.*`` submodule, so no import path can bypass the ordering.
"""

from src import config as _root_config  # noqa: F401  -- settings built before load_dotenv
from src.products import config as _products_config  # noqa: F401
