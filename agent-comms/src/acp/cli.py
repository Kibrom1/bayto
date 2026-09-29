"""acp CLI: acp --dir DIR --conv ID --role ROLE send|read|ack|transcript"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .core import Conversation, PermissionError_
from .models import Roster
from .roster import tool_cli_args
from .transport import FileTransport


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="acp")
    p.add_argument("--dir", default=os.environ.get("ACP_DIR", "."))
    p.add_argument("--conv", default=os.environ.get("ACP_CONV", "default"))
    p.add_argument("--role", default=os.environ.get("ACP_ROLE"))
    p.add_argument("--roles", default=os.environ.get("ACP_ROLES", ""), help="comma-separated roster")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--to", required=True)
    s.add_argument("--kind", default="note")
    s.add_argument("--reply-to")
    s.add_argument("body")
    sub.add_parser("read")
    a = sub.add_parser("ack")
    a.add_argument("message_id")
    sub.add_parser("transcript")
    r = sub.add_parser("report")
    r.add_argument("message_id")
    r.add_argument("--status", required=True)
    r.add_argument("--stage")
    r.add_argument("--output-ref")
    r.add_argument("--check", action="append", default=[], help="CMD=EXITCODE, repeatable")
    r.add_argument("--summary", default="")
    c = sub.add_parser("claim")
    c.add_argument("name")
    g = sub.add_parser("stage")
    g.add_argument("value", nargs="?")
    t = sub.add_parser("tool-flags", help="tools.json seat entry -> Claude Code CLI flags (M1.9)")
    t.add_argument("--tools-json", required=True)
    t.add_argument("--role", required=True)
    args = p.parse_args(argv)

    if args.cmd == "tool-flags":
        entry = json.loads(open(args.tools_json).read()).get(args.role, {})
        for flag in tool_cli_args(entry):
            print(flag)
        return 0

    roles = [r for r in args.roles.split(",") if r] or [args.role or "human"]
    conv = Conversation(args.conv, Roster(roles=roles), FileTransport(args.dir))
    try:
        if args.cmd == "send":
            env = conv.send(args.role, args.to.split(","), args.kind, args.body, in_reply_to=args.reply_to)
            print(env.message_id)
        elif args.cmd == "read":
            for e in conv.read(args.role):
                print(json.dumps(e.dump()))
        elif args.cmd == "ack":
            conv.ack(args.role, args.message_id)
        elif args.cmd == "transcript":
            for e in conv.transcript():
                print(f"[{e.seq}] {e.from_} -> {','.join(e.to)} ({e.kind}): {e.body}")
        elif args.cmd == "report":
            checks = {k: int(v) for k, _, v in (x.rpartition("=") for x in args.check)}
            print(json.dumps(conv.report(args.role, args.message_id, args.status, checks, args.summary,
                                          args.stage, args.output_ref)))
        elif args.cmd == "claim":
            return 0 if conv.claim(args.name) else 9
        elif args.cmd == "stage":
            print(conv.stage(args.value))
    except ValueError as ex:
        print(f"invalid: {ex}", file=sys.stderr)
        return 2
    except PermissionError_ as ex:
        print(f"permission denied: {ex}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
