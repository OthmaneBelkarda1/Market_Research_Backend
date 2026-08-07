"""Lecture des fichiers d'entrée, validation tolérante, cohérence, fraîcheur et
**condition de déclenchement**.

Règle centrale : **aucune exception n'est propagée pour une source**. Seules
deux situations sont bloquantes — l'absence de sortie F5 exploitable (l'agent
n'a alors ni verdict ni dossier) et l'incohérence de produit entre deux fichiers.

La condition de déclenchement est propre à F6 : la classification n'a lieu que
si `verdict_potentiel.declenche_plc` vaut vrai dans la sortie F5, ou si
`--forcer` a été demandé explicitement. Un non-déclenchement est un **résultat**,
documenté et sorti en code 0.
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
    ENTREE_RECOMMANDATIONS,
    LIMITE_EXECUTION_FORCEE,
    MODE_FORCE,
    MODE_NON_DECLENCHE,
    MODE_NORMAL,
    SEUIL_FRAICHEUR_JOURS,
    logger,
)
from schemas import (
    AlerteCoherence,
    Declenchement,
    EntreeConcurrence,
    EntreeInsights,
    EntreeRecommandations,
    EntreesChargees,
    ErreurCoherenceProduit,
    SourceUtilisee,
)

ENCODAGES_TESTES: tuple[str, ...] = ("utf-8-sig", "utf-16", "utf-8", "cp1252")
"""Encodages essayés dans l'ordre.

Une redirection PowerShell produit de l'UTF-16 avec BOM, jamais de l'UTF-8 :
un fichier d'entrée obtenu par `> sortie.json` serait illisible sans cette liste.
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


def age_en_jours(horodatage: str | None, reference: datetime | None = None) -> int | None:
    """Calcule l'âge en jours d'un horodatage ISO 8601.

    Args:
        horodatage: Horodatage ISO 8601, éventuellement suffixé « Z ».
        reference: Date de référence ; l'instant courant par défaut.

    Returns:
        L'âge en jours, ou `None` si l'horodatage est absent ou illisible.
    """
    if not horodatage:
        return None
    try:
        date = datetime.fromisoformat(str(horodatage).replace("Z", "+00:00"))
    except ValueError:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    fin = reference or datetime.now(UTC)
    if fin.tzinfo is None:
        fin = fin.replace(tzinfo=UTC)
    return max(0, (fin - date).days)


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
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in erreur.errors()[:5]
        )
        compte_rendu.avertissements.append(f"structure non conforme : {resume}")
        logger.warning("[%s] structure non conforme : %s", nom_entree, resume)
        return None, compte_rendu

    compte_rendu.donnees_disponibles = True
    compte_rendu.nb_items_charges = _compter(entree)
    compte_rendu.nb_items_exploites = compte_rendu.nb_items_charges
    compte_rendu.avertissements.extend(_avertissements_qualite(nom_entree, entree))
    return entree, compte_rendu


def _compter(entree: Any) -> int:
    """Compte les éléments exploitables d'une entrée.

    Args:
        entree: Entrée validée.

    Returns:
        Un décompte représentatif du contenu utile à F6.
    """
    if isinstance(entree, EntreeRecommandations):
        dossier = entree.dossier_synthese
        if dossier is None:
            return 0
        total = len(dossier.demande.indicateurs) if dossier.demande else 0
        total += len(dossier.concurrence.intensite) if dossier.concurrence else 0
        return total
    if isinstance(entree, EntreeConcurrence):
        return len(entree.concurrents)
    if isinstance(entree, EntreeInsights):
        return entree.stats_corpus.nb_unites_analysees if entree.stats_corpus else 0
    return 0


def _avertissements_qualite(nom: str, entree: Any) -> list[str]:
    """Qualifie la fraîcheur et la confiance héritée d'une entrée chargée.

    Args:
        nom: Nom de l'entrée.
        entree: Entrée validée.

    Returns:
        Les avertissements constatés, éventuellement vides.
    """
    avertissements: list[str] = []
    horodatage = getattr(entree, "horodatage_utc", None)
    age = age_en_jours(horodatage)
    if age is None:
        avertissements.append(
            "aucun horodatage exploitable : la fraîcheur ne peut pas être qualifiée"
        )
    elif age > SEUIL_FRAICHEUR_JOURS:
        avertissements.append(
            f"analyse produite il y a {age} jours, au-delà du seuil de "
            f"{SEUIL_FRAICHEUR_JOURS} jours"
        )
    if not getattr(entree, "donnees_suffisantes", False):
        avertissements.append("l'agent amont déclare `donnees_suffisantes=false`")
    confiance = getattr(entree, "confiance_globale", None)
    if confiance is not None and confiance.niveau == "faible":
        avertissements.append("confiance amont déclarée faible")
    logger.debug("[%s] %d avertissement(s) de qualité", nom, len(avertissements))
    return avertissements


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
                        f"« {ref.marche.geo} ». La phase classée croiserait deux "
                        f"marchés : sa portée régionale est caduque."
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
    chemin_recommandations: str,
    chemin_insights: str | None,
    chemin_concurrence: str | None,
) -> tuple[EntreesChargees, list[SourceUtilisee], list[AlerteCoherence]]:
    """Charge, valide et confronte les trois fichiers d'entrée.

    Args:
        chemin_recommandations: Sortie de F5 — requise.
        chemin_insights: Sortie de F3, ou `None`.
        chemin_concurrence: Sortie de F4, ou `None`.

    Returns:
        Le triplet `(entrees, sources, alertes)`.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
    """
    recommandations, cr_reco = _charger_source(
        chemin_recommandations, EntreeRecommandations, ENTREE_RECOMMANDATIONS
    )
    concurrence, cr_concurrence = _charger_source(
        chemin_concurrence, EntreeConcurrence, ENTREE_CONCURRENCE
    )
    insights, cr_insights = _charger_source(
        chemin_insights, EntreeInsights, ENTREE_INSIGHTS
    )

    sources = [cr_reco, cr_concurrence, cr_insights]
    presentes: list[tuple[str, Any]] = [
        (nom, entree)
        for nom, entree in (
            (ENTREE_RECOMMANDATIONS, recommandations),
            (ENTREE_CONCURRENCE, concurrence),
            (ENTREE_INSIGHTS, insights),
        )
        if entree is not None
    ]
    alertes = _controler_coherence(presentes)

    limites_amont = [
        f"[{nom}] {limite}" for nom, entree in presentes for limite in entree.limites
    ]

    entrees = EntreesChargees(
        recommandations=recommandations,
        concurrence=concurrence,
        insights=insights,
        produit=presentes[0][1].produit if presentes else None,
        marche=presentes[0][1].marche if presentes else None,
        limites_amont=limites_amont,
    )
    return entrees, sources, alertes


def evaluer_declenchement(
    recommandations: EntreeRecommandations, forcer: bool
) -> tuple[Declenchement, list[str]]:
    """Applique la condition de déclenchement du CDC.

    La classification n'a lieu que si F5 a conclu à un potentiel positif. Le
    drapeau `--forcer` permet l'étude et le test, il est tracé dans la sortie et
    assorti d'une limite systématique.

    Args:
        recommandations: Sortie F5 validée.
        forcer: Vrai si `--forcer` a été demandé.

    Returns:
        Le couple `(declenchement, limites_a_ajouter)`.
    """
    verdict = recommandations.verdict_potentiel
    amont = bool(verdict.declenche_plc)

    if amont:
        return (
            Declenchement(
                declenche_plc_amont=True,
                mode=MODE_NORMAL,
                motif=(
                    f"verdict amont « {verdict.verdict} » : `declenche_plc` vaut vrai, "
                    f"la classification de phase est déclenchée conformément au CDC."
                ),
            ),
            [],
        )

    if forcer:
        return (
            Declenchement(
                declenche_plc_amont=False,
                mode=MODE_FORCE,
                motif=(
                    f"verdict amont « {verdict.verdict} » : la classification n'aurait "
                    f"pas dû être déclenchée, elle l'a été par le drapeau `--forcer` "
                    f"à des fins d'étude."
                ),
            ),
            [LIMITE_EXECUTION_FORCEE],
        )

    return (
        Declenchement(
            declenche_plc_amont=False,
            mode=MODE_NON_DECLENCHE,
            motif=(
                f"verdict amont « {verdict.verdict} » : classification non déclenchée "
                f"conformément au CDC (PLC uniquement si le potentiel est positif). "
                f"Ce n'est pas une erreur."
            ),
        ),
        [],
    )
