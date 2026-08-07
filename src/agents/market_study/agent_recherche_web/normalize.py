"""Normalisation des items bruts, troncature et statistiques de couverture.

Aucun appel LLM, aucun effet de bord : toutes les fonctions de ce module sont
pures.

Le mapping des items bruts vers `PageWeb` repose exclusivement sur le schéma
**constaté** lors des runs d'exploration de l'actor (01/08/2026), relevé dans le
README. Les noms de champs sont centralisés dans `config`.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit

from config import (
    AXE_CONSOMMATEURS,
    AXE_CONCURRENCE,
    CLE_CRAWL,
    CLE_CRAWL_STATUT_HTTP,
    CLE_MARKDOWN,
    CLE_META_LANGUE,
    CLE_META_TITRE,
    CLE_META_URL,
    CLE_META_URL_REDIRIGEE,
    CLE_METADATA,
    CLE_SEARCH_RESULT,
    CLE_SERP_RANG,
    CLE_SERP_TITRE,
    CLE_SERP_TYPE,
    CLE_SERP_URL,
    MAX_CARACTERES_PAR_PAGE,
    PREFIXE_WWW,
    TLD_EXCEPTIONS,
    obtenir_logger,
)
from schemas import PageWeb, RequetePlanifiee, StatsCouverture

_LOG = obtenir_logger(__name__)


def deriver_tld(geo: str) -> str:
    """Dérive le TLD national à cibler à partir du code pays.

    Mapping simpliste et assumé comme tel : code ISO-2 en minuscules, hors
    exceptions déclarées dans `TLD_EXCEPTIONS`. Il ne couvre ni les TLD de
    second niveau, ni les marchés dont l'audience se concentre en .com.

    Args:
        geo: Code pays ISO-2, ex. « FR », « GB ».

    Returns:
        Le TLD sans point initial, ex. « fr », « uk ».
    """
    code = geo.strip().upper()
    return TLD_EXCEPTIONS.get(code, code.lower())


def extraire_domaine(url: str) -> str:
    """Extrait le nom d'hôte d'une URL, sans préfixe « www. ».

    Args:
        url: URL absolue de la page.

    Returns:
        Le nom d'hôte en minuscules, ou une chaîne vide si l'URL est illisible.
    """
    hote = urlsplit(url.strip()).netloc.casefold()
    if hote.startswith(PREFIXE_WWW):
        hote = hote[len(PREFIXE_WWW) :]
    return hote.split(":", 1)[0]


def _texte_ou_none(valeur: object) -> str | None:
    """Nettoie un champ texte optionnel.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        Le texte débarrassé de ses espaces de bord, ou `None` s'il est vide.
    """
    if not isinstance(valeur, str):
        return None
    texte = valeur.strip()
    return texte or None


def _entier_ou_none(valeur: object) -> int | None:
    """Convertit une valeur brute en entier lorsque c'est possible.

    Args:
        valeur: Valeur brute issue du dataset.

    Returns:
        L'entier correspondant, ou `None` si la conversion est impossible.
    """
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, (int, float)):
        return int(valeur)
    return None


def _sous_objet(item: dict, cle: str) -> dict:
    """Récupère un sous-objet d'un item brut.

    Args:
        item: Item brut du dataset.
        cle: Clé du sous-objet attendu.

    Returns:
        Le sous-objet, ou un dictionnaire vide s'il est absent ou mal typé.
    """
    valeur = item.get(cle)
    return valeur if isinstance(valeur, dict) else {}


def tronquer(contenu: str) -> tuple[str, bool]:
    """Tronque un Markdown au plafond de caractères configuré.

    Args:
        contenu: Markdown complet de la page.

    Returns:
        Un couple `(contenu, tronque)`.
    """
    if len(contenu) <= MAX_CARACTERES_PAR_PAGE:
        return contenu, False
    return contenu[:MAX_CARACTERES_PAR_PAGE], True


def normaliser_pages(items: list[dict], requete: RequetePlanifiee) -> list[PageWeb]:
    """Convertit les items bruts d'un run en pages normalisées.

    L'URL est prise dans `metadata.url`, puis `metadata.redirectedUrl`, puis
    `searchResult.url` : sur les crawls en échec, `metadata` est vide et seul le
    résultat de recherche porte l'URL. Le champ `markdown` peut valoir `None`
    dans ce cas — la page est alors conservée avec un contenu vide et sera
    écartée par le filtre de longueur minimale.

    Args:
        items: Items bruts renvoyés par l'actor.
        requete: Requête à l'origine du run, dont l'axe et le ciblage sont
            reportés sur chaque page.

    Returns:
        Les pages normalisées, dans l'ordre du dataset.
    """
    pages: list[PageWeb] = []
    for item in items:
        metadata = _sous_objet(item, CLE_METADATA)
        search_result = _sous_objet(item, CLE_SEARCH_RESULT)
        crawl = _sous_objet(item, CLE_CRAWL)

        url = (
            _texte_ou_none(metadata.get(CLE_META_URL))
            or _texte_ou_none(metadata.get(CLE_META_URL_REDIRIGEE))
            or _texte_ou_none(search_result.get(CLE_SERP_URL))
        )
        if not url:
            _LOG.warning("Item sans URL exploitable ignoré.")
            continue

        markdown = _texte_ou_none(item.get(CLE_MARKDOWN)) or ""
        if not markdown:
            _LOG.info(
                "Page sans contenu extrait (HTTP %s) : %s",
                crawl.get(CLE_CRAWL_STATUT_HTTP),
                url,
            )

        contenu, tronque = tronquer(markdown)
        pages.append(
            PageWeb(
                url=url,
                domaine=extraire_domaine(url),
                titre=(
                    _texte_ou_none(metadata.get(CLE_META_TITRE))
                    or _texte_ou_none(search_result.get(CLE_SERP_TITRE))
                ),
                contenu_markdown=contenu,
                contenu_tronque=tronque,
                requete_origine=requete.texte,
                axe_cible=requete.axe,
                ciblage=requete.ciblage,
                type_source=None,
                axes_servis=[],
                portee_regionale=None,
                pertinence=None,
                marques_detectees=[],
                type_resultat_serp=_texte_ou_none(search_result.get(CLE_SERP_TYPE)),
                rang_serp=_entier_ou_none(search_result.get(CLE_SERP_RANG)),
                langue_page=_texte_ou_none(metadata.get(CLE_META_LANGUE)),
            )
        )
    return pages


def calculer_stats(
    nb_pages_collectees: int,
    pages_retenues: list[PageWeb],
    axes_sous_couverts: list[str],
    nb_doublons_ecartes: int,
    nb_pages_exclues_domaine: int,
    nb_pages_trop_courtes: int,
    nb_pages_sous_seuil: int,
    nb_pages_non_classifiees: int,
) -> StatsCouverture:
    """Calcule les statistiques descriptives du corpus.

    Les répartitions portent sur les pages retenues, seul corpus effectivement
    livré ; les décomptes de filtrage restent disponibles séparément.

    Args:
        nb_pages_collectees: Pages renvoyées par tous les runs, avant filtrage.
        pages_retenues: Pages du corpus final.
        axes_sous_couverts: Axes restés déficitaires après le cycle de repli.
        nb_doublons_ecartes: Pages écartées comme doublons d'URL.
        nb_pages_exclues_domaine: Pages écartées par la liste de domaines.
        nb_pages_trop_courtes: Pages écartées par le plancher de caractères.
        nb_pages_sous_seuil: Pages écartées par le seuil de pertinence.
        nb_pages_non_classifiees: Pages conservées sans étiquetage LLM.

    Returns:
        Les statistiques de couverture du corpus.
    """
    return StatsCouverture(
        nb_pages_collectees=nb_pages_collectees,
        nb_pages_retenues=len(pages_retenues),
        nb_pages_axe1=sum(
            1 for page in pages_retenues if AXE_CONSOMMATEURS in page.axes_servis
        ),
        nb_pages_axe2=sum(
            1 for page in pages_retenues if AXE_CONCURRENCE in page.axes_servis
        ),
        repartition_par_ciblage=dict(
            Counter(page.ciblage for page in pages_retenues).most_common()
        ),
        repartition_par_type_source=dict(
            Counter(
                page.type_source or "non_classifie" for page in pages_retenues
            ).most_common()
        ),
        repartition_par_domaine=dict(
            Counter(page.domaine for page in pages_retenues).most_common()
        ),
        axes_sous_couverts=axes_sous_couverts,
        nb_doublons_ecartes=nb_doublons_ecartes,
        nb_pages_exclues_domaine=nb_pages_exclues_domaine,
        nb_pages_trop_courtes=nb_pages_trop_courtes,
        nb_pages_sous_seuil=nb_pages_sous_seuil,
        nb_pages_non_classifiees=nb_pages_non_classifiees,
    )
