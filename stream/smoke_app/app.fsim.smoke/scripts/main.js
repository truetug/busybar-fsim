// Смоук-приложение fsim: рисует надпись через HTTP API бара — тем же путём, что Clock.
// Проверяет сразу: установку пакета, запуск js_runner, fetch к localhost, display/draw.
async function draw(text) {
  const response = await fetch('http://localhost/api/display/draw', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      application_name: 'app.fsim.smoke',
      priority: 50,
      elements: [{ type: 'text', id: 'title', text, font: 'bold', color: '#FFFFFFFF', x: 2, y: 3, display: 'front' }]
    })
  });
  if (!response.ok) {
    throw new Error('draw: HTTP ' + response.status);
  }
}

draw('SMOKE OK').catch(e => console.error('app.fsim.smoke: ' + e));
