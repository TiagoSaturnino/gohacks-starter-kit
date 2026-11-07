"""Circuit breaker mínimo para chamadas à API.

Sem ele, uma API fora do ar faz cada linha de um lote esperar o timeout completo
(50 linhas x 30 s = 25 minutos de tela travada). Com ele, após N falhas seguidas
as próximas análises falham rápido (fail-closed) até o período de espera acabar.
"""
import threading
import time
from typing import Callable, Dict, Tuple


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        reset_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = None

    @property
    def state(self) -> str:
        """CLOSED (normal), OPEN (bloqueando) ou HALF_OPEN (libera 1 tentativa de teste)."""
        if self._opened_at is None:
            return "CLOSED"
        if self._clock() - self._opened_at >= self.reset_after_s:
            return "HALF_OPEN"
        return "OPEN"

    def allow(self) -> bool:
        return self.state != "OPEN"

    def retry_in_s(self) -> int:
        if self._opened_at is None:
            return 0
        return max(0, int(self.reset_after_s - (self._clock() - self._opened_at) + 0.999))

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._opened_at is not None or self._failures >= self.failure_threshold:
                self._opened_at = self._clock()  # abre (ou reabre após tentativa de teste)


_BREAKERS: Dict[Tuple[int, float], CircuitBreaker] = {}


def get_breaker(failure_threshold: int, reset_after_s: float) -> CircuitBreaker:
    """Instância compartilhada por processo (as sessões do Streamlit dividem o estado)."""
    key = (failure_threshold, reset_after_s)
    if key not in _BREAKERS:
        _BREAKERS[key] = CircuitBreaker(failure_threshold, reset_after_s)
    return _BREAKERS[key]


def reset_breakers() -> None:
    _BREAKERS.clear()
