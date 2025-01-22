from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class GRPCActionRequest(_message.Message):
    __slots__ = ("uid", "action")
    UID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    uid: str
    action: str
    def __init__(self, uid: _Optional[str] = ..., action: _Optional[str] = ...) -> None: ...

class GRPCActionResponse(_message.Message):
    __slots__ = ("uid", "action", "status")
    UID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    uid: str
    action: str
    status: str
    def __init__(self, uid: _Optional[str] = ..., action: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class GRPCDescribeTargetRequest(_message.Message):
    __slots__ = ("target_id",)
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    target_id: str
    def __init__(self, target_id: _Optional[str] = ...) -> None: ...

class GRPCDescribeTargetResponse(_message.Message):
    __slots__ = ("target_id", "description")
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    target_id: str
    description: str
    def __init__(self, target_id: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class GRPCInputRequest(_message.Message):
    __slots__ = ("uid",)
    UID_FIELD_NUMBER: _ClassVar[int]
    uid: str
    def __init__(self, uid: _Optional[str] = ...) -> None: ...

class GRPCInputResponse(_message.Message):
    __slots__ = ("uid", "data")
    UID_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    uid: str
    data: bytes
    def __init__(self, uid: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class GRPCLogRequest(_message.Message):
    __slots__ = ("logger_name",)
    LOGGER_NAME_FIELD_NUMBER: _ClassVar[int]
    logger_name: str
    def __init__(self, logger_name: _Optional[str] = ...) -> None: ...

class GRPCLogResponse(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: str
    def __init__(self, data: _Optional[str] = ...) -> None: ...

class GRPCRunImageRequest(_message.Message):
    __slots__ = ("target_id", "breakpoints", "path", "digest", "data", "execution_timeout_in_seconds")
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    BREAKPOINTS_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    DIGEST_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_TIMEOUT_IN_SECONDS_FIELD_NUMBER: _ClassVar[int]
    target_id: str
    breakpoints: _containers.RepeatedScalarFieldContainer[int]
    path: str
    digest: str
    data: bytes
    execution_timeout_in_seconds: float
    def __init__(self, target_id: _Optional[str] = ..., breakpoints: _Optional[_Iterable[int]] = ..., path: _Optional[str] = ..., digest: _Optional[str] = ..., data: _Optional[bytes] = ..., execution_timeout_in_seconds: _Optional[float] = ...) -> None: ...

class GRPCRunImageResponse(_message.Message):
    __slots__ = ("target_id", "path", "digest", "output", "status", "load_duration_in_seconds", "execution_duration_in_seconds")
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    DIGEST_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LOAD_DURATION_IN_SECONDS_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_DURATION_IN_SECONDS_FIELD_NUMBER: _ClassVar[int]
    target_id: str
    path: str
    digest: str
    output: bytes
    status: str
    load_duration_in_seconds: float
    execution_duration_in_seconds: float
    def __init__(self, target_id: _Optional[str] = ..., path: _Optional[str] = ..., digest: _Optional[str] = ..., output: _Optional[bytes] = ..., status: _Optional[str] = ..., load_duration_in_seconds: _Optional[float] = ..., execution_duration_in_seconds: _Optional[float] = ...) -> None: ...
