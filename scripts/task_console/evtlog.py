"""TaskScheduler 运行日志的读法:EvtQuery + 渲染 XML(pywin32),这是唯一的读取器。

比 Get-WinEvent 快三个数量级:慢的是每条事件都去解析一次 provider 元数据。
读不到时返回 enabled=False 并说明原因,不装作成功:一条空的运行日志和一段读不到的
运行日志在界面上不能长得一样。
"""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET

# TaskScheduler 的 Operational 日志。102 是任务级判决,201 是动作级返回码,两者可以不一致。
CHANNEL = "Microsoft-Windows-TaskScheduler/Operational"
WANTED = (100, 102, 111, 201, 203, 329)
# 计数事件 → 汇总字段。201(动作返回码)不计数,按 rc 归一后另收。
EVENT_FIELDS = {100: "starts", 102: "done", 111: "killed", 203: "failStart", 329: "timedOut"}
ACTION_RC = 201

_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def _is_no_more(e) -> bool:
    """EvtNext 读完时抛 ERROR_NO_MORE_ITEMS(259),那是正常结束不是失败。
    有些 pywin32 版本只有消息文本,所以按词边界匹配 259(`12590` 不算)。"""
    if getattr(e, "winerror", None) == 259:
        return True
    return re.search(r"(?<!\d)259(?!\d)", str(e)) is not None


def channel_enabled() -> tuple[bool | None, str | None]:
    """通道配置里的 IsEnabled。None 表示问不出来(和「关着」不是一回事)。

    关着的通道只要文件还在,EvtQuery 照样打得开并返回零条,所以要单独问。
    """
    try:
        import win32evtlog
        cfg = win32evtlog.EvtOpenChannelConfig(CHANNEL)
        val = win32evtlog.EvtGetChannelConfigProperty(
            cfg, win32evtlog.EvtChannelConfigEnabled)
        # 这个 API 在不同 pywin32 版本上要么直接给 bool,要么给 (值, 类型) 元组。
        if isinstance(val, tuple):
            val = val[0]
        return bool(val), None
    except Exception as e:
        return None, f"读不到通道配置: {e.__class__.__name__}"


def log_stats() -> tuple[int | None, int | None]:
    """(通道里最老的记录号, 通道里的记录总数)。问不出来就是 (None, None)。

    第一个数是**日志被清空的探测器**:EventRecordID 在日志被清空后从 1 重新开始,
    不看它的话,重新开始的记录号会长得像已经摄入过的旧记录号,新事件被去重逻辑整批丢掉,
    而两边的计数都还是「正常」的。摄入器把它存进 runlog_ingest,下一轮拿来比。
    """
    try:
        import win32evtlog
        h = win32evtlog.EvtOpenLog(CHANNEL, win32evtlog.EvtOpenChannelPath)
        out = []
        for prop in (win32evtlog.EvtLogOldestRecordNumber,
                     win32evtlog.EvtLogNumberOfLogRecords):
            v = win32evtlog.EvtGetLogInfo(h, prop)
            # 这个 API 返回 (值, 类型) 元组。
            out.append(int(v[0] if isinstance(v, tuple) else v))
        return out[0], out[1]
    except Exception:
        return None, None


def _xpath(ids, since: _dt.datetime | None) -> str:
    cond = " or ".join(f"EventID={i}" for i in ids)
    if since is None:
        return f"*[System[({cond})]]"
    # 事件日志的 XPath 只接受 UTC 的 ISO 时间。传本地时间会静默少取或多取一段。
    stamp = since.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return f"*[System[({cond}) and TimeCreated[@SystemTime>='{stamp}']]]"


def _parse(xml: str) -> dict | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    sysel = root.find(f"{_NS}System")
    if sysel is None:
        return None

    def txt(tag):
        el = sysel.find(f"{_NS}{tag}")
        return el.text if el is not None else None

    tc = sysel.find(f"{_NS}TimeCreated")
    stamp = tc.get("SystemTime") if tc is not None else None
    name = rc = None
    data = root.find(f"{_NS}EventData")
    if data is not None:
        for d in data.findall(f"{_NS}Data"):
            n = d.get("Name")
            if n == "TaskName":
                name = d.text
            elif n in ("ResultCode", "ReturnCode"):
                rc = d.text
    if not name or not stamp:
        return None
    try:
        # 事件里是 UTC,面板上其余时间都是本地时间,不转会让时间轴整体偏移。
        t = _dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    try:
        eid = int(txt("EventID") or 0)
        rid = int(txt("EventRecordID") or 0)
    except (TypeError, ValueError):
        return None
    return {"task": name.lstrip("\\"), "id": eid, "rid": rid,
            "t": t.strftime("%Y-%m-%d %H:%M:%S"), "rc": rc}


def read(days: int, max_events: int) -> dict:
    import win32evtlog

    since = _dt.datetime.now().astimezone() - _dt.timedelta(days=days)

    on, why = channel_enabled()
    if on is False:
        return {"enabled": False,
                "reason": "任务运行历史日志是关闭的。开启它是开始记录,不是恢复记录。",
                "events": []}
    # 问不出来不能当成开着。
    if on is None:
        return {"enabled": False, "reason": why or "读不到通道配置", "events": []}
    try:
        q = win32evtlog.EvtQuery(
            CHANNEL, win32evtlog.EvtQueryReverseDirection | win32evtlog.EvtQueryChannelPath,
            _xpath(WANTED, since))
    except Exception as e:
        # 日志不存在、没开启、或没权限。三种都不是「没有事件」。
        return {"enabled": False, "reason": f"打不开事件日志: {e.__class__.__name__}",
                "events": []}

    rows, dropped, broke = [], 0, None
    while len(rows) < max_events:
        try:
            batch = win32evtlog.EvtNext(q, 100)
        except Exception as e:
            # 读到一半失败要报出来,不能和读完了混成同一个 break。
            if not _is_no_more(e):
                broke = e.__class__.__name__
            break
        if not batch:
            break
        for ev in batch:
            try:
                xml = win32evtlog.EvtRender(ev, win32evtlog.EvtRenderEventXml)
            except Exception:
                dropped += 1
                continue
            row = _parse(xml)
            if row is None:
                dropped += 1
                continue
            rows.append(row)
            if len(rows) >= max_events:
                break

    oldest_rid, record_count = log_stats()
    return {
        "enabled": True,
        "reason": (f"事件读到一半失败({broke}),下面的条数是不完整的" if broke else None),
        "partial": bool(broke),
        "since": since.strftime("%Y-%m-%d"),
        "oldest": rows[-1]["t"] if rows else None,
        "count": len(rows),
        # 解析不了的事件要报出来。悄悄丢掉会让「这台机器没记录」和「我读不懂它记的东西」
        # 变成同一个空列表。
        "dropped": dropped,
        "truncated": len(rows) >= max_events,
        # 下面三个只有摄入器用:去重的水位线、日志被清空的探测器、通道里的记录总数。
        "maxRecordId": max((r["rid"] for r in rows if r.get("rid")), default=0),
        "oldestRecordId": oldest_rid,
        "recordCount": record_count,
        "events": rows,
    }
