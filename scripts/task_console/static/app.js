// Ordered classic modules share the existing page state. Events start last.
const CONSOLE_MODULES = [
  "api.js",
  "panels/tasks.js",
  "panels/skills.js",
  "panels/plugins.js",
  "panels/profile.js",
  "panels/repositories.js",
  "panels/storage.js",
  "panels/conversations.js",
  "panels/calls.js",
  "panels/overview.js",
  "panels/pipelines.js",
  "panels/review.js",
  "operations.js",
  "events.js"
];
(async function startConsole(){
  const failure = document.getElementById("module-error");
  const showFailure = message => { failure.hidden=false; failure.textContent="Console module failed: " + message; };
  window.addEventListener("error", event => showFailure(event.message || "script load error"));
  window.addEventListener("unhandledrejection", event => showFailure(String(event.reason)));
  try {
    for(const path of CONSOLE_MODULES){
      await new Promise((resolve,reject)=>{
        const script=document.createElement("script");
        script.src="/static/"+path;
        script.onload=resolve;
        script.onerror=()=>reject(new Error(path));
        document.head.appendChild(script);
      });
      if(!failure.hidden) throw new Error("module initialization stopped");
    }
    document.documentElement.dataset.consoleReady="true";
  } catch(error){ showFailure(error.message); }
})();
