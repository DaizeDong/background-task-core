"""Read-only task backup check through the existing controller export owner."""
import json

from .__main__ import _Parser
from .controller import load_runtime
from .contracts import ContractError
from .export_restore import check_backup


def main(argv=None, *, runtime=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--backup-directory', required=True)
    try:
        args = parser.parse_args(argv)
        result = check_backup(load_runtime() if runtime is None else runtime, args.backup_directory)
    except Exception as exc:
        # Codes are fixed owner vocabulary. Never emit exception text, XML,
        # task names, private paths, child output or failed snapshot contents.
        result = {'schemaVersion': 1, 'operation': 'backup-check', 'ok': False,
                  'read_only': True, 'status': 'FAILED',
                  'error': {'code': exc.code if isinstance(exc, ContractError) else 'backup_check_failed'}}
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
