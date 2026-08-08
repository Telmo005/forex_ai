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
$mt5InstallDir = $null
foreach ($p in $mt5Paths) {
    if ($p -and (Test-Path $p)) {
        Write-Ok "Encontrado em $p"
        $mt5Found = $true
        $mt5InstallDir = Split-Path $p -Parent
        break
    }
}
if (-not $mt5Found) {
    Write-Warn2 "Terminal MT5 nao encontrado nos caminhos habituais. Instala-o (site da tua corretora) antes de correr qualquer coisa em --mode mt5."
}

# --------------------------------------------------------------------
# 6. Copiar o EA para a pasta de dados do terminal + compilar (automatico)
#    Ainda assim NAO consegue: iniciar sessao na conta, confirmar modo
#    hedging, ativar "Algo Trading", nem anexar o EA a um grafico (isso
#    e estado de UI do terminal, nao existe forma segura de scriptar).
# --------------------------------------------------------------------
if ($mt5Found) {
    Write-Step "A procurar a(s) pasta(s) de dados do terminal MT5..."
    $terminalRoot = Join-Path $env:APPDATA "MetaQuotes\Terminal"
    $dataFolders = @()
    if (Test-Path $terminalRoot) {
        $dataFolders = Get-ChildItem -Path $terminalRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName "MQL5\Experts") }
    }

    if ($dataFolders.Count -eq 0) {
        Write-Warn2 "Nenhuma pasta de dados de terminal encontrada em '$terminalRoot' - abre o MT5 pelo menos uma vez (para ele se registar) e corre este script de novo para copiar/compilar o EA automaticamente."
    } else {
        if ($dataFolders.Count -gt 1) {
            Write-Warn2 "Mais do que uma instalacao de terminal MT5 encontrada - a copiar o EA para todas ($($dataFolders.Count))."
        }
        $metaEditorPath = Join-Path $mt5InstallDir "MetaEditor64.exe"
        foreach ($folder in $dataFolders) {
            $expertsDest = Join-Path $folder.FullName "MQL5\Experts\ForexAI"
            Write-Step "A copiar o EA para '$expertsDest'..."
            New-Item -ItemType Directory -Path $expertsDest -Force | Out-Null
            foreach ($f in @("ScalpingEA.mq5", "RiskGuard.mqh", "SignalBridge.mqh")) {
                Copy-Item -Path (Join-Path $RepoRoot "mql5\$f") -Destination $expertsDest -Force
            }
            Write-Ok "Ficheiros copiados."

            if (Test-Path $metaEditorPath) {
                Write-Step "A compilar ScalpingEA.mq5 via MetaEditor (linha de comandos)..."
                $eaPath = Join-Path $expertsDest "ScalpingEA.mq5"
                $ex5Path = Join-Path $expertsDest "ScalpingEA.ex5"
                # O codigo de saida do MetaEditor NAO segue a convencao habitual
                # (0=sucesso) - confirmado em teste manual: devolveu 1 mesmo
                # numa compilacao bem sucedida. O sinal fiavel e o .ex5 ter
                # sido criado/atualizado DEPOIS deste comando correr, nunca o
                # exit code nem o ficheiro /log (que nem sempre e gerado a
                # tempo). Start-Process -Wait bloqueia mesmo ate a MetaEditor
                # terminar (ao contrario de "&" isolado, cujo timing nao e
                # garantido para processos GUI como este).
                $beforeTime = if (Test-Path $ex5Path) { (Get-Item $ex5Path).LastWriteTimeUtc } else { $null }
                Start-Process -FilePath $metaEditorPath -ArgumentList "/compile:`"$eaPath`"" -Wait -WindowStyle Hidden | Out-Null
                if ((Test-Path $ex5Path) -and ((Get-Item $ex5Path).LastWriteTimeUtc -ne $beforeTime)) {
                    Write-Ok "Compilado com sucesso: $ex5Path"
                } else {
                    Write-Warn2 "A compilacao pode ter falhado - confirma manualmente no MetaEditor (abre ScalpingEA.mq5 e prime F7)."
                }
            } else {
                Write-Warn2 "MetaEditor64.exe nao encontrado em '$mt5InstallDir' - abre ScalpingEA.mq5 no MetaEditor manualmente e compila com F7."
            }
        }
    }
}

# --------------------------------------------------------------------
# Resumo final
# --------------------------------------------------------------------
Write-Step "Setup concluido. Falta so o que nenhum script consegue fazer por ti (sao interacoes manuais do terminal, por design de seguranca do MT5/corretora):"
Write-Host "  1. Abre o MetaTrader 5, inicia sessao na conta, confirma que o titulo diz 'Hedge'."
Write-Host "  2. No Navigator do MT5, arrasta 'ScalpingEA' (ja copiado e compilado) para um grafico."
Write-Host "  3. Ativa 'Algo Trading' na barra de ferramentas + Tools > Options > Expert Advisors."
Write-Host "  4. Duplo-clique em iniciar_dashboard.bat."
Write-Host ""
