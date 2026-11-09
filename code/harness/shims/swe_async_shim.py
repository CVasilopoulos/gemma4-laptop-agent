import asyncio
import inspect

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {a: pyfuncitem.funcargs[a] for a in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
