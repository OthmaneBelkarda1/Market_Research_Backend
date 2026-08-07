# `agent_analyse_concurrentielle/` — Axe 2 : analyse concurrentielle

Deuxième agent d'**analyse** du projet. Il ne collecte rien : ses entrées sont
les sorties JSON de `agent_aliexpress`, `agent_amazon`, `agent_meta_ads` et
`agent_recherche_web`. Aucun appel réseau hors API Anthropic.

Il produit l'identification consolidée des concurrents, le benchmark
prix/notes/volumes, l'intensité concurrentielle et publicitaire, les
positionnements observés et angles peu exploités, les forces et faiblesses
étayées, la différenciation du produit étudié et un tableau comparatif.

---

## 1. Place dans le pipeline

```
agent_aliexpress ────┐
agent_amazon ────────┼──►  agent_analyse_concurrentielle (F4)  ──►  output.json
agent_meta_ads ──────┤                                                    │
agent_recherche_web ─┘                                                    ▼
                                              agent_recommandations_strategiques (F5)
```

Les besoins consommateurs en général relèvent de `agent_insights_consommateurs`
(axe 1) ; les recommandations et le verdict de potentiel relèvent de l'axe 3.
Seule exception assumée ici : les avis Amazon rattachés à une offre concurrente
précise servent de preuves des forces et faiblesses **de cette offre**.

---

## 2. Usage

```bash
python main.py \
    --aliexpress ../fixtures/aliexpress.json \
    --amazon ../fixtures/amazon.json \
    --meta-ads ../fixtures/meta_ads.json \
    --recherche-web ../fixtures/recherche_web.json \
    [--prix-envisage 249.0 --devise-envisagee MAD] \
    [--langue-analyse fr] [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--aliexpress` | — | Sortie de `agent_aliexpress`. **Ce collecteur émet sur `stdout`** : le fichier est une redirection. |
| `--amazon`, `--meta-ads`, `--recherche-web` | — | Sorties natives `output.json`. |
| `--prix-envisage` / `--devise-envisagee` | — | Vont **ensemble** : l'un sans l'autre est une erreur argparse (code 2). La devise est validée par `^[A-Z]{3}$`. |
| `--sortie` | `output.json` | Chaîne vide = n'écrire aucun fichier (sous PowerShell : `--sortie=`). |
| `--stdout`, `--verbose` | absent | JSON aussi sur stdout ; logs détaillés sur `stderr`. |

**Codes de sortie** : `0` succès (y compris référentiel vide), `1` erreur
imprévue, `2` aucune entrée exploitable, produits divergents, ou usage argparse
invalide.

**Prérequis** : `ANTHROPIC_API_KEY` (voir `.env.example`). Aucun jeton Apify.

---

## 3. Les deux interdits structurants

Ils sont matérialisés dans le code, pas seulement dans les prompts.

**Aucune conversion de devise, jamais.** Un montant n'est transformé nulle part.
Un benchmark est toujours calculé pour un couple **(source, devise)** ; il
n'existe aucun chemin de code produisant une statistique inter-devises. Si
`--prix-envisage 249 --devise-envisagee MAD` est demandé alors que tous les
benchmarks sont en EUR, la sortie porte `comparaison impossible (devise)` et
s'arrête là.

**Une longévité publicitaire n'est pas une rentabilité.** `duree_diffusion_*`
mesure une durée de diffusion. Une campagne peut être diffusée longtemps à
perte ; les prompts l'interdisent explicitement et la limite est injectée
systématiquement.

### Normalisation d'étiquette de devise — ce que c'est, ce que ce n'est pas

`agent_amazon` publie un **symbole** (`€`) là où `agent_aliexpress` publie un
**code ISO** (`EUR`). Sans table de correspondance, deux benchmarks de la même
devise porteraient deux étiquettes différentes et `--devise-envisagee EUR` ne
trouverait jamais le benchmark Amazon.

`SYMBOLES_VERS_ISO` (dans `config.py`) ne fait que **renommer l'étiquette** :
aucun montant n'est modifié. Une devise absente de la table est conservée telle
quelle, sans supposition.

---

## 4. Lecture de la sortie

L'objet racine est un `ResultatAnalyseConcurrentielle`.

| Champ | Contenu |
|---|---|
| `horodatage_utc` | Date de production. **Enrichissement propre à cet agent** — F5 en a besoin pour qualifier la fraîcheur. |
| `referentiel_stats` | Offres par source, offres cœur, accessoires, annonces, pages, avis indexés, et **exclusions par motif**. |
| `concurrents` | Fiches triées par volume décroissant puis présence multi-sources. `analyse` n'est rempli que pour le top N. |
| `benchmark_prix` | Un objet **par source et par devise**. `segments` (terciles) uniquement à partir de 4 prix. |
| `position_prix_envisage` | Renseigné uniquement si une devise comparable existe. |
| `intensite_concurrentielle` | Indicateurs chiffrés + `lecture` rédigée. |
| `positionnement` | Axes, messages dominants, **angles peu exploités**, facteurs clés de succès, normes de marché. |
| `differenciation` | Attributs partagés, distinctifs potentiels, désavantages apparents. |
| `tableau_comparatif` | **Régénéré intégralement par le code** depuis les fiches validées. |
| `validite_regionale` | Portée réelle de chaque source, et ce qu'elle ne dit pas. |
| `statuts_analyse` | Un compte rendu par phase, dont la post-validation. |

### Ce qui vient du code et ce qui vient du modèle

| Produit par | Éléments |
|---|---|
| **Le code, exclusivement** | Tous les prix, médianes, dispersions, terciles, percentiles, volumes cumulés, concentrations, fourchettes, notes moyennes, longévités, comptages d'intensité, et le tableau comparatif entier. |
| **Le modèle** | Attributs lus dans les titres, claims publicitaires, rapprochements de concurrents, propositions de valeur, forces/faiblesses, niveaux de menace, positionnement, différenciation, rédactions. |

`validation.py` réécrit tout champ numérique depuis `benchmark.py` avant
publication : un nombre inventé par un modèle ne peut pas atteindre la sortie.

---

## 5. Organisation du code

Douze fichiers à plat, sens de dépendance unique :

```
config.py        → (aucune dépendance interne)
schemas.py       → config
chargement.py    → config, schemas     lecture, cohérence, portées régionales
referentiel.py   → config, schemas     offres/annonces/pages/avis — SANS LLM
extraction.py    → config, schemas     attributs + claims par lots (LLM extraction)
consolidation.py → config, schemas     rapprochement des concurrents (LLM synthèse)
benchmark.py     → config, schemas     tous les chiffres — SANS LLM
analyse.py       → config, schemas     4 chaînes qualitatives (LLM synthèse)
validation.py    → config, schemas     post-validation — SANS LLM
agent.py         → tous les précédents orchestration
main.py          → config, schemas, agent
```

---

## 6. Règles de consolidation et leur incertitude

Le rapprochement des concurrents se fait **par similarité de nom uniquement** :
aucune donnée de registre d'entreprise n'est disponible.

- `niveau_certitude_rapprochement="sur"` est réservé aux variations triviales
  (casse, accents, suffixe de boutique : « Baseus » / « BASEUS Official Store »).
- Tout le reste vaut `"probable"`, et une `AlerteCoherence` le signale.
- **Dans le doute, le prompt impose de ne pas fusionner** : deux entrées séparées
  valent mieux qu'un concurrent fabriqué.

Le code vérifie ensuite mécaniquement que chaque identifiant cité existe et
qu'aucun n'est rattaché à deux concurrents ; les offres et annonces orphelines
sont rattrapées (groupe « offres sans marque », annonceur d'origine).

**Conséquence à assumer** : une même entreprise vendant sous deux marques
restera séparée, et deux entités distinctes portant un nom voisin peuvent être
fusionnées à tort. Toute statistique agrégée hérite de cette incertitude.

---

## 7. Portées régionales : ce que chaque source décrit vraiment

C'est le piège de lecture principal de cet axe. Les quatre sources ne décrivent
pas le même plan, et `validite_regionale` le documente à chaque exécution.

| Source | Portée | Ce que ça veut dire |
|---|---|---|
| AliExpress | `region_etude` | Prix pour une livraison dans le pays demandé — dégradé en `mixte` si le pays confirmé diffère du pays demandé. |
| Amazon | `marketplace_pays` | Prix et avis du marché de la marketplace interrogée. `amazon.fr` décrit la France, pas « la région d'étude » si celle-ci est ailleurs. |
| Meta Ads | `diffusion_pays` | Annonces diffusées dans un pays, quel que soit celui de l'annonceur. **La présence publicitaire n'implique ni disponibilité produit, ni volume de vente.** |
| Recherche web | `mixte` | Portées hétérogènes ; le champ `portee_regionale` de chaque page fait foi, pas la source. |

`region_couverte=false` sur Amazon ou Meta Ads écarte proprement la source, avec
alerte, et l'analyse est livrée sur les autres.

---

## 8. Seuils et leur statut

Toutes ces valeurs sont des **heuristiques non validées**, dans `config.py`.

| Constante | Valeur | Rôle |
|---|---|---|
| `SEUIL_PERTINENCE_AMONT` | `0.5` | Sous ce score amont, l'élément est écarté ; `None` accepté. |
| `CORRESPONDANCES_COEUR_AMAZON` | `produit_equivalent`, `variante` | Cœur du benchmark. |
| `CORRESPONDANCES_COEUR_META` | `concurrent_direct`, `categorie_proche` | Idem côté annonces. |
| `TOP_N_CONCURRENTS_ANALYSES` | `8` | Concurrents analysés qualitativement (un appel chacun). |
| `TAILLE_LOT_ATTRIBUTS` / `_CLAIMS` | `12` / `10` | Tailles de lots d'extraction. |
| `MAX_AVIS_PREUVE_PAR_OFFRE` | `10` | Avis fournis comme preuves. |
| `SEUIL_MIN_OFFRES_FIABLE` | `5` | En deçà, confiance plafonnée à `faible`. |
| `MIN_PRIX_POUR_SEGMENTS` | `4` | En deçà, pas de terciles — seulement une fourchette. |
| `PART_TOP3_CONCENTRATION` | `3` | Concentration = part de volume des 3 premiers. |
| `NB_TENTATIVES_LLM` | `2` | 1 appel + 1 reprise portant l'erreur. |

> ⚠️ **Écart constaté avec la note de cadrage.** Celle-ci mentionnait les
> libellés courts `equivalent`, `concurrent`, `categorie`. Les collecteurs
> émettent en réalité `produit_equivalent`, `variante`, `concurrent_direct`,
> `categorie_proche`, `accessoire`, `hors_sujet` — vérifié dans
> `agent_amazon/config.py` et `agent_meta_ads/config.py`. Ce sont ces valeurs
> réelles qui sont implémentées ; retenir les libellés de la note aurait fait
> échouer silencieusement tout le filtrage.

---

## 9. Pièges de données constatés sur les sorties réelles

**`volume_achats_mensuel` d'Amazon est une mention textuelle par paliers**, pas
un entier : `"1K+ bought in past month"`. Le référentiel en extrait le
**plancher** du palier (1000). Conséquence publiée en limite : tout volume
cumulé est une **borne inférieure**, et les concentrations qui en découlent sont
approximatives. Un schéma déclarant `int` rejetait purement et simplement le
fichier — c'est ce qu'a montré la première exécution.

**`devise` d'Amazon est un symbole** (`€`), pas un code ISO. Voir §3.

**Encodage** : les collecteurs émettant sur `stdout` sont redirigés par le shell,
et **sous PowerShell une redirection produit de l'UTF-16 avec BOM** —
`agent_aliexpress/resultat_ma.json` en est un exemple réel. `chargement.py`
essaie `utf-8-sig`, `utf-16`, `utf-8` puis `cp1252`.

**AliExpress ne qualifie pas ses résultats** : contrairement à Amazon et Meta
Ads, ses offres ne portent aucune `correspondance`. Elles entrent donc toutes
dans le cœur du benchmark, ce qui peut y faire entrer des produits éloignés. La
limite est injectée automatiquement — **c'est le principal angle mort de cet
axe**, et il appartient au collecteur AliExpress de le combler.

---

## 10. Dégradation

| Situation | Comportement |
|---|---|
| Fichier absent, illisible, mal encodé, non conforme | Source écartée avec avertissement. Aucune exception propagée. |
| `donnees_disponibles=false` | Source écartée avec trace. |
| `region_couverte=false` (Amazon ou Meta Ads) | Source écartée + `AlerteCoherence(portee_regionale)` ; analyse livrée sur les autres. |
| Produits divergents | Erreur dédiée + **code 2**. |
| Référentiel vide | Sortie squelette, `donnees_suffisantes=false`, **code 0**. |
| < 5 offres cœur | Analyse produite, confiance plafonnée à `faible` + limite. |
| Aucune source de prix | `benchmark_prix=[]` + limite « benchmark prix impossible » ; annonces et positionnement livrés. |
| Lot d'extraction en échec | Éléments non enrichis (`None`, listes vides) + statut. Jamais bloquant. |
| Consolidation en échec | Rattrapage par le code : offres regroupées « sans marque », annonces rattachées à leur annonceur. |
| Chaîne qualitative en échec | Bloc à `None` + statut ; **les blocs chiffrés restent livrés**. |
| Synthèse en échec | Synthèse factuelle **générée par le code**. |

---

## 11. Coûts, durée et volumétrie mesurés

Exécution réelle du 05/08/2026, quatre fixtures, `--prix-envisage 249 --devise-envisagee MAD` :

| Mesure | Valeur |
|---|---|
| Référentiel | 165 offres (106 AliExpress + 59 Amazon), 6 annonces, 8 pages, 50 avis indexés |
| Concurrents consolidés | 41 |
| Appels LLM | **27** — 14 lots d'attributs + 1 lot de claims (extraction) ; 1 consolidation + 8 analyses de concurrents + 1 lecture transversale + 1 différenciation + 1 synthèse |
| Jetons `claude-haiku-4-5` | 34 804 entrée / 10 535 sortie |
| Jetons `claude-sonnet-4-5` | 63 402 entrée / 33 417 sortie |
| **Coût estimé** | **≈ 0,78 $** |
| **Durée** | **568 s** (9 min 28 s) |

Le coût est dominé par les 8 analyses par concurrent (modèle de synthèse) :
`TOP_N_CONCURRENTS_ANALYSES` est le levier direct.

Les tarifs de `config.py` sont saisis à la main : **à vérifier avant tout usage
budgétaire**.

---

## 12. Exécutions de validation réalisées

| Cas | Résultat observé |
|---|---|
| (a) Quatre sources, avec prix envisagé en MAD | **Exécution complète.** 2 benchmarks (AliExpress EUR, Amazon EUR), aucune agrégation inter-devises ; `position_prix_envisage` porte `comparaison impossible (devise)`. |
| (b) Amazon `region_couverte=false` | Source écartée, alerte `portee_regionale`, benchmark réduit à AliExpress, annonces et pages livrées. |
| (c) `--prix-envisage` dans une devise absente | Couvert par (a). |
| (d) AliExpress seul | Consolidation LLM absente → le rattrapage code regroupe les 106 offres sous « Offres marketplace sans marque identifiable ». |
| (e) Aucune source de prix (Meta + web) | `benchmark_prix=[]`, blocs publicitaires livrés, confiance `faible`. |
| (f) `--prix-envisage` sans `--devise-envisagee` | Erreur argparse explicite, **code 2**. |
| (g) `--devise-envisagee mad` (minuscules) | Erreur argparse explicite, **code 2**. |
| (h) Produits différents | Erreur nommant les deux produits, **code 2**. |
| (i) Référentiel vide, source disponible | Sortie squelette, `donnees_suffisantes=false`, **code 0**. |

Les cas (b) à (e) ont été validés sur les modules déterministes
(`chargement` → `referentiel` → `benchmark`), là où se joue toute la
dégradation : les phases LLM en aval sont strictement identiques à celles du
cas (a), déjà exécuté de bout en bout.

### Ce que la post-validation a corrigé sur le run (a)

- 35 preuves citant un identifiant inexistant, retirées ;
- 72 extraits absents de leur texte source, remplacés par le début réel ;
- 15 constats déclarés « fait » sans preuve valide, rétrogradés en « hypothese » ;
- 5 constats restés sans aucune preuve.

Ces chiffres ne sont pas un dysfonctionnement : ils mesurent précisément ce que
le garde-fou intercepte. Un modèle qui reformule un extrait au lieu de le
recopier produit une citation invérifiable — elle est corrigée et tracée.

---

## 13. Limites méthodologiques

Injectées systématiquement :

- Corpus **non exhaustif**, héritant des biais de collecte amont. **Aucune part
  de marché** n'en découle.
- Rapprochements de concurrents fondés sur la seule similarité de nom (§6).
- **Portées régionales hétérogènes** entre sources, non superposables (§7).
- **Longévité publicitaire ≠ rentabilité.**
- Classification des correspondances et extraction des claims produites par des
  modèles de langage, non validées empiriquement.

---

## 14. Choix de modèles

| Étape | Modèle | Pourquoi |
|---|---|---|
| Attributs, claims | `claude-haiku-4-5-20251001` | Extraction mécanique par lots. |
| Consolidation, analyse, différenciation, synthèse | `claude-sonnet-4-5-20250929` | Jugement et rédaction. |

Identifiants **vérifiés disponibles le 05/08/2026**. La génération courante
`claude-sonnet-5` rejette toute `temperature` non par défaut (erreur 400), ce
qui est incompatible avec l'exigence de température 0 : le modèle spécifié est
conservé.

---

## 15. Ce que ce module ne fait pas

Aucune collecte, aucune conversion de devise, aucune persistance hors
`--sortie`, aucun serveur ni interface, aucune suite de tests automatisés.
Aucune analyse des besoins consommateurs en général (rôle de F3), aucune
recommandation ni verdict (rôle de F5), aucune classification de cycle de vie.
