"""Phase « carte » : extraction par lots sur le modèle d'extraction.

Trois chaînes LCEL :

1. cartographie des unités courtes (sentiment, thèmes, pain points, besoins…) ;
2. cartographie des documents web (retours consommateurs **rapportés** par la page) ;
3. normalisation des libellés bruts en tables de correspondance.

Le remappage et le recomptage qui suivent la normalisation sont déterministes :
les fréquences éventuellement retournées par le modèle sont ignorées.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    INTENSITE_MAX,
    INTENSITE_MIN,
    MAX_TOKENS_EXTRACTION,
    MODELE_EXTRACTION,
    TAILLE_LOT_DOCUMENTS,
    TAILLE_LOT_UNITES,
    construire_modele,
    invoquer_structure,
    logger,
)
from schemas import (
    AnalyseDocument,
    AnalyseUnite,
    DocumentWeb,
    LotAnalysesDocuments,
    LotAnalysesUnites,
    StatutAnalyse,
    TableNormalisation,
    UniteConsommateur,
)

PHASE_UNITES: str = "carte_unites"
PHASE_DOCUMENTS: str = "carte_documents"
PHASE_NORMALISATION: str = "normalisation_libelles"

# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

_SYSTEME_UNITES = (
    "Tu es analyste d'études de marché. Tu cartographies des messages de "
    "consommateurs un par un, sans jamais rien inventer.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description : {produit_description}\n"
    "Langue d'analyse : {langue_analyse} — rédige TOUS tes libellés dans cette langue.\n\n"
    "Consignes impératives :\n"
    "- Le sentiment est celui du consommateur VIS-À-VIS DU TYPE DE PRODUIT "
    "concerné, pas l'humeur générale du message. Un message furieux contre un "
    "vendeur mais satisfait du produit est « positif ».\n"
    "- Utilise « non_applicable » dès que l'unité n'exprime aucune opinion sur ce "
    "type de produit : question logistique, plaisanterie, hors sujet. C'est le cas "
    "le plus fréquent dans un corpus réel — ne force pas une opinion.\n"
    "- Un pain point est un problème VÉCU ou CRAINT par le consommateur. "
    f"Intensité {INTENSITE_MIN} = simple gêne, 2 = problème net, "
    f"{INTENSITE_MAX} = rédhibitoire (renvoi, abandon, non-rachat).\n"
    "- Les libellés de thèmes et de pain points sont courts (2 à 6 mots), "
    "génériques et réutilisables d'une unité à l'autre : « tenue à l'effort », "
    "pas « ça tombe quand je cours le dimanche ».\n"
    "- Les besoins sont ce que le consommateur cherche à obtenir ; les attentes "
    "sont ce qu'il considère comme dû.\n"
    "- N'invente rien : toutes les listes peuvent être vides. Une liste vide est "
    "une réponse correcte et attendue.\n"
    "- `verbatim_cle` est vrai uniquement si le texte, cité tel quel, illustre "
    "clairement un problème ou une satisfaction.\n"
    "- Réponds pour CHAQUE unité du lot, en recopiant son identifiant à "
    "l'identique. N'invente aucun identifiant.{erreur_precedente}"
)

_HUMAIN_UNITES = (
    "Voici le lot d'unités à cartographier, au format JSON "
    "(champs : id_unite, source, note_sur_5, texte) :\n\n{lot}"
)

_SYSTEME_DOCUMENTS = (
    "Tu es analyste d'études de marché. Tu lis des pages web éditoriales "
    "(tests, comparatifs, articles) et tu en extrais UNIQUEMENT les retours "
    "consommateurs que la page rapporte.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse} — rédige tous tes libellés dans cette langue.\n\n"
    "Consignes impératives :\n"
    "- N'extrais que ce qui concerne l'expérience des utilisateurs de ce type de "
    "produit : reproches récurrents rapportés, besoins exprimés, points salués.\n"
    "- Ignore tout le reste : opinions du rédacteur sur d'autres catégories, "
    "encarts publicitaires, navigation, mentions légales, comparaisons de prix.\n"
    "- `position_editoriale` décrit l'avis de la page sur ce TYPE de produit : "
    "« recommande », « mitige », « deconseille », ou null si la page ne tranche pas.\n"
    "- Une page qui ne rapporte aucun retour consommateur donne des listes vides. "
    "C'est fréquent et parfaitement correct.\n"
    "- Recopie l'identifiant de chaque document à l'identique.{erreur_precedente}"
)

_HUMAIN_DOCUMENTS = (
    "Voici le lot de documents, au format JSON "
    "(champs : id_unite, titre, domaine, type_source, extrait) :\n\n{lot}"
)

_SYSTEME_NORMALISATION = (
    "Tu es analyste d'études de marché. On te donne des libellés bruts de "
    "{famille} produits unité par unité, avec leur fréquence brute. Ton travail "
    "est de les REGROUPER, pas de les réécrire librement.\n\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    "- Regroupe les libellés qui désignent la même réalité consommateur "
    "(synonymes, variantes de formulation, singulier/pluriel, traductions).\n"
    "- Ne fusionne JAMAIS deux réalités distinctes pour tenir dans le plafond : "
    "« autonomie insuffisante » et « charge lente » sont deux problèmes différents.\n"
    "- Produis au plus {plafond} regroupements, en privilégiant les plus fréquents.\n"
    "- Chaque `libelle_normalise` est court, générique et rédigé dans la langue "
    "d'analyse. Chaque `libelles_source` recopie À L'IDENTIQUE les libellés bruts "
    "reçus — c'est la clé de remappage, toute variation la casse.\n"
    "- Un libellé brut n'appartient qu'à un seul regroupement. Les libellés que tu "
    "ne regroupes pas seront simplement écartés.\n"
    "- Ne renvoie aucune fréquence : elles sont recalculées par le code."
    "{erreur_precedente}"
)

_HUMAIN_NORMALISATION = (
    "Libellés bruts et fréquences, au format JSON :\n\n{libelles}"
)


def _decouper(elements: list, taille: int) -> list[list]:
    """Découpe une liste en lots de taille fixe.

    Args:
        elements: Liste à découper.
        taille: Taille maximale d'un lot.

    Returns:
        La liste des lots.
    """
    return [elements[i : i + taille] for i in range(0, len(elements), taille)]


def cartographier_unites(
    unites: list[UniteConsommateur],
    produit_nom: str,
    produit_description: str,
    langue_analyse: str,
) -> tuple[list[AnalyseUnite], list[StatutAnalyse]]:
    """Cartographie les unités courtes par lots.

    Un lot en échec après reprise est écarté : les unités concernées sont
    simplement absentes des analyses, ce qui est tracé dans les statuts. Il n'y a
    pas de boucle de rattrapage.

    Args:
        unites: Unités à cartographier.
        produit_nom: Nom du produit étudié.
        produit_description: Description du produit étudié.
        langue_analyse: Code langue des libellés produits.

    Returns:
        Le couple `(analyses, statuts)`.
    """
    if not unites:
        return [], []

    modele = construire_modele(MODELE_EXTRACTION, MAX_TOKENS_EXTRACTION)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_UNITES), ("human", _HUMAIN_UNITES)]
    )
    chaine = gabarit | modele.with_structured_output(LotAnalysesUnites)

    analyses: list[AnalyseUnite] = []
    statuts: list[StatutAnalyse] = []

    for numero, lot in enumerate(_decouper(unites, TAILLE_LOT_UNITES), start=1):
        charge = json.dumps(
            [
                {
                    "id_unite": u.id_unite,
                    "source": u.source,
                    "note_sur_5": u.note_sur_5,
                    "texte": u.texte,
                }
                for u in lot
            ],
            ensure_ascii=False,
            indent=1,
        )
        resultat, tentatives, erreur = invoquer_structure(
            chaine,
            {
                "produit_nom": produit_nom,
                "produit_description": produit_description,
                "langue_analyse": langue_analyse,
                "lot": charge,
            },
            f"{PHASE_UNITES} lot {numero}",
        )
        if resultat is None:
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_UNITES,
                    succes=False,
                    message_erreur=f"lot {numero} écarté après reprise : {erreur}",
                    nb_elements=0,
                    nb_tentatives=tentatives,
                )
            )
            continue

        ids_lot = {u.id_unite for u in lot}
        retenues = [a for a in resultat.analyses if a.id_unite in ids_lot]
        manquantes = ids_lot - {a.id_unite for a in retenues}
        hallucinees = len(resultat.analyses) - len(retenues)
        analyses.extend(retenues)

        message = None
        if manquantes or hallucinees:
            message = (
                f"lot {numero} : {len(manquantes)} unité(s) non analysée(s), "
                f"{hallucinees} identifiant(s) inconnu(s) écarté(s)"
            )
        statuts.append(
            StatutAnalyse(
                phase=PHASE_UNITES,
                succes=True,
                message_erreur=message,
                nb_elements=len(retenues),
                nb_tentatives=tentatives,
            )
        )

    logger.debug("carte unités : %d analyses sur %d unités", len(analyses), len(unites))
    return analyses, statuts


def cartographier_documents(
    documents: list[DocumentWeb],
    produit_nom: str,
    langue_analyse: str,
) -> tuple[list[AnalyseDocument], list[StatutAnalyse]]:
    """Cartographie les documents web par lots.

    Args:
        documents: Documents à cartographier.
        produit_nom: Nom du produit étudié.
        langue_analyse: Code langue des libellés produits.

    Returns:
        Le couple `(analyses, statuts)`.
    """
    if not documents:
        return [], []

    modele = construire_modele(MODELE_EXTRACTION, MAX_TOKENS_EXTRACTION)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_DOCUMENTS), ("human", _HUMAIN_DOCUMENTS)]
    )
    chaine = gabarit | modele.with_structured_output(LotAnalysesDocuments)

    analyses: list[AnalyseDocument] = []
    statuts: list[StatutAnalyse] = []

    for numero, lot in enumerate(_decouper(documents, TAILLE_LOT_DOCUMENTS), start=1):
        charge = json.dumps(
            [
                {
                    "id_unite": d.id_unite,
                    "titre": d.titre,
                    "domaine": d.domaine,
                    "type_source": d.type_source,
                    "extrait": d.extrait,
                }
                for d in lot
            ],
            ensure_ascii=False,
            indent=1,
        )
        resultat, tentatives, erreur = invoquer_structure(
            chaine,
            {
                "produit_nom": produit_nom,
                "langue_analyse": langue_analyse,
                "lot": charge,
            },
            f"{PHASE_DOCUMENTS} lot {numero}",
        )
        if resultat is None:
            statuts.append(
                StatutAnalyse(
                    phase=PHASE_DOCUMENTS,
                    succes=False,
                    message_erreur=f"lot {numero} écarté après reprise : {erreur}",
                    nb_tentatives=tentatives,
                )
            )
            continue

        ids_lot = {d.id_unite for d in lot}
        retenues = [a for a in resultat.analyses if a.id_unite in ids_lot]
        analyses.extend(retenues)
        statuts.append(
            StatutAnalyse(
                phase=PHASE_DOCUMENTS,
                succes=True,
                nb_elements=len(retenues),
                nb_tentatives=tentatives,
            )
        )

    return analyses, statuts


def normaliser_libelles(
    frequences_brutes: dict[str, int],
    famille: str,
    plafond: int,
    langue_analyse: str,
) -> tuple[dict[str, str], StatutAnalyse]:
    """Construit une table de correspondance libellé brut → libellé normalisé.

    Args:
        frequences_brutes: Libellés bruts et leur fréquence d'apparition.
        famille: Famille traitée (« thèmes » ou « pain points »), pour le prompt.
        plafond: Nombre maximal de regroupements attendus.
        langue_analyse: Code langue des libellés produits.

    Returns:
        Le couple `(table_de_correspondance, statut)`. La table est vide si la
        chaîne a échoué : l'appelant conserve alors les libellés bruts.
    """
    if not frequences_brutes:
        return {}, StatutAnalyse(phase=PHASE_NORMALISATION, succes=True, nb_elements=0)

    modele = construire_modele(MODELE_EXTRACTION, MAX_TOKENS_EXTRACTION)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_NORMALISATION), ("human", _HUMAIN_NORMALISATION)]
    )
    chaine = gabarit | modele.with_structured_output(TableNormalisation)

    charge = json.dumps(
        sorted(
            ({"libelle": k, "frequence": v} for k, v in frequences_brutes.items()),
            key=lambda e: -e["frequence"],
        ),
        ensure_ascii=False,
        indent=1,
    )
    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "famille": famille,
            "plafond": plafond,
            "langue_analyse": langue_analyse,
            "libelles": charge,
        },
        f"{PHASE_NORMALISATION} ({famille})",
    )
    if resultat is None:
        return {}, StatutAnalyse(
            phase=PHASE_NORMALISATION,
            succes=False,
            message_erreur=(
                f"normalisation des {famille} en échec : {erreur}. Les libellés "
                f"bruts sont conservés tels quels, sans regroupement."
            ),
            nb_tentatives=tentatives,
        )

    table: dict[str, str] = {}
    for entree in resultat.entrees[:plafond]:
        cible = entree.libelle_normalise.strip()
        if not cible:
            continue
        for source in entree.libelles_source:
            brut = source.strip()
            if brut and brut not in table:
                table[brut] = cible

    inconnus = [b for b in table if b not in frequences_brutes]
    for brut in inconnus:
        table.pop(brut)

    return table, StatutAnalyse(
        phase=PHASE_NORMALISATION,
        succes=True,
        message_erreur=(
            f"{len(inconnus)} libellé(s) inventé(s) par le modèle écarté(s)"
            if inconnus
            else None
        ),
        nb_elements=len(set(table.values())),
        nb_tentatives=tentatives,
    )


def remapper_analyses(
    analyses: list[AnalyseUnite],
    analyses_documents: list[AnalyseDocument],
    table_themes: dict[str, str],
    table_pain_points: dict[str, str],
) -> tuple[list[AnalyseUnite], list[AnalyseDocument]]:
    """Applique les tables de normalisation aux analyses.

    Opération purement déterministe : un libellé absent de la table est conservé
    tel quel plutôt que supprimé, afin de ne jamais perdre silencieusement un
    signal.

    Args:
        analyses: Analyses d'unités.
        analyses_documents: Analyses de documents.
        table_themes: Correspondance des thèmes.
        table_pain_points: Correspondance des pain points.

    Returns:
        Le couple `(analyses_remappees, analyses_documents_remappees)`.
    """
    for analyse in analyses:
        analyse.themes = [table_themes.get(t.strip(), t.strip()) for t in analyse.themes]
        for pain in analyse.pain_points:
            pain.libelle = table_pain_points.get(pain.libelle.strip(), pain.libelle.strip())
    for document in analyses_documents:
        for retour in document.retours_rapportes:
            retour.libelle = table_pain_points.get(
                retour.libelle.strip(), retour.libelle.strip()
            )
    return analyses, analyses_documents


def frequences_brutes_themes(analyses: list[AnalyseUnite]) -> dict[str, int]:
    """Compte les libellés de thèmes bruts.

    Args:
        analyses: Analyses d'unités non remappées.

    Returns:
        Le dictionnaire `libelle → nombre d'unités distinctes`.
    """
    compteur: dict[str, set[str]] = {}
    for analyse in analyses:
        for libelle in analyse.themes:
            propre = libelle.strip()
            if propre:
                compteur.setdefault(propre, set()).add(analyse.id_unite)
    return {k: len(v) for k, v in compteur.items()}


def frequences_brutes_pain_points(
    analyses: list[AnalyseUnite], analyses_documents: list[AnalyseDocument]
) -> dict[str, int]:
    """Compte les libellés de pain points bruts, unités et documents confondus.

    Args:
        analyses: Analyses d'unités non remappées.
        analyses_documents: Analyses de documents non remappées.

    Returns:
        Le dictionnaire `libelle → nombre d'éléments distincts`.
    """
    compteur: dict[str, set[str]] = {}
    for analyse in analyses:
        for pain in analyse.pain_points:
            propre = pain.libelle.strip()
            if propre:
                compteur.setdefault(propre, set()).add(analyse.id_unite)
    for document in analyses_documents:
        for retour in document.retours_rapportes:
            propre = retour.libelle.strip()
            if propre:
                compteur.setdefault(propre, set()).add(document.id_unite)
    return {k: len(v) for k, v in compteur.items()}
