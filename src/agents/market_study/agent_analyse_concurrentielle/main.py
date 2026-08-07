"""Point d'entrée en ligne de commande de l'agent Analyse Concurrentielle.

`stdout` reste du JSON pur. Codes de sortie : 0 succès, 1 erreur imprévue,
2 entrée inexploitable, incohérence de produit ou usage argparse invalide.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from agent import analyser_concurrence
from config import (
    CODE_ENTREE_INEXPLOITABLE,
    CODE_ERREUR_IMPREVUE,
    CODE_SUCCES,
    REGEX_DEVISE,
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
        prog="agent_analyse_concurrentielle",
        description=(
            "Analyse de l'axe 2 — Analyse Concurrentielle. Consomme les sorties "
            "JSON des collecteurs AliExpress, Amazon, Meta Ads et Recherche web ; "
            "ne collecte rien lui-même et ne convertit jamais de devise."
        ),
        epilog=(
            "Note : le collecteur AliExpress émet sur stdout — le fichier passé à "
            "--aliexpress est une redirection. Amazon, Meta Ads et Recherche web "
            "écrivent nativement output.json."
        ),
    )
    analyseur.add_argument("--aliexpress", default=None, help="Sortie de agent_aliexpress.")
    analyseur.add_argument("--amazon", default=None, help="Sortie de agent_amazon.")
    analyseur.add_argument("--meta-ads", default=None, help="Sortie de agent_meta_ads.")
    analyseur.add_argument(
        "--recherche-web", default=None, help="Sortie de agent_recherche_web."
    )
    analyseur.add_argument(
        "--prix-envisage",
        type=float,
        default=None,
        help="Prix envisagé pour le produit étudié. Exige --devise-envisagee.",
    )
    analyseur.add_argument(
        "--devise-envisagee",
        default=None,
        help="Devise ISO-4217 du prix envisagé, ex. MAD. Exige --prix-envisage.",
    )
    analyseur.add_argument(
        "--langue-analyse", default="fr", help="Langue de rédaction (défaut : fr)."
    )
    analyseur.add_argument(
        "--sortie",
        default="output.json",
        help="Fichier de sortie ; chaîne vide pour n'écrire aucun fichier.",
    )
    analyseur.add_argument("--stdout", action="store_true", help="Émet aussi le JSON sur stdout.")
    analyseur.add_argument("--verbose", action="store_true", help="Journalisation détaillée.")
    return analyseur


def main(argv: list[str] | None = None) -> int:
    """Exécute l'agent depuis la ligne de commande.

    Args:
        argv: Arguments, ou `None` pour utiliser `sys.argv`.

    Returns:
        Le code de sortie du processus.
    """
    analyseur = construire_analyseur()
    arguments = analyseur.parse_args(argv)
    logger = configurer_logs(arguments.verbose)

    if (arguments.prix_envisage is None) != (arguments.devise_envisagee is None):
        analyseur.error(
            "--prix-envisage et --devise-envisagee vont ensemble : un prix sans "
            "devise n'a aucun sens, et cet agent ne convertit jamais une devise."
        )
    if arguments.devise_envisagee and not re.match(
        REGEX_DEVISE, arguments.devise_envisagee
    ):
        analyseur.error(
            f"--devise-envisagee doit être un code ISO-4217 en trois lettres "
            f"majuscules (reçu : « {arguments.devise_envisagee} »)."
        )

    if not any(
        (arguments.aliexpress, arguments.amazon, arguments.meta_ads, arguments.recherche_web)
    ):
        print(
            "Aucun fichier d'entrée fourni. Renseigne au moins l'une des options "
            "--aliexpress, --amazon, --meta-ads ou --recherche-web.",
            file=sys.stderr,
        )
        return CODE_ENTREE_INEXPLOITABLE

    try:
        with get_usage_metadata_callback() as consommation:
            resultat = analyser_concurrence(
                chemin_aliexpress=arguments.aliexpress,
                chemin_amazon=arguments.amazon,
                chemin_meta_ads=arguments.meta_ads,
                chemin_web=arguments.recherche_web,
                prix_envisage=arguments.prix_envisage,
                devise_envisagee=arguments.devise_envisagee,
                langue_analyse=arguments.langue_analyse,
            )
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except ErreurCoherenceProduit as erreur:
        print(f"Incohérence bloquante : {erreur}", file=sys.stderr)
        return CODE_ENTREE_INEXPLOITABLE
    except Exception as erreur:  # noqa: BLE001 — le CLI n'affiche jamais de trace nue
        logger.exception("erreur imprévue")
        print(f"Erreur imprévue : {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return CODE_ERREUR_IMPREVUE

    if all(not source.donnees_disponibles for source in resultat.sources_utilisees):
        print(
            "Aucun fichier d'entrée exploitable : "
            + " ; ".join(
                f"[{s.source}] {', '.join(s.avertissements) or 'non fourni'}"
                for s in resultat.sources_utilisees
            ),
            file=sys.stderr,
        )
        return CODE_ENTREE_INEXPLOITABLE

    document = resultat.model_dump_json(indent=2)

    if arguments.sortie:
        chemin = Path(arguments.sortie)
        chemin.write_text(document, encoding="utf-8")
        logger.info("résultat écrit dans %s", chemin)

    if arguments.stdout or not arguments.sortie:
        print(document)

    return CODE_SUCCES


if __name__ == "__main__":
    raise SystemExit(main())
