"""Lecture des fichiers d'entrée, validation tolérante, cohérence et fraîcheur.

Règle centrale : **aucune exception n'est propagée pour une source**. Seule
l'incohérence de produit entre deux fichiers est bloquante.

Sur la fraîcheur : **aucun champ d'horodatage n'est garanti par les contrats
amont**. Constat fait sur les sorties réelles du dépôt — les agents d'analyse
F3 et F4 publient un `horodatage_utc` (enrichissement introduit avec eux), le
collecteur Tendances n'en publie aucun. En son absence, la fraîcheur est
déclarée non qualifiable ; rien n'est inventé et surtout rien n'est déduit de
la date de modification du fichier, qui ne dit rien de la date de collecte.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import (
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_TENDANCES,
    SEUIL_FRAICHEUR_JOURS,
    logger,
)
from schemas import (
    AlerteCoherence,
    EntreeConcurrence,
    EntreeInsights,
    EntreeTendances,
    EntreesChargees,
    ErreurCoherenceProduit,
    QualiteEntree,
    SourceUtilisee,
)

ENCODAGES_TESTES: tuple[str, ...] = ("utf-8-sig", "utf-16", "utf-8", "cp1252")
"""Encodages essayés dans l'ordre.

Le collecteur Tendances émet sur `stdout` : le fichier passé à `--tendances` est
une redirection, et sous PowerShell une redirection produit de l'UTF-16 avec BOM.
"""


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


def _lire_json(chemin: Path) -> Any:
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


def _age_en_jours(horodatage: str | None) -> int | None:
    """Calcule l'âge en jours d'un horodatage ISO 8601.

    Args:
        horodatage: Horodatage ISO 8601, éventuellement suffixé « Z ».

    Returns:
        L'âge en jours, ou `None` si l'horodatage est absent ou illisible.
    """
    if not horodatage:
        return None
    try:
        date = datetime.fromisoformat(horodatage.replace("Z", "+00:00"))
    except ValueError:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - date).days)


def _charger_source(
    chemin: str | None, modele: type, nom_entree: str
) -> tuple[Any | None, SourceUtilisee]:
    """Charge et valide un fichier d'entrée, sans jamais lever.

    Args:
        chemin: Chemin du fichier, ou `None`.
        modele: Classe Pydantic du schéma de consommation.
        nom_entree: Nom court de l'entrée.

    Returns:
        Le couple `(entree_ou_None, compte_rendu)`.
    """
    compte_rendu = SourceUtilisee(source=nom_entree, fichier=chemin)
    if not chemin:
        return None, compte_rendu

    fichier = Path(chemin)
    if not fichier.is_file():
        compte_rendu.avertissements.append("fichier introuvable")
        logger.warning("[%s] fichier introuvable : %s", nom_entree, chemin)
        return None, compte_rendu

    try:
        brut = _lire_json(fichier)
    except (OSError, ValueError) as erreur:
        compte_rendu.avertissements.append(f"lecture impossible : {erreur}")
        logger.warning("[%s] lecture impossible : %s", nom_entree, erreur)
        return None, compte_rendu

    if not isinstance(brut, dict):
        compte_rendu.avertissements.append("racine JSON non objet")
        return None, compte_rendu

    try:
        entree = modele.model_validate(brut)
    except ValidationError as erreur:
        resume = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in erreur.errors()[:5]
        )
        compte_rendu.avertissements.append(f"structure non conforme : {resume}")
        logger.warning("[%s] structure non conforme : %s", nom_entree, resume)
        return None, compte_rendu

    compte_rendu.donnees_disponibles = True
    compte_rendu.nb_items_charges = _compter(entree)
    return entree, compte_rendu


def _compter(entree: Any) -> int:
    """Compte les éléments analytiques d'une entrée.

    Args:
        entree: Entrée validée.

    Returns:
        Un décompte représentatif du contenu.
    """
    if isinstance(entree, EntreeInsights):
        return len(entree.pain_points) + len(entree.besoins)
    if isinstance(entree, EntreeConcurrence):
        return len(entree.concurrents) + len(entree.benchmark_prix)
    if isinstance(entree, EntreeTendances):
        return 1 if entree.indicateurs else 0
    return 0


def _qualite(nom: str, entree: Any, compte_rendu: SourceUtilisee) -> QualiteEntree:
    """Qualifie la présence, la fraîcheur et la confiance héritée d'une entrée.

    Args:
        nom: Nom de l'entrée.
        entree: Entrée validée, ou `None`.
        compte_rendu: Compte rendu de chargement.

    Returns:
        La qualité constatée.
    """
    qualite = QualiteEntree(entree=nom, presente=entree is not None)
    qualite.avertissements.extend(compte_rendu.avertissements)
    if entree is None:
        return qualite

    if isinstance(entree, EntreeTendances):
        qualite.donnees_suffisantes = bool(entree.donnees_disponibles)
        qualite.fraicheur_qualifiable = False
        qualite.avertissements.append(
            "le contrat du collecteur Tendances ne porte aucun horodatage : la "
            "fraîcheur de cette entrée ne peut pas être qualifiée"
        )
        if not entree.donnees_disponibles:
            qualite.avertissements.append(
                "le collecteur déclare `donnees_disponibles=false`"
            )
        return qualite

    qualite.donnees_suffisantes = bool(getattr(entree, "donnees_suffisantes", False))
    confiance = getattr(entree, "confiance_globale", None)
    qualite.confiance_heritee = confiance.niveau if confiance else None
    qualite.horodatage = getattr(entree, "horodatage_utc", None)
    qualite.age_jours = _age_en_jours(qualite.horodatage)
    qualite.fraicheur_qualifiable = qualite.age_jours is not None

    if qualite.age_jours is None:
        qualite.avertissements.append(
            "aucun horodatage exploitable : la fraîcheur ne peut pas être qualifiée"
        )
    elif qualite.age_jours > SEUIL_FRAICHEUR_JOURS:
        qualite.avertissements.append(
            f"analyse produite il y a {qualite.age_jours} jours, au-delà du seuil de "
            f"{SEUIL_FRAICHEUR_JOURS} jours"
        )
    if not qualite.donnees_suffisantes:
        qualite.avertissements.append(
            "l'agent amont déclare `donnees_suffisantes=false`"
        )
    if qualite.confiance_heritee == "faible":
        qualite.avertissements.append("confiance amont déclarée faible")
    return qualite


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
                        f"« {ref.marche.geo} ». Les recommandations croisent deux "
                        f"marchés : leur portée régionale est caduque."
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


def charger_entrees(
    chemin_insights: str | None,
    chemin_concurrence: str | None,
    chemin_tendances: str | None,
) -> tuple[
    EntreesChargees, list[SourceUtilisee], list[AlerteCoherence], list[QualiteEntree]
]:
    """Charge, valide et confronte les trois fichiers d'entrée.

    Args:
        chemin_insights: Sortie de F3, ou `None`.
        chemin_concurrence: Sortie de F4, ou `None`.
        chemin_tendances: Sortie du collecteur Tendances, ou `None`.

    Returns:
        Le quadruplet `(entrees, sources, alertes, qualites)`.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
    """
    insights, cr_insights = _charger_source(chemin_insights, EntreeInsights, ENTREE_INSIGHTS)
    concurrence, cr_concurrence = _charger_source(
        chemin_concurrence, EntreeConcurrence, ENTREE_CONCURRENCE
    )
    tendances, cr_tendances = _charger_source(
        chemin_tendances, EntreeTendances, ENTREE_TENDANCES
    )

    sources = [cr_insights, cr_concurrence, cr_tendances]
    qualites = [
        _qualite(ENTREE_INSIGHTS, insights, cr_insights),
        _qualite(ENTREE_CONCURRENCE, concurrence, cr_concurrence),
        _qualite(ENTREE_TENDANCES, tendances, cr_tendances),
    ]

    presentes: list[tuple[str, Any]] = [
        (nom, entree)
        for nom, entree in (
            (ENTREE_INSIGHTS, insights),
            (ENTREE_CONCURRENCE, concurrence),
            (ENTREE_TENDANCES, tendances),
        )
        if entree is not None
    ]
    alertes = _controler_coherence(presentes)

    limites_amont: list[str] = [
        f"[{nom}] {limite}" for nom, entree in presentes for limite in entree.limites
    ]

    entrees = EntreesChargees(
        insights=insights,
        concurrence=concurrence,
        tendances=tendances,
        produit=presentes[0][1].produit if presentes else None,
        marche=presentes[0][1].marche if presentes else None,
        limites_amont=limites_amont,
    )
    return entrees, sources, alertes, qualites
