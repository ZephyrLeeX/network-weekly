/* Local interaction only: interface filtering and discovery status. No credentials. */
(() => {
  const search = document.querySelector('#interface-search');
  const table = document.querySelector('#interface-table');
  if (search && table) {
    const rows = [...table.querySelectorAll('tbody tr')];
    const count = document.querySelector('#selected-count');
    const update = () => {
      const query = search.value.trim().toLocaleLowerCase();
      rows.forEach(row => { row.hidden = !row.dataset.search.includes(query); });
      count.textContent = `已选择 ${table.querySelectorAll('input[type="checkbox"]:checked').length} 个`;
    };
    search.addEventListener('input', update);
    table.addEventListener('change', update);
    update();
  }
  const status = document.querySelector('#discovery-status');
  if (!status) return;
  const check = async () => {
    try {
      const response = await fetch(status.dataset.statusUrl, {credentials: 'same-origin'});
      if (!response.ok) throw new Error('status unavailable');
      const job = await response.json();
      if (job.status === 'succeeded') {
        window.location.replace(status.dataset.returnUrl);
      } else if (job.status === 'failed') {
        status.classList.add('failed');
        status.firstChild.textContent = job.error || '接口获取失败，请重试。';
      } else {
        window.setTimeout(check, 2000);
      }
    } catch (_) {
      status.classList.add('failed');
      status.firstChild.textContent = '状态暂不可用，请稍后重新获取。';
    }
  };
  check();
})();
