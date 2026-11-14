"""The command line. Without options it opens the window."""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__, command, config, options, strata
from .proc import Runner, clean_frozen_environment, own_scope


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strata-launcher", description=f"{APP_NAME}: set up, start, stop and watch a Strata server. "
                                "Without options it opens the window.")
    p.add_argument("--version", action="store_true", help="print the launcher's version")
    p.add_argument("--print", action="store_true", dest="print_command", help="print the strata command for the saved settings")
    p.add_argument("--start", action="store_true", help="do what the Start button does, without the window")
    p.add_argument("--stop", action="store_true", help="do what the Stop button does, without the window")
    p.add_argument("--status", action="store_true", help="say what Strata is serving now")
    p.add_argument("--at-login", action="store_true", help="used by the start-at-sign-in entry")
    p.add_argument("--tab", choices=("settings", "performance", "log", "setup", "app"), help="open the window on this tab")
    p.add_argument("--selftest", action="store_true",
                   help="open the window, press its real buttons against a stand-in for Strata and count threads and programs")
    p.add_argument("--selftest-seconds", type=float, default=6.0, help=argparse.SUPPRESS)
    return p


def _saved():
    cfg, _notes, _existed = config.load()
    opts, _problems = options.load()
    cfg["values"] = options.clean_values(opts, cfg.get("values"))
    if not cfg.get("strata_command"):
        cfg["strata_command"] = strata.discover() or ""
    return cfg, opts


def _need_strata(cfg) -> bool:
    if cfg.get("strata_command"):
        return True
    print("Strata was not found. Open the launcher and choose its folder on the Setup tab.", file=sys.stderr)
    return False


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    clean_frozen_environment()
    args = parser().parse_args(argv)
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.print_command or args.start or args.stop or args.status:
        cfg, opts = _saved()
        if not _need_strata(cfg):
            return 2
        runner = Runner()
        if args.print_command:
            print(command.printable(command.launch_command(cfg, opts)))
            return 0
        if args.status:
            _code, out = runner.capture(command.status_command(cfg), timeout=60)
            runs = strata.parse_status(out)
            print("\n".join(f"{r['name']}: {r['state']} at {r['url']}" for r in runs) or "Nothing is serving.")
            return 0
        if args.stop:
            return 0 if runner.stream(command.stop_command(cfg), print) == 0 else 1
        why = command.source_problem(cfg["source"])
        if why:
            print(why, file=sys.stderr)
            return 2
        cmd = command.launch_command(cfg, opts)
        return 0 if runner.stream(own_scope(cmd) if cfg.get("own_scope", True) else cmd, print) == 0 else 1
    if args.selftest:
        from . import selftest
        return selftest.run(seconds=args.selftest_seconds)
    from .ui import app
    return app.run([sys.argv[0]] + argv)


if __name__ == "__main__":
    sys.exit(main())
