"""Faux pipeline : treize scripts qui respectent le contrat CLI du vrai, sans rien appeler.

Aucun test automatise n'appelle les vrais collecteurs ni l'API Anthropic. Ces scripts
tiennent le meme contrat que les modules de `src/agents/market_study` -- JSON sur stdout,
`--sortie` pour les uns, codes de sortie 0/1/2/3 -- et rien d'autre.

L'orchestrateur les atteint par l'indirection `STUDY_PIPELINE_ROOT` : la disposition des
fichiers est celle du vrai pipeline, donc aucun chemin n'est code en dur nulle part.

Pilotage par variables d'environnement, `<MODULE>` etant le nom du script en majuscules :

    FAKE_<MODULE>_EXIT     code de sortie simule (defaut 0)
    FAKE_<MODULE>_SLEEP    duree simulee en secondes (defaut 0) -- pour les timeouts
    FAKE_<MODULE>_STDOUT   "invalide" : sortir en code 0 avec un stdout non JSON
    FAKE_PLC_NON_DECLENCHE "1" : sortie courte de non-declenchement de F6, en code 0
    FAKE_PIPELINE_TRACE    fichier ou tracer debuts et fins -- pour le semaphore

Chaque module ecrit en plus, systematiquement, `_trace_<module>.json` dans son repertoire
de travail (donc celui de l'etude) : debut et fin en `time.monotonic()`. C'est ce qui
permet de calculer un chevauchement reel sans compteur partage entre processus.

**Un fichier par module, et non un fichier commun** : a six collecteurs simultanes, six
`open(..., "a")` concurrents sur le meme fichier n'ont aucune atomicite garantie, et une
ligne tronquee ferait echouer le test au lieu du code teste. `FAKE_PIPELINE_TRACE` reste
utilisable pour le test du semaphore inter-etudes, ou les ecritures sont par construction
serialisees.
"""

import json
import os
import sys
import time
from pathlib import Path

# Les trois collecteurs qui n'emettent que sur stdout, comme dans le vrai pipeline.
STDOUT_ONLY = {"agent_tendances", "agent_reddit", "agent_aliexpress"}

PAYLOADS: dict[str, dict] = {
    "agent_tendances": {"module": "agent_tendances", "tendances": [{"mot_cle": "ceinture"}]},
    "agent_reddit": {"module": "agent_reddit", "contributions": [{"texte": "avis simule"}]},
    "agent_recherche_web": {"module": "agent_recherche_web", "pages": [{"url": "https://ex.test"}]},
    "agent_amazon": {"module": "agent_amazon", "produits": [{"asin": "B0TEST", "prix": 199}]},
    "agent_meta_ads": {"module": "agent_meta_ads", "annonces": [{"id": "ad-1"}]},
    "agent_aliexpress": {"module": "agent_aliexpress", "offres": [{"sku": "sku-1", "prix": 149}]},
    "agent_insights_consommateurs": {"module": "f3", "insights": [{"besoin": "maintien"}]},
    "agent_analyse_concurrentielle": {"module": "f4", "concurrents": [{"nom": "concurrent"}]},
    "agent_recommandations_strategiques": {
        "module": "f5",
        "verdict_potentiel": {
            "verdict": "positif",
            "score_total": 5,
            "declenche_plc": True,
            "confiance": "moyenne",
            "statut_regle": "hypothese_de_travail_a_valider",
        },
    },
    "agent_plc": {"module": "f6", "declenchement": {"mode": "automatique"}},
    "agent_restitution": {"module": "f7", "sections": 9},
}

PLC_NON_DECLENCHE = {
    "module": "f6",
    "declenchement": {"mode": "non_declenche", "raison": "verdict amont non positif"},
}

RAPPORT = "# Rapport d'etude simule\n\nNeuf sections, en faux.\n"
RESUME = "# Resume executif simule\n"


def _valeur(args: list[str], option: str) -> str | None:
    """La valeur d'une option de la ligne de commande, si elle y figure."""
    return (
        args[args.index(option) + 1]
        if option in args and args.index(option) + 1 < len(args)
        else None
    )


def _tracer(nom: str, evenement: str) -> None:
    """Trace debuts et fins d'execution, de deux facons complementaires.

    Le fichier commun `FAKE_PIPELINE_TRACE` sert au test du semaphore inter-etudes ; le
    fichier par module, lui, est toujours ecrit et sert aux tests de chevauchement.
    """
    horodatage = time.monotonic()

    # Un seul ecrivain par fichier : aucune concurrence, meme a six collecteurs.
    propre = Path.cwd() / f"_trace_{nom}.json"
    trace = json.loads(propre.read_text(encoding="utf-8")) if propre.exists() else {}
    trace[evenement] = horodatage
    propre.write_text(json.dumps(trace), encoding="utf-8")

    fichier = os.environ.get("FAKE_PIPELINE_TRACE")
    if not fichier:
        return
    ligne = json.dumps(
        {"module": nom, "cwd": Path.cwd().name, "evenement": evenement, "t": horodatage}
    )
    with open(fichier, "a", encoding="utf-8") as flux:
        flux.write(ligne + "\n")


def run(nom: str) -> int:
    """Execute le faux module `nom` et rend son code de sortie."""
    args = sys.argv[1:]
    prefixe = f"FAKE_{nom.upper()}"
    _tracer(nom, "debut")

    if duree := float(os.environ.get(f"{prefixe}_SLEEP", "0")):
        time.sleep(duree)

    code = int(os.environ.get(f"{prefixe}_EXIT", "0"))
    if code:
        print(f"{nom} : echec simule, code {code}.", file=sys.stderr)
        _tracer(nom, "fin")
        return code

    if os.environ.get(f"{prefixe}_STDOUT") == "invalide":
        print("ceci n'est pas du JSON")
        _tracer(nom, "fin")
        return 0

    payload = PAYLOADS[nom]
    if nom == "agent_plc" and os.environ.get("FAKE_PLC_NON_DECLENCHE") == "1":
        payload = PLC_NON_DECLENCHE

    if (sortie := _valeur(args, "--sortie")) is not None:
        Path(sortie).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if (rapport := _valeur(args, "--rapport")) is not None:
        Path(rapport).write_text(RAPPORT, encoding="utf-8")
    if (resume := _valeur(args, "--resume")) is not None:
        Path(resume).write_text(RESUME, encoding="utf-8")

    # Fidele au vrai : les modules a fichier ne parlent qu'avec --stdout.
    if nom in STDOUT_ONLY or "--stdout" in args:
        print(json.dumps(payload, ensure_ascii=False))

    _tracer(nom, "fin")
    return 0


def run_resolveur(nom: str, payload: dict) -> int:
    """Faux `langues_marche.py` / `devise_marche.py` : une table, jamais un appel."""
    code = int(os.environ.get(f"FAKE_{nom.upper()}_EXIT", "0"))
    if code:
        print(f"Pays absent de la table ({nom}).", file=sys.stderr)
        return code
    print(json.dumps(payload, ensure_ascii=False))
    return 0
