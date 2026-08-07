"""Filtres déterministes, classification LLM et seuil de pertinence.

Deux régimes bien séparés :

1. `filtrer_deterministe` — dédoublonnage et application des critères du plan
   (prix, note, nombre d'avis). Ces critères sont déjà poussés dans l'URL via la
   facette `rh=p_36` d'Amazon, mais celle-ci est APPROXIMATIVE et disparaît sur
   une relance sans filtres : ils sont donc re-vérifiés ici, en Python.
2. `classifier_produits` — étiquetage LLM par lots de la correspondance à la
   fiche produit, puis `appliquer_seuil_pertinence`. Une recherche Amazon
   remonte massivement des accessoires et des produits voisins ; sans cette
   étape, le corpus mélange le produit et sa coque de protection.

Un échec de classification ne fait jamais échouer la collecte : les produits
concernés sont conservés tels quels, avec `pertinence=None`, et ne sont pas
confrontés au seuil.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    CORRESPONDANCE_ACCESSOIRE,
    CORRESPONDANCE_EQUIVALENT,
    CORRESPONDANCE_HORS_SUJET,
    CORRESPONDANCE_VARIANTE,
    LONGUEUR_TITRE_CLASSIFICATION,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    SEUIL_PERTINENCE,
    TAILLE_LOT_CLASSIFICATION,
    TEMPERATURE_LLM,
    TYPES_CORRESPONDANCE,
    obtenir_logger,
)
from schemas import FicheProduit, LotClassification, ProduitAmazon, RecherchePlanifiee

_LOG = obtenir_logger(__name__)

PERTINENCE_MIN: float = 0.0
PERTINENCE_MAX: float = 1.0


class CompteursFiltrage:
    """Décomptes d'un passage de filtres déterministes."""

    def __init__(self) -> None:
        """Initialise tous les compteurs à zéro."""
        self.doublons = 0
        self.hors_criteres = 0


# --------------------------------------------------------------------------- #
# Filtres déterministes
# --------------------------------------------------------------------------- #


def _cle_dedoublonnage(produit: ProduitAmazon) -> str:
    """Construit la clé d'unicité d'un produit.

    L'ASIN est l'identifiant canonique d'Amazon ; l'URL ne sert que de repli
    lorsque la fiche n'a pas été visitée et que l'ASIN manque.

    Args:
        produit: Produit normalisé.

    Returns:
        La clé d'unicité, à défaut le titre.
    """
    return produit.asin or produit.url or produit.titre


def _respecte_criteres(produit: ProduitAmazon, recherche: RecherchePlanifiee) -> bool:
    """Vérifie qu'un produit satisfait les critères de sa recherche.

    Un produit sans prix n'est retenu que si la recherche ne posait aucune borne
    de prix : à défaut, on ne peut pas affirmer qu'il entre dans le budget visé.
    Même logique pour la note et le nombre d'avis, dont l'absence ne vaut pas
    satisfaction du critère.

    Args:
        produit: Produit normalisé.
        recherche: Recherche à l'origine du produit.

    Returns:
        Vrai si tous les critères posés sont satisfaits.
    """
    if recherche.prix_min is not None or recherche.prix_max is not None:
        if produit.prix is None:
            return False
        if recherche.prix_min is not None and produit.prix < recherche.prix_min:
            return False
        if recherche.prix_max is not None and produit.prix > recherche.prix_max:
            return False

    if recherche.note_min is not None:
        if produit.note is None or produit.note < recherche.note_min:
            return False

    if recherche.nb_avis_min:
        if produit.nb_avis is None or produit.nb_avis < recherche.nb_avis_min:
            return False

    return True


def filtrer_deterministe(
    produits: list[ProduitAmazon],
    recherche: RecherchePlanifiee,
    cles_vues: set[str],
) -> tuple[list[ProduitAmazon], CompteursFiltrage]:
    """Dédoublonne et applique les critères du plan, sans aucun appel LLM.

    Args:
        produits: Produits normalisés d'une recherche.
        recherche: Recherche à l'origine de ces produits.
        cles_vues: Clés déjà retenues, **modifié en place** pour que les
            recherches successives ne se recouvrent pas.

    Returns:
        Un couple `(produits_retenus, compteurs)`.
    """
    compteurs = CompteursFiltrage()
    retenus: list[ProduitAmazon] = []

    for produit in produits:
        cle = _cle_dedoublonnage(produit)
        if cle in cles_vues:
            compteurs.doublons += 1
            continue
        if not _respecte_criteres(produit, recherche):
            compteurs.hors_criteres += 1
            continue
        cles_vues.add(cle)
        retenus.append(produit)

    if compteurs.doublons or compteurs.hors_criteres:
        _LOG.info(
            "Filtres déterministes sur « %s » : %s doublon(s), %s hors critères, "
            "%s retenu(s).",
            recherche.mots_cles,
            compteurs.doublons,
            compteurs.hors_criteres,
            len(retenus),
        )
    return retenus, compteurs


# --------------------------------------------------------------------------- #
# Classification LLM
# --------------------------------------------------------------------------- #

_PROMPT_CLASSIFICATION = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu qualifies des fiches produits remontées par une recherche Amazon, "
            "pour une étude de marché portant sur un produit de référence.\n\n"
            "Pour CHAQUE fiche du lot, tu renvoies :\n"
            "- `index` : l'index exact de la fiche dans le lot soumis. N'en "
            "omets aucune, n'en invente aucune.\n"
            "- `correspondance` :\n"
            f"  • « {CORRESPONDANCE_EQUIVALENT} » — même catégorie et même "
            "usage que le produit de référence : c'est un concurrent direct.\n"
            f"  • « {CORRESPONDANCE_VARIANTE} » — même famille, mais une "
            "déclinaison notable (autre capacité, autre format, lot, pack).\n"
            f"  • « {CORRESPONDANCE_ACCESSOIRE} » — complément du produit et "
            "non substitut : housse, câble, support, pièce détachée.\n"
            f"  • « {CORRESPONDANCE_HORS_SUJET} » — autre catégorie.\n"
            "- `pertinence` : de 0 à 1, à quel point la fiche éclaire le marché "
            "du produit de référence. Un concurrent direct est proche de 1, un "
            "accessoire autour de 0,2, une fiche hors sujet à 0.\n\n"
            "Les titres Amazon sont saturés de mots-clés : juge sur le produit "
            "réellement vendu, pas sur la présence d'un terme. Une housse pour "
            "un produit N'EST PAS ce produit, même si son titre le nomme.",
        ),
        (
            "human",
            "Produit de référence\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}\n\n"
            "Fiches à qualifier :\n{fiches}",
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


def _decrire_lot(lot: list[ProduitAmazon]) -> str:
    """Met un lot de produits en forme pour le classifieur.

    Seuls le titre, la marque et le prix sont transmis : ce sont les seuls
    éléments qui distinguent un concurrent d'un accessoire, et le titre est déjà
    tronqué pour ne pas noyer le signal sous le remplissage SEO.

    Args:
        lot: Produits du lot, dans l'ordre.

    Returns:
        Le bloc de texte soumis au modèle.
    """
    lignes: list[str] = []
    for index, produit in enumerate(lot):
        titre = produit.titre[:LONGUEUR_TITRE_CLASSIFICATION]
        prix = f"{produit.prix} {produit.devise or ''}".strip() if produit.prix else "prix inconnu"
        marque = produit.marque or "marque inconnue"
        lignes.append(f"[{index}] {titre}\n     marque : {marque} | prix : {prix}")
    return "\n".join(lignes)


def _classifier_lot(lot: list[ProduitAmazon], produit_reference: FicheProduit) -> int:
    """Étiquette un lot de produits **en place**.

    Args:
        lot: Produits à qualifier.
        produit_reference: Fiche produit servant de référence.

    Returns:
        Le nombre de produits restés non étiquetés.
    """
    chaine = _PROMPT_CLASSIFICATION | _modele().with_structured_output(LotClassification)
    entree = {
        "nom": produit_reference.nom,
        "categorie": produit_reference.categorie,
        "description": produit_reference.description,
        "fiches": _decrire_lot(lot),
    }
    try:
        resultat: LotClassification = chaine.invoke(entree)
    except Exception as exception:  # noqa: BLE001 — le lot reste non classifié
        _LOG.warning("Lot de classification en échec : %s", exception)
        return len(lot)

    for classification in resultat.classifications:
        if not 0 <= classification.index < len(lot):
            _LOG.warning("Index de classification hors lot ignoré : %s", classification.index)
            continue
        correspondance = classification.correspondance.strip().casefold()
        if correspondance not in TYPES_CORRESPONDANCE:
            _LOG.warning("Correspondance hors nomenclature ignorée : %s", correspondance)
            continue
        produit = lot[classification.index]
        produit.correspondance = correspondance
        produit.pertinence = min(
            PERTINENCE_MAX, max(PERTINENCE_MIN, classification.pertinence)
        )

    return sum(1 for produit in lot if produit.pertinence is None)


def classifier_produits(
    produits: list[ProduitAmazon], produit_reference: FicheProduit
) -> tuple[list[ProduitAmazon], int]:
    """Qualifie les produits par lots, au regard de la fiche de référence.

    Args:
        produits: Produits à qualifier, modifiés en place.
        produit_reference: Fiche produit servant de référence.

    Returns:
        Un couple `(produits, nb_non_classifies)`.
    """
    if not produits:
        return produits, 0

    non_classifies = 0
    for debut in range(0, len(produits), TAILLE_LOT_CLASSIFICATION):
        lot = produits[debut : debut + TAILLE_LOT_CLASSIFICATION]
        non_classifies += _classifier_lot(lot, produit_reference)

    if non_classifies:
        _LOG.warning(
            "%s produit(s) sur %s non classifié(s) : conservés sans étiquetage.",
            non_classifies,
            len(produits),
        )
    return produits, non_classifies


def appliquer_seuil_pertinence(
    produits: list[ProduitAmazon],
) -> tuple[list[ProduitAmazon], int]:
    """Écarte les produits dont la pertinence est sous le seuil.

    Un produit non classifié — `pertinence` nulle — est CONSERVÉ : l'échec de la
    classification ne doit pas se traduire par une perte silencieuse de corpus.

    Args:
        produits: Produits qualifiés.

    Returns:
        Un couple `(produits_retenus, nb_ecartes)`.
    """
    retenus = [
        produit
        for produit in produits
        if produit.pertinence is None or produit.pertinence >= SEUIL_PERTINENCE
    ]
    ecartes = len(produits) - len(retenus)
    if ecartes:
        _LOG.info("Seuil de pertinence : %s produit(s) écarté(s).", ecartes)
    return retenus, ecartes
