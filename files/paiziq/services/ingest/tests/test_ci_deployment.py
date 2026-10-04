"""Exercise CI redeployment shutdown and refusal paths without calling Azure."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/azure/deploy_existing_backend.sh"


def test_make_help_exposes_ci_and_new_demo_targets():
    project = Path(__file__).resolve().parents[3]
    result = subprocess.run(["make", "help"], cwd=project, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for target in ("ci-smoke", "ci-deploy-azure", "procurement-demo", "phase0-demo"):
        assert target in result.stdout


@pytest.mark.parametrize("stuck", [False, True])
def test_ci_deployment_waits_for_all_old_writers_and_retains_configuration(tmp_path, stuck):
    calls = tmp_path / "calls.jsonl"
    fake_az = tmp_path / "az"
    fake_az.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['CI_TEST_CALLS'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "if args[:3] == ['containerapp', 'revision', 'list']: print('old-a\\nold-b')\n"
        "if args[:3] == ['containerapp', 'replica', 'list']:\n"
        "    print('1' if os.environ['CI_TEST_STUCK'] == '1' else '0')\n"
    )
    fake_az.chmod(0o755)
    for name in ("make", "sleep"):
        mock = tmp_path / name
        mock.write_text("#!/bin/sh\nexit 0\n")
        mock.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "CI_TEST_CALLS": str(calls),
        "CI_TEST_STUCK": "1" if stuck else "0",
        "AZ_RESOURCE_GROUP": "test-rg",
        "AZ_APP_NAME": "test-app",
        "AZ_IMAGE": "registry.example.invalid/ingest:abc123",
        "PAIZIQ_ENDPOINT": "https://backend.example.invalid",
        "PAIZIQ_API_KEY": "constructed-" + "test" * 8,
        "GITHUB_SHA": "abcdef012345" + "0" * 28,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    updates = [args for args in recorded if args[:2] == ["containerapp", "update"]]
    assert env["PAIZIQ_API_KEY"] not in result.stdout + result.stderr
    if stuck:
        assert result.returncode == 1
        assert "refusing concurrent SQLite writers" in result.stderr
        assert not updates
        return
    assert result.returncode == 0, result.stderr
    assert len(updates) == 1
    update = updates[0]
    assert update[update.index("--image") + 1] == env["AZ_IMAGE"]
    assert update[update.index("--revision-suffix") + 1] == "ci-abcdef012345-123-2"
    assert "--yaml" not in update and "--set-env-vars" not in update
    assert "--secrets" not in update
    update_index = recorded.index(update)
    for revision in ("old-a", "old-b"):
        deactivate = next(i for i, args in enumerate(recorded)
                          if args[:3] == ["containerapp", "revision", "deactivate"]
                          and revision in args)
        stopped = next(i for i, args in enumerate(recorded)
                       if args[:3] == ["containerapp", "replica", "list"]
                       and revision in args)
        assert deactivate < stopped < update_index


def test_ci_deployment_requires_image_before_touching_azure():
    env = {
        **os.environ,
        "AZ_RESOURCE_GROUP": "test-rg",
        "AZ_APP_NAME": "test-app",
        "AZ_IMAGE": "",
        "PAIZIQ_ENDPOINT": "https://backend.example.invalid",
        "PAIZIQ_API_KEY": "",
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "AZ_IMAGE is required" in result.stderr
