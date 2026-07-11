"""Push live CPU / RAM usage to the PC Info page.

    pip install psutil
    python pc_stats.py [device_ip]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smalltv import SmallTV

import psutil

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "192.168.219.112"


def main():
    tv = SmallTV(DEVICE)
    tv.set_mode("PC Info")
    print(f"pushing PC stats -> {DEVICE} (mode=PC Info), Ctrl+C to stop.")
    while True:
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        tv.lines(
            title="PC Monitor",
            l1=f"CPU  {cpu:4.0f} %",
            l2=f"RAM  {mem:4.0f} %",
            l3=time.strftime("%H:%M:%S"),
            switch=False,
        )
        time.sleep(2)


if __name__ == "__main__":
    main()
