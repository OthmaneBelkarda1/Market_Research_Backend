"""Phase de synthèse : deux chaînes LCEL sur le modèle de synthèse.

1. **Synthèse des insights** — rédige les descriptions, structure besoins et
   attentes, lit les comportements d'achat et relève les divergences entre
   sources. Elle reçoit les **agrégats** de la réduction, jamais le corpus brut.
2. **Lecture critique** — biais probables, facteurs de confiance et synthèse
   exécutive.

Aucune des deux ne produit de nombre : tous les chiffres présents dans la sortie
proviennent de `reduction.py` et sont réécrits par `validation.py`.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    MAX_TOKENS_SYNTHESE,
    MODELE_SYNTHESE,
    construire_modele,
    invoquer_structure,
)
from schemas import (
    CorpusPrepare,
    FicheProduit,
    Reduction,
    SortieLectureCritique,
    SortieSyntheseInsights,
    SourceUtilisee,
    StatutAnalyse,
)

PHASE_SYNTHESE: str = "synthese_insights"
PHASE_LECTURE: str = "lecture_critique"

_SYSTEME_SYNTHESE = (
    "Tu es analyste senior en études de marché. On te remet les AGRÉGATS déjà "
    "calculés d'un corpus de messages consommateurs : tu les interprètes et les "
    "rédiges. Tu ne recalcules rien.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Consignes impératives :\n"
    "- Ne cite QUE des identifiants d'unités présents dans les données fournies. "
    "Un identifiant inventé sera retiré et tracé comme erreur.\n"
    "- N'invente AUCUNE statistique, aucun pourcentage, aucun volume. Les nombres "
    "de la sortie finale sont ceux du code ; si tu en écris un, il sera écrasé.\n"
    "- Recopie les `libelle` de pain points À L'IDENTIQUE : ils servent de clé de "
    "rattachement aux chiffres déjà calculés.\n"
    "- Distingue rigoureusement ce que dit le corpus de ce que tu supposes. Si une "
    "lecture est une hypothèse, écris-le dans la description (« le corpus ne "
    "permet pas de trancher entre… »).\n"
    "- N'affirme jamais qu'un besoin est répandu « sur le marché » : le corpus "
    "n'est pas un échantillon représentatif. Formule en « dans le corpus ».\n"
    "- `divergences_sources` recense des écarts FACTUELS constatés entre sources "
    "ou entre portées régionale et globale, pas des impressions.\n"
    "- Un besoin est ce que le consommateur cherche à obtenir ; une attente est ce "
    "qu'il considère comme dû (« standard ») ou comme un plus qui le ferait "
    "choisir (« differenciant »).\n"
    "- `signaux_positifs` recense ce que les consommateurs LOUENT explicitement.\n"
    "- Si les agrégats sont trop pauvres pour conclure sur un point, laisse la "
    "liste vide plutôt que de meubler.{erreur_precedente}"
)

_HUMAIN_SYNTHESE = (
    "AGRÉGATS DU CORPUS (JSON)\n\n"
    "Base de calcul : {nb_base} unités porteuses d'une opinion applicable.\n\n"
    "Répartition des sentiments :\n{sentiment}\n\n"
    "Thèmes récurrents :\n{themes}\n\n"
    "Pain points hiérarchisés (avec verbatims candidats) :\n{pain_points}\n\n"
    "Besoins bruts agrégés :\n{besoins}\n\n"
    "Attentes brutes agrégées :\n{attentes}\n\n"
    "Signaux d'achat agrégés :\n{comportements}\n\n"
    "Éléments positifs relevés par les pages web :\n{positifs_web}"
)

_SYSTEME_LECTURE = (
    "Tu es analyste senior en études de marché, chargé de la lecture critique "
    "d'un corpus. Ton rôle est de dire ce que ce corpus NE permet PAS de "
    "conclure, aussi clairement que ce qu'il permet.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Marché : {marche}\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Consignes impératives :\n"
    "- Les biais que tu relèves doivent être ancrés dans les données fournies : "
    "sources présentes et absentes, volumes, langues, portées, échantillonnage.\n"
    "- `niveau_confiance` vaut « elevee », « moyenne » ou « faible ». Sois "
    "sévère : un corpus mono-source, faible en volume ou de portée majoritairement "
    "globale ne justifie pas mieux que « faible ».\n"
    "- La `synthese_executive` fait AU PLUS 12 lignes et suit cette structure, "
    "dans cet ordre : volume et nature du corpus ; sentiment dominant ; les 3 pain "
    "points prioritaires ; les besoins saillants ; la mise en garde principale.\n"
    "- N'affirme aucune taille de marché, aucune part de marché, aucune "
    "projection. Ce corpus ne les porte pas.\n"
    "- N'invente aucun nombre absent des données fournies.{erreur_precedente}"
)

_HUMAIN_LECTURE = (
    "STATISTIQUES DU CORPUS\n{stats}\n\n"
    "SOURCES CHARGÉES\n{sources}\n\n"
    "RÉPARTITION DES SENTIMENTS\n{sentiment}\n\n"
    "PAIN POINTS PRIORITAIRES\n{pain_points}\n\n"
    "BESOINS RETENUS\n{besoins}\n\n"
    "LIMITES DÉJÀ IDENTIFIÉES\n{limites}"
)


def _json(valeur) -> str:
    """Sérialise une valeur en JSON compact, destiné à un prompt.

    JSON COMPACT, PAS INDENTÉ — l'indentation est facturée comme le reste.
    Mesuré par `count_tokens` sur le run de référence : `indent=1` coûtait
    33 335 jetons d'entrée sur l'ensemble du pipeline, soit 9,5 % de toute
    l'entrée, pour zéro information supplémentaire — le modèle reçoit le même
    objet dans les deux cas. Les documents de sortie restent indentés : eux
    sont lus par des humains (`main.py`, `indent=2`).

    Args:
        valeur: Valeur sérialisable.

    Returns:
        Sa représentation JSON, accents conservés.
    """
    return json.dumps(valeur, ensure_ascii=False, separators=(",", ":"), default=str)


def synthetiser_insights(
    reduction: Reduction,
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[SortieSyntheseInsights | None, StatutAnalyse]:
    """Rédige les insights à partir des agrégats.

    Args:
        reduction: Agrégats déterministes.
        produit: Fiche du produit étudié.
        marche: Libellé du marché, pour le contexte.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_SYNTHESE), ("human", _HUMAIN_SYNTHESE)]
    )
    chaine = gabarit | modele.with_structured_output(SortieSyntheseInsights)

    pain_points = [
        {
            "libelle": p.libelle,
            "frequence_nb": p.frequence_nb,
            "frequence_pct": p.frequence_pct,
            "intensite_moyenne": p.intensite_moyenne,
            "sources": p.sources,
            "portee": p.portee,
            "verbatims_candidats": [
                {"id_unite": v.id_unite, "extrait": v.extrait}
                for v in reduction.verbatims_par_pain_point.get(p.libelle, [])
            ],
        }
        for p in reduction.pain_points
    ]

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "nb_base": reduction.nb_unites_base,
            "sentiment": _json(
                reduction.sentiment.model_dump(by_alias=True)
                if reduction.sentiment
                else {}
            ),
            "themes": _json([t.model_dump() for t in reduction.themes]),
            "pain_points": _json(pain_points),
            "besoins": _json([b.model_dump() for b in reduction.besoins_bruts]),
            "attentes": _json([a.model_dump() for a in reduction.attentes_brutes]),
            "comportements": _json(
                reduction.comportements.model_dump() if reduction.comportements else {}
            ),
            "positifs_web": _json(reduction.elements_positifs_documents),
        },
        PHASE_SYNTHESE,
    )
    statut = StatutAnalyse(
        phase=PHASE_SYNTHESE,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=len(resultat.besoins) if resultat else 0,
        nb_tentatives=tentatives,
    )
    return resultat, statut


def lecture_critique(
    corpus: CorpusPrepare,
    reduction: Reduction,
    sources: list[SourceUtilisee],
    besoins_libelles: list[str],
    limites: list[str],
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[SortieLectureCritique | None, StatutAnalyse]:
    """Produit la lecture critique et la synthèse exécutive.

    Args:
        corpus: Corpus préparé, pour ses statistiques.
        reduction: Agrégats déterministes.
        sources: Comptes rendus de chargement.
        besoins_libelles: Libellés des besoins retenus.
        limites: Limites déjà identifiées par le code.
        produit: Fiche du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_LECTURE), ("human", _HUMAIN_LECTURE)]
    )
    chaine = gabarit | modele.with_structured_output(SortieLectureCritique)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "stats": _json(corpus.stats.model_dump()),
            "sources": _json([s.model_dump() for s in sources]),
            "sentiment": _json(
                reduction.sentiment.model_dump(by_alias=True)
                if reduction.sentiment
                else {}
            ),
            "pain_points": _json(
                [
                    {
                        "libelle": p.libelle,
                        "frequence_nb": p.frequence_nb,
                        "score_priorite": p.score_priorite,
                        "portee": p.portee,
                        "sources": p.sources,
                    }
                    for p in reduction.pain_points[:8]
                ]
            ),
            "besoins": _json(besoins_libelles),
            "limites": _json(limites),
        },
        PHASE_LECTURE,
    )
    statut = StatutAnalyse(
        phase=PHASE_LECTURE,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_tentatives=tentatives,
    )
    return resultat, statut
