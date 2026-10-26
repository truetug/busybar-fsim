// Тест сети стенда (FS-8): запросы разных видов подряд; результат смотрят по network_log рекордера.
async function go(url, options) {
  try {
    const response = await fetch(url, options);
    const text = await response.text();
    return response.status + ':' + text.length;
  } catch (e) {
    return 'error:' + e;
  }
}

(async () => {
  const results = [];
  results.push(await go('https://live.test/ok'));
  results.push(await go('https://mocked.test/price?apikey=SECRET1&x=1'));
  results.push(await go('https://blocked.test/'));
  results.push(await go('http://live.test/ok'));
  results.push(await go('https://live.test/redir'));
  results.push(await go('https://mocked.test/post', { method: 'POST', headers: { Authorization: 'Bearer TOPSECRET', 'Content-Type': 'application/json' }, body: '{"a":1}' }));
  results.push(await go('https://live.test/big')); // последним: 1 МиБ в JS-куче бара может завершить приложение
  console.log('nettest: ' + results.join(' | '));
})();
