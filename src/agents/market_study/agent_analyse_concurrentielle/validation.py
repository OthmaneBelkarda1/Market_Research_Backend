"""Post-validation déterministe du résultat assemblé.

**Aucun appel LLM dans ce module.** Trois garanties y sont produites :

1. toute `Preuve.id_reference` existe dans le référentiel, et tout extrait
   textuel est une sous-chaîne du texte de sa preuve ;
2. un `PointEtaye` privé de toutes ses preuves passe en `hypothese` ;
3. tout champ numérique est réécrit depuis `benchmark.py`, et le tableau
   comparatif est intégralement régénéré par le code.
"""

from __future__ import annotations

import re

from config import (
    MAX_CARACTERES_EXTRAIT,
    STATUT_FAIT,
    STATUT_HYPOTHESE,
    logger,
)
from schemas import (
    AlerteCoherence,
    LigneComparatif,
    PointEtaye,
    Preuve,
    Referentiel,
    ResultatAnalyseConcurrentielle,
    SortieBenchmark,
    StatutAnalyse,
)

PHASE_POST_VALIDATION: str = "post_validation"

_ESPACES = re.compile(r"\s+")

TYPE_OFFRE: str = "offre"
TYPE_ANNONCE: str = "annonce"
TYPE_PAGE: str = "page"
TYPE_AVIS: str = "avis"


def _normaliser(texte: str) -> str:
    """Normalise les espaces pour la comparaison de sous-chaîne.

    Args:
        texte: Texte brut.

    Returns:
        Le texte aux espaces réduits, en minuscules.
    """
    return _ESPACES.sub(" ", texte).strip().lower()


class _Index:
    """Index des textes citables du référentiel, par identifiant."""

    def __init__(self, referentiel: Referentiel) -> None:
        """Construit l'index à partir du référentiel.

        Args:
            referentiel: Référentiel complet.
        """
        self.textes: dict[str, str] = {}
        self.types: dict[str, str] = {}
        for offre in referentiel.offres:
            self.textes[offre.id_offre] = offre.titre
            self.types[offre.id_offre] = TYPE_OFFRE
        for annonce in referentiel.annonces:
            self.textes[annonce.id_annonce] = annonce.texte_complet
            self.types[annonce.id_annonce] = TYPE_ANNONCE
        for page in referentiel.pages:
            self.textes[page.id_page] = page.extrait
            self.types[page.id_page] = TYPE_PAGE
        for avis in referentiel.avis:
            self.textes[avis.id_avis] = avis.texte
            self.types[avis.id_avis] = TYPE_AVIS


def _valider_preuves(
    preuves: list[Preuve], index: _Index, compteurs: dict[str, int]
) -> list[Preuve]:
    """Filtre les preuves invalides et corrige les extraits non conformes.

    Args:
        preuves: Preuves proposées.
        index: Index des textes citables.
        compteurs: Compteurs de correction, enrichis sur place.

    Returns:
        Les preuves conservées.
    """
    gardees: list[Preuve] = []
    for preuve in preuves:
        texte = index.textes.get(preuve.id_reference)
        if texte is None:
            compteurs["references_retirees"] = compteurs.get("references_retirees", 0) + 1
            continue
        preuve.type = index.types[preuve.id_reference]
        if preuve.extrait:
            if _normaliser(preuve.extrait) not in _normaliser(texte):
                preuve.extrait = texte[:MAX_CARACTERES_EXTRAIT].strip()
                compteurs["extraits_corriges"] = compteurs.get("extraits_corriges", 0) + 1
            elif len(preuve.extrait) > MAX_CARACTERES_EXTRAIT:
                preuve.extrait = preuve.extrait[:MAX_CARACTERES_EXTRAIT]
                compteurs["extraits_tronques"] = compteurs.get("extraits_tronques", 0) + 1
        gardees.append(preuve)
    return gardees


def _valider_points(
    points: list[PointEtaye], index: _Index, compteurs: dict[str, int]
) -> list[PointEtaye]:
    """Valide une liste de constats étayés.

    Un constat privé de toutes ses preuves est **conservé** mais rétrogradé en
    hypothèse : le supprimer silencieusement ferait disparaître un signal sans
    trace.

    Args:
        points: Constats proposés.
        index: Index des textes citables.
        compteurs: Compteurs de correction, enrichis sur place.

    Returns:
        Les constats validés.
    """
    for point in points:
        avant = len(point.preuves)
        point.preuves = _valider_preuves(point.preuves, index, compteurs)
        if not point.preuves and point.statut == STATUT_FAIT:
            point.statut = STATUT_HYPOTHESE
            compteurs["points_retrogrades"] = compteurs.get("points_retrogrades", 0) + 1
        elif avant and not point.preuves:
            compteurs["points_sans_preuve"] = compteurs.get("points_sans_preuve", 0) + 1
        if point.statut not in (STATUT_FAIT, STATUT_HYPOTHESE):
            point.statut = STATUT_HYPOTHESE
    return points


def _regenerer_tableau(
    resultat: ResultatAnalyseConcurrentielle,
) -> list[LigneComparatif]:
    """Régénère intégralement le tableau comparatif depuis les fiches validées.

    Args:
        resultat: Résultat en cours de validation.

    Returns:
        Le tableau comparatif.
    """
    lignes: list[LigneComparatif] = []
    for fiche in resultat.concurrents:
        analyse = fiche.analyse
        lignes.append(
            LigneComparatif(
                concurrent=fiche.concurrent.nom_canonique,
                presence_sources=[
                    source for source, nombre in fiche.concurrent.presence.items() if nombre
                ],
                fourchette_prix_par_devise=dict(fiche.stats.fourchette_prix_par_devise),
                note_moyenne=fiche.stats.note_moyenne,
                volume_ventes_cumule=fiche.stats.volume_ventes_cumule,
                argument_principal=(
                    analyse.arguments_marketing[0]
                    if analyse and analyse.arguments_marketing
                    else None
                ),
                force_principale=(
                    analyse.forces[0].point if analyse and analyse.forces else None
                ),
                faiblesse_principale=(
                    analyse.faiblesses[0].point if analyse and analyse.faiblesses else None
                ),
            )
        )
    return lignes


def valider(
    resultat: ResultatAnalyseConcurrentielle,
    referentiel: Referentiel,
    chiffres: SortieBenchmark,
) -> tuple[
    ResultatAnalyseConcurrentielle, list[StatutAnalyse], list[AlerteCoherence]
]:
    """Corrige le résultat assemblé et trace chaque correction.

    Args:
        resultat: Résultat brut, avant publication.
        referentiel: Référentiel, source de vérité des identifiants et des textes.
        chiffres: Résultats chiffrés, source de vérité des nombres.

    Returns:
        Le triplet `(resultat_corrige, statuts, alertes)`.
    """
    index = _Index(referentiel)
    compteurs: dict[str, int] = {}
    alertes: list[AlerteCoherence] = []

    # --- 1. Preuves des analyses par concurrent ---------------------------- #
    for fiche in resultat.concurrents:
        stats = chiffres.stats_par_concurrent.get(fiche.concurrent.nom_canonique)
        if stats is not None:
            fiche.stats = stats.model_copy(deep=True)
        if fiche.analyse is None:
            continue
        fiche.analyse.forces = _valider_points(fiche.analyse.forces, index, compteurs)
        fiche.analyse.faiblesses = _valider_points(
            fiche.analyse.faiblesses, index, compteurs
        )
        # Le segment de prix par source est recalculé depuis le benchmark.
        segments: dict[str, str] = {}
        for identifiant in fiche.concurrent.ids_offres:
            segment = chiffres.segment_par_offre.get(identifiant)
            if segment:
                source = "aliexpress" if identifiant.startswith("ax-") else "amazon"
                segments.setdefault(source, segment)
        fiche.analyse.segment_prix_par_source = segments

    # --- 2. Preuves du positionnement et de la différenciation ------------- #
    if resultat.positionnement is not None:
        for famille in (
            "messages_dominants",
            "angles_peu_exploites",
            "facteurs_cles_succes",
            "normes_marche",
        ):
            setattr(
                resultat.positionnement,
                famille,
                _valider_points(getattr(resultat.positionnement, famille), index, compteurs),
            )
    if resultat.differenciation is not None:
        for famille in (
            "attributs_partages",
            "attributs_distinctifs_potentiels",
            "desavantages_apparents",
        ):
            setattr(
                resultat.differenciation,
                famille,
                _valider_points(getattr(resultat.differenciation, famille), index, compteurs),
            )

    # --- 3. Nombres : écrasement systématique ------------------------------ #
    resultat.benchmark_prix = [b.model_copy(deep=True) for b in chiffres.benchmarks]
    resultat.position_prix_envisage = (
        chiffres.position_prix.model_copy(deep=True) if chiffres.position_prix else None
    )
    if chiffres.intensite is not None:
        lecture = (
            resultat.intensite_concurrentielle.lecture
            if resultat.intensite_concurrentielle
            else ""
        )
        resultat.intensite_concurrentielle = chiffres.intensite.model_copy(deep=True)
        resultat.intensite_concurrentielle.lecture = lecture
    resultat.referentiel_stats = referentiel.stats.model_copy(deep=True)

    # --- 4. Tableau comparatif : régénéré par le code ---------------------- #
    resultat.tableau_comparatif = _regenerer_tableau(resultat)

    # --- 5. Traçabilité ---------------------------------------------------- #
    statuts: list[StatutAnalyse] = []
    messages = {
        "references_retirees": (
            "preuve(s) citant un identifiant inexistant retirée(s) : le modèle a "
            "référencé des éléments absents du référentiel"
        ),
        "extraits_corriges": (
            "extrait(s) de preuve absent(s) du texte source, remplacé(s) par le "
            "début réel de ce texte"
        ),
        "extraits_tronques": "extrait(s) trop long(s) tronqué(s)",
        "points_retrogrades": (
            "constat(s) déclaré(s) « fait » sans preuve valide rétrogradé(s) en "
            "« hypothese »"
        ),
        "points_sans_preuve": "constat(s) restant(s) sans aucune preuve valide",
    }
    for cle, libelle in messages.items():
        if compteurs.get(cle):
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_POST_VALIDATION,
                    succes=True,
                    message_erreur=f"{compteurs[cle]} {libelle}.",
                    nb_elements=compteurs[cle],
                )
            )
    if compteurs.get("points_retrogrades") or compteurs.get("points_sans_preuve"):
        alertes.append(
            AlerteCoherence(
                type="preuve_manquante",
                detail=(
                    "Certains constats ne sont plus étayés par une preuve vérifiable ; "
                    "ils restent publiés mais en statut « hypothese »."
                ),
            )
        )
    if not statuts:
        statuts.append(
            StatutAnalyse(phase=PHASE_POST_VALIDATION, succes=True, nb_elements=0)
        )

    logger.debug("post-validation : %s", compteurs or "aucune correction")
    return resultat, statuts, alertes
