# KTX/SRT 매표 감시 도우미

## 목적
GitHub Actions cron으로 KTX/SRT 취소표를 폴링해 텔레그램으로 알림.
PWA(GitHub Pages)에서 감시 조건을 추가/삭제.

## 비목표 (하지 않을 것)
- 결제 자동화 — **절대 금지**. 좌석 선점(임시예약)까지만, 결제는 사용자가 앱에서 직접
- 명절 오픈런 초단위 경쟁 — cron 5~15분 지연으로 부적합
- 영리/대리예매 — 약관 위반. 본인 좌석만

## 아키텍처

```
[아이폰 PWA] ──APP_TOKEN──→ [CF Worker KV] ←──REMINDER_TOKEN──┐
                             config / state                   │
                                    ↑ */3 repository_dispatch  │
                              [Actions cron]                  │
                                    ↓                         │
                        worker/main.py (Python 3.11)          │
                          └─ adapters/korail.py (유일)         │
                                    ↓ 새 좌석 감지              │
                              notifier.py → 텔레그램            │
                                    └─────────state PUT────────┘
```

**config/state는 저장소에 커밋하지 않는다.** 워치 ID가 노선과 날짜를 그대로
담고 있어(`서울-대전-20260101-a1b2`) 공개 저장소에 올리면 언제 집이 비는지가
공개된다. 둘 다 CF Worker의 KV에 있고 토큰 없이는 읽히지 않는다. 저장소에는
코드만 있다. PWA의 GitHub PAT는 이제 Actions 전용("지금 확인" + 실행 기록).

## 모듈 명세

### `worker/adapters/base.py`
추상 인터페이스. 두 adapter가 동일 시그니처 노출:
```python
class Provider(Protocol):
    def login(self, user_id: str, password: str) -> None: ...
    def search(self, watch: Watch) -> list[Train]: ...
    def reserve(self, train: Train) -> Reservation: ...  # auto_reserve=true 시만
```
`Watch`, `Train`, `Reservation`은 pydantic 모델로 `worker/models.py`에 정의.

### `worker/adapters/korail.py` — 유일한 어댑터
- 2026-09-01 코레일-SR 통합 이후 전 고속열차(구 SRT = KTX-산천 포함)가 코레일
  백엔드에서 팔린다. SRT 앱·API는 2026-08-31 종료
- `pykorail` 사용 (MIT, PyPI). 코레일+ 백엔드는 **TLS 지문**으로 구형 클라이언트를
  거르기 때문에(korail2는 MACRO ERROR로 전멸) curl-cffi 위장을 쓰는 이 라이브러리가
  필요하다. 구 korail2 vendoring은 이때 제거됨
- **기기 프로파일 고정**: 매 실행 다른 기기로 보이면 봇처럼 보인다. 프로파일 id를
  state(`korail_device_profile`)에 저장해 재사용
- `search()`는 `time_min~time_max` 범위, `train_types` 일치, **잔여석 1석 이상**인 열차만 반환
- 로그인 세션은 함수 호출 단위. Actions 매 실행마다 새로 로그인
- **구 SRT 워치 호환**: `provider: "srt"` 워치는 main이 id 변경 없이 korail로
  별칭 처리하고 `train_types`의 "SRT"를 "KTX-산천"으로 매핑한다 (`_merged_provider`)

### `worker/matcher.py`
config의 watch 항목과 adapter `search()` 결과를 매칭. 신규 발견 좌석만 반환 (state의 `notified_train_ids`와 비교).

### `worker/state.py`
- 메모리상의 state dict를 다루는 순수 헬퍼만. 파일을 만지지 않는다 —
  실제 읽기/쓰기는 `remote.py`가 CF KV로 한다
- `last_run`, `watches[id].last_check`, `watches[id].notified_train_ids`,
  `pending_reservations`, `login_failures` 갱신

### `worker/pushover.py`
무음/방해금지를 뚫어야 하는 알림 전용. iOS는 Apple의 Critical Alerts 권한이
있는 앱만 무음 스위치를 무시할 수 있고 텔레그램에는 그 권한이 없다. 좌석 발견,
임시예약 성공, 결제 마감 임박은 priority=2(확인할 때까지 반복)로 나간다.
자격증명이 없으면 조용히 no-op.

알림 세기는 CF의 `/mode`(`home`/`away`)를 따른다. `away`면 긴급(2)을 높음(1)로
낮춰 무음 스위치를 존중한다 — 수업 중에 최대 음량으로 울리면 안 되니까. 위치는
폰만 알기 때문에 아이폰 단축어 자동화나 앱 토글이 이 값을 밀어 넣는다.

### `worker/notifier.py`
텔레그램 Bot API. 메시지 포맷:
```
🚄 [KTX] 서울→부산 2026-05-15
09:35 발 KTX 045 / 잔여 일반 2석
예매: https://www.letskorail.com/...
```
SRT는 코레일톡/SRT 앱 딥링크 또는 웹 URL.

### `worker/main.py`
1. CF KV에서 config 로드 → active watch만 필터
2. provider별로 그룹화, 각 adapter 로그인
3. matcher 돌리고 신규 좌석 → notifier
4. state를 CF KV에 PUT 후 종료
5. **모든 예외는 catch & 로그**. 한 watch 실패가 전체 중단시키면 안 됨

## Secrets (repo Settings → Secrets and variables → Actions)
- `KORAIL_ID`, `KORAIL_PW` (코레일+ 로그인 — 이메일/휴대폰/회원번호)
- ~~`SRT_ID`, `SRT_PW`~~ 통합으로 폐기 — 시크릿 삭제 가능
- `TELEGRAM_BOT_TOKEN` (BotFather에서 발급)
- `TELEGRAM_CHAT_ID` (본인 user id)
- `CF_WORKER_URL`, `REMINDER_TOKEN` (config/state 저장소 접근)
- `PUSHOVER_TOKEN`, `PUSHOVER_USER` (선택 — 무음/방해금지 뚫는 긴급 알림)
- `CLOUDFLARE_API_TOKEN` (worker 자동 배포)

CF Worker 쪽 시크릿(`npx wrangler secret put`): `GITHUB_TOKEN`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `REMINDER_TOKEN`, `APP_TOKEN`.

## PWA (`frontend/`)
- 단일 페이지. 워치 항목 리스트/추가/삭제/토글
- GitHub PAT(Actions 전용) + 앱 토큰(APP_TOKEN)을 localStorage 저장
- CF Worker `/config` GET → 수정 → PUT (KV는 last-write-wins, sha 없음)
- iOS 16.4+ 홈 화면 추가 시 푸시 알림 가능 (보조용, 주 알림은 텔레그램)
- manifest.json: standalone, theme #1a1a1a
- sw.js: offline shell만, 데이터는 항상 GitHub에서 fresh fetch

## 테스트
- `tests/test_matcher.py`: mock adapter 결과로 신규 좌석 판별 로직
- `tests/test_state.py`: atomic write, 동시성
- `tests/test_adapters.py`: 어댑터 스모크 + pykorail 매핑. CI에서 돈다 —
  실 계정이 필요한 통합 테스트만 수동 (`api-probe` 워크플로 dispatch)
- `tests/test_search_failure.py`: provider 전체 검색 실패 감지(감시 중단 알림)

## MVP 단계
1. **Phase 1**: korail/srt adapter, matcher, notifier, state, workflow → 알림까지
2. **Phase 2**: PWA 기본 (목록/추가/삭제)
3. **Phase 3**: auto_reserve (좌석 선점) 옵션. 결제는 절대 안 함
4. **Phase 4**: 동적 cron 간격 (명절 임박 시 자동 단축)

## 코드 스타일
- pydantic v2, 타입 힌트 필수
- 한글 로그 OK, 코드/주석 영어
- 함수당 30줄 이하 지향