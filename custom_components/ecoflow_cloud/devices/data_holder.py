import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from collections import OrderedDict
from typing import Any, Protocol, TypeVar

import jsonpath_ng.ext as jp
from homeassistant.util import dt

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


class BoundFifoList(list[_T]):
    def __init__(self, maxlen=20) -> None:
        super().__init__()
        self.maxlen = maxlen

    def append(self, __object: _T) -> None:
        super().insert(0, __object)
        while len(self) >= self.maxlen:
            self.pop()


@dataclasses.dataclass
class PreparedData:
    online: bool | None
    params: dict[str, Any] | None
    raw_data: dict[str, Any] | None
    is_auto: bool = True


class DataStatusCallback(Protocol):
    def on_explicit_status(self, online: bool) -> None: ...
    def on_data_received(self) -> None: ...


class _NoOpStatusCallback:
    def on_explicit_status(self, online: bool) -> None: pass
    def on_data_received(self) -> None: pass


class EcoflowDataHolder:
    def __init__(
        self,
        module_sn: str | None = None,
        collect_raw: bool = False,
        status_callback: DataStatusCallback | None = None,
    ):
        self._status_callback: DataStatusCallback = status_callback or _NoOpStatusCallback()
        self.module_sn = module_sn

        self.params = dict[str, Any]()

        self.__collect_raw = collect_raw
        self.set_params = BoundFifoList[dict[str, Any]]()
        self.set_params_time = dt.utcnow().replace(year=2000, month=1, day=1, hour=0, minute=0, second=0)

        self.set = BoundFifoList[dict[str, Any]]()
        self.set_reply = BoundFifoList[dict[str, Any]]()
        self.set_reply_time = dt.utcnow().replace(year=2000, month=1, day=1, hour=0, minute=0, second=0)
        self._set_reply_condition = threading.Condition()
        self._set_reply_by_id: OrderedDict[str, dict[str, Any]] = OrderedDict()

        self.get = BoundFifoList[dict[str, Any]]()
        self.get_reply = BoundFifoList[dict[str, Any]]()
        self.get_reply_time = dt.utcnow().replace(year=2000, month=1, day=1, hour=0, minute=0, second=0)

        self.set_status = BoundFifoList[dict[str, Any]]()
        self.set_status_time = dt.utcnow().replace(year=2000, month=1, day=1, hour=0, minute=0, second=0)

    def last_received_time(self):
        return max(
            self.set_status_time,
            self.set_params_time,
            # 1. get_reply can receive '"message": "The device is not online"'
            # 2. if device is online - get_reply message will update params, so param_time will be updated as well
            # , self.get_reply_time, self.set_reply_time
        )

    def add_set_message(self, data: PreparedData):
        self.__accept_prepared_data(data, self.set.append)
        self.set_time = dt.utcnow()

    def add_set_reply_message(self, data: PreparedData):
        self.__accept_prepared_data(data, self.set_reply.append)
        self.set_reply_time = dt.utcnow()
        if isinstance(data.raw_data, dict):
            message_id = data.raw_data.get("id")
            if message_id is not None:
                with self._set_reply_condition:
                    self._set_reply_by_id[str(message_id)] = data.raw_data
                    while len(self._set_reply_by_id) > 100:
                        self._set_reply_by_id.popitem(last=False)
                    self._set_reply_condition.notify_all()

    def wait_for_set_reply(self, message_id: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._set_reply_condition:
            while True:
                reply = self._set_reply_by_id.get(str(message_id))
                if reply is not None:
                    return reply
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._set_reply_condition.wait(remaining)

    def add_get_message(self, data: PreparedData):
        self.__accept_prepared_data(data, self.get.append)
        self.get_time = dt.utcnow()

    def add_get_reply_message(self, data: PreparedData):
        self.__accept_prepared_data(data, self.get_reply.append)
        self.get_reply_time = dt.utcnow()

    def update_to_target_state(self, target_state: dict[str, Any]):
        # key can be xpath!
        for key, value in target_state.items():
            jp.parse(key).update(self.params, value)

        self.set_params_time = dt.utcnow()

    def add_status(self, data: PreparedData):
        self.__accept_prepared_data(data, self.set_status.append)
        self.set_status_time = dt.utcnow()

    def add_data(self, data: PreparedData):
        if data.params is not None and self.module_sn is not None:
            if "moduleSn" not in data.params:
                return
            if data.params["moduleSn"] != self.module_sn:
                return

        self.__accept_prepared_data(data, self.__update_params)

    def __update_params(self, params: dict[str, Any]):
        if "params" in params:
            self.params.update(params["params"])
            self.set_params_time = dt.utcnow()

    def __accept_prepared_data(self, data: PreparedData, raw_data_acceptor: Callable[[dict[str, Any]], None]):
        if data.online is not None:
            self._status_callback.on_explicit_status(data.online)

        if data.params is not None:
            self.__update_params(data.params)
            # Only auto-mark online for MQTT messages; API data doesn't prove device is online
            if data.is_auto:
                self._status_callback.on_data_received()

        if self.__collect_raw and data.raw_data is not None:
            raw_data_acceptor(data.raw_data)
