"""Conformité au gabarit v2 de l'agent de restitution (F7).

Aucun de ces tests n'appelle un modèle ni une base : ils portent sur les garde-fous
que le correctif du run 8609db9e a introduits, et qui doivent tenir sans réseau.

Ce run est le cas de référence. F7 y a rendu un rapport dont les sept sous-blocs
narratifs disaient « Lecture narrative indisponible », en code de sortie 0, et
l'étude a été marquée ``completed`` : un décideur a reçu un document sans une phrase
d'analyse, avec l'apparence d'une étude complète. La cause était une variable de
gabarit de prompt jamais injectée — ``{max_mots}`` — et rien, ni dans la rédaction ni
dans la post-validation, ne s'en est aperçu.

Les modules de ``agent_restitution`` s'importent par nom nu (``from config import``) :
ils sont exécutés en sous-processus par l'orchestrateur, jamais importés par le
backend. D'où l'insertion de leur répertoire dans ``sys.path`` ci-dessous.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

RACINE_F7 = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agents"
    / "market_study"
    / "agent_restitution"
)
if str(RACINE_F7) not in sys.path:
    sys.path.insert(0, str(RACINE_F7))

config = importlib.import_module("config")
preparation_v2 = importlib.import_module("preparation_v2")
redaction_v2 = importlib.import_module("redaction_v2")
validation_v2 = importlib.import_module("validation_v2")
schemas = importlib.import_module("schemas")


# ---------------------------------------------------------------------------
# 1. Le défaut du run 8609db9e ne peut plus atteindre une étude
# ---------------------------------------------------------------------------
def test_un_gabarit_et_son_invocation_ne_peuvent_pas_diverger() -> None:
    """Le contrat d'invocation est vérifié au chargement, pas à la première requête.

    Reproduction exacte de l'incident : une variable est ajoutée au prompt système
    sans être injectée. Avant le correctif, cela passait l'import, passait la
    création de l'étude, passait les six collecteurs et les quatre analyses, et
    n'échouait qu'au premier appel au modèle — deux fois, à l'identique, puisque la
    reprise resoumettait la même entrée.
    """
    assert redaction_v2._verifier_contrat_invocation() is None  # état sain

    original = config.REGLES_FORMULATION_V2
    try:
        config.REGLES_FORMULATION_V2 = original + "\n- Orpheline {marqueur_absent}."
        recharge = importlib.reload(redaction_v2)
        pytest.fail(f"le module a chargé malgré la variable orpheline : {recharge}")
    except config.ConfigurationRedactionInvalide as erreur:
        assert "marqueur_absent" in str(erreur)
    finally:
        config.REGLES_FORMULATION_V2 = original
        importlib.reload(redaction_v2)


def test_chaque_sous_bloc_confie_au_modele_a_un_contrat() -> None:
    """`CONTRAT_SOUS_BLOCS` et `SOUS_BLOCS_REDIGES` décrivent le même ensemble.

    Un sous-bloc confié sans contrat sortirait sans être vérifié — c'est l'angle mort
    d'origine. Un contrat sans sous-bloc serait une règle que rien n'applique.
    """
    confies = {
        sous_bloc
        for blocs in config.SOUS_BLOCS_REDIGES.values()
        for sous_bloc in blocs
    }
    assert confies == set(config.CONTRAT_SOUS_BLOCS)


def test_une_erreur_deterministe_n_est_jamais_retentee() -> None:
    """Rejouer un gabarit cassé, c'est payer deux fois la même erreur.

    Le v1 retentait tout. Sur le run 8609db9e, huit invocations ont été facturées
    pour huit fois le même `KeyError`.
    """
    tentatives = []

    class ChaineCassee:
        def invoke(self, _variables: dict) -> None:
            tentatives.append(1)
            raise KeyError("max_mots")

    with pytest.raises(config.RedactionImpossible, match="déterministe"):
        redaction_v2.invoquer_ecran(ChaineCassee(), {}, "test")
    assert tentatives == [1], "une erreur déterministe ne se retente pas"


def test_une_erreur_transitoire_est_retentee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une panne réseau, elle, mérite les trois tentatives."""
    monkeypatch.setattr(redaction_v2, "BACKOFF_SECONDES", (0.0, 0.0))
    tentatives = []

    # Reconnue par son NOM : `_est_transitoire` classe sans importer `anthropic`,
    # dont ce module ne doit pas dépendre pour se charger.
    class SurchargeError(Exception):
        pass

    SurchargeError.__name__ = "RateLimitError"

    class ChaineSaturee:
        def invoke(self, _variables: dict) -> None:
            tentatives.append(1)
            raise SurchargeError("429")

    with pytest.raises(config.RedactionImpossible):
        redaction_v2.invoquer_ecran(ChaineSaturee(), {}, "test")
    assert len(tentatives) == redaction_v2.NB_TENTATIVES_TRANSITOIRE


# ---------------------------------------------------------------------------
# 2. Le gabarit est vérifié par le code, sous-bloc par sous-bloc
# ---------------------------------------------------------------------------
def _sortie(**sous_blocs: list[str]) -> schemas.SortieEcran:
    return schemas.SortieEcran(sous_blocs=dict(sous_blocs))


def test_un_nombre_de_puces_hors_contrat_est_un_ecart() -> None:
    ecarts = redaction_v2.ecarts_au_contrat(
        _sortie(pourquoi=["Deux puces 12 %.", "Au lieu de trois 8 %."]),
        [config.SB_POURQUOI],
    )
    assert any("2 puce(s) au lieu de 3" in ecart for ecart in ecarts)


def test_un_sous_bloc_vide_est_un_ecart() -> None:
    """Le cas exact du run 8609db9e : plus d'encart de repli, un écart."""
    ecarts = redaction_v2.ecarts_au_contrat(_sortie(pourquoi=[]), [config.SB_POURQUOI])
    assert any("aucune puce" in ecart for ecart in ecarts)


def test_les_cinq_libelles_des_concurrents_sont_exiges_dans_l_ordre() -> None:
    puces = [f"**{libelle}** — quelque chose." for libelle in config.LIBELLES_QUE_FONT]
    assert not redaction_v2.ecarts_au_contrat(
        _sortie(que_font_concurrents=puces), [config.SB_QUE_FONT]
    )

    permutees = [puces[1], puces[0], *puces[2:]]
    assert redaction_v2.ecarts_au_contrat(
        _sortie(que_font_concurrents=permutees), [config.SB_QUE_FONT]
    )


def test_le_chiffre_n_est_exige_que_la_ou_le_fait_en_porte_un() -> None:
    """Amendement A6 : un fait clé booléen n'a aucun chiffre à recopier.

    Exiger un chiffre là où la source n'en porte pas, c'est demander au modèle d'en
    inventer un — que la liste blanche retirerait ensuite.
    """
    chiffrables = {config.SB_POURQUOI: {0, 2}}
    sortie = _sortie(
        pourquoi=[
            "Pente de 5 ans quasi nulle.",
            "Signal qualitatif, sans chiffre.",
            "Concentration de 69,6 %.",
        ]
    )
    assert not redaction_v2.ecarts_au_contrat(
        sortie, [config.SB_POURQUOI], chiffrables
    )
    # Sans la table, la puce qualitative devient à tort un écart.
    assert redaction_v2.ecarts_au_contrat(sortie, [config.SB_POURQUOI])


def test_un_depassement_de_mots_n_arrete_pas_le_module() -> None:
    """Amendement A7 : la longueur a un recours, la structure n'en a pas.

    Un écran refusé parce qu'une puce fait 34 mots au lieu de 30 rendrait F7
    inutilisable — le modèle dépasse de quelques mots à chaque exécution. Le
    dépassement est signalé, il est réparé par compression, il ne bloque pas.
    """
    longue = " ".join(["mot"] * (config.MAX_MOTS_PUCE + 4))
    sortie = _sortie(risque_principal=[longue])
    assert redaction_v2.ecarts_au_contrat(sortie, [config.SB_RISQUE_PRINCIPAL])
    assert not redaction_v2.ecarts_au_contrat(
        sortie, [config.SB_RISQUE_PRINCIPAL], structurels_seuls=True
    )


def test_les_puces_excedentaires_sont_retirees_sans_rien_inventer() -> None:
    sortie = _sortie(entree_marche=[f"Puce {rang}." for rang in range(1, 8)])
    redaction_v2._ramener_au_plafond(sortie, [config.SB_ENTREE_MARCHE])
    assert len(sortie.sous_blocs[config.SB_ENTREE_MARCHE]) == 5


# ---------------------------------------------------------------------------
# 3. Plus aucune coupe muette
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texte",
    [
        "la crédibilité acquise ou",
        "sur la fiche produit, un",
        "un effet de mode par le",
        "mentionnent explicitement en",
        "une ellipse muette …",
        "une virgule finale,",
    ],
)
def test_une_coupe_machine_est_detectee(texte: str) -> None:
    """Les six formes relevées sur le rapport livré du run 8609db9e."""
    assert validation_v2._est_tronque(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "Une phrase française complète et bien finie.",
        "**Leurs prix** — de 8 à 25 EUR sur le canal observé",
        "Concentration du top 3 à 69,6 %",
    ],
)
def test_un_texte_entier_n_est_pas_pris_pour_une_coupe(texte: str) -> None:
    assert not validation_v2._est_tronque(texte)


def test_une_coupe_recule_jusqu_a_un_point_d_arret() -> None:
    """Retirer l'ellipse ne suffisait pas : la coupe restait, sans son signe."""
    repare = preparation_v2.couper_proprement("un niveau de fidélité sonore de…")
    assert repare == "un niveau de fidélité sonore"
    assert not validation_v2._est_tronque(repare)


def test_la_coupe_s_applique_cellule_par_cellule() -> None:
    """Appliquée au tableau entier, elle amputerait toutes les lignes suivantes."""
    tableau = (
        "| Besoin | Ce que dit le corpus |\n"
        "| --- | --- |\n"
        "| Qualité | Plusieurs avis le mentionnent explicitement en… |\n"
        "| Fidélité | Une phrase entière. Une seconde complète. |"
    )
    repare = preparation_v2.couper_bloc(tableau)
    assert "Une seconde complète." in repare, "les lignes suivantes sont intactes"
    assert not [
        texte
        for _, texte in validation_v2._textes_inspectables(repare)
        if validation_v2._est_tronque(texte)
    ]


def test_un_point_d_abreviation_ne_termine_pas_une_phrase() -> None:
    """« définir un critère d'arrêt (ex. » coupait juste avant l'exemple."""
    texte = "Définir un critère d'arrêt (ex. 30 jours sans vente). Et la suite."
    assert preparation_v2.premiere_phrase(texte).endswith("sans vente).")


# ---------------------------------------------------------------------------
# 4. Une source vide se voit
# ---------------------------------------------------------------------------
class _EntreesVides:
    concurrence = None
    insights = None
    recommandations = None


def test_les_six_sources_sont_toujours_citees() -> None:
    """Une source omise se lit comme hors périmètre, jamais comme vide.

    Sur le run 8609db9e, AliExpress avait rapporté zéro offre et ne figurait pas
    dans la ligne : rien ne le disait au lecteur.
    """
    ligne = preparation_v2.construire_ligne_sources(_EntreesVides())
    for source in config.SOURCES_LIGNE_SOURCES:
        assert preparation_v2.LIBELLES_SOURCES[source] in ligne


def test_une_source_vide_est_nommee_avec_sa_raison() -> None:
    ligne = preparation_v2.construire_ligne_sources(
        _EntreesVides(),
        {"aliexpress": {"donnees_disponibles": False, "raison": "anti_bot"}},
    )
    assert "AliExpress (0 offre — collecte bloquée)" in ligne


# ---------------------------------------------------------------------------
# 5. Côté orchestrateur : « a tourné » et « a rapporté » sont deux questions
# ---------------------------------------------------------------------------
def test_un_collecteur_sans_recolte_n_est_pas_un_succes() -> None:
    """AliExpress a rendu zéro offre en code 0, et a été classé ``succeeded``.

    L'étude est passée ``completed``, aucun encart « Étude partielle » n'a été
    affiché, et le rapport a comparé des prix sur un seul de ses deux canaux sans
    jamais le dire.
    """
    from src.studies.constants import COLLECTORS, StudySourceStatus
    from src.studies.runner import ModuleRun, _collector_status

    aliexpress = next(spec for spec in COLLECTORS if spec.source == "aliexpress")
    vide = ModuleRun(exit_code=0, duration_seconds=1.0, payload={"produits": []})
    plein = ModuleRun(
        exit_code=0, duration_seconds=1.0, payload={"produits": [{"sku": "a"}]}
    )

    assert _collector_status(aliexpress, vide) == StudySourceStatus.EMPTY
    assert _collector_status(aliexpress, plein) == StudySourceStatus.SUCCEEDED


def test_une_region_non_couverte_reste_un_resultat_normal() -> None:
    """`skipped_region` passe avant le décompte : ce n'est ni un échec ni un vide."""
    from src.studies.constants import COLLECTORS, EXIT_REGION_NOT_COVERED, StudySourceStatus
    from src.studies.runner import ModuleRun, _collector_status

    amazon = next(spec for spec in COLLECTORS if spec.source == "amazon")
    hors_zone = ModuleRun(exit_code=EXIT_REGION_NOT_COVERED, duration_seconds=1.0)
    assert _collector_status(amazon, hors_zone) == StudySourceStatus.SKIPPED_REGION


def test_le_code_de_sortie_de_f7_ne_recouvre_aucun_autre() -> None:
    """Amendement A5 : 4, parce que 2 est pris par tout le pipeline.

    Réutiliser 2 rendrait indiscernables un gabarit de prompt cassé et un JSON F5
    manquant — deux incidents dont ni la cause ni la réparation ne se ressemblent.
    """
    from src.studies.constants import (
        EXIT_REDACTION_IMPOSSIBLE,
        EXIT_REGION_NOT_COVERED,
        EXIT_SUCCESS,
        EXIT_UNUSABLE_INPUT,
    )

    codes = [
        EXIT_SUCCESS,
        EXIT_UNUSABLE_INPUT,
        EXIT_REGION_NOT_COVERED,
        EXIT_REDACTION_IMPOSSIBLE,
    ]
    assert len(set(codes)) == len(codes)
    assert EXIT_REDACTION_IMPOSSIBLE == config.CODE_REDACTION_IMPOSSIBLE
