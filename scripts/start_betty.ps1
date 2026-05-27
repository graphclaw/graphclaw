# scripts/start_betty.ps1 — Start the full GraphClaw dev stack and launch Betty chat.
#
# Usage:
#   .\scripts\start_betty.ps1
#   .\scripts\start_betty.ps1 -SkipSetup   # skip seed script if already provisioned
#   .\scripts\start_betty.ps1 -SkipReset   # don't reset the graph before seeding
#
# Requirements:
#   - Docker Desktop running
#   - .venv present (run: uv sync)
#   - ANTHROPIC_API_KEY set in environment or .env file

param(
    [switch]$SkipSetup,   # skip running setup_test_user.py (user already provisioned)
    [switch]$ResetGraph   # wipe TaskNode + GoalNode before setup (fresh slate)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DockerDir   = Join-Path $ProjectRoot "docker"
$Venv        = Join-Path $ProjectRoot ".venv\Scripts"
$Python      = Join-Path $Venv "python.exe"
$GraphclawCli = Join-Path $Venv "graphclaw.exe"

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
$env:AWS_ACCESS_KEY_ID       = "graphclaw"
$env:AWS_SECRET_ACCESS_KEY   = $env:MINIO_PASSWORD ?? "graphclaw_dev"
$env:STORAGE_ENDPOINT_URL    = "http://localhost:9000"
$env:STORAGE_BUCKET          = "graphclaw"
$env:STORAGE_REGION          = "us-east-1"
$env:DATABASE_URL             = "postgresql://graphclaw:$($env:DB_PASSWORD ?? 'graphclaw_dev')@localhost:5432/graphclaw"
$env:REDIS_URL                = "redis://localhost:6379"
$env:SECRETS_BACKEND          = "env_file"

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Error "ANTHROPIC_API_KEY is not set. Betty needs it to think."
    exit 1
}

# ---------------------------------------------------------------------------
# Helper: check a service is healthy
# ---------------------------------------------------------------------------
function Wait-Healthy {
    param([string]$ServiceName, [int]$MaxSeconds = 60)
    Write-Host "Waiting for $ServiceName to be healthy..." -NoNewline
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = docker compose -f "$DockerDir\docker-compose.yml" ps --format json 2>$null |
            ConvertFrom-Json |
            Where-Object { $_.Service -eq $ServiceName } |
            Select-Object -ExpandProperty Health -ErrorAction SilentlyContinue
        if ($status -eq "healthy") {
            Write-Host " healthy." -ForegroundColor Green
            return
        }
        Write-Host "." -NoNewline
        Start-Sleep 3
    }
    Write-Error "$ServiceName did not become healthy within ${MaxSeconds}s."
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Verify Docker is running
# ---------------------------------------------------------------------------
Write-Host "`n[1/5] Checking Docker..." -ForegroundColor Cyan
try {
    docker info *>$null
} catch {
    Write-Error "Docker is not running. Start Docker Desktop and try again."
    exit 1
}
Write-Host "     Docker OK." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Start containers (detached)
# ---------------------------------------------------------------------------
Write-Host "`n[2/5] Starting containers..." -ForegroundColor Cyan
Push-Location $DockerDir
docker compose up -d --remove-orphans
Pop-Location

# ---------------------------------------------------------------------------
# 3. Wait for core services to be healthy
# ---------------------------------------------------------------------------
Write-Host "`n[3/5] Waiting for services..." -ForegroundColor Cyan
Wait-Healthy "db"     60
Wait-Healthy "minio"  60
Wait-Healthy "redis"  30

# Give minio-init a moment to create the bucket
Start-Sleep 3

# ---------------------------------------------------------------------------
# 4. Run setup / seed (idempotent)
# ---------------------------------------------------------------------------
if (-not $SkipSetup) {
    Write-Host "`n[4/5] Running setup_test_user.py (idempotent)..." -ForegroundColor Cyan

    if ($ResetGraph) {
        Write-Host "     Resetting graph (TaskNode + GoalNode)..." -ForegroundColor Yellow
        & $GraphclawCli graph reset --yes
    }

    & $Python "$ProjectRoot\scripts\setup_test_user.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "setup_test_user.py failed (exit $LASTEXITCODE)."
        exit 1
    }
} else {
    Write-Host "`n[4/5] Skipping setup (-SkipSetup)." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 5. Resolve user ID and launch Betty
# ---------------------------------------------------------------------------
Write-Host "`n[5/5] Launching Betty..." -ForegroundColor Cyan

# Resolve the user ID from the graph (pick the first UserNode with matching email)
$UserIdJson = & $Python -c @"
import asyncio, os, sys, selectors
sys.path.insert(0, r'$ProjectRoot\src')
os.environ.setdefault('DATABASE_URL', '$($env:DATABASE_URL)')

async def get_user_id():
    from graphclaw.db.age.connection import create_pool
    from graphclaw.db.factory import create_graph_store
    pool = await create_pool(os.environ['DATABASE_URL'])
    store = create_graph_store('age', pool=pool)
    nodes = await store.list_nodes('UserNode')
    await pool.close()
    target_email = os.environ.get('TEST_USER_EMAIL', 'test-user@example.com')
    for n in nodes:
        if n.get('email') == target_email:
            print(n['id'])
            return
    # fallback: print first user
    if nodes:
        print(nodes[0]['id'])

asyncio.run(get_user_id(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
"@

$UserId = $UserIdJson.Trim()
if (-not $UserId) {
    Write-Error "Could not resolve user ID from graph. Run setup_test_user.py first."
    exit 1
}

Write-Host ""
Write-Host "  User ID : $UserId" -ForegroundColor White
Write-Host "  Model   : claude-sonnet-4-6" -ForegroundColor White
Write-Host "  MinIO   : http://localhost:9001  (user: graphclaw)" -ForegroundColor White
Write-Host ""

& $GraphclawCli agent chat --user-id $UserId
