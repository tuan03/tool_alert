const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let config = null, status = null, history = null, activeTab = 'notifications';
let refreshBusy = false, lastRefreshAt = 0;
const timeText = t => t ? new Date(t * 1000).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour12:false}) : 'Chưa có';
const stateNames = {pending:'Chờ gửi',sending:'Đang gửi',sent:'Đã gửi',retrying:'Đang thử lại',failed:'Lỗi',uncertain:'Cần kiểm tra',success:'Thành công',running:'Đang quét'};
function toast(text, error=false) { const t=$('toast');t.textContent=text;t.className='toast'+(error?' error':'');t.hidden=false; }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}});
  if(response.status===401){location.href='/login';throw new Error('Phiên đã hết hạn.');}
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Yêu cầu không hợp lệ.');
  return data;
}
function readForm() {
  return {
    username:$('username').value,password:$('password').value,bot_token:$('bot_token').value,
    clear_password:$('clear-password').checked,clear_bot_token:$('clear-bot').checked,
    chat_id:$('chat_id').value,topic_id:$('topic_id').value?Number($('topic_id').value):null,
    interval_seconds:Number($('interval_seconds').value),enabled:$('enabled').checked,
    stores:$('stores').value.split(',').map(s=>s.trim()).filter(Boolean),
    lookback_days:$('all-history').checked?null:Number($('lookback_days').value)
  };
}
function fillForm(s) {
  config=s;
  for(const key of ['username','chat_id','interval_seconds']) $(key).value=s[key];
  $('password').value='';$('bot_token').value='';
  $('clear-password').checked=false;$('clear-bot').checked=false;
  $('topic_id').value=s.topic_id||'';$('stores').value=s.stores.join(', ');
  $('enabled').checked=s.enabled;$('all-history').checked=s.lookback_days===null;
  $('lookback_days').value=s.lookback_days||30;$('lookback_days').disabled=s.lookback_days===null;
  $('password-hint').textContent=s.password_configured?'Đã lưu mật khẩu · để trống để giữ nguyên.':'Bí mật được mã hóa khi lưu.';
  $('bot-hint').textContent=s.bot_token_configured?'Đã lưu bot token · để trống để giữ nguyên.':'Nhập token bot của bạn.';
}
function renderStatus() {
  if(!status)return;
  $('state').textContent=status.running?'Đang quét':status.enabled?'Tự động bật':'Tạm dừng';
  $('state').className='badge'+(status.enabled?'':' warning');
  $('order-count').textContent=status.orders_count;
  $('sent-count').textContent=status.queue.sent||0;
  $('queue-label').textContent=((status.queue.pending||0)+(status.queue.retrying||0)+(status.queue.sending||0))+' đang chờ · '+((status.queue.failed||0)+(status.queue.uncertain||0))+' cần xử lý';
  $('last-success').textContent=timeText(status.last_success);
  $('interval-label').textContent='Chu kỳ '+status.interval_seconds+' giây';
  $('toggle').textContent=status.enabled?'Tạm dừng':'Bật tự động';
  $('scan-now').disabled=status.running;
  $('status-title').textContent=status.running?'Đang kiểm tra nguồn':!status.configured?'Chưa kết nối BKTeam':status.enabled?'Hệ thống đang theo dõi':'Quét tự động đang tạm dừng';
  $('status-description').textContent=status.last_error||(!status.configured?'Nhập và lưu tài khoản nguồn để bắt đầu.':status.last_scan?('Lượt gần nhất: '+(stateNames[status.last_scan.status]||status.last_scan.status)+' · '+status.last_scan.orders_count+' đơn · '+status.last_scan.new_orders+' đơn mới'):'Lượt quét đầu tiên sẽ tạo baseline, không gửi đơn cũ.');
  renderCountdown();
}
function renderCountdown(){
  if(!status)return;
  if(status.running){$('countdown').textContent='Đang quét';return;}
  if(!status.enabled||!status.next_scan_at){$('countdown').textContent='—';return;}
  const seconds=Math.max(0,Math.ceil(status.next_scan_at-Date.now()/1000));
  $('countdown').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
}
function cell(row,text){const td=document.createElement('td');td.textContent=String(text??'—');row.appendChild(td);return td;}
function renderHistory(){
  if(!history)return;
  const columns={notifications:['Thời gian','Store / Đơn','Giá trị','Đích gửi','Trạng thái',''],scans:['Bắt đầu','Kết thúc','Trang','Đơn / Mới','Kết quả'],orders:['Ghi nhận lần đầu','Store','Đơn','Giá trị','Gặp gần nhất']};
  $('history-head').replaceChildren();const head=document.createElement('tr');for(const name of columns[activeTab]){const th=document.createElement('th');th.textContent=name;head.appendChild(th);}$('history-head').appendChild(head);
  const body=$('history-body');body.replaceChildren();
  const rows=history[activeTab];
  if(!rows.length){const tr=document.createElement('tr');const td=cell(tr,'Chưa có dữ liệu. Cấu hình kết nối và chạy lượt quét đầu tiên.');td.colSpan=columns[activeTab].length;td.className='empty';body.appendChild(tr);return;}
  for(const item of rows){
    const tr=document.createElement('tr');
    if(activeTab==='notifications'){
      cell(tr,timeText(item.created_at));cell(tr,item.payload.store_name+' / '+item.payload.display_number);
      cell(tr,item.payload.amount+(item.payload.currency?' '+item.payload.currency:''));cell(tr,item.chat_id||'Chưa cấu hình');
      const td=cell(tr,'');const badge=document.createElement('span');badge.className='badge '+item.status;badge.textContent=stateNames[item.status]||item.status;td.appendChild(badge);
      if(item.error){const small=document.createElement('small');small.className='error-text';small.textContent=item.error;td.appendChild(small);}
      const action=cell(tr,'');
      if(['failed','uncertain'].includes(item.status)){const b=document.createElement('button');b.className='retry';b.textContent='Thử lại';b.onclick=()=>perform(b,async()=>{
        if(item.status==='uncertain'&&!confirm('Tin này có thể đã đến Telegram. Bạn đã kiểm tra và muốn gửi lại?'))return;
        await api('/api/notifications/'+item.id+'/retry',{method:'POST'});toast('Đã đưa tin vào hàng đợi.');await refresh();
      });action.appendChild(b);}
    }else if(activeTab==='scans'){
      cell(tr,timeText(item.started_at));cell(tr,timeText(item.finished_at));cell(tr,item.pages);cell(tr,item.orders_count+' / '+item.new_orders);
      const td=cell(tr,stateNames[item.status]||item.status);if(item.error){const s=document.createElement('small');s.className='error-text';s.textContent=item.error;td.appendChild(s);}
    }else{cell(tr,timeText(item.first_seen));cell(tr,item.payload.store_name);cell(tr,item.payload.display_number);cell(tr,item.payload.amount);cell(tr,timeText(item.last_seen));}
    body.appendChild(tr);
  }
}
async function refresh(){
  if(refreshBusy)return;refreshBusy=true;
  try{[status,history]=await Promise.all([api('/api/status'),api('/api/history')]);lastRefreshAt=Date.now();renderStatus();renderHistory();}
  catch(e){toast(e.message,true);}finally{refreshBusy=false;}
}
async function perform(button,action){button.disabled=true;try{await action();}catch(e){toast(e.message,true);}finally{button.disabled=false;}}
$('settings-form').addEventListener('submit',e=>{e.preventDefault();perform(e.submitter,async()=>{
  const data=await api('/api/settings',{method:'PUT',body:JSON.stringify(readForm())});fillForm(data);toast('Đã lưu cấu hình. Áp dụng từ lượt quét tiếp theo.');await refresh();
});});
$('all-history').onchange=()=>{$('lookback_days').disabled=$('all-history').checked;};
$('scan-now').onclick=e=>perform(e.currentTarget,async()=>{await api('/api/scan',{method:'POST'});toast('Đã bắt đầu quét.');await refresh();});
$('toggle').onclick=e=>perform(e.currentTarget,async()=>{config=await api('/api/settings',{method:'PUT',body:JSON.stringify({enabled:!status.enabled})});$('enabled').checked=config.enabled;toast(config.enabled?'Đã bật quét tự động.':'Đã tạm dừng lượt quét mới. Tin trong hàng đợi vẫn tiếp tục gửi.');await refresh();});
$('test-source').onclick=e=>perform(e.currentTarget,async()=>{toast('Đang kiểm tra đăng nhập BKTeam…');const result=await api('/api/source/test',{method:'POST',body:JSON.stringify(readForm())});toast(result.message);});
$('test-telegram').onclick=e=>perform(e.currentTarget,async()=>{const result=await api('/api/telegram/test',{method:'POST',body:JSON.stringify(readForm())});toast(result.status==='sent'?'Đã gửi tin thử thành công.':result.error,result.status!=='sent');});
$('logout').onclick=e=>perform(e.currentTarget,async()=>{await api('/auth/logout',{method:'POST'});location.href='/login';});
$('logout-footer').onclick=$('logout').onclick;
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{activeTab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x===b));renderHistory();});
api('/api/settings').then(fillForm).then(refresh).catch(e=>toast(e.message,true));
setInterval(()=>{if(!document.hidden)refresh();},5000);
setInterval(()=>{if(!document.hidden)renderCountdown();},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){renderCountdown();if(Date.now()-lastRefreshAt>=2000)refresh();}});
