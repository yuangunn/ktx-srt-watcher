# 집에서 폴링하기 — self-hosted runner 설치 (상세)

## 왜 필요한가

2026-09 코레일-SR 통합 후, 코레일+ 백엔드는 **데이터센터/VPN IP의 로그인을
차단**합니다 (응답 코드 `-8202`, "VPN 또는 데이터센터를 통해서는 서비스를
이용할 수 없습니다"). GitHub Actions 기본 러너는 Azure 데이터센터라서
자격증명이 맞아도 로그인 자체가 불가능합니다. 그래서 **폴링 잡만** 집의
기기(주거용 IP)에서 돌립니다. cron·시크릿·워크플로 구조는 그대로이고,
`runs-on`만 `[self-hosted, korail-home]`으로 바뀌었습니다.

CI(pytest)·Cloudflare 배포는 계속 GitHub 호스팅 러너에서 돕니다.

## 기기 선택

| | 라즈베리파이 (권장) | 맥 (맥북/맥미니) |
|---|---|---|
| 상시 구동 | 전기료 월 몇백 원, 무소음 | 잠자기 설정 손봐야 함 |
| 설치 난이도 | 쉬움 (systemd 서비스) | PATH 함정 하나 있음 (아래) |
| 요구 사항 | **64-bit OS 필수** (Pi 3 이상 + Raspberry Pi OS 64-bit) | Apple Silicon/Intel 무관 |

공통 요구: Python **3.10+** (`python3 --version`), git, 집 인터넷.
Pi OS bookworm은 3.11이라 그대로 됩니다. macOS는 아래 참고.

---

## 1) GitHub에서 러너 등록 시작

1. https://github.com/yuangunn/ktx-srt-watcher/settings/actions/runners/new
   (저장소 → Settings → Actions → Runners → **New self-hosted runner**)
2. 기기에 맞게 선택:
   - 라즈베리파이 → **Linux / ARM64**
   - Apple Silicon 맥 → **macOS / ARM64**, Intel 맥 → macOS / x64
3. 페이지에 **Download / Configure 명령이 등록 토큰과 함께** 표시됩니다.
   토큰은 약 1시간만 유효하니 페이지를 띄워둔 채 진행하세요.
   아래 명령은 버전 번호가 바뀌므로 **항상 그 페이지의 것을 복사**하세요.

## 2) 기기에서 실행

페이지의 Download 블록 그대로 (예시 모양):

```sh
mkdir actions-runner && cd actions-runner
curl -o actions-runner-<os>-<arch>-<버전>.tar.gz -L https://github.com/actions/runner/releases/download/...
tar xzf actions-runner-*.tar.gz
```

라즈베리파이에서 의존성 오류가 나면 한 번:

```sh
sudo ./bin/installdependencies.sh
```

이어서 Configure:

```sh
./config.sh --url https://github.com/yuangunn/ktx-srt-watcher --token <페이지의 토큰>
```

물어보는 것들 — **라벨만 중요합니다**:

| 질문 | 입력 |
|---|---|
| runner group | Enter (기본) |
| name | Enter (기본) |
| **additional labels** | **`korail-home`** ← 반드시. 워크플로가 이 라벨로 잡을 찾습니다 |
| work folder | Enter (기본) |

라벨을 빼먹었다면: `./config.sh remove --token <토큰>` 후 다시 config,
또는 GitHub Runners 페이지에서 러너 클릭 → 라벨 편집.

## 3) 서비스로 등록 (재부팅 자동 시작)

**라즈베리파이 (Linux):**

```sh
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status   # active (running) 확인
```

**맥 (macOS):**

```sh
./svc.sh install    # sudo 없이 — 로그인 세션의 LaunchAgent로 등록
./svc.sh start
./svc.sh status
```

### macOS만: PATH 함정 (중요)

macOS 기본 `/usr/bin/python3`(Xcode CLT)는 3.9라서 pykorail(3.10+)이 설치되지
않습니다. Homebrew 파이썬을 쓰되, **서비스로 뜬 러너는 Homebrew PATH를
모릅니다.** 러너 폴더의 `.env` 파일로 주입하세요:

```sh
brew install python
cd ~/actions-runner
echo "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" >> .env
./svc.sh stop && ./svc.sh start
```

(Intel 맥은 `/opt/homebrew/bin` 대신 `/usr/local/bin`이 Homebrew 경로입니다.
둘 다 넣어도 무해합니다.)

### macOS만: 잠자기 방지

맥이 잠들면 러너도 멈춥니다:

- 전원 어댑터 연결 유지
- 설정 → 디스플레이 → 고급 → **"전원 어댑터 연결 시 디스플레이가 꺼져 있을 때
  자동으로 잠자지 않기"** 켜기 (화면은 꺼져도 시스템은 깨어 있음)
- 맥북은 **덮개를 열어두세요** — 덮으면 외장 모니터 없이는 잠듭니다
- LaunchAgent는 **로그인된 동안** 도는 방식이라, 재부팅 후엔 한 번 로그인돼
  있어야 합니다 (자동 로그인 설정 가능)

## 4) 확인

1. Settings → Actions → Runners에 러너가 초록 **Idle** — 등록 성공
2. 몇 분 내 다음 크론 틱이 이 러너에서 돌고, Actions 탭의 `ticket-watch`가
   초록으로 완료
3. 첫 정상 폴링에서 텔레그램 **"✅ 코레일 검색 복구"** — 이게 최종 성공 신호
4. (선택) `api-probe` 워크플로를 runner=`korail-home`, run_probe=true로
   수동 실행하면 로그인+수서→부산 검색을 즉석 검증

## 문제 해결

| 증상 | 원인/조치 |
|---|---|
| 잡이 계속 Queued | 러너 오프라인이거나 **라벨 불일치** — Runners 페이지에서 라벨에 `korail-home` 있는지 확인 |
| `pip install` 실패: requires Python >=3.10 | macOS PATH 함정 — 위 `.env` 조치 |
| `python3 -m venv` 오류 (Linux) | `sudo apt install -y python3-venv` |
| config.sh가 의존성 오류 (Pi) | `sudo ./bin/installdependencies.sh` |
| 러너가 재부팅 후 안 뜸 (Linux) | `sudo ./svc.sh install`을 안 했거나 실패 — status 확인 |
| 러너가 재부팅 후 안 뜸 (macOS) | 로그인 전에는 LaunchAgent가 안 뜸 — 자동 로그인 설정 |

## 공개 저장소에서의 보안 (중요)

이 저장소는 공개라서, 외부인이 fork PR로 러너에서 코드를 실행하려 들 수
있습니다. 다음이 방어선입니다:

1. **Settings → Actions → General → Fork pull request workflows**에서
   **"Require approval for all outside collaborators"** 를 켜 두세요.
2. 셀프 호스티드 러너를 쓰는 워크플로(`watch.yml`, `api-probe.yml`)에는
   `pull_request` 트리거가 **없습니다** — 앞으로도 붙이지 마세요.
3. `pull_request`가 있는 `tests.yml`은 `ubuntu-latest`(GitHub 호스팅)에서만
   돕니다. 그대로 두세요.
4. 러너는 관리자 권한 없는 일반 계정으로 돌리는 걸 권합니다.

## 러너가 꺼져 있으면

잡이 쌓이지 않습니다 — `concurrency` 그룹이 최신 틱만 남기고, 15분 이상
큐에 있으면 CF 워커가 취소합니다. 기기가 다시 켜지면 다음 틱부터 자연히
재개됩니다. 며칠 꺼두면 그동안 감시가 멈출 뿐, 고장나는 것은 없습니다.

## 왜 다른 방법이 아닌가

- **한국 클라우드 IP**: 여전히 데이터센터 대역이라 같은 차단에 걸릴
  가능성이 크고, 월 비용이 듭니다.
- **상용 주거용 프록시**: 약관·법적 회색지대라 권하지 않습니다.
- **CF Worker에서 직접 폴링**: CF도 데이터센터이고, pykorail의 TLS 위장
  (curl-cffi 네이티브 코드)은 Workers 런타임에서 돌지 않습니다.
