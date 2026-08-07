"""Post-validation déterministe du résultat assemblé.

**Aucun appel LLM dans ce module.** Trois garanties y sont produites :

1. toute référence à une unité citée existe réellement dans le corpus analysé ;
2. tout extrait de verbatim est une sous-chaîne du texte source ;
3. tout champ numérique est réécrit depuis la réduction, ce qui rend impossible
   la survie d'un nombre inventé par un modèle.

Chaque correction est tracée dans `statuts_analyse` : la post-validation reste
silencieuse sur un run sain.
"""

from __future__ import annotations

import re

from config import (
    CONFIANCE_FAIBLE,
    MAX_ATTENTES,
    MAX_BESOINS,
    MAX_CARACTERES_EXTRAIT,
    MAX_PAIN_POINTS,
    MAX_SIGNAUX_POSITIFS,
    MAX_THEMES,
    MAX_VERBATIMS_PAR_PAIN_POINT,
)
from schemas import (
    AlerteCoherence,
    CorpusPrepare,
    Reduction,
    ResultatInsightsConsommateurs,
    StatutAnalyse,
)

PHASE_POST_VALIDATION: str = "post_validation"

_ESPACES = re.compile(r"\s+")


def _normaliser(texte: str) -> str:
    """Normalise les espaces pour la comparaison de sous-chaîne.

    Args:
        texte: Texte brut.

    Returns:
        Le texte aux espaces réduits, en minuscules.
    """
    return _ESPACES.sub(" ", texte).strip().lower()


def valider(
    resultat: ResultatInsightsConsommateurs,
    corpus: CorpusPrepare,
    reduction: Reduction,
) -> tuple[ResultatInsightsConsommateurs, list[StatutAnalyse], list[AlerteCoherence]]:
    """Corrige le résultat assemblé et trace chaque correction.

    Args:
        resultat: Résultat brut, avant publication.
        corpus: Corpus analysé, source de vérité des identifiants et des textes.
        reduction: Agrégats déterministes, source de vérité des nombres.

    Returns:
        Le triplet `(resultat_corrige, statuts, alertes)`.
    """
    textes = {u.id_unite: u.texte for u in corpus.unites}
    ids_valides = set(textes) | {d.id_unite for d in corpus.documents}

    retraits_references = 0
    extraits_corriges = 0
    pain_points_degrades: list[str] = []
    alertes: list[AlerteCoherence] = []

    # --- 1. Nombres : écrasement systématique depuis la réduction ---------- #
    chiffres_pp = {p.libelle: p for p in reduction.pain_points}
    chiffres_themes = {t.libelle: t for t in reduction.themes}

    pain_points_retenus = []
    for pain in resultat.pain_points:
        reference = chiffres_pp.get(pain.libelle)
        if reference is None:
            # Pain point inventé par la synthèse : aucun chiffre ne lui correspond.
            alertes.append(
                AlerteCoherence(
                    type="insight_non_ancre",
                    detail=(
                        f"le pain point « {pain.libelle} » ne correspond à aucun "
                        f"agrégat calculé ; il est écarté de la sortie."
                    ),
                )
            )
            continue
        pain.frequence_nb = reference.frequence_nb
        pain.frequence_pct = reference.frequence_pct
        pain.intensite_moyenne = reference.intensite_moyenne
        pain.score_priorite = reference.score_priorite
        pain.sources = list(reference.sources)
        pain.portee = reference.portee
        pain.confiance = reference.confiance
        pain_points_retenus.append(pain)
    resultat.pain_points = pain_points_retenus

    for theme in resultat.themes:
        reference = chiffres_themes.get(theme.libelle)
        if reference is not None:
            theme.frequence_nb = reference.frequence_nb
            theme.frequence_pct = reference.frequence_pct
            theme.sentiment_dominant = reference.sentiment_dominant
            theme.sources = list(reference.sources)
            theme.portee = reference.portee
            theme.exemples_id_unites = list(reference.exemples_id_unites)

    if reduction.sentiment is not None and resultat.sentiment is not None:
        commentaire = resultat.sentiment.commentaire
        resultat.sentiment = reduction.sentiment.model_copy(deep=True)
        resultat.sentiment.commentaire = commentaire

    if reduction.comportements is not None and resultat.comportements_achat is not None:
        lecture_prix = resultat.comportements_achat.sensibilite_prix
        resultat.comportements_achat = reduction.comportements.model_copy(deep=True)
        resultat.comportements_achat.sensibilite_prix.niveau = lecture_prix.niveau
        resultat.comportements_achat.sensibilite_prix.commentaire = (
            lecture_prix.commentaire
        )

    # --- 2. Verbatims : identifiant existant et extrait réellement présent -- #
    for pain in resultat.pain_points:
        verbatims_valides = []
        for verbatim in pain.verbatims[:MAX_VERBATIMS_PAR_PAIN_POINT]:
            source_texte = textes.get(verbatim.id_unite)
            if source_texte is None:
                retraits_references += 1
                continue
            if _normaliser(verbatim.extrait) not in _normaliser(source_texte):
                verbatim.extrait = source_texte[:MAX_CARACTERES_EXTRAIT].strip()
                extraits_corriges += 1
            verbatims_valides.append(verbatim)
        pain.verbatims = verbatims_valides
        if not pain.verbatims and pain.confiance != CONFIANCE_FAIBLE:
            pain.confiance = CONFIANCE_FAIBLE
            pain_points_degrades.append(pain.libelle)

    # --- 3. Preuves et exemples : identifiants existants -------------------- #
    def _filtrer_ids(identifiants: list[str]) -> list[str]:
        nonlocal retraits_references
        gardes = [i for i in identifiants if i in ids_valides]
        retraits_references += len(identifiants) - len(gardes)
        return gardes

    for besoin in resultat.besoins:
        besoin.preuves_id = _filtrer_ids(besoin.preuves_id)
        if not besoin.preuves_id:
            besoin.confiance = CONFIANCE_FAIBLE
    for signal in resultat.signaux_positifs:
        signal.preuves_id = _filtrer_ids(signal.preuves_id)
        if not signal.preuves_id:
            signal.confiance = CONFIANCE_FAIBLE
    for attente in resultat.attentes:
        attente.preuves_id = _filtrer_ids(attente.preuves_id)
    for theme in resultat.themes:
        theme.exemples_id_unites = _filtrer_ids(theme.exemples_id_unites)
    if resultat.comportements_achat is not None:
        for famille in (
            resultat.comportements_achat.criteres_choix,
            resultat.comportements_achat.freins,
            resultat.comportements_achat.declencheurs,
            resultat.comportements_achat.occasions_usage,
        ):
            for element in famille:
                element.preuves_id = _filtrer_ids(element.preuves_id)
        resultat.comportements_achat.sensibilite_prix.preuves_id = _filtrer_ids(
            resultat.comportements_achat.sensibilite_prix.preuves_id
        )

    # --- 4. Plafonds et tri ------------------------------------------------ #
    resultat.pain_points.sort(key=lambda p: (-p.score_priorite, p.libelle))
    resultat.pain_points = resultat.pain_points[:MAX_PAIN_POINTS]
    resultat.themes.sort(key=lambda t: (-t.frequence_nb, t.libelle))
    resultat.themes = resultat.themes[:MAX_THEMES]
    resultat.besoins = resultat.besoins[:MAX_BESOINS]
    resultat.attentes = resultat.attentes[:MAX_ATTENTES]
    resultat.signaux_positifs = resultat.signaux_positifs[:MAX_SIGNAUX_POSITIFS]

    # --- 5. Traçabilité ---------------------------------------------------- #
    statuts: list[StatutAnalyse] = []
    if retraits_references:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"{retraits_references} référence(s) à des identifiants "
                    f"inexistants retirée(s) : le modèle a cité des unités absentes "
                    f"du corpus."
                ),
                nb_elements=retraits_references,
            )
        )
    if extraits_corriges:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"{extraits_corriges} extrait(s) de verbatim ne figuraient pas "
                    f"dans leur texte source et ont été remplacés par le début réel "
                    f"de ce texte."
                ),
                nb_elements=extraits_corriges,
            )
        )
    if pain_points_degrades:
        libelles = ", ".join(f"« {p} »" for p in pain_points_degrades[:5])
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=(
                    f"{len(pain_points_degrades)} pain point(s) sans verbatim valide "
                    f"rétrogradé(s) en confiance faible : {libelles}."
                ),
                nb_elements=len(pain_points_degrades),
            )
        )
        alertes.append(
            AlerteCoherence(
                type="preuve_manquante",
                detail=(
                    "Certains pain points ne sont étayés par aucun verbatim vérifiable ; "
                    "ils restent publiés mais en confiance faible."
                ),
            )
        )
    if not statuts and not alertes:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_POST_VALIDATION,
                succes=True,
                message_erreur=None,
                nb_elements=0,
            )
        )

    return resultat, statuts, alertes
