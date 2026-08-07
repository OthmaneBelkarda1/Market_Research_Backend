# Agents du projet — entrées et sorties

## Le système

Ce dépôt est une **chaîne d'étude de marché e-commerce**. On lui donne une
**fiche produit** (nom, description, catégorie) et une **région d'étude** ; il
rend un **rapport d'étude de marché en Markdown**, assorti d'un verdict de
potentiel commercial argumenté, de recommandations priorisées et de l'inventaire
explicite de ce qu'il ne sait pas.

Onze modules Python autonomes, chacun exécutable seul en ligne de commande,
répartis en deux étages.

**Six agents de collecte** (§1 à §7) : une fiche produit + une région en entrée,
un **objet JSON unique** en sortie. Chacun interroge une source différente et ne
fait que collecter, normaliser et qualifier — **aucun n'analyse ni ne conclut**.

**Cinq agents d'analyse et de restitution** (§9) : ils ne collectent rien et
n'appellent que l'API Anthropic. Ils consomment les fichiers JSON produits en
amont et produisent les insights consommateurs, l'analyse concurrentielle, le
verdict de potentiel assorti de ses recommandations, la phase de cycle de vie du
marché, et enfin le rapport d'étude.

```
        collecte                          analyse                    restitution
reddit ─────────┐
amazon ─────────┼──► agent_insights_consommateurs (F3) ─┐
recherche_web ──┘                                       │
                                                        ├──► agent_recommandations_
aliexpress ─────┐                                       │     strategiques (F5) ──┐
amazon ─────────┼──► agent_analyse_concurrentielle (F4) ─┤            │            │
meta_ads ───────┤                                       │   declenche_plc         │
recherche_web ──┘                                       │            ▼            ▼
                                                        │      agent_plc (F6) ─► agent_
tendances ──────────────────────────────────────────────┘                        restitution (F7)
                                                                                     │
                                                                     rapport_etude.md │
                                                                  resume_executif.md ─┘
```

Le couplage entre étages est un **contrat JSON**, jamais un import de code :
chaque agent d'analyse re-déclare en Pydantic les seuls champs qu'il consomme,
en `extra="ignore"`. On peut donc remplacer, rejouer ou déboguer n'importe quel
maillon sans toucher aux autres.

### Ce qui traverse le système

| Étage | Ce qui circule | Qui le produit |
|---|---|---|
| Collecte | Corpus bruts normalisés : messages, avis, pages, offres, annonces, séries d'indices | Actors Apify + API AliExpress officielle, plus un LLM pour planifier les requêtes et classer la pertinence |
| Analyse | Constats chiffrés, référencés, typés `fait` / `hypothese` | Du code déterministe pour les nombres ; un LLM pour la lecture qualitative |
| Décision | Une grille de 5 critères notée, puis un **verdict calculé par une règle de code** | Le modèle note, le code décide |
| Restitution | Un rapport de 9 sections + un résumé d'une page | Du code pour les tableaux et les chiffres ; un LLM pour les transitions |

### Les cinq principes de conception

1. **Le modèle propose, le code décide.** Le verdict de potentiel (F5) et la
   phase de cycle de vie (F6) sont calculés par des fonctions pures, rejouables
   à l'identique. Le modèle note ou oriente, il ne conclut jamais.
2. **Aucun nombre produit par un modèle n'atteint une sortie.** Tous les chiffres
   sont calculés par du code, puis **réécrits** par une post-validation. F7 va
   plus loin : chaque nombre du rapport est confronté à une liste blanche
   construite depuis les entrées, et une phrase portant un nombre inconnu est
   retirée.
3. **Toute affirmation étayée est traçable.** Chaque fondement cite une `ref`
   vérifiée ; une référence inventée est retirée et tracée, jamais laissée en
   place. Une recommandation privée de tout fait est marquée « non ancrée »,
   jamais supprimée en silence.
4. **Ce qui n'est pas mesuré est déclaré.** Chaque module publie ses `limites` et
   ses `hypotheses`, et les propage à l'étage suivant sans les réécrire. Elles
   atterrissent verbatim dans l'annexe du rapport final.
5. **Aucune agrégation abusive.** Pas de conversion de devises, pas de moyenne
   entre langues, entre régions ou entre plateformes. Deux prix libellés dans
   deux devises décrivent deux marchés ; deux corpus de langues différentes
   décrivent deux segments.

### Ce que le système ne prétend pas faire

Il ne mesure **ni part de marché, ni volume de demande, ni taille de marché** :
ses corpus sont des collectes, pas des échantillons représentatifs. Il ne produit
**aucun calcul de rentabilité** — aucune donnée financière interne n'y entre, et
les prix cités sont des positionnements observés. Enfin, la **règle de verdict**
et la **grille de phases** sont des hypothèses de travail explicitement non
validées : ni le cahier des charges ni la spécification fonctionnelle ne
définissent le « potentiel commercial ». Chaque sortie concernée porte
`statut_regle="hypothese_de_travail_a_valider"`.

### Invariants communs à tous les modules

| Point | Règle |
|---|---|
| Sortie standard | **`stdout` = JSON pur**, `stderr` = progression. `--verbose` active les logs |
| Encodage | UTF-8 forcé en sortie ; en entrée, détection automatique `utf-8-sig` / `utf-16` / `utf-8` / `cp1252` — une redirection PowerShell produit de l'UTF-16 |
| Organisation | Un dossier par module, fichiers **à plat**, pas de sous-package, `config.py` seul détenteur des valeurs de réglage |
| Codes de sortie | `0` succès, `1` erreur d'exécution, `2` entrée inexploitable ou usage, `3` région non couverte (collecteurs) |
| Résultat défavorable | **Jamais une erreur** : verdict négatif, phase non classée ou non-déclenchement sortent en code 0. Un orchestrateur lit le JSON, pas le code de sortie |
| Dégradation | Une entrée manquante dégrade et se signale ; elle n'interrompt jamais la chaîne, sauf entrée strictement requise |

### Une étude complète, de bout en bout

**Une seule commande**, depuis la racine du dépôt, environnement virtuel activé :

```powershell
.\.venv\Scripts\Activate.ps1

.\etude_marche.ps1 -Nom "Ashwagandha Supplement" `
    -Description "Complément alimentaire à base d'Ashwagandha, gestion du stress" `
    -Categorie "health" -Geo ES
```

Ni la langue ni la devise ne sont demandées : les deux se déduisent du code pays
par table déterministe, sans aucun appel LLM —
[langues_marche.py](langues_marche.py) sert la langue principale du pays,
[devise_marche.py](devise_marche.py) sa monnaie. `-Langue`, `-Langues` et
`-Devise` restent disponibles et priment. Une seule langue par défaut : un marché
multilingue ne déclenche plus deux études sans qu'on le demande.

[etude_marche.ps1](etude_marche.ps1) enchaîne **les 11 étapes** : détection de la
langue d'étude, les six collecteurs, F3, F4, F5, puis F6 et F7. Tout est déposé
dans `etudes\<produit>-<GEO>\<langue>\`, un sous-répertoire par langue, et
l'étude se termine sur `rapport_etude.md` et `resume_executif.md`.

Aucun collecteur en échec n'interrompt l'étude : le manque est signalé et les
agents d'analyse le traitent comme une entrée absente. F6 décide seul de classer
ou non d'après le verdict ; le drapeau `--forcer` n'est délibérément **pas**
exposé par l'orchestrateur.

**Coût et durée de l'étage d'analyse**, mesurés sur le run *ashwagandha-ES*
(292 contributions, 165 offres) :

| Module | Appels LLM | Coût estimé | Durée |
|---|---|---|---|
| F3 — insights consommateurs | 26 | ≈ 0,45 $ | 312 s |
| F4 — analyse concurrentielle | 27 | ≈ 0,78 $ | 568 s |
| F5 — verdict et recommandations | 6 | ≈ 0,71 $ | 503 s |
| F6 — phase de cycle de vie | 2 (0 si non déclenché) | ≈ 0,14 $ | 126 s |
| F7 — rapport final | 6 | ≈ 0,10 $ | 80 s |
| **Total** | **67** | **≈ 2,18 $** | **≈ 26 min** |

Le coût de l'**étage de collecte** dépend d'Apify et non de l'API Anthropic ; ses
deux principaux leviers sont le nombre d'annonces Meta demandées (l'actor est
facturé **à l'annonce**) et le nombre de produits Amazon enrichis d'avis (**un
run d'actor par produit**). Un compte Apify en fin de quota tronque les runs en
les marquant `SUCCEEDED` : vérifier le quota avant toute mesure de volume.

---

## 0. Les modules

| Module | Source | Ce qu'il rapporte | Sortie |
|---|---|---|---|
| [agent_tendances/](agent_tendances/) | Google Trends (Apify) | Indicateurs de tendance, saisonnalité, momentum | stdout |
| [agent_reddit/](agent_reddit/) | Reddit (Apify) | Corpus de posts + commentaires anonymisés | stdout |
| [agent_recherche_web/](agent_recherche_web/) | Google/SERP + crawl (Apify) | Corpus de pages web sur 2 axes | `output.json` (+ `--stdout`) |
| [agent_aliexpress/](agent_aliexpress/) | API officielle AliExpress DS | Produits et prix par SKU, régionalisés | stdout |
| [agent_amazon/](agent_amazon/) | Amazon (Apify) | Produits concurrents + avis clients | `output.json` (+ `--stdout`) |
| [agent_meta_ads/](agent_meta_ads/) | Meta Ad Library (Apify) | Annonces publicitaires concurrentes | `output.json` (+ `--stdout`) |

| Module d'analyse | Entrées | Ce qu'il produit | Sortie |
|---|---|---|---|
| [agent_insights_consommateurs/](agent_insights_consommateurs/) | reddit, amazon, recherche_web | Sentiment, thèmes, pain points hiérarchisés, besoins, comportements d'achat | `output.json` (+ `--stdout`) |
| [agent_analyse_concurrentielle/](agent_analyse_concurrentielle/) | aliexpress, amazon, meta_ads, recherche_web | Concurrents consolidés, benchmark par devise, intensité, positionnement, différenciation | `output.json` (+ `--stdout`) |
| [agent_recommandations_strategiques/](agent_recommandations_strategiques/) | sorties de F3, F4 et tendances | Diagnostic croisé, **verdict de potentiel**, recommandations, opportunités, risques | `output.json` (+ `--stdout`) |
| [agent_plc/](agent_plc/) | sortie de F5 (requise), F3 et F4 | **Phase de cycle de vie** du marché et recommandations propres à cette phase — **uniquement si le verdict est positif** | `output.json` (+ `--stdout`) |
| [agent_restitution/](agent_restitution/) | sortie de F5 (requise), F3, F4 et F6 | **Rapport d'étude de marché** en Markdown + résumé exécutif | `rapport_etude.md`, `resume_executif.md`, `output.json` |

| Outil d'orchestration | Entrée | Ce qu'il produit | Sortie |
|---|---|---|---|
| [langues_marche.py](langues_marche.py) | un code pays | Langue d'étude du marché, par table déterministe (voir §8) | stdout |
| [devise_marche.py](devise_marche.py) | un code pays | Devise d'étude du marché, par table déterministe (voir §8) | stdout |

---

## 1. Le contrat commun

### Entrée partagée

Les six modules acceptent le **même objet d'entrée**, précisément pour qu'un
orchestrateur amont puisse tous les alimenter sans transformation.

**`FicheProduit`**

| Champ | Type | Obligatoire | Rôle |
|---|---|---|---|
| `nom` | `str` | oui | Titre commercial du produit |
| `description` | `str` | oui | Description libre |
| `categorie` | `str \| None` | **oui sur `agent_tendances`**, optionnel ailleurs | Catégorie e-commerce |

**`ParametresMarche`**

| Champ | Type | Rôle |
|---|---|---|
| `geo` | `str` | Région d'étude — format variable selon le module, voir ci-dessous |
| `langue` | `str` | Code langue ISO-2 minuscule (`fr`, `en`…). **Se dérive du marché, ne se suppose pas** — voir §8 |
| `devise` | `str` | **`agent_aliexpress` uniquement** — ISO-4217 (`MAD`, `EUR`…) |

Le format accepté par `geo` **n'est pas uniforme** :

| Module | `geo` accepté |
|---|---|
| tendances, reddit, recherche_web | Code ISO-2 strict (`FR`, `MA`) |
| aliexpress | Code ISO-2 strict, validé par regex `^[A-Z]{2}$` |
| amazon | ISO-2 **ou** texte libre (`Maroc`, `Lyon`) — résolu par le LLM |
| meta_ads | ISO-2, texte libre, **ou `ALL`** (tous pays) |

### Sortie partagée

Tous les résultats portent le même socle :

| Champ | Type | Rôle |
|---|---|---|
| `produit` | `FicheProduit` | Rappel de l'entrée |
| `marche` | `ParametresMarche` | Rappel de la région |
| `alertes_qualite_input` | `list[{type, detail}]` | Anomalies détectées dans la fiche — **signalées, jamais corrigées** (`contradiction`, `langue_inattendue`, `description_insuffisante`, `autre`) |
| `statuts_collecte` | `list[…]` | Un compte rendu par run/appel : succès, message d'erreur, nb d'items, nb de tentatives |
| `donnees_disponibles` | `bool` | Faux si la collecte n'a rien rapporté |
| `limites` | `list[str]` | Limites méthodologiques du corpus livré |
| `hypotheses` | `list[str]` | Hypothèses assumées par la collecte |

### Conventions communes

- **`stdout` = JSON pur**, `stderr` = progression. `--verbose` active les logs.
- Toutes les chaînes LLM tournent sur **`claude-haiku-4-5-20251001`**, température 0.
- Tous les modules Apify acceptent `APIFY_TOKEN` ou, en repli, `APIFY_API_TOKEN`.
- Encodage de sortie forcé en UTF-8 (le défaut Windows cp1252 corromprait les accents).

---

## 2. `agent_tendances/` — Google Trends

Dérive un mot-clé de recherche à partir de la fiche, interroge Google Trends sur
deux horizons (5 ans et 12 mois) et calcule des indicateurs quantitatifs.

**Prérequis** : `ANTHROPIC_API_KEY`, `APIFY_TOKEN`.
Dépendances supplémentaires : `numpy`, `pandas`.

**Entrée CLI** — les 5 arguments sont obligatoires :

```bash
python main.py \
    --nom "JBL Endurance Peak 4 Open Ear" \
    --description "Écouteurs à conduction d'air, crochets d'oreille…" \
    --categorie "electronics" \
    --geo FR --langue fr [--verbose]
```

> `--categorie` est **obligatoire ici**, contrairement aux autres modules.

**Sortie** — `ResultatTendances`, sur `stdout` :

| Champ | Contenu |
|---|---|
| `mots_cles` | `JeuMotsCles` : terme pivot, attribut différenciant, termes de repli, `niveau_repli`, `fallback_applique` |
| `indicateurs` | `indice_moyen_12m`, `profil_mensuel_12m` (clés `AAAA-MM`), `momentum_90j`, `pente_annuelle_5ans`, `volatilite`, `saisonnalite` (indice par mois, pic, creux, amplitude), `nb_breakout`, `concentration_geo` (top 5 zones), `signal_effet_de_mode`, `profil_courbe` |
| `requetes_emergentes` | Requêtes associées en forte progression + flag `est_breakout` |
| `sujets_associes` | Liste de sujets liés |
| `statuts_collecte` | Un par horizon (`5y` / `12m`) |

`profil_courbe` ∈ `effet_de_mode`, `emergent`, `croissance`, `maturite`,
`declin`, `indetermine`.

**Spécificités**
- Actor `data_xplorer/google-trends-fast-scraper`, mode `keyword`, proxy résidentiel.
- Collectes **séquentielles**, 20 s de pause entre appels.
- Jusqu'à **2 replis** de mot-clé si le terme pivot ne rapporte rien d'exploitable.
- Codes de sortie : `0` succès, `1` erreur bloquante (config ou LLM).

---

## 3. `agent_reddit/` — discussions Reddit

Construit une stratégie de recherche (requêtes marché + globales, subreddits
régionaux + thématiques), collecte les posts, les score par pertinence, puis
n'approfondit les commentaires que sur les meilleurs.

**Prérequis** : `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, et **`SEL_ANONYMISATION`**
(sel de hachage des pseudonymes ; sans lui, un sel public de repli rend les
empreintes réversibles par force brute).

**Entrée CLI**

```bash
python main.py \
    --nom "…" --description "…" [--categorie "electronics"] \
    --geo FR --langue fr [--verbose]
```

**Sortie** — `ResultatCollecteReddit`, sur `stdout` :

| Champ | Contenu |
|---|---|
| `strategie` | `requetes_marche`, `requetes_globales`, `subreddits_regionaux`, `subreddits_thematiques`, `justification` |
| `posts` | `id`, `titre`, `texte`, `subreddit`, `url`, `date_creation`, `score`, `nb_commentaires`, `portee` (régionale/globale), `origine`, `pertinence` (0–1), `auteur_pseudonymise`, `requete_source` |
| `commentaires` | `id`, `id_post`, `texte`, `date_creation`, `score`, `profondeur`, `auteur_pseudonymise` |
| `stats` | Posts collectés / retenus / approfondis, nb commentaires, répartition par subreddit et par portée, dates extrêmes |
| `statuts_collecte` | Par phase : `prospection_globale`, `prospection_subreddit`, `commentaires` |

**Spécificités**
- Actor `harshmaur/reddit-scraper`. **Collecte en 2 phases** pour contenir le coût
  des commentaires : prospection large, puis approfondissement des seuls posts retenus.
- Plafonds : 6 requêtes, 3 subreddits ciblés, 100 posts en recherche globale,
  30 posts par subreddit, **15 posts approfondis**, 25 commentaires par post.
- Seuil de pertinence 0,5 ; scoring LLM par lots de 20.
- **Aucun pseudonyme n'est stocké en clair** — uniquement une empreinte salée tronquée.

---

## 4. `agent_recherche_web/` — corpus web régionalisé

Génère un plan de requêtes Google sur **deux axes** — `axe1` consommateurs,
`axe2` concurrence — avec trois modes de ciblage géographique, puis crawle et
classe les pages.

**Prérequis** : `ANTHROPIC_API_KEY`, `APIFY_TOKEN`.

**Entrée CLI**

```bash
python main.py \
    --nom "…" --description "…" [--categorie "…"] \
    --geo MA --langue fr \
    [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--sortie` | `output.json` | Fichier écrasé à chaque exécution ; chaîne vide = n'écrire aucun fichier |
| `--stdout` | absent | Émet aussi le JSON sur la sortie standard |

**Sortie** — `ResultatRechercheWeb` :

| Champ | Contenu |
|---|---|
| `plan_requetes` | `texte` (opérateurs Google inclus), `axe`, `ciblage` (`tld`, `geo_keywords`, `ouverte`), `justification`, `est_repli` |
| `pages` | `url`, `domaine`, `titre`, `contenu_markdown` (tronqué à 20 000 car.), `contenu_tronque`, `requete_origine`, `axe_cible`, `ciblage`, `type_source`, `axes_servis`, `portee_regionale`, `pertinence`, `marques_detectees`, `type_resultat_serp` (`ORGANIC`/`SUGGESTED`), `rang_serp`, `langue_page` |
| `stats` | Pages collectées/retenues, pages par axe, répartitions (ciblage, type de source, domaine), `axes_sous_couverts`, compteurs d'exclusion (doublons, domaine exclu, trop courtes, sous seuil, non classifiées) |

`type_source` ∈ `comparatif`, `test_avis`, `article_presse`, `blog`,
`site_marque`, `site_marchand`, `forum`, `autre`.

**Spécificités**
- Actor `apify/rag-web-browser`, outil de scraping `raw-http`, **1 requête = 1 run**.
- 4 requêtes par axe, 2 requêtes ouvertes, 2 requêtes de repli si un axe reste
  sous 3 pages. Parallélisme 3 runs.
- Plancher de 500 caractères par page, seuil de pertinence 0,5, classification
  par lots de 10 sur un extrait de 1 500 caractères.
- `marques_detectees` est un **signal brut, non analysé**.

---

## 5. `agent_aliexpress/` — prix par SKU via l'API officielle

Seul module du projet à ne pas passer par Apify : il appelle l'**API
Dropshipping officielle** d'AliExpress. Seul aussi à renvoyer des **montants**,
d'où l'exigence d'une devise.

**Prérequis** : `ANTHROPIC_API_KEY`, `ALIEXPRESS_APP_KEY`,
`ALIEXPRESS_APP_SECRET`, `ALIEXPRESS_ACCESS_TOKEN`, `ALIEXPRESS_REFRESH_TOKEN`.
Le module rafraîchit seul l'access token et écrit le résultat dans `.tokens.json`,
qui prime alors sur le `.env` (jamais modifié).
Dépendance supplémentaire : `httpx` — pas d'`apify-client`.

**Entrée CLI** — **6 arguments obligatoires**, aucune valeur par défaut :

```bash
python main.py \
    --nom "Ceinture lombaire double traction" \
    --description "…" [--categorie "sante-bien-etre"] \
    --geo MA --langue fr --devise MAD [--verbose]
```

> ⚠️ **Une exécution = une région.** Une étude multi-régions se fait par
> exécutions successives, jamais par agrégation : le même produit vaut
> 10,99 EUR livré en France et 226,40 MAD livré au Maroc.

**Sortie** — `ResultatCollecteAliExpressAPI`, sur `stdout` :

| Champ | Contenu |
|---|---|
| `requetes` / `justification_requetes` | 2 à 4 requêtes catalogue dérivées par le LLM |
| `produits` (phase A) | `item_id`, `titre`, `url_produit`, `image`, `prix_vente`, `prix_original`, `devise`, `prix_formate`, `remise_pourcentage`, `note`, `taux_evaluation`, `nb_commandes`, `ids_categories`, `requete_origine`, `contexte` |
| `produits_detailles` (phase B) | `item_id`, `titre`, `nb_ventes`, `note_moyenne`, `nb_evaluations`, `statut_produit`, `delai_livraison_jours`, `skus[]`, `contexte` |
| `skus[]` | `sku_id`, `attributs_sku`, `attributs_lisibles`, `prix_base`, `prix_vente`, `devise`, `remise_pourcentage`, `stock_disponible` |
| `contexte` | `ContexteRegional` : pays de livraison demandé **et confirmé**, devise, langue, horodatage UTC, méthode API. **Recopié dans chaque ligne de prix** |
| `stats` | Devise, nb produits recherche/retenus/détaillés, nb SKU, min/médiane/max des prix (annonce et SKU), `totalCount` annoncé par requête, nb d'appels API |

**Spécificités**
- Méthodes `aliexpress.ds.text.search` (phase A) et `aliexpress.ds.product.get`
  (phase B), signature SHA-256.
- Plafonds : 4 requêtes, 20 items/page, 2 pages par requête, **15 produits détaillés**.
- La recherche est **instable** (~70 % d'échecs) : jusqu'à 5 tentatives sur erreur
  transitoire, backoff 10/25/45/60 s.
- `pays_livraison_confirme` ≠ `pays_livraison` signale un ciblage régional non garanti.
- Codes de sortie : `0` succès, **`2` région mal formée ou identifiants manquants**
  (seule erreur bloquante du module, délibérée : un prix sans région connue n'a
  aucune valeur).

---

## 6. `agent_amazon/` — produits concurrents et avis

Résout la région en **site Amazon du pays étudié**, planifie des recherches
(mots-clés + tri + facettes prix/note/avis), collecte les produits puis enrichit
les mieux classés de leurs avis.

**Prérequis** : `ANTHROPIC_API_KEY`, `APIFY_TOKEN`.

**Entrée CLI**

```bash
python main.py \
    --nom "…" --description "…" [--categorie "…"] \
    --geo FR --langue fr \
    [--domaine amazon.fr] [--avis 5] \
    [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--geo` | — | ISO-2 **ou texte libre** (`Maroc`, `Lyon`). Sélectionne le site Amazon **de ce pays**, jamais une adresse de livraison |
| `--domaine` | résolu | Marketplace imposée. **Court-circuite le contrôle de couverture** — à n'utiliser qu'en connaissance de cause |
| `--avis` | `5` | Produits de tête enrichis d'avis. **Un run d'actor par produit : principal levier de coût.** `0` pour n'en collecter aucun |

> ⚠️ **Le module ne s'applique qu'aux 22 pays disposant de leur propre site
> Amazon** (US, CA, MX, BR, GB, DE, FR, ES, IT, NL, BE, SE, PL, TR, EG, SA, AE,
> ZA, IN, JP, SG, AU). Pour tout autre pays — **le Maroc notamment** — rien
> n'est collecté, `region_couverte: false`, et la commande sort en **code 3**
> sans lancer le moindre run. Aucun repli sur « la marketplace la plus proche » :
> `amazon.fr` interrogé pour le Maroc décrit le marché français.

**Sortie** — `ResultatRechercheAmazon` :

| Champ | Contenu |
|---|---|
| `region_couverte` | Faux si le pays n'a pas de site Amazon propre |
| `marketplace` | `domaine`, `code_pays`, `explication` — nul si `region_couverte` est faux |
| `plan_recherches` | `mots_cles`, `tri`, `prix_min/max`, `note_min`, `nb_avis_min`, `justification`, `url`, `filtres_url`, `est_repli` |
| `produits` | `asin`, `titre`, `url`, `image`, `prix`, `devise`, `prix_barre`, `note`, `nb_avis`, `volume_achats_mensuel`, `marque`, `vendeur`, `note_vendeur`, `choix_amazon`, `rang_best_seller`, `disponible`, `livraison`, `recherche_origine`, `rang_collecte`, `correspondance`, `pertinence`, `avis[]` |
| `avis[]` | `note`, `titre`, `texte`, `date`, `achat_verifie`, `votes_utiles` — **jamais le nom du relecteur** (donnée personnelle, `includeGdprSensitive` laissé à faux) |
| `stats` | Produits collectés/retenus, produits avec avis, nb avis, compteurs d'exclusion, `nb_enregistrements_erreur` (signal de blocage anti-bot), min/médiane/max des prix, devise dominante **non convertie**, note moyenne, répartitions |

`correspondance` ∈ `equivalent`, `variante`, `accessoire`, `hors_sujet`.

**Spécificités**
- Actors `junglee/Amazon-crawler` (produits) et `junglee/amazon-reviews-scraper`
  (avis, **un run par produit**, ~10 avis par run sur le plan gratuit).
- 3 recherches + 1 recherche de repli si moins de 5 produits retenus ;
  30 produits max par recherche ; parallélisme 3 ; backoff long (20/60 s) car
  les échecs viennent surtout de blocages anti-bot.
- **Aucune adresse de livraison n'est transmise** : Amazon expose alors le
  catalogue complet du site dans sa propre devise. La mention `livraison` vaut
  donc pour le marché de la marketplace, pas pour la région d'étude.
- Codes de sortie : `0` succès, `1` erreur d'exécution, `2` usage argparse,
  **`3` région non couverte**.

---

## 7. `agent_meta_ads/` — annonces de la bibliothèque publicitaire Meta

Résout la région en pays de **diffusion**, planifie des recherches sur le texte
des annonces, collecte, dédoublonne par créatif et classe.

**Prérequis** : `ANTHROPIC_API_KEY`, `APIFY_TOKEN`.

**Entrée CLI**

```bash
python main.py \
    --nom "…" --description "…" [--categorie "…"] \
    --geo MA --langue fr \
    [--annonceur https://www.facebook.com/nike] \
    [--annonces 30] \
    [--sortie output.json] [--stdout] [--verbose]
```

| Argument | Défaut | Rôle |
|---|---|---|
| `--geo` | — | ISO-2, texte libre, ou **`ALL`**. Cible les annonces **diffusées** dans ce pays, quel que soit le pays de l'annonceur |
| `--annonceur` | — | URL d'une Page Facebook à surveiller directement. **Répétable** ; un run par URL, sans filtre de pays ni de statut |
| `--annonces` | `30` | Plafond d'annonces par recherche. **L'actor étant facturé À L'ANNONCE, c'est le principal levier de coût**, très loin devant le nombre de recherches |

**Sortie** — `ResultatRechercheMetaAds` :

| Champ | Contenu |
|---|---|
| `region_couverte` | Faux si la région n'a pas pu être résolue en un pays |
| `pays` | `code_pays` (ou `ALL`), `explication` |
| `plan_recherches` | `mots_cles`, `type_recherche` (`mots_cles` / `expression_exacte`), `statut_diffusion` (`actives`/`inactives`/`toutes`), `url`, `filtres_url`, `est_annonceur`, `est_repli` |
| `annonces` | `id_annonce`, `url_bibliotheque`, `annonceur`, `id_annonceur`, `titre`, `texte`, `description_lien`, `legende`, `cta`, `lien`, `image`, `video`, `type_media`, `id_collation`, `nb_declinaisons`, `plateformes`, `active`, `date_debut`, `date_fin`, `duree_diffusion_jours`, `portee_estimee`, `depense`, `devise`, `recherche_origine`, `rang_collecte`, `correspondance`, `pertinence` |
| `stats` | Annonces collectées/retenues, nb annonceurs, nb actives, doublons (dont **doublons de créatif**), compteurs d'exclusion, durée de diffusion médiane et max, répartitions (correspondance, annonceur, plateforme, CTA, recherche) |

`correspondance` ∈ `concurrent`, `categorie`, `accessoire`, `hors_sujet`.

**Pièges de lecture — inscrits dans le schéma**
- `description_lien` porte souvent **l'argumentaire complet**, là où `texte` se
  réduit à un titre.
- `date_fin` : sur une annonce **encore diffusée**, Meta y met la date du jour —
  ce n'est pas une date d'arrêt. Seul `active` dit si la diffusion se poursuit.
- `duree_diffusion_jours` est un indicateur de **longévité, jamais de rentabilité**.
- `portee_estimee` et `depense` ne sont publiées que pour les **annonces politiques**.
- L'URL `video` est **signée et éphémère** : elle expire en quelques heures.
- `id_collation` (groupe de déclinaisons calculé par Meta) est la **clé de
  dédoublonnage privilégiée** — obligatoire, l'actor renvoyant massivement le
  même créatif décliné.

**Spécificités**
- Actor `apify/facebook-ads-scraper` (officiel, SDK courant — les actors
  communautaires équivalents échouent).
- 3 recherches + 1 de repli sous 5 annonces retenues ; parallélisme 3 ;
  seuil de pertinence 0,5 ; classification par lots de 15 sur 400 caractères.
- `plafond_atteint` dans `statuts_collecte` signale un run tronqué par la limite.
- Codes de sortie : `0` succès, `1` erreur d'exécution, `2` usage argparse,
  **`3` région non résolue**.

---

## 8. Enchaîner les agents

Le socle d'entrée étant identique, un orchestrateur peut alimenter les six
modules avec le même couple `(FicheProduit, ParametresMarche)`. Trois réserves :

1. **`agent_aliexpress` exige en plus une `devise`**, et ne traite qu'une région
   par exécution.
2. **`agent_amazon` sort en code 3** sur tout pays sans site Amazon propre —
   le Maroc en fait partie. Le code de sortie est distinct précisément pour
   qu'un orchestrateur enchaîne sur un autre collecteur sans analyser le JSON.
   `agent_meta_ads` utilise le même code 3 pour une région non résolue.
3. **Vérifier le quota Apify avant toute mesure de volume** : un compte en fin de
   quota tronque les runs en les marquant `SUCCEEDED`, ce qui fausse silencieusement
   les statistiques de collecte.

Chaque module dispose par ailleurs d'un `README.md` détaillé dans son répertoire.

### Choisir la langue d'étude — `langues_marche.py`

`langue` n'est pas un détail de formulation : c'est elle qui décide de la langue
du mot-clé pivot, des requêtes Reddit, des requêtes Google et des recherches
Amazon et Meta. Une langue étrangère au marché ne produit pas une erreur, elle
produit un corpus **vide** — un mot-clé français interrogé sur le Google Trends
espagnol renvoie une série absente que le collecteur ne peut pas distinguer d'un
blocage anti-bot.

D'où un préalable à toute étude : dériver la langue du seul code pays. La table
sert **la langue principale du pays** — celle dans laquelle sa population écrit
au quotidien.

```bash
python langues_marche.py --geo MA
```

| Champ de sortie | Contenu |
|---|---|
| `codes` | Liste d'**un seul** code ISO 639-1. La forme reste une liste pour que l'orchestrateur puisse itérer quand plusieurs langues lui sont imposées |
| `langues[]` | `code`, `nom` (français), `role`, `justification` |
| `reserve` | Motif d'arbitrage si le marché en porte un, sinon `null` |
| `date_validite` | Date de vérification de la table |
| `limites` | Rappels méthodologiques |

**Spécificités**
- 244 pays, 75 langues. **Aucun appel LLM, aucun appel Apify** : consultation de
  table, gratuite et instantanée.
- Même jeu de pays que [devise_marche.py](devise_marche.py) : les deux tables se
  contrôlent l'une l'autre, un pays présent dans l'une et absent de l'autre est
  un défaut.
- Codes de sortie : `0` succès, `1` pays inconnu, `2` usage argparse. **Aucun
  repli** : une étude lancée sur une langue devinée à tort ne vaut rien.

**Ce que la table ne fait pas — la réserve à lire.** Elle ne distingue pas la
langue *parlée* de la langue *tapée dans un moteur*. Ces deux langues divergent
sur les marchés où la scolarisation se fait dans une langue étrangère : Inde,
Nigéria, Pakistan, Philippines, Maghreb, Afrique de l'Est. Un Indien cherche un
produit en anglais, pas en hindi. Ces cas sont recensés un à un dans
`MARCHES_A_ARBITRER`, remontés dans `reserve`, et l'orchestrateur les affiche en
`Write-Warning` au lancement. Les ignorer expose à un corpus vide sans qu'aucun
module ne soit en échec — exactement le défaut que ce module devait corriger.

**Un marché multilingue n'est plus étendu d'office.** Une seule langue est
retenue ; le segment linguistique écarté n'est pas couvert et son absence est
silencieuse par construction. Le couvrir se demande : `-Langues nl,fr`.

### Choisir la devise d'étude — `devise_marche.py`

Même préalable, raisonnement opposé. La langue de recherche est un **jugement
d'usage** — un modèle y apporte quelque chose. La devise d'un pays est un **fait
administratif** : une table la donne exactement, gratuitement, instantanément, et
se corrige à la ligne le jour d'une réforme monétaire. Aucun appel LLM.

```bash
python devise_marche.py --geo US
```

| Champ de sortie | Contenu |
|---|---|
| `devise` | Code ISO 4217, ex. `USD` |
| `nom` | Libellé français, pour qu'un humain puisse démentir le code |
| `date_validite` | Date de vérification de la table |
| `limites` | Dont le cas AliExpress ci-dessous |

**Spécificités**
- 244 pays, 153 devises. Les territoires sans économie de détail (AQ, BV, HM,
  GS, TF, UM) sont volontairement absents.
- Codes de sortie : `0` succès, `1` pays inconnu, `2` usage argparse. **Aucun
  repli sur un défaut** : des prix libellés dans une devise devinée resteraient
  cohérents entre eux, donc indétectables en aval, tout en étant faux.
- Trois entrées à contrôler en priorité, parce qu'elles ont changé récemment :
  Bulgarie → EUR (01/01/2026), Zimbabwe → ZWG (04/2024), Curaçao et
  Sint-Maarten → XCG (03/2025).

**Le piège à connaître.** `agent_aliexpress` **exclut** toute ligne de prix
libellée dans une autre devise que celle demandée, plutôt que de la convertir
(§5 des principes). Si la plateforme ne sait pas servir la monnaie locale d'un
marché, elle répond dans la sienne et *toutes* les lignes sont écartées : la
collecte revient vide sans qu'aucun module ne soit techniquement en échec. Le
symptôme est un `aliexpress.json` vide portant des anomalies `controle_devise`.
Le repli est de rejouer en `-Devise USD`.

**Un marché bilingue = deux études complètes.** Les indices Google Trends, les
corpus Reddit et les benchmarks prix ne se moyennent pas d'une langue à l'autre :
chaque langue décrit un segment distinct et se traite par une exécution séparée,
comme les régions pour `agent_aliexpress`. `etude_marche.ps1` s'en charge quand
on le lui demande (`-Langues nl,fr`), un sous-répertoire par langue, et multiplie
le coût d'autant. La langue de **rédaction des rapports**
d'analyse reste indépendante (`-LangueRapport`, `fr` par défaut) : une collecte
arabophone produit un rapport lisible en français.

---

## 9. Les cinq agents d'analyse et de restitution

Ils partagent un contrat propre, distinct de celui des collecteurs.

### Contrat commun d'analyse

| Point | Règle |
|---|---|
| Entrées | Des **fichiers JSON locaux** produits en amont. Pour F3 et F4, chemins tous optionnels, au moins un requis ; pour F5, idem ; **pour F6 et F7, `--recommandations` est obligatoire**. |
| Réseau | **API Anthropic uniquement.** Ni `apify-client`, ni `httpx` dans les `requirements.txt`. |
| Schémas de consommation | Re-déclaration Pydantic **minimale**, `extra="ignore"`, **aucun import du code des agents amont**. Le couplage est un contrat JSON. |
| Encodage d'entrée | Détection automatique : `utf-8-sig`, `utf-16`, `utf-8`, `cp1252`. Une redirection PowerShell produit de l'UTF-16, jamais de l'UTF-8. |
| Cohérence | `produit.nom` divergent entre deux fichiers → **code 2** (mélange d'études interdit). `marche.geo` divergent → alerte, traitement poursuivi. |
| Nombres | Tous calculés par du code déterministe, puis **réécrits** par la post-validation. Aucun nombre produit par un modèle n'atteint la sortie. |
| Citations | Toute preuve, tout fondement référence un identifiant vérifié. Une référence inventée est retirée et tracée. |
| Sortie | Socle commun `sources_utilisees`, `alertes_coherence`, `statuts_analyse`, `donnees_suffisantes`, `confiance_globale`, `limites`, `hypotheses`, plus `horodatage_utc` sur F3 et F4. |
| Codes de sortie | `0` succès, `1` erreur imprévue, `2` entrée inexploitable ou incohérence produit. |
| Modèles | `claude-haiku-4-5-20251001` pour l'extraction en lots, `claude-sonnet-4-5-20250929` pour la synthèse. Température 0. F5, F6 et F7 n'emploient que le second. |

### F3 — `agent_insights_consommateurs/`

```bash
python main.py --reddit … --amazon … --recherche-web … [--sortie output.json]
```

Corpus unifié d'unités consommateurs, échantillonnage stratifié, cartographie
LLM par lots, normalisation des libellés, réduction chiffrée, synthèse.
Mesuré : **26 appels, ≈ 0,45 $, 312 s** sur 292 unités.

### F4 — `agent_analyse_concurrentielle/`

```bash
python main.py --aliexpress … --amazon … --meta-ads … --recherche-web … \
    [--prix-envisage 249.0 --devise-envisagee MAD]
```

Référentiel d'offres/annonces/pages/avis, extraction d'attributs et de claims,
consolidation des concurrents, benchmark **par source et par devise**, analyse
qualitative, différenciation. **Aucune conversion de devise, jamais.**
Mesuré : **27 appels, ≈ 0,78 $, 568 s** sur 165 offres.

### F5 — `agent_recommandations_strategiques/`

```bash
python main.py --insights … --concurrence … --tendances …
```

Dossier de synthèse borné (seul contenu vu par les chaînes LLM), diagnostic
croisé, **notation de la grille de potentiel par le modèle puis application de
la règle de verdict par le code**, recommandations adaptées au verdict.
Mesuré : **6 appels, ≈ 0,71 $, 503 s**.

> ⚠️ **La règle de verdict est une hypothèse de travail**, non un arbitrage
> validé : ni le CDC ni la SFG ne définissent le « potentiel commercial ».
> Chaque sortie porte `statut_regle="hypothese_de_travail_a_valider"`.
> `declenche_plc` vaut vrai **uniquement** si le verdict est `positif` ; c'est la
> porte d'entrée du module de cycle de vie, et cet agent ne classe aucune phase.

### F6 — `agent_plc/`

```bash
python main.py --recommandations … [--insights …] [--concurrence …] [--forcer]
```

Module **conditionnel** : il ne classe que si `verdict_potentiel.declenche_plc`
vaut vrai. Sinon il produit une **sortie courte de non-déclenchement**, sans le
moindre appel LLM, en **code 0** — ce n'est pas une erreur.

Quatre familles de signaux temporels (demande, dynamique publicitaire, structure
de l'offre, corpus d'avis) sont extraites par le code ; **le modèle oriente
chaque famille, le code agrège et décide** d'une phase unique parmi
`introduction`, `croissance`, `maturite`, `declin` — ou `null`.
Mesuré : **0 appel** en non-déclenchement ; **2 appels, ≈ 0,14 $, 126 s** avec
`--forcer`.

> ⚠️ **La grille de lecture des phases est une hypothèse de travail**, non un
> arbitrage validé : ni le CDC ni la SFG ne définissent de grille de cycle de
> vie. Chaque sortie porte `statut_regle="hypothese_de_travail_a_valider"`.
>
> ⚠️ **Dépendance connue** : la sortie F4 ne contient pas encore
> `intensite_concurrentielle.dynamique_publicitaire` (exigence D4). La famille
> correspondante est donc `non_evaluable`, ce qui force l'incertitude à `elevee`.
> Elle n'est **jamais reconstituée localement** : le piège `date_fin` rend tout
> calcul local invalide.

### F7 — `agent_restitution/`

```bash
python main.py --recommandations … [--insights …] [--concurrence …] [--plc …] \
    [--rapport rapport_etude.md] [--resume resume_executif.md]
```

Dernier maillon : il ne collecte rien, n'analyse rien, et met en forme des
analyses existantes en un **rapport Markdown de 9 sections** et un **résumé
exécutif d'une page**. `stdout` reste du JSON pur — les documents ne sortent que
dans leurs fichiers dédiés.
Mesuré : **6 appels, ≈ 0,10 $, 75 s** sur quatre entrées ; **5 appels, ≈ 0,08 $,
57 s** sur F5 seule.

Trois garanties :

1. **Liste blanche numérique** — chaque nombre du rapport doit correspondre à une
   valeur des entrées ou d'un bloc généré par le code ; sinon la phrase porteuse
   est retirée et comptée dans `controles.nb_nombres_retires` (0 sur run sain).
2. **Verdict et phase recopiés tels quels**, jamais adoucis : le mot du verdict
   figure dans le titre de sa section et est vérifié contre le JSON.
3. **Bascules de verdict recalculées** par simulation de la règle F5 sur toutes
   les mutations mono-critère. Sur les fixtures du run n°1, seule la bascule
   « différenciation » subsiste — les trois autres, annoncées dans le texte libre
   de F5, sont incompatibles avec la règle et sont retirées.

Une analyse manquante ne produit jamais de section vide : la section est
construite depuis l'écho du dossier de synthèse et porte une **mention explicite
d'étude partielle** (exigence F7.3).

### Enchaîner les cinq

En pratique, [etude_marche.ps1](etude_marche.ps1) fait tout cela. Le détail
ci-dessous vaut pour un rejeu manuel, agent par agent :

```bash
cd agent_insights_consommateurs && python main.py --reddit … --amazon … --recherche-web …
cd ../agent_analyse_concurrentielle && python main.py --aliexpress … --amazon … --meta-ads … --recherche-web …
cd ../agent_recommandations_strategiques && python main.py \
    --insights ../agent_insights_consommateurs/output.json \
    --concurrence ../agent_analyse_concurrentielle/output.json \
    --tendances ../sortie_tendances.json
cd ../agent_plc && python main.py \
    --recommandations ../agent_recommandations_strategiques/output.json \
    --insights ../agent_insights_consommateurs/output.json \
    --concurrence ../agent_analyse_concurrentielle/output.json
cd ../agent_restitution && python main.py \
    --recommandations ../agent_recommandations_strategiques/output.json \
    --insights ../agent_insights_consommateurs/output.json \
    --concurrence ../agent_analyse_concurrentielle/output.json \
    --plc ../agent_plc/output.json
```

Quatre réserves :

1. **`agent_tendances` émet sur `stdout`** : le fichier passé à `--tendances`
   est une redirection, et son contrat ne porte **aucun horodatage** — F5 ne
   peut donc pas qualifier la fraîcheur de cette entrée.
2. **Un verdict négatif ou indéterminé sort en code 0.** C'est un résultat
   d'analyse, pas une erreur : un orchestrateur doit lire
   `verdict_potentiel.verdict`, jamais le code de sortie.
3. **F6 sort aussi en code 0 quand il ne classe rien** : lire
   `declenchement.mode`, jamais le code de sortie. Le drapeau `--forcer` est
   réservé à l'étude et au test, **interdit à l'orchestrateur en production**.
4. **F6 peut être enchaîné sans condition** : s'il ne se déclenche pas, sa sortie
   reste valide et F7 la consomme sans erreur, en produisant l'encart standard de
   phase non déterminée.

### Fixtures

`fixtures/` contient les sorties réelles des collecteurs, dont **seul l'en-tête
produit/marché a été harmonisé** sur un produit pivot afin que les agents
d'analyse puissent être exécutés bout à bout. Chaque fichier porte un bloc `_fixture`
décrivant son origine et son adaptation. Les deux fichiers `tendances*.json`
sont **synthétiques** : aucune sortie réelle du collecteur Tendances n'existe
sur disque.
