"""Small display helpers. Labels describe records, never live process state."""
from datetime import datetime, timezone
import json
import re
from pathlib import PureWindowsPath

ORIGINS = {"user_directed": "用户填写", "assistant_suggested": "助手建议", "plan_derived": "计划引用", "agent_recorded": "Agent 记录"}


def record_attribution(record, detailed=False):
    """Attribute reported checks to their writer, without claiming a new audit."""
    if not isinstance(record, dict) or record.get('origin') != 'agent_recorded':
        return ''
    actor = record.get('actor') if isinstance(record.get('actor'), dict) else {}
    verification = record.get('verification') if isinstance(record.get('verification'), dict) else {}
    status = {'passed': '通过', 'partial': '部分验证', 'not_run': '未验证',
              'failed': '失败'}.get(verification.get('status'), '未提供')
    label = 'Agent 记录 · ' + str(actor.get('name') or '未标明作者') + ' · 报告验证：' + status
    if not detailed:
        return label
    lines = [label]
    if actor.get('session_id'):
        lines.append('来源会话：' + str(actor['session_id']))
    if verification.get('summary'):
        lines.append('验证说明（由记录者报告）：' + str(verification['summary']))
    return '\n'.join(lines)
SOURCE_LABELS = {
    "rvc.previous_state": "前序训练状态",
    "rvc.followup_state": "后续训练状态", "rvc.followup_result": "后续训练结果",
    "rvc.restore_record": "续训恢复记录", "rvc.server_record": "本地服务登记",
    "rvc.selected_job": "关注任务配置", "rvc.selected_state": "关注任务状态",
    "rvc.selected_result": "关注任务结果", "rvc.progress": "工作流阶段记录",
}
FIELD_LABELS = {
    "status": "记录状态", "recorded_status": "保存的任务状态", "result_status": "结果状态",
    "execution_status": "执行记录", "task": "任务", "slice": "范围",
    "decision_status": "提案状态", "accepted_option": "已选方案", "patch_applied": "规范补丁已应用",
    "all_required_work_packages_completed": "必需工作包已完成", "all_required_complete": "必需工作包已完成",
    "remaining_required_work_packages": "剩余必需工作包", "remaining_required": "剩余必需工作包",
    "required_scheduled_conditions": "预定条件数", "complete_scheduled_conditions": "已完成条件数",
    "required_conditions": "预定条件数", "complete_conditions": "已完成条件数",
    "unresolved_conditions": "未解决条件数", "primary_report": "主要报告", "raw_observations": "原始观察数",
    "restored_epoch": "恢复时的轮次", "restored_step": "恢复时的步数", "restored_global_step": "恢复时的步数",
    "optimizer_restored": "优化器恢复记录", "optimizer_restored_by_trainer": "训练器确认恢复优化器",
    "resumed_by": "后续接续任务", "resumed_from": "前序任务", "outputs": "保存的产物入口",
    "source_recorded_at": "来源记录时间", "updated_at": "来源更新时间", "updated_utc": "来源更新时间",
    "created_at": "创建时间", "finished_at": "完成记录时间", "confirmed_at": "确认记录时间",
    "kind": "任务类型", "stage": "工作流阶段", "percent": "工作流粗进度（%）", "message": "来源说明",
    "url": "登记的网址", "saved_url": "登记的网址", "saved_pid": "历史进程编号", "pid": "历史进程编号",
    "head": "提交版本", "branch": "分支", "tracked_changes": "已跟踪文件变化", "untracked": "未跟踪文件",
    "size": "文件大小（字节）", "size_bytes": "文件大小（字节）", "text": "读取的文本", "tail": "日志尾部",
    "path": "文件路径", "mtime": "文件修改时间", "exit_code": "保存的退出码",
    "required_completed": "必需工作包已完成", "completed_conditions": "已完成条件数",
    "unresolved": "未解决条件数", "untracked_entries": "未跟踪入口", "excerpt": "内容节选",
    "truncated": "节选已截断", "validation": "验证范围",
}
VALUES = {
    "completed": "已完成（保存记录）", "complete": "已完成（保存记录）", "paused": "已暂停（保存记录）",
    "running": "当时运行中（当前未检查）", "queued": "当时等待中（当前未检查）",
    "pending": "待处理（保存记录）", "failed": "失败（保存记录）", "cancelled": "已取消（保存记录）",
    "PROPOSED": "已提出，待决定", "ACCEPTED": "已接受", "REJECTED": "已拒绝",
    "COMPLETE_REQUIRED_SCHEDULE": "预定必需条件已完成", "FROZEN_BEFORE_CONFIRMATION": "确认实验前协议已冻结",
    "complete_initial_exploratory_protocol": "首轮探索协议已完成", "train": "训练", "training": "训练",
    "convert": "换声", "conversion": "换声", "infer": "换声", "inference": "换声",
    "preprocess": "预处理", "extract": "特征提取", "prepare": "素材筛选",
}
CHANGE_LABELS = {"changed": "来源变化", "added": "新增来源", "removed": "来源移除", "unchanged": "未变化",
                 "unavailable": "无法比较", "error": "读取失败", "first_observed": "首次读取", "new": "首次读取",
                 "recovered": "已恢复读取", "missing": "文件缺失"}


def human_time(value, now=None):
    if not value:
        return "尚无时间记录"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone()
        current = now or datetime.now().astimezone()
        days = (current.date() - parsed.date()).days
        day = "今天" if days == 0 else "昨天" if days == 1 else parsed.strftime("%m 月 %d 日") if parsed.year == current.year else parsed.strftime("%Y 年 %m 月 %d 日")
        return f"{day} {parsed:%H:%M}"
    except (ValueError, TypeError):
        return str(value)


def source_label(source_id, explicit=None):
    if str(source_id).startswith("rvc.chain."):
        return "接续任务结果" if str(source_id).endswith(".result") else "接续任务状态"
    return SOURCE_LABELS.get(source_id) or explicit or str(source_id or "未命名来源")


def relative_time(value, now=None):
    if not value:
        return "尚未读取"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        seconds = max(0, (current - parsed).total_seconds())
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{int(seconds // 60)} 分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时前"
        return f"{int(seconds // 86400)} 天前 · {human_time(value)}"
    except (ValueError, TypeError):
        return human_time(value)


def short_material(entry, limit=30):
    """Use a readable role for machine-generated outputs; retain full path elsewhere."""
    label = str(entry.get("label") or entry.get("id") or "材料")
    path = PureWindowsPath(str(entry.get("target") or ""))
    suffix = path.suffix.lower()
    roles = {".pth": "模型权重", ".pt": "模型权重", ".index": "音色检索索引"}
    if suffix in roles:
        label = roles[suffix]
        epochs = re.search(r"(?:^|_)(\d+)e(?:_|\.)", path.name)
        if epochs:
            label += " · " + epochs.group(1) + " 轮"
    elif path.name == "voice_bundle.zip":
        label = "音色打包文件"
    elif path.name == "voice.json":
        label = "音色信息"
    elif re.match(r"^(?:wf_|\d{8}_)", label):
        label = "任务产物 · " + (suffix.lstrip(".").upper() or "文件夹")
    label = re.sub(r"^(?:打开|定位)\s*", "", label) or "材料"
    return label if len(label) <= limit else label[:limit-1] + "…"


def short_workline(value, limit=16):
    """Keep the work subject and type visible; do not show a partial date."""
    label = str(value or "选择工作线")
    parts = re.split(r"\s*·\s*", label)
    if len(parts) > 2 and re.match(r"\d{4}(?:-|/|年)", parts[2]):
        label = " · ".join(parts[:2]).rstrip()
        return label[:limit-1].rstrip() + "…"
    return label if len(label) <= limit else label[:limit-1].rstrip() + "…"


def rvc_summary(snapshot):
    observations = {item.get("source_id"): item for item in snapshot.get("observations", [])}
    selected = observations.get("rvc.selected_state") or observations.get("rvc.previous_state") or {}
    value = selected.get("value") or {}
    if not isinstance(value, dict):
        return ""
    status = value.get("recorded_status")
    result = "历史任务：" + display_value(status) if status else ""
    if value.get("resumed_by"):
        successors = [item for key, item in observations.items() if str(key).startswith("rvc.chain.") or key == "rvc.followup_state"]
        completed = any(isinstance(item.get("value"), dict) and (item["value"].get("recorded_status") == "completed" or item["value"].get("result_status") == "completed") for item in successors)
        result += " → 已有后续任务接续" + ("，后续记录已完成" if completed else "")
    if result:
        result += "；当前运行状态未检查。"
    return result


def field_label(field):
    return FIELD_LABELS.get(field, str(field))


def display_value(value, limit=3000):
    if value == {"missing": True}:
        return "未提供此字段"
    if value is None:
        return "明确为空"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, (list, dict)):
        result = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        result = VALUES.get(str(value), str(value))
    return result if len(result) <= limit else result[:limit] + "\n…（完整数据见原始记录）"


def observation_text(observation):
    lines = [source_label(observation.get("source_id"), observation.get("label")), ""]
    if observation.get("status") == "error":
        lines += ["本次读取失败：" + str(observation.get("error") or "未提供原因"), ""]
    lines += ["本次读取：" + human_time(observation.get("observed_at")),
              "来源记录：" + human_time(observation.get("source_recorded_at")),
              "文件修改：" + human_time(observation.get("source_mtime")),
              "位置：" + str(observation.get("source_ref") or "未提供"), ""]
    previous = observation.get("last_success")
    if observation.get("status") == "error" and isinstance(previous, dict):
        lines += ["保留的历史字段", "上次成功读取：" + str(previous.get("observed_at") or "未提供") + "（" + human_time(previous.get("observed_at")) + "）"]
        values = previous.get("value") or {}
    else:
        values = observation.get("value") or {}
    if isinstance(values, dict):
        lines += [field_label(key) + "：" + display_value(value) for key, value in values.items()]
    else:
        lines += [display_value(values)]
    return "\n".join(lines)
