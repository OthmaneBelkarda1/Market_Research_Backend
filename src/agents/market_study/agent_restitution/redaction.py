"""Chaînes de rédaction — **le seul module de cet agent qui appelle un modèle**.

Une chaîne par section narrative. Chacune ne reçoit que les données injectables
de sa section : un rédacteur qui n'a pas vu les chiffres d'une autre section ne
peut matériellement pas les citer de travers.

Le modèle **rédige des transitions et des lectures**. Il ne produit ni chiffre,
ni tableau, ni sélection de verbatim : tout cela est injecté par le code, et le
contrôle numérique de `validation.py` retire toute phrase qui s'en écarte.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from config import (
    MAX_RESERVES_SYNTHESE,
    MIN_RESERVES_SYNTHESE,
    REGLES_FORMULATION,
    SECTION_CONCURRENCE,
    SECTION_CONSOMMATEURS,
    SECTION_DEMANDE,
    SECTION_PLC,
    SECTION_SYNTHESE,
    SECTION_VERDICT,
    construire_modele,
    invoquer_structure,
)
from schemas import Injectables, SortieNarratif, StatutAnalyse

_SYSTEME_COMMUN = (
    "Tu es consultant senior. Tu restitues une étude de marché déjà réalisée à un "
    "décideur non technique : tu mets en forme des analyses existantes, tu n'en "
    "produis aucune nouvelle.\n\n"
    "Produit étudié : {produit}\n"
    "Marché : {marche}\n"
    "Langue de rédaction : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Section à rédiger : {titre_section}\n"
    "Longueur maximale : {longueur_max} mots, en {nb_paragraphes} paragraphe(s) au "
    "plus. Un paragraphe = un bloc de texte continu, sans titre ni puce.\n"
    "Niveau de fiabilité de cette section : {badge}. Une fiabilité faible impose la "
    "modalisation : « les données disponibles suggèrent », « dans le corpus "
    "collecté », jamais l'affirmation.\n\n"
    + REGLES_FORMULATION
    + "\n\n"
    "Tu ne réécris ni les tableaux, ni les listes fournies : ils sont déjà dans le "
    "rapport, au-dessus ou au-dessous de ton texte. Ton rôle est de dire ce qu'ils "
    "signifient, pas de les répéter.\n"
    "{consigne_specifique}"
    "{erreur_precedente}"
)

_HUMAIN_COMMUN = "DONNÉES DE LA SECTION\n{donnees}"

_CONSIGNES: dict[str, str] = {
    SECTION_SYNTHESE: (
        "\nConsignes propres à cette section :\n"
        "- Ouvre par le verdict tel qu'il est fourni, sans l'adoucir ni le "
        "reformuler. S'il est « indéterminé », le mot est « indéterminé ».\n"
        "- Enchaîne sur ce qui fonde ce verdict et sur ce qui reste à trancher.\n"
        f"- Produis en outre {MIN_RESERVES_SYNTHESE} à {MAX_RESERVES_SYNTHESE} "
        "RÉSERVES MAJEURES dans le champ `puces` : les objections qu'un décideur "
        "doit garder en tête avant d'engager quoi que ce soit. Une réserve tient "
        "en une phrase et porte sur la solidité des données ou sur un risque "
        "structurel, pas sur un détail d'exécution.\n"
        "- N'énumère pas les faits clés ni les recommandations : ils figurent déjà "
        "en liste sous ton texte."
    ),
    SECTION_VERDICT: (
        "\nConsignes propres à cette section :\n"
        "- Lis la grille : quels critères tirent le score vers le haut, lesquels "
        "vers le bas, et ce que le déséquilibre signifie.\n"
        "- Explique la portée de la règle sans la reformuler : elle est une "
        "hypothèse de travail, ses seuils ne sont pas des vérités de marché.\n"
        "- Les conditions de bascule fournies ont été RECALCULÉES : reprends-les "
        "telles quelles si tu les mentionnes, n'en invente aucune autre et n'affirme "
        "jamais qu'un autre critère ferait basculer le verdict.\n"
        "- Ne propose aucune recommandation ici."
    ),
    SECTION_PLC: (
        "\nConsignes propres à cette section :\n"
        "- La phase fournie est une classification produite par une grille de "
        "lecture EN HYPOTHÈSE DE TRAVAIL : dis ce qu'elle implique, et dis aussi ce "
        "que son incertitude interdit de conclure.\n"
        "- Ne remets jamais en cause le verdict de potentiel : cette section le "
        "prolonge, elle ne le rejuge pas.\n"
        "- La phase nommée est celle qui t'est fournie, sans synonyme ni nuance."
    ),
    SECTION_DEMANDE: (
        "\nConsignes propres à cette section :\n"
        "- Les indices de recherche sont RELATIFS : ils ne portent aucun volume "
        "absolu, donc aucune taille de marché. Dis-le si tu les commentes.\n"
        "- Quand deux indicateurs se contredisent (une tendance de fond positive et "
        "un recul récent, par exemple), expose la contradiction et ses lectures "
        "possibles ; ne tranche pas et ne fais pas de moyenne.\n"
        "- Aucune projection, aucune prévision."
    ),
    SECTION_CONSOMMATEURS: (
        "\nConsignes propres à cette section :\n"
        "- Ouvre par ce que le corpus documente le plus nettement.\n"
        "- Les écarts de sentiment entre sources s'EXPLIQUENT par les biais de "
        "chaque plateforme (public, langue, contexte d'expression) ; ils ne se "
        "moyennent pas. Si une explication de divergence t'est fournie, reprends-la.\n"
        "- Les extraits cités figurent déjà sous ton texte : ne les recopie pas.\n"
        "- Ne dis jamais « les consommateurs » : dis « les avis et discussions "
        "analysés » ou « les contributions du corpus »."
    ),
    SECTION_CONCURRENCE: (
        "\nConsignes propres à cette section :\n"
        "- Les prix ne se comparent qu'à l'intérieur d'une même source et d'une "
        "même devise. Aucune conversion n'a été faite ni ne doit être suggérée.\n"
        "- La portée régionale de chaque source est fournie : une source de portée "
        "mondiale ne décrit pas le marché étudié, dis-le.\n"
        "- Les angles peu exploités sont des ABSENCES D'OBSERVATION dans le corpus "
        "publicitaire et éditorial collecté, jamais des absences de marché : "
        "conserve la formulation « non observé dans le corpus »."
    ),
}

_NB_PARAGRAPHES: dict[str, int] = {
    SECTION_SYNTHESE: 2,
    SECTION_VERDICT: 2,
    SECTION_PLC: 2,
    SECTION_DEMANDE: 2,
    SECTION_CONSOMMATEURS: 3,
    SECTION_CONCURRENCE: 3,
}


def donnees_de_section(section: str, injectables: Injectables) -> dict[str, Any]:
    """Extrait la tranche de données injectables destinée à une section.

    Une chaîne de rédaction ne voit rien d'autre : c'est la garantie qu'elle ne
    peut pas citer un chiffre appartenant à une autre section.

    Args:
        section: Identifiant de la section.
        injectables: Données injectables complètes.

    Returns:
        Le dictionnaire des données de la section.
    """
    if section == SECTION_SYNTHESE:
        return {
            "verdict": injectables.verdict_lisible,
            "confiance_du_verdict": injectables.confiance_verdict,
            "faits_cles": injectables.faits_cles,
            "recommandations_majeures": injectables.recommandations_majeures,
            "risque_principal": injectables.risque_principal,
            "conditions_de_bascule_recalculees": [b.enonce for b in injectables.bascules],
            "sections_indisponibles": injectables.sections_degradees
            + injectables.sections_absentes,
        }
    if section == SECTION_VERDICT:
        return {
            "verdict": injectables.verdict_lisible,
            "confiance_du_verdict": injectables.confiance_verdict,
            "grille": injectables.tableau_grille,
            "regle_appliquee": injectables.regle_litterale,
            "conditions_de_bascule_recalculees": [b.enonce for b in injectables.bascules],
            "donnees_a_completer": injectables.donnees_a_completer,
        }
    if section == SECTION_PLC:
        return {
            "phase": injectables.phase_lisible,
            "incertitude": injectables.incertitude_phase,
            "signaux": injectables.tableau_signaux_plc,
            "recommandations_de_phase": injectables.recommandations_phase,
        }
    if section == SECTION_DEMANDE:
        return {"indicateurs": injectables.tableau_demande}
    if section == SECTION_CONSOMMATEURS:
        return {
            "besoins": injectables.tableau_besoins,
            "attentes": injectables.tableau_attentes,
            "irritants": injectables.pain_points,
            "sentiment_par_source": injectables.tableau_sentiment,
            "divergences_entre_sources": injectables.divergences,
        }
    if section == SECTION_CONCURRENCE:
        return {
            "intensite": injectables.tableau_intensite,
            "concurrents": injectables.tableau_concurrents,
            "benchmark_prix": injectables.tableau_benchmark,
            "portee_regionale_des_sources": injectables.portee_regionale,
            "standards_observes": injectables.normes_marche,
            "angles_peu_exploites": injectables.angles_peu_exploites,
        }
    return {}


def rediger_section(
    section: str,
    titre: str,
    longueur_max: int,
    injectables: Injectables,
    produit: str,
    marche: str,
    langue_analyse: str,
    consigne_supplementaire: str = "",
) -> tuple[SortieNarratif | None, StatutAnalyse]:
    """Rédige le narratif d'une section.

    Args:
        section: Identifiant de la section.
        titre: Titre affiché de la section.
        longueur_max: Longueur maximale du narratif, en mots.
        injectables: Données injectables complètes.
        produit: Nom du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.
        consigne_supplementaire: Consigne ajoutée lors d'une régénération.

    Returns:
        Le couple `(narratif_ou_None, statut)`.
    """
    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_COMMUN), ("human", _HUMAIN_COMMUN)]
    )
    chaine = gabarit | modele.with_structured_output(SortieNarratif)

    donnees = donnees_de_section(section, injectables)
    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit": produit,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "titre_section": titre,
            "longueur_max": longueur_max,
            "nb_paragraphes": _NB_PARAGRAPHES.get(section, 2),
            "badge": injectables.badges.get(section, "non qualifié"),
            "consigne_specifique": _CONSIGNES.get(section, "")
            + consigne_supplementaire,
            "donnees": json.dumps(donnees, ensure_ascii=False, separators=(",", ":")),
        },
        f"redaction_{section}",
    )
    return resultat, StatutAnalyse(
        phase=f"redaction_{section}",
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=len(resultat.paragraphes) if resultat else 0,
        nb_tentatives=tentatives,
    )
