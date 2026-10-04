"""Shared display helpers: four states, time and number formats. Synthetic values only."""
import re

from test_operations_ui import run

NOW = "const NOW=Date.UTC(2026,5,15,12,0,0);"


def test_count_cell_renders_five_distinct_states():
    cells = run("[countCell('loading'),countCell('unchecked'),countCell('broken',null,new Error('acme_reader_failed')),"
                "countCell('ok',0),countCell('ok',279921)]")
    assert len(set(cells)) == 5
    loading, unchecked, broken, zero, value = cells
    assert ">…<" in loading and "count-loading" in loading
    assert ">—<" in unchecked and 'title="未检查"' in unchecked and "count-unchecked" in unchecked
    assert ">!<" in broken and "count-broken" in broken and 'title="读取失败：acme_reader_failed"' in broken
    assert ">0<" in zero and "count-zero" in zero
    assert ">279,921<" in value
    # 没有值不是零:缺失的数字画成未检查,不画成 0。
    assert run("countCell('ok',null)") == unchecked


def test_error_block_keeps_the_raw_code_out_of_the_visible_text():
    html = run("errorBlock('工作记录',new Error('work_reader_failed'),'data-reload=\"work\"')")
    visible = re.sub(r"<[^>]*>", "", html)
    assert "工作记录读取失败" in visible and "work_reader_failed" not in visible
    assert re.search(r'title="[^"]*work_reader_failed[^"]*"', html)
    assert 'role="alert"' in html and "重试" in visible and 'data-reload="work"' in html
    # 「说明 (代号)」:说明留在正文,括号里的代号只进悬停。
    mixed = run("errorBlock('同步任务',new Error('磁盘没有挂上 (disk_missing)'))")
    assert "磁盘没有挂上" in re.sub(r"<[^>]*>", "", mixed)
    assert "disk_missing" not in re.sub(r"<[^>]*>", "", mixed) and "disk_missing" in mixed


def test_loading_and_empty_blocks_are_distinct_from_each_other_and_from_warnings():
    loading, empty, filtered = run("[loadingBlock('工作记录'),emptyBlock('没有记录'),emptyBlock('没有符合筛选条件的记录',{filtered:'work'})]")
    assert "warn" not in loading and "正在读取工作记录…" in loading and 'role="status"' in loading
    assert "清除筛选" not in empty and "state-filtered" not in empty
    assert 'data-reset-filters="work"' in filtered and "清除筛选" in filtered and "state-filtered" in filtered
    assert run("matchCount(43,2395,'个会话')") == "显示 43 / 共 2,395 个会话"


def test_time_and_number_formats():
    result = run(NOW + """[fmtTime(NOW-30000,{now:NOW}),fmtTime(NOW-3*3600000,{now:NOW}),fmtTime(NOW+27*60000,{now:NOW}),
      fmtTime(NOW-9*86400000,{now:NOW}),fmtTime(NOW+20000,{now:NOW,soon:'即将运行'}),fmtTime(NOW+20*3600000,{now:NOW}),
      fmtTime(NOW-426*3600000,{now:NOW}),fmtTime(null),fmtNum(279921),fmtNum(null),kb(8.9e9),kb(8482*1048576),
      fmtTime(Date.now()-30000),fmtTime(Date.now()-3*3600000),fmtTime(Date.now()+27*60000),fmtTime(Date.now()-9*86400000)]""")
    assert result[:7] == ["刚刚", "3 小时前", "27 分钟后", "9 天前", "即将运行", "20 小时后", "17 天前"]
    assert result[7] == "时间未知"
    assert result[8] == "279,921" and result[9] == "—"
    assert result[10].endswith("G") and result[11] == "8.3G"
    assert result[12:] == ["刚刚", "3 小时前", "27 分钟后", "9 天前"]


def test_absolute_time_and_full_title():
    result = run("""const local=new Date(2026,5,15,12,0,0).getTime();
      [fmtTime(new Date(2026,2,4,9,5,7).getTime(),{now:local}),fmtTime(new Date(2025,11,31,23,59,0).getTime(),{now:local}),
       fmtTime(new Date(2026,5,15,9,5,0).getTime(),{now:local,relative:false}),fullTime(new Date(2026,2,4,9,5,7).getTime()),
       fullTime(Math.floor(new Date(2026,2,4,9,5,7).getTime()/1000)),timeTag(new Date(2026,2,4,9,5,7).getTime(),{now:local})]""")
    assert result[0] == "03-04 09:05"
    assert result[1] == "2025-12-31 23:59"
    assert result[2] == "06-15 09:05"
    assert result[3] == result[4] == "2026-03-04 09:05:07"
    assert 'title="2026-03-04 09:05:07"' in result[5] and ">03-04 09:05<" in result[5]
