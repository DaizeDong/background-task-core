"""Explicit contained probe: no task folder/query/registration or credential reads.

Run manually on Windows. Outputs only booleans and safe error codes. The DPAPI
plaintext is disposable nonsecret text and the COM task definition stays in memory.
"""
import json
from t12_runtime_support import example_request, reg, render
from task_console.runtime_windows import COMTransport, DPAPIProtector
from task_console.scheduler_windows import WindowsScheduler


def main():
    results = {}
    try:
        protector = DPAPIProtector()
        data = b'nonsecret synthetic SecureString roundtrip'
        results['dpapi_roundtrip'] = protector.unprotect(protector.protect(data)) == data
        spec = reg._compile({'request': example_request()})['task_specs'][0]
        spec['name'] = 'AcmeSyntheticUnregisteredDefinition'
        value = render(spec, {'state': 'absent'}, False)
        prepared = WindowsScheduler(COMTransport()).prepare(spec['name'], value, 'a' * 32 + ':0')
        results['unregistered_com_prepare'] = prepared['state'] == 'present'
    except reg.Conflict as exc:
        results['error'] = exc.to_dict()
    print(json.dumps(results, sort_keys=True))
    return 0 if results == {'dpapi_roundtrip': True, 'unregistered_com_prepare': True} else 1


if __name__ == '__main__':
    raise SystemExit(main())
