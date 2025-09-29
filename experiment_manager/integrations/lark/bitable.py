"""Thin wrapper around Lark (Feishu) Bitable SDK operations."""

from __future__ import annotations

import hashlib
import json
import os
import time
import zlib
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:  # pragma: no cover - import guard for optional dependency resolution
    import lark_oapi as lark
    from lark_oapi.api.bitable.v1 import (
        AppTableRecord,
        CreateAppTableRecordRequest,
        ListAppTableFieldRequest,
    )
    from lark_oapi.api.drive.v1 import (
        UploadAllMediaRequest,
        UploadAllMediaRequestBody,
        UploadAllMediaResponse,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - handled gracefully below
    lark = None  # type: ignore
    AppTableRecord = None  # type: ignore
    CreateAppTableRecordRequest = None  # type: ignore
    UploadAllMediaRequest = None  # type: ignore
    UploadAllMediaRequestBody = None  # type: ignore
    UploadAllMediaResponse = None  # type: ignore
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class LarkBitableSdkNotInstalled(ImportError):
    """Raised when the official Lark SDK is not available in the environment."""


class LarkBitableError(RuntimeError):
    """Generic failure raised when invoking the Lark Bitable API."""


@dataclass(frozen=True)
class LarkBitableConfig:
    app_id: str
    app_secret: str
    app_token: str
    table_id: str
    view_id: Optional[str] = None
    user_id_type: Optional[str] = None
    client_token: Optional[str] = None
    ignore_consistency_check: Optional[bool] = None
    log_level: Optional[str] = None

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "LarkBitableConfig":
        missing: List[str] = [key for key in ("app_id", "app_secret", "app_token", "table_id") if not mapping.get(key)]
        if missing:
            raise LarkBitableError(f"配置缺少必要字段: {', '.join(missing)}")

        ignore_check = mapping.get("ignore_consistency_check")
        return cls(
            app_id=str(mapping["app_id"]),
            app_secret=str(mapping["app_secret"]),
            app_token=str(mapping["app_token"]),
            table_id=str(mapping["table_id"]),
            view_id=_optional_str(mapping.get("view_id")),
            user_id_type=_optional_str(mapping.get("user_id_type")),
            client_token=_optional_str(mapping.get("client_token")),
            ignore_consistency_check=_coerce_bool(ignore_check),
            log_level=_optional_str(mapping.get("log_level")),
        )


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "t", "yes", "y"}:
            return True
        if lowered in {"0", "false", "f", "no", "n"}:
            return False
    raise LarkBitableError(f"无法解析布尔值: {value!r}")


def _ensure_sdk_available() -> None:
    if _IMPORT_ERROR is not None or lark is None or AppTableRecord is None or CreateAppTableRecordRequest is None:
        raise LarkBitableSdkNotInstalled(
            "未安装 lark-oapi，请运行 `pip install lark-oapi` 或使用 README 中的依赖配置"
        )


_CLIENT_CACHE: Dict[Tuple[str, str, Optional[str]], Any] = {}
_CLIENT_CACHE_LOCK = Lock()
_FIELD_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, int]]] = {}
_FIELD_CACHE_TTL_SECONDS = 300.0


def _resolve_log_level(config: LarkBitableConfig):
    level_name = (config.log_level or "ERROR").upper()
    if hasattr(lark.LogLevel, level_name):  # type: ignore[attr-defined]
        return getattr(lark.LogLevel, level_name)
    return lark.LogLevel.ERROR  # type: ignore[attr-defined]


def _get_client(config: LarkBitableConfig):
    key = (config.app_id, config.app_secret, config.log_level)
    with _CLIENT_CACHE_LOCK:
        client = _CLIENT_CACHE.get(key)
        if client is not None:
            return client
        builder = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret)
        builder = builder.log_level(_resolve_log_level(config))
        client = builder.build()
        _CLIENT_CACHE[key] = client
        return client


class LarkBitableClient:
    """High-level helper around the official Lark SDK."""

    def __init__(self, config: LarkBitableConfig):
        _ensure_sdk_available()
        self._config = config
        self._client = _get_client(config)

    def create_record(self, fields: Mapping[str, Any]) -> List[str]:
        request_builder = CreateAppTableRecordRequest.builder()  # type: ignore[assignment]
        request_builder = request_builder.app_token(self._config.app_token)
        request_builder = request_builder.table_id(self._config.table_id)
        if self._config.view_id:
            # Create record API does not accept a view_id parameter. Keep value for potential
            # future use but skip adding it to the request to avoid attribute errors.
            pass
        if self._config.user_id_type:
            request_builder = request_builder.user_id_type(self._config.user_id_type)
        if self._config.client_token:
            request_builder = request_builder.client_token(self._config.client_token)
        if self._config.ignore_consistency_check is not None:
            request_builder = request_builder.ignore_consistency_check(self._config.ignore_consistency_check)

        record_payload = AppTableRecord.builder().fields(dict(fields)).build()  # type: ignore[call-arg]
        request = request_builder.request_body(record_payload).build()

        try:
            response = self._client.bitable.v1.app_table_record.create(request)
        except Exception as exc:  # pragma: no cover - SDK wraps errors
            raise LarkBitableError(f"调用飞书接口失败: {exc}") from exc

        if not response.success():
            details = {
                "code": response.code,
                "msg": response.msg,
                "log_id": response.get_log_id() if hasattr(response, "get_log_id") else None,
            }
            try:
                raw = response.raw.content  # type: ignore[attr-defined]
                if raw:
                    details["response"] = json.loads(raw)
            except Exception:
                pass
            raise LarkBitableError(
                "飞书接口返回错误: "
                + json.dumps(details, ensure_ascii=False)
            )

        return _extract_record_ids(response)

    def list_field_names(self, *, use_cache: bool = True) -> Dict[str, int]:
        cache_key = (self._config.app_token, self._config.table_id)
        now = time.monotonic()

        if use_cache:
            cached = _FIELD_CACHE.get(cache_key)
            if cached is not None:
                cached_at, names = cached
                if now - cached_at <= _FIELD_CACHE_TTL_SECONDS:
                    return dict(names)

        field_types: Dict[str, int] = {}
        page_token: Optional[str] = None

        while True:
            builder = ListAppTableFieldRequest.builder()
            builder = builder.app_token(self._config.app_token)
            builder = builder.table_id(self._config.table_id)
            builder = builder.page_size(100)
            if self._config.view_id:
                builder = builder.view_id(self._config.view_id)
            if page_token:
                builder = builder.page_token(page_token)

            request = builder.build()

            try:
                response = self._client.bitable.v1.app_table_field.list(request)
            except Exception as exc:  # pragma: no cover - SDK wraps errors
                raise LarkBitableError(f"获取飞书字段失败: {exc}") from exc

            if not response.success():
                details = {
                    "code": response.code,
                    "msg": response.msg,
                    "log_id": response.get_log_id() if hasattr(response, "get_log_id") else None,
                }
                data = getattr(response, "data", None)
                if data is not None:
                    try:
                        raw = response.raw.content  # type: ignore[attr-defined]
                        if raw:
                            details["response"] = json.loads(raw)
                    except Exception:
                        pass
                raise LarkBitableError(
                    "获取飞书字段失败: " + json.dumps(details, ensure_ascii=False)
                )

            data = getattr(response, "data", None)
            items = getattr(data, "items", None) if data is not None else None
            if items:
                for item in items:
                    field_name = getattr(item, "field_name", None)
                    if not field_name:
                        continue
                    type_code = getattr(item, "type", None)
                    if isinstance(type_code, int):
                        field_types[str(field_name)] = type_code
                    else:
                        field_types[str(field_name)] = -1

            has_more = bool(getattr(data, "has_more", False)) if data is not None else False
            if not has_more:
                break

            page_token = getattr(data, "page_token", None) or getattr(data, "next_page_token", None)
            if not page_token:
                break

        _FIELD_CACHE[cache_key] = (now, dict(field_types))
        return dict(field_types)

    def upload_file(self, file_path: str, parent_type: str = "bitable_file") -> str:
        """
        上传文件到飞书云文档并返回 file_token
        
        Args:
            file_path: 本地文件路径
            parent_type: 上传点类型，对于多维表格附件默认为 "bitable_file"
            
        Returns:
            上传成功后的 file_token
            
        Raises:
            LarkBitableError: 上传失败时抛出
            FileNotFoundError: 文件不存在时抛出
        """
        _ensure_sdk_available()
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
            
        # 计算文件大小和校验和
        file_size = os.path.getsize(file_path)
        
        with open(file_path, "rb") as f:
            file_content = f.read()
            # 使用 Adler-32 校验和（飞书API要求）
            checksum = str(zlib.adler32(file_content) & 0xffffffff)
        
        # 构造上传请求
        with open(file_path, "rb") as file:
            request_builder = UploadAllMediaRequestBody.builder() \
                .file_name(os.path.basename(file_path)) \
                .parent_type(parent_type) \
                .parent_node(self._config.app_token) \
                .size(str(file_size)) \
                .checksum(checksum) \
                .file(file)
            
            request = UploadAllMediaRequest.builder() \
                .request_body(request_builder.build()) \
                .build()
            
            # 发起上传请求
            try:
                response = self._client.drive.v1.media.upload_all(request)
            except Exception as exc:  # pragma: no cover - SDK wraps errors
                raise LarkBitableError(f"文件上传失败: {exc}") from exc
            
            # 处理响应
            if not response.success():
                details = {
                    "code": response.code,
                    "msg": response.msg,
                    "log_id": response.get_log_id() if hasattr(response, "get_log_id") else None,
                }
                try:
                    raw = response.raw.content
                    if raw:
                        details["response"] = json.loads(raw)
                except Exception:
                    pass
                raise LarkBitableError(
                    "文件上传失败: " + json.dumps(details, ensure_ascii=False)
                )
            
            # 提取 file_token
            data = getattr(response, "data", None)
            if data is None:
                raise LarkBitableError("文件上传响应中缺少数据")
                
            file_token = getattr(data, "file_token", None)
            if not file_token:
                raise LarkBitableError("文件上传响应中缺少 file_token")
                
            return str(file_token)


def _extract_record_ids(response: Any) -> List[str]:
    record_ids: List[str] = []
    data = getattr(response, "data", None)
    if data is None:
        return record_ids

    candidates: Iterable[Any] = []
    if getattr(data, "record", None) is not None:
        candidates = [data.record]
    elif getattr(data, "records", None):
        candidates = data.records

    for item in candidates:
        record_id = getattr(item, "record_id", None) or getattr(item, "id", None)
        if record_id:
            record_ids.append(str(record_id))
    return record_ids


def sync_record(mapping: Mapping[str, Any], fields: Mapping[str, Any]) -> List[str]:
    """Convenience helper to create a record using dict-style configuration."""

    config = LarkBitableConfig.from_mapping(mapping)
    client = LarkBitableClient(config)
    return client.create_record(fields)


def list_field_names(mapping: Mapping[str, Any], *, use_cache: bool = True) -> Dict[str, int]:
    """Return available field names mapped to their type codes for the given configuration."""

    config = LarkBitableConfig.from_mapping(mapping)
    client = LarkBitableClient(config)
    return client.list_field_names(use_cache=use_cache)


def upload_file_to_lark(mapping: Mapping[str, Any], file_path: str, parent_type: str = "bitable_file") -> str:
    """
    上传文件到飞书云文档并返回 file_token
    
    Args:
        mapping: 飞书配置字典
        file_path: 本地文件路径
        parent_type: 上传点类型，对于多维表格附件默认为 "bitable_file"
        
    Returns:
        上传成功后的 file_token
    """
    config = LarkBitableConfig.from_mapping(mapping)
    client = LarkBitableClient(config)
    return client.upload_file(file_path, parent_type)
