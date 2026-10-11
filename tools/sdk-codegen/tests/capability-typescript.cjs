const assert = require('node:assert/strict');
const sdk = require(require('node:path').resolve(process.argv[2]));
const {hostCapabilitiesToWire: wire, capabilityCount: count, parseCapabilities} = sdk;
const host = Object.freeze({
  toolBefore: Object.freeze({intercept: Object.freeze({allow:true, deny:true, returnResult:true, modifyInput:'replace-and-merge'}), observe:true}),
  sessionStart: Object.freeze({observe:true}),
});
assert.deepEqual(wire({}), {});
assert.deepEqual(wire(host), {
  'tool.before': {modes:['intercept','observe'], capabilities:{effects:['allow','deny','return','modify'], modify:{input:{replace:true,merge:true}}}},
  'session.start': {modes:['observe']},
});
assert.deepEqual(wire({toolBefore:{intercept:{deny:true}}}), {
  'tool.before': {modes:['intercept'],capabilities:{effects:['deny']}},
});
assert.deepEqual(wire({toolBefore:{observe:true}}), {'tool.before':{modes:['observe']}});
const elicitationOnly = {userElicitationRequest:{intercept:{elicitation:{form:true}}}};
assert.deepEqual(wire(elicitationOnly), {
  'user.elicitation.request': {modes:['intercept'],capabilities:{effects:[],elicitation:{form:{}}}},
});
assert.deepEqual(wire({userElicitationRequest:{observe:true}}), {'user.elicitation.request':{modes:['observe']}});
assert.deepEqual(wire({toolBefore:{intercept:{elicitation:{form:true}}}}), {
  'tool.before': {modes:['intercept'],capabilities:{effects:[],elicitation:{form:{}}}},
});
assert.deepEqual(wire({toolBefore:{intercept:{modifyInput:'replace'}}})['tool.before'].capabilities.modify.input, {replace:true,merge:false});
assert.deepEqual(wire({toolBefore:{intercept:{modifyInput:'merge'}}})['tool.before'].capabilities.modify.input, {replace:false,merge:true});
const dependent = {toolAfter:{intercept:{
  flow:{operations:['continue','stop','continue'],remainingContinuations:count(2),continuationCount:count(0),maxContinuations:count(5)},
  inject:{context:{append:true,deliverAt:['now','next_turn','now']}},
  elicitation:{form:true,url:true},
}}};
const rendered = wire(dependent)['tool.after'];
assert.deepEqual(rendered, {modes:['intercept'],capabilities:{
  effects:['flow','inject'],
  flow:{operations:['continue','stop'],remainingContinuations:2,continuationCount:0,maxContinuations:5},
  inject:{context:{append:true,deliverAt:['now','next_turn']}},
  elicitation:{form:{},url:{}},
}});
rendered.capabilities.flow.operations.push('stop');
rendered.capabilities.inject.context.deliverAt.push('now');
assert.equal(dependent.toolAfter.intercept.flow.operations.length, 3);
assert.equal(dependent.toolAfter.intercept.inject.context.deliverAt.length, 3);
const snapshot = wire(host); snapshot['tool.before'].capabilities.modify.input.replace = false;
assert.equal(wire(host)['tool.before'].capabilities.modify.input.replace, true);
for (const value of [-1, 0.5, Infinity, NaN, Number.MAX_SAFE_INTEGER + 1, '2']) assert.throws(() => count(value), TypeError);
assert.equal(count(0), 0); assert.equal(count(Number.MAX_SAFE_INTEGER), Number.MAX_SAFE_INTEGER);
for (const declaration of Object.values(wire(host)).concat(Object.values(wire(dependent)), Object.values(wire(elicitationOnly)))) {
  if (declaration.capabilities !== undefined) {
    const parsed = parseCapabilities(declaration.capabilities);
    assert.equal(parsed.ok, true, JSON.stringify(parsed));
  }
}
assert.equal(sdk.capabilities, undefined);
assert.equal(sdk.CapabilityBuilder, undefined);
console.log('TypeScript typed capability smoke passed');
