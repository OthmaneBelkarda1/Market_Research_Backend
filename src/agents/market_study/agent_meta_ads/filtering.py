"""Filtres déterministes, classification LLM et seuil de pertinence.

Deux régimes bien séparés :

1. `filtrer_deterministe` — dédoublonnage par identifiant **puis par créatif**,
   contrôle du statut de diffusion demandé, et rejet des enregistrements sans
   aucun contenu exploitable ;
2. `classifier_annonces` — étiquetage LLM par lots de la correspondance à la
   fiche produit, puis `appliquer_seuil_pertinence`. Une recherche par mots-clés
   sur le texte des annonces remonte massivement des créatifs voisins : sans
   cette étape, le corpus mélange les concurrents directs et les revendeurs
   d'accessoires.

Le dédoublonnage par créatif est le filtre le plus structurant du module : un
annonceur diffuse le même visuel et le même texte sous des dizaines
d'identifiants d'annonce distincts — un par audience, un par placement. Sans ce
rapprochement, un seul concurrent occupe tout le corpus et fausse chacune des
répartitions.

Un échec de classification ne fait jamais échouer la collecte : les annonces
concernées sont conservées telles quelles, avec `pertinence=None`, et ne sont
pas confrontées au seuil.
"""

from __future__ import annotations

import re
import unicodedata

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

from config import (
    ANTHROPIC_API_KEY,
    CORRESPONDANCE_ACCESSOIRE,
    CORRESPONDANCE_CATEGORIE,
    CORRESPONDANCE_CONCURRENT,
    CORRESPONDANCE_HORS_SUJET,
    LONGUEUR_CLE_CREATIF,
    LONGUEUR_TEXTE_CLASSIFICATION,
    MAX_TOKENS_LLM,
    MODELE_CLAUDE,
    SEUIL_PERTINENCE,
    STATUT_ACTIVES,
    STATUT_INACTIVES,
    TAILLE_LOT_CLASSIFICATION,
    TEMPERATURE_LLM,
    TYPES_CORRESPONDANCE,
    obtenir_logger,
)
from schemas import Annonce, FicheProduit, LotClassification, RecherchePlanifiee

_LOG = obtenir_logger(__name__)

PERTINENCE_MIN: float = 0.0
PERTINENCE_MAX: float = 1.0

_MOTIF_NON_ALPHANUM = re.compile(r"[^0-9a-z]+")


class CompteursFiltrage:
    """Décomptes d'un passage de filtres déterministes."""

    def __init__(self) -> None:
        """Initialise tous les compteurs à zéro."""
        self.doublons = 0
        self.doublons_creatif = 0
        self.hors_criteres = 0


# --------------------------------------------------------------------------- #
# Filtres déterministes
# --------------------------------------------------------------------------- #


def _empreinte(texte: str | None) -> str:
    """Réduit un texte à une empreinte comparable.

    Accents, casse, ponctuation, émojis et espaces sont écrasés : deux créatifs
    ne différant que par un émoji ou une variante d'espacement doivent se
    rapprocher.

    Args:
        texte: Texte à réduire, éventuellement nul.

    Returns:
        L'empreinte, chaîne vide si le texte est nul.
    """
    if not texte:
        return ""
    decompose = unicodedata.normalize("NFKD", texte.casefold())
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return _MOTIF_NON_ALPHANUM.sub("", sans_accents)


def _cle_annonce(annonce: Annonce) -> str:
    """Construit la clé d'unicité d'une annonce.

    L'identifiant d'archive est canonique ; le reste n'est qu'un repli pour les
    enregistrements qui en sont dépourvus.

    Args:
        annonce: Annonce normalisée.

    Returns:
        La clé d'unicité de l'annonce.
    """
    if annonce.id_annonce:
        return annonce.id_annonce
    return "|".join(
        _empreinte(valeur) for valeur in (annonce.annonceur, annonce.titre, annonce.texte)
    )


def _cle_creatif(annonce: Annonce) -> str:
    """Construit la clé de rapprochement des reprises d'un même créatif.

    Meta groupe lui-même les déclinaisons d'un créatif sous une `collationId` :
    quand elle est présente, elle fait foi — c'est le regroupement de la source,
    pas une heuristique. À défaut, la clé associe l'annonceur au début de son
    message : c'est le couple qui identifie une campagne, là où l'identifiant
    d'annonce n'identifie qu'une déclinaison d'audience. Le texte est tronqué
    pour qu'une variation de mention légale en fin de message ne fasse pas
    échouer le rapprochement.

    Args:
        annonce: Annonce normalisée.

    Returns:
        La clé de créatif.
    """
    if annonce.id_collation:
        return f"collation:{annonce.id_collation}"

    annonceur = _empreinte(annonce.annonceur or annonce.id_annonceur)
    corps = _empreinte(annonce.texte or annonce.description_lien)[:LONGUEUR_CLE_CREATIF]
    if corps:
        return f"{annonceur}|{corps}"
    # Sans texte, le créatif est identifié par ce qu'il affiche et par sa
    # destination : deux annonces sans texte pointant la même page produit avec
    # le même titre sont la même campagne.
    return f"{annonceur}|{_empreinte(annonce.titre)}|{_empreinte(annonce.lien)}"


def _a_du_contenu(annonce: Annonce) -> bool:
    """Vérifie qu'une annonce porte quelque chose d'analysable.

    Args:
        annonce: Annonce normalisée.

    Returns:
        Vrai si au moins un élément de créatif est exploitable.
    """
    return any(
        (
            annonce.titre,
            annonce.texte,
            annonce.description_lien,
            annonce.cta,
            annonce.lien,
            annonce.image,
            annonce.video,
        )
    )


def _respecte_statut(annonce: Annonce, recherche: RecherchePlanifiee) -> bool:
    """Vérifie que l'annonce correspond au statut de diffusion demandé.

    Le filtre est déjà posé dans l'URL : ce contrôle n'est qu'un garde-fou pour
    les cas où la bibliothèque sert autre chose que ce qui a été demandé. Une
    annonce dont le statut est INCONNU est conservée — l'absence de champ ne
    vaut pas contradiction avec la recherche.

    Args:
        annonce: Annonce normalisée.
        recherche: Recherche à l'origine de l'annonce.

    Returns:
        Vrai si le statut demandé est respecté, ou indéterminable.
    """
    if annonce.active is None:
        return True
    if recherche.statut_diffusion == STATUT_ACTIVES:
        return annonce.active
    if recherche.statut_diffusion == STATUT_INACTIVES:
        return not annonce.active
    return True


def filtrer_deterministe(
    annonces: list[Annonce],
    recherche: RecherchePlanifiee,
    cles_vues: set[str],
    creatifs_vus: set[str],
) -> tuple[list[Annonce], CompteursFiltrage]:
    """Dédoublonne et applique les critères de la recherche, sans appel LLM.

    Args:
        annonces: Annonces normalisées d'une recherche.
        recherche: Recherche à l'origine de ces annonces.
        cles_vues: Identifiants déjà retenus, **modifié en place**.
        creatifs_vus: Créatifs déjà retenus, **modifié en place**, pour que les
            recherches successives ne se recouvrent pas.

    Returns:
        Un couple `(annonces_retenues, compteurs)`.
    """
    compteurs = CompteursFiltrage()
    retenues: list[Annonce] = []

    for annonce in annonces:
        cle = _cle_annonce(annonce)
        if cle in cles_vues:
            compteurs.doublons += 1
            continue
        if not _a_du_contenu(annonce) or not _respecte_statut(annonce, recherche):
            compteurs.hors_criteres += 1
            continue

        creatif = _cle_creatif(annonce)
        if creatif in creatifs_vus:
            compteurs.doublons_creatif += 1
            cles_vues.add(cle)
            continue

        cles_vues.add(cle)
        creatifs_vus.add(creatif)
        retenues.append(annonce)

    if compteurs.doublons or compteurs.doublons_creatif or compteurs.hors_criteres:
        _LOG.info(
            "Filtres déterministes sur « %s » : %s doublon(s), %s reprise(s) de "
            "créatif, %s hors critères, %s retenue(s).",
            recherche.mots_cles,
            compteurs.doublons,
            compteurs.doublons_creatif,
            compteurs.hors_criteres,
            len(retenues),
        )
    return retenues, compteurs


# --------------------------------------------------------------------------- #
# Classification LLM
# --------------------------------------------------------------------------- #

_PROMPT_CLASSIFICATION = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Tu qualifies des ANNONCES PUBLICITAIRES remontées par une recherche "
            "dans la bibliothèque publicitaire de Meta, pour une étude de marché "
            "portant sur un produit de référence.\n\n"
            "Pour CHAQUE annonce du lot, tu renvoies :\n"
            "- `index` : l'index exact de l'annonce dans le lot soumis. N'en "
            "omets aucune, n'en invente aucune.\n"
            "- `correspondance` :\n"
            f"  • « {CORRESPONDANCE_CONCURRENT} » — l'annonce vend un produit de "
            "même catégorie et de même usage que le produit de référence : c'est "
            "un concurrent direct.\n"
            f"  • « {CORRESPONDANCE_CATEGORIE} » — l'annonce relève de la même "
            "famille de besoin, mais avec un produit ou un positionnement "
            "sensiblement différent.\n"
            f"  • « {CORRESPONDANCE_ACCESSOIRE} » — l'annonce vend un complément "
            "et non un substitut : housse, câble, support, pièce détachée.\n"
            f"  • « {CORRESPONDANCE_HORS_SUJET} » — autre catégorie, ou annonce "
            "qui ne vend pas de produit (recrutement, notoriété de marque, "
            "événement).\n"
            "- `pertinence` : de 0 à 1, à quel point l'annonce éclaire le marché "
            "du produit de référence. Un concurrent direct est proche de 1, un "
            "accessoire autour de 0,2, une annonce hors sujet à 0.\n\n"
            "Un texte d'annonce est du discours commercial : il exagère, il "
            "emploie des superlatifs et il cite parfois des marques qu'il ne vend "
            "pas. Juge sur le PRODUIT RÉELLEMENT PROPOSÉ, pas sur la présence "
            "d'un terme. Une annonce de coque pour un produit N'EST PAS une "
            "annonce de ce produit, même si elle le nomme.",
        ),
        (
            "human",
            "Produit de référence\n"
            "Titre : {nom}\n"
            "Catégorie : {categorie}\n"
            "Description : {description}\n\n"
            "Annonces à qualifier :\n{annonces}",
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


def _decrire_lot(lot: list[Annonce]) -> str:
    """Met un lot d'annonces en forme pour le classifieur.

    Sont transmis l'annonceur, le titre, le texte, l'appel à l'action et la
    destination du clic : ce sont les seuls éléments qui disent ce qui est
    réellement vendu.

    Le corps du créatif et la description du lien sont **concaténés** : sur les
    annonces observées, le corps se réduit souvent à un titre de quelques mots
    tandis que la description porte tout l'argumentaire. Ne transmettre que le
    corps priverait le classifieur de l'essentiel. L'ensemble est tronqué pour
    ne pas noyer le signal sous les mentions légales et les hashtags.

    Args:
        lot: Annonces du lot, dans l'ordre.

    Returns:
        Le bloc de texte soumis au modèle.
    """
    lignes: list[str] = []
    for index, annonce in enumerate(lot):
        texte = " ".join(
            partie.replace("\n", " ").strip()
            for partie in (annonce.texte, annonce.description_lien)
            if partie
        ).strip()
        lignes.append(
            f"[{index}] annonceur : {annonce.annonceur or 'inconnu'}\n"
            f"     titre : {annonce.titre or '—'}\n"
            f"     texte : {texte[:LONGUEUR_TEXTE_CLASSIFICATION] or '—'}\n"
            f"     bouton : {annonce.cta or '—'} | destination : {annonce.lien or '—'}"
        )
    return "\n".join(lignes)


def _classifier_lot(lot: list[Annonce], produit_reference: FicheProduit) -> int:
    """Étiquette un lot d'annonces **en place**.

    Args:
        lot: Annonces à qualifier.
        produit_reference: Fiche produit servant de référence.

    Returns:
        Le nombre d'annonces restées non étiquetées.
    """
    chaine = _PROMPT_CLASSIFICATION | _modele().with_structured_output(LotClassification)
    entree = {
        "nom": produit_reference.nom,
        "categorie": produit_reference.categorie,
        "description": produit_reference.description,
        "annonces": _decrire_lot(lot),
    }
    try:
        resultat: LotClassification = chaine.invoke(entree)
    except Exception as exception:  # noqa: BLE001 — le lot reste non classifié
        _LOG.warning("Lot de classification en échec : %s", exception)
        return len(lot)

    for classification in resultat.classifications:
        if not 0 <= classification.index < len(lot):
            _LOG.warning(
                "Index de classification hors lot ignoré : %s", classification.index
            )
            continue
        correspondance = classification.correspondance.strip().casefold()
        if correspondance not in TYPES_CORRESPONDANCE:
            _LOG.warning("Correspondance hors nomenclature ignorée : %s", correspondance)
            continue
        annonce = lot[classification.index]
        annonce.correspondance = correspondance
        annonce.pertinence = min(
            PERTINENCE_MAX, max(PERTINENCE_MIN, classification.pertinence)
        )

    return sum(1 for annonce in lot if annonce.pertinence is None)


def classifier_annonces(
    annonces: list[Annonce], produit_reference: FicheProduit
) -> tuple[list[Annonce], int]:
    """Qualifie les annonces par lots, au regard de la fiche de référence.

    Args:
        annonces: Annonces à qualifier, modifiées en place.
        produit_reference: Fiche produit servant de référence.

    Returns:
        Un couple `(annonces, nb_non_classifiees)`.
    """
    if not annonces:
        return annonces, 0

    non_classifiees = 0
    for debut in range(0, len(annonces), TAILLE_LOT_CLASSIFICATION):
        lot = annonces[debut : debut + TAILLE_LOT_CLASSIFICATION]
        non_classifiees += _classifier_lot(lot, produit_reference)

    if non_classifiees:
        _LOG.warning(
            "%s annonce(s) sur %s non classifiée(s) : conservées sans étiquetage.",
            non_classifiees,
            len(annonces),
        )
    return annonces, non_classifiees


def appliquer_seuil_pertinence(annonces: list[Annonce]) -> tuple[list[Annonce], int]:
    """Écarte les annonces dont la pertinence est sous le seuil.

    Une annonce non classifiée — `pertinence` nulle — est CONSERVÉE : l'échec de
    la classification ne doit pas se traduire par une perte silencieuse de
    corpus.

    Args:
        annonces: Annonces qualifiées.

    Returns:
        Un couple `(annonces_retenues, nb_ecartees)`.
    """
    retenues = [
        annonce
        for annonce in annonces
        if annonce.pertinence is None or annonce.pertinence >= SEUIL_PERTINENCE
    ]
    ecartees = len(annonces) - len(retenues)
    if ecartees:
        _LOG.info("Seuil de pertinence : %s annonce(s) écartée(s).", ecartees)
    return retenues, ecartees
