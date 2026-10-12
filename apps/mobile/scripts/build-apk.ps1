<#
.SYNOPSIS
Baut eine installierbare, signierte APK auf diesem Rechner.

.DESCRIPTION
Ohne Expo-Konto und ohne Play Store: `expo prebuild` erzeugt aus der
Expo-Konfiguration ein Android-Projekt, Gradle baut daraus die APK. Das
Verzeichnis android/ ist reines Erzeugnis und steht in .gitignore - jeder Lauf
mit -Clean stellt es aus app.json neu her, damit keine handgepflegten
Aenderungen darin unbemerkt mitwandern.

Die Signierung laeuft ueber die von Gradle vorgesehenen
android.injected.signing.*-Eigenschaften statt ueber eine Bearbeitung von
android/app/build.gradle: diese Datei wird von prebuild ohnehin jedes Mal neu
geschrieben, jede Aenderung darin waere beim naechsten Lauf wieder fort.

.PARAMETER ApiBaseUrl
Adresse, die als "Finanzplaner Cloud" fest in die App gebaut wird. Bleibt sie
leer, zeigt die Serverauswahl nur "Eigener Server" und die Adresse wird beim
ersten Start eingetippt (siehe apps/mobile/src/api/config.ts, HAS_CLOUD_PRESET
- eine Adresse aus dem privaten Netz zaehlt dort bewusst nicht als Cloud).
#>
[CmdletBinding()]
param(
    [string]$ApiBaseUrl = '',
    [string]$SupabaseUrl = '',
    [string]$SupabaseAnonKey = '',

    # Prozessorarchitekturen, fuer die native Bibliotheken uebersetzt werden.
    # Vorgabe ist nur arm64-v8a: jedes Android-Geraet der letzten Jahre laeuft
    # darauf, und jede weitere Architektur bedeutet einen kompletten weiteren
    # C++-Durchlauf. Das ist hier nicht nur Zeit, sondern auch Risiko - die
    # Pfade unter node_modules/.pnpm sind so lang, dass CMake ohnehin schon vor
    # der 250-Zeichen-Grenze fuer Objektdateien warnt. Fuer einen Emulator auf
    # einem Intel-Rechner zusaetzlich "x86_64" angeben.
    [string]$Abi = 'arm64-v8a',

    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$mobileRoot = Split-Path $PSScriptRoot -Parent
$keystore = Join-Path $mobileRoot 'release.keystore'
$keystoreProps = Join-Path $mobileRoot 'keystore.properties'
$alias = 'finanzplaner'

# --------------------------------------------------------------------------
# Werkzeuge
# --------------------------------------------------------------------------

if (-not $env:JAVA_HOME) {
    $jdk = Get-ChildItem "$env:LOCALAPPDATA\Programs\Eclipse Adoptium" -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^jdk-17' } | Select-Object -First 1
    if (-not $jdk) { throw "JAVA_HOME ist nicht gesetzt und kein JDK 17 gefunden." }
    $env:JAVA_HOME = $jdk.FullName
}
if (-not $env:ANDROID_HOME) {
    $sdk = "$env:LOCALAPPDATA\Android\Sdk"
    if (-not (Test-Path $sdk)) { throw "Android SDK nicht gefunden ($sdk)." }
    $env:ANDROID_HOME = $sdk
}
Write-Host "JDK: $env:JAVA_HOME"
Write-Host "SDK: $env:ANDROID_HOME"

# --------------------------------------------------------------------------
# Schluesselspeicher
# --------------------------------------------------------------------------

if (-not (Test-Path $keystore)) {
    Write-Host "Lege Schluesselspeicher an: $keystore"
    $buffer = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    $storePass = ([Convert]::ToBase64String($buffer) -replace '[^a-zA-Z0-9]', '')

    & "$env:JAVA_HOME\bin\keytool.exe" -genkeypair -v `
        -keystore $keystore -alias $alias `
        -keyalg RSA -keysize 4096 -validity 10950 `
        -storepass $storePass -keypass $storePass `
        -dname "CN=Finanzplaner, OU=Privat, O=Privat, C=DE"
    if ($LASTEXITCODE -ne 0) { throw "Anlegen des Schluesselspeichers fehlgeschlagen" }

    # Getrennt ablegen, damit der Speicher selbst keine Klartextpasswoerter
    # traegt. Beide Dateien stehen in .gitignore.
    @(
        "# Zugang zu release.keystore. NICHT ins Repository, aber unbedingt sichern:",
        "# ohne diese Datei laesst sich eine bereits installierte App nicht mehr",
        "# aktualisieren - Android verweigert jede Aktualisierung, die mit einem",
        "# anderen Schluessel signiert ist. Dann hilft nur Deinstallieren, und die",
        "# lokalen App-Daten sind weg.",
        "storeFile=release.keystore",
        "keyAlias=$alias",
        "storePassword=$storePass",
        "keyPassword=$storePass"
    ) | Set-Content -Path $keystoreProps -Encoding utf8
    Write-Host "Zugangsdaten: $keystoreProps (sichern!)"
}

$props = @{}
foreach ($line in Get-Content $keystoreProps) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $props[$k.Trim()] = $v.Trim()
}

# --------------------------------------------------------------------------
# Android-Projekt erzeugen
# --------------------------------------------------------------------------

$env:EXPO_PUBLIC_API_BASE_URL = $ApiBaseUrl
$env:EXPO_PUBLIC_SUPABASE_URL = $SupabaseUrl
$env:EXPO_PUBLIC_SUPABASE_ANON_KEY = $SupabaseAnonKey

# Ohne -ApiBaseUrl soll wirklich KEINE Adresse im Erzeugnis stehen.
#
# Zwei Fallen liegen hier dicht beieinander: PowerShell LOESCHT eine
# Umgebungsvariable, der man '' zuweist - und dotenv fuellt eine geloeschte
# Variable anschliessend aus der Root-.env nach (es ueberschreibt nur nichts,
# was schon gesetzt ist). Ohne Angabe kaeme so die Entwicklungsadresse dieses
# Rechners in eine APK, die zum Weitergeben gedacht ist. Der Schalter haelt
# metro.config.js davon ab, die Root-.env ueberhaupt zu lesen; es gilt dann
# der Fallback aus src/api/config.ts, und HAS_CLOUD_PRESET bleibt falsch.
if ([string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
    $env:FINANZPLANER_IGNORE_ROOT_ENV = '1'
    Write-Host "Ohne Voreinstellung: die App fragt beim ersten Start nach der Serveradresse."
} else {
    $env:FINANZPLANER_IGNORE_ROOT_ENV = '0'
    Write-Host "Voreinstellung: $ApiBaseUrl"
}
# Schaltet in metro.config.js das Wurzelverzeichnis um - Begruendung dort.
$env:FINANZPLANER_EMBED_BUILD = '1'

# Kurzer Ablageort fuer die C++-Zwischenstaende, siehe
# short-cxx-paths.init.gradle. Bewusst direkt unter dem Laufwerksbuchstaben:
# jedes Zeichen zaehlt gegen die 250-Zeichen-Grenze von CMake.
if (-not $env:FINANZPLANER_CXX_DIR) { $env:FINANZPLANER_CXX_DIR = 'C:/fp-cxx' }
if (-not (Test-Path $env:FINANZPLANER_CXX_DIR)) {
    $null = New-Item -ItemType Directory -Path $env:FINANZPLANER_CXX_DIR -Force
}

Push-Location $mobileRoot
try {
    $androidDir = Join-Path $mobileRoot 'android'
    if ($Clean -and (Test-Path $androidDir)) {
        Write-Host "Entferne vorhandenes android/"
        Remove-Item $androidDir -Recurse -Force
    }

    if (-not (Test-Path $androidDir)) {
        Write-Host "== expo prebuild =="
        & npx --yes expo prebuild --platform android --no-install
        if ($LASTEXITCODE -ne 0) { throw "expo prebuild fehlgeschlagen" }
    }

    # Den JavaScript-Bundle immer neu erzeugen.
    #
    # Gradle haelt :app:createBundleReleaseJsAndAssets fuer aktuell, solange
    # sich die Quelldateien nicht geaendert haben - die EXPO_PUBLIC_-Variablen
    # zaehlen dabei NICHT als Eingabe. Ein Lauf mit anderer Serveradresse
    # lieferte deshalb stillschweigend den Bundle des vorherigen Laufs, und die
    # APK sah fertig aus, enthielt aber den alten Stand. Das Loeschen der
    # Ausgabe kostet gut zwanzig Sekunden und nimmt genau diese Falle weg.
    $bundleDir = Join-Path $androidDir 'app\build\generated\assets\createBundleReleaseJsAndAssets'
    if (Test-Path $bundleDir) {
        Write-Host "Erzwinge neuen JS-Bundle (entferne $bundleDir)"
        Remove-Item $bundleDir -Recurse -Force
    }

    Write-Host "== Gradle: assembleRelease ($Abi) =="
    Push-Location $androidDir
    try {
        & .\gradlew.bat assembleRelease --no-daemon `
            --init-script (Join-Path $PSScriptRoot 'short-cxx-paths.init.gradle') `
            "-PreactNativeArchitectures=$Abi" `
            "-Pandroid.injected.signing.store.file=$keystore" `
            "-Pandroid.injected.signing.store.password=$($props['storePassword'])" `
            "-Pandroid.injected.signing.key.alias=$($props['keyAlias'])" `
            "-Pandroid.injected.signing.key.password=$($props['keyPassword'])"
        if ($LASTEXITCODE -ne 0) { throw "Gradle-Build fehlgeschlagen" }
    } finally {
        Pop-Location
    }

    $apk = Join-Path $androidDir 'app\build\outputs\apk\release\app-release.apk'
    if (-not (Test-Path $apk)) { throw "APK nicht gefunden: $apk" }

    $outDir = Join-Path $mobileRoot 'build'
    if (-not (Test-Path $outDir)) { $null = New-Item -ItemType Directory $outDir }
    $version = (Get-Content (Join-Path $mobileRoot 'app.json') -Raw | ConvertFrom-Json).expo.version
    $target = Join-Path $outDir "finanzplaner-$version.apk"
    Copy-Item $apk $target -Force

    Write-Host ""
    Write-Host ("Fertig: {0} ({1:N1} MB)" -f $target, ((Get-Item $target).Length / 1MB))
} finally {
    Pop-Location
}
