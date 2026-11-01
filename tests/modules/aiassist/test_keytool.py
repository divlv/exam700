"""
The command line that stores the Azure OpenAI key encrypted.

The tool handles the real key, so the tests check above all what it never does:
print the key or the decryption key, or overwrite a file it did not create.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from api.modules.aiassist import keytool
from api.modules.aiassist.internal import _client, _secret

KEY = "azure-key-9876543210fedcba-NOT-REAL"


def _run(*argv: str, environ: dict[str, str] | None = None, typed: str | None = None):
    out, err = io.StringIO(), io.StringIO()
    prompts: list[str] = []

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return typed if typed is not None else ""

    code = keytool.main(list(argv), environ=environ or {}, read_secret=read_secret, out=out, err=err)
    return code, out.getvalue(), err.getvalue(), prompts


def _generate(tmp_path: Path, **kwargs):
    return _run("generate", "--out-dir", str(tmp_path), "--from-env", "SOURCE", environ={"SOURCE": KEY}, **kwargs)


def test_generate_writes_a_pair_that_decrypts_to_the_key(tmp_path):
    code, out, err, _ = _generate(tmp_path)

    assert code == 0, err
    token = (tmp_path / keytool.ENCRYPTED_FILE).read_text()
    decryption_key = (tmp_path / keytool.DECRYPTION_KEY_FILE).read_text()
    assert _secret.decrypt(token, decryption_key) == KEY
    assert len(decryption_key) == 44 and not decryption_key.endswith("\n")


def test_generate_never_prints_the_key_or_the_decryption_key(tmp_path):
    code, out, err, _ = _generate(tmp_path)
    token = (tmp_path / keytool.ENCRYPTED_FILE).read_text()
    decryption_key = (tmp_path / keytool.DECRYPTION_KEY_FILE).read_text()

    assert code == 0
    for secret in (KEY, decryption_key):
        assert secret not in out and secret not in err
    # The token is meant to be pasted into the manifest, and is useless without the key.
    assert token in out
    assert str(tmp_path / keytool.DECRYPTION_KEY_FILE) in out


def test_the_pair_is_accepted_by_the_application_settings(tmp_path):
    _generate(tmp_path)
    env = {
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/openai/v1",
        "AZURE_OPENAI_DEPLOYMENT": "dep",
        "AZURE_OPENAI_API_KEY_ENCRYPTED": (tmp_path / keytool.ENCRYPTED_FILE).read_text(),
        # Exactly as a Kubernetes Secret mounted from the file would deliver it.
        "AZURE_OPENAI_API_KEY_DECRYPTION_KEY": (tmp_path / keytool.DECRYPTION_KEY_FILE).read_text(),
    }

    assert _client.load_config(env).api_key == KEY


def test_generate_asks_for_the_key_with_a_hidden_prompt_when_no_variable_is_given(tmp_path):
    code, out, err, prompts = _run("generate", "--out-dir", str(tmp_path), typed=f"  {KEY}  ")

    assert code == 0, err
    assert len(prompts) == 1
    token = (tmp_path / keytool.ENCRYPTED_FILE).read_text()
    key = (tmp_path / keytool.DECRYPTION_KEY_FILE).read_text()
    assert _secret.decrypt(token, key) == KEY  # the typed spaces are not part of it


def test_generate_creates_the_folder(tmp_path):
    target = tmp_path / "does" / "not" / "exist"

    code, _, err, _ = _run("generate", "--out-dir", str(target), "--from-env", "SOURCE", environ={"SOURCE": KEY})

    assert code == 0, err
    assert (target / keytool.ENCRYPTED_FILE).is_file()


def test_generate_never_overwrites_an_existing_pair(tmp_path):
    _generate(tmp_path)
    before = {
        name: (tmp_path / name).read_text() for name in (keytool.ENCRYPTED_FILE, keytool.DECRYPTION_KEY_FILE)
    }

    code, out, err, _ = _run(
        "generate", "--out-dir", str(tmp_path), "--from-env", "SOURCE", environ={"SOURCE": "another-key-0000"}
    )

    assert code == 1
    assert "перезаписывать не буду" in err
    for name, content in before.items():
        assert (tmp_path / name).read_text() == content


def test_a_lone_leftover_file_stops_the_run_before_anything_is_written(tmp_path):
    (tmp_path / keytool.DECRYPTION_KEY_FILE).write_text("leftover")

    code, _, err, _ = _generate(tmp_path)

    assert code == 1
    assert not (tmp_path / keytool.ENCRYPTED_FILE).exists()
    assert (tmp_path / keytool.DECRYPTION_KEY_FILE).read_text() == "leftover"


@pytest.mark.parametrize("environ", [{}, {"SOURCE": ""}, {"SOURCE": "   "}])
def test_a_missing_or_blank_variable_is_a_clear_error(tmp_path, environ):
    code, _, err, _ = _run("generate", "--out-dir", str(tmp_path), "--from-env", "SOURCE", environ=environ)

    assert code == 1
    assert "SOURCE" in err
    assert list(tmp_path.iterdir()) == []


def test_an_empty_prompt_is_an_error_and_writes_nothing(tmp_path):
    code, _, err, _ = _run("generate", "--out-dir", str(tmp_path), typed="   ")

    assert code == 1
    assert "не введён" in err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows ignores POSIX permission bits")
def test_the_files_are_private_to_the_owner(tmp_path):
    _generate(tmp_path)

    for name in (keytool.ENCRYPTED_FILE, keytool.DECRYPTION_KEY_FILE):
        assert (tmp_path / name).stat().st_mode & 0o077 == 0


# --- check ------------------------------------------------------------------


def test_check_confirms_a_matching_pair_without_showing_the_key(tmp_path):
    _generate(tmp_path)

    code, out, err, _ = _run(
        "check",
        "--encrypted-file", str(tmp_path / keytool.ENCRYPTED_FILE),
        "--key-file", str(tmp_path / keytool.DECRYPTION_KEY_FILE),
    )  # fmt: skip

    assert code == 0, err
    assert f"{len(KEY)} символов" in out
    assert KEY not in out


def test_check_rejects_a_pair_that_does_not_fit(tmp_path):
    _generate(tmp_path)
    other = tmp_path / "other.key"
    other.write_text(_secret.generate_key())

    code, out, err, _ = _run(
        "check",
        "--encrypted-file", str(tmp_path / keytool.ENCRYPTED_FILE),
        "--key-file", str(other),
    )  # fmt: skip

    assert code == 1
    assert "ОШИБКА" in err and "OK" not in out
    assert other.read_text() not in err


def test_check_reports_a_missing_file(tmp_path):
    code, _, err, _ = _run(
        "check",
        "--encrypted-file", str(tmp_path / "nope.encrypted"),
        "--key-file", str(tmp_path / "nope.key"),
    )  # fmt: skip

    assert code == 1
    assert "nope.encrypted" in err


# --- the real entry point ---------------------------------------------------


def test_the_module_runs_from_the_command_line(tmp_path):
    """``python -m api.modules.aiassist.keytool`` from the repository root, as the guide says."""
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "SOURCE_KEY": KEY, "PYTHONIOENCODING": "utf-8"}

    result = subprocess.run(
        [sys.executable, "-m", "api.modules.aiassist.keytool", "generate", "--out-dir", str(tmp_path),
         "--from-env", "SOURCE_KEY"],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    token = (tmp_path / keytool.ENCRYPTED_FILE).read_text()
    key = (tmp_path / keytool.DECRYPTION_KEY_FILE).read_text()
    assert _secret.decrypt(token, key) == KEY
    assert KEY not in result.stdout + result.stderr and key not in result.stdout + result.stderr
