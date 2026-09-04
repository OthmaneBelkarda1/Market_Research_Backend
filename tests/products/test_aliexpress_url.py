"""Canonicalisation des URL AliExpress avant envoi à l'actor.

Aucun réseau : la fonction est pure, et c'est tout l'intérêt de la tester ici
plutôt que par une extraction — un run d'actor coûte une minute et de l'argent.

Le cas qui a motivé ces tests : AliExpress diffuse des URL promotionnelles qui
portent l'identifiant du produit dans un paramètre plutôt que dans le chemin.

    /ssr/300000512/BundleDeals2?productIds=1005012768742030%3A12000059308786738…

Sans `item/` dans le chemin, la canonicalisation les laissait passer telles
quelles, l'actor terminait en `FAILED`, et l'extraction consommait la totalité de
son budget de 300 secondes avant de sortir en `ExtractionTimedOut`. La façon la
plus coûteuse qui soit de dire « mauvaise URL ».
"""

from __future__ import annotations

import pytest
from src.agents.product_extraction.actors import _aliexpress_canonical_url

PROMO_PRODUCTION = (
    "https://www.aliexpress.com/ssr/300000512/BundleDeals2"
    "?spm=a2g0o.home.pcJustForYou.1.72566278ZzvyXr"
    "&productIds=1005012768742030%3A12000059308786738"
    "&pha_manifest=ssr&_immersiveMode=true&disableNav=YES"
)


def test_une_url_promotionnelle_redonne_la_page_produit() -> None:
    """L'identifiant est récupéré dans `productIds`, la variante après « : » ignorée."""
    assert (
        _aliexpress_canonical_url(PROMO_PRODUCTION)
        == "https://www.aliexpress.com/item/1005012768742030.html"
    )


def test_un_identifiant_niche_dans_utparam_url_est_retrouve() -> None:
    """`utparam-url` encode ses propres paramètres : `x_object_id%3A<id>`."""
    url = (
        "https://www.aliexpress.com/ssr/300000512/X"
        "?utparam-url=scene%3Ahome%7Cx_object_id%3A1005012323403801"
    )
    assert (
        _aliexpress_canonical_url(url)
        == "https://www.aliexpress.com/item/1005012323403801.html"
    )


@pytest.mark.parametrize(
    ("url", "attendu"),
    [
        (
            "https://fr.aliexpress.com/item/1005012323403801.html?spm=a2g0o&pdp_npi=6%40dis",
            "https://www.aliexpress.com/item/1005012323403801.html",
        ),
        (
            "https://es.aliexpress.com/item/1005012323403801.html",
            "https://www.aliexpress.com/item/1005012323403801.html",
        ),
    ],
)
def test_un_hote_pays_est_ramene_sur_www_sans_son_tracking(url: str, attendu: str) -> None:
    """Comportement d'origine, préservé : les hôtes pays redirigent, `www.` scrape."""
    assert _aliexpress_canonical_url(url) == attendu


def test_le_tld_us_est_conserve() -> None:
    """`aliexpress.us` est une boutique distincte, pas une traduction de `.com`.

    La réécrire scraperait une autre annonce, à un autre prix.
    """
    url = "https://www.aliexpress.us/item/3256801234567890.html"
    assert _aliexpress_canonical_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        # Aucun identifiant à récupérer : mieux vaut laisser l'actor répondre que
        # fabriquer une URL au hasard.
        "https://www.aliexpress.com/category/100003109/women-clothing.html",
        # Un identifiant trop court est un identifiant de session, pas d'article.
        "https://www.aliexpress.com/ssr/300000512/X?productIds=12345",
        # Un autre domaine ne doit jamais devenir une URL AliExpress, quel que soit
        # ce que sa query string contient.
        "https://www.amazon.fr/dp/B0TEST?productIds=1005012768742030",
    ],
)
def test_une_url_sans_identifiant_exploitable_reste_intacte(url: str) -> None:
    assert _aliexpress_canonical_url(url) == url
