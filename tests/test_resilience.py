from src.resilience import CircuitBreaker, get_breaker, reset_breakers


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_opens_after_threshold_and_blocks():
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=3, reset_after_s=60, clock=clock)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.state == "CLOSED" and breaker.allow()
    breaker.record_failure()
    assert breaker.state == "OPEN" and not breaker.allow()
    assert breaker.retry_in_s() == 60


def test_half_open_after_reset_then_closes_on_success():
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=60, clock=clock)
    breaker.record_failure()
    clock.now = 61
    assert breaker.state == "HALF_OPEN" and breaker.allow()
    breaker.record_success()
    assert breaker.state == "CLOSED"


def test_failure_in_half_open_reopens():
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=1, reset_after_s=60, clock=clock)
    breaker.record_failure()
    clock.now = 61
    breaker.record_failure()
    assert breaker.state == "OPEN"
    clock.now = 100
    assert breaker.state == "OPEN"  # a contagem recomeçou na reabertura
    clock.now = 122
    assert breaker.state == "HALF_OPEN"


def test_get_breaker_is_shared_per_config():
    reset_breakers()
    assert get_breaker(3, 60.0) is get_breaker(3, 60.0)
    assert get_breaker(3, 60.0) is not get_breaker(5, 60.0)
    reset_breakers()
