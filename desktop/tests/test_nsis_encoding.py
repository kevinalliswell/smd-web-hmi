"""Exercise the encoding gate; Windows builds supply the actual NSIS compiler."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/release/check_nsis_encoding.py"
SPEC = importlib.util.spec_from_file_location("check_nsis_encoding", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

SOURCE = 'Unicode true\nSection\nMessageBox MB_ICONSTOP "安装失败 ${VERSION}；查看日志。" /SD IDOK\nSectionEnd\n'
OUTPUT = SOURCE.replace("${VERSION}", "0.3.0-rc.4")


@pytest.fixture
def compiler_arguments(tmp_path):
    source = tmp_path / "中文路径" / "installer.nsi"
    source.parent.mkdir()
    source.write_text(SOURCE, encoding="utf-8")
    return ["/V3", "/INPUTCHARSET", "UTF8", "/DVERSION=0.3.0-rc.4", str(source)]


def result(stdout=OUTPUT.encode("utf-8"), returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, b"compiler diagnostic")


def test_checks_preprocessed_text_using_the_production_compiler_arguments(compiler_arguments, monkeypatch):
    calls = []
    original = compiler_arguments[:]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return result(b"\xef\xbb\xbf" + OUTPUT.encode("utf-8"))

    monkeypatch.setattr(checker.subprocess, "run", run)
    assert checker.check_encoding("makensis.exe", compiler_arguments) == 1
    assert compiler_arguments == original
    assert calls[0][0] == [
        "makensis.exe",
        *original[:-1],
        "/PPO",
        "/OUTPUTCHARSET",
        "UTF8",
        original[-1],
    ]
    assert calls[0][1] == {"capture_output": True, "timeout": 120, "check": False}


@pytest.mark.parametrize("output", [OUTPUT.encode("utf-8").decode("cp1252", errors="replace"), "Unicode true\n"])
def test_rejects_mojibake_or_missing_message_even_when_preprocessing_succeeds(compiler_arguments, monkeypatch, output):
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **kw: result(output.encode("utf-8")))
    with pytest.raises(ValueError, match="MessageBox text changed"):
        checker.check_encoding("makensis.exe", compiler_arguments)


def test_correct_text_in_a_comment_does_not_hide_a_corrupted_message(compiler_arguments, monkeypatch):
    output = "# " + OUTPUT.splitlines()[2] + '\nMessageBox MB_ICONSTOP "broken"\n'
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **kw: result(output.encode("utf-8")))
    with pytest.raises(ValueError, match="MessageBox text changed"):
        checker.check_encoding("makensis.exe", compiler_arguments)


@pytest.mark.parametrize(
    "source_message, preprocessed_message",
    [
        (r"安装失败$\r$\n查看日志。", "安装失败\r\n查看日志。"),
        (r"安装失败$\n查看日志。", "安装失败\r\n查看日志。"),
        (r"安装失败$\t查看日志。", "安装失败\t查看日志。"),
        (r"字面量$$\n保留。", r"字面量$$\n保留。"),
        (r"字面量$$$$\r保留。", r"字面量$$$$\r保留。"),
        (r"字面量$$$\n与换行。", "字面量$$\n与换行。"),
    ],
)
def test_matches_nsis_311_control_character_expansion(
    compiler_arguments, monkeypatch, source_message, preprocessed_message
):
    # NSIS v3.11 ps_addtoline expands r/n/t before /PPO prints the quoted line.
    # A pair of dollar signs is preserved by the preprocessor and prevents the
    # following backslash from beginning another control-character expansion.
    source = f'MessageBox MB_ICONSTOP "{source_message}" /SD IDOK\n'
    output = f'MessageBox MB_ICONSTOP "{preprocessed_message}" /SD IDOK\n'
    Path(compiler_arguments[-1]).write_text(source, encoding="utf-8")
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **kw: result(output.encode("utf-8")))
    assert checker.check_encoding("makensis.exe", compiler_arguments) == 1


def test_does_not_decode_literal_escaped_output_a_second_time(compiler_arguments, monkeypatch):
    source = 'MessageBox MB_ICONSTOP "字面量$$\\n保留。" /SD IDOK\n'
    incorrectly_expanded = 'MessageBox MB_ICONSTOP "字面量$\n保留。" /SD IDOK\n'
    Path(compiler_arguments[-1]).write_text(source, encoding="utf-8")
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **kw: result(incorrectly_expanded.encode("utf-8")))
    with pytest.raises(ValueError, match="MessageBox text changed"):
        checker.check_encoding("makensis.exe", compiler_arguments)


def test_nonzero_compiler_exit_cannot_pass_with_correct_stdout(compiler_arguments, monkeypatch):
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **kw: result(returncode=7))
    with pytest.raises(ValueError, match="makensis preprocessing failed.*7"):
        checker.check_encoding("makensis.exe", compiler_arguments)


def test_compiler_output_must_be_valid_utf8(compiler_arguments, monkeypatch):
    monkeypatch.setattr(checker.subprocess, "run", lambda *a, **kw: result(b"\xff"))
    with pytest.raises(ValueError, match="UTF-8"):
        checker.check_encoding("makensis.exe", compiler_arguments)


def test_timeout_is_a_bounded_gate_failure(compiler_arguments, monkeypatch):
    def stalled(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(checker.subprocess, "run", stalled)
    with pytest.raises(ValueError, match="timed out"):
        checker.check_encoding("makensis.exe", compiler_arguments)


def test_absent_chinese_checks_cannot_silently_pass(compiler_arguments, monkeypatch):
    Path(compiler_arguments[-1]).write_text('MessageBox MB_OK "ASCII only"\n', encoding="utf-8")
    monkeypatch.setattr(
        checker.subprocess, "run", lambda *a, **kw: pytest.fail("no messages must fail before compilation")
    )
    with pytest.raises(ValueError, match="No non-ASCII MessageBox"):
        checker.check_encoding("makensis.exe", compiler_arguments)


def test_cli_reports_failure_without_traceback_when_compiler_cannot_start(compiler_arguments, tmp_path):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--makensis", str(tmp_path / "missing-compiler"), "--", *compiler_arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    assert completed.returncode != 0
    assert "NSIS encoding check failed:" in completed.stderr
    assert "Traceback" not in completed.stderr
