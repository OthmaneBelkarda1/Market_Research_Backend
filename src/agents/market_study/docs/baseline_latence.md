# Baseline de latence — chaîne d'analyse F3→F7

> Chantier 0 du chantier d'optimisation. **Aucune modification de code n'a été
> faite pour produire ce document.** Les durées sont mesurées de l'extérieur,
> horodatage avant et après chaque commande ; les durées par phase sont
> reconstituées depuis les journaux `--verbose`, où chaque ligne « succès en N
> tentative(s) » horodate la fin d'un appel.

- **Jeu de référence** : `etudes/ashwagandha-supplement-ES/es/` (292
  contributions consommateurs, 165 offres, 12 pages web).
- **Sorties conservées** : `docs/baseline/ashwagandha-ES/` — ce sont elles
  l'étalon de non-régression de tous les chantiers suivants.
- **Exécution** : 2026-08-06, 17:18 → 17:46, séquentielle, un module après
  l'autre, `.venv` Python 3.14.3, `langchain-anthropic` 1.5.2.

---

## 1. Mesure d'ensemble

| Module | Durée | Part | Appels LLM | Coût |
|---|---:|---:|---:|---:|
| F3 — insights consommateurs | 409,6 s | 25 % | 25 | 0,546 $ |
| F4 — analyse concurrentielle | 666,3 s | 41 % | 31 | 0,891 $ |
| F5 — recommandations et verdict | 497,7 s | 30 % | 6 | 0,745 $ |
| F6 — cycle de vie | 1,9 s | 0 % | **0** | — |
| F7 — restitution | 65,4 s | 4 % | 5 | 0,091 $ |
| **Total** | **1 641,7 s (27,4 min)** | | **67** | **2,273 $** |

**Écart avec le constat opérationnel des ~40 min : il n'y en a pas, le périmètre
diffère.** F3→F7 mesuré vaut 27,4 min, très proche des ~26,5 min documentés par
module. Les ~13 min restantes du constat à 40 min appartiennent aux six
collecteurs, qui sont hors périmètre de ce chantier. **La cible « < 15 min »
porte donc sur un périmètre qui en fait aujourd'hui 27,4, pas 40** : le facteur
à gagner est de 1,8, pas de 2,7.

**Le temps mort est négligeable.** Sur F3, 409,6 s mesurées de l'extérieur contre
407,7 s d'appels LLM tracés : 1,9 s pour le démarrage de Python, les imports
LangChain, la lecture des entrées et l'écriture de la sortie. Même ordre partout
ailleurs. **Il n'y a rien à gagner hors des appels LLM** — le pipeline n'attend
pas sur du calcul local, il attend sur le réseau.

**F6 est un cas particulier à ne pas lire trop vite.** 1,9 s et zéro appel, parce
que le verdict de ce jeu de référence est `indetermine` : la classification n'est
pas déclenchée. Sur un verdict positif — le run *air-compression-leg-massager-US*
— F6 passe 2 appels enchaînés, soit ~126 s. **Le budget doit être calculé sur ce
cas-là**, pas sur celui-ci.

---

## 2. Cartographie des appels

### F3 — 25 appels, 409,6 s

| Phase | Modèle | Appels | Cumul | Moy. | Max |
|---|---|---:|---:|---:|---:|
| `carte_unites` | haiku | 18 | 230,2 s | 12,8 s | 20,8 s |
| `synthese_insights` | sonnet | 1 | **101,8 s** | | |
| `lecture_critique` | sonnet | 1 | 29,0 s | | |
| `normalisation_libelles` | haiku | 2 | 27,0 s | 13,5 s | 20,0 s |
| `carte_documents` | haiku | 3 | 19,8 s | 6,6 s | 7,7 s |

### F4 — 31 appels, 666,3 s

| Phase | Modèle | Appels | Cumul | Moy. | Max |
|---|---|---:|---:|---:|---:|
| `consolidation_concurrents` | sonnet | 1 | **114,3 s** | | |
| `extraction_attributs` | haiku | 15 | 72,8 s | 4,9 s | 7,4 s |
| `lecture_transversale` | sonnet | 1 | 65,8 s | | |
| `analyse_concurrent` | sonnet | 8 | 322,5 s | 40,3 s | 55,2 s |
| `differenciation` | sonnet | 1 | 50,3 s | | |
| `extraction_claims` | haiku | 4 | 22,1 s | 5,5 s | 7,1 s |
| `synthese_executive` | sonnet | 1 | 16,4 s | | |

### F5 — 6 appels, 497,7 s — **tous en sonnet**

| Phase | Durée |
|---|---:|
| `recommandations` | **153,9 s** |
| `opportunites_risques` | 110,3 s |
| `diagnostic_croise` | 82,5 s |
| `notation_grille` | 79,5 s |
| `faits_cles_synthese` | 50,7 s |
| `conditions_reexamen` | 17,7 s |

### F7 — 5 appels, 65,4 s — **tous en sonnet**

`redaction_synthese` 17,2 s · `redaction_consommateurs` 13,8 s ·
`redaction_concurrence` 13,2 s · `redaction_verdict` 11,5 s ·
`redaction_demande` 7,6 s. La section cycle de vie n'est pas rédigée ici (phase
non déterminée) : sur un verdict positif il y en a six.

**Aucune tentative en échec sur l'ensemble du run.** 67 appels, 67 succès en
première tentative. Le retry de validation Pydantic n'a pas servi.

---

## 3. Les cinq hypothèses du prompt

| # | Hypothèse | Verdict | Constat |
|---|---|---|---|
| 1 | Lots F3/F4 séquentiels | **Confirmée** | `for … in _decouper(…)` + `invoquer_structure` → `chaine.invoke` |
| 2 | 8 analyses par concurrent séquentielles | **Confirmée** | `for concurrent in concurrents[:TOP_N_CONCURRENTS_ANALYSES]` |
| 3 | Chaînes F5 en série | **Confirmée** | 6 appels enchaînés dans `recommander()` |
| 4 | F6/F7 lancés à la main | **INFIRMÉE** | Câblés dans `etude_marche.ps1` depuis le 06/08/2026 (étapes 10 et 11 sur 11). Le point 3 du chantier 3 est sans objet. |
| 5 | Aucun prompt caching | **Confirmée** | Aucune occurrence de `cache_control` dans le dépôt |

### Trois écarts supplémentaires, non prévus par le prompt

**Le dépôt possède déjà son idiome de concurrence.**
`ThreadPoolExecutor(max_workers=PARALLELISME_MAX)` + `executeur.map(...)` dans
[agent_recherche_web/agent.py:110](../agent_recherche_web/agent.py#L110),
[agent_amazon/agent.py:120](../agent_amazon/agent.py#L120) et
[agent_meta_ads/agent.py:123](../agent_meta_ads/agent.py#L123), avec
`PARALLELISME_MAX = 3` en constante commentée. `executeur.map` restitue les
résultats **dans l'ordre des entrées** : l'exigence de déterminisme d'agrégation
du §2.1 est satisfaite par construction, sans indexation explicite.
**Recommandation : retenir cet idiome plutôt qu'asyncio/`abatch`.** Le code
d'analyse est intégralement synchrone ; y introduire une boucle d'événements
pour un gain identique serait une dette gratuite, et ferait diverger les cinq
agents d'analyse des quatre collecteurs déjà parallélisés.

**Il n'existe aucun backoff réseau, et c'est un prérequis, pas un raffinement.**
`invoquer_structure` attrape `except Exception` sans distinguer un `429` d'une
erreur de validation Pydantic : un throttling consomme l'une des deux tentatives
et, s'il se répète, **écarte le lot**. Aujourd'hui le risque est théorique — un
seul appel est en vol à la fois. À 6 appels concurrents ce serait le premier mode
de panne du chantier 2, et il dégraderait silencieusement la sortie au lieu de la
ralentir. Le backoff doit être livré **avec** la concurrence, pas après.

**Le palier de débit du compte Anthropic n'a pas été vérifié.** `MAX_CONCURRENCE_LLM
= 6` est à confirmer avant le chantier 2 ; le comportement observé au premier run
concurrent tranchera.

---

## 4. Dépendances réelles entre chaînes

Relevées dans le code, pas dans les specs. Deux d'entre elles contredisent le
prompt, dans les deux sens.

| Chaîne | Ce qu'elle consomme réellement | Conséquence |
|---|---|---|
| F3 `lecture_critique` | `besoins`, issu de `synthetiser_insights` | **Reste en série.** Le §2.2 posait la question, la réponse est non. |
| F3 `normalisation` (thèmes / pain points) | les cartes uniquement | Parallélisables entre elles ✔ |
| F3 `carte_documents` | rien de `carte_unites` | Parallélisable avec les unités ✔ |
| F4 `consolider` | le référentiel, **jamais** `attributs_extraits` | Peut remonter en parallèle de l'extraction ✔ |
| F4 `lire_transversalement` | `offre.attributs_extraits`, **jamais** `analyses` | Parallélisable avec les 8 analyses ✔ |
| F4 `analyser_differenciation` | `lecture` | Reste en aval de la lecture |
| F4 `rediger_synthese` | `resumes` (issus des analyses) **et** `lecture` | Reste en dernier |
| F5 `noter_grille` | le dossier seul, **pas** `diagnostic` | Parallélisable avec le diagnostic ✔ |
| F5 `rediger_conditions_reexamen` | `verdict` seul | **Parallélisable avec recommandations et opportunités — le §2.4 la laissait en série.** |
| F5 `produire_restitution` | verdict, recommandations, opportunités | Reste en dernier |
| F7 — 6 sections | chacune sa tranche via `donnees_de_section` | Parallélisme total ✔ |

**Une dépendance irréductible, entre processus.** F7 lit `plc.json` : F6 doit
avoir fini. Sur un verdict positif, cela ajoute ~126 s en série qu'aucune
parallélisation intra-agent ne réduit.

---

## 5. Projection — ce que les chantiers 2 et 3 peuvent rendre

Estimations à concurrence 6, en supposant les durées d'appel inchangées (elles le
sont : même modèle, même prompt, même charge).

| Module | Aujourd'hui | Projeté | Chemin critique résiduel |
|---|---:|---:|---|
| F3 | 409,6 s | ~200 s | `synthese_insights` (102 s) → `lecture_critique` (29 s) |
| F4 | 666,3 s | ~275 s | `consolidation` (114 s) → analyses ∥ lecture (~95 s) → différenciation (50 s) → synthèse (16 s) |
| F5 | 497,7 s | ~285 s | diagnostic ∥ notation (83 s) → recommandations (154 s) → faits clés (51 s) |
| F6 | 1,9 s | 1,9 s | rien à paralléliser (2 appels enchaînés si déclenché) |
| F7 | 65,4 s | ~22 s | la plus longue section (17 s) |

- **Chantier 2 seul, exécution séquentielle des modules : ~13,1 min.** Sous la
  cible, mais sans marge.
- **Chantiers 2 + 3 (F3 ∥ F4) : ~9,8 min.** `max(200, 275) + 285 + 2 + 22`.
- **Sur un verdict positif** (F6 déclenché à ~126 s, F7 à six sections) :
  **~12 min**. La cible tient, la marge est de trois minutes.

**Ce qui résiste.** Après parallélisation, plus de 60 % du temps restant tient
dans **cinq appels sonnet uniques et longs** : `recommandations` 154 s,
`consolidation_concurrents` 114 s, `synthese_insights` 102 s, `lecture_transversale`
66 s, `differenciation` 50 s. Aucune concurrence ne les réduit. Les attaquer
supposerait de découper ou d'alléger des prompts métier — explicitement hors
périmètre. **Si la cible n'est pas tenue après les chantiers 2 à 4, c'est là que
se trouve l'arbitrage, et il vous appartient.**

---

## 6. Prochaine étape

Chantier 1 — instrumentation permanente. Un point à noter : les cinq agents
émettent **déjà** leur consommation de jetons sur `stderr` en mode normal, via
`get_usage_metadata_callback` et `resumer_consommation`. Il manque la durée, le
nombre d'appels et le détail par phase, tous additifs. Le chantier 4 pourra
mesurer l'effet du caching par le même instrument, moyennant l'ajout des
compteurs `cache_read_input_tokens` et `cache_creation_input_tokens` à
`resumer_consommation`.
