"""Point d'entrée en ligne de commande de l'agent PLC.

`stdout` reste du JSON pur. Codes de sortie : 0 succès — **y compris pour un
non-déclenchement, qui est un résultat d'analyse et non une erreur** —, 1 erreur
imprévue, 2 entrée inexploitable ou incohérence produit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from agent import classifier_plc
from config import (
    CODE_ENTREE_INEXPLOITABLE,
    CODE_ERREUR_IMPREVUE,
    CODE_SUCCES,
    configurer_logs,
    resumer_consommation,
)
from schemas import ErreurCoherenceProduit


def construire_analyseur() -> argparse.ArgumentParser:
    """Construit l'analyseur d'arguments de la commande.

    Returns:
        L'analyseur configuré.
    """
    analyseur = argparse.ArgumentParser(
        prog="agent_plc",
        description=(
            "Classification de phase de cycle de vie (PLC). Consomme la sortie de "
            "F5 — requise — et, en option, celles de F3 et F4 ; ne collecte rien et "
            "ne relit aucune donnée brute de collecteur."
        ),
        epilog=(
            "La classification n'a lieu que si le verdict amont est positif "
            "(verdict_potentiel.declenche_plc). Sinon, une sortie courte de "
            "non-déclenchement est produite en code 0. --forcer permet l'exécution "
            "à des fins d'étude ; il est tracé dans la sortie et interdit en "
            "production."
        ),
    )
    analyseur.add_argument(
        "--recommandations",
        required=True,
        help="Sortie de agent_recommandations_strategiques (F5) — requise.",
    )
    analyseur.add_argument(
        "--insights", default=None, help="Sortie de agent_insights_consommateurs (F3)."
    )
    analyseur.add_argument(
        "--concurrence",
        default=None,
        help="Sortie de agent_analyse_concurrentielle (F4).",
    )
    analyseur.add_argument(
        "--forcer",
        action="store_true",
        help=(
            "Classer malgré un verdict amont non positif. Usage d'étude et de test "
            "uniquement : tracé dans la sortie, interdit à l'orchestrateur."
        ),
    )
    analyseur.add_argument(
        "--langue-analyse", default="fr", help="Langue de rédaction (défaut : fr)."
    )
    analyseur.add_argument(
        "--sortie",
        default="output.json",
        help="Fichier de sortie ; chaîne vide pour n'écrire aucun fichier.",
    )
    analyseur.add_argument(
        "--stdout", action="store_true", help="Émet aussi le JSON sur stdout."
    )
    analyseur.add_argument(
        "--verbose", action="store_true", help="Journalisation détaillée."
    )
    return analyseur


def main(argv: list[str] | None = None) -> int:
    """Exécute l'agent depuis la ligne de commande.

    Args:
        argv: Arguments, ou `None` pour utiliser `sys.argv`.

    Returns:
        Le code de sortie du processus.
    """
    arguments = construire_analyseur().parse_args(argv)
    logger = configurer_logs(arguments.verbose)

    try:
        with get_usage_metadata_callback() as consommation:
            resultat = classifier_plc(
                chemin_recommandations=arguments.recommandations,
                chemin_insights=arguments.insights,
                chemin_concurrence=arguments.concurrence,
                forcer=arguments.forcer,
                langue_analyse=arguments.langue_analyse,
            )
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except ErreurCoherenceProduit as erreur:
        print(f"Incohérence bloquante : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except ValueError as erreur:
        print(f"Entrée inexploitable : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except Exception as erreur:  # noqa: BLE001 — le CLI n'affiche jamais de trace nue
        logger.exception("erreur imprévue")
        print(f"Erreur imprévue : {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return CODE_ERREUR_IMPREVUE

    document = resultat.model_dump_json(indent=2, by_alias=True)

    if arguments.sortie:
        chemin = Path(arguments.sortie)
        chemin.write_text(document, encoding="utf-8")
        logger.info("résultat écrit dans %s", chemin)

    if arguments.stdout or not arguments.sortie:
        print(document)

    # Un non-déclenchement est un résultat d'analyse, jamais une erreur : le code
    # de sortie reste 0 pour que l'appelant lise le JSON, pas le code.
    classification = resultat.classification
    print(
        f"Déclenchement : {resultat.declenchement.mode} | phase : "
        f"{classification.phase_probable if classification else 'aucune'} | "
        f"incertitude : {classification.incertitude if classification else 'n/a'} | "
        f"confiance : {resultat.confiance_globale.niveau}",
        file=sys.stderr,
    )
    return CODE_SUCCES


if __name__ == "__main__":
    raise SystemExit(main())
