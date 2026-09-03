"""Point d'entrée CLI de l'agent de collecte de tendances.

Exemple ::

    python main.py \
        --nom "JBL Endurance Peak 4 Open Ear" \
        --description "Écouteurs à conduction d'air, crochets d'oreille..." \
        --categorie "electronics" \
        --geo FR --langue fr
"""

from __future__ import annotations

import argparse
import sys

from langchain_core.callbacks import get_usage_metadata_callback

from agent import analyser_tendances
from config import configurer_logging, resumer_consommation
from schemas import FicheProduit, ParametresMarche


def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    parseur = argparse.ArgumentParser(
        description=(
            "Collecte et analyse les signaux de tendance Google Trends d'un "
            "produit e-commerce sur un marché donné."
        )
    )
    parseur.add_argument("--nom", required=True, help="Titre du produit.")
    parseur.add_argument("--description", required=True, help="Description du produit.")
    parseur.add_argument("--categorie", required=True, help="Catégorie du produit.")
    parseur.add_argument("--geo", required=True, help="Code pays ISO-2, ex. FR.")
    parseur.add_argument("--langue", required=True, help="Code langue ISO-2, ex. fr.")
    parseur.add_argument(
        "--verbose",
        action="store_true",
        help="Active les logs de progression sur stderr (stdout reste du JSON pur).",
    )
    return parseur.parse_args()


def main() -> int:
    """Exécute l'analyse et sérialise le résultat en JSON sur `stdout`.

    Returns:
        Le code de sortie du processus : `0` en cas de succès, `1` en cas
        d'erreur bloquante.
    """
    arguments = _analyser_arguments()
    logger = configurer_logging(verbose=arguments.verbose)

    produit = FicheProduit(
        nom=arguments.nom,
        description=arguments.description,
        categorie=arguments.categorie,
    )
    marche = ParametresMarche(geo=arguments.geo, langue=arguments.langue)

    try:
        with get_usage_metadata_callback() as consommation:
            resultat = analyser_tendances(produit=produit, marche=marche)
        recapitulatif = resumer_consommation(consommation.usage_metadata)
        if recapitulatif:
            print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    except Exception as exception:  # noqa: BLE001 — erreur de configuration ou LLM
        logger.error("Analyse interrompue : %s", exception)
        return 1

    print(resultat.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
