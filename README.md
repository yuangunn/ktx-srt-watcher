# KTX/SRT 취소표 감시 봇

GitHub Actions + Cloudflare Worker bridge가 설정한 간격(최소 10분, 고정/랜덤)으로 [코레일](https://www.letskorail.com)/[SRT](https://etk.srail.kr)를 폴링해 취소표를 잡고, 텔레그램과 [Pushover](https://pushover.net)로 알림을 보냅니다. 옵션으로 임시예약(20분 hold)까지 자동으로 잡고, 결제 마감이 다가오면 반복 알림을 보냅니다. **결제는 절대 자동화하지 않습니다** — 본인 직접.

Pushover를 쓰는 이유는 하나입니다. iOS는 Apple이 **Critical Alerts** 권한을 준 앱만 무음 스위치와 방해금지를 무시할 수 있고, 텔레그램에는 그 권한이 없습니다. 새벽 3시에 뜬 취소표를 자느라 놓치는 문제는 텔레그램 쪽에서는 해결할 방법이 없습니다.

PWA([Cloudflare Pages](https://pages.cloudflare.com))로 워치 추가/수정/삭제, 통계 보기, 로그 확인. iOS Safari에서 standalone 설치 가능.

> **워치 목록과 폴링 상태는 저장소에 없습니다.** 워치 ID가 노선과 날짜를 그대로 담고 있어서(`서울-대전-20260101-a1b2`) 공개 저장소에 커밋하면 언제 집이 비는지가 공개됩니다. 둘 다 Cloudflare Worker의 KV에 있고 토큰 없이는 읽히지 않습니다. 저장소에는 코드만 있습니다.

---

## 무엇이 동작하나

- **취소표 감시** — `[수원→부산] 2026-05-23 06:00–14:00 KTX` 같은 워치를 설정한 간격으로 돌리며 잔여석 발견 시 알림
- **무음을 뚫는 긴급 알림** — 좌석 발견·임시예약·결제 마감 임박은 Pushover 긴급(priority 2)으로 나가 **확인을 누를 때까지 60초마다 반복**. 알림 본문에 멈추는 방법이 적혀 있어, Pushover를 모르는 가족이 폰을 집어도 끌 수 있음
- **집/외출 전환** — 외출 중에는 긴급을 높음으로 낮춰 무음 스위치를 존중(수업 중 최대 음량 방지). 앱 토글 또는 iOS 단축어 위치 자동화. **직접 바꾼 값이 자동화보다 6시간 우선**
- **자동 임시예약** (옵션) — 잔여석 발견 즉시 20분 hold. 결제는 코레일톡/SR 앱에서 직접. 성공 시 스스로 꺼지고, **결제 없이 만료되면 다시 켜져** 계속 찾음
- **대기예약 fallback** (옵션) — 매진된 좌석에 대해 코레일 `try_waiting` / SRT `reserve_standby`로 대기 등록
- **결제 마감 알림** — 임시예약 후 T+3/6/9/12분, 그리고 마지막 5분은 매분. 마지막 구간은 긴급 우선순위
- **알림 테스트** — 실제 좌석 발견 알림 경로를 그대로 발송. 텔레그램/Pushover 개별 버튼(실패 원인이 서로 달라서). 조회도 예약도 하지 않음
- **역 선택** — 최근 경로(한 번에 양쪽 채움)·최근 역·검색. 80개 드롭다운을 훑지 않아도 됨
- **PWA 설정 화면** — 알림 / Pushover / 감시 동작 / 시스템 네 섹션. Pushover·단축어 설정 안내 내장
- **통계 + 최근 실행 로그** — 실제 폴링만 집계(throttle로 건너뛴 실행은 제외), 일별 막대 그래프, 워크플로우 로그 모달
- **시스템 안전망** — 상태 카드(폴링·Actions·CF Worker·PAT 만료), Heartbeat(6시간 침묵 시 알림), PAT 만료 7일 전 경고, 일요일 21시 주간 요약

## 무엇이 동작하지 않나 (의도)

- ❌ **결제** — 카드 정보 저장/사용 안 함. 임시예약까지만, 결제는 사용자가 앱에서
- ❌ **명절/오픈런 초고경쟁** — 최소 10분 간격으로는 매크로 봇 못 이김. 비성수기/평일 캔슬 대상
- ❌ **타인 좌석** — 본인 코레일/SR 계정으로만 동작

---

## 아키텍처

```
       ┌─────────────────────┐
       │  PWA (CF Pages)     │ 사용자가 워치 추가/수정/삭제,
       │  ktx-srt-watcher    │ 설정 변경, "지금 확인" 버튼,
       │  .pages.dev         │ 통계 / 최근 실행 로그 보기
       └────┬───────────┬────┘
            │           │ APP_TOKEN
            │           │ (워치 목록 / 폴링 상태)
            │           └──────────────┐
            │ GitHub PAT               ▼
            │ (Actions 전용)  ┌─────────────────────┐
            │                 │  CF Worker + KV     │
            │                 │  (cloudflare-worker)│
            ▼                 │                     │
       ┌─────────────────────┐│  KV:                │
       │  GitHub Repo        ││   - config (워치)   │
       │  (public, 코드만)   ││   - state (폴링기록)│
       │  - .github/...      ││                     │
       │  - worker/...       ││  Cron */3:          │
       │  - frontend/...     ││   → repository_     │
       │  - cloudflare-      ││     dispatch ──┐    │
       │      worker/...     ││                │    │
       └──────────┬──────────┘└────────────────┼────┘
                  ▼                            │
       ┌─────────────────────┐                 │
       │  GitHub Actions     │◄────────────────┘
       │  ticket-watch.yml   │
       │                     │   GET /config           ┐
       │   python -m         │   PUT /state            │
       │     worker.main     │   POST /reminder/       │ REMINDER_
       │                     │        schedule         │  TOKEN
       │   - korail2 / SRT   │  ───────────────────────┘
       │   - Telegram alert  │
       └──────────┬──────────┘   CF Worker 나머지 cron:
                  │                */1  리마인더 발송
                  │                */6h 하트비트
                  ▼                daily PAT 만료 확인
       ┌─────────────────────┐     weekly 주간 요약
       │  텔레그램 봇 알림   │◄──────────────────────
       └─────────────────────┘
```

**워치 목록과 폴링 기록은 저장소에 없습니다.** 워치 ID가 노선과 날짜를 그대로
담고 있어서(`서울-대전-20260101-a1b2`) 공개 저장소에 커밋하면 언제 집이 비는지가
공개됩니다. 둘 다 CF Worker의 KV에 있고 토큰 없이는 읽히지 않습니다.

---

## 셋업 (자기 인스턴스 만들기)

### 0. 사전 준비

- **GitHub** 계정 — **레포는 public이어야 합니다.** private은 Actions 무료 분(월 2,000분)에 걸립니다. 민감한 데이터는 저장소에 없으니(위 참고) public이어도 안전합니다
- **Cloudflare** 계정 (free OK)
- **Node.js 18+** + `npm`
- 코레일 / SR 본인 계정 (앱 로그인되는 그 ID/PW)
- 텔레그램 (봇은 [@BotFather](https://t.me/BotFather)에서 새로 발급 가능)
- **Pushover** (선택, 일회성 약 $5) — 무음·방해금지를 뚫는 알림이 필요하면

### 1. 레포 fork

```bash
gh repo fork yuangunn/ktx-srt-watcher --clone
cd ktx-srt-watcher
```

또는 GitHub 웹에서 Fork 후 `git clone`.

### 2. 텔레그램 봇 만들기

[@BotFather](https://t.me/BotFather)에서:
- `/newbot` → 이름 / username 입력 → 발급된 token 메모
- 본인 봇 채팅창에 `/start` 한 번 보내기 (서버가 chat_id 알려면 첫 메시지 필요)
- chat_id 확인: `https://api.telegram.org/bot<TOKEN>/getUpdates` 의 `chat.id`

### 3. GitHub Secrets 등록

본인 fork된 repo Settings → Secrets and variables → Actions:

| Secret | 값 |
|---|---|
| `KORAIL_ID` | 코레일 회원번호 (10자리 숫자) — 또는 가입 이메일 |
| `KORAIL_PW` | 코레일 비밀번호 |
| `SRT_ID` | SR 회원번호 (10자리) — 휴대폰 번호 형식도 가능 (`010XXXXXXXX`) |
| `SRT_PW` | SR 비밀번호 |
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급받은 token |
| `TELEGRAM_CHAT_ID` | 본인 chat_id (숫자) |
| `PUSHOVER_TOKEN` | (선택) Pushover 앱 API token — [pushover.net/apps/build](https://pushover.net/apps/build) |
| `PUSHOVER_USER` | (선택) Pushover User Key — pushover.net 첫 화면 우상단. **API token과 다른 값** |
| `CLOUDFLARE_API_TOKEN` | Worker 자동 배포용. CF → My Profile → API Tokens → "Edit Cloudflare Workers" 템플릿 |

Pushover 두 값이 없으면 알림은 텔레그램으로만 나갑니다(무음은 못 뚫습니다).
`CF_WORKER_URL` + `REMINDER_TOKEN`은 CF Worker 배포 후에 추가 (5단계).

### 4. Cloudflare Worker 배포

```bash
cd cloudflare-worker
npm install
npx wrangler login            # 브라우저에서 CF 계정 인증
npx wrangler kv namespace create REMINDERS    # 결제 마감 알림용
npx wrangler kv namespace create STATE        # 워치 목록 + 폴링 상태 + 집/외출
```

**wrangler.toml** 안에 두 `[[kv_namespaces]] id` 값 갱신:
```toml
[[kv_namespaces]]
binding = "REMINDERS"
id = "<REMINDERS id>"

[[kv_namespaces]]
binding = "STATE"
id = "<STATE id>"
```

또 `[vars] GITHUB_REPO` 값을 본인 fork 경로로 갱신:
```toml
[vars]
GITHUB_REPO = "<your-username>/ktx-srt-watcher"
```

CF Worker secret 등록:

```bash
# fine-grained PAT 만들고 (github.com/settings/personal-access-tokens/new)
#   - Repository access: 본인 fork만
#   - Permissions: Contents Read/write + Actions Read/write
# 발급된 github_pat_... 값을 입력
npx wrangler secret put GITHUB_TOKEN

# 텔레그램
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID

# 임의의 strong shared secret (worker.main이 CF Worker에 인증할 때 사용)
# 예: openssl rand -hex 32
npx wrangler secret put REMINDER_TOKEN

# PWA가 CF Worker에 인증할 때 쓰는 별도 토큰 (REMINDER_TOKEN과 다른 값으로)
# 브라우저에 저장되는 값이라 분리합니다 — 새어도 state 쓰기는 불가
npx wrangler secret put APP_TOKEN

# (선택) 결제 마감 알림도 Pushover로 보내려면
npx wrangler secret put PUSHOVER_TOKEN
npx wrangler secret put PUSHOVER_USER

npx wrangler deploy
```

이후로는 손으로 배포할 일이 없습니다. `cloudflare-worker/` 아래가 바뀐 채로 `main`에 push되면
`.github/workflows/deploy-worker.yml`이 배포하고 `/health`까지 확인합니다.

배포 완료 출력의 URL(예: `https://ktx-srt-watcher-cron.<account>.workers.dev`)을 메모.

### 5. GitHub Secrets 추가

```bash
# CF Worker URL
gh secret set CF_WORKER_URL -R <your-username>/ktx-srt-watcher --body "https://ktx-srt-watcher-cron.<account>.workers.dev"

# 4단계의 REMINDER_TOKEN과 똑같은 값
gh secret set REMINDER_TOKEN -R <your-username>/ktx-srt-watcher --body "<same-value-as-cf>"
```

### 6. PWA 배포 (Cloudflare Pages)

1. [pages.cloudflare.com](https://pages.cloudflare.com) → Create project → Connect to Git → 본인 fork 선택
2. Build settings:
   - Framework preset: **None**
   - Build command: (비움)
   - Build output directory: `frontend`
3. Deploy → 출력 URL(예: `https://ktx-srt-watcher.pages.dev`)

iOS Safari로 그 URL 접속 → 공유 → 홈 화면에 추가.

### 7. PWA 첫 연결

PWA 첫 화면에서 세 가지 입력:
- Repository: `<your-username>/ktx-srt-watcher`
- Personal Access Token: fine-grained PAT. **Contents는 Read-only면 충분합니다** — 워치는 저장소가 아니라 KV에 저장되므로, PAT는 "지금 확인"(Actions 실행)과 실행 기록 조회에만 씁니다
- 앱 토큰: 4단계의 `APP_TOKEN`과 **같은 값**

연결 시 GitHub와 CF Worker를 각각 검사해서, 실패하면 어느 쪽인지 메시지에 나옵니다.

### 8. 첫 워치 추가

PWA `+ 워치 추가` 버튼:
- 제공자, 출발/도착, 날짜, 시간 범위, 열차 종류, 인원, 좌석 등급, 자동 예약 토글

저장하면 CF Worker의 KV에 저장되고, 다음 cron tick에 폴링 시작.

---

## 설정 옵션 (PWA 설정 패널)

| 항목 | 의미 | 기본값 |
|---|---|---|
| 폴링 간격 | 모드 3종: **고정**(`poll_interval_min`) / **범위 랜덤**(`poll_interval_range: [24,36]`) / **목록 랜덤**(`poll_interval_choices: [27,33,42]`). 모두 최소 10분 강제. 랜덤 모드는 매번 다른 간격으로 폴링해 봇 탐지 회피 | 고정 |
| 집에 있음 | ON이면 긴급 알림이 무음·방해금지를 뚫고 반복. OFF면 알림은 오되 무음 스위치를 따름. 앱 토글이 단축어 자동화보다 6시간 우선 | ON |
| 빈 결과도 알림 | cron이 잔여 0건 발견해도 텔레그램으로 요약 메시지 발송 | OFF |
| 잔여 있는 동안 계속 알림 | 이미 알린 좌석도 잔여가 있는 한 폴링마다 다시 알림. OFF면 좌석당 한 번 | OFF |
| 대기예약 자동 등록 | 매진 좌석에 대해 자동 대기예약 (코레일 `try_waiting` / SRT `reserve_standby`) | OFF |
| 조용한 시간 (KST) | 이 시간대 cron 알림은 음소거 (`disable_notification`). 수동/임시예약/마감 알림은 무관 | 비활성 |
| 워치별 자동 예약 | 좌석 발견 시 임시예약 자동 시도. 성공하면 스스로 꺼지고, 결제 없이 만료되면 다시 켜짐 | OFF |

### 폴링 간격과 트리거 주기

폴링은 **CF 트리거(`*/3`) 경계에만** 걸립니다. 그래서 설정한 간격이 3분 단위로 올림됩니다.

| 설정 범위 | 실제 나오는 간격 |
|---|---|
| 24~36 | 24 / 27 / 30 / 33 / 36 — 고르게 |
| 27~29 | 27 / 30 — 범위가 트리거 주기보다 좁아 뭉개짐 |

**간격을 3의 배수로 잡거나 범위를 넓게 잡으세요.** 좁은 범위는 랜덤이 몇 개 값으로 수렴해서, 봇 탐지 회피라는 목적을 잃습니다.

---

## 비용 (free tier 안에 안전한 설정)

| 서비스 | 사용량 | 한도 | 여유 |
|---|---|---|---|
| GitHub Actions (public) | 무제한 (퍼블릭 레포는 무료) | — | 무제한 |
| Cloudflare Workers | ~1,900 invocations/일 (`*/3` 480 + `*/1` 1,440 + 기타) | 100,000/일 | 매우 여유 |
| Cloudflare Pages | 정적 파일 호스팅 | 500 build/월, 무제한 요청 | 여유 |
| Cloudflare KV | ~1,000 reads + ~50 writes/일 | 100k reads, 1k writes/일 | 여유 |
| Pushover | 좌석 발견 시에만 | 10,000 메시지/월 (앱은 일회성 구매) | 매우 여유 |

레포가 퍼블릭이라 GitHub Actions 실행 시간은 무제한·무료입니다. CF 트리거는 `*/3`으로 촘촘하게 두고, 실제 폴링 빈도는 워커의 throttle(최소 10분, fixed/range/choices)이 제어합니다.

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 폴링이 설정한 간격보다 늦거나 불규칙함 | 실제 폴링은 CF Worker `*/3 repository_dispatch` 트리거 위에서 워커 throttle로 동작. 폴링은 트리거 경계에만 걸리므로 설정 간격이 3분 단위로 올림됨(24~36 범위 → 24/27/30/33/36). 랜덤 모드는 의도적으로 불규칙. CF Worker 정상 배포 여부는 `npx wrangler tail`로 확인 |
| 텔레그램 알림이 안 옴 | 1) `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` GitHub secret 확인. 2) 봇 채팅창에 `/start` 한 번 보냈는지. 3) PWA의 "조용한 시간"이 지금 시간대를 포함하는지 |
| 자동 예약 직후 "동일한 예약" 에러 | state 기록이 다음 run보다 늦게 반영된 case. 어댑터가 `WRR800029` / "동일한 예약" 메시지를 detect하면 silent dedupe — 정상 동작 |
| `결제 마감 12:06`처럼 시간이 이상함 | 이전 버그. 지금은 코레일/SRT 응답의 실제 deadline + KST 변환을 사용. 신규 임시예약부터 정상 |
| Run watcher 로그가 비어 보임 | poll 간격 throttle로 skip된 run (랜덤 모드는 state의 `next_poll_at` 전까지 skip). 모달 상단에 안내 배너 표시. 실제 조회 결과는 직전 실제 실행 클릭해서 확인 |
| PAT 만료 임박 알림이 옴 | CF Worker의 daily PAT check 동작 중. 안내된 절차로 토큰 regenerate + `npx wrangler secret put GITHUB_TOKEN` 갱신 |
| 워커가 6시간 침묵 알림 | Heartbeat 동작. CF Worker 또는 GHA Actions, PAT 점검 필요 |
| Pushover 알림이 안 옴 | 설정 → 알림 → **Pushover 테스트**를 폰 무음 상태로 눌러 확인. 그래도 안 울리면 Pushover 앱의 **Critical Alerts 허용**이 꺼져 있을 가능성이 큽니다. 그 다음은 User Key와 API Token을 바꿔 넣지 않았는지 — 서로 다른 값입니다 |
| Pushover 알림이 계속 반복됨 | 긴급 우선순위의 의도된 동작. 알림을 열거나 Pushover 앱의 **[Acknowledge]** 를 누르면 멈추고, 안 눌러도 15분 뒤(테스트는 2분) 자동으로 멈춥니다 |
| 앱 실행 시 "네트워크에 연결되지 않아…" | PWA 콜드 스타트가 네트워크보다 먼저 뜬 경우. 알아서 재시도하고, 연결이 돌아오면 자동으로 다시 불러옵니다 |
| 앱에서 앱 토큰 확인 실패 | `npx wrangler secret put APP_TOKEN` 값과 앱에 넣은 값이 같은지 확인. 메시지에 `→ 401`이면 토큰 불일치, 그 외는 네트워크 |
| 워치가 계속 "잔여 0" | 정상입니다. 어댑터가 **좌석 있는 열차만** 조회하므로(`include_no_seats=False` / `available_only=True`), 0은 조회 실패가 아니라 그 시간대 전 열차 매진이라는 뜻입니다 |

---

## 라이선스 / 면책

- 코드: MIT 또는 본인 fork에서 자유 결정
- **Korail/SRT 약관 준수 필수** — 본인 좌석만, 영리 목적 금지, 매크로 의심 트래픽 자제
- 본 시스템은 비공식 (코레일/SR 공식 라이브러리 아님). 두 서비스의 anti-bot 정책 변경 시 동작 멈출 수 있음
- 결제 자동화 절대 금지 — 임시예약(20분 hold)까지만, 결제는 직접

## 디렉토리 구조

```
ktx-srt-watcher/
├── worker/                       # Python 백엔드
│   ├── main.py                   # 진입점
│   ├── adapters/
│   │   ├── base.py               # Provider Protocol
│   │   ├── korail.py             # 코레일 (chasehuh fork pin)
│   │   └── srt.py                # SRT (SRTrain)
│   ├── matcher.py
│   ├── notifier.py               # 텔레그램
│   ├── pushover.py               # 무음/방해금지를 뚫는 긴급 알림
│   ├── models.py
│   ├── throttle.py               # 폴링 간격 판정 (stdlib only)
│   ├── gate.py                   # pip install 전에 폴링 여부 결정
│   ├── remote.py                 # CF KV의 config/state/mode 읽기/쓰기
│   └── state.py                  # state 딕셔너리 조작
├── tests/                        # pytest, 156
├── frontend/                     # PWA
│   ├── index.html
│   ├── DESIGN.md                 # 디자인 시스템 (google-labs/design.md format)
│   ├── manifest.json
│   ├── sw.js
│   ├── css/app.css
│   ├── js/app.js
│   └── icons/
├── cloudflare-worker/            # CF Worker (cron bridge + reminders + heartbeat)
│   ├── wrangler.toml
│   ├── src/index.js
│   └── package.json
├── .github/workflows/
│   ├── watch.yml                 # 폴링 + 알림 테스트
│   └── deploy-worker.yml         # cloudflare-worker/ 변경 시 자동 배포
└── requirements.txt
```

기여 / 이슈는 GitHub PR로.
