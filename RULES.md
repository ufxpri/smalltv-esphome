# SmallTV-Ultra 펌웨어 개발 철칙 & 복구 예방책

> 목표: **어떤 펌웨어를 올려도 시리얼 없이 OTA로 항상 복구 가능**하게 유지한다.
> (핵심 교훈: ESP8266은 코어 1개 → 화면/무거운 코드가 CPU를 오래 잡으면 WiFi가 굶어 먹통이 된다.)

## A. 프로그래밍 철칙 (사람이 지킬 것)

1. **메인 루프를 막지 마라.** `lambda` / `interval` / `on_*` 안의 한 작업이 **~50ms**를 넘기지 않게.
   - 금지: `delay()`, 긴 `for` 루프, 전체화면을 여러 번 칠하는 그리기, 블로킹 네트워크 호출.
2. **무거운 것은 반드시 `wifi.connected`로 게이팅.** 디스플레이 갱신·애니메이션·게임 로직은 WiFi 붙어있을 때만 돌린다. (WiFi 끊기면 CPU를 놔줘서 자가 재접속)
3. **생명줄 컴포넌트 절대 제거 금지:** `wifi`, `ota`, `safe_mode`, `api`, `web_server`, `captive_portal`.
4. **새 화면 코드는 느린 주기(5~10s)로 먼저** 올려 안정 확인 → 그 다음 주기를 조인다.
5. **RAM 감시:** 컴파일 후 `RAM %` 확인(현재 ~55%). 여유 힙 부족 = OOM 크래시. 큰 버퍼 조심.
6. **디스플레이 lambda는 가볍게:** `fill(0x000000)`(memset·빠름) 위주. fractional-framebuffer는 프래그먼트마다 lambda를 30번 재실행하므로 무거운 그리기는 ×30로 폭증한다.
7. **WiFi 자격증명·핀맵·INVON은 함부로 건드리지 않는다.** (핀맵/색상은 이미 검증됨)
8. **큰 변경은 한 번에 하나씩.** "safe_mode가 받쳐준다"를 믿되 남용하지 않는다.

## B. 펌웨어에 내장된 자동 복구책 (이미 적용됨)

| 장치 | 역할 |
|---|---|
| **`safe_mode` (boot_is_good_after 10min)** | 어떤 이유로든 재부팅 루프가 생기면 → 디스플레이 없이 **WiFi+OTA만** 켜는 최소모드로 진입 → **시리얼 없이 OTA로 재플래시**. 5분마다 재부팅하는 느린 루프도 결국 잡힘(~50분 내). |
| **`wifi.connected` 게이팅** | 화면이 WiFi를 굶기는 상황 자체를 원천 차단. WiFi 끊기면 화면 정지 → 라디오가 재접속 → 화면 재개(자가치유). |
| **`api: reboot_timeout: 0s`** | Home Assistant 없이 단독 실행해도 15분마다 리부팅하지 않음. |
| **폴백 AP** (`SmallTV-Ultra Fallback` / `smalltv12345`) | WiFi 자체가 안 되면 자기 핫스팟을 띄워 `192.168.4.1`로 접근 가능. |

### 왜 이걸로 "직접 접속 불가" 상황이 커버되나
- **나쁜 펌웨어가 크래시/재부팅을 유발** → `safe_mode`가 잡음 → OTA 복구.
- **크래시 없이 조용히 WiFi만 죽이는 경우**(우리가 겪은 데드락) → `wifi.connected` 게이팅으로 애초에 발생 안 함. 설령 블로킹이 심하면 하드웨어 워치독이 리셋 → 다시 `safe_mode`가 잡음.
- 두 안전장치가 함께 있으면 **사실상 OTA로 항상 돌아온다.**

## C. 최후의 수단 (내장책이 다 실패했을 때만)

- **시리얼 복구:** `firmware-v1.bin`(네트워크 전용) 보유.
  1. IO0(GPIO0)→GND, USB-C 재삽입 (다운로드 모드)
  2. `python -m esptool --port COM4 --before no-reset --after no-reset write-flash --flash-mode keep --flash-size keep 0x0 firmware-v1.bin`
  3. IO0 점퍼 제거 후 재부팅
- MAC `48:3f:da:03:08:67`, 플래시 4MB, ESP8266EX, CH340=COM4.

## D. 배포 체크리스트 (매번)

- [ ] `python -m esphome compile smalltv-ultra.yaml` → RAM/Flash % 확인
- [ ] 무거운 로직에 `wifi.connected` 게이팅 있는지
- [ ] `safe_mode`/`ota`/`wifi` 그대로 있는지
- [ ] `python -m esphome upload smalltv-ultra.yaml --device <IP>` (compile 먼저! upload 단독은 재컴파일 안 함)
- [ ] 업로드 후 30~60초 관찰: `uptime`이 60s를 넘겨 오르면 정상 (재부팅 루프 아님)
