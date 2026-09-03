"""Chaînes qualitatives sur le modèle de synthèse.

Quatre chaînes LCEL :

1. **analyse par concurrent** (top N, un appel chacun) — proposition de valeur,
   forces et faiblesses étayées, niveau de menace ;
2. **lecture transversale** (un appel) — positionnement observé, angles peu
   exploités, normes de marché, et rédaction de la lecture d'intensité ;
3. **différenciation** (un appel) — position du produit étudié ;
4. **synthèse exécutive** (un appel).

Aucune de ces chaînes ne produit de nombre publiable : les chiffres viennent de
`benchmark.py` et sont réécrits par `validation.py`.
"""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate

from config import (
    MAX_AVIS_PREUVE_PAR_OFFRE,
    MAX_CARACTERES_EXTRAIT,
    MAX_TOKENS_SYNTHESE,
    MODELE_SYNTHESE,
    TOP_N_CONCURRENTS_ANALYSES,
    construire_modele,
    invoquer_structure,
)
from schemas import (
    AnalyseConcurrent,
    ConcurrentConsolide,
    Differenciation,
    FicheProduit,
    Referentiel,
    SortieBenchmark,
    SortieLectureTransversale,
    SortieSynthese,
    StatsConcurrent,
    StatutAnalyse,
)

PHASE_CONCURRENT: str = "analyse_concurrent"
PHASE_TRANSVERSALE: str = "lecture_transversale"
PHASE_DIFFERENCIATION: str = "differenciation"
PHASE_SYNTHESE: str = "synthese_executive"

_RAPPEL_PREUVES = (
    "- Chaque force et chaque faiblesse DOIT porter au moins une preuve, par "
    "identifiant EXACT tiré des données fournies (id d'offre, d'annonce, de page "
    "ou d'avis). Une preuve inventée sera retirée et l'item rétrogradé.\n"
    "- `statut` vaut « fait » uniquement si la preuve est une donnée constatée "
    "(note, volume, prix, texte d'avis, claim publicitaire). Toute interprétation, "
    "déduction ou supposition vaut « hypothese ».\n"
    "- `extrait` recopie un passage EXACT du texte de la preuve, jamais une "
    "reformulation, et au plus " + str(MAX_CARACTERES_EXTRAIT) + " caractères.\n"
)

_SYSTEME_CONCURRENT = (
    "Tu es analyste concurrentiel senior. Tu analyses UN concurrent à partir des "
    "seules données fournies.\n\n"
    "Produit étudié (le nôtre) : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse} — rédige tout dans cette langue.\n\n"
    "Consignes impératives :\n"
    + _RAPPEL_PREUVES
    + "- `niveau_menace` (« fort », « moyen », « faible ») se justifie par les "
    "statistiques fournies : volumes, notes, nombre d'offres, présence multi-sources, "
    "longévité publicitaire. Jamais par une intuition de marque.\n"
    "- Une longévité d'annonce mesure une durée de diffusion, JAMAIS une "
    "rentabilité : ne conclus rien sur la performance économique.\n"
    "- N'affirme aucune part de marché : le corpus ne la porte pas.\n"
    "- Si les données sont trop pauvres, dis-le dans `justification_menace` et "
    "laisse les listes courtes plutôt que de meubler.{erreur_precedente}"
)

_HUMAIN_CONCURRENT = (
    "CONCURRENT : {nom}\n"
    "Type : {type_concurrent} | certitude du rapprochement : {certitude}\n\n"
    "STATISTIQUES CALCULÉES\n{stats}\n\n"
    "SES OFFRES\n{offres}\n\n"
    "SES ANNONCES ET LEURS CLAIMS\n{annonces}\n\n"
    "AVIS CLIENTS RATTACHÉS À SES OFFRES\n{avis}\n\n"
    "EXTRAITS DE PAGES WEB LE MENTIONNANT\n{pages}"
)

_SYSTEME_TRANSVERSALE = (
    "Tu es analyste concurrentiel senior. Tu produis la lecture TRANSVERSALE d'un "
    "marché à partir des seules données fournies.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    + _RAPPEL_PREUVES
    + "- Les `angles_peu_exploites` sont des ABSENCES CONSTATÉES DANS LE CORPUS. "
    "Formule-les explicitement ainsi, en citant les volumes : « non observé dans le "
    "corpus ({nb_annonces} annonces, {nb_pages} pages) ». N'écris JAMAIS qu'un angle "
    "« n'existe pas sur le marché » : un corpus non exhaustif ne le prouve pas.\n"
    "- Les `normes_marche` sont les pratiques constatées de façon répétée : "
    "livraison, garanties, seuils de prix psychologiques, formats de bundle.\n"
    "- `lecture_intensite` commente les indicateurs d'intensité fournis en 4 à 6 "
    "phrases sourcées. Elle doit dire ce que ces indicateurs NE mesurent pas.\n"
    "- N'invente aucun nombre : reprends uniquement ceux qu'on te donne."
    "{erreur_precedente}"
)

_HUMAIN_TRANSVERSALE = (
    "CLAIMS PUBLICITAIRES AGRÉGÉS\n{claims}\n\n"
    "BENCHMARK DE PRIX (par source et par devise, sans conversion)\n{benchmark}\n\n"
    "INDICATEURS D'INTENSITÉ\n{intensite}\n\n"
    "ATTRIBUTS D'OFFRES LES PLUS FRÉQUENTS\n{attributs}\n\n"
    "PAGES WEB DU CORPUS\n{pages}"
)

_SYSTEME_DIFFERENCIATION = (
    "Tu es analyste concurrentiel senior. Tu situes le produit étudié face aux "
    "offres observées.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Description fournie par le porteur : {produit_description}\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Consignes impératives :\n"
    "- La description du produit étudié est une DÉCLARATION du porteur, non une "
    "donnée vérifiée. Tout item qui en dépend a le statut « hypothese ».\n"
    "- Un item ne peut valoir « fait » que si sa preuve est un élément du corpus "
    "concurrentiel (attribut d'offre, claim, extrait de page) montrant ce que font "
    "les concurrents.\n"
    + _RAPPEL_PREUVES
    + "- `attributs_partages` : ce que le produit a en commun avec l'offre observée.\n"
    "- `attributs_distinctifs_potentiels` : ce qui pourrait le distinguer — le mot "
    "« potentiels » est essentiel, rien n'est vérifié.\n"
    "- `desavantages_apparents` : ce que les concurrents offrent et que la fiche "
    "produit ne mentionne pas. L'absence dans une fiche n'est pas une absence dans "
    "le produit : dis-le.{erreur_precedente}"
)

_HUMAIN_DIFFERENCIATION = (
    "ATTRIBUTS OBSERVÉS CHEZ LES CONCURRENTS\n{attributs}\n\n"
    "ANGLES PEU EXPLOITÉS RELEVÉS\n{angles}\n\n"
    "NORMES DE MARCHÉ RELEVÉES\n{normes}\n\n"
    "BENCHMARK DE PRIX\n{benchmark}"
)

_SYSTEME_SYNTHESE = (
    "Tu es analyste concurrentiel senior. Tu rédiges la synthèse exécutive d'une "
    "analyse concurrentielle déjà produite.\n\n"
    "Produit étudié : {produit_nom}\n"
    "Langue d'analyse : {langue_analyse}.\n\n"
    "Contraintes de forme impératives : AU PLUS 12 lignes, dans cet ordre — "
    "structure du marché observé ; benchmark clé PAR DEVISE ; intensité "
    "concurrentielle ; 2 à 3 enseignements de positionnement ; la mise en garde "
    "régionale principale.\n\n"
    "Consignes impératives :\n"
    "- N'invente aucun nombre : n'utilise que ceux fournis.\n"
    "- Ne compare jamais deux montants de devises différentes.\n"
    "- N'affirme aucune part de marché ni aucun volume de marché.\n"
    "- Ne présente aucune recommandation ni aucun verdict : ce n'est pas ton rôle."
    "{erreur_precedente}"
)

_HUMAIN_SYNTHESE = (
    "CONCURRENTS (nom, présence, stats)\n{concurrents}\n\n"
    "BENCHMARK DE PRIX\n{benchmark}\n\n"
    "INTENSITÉ\n{intensite}\n\n"
    "POSITIONNEMENT\n{positionnement}\n\n"
    "PORTÉES RÉGIONALES DES SOURCES\n{validite}"
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


def analyser_concurrents(
    concurrents: list[ConcurrentConsolide],
    stats: dict[str, StatsConcurrent],
    referentiel: Referentiel,
    segment_par_offre: dict[str, str],
    produit: FicheProduit,
    langue_analyse: str,
) -> tuple[dict[str, AnalyseConcurrent], list[StatutAnalyse]]:
    """Analyse qualitativement les principaux concurrents, un appel chacun.

    Args:
        concurrents: Concurrents consolidés, déjà triés par importance.
        stats: Statistiques par concurrent.
        referentiel: Référentiel complet, source des preuves.
        segment_par_offre: Segment de prix par offre.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(analyses_par_nom, statuts)`.
    """
    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    # POINT DE CACHE — seule chaîne du dépôt qui remplit les deux conditions du
    # caching : plus d'un appel (un par concurrent, huit sur le run de référence)
    # ET un préfixe au-dessus du seuil du modèle (1 818 jetons mesurés contre
    # 1 024 exigés par Sonnet 5). L'appariement se fait sur le préfixe rendu dans
    # l'ordre `tools` → `system` → `messages` : le marqueur posé sur le bloc
    # système fait donc porter le cache sur la définition d'outil ET les consignes,
    # tandis que la charge utile propre à chaque concurrent reste après la coupure.
    # Le bloc système est identique d'un concurrent à l'autre — `produit_nom` et
    # `langue_analyse` sont constants sur une exécution — donc le premier appel
    # écrit (×1,25) et les suivants lisent (×0,10). Neutre par construction : la
    # charge utile envoyée est inchangée, seule sa facturation l'est.
    #
    # Limite connue : `erreur_precedente` termine le bloc système et devient non
    # vide sur une reprise (`invoquer_structure`). Une reprise invalide donc le
    # cache jusqu'à la fin de l'appel. C'est rare et sans conséquence ; déplacer
    # ce champ dans le message humain changerait la charge utile.
    gabarit = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                [
                    {
                        "type": "text",
                        "text": _SYSTEME_CONCURRENT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            ),
            ("human", _HUMAIN_CONCURRENT),
        ]
    )
    chaine = gabarit | modele.with_structured_output(AnalyseConcurrent)

    index_offres = {o.id_offre: o for o in referentiel.offres}
    index_annonces = {a.id_annonce: a for a in referentiel.annonces}
    index_pages = {p.id_page: p for p in referentiel.pages}
    avis_par_offre: dict[str, list] = {}
    for avis in referentiel.avis:
        avis_par_offre.setdefault(avis.id_offre, []).append(avis)

    analyses: dict[str, AnalyseConcurrent] = {}
    statuts: list[StatutAnalyse] = []

    for concurrent in concurrents[:TOP_N_CONCURRENTS_ANALYSES]:
        offres = [index_offres[i] for i in concurrent.ids_offres if i in index_offres]
        annonces = [index_annonces[i] for i in concurrent.ids_annonces if i in index_annonces]
        pages = [index_pages[i] for i in concurrent.ids_pages if i in index_pages]
        avis: list = []
        for offre in offres:
            avis.extend(avis_par_offre.get(offre.id_offre, [])[:MAX_AVIS_PREUVE_PAR_OFFRE])

        resultat, tentatives, erreur = invoquer_structure(
            chaine,
            {
                "produit_nom": produit.nom,
                "langue_analyse": langue_analyse,
                "nom": concurrent.nom_canonique,
                "type_concurrent": concurrent.type,
                "certitude": concurrent.niveau_certitude_rapprochement,
                "stats": _json(stats.get(concurrent.nom_canonique, StatsConcurrent()).model_dump()),
                "offres": _json(
                    [
                        {
                            "id_offre": o.id_offre,
                            "source": o.source,
                            "titre": o.titre[:160],
                            "prix": o.prix,
                            "devise": o.devise,
                            "segment_prix": segment_par_offre.get(o.id_offre),
                            "note": o.note,
                            "nb_avis": o.nb_avis_ou_evaluations,
                            "volume_ventes": o.volume_ventes,
                            "badges": o.badges,
                            "attributs": o.attributs_extraits,
                        }
                        for o in offres[:20]
                    ]
                ),
                "annonces": _json(
                    [
                        {
                            "id_annonce": a.id_annonce,
                            "cta": a.cta,
                            "active": a.active,
                            "duree_diffusion_jours": a.duree_diffusion_jours,
                            "plateformes": a.plateformes,
                            "claims": a.claims.model_dump() if a.claims else None,
                            "texte": a.texte_complet[:400],
                        }
                        for a in annonces[:12]
                    ]
                ),
                "avis": _json(
                    [
                        {
                            "id_reference": v.id_avis,
                            "note": v.note,
                            "texte": v.texte[:400],
                        }
                        for v in avis[:MAX_AVIS_PREUVE_PAR_OFFRE * 2]
                    ]
                ),
                "pages": _json(
                    [
                        {
                            "id_page": p.id_page,
                            "domaine": p.domaine,
                            "titre": p.titre,
                            "extrait": p.extrait[:600],
                        }
                        for p in pages[:5]
                    ]
                ),
            },
            f"{PHASE_CONCURRENT} « {concurrent.nom_canonique} »",
        )
        if resultat is not None:
            analyses[concurrent.nom_canonique] = resultat
        statuts.append(
            StatutAnalyse(
                phase=PHASE_CONCURRENT,
                succes=resultat is not None,
                message_erreur=(
                    None
                    if resultat is not None
                    else f"« {concurrent.nom_canonique} » non analysé : {erreur}"
                ),
                nb_elements=1 if resultat is not None else 0,
                nb_tentatives=tentatives,
            )
        )
    return analyses, statuts


def lire_transversalement(
    referentiel: Referentiel,
    chiffres: SortieBenchmark,
    produit: FicheProduit,
    langue_analyse: str,
) -> tuple[SortieLectureTransversale | None, StatutAnalyse]:
    """Produit le positionnement observé et la lecture d'intensité.

    Args:
        referentiel: Référentiel complet.
        chiffres: Résultats chiffrés du benchmark.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(sortie_ou_None, statut)`.
    """
    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_TRANSVERSALE), ("human", _HUMAIN_TRANSVERSALE)]
    )
    chaine = gabarit | modele.with_structured_output(SortieLectureTransversale)

    frequences: dict[str, int] = {}
    for offre in referentiel.offres:
        for attribut in offre.attributs_extraits:
            frequences[attribut] = frequences.get(attribut, 0) + 1
    attributs_tries = sorted(frequences.items(), key=lambda kv: (-kv[1], kv[0]))[:40]

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "langue_analyse": langue_analyse,
            "nb_annonces": len(referentiel.annonces),
            "nb_pages": len(referentiel.pages),
            "claims": _json(
                [
                    {
                        "id_annonce": a.id_annonce,
                        "annonceur": a.annonceur,
                        "cta": a.cta,
                        "claims": a.claims.model_dump() if a.claims else None,
                    }
                    for a in referentiel.annonces[:40]
                ]
            ),
            "benchmark": _json([b.model_dump() for b in chiffres.benchmarks]),
            "intensite": _json(chiffres.intensite.model_dump() if chiffres.intensite else {}),
            "attributs": _json([{"attribut": a, "nb_offres": n} for a, n in attributs_tries]),
            "pages": _json(
                [
                    {
                        "id_page": p.id_page,
                        "domaine": p.domaine,
                        "type_source": p.type_source,
                        "titre": p.titre,
                        "extrait": p.extrait[:500],
                    }
                    for p in referentiel.pages[:12]
                ]
            ),
        },
        PHASE_TRANSVERSALE,
    )
    return resultat, StatutAnalyse(
        phase=PHASE_TRANSVERSALE,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_tentatives=tentatives,
    )


def analyser_differenciation(
    referentiel: Referentiel,
    lecture: SortieLectureTransversale | None,
    chiffres: SortieBenchmark,
    produit: FicheProduit,
    langue_analyse: str,
) -> tuple[Differenciation | None, StatutAnalyse]:
    """Situe le produit étudié face aux offres observées.

    Args:
        referentiel: Référentiel complet.
        lecture: Lecture transversale, éventuellement absente.
        chiffres: Résultats chiffrés du benchmark.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(differenciation_ou_None, statut)`.
    """
    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_DIFFERENCIATION), ("human", _HUMAIN_DIFFERENCIATION)]
    )
    chaine = gabarit | modele.with_structured_output(Differenciation)

    frequences: dict[str, list[str]] = {}
    for offre in referentiel.offres:
        for attribut in offre.attributs_extraits:
            frequences.setdefault(attribut, []).append(offre.id_offre)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "produit_description": produit.description,
            "langue_analyse": langue_analyse,
            "attributs": _json(
                [
                    {"attribut": a, "nb_offres": len(ids), "ids_offres": ids[:5]}
                    for a, ids in sorted(frequences.items(), key=lambda kv: -len(kv[1]))[:40]
                ]
            ),
            "angles": _json(
                [p.model_dump() for p in lecture.positionnement.angles_peu_exploites]
                if lecture
                else []
            ),
            "normes": _json(
                [p.model_dump() for p in lecture.positionnement.normes_marche]
                if lecture
                else []
            ),
            "benchmark": _json([b.model_dump() for b in chiffres.benchmarks]),
        },
        PHASE_DIFFERENCIATION,
    )
    return resultat, StatutAnalyse(
        phase=PHASE_DIFFERENCIATION,
        succes=resultat is not None,
        message_erreur=erreur,
        nb_tentatives=tentatives,
    )


def rediger_synthese(
    concurrents_resumes: list[dict],
    chiffres: SortieBenchmark,
    lecture: SortieLectureTransversale | None,
    validite: list[dict],
    produit: FicheProduit,
    langue_analyse: str,
) -> tuple[str, StatutAnalyse]:
    """Rédige la synthèse exécutive.

    Args:
        concurrents_resumes: Résumés chiffrés des concurrents.
        chiffres: Résultats chiffrés du benchmark.
        lecture: Lecture transversale, éventuellement absente.
        validite: Portées régionales des sources.
        produit: Fiche du produit étudié.
        langue_analyse: Code langue de rédaction.

    Returns:
        Le couple `(synthese, statut)`. La synthèse est vide en cas d'échec :
        l'appelant produit alors un repli par le code.
    """
    modele = construire_modele(MODELE_SYNTHESE, MAX_TOKENS_SYNTHESE)
    gabarit = ChatPromptTemplate.from_messages(
        [("system", _SYSTEME_SYNTHESE), ("human", _HUMAIN_SYNTHESE)]
    )
    chaine = gabarit | modele.with_structured_output(SortieSynthese)

    resultat, tentatives, erreur = invoquer_structure(
        chaine,
        {
            "produit_nom": produit.nom,
            "langue_analyse": langue_analyse,
            "concurrents": _json(concurrents_resumes),
            "benchmark": _json([b.model_dump() for b in chiffres.benchmarks]),
            "intensite": _json(chiffres.intensite.model_dump() if chiffres.intensite else {}),
            "positionnement": _json(
                lecture.positionnement.model_dump() if lecture else {}
            ),
            "validite": _json(validite),
        },
        PHASE_SYNTHESE,
    )
    return (
        resultat.synthese_executive if resultat is not None else "",
        StatutAnalyse(
            phase=PHASE_SYNTHESE,
            succes=resultat is not None,
            message_erreur=erreur,
            nb_tentatives=tentatives,
        ),
    )
