"""Verify NSIS actually decodes installer messages using the production arguments.

Usage: python check_nsis_encoding.py --makensis makensis.exe -- <compiler arguments>
The final argument must be the production .nsi script. The build must reuse the
same argument list for its subsequent compilation; /PPO does not build or run an
installer. NSIS /OUTPUTCHARSET controls stdout, independently of input encoding.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

MESSAGE = re.compile(r'^\s*MessageBox\s+\S+\s+"((?:\$\\.|[^"])*)"', re.MULTILINE)
SYMBOL = re.compile(r"\$\{([^}]+)\}")


def messages(source: str) -> list[str]:
    # NSIS continuations join physical lines before interpreting instructions.
    return MESSAGE.findall(re.sub(r"\\\r?\n\s*", "", source))


def check_encoding(makensis: str, arguments: list[str]) -> int:
    if not arguments or Path(arguments[-1]).suffix.lower() != ".nsi":
        raise ValueError("The final compiler argument must be the production .nsi script")
    source = Path(arguments[-1]).read_text(encoding="utf-8-sig")
    expected = [message for message in messages(source) if not message.isascii()]
    if not expected:
        raise ValueError("No non-ASCII MessageBox text found; the encoding gate would check nothing")
    defines = {}
    for argument in arguments:
        if argument.startswith(("/D", "-D")):
            name, _, value = argument[2:].partition("=")
            defines[name] = value
    expected = [SYMBOL.sub(lambda match: defines.get(match[1], match[0]), message) for message in expected]
    command = [makensis, *arguments[:-1], "/PPO", "/OUTPUTCHARSET", "UTF8", arguments[-1]]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("makensis preprocessing timed out after 120 seconds") from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="backslashreplace").strip()[-1000:]
        raise ValueError(f"makensis preprocessing failed ({completed.returncode}): {detail}")
    try:
        output = completed.stdout.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("makensis output is not valid UTF-8 despite /OUTPUTCHARSET UTF8") from exc
    actual = set(messages(output))
    for index, message in enumerate(expected, 1):
        if message not in actual:
            raise ValueError(
                f"MessageBox text changed or disappeared during preprocessing (message {index}); "
                "check the script coding declaration and /INPUTCHARSET UTF8"
            )
    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--makensis", required=True)
    parser.add_argument("compiler_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.compiler_arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    try:
        count = check_encoding(args.makensis, arguments)
    except (OSError, ValueError) as exc:
        print(f"NSIS encoding check failed: {exc}", file=sys.stderr)
        return 1
    print(f"NSIS preprocessing preserved all {count} non-ASCII MessageBox strings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
