// Minimal DOM fixtures based on the visible Google Flights loading indicators.
const fs = require('node:fs');
const vm = require('node:vm');
const predicate = fs.readFileSync(0, 'utf8');

class Element {
  constructor({ tag = 'div', attrs = {}, text = '', style = {}, parent = null, width = 16, height = 16 } = {}) {
    Object.assign(this, { tag, attrs, innerText: text, parentElement: parent, width, height });
    this.style = { display: 'block', visibility: 'visible', opacity: '1', ...style };
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  getBoundingClientRect() { return { width: this.width, height: this.height }; }
  getClientRects() { return this.width && this.height ? [this.getBoundingClientRect()] : []; }
  matches(selector) {
    const attr = selector.match(/^\[([^\]*=]+)(\*?=)'([^']+)'\]$/);
    if (attr) return attr[2] === '*=' ? (this.attrs[attr[1]] || '').includes(attr[3]) : this.attrs[attr[1]] === attr[3];
    if (selector.startsWith('.')) return selector.slice(1).split('.').every(c => (this.attrs.class || '').split(' ').includes(c));
    if (selector === 'li.pIav2d') return this.tag === 'li' && (this.attrs.class || '').split(' ').includes('pIav2d');
    return false;
  }
}

const card = new Element({ tag: 'li', attrs: { role: 'listitem', class: 'pIav2d' }, text: 'Qatar Airways 1 stop HEL–HAN €956 round trip' });
const tab = new Element({ attrs: { role: 'tab' }, text: 'Cheapest €631' });
function snapshot(extras, cheapTab = tab) {
  const nodes = [card, cheapTab, ...extras];
  const document = {
    // The hidden accessibility label remains in innerText after loading finishes.
    body: { innerText: `Loading results\nDeparting flights\n${cheapTab.innerText}\n${card.innerText}` },
    querySelectorAll: selectors => nodes.filter(node => selectors.split(',').some(s => node.matches(s.trim()))),
  };
  return vm.runInNewContext(`(${predicate})()`, { Element, document, getComputedStyle: node => node.style });
}

const bar = options => new Element({ attrs: { role: 'progressbar', 'aria-hidden': 'true', 'aria-label': 'Fetching results' }, ...options });
const hiddenFirstBar = bar({ style: { opacity: '0' } });
const hiddenParent = new Element({ style: { display: 'none' } });
const cases = {
  plane_after_hidden_first_bar: snapshot([hiddenFirstBar, bar({ width: 990, height: 2 })]),
  cheapest_spinner: snapshot([hiddenFirstBar, bar()]),
  nearby_airports: snapshot([new Element({ attrs: { class: 'HoPSkc' }, text: 'Searching nearby airports...' })]),
  skeleton: snapshot([new Element({ attrs: { class: 'flight-skeleton' } })]),
  finished_hidden_labels: snapshot([hiddenFirstBar]),
  hidden_parent: snapshot([bar({ parent: hiddenParent })]),
  zero_sized_spinner: snapshot([bar({ width: 0, height: 0 })]),
  cheaper_update: snapshot([], new Element({ attrs: { role: 'tab' }, text: 'Cheapest €600' })),
};
process.stdout.write(JSON.stringify(cases));
