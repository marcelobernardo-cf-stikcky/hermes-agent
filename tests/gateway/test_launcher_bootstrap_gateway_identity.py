"""A gateway started through ``runtime_command``'s ``python -I -c <bootstrap> gateway run`` is a live
gateway (runpy runs hermes_cli.main in-process); the restart watcher's ``python -c <src> ... gateway
run`` is not (#107002). Before this, the updater aborted with "Could not inspect gateway PID"."""
from pathlib import Path

from gateway import status
from hermes_cli._launchers import runtime_command


def _cmdline(args):
    return " ".join(runtime_command(Path("."), args, python="python.exe"))


def test_bootstrap_launched_gateway_run_is_a_gateway():
    assert status.looks_like_gateway_command_line(_cmdline(["gateway", "run", "--replace"]))


def test_bootstrap_launched_non_run_subcommands_are_not_gateways():
    assert not status.looks_like_gateway_command_line(_cmdline(["gateway", "status"]))
    assert not status.looks_like_gateway_command_line(_cmdline(["chat"]))


def test_restart_watcher_inline_source_is_still_not_a_gateway():
    watcher = 'python.exe -c "import sys,time; time.sleep(1)" 4242 python.exe -m hermes_cli.main gateway run'
    assert not status.looks_like_gateway_command_line(watcher)
