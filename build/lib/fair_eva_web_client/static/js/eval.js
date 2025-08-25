(function () {
  const data = window.FAIR_DATA || { aggregated: {}, colours: {} };

  // 1) Barras por grupo
  document.querySelectorAll('.progress-fill').forEach(el => {
    const pts = Number(el.dataset.points || 0);
    const color = el.dataset.color || '#888';
    // asume escala 0..100; ajusta si es 0..N
    const pct = Math.max(0, Math.min(100, pts));
    el.style.width = pct + '%';
    el.style.background = color;
    el.style.transition = 'width 600ms ease';
  });

  // 2) Gráfico general (radar o doughnut). Ejemplo doughnut:
  const ctx = document.getElementById('fairOverview');
  if (ctx && window.Chart) {
    const labels = Object.keys(data.aggregated).filter(k => k !== 'fair');
    const values = labels.map(k => data.aggregated[k] ?? 0);
    const colors = labels.map(k => data.colours[k] || '#999');

    new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: colors }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'bottom' },
          tooltip: { enabled: true }
        },
        cutout: '55%'
      }
    });
  }
})(); 
