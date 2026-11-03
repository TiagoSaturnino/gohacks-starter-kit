import pytest
from src.scenarios import load_scenario

@pytest.fixture
def scenario():
    return load_scenario("ecommerce")