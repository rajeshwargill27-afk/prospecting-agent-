"""CLI entrypoint.

    python run.py                          # phases 1-5, stops at the gate
    python run.py --approve <token>        # phase 6, requires the token
"""

import argparse

from src.pipeline import run, send

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="config/furniture_arte.json")
parser.add_argument("--backend", default="mock", choices=["mock", "live"])
parser.add_argument("--approve", metavar="TOKEN",
                    help="approval token from out/send_manifest.json")
args = parser.parse_args()

if args.approve:
    send(args.config, args.approve, backend=args.backend)
else:
    run(args.config, backend=args.backend)
