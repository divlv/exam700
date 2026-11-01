"""
Command line for storing the Azure OpenAI key encrypted.

Run from the repository root::

    python -m api.modules.aiassist.keytool generate --out-dir C:\\tmp\\az700-keys
    python -m api.modules.aiassist.keytool check --encrypted-file ... --key-file ...

``generate`` reads the key without echoing it (or from an environment variable),
makes a new decryption key and writes two files: the encrypted key and the
decryption key. The encrypted key goes into one Kubernetes Secret and the
decryption key into another, so that neither a leaked manifest nor a pasted
``get secret`` holds both. Neither the key nor the decryption key is ever printed.

``check`` decrypts a pair of files and says whether they fit, without showing
what is inside.

The messages are for the person running the tool and are in Russian, like the
rest of the application's interface.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from api.modules.aiassist.internal import _secret

#: Names of the two files ``generate`` writes. ``az700-ai-key.*`` is in .gitignore.
ENCRYPTED_FILE = "az700-ai-key.encrypted"
DECRYPTION_KEY_FILE = "az700-ai-key.decryption-key"


class _ToolError(Exception):
    """Something the operator can fix; the message is shown as it is."""


def _write_new_file(path: Path, text: str) -> None:
    """
    Create ``path`` with ``text`` and owner-only permissions, never replacing a file.

    The mode is honoured on Linux and macOS; Windows ignores it, so the files
    there are as private as the folder they are written to.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise _ToolError(f"Файл уже существует, перезаписывать не буду: {path}") from None
    with os.fdopen(fd, "w", encoding="ascii", newline="") as handle:
        handle.write(text)


def _read_api_key(
    from_env: str | None,
    environ: Mapping[str, str],
    read_secret: Callable[[str], str],
) -> str:
    if from_env:
        value = environ.get(from_env, "").strip()
        if not value:
            raise _ToolError(f"Переменная окружения {from_env} не задана или пуста.")
        return value
    value = read_secret("Ключ Azure OpenAI (ввод скрыт): ").strip()
    if not value:
        raise _ToolError("Ключ не введён.")
    return value


def _generate(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    read_secret: Callable[[str], str],
    out: TextIO,
) -> None:
    out_dir = Path(args.out_dir)
    encrypted_path = out_dir / ENCRYPTED_FILE
    key_path = out_dir / DECRYPTION_KEY_FILE
    for path in (encrypted_path, key_path):
        if path.exists():
            raise _ToolError(f"Файл уже существует, перезаписывать не буду: {path}")

    api_key = _read_api_key(args.from_env, environ, read_secret)
    decryption_key = _secret.generate_key()
    token = _secret.encrypt(api_key, decryption_key)
    # Prove that the pair works before anything is written: a pair that does not
    # decrypt would be found out only after it was deployed.
    if _secret.decrypt(token, decryption_key) != api_key:
        raise _ToolError("Внутренняя ошибка: проверка расшифровки не сошлась. Ничего не записано.")

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_new_file(encrypted_path, token)
    try:
        _write_new_file(key_path, decryption_key)
    except _ToolError:
        encrypted_path.unlink(missing_ok=True)
        raise

    print("Готово. Файлы (не коммитьте их; ключ расшифровки удалите после переноса на сервер):", file=out)
    print(f"  зашифрованный ключ : {encrypted_path}", file=out)
    print(f"  ключ расшифровки   : {key_path}", file=out)
    print("", file=out)
    print("Зашифрованный ключ для az700-secret.yaml (поле AZURE_OPENAI_API_KEY_ENCRYPTED):", file=out)
    print(token, file=out)
    print("", file=out)
    print(
        "Проверить пару: python -m api.modules.aiassist.keytool check "
        f'--encrypted-file "{encrypted_path}" --key-file "{key_path}"',
        file=out,
    )
    print("Дальше: docs/deploy-k3s.md, раздел про кнопку AI (Secret az700-ai-key).", file=out)


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise _ToolError(f"Не удалось прочитать файл {path}: {error.strerror or error.__class__.__name__}") from None


def _check(args: argparse.Namespace, out: TextIO) -> None:
    token = _read_text(args.encrypted_file)
    key = _read_text(args.key_file)
    try:
        api_key = _secret.decrypt(token, key)
    except _secret.SecretError as error:
        raise _ToolError(str(error)) from None
    print(f"OK: ключ расшифровывается, {len(api_key)} символов.", file=out)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    read_secret: Callable[[str], str] = getpass.getpass,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """
    Run the tool and return the exit code (0 on success, 1 on a problem the operator can fix).

    Args:
        argv: Arguments; ``sys.argv[1:]`` by default.
        environ: The environment ``--from-env`` reads from; ``os.environ`` by default.
        read_secret: Hidden prompt for the key; tests pass a stub.
        out: Where results go; ``sys.stdout`` by default.
        err: Where problems go; ``sys.stderr`` by default.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    environ = os.environ if environ is None else environ

    parser = argparse.ArgumentParser(
        prog="python -m api.modules.aiassist.keytool",
        description="Хранение ключа Azure OpenAI в зашифрованном виде.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="зашифровать ключ и создать ключ расшифровки")
    generate.add_argument("--out-dir", required=True, help="папка для двух файлов (создаётся, если её нет)")
    generate.add_argument(
        "--from-env",
        metavar="NAME",
        help="взять ключ из переменной окружения NAME вместо скрытого ввода",
    )

    check = commands.add_parser("check", help="проверить, что файлы подходят друг к другу")
    check.add_argument("--encrypted-file", required=True)
    check.add_argument("--key-file", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            _generate(args, environ, read_secret, out)
        else:
            _check(args, out)
    except (_ToolError, _secret.SecretError) as error:
        print(f"ОШИБКА: {error}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
