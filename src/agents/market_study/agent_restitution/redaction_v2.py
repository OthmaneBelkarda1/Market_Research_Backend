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
import logging
import re
import time
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from config import (
    CONTRAT_SOUS_BLOCS,
    ECRAN_CONCURRENCE,
    ECRAN_CONSOMMATEUR,
    ECRAN_DECISION,
    ECRAN_RECOMMANDATIONS,
    LIBELLES_QUE_FONT,
    MAX_MOTS_PUCE,
    MAX_MOTS_PUCE_CONCURRENTS,
    NB_FAITS_CLES_DECISION,
    NB_POINTS_FRICTION,
    PHRASE_CLIENTELE_NON_CARACTERISEE,
    REGLES_FORMULATION,
    REGLES_FORMULATION_V2,
    REGLES_LANGUE_V21,
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
    ConfigurationRedactionInvalide,
    RedactionImpossible,
    construire_modele,
    lexique_pour_prompt,
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
    "Niveau de fiabilité : {badge}. Une fiabilité faible impose la prudence : "
    "« les données disponibles suggèrent », « d'après les avis analysés », jamais "
    "l'affirmation.\n\n"
    # Le lexique passe AVANT les règles de forme : c'est la contrainte la plus
    # souvent oubliée, et un modèle applique mieux ce qu'il a lu en premier.
    + REGLES_LANGUE_V21.format(lexique_impose=lexique_pour_prompt())
    + "\n\n"
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
        "fourni. Quand le fait clé porte un chiffre, ta puce le RECOPIE "
        "EXACTEMENT ; quand il n'en porte aucun — un constat qualitatif, un "
        "booléen — n'en invente surtout pas. Tu reformules pour la lisibilité, "
        "tu n'ajoutes rien."
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
        "puce : "
        + " / ".join(f"**{libelle}**" for libelle in LIBELLES_QUE_FONT)
        + ". Chaque puce fait au plus "
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


logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Invocation : trois familles d'erreur, trois conduites
# --------------------------------------------------------------------------- #
# Le v1 traite toute erreur de la même façon : deux tentatives, puis un repli.
# Sur une panne réseau c'est la bonne conduite. Sur un gabarit cassé, c'est deux
# fois le même échec au mot près — le run 8609db9e a rejoué huit invocations
# identiquement perdues avant de rendre un rapport vide. Une erreur qui ne dépend
# pas du réseau ne se retente pas : elle se signale.
#
# `invoquer_structure` reste intacte dans `config.py` : le v1 s'en sert, et son
# contrat ne change pas.

NB_TENTATIVES_TRANSITOIRE: int = 3
BACKOFF_SECONDES: tuple[float, ...] = (2.0, 8.0)
"""Attentes entre les tentatives. Exponentiel : un 429 se répare en patientant,
pas en réessayant tout de suite."""

_ERREURS_TRANSITOIRES: frozenset[str] = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "APIResponseValidationError",
    "InternalServerError",
    "OverloadedError",
    "RateLimitError",
    "ServiceUnavailableError",
})
"""Erreurs du SDK Anthropic qui se réparent en réessayant.

Reconnues par leur NOM plutôt que par `isinstance` : `anthropic` est une
dépendance transitive de `langchain_anthropic`, et ce module ne doit pas
échouer à l'import parce qu'une version en a déplacé une classe."""


def _est_transitoire(erreur: Exception) -> bool:
    """Dit si une erreur d'invocation a une chance d'aboutir en réessayant.

    Args:
        erreur: Exception levée par la chaîne.

    Returns:
        `True` si une nouvelle tentative a un sens.
    """
    if isinstance(erreur, TimeoutError | ConnectionError):
        return True
    if type(erreur).__name__ not in _ERREURS_TRANSITOIRES:
        return False
    # Un `APIStatusError` porte son code : 429 et 5xx se retentent, 400 et 401
    # sont des défauts de requête ou de clé, que la patience n'arrange pas.
    code = getattr(erreur, "status_code", None)
    return code is None or code == 429 or 500 <= code < 600


def _est_deterministe(erreur: Exception) -> bool:
    """Dit si une erreur redonnera exactement la même chose à la tentative suivante.

    `KeyError` est ici la signature du défaut 8609db9e : `ChatPromptTemplate` la
    lève quand une variable du gabarit n'est pas fournie, et l'entrée soumise à
    la reprise étant la même, la reprise échoue à l'identique.

    Args:
        erreur: Exception levée par la chaîne.

    Returns:
        `True` si aucune reprise ne peut aboutir.
    """
    return isinstance(
        erreur, KeyError | ConfigurationRedactionInvalide | TypeError | AttributeError
    )


def invoquer_ecran(chaine: Any, variables: dict[str, Any], libelle: str) -> Any:
    """Invoque une chaîne de rédaction v2, en distinguant les causes d'échec.

    Args:
        chaine: Chaîne LCEL à sortie structurée.
        variables: Variables du gabarit, `erreur_precedente` comprise.
        libelle: Libellé de l'étape, pour la journalisation.

    Returns:
        La sortie structurée.

    Raises:
        RedactionImpossible: Erreur déterministe — aucune reprise n'est tentée —
            ou transitoire persistante après `NB_TENTATIVES_TRANSITOIRE` essais.
    """
    derniere_erreur: str | None = None
    for tentative in range(1, NB_TENTATIVES_TRANSITOIRE + 1):
        try:
            resultat = chaine.invoke(variables)
        except Exception as erreur:  # noqa: BLE001 — reclassée juste en dessous
            derniere_erreur = f"{type(erreur).__name__} : {erreur}"
            if _est_deterministe(erreur):
                raise RedactionImpossible(
                    f"{libelle} — erreur déterministe, aucune reprise ne peut "
                    f"aboutir : {derniere_erreur}"
                ) from erreur
            if not _est_transitoire(erreur) or tentative == NB_TENTATIVES_TRANSITOIRE:
                raise RedactionImpossible(
                    f"{libelle} — échec après {tentative} tentative(s) : "
                    f"{derniere_erreur}"
                ) from erreur
            attente = BACKOFF_SECONDES[min(tentative - 1, len(BACKOFF_SECONDES) - 1)]
            logger.warning(
                "%s — tentative %d/%d en échec (%s), reprise dans %.0f s",
                libelle, tentative, NB_TENTATIVES_TRANSITOIRE, derniere_erreur, attente,
            )
            time.sleep(attente)
            continue
        if resultat is None:
            derniere_erreur = "le modèle n'a retourné aucune sortie structurée"
            if tentative == NB_TENTATIVES_TRANSITOIRE:
                raise RedactionImpossible(f"{libelle} — {derniere_erreur}")
            continue
        logger.debug("%s — succès en %d tentative(s)", libelle, tentative)
        return resultat
    raise RedactionImpossible(f"{libelle} — {derniere_erreur}")


MAX_MOTS_PUCE_PAR_ECRAN: dict[str, int] = {ECRAN_CONCURRENCE: MAX_MOTS_PUCE_CONCURRENTS}
"""Budget de mots par puce, quand un écran s'écarte de la valeur commune.

L'écran concurrence porte les cinq puces à libellé fixe, dont l'amorce en gras
consomme déjà deux mots."""


def max_mots_ecran(ecran: str) -> int:
    """Donne le budget de mots d'une puce de cet écran.

    Args:
        ecran: Identifiant de l'écran.

    Returns:
        Le nombre maximal de mots admis pour une puce.
    """
    return MAX_MOTS_PUCE_PAR_ECRAN.get(ecran, MAX_MOTS_PUCE)


def variables_ecran(
    ecran: str,
    *,
    titre: str = "",
    budget_mots: int = 0,
    badge: str = "",
    consignes_sous_blocs: str = "",
    donnees: str = "",
    produit: str = "",
    marche: str = "",
    langue_analyse: str = "",
    erreur_precedente: str = "",
) -> dict[str, Any]:
    """Construit le dictionnaire d'invocation d'une chaîne d'écran.

    LE POINT DE CE CORRECTIF. Les quatre chaînes partageaient un prompt système
    et quatre dictionnaires écrits à la main, dont aucun ne portait `max_mots` :
    la variable venait de `REGLES_FORMULATION_V2`, concaténée dans le système,
    et personne ne l'y avait cherchée. Les quatre chaînes du run 8609db9e ont
    échoué sur le même `KeyError`, deux fois chacune, et le rapport est parti
    sans une phrase.

    Il n'y a donc plus qu'un seul endroit où ce dictionnaire se construit, et
    `_verifier_contrat_invocation` compare ses clés aux variables du gabarit au
    chargement du module. Ajouter un `{marqueur}` dans un prompt sans l'injecter
    ici casse l'import, pas l'étude.

    Les valeurs par défaut ne servent qu'à cette vérification : une invocation
    réelle les fournit toutes.

    Args:
        ecran: Identifiant de l'écran, qui fixe le budget de mots par puce.
        titre: Titre affiché de l'écran.
        budget_mots: Budget total de l'écran.
        badge: Niveau de fiabilité de l'écran.
        consignes_sous_blocs: Consignes des sous-blocs à produire.
        donnees: Données de l'écran, sérialisées.
        produit: Nom du produit étudié.
        marche: Libellé du marché.
        langue_analyse: Code langue de rédaction.
        erreur_precedente: Consigne de reprise, déjà mise en forme. Vide à la
            première rédaction.

    Returns:
        Le dictionnaire complet des variables du gabarit d'écran.
    """
    return {
        "produit": produit,
        "marche": marche,
        "langue_analyse": langue_analyse,
        "titre_ecran": titre,
        "budget_mots": budget_mots,
        "badge": badge,
        "max_mots": max_mots_ecran(ecran),
        "consignes_sous_blocs": consignes_sous_blocs,
        "donnees": donnees,
        "erreur_precedente": erreur_precedente,
    }


def _consigne_chiffres_pourquoi(injectables: Injectables) -> str:
    """Dit au modèle, fait par fait, quel chiffre sa puce doit recopier.

    La consigne était statique — « chaque puce porte au moins un chiffre » — là
    où l'exigence dépend des données : un fait clé qualitatif n'en porte aucun, et
    un fait chiffré peut n'en porter qu'un, noyé dans une phrase. Le modèle
    laissait tomber le chiffre d'un fait sur trois, l'écran était rejeté, et la
    régénération repartait sur la même consigne vague.

    Le code extrait donc les chiffres de chaque fait et les nomme. Il n'en
    fabrique aucun : ce sont ceux du fait, dans son écriture d'origine, celle que
    la liste blanche admettra.

    Args:
        injectables: Données injectables.

    Returns:
        Le complément de consigne, vide s'il n'y a aucun fait clé.
    """
    faits = list(injectables.faits_cles_decision)
    if not faits:
        return ""
    lignes = []
    for rang, fait in enumerate(faits, start=1):
        chiffres = [trouve.strip() for trouve in _CHIFFRES_ECRITS.findall(str(fait))]
        if chiffres:
            cites = ", ".join(f"« {chiffre} »" for chiffre in dict.fromkeys(chiffres))
            lignes.append(
                f"    puce {rang} : recopie {cites} tel quel dans ta phrase."
            )
        else:
            lignes.append(
                f"    puce {rang} : ce fait ne porte AUCUN chiffre — n'en écris "
                f"aucun, la phrase reste qualitative."
            )
    return (
        "\n  CHIFFRES À REPRENDRE, dans l'ordre des faits clés :\n"
        + "\n".join(lignes)
    )


def _consigne_avec_contrat(sous_bloc: str) -> str:
    """Ajoute à la consigne d'un sous-bloc les bornes chiffrées de son contrat.

    La forme n'était dite qu'une fois, dans les règles générales du prompt
    système, à distance de la consigne qu'elle borne. Le modèle la perdait :
    « risque_principal » sortait à 49 mots pour un plafond de 30, et le contrôle
    de conformité arrêtait alors tout le module pour un défaut de forme. Rappelée
    à la ligne même du sous-bloc, la contrainte est tenue — et elle est DÉRIVÉE du
    contrat, donc consigne et contrôle ne peuvent pas diverger.

    Args:
        sous_bloc: Identifiant du sous-bloc.

    Returns:
        La consigne, suivie de ses bornes chiffrées.
    """
    consigne = _CONSIGNES_SOUS_BLOCS[sous_bloc]
    contrat = CONTRAT_SOUS_BLOCS.get(sous_bloc)
    if contrat is None:
        return consigne
    nombre = (
        f"exactement {contrat.nb_puces_min}"
        if contrat.nb_puces_min == contrat.nb_puces_max
        else f"{contrat.nb_puces_min} à {contrat.nb_puces_max}"
    )
    return (
        f"{consigne}\n"
        f"  CONTRAT VÉRIFIÉ SUR TA SORTIE — `{sous_bloc}` : {nombre} puce(s), et "
        f"CHACUNE au plus {contrat.max_mots_puce} mots. Compte-les avant de "
        f"répondre : une puce de {contrat.max_mots_puce + 1} mots fait rejeter "
        f"l'écran entier."
    )


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
            "verdict calculé": injectables.ligne_verdict,
            "faits clés": injectables.faits_cles_decision,
            "risque principal": injectables.risque_principal_decision,
        }
    if ecran == ECRAN_CONSOMMATEUR:
        return {
            "besoins": injectables.tableau_besoins,
            "attentes": injectables.tableau_attentes,
            "reproches, dans l'ordre": [
                {"titre": p["libelle"], "ce que disent les avis": p.get("description", "")}
                for p in injectables.pain_points
            ],
            "tonalité des avis par source": injectables.tableau_sentiment,
            "écarts entre sources": injectables.divergences,
        }
    if ecran == ECRAN_CONCURRENCE:
        return {
            "indicateurs de demande": injectables.dynamique_demande,
            "intensite": injectables.tableau_intensite,
            "concurrents": [
                {cle: valeur for cle, valeur in ligne.items() if cle != "force_brute"}
                for ligne in injectables.concurrents_v2
            ],
            "prix comparés": injectables.tableau_benchmark,
            # Clé en français : le modèle recopie parfois le nom d'une clé dans
            # sa phrase, et « portee_regionale_des_sources » atterrissait alors
            # tel quel sous les yeux du lecteur.
            "ce que couvre chaque source": injectables.portee_regionale,
            "standards observés": injectables.normes_marche,
            "clientèle visée": "",
        }
    if ecran == ECRAN_RECOMMANDATIONS:
        return {
            "fourchette proposée": injectables.fourchette_prix,
            "conditions de prix": injectables.conditions_prix,
            "actions prioritaires": [
                a.get("enonce") or a.get("enonce_brut", "")
                for a in injectables.actions_p1
            ],
            "angles saturés à éviter": injectables.normes_marche,
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
    erreur_precedente: str = "",
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
        erreur_precedente: Motif de rejet de la sortie précédente, transmis tel
            quel au modèle. Vide à la première rédaction.

    Returns:
        Le couple `(sortie, statut)`. `statut.succes` est faux lorsque la sortie
        s'écarte du contrat de ses sous-blocs : l'appelant régénère une fois, en
        repassant `statut.message_erreur` par `erreur_precedente`.

    Raises:
        RedactionImpossible: L'invocation n'a pas abouti — voir `invoquer_ecran`.
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

    resultat = invoquer_ecran(
        chaine,
        variables_ecran(
            ecran,
            titre=titre,
            budget_mots=budget_mots,
            badge=injectables.badges.get(ecran, "non qualifié"),
            consignes_sous_blocs="\n".join(
                _consigne_avec_contrat(sous_bloc)
                + (
                    _consigne_chiffres_pourquoi(injectables)
                    if sous_bloc == SB_POURQUOI
                    else ""
                )
                for sous_bloc in demandes
            )
            + consigne_supplementaire,
            donnees=json.dumps(
                donnees_ecran(ecran, injectables),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            produit=produit,
            marche=marche,
            langue_analyse=langue_analyse,
            erreur_precedente=(
                "\n\nATTENTION — ta réponse précédente a été rejetée pour cette "
                f"raison :\n{erreur_precedente}\nCorrige EXACTEMENT ce point : le "
                "reste de ta réponse était conforme."
                if erreur_precedente
                else ""
            ),
        ),
        f"redaction_{ecran}",
    )
    if resultat is not None:
        # Un sous-bloc non demandé serait affiché nulle part : le retirer évite
        # qu'il pèse sur le budget de mots sans jamais atteindre le lecteur.
        resultat.sous_blocs = {
            cle: puces for cle, puces in resultat.sous_blocs.items() if cle in demandes
        }
        _ramener_au_plafond(resultat, demandes)
        ecarts = ecarts_au_contrat(
            resultat, demandes, rangs_chiffrables(injectables)
        )
        if ecarts:
            return resultat, StatutAnalyse(
                phase=f"redaction_{ecran}",
                succes=False,
                message_erreur="sortie non conforme au gabarit — " + " ; ".join(ecarts),
                nb_elements=sum(len(p) for p in resultat.sous_blocs.values()),
                nb_tentatives=1,
            )
    return resultat, StatutAnalyse(
        phase=f"redaction_{ecran}",
        succes=True,
        nb_elements=sum(len(p) for p in resultat.sous_blocs.values()),
        nb_tentatives=1,
    )


_CHIFFRE = re.compile(r"\d")
_CHIFFRES_ECRITS = re.compile(r"\d+(?:[.,]\d+)?\s*%?")
"""Les chiffres d'un texte, dans leur écriture exacte.

L'écriture compte autant que la valeur : la liste blanche compare des valeurs,
mais le lecteur compare des écritures, et « 69,6 % » recopié en « 69.6% » signale
un passage par le modèle là où il ne devrait y en avoir aucun."""


def _ramener_au_plafond(sortie: SortieEcran, demandes: list[str]) -> None:
    """Retire les puces excédentaires d'un sous-bloc, par la fin.

    Une puce EN TROP se répare sans rien inventer : on garde les premières, qui
    sont celles que la consigne demandait dans l'ordre, et on retire la dernière —
    la moins prioritaire. C'est exactement ce que `couper_au_budget` fait déjà
    pour tenir les budgets de mots.

    Une puce MANQUANTE, elle, n'a aucune réparation de ce genre : la fabriquer
    reviendrait à inventer une idée que l'analyse n'a pas produite. Ce cas-là
    reste un écart, et il arrête le module.

    Args:
        sortie: Sortie structurée de l'écran, modifiée sur place.
        demandes: Sous-blocs confiés au modèle.
    """
    for sous_bloc in demandes:
        contrat = CONTRAT_SOUS_BLOCS.get(sous_bloc)
        puces = sortie.sous_blocs.get(sous_bloc)
        if contrat is None or not puces or len(puces) <= contrat.nb_puces_max:
            continue
        logger.info(
            "sous-bloc %s : %d puces pour un plafond de %d, les dernières sont retirées",
            sous_bloc, len(puces), contrat.nb_puces_max,
        )
        sortie.sous_blocs[sous_bloc] = puces[: contrat.nb_puces_max]


def rangs_chiffrables(injectables: Injectables) -> dict[str, set[int]]:
    """Dit, sous-bloc par sous-bloc, quelles puces PEUVENT porter un chiffre.

    Le gabarit demande que chaque puce de « Pourquoi » recopie un chiffre de son
    fait clé. La règle est bonne, et elle est inapplicable telle quelle : un fait
    clé amont est parfois qualitatif — « signal d'effet de mode confirmé », dont
    la valeur est un booléen. Exiger un chiffre là où la source n'en porte aucun,
    c'est demander au modèle d'en inventer un, que la liste blanche retirerait
    ensuite. Le rang est donc exigé chiffré si, et seulement si, son fait clé
    l'est. Voir l'amendement A6.

    Args:
        injectables: Données injectables.

    Returns:
        Les rangs (indexés à zéro) devant porter un chiffre, par sous-bloc.
    """
    return {
        SB_POURQUOI: {
            rang
            for rang, fait in enumerate(injectables.faits_cles_decision)
            if _CHIFFRE.search(str(fait))
        }
    }


def ecarts_au_contrat(
    sortie: SortieEcran,
    demandes: list[str],
    chiffrables: dict[str, set[int]] | None = None,
    *,
    structurels_seuls: bool = False,
) -> list[str]:
    """Confronte une sortie d'écran au contrat de ses sous-blocs.

    Le motif renvoyé est destiné à `erreur_precedente` : il dit au modèle ce
    qu'il a manqué (« 2 puces au lieu de 3 »), et non qu'il a échoué. C'est ce
    qui rend la régénération unique utile plutôt que d'être une seconde chance
    identique à la première — celle qui, dans l'incident 8609db9e, a redonné la
    même erreur au mot près.

    Args:
        sortie: Sortie structurée de l'écran.
        demandes: Sous-blocs effectivement confiés au modèle.
        chiffrables: Rangs pouvant porter un chiffre, par sous-bloc — voir
            `rangs_chiffrables`. `None` exige un chiffre partout où le contrat le
            demande, ce qui n'est correct que si toutes les sources en portent un.
        structurels_seuls: Ne relever que les écarts SANS recours — nombre de
            puces, libellés imposés, chiffre manquant, sous-bloc vide. Les
            dépassements de mots en sont exclus : ils ont une réparation
            (régénération, puis compression rédactionnelle) et ne doivent pas
            arrêter le module. Voir l'amendement A7.

    Returns:
        La liste des écarts constatés, vide si la sortie est conforme.
    """
    ecarts: list[str] = []
    for sous_bloc in demandes:
        contrat = CONTRAT_SOUS_BLOCS.get(sous_bloc)
        if contrat is None:
            continue
        puces = [p.strip() for p in sortie.sous_blocs.get(sous_bloc, []) if p.strip()]
        if not puces:
            ecarts.append(f"« {sous_bloc} » : aucune puce")
            continue
        if not contrat.nb_puces_min <= len(puces) <= contrat.nb_puces_max:
            attendu = (
                f"{contrat.nb_puces_min}"
                if contrat.nb_puces_min == contrat.nb_puces_max
                else f"{contrat.nb_puces_min} à {contrat.nb_puces_max}"
            )
            ecarts.append(
                f"« {sous_bloc} » : {len(puces)} puce(s) au lieu de {attendu}"
            )
        for rang, puce in enumerate(puces, start=1):
            if not structurels_seuls and len(puce.split()) > contrat.max_mots_puce:
                ecarts.append(
                    f"« {sous_bloc} » puce {rang} : {len(puce.split())} mots, "
                    f"maximum {contrat.max_mots_puce}"
                )
            attendus = None if chiffrables is None else chiffrables.get(sous_bloc, set())
            exige = contrat.chiffre_obligatoire and (
                attendus is None or (rang - 1) in attendus
            )
            if exige and not _CHIFFRE.search(puce):
                ecarts.append(f"« {sous_bloc} » puce {rang} : aucun chiffre")
        if contrat.libelles_fixes:
            ecarts.extend(_ecarts_libelles(sous_bloc, puces, contrat.libelles_fixes))
    return ecarts


def _ecarts_libelles(
    sous_bloc: str, puces: list[str], attendus: tuple[str, ...]
) -> list[str]:
    """Vérifie que les puces portent les libellés imposés, dans l'ordre.

    Args:
        sous_bloc: Identifiant du sous-bloc, pour le message.
        puces: Puces produites.
        attendus: Libellés imposés, dans l'ordre.

    Returns:
        Les écarts de libellé, vide si l'ordre et les intitulés sont tenus.
    """
    ecarts: list[str] = []
    for rang, attendu in enumerate(attendus):
        if rang >= len(puces):
            ecarts.append(f"« {sous_bloc} » : puce « {attendu} » manquante")
        elif f"**{attendu}**" not in puces[rang]:
            ecarts.append(
                f"« {sous_bloc} » puce {rang + 1} : doit commencer par "
                f"« **{attendu}** »"
            )
    return ecarts


def _verifier_contrat_invocation() -> None:
    """Compare les variables des gabarits de prompt aux dictionnaires fournis.

    Exécutée à l'import. Une divergence lève `ConfigurationRedactionInvalide`
    AVANT le moindre appel au modèle : le défaut qui a coûté le run 8609db9e
    aurait été une erreur de chargement, visible au premier lancement, au lieu
    d'un rapport vide livré à un décideur.

    Raises:
        ConfigurationRedactionInvalide: Si un gabarit et son dictionnaire
            d'invocation ne portent pas exactement les mêmes variables, ou si
            `CONTRAT_SOUS_BLOCS` et `SOUS_BLOCS_REDIGES` divergent.
    """
    gabarit_ecran = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_ECRAN), ("human", _HUMAIN_ECRAN)]
    )
    for ecran in SOUS_BLOCS_REDIGES:
        attendues = set(gabarit_ecran.input_variables)
        fournies = set(variables_ecran(ecran))
        if attendues != fournies:
            raise ConfigurationRedactionInvalide(
                f"gabarit de l'écran « {ecran} » : "
                f"variables attendues et non fournies {sorted(attendues - fournies)}, "
                f"fournies et inconnues du gabarit {sorted(fournies - attendues)}."
            )

    gabarit_compression = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_COMPRESSION), ("human", _HUMAIN_COMPRESSION)]
    )
    attendues = set(gabarit_compression.input_variables)
    fournies = {"langue_analyse", "max_mots", "textes", "erreur_precedente"}
    if attendues != fournies:
        raise ConfigurationRedactionInvalide(
            f"gabarit de compression : "
            f"attendues et non fournies {sorted(attendues - fournies)}, "
            f"fournies et inconnues {sorted(fournies - attendues)}."
        )

    confies = {sb for blocs in SOUS_BLOCS_REDIGES.values() for sb in blocs}
    if confies != set(CONTRAT_SOUS_BLOCS):
        raise ConfigurationRedactionInvalide(
            "CONTRAT_SOUS_BLOCS et SOUS_BLOCS_REDIGES divergent : "
            f"confiés sans contrat {sorted(confies - set(CONTRAT_SOUS_BLOCS))}, "
            f"contrat sans sous-bloc confié {sorted(set(CONTRAT_SOUS_BLOCS) - confies)}."
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

    # Une compression qui échoue n'est PAS une rédaction impossible : le texte
    # intégral prend le relais, plus long que le gabarit ne le voudrait mais
    # exact. C'est le seul endroit du v2 où un échec se dégrade, et il se dégrade
    # vers le vrai, jamais vers le vide.
    try:
        resultat = invoquer_ecran(
            chaine,
            {
                "langue_analyse": langue_analyse,
                "max_mots": max_mots,
                "textes": json.dumps(textes, ensure_ascii=False, separators=(",", ":")),
                "erreur_precedente": "",
            },
            "compression_cellules",
        )
    except RedactionImpossible as erreur:
        return None, StatutAnalyse(
            phase="compression_cellules",
            succes=False,
            message_erreur=str(erreur),
            nb_elements=len(textes),
            nb_tentatives=NB_TENTATIVES_TRANSITOIRE,
        )
    if len(resultat.textes) != len(textes):
        return None, StatutAnalyse(
            phase="compression_cellules",
            succes=False,
            message_erreur=(
                f"compression rejetée : {len(resultat.textes)} texte(s) renvoyé(s) "
                f"pour {len(textes)} demandé(s)."
            ),
            nb_elements=len(textes),
            nb_tentatives=1,
        )
    return resultat.textes, StatutAnalyse(
        phase="compression_cellules",
        succes=True,
        nb_elements=len(resultat.textes),
        nb_tentatives=1,
    )


_SYSTEME_AFFIRMATIF = (
    "Tu réécris des constats d'analyse concurrentielle pour un rapport destiné à "
    "un commerçant, en français d'affaires courant.\n\n"
    "Langue : {langue_analyse}.\n\n"
    "RÈGLES — elles sont vérifiées sur ta sortie :\n"
    "- Renvoie EXACTEMENT autant de textes qu'on t'en donne, dans le MÊME ORDRE.\n"
    "- Chaque texte passe à la FORME AFFIRMATIVE. « Aucune annonce ne met en avant "
    "la garantie » devient « Personne ne met en avant la garantie ». La double "
    "négation est la seule chose que tu changes de fond.\n"
    "- Tu ne peux employer AUCUN chiffre qui ne figure pas déjà dans le texte "
    "d'origine correspondant. Aucun calcul, aucun arrondi. Un chiffre ajouté fait "
    "rejeter toute la réécriture.\n"
    "- Tu ne retires AUCUNE réserve de méthode : « non observé dans les données "
    "collectées » dit que le constat porte sur ce qui a été vu, pas sur le marché "
    "entier. Cette nuance reste.\n"
    "- Au plus {max_mots} mots par texte."
    "{erreur_precedente}"
)

_HUMAIN_AFFIRMATIF = "CONSTATS À RÉÉCRIRE, DANS L'ORDRE\n{textes}"


def reformuler_affirmatif(
    textes: list[str], max_mots: int, langue_analyse: str
) -> tuple[list[str] | None, StatutAnalyse]:
    """Passe des constats à la forme affirmative.

    L'analyse concurrentielle publie ses angles inexploités en double négation :
    « Aucun claim publicitaire du corpus ne met en avant le prix… hormis un seul
    annonceur ». Il faut relire deux fois pour savoir si quelqu'un le fait ou non.
    « Personne ne met en avant le prix, sauf un annonceur » dit la même chose et
    se comprend du premier coup.

    L'échec n'est pas fatal : les constats d'origine sont conservés. Ils sont
    lourds, ils restent exacts — et c'est un texte d'analyse amont, que le module
    n'a pas vocation à perdre.

    Args:
        textes: Constats d'origine, dans l'ordre.
        max_mots: Nombre maximal de mots par texte produit.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(textes_réécrits_ou_None, statut)`.
    """
    if not textes:
        return [], StatutAnalyse(phase="reformulation_affirmative", succes=True)

    modele = construire_modele()
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_AFFIRMATIF), ("human", _HUMAIN_AFFIRMATIF)]
    )
    chaine = gabarit | modele.with_structured_output(SortieCompression)

    try:
        resultat = invoquer_ecran(
            chaine,
            {
                "langue_analyse": langue_analyse,
                "max_mots": max_mots,
                "textes": json.dumps(textes, ensure_ascii=False, separators=(",", ":")),
                "erreur_precedente": "",
            },
            "reformulation_affirmative",
        )
    except RedactionImpossible as erreur:
        return None, StatutAnalyse(
            phase="reformulation_affirmative",
            succes=False,
            message_erreur=str(erreur),
            nb_elements=len(textes),
        )
    if len(resultat.textes) != len(textes):
        return None, StatutAnalyse(
            phase="reformulation_affirmative",
            succes=False,
            message_erreur=(
                f"réécriture rejetée : {len(resultat.textes)} texte(s) renvoyé(s) "
                f"pour {len(textes)} demandé(s)."
            ),
            nb_elements=len(textes),
        )
    return resultat.textes, StatutAnalyse(
        phase="reformulation_affirmative",
        succes=True,
        nb_elements=len(resultat.textes),
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


# Le contrat est vérifié au chargement, jamais à la première invocation : une
# étude qui atteint la phase de restitution a déjà payé six collecteurs et quatre
# analyses, et c'est le pire moment pour découvrir une faute de gabarit.
_verifier_contrat_invocation()
