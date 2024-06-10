"""
Simvue Client Executor
======================

Adds functionality for executing commands from the command line as part of a Simvue run, the executor
monitors the exit code of the command setting the status to failure if non-zero.
Stdout and Stderr are sent to Simvue as artifacts.
"""

__author__ = "Kristian Zarebski"
__date__ = "2023-11-15"

import logging
import multiprocessing.synchronize
import sys
import multiprocessing
import os
import psutil
import subprocess
import pathlib
import time
import typing

if typing.TYPE_CHECKING:
    import simvue

logger = logging.getLogger(__name__)


class CompletionCallback(typing.Protocol):
    def __call__(self, *, status_code: int, std_out: str, std_err: str) -> None: ...


def _execute_process(
    proc_id: str,
    command: typing.List[str],
    runner_name: str,
    environment: typing.Optional[typing.Dict[str, str]],
) -> subprocess.Popen:
    with open(f"{runner_name}_{proc_id}.err", "w") as err:
        with open(f"{runner_name}_{proc_id}.out", "w") as out:
            _result = subprocess.Popen(
                command,
                stdout=out,
                stderr=err,
                universal_newlines=True,
                env=environment,
            )

    return _result


class Executor:
    """Command Line command executor

    Adds execution of command line commands as part of a Simvue run, the status of these commands is monitored
    and if non-zero cause the Simvue run to be stated as 'failed'. The executor accepts commands either as a
    set of positional arguments or more specifically as components, two of these 'input_file' and 'script' then
    being used to set the relevant metadata within the Simvue run itself.
    """

    def __init__(self, simvue_runner: "simvue.Run", keep_logs: bool = True) -> None:
        """Initialise an instance of the Simvue executor attaching it to a Run.

        Parameters
        ----------
        simvue_runner : simvue.Run
            An instance of the Simvue runner used to send command execution feedback
        keep_logs : bool, optional
            whether to keep the stdout and stderr logs locally, by default False
        """
        self._runner = simvue_runner
        self._keep_logs = keep_logs
        self._completion_callbacks: dict[str, typing.Optional[CompletionCallback]] = {}
        self._completion_triggers: dict[
            str, typing.Optional[multiprocessing.synchronize.Event]
        ] = {}
        self._exit_codes: dict[str, int] = {}
        self._std_err: dict[str, str] = {}
        self._std_out: dict[str, str] = {}
        self._alert_ids: dict[str, str] = {}
        self._command_str: dict[str, str] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def add_process(
        self,
        identifier: str,
        *args,
        executable: typing.Optional[str] = None,
        script: typing.Optional[pathlib.Path] = None,
        input_file: typing.Optional[pathlib.Path] = None,
        env: typing.Optional[typing.Dict[str, str]] = None,
        completion_callback: typing.Optional[CompletionCallback] = None,
        completion_trigger: typing.Optional[multiprocessing.synchronize.Event] = None,
        **kwargs,
    ) -> None:
        """Add a process to be executed to the executor.

        This process can take many forms, for example a be a set of positional arguments:

        ```python
        executor.add_process("my_process", "ls", "-ltr")
        ```

        Provide explicitly the components of the command:

        ```python
        executor.add_process("my_process", executable="bash", debug=True, c="return 1")
        executor.add_process("my_process", executable="bash", script="my_script.sh", input="parameters.dat")
        ```

        or a mixture of both. In the latter case arguments which are not 'executable', 'script', 'input'
        are taken to be options to the command, for flags `flag=True` can be used to set the option and
        for options taking values `option=value`.

        When the process has completed if a function has been provided for the `completion_callback` argument
        this will be called, this callback is expected to take the following form:

        ```python
        def callback_function(status_code: int, std_out: str, std_err: str) -> None:
            ...
        ```

        Note `completion_callback` is not supported on Windows operating systems.

        Parameters
        ----------
        identifier : str
            A unique identifier for this process
        executable : str | None, optional
            the main executable for the command, if not specified this is taken to be the first
            positional argument, by default None
        script : str | None, optional
            the script to run, note this only work if the script is not an option, if this is the case
            you should provide it as such and perform the upload manually, by default None
        input_file : str | None, optional
            the input file to run, note this only work if the input file is not an option, if this is the case
            you should provide it as such and perform the upload manually, by default None
        env : typing.Dict[str, str], optional
            environment variables for process
        completion_callback : typing.Callable | None, optional
            callback to run when process terminates (not supported on Windows)
        completion_trigger : multiprocessing.Event | None, optional
            this trigger event is set when the processes completes
        """
        _pos_args = list(args)

        if not self._runner.name:
            raise RuntimeError("Cannot add process, expected Run instance to have name")

        if sys.platform == "win32" and completion_callback:
            logger.warning(
                "Completion callback for 'add_process' may fail on Windows due to "
                "due to function pickling restrictions"
            )

        if script:
            self._runner.save_file(file_path=script, category="code")

        if input_file:
            self._runner.save_file(file_path=input_file, category="input")

        _command: typing.List[str] = []

        if executable:
            _command += [f"{executable}"]
        else:
            _command += [_pos_args[0]]
            _pos_args.pop(0)

        if script:
            _command += [f"{script}"]

        if input_file:
            _command += [f"{input_file}"]

        for arg, value in kwargs.items():
            if arg.startswith("__"):
                continue

            arg = arg.replace("_", "-")

            if len(arg) == 1:
                if isinstance(value, bool) and value:
                    _command += [f"-{arg}"]
                else:
                    _command += [f"-{arg}", f"{value}"]
            else:
                if isinstance(value, bool) and value:
                    _command += [f"--{arg}"]
                else:
                    _command += [f"--{arg}", f"{value}"]

        _command += _pos_args

        self._command_str[identifier] = " ".join(_command)
        self._completion_callbacks[identifier] = completion_callback
        self._completion_triggers[identifier] = completion_trigger

        self._processes[identifier] = _execute_process(
            identifier, _command, self._runner.name, env
        )

        self._alert_ids[identifier] = self._runner.create_alert(
            name=f"{identifier}_exit_status", source="user"
        )

    @property
    def success(self) -> int:
        """Return whether all attached processes completed successfully"""
        return all(i == 0 for i in self._exit_codes.values())

    @property
    def exit_status(self) -> int:
        """Returns the first non-zero exit status if applicable"""
        _non_zero = [i for i in self._exit_codes.values() if i != 0]

        if _non_zero:
            return _non_zero[0]

        return 0

    def get_error_summary(self) -> dict[str, typing.Optional[str]]:
        """Returns the summary messages of all errors"""
        return {
            identifier: self._get_error_status(identifier)
            for identifier, value in self._exit_codes.items()
            if value
        }

    def get_command(self, process_id: str) -> str:
        """Returns the command executed within the given process.

        Parameters
        ----------
        process_id : str
            Unique identifier for the process

        Returns
        -------
        str
            command as a string
        """
        if process_id not in self._processes:
            raise KeyError(f"Failed to retrieve '{process_id}', no such process")
        return self._command_str[process_id]

    def _get_error_status(self, process_id: str) -> typing.Optional[str]:
        err_msg: typing.Optional[str] = None

        # Return last 10 lines of stdout if stderr empty
        if not (err_msg := self._std_err.get(process_id)) and (
            std_out := self._std_out.get(process_id)
        ):
            err_msg = "  Tail STDOUT:\n\n"
            start_index = -10 if len(lines := std_out.split("\n")) > 10 else 0
            err_msg += "\n".join(lines[start_index:])
        return err_msg

    def _update_alerts(self) -> None:
        """Send log events for the result of each process"""
        for proc_id, code in self._exit_codes.items():
            if code != 0:
                # If the process fails then purge the dispatcher event queue
                # and ensure that the stderr event is sent before the run closes
                if self._runner._dispatcher:
                    self._runner._dispatcher.purge()

                self._runner.log_alert(self._alert_ids[proc_id], "critical")
            else:
                self._runner.log_alert(self._alert_ids[proc_id], "ok")

            # Wait for the dispatcher to send the latest information before
            # allowing the executor to finish (and as such the run instance to exit)
            _wait_limit: float = 1
            _current_time: float = 0
            while (
                self._runner._dispatcher
                and not self._runner._dispatcher.empty
                and _current_time < _wait_limit
            ):
                time.sleep((_current_time := _current_time + 0.1))

    def _save_output(self) -> None:
        """Save the output to Simvue"""
        for proc_id in self._exit_codes.keys():
            # Only save the file if the contents are not empty
            if self._std_err.get(proc_id):
                self._runner.save_file(
                    f"{self._runner.name}_{proc_id}.err", category="output"
                )
            if self._std_out.get(proc_id):
                self._runner.save_file(
                    f"{self._runner.name}_{proc_id}.out", category="output"
                )

    def kill_process(self, process_id: str) -> None:
        """Kill a running process by ID"""
        if not (process := self._processes.get(process_id)):
            logger.error(
                f"Failed to terminate process '{process_id}', no such identifier."
            )
            return

        parent = psutil.Process(process.pid)

        for child in parent.children(recursive=True):
            logger.debug(f"Terminating child process {child.pid}: {child.name()}")
            child.kill()

        for child in parent.children(recursive=True):
            child.wait()

        logger.debug(f"Terminating child process {process.pid}: {process.args}")
        process.kill()
        process.wait()

        self._execute_callback(process_id)

    def kill_all(self) -> None:
        """Kill all running processes"""
        for process in self._processes.keys():
            self.kill_process(process)

    def _clear_cache_files(self) -> None:
        """Clear local log files if required"""
        if not self._keep_logs:
            for proc_id in self._exit_codes.keys():
                os.remove(f"{self._runner.name}_{proc_id}.err")
                os.remove(f"{self._runner.name}_{proc_id}.out")

    def _execute_callback(self, identifier: str) -> None:
        with open(f"{self._runner.name}_{identifier}.err") as err:
            std_err = err.read()

        with open(f"{self._runner.name}_{identifier}.out") as out:
            std_out = out.read()

        if callback := self._completion_callbacks.get(identifier):
            callback(
                status_code=self._processes[identifier].returncode,
                std_out=std_out,
                std_err=std_err,
            )
        if completion_trigger := self._completion_triggers.get(identifier):
            completion_trigger.set()

    def wait_for_completion(self) -> None:
        """Wait for all processes to finish then perform tidy up and upload"""
        for identifier, process in self._processes.items():
            process.wait()
            self._execute_callback(identifier)

        self._update_alerts()
        self._save_output()

        if not self.success:
            self._runner.set_status("failed")
        self._clear_cache_files()
