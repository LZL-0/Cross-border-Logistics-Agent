/* 接口层自测：node scripts/test_api.js */
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');

global.window = global;
const store = {};
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }
};
eval(fs.readFileSync(path.join(root, 'app/assets/www/data.js'), 'utf8'));
eval(fs.readFileSync(path.join(root, 'app/assets/www/engine.js'), 'utf8'));
const E = global.Engine;

function call(p, params, body) {
  return E.handleApi(p, params || {}, body || {});
}

const ask = call('/api/ask', {}, { text: '300kg 家具，尺寸 120x80x60cm，货值 30000 元，发德国，要海运拼柜' });
console.log('ASK agent=', ask.agent, '| trace=', ask.trace.map(t => t.action).join(','), '| 长度', ask.answer.length);
console.log(ask.answer.split('\n').slice(0, 10).join('\n'));

const q = call('/api/quote', {}, { country: '德国', weight_kg: 20, length: 50, width: 40, height: 30, declared_value: 5000, hs: '6109100010', prefer: 'balance' });
console.log('\nQUOTE ok=', q.ok, 'count=', q.data.count, 'rec=', q.data.recommendation.carrier, q.data.recommendation.landed_total);

const exp = call('/api/export', { method: 'POST' }, {
  country: 'DE', carrier: '顺丰国际', mode: 'express', weight_kg: 20, declared_value: 5000,
  freight_total: 1815.91, tax: 1664, landed_total: 3479.91, eta_date: '12-02 ~ 12-07', note: 'node test'
});
console.log('\nEXPORT ok=', exp.ok, 'id=', exp.data.quote_id);
console.log(exp.data.csv.trim());

console.log('\nSTATS', JSON.stringify(call('/api/stats').quotes));
console.log('\nQUOTES total=', call('/api/quotes', { limit: '10' }).data.total);
console.log('\nHS', JSON.stringify(call('/api/hs', { keyword: '玩具' }).data.candidates.map(c => c.name)));
console.log('\nDUTY ok=', call('/api/duty', { hs: '9503000000', country: 'US', declared_value: '2000' }).data);
console.log('\nCONFIG', JSON.stringify(call('/api/config')));
