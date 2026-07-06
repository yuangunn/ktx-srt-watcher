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
[아이폰 PWA] ─GitHub REST API→ config.json (repo)
                                    ↓ push trigger
                              [Actions cron]
                                    ↓
                        worker/main.py (Python 3.11)
                          ├─ adapters/korail.py
                          └─ adapters/srt.py
                                    ↓ 새 좌석 감지
                              notifier.py → 텔레그램
                                    ↓
                            state.json 커밋
```

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

### `worker/adapters/korail.py`
- `korail2` 라이브러리 사용
- `search()`는 `time_min~time_max` 범위, `train_types` 일치, **잔여석 1석 이상**인 열차만 반환
- 로그인 세션은 함수 호출 단위. Actions 매 실행마다 새로 로그인

### `worker/adapters/srt.py`
- `SRT` 라이브러리 사용 (PyPI: `SRT`)
- 그 외 동일

### `worker/matcher.py`
config의 watch 항목과 adapter `search()` 결과를 매칭. 신규 발견 좌석만 반환 (state.json의 `notified_train_ids`와 비교).

### `worker/state.py`
- 읽기/쓰기 + atomic write (tmp → rename)
- `last_run`, `watches[id].last_check`, `watches[id].notified_train_ids` 갱신

### `worker/notifier.py`
텔레그램 Bot API. 메시지 포맷:
```
🚄 [KTX] 서울→부산 2026-05-15
09:35 발 KTX 045 / 잔여 일반 2석
예매: https://www.letskorail.com/...
```
SRT는 코레일톡/SRT 앱 딥링크 또는 웹 URL.

### `worker/main.py`
1. config.json 로드 → active watch만 필터
2. provider별로 그룹화, 각 adapter 로그인
3. matcher 돌리고 신규 좌석 → notifier
4. state.json 갱신 후 종료
5. **모든 예외는 catch & 로그**. 한 watch 실패가 전체 중단시키면 안 됨

## Secrets (repo Settings → Secrets and variables → Actions)
- `KORAIL_ID`, `KORAIL_PW`
- `SRT_ID`, `SRT_PW`
- `TELEGRAM_BOT_TOKEN` (BotFather에서 발급)
- `TELEGRAM_CHAT_ID` (본인 user id)

## PWA (`frontend/`)
- 단일 페이지. config.json 항목 리스트/추가/삭제/토글
- GitHub Personal Access Token (repo scope) 입력받아 localStorage 저장
- GitHub Contents API로 config.json GET → 수정 → PUT (sha 포함)
- iOS 16.4+ 홈 화면 추가 시 푸시 알림 가능 (보조용, 주 알림은 텔레그램)
- manifest.json: standalone, theme #1a1a1a
- sw.js: offline shell만, 데이터는 항상 GitHub에서 fresh fetch

## 테스트
- `tests/test_matcher.py`: mock adapter 결과로 신규 좌석 판별 로직
- `tests/test_state.py`: atomic write, 동시성
- adapter 통합 테스트는 수동 (실 계정 필요), CI에서 제외

## MVP 단계
1. **Phase 1**: korail/srt adapter, matcher, notifier, state, workflow → 알림까지
2. **Phase 2**: PWA 기본 (목록/추가/삭제)
3. **Phase 3**: auto_reserve (좌석 선점) 옵션. 결제는 절대 안 함
4. **Phase 4**: 동적 cron 간격 (명절 임박 시 자동 단축)

## 코드 스타일
- pydantic v2, 타입 힌트 필수
- 한글 로그 OK, 코드/주석 영어
- 함수당 30줄 이하 지향