"""High-level helpers shared by Experiment and Lark integrations."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Union
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse

from experiment_manager.integrations.lark.bitable import (
    LarkBitableClient,
    LarkBitableConfig,
    LarkBitableError,
    LarkBitableSdkNotInstalled,
    list_field_names,
    sync_record,
    upload_file_to_lark,
)

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
BITABLE_TEXT_FIELD_TYPES = {1}              # 文本字段
BITABLE_NUMBER_FIELD_TYPES = {2}            # 数字字段
BITABLE_SINGLE_SELECT_FIELD_TYPES = {3}     # 单选字段
BITABLE_MULTI_SELECT_FIELD_TYPES = {4}      # 多选字段
BITABLE_DATE_FIELD_TYPES = {5}              # 日期字段
BITABLE_ATTACHMENT_FIELD_TYPES = {17}       # 附件字段

def coerce_lark_config_input(config: Optional[Union[Dict[str, str], str, Mapping[str, str]]]) -> Dict[str, str]:
    if not config:
        return {}
    if isinstance(config, str):
        return {"url": config}
    if isinstance(config, Mapping):
        return {key: value for key, value in config.items() if value is not None}
    raise TypeError("lark_config 必须为 str URL 或包含配置的字典")


def expand_lark_config(config: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not config:
        return None

    expanded = dict(config)
    url_keys = ("url", "table_url", "bitable_url")
    for key in url_keys:
        url_value = expanded.get(key)
        if not url_value:
            continue
        tokens = parse_bitable_url(url_value)
        if not tokens:
            continue
        for field in ("app_token", "table_id", "view_id"):
            value = tokens.get(field)
            if value:
                expanded[field] = value
    return expanded


_APP_TOKEN_RE = re.compile(r"app[0-9A-Za-z]{5,}")
_TABLE_TOKEN_RE = re.compile(r"tbl[0-9A-Za-z]{5,}")
_VIEW_TOKEN_RE = re.compile(r"vew[0-9A-Za-z]{5,}")
_URL_QUERY_KEYS = {
    "table_id": ("table", "tableId", "table_id"),
    "view_id": ("view", "viewId", "view_id"),
}


def parse_bitable_url(url: str) -> Dict[str, str]:
    """Extract ``app_token``/``table_id``/``view_id`` from a Feishu Bitable URL."""

    if not url:
        return {}

    parsed = urlparse(url)
    tokens: Dict[str, str] = {}

    path_segments = [segment for segment in parsed.path.split("/") if segment]
    for segment in path_segments:
        if _APP_TOKEN_RE.fullmatch(segment):
            tokens["app_token"] = segment
            break

    if "app_token" not in tokens:
        for idx, segment in enumerate(path_segments[:-1]):
            if segment.lower() in {"base", "bitable"}:
                candidate = path_segments[idx + 1]
                if candidate:
                    tokens["app_token"] = candidate
                break

    def _consume_query(query: str) -> None:
        if not query:
            return
        qs = parse_qs(query, keep_blank_values=True)
        for key, aliases in _URL_QUERY_KEYS.items():
            for alias in aliases:
                values = qs.get(alias)
                if values:
                    value = values[0]
                    if value:
                        tokens.setdefault(key, value)
                        break

    _consume_query(parsed.query)

    if "=" in parsed.fragment:
        _consume_query(parsed.fragment)

    if "app_token" not in tokens:
        for segment in path_segments:
            match = _APP_TOKEN_RE.search(segment)
            if match:
                tokens["app_token"] = match.group(0)
                break
        else:
            match = _APP_TOKEN_RE.search(parsed.fragment)
            if match:
                tokens["app_token"] = match.group(0)

    for token_key, pattern in (("table_id", _TABLE_TOKEN_RE), ("view_id", _VIEW_TOKEN_RE)):
        if token_key in tokens:
            continue
        for segment in path_segments:
            match = pattern.search(segment)
            if match:
                tokens[token_key] = match.group(0)
                break
        else:
            match = pattern.search(parsed.fragment)
            if match:
                tokens[token_key] = match.group(0)

    return tokens


# 将原始记录 row 转换为适配飞书字段 field_types 格式的数据并返回
def normalize_lark_fields(fields_data: dict, field_types: dict, config: Optional[Mapping[str, Any]] = None) -> dict:
    """
    根据字段类型标准化数据格式以适配飞书多维表格的API
    Args:
        fields_data: 待标准化的字段数据字典
        field_types: 字段名到字段类型代码的映射字典 (str -> int)
        config: 飞书配置（附件字段需要用）
    Returns:
        标准化后的字段数据字典
    """
    result = {} # 存储标准化后的结果
    
    for field_name, value in fields_data.items():
        if field_name not in field_types:
            raise ValueError(f"字段 '{field_name}' 不存在于目标表格中")
            
        field_type = field_types[field_name]    # 获取字段类型 (数字代码)
        
        if field_type in BITABLE_TEXT_FIELD_TYPES:
            # 文本类型字段，确保值是字符串
            result[field_name] = str(value) if value is not None else ""
            
        elif field_type in BITABLE_NUMBER_FIELD_TYPES:
            # 数值类型字段，确保值是数字
            try:
                result[field_name] = float(value) if value is not None else 0
            except (ValueError, TypeError):
                raise ValueError(f"字段 '{field_name}' 期望数值，但收到: {value!r}") from None
                
        elif field_type in BITABLE_SINGLE_SELECT_FIELD_TYPES:
            # 单选字段，接受选项ID或选项名称
            # 飞书API接受选项ID字符串或选项名称字符串
            if value is not None:
                result[field_name] = str(value)
            else:
                result[field_name] = ""
                
        elif field_type in BITABLE_MULTI_SELECT_FIELD_TYPES:
            # 多选字段，接受选项ID数组或选项名称数组  
            # 飞书API接受选项ID字符串数组或选项名称字符串数组
            if value is not None:
                if isinstance(value, (list, tuple)):
                    # 如果已经是列表或元组，转换每个元素为字符串
                    result[field_name] = [str(item) for item in value]
                else:
                    # 如果是单个值，包装为列表
                    result[field_name] = [str(value)]
            else:
                result[field_name] = []
                
        elif field_type in BITABLE_DATE_FIELD_TYPES:
            # 日期类型字段，转换为时间戳（毫秒）
            if value is not None:
                if isinstance(value, datetime):
                    # 确保datetime对象有时区信息
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=LOCAL_TZ)
                    timestamp = int(value.timestamp() * 1000)
                    result[field_name] = timestamp
                elif isinstance(value, str):
                    if value == "":
                        # 空字符串跳过（不包含在结果中）
                        continue
                    try:
                        # 尝试解析字符串为datetime
                        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=LOCAL_TZ)
                        timestamp = int(dt.timestamp() * 1000)
                        result[field_name] = timestamp
                    except ValueError:
                        # 解析失败
                        raise ValueError(f"字段 '{field_name}' 期望日期时间字符串，但收到: {value!r}") from None
                else:
                    # 其他类型
                    raise ValueError(f"字段 '{field_name}' 期望日期时间，但收到: {value!r}") from None
            else:
                # 空值
                raise ValueError(f"字段 '{field_name}' 期望日期时间，但收到空值") from None
                
        elif field_type in BITABLE_ATTACHMENT_FIELD_TYPES:
            # 附件字段，支持自动上传文件路径或使用现有的 file_token
            # 飞书API需要的格式: [{"file_token": "xxx"}, {"file_token": "yyy"}]
            if value is not None:
                if isinstance(value, str):
                    if os.path.exists(value):
                        # 文件路径存在，尝试上传
                        if config is None:
                            raise ValueError(f"字段 '{field_name}' 需要上传文件但缺少飞书配置")
                        try:
                            file_token = upload_file_to_lark(config, value)
                            result[field_name] = [{"file_token": file_token}]
                        except Exception as exc:
                            raise ValueError(f"字段 '{field_name}' 文件上传失败: {exc}") from exc
                    else:
                        raise ValueError(f"字段 '{field_name}' 的文件路径不存在: {value}")
                elif isinstance(value, (list, tuple)):
                    # 处理多个项目
                    attachment_list = []
                    for item in value:
                        if isinstance(item, str):
                            if os.path.exists(item):
                                # 文件路径存在，上传
                                if config is None:
                                    raise ValueError(f"字段 '{field_name}' 需要上传文件但缺少飞书配置")
                                try:
                                    file_token = upload_file_to_lark(config, item)
                                    attachment_list.append({"file_token": file_token})
                                except Exception as exc:
                                    raise ValueError(f"字段 '{field_name}' 文件上传失败: {exc}") from exc
                            else:
                                raise ValueError(f"字段 '{field_name}' 的文件路径不存在: {item}")
                        else:
                            # 无效的附件格式
                            raise ValueError(f"字段 '{field_name}' 的附件项期望文件路径，但收到: {item!r}")
                    result[field_name] = attachment_list
                else:
                    # 无效的附件格式
                    raise ValueError(f"字段 '{field_name}' 期望文件路径或其列表，但收到: {value!r}")
            else:
                # 空值，设置为空列表
                result[field_name] = []
            
        else:
            # 未知字段类型
            raise ValueError(f"字段 '{field_name}' 的类型为 {field_type}, sync_utils.py 中暂无相关处理逻辑")
            
    return result


def sync_row_to_lark(
    row: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    logger: Callable[[str], None],
) -> bool:
    try:
        field_types = list_field_names(config)  # 获取目标表格中的字段信息
    except LarkBitableSdkNotInstalled as exc:
        logger(f"飞书同步失败: {exc}")
        return False
    except LarkBitableError as exc:
        logger(f"飞书字段获取失败: {exc}")
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger(f"飞书字段获取失败: {exc}")
        return False

    missing_fields = [key for key in row if key not in field_types]
    if missing_fields:
        logger("飞书同步被跳过: 未找到字段 " + ", ".join(missing_fields))
        return False

    try:
        fields = normalize_lark_fields(row, field_types, config)    # 将 row 转换为飞书字段格式
    except ValueError as exc:
        logger(f"飞书同步失败: {exc}")
        return False

    try:
        record_ids = sync_record(config, fields)    # 同步记录到飞书
    except LarkBitableSdkNotInstalled as exc:
        logger(f"飞书同步失败: {exc}")
        return False
    except LarkBitableError as exc:
        logger(f"飞书同步失败: {exc}")
        return False
    except Exception as exc:
        logger(f"飞书同步失败: {exc}")
        return False

    if record_ids:
        logger(f"飞书同步成功，记录ID: {', '.join(record_ids)}")
    else:
        logger("飞书同步成功")
    return True


def resolve_lark_config(
    overrides: Optional[Union[Dict[str, str], str, Mapping[str, str]]],
    *,
    existing: Optional[Dict[str, str]] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, str]]:
    """
    解析和验证飞书配置
    Args:
        overrides: 本次调用提供的飞书配置覆盖
        existing: 现有的飞书配置
        logger: 日志记录函数
    Returns:
        解析和验证后的飞书配置，如果配置不完整则返回 None
    """
    base_config: Dict[str, str] = {}
    if existing:
        base_config.update(existing)
    overrides_config = coerce_lark_config_input(overrides)
    if overrides_config:
        base_config.update(overrides_config)
    expanded_config = expand_lark_config(base_config)
    base_config = expanded_config or {}

    if not base_config:
        logger("飞书同步被跳过: 未提供 Lark 配置")
        return None

    required_keys = ["app_id", "app_secret", "app_token", "table_id"]
    missing = [key for key in required_keys if not base_config.get(key)]
    if missing:
        logger("飞书同步被跳过: 缺少必要配置 " + ", ".join(missing))
        return None

    return base_config


__all__ = [
    "LOCAL_TZ",
    "BITABLE_DATE_FIELD_TYPES",
    "BITABLE_TEXT_FIELD_TYPES",
    "BITABLE_NUMBER_FIELD_TYPES",
    "coerce_lark_config_input",
    "expand_lark_config",
    "normalize_lark_fields",
    "resolve_lark_config",
    "sync_row_to_lark",
]
