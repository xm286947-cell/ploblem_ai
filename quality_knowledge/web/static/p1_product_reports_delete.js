(()=>{
  const shell=document.querySelector('.qr-shell'),history=document.querySelector('#qr-history');
  if(!shell||!history)return;
  const api=shell.dataset.api;
  function enhance(){
    history.querySelectorAll('.qr-history-row').forEach(row=>{
      if(row.querySelector('.qr-delete')||row.querySelector('.qr-status.PUBLISHED'))return;
      const button=document.createElement('button');button.type='button';button.className='qr-delete';button.textContent='删除草稿';
      button.addEventListener('click',async event=>{
        event.stopPropagation();
        if(!confirm('确认删除这份未发布报告？删除后无法恢复。'))return;
        const response=await fetch(`${api}/product-reports/${encodeURIComponent(row.dataset.id)}`,{method:'DELETE'});
        const body=await response.json().catch(()=>({detail:'删除失败'}));
        if(!response.ok){alert(body.detail||'删除失败');return}
        row.remove();const current=document.querySelector('#qr-report');if(current)current.hidden=true;
      });row.appendChild(button);
    });
  }
  new MutationObserver(enhance).observe(history,{childList:true,subtree:true});enhance();
})();
