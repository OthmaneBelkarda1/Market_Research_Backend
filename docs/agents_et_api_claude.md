# Les agents du projet et leur usage de l'API Claude

Ce document décrit **comment fonctionne l'ensemble des agents** du dépôt et
**comment chacun consomme l'API Anthropic (Claude)** : quel modèle, à quel
endroit du code, sur quel patron d'appel, avec quels garde-fous.

Il complète deux documents existants sans les remplacer :

- [`src/agents/market_study/AGENTS.md`](../src/agents/market_study/AGENTS.md) —
  le contrat fonctionnel entrées/sorties des onze modules d'étude de marché ;
- [`docs/pipeline_contrats.md`](pipeline_contrats.md) — le contrat que le backend
  câble pour les exécuter.

Ici, l'angle est différent : **l'architecture d'exécution et la couche LLM**.

---

## 1. Vue d'ensemble

Le dépôt contient **douze agents**, répartis en deux ensembles qui ne partagent
ni le même cycle de vie, ni la même façon d'appeler Claude.

| Ensemble | Nb | Emplacement | Nature | Comment Claude est appelé |
|---|---|---|---|---|
| **Extraction produit** | 1 | [`src/agents/product_extraction/`](../src/agents/product_extraction/) | Package Python importé par le backend | **Agent à outils** (boucle de raisonnement, `create_agent`) |
| **Étude de marché** | 11 | [`src/agents/market_study/`](../src/agents/market_study/) | Onze exécutables CLI autonomes, lancés en sous-processus | **Chaînes LCEL à sortie structurée** (un appel = une tâche) |

Les deux ensembles sont pilotés par une API FastAPI ([`src/main.py`](../src/main.py)) :

```
                        FastAPI (src/main.py)
                                │
        ┌───────────────────────┴───────────────────────┐
        │                                               │
  src/products/                                   src/studies/
  (fiche produit)                                 (étude de marché)
        │                                               │
  extraction.py                                    runner.py
        │  import direct                                │  subprocess
        ▼                                               ▼
  agents/product_extraction/              agents/market_study/  (11 modules)
  → agent LangChain à 3 outils            → 6 collecteurs + 5 analyseurs
```

Deux règles de couplage structurent tout le reste :

1. **Le backend n'importe jamais le code des onze modules d'étude.** Il les lance
   en sous-processus avec des arguments explicites et lit leur JSON sur `stdout`
   ([`src/studies/runner.py`](../src/studies/runner.py)). Le contrat est un JSON
   plus un code de sortie, jamais un import.
2. **Les modules d'analyse n'importent pas le code des collecteurs.** Chacun
   re-déclare en Pydantic, en `extra="ignore"`, les seuls champs qu'il consomme.

---

## 2. L'agent d'extraction produit

**Rôle** — une URL de page produit en entrée, une fiche produit standardisée en
sortie (`name`, `description`, `category`, `image_url`, `source_url`).

**Conception hybride** : le modèle fait du jugement, le code fait la collecte et
les chiffres.

```
URL ─► [code] routage ─► [outil] Playwright ou acteur Apify ─► champs déterministes
                                                             ─► preuves brutes
    ─► [LLM] lit les preuves, remplit les champs flous ─► ProductDraft
    ─► [code] recouvre avec les champs déterministes ─► ProductData
```

### Comment l'API Claude est utilisée

| Point | Valeur |
|---|---|
| Fichier | [`src/agents/product_extraction/agent.py`](../src/agents/product_extraction/agent.py) |
| Bibliothèque | `langchain.agents.create_agent` + `langchain_anthropic.ChatAnthropic` |
| Modèle | `claude-opus-5` (surchargeable par `ANTHROPIC_MODEL`) |
| `max_tokens` | `8000` (`PRODUCT_MAX_OUTPUT_TOKENS`) |
| `temperature` | **non transmis** — voir la note ci-dessous |
| Sortie contrainte | `response_format=ProductDraft` (Pydantic) |
| Boucle max | `recursion_limit=AGENT_MAX_STEPS` = `20` (`PRODUCT_AGENT_MAX_STEPS`) |
| Appel | `await agent.ainvoke({"messages": [("human", ...)]})` |

C'est le **seul agent du dépôt qui laisse le modèle décider de ses actions** :
il expose trois outils et Claude choisit lesquels appeler, dans quel ordre, et
peut réessayer par un autre chemin dans la même boucle de raisonnement.

| Outil | Ce qu'il fait | Réseau |
|---|---|---|
| `inspect_url` | Renvoie la stratégie de scraping requise pour l'URL | non |
| `fetch_product_page` | Rendu Playwright + parsing déterministe | oui |
| `fetch_via_apify` | Lance l'acteur Apify routé (marketplaces protégées) | oui |

Le prompt système suit le cadre **G.A.M.E.** (Goal / Actions / Memory /
Environment) et est assemblé dynamiquement : la règle sur les variantes est
insérée ou retirée selon `INCLUDE_VARIANTS`, et le pays acheteur est interpolé.

### Les garde-fous

- **La clé est vérifiée avant la construction du modèle** (`require_anthropic_key()`
  dans `build_agent`), afin qu'une clé absente devienne une réponse « non
  configuré » explicite et non un 500 nu remonté par le client HTTP.
- **`use_agent=False` court-circuite entièrement Claude** : le pipeline
  déterministe seul produit un `ProductData` du même schéma, sans clé API ni
  coût de jetons.
- **Repli sur échec du modèle** : si `ainvoke` lève et qu'aucun outil n'a réussi,
  le code relance la collecte déterministe pour que l'appelant reçoive quand même
  un enregistrement ([`agent.py:311-327`](../src/agents/product_extraction/agent.py#L311-L327)).
- **Le code a le dernier mot sur les chiffres** : `overlay_reliable()` recouvre la
  réponse du modèle avec les champs parsés de façon déterministe (prix, URL
  d'image, tableau de specs). Le modèle ne peut pas « corriger » un prix.

> **Note sur `temperature`** — l'absence du paramètre est délibérée et commentée
> dans le code : *« No temperature: the current Claude models reject sampling
> parameters »*. C'est exact pour les modèles récents (famille Opus 5 / 4.7 /
> 4.8, Sonnet 5), qui rejettent `temperature`, `top_p` et `top_k` en 400. Les
> onze modules d'étude de marché tournent, eux, sur des modèles antérieurs qui
> l'acceptent encore — d'où la divergence entre les deux ensembles.

---

## 3. Les onze agents d'étude de marché

Onze modules Python autonomes, chacun exécutable seul en ligne de commande,
répartis en deux étages. Le détail fonctionnel de chacun est dans
[`AGENTS.md`](../src/agents/market_study/AGENTS.md) ; on résume ici leur rôle et
on détaille leur consommation de l'API.

```
        collecte                          analyse                    restitution
reddit ─────────┐
amazon ─────────┼──► insights_consommateurs (F3) ─┐
recherche_web ──┘                                 │
                                                  ├──► recommandations_
aliexpress ─────┐                                 │     strategiques (F5) ──┐
amazon ─────────┼──► analyse_concurrentielle (F4) ─┤          │             │
meta_ads ───────┤                                 │   declenche_plc         │
recherche_web ──┘                                 │          ▼              ▼
                                                  │      plc (F6) ──►  restitution (F7)
tendances ────────────────────────────────────────┘                        │
                                                            rapport_etude.md│
                                                         resume_executif.md ┘
```

### 3.1 Les six collecteurs

Ils interrogent une source externe (Apify, ou l'API officielle AliExpress) et ne
font que **collecter, normaliser et qualifier** — aucun n'analyse ni ne conclut.

| Agent | Source | Rôle de Claude dans cet agent |
|---|---|---|
| [`agent_tendances/`](../src/agents/market_study/agent_tendances/) | Google Trends | Dériver le mot-clé pivot |
| [`agent_reddit/`](../src/agents/market_study/agent_reddit/) | Reddit | Bâtir la stratégie de recherche, scorer la pertinence |
| [`agent_recherche_web/`](../src/agents/market_study/agent_recherche_web/) | SERP + crawl | Planifier les requêtes, classer les pages |
| [`agent_aliexpress/`](../src/agents/market_study/agent_aliexpress/) | API DS AliExpress | Dériver les requêtes catalogue |
| [`agent_amazon/`](../src/agents/market_study/agent_amazon/) | Amazon | Résoudre la région, planifier, classer |
| [`agent_meta_ads/`](../src/agents/market_study/agent_meta_ads/) | Meta Ad Library | Résoudre la région, planifier, classer |

**Tous les six utilisent exclusivement `claude-haiku-4-5-20251001`, température 0.**

Détail des appels, module par module :

| Agent | Fichier | Chaîne LLM | Sortie structurée | Lot |
|---|---|---|---|---|
| tendances | `keywords.py` | contrôle qualité de la fiche | `RapportQualiteInput` | — |
| | `keywords.py` | dérivation du jeu de mots-clés | `PropositionMotsCles` | — |
| reddit | `strategy.py` | contrôle qualité de la fiche | `RapportQualiteInput` | — |
| | `strategy.py` | stratégie de recherche (requêtes + subreddits) | `StrategieRecherche` | — |
| | `strategy.py` | subreddit généraliste du pays | — | — |
| | `relevance.py` | scoring de pertinence des posts | `LotClassification` | **20** |
| recherche_web | `queries.py` | contrôle qualité de la fiche | `RapportQualiteInput` | — |
| | `queries.py` | plan de requêtes sur 2 axes | `PlanRequetes` | — |
| | `filtering.py` | classification des pages | `LotClassification` | **10** |
| aliexpress | `strategy.py` | contrôle qualité de la fiche | `RapportQualiteInput` | — |
| | `strategy.py` | requêtes catalogue | `RequetesMarketplace` | — |
| amazon | `strategy.py` | contrôle qualité de la fiche | `RapportQualiteInput` | — |
| | `strategy.py` | résolution de la région → marketplace | `RegionResolue` | — |
| | `strategy.py` | plan de recherches | `PlanRecherches` | — |
| | `filtering.py` | classification des produits | `LotClassification` | **15** |
| meta_ads | `strategy.py` | contrôle qualité de la fiche | `RapportQualiteInput` | — |
| | `strategy.py` | résolution de la région → pays de diffusion | `RegionResolue` | — |
| | `strategy.py` | plan de recherches | `PlanRecherches` | — |
| | `filtering.py` | classification des annonces | `LotClassification` | **15** |

Plafonds de jetons produits, par collecteur :

| Agent | `MAX_TOKENS_LLM` |
|---|---|
| tendances, aliexpress | `1024` |
| reddit | `2048` |
| recherche_web, amazon, meta_ads | `4096` |

### 3.2 Les cinq agents d'analyse et de restitution

Ils ne collectent rien : **leur seul accès réseau est l'API Anthropic**. Ni
`apify-client`, ni `httpx` dans leurs `requirements.txt`. Ils consomment les
fichiers JSON produits en amont.

| Agent | Entrées | Ce qu'il produit |
|---|---|---|
| [`agent_insights_consommateurs/`](../src/agents/market_study/agent_insights_consommateurs/) (F3) | reddit, amazon, recherche_web | Sentiment, thèmes, pain points, besoins |
| [`agent_analyse_concurrentielle/`](../src/agents/market_study/agent_analyse_concurrentielle/) (F4) | aliexpress, amazon, meta_ads, recherche_web | Concurrents, benchmark par devise, positionnement |
| [`agent_recommandations_strategiques/`](../src/agents/market_study/agent_recommandations_strategiques/) (F5) | F3, F4, tendances | Diagnostic, **verdict de potentiel**, recommandations |
| [`agent_plc/`](../src/agents/market_study/agent_plc/) (F6) | F5 (requise), F3, F4 | **Phase de cycle de vie** — seulement si verdict positif |
| [`agent_restitution/`](../src/agents/market_study/agent_restitution/) (F7) | F5 (requise), F3, F4, F6 | Rapport Markdown 9 sections + résumé exécutif |

Ils emploient **deux modèles**, choisis par nature de tâche :

| Modèle | Rôle | `max_tokens` |
|---|---|---|
| `claude-haiku-4-5-20251001` | Extraction et cartographie **par lots** (F3, F4 seulement) | `8000` |
| `claude-sonnet-4-5-20250929` | Synthèse et rédaction (F3 à F7) | `16000` (F7 : `4000`, F6 : `8000`) |

Détail des chaînes :

| Agent | Fichier | Chaîne LLM | Modèle | Lot |
|---|---|---|---|---|
| **F3** | `carte.py` | cartographie des unités consommateurs | haiku | **15** |
| | `carte.py` | cartographie des documents longs | haiku | **4** |
| | `carte.py` | normalisation des libellés | haiku | — |
| | `synthese.py` | synthèse des insights | sonnet | — |
| | `synthese.py` | lecture critique de la synthèse | sonnet | — |
| **F4** | `extraction.py` | extraction d'attributs | haiku | **12** |
| | `extraction.py` | extraction de claims | haiku | **10** |
| | `consolidation.py` | consolidation des concurrents | haiku | — |
| | `analyse.py` | analyse par concurrent | sonnet | — |
| | `analyse.py` | lecture transversale | sonnet | — |
| | `analyse.py` | différenciation | sonnet | — |
| | `analyse.py` | synthèse concurrentielle | sonnet | — |
| **F5** | `diagnostic.py` | diagnostic croisé | sonnet | — |
| | `potentiel.py` | **notation** de la grille de potentiel (×2 chaînes) | sonnet | — |
| | `recommandations.py` | recommandations, opportunités, risques (×3) | sonnet | — |
| **F6** | `classification.py` | orientation des familles de signaux | sonnet | — |
| | `recommandations.py` | recommandations propres à la phase | sonnet | — |
| **F7** | `redaction.py` | narratif et transitions du rapport | sonnet | — |

> **F6 peut ne faire aucun appel.** Si `verdict_potentiel.declenche_plc` est
> faux, le module produit une sortie courte de non-déclenchement, **sans le
> moindre appel LLM**, en code de sortie 0. Ce n'est pas une erreur.

---

## 4. La couche d'accès à Claude — ce qui est commun

### 4.1 On passe par LangChain, jamais par le SDK `anthropic` directement

**Aucun fichier du dépôt n'importe `anthropic` ni n'instancie `Anthropic()`.**
Tout passe par l'intégration LangChain :

```python
from langchain_anthropic import ChatAnthropic
```

Conséquence pratique : les fonctionnalités de l'API Anthropic accessibles sont
celles que `langchain_anthropic` expose. Ni mise en cache de prompt
(`cache_control`), ni thinking étendu, ni traitement par lots (Batches API) ne
sont utilisés à ce jour.

### 4.2 L'authentification

Une seule variable, `ANTHROPIC_API_KEY`, partagée par les douze agents. Trois
façons de la lire cohabitent :

| Ensemble | Mécanisme |
|---|---|
| `product_extraction` | `load_dotenv(override=True)` à l'import, puis `require_anthropic_key()` au moment de construire l'agent |
| Six collecteurs | `ANTHROPIC_API_KEY` lu dans `config.py`, passé explicitement en `api_key=` à `ChatAnthropic`, `RuntimeError` si absent |
| Cinq analyseurs | `verifier_cle_api()` au démarrage, puis clé laissée à la résolution implicite du client |

Le backend, lui, ne bloque pas au démarrage sur une clé absente :
`check_pipeline_credentials()` ([`runner.py:82`](../src/studies/runner.py#L82))
journalise un avertissement et laisse chaque module signaler lui-même le manque
au moment où il en a besoin.

### 4.3 Les deux patrons d'appel

**Patron A — chaîne LCEL à sortie structurée.** C'est le patron des onze modules
d'étude de marché, sans exception :

```python
chaine = _PROMPT | _modele().with_structured_output(MonSchemaPydantic)
resultat: MonSchemaPydantic = chaine.invoke(variables)
```

`with_structured_output` traduit le schéma Pydantic en définition d'outil
Anthropic et force le modèle à l'appeler ; la réponse revient validée. Un objet
racine est toujours requis — d'où les enveloppes `LotClassification`,
`LotAttributs`, etc. pour les traitements par lots.

**Patron B — agent à outils.** Réservé à `product_extraction` :

```python
create_agent(ChatAnthropic(...), tools, system_prompt=..., response_format=ProductDraft)
```

Le modèle décide de la séquence d'appels d'outils ; la réponse finale est
contrainte à `ProductDraft`.

### 4.4 La reprise sur échec

Les cinq agents d'analyse partagent une fonction unique, dupliquée dans chaque
`config.py` (les modules sont volontairement autonomes) :

```python
def invoquer_structure(chaine, entree, libelle) -> tuple[Any | None, int, str | None]:
```

Son principe : `NB_TENTATIVES_LLM = 2` tentatives, et **la deuxième réinjecte le
message d'erreur de la première dans le prompt** via la variable réservée
`erreur_precedente` :

> « ATTENTION — ta réponse précédente a été rejetée pour cette raison : … Corrige-la
> et respecte strictement le schéma demandé. »

Elle renvoie `(resultat, nb_tentatives, message_erreur)` et **ne lève jamais** :
un échec total renvoie `None` et l'appelant dégrade. Voir par exemple
[`agent_insights_consommateurs/config.py:347`](../src/agents/market_study/agent_insights_consommateurs/config.py#L347).

Les six collecteurs appliquent la même philosophie plus simplement : chaque
`chaine.invoke()` est enveloppé d'un `try/except Exception` large, commenté
`# noqa: BLE001 — toute erreur doit dégrader, pas casser`. Un contrôle qualité
indisponible renvoie une liste d'alertes vide et la collecte continue.

### 4.5 La comptabilité des jetons et du coût

Les cinq agents d'analyse mesurent leur consommation via le callback LangChain,
dans leur `main.py` :

```python
from langchain_core.callbacks import get_usage_metadata_callback

with get_usage_metadata_callback() as consommation:
    ...
recapitulatif = resumer_consommation(consommation.usage_metadata)
```

`resumer_consommation()` applique la table de tarifs déclarée dans chaque
`config.py` :

```python
TARIFS_USD_PAR_MTOK = {
    MODELE_EXTRACTION: (1.00, 5.00),    # haiku 4.5 : $/M jetons entrée, sortie
    MODELE_SYNTHESE:   (3.00, 15.00),   # sonnet 4.5
}
```

et produit une ligne du type
`claude-haiku-4-5-20251001 : 128340 jetons entrée / 9210 sortie (~0,1743 $) | total estimé ~0,4512 $`,
émise sur `stderr`. **Les six collecteurs et l'agent d'extraction produit ne
comptabilisent rien** — leur coût dominant est Apify, pas Anthropic.

### 4.6 Le parallélisme

Trois collecteurs (`amazon`, `meta_ads`, `recherche_web`) exécutent leurs runs
Apify **et leurs classifications LLM** dans un `ThreadPoolExecutor` borné à
`PARALLELISME_MAX = 3`. `agent_tendances` est strictement séquentiel (20 s de
pause entre appels, imposé par la source). Les cinq analyseurs sont séquentiels.
Côté backend, `MAX_CONCURRENCY` borne le nombre d'études simultanées.

---

## 5. Récapitulatif : quel modèle où

| Agent | Modèle(s) | Température | Où c'est déclaré |
|---|---|---|---|
| `product_extraction` | `claude-opus-5` | *(non transmis)* | `config.py:26` |
| `agent_tendances` | `claude-haiku-4-5-20251001` | 0.0 | `config.py:38` |
| `agent_reddit` | `claude-haiku-4-5-20251001` | 0.0 | `config.py:53` |
| `agent_recherche_web` | `claude-haiku-4-5-20251001` | 0.0 | `config.py:42` |
| `agent_aliexpress` | `claude-haiku-4-5-20251001` | 0.0 | `config.py:57` |
| `agent_amazon` | `claude-haiku-4-5-20251001` | 0.0 | `config.py:42` |
| `agent_meta_ads` | `claude-haiku-4-5-20251001` | 0.0 | `config.py:43` |
| `agent_insights_consommateurs` | haiku 4.5 + `claude-sonnet-4-5-20250929` | 0.0 | `config.py:72,75` |
| `agent_analyse_concurrentielle` | haiku 4.5 + sonnet 4.5 | 0.0 | `config.py:62,65` |
| `agent_recommandations_strategiques` | `claude-sonnet-4-5-20250929` | 0.0 | `config.py:71` |
| `agent_plc` | `claude-sonnet-4-5-20250929` | 0.0 | `config.py:73` |
| `agent_restitution` | `claude-sonnet-4-5-20250929` | 0.0 | `config.py:76` |

Coût mesuré de l'étage d'analyse sur le run de référence *ashwagandha-ES*
(292 contributions, 165 offres) :

| Module | Appels LLM | Coût estimé | Durée |
|---|---|---|---|
| F3 — insights consommateurs | 26 | ≈ 0,45 $ | 312 s |
| F4 — analyse concurrentielle | 27 | ≈ 0,78 $ | 568 s |
| F5 — verdict et recommandations | 6 | ≈ 0,71 $ | 503 s |
| F6 — phase de cycle de vie | 2 (0 si non déclenché) | ≈ 0,14 $ | 126 s |
| F7 — rapport final | 6 | ≈ 0,10 $ | 80 s |
| **Total** | **67** | **≈ 2,18 $** | **≈ 26 min** |

L'étage de collecte, lui, coûte surtout **en Apify** : le nombre d'annonces Meta
demandées (l'acteur est facturé à l'annonce) et le nombre de produits Amazon
enrichis d'avis (un run d'acteur par produit) dominent très largement la facture
Anthropic de ces six modules.

---

## 6. Les principes qui encadrent l'usage du modèle

Cinq règles de conception traversent tout le dépôt et expliquent pourquoi les
appels LLM sont si étroitement bornés.

1. **Le modèle propose, le code décide.** Le verdict de potentiel (F5) et la
   phase de cycle de vie (F6) sont calculés par des fonctions pures. Le modèle
   *note* la grille, il ne conclut jamais.
2. **Aucun nombre produit par un modèle n'atteint une sortie.** Tous les chiffres
   sont calculés par du code puis **réécrits** par une post-validation. F7 va
   plus loin : chaque nombre du rapport est confronté à une liste blanche
   construite depuis les entrées, et une phrase portant un nombre inconnu est
   retirée. Côté extraction produit, `overlay_reliable()` joue le même rôle.
3. **Toute affirmation étayée est traçable.** Chaque fondement cite une `ref`
   vérifiée ; une référence inventée est retirée et tracée, jamais laissée.
4. **Ce qui n'est pas mesuré est déclaré.** Chaque module publie ses `limites` et
   ses `hypotheses` — dont, systématiquement, le fait que la classification est
   produite par un modèle de langage et n'a pas été validée contre un codage
   humain.
5. **Aucune agrégation abusive.** Pas de conversion de devises, pas de moyenne
   entre langues, régions ou plateformes. Le prompt d'extraction produit
   l'énonce explicitement : *« never convert between currencies and never
   "correct" a currency you find surprising »*.

Deux conséquences directes sur la façon d'écrire les prompts ici :

- **Les prompts avertissent le modèle de ce qu'il ne peut pas savoir.** Celui de
  `agent_reddit` lui dit noir sur blanc : *« tu peux proposer des subreddits
  inexistants ou inactifs, et tu n'as AUCUN moyen de le vérifier. Ne prétends
  jamais avoir vérifié leur existence »*.
- **Une sortie défavorable n'est jamais une erreur.** Verdict négatif, phase non
  classée, non-déclenchement : tout sort en code 0. Un orchestrateur lit le JSON,
  jamais le code de sortie.

---

## 7. Points d'attention

**Deux générations de modèles cohabitent.** `product_extraction` a été porté sur
`claude-opus-5`, tandis que les onze modules d'étude de marché tournent encore
sur `claude-haiku-4-5-20251001` et `claude-sonnet-4-5-20250929`. C'est
fonctionnel — ces identifiants restent valides — mais cela explique la divergence
sur `temperature` : les modèles récents rejettent les paramètres
d'échantillonnage en 400, les modèles 4.5 les acceptent. **Toute migration des
onze modules vers un modèle récent devra retirer `temperature=0.0` de chaque
`ChatAnthropic(...)`**, sans quoi chaque appel échouera.

**Les identifiants sont figés en dur, sauf un.** Seul `product_extraction` lit
son modèle depuis l'environnement (`ANTHROPIC_MODEL`). Les onze autres portent la
chaîne littérale dans leur `config.py` — par conception, chaque module étant
autonome, mais cela signifie douze points de modification lors d'une migration.

**Aucune mise en cache de prompt n'est en place.** Les prompts système des
analyseurs sont volumineux et stables d'un lot à l'autre ; `cache_control` sur le
préfixe réduirait sensiblement le coût des étapes par lots (F3 et F4 concentrent
53 des 67 appels). Cela demande de passer par les paramètres additionnels de
`ChatAnthropic`, ou par le SDK Anthropic directement.

**Un module en échec LLM dégrade silencieusement.** C'est voulu, mais cela veut
dire qu'un `stderr` non lu masque une analyse partielle. Les compteurs
`statuts_analyse` et `nb_tentatives` de chaque sortie JSON sont la seule trace
exploitable en aval : les surveiller vaut mieux que les codes de sortie.
