"""Orchestration de bout en bout de l'analyse de tendances.

Exécution **strictement séquentielle** : deux requêtes simultanées depuis le
même pool de proxies déclenchent la détection anti-bot de Google. Aucun
`asyncio.gather`, aucun `ThreadPoolExecutor`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import (
    LIMITE_LANGUE_NON_PARAMETRABLE,
    LIMITE_REQUETES_EMERGENTES,
    LIMITE_SERIE_HEBDOMADAIRE,
    LIMITES_METHODOLOGIQUES,
    NB_REPLIS_MAX,
    PAUSE_ENTRE_APPELS_SECS,
    SEUIL_INDICE_BRUIT,
    TIMEFRAME_5ANS,
    TIMEFRAME_12M,
    obtenir_logger,
)
from indicators import construire_indicateurs, extraire_serie, indice_moyen
from keywords import controler_fiche_produit, deriver_mots_cles
from schemas import (
    FicheProduit,
    JeuMotsCles,
    ParametresMarche,
    ResultatTendances,
    StatutCollecte,
)
from trends_source import collecter_tendances

_LOG = obtenir_logger(__name__)


@dataclass
class _Tentative:
    """Résultat d'une passe de collecte pour un terme donné."""

    terme: str
    niveau: int
    item_12m: dict | None = None
    item_5ans: dict | None = None
    statuts: list[StatutCollecte] = field(default_factory=list)
    indice_moyen_12m: float | None = None

    @property
    def exploitable(self) -> bool:
        """Indique si au moins une des deux séries a été collectée."""
        return self.item_12m is not None or self.item_5ans is not None

    @property
    def au_dessus_du_bruit(self) -> bool:
        """Indique si l'indice moyen 12 mois dépasse le seuil de bruit."""
        return (
            self.indice_moyen_12m is not None
            and self.indice_moyen_12m >= SEUIL_INDICE_BRUIT
        )


def _pause(deja_appele: bool) -> bool:
    """Applique la pause obligatoire entre deux appels à la source.

    Args:
        deja_appele: Vrai si un appel a déjà été émis dans cette exécution.

    Returns:
        Toujours `True`, à réaffecter au drapeau de l'appelant.
    """
    if deja_appele:
        _LOG.info("Pause de %s s avant l'appel suivant", PAUSE_ENTRE_APPELS_SECS)
        time.sleep(PAUSE_ENTRE_APPELS_SECS)
    return True


def _collecter_pour_terme(
    terme: str, niveau: int, marche: ParametresMarche, deja_appele: bool
) -> tuple[_Tentative, bool]:
    """Collecte les deux horizons pour un terme donné, séquentiellement.

    Args:
        terme: Mot-clé interrogé.
        niveau: Niveau de repli associé au terme (0 = terme pivot).
        marche: Marché ciblé.
        deja_appele: Vrai si un appel a déjà été émis dans cette exécution.

    Returns:
        Un couple `(tentative, deja_appele)`.
    """
    tentative = _Tentative(terme=terme, niveau=niveau)

    deja_appele = _pause(deja_appele)
    item_12m, statut_12m = collecter_tendances(
        terme=terme, geo=marche.geo, timeframe=TIMEFRAME_12M, fetch_regional=True
    )
    tentative.item_12m = item_12m
    tentative.statuts.append(statut_12m)

    deja_appele = _pause(deja_appele)
    item_5ans, statut_5ans = collecter_tendances(
        terme=terme, geo=marche.geo, timeframe=TIMEFRAME_5ANS, fetch_regional=False
    )
    tentative.item_5ans = item_5ans
    tentative.statuts.append(statut_5ans)

    tentative.indice_moyen_12m = indice_moyen(extraire_serie(item_12m))
    return tentative, deja_appele


def _selectionner_tentative(tentatives: list[_Tentative]) -> _Tentative:
    """Retient la tentative la plus exploitable.

    Priorité : la première tentative dont l'indice moyen 12 mois dépasse le
    seuil de bruit ; à défaut, celle dont l'indice moyen est le plus élevé ; à
    défaut, la première ayant produit une série.

    Args:
        tentatives: Tentatives effectuées, dans l'ordre chronologique.

    Returns:
        La tentative retenue.
    """
    for tentative in tentatives:
        if tentative.au_dessus_du_bruit:
            return tentative

    mesurees = [t for t in tentatives if t.indice_moyen_12m is not None]
    if mesurees:
        return max(mesurees, key=lambda t: t.indice_moyen_12m or 0.0)

    exploitables = [t for t in tentatives if t.exploitable]
    return exploitables[0] if exploitables else tentatives[0]


def _construire_hypotheses(
    produit: FicheProduit,
    mots_cles: JeuMotsCles,
    terme_initial: str,
    tentative: _Tentative,
) -> list[str]:
    """Construit les hypothèses d'interprétation du résultat.

    Args:
        produit: Fiche produit analysée.
        mots_cles: Jeu de mots-clés effectivement utilisé.
        terme_initial: Terme pivot dérivé de la fiche, avant tout repli.
        tentative: Tentative retenue.

    Returns:
        Les hypothèses à joindre au résultat.
    """
    hypotheses = [
        f"Le produit « {produit.nom} » est assimilé au terme de recherche "
        f"« {tentative.terme} »."
    ]
    hypotheses.append(
        f"Terme pivot initialement dérivé de la fiche : « {terme_initial} ». "
        f"Justification : {mots_cles.justification}"
    )
    if mots_cles.attribut_differenciant:
        hypotheses.append(
            "L'attribut différenciant retenu pour caractériser le produit est "
            f"« {mots_cles.attribut_differenciant} »."
        )
    if mots_cles.fallback_applique:
        hypotheses.append(
            f"Repli de niveau {mots_cles.niveau_repli} appliqué : « {terme_initial} » "
            f"n'a pas produit de données au-dessus du seuil de bruit (indice moyen "
            f"12 mois < {SEUIL_INDICE_BRUIT}) ou sa collecte a échoué. Terme "
            f"finalement interrogé : « {tentative.terme} »."
        )
    return hypotheses


def _construire_limites(
    mots_cles: JeuMotsCles, tentative: _Tentative, indicateurs_manquants: list[str]
) -> list[str]:
    """Assemble les limites méthodologiques et les limites du run courant.

    Args:
        mots_cles: Jeu de mots-clés effectivement utilisé.
        tentative: Tentative retenue.
        indicateurs_manquants: Indicateurs non calculables.

    Returns:
        La liste des limites à joindre au résultat.
    """
    limites = list(LIMITES_METHODOLOGIQUES)
    limites.append(LIMITE_REQUETES_EMERGENTES)
    limites.append(LIMITE_SERIE_HEBDOMADAIRE)
    limites.append(LIMITE_LANGUE_NON_PARAMETRABLE)

    for statut in tentative.statuts:
        if not statut.succes:
            limites.append(
                f"Collecte {statut.horizon} indisponible pour "
                f"« {statut.terme_interroge} » : {statut.message_erreur}"
            )
    if indicateurs_manquants:
        limites.append(
            "Indicateurs non calculables faute de données suffisantes : "
            + ", ".join(indicateurs_manquants)
            + "."
        )
    attribut = (mots_cles.attribut_differenciant or "").casefold()
    if attribut and attribut not in tentative.terme.casefold():
        limites.append(
            f"Le terme interrogé « {tentative.terme} » ne porte pas l'attribut "
            f"différenciant « {mots_cles.attribut_differenciant} » : les indicateurs "
            "décrivent la catégorie générique, pas le segment spécifique du produit."
        )
    if tentative.indice_moyen_12m is not None and not tentative.au_dessus_du_bruit:
        limites.append(
            f"L'indice moyen 12 mois du terme retenu ({tentative.indice_moyen_12m:.2f}) "
            f"reste sous le seuil de bruit ({SEUIL_INDICE_BRUIT}) : la série relève "
            "majoritairement de l'échantillonnage et n'est pas interprétable."
        )
    return limites


def analyser_tendances(
    produit: FicheProduit,
    marche: ParametresMarche,
) -> ResultatTendances:
    """Analyse les signaux de tendance d'un produit sur un marché donné.

    Séquence : contrôle qualité de la fiche, dérivation du mot-clé pivot,
    collecte 12 mois puis 5 ans, repli de mot-clé si la série est sous le seuil
    de bruit, puis calcul des indicateurs. La fonction ne lève jamais
    d'exception liée à la collecte : en cas d'échec total, elle retourne un
    résultat avec `donnees_disponibles=False`.

    Args:
        produit: Fiche produit à analyser.
        marche: Marché ciblé (code pays et langue).

    Returns:
        Le résultat structuré et validé de l'analyse.
    """
    _LOG.info("Analyse de « %s » sur le marché %s", produit.nom, marche.geo)

    alertes = controler_fiche_produit(produit, marche)
    mots_cles = deriver_mots_cles(produit, marche)

    termes = [mots_cles.terme_pivot] + mots_cles.termes_replis[:NB_REPLIS_MAX]
    tentatives: list[_Tentative] = []
    deja_appele = False

    for niveau, terme in enumerate(termes):
        _LOG.info("Collecte niveau %s pour le terme « %s »", niveau, terme)
        tentative, deja_appele = _collecter_pour_terme(terme, niveau, marche, deja_appele)
        tentatives.append(tentative)

        if tentative.au_dessus_du_bruit:
            break
        if niveau < len(termes) - 1:
            _LOG.warning(
                "Terme « %s » écarté (indice moyen 12 mois : %s) — passage au repli",
                terme,
                tentative.indice_moyen_12m,
            )

    retenue = _selectionner_tentative(tentatives)
    mots_cles_finaux = mots_cles.model_copy(
        update={
            "terme_pivot": retenue.terme,
            "niveau_repli": retenue.niveau,
            "fallback_applique": retenue.niveau > 0,
        }
    )

    # L'actor ne renvoie ni requêtes associées ni sujets associés en mode
    # `keyword` : ces listes restent vides et la limite correspondante est
    # explicitée dans `limites`.
    indicateurs, manquants = construire_indicateurs(
        item_12m=retenue.item_12m, item_5ans=retenue.item_5ans, requetes_emergentes=[]
    )

    statuts = [statut for tentative in tentatives for statut in tentative.statuts]
    resultat = ResultatTendances(
        produit=produit,
        marche=marche,
        alertes_qualite_input=alertes,
        mots_cles=mots_cles_finaux,
        indicateurs=indicateurs,
        requetes_emergentes=[],
        sujets_associes=[],
        statuts_collecte=statuts,
        donnees_disponibles=indicateurs is not None,
        limites=_construire_limites(mots_cles_finaux, retenue, manquants),
        hypotheses=_construire_hypotheses(
            produit, mots_cles_finaux, mots_cles.terme_pivot, retenue
        ),
    )
    _LOG.info(
        "Analyse terminée — données disponibles : %s, terme retenu : « %s »",
        resultat.donnees_disponibles,
        retenue.terme,
    )
    return resultat
