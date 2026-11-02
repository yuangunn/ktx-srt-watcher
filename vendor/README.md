# vendor/

## pykorail-0.2.2-py3-none-any.whl

The 코레일+ client the watcher is built on (`worker/adapters/korail.py`).
MIT licensed; the license text ships inside the wheel
(`pykorail-0.2.2.dist-info/licenses/LICENSE`).

**Why it lives here.** The `pykorail` project was deleted from PyPI on
2026-11-02 around 19:05 KST (`https://pypi.org/simple/pykorail/` → 404). The
home runner reinstalls dependencies on every poll, so from that tick on every
run failed at `pip install` with `No matching distribution found for
pykorail==0.2.2`. `requirements.txt` now installs this file by path, so the
install no longer depends on PyPI, and a stranger re-registering the freed
name on PyPI cannot get a build installed here.

**Provenance.** This is the exact file PyPI served, recovered from two
independent PyPI mirrors that had not yet dropped it:

| | |
|---|---|
| sha256 | `be639a15b65006dd805ae16905db776b40404ebb2062e8387b7a03cb79111d4c` |
| blake2b-256 | `2141e34f069501b4f4402ad40b92a2f688ab9929590d259be6812beb4bb5a58c` |
| Sources | `mirror.kakao.com/pypi`, `mirrors.aliyun.com/pypi` (byte-identical) |

Both mirrors' simple index lists the sha256 above (copied from PyPI's own
metadata), and the blake2b matches PyPI's storage path
`packages/21/41/e34f0695…/pykorail-0.2.2-py3-none-any.whl`.
Check with `sha256sum vendor/pykorail-0.2.2-py3-none-any.whl`.

**Replacing it.** There is no upstream to upgrade from any more, and
Dependabot cannot see this pin. If 코레일+ changes and 0.2.2 stops working (a
"감시 중단" alert), a newer build must be found and verified the same way:
the same sha256 from more than one source. Then swap the file, update the
path in `requirements.txt` and the hashes above, and dispatch `api-probe` on
`korail-home` before merging.
