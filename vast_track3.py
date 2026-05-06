#!/usr/bin/env python3
"""Small Vast.ai cockpit for modded-nanogpt Track 3 experiments."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
TRACK3_REPO = Path("/code/modded-nanogpt")
DEFAULT_MANIFEST_DIR = TRACK3_REPO / ".codex_logs" / "vast"
DEFAULT_IMAGE = "nvidia/cuda:12.6.2-cudnn-devel-ubuntu24.04"
DEFAULT_REPO_URL = "https://github.com/KellerJordan/modded-nanogpt.git"

GPU_ALIASES = {
    "h100": ["H100_SXM", "H100_PCIE", "H100_NVL"],
    "h100-sxm": ["H100_SXM"],
    "h100_sxm": ["H100_SXM"],
    "h100-pcie": ["H100_PCIE"],
    "h100_pcie": ["H100_PCIE"],
    "h100-nvl": ["H100_NVL"],
    "h100_nvl": ["H100_NVL"],
    "h200": ["H200", "H200_NVL"],
    "a100": ["A100_SXM4", "A100_PCIE", "A100X", "A800_PCIE"],
    "a100-sxm4": ["A100_SXM4"],
    "a100_sxm4": ["A100_SXM4"],
    "a100-pcie": ["A100_PCIE"],
    "a100_pcie": ["A100_PCIE"],
    "3090": ["RTX_3090"],
    "rtx3090": ["RTX_3090"],
    "rtx-3090": ["RTX_3090"],
    "rtx_3090": ["RTX_3090"],
    "4090": ["RTX_4090"],
    "rtx4090": ["RTX_4090"],
    "rtx-4090": ["RTX_4090"],
    "rtx_4090": ["RTX_4090"],
}


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def vast_env() -> dict[str, str]:
    env = os.environ.copy()
    key = env.get("VAST_API_KEY") or env.get("VAST_AI_KEY")
    for env_file in (Path.cwd() / ".env", TRACK3_REPO / ".env", SCRIPT_DIR / ".env"):
        values = parse_dotenv(env_file)
        key = key or values.get("VAST_API_KEY") or values.get("VAST_AI_KEY")
    if key and not env.get("VAST_API_KEY"):
        env["VAST_API_KEY"] = key
    return env


def vast_base_cmd() -> list[str]:
    override = os.environ.get("VASTAI_BIN")
    if override:
        return shlex.split(override)
    if found := shutil.which("vastai"):
        return [found]
    if shutil.which("uvx"):
        return ["uvx", "--from", "vastai", "vastai"]
    raise SystemExit("Could not find vastai or uvx. Install uv or set VASTAI_BIN.")


def run_vast(args: list[str], *, raw: bool = False, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    cmd = vast_base_cmd()
    if raw:
        cmd.append("--raw")
    cmd.extend(args)
    return subprocess.run(
        cmd,
        check=check,
        env=vast_env(),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def vast_json(args: list[str]) -> object:
    result = run_vast(args, raw=True, capture=True)
    stdout = result.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(f"Vast.ai did not return JSON for: {shlex.join(['vastai', *args])}\n{stdout}") from exc


def bool_token(value: str) -> str:
    return {"true": "true", "false": "false", "any": "any"}[value]


def gpu_query(gpu: str) -> str:
    names = GPU_ALIASES.get(gpu.lower(), [gpu.replace(" ", "_")])
    if len(names) == 1:
        return f"gpu_name={names[0]}"
    return "gpu_name in [" + ",".join(names) + "]"


def offer_price(offer: dict) -> float | None:
    for key in ("dph_total", "dph", "discounted_dph_total"):
        value = offer.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    search = offer.get("search")
    if isinstance(search, dict):
        value = search.get("totalHour") or search.get("discountedTotalPerHour")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def gb_from_vast_ram(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    gb = float(value)
    if gb > 1024:
        gb /= 1024
    return f"{gb:.1f}"


def shorten(value: object, width: int) -> str:
    text = "-" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[: width - 1] + "."


def print_table(rows: list[dict], columns: list[tuple[str, str, int]]) -> None:
    if not rows:
        print("No rows.")
        return
    header = "  ".join(label.ljust(width) for _, label, width in columns)
    print(header)
    print("  ".join("-" * width for _, _, width in columns))
    for row in rows:
        print("  ".join(shorten(row.get(key), width).ljust(width) for key, _, width in columns))


def compact_offer_rows(offers: list[dict]) -> list[dict]:
    rows = []
    for offer in offers:
        gpus = offer.get("num_gpus")
        price = offer_price(offer)
        per_gpu = price / gpus if isinstance(gpus, int) and gpus and price is not None else None
        rel = offer.get("reliability") or offer.get("reliability2")
        rows.append(
            {
                "id": offer.get("id") or offer.get("ask_contract_id"),
                "dph": f"{price:.4f}" if price is not None else "-",
                "gpu_hr": f"{per_gpu:.4f}" if per_gpu is not None else "-",
                "gpu": offer.get("gpu_name"),
                "n": gpus,
                "vram": gb_from_vast_ram(offer.get("gpu_ram")),
                "rel": f"{rel:.4f}" if isinstance(rel, (int, float)) else "-",
                "ver": offer.get("verification") or offer.get("verified"),
                "loc": offer.get("geolocation"),
                "cuda": offer.get("cuda_max_good") or offer.get("cuda_vers"),
                "drv": offer.get("driver_version") or offer.get("driver_vers"),
                "disk": offer.get("disk_space"),
                "inet": f"{offer.get('inet_down', '-')}/{offer.get('inet_up', '-')}",
                "dir": offer.get("direct_port_count"),
                "machine": offer.get("machine_id"),
            }
        )
    return rows


def build_search_query(args: argparse.Namespace) -> str:
    parts = [
        gpu_query(args.gpu),
        f"num_gpus={args.gpus}",
        "rentable=true",
    ]
    if args.verified != "any":
        parts.append(f"verified={bool_token(args.verified)}")
    if args.rented != "any":
        parts.append(f"rented={bool_token(args.rented)}")
    if args.min_reliability is not None:
        parts.append(f"reliability>={args.min_reliability}")
    if args.cuda_min is not None:
        parts.append(f"cuda_vers>={args.cuda_min}")
    if args.min_gpu_ram is not None:
        parts.append(f"gpu_ram>={args.min_gpu_ram}")
    if args.min_disk_space is not None:
        parts.append(f"disk_space>={args.min_disk_space}")
    if args.require_direct_port:
        parts.append("direct_port_count>=1")
    if args.extra_query:
        parts.append(args.extra_query)
    return " ".join(parts)


def cmd_search(args: argparse.Namespace) -> int:
    query = build_search_query(args)
    vast_args = [
        "search",
        "offers",
        query,
        "-n",
        "--limit",
        str(args.limit),
        "--storage",
        str(args.storage),
        "-o",
        args.order,
        "-t",
        args.pricing,
    ]
    offers = vast_json(vast_args)
    if not isinstance(offers, list):
        raise SystemExit(f"Unexpected search result: {offers!r}")
    if args.json:
        print(json.dumps(offers, indent=2, sort_keys=True))
        return 0
    print(f"query: {query}")
    print_table(
        compact_offer_rows(offers),
        [
            ("id", "offer", 10),
            ("dph", "$/hr", 8),
            ("gpu_hr", "$/gpu", 8),
            ("gpu", "gpu", 12),
            ("n", "n", 3),
            ("vram", "vram", 6),
            ("rel", "rel", 7),
            ("ver", "verified", 10),
            ("loc", "location", 18),
            ("cuda", "cuda", 6),
            ("drv", "driver", 10),
            ("disk", "disk", 7),
            ("inet", "down/up", 15),
            ("dir", "dir", 4),
            ("machine", "machine", 9),
        ],
    )
    if offers:
        offer_id = offers[0].get("id") or offers[0].get("ask_contract_id")
        print()
        print("cheapest rent command:")
        print(
            shlex.join(
                [
                    str(Path(__file__).resolve()),
                    "rent",
                    str(offer_id),
                    "--label",
                    f"track3-{args.gpus}x-{args.gpu}",
                ]
            )
        )
    return 0


def compact_instance_rows(instances: list[dict]) -> list[dict]:
    rows = []
    for instance in instances:
        price = offer_price(instance)
        rows.append(
            {
                "id": instance.get("id"),
                "status": instance.get("actual_status") or instance.get("status"),
                "label": instance.get("label"),
                "dph": f"{price:.4f}" if price is not None else "-",
                "gpu": instance.get("gpu_name"),
                "n": instance.get("num_gpus"),
                "host": instance.get("ssh_host") or instance.get("public_ipaddr"),
                "port": instance.get("ssh_port"),
                "image": instance.get("image_uuid") or instance.get("image_args") or instance.get("image"),
                "machine": instance.get("machine_id"),
            }
        )
    return rows


def cmd_instances(args: argparse.Namespace) -> int:
    result = vast_json(["show", "instances"])
    instances = result if isinstance(result, list) else result.get("instances", []) if isinstance(result, dict) else []
    if args.json:
        print(json.dumps(instances, indent=2, sort_keys=True))
        return 0
    print_table(
        compact_instance_rows(instances),
        [
            ("id", "id", 10),
            ("status", "status", 12),
            ("label", "label", 20),
            ("dph", "$/hr", 8),
            ("gpu", "gpu", 12),
            ("n", "n", 3),
            ("host", "host", 18),
            ("port", "port", 7),
            ("image", "image", 24),
            ("machine", "machine", 9),
        ],
    )
    return 0


def lookup_offer(offer_id: int) -> dict | None:
    result = vast_json(
        [
            "search",
            "offers",
            f"id={offer_id} rented=any rentable=any verified=any",
            "-n",
            "--limit",
            "1",
            "-o",
            "dph",
        ]
    )
    if isinstance(result, list) and result:
        return result[0]
    return None


def write_manifest(args: argparse.Namespace, payload: dict) -> Path:
    manifest_dir = Path(args.manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    kind = payload.get("kind", "vast")
    instance_id = payload.get("instance_id") or payload.get("result", {}).get("new_contract") or "unknown"
    path = manifest_dir / f"{stamp}_{kind}_{instance_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def track3_onstart_script(args: argparse.Namespace) -> str:
    run_cmd = (
        "torchrun --standalone --nproc_per_node=$(nvidia-smi -L | wc -l) "
        "records/track_3_optimization/train_gpt_simple.py"
    )
    lines = [
        "#!/usr/bin/env bash",
        "set -euxo pipefail",
        "mkdir -p /workspace",
        "exec > >(tee -a /workspace/track3_onstart.log) 2>&1",
        "apt-get update",
        (
            "apt-get install -y --no-install-recommends "
            "ca-certificates curl git python3 python3-venv python3-pip"
        ),
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        'export PATH="$HOME/.local/bin:$PATH"',
        "rm -rf /workspace/modded-nanogpt",
        f"git clone --branch {shlex.quote(args.repo_branch)} {shlex.quote(args.repo_url)} /workspace/modded-nanogpt",
        "cd /workspace/modded-nanogpt",
        "uv venv --python 3.12 || uv venv",
        ". .venv/bin/activate",
        f"uv pip install torch=={shlex.quote(args.torch_version)} huggingface_hub",
        f"python data/cached_fineweb10B.py {int(args.fineweb_chunks)}",
    ]
    if args.run_after_setup:
        lines.extend(
            [
                "mkdir -p /workspace/modded-nanogpt/logs",
                f"nohup bash -lc {shlex.quote(run_cmd)} > /workspace/modded-nanogpt/logs/track3_vast.log 2>&1 &",
                "echo started track3 run: /workspace/modded-nanogpt/logs/track3_vast.log",
            ]
        )
    return "\n".join(lines) + "\n"


def cmd_onstart(args: argparse.Namespace) -> int:
    print(track3_onstart_script(args), end="")
    return 0


def cmd_rent(args: argparse.Namespace) -> int:
    onstart_cmd = args.onstart_cmd
    if args.onstart_file:
        onstart_cmd = Path(args.onstart_file).read_text()
    if args.track3_onstart:
        onstart_cmd = track3_onstart_script(args)

    offer = lookup_offer(args.offer_id)
    vast_args = ["create", "instance", str(args.offer_id)]
    if args.template_hash:
        vast_args.extend(["--template_hash", args.template_hash])
    else:
        vast_args.extend(["--image", args.image])
    vast_args.extend(["--disk", str(args.disk), "--ssh", "--label", args.label, "--cancel-unavail"])
    if args.direct:
        vast_args.append("--direct")
    if args.env:
        vast_args.extend(["--env", args.env])
    if onstart_cmd:
        vast_args.extend(["--onstart-cmd", onstart_cmd])

    printable = shlex.join([*vast_base_cmd(), "--raw", *vast_args])
    if args.dry_run:
        print(printable)
        return 0

    result = vast_json(vast_args)
    instance_id = result.get("new_contract") if isinstance(result, dict) else None
    manifest_payload = {
        "kind": "rent",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "offer_id": args.offer_id,
        "instance_id": instance_id,
        "label": args.label,
        "image": args.image if not args.template_hash else None,
        "template_hash": args.template_hash,
        "disk": args.disk,
        "direct": args.direct,
        "track3_onstart": args.track3_onstart,
        "offer": offer,
        "result": result,
    }
    manifest = write_manifest(args, manifest_payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"manifest: {manifest}")
    if instance_id:
        print("ssh command:")
        print(shlex.join([str(Path(__file__).resolve()), "ssh", str(instance_id)]))
        print("destroy command:")
        print(shlex.join([str(Path(__file__).resolve()), "destroy", str(instance_id), "--yes"]))
    return 0


def ssh_parts_from_url(text: str) -> list[str]:
    target = text.strip().splitlines()[-1].strip()
    parsed = urlparse(target)
    if parsed.scheme == "ssh" and parsed.hostname:
        user = parsed.username or "root"
        cmd = ["ssh"]
        if parsed.port:
            cmd.extend(["-p", str(parsed.port)])
        cmd.append(f"{user}@{parsed.hostname}")
        return cmd
    if target.startswith("ssh "):
        return shlex.split(target)
    return ["ssh", target]


def get_ssh_parts(instance_id: int) -> list[str]:
    result = run_vast(["ssh-url", str(instance_id)], capture=True)
    return ssh_parts_from_url(result.stdout)


def cmd_ssh(args: argparse.Namespace) -> int:
    cmd = get_ssh_parts(args.instance_id)
    if args.identity:
        cmd[1:1] = ["-i", args.identity]
    if args.command:
        remote_command = args.command[1:] if args.command[0] == "--" else args.command
        cmd.append(" ".join(remote_command))
    if args.exec:
        return subprocess.run(cmd).returncode
    print(shlex.join(cmd))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    vast_args = ["logs", str(args.instance_id), "--tail", str(args.tail)]
    if args.filter:
        vast_args.extend(["--filter", args.filter])
    if args.daemon:
        vast_args.append("--daemon-logs")
    return run_vast(vast_args, capture=False, check=False).returncode


def cmd_destroy(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to destroy without --yes. This deletes the instance and its local disk.")
        print(shlex.join([str(Path(__file__).resolve()), "destroy", str(args.instance_id), "--yes"]))
        return 2
    result = vast_json(["destroy", "instance", str(args.instance_id), "--yes"])
    manifest = write_manifest(
        args,
        {
            "kind": "destroy",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "instance_id": args.instance_id,
            "result": result,
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"manifest: {manifest}")
    return 0


def add_onstart_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--repo-branch", default="master")
    parser.add_argument("--torch-version", default="2.11")
    parser.add_argument("--fineweb-chunks", type=int, default=40)
    parser.add_argument("--run-after-setup", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-dir", default=str(DEFAULT_MANIFEST_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search and rank Vast offers.")
    search.add_argument("--gpu", default="h100", help="GPU alias or Vast gpu_name, default: h100")
    search.add_argument("--gpus", type=int, default=4)
    search.add_argument("--verified", choices=["true", "false", "any"], default="false")
    search.add_argument("--rented", choices=["true", "false", "any"], default="false")
    search.add_argument("--min-reliability", type=float, default=0.98)
    search.add_argument("--cuda-min", type=float, default=12.1)
    search.add_argument("--min-gpu-ram", type=float)
    search.add_argument("--min-disk-space", type=float)
    search.add_argument("--require-direct-port", action="store_true")
    search.add_argument("--storage", type=float, default=120)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--pricing", choices=["on-demand", "reserved", "bid"], default="on-demand")
    search.add_argument("--order", default="dph")
    search.add_argument("--extra-query")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)

    rent = subparsers.add_parser("rent", help="Rent a specific offer id.")
    rent.add_argument("offer_id", type=int)
    rent.add_argument("--image", default=DEFAULT_IMAGE)
    rent.add_argument("--template-hash")
    rent.add_argument("--disk", type=float, default=120)
    rent.add_argument("--label", default="track3-vast")
    rent.add_argument("--direct", action="store_true")
    rent.add_argument("--env")
    rent.add_argument("--onstart-cmd")
    rent.add_argument("--onstart-file")
    rent.add_argument("--track3-onstart", action="store_true")
    rent.add_argument("--dry-run", action="store_true")
    add_onstart_args(rent)
    rent.set_defaults(func=cmd_rent)

    onstart = subparsers.add_parser("track3-onstart", help="Print the optional Track 3 setup onstart script.")
    add_onstart_args(onstart)
    onstart.set_defaults(func=cmd_onstart)

    instances = subparsers.add_parser("instances", help="Show current instances.")
    instances.add_argument("--json", action="store_true")
    instances.set_defaults(func=cmd_instances)

    ssh = subparsers.add_parser("ssh", help="Print or execute an ssh command for an instance.")
    ssh.add_argument("instance_id", type=int)
    ssh.add_argument("-i", "--identity")
    ssh.add_argument("--exec", action="store_true", help="Run ssh instead of only printing it.")
    ssh.add_argument("command", nargs=argparse.REMAINDER)
    ssh.set_defaults(func=cmd_ssh)

    logs = subparsers.add_parser("logs", help="Show Vast container logs.")
    logs.add_argument("instance_id", type=int)
    logs.add_argument("--tail", type=int, default=200)
    logs.add_argument("--filter")
    logs.add_argument("--daemon", action="store_true")
    logs.set_defaults(func=cmd_logs)

    destroy = subparsers.add_parser("destroy", help="Destroy an instance and write a manifest.")
    destroy.add_argument("instance_id", type=int)
    destroy.add_argument("--yes", action="store_true")
    destroy.set_defaults(func=cmd_destroy)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
