import pytest
import sys
import time

import ray
from ray.exceptions import RuntimeEnvSetupError
from ray.runtime_env import RuntimeEnv, RuntimeEnvConfig


bad_runtime_env_cache_ttl_seconds = 10


@pytest.mark.skipif(
    sys.platform == "win32", reason="conda in runtime_env unsupported on Windows."
)
@pytest.mark.parametrize("runtime_env_class", [dict, RuntimeEnv])
@pytest.mark.parametrize(
    "set_bad_runtime_env_cache_ttl_seconds",
    [
        str(bad_runtime_env_cache_ttl_seconds),
    ],
    indirect=True,
)
def test_invalid_conda_env(
    shutdown_only, runtime_env_class, set_bad_runtime_env_cache_ttl_seconds
):
    ray.init()

    @ray.remote
    def f():
        pass

    @ray.remote
    class A:
        def f(self):
            pass

    start = time.time()
    bad_env = runtime_env_class(conda={"dependencies": ["this_doesnt_exist"]})
    with pytest.raises(
        RuntimeEnvSetupError,
        # The actual error message should be included in the exception.
        match="ResolvePackageNotFound",
    ):
        ray.get(f.options(runtime_env=bad_env).remote())
    first_time = time.time() - start

    # Check that another valid task can run.
    ray.get(f.remote())

    a = A.options(runtime_env=bad_env).remote()
    with pytest.raises(
        ray.exceptions.RuntimeEnvSetupError, match="ResolvePackageNotFound"
    ):
        ray.get(a.f.remote())

    # The second time this runs it should be faster as the error is cached.
    start = time.time()
    with pytest.raises(RuntimeEnvSetupError, match="ResolvePackageNotFound"):
        ray.get(f.options(runtime_env=bad_env).remote())

    assert (time.time() - start) < (first_time / 2.0)

    # Sleep to wait bad runtime env cache removed.
    time.sleep(bad_runtime_env_cache_ttl_seconds)

    # The third time this runs it should be slower as the error isn't cached.
    start = time.time()
    with pytest.raises(RuntimeEnvSetupError, match="ResolvePackageNotFound"):
        ray.get(f.options(runtime_env=bad_env).remote())

    assert (time.time() - start) > (first_time / 2.0)


def test_runtime_env_config(start_cluster):
    _, address = start_cluster
    bad_configs = []
    bad_configs.append({"setup_timeout_seconds": 10.0})
    bad_configs.append({"setup_timeout_seconds": 0})
    bad_configs.append({"setup_timeout_seconds": "10"})

    good_configs = []
    good_configs.append({"setup_timeout_seconds": 10})
    good_configs.append({"setup_timeout_seconds": -1})

    @ray.remote
    def f():
        return True

    def raise_exception_run(fun, *args, **kwargs):
        try:
            fun(*args, **kwargs)
        except Exception:
            pass
        else:
            assert False

    for bad_config in bad_configs:

        def run(runtime_env):
            raise_exception_run(ray.init, address, runtime_env=runtime_env)
            raise_exception_run(f.options, runtime_env=runtime_env)

        runtime_env = {"config": bad_config}
        run(runtime_env)

        raise_exception_run(RuntimeEnvConfig, **bad_config)
        raise_exception_run(RuntimeEnv, config=bad_config)

    for good_config in good_configs:

        def run(runtime_env):
            ray.shutdown()
            ray.init(address, runtime_env=runtime_env)
            assert ray.get(f.options(runtime_env=runtime_env).remote())

        runtime_env = {"config": good_config}
        run(runtime_env)
        runtime_env = {"config": RuntimeEnvConfig(**good_config)}
        run(runtime_env)
        runtime_env = RuntimeEnv(config=good_config)
        run(runtime_env)
        runtime_env = RuntimeEnv(config=RuntimeEnvConfig(**good_config))
        run(runtime_env)


@pytest.mark.parametrize(
    "call_ray_start",
    ["ray start --head --ray-client-server-port 25553"],
    indirect=True,
)
@pytest.mark.parametrize("use_client", [False, True])
def test_get_current_runtime_env(call_ray_start, use_client):
    job_runtime_env = {"env_vars": {"a": "b"}}

    if not use_client:
        address = call_ray_start
        ray.init(address, runtime_env=job_runtime_env)
    else:
        ray.init("ray://localhost:25553", runtime_env=job_runtime_env)

    current_runtime_env = ray.get_runtime_context().runtime_env
    current_runtime_env_2 = ray.get_runtime_context().runtime_env
    # Ensure we can get a new instance for update.
    assert current_runtime_env is not current_runtime_env_2
    assert isinstance(current_runtime_env, dict)
    assert current_runtime_env == job_runtime_env

    @ray.remote
    def get_runtime_env():
        return ray.get_runtime_context().runtime_env

    assert ray.get(get_runtime_env.remote()) == job_runtime_env

    task_runtime_env = {"env_vars": {"a": "c"}}
    assert (
        ray.get(get_runtime_env.options(runtime_env=task_runtime_env).remote())
        == task_runtime_env
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-sv", __file__]))
