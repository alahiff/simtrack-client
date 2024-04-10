"""
Simvue Run
==========

Main class for recording metrics and information to Simvue during code execution.
This forms the central API for users.
"""

import contextlib
import datetime
import json
import logging
import mimetypes
import threading
import os
import re
import sys
import time
import typing
import uuid
from datetime import timezone

import click
import msgpack
import psutil
from pydantic import ValidationError

import simvue.api as sv_api

from .dispatch import Dispatcher
from .executor import Executor
from .factory import Simvue
from .metrics import get_gpu_metrics, get_process_cpu, get_process_memory
from .models import RunInput
from .serialization import Serializer
from .system import get_system
from .utilities import (
    calculate_sha256,
    compare_alerts,
    create_file,
    get_auth,
    get_expiry,
    get_offline_directory,
    validate_timestamp,
)

INIT_MISSING = "initialize a run using init() first"
QUEUE_SIZE = 10000
UPLOAD_TIMEOUT = 30
HEARTBEAT_INTERVAL: int = 60
RESOURCES_METRIC_PREFIX: str = "resources"

logger = logging.getLogger(__name__)


def walk_through_files(path):
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            yield os.path.join(dirpath, filename)


class Run:
    """
    Track simulation details based on token and URL
    """

    def __init__(self, mode="online"):
        self._uuid = str(uuid.uuid4())
        self._mode = mode
        self._name = None
        self._executor = Executor(self)
        self._dispatcher = None
        self._id = None
        self._suppress_errors = False
        self._queue_blocking = False
        self._status = None
        self._upload_time_log = None
        self._upload_time_event = None
        self._data = []
        self._events = []
        self._step = 0
        self._queue_size = QUEUE_SIZE
        self._metrics_queue = None
        self._events_queue = None
        self._active = False
        self._url, self._token = get_auth()
        self._headers = {"Authorization": f"Bearer {self._token}"}
        self._simvue = None
        self._pid = 0
        self._resources_metrics_interval = 30
        self._shutdown_event = None
        self._heartbeat_termination_trigger = None
        self._storage_id = None
        self._heartbeat_thread = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self._executor.wait_for_completion()
        identifier = self._id
        logger.debug(
            "Automatically closing run %s in status %s", identifier, self._status
        )

        if self._heartbeat_thread:
            self._heartbeat_termination_trigger.set()
            self._heartbeat_thread.join()

        if (self._id or self._mode == "offline") and self._status == "running":
            if not type:
                if self._shutdown_event is not None:
                    self._shutdown_event.set()
                if self._dispatcher:
                    self._dispatcher.join()
                self.set_status("completed")
            else:
                if self._active:
                    self.log_event(f"{type.__name__}: {value}")
                if type.__name__ in ("KeyboardInterrupt") and self._active:
                    self.set_status("terminated")
                else:
                    if traceback and self._active:
                        self.log_event(f"Traceback: {traceback}")
                        self.set_status("failed")
        else:
            if self._shutdown_event is not None:
                self._shutdown_event.set()
            if self._dispatcher:
                self._dispatcher.purge()
                self._dispatcher.join()

        if _non_zero := self.executor.exit_status:
            logger.error(
                f"Simvue process executor terminated with non-zero exit status {_non_zero}"
            )
            sys.exit(_non_zero)

    def _check_token(self):
        """
        Check if token is valid
        """
        if self._mode == "online" and time.time() - get_expiry(self._token) > 0:
            self._error("token has expired or is invalid")

    @property
    def duration(self) -> float:
        return time.time() - self._start_time

    @property
    def time_stamp(self) -> str:
        return datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

    @property
    def processes(self) -> list[psutil.Process]:
        """
        Create an array containing a list of processes
        """
        if not self._parent_process:
            return []

        _all_processes: list[psutil.Process] = [self._parent_process]

        with contextlib.suppress((psutil.NoSuchProcess, psutil.ZombieProcess)):
            for child in self._parent_process.children(recursive=True):
                if child not in _all_processes:
                    _all_processes.append(child)

        return list(set(_all_processes))

    def _get_sysinfo(self) -> dict[str, typing.Any]:
        cpu = get_process_cpu(self.processes)
        memory = get_process_memory(self.processes)
        gpu = get_gpu_metrics(self.processes)
        data = {}

        if memory is not None and cpu is not None:
            data = {
                f"{RESOURCES_METRIC_PREFIX}/cpu.usage.percent": cpu,
                f"{RESOURCES_METRIC_PREFIX}/memory.usage": memory,
            }
            if gpu:
                for item in gpu:
                    data[item] = gpu[item]
        return data

    def _create_heartbeat_callback(
        self,
    ) -> typing.Callable[[str, dict, str, bool], None]:
        def _heartbeat(
            url: str = self._url,
            headers: dict[str, str] = self._headers,
            run_id: str = self._id,
            online: bool = self._mode == "online",
            heartbeat_trigger: threading.Event = self._heartbeat_termination_trigger,
        ) -> None:
            last_heartbeat = time.time()

            # Get the system metrics once before looping
            self._add_metrics_to_dispatch(self._get_sysinfo())

            # This loop is run in a daemon thread so termination occurs when
            # parent closes
            while not heartbeat_trigger.is_set():
                time.sleep(0.1)

                if time.time() - last_heartbeat < HEARTBEAT_INTERVAL:
                    continue

                last_heartbeat = time.time()

                # System metrics are appended to the queue at an interval
                # equivalent to the heartbeat interval
                # Hard coded step to 0 for resource metrics so that user
                # logged metrics dont appear to 'skip' steps
                self._add_metrics_to_dispatch(self._get_sysinfo())

                if online:
                    _data = {"id": run_id}
                    sv_api.put(f"{url}/api/runs/heartbeat", headers=headers, data=_data)
                else:
                    create_file(os.path.join(get_offline_directory(), "heartbeat"))

        return _heartbeat

    def _create_dispatch_callback(
        self,
    ) -> typing.Callable[[list[typing.Any], str, dict[str, typing.Any]], None]:
        """Generates the relevant callback for posting of metrics and events

        The generated callback is assigned to the dispatcher instance and is
        executed on metrics and events objects held in a buffer.
        """

        if not self._uuid:
            raise RuntimeError("Expected unique identifier for run")

        def _offline_dispatch_callback(
            buffer: list[typing.Any],
            category: str,
            run_id=self._id,
            uuid: str = self._uuid,
        ) -> None:
            if not os.path.exists((_offline_directory := get_offline_directory())):
                logger.error(
                    f"Cannot write to offline directory '{_offline_directory}', directory not found."
                )
                return
            _directory = os.path.join(_offline_directory, uuid)

            unique_id = time.time()
            filename = f"{_directory}/{category}-{unique_id}"
            _data = {category: buffer, "run": run_id}
            try:
                with open(filename, "w") as fh:
                    json.dump(_data, fh)
            except Exception as err:
                if self._suppress_errors:
                    logger.error(
                        "Got exception writing offline update for %s: %s",
                        category,
                        str(err),
                    )
                else:
                    raise err

        def _online_dispatch_callback(
            buffer: list[typing.Any],
            category: str,
            url=self._url,
            run_id=self._id,
            headers=self._headers,
        ) -> None:
            if not buffer:
                return
            _data = {category: buffer, "run": run_id}
            _data_bin = msgpack.packb(_data, use_bin_type=True)
            _url: str = f"{url}/api/{category}"

            _msgpack_header = headers | {"Content-Type": "application/msgpack"}

            sv_api.post(
                url=_url, headers=_msgpack_header, data=_data_bin, is_json=False
            )

        return (
            _online_dispatch_callback
            if self._mode == "online"
            else _offline_dispatch_callback
        )

    def _start(self, reconnect=False):
        """
        Start a run
        """
        if self._mode == "disabled":
            return True

        if self._mode != "offline":
            self._uuid = "notused"

        logger.debug("Starting run")

        self._check_token()

        data = {"status": self._status}

        if reconnect:
            data["system"] = get_system()

            if not self._simvue.update(data):
                return False

        self._start_time = time.time()

        if self._pid == 0:
            self._pid = os.getpid()

        self._parent_process = psutil.Process(self._pid) if self._pid else None

        self._shutdown_event = threading.Event()
        self._heartbeat_termination_trigger = threading.Event()

        self._dispatcher = Dispatcher(
            termination_trigger=self._shutdown_event,
            queue_blocking=self._queue_blocking,
            queue_categories=["events", "metrics"],
            callback=self._create_dispatch_callback(),
        )

        self._heartbeat_thread = threading.Thread(
            target=self._create_heartbeat_callback(), daemon=True
        )

        self._dispatcher.start()
        self._heartbeat_thread.start()

        self._active = True

    def _error(self, message):
        """
        Raise an exception if necessary and log error
        """
        if not self._suppress_errors:
            raise RuntimeError(message)
        else:
            logger.error(message)

    def init(
        self,
        name=None,
        metadata={},
        tags=[],
        description=None,
        folder="/",
        running=True,
        ttl=-1,
    ):
        """
        Initialise a run
        """
        if self._mode not in ("online", "offline", "disabled"):
            self._error("invalid mode specified, must be online, offline or disabled")

        if self._mode == "disabled":
            return True

        if not self._token or not self._url:
            self._error(
                "Unable to get URL and token from environment variables or config file"
            )

        if name:
            if not re.match(r"^[a-zA-Z0-9\-\_\s\/\.:]+$", name):
                self._error("specified name is invalid")

        self._name = name

        if running:
            self._status = "running"
        else:
            self._status = "created"

        data = {
            "metadata": metadata,
            "tags": tags,
            "system": {"cpu": {}, "gpu": {}, "platform": {}},
            "status": self._status,
            "ttl": ttl,
        }

        if name:
            data["name"] = name

        if description:
            data["description"] = description

        data["folder"] = folder

        if self._status == "running":
            data["system"] = get_system()
        elif self._status == "created":
            del data["system"]

        self._check_token()

        # compare with pydantic RunInput model
        try:
            RunInput(**data)
        except ValidationError as err:
            self._error(err)

        self._simvue = Simvue(self._name, self._uuid, self._mode, self._suppress_errors)
        name, self._id = self._simvue.create_run(data)

        if not name:
            return False
        elif name is not True:
            self._name = name

        if self._status == "running":
            self._start()

        if self._mode == "online":
            click.secho(f"[simvue] Run {self._name} created", bold=True, fg="green")
            click.secho(
                f"[simvue] Monitor in the UI at {self._url}/dashboard/runs/run/{self._id}",
                bold=True,
                fg="green",
            )

        return True

    def add_process(
        self,
        identifier: str,
        *cmd_args,
        executable: typing.Optional[str] = None,
        script: typing.Optional[str] = None,
        input_file: typing.Optional[str] = None,
        completion_callback: typing.Optional[
            typing.Callable[[int, int, str], None]
        ] = None,
        env: typing.Optional[typing.Dict[str, str]] = None,
        **cmd_kwargs,
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

        Parameters
        ----------
        identifier : str
            A unique identifier for this process
        executable : str | None, optional
            the main executable for the command, if not specified this is taken to be the first
            positional argument, by default None
        *positional_arguments
            all other positional arguments are taken to be part of the command to execute
        script : str | None, optional
            the script to run, note this only work if the script is not an option, if this is the case
            you should provide it as such and perform the upload manually, by default None
        input_file : str | None, optional
            the input file to run, note this only work if the input file is not an option, if this is the case
            you should provide it as such and perform the upload manually, by default None
        completion_callback : typing.Callable | None, optional
            callback to run when process terminates
        env : typing.Dict[str, str], optional
            environment variables for process
        **kwargs
            all other keyword arguments are interpreted as options to the command
        """
        _cmd_list: typing.List[str] = []
        _pos_args = list(cmd_args)

        # Assemble the command for saving to metadata as string
        if executable:
            _cmd_list += [executable]
        else:
            _cmd_list += [_pos_args[0]]
            executable = _pos_args[0]
            _pos_args.pop(0)

        for kwarg, val in cmd_kwargs.items():
            if len(kwarg) == 1:
                if isinstance(val, bool) and val:
                    _cmd_list += [f"-{kwarg}"]
                else:
                    _cmd_list += [f"-{kwarg}{(' '+val) if val else ''}"]
            else:
                if isinstance(val, bool) and val:
                    _cmd_list += [f"--{kwarg}"]
                else:
                    _cmd_list += [f"--{kwarg}{(' '+val) if val else ''}"]

        _cmd_list += _pos_args
        _cmd_str = " ".join(_cmd_list)

        # Store the command executed in metadata
        self.update_metadata({f"{identifier}_command": _cmd_str})

        # Add the process to the executor
        self._executor.add_process(
            identifier,
            *_pos_args,
            executable=executable,
            script=script,
            input_file=input_file,
            completion_callback=completion_callback,
            env=env,
            **cmd_kwargs,
        )

    def kill_process(self, process_id: str) -> None:
        """Kill a running process by ID

        Parameters
        ----------
        process_id : str
            the unique identifier for the added process
        """
        self._executor.kill_process(process_id)

    def kill_all_processes(self) -> None:
        """Kill all currently running processes."""
        self._executor.kill_all()

    @property
    def executor(self) -> Executor:
        """Return the executor for this run"""
        return self._executor

    @property
    def name(self):
        """
        Return the name of the run
        """
        return self._name

    @property
    def uid(self):
        """
        Return the local unique identifier of the run
        """
        return self._uuid

    @property
    def id(self):
        """
        Return the unique id of the run
        """
        return self._id

    def reconnect(self, run_id, uid=None):
        """
        Reconnect to a run in the created state
        """
        if self._mode == "disabled":
            return True

        self._status = "running"
        self._uuid = uid

        self._id = run_id
        self._simvue = Simvue(self._name, self._id, self._mode, self._suppress_errors)
        self._start(reconnect=True)

    def set_pid(self, pid):
        """
        Set pid of process to be monitored
        """
        self._pid = pid

    def config(
        self,
        suppress_errors=False,
        queue_blocking=False,
        queue_size=QUEUE_SIZE,
        disable_resources_metrics=False,
        resources_metrics_interval=30,
        storage_id=None,
    ):
        """
        Optional configuration
        """
        if not isinstance(suppress_errors, bool):
            self._error("suppress_errors must be boolean")
        self._suppress_errors = suppress_errors

        if not isinstance(queue_blocking, bool):
            self._error("queue_blocking must be boolean")
        self._queue_blocking = queue_blocking

        if not isinstance(queue_size, int):
            self._error("queue_size must be an integer")
        self._queue_size = queue_size

        if not isinstance(disable_resources_metrics, bool):
            self._error(
                f"disable_resources_metrics must be boolean, but got '{disable_resources_metrics}'"
            )

        if disable_resources_metrics:
            self._pid = None

        if not isinstance(resources_metrics_interval, int):
            self._error("resources_metrics_interval must be an integer")
        self._resources_metrics_interval = resources_metrics_interval

        if storage_id:
            self._storage_id = storage_id

    def update_metadata(self, metadata):
        """
        Add/update metadata
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not isinstance(metadata, dict):
            self._error("metadata must be a dict")
            return False

        data = {"metadata": metadata}

        if self._simvue.update(data):
            return True

        return False

    def update_tags(self, tags):
        """
        Add/update tags
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        data = {"tags": tags}

        if self._simvue.update(data):
            return True

        return False

    def log_event(self, message, timestamp=None):
        """
        Write event
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Run is not active")
            return False

        if self._status != "running":
            self._error("Cannot log events when not in the running state")
            return False

        if timestamp and not validate_timestamp(timestamp):
            self._error("Invalid timestamp format")
            return False

        _data = {"message": message, "timestamp": timestamp or self.time_stamp}
        self._dispatcher.add_item(_data, "events", self._queue_blocking)

        return True

    def _add_metrics_to_dispatch(self, metrics, step=None, time=None, timestamp=None):
        if self._mode == "disabled":
            return True

        # If there are no metrics to log just ignore
        if not metrics:
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Run is not active")
            return False

        if self._status != "running":
            self._error("Cannot log metrics when not in the running state")
            return False

        if not isinstance(metrics, dict) and not self._suppress_errors:
            self._error("Metrics must be a dict")
            return False

        if timestamp and not validate_timestamp(timestamp):
            self._error("Invalid timestamp format")
            return False

        _data: dict[str, typing.Any] = {
            "values": metrics,
            "time": time if time is not None else self.duration,
            "timestamp": timestamp if timestamp is not None else self.time_stamp,
            "step": step if step is not None else self._step,
        }
        self._dispatcher.add_item(_data, "metrics", self._queue_blocking)

        return True

    def log_metrics(self, metrics, step=None, time=None, timestamp=None):
        """
        Write metrics
        """
        self._add_metrics_to_dispatch(
            metrics, step=step, time=time, timestamp=timestamp
        )
        self._step += 1

    def save(
        self,
        filename,
        category,
        filetype=None,
        preserve_path=False,
        name=None,
        allow_pickle=False,
    ):
        """
        Upload file or object
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if self._status == "created" and category == "output":
            self._error("Cannot upload output files for runs in the created state")
            return False

        is_file = False
        if isinstance(filename, str):
            if not os.path.isfile(filename):
                self._error(f"File {filename} does not exist")
                return False
            else:
                is_file = True

        if filetype:
            mimetypes_valid = ["application/vnd.plotly.v1+json"]
            mimetypes.init()
            for _, value in mimetypes.types_map.items():
                mimetypes_valid.append(value)

            if filetype not in mimetypes_valid:
                self._error("Invalid MIME type specified")
                return False

        data = {}
        if preserve_path:
            data["name"] = filename
            if data["name"].startswith("./"):
                data["name"] = data["name"][2:]
        elif is_file:
            data["name"] = os.path.basename(filename)

        if name:
            data["name"] = name

        data["run"] = self._name
        data["category"] = category

        if is_file:
            data["size"] = os.path.getsize(filename)
            data["originalPath"] = os.path.abspath(
                os.path.expanduser(os.path.expandvars(filename))
            )
            data["checksum"] = calculate_sha256(filename, is_file)

            if data["size"] == 0:
                click.secho(
                    "WARNING: saving zero-sized files not currently supported",
                    bold=True,
                    fg="yellow",
                )
                return True

        # Determine mimetype
        mimetype = None
        if not filetype and is_file:
            mimetypes.init()
            mimetype = mimetypes.guess_type(filename)[0]
            if not mimetype:
                mimetype = "application/octet-stream"
        elif is_file:
            mimetype = filetype

        if mimetype:
            data["type"] = mimetype

        if not is_file:
            data["pickled"], data["type"] = Serializer().serialize(
                filename, allow_pickle
            )
            if not data["type"] and not allow_pickle:
                self._error("Unable to save Python object, set allow_pickle to True")
            data["checksum"] = calculate_sha256(data["pickled"], False)
            data["originalPath"] = ""
            data["size"] = sys.getsizeof(data["pickled"])

        if self._storage_id:
            data["storage"] = self._storage_id

        # Register file
        if not self._simvue.save_file(data):
            return False

        return True

    def save_directory(self, directory, category, filetype=None, preserve_path=False):
        """
        Upload a whole directory
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not os.path.isdir(directory):
            self._error(f"Directory {directory} does not exist")
            return False

        if filetype:
            mimetypes_valid = []
            mimetypes.init()
            for _, value in mimetypes.types_map.items():
                mimetypes_valid.append(value)

            if filetype not in mimetypes_valid:
                self._error("Invalid MIME type specified")
                return False

        for filename in walk_through_files(directory):
            if os.path.isfile(filename):
                self.save(filename, category, filetype, preserve_path)

        return True

    def save_all(self, items, category, filetype=None, preserve_path=False):
        """
        Save the list of files and/or directories
        """
        if self._mode == "disabled":
            return True

        for item in items:
            if os.path.isfile(item):
                self.save(item, category, filetype, preserve_path)
            elif os.path.isdir(item):
                self.save_directory(item, category, filetype, preserve_path)
            else:
                self._error(f"{item}: No such file or directory")

    def set_status(self, status):
        """
        Set run status
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Run is not active")
            return False

        if status not in ("completed", "failed", "terminated"):
            self._error("invalid status")

        data = {"name": self._name, "status": status}
        self._status = status

        if self._simvue.update(data):
            return True

        return False

    def close(self):
        """f
        Close the run
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Run is not active")
            return False

        if self._heartbeat_thread:
            self._heartbeat_termination_trigger.set()
            self._heartbeat_thread.join()

        if self._shutdown_event:
            self._shutdown_event.set()

        if self._status != "failed":
            if self._dispatcher:
                self._dispatcher.join()
            self.set_status("completed")
        elif self._dispatcher:
            self._dispatcher.purge()
            self._dispatcher.join()

    def set_folder_details(self, path, metadata={}, tags=[], description=None):
        """
        Add metadata to the specified folder
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Run is not active")
            return False

        if not isinstance(metadata, dict):
            self._error("metadata must be a dict")
            return False

        if not isinstance(tags, list):
            self._error("tags must be a list")
            return False

        data = {"path": path}

        if metadata:
            data["metadata"] = metadata

        if tags:
            data["tags"] = tags

        if description:
            data["description"] = description

        if self._simvue.set_folder_details(data):
            return True

        return False

    def add_alerts(self, ids=None, names=None):
        """
        Add one or more existing alerts by name or id
        """
        ids = ids or []
        names = names or []

        if names and not ids:
            alerts = self._simvue.list_alerts()
            if alerts:
                for alert in alerts:
                    if alert["name"] in names:
                        ids.append(alert["id"])
            else:
                self._error("No existing alerts")
                return False
        elif not names and not ids:
            self._error("Need to provide alert ids or alert names")
            return False

        data = {"id": self._id, "alerts": ids}
        if self._simvue.update(data):
            return True

        return False

    def add_alert(
        self,
        name,
        source="metrics",
        frequency=None,
        window=5,
        rule=None,
        metric=None,
        threshold=None,
        range_low=None,
        range_high=None,
        notification="none",
        pattern=None,
    ):
        """
        Creates an alert with the specified name (if it doesn't exist)
        and applies it to the current run
        """
        if self._mode == "disabled":
            return True

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if rule:
            if rule not in (
                "is below",
                "is above",
                "is outside range",
                "is inside range",
            ):
                self._error("alert rule invalid")
                return False

        if rule in ("is below", "is above") and threshold is None:
            self._error("threshold must be defined for the specified alert type")
            return False

        if rule in ("is outside range", "is inside range") and (
            range_low is None or range_high is None
        ):
            self._error(
                "range_low and range_high must be defined for the specified alert type"
            )
            return False

        if notification not in ("none", "email"):
            self._error("notification must be either none or email")
            return False

        if source not in ("metrics", "events", "user"):
            self._error("source must be either metrics, events or user")
            return False

        alert_definition = {}

        if source == "metrics":
            alert_definition["metric"] = metric
            alert_definition["window"] = window
            alert_definition["rule"] = rule
            if threshold is not None:
                alert_definition["threshold"] = threshold
            elif range_low is not None and range_high is not None:
                alert_definition["range_low"] = range_low
                alert_definition["range_high"] = range_high
        elif source == "events":
            alert_definition["pattern"] = pattern
        else:
            alert_definition = None

        alert = {
            "name": name,
            "frequency": frequency,
            "notification": notification,
            "source": source,
            "alert": alert_definition,
        }

        # Check if the alert already exists
        alert_id = None
        alerts = self._simvue.list_alerts()
        if alerts:
            for existing_alert in alerts:
                if existing_alert["name"] == alert["name"]:
                    if compare_alerts(existing_alert, alert):
                        alert_id = existing_alert["id"]
                        logger.info("Existing alert found with id: %s", alert_id)

        if not alert_id:
            response = self._simvue.add_alert(alert)
            if response:
                if "id" in response:
                    alert_id = response["id"]
            else:
                self._error("unable to create alert")
                return False

        if alert_id:
            # TODO: What if we keep existing alerts/add a new one later?
            data = {"id": self._id, "alerts": [alert_id]}
            if self._simvue.update(data):
                return True

        return False

    def log_alert(self, name, state):
        """
        Set the state of an alert
        """
        if state not in ("ok", "critical"):
            self._error('state must be either "ok" or "critical"')
            return False

        self._simvue.set_alert_state(name, state)
