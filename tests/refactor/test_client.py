import pytest
import os.path
import typing
import glob
import time
import tempfile
import simvue.client as svc
import simvue.run as sv_run
import simvue.filter as sv_filter


@pytest.mark.dependency
@pytest.mark.client
def test_get_events(create_test_run: tuple[sv_run.Run, dict]) -> None:
    client = svc.Client()
    assert client.get_events(run_id=create_test_run[1]["run_id"])


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize(
    "from_run", (True, False)
)
def test_get_alerts(create_test_run: tuple[sv_run.Run, dict], from_run: bool) -> None:
    client = svc.Client()

    if from_run:
        assert (
            len(client.get_alerts(run_id=create_test_run[1]["run_id"], critical_only=False)) == 5
        )
    else:
        assert client.get_alerts(names_only=True)


@pytest.mark.dependency
@pytest.mark.client
def test_get_run_id_from_name(create_test_run: tuple[sv_run.Run, dict]) -> None:
    client = svc.Client()
    assert (
        client.get_run_id_from_name(create_test_run[1]["run_name"])
        == create_test_run[1]["run_id"]
    )


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize("aggregate", (True, False), ids=("aggregate", "complete"))
def test_get_metric_values(
    create_test_run: tuple[sv_run.Run, dict], aggregate: bool
) -> None:
    client = svc.Client()
    time.sleep(0.5)
    _metrics_dict = client.get_metric_values(
        run_ids=[create_test_run[1]["run_id"]],
        metric_names=[create_test_run[1]["metrics"][0]],
        xaxis="step",
        aggregate=aggregate,
        output_format="dict",
    )
    assert _metrics_dict
    assert isinstance(_metrics_dict, dict)
    _first_entry: dict = next(iter(_metrics_dict.values()))
    assert create_test_run[1]["metrics"][0] in _metrics_dict.keys()
    if aggregate:
        _value_types = set(i[1] for i in _first_entry.keys())
        assert all(
            i in _value_types for i in ("average", "min", "max")
        ), f"Expected ('average', 'min', 'max') in {_value_types}"
    else:
        _runs = set(i[1] for i in _first_entry.keys())
        assert create_test_run[1]["run_id"] in _runs


@pytest.mark.dependency
@pytest.mark.client
def test_plot_metrics(create_test_run: tuple[sv_run.Run, dict]) -> None:
    try:
        import matplotlib
    except ImportError:
        pytest.skip("Plotting modules not found")

    client = svc.Client()
    client.plot_metrics(
        run_ids=[create_test_run[1]["run_id"]],
        metric_names=list(create_test_run[1]["metrics"]),
        xaxis="time",
    )


@pytest.mark.dependency
@pytest.mark.client
def test_get_artifacts(create_test_run: tuple[sv_run.Run, dict]) -> None:
    client = svc.Client()
    assert client.list_artifacts(create_test_run[1]["run_id"])
    assert client.get_artifact(create_test_run[1]["run_id"], name="test_attributes")


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize("file_id", (1, 2, 3), ids=lambda x: f"file_{x}")
def test_get_artifact_as_file(
    create_test_run: tuple[sv_run.Run, dict], file_id: int
) -> None:
    with tempfile.TemporaryDirectory() as tempd:
        client = svc.Client()
        client.get_artifact_as_file(
            create_test_run[1]["run_id"],
            name=create_test_run[1][f"file_{file_id}"],
            path=tempd,
        )
        assert create_test_run[1][f"file_{file_id}"] in [
            os.path.basename(i) for i in glob.glob(os.path.join(tempd, "*"))
        ]


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize("category", (None, "code", "input", "output"))
def test_get_artifacts_as_files(
    create_test_run: tuple[sv_run.Run, dict],
    category: typing.Literal["code", "input", "output"],
) -> None:
    with tempfile.TemporaryDirectory() as tempd:
        client = svc.Client()
        client.get_artifacts_as_files(
            create_test_run[1]["run_id"], category=category, path=tempd
        )
        files = [os.path.basename(i) for i in glob.glob(os.path.join(tempd, "*"))]
        if not category or category == "input":
            assert create_test_run[1]["file_1"] in files
        if not category or category == "output":
            assert create_test_run[1]["file_2"] in files
        if not category or category == "code":
            assert create_test_run[1]["file_3"] in files


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize(
    "filters",
    (None, "list", "object"),
    ids=("no_filters", "filter_list", "filter_object"),
)
def test_get_runs(
    create_test_run: tuple[sv_run.Run, dict], filters: typing.Optional[str]
) -> None:
    client = svc.Client()

    if filters == "list":
        filter_obj = ["has tag.simvue_client_unit_tests"]
    elif filters == "object":
        filter_obj = sv_filter.RunsFilter()
        filter_obj.has_tag("simvue_client_unit_tests")
    else:
        filter_obj = None

    assert client.get_runs(filters=filter_obj)


@pytest.mark.dependency
@pytest.mark.client
def test_get_run(create_test_run: tuple[sv_run.Run, dict]) -> None:
    client = svc.Client()
    assert client.get_run(run_id=create_test_run[1]["run_id"])


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize(
    "filters",
    (None, "list", "object"),
    ids=("no_filters", "filters_list", "filters_object"),
)
def test_get_folder(
    create_test_run: tuple[sv_run.Run, dict],
    filters: typing.Optional[str]
) -> None:
    if filters == "list":
        filter_object = ["path == /simvue_unit_testing"]
    elif filters == "object":
        filter_object = sv_filter.FoldersFilter()
        filter_object.has_path("/simvue_unit_testing")
    else:
        filter_object = None
    client = svc.Client()
    assert client.get_folders(filters=filter_object)
    assert client.get_folder(folder_path="/simvue_unit_testing")


@pytest.mark.dependency
@pytest.mark.client
def test_get_metrics_names(create_test_run: tuple[sv_run.Run, dict]) -> None:
    client = svc.Client()
    time.sleep(1)
    assert client.get_metrics_names(create_test_run[1]["run_id"])


PRE_DELETION_TESTS: list[str] = [
    "test_get_metrics",
    "test_get_runs",
    "test_get_run",
    "test_get_artifact_as_file",
    "test_get_artifacts_as_files",
    "test_get_folders",
    "test_get_metrics_names",
    "test_get_metrics_multiple",
    "test_plot_metrics",
    "test_get_run_id_from_name",
    "test_get_folder",
]


@pytest.mark.dependency
@pytest.mark.client(depends=PRE_DELETION_TESTS)
def test_run_deletion(create_test_run: tuple[sv_run.Run, dict]) -> None:
    run, run_data = create_test_run
    run.close()
    client = svc.Client()
    assert not client.delete_run(run_data["run_id"])


@pytest.mark.dependency
@pytest.mark.client(depends=PRE_DELETION_TESTS)
def test_runs_deletion(create_test_run: tuple[sv_run.Run, dict]) -> None:
    run, run_data = create_test_run
    run.close()
    client = svc.Client()
    assert len(client.delete_runs(run_data["folder"])) > 0


@pytest.mark.dependency
@pytest.mark.client(depends=PRE_DELETION_TESTS + ["test_runs_deletion"])
def test_folder_deletion(create_test_run: tuple[sv_run.Run, dict]) -> None:
    run, run_data = create_test_run
    run.close()
    client = svc.Client()
    # This test is called last, one run created so expect length 1
    assert len(client.delete_folder(run_data["folder"], remove_runs=True)) == 1


@pytest.mark.dependency
@pytest.mark.client
@pytest.mark.parametrize("aggregate", (True, False), ids=("aggregated", "normal"))
@pytest.mark.parametrize("output_format", ("dict", "dataframe"))
@pytest.mark.parametrize("xaxis", ("step", "time", "timestamp"))
def test_multiple_metric_retrieval(
    create_test_run: tuple[sv_run.Run, dict],
    aggregate: bool,
    output_format: typing.Literal["dict", "dataframe"],
    xaxis: typing.Literal["step", "time", "timestamp"],
) -> None:
    client = svc.Client()
    if output_format == "dataframe":
        try:
            import pandas
        except ImportError:
            pytest.skip(reason="Pandas not available")

    if aggregate and xaxis == "timestamp":
        with pytest.raises(AssertionError):
            client.get_metric_values(
                run_ids=[create_test_run[1]["run_id"]],
                metric_names=list(create_test_run[1]["metrics"]),
                xaxis=xaxis,
                aggregate=aggregate,
                output_format=output_format,
            )
        return

    client.get_metric_values(
        run_ids=[create_test_run[1]["run_id"]],
        metric_names=list(create_test_run[1]["metrics"]),
        xaxis=xaxis,
        aggregate=aggregate,
        output_format=output_format,
    )
