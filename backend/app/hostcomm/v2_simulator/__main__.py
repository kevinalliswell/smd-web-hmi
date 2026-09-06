"""Run an explicitly configured inert loopback device, independently of FastAPI."""

import argparse
import asyncio
from pathlib import Path

from . import SimulatorPairing, V2Simulator, synthetic_profile


def parser():
    result = argparse.ArgumentParser(description="HostComm 2.0 inert loopback simulator; never hardware I/O")
    result.add_argument(
        "--storage", type=Path, required=True, help="dedicated simulator SQLite path, not the HMI database"
    )
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=34212)
    transport = result.add_mutually_exclusive_group(required=True)
    transport.add_argument("--test-plaintext", action="store_true", help="explicit inert loopback-only TCP test mode")
    transport.add_argument(
        "--psk-file", type=Path, help="private 64-hex PSK file; TLS AES128 policy must be set at process start"
    )
    result.add_argument(
        "--approve-synthetic-profile",
        action="store_true",
        help="allow runs on synthetic SOFTWARE values; never approval for real equipment",
    )
    defaults = SimulatorPairing()
    for name in ("device_id", "controller_id", "controller_epoch"):
        result.add_argument("--" + name.replace("_", "-"), default=getattr(defaults, name))
    result.add_argument("--role", choices=("control", "diagnostic"), default="control")
    return result


async def run(args):
    pairing = SimulatorPairing(args.device_id, args.controller_id, args.controller_epoch, args.role)
    simulator = V2Simulator(
        args.storage,
        host=args.host,
        port=args.port,
        psk_file=args.psk_file,
        pairing=pairing,
        test_plaintext=args.test_plaintext,
        profile=synthetic_profile(approved=args.approve_synthetic_profile),
        auto_sample=True,
    )
    try:
        await simulator.start()
        host, port = simulator.address
        mode = "TEST PLAINTEXT" if args.test_plaintext else "TLS 1.3 PSK"
        print(f"INERT SIMULATOR / NO PHYSICAL I/O / {mode} / {host}:{port}", flush=True)
        print(f"device_id={simulator.device_id}; profile={simulator.profile['profile_id']}", flush=True)
        await asyncio.Event().wait()
    finally:
        await simulator.close()


def main():
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
