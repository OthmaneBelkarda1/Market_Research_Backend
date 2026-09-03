"""Point d'entrée en ligne de commande de l'agent AliExpress API.

Le résultat est sérialisé en JSON indenté sur la sortie standard ; toute trace
de progression part sur `stderr`, afin que `stdout` reste parsable.

Les cinq arguments de la région d'étude et de la fiche produit sont
obligatoires. Il n'existe AUCUNE valeur par défaut pour `--geo`, `--devise` ou
`--langue` : une région absente ou mal formée arrête l'exécution avant tout
appel API. C'est la seule erreur bloquante du module, et elle est délibérée —
un prix collecté sans région connue n'a aucune valeur pour une étude.

Exemple :
    python main.py \\
        --nom "Ceinture lombaire double traction" \\
        --description "Ceinture de soutien lombaire à double sangle de traction." \\
        --categorie "sante-bien-etre" \\
        --geo MA --langue fr --devise MAD
"""

from __future__ import annotations

import argparse
import sys

from langchain_core.callbacks import get_usage_metadata_callback
from pydantic import ValidationError

from agent import collecter_aliexpress_api
from config import configurer_logging, resumer_consommation, verifier_identifiants
from schemas import FicheProduit, ParametresMarche

_INDENTATION_JSON = 2

_CODE_SORTIE_CONFIGURATION = 2
"""Code de sortie réservé aux erreurs de configuration et de région : elles se
corrigent sur la ligne de commande ou dans `.env`, pas dans le code."""


def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    analyseur = argparse.ArgumentParser(
        description=(
            "Collecte les produits et prix par SKU d'un produit e-commerce sur "
            "AliExpress, via l'API officielle Dropshipping, pour UNE région "
            "d'étude donnée. Une étude multi-régions se fait par exécutions "
            "successives, jamais en une seule."
        )
    )
    analyseur.add_argument("--nom", required=True, help="Titre commercial du produit.")
    analyseur.add_argument(
        "--description", required=True, help="Description libre du produit."
    )
    analyseur.add_argument(
        "--categorie", default=None, help="Catégorie e-commerce (optionnel)."
    )
    analyseur.add_argument(
        "--geo",
        required=True,
        help="Pays de livraison de la région d'étude, ISO-2, ex. MA. Sans défaut.",
    )
    analyseur.add_argument(
        "--langue",
        required=True,
        help="Langue du marché, ISO-2, ex. fr. Sans défaut.",
    )
    analyseur.add_argument(
        "--devise",
        required=True,
        help="Devise d'affichage des prix, ISO-4217, ex. MAD. Sans défaut.",
    )
    analyseur.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche la progression de la collecte sur stderr.",
    )
    return analyseur.parse_args()


def _construire_marche(arguments: argparse.Namespace) -> ParametresMarche:
    """Valide et assemble la région d'étude.

    Args:
        arguments: Arguments de la ligne de commande.

    Returns:
        La région d'étude validée.

    Raises:
        SystemExit: Si le triplet est mal formé — le message précise le format
            attendu de chaque code.
    """
    try:
        return ParametresMarche(
            geo=arguments.geo.strip().upper(),
            langue=arguments.langue.strip().lower(),
            devise=arguments.devise.strip().upper(),
        )
    except ValidationError as exception:
        champs = ", ".join(str(erreur["loc"][0]) for erreur in exception.errors())
        print(
            "Région d'étude invalide "
            f"(--geo={arguments.geo!r}, --langue={arguments.langue!r}, "
            f"--devise={arguments.devise!r}).\n"
            f"Champ(s) en cause : {champs}.\n"
            "Formats attendus : --geo pays ISO-2 (ex. MA, FR, US), --langue "
            "ISO-2 minuscule (ex. fr, en), --devise ISO-4217 (ex. MAD, EUR, USD).\n"
            "Aucune valeur par défaut n'existe : la région conditionne les prix "
            "collectés et doit être explicite.",
            file=sys.stderr,
        )
        raise SystemExit(_CODE_SORTIE_CONFIGURATION) from exception


def main() -> None:
    """Exécute la collecte et écrit le résultat JSON sur la sortie standard."""
    arguments = _analyser_arguments()
    configurer_logging(verbose=arguments.verbose)

    marche = _construire_marche(arguments)

    try:
        verifier_identifiants()
    except RuntimeError as exception:
        print(str(exception), file=sys.stderr)
        raise SystemExit(_CODE_SORTIE_CONFIGURATION) from exception

    produit = FicheProduit(
        nom=arguments.nom,
        description=arguments.description,
        categorie=arguments.categorie,
    )

    with get_usage_metadata_callback() as consommation:
        resultat = collecter_aliexpress_api(produit, marche)
    recapitulatif = resumer_consommation(consommation.usage_metadata)
    if recapitulatif:
        print(f"Consommation LLM — {recapitulatif}", file=sys.stderr)
    print(resultat.model_dump_json(indent=_INDENTATION_JSON))


if __name__ == "__main__":
    main()
