# ============================================================
# Get-MeliToken-Analysis.ps1
# Gera token OAuth para o Google Apps Script e executa
# análise de dados do meli-bi-data
# ============================================================

param(
    [Parameter(Mandatory=$false)]
    [string]$ClientId        = $env:GOOGLE_CLIENT_ID,

    [Parameter(Mandatory=$false)]
    [string]$ClientSecret    = $env:GOOGLE_CLIENT_SECRET,

    [Parameter(Mandatory=$false)]
    [string]$AppsScriptUrl   = "https://script.google.com/a/macros/mercadolivre.com/s/AKfycbwHPGge1qphBGrT27F1af9tT7Zo7VsUSatZcHyxuwSTtf_QdXSFeFp9OCbvDHEEEtC-/exec",

    [Parameter(Mandatory=$false)]
    [string]$MeliBiDataUrl   = $env:MELI_BI_DATA_URL,

    [Parameter(Mandatory=$false)]
    [string]$OutputPath      = ".\analise_reunioes.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── 1. GERAR TOKEN OAUTH (Device Flow) ─────────────────────────────────────

function Get-GoogleOAuthToken {
    param([string]$ClientId, [string]$ClientSecret)

    $scopes = "https://www.googleapis.com/auth/script.external_request " +
              "https://www.googleapis.com/auth/spreadsheets.readonly"

    Write-Host "`n[1/3] Solicitando código de autorização..." -ForegroundColor Cyan

    # Inicia Device Authorization Request
    $deviceResp = Invoke-RestMethod -Method Post `
        -Uri "https://oauth2.googleapis.com/device/code" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body "client_id=$ClientId&scope=$([Uri]::EscapeDataString($scopes))"

    Write-Host "`n  Acesse: " -NoNewline
    Write-Host $deviceResp.verification_url -ForegroundColor Yellow
    Write-Host "  Digite o código: " -NoNewline
    Write-Host $deviceResp.user_code -ForegroundColor Green
    Write-Host "`n  Aguardando autorização..." -ForegroundColor Gray

    # Polling até receber o token
    $interval  = $deviceResp.interval
    $expiresIn = $deviceResp.expires_in
    $elapsed   = 0

    while ($elapsed -lt $expiresIn) {
        Start-Sleep -Seconds $interval
        $elapsed += $interval

        try {
            $tokenResp = Invoke-RestMethod -Method Post `
                -Uri "https://oauth2.googleapis.com/token" `
                -ContentType "application/x-www-form-urlencoded" `
                -Body ("client_id=$ClientId" +
                       "&client_secret=$ClientSecret" +
                       "&device_code=$($deviceResp.device_code)" +
                       "&grant_type=urn:ietf:params:oauth:grant-type:device_code")

            Write-Host "  Token obtido com sucesso!" -ForegroundColor Green
            return $tokenResp.access_token
        }
        catch {
            $errBody = $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($errBody.error -eq "authorization_pending") { continue }
            if ($errBody.error -eq "slow_down") { $interval += 5; continue }
            throw
        }
    }
    throw "Tempo expirado — autorização não concluída."
}

# ─── 2. BUSCAR DADOS DO APPS SCRIPT / MELI-BI-DATA ──────────────────────────

function Get-ReunioesData {
    param([string]$Token, [string]$Url)

    Write-Host "`n[2/3] Buscando dados de reuniões..." -ForegroundColor Cyan

    $headers = @{ Authorization = "Bearer $Token" }
    $resp    = Invoke-RestMethod -Uri $Url -Headers $headers -Method Get

    if (-not $resp) { throw "Resposta vazia do endpoint." }
    return $resp
}

function Get-MeliBiData {
    param([string]$Token, [string]$BaseUrl)

    Write-Host "      Buscando dados do meli-bi-data..." -ForegroundColor Cyan

    $headers = @{
        Authorization  = "Bearer $Token"
        "Content-Type" = "application/json"
        Accept         = "application/json"
    }

    # Ajuste o endpoint e o body de acordo com a API real do meli-bi-data
    $body = @{
        query     = "SELECT * FROM reunioes_touch WHERE data >= CURRENT_DATE - 30"
        format    = "json"
    } | ConvertTo-Json

    $resp = Invoke-RestMethod -Uri "$BaseUrl/query" `
                              -Headers $headers `
                              -Method Post `
                              -Body $body

    return $resp
}

# ─── 3. ANÁLISE DOS DADOS ────────────────────────────────────────────────────

function Invoke-Analise {
    param($Dados)

    Write-Host "`n[3/3] Executando análise..." -ForegroundColor Cyan

    $reunioes = if ($Dados.reunioes) { $Dados.reunioes } else { $Dados }

    $analise = [ordered]@{
        gerado_em        = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        total_reunioes   = $reunioes.Count

        por_categoria    = $reunioes |
            Group-Object -Property categoria |
            Sort-Object Count -Descending |
            ForEach-Object { [ordered]@{ categoria = $_.Name; total = $_.Count } }

        por_frequencia   = $reunioes |
            Group-Object -Property frequencia |
            Sort-Object Count -Descending |
            ForEach-Object { [ordered]@{ frequencia = $_.Name; total = $_.Count } }

        horas_por_semana = ($reunioes |
            Measure-Object -Property duracao_min -Sum).Sum / 60

        reunioes_1on1    = ($reunioes | Where-Object { $_.categoria -match "1:1|1on1" }).Count

        top5_duracao     = $reunioes |
            Sort-Object duracao_min -Descending |
            Select-Object -First 5 |
            ForEach-Object { [ordered]@{ nome = $_.nome; duracao_min = $_.duracao_min } }
    }

    # Exibe resumo no terminal
    Write-Host "`n  ── RESUMO ──────────────────────────────────" -ForegroundColor White
    Write-Host ("  Total de reuniões : {0}"     -f $analise.total_reunioes)
    Write-Host ("  Horas/semana      : {0:F1}" -f $analise.horas_por_semana)
    Write-Host ("  Reuniões 1:1      : {0}"     -f $analise.reunioes_1on1)
    Write-Host "  Por categoria:"
    $analise.por_categoria | ForEach-Object {
        Write-Host ("    {0,-20} {1}" -f $_.categoria, $_.total)
    }
    Write-Host "  ─────────────────────────────────────────────`n"

    return $analise
}

# ─── MAIN ────────────────────────────────────────────────────────────────────

try {
    # Valida parâmetros obrigatórios
    if (-not $ClientId)     { throw "Defina GOOGLE_CLIENT_ID ou passe -ClientId" }
    if (-not $ClientSecret) { throw "Defina GOOGLE_CLIENT_SECRET ou passe -ClientSecret" }

    $token = Get-GoogleOAuthToken -ClientId $ClientId -ClientSecret $ClientSecret

    # Decide a fonte dos dados
    $dados = if ($MeliBiDataUrl) {
        Get-MeliBiData -Token $token -BaseUrl $MeliBiDataUrl
    } else {
        Get-ReunioesData -Token $token -Url $AppsScriptUrl
    }

    $analise = Invoke-Analise -Dados $dados

    # Salva resultado
    $analise | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "  Análise salva em: $OutputPath" -ForegroundColor Green
}
catch {
    Write-Host "`nERRO: $_" -ForegroundColor Red
    exit 1
}
