"""
Simvue Client
=============

Contains a Simvue client class for interacting with existing objects on the
server including deletion and retrieval.
"""

import json
import logging
import os
import typing
import http
import pydantic
from concurrent.futures import ThreadPoolExecutor, as_completed
from pandas import DataFrame

import requests

from .converters import (
    aggregated_metrics_to_dataframe,
    to_dataframe,
    parse_run_set_metrics,
)
from .serialization import deserialize_data
from .simvue_types import DeserializedContent
from .utilities import check_extra, prettify_pydantic
from .models import FOLDER_REGEX, NAME_REGEX
from .config.user import SimvueConfiguration

if typing.TYPE_CHECKING:
    pass

CONCURRENT_DOWNLOADS = 10
DOWNLOAD_CHUNK_SIZE = 8192
DOWNLOAD_TIMEOUT = 30

logger = logging.getLogger(__file__)


def downloader(job: dict[str, str]) -> bool:
    """Download a job output to the location specified within the definition

    Parameters
    ----------
    job : dict[str, str]
        a dictionary containing information on URL and path for a given job
        this information is then used to perform the download

    Returns
    -------
    bool
        whether the file was created successfully
    """
    # Check to make sure all requirements have been retrieved first
    for key in ("url", "path", "filename"):
        if key not in job:
            logger.warning(f"Expected key '{key}' during job object retrieval")
            raise RuntimeError(
                "Failed to retrieve required information during job download"
            )

    try:
        response = requests.get(job["url"], stream=True, timeout=DOWNLOAD_TIMEOUT)
        response = requests.get(job["url"], stream=True, timeout=DOWNLOAD_TIMEOUT)
    except requests.exceptions.RequestException:
        return False

    total_length = response.headers.get("content-length")
    total_length = response.headers.get("content-length")

    save_location: str = os.path.join(job["path"], job["filename"])

    if not os.path.isdir(job["path"]):
        raise ValueError(f"Cannot write to '{job['path']}', not a directory.")

    logger.debug(f"Writing file '{save_location}'")

    with open(save_location, "wb") as fh:
        if total_length is None:
            fh.write(response.content)
        else:
            for data in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                fh.write(data)

    return os.path.exists(save_location)


class Client:
    """
    Class for querying Simvue
    """

    def __init__(
        self,
        *,
        server_token: typing.Optional[str] = None,
        server_url: typing.Optional[str] = None,
    ) -> None:
        """Initialise an instance of the Simvue client

        Parameters
        ----------
        server_token : str, optional
            specify token, if unset this is read from the config file
        server_url : str, optional
            specify URL, if unset this is read from the config file
        """
        self._user_config = SimvueConfiguration.fetch(
            server_token=server_token, server_url=server_url
        )

        for label, value in zip(
            ("URL", "API token"),
            (self._user_config.server.url, self._user_config.server.url),
        ):
            if not value:
                logger.warning(f"No {label} specified")

        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {self._user_config.server.token}"
        }

    def _get_json_from_response(
        self,
        expected_status: list[int],
        scenario: str,
        response: requests.Response,
    ) -> typing.Union[dict, list]:
        try:
            json_response = response.json()
            json_response = json_response or {}
        except json.JSONDecodeError:
            json_response = None

        error_str = f"{scenario} failed "

        if (_status_code := response.status_code) in expected_status:
            if json_response is not None:
                return json_response
            details = "could not request JSON response"
        else:
            error_str += f"with status {_status_code}"
            details = (json_response or {}).get("details")

        try:
            txt_response = response.text
        except UnicodeDecodeError:
            txt_response = None

        if details:
            error_str += f": {details}"
        elif txt_response:
            error_str += f": {txt_response}"

        raise RuntimeError(error_str)

    @prettify_pydantic
    @pydantic.validate_call
    def get_run_id_from_name(
        self, name: typing.Annotated[str, pydantic.Field(pattern=NAME_REGEX)]
    ) -> str:
        """Get Run ID from the server matching the specified name

        Assumes a unique name for this run. If multiple results are found this
        method will fail.

        Parameters
        ----------
        name : str
            the name of the run

        Returns
        -------
        str
            the unique identifier for this run

        Raises
        ------
        RuntimeError
            if either information could not be retrieved from the server,
            or multiple/no runs are found
        """
        params: dict[str, str] = {"filters": json.dumps([f"name == {name}"])}

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/runs",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario="Retrieval of run ID from name",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary as response for ID "
                f"retrieval but got {type(json_response)}"
            )

        if not (response_data := json_response.get("data")):
            raise RuntimeError(f"No ID found for run '{name}'")

        if len(response_data) == 0:
            raise RuntimeError("Could not collect ID - no run found with this name.")
        if len(response_data) > 1:
            raise RuntimeError(
                "Could not collect ID - more than one run exists with this name."
            )
        if not (first_id := response_data[0].get("id")):
            raise RuntimeError("Failed to retrieve identifier for run.")
        return first_id

    @prettify_pydantic
    @pydantic.validate_call
    def get_run(self, run_id: str) -> typing.Optional[dict[str, typing.Any]]:
        """Retrieve a single run

        Parameters
        ----------
        run_id : str
            the unique identifier for this run

        Returns
        -------
        dict[str, Any]
            response containing information on the given run

        Raises
        ------
        RuntimeError
            if retrieval of information from the server on this run failed
        """

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/runs/{run_id}", headers=self._headers
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK, http.HTTPStatus.NOT_FOUND],
            scenario=f"Retrieval of run '{run_id}'",
            response=response,
        )

        if response.status_code == http.HTTPStatus.NOT_FOUND:
            return None

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during run retrieval "
                f"but got '{type(json_response)}'"
            )
        return json_response

    @prettify_pydantic
    @pydantic.validate_call
    def get_run_name_from_id(self, run_id: str) -> str:
        """Retrieve the name of a run from its identifier

        Parameters
        ----------
        run_id : str
            the unique identifier for the run

        Returns
        -------
        str
            the registered name for the run
        """
        if not run_id:
            raise ValueError("Expected value for run_id but got None")

        _run_data = self.get_run(run_id)

        if not _run_data:
            raise RuntimeError(f"Failed to retrieve data for run '{run_id}'")

        if not (_name := _run_data.get("name")):
            raise RuntimeError("Expected key 'name' in server response")
        return _name

    @prettify_pydantic
    @pydantic.validate_call
    def get_runs(
        self,
        filters: typing.Optional[list[str]],
        *,
        system: bool = False,
        metrics: bool = False,
        alerts: bool = False,
        metadata: bool = False,
        output_format: typing.Literal["dict", "dataframe"] = "dict",
        count_limit: typing.Optional[pydantic.PositiveInt] = 100,
        start_index: typing.Optional[pydantic.PositiveInt] = 0,
        show_shared: bool = False,
    ) -> typing.Union[
        DataFrame, list[dict[str, typing.Union[int, str, float, None]]], None
    ]:
        """Retrieve all runs matching filters.

        Parameters
        ----------
        filters: list[str] | None
            set of filters to apply to query results. If None is specified
            return all results without filtering.
        metadata : bool, optional
            whether to include metadata information in the response.
            Default False.
        metrics : bool, optional
            whether to include metrics information in the response.
            Default False.
        alerts : bool, optional
            whether to include alert information in the response.
            Default False.
        output_format : Literal['dict', 'dataframe'], optional
            the structure of the response, either a dictionary or a dataframe.
            Default is 'dict'. Pandas must be installed for 'dataframe'.
        count_limit : int, optional
            maximum number of entries to return. Default is 100.
        start_index : int, optional
            the index from which to count entries. Default is 0.
        show_shared : bool, optional
            whether to include runs shared with the current user. Default is False.

        Returns
        -------
        dict | pandas.DataFrame
            either the JSON response from the runs request or the results in the
            form of a Pandas DataFrame

        Raises
        ------
        ValueError
            if a value outside of 'dict' or 'dataframe' is specified
        RuntimeError
            if there was a failure in data retrieval from the server
        """
        if not show_shared:
            filters = (filters or []) + ["user == self"]

        params = {
            "filters": json.dumps(filters),
            "return_basic": True,
            "return_metrics": metrics,
            "return_alerts": alerts,
            "return_system": system,
            "return_metadata": metadata,
            "count": count_limit,
            "start": start_index,
        }

        response = requests.get(
            f"{self._user_config.server.url}/api/runs",
            headers=self._headers,
            params=params,
        )

        response.raise_for_status()

        if output_format not in ("dict", "dataframe"):
            raise ValueError("Invalid format specified")

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario="Run retrieval",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during retrieval of runs "
                f"but got '{type(json_response)}'"
            )

        if (response_data := json_response.get("data")) is not None:
            return response_data
        elif output_format == "dataframe":
            return to_dataframe(response.json())
        else:
            raise RuntimeError("Failed to retrieve runs data")

    @prettify_pydantic
    @pydantic.validate_call
    def delete_run(self, run_id: str) -> typing.Optional[dict]:
        """Delete run by identifier

        Parameters
        ----------
        run_id : str
            the unique identifier for the run

        Returns
        -------
        dict | None
            the request response after deletion

        Raises
        ------
        RuntimeError
            if the deletion failed due to server request error
        """

        response = requests.delete(
            f"{self._user_config.server.url}/api/runs/{run_id}",
            headers=self._headers,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Deletion of run '{run_id}'",
            response=response,
        )

        logger.debug(f"Run '{run_id}' deleted successfully")

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during run deletion "
                f"but got '{type(json_response)}'"
            )

        return json_response or None

    def _get_folder_id_from_path(self, path: str) -> typing.Optional[str]:
        """Retrieve folder identifier for the specified path if found

        Parameters
        ----------
        path : str
            the path to search for

        Returns
        -------
        str | None
            if a match is found, return the identifier of the folder
        """
        params: dict[str, str] = {"filters": json.dumps([f"path == {path}"])}

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/folders",
            headers=self._headers,
            params=params,
        )

        if (
            response.status_code == http.HTTPStatus.OK
            and (response_data := response.json().get("data"))
            and (identifier := response_data[0].get("id"))
        ):
            return identifier

        return None

    @prettify_pydantic
    @pydantic.validate_call
    def delete_runs(
        self, folder_path: typing.Annotated[str, pydantic.Field(pattern=FOLDER_REGEX)]
    ) -> typing.Optional[list]:
        """Delete runs in a named folder

        Parameters
        ----------
        folder_path : str
            the path of the folder on which to perform deletion. All folder
            paths are prefixed with `/`

        Returns
        -------
        list | None
            List of deleted runs

        Raises
        ------
        RuntimeError
            if deletion fails due to server request error
        """
        folder_id = self._get_folder_id_from_path(folder_path)

        if not folder_id:
            raise ValueError(f"Could not find a folder matching '{folder_path}'")

        params: dict[str, bool] = {"runs_only": True, "runs": True}

        response = requests.delete(
            f"{self._user_config.server.url}/api/folders/{folder_id}",
            headers=self._headers,
            params=params,
        )

        if response.status_code == http.HTTPStatus.OK:
            if runs := response.json().get("runs", []):
                logger.debug(f"Runs from '{folder_path}' deleted successfully: {runs}")
            else:
                logger.debug("Folder empty, no runs deleted.")
            return runs

        raise RuntimeError(
            f"Deletion of runs from folder '{folder_path}' failed"
            f"with code {response.status_code}: {response.text}"
        )

    @prettify_pydantic
    @pydantic.validate_call
    def delete_folder(
        self,
        folder_path: typing.Annotated[str, pydantic.Field(pattern=FOLDER_REGEX)],
        recursive: bool = False,
        remove_runs: bool = False,
        allow_missing: bool = False,
    ) -> typing.Optional[list]:
        """Delete a folder by name

        Parameters
        ----------
        folder_path : str
            name of the folder to delete. All paths are prefixed with `/`
        recursive : bool, optional
            if folder contains additional folders remove these, else return an
            error. Default False.
        remove_runs : bool, optional
            whether to delete runs associated with this folder, by default False
        allow_missing : bool, optional
            allows deletion of folders which do not exist, else raise exception,
            default is exception raise

        Returns
        -------
        list | None
            if a folder is identified the runs also removed during execution

        Raises
        ------
        RuntimeError
            if deletion of the folder from the server failed
        """
        folder_id = self._get_folder_id_from_path(folder_path)

        if not folder_id:
            if allow_missing:
                return None
            else:
                raise RuntimeError(
                    f"Deletion of folder '{folder_path}' failed, "
                    "folder does not exist."
                )

        params: dict[str, bool] = {"runs": True} if remove_runs else {}
        params |= {"recursive": recursive}

        response = requests.delete(
            f"{self._user_config.server.url}/api/folders/{folder_id}",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK, http.HTTPStatus.NOT_FOUND],
            scenario=f"Deletion of folder '{folder_path}'",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during folder deletion "
                f"but got '{type(json_response)}'"
            )

        runs: list[dict] = json_response.get("runs", [])
        return runs

    @prettify_pydantic
    @pydantic.validate_call
    def delete_alert(self, alert_id: str) -> None:
        """Delete an alert from the server by ID

        Parameters
        ----------
        alert_id : str
            the unique identifier for the alert
        """
        response = requests.delete(
            f"{self._user_config.server.url}/api/alerts/{alert_id}",
            headers=self._headers,
        )

        if response.status_code == http.HTTPStatus.OK:
            logger.debug(f"Alert '{alert_id}' deleted successfully")
            return

        raise RuntimeError(
            f"Deletion of alert '{alert_id}' failed"
            f"with code {response.status_code}: {response.text}"
        )

    @prettify_pydantic
    @pydantic.validate_call
    def list_artifacts(self, run_id: str) -> list[dict[str, typing.Any]]:
        """Retrieve artifacts for a given run

        Parameters
        ----------
        run_id : str
            unique identifier for the run

        Returns
        -------
        list[dict[str, typing.Any]]
            list of relevant artifacts

        Raises
        ------
        RuntimeError
            if retrieval of artifacts failed when communicating with the server
        """
        params: dict[str, str] = {"runs": json.dumps([run_id])}

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/artifacts",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Retrieval of artifacts for run '{run_id}",
            response=response,
        )

        if not isinstance(json_response, list):
            raise RuntimeError(
                "Expected list of entries from JSON response during artifact "
                f"retrieval but got '{type(json_response)}'"
            )
        return json_response

    def _retrieve_artifact_from_server(
        self,
        run_id: str,
        name: str,
    ) -> typing.Union[dict, list]:
        params: dict[str, str | None] = {"name": name}

        response = requests.get(
            f"{self._user_config.server.url}/api/runs/{run_id}/artifacts",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK, http.HTTPStatus.NOT_FOUND],
            scenario=f"Retrieval of artifact '{name}' for run '{run_id}'",
            response=response,
        )

        if isinstance(json_response, dict) and (detail := json_response.get("detail")):
            raise RuntimeError(f"Failed to retrieve artifact '{name}': {detail}")

        if not isinstance(json_response, list):
            raise RuntimeError(
                "Expected list from JSON response during retrieval of "
                f"artifact but got '{type(json_response)}'"
            )

        return json_response

    @prettify_pydantic
    @pydantic.validate_call
    def abort_run(self, run_id: str, reason: str) -> typing.Union[dict, list]:
        """Abort a currently active run on the server

        Parameters
        ----------
        run_id : str
            the unique identifier for the run
        reason : str
            reason for abort

        Returns
        -------
        dict | list
            response from server
        """
        body: dict[str, str | None] = {"id": run_id, "reason": reason}

        response = requests.put(
            f"{self._user_config.server.url}/api/runs/abort",
            headers=self._headers,
            json=body,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK, http.HTTPStatus.NOT_FOUND],
            scenario=f"Abort of run '{run_id}'",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected list from JSON response during retrieval of "
                f"artifact but got '{type(json_response)}'"
            )

        return json_response

    @prettify_pydantic
    @pydantic.validate_call
    def get_artifact(
        self, run_id: str, name: str, allow_pickle: bool = False
    ) -> typing.Any:
        """Return the contents of a specified artifact

        Parameters
        ----------
        run_id : str
            the unique identifier of the run from which to retrieve the artifact
        name : str
            the name of the artifact to retrieve
        allow_pickle : bool, optional
            whether to de-pickle the retrieved data, by default False

        Returns
        -------
        DataFrame | Figure | FigureWidget | ndarray | Buffer | Tensor | bytes
            de-serialized content of artifact if retrieved, else content
            of the server response

        Raises
        ------
        RuntimeError
            if retrieval of artifact from the server failed
        """
        json_response = self._retrieve_artifact_from_server(run_id, name)

        if not json_response:
            return None

        url = json_response[0]["url"]
        mimetype = json_response[0]["type"]
        url = json_response[0]["url"]
        mimetype = json_response[0]["type"]

        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()

        content: typing.Optional[DeserializedContent] = deserialize_data(
            response.content, mimetype, allow_pickle
        )

        # Numpy array return means just 'if content' will be ambiguous
        # so must explicitly check if None
        return response.content if content is None else content

    @prettify_pydantic
    @pydantic.validate_call
    def get_artifact_as_file(
        self, run_id: str, name: str, path: typing.Optional[str] = None
    ) -> None:
        """Retrieve the specified artifact in the form of a file

        Information is saved to a file as opposed to deserialized

        Parameters
        ----------
        run_id : str
            unique identifier for the run to be queried
        name : str
            the name of the artifact to be retrieved
        path : str | None, optional
            path to download retrieved content to, the default of None
            uses the current working directory.

        Raises
        ------
        RuntimeError
            if there was a failure during retrieval of information from the
            server
        """
        json_response = self._retrieve_artifact_from_server(run_id, name)

        if not json_response:
            raise RuntimeError(
                f"Failed to download artifact '{name}' from run '{run_id}',"
                " no results found."
            )

        if not (url := json_response[0].get("url")):
            raise RuntimeError(
                "Failed to download artifacts, "
                "expected URL for retrieval but server "
                "did not return result"
            )

        downloader(
            {
                "url": url,
                "filename": os.path.basename(name),
                "path": path or os.getcwd(),
            }
        )

    def _assemble_artifact_downloads(
        self,
        request_response: requests.Response,
        startswith: typing.Optional[str],
        endswith: typing.Optional[str],
        contains: typing.Optional[str],
        out_path: str,
    ) -> list[dict[str, str]]:
        downloads: list[dict[str, str]] = []

        for item in request_response.json():
            for key in ("url", "name"):
                if key not in item:
                    raise RuntimeError(
                        f"Expected key '{key}' in request "
                        "response during file retrieval"
                    )

            if startswith and not item["name"].startswith(startswith):
                continue
            if contains and contains not in item["name"]:
                continue
            if endswith and not item["name"].endswith(endswith):
                continue

            file_name: str = os.path.basename(item["name"])
            file_dir: str = os.path.join(out_path, os.path.dirname(item["name"]))

            job: dict[str, str] = {
                "url": item["url"],
                "filename": file_name,
                "path": file_dir,
            }

            if os.path.isfile(file_path := os.path.join(file_dir, file_name)):
                logger.warning(f"File '{file_path}' exists, skipping")
                continue

            os.makedirs(job["path"], exist_ok=True)

            downloads.append(job)

        return downloads

    @prettify_pydantic
    @pydantic.validate_call
    def get_artifacts_as_files(
        self,
        run_id: str,
        category: typing.Optional[typing.Literal["input", "output", "code"]] = None,
        path: typing.Optional[str] = None,
        startswith: typing.Optional[str] = None,
        contains: typing.Optional[str] = None,
        endswith: typing.Optional[str] = None,
    ) -> None:
        """Retrieve artifacts from the given run as a set of files

        Parameters
        ----------
        run_id : str
            the unique identifier for the run
        path : str | None, optional
            location to download files to, the default of None will download
            them to the current working directory
        startswith : str, optional
            only download artifacts with this prefix in their name, by default None
        contains : str, optional
            only download artifacts containing this term in their name, by default None
        endswith : str, optional
            only download artifacts ending in this term in their name, by default None

        Raises
        ------
        RuntimeError
            if there was a failure retrieving artifacts from the server
        """
        params: dict[str, typing.Optional[str]] = {"category": category}

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/runs/{run_id}/artifacts",
            headers=self._headers,
            params=params,
        )

        self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Download of artifacts for run '{run_id}'",
            response=response,
        )

        downloads: list[dict[str, str]] = self._assemble_artifact_downloads(
            request_response=response,
            startswith=startswith,
            endswith=endswith,
            contains=contains,
            out_path=path or os.getcwd(),
        )

        with ThreadPoolExecutor(CONCURRENT_DOWNLOADS) as executor:
            futures = [executor.submit(downloader, item) for item in downloads]
            for future, download in zip(as_completed(futures), downloads):
                try:
                    future.result()
                except Exception as e:
                    raise RuntimeError(
                        f"Download of file {download['url']} "
                        f"failed with exception: {e}"
                    )

    @prettify_pydantic
    @pydantic.validate_call
    def get_folder(
        self, folder_path: typing.Annotated[str, pydantic.Field(pattern=FOLDER_REGEX)]
    ) -> typing.Optional[dict[str, typing.Any]]:
        """Retrieve a folder by identifier

        Parameters
        ----------
        folder_path : str
            the path of the folder to retrieve on the server.
            Paths are prefixed with `/`

        Returns
        -------
        dict[str, typing.Any] | None
            data for the requested folder if it exists else None

        Raises
        ------
        RuntimeError
            if there was a failure when retrieving information from the server
        """
        if not (_folders := self.get_folders(filters=[f"path == {folder_path}"])):
            return None
        return _folders[0]

    @pydantic.validate_call
    def get_folders(
        self,
        *,
        filters: typing.Optional[list[str]] = None,
        count: pydantic.PositiveInt = 100,
        start_index: pydantic.NonNegativeInt = 0,
    ) -> list[dict[str, typing.Any]]:
        """Retrieve folders from the server

        Parameters
        ----------
        filters : list[str] | None
            set of filters to apply to the search
        count : int, optional
            maximum number of entries to return. Default is 100.
        start_index : int, optional
            the index from which to count entries. Default is 0.

        Returns
        -------
        list[dict[str, Any]]
            all data for folders matching the filter request

        Raises
        ------
        RuntimeError
            if there was a failure retrieving data from the server
        """
        params: dict[str, typing.Union[str, int]] = {
            "filters": json.dumps(filters or []),
            "count": count,
            "start": start_index,
        }

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/folders",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario="Retrieval of folders",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during folder retrieval "
                f"but got '{type(json_response)}'"
            )

        if (data := json_response.get("data")) is None:
            raise RuntimeError(
                "Expected key 'data' in response during folder retrieval"
            )

        return data

    @prettify_pydantic
    @pydantic.validate_call
    def get_metrics_names(self, run_id: str) -> list[str]:
        """Return information on all metrics within a run

        Parameters
        ----------
        run_id : str
            unique identifier of the run

        Returns
        -------
        list[str]
            names of metrics in the given run

        Raises
        ------
        RuntimeError
            if there was a failure retrieving information from the server
        """
        params = {"runs": json.dumps([run_id])}

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/metrics/names",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Request for metric names for run '{run_id}'",
            response=response,
        )

        if not isinstance(json_response, list):
            raise RuntimeError(
                "Expected list from JSON response during folder retrieval "
                f"but got '{type(json_response)}'"
            )

        return json_response

    def _get_run_metrics_from_server(
        self,
        metric_names: list[str],
        run_ids: list[str],
        xaxis: str,
        aggregate: bool,
        max_points: typing.Optional[int] = None,
    ) -> dict[str, typing.Any]:
        params: dict[str, typing.Union[str, int, None]] = {
            "runs": json.dumps(run_ids),
            "aggregate": aggregate,
            "metrics": json.dumps(metric_names),
            "xaxis": xaxis,
            "max_points": max_points,
        }

        metrics_response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/metrics",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Retrieval of metrics '{metric_names}' in " f"runs '{run_ids}'",
            response=metrics_response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response for metric retrieval"
            )

        return json_response

    @prettify_pydantic
    @pydantic.validate_call
    def get_metric_values(
        self,
        metric_names: list[str],
        xaxis: typing.Literal["step", "time", "timestamp"],
        *,
        output_format: typing.Literal["dataframe", "dict"] = "dict",
        run_ids: typing.Optional[list[str]] = None,
        run_filters: typing.Optional[list[str]] = None,
        use_run_names: bool = False,
        aggregate: bool = False,
        max_points: typing.Optional[pydantic.PositiveInt] = None,
    ) -> typing.Union[dict, DataFrame, None]:
        """Retrieve the values for a given metric across multiple runs

        Uses filters to specify which runs should be retrieved.

        NOTE if the number of runs exceeds 100 'aggregated' will be set to True,
        and aggregated is not supported for the 'timestamp' xaxis format

        Parameters
        ----------
        metric_names : list[str]
            the names of metrics to return values for
        xaxis : Literal['step', 'time', 'timestamp']
            the xaxis type
        output_format : Literal['dataframe', 'list']
            the format of the output, either a list or a Pandas dataframe
        run_ids : list[str], optional
            list of runs by id to include within metric retrieval
        run_filters : list[str]
            filters for specifying runs to include
        use_run_names : bool, optional
            use run names as opposed to IDs, note this is not recommended for
            multiple runs with the same name. Default is False.
        aggregate : bool, optional
            return results as averages (not compatible with xaxis=timestamp),
            default is False
        max_points : int, optional
            maximum number of data points, by default None (all)

        Returns
        -------
        dict or DataFrame or None
            values for the given metric at each time interval
            if no runs pass filtering then return None
        """
        if not metric_names:
            raise ValueError("No metric names were provided")

        if run_filters and run_ids:
            raise AssertionError(
                "Specification of both 'run_ids' and 'run_filters' "
                "in get_metric_values is ambiguous"
            )

        if xaxis == "timestamp" and aggregate:
            raise AssertionError(
                "Cannot return metric values with options 'aggregate=True' and "
                "'xaxis=timestamp'"
            )

        if run_filters is not None:
            if not (filtered_runs := self.get_runs(filters=run_filters)):
                return None

            run_ids = [run["id"] for run in filtered_runs if run["id"]]

            if use_run_names:
                run_labels = [run["name"] for run in filtered_runs]
        elif run_ids is not None:
            if use_run_names:
                run_labels = [
                    self.get_run_name_from_id(run_id) for run_id in run_ids if run_id
                ]
        else:
            raise AssertionError(
                "Expected either argument 'run_ids' or 'run_filters' for get_metric_values"
            )

        if not run_ids or any(not i for i in run_ids):
            raise ValueError(
                f"Expected list of run identifiers for 'run_ids' but got '{run_ids}'"
            )

        if not use_run_names:
            run_labels = run_ids

        # Now get the metrics for each run
        run_metrics = self._get_run_metrics_from_server(
            metric_names=metric_names,
            run_ids=run_ids,
            xaxis=xaxis,
            aggregate=aggregate,
            max_points=max_points,
        )

        if not run_metrics:
            return None

        if aggregate:
            return aggregated_metrics_to_dataframe(
                run_metrics, xaxis=xaxis, parse_to=output_format
            )
        else:
            return parse_run_set_metrics(
                run_metrics, xaxis=xaxis, run_labels=run_labels, parse_to=output_format
            )

    @check_extra("plot")
    @prettify_pydantic
    @pydantic.validate_call
    def plot_metrics(
        self,
        run_ids: list[str],
        metric_names: list[str],
        xaxis: typing.Literal["step", "time"],
        *,
        max_points: typing.Optional[int] = None,
    ) -> typing.Any:
        """Plt the time series values for multiple metrics/runs

        Parameters
        ----------
        run_ids : list[str]
            unique identifiers for runs to plot
        metric_names : list[str]
            names of metrics to plot
        xaxis : str, ('step' | 'time' | 'timestep')
            the x axis to plot against
        max_points : int, optional
            maximum number of data points, by default None (all)

        Returns
        -------
        Figure
            plot figure object

        Raises
        ------
        ValueError
            if invalid arguments are provided
        """
        if not isinstance(run_ids, list):
            raise ValueError("Invalid runs specified, must be a list of run names.")

        if not isinstance(metric_names, list):
            raise ValueError("Invalid names specified, must be a list of metric names.")

        data: DataFrame = self.get_metric_values(  # type: ignore
            run_ids=run_ids,
            metric_names=metric_names,
            xaxis=xaxis,
            max_points=max_points,
            output_format="dataframe",
            aggregate=False,
        )

        if data is None:
            raise RuntimeError(
                f"Cannot plot metrics {metric_names}, "
                f"no data found for runs {run_ids}."
            )

        # Undo multi-indexing
        flattened_df = data.reset_index()

        import matplotlib.pyplot as plt

        for run in run_ids:
            for name in metric_names:
                label = None
                if len(run_ids) > 1 and len(metric_names) > 1:
                    label = f"{run}: {name}"
                elif len(run_ids) > 1 and len(metric_names) == 1:
                    label = run
                elif len(run_ids) == 1 and len(metric_names) > 1:
                    label = name

                flattened_df.plot(y=name, x=xaxis, label=label)

        if xaxis == "step":
            plt.xlabel("Steps")
        elif xaxis == "time":
            plt.xlabel("Relative Time")
        if xaxis == "step":
            plt.xlabel("steps")
        elif xaxis == "timestamp":
            plt.xlabel("Time")

        if len(metric_names) == 1:
            plt.ylabel(metric_names[0])

        return plt.figure()

    @prettify_pydantic
    @pydantic.validate_call
    def get_events(
        self,
        run_id: str,
        *,
        message_contains: typing.Optional[str] = None,
        start_index: typing.Optional[pydantic.NonNegativeInt] = None,
        count_limit: typing.Optional[pydantic.PositiveInt] = None,
    ) -> list[dict[str, str]]:
        """Return events for a specified run

        Parameters
        ----------
        run_id : str
            the unique identifier of the run to query
        message_contains : str, optional
            filter to events with message containing this expression, by default None
        start_index : typing.int, optional
            slice results returning only those above this index, by default None
        count_limit : typing.int, optional
            limit number of returned results, by default None

        Returns
        -------
        list[dict[str, str]]
            list of matching events containing entries with message and timestamp data

        Raises
        ------
        RuntimeError
            if there was a failure retrieving information from the server
        """

        msg_filter: str = (
            json.dumps([f"event.message contains {message_contains}"])
            if message_contains
            else ""
        )

        params: dict[str, typing.Union[str, int]] = {
            "run": run_id,
            "filters": msg_filter,
            "start": start_index or 0,
            "count": count_limit or 0,
        }

        response = requests.get(
            f"{self._user_config.server.url}/api/events",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Retrieval of events for run '{run_id}'",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response when retrieving events"
            )

        return response.json().get("data", [])

    @prettify_pydantic
    @pydantic.validate_call
    def get_alerts(
        self,
        *,
        run_id: typing.Optional[str] = None,
        critical_only: bool = True,
        names_only: bool = True,
        start_index: typing.Optional[pydantic.NonNegativeInt] = None,
        count_limit: typing.Optional[pydantic.PositiveInt] = None,
    ) -> list[dict[str, typing.Any]]:
        """Retrieve alerts for a given run

        Parameters
        ----------
        run_id : str | None
            The ID of the run to find alerts for
        critical_only : bool, optional
            If a run is specified, whether to only return details about alerts which are currently critical, by default True
        names_only: bool, optional
            Whether to only return the names of the alerts (otherwise return the full details of the alerts), by default True
        start_index : typing.int, optional
            slice results returning only those above this index, by default None
        count_limit : typing.int, optional
            limit number of returned results, by default None

        Returns
        -------
        list[dict[str, Any]]
            a list of all alerts for this run which match the constrains specified

        Raises
        ------
        RuntimeError
            if there was a failure retrieving data from the server
        """
        params: dict[str, int] = {"count": count_limit or 0, "start": start_index or 0}
        if not run_id:
            response = requests.get(
                f"{self._user_config.server.url}/api/alerts/",
                headers=self._headers,
                params=params,
            )

            json_response = self._get_json_from_response(
                expected_status=[http.HTTPStatus.OK],
                scenario=f"Retrieval of alerts for run '{run_id}'",
                response=response,
            )
        else:
            response = requests.get(
                f"{self._user_config.server.url}/api/runs/{run_id}",
                headers=self._headers,
                params=params,
            )

            json_response = self._get_json_from_response(
                expected_status=[200],
                scenario=f"Retrieval of alerts for run '{run_id}'",
                response=response,
            )

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response when retrieving alerts"
            )

        if run_id and (alerts := json_response.get("alerts")) is None:
            raise RuntimeError(
                "Expected key 'alerts' in response when retrieving "
                f"alerts for run '{run_id}': {json_response}"
            )
        elif not run_id and (alerts := json_response.get("data")) is None:
            raise RuntimeError(
                "Expected key 'data' in response when retrieving "
                f"alerts: {json_response}"
            )

        if run_id and critical_only:
            if names_only:
                return [
                    alert["alert"].get("name")
                    for alert in alerts
                    if alert["status"].get("current") == "critical"
                ]
            else:
                return [
                    alert
                    for alert in alerts
                    if alert["status"].get("current") == "critical"
                ]
        if names_only:
            if run_id:
                return [alert["alert"].get("name") for alert in alerts]
            else:
                return [alert.get("name") for alert in alerts]

        return alerts

    @prettify_pydantic
    @pydantic.validate_call
    def get_tags(
        self,
        *,
        start_index: typing.Optional[pydantic.NonNegativeInt] = None,
        count_limit: typing.Optional[pydantic.PositiveInt] = None,
    ) -> list[dict]:
        """Retrieve tags

        Parameters
        ----------
        start_index : typing.int, optional
            slice results returning only those above this index, by default None
        count_limit : typing.int, optional
            limit number of returned results, by default None

        Returns
        -------
        list[dict[str, Any]]
            a list of all tags for this run which match the constrains specified

        Raises
        ------
        RuntimeError
            if there was a failure retrieving data from the server
        """
        params = {"count": count_limit or 0, "start": start_index or 0}
        response = requests.get(
            f"{self._user_config.server.url}/api/tags",
            headers=self._headers,
            params=params,
        )

        json_response = self._get_json_from_response(
            expected_status=[200],
            scenario="Retrieval of tags",
            response=response,
        )

        if not isinstance(json_response, dict):
            raise RuntimeError("Expected list from JSON response when retrieving tags")

        if not (data := json_response.get("data")):
            raise RuntimeError("Expected key 'data' in response during tags retrieval")

        return data

    @prettify_pydantic
    @pydantic.validate_call
    def delete_tag(self, tag_id: str) -> None:
        """Delete a tag by its identifier

        Parameters
        ----------
        tag_id : str
            unique identifier for the tag

        Raises
        ------
        RuntimeError
            if the deletion failed due to a server request error
        """

        response = requests.delete(
            f"{self._user_config.server.url}/api/tags/{tag_id}",
            headers=self._headers,
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK],
            scenario=f"Deletion of tag '{tag_id}'",
            response=response,
        )

        logger.debug(f"Tag '{tag_id}' deleted successfully")

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during run deletion "
                f"but got '{type(json_response)}'"
            )

        return json_response or None

    @prettify_pydantic
    @pydantic.validate_call
    def get_tag(self, tag_id: str) -> typing.Optional[dict[str, typing.Any]]:
        """Retrieve a single tag

        Parameters
        ----------
        tag_id : str
            the unique identifier for this tag

        Returns
        -------
        dict[str, Any]
            response containing information on the given tag

        Raises
        ------
        RuntimeError
            if retrieval of information from the server on this tag failed
        """

        response: requests.Response = requests.get(
            f"{self._user_config.server.url}/api/tag/{tag_id}", headers=self._headers
        )

        json_response = self._get_json_from_response(
            expected_status=[http.HTTPStatus.OK, http.HTTPStatus.NOT_FOUND],
            scenario=f"Retrieval of tag '{tag_id}'",
            response=response,
        )

        if response.status_code == http.HTTPStatus.NOT_FOUND:
            return None

        if not isinstance(json_response, dict):
            raise RuntimeError(
                "Expected dictionary from JSON response during tag retrieval "
                f"but got '{type(json_response)}'"
            )
        return json_response
