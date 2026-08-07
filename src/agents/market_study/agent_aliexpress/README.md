# agent_aliexpress_api — collecte de prix par SKU sur AliExpress, région par région

Module autonome en ligne de commande. À partir d'une fiche produit et d'une
**région d'étude** — pays de livraison, devise, langue —, il interroge l'API
officielle AliExpress (méthodes Dropshipping) et retourne les produits
correspondants, leurs prix par SKU dans la devise demandée, des statistiques
descriptives et l'appareil critique de la collecte (statuts, limites,
hypothèses).

Le module **collecte et qualifie**. Il n'interprète pas : aucune analyse de
marché, aucune comparaison concurrentielle, aucune conversion de change.

---

## 1. La raison d'être : le prix dépend de la région

Un même produit, relevé le même jour, n'a pas le même prix selon le pays de
livraison. Ce n'est pas un artefact de conversion, c'est une tarification
distincte. Mesure du 03/08/2026 sur l'article `1005008784423024`, SKU
`5:361386;14:29#Black-1PCS`, relevé à quelques minutes d'intervalle :

| Région d'étude | Prix de vente | Stock annoncé |
|---|---|---|
| FR / EUR / fr | 6,19 EUR | 2 |
| MA / MAD / fr | 142,03 MAD | 1 988 |

Au taux de change du marché, 6,19 EUR valent environ 67 MAD. Le prix marocain
est plus du double. **Les prix, les remises et les stocks sont régionaux** : il
n'existe pas de « prix AliExpress » d'un produit, seulement un prix pour un
triplet {pays, devise, langue} donné, à un instant donné.

D'où les règles non négociables du module :

- la région d'étude est **obligatoire** et n'a **aucune valeur par défaut**,
  nulle part — ni dans le code, ni dans la configuration. Une région absente ou
  mal formée arrête l'exécution avant tout appel API. C'est la seule erreur
  bloquante ;
- le triplet est propagé à **chaque** appel (`countryCode` / `currency` /
  `local` en recherche, `ship_to_country` / `target_currency` /
  `target_language` en détail) ;
- **chaque ligne de prix porte son contexte** : pays, devise, langue,
  horodatage UTC, méthode API d'origine, et le pays de livraison confirmé par
  l'API quand elle le renvoie ;
- la devise de la réponse est **contrôlée** contre la devise demandée. Toute
  divergence exclut la ligne et produit une anomalie explicite — jamais une
  correction ni une conversion silencieuse ;
- une étude multi-régions = **plusieurs exécutions séparées**, une par région.
  Le module n'agrège jamais deux régions et ne convertit aucun montant.

---

## 2. Installation

```bash
cd agent_aliexpress_api
pip install -r requirements.txt
```

Python ≥ 3.11 requis. Dépendances : `httpx`, `pydantic` v2, `langchain` +
`langchain-core` + `langchain-anthropic`, `python-dotenv`.

### Variables d'environnement

Les identifiants se lisent **exclusivement** dans un fichier `.env` (à la racine
du projet ou du module) ou dans l'environnement. Jamais en argument de ligne de
commande, jamais en dur dans le code. Un `.env.example` accompagne le module.

```dotenv
ANTHROPIC_API_KEY=
ALIEXPRESS_APP_KEY=540792
ALIEXPRESS_APP_SECRET=
ALIEXPRESS_ACCESS_TOKEN=
ALIEXPRESS_REFRESH_TOKEN=
```

Si `ALIEXPRESS_APP_KEY` ou `ALIEXPRESS_APP_SECRET` manquent, ou si aucun token
n'est disponible ni dans `.env` ni dans `.tokens.json`, l'exécution s'arrête au
démarrage avec la liste exacte des variables attendues.

Le module **ne crée jamais d'application** et **ne génère jamais de nouveaux
identifiants**. Seul le rafraîchissement de l'`access_token` lui appartient.

### `.tokens.json`

Après un rafraîchissement, le module écrit `agent_aliexpress_api/.tokens.json` :

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "rafraichi_le": "1785780..."
}
```

- C'est la **seule écriture disque** du module, et ce n'est pas une donnée
  d'étude : c'est un état de session technique.
- Ce fichier **prime sur `.env`**, qui n'est jamais modifié. Pour repartir des
  valeurs de `.env`, supprimer `.tokens.json`.
- Il est dans `.gitignore`, au même titre que `.env`.

---

## 3. Utilisation

```bash
python main.py \
    --nom "Ceinture lombaire double traction" \
    --description "Ceinture de soutien lombaire avec double sangle de traction réglable…" \
    --categorie "sante-bien-etre" \
    --geo MA --langue fr --devise MAD
```

| Argument | Obligatoire | Rôle |
|---|---|---|
| `--nom` | oui | Titre commercial du produit |
| `--description` | oui | Description libre |
| `--categorie` | non | Catégorie e-commerce |
| `--geo` | **oui** | Pays de livraison, ISO-2 majuscule (`MA`, `FR`, `US`) |
| `--langue` | **oui** | Langue du marché, ISO-2 minuscule (`fr`, `en`) |
| `--devise` | **oui** | Devise d'affichage, ISO-4217 (`MAD`, `EUR`, `USD`) |
| `--verbose` | non | Progression sur `stderr` |

Le résultat est écrit en JSON indenté sur **stdout** ; les traces vont sur
**stderr**. `python main.py … > resultat.json` produit donc un fichier propre.

Omettre `--devise` ou fournir un code mal formé échoue immédiatement :

```
$ python main.py --nom X --description Y --geo MAR --langue fr --devise MAD
Région d'étude invalide (--geo='MAR', --langue='fr', --devise='MAD').
Champ(s) en cause : geo.
Formats attendus : --geo pays ISO-2 (ex. MA, FR, US), --langue ISO-2 minuscule
(ex. fr, en), --devise ISO-4217 (ex. MAD, EUR, USD).
Aucune valeur par défaut n'existe : la région conditionne les prix collectés et
doit être explicite.
```

---

## 4. Fonctionnement

```
contrôle qualité de la fiche (LLM, ne bloque jamais)
  → dérivation de 2 à 4 requêtes marketplace (LLM, repli déterministe)
    → PHASE A : aliexpress.ds.text.search, requêtes × pages
      → dédoublonnage par itemId + sélection déterministe
        → PHASE B : aliexpress.ds.product.get sur la seule sélection
          → normalisation, contrôles, statistiques
```

Le découpage en deux phases est une **contrainte de quota** : le détail coûte un
appel par produit. Il n'est payé que pour les produits retenus.

Exécution strictement **séquentielle** : maîtrise du quota, lisibilité des
statuts, et de toute façon la méthode de recherche est trop instable pour
qu'une rafale d'appels concurrents apporte quoi que ce soit (§ 6).

### Règle de sélection de la phase B

1. écarter les produits dont le titre partage moins de
   `SEUIL_SIMILARITE_TITRE` (0,25) des mots significatifs d'une requête — mots
   de moins de 3 caractères exclus, accents repliés. La mesure est la part des
   mots de la **requête** retrouvés dans le titre, et non l'inverse : un titre
   AliExpress compte couramment vingt mots, une mesure symétrique serait
   écrasée ;
2. trier par nombre de commandes décroissant, puis note décroissante, puis
   `item_id` — ce dernier critère ne départage rien de significatif, il garantit
   un ordre stable d'une exécution à l'autre ;
3. conserver les 15 premiers.

Si le filtre vide la sélection, il est **neutralisé** pour cette exécution et la
limite `LIMITE_SELECTION_NON_FILTREE` est consignée : mieux vaut détailler des
produits possiblement hors sujet, et le dire, que ne rien détailler.

Ce seuil est une **heuristique non validée empiriquement** : aucun échantillon
annoté n'a servi à le calibrer.

### Dégradation gracieuse

| Incident | Comportement |
|---|---|
| Échec de la chaîne LLM de requêtes | Repli déterministe : le nom du produit devient la requête unique, limite consignée, la collecte continue |
| Échec du contrôle qualité de la fiche | Liste d'alertes vide, aucun blocage |
| Échec d'une requête de recherche | Les autres requêtes se poursuivent, `LIMITE_PHASE_A_PARTIELLE` |
| Échec de la phase B | La phase A est retournée seule, `LIMITE_PHASE_B_ABSENTE` |
| Échec total | `donnees_disponibles=false`, listes vides, statuts détaillés, **aucune exception** |
| Refresh token expiré | Statut `reautorisation_oauth_requise`, collecte interrompue immédiatement (aucune reprise ne peut aboutir) |

### Zéro résultat n'est pas une erreur

Une réponse valide sans aucun produit produit `succes=true`, `nb_items=0` et un
message explicatif : soit aucun produit du programme dropshipping ne correspond
à la requête, soit aucun n'est livrable dans la région. C'est une information
sur le marché. La distinction avec un échec technique est critique pour
interpréter le biais de couverture en aval — elle est portée par le champ
`succes` des statuts, jamais par le seul comptage d'items.

---

## 5. Schéma réel des réponses (relevé du 03/08/2026)

Tout ce qui suit a été constaté sur la passerelle réelle, et non déduit de la
documentation. Le mapping de `normalize.py` ne lit **que** ces champs.

### 5.1 Passerelles et signature

| Usage | URL |
|---|---|
| Méthodes métier `aliexpress.ds.*` | `https://api-sg.aliexpress.com/sync` (POST, paramètres en query string) |
| Authentification | `https://api-sg.aliexpress.com/rest/auth/token/create` et `/refresh` |

Signature HMAC-SHA256, hexadécimal majuscule :

```python
base = "".join(k + params[k] for k in sorted(params))
if chemin_rest:                 # uniquement pour /rest/auth/*
    base = chemin_rest + base   # ex. "/auth/token/refresh" + concat
signature = hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()
```

Paramètres système : `method` (pour `/sync`), `app_key`, `access_token`
(méthodes métier uniquement), `sign_method=sha256`, `timestamp` (epoch
millisecondes), puis `sign`. **Le secret ne sert qu'au calcul local** : il
n'apparaît dans aucune requête ni aucun log.

### 5.2 `aliexpress.ds.text.search`

Paramètres : `keyWord`, `countryCode`, `currency`, `local`, `pageSize`,
`pageIndex`.

```json
{"aliexpress_ds_text_search_response": {
  "code": "00",
  "data": {
    "pageIndex": 1, "pageSize": 5, "totalCount": 8000,
    "products": {"selection_search_product": [{
      "itemId": "1005006167563587",
      "title": "Ceinture de soutien lombaire pour le dos, Double traction réglable…",
      "itemUrl": "//www.aliexpress.com/item/1005006167563587.html?skuId=…",
      "itemMainPic": "https://ae-pic-a1.aliexpress-media.com/kf/….jpg",
      "score": "5.0", "evaluateRate": "100.0", "orders": "5",
      "cateId": "66,200001355,200368146,200001427",
      "targetSalePrice": "15.29", "targetOriginalPrice": "30.58",
      "targetOriginalPriceCurrency": "EUR", "salePriceFormat": "15,29€",
      "originMinPrice": "{\"shipToCountry\":\"FR\",\"currencyCode\":\"EUR\",\"formatPrice\":\"15,29€\",…}",
      "originalPrice": "232.37", "salePrice": "116.19", "salePriceCurrency": "CNY",
      "discount": "0%"
    }]}
  }
}}
```

#### ⚠️ Piège régional — CONFIRMÉ

`salePrice` et `originalPrice` sont libellés **dans la devise du vendeur**
(`salePriceCurrency` valant `CNY`, et parfois `USD` — la valeur n'est même pas
constante d'un produit à l'autre), quelle que soit la devise demandée. Seuls
`targetSalePrice`, `targetOriginalPrice`, `targetOriginalPriceCurrency` et
`salePriceFormat` sont exprimés dans la devise d'étude.

Sur l'exemple ci-dessus : `salePrice=116.19 CNY` contre
`targetSalePrice=15.29 EUR`. Consommer `salePrice` par erreur détruirait le
ciblage régional. **Les clés `salePrice` et `originalPrice` ne sont pas même
définies dans `config.py`**, afin qu'aucun code du module ne puisse les lire.

`originMinPrice` est une **chaîne contenant du JSON imbriqué**, qui porte le
pays de livraison réellement appliqué (`shipToCountry`) : c'est le seul contrôle
indépendant du ciblage régional disponible en phase A. Le module le lit et le
recopie dans `contexte.pays_livraison_confirme`.

Autres pièges de champs :

- `orders` est une chaîne mise en forme : `"5"`, `"3,000+"`, ou vide. Le
  suffixe `+` marque un palier : la valeur retenue est un **plancher**, jamais
  un compte exact ;
- `score` et `evaluateRate` peuvent être des chaînes vides ;
- `cateId` est une liste d'identifiants jointe par des virgules ;
- `itemUrl` est **protocole-relatif** (`//www.aliexpress.com/…`) : le module
  rétablit `https:` ;
- `discount` est **incohérent** avec les prix cibles — observé à `"0%"` sur un
  produit affichant −50 % entre `targetOriginalPrice` et `targetSalePrice`. Le
  champ n'est pas exploité, la remise est recalculée.

### 5.3 `aliexpress.ds.product.get`

Paramètres : `product_id`, `ship_to_country`, `target_currency`,
`target_language`.

```json
{"aliexpress_ds_product_get_response": {
  "rsp_code": 200, "rsp_msg": "Call succeeds",
  "result": {
    "ae_item_base_info_dto": {
      "subject": "Ceinture de soutien lombaire…", "sales_count": "5",
      "evaluation_count": "3", "avg_evaluation_rating": "5.0",
      "product_status_type": "onSelling", "category_id": 200001427,
      "product_id": 1005006167563587, "currency_code": "CNY",
      "mobile_detail": "…", "detail": "…"
    },
    "ae_item_sku_info_dtos": {"ae_item_sku_info_d_t_o": [{
      "sku_id": "12000036083778130",
      "sku_attr": "14:200211869#Back Lumbar Belt;491:200000313#XL",
      "sku_price": "31.19", "offer_sale_price": "15.59",
      "currency_code": "EUR", "sku_available_stock": 63,
      "price_include_tax": true,
      "ae_sku_property_dtos": {"ae_sku_property_d_t_o": [
        {"sku_property_name": "Couleur", "sku_property_value": "Rose rouge"},
        {"sku_property_name": "Taille", "sku_property_value": "Moyen"}
      ]}
    }]},
    "logistics_info_dto": {"delivery_time": 7, "ship_to_country": "FR"},
    "ae_store_info": {…}, "ae_item_properties": {…},
    "ae_multimedia_info_dto": {…}, "package_info_dto": {…}
  }
}}
```

#### ⚠️ Deuxième piège de devise — au niveau du détail

`ae_item_base_info_dto.currency_code` vaut **`CNY` même sur une demande
MA/MAD** : c'est la devise du vendeur. Seul le `currency_code` **du SKU** porte
la devise d'étude. Le contrôle de devise du module s'effectue donc
**exclusivement au niveau du SKU**.

`logistics_info_dto.ship_to_country` confirme le pays de livraison appliqué :
contrôle indépendant du ciblage régional en phase B.

`sku_price` est le prix barré, `offer_sale_price` le prix de vente.

### 5.4 Détection de succès — hétérogène

| Méthode | Succès | Échec observé |
|---|---|---|
| `text.search` | `code == "00"` (**chaîne**) | `code == "NGSELECTION_SEARCH_ERROR"`, sans bloc `data` |
| `product.get` | `rsp_code == 200` (**entier**) | `rsp_code == 605`, `rsp_msg == "ITEM_ID_NOT_FOUND"`, sans bloc `result` |

Toute autre forme est traitée comme un échec explicite, jamais comme un vide.

### 5.5 Écarts constatés avec la spécification fournie

Deux points des faits transmis ne correspondent pas au comportement réel. Le
code suit le comportement réel, la forme annoncée restant acceptée en repli.

**a) Forme des erreurs de la passerelle.** Annoncée au niveau racine :

```json
{"type": "ISV", "code": "IllegalAccessToken", "message": "…"}
```

Réellement observée le 03/08/2026, sur les deux méthodes, avec un token
volontairement invalidé — l'erreur est **enveloppée** et la clé du message est
`msg`, pas `message` :

```json
{"error_response": {"type": "ISV", "code": "IllegalAccessToken",
                    "msg": "The specified access token is invalid or expired",
                    "request_id": "…", "_trace_id_": "…"}}
```

Conséquence : lire la forme racine seule aurait fait passer un token expiré pour
une « réponse de forme inattendue », et **l'auto-refresh ne se serait jamais
déclenché**. Le module lit `error_response` en priorité, la racine en repli, et
accepte les deux clés de message.

**b) Le champ `discount`** est inexploitable (§ 5.2) : la remise est recalculée
à partir des seuls prix cibles.

---

## 6. Fiabilité de la recherche — instabilité mesurée

`aliexpress.ds.text.search` échoue fréquemment en `NGSELECTION_SEARCH_ERROR`
sur des requêtes et des régions pourtant valides, sans changer un paramètre
entre deux appels.

Mesures du 03/08/2026, même requête, même région (FR/EUR) :

| Espacement des appels | Appels | Succès |
|---|---|---|
| 0,5 s | 12 | **0** |
| 10 s | 6 | 2 |
| 30 s | 5 | 1 |

Une combinaison FR/EUR qui venait de réussir a échoué douze fois de suite deux
minutes plus tard, puis a de nouveau fonctionné. **L'échec est indépendant de
l'espacement** : ce n'est pas un plafond de débit que l'on pourrait respecter,
mais une instabilité de la passerelle par fenêtres de quelques minutes.

Conséquences sur la conception :

- politique de reprise dédiée aux erreurs transitoires : jusqu'à **5
  tentatives**, attentes de 10 s, 25 s, 45 s puis 60 s — seule l'insistance dans
  le temps traverse ces fenêtres ;
- pause entre recherches maintenue à 3 s, par prudence résiduelle et non comme
  remède : l'espacer davantage n'apporterait rien et allongerait l'exécution ;
- `product.get` n'a **jamais** montré ce comportement et conserve la politique
  générale (2 tentatives, 5 s puis 20 s).

Une requête peut malgré tout rester en échec. La couverture d'une exécution
n'est donc **pas reproductible à l'identique**, et un écart de volume entre deux
exécutions peut n'être qu'un artefact de disponibilité de la passerelle. Cette
limite est injectée dans chaque résultat.

---

## 7. Quotas, budget d'appels et durée observée

Aucune limite n'est affichée dans la console développeur (« API Call Limit »
vide). Des sources secondaires citent ~5 000 requêtes/jour, **non confirmées**.
Tout code d'erreur évoquant un dépassement de flux (fragments `flow`, `limit`,
`quota`, `frequency`, `too many`, `traffic`) est traité comme un échec explicite
avec un message dédié ; cette détection est heuristique, faute de liste
officielle.

### Budget nominal

| Poste | Plafond |
|---|---|
| Requêtes marketplace | 4 |
| Pages par requête | 2 (20 résultats par page) |
| Phase A | ≤ 8 appels |
| Phase B | ≤ 15 appels |
| Rafraîchissement du token | 1 appel d'authentification |
| **Total nominal** | **≤ 24 appels** |

Les nouvelles tentatives sur erreur transitoire **s'ajoutent** à ce nominal.
C'est un choix assumé : sans elles, l'instabilité du § 6 ferait échouer la
moitié des requêtes. Le compte réel est reporté dans
`stats.nb_appels_api`.

Une requête s'arrête avant la page 2 si la page 1 renvoie moins de 20 items.

### Mesures réelles de bout en bout (03/08/2026)

| Exécution | Appels métier | Durée | Produits | Détaillés | SKU |
|---|---|---|---|---|---|
| FR / EUR / fr | 28 | 3 min 35 s | 149 | 15 | 82 |
| MA / MAD / fr | 33 | 5 min 34 s | 151 | 15 | 78 |
| FR / EUR / fr (reprise) | 35 | 7 min 20 s | 130 | 15 | 82 |

L'écart au nominal (23) vient entièrement des reprises sur
`NGSELECTION_SEARCH_ERROR`. La durée est dominée par les attentes de reprise,
non par les appels eux-mêmes (~1,5 s chacun).

La troisième exécution illustre le comportement en cas d'échec persistant :
une page de recherche est restée en échec après les 5 tentatives, la collecte
s'est poursuivie sur les autres requêtes, `LIMITE_PHASE_A_PARTIELLE` a été
consignée et le statut correspondant porte `succes=false` avec le code
d'erreur. Elle illustre aussi la non-reproductibilité annoncée au § 6 : même
fiche, même région, 149 produits au premier passage contre 130 au second, à
25 minutes d'intervalle. **Un écart de volume entre deux exécutions ne dit rien
du marché** — seulement de la disponibilité de la passerelle.

---

## 8. Validation régionale de bout en bout

Deux exécutions sur la même fiche produit, à quelques minutes d'intervalle.

**Prix d'annonce (phase A), dans la devise d'étude :**

| | FR / EUR | MA / MAD |
|---|---|---|
| min | 1,16 | 35,87 |
| médiane | 10,49 | 153,22 |
| max | 50,39 | 843,35 |

**Mêmes produits, prix différents** — 66 produits communs aux deux exécutions :

| itemId | FR | MA | Remise FR | Remise MA |
|---|---|---|---|---|
| 1005012646738506 | 15,69 EUR | 344,36 MAD | 49,94 % | 50,00 % |
| 1005012801277579 | 8,79 EUR | 258,34 MAD | 57,00 % | 52,00 % |
| 1005006237135770 | 24,39 EUR | 354,46 MAD | 67,00 % | 50,00 % |

**Mêmes SKU, prix et stocks différents** — phase B :

| itemId / SKU | FR | MA |
|---|---|---|
| 1005008784423024 · `5:361386;14:29#Black-1PCS` | 6,19 EUR — stock 2 | 142,03 MAD — stock 1 988 |
| 1005008098602910 · `491:200000314#3XL` | 17,79 EUR — stock 44 996 | 293,59 MAD — stock 3 |
| 1005010700679942 · `14:193` | 4,19 EUR — stock 29 | 97,55 MAD — stock 29 |

Aucun de ces couples ne se déduit de l'autre par un taux de change constant, et
la profondeur de remise elle-même varie par région. Chaque ligne porte le bon
contexte : `pays_livraison`, `devise`, `langue`, `horodatage_utc`,
`methode_api`, et `pays_livraison_confirme` renvoyé par l'API (`FR` sur le run
FR, `MA` sur le run MA).

Le paramètre `local` est composé `langue_PAYS` (`fr_MA`). Vérification faite :
`fr_MA` et `fr_FR` sur une même demande MA/MAD renvoient des résultats
identiques — c'est `countryCode` qui pilote le ciblage, `local` ne portant que
la langue d'affichage.

---

## 9. Cycle de vie des tokens

Statut d'application **« Test »** (constaté en console le 03/08/2026) :

| Jeton | Durée | Traitement |
|---|---|---|
| `access_token` | 24 h | Rafraîchi automatiquement par le module |
| `refresh_token` | 48 h | **Hors de portée du module** : sa péremption exige un clic humain |

### Auto-refresh — validé

Sur `IllegalAccessToken`, le module appelle `/rest/auth/token/refresh`, réécrit
`.tokens.json` et **rejoue l'appel une seule fois**, sans attente ni tentative
consommée. Si l'erreur persiste, l'échec est définitif. Test du 03/08/2026 avec
un `access_token` volontairement invalidé : statut `succes=true`, 2 appels
métier, aucune boucle.

### Ré-autorisation OAuth — procédure humaine

Si le refresh token est expiré ou invalide, le module produit un statut d'étape
`reautorisation_oauth_requise` contenant l'URL à ouvrir, **interrompt
immédiatement la collecte** — aucune reprise ne peut aboutir — et ne boucle
jamais. Test du 03/08/2026 avec un refresh token invalide : 1 appel métier, puis
arrêt.

Pour ré-amorcer une session :

1. ouvrir dans un navigateur, connecté au compte AliExpress de l'application :

   ```
   https://api-sg.aliexpress.com/oauth/authorize?response_type=code&force_auth=true&redirect_uri=<URI déclarée dans la console>&client_id=540792
   ```

2. autoriser l'application. Le navigateur est redirigé vers l'URI déclarée, avec
   un paramètre `code=…` dans l'URL ;
3. échanger ce code depuis un interpréteur Python, dans le dossier du module :

   ```python
   from auth import creer_token
   print(creer_token("LE_CODE_RECUPERE", redirect_uri="<URI déclarée dans la console>"))
   ```

   En cas de succès, `.tokens.json` est réécrit et les exécutions reprennent
   normalement. `creer_token` n'est **jamais** appelée automatiquement :
   obtenir le code exige une action humaine.

Le code d'autorisation est à usage unique et de courte durée : l'échange doit
suivre le clic sans tarder.

---

## 10. Format de sortie

Objet `ResultatCollecteAliExpressAPI` :

| Champ | Contenu |
|---|---|
| `produit`, `marche` | Entrées, recopiées telles quelles |
| `alertes_qualite_input` | Anomalies de la fiche, signalées sans correction |
| `requetes`, `justification_requetes` | Requêtes marketplace retenues et leur motivation |
| `produits` | Phase A dédoublonnée — prix d'annonce dans la devise d'étude, avec contexte régional |
| `produits_detailles` | Phase B — prix par SKU, stocks, attributs lisibles, avec contexte régional |
| `stats` | min/médiane/max des prix d'annonce **et** des prix SKU, totaux annoncés par requête, nombre d'appels réels |
| `statuts_collecte` | Un statut par appel et par contrôle : `recherche`, `detail`, `controle_devise`, `controle_prix`, `controle_region`, `strategie`, `reautorisation_oauth_requise` |
| `donnees_disponibles` | Faux si aucun produit n'a pu être collecté |
| `limites`, `hypotheses` | Appareil critique, systématiquement injecté |

Le prix d'annonce de la phase A est celui du **SKU le moins cher** du produit ;
les prix par SKU de la phase B font foi pour toute lecture fine.

Les accents sont préservés (`sys.stdout.reconfigure(encoding="utf-8")` au
chargement de `config.py`) : le JSON de sortie ne contient aucune séquence
échappée.

---

## 11. Limites méthodologiques

Injectées dans chaque résultat, à lire avant toute exploitation.

1. **Biais de couverture.** L'API Dropshipping n'expose que les produits
   éligibles au programme dropshipping **et** livrables dans la région demandée.
   L'absence d'un produit dans cette collecte n'est **en aucun cas** un signal
   d'absence de ce produit sur le marché.
2. **Prix de référence, pas prix perçu.** Les montants excluent Welcome Deals,
   coupons panier et promotions de session. Le prix effectivement payé peut être
   inférieur. L'écart avec une source de scraping est un **signal d'intensité
   promotionnelle**, pas une erreur de collecte.
3. **Instantané horodaté.** Prix et stocks valent pour le seul triplet demandé,
   à l'instant du relevé. Toute comparaison exige le même triplet et un
   horodatage proche.
4. **Aucune conversion de change.** L'API ne fournit pas de taux. Les montants
   ne sont comparables qu'au sein d'une même exécution.
5. **Quota journalier non confirmé** (§ 7).
6. **Instabilité de la recherche** (§ 6) : la couverture n'est pas reproductible
   à l'identique.
7. **Sélection de la phase B heuristique**, non validée empiriquement (§ 4).
8. **`discount` inexploitable** (§ 5.2).

Limites conditionnelles, ajoutées selon le déroulé : requêtes non optimisées
(repli sans LLM), phase A ou B partielle, divergence de devise, divergence de
pays de livraison, remise supérieure à 60 %, prix de vente supérieur au prix de
base, filtre de similarité neutralisé, aucune donnée collectée.

**Hypothèses** systématiquement consignées : assimilation du produit aux
requêtes retenues ; règle de sélection de la phase B ; interprétation du prix
d'annonce comme prix du SKU le moins cher ; comparabilité approximative des
notes entre régions.

Ce module ne produit **jamais** d'affirmation sur la taille d'un marché à partir
de cette collecte.

---

## 12. Organisation du code

Un seul dossier, fichiers à plat, aucun sous-package, aucun `__init__.py`.
Imports absolus à plat, lancement depuis le dossier.

| Fichier | Rôle | Dépend de |
|---|---|---|
| `config.py` | Constantes, `.env`, plafonds, schéma réel, libellés, logging | — |
| `schemas.py` | Modèles Pydantic d'entrée et de sortie | `config` |
| `strategy.py` | Contrôle qualité de la fiche + requêtes marketplace (LangChain) | `config`, `schemas` |
| `auth.py` | Signature, refresh, `.tokens.json` | `config` |
| `aliexpress_source.py` | Appels `text.search` / `product.get`, erreurs, reprises | `config`, `schemas`, `auth` |
| `normalize.py` | Mapping brut → modèles, contrôles de devise et de prix, stats | `config`, `schemas` |
| `agent.py` | Orchestration de bout en bout | `config`, `schemas`, `strategy`, `aliexpress_source`, `normalize` |
| `main.py` | Point d'entrée CLI | `config`, `schemas`, `agent` |

Sens de dépendance unique, aucun import circulaire.

### Hors périmètre, par construction

Aucune persistance de données d'étude (base, ORM, fichier de résultats, cache
disque) — `.tokens.json` est un état de session technique, pas une donnée
d'étude. Aucun serveur web, aucune interface, aucun test automatisé. Aucune
analyse : ni sentiment, ni insight, ni comparaison concurrentielle, ni
réconciliation avec une autre source, ni conversion de devises. Aucun appel aux
API de commande, logistique ou fret. Aucun scraping : l'API officielle
exclusivement.

Le modèle LLM (`claude-haiku-4-5-20251001`) est une constante unique de
`config.py`, utilisée par les deux seules chaînes LCEL du module.
