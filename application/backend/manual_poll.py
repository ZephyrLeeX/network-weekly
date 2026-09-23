"""Read-only, non-persistent manual device collection diagnostics.

This command deliberately calls the Wave 1 collection orchestrator directly.
It never enters the scheduler/persistence pipeline, creates no planned cycle,
and therefore cannot affect metrics, Coverage, reachability, interface state,
or IRF observation history.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence

from sqlalchemy.exc import SQLAlchemyError

from backend.collect.session import FAILED, DeviceCollectionOutcome, SectionResult, run_collection
from backend.config import ConfigError, Settings, load_settings
from backend.db.engine import get_session_factory
from backend.log import setup_logging
from backend.monitoring.credentials import build_contexts
from backend.monitoring.poll import CREDENTIALS_SECTION, DevicePollContext
from backend.secrets import SecretsError, load_secrets

_EXIT_SUCCESS = 0
_EXIT_COLLECTION_FAILURE = 1
_EXIT_USAGE_OR_CONFIG = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.manual_poll",
        description=(
            "Run real device collection without persisting a poll run, metrics, state, "
            "IRF observations, or Coverage."
        ),
        epilog="Exit 0: all SUCCESS; 1: any PARTIAL/FAILED; 2: usage/configuration error.",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--device",
        action="append",
        metavar="NAME",
        help="enabled device name; repeat to select multiple devices",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="collect every enabled device in stable order",
    )
    return parser


def _select_contexts(
    contexts: Sequence[DevicePollContext], device_names: Sequence[str] | None
) -> list[DevicePollContext]:
    ordered = sorted(contexts, key=lambda item: (item.device_name, item.device_id))
    if device_names is None:
        if not ordered:
            raise ValueError("no enabled devices found")
        return ordered

    requested = {name.strip() for name in device_names if name.strip()}
    if len(requested) != len(set(device_names)) or len(requested) != len(device_names):
        raise ValueError("device names must be non-empty and may not be repeated")
    available = {context.device_name for context in ordered}
    missing = sorted(requested - available)
    if missing:
        raise ValueError("device is not enabled or does not exist: " + ", ".join(missing))
    return [context for context in ordered if context.device_name in requested]


def _unavailable_outcome(context: DevicePollContext) -> DeviceCollectionOutcome:
    outcome = DeviceCollectionOutcome(device_name=context.device_name)
    outcome.sections.append(
        SectionResult(
            name=CREDENTIALS_SECTION,
            status=FAILED,
            error="SNMP credentials unavailable",
        )
    )
    return outcome


def _render_summary(outcome: DeviceCollectionOutcome) -> str:
    identity = outcome.identity
    ssh_reachable = (
        "unknown" if outcome.ssh_reachable is None else str(outcome.ssh_reachable).lower()
    )
    failed_sections = ", ".join(outcome.failed_sections) or "none"
    lines = [
        f"DEVICE: {outcome.device_name}",
        f"STATUS: {outcome.overall_status}",
        f"FAILED_SECTIONS: {failed_sections}",
        f"DEADLINE_EXCEEDED: {str(outcome.deadline_exceeded).lower()}",
        f"SSH_REACHABLE: {ssh_reachable}",
        "IDENTITY:",
        f"  sys_name: {identity.sys_name if identity is not None else 'unavailable'}",
        f"  sys_object_id: {identity.sys_object_id if identity is not None else 'unavailable'}",
        f"  uptime: {identity.uptime_seconds if identity is not None else 'unavailable'}",
        f"CPU_SAMPLES: {len(outcome.cpu or ())}",
        f"MEMORY_SAMPLES: {len(outcome.memory or ())}",
        f"INTERFACES: {len(outcome.interfaces or ())}",
        f"AGGREGATIONS: {len(outcome.aggregations or ())}",
    ]
    return "\n".join(lines)


def run_diagnostics(
    contexts: Sequence[DevicePollContext],
    settings: Settings,
    *,
    collect: Callable[..., DeviceCollectionOutcome] = run_collection,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Collect selected devices serially with the production deadline/stagger."""

    all_success = True
    for index, context in enumerate(contexts):
        if index:
            sleeper(settings.poll_stagger_seconds)
        if context.snmp is None:
            outcome = _unavailable_outcome(context)
            print(f"ERROR: {context.device_name}: SNMP credentials unavailable", file=sys.stderr)
        else:
            try:
                outcome = collect(
                    context.snmp,
                    context.ssh,
                    context.device_name,
                    deadline_seconds=settings.poll_deadline_seconds,
                )
            except Exception as exc:  # noqa: BLE001 (class only; exception may carry secrets)
                outcome = DeviceCollectionOutcome(device_name=context.device_name)
                outcome.sections.append(
                    SectionResult(name="runtime", status=FAILED, error=type(exc).__name__)
                )
                print(
                    f"ERROR: {context.device_name}: collection failed ({type(exc).__name__})",
                    file=sys.stderr,
                )
        print(_render_summary(outcome))
        if index != len(contexts) - 1:
            print()
        all_success = all_success and outcome.overall_status == "SUCCESS"
    return _EXIT_SUCCESS if all_success else _EXIT_COLLECTION_FAILURE


def main(
    argv: Sequence[str] | None = None,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    collect: Callable[..., DeviceCollectionOutcome] = run_collection,
) -> int:
    """CLI entry point for the non-persistent manual diagnostic poll."""

    args = _parser().parse_args(argv)
    try:
        settings = load_settings()
        setup_logging(settings)
        secrets = load_secrets(settings.secrets_file)
        session_factory = get_session_factory()
        with session_factory() as session:
            contexts = build_contexts(session, secrets)
        selected = _select_contexts(contexts, args.device if not args.all else None)
    except (ConfigError, SecretsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return _EXIT_USAGE_OR_CONFIG
    except SQLAlchemyError as exc:
        print(f"ERROR: database unavailable ({type(exc).__name__})", file=sys.stderr)
        return _EXIT_USAGE_OR_CONFIG
    return run_diagnostics(selected, settings, collect=collect, sleeper=sleeper)


if __name__ == "__main__":
    raise SystemExit(main())
