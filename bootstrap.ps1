$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BootstrapOnly = $false
$ValidateOnly = $false
$AppArguments = New-Object System.Collections.Generic.List[string]
foreach ($Argument in $args) {
    if ($Argument -eq "--bootstrap-only") {
        $BootstrapOnly = $true
    } elseif ($Argument -eq "--validate-bootstrap") {
        $ValidateOnly = $true
    } else {
        $AppArguments.Add([string]$Argument)
    }
}

$RequiredFiles = @(
    "bootstrap-config.json",
    "requirements.lock.txt",
    "estrategia_download_edge_any.py",
    "interface_web.py",
    "estrategia_downloader\__init__.py",
    "estrategia_downloader\app.py",
    "estrategia_downloader\alerts.py",
    "estrategia_downloader\browser.py",
    "estrategia_downloader\config.py",
    "estrategia_downloader\diagnostics.py",
    "estrategia_downloader\discovery.py",
    "estrategia_downloader\downloads.py",
    "estrategia_downloader\errors.py",
    "estrategia_downloader\models.py",
    "estrategia_downloader\utils.py",
    "interface\index.html",
    "interface\app.js",
    "interface\styles.css"
)

$AppHome = if ($env:ESTRATEGIA_APP_HOME) {
    [IO.Path]::GetFullPath($env:ESTRATEGIA_APP_HOME)
} else {
    Join-Path $env:LOCALAPPDATA "EstrategiaCursoDownloader"
}
$CacheDir = Join-Path $AppHome "cache"
$LogsDir = Join-Path $AppHome "logs"
$StateDir = Join-Path $AppHome "state"
$RuntimeRoot = Join-Path $AppHome "runtime"
$EnvironmentRoot = Join-Path $AppHome "environment"

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
$LogFile = Join-Path $LogsDir "bootstrap.log"
$SessionLog = Join-Path $LogsDir ("bootstrap-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

function Write-TechnicalLog {
    param([string]$Message)
    $Line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogFile -Value $Line -Encoding UTF8
    Add-Content -LiteralPath $SessionLog -Value $Line -Encoding UTF8
}

function Write-Step {
    param([int]$Number, [string]$Title, [string]$Detail = "")
    Write-Host ""
    Write-Host ("[{0}/5] {1}" -f $Number, $Title) -ForegroundColor Cyan
    if ($Detail) {
        Write-Host ("      {0}" -f $Detail)
    }
    Write-TechnicalLog ("STEP {0}: {1} {2}" -f $Number, $Title, $Detail)
}

function Stop-Friendly {
    param([string]$Title, [string]$Explanation, [string]$Retry = "")
    Write-Host ""
    Write-Host $Title -ForegroundColor Red
    Write-Host ""
    Write-Host $Explanation
    if ($Retry) {
        Write-Host ""
        Write-Host $Retry
    }
    Write-Host ""
    Write-Host "Detalhes tecnicos foram gravados em:"
    Write-Host $LogFile -ForegroundColor Yellow
    Write-TechnicalLog ("FATAL: {0} | {1} | {2}" -f $Title, $Explanation, $Retry)
    try {
        if ($env:ESTRATEGIA_BOOTSTRAP_NO_DIALOG -eq "1") {
            throw "Caixa de dialogo desativada para teste."
        }
        Add-Type -AssemblyName System.Windows.Forms
        $Body = "$Explanation`r`n`r`n$Retry`r`n`r`nDetalhes tecnicos:`r`n$LogFile"
        [Windows.Forms.MessageBox]::Show(
            $Body,
            "Estrategia Curso Downloader",
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    } catch {
        Write-TechnicalLog ("Nao foi possivel abrir a caixa de erro: " + $_.Exception.Message)
    }
    exit 1
}

function Assert-ApplicationFiles {
    $Missing = @()
    foreach ($RelativePath in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $ScriptRoot $RelativePath) -PathType Leaf)) {
            $Missing += $RelativePath
        }
    }
    if ($Missing.Count -gt 0) {
        Write-TechnicalLog ("Arquivos ausentes: " + ($Missing -join ", "))
        Stop-Friendly `
            "Alguns arquivos do aplicativo nao foram encontrados." `
            "Se voce abriu o programa dentro do ZIP, clique em 'Extrair tudo' e execute iniciar.bat novamente na pasta extraida." `
            ("Arquivos ausentes: " + ($Missing -join ", "))
    }
}

function Get-WindowsArchitecture {
    if ($env:ESTRATEGIA_BOOTSTRAP_TEST_ARCHITECTURE -in @("x64", "arm64", "x86")) {
        Write-TechnicalLog ("Arquitetura de teste forcada: " + $env:ESTRATEGIA_BOOTSTRAP_TEST_ARCHITECTURE)
        return $env:ESTRATEGIA_BOOTSTRAP_TEST_ARCHITECTURE
    }
    $Architecture = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        $env:PROCESSOR_ARCHITECTURE
    }
    switch -Regex ($Architecture) {
        "ARM64" { return "arm64" }
        "AMD64" { return "x64" }
        "x86" { return "x86" }
        default { throw "Arquitetura do Windows nao suportada: $Architecture" }
    }
}

function Get-EdgePath {
    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\Edge\Application\msedge.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    return $Candidates | Select-Object -First 1
}

function Test-PythonRuntime {
    param([string]$PythonExe, [string]$ExpectedVersion)
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        return $false
    }
    try {
        $Version = & $PythonExe -c "import platform; print(platform.python_version())" 2>> $SessionLog
        return ($LASTEXITCODE -eq 0 -and $Version.Trim() -eq $ExpectedVersion)
    } catch {
        Write-TechnicalLog ("Falha ao testar Python em '$PythonExe': " + $_.Exception.Message)
        return $false
    }
}

function Find-CompatiblePython {
    param([string]$ExpectedVersion)
    $Candidates = New-Object System.Collections.Generic.List[string]
    $RegistryKeys = @(
        "HKCU:\Software\Python\PythonCore\$($Config.pythonSeries)\InstallPath",
        "HKLM:\Software\Python\PythonCore\$($Config.pythonSeries)\InstallPath",
        "HKLM:\Software\WOW6432Node\Python\PythonCore\$($Config.pythonSeries)\InstallPath"
    )
    foreach ($Key in $RegistryKeys) {
        if (Test-Path -LiteralPath $Key) {
            $Properties = Get-ItemProperty -LiteralPath $Key
            if ($Properties.ExecutablePath) {
                $Candidates.Add([string]$Properties.ExecutablePath)
            } elseif ($Properties.'(default)') {
                $Candidates.Add((Join-Path ([string]$Properties.'(default)') "python.exe"))
            } else {
                $InstallPath = (Get-Item -LiteralPath $Key).GetValue("")
                if ($InstallPath) {
                    $Candidates.Add((Join-Path ([string]$InstallPath) "python.exe"))
                }
            }
        }
    }
    foreach ($CommandName in @("python.exe", "py.exe")) {
        $Command = Get-Command $CommandName -ErrorAction SilentlyContinue
        if ($Command -and $Command.Source -and $CommandName -eq "python.exe") {
            $Candidates.Add([string]$Command.Source)
        }
    }
    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (Test-PythonRuntime $Candidate $ExpectedVersion) {
            return $Candidate
        }
    }
    return $null
}

function Assert-PythonInstaller {
    param($InstallerMetadata, [string]$InstallerPath, [string]$PublisherPattern)
    $ActualHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ExpectedHash = ([string]$InstallerMetadata.sha256).ToLowerInvariant()
    if ($ActualHash -ne $ExpectedHash) {
        throw "SHA-256 inesperado. Esperado=$ExpectedHash Recebido=$ActualHash"
    }
    $Signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
    if ($Signature.Status -ne [Management.Automation.SignatureStatus]::Valid) {
        throw "Assinatura Authenticode invalida: $($Signature.Status)"
    }
    if (-not $Signature.SignerCertificate -or
        $Signature.SignerCertificate.Subject -notmatch $PublisherPattern) {
        throw "Publicador Authenticode inesperado: $($Signature.SignerCertificate.Subject)"
    }
    Write-TechnicalLog ("Instalador verificado: SHA-256=$ActualHash; Assinante=" + $Signature.SignerCertificate.Subject)
}

function Get-VerifiedPythonInstaller {
    param($InstallerMetadata, [string]$PublisherPattern)
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $InstallerPath = Join-Path $CacheDir ([string]$InstallerMetadata.file)
    if (Test-Path -LiteralPath $InstallerPath -PathType Leaf) {
        try {
            Assert-PythonInstaller $InstallerMetadata $InstallerPath $PublisherPattern
            Write-Host "      Instalador em cache verificado."
            return $InstallerPath
        } catch {
            Write-TechnicalLog ("Cache invalido: " + $_.Exception.Message)
            $Quarantine = "$InstallerPath.invalid-$(Get-Date -Format 'yyyyMMddHHmmss')"
            Move-Item -LiteralPath $InstallerPath -Destination $Quarantine
        }
    }

    Write-Host ("      Baixando Python {0}..." -f $Config.pythonVersion)
    Write-TechnicalLog ("Download: " + [string]$InstallerMetadata.url)
    try {
        Invoke-WebRequest -Uri ([string]$InstallerMetadata.url) -OutFile $InstallerPath -UseBasicParsing
    } catch {
        Write-TechnicalLog ("Falha de download: " + $_.Exception.ToString())
        Stop-Friendly `
            "Nao foi possivel baixar o Python do aplicativo." `
            "Verifique sua conexao com a internet e tente novamente." `
            "Se a rede da empresa bloqueia python.org, contate o suporte da rede."
    }
    Write-Host "      Download concluido. Verificando integridade..."
    try {
        Assert-PythonInstaller $InstallerMetadata $InstallerPath $PublisherPattern
    } catch {
        Write-TechnicalLog ("Falha de integridade: " + $_.Exception.ToString())
        Stop-Friendly `
            "A verificacao de seguranca do instalador falhou." `
            "O arquivo baixado nao sera executado." `
            "Tente novamente. Se o erro persistir, verifique antivirus, proxy ou rede corporativa."
    }
    Write-Host "      Integridade e assinatura confirmadas."
    return $InstallerPath
}

function Invoke-PythonInstaller {
    param([string]$InstallerPath, [string]$RuntimeDir, [switch]$Repair)
    $InstallerLog = Join-Path $LogsDir ("python-installer-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    if ($Repair) {
        $InstallerArguments = @("/repair", "/quiet", "/log", ('"{0}"' -f $InstallerLog))
    } else {
        $InstallerArguments = @(
            "/quiet",
            "InstallAllUsers=0",
            ('TargetDir="{0}"' -f $RuntimeDir),
            "Include_exe=1",
            "Include_lib=1",
            "Include_pip=1",
            "Include_tcltk=1",
            "Include_launcher=0",
            "InstallLauncherAllUsers=0",
            "AssociateFiles=0",
            "Shortcuts=0",
            "PrependPath=0",
            "AppendPath=0",
            "Include_doc=0",
            "Include_test=0",
            "/log",
            ('"{0}"' -f $InstallerLog)
        )
    }
    Write-TechnicalLog ("Executando instalador: $InstallerPath " + ($InstallerArguments -join " "))
    $Process = Start-Process -FilePath $InstallerPath -ArgumentList $InstallerArguments -Wait -PassThru
    Write-TechnicalLog ("Codigo do instalador: " + $Process.ExitCode)
    return $Process.ExitCode
}

function Ensure-PythonRuntime {
    param($InstallerMetadata, [string]$RuntimeDir, [string]$PythonExe)
    if (Test-PythonRuntime $PythonExe ([string]$Config.pythonVersion)) {
        Write-Host ("      Python privado {0} pronto." -f $Config.pythonVersion)
        return $PythonExe
    }

    # O instalador oficial nao instala duas copias per-user da mesma versao
    # exata. Quando essa versao ja existe, reutilizamos somente o executavel;
    # os pacotes continuam isolados no ambiente privado do aplicativo.
    $CompatiblePython = if ($env:ESTRATEGIA_BOOTSTRAP_FORCE_PRIVATE -eq "1") {
        $null
    } else {
        Find-CompatiblePython ([string]$Config.pythonVersion)
    }
    if ($CompatiblePython) {
        Write-Host ("      Python {0} compativel encontrado." -f $Config.pythonVersion)
        Write-Host "      Os componentes do aplicativo continuarao em ambiente isolado."
        Write-TechnicalLog ("Reutilizando runtime compativel: $CompatiblePython")
        return $CompatiblePython
    }

    Write-Host "      Python do aplicativo nao encontrado ou precisa de reparo."
    Write-Host "      Uma copia privada sera preparada somente para este aplicativo."
    Write-Host "      O PATH e as outras instalacoes do Windows nao serao alterados."
    $InstallerPath = Get-VerifiedPythonInstaller $InstallerMetadata ([string]$Config.publisherPattern)

    if (Test-Path -LiteralPath $RuntimeDir) {
        Write-Host "      Reparando o Python privado..."
        [void](Invoke-PythonInstaller $InstallerPath $RuntimeDir -Repair)
    }
    if (-not (Test-PythonRuntime $PythonExe ([string]$Config.pythonVersion))) {
        Write-Host "      Instalando o Python privado..."
        $ExitCode = Invoke-PythonInstaller $InstallerPath $RuntimeDir
        if ($ExitCode -ne 0 -and $ExitCode -ne 3010) {
            Stop-Friendly `
                "Nao foi possivel preparar o Python do aplicativo." `
                "A instalacao por usuario terminou com codigo $ExitCode." `
                "Tente novamente. Nenhuma permissao de Administrador e necessaria."
        }
    }
    if (-not (Test-PythonRuntime $PythonExe ([string]$Config.pythonVersion))) {
        Stop-Friendly `
            "O Python privado foi instalado, mas nao respondeu corretamente." `
            "O ambiente pode ter sido bloqueado por antivirus ou politica corporativa." `
            "Tente novamente; o bootstrap tentara reparar a instalacao."
    }
    Write-Host "      Python privado instalado e verificado."
    return $PythonExe
}

function Test-ApplicationEnvironment {
    param([string]$PythonExe)
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        return $false
    }
    try {
        & $PythonExe -c "import requests, selenium; print('ok')" 1>> $SessionLog 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function New-ApplicationEnvironment {
    param([string]$RuntimePython, [string]$EnvironmentDir)
    if (Test-Path -LiteralPath $EnvironmentDir) {
        $Broken = "$EnvironmentDir.broken-$(Get-Date -Format 'yyyyMMddHHmmss')"
        Write-TechnicalLog ("Movendo ambiente quebrado para: $Broken")
        Move-Item -LiteralPath $EnvironmentDir -Destination $Broken
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EnvironmentDir) | Out-Null
    & $RuntimePython -m venv $EnvironmentDir 1>> $SessionLog 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-Friendly `
            "Nao foi possivel criar o ambiente privado do aplicativo." `
            "O Python foi preparado, mas a criacao do ambiente falhou." `
            "Tente novamente; detalhes adicionais estao no log."
    }
}

function Ensure-Dependencies {
    param([string]$RuntimePython, [string]$EnvironmentDir, [string]$RequirementsPath)
    $EnvironmentPython = Join-Path $EnvironmentDir "Scripts\python.exe"
    $RequirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $StatePath = Join-Path $StateDir "environment.json"
    $RecordedHash = ""
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try {
            $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
            $RecordedHash = [string]$State.requirementsSha256
        } catch {
            Write-TechnicalLog ("Estado de dependencias invalido: " + $_.Exception.Message)
        }
    }

    $EnvironmentWorks = Test-ApplicationEnvironment $EnvironmentPython
    if (-not $EnvironmentWorks) {
        Write-Host "      Ambiente ausente ou danificado; recriando..."
        New-ApplicationEnvironment $RuntimePython $EnvironmentDir
        $EnvironmentWorks = $true
        $RecordedHash = ""
    }

    if ($RecordedHash -eq $RequirementsHash -and $EnvironmentWorks) {
        Write-Host "      Componentes ja estao atualizados."
        return $EnvironmentPython
    }

    Write-Host "      Instalando componentes testados (Selenium e Requests)..."
    Write-TechnicalLog ("Instalando requirements SHA-256=$RequirementsHash")
    $PreviousErrorPreference = $ErrorActionPreference
    try {
        # pip pode escrever avisos em stderr mesmo quando funciona. Captura-los
        # no log nao deve transformar um aviso em excecao do PowerShell.
        $ErrorActionPreference = "Continue"
        & $EnvironmentPython -m pip install `
            --disable-pip-version-check `
            --no-input `
            --upgrade `
            -r $RequirementsPath 1>> $SessionLog 2>&1
        $PipExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorPreference
    }
    if ($PipExitCode -ne 0 -or -not (Test-ApplicationEnvironment $EnvironmentPython)) {
        Write-TechnicalLog ("pip terminou com codigo $PipExitCode")
        Stop-Friendly `
            "Nao foi possivel instalar os componentes do aplicativo." `
            "Verifique a conexao com a internet e tente novamente." `
            "Uma rede corporativa pode estar bloqueando o acesso ao indice de pacotes Python."
    }

    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    @{
        pythonVersion = [string]$Config.pythonVersion
        architecture = $Architecture
        requirementsSha256 = $RequirementsHash
        updatedAt = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding UTF8
    Write-Host "      Componentes instalados."
    return $EnvironmentPython
}

try {
    Clear-Host
    Write-Host "Estrategia Curso Downloader" -ForegroundColor Green
    Write-Host "Preparacao automatica e privada para este aplicativo."
    Write-TechnicalLog ("Inicio. ScriptRoot=$ScriptRoot AppHome=$AppHome Args=" + ($args -join " "))

    Write-Step 1 "Verificando os componentes necessarios..."
    Assert-ApplicationFiles
    $ConfigPath = Join-Path $ScriptRoot "bootstrap-config.json"
    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $Architecture = Get-WindowsArchitecture
    $InstallerMetadata = $Config.installers.$Architecture
    if (-not $InstallerMetadata) {
        throw "Nao ha instalador configurado para a arquitetura $Architecture."
    }
    $EdgePath = Get-EdgePath
    if (-not $EdgePath) {
        Stop-Friendly `
            "Microsoft Edge nao foi encontrado." `
            "O aplicativo usa o Microsoft Edge para realizar o login e acessar os cursos." `
            "Instale o Microsoft Edge pelo site oficial e execute iniciar.bat novamente."
    }
    $EdgeVersion = (Get-Item -LiteralPath $EdgePath).VersionInfo.ProductVersion
    Write-Host ("      Microsoft Edge encontrado ({0})." -f $EdgeVersion)
    Write-TechnicalLog ("Edge=$EdgePath Version=$EdgeVersion Architecture=$Architecture")
    if ($ValidateOnly) {
        Write-Host ""
        Write-Host "Bootstrap e arquivos obrigatorios validados." -ForegroundColor Green
        exit 0
    }

    $RuntimeDir = Join-Path $RuntimeRoot ("python-{0}-{1}" -f $Config.pythonVersion, $Architecture)
    $RuntimePython = Join-Path $RuntimeDir "python.exe"
    Write-Step 2 "Preparando o Python interno..."
    $RuntimePython = Ensure-PythonRuntime $InstallerMetadata $RuntimeDir $RuntimePython

    $EnvironmentDir = Join-Path $EnvironmentRoot ("python-{0}-{1}" -f $Config.pythonVersion, $Architecture)
    Write-Step 3 "Preparando o ambiente do aplicativo..."
    if (Test-ApplicationEnvironment (Join-Path $EnvironmentDir "Scripts\python.exe")) {
        Write-Host "      Ambiente privado encontrado."
    } else {
        Write-Host "      O ambiente sera criado ou reparado automaticamente."
    }

    Write-Step 4 "Verificando componentes do aplicativo..."
    $EnvironmentPython = Ensure-Dependencies `
        $RuntimePython `
        $EnvironmentDir `
        (Join-Path $ScriptRoot "requirements.lock.txt")

    if ($BootstrapOnly) {
        Write-Host ""
        Write-Host "Bootstrap concluido com sucesso." -ForegroundColor Green
        Write-TechnicalLog "Bootstrap-only concluido."
        exit 0
    }

    Write-Step 5 "Iniciando o Estrategia Curso Downloader..." "Abrindo a interface no Edge..."
    Write-TechnicalLog ("Python da aplicacao: $EnvironmentPython")
    & $EnvironmentPython (Join-Path $ScriptRoot "estrategia_download_edge_any.py") @AppArguments
    $ApplicationExitCode = $LASTEXITCODE
    Write-TechnicalLog ("Aplicacao encerrada com codigo $ApplicationExitCode")
    exit $ApplicationExitCode
} catch {
    Write-TechnicalLog ("EXCECAO: " + $_.Exception.ToString())
    Stop-Friendly `
        "Nao foi possivel preparar o aplicativo." `
        "O bootstrap encontrou um problema inesperado." `
        "Tente novamente. Se o problema continuar, consulte o log tecnico."
}
