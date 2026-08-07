"""Consolidation des concurrents à travers les sources.

Un appel unique au modèle de synthèse sur une **vue compacte** du référentiel
(marques distinctes, annonceurs, marques détectées sur le web, offres sans
marque). Le rapprochement se fait par **similarité de nom uniquement** : aucune
donnée de registre d'entreprise n'est disponible, et l'agent ne doit jamais
laisser croire le contraire.

Le code vérifie ensuite mécaniquement le résultat : identifiants existants,
aucun identifiant rattaché à deux concurrents, groupe « sans marque » complété.
"""

from __future__ import annotations

import json
from collections import defaultdict

from langchain_core.prompts import ChatPromptTemplate

from config import (
    CERTITUDE_PROBABLE,
    CERTITUDE_SURE,
    MAX_TOKENS_SYNTHESE,
    MODELE_SYNTHESE,
    NOM_GROUPE_SANS_MARQUE,
    SOURCE_ALIEXPRESS,
    SOURCE_AMAZON,
    SOURCE_META_ADS,
    SOURCE_WEB,
    TYPE_SANS_MARQUE,
    construire_modele,
    invoquer_structure,
    logger,
)
from schemas import (
    AlerteCoherence,
    ConcurrentConsolide,
    LotConcurrents,
    Referentiel,
    StatutAnalyse,
)

PHASE_CONSOLIDATION: str = "consolidation_concurrents"

_SYSTEME = (
    "Tu es analyste concurrentiel. On te donne les entités commerciales repérées "
    "dans quatre sources hétérogènes et tu dois dire lesquelles désignent le MÊME "
    "concurrent.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    "- Tu ne disposes QUE des noms et des volumes. Tu n'as accès à aucun registre "
    "d'entreprises : rapproche par similarité de nom, et rien d'autre.\n"
    "- `niveau_certitude_rapprochement` vaut « sur » UNIQUEMENT pour des variations "
    "triviales : casse, accents, suffixe de boutique (« Baseus » / « BASEUS Official "
    "Store »), espaces. Tout le reste vaut « probable ».\n"
    "- DANS LE DOUTE, NE FUSIONNE PAS. Deux entrées séparées valent mieux qu'une "
    "fusion erronée : une fusion abusive fabrique un concurrent qui n'existe pas.\n"
    "- Ne rapproche jamais deux marques différentes d'un même groupe supposé : tu "
    "ne peux pas le savoir depuis un nom.\n"
    "- `type` vaut « marque_etablie » (marque identifiable et reconnue), "
    "« marque_marketplace » (marque de vendeur marketplace peu identifiable), "
    "« annonceur_seul » (présent uniquement en publicité) ou « offres_sans_marque ».\n"
    "- Reporte dans `ids_offres`, `ids_annonces` et `ids_pages` les identifiants "
    "EXACTS fournis. N'en invente aucun, n'en omets aucun que tu as rattaché.\n"
    "- Un identifiant n'appartient qu'à UN concurrent.\n"
    "- Ne crée pas d'entrée pour le produit étudié lui-même.{erreur_precedente}"
)

_HUMAIN = (
    "MARQUES D'OFFRES (nom, nb offres, volume cumulé, identifiants) :\n{marques}\n\n"
    "ANNONCEURS (nom, nb annonces, identifiants) :\n{annonceurs}\n\n"
    "MARQUES DÉTECTÉES SUR LES PAGES WEB (nom, identifiants de pages) :\n{marques_web}\n\n"
    "OFFRES SANS MARQUE IDENTIFIABLE (identifiant, titre tronqué) :\n{sans_marque}"
)


def _vue_compacte(referentiel: Referentiel) -> dict[str, str]:
    """Construit la vue compacte soumise au modèle.

    Args:
        referentiel: Référentiel complet.

    Returns:
        Les quatre blocs sérialisés en JSON.
    """
    marques: dict[str, dict] = defaultdict(
        lambda: {"nb_offres": 0, "volume_cumule": 0, "ids": []}
    )
    sans_marque: list[dict] = []
    for offre in referentiel.offres:
        if offre.marque:
            entree = marques[offre.marque]
            entree["nb_offres"] += 1
            entree["volume_cumule"] += offre.volume_ventes or 0
            entree["ids"].append(offre.id_offre)
        else:
            sans_marque.append({"id_offre": offre.id_offre, "titre": offre.titre[:110]})

    annonceurs: dict[str, list[str]] = defaultdict(list)
    for annonce in referentiel.annonces:
        annonceurs[annonce.annonceur].append(annonce.id_annonce)

    marques_web: dict[str, list[str]] = defaultdict(list)
    for page in referentiel.pages:
        for marque in page.marques_detectees:
            marques_web[marque].append(page.id_page)

    serialiser = lambda valeur: json.dumps(valeur, ensure_ascii=False, indent=1)  # noqa: E731
    return {
        "marques": serialiser(
            [{"nom": nom, **donnees} for nom, donnees in sorted(marques.items())]
        ),
        "annonceurs": serialiser(
            [{"nom": nom, "nb_annonces": len(ids), "ids": ids} for nom, ids in sorted(annonceurs.items())]
        ),
        "marques_web": serialiser(
            [{"nom": nom, "ids_pages": ids} for nom, ids in sorted(marques_web.items())]
        ),
        "sans_marque": serialiser(sans_marque[:60]),
    }


def _corriger(
    concurrents: list[ConcurrentConsolide], referentiel: Referentiel
) -> tuple[list[ConcurrentConsolide], list[str]]:
    """Corrige mécaniquement le résultat de la consolidation.

    Args:
        concurrents: Concurrents proposés par le modèle.
        referentiel: Référentiel, source de vérité des identifiants.

    Returns:
        Le couple `(concurrents_corrigés, corrections_décrites)`.
    """
    ids_offres = {o.id_offre for o in referentiel.offres}
    ids_annonces = {a.id_annonce for a in referentiel.annonces}
    ids_pages = {p.id_page for p in referentiel.pages}

    corrections: list[str] = []
    deja_vus: set[str] = set()
    retenus: list[ConcurrentConsolide] = []

    for concurrent in concurrents:
        nom = concurrent.nom_canonique.strip()
        if not nom:
            continue

        def _filtrer(candidats: list[str], valides: set[str], famille: str) -> list[str]:
            gardes: list[str] = []
            for identifiant in candidats:
                if identifiant not in valides:
                    corrections.append(
                        f"identifiant {famille} inexistant « {identifiant} » retiré "
                        f"de « {nom} »"
                    )
                    continue
                if identifiant in deja_vus:
                    corrections.append(
                        f"identifiant {famille} « {identifiant} » déjà rattaché à un "
                        f"autre concurrent : retiré de « {nom} »"
                    )
                    continue
                deja_vus.add(identifiant)
                gardes.append(identifiant)
            return gardes

        concurrent.nom_canonique = nom
        concurrent.ids_offres = _filtrer(concurrent.ids_offres, ids_offres, "d'offre")
        concurrent.ids_annonces = _filtrer(concurrent.ids_annonces, ids_annonces, "d'annonce")
        concurrent.ids_pages = _filtrer(concurrent.ids_pages, ids_pages, "de page")
        if concurrent.niveau_certitude_rapprochement not in (
            CERTITUDE_SURE,
            CERTITUDE_PROBABLE,
        ):
            concurrent.niveau_certitude_rapprochement = CERTITUDE_PROBABLE
        concurrent.presence = {
            SOURCE_ALIEXPRESS: sum(
                1 for i in concurrent.ids_offres if i.startswith("ax-")
            ),
            SOURCE_AMAZON: sum(1 for i in concurrent.ids_offres if i.startswith("amz-")),
            SOURCE_META_ADS: len(concurrent.ids_annonces),
            SOURCE_WEB: len(concurrent.ids_pages),
        }
        if any(concurrent.presence.values()):
            retenus.append(concurrent)
        else:
            corrections.append(
                f"concurrent « {nom} » écarté : plus aucun identifiant valide après "
                f"correction"
            )

    # Rattrapage : toute offre orpheline rejoint le groupe « sans marque ».
    orphelines = [o.id_offre for o in referentiel.offres if o.id_offre not in deja_vus]
    if orphelines:
        groupe = next(
            (c for c in retenus if c.type == TYPE_SANS_MARQUE),
            None,
        )
        if groupe is None:
            groupe = ConcurrentConsolide(
                nom_canonique=NOM_GROUPE_SANS_MARQUE,
                type=TYPE_SANS_MARQUE,
                niveau_certitude_rapprochement=CERTITUDE_SURE,
            )
            retenus.append(groupe)
        groupe.ids_offres.extend(orphelines)
        groupe.presence = {
            SOURCE_ALIEXPRESS: sum(1 for i in groupe.ids_offres if i.startswith("ax-")),
            SOURCE_AMAZON: sum(1 for i in groupe.ids_offres if i.startswith("amz-")),
            SOURCE_META_ADS: len(groupe.ids_annonces),
            SOURCE_WEB: len(groupe.ids_pages),
        }
        corrections.append(
            f"{len(orphelines)} offre(s) non rattachée(s) par le modèle regroupée(s) "
            f"sous « {groupe.nom_canonique} »"
        )

    orphelines_annonces = [
        a.id_annonce for a in referentiel.annonces if a.id_annonce not in deja_vus
    ]
    if orphelines_annonces:
        index_annonceur: dict[str, list[str]] = defaultdict(list)
        for annonce in referentiel.annonces:
            if annonce.id_annonce in orphelines_annonces:
                index_annonceur[annonce.annonceur].append(annonce.id_annonce)
        for annonceur, identifiants in index_annonceur.items():
            retenus.append(
                ConcurrentConsolide(
                    nom_canonique=annonceur,
                    type="annonceur_seul",
                    ids_annonces=identifiants,
                    niveau_certitude_rapprochement=CERTITUDE_SURE,
                    presence={
                        SOURCE_ALIEXPRESS: 0,
                        SOURCE_AMAZON: 0,
                        SOURCE_META_ADS: len(identifiants),
                        SOURCE_WEB: 0,
                    },
                )
            )
        corrections.append(
            f"{len(orphelines_annonces)} annonce(s) non rattachée(s) par le modèle "
            f"rattachée(s) à leur annonceur d'origine"
        )
    return retenus, corrections


def consolider(
    referentiel: Referentiel, produit_nom: str, langue_analyse: str
) -> tuple[list[ConcurrentConsolide], StatutAnalyse, list[AlerteCoherence]]:
    """Consolide les concurrents à travers les sources.

    Args:
        referentiel: Référentiel complet.
        produit_nom: Nom du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le triplet `(concurrents, statut, alertes)`.
    """
    if referentiel.est_vide():
        return [], StatutAnalyse(phase=PHASE_CONSOLIDATION, succes=True, nb_elements=0), []

    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    gabarit = ChatPromptTemplate.from_messages([("system", _SYSTEME), ("human", _HUMAIN)])
    chaine = gabarit | modele.with_structured_output(LotConcurrents)

    entree = {"produit_nom": produit_nom, "langue_analyse": langue_analyse}
    entree.update(_vue_compacte(referentiel))
    resultat, tentatives, erreur = invoquer_structure(chaine, entree, PHASE_CONSOLIDATION)

    proposes = resultat.concurrents if resultat is not None else []
    concurrents, corrections = _corriger(proposes, referentiel)

    alertes: list[AlerteCoherence] = []
    probables = [
        c.nom_canonique
        for c in concurrents
        if c.niveau_certitude_rapprochement == CERTITUDE_PROBABLE and len(c.alias) > 0
    ]
    if probables:
        alertes.append(
            AlerteCoherence(
                type="rapprochement_incertain",
                detail=(
                    f"{len(probables)} concurrent(s) résultent d'un rapprochement "
                    f"seulement « probable » par similarité de nom : "
                    f"{', '.join(probables[:5])}. Toute statistique agrégée sur ces "
                    f"entités hérite de cette incertitude."
                ),
            )
        )

    statut = StatutAnalyse(
        phase=PHASE_CONSOLIDATION,
        succes=resultat is not None,
        message_erreur=(
            erreur
            if resultat is None
            else ("; ".join(corrections[:6]) if corrections else None)
        ),
        nb_elements=len(concurrents),
        nb_tentatives=tentatives,
    )
    logger.debug(
        "consolidation : %d concurrent(s), %d correction(s)", len(concurrents), len(corrections)
    )
    return concurrents, statut, alertes
