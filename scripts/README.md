# Scripts — reuniao-touch

## Get-MeliToken-Analysis.ps1

Gera um token OAuth do Google e executa análise de dados de reuniões (via Google Apps Script ou meli-bi-data).

### Pré-requisitos

1. **Credenciais OAuth** — crie um projeto no [Google Cloud Console](https://console.cloud.google.com/):
   - APIs & Services → Credentials → Create Credentials → **OAuth 2.0 Client ID**
   - Application type: **TV and Limited Input devices** (habilita o Device Flow)
   - Copie o `Client ID` e o `Client Secret`

2. **PowerShell 5.1+** ou **PowerShell 7** (Windows/Linux/Mac)

---

### Uso rápido

```powershell
# Opção A — via variáveis de ambiente (recomendado)
$env:GOOGLE_CLIENT_ID     = "SEU_CLIENT_ID.apps.googleusercontent.com"
$env:GOOGLE_CLIENT_SECRET = "SEU_CLIENT_SECRET"

.\scripts\Get-MeliToken-Analysis.ps1

# Opção B — passando parâmetros direto
.\scripts\Get-MeliToken-Analysis.ps1 `
    -ClientId     "SEU_CLIENT_ID" `
    -ClientSecret "SEU_CLIENT_SECRET"

# Opção C — com meli-bi-data como fonte de dados
$env:MELI_BI_DATA_URL = "https://api.meli-bi-data.internal/v1"
.\scripts\Get-MeliToken-Analysis.ps1
```

### Fluxo de autorização

```
[1/3] Solicitando código...
  Acesse: https://www.google.com/device
  Digite o código: ABCD-EFGH
  Aguardando autorização...

[2/3] Buscando dados...

[3/3] Executando análise...
  ── RESUMO ─────────────────
  Total de reuniões : 24
  Horas/semana      : 8.5
  Reuniões 1:1      : 5
  Por categoria:
    Daily             8
    Weekly            6
    ...
  Análise salva em: .\analise_reunioes.json
```

### Parâmetros

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `-ClientId` | Google OAuth Client ID | `$env:GOOGLE_CLIENT_ID` |
| `-ClientSecret` | Google OAuth Client Secret | `$env:GOOGLE_CLIENT_SECRET` |
| `-AppsScriptUrl` | URL do Web App publicado | URL do projeto |
| `-MeliBiDataUrl` | URL base do meli-bi-data (opcional) | `$env:MELI_BI_DATA_URL` |
| `-OutputPath` | Caminho do JSON de saída | `.\analise_reunioes.json` |
