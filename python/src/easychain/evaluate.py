"""``easychain eval``: the CI gate (Phase 5.3, docs/ci.md).

It runs a Test Set (each case ``repeat`` times), compares it with a **baseline file** kept in
git next to the Test Set, and says whether anything got worse:

- with a baseline, a **regression** is a case that passes less often than it did (a case that
  failed before and still fails is a known failure, not a regression), or the whole run
  getting slower or dearer than allowed (``--max-slower``, ``--max-costlier``);
- without one, every case must pass, as with ``easychain test``.

``--save-baseline`` writes the run as the new baseline. ``--budget`` stops starting cases once
the run has cost that many dollars (for real models in CI), and says which didn't run.
``--summary`` appends a Markdown report (GitHub's ``$GITHUB_STEP_SUMMARY``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .testruns import FORMAT, case_summary, compare, describe_change, summary
from .testsets import _substitute, run_case


@dataclass
class Evaluation:
    test_set: str
    flow: str
    stand_in: bool
    repeat: int
    cases: list[dict[str, Any]]
    summary: dict[str, Any]
    # Cases that didn't run because the budget was spent.
    not_run: list[str] = field(default_factory=list)
    comparison: dict[str, Any] | None = None
    # Why the gate fails (empty: it passes).
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def baseline(self) -> dict[str, Any]:
        """What a baseline file keeps."""
        return {
            "format": FORMAT,
            "test_set": self.test_set,
            "flow": self.flow,
            "stand_in": self.stand_in,
            "repeat": self.repeat,
            "created_at": time.time(),
            "summary": self.summary,
            "cases": self.cases,
        }


async def run_evaluation(
    path: str | Path,
    *,
    variables: dict[str, str] | None = None,
    stand_in: bool | None = None,
    repeat: int = 1,
    budget: float | None = None,
    sandbox: Any = None,
) -> Evaluation:
    """Run every case ``repeat`` times (stopping once ``budget`` dollars are spent)."""
    from .spec import load_spec
    from .templates.samples import ensure_sample_knowledge, ensure_samples

    ensure_samples()
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _substitute(data, variables or {})
    spec = load_spec(path.parent / data["flow"])
    if any(
        st.type == "knowledge_search" and st.settings.knowledge_base == "help_centre"
        for st in spec.steps
    ):
        ensure_sample_knowledge()
    if stand_in is None:
        stand_in = bool(data.get("stand_in"))
    spent = 0.0
    cases: list[dict[str, Any]] = []
    not_run: list[str] = []
    for case in data.get("cases") or []:
        name = str(case.get("name") or "case")
        if budget is not None and spent >= budget:
            not_run.append(name)
            continue
        runs = []
        for _ in range(max(1, repeat)):
            result = await run_case(
                spec, case, stand_in, sandbox, judge_model=data.get("judge_model")
            )
            runs.append(result.to_dict())
            spent += result.metrics.get("cost") or 0.0
        cases.append(case_summary(name, runs))
    return Evaluation(
        test_set=path.name,
        flow=str(data["flow"]),
        stand_in=stand_in,
        repeat=max(1, repeat),
        cases=cases,
        summary=summary(cases),
        not_run=not_run,
    )


def load_baseline(path: str | Path) -> dict[str, Any] | None:
    """A baseline file, or None when there's none yet."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(
            f"{path.name} isn't an Easy Chain baseline (format {data.get('format')!r}); make one "
            "with `easychain eval … --save-baseline`."
        )
    return data


def save_baseline(path: str | Path, evaluation: Evaluation) -> None:
    Path(path).write_text(
        json.dumps(evaluation.baseline(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _percent(after: float | None, before: float | None) -> float | None:
    if after is None or before is None or before <= 0:
        return None
    return (after - before) / before * 100


def gate(
    evaluation: Evaluation,
    baseline: dict[str, Any] | None,
    *,
    max_slower: float | None = None,
    max_costlier: float | None = None,
) -> Evaluation:
    """Fill in the comparison and what makes the gate fail."""
    problems: list[str] = []
    if baseline is None:
        problems += [
            f"{c['name']} failed: {'; '.join(c['failures']) or 'see above'}"
            for c in evaluation.cases
            if c["passed"] < c["runs"]
        ]
    else:
        evaluation.comparison = compare(evaluation.cases, baseline.get("cases") or [])
        problems += [
            f"Regression: {describe_change(row)}"
            for row in evaluation.comparison["cases"]
            if row["change"] == "worse"
        ]
        before = baseline.get("summary") or {}
        slower = _percent(evaluation.summary["seconds"], before.get("seconds"))
        if max_slower is not None and slower is not None and slower > max_slower:
            problems.append(
                f"The run took {slower:.0f}% longer than the baseline (at most {max_slower:g}% "
                "allowed)."
            )
        costlier = _percent(evaluation.summary["cost"], before.get("cost"))
        if max_costlier is not None and costlier is not None and costlier > max_costlier:
            problems.append(
                f"The run cost {costlier:.0f}% more than the baseline (at most "
                f"{max_costlier:g}% allowed)."
            )
    if evaluation.not_run:
        problems.append(
            f"The budget was spent before {len(evaluation.not_run)} case(s) ran: "
            f"{', '.join(evaluation.not_run)}."
        )
    evaluation.problems = problems
    return evaluation


_MARK = {"better": "⬆ better", "worse": "⬇ worse", "same": "same", "new": "new", "gone": "not run"}


def _cell(text: str) -> str:
    """Text for a Markdown table cell (a | would end the cell)."""
    return text.replace("|", "\\|")


def markdown(evaluation: Evaluation) -> str:
    """A report for a CI run's summary page."""
    s = evaluation.summary
    cost = "unknown" if s["cost"] is None else f"${s['cost']:.4f}"
    lines = [
        f"### {'✅' if evaluation.ok else '❌'} {evaluation.test_set}",
        "",
        f"{s['passed']} of {s['total']} cases passed"
        + (f", {s['flaky']} flaky" if s["flaky"] else "")
        + f" · {s['seconds']:.1f} s · {cost}"
        + (" · stand-in AI" if evaluation.stand_in else "")
        + (f" · each case {evaluation.repeat} times" if evaluation.repeat > 1 else ""),
        "",
        "| Case | Passed | Against the baseline | Time | Cost |",
        "|---|---|---|---|---|",
    ]
    changes = {r["name"]: r for r in (evaluation.comparison or {}).get("cases", [])}
    for case in evaluation.cases:
        row = changes.get(case["name"])
        change = _MARK[row["change"]] if row else "–"
        passed = f"{case['passed']}/{case['runs']}" + (" (flaky)" if case["flaky"] else "")
        case_cost = "–" if case["cost"] is None else f"${case['cost']:.4f}"
        name = _cell(case["name"])
        lines.append(f"| {name} | {passed} | {change} | {case['seconds']:.1f} s | {case_cost} |")
    for name in evaluation.not_run:
        lines.append(f"| {_cell(name)} | not run (budget) | – | – | – |")
    if evaluation.problems:
        lines += ["", "**Why it failed:**", ""] + [f"- {p}" for p in evaluation.problems]
    return "\n".join(lines) + "\n"
