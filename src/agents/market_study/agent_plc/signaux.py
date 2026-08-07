"""Construction du dossier PLC — **aucun appel LLM dans ce module**.

Quatre familles de signaux temporels sont extraites des entrées, chaque
indicateur portant une `ref` stable. C'est le SEUL contenu qui atteindra les
chaînes LLM : aucune information hors dossier PLC ne doit les toucher.

⚠️ **Règle de non-reconstitution.** La famille « dynamique publicitaire » est
lue dans la sortie F4, à défaut dans l'écho F5. Si elle est absente des deux —
état constaté au run n°1, exigence D4 non implémentée en amont — elle est
déclarée non évaluable et signalée. Elle n'est **jamais** reconstituée depuis
les durées de diffusion : sur une annonce active, `date_fin` vaut la date de
collecte, ce qui rend tout calcul local d'ancienneté ou d'arrêt invalide.
"""

from __future__ import annotations

from datetime import datetime

from chargement import age_en_jours
from config import (
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_RECOMMANDATIONS,
    FAMILLE_CORPUS,
    FAMILLE_DEMANDE,
    FAMILLE_OFFRE,
    FAMILLE_PUBLICITE,
    FAMILLES_SIGNAUX,
    SOURCE_CONCURRENCE,
    SOURCE_ECHO_F5,
    SOURCE_INSIGHTS,
    TYPE_MARQUE_ETABLIE,
    TYPE_OFFRES_SANS_MARQUE,
    logger,
)
from schemas import (
    DossierPLC,
    DynamiquePublicitaireEntree,
    EntreeConcurrence,
    EntreeInsights,
    EntreeRecommandations,
    EntreesChargees,
    FamilleSignaux,
    IndicateurSignal,
)

AVERTISSEMENT_D4: str = (
    "dynamique_publicitaire absente des entrées : exigence D4 non satisfaite en "
    "amont ; famille non évaluable en attendant le correctif F4. Aucune "
    "reconstitution locale n'est possible — sur une annonce active, `date_fin` "
    "vaut la date de collecte."
)

DETAIL_INDICE_RELATIF: str = (
    "indice relatif à la période interrogée, aucun volume absolu de recherche"
)
DETAIL_CORPUS: str = (
    "volume de collecte, jamais un volume de marché ni un nombre d'acheteurs"
)
DETAIL_LONGEVITE: str = (
    "mesure une persistance de diffusion, jamais une rentabilité"
)

REF_LECTURE_INTENSITE: str = "concurrence.intensite.lecture"
"""Écarté du dossier : c'est un commentaire rédigé, pas un indicateur."""


def _nombre(valeur: float | int | None) -> str:
    """Formate un nombre pour l'affichage dans le dossier.

    Args:
        valeur: Valeur numérique, ou `None`.

    Returns:
        La valeur formatée, ou une chaîne vide si elle est absente.
    """
    if valeur is None:
        return ""
    if isinstance(valeur, int) or float(valeur).is_integer():
        return str(int(valeur))
    return f"{float(valeur):.2f}".rstrip("0").rstrip(".")


def _famille_vide(identifiant: str, avertissements: list[str]) -> FamilleSignaux:
    """Construit une famille non évaluable.

    Args:
        identifiant: Identifiant de la famille.
        avertissements: Motifs de non-disponibilité.

    Returns:
        La famille marquée non disponible.
    """
    intitule = next(
        (f["intitule"] for f in FAMILLES_SIGNAUX if f["id"] == identifiant), identifiant
    )
    return FamilleSignaux(
        famille=identifiant,
        intitule=intitule,
        disponible=False,
        source_effective=None,
        indicateurs=[],
        avertissements=avertissements,
    )


def _construire_demande(recommandations: EntreeRecommandations) -> FamilleSignaux:
    """Extrait la trajectoire de la demande depuis l'écho Tendances de F5.

    Args:
        recommandations: Sortie F5 validée.

    Returns:
        La famille « demande ».
    """
    dossier = recommandations.dossier_synthese
    demande = dossier.demande if dossier else None
    if demande is None or not demande.indicateurs:
        return _famille_vide(
            FAMILLE_DEMANDE,
            [
                "aucun indicateur de tendance dans l'écho F5 : la trajectoire de la "
                "demande ne peut pas être qualifiée"
            ],
        )

    indicateurs = [
        IndicateurSignal(
            ref=element.ref,
            libelle=element.libelle,
            valeur=element.valeur,
            detail=element.detail or DETAIL_INDICE_RELATIF,
        )
        for element in demande.indicateurs
    ]
    avertissements: list[str] = [
        "les indices de tendance sont relatifs : ils ne portent aucun volume absolu "
        "de recherche, donc aucune taille de marché"
    ]
    if demande.fallback_applique:
        avertissements.append(
            "le collecteur Tendances a appliqué un repli de mot-clé : la série "
            "décrit un terme voisin, pas le terme pivot initial"
        )
    if demande.effet_de_mode:
        avertissements.append(
            f"effet de mode signalé en amont : {demande.motif_effet_de_mode}"
        )
    return FamilleSignaux(
        famille=FAMILLE_DEMANDE,
        intitule="Trajectoire de la demande",
        disponible=True,
        source_effective=SOURCE_ECHO_F5,
        indicateurs=indicateurs,
        avertissements=avertissements,
    )


def _indicateurs_publicitaires(
    dynamique: DynamiquePublicitaireEntree,
) -> list[IndicateurSignal]:
    """Convertit un bloc de dynamique publicitaire en indicateurs citables.

    Args:
        dynamique: Bloc D4 renseigné.

    Returns:
        Les indicateurs, dans un ordre stable.
    """
    prefixe = "concurrence.dynamique_publicitaire"
    champs: tuple[tuple[str, str, str], ...] = (
        (
            "part_lancements_recents",
            "Part de lancements publicitaires récents",
            "fenêtre de récence définie par l'agent amont, non recalculée ici",
        ),
        ("nb_lancements_recents", "Nombre de lancements récents", ""),
        (
            "anciennete_mediane_actives_jours",
            "Ancienneté médiane des annonces actives (jours)",
            DETAIL_LONGEVITE,
        ),
        (
            "anciennete_max_actives_jours",
            "Ancienneté maximale des annonces actives (jours)",
            DETAIL_LONGEVITE,
        ),
        (
            "part_annonces_actives",
            "Part des annonces encore actives",
            "seul le drapeau `active` fait foi ; aucune date de fin n'est un arrêt",
        ),
        (
            "nb_arrets_recents",
            "Nombre d'arrêts réels récents",
            "arrêts confirmés par l'agent amont, jamais déduits d'une date de fin",
        ),
    )
    indicateurs: list[IndicateurSignal] = []
    for champ, libelle, detail in champs:
        valeur = getattr(dynamique, champ, None)
        if valeur is None:
            continue
        indicateurs.append(
            IndicateurSignal(
                ref=f"{prefixe}.{champ}",
                libelle=libelle,
                valeur=_nombre(valeur),
                detail=detail,
            )
        )
    if dynamique.repartition_lancements_mensuels:
        repartition = "; ".join(
            f"{mois} : {nb}"
            for mois, nb in sorted(dynamique.repartition_lancements_mensuels.items())
        )
        indicateurs.append(
            IndicateurSignal(
                ref=f"{prefixe}.repartition_lancements_mensuels",
                libelle="Répartition mensuelle des lancements",
                valeur=repartition,
                detail="dates de lancement déclarées par l'agent amont",
            )
        )
    return indicateurs


def _construire_publicite(
    concurrence: EntreeConcurrence | None, recommandations: EntreeRecommandations
) -> FamilleSignaux:
    """Extrait la dynamique publicitaire, sans jamais la reconstituer.

    Args:
        concurrence: Sortie F4, ou `None`.
        recommandations: Sortie F5 validée, porteuse de l'écho éventuel.

    Returns:
        La famille « dynamique_publicitaire », le plus souvent non évaluable.
    """
    candidates: list[tuple[str, DynamiquePublicitaireEntree | None]] = []
    if concurrence is not None and concurrence.intensite_concurrentielle is not None:
        candidates.append(
            (SOURCE_CONCURRENCE, concurrence.intensite_concurrentielle.dynamique_publicitaire)
        )
    dossier = recommandations.dossier_synthese
    if dossier is not None and dossier.concurrence is not None:
        candidates.append((SOURCE_ECHO_F5, dossier.concurrence.dynamique_publicitaire))

    for source, dynamique in candidates:
        if dynamique is not None and dynamique.renseignee():
            indicateurs = _indicateurs_publicitaires(dynamique)
            avertissements = [DETAIL_LONGEVITE]
            if dynamique.avertissement_date_fin:
                avertissements.append(dynamique.avertissement_date_fin)
            return FamilleSignaux(
                famille=FAMILLE_PUBLICITE,
                intitule="Dynamique des campagnes Meta",
                disponible=bool(indicateurs),
                source_effective=source if indicateurs else None,
                indicateurs=indicateurs,
                avertissements=avertissements,
            )

    logger.warning("dynamique_publicitaire absente des entrées — famille non évaluable")
    return _famille_vide(FAMILLE_PUBLICITE, [AVERTISSEMENT_D4])


def _construire_offre(
    concurrence: EntreeConcurrence | None, recommandations: EntreeRecommandations
) -> FamilleSignaux:
    """Extrait la structure et la saturation de l'offre.

    Args:
        concurrence: Sortie F4, ou `None`.
        recommandations: Sortie F5 validée, porteuse de l'écho.

    Returns:
        La famille « structure_offre ».
    """
    indicateurs: list[IndicateurSignal] = []
    avertissements: list[str] = []

    if concurrence is not None and concurrence.intensite_concurrentielle is not None:
        intensite = concurrence.intensite_concurrentielle
        champs: tuple[tuple[str, str, str], ...] = (
            ("nb_concurrents_identifies", "Concurrents identifiés", ""),
            ("nb_offres_coeur", "Offres au cœur du benchmark", ""),
            ("nb_annonceurs", "Annonceurs actifs", ""),
            ("nb_annonces_actives", "Annonces actives", ""),
            (
                "concentration_volumes_top3_pct",
                "Concentration des volumes du top 3 (%)",
                "part des volumes captée par les trois premiers acteurs du corpus",
            ),
        )
        for champ, libelle, detail in champs:
            valeur = getattr(intensite, champ, None)
            if valeur in (None, 0):
                continue
            indicateurs.append(
                IndicateurSignal(
                    ref=f"concurrence.intensite.{champ}",
                    libelle=libelle,
                    valeur=_nombre(valeur),
                    detail=detail,
                )
            )
        source = SOURCE_CONCURRENCE
    else:
        dossier = recommandations.dossier_synthese
        echo = dossier.concurrence if dossier else None
        for element in echo.intensite if echo else []:
            if element.ref == REF_LECTURE_INTENSITE:
                continue
            indicateurs.append(
                IndicateurSignal(
                    ref=element.ref,
                    libelle=element.libelle,
                    valeur=element.valeur,
                    detail=element.detail,
                )
            )
        source = SOURCE_ECHO_F5
        if indicateurs:
            avertissements.append(
                "structure de l'offre lue dans l'écho F5 : la sortie F4 n'a pas été "
                "fournie, la typologie des concurrents n'est pas disponible"
            )

    if concurrence is not None and concurrence.concurrents:
        total_offres = sum(
            fiche.stats.nb_offres if fiche.stats else 0 for fiche in concurrence.concurrents
        )
        offres_sans_marque = sum(
            fiche.stats.nb_offres if fiche.stats else 0
            for fiche in concurrence.concurrents
            if fiche.concurrent.type == TYPE_OFFRES_SANS_MARQUE
        )
        nb_marques = sum(
            1
            for fiche in concurrence.concurrents
            if fiche.concurrent.type == TYPE_MARQUE_ETABLIE
        )
        if total_offres:
            part = 100.0 * offres_sans_marque / total_offres
            indicateurs.append(
                IndicateurSignal(
                    ref="concurrence.concurrents.part_offres_sans_marque",
                    libelle="Part des offres portées par des vendeurs sans marque (%)",
                    valeur=f"{part:.1f}",
                    detail=(
                        f"{offres_sans_marque} offre(s) sans marque sur {total_offres} "
                        f"offres rattachées à un concurrent du corpus"
                    ),
                )
            )
        indicateurs.append(
            IndicateurSignal(
                ref="concurrence.concurrents.nb_marques_etablies",
                libelle="Marques établies identifiées",
                valeur=str(nb_marques),
                detail="comptage par type de concurrent consolidé par l'agent amont",
            )
        )

    if concurrence is not None and concurrence.referentiel_stats is not None:
        stats = concurrence.referentiel_stats
        if stats.nb_offres_par_source:
            indicateurs.append(
                IndicateurSignal(
                    ref="concurrence.referentiel_stats.nb_offres_par_source",
                    libelle="Offres collectées par source",
                    valeur="; ".join(
                        f"{source_offre} : {nb}"
                        for source_offre, nb in sorted(stats.nb_offres_par_source.items())
                    ),
                    detail=DETAIL_CORPUS,
                )
            )

    if not indicateurs:
        return _famille_vide(
            FAMILLE_OFFRE,
            [
                "aucune donnée de structure d'offre : ni sortie F4, ni écho F5 "
                "exploitable"
            ],
        )

    avertissements.append(DETAIL_CORPUS)
    return FamilleSignaux(
        famille=FAMILLE_OFFRE,
        intitule="Structure et saturation de l'offre",
        disponible=True,
        source_effective=source,
        indicateurs=indicateurs,
        avertissements=avertissements,
    )


def _construire_corpus(
    insights: EntreeInsights | None, horodatage_run: str | None
) -> FamilleSignaux:
    """Extrait la récence et la densité du corpus d'avis.

    Args:
        insights: Sortie F3, ou `None`.
        horodatage_run: Horodatage de référence du run amont.

    Returns:
        La famille « corpus_avis ».
    """
    if insights is None or insights.stats_corpus is None:
        return _famille_vide(
            FAMILLE_CORPUS,
            [
                "sortie F3 non fournie : la récence et la densité du corpus d'avis "
                "ne peuvent pas être qualifiées"
            ],
        )

    stats = insights.stats_corpus
    indicateurs: list[IndicateurSignal] = []
    periode = stats.periode_couverte
    reference = None
    if horodatage_run:
        try:
            reference = datetime.fromisoformat(horodatage_run.replace("Z", "+00:00"))
        except ValueError:
            reference = None

    if periode is not None and (periode.min or periode.max):
        indicateurs.append(
            IndicateurSignal(
                ref="insights.stats_corpus.periode_couverte",
                libelle="Période couverte par le corpus d'avis",
                valeur=f"{periode.min or 'inconnue'} → {periode.max or 'inconnue'}",
                detail="dates de publication des contenus collectés",
            )
        )
        anciennete_min = age_en_jours(periode.min, reference)
        anciennete_max = age_en_jours(periode.max, reference)
        if anciennete_min is not None:
            indicateurs.append(
                IndicateurSignal(
                    ref="insights.stats_corpus.anciennete_corpus_jours",
                    libelle="Ancienneté du contenu le plus ancien (jours)",
                    valeur=str(anciennete_min),
                    detail="mesurée par rapport à la date du run amont",
                )
            )
        if anciennete_max is not None:
            indicateurs.append(
                IndicateurSignal(
                    ref="insights.stats_corpus.recence_corpus_jours",
                    libelle="Ancienneté du contenu le plus récent (jours)",
                    valeur=str(anciennete_max),
                    detail="mesurée par rapport à la date du run amont",
                )
            )

    if stats.nb_unites_par_source:
        indicateurs.append(
            IndicateurSignal(
                ref="insights.stats_corpus.nb_unites_par_source",
                libelle="Contributions analysées par source",
                valeur="; ".join(
                    f"{source} : {nb}"
                    for source, nb in sorted(stats.nb_unites_par_source.items())
                ),
                detail=DETAIL_CORPUS,
            )
        )
    if stats.nb_unites_analysees:
        indicateurs.append(
            IndicateurSignal(
                ref="insights.stats_corpus.nb_unites_analysees",
                libelle="Contributions analysées au total",
                valeur=str(stats.nb_unites_analysees),
                detail=DETAIL_CORPUS,
            )
        )

    if not indicateurs:
        return _famille_vide(
            FAMILLE_CORPUS,
            ["la sortie F3 ne porte aucune statistique de corpus exploitable"],
        )

    return FamilleSignaux(
        famille=FAMILLE_CORPUS,
        intitule="Récence et densité du corpus d'avis",
        disponible=True,
        source_effective=SOURCE_INSIGHTS,
        indicateurs=indicateurs,
        avertissements=[DETAIL_CORPUS],
    )


def construire_dossier(entrees: EntreesChargees) -> DossierPLC:
    """Construit le dossier PLC complet depuis les entrées chargées.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le dossier PLC, toutes familles présentes — disponibles ou non.

    Raises:
        ValueError: Si la sortie F5 est absente, ce qui est impossible en aval
            du contrôle de `main.py`.
    """
    recommandations = entrees.recommandations
    if recommandations is None:
        raise ValueError("le dossier PLC exige la sortie F5")

    familles = [
        _construire_demande(recommandations),
        _construire_publicite(entrees.concurrence, recommandations),
        _construire_offre(entrees.concurrence, recommandations),
        _construire_corpus(entrees.insights, recommandations.horodatage_utc),
    ]

    confiances: dict[str, str | None] = {
        ENTREE_RECOMMANDATIONS: (
            recommandations.confiance_globale.niveau
            if recommandations.confiance_globale
            else None
        ),
        ENTREE_CONCURRENCE: (
            entrees.concurrence.confiance_globale.niveau
            if entrees.concurrence and entrees.concurrence.confiance_globale
            else None
        ),
        ENTREE_INSIGHTS: (
            entrees.insights.confiance_globale.niveau
            if entrees.insights and entrees.insights.confiance_globale
            else None
        ),
    }

    dossier = DossierPLC(
        familles=familles,
        verdict_amont=recommandations.verdict_potentiel.verdict,
        confiances_amont=confiances,
    )
    logger.debug(
        "dossier PLC : %d famille(s) disponible(s) sur %d",
        len(dossier.familles_disponibles()),
        len(familles),
    )
    return dossier


def limites_du_dossier(dossier: DossierPLC) -> list[str]:
    """Dérive les limites imposées par l'état des familles.

    Args:
        dossier: Dossier PLC construit.

    Returns:
        Les limites à publier, éventuellement vides.
    """
    limites: list[str] = []
    for famille in dossier.familles:
        if famille.disponible:
            continue
        for avertissement in famille.avertissements:
            limites.append(f"[{famille.famille}] {avertissement}")
    return limites
