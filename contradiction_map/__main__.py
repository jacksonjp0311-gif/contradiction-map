"""One entry point for the local UI and agent-readable scan reports."""
import argparse
import json
from pathlib import Path
import sys

from .engine import ScanCancelled, ScanError, scan

def _port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port

def main(argv=None):
    parser = argparse.ArgumentParser(prog="contradiction-map",description="Map narrow, evidence-backed repository inconsistencies locally.")
    sub = parser.add_subparsers(dest="command",required=True)
    scan_parser = sub.add_parser("scan",help="Read-only scan of a local folder or file")
    scan_parser.add_argument("path")
    scan_parser.add_argument("--json",action="store_true",help="Print machine-readable schema 1.0 JSON")
    scan_parser.add_argument("--output",type=Path,help="Write the report to this file (JSON with --json)")
    scan_parser.add_argument("--fail-on",choices=["deterministic","any"],help="Return exit 1 if the chosen findings are present")
    serve_parser = sub.add_parser("serve",help="Start the localhost UI and companion service")
    serve_parser.add_argument("--port",type=_port,default=8765)
    serve_parser.add_argument("--open",action="store_true",help="Open the UI in the default browser")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve
            serve(host="127.0.0.1",port=args.port,open_browser=args.open)
            return 0
        report = scan(args.path)
        if args.json:
            output = json.dumps(report,indent=2,ensure_ascii=True)
        else:
            s = report["summary"]
            lines = [f"Contradiction Map · {report['target']['name']}",f"{s['files_scanned']} files · {s['deterministic']} deterministic conflicts · {s['heuristic']} heuristic candidates"]
            for finding in report["findings"]:
                lines.append(f"\n{finding['rank']}. [{finding['severity']}/{finding['kind']}] {finding['rule_id']} {finding['title']}")
                lines.append(finding["explanation"])
                for evidence in finding["evidence"]:
                    lines.append(f"  {evidence['path']}:{evidence['line']}  {evidence['text']}")
            lines.extend(f"Warning: {w}" for w in report["coverage"]["warnings"])
            lines.append("\nA clean result is not proof of consistency. Use --json for coverage and limitations.")
            output = "\n".join(lines)
            output = "".join(char if char.isprintable() or char in "\n\t" else f"\\u{ord(char):04x}" for char in output)
        if args.output:
            args.output.write_text(output+"\n",encoding="utf-8")
        else:
            print(output)
        if args.fail_on == "any" and report["summary"]["findings"]: return 1
        if args.fail_on == "deterministic" and report["summary"]["deterministic"]: return 1
        return 0
    except (ScanError,OSError) as exc:
        print(f"Error: {exc}",file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled.",file=sys.stderr)
        return 130

if __name__ == "__main__":
    raise SystemExit(main())
