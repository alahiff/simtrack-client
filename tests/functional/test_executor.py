import typing
import pytest
import simvue
import time
import sys
import tempfile
import pathlib
import os
import multiprocessing
import multiprocessing.synchronize
    

@pytest.mark.executor
@pytest.mark.parametrize("successful", (True, False), ids=("successful", "failing"))
def test_executor_add_process(
    successful: bool,
    request: pytest.FixtureRequest
) -> None:
    import logging
    trigger = multiprocessing.Event()

    def completion_callback(*_, trigger=trigger, **__):
        trigger.set()
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    run = simvue.Run()
    run.init(
        f"test_executor_{'success' if successful else 'fail'}",
        tags=["simvue_client_unit_tests", request.node.name.replace("[", "_").replace("]", "_")],
        folder="/simvue_unit_testing"
    )
    run.add_process(
        identifier=f"test_add_process_{'success' if successful else 'fail'}",
        c=f"exit {0 if successful else 1}",
        executable="bash" if sys.platform != "win32" else "powershell",
        completion_callback=completion_callback
    )

    while not trigger.is_set():
        time.sleep(1)

    if successful:
        run.close()
    else:
        with pytest.raises(SystemExit):
            run.close()


@pytest.mark.executor
@pytest.mark.unix
def test_executor_multiprocess(request: pytest.FixtureRequest) -> None:
    triggers: dict[int, multiprocessing.synchronize.Event] = {}
    callbacks: dict[int, typing.Callable] = {}
    events: dict[int, bool] = {}
    with tempfile.TemporaryDirectory() as tempd:
        with simvue.Run() as run:
            run.init(
                "test_executor_multiprocess",
                folder="/simvue_unit_testing",
                tags=["simvue_client_tests", request.node.name]
            )

            for i in range(10):
                def callback(*_, evts=events, ident=i, **__):
                    evts[ident] = True
                events[i] = False
                triggers[i] = multiprocessing.Event()
                callbacks[i] = callback
                out_file = pathlib.Path(tempd).joinpath(f"out_file_{i}.dat")
                run.add_process(
                    f"cmd_{i}",
                    executable="bash",
                    c="for i in {0..10}; do sleep 0.5; echo $i >> "+ f"{out_file}; done",
                    completion_trigger=triggers[i],
                    completion_callback=callbacks[i]
                )
            time.sleep(1)
        for i in range(10):
            out_file = pathlib.Path(tempd).joinpath(f"out_file_{i}.dat")
            assert events[i]
            assert out_file.exists()
            assert triggers[i].is_set()
    for i in range(10):
        os.remove(f"test_executor_multiprocess_cmd_{i}.err")
        os.remove(f"test_executor_multiprocess_cmd_{i}.out")


@pytest.mark.executor
def test_add_process_command_assembly(request: pytest.FixtureRequest) -> None:
    with tempfile.TemporaryDirectory() as tempd:
        _python_script="""
import argparse
import os.path

parser = argparse.ArgumentParser()
parser.add_argument('input_file')
parser.add_argument('--output-file')

args=parser.parse_args()

in_text = open(args.input_file).read()

with open(args.output_file, 'w') as out_f:
    out_f.write(in_text)
    out_f.write('End of Line.')
"""
        with (in_file := pathlib.Path(tempd).joinpath("input.txt")).open("w") as out_f:
            out_f.write("Flynn has entered the grid.\n")

        with (code_file := pathlib.Path(tempd).joinpath("demo.py")).open("w") as out_f:
            out_f.write(_python_script)

        out_file = pathlib.Path(tempd).joinpath("output.txt")
        expected_cmd = f"python {code_file} {in_file} --output-file {out_file}"

        with simvue.Run() as run:
            run.init(
                "test_advanced_executor",
                folder="/simvue_unit_testing",
                tags=["simvue_client_tests", request.node.name]
            )
            run.add_process(
                identifier=(exe_id := "advanced_run"),
                executable="python",
                script=f"{code_file}",
                input_file=f"{in_file}",
                output_file=out_file
            )
            assert run._executor.command_str[exe_id] == expected_cmd

@pytest.mark.executor
def test_completion_callbacks_var_change(request: pytest.FixtureRequest) -> None:
    success: dict[str, bool] = {"complete": False}
    def completion_callback(*_, success: dict[str, bool]=success, **__):
        success["complete"] = True

    with simvue.Run() as run:
        run.init(
            "test_completion_callbacks_var_change",
            folder="/simvue_unit_testing",
            tags=["simvue_client_tests", request.node.name]
        )
        run.add_process(
            identifier="test_completion_callbacks_var_change",
            executable="bash",
            c="exit 0",
            completion_callback=completion_callback
        )

    # Need a slight delay before checking
    time.sleep(1)

    assert success["complete"]

@pytest.mark.executor
@pytest.mark.unix
def test_completion_trigger_set(request: pytest.FixtureRequest) -> None:
    trigger = multiprocessing.Event()

    with simvue.Run() as run:
        run.init(
            "test_completion_trigger_set",
            folder="/simvue_unit_testing",
            tags=["simvue_client_tests", request.node.name]
        )
        run.add_process(
            identifier="test_completion_trigger_set",
            executable="bash",
            c="exit 0",
            completion_trigger=trigger
        )

    # Need a slight delay before checking
    time.sleep(1)

    assert trigger.is_set()

@pytest.mark.executor
def test_completion_callbacks_trigger_set(request: pytest.FixtureRequest) -> None:
    trigger = multiprocessing.Event()

    def completion_callback(*_, trigger=trigger, **__):
        trigger.set()

    with simvue.Run() as run:
        run.init(
            "test_completion_callbacks_trigger_set",
            folder="/simvue_unit_testing",
            tags=["simvue_client_tests", request.node.name]
        )
        run.add_process(
            identifier="test_completion_callbacks_trigger_set",
            executable="bash",
            c="exit 0",
            completion_callback=completion_callback
        )

    # Need a slight delay before checking
    time.sleep(1)

    assert trigger.is_set()

