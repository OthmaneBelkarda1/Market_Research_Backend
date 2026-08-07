"""Agrégations déterministes des analyses cartographiées.

**Aucun appel LLM dans ce module.** Tous les nombres publiés par l'agent —
fréquences, pourcentages, intensités, scores de priorité — sont calculés ici et
nulle part ailleurs. La post-validation les réécrira dans la sortie finale, de
sorte qu'aucune valeur numérique produite par un modèle ne puisse survivre.

Convention de comptage assumée : **les fréquences portent sur les unités
consommateurs** (posts, commentaires, avis). Une page web atteste qu'un thème
existe dans le discours éditorial et compte donc dans les *sources* d'un
insight, mais elle n'est pas une opinion individuelle et n'entre pas dans les
fréquences.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from config import (
    COEFFICIENT_MULTI_SOURCE,
    CONFIANCE_ELEVEE,
    CONFIANCE_FAIBLE,
    CONFIANCE_MOYENNE,
    MAX_CARACTERES_EXTRAIT,
    MAX_ELEMENTS_COMPORTEMENT,
    MAX_VERBATIMS_PAR_PAIN_POINT,
    PORTEE_GLOBALE,
    PORTEE_MIXTE,
    PORTEE_REGIONALE,
    SENTIMENTS_APPLICABLES,
    SENTIMENT_NON_APPLICABLE,
    SEUIL_CONFIANCE_ELEVEE_NB,
    SEUIL_CONFIANCE_MOYENNE_NB,
    SEUIL_PORTEE_DOMINANTE,
    SOURCE_PAR_TYPE_UNITE,
    SOURCE_WEB,
)
from schemas import (
    AnalyseDocument,
    AnalyseUnite,
    ComportementsAchat,
    DocumentWeb,
    ElementFrequence,
    PainPoint,
    Reduction,
    RepartitionSentiment,
    Sentiment,
    SensibilitePrix,
    Theme,
    UniteConsommateur,
    Verbatim,
)

_ESPACES = re.compile(r"\s+")


def _cle_libelle(libelle: str) -> str:
    """Normalise légèrement un libellé pour le regroupement exact.

    Args:
        libelle: Libellé brut.

    Returns:
        Le libellé en minuscules, espaces normalisés.
    """
    return _ESPACES.sub(" ", libelle.strip().lower())


def _portee_dominante(portees: list[str]) -> str:
    """Détermine la portée d'un insight à partir de celles de ses unités.

    Args:
        portees: Portées des unités porteuses.

    Returns:
        « regionale », « globale » ou « mixte ».
    """
    if not portees:
        return PORTEE_MIXTE
    compte = Counter(portees)
    total = len(portees)
    for candidate in (PORTEE_REGIONALE, PORTEE_GLOBALE):
        if compte.get(candidate, 0) / total >= SEUIL_PORTEE_DOMINANTE:
            return candidate
    return PORTEE_MIXTE


def _confiance(nb_unites: int, nb_sources: int) -> str:
    """Qualifie la confiance d'un insight à partir de son assise.

    Args:
        nb_unites: Nombre d'unités distinctes porteuses.
        nb_sources: Nombre de sources distinctes.

    Returns:
        « elevee », « moyenne » ou « faible ».
    """
    if nb_unites >= SEUIL_CONFIANCE_ELEVEE_NB and nb_sources >= 2:
        return CONFIANCE_ELEVEE
    if nb_unites >= SEUIL_CONFIANCE_MOYENNE_NB:
        return CONFIANCE_MOYENNE
    return CONFIANCE_FAIBLE


def _repartition(sentiments: list[str]) -> RepartitionSentiment:
    """Compte les sentiments applicables d'un groupe d'unités.

    Args:
        sentiments: Sentiments bruts, y compris « non_applicable ».

    Returns:
        La répartition, `base_nb` excluant les sentiments non applicables.
    """
    compte = Counter(s for s in sentiments if s in SENTIMENTS_APPLICABLES)
    return RepartitionSentiment(
        positif=compte.get("positif", 0),
        negatif=compte.get("negatif", 0),
        neutre=compte.get("neutre", 0),
        mixte=compte.get("mixte", 0),
        base_nb=sum(compte.values()),
    )


def _agreger_signaux(
    valeurs: list[tuple[str, str]],
) -> list[ElementFrequence]:
    """Agrège des signaux d'achat par libellé normalisé.

    Args:
        valeurs: Couples `(libelle_brut, id_unite)`.

    Returns:
        Les éléments les plus fréquents, bornés par `MAX_ELEMENTS_COMPORTEMENT`.
    """
    groupes: dict[str, tuple[str, list[str]]] = {}
    for libelle, id_unite in valeurs:
        if not libelle or not libelle.strip():
            continue
        cle = _cle_libelle(libelle)
        if cle not in groupes:
            groupes[cle] = (libelle.strip(), [])
        groupes[cle][1].append(id_unite)

    elements = [
        ElementFrequence(
            libelle=affiche,
            frequence_nb=len(set(ids)),
            preuves_id=sorted(set(ids))[:MAX_VERBATIMS_PAR_PAIN_POINT],
        )
        for affiche, ids in groupes.values()
    ]
    elements.sort(key=lambda e: (-e.frequence_nb, e.libelle))
    return elements[:MAX_ELEMENTS_COMPORTEMENT]


def reduire(
    unites: list[UniteConsommateur],
    analyses: list[AnalyseUnite],
    documents: list[DocumentWeb],
    analyses_documents: list[AnalyseDocument],
) -> Reduction:
    """Calcule tous les agrégats chiffrés de l'analyse.

    Args:
        unites: Unités effectivement soumises à la cartographie.
        analyses: Analyses d'unités, libellés déjà normalisés.
        documents: Documents web soumis à la cartographie.
        analyses_documents: Analyses de documents, libellés déjà normalisés.

    Returns:
        L'objet `Reduction` portant thèmes, pain points, sentiment, comportements
        et verbatims candidats.
    """
    index_unites = {u.id_unite: u for u in unites}
    index_documents = {d.id_unite: d for d in documents}
    analyses = [a for a in analyses if a.id_unite in index_unites]
    analyses_documents = [a for a in analyses_documents if a.id_unite in index_documents]

    base = [a for a in analyses if a.sentiment in SENTIMENTS_APPLICABLES]
    nb_base = len(base)

    # --- Sentiment --------------------------------------------------------- #
    sentiment = Sentiment(
        global_=_repartition([a.sentiment for a in analyses]),
        par_source={},
        par_portee={},
    )
    par_source: dict[str, list[str]] = defaultdict(list)
    par_portee: dict[str, list[str]] = defaultdict(list)
    for analyse in analyses:
        unite = index_unites[analyse.id_unite]
        par_source[SOURCE_PAR_TYPE_UNITE[unite.source]].append(analyse.sentiment)
        par_portee[unite.portee].append(analyse.sentiment)
    sentiment.par_source = {k: _repartition(v) for k, v in sorted(par_source.items())}
    sentiment.par_portee = {k: _repartition(v) for k, v in sorted(par_portee.items())}

    # --- Thèmes ------------------------------------------------------------ #
    themes_unites: dict[str, tuple[str, set[str]]] = {}
    for analyse in analyses:
        for libelle in analyse.themes:
            cle = _cle_libelle(libelle)
            if not cle:
                continue
            if cle not in themes_unites:
                themes_unites[cle] = (libelle.strip(), set())
            themes_unites[cle][1].add(analyse.id_unite)

    themes_documents: dict[str, set[str]] = defaultdict(set)
    for analyse in analyses_documents:
        for retour in analyse.retours_rapportes:
            themes_documents[_cle_libelle(retour.libelle)].add(analyse.id_unite)
        for besoin in analyse.besoins_rapportes:
            themes_documents[_cle_libelle(besoin)].add(analyse.id_unite)

    themes: list[Theme] = []
    for cle, (affiche, ids) in themes_unites.items():
        unites_liees = [index_unites[i] for i in ids]
        sentiments = [a.sentiment for a in analyses if a.id_unite in ids]
        applicables = [s for s in sentiments if s in SENTIMENTS_APPLICABLES]
        sources = sorted({SOURCE_PAR_TYPE_UNITE[u.source] for u in unites_liees})
        if themes_documents.get(cle):
            sources = sorted(set(sources) | {SOURCE_WEB})
        exemples = sorted(unites_liees, key=lambda u: u.poids_social, reverse=True)
        themes.append(
            Theme(
                libelle=affiche,
                frequence_nb=len(ids),
                frequence_pct=round(100 * len(ids) / nb_base, 2) if nb_base else 0.0,
                sentiment_dominant=(
                    Counter(applicables).most_common(1)[0][0]
                    if applicables
                    else SENTIMENT_NON_APPLICABLE
                ),
                sources=sources,
                portee=_portee_dominante([u.portee for u in unites_liees]),
                exemples_id_unites=[u.id_unite for u in exemples[:3]],
            )
        )
    themes.sort(key=lambda t: (-t.frequence_nb, t.libelle))

    # --- Pain points ------------------------------------------------------- #
    pp_unites: dict[str, tuple[str, set[str], list[int]]] = {}
    for analyse in analyses:
        for detecte in analyse.pain_points:
            cle = _cle_libelle(detecte.libelle)
            if not cle:
                continue
            if cle not in pp_unites:
                pp_unites[cle] = (detecte.libelle.strip(), set(), [])
            pp_unites[cle][1].add(analyse.id_unite)
            pp_unites[cle][2].append(detecte.intensite)

    pp_documents: dict[str, set[str]] = defaultdict(set)
    for analyse in analyses_documents:
        for retour in analyse.retours_rapportes:
            pp_documents[_cle_libelle(retour.libelle)].add(analyse.id_unite)

    citables = {a.id_unite for a in analyses if a.verbatim_cle}
    pain_points: list[PainPoint] = []
    verbatims_par_pp: dict[str, list[Verbatim]] = {}
    for cle, (affiche, ids, intensites) in pp_unites.items():
        unites_liees = [index_unites[i] for i in ids]
        sources = sorted({SOURCE_PAR_TYPE_UNITE[u.source] for u in unites_liees})
        if pp_documents.get(cle):
            sources = sorted(set(sources) | {SOURCE_WEB})
        frequence_pct = round(100 * len(ids) / nb_base, 2) if nb_base else 0.0
        intensite_moyenne = round(sum(intensites) / len(intensites), 2)
        score = round(
            frequence_pct
            * intensite_moyenne
            * (1 + COEFFICIENT_MULTI_SOURCE * (len(sources) - 1)),
            2,
        )
        pain_points.append(
            PainPoint(
                libelle=affiche,
                frequence_nb=len(ids),
                frequence_pct=frequence_pct,
                intensite_moyenne=intensite_moyenne,
                score_priorite=score,
                sources=sources,
                portee=_portee_dominante([u.portee for u in unites_liees]),
                confiance=_confiance(len(ids), len(sources)),
            )
        )

        candidats = [u for u in unites_liees if u.id_unite in citables]
        candidats.sort(key=lambda u: u.poids_social, reverse=True)
        verbatims_par_pp[affiche] = [
            Verbatim(
                id_unite=u.id_unite,
                source=SOURCE_PAR_TYPE_UNITE[u.source],
                extrait=u.texte[:MAX_CARACTERES_EXTRAIT].strip(),
            )
            for u in candidats[:MAX_VERBATIMS_PAR_PAIN_POINT]
        ]

    pain_points.sort(key=lambda p: (-p.score_priorite, p.libelle))

    # --- Besoins, attentes, signaux d'achat -------------------------------- #
    besoins_bruts = _agreger_signaux(
        [(b, a.id_unite) for a in analyses for b in a.besoins]
        + [(b, a.id_unite) for a in analyses_documents for b in a.besoins_rapportes]
    )
    attentes_brutes = _agreger_signaux(
        [(x, a.id_unite) for a in analyses for x in a.attentes]
    )

    comportements = ComportementsAchat(
        criteres_choix=_agreger_signaux(
            [
                (a.signaux_achat.critere_choix or "", a.id_unite)
                for a in analyses
                if a.signaux_achat.critere_choix
            ]
        ),
        freins=_agreger_signaux(
            [
                (a.signaux_achat.frein or "", a.id_unite)
                for a in analyses
                if a.signaux_achat.frein
            ]
        ),
        declencheurs=_agreger_signaux(
            [
                (a.signaux_achat.declencheur or "", a.id_unite)
                for a in analyses
                if a.signaux_achat.declencheur
            ]
        ),
        occasions_usage=_agreger_signaux(
            [
                (a.signaux_achat.occasion_usage or "", a.id_unite)
                for a in analyses
                if a.signaux_achat.occasion_usage
            ]
        ),
        sensibilite_prix=SensibilitePrix(
            preuves_id=sorted(
                {a.id_unite for a in analyses if a.signaux_achat.mention_prix}
            )[:MAX_VERBATIMS_PAR_PAIN_POINT]
        ),
    )

    positifs_documents = sorted(
        {
            element.strip()
            for a in analyses_documents
            for element in a.elements_positifs
            if element and element.strip()
        }
    )

    return Reduction(
        themes=themes,
        pain_points=pain_points,
        sentiment=sentiment if analyses else None,
        comportements=comportements if analyses else None,
        verbatims_par_pain_point=verbatims_par_pp,
        besoins_bruts=besoins_bruts,
        attentes_brutes=attentes_brutes,
        elements_positifs_documents=positifs_documents,
        nb_unites_base=nb_base,
    )
