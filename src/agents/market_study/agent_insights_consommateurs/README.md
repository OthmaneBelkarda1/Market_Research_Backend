# `agent_insights_consommateurs/` — Axe 1 : insights consommateurs

Premier agent d'**analyse** du projet. Il ne collecte rien : ses entrées sont
les fichiers JSON produits par `agent_reddit`, `agent_amazon` et
`agent_recherche_web`. Il n'appelle aucune API hors celle d'Anthropic.

À partir de ces corpus, il dégage le **sentiment**, les **thèmes récurrents**,
les **pain points hiérarchisés**, les **besoins et attentes**, les
**comportements d'achat** et les **signaux positifs** — chaque insight restant
traçable jusqu'aux verbatims sources.

---

## 1. Place dans le pipeline

```
agent_reddit ─────────┐
agent_amazon ─────────┼──►  agent_insights_consommateurs (F3)  ──►  output.json
agent_recherche_web ──┘                                                   │
                                                                          ▼
                                              agent_recommandations_strategiques (F5)
```

La sortie est consommée par l'agent de recommandations (axe 3). L'analyse
concurrentielle (axe 2) est le rôle de `agent_analyse_concurrentielle`, et la
classification de cycle de vie celui d'un futur module : rien de tout cela
n'est produit ici.

---

## 2. Usage

```bash
python main.py \
    --reddit ../fixtures/reddit.json \
    --amazon ../fixtures/amazon.json \
    --recherche-web ../fixtures/recherche_web.json \
    [--langue-analyse fr] [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--reddit` | — | Sortie de `agent_reddit`. **Ce collecteur émet sur `stdout`** : le fichier est une redirection (`python main.py … > sortie_reddit.json`). |
| `--amazon` | — | Sortie de `agent_amazon` (`output.json` natif). |
| `--recherche-web` | — | Sortie de `agent_recherche_web` (`output.json` natif). |
| `--langue-analyse` | `fr` | Langue de rédaction de l'analyse. |
| `--sortie` | `output.json` | Fichier écrasé à chaque exécution ; **chaîne vide = n'écrire aucun fichier** (sous PowerShell, écrire `--sortie=`). |
| `--stdout` | absent | Émet aussi le JSON sur la sortie standard. |
| `--verbose` | absent | Journalisation détaillée — sur `stderr` uniquement. |

Les trois chemins sont optionnels, mais au moins un doit être fourni.

**Codes de sortie** : `0` succès (y compris corpus vide mais source disponible),
`1` erreur imprévue, `2` aucun fichier fourni ou exploitable, ou produits
divergents entre fichiers.

`stdout` reste du **JSON pur** : progression, avertissements et récapitulatif de
consommation partent tous sur `stderr`.

**Prérequis** : `ANTHROPIC_API_KEY` (voir `.env.example`). Aucun jeton Apify.

---

## 3. Lecture de la sortie

L'objet racine est un `ResultatInsightsConsommateurs`.

| Champ | Contenu |
|---|---|
| `produit`, `marche` | En-tête repris du **premier fichier valide** (ordre Reddit → Amazon → web). |
| `horodatage_utc` | Date de production de l'analyse. **Enrichissement propre à cet agent** : aucun contrat amont n'en porte, et F5 en a besoin pour qualifier la fraîcheur. |
| `sources_utilisees` | Par source : fichier, disponibilité, items chargés, items exploités, avertissements. |
| `alertes_coherence` | Divergences non bloquantes (`produit_divergent`, `marche_divergent`, `portee_regionale`, `preuve_manquante`, `insight_non_ancre`). |
| `stats_corpus` | Volumes par source, taux d'échantillonnage, période couverte, répartition de portée, langues constatées. |
| `sentiment` | Répartitions globale, par source et par portée, plus un commentaire rédigé. |
| `themes` | Thèmes récurrents chiffrés. |
| `pain_points` | **Triés par `score_priorite` décroissant**, avec sources, portée, verbatims vérifiés et confiance. |
| `besoins`, `attentes` | Structurés (`type`, `niveau_exigence`) et adossés à des `preuves_id`. |
| `comportements_achat` | Critères de choix, freins, déclencheurs, occasions d'usage, sensibilité au prix. |
| `signaux_positifs` | Ce que les consommateurs louent explicitement. |
| `divergences_sources` | Écarts factuels entre sources ou entre portées. |
| `synthese_executive` | ≤ 12 lignes, structure imposée. |
| `statuts_analyse` | Un compte rendu par phase, dont la post-validation. |
| `donnees_suffisantes`, `confiance_globale`, `limites`, `hypotheses` | Appareil critique. |

### Ce qui vient du code et ce qui vient du modèle

C'est la garantie centrale de ce module :

| Produit par | Éléments |
|---|---|
| **Le code, exclusivement** | Toutes les fréquences, tous les pourcentages, les intensités moyennes, les scores de priorité, les répartitions de sentiment, les portées, les niveaux de confiance des insights, la sélection des verbatims candidats, le niveau de `confiance_globale`. |
| **Le modèle** | Les libellés, les descriptions rédigées, les regroupements de libellés, le typage des besoins, le niveau d'exigence des attentes, les divergences relevées, la synthèse exécutive. |

`validation.py` **réécrit systématiquement** tout champ numérique depuis
`reduction.py` avant publication : un nombre inventé par un modèle ne peut pas
survivre jusqu'à la sortie.

---

## 4. Organisation du code

Onze fichiers à plat, sans sous-package ni `__init__.py`. Sens des dépendances
unique, aucun import circulaire :

```
config.py      → (aucune dépendance interne)
schemas.py     → config
chargement.py  → config, schemas      lecture, validation tolérante, cohérence
corpus.py      → config, schemas      unités, filtrage, échantillonnage — SANS LLM
carte.py       → config, schemas      extraction par lots + normalisation (LLM)
reduction.py   → config, schemas      tous les agrégats chiffrés — SANS LLM
synthese.py    → config, schemas      rédaction et lecture critique (LLM)
validation.py  → config, schemas      post-validation — SANS LLM
agent.py       → tous les précédents  orchestration
main.py        → config, schemas, agent
```

`config.py` porte aussi la plomberie LLM partagée (fabrique de modèle, reprise
sur échec, comptage de jetons). C'est le seul point commun autorisé entre
`carte.py` et `synthese.py` sans créer d'import croisé.

---

## 5. Seuils, formules et leur statut

**Toutes les valeurs ci-dessous sont des heuristiques non validées
empiriquement.** Elles vivent dans `config.py` et sont destinées à être
recalibrées sur des cas réels.

| Constante | Valeur | Rôle |
|---|---|---|
| `SEUIL_PERTINENCE_AMONT` | `0.5` | Sous ce score collecteur, l'unité est écartée. `None` est accepté : l'absence de score n'est pas une preuve de non-pertinence. |
| `MAX_UNITES_CORPUS` | `400` | Plafond d'unités soumises au LLM. |
| `PART_MAX_PAR_SOURCE` | `0.5` | Part maximale d'une source — **inappliquée si une seule source existe**. |
| `MAX_CARACTERES_UNITE` | `1200` | Troncature d'une unité. |
| `MAX_DOCUMENTS_WEB` / `MAX_CARACTERES_DOCUMENT` | `20` / `6000` | Bornes des documents web. |
| `TAILLE_LOT_UNITES` / `TAILLE_LOT_DOCUMENTS` | `15` / `4` | Tailles de lots LLM. |
| `MAX_THEMES` / `MAX_PAIN_POINTS` | `12` / `15` | Plafonds après normalisation. |
| `SEUIL_MIN_UNITES_FIABLE` | `30` | En deçà, `confiance_globale` est plafonnée à `faible`. |
| `SEUIL_CONFIANCE_ELEVEE_NB` / `_MOYENNE_NB` | `10` / `4` | Assise minimale d'un insight ; « élevée » exige en plus ≥ 2 sources. |
| `SEUIL_PORTEE_DOMINANTE` | `0.70` | Un insight est régional ou global à partir de 70 % d'unités d'une même portée, sinon « mixte ». |
| `COEFFICIENT_MULTI_SOURCE` | `0.25` | Bonus par source supplémentaire dans le score de priorité. |
| `NB_TENTATIVES_LLM` | `2` | 1 appel + 1 reprise portant le message d'erreur. |

**Formule de priorité** (hypothèse de travail, recopiée dans `hypotheses`) :

```
score_priorite = frequence_pct × intensite_moyenne × (1 + 0.25 × (nb_sources − 1))
```

`frequence_pct` est calculé sur la base des unités **porteuses d'une opinion
applicable** (sentiment ≠ `non_applicable`), pas sur le corpus entier. Cette
base est publiée telle quelle dans `sentiment.global.base_nb`.

### Convention de comptage assumée

Les fréquences portent sur les **unités consommateurs** (posts, commentaires,
avis). Une page web atteste qu'un thème existe dans le discours éditorial : elle
entre donc dans les `sources` d'un insight, mais **pas** dans sa fréquence — une
page n'est pas une opinion individuelle, et la compter comme telle gonflerait
artificiellement le poids d'un rédacteur unique.

---

## 6. Dégradation : ce que fait l'agent quand il manque quelque chose

| Situation | Comportement |
|---|---|
| Fichier absent, illisible, mal encodé ou non conforme | Source écartée avec avertissement dans `sources_utilisees`. **Aucune exception n'est propagée pour une source.** |
| `donnees_disponibles=false` | Source écartée avec trace explicite. |
| Aucune source exploitable | `stderr` explicite + **code 2**. |
| Produits divergents entre deux fichiers | Erreur dédiée + **code 2** (mélange d'études interdit). |
| Marchés (`geo`) divergents | `AlerteCoherence(marche_divergent)`, traitement poursuivi. |
| Corpus vide après filtrage, mais source disponible | Sortie squelette valide (`sentiment=null`, listes vides), `donnees_suffisantes=false`, **code 0**. |
| Corpus < 30 unités | Analyse complète, `confiance_globale=faible` + limite explicite. |
| Une seule source | Limite explicite sur la représentativité + confiance plafonnée à `faible`. |
| Un lot de cartographie en échec après reprise | Lot écarté, unités non analysées, statut tracé. **Pas de boucle de rattrapage.** |
| Normalisation de libellés en échec | Libellés bruts conservés sans regroupement + statut. |
| Chaîne de synthèse en échec | Agrégats chiffrés livrés quand même ; `synthese_executive` minimale **générée par le code**. |

---

## 7. Encodage des fichiers d'entrée — piège constaté

Les collecteurs qui émettent sur `stdout` sont redirigés vers un fichier par le
shell. **Sous PowerShell, cette redirection produit de l'UTF-16 avec BOM**, pas
de l'UTF-8 : `agent_aliexpress/resultat_ma.json` en est un exemple réel dans ce
dépôt. `chargement.py` essaie donc successivement `utf-8-sig`, `utf-16`,
`utf-8` et `cp1252` avant d'abandonner une source.

Second constat, dans les données elles-mêmes : la sortie réelle de
`agent_reddit` contient du **texte doublement encodé** (`ΓÇô` pour un tiret
cadratin, `Ã©` pour `é`) sur environ 25 % de ses unités. L'agent le détecte, ne
le confond pas avec une écriture étrangère, et l'inscrit en limite — la
classification de ces unités est dégradée d'autant. **Le correctif appartient au
collecteur, pas à l'analyse.**

---

## 8. Coûts, durée et volumétrie mesurés

Exécution réelle du 05/08/2026 sur les trois fixtures du dépôt
(`../fixtures/reddit.json`, `amazon.json`, `recherche_web.json`) :

| Mesure | Valeur |
|---|---|
| Corpus | 302 unités brutes → 292 éligibles → **292 analysées** ; 5 documents web |
| Appels LLM | **26** — 20 lots d'unités + 2 lots de documents + 2 normalisations (extraction), 2 chaînes de synthèse |
| Jetons `claude-haiku-4-5` | 108 780 entrée / 35 528 sortie |
| Jetons `claude-sonnet-4-5` | 19 361 entrée / 6 838 sortie |
| **Coût estimé** | **≈ 0,45 $** |
| **Durée** | **312 s** (5 min 12 s) |

Le récapitulatif est émis sur `stderr` à chaque exécution. Les tarifs employés
(`TARIFS_USD_PAR_MTOK` dans `config.py`) sont saisis à la main et ne sont pas
interrogés en ligne : **à vérifier avant tout usage budgétaire.**

Le coût est dominé par la phase d'extraction, elle-même proportionnelle au
nombre d'unités. Abaisser `MAX_UNITES_CORPUS` est le levier direct.

Exécution « Reddit seul » (242 unités, 1 source) : 152 s.

---

## 9. Exécutions de validation réalisées

| Cas | Résultat observé |
|---|---|
| (a) Trois sources | 292 unités, 12 thèmes, 15 pain points, 7 besoins, 7 attentes, 7 signaux positifs. **Post-validation silencieuse** : aucune référence retirée, aucun extrait corrigé, aucune alerte — le run est sain. |
| (b) Reddit seul | Le corpus Reddit du dépôt porte sur un autre produit : le modèle a classé **les 242 unités en `non_applicable`**, `base_nb=0`, aucun insight fabriqué, `donnees_suffisantes=false`. C'est le comportement attendu du protocole anti-hallucination. |
| (c) `donnees_disponibles=false` | Source écartée proprement, message explicite, **code 2**. |
| (d) Produits différents entre deux fichiers | Erreur dédiée nommant les deux produits, **code 2**. |
| (e) Aucun fichier fourni | Message d'usage, **code 2**. |
| (f) Fichier inexistant | Source écartée avec avertissement, **code 2** (aucune autre source). |
| (g) Source disponible mais sans avis | Sortie squelette, `donnees_suffisantes=false`, **code 0**, aucune exception. |
| (h) Échantillonnage (plafond abaissé à 60) | Quotas respectés : 30 Reddit / 30 Amazon, taux consigné, limite explicite. Source unique : plafond de part non appliqué. |

---

## 10. Limites méthodologiques

Injectées systématiquement dans la sortie :

- Le corpus n'est **pas exhaustif** et hérite des biais de collecte amont. Il ne
  constitue **en aucun cas** un échantillon représentatif d'un marché.
- La classification (sentiment, thèmes, pain points) est produite par un modèle
  de langage et **n'a pas été validée** contre un codage humain.
- Les populations diffèrent radicalement entre sources : contributeurs Reddit,
  acheteurs Amazon, rédacteurs web. Les agrégats inter-sources mélangent des
  publics non comparables.
- **Aucune inférence sur la taille du marché** n'est possible : un nombre
  d'unités mesure une activité de discussion collectée, pas une demande.

S'y ajoutent, selon les cas : l'échantillonnage appliqué, le mono-sourcing, le
volume sous le seuil de fiabilité, le mojibake amont et les limites reportées
par chaque collecteur (préfixées `[reddit]`, `[amazon]`, `[recherche_web]`).

---

## 11. Choix de modèles

| Étape | Modèle | Pourquoi |
|---|---|---|
| Cartographie par lots, normalisation | `claude-haiku-4-5-20251001` | Tâches mécaniques, fort volume. |
| Synthèse, lecture critique | `claude-sonnet-4-5-20250929` | Hiérarchisation et rédaction. |

Les deux identifiants ont été **vérifiés disponibles le 05/08/2026**.

`claude-sonnet-4-5-20250929` est un modèle « legacy actif ». La génération
courante est `claude-sonnet-5`, mais elle **rejette toute valeur de
`temperature` non par défaut** (erreur 400) : elle est incompatible avec
l'exigence de température 0 de la spécification. Le modèle spécifié est donc
conservé ; migrer supposerait d'abandonner `temperature=0` et de revalider la
stabilité des sorties.

---

## 12. Ce que ce module ne fait pas

Aucune collecte, aucune persistance hors `--sortie`, aucun serveur ni interface,
aucune suite de tests automatisés. Aucune analyse concurrentielle (rôle de F4),
aucune recommandation ni verdict de potentiel (rôle de F5), aucune
classification de cycle de vie.
