# agent_recherche_web

Agent de **recherche web régionalisée** pour l'étude d'un produit e-commerce sur
un marché donné. À partir d'une fiche produit et d'une région d'étude, il
collecte un corpus de pages web servant deux axes d'analyse :

- **Axe 1 — consommateurs** : tests, avis éditoriaux, retours d'usage, problèmes
  récurrents évoqués par la presse et les blogs ;
- **Axe 2 — concurrence** : comparatifs (« meilleurs X 2026 »), concurrents et
  marques (y compris en vente directe, hors places de marché), argumentaires et
  éléments de positionnement.

Le module **collecte, filtre et étiquette** un corpus. Il ne l'interprète pas :
aucune analyse de sentiment, aucun relevé de points de douleur, aucun benchmark
concurrentiel, aucune synthèse. Ces traitements relèvent des modules d'analyse
en aval.

Composant isolé et réutilisable : il retourne un objet en mémoire et l'affiche en
JSON sur la sortie standard. Aucune persistance, aucun serveur, aucune interface.

---

## 1. Installation

```bash
cd agent_recherche_web
pip install -r requirements.txt
cp .env.example .env      # puis renseigner les deux clés
```

Python ≥ 3.11 requis.

`.env` :

```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_TOKEN=apify_api_...
```

`APIFY_API_TOKEN` est accepté en repli — c'est le nom utilisé par la console
Apify.

## 2. Usage

Lancement **depuis l'intérieur du dossier** (imports absolus à plat) :

```bash
python main.py \
    --nom "JBL Endurance Peak 4 Open Ear" \
    --description "Écouteurs sans fil à conduction ouverte (open ear), conçus pour le sport : maintien par crochet d'oreille, résistance à l'eau IP68, autonomie 10h + boîtier." \
    --categorie "electronics" \
    --geo MA \
    --langue fr \
    --verbose
```

| Argument | Obligatoire | Rôle |
|---|---|---|
| `--nom` | oui | Titre commercial du produit. |
| `--description` | oui | Description libre. |
| `--categorie` | non | Catégorie e-commerce. |
| `--geo` | oui | Code pays ISO-2 de la région d'étude (`FR`, `MA`, `GB`…). |
| `--langue` | oui | Code langue ISO-2 du marché (`fr`, `en`…). |
| `--sortie` | non | Fichier JSON écrit à chaque exécution. Défaut : `output.json`. |
| `--stdout` | non | Affiche **aussi** le JSON sur la sortie standard. |
| `--verbose` | non | Progression sur `stderr`. |

### Où va le résultat

Par défaut, le corpus est écrit dans **`output.json`** dans le répertoire
courant, en UTF-8, **écrasé à chaque exécution**. Une ligne de confirmation
(chemin, nombre de pages, taille) est affichée sur `stderr`.

```bash
python main.py --nom "…" --description "…" --geo FR --langue fr
# → Résultat écrit dans …/agent_recherche_web/output.json (12 page(s), 214 Ko).
```

Autre nom de fichier :

```bash
python main.py --nom "…" --description "…" --geo MA --langue fr --sortie corpus_ma.json
```

Pour chaîner le résultat vers un autre outil, `--stdout` remet le JSON sur la
sortie standard — les logs restant sur `stderr`, elle demeure parsable :

```bash
python main.py --nom "…" --description "…" --geo FR --langue fr --stdout --sortie "" | jq '.stats'
```

`--sortie ""` désactive l'écriture du fichier.

> L'écriture du fichier est un **confort de la CLI**, entièrement contenu dans
> `main.py`. `agent.rechercher_web` retourne un objet en mémoire et n'écrit
> rien : le module reste sans persistance pour un appelant programmatique.

Intégration programmatique :

```python
from agent import rechercher_web
from schemas import FicheProduit, ParametresMarche

resultat = rechercher_web(
    FicheProduit(nom="…", description="…", categorie="electronics"),
    ParametresMarche(geo="FR", langue="fr"),
)
```

`rechercher_web` **ne lève jamais d'exception** : un échec total de la collecte
retourne un `ResultatRechercheWeb` exploitable, `donnees_disponibles=False`,
statuts détaillés à l'appui.

## 3. Architecture

Un seul dossier, fichiers à plat, sens de dépendance strict et sans cycle :

```
config.py      ← (aucune dépendance interne)
schemas.py     ← config
queries.py     ← config, schemas
web_source.py  ← config, schemas
filtering.py   ← config, schemas
normalize.py   ← config, schemas
agent.py       ← config, schemas, queries, web_source, filtering, normalize
main.py        ← config, schemas, agent
```

| Fichier | Rôle |
|---|---|
| `config.py` | Toutes les constantes, chargement `.env`, logging. Aucune valeur magique ailleurs. |
| `schemas.py` | Contrats Pydantic v2 d'entrée et de sortie. |
| `queries.py` | Contrôle qualité de la fiche + plan de requêtes (chaînes LCEL) + contrôle mécanique de conformité. |
| `web_source.py` | Wrapper de l'actor `apify/rag-web-browser`, une requête = un run. |
| `filtering.py` | Dédoublonnage, exclusions de domaines, classification LLM par lots. |
| `normalize.py` | Mapping des items bruts, troncature, statistiques. Fonctions pures. |
| `agent.py` | Orchestration, parallélisme borné, contrôle de couverture et repli. |
| `main.py` | CLI. |

### Séquence d'exécution

1. **Contrôle qualité de la fiche** → `alertes_qualite_input` (informatif, ne
   bloque jamais).
2. **Dérivation du TLD** depuis `geo`, puis **plan de requêtes** :
   2 × 4 requêtes régionalisées + 2 requêtes ouvertes.
3. **Exécution des runs**, une requête par run, parallélisme borné à
   `PARALLELISME_MAX`.
4. **Filtres déterministes** (dédoublonnage, domaines exclus, longueur minimale)
   puis **classification LLM par lots**, puis seuil de pertinence.
5. **Contrôle de couverture** : tout axe sous `SEUIL_MIN_PAGES_PAR_AXE` déclenche
   **un unique** cycle de requêtes de repli (étapes 3–4). Un axe encore
   déficitaire après ce cycle est consigné dans `stats.axes_sous_couverts` — il
   n'y a jamais de second cycle.
6. **Statistiques** et construction du résultat.

--------------------------

## 4. Schéma de sortie réel de l'actor

**Relevé sur des runs d'exploration réels du 01/08/2026**, et non déduit de la
documentation. Aucun parsing du code ne repose sur un champ supposé ; les noms
de champs sont centralisés dans `config.py`.

Un item de dataset = une page :

```jsonc
{
  "crawl": {
    "httpStatusCode": 200,
    "httpStatusMessage": "OK",
    "loadedAt": "2026-08-01T18:23:12.953Z",
    "uniqueKey": "Geyg93k2kO",
    "requestStatus": "handled"
  },
  "searchResult": {                      // le résultat Google
    "title": "Écouteurs Ear Nothing - Test et avis",
    "description": "Mar 19, 2025 — Écouteurs Ear Nothing…Read more",
    "url": "https://www.runpack.fr/36569-ecouteurs-ear-nothing-test-et-avis",
    "resultType": "ORGANIC",             // ou "SUGGESTED"
    "rank": 3
  },
  "metadata": {                          // les métadonnées de la page chargée
    "title": "Écouteurs Ear Nothing - Test et avis",
    "description": "Courir en musique c'est bien…",
    "url": "https://www.runpack.fr/36569-…",
    "redirectedUrl": "https://www.runpack.fr/36569-…",
    "languageCode": "fr-FR",
    "author": "Thomas Piquart"           // ABSENT sur une partie des pages
  },
  "query": "écouteurs open ear test avis site:.fr",   // écho de la requête
  "markdown": "Écouteurs Ear Nothing - Test et avis\n\n[…"  // OU null
}
```

### Trois pièges du schéma, tous constatés en run réel

1. **`markdown` peut valoir `null`.** Quand le crawl de la page échoue
   (`crawl.httpStatusCode = 500`), l'actor émet quand même l'item, avec un
   `metadata` vide ou partiel. Le module traite ce cas comme un contenu vide,
   écarté ensuite par `MIN_CARACTERES_PAGE`.
2. **`resultType` peut valoir `SUGGESTED`.** Quand Google n'a **aucun** résultat
   organique pour la requête, il renvoie des pages de substitution. Un run de
   contrôle sur une requête volontairement introuvable a ainsi renvoyé trois
   pages `SUGGESTED` en anglais, allemand et croate — sans le moindre rapport
   avec la requête. Le champ est reporté dans `PageWeb.type_resultat_serp`, et
   sa présence ajoute une limite explicite au résultat.
3. **`metadata` peut être vide sur les crawls en échec** : l'URL est alors prise
   dans `searchResult.url`. C'est pourquoi le mapping enchaîne
   `metadata.url` → `metadata.redirectedUrl` → `searchResult.url`.

### Piège du build de l'actor

Le tag de build **`latest` de `apify/rag-web-browser` pointe sur une version de
2024** (0.0.32), dont le schéma d'entrée est incompatible : ni `scrapingTool`,
ni `serpProxyGroup`. Le build réellement exécuté par défaut est **`version-1`**
(1.0.24 au 01/08/2026). Ne pas épingler `latest` en croyant obtenir la dernière
version. Le module n'épingle aucun build et laisse Apify appliquer son défaut.

### Payload envoyé

```python
{
    "query": "<une seule requête>",       # requis
    "maxResults": 3,
    "outputFormats": ["markdown"],
    "requestTimeoutSecs": 60,
    "scrapingTool": "raw-http",
}
```

Le payload est **logué en UTF-8 avant chaque appel** (`--verbose`) : c'est le
seul moyen de vérifier qu'aucune corruption d'encodage n'affecte les accents et
que l'opérateur `site:` est bien présent là où il doit l'être.

---

## 5. Comportement observé du ciblage régional

### 5.1 L'opérateur `site:` est respecté

| Requête | Résultats |
|---|---|
| `écouteurs open ear test avis site:.fr` | 3/3 en `.fr` (runpack.fr, runfitfun.fr, amazon.fr) |
| `écouteurs open ear avis site:.ma` | 3/3 en `.ma` (jumia.ma, baseus-store.ma, jeshop.ma) |

Le filtrage par TLD fonctionne, y compris sur un TLD peu répandu. **Les accents
sont intégralement préservés** dans l'écho `query` de l'actor — le correctif
d'encodage appliqué au chargement de `config.py` est effectif.

### 5.2 Mais la SERP reste géolocalisée États-Unis / anglais

L'actor n'expose **aucun paramètre de pays ni de langue de recherche**.
`serpProxyGroup` choisit un groupe de proxies SERP (`GOOGLE_SERP` ou `SHADER`),
**pas un pays**. Les descriptions renvoyées par l'actor le confirment : dates au
format américain (`Mar 19, 2025`), libellé `Read more`.

Conséquence méthodologique, injectée systématiquement dans `limites` : **le
classement des résultats reste celui d'un utilisateur américain**, pas celui d'un
consommateur du marché étudié. Le ciblage régional est une approximation
construite au niveau des requêtes, jamais une géolocalisation réelle. Aucun
contournement par proxy n'est tenté.

### 5.3 Ce que chaque mode de ciblage rapporte réellement

Sur le produit test (écouteurs open-ear) :

| Marché | Mode | Ce qui remonte |
|---|---|---|
| FR | `tld` | Blogs et médias tech français : runfitfun.fr, gamewave.fr, actu.fr, on-mag.fr, zdnet.fr. Éditorial dense. |
| FR | `geo_keywords` | Comparatifs des grands médias (frandroid.com, 01net.com, son-video.com) et **sites de marque** (fr.jbl.com, fr.nothing.tech, bose.fr). |
| MA | `tld` | **Presque exclusivement du marchand** : baseus-store.ma, oroud.ma, jumia.ma. Aucun éditorial marocain sur ce sujet. |
| MA | `geo_keywords` | Marques présentes localement (ma.oraimo.com, huawei) **plus des comparatifs français hors marché** — correctement étiquetés `portee_regionale=false` par la classification. |
| — | `ouverte` | Filet de sécurité : remonte du contenu francophone non régionalisé. |

Deux enseignements pratiques :

- **`site:.<tld>` sur un marché émergent renvoie du commerce, pas de
  l'éditorial.** C'est une information légitime sur le marché — et non un échec
  de collecte : `web_source` produit alors `succes=True` avec `nb_pages=0` en
  ciblage régional, et ajoute la limite `LIMITE_TLD_PEU_FOURNI`.
- **Les mots-clés géographiques débordent du marché.** Sur `geo=MA, langue=fr`,
  une part du corpus est constituée de comparatifs français. Le champ
  `portee_regionale` produit par la classification est le seul moyen de les
  distinguer — il ne doit pas être ignoré en aval.

### 5.4 Mapping `geo` → TLD

Volontairement simpliste : code ISO-2 en minuscules, hors `TLD_EXCEPTIONS`
(`GB` → `uk`). Il ne couvre **ni** les TLD de second niveau (`.co.uk`,
`.com.br`), **ni** les marchés dont l'audience se concentre en `.com`. À
enrichir au besoin — c'est un point d'entrée unique dans `config.py`.

---

## 6. `raw-http` plutôt que `browser-playwright`

`SCRAPING_TOOL = "raw-http"`, retenu sur mesure.

**Ce que `raw-http` produit** sur les pages testées : Markdown exploitable
partout où le contenu est servi côté serveur — 8 100 caractères sur runpack.fr,
23 000 sur runfitfun.fr, 32 700 sur blog.son-video.com, 65 000 sur frandroid.com,
68 000 sur 20minutes.fr. Latence de run mesurée à 11–15 s.

**Où il échoue** : sur les catalogues e-commerce protégés ou entièrement rendus
côté client. Constaté en `HTTP 500`, `markdown = null` : shokz.fr,
skullcandy.eu, fnac.com, jumia.ma. Sur le run `geo=MA`, **10 pages sur 30** sont
tombées pour cette raison — c'est la principale perte de rendement du module.

**Pourquoi ne pas basculer sur `browser-playwright` :** les pages perdues sont
très majoritairement des **fiches produit marchandes**, c'est-à-dire précisément
le type de source le moins utile aux deux axes visés — les tests, avis et
comparatifs, eux, passent en `raw-http`. Un rendu navigateur coûterait plus de
temps et de calcul par run pour récupérer surtout du catalogue.

**Quand basculer** : si l'analyse aval a besoin des pages de marque et des
catalogues (relevés de prix, argumentaires commerciaux, gammes), passer
`SCRAPING_TOOL` à `"browser-playwright"` dans `config.py`. Un seul point à
modifier, aucun autre changement de code. Prévoir alors un `TIMEOUT_RUN_SECS`
plus large et un coût par run supérieur.

---

## 7. Coût observé

**Mesuré sur 48 runs réels** de `apify/rag-web-browser` (exploration + trois
exécutions complètes), compte Apify plan STARTER, tier BRONZE :

| Métrique | Valeur |
|---|---|
| Coût médian par run | **0,0081 $** |
| Coût moyen par run | **0,0102 $** |
| Coût minimum / maximum | 0,0054 $ / 0,0332 $ |
| Durée d'un run | 11–15 s (68 s sur le cas dégénéré) |
| Total des 48 runs | 0,4883 $ |

Le run le plus cher (0,0332 $, 68 s) est celui de la requête volontairement
introuvable : sans résultat organique, l'actor multiplie les tentatives sur des
pages de substitution qui échouent. **Une requête trop spécifique coûte donc
quatre fois plus cher qu'une requête ordinaire, pour un résultat inutilisable.**

Coût par exécution complète de la CLI :

| Scénario | Runs | Coût Apify |
|---|---|---|
| Sans repli | 10 | **≈ 0,08 $** |
| Repli sur un axe | 12 | ≈ 0,10 $ |
| Repli sur les deux axes | 14 | ≈ 0,11 $ |

L'actor lui-même est gratuit : ce coût est intégralement de la consommation de
plateforme Apify (unités de calcul — l'actor tourne avec 8 Go de mémoire par
défaut). L'hypothèse de départ « quelques centimes par exécution » est
**vérifiée**.

S'y ajoutent les appels Claude (`claude-haiku-4-5-20251001`) : 1 contrôle
qualité + 1 plan de requêtes + 1 appel de classification par lot de 10 pages
(+ 1 appel par axe en repli), soit **3 à 6 appels** par exécution. La
classification domine le volume de jetons — 10 pages × ~1 500 caractères
d'extrait par appel. Le coût correspondant est à évaluer sur la tarification
Anthropic en vigueur ; il n'a pas été instrumenté ici.

---

## 8. Seuils retenus

Tous dans `config.py`, ajustables sans toucher au reste du code.

| Constante | Valeur | Justification |
|---|---|---|
| `NB_REQUETES_PAR_AXE` | 4 | 2 en `tld` + 2 en `geo_keywords`, quotas égaux par axe. |
| `NB_REQUETES_OUVERTES` | 2 | Filet de sécurité si le ciblage régional ne rapporte rien. |
| `NB_REQUETES_REPLI` | 2 | Par axe déficitaire, sur **un seul** cycle. |
| `MAX_RESULTS_PAR_REQUETE` | 3 | 30 pages brutes par exécution avant filtrage. |
| `PARALLELISME_MAX` | 3 | Voir §9. `1` rétablit le séquentiel. |
| `TIMEOUT_RUN_SECS` | 300 | Large au regard des 11–15 s observés. |
| `REQUEST_TIMEOUT_SECS` | 60 | Chargement d'une page cible. |
| `NB_TENTATIVES_MAX` | 2 | Backoff 5 s puis 20 s. |
| `MIN_CARACTERES_PAGE` | 500 | Écarte les crawls échoués (`markdown = null`) et les pages sans contenu. |
| `MAX_CARACTERES_PAR_PAGE` | 20 000 | Les pages vont de 8 000 à 178 000 caractères, l'excédent étant surtout navigation et pied de page. Troncature signalée par `contenu_tronque`. |
| `TAILLE_LOT_CLASSIFICATION` | 10 | Pages par appel LLM. |
| `LONGUEUR_EXTRAIT_CLASSIFICATION` | 1 500 | Caractères transmis au classifieur. |

**Deux seuils sont des heuristiques non validées empiriquement** — aucune mesure
de précision ni de rappel sur un échantillon annoté :

| Constante | Valeur | Effet observé |
|---|---|---|
| `SEUIL_PERTINENCE` | 0,5 | Sur les trois exécutions réelles : 0 page écartée sur `geo=FR`, 3 sur `geo=MA`. Les scores observés se concentrent entre 0,5 et 0,9. |
| `SEUIL_MIN_PAGES_PAR_AXE` | 3 | Plancher de non-vacuité, pas un seuil de représentativité. Non atteint sur aucune des exécutions FR/MA — le repli n'a été validé qu'en forçant le seuil. |

### Domaines exclus

`DOMAINES_EXCLUS` retire du corpus les domaines **déjà couverts par les autres
collecteurs du projet** (Amazon, AliExpress, Reddit) et ceux sans contenu
textuel exploitable (Facebook, Instagram, Pinterest, YouTube). L'exclusion évite
le double comptage entre sources et empêche une fiche produit marchande de
passer pour un article éditorial.

Ce n'est pas théorique : sur les exécutions réelles, **4 à 11 pages par
exécution** ont été écartées à ce titre, `amazon.fr` remontant régulièrement en
tête même sur une requête `site:.fr`.

### Dédoublonnage — le piège `srsltid`

Le dédoublonnage se fait sur une URL normalisée : fragment retiré, barre oblique
finale ignorée, **et paramètres de tracking publicitaire supprimés**
(`PARAMETRES_URL_IGNORES` : `srsltid`, `utm_*`, `gclid`…).

Ce dernier point n'est pas cosmétique. Google appose un `srsltid` **différent à
chaque clic**. Sans ce retrait, une première exécution réelle avait conservé
**trois fois le même article** de `langsdom.fr`, sous trois URLs ne différant
que par ce paramètre — triplant son poids dans le corpus livré. Le retrait est
limité aux paramètres purement publicitaires : `?p=123` ou `?page=2` identifient
souvent la ressource et sont conservés. L'URL stockée dans `PageWeb.url` reste
celle de la collecte, non altérée.

---

## 9. Parallélisme

`PARALLELISME_MAX = 3`, via `concurrent.futures.ThreadPoolExecutor`.

Contrairement à une collecte Google Trends — où les sessions concurrentes se
font bloquer par l'anti-bot — **chaque run passe ici par l'infrastructure SERP
gérée d'Apify** (`serpProxyGroup=GOOGLE_SERP`, valeur par défaut conservée). Un
parallélisme modéré est donc sans risque de blocage : les trois exécutions
réelles n'ont produit aucun échec de run (48/48 `SUCCEEDED`).

Ramener `PARALLELISME_MAX` à `1` rétablit une exécution strictement séquentielle,
sans autre changement.

---

## 10. Plan de requêtes : le modèle propose, le code dispose

La chaîne LCEL produit le **texte final** de chaque requête. Le code applique
ensuite des contrôles mécaniques, **sans jamais re-prompter en boucle** :

| Contrôle | Correction appliquée |
|---|---|
| Mode `tld` sans `site:.<tld>`, ou avec un autre TLD | Tout opérateur `site:` est retiré, puis `site:.<tld>` est ajouté en fin de requête. Le TLD est **imposé par le code**, jamais choisi par le modèle. |
| Mode `geo_keywords` ou `ouverte` contenant `site:` | L'opérateur est retiré. |
| Mode `geo_keywords` sans le nom du pays | Le nom du pays (fourni par le modèle dans `nom_pays_marche`) est ajouté. Comparaison insensible à la casse et aux accents. |
| Requête reprenant le **titre produit brut** | Requête écartée — aucune reformulation mécanique n'est possible. |
| Mode de ciblage ou axe hors nomenclature | Requête écartée. |
| Requêtes en doublon | Écartées (comparaison sans accents ni casse). |
| Quota d'un couple (axe, ciblage) dépassé | Requêtes excédentaires écartées. |
| Quota non atteint | Consigné en log et en limite `LIMITE_PLAN_INCOMPLET`. Aucune re-sollicitation. |

**Limite connue de ces contrôles :** l'interdiction du titre produit brut ne
porte que sur la reprise **intégrale** du titre. Une référence partielle
(« JBL Endurance Peak concurrents France ») passe le contrôle — cas réellement
observé sur une requête de repli. C'est un compromis assumé : sur l'axe
concurrence, nommer la marque est parfois légitime, et aucune règle mécanique ne
distingue proprement les deux cas.

Exemple de plan produit (`geo=MA, langue=fr`) :

```
axe1/tld           | écouteurs open ear avis site:.ma
axe1/tld           | écouteurs open ear sport test site:.ma
axe1/geo_keywords  | écouteurs open ear prix Maroc
axe1/geo_keywords  | écouteurs open ear problème autonomie Maroc
axe2/tld           | meilleurs écouteurs open ear sport site:.ma
axe2/tld           | comparatif écouteurs open ear site:.ma
axe2/geo_keywords  | écouteurs open ear alternative Maroc
axe2/geo_keywords  | quelle marque écouteurs open ear choisir Maroc
mixte/ouverte      | écouteurs open ear conduction osseuse
mixte/ouverte      | écouteurs sport sans fil crochet oreille
```

---

## 11. Dégradation gracieuse

Le module ne s'arrête jamais sur un échec partiel.

| Situation | Comportement |
|---|---|
| Contrôle qualité de la fiche en échec | Liste d'alertes vide, traitement poursuivi. |
| Plan de requêtes ingénérable | `donnees_disponibles=False`, statuts détaillés, **aucune exception**. |
| Une partie des requêtes en échec | Poursuite avec les autres, `LIMITE_COLLECTE_PARTIELLE`. |
| **Toutes** les requêtes en échec | `donnees_disponibles=False`, listes vides, `LIMITE_AUCUNE_DONNEE`. |
| Run `SUCCEEDED` à 0 page, ciblage `tld`/`geo_keywords` | `succes=True`, `nb_pages=0`, message explicatif — **information légitime**. |
| Run `SUCCEEDED` à 0 page, ciblage `ouverte` | `succes=False` — une requête sans restriction ne peut normalement pas être stérile ; symptôme d'un problème de requête ou de proxy. |
| Un lot de classification en échec après nouvelle tentative | Pages **conservées**, `type_source`/`portee_regionale`/`pertinence` à `None`, `axes_servis` retombant sur l'axe de la requête d'origine. Ces pages **ne sont pas confrontées au seuil de pertinence**. Limite explicite ajoutée. |
| Classification totalement en échec | `LIMITE_CORPUS_NON_CLASSIFIE`. Le corpus n'est **jamais vidé** par un échec LLM. |
| Axe encore déficitaire après repli | Consigné dans `stats.axes_sous_couverts` + `LIMITE_AXES_SOUS_COUVERTS`. **Jamais de second cycle.** |

Cas particulier documenté : pour une page non classifiée issue d'une requête
**ouverte** (axe `mixte`), `axes_servis` reçoit **les deux axes**. L'axe
réellement servi est inconnu ; n'en retenir aucun retirerait la page des deux
décomptes de couverture alors qu'elle figure bien dans le corpus livré. Le choix
est signalé dans la limite correspondante.

---

## 12. Schéma de sortie du module

```jsonc
{
  "produit": { "nom": "…", "description": "…", "categorie": "electronics" },
  "marche":  { "geo": "MA", "langue": "fr" },

  "alertes_qualite_input": [
    { "type": "contradiction", "detail": "…" }
  ],

  "plan_requetes": [
    {
      "texte": "écouteurs open ear avis site:.ma",
      "axe": "axe1",                    // axe1 | axe2 | mixte
      "ciblage": "tld",                 // tld | geo_keywords | ouverte
      "justification": "…",
      "est_repli": false
    }
  ],

  "pages": [
    {
      "url": "https://…",
      "domaine": "baseus-store.ma",
      "titre": "…",
      "contenu_markdown": "…",          // tronqué à 20 000 caractères
      "contenu_tronque": true,
      "requete_origine": "écouteurs open ear avis site:.ma",
      "axe_cible": "axe1",              // axe de la requête d'origine
      "ciblage": "tld",
      "type_source": "site_marchand",   // comparatif | test_avis | article_presse |
                                        // blog | site_marque | site_marchand |
                                        // forum | autre | null
      "axes_servis": ["axe2"],          // attribué par la classification
      "portee_regionale": true,
      "pertinence": 0.6,                // 0–1, null si classification indisponible
      "marques_detectees": ["Baseus"],  // signal brut, non analysé
      "type_resultat_serp": "ORGANIC",  // ORGANIC | SUGGESTED
      "rang_serp": 2,
      "langue_page": "fr-FR"
    }
  ],

  "stats": {
    "nb_pages_collectees": 30,          // avant tout filtrage
    "nb_pages_retenues": 12,
    "nb_pages_axe1": 5,
    "nb_pages_axe2": 12,
    "repartition_par_ciblage":     { "geo_keywords": 7, "ouverte": 3, "tld": 2 },
    "repartition_par_type_source": { "site_marchand": 4, "site_marque": 3, "…": 0 },
    "repartition_par_domaine":     { "ma.oraimo.com": 2, "…": 1 },
    "axes_sous_couverts": [],
    "nb_doublons_ecartes": 1,
    "nb_pages_exclues_domaine": 4,
    "nb_pages_trop_courtes": 10,
    "nb_pages_sous_seuil": 3,
    "nb_pages_non_classifiees": 0
  },

  "statuts_collecte": [
    { "requete": "…", "succes": true, "message_erreur": null,
      "nb_pages": 3, "nb_tentatives": 1 }
  ],

  "donnees_disponibles": true,
  "limites": ["…"],
  "hypotheses": ["…"]
}
```

Les cinq derniers compteurs de `stats` complètent les champs demandés : ils
rendent explicite l'écart entre `nb_pages_collectees` et `nb_pages_retenues`,
qui serait autrement inexplicable en lecture.

---

## 13. Limites méthodologiques

Injectées **systématiquement** dans `limites`, quelles que soient les
circonstances de l'exécution.

1. **La SERP est géolocalisée États-Unis / anglais.** Le ciblage régional par
   TLD et mots-clés est une approximation ; le classement des résultats reste
   celui d'un utilisateur américain.
2. **`site:.<tld>` exclut les acteurs locaux hébergés en `.com`**, nombreux sur
   la plupart des marchés. Les mots-clés géographiques compensent
   partiellement, sans garantie d'exhaustivité.
3. **Top-N Google par requête** : le corpus n'est pas exhaustif et reflète les
   biais de référencement (SEO, contenu affilié, pages générées). Chaque page
   est un **signal à recouper, jamais un fait** ; les chiffres rencontrés (prix,
   parts de marché, classements) doivent être revalidés sur des sources
   structurées.
4. **`type_source`, `portee_regionale` et `pertinence` sont des heuristiques
   LLM** non validées sur un échantillon annoté.
5. **Redondance possible** avec les corpus des autres collecteurs malgré
   l'exclusion de domaines.
6. **Fraîcheur non garantie** : aucun filtre de date fiable sur ce mode de
   collecte.

Limites conjoncturelles ajoutées selon l'exécution : plan incomplet, collecte
partielle ou nulle, TLD peu fourni, corpus non ou partiellement classifié,
présence de résultats `SUGGESTED`, axes sous-couverts.

### Hypothèses

Injectées systématiquement dans `hypotheses` :

1. **Assimilation du produit aux requêtes retenues** — les pages portent sur la
   catégorie de besoin visée, pas nécessairement sur la référence exacte. Les
   justifications produites par la chaîne de plan y sont reproduites.
2. **Mapping `geo` → TLD appliqué**, avec la valeur effectivement utilisée.
3. **Seuils de pertinence et de couverture utilisés**, avec leurs valeurs.

---

## 14. Ce que le module ne fait pas

Volontairement, et sans point d'extension prévu à cet effet :

- aucune persistance dans la logique métier — pas de base de données, pas de
  cache, pas d'état conservé entre deux exécutions. La seule écriture disque est
  le fichier de sortie de la CLI (`output.json`), produit par `main.py` après
  coup et écrasé à chaque exécution ; `agent.rechercher_web` n'écrit rien ;
- aucun serveur web, aucune API HTTP, aucune interface ;
- aucune authentification applicative ;
- aucune suite de tests automatisés ;
- **aucune analyse de fond** : ni sentiment, ni points de douleur, ni benchmark
  concurrentiel, ni synthèse. Le module produit un corpus étiqueté ; son
  interprétation appartient aux modules d'analyse en aval ;
- **aucune affirmation sur la taille d'un marché ni sur la représentativité du
  corpus.** Un axe sous-couvert ou un TLD sans résultat signale une absence de
  *contenu indexé*, jamais une absence de marché.
