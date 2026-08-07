# agent_meta_ads

Agent CLI de collecte **Meta Ads Library** : une fiche produit et un pays
d'étude en entrée, un corpus d'annonces concurrentes qualifié — annonceurs,
créatifs, appels à l'action, destinations de clic, longévité de diffusion — en
JSON en sortie.

> La bibliothèque publicitaire de Meta n'a pas de « pays non couverts » : elle
> expose les annonces diffusées **partout**, `MA` comme `FR`. Le module ne
> refuse donc jamais une région pour cause de périmètre — seulement une région
> qu'il n'a pas su résoudre en un pays. Voir §4.

C'est le portage de l'ancien script `metaads.py` (racine du projet) dans
l'architecture des autres collecteurs du projet — `agent_tendances`,
`agent_reddit`, `agent_recherche_web`, `agent_aliexpress_api`, `agent_amazon` —
avec le même contrat d'entrée, le même style de sortie et le même appareil
critique (`statuts_collecte`, `limites`, `hypotheses`).

---

## 1. Ce que fait le module — et ce qu'il ne fait pas

**Il fait :**

- résoudre une région d'étude en **pays de diffusion** (`MA`, `Casablanca` →
  `MA`, `monde` → `ALL`) ;
- transformer une fiche produit en **plan de recherches** sur la bibliothèque
  publicitaire (mots-clés dans la langue des annonces, mode d'appariement,
  statut de diffusion) ;
- exécuter ces recherches via l'actor Apify `apify/facebook-ads-scraper`, une
  recherche = un run, avec **élargissement** des recherches restées vides ;
- surveiller en plus, sur demande, des **annonceurs désignés** par URL de Page ;
- **rapprocher les reprises d'un même créatif**, qu'un annonceur diffuse sous
  des dizaines d'identifiants d'annonce (§8.1) ;
- **qualifier** chaque annonce par rapport au produit de référence (concurrent
  direct / catégorie proche / accessoire / hors sujet) ;
- calculer la **longévité de diffusion** de chaque annonce, et livrer le tout
  avec ses statistiques, ses statuts de collecte, ses limites et ses hypothèses.

**Il ne fait pas :**

- **aucune analyse ni recommandation** — c'est un collecteur, pas un analyste ;
- **aucune mesure de performance** : Meta ne publie ni portée, ni dépense, ni
  ciblage pour les annonces commerciales. Rien dans ce corpus ne dit qu'une
  annonce a marché (§11) ;
- **aucun repli sur un pays par défaut** ni sur « tous les pays » quand la région
  n'est pas résolue ;
- **aucun téléchargement de créatif** : seules les URLs d'images sont relevées,
  et les vidéos ne le sont pas (§7.2) ;
- aucune boucle infinie de rattrapage : au plus **un** cycle de repli.

---

## 2. Installation

```bash
pip install -r requirements.txt
cp .env.example .env      # puis renseigner les deux clés
```

| Variable | Usage |
| --- | --- |
| `ANTHROPIC_API_KEY` | Contrôle qualité de la fiche, résolution de région, plan de recherches, classification des annonces. |
| `APIFY_TOKEN` | Actor `apify/facebook-ads-scraper`. `APIFY_API_TOKEN` est accepté en repli. |

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
    --geo MA \
    --langue fr \
    --verbose
```

| Option | Défaut | Rôle |
| --- | --- | --- |
| `--nom` | requis | Titre commercial du produit. |
| `--description` | requis | Description libre. |
| `--categorie` | — | Catégorie e-commerce. |
| `--geo` | requis | Code ISO-2, **lieu en texte libre** (« Maroc », « Casablanca »), ou `ALL` pour tous les pays. |
| `--langue` | requis | Langue du marché, ISO-2. |
| `--annonceur` | — | URL de Page Facebook à surveiller directement. **Répétable** : un run par URL. |
| `--annonces` | `30` | Plafond d'annonces **par recherche**. L'actor est facturé à l'annonce : c'est le levier de coût. |
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
| `3` | **Région non résolue en un pays** : rien n'a été collecté, rien n'a été facturé. |

### En bibliothèque

```python
from agent import rechercher_meta_ads
from schemas import FicheProduit, ParametresMarche

resultat = rechercher_meta_ads(
    FicheProduit(nom="…", description="…", categorie="electronics"),
    ParametresMarche(geo="MA", langue="fr"),
    urls_annonceurs=["https://www.facebook.com/nike"],   # optionnel
    max_annonces_par_recherche=20,
)
if not resultat.region_couverte:                       # à tester en premier
    print(resultat.limites[1])                         # motif exact du refus
else:
    print(resultat.pays.code_pays, len(resultat.annonces))
```

`rechercher_meta_ads` **ne lève jamais d'exception** : un échec total renvoie un
résultat exploitable avec `donnees_disponibles=False` et le détail de chaque run
dans `statuts_collecte`.

`FicheProduit` et `ParametresMarche` sont les modèles partagés par tous les
collecteurs du projet : un orchestrateur amont alimente les six agents avec le
même objet.

---

## 4. Le cœur du module : le pays de diffusion

### 4.1 Un pays de DIFFUSION, pas un annonceur ni une livraison

Le paramètre `country` de la bibliothèque publicitaire sélectionne les annonces
**diffusées** dans ce pays. Ce n'est ni le pays de l'annonceur, ni le pays
d'expédition du produit :

- une agence de Shenzhen qui pousse ses créatifs au Maroc **est** au corpus ;
- une PME marocaine qui n'annonce qu'en France **n'y est pas**.

C'est voulu. L'objet du module est la **pression publicitaire subie** sur le
marché étudié — ce qu'un consommateur y voit défiler dans son fil —, pas
l'activité des entreprises locales.

`is_targeted_country=false` complète ce choix : une annonce internationale
diffusée localement est retenue, sans exiger que le pays soit son unique
ciblage. Le module suit ici la décision de l'ancien `metaads.py`, conservée
telle quelle.

### 4.2 Comment une région devient un pays

`strategy.resoudre_pays` décide dans cet ordre :

1. **`ALL`, `monde`, `world`, `global`…** → tous les pays, explicitement, avec un
   avertissement : le corpus mêle alors des marchés très différents et ne décrit
   aucun d'eux en particulier ;
2. **un code ISO-2**, pris tel quel — la bibliothèque les accepte tous ;
3. **le modèle**, pour toute autre saisie (nom de pays, ville, région, autre
   alphabet). Il identifie **uniquement le pays** et ne propose aucun pays de
   substitution.

Une saisie irrésoluble donne un **refus**, jamais un pays par défaut ni un repli
sur le monde entier : ce serait livrer un corpus qui ne décrit pas la région
demandée, sans que rien ne le signale.

```bash
$ python main.py --nom "…" --description "…" --geo "???" --langue fr
Région « ??? » non résolue.
Région « ??? » non résolue en un pays. La région d'étude n'a pas pu être résolue
en un pays. Aucune collecte n'est lancée : interroger la bibliothèque
publicitaire sur un pays par défaut — ou sur le monde entier — livrerait un
corpus qui ne décrit pas la région demandée, sans que rien ne le signale.
Reprendre avec un code ISO-2 (« MA », « FR »), ou avec « ALL » pour viser
explicitement tous les pays.
Aucun run Apify n'a été lancé.
$ echo $?
3
```

Le JSON est tout de même écrit, exploitable par un orchestrateur, avec
`region_couverte: false`, `pays: null` et **aucune hypothèse** : les limites
méthodologiques habituelles sont omises, elles décriraient un corpus qui
n'existe pas.

### 4.3 Ce que la bibliothèque expose réellement

Trois asymétries à connaître avant d'interpréter quoi que ce soit :

| Sujet | Réalité |
| --- | --- |
| **Annonces actives** | Exposées pour tous les pays, sans condition. C'est le régime nominal du module. |
| **Annonces arrêtées** | Ne restent normalement consultables **que dans l'Union européenne** (archivage imposé par le règlement sur les services numériques). Une recherche `statut_diffusion = inactives` hors UE est donc attendue vide. |
| **Portée et dépense** | Publiées pour les seules annonces **politiques et de société**. Sur un corpus commercial, `portee_estimee` et `depense` sont nuls d'un bout à l'autre. |

⚠️ Les deux premières lignes reprennent la politique affichée par Meta ; elles
n'ont **pas** été vérifiées run à run par ce module. La troisième est constatée
sur les collectes de `metaads.py`.

---

## 5. Architecture

Huit fichiers à plat, comme les autres agents du projet. Le sens de lecture va
de haut en bas ; aucune dépendance circulaire.

| Fichier | Rôle | LLM | Réseau |
| --- | --- | --- | --- |
| `config.py` | Constantes, `.env`, logging, paramètres d'URL de la bibliothèque, limites et hypothèses. **Aucune valeur magique ailleurs.** | — | — |
| `schemas.py` | Contrats Pydantic v2 d'entrée, de sortie et des sorties structurées. | — | — |
| `strategy.py` | Contrôle qualité de la fiche, **résolution du pays**, plan de recherches, construction des URLs. | ✔ | — |
| `meta_ads_source.py` | L'actor Apify. Ne propage jamais d'exception. | — | ✔ |
| `normalize.py` | Items bruts → `Annonce`, durée de diffusion, statistiques. | — | — |
| `filtering.py` | Dédoublonnage par identifiant **et par créatif**, statut, classification par lots, seuil de pertinence. | ✔ | — |
| `agent.py` | Orchestration, élargissement, cycle de repli, limites, hypothèses. | — | — |
| `main.py` | CLI, écriture du JSON. | — | — |

### Séquence d'exécution

```
fiche produit + région
  │
  ├─ région → pays de diffusion ................. strategy (table triviale, LLM en repli)
  │     └─ région non résolue → ARRÊT IMMÉDIAT (0 run, 0 appel LLM de plus)
  ├─ contrôle qualité de la fiche ............... strategy (LLM, informatif)
  ├─ plan de N recherches ....................... strategy (LLM, contrôlé par le code)
  │     └─ + 1 recherche par --annonceur ........ strategy (aucun LLM)
  │
  ├─ CYCLE 1
  │   ├─ N runs `facebook-ads-scraper` en parallèle  meta_ads_source
  │   ├─ élargissement des recherches vides (pause)  agent + strategy
  │   ├─ normalisation + durée de diffusion ...... normalize
  │   ├─ dédoublonnage identifiant + créatif ..... filtering
  │   ├─ classification par lots ................. filtering (LLM)
  │   └─ seuil de pertinence ..................... filtering
  │
  ├─ si corpus < SEUIL_MIN_ANNONCES → CYCLE DE REPLI (une seule fois)
  │
  ├─ tri par pertinence, puis longévité .......... agent
  └─ statistiques, limites, hypothèses ........... normalize + agent
```

---

## 6. Le plan de recherches

⚠️ **Ce n'est pas une recherche e-commerce.** Le moteur de la bibliothèque
apparie les mots sur le **texte des annonces**, pas sur un catalogue produit. Le
prompt insiste sur ce point : il faut écrire comme un **annonceur** qui vend, et
non comme un acheteur qui cherche. Un produit vendu par un créatif purement
visuel est, lui, structurellement invisible (limite jointe au résultat).

Le modèle **propose**, le code **dispose**. Chaque recherche proposée passe un
contrôle mécanique dans `strategy._conformer` :

| Contrôle | Traitement |
| --- | --- |
| Mots-clés vides | Recherche écartée. |
| Mots-clés reprenant le **titre commercial brut** | Recherche écartée — une référence complète n'apparaît dans aucun texte d'annonce. |
| `type_recherche` hors nomenclature | Ramené à `mots_cles`. |
| `statut_diffusion` hors nomenclature | Ramené à `actives`. |
| Doublon de mots-clés | Écarté. |

Si le plan n'atteint pas `NB_RECHERCHES`, `LIMITE_PLAN_INCOMPLET` est jointe au
résultat — aucune re-sollicitation du modèle n'est faite.

### URLs produites

```
https://www.facebook.com/ads/library/?q=ecouteurs+open+ear&country=MA&active_status=active
        &ad_type=all&search_type=keyword_unordered&media_type=all&is_targeted_country=false
```

| Paramètre | Valeur | Décidé par |
| --- | --- | --- |
| `q` | mots-clés | le modèle, contrôlé par le code |
| `country` | `MA` … ou `ALL` | `resoudre_pays` |
| `active_status` | `active` / `inactive` / `all` | le modèle (`statut_diffusion`) |
| `search_type` | `keyword_unordered` / `keyword_exact_phrase` | le modèle (`type_recherche`) |
| `ad_type` | `all` — et non le seul périmètre politique | constante |
| `media_type` | `all` — images, vidéos et créatifs sans média | constante |
| `is_targeted_country` | `false` — voir §4.1 | constante |
| `content_languages[0]` | *(absent)* | `FILTRER_PAR_LANGUE_CONTENU`, **faux** |

`content_languages[0]` filtrerait sur la langue du **créatif**. Il est laissé
désactivé pour deux raisons : sur un marché multilingue il ampute le corpus
d'une partie de la pression publicitaire réellement subie, et il n'a **pas été
vérifié sur un run réel** de ce module.

Une URL d'annonceur (`--annonceur`) est, elle, transmise **telle quelle** :
aucun paramètre n'y est ajouté, et elle n'est jamais élargie.

---

## 7. L'actor Apify

### 7.1 `apify/facebook-ads-scraper` — payload

Une recherche = un run. **Tous les filtres passent par l'URL, aucun par le
payload** : ce sont les paramètres de la bibliothèque elle-même, et les pousser
dans l'URL garantit que l'actor voit exactement la page qu'un humain verrait,
sans dépendre d'un champ d'entrée dont le nom peut changer d'une version
d'actor à l'autre.

```json
{
  "startUrls": [{"url": "https://www.facebook.com/ads/library/?q=…"}],
  "resultsLimit": 30
}
```

L'actor complète de lui-même ce payload avec ses défauts — `activeStatus: ""`,
`sorting: ""`, `enrichWithEcommerceData: false` (relevé dans l'`INPUT` des runs
du 04/08/2026). Ces champs restent délibérément non renseignés : le statut passe
par l'URL, et l'enrichissement e-commerce est un poste de coût supplémentaire.

`resultsLimit` est le seul rempart contre la facturation : **l'actor est
facturé à l'annonce**, et une catégorie grand public compte des milliers
d'annonces actives sur un grand pays. Un run qui atteint le plafond est signalé
(`plafond_atteint`) et déclenche `LIMITE_PLAFOND_ATTEINT` : le corpus est alors
un échantillon tronqué, dans un ordre que Meta ne documente pas.

Cet actor est **officiel et sur SDK courant** : il se lance aussi bien par
`apify-client` que par le serveur MCP — contrairement aux actors communautaires
de la même catégorie (`solidcode/…`, `automly/…`), qui échouent sur l'origine de
run « MCP ». Ce module passe de toute façon par `apify-client` en direct.

### 7.2 Le piège de l'enveloppe

**Une recherche sans résultat ne produit pas un dataset vide.** L'actor y écrit
un item d'un autre type — une enveloppe :

```json
{ "inputUrl": "https://…", "results": [], "totalCount": 0,
  "isResultComplete": true, "pageInfo": { … } }
```

Prise pour une annonce, elle fait croire à un item collecté : la recherche n'est
plus vue comme vide, l'élargissement ne se déclenche pas et
`LIMITE_RECHERCHES_VIDES` n'est pas levée. `meta_ads_source.deballer` traite le
cas **avant tout décompte** : les enveloppes portant des `results` sont dépliées
(liste d'annonces ou liste de listes), les autres jetées.

Constaté sur les runs du 04/08/2026 : trois recherches sans résultat, trois
enveloppes, `totalCount: 0`.

### 7.3 Schéma de sortie exploité

Noms de champs centralisés dans `config.py`, relevés sur les runs réels du
04/08/2026.

| Champ | Type | Devient |
| --- | --- | --- |
| `adArchiveID` / `adArchiveId` | `str` | `id_annonce` — clé de dédoublonnage, et `url_bibliotheque` |
| `pageName`, `pageId` / `pageID` | `str` | `annonceur`, `id_annonceur` |
| `collationId`, `collationCount` | `str`, `int` | `id_collation`, `nb_declinaisons` — **le regroupement de créatifs par Meta** (§8.1) |
| `snapshot.title` | `str` | `titre` |
| `snapshot.body` | `{text}` **ou** `str` | `texte` — les deux formes sont gérées |
| `snapshot.linkDescription` | `str` | `description_lien` — **porte souvent tout l'argumentaire** |
| `snapshot.ctaText`, `snapshot.linkUrl` | `str` | `cta`, `lien` |
| `snapshot.caption` | `str` | `legende` |
| `snapshot.displayFormat` | `"VIDEO"` / `"IMAGE"` / … | `type_media` |
| `snapshot.images[0]` | `{resized_image_url \| original_image_url \| …}` | `image` |
| `snapshot.videos[0]` | `{videoHdUrl \| videoSdUrl \| videoPreviewImageUrl}` | `video` |
| `publisherPlatform` | `[str]` **ou** `str` | `plateformes`, en minuscules et sans doublon |
| `isActive` | `bool` | `active` |
| `startDateFormatted` / `startDate` | `str` / horodatage | `date_debut` |
| `endDateFormatted` / `endDate` | `str` / horodatage | `date_fin`, et `duree_diffusion_jours` |
| `reachEstimate`, `spend`, `currency` | `str` | `portee_estimee`, `depense`, `devise` — **annonces politiques uniquement** |

Trois pièges vérifiés sur les runs réels :

- **`body` ≠ l'argumentaire.** Sur les annonces observées, `body.text` tenait en
  quatre mots (« (Doublage) Tentation silencieuse ») pendant que
  `linkDescription` portait quatre lignes de vente. Les deux sont concaténés
  avant classification — ne transmettre que le corps priverait le classifieur de
  l'essentiel.
- **`displayFormat` prime sur les listes de médias.** Une annonce vidéo porte
  *aussi* une liste `images` — sa vignette. Se fier à la présence des listes la
  ferait passer pour une annonce image. La présence ne sert que de repli pour
  les formats hors nomenclature (`DCO`, `CAROUSEL`…).
- **`endDateFormatted` est renseigné même sur une annonce active** : Meta y met
  la date du jour. Ce n'est pas une date d'arrêt — seul `active` dit si la
  diffusion se poursuit.

⚠️ **URLs de médias éphémères.** `image` et `video` pointent le CDN Facebook avec
une signature qui expire en quelques heures. Elles servent à consulter un
créatif dans la foulée de la collecte, pas à l'archiver.

⚠️ **`duree_diffusion_jours`** est calculée jusqu'à la **date du run** pour une
annonce encore active. Deux collectes à un mois d'intervalle ne donnent donc pas
la même valeur pour la même annonce — c'est le seul champ dépendant du moment de
la collecte.

Champs observés mais **non exploités** : `totalActiveTime` (secondes de
diffusion, souvent nul), `snapshot.cards` (carrousels : un titre et un lien par
carte), `pageLikeCount`, `pageCategories`, `snapshot.pageProfileUri`,
`categories`, `targetedOrReachedCountries` (vide sur les annonces
commerciales).

### 7.4 Robustesse

| Mécanisme | Portée |
| --- | --- |
| `NB_TENTATIVES_MAX = 2`, backoff `(20 s, 60 s)` | Run en échec (statut ≠ `SUCCEEDED`, exception réseau). Attente longue **délibérée** : réessayer aussitôt réutilise la session proxy qui vient d'être refusée. |
| Élargissement, après `PAUSE_AVANT_REPLI_SECS = 20 s` | Run `SUCCEEDED` mais **sans annonce**. L'URL est rejouée sans son filtre de statut (et sans filtre de langue). **Uniquement s'il reste un filtre à relâcher** : une recherche déjà tous statuts confondus n'est pas rejouée, ce serait un run payé pour la même requête. Les URLs d'annonceur ne sont jamais élargies. |
| Cycle de repli | Corpus final sous `SEUIL_MIN_ANNONCES`. Le modèle génère des recherches plus larges. **Une seule fois par exécution, sous aucune condition deux.** |
| `PARALLELISME_MAX = 3` | Runs simultanés. La valeur `1` rétablit une exécution strictement séquentielle. |

Un run réussi mais vide n'est **pas** un échec : sur cette source, une recherche
vide est un résultat en soi — personne n'annonce sur ces mots dans ce pays. Le
statut porte `succes=true`, `nb_items=0` et un message de diagnostic, et
`LIMITE_RECHERCHES_VIDES` est jointe au résultat.

---

## 8. Qualification du corpus

### 8.1 Dédoublonnage — le filtre structurant du module

Deux passes, cumulatives sur tous les cycles :

1. **par identifiant d'annonce** (`adArchiveID`) — une même annonce remontée par
   deux recherches n'est comptée qu'une fois ;
2. **par créatif** — `collationId` **quand Meta la fournit** : c'est son propre
   regroupement des déclinaisons d'un créatif, et il fait foi. À défaut,
   heuristique `annonceur + 200 premiers caractères du texte`, accents, casse,
   ponctuation et émojis écrasés ; sans texte, repli sur
   `annonceur + titre + destination du clic`.

La seconde passe n'est pas cosmétique. Un annonceur diffuse le **même** visuel
et le **même** texte sous des dizaines d'identifiants — un par audience, un par
placement. Sans ce rapprochement, un seul concurrent occupe tout le corpus et
fausse chacune des répartitions. Le décompte est publié séparément
(`stats.nb_doublons_creatif`) : c'est en soi un indicateur d'intensité de
campagne.

⚠️ Corollaire à ne pas oublier : **le décompte d'annonces n'est pas un décompte
de campagnes**, et le rapprochement reste approximatif — un créatif traduit ou
légèrement réécrit compte deux fois.

Sont également écartées : les annonces sans **aucun** contenu exploitable (ni
titre, ni texte, ni appel à l'action, ni lien, ni image), et celles dont le
statut contredit celui demandé. Une annonce dont le statut est **inconnu** est
conservée : l'absence de champ ne vaut pas contradiction.

### 8.2 Classification LLM (`filtering.classifier_annonces`)

Par lots de 15, chaque annonce est confrontée au produit de référence :

| `correspondance` | Sens | Pertinence typique |
| --- | --- | --- |
| `concurrent_direct` | Vend un produit de même catégorie et de même usage. | ~1 |
| `categorie_proche` | Même famille de besoin, produit ou positionnement sensiblement différent. | 0,5–0,9 |
| `accessoire` | Complément et non substitut : housse, câble, support. | ~0,2 |
| `hors_sujet` | Autre catégorie, ou annonce qui ne vend rien (recrutement, notoriété, événement). | 0 |

Le prompt rappelle qu'un texte d'annonce est du **discours commercial** : il
exagère et cite parfois des marques qu'il ne vend pas. Le jugement porte sur le
produit réellement proposé, pas sur la présence d'un terme.

Les annonces sous `SEUIL_PERTINENCE = 0.5` sont écartées. Une annonce **non
classifiée** (lot en échec) est **conservée** et n'est pas confrontée au seuil :
l'échec d'un appel LLM ne doit pas se traduire par une perte silencieuse de
corpus. Elle est comptée dans `stats.nb_annonces_non_classifiees` et déclenche
`LIMITE_CORPUS_*_CLASSIFIE`.

### 8.3 Ordre du corpus

Tri par **pertinence décroissante**, puis par **longévité de diffusion
décroissante**, puis par rang de collecte. À pertinence égale, l'annonce
diffusée depuis des mois passe devant : c'est le seul signal de sélection dont
ce corpus dispose. Ce n'est **pas** une mesure de performance (§11).

---

## 9. Structure du JSON de sortie

```jsonc
{
  "produit":  { "nom": "…", "description": "…", "categorie": "…" },
  "marche":   { "geo": "MA", "langue": "fr" },
  "region_couverte": true,          // faux ⇒ rien n'a été collecté, voir §4.2
  "pays": {                         // null quand region_couverte vaut faux
    "code_pays": "MA",
    "explication": "« Casablanca » → MA : collecte des annonces diffusées en MA…"
  },
  "alertes_qualite_input": [ { "type": "contradiction", "detail": "…" } ],
  "plan_recherches": [
    {
      "mots_cles": "écouteurs open ear", "type_recherche": "mots_cles",
      "statut_diffusion": "actives", "justification": "…",
      "url": "https://www.facebook.com/ads/library/?q=…",
      "filtres_url": true, "est_annonceur": false, "est_repli": false
    }
  ],
  "annonces": [
    {
      "id_annonce": "1234567890",
      "url_bibliotheque": "https://www.facebook.com/ads/library/?id=1234567890",
      "annonceur": "Boutique Test", "id_annonceur": "999",
      "titre": "Écouteurs open ear", "texte": "Le son sans bouchons 🎧 …",
      "description_lien": "Autonomie 40 h, IP68, livraison 24 h partout au Maroc.",
      "legende": "boutique.test", "cta": "Acheter",
      "lien": "https://boutique.test/produit",
      "image": "https://…/1.jpg", "video": null, "type_media": "image",
      "id_collation": "1371739464882749", "nb_declinaisons": 4,
      "plateformes": ["facebook", "instagram"], "active": true,
      "date_debut": "2026-06-01", "date_fin": null,
      "duree_diffusion_jours": 64,
      "portee_estimee": null, "depense": null, "devise": null,
      "recherche_origine": "écouteurs open ear", "rang_collecte": 3,
      "correspondance": "concurrent_direct", "pertinence": 0.95
    }
  ],
  "stats": {
    "nb_annonces_collectees": 90, "nb_annonces_retenues": 24,
    "nb_annonceurs": 11, "nb_annonces_actives": 24,
    "nb_doublons_ecartes": 6, "nb_doublons_creatif": 41,
    "nb_annonces_hors_criteres": 3, "nb_annonces_sous_seuil": 16,
    "nb_annonces_non_classifiees": 0,
    "duree_diffusion_mediane_jours": 28.0, "duree_diffusion_max_jours": 213,
    "repartition_par_correspondance": { "concurrent_direct": 15, "categorie_proche": 9 },
    "repartition_par_annonceur": { "Boutique Test": 4 },
    "repartition_par_plateforme": { "facebook": 24, "instagram": 19 },
    "repartition_par_cta": { "Acheter": 12, "En savoir plus": 7 },
    "repartition_par_recherche": { "écouteurs open ear": 11 }
  },
  "statuts_collecte": [
    { "recherche": "écouteurs open ear", "url": "https://…", "succes": true,
      "message_erreur": null, "nb_items": 30, "nb_tentatives": 1,
      "plafond_atteint": true }
  ],
  "donnees_disponibles": true,
  "limites":    [ "…" ],
  "hypotheses": [ "…" ]
}
```

`limites` et `hypotheses` ne sont pas décoratives : elles sont destinées à
accompagner le corpus jusqu'à l'agent d'analyse en aval, pour qu'il ne prenne
pas une bibliothèque publicitaire pour une mesure d'audience.

---

## 10. Coût d'une exécution

| Poste | Runs | Remarque |
| --- | --- | --- |
| Recherches du plan | `NB_RECHERCHES` = **3** | |
| Annonceurs surveillés | 1 par `--annonceur` | Optionnel. |
| Élargissement des recherches vides | 0 à 3 | Seulement si une recherche est restée vide **et** qu'il reste un filtre à relâcher. |
| Cycle de repli | 0 ou **1** | Seulement si le corpus reste sous le seuil. |
| Appels Claude Haiku | 3 à 6 | Sorties courtes, coût négligeable devant l'actor. |

Soit **3 à 7 runs Apify** pour une exécution nominale — mais le nombre de runs
n'est pas le bon indicateur ici : **l'actor est facturé à l'annonce**. Le coût
réel est piloté par `--annonces` (× le nombre de runs), et par lui seul.
`--annonces 10` divise la facture par trois.

⚠️ **Quota Apify.** Un compte en fin de quota **tronque les runs sans les faire
échouer** : ils remontent en `SUCCEEDED` avec un dataset incomplet. Vérifier le
quota avant toute interprétation d'un volume d'annonces — c'est une limite
systématiquement jointe au résultat, pour cette raison précise.

---

## 11. Limites connues et pièges

| Sujet | À savoir |
| --- | --- |
| **Aucune donnée de performance** | Meta ne publie ni portée, ni dépense, ni ciblage pour les annonces commerciales. Rien dans ce corpus ne dit qu'une annonce a marché. |
| **Longévité ≠ rentabilité** | `duree_diffusion_jours` est un **indice** qu'un annonceur y trouve son compte, jamais une preuve : un budget mal suivi produit exactement la même trace. |
| **Annonces arrêtées** | Normalement consultables dans la seule UE (§4.3). Une recherche sur les inactives ailleurs est attendue vide — non vérifié run à run. |
| **Recherche textuelle** | L'appariement porte sur le texte des annonces. Un produit vendu par une créative purement visuelle est invisible. |
| **Décompte ≠ campagnes** | Le rapprochement des créatifs est approximatif (§8.1) : un créatif traduit ou réécrit compte deux fois. |
| **Corpus tronqué** | Plafonné par recherche, servi dans un ordre non documenté. `plafond_atteint` le signale, mais aucun volume ne doit en être déduit. |
| **Homonymie des mots-clés** | L'appariement est littéral et sans contexte : « short » a ramené des applis de mini-séries (« short drama »). La classification les écarte — mais le run est facturé quand même. |
| **Langue des mots-clés** | Sur un marché non francophone, des mots-clés français ramènent surtout des annonceurs internationaux. `--langue` pilote la langue de rédaction du plan : c'est le réglage le plus déterminant du résultat. |
| **URLs de médias** | Signées et éphémères (quelques heures). Ne pas les stocker comme des références durables. |
| **Périmètre Meta** | Google, TikTok, les places de marché et le référencement naturel sont absents. Une bibliothèque pauvre ne signifie pas un marché sans concurrence. |
| **Heuristiques** | `pertinence`, `correspondance`, `SEUIL_PERTINENCE`, `SEUIL_MIN_ANNONCES` ne sont validés sur aucun échantillon annoté. |
| **`--geo ALL`** | Mélange des marchés incomparables. À réserver à une exploration, jamais à une étude régionale. |

---

## 12. Réglages courants

Tout se règle dans `config.py`, sans toucher au reste du code.

| Constante | Défaut | Effet |
| --- | --- | --- |
| `MAX_ANNONCES_PAR_RECHERCHE` | 30 | **Le levier de coût** (`--annonces` l'écrase). |
| `NB_RECHERCHES` | 3 | Angles de recherche du plan. |
| `SEUIL_MIN_ANNONCES` | 5 | Déclenchement du cycle de repli. |
| `SEUIL_PERTINENCE` | 0.5 | Sévérité du filtre de pertinence. |
| `LONGUEUR_CLE_CREATIF` | 200 | Sensibilité du rapprochement des créatifs. |
| `FILTRER_PAR_LANGUE_CONTENU` | `False` | Ajoute `content_languages[0]` aux URLs. **Non vérifié** (§6). |
| `PARALLELISME_MAX` | 3 | Runs simultanés ; `1` = séquentiel. |
| `MODELE_CLAUDE` | `claude-haiku-4-5-20251001` | Les quatre étapes LLM sont mécaniques et à sortie courte. |

---

## 13. Ce qui change par rapport à `metaads.py`

| | `metaads.py` | `agent_meta_ads/` |
| --- | --- | --- |
| Entrée | Requête en texte libre ou URL collée | `FicheProduit` + `ParametresMarche`, partagés avec les autres agents |
| LLM | OpenAI `gpt-5-nano`, un seul appel de planification | Claude Haiku via `langchain-anthropic`, comme le reste du projet |
| Accès Apify | Serveur MCP hébergé + REST `httpx` pour le dataset | `apify-client` en direct — plus de polling manuel du run |
| Recherches | Une seule | Plan de `NB_RECHERCHES` angles + élargissement + cycle de repli |
| URL d'annonceur | Mode principal (`start_url`) | Conservé, en **complément** du plan (`--annonceur`, répétable) |
| Dédoublonnage | Aucun | Par identifiant **et par créatif** (§8.1) |
| Qualification | Aucune | Classification concurrent / catégorie / accessoire / hors sujet + seuil |
| Longévité | Dates brutes | `duree_diffusion_jours`, et tri du corpus dessus |
| Sortie | Affichage texte dans le terminal | JSON structuré, avec stats, statuts, limites et hypothèses |
| Robustesse | Aucune reprise | Tentatives avec backoff, élargissement, repli, aucune exception propagée |

L'ancien `metaads.py` reste à la racine du projet et fonctionne toujours ; il
n'a pas été modifié.

---

## 14. État de validation

Vérifié hors réseau sur ce portage : résolution du pays (code ISO-2, mots
« monde »/`ALL`, saisie vide refusée, arrêt effectif sans appel Apify et code de
sortie 3), construction des URLs (encodage des mots-clés, traduction du statut
et du mode d'appariement, filtre de langue bien absent), conformation des
recherches proposées (nomenclatures ramenées au défaut, titre commercial brut et
mots-clés vides écartés), élargissement et non-élargissement des recherches déjà
larges, transmission littérale des URLs d'annonceur, **déballage des enveloppes**
(vide, pleine, liste de listes), normalisation d'items bruts aux deux
conventions de champs (dates ISO et horodatages, corps objet et corps chaîne,
plateformes en liste et en chaîne, item vide, `displayFormat` primant sur la
vignette d'une vidéo), dédoublonnage par identifiant, **par `collationId`** et
par créatif y compris le repli sans texte, seuil de pertinence,
statistiques, orchestration complète sur sources simulées (nominal, plafond
atteint, annonceur imposé, échec total, recherches vides puis élargies, région
non résolue, plan impossible), formatage de tous les gabarits de prompt et
interface CLI.

### Premier run réel — 04/08/2026, `--geo MA --langue fr`

4 runs, 18 items, **0 annonce retenue**. Trois enseignements, tous répercutés
dans le code :

1. **Bug corrigé** — les 3 recherches à zéro résultat renvoyaient une
   *enveloppe* comptée comme un item collecté (§7.2). L'élargissement ne se
   déclenchait donc jamais, et la limite « recherches vides » n'était pas levée.
   `deballer` traite désormais le cas avant tout décompte.
2. **Mapping enrichi** — le dataset réel expose `collationId` / `collationCount`
   (regroupement des créatifs par Meta, désormais clé de dédoublonnage
   privilégiée), `linkDescription` (qui portait l'argumentaire que `body.text`
   n'avait pas) et `displayFormat` + `videos` (le point de mapping signalé comme
   fragile est levé). Voir §7.3.
3. **Le pipeline a bien fonctionné** — les 15 annonces du cycle de repli étaient
   des applis de mini-séries (« short drama ») ramenées par le mot « short », et
   la classification les a toutes écartées à juste titre. Ce sont les
   **mots-clés** qui étaient en cause : du français sur un marché où les
   annonces de cette catégorie sont en arabe ou en darija.

Reste à confirmer sur un run ultérieur : le **comportement de `resultsLimit`**
sur une recherche très large, et le **remplissage de `results`** dans une
enveloppe (observé vide, déplié par précaution).
