# agent_reddit — collecte de discussions et d'avis consommateurs sur Reddit

Module autonome, exécutable en ligne de commande. À partir d'une **fiche produit**
et d'une **région d'étude**, il collecte, dédoublonne, filtre et anonymise un
corpus de posts et de commentaires Reddit, puis retourne un objet Pydantic validé
sérialisé en JSON sur `stdout`.

Le module **collecte et qualifie** un corpus. Il ne l'interprète pas : aucune
analyse de sentiment, aucune extraction de *pain points*, aucune synthèse
d'insights — c'est le rôle d'un module aval. Il ne produit ni ne permet aucune
affirmation sur la taille d'un marché ou un volume de demande.

---

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env   # puis renseigner les trois variables
```

Python ≥ 3.11 requis.

### Variables d'environnement

| Variable | Rôle | Obligatoire |
|---|---|---|
| `ANTHROPIC_API_KEY` | Étapes LLM : contrôle qualité, stratégie de recherche, scoring de pertinence | oui |
| `APIFY_TOKEN` | Exécution de l'actor `harshmaur/reddit-scraper`. `APIFY_API_TOKEN` accepté en repli | oui |
| `SEL_ANONYMISATION` | Chaîne libre servant de sel au hachage des pseudonymes | fortement recommandé |

Sans `SEL_ANONYMISATION`, un sel de repli **public** est utilisé et un
avertissement est émis sur `stderr` : les empreintes d'auteur redeviennent alors
réversibles par force brute sur le dictionnaire des pseudonymes Reddit.

Le `.env` est cherché en remontant depuis le répertoire courant : un `.env`
placé à la racine du projet parent est donc trouvé.

---

## Usage

Le programme se lance **depuis l'intérieur du dossier** (imports absolus à plat) :

```bash
cd agent_reddit
python main.py \
    --nom "Shokz OpenRun Pro 2" \
    --description "Casque à conduction osseuse sans fil, oreilles libres, pour la course et le vélo." \
    --categorie "electronique / audio sport" \
    --geo FR \
    --langue fr \
    --verbose
```

| Argument | Obligatoire | Description |
|---|---|---|
| `--nom` | oui | Titre commercial du produit |
| `--description` | oui | Description libre |
| `--categorie` | non | Catégorie e-commerce |
| `--geo` | oui | Code pays ISO-2 de la région d'étude, ex. `FR` |
| `--langue` | oui | Code langue ISO-2 du marché, ex. `fr` |
| `--verbose` | non | Progression détaillée **sur `stderr` uniquement** |

`stdout` ne contient **que** le JSON du résultat : la sortie reste parsable en
l'état (`python main.py ... 2>/dev/null | jq .`).

---

## Architecture

```
config.py         constantes, .env, plafonds de coût, libellés, limites
schemas.py        contrats Pydantic v2 (entrée / sortie)
strategy.py       contrôle qualité de la fiche + stratégie de recherche (2 chaînes LCEL)
reddit_source.py  wrapper de l'actor Apify (2 modes) + gestion d'erreurs
relevance.py      dédoublonnage déterministe + scoring LLM par lots
normalize.py      normalisation, anonymisation RGPD, statistiques (fonctions pures)
agent.py          orchestration de bout en bout
main.py           point d'entrée CLI
```

Dépendances en sens unique strict, sans import circulaire :

```
config       ← (aucune)
schemas      ← config
strategy     ← config, schemas
reddit_source← config, schemas
relevance    ← config, schemas
normalize    ← config, schemas
agent        ← config, schemas, strategy, reddit_source, relevance, normalize
main         ← config, schemas, agent
```

---

## Déroulé de la collecte

La collecte est découpée en **deux phases**, par contrainte de coût : les
commentaires sont facturés à l'item, on ne les collecte donc que pour les posts
ayant survécu au filtrage.

1. **Contrôle qualité de la fiche** (LLM) — signale contradictions internes,
   langue inattendue, description insuffisante. **Ne bloque jamais.**
2. **Stratégie de recherche** (LLM) — requêtes consommateur en langue du marché
   et en anglais, subreddits régionaux et thématiques. La chaîne est contrainte
   de proposer **exactement un subreddit généraliste du pays**
   (`NB_SUBREDDITS_REGIONAUX`) : voir « Ancrage régional » ci-dessous.
3. **Phase A, run 1** — recherche globale sur tout Reddit, toutes requêtes,
   **sans commentaires**.
4. **Phase A, runs 2..N** — une recherche par subreddit cible
   (`withinCommunity` n'accepte **qu'un seul** subreddit par run), requêtes en
   langue du marché uniquement.
5. **Dédoublonnage** par identifiant de post entre les runs — les recherches
   globale et restreintes se recouvrent — puis **scoring LLM par lots** et
   rétention des posts au-dessus du seuil.
6. **Phase B** — un run unique sur les URLs des posts les plus prometteurs
   (couple pertinence × nombre de commentaires).
7. **Normalisation, anonymisation, statistiques**, puis construction du résultat.

**Au plus `1 + NB_MAX_SUBREDDITS_CIBLES + 1` = 5 runs Apify par exécution**,
exécutés **séquentiellement** : le volume ne justifie aucun parallélisme, chaque
run facture des frais de démarrage, et la lisibilité des statuts prime sur la
latence.

---

## Schéma de sortie réel de l'actor `harshmaur/reddit-scraper`

Relevé sur deux **runs d'exploration réels** du 30/07/2026. Le schéma annoncé
est riche (~140 champs) et **non contractuel** : le parsing du module ne repose
que sur les champs ci-dessous, effectivement observés. Les noms sont centralisés
dans `config.py`, section « Schéma réel de l'actor ».

### Distinguer un post d'un commentaire

Le dataset est **hétérogène** : posts et commentaires cohabitent dans le même
dataset. Le discriminant est le champ **`dataType`** :

| `dataType` | Nombre de champs observés | Produit par |
|---|---|---|
| `"post"` | 76 | recherche par mots-clés **et** `startUrls` |
| `"comment"` | 39 | uniquement si `crawlCommentsPerPost: true` |

Un run de phase B renvoie donc **à la fois** les posts ciblés (re-sauvegardés,
et donc **re-facturés**) et leurs commentaires.

### Champs d'un item `post` exploités

| Champ | Exemple observé | Mappé vers |
|---|---|---|
| `id` | `"t3_1twgy7k"` | `PostReddit.id` |
| `title` | `"J'ai testé les Shokz OpenDots 2 en running…"` | `titre` |
| `body` | corps du post, `""` si post-lien | `texte` |
| `communityName` | `"r/shokz"` | `subreddit` |
| `postUrl` | URL canonique du post | `url` |
| `createdAt` | `"2026-06-04T07:56:49.000Z"` | `date_creation` (recanonisé) |
| `score` | `0` | `score` |
| `commentsCount` | `239` | `nb_commentaires` |
| `authorName` | `"Brick_Lanky"` | `auteur_pseudonymise` (**haché**) |
| `searchTerm` | `"écouteurs open ear avis"` | `requete_source` |

⚠️ `searchTerm` n'est présent **que** sur les items issus d'une recherche par
mots-clés. Il est **absent** des items d'un run `startUrls` : le code le traite
comme optionnel.

Autres champs disponibles mais **non retenus** : `parsedId`, `upVotes`,
`upvoteRatio`, `flair`, `postType`, `domain`, `thumbnail`, `images`,
`galleryImages`, `mediaAssets`, `media`, `ageHours`, `scorePerHour`,
`engagementTotal`, `isHighEngagement`, `wordCount`, `over18`, `locked`,
`archived`, `numCrossposts`, `subredditSubscribers`, etc.

### Champs d'un item `comment` exploités

| Champ | Exemple observé | Mappé vers |
|---|---|---|
| `id` | `"ou8mg3u"` (**sans** préfixe `t1_`) | `CommentaireReddit.id` |
| `postId` | `"t3_1uhjic6"` | `id_post` |
| `body` | texte du commentaire | `texte` |
| `commentCreatedAt` | `"2026-06-28T02:12:25.000Z"` | `date_creation` |
| `score` | `204` | `score` |
| `depth` | `0` (réponse directe au post) | `profondeur` |
| `authorName` | `"ToaruBaka"` | `auteur_pseudonymise` (**haché**) |

⚠️ Pièges relevés, à ne pas déduire du schéma des posts :

- la date d'un commentaire est dans **`commentCreatedAt`**, pas `createdAt` ;
- l'`id` d'un commentaire est **nu**, alors que celui d'un post porte le
  préfixe `t3_` ;
- le rattachement au fil se fait par **`comment.postId` ↔ `post.id`** (tous deux
  au format `t3_…`) ;
- le subreddit d'un commentaire est dans `subredditName`, **sans** préfixe
  `r/`, alors que le post utilise `communityName` **avec** préfixe.

---

## Traitement d'anonymisation (RGPD)

- Le pseudonyme d'auteur (`authorName`) est remplacé par les **16 premiers
  caractères hexadécimaux** de `sha256(pseudo + SEL_ANONYMISATION)`. Le
  pseudonyme en clair n'apparaît **jamais** dans la sortie.
- Les marqueurs de compte supprimé (`[deleted]`, `[removed]`, valeur vide) sont
  regroupés sous la valeur unique `"anonyme"` : ils ne désignent aucune personne,
  et les hacher créerait autant de faux auteurs distincts.
- **Aucun champ de profil utilisateur** ne subsiste : `authorId`,
  `parsedAuthorId`, `authorFullname`, `authorFlairText`, `authorPremium`,
  `isSubmitter` sont écartés. La garantie est **structurelle** — les modèles
  Pydantic de sortie sont fermés et construits champ par champ, aucun champ
  brut n'est recopié en bloc.
- Le **texte** des posts et des commentaires est conservé **tel quel**, comme
  spécifié.

⚠️ **Risque résiduel documenté.** Le texte conservé tel quel peut contenir des
pseudonymes en clair : mentions `u/pseudo`, auto-signatures, URLs de profil
écrites par les auteurs eux-mêmes. Ce cas a été **observé** lors de la
validation (un post dont le corps citait le pseudonyme de son propre auteur).
L'anonymisation porte sur les **métadonnées d'auteur**, pas sur le contenu
rédactionnel. Un module aval qui republierait ces textes doit en tenir compte.

---

## Coût observé

Tarification de l'actor constatée le 30/07/2026 (modèle `PAY_PER_EVENT`) :

| Événement | Prix | Déclencheur |
|---|---|---|
| `init` | **0,02 $** par run | à chaque **démarrage** de run |
| `result` | **0,0018 $** (tier Bronze) — 0,002 $ en tier gratuit, 0,0015 $ à partir du tier Gold | à chaque item sauvegardé, **post ou commentaire** |

Les coûts relevés recoupent **exactement** le modèle
`0,02 $ + 0,0018 $ × nb_items` (compte de validation en plan Starter, tier
Bronze).

#### Runs d'exploration

| Run | Items | Coût constaté |
|---|---|---|
| Exploration 1 — recherche simple, 1 requête, `maxPostsCount: 5` | 5 posts | 0,030 $ |
| Exploration 2 — `startUrls` (2 posts) + `crawlCommentsPerPost: true`, 5 commentaires/post | 2 posts + 10 commentaires | 0,044 $ |
| Mesure de contrôle — 4 requêtes, `maxPostsCount: 10` | 40 posts | 0,092 $ |

#### Exécution complète de bout en bout — mesure réelle

Produit test : **Shokz OpenRun Pro 2**, `--geo FR --langue fr`, plafonds par
défaut, 30/07/2026. Durée totale : **3 min 11 s**.

| Run | Phase | `maxPostsCount` envoyé | Items | Coût |
|---|---|---|---|---|
| 1 | prospection globale, 6 requêtes | 16 (= 100 ÷ 6) | 95 posts | 0,191 $ |
| 2 | `withinCommunity=r/france`, 4 requêtes | 7 (= 30 ÷ 4) | 21 posts | 0,058 $ |
| 3 | `withinCommunity=r/running` | 7 | 14 posts | 0,045 $ |
| 4 | `withinCommunity=r/headphones` | 7 | 16 posts | 0,049 $ |
| 5 | phase B — commentaires de 15 posts | 15 (nb d'URLs) | 15 posts + 267 commentaires | 0,528 $ |
| | **Total, 5 runs** | | | **0,870 $** |

Corpus obtenu : **146 posts collectés → 144 après dédoublonnage → 64 retenus**
après filtrage de pertinence (80 écartés sous le seuil), **15 posts approfondis,
267 commentaires**, fenêtre 2021-04-20 → 2026-07-30. Aucun run en échec, aucune
nouvelle tentative nécessaire.

**0,87 $ par exécution complète**, sous la fourchette attendue de 1 à 2 $. Le
run 1 tient son budget au post près (95 items pour 96 budgétés), ce qui valide
la répartition du plafond décrite ci-dessous.

Le poste de dépense dominant reste la **phase B** — 0,528 $, soit 61 % du total.
Au pire `NB_MAX_POSTS_APPROFONDIS × (1 + NB_MAX_COMMENTAIRES_PAR_POST)` items,
soit 15 × 26 = 390 items ≈ 0,72 $. C'est précisément ce que le découpage en deux
phases sert à contenir : collecter les commentaires des 144 posts de prospection
aurait coûté environ **6,7 $**, et **plus de 34 $** sans la répartition du
plafond (734 posts).

### Plafonds configurés (`config.py`)

| Constante | Valeur | Effet sur le coût |
|---|---|---|
| `NB_MAX_REQUETES` | 6 | requêtes marché + anglais confondues ; pas de coût direct, dilue le plafond de posts |
| `NB_MAX_SUBREDDITS_CIBLES` | 3 | 3 démarrages de run supplémentaires (≈ 0,06 $) |
| `NB_SUBREDDITS_REGIONAUX` | 1 | 1 des 3 créneaux réservé à l'ancrage géographique |
| `NB_MAX_POSTS_RECHERCHE_GLOBALE` | 100 | ≈ 0,18 $ |
| `NB_MAX_POSTS_PAR_SUBREDDIT` | 30 | ≈ 0,054 $ par subreddit |
| `NB_MAX_POSTS_APPROFONDIS` | 15 | posts re-facturés en phase B |
| `NB_MAX_COMMENTAIRES_PAR_POST` | 25 | poste de dépense dominant |
| `FENETRE_RECHERCHE` | `"year"` | — |
| `SEUIL_PERTINENCE` | 0,5 | **heuristique non validée empiriquement** |
| `TAILLE_LOT_PERTINENCE` | 20 | posts par appel LLM |

Coût LLM (Claude Haiku 4.5) : négligeable devant le coût Apify — deux appels
courts de cadrage plus un appel de scoring par tranche de 20 posts.

---

## Contraintes documentées de l'actor

Relevées dans le schéma d'entrée et la documentation de l'actor, et prises en
compte par le module :

1. **`withinCommunity` n'accepte qu'un seul subreddit par run.** La couverture
   multi-subreddits se fait par **plusieurs runs** (boucle dans `agent.py`),
   jamais par concaténation. Constaté conforme : la valeur est acceptée sous la
   forme `r/nom`.
2. **Plans Apify gratuits : 40 mots-clés maximum par run** ; les suivants sont
   ignorés et signalés dans le log du run. Le module en envoie au plus 6.
3. **`postedAfter` force le tri `new` et ignore `searchTime`.** Le module retient
   `searchSort=relevance` + `searchTime=year` : la pertinence prime sur la
   fraîcheur pour une étude de marché. *Alternative* : pour cibler une fenêtre
   temporelle précise (lancement produit, saisonnalité), utiliser `postedAfter`
   / `postedBefore` en acceptant le tri chronologique — un tri par date sur une
   requête à fort volume remonte surtout du bruit récent.
4. **Reddit plafonne toute liste de résultats à ~1 000 posts**, quelle que soit
   la requête : l'exhaustivité est structurellement hors d'atteinte.
5. **Le `fastMode` est actif d'office sur les recherches par mots-clés** et peut
   manquer des posts. Il n'est désactivable que pour les URLs de pages de
   recherche, mode que le module n'utilise pas.
6. Aucun champ **`mcp*`** n'est renseigné : les connecteurs de livraison
   (Slack, Notion, GitHub…) sont hors périmètre.

### ⚠️ Écart confirmé : `maxPostsCount` est un quota PAR MOT-CLÉ

La description du champ dans le schéma d'entrée annonce « *Maximum number of
posts to save **across all** search results, subreddit pages, and user
profiles* ». **C'est faux : l'actor l'applique par mot-clé.**

#### Mesure de contrôle décisive

Quatre requêtes à fort volume, `maxPostsCount=10`, recherche globale :

| `searchTerm` | Items reçus |
|---|---|
| `bone conduction headphones running` | 10 |
| `open ear headphones worth it` | 10 |
| `best running headphones` | 10 |
| `wireless earbuds sweat` | 10 |
| **Total** | **40** |

Exactement 10 par requête. Attendu si plafond global : 10. Attendu si quota par
mot-clé : 40. **Le quota est par mot-clé, et il est respecté à la lettre.**

Ce modèle explique toutes les mesures antérieures :

| Run | `searchTerms` | `maxPostsCount` | Attendu (n × plafond) | **Reçus** |
|---|---|---|---|---|
| exploration 1 | 1 | 5 | 5 | **5** |
| prospection globale | 6 | 100 | 600 | **571** — disponibilité |
| `withinCommunity=r/france` | 4 | 30 | 120 | **67** — subreddit peu fourni |
| `withinCommunity=r/running` | 4 | 30 | 120 | **45** — idem |
| `withinCommunity=r/headphones` | 4 | 30 | 120 | **62** — idem |

L'incidence est directement financière : sans correction, 571 items au lieu de
100, soit **1,03 $ au lieu des 0,18 $ budgétés** sur ce seul run.

> **Fausse piste écartée en cours de route.** Une première tentative de
> correction a semblé produire un effondrement des volumes (6, 3, 4 et 3 items).
> Cette mesure était **invalide** : le run s'était terminé sur
> `ForbiddenError: Monthly usage hard limit exceeded` — le quota mensuel du
> compte Apify de test était épuisé et tronquait les runs. La mesure de contrôle
> ci-dessus, refaite sur un compte au quota intact, tranche sans ambiguïté.

#### Correctif appliqué

Plutôt que d'abaisser les constantes de plafond — ce qui aurait rendu leur
valeur dépendante du nombre de requêtes produites par le LLM, donc
imprévisible — `reddit_source._payload_recherche` **répartit le plafond budgété
entre les requêtes** avant l'envoi :

```python
plafond_par_requete = max(NB_MIN_POSTS_PAR_REQUETE, max_posts // len(requetes))
```

`NB_MAX_POSTS_RECHERCHE_GLOBALE` et `NB_MAX_POSTS_PAR_SUBREDDIT` conservent
ainsi leur sémantique de **coût total du run**, quel que soit le nombre de
requêtes dérivées. La répartition est loguée à chaque appel (`--verbose`).

*Alternatives écartées* : plafonner côté client après réception — les items sont
facturés à la sauvegarde, pas à la lecture, cela ne coûterait rien de moins ;
lancer un run par requête — exact aussi, mais multiplie les frais de démarrage
et sort du critère « au plus `1 + NB_MAX_SUBREDDITS_CIBLES + 1` runs ».

*Complément recommandé* : renseigner `maxTotalChargeUsd` dans les options de run
pour un plafond dur côté Apify, indépendant du comportement de l'actor.

### Comportements de robustesse

- **Timeout** de 600 s par run, **2 tentatives** avec backoff 5 s puis 20 s.
- `rechercher_posts` et `collecter_commentaires` **ne propagent jamais
  d'exception** : toute erreur devient un `StatutCollecte(succes=False, …)`.
- **Le payload exact est logué en UTF-8 avant chaque appel** (`--verbose`) :
  c'est le seul moyen de vérifier qu'aucune corruption d'encodage n'affecte les
  requêtes accentuées. `config.py` force `sys.stdout/stderr.reconfigure(
  encoding="utf-8")` au chargement — une requête corrompue (« Θcouteurs » au
  lieu de « écouteurs ») produit une recherche vide ou hors sujet.
- **Succès silencieux.** Un run peut se terminer `SUCCEEDED` avec 0 item. Le
  traitement dépend de la phase, et la distinction est **critique pour
  l'interprétation en aval** :

  | Phase | 0 item | Statut produit |
  |---|---|---|
  | Prospection **globale** | anormal — requêtes trop spécifiques, corrompues, ou blocage | `succes=False` + message explicite |
  | Prospection **restreinte à un subreddit** | information légitime — subreddit inexistant, inactif ou hors sujet | `succes=True`, `nb_items=0` + message explicatif |

- **Dégradation gracieuse.** Échec d'un run de prospection → les autres se
  poursuivent. Échec de la phase B → corpus de posts sans commentaires, limite
  explicite. Échec du scoring LLM → **le corpus est conservé** avec
  `pertinence=None`, jamais vidé. Échec total → `donnees_disponibles=false`,
  listes vides, statuts détaillés, **sans exception levée**.

---

## Limites méthodologiques

Ces limites sont injectées **systématiquement** dans le champ `limites` du
résultat. Elles ne sont pas décoratives : elles conditionnent toute lecture du
corpus.

1. **Reddit n'est pas représentatif** de la population d'un marché. La base
   d'utilisateurs est documentée comme jeune, majoritairement masculine,
   technophile et anglophone. Le corpus n'est **en aucun cas** un échantillon de
   consommateurs.
2. **Aucun filtre géographique natif n'existe sur Reddit.** La
   « régionalisation » repose uniquement sur le choix des subreddits et la
   langue des requêtes — c'est une approximation.
3. **La couverture varie fortement selon le pays.** Pour les marchés non
   anglophones, le volume peut être très faible. **L'absence de discussions ne
   constitue pas un signal d'absence de marché.**
4. Le **score de pertinence est une heuristique LLM non validée** et le seuil de
   rétention est arbitraire.
5. **La recherche Reddit est non exhaustive** : plafond ~1 000 posts par liste,
   `fastMode`, tri par pertinence opaque.
6. Les contenus se limitent à la **fenêtre de recherche configurée** : les
   opinions ont pu évoluer depuis.

Limites conjoncturelles ajoutées selon le déroulé : prospection partielle,
corpus non ou partiellement filtré, absence de commentaires, absence totale de
données, exclusion des posts à zéro commentaire de la phase B.

### Hypothèses

Injectées systématiquement dans le champ `hypotheses` :

- **Assimilation du produit aux requêtes retenues** — les discussions portent
  sur la catégorie de besoin visée, pas nécessairement sur la référence produit
  exacte. La justification de la chaîne de stratégie y est jointe.
- **Subreddits non vérifiés a priori** — proposés par un LLM, qui peut inventer
  des communautés inexistantes ou inactives. Leur existence n'est constatée
  qu'à l'exécution ; un run restreint à 0 post est une information, pas un échec.
- **Règle d'attribution de `portee`** — un post est `regionale` s'il provient
  d'un subreddit régional ciblé **ou** d'une requête rédigée dans la langue du
  marché (quand celle-ci n'est pas l'anglais) ; `globale` sinon.

  ⚠️ Cette règle est **volontairement grossière** et se trompe dans un cas
  observé : un post d'un subreddit anglophone généraliste remonté par une
  requête française est classé `regionale`. Reddit n'exposant aucune
  géolocalisation, aucune règle plus fine n'est disponible sans inférence
  supplémentaire — hors périmètre de ce module.

### Calibration du seuil de pertinence — mesurée, pas supposée

`SEUIL_PERTINENCE = 0.5` reste une heuristique, mais elle a été **contrôlée
empiriquement** sur un corpus réel (136 posts uniques, genouillère
orthopédique, `geo=US`), en rejouant le scoring sur les datasets bruts sans
relancer de collecte.

| Palier | Posts |
|---|---|
| 0,0 – 0,2 | 61 |
| 0,2 – 0,3 | 11 |
| 0,3 – 0,4 | 6 |
| 0,4 – 0,5 | 3 |
| **0,5 – 0,7** | 6 |
| 0,7 – 0,9 | 28 |
| 0,9 – 1,0 | 21 |

La distribution est **fortement bimodale** : 53 % des posts sous 0,3, 36 %
au-dessus de 0,7, et seulement **6,6 % dans toute la zone frontière
0,30–0,49**. Le scorer tranche nettement ; la position exacte du seuil a donc
peu d'incidence — l'abaisser de 0,5 à 0,3 n'ajouterait que 9 posts.

Relecture manuelle de ces 9 posts frontières : **6 sont correctement écartés**
(arthroscopie de cheville, luxation d'épaule, scoliose, guide de squats,
fléchisseurs de hanche), **1 seul est un manque réel** — un fil sur la
photobiomodulation pour l'arthrose du genou, soit une solution concurrente sur
le même besoin — et 2 sont discutables. L'échantillon sous 0,3 est du bruit
franc (vétérinaire canin, baskets de volley, développé couché).

**Conclusion : conserver 0,5.** Le gain d'un abaissement est marginal et se paie
en dilution du corpus et en budget de phase B.

En revanche, la calibration a mis au jour la vraie cause du bruit : **le choix
des subreddits thématiques**, traité par la règle de spécificité ci-dessous.

### Spécificité des subreddits thématiques

Sur le corpus ci-dessus, la chaîne avait retenu `r/Health` et `r/fitness` —
deux communautés **généralistes**. Résultat : 53 % de bruit franc (guides de
squats, tennis et longévité, étirements, mobilité des poignets). Or les bonnes
sources existaient et remontaient par la recherche globale :
`r/KneeInjuries`, `r/ACL`, `r/Osteoarthritis`, `r/MeniscusInjuries`,
`r/PatellaTracking`.

Le prompt impose donc désormais de viser la communauté dédiée au **problème, à
la pathologie, à la pratique ou à l'objet précis** que le produit adresse, et
**interdit** les agrégateurs généralistes (`r/Health`, `r/fitness`,
`r/AskReddit`, `r/LifeProTips`, `r/BuyItForLife`, `r/technology`, `r/gadgets`)
sauf absence réelle d'alternative plus précise.

Vérifié sur le cas en échec : la chaîne propose maintenant `r/KneeInjuries` et
`r/Osteoarthritis`.

### Requêtes dupliquées sur un marché anglophone

Lorsque `langue=en`, `requetes_marche` et `requetes_globales` sont toutes deux
en anglais et la chaîne produisait fréquemment les **mêmes formulations** des
deux côtés. Constaté sur un run réel : les six `searchTerms` envoyés étaient
trois requêtes répétées — la moitié du plafond de collecte partait en doublons,
facturés pour les mêmes posts.

Corrigé à deux niveaux : le prompt exige des **angles complémentaires** entre
les deux listes même en marché anglophone (intention d'achat et comparaison
d'un côté, problème vécu et efficacité réelle de l'autre), et
`agent._selectionner_requetes` dédoublonne les deux listes ensemble en filet de
sécurité, avec avertissement logué.

### Ancrage régional — pourquoi un subreddit du pays est imposé

La règle d'attribution de `portee` repose sur deux leviers : le subreddit
d'origine, et la langue de la requête. **Sur un marché anglophone, le second
levier est inopérant** — les requêtes marché et globales sont toutes en anglais,
la distinction n'a plus de sens.

Constat sur un run réel `--geo US --langue en` (genouillère orthopédique) : la
chaîne n'avait proposé **aucun** subreddit régional, ne retenant que
`r/Health` et `r/fitness`. Résultat : `repartition_par_portee` =
`{"globale": 56}`, **zéro post régional**. Les 56 posts venaient de
`r/KneeInjuries`, `r/ACL`, `r/Osteoarthritis`, `r/MuayThai`, `r/Skigear`… —
aucun rattaché à un pays. **L'argument `--geo US` n'avait eu aucun effet sur ce
qui était collecté.**

La chaîne de stratégie est donc désormais contrainte de proposer **exactement un
subreddit généraliste du pays**, choisi sur le seul critère du volume de
discussion, indépendamment du produit. Vérifié : `FR → r/france`,
`US → r/AskAnAmerican`, `MA → r/Morocco`.

Deux conséquences à assumer :

- **Un subreddit généraliste remonte peu de discussions sur un produit précis.**
  C'est attendu, et c'est une information : quand le run régional ne donne aucun
  post retenu, la limite `LIMITE_ANCRAGE_REGIONAL_FAIBLE` est ajoutée et
  interprète explicitement le vide comme une absence de discussion locale, **pas
  comme une absence de marché**.
- Le run régional coûte un démarrage (0,02 $) même s'il ne rapporte rien. C'est
  le prix de la traçabilité géographique.

#### Rattrapage — la règle « obligatoire » ne suffit pas

Un LLM n'est pas contraignable, et l'omission a été **constatée en pratique** :
sur un test `geo=BR`, la chaîne renvoyait une liste régionale vide alors que
`r/brasil` compte plus d'un million de membres. Une simple relance ne sert à
rien — à `TEMPERATURE_LLM = 0`, la même entrée redonne la même réponse.

`strategy._completer_subreddit_regional` effectue donc un **appel dédié et
court**, à l'entrée différente (le seul code pays), déclenché uniquement en cas
d'omission. Vérifié : `BR` → liste vide → rattrapage → `r/brasil`.

Si le rattrapage échoue lui aussi, un avertissement est logué et la limite
`LIMITE_SANS_ANCRAGE_REGIONAL` est ajoutée au résultat : le corpus est alors
explicitement déclaré sans rapport établi avec le marché étudié.

#### Rien n'est codé en dur

La stratégie est **intégralement dérivée** de la fiche produit et du code pays à
chaque exécution. Les subreddits cités dans les prompts sont des exemples
d'orientation, pas une table de correspondance. Vérifié sur des produits et
marchés sans rapport entre eux :

| Produit | `geo` | Régional | Thématiques |
|---|---|---|---|
| Machine à pâtes fraîches | IT | `r/italy` | `r/Cooking`, `r/ItalianFood` |
| Poussette tout-terrain | MA | `r/Morocco` | `r/Parenting`, `r/BabyGear` |
| Souris gaming 58 g | GB | `r/unitedkingdom` | `r/MouseReview`, `r/CompetitiveGaming` |
| Sérum vitamine C | BR | `r/brasil` | `r/SkincareAddiction`, `r/30PlusSkinCare` |
| Tapis de yoga | VN | `r/vietnam` | `r/yoga`, `r/FitnessVietnam` |
| Cafetière italienne | PL | `r/Polska` | `r/coffee`, `r/espresso` |

Les requêtes suivent la langue du marché — italien, français, anglais,
portugais, vietnamien, polonais — accents et alphabets non latins inclus.

---

## Structure du résultat

```jsonc
{
  "produit": { "nom": "…", "description": "…", "categorie": "…" },
  "marche": { "geo": "FR", "langue": "fr" },
  "alertes_qualite_input": [ { "type": "contradiction", "detail": "…" } ],
  "strategie": {
    "requetes_marche": ["…"], "requetes_globales": ["…"],
    "subreddits_regionaux": ["r/…"], "subreddits_thematiques": ["r/…"],
    "justification": "…"
  },
  "posts": [ {
    "id": "t3_…", "titre": "…", "texte": "…", "subreddit": "r/…", "url": "…",
    "date_creation": "2026-06-04T07:56:49Z", "score": 12, "nb_commentaires": 34,
    "portee": "regionale", "origine": "recherche_globale", "pertinence": 0.9,
    "auteur_pseudonymise": "8c63d10cb36c6134", "requete_source": "…"
  } ],
  "commentaires": [ {
    "id": "…", "id_post": "t3_…", "texte": "…",
    "date_creation": "…", "score": 4, "profondeur": 0,
    "auteur_pseudonymise": "a83d95ab4ca9941c"
  } ],
  "stats": {
    "nb_posts_collectes": 0, "nb_posts_retenus": 0, "nb_posts_approfondis": 0,
    "nb_commentaires": 0, "repartition_par_subreddit": {},
    "repartition_par_portee": {},
    "date_plus_ancienne": null, "date_plus_recente": null
  },
  "statuts_collecte": [ {
    "phase": "prospection_globale", "cible": "…", "succes": true,
    "message_erreur": null, "nb_items": 0, "nb_tentatives": 1
  } ],
  "donnees_disponibles": true,
  "limites": ["…"],
  "hypotheses": ["…"]
}
```

`nb_posts_collectes` compte les posts **dédoublonnés avant filtrage** ;
`nb_posts_retenus` compte ceux qui ont passé le seuil de pertinence. Les
répartitions portent sur les posts retenus.

---

## Validation

Validé par **exécution réelle de bout en bout** via la CLI (aucun test
automatisé, conformément au périmètre). Contrôles passés sur la sortie du run
de référence décrit plus haut :

- sortie `stdout` = JSON valide, conforme à `ResultatCollecteReddit` après
  `model_validate` ;
- **accents corrects** — 18 titres accentués, requêtes marché intactes
  (`écouteurs conduction osseuse avis`) dans le payload logué comme dans la
  sortie ;
- `portee`, `origine` et `pertinence` renseignés sur les 64 posts (pertinence de
  0,50 à 0,95, aucun `None`) ; aucun doublon d'`id` ; répartition `regionale`
  44 / `globale` 20 ; origines `recherche_globale` 49 / `subreddit_cible` 15 ;
- **aucun pseudonyme ni champ de profil en clair** : `authorName`, `authorId`,
  `authorFullname`, `authorPremium`, `authorFlairText`, `parsedAuthorId`,
  `subredditSubscribers` absents de la sortie ; 100 % des empreintes au format
  `[0-9a-f]{16}` ou `anonyme` ;
- 267 commentaires, tous rattachés à un post du corpus ;
- 5 runs Apify, conformes au plafond `1 + NB_MAX_SUBREDDITS_CIBLES + 1`, tous en
  succès à la première tentative.

### Dégradation gracieuse — validée sur une panne réelle

Un run de validation antérieur a rencontré une panne non simulée : le quota
mensuel du compte Apify alors utilisé s'est épuisé en cours d'exécution
(`ForbiddenError: Monthly usage hard limit exceeded`). Comportement observé,
conforme à la spécification :

- les deux tentatives de la phase B ont échoué avec backoff 5 s puis 20 s,
  **sans exception propagée** ;
- le `StatutCollecte` de la phase `commentaires` portait `succes=false` et le
  message d'erreur exact renvoyé par Apify ;
- l'agent a retourné le corpus de posts **sans commentaires**, avec la limite
  explicite « Phase B non aboutie » ;
- code de sortie du processus : **0**, JSON complet sur `stdout`.

Enseignement à retenir : **un compte Apify en fin de quota termine ses runs en
`SUCCEEDED` avec beaucoup moins d'items que demandé**, sans avertissement
intermédiaire. Toute mesure de volume ou de coût prise dans cet état est
invalide — vérifier `client.user("me").limits()` avant une campagne de mesure.

---

## Hors périmètre

Ce module ne contient et ne prépare **aucune** : persistance (base de données,
ORM, fichier de sortie, cache), API HTTP ou serveur web, interface graphique,
authentification, suite de tests automatisés, analyse de sentiment ou synthèse
d'insights. Il retourne un objet en mémoire et l'affiche en JSON. La validation
s'est faite par **exécution réelle de bout en bout**, pas par tests unitaires.
