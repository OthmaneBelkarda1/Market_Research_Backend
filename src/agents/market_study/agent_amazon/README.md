# agent_amazon

Agent CLI de collecte **Amazon régionalisée** : une fiche produit et un pays
d'étude en entrée, un corpus de produits concurrents qualifié — prix, notes,
volumes d'achat, vendeurs, avis clients — en JSON en sortie.

> ⚠️ **L'agent ne s'applique qu'aux pays où Amazon exploite son propre site.**
> `FR` → `amazon.fr`. `MA`, `CH`, `NG`, `BE` → **refus** : l'exécution s'arrête
> avant le moindre run, avec `region_couverte: false` et le code de sortie 3.
> Voir §4.

C'est le portage de l'ancien script `amazon.py` (racine du projet) dans
l'architecture des autres collecteurs du projet — `agent_tendances`,
`agent_reddit`, `agent_recherche_web`, `agent_aliexpress_api` — avec le même
contrat d'entrée, le même style de sortie et le même appareil critique
(`statuts_collecte`, `limites`, `hypotheses`).

---

## 1. Ce que fait le module — et ce qu'il ne fait pas

**Il fait :**

- résoudre un pays d'étude en **son** site Amazon (`FR` → `amazon.fr`,
  `Lyon` → `amazon.fr`, `AE` → `amazon.ae`…), et **s'arrêter** si ce pays n'en a
  pas ;
- transformer une fiche produit en **plan de recherches** Amazon (mots-clés dans
  la langue de la marketplace, tri, bornes de prix, planchers de note et d'avis) ;
- exécuter ces recherches via l'actor Apify `junglee/Amazon-crawler`, une
  recherche = un run, avec relance des recherches restées vides ;
- **qualifier** chaque fiche remontée par rapport au produit de référence
  (concurrent direct / variante / accessoire / hors sujet) ;
- collecter les **avis clients** des produits les plus pertinents via
  `junglee/amazon-reviews-scraper`, un run par produit ;
- livrer le tout avec ses statistiques, ses statuts de collecte, ses limites et
  ses hypothèses.

**Il ne fait pas :**

- **aucune analyse ni recommandation** — c'est un collecteur, pas un analyste ;
- **aucun repli sur une marketplace voisine** : pas d'`amazon.fr` pour étudier le
  Maroc (voir §4) ;
- **aucun filtrage par pays de livraison** (voir §4) ;
- **aucune conversion de devise** : les prix restent dans la devise de la
  marketplace interrogée, et ne sont comparables à rien d'autre ;
- aucune boucle infinie de rattrapage : au plus **un** cycle de repli.

---

## 2. Installation

```bash
pip install -r requirements.txt
cp .env.example .env      # puis renseigner les deux clés
```

| Variable | Usage |
| --- | --- |
| `ANTHROPIC_API_KEY` | Contrôle qualité de la fiche, résolution de région, plan de recherches, classification des produits. |
| `APIFY_TOKEN` | Actors `junglee/Amazon-crawler` et `junglee/amazon-reviews-scraper`. `APIFY_API_TOKEN` est accepté en repli. |

Le `.env` est cherché depuis le répertoire courant et remonte l'arborescence
(`find_dotenv(usecwd=True)`) : un `.env` à la racine du projet fonctionne aussi.

---

## 3. Utilisation

### En ligne de commande

```bash
python main.py \
    --nom "JBL Endurance Peak 4 Open Ear" \
    --description "Écouteurs à conduction ouverte pour le sport." \
    --categorie "electronics" \
    --geo FR \
    --langue fr \
    --verbose
```

| Option | Défaut | Rôle |
| --- | --- | --- |
| `--nom` | requis | Titre commercial du produit. |
| `--description` | requis | Description libre. |
| `--categorie` | — | Catégorie e-commerce. |
| `--geo` | requis | Code ISO-2 **ou lieu en texte libre** (« France », « Lyon », « UAE »). Un pays sans site Amazon arrête l'exécution. |
| `--langue` | requis | Langue du marché, ISO-2. |
| `--domaine` | — | Impose la marketplace (`amazon.de`) et court-circuite **la résolution de région ET le contrôle de couverture**. |
| `--avis` | `5` | Produits de tête enrichis d'avis. `0` désactive complètement la collecte d'avis. |
| `--sortie` | `output.json` | Fichier écrasé à chaque exécution. Chaîne vide = pas de fichier. |
| `--stdout` | off | Sérialise aussi le JSON sur `stdout`. |
| `--verbose` | off | Progression sur `stderr`. |

`stdout` ne contient jamais que du JSON : toute trace part sur `stderr`, ce qui
rend `python main.py … --stdout --sortie "" | jq …` utilisable tel quel.

**Codes de sortie** — pour qu'un orchestrateur enchaîne sans analyser le JSON :

| Code | Sens |
| --- | --- |
| `0` | Exécution menée à son terme (même si le corpus est vide : lire `donnees_disponibles`). |
| `2` | Erreur d'usage (argparse). |
| `3` | **Pays sans site Amazon** : rien n'a été collecté, rien n'a été facturé. |

### En bibliothèque

```python
from agent import rechercher_amazon
from schemas import FicheProduit, ParametresMarche

resultat = rechercher_amazon(
    FicheProduit(nom="…", description="…", categorie="electronics"),
    ParametresMarche(geo="FR", langue="fr"),
    nb_produits_avis=3,          # 0 pour ne collecter aucun avis
)
if not resultat.region_couverte:                       # à tester en premier
    print(resultat.limites[1])                         # motif exact du refus
else:
    print(resultat.marketplace.domaine, len(resultat.produits))
```

`rechercher_amazon` **ne lève jamais d'exception** : un échec total renvoie un
résultat exploitable avec `donnees_disponibles=False` et le détail de chaque run
dans `statuts_collecte`.

`FicheProduit` et `ParametresMarche` sont les modèles partagés par tous les
collecteurs du projet : un orchestrateur amont alimente les cinq agents avec le
même objet.

---

## 4. Le cœur du module : la région

### 4.0 Pas de site Amazon dans le pays ⇒ pas d'agent

C'est la règle la plus importante du module. `strategy.resoudre_marketplace`
renvoie `None`, `rechercher_amazon` s'arrête immédiatement, **avant le premier
run Apify et avant le premier appel LLM**.

```bash
$ python main.py --nom "…" --description "…" --geo MA --langue fr
Agent inapplicable à « MA ».
« MA » : aucun site Amazon dans ce pays. Amazon n'exploite pas de site dans ce
pays. Cet agent est donc INAPPLICABLE à cette région : il ne saurait interroger
qu'une marketplace étrangère, dont le catalogue, les prix et les avis
décriraient un autre marché. Utiliser un collecteur adapté à la région —
AliExpress, Temu, recherche web ou Reddit.
Aucun run Apify n'a été lancé.
$ echo $?
3
```

**Pourquoi ce refus plutôt qu'un repli.** Interroger `amazon.fr` pour étudier le
Maroc produit un corpus parfaitement plausible — et faux : les prix sont en
euros pour un marché en dirhams, les vendeurs livrent en France, les avis
viennent de consommateurs français, et les marques présentes ne sont pas celles
qu'un Marocain rencontre. Une étude bâtie là-dessus se trompe de marché sans
jamais le signaler. Un résultat vide et explicite vaut mieux qu'un corpus
convaincant hors sujet.

Le JSON est tout de même écrit, exploitable par un orchestrateur :

```jsonc
{
  "marche": { "geo": "MA", "langue": "fr" },
  "region_couverte": false,
  "marketplace": null,
  "produits": [],
  "donnees_disponibles": false,
  "limites": [ "AUCUNE COLLECTE N'A ÉTÉ LANCÉE : …", "« MA » : aucun site Amazon dans ce pays. …" ],
  "hypotheses": []
}
```

Les limites méthodologiques habituelles sont **omises** dans ce cas : elles
décriraient un corpus qui n'existe pas.

Deux échappatoires, et deux seulement :

- `--domaine amazon.fr` — décision explicite d'opérateur. Le contrôle est
  court-circuité, l'explication le dit, et c'est alors à l'analyste d'assumer
  que le corpus décrit un autre marché.
- ajouter le pays dans `MARKETPLACE_PAR_PAYS` (`config.py`) — à réserver au cas
  où Amazon y ouvre réellement un site.

### 4.1 Une région choisit un SITE, pas une adresse de livraison

C'est la décision structurante du module, héritée de `amazon.py` et conservée
telle quelle. Aucun `countryCode` ni `zipCode` n'est transmis aux actors.

Renseigner une adresse de livraison ferait deux choses, toutes deux
indésirables pour une étude de marché :

1. Amazon **masquerait** tout ce qu'il ne peut pas expédier à cette adresse ;
2. Amazon **convertirait** les prix dans la devise du pays de livraison.

Le corpus livré est donc le catalogue **complet** de la marketplace, dans sa
**propre devise**. Une marketplace n'est pas une destination de livraison.
`proxyCountry` est laissé sur `AUTO_SELECT` : l'actor apparie lui-même son proxy
au domaine interrogé.

### 4.2 Comment une région devient une marketplace

`strategy.resoudre_marketplace` décide dans cet ordre :

1. **`--domaine` / `domaine_force`** — l'opérateur a tranché, tout contrôle est
   court-circuité ;
2. **la table `MARKETPLACE_PAR_PAYS`** (`config.py`) si `--geo` est un code
   ISO-2 : réponse instantanée, identique à chaque exécution, **et refus si le
   code n'y figure pas** ;
3. **le modèle**, pour toute autre saisie (nom de pays, ville, région, autre
   alphabet). Il identifie **uniquement le pays** — il ne choisit aucun site et
   ne propose aucun pays de substitution ; la table tranche ensuite. Un lieu
   irrésoluble donne un refus, jamais un site par défaut.

Les 22 pays couverts, et eux seuls :

| Pays | Site | | Pays | Site |
| --- | --- | --- | --- | --- |
| `US` | `amazon.com` | | `BE` | `amazon.com.be` |
| `CA` | `amazon.ca` | | `SE` | `amazon.se` |
| `MX` | `amazon.com.mx` | | `PL` | `amazon.pl` |
| `BR` | `amazon.com.br` | | `TR` | `amazon.com.tr` |
| `GB` | `amazon.co.uk` | | `EG` | `amazon.eg` |
| `DE` | `amazon.de` | | `AE` | `amazon.ae` |
| `FR` | `amazon.fr` | | `SA` | `amazon.sa` |
| `ES` | `amazon.es` | | `ZA` | `amazon.co.za` |
| `IT` | `amazon.it` | | `IN` | `amazon.in` |
| `NL` | `amazon.nl` | | `JP` | `amazon.co.jp` |
| | | | `SG` | `amazon.sg` |
| | | | `AU` | `amazon.com.au` |

Tout le reste — `MA`, `DZ`, `TN`, `CH`, `AT`, `PT`, `IE`, `NG`, `KW`, `AR`,
`NZ`, `KR`… — est refusé (§4.0).

⚠️ **Cette table est un relevé manuel** (03/08/2026), pas une liste tirée d'une
API. Deux erreurs possibles, de gravité opposée : un pays **manquant** provoque
un refus injustifié mais ne coûte rien ; un domaine **erroné ou non supporté par
l'actor** produit des URLs mortes et des runs facturés pour rien. Deux cas
laissés dehors faute de certitude :

- **`IE` / `amazon.ie`** — site irlandais dédié annoncé par Amazon, ouverture
  effective non vérifiée. À confirmer avant de l'ajouter.
- **`CN` / `amazon.cn`** — exclu délibérément : la marketplace domestique
  chinoise a fermé en 2019, il ne reste qu'une vitrine d'import sans catalogue à
  étudier.

À vérifier également au premier run réel : que `junglee/Amazon-crawler` sait
crawler `amazon.com.be` et `amazon.co.za`, les deux plus récents.

Le résultat expose le choix et sa justification :

```json
"region_couverte": true,
"marketplace": {
  "domaine": "amazon.fr",
  "code_pays": "FR",
  "explication": "« Lyon » → FR : collecte sur amazon.fr, le site Amazon du pays."
}
```

---

## 5. Architecture

Huit fichiers à plat, comme les autres agents du projet. Le sens de lecture va
de haut en bas ; aucune dépendance circulaire.

| Fichier | Rôle | LLM | Réseau |
| --- | --- | --- | --- |
| `config.py` | Constantes, `.env`, logging, table des marketplaces, limites et hypothèses. **Aucune valeur magique ailleurs.** | — | — |
| `schemas.py` | Contrats Pydantic v2 d'entrée, de sortie et des sorties structurées. | — | — |
| `strategy.py` | Contrôle qualité de la fiche, **résolution de la région et garde de couverture**, plan de recherches, construction des URLs Amazon. | ✔ | — |
| `amazon_source.py` | Les deux actors Apify. Ne propage jamais d'exception. | — | ✔ |
| `normalize.py` | Items bruts → `ProduitAmazon` / `Avis`, statistiques. Fonctions **pures**. | — | — |
| `filtering.py` | Dédoublonnage, critères du plan, classification par lots, seuil de pertinence. | ✔ | — |
| `agent.py` | Orchestration, cycle de repli, limites, hypothèses. | — | — |
| `main.py` | CLI, écriture du JSON. | — | — |

### Séquence d'exécution

```
fiche produit + région
  │
  ├─ région → marketplace ........................ strategy (table, LLM en repli)
  │     └─ pays sans site Amazon → ARRÊT IMMÉDIAT (0 run, 0 appel LLM)
  ├─ contrôle qualité de la fiche ................ strategy (LLM, informatif)
  ├─ plan de N recherches ........................ strategy (LLM, contrôlé par le code)
  │
  ├─ CYCLE 1
  │   ├─ N runs `Amazon-crawler` en parallèle ..... amazon_source
  │   ├─ relance des recherches vides (pause) ..... agent + strategy
  │   ├─ normalisation ........................... normalize
  │   ├─ dédoublonnage + critères du plan ........ filtering
  │   ├─ classification par lots ................. filtering (LLM)
  │   └─ seuil de pertinence ..................... filtering
  │
  ├─ si corpus < SEUIL_MIN_PRODUITS → CYCLE DE REPLI (une seule fois)
  │
  ├─ tri par pertinence, puis rang de collecte ... agent
  ├─ avis des K premiers produits ................ amazon_source (1 run / produit)
  └─ statistiques, limites, hypothèses ........... normalize + agent
```

---

## 6. Le plan de recherches

Le modèle **propose**, le code **dispose**. Chaque recherche proposée passe un
contrôle mécanique dans `strategy._conformer` :

| Contrôle | Traitement |
| --- | --- |
| Mots-clés vides | Recherche écartée. |
| Mots-clés reprenant le **titre commercial brut** | Recherche écartée — sur Amazon, une référence complète ne remonte au mieux qu'une fiche. |
| Tri hors nomenclature | Ramené à `pertinence`. |
| Bornes de prix ≤ 0 | Borne ignorée. |
| Bornes de prix inversées | Permutées. |
| `note_min` hors de l'échelle 1–5 | Ignorée. |
| Doublon de mots-clés | Écarté. |

Le prompt impose par ailleurs : mots-clés **dans la langue de la marketplace**
(pas celle de la fiche), formulations courtes et catégorielles, conservation de
l'attribut différenciant, bornes de prix **dans la devise de la marketplace**,
et un angle différent par recherche.

Si le plan n'atteint pas `NB_RECHERCHES`, `LIMITE_PLAN_INCOMPLET` est jointe au
résultat — aucune re-sollicitation du modèle n'est faite.

### URLs produites

Les critères sont poussés **dans l'URL Amazon** plutôt qu'appliqués seulement en
Python : sans cela, l'actor dépenserait tout son quota d'items à scraper des
produits qu'on écarte ensuite.

```
https://www.amazon.fr/s?k=chargeur+voiture+magnetique&s=price-asc-rank&rh=p_36%3A-3000
                          └── mots-clés ──┘         └── tri ──┘       └─ prix ≤ 30 € ─┘
```

| Tri du module | Paramètre `s=` d'Amazon |
| --- | --- |
| `pertinence` | *(aucun)* |
| `meilleures_ventes` | `exact-aware-popularity-rank` |
| `prix_croissant` | `price-asc-rank` |
| `prix_decroissant` | `price-desc-rank` |
| `note` | `review-rank` |
| `nouveautes` | `date-desc-rank` |

⚠️ **Piège des unités.** La facette `rh=p_36:<min>-<max>` s'exprime en unités
**mineures** (centimes) : 30 € s'écrit `3000`. Sur les marketplaces dont la
devise n'a pas de sous-unité — `amazon.co.jp` — l'unité mineure est l'unité
elle-même, et multiplier par 100 demanderait un prix cent fois trop élevé. La
liste est dans `MARKETPLACES_SANS_DECIMALES`.

---

## 7. Les actors Apify

### 7.1 `junglee/Amazon-crawler` — produits

Une recherche = un run. Payload envoyé :

```json
{
  "categoryOrProductUrls": [{"url": "https://www.amazon.fr/s?k=…"}],
  "maxItemsPerStartUrl": 30,
  "maxSearchPagesPerStartUrl": 2,
  "scrapeProductDetails": true,
  "scrapeSellers": true,
  "useCaptchaSolver": true
}
```

- `scrapeProductDetails` fait visiter chaque fiche : sans lui, on n'obtient que
  les données maigres de la vignette de résultats — ni ASIN, ni rangs Best
  Sellers, ni détail de notation.
- `scrapeSellers` ajoute le profil du vendeur (note globale sur la durée de vie
  du compte, nombre de notes) au nom du vendeur, déjà présent sans l'option.
- `useCaptchaSolver` n'est activé que sur `amazon.com`
  (`UTILISER_SOLVEUR_CAPTCHA_SUR`) : d'après la documentation de l'actor, il
  n'est fiable que là ; ailleurs il ajoute des tentatives au lieu d'en épargner.
- `maxSearchPagesPerStartUrl` est calculé sur une base volontairement pessimiste
  de 24 produits par page (contre ~48 réels) : Amazon en bloque une partie, la
  marge évite qu'une recherche étroite s'arrête sous le quota.
- **L'actor crawle n'importe quelle URL de listing** : page de recherche, de
  catégorie, Best Sellers, ou fiche produit isolée. Ce module ne lui envoie que
  des pages de recherche qu'il construit lui-même, mais la capacité existe.

**Schéma de sortie exploité** (noms de champs centralisés dans `config.py`,
relevés via l'implémentation précédente `amazon.py`, en service) :

| Champ | Type | Devient |
| --- | --- | --- |
| `asin` | `str` | `asin` — clé de dédoublonnage |
| `title` | `str` | `titre` |
| `url`, `thumbnailImage` | `str` | `url`, `image` |
| `price` | `{value, currency}` | `prix`, `devise` |
| `listPrice` | `{value, currency}` | `prix_barre` |
| `stars`, `reviewsCount` | `float`, `int` | `note`, `nb_avis` |
| `monthlyPurchaseVolume` | `str` | `volume_achats_mensuel` |
| `brand` | `str` | `marque` |
| `seller` | `str` **ou** `{name, ratingLifetime:{starsOutOf5, ratingCount}}` | `vendeur`, `note_vendeur`, `nb_notes_vendeur` |
| `isAmazonChoice` | `bool` | `choix_amazon` |
| `bestsellerRanks` | `[{rank, category}]` | `rang_best_seller`, `categorie_best_seller` |
| `delivery`, `inStock` | `str`, `bool` | `livraison`, `disponible` |
| `error` | `str` | **pas un produit** — voir ci-dessous |

⚠️ **`error` dans le dataset.** L'actor n'échoue pas quand Amazon lui sert une
page vide ou bloquée : il écrit `{"error": "no_results_found", …}` **dans le
dataset**, à la place des produits. Un run peut donc être `SUCCEEDED` et ne
contenir aucun produit. Ces enregistrements sont comptés
(`stats.nb_enregistrements_erreur`) et déclenchent `LIMITE_BLOCAGE_AMAZON`.

Le champ `seller` a **deux formes** selon que `scrapeSellers` est actif : une
chaîne ou un objet. Les deux sont gérées dans `normalize._vendeur`.

### 7.2 `junglee/amazon-reviews-scraper` — avis

**Un produit = un run**, délibérément. L'actor plafonne un run à une dizaine
d'avis : grouper plusieurs URLs dépenserait toute l'allocation sur le premier
produit de la liste.

```json
{
  "productUrls": [{"url": "https://www.amazon.fr/dp/B0…"}],
  "maxReviews": 10,
  "sort": "helpful",
  "filterByRatings": ["allStars"],
  "includeGdprSensitive": false,
  "deduplicateRedirectedAsins": true,
  "reviewsCutoffDate": "2 years"
}
```

- `includeGdprSensitive: false` — le nom du relecteur est une donnée personnelle
  sans usage ici. Il n'apparaît nulle part dans la sortie.
- `reviewsCutoffDate` — une annonce ayant changé de main ou de qualité conserve
  ses anciens avis ; la coupure dépense le petit budget d'avis sur ce qui est
  vrai aujourd'hui. Accepte `« N days|months|years »` ou une date ISO.
- `deduplicateRedirectedAsins` — un même produit atteignable sous plusieurs ASIN
  ne remonte pas ses avis en triple.

| Champ | Devient |
| --- | --- |
| `ratingScore` | `note` |
| `reviewTitle` | `titre` — **préfixe « 5.0 out of 5 stars – » retiré** |
| `reviewDescription` | `texte` — un avis vide est écarté |
| `date` / `reviewedIn` | `date`, non normalisée |
| `isVerified` | `achat_verifie` |
| `reviewReaction` | `votes_utiles` |

### 7.3 Robustesse

| Mécanisme | Portée |
| --- | --- |
| `NB_TENTATIVES_MAX = 2`, backoff `(20 s, 60 s)` | Run en échec (statut ≠ `SUCCEEDED`, exception réseau). Attente longue **délibérée** : un échec vient le plus souvent de l'anti-bot, et réessayer aussitôt réutilise la session proxy qui vient d'être refusée. |
| Relance sans filtres, après `PAUSE_AVANT_REPLI_SECS = 20 s` | Run `SUCCEEDED` mais **sans produit**. L'URL est rejouée sans sa facette de prix ; les critères restent appliqués côté Python. Une seule fois par recherche. |
| Cycle de repli | Corpus final sous `SEUIL_MIN_PRODUITS`. Le modèle génère des recherches plus larges. **Une seule fois par exécution, sous aucune condition deux.** |
| `PARALLELISME_MAX = 3` | Runs simultanés. Chaque run a sa propre session proxy côté Apify. La valeur `1` rétablit une exécution strictement séquentielle. |

---

## 8. Qualification du corpus

### 8.1 Filtres déterministes (`filtering.filtrer_deterministe`)

- **Dédoublonnage** par ASIN, puis URL, puis titre — cumulatif sur tous les
  cycles : une même fiche remontée par deux recherches n'est comptée qu'une fois.
- **Critères du plan** re-vérifiés en Python : la facette d'URL d'Amazon est
  approximative et disparaît sur une relance sans filtres.
- Un produit **sans prix** est écarté si la recherche posait une borne de prix :
  l'absence de donnée ne vaut pas satisfaction du critère. Idem pour la note et
  le nombre d'avis.

### 8.2 Classification LLM (`filtering.classifier_produits`)

Par lots de 15, chaque fiche est confrontée au produit de référence :

| `correspondance` | Sens | Pertinence typique |
| --- | --- | --- |
| `produit_equivalent` | Même catégorie, même usage : concurrent direct. | ~1 |
| `variante` | Même famille, déclinaison notable (capacité, format, lot). | 0,6–0,9 |
| `accessoire` | Complément et non substitut : housse, câble, support. | ~0,2 |
| `hors_sujet` | Autre catégorie. | 0 |

Cette étape n'est pas cosmétique : une recherche Amazon remonte massivement des
accessoires dont le titre **nomme** le produit cherché. Sans elle, le corpus
mélange le produit et sa coque de protection.

Les produits sous `SEUIL_PERTINENCE = 0.5` sont écartés. Un produit **non
classifié** (lot en échec) est **conservé** et n'est pas confronté au seuil :
l'échec d'un appel LLM ne doit pas se traduire par une perte silencieuse de
corpus. Il est compté dans `stats.nb_produits_non_classifies` et déclenche
`LIMITE_CORPUS_*_CLASSIFIE`.

### 8.3 Ordre du corpus et choix des produits enrichis d'avis

Le corpus final est trié par **pertinence décroissante**, puis par **rang de
collecte** (l'ordre dans lequel Amazon a servi les produits pour le tri
demandé). Les `--avis` premiers produits de cette liste reçoivent leurs avis :
le budget d'avis va donc aux concurrents les plus directs, pas aux premiers
arrivés.

---

## 9. Structure du JSON de sortie

```jsonc
{
  "produit":  { "nom": "…", "description": "…", "categorie": "…" },
  "marche":   { "geo": "FR", "langue": "fr" },
  "region_couverte": true,          // faux ⇒ rien n'a été collecté, voir §4.0
  "marketplace": {                  // null quand region_couverte vaut faux
    "domaine": "amazon.fr", "code_pays": "FR", "explication": "…"
  },
  "alertes_qualite_input": [ { "type": "contradiction", "detail": "…" } ],
  "plan_recherches": [
    {
      "mots_cles": "écouteurs open ear sport", "tri": "meilleures_ventes",
      "prix_min": null, "prix_max": 80.0, "note_min": 4.0, "nb_avis_min": 100,
      "justification": "…", "url": "https://www.amazon.fr/s?k=…",
      "filtres_url": true, "est_repli": false
    }
  ],
  "produits": [
    {
      "asin": "B0…", "titre": "…", "url": "…", "image": "…",
      "prix": 59.99, "devise": "€", "prix_barre": 79.99,
      "note": 4.4, "nb_avis": 1523,
      "volume_achats_mensuel": "500+ achetés le mois dernier",
      "marque": "JBL", "vendeur": "Amazon", "note_vendeur": null,
      "nb_notes_vendeur": null, "choix_amazon": true,
      "rang_best_seller": 12, "categorie_best_seller": "High-Tech",
      "disponible": true, "livraison": "Livraison GRATUITE",
      "recherche_origine": "écouteurs open ear sport", "rang_collecte": 3,
      "correspondance": "produit_equivalent", "pertinence": 0.95,
      "avis": [
        { "note": 5, "titre": "…", "texte": "…", "date": "12 mars 2026",
          "achat_verifie": true, "votes_utiles": "21 personne(s) ont trouvé cet avis utile" }
      ]
    }
  ],
  "stats": {
    "nb_produits_collectes": 84, "nb_produits_retenus": 31,
    "nb_produits_avec_avis": 5, "nb_avis_collectes": 47,
    "nb_doublons_ecartes": 12, "nb_produits_hors_criteres": 28,
    "nb_produits_sous_seuil": 13, "nb_produits_non_classifies": 0,
    "nb_enregistrements_erreur": 1,
    "prix_min": 12.9, "prix_median": 42.0, "prix_max": 189.0, "devise": "€",
    "note_moyenne": 4.31,
    "repartition_par_correspondance": { "produit_equivalent": 18, "variante": 13 },
    "repartition_par_marque": { "JBL": 6, "Shokz": 4 },
    "repartition_par_recherche": { "écouteurs open ear sport": 14 }
  },
  "statuts_collecte": [
    { "recherche": "écouteurs open ear sport", "type_run": "produits",
      "succes": true, "message_erreur": null, "nb_items": 30, "nb_tentatives": 1 }
  ],
  "donnees_disponibles": true,
  "limites":    [ "…" ],
  "hypotheses": [ "…" ]
}
```

`limites` et `hypotheses` ne sont pas décoratives : elles sont destinées à
accompagner le corpus jusqu'à l'agent d'analyse en aval, pour qu'il ne prenne
pas un catalogue Amazon pour une photographie de tout le commerce en ligne du
pays.

---

## 10. Coût d'une exécution

| Poste | Runs | Remarque |
| --- | --- | --- |
| Recherches du plan | `NB_RECHERCHES` = **3** | Le poste le plus lent (visite de chaque fiche). |
| Relance des recherches vides | 0 à 3 | Seulement si Amazon a servi des pages vides. |
| Cycle de repli | 0 ou **1** | Seulement si le corpus reste sous le seuil. |
| Avis | `--avis` = **5** | Un run par produit. **Le principal levier de coût.** |
| Appels Claude Haiku | 4 à 6 | Sorties courtes, coût négligeable devant les actors. |

Soit **8 à 12 runs Apify** pour une exécution nominale. `--avis 0` supprime d'un
coup la moitié du budget.

⚠️ **Quota Apify.** Un compte en fin de quota **tronque les runs sans les faire
échouer** : ils remontent en `SUCCEEDED` avec un dataset incomplet. Vérifier le
quota avant toute interprétation d'un volume de produits — c'est une limite
systématiquement jointe au résultat, pour cette raison précise.

---

## 11. Limites connues et pièges

| Sujet | À savoir |
| --- | --- |
| **Portée régionale** | L'agent ne couvre que 22 pays (§4.2). Ailleurs, il refuse — c'est voulu. Là où il s'applique, il ne décrit qu'Amazon, pas tout le commerce en ligne du pays. |
| **`--domaine`** | Court-circuite la garde de couverture. Un corpus obtenu ainsi peut décrire un marché autre que celui de `--geo` : `marketplace.explication` le signale, mais rien ne l'empêche. |
| **Anti-bot** | Amazon bloque une part variable des requêtes. Un corpus court peut n'être qu'un blocage : `nb_enregistrements_erreur` et `statuts_collecte` le disent. |
| **Prix** | Non convertis, relevés à l'instant du run, variables d'un jour à l'autre. Jamais comparables entre marketplaces. |
| **Classement Amazon** | Commercial : publicité, performance vendeur, ancienneté. `rang_collecte` n'est pas un classement de qualité. |
| **Avis** | « Meilleurs avis » retenus par Amazon, quelques-uns par produit, sur 2 ans. Illustratifs, jamais représentatifs — aucune statistique de satisfaction ne doit en être tirée. |
| **Heuristiques** | `pertinence`, `correspondance`, `SEUIL_PERTINENCE`, `SEUIL_MIN_PRODUITS` ne sont validés sur aucun échantillon annoté. |
| **Table des marketplaces** | Relevé manuel des sites Amazon existants, à jour au 03/08/2026. Amazon ouvre et ferme des sites : une ligne à ajouter ou retirer dans `MARKETPLACE_PAR_PAYS` le cas échéant. |
| **Casse de l'actor** | L'identifiant est `junglee/Amazon-crawler`, avec un `A` majuscule. |

---

## 12. Réglages courants

Tout se règle dans `config.py`, sans toucher au reste du code.

| Constante | Défaut | Effet |
| --- | --- | --- |
| `NB_RECHERCHES` | 3 | Angles de recherche du plan. |
| `MAX_PRODUITS_PAR_RECHERCHE` | 30 | Produits scrapés par run. |
| `NB_PRODUITS_AVIS` | 5 | Produits enrichis d'avis (`--avis` l'écrase). |
| `NB_AVIS_PAR_PRODUIT` | 10 | Plafond réel de l'actor sur le plan gratuit. |
| `ANCIENNETE_MAX_AVIS` | `"2 years"` | Fenêtre de fraîcheur des avis. |
| `SEUIL_PERTINENCE` | 0.5 | Sévérité du filtre de pertinence. |
| `SEUIL_MIN_PRODUITS` | 5 | Déclenchement du cycle de repli. |
| `PARALLELISME_MAX` | 3 | Runs simultanés ; `1` = séquentiel. |
| `MODELE_CLAUDE` | `claude-haiku-4-5-20251001` | Les quatre étapes LLM sont mécaniques et à sortie courte. |
| `MARKETPLACE_PAR_PAYS` | 22 pays | **Périmètre d'applicabilité de l'agent.** Un pays absent = refus. |

---

## 13. Ce qui change par rapport à `amazon.py`

| | `amazon.py` | `agent_amazon/` |
| --- | --- | --- |
| Entrée | Requête en texte libre ou URL Amazon collée | `FicheProduit` + `ParametresMarche`, partagés avec les autres agents |
| LLM | OpenAI `gpt-5-nano` | Claude Haiku via `langchain-anthropic`, comme le reste du projet |
| Accès Apify | Serveur MCP hébergé | `apify-client` en direct — pas de dépendance au MCP ni à son bug d'origine sur les actors à ancien SDK |
| Recherches | Une seule | Plan de `NB_RECHERCHES` angles + cycle de repli |
| Qualification | Filtres prix / note / avis | Idem **+** classification concurrent / variante / accessoire / hors sujet |
| Sortie | Affichage texte dans le terminal | JSON structuré, avec stats, statuts, limites et hypothèses |
| Robustesse | Une relance sans facettes | Tentatives avec backoff, relance sans facettes, cycle de repli, aucune exception propagée |
| Région | Repli sur « la marketplace la plus proche » (`MA` → `amazon.fr`) | **Refus** si le pays n'a pas son propre site Amazon ; aucune adresse de livraison, comme avant |

L'ancien `amazon.py` reste à la racine du projet et fonctionne toujours ; il n'a
pas été modifié.

---

## 14. État de validation

Vérifié hors réseau sur ce portage : garde de couverture (20 pays acceptés,
`MA`/`CH`/`NG`/`BE`/`XX`/vide refusés, texte libre résolu puis refusé,
`--domaine` qui court-circuite), arrêt effectif sans aucun appel Apify ni LLM et
code de sortie 3, construction et relance des URLs (facette de prix,
marketplaces sans décimales), normalisation d'items bruts aux deux schémas
d'actor, dédoublonnage, critères du plan, seuil de pertinence, statistiques,
orchestration complète sur sources simulées (nominal, repli, plan impossible),
formatage de tous les gabarits de prompt et interface CLI.

**Non exécuté ici** : un run complet contre les actors Apify et l'API Anthropic
— il consomme du quota Apify réel. Le premier run réel se lance de préférence
avec `--geo FR --verbose --avis 0`, qui journalise le plan, les URLs et les
payloads sans engager le poste de coût des avis.
