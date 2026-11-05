# pykorail 직접 고치기

`pykorail`은 2026-11-02 PyPI와 GitHub에서 함께 삭제됐습니다. 코레일이 앱을
바꿔 감시가 막혀도 기다릴 업스트림이 없으니, `vendor/`의 휠을 **우리가 직접
고쳐서** 다시 묶습니다. 이 문서는 그 절차입니다. 출처·해시·라이선스는
[`vendor/README.md`](../vendor/README.md).

도구는 두 개입니다.

| | 하는 일 |
|---|---|
| `scripts/pykorail_wheel.py` | 휠 풀기 → 고치기 → 다시 묶기 (표준 라이브러리만, 어디서나 실행) |
| `api-probe` 워크플로 | 집 러너에서 실제 로그인·조회. 앱 버전 후보값을 **휠 없이** 시험하는 입력 포함 |

## 1. 언제 보나

- 텔레그램 **"🚨 코레일 검색 실패 — 감시 중단"** 또는 **"로그인 실패 — 감시 중단"**.
  알림의 `사유:` 줄에 코레일이 돌려준 메시지가 그대로 있습니다.
- 앱 스토어에 코레일톡(코레일+) 새 버전이 나온 직후. 막히기 전에 미리 확인해 볼 때.

## 2. 먼저 진단 — api-probe

GitHub → Actions → **api-probe** → Run workflow
(`run_probe` 체크, `runner`는 `korail-home`, 나머지는 비움).
코레일+는 데이터센터 IP를 막아서 `ubuntu-latest`로는 진단이 안 됩니다.

출력 첫 줄에 지금 보내는 앱 신원이 찍힙니다.

```
python 3.14.4 | pykorail 0.6.1 | app 7.1.0 / Version 261028001
login OK
search 서울→부산: 31 trains (12 with seats), types=[...]
```

## 3. 증상 → 원인

코레일+ 차단은 여러 겹입니다. 어느 겹에 걸렸는지가 메시지로 드러납니다.

| 증상 (`사유:` 또는 probe 출력) | 걸린 겹 | 고칠 곳 | 난이도 |
|---|---|---|---|
| 로그인 OK, 조회 **`-8204`** "최신 버전으로 업데이트" | 앱 버전 검사 | `constants.py`의 `APP_DISPLAY_VERSION`·`APP_VERSION` → [4](#4-가장-흔한-경우--8204-앱-버전) | 쉬움 (10분) |
| **`-8202`** "VPN 또는 데이터센터…" | IP 차단 | pykorail 아님. 집 러너의 VPN·프록시 확인 ([SELF_HOSTED_RUNNER.md](SELF_HOSTED_RUNNER.md)) | — |
| **MACRO ERROR**, TLS/HTTP 403 계열 | TLS 지문 | `constants.py`의 `IMPERSONATE`, 또는 `curl-cffi` 버전 → [5](#5-tls-지문-macro-error) | 보통 |
| 비밀번호가 맞는데 로그인 거부, 일반 오류 | DynaPath 토큰(`x-dynapath-m-token`) | `auth/dynapath.py`, `auth/signer.py` → [6](#6-dynapath-토큰이-바뀐-경우) | 어려움 |
| 앱도 기기 인증(StrongBox/TEE 하드웨어 증명) 없으면 거부 | 하드웨어 증명 | 파이썬으로 재현 불가 → [7](#7-한계) | 불가 |
| `pip install`에서 실패 | 설치 | 휠 경로·RECORD 문제. `python3 scripts/pykorail_wheel.py verify` | 쉬움 |

참고: 2026-10-21 앱 7.0.10 개편 때 0.2.2는 로그인은 되고 조회만 `-8204`로
막혔습니다. 그런데 이를 푼 0.6.0(10/28)은 버전 값만이 아니라 DynaPath 토큰도
새로 만들었습니다. 즉 `-8204`가 버전만의 문제라는 보장은 없습니다. 버전을
올려도 안 풀리면 6번 경우입니다.

## 4. 가장 흔한 경우: -8204 (앱 버전)

서버가 요청에 실린 앱 버전으로 "최신 앱인가"를 검사합니다. 값은 두 개입니다.

| 상수 | 폼 필드 | 실리는 곳 | 지금 값 |
|---|---|---|---|
| `APP_DISPLAY_VERSION` | `AppVersion` | 로그인, 공통 요청 | `7.1.0` |
| `APP_VERSION` | `Version` | 모든 요청(조회 포함) | `261028001` |

### 4-1. 후보값 찾기

- **`APP_DISPLAY_VERSION`**: 스토어에 보이는 코레일톡 최신 버전 (예: `7.2.0`).
  안드로이드 기준이 정확하고, APK 미러 사이트의 버전 목록에 출시일과 함께 나옵니다.
- **`APP_VERSION`** (9자리): 앱 트래픽에서만 보이는 값이라 확정된 공식이 없습니다.
  지금까지 관측된 값은 `250722001`(7.0.8, 7.0.10 공통)과 `261028001`(7.1.0)로,
  **YYMMDD + 001** 꼴입니다. 새 버전의 출시일로 만든 값이 첫 후보입니다
  (추정이니 probe로 확인). 7.0.10이 7.0.8의 값을 그대로 썼듯이 안 바뀌는 때도
  있으니, **표시 버전만 먼저** 시험하세요.

### 4-2. 휠 없이 시험 (probe)

api-probe를 `try_app_version`(예: `7.2.0`)에만 값을 넣고 돌립니다.
여전히 `-8204`면 `try_api_version`(예: `261201001`)도 넣고 다시 돌립니다.
값은 그 실행의 메모리에서만 바뀌고 감시에는 영향이 없습니다. 첫 줄이
`app 7.2.0 / Version … (TRIAL, in memory only)`로 바뀌고 조회 결과가 나오면 맞은 값입니다.

### 4-3. 고친 휠 만들기

```bash
git switch -c fix/pykorail-app-7.2.0
python3 scripts/pykorail_wheel.py unpack                       # → build/pykorail-src (git 무시)
python3 scripts/pykorail_wheel.py bump-app --display 7.2.0     # probe에서 통한 값만
#   (APP_VERSION도 바꿔야 했다면: --api 261201001)
python3 scripts/pykorail_wheel.py pack --version 0.6.1+ktx.1 --update-requirements
git rm vendor/pykorail-0.6.1-py3-none-any.whl                  # 원본은 git 기록에 남음
python3 scripts/pykorail_wheel.py verify
python -m pytest tests/ -q
rm -rf build/pykorail-src
```

- 버전은 `0.6.1+ktx.N`으로 붙입니다(N은 우리 패치 순번). `+` 뒤는 로컬 버전이라
  업스트림 배포와 절대 헷갈리지 않습니다.
- `pack`은 결정적입니다. 같은 트리면 같은 바이트, 같은 sha256이 나옵니다.

### 4-4. 반영

1. [`vendor/README.md`](../vendor/README.md)에 패치 이력 한 줄(무엇을, 왜, 새 sha256)을 남기고,
   `requirements.txt` 주석과 `CLAUDE.md`의 버전 언급도 맞춥니다.
2. PR을 열고, **브랜치로** api-probe(`run_probe` 체크, 입력 비움)를 돌려 고친 휠
   자체로 조회가 되는지 봅니다. 마지막 `adapter search` 줄까지 나와야 합니다.
3. CI 녹색 + probe 통과면 머지. 다음 폴링부터 반영됩니다.

## 5. TLS 지문 (MACRO ERROR)

코레일+가 TLS 핸드셰이크 모양으로 구형 클라이언트를 거릅니다. pykorail은
curl-cffi로 크롬 안드로이드를 흉내 냅니다.

1. 먼저 `curl-cffi`만 올려 봅니다 (`requirements.txt`의 `curl-cffi==` 핀, Dependabot PR이 이미 있을 수 있음).
   지금 핀(0.16.3)의 안드로이드 타깃은 `chrome99_android`, `chrome131_android`뿐이라
   더 새 타깃이 필요하면 curl-cffi부터 올려야 합니다.
2. 그래도 막히면 `build/pykorail-src/pykorail/constants.py`의 `IMPERSONATE`
   (지금 `chrome131_android`)를 curl-cffi가 지원하는 더 새 안드로이드 타깃으로 바꿉니다.
   지원 목록은 설치된 curl-cffi에서
   `python -c "from curl_cffi.requests import BrowserType; print([b.value for b in BrowserType])"`.
3. 4-3과 같은 방법으로 묶고(`bump-app` 대신 직접 수정), 4-4대로 반영합니다.

## 6. DynaPath 토큰이 바뀐 경우

로그인부터 거부되고 버전·TLS로 안 풀리면, 앱의 봇 차단 SDK(DynaPath)가 바뀐 것입니다.
토큰은 `auth/dynapath.py`가 만들고(`201` 프리픽스, SDK `2.0.1`, 앱 id `korailplus`,
기기의 `android_id`가 들어감), `auth/signer.py`가 어느 요청에 붙일지 정합니다.
상수는 `constants.py`의 `TOKEN_PREFIX`, `DYNAPATH_SDK_VERSION`, `DYNAPATH_APP_ID`.

- **SDK 버전이나 프리픽스만** 바뀌었으면 상수 수정으로 끝날 수 있습니다. 시험은 5번처럼.
- **비밀값이나 알고리즘**이 바뀌었으면 새 앱의 네이티브 라이브러리
  (`libdp_ffi_android.so`)를 역분석해야 합니다. 상수 수정과는 차원이 다른 큰 작업입니다.
  현실적인 순서: (1) PyPI 미러와 GitHub에서 누가 새 버전이나 포크를 올렸는지 확인하고
  `vendor/README.md`의 검증 절차(여러 출처 해시 일치 + 코드 검사)대로 들여옴, (2) 없으면 역분석.

## 7. 한계

- **하드웨어 증명**: 코레일이 앱 기기의 StrongBox/TEE 증명을 필수로 만들면 어떤
  파이썬 클라이언트도 통과할 수 없습니다. 그때는 감시를 접어야 합니다.
- **두 벌의 코드**: 휠 안의 `_sync/`는 `_async/`에서 자동 생성된 사본입니다(파일 머리의
  "생성물" 표시). 생성 스크립트는 휠에 없으니, 코드를 고칠 때는 **둘 다** 같은 내용으로
  고칩니다. 감시가 실제로 쓰는 것은 `_sync/`입니다. 상수(`constants.py`)는 한 곳뿐입니다.
- **라이선스**: 고친 휠도 "pykorail License"(MIT + 상업적 이득 금지)를 따릅니다.
  `pack`은 `dist-info/licenses/LICENSE`를 그대로 싣습니다. 지우지 마세요.
  본인 여행용 개인 프로젝트 범위를 벗어나 배포하거나 유료로 쓰면 안 됩니다.

## 휠 안 지도

| 경로 | 내용 |
|---|---|
| `pykorail/constants.py` | 앱 신원값(버전, `API_KEY`, `SID_KEY`), DynaPath 상수, `IMPERSONATE`, 엔드포인트 |
| `pykorail/transport.py` | curl-cffi 세션 생성 (TLS 위장) |
| `pykorail/auth/dynapath.py`, `auth/signer.py` | `x-dynapath-m-token` 생성과 부착 규칙 |
| `pykorail/crypto.py` | 비밀번호·Sid 암호화 (AES-CBC) |
| `pykorail/device/` | 기기 프로파일, `android_id` (워치는 state에 고정 저장) |
| `pykorail/_sync/`, `pykorail/_async/` | 클라이언트·요청 조립 (위 "두 벌의 코드" 참고) |
| `pykorail-<ver>.dist-info/` | METADATA, RECORD(파일별 해시), LICENSE. `pack`이 다시 씀 |
