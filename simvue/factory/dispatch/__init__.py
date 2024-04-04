"""
Dispatch
========

Contains factory method for selecting dispatcher type based on Simvue Configuration
"""

import typing

if typing.TYPE_CHECKING:
    from .base import DispatcherBaseClass
    from threading import Event

from .queued import QueuedDispatcher
from .prompt import PromptDispatcher


def Dispatcher(
    mode: typing.Literal["prompt", "queued"],
    callback: typing.Callable[[list[typing.Any], str, dict[str, typing.Any]], None],
    object_types: list[str],
    termination_trigger: "Event",
    attributes: dict[str, typing.Any] | None = None,
    **kwargs,
) -> "DispatcherBaseClass":
    """Returns instance of dispatcher based on configuration

    Options are 'queued' which is the default and adds objects to a queue as well
    as restricts the rate of dispatch, and 'prompt' which executes the callback
    immediately

    Parameters
    ----------
    mode : typing.Literal['prompt', 'queued']
        _description_
    callback : typing.Callable[[list[typing.Any], str, dict[str, typing.Any]], None]
        callback to be executed on each item provided
    object_types : list[str]
        categories, this is mainly used for creation of queues in a QueueDispatcher
    termination_trigger : Event
        event which triggers termination of the dispatcher
    attributes : dict[str, typing.Any] | None, optional
        any additional attributes to be provided to the callback, by default None

    Returns
    -------
    DispatcherBaseClass
        either a PromptDispatcher or QueueDispatcher instance
    """
    if mode == "prompt":
        return PromptDispatcher(
            callback=callback,
            object_types=object_types,
            termination_trigger=termination_trigger,
            attributes=attributes,
            **kwargs,
        )
    else:
        return QueuedDispatcher(
            callback=callback,
            object_types=object_types,
            termination_trigger=termination_trigger,
            attributes=attributes,
            **kwargs,
        )