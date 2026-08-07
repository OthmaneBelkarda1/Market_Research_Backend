"""``studies`` domain -- the market study attached to a product sheet.

A study is the unit of work of the whole product: one product, one region, one language.
This lot (F8.1) owns the persistence and the lifecycle only; the pipeline that fills the
tables is wired in F8.2 behind ``service.launch_study``.

Settings are materialized here, like in ``src/products/__init__.py``, so that the domain
configuration is built before any import can reach an agent that calls ``load_dotenv``.
"""

from src import config as _root_config  # noqa: F401  -- settings built before load_dotenv
from src.studies import config as _studies_config  # noqa: F401
