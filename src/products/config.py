"""Settings scoped to the ``products`` domain (Supabase Storage, image limits,
automated extraction)."""

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.products.constants import DEFAULT_ALLOWED_REGIONS


class ProductsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Dashboard Supabase -> Settings -> API
    SUPABASE_URL: AnyHttpUrl
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_STORAGE_BUCKET: str = "product-images"

    MAX_IMAGE_SIZE_BYTES: int = Field(default=5 * 1024 * 1024, ge=1)
    STORAGE_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)

    # --- POST /products/extract -------------------------------------------
    # Comma-separated on purpose: pydantic-settings parses a `set[str]`/`list[str]`
    # field as JSON, which would reject the natural `EXTRACTION_ALLOWED_REGIONS=MA,FR`.
    EXTRACTION_ALLOWED_REGIONS: str = DEFAULT_ALLOWED_REGIONS
    # One Chromium (~300 MB) per concurrent extraction: this bounds memory, not just
    # load. Down from 2 after a Render instance was killed for exceeding its memory
    # limit: an extraction can overlap a study's collection phase, and the browsers
    # land on top of that peak rather than beside it. Raise it with the instance.
    EXTRACTION_MAX_CONCURRENCY: int = Field(default=1, ge=1)
    # Global budget per extraction, queueing time included (see extraction.extract_product).
    EXTRACTION_TIMEOUT_SECONDS: float = Field(default=300.0, gt=0)

    @property
    def allowed_regions(self) -> frozenset[str]:
        """The region whitelist, normalized. Empty entries are ignored."""
        return frozenset(
            part.strip().upper()
            for part in self.EXTRACTION_ALLOWED_REGIONS.split(",")
            if part.strip()
        )

    @property
    def sorted_allowed_regions(self) -> list[str]:
        """Stable order, so error messages and OpenAPI examples stay reproducible."""
        return sorted(self.allowed_regions)

    def region_autorisee(self, region: str) -> bool:
        """Dit si une région passe la liste blanche.

        Une liste VIDE n'autorise pas rien, elle n'interdit rien : c'est le réglage
        par défaut, et il ouvre les régions que le pipeline sait traiter. Une liste
        renseignée restreint à ses seuls membres.

        Args:
            region: Code ISO 3166-1 alpha-2, déjà en majuscules.

        Returns:
            `True` si la région est acceptée.
        """
        return not self.allowed_regions or region in self.allowed_regions


products_settings = ProductsConfig()
