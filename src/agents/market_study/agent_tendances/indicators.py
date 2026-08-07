"""Calcul déterministe des indicateurs de tendance.

Aucun appel LLM dans ce module. Toutes les fonctions sont pures et tolérantes
aux données manquantes : elles retournent `None` (ou une valeur neutre) plutôt
que de lever une exception.

Structure réelle d'un item renvoyé par l'actor en mode `keyword` (constatée par
run réel, voir README) ::

    {
      "keyword": "écouteurs open ear",
      "timeframe": "today 12-m",
      "geo": "FR",
      "language": "fr-FR",
      "trends_url": "https://trends.google.com/...",
      "timeline_data": {
        "écouteurs open ear": {"2025-07-27": 0, ...},
        "isPartial":          {"2025-07-27": false, ...}
      },
      "region_data": [{"rank": 1, "region": "Île-de-France", "value": 100}],
      "data_granularity": "week"
    }
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    CLE_TIMELINE_PARTIELLE,
    FENETRE_MOMENTUM_JOURS,
    LIBELLE_BREAKOUT,
    MIN_JOURS_MOMENTUM,
    MIN_MOIS_REGRESSION,
    MIN_POINTS_SERIE,
    NB_ZONES_GEO,
    PROFIL_CROISSANCE,
    PROFIL_DECLIN,
    PROFIL_EFFET_DE_MODE,
    PROFIL_EMERGENT,
    PROFIL_INDETERMINE,
    PROFIL_MATURITE,
    RATIO_EFFONDREMENT_MODE,
    SEUIL_ANCIENNETE_PIC_MOIS,
    SEUIL_BREAKOUT_PCT,
    SEUIL_INDICE_MOYEN_ELEVE,
    SEUIL_INDICE_MOYEN_FAIBLE,
    SEUIL_MOMENTUM_EMERGENT,
    SEUIL_PENTE_NEGATIVE,
    SEUIL_PENTE_NEUTRE,
    SEUIL_PENTE_POSITIVE,
    SEUIL_VOLATILITE_ELEVEE,
    obtenir_logger,
)
from schemas import IndicateursTendance, RequeteEmergente, Saisonnalite

_LOG = obtenir_logger(__name__)

_JOURS_PAR_MOIS = 30.44
_MOIS_PAR_AN = 12


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def extraire_serie(item: dict | None, exclure_points_partiels: bool = True) -> pd.Series | None:
    """Extrait la série temporelle d'un item de dataset.

    Args:
        item: Item brut renvoyé par l'actor, ou `None`.
        exclure_points_partiels: Si vrai, écarte les points marqués `isPartial`
            (dernière période en cours, systématiquement sous-estimée).

    Returns:
        Une série indexée par date, triée chronologiquement, ou `None` si
        aucune série exploitable n'est présente.
    """
    if not isinstance(item, dict):
        return None
    timeline = item.get("timeline_data")
    if not isinstance(timeline, dict):
        return None

    brute: dict | None = None
    partiels: dict = {}
    for cle, valeur in timeline.items():
        if cle == CLE_TIMELINE_PARTIELLE:
            partiels = valeur if isinstance(valeur, dict) else {}
        elif isinstance(valeur, dict) and brute is None:
            brute = valeur
    if not brute:
        return None

    dates: list[pd.Timestamp] = []
    valeurs: list[float] = []
    for cle, valeur in brute.items():
        if exclure_points_partiels and bool(partiels.get(cle, False)):
            continue
        horodatage = pd.to_datetime(cle, errors="coerce")
        if pd.isna(horodatage) or not isinstance(valeur, (int, float, bool)):
            continue
        dates.append(horodatage)
        valeurs.append(float(valeur))

    if not dates:
        return None
    serie = pd.Series(valeurs, index=pd.DatetimeIndex(dates)).sort_index()
    return serie[~serie.index.duplicated(keep="last")]


def compter_points(item: dict | None) -> int:
    """Compte les points de la série contenue dans un item.

    Args:
        item: Item brut renvoyé par l'actor, ou `None`.

    Returns:
        Le nombre de points exploitables, `0` si aucun.
    """
    serie = extraire_serie(item)
    return 0 if serie is None else int(serie.size)


def _agreger_par_mois(serie: pd.Series) -> pd.Series:
    """Agrège une série hebdomadaire en moyenne mensuelle.

    Args:
        serie: Série indexée par date.

    Returns:
        La série des moyennes mensuelles, valeurs manquantes écartées.
    """
    return serie.resample("MS").mean().dropna()


# --------------------------------------------------------------------------- #
# Indicateurs élémentaires
# --------------------------------------------------------------------------- #


def profil_mensuel(serie: pd.Series | None) -> dict[str, float]:
    """Agrège une série en moyennes mensuelles datées, par ordre chronologique.

    À la différence de `calculer_saisonnalite`, qui regroupe les mois
    *calendaires* de plusieurs années, cette fonction conserve l'année : elle
    décrit l'évolution réelle de la fenêtre interrogée, mois par mois.

    Les mois situés aux deux extrémités de la fenêtre sont souvent incomplets
    (la série hebdomadaire ne commence ni ne finit sur un début de mois) : leur
    moyenne porte alors sur moins de semaines que les autres.

    Args:
        serie: Série indexée par date, ou `None`.

    Returns:
        Un dictionnaire `{"AAAA-MM": indice_moyen}` trié chronologiquement,
        vide si la série est absente.
    """
    if serie is None or serie.empty:
        return {}
    mensuelle = _agreger_par_mois(serie)
    return {
        horodatage.strftime("%Y-%m"): float(valeur)
        for horodatage, valeur in mensuelle.items()
    }


def indice_moyen(serie: pd.Series | None) -> float | None:
    """Calcule la moyenne arithmétique d'une série.

    Args:
        serie: Série d'indices, ou `None`.

    Returns:
        La moyenne, ou `None` si la série est absente ou vide.
    """
    if serie is None or serie.empty:
        return None
    return float(serie.mean())


def momentum(serie: pd.Series | None) -> float | None:
    """Calcule la variation relative entre les deux dernières fenêtres de 90 jours.

    Args:
        serie: Série d'indices sur 12 mois, ou `None`.

    Returns:
        La variation relative (`0.23` = +23 %), ou `None` si l'historique
        couvre moins de `MIN_JOURS_MOMENTUM` jours ou si la fenêtre de
        référence est nulle.
    """
    if serie is None or serie.empty:
        return None
    fin = serie.index.max()
    if (fin - serie.index.min()).days < MIN_JOURS_MOMENTUM:
        return None

    debut_recent = fin - pd.Timedelta(days=FENETRE_MOMENTUM_JOURS)
    debut_precedent = fin - pd.Timedelta(days=2 * FENETRE_MOMENTUM_JOURS)
    recent = serie[serie.index > debut_recent]
    precedent = serie[(serie.index > debut_precedent) & (serie.index <= debut_recent)]
    if recent.empty or precedent.empty:
        return None

    reference = float(precedent.mean())
    if reference == 0.0:
        return None
    return float(recent.mean()) / reference - 1.0


def pente_annuelle(serie: pd.Series | None) -> float | None:
    """Ajuste une régression linéaire de degré 1 sur la série mensuelle.

    Args:
        serie: Série d'indices sur 5 ans, ou `None`.

    Returns:
        La pente en points d'indice par an, ou `None` si l'historique mensuel
        est trop court.
    """
    if serie is None or serie.empty:
        return None
    mensuelle = _agreger_par_mois(serie)
    if mensuelle.size < MIN_MOIS_REGRESSION:
        return None
    abscisses = np.arange(mensuelle.size, dtype=float)
    pente_mensuelle = float(np.polyfit(abscisses, mensuelle.to_numpy(dtype=float), 1)[0])
    return pente_mensuelle * _MOIS_PAR_AN


def volatilite(serie: pd.Series | None) -> float | None:
    """Calcule le coefficient de variation d'une série.

    Args:
        serie: Série d'indices sur 5 ans, ou `None`.

    Returns:
        `écart-type / moyenne`, ou `None` si la série est trop courte ou de
        moyenne nulle.
    """
    if serie is None or serie.size < MIN_POINTS_SERIE:
        return None
    moyenne = float(serie.mean())
    if moyenne == 0.0:
        return None
    return float(serie.std(ddof=1)) / moyenne


def calculer_saisonnalite(serie: pd.Series | None) -> Saisonnalite | None:
    """Calcule le profil saisonnier moyen par mois calendaire.

    Args:
        serie: Série d'indices sur 5 ans, ou `None`.

    Returns:
        Le profil saisonnier, ou `None` si les 12 mois calendaires ne sont pas
        tous couverts.
    """
    if serie is None or serie.empty:
        return None
    mensuelle = _agreger_par_mois(serie)
    if mensuelle.empty:
        return None

    par_mois = mensuelle.groupby(mensuelle.index.month).mean()
    if par_mois.size < _MOIS_PAR_AN:
        return None

    indices = {int(mois): float(valeur) for mois, valeur in par_mois.items()}
    moyenne = float(par_mois.mean())
    amplitude = (
        (float(par_mois.max()) - float(par_mois.min())) / moyenne if moyenne else None
    )
    return Saisonnalite(
        indice_par_mois=indices,
        mois_pic=int(par_mois.idxmax()),
        mois_creux=int(par_mois.idxmin()),
        amplitude=amplitude,
    )


def compter_breakout(requetes: list[RequeteEmergente]) -> int:
    """Compte les requêtes en progression qualifiées de *breakout*.

    Args:
        requetes: Requêtes émergentes remontées par la source.

    Returns:
        Le nombre de requêtes marquées « Breakout » ou dont la variation
        atteint `SEUIL_BREAKOUT_PCT`.
    """
    total = 0
    for requete in requetes:
        if requete.est_breakout:
            total += 1
            continue
        brut = requete.variation.strip().lower().replace("%", "").replace(" ", "")
        if brut == LIBELLE_BREAKOUT:
            total += 1
            continue
        try:
            if float(brut.replace(",", "").replace("+", "")) >= SEUIL_BREAKOUT_PCT:
                total += 1
        except ValueError:
            continue
    return total


def concentration_geo(item: dict | None) -> list[dict]:
    """Calcule la part relative des principales zones géographiques.

    Args:
        item: Item brut portant `region_data`, ou `None`.

    Returns:
        Les `NB_ZONES_GEO` premières zones sous la forme
        `[{"zone": str, "part": float}]`, liste vide si l'information est absente.
    """
    if not isinstance(item, dict):
        return []
    regions = item.get("region_data")
    if not isinstance(regions, list) or not regions:
        return []

    couples: list[tuple[str, float]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        zone = region.get("region")
        valeur = region.get("value")
        if isinstance(zone, str) and isinstance(valeur, (int, float)):
            couples.append((zone, float(valeur)))

    total = sum(valeur for _, valeur in couples)
    if not couples or total <= 0:
        return []
    couples.sort(key=lambda couple: couple[1], reverse=True)
    return [
        {"zone": zone, "part": valeur / total} for zone, valeur in couples[:NB_ZONES_GEO]
    ]


def detecter_effet_de_mode(serie: pd.Series | None) -> bool:
    """Détecte un profil d'effet de mode sur la série longue.

    Le signal est levé si le pic historique remonte à plus de
    `SEUIL_ANCIENNETE_PIC_MOIS` mois **et** si l'indice actuel (moyenne des
    `FENETRE_MOMENTUM_JOURS` derniers jours) est inférieur à
    `RATIO_EFFONDREMENT_MODE` fois la valeur du pic.

    Args:
        serie: Série d'indices sur 5 ans, ou `None`.

    Returns:
        `True` si le signal est levé, `False` sinon ou si la série est absente.
    """
    if serie is None or serie.size < MIN_POINTS_SERIE:
        return False

    valeur_pic = float(serie.max())
    if valeur_pic <= 0:
        return False

    date_pic = serie.idxmax()
    fin = serie.index.max()
    anciennete_mois = (fin - date_pic).days / _JOURS_PAR_MOIS
    if anciennete_mois <= SEUIL_ANCIENNETE_PIC_MOIS:
        return False

    recent = serie[serie.index > fin - pd.Timedelta(days=FENETRE_MOMENTUM_JOURS)]
    if recent.empty:
        return False
    return float(recent.mean()) < RATIO_EFFONDREMENT_MODE * valeur_pic


def classifier_profil(
    indice_moyen_12m: float | None,
    momentum_90j: float | None,
    pente_annuelle_5ans: float | None,
    volatilite_5ans: float | None,
    signal_effet_de_mode: bool,
) -> str:
    """Classe la courbe selon des règles déterministes.

    ⚠️ Classification heuristique : les seuils utilisés ne sont pas validés
    empiriquement.

    Args:
        indice_moyen_12m: Indice moyen sur 12 mois.
        momentum_90j: Variation relative entre les deux dernières fenêtres de 90 jours.
        pente_annuelle_5ans: Pente en points d'indice par an.
        volatilite_5ans: Coefficient de variation sur 5 ans.
        signal_effet_de_mode: Résultat de `detecter_effet_de_mode`.

    Returns:
        Une valeur parmi `effet_de_mode`, `emergent`, `croissance`, `maturite`,
        `declin`, `indetermine`.
    """
    if signal_effet_de_mode:
        return PROFIL_EFFET_DE_MODE

    if (
        indice_moyen_12m is not None
        and momentum_90j is not None
        and volatilite_5ans is not None
        and indice_moyen_12m < SEUIL_INDICE_MOYEN_FAIBLE
        and momentum_90j > SEUIL_MOMENTUM_EMERGENT
        and volatilite_5ans > SEUIL_VOLATILITE_ELEVEE
    ):
        return PROFIL_EMERGENT

    if pente_annuelle_5ans is None:
        return PROFIL_INDETERMINE

    if pente_annuelle_5ans > SEUIL_PENTE_POSITIVE:
        return PROFIL_CROISSANCE

    if (
        abs(pente_annuelle_5ans) <= SEUIL_PENTE_NEUTRE
        and indice_moyen_12m is not None
        and indice_moyen_12m >= SEUIL_INDICE_MOYEN_ELEVE
    ):
        return PROFIL_MATURITE

    if pente_annuelle_5ans < SEUIL_PENTE_NEGATIVE:
        return PROFIL_DECLIN

    return PROFIL_INDETERMINE


# --------------------------------------------------------------------------- #
# Agrégation
# --------------------------------------------------------------------------- #


def construire_indicateurs(
    item_12m: dict | None,
    item_5ans: dict | None,
    requetes_emergentes: list[RequeteEmergente] | None = None,
) -> tuple[IndicateursTendance | None, list[str]]:
    """Assemble tous les indicateurs à partir des données disponibles.

    Args:
        item_12m: Item brut de l'horizon 12 mois, ou `None` si la collecte a échoué.
        item_5ans: Item brut de l'horizon 5 ans, ou `None` si la collecte a échoué.
        requetes_emergentes: Requêtes en progression remontées par la source.

    Returns:
        Un couple `(indicateurs, indicateurs_manquants)`. `indicateurs` vaut
        `None` si aucune série n'est exploitable ; `indicateurs_manquants`
        énumère les indicateurs non calculables.
    """
    serie_12m = extraire_serie(item_12m)
    serie_5ans = extraire_serie(item_5ans)
    if serie_12m is None and serie_5ans is None:
        return None, []

    moyenne_12m = indice_moyen(serie_12m)
    mensuel_12m = profil_mensuel(serie_12m)
    momentum_90j = momentum(serie_12m)
    pente = pente_annuelle(serie_5ans)
    coefficient_variation = volatilite(serie_5ans)
    saisonnalite = calculer_saisonnalite(serie_5ans)
    zones = concentration_geo(item_12m)
    effet_de_mode = detecter_effet_de_mode(serie_5ans)
    requetes = requetes_emergentes or []

    manquants: list[str] = []
    for nom, valeur in (
        ("indice_moyen_12m", moyenne_12m),
        ("momentum_90j", momentum_90j),
        ("pente_annuelle_5ans", pente),
        ("volatilite", coefficient_variation),
        ("saisonnalite", saisonnalite),
    ):
        if valeur is None:
            manquants.append(nom)
    if not zones:
        manquants.append("concentration_geo")

    indicateurs = IndicateursTendance(
        indice_moyen_12m=moyenne_12m,
        profil_mensuel_12m=mensuel_12m,
        momentum_90j=momentum_90j,
        pente_annuelle_5ans=pente,
        volatilite=coefficient_variation,
        saisonnalite=saisonnalite,
        nb_breakout=compter_breakout(requetes),
        concentration_geo=zones,
        signal_effet_de_mode=effet_de_mode,
        profil_courbe=classifier_profil(
            moyenne_12m, momentum_90j, pente, coefficient_variation, effet_de_mode
        ),
    )
    _LOG.info(
        "Indicateurs calculés : profil=%s, indice_moyen_12m=%s, pente_5ans=%s",
        indicateurs.profil_courbe,
        moyenne_12m,
        pente,
    )
    return indicateurs, manquants
