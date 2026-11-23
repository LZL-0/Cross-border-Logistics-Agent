/* 离线引擎与 Python 版本的对数脚本：node scripts/test_engine.js */
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');

global.window = global;
global.localStorage = {
  _d: {},
  getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
  setItem(k, v) { this._d[k] = String(v); }
};
global.Response = class { constructor(body, init) { this.body = body; this.status = (init && init.status) || 200; } json() { return Promise.resolve(JSON.parse(this.body)); } };

eval(fs.readFileSync(path.join(root, 'app/assets/www/data.js'), 'utf8'));
eval(fs.readFileSync(path.join(root, 'app/assets/www/engine.js'), 'utf8'));

const E = global.Engine;
function show(title, obj) {
  console.log('\n=== ' + title + ' ===');
  console.log(JSON.stringify(obj, null, 2));
}

const c1 = E.compare('德国', 20, 50, 40, 30, '', 1, '6109100010', 5000, null, 'balance');
console.log('count', c1.count, 'tax', c1.tax && c1.tax.total_tax, 'rec', c1.recommendation.carrier,
  c1.recommendation.landed_total, c1.recommendation.eta_days, c1.recommendation.eta_date, c1.recommendation.score);
console.log('quotes:', c1.quotes.map(q => [q.carrier, q.total, q.tax, q.agency_fee, q.landed_total, q.eta_days, q.score].join('|')).join('\n'));

const cl = E.estimate('德国', 'air', '6109100010', ['商业发票', '装箱单'], '', '', 5000);
console.log('\nclearance', cl.clearance_days, cl.confidence, cl.missing_docs);

const p = E.planShipment('德国', 300, 120, 80, 60, 30000, '9403600000', 'lcl', 10, null, 'balance');
console.log('\nplan', p.ok, p.chosen_quote && p.chosen_quote.carrier, p.chosen_quote && p.chosen_quote.landed_total,
  p.chosen_quote && p.chosen_quote.eta_days, p.timeline && p.timeline.map(t => t.stage + ':' + t.start + '-' + t.end + '@' + t.date).join(' / '));
if (p.docs) console.log('docs missing', p.docs.missing);

const a = E.ask('20kg 服装，尺寸 50x40x30cm，货值 5000 元，发德国，比一下价');
console.log('\nask agent', a.agent);
console.log(a.answer.split('\n').slice(0, 8).join('\n'));
console.log('trace', a.trace.map(t => t.action + ':' + t.ok + ':' + String(t.result).slice(0, 40)).join(' | '));
