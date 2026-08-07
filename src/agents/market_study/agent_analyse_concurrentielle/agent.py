"""Orchestration de bout en bout de l'agent Analyse Concurrentielle.

Séquence : chargement → référentiel → extraction → consolidation → benchmark
→ analyse → post-validation → assemblage.

La dégradation est gracieuse : une source manquante, un lot en échec ou une
chaîne qualitative en erreur réduisent la sortie sans jamais l'annuler. Les
blocs chiffrés sont livrés même quand toutes les chaînes qualitatives échouent.
"""

from __future__ import annotations

from datetime import UTC, datetime

from analyse import (
    analyser_concurrents,
    analyser_differenciation,
    lire_transversalement,
    rediger_synthese,
)
from benchmark import calculer
from chargement import charger_entrees
from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    HYPOTHESES_SYSTEMATIQUES,
    LIMITES_SYSTEMATIQUES,
    SEUIL_MIN_OFFRES_FIABLE,
    SOURCE_ALIEXPRESS,
    SOURCE_AMAZON,
    SOURCE_META_ADS,
    SOURCE_WEB,
    TOP_N_CONCURRENTS_ANALYSES,
    logger,
    verifier_cle_api,
)
from consolidation import consolider
from extraction import extraire_attributs, extraire_claims
from referentiel import construire_referentiel
from schemas import (
    ConcurrentConsolide,
    ConfianceGlobale,
    FicheConcurrent,
    FicheProduit,
    ParametresMarche,
    Referentiel,
    ResultatAnalyseConcurrentielle,
    SortieBenchmark,
    StatsConcurrent,
    StatutAnalyse,
)
from validation import valider


def _horodatage() -> str:
    """Retourne l'horodatage courant en ISO 8601 UTC.

    Returns:
        Un horodatage à la seconde, suffixé « Z ».
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _trier_concurrents(
    concurrents: list[ConcurrentConsolide], stats: dict[str, StatsConcurrent]
) -> list[ConcurrentConsolide]:
    """Trie les concurrents par volume décroissant puis présence multi-sources.

    Args:
        concurrents: Concurrents consolidés.
        stats: Statistiques par concurrent.

    Returns:
        Les concurrents triés.
    """

    def cle(concurrent: ConcurrentConsolide) -> tuple:
        chiffres = stats.get(concurrent.nom_canonique, StatsConcurrent())
        nb_sources = sum(1 for valeur in concurrent.presence.values() if valeur)
        return (
            -(chiffres.volume_ventes_cumule or 0),
            -nb_sources,
            -chiffres.nb_offres,
            concurrent.nom_canonique,
        )

    return sorted(concurrents, key=cle)


def _synthese_de_repli(
    referentiel: Referentiel, chiffres: SortieBenchmark, produit: FicheProduit
) -> str:
    """Rédige une synthèse minimale, sans LLM.

    Args:
        referentiel: Référentiel complet.
        chiffres: Résultats chiffrés.
        produit: Fiche du produit étudié.

    Returns:
        Une synthèse factuelle de quelques lignes.
    """
    lignes = [
        f"Produit : {produit.nom}.",
        f"Référentiel : {len(referentiel.offres)} offre(s) dont "
        f"{referentiel.stats.nb_offres_coeur} au cœur du benchmark, "
        f"{len(referentiel.annonces)} annonce(s), {len(referentiel.pages)} page(s).",
    ]
    for repere in chiffres.benchmarks:
        lignes.append(
            f"Benchmark {repere.source} en {repere.devise} : "
            f"{repere.prix_min} / médiane {repere.prix_mediane} / {repere.prix_max} "
            f"sur {repere.nb_offres_avec_prix} offre(s)."
        )
    if not chiffres.benchmarks:
        lignes.append("Aucun benchmark de prix : aucune source de prix exploitable.")
    lignes.append(
        "La rédaction analytique n'a pas pu être produite (chaînes de synthèse en "
        "échec) : seuls les blocs chiffrés sont livrés."
    )
    return "\n".join(lignes)


def _confiance_globale(
    referentiel: Referentiel, nb_sources: int, nb_concurrents: int
) -> ConfianceGlobale:
    """Détermine la confiance globale, plafonnée par le volume du référentiel.

    Args:
        referentiel: Référentiel complet.
        nb_sources: Nombre de sources exploitées.
        nb_concurrents: Nombre de concurrents consolidés.

    Returns:
        Le niveau de confiance et sa justification.
    """
    nb_coeur = referentiel.stats.nb_offres_coeur
    facteurs = [
        f"{nb_coeur} offre(s) au cœur du benchmark",
        f"{nb_sources} source(s) exploitée(s)",
        f"{nb_concurrents} concurrent(s) consolidé(s)",
    ]
    if nb_coeur < SEUIL_MIN_OFFRES_FIABLE:
        return ConfianceGlobale(
            niveau=CONFIANCE_FAIBLE,
            justification=(
                f"{nb_coeur} offre(s) au cœur du benchmark, sous le seuil de "
                f"fiabilité de {SEUIL_MIN_OFFRES_FIABLE} : toute statistique de prix "
                f"calculée sur cette base est instable."
            ),
            facteurs=facteurs,
        )
    if nb_sources <= 1:
        return ConfianceGlobale(
            niveau=CONFIANCE_FAIBLE,
            justification=(
                "Une seule source exploitée : aucun recoupement n'est possible entre "
                "plans d'observation."
            ),
            facteurs=facteurs,
        )
    niveau = (
        CONFIANCE_ELEVEE
        if nb_coeur >= 3 * SEUIL_MIN_OFFRES_FIABLE and nb_sources >= 3
        else CONFIANCE_MOYENNE
    )
    return ConfianceGlobale(
        niveau=niveau,
        justification=(
            f"{nb_coeur} offres au cœur du benchmark réparties sur {nb_sources} "
            f"sources, portées régionales hétérogènes documentées."
        ),
        facteurs=facteurs,
    )


def analyser_concurrence(
    chemin_aliexpress: str | None,
    chemin_amazon: str | None,
    chemin_meta_ads: str | None,
    chemin_web: str | None,
    prix_envisage: float | None,
    devise_envisagee: str | None,
    langue_analyse: str,
) -> ResultatAnalyseConcurrentielle:
    """Produit l'analyse complète de l'axe 2 à partir des sorties collecteurs.

    Args:
        chemin_aliexpress: Sortie de `agent_aliexpress`, ou `None`.
        chemin_amazon: Sortie de `agent_amazon`, ou `None`.
        chemin_meta_ads: Sortie de `agent_meta_ads`, ou `None`.
        chemin_web: Sortie de `agent_recherche_web`, ou `None`.
        prix_envisage: Prix envisagé pour le produit étudié, ou `None`.
        devise_envisagee: Devise du prix envisagé, ou `None`.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le résultat validé.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
        RuntimeError: Si la clé API est absente alors qu'un référentiel exploitable
            a été constitué.
    """
    entrees, sources, alertes, validite = charger_entrees(
        chemin_aliexpress, chemin_amazon, chemin_meta_ads, chemin_web
    )
    produit = entrees.produit or FicheProduit(nom="inconnu", description="")
    marche = entrees.marche or ParametresMarche(geo="??", langue=langue_analyse)

    referentiel = construire_referentiel(entrees)
    limites = list(entrees.limites_amont) + list(referentiel.limites)
    limites.extend(LIMITES_SYSTEMATIQUES)

    exploites = {
        SOURCE_ALIEXPRESS: referentiel.stats.nb_offres_par_source.get(SOURCE_ALIEXPRESS, 0),
        SOURCE_AMAZON: referentiel.stats.nb_offres_par_source.get(SOURCE_AMAZON, 0),
        SOURCE_META_ADS: referentiel.stats.nb_annonces,
        SOURCE_WEB: referentiel.stats.nb_pages,
    }
    for compte_rendu in sources:
        compte_rendu.nb_items_exploites = exploites.get(compte_rendu.source, 0)

    if referentiel.est_vide():
        logger.warning("référentiel vide : sortie squelette")
        return ResultatAnalyseConcurrentielle(
            produit=produit,
            marche=marche,
            horodatage_utc=_horodatage(),
            sources_utilisees=sources,
            alertes_coherence=alertes,
            referentiel_stats=referentiel.stats,
            validite_regionale=validite,
            synthese_executive=(
                f"Aucune offre, annonce ni page concurrente exploitable n'a pu être "
                f"constituée pour « {produit.nom} ». Aucune analyse concurrentielle "
                f"n'est produite : relancer les collecteurs en amont est la seule issue."
            ),
            donnees_suffisantes=False,
            confiance_globale=ConfianceGlobale(
                niveau=CONFIANCE_FAIBLE,
                justification="Référentiel vide : aucune analyse possible.",
            ),
            limites=limites,
            hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
        )

    verifier_cle_api()
    statuts: list[StatutAnalyse] = []

    # --- Extraction -------------------------------------------------------- #
    statuts.extend(extraire_attributs(referentiel.offres, produit.nom, langue_analyse))
    statuts.extend(extraire_claims(referentiel.annonces, produit.nom, langue_analyse))

    # --- Consolidation ----------------------------------------------------- #
    concurrents, statut_consolidation, alertes_consolidation = consolider(
        referentiel, produit.nom, langue_analyse
    )
    statuts.append(statut_consolidation)
    alertes.extend(alertes_consolidation)

    # --- Benchmark --------------------------------------------------------- #
    chiffres = calculer(referentiel, concurrents, prix_envisage, devise_envisagee)
    concurrents = _trier_concurrents(concurrents, chiffres.stats_par_concurrent)

    if not chiffres.benchmarks:
        limites.append(
            "Benchmark prix impossible : aucune source de prix exploitable (ni "
            "AliExpress, ni Amazon). Les blocs de positionnement et de pression "
            "publicitaire restent livrés."
        )
    if referentiel.stats.nb_offres_coeur < SEUIL_MIN_OFFRES_FIABLE:
        limites.append(
            f"Seulement {referentiel.stats.nb_offres_coeur} offre(s) au cœur du "
            f"benchmark, sous le seuil de {SEUIL_MIN_OFFRES_FIABLE} : les médianes et "
            f"les segments sont indicatifs, pas stables."
        )

    # --- Analyse qualitative ----------------------------------------------- #
    analyses, statuts_concurrents = analyser_concurrents(
        concurrents,
        chiffres.stats_par_concurrent,
        referentiel,
        chiffres.segment_par_offre,
        produit,
        langue_analyse,
    )
    statuts.extend(statuts_concurrents)

    lecture, statut_lecture = lire_transversalement(
        referentiel, chiffres, produit, langue_analyse
    )
    statuts.append(statut_lecture)

    differenciation, statut_differenciation = analyser_differenciation(
        referentiel, lecture, chiffres, produit, langue_analyse
    )
    statuts.append(statut_differenciation)

    fiches = [
        FicheConcurrent(
            concurrent=concurrent,
            stats=chiffres.stats_par_concurrent.get(
                concurrent.nom_canonique, StatsConcurrent()
            ),
            analyse=analyses.get(concurrent.nom_canonique),
        )
        for concurrent in concurrents
    ]
    if len(concurrents) > TOP_N_CONCURRENTS_ANALYSES:
        limites.append(
            f"Seuls les {TOP_N_CONCURRENTS_ANALYSES} premiers concurrents (par volume "
            f"puis présence multi-sources) ont fait l'objet d'une analyse qualitative ; "
            f"les {len(concurrents) - TOP_N_CONCURRENTS_ANALYSES} suivants ne portent "
            f"que leurs statistiques."
        )

    resumes = [
        {
            "concurrent": fiche.concurrent.nom_canonique,
            "type": fiche.concurrent.type,
            "presence": fiche.concurrent.presence,
            "stats": fiche.stats.model_dump(),
            "niveau_menace": fiche.analyse.niveau_menace if fiche.analyse else None,
        }
        for fiche in fiches[:TOP_N_CONCURRENTS_ANALYSES]
    ]
    synthese, statut_synthese = rediger_synthese(
        resumes,
        chiffres,
        lecture,
        [v.model_dump() for v in validite],
        produit,
        langue_analyse,
    )
    statuts.append(statut_synthese)

    nb_sources_exploitees = sum(1 for valeur in exploites.values() if valeur)
    resultat = ResultatAnalyseConcurrentielle(
        produit=produit,
        marche=marche,
        horodatage_utc=_horodatage(),
        sources_utilisees=sources,
        alertes_coherence=alertes,
        referentiel_stats=referentiel.stats,
        concurrents=fiches,
        benchmark_prix=chiffres.benchmarks,
        position_prix_envisage=chiffres.position_prix,
        intensite_concurrentielle=chiffres.intensite,
        positionnement=lecture.positionnement if lecture else None,
        differenciation=differenciation,
        validite_regionale=validite,
        synthese_executive=(
            synthese.strip()
            or _synthese_de_repli(referentiel, chiffres, produit)
        ),
        statuts_analyse=statuts,
        donnees_suffisantes=bool(referentiel.stats.nb_offres_coeur or referentiel.annonces),
        confiance_globale=_confiance_globale(
            referentiel, nb_sources_exploitees, len(concurrents)
        ),
        limites=limites,
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
    )
    if resultat.intensite_concurrentielle is not None and lecture is not None:
        resultat.intensite_concurrentielle.lecture = lecture.lecture_intensite

    resultat, statuts_validation, alertes_validation = valider(
        resultat, referentiel, chiffres
    )
    resultat.statuts_analyse.extend(statuts_validation)
    resultat.alertes_coherence.extend(alertes_validation)
    return resultat
