"""JSON task planning and authority-controlled actions. Inputs remain private."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .compiler import plan
from .contracts import ContractError


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ContractError("invalid_arguments", "arguments")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_json_key", "input")
        result[key] = value
    return result


def _read(path: str):
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, object_pairs_hook=_unique_keys)
    except OSError as exc:
        raise ContractError("unreadable_input", "input_file") from exc


def main(argv: list[str] | None = None, *, runtime=None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("command", choices=["plan", "registration-plan", "runtime-status", "apply", "retire", "recover", "control", "retire-plan", "export", "restore-plan", "restore", "restore-recover", "adoption-plan", "adoption-apply", "adoption-resume"])
    parser.add_argument('--source-binding')
    parser.add_argument('--review-only', action='store_true')
    parser.add_argument('--approval-revision')
    parser.add_argument('--name')
    parser.add_argument('--verb', choices=['enable', 'disable', 'run', 'stop', 'retire'])
    parser.add_argument("--request", help="JSON request file; defaults to stdin")
    parser.add_argument("--component", action="append", help="Explicit .console.json; repeat for each component")
    parser.add_argument("--bindings")
    parser.add_argument("--machine")
    parser.add_argument("--baseline")
    parser.add_argument("--expected-revision")
    parser.add_argument("--task-id")
    parser.add_argument("--reason")
    parser.add_argument("--transaction-id")
    parser.add_argument('--runtime-config')
    parser.add_argument('--private-root')
    parser.add_argument('--state-root')
    parser.add_argument('--vault-root')
    parser.add_argument('--operation', choices=['apply', 'enable', 'disable', 'retire'], default='apply')
    parser.add_argument('--migrate', action='store_true')
    parser.add_argument('--approve-enable', action='store_true')
    args = None
    def failure(exc):
        from .controller import error_result
        return error_result(exc, name=getattr(args, 'name', '') or '', verb=getattr(args, 'verb', '') or '')
    try:
        args = parser.parse_args(argv)
        if (args.source_binding or args.review_only) and not args.command.startswith('adoption-'):
            raise ContractError('invalid_arguments', 'adoption')
        configuration = (args.runtime_config, args.private_root, args.state_root, args.vault_root)
        if any(configuration) and (runtime is not None or args.command == 'plan'):
            raise ContractError('invalid_runtime_arguments', 'runtime')
        if args.command != "plan":
            from . import registration
            from . import controller
            if runtime is None:
                runtime = controller.load_runtime(runtime_config=args.runtime_config, private_root=args.private_root,
                                                 state_root=args.state_root, vault_root=args.vault_root,
                                                 for_recovery=args.command in ('recover', 'restore-recover', 'adoption-plan', 'adoption-apply', 'adoption-resume', 'runtime-status'))
            if any((args.component, args.bindings, args.machine, args.baseline)):
                raise ContractError("invalid_arguments", "mutation")
            if args.command.startswith('adoption-'):
                from . import adoption
                if any((args.task_id, args.name, args.verb, args.migrate, args.approve_enable, args.expected_revision)):
                    raise ContractError('invalid_arguments', 'adoption')
                if args.command == 'adoption-plan':
                    if not args.source_binding or args.request or args.transaction_id or args.approval_revision:
                        raise ContractError('invalid_arguments', 'adoption_plan')
                    result = adoption.build_plan(runtime, source_binding=args.source_binding, review_only=args.review_only)
                elif args.command == 'adoption-resume':
                    if not args.transaction_id or not args.approval_revision or args.request or args.source_binding or args.review_only:
                        raise ContractError('invalid_arguments', 'adoption_resume')
                    result = adoption.resume(args.transaction_id, args.approval_revision, runtime=runtime)
                else:
                    if not args.approval_revision or args.source_binding or args.review_only or args.transaction_id:
                        raise ContractError('invalid_arguments', 'adoption_apply')
                    payload = _read(args.request) if args.request else json.load(sys.stdin, object_pairs_hook=_unique_keys)
                    result = adoption.apply(payload, args.approval_revision, runtime=runtime)
                print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
                return 0 if args.command == 'adoption-plan' or result.get('ok') else 3
            if args.command in ('export', 'restore-plan', 'restore', 'restore-recover'):
                selected = controller.Controller(runtime)
                if args.command == 'export':
                    if args.request or args.task_id or args.approval_revision:
                        raise ContractError('invalid_arguments', 'export')
                    result = selected.export_tasks()
                elif args.command == 'restore-recover':
                    result = selected.restore_recover(args.transaction_id, args.approval_revision)
                else:
                    payload = _read(args.request) if args.request else json.load(sys.stdin, object_pairs_hook=_unique_keys)
                    if args.command == 'restore-plan':
                        if not isinstance(payload, dict) or set(payload) - {'archive', 'task_ids', 'rebinding'}:
                            raise ContractError('invalid_restore_request', 'input')
                        result = selected.restore_plan(payload.get('archive'), payload.get('task_ids'), rebinding=payload.get('rebinding'))
                    else:
                        result = selected.restore(payload, args.approval_revision)
                print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
                return 0 if args.command == 'restore-plan' or result.get('ok') else 3
            if args.command in ('control', 'retire-plan'):
                if args.command == 'control' and not args.name:
                    payload = _read(args.request) if args.request else json.load(sys.stdin, object_pairs_hook=_unique_keys)
                    if not isinstance(payload, dict) or set(payload) - {'name', 'verb', 'reason'}:
                        raise ContractError('invalid_control_request', 'input')
                    args.name, args.verb, args.reason = payload.get('name'), payload.get('verb'), payload.get('reason', '')
                elif args.request:
                    raise ContractError('invalid_arguments', 'control_arguments')
                if not args.name or (args.command == 'control' and not args.verb):
                    raise ContractError('missing_field', 'control_arguments')
                selected = controller.Controller(runtime)
                result = (selected.action(args.name, args.verb, reason=args.reason or '') if args.command == 'control'
                          else selected.retire_plan(args.name, args.reason or ''))
                print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
                return controller.exit_code(result) if args.command == 'control' else 0
            if args.command == 'runtime-status':
                selected = registration._use(runtime)
                # Enumerate the journal owner first: before the initial receipt
                # exists the ordinary reader has nothing to read, and the pending
                # bootstrap transaction is the only public recovery handle.
                pending = selected.journal.pending(['authority'])
                bootstrap = [record['transaction_id'] for record in selected.journal.list()
                             if isinstance(record.get('bootstrap'), dict)
                             and record['transaction_id'] in pending]
                authority = getattr(selected, 'authority', None)
                result = {'schemaVersion': 1, 'pending': pending, 'pending_bootstrap': bootstrap,
                          'action_required': 'adoption-resume' if bootstrap else None}
                try:
                    bundle = selected.load()
                    registration._compile(bundle)
                except ContractError as exc:
                    published = authority is not None and (authority.config.adoption.exists()
                                                           or authority.pointer.exists())
                    result.update(authority_epoch=None, generation=None, authority=exc.code,
                                  initialized=published)
                else:
                    result.update(authority_epoch=bundle['request']['machine']['authority_epoch'],
                                  generation=bundle.get('authority_generation'),
                                  authority='available', initialized=True)
                print(json.dumps(result, ensure_ascii=True, sort_keys=True))
                # Unreadable authority still reports; a pending bootstrap explains it.
                return 0 if result['authority'] == 'available' or bootstrap else 2
            if args.command == 'registration-plan':
                if not args.task_id:
                    raise ContractError('missing_field', 'task_id')
                result = registration.build_plan(runtime, [args.task_id], operation=args.operation,
                          migrate=args.migrate, approve_enable=args.approve_enable, reason=args.reason or '')
                print(json.dumps(result, ensure_ascii=True, sort_keys=True))
                return 0
            if args.command == "recover":
                if not args.transaction_id:
                    raise ContractError("missing_field", "transaction_id")
                result = registration.recover(args.transaction_id, runtime=runtime)
            elif args.command == "retire":
                if not all((args.task_id, args.reason, args.expected_revision)):
                    raise ContractError("missing_field", "retire_arguments")
                result = registration.retire(args.task_id, args.expected_revision,
                                             reason=args.reason, runtime=runtime)
            else:
                if not args.expected_revision:
                    raise ContractError("missing_field", "expected_revision")
                request = _read(args.request) if args.request else json.load(sys.stdin, object_pairs_hook=_unique_keys)
                result = registration.apply(request, args.expected_revision, runtime=runtime)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
            successful = result["status"] == "committed" or (args.command == "recover" and result["status"] == "rolled_back")
            return 0 if successful and result.get("cleaned") else 3
        separate = (args.component, args.bindings, args.machine, args.baseline)
        if any(separate):
            if args.request or not all(separate):
                raise ContractError("invalid_arguments", "input_files")
            request = {"schemaVersion": 1, "components": [_read(path) for path in args.component],
                       "bindings": _read(args.bindings), "machine": _read(args.machine), "baseline": _read(args.baseline)}
        else:
            request = _read(args.request) if args.request else json.load(sys.stdin, object_pairs_hook=_unique_keys)
        result = plan(request)
    except (json.JSONDecodeError, UnicodeError):
        result = failure(ContractError('invalid_json', 'input'))
    except ContractError as exc:
        result = failure(exc)
    except Exception:
        # Never print transport/DPAPI/XML or child output through an exception.
        result = failure(RuntimeError())
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 2 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
