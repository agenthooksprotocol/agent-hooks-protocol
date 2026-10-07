const assert = require('node:assert/strict');
const {capabilities: c, parseCapabilities} = require(require('node:path').resolve(process.argv[2]));
const base = c.intercept().deny();
const modified = base.modifyInput({replace: true});
assert.deepEqual(JSON.parse(JSON.stringify(modified)), {
  modes: ['intercept','observe'], capabilities: {effects: ['deny','modify'], modify:{input:{replace:true,merge:false}}}
});
assert.deepEqual(base.build().capabilities, {effects:['deny']});
assert.deepEqual(c.observe(), {modes:['observe']});
assert.equal(c.observe().deny, undefined);
assert.throws(() => c.intercept().build());
assert.throws(() => JSON.stringify(c.intercept()));
for (const value of [{}, {replace:false}, {replace:1}, {oops:true}, null]) assert.throws(() => base.modifyInput(value));
assert.throws(() => base.flow([]));
assert.throws(() => base.flow(['continue']));
assert.throws(() => base.flow(['stop'], {continuationCount:-1}));
assert.throws(() => base.flow(['stop'], {maxContinuations:Infinity}));
assert.throws(() => base.injectContext([]));
assert.throws(() => base.injectContext(['invalid']));
const both = modified.modifyInput({merge:true}).modifyOutput({replace:true});
assert.deepEqual(both.build().capabilities.modify.input, {replace:true,merge:true});
const snapshot = both.build(); snapshot.capabilities.effects.push('ask'); snapshot.capabilities.modify.input.replace = false;
assert.equal(both.build().capabilities.modify.input.replace, true);
assert.equal(both.build().capabilities.effects.includes('ask'), false);
const all = both.allow().ask().message().return().elicitationForm().elicitationUrl()
  .flow(['continue','stop'], {remainingContinuations:2, continuationCount:0})
  .injectContext(['now','next_turn']);
assert.deepEqual(all.build().capabilities.elicitation, {form:{},url:{}});
assert.equal(base.build().capabilities.elicitation, undefined);
for (const declaration of [base, both, all, c.intercept().elicitationUrl()]) {
  const parsed = parseCapabilities(declaration.capabilities);
  assert.equal(parsed.ok, true, JSON.stringify(parsed));
}
console.log('TypeScript capability smoke passed');
