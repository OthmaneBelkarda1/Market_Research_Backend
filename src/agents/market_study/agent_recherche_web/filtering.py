"""Filtres déterministes et classification LLM du corpus de pages.

Deux étages nettement séparés :

1. des filtres **sans LLM** — dédoublonnage par URL normalisée, exclusion des
   domaines déjà couverts par les autres collecteurs, élimination des pages trop
   courtes ;
2. une **classification LLM par lots**, qui étiquette chaque page (type de
   source, axes servis, portée régionale, pertinence, marques citées) sans
   jamais interpréter son contenu.

La classification se dégrade gracieusement : un lot en échec après nouvelle
tentative laisse ses pages dans le corpus, champs à `None`, plutôt que de les
supprimer. Un échec LLM ne vide jamais le corpus.
"""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    AXES_ANALYSE,
    AXE_MIXTE,
    DOMAINES_EXCLUS,
    LIBELLES_AXES,
    LONGUEUR_EXTRAIT_CLASSIFICATION,
    MAX_TOKENS_LLM,
    MIN_CARACTERES_PAGE,
    MODELE_CLAUDE,
    PARAMETRES_URL_IGNORES,
    SEUIL_PERTINENCE,
    TAILLE_LOT_CLASSIFICATION,
    TEMPERATURE_LLM,
    TYPES_SOURCE,
    obtenir_logger,
)
from schemas import (
    ClassificationPage,
    FicheProduit,
    LotClassification,
    PageWeb,
    ParametresMarche,
)

_LOG = obtenir_logger(__name__)

_TYPE_SOURCE_PAR_DEFAUT = "autre"
_NB_TENTATIVES_CLASSIFICATION = 2
_SEPARATEUR_PAGES = "\n\n---\n\n"


class CompteursFiltrage(NamedTuple):
    """Décompte des pages écartées par chaque filtre déterministe."""

    doublons: int
    domaines_exclus: int
    trop_courtes: int


_PROMPT_CLASSIFICATION = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu es documentaliste. Tu ÉTIQUETTES des pages web collectées pour "
            "une étude de marché. Tu ne résumes pas, tu n'analyses pas le fond, "
            "tu ne portes aucun jugement sur le produit : tu classes.\n\n"
            "Pour CHAQUE page du lot, renvoie une entrée avec son `index` "
            "(celui affiché dans le lot) et :\n\n"
            "1. `type_source`, une valeur parmi : "
            + ", ".join(f"« {valeur} »" for valeur in TYPES_SOURCE)
            + ".\n"
            "   - « comparatif » : la page classe ou compare plusieurs produits "
            "ou marques (« meilleurs X », « top 10 », « X vs Y »).\n"
            "   - « test_avis » : la page teste ou donne un avis argumenté sur "
            "UN produit.\n"
            "   - « article_presse » : contenu rédactionnel d'un média établi.\n"
            "   - « blog » : publication personnelle ou de niche.\n"
            "   - « site_marque » : site officiel d'un fabricant.\n"
            "   - « site_marchand » : page de vente ou catalogue d'un "
            "distributeur.\n"
            "   - « forum » : fil de discussion entre utilisateurs.\n"
            "   - « autre » : rien de ce qui précède.\n\n"
            "2. `axes_servis`, liste NON VIDE de « {axe1} » et/ou « {axe2} » :\n"
            "   - « {axe1} » = axe {libelle_axe1}. Retiens-le si la page "
            "rapporte des tests, des avis, des retours d'usage ou des problèmes "
            "rencontrés.\n"
            "   - « {axe2} » = axe {libelle_axe2}. Retiens-le si la page compare "
            "des produits, cite des marques concurrentes ou expose un "
            "positionnement commercial.\n"
            "   - Une même page peut servir les deux axes : un comparatif "
            "détaillant les défauts de chaque modèle sert les deux.\n\n"
            "3. `portee_regionale` : vrai si la page concerne le marché étudié "
            "(pays {geo}, langue {langue}). Juge-en par des indices FACTUELS : "
            "langue de rédaction, devise et niveau de prix affichés, enseignes "
            "et distributeurs cités, extension du nom de domaine, mentions "
            "légales. Une page rédigée dans la langue du marché mais visant un "
            "autre pays n'est PAS de portée régionale.\n\n"
            "4. `pertinence` : score de 0 à 1 mesurant le rapport de la page au "
            "PRODUIT ÉTUDIÉ ou à sa catégorie. 0 = hors sujet complet ; 0,5 = "
            "traite de la catégorie sans entrer dans le sujet ; 1 = traite "
            "directement du produit ou de ses concurrents immédiats. Sois "
            "sévère : une page d'actualité générale qui cite le produit en "
            "passant n'est pas pertinente.\n\n"
            "5. `marques_detectees` : liste BRUTE des marques et fabricants "
            "cités dans l'extrait. Aucune analyse, aucun classement, aucune "
            "déduction — uniquement ce qui est écrit. Liste vide si aucune.\n\n"
            "Renvoie exactement une entrée par page du lot, sans en omettre "
            "aucune.",
        ),
        (
            "human",
            "Produit étudié : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}\n"
            "Marché : pays={geo}, langue={langue}\n\n"
            "LOT DE PAGES À ÉTIQUETER :\n\n{lot}",
        ),
    ]
)


def _modele() -> ChatAnthropic:
    """Instancie le modèle Claude utilisé par la classification.

    Returns:
        Le client `ChatAnthropic` configuré.

    Raises:
        RuntimeError: Si `ANTHROPIC_API_KEY` est absente de l'environnement.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY absente de l'environnement.")
    return ChatAnthropic(
        model=MODELE_CLAUDE,
        temperature=TEMPERATURE_LLM,
        max_tokens=MAX_TOKENS_LLM,
        api_key=ANTHROPIC_API_KEY,
    )


# --------------------------------------------------------------------------- #
# Filtres déterministes
# --------------------------------------------------------------------------- #


def normaliser_url(url: str) -> str:
    """Produit la forme comparable d'une URL, pour le dédoublonnage.

    Trois normalisations : le fragment est retiré, la barre oblique finale est
    ignorée, et les paramètres de tracking publicitaire de
    `PARAMETRES_URL_IGNORES` sont supprimés. Sans ce dernier point, le
    dédoublonnage laisse passer les pages remontées plusieurs fois par la SERP,
    Google apposant un `srsltid` différent à chaque clic.

    Args:
        url: URL brute.

    Returns:
        L'URL normalisée en minuscules.
    """
    parties = urlsplit(url.strip())
    parametres = [
        (cle, valeur)
        for cle, valeur in parse_qsl(parties.query, keep_blank_values=True)
        if cle.casefold() not in PARAMETRES_URL_IGNORES
    ]
    sans_fragment = urlunsplit(
        (parties.scheme, parties.netloc, parties.path, urlencode(parametres), "")
    )
    return sans_fragment.rstrip("?/").casefold()


def _est_domaine_exclu(domaine: str) -> bool:
    """Indique si un domaine figure dans la liste d'exclusion.

    La comparaison est un test de sous-chaîne : les entrées se terminant par un
    point (« amazon. ») couvrent toutes les déclinaisons nationales, celles
    portant un TLD (« reddit.com ») ne visent que ce domaine.

    Args:
        domaine: Nom d'hôte normalisé de la page.

    Returns:
        Vrai si la page doit être écartée.
    """
    return any(exclu in domaine for exclu in DOMAINES_EXCLUS)


def filtrer_deterministe(
    pages: list[PageWeb], urls_vues: set[str]
) -> tuple[list[PageWeb], CompteursFiltrage]:
    """Applique les filtres sans LLM à un lot de pages fraîchement collectées.

    Les requêtes du plan se recouvrent largement : une même page remonte
    fréquemment sur plusieurs d'entre elles. Le dédoublonnage se fait sur l'URL
    normalisée, et `urls_vues` est enrichi au passage pour que les cycles de
    collecte successifs (repli compris) restent cohérents entre eux.

    Args:
        pages: Pages normalisées d'un ou plusieurs runs.
        urls_vues: URLs déjà retenues, **modifié en place**.

    Returns:
        Un couple `(pages_retenues, compteurs)`.
    """
    retenues: list[PageWeb] = []
    doublons = 0
    exclues = 0
    courtes = 0

    for page in pages:
        cle = normaliser_url(page.url)
        if cle in urls_vues:
            doublons += 1
            continue
        if _est_domaine_exclu(page.domaine):
            exclues += 1
            _LOG.info("Domaine exclu (couvert par un autre collecteur) : %s", page.domaine)
            continue
        if len(page.contenu_markdown) < MIN_CARACTERES_PAGE:
            courtes += 1
            _LOG.info(
                "Page écartée, contenu insuffisant (%s caractères) : %s",
                len(page.contenu_markdown),
                page.url,
            )
            continue
        urls_vues.add(cle)
        retenues.append(page)

    compteurs = CompteursFiltrage(doublons, exclues, courtes)
    _LOG.info(
        "Filtrage déterministe : %s page(s) sur %s retenue(s) — %s doublon(s), "
        "%s domaine(s) exclu(s), %s page(s) trop courte(s).",
        len(retenues),
        len(pages),
        doublons,
        exclues,
        courtes,
    )
    return retenues, compteurs


def appliquer_seuil_pertinence(pages: list[PageWeb]) -> tuple[list[PageWeb], int]:
    """Écarte du corpus les pages sous le seuil de pertinence.

    Les pages non classifiées (`pertinence=None`) sont CONSERVÉES : leur score
    est inconnu, pas nul, et un échec de classification ne doit pas amputer le
    corpus.

    Args:
        pages: Pages classifiées ou non.

    Returns:
        Un couple `(pages_retenues, nb_ecartees)`.
    """
    retenues = [
        page
        for page in pages
        if page.pertinence is None or page.pertinence >= SEUIL_PERTINENCE
    ]
    ecartees = len(pages) - len(retenues)
    if ecartees:
        _LOG.info(
            "Seuil de pertinence (%s) : %s page(s) écartée(s).",
            SEUIL_PERTINENCE,
            ecartees,
        )
    return retenues, ecartees


# --------------------------------------------------------------------------- #
# Classification LLM
# --------------------------------------------------------------------------- #


def _axes_de_repli(page: PageWeb) -> list[str]:
    """Détermine les axes attribués à une page non classifiée.

    L'axe de la requête d'origine sert de repli. Pour une requête ouverte, dont
    l'axe est « mixte », les deux axes sont retenus : l'axe réellement servi est
    inconnu, et n'en retenir aucun retirerait la page des deux décomptes de
    couverture alors qu'elle figure bien dans le corpus.

    Args:
        page: Page dont la classification a échoué.

    Returns:
        Les axes attribués par défaut.
    """
    if page.axe_cible == AXE_MIXTE:
        return list(AXES_ANALYSE)
    return [page.axe_cible]


def _formater_lot(lot: list[PageWeb]) -> str:
    """Met en forme un lot de pages pour le prompt de classification.

    Args:
        lot: Pages du lot, dans l'ordre d'indexation.

    Returns:
        Le bloc texte soumis au modèle.
    """
    blocs = []
    for index, page in enumerate(lot):
        extrait = page.contenu_markdown[:LONGUEUR_EXTRAIT_CLASSIFICATION]
        blocs.append(
            f"index: {index}\n"
            f"titre: {page.titre or '(sans titre)'}\n"
            f"url: {page.url}\n"
            f"requête d'origine: {page.requete_origine}\n"
            f"extrait:\n{extrait}"
        )
    return _SEPARATEUR_PAGES.join(blocs)


def _appliquer_classification(
    page: PageWeb, classification: ClassificationPage
) -> PageWeb:
    """Reporte un étiquetage LLM sur une page, après validation des valeurs.

    Les valeurs hors nomenclature sont ramenées à un repli sûr plutôt que
    rejetées : un `type_source` inventé devient « autre », une liste d'axes vide
    ou invalide retombe sur l'axe de la requête d'origine, et le score est borné
    à l'intervalle [0, 1].

    Args:
        page: Page à étiqueter.
        classification: Étiquetage produit par le modèle.

    Returns:
        La page enrichie de ses étiquettes.
    """
    type_source = classification.type_source.strip().casefold()
    if type_source not in TYPES_SOURCE:
        _LOG.warning(
            "Type de source hors nomenclature (« %s ») ramené à « %s » : %s",
            classification.type_source,
            _TYPE_SOURCE_PAR_DEFAUT,
            page.url,
        )
        type_source = _TYPE_SOURCE_PAR_DEFAUT

    axes = [
        axe.strip().casefold()
        for axe in classification.axes_servis
        if axe.strip().casefold() in AXES_ANALYSE
    ]
    if not axes:
        _LOG.warning("Axes servis absents ou invalides, repli sur l'axe ciblé : %s", page.url)
        axes = _axes_de_repli(page)

    marques = []
    vues: set[str] = set()
    for marque in classification.marques_detectees:
        propre = marque.strip()
        if propre and propre.casefold() not in vues:
            vues.add(propre.casefold())
            marques.append(propre)

    page.type_source = type_source
    page.axes_servis = list(dict.fromkeys(axes))
    page.portee_regionale = classification.portee_regionale
    page.pertinence = min(1.0, max(0.0, classification.pertinence))
    page.marques_detectees = marques
    return page


def _classifier_lot(
    lot: list[PageWeb],
    produit: FicheProduit,
    marche: ParametresMarche,
) -> bool:
    """Classifie un lot de pages, avec une nouvelle tentative en cas d'échec.

    Args:
        lot: Pages du lot, modifiées en place lorsque la classification aboutit.
        produit: Fiche produit, contexte du scoring de pertinence.
        marche: Marché étudié, contexte du jugement de portée régionale.

    Returns:
        Vrai si le lot a été classifié, faux s'il reste à étiqueter par défaut.
    """
    axe1, axe2 = AXES_ANALYSE
    entree = {
        "nom": produit.nom,
        "description": produit.description,
        "categorie": produit.categorie,
        "geo": marche.geo,
        "langue": marche.langue,
        "axe1": axe1,
        "axe2": axe2,
        "libelle_axe1": LIBELLES_AXES[axe1],
        "libelle_axe2": LIBELLES_AXES[axe2],
        "lot": _formater_lot(lot),
    }

    for tentative in range(1, _NB_TENTATIVES_CLASSIFICATION + 1):
        try:
            chaine = _PROMPT_CLASSIFICATION | _modele().with_structured_output(
                LotClassification
            )
            resultat: LotClassification = chaine.invoke(entree)
        except Exception as exception:  # noqa: BLE001 — un échec ne vide pas le corpus
            _LOG.warning(
                "Classification du lot en échec (tentative %s/%s) : %s",
                tentative,
                _NB_TENTATIVES_CLASSIFICATION,
                exception,
            )
            continue

        etiquetees = 0
        for classification in resultat.classifications:
            if 0 <= classification.index < len(lot):
                _appliquer_classification(lot[classification.index], classification)
                etiquetees += 1
            else:
                _LOG.warning("Index hors lot ignoré : %s", classification.index)

        if etiquetees:
            if etiquetees < len(lot):
                _LOG.warning(
                    "Lot partiellement étiqueté : %s page(s) sur %s.",
                    etiquetees,
                    len(lot),
                )
            return True

        _LOG.warning(
            "Classification vide renvoyée (tentative %s/%s).",
            tentative,
            _NB_TENTATIVES_CLASSIFICATION,
        )

    return False


def classifier_pages(
    pages: list[PageWeb],
    produit: FicheProduit,
    marche: ParametresMarche,
) -> tuple[list[PageWeb], int]:
    """Étiquette les pages par lots, en dégradant gracieusement les échecs.

    Args:
        pages: Pages ayant passé les filtres déterministes.
        produit: Fiche produit étudiée.
        marche: Région d'étude.

    Returns:
        Un couple `(pages, nb_non_classifiees)`. Les pages sont retournées dans
        l'ordre reçu ; celles dont le lot a échoué conservent `type_source`,
        `portee_regionale` et `pertinence` à `None`, avec `axes_servis`
        retombant sur l'axe de leur requête d'origine.
    """
    if not pages:
        return [], 0

    for debut in range(0, len(pages), TAILLE_LOT_CLASSIFICATION):
        lot = pages[debut : debut + TAILLE_LOT_CLASSIFICATION]
        _LOG.info(
            "Classification du lot %s–%s (%s page(s)).",
            debut,
            debut + len(lot) - 1,
            len(lot),
        )
        _classifier_lot(lot, produit, marche)

    non_classifiees = 0
    for page in pages:
        if page.pertinence is None:
            page.axes_servis = _axes_de_repli(page)
            non_classifiees += 1

    if non_classifiees:
        _LOG.warning(
            "%s page(s) sur %s conservée(s) sans classification.",
            non_classifiees,
            len(pages),
        )
    return pages, non_classifiees
