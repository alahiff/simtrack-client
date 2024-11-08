import pytest
import simvue
import time
import contextlib
import random
import threading
from multiprocessing import Process, Manager


@pytest.mark.scenario
def test_time_multi_run_create_threshold() -> None:
    start = time.time()
    runs: list[simvue.Run] = []
    for i in range(10):
        run = simvue.Run()
        run.init(
            f"test run {i}",
            tags=["test_benchmarking"],
            folder="/simvue_benchmark_testing",
            retention_period="1 hour"
        )
        runs.append(run)
    for run in runs:
        run.close()
    end = time.time()
    client = simvue.Client()
    with contextlib.suppress(RuntimeError):
        client.delete_runs("/simvue_benchmark_testing")
        client.delete_folder(
            "/simvue_benchmark_testing",
            remove_runs=False,
            allow_missing=True,
            recursive=True,
        )
    assert start - end < 60.0


@pytest.fixture
def run_deleter(request):
    ident_dict = {}

    def delete_run():
        simvue.Client().delete_run(ident_dict["ident"])

    request.addfinalizer(delete_run)
    return ident_dict

def upload(name: str, values_per_run: int, shared_dict) -> None:
    run = simvue.Run()
    run.init(name=name, tags=["simvue_client_tests"])
    shared_dict["ident"] = run._id
    for i in range(values_per_run):
        run.log_metrics({"increment": i})
    run.close()

@pytest.mark.scenario
@pytest.mark.parametrize("values_per_run", (1, 2, 100, 1500))
@pytest.mark.parametrize("processing", ("local", "on_thread", "on_process"))
def test_uploaded_data_immediately_accessible(
    values_per_run: int, processing: str, run_deleter
) -> None:
    name = "Test-" + str(random.randint(0, 1000000000))
    manager = Manager()
    shared_dict = manager.dict()

    if processing == "local":
        upload(name, values_per_run, shared_dict)
    else:
        if processing == "on_thread":
            thread = threading.Thread(
                target=upload, args=(name, values_per_run, shared_dict)
            )
        else:
            thread = Process(target=upload, args=(name, values_per_run, shared_dict))
        thread.start()
        thread.join()

    run_deleter["ident"] = shared_dict["ident"]

    values = simvue.Client().get_metric_values(
        ["increment"], "step", run_ids=[shared_dict["ident"]], max_points=2 * values_per_run, aggregate=False
    )["increment"]

    assert len(values) == values_per_run, "all uploaded values should be returned"

    for i in range(len(values)):
        assert i == int(values[(i, shared_dict["ident"])]), "values should be ascending ints"
