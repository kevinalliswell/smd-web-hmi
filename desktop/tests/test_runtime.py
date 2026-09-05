from pathlib import Path

import pytest
from smd_desktop.runtime import load_environment, require_fixed_runtime
from smd_desktop.single_instance import AlreadyRunning, single_instance


def test_single_instance_blocks_second_owner_and_releases(tmp_path):
    lock = tmp_path / "service.lock"
    with single_instance("SmdHmi.Test", lock):
        with pytest.raises(AlreadyRunning):
            with single_instance("SmdHmi.Test", lock):
                pass
    with single_instance("SmdHmi.Test", lock):
        pass


def test_config_is_absolute_and_loaded_without_interpolation(tmp_path, monkeypatch):
    config = tmp_path / "config/service.env"
    config.parent.mkdir()
    config.write_text(
        'SMD_JWT_SECRET="literal${DO_NOT_EXPAND}"\nSMD_DB_PATH="' + str(tmp_path / "custom/db.sqlite") + '"\n'
    )
    monkeypatch.setenv("SMD_JWT_SECRET", "stale")
    load_environment(tmp_path, tmp_path / "versions/0.3.0")
    import os

    assert os.environ["SMD_JWT_SECRET"] == "literal${DO_NOT_EXPAND}"
    assert Path(os.environ["SMD_MAINTENANCE_FILE"]) == tmp_path / "maintenance.json"


def test_runtime_never_falls_back_to_system_webview(tmp_path):
    with pytest.raises(RuntimeError, match="WebView2"):
        require_fixed_runtime(tmp_path)
