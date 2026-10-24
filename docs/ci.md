# Checking flows in CI

`easychain eval` runs a flow's [Test Set](testing.md) and compares it with a **baseline**: a
small JSON file kept in git next to the Test Set. CI then fails when something got worse, and
says which case and how.

## The gate

```bash
easychain eval flows/support-bot.tests.yaml
```

| | The gate fails when… |
|---|---|
| **There's a baseline** (`support-bot.baseline.json` next to the Test Set, or `--baseline FILE`) | a case passes less often than in the baseline: a **regression**. A case that failed before and still fails is a known failure, not a regression. |
| **There's none yet** | any case fails, as with `easychain test`. |

It exits with 0 when the gate passes and 1 when it doesn't, and prints each case and why it
failed.

Make or update the baseline when the results are what you want, and commit it:

```bash
easychain eval flows/support-bot.tests.yaml --save-baseline
git add flows/support-bot.baseline.json
```

### Options

| Option | Does |
|---|---|
| `--baseline FILE` | the baseline to compare with (default: `<name>.baseline.json` next to the Test Set) |
| `--save-baseline` | write this run as the baseline (to `--baseline FILE`, or the default place) |
| `--stand-in` / `--real-model` | the stand-in AI (free, no keys) or the flows' real models; default: as the Test Set says |
| `--repeat N` | run each case N times; a case's pass rate is compared, and flaky cases are marked |
| `--max-slower PCT` | also fail when the whole run takes more than PCT percent longer than the baseline |
| `--max-costlier PCT` | also fail when it costs more than PCT percent more |
| `--budget DOLLARS` | stop starting cases once the run has cost this much (the ones not run fail the gate) |
| `--var NAME=VALUE` | a `${NAME}` value the cases use |
| `--summary FILE` | append a Markdown report (on GitHub: `$GITHUB_STEP_SUMMARY`) |
| `--json FILE` | write the results as JSON |

With the stand-in AI, cases cost nothing and time is about the flow itself, so the gate
checks the flow's logic on every push. Real models are for a check before a release (below).

## GitHub Actions

This repository has an action for it, `.github/actions/easychain-eval`:

```yaml
jobs:
  flows:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Shubham-wiz/EasyChain/.github/actions/easychain-eval@main
        with:
          test-sets: flows/*.tests.yaml
          stand-in: "true"
```

Its inputs: `test-sets` (files or globs), `stand-in` (`file`, `true` or `false`), `repeat`,
`variables` (`NAME=VALUE` lines), `budget`, `max-slower`, `max-costlier`, `install` (what pip
installs; by default Easy Chain from this repository) and `python-version`.

Each Test Set gets a section on the run's summary page: how many cases passed, a table of the
cases with their change against the baseline, time and cost, and why the gate failed.

Easy Chain's own CI runs it over the templates' Test Sets on every push (the **Flows** job).

### Real models before a release

`.github/workflows/real-models.yml` is started by hand, from the Actions tab (decision 5.5). It
runs the templates' Test Sets with their real models, with each case 3 times by default, and
stops starting cases after a budget per Test Set.

It needs `OPENAI_API_KEY` (and `ANTHROPIC_API_KEY` for the Claude templates) under **Settings →
Secrets and variables → Actions**. Give each key a spending limit at the provider too.

## Other CI systems

Any CI that can run Python can run the gate:

```bash
pip install "easychain @ git+https://github.com/Shubham-wiz/EasyChain@main#subdirectory=python"
for f in flows/*.tests.yaml; do
  easychain eval "$f" --stand-in --summary report.md || failed=1
done
exit ${failed:-0}
```

GitLab CI, for example:

```yaml
flows:
  image: python:3.12
  script:
    - pip install "easychain @ git+https://github.com/Shubham-wiz/EasyChain@main#subdirectory=python"
    - for f in flows/*.tests.yaml; do easychain eval "$f" --stand-in --summary report.md || failed=1; done
    - exit ${failed:-0}
  artifacts:
    when: always
    paths: [report.md]
```

## The baseline file

```json
{
  "format": "easychain-test-run/1",
  "test_set": "support-bot.tests.yaml",
  "flow": "support-bot.flow.yaml",
  "stand_in": true,
  "repeat": 1,
  "summary": {"total": 12, "passed": 12, "failed": 0, "flaky": 0, "seconds": 3.2, "cost": 0.0},
  "cases": [{"name": "Refund times", "runs": 1, "passed": 1, "pass_rate": 1.0, "flaky": false,
             "failures": [], "seconds": 0.2, "tokens": 0, "cost": 0.0}]
}
```

It's the same per-case summary that a kept Test Run in the app shows ([testing.md](testing.md)).
