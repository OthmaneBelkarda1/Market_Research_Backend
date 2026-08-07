"""Construction du dossier de synthèse.

**Aucun appel LLM dans ce module.** L'extraction est déterministe et bornée.

Règle absolue : **aucune information hors dossier n'atteint les chaînes LLM.**
C'est la condition de la traçabilité — un fondement ne peut citer que ce qui a
été mis dans le dossier, et la post-validation rejette tout le reste.

Chaque élément porte une `ref` stable, qui est à la fois son adresse de citation
et la clé de récupération de sa valeur exacte lors de la post-validation.
"""

from __future__ import annotations

from config import (
    CONFIANCE_FAIBLE,
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_TENDANCES,
    MAX_ANGLES_DOSSIER,
    MAX_ATTENTES_DOSSIER,
    MAX_BESOINS_DOSSIER,
    MAX_CONCURRENTS_DOSSIER,
    MAX_FCS_DOSSIER,
    MAX_PAIN_POINTS_DOSSIER,
    MAX_REQUETES_EMERGENTES_DOSSIER,
    MOTIF_PLAFONNEMENT_MODE,
    PROFIL_EFFET_DE_MODE,
    logger,
)
from schemas import (
    DossierSynthese,
    ElementDossier,
    EntreesChargees,
    QualiteDonnees,
    QualiteEntree,
    SignauxConcurrence,
    SignauxConsommateur,
    SignauxDemande,
)


def _element(ref: str, libelle: str, valeur, detail: str = "") -> ElementDossier:
    """Fabrique un élément de dossier, valeur normalisée en texte.

    Args:
        ref: Référence stable.
        libelle: Intitulé lisible.
        valeur: Valeur brute, convertie en chaîne.
        detail: Complément éventuel.

    Returns:
        L'élément prêt à être cité.
    """
    return ElementDossier(
        ref=ref, libelle=libelle, valeur="" if valeur is None else str(valeur), detail=detail
    )


def _construire_demande(entrees: EntreesChargees) -> SignauxDemande | None:
    """Extrait les signaux de demande depuis la sortie Tendances.

    Args:
        entrees: Entrées chargées.

    Returns:
        Les signaux de demande, ou `None` si l'entrée est absente ou vide.
    """
    tendances = entrees.tendances
    if tendances is None or tendances.indicateurs is None:
        return None

    indicateurs = tendances.indicateurs
    elements: list[ElementDossier] = []

    def ajouter(champ: str, libelle: str, valeur, detail: str = "") -> None:
        if valeur is not None:
            elements.append(
                _element(f"tendances.indicateurs.{champ}", libelle, valeur, detail)
            )

    ajouter(
        "profil_courbe",
        "Profil de la courbe de demande",
        indicateurs.profil_courbe or None,
        "Classification produite par le collecteur, non validée empiriquement.",
    )
    ajouter("indice_moyen_12m", "Indice moyen sur 12 mois", indicateurs.indice_moyen_12m,
            "Indice relatif de 0 à 100 : aucun volume absolu n'en découle.")
    ajouter("momentum_90j", "Momentum sur 90 jours", indicateurs.momentum_90j,
            "Variation relative ; 0.19 signifie +19 %.")
    ajouter("pente_annuelle_5ans", "Pente annuelle sur 5 ans", indicateurs.pente_annuelle_5ans,
            "Points d'indice par an.")
    ajouter("volatilite", "Volatilité", indicateurs.volatilite,
            "Coefficient de variation de la série 5 ans.")
    ajouter("nb_breakout", "Nombre de requêtes en explosion", indicateurs.nb_breakout)
    ajouter("signal_effet_de_mode", "Signal d'effet de mode", indicateurs.signal_effet_de_mode)

    if indicateurs.saisonnalite is not None:
        saison = indicateurs.saisonnalite
        if saison.mois_pic is not None:
            elements.append(
                _element(
                    "tendances.indicateurs.saisonnalite.mois_pic",
                    "Mois de pic saisonnier",
                    saison.mois_pic,
                )
            )
        if saison.amplitude is not None:
            elements.append(
                _element(
                    "tendances.indicateurs.saisonnalite.amplitude",
                    "Amplitude saisonnière",
                    saison.amplitude,
                    "(max − min) / moyenne des indices mensuels.",
                )
            )
    if indicateurs.concentration_geo:
        tete = indicateurs.concentration_geo[0]
        elements.append(
            _element(
                "tendances.indicateurs.concentration_geo",
                "Zone la plus concentrée",
                f"{tete.get('zone')} ({tete.get('part')})",
            )
        )

    requetes = [
        _element(
            f"tendances.requetes_emergentes[{index}]",
            "Requête émergente",
            f"{requete.requete} ({requete.variation})",
            "Signal de curiosité, pas de demande d'achat.",
        )
        for index, requete in enumerate(
            tendances.requetes_emergentes[:MAX_REQUETES_EMERGENTES_DOSSIER]
        )
    ]

    effet_de_mode = bool(indicateurs.signal_effet_de_mode) or (
        indicateurs.profil_courbe == PROFIL_EFFET_DE_MODE
    )
    motif = ""
    if effet_de_mode:
        raisons = []
        if indicateurs.signal_effet_de_mode:
            raisons.append("`signal_effet_de_mode` levé par le collecteur")
        if indicateurs.profil_courbe == PROFIL_EFFET_DE_MODE:
            raisons.append("`profil_courbe` classé « effet_de_mode »")
        motif = " et ".join(raisons)

    return SignauxDemande(
        terme_pivot=tendances.mots_cles.terme_pivot if tendances.mots_cles else "",
        fallback_applique=(
            tendances.mots_cles.fallback_applique if tendances.mots_cles else False
        ),
        indicateurs=elements,
        requetes_emergentes=requetes,
        effet_de_mode=effet_de_mode,
        motif_effet_de_mode=motif,
    )


def _construire_consommateur(entrees: EntreesChargees) -> SignauxConsommateur | None:
    """Extrait les signaux consommateurs depuis la sortie F3.

    Args:
        entrees: Entrées chargées.

    Returns:
        Les signaux consommateurs, ou `None` si l'entrée est absente.
    """
    insights = entrees.insights
    if insights is None:
        return None

    pain_points = [
        _element(
            f"insights.pain_points[{index}]",
            pain.libelle,
            f"{pain.frequence_pct} % des unités, intensité {pain.intensite_moyenne}, "
            f"score {pain.score_priorite}",
            f"{pain.description} (portée {pain.portee}, confiance {pain.confiance})",
        )
        for index, pain in enumerate(insights.pain_points[:MAX_PAIN_POINTS_DOSSIER])
    ]
    besoins = [
        _element(
            f"insights.besoins[{index}]",
            besoin.libelle,
            besoin.type or "besoin",
            f"{besoin.description} (confiance {besoin.confiance})",
        )
        for index, besoin in enumerate(insights.besoins[:MAX_BESOINS_DOSSIER])
    ]
    attentes = [
        _element(
            f"insights.attentes[{index}]",
            attente.libelle,
            attente.niveau_exigence or "standard",
            attente.description,
        )
        for index, attente in enumerate(insights.attentes[:MAX_ATTENTES_DOSSIER])
    ]
    positifs = [
        _element(
            f"insights.signaux_positifs[{index}]",
            signal.libelle,
            signal.type or "signal positif",
            signal.description,
        )
        for index, signal in enumerate(insights.signaux_positifs[:MAX_BESOINS_DOSSIER])
    ]

    sentiment = None
    if insights.sentiment is not None and insights.sentiment.global_ is not None:
        globale = insights.sentiment.global_
        sentiment = _element(
            "insights.sentiment",
            "Répartition des sentiments",
            f"{globale.positif} positifs / {globale.negatif} négatifs / "
            f"{globale.neutre} neutres / {globale.mixte} mixtes sur {globale.base_nb} unités",
            insights.sentiment.commentaire,
        )

    sensibilite = None
    if (
        insights.comportements_achat is not None
        and insights.comportements_achat.sensibilite_prix is not None
    ):
        prix = insights.comportements_achat.sensibilite_prix
        sensibilite = _element(
            "insights.comportements_achat.sensibilite_prix",
            "Sensibilité au prix",
            prix.niveau,
            prix.commentaire,
        )

    return SignauxConsommateur(
        pain_points=pain_points,
        besoins=besoins,
        attentes=attentes,
        signaux_positifs=positifs,
        sentiment=sentiment,
        sensibilite_prix=sensibilite,
        divergences_sources=list(insights.divergences_sources[:6]),
        confiance_f3=(
            insights.confiance_globale.niveau
            if insights.confiance_globale
            else CONFIANCE_FAIBLE
        ),
    )


def _construire_concurrence(entrees: EntreesChargees) -> SignauxConcurrence | None:
    """Extrait les signaux concurrentiels depuis la sortie F4.

    Args:
        entrees: Entrées chargées.

    Returns:
        Les signaux concurrentiels, ou `None` si l'entrée est absente.
    """
    concurrence = entrees.concurrence
    if concurrence is None:
        return None

    intensite: list[ElementDossier] = []
    if concurrence.intensite_concurrentielle is not None:
        mesures = concurrence.intensite_concurrentielle
        for champ, libelle, valeur in (
            ("nb_concurrents_identifies", "Concurrents identifiés", mesures.nb_concurrents_identifies),
            ("nb_offres_coeur", "Offres au cœur du benchmark", mesures.nb_offres_coeur),
            ("nb_annonceurs", "Annonceurs actifs", mesures.nb_annonceurs),
            ("nb_annonces_actives", "Annonces actives", mesures.nb_annonces_actives),
            (
                "duree_diffusion_mediane_jours",
                "Longévité publicitaire médiane (jours)",
                mesures.duree_diffusion_mediane_jours,
            ),
            (
                "concentration_volumes_top3_pct",
                "Concentration des volumes du top 3 (%)",
                mesures.concentration_volumes_top3_pct,
            ),
        ):
            if valeur is not None:
                intensite.append(
                    _element(f"concurrence.intensite.{champ}", libelle, valeur)
                )
        if mesures.lecture:
            intensite.append(
                _element(
                    "concurrence.intensite.lecture",
                    "Lecture de l'intensité par F4",
                    mesures.lecture[:400],
                )
            )

    benchmark: list[ElementDossier] = []
    bornes: dict[str, dict[str, float]] = {}
    for repere in concurrence.benchmark_prix:
        prefixe = f"concurrence.benchmark_prix[{repere.source}][{repere.devise}]"
        benchmark.append(
            _element(
                f"{prefixe}.mediane",
                f"Prix médian {repere.source} en {repere.devise}",
                repere.prix_mediane,
                f"sur {repere.nb_offres_avec_prix} offres, "
                f"étendue {repere.prix_min}–{repere.prix_max}, "
                f"dispersion {repere.dispersion}",
            )
        )
        benchmark.append(
            _element(
                f"{prefixe}.etendue",
                f"Étendue de prix {repere.source} en {repere.devise}",
                f"{repere.prix_min}–{repere.prix_max}",
            )
        )
        for segment in repere.segments:
            benchmark.append(
                _element(
                    f"{prefixe}.segment.{segment.nom}",
                    f"Segment {segment.nom} {repere.source} en {repere.devise}",
                    f"{segment.borne_basse}–{segment.borne_haute}",
                )
            )
        courant = bornes.setdefault(repere.devise, {"min": repere.prix_min, "max": repere.prix_max})
        courant["min"] = min(courant["min"], repere.prix_min)
        courant["max"] = max(courant["max"], repere.prix_max)

    position = None
    if concurrence.position_prix_envisage is not None:
        pos = concurrence.position_prix_envisage
        position = _element(
            "concurrence.position_prix_envisage",
            "Position du prix envisagé",
            f"{pos.prix} {pos.devise} — percentile {pos.percentile}, "
            f"segment {pos.segment}, écart médiane {pos.ecart_mediane_pct} %",
            pos.commentaire,
        )

    angles: list[ElementDossier] = []
    facteurs: list[ElementDossier] = []
    if concurrence.positionnement is not None:
        angles = [
            _element(
                f"concurrence.positionnement.angles_peu_exploites[{index}]",
                point.point,
                point.statut,
                "Absence constatée dans le corpus F4, jamais une absence de marché.",
            )
            for index, point in enumerate(
                concurrence.positionnement.angles_peu_exploites[:MAX_ANGLES_DOSSIER]
            )
        ]
        facteurs = [
            _element(
                f"concurrence.positionnement.facteurs_cles_succes[{index}]",
                point.point,
                point.statut,
            )
            for index, point in enumerate(
                concurrence.positionnement.facteurs_cles_succes[:MAX_FCS_DOSSIER]
            )
        ]

    differenciation: list[ElementDossier] = []
    if concurrence.differenciation is not None:
        for famille, libelle in (
            ("attributs_distinctifs_potentiels", "Attribut distinctif potentiel"),
            ("attributs_partages", "Attribut partagé"),
            ("desavantages_apparents", "Désavantage apparent"),
        ):
            for index, point in enumerate(
                getattr(concurrence.differenciation, famille)[:MAX_ANGLES_DOSSIER]
            ):
                differenciation.append(
                    _element(
                        f"concurrence.differenciation.{famille}[{index}]",
                        f"{libelle} : {point.point}",
                        point.statut,
                    )
                )

    menaces = [
        _element(
            f"concurrence.concurrents[{index}]",
            fiche.concurrent.nom_canonique,
            (
                f"menace {fiche.analyse.niveau_menace}"
                if fiche.analyse and fiche.analyse.niveau_menace
                else "menace non évaluée"
            ),
            (
                f"{fiche.stats.nb_offres if fiche.stats else 0} offre(s), "
                f"fourchettes {fiche.stats.fourchette_prix_par_devise if fiche.stats else {}}"
            ),
        )
        for index, fiche in enumerate(concurrence.concurrents[:MAX_CONCURRENTS_DOSSIER])
    ]

    return SignauxConcurrence(
        intensite=intensite,
        benchmark=benchmark,
        position_prix=position,
        angles_peu_exploites=angles,
        facteurs_cles_succes=facteurs,
        differenciation=differenciation,
        menaces=menaces,
        validite_regionale=[
            f"{v.source} ({v.portee}) : {v.commentaire}"
            for v in concurrence.validite_regionale
        ],
        devises_benchmark=sorted({b.devise for b in concurrence.benchmark_prix}),
        bornes_benchmark=bornes,
        confiance_f4=(
            concurrence.confiance_globale.niveau
            if concurrence.confiance_globale
            else CONFIANCE_FAIBLE
        ),
    )


def construire_dossier(
    entrees: EntreesChargees, qualites: list[QualiteEntree]
) -> DossierSynthese:
    """Construit le dossier de synthèse complet.

    Args:
        entrees: Entrées chargées et validées.
        qualites: Qualité constatée de chaque entrée.

    Returns:
        Le dossier, seul contenu qui atteindra les chaînes LLM.
    """
    degradees = sum(
        1
        for q in qualites
        if q.presente and (not q.donnees_suffisantes or q.confiance_heritee == CONFIANCE_FAIBLE)
    )
    dossier = DossierSynthese(
        demande=_construire_demande(entrees),
        consommateur=_construire_consommateur(entrees),
        concurrence=_construire_concurrence(entrees),
        qualite_donnees=QualiteDonnees(
            entrees=qualites,
            nb_entrees_presentes=sum(1 for q in qualites if q.presente),
            nb_entrees_degradees=degradees,
        ),
    )
    logger.debug(
        "dossier : demande=%s, consommateur=%s, concurrence=%s ; %d références",
        dossier.demande is not None,
        dossier.consommateur is not None,
        dossier.concurrence is not None,
        len(dossier.references()),
    )
    return dossier


def entrees_manquantes(dossier: DossierSynthese) -> set[str]:
    """Liste les entrées absentes du dossier.

    Args:
        dossier: Dossier de synthèse.

    Returns:
        L'ensemble des noms d'entrées absentes.
    """
    manquantes: set[str] = set()
    if dossier.demande is None:
        manquantes.add(ENTREE_TENDANCES)
    if dossier.consommateur is None:
        manquantes.add(ENTREE_INSIGHTS)
    if dossier.concurrence is None:
        manquantes.add(ENTREE_CONCURRENCE)
    return manquantes


def motif_plafonnement(dossier: DossierSynthese) -> str | None:
    """Indique si le plafonnement « effet de mode » doit être appliqué.

    Args:
        dossier: Dossier de synthèse.

    Returns:
        Le motif de plafonnement, ou `None`.
    """
    if dossier.demande is not None and dossier.demande.effet_de_mode:
        return MOTIF_PLAFONNEMENT_MODE
    return None
