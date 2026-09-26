document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const button = e.target.querySelector('button');
  const message = document.getElementById('message');
  button.disabled = true;
  try {
    const response = await fetch('/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:document.getElementById('access-token').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Không đăng nhập được.');
    location.href = '/';
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
});
