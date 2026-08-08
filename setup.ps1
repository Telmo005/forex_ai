<#
.SYNOPSIS
    Setup de raiz do forex_ai_project numa máquina nova (VPS ou PC) —
    verifica o Python, instala as dependências (incluindo o pacote
    MetaTrader5, que fica comentado em requirements.txt por só ser
    necessário em --mode mt5), garante que output/ existe, e copia
    opcionalmente os ficheiros essenciais de output/ de uma origem
    partilhada — para não teres de copiar tudo à mão.

    O que este script NÃO consegue automatizar (fora do nosso código,
    ver conversa 2026-08): instalar/licenciar o terminal MetaTrader 5
    em si, iniciar sessão na conta, confirmar modo hedging, ativar
    "Algo Trading", ou resolver permissões do lado da corretora. Isso
    continua a exigir os passos manuais do guia.

.PARAMETER OutputSourcePath
    Caminho (pasta local, unidade de rede, ou pasta sincronizada por
    cloud já montada na máquina) de onde copiar strategy_lab.db,
    strategy_lab_journal.md e live_trades.db, se existirem aí. Opcional
    — sem isto, output/ arranca vazio (a busca contínua começa a
    acumular do zero).

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -OutputSourcePath "D:\backup_forex_ai\output"
#>
param(
    [string]$OutputSourcePath = ""
)

$RepoRoot = $PSScriptRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "    OK: $msg" -ForegroundColor Green
}

function Write-Warn2($msg) {
    Write-Host "    AVISO: $msg" -ForegroundColor Yellow
}

# --------------------------------------------------------------------
# 1. Python
# --------------------------------------------------------------------
Write-Step "A verificar Python..."
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "ERRO: Python nao encontrado no PATH." -ForegroundColor Red
    Write-Host "Instala Python 3.10+ em https://www.python.org/downloads/ (marca 'Add python.exe to PATH') e corre este script de novo." -ForegroundColor Red
    exit 1
}
$versionOutput = (& python --version 2>&1) | Out-String
Write-Ok "Encontrado: $($versionOutput.Trim())"
$versionMatch = [regex]::Match($versionOutput, '(\d+)\.(\d+)')
if ($versionMatch.Success) {
    $major = [int]$versionMatch.Groups[1].Value
    $minor = [int]$versionMatch.Groups[2].Value
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        Write-Warn2 "Python $major.$minor detetado - este projeto foi feito para 3.10+. Pode nao funcionar bem."
    }
}

# --------------------------------------------------------------------
# 2. Dependencias Python
# --------------------------------------------------------------------
Write-Step "A instalar dependencias Python (requirements.txt)..."
& python -m pip install -r (Join-Path $RepoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: pip install -r requirements.txt falhou (ver acima)." -ForegroundColor Red
    exit 1
}
Write-Ok "Dependencias instaladas."

Write-Step "A instalar o pacote MetaTrader5 (comentado em requirements.txt, so necessario para --mode mt5)..."
& python -m pip install MetaTrader5
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "Falha a instalar MetaTrader5 - so funciona em Windows de 64-bit. Instala manualmente mais tarde se precisares."
} else {
    Write-Ok "MetaTrader5 instalado."
}

# --------------------------------------------------------------------
# 3. Pasta output/
# --------------------------------------------------------------------
Write-Step "A garantir que output/ existe..."
$OutputDir = Join-Path $RepoRoot "output"
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
    Write-Ok "Criada $OutputDir"
} else {
    Write-Ok "Ja existe."
}

# --------------------------------------------------------------------
# 4. Copiar ficheiros essenciais de output/, se foi dada uma origem
# --------------------------------------------------------------------
if ($OutputSourcePath) {
    Write-Step "A copiar ficheiros essenciais de '$OutputSourcePath'..."
    if (-not (Test-Path $OutputSourcePath)) {
        Write-Warn2 "Caminho '$OutputSourcePath' nao encontrado - a saltar copia, output/ fica vazio."
    } else {
        $essentialFiles = @("strategy_lab.db", "strategy_lab_journal.md", "live_trades.db")
        foreach ($f in $essentialFiles) {
            $src = Join-Path $OutputSourcePath $f
            if (Test-Path $src) {
                Copy-Item -Path $src -Destination (Join-Path $OutputDir $f) -Force
                Write-Ok "Copiado $f"
            } else {
                Write-Warn2 "$f nao encontrado em '$OutputSourcePath' - ignorado (fica sem ele)."
            }
        }
    }
} else {
    Write-Warn2 "Nenhum -OutputSourcePath dado - output/ arranca vazio (a busca continua comeca a acumular do zero)."
}

# --------------------------------------------------------------------
# 5. Confirmar se o terminal MT5 esta instalado (so deteta, nunca instala)
# --------------------------------------------------------------------
Write-Step "A procurar o terminal MetaTrader 5..."
$mt5Paths = @(
    (Join-Path $env:PROGRAMFILES "MetaTrader 5\terminal64.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "MetaTrader 5\terminal.exe")
)
$mt5Found = $false
foreach ($p in $mt5Paths) {
    if ($p -and (Test-Path $p)) {
        Write-Ok "Encontrado em $p"
        $mt5Found = $true
        break
    }
}
if (-not $mt5Found) {
    Write-Warn2 "Terminal MT5 nao encontrado nos caminhos habituais. Instala-o (site da tua corretora) antes de correr qualquer coisa em --mode mt5."
}

# --------------------------------------------------------------------
# Resumo final
# --------------------------------------------------------------------
Write-Step "Setup concluido. Falta so o que nenhum script consegue fazer por ti:"
Write-Host "  1. Abre o MetaTrader 5, inicia sessao na conta, confirma que o titulo diz 'Hedge'."
Write-Host "  2. Compila e anexa mql5/ScalpingEA.mq5 a um grafico (ver guia completo do projeto)."
Write-Host "  3. Ativa 'Algo Trading' na barra de ferramentas + Tools > Options > Expert Advisors."
Write-Host "  4. Duplo-clique em iniciar_dashboard.bat."
Write-Host ""
