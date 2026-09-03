# Contrats des modules du pipeline d'étude de marché

> **Référence de câblage de l'orchestrateur** ([`src/studies/runner.py`](../src/studies/runner.py)).
> Établi le 2026-08-06 depuis le code réel, les `--help` exécutés des 11 modules,
> `etude_marche.ps1` et `docs/baseline_latence.md` du pipeline — pas depuis une
> documentation d'intention.
>
> Le pipeline vit dans [`src/agents/market_study/`](../src/agents/market_study/), copié
> **à l'identique** (149 fichiers, hachages vérifiés). Aucune de ses lignes n'est modifiée :
> tout écart de comportement se règle par argument de ligne de commande ou par variable
> d'environnement. Si ce document et le code du pipeline divergent, **c'est le code du
> pipeline qui fait foi** — et ce document qui est à corriger.

## 1. Socle d'entrée des six collecteurs

```
--nom <str> --description <str> --categorie <str> --geo <ISO-2> --langue <ISO-2> [--verbose]
```

`--categorie` est optionnel pour tous sauf Tendances. `--geo` et `--langue` n'ont **jamais**
de valeur par défaut : une région absente arrête l'exécution avant tout appel facturé.

## 2. Les onze modules

| # | Module | Entrées propres | Sorties | Codes de sortie |
|---|---|---|---|---|
| 1 | `agent_tendances/main.py` | socle (`--categorie` requis) | **stdout uniquement** | `0` ; `1` toute erreur |
| 2 | `agent_reddit/main.py` | socle | **stdout uniquement** | `0` ; `1` |
| 3 | `agent_recherche_web/main.py` | socle | `--sortie <fichier>` (défaut `output.json` **dans le cwd**) ; stdout **seulement avec `--stdout`** | `0` ; `1` |
| 4 | `agent_amazon/main.py` | socle + `--avis N` (défaut 5), `--domaine` | idem | `0` ; `1` ; **`3` pays sans site Amazon** |
| 5 | `agent_meta_ads/main.py` | socle + `--annonces N` (défaut 30), `--annonceur URL` | idem | `0` ; `1` ; **`3` région non résolue** |
| 6 | `agent_aliexpress/main.py` | socle + **`--devise <ISO-4217>` obligatoire** | **stdout uniquement** | `0` ; **`2` configuration ou région invalide** |
| 7 | `agent_insights_consommateurs/main.py` (F3) | `--reddit` `--amazon` `--recherche-web` (tous optionnels) `--langue-analyse` | `--sortie` + `--stdout` | `0` ; `1` ; `2` |
| 8 | `agent_analyse_concurrentielle/main.py` (F4) | `--aliexpress` `--amazon` `--meta-ads` `--recherche-web` ; `--prix-envisage` + `--devise-envisagee` (ensemble) | idem | `0` ; `1` ; `2` |
| 9 | `agent_recommandations_strategiques/main.py` (F5) | `--insights` `--concurrence` `--tendances` | idem | `0` ; `1` ; `2` |
| 10 | `agent_plc/main.py` (F6) | **`--recommandations` requis** ; `--insights` `--concurrence` | idem | `0` ; `1` ; `2` |
| 11 | `agent_restitution/main.py` (F7) | **`--recommandations` requis** ; `--insights` `--concurrence` `--plc` | **`--rapport <md>`**, **`--resume <md>`**, `--sortie <json>` de métadonnées | `0` ; `1` ; `2` |

Sémantique commune : `0` = succès **y compris résultat défavorable** (verdict négatif, PLC non
déclenchée) ; `1` = erreur d'exécution ; `2` = entrée inexploitable ; `3` = région non couverte,
**situation normale** et non un échec.

`--forcer` (F6) est **interdit à l'orchestrateur** : le module lui-même le documente comme
réservé à l'étude et au test.

## 3. Trois contrats de sortie, pas un seul

C'est le point où la documentation d'intention (« stdout = JSON pur ») ne suffit pas :

| Groupe | Modules | Ce que fait l'orchestrateur |
|---|---|---|
| stdout seul | Tendances, Reddit, AliExpress | capture `stdout`, parse, écrit lui-même le fichier du workdir |
| fichier, stdout sur demande | Recherche web, Amazon, Meta Ads | passe `--sortie <workdir>/x.json --stdout` et parse `stdout` |
| fichier + stdout sur demande | F3, F4, F5, F6, F7 | idem ; F7 reçoit en plus `--rapport` et `--resume` |

**Le `cwd` du sous-processus est le workdir de l'étude.** Sans cela, un module lancé sans
`--sortie` écrirait son `output.json` dans le dossier courant du serveur.

## 4. Langue et devise — deux tables déterministes

| Utilitaire | Appel | Sortie | Échec |
|---|---|---|---|
| `langues_marche.py` | `--geo <ISO-2>` | `{geo, codes[], langues[], justification, reserve, source, date_validite, limites[]}` | code `1` + message sur stderr |
| `devise_marche.py` | `--geo <ISO-2>` | `{geo, devise, nom, source, date_validite, limites[]}` | code `1` + message sur stderr |

Les deux couvrent le même jeu de 244 pays, ne font **aucun appel LLM**, ne lisent **aucun
`.env`** et n'exigent **aucune clé**. Ils sont malgré tout invoqués en sous-processus : la
règle d'architecture « le couplage est un contrat JSON, jamais un import de code » prime, et
le coût est de quelques dizaines de millisecondes.

Deux conséquences pour l'API :

- **La devise n'est pas remappée côté backend.** Un pays absent de la table arrête l'étude
  (`CURRENCY_NOT_MAPPED`) : deviner une devise fausserait tout le benchmark de prix aval sans
  qu'aucun contrôle ne puisse le rattraper.
- **La table des langues est un `dict[str, str]` : toujours une langue et une seule.** Le
  multi-langues du script PowerShell (`-Langues fr,ar`) est un **forçage manuel**, jamais une
  détection. L'orchestrateur ne crée donc aucune étude sœur. Le champ `reserve` (« marché à
  arbitrer » — `MA` rend `ar` en signalant la place du français) est journalisé et persisté
  dans `study.progress.langue`, jamais ignoré.

## 5. Variables d'environnement

| Variable | Modules | Sans elle |
|---|---|---|
| `ANTHROPIC_API_KEY` | les 11 | aucune analyse, aucune dérivation de mots-clés |
| `APIFY_TOKEN` (repli : `APIFY_API_TOKEN`) | Tendances, Reddit, Recherche web, Amazon, Meta Ads | 5 collecteurs en échec |
| `SEL_ANONYMISATION` | Reddit | sel public de repli — anonymisation affaiblie |
| `ALIEXPRESS_APP_KEY`, `ALIEXPRESS_APP_SECRET`, `ALIEXPRESS_ACCESS_TOKEN`, `ALIEXPRESS_REFRESH_TOKEN` | AliExpress | collecteur en échec (code 2) |

**Aucun module n'appelle `load_dotenv(override=True)`** — contrairement à
`src/agents/product_extraction`. L'environnement transmis par le backend prime donc toujours
sur le `.env` du disque. Les modules cherchent le `.env` en remontant depuis le `cwd`
(`find_dotenv(usecwd=True)`) ou depuis leur propre fichier : le workdir étant sous la racine
du dépôt, le `.env` du backend est trouvé dans les deux cas.

## 6. Workdir d'une étude

Noms de fichiers repris de `etude_marche.ps1`, qui reste la référence d'enchaînement :

```
var/studies/{study_id}/
├── tendances.json  reddit.json  recherche_web.json      # collecte
├── amazon.json     meta_ads.json  aliexpress.json
├── insights.json   concurrence.json                     # F3, F4
├── recommandations.json  plc.json                       # F5, F6
├── rapport_etude.md  resume_executif.md  restitution.json   # F7
```

Les entrées sont écrites en UTF-8. Les agents d'analyse relisent en
`utf-8-sig, utf-16, utf-8, cp1252` : le BOM n'est pas un piège.

## 7. Durées et coûts mesurés

Run étalon `ashwagandha-supplement-ES` du 2026-08-06 (`docs/baseline_latence.md` du pipeline) :

| Module | Durée | Appels LLM | Coût |
|---|---:|---:|---:|
| F3 insights | 409,6 s | 25 | 0,546 $ |
| F4 concurrence | **666,3 s** | 31 | 0,891 $ |
| F5 verdict | 497,7 s | 6 | 0,745 $ |
| F6 cycle de vie | 1,9 s (**~126 s si le verdict déclenche**) | 0 | — |
| F7 restitution | 65,4 s | 5 | 0,091 $ |
| **Analyse** | **1 641,7 s — 27,4 min** | **67** | **2,273 $** |
| Six collecteurs | ~13 min | — | crédits Apify |

D'où les valeurs par défaut : `STUDY_TIMEOUT_ANALYSIS_SECONDS=1800` (2,7× le pire module
mesuré), `STUDY_TIMEOUT_COLLECTOR_SECONDS=1200`.

Les deux seuls leviers de coût de la collecte : `--avis` (Amazon facture **un run d'actor par
produit enrichi**) et `--annonces` (Meta facture **à l'annonce**).

## 8. Écarts assumés avec le script de référence

1. **Parallélisme.** `etude_marche.ps1` est entièrement séquentiel. L'orchestrateur lance les
   six collecteurs par vagues concurrentes (`STUDY_COLLECT_PARALLEL`, défaut 2 — ils sont
   indépendants par conception, la borne est un budget mémoire) et F3 ∥ F4 (entrées disjointes, 1 076 s → 666 s de mur). C'est une
   nouveauté, pas une reprise. Le coût API est inchangé : mêmes appels, mêmes tokens,
   simplement rapprochés dans le temps.
2. **Pas d'études sœurs multi-langues** (§4).
3. **Pas de mapping de devise côté backend** (§4).
4. **Python.** Le pipeline a été validé sous Python 3.14.3 ; le backend tourne en 3.12.13,
   avec les mêmes versions de paquets. `STUDY_PYTHON_EXECUTABLE` permet de repointer les
   sous-processus sur un autre interpréteur sans rien changer d'autre.
