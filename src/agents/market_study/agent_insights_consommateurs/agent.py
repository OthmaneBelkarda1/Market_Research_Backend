"""Orchestration de bout en bout de l'agent Insights Consommateurs.

Séquence : chargement → corpus → carte → normalisation → réduction → synthèse
→ post-validation → assemblage.

La dégradation est gracieuse à chaque étape : une source manquante, un lot en
échec ou une chaîne de synthèse en erreur réduisent la sortie, ne l'annulent
jamais. Le seul cas bloquant — aucun fichier exploitable — est traité en amont
par `main.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from carte import (
    cartographier_documents,
    cartographier_unites,
    frequences_brutes_pain_points,
    frequences_brutes_themes,
    normaliser_libelles,
    remapper_analyses,
)
from chargement import charger_entrees
from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    FAMILLE_PAIN_POINTS_LIBELLE,
    FAMILLE_THEMES_LIBELLE,
    HYPOTHESES_SYSTEMATIQUES,
    LIMITES_SYSTEMATIQUES,
    MAX_PAIN_POINTS,
    MAX_THEMES,
    SEUIL_MIN_UNITES_FIABLE,
    SOURCE_WEB,
    logger,
    verifier_cle_api,
)
from corpus import construire_corpus
from reduction import reduire
from schemas import (
    Attente,
    Besoin,
    ConfianceGlobale,
    CorpusPrepare,
    EntreesChargees,
    FicheProduit,
    PainPoint,
    ParametresMarche,
    Reduction,
    ResultatInsightsConsommateurs,
    SortieLectureCritique,
    SortieSyntheseInsights,
    StatutAnalyse,
)
from synthese import lecture_critique, synthetiser_insights
from validation import valider


def _horodatage() -> str:
    """Retourne l'horodatage courant en ISO 8601 UTC.

    Returns:
        Un horodatage à la seconde, suffixé « Z ».
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _synthese_de_repli(
    corpus: CorpusPrepare, reduction: Reduction, produit: FicheProduit
) -> str:
    """Rédige une synthèse exécutive minimale, sans LLM.

    Utilisée lorsque la chaîne de lecture critique a échoué : les agrégats
    chiffrés restent livrés, la rédaction est assurée par le code.

    Args:
        corpus: Corpus analysé.
        reduction: Agrégats déterministes.
        produit: Fiche du produit étudié.

    Returns:
        Une synthèse factuelle de quelques lignes.
    """
    lignes = [
        f"Produit : {produit.nom}.",
        f"Corpus analysé : {corpus.stats.nb_unites_analysees} unité(s) consommateur "
        f"et {corpus.stats.nb_documents_analyses} document(s) web, réparties "
        f"ainsi : {corpus.stats.nb_unites_par_source}.",
    ]
    if reduction.sentiment is not None:
        globale = reduction.sentiment.global_
        lignes.append(
            f"Sentiment sur {globale.base_nb} unité(s) exploitable(s) : "
            f"{globale.positif} positif, {globale.negatif} négatif, "
            f"{globale.neutre} neutre, {globale.mixte} mixte."
        )
    if reduction.pain_points:
        tete = ", ".join(f"« {p.libelle} »" for p in reduction.pain_points[:3])
        lignes.append(f"Pain points prioritaires : {tete}.")
    else:
        lignes.append("Aucun pain point n'a pu être dégagé du corpus.")
    lignes.append(
        "La rédaction analytique n'a pas pu être produite (chaîne de synthèse en "
        "échec) : seuls les agrégats chiffrés sont livrés."
    )
    return "\n".join(lignes)


def _confiance_globale(
    corpus: CorpusPrepare,
    lecture: SortieLectureCritique | None,
    nb_sources: int,
) -> ConfianceGlobale:
    """Détermine la confiance globale, plafonnée par le volume du corpus.

    Le niveau proposé par le modèle n'est jamais retenu tel quel : le code le
    plafonne selon `SEUIL_MIN_UNITES_FIABLE` et le nombre de sources.

    Args:
        corpus: Corpus analysé.
        lecture: Sortie de la lecture critique, éventuellement absente.
        nb_sources: Nombre de sources effectivement exploitées.

    Returns:
        Le niveau de confiance et sa justification.
    """
    facteurs: list[str] = list(lecture.facteurs_confiance) if lecture else []
    nb_unites = corpus.stats.nb_unites_analysees

    if nb_unites < SEUIL_MIN_UNITES_FIABLE:
        niveau = CONFIANCE_FAIBLE
        justification = (
            f"Corpus de {nb_unites} unité(s), en deçà du seuil de fiabilité de "
            f"{SEUIL_MIN_UNITES_FIABLE} : toute fréquence calculée sur cette base "
            f"est instable."
        )
    elif nb_sources <= 1:
        niveau = CONFIANCE_FAIBLE
        justification = (
            "Une seule source exploitée : les insights reflètent le public de "
            "cette source et ne peuvent être recoupés."
        )
    else:
        propose = (lecture.niveau_confiance if lecture else CONFIANCE_MOYENNE) or ""
        niveau = (
            propose
            if propose in (CONFIANCE_ELEVEE, CONFIANCE_MOYENNE, CONFIANCE_FAIBLE)
            else CONFIANCE_MOYENNE
        )
        if niveau == CONFIANCE_ELEVEE and nb_unites < 2 * SEUIL_MIN_UNITES_FIABLE:
            niveau = CONFIANCE_MOYENNE
            facteurs.append(
                f"niveau ramené de « élevée » à « moyenne » par le code : "
                f"{nb_unites} unités restent un volume modeste"
            )
        justification = (
            lecture.justification_confiance
            if lecture and lecture.justification_confiance
            else f"{nb_unites} unités analysées sur {nb_sources} sources."
        )

    if lecture and lecture.biais_probables:
        facteurs.extend(lecture.biais_probables)
    return ConfianceGlobale(niveau=niveau, justification=justification, facteurs=facteurs)


def _assembler_insights(
    synthese: SortieSyntheseInsights | None, reduction: Reduction
) -> tuple[list[PainPoint], list[Besoin], list[Attente], list[Besoin]]:
    """Fusionne les agrégats chiffrés avec la rédaction de la synthèse.

    Args:
        synthese: Sortie de la chaîne de synthèse, éventuellement absente.
        reduction: Agrégats déterministes.

    Returns:
        Le quadruplet `(pain_points, besoins, attentes, signaux_positifs)`.
    """
    descriptions = (
        {d.libelle: d.description for d in synthese.descriptions_pain_points}
        if synthese
        else {}
    )
    pain_points = []
    for chiffre in reduction.pain_points[:MAX_PAIN_POINTS]:
        pain = chiffre.model_copy(deep=True)
        pain.description = descriptions.get(pain.libelle, "")
        pain.verbatims = list(reduction.verbatims_par_pain_point.get(pain.libelle, []))
        pain_points.append(pain)

    if synthese is None:
        besoins = [
            Besoin(libelle=b.libelle, preuves_id=list(b.preuves_id))
            for b in reduction.besoins_bruts
        ]
        attentes = [
            Attente(libelle=a.libelle, preuves_id=list(a.preuves_id))
            for a in reduction.attentes_brutes
        ]
        return pain_points, besoins, attentes, []

    besoins = [
        Besoin(
            libelle=b.libelle,
            description=b.description,
            type=b.type,
            preuves_id=list(b.preuves_id),
        )
        for b in synthese.besoins
    ]
    attentes = [
        Attente(
            libelle=a.libelle,
            description=a.description,
            niveau_exigence=a.niveau_exigence,
            preuves_id=list(a.preuves_id),
        )
        for a in synthese.attentes
    ]
    positifs = [
        Besoin(
            libelle=s.libelle,
            description=s.description,
            type=s.type,
            preuves_id=list(s.preuves_id),
        )
        for s in synthese.signaux_positifs
    ]
    return pain_points, besoins, attentes, positifs


def _squelette(
    produit: FicheProduit,
    marche: ParametresMarche,
    corpus: CorpusPrepare,
    limites: list[str],
) -> ResultatInsightsConsommateurs:
    """Construit une sortie valide mais vide, corpus insuffisant.

    Args:
        produit: Fiche du produit étudié.
        marche: Marché d'étude.
        corpus: Corpus préparé (vide ou quasi vide).
        limites: Limites accumulées.

    Returns:
        Un résultat conforme au schéma, `donnees_suffisantes=false`.
    """
    return ResultatInsightsConsommateurs(
        produit=produit,
        marche=marche,
        horodatage_utc=_horodatage(),
        stats_corpus=corpus.stats,
        synthese_executive=(
            f"Aucune unité consommateur exploitable n'a pu être constituée pour "
            f"« {produit.nom} » après filtrage. Aucun insight n'est produit : "
            f"relancer les collecteurs en amont est la seule issue."
        ),
        donnees_suffisantes=False,
        confiance_globale=ConfianceGlobale(
            niveau=CONFIANCE_FAIBLE,
            justification="Corpus vide après filtrage : aucune analyse possible.",
        ),
        limites=limites,
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
    )


def analyser_insights(
    chemin_reddit: str | None,
    chemin_amazon: str | None,
    chemin_web: str | None,
    langue_analyse: str,
) -> ResultatInsightsConsommateurs:
    """Produit l'analyse complète de l'axe 1 à partir des sorties collecteurs.

    Args:
        chemin_reddit: Chemin de la sortie `agent_reddit`, ou `None`.
        chemin_amazon: Chemin de la sortie `agent_amazon`, ou `None`.
        chemin_web: Chemin de la sortie `agent_recherche_web`, ou `None`.
        langue_analyse: Code langue de rédaction de l'analyse.

    Returns:
        Le résultat validé.

    Raises:
        ErreurCoherenceProduit: Si les fichiers portent des produits différents.
        RuntimeError: Si la clé API Anthropic est absente alors qu'un corpus
            exploitable a été constitué.
    """
    entrees, sources, alertes = charger_entrees(chemin_reddit, chemin_amazon, chemin_web)
    produit = entrees.produit or FicheProduit(nom="inconnu", description="")
    marche = entrees.marche or ParametresMarche(geo="??", langue=langue_analyse)

    corpus = construire_corpus(entrees)
    limites = list(entrees.limites_amont) + list(corpus.limites)
    limites.extend(LIMITES_SYSTEMATIQUES)

    for compte_rendu in sources:
        if compte_rendu.source in corpus.stats.nb_unites_par_source:
            compte_rendu.nb_items_exploites = corpus.stats.nb_unites_par_source[
                compte_rendu.source
            ]
        elif compte_rendu.source == SOURCE_WEB:
            compte_rendu.nb_items_exploites = corpus.stats.nb_documents_analyses

    if not corpus.unites and not corpus.documents:
        logger.warning("corpus vide après filtrage : sortie squelette")
        squelette = _squelette(produit, marche, corpus, limites)
        squelette.sources_utilisees = sources
        squelette.alertes_coherence = alertes
        return squelette

    verifier_cle_api()
    statuts: list[StatutAnalyse] = []
    libelle_marche = f"{marche.geo} ({marche.langue})"

    # --- Carte ------------------------------------------------------------- #
    analyses, statuts_unites = cartographier_unites(
        corpus.unites, produit.nom, produit.description, langue_analyse
    )
    statuts.extend(statuts_unites)
    analyses_documents, statuts_docs = cartographier_documents(
        corpus.documents, produit.nom, langue_analyse
    )
    statuts.extend(statuts_docs)

    # --- Normalisation des libellés ---------------------------------------- #
    table_themes, statut_themes = normaliser_libelles(
        frequences_brutes_themes(analyses),
        FAMILLE_THEMES_LIBELLE,
        MAX_THEMES,
        langue_analyse,
    )
    statuts.append(statut_themes)
    table_pain, statut_pain = normaliser_libelles(
        frequences_brutes_pain_points(analyses, analyses_documents),
        FAMILLE_PAIN_POINTS_LIBELLE,
        MAX_PAIN_POINTS,
        langue_analyse,
    )
    statuts.append(statut_pain)
    analyses, analyses_documents = remapper_analyses(
        analyses, analyses_documents, table_themes, table_pain
    )

    # --- Réduction --------------------------------------------------------- #
    reduction = reduire(corpus.unites, analyses, corpus.documents, analyses_documents)

    # --- Synthèse ---------------------------------------------------------- #
    synthese, statut_synthese = synthetiser_insights(
        reduction, produit, libelle_marche, langue_analyse
    )
    statuts.append(statut_synthese)

    pain_points, besoins, attentes, positifs = _assembler_insights(synthese, reduction)

    lecture, statut_lecture = lecture_critique(
        corpus,
        reduction,
        sources,
        [b.libelle for b in besoins],
        limites,
        produit,
        libelle_marche,
        langue_analyse,
    )
    statuts.append(statut_lecture)

    # --- Assemblage -------------------------------------------------------- #
    nb_sources_exploitees = len(
        [s for s in sources if s.nb_items_exploites > 0]
    )
    if corpus.stats.nb_unites_analysees < SEUIL_MIN_UNITES_FIABLE:
        limites.append(
            f"Corpus de {corpus.stats.nb_unites_analysees} unités, sous le seuil de "
            f"fiabilité de {SEUIL_MIN_UNITES_FIABLE} : les fréquences sont "
            f"indicatives et non stables."
        )

    sentiment = reduction.sentiment.model_copy(deep=True) if reduction.sentiment else None
    if sentiment is not None and synthese is not None:
        sentiment.commentaire = synthese.commentaire_sentiment

    comportements = (
        reduction.comportements.model_copy(deep=True) if reduction.comportements else None
    )
    if comportements is not None and synthese is not None:
        comportements.sensibilite_prix.niveau = synthese.sensibilite_prix.niveau
        comportements.sensibilite_prix.commentaire = synthese.sensibilite_prix.commentaire

    resultat = ResultatInsightsConsommateurs(
        produit=produit,
        marche=marche,
        horodatage_utc=_horodatage(),
        sources_utilisees=sources,
        alertes_coherence=alertes,
        stats_corpus=corpus.stats,
        sentiment=sentiment,
        themes=[t.model_copy(deep=True) for t in reduction.themes[:MAX_THEMES]],
        pain_points=pain_points,
        besoins=besoins,
        attentes=attentes,
        comportements_achat=comportements,
        signaux_positifs=positifs,
        divergences_sources=list(synthese.divergences_sources) if synthese else [],
        synthese_executive=(
            lecture.synthese_executive
            if lecture and lecture.synthese_executive.strip()
            else _synthese_de_repli(corpus, reduction, produit)
        ),
        statuts_analyse=statuts,
        donnees_suffisantes=bool(reduction.nb_unites_base),
        limites=limites,
        hypotheses=list(HYPOTHESES_SYSTEMATIQUES),
    )
    resultat.confiance_globale = _confiance_globale(corpus, lecture, nb_sources_exploitees)

    resultat, statuts_validation, alertes_validation = valider(resultat, corpus, reduction)
    resultat.statuts_analyse.extend(statuts_validation)
    resultat.alertes_coherence.extend(alertes_validation)
    return resultat
