#!/usr/bin/env python3
"""页面里的脚本必须能解析。

这条来自同一个错误在一天里犯了两次:往 `if / else if / else` 链中间插了两行,整块脚本
当场解析失败。表现是**页面照常打开、状态码 200、每一栏都停在「读取中」** :
没有红色,没有报错弹窗,只是什么都不动。一个语法错误在这里长得像「后端有点慢」。

两次的成因也一样:替换锚点用了一行同时出现在启动区和分发链里的代码。所以这条测试不是
在防手滑,是在防一类**改法**:任何按文本替换往这个文件里插东西的动作,都要有一道
能当场报错的闸。

node 不在就跳过,并且大声说明跳过了 : 一个静默跳过的检查比没有检查更糟,
它会让人以为这里被看过了。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

PAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "scripts", "task_console", "console.html")
NODE = shutil.which("node")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def scripts():
    """页面里所有内联脚本块。"""
    html = open(PAGE, encoding="utf-8").read()
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def check(js: str):
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        open(path, "w", encoding="utf-8").write(js)
        r = subprocess.run([NODE, "--check", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return r.returncode, (r.stderr or r.stdout or "")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_there_is_a_script_to_check():
    """负对照的第一半:抽不到脚本时下面那条会空转全绿。"""
    blocks = scripts()
    assert blocks, "页面里一个内联脚本块都没抽到"
    assert sum(len(b) for b in blocks) > 5000, "抽到的脚本短得不像整页逻辑"


@pytest.mark.skipif(NODE is None, reason="没有 node,无法解析页面脚本")
def test_every_inline_script_parses():
    for i, js in enumerate(scripts()):
        rc, err = check(js)
        assert rc == 0, f"第 {i + 1} 个脚本块解析失败:\n{err[:600]}"


@pytest.mark.skipif(NODE is None, reason="没有 node,无法解析页面脚本")
def test_the_check_can_actually_fail():
    """负对照的另一半:证明这道闸真的会红。

    没有它,一个永远返回 0 的检查器和一个真的解析通过的检查器输出一模一样。
    投毒的形状刻意选成那次真事故:往 if/else 链中间插一行。
    """
    good = scripts()[0]
    poisoned = good.replace("else await loadMaint();",
                            "loadConvos();\n    else await loadMaint();", 1)
    assert poisoned != good, "投毒锚点没命中,这条负对照什么都没证明"
    rc, err = check(poisoned)
    assert rc != 0
    assert "else" in err.lower()


# ---------- 固定高度的元素选择器 ----------
# 这一条是为一个犯了两次的错写的:样式表里曾有 `button{height:22px}`,一条元素选择器,
# 于是任何拿 <button> 当积木的多行部件都被钉死,内容照自己的高度排、溢出去盖住下一行。
# 屏幕上看是排版错乱,不像高度被覆盖,而且各项高度的数字读起来完全「合理」。
# 第一次是概览的六个指标格,第二次是时间轴的三个缩放按钮(换成比例字体后内容涨到 30px)。
# 当时的对策是「以后记得写 height:auto」,两次都没记住。所以改成 min-height 并在这里钉住。

import re as _re


def _page_style():
    """页面里所有 <style> 块拼起来。"""
    html = open(PAGE, encoding="utf-8").read()
    blocks = _re.findall(r"<style>(.*?)</style>", html, _re.S)
    assert blocks, "页面里一个 <style> 都没读到,这条检查会因为没东西可查而打印绿色"
    css = chr(10).join(blocks)
    # 注释要剥掉:样式表里那段解释为什么不能写死高度的注释,本身就引用了 `button{height:22px}`
    # 这个反例,于是检查器把讲这条规则的话当成了这条规则。
    # 一道会被「讨论它自己」触发的闸,最后一定会被人关掉。
    return _re.sub(r"/\*.*?\*/", "", css, flags=_re.S)


# td/th 不在名单里:在表格布局里 height 本来就被当成最小值,内容多了格子会自己长,
# 所以那不是这个 bug。把它们算进来只会逼着下一个人去放宽这条检查,
# 而放宽过的检查会连真的那一类一起放过。
# `[^-]height` 写不得:它要求 height 前面必须有一个字符,于是 `button{height:22px}`
# 这种把 height 放在第一条的写法直接漏掉。这个盲区是下面那条负对照抓出来的,
# 而它正是「负对照不是走过场」的实例:第一版正则对着页面打印绿色,同时漏掉一半形状。
_FIXED_H = _re.compile(r"(?<![.#\w-])(button|input|select)\s*\{[^}]*?(?<!-)height\s*:\s*\d")


def test_no_element_selector_pins_a_fixed_height():
    css = _page_style()
    hits = [m.group(0)[:70] for m in _FIXED_H.finditer(css)]
    assert not hits, (
        "元素选择器上写死 height 会裁掉任何多行部件,用 min-height。命中: " + repr(hits))


def test_the_fixed_height_rule_can_actually_fire():
    """负对照:把那条规则投毒回去,上面的检查必须命中。
    不做这一步的话,一个正则写错的检查和一个真没发现问题的检查打印出的绿色一模一样。"""
    poisoned = _page_style() + "\nbutton{height:22px}\n"
    assert _FIXED_H.search(poisoned), "投毒后仍未命中,这条正则是空拦的"


# ---------- 装内容的容器不许写死高度 ----------
# 上一轮把 `button{height:22px}` 改成 min-height 并配了一道闸,但那道闸只盯**元素选择器**,
# 而同一个坑在 `#bar{height:26px}` 上原样留着:#bar 继承了 .navbar 的 flex-wrap,
# 窗口窄到约 760px 时五个全局计数换到第二行,然后被裁进 26px 的盒子里,
# 既看不见也几乎不可能滚到。整页最顶上的「有没有事」摘要于是在窄屏下静默消失。
#
# 想把那道闸按形状扩到 id/class 上,量过一次:对已知正样本召回 2/2,
# **但在干净文件上误报 18 处**, 圆点、进度条、时间轴刻度这类本来就该定高的装饰。
# 一道在干净代码上报 18 次的闸门会被关掉,所以不按形状扩,改成点名。
# 名单里的每一项都是「装会变多变少的内容」的容器;新增一个容器要显式加进来,那是刻意的。
_CONTAINERS = ("#bar", "#side", "#view", ".pad", ".box", ".mt-rows", ".rp-grid", ".tiles")


def _rules_for(css, sel):
    """返回**正好是** sel 这个选择器的规则体。

    刻意不含后代:`#bar .seg{height:100%}` 是子元素填满父级的合法写法,
    第一版把它算了进来,于是这道闸对着干净文件报了一次 : 一道会误报的闸门会被关掉,
    所以宁可窄一点,名单里再加容器也是显式动作。
    """
    out = []
    for m in _re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        heads = [h.strip() for h in m.group(1).split(",")]
        if any(h == sel or h.startswith(sel + ".") or h.startswith(sel + ":")
               for h in heads):
            out.append(m.group(2))
    return out


# 只认 px。百分比和 vh 是「跟着别人走」,不会把内容裁掉;写死像素才会。
_PX_H = _re.compile(r"(?<!-)height\s*:\s*\d+(\.\d+)?px")


def test_layout_containers_do_not_pin_a_fixed_height():
    css = _page_style()
    bad = []
    for sel in _CONTAINERS:
        for body in _rules_for(css, sel):
            if _PX_H.search(body):
                bad.append(sel + " {" + " ".join(body.split())[:60] + "}")
    assert not bad, "容器写死 height 会把溢出的内容裁掉,用 min-height。命中: " + repr(bad)


def test_the_container_check_can_actually_fire():
    """负对照。这条尤其要紧:上面那个 _rules_for 只要选择器匹配写错一点,
    就会对着任何输入都返回空列表,然后永远打印绿色。"""
    # 用合成样式表,不拼真文件:拼真文件的话,这条负对照的成立与否会取决于产品此刻干不干净,
    # 而一条依赖被测对象状态的负对照,在被测对象变脏的那一刻就跟着红,分不清是谁的问题。
    def hits(css):
        return [b for sel in _CONTAINERS for b in _rules_for(css, sel) if _PX_H.search(b)]

    assert hits("#bar{height:26px}"), "投毒后仍未命中,这条检查是空拦的"
    assert hits(".mt-rows{overflow:auto;height:172px}"), "属性不在首位时也要命中"
    # 反向,四种都不该命中:百分比、视口单位、min-/max- 前缀、以及后代选择器。
    assert not hits("#view{height:100%}"), "百分比是跟着别人走,不会裁掉内容"
    assert not hits("#view{height:50vh}"), "视口单位同理"
    assert not hits("#bar{min-height:26px}"), "min-height 正是我们要的写法"
    assert not hits("#bar{max-height:26px}"), "max-height 不该被这条规则管"
    assert not hits("#bar .seg{height:100%}"), "子元素填满父级是合法写法"


def test_the_container_check_reads_a_real_stylesheet():
    """再一条:确认它真的读到了那几个容器的规则,而不是一个都没匹配上。"""
    css = _page_style()
    found = [sel for sel in _CONTAINERS if _rules_for(css, sel)]
    assert len(found) >= 5, f"只匹配到 {found},选择器写法可能和样式表对不上"


def test_the_enter_space_handler_only_lists_selectors_that_can_be_focused():
    """Enter/空格处理器里列的每一类,都必须真的有渲染点给它 tabindex。

    ⚠ 它原来列了五类,而**只有一类**在渲染时给了 tabindex="0" ——
    另外四类既没有 tabindex 也不是原生可聚焦元素,那四条分支永远走不到。
    一段覆盖了五类、其中四类是死的处理器,读起来像「键盘可达性已经做过了」,
    而真相是只做了五分之一。**死分支不会以任何方式报出来,它只是让人不再去补那件事。**

    判据是「处理器里的选择器」与「渲染时带 tabindex 的选择器」对得上,
    不是「处理器里有没有某个名字」—— 后者只能证明我写了那个名字。
    """
    src = open(PAGE, encoding="utf-8").read()
    m = _re.search(r'closest\(\s*"([^"]*data-fr[^"]*)"\s*\)', src)
    assert m, "找不到 Enter/空格处理器里的那个 closest 选择器"
    listed = [x.strip() for x in m.group(1).split(",") if x.strip()]

    # 渲染时真的给了 tabindex(且不是 -1)的那些属性/类名
    focusable_attrs = set()
    for hit in _re.findall(r'tabindex="(-?\d)"', src):
        pass
    # 逐个检查:选择器里出现的 data-* 属性,必须在某个带 tabindex="0" 的渲染串里出现
    bad = []
    for sel in listed:
        key = _re.sub(r'^\[|\]$', "", sel).split("=")[0].lstrip(".")
        # 找到同时包含这个 key 和 tabindex="0" 的那一行
        ok = any(key in line and 'tabindex="0"' in line for line in src.splitlines())
        if not ok:
            bad.append(f'{sel}  (没有任何渲染点给它 tabindex="0")')
    assert not bad, ("Enter/空格处理器列了聚焦不到的选择器:\n  " + "\n  ".join(bad))
    assert listed, "选择器列表是空的,这条检查什么都没在查"
    _ = focusable_attrs


# ---------- 死 CSS:规则有没有对象 ----------
#
# 这一组是补一个**没有任何闸门在问的问题**:一条 CSS 规则,页面上有没有东西会匹配它。
# 之前修过一条 `.cv-gh .hm`, 选择器和 JS 渲染出来的类名对不上,一行都没生效过,
# 而它是靠人读出来的。机械扫一遍立刻又找出六条。
# **一个靠人眼发现的缺陷类别,复发率是 100%。**
#
# 并进这个文件而不是新开一个,是因为 CSS 解析器和「负对照」这两样这里已经有了。
# 多一个测试文件不会让缺陷少一类,只会让下一个人多一个地方要读。

# 由模板拼出来的类名。每一条都要注明拼接点,否则这张豁免表会变成一个静默的免检名单。
_CSS_ALLOW = {
    "d-": "状态点 / 分段条,由 `d-${k}` 拼(rpListRow、rp-bar、图例)",
    "s-": "产物新鲜度灯板,由 `s-${t.state}` 拼(renderFresh)",
    "h1": "热力图色阶,由 `class=\"${k}\"` 拼(renderHeat)",
    "h2": "热力图色阶,同上",
    "h3": "热力图色阶,同上",
    "h4": "热力图色阶,同上",
    "h5": "热力图色阶,同上",
    "k-": "体征分段条,由 `k-ok` / `k-bad` / `k-st` 字面量拼在模板里",
    "info": "自检状态,由后端下发的状态字符串(selfcheck)决定",
    "pending": "任务状态,由后端 status_of 下发",
    "missing": "自检状态,由后端下发",
}


def _class_names_in_css(css):
    """CSS 里出现过的类名。只取选择器部分,不碰声明块。"""
    names = set()
    for chunk in _re.split(r"\}", css):
        sel = chunk.rsplit("{", 1)[0] if "{" in chunk else ""
        if "@" in sel:                       # @media / @supports 的条件里没有类名
            sel = _re.sub(r"@[^{]*", "", sel)
        names.update(_re.findall(r"\.(-?[_a-zA-Z][\w-]*)", sel))
    return names


def _page_without_style():
    """页面去掉 <style> 之后剩下的东西:HTML 加 JS。类名要在这里面找得到。"""
    html = open(PAGE, encoding="utf-8").read()
    return _re.sub(r"<style>.*?</style>", "", html, flags=_re.S)


def _dead_class_names():
    css = _page_style()
    body = _page_without_style()
    dead = []
    for name in sorted(_class_names_in_css(css)):
        if any(name.startswith(p) or name == p for p in _CSS_ALLOW):
            continue
        if _re.search(r"(?<![-\w])" + _re.escape(name) + r"(?![-\w])", body):
            continue
        dead.append(name)
    return dead


def test_no_css_rule_is_written_for_a_class_nothing_renders():
    """每条类选择器都要有对象。

    ⚠ 这道闸有一个**漏报面**,必须知道:类名同时又是常用词时(warn / bad / ok / note),
    词边界搜索会在别的上下文里命中它,于是判它「用到了」。也就是说绿色的含义是
    「没有明显死掉的类」,**不是**「没有死 CSS」。
    量过之后,漏报面比误报面大。写在这里是因为下一个人一定会把绿色读成后者。
    """
    dead = _dead_class_names()
    assert not dead, ("这些类只有 CSS 规则,页面上没有任何东西会匹配它们:\n  "
                      + "\n  ".join(dead))


def test_the_dead_css_check_can_actually_fire():
    """负对照。

    没有这条,一个抽不到任何规则的解析器和一个真的全绿的检查器输出一模一样 ——
    而这正是这个仓反复在修的那类缺陷,这道闸自己也不能例外。
    """
    css = _page_style() + chr(10) + ".definitely-not-used-anywhere{color:red}"
    body = _page_without_style()
    names = _class_names_in_css(css)
    assert "definitely-not-used-anywhere" in names, "解析器没抽到注入的那条规则"
    assert not _re.search(r"(?<![-\w])definitely-not-used-anywhere(?![-\w])", body)


def test_the_allowlist_only_covers_names_that_are_really_templated():
    """豁免表里的每一条都必须真的是拼出来的。

    一张没人核对的豁免表,和一个关掉的检查区别不大:它会慢慢变成
    「加进去就不用管了」的地方。判据是那个前缀在 JS 里确实出现在模板拼接里。
    """
    import glob as _glob
    import os as _os
    # ⚠ 依据可能在两处:页面自己拼的(`d-${k}`),或者后端下发的状态字符串,
    # info / pending / missing 就是后一种,它们在页面里一次都不出现。
    # 这条用例的第一版只搜页面,于是把三条**正当的**豁免判成没依据。
    # 一个只看了一半来源的检查器,报出来的「没依据」和真的没依据长得一样。
    hay = _page_without_style()
    scr = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                        "scripts", "task_console")
    for f in _glob.glob(_os.path.join(scr, "*.py")):
        hay += open(f, encoding="utf-8").read()
    unjustified = [p for p in _CSS_ALLOW if not _re.search(_re.escape(p), hay)]
    assert not unjustified, ("豁免表里这些前缀在页面和后端里都找不到,豁免没有依据: "
                             + " | ".join(unjustified))


# ---- vendor 类名撞在表格元素上 -------------------------------------------------------------------
# 真实事故 2026-09-15:调用屏的明细表给每一行写了 `class="row"`,当作「这是一行」的语义标记。
# 而 Tabler 的 `.row` 是它栅格系统的容器,规则是 `display:flex`。于是每个 <tr> 变成 flex 容器、
# 每个 <td> 变成 block,九列竖着堆起来,一行从 20px 变成 184px,整张表 9300px 高。
#
# **页面没有报任何错**,表格里的字一个不少,只是排成了九行。真浏览器里量 computed display
# 才看得出来, 这正是「配色靠量、裁剪靠看」那条经验的另一面。
#
# 判据刻意收窄到**表格元素**,而不是「页面用的类名不许和 Tabler 撞」:后者会把 btn / nav-item /
# navbar-* / page / show 这些**故意在用**的 Tabler 组件类全部判红(实测 10 个,全是正当使用),
# 于是要么加一张没人核的白名单,要么这条闸被关掉。而一个 Tabler 的布局类出现在 <tr>/<td>/<th>
# 上,没有一种情况是正当的, 那套栅格从设计上就不是给表格用的。


def _tabler_layout_classes():
    """Tabler 里会改 display 的类名。会改 display 的类才有能力拆掉表格的布局。"""
    css = open(os.path.join(os.path.dirname(PAGE), "vendor", "tabler", "tabler.min.css"),
               encoding="utf-8", errors="replace").read()
    names = set()
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if "display:" not in m.group(2):
            continue
        names.update(re.findall(r"\.([A-Za-z][\w-]*)", m.group(1)))
    return names


def _classes_on_table_elements(html: str):
    """(元素名, 类名) 对,只取 table / tr / td / th 上写着的类。"""
    out = []
    for m in re.finditer(r"<(table|tr|td|th)\b([^>]*)>", html, re.I):
        for c in re.finditer(r'class="([^"]*)"', m.group(2)):
            for tok in c.group(1).split():
                # JS 拼接出来的片段(`class="lrow' + ...`)在这里会留下半个 token,
                # 带引号或 ${ 的一律跳过:它们不是字面类名。
                if tok and "'" not in tok and "$" not in tok and "+" not in tok:
                    out.append((m.group(1).lower(), tok))
    return out


def test_no_vendor_layout_class_sits_on_a_table_element():
    bad = [(el, c) for el, c in _classes_on_table_elements(open(PAGE, encoding="utf-8").read())
           if c in _tabler_layout_classes()]
    assert not bad, (
        "这些 Tabler 布局类被写在了表格元素上,会把表格的行列结构拆掉(实测一行 20px 变 184px):\n  "
        + "\n  ".join(f"<{el} class=\"{c}\">" for el, c in bad))


def test_the_table_class_check_can_actually_fire():
    """负对照:把当初那个 `class="row"` 塞回一个 <tr>,这条闸必须抓到。

    没有这一条,一个抽不到 Tabler 类名的解析器(vendor 路径写错、正则不匹配)
    会和一个真的没有撞车的页面打印出同样的绿色。
    """
    layout = _tabler_layout_classes()
    assert "row" in layout, "没从 Tabler 里抽到 .row 的 display 规则,解析器大概没在工作"
    poisoned = '<table><tr class="row" data-i="1"><td>x</td></tr></table>'
    bad = [(el, c) for el, c in _classes_on_table_elements(poisoned) if c in layout]
    assert bad == [("tr", "row")], f"投毒没有被抓到,实际 {bad}"
