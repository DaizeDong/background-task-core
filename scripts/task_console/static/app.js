// Ordered classic modules share the existing page state. Events start last.
const CONSOLE_MODULES = [
  "api.js",
  "actions.js",
  "panels/tasks.js",
  "panels/skills.js",
  "panels/plugins.js",
  "panels/integrations.js",
  "panels/profile.js",
  "panels/repositories.js",
  "panels/storage.js",
  "panels/conversations.js",
  "panels/conversation-actions.js",
  "panels/convchain.js",
  "panels/calls.js",
  "panels/overview.js",
  "panels/pipelines.js",
  "panels/review.js",
  "work-model.js",
  "work-actions.js",
  "workbench.js",
  "navigation.js",
  "operations.js",
  "events.js"
];
// These panels have guarded entrypoints and no core initialization side effects.
// The remaining classic modules still form the core's shared dependency graph.
const OPTIONAL_PANELS = {
  'panels/plugins.js': 'mt-plugins',
  'panels/integrations.js': 'integration-list',
  'panels/convchain.js': 'chbox',
  'panels/conversation-actions.js': 'cv-action-error'
};
(async function startConsole(){
  const failure = document.getElementById("module-error");
  const showFailure = message => { failure.hidden=false; failure.textContent="Console module failed: " + message; };
  let loading=null, moduleError=null;
  const panelFailure = path => {
    const panel=document.getElementById(OPTIONAL_PANELS[path]);
    panel.textContent='此面板加载失败，请刷新页面重试。其他区域可继续使用。';
    panel.hidden=false;
  };
  window.addEventListener("error", event => {
    if(loading && OPTIONAL_PANELS[loading]) {moduleError=event.message;panelFailure(loading);}
    else showFailure(event.message || "script load error");
  });
  window.addEventListener("unhandledrejection", event => showFailure(String(event.reason)));
  // Fetch a small window ahead. Script insertion stays sequential so a failed
  // dependency still stops startup before dependent code or events can execute.
  let prepared=0;
  const prepareDownloads = index => {
    while(prepared < Math.min(index+4,CONSOLE_MODULES.length)){
      const link=document.createElement('link');
      link.rel='preload';link.as='script';
      link.href='/static/'+CONSOLE_MODULES[prepared++];
      document.head.appendChild(link);
    }
  };
  const loadScript = path => new Promise((resolve,reject)=>{
    let failures=0;
    const append = () => {
      const script=document.createElement('script');
      script.src='/static/'+path;
      script.onload=resolve;
      script.onerror=()=>{
        // A resource error means this script did not execute. Retry once for
        // transient transport failures; syntax/runtime errors are not replayed.
        script.remove();
        if(++failures<2) setTimeout(append,150);
        else reject(new Error(path));
      };
      document.head.appendChild(script);
    };
    append();
  });
  try {
    for(const [index,path] of CONSOLE_MODULES.entries()){
      prepareDownloads(index);
      loading=path;moduleError=null;
      try { await loadScript(path);
      if(moduleError) throw new Error(moduleError);
      } catch(error) {
        if(!OPTIONAL_PANELS[path]) throw error;
        panelFailure(path);
      }
      loading=null;
      if(!failure.hidden) throw new Error("module initialization stopped");
    }
    document.documentElement.dataset.consoleReady="true";
  } catch(error){ showFailure(error.message); }
})();
