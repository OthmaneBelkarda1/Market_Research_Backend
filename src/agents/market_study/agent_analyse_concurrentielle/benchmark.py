"""Calculs chiffrés : benchmark de prix, volumes, concentration, intensité.

**Aucun appel LLM dans ce module.** Tous les nombres publiés par l'agent en
proviennent, et la post-validation les réécrit dans la sortie finale.

Deux interdits absolus y sont matérialisés :

- **aucune conversion de devise** — un montant n'est jamais transformé ;
- **aucune agrégation inter-devises** — un benchmark est toujours calculé pour
  un couple (source, devise), jamais au-delà.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from config import (
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    MIN_PRIX_POUR_SEGMENTS,
    PART_TOP3_CONCENTRATION,
    SEGMENT_COEUR,
    SEGMENT_ENTREE,
    SEGMENT_PREMIUM,
    SEUIL_MIN_OFFRES_FIABLE,
    logger,
)
from schemas import (
    BenchmarkSource,
    ConcurrentConsolide,
    IntensiteConcurrentielle,
    OffreConcurrente,
    PositionPrixEnvisage,
    Referentiel,
    SegmentPrix,
    SortieBenchmark,
    StatsConcurrent,
)


def _dispersion(prix: list[float], mediane: float) -> float:
    """Calcule la dispersion relative d'une série de prix.

    Args:
        prix: Prix triés ou non, au moins un élément.
        mediane: Médiane de la série.

    Returns:
        L'écart interquartile rapporté à la médiane si la série le permet,
        l'étendue rapportée à la médiane sinon ; 0 si la médiane est nulle.
    """
    if not mediane:
        return 0.0
    if len(prix) >= MIN_PRIX_POUR_SEGMENTS:
        quartiles = statistics.quantiles(sorted(prix), n=4, method="inclusive")
        return round((quartiles[2] - quartiles[0]) / mediane, 3)
    return round((max(prix) - min(prix)) / mediane, 3)


def _segments(prix: list[float]) -> list[SegmentPrix]:
    """Découpe une série de prix en terciles.

    Args:
        prix: Prix de la source et de la devise considérées.

    Returns:
        Trois segments, ou une liste vide si la série est trop courte.
    """
    if len(prix) < MIN_PRIX_POUR_SEGMENTS:
        return []
    tries = sorted(prix)
    bornes = statistics.quantiles(tries, n=3, method="inclusive")
    limites = [
        (SEGMENT_ENTREE, tries[0], bornes[0]),
        (SEGMENT_COEUR, bornes[0], bornes[1]),
        (SEGMENT_PREMIUM, bornes[1], tries[-1]),
    ]
    segments: list[SegmentPrix] = []
    for index, (nom, basse, haute) in enumerate(limites):
        if index == 0:
            compte = sum(1 for p in tries if p <= haute)
        elif index == 1:
            compte = sum(1 for p in tries if basse < p <= haute)
        else:
            compte = sum(1 for p in tries if p > basse)
        segments.append(
            SegmentPrix(
                nom=nom,
                borne_basse=round(basse, 2),
                borne_haute=round(haute, 2),
                nb_offres=compte,
            )
        )
    return segments


def _segment_du_prix(prix: float, segments: list[SegmentPrix]) -> str | None:
    """Situe un prix dans un découpage en segments.

    Args:
        prix: Prix à situer.
        segments: Segments de la source et de la devise.

    Returns:
        Le nom du segment, ou `None` si aucun découpage n'existe.
    """
    for segment in segments:
        if segment.borne_basse <= prix <= segment.borne_haute:
            return segment.nom
    if segments:
        return SEGMENT_ENTREE if prix < segments[0].borne_basse else SEGMENT_PREMIUM
    return None


def calculer_benchmarks(referentiel: Referentiel) -> list[BenchmarkSource]:
    """Calcule un benchmark par couple (source, devise).

    Args:
        referentiel: Référentiel des offres.

    Returns:
        Les benchmarks, triés par source puis devise.
    """
    groupes: dict[tuple[str, str], list[float]] = defaultdict(list)
    for offre in referentiel.offres:
        if offre.est_accessoire or offre.prix is None or not offre.devise:
            continue
        groupes[(offre.source, offre.devise)].append(offre.prix)

    benchmarks: list[BenchmarkSource] = []
    for (source, devise), prix in sorted(groupes.items()):
        if not prix:
            continue
        mediane = round(statistics.median(prix), 2)
        segments = _segments(prix)
        commentaire = (
            ""
            if segments
            else (
                f"{len(prix)} prix disponibles, sous le minimum de "
                f"{MIN_PRIX_POUR_SEGMENTS} requis pour un découpage en terciles : "
                f"seule la fourchette est publiée."
            )
        )
        benchmarks.append(
            BenchmarkSource(
                source=source,
                devise=devise,
                nb_offres_avec_prix=len(prix),
                prix_min=round(min(prix), 2),
                prix_mediane=mediane,
                prix_max=round(max(prix), 2),
                dispersion=_dispersion(prix, mediane),
                segments=segments,
                commentaire=commentaire,
            )
        )
    return benchmarks


def situer_prix_envisage(
    benchmarks: list[BenchmarkSource],
    referentiel: Referentiel,
    prix_envisage: float | None,
    devise_envisagee: str | None,
) -> PositionPrixEnvisage | None:
    """Situe un prix envisagé dans le benchmark de même devise.

    Aucune conversion n'est tentée : si aucune source ne publie de prix dans la
    devise demandée, la comparaison est déclarée impossible.

    Args:
        benchmarks: Benchmarks calculés.
        referentiel: Référentiel, pour le calcul du percentile.
        prix_envisage: Prix envisagé, ou `None`.
        devise_envisagee: Devise du prix envisagé, ou `None`.

    Returns:
        La position, ou `None` si aucun prix n'a été fourni.
    """
    if prix_envisage is None or not devise_envisagee:
        return None

    candidats = [b for b in benchmarks if b.devise == devise_envisagee]
    if not candidats:
        devises = sorted({b.devise for b in benchmarks})
        return PositionPrixEnvisage(
            prix=prix_envisage,
            devise=devise_envisagee,
            commentaire=(
                f"comparaison impossible (devise) : aucun benchmark n'est libellé en "
                f"{devise_envisagee}"
                + (f" — devises disponibles : {', '.join(devises)}." if devises else ".")
                + " Aucune conversion n'est effectuée, par construction."
            ),
        )

    reference = max(candidats, key=lambda b: b.nb_offres_avec_prix)
    prix_source = [
        o.prix
        for o in referentiel.offres
        if o.source == reference.source
        and o.devise == reference.devise
        and o.prix is not None
        and not o.est_accessoire
    ]
    percentile = (
        round(100 * sum(1 for p in prix_source if p <= prix_envisage) / len(prix_source), 1)
        if prix_source
        else None
    )
    ecart = (
        round(100 * (prix_envisage - reference.prix_mediane) / reference.prix_mediane, 1)
        if reference.prix_mediane
        else None
    )
    autres = [
        f"{b.source} (médiane {b.prix_mediane}, {b.nb_offres_avec_prix} offres)"
        for b in candidats
        if b.source != reference.source
    ]
    commentaire = (
        f"situé face au benchmark {reference.source} en {reference.devise} "
        f"({reference.nb_offres_avec_prix} offres), retenu parce qu'il porte le plus "
        f"grand nombre de prix dans cette devise. Cette comparaison ne vaut que pour "
        f"la portée régionale de cette source."
    )
    if autres:
        commentaire += (
            f" D'autres sources publient des prix dans la même devise sur des plans "
            f"de commercialisation différents : {', '.join(autres)}. Les comparer au "
            f"même prix envisagé n'a de sens qu'à canal comparable — un prix de "
            f"marketplace de détail et un prix de grossiste ne se lisent pas ensemble."
        )
    return PositionPrixEnvisage(
        prix=prix_envisage,
        devise=devise_envisagee,
        source_comparable=reference.source,
        percentile=percentile,
        segment=_segment_du_prix(prix_envisage, reference.segments),
        ecart_mediane_pct=ecart,
        commentaire=commentaire,
    )


def _fourchette(prix: list[float]) -> str:
    """Formate une fourchette de prix.

    Args:
        prix: Prix d'une même devise.

    Returns:
        « min–max » ou la valeur unique, arrondies au centième.
    """
    bas, haut = min(prix), max(prix)
    if bas == haut:
        return f"{bas:.2f}"
    return f"{bas:.2f}–{haut:.2f}"


def calculer_stats_concurrents(
    referentiel: Referentiel, concurrents: list[ConcurrentConsolide]
) -> dict[str, StatsConcurrent]:
    """Calcule les statistiques de chaque concurrent consolidé.

    Args:
        referentiel: Référentiel complet.
        concurrents: Concurrents consolidés.

    Returns:
        Le dictionnaire `nom_canonique → statistiques`.
    """
    index_offres = {o.id_offre: o for o in referentiel.offres}
    index_annonces = {a.id_annonce: a for a in referentiel.annonces}
    stats: dict[str, StatsConcurrent] = {}

    for concurrent in concurrents:
        offres = [index_offres[i] for i in concurrent.ids_offres if i in index_offres]
        annonces = [
            index_annonces[i] for i in concurrent.ids_annonces if i in index_annonces
        ]
        prix_par_devise: dict[str, list[float]] = defaultdict(list)
        for offre in offres:
            if offre.prix is not None and offre.devise:
                prix_par_devise[offre.devise].append(offre.prix)
            for prix_sku in offre.prix_skus:
                if offre.devise:
                    prix_par_devise[offre.devise].append(prix_sku)

        notes = [o.note for o in offres if o.note is not None]
        volumes = [o.volume_ventes for o in offres if o.volume_ventes is not None]
        longevites = [
            a.duree_diffusion_jours for a in annonces if a.duree_diffusion_jours is not None
        ]

        stats[concurrent.nom_canonique] = StatsConcurrent(
            fourchette_prix_par_devise={
                devise: _fourchette(valeurs) for devise, valeurs in sorted(prix_par_devise.items())
            },
            prix_min_par_devise={
                devise: round(min(v), 2) for devise, v in sorted(prix_par_devise.items())
            },
            prix_max_par_devise={
                devise: round(max(v), 2) for devise, v in sorted(prix_par_devise.items())
            },
            note_moyenne=round(sum(notes) / len(notes), 2) if notes else None,
            nb_offres=len(offres),
            volume_ventes_cumule=sum(volumes) if volumes else None,
            nb_annonces=len(annonces),
            nb_annonces_actives=sum(1 for a in annonces if a.active),
            longevite_max_jours=max(longevites) if longevites else None,
            nb_pages_mentionnant=len(concurrent.ids_pages),
        )
    return stats


def calculer_intensite(
    referentiel: Referentiel,
    concurrents: list[ConcurrentConsolide],
    stats_concurrents: dict[str, StatsConcurrent],
) -> IntensiteConcurrentielle:
    """Calcule les indicateurs d'intensité concurrentielle et publicitaire.

    Le champ `lecture` est laissé vide : sa rédaction relève de `analyse.py`.

    Args:
        referentiel: Référentiel complet.
        concurrents: Concurrents consolidés.
        stats_concurrents: Statistiques par concurrent.

    Returns:
        Les indicateurs chiffrés.
    """
    durees = [
        a.duree_diffusion_jours
        for a in referentiel.annonces
        if a.duree_diffusion_jours is not None
    ]
    plateformes: dict[str, int] = defaultdict(int)
    for annonce in referentiel.annonces:
        for plateforme in annonce.plateformes:
            plateformes[plateforme] += 1
    dominantes = [
        nom for nom, _ in sorted(plateformes.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    ]

    volumes = sorted(
        (s.volume_ventes_cumule or 0 for s in stats_concurrents.values()), reverse=True
    )
    total_volume = sum(volumes)
    concentration = (
        round(100 * sum(volumes[:PART_TOP3_CONCENTRATION]) / total_volume, 1)
        if total_volume
        else None
    )

    nb_offres_coeur = referentiel.stats.nb_offres_coeur
    if nb_offres_coeur >= 2 * SEUIL_MIN_OFFRES_FIABLE and len(referentiel.annonces) >= 5:
        confiance = CONFIANCE_ELEVEE
    elif nb_offres_coeur >= SEUIL_MIN_OFFRES_FIABLE:
        confiance = CONFIANCE_MOYENNE
    else:
        confiance = CONFIANCE_FAIBLE

    return IntensiteConcurrentielle(
        nb_concurrents_identifies=len(concurrents),
        nb_offres_coeur=nb_offres_coeur,
        nb_annonceurs=len({a.annonceur for a in referentiel.annonces}),
        nb_annonces_actives=sum(1 for a in referentiel.annonces if a.active),
        duree_diffusion_mediane_jours=round(statistics.median(durees), 1) if durees else None,
        duree_diffusion_max_jours=max(durees) if durees else None,
        plateformes_dominantes=dominantes,
        concentration_volumes_top3_pct=concentration,
        lecture="",
        confiance=confiance,
    )


def segments_par_offre(
    referentiel: Referentiel, benchmarks: list[BenchmarkSource]
) -> dict[str, str]:
    """Situe chaque offre dans le segment de prix de sa source et de sa devise.

    Args:
        referentiel: Référentiel complet.
        benchmarks: Benchmarks calculés.

    Returns:
        Le dictionnaire `id_offre → nom de segment`.
    """
    index = {(b.source, b.devise): b for b in benchmarks}
    resultat: dict[str, str] = {}
    for offre in referentiel.offres:
        if offre.prix is None or not offre.devise:
            continue
        reference = index.get((offre.source, offre.devise))
        if reference is None or not reference.segments:
            continue
        segment = _segment_du_prix(offre.prix, reference.segments)
        if segment:
            resultat[offre.id_offre] = segment
    return resultat


def calculer(
    referentiel: Referentiel,
    concurrents: list[ConcurrentConsolide],
    prix_envisage: float | None,
    devise_envisagee: str | None,
) -> SortieBenchmark:
    """Produit l'ensemble des résultats chiffrés de l'analyse.

    Args:
        referentiel: Référentiel complet.
        concurrents: Concurrents consolidés.
        prix_envisage: Prix envisagé pour le produit étudié, ou `None`.
        devise_envisagee: Devise du prix envisagé, ou `None`.

    Returns:
        L'ensemble des résultats chiffrés.
    """
    benchmarks = calculer_benchmarks(referentiel)
    stats = calculer_stats_concurrents(referentiel, concurrents)
    sortie = SortieBenchmark(
        benchmarks=benchmarks,
        position_prix=situer_prix_envisage(
            benchmarks, referentiel, prix_envisage, devise_envisagee
        ),
        intensite=calculer_intensite(referentiel, concurrents, stats),
        stats_par_concurrent=stats,
        segment_par_offre=segments_par_offre(referentiel, benchmarks),
    )
    logger.debug(
        "benchmark : %d couple(s) (source, devise) ; %d concurrents chiffrés",
        len(benchmarks),
        len(stats),
    )
    return sortie
