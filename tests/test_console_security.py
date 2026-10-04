#!/usr/bin/env python3
"""task_console 的四道安全控制,现在有了会失败的证据。

这四条以前只有一次人工实测的记录。一个能改系统状态的 HTTP 端点,它的防线不该只存在于
某人的记忆里:所以每条控制都配一条**正对照**(正常请求必须通过),否则一个把所有请求
都拒掉的服务器也能让「拒绝」那半边全绿。

Host 白名单是新加的。绑 127.0.0.1 挡得住网段,挡不住 DNS rebinding:token 是被替换进
'/' 那张页面的,任何能同源请求 '/' 的东西直接把 token 读走。rebinding 之后 Host 头带的是
攻击者的域名,和三个回环字面量对不上,这就是它被挡住的地方。
"""
import http.client
import json
import os
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts", "task_console"))

if os.name != "nt":
    pytest.skip("task_console 只在 Windows 上有意义", allow_module_level=True)

import server as S  # noqa: E402

TOKEN = "test-token-not-a-real-one"

# ⚠ 这一行必须跑在任何 fixture 之前:它拍下 Handler **出厂时**的 token 默认值。
# 下面那条用例要钉的正是「出厂默认值不能通过鉴权」,而如果用例自己去 setattr 一个哨兵,
# 它测的就变成了「我刚设的那个值不能通过」, 把默认值改回 "" 照样绿。
# (投毒时实测过:第一版就是那个形状,四条投毒里唯独这条没红。)
DEFAULT_TOKEN = S.Handler.token


@pytest.fixture(scope="module")
def srv():
    S.Handler.token = TOKEN
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    port = httpd.server_address[1]
    S.Handler.allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()


def call(port, method, path, host=None, token=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host if host is not None else f"127.0.0.1:{port}"}
    if token is not None:
        headers["X-Console-Token"] = token
    payload = json.dumps(body).encode() if body is not None else None
    if payload:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    c.request(method, path, body=payload, headers=headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


# ---------- 令牌 ----------

def test_api_without_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/hours?from=2026-01-01&to=2026-01-02")
    assert st == 403


def test_api_with_wrong_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/hours?from=2026-01-01&to=2026-01-02", token="wrong")
    assert st == 403


def test_correct_token_is_not_403(srv):
    # 正对照。没有它,一个把每个请求都拒掉的服务器会让上面两条全绿。
    st, _ = call(srv, "GET", "/api/hours?from=2026-01-01&to=2026-01-02", token=TOKEN)
    assert st != 403


# ---------- 动词白名单 ----------

def test_unknown_verb_is_400(srv):
    st, body = call(srv, "POST", "/api/act", token=TOKEN,
                    body={"verb": "delete", "name": "Whatever"})
    assert st == 400
    assert b"verb" in body


def test_missing_task_name_is_400(srv):
    st, _ = call(srv, "POST", "/api/act", token=TOKEN, body={"verb": "run", "name": ""})
    assert st == 400


def test_act_without_token_is_403_before_any_verb_check(srv):
    # 顺序很重要:一个先校验动词再校验令牌的端点,会把「这个动词存在吗」告诉未鉴权的调用方。
    st, _ = call(srv, "POST", "/api/act", body={"verb": "delete", "name": "X"})
    assert st == 403


def test_allowed_verbs_are_exactly_four():
    # 这条钉的是集合本身。加动词是一个需要有人明确改这行的动作,不是顺手就能滑进去的。
    assert set(S.VERBS) == {"enable", "disable", "run", "stop"}


# ---------- Host 白名单(新加) ----------

def test_rebinding_host_is_rejected(srv):
    st, body = call(srv, "GET", "/", host="evil.example.com")
    assert st == 400
    assert b"host" in body.lower()


def test_missing_host_is_rejected(srv):
    st, _ = call(srv, "GET", "/", host="")
    assert st == 400


def test_loopback_hosts_are_accepted(srv):
    # 正对照。三个回环写法浏览器都可能发,一个把它们也挡掉的白名单等于把控制台锁死。
    for h in (f"127.0.0.1:{srv}", f"localhost:{srv}"):
        st, _ = call(srv, "GET", "/", host=h)
        assert st == 200, h


def test_host_check_also_guards_the_page_that_carries_the_token(srv):
    # 这才是这道闸真正防的东西:token 在 '/' 的 HTML 里,所以 '/' 必须和 /api/ 一样受保护。
    st, body = call(srv, "GET", "/", host="attacker.test")
    assert st == 400
    assert TOKEN.encode() not in body


def test_star_is_a_separate_branch_not_a_hostname():
    # '*' 不能是名单里的一项,否则任何能写进名单的名字都可能把整道闸关掉。
    h = S.Handler
    saved = h.allowed_hosts
    try:
        h.allowed_hosts = {"*"}
        inst = object.__new__(h)
        inst.headers = {"Host": "evil.example.com"}
        assert h._host_ok(inst) is False
    finally:
        h.allowed_hosts = saved


def test_default_allowed_hosts_is_fail_closed():
    """忘了设置和明确允许一切必须是两件事。

    断言的是**源码里声明的默认值**而不是运行时的类属性:上面那个 fixture 会把类属性改掉,
    读运行时值的断言只能证明「某个测试设过它」,证明不了「没人设的时候是什么」。
    """
    src = open(os.path.join(HERE, "scripts", "task_console", "server.py"),
                  encoding="utf-8").read()
    assert "allowed_hosts: object = frozenset()" in src


# ---------- 维护端点(新):它自己的表,不蹭任务那张 ----------

def test_maint_read_without_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/maint")
    assert st == 403


def test_maint_act_without_token_is_403(srv):
    st, _ = call(srv, "POST", "/api/maint/act",
                 body={"action": "skill.archive", "name": "x"})
    assert st == 403


def test_maint_act_rejects_a_host_it_does_not_know(srv):
    st, _ = call(srv, "POST", "/api/maint/act", host="evil.example.com", token=TOKEN,
                 body={"action": "skill.archive", "name": "x"})
    assert st == 400


def test_maint_act_refuses_an_action_outside_its_table(srv):
    st, body = call(srv, "POST", "/api/maint/act", token=TOKEN,
                    body={"action": "skill.delete", "name": "x"})
    assert st == 400
    assert b"bad_action" in body


def test_task_verbs_are_not_reachable_through_the_maint_endpoint(srv):
    # 两张表必须是两张表。任务动词从维护端点进来必须被拒,否则「分开」只是文档上的说法。
    st, body = call(srv, "POST", "/api/maint/act", token=TOKEN,
                    body={"action": "run", "name": "SomeTask"})
    assert st == 400
    assert b"bad_action" in body


def test_maint_verbs_are_not_reachable_through_the_task_endpoint(srv):
    # 反方向同理。
    st, body = call(srv, "POST", "/api/act", token=TOKEN,
                    body={"verb": "skill.archive", "name": "x"})
    assert st == 400
    assert b"verb" in body


def test_selfcheck_without_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/selfcheck")
    assert st == 403


def test_selfcheck_with_token_answers(srv):
    # 正对照。自检端点自己要是 403 了,页面上那条会显示「自检本身失败」而不是假绿。
    st, body = call(srv, "GET", "/api/selfcheck", token=TOKEN)
    assert st == 200
    assert b'"rows"' in body and b'"probed"' in body


def test_repos_read_without_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/repos")
    assert st == 403


def test_repo_push_is_not_an_action(srv):
    # 动作表里没有 push,从端点也进不去。
    st, body = call(srv, "POST", "/api/maint/act", token=TOKEN,
                    body={"action": "repo.push", "name": "x"})
    assert st == 400
    assert b"bad_action" in body


def test_sys_read_without_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/sys")
    assert st == 403


def test_clean_action_cannot_be_pointed_at_a_path(srv, tmp_path, monkeypatch):
    """删除动作不收路径:喂一条路径进去,它只清自己那批,绝不去动别的地方。

    ⚠ 这条用例原来什么都没断言。它写的是 `assert st in (200, 400)`,再在 400 分支里
    `assert no_config in body or missing_src in body` —— 两条通路全接受,而且这台机器上
    根本没配 TASK_CONSOLE_PLUGIN_CACHE,于是它每次都走 no_config 那条,
    **删除逻辑一次都没有被执行过**。把 clean_temp_git 改成 `shutil.rmtree(name)` 它照样绿。
    一条名字里写着「不能被指到别的路径」的用例,从来没有验证过任何路径。

    现在把环境搭成确定的:缓存目录里放一个够旧、够格被删的暂存目录,**外面**放一个哨兵。
    正对照是「它真的删掉了自己那一个」(否则「什么都没删」也能让哨兵活下来),
    负对照是「哨兵还在」。
    """
    import os
    import time

    cache = tmp_path / "cache"
    cache.mkdir()
    victim = cache / "temp_git_123_abc"          # 名字形状与年龄都够格
    victim.mkdir()
    (victim / "f.txt").write_text("x", encoding="utf-8")
    old = time.time() - 10 * 3600
    os.utime(victim, (old, old))

    sentinel = tmp_path / "DO-NOT-TOUCH"          # 在缓存目录之外
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep", encoding="utf-8")

    monkeypatch.setenv("TASK_CONSOLE_PLUGIN_CACHE", str(cache))
    st, body = call(srv, "POST", "/api/maint/act", token=TOKEN,
                    body={"action": "clean.tempgit", "name": "../DO-NOT-TOUCH"})

    assert st == 200, body[:300]
    # 正对照:它确实动手了。少了这一条,一个什么都不做的实现也能通过下面那条。
    assert not victim.exists(), "自己那批没被清掉,这条用例根本没执行到删除逻辑"
    assert b'"removed": 1' in body.replace(b" ", b"") or b'"removed":1' in body.replace(b" ", b"")
    # 真正要钉的那条:name 里的路径没有被当成删除目标。
    assert sentinel.exists() and (sentinel / "keep.txt").exists(), \
        "name 里的相对路径被当成了删除目标"


def test_retire_plan_without_token_is_403(srv):
    st, _ = call(srv, "POST", "/api/retire/plan", body={"name": "X"})
    assert st == 403


def test_retire_without_a_reason_is_refused(srv):
    """退役必须带原因。

    ⚠ 原来断言的是 `no_reason 或 bad_name 或 no_config`。三选一意味着这条用例
    **分不出「因为没写原因被挡」和「因为任务名不合法被挡」** —— 把 no_reason 那道闸
    整个删掉,它照样绿(名字或配置那两条会接住)。一个能被三种不同原因满足的断言,
    钉不住其中任何一种。
    """
    st, body = call(srv, "POST", "/api/maint/act", token=TOKEN,
                    body={"action": "task.retire", "name": "SomeTask", "arg": ""})
    assert st == 400
    assert b"no_reason" in body, body[:300]


def test_retire_with_a_bad_name_is_refused_for_a_different_reason(srv):
    """负对照:换成不合法的名字,理由必须变成 bad_name。

    有了这一条,上面那条才是在钉「没写原因」而不是在钉「反正会被挡」。
    两条用例的输入只差一个字段,输出必须是两个不同的拒绝理由。
    """
    st, body = call(srv, "POST", "/api/maint/act", token=TOKEN,
                    body={"action": "task.retire", "name": "../evil", "arg": ""})
    assert st == 400
    assert b"bad_name" in body, body[:300]



def test_an_uninitialised_handler_refuses_everything(srv):
    """令牌没被设过时,鉴权必须拒绝,而不是放行。

    ⚠ `Handler.token` 以前的类默认值是 `""`,而 `_authed` 用的是
    `compare_digest(请求头 or "", self.token)` —— 于是一个**根本不带这个头**的请求
    会得到 compare_digest("", "") → True,直接过鉴权。它没被利用只是因为 Host 闸
    恰好先开火;也就是说令牌这道控制在「没初始化」状态下靠的是另一道控制兜底,
    而紧挨着它的 allowed_hosts 有四行注释专门解释自己为什么 fail-closed。
    """
    # ⚠ 这里曾经有一句 `assert not DEFAULT_TOKEN`。它对 "" 恒成立,而 "" 正是当年那个
    # 出问题的默认值, 一句在它要防的那个值上恒真的断言,正是这一轮在清理的那一类。
    # 真正能失败的判据只有行为:**把 Handler 退回出厂状态,它必须拒绝一切**。
    # 两层控制哪一层还在都能让它成立(None 哨兵、或 _authed 里的 `if not self.token`),
    # 而两层同时退回当年那个形态时它会红, 投毒验过。
    saved = S.Handler.token
    try:
        # 把 Handler 退回出厂状态,而不是退回一个我自己挑的哨兵。
        S.Handler.token = DEFAULT_TOKEN
        st, _ = call(srv, "GET", "/api/tasks")            # 不带令牌
        assert st == 403, "未初始化的 Handler 放行了一个不带令牌的请求"
        st, _ = call(srv, "GET", "/api/tasks", token="")   # 带一个空令牌
        assert st == 403, "未初始化的 Handler 放行了一个空令牌"
    finally:
        S.Handler.token = saved
    # 正对照:恢复之后正常的令牌仍然能用,证明上面拒的不是「服务器本来就坏了」。
    # ⚠ 打 /api/selfcheck 而不是 /api/tasks。后者会真的枚举整台机器的计划任务
    # (实测 3 秒,服务端给 90 秒),而这个客户端的 socket 超时只有 10 秒,
    # 机器上任务多一点、或者同时有别的 PowerShell 在跑,这条就红。
    # 实测同一份代码连跑两轮,一轮两条红(各卡满 10 秒)、一轮全过。
    # 这两条守的东西很重要,而它们原来用一个**跟被测控制毫无关系**的真机开销决定成败:
    # 那正是「训练所有人忽略红灯」的那种红, 跟改动无关,复跑一次就好,
    # 于是下一次真的红也会被当成同一回事。
    # 这里要证明的只是「带对令牌能拿到 200」,任何一个要鉴权的端点都够。
    st, _ = call(srv, "GET", "/api/selfcheck", token=TOKEN)
    assert st == 200


def test_convos_read_without_token_is_403(srv):
    st, _ = call(srv, "GET", "/api/convos")
    assert st == 403


# ---------- vendor 静态资产 ----------
# vendor 下是随仓发的第三方资产(Tabler),刻意免令牌:<link> 和 <script> 标签发不了
# 自定义请求头,而这里面没有任何秘密。免令牌就意味着这条路径唯一的控制是「不许爬出
# vendor 目录」,所以它必须被单独测,并且要有一个证明它不是空拦的正对照。

def test_vendor_serves_a_real_asset(srv):
    st, body = call(srv, "GET", "/vendor/tabler/tabler.min.css")
    assert st == 200, st
    assert b"Tabler" in body[:400], body[:120]


def test_vendor_asset_needs_no_token(srv):
    # 正对照:证明上面那条 200 不是因为测试恰好带了令牌。
    st, _ = call(srv, "GET", "/vendor/tabler/tabler.min.css", token=None)
    assert st == 200, st


@pytest.mark.parametrize("path", [
    "/vendor/../server.py",
    "/vendor/tabler/../../server.py",
    "/vendor/%2e%2e/server.py",          # 百分号编码:前缀检查看不出来
    "/vendor/tabler/%2e%2e%2f%2e%2e%2fserver.py",
])
def test_vendor_refuses_to_climb_out(srv, path):
    st, _ = call(srv, "GET", path)
    assert st == 404, f"{path} 爬出去了: {st}"


def test_vendor_traversal_target_actually_exists(srv):
    # 负对照:上面那些 404 必须是被闸挡的,不能是「文件本来就不在」。
    # server.py 就在 vendor 的上两级,真的存在;直接确认一下,
    # 否则那四条断言全都可以在闸门被拆掉之后照样通过。
    import server as S2
    from pathlib import Path
    assert (Path(S2.VENDOR).parent / "server.py").is_file()


def test_vendor_missing_file_is_404(srv):
    st, _ = call(srv, "GET", "/vendor/tabler/nope.css")
    assert st == 404, st

# ---------- vendor:形状拒绝必须发生在碰文件系统之前 ----------
# 第一版这道闸只有「解析之后确认仍在 vendor 里」。它确实拦得住,文件一个字节都没泄。
# 但 "//host/share/x" 会让 Path.resolve() 在 Windows 上按 UNC 去**连那台主机**,
# 而那一步发生在归属检查之前:实测对一个不可路由地址(RFC 5737 的 192.0.2.1)
# 耗时 21.07 秒,对一台可达的攻击者主机则是一次自动 NTLM 协商。
# 免令牌 + 任意主机 + 每请求二十多秒,凑起来是一个不用登录的外联与阻塞原语,
# 而返回码从头到尾都是干净的 404。
#
# 所以下面这组用例分两种:一种断言仍然 404(结果对),
# 另一种断言**耗时**(证明它没有先去连网络)。只测前者的话,这个洞原封不动。

@pytest.mark.parametrize("path", [
    "/vendor///192.0.2.1/share/x",              # 浏览器不会折叠 path 里的连续斜杠
    "/vendor/%2F%2F192.0.2.1%2Fshare%2Fx",      # 百分号编码同形
    "/vendor/" + chr(92) + chr(92) + "192.0.2.1" + chr(92) + "share",   # 反斜杠 UNC
    "/vendor/C:/Windows/win.ini",               # 带盘符的绝对路径
    "/vendor/tabler/../../../server.py",        # 多级穿越
    "/vendor/./tabler/tabler.min.css",          # 单点段
    "/vendor/",                                 # 空 rel
])
def test_vendor_rejects_by_shape(srv, path):
    st, _ = call(srv, "GET", path)
    assert st == 404, f"{path} 没被挡: {st}"


def test_vendor_unc_is_refused_without_touching_the_network(srv):
    """UNC 那条必须**立刻**返回,不能先去连主机。

    这是本条唯一能分辨「修好了」和「没修但恰好也 404」的判据:两种实现的状态码一样,
    只有耗时不一样。192.0.2.1 是 RFC 5737 的 TEST-NET-1,保证不可路由,
    所以未修的实现会在这里卡二十秒以上。
    """
    # 刻意用一个**上面那组用例没碰过**的地址。第一版这里和 test_vendor_rejects_by_shape
    # 共用 192.0.2.1,于是形状用例先跑并阻塞了 21 秒之后,Windows 缓存了那次失败的解析,
    # 轮到这条时秒返,投毒状态下它照样打印绿色 : 一个只在自己是第一个碰那台主机时
    # 才有效的耗时判据,和没有判据差不多。
    t0 = time.monotonic()
    st, _ = call(srv, "GET", "/vendor///192.0.2.77/share/x")
    dt = time.monotonic() - t0
    assert st == 404, st
    assert dt < 2.0, f"返回是 404 但花了 {dt:.1f}s,说明形状检查跑在了 resolve() 之后"


def test_vendor_still_serves_the_real_asset(srv):
    """正对照:上面那一串 404 不能是因为把整条路由拒死了。"""
    st, body = call(srv, "GET", "/vendor/tabler/tabler.min.css")
    assert st == 200, st
    assert b"Tabler" in body[:400]


# ---------- 不许被 iframe 进去 ----------
# Host 白名单防的是 DNS rebinding。iframe 走另一条路:它发的 Host 就是 127.0.0.1:8787,
# 白名单原样放行,页面带着一枚有效令牌正常渲染。攻击者不需要读到任何东西,
# 只要骗一次点击落在他知道位置的按钮上,而这一页上的按钮会真删目录、真跑计划任务。

def _headers(port, path="/"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    r.read()
    h = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return h


def test_the_page_refuses_to_be_framed(srv):
    h = _headers(srv, "/")
    assert "frame-ancestors 'none'" in h.get("content-security-policy", ""), h.get("content-security-policy")
    assert h.get("x-frame-options", "").upper() == "DENY", h.get("x-frame-options")


def test_every_response_carries_the_frame_ban_not_just_the_page(srv):
    """API 和静态资产也要带。只给 / 加,等于把「以后新增的路由」全漏掉。"""
    for p in ("/favicon.svg", "/vendor/tabler/tabler.min.css", "/api/selfcheck",
              "/static/app.js", "/static/styles.css", "/api/components"):
        h = _headers(srv, p)
        assert "frame-ancestors 'none'" in h.get("content-security-policy", ""), p


# ---------- 「未检查」不能被压成「零」 ----------
# build_freshness 在没有健康清单时仍然返回一个 summary(total/bad 全是 0),
# 前端「有没有 summary」那个条件因此永远成立,于是概览最显眼的那块板子印出绿色的 0。
# 后端这一半的责任是:把 load_health 给出的**具体**原因带上去。
# 「清单 JSON 坏了」和「压根没设环境变量」是两件事,前者要修,后者是没启用;
# 压成同一句之后,页面上没有任何办法把它们分开。

def test_build_freshness_passes_the_specific_reason_through():
    got = S.build_freshness({}, {}, "健康监控清单解析失败(/x/y.json): Expecting value")
    assert got["summary"]["bad"] == 0
    assert "解析失败" in got["reason"], got["reason"]


def test_build_freshness_still_says_something_when_no_reason_is_given():
    """正对照:调用方没给原因时不能变成空字符串或 None,
    否则前端那句 `if(reason)` 会走回「有 summary 就当成功」的老路。"""
    got = S.build_freshness({}, {}, None)
    assert got["reason"], got["reason"]


def test_two_different_failures_do_not_produce_the_same_sentence():
    """这条才是这一组的重点:两种故障必须在页面上长得不一样。"""
    a = S.build_freshness({}, {}, "没有设 TASK_CONSOLE_HEALTH")["reason"]
    b = S.build_freshness({}, {}, "健康监控清单解析失败(/x/y.json)")["reason"]
    assert a != b


# ---------- 同一条规则只能有一份实现 ----------
# server.py 曾经 `from rcnorm import norm_rc as _norm_rc_unused`,然后在下面自己抄了一份
# 同名实现。渲染侧和写入侧从此是两份代码:当时逐位等价,但任何一侧改了边界条件另一侧不会
# 跟着变,而症状是**同一个退出码在两个面板上一个算成功一个算失败**,没有任何东西会报警。
# 别名里的 unused 还让读代码的人以为共享那份已经在用了。

def test_server_uses_the_shared_norm_rc_not_a_copy():
    import rcnorm
    assert S.norm_rc is rcnorm.norm_rc, (
        "server.py 又有自己的 norm_rc 了。同一条规则两份实现,漂了不报警。")


def test_norm_rc_still_unwraps_the_hresult_form():
    """正对照:上面那条 is 断言对着两个都坏掉的实现也会通过,
    所以这里钉住这条规则本身的行为(实测换来的那两条)。"""
    assert S.norm_rc(2147942402) == 2, "事件日志把 Win32 码包成 HRESULT,必须解包"
    assert S.norm_rc(-2147024891) == 5, "带符号 int32 的同一个值也要认"
    assert S.norm_rc(None) is None, "没有返回码就是 None,不是 0"
    assert S.norm_rc("") is None


# ---------- 导出字段必须显式两分 ----------
# 这层白名单已经吃掉过三次字段:reason(空库那条判定生效了,而页面上只有一个没有原因的
# available=False)、lastIngest(区分「摄入器挂了」和「本来就没跑过」的唯一信号)、
# countScope(那个数字属于哪个时间窗)。每一次的表现都一样:
# **后端算对了,页面上什么都没有,而没有任何东西报错。**
#
# 所以现在要求显式两分:导出的(*_OUT)加上刻意不导出的(*_DROP),
# 两边必须覆盖生产者写下的每一个键。忘了归类会让这条测试变红,
# 而不是让那个字段安静地到不了页面。

import inspect  # noqa: E402
import re as _re  # noqa: E402


def _producer_keys(varname):
    """从 server.py 源码里读出 `varname = {...}` 这个字面量写下的键。

    读源码而不是调 build_payload:后者要真跑 PowerShell。
    读源码的坏处是形状一变就失效,所以下面立刻断言读到的数量下限。
    """
    src = inspect.getsource(S)
    keys = set()
    for m in _re.finditer(r"^\s*" + varname + r"\s*=\s*\{", src, _re.M):
        i = src.index("{", m.start())
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        keys |= set(_re.findall(r'"([a-zA-Z][a-zA-Z0-9_]*)"\s*:', src[i:j]))
    assert len(keys) >= 5, f"只从 {varname} 读到 {keys},源码形状可能变了"
    return keys


@pytest.mark.parametrize("var,out,drop", [
    ("hist", "HISTORY_OUT", "HISTORY_DROP"),
    ("runs", "RUNLOG_OUT", "RUNLOG_DROP"),
])
def test_every_produced_field_is_either_exported_or_deliberately_dropped(var, out, drop):
    produced = _producer_keys(var)
    classified = set(getattr(S, out)) | set(getattr(S, drop))
    missing = produced - classified
    assert not missing, (
        f"{var} 里这些键既没导出也没写进 {drop},它们会安静地到不了页面: {sorted(missing)}")


def test_the_reason_field_is_exported():
    """单独钉住 reason:它是三次事故里的第一次,也是最贵的一次。"""
    assert "reason" in S.HISTORY_OUT and "reason" in S.RUNLOG_OUT


def test_the_producer_reader_can_actually_fail():
    """负对照:上面几条全靠 _producer_keys 真的读到了东西。
    读一个不存在的变量必须炸,而不是返回空集合让断言恰好通过。"""
    with pytest.raises(AssertionError):
        _producer_keys("nosuchvariable")


def test_act_never_answers_with_an_undefined_message(srv, monkeypatch):
    """act.ps1 什么都没输出时,响应里必须有可读的原因,而不是缺一个 message。

    ⚠ 原来 `res = json.loads(out) if out else {}` —— 一个字节都没输出时 res 是空字典,
    而 **err 完全不进响应**(只有 JSON 解析失败那一支才用 out or err)。
    前端无条件读 `r.message`,于是右下角只弹出「<任务名>:undefined」四秒后消失,
    真正的错误文本(解释器找不到、被 ExecutionPolicy 挡下、脚本解析失败 ——
    这几种都是 rc!=0 且 stdout 为空、stderr 有正文)停在 server 进程里从不外传。
    **一个报错却不说错在哪的界面,和不报错差不多。**

    这条用例**一次都不碰真机**:枚举和动作两次 run_ps 都换成替身,任务名是编的。
    第一版让枚举走真机、再拿本机第一个任务名去 POST disable —— 那是在一台活机器上
    按下一个破坏性动作,只靠「替身会拦住它」这一个假设兜底。
    禁令不是控制,给合成输入才是。
    """
    import json as _json

    FAKE_TASKS = _json.dumps({"tasks": [{"name": "AcmeSyntheticTask", "state": "Ready"}]})

    def fake_run_ps(script, env_extra=None, timeout=90, args=None):
        if script == S.COLLECT:
            return 0, FAKE_TASKS, ""
        # act.ps1: rc 非零、没有 stdout、只有 stderr, 正是那几种真实故障的形状
        return 1, "", "AcmeUnreadableStderrText"

    monkeypatch.setattr(S, "run_ps", fake_run_ps)

    def failed_controller(*args, **kwargs):
        raise S.task_control.ContractError('bounded_transport_failed', 'transport')
    monkeypatch.setattr(S.task_control, 'action', failed_controller)

    st, body = call(srv, "POST", "/api/act", token=TOKEN,
                    body={"name": "AcmeSyntheticTask", "verb": "disable"})
    assert st == 500, body[:200]
    payload = _json.loads(body.decode("utf-8"))
    assert payload.get("message"), "响应里没有 message,前端会印出 undefined"
    assert payload['error']['code'] == 'bounded_transport_failed'
    assert "AcmeUnreadableStderrText" not in str(payload)
    assert payload.get("ok") is False
    assert payload.get("name") == "AcmeSyntheticTask"


def test_act_still_reports_a_real_message_when_the_script_speaks(srv, monkeypatch):
    """正对照:脚本正常回话时,message 用它自己的,不被兜底文案顶掉。

    少了这一条,一个「永远把 message 设成固定字符串」的实现也能让上面那条通过。
    """
    import json as _json

    def fake_run_ps(script, env_extra=None, timeout=90, args=None):
        if script == S.COLLECT:
            return 0, _json.dumps({"tasks": [{"name": "AcmeSyntheticTask"}]}), ""
        return 0, _json.dumps({"ok": True, "message": "AcmeScriptSaidThis",
                               "before": "Ready", "after": "Disabled"}), ""

    monkeypatch.setattr(S, "run_ps", fake_run_ps)
    monkeypatch.setattr(S.task_control, 'action', lambda *args, **kwargs: {
        'ok': True, 'message': 'AcmeScriptSaidThis', 'before': 'Ready', 'after': 'Disabled'})
    st, body = call(srv, "POST", "/api/act", token=TOKEN,
                    body={"name": "AcmeSyntheticTask", "verb": "disable"})
    assert st == 200
    payload = _json.loads(body.decode("utf-8"))
    assert payload["message"] == "AcmeScriptSaidThis"
    assert payload["before"] == "Ready" and payload["after"] == "Disabled"


def test_an_unexpected_exception_becomes_a_500_not_a_dropped_connection(srv, monkeypatch):
    """处理器里逃出来的异常必须变成一个 500 JSON,而不是一个断掉的连接。

    ⚠ do_POST 的动作分支原来没有任何兜底,而这几种都真的会发生:
    run_ps 的 subprocess.TimeoutExpired(枚举 90s / 动作 60s)、
    `json.loads(out)["tasks"]` 的 ValueError / KeyError。
    异常会逃到 socketserver 的 handle_error,它打印一段 traceback 然后**直接关连接** ——
    而 log_message 被置空,本地窗口里几乎什么都看不到。
    **一个报错方式是「连接消失」的接口,和一个挂掉的服务器长得一样。**
    """
    import json as _json

    def boom(*a, **k):
        raise TimeoutError("AcmeSimulatedTimeout")

    monkeypatch.setattr(S, "run_ps", boom)
    st, body = call(srv, "POST", "/api/act", token=TOKEN,
                    body={"name": "AcmeSyntheticTask", "verb": "disable"})
    assert st == 500, (st, body[:200])
    payload = _json.loads(body.decode("utf-8"))
    assert "AcmeSimulatedTimeout" in payload.get("error", ""), payload


def test_a_get_endpoint_exception_also_becomes_a_500(srv, monkeypatch):
    """GET 一侧同样。少了这一条,只给 POST 加兜底也能让上面那条过。"""
    import json as _json

    def boom(*a, **k):
        raise RuntimeError("AcmeSimulatedGetFailure")

    monkeypatch.setattr(S, "build_payload", boom)
    st, body = call(srv, "GET", "/api/tasks", token=TOKEN)
    assert st == 500, (st, body[:200])
    assert b"AcmeSimulatedGetFailure" in body


def test_the_server_still_answers_after_an_exception(srv, monkeypatch):
    """兜底之后连接要还能用 —— 否则「返回了 500」和「连接断了」的差别只是措辞。"""
    def boom(*a, **k):
        raise RuntimeError("AcmeTransient")

    monkeypatch.setattr(S, "build_payload", boom)
    call(srv, "GET", "/api/tasks", token=TOKEN)
    monkeypatch.undo()
    # 同上:恢复之后打一个便宜的端点。要证明的是「连接还能用」,不是「任务枚举还能跑」。
    st, _ = call(srv, "GET", "/api/selfcheck", token=TOKEN)
    assert st == 200, "出过一次异常之后服务器不再正常回话"


# ---------- 每一条 /api/ 路由都必须自己查令牌 ----------
#
# 上面那几条 403 用例是**逐端点手写**的,于是新加一条路由、忘了写 `_authed()`,
# 整个套件不会有任何东西变红, 这个仓最近一次加面板就新增了四条路由,
# 它们碰巧都写了,但那靠的是记性,不是控制。
#
# 令牌是这个服务器仅有的三道控制之一,而它挡的正是「你开着的任意一个网页 POST 到
# 这个端口」。一条漏检的路由不会以任何方式显形:它照常返回正确的数据。
#
# 所以判据从「这几个端点会 403」改成「**源码里每一条 /api/ 分支都查了令牌**」,
# 新路由自动进闸。这是静态判据,不是跑一遍, 跑一遍只能覆盖想得起来的那几条。

# 路由分支的四种写法都要认。漏掉任何一种,那些路由就静悄悄地不受这道闸管,
# 而第一版正则正是这么漏的:它只认 `path ==`,于是 POST 那几条(写成
# `self.path.split("?", 1)[0] != "/api/act"`,既有 `self.` 前缀又是 `!=`)整类没进扫描,
# 包括这个服务器上唯一能改机器状态的那条。**一个漏掉一半输入的扫描器,
# 和一个真的全都合规的服务器,打印一样的绿色。**
_ROUTE_RE = re.compile(
    r'(?:self\.)?path(?:\.split\("\?",\s*1\)\[0\])?\s*'
    r'(?:==|!=|\.startswith\(|\s+in\s+\()\s*"(/api/[^"]*)"')


def _api_route_literals():
    """server.py 里每一条 /api/ 路由的字面量,按它在 do_GET / do_POST 里出现的顺序。"""
    src = open(S.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    return src, _ROUTE_RE.findall(src) + list(S.integrations.ROUTES)


def test_the_route_scanner_finds_the_routes():
    """负对照:扫不到路由的扫描器,和一个真的每条都合规的服务器打印同样的绿色。"""
    _src, routes = _api_route_literals()
    assert len(routes) >= 10, f"只扫到 {len(routes)} 条 /api/ 路由,扫描器大概没在工作"
    assert "/api/tasks" in routes and "/api/act" in routes


def test_every_api_branch_checks_the_token():
    """每一条 /api/ 分支,从它自己那行往下到下一条分支之前,必须出现 `_authed()`。

    窗口取到「下一条路由分支」而不是固定行数:固定行数会在某条处理逻辑变长时
    悄悄开始漏检,而那正好是最容易出事的那条。
    """
    src, routes = _api_route_literals()
    lines = src.splitlines()
    # 每条路由字面量出现的行号(取第一次,分支判断就在那里)
    marks = []
    for r in routes:
        for i, ln in enumerate(lines):
            if f'"{r}"' in ln and _ROUTE_RE.search(ln):
                marks.append((i, r))
                break
    marks.sort()
    bad = []
    for n, (i, r) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        window = "\n".join(lines[i:end])
        # 委托给一个 _xxx() 处理器的分支,鉴权在那个处理器里, 跟进去看。
        m = re.search(r"return self\.(_[a-z_]+)\(\)", window)
        if m:
            fn = m.group(1)
            body = re.search(rf"def {fn}\(self\):(.*?)(?=\n    def |\Z)", src, re.S)
            window += body.group(1) if body else ""
        if "_authed()" not in window:
            bad.append(f"{r}  (第 {i + 1} 行起)")
    assert not bad, ("这些 /api/ 路由没有查令牌,任何一个已打开的网页都能调到它们:\n  "
                     + "\n  ".join(bad))


def test_the_token_check_scanner_can_actually_fire():
    """投毒:把一条分支的 `_authed()` 拿掉,上面那条必须点名它。

    判据是在源码文本上跑的,所以投毒也在文本上做,不碰真文件。
    """
    src, _ = _api_route_literals()
    poisoned = src.replace(
        '        if path == "/api/hours":\n            if not self._authed():\n'
        '                return self._json(403, {"error": "bad token"})\n',
        '        if path == "/api/hours":\n', 1)
    assert poisoned != src, "投毒没有命中,这条负对照什么都没证明"
    assert "/api/hours" in poisoned
    lines = poisoned.splitlines()
    i = next(n for n, ln in enumerate(lines) if '"/api/hours"' in ln and _ROUTE_RE.search(ln))
    nxt = next((n for n in range(i + 1, len(lines))
                if _ROUTE_RE.search(lines[n])), len(lines))
    assert "_authed()" not in "\n".join(lines[i:nxt]), "投毒之后那段窗口里还有 _authed(),判据测不到它"


@pytest.mark.parametrize('path', list(S.integrations.ROUTES) + ['/api/integrations'])
def test_registered_read_routes_require_auth_before_calling_provider(srv, path):
    assert call(srv, 'GET', path)[0] == 403


def test_registered_read_dispatch_auth_negative_control():
    src, _ = _api_route_literals()
    block = src.split('if path in integrations.ROUTES:', 1)[1].split("if path == '/api/work/context':", 1)[0]
    assert 'if not self._authed():' in block
    poisoned = block.replace('if not self._authed():', 'if False:', 1)
    assert poisoned != block
    assert 'self._authed()' not in poisoned


# ---------- 删除与修复:五条路由 ----------
# 删除会从计划程序里拿掉任务,修复会让 Agent 开工。两件事都只能由带令牌的同源请求发起。
_TASK_OP_ROUTES = [
    ("POST", "/api/task/delete/plan", {"name": "AcmeSync", "reason": "合成"}),
    ("POST", "/api/task/delete/apply", {"name": "AcmeSync", "token": "synthetic"}),
    ("POST", "/api/task/repair/preview", {"name": "AcmeSync"}),
    ("POST", "/api/task/repair", {"name": "AcmeSync", "request_id": "synthetic-request-01"}),
    ("GET", "/api/task/repairs", None),
]


def test_the_route_scanner_sees_the_delete_and_repair_routes():
    """五条都得进令牌通扫:写成 `in (...)` 一组时只有第一条会被正则认出来。"""
    _src, routes = _api_route_literals()
    assert {path for _m, path, _b in _TASK_OP_ROUTES} <= set(routes)


@pytest.mark.parametrize("method,path,body", _TASK_OP_ROUTES)
def test_delete_and_repair_routes_without_token_are_403_and_never_reach_their_module(srv, monkeypatch, method, path, body):
    for module, names in ((S.task_delete, ("plan", "apply")), (S.task_repair, ("preview", "submit", "orders"))):
        for name in names:
            monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail("reached without a token"))
    assert call(srv, method, path, body=body)[0] == 403
    assert call(srv, method, path, body=body, token="wrong")[0] == 403


@pytest.mark.parametrize("method,path,body", _TASK_OP_ROUTES)
def test_delete_and_repair_routes_reject_a_rebinding_host(srv, method, path, body):
    assert call(srv, method, path, host="attacker.example:80", token=TOKEN, body=body)[0] == 400


# ---------- 对话链:四条路由 ----------
# 这四条读的是会话转录,其中一条(fork)会往会话目录里写一份新文件。
# 客户端给的只有 id,所以控制是两层:令牌/Host(和别的路由一样),加上「id 先按形状拒绝,
# 再碰文件系统」。后一层单独测,而且要证明拒绝发生时**文件系统一次都没被碰过**:
# 只断言 400 的话,一个先 glob 再发现不对的实现也是 400。
# 全部是合成 id 和 tmp_path 里现造的转录。

_CV_SID = "11111111-1111-4111-8111-111111111111"
_CV_U = "00000000-0000-4000-8000-000000000001"
_CV_U2 = "00000000-0000-4000-8000-000000000002"
_CV_ROUTES = [
    ("GET", f"/api/convo/chain?id={_CV_SID}", None),
    ("GET", f"/api/convo/node?id={_CV_SID}&u={_CV_U}", None),
    ("GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U}", None),
    ("POST", "/api/convo/fork", {"id": _CV_SID, "at": _CV_U}),
]


def test_the_route_scanner_sees_all_four_convo_routes():
    """四条都得进令牌通扫。单引号写的路由字面量不进那个正则,会静悄悄地逃过扫描。"""
    _src, routes = _api_route_literals()
    assert {"/api/convo/chain", "/api/convo/node", "/api/convo/export",
            "/api/convo/fork"} <= set(routes)


@pytest.mark.parametrize("method,path,body", _CV_ROUTES)
def test_convo_routes_without_token_are_403(srv, method, path, body):
    st, _ = call(srv, method, path, body=body)
    assert st == 403


@pytest.mark.parametrize("method,path,body", _CV_ROUTES)
def test_convo_routes_reject_a_rebinding_host(srv, method, path, body):
    st, _ = call(srv, method, path, host="attacker.example:80", token=TOKEN, body=body)
    assert st == 400


@pytest.fixture
def _no_fs(monkeypatch):
    """记下 convo-chain 有没有被叫到文件系统那一步,以及路由有没有越过自己的形状闸。

    四个 API 自己入口处也判形状,所以只 spy 文件系统的话,路由层那一道被拿掉也照样绿。
    把四个 API 也换成 spy,钉住的是「路由自己先拒绝」这一层。路由调的是包上的公开名
    (convo_chain.chain…),库内部互相调的是 core 模块里的名字,所以两处都要换。"""
    import convo_chain
    from convo_chain import core
    seen = []

    def spy(*a, **k):
        seen.append(a)
        raise AssertionError("形状不对的 id 走过了路由的形状闸")
    for name in ("chain", "node", "export_md", "fork"):
        monkeypatch.setattr(convo_chain, name, spy)
    for name in ("locate", "_base", "chain", "node", "export_md", "fork"):
        monkeypatch.setattr(core, name, spy)
    return seen


# 每条钉自己那道闸的码:「码在这几个里面」放开任何一道都不会红。
@pytest.mark.parametrize("method,path,body,code", [
    ("GET", "/api/convo/chain?id=..%2F..%2Fsecret", None, "bad_id"),
    ("GET", "/api/convo/chain?id=not-a-uuid", None, "bad_id"),
    ("GET", "/api/convo/chain", None, "bad_id"),
    ("GET", f"/api/convo/chain?id={_CV_SID}&sub=..%2F..%2Fx", None, "bad_sub"),
    ("GET", f"/api/convo/chain?id={_CV_SID}&leaf=..%2Fx", None, "bad_leaf"),
    ("GET", f"/api/convo/node?id={_CV_SID}&u=..%2F..%2Fx", None, "bad_uuid"),
    ("GET", f"/api/convo/node?id={_CV_SID}", None, "bad_uuid"),
    ("GET", "/api/convo/node?id=%2A&u=" + _CV_U, None, "bad_id"),
    ("GET", f"/api/convo/chain?id={_CV_SID}&path=..%2Fx", None, "bad_query"),
    ("GET", f"/api/convo/chain?id={_CV_SID}&id={_CV_SID}", None, "bad_query"),
    ("GET", f"/api/convo/export?id=..%2F..%2Fx&to={_CV_U}", None, "bad_id"),
    ("GET", f"/api/convo/export?id={_CV_SID}&to=..%2Fx", None, "bad_uuid"),
    ("GET", f"/api/convo/export?id={_CV_SID}", None, "bad_uuid"),
    ("GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U}&leaf=x", None, "bad_leaf"),
    ("GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U}&tools=yes", None, "bad_query"),
    ("GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U}&path=x", None, "bad_query"),
    # 形状闸是整串匹配:结尾多一个换行的 id 不是 UUID(以前 `$` 放它过去)。
    ("GET", f"/api/convo/export?id={_CV_SID}%0A&to={_CV_U}", None, "bad_id"),
    ("GET", f"/api/convo/chain?id={_CV_SID}&sub=abc%0A", None, "bad_sub"),
    ("POST", "/api/convo/fork", {"id": "..\\..\\x", "at": _CV_U}, "bad_id"),
    ("POST", "/api/convo/fork", {"id": _CV_SID, "at": "*"}, "bad_uuid"),
    ("POST", "/api/convo/fork", {"id": _CV_SID, "at": _CV_U, "sub": "../x"}, "bad_sub"),
    ("POST", "/api/convo/fork", {"id": _CV_SID, "at": _CV_U, "path": "x"}, "bad_body"),
    ("POST", "/api/convo/fork", [_CV_SID], "bad_body"),
])
def test_convo_bad_input_is_400_before_touching_the_filesystem(srv, _no_fs, method, path,
                                                              body, code):
    st, data = call(srv, method, path, token=TOKEN, body=body)
    assert (st, json.loads(data)["code"]) == (400, code), data[:200]
    assert _no_fs == [], "拒绝之前已经碰过文件系统"


def test_convo_subagent_fork_is_refused_before_the_filesystem(srv, monkeypatch):
    """子代理的转录只读。这一道在 convo_chain.fork 里(不是形状闸),所以只 spy 文件系统。"""
    from convo_chain import core
    seen = []
    monkeypatch.setattr(core, "locate", lambda *a, **k: seen.append(a))
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN,
                    body={"id": _CV_SID, "at": _CV_U, "sub": "abc"})
    assert (st, json.loads(data)["code"]) == (400, "no_sub_fork") and seen == []


def test_convo_good_shape_does_reach_the_module(srv, monkeypatch, tmp_path):
    """正对照:形状对的 id 真的走到了 convo-chain。否则上面那组 400 可能只是
    「这条路由对什么都回 400」。根目录指向一个空目录,于是答案是 not_found。"""
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", str(tmp_path))
    for method, path, body in _CV_ROUTES:
        st, data = call(srv, method, path, token=TOKEN, body=body)
        assert (st, json.loads(data)["code"]) == (400, "not_found"), path


def test_convo_unset_root_is_not_checked_not_an_error(srv, monkeypatch):
    """读侧没配根目录是「未检查」(200 + available:false),写侧是硬失败(400 unavailable)。"""
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", "")
    for path in (f"/api/convo/chain?id={_CV_SID}", f"/api/convo/node?id={_CV_SID}&u={_CV_U}"):
        st, data = call(srv, "GET", path, token=TOKEN)
        assert st == 200 and json.loads(data)["available"] is False, path
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN, body={"id": _CV_SID, "at": _CV_U})
    assert (st, json.loads(data)["code"]) == (400, "unavailable")


def test_convo_unset_root_names_the_console_variable(srv, monkeypatch, tmp_path):
    """convo-chain 不读环境变量,根目录只由路由从 TASK_CONSOLE_SESSIONS 取出传进去。

    所以没设时的原因必须点名运维要去设的那个变量,而且读写四条的形状与以前一致:
    读侧 200 + available:false,写侧 400 unavailable。CONVO_CHAIN_ROOT 是库的 CLI 才读的
    变量,把它指向一个真有转录的目录也不能让控制台读到东西(负对照:库没有自己去环境里找)。"""
    d = tmp_path / "proj-a"
    d.mkdir()
    (d / f"{_CV_SID}.jsonl").write_text(json.dumps(
        {"type": "user", "uuid": _CV_U, "parentUuid": None, "sessionId": _CV_SID,
         "message": {"role": "user", "content": "hello"}}) + "\n", encoding="utf-8")
    monkeypatch.delenv("TASK_CONSOLE_SESSIONS", raising=False)
    monkeypatch.setenv("CONVO_CHAIN_ROOT", str(tmp_path))
    for path in (f"/api/convo/chain?id={_CV_SID}", f"/api/convo/node?id={_CV_SID}&u={_CV_U}"):
        st, data = call(srv, "GET", path, token=TOKEN)
        assert (st, json.loads(data)) == (200, {"available": False, "reason": S.CONVO_ROOT_UNSET}), path
    st, data = call(srv, "GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U}", token=TOKEN)
    assert (st, json.loads(data)) == (400, {"error": S.CONVO_ROOT_UNSET, "code": "unavailable"})
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN, body={"id": _CV_SID, "at": _CV_U})
    assert (st, json.loads(data)) == (400, {"error": S.CONVO_ROOT_UNSET, "code": "unavailable"})
    assert "TASK_CONSOLE_SESSIONS" in S.CONVO_ROOT_UNSET
    # 正对照:同一个目录经 TASK_CONSOLE_SESSIONS 给进去,同一条请求就读得到。
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", str(tmp_path))
    st, data = call(srv, "GET", f"/api/convo/chain?id={_CV_SID}", token=TOKEN)
    assert st == 200 and json.loads(data)["available"] is True


def test_convo_unset_root_keeps_the_library_gate_order(srv, monkeypatch):
    """「子代理不能分叉」在库里先于根目录判。路由若抢先拦 unset,就会把这道闸的码换掉。"""
    monkeypatch.delenv("TASK_CONSOLE_SESSIONS", raising=False)
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN,
                    body={"id": _CV_SID, "at": _CV_U, "sub": "abc"})
    assert (st, json.loads(data)["code"]) == (400, "no_sub_fork")


def test_convo_fork_round_trip_over_http(srv, monkeypatch, tmp_path):
    """整条 HTTP 路径走一遍:合成转录 → 链 → 节点 → 导出 → 分叉;新文件在同一个项目目录,
    源文件一个字节不变。"""
    d = tmp_path / "proj-a"
    d.mkdir()
    lines = [
        {"type": "user", "uuid": _CV_U, "parentUuid": None, "sessionId": _CV_SID,
         "cwd": "C:/work/example", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "uuid": _CV_U2, "parentUuid": _CV_U,
         "sessionId": _CV_SID, "cwd": "C:/work/example",
         "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
    ]
    src = d / f"{_CV_SID}.jsonl"
    src.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    before = src.read_bytes()
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", str(tmp_path))
    st, data = call(srv, "GET", f"/api/convo/chain?id={_CV_SID}&leaf=&sub=", token=TOKEN)
    assert st == 200 and json.loads(data)["leaf"] == _CV_U2
    st, data = call(srv, "GET", f"/api/convo/node?id={_CV_SID}&u={_CV_U2}", token=TOKEN)
    assert st == 200 and json.loads(data)["text"] == "hi"
    st, data = call(srv, "GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U2}&tools=1&thinking=0",
                    token=TOKEN)
    assert st == 200 and "hello" in json.loads(data)["text"]
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN, body={"id": _CV_SID, "at": _CV_U2})
    assert st == 200, data[:300]
    r = json.loads(data)
    assert (d / f"{r['newId']}.jsonl").is_file() and src.read_bytes() == before


def test_convo_post_body_has_a_size_cap(srv, _no_fs):
    """分叉 POST 的正文只有几个 id。超过上限的直接 413,不解析、不碰文件系统。"""
    big = {"id": _CV_SID, "at": _CV_U, "leaf": "x" * (S.CONVO_BODY_MAX + 10)}
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN, body=big)
    assert (st, json.loads(data)["code"]) == (413, "too_large")
    assert _no_fs == []


def test_convo_post_body_just_under_the_cap_is_parsed(srv, _no_fs):
    """正对照:同样大、只是没超上限的正文照常解析(这里走到形状闸,回 400 bad_id)。
    没有它,上面那条可能只是「什么正文都 413」。"""
    ok = {"id": "x" * (S.CONVO_BODY_MAX - 200), "at": _CV_U}
    st, data = call(srv, "POST", "/api/convo/fork", token=TOKEN, body=ok)
    assert (st, json.loads(data)["code"]) == (400, "bad_id")


def test_convo_export_is_a_read_that_works_in_read_only_preview(srv, monkeypatch, tmp_path):
    """导出不写任何东西。做成 POST 时只读预览导不了,每次导出还让读缓存全部失效。
    负对照:同一个只读预览里分叉(真的写文件)照样 403。"""
    d = tmp_path / "proj-a"
    d.mkdir()
    (d / f"{_CV_SID}.jsonl").write_text(json.dumps(
        {"type": "user", "uuid": _CV_U, "parentUuid": None, "sessionId": _CV_SID,
         "message": {"role": "user", "content": "hello"}}) + "\n", encoding="utf-8")
    monkeypatch.setenv("TASK_CONSOLE_SESSIONS", str(tmp_path))
    monkeypatch.setenv("TASK_CONSOLE_READ_ONLY", "1")
    invalidated = []
    monkeypatch.setattr(S.source_reads.boundary, "invalidate", lambda: invalidated.append(1))
    st, data = call(srv, "GET", f"/api/convo/export?id={_CV_SID}&to={_CV_U}", token=TOKEN)
    assert st == 200 and "hello" in json.loads(data)["text"]
    assert invalidated == []
    st, _ = call(srv, "POST", "/api/convo/fork", token=TOKEN, body={"id": _CV_SID, "at": _CV_U})
    assert st == 403
    st, _ = call(srv, "POST", "/api/convo/export", token=TOKEN, body={"id": _CV_SID, "to": _CV_U})
    assert st == 403


def test_a_rejected_request_does_not_read_an_unbounded_body(srv):
    """鉴权之前的 _drain 以前照着客户端自称的 Content-Length 读到底。自称 1 GB、一个字节
    都不发的请求,会让服务器线程一直等下去。现在超过上限就不读,直接回拒绝并断开。"""
    import socket
    s = socket.create_connection(("127.0.0.1", srv), timeout=10)
    try:
        s.sendall((f"POST /api/convo/fork HTTP/1.1\r\nHost: 127.0.0.1:{srv}\r\n"
                   "Content-Type: application/json\r\nContent-Length: 1000000000\r\n\r\n").encode())
        s.settimeout(5)
        head = s.recv(64)
    finally:
        s.close()
    assert head.startswith(b"HTTP/1.") and b" 403 " in head


def test_a_rejected_request_under_the_drain_cap_is_still_drained(srv):
    """正对照:正文不大时照旧先读掉再回 403(见 _drain 为什么),连接不被重置。"""
    st, _ = call(srv, "POST", "/api/convo/fork", body={"id": _CV_SID, "at": _CV_U, "pad": "x" * 5000})
    assert st == 403
