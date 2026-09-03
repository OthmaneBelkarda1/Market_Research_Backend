# Agent F3 — Insights Consommateurs

> **Rôle dans la chaîne** : troisième module du pipeline d'étude de marché. Il ne collecte
> rien : il consomme les sorties JSON de trois collecteurs (Reddit, Amazon, Recherche web)
> et produit l'analyse de l'**axe 1 — la voix du consommateur**.
>
> **Position** : déclenché après la barrière de collecte, en parallèle de F4 (analyse
> concurrentielle), dont il ne lit rien et qui ne lit rien de lui. Sa sortie
> `insights.json` alimente ensuite F5, F6 et F7.
>
> **Emplacement** : `src/agents/market_study/agent_insights_consommateurs/`

---

## 1. Le principe directeur : le modèle qualifie, le code compte

C'est la clé de lecture de tout l'agent, et ce qui explique son découpage en fichiers.

Le travail est réparti selon une ligne stricte :

| Le modèle fait | Le code fait |
|---|---|
| Classer un avis (sentiment, thèmes, pain points, signaux d'achat) | Compter les classifications |
| Regrouper des libellés synonymes | Calculer fréquences, pourcentages, scores |
| Rédiger des descriptions et une synthèse | Trier, plafonner, décider de la confiance |
| Repérer des biais méthodologiques | Vérifier que chaque citation existe vraiment |

**Aucun nombre publié par l'agent ne vient d'un modèle.** Tous sont calculés dans
`reduction.py`, et `validation.py` les réécrit dans la sortie finale — de sorte qu'un
chiffre inventé par un LLM ne peut pas survivre jusqu'au rapport. C'est une garantie
architecturale, pas une consigne de prompt.

Corollaire visible dans l'arborescence : **trois fichiers sur neuf ne touchent jamais au
LLM** (`corpus.py`, `reduction.py`, `validation.py`) et sont entièrement déterministes —
à entrée identique, sortie identique.

---

## 2. Le pipeline de bout en bout

```
  reddit.json    amazon.json    recherche_web.json
       │              │                │
       └──────────────┴────────────────┘
                      ▼
   ①  CHARGEMENT            chargement.py     (aucun LLM)
       lecture tolérante, validation, contrôle de cohérence produit
                      ▼
   ②  CORPUS                corpus.py         (aucun LLM)
       unités + documents, filtrage, dédoublonnage, échantillonnage
                      ▼
   ③  CARTE                 carte.py          ◄── LLM haiku, par lots
       classification unité par unité, puis document par document
                      ▼
   ④  NORMALISATION         carte.py          ◄── LLM haiku, 2 appels
       « livraison lente » + « délai trop long » → un seul libellé
                      ▼
   ⑤  RÉDUCTION             reduction.py      (aucun LLM)
       TOUS les chiffres de la sortie sont calculés ici
                      ▼
   ⑥  SYNTHÈSE              synthese.py       ◄── LLM sonnet, 2 appels
       rédaction à partir des agrégats — jamais du corpus brut
                      ▼
   ⑦  ASSEMBLAGE            agent.py          (aucun LLM)
       fusion chiffres + rédaction, plafonnement de la confiance
                      ▼
   ⑧  POST-VALIDATION       validation.py     (aucun LLM)
       réécriture des nombres, vérification des citations
                      ▼
                 insights.json
```

### Ce que chaque étape fait

**① Chargement.** Lit les trois fichiers, valide chacun contre un schéma tolérant. Règle
centrale : *aucune exception n'est propagée pour une source*. Un fichier absent, illisible
ou non conforme écarte sa source avec un avertissement tracé, et l'analyse continue sur les
autres. **Un seul cas est bloquant** : deux fichiers portant des produits différents — cela
signifie qu'on mélange deux études, et l'agent s'arrête (`ErreurCoherenceProduit`, code 2).

**② Corpus.** Transforme les enregistrements collecteurs en deux populations distinctes :

- les **unités consommateurs** — un post Reddit, un commentaire, un avis Amazon : une
  opinion individuelle ;
- les **documents web** — une page qui *rapporte* des retours consommateurs, sans être
  elle-même une opinion.

Puis filtre (seuil de pertinence amont 0,5 ; longueur minimale 15 caractères),
dédoublonne, et **échantillonne** si le corpus dépasse 400 unités — par quotas de source
(50 % maximum chacune), en priorisant pertinence, puis poids social, puis récence. Chaque
réduction opérée est inscrite dans les `limites` de la sortie.

**③ Carte.** Le premier étage LLM. Les unités sont découpées en **lots de 15**, les
documents en **lots de 4**, et chaque lot part dans une chaîne à sortie structurée. Le
modèle attribue à chaque unité : sentiment, thèmes, pain points avec intensité, besoins,
attentes, signaux d'achat. Un lot en échec après reprise est **écarté sans rattrapage** :
ses unités sont simplement absentes de l'analyse, ce qui est tracé.

**④ Normalisation.** Le modèle a produit des libellés libres, donc redondants. Deux appels
— un pour les thèmes, un pour les pain points — construisent une table de correspondance
qui fusionne les synonymes. **Les fréquences éventuellement renvoyées par le modèle sont
ignorées** : seule la table de remappage est retenue, et le recomptage est refait par le
code.

**⑤ Réduction.** Le cœur chiffré. Calcule les répartitions de sentiment (global, par
source, par portée), les fréquences de thèmes et de pain points, l'intensité moyenne, la
confiance par insight, et le score de priorité :

```
score_priorite = frequence_pct × intensite_moyenne × (1 + 0,25 × (nb_sources − 1))
```

Convention de comptage assumée : **les fréquences portent sur les unités consommateurs.**
Une page web atteste qu'un thème existe dans le discours éditorial et compte donc dans les
*sources* d'un insight, mais n'est pas une opinion individuelle et n'entre pas dans les
fréquences.

**⑥ Synthèse.** Le second étage LLM, sur un modèle plus capable. Deux chaînes qui reçoivent
**les agrégats, jamais le corpus brut** :

1. *Synthèse des insights* — rédige les descriptions de pain points, structure besoins et
   attentes, lit les comportements d'achat, relève les divergences entre sources ;
2. *Lecture critique* — biais probables, facteurs de confiance, synthèse exécutive.

Aucune des deux ne produit de nombre.

**⑦ Assemblage.** Fusionne les chiffres de ⑤ et la rédaction de ⑥. C'est ici que le
**niveau de confiance proposé par le modèle est plafonné par le code** : moins de 30
unités → confiance faible, quoi qu'en dise le modèle ; une seule source → faible ; « élevée »
avec moins de 60 unités → ramenée à « moyenne », avec mention explicite du déclassement.

**⑧ Post-validation.** Le filet de sécurité final, en trois garanties :

1. **les nombres sont réécrits** depuis la réduction — un pain point rédigé par la synthèse
   mais sans agrégat correspondant est *supprimé* et signalé (`insight_non_ancre`) ;
2. **toute référence à une unité citée doit exister** dans le corpus, sinon elle est retirée ;
3. **tout extrait de verbatim doit être une sous-chaîne du texte source**, sinon il est
   remplacé par le vrai début du texte.

Chaque correction est tracée dans `statuts_analyse`. Sur un run sain, cette étape est
silencieuse.

---

## 3. Rôle de chaque fichier

| Fichier | Lignes | LLM | Rôle |
|---|---:|:---:|---|
| `main.py` | 138 | — | Point d'entrée CLI |
| `agent.py` | 424 | — | Orchestration des 8 étapes |
| `chargement.py` | 294 | — | ① Lecture et validation des entrées |
| `corpus.py` | 505 | — | ② Constitution du corpus |
| `carte.py` | 473 | **oui** | ③④ Classification et normalisation par lots |
| `reduction.py` | 365 | — | ⑤ Tous les calculs chiffrés |
| `synthese.py` | 263 | **oui** | ⑥ Rédaction analytique |
| `validation.py` | 246 | — | ⑧ Post-validation anti-hallucination |
| `schemas.py` | 613 | — | Contrats Pydantic, de bout en bout |
| `config.py` | 457 | — | Constantes, seuils, plomberie LLM |

### `main.py` — l'interface en ligne de commande

Construit l'analyseur d'arguments (`--reddit`, `--amazon`, `--recherche-web`,
`--langue-analyse`, `--sortie`, `--stdout`, `--verbose`), appelle `analyser_insights()` et
sérialise le résultat.

Discipline de sortie : **`stdout` reste du JSON pur**, toute progression et toute erreur
partent sur `stderr`. Trois codes de sortie : `0` succès, `1` erreur imprévue, `2` entrée
inexploitable ou incohérence de produit. Aucune trace Python nue n'est jamais affichée.

C'est aussi ici qu'est mesurée la consommation de jetons, via
`get_usage_metadata_callback()` autour de l'appel, résumée par `resumer_consommation()`.

### `agent.py` — l'orchestrateur

Enchaîne les huit étapes et **porte à lui seul la logique de dégradation gracieuse**. Trois
fonctions méritent d'être connues :

- `analyser_insights()` — la séquence complète, seule fonction publique du module ;
- `_confiance_globale()` — le plafonnement de la confiance décrit en ⑦ ;
- `_synthese_de_repli()` — rédige une synthèse minimale **sans LLM** quand la lecture
  critique a échoué : les agrégats chiffrés restent livrés, la rédaction est assurée par le
  code ;
- `_squelette()` — produit une sortie conforme au schéma mais vide, avec
  `donnees_suffisantes = false`, quand le corpus est vide après filtrage. L'agent ne renvoie
  jamais d'erreur pour cause de corpus insuffisant : il renvoie un résultat qui dit qu'il
  est insuffisant.

### `chargement.py` — la porte d'entrée tolérante

`charger_entrees()` retourne un triplet `(entrées, comptes rendus par source, alertes)`.
Chaque source échouée devient un compte rendu avec ses avertissements, jamais une exception.
`_controler_coherence()` compare les en-têtes produit/marché des fichiers : une description
divergente est une simple alerte (celle du fichier prioritaire l'emporte), un **nom de
produit** divergent est bloquant.

### `corpus.py` — le corpus, et rien que du déterminisme

Aucun appel LLM, toutes les fonctions pures. Convertit les enregistrements de chaque
collecteur en `UniteConsommateur` ou `DocumentWeb`, applique les seuils, dédoublonne par
texte normalisé, échantillonne, et produit les `StatsCorpus` (volumes par source, taux
d'échantillonnage, période couverte, répartition de portée, langues constatées).

Détecte aussi le **mojibake** — texte doublement encodé par un collecteur amont — et le
signale dans les limites plutôt que de le masquer.

### `carte.py` — le premier étage LLM

Trois chaînes LCEL sur le modèle d'extraction :

| Chaîne | Taille de lot | Sortie structurée |
|---|---|---|
| Cartographie des unités | 15 | `LotAnalysesUnites` |
| Cartographie des documents | 4 | `LotAnalysesDocuments` |
| Normalisation des libellés | — (2 appels) | `TableNormalisation` |

Plus les fonctions de service `frequences_brutes_themes()`, `frequences_brutes_pain_points()`
(ce qui est soumis à la normalisation) et `remapper_analyses()` (application de la table).

Les lots sont traités **séquentiellement**, dans une boucle `for`.

### `reduction.py` — la source de vérité chiffrée

Aucun appel LLM. `reduire()` est la seule fonction publique. Tout ce que l'agent publie
comme nombre — répartitions de sentiment, fréquences, intensités, scores de priorité,
confiance par insight, portée dominante — sort d'ici et de nulle part ailleurs.

Sélectionne aussi les **verbatims candidats** par pain point (3 maximum, 300 caractères
maximum) que la synthèse pourra citer.

### `synthese.py` — le second étage LLM

Deux chaînes sur le modèle de synthèse, `synthetiser_insights()` et `lecture_critique()`.
Elles reçoivent les agrégats sérialisés en JSON — sentiment, thèmes, pain points avec leurs
verbatims candidats, besoins, attentes, comportements — et rendent de la prose structurée.

### `validation.py` — le filet anti-hallucination

Aucun appel LLM. `valider()` applique les trois garanties de l'étape ⑧ et retourne le
résultat corrigé accompagné des statuts et alertes décrivant chaque correction.

### `schemas.py` — les contrats

Trois familles de modèles Pydantic v2 :

1. **schémas de consommation** (`SchemaConsomme`, `EntreeReddit`, `EntreeAmazon`,
   `EntreeRechercheWeb`…) — re-déclaration minimale des seuls champs consommés, avec
   `extra="ignore"` et **aucun import du code des collecteurs**. Le couplage se fait par
   contrat JSON, jamais par dépendance de code : un collecteur peut évoluer sans casser F3
   tant que les champs listés restent présents ;
2. **modèles internes** (`UniteConsommateur`, `DocumentWeb`, `CorpusPrepare`, `Reduction`) ;
3. **sorties structurées des chaînes LLM** (`LotAnalysesUnites`, `TableNormalisation`,
   `SortieSyntheseInsights`, `SortieLectureCritique`) et le **résultat final**
   (`ResultatInsightsConsommateurs`).

### `config.py` — toutes les valeurs réglables

*« Aucune valeur magique ne doit exister ailleurs que dans ce module. »* Ne dépend d'aucun
autre module interne. Porte aussi la plomberie LLM : `construire_modele()`,
`invoquer_structure()` (avec sa reprise sur échec), `verifier_cle_api()` et
`resumer_consommation()`.

Les seuils les plus structurants :

| Constante | Valeur | Effet |
|---|---:|---|
| `MAX_UNITES_CORPUS` | 400 | Plafond au-delà duquel on échantillonne |
| `PART_MAX_PAR_SOURCE` | 0,5 | Quota d'échantillonnage par source |
| `SEUIL_PERTINENCE_AMONT` | 0,5 | Score de pertinence collecteur minimal |
| `TAILLE_LOT_UNITES` | 15 | Unités par appel de cartographie |
| `TAILLE_LOT_DOCUMENTS` | 4 | Documents par appel |
| `SEUIL_MIN_UNITES_FIABLE` | 30 | En deçà : confiance forcée à « faible » |
| `MAX_THEMES` / `MAX_PAIN_POINTS` | 12 / 15 | Plafonds de publication |
| `NB_TENTATIVES_LLM` | 2 | Une reprise après échec de validation |

> Ces seuils sont documentés dans le code comme des **heuristiques non validées
> empiriquement**, destinées à être recalibrées sur cas réels. Le score de priorité en
> particulier porte une pondération explicitement qualifiée d'arbitraire.

---

## 4. Volumétrie des appels LLM

Le nombre d'appels n'est pas fixe : il dépend du volume collecté.

```
appels = ⌈nb_unités / 15⌉  +  ⌈nb_documents / 4⌉  +  2   (extraction)
       +  2                                              (synthèse)
```

Sur le run de référence *ashwagandha-ES* (292 contributions), cela donne **26 appels
mesurés** : 24 sur le modèle d'extraction, 2 sur le modèle de synthèse. Le premier étage
concentre donc l'essentiel des appels et du volume de jetons, le second l'essentiel du coût
unitaire.

---

## 5. Dégradation gracieuse — la matrice

C'est la propriété la plus importante de l'agent en exploitation : **il ne s'arrête
presque jamais**.

| Ce qui échoue | Conséquence |
|---|---|
| Un fichier d'entrée absent ou illisible | Source écartée, avertissement tracé, analyse continue |
| Un lot de cartographie en échec | Unités du lot absentes des analyses, statut tracé, pas de rattrapage |
| Une normalisation en échec | Libellés bruts conservés, non fusionnés |
| La chaîne de synthèse en échec | Chiffres livrés sans rédaction, besoins/attentes en libellés bruts |
| La lecture critique en échec | Synthèse exécutive rédigée par le code (`_synthese_de_repli`) |
| Corpus vide après filtrage | Sortie squelette conforme, `donnees_suffisantes = false` |
| **Produits différents entre fichiers** | **Bloquant** — code de sortie 2 |
| Clé API absente avec un corpus exploitable | **Bloquant** — `RuntimeError` |

Deux cas bloquants seulement, et tous deux signalent une erreur d'usage, pas une défaillance
d'analyse.

---

## 6. Entrées et sortie

**Entrées** — les sorties JSON de trois collecteurs, toutes optionnelles mais au moins une
requise :

| Option | Collecteur | Nature |
|---|---|---|
| `--reddit` | `agent_reddit` | posts et commentaires |
| `--amazon` | `agent_amazon` | avis produits |
| `--recherche-web` | `agent_recherche_web` | pages dont `axes_servis` contient `axe1` |

**Sortie** — `insights.json` (`ResultatInsightsConsommateurs`), qui porte notamment :
`stats_corpus`, `sentiment`, `themes`, `pain_points` (avec verbatims vérifiés), `besoins`,
`attentes`, `comportements_achat`, `signaux_positifs`, `divergences_sources`,
`synthese_executive`, `confiance_globale`, `statuts_analyse`, `alertes_coherence`,
`limites` et `hypotheses`.

Les quatre derniers champs sont l'appareil d'auditabilité : ils disent ce qui a échoué, ce
qui a été corrigé, ce que l'analyse ne couvre pas, et sur quelles conventions elle repose.

**Exemple d'invocation**

```bash
cd src/agents/market_study/agent_insights_consommateurs
python main.py \
    --reddit ../../../../workdir/reddit.json \
    --amazon ../../../../workdir/amazon.json \
    --recherche-web ../../../../workdir/recherche_web.json \
    --langue-analyse fr \
    --sortie ../../../../workdir/insights.json
```

---

## 7. Ce qu'il faut retenir

1. **Le modèle ne compte jamais.** Tous les chiffres viennent de `reduction.py` et sont
   réécrits par `validation.py`. Un nombre halluciné ne peut pas atteindre le rapport.
2. **Trois fichiers sur neuf sont purement déterministes**, ce qui les rend testables sans
   clé API ni réseau.
3. **Le couplage aux collecteurs passe par le JSON, pas par le code.** `schemas.py`
   re-déclare les champs consommés en `extra="ignore"` — un collecteur peut évoluer
   librement tant que ces champs subsistent.
4. **La dégradation est la norme, l'arrêt l'exception.** Deux cas bloquants seulement,
   tous deux relevant de l'erreur d'usage.
5. **Le code a le dernier mot sur la confiance.** Le niveau proposé par le modèle est
   plafonné par le volume réel du corpus, et le déclassement est écrit dans la sortie.
