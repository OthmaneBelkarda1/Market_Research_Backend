# Agent IA d'Étude de Marché E-commerce — Backend

Backend FastAPI du pipeline d'étude de marché e-commerce (projet interne Marketing Confort).
À terme, il orchestrera les features F1 → F7 (cadrage produit, collecte multi-sources, analyses
LLM, module PLC, restitution) autour d'une base PostgreSQL Supabase servant de point d'échange
entre la collecte et l'analyse.

**Itération courante : socle du projet, F1, extraction automatique d'une fiche produit, et
études de marché exécutées de bout en bout (F8.1 + F8.2).**

- `POST /products` — reçoit une fiche produit en JSON, la valide et la persiste dans la
  table `product`. Le champ optionnel `image_url` accepte l'URL d'une image déjà hébergée.
- `POST /products/extract` — **saisie automatique** : on n'envoie qu'une URL de page
  produit et une région ; l'agent lit la page et la fiche qu'il produit est stockée dans
  la même table `product`. Voir [Extraction automatique](#extraction-automatique).
- `POST /products/{product_id}/image` — endpoint séparé : téléverse un fichier image dans
  Supabase Storage et fait pointer la fiche vers son URL publique.
- `POST /studies`, `GET /studies`, `GET /studies/{study_id}`, et la lecture des résultats
  de collecte `GET /studies/{study_id}/sources[/{source}]` — **socle des études de
  marché** : une étude est créée automatiquement dès qu'une fiche produit est enregistrée.
  Voir [Études de marché](#études-de-marché--socle-f81).
- `src/agents/` contient les agents IA : l'agent d'extraction et le **pipeline d'étude de
  marché** (11 modules). Ils sont invoqués depuis la couche `service.py` des domaines,
  jamais depuis les routers.
- Ni authentification, ni autorisation, ni Docker : installation et exécution directes sur
  la machine, base de données hébergée sur Supabase.

## Prérequis

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (recommandé) ou `pip`
- Un projet Supabase (base PostgreSQL + Storage)
- **Chromium pour Playwright** (voir [Extraction automatique](#extraction-automatique))

## Installation

Avec uv :

```bash
uv sync
```

Avec pip :

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS / Linux
pip install -e . --group dev
```

Puis, dans les deux cas, le navigateur utilisé par l'agent d'extraction :

```bash
uv run playwright install chromium
```

Sur Linux, les bibliothèques système que Chromium exige ne sont pas installées par `pip` :

```bash
uv run playwright install --with-deps chromium   # installe aussi les paquets systeme (sudo)
```

Sans ce navigateur, tout le reste de l'API fonctionne ; seules les extractions rendues par
un navigateur échouent en `502`. L'application le signale au démarrage par un
avertissement dans les logs — elle ne plante pas.

## Configuration

```bash
cp .env.example .env           # Windows : copy .env.example .env
```

Puis renseigner `.env` :

| Variable | Où la trouver |
|---|---|
| `ENVIRONMENT` | `local`, `testing`, `staging` ou `production`. `/docs` n'est exposé qu'en `local` et `staging`. |
| `DATABASE_URL` | Dashboard Supabase → **Settings → Database → Connection string**, onglet **Session pooler**. |
| `TEST_DATABASE_URL` | Même format, mais pointant vers une base Supabase **dédiée aux tests**. |
| `SUPABASE_URL` | Dashboard Supabase → **Settings → API** → *Project URL*. |
| `SUPABASE_SERVICE_ROLE_KEY` | Dashboard Supabase → **Settings → API** → clé `service_role`. Secret : jamais commitée. |
| `SUPABASE_STORAGE_BUCKET` | `product-images` (voir ci-dessous). |
| `OPENAI_API_KEY` | Secret. Requis uniquement si `use_agent: true` sur `/products/extract`. |
| `APIFY_API_TOKEN` | Secret. Requis uniquement pour les places de marché (Amazon, Temu, AliExpress, Walmart, eBay). |
| `PRODUCT_COUNTRY`, `PRODUCT_VARIANTS`, `OPENAI_MODEL` | Réglages de l'agent, lus **une seule fois** à son import. |
| `EXTRACTION_ALLOWED_REGIONS` | Régions ISO acceptées. Défaut : `MA,FR,ES,US,AE`. |
| `EXTRACTION_MAX_CONCURRENCY` | Extractions simultanées. Défaut : `2`. |
| `EXTRACTION_TIMEOUT_SECONDS` | Budget par extraction, attente incluse. Défaut : `300`. |
| `STUDY_AUTO_START` | Une étude est créée automatiquement à l'enregistrement d'un produit. Défaut : `true`. |
| `STUDY_ALLOWED_REGIONS` | Régions ISO pour lesquelles une étude peut être lancée. Défaut : `MA,FR,ES,US,AE`. |

Les deux secrets passent **exclusivement** par variable d'environnement : jamais en dur
dans le code, jamais dans les logs, jamais dans une réponse d'erreur.

### ⚠️ Effet de bord `.env`

`src/agents/product_extraction/config.py` appelle `load_dotenv(override=True)` à son
import. Les valeurs de `.env` **écrasent** donc les variables d'environnement réellement
injectées par le shell, la CI ou systemd — `DATABASE_URL` et `SUPABASE_*` compris.

Conséquences concrètes :

- Si tu injectes ces variables autrement que par `.env`, **supprime le `.env` du disque**
  ou tiens les deux alignés, sinon le fichier gagne silencieusement.
- La configuration de l'application, elle, reste déterministe : `src/products/__init__.py`
  instancie chaque `BaseSettings` du projet **avant** que l'agent puisse toucher à
  `os.environ` (voir sa docstring).
- `tests/conftest.py` capture `TEST_DATABASE_URL` avant tout import de `src`, pour que
  `TEST_DATABASE_URL=... uv run pytest` continue de primer sur `.env`.

Adaptation de la chaîne de connexion Supabase pour SQLAlchemy async :

1. partir de la chaîne **Session pooler** fournie par le Dashboard ;
2. remplacer le schéma `postgresql://` par `postgresql+asyncpg://` ;
3. supprimer les paramètres de requête éventuels (`?sslmode=require`, `?pgbouncer=true`…),
   non supportés par `asyncpg` ;
4. URL-encoder le mot de passe s'il contient des caractères spéciaux.

```
postgresql+asyncpg://postgres.<project-ref>:<mot-de-passe>@<host>.pooler.supabase.com:5432/postgres
```

### Bucket Supabase Storage

Dashboard Supabase → **Storage → New bucket** :

- nom : `product-images`
- **Public bucket** : activé (les `image_url` renvoyées par l'API sont des URLs publiques)

Le bucket n'est nécessaire que pour `POST /products/{product_id}/image`. Seule l'URL de
l'image est stockée en base (`product.image_url`) ; le binaire reste dans le bucket.

## Migrations

```bash
uv run alembic upgrade head        # crée `product` puis les 4 tables `study*`
```

Créer une nouvelle migration (le nom du fichier suit `%(year)-%(month)-%(day)_%(slug)s`) :

```bash
uv run alembic revision --autogenerate -m "add something"
```

## Lancer l'application

```bash
uv run uvicorn src.main:app --reload
```

- API : http://127.0.0.1:8000
- Documentation OpenAPI : http://127.0.0.1:8000/docs

### Note : Windows et `--reload`

`--reload` fonctionne, y compris pour les extractions. Une ligne apparaît dans les logs :

```
This event loop cannot start the Playwright driver (Windows + uvicorn
--reload/--workers>1 forces a SelectorEventLoop). Running the extraction on a dedicated
ProactorEventLoop thread instead.
```

C'est informatif, pas une erreur. Explication : sur Windows, `uvicorn` bascule sur un
`SelectorEventLoop` dès que `--reload` **ou** `--workers > 1` est utilisé
([`uvicorn/loops/asyncio.py`](https://github.com/encode/uvicorn/blob/master/uvicorn/loops/asyncio.py) :
`use_subprocess = reload or workers > 1`). Or cette boucle ne sait pas créer de
sous-processus, et Playwright démarre son driver comme un sous-processus — le navigateur
ne pourrait donc pas démarrer du tout.

`_on_browser_capable_loop` ([src/products/extraction.py](./src/products/extraction.py))
détecte le cas et exécute l'extraction dans un thread dédié possédant sa propre
`ProactorEventLoop`. La boucle principale reste en `await` : rien n'est bloqué. Sur Linux,
macOS, et sur Windows sans `--reload`, ce chemin n'est pas emprunté — l'agent tourne
directement sur la boucle de la requête, sans thread ajouté.

Seule réserve : en cas de timeout, la boucle principale cesse d'attendre mais le thread ne
peut pas être tué et va jusqu'au bout ; le créneau du sémaphore n'est donc libéré qu'à ce
moment-là.

Créer une fiche produit (F1) :

```bash
curl -X POST http://127.0.0.1:8000/products \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Chaise de bureau ergonomique",
        "description": "Chaise à assise réglable, dossier maillé.",
        "category": "Mobilier de bureau",
        "region": "FR",
        "image_url": "https://example.com/chaise.png"
      }'
```

`region` est le **pays** de l'étude de marché : un code ISO 3166-1 alpha-2 de la liste
blanche `EXTRACTION_ALLOWED_REGIONS` (`fr` est normalisé en `FR`, toute autre valeur est
refusée en `422`). Enregistrer la fiche déclenche automatiquement une étude — voir
[Études de marché](#études-de-marché--socle-f81).

Téléverser une image pour cette fiche (endpoint séparé, remplace `image_url`) :

```bash
curl -X POST http://127.0.0.1:8000/products/<product_id>/image \
  -F "image=@chaise.png"
```

## Les endpoints

Huit au total, dans deux domaines. Ni authentification ni autorisation à cette itération :
tout ce qui suit est ouvert.

### `products` — les fiches produit

| Méthode | Chemin | Rôle | Codes |
|---|---|---|---|
| `POST` | `/products` | Crée une fiche produit à la main. Déclenche une étude si `STUDY_AUTO_START`. | `201` `422` |
| `POST` | `/products/extract` | Même résultat depuis une **URL de page produit** : l'agent lit la page et en tire la fiche. Déclenche une étude de la même façon. | `201` `422` `500` `502` `504` |
| `POST` | `/products/{product_id}/image` | Téléverse l'image d'une fiche dans Supabase Storage et fait pointer la fiche vers son URL publique. Endpoint séparé, car il parle `multipart` et non JSON. | `200` `404` `413` `422` `502` |

**Le domaine est en écriture seule** : il n'existe ni `GET /products` ni
`GET /products/{id}`. Une fiche se relit aujourd'hui par les études qui la référencent, ou
directement en base. C'est un manque assumé de cette itération, pas un oubli.

### `studies` — les études de marché

| Méthode | Chemin | Rôle | Codes |
|---|---|---|---|
| `POST` | `/studies` | Lance une étude pour une fiche existante. Répond immédiatement en `202` : une étude dure 30 à 60 min. | `202` `404` `409` `422` |
| `GET` | `/studies` | Historique : filtres `product_id` / `status`, pagination `limit`/`offset`, plus récentes d'abord. | `200` `422` |
| `GET` | `/studies/{study_id}` | État d'une étude : statut, `progress` par module, `phase_durations`, `error`. **C'est l'endpoint qu'on interroge en boucle** pendant qu'elle tourne. | `200` `404` `422` |
| `GET` | `/studies/{study_id}/sources` | Bilan de collecte : une entrée par collecteur, **sans** les JSON collectés. | `200` `404` `422` |
| `GET` | `/studies/{study_id}/sources/{source}` | Le JSON brut d'un collecteur, tel que le pipeline l'a produit. | `200` `404` `422` |

Détail des deux derniers : [Lire les résultats de collecte](#lire-les-résultats-de-collecte).
Détail des codes : [Codes de réponse](#codes-de-réponse-1).

Un parcours complet, du produit au JSON collecté :

```bash
# 1. Une fiche produit, à la main ou depuis une URL
curl -X POST http://127.0.0.1:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Ceinture lombaire", "description": "Double sangle de traction.",
       "category": "sante-bien-etre", "region": "FR"}'

# 2. L'étude est déjà partie (STUDY_AUTO_START) ; sinon, à la main
curl -X POST http://127.0.0.1:8000/studies \
  -H "Content-Type: application/json" \
  -d '{"product_id": "<product_id>", "region": "FR"}'

# 3. Suivre son avancement (statut, progress par module, phase_durations)
curl http://127.0.0.1:8000/studies/<study_id>

# 4. Voir comment chaque collecteur s'en est sorti, puis lire l'un d'eux
curl http://127.0.0.1:8000/studies/<study_id>/sources
curl http://127.0.0.1:8000/studies/<study_id>/sources/reddit
```

**Ce qui n'existe pas encore** : la restitution `GET /studies/{id}/report` (rapport et
verdict), la relance et l'annulation d'une étude, la suppression, et toute lecture des
tables `study_analysis` et `study_report` par l'API. Voir
[Hors périmètre](#hors-périmètre-de-cette-itération).

## Extraction automatique

`POST /products/extract` remplace la saisie manuelle : on envoie une URL de page produit
et une région, l'agent `src/agents/product_extraction/` lit la page, et la fiche qu'il en
tire (nom, description, catégorie, image) est stockée dans la table `product` — la même
que F1.

```bash
curl -X POST http://127.0.0.1:8000/products/extract \
  -H "Content-Type: application/json" \
  -d '{
        "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        "region": "MA",
        "use_agent": false
      }'
```

```json
{
  "product": {
    "id": "c8b8bbea-1649-4356-903d-2bcfea0cecda",
    "name": "A Light in the Attic",
    "description": "A Light in the Attic, listed under Poetry. It is priced at 51.77 GBP…",
    "category": "Poetry",
    "region": "MA",
    "image_url": "https://books.toscrape.com/media/cache/fe/72/fe72f0532301ec28892ae79a629a293c.jpg",
    "created_at": "2026-07-25T15:53:01+0000",
    "updated_at": "2026-07-25T15:53:01+0000"
  },
  "source_url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
  "warnings": []
}
```

### La région

`region` est **obligatoire** et n'est **jamais déduite** de l'URL ni de son TLD. Les prix,
la devise et la disponibilité dépendent du pays d'où l'on regarde : la région envoyée fixe
la locale, le fuseau et l'`Accept-Language` du navigateur, et le pays de proxy du scraper
hébergé. C'est aussi la valeur écrite dans `product.region`.

Elle doit être un code ISO 3166-1 alpha-2 de la liste blanche `EXTRACTION_ALLOWED_REGIONS`
(`MA,FR,ES,US,AE` par défaut) ; la casse est normalisée (`fr` → `FR`). Toute autre valeur
est rejetée en `422` avec la liste des valeurs acceptées.

> **Note d'implémentation.** L'agent fige `PRODUCT_COUNTRY` à son import : une région par
> requête n'est pas supportée nativement. Le chemin navigateur l'est déjà (locale, fuseau
> et `Accept-Language` sont des paramètres d'appel) ; pour le chemin Apify,
> `src/products/extraction.py` enregistre au démarrage un clone d'adaptateur par
> (acteur × région autorisée) — clé `amazon@MA` — via le point d'extension
> `register_adapter()` de l'agent. Aucune ligne de l'agent n'est modifiée, aucun état
> global n'est muté, et les extractions restent parallèles.

### Durées et limites

| Aspect | Valeur |
|---|---|
| Durée typique | **10 s à 2 min** — un vrai navigateur est démarré, ou un run de scraper hébergé est attendu |
| Mesuré ici | 6,7 s books.toscrape.com et 10,7 s scrapeme.live (`use_agent: false`) ; 111 s gymshark.com (`use_agent: true`, boutique Shopify lourde) |
| Concurrence | `EXTRACTION_MAX_CONCURRENCY` (défaut 2) — chaque extraction démarre son propre Chromium (~300 Mo) |
| Timeout | `EXTRACTION_TIMEOUT_SECONDS` (défaut 300), attente d'un créneau incluse → `504` |

L'appel est **synchrone** : le client attend la fin de l'extraction. C'est volontaire pour
cette itération. Si la latence devient un problème côté front, l'évolution recommandée est
une table `extraction_job` en base ou une file (Arq / Celery) — pas un store en mémoire,
qui perdrait les jobs au redémarrage et ne survivrait pas à plusieurs workers uvicorn.

### `use_agent`

| Valeur | Effet |
|---|---|
| `true` (défaut) | Un LLM normalise les champs extraits. Requiert `OPENAI_API_KEY`, consomme des tokens. |
| `false` | Extraction déterministe (JSON-LD, Shopify, microdata, Open Graph, heuristiques HTML). Aucun appel LLM, aucun coût. |

### Ce qui n'est jamais inventé

`name`, `description` et `category` sont `NOT NULL` en base. Si l'extraction n'en produit
pas, la requête échoue en `422` (`Missing: …`) plutôt que de stocker une fiche à moitié
vide. L'image est la seule partie optionnelle : une extraction sans image donne quand même
une ligne, `image_url` à `null` et un avertissement dans `warnings`.

### Codes de réponse

| Code | Cas |
|---|---|
| `201` | Fiche extraite et stockée |
| `422` | `url` invalide ; `region` absente, malformée ou hors liste blanche ; extraction trop incomplète |
| `500` | Agent non configuré (secret manquant) ou erreur serveur inattendue |
| `502` | Page non chargeable (réseau, timeout, blocage anti-bot) ou run du scraper hébergé en échec |
| `504` | Extraction au-delà de `EXTRACTION_TIMEOUT_SECONDS` |

### L'agent

`src/agents/product_extraction/` est intégré **tel quel** : aucune de ses lignes n'est
modifiée, et il est exclu de `ruff` (`extend-exclude`). Tout changement de comportement
passe par ses variables d'environnement ou par ses points d'extension `register_domain()`
/ `register_adapter()`. Un seul module du projet l'importe :
[`src/products/extraction.py`](./src/products/extraction.py).

## Études de marché

Une **étude** est l'unité de travail du produit : *un produit, une région, une langue*.
Elle exécute le pipeline de [`src/agents/market_study/`](./src/agents/market_study/) —
6 collecteurs puis 5 agents d'analyse — et dépose tout ce qu'ils produisent en base.

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/studies` | Lance une étude pour une fiche produit (202) |
| `GET` | `/studies` | Historique : filtres `product_id` / `status`, pagination `limit`/`offset`, tri `created_at` décroissant |
| `GET` | `/studies/{study_id}` | État d'une étude : statut, `progress` par module, `error` |
| `GET` | `/studies/{study_id}/sources` | Bilan de collecte : une entrée par collecteur (`status`, `error`, `exit_code`, durée), **sans** les JSON collectés |
| `GET` | `/studies/{study_id}/sources/{source}` | Le JSON brut d'un collecteur, tel que le pipeline l'a produit |

```bash
curl -X POST http://127.0.0.1:8000/studies \
  -H "Content-Type: application/json" \
  -d '{"product_id": "c8b8bbea-1649-4356-903d-2bcfea0cecda", "region": "MA"}'

curl "http://127.0.0.1:8000/studies?product_id=c8b8bbea-1649-4356-903d-2bcfea0cecda&limit=20"
curl http://127.0.0.1:8000/studies/<study_id>
curl http://127.0.0.1:8000/studies/<study_id>/sources
curl http://127.0.0.1:8000/studies/<study_id>/sources/reddit
```

### Lire les résultats de collecte

Les deux endpoints sont séparés parce qu'un seul `payload` de collecteur pèse souvent
plusieurs mégaoctets : le bilan reste consultable en boucle pendant qu'une étude tourne,
et le JSON n'est transféré que pour la source réellement demandée.

- Une source **apparaît dès qu'elle a fini** : la liste se remplit pendant la collecte.
  Elle est triée par nom de source, jamais par date, pour qu'un *polling* ne réordonne pas
  ce qui a déjà été renvoyé — les collecteurs tournent en parallèle et finissent dans un
  ordre imprévisible.
- `payload` est `null` dès que `status` n'est pas `succeeded` ; `error` dit pourquoi.
- **`404` sur une source ≠ collecte vide** : il signifie que le collecteur n'a pas encore
  écrit sa ligne (étude encore en collecte, ou jamais arrivée jusque-là). Un collecteur
  qui a tourné sans rien trouver a bien une ligne, en `succeeded`.
- Un nom de source inconnu est refusé en `422` (valeurs admises : les six de
  `study_source_data` ci-dessous).

### Déclenchement automatique

Enregistrer un produit — par `POST /products` comme par `POST /products/extract` — crée
une étude pour la région de la fiche, avec `trigger_source` à `products` ou `extractions`.
`STUDY_AUTO_START=false` désactive ce comportement sans toucher au reste.

Deux garanties :

- **L'échec de la création d'étude ne fait jamais échouer l'enregistrement du produit.**
  La fiche est déjà commitée quand le hook s'exécute ; un problème est journalisé en
  `WARNING`, la réponse reste `201`.
- **Aucune région n'est devinée.** Les lignes `product` antérieures à ce lot portent un
  libellé d'affichage (« Ile-de-France ») et non un pays : elles ne déclenchent aucune
  étude, et rien n'est déduit de leur contenu.

### Verrou anti-doublon

Une étude coûte ~2 $ de crédits LLM plus des crédits Apify et dure 30 à 60 minutes : le
doublon accidentel est le premier risque opérationnel. Tant qu'une étude est **active**
(`created`, `collecting`, `analyzing`, `reporting`) pour un couple (produit, région), un
second `POST /studies` renvoie **409** avec l'identifiant de l'étude en cours :

```json
{"detail": {"message": "A study is already running for this product and region…",
            "study_id": "8f14e45f-ceea-467a-9b2f-ee8f1f2f3a10"}}
```

Une étude terminée (`completed`, `partial`, `failed`) ne bloque rien : la relance est un
cas d'usage prévu.

### Modèle de données

Quatre tables, motif *blackboard* : le pipeline y dépose ses sorties, les agents aval les
y relisent.

| Table | Contenu |
|---|---|
| `study` | L'étude : produit, région, langue, devise, statut, `progress`, `error`, horodatages |
| `study_source_data` | Une ligne par collecteur (`google_trends`, `reddit`, `recherche_web`, `aliexpress`, `amazon`, `meta_ads`) : `payload` jsonb, `status` (`succeeded`/`failed`/`skipped_region`), `exit_code` |
| `study_analysis` | Une ligne par agent d'analyse (`f3_insights`, `f4_concurrence`, `f5_verdict`, `f6_plc`) |
| `study_report` | Le rapport F7 : `rapport_markdown`, `resume_markdown`, `payload` |

Les sorties sont stockées en `jsonb` et non éclatées en colonnes : leurs schémas sont des
**contrats JSON versionnés par le pipeline**, que les agents aval redéclarent en Pydantic
`extra="ignore"` pour les seuls champs qu'ils consomment. Les répliquer en colonnes
créerait une double maintenance de ces contrats.

`skipped_region` (code de sortie 3 d'un collecteur) est une **situation normale**, pas un
échec : Amazon n'a pas de site marocain, par exemple.

## Exécution d'une étude

### Ce qu'elle coûte, ce qu'elle dure

| | |
|---|---|
| **Durée** | **~40 min** : ~13 min de collecte, 27,4 min d'analyse (mesuré) |
| **Coût** | **~2,3 $** d'API Anthropic pour l'analyse (67 appels), **plus des crédits Apify** pour la collecte |
| **Concurrence** | **une étude à la fois** (`STUDY_MAX_CONCURRENCY`) |
| **Détail par module** | [`docs/pipeline_contrats.md`](./docs/pipeline_contrats.md) §7 |

Les deux seuls leviers de coût de la collecte : `STUDY_AMAZON_AVIS` (Amazon facture **un
run d'actor par produit enrichi d'avis**) et `STUDY_META_ANNONCES` (Meta facture **à
l'annonce**). Pour un run de validation bon marché : `STUDY_AMAZON_AVIS=0`,
`STUDY_META_ANNONCES=10`.

### Clés requises

`ANTHROPIC_API_KEY` (les 11 modules), `APIFY_TOKEN` (5 collecteurs, `APIFY_API_TOKEN` en
repli), `SEL_ANONYMISATION` (anonymisation Reddit) et le quadruplet `ALIEXPRESS_*`.
Une clé manquante **n'empêche pas le démarrage** : un avertissement est journalisé par clé
au lancement, et le module concerné échoue en le disant — le reste de l'étude continue.

### ⚠️ Un seul worker uvicorn

Les études sont des `asyncio.Task` **dans le processus**, protégées par un sémaphore
in-process. Avec plusieurs workers, chacun aurait son propre sémaphore et lancerait ses
propres études : le verrou anti-doublon ne tiendrait plus. Lancer sans `--workers`, comme
pour l'extraction.

Au démarrage, toute étude restée `collecting` / `analyzing` / `reporting` dans une vie
antérieure du processus est passée à `failed` avec le code `INTERRUPTED_BY_RESTART` : elle
est relançable par `POST /studies`, et **tout ce qui avait été collecté reste en base**.
La reprise fine (repartir de l'étape interrompue) est hors périmètre.

### Déroulé

```
created → collecting : 6 collecteurs simultanés (STUDY_COLLECT_PARALLEL, défaut 6)
                       ── barrière : les 6 terminés, quel que soit leur statut ──
        → analyzing  : F3 ∥ F4, puis F5 (dès que les deux sont finis), puis F6
        → reporting  : F7 → table study_report
        → completed | partial | failed
```

La phase de collecte coûte donc le **collecteur le plus lent**, pas leur somme : les six
sont indépendants par conception (sources disjointes, fichiers de sortie disjoints, aucun
ne lit la sortie d'un autre). Le coût API est inchangé — mêmes appels, mêmes tokens.

Le parallélisme est **borné par un sémaphore**, jamais un `gather` libre : les vraies
limites ne sont pas dans le code mais dans les comptes (runs d'actors Apify concurrents —
5 collecteurs sur 6 passent par Apify —, débit Anthropic, RAM des 6 sous-processus). En cas
d'erreurs de quota récurrentes, `STUDY_COLLECT_PARALLEL=3` est le levier de repli, sans
redéploiement.

Un échec ou un timeout de collecteur n'interrompt **jamais** les autres, et F5 démarre dès
que F3 et F4 sont terminés *quel que soit leur statut* : la dégradation gracieuse appartient
aux modules, jamais à l'orchestrateur.

- `completed` : aucun module en échec (`skipped_region` ne compte pas).
- `partial` : au moins un module en échec, mais F7 a produit un rapport.
- `failed` : F7 en échec, ou étude inexécutable (devise non mappée, produit disparu, tous
  les collecteurs en échec). **Un verdict négatif ou indéterminé est un résultat, jamais un
  échec.**

Chaque sortie est écrite en base **dès sa réception**, et `study.progress` est mis à jour à
chaque transition : un crash en cours d'étude laisse en place tout ce qui a été collecté.

### Durées et concurrence

`study.progress.phase_durations` donne le temps de chaque phase, en secondes :

```json
{"collecting": 512.4, "analyzing": 1287.9, "reporting": 143.2, "total": 1948.1}
```

À la fin de chaque étude, un récapitulatif est journalisé : durée par module, durée par
phase, total, et **gain vs séquentiel** = (somme des durées des modules) − (durée totale
mesurée). C'est ce que l'étude aurait coûté si chaque module avait attendu le précédent.

Points durs de la concurrence, pour qui touchera à `runner.py` :

- **Une `AsyncSession` par tâche.** Une session n'est pas sûre à partager entre tâches
  concurrentes ; chaque collecteur et chacun de F3/F4 ouvre la sienne.
- **`progress` est fusionné en SQL** (`progress = progress || :patch`), jamais en
  lecture-modification-écriture côté Python. C'est ce qui évite les *lost updates* : à six
  écrivains, deux collecteurs qui finissent ensemble réécriraient chacun un dictionnaire
  amputé de l'entrée de l'autre. La fusion est superficielle, et chaque clé de premier
  niveau n'a qu'un seul écrivain — un module, ou l'orchestrateur pour `phase_durations`.
- **Les lignes de résultat sont écrites en `INSERT … ON CONFLICT DO UPDATE`**, sur les
  contraintes `UNIQUE` existantes : c'est Postgres qui arbitre, pas une lecture suivie
  d'une écriture dans une autre transaction.
- Les logs sont préfixés par le module (`[aliexpress] …`, `[f4_concurrence] …`) : en
  exécution entrelacée, c'est la seule façon de les relire.

### ⚠️ Windows : `--reload` empêche toute étude de démarrer

Les onze modules du pipeline sont des **sous-processus**, et sur Windows `uvicorn` bascule
sur une `SelectorEventLoop` dès `--reload` ou `--workers > 1`. Cette boucle ne sait pas
créer de sous-processus : `asyncio.create_subprocess_exec` y lève `NotImplementedError`,
et l'étude échouerait dès son premier module.

`_on_subprocess_capable_loop` ([src/studies/runner.py](./src/studies/runner.py)) applique le
même remède que l'extraction : le module est exécuté sur un thread possédant sa propre
`ProactorEventLoop`, la boucle principale restant en `await`. Le repli est **par module** et
non par étude, parce que le moteur de base de données et son pool sont liés à la boucle
principale — y déplacer l'étude entière y entraînerait toutes ses sessions.

Sur Linux, macOS, et sur Windows sans `--reload`, ce chemin n'est pas emprunté. Même réserve
que pour l'extraction : en cas de timeout, le thread ne peut pas être tué et le créneau du
sémaphore n'est libéré qu'à son retour réel.

### Espace de travail

`var/studies/{study_id}/` (gitignoré) contient les fichiers du pipeline lui-même —
`tendances.json`, `amazon.json`, …, `rapport_etude.md`. Ce sont eux qui font foi en cas de
doute sur une formulation du rapport, d'où `STUDY_KEEP_WORKDIR=true` par défaut. **La purge
est manuelle** : `rm -rf var/studies/*` (Windows : `Remove-Item var\studies\* -Recurse`).

### Le pipeline

`src/agents/market_study/` est intégré **tel quel** : 149 fichiers copiés, hachages
vérifiés, aucune ligne modifiée, exclu de `ruff`. Ses onze modules restent des exécutables
autonomes appelés en sous-processus — jamais importés. Un seul module du backend le
connaît : [`src/studies/runner.py`](./src/studies/runner.py), qui n'a d'autre référence que
[`docs/pipeline_contrats.md`](./docs/pipeline_contrats.md).

Il reste utilisable seul, sans l'API :

```powershell
cd src\agents\market_study
.\etude_marche.ps1 -Nom "Ceinture lombaire" -Description "..." -Categorie "sante-bien-etre" -Geo MA
```

## Tests

Les tests s'exécutent contre une **vraie base PostgreSQL** (`TEST_DATABASE_URL`) ; seuls
l'écriture dans Supabase Storage, l'agent d'extraction et le pipeline d'étude sont
remplacés par des faux. Sans `TEST_DATABASE_URL`, la suite est ignorée (`skipped`).

**Aucun test n'appelle un vrai collecteur ni l'API Anthropic.** Deux garde-fous :
`tests/studies/fake_pipeline/` reproduit le contrat CLI des 11 modules (codes de sortie et
lenteur pilotables par variables d'environnement), atteint par l'indirection
`STUDY_PIPELINE_ROOT` ; et une fixture `autouse` neutralise `launch_study`, si bien qu'une
étude créée pendant un test ne démarre jamais le vrai pipeline.

```bash
uv run pytest                    # tests unitaires (aucun accès réseau)
uv run pytest -m integration     # extractions réelles sur books.toscrape.com et scrapeme.live
```

Les tests marqués `integration` sortent sur le réseau. Ils visent deux bacs à sable
gratuits, rendus par le navigateur et appelés avec `use_agent: false` : aucun crédit
OpenAI ni Apify n'est consommé. Chromium doit être installé.

## Lint

```bash
uv run ruff check src tests
uv run ruff format --check src tests
```

## Structure

```
src/
├── agents/                     # agents IA, appelés depuis la couche service
│   ├── product_extraction/     # URL e-commerce -> fiche produit (intégré tel quel)
│   └── market_study/           # pipeline d'étude : 6 collecteurs + F3->F7 (tel quel)
├── studies/                    # domaine etude de marche (F8.1)
│   ├── router.py               # POST /studies, GET /studies, GET /studies/{id}[/sources[/{source}]]
│   ├── schemas.py              # StudyCreate / StudyResponse / StudyListResponse / StudySource*
│   ├── models.py               # ORM : study, study_source_data, study_analysis, study_report
│   ├── service.py              # creation, verrou anti-doublon, transitions, launch_study (stub F8.2)
│   ├── dependencies.py         # session + chargement/validation du study_id
│   ├── config.py               # settings du domaine (prefixe STUDY_)
│   ├── constants.py            # statuts, sources, agents, declencheurs, codes d'erreur
│   └── exceptions.py
├── products/                   # domaine fiche produit : F1 + extraction
│   ├── router.py               # endpoints (aucune logique métier)
│   ├── schemas.py              # ProductCreate / ProductResponse / ProductExtraction*
│   ├── models.py               # ORM : table `product`
│   ├── service.py              # logique métier + point d'appel de l'agent
│   ├── extraction.py           # SEUL module qui importe l'agent : région, sémaphore,
│   │                           # timeout, mapping d'erreurs, contrôle Chromium
│   ├── dependencies.py         # session + chargement/validation du product_id
│   ├── storage.py              # Supabase Storage (REST + httpx.AsyncClient)
│   ├── config.py               # settings du domaine
│   ├── constants.py            # codes d'erreur, profils de région
│   └── exceptions.py
├── config.py                   # settings globales
├── database.py                 # engine async + session factory + get_db
├── exceptions.py               # exceptions globales
├── models.py                   # bases ORM / Pydantic partagées
└── main.py                     # app FastAPI + lifespan
```

## Codes de réponse

`POST /products`

| Code | Cas |
|---|---|
| `201` | Fiche produit créée (et étude lancée si `STUDY_AUTO_START`) |
| `422` | Champ obligatoire manquant (`name`, `description`, `category`, `region`), ou champ invalide (longueurs, `region` hors liste blanche ou mal formée, `image_url` mal formée) |

`POST /studies`

| Code | Cas |
|---|---|
| `202` | Étude créée, en statut `created` |
| `404` | Aucune fiche produit pour cet identifiant |
| `409` | Une étude est déjà en cours pour ce couple (produit, région) — son `study_id` est renvoyé |
| `422` | Aucune région (ni dans la requête ni sur la fiche), ou région hors liste blanche |

`POST /products/{product_id}/image`

| Code | Cas |
|---|---|
| `200` | Image stockée, fiche mise à jour |
| `404` | Aucune fiche produit pour cet identifiant |
| `413` | Image supérieure à 5 Mo |
| `422` | Fichier qui n'est pas une image JPEG/PNG/WebP (vérification par *magic bytes*, pas seulement par extension) |
| `502` | Échec de l'upload Supabase Storage — la fiche reste inchangée |

`POST /products/extract` — détaillé sous [Extraction automatique](#codes-de-réponse).

`GET /studies` et `GET /studies/{study_id}`

| Code | Cas |
|---|---|
| `200` | Une page de l'historique, ou l'étude demandée |
| `404` | Aucune étude pour cet identifiant |
| `422` | `limit` hors bornes (1 à 100), `offset` négatif, ou `status` / `product_id` mal formé |

`GET /studies/{study_id}/sources` et `/sources/{source}`

| Code | Cas |
|---|---|
| `200` | Le bilan de collecte, ou la ligne du collecteur demandé (`payload` inclus) |
| `404` | Aucune étude pour cet identifiant, **ou** ce collecteur n'a pas encore écrit sa ligne — ce qui n'est pas la même chose qu'une collecte vide |
| `422` | Nom de collecteur inconnu (les six admis sont ceux de `study_source_data`) |

## Hors périmètre de cette itération

**Ordonnancement fin par dépendances** — démarrer F3 dès que ses seules entrées (Reddit,
pages de l'axe 1, avis) sont prêtes, sans attendre les collecteurs de l'axe 2. Le gain
n'existe que si le collecteur le plus lent n'alimente qu'un seul axe ; il se paie d'une
matrice de dépendances à maintenir et de statuts de phase moins lisibles. Piste identifiée,
délibérément non codée.

F1.4 (détection de doublon), **restitution `GET /studies/{id}/report` — rapport et verdict
— et relance (lot F8.3)** (les JSON par source, eux, sont déjà lisibles via
`GET /studies/{id}/sources`), reprise fine d'une étude interrompue,
annulation ou suppression d'étude, file de tâches persistante, multi-worker,
authentification, RLS, frontend, Docker.
