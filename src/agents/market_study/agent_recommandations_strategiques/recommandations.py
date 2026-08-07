"""Chaînes de recommandations, d'opportunités/risques et de restitution.

Trois chaînes LCEL, toutes alimentées par le dossier de synthèse, le diagnostic
et le verdict — jamais par les données brutes des collecteurs.

Le prompt s'adapte au verdict :

- **négatif** : les recommandations se réduisent à l'essentiel défendable, et
  une recommandation P1 de **non-lancement argumenté** est exigée ;
- **indéterminé** : `donnees_a_completer` est rempli avec précision (quel agent
  relancer, quel signal manque, pourquoi il changerait le verdict), et les
  recommandations se limitent au « sans regret ».
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    MAX_FAITS_CLES,
    MAX_OPPORTUNITES,
    MAX_RECOMMANDATIONS_PAR_DOMAINE,
    MAX_RISQUES,
    MIN_FAITS_CLES,
    VERDICT_INDETERMINE,
    VERDICT_NEGATIF,
    construire_modele,
    invoquer_structure,
)
from schemas import (
    Diagnostic,
    DossierSynthese,
    FicheProduit,
    SortieOpportunitesRisques,
    SortieRecommandations,
    SortieRestitution,
    StatutAnalyse,
    VerdictPotentiel,
)

PHASE_RECOMMANDATIONS: str = "recommandations"
PHASE_OPPORTUNITES: str = "opportunites_risques"
PHASE_RESTITUTION: str = "faits_cles_synthese"

_SOCLE_FONDEMENTS = (
    "- Chaque élément produit porte des `fondements`. Un fondement de type "
    "« fait » DOIT citer une `ref` EXACTE du dossier ; sans ref valide, utilise "
    "« hypothese ». Toute ref inventée sera retirée.\n"
    "- **Interdiction absolue d'utiliser une connaissance extérieure au dossier.** "
    "Aucun fait de marché mémorisé, aucune notoriété de marque, aucun chiffre "
    "d'ordre de grandeur supposé.\n"
    "- N'affirme aucune part de marché, aucun volume de demande, aucune projection "
    "de chiffre d'affaires : le dossier ne les porte pas.\n"
    "- **Ne convertis JAMAIS une devise et ne propose jamais d'en obtenir le taux.** "
    "Deux prix libellés dans des devises différentes décrivent deux marchés "
    "différents, pas le même montant. Le remède à un benchmark manquant dans une "
    "devise est de COLLECTER des prix dans cette devise et cette région.\n"
)

_SYSTEME_RECOMMANDATIONS = (
    "Tu es consultant senior en stratégie e-commerce. Tu produis des "
    "recommandations ACTIONNABLES à partir d'un dossier de synthèse, d'un "
    "diagnostic croisé et d'un verdict de potentiel déjà calculé.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Verdict calculé par le code : **{verdict}**\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Consignes impératives :\n"
    + _SOCLE_FONDEMENTS
    + "- Les recommandations PRODUIT répondent à des pain points documentés ou "
    "matérialisent une différenciation identifiée.\n"
    "- La recommandation PRIX ne propose de fourchettes QUE dans les devises "
    "présentes au benchmark : {devises}. Chaque borne doit rester DANS l'étendue "
    "observée pour cette devise : {bornes}. Toute fourchette hors de ces bornes "
    "sera corrigée. La `logique_ancrage` est explicite (« cœur de marché, sous la "
    "médiane », « premium assumé au-dessus du 3e tercile »…). Si aucune devise "
    "n'est disponible, ne produis AUCUNE fourchette.\n"
    "- **Ces fourchettes sont des positionnements de marché, jamais des calculs de "
    "rentabilité** : aucune donnée de coût ou de marge n'est disponible. Dis-le "
    "dans les `conditions`.\n"
    "- La recommandation POSITIONNEMENT énonce un segment cible, un angle et une "
    "promesse.\n"
    "- Les recommandations MARKETING justifient leurs canaux par les données "
    "(plateformes dominantes observées), relient les messages aux pain points, et "
    "signalent les angles à éviter parce que saturés.\n"
    "- Chaque recommandation porte : une priorité justifiée, un horizon, un impact "
    "attendu, un effort estimé, des risques associés et des `indicateurs_suivi` "
    "MESURABLES.\n"
    "- Les `id_reco` suivent le format « reco-<domaine>-<n> ».\n\n"
    "{consigne_verdict}{erreur_precedente}"
)

_CONSIGNE_POSITIF = (
    "ADAPTATION AU VERDICT POSITIF : produis un jeu complet de recommandations, "
    "hiérarchisées par priorité. Reste sobre : la règle de verdict est une "
    "hypothèse de travail, pas une validation de marché.\n"
)

_CONSIGNE_NEGATIF = (
    "ADAPTATION AU VERDICT NÉGATIF — impérative :\n"
    "- La PREMIÈRE recommandation produit doit être une recommandation de "
    "**NON-LANCEMENT**, en priorité P1, argumentée par les critères défaillants de "
    "la grille et fondée sur des refs du dossier.\n"
    "- Les pivots envisageables viennent ensuite, en P2 ou P3.\n"
    "- Les autres domaines (prix, positionnement, marketing) se réduisent à "
    "l'essentiel défendable : ne construis pas un plan de lancement pour un produit "
    "que tu recommandes de ne pas lancer.\n"
)

_CONSIGNE_INDETERMINE = (
    "ADAPTATION AU VERDICT INDÉTERMINÉ — impérative :\n"
    "- Remplis `donnees_a_completer` avec PRÉCISION : pour chaque manque, dis quel "
    "agent relancer, quel signal précis manque, et POURQUOI il ferait basculer le "
    "verdict. « Compléter l'analyse » n'est pas une réponse acceptable.\n"
    "- Limite les recommandations aux actions « sans regret » : celles qui restent "
    "utiles quel que soit le verdict final.\n"
    "- N'engage aucune dépense significative dans tes recommandations.\n"
)

_HUMAIN_RECOMMANDATIONS = (
    "DOSSIER DE SYNTHÈSE\n{dossier}\n\n"
    "DIAGNOSTIC CROISÉ\n{diagnostic}\n\n"
    "VERDICT ET GRILLE\n{verdict_detail}\n\n"
    "RÉFÉRENCES CITABLES (toute autre ref sera rejetée)\n{refs}"
)

_SYSTEME_OPPORTUNITES = (
    "Tu es consultant senior en stratégie. Tu identifies les OPPORTUNITÉS et les "
    "RISQUES d'un projet, à partir du seul dossier fourni.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Verdict calculé : {verdict}\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    + _SOCLE_FONDEMENTS
    + "- Une opportunité est ANCRÉE : elle croise un angle peu exploité, un besoin "
    "non couvert ou une fenêtre de demande. Ses `conditions_de_capture` disent ce "
    "qu'il faut réunir pour la saisir.\n"
    "- Un angle « peu exploité » est une ABSENCE CONSTATÉE DANS LE CORPUS, pas une "
    "absence de marché : formule les opportunités qui en découlent avec cette "
    "réserve.\n"
    "- Chaque risque porte un `type` parmi : marche, concurrentiel, produit, "
    "operationnel, effet_de_mode, donnees ; une gravité ; et une `attenuation` "
    "concrète.\n"
    "- Le risque « donnees » doit être présent dès qu'une entrée est absente ou "
    "dégradée : décider sur données incomplètes EST un risque.\n"
    "{consigne_mode}{erreur_precedente}"
)

_CONSIGNE_EFFET_DE_MODE = (
    "- ⚠️ La source Tendances signale un EFFET DE MODE ({motif}). Un risque de type "
    "`effet_de_mode` est OBLIGATOIRE, avec une atténuation concrète (limitation "
    "des engagements de stock, fenêtre de sortie courte, critère d'arrêt).\n"
)

_HUMAIN_OPPORTUNITES = (
    "DOSSIER DE SYNTHÈSE\n{dossier}\n\n"
    "DIAGNOSTIC\n{diagnostic}\n\n"
    "QUALITÉ DES ENTRÉES\n{qualite}\n\n"
    "RÉFÉRENCES CITABLES\n{refs}"
)

_SYSTEME_RESTITUTION = (
    "Tu es consultant senior en stratégie. Tu produis la restitution finale d'une "
    "étude : les faits clés, les hypothèses globales et la synthèse exécutive.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Verdict calculé par le code : {verdict} (confiance {confiance})\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    f"- `faits_cles` : entre {MIN_FAITS_CLES} et {MAX_FAITS_CLES} données du dossier, "
    "les plus déterminantes pour le verdict. Chacune porte une `ref` EXACTE du "
    "dossier. La `valeur` sera de toute façon recopiée par le code depuis le "
    "dossier : ne l'invente pas.\n"
    "- `hypotheses_globales` : ce que l'étude a dû supposer pour conclure. Sois "
    "explicite sur le statut d'hypothèse de la règle de verdict.\n"
    "- `synthese_executive` : AU PLUS 15 lignes, dans cet ordre imposé — (1) verdict "
    "et confiance ; (2) trois justifications sourcées ; (3) trois recommandations "
    "majeures ; (4) le risque principal ; (5) si applicable, les données à "
    "compléter.\n"
    "- N'invente aucun nombre absent du dossier. N'affirme aucune part de marché.\n"
    "- Ne présente jamais le verdict comme une validation de marché : il découle "
    "d'une règle qui reste une hypothèse de travail.{erreur_precedente}"
)

_HUMAIN_RESTITUTION = (
    "DOSSIER DE SYNTHÈSE\n{dossier}\n\n"
    "VERDICT ET GRILLE\n{verdict_detail}\n\n"
    "DIAGNOSTIC\n{diagnostic}\n\n"
    "RECOMMANDATIONS RETENUES\n{recommandations}\n\n"
    "RISQUES RETENUS\n{risques}\n\n"
    "DONNÉES À COMPLÉTER\n{a_completer}\n\n"
    "RÉFÉRENCES CITABLES\n{refs}"
)


def _json(valeur) -> str:
    """Sérialise une valeur en JSON lisible.

    Args:
        valeur: Valeur sérialisable.

    Returns:
        Sa représentation JSON, accents conservés.
    """
    return json.dumps(valeur, ensure_ascii=False, indent=1, default=str)


def _consigne_verdict(verdict: str) -> str:
    """Sélectionne la consigne conditionnelle correspondant au verdict.

    Args:
        verdict: Verdict calculé.

    Returns:
        Le bloc de consignes à injecter dans le prompt.
    """
    if verdict == VERDICT_NEGATIF:
        return _CONSIGNE_NEGATIF
    if verdict == VERDICT_INDETERMINE:
        return _CONSIGNE_INDETERMINE
    return _CONSIGNE_POSITIF


def produire_recommandations(
    dossier: DossierSynthese,
    diagnostic: Diagnostic | None,
    verdict: VerdictPotentiel,
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[SortieRecommandations | None, StatutAnalyse]:
    """Produit les recommandations par domaine.

    Args:
        dossier: Dossier de synthèse.
        diagnostic: Diagnostic croisé, éventuellement absent.
        verdict: Verdict calculé par le code.
        produit: Fiche du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_RECOMMANDATIONS), ("human", _HUMAIN_RECOMMANDATIONS)]
    )
    chaine = gabarit | modele.with_structured_output(SortieRecommandations)

    devises = dossier.concurrence.devises_benchmark if dossier.concurrence else []
    bornes = dossier.concurrence.bornes_benchmark if dossier.concurrence else {}

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "verdict": verdict.verdict,
            "consigne_verdict": _consigne_verdict(verdict.verdict),
            "devises": ", ".join(devises) if devises else "AUCUNE",
            "bornes": _json(bornes) if bornes else "aucune",
            "dossier": dossier.model_dump_json(indent=1),
            "diagnostic": _json(diagnostic.model_dump()) if diagnostic else "indisponible",
            "verdict_detail": _json(verdict.model_dump()),
            "refs": _json(sorted(dossier.references())),
        },
        PHASE_RECOMMANDATIONS,
    )
    if resultat is not None:
        resultat.recommandations_produit = resultat.recommandations_produit[
            :MAX_RECOMMANDATIONS_PAR_DOMAINE
        ]
        resultat.recommandations_marketing = resultat.recommandations_marketing[
            :MAX_RECOMMANDATIONS_PAR_DOMAINE
        ]
    return resultat, StatutAnalyse(
        phase=PHASE_RECOMMANDATIONS,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=(
            len(resultat.recommandations_produit) + len(resultat.recommandations_marketing)
            if resultat
            else 0
        ),
        nb_tentatives=tentatives,
    )


def produire_opportunites_risques(
    dossier: DossierSynthese,
    diagnostic: Diagnostic | None,
    verdict: VerdictPotentiel,
    produit: FicheProduit,
    langue_analyse: str,
) -> tuple[SortieOpportunitesRisques | None, StatutAnalyse]:
    """Produit les opportunités et les risques.

    Args:
        dossier: Dossier de synthèse.
        diagnostic: Diagnostic croisé, éventuellement absent.
        verdict: Verdict calculé.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_OPPORTUNITES), ("human", _HUMAIN_OPPORTUNITES)]
    )
    chaine = gabarit | modele.with_structured_output(SortieOpportunitesRisques)

    effet_de_mode = bool(dossier.demande and dossier.demande.effet_de_mode)
    consigne = (
        _CONSIGNE_EFFET_DE_MODE.format(
            motif=dossier.demande.motif_effet_de_mode if dossier.demande else ""
        )
        if effet_de_mode
        else ""
    )

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "langue_analyse": langue_analyse,
            "verdict": verdict.verdict,
            "consigne_mode": consigne,
            "dossier": dossier.model_dump_json(indent=1),
            "diagnostic": _json(diagnostic.model_dump()) if diagnostic else "indisponible",
            "qualite": _json(dossier.qualite_donnees.model_dump()),
            "refs": _json(sorted(dossier.references())),
        },
        PHASE_OPPORTUNITES,
    )
    if resultat is not None:
        resultat.opportunites = resultat.opportunites[:MAX_OPPORTUNITES]
        resultat.risques = resultat.risques[:MAX_RISQUES]
    return resultat, StatutAnalyse(
        phase=PHASE_OPPORTUNITES,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=(
            len(resultat.opportunites) + len(resultat.risques) if resultat else 0
        ),
        nb_tentatives=tentatives,
    )


def produire_restitution(
    dossier: DossierSynthese,
    diagnostic: Diagnostic | None,
    verdict: VerdictPotentiel,
    recommandations: SortieRecommandations | None,
    risques: SortieOpportunitesRisques | None,
    produit: FicheProduit,
    langue_analyse: str,
) -> tuple[SortieRestitution | None, StatutAnalyse]:
    """Produit les faits clés, les hypothèses globales et la synthèse exécutive.

    Args:
        dossier: Dossier de synthèse.
        diagnostic: Diagnostic croisé, éventuellement absent.
        verdict: Verdict calculé.
        recommandations: Recommandations retenues, éventuellement absentes.
        risques: Opportunités et risques, éventuellement absents.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_RESTITUTION), ("human", _HUMAIN_RESTITUTION)]
    )
    chaine = gabarit | modele.with_structured_output(SortieRestitution)

    liste_recos = []
    if recommandations is not None:
        liste_recos = [
            {"id_reco": r.id_reco, "domaine": r.domaine, "enonce": r.enonce, "priorite": r.priorite}
            for r in (
                recommandations.recommandations_produit
                + recommandations.recommandations_marketing
                + (
                    [recommandations.recommandation_positionnement]
                    if recommandations.recommandation_positionnement
                    else []
                )
            )
        ]

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "langue_analyse": langue_analyse,
            "verdict": verdict.verdict,
            "confiance": verdict.confiance,
            "dossier": dossier.model_dump_json(indent=1),
            "verdict_detail": _json(verdict.model_dump()),
            "diagnostic": _json(diagnostic.model_dump()) if diagnostic else "indisponible",
            "recommandations": _json(liste_recos),
            "risques": _json([r.model_dump() for r in risques.risques]) if risques else "[]",
            "a_completer": _json(
                recommandations.donnees_a_completer if recommandations else []
            ),
            "refs": _json(sorted(dossier.references())),
        },
        PHASE_RESTITUTION,
    )
    if resultat is not None:
        resultat.faits_cles = resultat.faits_cles[:MAX_FAITS_CLES]
    return resultat, StatutAnalyse(
        phase=PHASE_RESTITUTION,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=len(resultat.faits_cles) if resultat else 0,
        nb_tentatives=tentatives,
    )
