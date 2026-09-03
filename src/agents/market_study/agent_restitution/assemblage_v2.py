"""Assemblage du rapport décisionnel — **aucun appel LLM ici**.

Cinq écrans, dans un ordre fixe, chacun un titre `##` et des sous-blocs `###`
formulés en questions métier. Le rendu est entièrement déterministe.

Ce que ce module garantit, et que le v1 ne garantissait pas :

- **Un seul résumé dans le document.** `resume_executif.md` est la copie exacte de
  l'écran « Décision » ; le corps du rapport ne le répète pas.
- **Aucune cellule ne se termine par « … ».** Les textes longs sont arrivés ici
  déjà compressés ou coupés au mot entier.
- **Les blocs secondaires sont repliés** dans des `<details>` plutôt que
  supprimés : le détail reste accessible, il ne s'impose plus à la lecture.
- **Une section dégradée tient en une ligne.** Le v1 consacrait une page à dire
  « non disponible ».

Les commentaires HTML de traçabilité sont conservés — ils servent l'audit — et
c'est au frontend de les masquer.
"""

from __future__ import annotations

from config import (
    ECRAN_CONCURRENCE,
    ECRAN_CONSOMMATEUR,
    ECRAN_DECISION,
    ECRAN_METHODE,
    ECRAN_RECOMMANDATIONS,
    ENCART_NARRATIF_INDISPONIBLE,
    ENTREE_CONCURRENCE,
    ENTREE_INSIGHTS,
    ENTREE_PLC,
    ENTREE_RECOMMANDATIONS,
    GABARIT_RAPPORT_V2,
    GLOSSAIRE,
    MARQUEUR_GABARIT_V2,
    METHODOLOGIE,
    PHRASE_RAPPEL_PRIX,
    SB_ACTIONS,
    SB_AIMERAIENT,
    SB_APPRECIENT,
    SB_CHANGER_DECISION,
    SB_CINQ_FORCES,
    SB_DERANGE,
    SB_DYNAMIQUE,
    SB_ENTREE_MARCHE,
    SB_EXEMPLES,
    SB_MANQUE_TRANCHER,
    SB_OPPORTUNITES_RISQUES,
    SB_PERSONNE_NE_FAIT,
    SB_PHASE,
    SB_POURQUOI,
    SB_POURQUOI_ACHAT,
    SB_PRIX,
    SB_PRIX_PRATIQUES,
    SB_QUE_FONT,
    SB_RISQUE_PRINCIPAL,
)
from preparation import tableau
from preparation_v2 import compter_mots
from schemas import Injectables, SectionProduite, SortieEcran

_TITRES_SOUS_BLOCS: dict[str, str] = {
    identifiant: libelle
    for ecran in GABARIT_RAPPORT_V2
    for identifiant, libelle in ecran["sous_blocs"]
}


def _bloc(*morceaux: str) -> str:
    """Assemble des morceaux non vides en un bloc Markdown.

    Args:
        *morceaux: Morceaux de texte, éventuellement vides.

    Returns:
        Le bloc, morceaux séparés d'une ligne blanche.
    """
    return "\n\n".join(m.strip() for m in morceaux if m and m.strip())


def _puces(elements: list[str]) -> str:
    """Rend une liste à puces Markdown.

    Args:
        elements: Éléments de la liste.

    Returns:
        La liste, ou une chaîne vide.
    """
    return "\n".join(f"- {e.strip()}" for e in elements if e and e.strip())


def _commentaire_sources(refs: list[str]) -> str:
    """Rend le commentaire HTML de traçabilité d'un écran.

    Args:
        refs: Références des sources de l'écran.

    Returns:
        Le commentaire HTML, invisible au rendu.
    """
    return f"<!-- sources: {'; '.join(refs) if refs else 'aucune'} -->"


def _sous_bloc(
    identifiant: str,
    injectables: Injectables,
    narratif: SortieEcran | None,
    *,
    avant: str = "",
    apres: str = "",
) -> str:
    """Rend un sous-bloc : son titre-question, puis son contenu.

    Trois contenus possibles, dans cet ordre de priorité : la phrase standard si
    la donnée manque, les puces du modèle si elles existent, l'encart de repli
    sinon. Un sous-bloc n'est jamais rendu vide.

    Args:
        identifiant: Identifiant du sous-bloc.
        injectables: Données injectables.
        narratif: Puces produites pour l'écran, ou `None`.
        avant: Contenu du code inséré avant les puces (tableau, ligne…).
        apres: Contenu du code inséré après les puces.

    Returns:
        Le sous-bloc Markdown complet.
    """
    titre = f"### {_TITRES_SOUS_BLOCS.get(identifiant, identifiant)}"
    standard = injectables.sous_blocs_standards.get(identifiant)
    if standard:
        return _bloc(titre, standard)

    puces = list(narratif.sous_blocs.get(identifiant, [])) if narratif else []
    corps = _puces(puces)
    if not (corps or avant or apres):
        corps = ENCART_NARRATIF_INDISPONIBLE
    return _bloc(titre, avant, corps, apres)


# =========================================================================== #
# Écran 0 — Décision
# =========================================================================== #


def ecran_decision(injectables: Injectables, narratif: SortieEcran | None) -> str:
    """Rend l'écran de décision — le seul résumé du document.

    Args:
        injectables: Données injectables.
        narratif: Puces produites pour cet écran.

    Returns:
        Le corps de l'écran, titre `##` compris.
    """
    return _bloc(
        f"## Décision : {injectables.decision_libelle}",
        injectables.ligne_verdict,
        _sous_bloc(SB_POURQUOI, injectables, narratif),
        _sous_bloc(SB_RISQUE_PRINCIPAL, injectables, narratif),
        _bloc(
            f"### {_TITRES_SOUS_BLOCS[SB_CHANGER_DECISION]}",
            _puces(injectables.puces_changer_decision),
        ),
        _bloc(
            f"### {_TITRES_SOUS_BLOCS[SB_MANQUE_TRANCHER]}",
            _puces(injectables.puces_manque_trancher),
        )
        if injectables.puces_manque_trancher
        else "",
        injectables.ligne_sources,
    )


# =========================================================================== #
# Écran 1 — Le consommateur
# =========================================================================== #


def _points_de_friction(
    injectables: Injectables, narratif: SortieEcran | None
) -> str:
    """Rend les points de friction : titre, chiffres du code, phrase du modèle.

    Le titre, la fréquence et l'intensité viennent du code. Seule la phrase
    d'explication vient du modèle, et elle est appariée par RANG : la n-ième
    phrase commente le n-ième point. Un rang sans phrase affiche le libellé seul
    plutôt qu'une phrase empruntée à un autre point.

    Args:
        injectables: Données injectables.
        narratif: Puces produites pour l'écran.

    Returns:
        Le bloc des points de friction.
    """
    phrases = list(narratif.sous_blocs.get(SB_DERANGE, [])) if narratif else []
    morceaux: list[str] = []
    for rang, point in enumerate(injectables.pain_points):
        chiffres = " · ".join(
            partie
            for partie in (
                f"{point['frequence']} des contributions" if point.get("frequence") else "",
                f"intensité {point['intensite']}" if point.get("intensite") else "",
            )
            if partie
        )
        entete = f"- **{point['libelle']}**"
        if chiffres:
            entete += f" — {chiffres}"
        if rang < len(phrases) and phrases[rang].strip():
            entete += f" — {phrases[rang].strip()}"
        morceaux.append(entete)

        extrait = injectables.verbatims.get(point["cle"])
        if extrait is not None:
            morceaux.append(
                f"  > « {extrait.texte} »\n"
                f"  <!-- extrait: {extrait.id_unite} ({extrait.source}) -->"
            )
    return "\n".join(morceaux)


def ecran_consommateur(injectables: Injectables, narratif: SortieEcran | None) -> str:
    """Rend l'écran consommateur.

    Args:
        injectables: Données injectables.
        narratif: Puces produites pour cet écran.

    Returns:
        Le corps de l'écran, titre `##` compris.
    """
    friction = _points_de_friction(injectables, narratif)
    return _bloc(
        "## Le consommateur",
        injectables.badges.get(ECRAN_CONSOMMATEUR, ""),
        injectables.mentions_partielles.get(ECRAN_CONSOMMATEUR, ""),
        _sous_bloc(SB_POURQUOI_ACHAT, injectables, narratif),
        _sous_bloc(SB_APPRECIENT, injectables, narratif),
        _bloc(f"### {_TITRES_SOUS_BLOCS[SB_DERANGE]}", friction)
        if friction
        else _sous_bloc(SB_DERANGE, injectables, narratif),
        _sous_bloc(SB_AIMERAIENT, injectables, narratif),
        "**Répartition du sentiment par source**"
        if injectables.tableau_sentiment
        else "",
        injectables.tableau_sentiment,
        _puces(injectables.divergences),
        injectables.details_besoins_attentes,
    )


# =========================================================================== #
# Écran 2 — Le marché et les concurrents
# =========================================================================== #


def _tableau_exemples(injectables: Injectables) -> str:
    """Rend le tableau de repli des concurrents observés.

    Le frontend remplace les marqueurs de widget par ses bandes d'images ; ce
    tableau reste dans le Markdown pour que le fichier téléchargé se lise seul.

    Args:
        injectables: Données injectables.

    Returns:
        Le tableau Markdown, ou une chaîne vide.
    """
    return tableau(
        ["Concurrent", "Canal", "Prix", "Note", "Volume", "Force", "Faiblesse"],
        [
            [
                ligne["concurrent"],
                ligne["canal"],
                ligne["prix"],
                ligne["note"],
                ligne["volume"],
                ligne.get("force") or ligne.get("force_brute", ""),
                ligne.get("faiblesse") or ligne.get("faiblesse_brute", ""),
            ]
            for ligne in injectables.concurrents_v2
        ],
    )


def ecran_concurrence(injectables: Injectables, narratif: SortieEcran | None) -> str:
    """Rend l'écran marché et concurrents.

    Args:
        injectables: Données injectables.
        narratif: Puces produites pour cet écran.

    Returns:
        Le corps de l'écran, titre `##` compris.
    """
    exemples = _bloc(
        f"### {_TITRES_SOUS_BLOCS[SB_EXEMPLES]}",
        "\n".join(injectables.widgets_extraits),
        _tableau_exemples(injectables),
    )
    prix = _sous_bloc(
        SB_PRIX_PRATIQUES,
        injectables,
        narratif,
        avant=_bloc(
            injectables.tableau_benchmark,
            "> **Aucune conversion de devise n'a été effectuée.** Deux prix libellés "
            "dans deux devises décrivent deux marchés, pas le même montant."
            if injectables.tableau_benchmark
            else "",
            _puces(injectables.portee_regionale),
        ),
    )
    forces = _bloc(
        f"### {_TITRES_SOUS_BLOCS[SB_CINQ_FORCES]}", injectables.tableau_cinq_forces
    )
    angles = _bloc(
        f"### {_TITRES_SOUS_BLOCS[SB_PERSONNE_NE_FAIT]}",
        _puces(injectables.puces_personne_ne_fait)
        or injectables.sous_blocs_standards.get(SB_PERSONNE_NE_FAIT, ""),
        injectables.details_angles,
    )
    return _bloc(
        "## Le marché et les concurrents",
        injectables.badges.get(ECRAN_CONCURRENCE, ""),
        injectables.mentions_partielles.get(ECRAN_CONCURRENCE, ""),
        _sous_bloc(
            SB_DYNAMIQUE, injectables, narratif, avant=injectables.dynamique_demande
        ),
        _sous_bloc(SB_QUE_FONT, injectables, narratif),
        exemples if injectables.concurrents_v2 else "",
        prix,
        forces,
        angles,
    )


# =========================================================================== #
# Écran 3 — Ce que nous recommandons
# =========================================================================== #


def _tableau_actions(injectables: Injectables) -> str:
    """Rend le tableau des actions de priorité 1.

    Args:
        injectables: Données injectables.

    Returns:
        Le tableau Markdown, ou une chaîne vide.
    """
    return tableau(
        ["Action", "Domaine", "Horizon", "Effort", "Indicateur de suivi"],
        [
            [
                action.get("enonce") or action.get("enonce_brut", ""),
                action["domaine"],
                action["horizon"],
                action["effort"],
                action["indicateur"],
            ]
            for action in injectables.actions_p1
        ],
    )


def ecran_recommandations(
    injectables: Injectables, narratif: SortieEcran | None
) -> str:
    """Rend l'écran des recommandations.

    Args:
        injectables: Données injectables.
        narratif: Puces produites pour cet écran.

    Returns:
        Le corps de l'écran, titre `##` compris.
    """
    phase = _bloc(
        f"### {_TITRES_SOUS_BLOCS[SB_PHASE]}",
        injectables.sous_blocs_standards.get(SB_PHASE, ""),
        injectables.ligne_phases,
        f"Incertitude de la classification : {injectables.incertitude_phase}."
        if injectables.phase_brute and injectables.incertitude_phase
        else "",
        _puces(injectables.puces_phase),
    )
    actions = _bloc(
        f"### {_TITRES_SOUS_BLOCS[SB_ACTIONS]}",
        _tableau_actions(injectables),
        injectables.tableau_actions_suivantes,
    )
    prix = _sous_bloc(
        SB_PRIX,
        injectables,
        narratif,
        avant=f"**Fourchette proposée : {injectables.fourchette_prix}.**"
        if injectables.fourchette_prix
        else "",
        apres=f"> {PHRASE_RAPPEL_PRIX}" if injectables.fourchette_prix else "",
    )
    opportunites = _bloc(
        f"### {_TITRES_SOUS_BLOCS[SB_OPPORTUNITES_RISQUES]}",
        "**Opportunités**" if injectables.puces_opportunites else "",
        _puces(injectables.puces_opportunites),
        "**Risques**" if injectables.puces_risques else "",
        _puces(injectables.puces_risques),
        injectables.details_opportunites_risques,
    )
    return _bloc(
        "## Ce que nous recommandons",
        injectables.badges.get(ECRAN_RECOMMANDATIONS, ""),
        phase,
        actions,
        prix,
        _sous_bloc(SB_ENTREE_MARCHE, injectables, narratif),
        opportunites,
    )


# =========================================================================== #
# Écran 4 — Méthode et limites
# =========================================================================== #


def ecran_methode(injectables: Injectables) -> str:
    """Rend l'écran méthode, entièrement replié.

    Args:
        injectables: Données injectables.

    Returns:
        Le corps de l'écran, titre `##` compris.
    """
    limites: list[str] = []
    for famille, elements in injectables.limites_par_famille:
        limites.append(f"**{famille}**")
        limites.append(_puces(elements))
    glossaire = _puces(
        [f"**{terme}** — {definition}" for terme, definition in GLOSSAIRE]
    )
    contenu = _bloc(
        "**Sources et volumes exploités**" if injectables.annexe_sources else "",
        injectables.annexe_sources,
        "**Période couverte**",
        injectables.annexe_periode,
        "**Méthode**",
        # Les points de méthode portent déjà leur numéro : les préfixer d'une
        # puce produirait « - 1. … ».
        "\n".join(METHODOLOGIE),
        "**Limites de l'étude**",
        "Les limites ci-dessous sont reprises telles quelles des analyses amont : "
        "elles ne sont ni réécrites, ni atténuées.",
        _bloc(*limites),
        "**Hypothèses assumées**" if injectables.hypotheses else "",
        _puces(injectables.hypotheses),
        "**Glossaire des indicateurs**",
        glossaire,
    )
    return (
        "## Méthode et limites\n\n"
        "<details>\n<summary>Méthode, sources et limites</summary>\n\n"
        f"{contenu}\n\n</details>"
    )


# =========================================================================== #
# Assemblage
# =========================================================================== #

_RENDUS = {
    ECRAN_DECISION: ecran_decision,
    ECRAN_CONSOMMATEUR: ecran_consommateur,
    ECRAN_CONCURRENCE: ecran_concurrence,
    ECRAN_RECOMMANDATIONS: ecran_recommandations,
}

_ENTREES_PAR_ECRAN: dict[str, list[str]] = {
    ECRAN_DECISION: [ENTREE_RECOMMANDATIONS],
    ECRAN_CONSOMMATEUR: [ENTREE_INSIGHTS],
    ECRAN_CONCURRENCE: [ENTREE_CONCURRENCE],
    ECRAN_RECOMMANDATIONS: [ENTREE_RECOMMANDATIONS, ENTREE_PLC],
    ECRAN_METHODE: [ENTREE_RECOMMANDATIONS],
}


def _entete(injectables: Injectables) -> str:
    """Rend l'en-tête du rapport : deux lignes, et le marqueur de gabarit.

    Args:
        injectables: Données injectables.

    Returns:
        Le bloc d'en-tête Markdown.
    """
    meta = injectables.entete
    return _bloc(
        MARQUEUR_GABARIT_V2,
        f"# {meta.get('produit', '')} — marché {meta.get('marche', '')}",
        injectables.ligne_meta,
        injectables.encart_partielle_v2,
    )


def assembler_rapport_v2(
    injectables: Injectables,
    narratifs: dict[str, SortieEcran | None],
) -> tuple[str, list[SectionProduite]]:
    """Assemble le rapport décisionnel complet.

    Args:
        injectables: Données injectables construites par le code.
        narratifs: Puces produites par les chaînes de rédaction, par écran.

    Returns:
        Le couple `(markdown, sections_produites)`.
    """
    document: list[str] = [_entete(injectables)]
    sections: list[SectionProduite] = []

    for ecran in GABARIT_RAPPORT_V2:
        identifiant = ecran["id"]
        narratif = narratifs.get(identifiant)
        if identifiant == ECRAN_METHODE:
            corps = ecran_methode(injectables)
        else:
            corps = _RENDUS[identifiant](injectables, narratif)
        refs = injectables.refs_par_section.get(identifiant, [])
        document.append(f"\n---\n\n{corps}\n\n{_commentaire_sources(refs)}")

        produites = sorted(narratif.sous_blocs) if narratif else []
        sections.append(
            SectionProduite(
                id_section=identifiant,
                titre=ecran["titre"],
                entrees_utilisees=[
                    entree
                    for entree in _ENTREES_PAR_ECRAN.get(identifiant, [])
                    if entree not in injectables.sections_absentes
                ],
                badge_confiance=injectables.badges.get(identifiant),
                nb_mots_narratif=compter_mots(
                    " ".join(
                        puce
                        for puces in (narratif.sous_blocs.values() if narratif else [])
                        for puce in puces
                    )
                ),
                degradee=identifiant in injectables.sections_degradees,
                refs_sources=refs,
                sous_blocs_produits=produites,
                nb_mots_budget=ecran["budget_mots"],
            )
        )
    return "\n".join(document) + "\n", sections


def assembler_resume_v2(
    injectables: Injectables, narratifs: dict[str, SortieEcran | None]
) -> str:
    """Assemble le résumé exécutif : l'écran de décision, et rien d'autre.

    Le v1 produisait un résumé qui répétait mot pour mot la première section du
    rapport. Ici, il n'y a plus qu'un seul texte, publié à deux endroits.

    Args:
        injectables: Données injectables.
        narratifs: Puces produites par les chaînes de rédaction.

    Returns:
        Le résumé Markdown.
    """
    return (
        _bloc(
            _entete(injectables),
            ecran_decision(injectables, narratifs.get(ECRAN_DECISION)),
        )
        + "\n"
    )
