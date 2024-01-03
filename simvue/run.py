"""
Simvue Run
==========

Contains objects and methods relating to definition of a Simvue run for
communicating with the Simvue server during a simulation run. This includes
collection of metadata about the host system, logging of metrics and
updating run status
"""

import datetime
import hashlib
import logging
import mimetypes
import os
import re
import multiprocessing
import pathlib
import socket
import GPUtil
import sys
import cpuinfo
import time as tm
import platform
import typing
import types
import glob
import uuid
import typing

from multiprocessing.synchronize import Event

from .worker import Worker
from .factory import Simvue
from .serialization import Serializer
from .models import RunInput
from .factory.remote import Remote
from .factory.offline import Offline
from .utilities import get_auth, get_expiry, skip_if_failed
from .executor import Executor
from pydantic import ValidationError

INIT_MISSING: str = "initialize a run using init() first"
QUEUE_SIZE: int = 10000
CHECKSUM_BLOCK_SIZE: int = 4096
UPLOAD_TIMEOUT: int = 30

logger = logging.getLogger(__name__)


def compare_alerts(first: dict[str, typing.Any], second: dict[str, typing.Any]) -> bool:
    """Compare two alert definitions."""
    return all(
        _f_val == second.get(k)
        for k in ("name", "description", "source", "frequency", "notification")
        if (_f_val := first.get(k))
    )


def walk_through_files(path) -> typing.Iterator[str]:
    return glob.iglob(os.path.join(path, "**"), recursive=True)


def get_cpu_info() -> typing.Tuple[str, str]:
    """
    Get CPU info
    """
    cpu_info: typing.Dict[str, typing.Any] = cpuinfo.get_cpu_info()
    return cpu_info["brand_raw"], cpu_info["arch_string_raw"]


def get_gpu_info() -> typing.Dict[str, str]:
    """
    Get GPU info
    """
    gpus: typing.List[GPUtil.GPU] = GPUtil.getGPUs()

    if not gpus:
        return {"name": "", "driver_version": ""}

    return {"name": gpus[0].name, "driver_version": gpus[0].driver}


def get_system() -> typing.Dict[str, typing.Any]:
    """
    Get system details
    """
    cpu = get_cpu_info()
    gpu = get_gpu_info()

    system: typing.Dict[str, typing.Any] = {}
    system["cwd"] = os.getcwd()
    system["hostname"] = socket.gethostname()
    system["pythonversion"] = (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )
    system["platform"] = {}
    system["platform"]["system"] = platform.system()
    system["platform"]["release"] = platform.release()
    system["platform"]["version"] = platform.version()
    system["cpu"] = {}
    system["cpu"]["arch"] = cpu[1]
    system["cpu"]["processor"] = cpu[0]
    system["gpu"] = {}
    system["gpu"]["name"] = gpu["name"]
    system["gpu"]["driver"] = gpu["driver_version"]

    return system


def calculate_sha256(filename: str, is_file: bool) -> str | None:
    """
    Calculate sha256 checksum of the specified file
    """
    sha256_hash = hashlib.sha256()
    if is_file:
        try:
            with open(filename, "rb") as fd:
                for byte_block in iter(lambda: fd.read(CHECKSUM_BLOCK_SIZE), b""):
                    sha256_hash.update(byte_block)
                return sha256_hash.hexdigest()
        except Exception:
            return None

    if isinstance(filename, str):
        sha256_hash.update(bytes(filename, "utf-8"))
    else:
        sha256_hash.update(bytes(filename))
    return sha256_hash.hexdigest()


def validate_timestamp(timestamp: str) -> bool:
    """
    Validate a user-provided timestamp
    """
    try:
        datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
        return True
    except ValueError:
        return False


class Run:
    """
    Track simulation details based on token and URL
    """
    def __init__(self, mode: str = "online") -> None:
        self._uuid: str = str(uuid.uuid4())
        self._mode: str = mode
        self._name: str | None = None
        self._id: str | None = None
        self._executor = Executor(self)
        self._suppress_errors: bool = True
        self._queue_blocking: bool = False
        self._status: str | None = None
        self._step: int = 0
        self._queue_size: int = QUEUE_SIZE
        self._metrics_queue: None | multiprocessing.Queue = None
        self._events_queue: None | multiprocessing.Queue = None
        self._active: bool = False
        self._aborted: bool = False
        self._url, self._token = get_auth()
        self._headers: typing.Dict[str, str] = {
            "Authorization": f"Bearer {self._token}"
        }
        self._simvue: Offline | Remote | None = None
        self._pid: int = 0
        self._resources_metrics_interval: int = 30
        self._shutdown_event: Event | None = None
        self._storage_id: int | None = None

    def __enter__(self) -> "Run":
        return self

    def __exit__(
        self,
        type: typing.Optional[typing.Type[BaseException]],
        value: typing.Optional[BaseException],
        traceback: typing.Optional[types.TracebackType],
    ) -> None:
        self._executor.wait_for_completion()

        identifier: str = self._id
        logger.debug(
            "Automatically closing run %s in status %s", identifier, self._status
        )

        if not all([self._id or self._mode == "offline", self._status == "running"]):
            return

        if self._shutdown_event is not None:
            self._shutdown_event.set()

        if not type:
            if self._active:
                self.set_status("completed")
        else:
            if self._active:
                self.log_event(f"{type.__name__}: {value}")
            if type.__name__ in ("KeyboardInterrupt") and self._active:
                self.set_status("terminated")
            else:
                if self._active:
                    self.log_event(f"{type.__name__}: {value}")
                if type.__name__ in ('KeyboardInterrupt') and self._active:
                    self.set_status('terminated')
                else:
                    if traceback and self._active:
                        self.log_event(f"Traceback: {traceback}")
                        self.set_status('failed')
        
        if (_non_zero := self.executor.exit_status):
            logger.error(f"Simvue process executor terminated with non-zero exit status {_non_zero}")
            sys.exit(_non_zero)

    def _check_token(self) -> None:
        """
        Check if token is valid
        """
        if self._mode == "online" and tm.time() - get_expiry(self._token) > 0:
            self._error("token has expired or is invalid")

    def _start(self, reconnect: bool = False) -> bool:
        """
        Start a run
        """
        if self._mode == "disabled":
            self._error("Cannot start run, Simvue is disabled")
            return False

        if self._mode != "offline":
            self._uuid = None

        logger.debug("Starting run")

        self._check_token()

        data = {"status": self._status, "id": self._id}

        if reconnect:
            data["system"] = get_system()
            if not self._simvue.update(data):
                self._error("Failed to update session data")
                return False

        self._start_time = tm.time()

        if self._pid == 0:
            self._pid = os.getpid()

        self._metrics_queue = multiprocessing.Manager().Queue(maxsize=self._queue_size)
        self._events_queue = multiprocessing.Manager().Queue(maxsize=self._queue_size)
        self._shutdown_event = multiprocessing.Manager().Event()
        self._worker = Worker(
            self._metrics_queue,
            self._events_queue,
            self._shutdown_event,
            self._uuid,
            self._name,
            self._id,
            self._url,
            self._headers,
            self._mode,
            self._pid,
            self._resources_metrics_interval,
        )

        if multiprocessing.current_process()._parent_pid is None:
            self._worker.start()

        self._active = True

    def _error(self, message: str) -> None:
        """
        Raise an exception if necessary and log error
        """
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        if not self._suppress_errors:
            raise RuntimeError(message)
        logger.error(message)

        # If an error is thrown Simvue Client will now enter an aborted
        # state allowing other Python code to complete, but putting
        # the client out of action, hence job is now 'lost'
        if self._simvue:
            self._simvue.update({"name": self._name, "status": "lost"})

        self._aborted = True

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def init(
        self,
        name: str | None = None,
        metadata: typing.Dict[str, typing.Any] | None = None,
        tags: typing.List[str] | None = None,
        description: str | None = None,
        folder: str = "/",
        running: bool = True,
        ttl: int = -1,
    ) -> bool:
        """
        Initialise a run
        """
        if self._mode not in ("online", "offline", "disabled"):
            self._error("invalid mode specified, must be online, offline or disabled")
            return False

        if self._mode == "disabled":
            self._error("Cannot initialise, Simvue is disabled")
            return False

        if (not self._token or not self._url) and self._mode != "offline":
            self._error(
                "Unable to get URL and token from environment variables or config file"
            )

        if name and not re.match(r"^[a-zA-Z0-9\-\_\s\/\.:]+$", name):
            self._error("specified name is invalid")
            return False

        self._name = name

        if running:
            self._status = "running"
        else:
            self._status = "created"

        data = {
            "metadata": metadata or {},
            "tags": tags or [],
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
            return False

        self._simvue = Simvue(self._name, self._uuid, self._mode, self._suppress_errors)
        name, self._id = self._simvue.create_run(data)

        if not name:
            return False
        elif name is not True:
            self._name = name

        if self._status == "running":
            self._start()
        return True

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def add_process(
        self,
        identifier: str,
        *cmd_args,
        executable: str | None = None,
        script: str | None = None,
        input_file: str | None = None,
        print_stdout: bool = False,
        completion_callback: typing.Callable | None=None,
        env: typing.Optional[typing.Dict[str, str]]=None,
        **cmd_kwargs
    ) -> None:
        """Add a process to be executed to the executor.

        This process can take many forms, for example a be a set of positional arguments:

        ```python
        executor.add_process("my_process", "ls", "-ltr")
        ```

        Provide explicitly the cfomponents of the command:

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
        print_stdout : bool, optional
            print output of command to the terminal, default is False
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
            print_stdout=print_stdout,
            completion_callback=completion_callback,
            env=env,
            **cmd_kwargs
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
    def name(self) -> str | None:
        """
        Return the name of the run
        """
        return self._name

    @property
    def uid(self) -> str:
        """
        Return the local unique identifier of the run
        """
        return self._uuid

    @property
    def id(self) -> str | None:
        """
        Return the unique id of the run
        """
        return self._id

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def reconnect(self, run_id, uid=None) -> bool:
        """
        Reconnect to a run in the created state
        """
        if self._mode == "disabled":
            self._error("Cannot reconnect, Simvue is disabled")
            return False

        self._status = "running"
        self._uuid = uid

        self._id = run_id

        self._simvue = Simvue(
            self._name, self._uuid, self._mode, self._suppress_errors
        )
        self._start(reconnect=True)

    @skip_if_failed("_aborted", "_suppress_errors", None)
    def set_pid(self, pid: str) -> None:
        """
        Set pid of process to be monitored
        """
        self._pid = pid

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def config(
        self,
        suppress_errors: bool | None = None,
        queue_blocking: bool | None = None,
        queue_size: int | None = None,
        disable_resources_metrics: bool | None = None,
        resources_metrics_interval: int | None = None,
        storage_id: int | None = None
    ) -> None:
        """Optional configuration

        Update configuration settings for the Simvue run instance

        Parameters
        ----------
        suppress_errors : bool, optional
            whether log errors as opposed to raise exceptions, by default False
        queue_blocking : bool, optional
            whether to apply queue blocking to requests, by default False
        queue_size : int, optional
            the size of the queue for requests, by default QUEUE_SIZE
        disable_resources_metrics : bool, optional
            whether to disable resource metrics for the run, by default False
        resources_metrics_interval : int, optional
            how often to gather resource metrics, by default 30
        """
        # The arguments are None by default so all configurations do
        # not have to be repeated every time a user wants to update just
        # one of them
        if suppress_errors is not None:
            if not isinstance(suppress_errors, bool):
                self._error("suppress_errors must be boolean")
            self._suppress_errors = suppress_errors
            if self._simvue:
                self._simvue._suppress_errors = suppress_errors

        if queue_blocking is not None:
            if not isinstance(queue_blocking, bool):
                self._error("queue_blocking must be boolean")
            self._queue_blocking = queue_blocking

        if queue_size is not None:
            if not isinstance(queue_size, int):
                self._error("queue_size must be an integer")
            self._queue_size = queue_size

        if disable_resources_metrics is not None:
            if not isinstance(disable_resources_metrics, bool):
                self._error("disable_resources_metrics must be boolean")

        if disable_resources_metrics:
            self._pid = None

        if resources_metrics_interval is not None:
            if not isinstance(resources_metrics_interval, int):
                self._error("resources_metrics_interval must be an integer")
            self._resources_metrics_interval = resources_metrics_interval

        self._storage_id = storage_id or self._storage_id

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def update_metadata(self, metadata: typing.Dict[str, typing.Any]) -> bool:
        """Update metadata for this run.

        Parameters
        ----------
        metadata : typing.Dict[str, typing.Any]
            a dictionary containing key-value pairs for the metadata to
            send to the server

        Returns
        -------
        bool
            whether update of metadata was successful
        """
        if self._mode == "disabled":
            self._error("Cannot update metadata, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not isinstance(metadata, dict):
            self._error("metadata must be a dict")
            return False

        data = {"name": self._name, "metadata": metadata}

        if self._simvue.update(data):
            return True

        return False

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def update_tags(self, tags: typing.List[str]) -> bool:
        """Update list of tags for this run

        Parameters
        ----------
        tags : typing.List[str]
            list of tags to apply to the current run

        Returns
        -------
        bool
            whether tag application was successful
        """
        if self._mode == "disabled":
            self._error("Cannot update tags, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        data = {"tags": tags, "id": self._id}

        if self._simvue.update(data):
            return True

        return False

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def log_event(self, message: str, timestamp: str | None = None) -> bool:
        """Write an event to the server.

        Parameters
        ----------
        message : str
            the message to be displayed within the event
        timestamp : str | None, optional
            timestamp for event occurence, by default None

        Returns
        -------
        bool
            whether event creation was successful
        """
        if self._mode == "disabled":
            self._error("Cannot log event, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Cannot log event, run is not active")
            return False

        if self._status != "running":
            self._error("Cannot log events when not in the running state")
            return False

        data = {}
        data["message"] = message
        data["timestamp"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        if timestamp is not None:
            if validate_timestamp(timestamp):
                data["timestamp"] = timestamp
            else:
                self._error("Invalid timestamp format")
                return False

        try:
            self._events_queue.put(data, block=self._queue_blocking)
        except Exception as err:
            logger.error(str(err))
            return False

        return True

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def log_metrics(
        self,
        metrics: typing.Dict[str, str | int | float],
        step: int | None = None,
        time: int | None = None,
        timestamp: str | None = None,
    ) -> bool:
        """Send metrics to the server.

        Parameters
        ----------
        metrics : typing.Dict[str, str  |  int  |  float]
            a dictionary containing metrics to be recorded, these
            are key-value pairs and can be updated every interval
        step : int | None, optional
            if provided, the step of the process/simulation, by default None
        time : int | None, optional
            if provided, the time of recording the metric, by default None
        timestamp : str | None, optional
            if ptovided, a timestamp of when the metric was recorded, by default None

        Returns
        -------
        bool
            if the metric update was successful
        """
        if self._mode == "disabled":
            self._error("Cannot log metrics, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Cannot log metrics, run is not active")
            return False

        if self._status != "running":
            self._error("Cannot log metrics when not in the running state")
            return False

        if not isinstance(metrics, dict) and not self._suppress_errors:
            self._error("Metrics must be a dict")
            return False

        data: typing.Dict[str, int | float | str] = {
            "values": metrics,
            "time": tm.time() - self._start_time,
        }

        if time is not None:
            data["time"] = time
        data["timestamp"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        if timestamp is not None:
            if validate_timestamp(timestamp):
                data["timestamp"] = timestamp
            else:
                self._error("Invalid timestamp format")
                return False

        data["step"] = step if step is not None else self._step

        self._step += 1

        try:
            self._metrics_queue.put(data, block=self._queue_blocking)
        except Exception as err:
            logger.error(str(err))
            return False

        return True

    def _assemble_file_data(
        self, filename: str, filetype: str | None, is_file: bool
    ) -> typing.Dict[str, typing.Any] | None:
        """Collect information for a given file"""
        data: typing.Dict[str, typing.Any] = {}
        data["size"] = os.path.getsize(filename)
        data["originalPath"] = os.path.abspath(
            os.path.expanduser(os.path.expandvars(filename))
        )
        data["checksum"] = calculate_sha256(filename, is_file)

        if data["size"] == 0:
            logger.warning("Saving zero-sized files not currently supported")
            return None

        # Determine mimetype
        if not filetype:
            mimetypes.init()
            data["type"] = (
                mimetypes.guess_type(filename)[0] or "application/octet-stream"
            )
        else:
            data["type"] = filetype

        return data

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def save(
        self,
        filename: str,
        category: str,
        filetype: str | None = None,
        preserve_path: bool = False,
        name: str | None = None,
        allow_pickle: bool = False,
    ) -> bool:
        """Save a file associated with this run to the server

        Parameters
        ----------
        filename : str
            the name of the file to upload
        category : str
            whether this file is input/output/other
        filetype : str | None, optional
            the type of the file, by default None
        preserve_path : bool, optional
            whether to use file name or full path when storing, by default False
        name : str | None, optional
            a label for this file, by default None
        allow_pickle : bool, optional
            whether the file should be pickled, by default False

        Returns
        -------
        bool
            returns True if file submission was successful
        """
        if self._mode == "disabled":
            self._error("Cannot save file, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if self._status == "created" and category == "output":
            self._error("Cannot upload output files for runs in the created state")
            return False

        is_file: bool = False

        if isinstance(filename, str):
            if not os.path.isfile(filename):
                self._error(f"File {filename} does not exist")
                return False
            is_file = True

        if filetype:
            mimetypes_valid = ["application/vnd.plotly.v1+json"]
            mimetypes.init()
            for _, value in mimetypes.types_map.items():
                mimetypes_valid.append(value)

            if filetype not in mimetypes_valid:
                self._error("Invalid MIME type specified")
                return False

        data: typing.Dict[str, typing.Any] = {}

        if preserve_path:
            # If the path starts with ./ or .\ this automatically removes it
            data["name"] = os.path.join(*pathlib.Path(filename).parts)
        elif is_file:
            data["name"] = os.path.basename(filename)

        if name:
            data["name"] = name

        data["run"] = self._name
        data["category"] = category

        if is_file:
            file_data = self._assemble_file_data(filename, filetype, is_file)
            if not file_data:
                self._error("No file data assembled.")
                return False
            data |= file_data
        else:
            data["pickled"], data["type"] = Serializer().serialize(
                filename, allow_pickle
            )
            if not data["type"] and not allow_pickle:
                self._error("Unable to save Python object, set allow_pickle to True")
            data["checksum"] = calculate_sha256(data["pickled"], False)
            data["originalPath"] = ""
            data["size"] = sys.getsizeof(data["pickled"])

        if self._storage_id:
            data['storage'] = self._storage_id

        # Register file
        return self._simvue.save_file(data)

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def save_directory(
        self,
        directory: str,
        category: str,
        filetype: str | None = None,
        preserve_path: bool = False,
    ) -> bool:
        """Upload contents of an entire directory

        Parameters
        ----------
        directory : str
            the directory from which to upload
        category : str
            the category of the contained files (input/output/other)
        filetype : str | None, optional
            the type of the file, by default None
        preserve_path : bool, optional
            whether to store as directory name or full path, by default False

        Returns
        -------
        bool
            returns True if upload was successful
        """
        if self._mode == "disabled":
            self._error("Cannot save directory, Simvue is disabled")
            return False

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

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def save_all(
        self,
        items: typing.List[str],
        category: str,
        filetype: str | None = None,
        preserve_path: bool = False,
    ) -> bool:
        """Save a set of files.

        Parameters
        ----------
        items : typing.List[str]
            a list of items to
        category : str
            _description_
        filetype : str | None, optional
            _description_, by default None
        preserve_path : bool, optional
            _description_, by default False

        Returns
        -------
        bool
            _description_
        """
        if self._mode == "disabled":
            self._error("Cannot save all, Simvue is disabled")
            return False

        for item in items:
            if os.path.isfile(item):
                self.save(item, category, filetype, preserve_path)
            elif os.path.isdir(item):
                self.save_directory(item, category, filetype, preserve_path)
            else:
                self._error(f"{item}: No such file or directory")

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def set_status(self, status: str) -> bool:
        """
        Set run status
        """
        if self._mode == "disabled":
            self._error("Cannot set status, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Cannot set statis, run is not active")
            return False

        if status not in ("completed", "failed", "terminated"):
            self._error("invalid status")
            return False

        if not self._name:
            self._error("Failed to retrieve name from run")
            return False

        data = {"name": self._name, "status": status}
        self._status = status

        return self._simvue.update(data) is not None

    @skip_if_failed("_aborted", "_suppress_errors", {})
    def close(self) -> bool | None:
        """
        Close the run
        """
        if self._mode == "disabled":
            self._error("Cannot close run, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Cannot close the run, run is not active")
            return False

        if self._status != "failed":
            self.set_status("completed")

        self._shutdown_event.set()

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def set_folder_details(
        self,
        path: str,
        metadata: typing.Dict[str, typing.Any] | None = None,
        tags: typing.List[str] | None = None,
        description: str | None = None,
    ) -> bool:
        """
        Add metadata to the specified folder
        """
        if self._mode == "disabled":
            self._error("Cannot set folder details, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if not self._active:
            self._error("Cannot set folder details, run is not active")
            return False

        if metadata and not isinstance(metadata, dict):
            self._error("metadata must be a dict")
            return False

        if tags and not isinstance(tags, list):
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

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def add_alerts(
        self, ids: typing.List[str] | None = None, names: typing.List[str] | None = None
    ) -> bool:
        """
        Add one or more existing alerts by name or id
        """
        ids = ids or []
        names = names or []

        if names and not ids:
            alerts = self._simvue.list_alerts() or []
            if not alerts:
                self._error("No existing alerts")
                return False
            ids += [
                alert["id"]
                for alert in alerts
                if alert["name"] in names
            ]
        elif not names and not ids:
            self._error("Need to provide alert ids or alert names")
            return False

        data = {"id": self._id, "alerts": ids}
        if self._simvue.update(data):
            return True

        return False

    @skip_if_failed("_aborted", "_suppress_errors", False)
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
            self._error("Cannot add alert, Simvue is disabled")
            return False

        if not self._uuid and not self._name:
            self._error(INIT_MISSING)
            return False

        if rule not in (
            "is below",
            "is above",
            "is outside range",
            "is inside range",
            None
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

    @skip_if_failed("_aborted", "_suppress_errors", False)
    def log_alert(self, name, state):
        """
        Set the state of an alert
        """
        if state not in ("ok", "critical"):
            self._error('state must be either "ok" or "critical"')
            return False

        self._simvue.set_alert_state(name, state)
