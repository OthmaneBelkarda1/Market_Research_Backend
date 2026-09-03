"""Chaînes de rédaction du gabarit v2 — **le seul module v2 qui appelle un modèle**.

Une chaîne par écran narratif, plus une chaîne de compression rédactionnelle.

Deux différences de fond avec le v1 :

1. **La sortie est structurée par sous-bloc**, et ce sont des puces, pas des
   paragraphes. Chaque sous-titre est une question métier ; les puces y répondent.
   Le modèle ne reçoit que les sous-blocs qui lui sont confiés — les autres sont
   des tableaux, des lignes standard ou des textes amont, produits par le code.
2. **La compression rédactionnelle remplace la troncature à « … »**. Une cellule
   trop longue n'est plus coupée au milieu d'un argument : elle est réécrite plus
   court. Comme tout texte de modèle, la sortie passe au contrôle de liste
   blanche ; une compression qui introduirait un chiffre absent de l'original est
   rejetée et l'original coupé au dernier mot entier prend le relais.

Le modèle ne produit toujours **aucune donnée** : ni chiffre, ni tableau, ni
sélection d'extrait.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from config import (
    ECRAN_CONCURRENCE,
    ECRAN_CONSOMMATEUR,
    ECRAN_DECISION,
    ECRAN_RECOMMANDATIONS,
    MAX_MOTS_PUCE,
    MAX_MOTS_PUCE_CONCURRENTS,
    NB_FAITS_CLES_DECISION,
    NB_POINTS_FRICTION,
    PHRASE_CLIENTELE_NON_CARACTERISEE,
    REGLES_FORMULATION,
    REGLES_FORMULATION_V2,
    SB_AIMERAIENT,
    SB_APPRECIENT,
    SB_DERANGE,
    SB_DYNAMIQUE,
    SB_ENTREE_MARCHE,
    SB_POURQUOI,
    SB_POURQUOI_ACHAT,
    SB_PRIX,
    SB_PRIX_PRATIQUES,
    SB_QUE_FONT,
    SB_RISQUE_PRINCIPAL,
    SOUS_BLOCS_REDIGES,
    construire_modele,
    invoquer_structure,
)
from schemas import Injectables, SortieCompression, SortieEcran, StatutAnalyse

_SYSTEME_ECRAN = (
    "Tu es consultant senior. Tu restitues à un décideur une étude de marché déjà "
    "réalisée : tu mets en forme des analyses existantes, tu n'en produis aucune "
    "nouvelle.\n\n"
    "Produit étudié : {produit}\n"
    "Marché : {marche}\n"
    "Langue de rédaction : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Écran à rédiger : {titre_ecran}\n"
    "Budget de l'écran : {budget_mots} mots au total, toutes puces confondues.\n"
    "Niveau de fiabilité : {badge}. Une fiabilité faible impose la modalisation : "
    "« les données disponibles suggèrent », « dans le corpus collecté », jamais "
    "l'affirmation.\n\n"
    + REGLES_FORMULATION_V2
    + "\n\n"
    + REGLES_FORMULATION
    + "\n\n"
    "Tu ne réécris ni les tableaux, ni les listes fournies : ils sont déjà dans le "
    "rapport, au-dessus ou au-dessous de tes puces. Ton rôle est de dire ce qu'ils "
    "signifient, pas de les répéter.\n\n"
    "SOUS-BLOCS À PRODUIRE — n'en écris aucun autre, et n'en omets aucun :\n"
    "{consignes_sous_blocs}"
    "{erreur_precedente}"
)

_HUMAIN_ECRAN = "DONNÉES DE L'ÉCRAN\n{donnees}"

# --------------------------------------------------------------------------- #
# Consignes par sous-bloc
# --------------------------------------------------------------------------- #
# Chaque consigne dit la question posée, le nombre de puces attendu et ce sur quoi
# elles doivent porter. Le modèle ne voit que les sous-blocs qui lui sont confiés.

_CONSIGNES_SOUS_BLOCS: dict[str, str] = {
    SB_POURQUOI: (
        f"- `{SB_POURQUOI}` — question « Pourquoi ». Exactement "
        f"{NB_FAITS_CLES_DECISION} puces, une par fait clé fourni, dans l'ordre "
        "fourni. Chaque puce PORTE AU MOINS UN CHIFFRE recopié exactement du fait "
        "clé correspondant. Tu reformules pour la lisibilité, tu n'ajoutes rien."
    ),
    SB_RISQUE_PRINCIPAL: (
        f"- `{SB_RISQUE_PRINCIPAL}` — question « Le risque principal ». UNE seule "
        "puce, celle du risque fourni : ce qu'il menace, et ce qui l'atténuerait."
    ),
    SB_POURQUOI_ACHAT: (
        f"- `{SB_POURQUOI_ACHAT}` — question « Pourquoi ils achètent — ou non ». "
        "2 à 4 puces : d'abord les besoins dominants qui motivent l'achat, ensuite "
        "les freins constatés. Une puce par idée, jamais une liste dans une puce."
    ),
    SB_APPRECIENT: (
        f"- `{SB_APPRECIENT}` — question « Ce qu'ils apprécient ». 2 à 4 puces, "
        "tirées des signaux positifs et des attentes de niveau standard que le "
        "corpus montre satisfaites."
    ),
    SB_DERANGE: (
        f"- `{SB_DERANGE}` — question « Ce qui les dérange ». Exactement UNE PHRASE "
        f"PAR POINT DE FRICTION FOURNI, dans l'ordre fourni, au plus "
        f"{NB_POINTS_FRICTION} au total. N'écris NI le titre, NI le pourcentage, NI "
        "l'intensité : le code les place déjà devant ta phrase. Ta phrase dit ce que "
        "le point recouvre concrètement, rien d'autre."
    ),
    SB_AIMERAIENT: (
        f"- `{SB_AIMERAIENT}` — question « Ce qu'ils aimeraient trouver ». 2 à 4 "
        "puces : attentes de niveau « différenciant » et besoins que le corpus "
        "signale comme non couverts."
    ),
    SB_DYNAMIQUE: (
        f"- `{SB_DYNAMIQUE}` — question « Dynamique de la demande ». UNE puce de "
        "lecture des indicateurs fournis. Les indices de recherche sont RELATIFS : "
        "ils ne portent aucun volume absolu et donc aucune taille de marché ; "
        "dis-le. Aucune projection."
    ),
    SB_QUE_FONT: (
        f"- `{SB_QUE_FONT}` — question « Que font les concurrents ? ». EXACTEMENT "
        "CINQ puces, dans cet ordre et avec ces libellés en gras au début de chaque "
        "puce : **Leurs forces** / **Leurs faiblesses** / **Leur marketing** / "
        f"**Leurs prix** / **Leur clientèle**. Chaque puce fait au plus "
        f"{MAX_MOTS_PUCE_CONCURRENTS} mots. Si la clientèle visée ne t'est pas "
        f"fournie, écris exactement : « **Leur clientèle** — "
        f"{PHRASE_CLIENTELE_NON_CARACTERISEE} »"
    ),
    SB_PRIX_PRATIQUES: (
        f"- `{SB_PRIX_PRATIQUES}` — question « Prix pratiqués ». UNE puce : l'écart "
        "entre canaux et son explication. Les prix ne se comparent qu'à l'intérieur "
        "d'une même devise ; aucune conversion n'a été faite ni ne doit être "
        "suggérée."
    ),
    SB_PRIX: (
        f"- `{SB_PRIX}` — question « Prix ». 2 à 3 puces : sur quel canal l'ancrage "
        "s'appuie et pourquoi, le prix de test proposé, la condition qui "
        "autoriserait une montée en gamme. N'écris pas la fourchette elle-même : le "
        "code l'affiche au-dessus de tes puces."
    ),
    SB_ENTREE_MARCHE: (
        f"- `{SB_ENTREE_MARCHE}` — question « Entrée sur le marché ». 3 à 5 puces : "
        "positionnement recommandé, cible prioritaire, angle de communication, "
        "angles à éviter parce que saturés, canaux. Une idée par puce."
    ),
}


def donnees_ecran(ecran: str, injectables: Injectables) -> dict[str, Any]:
    """Extrait la tranche de données injectables destinée à un écran.

    Une chaîne ne voit rien d'autre : c'est la garantie qu'elle ne peut pas citer
    un chiffre appartenant à un autre écran.

    Args:
        ecran: Identifiant de l'écran.
        injectables: Données injectables complètes.

    Returns:
        Le dictionnaire des données de l'écran.
    """
    if ecran == ECRAN_DECISION:
        return {
            "decision": injectables.decision_libelle,
            "verdict_calcule": injectables.ligne_verdict,
            "faits_cles": injectables.faits_cles_decision,
            "risque_principal": injectables.risque_principal_decision,
        }
    if ecran == ECRAN_CONSOMMATEUR:
        return {
            "besoins": injectables.tableau_besoins,
            "attentes": injectables.tableau_attentes,
            "points_de_friction_dans_l_ordre": [
                {"titre": p["libelle"], "ce_que_dit_le_corpus": p.get("description", "")}
                for p in injectables.pain_points
            ],
            "sentiment_par_source": injectables.tableau_sentiment,
            "divergences_entre_sources": injectables.divergences,
        }
    if ecran == ECRAN_CONCURRENCE:
        return {
            "indicateurs_de_demande": injectables.dynamique_demande,
            "intensite": injectables.tableau_intensite,
            "concurrents": [
                {cle: valeur for cle, valeur in ligne.items() if cle != "force_brute"}
                for ligne in injectables.concurrents_v2
            ],
            "benchmark_prix": injectables.tableau_benchmark,
            "portee_regionale_des_sources": injectables.portee_regionale,
            "standards_observes": injectables.normes_marche,
            "clientele_cible": "",
        }
    if ecran == ECRAN_RECOMMANDATIONS:
        return {
            "fourchette_proposee": injectables.fourchette_prix,
            "conditions_de_prix": injectables.conditions_prix,
            "actions_prioritaires": [
                a.get("enonce") or a.get("enonce_brut", "")
                for a in injectables.actions_p1
            ],
            "angles_satures_a_eviter": injectables.normes_marche,
        }
    return {}


def sous_blocs_a_rediger(ecran: str, injectables: Injectables) -> list[str]:
    """Détermine les sous-blocs qu'il reste à confier au modèle.

    Un sous-bloc qui affiche sa phrase standard, faute de donnée, n'est pas
    rédigé : demander au modèle de commenter une absence l'inviterait à la
    combler.

    Args:
        ecran: Identifiant de l'écran.
        injectables: Données injectables.

    Returns:
        Les identifiants de sous-blocs à rédiger.
    """
    return [
        sous_bloc
        for sous_bloc in SOUS_BLOCS_REDIGES.get(ecran, ())
        if sous_bloc not in injectables.sous_blocs_standards
    ]


def rediger_ecran(
    ecran: str,
    titre: str,
    budget_mots: int,
    injectables: Injectables,
    produit: str,
    marche: str,
    langue_analyse: str,
    consigne_supplementaire: str = "",
) -> tuple[SortieEcran | None, StatutAnalyse]:
    """Rédige les puces d'un écran, sous-bloc par sous-bloc.

    Args:
        ecran: Identifiant de l'écran.
        titre: Titre affiché de l'écran.
        budget_mots: Budget de mots de l'écran.
        injectables: Données injectables complètes.
        produit: Nom du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.
        consigne_supplementaire: Consigne ajoutée lors d'une régénération.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    demandes = sous_blocs_a_rediger(ecran, injectables)
    if not demandes:
        return SortieEcran(), StatutAnalyse(
            phase=f"redaction_{ecran}",
            succes=True,
            message_erreur=(
                "aucun sous-bloc à rédiger : tous affichent leur phrase standard, "
                "faute de donnée exploitable."
            ),
            nb_elements=0,
        )

    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_ECRAN), ("human", _HUMAIN_ECRAN)]
    )
    chaine = gabarit | modele.with_structured_output(SortieEcran)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit": produit,
            "marche": marche,
            "langue_analyse": langue_analyse,
            "titre_ecran": titre,
            "budget_mots": budget_mots,
            "badge": injectables.badges.get(ecran, "non qualifié"),
            "consignes_sous_blocs": "\n".join(
                _CONSIGNES_SOUS_BLOCS[sous_bloc] for sous_bloc in demandes
            )
            + consigne_supplementaire,
            "donnees": json.dumps(
                donnees_ecran(ecran, injectables), ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
        f"redaction_{ecran}",
    )
    if resultat is not None:
        # Un sous-bloc non demandé serait affiché nulle part : le retirer évite
        # qu'il pèse sur le budget de mots sans jamais atteindre le lecteur.
        resultat.sous_blocs = {
            cle: puces for cle, puces in resultat.sous_blocs.items() if cle in demandes
        }
    return resultat, StatutAnalyse(
        phase=f"redaction_{ecran}",
        succes=resultat is not None,
        message_erreur=erreur,
        nb_elements=sum(len(p) for p in resultat.sous_blocs.values()) if resultat else 0,
        nb_tentatives=tentatives,
    )


_SYSTEME_COMPRESSION = (
    "Tu raccourcis des cellules de tableau pour un rapport d'étude de marché.\n\n"
    "Langue : {langue_analyse}.\n\n"
    "RÈGLES — elles sont vérifiées sur ta sortie :\n"
    "- Renvoie EXACTEMENT autant de textes qu'on t'en donne, dans le MÊME ORDRE.\n"
    "- Chaque version raccourcie fait au plus {max_mots} mots.\n"
    "- Tu ne peux employer AUCUN chiffre qui ne figure pas déjà dans le texte "
    "d'origine correspondant. Aucun calcul, aucun arrondi, aucune conversion. Un "
    "chiffre ajouté fait rejeter toute la compression.\n"
    "- Tu conserves l'ARGUMENT : ce qui disparaît est le remplissage, jamais la "
    "raison. Mieux vaut une formule sèche qu'une phrase élégante amputée de son "
    "fait.\n"
    "- Aucun superlatif ajouté, aucun adoucissement, aucune connaissance externe."
    "{erreur_precedente}"
)

_HUMAIN_COMPRESSION = "TEXTES À RACCOURCIR, DANS L'ORDRE\n{textes}"


def compresser_cellules(
    textes: list[str], max_mots: int, langue_analyse: str
) -> tuple[list[str] | None, StatutAnalyse]:
    """Raccourcit des textes de cellule sans les tronquer à « … ».

    Args:
        textes: Textes d'origine, dans l'ordre.
        max_mots: Nombre maximal de mots par texte produit.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(textes_compresses_ou_None, statut)`. La liste renvoyée a
        toujours la longueur des textes fournis, ou vaut `None`.
    """
    if not textes:
        return [], StatutAnalyse(phase="compression_cellules", succes=True, nb_elements=0)

    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_COMPRESSION), ("human", _HUMAIN_COMPRESSION)]
    )
    chaine = gabarit | modele.with_structured_output(SortieCompression)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "langue_analyse": langue_analyse,
            "max_mots": max_mots,
            "textes": json.dumps(textes, ensure_ascii=False, separators=(",", ":")),
        },
        "compression_cellules",
    )
    if resultat is None or len(resultat.textes) != len(textes):
        return None, StatutAnalyse(
            phase="compression_cellules",
            succes=False,
            message_erreur=(
                erreur
                or (
                    f"compression rejetée : {len(resultat.textes) if resultat else 0} "
                    f"texte(s) renvoyé(s) pour {len(textes)} demandé(s)."
                )
            ),
            nb_elements=len(textes),
            nb_tentatives=tentatives,
        )
    return resultat.textes, StatutAnalyse(
        phase="compression_cellules",
        succes=True,
        nb_elements=len(resultat.textes),
        nb_tentatives=tentatives,
    )


MAX_MOTS_PUCE_PAR_SOUS_BLOC: dict[str, int] = {
    SB_QUE_FONT: MAX_MOTS_PUCE_CONCURRENTS,
}
"""Budget de mots par puce, quand il s'écarte de la valeur commune."""


def budget_puce(sous_bloc: str) -> int:
    """Donne le budget de mots d'une puce.

    Args:
        sous_bloc: Identifiant du sous-bloc.

    Returns:
        Le nombre maximal de mots admis pour une puce de ce sous-bloc.
    """
    return MAX_MOTS_PUCE_PAR_SOUS_BLOC.get(sous_bloc, MAX_MOTS_PUCE)
