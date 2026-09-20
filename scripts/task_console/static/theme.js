// Apply the saved preference before styles load. This is the only theme owner.
window.ConsoleTheme = (() => {
  const root=document.documentElement, media=matchMedia('(prefers-color-scheme: dark)');
  const valid=value=>['light','dark','system'].includes(value) ? value : 'system';
  let preference='system';
  try{preference=valid(localStorage.getItem('tc.theme'));}catch(_){}
  function apply(){
    const effective=preference==='system' ? (media.matches?'dark':'light') : preference;
    root.dataset.theme=effective;
    root.dataset.bsTheme=effective;
    root.style.colorScheme=effective;
    const control=document.getElementById('theme-select');
    if(control) control.value=preference;
  }
  function set(value){
    preference=valid(value);
    try{localStorage.setItem('tc.theme',preference);}catch(_){}
    apply();
  }
  media.addEventListener('change',apply);
  window.addEventListener('storage',event=>{
    if(event.key==='tc.theme' || event.key===null){preference=valid(event.newValue);apply();}
  });
  apply();
  return {set,apply};
})();
