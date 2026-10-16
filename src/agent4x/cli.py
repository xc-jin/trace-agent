from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from agent4x.domain import DomainError
from agent4x.evidence import import_source
from agent4x.report import render_report
from agent4x.store import Store


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_constraints(values):
    result = {}
    for value in values:
        key, separator, content = value.partition("=")
        if not separator or key in result:
            raise DomainError("Use distinct --constraint key=value arguments")
        result[key] = content
    return result


def parser():
    root = argparse.ArgumentParser(description="TraceAgent：可追溯、可修订的技术决策助手")
    root.add_argument("--data-dir", type=Path, default=Path(".agent4x"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="离线演示，不使用 key、不允许网络")

    task = commands.add_parser("task").add_subparsers(dest="action", required=True)
    new = task.add_parser("new")
    new.add_argument("goal")
    new.add_argument("--project", required=True)
    new.add_argument("--constraint", action="append", default=[])
    show = task.add_parser("show")
    show.add_argument("task_id")
    amend = task.add_parser("amend")
    amend.add_argument("task_id")
    amend.add_argument("key")
    amend.add_argument("value")

    source = commands.add_parser("source").add_subparsers(dest="action", required=True).add_parser("add")
    source.add_argument("task_id")
    source.add_argument("relative_path")
    source.add_argument("--root", type=Path, required=True)

    run = commands.add_parser("run", help="调用真实 DeepSeek；任务上下文会发送至外部服务")
    run.add_argument("task_id")
    run.add_argument("--allow-search", action="store_true", help="允许真实 Tavily 请求及相关费用")
    run.add_argument("--max-model-calls", type=int, default=8)
    run.add_argument("--max-tool-calls", type=int, default=18)
    run.add_argument("--seconds", type=float, default=120)

    report = commands.add_parser("report")
    report.add_argument("task_id")
    report.add_argument("--output", type=Path)
    trace = commands.add_parser("trace")
    trace.add_argument("task_id")

    memory = commands.add_parser("memory").add_subparsers(dest="action", required=True)
    listing = memory.add_parser("list")
    listing.add_argument("--project", required=True)
    propose = memory.add_parser("propose")
    propose.add_argument("task_id")
    propose.add_argument("text")
    for action in ("confirm", "delete", "edit"):
        command = memory.add_parser(action)
        command.add_argument("memory_id")
        if action == "edit":
            command.add_argument("text")
    return root


def dispatch(arguments):
    store = Store(arguments.data_dir)
    if arguments.command == "demo":
        from agent4x.demo import run_demo

        print_json(run_demo(store))
    elif arguments.command == "task":
        if arguments.action == "new":
            print_json(store.new_task(arguments.goal, arguments.project, parse_constraints(arguments.constraint)))
        elif arguments.action == "show":
            print_json(store.snapshot(arguments.task_id))
        else:
            print_json(store.amend(arguments.task_id, arguments.key, arguments.value))
    elif arguments.command == "source":
        print_json(import_source(store, arguments.task_id, arguments.root, arguments.relative_path))
    elif arguments.command == "report":
        text = render_report(store, arguments.task_id)
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(text)
            print(f"Report saved: {arguments.output}")
        else:
            print(text)
    elif arguments.command == "trace":
        print_json(store.events(arguments.task_id))
    elif arguments.command == "memory":
        if arguments.action == "list":
            print_json(store.memories(arguments.project))
        elif arguments.action == "propose":
            print_json(store.propose_memory(arguments.task_id, arguments.text))
        else:
            print_json(store.memory_action(arguments.memory_id, arguments.action, getattr(arguments, "text", "")))
    else:
        from dotenv import load_dotenv
        from agent4x.agent import live_model, run_agent
        from agent4x.settings import Settings

        load_dotenv(Path.cwd() / ".env", override=False)
        settings = Settings.from_environment()
        search_client = None
        if arguments.allow_search:
            if not settings.tavily_key:
                raise DomainError("TAVILY_API_KEY is required only when --allow-search is enabled")
            from tavily import TavilyClient

            search_client = TavilyClient(api_key=settings.tavily_key)
        print_json(run_agent(
            store, arguments.task_id, live_model(settings), search_client=search_client,
            model_limit=arguments.max_model_calls, tool_limit=arguments.max_tool_calls, seconds=arguments.seconds,
        ))


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        dispatch(arguments)
        return 0
    except Exception as error:
        if isinstance(error, (DomainError, FileNotFoundError, UnicodeError, ValueError)):
            message = str(error)
            for name, value in os.environ.items():
                if name.endswith(("API_KEY", "TOKEN", "SECRET")) and len(value) >= 4:
                    message = message.replace(value, "[REDACTED]")
            print(f"Error: {message}", file=sys.stderr)
        else:
            print(f"Run failed ({type(error).__name__}); inspect local trace, credentials and limits. No success claimed.", file=sys.stderr)
        return 1
