"""就地改名、AI 起名和删除对话框的辨认信息。node:vm 台架直接调页面里的函数,数据全是合成的。"""
import json

from test_operations_ui import run
from test_keyboard_conventions import KEYS

SID = "00000001-0000-4000-8000-000000000001"
FORK_A = "00000002-0000-4000-8000-000000000001"
FORK_B = "00000003-0000-4000-8000-000000000001"
OTHER = "00000004-0000-4000-8000-000000000001"


def rows():
    def row(sid, title):
        return {"id": sid, "title": title, "titleFrom": "custom-title", "file": f"C:/Acme/{sid}.jsonl",
                "humanSeen": 3, "partial": False, "ageHours": 2, "bytes": 1024, "preview": "synthetic"}
    return {"available": True, "summary": {"files": 4, "matched": 4, "groups": 1, "bytes": 4096},
            "groups": [{"id": "C--Acme-source", "cwd": "C:/Acme/source", "count": 4, "bytes": 4096,
                        "newest": 1_800_000_000, "shown": [
                            row(SID, "Acme pipeline"),
                            row(FORK_A, "Acme pipeline (fork @ 1234abcd)"),
                            row(FORK_B, "Acme pipeline (fork @ 5678abcd)"),
                            row(OTHER, "Sample notes")]}]}


def setup(extra=""):
    return KEYS + "CONVOS=" + json.dumps(rows()) + """;
      CURVIEW='convos';const painted=[],toasts=[],calls=[];
      cvPaintGroup=g=>painted.push(cvRows(g));renderConvos=()=>painted.push('all');cvObserve=()=>{};
      toast=(message,tone)=>toasts.push([message,tone]);loadConvos=async()=>calls.push(['load']);
      for(const id of ['cv-submit','cv-cancel','cv-dialog']) $(id).classList={add(){},remove(){}};
      $('cv-dialog').showModal=function(){this.open=true;};$('cv-dialog').close=function(){this.open=false;};
    """ + extra


def test_rows_carry_a_rename_pencil_and_swap_in_the_editor():
    result = run("""(()=>{
      const g=CONVOS.groups[0], before=cvRows(g);
      cvQuickRename(SID,'list');const during=painted[painted.length-1];
      return {before,during,state:{id:CV_INLINE.id,value:CV_INLINE.value,save:cvInlineReason('save')}};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert f'data-cvquick="{SID}"' in result["before"] and f'data-cvopen="{SID}"' in result["before"]
    # 正在改的那一行:标题换成输入框,三个按钮都在;别的行照旧是打开会话的标题。
    editing = result["during"].split(f'data-cvfile="C:/Acme/{SID}.jsonl"')[1].split('data-cvfile=')[0]
    assert 'id="cv-inline-input"' in editing and 'value="Acme pipeline"' in editing
    for marker in ("data-cvinline-ai", "data-cvinline-save", "data-cvinline-cancel", "AI 起名", "保存", "取消"):
        assert marker in editing, marker
    assert f'data-cvopen="{SID}"' not in editing and f'data-cvopen="{OTHER}"' in result["during"]
    assert result["state"] == {"id": SID, "value": "Acme pipeline", "save": "名称没有变化"}
    # 未改名时保存按钮渲染成禁用,原因写在悬停里。
    assert 'data-cvinline-save disabled' in editing and "名称没有变化" in editing


def test_save_reuses_the_rename_request_and_updates_the_row_in_place():
    result = run("""(async()=>{
      api=async(path,options)=>{calls.push([path,JSON.parse(options.body),!!options.inspect]);
        return {id:SID,title:'Acme 新名字',projectDir:'C--Acme-source',file:'f',cwd:'C:/Acme/source',warnings:[]};};
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 新名字';
      const reason=cvInlineReason('save');await cvInlineSave();
      return {reason,calls,title:CONVOS.groups[0].shown[0].title,open:CV_INLINE,toasts};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["reason"] == ""
    assert result["calls"][0] == ["/api/convo/rename", {"id": SID, "expectedProject": "C--Acme-source",
                                                        "title": "Acme 新名字"}, False]
    assert result["calls"][1] == ["load"]
    assert result["title"] == "Acme 新名字" and result["open"] is None
    assert ["会话名称已保存", "ok"] in result["toasts"]


def test_failed_save_keeps_the_editor_and_the_typed_text():
    result = run("""(async()=>{
      api=async()=>{throw Object.assign(new Error('synthetic conflict (conflict)'),{status:409});};
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 打了一半';await cvInlineSave();
      return {value:CV_INLINE.value,busy:CV_INLINE.busy,note:CV_INLINE.note,tone:CV_INLINE.tone};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result == {"value": "Acme 打了一半", "busy": False, "note": "synthetic conflict (conflict)", "tone": "error"}


def test_ai_suggestion_fills_the_input_and_never_saves():
    result = run("""(async()=>{
      api=async(path,options)=>{calls.push([path,JSON.parse(options.body),options.inspect===true]);
        return {title:'整理 Acme 流水线',provider:'synthetic-provider'};};
      cvQuickRename(SID,'list');const pending=cvInlineSuggest();
      const during={suggesting:CV_INLINE.suggesting,save:cvInlineReason('save'),ai:cvInlineReason('ai'),
        note:CV_INLINE.note,html:painted[painted.length-1].includes('id="cv-inline-input"')};
      await pending;
      return {during,calls,value:CV_INLINE.value,note:CV_INLINE.note,save:cvInlineReason('save')};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["during"] == {"suggesting": True, "save": "正在生成名称", "ai": "正在生成名称",
                                "note": "正在生成…", "html": True}
    assert result["calls"] == [["/api/convo/suggest-title", {"id": SID, "expectedProject": "C--Acme-source"}, True]]
    assert result["value"] == "整理 Acme 流水线" and "synthetic-provider" in result["note"]
    assert result["save"] == ""


def test_ai_failure_is_shown_inline_and_a_late_answer_after_cancel_is_dropped():
    result = run("""(async()=>{
      api=async()=>{throw Object.assign(new Error('模型暂时不可用，未生成名称 (llm_unavailable)'),{status:503});};
      cvQuickRename(SID,'list');await cvInlineSuggest();
      const failed={value:CV_INLINE.value,note:CV_INLINE.note,tone:CV_INLINE.tone,suggesting:CV_INLINE.suggesting};
      let finish;api=()=>new Promise(resolve=>finish=resolve);
      const pending=cvInlineSuggest();cvInlineClose();finish({title:'迟到的名字'});await pending;
      return {failed,after:CV_INLINE};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["failed"]["value"] == "Acme pipeline" and result["failed"]["tone"] == "error"
    assert "llm_unavailable" in result["failed"]["note"] and result["failed"]["suggesting"] is False
    assert result["after"] is None


def test_escape_cancels_the_editor_instead_of_clearing_its_text():
    result = run("""(()=>{
      const input=field('INPUT','text','Acme 改了一半');document.activeElement=input;
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 改了一半';
      const ev=key('Escape');const handled=handleEscape(ev);
      const first={handled,prevented:ev.prevented,open:CV_INLINE,inputValue:input.value,dispatched:[...input.dispatched]};
      cvQuickRename(SID,'list');CV_INLINE.busy=true;const busy=handleEscape(key('Escape'));
      const stillOpen=!!CV_INLINE;CV_INLINE=null;
      const plain=handleEscape(key('Escape'));
      return {first,busy,stillOpen,plain,cleared:input.value};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["first"] == {"handled": True, "prevented": True, "open": None,
                               "inputValue": "Acme 改了一半", "dispatched": []}
    assert result["busy"] is True and result["stillOpen"] is True
    # 负对照:没有编辑器时,同一个焦点上的 Esc 照旧是「清空搜索框」那条路。
    assert result["plain"] is True and result["cleared"] == ""


def test_escape_on_another_view_leaves_the_editor_alone():
    result = run("""(()=>{
      cvQuickRename(SID,'list');CURVIEW='tasks';return {handled:cvInlineEscape(),open:!!CV_INLINE};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result == {"handled": False, "open": True}


def test_clicking_outside_closes_only_an_unchanged_editor():
    result = run("""(()=>{
      const outside={target:{closest:()=>null}}, inside={target:{closest:s=>s==='[data-cvinline]'?{}:null}};
      cvQuickRename(SID,'list');cvInlinePointerDown(outside);const clean=CV_INLINE;
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 新的';cvInlinePointerDown(inside);const insideKept=!!CV_INLINE;
      cvInlinePointerDown(outside);
      return {clean,insideKept,dirtyKept:!!CV_INLINE,value:CV_INLINE.value,tone:CV_INLINE.tone};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result == {"clean": None, "insideKept": True, "dirtyKept": True, "value": "Acme 新的", "tone": "error"}


def test_a_second_editor_does_not_discard_unsaved_text():
    result = run("""(()=>{
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 未保存';cvQuickRename(OTHER,'list');
      const kept={id:CV_INLINE.id,value:CV_INLINE.value};
      CV_INLINE.value=CV_INLINE.original;cvQuickRename(OTHER,'list');
      return {kept,moved:CV_INLINE.id};
    })()""".replace("SID", json.dumps(SID)).replace("OTHER", json.dumps(OTHER)), setup())
    assert result == {"kept": {"id": SID, "value": "Acme 未保存"}, "moved": OTHER}


def test_chain_title_is_a_rename_button_and_becomes_the_editor():
    result = run("""(()=>{
      CH_ID=SID;CH_SUB=null;CH={available:true,id:SID,title:'Acme pipeline',projectDir:'C--Acme-source',turns:[],subagents:[],leafIsDefault:true};
      chRenderHead();const button=$('chtitle').innerHTML;
      cvQuickRename(SID,'chain');const editing=$('chtitle').innerHTML;
      CH_SUB='agent-acme';$('chtitle').innerHTML='';chRenderHead();const sub=$('chtitle').innerHTML+'|'+$('chtitle').textContent;
      return {button,editing,sub};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert f'data-cvquick-chain="{SID}"' in result["button"] and "Acme pipeline" in result["button"]
    assert 'id="cv-inline-input"' in result["editing"] and "data-cvinline-ai" in result["editing"]
    # 子代理只读:标题不能点。
    assert result["sub"] == "|Acme pipeline"


def test_rename_dialog_ai_button_fills_the_field_without_saving():
    result = run("""(async()=>{
      api=async(path,options)=>{calls.push([path,options.inspect===true]);return {title:'Acme 建议名',provider:'p'};};
      cvOpenManager('rename',SID);const before=$('cv-new-title').value;
      const pending=cvDialogSuggest();const during=cvSubmitReason();await pending;
      return {before,during,after:$('cv-new-title').value,calls,note:$('cv-edit-note').textContent,submit:cvSubmitReason()};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["before"] == "Acme pipeline" and result["during"] == "正在生成名称"
    assert result["after"] == "Acme 建议名" and result["calls"] == [["/api/convo/suggest-title", True]]
    assert result["submit"] == "" and "确认" in result["note"]


def test_delete_dialog_names_the_one_session_and_its_lookalikes():
    result = run("""(async()=>{
      const plan={files:2,bytes:900,indexEntries:1,fingerprint:'a'.repeat(64)};
      api=async(path)=>path.endsWith('delete-plan')?plan:{deleted:true};
      cvOpenManager('delete',FORK_A);await Promise.resolve();await Promise.resolve();
      const shown={facts:$('cv-delete-facts').innerHTML,alike:$('cv-delete-alike').textContent,hidden:$('cv-delete-alike').hidden};
      await cvOpenDelete(CV_EDIT.row,true);await cvDelete();
      const deleted=toasts.find(t=>t[1]==='ok');
      CV_EDIT={kind:'delete',row:cvSession(OTHER)};await cvOpenDelete(CV_EDIT.row,true);
      return {shown,deleted,other:{alike:$('cv-delete-alike').textContent,hidden:$('cv-delete-alike').hidden}};
    })()""".replace("FORK_A", json.dumps(FORK_A)).replace("OTHER", json.dumps(OTHER)), setup())
    shown = result["shown"]
    assert "C:/Acme/source" in shown["facts"] and FORK_A[:8] in shown["facts"] and "最近活动" in shown["facts"]
    assert shown["hidden"] is False
    assert "2 个" in shown["alike"] and SID[:8] in shown["alike"] and FORK_B[:8] in shown["alike"]
    assert f"只会删除这一个（{FORK_A[:8]}）" in shown["alike"]
    assert "另有 2 个" in result["deleted"][0] and FORK_A[:8] in result["deleted"][0]
    # 负对照:没有长得像的会话时,这一行不出现,成功提示也是原来那句。
    assert result["other"] == {"alike": "", "hidden": True}


def test_lookalikes_match_identical_titles_and_shared_fork_bases_only():
    result = run("""(()=>{
      const titles=t=>cvLookalikes({id:'x',title:t}).map(r=>r.title).sort();
      return {base:titles('Acme pipeline'),fork:titles('Acme pipeline (fork @ 9999abcd)'),
        none:titles('Acme'),empty:titles('')};
    })()""", setup())
    trio = ["Acme pipeline", "Acme pipeline (fork @ 1234abcd)", "Acme pipeline (fork @ 5678abcd)"]
    assert result["base"] == trio and result["fork"] == trio
    assert result["none"] == [] and result["empty"] == []


def test_suggestion_is_a_read_not_a_receipted_write():
    """真的 api():建议请求不进「最近操作」、不让读缓存失效;同样走一遍改名(写)则会。"""
    result = run("""(async()=>{
      fetch=async()=>({ok:true,status:200,json:async()=>({title:'示例',provider:'p',id:SID})});
      const epoch0=API_READ_EPOCH;
      await cvRequestSuggestion({id:SID,projectDir:'C--Acme-source'});
      const after={ops:ConsoleActions.operations.length,epoch:API_READ_EPOCH-epoch0};
      await cvEditRequest('rename',{id:SID,projectDir:'C--Acme-source'},'示例');
      return {after,write:{ops:ConsoleActions.operations.length,label:ConsoleActions.operations[0].label}};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["after"] == {"ops": 0, "epoch": 0}
    assert result["write"] == {"ops": 1, "label": "重命名会话"}


def test_operation_tables_never_call_a_suggestion_a_save():
    result = run("[operationLabel('/api/convo/suggest-title',{}),operationOutcome('/api/convo/suggest-title',{},{title:'x'})]")
    assert result[0] == "生成名称建议"
    assert result[1]["message"] == "已生成名称建议，尚未保存"


# ---------- 复审补的几条 ----------

def test_a_save_button_rendered_disabled_gets_its_plain_title_back_once_enabled():
    """渲染成禁用的按钮带 data-locked-title,打字让它可用以后悬停提示回到「保存」。"""
    result = run("""(()=>{
      cvQuickRename(SID,'list');const html=cvInlineHtml('list',SID);
      const save=html.split('id="cv-inline-save"')[1].split('>')[0];
      const locked=(save.match(/data-locked-title="([^"]*)"/) || [])[1];
      const title=(save.match(/ title="([^"]*)"/) || [])[1];
      const button={disabled:true,title,dataset:{label:'保存',lockedTitle:locked}};
      setDisabled(button,'');
      return {locked,title,after:{disabled:button.disabled,title:button.title}};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["locked"] and result["locked"] == result["title"] and "名称没有变化" in result["title"]
    assert result["after"] == {"disabled": False, "title": "保存"}


def test_closing_the_editor_returns_focus_to_its_opener():
    result = run("""(async()=>{
      const asked=[], focused=[];
      document.querySelector=s=>{asked.push(s);return s.startsWith('[data-cvquick')?{focus(){focused.push(s);}}:null;};
      cvQuickRename(SID,'list');cvInlineEscape();
      const esc=[...focused];focused.length=0;
      api=async()=>({id:SID,title:'Acme 新名字',projectDir:'C--Acme-source',warnings:[]});
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 新名字';await cvInlineSave();
      const saved=[...focused];focused.length=0;
      CH_ID=SID;CH_SUB=null;CH={available:true,id:SID,title:'Acme pipeline',projectDir:'C--Acme-source',turns:[],subagents:[],leafIsDefault:true};
      cvQuickRename(SID,'chain');cvInlineClose();
      const chain=[...focused];focused.length=0;
      document.activeElement={tagName:'BUTTON',isConnected:true};
      cvQuickRename(SID,'list');cvInlineClose();
      return {esc,saved,chain,busyElsewhere:[...focused]};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["esc"] == [f'[data-cvquick="{SID}"]']
    # 保存后重画一次、整表刷新后再一次:两次都把焦点还回铅笔。
    assert result["saved"] == [f'[data-cvquick="{SID}"]'] * 2
    assert result["chain"] == [f'[data-cvquick-chain="{SID}"]']
    # 负对照:焦点已经在别的元素上(人点了别处)就不抢。
    assert result["busyElsewhere"] == []


def test_escape_with_the_list_hidden_behind_a_chain_closes_the_chain_not_the_editor():
    result = run("""(()=>{
      let chainEsc=0;convoChainEscape=()=>{chainEsc++;return true;};
      cvQuickRename(SID,'list');CV_INLINE.value='Acme typed but unsaved';
      $('cvbox').hidden=true;$('chbox').hidden=false;
      cvInlinePointerDown({target:{closest:()=>null}});
      const tone=CV_INLINE.tone;
      const handled=handleEscape(key('Escape'));
      const after={value:CV_INLINE?.value,chainEsc};
      $('cvbox').hidden=false;
      const visible=cvInlineEscape();
      return {tone,handled,after,visible,open:CV_INLINE};
    })()""".replace("SID", json.dumps(SID)), setup())
    # 列表被盖住时:点别处不往看不见的编辑器里写提示,Esc 退的是对话链,打的字留着。
    assert result["tone"] == ""
    assert result["handled"] is True and result["after"] == {"value": "Acme typed but unsaved", "chainEsc": 1}
    # 正对照:列表回来以后,同一个 Esc 关的是编辑器。
    assert result["visible"] is True and result["open"] is None


def test_closing_the_chain_drops_its_title_editor_and_keeps_the_typed_name_visible():
    result = run("""(()=>{
      CH_ID=SID;CH_SUB=null;CH={available:true,id:SID,title:'Acme pipeline',projectDir:'C--Acme-source',turns:[],subagents:[],leafIsDefault:true};
      $('chbox').hidden=false;
      cvQuickRename(SID,'chain');CV_INLINE.value='Acme typed in chain header';
      chClose(true);
      const afterClose=CV_INLINE;
      $('cvbox').hidden=false;cvQuickRename(OTHER,'list');
      return {afterClose,toasts,listEditor:CV_INLINE&&{id:CV_INLINE.id,scope:CV_INLINE.scope}};
    })()""".replace("SID", json.dumps(SID)).replace("OTHER", json.dumps(OTHER)), setup())
    assert result["afterClose"] is None
    assert any("Acme typed in chain header" in m and tone == "warn" for m, tone in result["toasts"])
    # 列表的铅笔照常能用。
    assert result["listEditor"] == {"id": OTHER, "scope": "list"}


def test_a_dirty_editor_hidden_behind_the_chain_explains_itself_in_a_toast():
    result = run("""(()=>{
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 未保存';
      $('cvbox').hidden=true;$('chbox').hidden=false;CH_ID=OTHER;
      cvQuickRename(OTHER,'chain');
      return {kept:{id:CV_INLINE.id,scope:CV_INLINE.scope,value:CV_INLINE.value,note:CV_INLINE.note},toasts};
    })()""".replace("SID", json.dumps(SID)).replace("OTHER", json.dumps(OTHER)), setup())
    assert result["kept"] == {"id": SID, "scope": "list", "value": "Acme 未保存", "note": ""}
    assert any("列表里有一个还没保存的名称" in m for m, _ in result["toasts"])


def test_rename_dialog_locks_the_field_while_a_suggestion_is_pending():
    result = run("""(async()=>{
      let finish;api=()=>new Promise(resolve=>finish=resolve);
      cvOpenManager('rename',SID);const pending=cvDialogSuggest();
      const during={disabled:$('cv-new-title').disabled,title:$('cv-new-title').title};
      finish({title:'示例名称',provider:'p'});await pending;
      const after={disabled:$('cv-new-title').disabled,value:$('cv-new-title').value};
      CV_EDIT=null;cvOpenManager('move',SID);
      return {during,after,move:$('cv-new-title').disabled};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["during"]["disabled"] is True and "正在生成名称" in result["during"]["title"]
    assert result["after"] == {"disabled": False, "value": "示例名称"}
    assert result["move"] is False


def test_enter_in_the_inline_box_does_nothing_while_save_is_disabled():
    result = run("""(()=>{
      const sent=[];api=async(path)=>{sent.push(path);return {id:SID,title:'x'};};
      cvQuickRename(SID,'list');CV_INLINE.value='Acme 新名字';
      const ev=()=>({target:{id:'cv-inline-input'},key:'Enter',preventDefault(){}});
      $('cv-inline-save').disabled=true;cvInlineKeydown(ev());
      const blocked=[...sent], stillOpen=!!CV_INLINE && !CV_INLINE.busy;
      $('cv-inline-save').disabled=false;cvInlineKeydown(ev());
      return {blocked,stillOpen,sent};
    })()""".replace("SID", json.dumps(SID)), setup())
    assert result["blocked"] == [] and result["stillOpen"] is True
    # 正对照:按钮可用时 Enter 照常保存。
    assert result["sent"] == ["/api/convo/rename"]


def test_delete_mode_gives_the_submit_button_its_own_hover_title():
    result = run("""(async()=>{
      const plan={files:2,bytes:900,indexEntries:1,fingerprint:'a'.repeat(64)};
      api=async()=>plan;
      cvOpenManager('rename',SID);const rename={title:$('cv-submit').title,label:$('cv-submit').dataset.label};
      $('cv-dialog').close();cvOpenManager('delete',OTHER);await Promise.resolve();await Promise.resolve();
      return {rename,del:{text:$('cv-submit').textContent,title:$('cv-submit').title,label:$('cv-submit').dataset.label}};
    })()""".replace("SID", json.dumps(SID)).replace("OTHER", json.dumps(OTHER)), setup())
    assert result["rename"]["label"] == "保存名称" and result["rename"]["title"].startswith("保存名称")
    assert result["del"] == {"text": "永久删除", "title": "永久删除", "label": "永久删除"}
