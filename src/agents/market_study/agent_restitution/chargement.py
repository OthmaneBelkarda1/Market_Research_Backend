"""Lecture des fichiers d'entrée, validation tolérante, cohérence et inventaire
des blocs disponibles.

Règle centrale : **aucune exception n'est propagée pour une source**. Seules
deux situations sont bloquantes — l'absence de sortie F5 exploitable (sans
verdict ni dossier, il n'y a pas de rapport à écrire) et l'incohérence de
produit entre deux fichiers.

L'**inventaire des blocs** décide, pour chaque section du gabarit, si elle sera
construite depuis son entrée native, depuis l'écho de synthèse (mode dégradé,
mention obligatoire dans le rapport — exigence F7.3), ou remplacée par un encart
standard.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import (
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_PLC,
    ENTREE_RECOMMANDATIONS,
    GABARIT_RAPPORT,
    SECTION_PLC,
    logger,
)
from schemas import (
    AlerteCoherence,
    EntreeConcurrence,
    EntreeInsights,
    EntreePLC,
    EntreeRecommandations,
    EntreesChargees,
    ErreurCoherenceProduit,
    SourceUtilisee,
)

ENCODAGES_TESTES: tuple[str, ...] = ("utf-8-sig", "utf-16", "utf-8", "cp1252")
"""Encodages essayés dans l'ordre : une redirection PowerShell produit de
l'UTF-16 avec BOM, jamais de l'UTF-8."""


def _normaliser_nom(valeur: str) -> str:
    """Normalise un nom de produit pour la comparaison inter-fichiers.

    Args:
        valeur: Nom brut.

    Returns:
        Le nom sans accents, en minuscules, espaces normalisés.
    """
    decompose = unicodedata.normalize("NFKD", valeur)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(sans_accent.lower().split())


def lire_json(chemin: Path) -> Any:
    """Lit un fichier JSON quel que soit son encodage.

    Args:
        chemin: Chemin du fichier.

    Returns:
        L'objet Python décodé.

    Raises:
        ValueError: Si aucun encodage testé ne permet de décoder le contenu.
        OSError: Si le fichier est illisible.
    """
    brut = chemin.read_bytes()
    derniere: Exception | None = None
    for encodage in ENCODAGES_TESTES:
        try:
            return json.loads(brut.decode(encodage))
        except (UnicodeDecodeError, UnicodeError, json.JSONDecodeError) as erreur:
            derniere = erreur
    raise ValueError(
        f"contenu non décodable en JSON (encodages testés : "
        f"{', '.join(ENCODAGES_TESTES)}) — {derniere}"
    )


def _charger_source(
    chemin: str | None, modele: type, nom_entree: str
) -> tuple[Any | None, dict | None, SourceUtilisee]:
    """Charge et valide un fichier d'entrée, sans jamais lever.

    Le dictionnaire brut est conservé : c'est lui, et non le modèle filtré, qui
    alimente la liste blanche numérique. Un nombre présent dans les entrées mais
    non consommé par le schéma reste ainsi citable.

    Args:
        chemin: Chemin du fichier, ou `None`.
        modele: Classe Pydantic du schéma de consommation.
        nom_entree: Nom court de l'entrée.

    Returns:
        Le triplet `(entree_ou_None, brut_ou_None, compte_rendu)`.
    """
    compte_rendu = SourceUtilisee(source=nom_entree, fichier=chemin)
    if not chemin:
        return None, None, compte_rendu

    fichier = Path(chemin)
    if not fichier.is_file():
        compte_rendu.avertissements.append("fichier introuvable")
        logger.warning("[%s] fichier introuvable : %s", nom_entree, chemin)
        return None, None, compte_rendu

    try:
        brut = lire_json(fichier)
    except (OSError, ValueError) as erreur:
        compte_rendu.avertissements.append(f"lecture impossible : {erreur}")
        logger.warning("[%s] lecture impossible : %s", nom_entree, erreur)
        return None, None, compte_rendu

    if not isinstance(brut, dict):
        compte_rendu.avertissements.append("racine JSON non objet")
        return None, None, compte_rendu

    try:
        entree = modele.model_validate(brut)
    except ValidationError as erreur:
        resume = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in erreur.errors()[:5]
        )
        compte_rendu.avertissements.append(f"structure non conforme : {resume}")
        logger.warning("[%s] structure non conforme : %s", nom_entree, resume)
        return None, None, compte_rendu

    compte_rendu.donnees_disponibles = True
    compte_rendu.nb_items_charges = _compter(entree)
    compte_rendu.nb_items_exploites = compte_rendu.nb_items_charges
    if not getattr(entree, "donnees_suffisantes", True):
        compte_rendu.avertissements.append(
            "l'agent amont déclare `donnees_suffisantes=false`"
        )
    confiance = getattr(entree, "confiance_globale", None)
    if confiance is not None and confiance.niveau == "faible":
        compte_rendu.avertissements.append("confiance amont déclarée faible")
    return entree, brut, compte_rendu


def _compter(entree: Any) -> int:
    """Compte les éléments restituables d'une entrée.

    Args:
        entree: Entrée validée.

    Returns:
        Un décompte représentatif du contenu utile au rapport.
    """
    if isinstance(entree, EntreeRecommandations):
        return (
            len(entree.recommandations_produit)
            + len(entree.recommandations_marketing)
            + len(entree.opportunites)
            + len(entree.risques)
        )
    if isinstance(entree, EntreeInsights):
        return len(entree.pain_points) + len(entree.besoins) + len(entree.attentes)
    if isinstance(entree, EntreeConcurrence):
        return len(entree.tableau_comparatif) + len(entree.benchmark_prix)
    if isinstance(entree, EntreePLC):
        return len(entree.recommandations_phase) + len(entree.signaux)
    return 0


def _controler_coherence(entrees: list[tuple[str, Any]]) -> list[AlerteCoherence]:
    """Compare les en-têtes produit/marché des fichiers chargés.

    Args:
        entrees: Couples `(nom_entree, entree)` dans l'ordre de priorité.

    Returns:
        Les alertes non bloquantes.

    Raises:
        ErreurCoherenceProduit: Si deux fichiers portent des produits différents.
    """
    alertes: list[AlerteCoherence] = []
    if not entrees:
        return alertes
    source_ref, ref = entrees[0]
    nom_ref = _normaliser_nom(ref.produit.nom)

    for source, entree in entrees[1:]:
        if _normaliser_nom(entree.produit.nom) != nom_ref:
            raise ErreurCoherenceProduit(
                f"produits différents entre les fichiers d'entrée : "
                f"[{source_ref}] « {ref.produit.nom} » vs [{source}] "
                f"« {entree.produit.nom} ». Mélanger deux études est interdit."
            )
        if entree.marche.geo.strip().upper() != ref.marche.geo.strip().upper():
            alertes.append(
                AlerteCoherence(
                    type="marche_divergent",
                    detail=(
                        f"marché différent : [{source}] porte sur "
                        f"« {entree.marche.geo} », [{source_ref}] sur "
                        f"« {ref.marche.geo} ». Le rapport croiserait deux marchés : "
                        f"sa portée régionale est caduque."
                    ),
                )
            )
        if entree.produit.description.strip() != ref.produit.description.strip():
            alertes.append(
                AlerteCoherence(
                    type="produit_divergent",
                    detail=(
                        f"la description produit de [{source}] diffère de celle de "
                        f"[{source_ref}], qui fait foi."
                    ),
                )
            )
    return alertes


def inventorier_blocs(entrees: EntreesChargees) -> tuple[list[str], list[str]]:
    """Inventorie les sections dégradées et les sections absentes.

    Une section est **dégradée** lorsque son entrée native manque mais que
    l'écho du dossier de synthèse permet de la construire ; elle est **absente**
    lorsque rien ne permet de la construire — un encart standard la remplace
    alors, jamais un vide silencieux.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le couple `(sections_degradees, sections_absentes)`.
    """
    degradees: list[str] = []
    absentes: list[str] = []
    dossier = (
        entrees.recommandations.dossier_synthese if entrees.recommandations else None
    )

    for section in GABARIT_RAPPORT:
        requises = section["entrees_requises"]
        manquantes = [nom for nom in requises if not entrees.presente(nom)]
        if not manquantes:
            continue
        if section["id"] == SECTION_PLC:
            absentes.append(section["id"])
            continue
        echo_disponible = dossier is not None and (
            (ENTREE_INSIGHTS in manquantes and dossier.consommateur is not None)
            or (ENTREE_CONCURRENCE in manquantes and dossier.concurrence is not None)
        )
        if echo_disponible:
            degradees.append(section["id"])
        else:
            absentes.append(section["id"])
    logger.debug("blocs : %d dégradé(s), %d absent(s)", len(degradees), len(absentes))
    return degradees, absentes


def charger_entrees(
    chemin_recommandations: str,
    chemin_insights: str | None,
    chemin_concurrence: str | None,
    chemin_plc: str | None,
) -> tuple[EntreesChargees, list[SourceUtilisee], list[AlerteCoherence], list[dict]]:
    """Charge, valide et confronte les quatre fichiers d'entrée.

    Args:
        chemin_recommandations: Sortie de F5 — requise.
        chemin_insights: Sortie de F3, ou `None`.
        chemin_concurrence: Sortie de F4, ou `None`.
        chemin_plc: Sortie de F6, ou `None`.

    Returns:
        Le quadruplet `(entrees, sources, alertes, bruts)` — `bruts` étant la
        liste des dictionnaires JSON d'origine, matière première de la liste
        blanche numérique.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
    """
    recommandations, brut_reco, cr_reco = _charger_source(
        chemin_recommandations, EntreeRecommandations, ENTREE_RECOMMANDATIONS
    )
    insights, brut_insights, cr_insights = _charger_source(
        chemin_insights, EntreeInsights, ENTREE_INSIGHTS
    )
    concurrence, brut_concurrence, cr_concurrence = _charger_source(
        chemin_concurrence, EntreeConcurrence, ENTREE_CONCURRENCE
    )
    plc, brut_plc, cr_plc = _charger_source(chemin_plc, EntreePLC, ENTREE_PLC)

    sources = [cr_reco, cr_insights, cr_concurrence, cr_plc]
    presentes: list[tuple[str, Any]] = [
        (nom, entree)
        for nom, entree in (
            (ENTREE_RECOMMANDATIONS, recommandations),
            (ENTREE_INSIGHTS, insights),
            (ENTREE_CONCURRENCE, concurrence),
            (ENTREE_PLC, plc),
        )
        if entree is not None
    ]
    alertes = _controler_coherence(presentes)

    limites_amont = [
        f"[{nom}] {limite}" for nom, entree in presentes for limite in entree.limites
    ]
    hypotheses_amont = list(recommandations.hypotheses) if recommandations else []

    entrees = EntreesChargees(
        recommandations=recommandations,
        insights=insights,
        concurrence=concurrence,
        plc=plc,
        produit=presentes[0][1].produit if presentes else None,
        marche=presentes[0][1].marche if presentes else None,
        limites_amont=limites_amont,
        hypotheses_amont=hypotheses_amont,
        blocs_disponibles={nom: True for nom, _ in presentes},
    )
    bruts = [b for b in (brut_reco, brut_insights, brut_concurrence, brut_plc) if b]
    return entrees, sources, alertes, bruts
