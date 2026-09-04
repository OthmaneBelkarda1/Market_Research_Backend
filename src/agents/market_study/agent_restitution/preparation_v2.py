"""Préparation propre au gabarit v2 — **code pur, aucun appel LLM ici**.

Ce module ne remplace pas `preparation.py` : il le PROLONGE. `preparer()` construit
les injectables communs aux deux gabarits — verdict, bascules simulées, badges,
liste blanche, annexe — et `enrichir()` y ajoute ce que le seul rapport
décisionnel consomme : la ligne de décision, les sélections bornées, le tableau
des cinq forces, les lignes standard des sous-blocs sans donnée.

Trois principes s'appliquent ici comme partout dans cet agent :

1. **Rien n'est inventé.** Un sous-bloc sans donnée exploitable affiche sa phrase
   standard et le cas est tracé dans `statuts_analyse` ; il ne reçoit jamais un
   contenu déduit.
2. **Aucune troncature à « … ».** Les textes trop longs sont soit coupés au
   dernier mot entier, soit — pour les cellules de tableau — confiés à la
   compression rédactionnelle de `redaction.py`, qui ne peut pas ajouter de
   chiffre.
3. **Les estimations se déclarent.** Les trois forces estimées par règle portent
   la mention « estimation par règle », et leurs seuils sont publiés en annexe
   comme hypothèses de travail.
"""

from __future__ import annotations

import re
from typing import Any

from config import (
    BESOIN_CINQ_FORCES_AMONT,
    ECRAN_CONCURRENCE,
    ECRAN_CONSOMMATEUR,
    ECRAN_DECISION,
    ECRAN_METHODE,
    ECRAN_RECOMMANDATIONS,
    BESOIN_CLIENTELE_CIBLE,
    BESOIN_UNITES_VOLUME,
    CINQ_FORCES_SOURCE_ABSENTE,
    CINQ_FORCES_SOURCE_F5,
    CINQ_FORCES_SOURCE_REGLES,
    COLONNES_BENCHMARK_GABARIT,
    DECIMALES_MONTANT,
    ENCART_ETUDE_PARTIELLE_V2,
    INDICATEURS_DEMANDE_GABARIT,
    MOTS_OUTILS_FIN_INTERDITS,
    MENTION_SOURCE_VIDE,
    RAISONS_SOURCE_VIDE,
    SOURCES_LIGNE_SOURCES,
    TITRE_AUTRES_INDICATEURS,
    UNITES_SOURCES,
    VALEUR_NON_CALCULABLE,
    VALEUR_NON_CALCULE,
    FORCE_CLIENTS,
    FORCE_ENTREE,
    FORCE_FOURNISSEURS,
    FORCE_RIVALITE,
    FORCE_SUBSTITUTS,
    GABARIT_WIDGET_EXTRAITS,
    LECTURE_FORCE_NON_EVALUEE,
    LIBELLES_CINQ_FORCES,
    LIBELLES_ENTREES,
    LIBELLES_VERDICT,
    MAX_MOTS_ACTION,
    MAX_MOTS_CELLULE_COURTE,
    MAX_MOTS_PUCE,
    MAX_MOTS_PUCE_CONCURRENTS,
    MENTION_ESTIMATION_REGLE,
    NB_ACTIONS_P1,
    NB_FAITS_CLES_DECISION,
    NB_MANQUES_DECISION,
    NB_OPPORTUNITES,
    NB_POINTS_FRICTION,
    NB_RISQUES,
    NB_VERBATIMS,
    NIVEAU_ELEVE,
    NIVEAU_FAIBLE,
    NIVEAU_MOYEN,
    NIVEAU_NON_EVALUE,
    NB_CONCURRENTS_TABLEAU_V2,
    PHASES_PLC,
    PHRASE_AUCUNE_BASCULE,
    PHRASE_GO_CONDITIONNEL_SANS_CONDITION,
    PHRASE_NON_DOCUMENTE,
    PHRASE_PLC_NON_EVALUEE,
    PHRASE_TENDANCES_ABSENTES,
    SB_AIMERAIENT,
    SB_APPRECIENT,
    SB_DERANGE,
    SB_DYNAMIQUE,
    SB_PERSONNE_NE_FAIT,
    SB_PHASE,
    SB_POURQUOI_ACHAT,
    SB_PRIX,
    SB_PRIX_PRATIQUES,
    SB_QUE_FONT,
    SCORE_MAX_CRITERE,
    SECTION_ANNEXE,
    SECTION_CONCURRENCE,
    SECTION_CONSOMMATEURS,
    SECTION_PLC,
    SECTION_RECOMMANDATIONS,
    SECTION_SYNTHESE,
    SEUILS_CINQ_FORCES,
    SEUIL_PRIX_ENTREE_FACILE,
    SOURCES_WIDGETS,
    UNITES_VOLUME,
    UNITE_VOLUME_INDETERMINEE,
    VERDICT_INDETERMINE,
    logger,
)
from preparation import (
    _valeur_lisible,
    depouiller_injectables,
    formater_montant,
    formater_nombre,
    selectionner_verbatim,
    tableau,
)
from schemas import Injectables, StatutAnalyse

PHASE_PREPARATION_V2: str = "preparation_v2"

SECTIONS_DU_V1: dict[str, tuple[str, ...]] = {
    ECRAN_DECISION: (SECTION_SYNTHESE,),
    ECRAN_CONSOMMATEUR: (SECTION_CONSOMMATEURS,),
    ECRAN_CONCURRENCE: (SECTION_CONCURRENCE,),
    ECRAN_RECOMMANDATIONS: (SECTION_RECOMMANDATIONS, SECTION_PLC),
    ECRAN_METHODE: (SECTION_ANNEXE,),
}
"""Écran v2 → sections v1 dont il reprend le contenu.

Badges, mentions d'étude partielle et références de traçabilité sont construits
par `preparation.py` sur les identifiants du v1. Plutôt que de dupliquer ce
travail, les écrans v2 les héritent : la première section renseignée gagne.
"""


def alias_v2(source: dict, cible_defaut=None) -> dict:
    """Recopie un dictionnaire indexé par section v1 sur les écrans v2.

    Args:
        source: Dictionnaire indexé par identifiant de section v1.
        cible_defaut: Valeur retenue quand aucune section ne renseigne l'écran.

    Returns:
        Le dictionnaire enrichi des clés d'écran v2.
    """
    enrichi = dict(source)
    for ecran, sections in SECTIONS_DU_V1.items():
        for section in sections:
            if source.get(section):
                enrichi[ecran] = source[section]
                break
        else:
            if cible_defaut is not None:
                enrichi[ecran] = cible_defaut
    return enrichi

INTENSITE_MAX_F3: int = 3
"""Borne haute de l'échelle d'intensité de l'analyse des avis.

**L'échelle va de 1 à 3** — « 1 = gêne, 2 = problème net, 3 = rédhibitoire » —,
et non de 1 à 5 comme le supposait la spécification du gabarit v2. Afficher
« 2,22/5 » ferait lire comme faible une intensité qui est en réalité au-dessus du
milieu de son échelle. Le dénominateur affiché est donc celui de la source.
"""

MOTS_FRICTION_PRIX_ACCES: tuple[str, ...] = (
    "prix",
    "cher",
    "chers",
    "chère",
    "chères",
    "coût",
    "coûts",
    "cout",
    "couts",
    "tarif",
    "tarifs",
    "budget",
    "budgets",
    "livraison",
    "livraisons",
    "délai",
    "délais",
    "delai",
    "delais",
    "rupture",
    "ruptures",
    "stock",
    "stocks",
    "accès",
    "acces",
    "remboursement",
    "remboursements",
)
"""Marqueurs d'un point de friction de nature économique ou d'accès, mots ENTIERS.

Ils servent la seule règle des cinq forces qui a besoin de lire du texte, et le
test s'est trompé deux fois avant d'être ancré correctement :

- une recherche par sous-chaîne trouvait « cher » dans « recherche » ;
- un ancrage sur le seul début de mot trouvait « cout » dans « couture ».

Les deux erreurs classaient un point de friction purement fonctionnel en friction
de prix, et faisaient remonter « pouvoir des clients » d'un cran sans qu'aucune
donnée économique ne le justifie. Les formes fléchies sont donc énumérées plutôt
que devinées.
"""

RADICAUX_FRICTION_PRIX_ACCES: tuple[str, ...] = ("disponib", "indisponib")
"""Radicaux dont les formes fléchies sont trop nombreuses pour être listées.

« disponib » couvre disponible, disponibles, disponibilité — et aucun autre mot
français ne commence ainsi, ce qui rend le radical sûr. Tout marqueur dont le
radical est ambigu appartient à la liste des mots entiers, jamais ici.
"""

MOTIF_FRICTION_PRIX_ACCES = re.compile(
    r"\b(?:"
    + "|".join(re.escape(mot) for mot in MOTS_FRICTION_PRIX_ACCES)
    + r")\b|\b(?:"
    + "|".join(re.escape(radical) for radical in RADICAUX_FRICTION_PRIX_ACCES)
    + r")\w*",
    re.IGNORECASE,
)

LIBELLES_SOURCES: dict[str, str] = {
    "amazon": "Amazon",
    "aliexpress": "AliExpress",
    "reddit": "Reddit",
    "recherche_web": "Web",
    "meta_ads": "Publicité",
    "google_trends": "Tendances",
    # `tendances` est le nom historique de la même source : l'orchestrateur
    # nomme son collecteur `google_trends`, et les deux clés doivent rendre le
    # même libellé tant que les deux vocabulaires coexistent.
    "tendances": "Tendances",
}


# =========================================================================== #
# Outils de forme
# =========================================================================== #


def couper_mots(texte: str, max_mots: int) -> str:
    """Coupe un texte à un nombre de mots, sans ellipse finale.

    La troncature à « … » du gabarit v1 perdait l'argument sans le dire. Ici le
    texte s'arrête sur un mot entier ; quand il s'agit d'une cellule de tableau,
    c'est la compression rédactionnelle qui est appelée d'abord, et cette coupe
    n'est que le repli.

    Args:
        texte: Texte source.
        max_mots: Nombre maximal de mots conservés.

    Returns:
        Le texte coupé, sans ponctuation de troncature.
    """
    mots = " ".join((texte or "").split()).split(" ")
    if len(mots) <= max_mots:
        return " ".join(mots)
    return " ".join(mots[:max_mots]).rstrip(" ,;:—-")


def compter_mots(texte: str) -> int:
    """Compte les mots d'un texte.

    Args:
        texte: Texte à mesurer.

    Returns:
        Le nombre de mots.
    """
    return len((texte or "").split())


ABREVIATIONS_SANS_FIN_DE_PHRASE: frozenset[str] = frozenset(
    {"ex", "cf", "p", "pp", "env", "art", "fig", "n", "no", "vs", "etc", "min", "max"}
)
"""Mots dont le point n'achève pas une phrase.

Le point d'une abréviation était pris pour une fin de phrase : une atténuation
de risque est sortie sur « … et définir un critère d'arrêt (ex. » — coupée juste
avant l'exemple qui en faisait tout l'intérêt, et sans que rien ne le signale."""


def _position_separateur(texte: str, separateur: str) -> int:
    """Trouve la première occurrence d'un séparateur qui termine vraiment.

    Args:
        texte: Texte à examiner.
        separateur: Séparateur cherché.

    Returns:
        La position retenue, ou -1 si aucune n'est valable.
    """
    depart = 0
    while True:
        position = texte.find(separateur, depart)
        if position < 0:
            return -1
        if separateur != ". ":
            return position
        dernier = texte[:position].rsplit(" ", 1)[-1].strip("(«\"'")
        if dernier.lower() not in ABREVIATIONS_SANS_FIN_DE_PHRASE:
            return position
        depart = position + 1


def premiere_phrase(texte: str, max_mots: int | None = None) -> str:
    """Ramène un texte à sa première phrase, plafonnée en nombre de mots.

    Les énoncés amont sont construits pour un dossier d'analyse : une consigne,
    puis sa justification (« … POURQUOI : … »). Un écran de décision n'a besoin
    que de la consigne ; la justification reste disponible dans les analyses
    sources, que l'écran méthode référence.

    Args:
        texte: Texte source.
        max_mots: Plafond de mots, ou `None` pour ne pas couper. `None` est le
            cas des porteurs de sens indivisible — titres d'opportunité, de
            risque, énoncés d'action : la première phrase suffit à les borner, et
            une coupe au n-ième mot leur fait dire autre chose.

    Returns:
        La première phrase, coupée au mot entier si un plafond est donné.
    """
    propre = " ".join((texte or "").split())
    for separateur in (". ", " POURQUOI", " ; "):
        position = _position_separateur(propre, separateur)
        if 0 < position:
            propre = propre[: position + (1 if separateur == ". " else 0)]
            break
    if max_mots is None:
        return propre.rstrip(" ,;:")
    return couper_mots(propre, max_mots).rstrip(" ,;:")


def _bloc_replie(titre: str, contenu: str) -> str:
    """Enveloppe un contenu dans un bloc HTML repliable.

    Args:
        titre: Libellé du résumé cliquable.
        contenu: Markdown replié.

    Returns:
        Le bloc `<details>`, ou une chaîne vide si le contenu est vide.
    """
    if not (contenu or "").strip():
        return ""
    return f"<details>\n<summary>{titre}</summary>\n\n{contenu.strip()}\n\n</details>"


# =========================================================================== #
# Les cinq forces
# =========================================================================== #


def _niveau_rivalite(intensite: Any) -> tuple[str, str]:
    """Estime la rivalité actuelle à partir des volumes concurrentiels.

    Args:
        intensite: Bloc d'intensité concurrentielle, ou `None`.

    Returns:
        Le couple `(niveau, lecture)`.
    """
    if intensite is None:
        return NIVEAU_NON_EVALUE, LECTURE_FORCE_NON_EVALUEE
    concurrents = intensite.nb_concurrents_identifies
    offres = intensite.nb_offres_coeur
    if (
        concurrents >= SEUILS_CINQ_FORCES["rivalite_concurrents_eleve"]
        or offres >= SEUILS_CINQ_FORCES["rivalite_offres_eleve"]
    ):
        niveau = NIVEAU_ELEVE
    elif (
        concurrents >= SEUILS_CINQ_FORCES["rivalite_concurrents_moyen"]
        or offres >= SEUILS_CINQ_FORCES["rivalite_offres_moyen"]
    ):
        niveau = NIVEAU_MOYEN
    else:
        niveau = NIVEAU_FAIBLE
    return niveau, (
        f"{concurrents} concurrents identifiés et {offres} offres au cœur du "
        f"benchmark ({MENTION_ESTIMATION_REGLE})."
    )


def _mediane_canal_le_moins_cher(concurrence: Any) -> tuple[float | None, str, str]:
    """Trouve la médiane de prix la plus basse, sans convertir aucune devise.

    Deux prix libellés dans deux devises décrivent deux marchés : la comparaison
    ne porte que sur les médianes prises telles quelles, et la devise retenue est
    affichée avec la valeur.

    Args:
        concurrence: Analyse concurrentielle, ou `None`.

    Returns:
        Le triplet `(mediane, source, devise)` ; `mediane` vaut `None` si aucun
        benchmark n'est disponible.
    """
    if concurrence is None or not concurrence.benchmark_prix:
        return None, "", ""
    retenu = min(concurrence.benchmark_prix, key=lambda b: b.prix_mediane)
    return retenu.prix_mediane, retenu.source, retenu.devise


def _niveau_entree(concurrence: Any) -> tuple[str, str]:
    """Estime la facilité d'entrée de nouveaux concurrents.

    Args:
        concurrence: Analyse concurrentielle, ou `None`.

    Returns:
        Le couple `(niveau, lecture)`.
    """
    mediane, source, devise = _mediane_canal_le_moins_cher(concurrence)
    intensite = concurrence.intensite_concurrentielle if concurrence else None
    if mediane is None or intensite is None:
        return NIVEAU_NON_EVALUE, LECTURE_FORCE_NON_EVALUEE

    prix_bas = mediane < SEUIL_PRIX_ENTREE_FACILE
    sans_annonceur = intensite.nb_annonceurs == 0
    if prix_bas and sans_annonceur:
        niveau = NIVEAU_ELEVE
    elif prix_bas or sans_annonceur:
        niveau = NIVEAU_MOYEN
    else:
        niveau = NIVEAU_FAIBLE
    return niveau, (
        f"Médiane la plus basse à {formater_montant(mediane, devise)} sur "
        f"{LIBELLES_SOURCES.get(source, source)}, {intensite.nb_annonceurs} annonceur(s) "
        f"actif(s) ({MENTION_ESTIMATION_REGLE})."
    )


def _friction_prix_ou_acces(insights: Any) -> str:
    """Cherche un point de friction de nature économique ou d'accès.

    Args:
        insights: Analyse des avis et discussions, ou `None`.

    Returns:
        Le libellé du premier point de friction concerné, ou une chaîne vide.
    """
    if insights is None:
        return ""
    for point in insights.pain_points:
        texte = f"{point.libelle} {point.description}"
        if MOTIF_FRICTION_PRIX_ACCES.search(texte):
            return point.libelle
    return ""


def _niveau_clients(concurrence: Any, insights: Any) -> tuple[str, str]:
    """Estime le pouvoir des clients.

    Args:
        concurrence: Analyse concurrentielle, ou `None`.
        insights: Analyse des avis et discussions, ou `None`.

    Returns:
        Le couple `(niveau, lecture)`.
    """
    intensite = concurrence.intensite_concurrentielle if concurrence else None
    if intensite is None and insights is None:
        return NIVEAU_NON_EVALUE, LECTURE_FORCE_NON_EVALUEE

    friction = _friction_prix_ou_acces(insights)
    offres = intensite.nb_offres_coeur if intensite else 0
    choix_large = offres >= SEUILS_CINQ_FORCES["clients_offres_coeur"]
    if friction and choix_large:
        niveau = NIVEAU_ELEVE
    elif friction or choix_large:
        niveau = NIVEAU_MOYEN
    else:
        niveau = NIVEAU_FAIBLE

    if friction:
        motif = f"point de friction économique ou d'accès documenté (« {friction} »)"
    else:
        motif = "aucun point de friction de nature économique ou d'accès documenté"
    return niveau, (
        f"{offres} offres au cœur du benchmark, {motif} ({MENTION_ESTIMATION_REGLE})."
    )


def construire_cinq_forces(
    recommandations: Any, concurrence: Any, insights: Any
) -> tuple[str, str, list[str]]:
    """Construit le tableau des cinq forces et déclare son origine.

    Les cinq lignes sont TOUJOURS présentes : une force non couverte s'affiche
    « non évalué » avec sa lecture standard. Une ligne absente laisserait croire
    que la force a été jugée sans intérêt.

    Args:
        recommandations: Analyse de synthèse.
        concurrence: Analyse concurrentielle, ou `None`.
        insights: Analyse des avis et discussions, ou `None`.

    Returns:
        Le triplet `(tableau_markdown, origine, hypotheses)`.
    """
    entetes = ["Force", "Niveau", "Lecture"]
    publiees = getattr(recommandations, "cinq_forces", None)
    if publiees:
        par_force = {item.force: item for item in publiees}
        lignes = []
        for cle, libelle in LIBELLES_CINQ_FORCES:
            item = par_force.get(cle)
            lignes.append(
                [
                    libelle,
                    item.niveau if item else NIVEAU_NON_EVALUE,
                    item.justification if item else LECTURE_FORCE_NON_EVALUEE,
                ]
            )
        return tableau(entetes, lignes), CINQ_FORCES_SOURCE_F5, []

    if concurrence is None and insights is None:
        lignes = [
            [libelle, NIVEAU_NON_EVALUE, LECTURE_FORCE_NON_EVALUEE]
            for _, libelle in LIBELLES_CINQ_FORCES
        ]
        return tableau(entetes, lignes), CINQ_FORCES_SOURCE_ABSENTE, []

    estimations = {
        FORCE_RIVALITE: _niveau_rivalite(
            concurrence.intensite_concurrentielle if concurrence else None
        ),
        FORCE_ENTREE: _niveau_entree(concurrence),
        FORCE_CLIENTS: _niveau_clients(concurrence, insights),
        FORCE_FOURNISSEURS: (NIVEAU_NON_EVALUE, LECTURE_FORCE_NON_EVALUEE),
        FORCE_SUBSTITUTS: (NIVEAU_NON_EVALUE, LECTURE_FORCE_NON_EVALUEE),
    }
    lignes = [
        [libelle, estimations[cle][0], estimations[cle][1]]
        for cle, libelle in LIBELLES_CINQ_FORCES
    ]
    return tableau(entetes, lignes), CINQ_FORCES_SOURCE_REGLES, [
        BESOIN_CINQ_FORCES_AMONT
    ]


# =========================================================================== #
# Concurrents et volumes
# =========================================================================== #


def unite_volume(sources: list[str]) -> str:
    """Détermine l'unité du volume de ventes d'une ligne du comparatif.

    L'analyse concurrentielle publie un champ unique alors que les canaux ne
    mesurent pas la même chose : volume mensuel côté Amazon, cumul des commandes
    côté AliExpress. Quand la ligne ne porte qu'un canal, l'unité est certaine ;
    quand elle en porte plusieurs, elle est déclarée indéterminée — la deviner
    serait exactement le mélange silencieux que le rapport doit dénoncer.

    Args:
        sources: Sources où le concurrent est présent.

    Returns:
        Le suffixe d'unité à afficher.
    """
    connues = [UNITES_VOLUME[s] for s in sources if s in UNITES_VOLUME]
    if len(set(connues)) == 1:
        return connues[0]
    return UNITE_VOLUME_INDETERMINEE


def selectionner_concurrents(concurrence: Any) -> list[dict[str, str]]:
    """Retient les principaux concurrents observés, volume puis note.

    Args:
        concurrence: Analyse concurrentielle, ou `None`.

    Returns:
        Les lignes retenues, textes de force et de faiblesse encore bruts : ils
        passent ensuite par la compression rédactionnelle.
    """
    if concurrence is None or not concurrence.tableau_comparatif:
        return []
    tries = sorted(
        concurrence.tableau_comparatif,
        key=lambda c: (-(c.volume_ventes_cumule or 0), -(c.note_moyenne or 0.0)),
    )
    lignes: list[dict[str, str]] = []
    for ligne in tries[:NB_CONCURRENTS_TABLEAU_V2]:
        prix = " ; ".join(
            f"{fourchette} {devise}"
            for devise, fourchette in ligne.fourchette_prix_par_devise.items()
        )
        if ligne.volume_ventes_cumule is None:
            volume = "—"
        else:
            volume = (
                f"{formater_nombre(ligne.volume_ventes_cumule, 0)} "
                f"{unite_volume(ligne.presence_sources)}"
            )
        lignes.append(
            {
                "concurrent": ligne.concurrent,
                "canal": ", ".join(
                    LIBELLES_SOURCES.get(s, s) for s in ligne.presence_sources
                ),
                "prix": prix or "—",
                "note": formater_nombre(ligne.note_moyenne, 2)
                if ligne.note_moyenne is not None
                else "—",
                "volume": volume,
                "force_brute": ligne.force_principale or "",
                "faiblesse_brute": ligne.faiblesse_principale or "",
                "force": "",
                "faiblesse": "",
            }
        )
    return lignes


def selectionner_actions(recommandations: Any) -> tuple[list[dict[str, str]], list[Any]]:
    """Sépare les actions de priorité 1 des suivantes.

    Args:
        recommandations: Analyse de synthèse.

    Returns:
        Le couple `(actions_p1, recommandations_suivantes)`.
    """
    toutes = [
        r
        for r in (
            list(recommandations.recommandations_produit)
            + (
                [recommandations.recommandation_positionnement]
                if recommandations.recommandation_positionnement
                else []
            )
            + list(recommandations.recommandations_marketing)
        )
        if r is not None
    ]
    p1 = [r for r in toutes if (r.priorite or "P3") == "P1"][:NB_ACTIONS_P1]
    suivantes = [r for r in toutes if (r.priorite or "P3") != "P1"]
    actions = [
        {
            "id_reco": r.id_reco,
            "enonce_brut": r.enonce,
            "enonce": r.enonce
            if compter_mots(r.enonce) <= MAX_MOTS_ACTION
            else "",
            "domaine": r.domaine,
            "horizon": r.horizon,
            "effort": r.effort_estime,
            "indicateur": r.indicateurs_suivi[0] if r.indicateurs_suivi else "—",
        }
        for r in p1
    ]
    return actions, suivantes


# =========================================================================== #
# Lignes de contexte
# =========================================================================== #


# --------------------------------------------------------------------------- #
# Dynamique de la demande — quatre indicateurs, pas neuf
# --------------------------------------------------------------------------- #

MOTIFS_INDICATEURS_DEMANDE: dict[str, tuple[str, ...]] = {
    "profil": ("profil_courbe",),
    "momentum_90j": ("momentum_90j", "momentum_90_jours", "momentum"),
    "pente_5ans": ("pente_annuelle_5ans",),
    "saisonnalite": ("saisonnalite.mois_pic", "saisonnalite"),
}
"""Suffixes de `ref` reconnus pour chacun des quatre indicateurs du gabarit.

Plusieurs graphies sont acceptées pour le momentum : l'analyse amont ne le
publie pas encore, et le jour où elle le fera son nom exact n'est pas décidé.
Aucune n'est trouvée aujourd'hui, donc il s'affiche « non calculable » — ce que
le gabarit demande, et ce qui vaut mieux qu'une ligne absente que le lecteur
prendrait pour un oubli de lecture."""


def _indicateur_correspondant(elements: list, cle: str) -> Any:
    """Retrouve l'élément de dossier qui porte un indicateur du gabarit.

    Args:
        elements: Éléments de `dossier.demande.indicateurs`.
        cle: Clé du gabarit (`profil`, `momentum_90j`…).

    Returns:
        L'élément correspondant, ou `None` s'il n'est pas publié.
    """
    for suffixe in MOTIFS_INDICATEURS_DEMANDE.get(cle, ()):
        for element in elements:
            if str(getattr(element, "ref", "")).endswith(suffixe):
                return element
    return None


def construire_dynamique_demande(dossier: Any) -> tuple[str, str]:
    """Construit le tableau des quatre indicateurs, et le repli des autres.

    Le gabarit en fixe QUATRE. Le run 8609db9e en affichait neuf, dans l'ordre où
    l'analyse amont les publie : le lecteur ne pouvait plus voir lesquels portent
    la décision, et le momentum — le seul qui dise si la demande bouge en ce
    moment — n'y était pas. Les cinq autres ne sont pas jetés pour autant : ils
    passent dans un bloc replié, consultable et hors du chemin de lecture.

    Args:
        dossier: `dossier_synthese` de l'analyse de synthèse.

    Returns:
        Le couple `(tableau_des_quatre, details_des_autres)`.
    """
    demande = getattr(dossier, "demande", None)
    if demande is None:
        return "", ""
    elements = list(demande.indicateurs)

    retenus: list[Any] = []
    lignes: list[list[str]] = []
    for cle, libelle in INDICATEURS_DEMANDE_GABARIT:
        element = _indicateur_correspondant(elements, cle)
        if element is None:
            # Une VALEUR explicite, jamais une ligne manquante : « non calculable »
            # est un constat que le lecteur peut opposer à l'analyse, une ligne
            # absente n'est rien du tout.
            lignes.append([libelle, VALEUR_NON_CALCULABLE, ""])
            continue
        retenus.append(element)
        lignes.append(
            [
                libelle,
                _valeur_lisible(element.ref, element.valeur),
                (element.detail or "").strip(),
            ]
        )
    principal = tableau(["Indicateur", "Valeur", "Comment le lire"], lignes)

    autres = [element for element in elements if element not in retenus]
    if not autres:
        return principal, ""
    corps = tableau(
        ["Indicateur", "Valeur", "Comment le lire"],
        [
            [
                element.libelle,
                _valeur_lisible(element.ref, element.valeur),
                (element.detail or "").strip(),
            ]
            for element in autres
        ],
    )
    details = (
        f"<details>\n<summary>{TITRE_AUTRES_INDICATEURS}</summary>\n\n"
        f"{corps}\n</details>"
    )
    return principal, details


# --------------------------------------------------------------------------- #
# Prix pratiqués — trois segments, pas deux extrêmes
# --------------------------------------------------------------------------- #


def construire_benchmark_v2(concurrence: Any) -> str:
    """Construit le tableau des prix par canal, segmenté.

    Le gabarit demande entrée / cœur / premium / médiane là où le v1 affichait
    minimum / médiane / maximum. La différence n'est pas cosmétique : un décideur
    qui cherche où se placer lit une STRUCTURE de marché, et deux extrêmes que la
    première annonce aberrante déplace ne lui en donnent aucune.

    Les trois segments sont déjà calculés par l'analyse concurrentielle. Un
    segment absent affiche « non calculé » plutôt que de faire disparaître la
    colonne pour tout le monde.

    Aucune conversion de devise : deux devises font deux lignes, jamais une
    moyenne.

    Args:
        concurrence: Sortie F4 validée, ou `None`.

    Returns:
        Le tableau Markdown, ou une chaîne vide sans benchmark.
    """
    if concurrence is None or not concurrence.benchmark_prix:
        return ""

    def borne(bloc: Any, nom: str) -> str:
        segment = next((s for s in bloc.segments if s.nom == nom), None)
        if segment is None:
            return VALEUR_NON_CALCULE
        return (
            f"{formater_nombre(segment.borne_basse, DECIMALES_MONTANT)}–"
            f"{formater_montant(segment.borne_haute, bloc.devise)}"
        )

    lignes = [
        [
            bloc.source,
            bloc.devise,
            str(bloc.nb_offres_avec_prix),
            borne(bloc, "entree"),
            borne(bloc, "coeur"),
            borne(bloc, "premium"),
            formater_montant(bloc.prix_mediane, bloc.devise),
        ]
        for bloc in concurrence.benchmark_prix
    ]
    return tableau([libelle for _, libelle in COLONNES_BENCHMARK_GABARIT], lignes)


def _volumes_par_source(entrees: Any) -> dict[str, int]:
    """Compte, pour chaque source, ce que les analyses amont en ont retenu.

    Args:
        entrees: Fichiers d'entrée validés.

    Returns:
        Le volume par identifiant de source, zéro compris.
    """
    concurrence = entrees.concurrence
    insights = entrees.insights
    stats = concurrence.referentiel_stats if concurrence else None
    corpus = insights.stats_corpus if insights else None
    offres = stats.nb_offres_par_source if stats else {}
    avis = corpus.nb_unites_par_source if corpus else {}

    recommandations = entrees.recommandations
    dossier = recommandations.dossier_synthese if recommandations else None

    return {
        "amazon": offres.get("amazon", 0) or avis.get("amazon", 0),
        "aliexpress": offres.get("aliexpress", 0) or avis.get("aliexpress", 0),
        "reddit": avis.get("reddit", 0),
        # Un seul compteur de pages web pour tout le document — celui du
        # référentiel. Le run 8609db9e en affichait deux, cinq ici et deux au
        # tableau de méthode, sans qu'aucun ne fasse autorité.
        "recherche_web": stats.nb_pages if stats else 0,
        "meta_ads": stats.nb_annonces if stats else 0,
        "google_trends": 1 if (dossier is not None and dossier.demande is not None) else 0,
    }


def _mention_source(source: str, volume: int, etat: dict[str, Any]) -> str:
    """Rend une source de la ligne « Sources analysées », vide ou non.

    Args:
        source: Identifiant de la source.
        volume: Volume retenu par les analyses amont.
        etat: État transmis par l'orchestrateur pour cette source, éventuellement
            vide : `donnees_disponibles`, `nb_items`, `raison`.

    Returns:
        Le fragment de ligne, toujours non vide.
    """
    libelle = LIBELLES_SOURCES.get(source, source)
    unite = UNITES_SOURCES.get(source, "élément")
    if volume:
        pluriel = "" if volume == 1 or unite.endswith("s") else "s"
        return f"{libelle} ({formater_nombre(volume, 0)} {unite}{pluriel})"

    # Volume nul : la source est nommée AVEC sa raison. C'est tout l'objet de ce
    # correctif — sur le run 8609db9e, AliExpress n'avait rapporté aucune offre
    # et n'était tout simplement pas citée, donc rien ne le disait au lecteur.
    brute = str(etat.get("raison") or "").strip()
    if not brute and etat.get("donnees_disponibles") is False:
        brute = "non_collectee"
    if not brute:
        brute = "aucun_resultat"
    raison = RAISONS_SOURCE_VIDE.get(brute, brute)
    return MENTION_SOURCE_VIDE.format(libelle=libelle, unite=unite, raison=raison)


def construire_ligne_sources(
    entrees: Any, etat_sources: dict[str, dict[str, Any]] | None = None
) -> str:
    """Rappelle en une ligne ce qui a été collecté, et en quel volume.

    LES SIX SOURCES SONT TOUJOURS CITÉES. Une source omise se lit comme une
    source hors périmètre, jamais comme une source vide : le lecteur du run
    8609db9e ne pouvait pas savoir qu'AliExpress avait rapporté zéro offre,
    puisque AliExpress ne figurait nulle part. Une collecte infructueuse est un
    résultat d'étude, et elle se publie.

    Args:
        entrees: Fichiers d'entrée validés.
        etat_sources: État transmis par l'orchestrateur (`--sources-etat`), qui
            porte la RAISON d'une collecte vide. Absent, la ligne dit qu'il n'y a
            eu aucun résultat sans pouvoir en dire plus.

    Returns:
        La ligne « Sources analysées : … ».
    """
    volumes = _volumes_par_source(entrees)
    etats = etat_sources or {}
    morceaux = [
        _mention_source(source, volumes.get(source, 0), etats.get(source, {}))
        for source in SOURCES_LIGNE_SOURCES
    ]
    return f"Sources analysées : {' · '.join(morceaux)}."


def construire_ligne_phases(injectables: Injectables) -> str:
    """Rend la ligne des quatre phases, celle retenue marquée.

    Args:
        injectables: Données injectables.

    Returns:
        La ligne des phases, ou une chaîne vide si aucune phase n'est retenue.
    """
    if not injectables.phase_brute:
        return ""
    morceaux = [
        f"**{libelle} ←**" if cle == injectables.phase_brute else libelle
        for cle, libelle in PHASES_PLC
    ]
    return " · ".join(morceaux)


CHAMPS_SANS_ELLIPSE: tuple[str, ...] = (
    "tableau_besoins",
    "tableau_attentes",
    "tableau_sentiment",
    "tableau_intensite",
    "tableau_benchmark",
    "tableau_actions_suivantes",
    "details_besoins_attentes",
    "details_opportunites_risques",
    "risque_principal_decision",
    "puces_opportunites",
    "puces_risques",
    "pain_points",
    "concurrents_v2",
    "annexe_sources",
)
"""Champs rendus par le v2 où une ellipse de troncature ne doit pas survivre.

`limites_par_famille` en est volontairement absent : les limites sont restituées
verbatim, et une ellipse qu'un analyste amont a écrite lui appartient.
"""


def retirer_ellipses(injectables: Injectables) -> int:
    """Supprime les ellipses de troncature des champs rendus par le v2.

    ELLE RECULE, elle n'efface pas. La première version de cette fonction se
    contentait de retirer le caractère « … », en pariant que la coupe au dernier
    mot entier suffisait à faire une fin de phrase. Elle ne suffit pas : le run
    8609db9e a livré « … la crédibilité acquise ou », « … kit complet plug and »,
    « … plusieurs avis Amazon mentionnent explicitement en ». Le signe de la
    coupe avait disparu, la coupe était toujours là, et le lecteur n'avait plus
    même l'ellipse pour s'en apercevoir — le remède était pire que le mal.

    Le texte recule donc jusqu'à un point d'arrêt : la dernière ponctuation forte
    si elle n'ampute pas la moitié du texte, sinon le dernier mot qui ne soit pas
    un mot-outil. Une phrase qui s'arrête sur « ou », « en » ou « comme » n'a pas
    été rédigée, elle a été coupée.

    Args:
        injectables: Données injectables, modifiées sur place.

    Returns:
        Le nombre d'ellipses retirées.
    """
    total = 0

    def nettoyer(valeur):
        nonlocal total
        if isinstance(valeur, str):
            total += valeur.count("\u2026")
            return couper_bloc(valeur)
        if isinstance(valeur, list):
            return [nettoyer(element) for element in valeur]
        if isinstance(valeur, dict):
            return {cle: nettoyer(sous) for cle, sous in valeur.items()}
        return valeur

    for champ in CHAMPS_SANS_ELLIPSE:
        setattr(injectables, champ, nettoyer(getattr(injectables, champ)))

    # Les badges sont de la prose, pas des cellules : les couper net au milieu
    # d'une phrase produirait une affirmation tronquée qui se lit comme entière.
    # Les badges sont de la prose, pas des cellules : une seule phrase, donc un
    # seul fragment.
    injectables.badges = {
        cle: couper_proprement(valeur) for cle, valeur in injectables.badges.items()
    }
    total += sum(1 for valeur in injectables.badges.values() if "…" not in valeur)
    return total


def _sans_moignon(fragment: str) -> str:
    """Retire d'un fragment le mot-outil final laissé par une coupe.

    Args:
        fragment: Texte d'une cellule, d'une puce ou d'une ligne.

    Returns:
        Le fragment sans sa fin pendante.
    """
    propre = fragment.rstrip(" ,;:")
    mots = propre.split(" ")
    while len(mots) > 1 and mots[-1].strip(".,;:").lower() in MOTS_OUTILS_FIN_INTERDITS:
        mots.pop()
    return " ".join(mots).rstrip(" ,;:")


def couper_proprement(fragment: str) -> str:
    """Ramène UN fragment tronqué à un point d'arrêt lisible.

    Deux reculs, dans cet ordre :

    1. la dernière ponctuation forte, si elle laisse au moins la moitié du
       fragment — en deçà, on perdrait plus que la coupe n'avait perdu ;
    2. à défaut, le dernier mot qui ne soit pas un mot-outil ni une virgule.

    Le second recul est ce qui manquait. « Certains consommateurs recherchent un
    niveau de fidélité sonore de » et « Certains consommateurs recherchent un
    niveau de fidélité sonore » disent la même chose au lecteur ; la première le
    laisse attendre une suite qui ne viendra pas.

    UN FRAGMENT, et non un bloc : appliquée à un tableau Markdown entier, la
    recherche de ponctuation forte trouverait un point dans une cellule du milieu
    et amputerait toutes les lignes suivantes. C'est `couper_bloc` qui découpe.

    Args:
        fragment: Texte d'une seule cellule, ellipse comprise.

    Returns:
        Le fragment reculé jusqu'à son dernier point d'arrêt.
    """
    if "…" not in fragment:
        # Rien à réparer : seul un moignon de mot-outil est retiré, et le texte
        # est rendu intact sinon. Chercher une fin de phrase dans un texte non
        # tronqué reviendrait à en jeter la dernière.
        return _sans_moignon(fragment)

    propre = fragment.replace("…", "").rstrip()
    coupe = max(propre.rfind(". "), propre.rfind("! "), propre.rfind("? "))
    if coupe > len(propre) // 2:
        return propre[: coupe + 1]
    return _sans_moignon(propre)


def couper_bloc(texte: str) -> str:
    """Applique `couper_proprement` à chaque cellule ou ligne d'un bloc.

    Les injectables du v2 sont rarement une phrase seule : ce sont des tableaux
    Markdown et des listes de puces, où chaque cellule est un texte autonome
    tronqué pour son propre compte. Le découpage se fait donc sur les lignes puis
    sur les barres verticales, et les lignes de séparation d'un tableau
    (`| --- |`) passent sans être touchées.

    Args:
        texte: Bloc Markdown, tableau compris.

    Returns:
        Le bloc, chaque fragment ramené à son point d'arrêt.
    """
    lignes = []
    for ligne in texte.split("\n"):
        if "|" not in ligne:
            lignes.append(couper_proprement(ligne) if ligne.strip() else ligne)
            continue
        if set(ligne.strip()) <= set("| -:"):
            lignes.append(ligne)  # séparateur de tableau
            continue
        morceaux = ligne.split("|")
        lignes.append(
            "|".join(
                f" {couper_proprement(cellule.strip())} " if cellule.strip() else cellule
                for cellule in morceaux
            )
        )
    return "\n".join(lignes)


def couper_a_la_phrase(texte: str) -> str:
    """Alias historique de `couper_bloc`, conservé pour les appelants.

    Args:
        texte: Texte éventuellement tronqué.

    Returns:
        Le texte, chaque fragment ramené à son point d'arrêt.
    """
    return couper_bloc(texte)


# =========================================================================== #
# Enrichissement
# =========================================================================== #


def enrichir(
    injectables: Injectables,
    entrees: Any,
    degradees: list[str],
    absentes: list[str],
    etat_sources: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[StatutAnalyse], list[str]]:
    """Complète les injectables avec ce que le gabarit v2 consomme.

    Args:
        injectables: Injectables déjà construits par `preparer()`.
        entrees: Fichiers d'entrée validés.
        degradees: Sections construites depuis l'écho de synthèse.
        absentes: Sections remplacées par un encart standard.
        etat_sources: État des collecteurs transmis par l'orchestrateur.

    Returns:
        Le couple `(statuts, hypotheses)`.
    """
    statuts: list[StatutAnalyse] = []
    hypotheses: list[str] = []
    recommandations = entrees.recommandations
    insights = entrees.insights
    concurrence = entrees.concurrence
    verdict = recommandations.verdict_potentiel
    standards: dict[str, str] = {}

    # --- Écran 0 : décision ------------------------------------------------- #
    injectables.decision_libelle = LIBELLES_VERDICT.get(
        verdict.verdict, verdict.verdict
    )
    injectables.score_max = verdict.nb_criteres_evalues * SCORE_MAX_CRITERE
    injectables.ligne_verdict = (
        f"Verdict calculé : {injectables.verdict_lisible} · score "
        f"{verdict.score_total}/{injectables.score_max} · fiabilité "
        f"{injectables.confiance_verdict}"
    )
    injectables.ligne_meta = (
        f"Étude du {injectables.entete.get('date_run', '')} · "
        f"portée : {injectables.entete.get('portee', '')}"
    )
    injectables.faits_cles_decision = injectables.faits_cles[:NB_FAITS_CLES_DECISION]
    injectables.risque_principal_decision = injectables.risque_principal

    injectables.puces_changer_decision = (
        [b.enonce for b in injectables.bascules]
        if injectables.bascules
        else [PHRASE_AUCUNE_BASCULE]
    )

    # « Ce qu'il manque » nomme le SIGNAL manquant, jamais l'agent qui le
    # produirait : « collecter les tendances de recherche », pas « relancer
    # l'agent Tendances ».
    manques: list[str] = []
    for section in absentes + degradees:
        libelle = LIBELLES_ENTREES.get(section)
        if libelle:
            manques.append(f"Compléter l'{libelle} : elle n'a pas été fournie.")
    manques.extend(
        premiere_phrase(manque)
        for manque in injectables.donnees_a_completer
    )
    injectables.puces_manque_trancher = manques[:NB_MANQUES_DECISION]

    if (
        verdict.verdict == VERDICT_INDETERMINE
        and not injectables.bascules
        and not injectables.puces_manque_trancher
    ):
        injectables.puces_manque_trancher = [PHRASE_GO_CONDITIONNEL_SANS_CONDITION]

    injectables.ligne_sources = construire_ligne_sources(entrees, etat_sources)

    # L'étude est partielle dès qu'un BLOC d'analyse manque **ou** qu'une SOURCE
    # est revenue vide. Le second cas manquait : le run 8609db9e portait ses
    # quatre analyses et n'a affiché aucun encart, alors qu'AliExpress — l'un des
    # deux canaux de prix — n'avait rien rapporté du tout.
    volumes = _volumes_par_source(entrees)
    etats = etat_sources or {}
    manquants = [
        LIBELLES_ENTREES.get(s, s) for s in absentes + degradees if s in LIBELLES_ENTREES
    ]
    manquants += [
        LIBELLES_SOURCES.get(source, source)
        for source in SOURCES_LIGNE_SOURCES
        if not volumes.get(source)
        and etats.get(source, {}).get("donnees_disponibles") is not True
    ]
    if manquants:
        injectables.encart_partielle_v2 = ENCART_ETUDE_PARTIELLE_V2.format(
            blocs=", ".join(dict.fromkeys(manquants)),
            accord="non disponibles" if len(manquants) > 1 else "non disponible",
        )
        statuts.append(
            StatutAnalyse(
                phase="etude_partielle",
                succes=True,
                message_erreur=(
                    "étude partielle — bloc(s) ou source(s) sans donnée : "
                    + ", ".join(dict.fromkeys(manquants))
                ),
                nb_elements=len(manquants),
            )
        )

    # --- Écran 1 : le consommateur ------------------------------------------ #
    # Les points de friction sont déjà sélectionnés par `preparer()` ; seuls les
    # deux premiers portent un extrait, et l'intensité est affichée sur SON
    # échelle, celle de la source.
    injectables.pain_points = injectables.pain_points[:NB_POINTS_FRICTION]
    if insights is not None:
        for rang, point in enumerate(insights.pain_points[:NB_POINTS_FRICTION]):
            if rang >= len(injectables.pain_points):
                break
            injectables.pain_points[rang]["intensite"] = (
                f"{formater_nombre(point.intensite_moyenne, 2)}/{INTENSITE_MAX_F3}"
            )
        conserves = {
            f"irritant-{rang + 1}" for rang in range(min(NB_VERBATIMS, NB_POINTS_FRICTION))
        }
        injectables.verbatims = {
            cle: extrait
            for cle, extrait in injectables.verbatims.items()
            if cle in conserves
        }
        for rang in range(min(NB_VERBATIMS, len(insights.pain_points))):
            cle = f"irritant-{rang + 1}"
            if cle not in injectables.verbatims:
                extrait = selectionner_verbatim(insights.pain_points[rang].verbatims)
                if extrait is not None:
                    injectables.verbatims[cle] = extrait

    # Les écarts entre sources sont un commentaire d'analyse, souvent long : le
    # constat tient dans sa première phrase, l'argumentation reste en amont.
    # Première phrase, et rien de plus : ces puces figurent parmi les
    # TEXTES_NON_COMPRESSIBLES, donc aucune coupe au n-ième mot ne les atteint.
    injectables.divergences = [
        premiere_phrase(divergence)
        for divergence in injectables.divergences
    ]

    injectables.details_besoins_attentes = _bloc_replie(
        "Détail des besoins et attentes",
        "\n\n".join(
            partie
            for partie in (
                "**Besoins exprimés**" if injectables.tableau_besoins else "",
                injectables.tableau_besoins,
                "**Attentes exprimées**" if injectables.tableau_attentes else "",
                injectables.tableau_attentes,
            )
            if partie
        ),
    )
    if insights is None:
        for sous_bloc in (SB_POURQUOI_ACHAT, SB_APPRECIENT, SB_DERANGE, SB_AIMERAIENT):
            standards.setdefault(sous_bloc, PHRASE_NON_DOCUMENTE)

    # --- Écran 2 : le marché et les concurrents ----------------------------- #
    dossier = recommandations.dossier_synthese
    if dossier is not None and dossier.demande is not None:
        injectables.dynamique_demande, injectables.autres_indicateurs_demande = (
            construire_dynamique_demande(dossier)
        )
    else:
        injectables.dynamique_demande = ""
        injectables.autres_indicateurs_demande = ""
        standards[SB_DYNAMIQUE] = PHRASE_TENDANCES_ABSENTES

    injectables.concurrents_v2 = selectionner_concurrents(concurrence)
    injectables.widgets_extraits = [
        GABARIT_WIDGET_EXTRAITS.format(source=source)
        for source in SOURCES_WIDGETS
        if concurrence is not None
        and source in {s for c in concurrence.tableau_comparatif for s in c.presence_sources}
    ]
    if concurrence is not None and not any(
        getattr(c, "clientele_cible", None) for c in concurrence.tableau_comparatif
    ):
        hypotheses.append(BESOIN_CLIENTELE_CIBLE)
    if injectables.concurrents_v2:
        hypotheses.append(BESOIN_UNITES_VOLUME)
    else:
        standards[SB_QUE_FONT] = PHRASE_NON_DOCUMENTE

    benchmark_v2 = construire_benchmark_v2(concurrence)
    if benchmark_v2:
        injectables.tableau_benchmark = benchmark_v2
    if not injectables.tableau_benchmark:
        standards[SB_PRIX_PRATIQUES] = PHRASE_NON_DOCUMENTE

    tableau_forces, origine, hyp_forces = construire_cinq_forces(
        recommandations, concurrence, insights
    )
    injectables.tableau_cinq_forces = tableau_forces
    injectables.cinq_forces_source = origine
    hypotheses.extend(hyp_forces)
    if origine == CINQ_FORCES_SOURCE_REGLES:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION_V2,
                succes=True,
                message_erreur=(
                    "cinq forces non publiées par l'analyse de synthèse : trois "
                    "estimées par règle déterministe, deux déclarées non évaluées."
                ),
                nb_elements=len(LIBELLES_CINQ_FORCES),
            )
        )

    # La formulation « non observé dans le corpus » est une précaution de méthode :
    # elle ouvre l'énoncé amont, donc la première phrase la conserve toujours.
    injectables.puces_personne_ne_fait = [
        premiere_phrase(angle)
        for angle in injectables.angles_peu_exploites[:5]
    ]
    injectables.details_angles = _bloc_replie(
        "Autres angles peu exploités",
        "\n".join(f"- {a}" for a in injectables.angles_peu_exploites[5:]),
    )
    if not injectables.puces_personne_ne_fait:
        standards[SB_PERSONNE_NE_FAIT] = PHRASE_NON_DOCUMENTE

    # --- Écran 3 : ce que nous recommandons --------------------------------- #
    injectables.ligne_phases = construire_ligne_phases(injectables)
    if injectables.phase_brute and entrees.plc is not None:
        # Recopiees, jamais reformulees : ce sont des recommandations amont.
        injectables.puces_phase = [
            premiere_phrase(r.enonce)
            for r in entrees.plc.recommandations_phase[:3]
        ]
    if not injectables.phase_brute:
        motif = ""
        if entrees.plc is not None and entrees.plc.declenchement.motif:
            motif = f" ({entrees.plc.declenchement.motif})"
        standards[SB_PHASE] = f"{PHRASE_PLC_NON_EVALUEE}{motif}"

    injectables.actions_p1, suivantes = selectionner_actions(recommandations)
    injectables.tableau_actions_suivantes = _bloc_replie(
        "Actions suivantes",
        tableau(
            ["Action", "Domaine", "Priorité", "Horizon"],
            [
                [
                    premiere_phrase(r.enonce),
                    r.domaine,
                    r.priorite,
                    r.horizon,
                ]
                for r in suivantes
            ],
        ),
    )

    prix = recommandations.recommandation_prix
    if prix is not None and prix.fourchettes:
        injectables.fourchette_prix = " ; ".join(
            f"{formater_nombre(f.min, 2)}–{formater_montant(f.max, f.devise)}"
            for f in prix.fourchettes
        )
        injectables.conditions_prix = list(prix.conditions)
    else:
        standards[SB_PRIX] = PHRASE_NON_DOCUMENTE

    # UN TITRE N'EST JAMAIS COUPÉ. Il l'était à douze mots, et le run 8609db9e a
    # livré « **… sur la fiche produit, un** — » et « **… effet de mode par le** — » :
    # des titres qui ne nomment plus rien, et dont le lecteur ne peut pas deviner
    # qu'ils ont été amputés. La première PHRASE reste prise — c'est une coupe
    # sémantique, à la ponctuation de l'analyste — mais plus aucune coupe au
    # n-ième mot. Une puce trop longue passe ensuite par la compression
    # rédactionnelle, qui réécrit au lieu de couper.
    injectables.puces_opportunites = [
        f"**{premiere_phrase(o.libelle)}** — "
        f"{premiere_phrase(o.conditions_de_capture[0] if o.conditions_de_capture else o.description)}"
        for o in recommandations.opportunites[:NB_OPPORTUNITES]
    ]
    injectables.puces_risques = [
        f"**{premiere_phrase(r.libelle)}** — {premiere_phrase(r.attenuation)}"
        for r in recommandations.risques[:NB_RISQUES]
    ]
    reste = "\n\n".join(
        partie
        for partie in (
            "**Autres opportunités**"
            if len(recommandations.opportunites) > NB_OPPORTUNITES
            else "",
            "\n".join(
                f"- **{o.libelle}** — {premiere_phrase(o.description)}"
                for o in recommandations.opportunites[NB_OPPORTUNITES:]
            ),
            "**Autres risques**"
            if len(recommandations.risques) > NB_RISQUES
            else "",
            "\n".join(
                f"- **{r.libelle}** ({r.gravite}) — {premiere_phrase(r.attenuation)}"
                for r in recommandations.risques[NB_RISQUES:]
            ),
        )
        if partie
    )
    injectables.details_opportunites_risques = _bloc_replie(
        "Opportunités et risques secondaires", reste
    )

    # Les badges, mentions et références sont indexés par section v1 : on les
    # rend lisibles aussi par identifiant d'écran, sans les recalculer.
    injectables.badges = alias_v2(injectables.badges)
    injectables.mentions_partielles = alias_v2(injectables.mentions_partielles)
    injectables.refs_par_section = alias_v2(injectables.refs_par_section)

    injectables.sous_blocs_standards = standards
    if standards:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION_V2,
                succes=True,
                message_erreur=(
                    f"{len(standards)} sous-bloc(s) sans donnée exploitable : phrase "
                    f"standard affichée, aucune rédaction demandée au modèle "
                    f"({', '.join(sorted(standards))})."
                ),
                nb_elements=len(standards),
            )
        )
    # Le dépouillement du vocabulaire interne a déjà tourné dans `preparer()`,
    # mais les champs ci-dessus sont nés après lui : on le rappelle. Il est
    # idempotent — une substitution déjà faite ne se refait pas.
    substitutions = depouiller_injectables(injectables)
    ellipses = retirer_ellipses(injectables)
    if ellipses:
        statuts.append(
            StatutAnalyse(
                phase=PHASE_PREPARATION_V2,
                succes=True,
                message_erreur=(
                    f"{ellipses} ellipse(s) de troncature retirée(s) : une cellule "
                    f"coupée ne doit pas se faire passer pour une phrase entière."
                ),
                nb_elements=ellipses,
            )
        )
    logger.debug(
        "gabarit v2 : %d sous-bloc(s) standard, cinq forces depuis %s, "
        "%d substitution(s), %d ellipse(s) retirée(s)",
        len(standards),
        origine,
        substitutions,
        ellipses,
    )
    return statuts, hypotheses
