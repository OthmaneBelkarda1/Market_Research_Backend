"""Diagnostic croisé des trois faces du marché.

Une chaîne LCEL unique. Son entrée est le **dossier de synthèse sérialisé**, et
rien d'autre : c'est ce qui rend chaque constat vérifiable.

Le point délicat traité ici est la **contradiction**. Quand la demande décline
selon Tendances mais que la pression publicitaire est forte selon F4, deux
lectures opposées restent possibles. La consigne interdit de trancher : la
`lecture_prudente` expose les explications concurrentes et dit ce qu'il faudrait
observer pour départager.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import construire_modele, invoquer_structure
from schemas import Diagnostic, DossierSynthese, FicheProduit, StatutAnalyse

PHASE_DIAGNOSTIC: str = "diagnostic_croise"

_SYSTEME = (
    "Tu es consultant senior en stratégie. Tu confrontes trois faces d'un marché — "
    "la DEMANDE (tendances de recherche), les CONSOMMATEURS (corpus d'opinions) et "
    "l'OFFRE (analyse concurrentielle) — pour en tirer un diagnostic croisé.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Marché : {marche}\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Consignes impératives :\n"
    "- Chaque constat cite ses `ref` du dossier. Un fondement de type « fait » DOIT "
    "porter une `ref` EXACTE ; sans ref valide, utilise « hypothese ». Toute ref "
    "inventée sera retirée et tracée comme erreur.\n"
    "- **Interdiction absolue d'utiliser une connaissance extérieure au dossier** : "
    "aucun fait de marché mémorisé, aucune notoriété de marque, aucun ordre de "
    "grandeur supposé. Si le dossier ne le dit pas, tu ne le sais pas.\n"
    "- Une CONVERGENCE est un constat que deux faces au moins soutiennent. Ne "
    "qualifie pas de convergence ce qu'une seule source affirme.\n"
    "- Une CONTRADICTION oppose deux faces. Sa `lecture_prudente` doit exposer les "
    "explications POSSIBLES sans trancher arbitrairement, et dire ce qu'il faudrait "
    "observer pour départager. Ne choisis jamais la lecture la plus flatteuse.\n"
    "- `lecture_marche` fait 5 à 10 phrases : ce que le dossier établit, ce qu'il "
    "suggère, ce qu'il ne permet pas de conclure.\n"
    "- `fenetre_opportunite` n'est renseignée que si le dossier porte un élément de "
    "temporalité (saisonnalité, momentum, longévité publicitaire). Sinon : null.\n"
    "- N'affirme aucune taille de marché, aucune part de marché, aucune projection "
    "de vente : le dossier ne les porte pas.\n"
    "- Ne formule ici AUCUNE recommandation et AUCUN verdict.{erreur_precedente}"
)

_HUMAIN = (
    "DOSSIER DE SYNTHÈSE\n{dossier}\n\n"
    "RÉFÉRENCES CITABLES (toute autre ref sera rejetée)\n{refs}\n\n"
    "ENTRÉES ABSENTES — ne conclus rien sur ces faces\n{absentes}"
)


def etablir_diagnostic(
    dossier: DossierSynthese,
    entrees_absentes: set[str],
    produit: FicheProduit,
    marche: str,
    langue_analyse: str,
) -> tuple[Diagnostic | None, StatutAnalyse]:
    """Produit le diagnostic croisé.

    Args:
        dossier: Dossier de synthèse, seul contenu transmis.
        entrees_absentes: Entrées non chargées.
        produit: Fiche du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(diagnostic_ou_None, statut)`.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages([("system", _SYSTEME), ("human", _HUMAIN)])
    chaine = gabarit | modele.with_structured_output(Diagnostic)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "dossier": dossier.model_dump_json(),
            "refs": json.dumps(sorted(dossier.references()), ensure_ascii=False, separators=(",", ":")),
            "absentes": json.dumps(sorted(entrees_absentes), ensure_ascii=False)
            if entrees_absentes
            else "aucune",
        },
        PHASE_DIAGNOSTIC,
    )
    return resultat, StatutAnalyse(
        phase=PHASE_DIAGNOSTIC,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=(
            len(resultat.convergences) + len(resultat.contradictions) if resultat else 0
        ),
        nb_tentatives=tentatives,
    )
