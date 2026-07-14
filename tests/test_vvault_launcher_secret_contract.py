from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "open-vvault-standalone.sh"


def test_launcher_loads_runtime_contract_from_keychain_without_local_fallback():
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "security find-generic-password" in source
    assert "org.thewreck.vvault.body-database-url" in source
    assert "org.thewreck.vvault.object-storage-url" in source
    assert "org.thewreck.vvault.object-storage-service-key" in source
    assert 'export VVAULT_BODY_DATABASE_URL' in source
    assert 'export VVAULT_OBJECT_STORAGE_URL' in source
    assert 'export VVAULT_OBJECT_STORAGE_SERVICE_KEY' in source
    assert "127.0.0.1:5432" not in source


def test_launcher_fails_closed_when_runtime_contract_is_missing():
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'if [ -z "${VVAULT_BODY_DATABASE_URL}" ]' in source
    assert 'if [ -z "${VVAULT_OBJECT_STORAGE_URL}" ] || [ -z "${VVAULT_OBJECT_STORAGE_SERVICE_KEY}" ]' in source
    assert "exit 1" in source


def test_launcher_routes_database_through_stable_ssh_tunnel():
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'DATABASE_TUNNEL_PORT="${VVAULT_DATABASE_TUNNEL_PORT:-25432}"' in source
    assert 'DATABASE_SSH_HOST="${VVAULT_DATABASE_SSH_HOST:-165.245.136.194}"' in source
    assert 'DATABASE_SSH_KEY="${VVAULT_DATABASE_SSH_KEY:-${HOME}/.ssh/digitalocean_vvault}"' in source
    assert "-o ExitOnForwardFailure=yes" in source
    assert 'DATABASE_REMOTE_HOST}:${DATABASE_REMOTE_PORT}' in source
    assert 'parsed._replace(netloc=netloc)' in source


def test_launcher_allows_database_backed_health_checks_to_complete():
    source = LAUNCHER.read_text(encoding="utf-8")

    assert 'VVAULT_HEALTH_REQUEST_TIMEOUT_SECONDS:-10' in source
    assert 'curl -fsS --max-time "${request_timeout}"' in source
