from types import SimpleNamespace

from vvault.server import capacity_readiness as capacity


def _stats(*, available_gib, total_gib=40, inode_fraction=0.5, readonly=False):
    total_inodes = 1_000_000
    return SimpleNamespace(
        f_bavail=available_gib * capacity.GIB,
        f_frsize=1,
        f_blocks=total_gib * capacity.GIB,
        f_favail=int(total_inodes * inode_fraction),
        f_files=total_inodes,
        f_flag=1 if readonly else 0,
    )


def test_capacity_thresholds_are_separate_from_authentication(monkeypatch):
    for available, expected in [(9, "ready"), (7, "warning"), (4, "deployment_blocked"), (0, "critical")]:
        monkeypatch.setattr(capacity.os, "statvfs", lambda _mount, value=available: _stats(available_gib=value))
        result = capacity.capacity_status(now=0)
        assert result["status"] == expected
        assert result["authentication_regression"] is False


def test_inode_readonly_and_structured_recent_events_are_critical(monkeypatch, tmp_path):
    monkeypatch.setattr(capacity.os, "statvfs", lambda _mount: _stats(available_gib=20, inode_fraction=0.005))
    assert capacity.capacity_status(now=0)["critical"] is True
    monkeypatch.setattr(capacity.os, "statvfs", lambda _mount: _stats(available_gib=20, readonly=True))
    assert capacity.capacity_status(now=0)["critical"] is True
    event = tmp_path / "event.json"
    event.write_text('{"code":"ENOSPC","mount":"/","observed_at":"1970-01-01T00:00:00+00:00"}')
    monkeypatch.setattr(capacity.os, "statvfs", lambda _mount: _stats(available_gib=20))
    assert capacity.capacity_status(event_path=str(event), now=299)["critical"] is True
    assert capacity.capacity_status(event_path=str(event), now=301)["critical"] is False
