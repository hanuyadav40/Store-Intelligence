"""
Shared fixtures for the test suite.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.services.state_manager import StateManager


@pytest.fixture(scope="function")
def fresh_state(monkeypatch):
    """
    Provide a clean StateManager for each test.
    Monkeypatches state_manager in all modules that import it.
    """
    sm = StateManager()
    # Patch everywhere it is imported
    import app.services.state_manager as sm_module
    import app.api.routes as routes_module
    import analytics.metrics as metrics_module
    import analytics.funnel as funnel_module
    import analytics.anomalies as anomalies_module

    monkeypatch.setattr(sm_module, "state_manager", sm)
    monkeypatch.setattr(routes_module, "state_manager", sm)
    monkeypatch.setattr(metrics_module, "state_manager", sm)
    monkeypatch.setattr(funnel_module, "state_manager", sm)
    monkeypatch.setattr(anomalies_module, "state_manager", sm)
    return sm


@pytest.fixture(scope="function")
def app_instance(fresh_state, monkeypatch):
    """
    FastAPI test app with video processor disabled (no background threads).
    """
    import app.services.video_processor as vp_module

    class _NoopOrchestrator:
        def start(self): pass
        def stop(self): pass

    monkeypatch.setattr(vp_module, "orchestrator", _NoopOrchestrator())
    return create_app()


@pytest_asyncio.fixture
async def async_client(app_instance):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
