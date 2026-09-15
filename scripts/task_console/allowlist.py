"""备份 allow-list(`$TaskNames = @( 'A', 'B' )`)的**唯一**解析器。

这个文件存在的理由是同一份东西曾经有三份互不相同的解析实现:

  - `server.py` 用 `\\$TaskNames\\s*=\\s*@\\((.*?)\\n\\)` —— **要求收尾括号顶格**;
  - `retire.py` 用 `(\\$TaskNames\\s*=\\s*@\\()(.*?)(\\n\\s*\\))` —— 允许它缩进;
  - `retire.py` 的重写还有第三种「整行必须只有一个引号名」的判据。

三者没有共用常量,也没有任何一处对账。后果是可以做到的:allow-list 脚本按常见
PowerShell 风格把收尾括号写成缩进的 `  )`,于是 `server` 匹配失败、`allow` 变成 None,
整张表的「备份」列显示 ?、「不在备份清单,换机会静默丢失」这条 issue 对所有任务一律不报
—— **一个真的漏了备份的任务被显示成「未检查」**;而同一时刻点退役,`retire` 用宽一格的
正则把同一个文件读得好好的,报「这一处要改」并真的改写它。
**同一个文件,两个都自称权威的答案。**

⚠ 三份实现合并成一份之后,合并进来的那一份仍然**只认跨行写法**:收尾括号必须在
另起一行上。而 `@( 'A', 'B' )` 写在一行里是完全合法、而且是最常见的 PowerShell 写法
—— 连本文件开头那句文档里举的例子都是这一种。它匹配不上,`parse_names` 返回
(None, 「找不到 $TaskNames = @( ... ) 这个块」),于是整张表的备份列又变回 ?,
而那正是这个模块存在的理由所反对的那一种输出:**一个真的漏了备份的任务被显示成
「未检查」**,而文件本身好好的、人照着文档写的也没错。

所以下面有两个模式,**单行那个先试**:
`INLINE` 里的 `[^)\n]*` 不许跨行,所以它对跨行块根本不可能命中,先试它是安全的;
反过来先试 `BLOCK` 则不安全 —— 遇到单行写法时,它的 `.*?` 会一路吞到文件后面
某个恰好独占一行的 `)`,把中间所有带引号的字符串一并当成任务名。
**一个匹配过宽的解析器,和一个真的读对了的解析器,输出长得一模一样。**

这里还带上另一个仓的闸门(`check-drift.ps1` 的 `Get-PsArray`)用血换来的一条:
**扫引号之前必须先去掉整行注释**。英文散文里的撇号(`the repo's tools`)和一个开引号
长得一模一样,一条注释就能让解析器把后面半个清单吞掉。那次的表现是连续九轮绿灯之后
突然报出三个不存在的漂移,同时**掩盖掉一个真的**。
"""
from __future__ import annotations

import re

# 单行写法:`$TaskNames = @( 'A', 'B' )`。`[^)\n]*` 同时挡住换行和右括号,
# 所以它要么在同一行里干净地闭合,要么彻底不匹配 —— 不存在「吞到下面去」这种中间态。
INLINE = re.compile(r"(\$TaskNames\s*=\s*@\()([^)\n]*)(\))")

# 跨行写法。收尾括号允许缩进(两种写法都在真实脚本里出现过)。
# 非贪婪到**第一行只有右括号的行**。
BLOCK = re.compile(r"(\$TaskNames\s*=\s*@\()(.*?)(\n[ \t]*\))", re.S)

# 两个模式的第 2 组都是块体。顺序即优先级,理由见模块文档。
_PATTERNS = (INLINE, BLOCK)

# 一行里可以有多个名字:`'A', 'B'` 是合法的 PowerShell,而按行只取第一个会静默漏掉后面的。
_NAME = re.compile(r"'([^']+)'")


def _strip_comments(body: str) -> str:
    """去掉整行注释。只去整行的,不去行尾的 `# ...`:

    一个 `'Name'  # 说明` 里的 `#` 后面不会再有名字,而按 `#` 截断会在
    `'Na#me'` 这种(合法但没人会写的)名字上出错。整行注释是已知会咬人的那一种,
    行尾注释不是 —— 只关掉证明会咬人的那一个。
    """
    return "\n".join(ln for ln in body.split("\n") if not ln.lstrip().startswith("#"))


def find_block(text: str) -> re.Match | None:
    """定位 `$TaskNames = @( ... )`,单行与跨行两种写法都认。找不到返回 None。

    返回的 Match 第 2 组是块体,两个模式一致 —— 调用方不需要知道命中的是哪一个。
    """
    for pat in _PATTERNS:
        m = pat.search(text)
        if m:
            return m
    return None


def parse_names(text: str) -> tuple[set[str] | None, str | None]:
    """返回 (名字集合, 出错原因)。

    **找不到块 -> (None, 原因),不是空集合。** 两者在界面上是完全不同的断言:
    None 是「我没能检查」,空集合是「一个任务都没有被备份」,后者响亮得多。
    折叠它们会让「解析器坏了」长得像「你的备份清单是空的」,反之亦然。
    """
    m = find_block(text)
    if not m:
        return None, "找不到 $TaskNames = @( ... ) 这个块"
    names = set(_NAME.findall(_strip_comments(m.group(2))))
    if not names:
        return None, "$TaskNames 里解析出 0 个名字,判为未检查而不是全部缺失"
    return names, None
