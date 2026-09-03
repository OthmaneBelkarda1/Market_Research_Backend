"""Extraction par lots sur le modèle d'extraction : attributs et claims.

Deux chaînes LCEL, toutes deux non bloquantes : l'échec d'un lot après reprise
laisse les éléments concernés non enrichis (listes vides, champs `None`) et
inscrit un statut. L'analyse continue toujours.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    MAX_CARACTERES_TEXTE_ANNONCE,
    MAX_TOKENS_EXTRACTION,
    MODELE_EXTRACTION,
    TAILLE_LOT_ATTRIBUTS,
    TAILLE_LOT_CLAIMS,
    construire_modele,
    invoquer_structure,
    logger,
)
from schemas import (
    AnnonceConcurrente,
    ClaimsAnnonce,
    LotAttributs,
    LotClaims,
    OffreConcurrente,
    StatutAnalyse,
)

PHASE_ATTRIBUTS: str = "extraction_attributs"
PHASE_CLAIMS: str = "extraction_claims"

_SYSTEME_ATTRIBUTS = (
    "Tu es analyste produit. On te donne des TITRES d'offres marchandes et tu en "
    "extrais les caractéristiques objectives qui y sont LISIBLES.\n\n"
    "Produit de référence de l'étude : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse} — normalise tes attributs dans cette langue.\n\n"
    "Consignes impératives :\n"
    "- 2 à 5 attributs par offre, chacun de 1 à 4 mots.\n"
    "- Uniquement ce que le titre affirme : format, technologie, matière, "
    "certification, capacité, quantité, compatibilité, usage revendiqué.\n"
    "- Ne déduis JAMAIS au-delà du titre. Un titre qui ne dit rien d'exploitable "
    "donne une liste vide — c'est une réponse correcte.\n"
    "- Normalise les formulations pour qu'elles soient comparables d'une offre à "
    "l'autre : « IP54 » et non « résistant aux éclaboussures IP54 ».\n"
    "- N'invente aucune marque et ne recopie pas le nom de marque comme attribut.\n"
    "- Recopie chaque identifiant d'offre à l'identique ; n'en invente aucun."
    "{erreur_precedente}"
)

_HUMAIN_ATTRIBUTS = "Lot d'offres (JSON : id_offre, titre) :\n\n{lot}"

_SYSTEME_CLAIMS = (
    "Tu es analyste publicitaire. On te donne le texte d'annonces concurrentes et "
    "tu en extrais l'argumentaire.\n\n"
    "Produit de référence de l'étude : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse} — rédige tes champs dans cette langue, "
    "même si l'annonce est rédigée dans une autre langue.\n\n"
    "Consignes impératives :\n"
    "- `promesse_principale` : le bénéfice mis en avant, en une phrase courte.\n"
    "- `angle` : « prix », « qualite », « innovation », « statut », « praticite », "
    "« urgence », « preuve_sociale » ou « autre ». Choisis l'angle DOMINANT.\n"
    "- `offre_commerciale` : remise, bundle, livraison offerte, garantie — "
    "uniquement si l'annonce l'énonce.\n"
    "- `cible_suggeree` : le public que l'annonce désigne, s'il est explicite.\n"
    "- Tout champ que le texte ne permet pas d'établir vaut null. Ne devine pas.\n"
    "- Recopie chaque identifiant d'annonce à l'identique.{erreur_precedente}"
)

_HUMAIN_CLAIMS = "Lot d'annonces (JSON : id_annonce, annonceur, cta, texte) :\n\n{lot}"


def _decouper(elements: list, taille: int) -> list[list]:
    """Découpe une liste en lots de taille fixe.

    Args:
        elements: Liste à découper.
        taille: Taille maximale d'un lot.

    Returns:
        La liste des lots.
    """
    return [elements[i : i + taille] for i in range(0, len(elements), taille)]


def extraire_attributs(
    offres: list[OffreConcurrente], produit_nom: str, langue_analyse: str
) -> list[StatutAnalyse]:
    """Enrichit les offres de leurs attributs, en place.

    Args:
        offres: Offres du référentiel, modifiées sur place.
        produit_nom: Nom du produit étudié.
        langue_analyse: Code langue des attributs normalisés.

    Returns:
        Les statuts de chaque lot.
    """
    if not offres:
        return []

    modele = construire_modele(MODELE_EXTRACTION, MAX_TOKENS_EXTRACTION)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_ATTRIBUTS), ("human", _HUMAIN_ATTRIBUTS)]
    )
    chaine = gabarit | modele.with_structured_output(LotAttributs)
    index = {o.id_offre: o for o in offres}
    statuts: list[StatutAnalyse] = []

    for numero, lot in enumerate(_decouper(offres, TAILLE_LOT_ATTRIBUTS), start=1):
        charge = json.dumps(
            [{"id_offre": o.id_offre, "titre": o.titre} for o in lot],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        resultat, tentatives, erreur = invoquer_structure(
            chaine,
            {
                "produit_nom": produit_nom,
                "langue_analyse": langue_analyse,
                "lot": charge,
            },
            f"{PHASE_ATTRIBUTS} lot {numero}",
        )
        if resultat is None:
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_ATTRIBUTS,
                    succes=False,
                    message_erreur=(
                        f"lot {numero} écarté : {erreur}. Les {len(lot)} offre(s) "
                        f"concernées restent sans attributs."
                    ),
                    nb_tentatives=tentatives,
                )
            )
            continue

        ids_lot = {o.id_offre for o in lot}
        enrichies = 0
        for element in resultat.offres:
            if element.id_offre in ids_lot:
                index[element.id_offre].attributs_extraits = [
                    a.strip() for a in element.attributs if a.strip()
                ]
                enrichies += 1
        statuts.append(
            StatutAnalyse(
                phase=PHASE_ATTRIBUTS,
                succes=True,
                message_erreur=(
                    f"lot {numero} : {len(ids_lot) - enrichies} offre(s) non enrichie(s)"
                    if enrichies < len(ids_lot)
                    else None
                ),
                nb_elements=enrichies,
                nb_tentatives=tentatives,
            )
        )

    logger.debug(
        "attributs : %d offre(s) enrichie(s) sur %d",
        sum(1 for o in offres if o.attributs_extraits),
        len(offres),
    )
    return statuts


def extraire_claims(
    annonces: list[AnnonceConcurrente], produit_nom: str, langue_analyse: str
) -> list[StatutAnalyse]:
    """Enrichit les annonces de leurs claims, en place.

    Args:
        annonces: Annonces du référentiel, modifiées sur place.
        produit_nom: Nom du produit étudié.
        langue_analyse: Code langue des champs produits.

    Returns:
        Les statuts de chaque lot.
    """
    if not annonces:
        return []

    modele = construire_modele(MODELE_EXTRACTION, MAX_TOKENS_EXTRACTION)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_CLAIMS), ("human", _HUMAIN_CLAIMS)]
    )
    chaine = gabarit | modele.with_structured_output(LotClaims)
    index = {a.id_annonce: a for a in annonces}
    statuts: list[StatutAnalyse] = []

    for numero, lot in enumerate(_decouper(annonces, TAILLE_LOT_CLAIMS), start=1):
        charge = json.dumps(
            [
                {
                    "id_annonce": a.id_annonce,
                    "annonceur": a.annonceur,
                    "cta": a.cta,
                    "texte": a.texte_complet[:MAX_CARACTERES_TEXTE_ANNONCE],
                }
                for a in lot
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        resultat, tentatives, erreur = invoquer_structure(
            chaine,
            {
                "produit_nom": produit_nom,
                "langue_analyse": langue_analyse,
                "lot": charge,
            },
            f"{PHASE_CLAIMS} lot {numero}",
        )
        if resultat is None:
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_CLAIMS,
                    succes=False,
                    message_erreur=(
                        f"lot {numero} écarté : {erreur}. Les {len(lot)} annonce(s) "
                        f"concernées restent sans claims."
                    ),
                    nb_tentatives=tentatives,
                )
            )
            continue

        ids_lot = {a.id_annonce for a in lot}
        enrichies = 0
        for element in resultat.annonces:
            if element.id_annonce in ids_lot:
                index[element.id_annonce].claims = ClaimsAnnonce(
                    promesse_principale=element.promesse_principale,
                    angle=element.angle,
                    offre_commerciale=element.offre_commerciale,
                    cible_suggeree=element.cible_suggeree,
                )
                enrichies += 1
        statuts.append(
            StatutAnalyse(
                phase=PHASE_CLAIMS,
                succes=True,
                message_erreur=(
                    f"lot {numero} : {len(ids_lot) - enrichies} annonce(s) non enrichie(s)"
                    if enrichies < len(ids_lot)
                    else None
                ),
                nb_elements=enrichies,
                nb_tentatives=tentatives,
            )
        )
    return statuts
