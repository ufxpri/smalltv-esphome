# smalltv — PC client library

Tiny, dependency-free Python client to drive a SmallTV-Ultra running this
firmware from a PC. It wraps the device's `web_server` REST API, so anything it
does also works from `curl` or any language.

```python
from smalltv import SmallTV
tv = SmallTV("192.168.219.112")     # device IP (or hostname)

tv.set_mode("Clock")                 # switch page (must be built into the firmware)
tv.backlight(0.5)                    # 0.0 – 1.0

# PC Info page — generic title + 3 lines
tv.lines(title="Build", l1="tests: pass", l2="deploy: ok")

# Stocks page
tv.stock(ticker="AAPL", price="192.34", change="+1.2%")

# Generic — any entity the firmware exposes
tv.set_text("line1", "hello")
tv.set_select("mode", "Off")
```

Pages must be included in the uploaded build (see `tools/build.py`). `set_mode`
only accepts pages that are actually flashed.

## Examples (`examples/`)
| script | what it does | needs |
|---|---|---|
| `stock_bridge.py` | reads the ticker typed in the device web UI, fetches live quotes, pushes price/change | — |
| `pc_stats.py` | streams CPU/RAM to the PC Info page | `pip install psutil` |

```sh
python examples/stock_bridge.py 192.168.219.112
python examples/pc_stats.py     192.168.219.112
```

## REST cheat-sheet (no Python needed)
```sh
curl -X POST 'http://<ip>/select/mode/set?option=Stocks'
curl -X POST 'http://<ip>/text/ticker/set?value=TSLA'
curl -X POST 'http://<ip>/light/backlight/turn_on?brightness=128'
curl        'http://<ip>/text/ticker'        # read current value
```
