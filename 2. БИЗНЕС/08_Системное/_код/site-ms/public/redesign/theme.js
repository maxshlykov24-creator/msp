(() => {
  const root = document.documentElement;
  try {
    if (localStorage.getItem('ms-product.redesign.theme') === 'dark') root.dataset.theme = 'dark';
    root.toggleAttribute('data-paused', localStorage.getItem('ms-product.redesign.paused') === 'true');
  } catch {}
})();
