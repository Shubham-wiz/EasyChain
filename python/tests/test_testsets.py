"""Test Sets prove what they say: answers must be asked for, and the model can be chosen."""

from __future__ import annotations

from pathlib import Path

from easychain.testsets import run_test_set

from .conftest import ROOT

HELLO = ROOT / "examples" / "hello.flow.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hello.tests.yaml"
    path.write_text(f"flow: {HELLO.as_posix()}\n" + text, encoding="utf-8", newline="\n")
    return path


async def test_an_answer_the_run_never_asked_for_fails_the_case(tmp_path):
    path = write(
        tmp_path,
        "stand_in: true\ncases:\n"
        "- name: expects an approval that never comes\n"
        "  inputs: {question: hi}\n"
        "  answers: [{action: approve}]\n",
    )
    [result] = await run_test_set(path)
    assert not result.passed
    assert any("weren't used" in f for f in result.failures), result.failures


async def test_the_real_model_can_be_used_even_if_the_file_says_stand_in(tmp_path, fake_openai):
    path = write(
        tmp_path,
        "stand_in: true\ncases:\n- name: says something\n  inputs: {question: hi}\n",
    )
    [stand_in] = await run_test_set(path)
    assert stand_in.passed and stand_in.output["answer"].startswith("[Stand-in AI")
    [real] = await run_test_set(path, stand_in=False)
    assert real.passed, real.failures
    assert not real.output["answer"].startswith("[Stand-in AI")
