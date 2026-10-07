"""無料枠のレート制限（RPM / RPD）を超えないためのプロセス共有ガード。

- 直近 60 秒・直近 24 時間の呼び出し時刻をメモリ上に保持するだけで、個人情報は持たない。
- Streamlit では ``st.cache_resource`` で 1 プロセスに 1 個だけ生成し、全セッションで共有する。
- アプリの再起動でカウンタはリセットされる（Google 側の上限より保守的な値を設定して補う）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

MINUTE = 60.0
DAY = 24 * 60 * 60.0


@dataclass(frozen=True)
class AcquireResult:
    ok: bool
    wait_seconds: float = 0.0
    reason: str = ""


class RateGuard:
    def __init__(self, rpm: int, rpd: int, clock: Callable[[], float] = time.monotonic) -> None:
        if rpm < 1 or rpd < 1:
            raise ValueError("rpm and rpd must be >= 1")
        self.rpm = rpm
        self.rpd = rpd
        self._clock = clock
        self._lock = threading.Lock()
        self._minute: deque[float] = deque()
        self._day: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._minute and now - self._minute[0] >= MINUTE:
            self._minute.popleft()
        while self._day and now - self._day[0] >= DAY:
            self._day.popleft()

    def try_acquire(self) -> AcquireResult:
        """1 回分の呼び出し枠を確保する。確保できなければ待ち秒数を返す（ブロックしない）。"""
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._day) >= self.rpd:
                return AcquireResult(False, DAY - (now - self._day[0]), "rpd")
            if len(self._minute) >= self.rpm:
                return AcquireResult(False, MINUTE - (now - self._minute[0]), "rpm")
            self._minute.append(now)
            self._day.append(now)
            return AcquireResult(True)

    def usage(self) -> tuple[int, int]:
        """(直近 1 分の呼び出し数, 直近 24 時間の呼び出し数)"""
        with self._lock:
            self._prune(self._clock())
            return len(self._minute), len(self._day)
