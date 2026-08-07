<#
.SYNOPSIS
    Étude de marché complète : les 6 collecteurs, les 3 agents d'analyse, la
    classification de cycle de vie et le rapport final.

.DESCRIPTION
    Enchaîne le pipeline documenté dans AGENTS.md pour un produit et une région.
    Chaque sortie est écrite dans un répertoire d'étude unique ; les agents
    d'analyse ne reçoivent que les fichiers réellement produits.

    LIVRABLE — l'étude se termine par `rapport_etude.md` et `resume_executif.md`,
    déposés dans le répertoire de la langue. Les JSON intermédiaires restent à
    côté : ce sont eux qui font foi en cas de doute sur une formulation du
    rapport.

    CYCLE DE VIE — l'étape F6 n'est pas conditionnée ici : elle est toujours
    lancée, et c'est elle qui décide de classer ou non selon le verdict de F5.
    Un verdict non positif produit une sortie courte de non-déclenchement, sans
    le moindre appel LLM, que F7 restitue par un encart standard. Le drapeau
    `--forcer` de F6 n'est délibérément PAS exposé ici : il est réservé à
    l'étude et au test, jamais à une exécution de production.

    À lancer DEPUIS LA RACINE DU DÉPÔT : les modules cherchent le fichier .env
    en remontant l'arborescence depuis le répertoire courant.

    LANGUE ET DEVISE — ni l'une ni l'autre n'est demandée. Les deux se déduisent
    du seul code pays, par table déterministe et sans aucun appel LLM :
    `langues_marche.py` sert la langue principale du pays, `devise_marche.py` sa
    monnaie. La langue pilote la dérivation des mots-clés de TOUS les
    collecteurs ; la devise ne concerne qu'AliExpress et le prix envisagé.

    UNE SEULE LANGUE PAR DÉFAUT. Un marché multilingue n'est plus étendu d'office
    à deux études : le segment linguistique écarté n'est pas couvert, et son
    absence est silencieuse par construction. Pour le couvrir, il faut le
    demander : `-Langues nl,fr`. L'étude est alors exécutée INTÉGRALEMENT une
    fois par langue — collecte et analyse comprises, donc à coût et durée
    multipliés.

    LA RÉSERVE À LIRE. Sur certains marchés, la langue maternelle n'est pas celle
    qui est TAPÉE dans un moteur : Inde, Nigéria, Pakistan, Philippines, Maghreb,
    Afrique de l'Est. La table le signale par un avertissement au lancement.
    L'ignorer expose à un corpus vide sans qu'aucun module ne soit en échec.

    De même côté devise : AliExpress écarte les prix libellés dans une autre
    devise que celle demandée, si bien qu'un marché dont la monnaie n'est pas
    servie par la plateforme rend une collecte vide sans être en échec.
    Réimposer USD ou EUR est alors le repli.

    `-LangueRapport` reste indépendante : les rapports d'analyse sont rédigés en
    français quelle que soit la langue de collecte.

    Forçages, du plus fort au plus faible : `-Langues fr,ar` (liste imposée),
    `-Langue es` (langue unique imposée), puis la table. `-Devise USD` prime de
    même sur la table des devises. `-Confirmer` demande validation avant de
    lancer plusieurs études.

    Aucun collecteur en échec n'interrompt l'étude : le manque est signalé et
    les agents d'analyse le traitent comme une entrée absente (dégradation
    documentée dans leurs README respectifs).

.EXAMPLE
    .\etude_marche.ps1 -Nom "JBL Endurance Peak 4 Open Ear" `
        -Description "Écouteurs à conduction d'air, crochets d'oreille, IP54" `
        -Categorie "electronics" -Geo FR
    # Langue détectée : fr. Devise déduite : EUR. Une seule étude.

.EXAMPLE
    .\etude_marche.ps1 -Nom "Ceinture lombaire double traction" `
        -Description "Ceinture de maintien lombaire réglable" `
        -Categorie "sante-bien-etre" -Geo MA -PrixEnvisage 249
    # Langues détectées : fr + ar, devise déduite MAD. DEUX études complètes,
    # dans etudes\…-MA\fr et \ar. Le prix envisagé est lu en MAD.

.EXAMPLE
    .\etude_marche.ps1 -Nom "Ashwagandha Supplement" -Description "…" `
        -Categorie "health" -Geo ES -Langue es
    # Forçage de la langue : aucune détection, une seule étude en espagnol.

.EXAMPLE
    .\etude_marche.ps1 -Nom "Ceinture de sudation" -Description "…" `
        -Categorie "sport" -Geo TN -Devise USD
    # Forçage de la devise, à n'employer qu'APRÈS constat : si une première
    # exécution rend un aliexpress.json vide en signalant des divergences de
    # devise, c'est que la plateforme ne sert pas le TND. Rejouer en USD.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Nom,
    [Parameter(Mandatory = $true)][string]$Description,
    [Parameter(Mandatory = $true)][string]$Categorie,
    [Parameter(Mandatory = $true)][string]$Geo,
    [string]$Devise,
    [string]$Langue,
    [string[]]$Langues,
    [string]$LangueRapport = "fr",
    [string]$Etude,
    [int]$Avis = 5,
    [int]$Annonces = 30,
    [double]$PrixEnvisage,
    [switch]$Confirmer,
    [switch]$Verbeux
)

$ErrorActionPreference = "Stop"

# Les trois collecteurs redirigés émettent de l'UTF-8. Sans cette ligne,
# PowerShell décode leur stdout dans la page de codes ANSI et fabrique du
# mojibake — défaut déjà constaté sur des sorties Reddit.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

$Racine = $PSScriptRoot
$Debut = Get-Date
$script:Resultats = @()

function Etape([string]$Titre) {
    Write-Host ""
    Write-Host "=== $Titre ===" -ForegroundColor Cyan
}

function Ajouter([string[]]$Liste, [string]$Option, [string]$Fichier) {
    <# Ajoute l'option seulement si le fichier existe réellement. #>
    if (Test-Path $Fichier) { return , ($Liste + @($Option, $Fichier)) }
    Write-Warning "$Option ignoré : $(Split-Path $Fichier -Leaf) absent."
    return , $Liste
}

function ResoudreDevise {
    <#
        Détermine la devise d'étude à partir du seul code pays, sauf forçage.

        Résolution déterministe par table (`devise_marche.py`), sans appel LLM :
        la devise d'un pays est un fait administratif, pas une estimation
        d'usage comme la langue de recherche.

        Un pays absent de la table interrompt l'étude. Deviner une devise
        fausserait tout le benchmark de prix aval sans qu'aucun contrôle ne
        puisse le rattraper : les prix seraient comparés entre eux, donc
        cohérents, mais libellés dans une monnaie que le marché n'emploie pas.
    #>
    if ($Devise) {
        Etape "0/11  Devise imposée"
        Write-Host "  $($Devise.Trim().ToUpper()) — aucune résolution lancée."
        return $Devise.Trim().ToUpper()
    }

    Etape "0/11  Devise du marché $Geo (table déterministe)"
    $brut = python (Join-Path $Racine "devise_marche.py") --geo $Geo
    if ($LASTEXITCODE -ne 0) {
        throw ("Devise indéterminable pour « $Geo » (code $LASTEXITCODE). " +
            "Vérifiez le code pays ISO-2, ou imposez-la : -Devise USD.")
    }

    $resolution = ($brut -join "`n") | ConvertFrom-Json
    Write-Host ("  {0} — {1}" -f $resolution.devise, $resolution.nom)
    Write-Host "  Table vérifiée le $($resolution.date_validite), jamais interrogée en ligne." `
        -ForegroundColor DarkGray
    Write-Host ("  Rappel : AliExpress écarte les prix libellés dans une autre devise " +
        "que celle demandée. Collecte vide sur ce collecteur ? réessayez en -Devise USD.") `
        -ForegroundColor DarkGray
    return $resolution.devise
}

function ResoudreLangues {
    <#
        Détermine les langues d'étude, par ordre de priorité :
        -Langues (liste imposée), -Langue (langue unique imposée), puis la
        table déterministe sur le seul code pays.

        Une résolution en échec interrompt l'étude : lancer les six collecteurs
        sur une langue devinée à tort produit un corpus vide sans qu'aucun
        module ne soit techniquement en erreur.
    #>
    if ($Langues) {
        $codes = @($Langues | ForEach-Object { $_.Trim().ToLower() } |
            Where-Object { $_ } | Select-Object -Unique)
        Etape "0/11  Langues imposées"
        Write-Host "  $($codes -join ', ') — aucune résolution lancée."
        return , $codes
    }

    if ($Langue) {
        Etape "0/11  Langue imposée"
        Write-Host "  $($Langue.Trim().ToLower()) — aucune résolution lancée."
        return , @($Langue.Trim().ToLower())
    }

    Etape "0/11  Langue du marché $Geo (table déterministe)"
    $brut = python (Join-Path $Racine "langues_marche.py") --geo $Geo
    if ($LASTEXITCODE -ne 0) {
        throw ("Langue indéterminable pour « $Geo » (code $LASTEXITCODE). " +
            "Vérifiez le code pays ISO-2, ou imposez-la : -Langue es.")
    }

    $resolution = ($brut -join "`n") | ConvertFrom-Json
    foreach ($l in $resolution.langues) {
        Write-Host ("  {0,-3} {1,-16} {2}" -f $l.code, $l.nom, $l.role)
    }
    Write-Host "  Table vérifiée le $($resolution.date_validite), jamais interrogée en ligne." `
        -ForegroundColor DarkGray

    # La réserve n'est pas une note de bas de page : sur ces marchés, la langue
    # maternelle n'est pas celle qui est tapée dans un moteur, et l'étude peut
    # revenir vide sans qu'aucun module ne soit en échec.
    if ($resolution.reserve) {
        Write-Warning "Marché à arbitrer — $($resolution.reserve)"
    }
    return , @($resolution.codes)
}

function ExecuterEtude([string]$LangueEtude, [string]$Repertoire) {
    <#
        Exécute les 11 étapes pour UNE langue et dépose tout dans $Repertoire.
        Le résultat est empilé dans $script:Resultats plutôt que renvoyé : la
        moindre sortie stdout inattendue d'un collecteur polluerait la valeur
        de retour de la fonction.
    #>
    New-Item -ItemType Directory -Force -Path $Repertoire | Out-Null

    # Socle d'entrée commun aux six collecteurs (cf. AGENTS.md §1).
    $socle = @(
        "--nom", $Nom,
        "--description", $Description,
        "--categorie", $Categorie,
        "--geo", $Geo,
        "--langue", $LangueEtude
    )
    if ($Verbeux) { $socle += "--verbose" }

    # --- Collecte ---------------------------------------------------------
    # Trois collecteurs (Tendances, Reddit, AliExpress) n'émettent que sur stdout :
    # leur sortie est redirigée. `-Encoding utf8` produit un BOM, détecté par les
    # agents d'analyse — contrairement à `>` qui produit de l'UTF-16.

    Etape "1/11  Tendances (Google Trends) — langue $LangueEtude"
    $fTendances = Join-Path $Repertoire "tendances.json"
    python (Join-Path $Racine "agent_tendances\main.py") @socle |
        Out-File -FilePath $fTendances -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Tendances en échec (code $LASTEXITCODE) — entrée abandonnée."
        Remove-Item $fTendances -Force -ErrorAction SilentlyContinue
    }

    Etape "2/11  Reddit (exige SEL_ANONYMISATION) — langue $LangueEtude"
    $fReddit = Join-Path $Repertoire "reddit.json"
    python (Join-Path $Racine "agent_reddit\main.py") @socle |
        Out-File -FilePath $fReddit -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Reddit en échec (code $LASTEXITCODE) — entrée abandonnée."
        Remove-Item $fReddit -Force -ErrorAction SilentlyContinue
    }

    Etape "3/11  Recherche web — langue $LangueEtude"
    $fWeb = Join-Path $Repertoire "recherche_web.json"
    python (Join-Path $Racine "agent_recherche_web\main.py") @socle --sortie $fWeb | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Warning "Recherche web en échec (code $LASTEXITCODE)." }

    Etape "4/11  Amazon (code 3 = pays sans site Amazon propre) — langue $LangueEtude"
    $fAmazon = Join-Path $Repertoire "amazon.json"
    python (Join-Path $Racine "agent_amazon\main.py") @socle --avis $Avis --sortie $fAmazon | Out-Host
    if ($LASTEXITCODE -eq 3) {
        Write-Warning "$Geo n'a pas de site Amazon propre — collecteur ignoré, aucun run lancé."
    } elseif ($LASTEXITCODE -ne 0) {
        Write-Warning "Amazon en échec (code $LASTEXITCODE)."
    }

    Etape "5/11  Meta Ads (facturé À L'ANNONCE : -Annonces est le levier de coût) — langue $LangueEtude"
    $fMeta = Join-Path $Repertoire "meta_ads.json"
    python (Join-Path $Racine "agent_meta_ads\main.py") @socle --annonces $Annonces --sortie $fMeta | Out-Host
    if ($LASTEXITCODE -eq 3) {
        Write-Warning "Région $Geo non résolue — collecteur Meta Ads ignoré."
    } elseif ($LASTEXITCODE -ne 0) {
        Write-Warning "Meta Ads en échec (code $LASTEXITCODE)."
    }

    Etape "6/11  AliExpress (API officielle — une exécution = une région) — langue $LangueEtude"
    $fAli = Join-Path $Repertoire "aliexpress.json"
    python (Join-Path $Racine "agent_aliexpress\main.py") @socle --devise $Devise |
        Out-File -FilePath $fAli -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "AliExpress en échec (code $LASTEXITCODE) — entrée abandonnée."
        Remove-Item $fAli -Force -ErrorAction SilentlyContinue
    }

    # --- Analyse ----------------------------------------------------------
    # `--langue-analyse` porte la langue de RÉDACTION du rapport, jamais la
    # langue de collecte : une étude arabophone reste lisible en français.

    Etape "7/11  F3 — insights consommateurs ($LangueEtude)"
    $fInsights = Join-Path $Repertoire "insights.json"
    $argsF3 = @()
    $argsF3 = Ajouter $argsF3 "--reddit" $fReddit
    $argsF3 = Ajouter $argsF3 "--amazon" $fAmazon
    $argsF3 = Ajouter $argsF3 "--recherche-web" $fWeb
    if ($argsF3.Count -eq 0) {
        Write-Warning "Aucune entrée pour F3 — analyse consommateurs sautée."
    } else {
        $argsF3 += @("--langue-analyse", $LangueRapport, "--sortie", $fInsights)
        if ($Verbeux) { $argsF3 += "--verbose" }
        python (Join-Path $Racine "agent_insights_consommateurs\main.py") @argsF3 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warning "F3 en échec (code $LASTEXITCODE)." }
    }

    Etape "8/11  F4 — analyse concurrentielle ($LangueEtude)"
    $fConcurrence = Join-Path $Repertoire "concurrence.json"
    $argsF4 = @()
    $argsF4 = Ajouter $argsF4 "--aliexpress" $fAli
    $argsF4 = Ajouter $argsF4 "--amazon" $fAmazon
    $argsF4 = Ajouter $argsF4 "--meta-ads" $fMeta
    $argsF4 = Ajouter $argsF4 "--recherche-web" $fWeb
    if ($argsF4.Count -eq 0) {
        Write-Warning "Aucune entrée pour F4 — analyse concurrentielle sautée."
    } else {
        if ($PSBoundParameters.ContainsKey('PrixEnvisage')) {
            $argsF4 += @("--prix-envisage", $PrixEnvisage, "--devise-envisagee", $Devise)
        }
        $argsF4 += @("--langue-analyse", $LangueRapport, "--sortie", $fConcurrence)
        if ($Verbeux) { $argsF4 += "--verbose" }
        python (Join-Path $Racine "agent_analyse_concurrentielle\main.py") @argsF4 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warning "F4 en échec (code $LASTEXITCODE)." }
    }

    Etape "9/11  F5 — recommandations stratégiques et verdict ($LangueEtude)"
    $fReco = Join-Path $Repertoire "recommandations.json"
    $argsF5 = @()
    $argsF5 = Ajouter $argsF5 "--insights" $fInsights
    $argsF5 = Ajouter $argsF5 "--concurrence" $fConcurrence
    $argsF5 = Ajouter $argsF5 "--tendances" $fTendances
    if ($argsF5.Count -eq 0) {
        Write-Warning "Aucune entrée pour F5 — aucun verdict productible."
    } else {
        $argsF5 += @("--langue-analyse", $LangueRapport, "--sortie", $fReco)
        if ($Verbeux) { $argsF5 += "--verbose" }
        python (Join-Path $Racine "agent_recommandations_strategiques\main.py") @argsF5 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warning "F5 en échec (code $LASTEXITCODE)." }
    }

    # --- Cycle de vie et restitution --------------------------------------
    # Ces deux étapes exigent la sortie de F5 : sans verdict ni dossier de
    # synthèse, il n'y a ni phase à classer, ni rapport à écrire.

    Etape "10/11  F6 — phase de cycle de vie ($LangueEtude)"
    $fPlc = Join-Path $Repertoire "plc.json"
    if (-not (Test-Path $fReco)) {
        Write-Warning "Aucune sortie F5 — classification de phase sautée."
    } else {
        # F6 décide seul de classer ou non, d'après `declenche_plc`. Un verdict
        # non positif produit une sortie courte, valide, sans appel LLM — c'est
        # un résultat, pas une erreur, et le code de sortie reste 0.
        $argsF6 = @("--recommandations", $fReco)
        $argsF6 = Ajouter $argsF6 "--insights" $fInsights
        $argsF6 = Ajouter $argsF6 "--concurrence" $fConcurrence
        $argsF6 += @("--langue-analyse", $LangueRapport, "--sortie", $fPlc)
        if ($Verbeux) { $argsF6 += "--verbose" }
        python (Join-Path $Racine "agent_plc\main.py") @argsF6 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warning "F6 en échec (code $LASTEXITCODE)." }
    }

    Etape "11/11  F7 — rapport d'étude ($LangueEtude)"
    $fRapport = Join-Path $Repertoire "rapport_etude.md"
    $fResume = Join-Path $Repertoire "resume_executif.md"
    if (-not (Test-Path $fReco)) {
        Write-Warning "Aucune sortie F5 — rapport non productible."
    } else {
        # Une analyse absente ne bloque pas F7 : la section correspondante est
        # construite depuis l'écho du dossier de synthèse et porte sa mention
        # d'étude partielle.
        $argsF7 = @("--recommandations", $fReco)
        $argsF7 = Ajouter $argsF7 "--insights" $fInsights
        $argsF7 = Ajouter $argsF7 "--concurrence" $fConcurrence
        $argsF7 = Ajouter $argsF7 "--plc" $fPlc
        $argsF7 += @(
            "--rapport", $fRapport,
            "--resume", $fResume,
            "--langue-analyse", $LangueRapport,
            "--sortie", (Join-Path $Repertoire "restitution.json")
        )
        if ($Verbeux) { $argsF7 += "--verbose" }
        python (Join-Path $Racine "agent_restitution\main.py") @argsF7 | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Warning "F7 en échec (code $LASTEXITCODE)." }
    }

    # --- Restitution de cette langue --------------------------------------
    Write-Host ""
    Write-Host "Répertoire ($LangueEtude) : $Repertoire"
    Get-ChildItem $Repertoire -File |
        Select-Object Name, @{n = 'Ko'; e = { [math]::Round($_.Length / 1KB) } } |
        Format-Table -AutoSize | Out-Host

    $verdict = $null
    if (Test-Path $fReco) {
        $verdict = (Get-Content $fReco -Raw -Encoding UTF8 | ConvertFrom-Json).verdict_potentiel
        Write-Host ("Verdict ({0}) : {1} — score {2} — declenche_plc={3} — confiance {4}" -f `
                $LangueEtude, $verdict.verdict, $verdict.score_total, $verdict.declenche_plc, $verdict.confiance) `
            -ForegroundColor Yellow
        Write-Host "Rappel : la règle de verdict est une hypothèse de travail ($($verdict.statut_regle))."
    }

    $phase = "non classée"
    if (Test-Path $fPlc) {
        $plc = Get-Content $fPlc -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($plc.classification -and $plc.classification.phase_probable) {
            $phase = "{0} (incertitude {1})" -f $plc.classification.phase_probable, $plc.classification.incertitude
        }
        Write-Host ("Cycle de vie ({0}) : déclenchement {1} — phase {2}" -f `
                $LangueEtude, $plc.declenchement.mode, $phase)
    }

    if (Test-Path $fRapport) {
        Write-Host "Rapport   : $fRapport" -ForegroundColor Yellow
        Write-Host "Résumé    : $fResume" -ForegroundColor Yellow
    }

    $script:Resultats += [pscustomobject]@{
        Langue       = $LangueEtude
        Repertoire   = $Repertoire
        Verdict      = if ($verdict) { $verdict.verdict } else { "absent" }
        Score        = if ($verdict) { $verdict.score_total } else { $null }
        Confiance    = if ($verdict) { $verdict.confiance } else { $null }
        DeclenchePlc = if ($verdict) { $verdict.declenche_plc } else { $false }
        Phase        = $phase
        Rapport      = if (Test-Path $fRapport) { $fRapport } else { $null }
    }
}

# --- Orchestration ---------------------------------------------------------

# Les deux résolutions sont des consultations de table, gratuites et
# instantanées. Elles couvrent exactement le même jeu de 244 pays : un code
# valide pour l'une l'est pour l'autre, et un code inconnu arrête l'étude ici,
# avant le moindre appel facturé.
$Devise = ResoudreDevise

$codes = ResoudreLangues

if (-not $Etude) {
    $ardoise = ($Nom -replace '[^\w]+', '-').Trim('-').ToLower()
    $Etude = Join-Path $Racine ("etudes\{0}-{1}" -f $ardoise, $Geo.ToUpper())
}
New-Item -ItemType Directory -Force -Path $Etude | Out-Null

if ($codes.Count -gt 1) {
    # Les parenthèses autour de la concaténation sont indispensables :
    # l'opérateur -f lie plus fort que +, sans elles seul le dernier fragment
    # serait formaté et les {0} des précédents s'afficheraient tels quels.
    Write-Warning (("{0} langues imposées ({1}) : l'étude COMPLÈTE — collecte ET " +
            "analyse — sera exécutée {0} fois. Coût Apify/Anthropic et durée " +
            "multipliés par {0}. Pour n'en garder qu'une : -Langue <code>.") `
            -f $codes.Count, ($codes -join ', '))

    # -Confirmer offre un point d'arrêt avant de doubler la dépense. Absent par
    # défaut, pour ne pas bloquer une exécution non surveillée.
    if ($Confirmer) {
        $reponse = Read-Host "Lancer les $($codes.Count) études ? [o/N]"
        if ($reponse -notmatch '^(o|oui|y|yes)$') {
            Write-Host "Étude annulée."
            return
        }
    }
}

$rang = 0
foreach ($code in $codes) {
    $rang++
    Write-Host ""
    Write-Host ("########## Étude $Geo — langue $code ({0}/{1}) ##########" -f `
            $rang, $codes.Count) -ForegroundColor Green
    ExecuterEtude $code (Join-Path $Etude $code)
}

# --- Restitution globale ---------------------------------------------------

Etape "Étude terminée"
Write-Host "Répertoire racine : $Etude"
# Colonnes explicites : `Format-Table -AutoSize` sur l'objet complet laisse le
# chemin absorber toute la largeur et supprime silencieusement les colonnes
# suivantes — dont le verdict.
$script:Resultats |
    Format-Table Langue, Verdict, Score, Confiance, DeclenchePlc, Phase -AutoSize |
    Out-Host

# Les chemins sont listés à part : dans le tableau, ils absorberaient toute la
# largeur et feraient disparaître les colonnes de verdict.
foreach ($resultat in $script:Resultats) {
    if ($resultat.Rapport) {
        Write-Host ("Rapport ({0}) : {1}" -f $resultat.Langue, $resultat.Rapport) -ForegroundColor Yellow
    }
}

if ($codes.Count -gt 1) {
    Write-Host ("Les verdicts par langue ne se moyennent pas : chacun décrit un segment " +
        "linguistique distinct du marché $Geo, sur des corpus collectés séparément.") `
        -ForegroundColor DarkGray
}
Write-Host ("Durée totale : {0:n0} min" -f ((Get-Date) - $Debut).TotalMinutes)
