"""Command line entry point: ``dea <command>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _cmd_synth(args: argparse.Namespace) -> int:
    from dea.synth import generate_cohort

    cohort = generate_cohort(
        n_encounters=args.n, seed=args.seed, name=args.name or Path(args.out).name
    )
    cohort.save(args.out)
    print(f"wrote {len(cohort)} synthetic encounters to {args.out}")
    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    from dea.io import detect

    det = detect(args.source)
    print(
        json.dumps(
            {k: (str(v) if isinstance(v, Path) else v) for k, v in det.__dict__.items()},
            indent=2,
            default=str,
        )
    )
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from dea.io import import_path

    overrides = {
        k: getattr(args, k)
        for k in (
            "encounter_col",
            "time_col",
            "variable_col",
            "value_col",
            "time_unit",
            "table_kind",
        )
        if getattr(args, k, None)
    }
    cohort, report = import_path(args.source, name=args.name, static_path=args.static, **overrides)
    cohort.save(args.out)
    for line in report.summary_lines():
        print(line)
    print(f"saved to {args.out}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    from dea.io import load_cohort

    cohort = load_cohort(args.cohort)
    s = cohort.summary()
    print(
        f"{s.name}: {s.n_encounters} encounters, {s.n_variables} variables, "
        f"{s.n_observations} observations"
    )
    print(
        f"length of stay (hours): median {s.los_hours_median:.1f}, "
        f"min {s.los_hours_min:.1f}, max {s.los_hours_max:.1f}"
    )
    print("static columns:", ", ".join(cohort.static_columns) or "-")
    print("variables:", ", ".join(cohort.variables))
    if cohort.config.filters:
        print("filters:", ", ".join(r.label for r in cohort.config.filters))
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    from dea.io import load_cohort
    from dea.profile import profile_cohort

    profile = profile_cohort(load_cohort(args.cohort))
    if args.json:
        print(profile.to_json())
        return 0
    print(f"{profile.name}: {profile.n_encounters} encounters, {profile.n_variables} variables")
    sp = profile.sampling
    if sp.median_interval_s is not None:
        print(
            f"sampling: median interval {sp.median_interval_s:.0f}s, "
            f"regular fraction {sp.regular_fraction:.2f}, suggested grid {sp.suggested_resample}"
        )
    print(
        profile.variables_frame().select(
            "name", "coverage", "interval", "min", "median", "max", "flags"
        )
    )
    for w in profile.warnings:
        print("warning:", w)
    return 0


def _cmd_advise(args: argparse.Namespace) -> int:
    from dea.advise import Advisor, AdvisorError
    from dea.io import load_cohort
    from dea.profile import profile_cohort

    cohort = load_cohort(args.cohort)
    profile = profile_cohort(cohort)
    advisor = Advisor(model=args.model, base_url=args.base_url)
    if args.show_payload:
        print(json.dumps(advisor.payload(profile), indent=2))
        return 0
    try:
        rec = advisor.recommend(profile)
    except AdvisorError as exc:
        print(f"advisor failed: {exc}", file=sys.stderr)
        return 1
    print(rec.model_dump_json(indent=2))
    if args.apply:
        cohort.config = rec.apply_to(cohort.config, cohort.variables)
        cohort.config.save(cohort.root / "cohort.yaml")
        print(f"applied recommendation to {cohort.root / 'cohort.yaml'}")
    return 0


THEME_ARGS = [
    "--theme.primaryColor",
    "#2a78d6",
    "--theme.backgroundColor",
    "#fcfcfb",
    "--theme.secondaryBackgroundColor",
    "#f1f1ee",
    "--theme.textColor",
    "#0b0b0b",
]


def _cmd_ui(args: argparse.Namespace) -> int:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("streamlit is not installed: pip install dea[ui]", file=sys.stderr)
        return 1
    if args.data_dir:
        os.environ["DEA_DATA_DIR"] = str(Path(args.data_dir).resolve())
    app = Path(__file__).parent / "ui" / "app.py"
    sys.argv = [
        "streamlit",
        "run",
        str(app),
        "--server.headless",
        "true" if args.headless else "false",
        "--browser.gatherUsageStats",
        "false",
        *THEME_ARGS,
    ]
    if args.port:
        sys.argv += ["--server.port", str(args.port)]
    return int(stcli.main() or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dea", description="Diagnostic Expert Advisor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("synth", help="generate a synthetic cohort")
    p.add_argument("out")
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--name")
    p.set_defaults(func=_cmd_synth)

    p = sub.add_parser("detect", help="guess the layout of a data source")
    p.add_argument("source")
    p.set_defaults(func=_cmd_detect)

    p = sub.add_parser("import", help="import a data source into a DEA cohort")
    p.add_argument("source")
    p.add_argument("out")
    p.add_argument("--name")
    p.add_argument("--static", help="separate static table (csv/parquet)")
    p.add_argument("--encounter-col")
    p.add_argument("--time-col")
    p.add_argument("--variable-col")
    p.add_argument("--value-col")
    p.add_argument("--time-unit", choices=["auto", "datetime", "s", "ms", "m", "h", "d"])
    p.add_argument("--table-kind", choices=["long", "wide"])
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("info", help="summarize a cohort")
    p.add_argument("cohort")
    p.set_defaults(func=_cmd_info)

    p = sub.add_parser("profile", help="profile a cohort")
    p.add_argument("cohort")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_profile)

    p = sub.add_parser("advise", help="ask the AI advisor for processing recommendations")
    p.add_argument("cohort")
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--show-payload", action="store_true", help="print what would be sent and exit")
    p.add_argument("--apply", action="store_true", help="write the recommendation into cohort.yaml")
    p.set_defaults(func=_cmd_advise)

    p = sub.add_parser("ui", help="start the Streamlit app")
    p.add_argument("--data-dir")
    p.add_argument("--port", type=int)
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=_cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
