"""Point d'entrée en ligne de commande de l'agent de recherche Amazon régionalisée.

Le résultat est écrit en JSON indenté dans un fichier — `output.json` par
défaut, écrasé à chaque exécution — et, sur demande (`--stdout`), sérialisé sur
la sortie standard pour être redirigé ou chaîné. Toute trace de progression part
sur `stderr`, afin que `stdout` reste parsable.

L'écriture du fichier est un confort de la CLI : `agent.rechercher_amazon`
retourne un objet en mémoire et n'écrit rien.

Si le pays étudié n'a pas son propre site Amazon (Maroc, Suisse, Nigeria…), rien
n'est collecté : le JSON porte `region_couverte: false`, le motif est rappelé sur
`stderr` et la commande sort avec le code 3.

Exemples :
    python main.py \\
        --nom "JBL Endurance Peak 4 Open Ear" \\
        --description "Écouteurs à conduction ouverte pour le sport." \\
        --categorie "electronics" \\
        --geo FR \\
        --langue fr

    # Le pays accepte aussi du texte libre.
    python main.py --nom "..." --description "..." --geo "Lyon" --langue fr

    # Sort en code 3 sans rien collecter : le Maroc n'a pas de site Amazon.
    python main.py --nom "..." --description "..." --geo MA --langue fr

    # Décision d'opérateur : interroger amazon.fr en le sachant.
    python main.py --nom "..." --description "..." --geo MA --langue fr \\
        --domaine amazon.fr --avis 0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent import rechercher_amazon
from config import NB_PRODUITS_AVIS, configurer_logging, obtenir_logger
from schemas import FicheProduit, ParametresMarche, ResultatRechercheAmazon

_LOG = obtenir_logger(__name__)

_INDENTATION_JSON = 2

FICHIER_SORTIE_DEFAUT = "output.json"
"""Fichier écrit à chaque exécution, dans le répertoire courant."""

ENCODAGE_SORTIE = "utf-8"
"""Encodage imposé à l'écriture : sur Windows, le défaut est cp1252 et
corromprait tous les accents des titres et des avis collectés."""

CODE_SORTIE_REGION_NON_COUVERTE = 3
"""Code de sortie lorsque le pays étudié n'a pas de site Amazon propre.

Distinct de 0 (succès), de 1 (erreur d'exécution) et de 2 (erreur d'usage
argparse), pour qu'un orchestrateur amont puisse enchaîner sur un autre
collecteur sans analyser le JSON."""


def _analyser_arguments() -> argparse.Namespace:
    """Déclare et lit les arguments de la ligne de commande.

    Returns:
        Les arguments analysés.
    """
    analyseur = argparse.ArgumentParser(
        description=(
            "Collecte et qualifie un corpus de produits Amazon pour un produit "
            "e-commerce, sur la marketplace desservant la région d'étude."
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
        help=(
            "Pays étudié : code ISO-2 (« FR ») ou lieu en texte libre "
            "(« France », « Lyon »). Sélectionne le site Amazon DE CE PAYS, "
            "jamais une adresse de livraison. Un pays sans site Amazon propre "
            "arrête l'exécution (code de sortie 3)."
        ),
    )
    analyseur.add_argument(
        "--langue", required=True, help="Code langue ISO-2 du marché, ex. fr."
    )
    analyseur.add_argument(
        "--domaine",
        default=None,
        help=(
            "Marketplace imposée, ex. amazon.de. Court-circuite la résolution de "
            "la région ET le contrôle de couverture du pays : à n'utiliser qu'en "
            "connaissance de cause."
        ),
    )
    analyseur.add_argument(
        "--avis",
        type=int,
        default=NB_PRODUITS_AVIS,
        help=(
            f"Produits de tête enrichis d'avis, 0 pour n'en collecter aucun "
            f"(défaut : {NB_PRODUITS_AVIS}). Un run d'actor par produit : c'est "
            "le principal levier de coût."
        ),
    )
    analyseur.add_argument(
        "--sortie",
        default=FICHIER_SORTIE_DEFAUT,
        help=(
            f"Fichier JSON de sortie (défaut : {FICHIER_SORTIE_DEFAUT}, écrasé à "
            "chaque exécution). Passer une chaîne vide pour n'écrire aucun fichier."
        ),
    )
    analyseur.add_argument(
        "--stdout",
        action="store_true",
        help="Affiche aussi le JSON sur la sortie standard, pour le rediriger.",
    )
    analyseur.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche la progression de la collecte sur stderr.",
    )
    return analyseur.parse_args()


def _ecrire_resultat(resultat: ResultatRechercheAmazon, chemin: str) -> None:
    """Écrit le résultat en JSON indenté dans un fichier.

    L'échec d'écriture n'interrompt pas la CLI : la collecte a déjà eu lieu et
    son coût est engagé. L'erreur est signalée sur `stderr`, et `--stdout` reste
    le moyen de récupérer le corpus.

    Args:
        resultat: Résultat de la collecte.
        chemin: Chemin du fichier à écrire, relatif au répertoire courant.
    """
    fichier = Path(chemin)
    try:
        fichier.write_text(
            resultat.model_dump_json(indent=_INDENTATION_JSON), encoding=ENCODAGE_SORTIE
        )
    except OSError as exception:
        _LOG.error("Écriture de « %s » impossible : %s", fichier, exception)
        return

    # Confirmation sur stderr, et non via le logger : elle doit rester visible
    # sans `--verbose`, sans pour autant être émise au niveau WARNING.
    site = resultat.marketplace.domaine if resultat.marketplace else "aucune marketplace"
    print(
        f"Résultat écrit dans {fichier.resolve()} "
        f"({len(resultat.produits)} produit(s) sur {site}, "
        f"{resultat.stats.nb_avis_collectes} avis, "
        f"{round(fichier.stat().st_size / 1024)} Ko).",
        file=sys.stderr,
    )


def main() -> None:
    """Exécute la collecte, écrit le fichier de sortie et, sur demande, stdout."""
    arguments = _analyser_arguments()
    configurer_logging(verbose=arguments.verbose)

    produit = FicheProduit(
        nom=arguments.nom,
        description=arguments.description,
        categorie=arguments.categorie,
    )
    # `geo` n'est pas forcé en majuscules : il accepte aussi un lieu en texte
    # libre, que `strategy.resoudre_marketplace` résout en code pays.
    marche = ParametresMarche(
        geo=arguments.geo.strip(),
        langue=arguments.langue.strip().lower(),
    )

    resultat = rechercher_amazon(
        produit,
        marche,
        domaine_force=arguments.domaine,
        nb_produits_avis=max(0, arguments.avis),
    )

    if arguments.sortie:
        _ecrire_resultat(resultat, arguments.sortie)
    if arguments.stdout:
        print(resultat.model_dump_json(indent=_INDENTATION_JSON))

    if not resultat.region_couverte:
        # Le motif exact est déjà dans le JSON ; il est répété sur stderr pour
        # que l'opérateur comprenne sans ouvrir le fichier.
        print(
            f"\nAgent inapplicable à « {marche.geo} ».\n"
            f"{resultat.statuts_collecte[0].message_erreur}\n"
            "Aucun run Apify n'a été lancé.",
            file=sys.stderr,
        )
        sys.exit(CODE_SORTIE_REGION_NON_COUVERTE)


if __name__ == "__main__":
    main()
