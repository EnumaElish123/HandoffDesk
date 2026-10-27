"""Explicit, read-only project bindings and path containment checks."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PureWindowsPath


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self, path, *, roots_override=None):
        self.path = Path(path)
        try:
            data = self.path.read_bytes()
            if len(data) > 2 * 1024 * 1024:
                raise RegistryError("项目登记文件超过 2 MiB")
            self.config = json.loads(data.decode("utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RegistryError(f"无法读取项目登记：{exc}") from exc
        if not isinstance(self.config, dict) or self.config.get("schema_version") != 1:
            raise RegistryError("项目登记 schema_version 必须为 1")
        self.projects = self.config.get("projects")
        if not isinstance(self.projects, list) or not self.projects:
            raise RegistryError("项目登记必须包含 projects 列表")
        self.defaults = self.config.get("defaults", {})
        override_path = self.path.with_name('roots.local.json')
        override_bytes = b''
        if roots_override is None and override_path.exists():
            try:
                override_bytes = override_path.read_bytes()
                if len(override_bytes) > 262144:
                    raise RegistryError('本机目录配置不能超过 256 KiB。')
                roots_override = json.loads(override_bytes.decode('utf-8-sig'))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RegistryError(f'无法读取本机目录配置 roots.local.json：{exc}') from exc
        elif roots_override is not None:
            override_bytes = json.dumps(roots_override, ensure_ascii=False, sort_keys=True).encode('utf-8')
            if len(override_bytes) > 262144:
                raise RegistryError('本机目录配置不能超过 256 KiB。')
        if roots_override is not None:
            if (not isinstance(roots_override, dict) or set(roots_override) != {'schema_version', 'projects'}
                    or type(roots_override.get('schema_version')) is not int or roots_override['schema_version'] != 1
                    or not isinstance(roots_override.get('projects'), dict)):
                raise RegistryError('本机目录配置格式应为 schema_version: 1 和 projects 对象。')
            project_map = {project['id']: project for project in self.projects
                           if isinstance(project, dict) and isinstance(project.get('id'), str)}
            for project_id, roots in roots_override['projects'].items():
                if project_id not in project_map or not isinstance(roots, dict) or not roots:
                    raise RegistryError(f'本机目录配置含未登记项目或空目录项：{project_id}')
                known = project_map[project_id].get('roots', {})
                if not isinstance(known, dict) or any(key not in known for key in roots):
                    raise RegistryError(f'本机目录配置含未登记的根目录名称：{project_id}')
                for key, value in roots.items():
                    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value) or not Path(value).is_absolute():
                        raise RegistryError(f'{project_id}.{key} 必须是本机绝对目录。')
                    if os.name != 'nt' and (PureWindowsPath(value).drive or value.startswith('\\\\')):
                        raise RegistryError(f'{project_id}.{key} 需要 Ubuntu 本机目录，不能使用 Windows 盘符。')
                known.update(roots)
        self.roots_override = roots_override
        self.fingerprint = hashlib.sha256(data + (b'\0roots.local\0' + override_bytes if override_bytes else b'')).hexdigest()
        ids = set()
        for project in self.projects:
            if not isinstance(project, dict):
                raise RegistryError("项目登记项必须为对象")
            project_id = project.get("id", "")
            if not isinstance(project_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", project_id):
                raise RegistryError("项目 ID 只能含字母、数字、下划线和连字符")
            if project_id in ids:
                raise RegistryError(f"项目 ID 重复：{project_id}")
            ids.add(project_id)
            if not isinstance(project.get("name"), str):
                raise RegistryError(f"{project_id} 缺少项目名称")
            roots = project.get("roots")
            if not isinstance(roots, dict) or not roots:
                raise RegistryError(f"{project_id} 缺少项目根目录")
            for root in roots.values():
                if not isinstance(root, str) or not Path(root).is_absolute():
                    raise RegistryError(f"{project_id} 的根目录不适用于当前系统。请先通过 configure-roots 配置本机目录（见 docs/ubuntu.md），或设置 config/roots.local.json。")
            source_ids = set()
            for source in project.get("sources", []):
                source_id = source.get("id")
                if not isinstance(source_id, str) or not source_id or source_id in source_ids:
                    raise RegistryError(f"{project_id} 来源 ID 为空或重复")
                source_ids.add(source_id)
                self.source_path(project, source)
                fields = source.get("fields", {})
                if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
                    raise RegistryError(f"{source_id} 字段映射必须为字符串字典")
            entry_ids = [entry.get("id") for entry in project.get("entries", [])]
            if any(not isinstance(e, str) or not e for e in entry_ids) or len(set(entry_ids)) != len(entry_ids):
                raise RegistryError(f"{project_id} 材料入口 ID 为空或重复")

    def get(self, project_id):
        key = str(project_id).strip().casefold()
        matches = [p for p in self.projects if key in [str(x).casefold() for x in [p["id"], p["name"], *p.get("aliases", [])]]]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RegistryError(f"项目名称或别名有歧义：{project_id}")
        raise RegistryError(f"未登记项目：{project_id}")

    def resolve(self, project, root_key, relative):
        if isinstance(project, str):
            project = self.get(project)
        try:
            root = Path(project["roots"][root_key]).resolve()
        except (KeyError, OSError, RuntimeError) as exc:
            raise RegistryError(f"未登记或无法解析的根目录：{root_key}") from exc
        if not isinstance(relative, (str, os.PathLike)):
            raise RegistryError("材料路径必须是相对路径")
        raw = str(relative)
        win = PureWindowsPath(raw)
        if not raw or "\x00" in raw or Path(raw).is_absolute() or win.is_absolute() or win.drive or win.root:
            raise RegistryError("不接受绝对路径或带驱动器的材料路径")
        # Normalise Windows separators even when unit tests run on another OS.
        parts = raw.replace("\\", "/").split("/")
        if any(part == ".." or ":" in part for part in parts):
            raise RegistryError("材料路径不允许上级目录跳转或数据流")
        try:
            resolved = root.joinpath(*parts).resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RegistryError("材料路径解析后位于登记根目录之外") from exc
        return resolved

    def source_path(self, project, source):
        return self.resolve(project, source.get("root", "main"), source.get("path", ""))
