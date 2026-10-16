"""Command line entry point: ``dea <command>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import polars as pl

from dea.update import REPO


def _cmd_synth(args: argparse.Namespace) -> int:
    if args.mimic:
        from dea.synth import write_mimic_demo

        write_mimic_demo(args.out, n_stays=args.n, seed=args.seed)
        print(f"wrote {args.n} synthetic ICU stays in MIMIC-IV layout to {args.out}")
        return 0
    from dea.synth import write_synthetic

    write_synthetic(args.out, n_encounters=args.n, seed=args.seed, name=args.name, sites=args.sites)
    print(f"wrote {args.n} synthetic encounters to {args.out}")
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
            "unit_col",
            "time_unit",
            "table_kind",
        )
        if getattr(args, k, None)
    }
    fixes = None
    if args.fix:
        fixes = "recommended" if args.fix == "recommended" else args.fix.split(",")
    cohort, report = import_path(
        args.source, name=args.name, static_path=args.static, fixes=fixes, **overrides
    )
    if args.mapping:
        from dea.standards.mapping import apply_mapping, load_mapping, load_units, mapping_report

        mapping = load_mapping(args.mapping)
        info = mapping_report(cohort, mapping)
        cohort = apply_mapping(cohort, mapping, load_units(args.mapping))
        report.mapping["site_mapping"] = (
            f"{len(info['applied'])} applied, {len(info['unused'])} unused"
        )
    cohort.save(args.out)
    report.save(args.out)
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
        if args.rename:
            cohort = cohort.rename_variables(rec.variable_map(cohort.variables))
            cohort.save()
            print(f"renamed {len(cohort.config.variable_map)} variable(s) and saved {cohort.root}")
        else:
            cohort.config.save(cohort.root / "cohort.yaml")
        print(f"applied recommendation to {cohort.root / 'cohort.yaml'}")
    return 0


def _cmd_rename(args: argparse.Namespace) -> int:
    from dea.io import load_cohort

    cohort = load_cohort(args.cohort)
    if args.mapping_file:
        from dea.standards.mapping import apply_mapping, load_mapping, load_units, mapping_report

        mapping = load_mapping(args.mapping_file)
        info = mapping_report(cohort, mapping)
        cohort = apply_mapping(cohort, mapping, load_units(args.mapping_file))
        print(
            f"applied {len(info['applied'])}, unused {len(info['unused'])}, "
            f"still unmapped {len(info['unmapped'])}"
        )
    else:
        mapping = dict(pair.split("=", 1) for pair in args.mapping)
        cohort = cohort.rename_variables(mapping)
    cohort.save()
    print(f"variables now: {', '.join(cohort.variables)}")
    return 0


def _cmd_suggest(args: argparse.Namespace) -> int:
    from dea.io import load_cohort
    from dea.suggest import apply_suggestions, suggest

    cohort = load_cohort(args.cohort)
    suggestions = suggest(cohort)
    for s in suggestions:
        mark = "*" if s.applicable else " "
        print(f"[{mark}] {s.kind:10} {s.title}\n      {s.detail}")
    if args.apply:
        chosen = [
            s for s in suggestions if s.applicable and (not args.kinds or s.kind in args.kinds)
        ]
        apply_suggestions(cohort, chosen).save()
        print(f"applied {len(chosen)} suggestion(s) and saved {cohort.root}")
    return 0


def _cmd_nlp(args: argparse.Namespace) -> int:
    from dea.nlp import CONNECTORS, available_connectors, make_annotator

    if args.nlp_command == "list":
        for name, ok in available_connectors().items():
            doc = (CONNECTORS[name].__doc__ or "").strip().splitlines()[0]
            print(f"{'[x]' if ok else '[ ]'} {name:13} {doc}")
        return 0
    if args.nlp_command == "install":
        cls = CONNECTORS[args.connector]
        if not hasattr(cls, "install"):
            print(f"{args.connector} needs no model download")
            return 0
        cls.install(*([args.model] if args.model else []))
        print(f"installed {args.connector} model{' ' + args.model if args.model else ''}")
        return 0
    from dea.io import load_cohort
    from dea.nlp import annotate_cohort, entity_summary

    cohort = load_cohort(args.cohort)
    kwargs = {"model": args.model} if args.model else {}
    if args.labels:
        kwargs["labels"] = args.labels
    annotator = make_annotator(args.connector, **kwargs)
    entities = annotate_cohort(
        cohort, annotator, event_variables=args.events or None, static_columns=args.static or None
    )
    print(
        f"{entities.height} entities from {args.connector}; stored under derived/entities.parquet"
    )
    print(entity_summary(entities, len(cohort)).head(25))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from dea.io import load_cohort
    from dea.quality import run_checks

    found = run_checks(load_cohort(args.cohort))
    if args.json:
        print(json.dumps([c.to_dict() for c in found], indent=2))
        return 0
    if not found:
        print("no findings")
    for c in found:
        fix = "  [fix available]" if c.action else ""
        print(f"{c.severity:>7}  {c.title}{fix}")
        print(f"         {c.detail}")
    return 1 if any(c.severity == "error" for c in found) else 0


def _cmd_sites(args: argparse.Namespace) -> int:
    from dea.io import load_cohort
    from dea.quality import detect_site_column, site_checks, site_profile

    cohort = load_cohort(args.cohort)
    col = args.column or cohort.config.site_column or detect_site_column(cohort.static)
    if not col:
        print("no site column found; pass --column", file=sys.stderr)
        return 1
    prof = site_profile(cohort, col)
    print(f"site column: {col}")
    with pl.Config(tbl_rows=200, tbl_cols=12, fmt_str_lengths=40):
        print(
            prof.select(
                "site",
                "variable",
                "n_encounters",
                "coverage",
                "n_obs",
                "median",
                "p25",
                "p75",
                "interval_s",
            )
        )
    for c in site_checks(cohort, col, prof):
        print(f"{c.severity:>7}  {c.title}")
        print(f"         {c.detail}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from dea.io import load_cohort

    cohort = load_cohort(args.cohort)
    if args.fhir:
        from dea.standards.fhir import export_fhir

        written = export_fhir(cohort, args.out)
        print(", ".join(f"{n} {k}" for k, n in written.items()), f"-> {args.out}")
        return 0
    out = Path(args.out)
    cohort.wide(every=args.every, with_static=True).write_parquet(out)
    print(f"wrote {out}")
    return 0


def _cmd_ml(args: argparse.Namespace) -> int:
    from dea.io import load_cohort
    from dea.ml import DatasetSpec, make_dataset

    cohort = load_cohort(args.cohort)
    spec = DatasetSpec(
        label=args.label,
        cutoff_hours=args.cutoff,
        split=args.split,
        test_size=args.test_size,
        seed=args.seed,
        exclude=args.exclude or [],
    )
    ds = make_dataset(cohort, spec)
    out = Path(args.out)
    ds.frame().write_parquet(out)
    info = ds.summary()
    print(
        f"{info['n']} encounters, {info['n_features']} features, {info['positives']} positive, "
        f"train {info['train']} / test {info['test']}"
    )
    if info["dropped"]:
        print("dropped to avoid leakage:", ", ".join(info["dropped"]))
    print(f"wrote {out}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from dea.io import load_cohort
    from dea.report import write_report

    out = write_report(load_cohort(args.cohort), args.out)
    print(f"wrote {out}")
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    from dea.update import check, describe

    inst = describe()
    where = f" ({inst.root})" if inst.root else ""
    commit = f" at {inst.commit[:9]}" if inst.commit else ""
    branch = f" on {inst.branch}" if inst.branch else ""
    print(f"dea {inst.version}, installed from {inst.mode}{where}{commit}{branch}")
    if args.check:
        info = check()
        if info["update_available"] is None:
            print("could not compare with GitHub (offline, or no commit recorded)")
        elif info["update_available"]:
            print(f"update available: {info['latest'][:9]} on {REPO}. Run: dea update")
        else:
            print("up to date")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    from dea.update import check, update, update_command

    if args.check:
        info = check()
        state = {None: "unknown", True: "update available", False: "up to date"}
        print(f"{state[info['update_available']]}; would run:")
        for cmd in info["command"]:
            print("  " + " ".join(cmd))
        return 0 if info["update_available"] is not True else 3
    code = update()
    if code:
        print(f"update failed (exit {code}); the commands were:", file=sys.stderr)
        for cmd in update_command():
            print("  " + " ".join(cmd), file=sys.stderr)
        return code
    print("DEA is up to date. Restart `dea serve` to pick up the new code.")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from dea.update import auto_update_requested, update

    if auto_update_requested(args.auto_update) and not os.environ.get("DEA_UPDATED"):
        if update() == 0:
            # the running process still has the old code: start over without updating again
            env = {**os.environ, "DEA_UPDATED": "1"}
            argv = [a for a in sys.argv[1:] if a != "--auto-update"]
            os.execve(sys.executable, [sys.executable, "-m", "dea.cli", *argv], env)
        print("warning: update failed, starting the installed version", file=sys.stderr)
    try:
        import uvicorn
    except ImportError:
        print("uvicorn/fastapi are not installed: pip install dea[web]", file=sys.stderr)
        return 1
    if args.data_dir:
        os.environ["DEA_DATA_DIR"] = str(Path(args.data_dir).resolve())
    data = os.environ.get("DEA_DATA_DIR", "data")
    from dea.api.app import find_frontend

    if find_frontend() is None:
        print(
            "warning: the web app is not built; only the API will be served. "
            "Run `npm ci && npm run build` in frontend/ (see README).",
            file=sys.stderr,
        )
    print(f"DEA web app on http://{args.host}:{args.port}  (data: {data})")
    uvicorn.run(
        "dea.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dea", description="Diagnostic Expert Advisor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("synth", help="generate a synthetic cohort")
    p.add_argument("out")
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--name")
    p.add_argument("--mimic", action="store_true", help="write MIMIC-IV-shaped CSV tables instead")
    p.add_argument("--sites", type=int, default=1, help="spread encounters over N hospitals")
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
    p.add_argument("--unit-col", help="per-row unit column of a long table")
    p.add_argument(
        "--fix",
        help="apply check fixes before writing: 'recommended' (dedupe, unit conversions, "
        "numbers in text) or a comma-separated list of check ids from `dea check`",
    )
    p.add_argument(
        "--mapping", help="site mapping YAML (local names -> concepts), applied on import"
    )
    p.add_argument("--time-unit", choices=["auto", "datetime", "s", "ms", "m", "h", "d"])
    p.add_argument("--table-kind", choices=["long", "wide"])
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("check", help="run the data quality checks on a cohort")
    p.add_argument("cohort")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser("sites", help="compare variables across sites / hospitals / exports")
    p.add_argument("cohort")
    p.add_argument("--column", help="static column naming the site")
    p.set_defaults(func=_cmd_sites)

    p = sub.add_parser("export", help="export a cohort as a wide Parquet table or FHIR NDJSON")
    p.add_argument("cohort")
    p.add_argument("out")
    p.add_argument("--fhir", action="store_true", help="write FHIR R4 NDJSON resources")
    p.add_argument("--every", help="resampling grid for the wide table, e.g. 1h")
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("ml", help="write an encounter-level (X, y) table with cutoff and split")
    p.add_argument("cohort")
    p.add_argument("out")
    p.add_argument("--label", required=True, help="tag label, static condition or SQL rule")
    p.add_argument("--cutoff", type=float, help="use only data before this many hours")
    p.add_argument("--split", default="encounter", help="encounter | site | group:<column>")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--exclude", nargs="*", help="static columns to leave out")
    p.set_defaults(func=_cmd_ml)

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
    p.add_argument("--rename", action="store_true", help="with --apply: also rename variables")
    p.set_defaults(func=_cmd_advise)

    p = sub.add_parser("rename", help="rename variables, e.g. dea rename data/c HF=heart_rate")
    p.add_argument("cohort")
    p.add_argument("mapping", nargs="*", metavar="OLD=NEW")
    p.add_argument("--mapping-file", help="site mapping YAML instead of OLD=NEW pairs")
    p.set_defaults(func=_cmd_rename)

    p = sub.add_parser(
        "suggest", help="rule-based suggestions: harmonization, processing, tags, quality"
    )
    p.add_argument("cohort")
    p.add_argument("--apply", action="store_true", help="apply all applicable suggestions and save")
    p.add_argument(
        "--kinds",
        nargs="*",
        help="with --apply: only these kinds (harmonize static drop processing tag filter plot)",
    )
    p.set_defaults(func=_cmd_suggest)

    p = sub.add_parser("nlp", help="local NLP connectors: NER, de-identification, LLM extraction")
    nsub = p.add_subparsers(dest="nlp_command", required=True)
    nsub.add_parser("list", help="show connectors and whether they are installed")
    q = nsub.add_parser("install", help="download a connector's model into DEA_MODELS_DIR")
    q.add_argument("connector", choices=["spacy", "gliner", "transformers"])
    q.add_argument("--model")
    q = nsub.add_parser("run", help="annotate a cohort's free text")
    q.add_argument("cohort")
    q.add_argument(
        "--connector",
        default="spacy",
        choices=["spacy", "gliner", "transformers", "presidio", "llm"],
    )
    q.add_argument("--model")
    q.add_argument("--labels", nargs="*", help="entity types for gliner/llm")
    q.add_argument("--events", nargs="*", help="event variables to annotate (default: all)")
    q.add_argument("--static", nargs="*", help="static text columns to annotate")
    p.set_defaults(func=_cmd_nlp)

    p = sub.add_parser("report", help="write a one-page HTML report of a cohort")
    p.add_argument("cohort")
    p.add_argument("out")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("serve", help="start the web app (FastAPI backend + React frontend)")
    p.add_argument("--data-dir")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.add_argument(
        "--auto-update",
        action="store_true",
        help="run `dea update` first, then start (also DEA_AUTO_UPDATE=1)",
    )
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser(
        "update", help="bring this installation up to date (git pull + uv sync, or uv tool upgrade)"
    )
    p.add_argument("--check", action="store_true", help="only report whether an update exists")
    p.set_defaults(func=_cmd_update)

    p = sub.add_parser("version", help="version, install mode and commit")
    p.add_argument("--check", action="store_true", help="also compare with GitHub")
    p.set_defaults(func=_cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
